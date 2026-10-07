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
import secrets
import time
from typing import Any

from . import deck_stats
from .archidekt import Deck
from .db import Database
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
        self, sub: str, deck_ref: str, *, simulate: bool = True, games: int = DEFAULT_GAMES
    ) -> dict[str, Any]:
        """Read the deck, compute its statistics, optionally simulate and validate it, store
        the result and return it. Refuses a second report of an unchanged deck within
        ``min_interval`` seconds (returns the existing one instead)."""
        if not isinstance(games, int) or isinstance(games, bool) or games < 10 or games > MAX_GAMES:
            raise DeckError("invalid", f"games must be an integer from 10 to {MAX_GAMES}")
        key = (sub, _clean_deck_id(deck_ref))
        flight = self._runs.setdefault(key, _Flight())
        flight.users += 1
        seen = flight.finished
        try:
            async with flight.lock:
                if flight.finished > seen and flight.report_id:
                    # A run for this deck finished while this request waited: reuse its report
                    # instead of starting a second simulation.
                    out = self.get(sub, flight.report_id)
                    out["reused"] = True
                    return out
                out = await self._run(sub, deck_ref, simulate=simulate, games=games)
                flight.report_id = out["report_id"]
                flight.finished += 1
                return out
        finally:
            flight.users -= 1
            if flight.users <= 0:
                self._runs.pop(key, None)

    async def _run(self, sub: str, deck_ref: str, *, simulate: bool, games: int) -> dict[str, Any]:
        deck = await self.decks.get_any_deck(sub, deck_ref)
        latest = self._latest(sub, deck.id)
        now = int(time.time())
        if (
            latest is not None
            and latest["fingerprint"] == deck.fingerprint()
            and now - int(latest["taken_at"]) < self.min_interval
        ):
            out = self.get(sub, latest["id"])
            out["reused"] = True
            return out
        stats = deck_stats.compute(deck)
        goldfish: dict[str, Any] | None = None
        validation: dict[str, Any] | None = None
        if self.mf is not None:
            text = deck_to_text(deck)
            commander = (stats.get("commanders") or [None])[0]
            validation = await self._mf(sub, "validate_decklist", {"decklist": text, "commander": commander})
            if simulate:
                goldfish = await self._mf(sub, "goldfish_run", {"deck": text, "n": games})
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

    async def _mf(self, sub: str, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """One Mystic Forge call, recorded as it came back: structured content when the tool
        gives it, else its text. Failures are recorded, never raised, so a report still holds
        the deck statistics when the research service is down. The call counts against the
        member's own Mystic Forge cap like an interactive one; past it the report is refused
        (rate_limited) and nothing is stored."""
        assert self.mf is not None
        result = await self.mf.call(
            tool, {k: v for k, v in arguments.items() if v is not None}, owner=sub, internal=True
        )
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
        return out

    # -- reading --------------------------------------------------------------
    def _latest(self, sub: str, deck_id: str) -> dict[str, Any] | None:
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT id, fingerprint, taken_at FROM reports WHERE owner_sub = ? AND deck_id = ? "
                "ORDER BY taken_at DESC, rowid DESC LIMIT 1",
                (sub, deck_id),
            ).fetchone()
        return dict(row) if row else None

    def list(self, sub: str, deck_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Newest first, with the trend numbers but not the full report."""
        limit = max(1, min(int(limit), 200))
        sql = "SELECT * FROM reports WHERE owner_sub = ?"
        args: list[Any] = [sub]
        if deck_id:
            sql += " AND deck_id = ?"
            args.append(str(deck_id))
        sql += " ORDER BY taken_at DESC, rowid DESC LIMIT ?"
        args.append(limit)
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
        return {
            "report_id": d["id"],
            "deck_id": d["deck_id"],
            "deck_name": d["deck_name"],
            "deck_url": f"https://archidekt.com/decks/{d['deck_id']}",
            "taken_at": d["taken_at"],
            "fingerprint": d["fingerprint"],
            "metrics": metrics,
            "has_goldfish": bool(goldfish and goldfish.get("ok")),
            "has_validation": bool(d.get("validation_json")),
            "bracket_estimate": (stats.get("bracket_estimate") or {}).get("bracket"),
        }


def deck_summary_for(deck: Deck) -> dict[str, Any]:
    """The short deck block shared by tools, pages and the API."""
    return {
        "id": deck.id,
        "name": deck.name,
        "owner": deck.owner,
        "updated_at": deck.updated_at,
        "url": f"https://archidekt.com/decks/{deck.id}",
    }


__all__ = ["ReportService", "TREND_KEYS", "deck_summary_for"]
