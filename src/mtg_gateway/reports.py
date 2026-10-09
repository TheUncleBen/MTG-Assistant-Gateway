"""Stored deck reports: the numbers a "test my deck" run produces, kept per user over time.

A report is one read of a deck plus what the gateway can compute or ask Mystic Forge for:
the deck statistics from ``deck_stats`` (always), a goldfish simulation and a decklist
validation (when the research service is configured and reachable). Reports are owner-scoped,
bounded per user and per deck, and never change anything on Archidekt. Mystic Forge stays
stateless: its results are copied into the gateway's own table, which is what the history
page and the assistant read back.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from typing import Any

from . import deck_stats
from .archidekt import Deck
from .db import Database, _like
from .decklist import DecklistError, front_faces, parse_decklist, to_text
from .decks import DeckError, DeckService, _clean_deck_id, current_client, deck_to_text
from .mf_proxy import MysticForgeProxy, is_busy

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    owner_sub TEXT NOT NULL,
    deck_id TEXT NOT NULL,
    deck_name TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    taken_at INTEGER NOT NULL,
    stats_json TEXT NOT NULL,
    goldfish_json TEXT,
    validation_json TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS reports_owner ON reports(owner_sub, taken_at);
CREATE INDEX IF NOT EXISTS reports_deck ON reports(owner_sub, deck_id, taken_at);
"""

MAX_REPORTS_PER_USER = 200
MAX_GAMES = 2000
DEFAULT_GAMES = 300
# Numbers the history page trends over time. Each is a path into the stats dict.
TREND_KEYS = (
    "card_count",
    "average_mana_value",
    "land_count",
    "price_total",
    "salt_total",
)


