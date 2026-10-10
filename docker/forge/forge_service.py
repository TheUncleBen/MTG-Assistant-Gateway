"""Job service around Forge's headless ``sim`` mode, for the gateway's deck simulations.

Forge itself runs unchanged: this file only writes deck files, starts ``java -jar <forge> sim``
and reads what it prints. One simulation runs at a time and its Java process exits when the job
ends, so an idle container holds no Forge memory. Only the gateway reaches this service, over the
stack's private network.

Endpoints (JSON):
  GET    /health          status, Forge version, busy flag, queue length
  GET    /precons         the bundled Commander precons a deck entry can name as {"precon": name}
  POST   /check           {"names": [...]} -> each name's spelling in Forge's card list, or null
  POST   /jobs            {"decks": [{"commander": [...], "main": [[count, name], ...]}], "games": n}
  GET    /jobs/<id>       state, games done, per-game winners, cards Forge could not load
  DELETE /jobs/<id>       stop a queued or running job
"""

from __future__ import annotations

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
JOB_TIMEOUT = int(os.environ.get("FORGE_JOB_TIMEOUT", "7200"))
# Forge's sim calls a game a draw after this many seconds (its -c flag; Forge's own default is 120,
# too short for a four-player Commander game on a Raspberry Pi).
GAME_SECONDS = int(os.environ.get("FORGE_GAME_SECONDS", "600"))
KEEP_SECONDS = int(os.environ.get("FORGE_KEEP_SECONDS", "3600"))
FORMATS = {"Commander", "Constructed"}
MAX_DECKS = 4
MAX_BODY = 256 * 1024

# Forge prints one line per finished game. Confirmed against Forge 2.0.15 output in CI
# (scripts/ci_smoke_forge.sh); unrecognised lines are kept in the log tail, never guessed at.
RESULT = re.compile(r"Game (?P<game>\d+) ended in (?P<ms>\d+) ms\. (?P<rest>.*)$")
WINNER = re.compile(r"(?P<who>\S.*?) has won", re.IGNORECASE)
DRAW = re.compile(r"\bdraw\b", re.IGNORECASE)
STOPPED_SLOW = re.compile(r"Stopping slow match as draw", re.IGNORECASE)
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


class Job:
    def __init__(self, decks: list[dict[str, Any]], games: int, fmt: str, seed: int | None = None) -> None:
        self.seed = seed
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
        self.returncode: int | None = None
        self.proc: subprocess.Popen[str] | None = None
        self.cancelled = False

    def view(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "state": self.state,
            "format": self.format,
            "games_requested": self.games,
            "games_done": len(self.results),
            "results": self.results,
            "load_problems": self.load_problems[:40],
            "log_tail": self.tail[-40:],
            "returncode": self.returncode,
            "seed": self.seed,
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


def run_job(job: Job) -> None:
    work = Path(tempfile.mkdtemp(prefix="forge-job-"))
    try:
        names = []
        for i, deck in enumerate(job.decks, start=1):
            fname = f"D{i}.dck"
            (work / fname).write_text(deck_file(i, deck, job.format), "utf-8")
            names.append(fname)
        cmd = [
            "nice",
            "-n",
            str(NICE),
            "java",
            f"-Xmx{XMX}",
            "-Djava.awt.headless=true",
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
            str(job.games),
            "-c",
            str(GAME_SECONDS),
            *(["-s", str(job.seed)] if job.seed is not None else []),
            "-q",
        ]
        job.started = time.time()
        job.state = "running"
        job.proc = subprocess.Popen(
            cmd,
            cwd=FORGE_HOME,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
            start_new_session=True,
        )
        deadline = job.started + JOB_TIMEOUT
        timer = threading.Timer(JOB_TIMEOUT, lambda: _kill(job))
        timer.start()
        stopped_slow = False
        try:
            assert job.proc.stdout is not None
            for raw in job.proc.stdout:
                line = raw.rstrip("\n")
                if not line.strip():
                    continue
                job.tail = (job.tail + [line[:300]])[-80:]
                if STOPPED_SLOW.search(line):
                    stopped_slow = True
                result = parse_result(line)
                if result:
                    if stopped_slow:
                        # Forge ends a game that runs past its time limit as a draw, yet still prints
                        # a winner on the result line; count it as the draw it is.
                        result.update(winner=None, draw=True, stopped_slow=True)
                        result.pop("winner_text", None)
                        stopped_slow = False
                    job.results.append(result)
                elif LOAD_PROBLEM.search(line):
                    job.load_problems.append(line.strip()[:300])
            job.returncode = job.proc.wait()
        finally:
            timer.cancel()
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
        job.proc = None
        shutil.rmtree(work, ignore_errors=True)


def _kill(job: Job) -> None:
    proc = job.proc
    if proc and proc.poll() is None:
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
    if not isinstance(games, int) or not 1 <= games <= MAX_GAMES:
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
        if length > MAX_BODY:
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
                },
            )
            return
        if self.path == "/precons":
            self.send(
                200,
                {
                    "precons": [
                        {"name": p["name"], "release_date": p["release_date"], "commander": p["commander"]}
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
                    not isinstance(seed, int) or isinstance(seed, bool) or not -(2**63) <= seed < 2**63
                ):
                    raise ValueError("seed must be a 64-bit whole number")
                job = Job(decks, games, fmt, seed)
                with JOBS_LOCK:
                    JOBS[job.id] = job
                QUEUE.put(job)
                self.send(202, job.view())
                return
            self.send(404, {"error": "not found"})
        except (ValueError, json.JSONDecodeError) as exc:
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
    print(f"forge-service {VERSION}: {len(NAMES)} card names, Xmx {XMX}, port {port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
