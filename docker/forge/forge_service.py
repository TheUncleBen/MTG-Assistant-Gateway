"""Job service around Forge's headless ``sim`` mode, for the gateway's deck simulations.

Forge itself runs unchanged: this file only writes deck files, starts ``java -jar <forge> sim``
and reads what it prints. Its Java processes exit when the job ends, so an idle container holds no
Forge memory. Only the gateway reaches this service, over the stack's private network.

A job's games are cut into fixed slices by their number alone (``slices``), each slice one Forge
process with its own seed made from the job's seed, so the cut and the seeds are the same however
many slices play at once (Forge does not always replay a game exactly from its seed, so the games
themselves can still differ). A job asks for ``parallel`` slices at once. The number of places,
one Forge process each, shared by all jobs, is FORGE_MAX_PARALLEL when set, else what the
container's CPUs and memory fit (``max_parallel``), because each process needs its own CPU time and
memory (FORGE_XMX) inside the container's limit. Jobs start in the order they
came, each when a place is free, on the free places it asked for (fewer when fewer are free); so a
job that leaves places unused (a comparison's first deck, say) lets the next one play beside it.

Endpoints (JSON):
  GET    /health          status, Forge version, busy flag, queue length, free places, solo test
                          support and the longest a game may run (game_seconds)
  GET    /precons         the bundled Commander precons a deck entry can name as {"precon": name}
  POST   /check           {"names": [...]} -> each name's spelling in Forge's card list, or null
  POST   /jobs            {"decks": [{"commander": [...], "main": [[count, name], ...]}], "games": n,
                           "seed"?: s, "parallel"?: p}
                          or a solo test: {"solo": {"opponents"?: 1-3, "until_turn"?: 1-30, "game_seconds"?:
                          10-FORGE_GAME_SECONDS}, "decks": [one deck], ...}: the deck plays against
                          opponents who do nothing (solo_opponent)
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
# Where Docker can't cap memory (Raspberry Pi OS turns the kernel's memory controller off by
# default, and Docker then drops a stack's memory limit), these keep Forge in bounds from inside
# the container, with nothing to change on the machine; they are not a hard limit as the kernel's
# memory controller is. FORGE_MEMORY_BUDGET (the stack passes FORGE_MEMORY_LIMIT) sizes the places
# when no cgroup limit can be read. Each Java gets a ceiling on its class data on top of FORGE_XMX,
# and is the kernel's first pick when the machine runs out of memory, so a Forge game is stopped
# before the gateway or other services (raising oom_score_adj needs no privilege; lowering it does).
MEMORY_BUDGET = os.environ.get("FORGE_MEMORY_BUDGET", "")
MAX_METASPACE = os.environ.get("FORGE_MAX_METASPACE", "256m")


def oom_score(text: str) -> tuple[int, str]:
    """The out-of-memory score each Java runs with (0: none), and why none when it is not set.
    Forge runs unprivileged, so choom can only raise the score, to at most 1000; any other value
    would stop every Java from starting, so it is left out and the start log says why."""
    try:
        value = int(text.strip())
    except ValueError:
        return 0, f"FORGE_OOM_SCORE_ADJ={text!r} is not a whole number from 0 to 1000"
    if value == 0:
        return 0, "FORGE_OOM_SCORE_ADJ=0 turns it off"
    if not 1 <= value <= 1000:
        return 0, f"FORGE_OOM_SCORE_ADJ={value} is outside 1 to 1000 (Forge can only raise it)"
    return value, ""


OOM_SCORE_ADJ, OOM_SCORE_OFF = oom_score(os.environ.get("FORGE_OOM_SCORE_ADJ", "1000"))
# What one Forge process needs to play a four-player game at a usable speed: about 2 CPUs and
# 1.2 GB. Measured on GitHub's build machines with 1536M and 2 CPUs, one game per process: one game
# peaked near 600 MiB and two at once near 1,050 MiB, but two at once were slower than one on x86
# and three at once pushed games past the time limit, so 2 CPUs a game holds; 1.2 GB is kept as
# the safe figure until a Pi has been measured.
GAME_CPUS = 2.0
GAME_MEMORY = 1.2e9
# The most Forge processes one container runs at once, whatever its size or FORGE_MAX_PARALLEL.
MAX_PLACES = 16


def _read(path: str) -> str:
    try:
        return Path(path).read_text("utf-8").strip()
    except OSError:
        return ""


def machine_cpus() -> float:
    """The CPUs this container may use: its cgroup quota (a stack's ``cpus`` limit), else the
    machine's count."""
    quota = _read("/sys/fs/cgroup/cpu.max").split()  # cgroup v2: "200000 100000" or "max 100000"
    if len(quota) == 2 and quota[0].isdigit() and quota[1].isdigit() and int(quota[1]) > 0:
        return int(quota[0]) / int(quota[1])
    v1, period = _read("/sys/fs/cgroup/cpu/cpu.cfs_quota_us"), _read("/sys/fs/cgroup/cpu/cpu.cfs_period_us")
    if v1.isdigit() and period.isdigit() and int(period) > 0:
        return int(v1) / int(period)
    return float(os.cpu_count() or 1)


def size_bytes(text: str) -> int | None:
    """A size as Docker writes it ("1536M", "1.5g", "800000k" or plain bytes) in bytes; None if not one."""
    m = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*([kmg]?)b?\s*", text.lower())
    if not m:
        return None
    return int(float(m.group(1)) * {"": 1, "k": 2**10, "m": 2**20, "g": 2**30}[m.group(2)])


def machine_memory() -> int | None:
    """The memory this container may use in bytes: its cgroup limit (a stack's ``memory`` limit),
    else FORGE_MEMORY_BUDGET, else the machine's total; None when none can be read, or when a
    budget is set but is not a size (then one place, and the start log says why: never the whole
    machine's memory when the operator meant less)."""
    for path in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        value = _read(path)
        if value.isdigit() and int(value) < 2**60:  # cgroup v1 writes a huge number for "no limit"
            return int(value)
    if MEMORY_BUDGET.strip():
        return size_bytes(MEMORY_BUDGET) or None
    for line in _read("/proc/meminfo").splitlines():
        if line.startswith("MemTotal:") and line.split()[1].isdigit():
            return int(line.split()[1]) * 1024
    return None


def places_for(cpus: float, memory: int | None) -> int:
    """How many Forge processes fit: one per GAME_CPUS and GAME_MEMORY, from 1 to MAX_PLACES."""
    by_memory = int(memory // GAME_MEMORY) if memory else 1
    return max(1, min(MAX_PLACES, int(cpus // GAME_CPUS), by_memory))


def max_parallel(setting: str | None) -> tuple[int, str]:
    """Forge processes that may run at once, shared by all jobs in order, and where the number
    came from: FORGE_MAX_PARALLEL when set (1 to MAX_PLACES), else what the container's CPUs and memory fit
    (places_for). Each process takes up to FORGE_XMX of heap plus Java's own memory."""
    if setting and setting.strip():
        return max(1, min(MAX_PLACES, int(setting))), "FORGE_MAX_PARALLEL"
    return places_for(machine_cpus(), machine_memory()), "machine"


MAX_PARALLEL, PLACES_FROM = max_parallel(os.environ.get("FORGE_MAX_PARALLEL"))
# A job's games are cut into slices of at most this many, the same way at any parallel setting:
# one game per group, so every game of a run can play at once when there are places for them.
SLICE_GAMES = 1
JOB_TIMEOUT = int(os.environ.get("FORGE_JOB_TIMEOUT", "7200"))
# Forge's sim calls a game a draw after this many seconds (its -c flag; Forge's own default is 120,
# too short for most four-player Commander games). Four minutes keeps the slowest game inside a
# five-minute report.
GAME_SECONDS = int(os.environ.get("FORGE_GAME_SECONDS", "240"))
# Forge reads each card's script only when a game needs it (its own LOAD_CARD_SCRIPTS_LAZILY
# preference) instead of all ~35,000 at start-up: measured 7 s -> 3 s per Forge start on 4 CPUs,
# with the same seeded game. FORGE_LAZY_CARDS=0 loads them all again.
LAZY_CARDS = os.environ.get("FORGE_LAZY_CARDS", "1") != "0"
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

# The solo test: the deck (seat D1) plays against opponents who do nothing, so Forge's full rules
# play every card it has. Each opponent's commander costs {W}{W}{U}{U}{B}{B}{R}{R}{G}{G} and the rest
# of its deck is Wastes, which make only colourless mana, so it never casts anything.
SOLO_COMMANDER = "Progenitus"
SOLO_LAND = "Wastes"
SOLO_MAX_OPPONENTS = 3
SOLO_MAX_TURN = 30
SOLO_MIN_SECONDS = 10  # the shortest time limit a solo game may ask for (FORGE_GAME_SECONDS is the longest)
# Forge's game log lines a solo test reads (Forge 2.0.15, sim mode without -q), for seat D1 or
# its opponents D2 to D4:
#   "Turn: Turn 13 (Ai(1)-D1)"
#   "Land: Ai(1)-D1 played Forest (45)"
#   "Add To Stack: Ai(1)-D1 cast Shock targeting [Ai(4)-D4]"
#   "Life: Life: Ai(4)-D4 22 > 20"
#   "Damage: Bashful Beastie (70) deals 5 combat damage to Ai(2)-D2."
#   "Mulligan: Ai(1)-D1 has kept a hand of 7 cards"
SOLO_TURN = re.compile(r"^Turn: Turn \d+ \((?P<who>.+)\)\s*$")
SOLO_LAND_LINE = re.compile(r"^Land: Ai\(\d+\)-D1 played (?P<card>.+?)(?: \(\d+\))?\s*$")
SOLO_CAST = re.compile(r"^Add To Stack: Ai\(\d+\)-D1 cast (?P<card>.+?)(?: targeting .*)?\s*$")
SOLO_LIFE = re.compile(r"^Life: Life: Ai\(\d+\)-D(?P<seat>[2-4]) (?P<before>-?\d+) > (?P<after>-?\d+)\s*$")
SOLO_COMBAT = re.compile(
    r"^Damage: (?P<card>.+?) \(\d+\) deals (?P<n>\d+) combat damage to Ai\(\d+\)-D(?P<seat>[2-4])\b"
)
SOLO_KEPT = re.compile(r"^Mulligan: Ai\(\d+\)-D1 has kept a hand of (?P<n>\d+) cards")


def solo_opponent(fmt: str) -> dict[str, Any]:
    """An opponent who does nothing: Wastes and, in Commander, a commander it can never pay for."""
    if fmt == "Commander":
        return {"commander": [SOLO_COMMANDER], "main": [[99, SOLO_LAND]]}
    return {"commander": [], "main": [[60, SOLO_LAND]]}


class SoloGame:
    """What the deck did in one solo-test game, read from Forge's log line by line. Turns are the
    deck's own turns (its first is 1), whichever seat Forge lets start; what happens on an
    opponent's turn counts in the deck's turn before it (or its first)."""

    def __init__(self, commanders: list[str], opponents: int, life: int, until_turn: int) -> None:
        self.commanders = {norm(c) for c in commanders}
        self.until_turn = until_turn
        self.turn = 0
        self.life = {seat: life for seat in range(2, 2 + opponents)}
        # commander damage per opponent and commander: 21 from one commander puts a player out
        self.cmdr: dict[tuple[int, str], int] = {}
        self.out: set[int] = set()
        self.lands: list[int] = []
        self.casts: list[int] = []
        self.damage: list[int] = []
        self.kept: int | None = None
        self.commander_cast_turn: int | None = None
        self.first_out_turn: int | None = None
        self.all_out_turn: int | None = None
        self.past_limit = False
        self.began = False  # the first turn has started: what follows is the game, not loading

    def _at(self, series: list[int], n: int) -> None:
        i = max(self.turn, 1) - 1
        while len(series) <= i:
            series.append(0)
        series[i] += n

    def _out(self, seat: int) -> None:
        if seat in self.out:
            return
        self.out.add(seat)
        turn = max(self.turn, 1)
        if self.first_out_turn is None:
            self.first_out_turn = turn
        if len(self.out) == len(self.life) and self.all_out_turn is None:
            self.all_out_turn = turn

    def feed(self, line: str) -> None:
        """Read one log line. Sets ``past_limit`` when the deck's turn after ``until_turn`` begins;
        what happens from then on is past the solo test's last turn and is not counted."""
        m = SOLO_TURN.match(line)
        if m:
            self.began = True
            if m.group("who").endswith("-D1"):
                self.turn += 1
                if self.turn > self.until_turn:
                    self.past_limit = True
                    return
                for series in (self.lands, self.casts, self.damage):
                    self._at(series, 0)
            return
        if self.past_limit:
            return
        m = SOLO_KEPT.match(line)
        if m:
            self.kept = int(m.group("n"))
            return
        m = SOLO_LAND_LINE.match(line)
        if m:
            self._at(self.lands, 1)
            return
        m = SOLO_CAST.match(line)
        if m:
            self._at(self.casts, 1)
            if self.commander_cast_turn is None and norm(m.group("card")) in self.commanders:
                self.commander_cast_turn = max(self.turn, 1)
            return
        m = SOLO_LIFE.match(line)
        if m:
            seat, before, after = int(m.group("seat")), int(m.group("before")), int(m.group("after"))
            if seat in self.life:
                self.life[seat] = after
                if after < before:
                    self._at(self.damage, before - after)
                if after <= 0:
                    self._out(seat)
            return
        m = SOLO_COMBAT.match(line)
        if m and norm(m.group("card")) in self.commanders:
            seat, key = int(m.group("seat")), (int(m.group("seat")), norm(m.group("card")))
            if seat in self.life:
                self.cmdr[key] = self.cmdr.get(key, 0) + int(m.group("n"))
                if self.cmdr[key] >= 21:
                    self._out(seat)

    def view(self) -> dict[str, Any]:
        turns = min(self.turn, self.until_turn)
        return {
            "turns": turns,
            "kept": self.kept,
            "commander_cast_turn": self.commander_cast_turn,
            "first_out_turn": self.first_out_turn,
            "won_turn": self.all_out_turn,
            "lands_by_turn": self.lands[:turns],
            "casts_by_turn": self.casts[:turns],
            "damage_by_turn": self.damage[:turns],
            "commander_damage": max(self.cmdr.values(), default=0),
        }


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
    possible, larger first (with groups of up to 5: 12 -> [4, 4, 4], 7 -> [4, 3]). It depends on
    the number of games only."""
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
        self,
        decks: list[dict[str, Any]],
        games: int,
        fmt: str,
        seed: int | None = None,
        parallel: int = 1,
        solo: dict[str, int] | None = None,
    ) -> None:
        # A solo test ({"opponents", "until_turn"}): decks[0] plays against solo_opponent decks.
        self.solo = solo
        if solo:
            decks = [decks[0]] + [solo_opponent(fmt) for _ in range(solo["opponents"])]
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
            **({"solo": self.solo} if self.solo else {}),
            "queued_s": round((self.started or time.time()) - self.created, 1),
            "run_s": round((self.finished or time.time()) - self.started, 1) if self.started else None,
            "forge_version": VERSION,
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()
QUEUE: queue.Queue[Job] = queue.Queue()
# The free places (0 to FORGE_MAX_PARALLEL - 1), shared by all jobs; a place is one Forge process
# with its own home (place_home). Taken by the worker for a job, given back as each one finishes.
PLACES = threading.Condition()
FREE_PLACES: list[int] = list(range(MAX_PARALLEL))
HELD: list[Job] = []  # the job the worker holds while it waits for a place (counted as queued)


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


def place_home(slot: int) -> Path:
    """Forge's home for parallel place ``slot``, where it reads its preferences and writes its log:
    the container's HOME for the first place, a folder of its own below it for each other one."""
    base = Path(os.environ.get("HOME", "/data"))
    home = base / f"slot-{slot}" if slot else base
    prefs = home / ".forge" / "preferences" / "forge.preferences"
    prefs.parent.mkdir(parents=True, exist_ok=True)
    old = prefs.read_text("utf-8").splitlines() if prefs.exists() else []
    # Only the one line is set; any other preference already in the file stays as it is.
    lines = [ln for ln in old if not ln.startswith("LOAD_CARD_SCRIPTS_LAZILY=")]
    lines.append(f"LOAD_CARD_SCRIPTS_LAZILY={'true' if LAZY_CARDS else 'false'}")
    if lines != old:
        prefs.write_text("\n".join(lines) + "\n", "utf-8")
    return home


def run_slice(job: Job, index: int, work: Path, names: list[str], slot: int) -> int | None:
    """Play slice ``index`` in its own Forge process and add its games to the job, numbered after the
    slices before it. ``slot`` is which parallel place it uses, each with its own home (place_home)."""
    offset = sum(job.slices[:index])
    seed = slice_seed(job.seed, index)
    cmd = [
        *(["choom", "-n", str(OOM_SCORE_ADJ), "--"] if OOM_SCORE_ADJ and shutil.which("choom") else []),
        "nice",
        "-n",
        str(NICE),
        "java",
        f"-Xmx{XMX}",
        f"-XX:MaxMetaspaceSize={MAX_METASPACE}",
        # Speed, measured on 2 CPUs of an x86 PC with the same seeded games (2026-10-21): together
        # about 6% faster a game. The serial collector leaves both CPUs to the game; the class
        # archive (Java 19 and later) is written in the place's home by its first game and lets
        # each later Java start faster (4.6 s to 4.2 s). A stale archive is rebuilt by itself.
        "-XX:+UseSerialGC",
        "-XX:+AutoCreateSharedArchive",
        f"-XX:SharedArchiveFile={place_home(slot) / f'forge-{VERSION}.jsa'}",
        "-Djava.awt.headless=true",
        # Java takes its home from the user account, never from $HOME, so without this every
        # place shared one preferences and log folder.
        f"-Duser.home={place_home(slot)}",
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
        str((job.solo or {}).get("game_seconds", GAME_SECONDS)),
        *(["-s", str(seed)] if seed is not None else []),
        # A solo test reads the game log (SoloGame); other jobs need only the results.
        *([] if job.solo else ["-q"]),
    ]
    with job.lock:
        if job.cancelled or job.timed_out:
            job.slice_state[index] = "cancelled"
            return None
        proc = subprocess.Popen(
            cmd,
            cwd=FORGE_HOME,
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
    solo = new_solo(job) if job.solo else None
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.rstrip("\n")
        if not line.strip():
            continue
        if solo is not None:
            # Forge prints a game's log only once the game has ended (or its time limit stopped it),
            # so a solo game plays on past ``until_turn``; what comes after that turn isn't counted.
            solo.feed(line)
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
            if solo is not None:
                view = solo.view()
                if result.get("winner") == 1 and view["won_turn"] is None and not solo.past_limit:
                    view["won_turn"] = max(solo.turn, 1)  # won another way (poison, a card)
                if solo.past_limit and view["won_turn"] is None:
                    result["turn_limit"] = True  # not won by the solo test's last turn
                result["solo"] = view
                solo = new_solo(job)
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
            elif (
                not (solo is not None and solo.began)  # a solo game's own log is not a load problem
                and LOAD_PROBLEM.search(line)
                and line.strip()[:300] not in job.load_problems
            ):
                job.load_problems.append(line.strip()[:300])
    code = proc.wait()
    proc.stdout.close()
    with job.lock:
        job.procs.pop(index, None)
        job.slice_state[index] = "done" if code == 0 else "failed"
    return code


def new_solo(job: Job) -> SoloGame:
    life = 40 if job.format == "Commander" else 20
    assert job.solo is not None
    return SoloGame(job.decks[0]["commander"], job.solo["opponents"], life, job.solo["until_turn"])


def run_job(job: Job, slots: list[int] | None = None) -> None:
    """Play the job's slices on ``slots`` (its places, one Forge process each; by default places
    0 to parallel - 1). Each place given back with ``release_place`` once it has no slice left."""
    slots = list(range(job.parallel)) if slots is None else slots
    work = Path(tempfile.mkdtemp(prefix="forge-job-"))
    started_places: set[int] = set()
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
        holding = [len(slots)]  # places this job still plays on

        def place(slot: int) -> None:
            # Each parallel place takes the next slice in order until none is left, then lets go.
            # While another job waits for a place, a job on more than one place gives one back
            # after its group ends, so one run never holds every place until its last game. The
            # give-back counts itself in the same locked step, so two places never both let go
            # of what was the job's last but one.
            gave_back = False
            try:
                while True:
                    with pending_lock:
                        if not pending or job.cancelled or time.time() >= deadline:
                            return
                        if holding[0] > 1 and waiting():
                            holding[0] -= 1
                            gave_back = True
                            return
                        index = pending.pop(0)
                    try:
                        codes[index] = run_slice(job, index, work, names, slot)
                    except Exception as exc:  # one slice failing ends the job as failed, never the service
                        with job.lock:
                            job.slice_state[index] = "failed"
                            job.tail.append(f"service error: {exc!r}"[:300])
            finally:
                if not gave_back:
                    with pending_lock:
                        holding[0] -= 1
                release_place(slot)

        try:
            places = [threading.Thread(target=place, args=(slot,), daemon=True) for slot in slots]
            started_places.update(slots)
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
        for slot in slots:
            if slot not in started_places:  # the job failed before its places started
                release_place(slot)


def release_place(slot: int) -> None:
    with PLACES:
        if slot not in FREE_PLACES and slot < MAX_PARALLEL:
            FREE_PLACES.append(slot)
            FREE_PLACES.sort()
        PLACES.notify_all()


def waiting() -> bool:
    """Whether a job is waiting for a place (the one the worker holds)."""
    return any(j.state == "queued" for j in list(HELD))


def take_places(job: Job) -> list[int] | None:
    """Wait for a free place, then take as many as the job asks for (at least one): the places it
    plays on. None when the job was cancelled while it waited."""
    with PLACES:
        while not FREE_PLACES and job.state == "queued":
            PLACES.wait(timeout=1.0)  # a cancel only sets the job's state; checked each second
        if job.state != "queued":
            return None
        slots = FREE_PLACES[: job.parallel]
        del FREE_PLACES[: len(slots)]
        return slots


def run_and_prune(job: Job, slots: list[int]) -> None:
    run_job(job, slots)
    prune()


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
    """Start jobs in the order they came, each once a place is free; the next job waits for a
    place, never overtaking an earlier one."""
    while True:
        job = QUEUE.get()
        HELD[:] = [job]
        try:
            slots = take_places(job) if job.state == "queued" else None
        finally:
            HELD.clear()
        if slots:
            threading.Thread(target=run_and_prune, args=(job, slots), daemon=True).start()
        else:
            prune()


def prune() -> None:
    cutoff = time.time() - KEEP_SECONDS
    with JOBS_LOCK:
        for jid in [j.id for j in JOBS.values() if j.finished and j.finished < cutoff]:
            JOBS.pop(jid, None)


def validate_solo(body: dict[str, Any], fmt: str) -> dict[str, int] | None:
    """A solo test's settings, or None for a game against other decks."""
    solo = body.get("solo")
    if solo is None:
        return None
    if not isinstance(solo, dict):
        raise ValueError("solo is an object with opponents and until_turn")
    opponents = solo.get("opponents", SOLO_MAX_OPPONENTS if fmt == "Commander" else 1)
    until = solo.get("until_turn", 10)
    seconds = solo.get("game_seconds", GAME_SECONDS)
    for value, low, top, name in (
        (opponents, 1, SOLO_MAX_OPPONENTS, "opponents"),
        (until, 1, SOLO_MAX_TURN, "until_turn"),
        (seconds, min(SOLO_MIN_SECONDS, GAME_SECONDS), GAME_SECONDS, "game_seconds"),
    ):
        if not isinstance(value, int) or isinstance(value, bool) or not low <= value <= top:
            raise ValueError(f"solo {name} must be a whole number from {low} to {top}")
    out = {"opponents": opponents, "until_turn": until}
    if seconds != GAME_SECONDS:
        out["game_seconds"] = seconds
    return out


def validate(body: dict[str, Any]) -> tuple[list[dict[str, Any]], int, str, list[str]]:
    fmt = body.get("format", "Commander")
    if fmt not in FORMATS:
        raise ValueError(f"format must be one of {sorted(FORMATS)}")
    games = body.get("games")
    if not isinstance(games, int) or isinstance(games, bool) or not 1 <= games <= MAX_GAMES:
        raise ValueError(f"games must be a whole number from 1 to {MAX_GAMES}")
    decks = body.get("decks")
    if body.get("solo") is not None:
        if not isinstance(decks, list) or len(decks) != 1:
            raise ValueError("a solo test lists exactly one deck")
    elif not isinstance(decks, list) or not 2 <= len(decks) <= MAX_DECKS:
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
                    "queued": QUEUE.qsize() + len(HELD),
                    "max_parallel": MAX_PARALLEL,
                    # "machine" when the number follows the container's CPUs and memory
                    "max_parallel_from": PLACES_FROM,
                    # places no job holds now: the gateway starts a run on the copy with the most
                    "free_places": len(FREE_PLACES),
                    # what a solo test may ask for: this image plays them, each game at most this long
                    "solo": True,
                    "game_seconds": GAME_SECONDS,
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
                if QUEUE.qsize() + len(HELD) >= MAX_QUEUE:
                    self.send(429, {"error": "simulation queue is full; try again later"})
                    return
                seed = body.get("seed")
                if seed is not None and (
                    not isinstance(seed, int) or isinstance(seed, bool) or not 0 <= seed < 2**63
                ):
                    # Forge reads an argument starting with "-" as a flag, so seeds are never negative.
                    raise ValueError("seed must be a whole number from 0 to 2**63 - 1")
                parallel = body.get("parallel", 1)
                if (
                    not isinstance(parallel, int)
                    or isinstance(parallel, bool)
                    or not 1 <= parallel <= MAX_PLACES
                ):
                    raise ValueError(f"parallel must be a whole number from 1 to {MAX_PLACES}")
                # More than FORGE_MAX_PARALLEL is not refused: the job plays as many at once as allowed.
                job = Job(decks, games, fmt, seed, parallel, validate_solo(body, fmt))
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
        f"up to {MAX_PARALLEL} at once ({'from the machine' if PLACES_FROM == 'machine' else PLACES_FROM}), "
        f"port {port}",
        flush=True,
    )
    if MEMORY_BUDGET.strip() and not size_bytes(MEMORY_BUDGET):
        print(f"FORGE_MEMORY_BUDGET={MEMORY_BUDGET!r} is not a size such as 1536M: one place", flush=True)
    if OOM_SCORE_OFF:
        print(f"no out-of-memory score for Forge's Java: {OOM_SCORE_OFF}", flush=True)
    elif not shutil.which("choom"):
        print("no out-of-memory score for Forge's Java: choom is missing", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
