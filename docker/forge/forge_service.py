"""Job service around Forge's headless ``sim`` mode, for the gateway's deck simulations.

Forge itself runs unchanged: this file only writes deck files, starts ``java -jar <forge> sim``
and reads what it prints. One simulation runs at a time and its Java processes exit when the job
ends, so an idle container holds no Forge memory. Only the gateway reaches this service, over the
stack's private network.

A job's games are cut into fixed slices by their number alone (``slices``), each slice one Forge
process with its own seed made from the job's seed, so the cut and the seeds are the same however
many slices play at once (Forge does not always replay a game exactly from its seed, so the games
themselves can still differ). A job asks for ``parallel`` slices at once; FORGE_MAX_PARALLEL (default 1)
caps it, because each Forge process needs its own memory (FORGE_XMX) inside the container's limit.

Endpoints (JSON):
  GET    /health          status, Forge version, busy flag, queue length
  GET    /precons         the bundled Commander precons a deck entry can name as {"precon": name}
  POST   /check           {"names": [...]} -> each name's spelling in Forge's card list, or null
  POST   /jobs            {"decks": [{"commander": [...], "main": [[count, name], ...]}], "games": n,
                           "seed"?: s, "parallel"?: p}
  GET    /jobs/<id>       state, games done, per-game winners, cards Forge could not load
  DELETE /jobs/<id>       stop a queued or running job
"""

from __future__ import annotations

import hashlib
import json
import os
import queue
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FORGE_HOME = Path(os.environ.get("FORGE_HOME", "/opt/forge"))
NAMES_FILE = Path(os.environ.get("FORGE_NAMES", "/opt/forge-index/names.txt"))
VERSION = os.environ.get("FORGE_VERSION", "unknown")
XMX = os.environ.get("FORGE_XMX", "1g")
NICE = int(os.environ.get("FORGE_NICE", "10"))
MAX_GAMES = int(os.environ.get("FORGE_MAX_GAMES", "50"))
MAX_QUEUE = int(os.environ.get("FORGE_MAX_QUEUE", "4"))
# Forge processes one job may run at once. Each takes up to FORGE_XMX of heap plus Java's own
# memory (one four-player game peaked near 790 MiB), so raise it only with the container's limit.
MAX_PARALLEL = max(1, min(4, int(os.environ.get("FORGE_MAX_PARALLEL", "1"))))
# A job's games are cut into slices of at most this many, the same way at any parallel setting.
# TODO(Q138 item 1, held for Ben's re-confirmation card): groups of up to five (R-207), which the
# card recommends keeping; no replay promise rests on the groups.
SLICE_GAMES = max(1, min(5, int(os.environ.get("FORGE_SLICE_GAMES", "5"))))
JOB_TIMEOUT = int(os.environ.get("FORGE_JOB_TIMEOUT", "7200"))
# Forge's sim calls a game a draw after this many seconds (its -c flag; Forge's own default is 120,
# too short for a four-player Commander game on a Raspberry Pi).
GAME_SECONDS = int(os.environ.get("FORGE_GAME_SECONDS", "600"))
KEEP_SECONDS = int(os.environ.get("FORGE_KEEP_SECONDS", str(48 * 3600)))
FORMATS = {"Commander", "Constructed"}
MAX_DECKS = 4
MAX_BODY = 256 * 1024