class _Flight:
    """The run in progress for one (member, deck) and the requests waiting on it."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0
        self.finished = 0
        self.report_id: str | None = None


class ReportService:
    def __init__(
        self,
        db: Database,
        decks: DeckService,
        mf: MysticForgeProxy | None,
        *,
        min_interval: int = 600,
    ):
        self.db = db
        self.decks = decks
        self.mf = mf
        self.min_interval = min_interval
        # One run at a time per (member, deck): a request that arrives while one runs waits for
        # it and then reuses its report instead of starting another simulation.
        self._runs: dict[tuple[str, str], _Flight] = {}
        with db.tx() as c:
            for stmt in SCHEMA.split(";"):
                if stmt.strip():
                    c.execute(stmt)
            # The app (OAuth client id, or the browser) that ran each report: an app deletes only
            # its own reports through the API. Older reports have none and only the browser may.
            if "created_by_client" not in {r["name"] for r in c.execute("PRAGMA table_info(reports)")}:
                c.execute("ALTER TABLE reports ADD COLUMN created_by_client TEXT")

    # -- running --------------------------------------------------------------
    async def run(
        self,
        sub: str,
        deck_ref: str,
        *,
        simulate: bool = True,
        games: int = DEFAULT_GAMES,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Read the deck, compute its statistics, optionally simulate and validate it, store
        the result and return it. Refuses a second report of an unchanged deck within
        ``min_interval`` seconds (returns the existing one instead) unless ``options`` (the
        simulator's knobs, see ``sim_options``) are given, since they change the simulation."""
        if not isinstance(games, int) or isinstance(games, bool) or games < 10 or games > MAX_GAMES:
            raise DeckError("invalid", f"games must be an integer from 10 to {MAX_GAMES}")
        options = sim_options(options)
        key = (sub, _clean_deck_id(deck_ref))
        flight = self._runs.setdefault(key, _Flight())
        flight.users += 1
        seen = flight.finished
        try:
            async with flight.lock:
                if flight.finished > seen and flight.report_id:
                    # A run for this deck finished while this request waited: reuse its report
                    # instead of starting a second simulation (unless that run's research
                    # calls failed; then this request gets its own try).
                    out = self.get(sub, flight.report_id)
                    if _succeeded(
                        {"goldfish_json": out.get("goldfish"), "validation_json": out.get("validation")}
                    ):
                        out["reused"] = True
                        return out
                out = await self._run(sub, deck_ref, simulate=simulate, games=games, options=options)
                flight.report_id = out["report_id"]
                flight.finished += 1
                return out
        finally:
            flight.users -= 1
            if flight.users <= 0:
                self._runs.pop(key, None)

    async def run_text(
        self,
        sub: str,
        text: str,
        *,
        simulate: bool = True,
        games: int = DEFAULT_GAMES,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The same validation and simulation for a pasted or hypothetical list. Returned, never
        stored: reports are filed under an Archidekt deck, and a list has none to compare over
        time (clone or create the deck to keep its reports)."""
        if not isinstance(games, int) or isinstance(games, bool) or games < 10 or games > MAX_GAMES:
            raise DeckError("invalid", f"games must be an integer from 10 to {MAX_GAMES}")
        options = sim_options(options)
        try:
            cards = parse_decklist(text)
        except DecklistError as exc:
            raise DeckError("invalid", f"decklist could not be read: {exc}") from exc
        stats = deck_stats.compute_from_text(cards)
        main = to_text(cards)
        commander = (stats.get("commanders") or [None])[0]
        goldfish: dict[str, Any] | None = None
        validation: dict[str, Any] | None = None
        if self.mf is not None:
            # The research service reads names through Scryfall's collection lookup, which
            # knows double-faced cards by their front face only.
            sim_text, sim_commander = front_faces(main), _front_face(commander)
            validation = await self._mf(
                sub, "validate_decklist", {"decklist": sim_text, "commander": sim_commander}
            )
            if simulate and commander is None:
                goldfish = {"tool": "goldfish_run", "ok": False, "text": NO_COMMANDER_TEXT}
            elif simulate:
                goldfish = await self._mf(sub, "goldfish_run", {"deck": sim_text, "n": games, **options})
                _commander_aside(goldfish, sim_commander)
        return {
            "stored": False,
            "deck": {"id": None, "name": "pasted list", "card_count": stats["card_count"]},
            "stats": stats,
            "goldfish": goldfish,
            "validation": validation,
            "has_goldfish": bool(goldfish and goldfish.get("ok")),
            "decklist_text": main,
        }

    async def _run(
        self, sub: str, deck_ref: str, *, simulate: bool, games: int, options: dict[str, Any]
    ) -> dict[str, Any]:
        deck = await self.decks.get_any_deck(sub, deck_ref)
        latest = self._latest(sub, deck.id)
        now = int(time.time())
        if (
            not options
            and latest is not None
            and latest["fingerprint"] == deck.fingerprint()
            and now - int(latest["taken_at"]) < self.min_interval
            and _succeeded(latest)
            # a run that asks to simulate never reuses one made without a simulation
            and not (simulate and self.mf is not None and latest.get("goldfish_json") is None)
        ):
            out = self.get(sub, latest["id"])
            out["reused"] = True
            return out
        stats = deck_stats.compute(deck)
        goldfish: dict[str, Any] | None = None
        validation: dict[str, Any] | None = None
        if self.mf is not None:
            # Front faces only: see run_text.
            text = front_faces(deck_to_text(deck))
            commander = _front_face((stats.get("commanders") or [None])[0])
            validation = await self._mf(sub, "validate_decklist", {"decklist": text, "commander": commander})
            if simulate and commander is None:
                # Mystic Forge's text path would take the first line as the commander and
                # simulate a 59-card deck; refuse plainly instead.
                goldfish = {"tool": "goldfish_run", "ok": False, "text": NO_COMMANDER_TEXT}
            elif simulate:
                goldfish = await self._mf(sub, "goldfish_run", {"deck": text, "n": games, **options})
                _commander_aside(goldfish, commander)
        rid = "rep_" + secrets.token_urlsafe(9)
        with self.db.tx() as c:
            # Stored only while the member exists: a report still running when they deleted
            # their data is dropped, not written back for the deleted account.
            stored = c.execute(
                "INSERT INTO reports (id, owner_sub, deck_id, deck_name, fingerprint, taken_at, stats_json, "
                "goldfish_json, validation_json, created_by_client) SELECT ?,?,?,?,?,?,?,?,?,? "
                "WHERE EXISTS (SELECT 1 FROM users WHERE sub = ?)",
                (
                    rid,
                    sub,
                    deck.id,
                    deck.name,
                    deck.fingerprint(),
                    now,
                    json.dumps(stats),
                    json.dumps(goldfish) if goldfish is not None else None,
                    json.dumps(validation) if validation is not None else None,
                    current_client.get(),
                    sub,
                ),
            ).rowcount
            if not stored:
                raise DeckError(
                    "not_found", "Your gateway data was deleted while the report ran; nothing was kept."
                )
            c.execute(
                "DELETE FROM reports WHERE owner_sub = ? AND id NOT IN ("
                "SELECT id FROM reports WHERE owner_sub = ? ORDER BY taken_at DESC, rowid DESC LIMIT ?)",
                (sub, sub, MAX_REPORTS_PER_USER),
            )
        self.db.audit("report_created", sub=sub, detail={"report_id": rid, "deck_id": deck.id})
        return self.get(sub, rid)

    async def ab(
        self,
        sub: str,
        text_a: str,
        text_b: str,
        *,
        games: int = DEFAULT_GAMES,
        options: dict[str, Any] | None = None,
        commanders: tuple[bool, bool] = (True, True),
    ) -> dict[str, Any] | None:
        """A paired goldfish A/B of two decklists (game for game under the same seeds, with the
        deltas' confidence intervals and significance), as the research service reports it.
        Not stored: it is a comparison, not a report of one deck. None without the service.
        ``commanders`` says whether each list has a known commander; without one the simulator
        would take the first line as the commander, so the A/B is refused instead."""
        if self.mf is None:
            return None
        if not isinstance(games, int) or isinstance(games, bool) or games < 10 or games > MAX_GAMES:
            raise DeckError("invalid", f"games must be an integer from 10 to {MAX_GAMES}")
        opts = sim_options(options, ab=True)
        if not all(commanders):
            return {"tool": "goldfish_ab", "ok": False, "text": NO_COMMANDER_TEXT}
        args = {"deck_a": text_a, "deck_b": text_b, "n": games, **opts}
        return await self._mf(sub, "goldfish_ab", args)

    async def _mf(self, sub: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One Mystic Forge call, recorded as it came back: structured content when the tool
        gives it, else its text. Failures are recorded, never raised, so a report still holds
        the deck statistics when the research service is down. The call counts against the
        member's own Mystic Forge cap like an interactive one; past it the report is refused
        (rate_limited) and nothing is stored."""
        assert self.mf is not None
        # Mystic Forge's tools take one ``params`` object (pydantic models, published as
        # {"params": {...}} in its tool list); flat arguments are refused before the tool runs.
        params = {k: v for k, v in arguments.items() if v is not None}
        result = await self.mf.call(tool, {"params": params}, owner=sub, internal=True)
        if is_busy(result):
            raise DeckError(
                "rate_limited",
                "Research calls for your account are still running; wait for them to finish and run "
                "the report again.",
            )
        out: dict[str, Any] = {"tool": tool, "ok": not result.is_error}
        if result.structured_content:
            out["data"] = result.structured_content
        texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
        if texts:
            text = "\n".join(texts)
            out["text"] = text[:60_000]
            if "data" not in out:
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        out["data"] = parsed
                except ValueError:
                    pass
            # The simulators answer a refusal ("Commander '...' was not recognized", "No cards
            # found") as plain text, not as a tool error; a run without its results block failed.
            marker = SIM_RESULT_MARKERS.get(tool)
            if marker and marker not in text:
                out["ok"] = False
        elif tool in SIM_RESULT_MARKERS:
            out["ok"] = False
        return out

    # -- reading --------------------------------------------------------------
    def _latest(self, sub: str, deck_id: str) -> dict[str, Any] | None:
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT id, fingerprint, taken_at, goldfish_json, validation_json FROM reports "
                "WHERE owner_sub = ? AND deck_id = ? "
                "ORDER BY taken_at DESC, rowid DESC LIMIT 1",
                (sub, deck_id),
            ).fetchone()
        return dict(row) if row else None

    def list(
        self,
        sub: str,
        deck_id: str | None = None,
        limit: int = 20,
        *,
        search: str | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Newest first, with the trend numbers but not the full report. ``search`` matches the
        deck name, case-insensitively."""
        limit = max(1, min(int(limit), 2100))  # the History page reads up to its last page plus one
        sql = "SELECT * FROM reports WHERE owner_sub = ?"
        args: list[Any] = [sub]
        if deck_id:
            sql += " AND deck_id = ?"
            args.append(str(deck_id))
        if search:
            sql += " AND deck_name LIKE ? ESCAPE '\\'"
            args.append(_like(search))
        sql += " ORDER BY taken_at DESC, rowid DESC LIMIT ? OFFSET ?"
        args += [limit, max(0, int(offset))]
        with self.db._lock:
            rows = self.db._conn.execute(sql, args).fetchall()
        return [self._summary(dict(r)) for r in rows]

    def get(self, sub: str, report_id: str) -> dict[str, Any]:
        row = self.db._one("SELECT * FROM reports WHERE id = ? AND owner_sub = ?", (str(report_id), sub))
        if row is None:
            raise DeckError("not_found", "No such report for your account.")
        d = dict(row)
        out = self._summary(d)
        out["stats"] = json.loads(d["stats_json"])
        out["goldfish"] = json.loads(d["goldfish_json"]) if d.get("goldfish_json") else None
        out["validation"] = json.loads(d["validation_json"]) if d.get("validation_json") else None
        return out

    def decks_seen(self, sub: str) -> dict[str, str]:
        """The decks the member has reports for: id -> last known name."""
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT deck_id, deck_name FROM reports WHERE owner_sub = ? ORDER BY taken_at", (sub,)
            ).fetchall()
        return {str(r["deck_id"]): str(r["deck_name"] or "") for r in rows}

    def series(self, sub: str, deck_id: str, limit: int = 60) -> list[dict[str, Any]]:
        """Oldest first: taken_at plus the trend numbers, for charts."""
        rows = self.list(sub, deck_id, limit=limit)
        return [
            {"report_id": r["report_id"], "taken_at": r["taken_at"], **r["metrics"]} for r in reversed(rows)
        ]

    def delete(self, sub: str, report_id: str, *, client_id: str | None = None) -> None:
        """Delete one of the member's reports. With ``client_id`` (an app's bearer token), only a
        report that app ran itself; the member's browser passes none and may delete any."""
        sql = "DELETE FROM reports WHERE id = ? AND owner_sub = ?"
        args: tuple[str, ...] = (str(report_id), sub)
        if client_id is not None:
            sql += " AND created_by_client = ?"
            args += (client_id,)
        with self.db.tx() as c:
            cur = c.execute(sql, args)
        if cur.rowcount == 0:
            raise DeckError("not_found", "No such report for your account.")

    @staticmethod
    def _summary(d: dict[str, Any]) -> dict[str, Any]:
        stats = json.loads(d["stats_json"]) if d.get("stats_json") else {}
        metrics = {k: stats.get(k) for k in TREND_KEYS}
        goldfish = json.loads(d["goldfish_json"]) if d.get("goldfish_json") else None
        validation = json.loads(d["validation_json"]) if d.get("validation_json") else None
        return {
            "report_id": d["id"],
            "deck_id": d["deck_id"],
            "deck_name": d["deck_name"],
            "deck_url": f"https://archidekt.com/decks/{d['deck_id']}",
            "taken_at": d["taken_at"],
            "fingerprint": d["fingerprint"],
            "metrics": metrics,
            "has_goldfish": bool(goldfish and goldfish.get("ok")),
            "has_validation": bool(validation and validation.get("ok")),
            "bracket_estimate": (stats.get("bracket_estimate") or {}).get("bracket"),
            "created_by_client": d.get("created_by_client"),
        }


def _succeeded(row: dict[str, Any]) -> bool:
    """Whether a stored report's research blocks all came back ok. A report whose simulation or
    validation failed (the service down, a refusal) is kept as the record of that failure but
    never reused in place of a fresh run."""
    for key in ("goldfish_json", "validation_json"):
        block = row.get(key)
        if isinstance(block, str):
            block = json.loads(block)
        if block is not None and not block.get("ok"):
            return False
    return True


# The simulator's knobs a report or an A/B passes through to the research service, which
# validates their values (the proxy already bounds n and until_turn). Unknown keys are refused
# here so a typo never silently runs the default simulation.
RUN_OPTIONS = ("annotations", "combos", "seed", "until_turn", "opponents", "mulligan")
AB_OPTIONS = ("annotations", "annotations_a", "annotations_b", "combos", "seed", "until_turn")
AB_FLAGS = ("allow_different_commanders",)


def _front_face(name: str | None) -> str | None:
    return name.split(" // ", 1)[0] if isinstance(name, str) else name


# One entry of the honesty report's card lines: "Name (drawn 11%)", comma-separated. Names hold
# commas themselves ("Liesa, Forgotten Archangel"), so entries split on the "(drawn …)" tail.
_ENTRY = re.compile(r"(.+?)( \(drawn \d+%\))(?:, |$)")


def _commander_aside(goldfish: dict[str, Any], commander: str | None) -> None:
    """Take the commander out of the simulation's "unrecognized" list, in the metrics and in the
    text. The simulator casts it from the command zone (so it is never drawn) and plays it as a
    creature; its honesty report still files it as an unrecognized card "drawn 0%", which reads
    as a lookup failure. The report says instead that the commander's abilities beyond combat
    are not modelled. Nothing else in the result changes; a result without the list is left
    as it is."""
    if not commander or not goldfish.get("ok"):
        return
    data = goldfish.get("data")
    metrics = data.get("metrics") if isinstance(data, dict) else None
    honesty = metrics.get("honesty") if isinstance(metrics, dict) else None
    if not isinstance(honesty, dict):
        return
    unrecognized = honesty.get("unrecognized")
    if not isinstance(unrecognized, list):
        return
    kept = [c for c in unrecognized if not (isinstance(c, dict) and c.get("name") == commander)]
    if len(kept) == len(unrecognized):
        return
    honesty["unrecognized"] = kept
    honesty["commander_note"] = (
        f"{commander} is cast from the command zone (never drawn); its abilities beyond combat are "
        "not modelled"
    )
    text = goldfish.get("text")
    if not isinstance(text, str):
        return
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not line.startswith("Unrecognized"):
            continue
        # "Unrecognized — 32 cards (candidates for annotation; see goldfish_annotate):" then one
        # line "- Name (drawn 11%), Name (drawn 0%), ..."
        if i + 1 < len(lines) and lines[i + 1].startswith("- "):
            entries = _ENTRY.findall(lines[i + 1][2:])
            rest = [name + drawn for name, drawn in entries if name != commander]
            # Only rewrite a line the parse reproduces in full: an entry without a "(drawn N%)"
            # tail would otherwise be dropped silently.
            if ", ".join(n + d for n, d in entries) != lines[i + 1][2:] or len(rest) == len(entries):
                break
            lines[i + 1] = "- " + ", ".join(rest) if rest else "- (none)"
            lines[i] = re.sub(
                r"\b(\d+) cards?\b",
                lambda m: f"{int(m.group(1)) - 1} card{'' if int(m.group(1)) - 1 == 1 else 's'}",
                line,
                count=1,
            )
            lines.insert(i + 2, f"Commander — {honesty['commander_note']}")
        break
    goldfish["text"] = "\n".join(lines)


# What a successful simulation's text always contains (Mystic Forge's renderers).
SIM_RESULT_MARKERS = {"goldfish_run": "## Metrics", "goldfish_ab": "## Deltas"}
NO_COMMANDER_TEXT = (
    "Not simulated: the goldfish simulator models Commander decks and needs one card in the "
    "deck's Commander (premier) category. Put the commander in that category and run again."
)


def sim_options(options: dict[str, Any] | None, *, ab: bool = False) -> dict[str, Any]:
    """The given simulator options with unset ones dropped; refuses keys the simulation does
    not take (``invalid``)."""
    if not options:
        return {}
    if not isinstance(options, dict):
        raise DeckError("invalid", "simulation options must be an object")
    allowed = (*AB_OPTIONS, *AB_FLAGS) if ab else RUN_OPTIONS
    out = {k: v for k, v in options.items() if v is not None and v != [] and v != {}}
    unknown = sorted(k for k in out if k not in allowed)
    if unknown:
        raise DeckError("invalid", f"unknown simulation option(s): {', '.join(unknown)}")
    if len(json.dumps(out)) > 60_000:
        raise DeckError("too_large", "simulation options larger than 60 kB")
    for key, low, high in (("opponents", 1, 5), ("until_turn", 1, 30), ("seed", -(2**63), 2**63 - 1)):
        if key in out and (not isinstance(out[key], int) or isinstance(out[key], bool)):
            raise DeckError("invalid", f"simulation option {key} must be an integer")
        if key in out and not low <= out[key] <= high:
            raise DeckError("invalid", f"simulation option {key} must be from {low} to {high}")
    for key in ("annotations", "annotations_a", "annotations_b", "combos"):
        if key in out and not isinstance(out[key], list):
            raise DeckError("invalid", f"simulation option {key} must be a list")
    if "mulligan" in out and not isinstance(out["mulligan"], dict):
        raise DeckError("invalid", "simulation option mulligan must be an object")
    return out


def deck_summary_for(deck: Deck) -> dict[str, Any]:
    """The short deck block shared by tools, pages and the API."""
    return {
        "id": deck.id,
        "name": deck.name,
        "owner": deck.owner,
        "updated_at": deck.updated_at,
        "url": f"https://archidekt.com/decks/{deck.id}",
    }


__all__ = ["ReportService", "TREND_KEYS", "deck_summary_for", "sim_options"]