# Forge prints one line per finished game (SimulateMatch.java in Forge 2.0.15):
#   "Game Result: Game 1 ended in 2136 ms. Ai(1)-D1 has won!"
#   "Game Result: Game 2 ended in a Draw! Took 600012 ms."
# Unrecognised lines are kept in the log tail, never guessed at.
RESULT = re.compile(
    r"Game (?P<game>\d+) ended in (?:(?P<ms>\d+) ms\. (?P<rest>.*)|a draw! took (?P<draw_ms>\d+) ms)",
    re.IGNORECASE,
)
WINNER = re.compile(r"(?P<who>\S.*?) has won", re.IGNORECASE)
DRAW = re.compile(r"\bdraw\b", re.IGNORECASE)
HEADER = re.compile(r"\bvs\b.* - .* of \w+")
STOPPED_SLOW = re.compile(r"Stopping slow match as draw", re.IGNORECASE)
# Forge prints each game's outcome entries just before its result line (SimulateMatch.java:233-243):
#   "Game Outcome: Turn 18"
#   "Game Outcome: Ai(1)-D1 has lost because life total reached 0"
#   "Game Outcome: Ai(2)-D2 has lost due to accumulation of 21 damage from generals"
OUTCOME_TURN = re.compile(r"Game Outcome: Turn (?P<turn>\d+)", re.IGNORECASE)
OUTCOME_LOST = re.compile(
    r"Game Outcome: .*?\bD(?P<seat>[1-4]) has lost (?:because|due to) (?P<why>.+)", re.IGNORECASE
)
LOAD_PROBLEM = re.compile(
    r"(could not|cannot|can't|unable to) (find|load)|unsupported|not supported|unknown card", re.IGNORECASE
)


def norm(name: str) -> str:
    """Case-, accent- and punctuation-insensitive form used to match card names."""
    for a, b in (("\u2019", "'"), ("\u2018", "'"), ("\u2014", "-"), ("\u2013", "-"), ("\ua789", ":")):
        name = name.replace(a, b)
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", text).strip().casefold()


def load_names() -> dict[str, str]:
    names: dict[str, str] = {}
    if NAMES_FILE.exists():
        for line in NAMES_FILE.read_text("utf-8").splitlines():
            if line.strip():
                names.setdefault(norm(line), line.strip())
    return names


NAMES = load_names()
ALIASES_FILE = Path(os.environ.get("FORGE_ALIASES", "/opt/forge-index/aliases.json"))
ALIASES: dict[str, str] = (
    {norm(k): v for k, v in json.loads(ALIASES_FILE.read_text("utf-8")).items()}
    if ALIASES_FILE.exists()
    else {}
)


PRECONS_FILE = Path(os.environ.get("FORGE_PRECONS", "/opt/forge-index/precons.json"))
PRECONS: list[dict[str, Any]] = json.loads(PRECONS_FILE.read_text("utf-8")) if PRECONS_FILE.exists() else []


def forge_jar() -> Path:
    # The release holds several launchers; only the desktop one has the headless `sim` mode (the
    # mobile-dev one needs a display even to start).
    jars = sorted(FORGE_HOME.glob("forge-gui-desktop-*jar-with-dependencies.jar"))
    if not jars:
        raise RuntimeError(f"no Forge jar in {FORGE_HOME}")
    return jars[-1]


def resolve(name: str) -> str | None:
    """Forge's spelling of ``name``; a split or double-faced card may be given by its front face."""
    for candidate in (name, name.split(" // ")[0]):
        hit = NAMES.get(norm(candidate)) or ALIASES.get(norm(candidate))
        if hit:
            return hit
    return None


def slices(games: int) -> list[int]:
    """How a job of ``games`` games is cut: the fewest slices of at most SLICE_GAMES, as even as
    possible, larger first (12 -> [4, 4, 4], 7 -> [4, 3]). It depends on the number of games only."""
    count = max(1, -(-games // SLICE_GAMES))
    base, extra = divmod(games, count)
    return [base + (1 if i < extra else 0) for i in range(count)]


def slice_seed(seed: int | None, index: int) -> int | None:
    """The seed of slice ``index``: the job's own seed for the first, so a job of one slice plays as
    before slices existed; one made from it for the others (0 to 2**63 - 1, as Forge reads seeds)."""
    if seed is None or index == 0:
        return seed
    digest = hashlib.sha256(f"{seed}:{index}".encode()).digest()
    return int.from_bytes(digest[:8], "big") % 2**63


class Job:
    def __init__(
        self, decks: list[dict[str, Any]], games: int, fmt: str, seed: int | None = None, parallel: int = 1
    ) -> None:
        self.seed = seed
        self.slices = slices(games)
        self.parallel = max(1, min(parallel, MAX_PARALLEL, len(self.slices)))
        self.id = secrets.token_hex(8)
        self.decks = decks
        self.games = games
        self.format = fmt
        self.state = "queued"
        self.created = time.time()
        self.started: float | None = None
        self.finished: float | None = None
        self.results: list[dict[str, Any]] = []
        self.load_problems: list[str] = []
        self.tail: list[str] = []
        self.header = ""  # Forge's "Ai(1)-D1 vs Ai(2)-D2 - 3 games of Commander seed 42" line
        self.returncode: int | None = None
        self.procs: dict[int, subprocess.Popen[str]] = {}  # slice index -> its running Forge
        self.slice_state: list[str] = ["queued"] * len(self.slices)
        self.lock = threading.Lock()
        self.cancelled = False
        self.timed_out = False  # set by the job's timer, so no group starts after it fired

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "format": self.format,
            "games_requested": self.games,
            "games_done": len(self.results),
            "results": sorted(self.results, key=lambda r: r["game"]),
            "load_problems": self.load_problems[:40],
            "log_tail": self.tail[-40:],
            "returncode": self.returncode,
            "seed": self.seed,
            "parallel": self.parallel,
            "max_parallel": MAX_PARALLEL,
            "slices": [
                {"games": n, "seed": slice_seed(self.seed, i), "state": self.slice_state[i]}
                for i, n in enumerate(self.slices)
            ],
            "header": self.header,
            "game_seconds": GAME_SECONDS,
            "queued_s": round((self.started or time.time()) - self.created, 1),
            "run_s": round((self.finished or time.time()) - self.started, 1) if self.started else None,
            "forge_version": VERSION,
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
QUEUE: queue.Queue[Job] = queue.Queue()


def deck_file(index: int, deck: dict[str, Any], fmt: str) -> str:
    """A Forge .dck file. Decks are named D1..D4 so the winner line maps back to the request."""
    lines = ["[metadata]", f"Name=D{index}"]
    if fmt == "Commander":
        lines.append("[Commander]")
        lines += [f"1 {name}" for name in deck["commander"]]
    lines.append("[Main]")
    lines += [f"{count} {name}" for count, name in deck["main"]]
    return "\n".join(lines) + "\n"


def parse_result(line: str) -> dict[str, Any] | None:
    m = RESULT.search(line)
    if not m:
        return None
    if m.group("draw_ms") is not None:
        return {"game": int(m.group("game")), "ms": int(m.group("draw_ms")), "winner": None, "draw": True}
    rest = m.group("rest")
    out: dict[str, Any] = {
        "game": int(m.group("game")),
        "ms": int(m.group("ms")),
        "winner": None,
        "draw": False,
    }
    w = WINNER.search(rest)
    if w:
        d = re.search(r"\bD([1-4])\b", w.group("who"))
        out["winner"] = int(d.group(1)) if d else None
        out["winner_text"] = w.group("who")[:120]
    elif DRAW.search(rest):
        out["draw"] = True
    else:
        out["unparsed"] = rest[:200]
    return out


def slot_home(slot: int) -> Path:
    """Forge's home for parallel place ``slot`` (its preferences and logs), with card scripts read
    only when a game needs them (Forge's LOAD_CARD_SCRIPTS_LAZILY), which halves start-up."""
    base = Path(os.environ.get("HOME", "/data"))
    home = base / f"slot-{slot}" if slot else base
    prefs = home / ".forge" / "preferences" / "forge.preferences"
    if not prefs.exists():
        prefs.parent.mkdir(parents=True, exist_ok=True)
        if os.environ.get("FORGE_LAZY_CARDS", "1") == "1":
            prefs.write_text("LOAD_CARD_SCRIPTS_LAZILY=true\n", "utf-8")
    return home


def run_slice(job: Job, index: int, work: Path, names: list[str], slot: int) -> int | None:
    """Play slice ``index`` in its own Forge process and add its games to the job, numbered after the
    slices before it. ``slot`` is which of the job's parallel places it uses: the first keeps the
    container's HOME (Forge writes its preferences and logs there), the others get their own."""
    offset = sum(job.slices[:index])
    seed = slice_seed(job.seed, index)
    cmd = [
        "nice",
        "-n",
        str(NICE),
        "java",
        f"-Xmx{XMX}",
        "-Djava.awt.headless=true",
        # Java takes its home from the user account, never $HOME, so each place names its own
        f"-Duser.home={slot_home(slot)}",
        "-jar",
        str(forge_jar()),
        "sim",
        "-D",
        str(work),
        "-d",
        *names,
        "-f",
        job.format,
        "-n",
        str(job.slices[index]),
        "-c",
        str(GAME_SECONDS),
        *(["-s", str(seed)] if seed is not None else []),
        "-q",
    ]
    env = None
    with job.lock:
        if job.cancelled or job.timed_out:
            job.slice_state[index] = "cancelled"
            return None
        proc = subprocess.Popen(
            cmd,
            cwd=FORGE_HOME,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        job.procs[index] = proc
        job.slice_state[index] = "running"
    tag = f"[slice {index + 1}] " if len(job.slices) > 1 else ""
    stopped_slow = False
    outcome: dict[str, Any] = {}  # the turn and each loser's reason, for the next result line
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        turn, lost = OUTCOME_TURN.search(line), OUTCOME_LOST.search(line)
        if turn:
            outcome["turn"] = int(turn.group("turn"))
        elif lost:
            outcome.setdefault("lost", {})[lost.group("seat")] = lost.group("why").strip()[:120]
        result = parse_result(line)
        if result:
            result["game"] += offset
            result.update(outcome)
            outcome = {}
            if stopped_slow:
                # Forge ends a game that runs past its time limit as a draw; count it as one
                # even if a winner is printed, and mark why.
                result.update(winner=None, draw=True, stopped_slow=True)
                result.pop("winner_text", None)
                stopped_slow = False
        with job.lock:
            job.tail = (job.tail + [(tag + line)[:300]])[-80:]
            if index == 0 and not job.header and HEADER.search(line):
                job.header = line.strip()[:300]
            if STOPPED_SLOW.search(line):
                stopped_slow = True
            if result:
                job.results.append(result)
            elif LOAD_PROBLEM.search(line) and line.strip()[:300] not in job.load_problems:
                job.load_problems.append(line.strip()[:300])
    code = proc.wait()
    proc.stdout.close()
    with job.lock:
        job.procs.pop(index, None)
        job.slice_state[index] = "done" if code == 0 else "failed"
    return code


def run_job(job: Job) -> None:
    work = Path(tempfile.mkdtemp(prefix="forge-job-"))
    try:
        names = []
        for i, deck in enumerate(job.decks, start=1):
            fname = f"D{i}.dck"
            (work / fname).write_text(deck_file(i, deck, job.format), "utf-8")
            names.append(fname)
        forge_jar()  # no jar: the job fails here, before any slice starts
        job.started = time.time()
        job.state = "running"
        deadline = job.started + JOB_TIMEOUT
        timer = threading.Timer(JOB_TIMEOUT, lambda: _time_out(job))
        timer.start()
        codes: list[int | None] = [None] * len(job.slices)
        pending = list(range(len(job.slices)))
        pending_lock = threading.Lock()

        def place(slot: int) -> None:
            # Each parallel place takes the next slice in order until none is left.
            while True:
                with pending_lock:
                    if not pending or job.cancelled or time.time() >= deadline:
                        return
                    index = pending.pop(0)
                try:
                    codes[index] = run_slice(job, index, work, names, slot)
                except Exception as exc:  # one slice failing ends the job as failed, never the service
                    with job.lock:
                        job.slice_state[index] = "failed"
                        job.tail.append(f"service error: {exc!r}"[:300])

        try:
            places = [
                threading.Thread(target=place, args=(slot,), daemon=True) for slot in range(job.parallel)
            ]
            for t in places:
                t.start()
            for t in places:
                t.join()
        finally:
            timer.cancel()
        failed = [c for c in codes if c not in (0, None)]
        job.returncode = failed[0] if failed else (0 if all(c == 0 for c in codes) else None)
        if job.cancelled:
            job.state = "cancelled"
        elif time.time() >= deadline:
            job.state = "timeout"
        elif job.returncode == 0 and job.results:
            job.state = "done"
        else:
            job.state = "failed"
    except Exception as exc:  # a failed job is reported, never fatal to the service
        job.state = "failed"
        job.tail.append(f"service error: {exc!r}"[:300])
    finally:
        job.finished = time.time()
        with job.lock:
            job.slice_state = [st if st != "queued" else job.state for st in job.slice_state]
        shutil.rmtree(work, ignore_errors=True)


def _time_out(job: Job) -> None:
    """The job's time limit: no group starts after this (run_slice checks it under the same lock),
    and the running ones are killed."""
    with job.lock:
        job.timed_out = True
    _kill(job)


def _kill(job: Job) -> None:
    with job.lock:
        procs = list(job.procs.values())
    for proc in procs:
        if proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


def worker() -> None:
    while True:
        job = QUEUE.get()
        if job.state == "queued":
            run_job(job)
        prune()


def prune() -> None:
    cutoff = time.time() - KEEP_SECONDS
    with JOBS_LOCK:
        for jid in [j.id for j in JOBS.values() if j.finished and j.finished < cutoff]:
            JOBS.pop(jid, None)


def validate(body: dict[str, Any]) -> tuple[list[dict[str, Any]], int, str, list[str]]:
    fmt = body.get("format", "Commander")
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {sorted(FORMATS)}")
    games = body.get("games")
    if not isinstance(games, int) or isinstance(games, bool) or not 1 <= games <= MAX_GAMES:
        raise ValueError(f"games must be a whole number from 1 to {MAX_GAMES}")
    decks = body.get("decks")
    if not isinstance(decks, list) or not 2 <= len(decks) <= MAX_DECKS:
        raise ValueError(f"decks must list 2 to {MAX_DECKS} decks")
    clean: list[dict[str, Any]] = []
    unknown: list[str] = []
    for deck in decks:
        if not isinstance(deck, dict):
            raise ValueError("each deck is an object with commander and main, or a precon name")
        if "precon" in deck:
            # A bundled precon by name (GET /precons), used as an opponent.
            match = [p for p in PRECONS if p["name"] == deck["precon"]]
            if not match or fmt != "Commander":
                raise ValueError(f"no bundled Commander precon named {deck['precon']!r}")
            deck = match[0]
        commander = deck.get("commander") or []
        main = deck.get("main") or []
        if not isinstance(commander, list) or not isinstance(main, list):
            raise ValueError("commander and main are lists")
        if fmt == "Commander" and not 1 <= len(commander) <= 2:
            raise ValueError("a Commander deck names one or two commanders")
        out = {"commander": [], "main": []}
        for name in commander:
            if not isinstance(name, str):
                raise ValueError("commander names are strings")
            hit = resolve(name)
            if hit is None:
                unknown.append(name)
            else:
                out["commander"].append(hit)
        for row in main:
            if not (
                isinstance(row, list)
                and len(row) == 2
                and isinstance(row[0], int)
                and not isinstance(row[0], bool)
                and isinstance(row[1], str)
                and 1 <= row[0] <= 250
            ):
                raise ValueError("main rows are [count, name]")
            hit = resolve(row[1])
            if hit is None:
                unknown.append(row[1])
            else:
                out["main"].append([row[0], hit])
        clean.append(out)
    return clean, games, fmt, unknown


class Handler(BaseHTTPRequestHandler):
    server_version = "forge-service"
    timeout = 30  # seconds a connection may sit idle before its thread lets go

    def log_message(self, fmt: str, *args: Any) -> None:  # one short line per request
        print(f"{self.command} {self.path.split('?')[0]} {args[1] if len(args) > 1 else ''}", flush=True)

    def send(self, status: int, payload: dict[str, Any]) -> None:
        data = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 <= length <= MAX_BODY:
            raise ValueError("request too large")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("request body must be a JSON object")
        return data

    def do_GET(self) -> None:
        if self.path == "/health":
            with JOBS_LOCK:
                busy = any(j.state == "running" for j in JOBS.values())
            self.send(
                200,
                {
                    "status": "ok",
                    "forge_version": VERSION,
                    "cards": len(NAMES),
                    "aliases": len(ALIASES),
                    "busy": busy,
                    "queued": QUEUE.qsize(),
                    "max_parallel": MAX_PARALLEL,
                },
            )
            return
        if self.path == "/precons":
            self.send(
                200,
                {
                    # newest first, with each precon's estimated bracket (precons.py) when the
                    # image has one, so the gateway can seat opponents of the deck's bracket
                    "precons": [
                        {
                            "name": p["name"],
                            "release_date": p["release_date"],
                            "commander": p["commander"],
                            **({"bracket": p["bracket"]} if isinstance(p.get("bracket"), int) else {}),
                            **({"game_changers": p["game_changers"]} if p.get("game_changers") else {}),
                        }
                        for p in PRECONS
                    ]
                },
            )
            return
        m = re.fullmatch(r"/jobs/([0-9a-f]{16})", self.path)
        if m:
            with JOBS_LOCK:
                job = JOBS.get(m.group(1))
            if job is None:
                self.send(404, {"error": "no such job"})
            else:
                self.send(200, job.view())
            return
        self.send(404, {"error": "not found"})

    def do_POST(self) -> None:
        try:
            body = self.body()
            if self.path == "/check":
                names = body.get("names")
                if (
                    not isinstance(names, list)
                    or len(names) > 500
                    or not all(isinstance(n, str) for n in names)
                ):
                    raise ValueError("names must be a list of at most 500 strings")
                self.send(200, {"resolved": {n: resolve(n) for n in names}})
                return
            if self.path == "/jobs":
                decks, games, fmt, unknown = validate(body)
                if unknown:
                    self.send(422, {"error": "cards Forge does not have", "unknown": sorted(set(unknown))})
                    return
                if QUEUE.qsize() >= MAX_QUEUE:
                    self.send(429, {"error": "simulation queue is full; try again later"})
                    return
                seed = body.get("seed")
                if seed is not None and (
                    not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2**63
                ):
                    # Forge reads an argument starting with "-" as a flag, so seeds are never negative.
                    raise ValueError("seed must be a whole number from 0 to 2**63 - 1")
                parallel = body.get("parallel", 1)
                if not isinstance(parallel, int) or isinstance(parallel, bool) or not 1 <= parallel <= 4:
                    raise ValueError("parallel must be a whole number from 1 to 4")
                # More than FORGE_MAX_PARALLEL is not refused: the job plays as many at once as allowed.
                job = Job(decks, games, fmt, seed, parallel)
                with JOBS_LOCK:
                    JOBS[job.id] = job
                QUEUE.put(job)
                self.send(202, job.view())
                return
            self.send(404, {"error": "not found"})
        except (ValueError, TypeError, AttributeError) as exc:  # JSONDecodeError is a ValueError
            self.send(400, {"error": str(exc)})

    def do_DELETE(self) -> None:
        m = re.fullmatch(r"/jobs/([0-9a-f]{16})", self.path)
        with JOBS_LOCK:
            job = JOBS.get(m.group(1)) if m else None
        if job is None:
            self.send(404, {"error": "no such job"})
            return
        job.cancelled = True
        if job.state == "queued":
            job.state = "cancelled"
            job.finished = time.time()
        else:
            _kill(job)
        self.send(200, job.view())


def main() -> None:
    threading.Thread(target=worker, daemon=True).start()
    port = int(os.environ.get("FORGE_PORT", "8000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print(
        f"forge-service {VERSION}: {len(NAMES)} card names, Xmx {XMX}, "
        f"up to {MAX_PARALLEL} at once, port {port}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
