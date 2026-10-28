"""A member's plain summary of the last day or week: watched card prices and deck values, deck
reports finished, proposals waiting and applied.

The gateway writes it from its own data with fixed sentences; it has no AI model and does not
call one (that would need a paid model API). The member's own assistant can turn it into a
written summary on a schedule of their choosing through the read-only ``get_summary`` tool
(docs/USING.md). Members who choose daily or weekly summaries in the Notifications card of their
Account page get them through ntfy (notify.py); without a notification server there is no schedule
to choose. With watchlists off (MTG_WATCHLISTS_ENABLED) the summary has no prices or deck values.
"""

from __future__ import annotations

import datetime as _dt
import logging
from typing import TYPE_CHECKING, Any, Literal

from . import prices as _prices
from .forge import FINAL_STATES
from .prices import day_of, days_before, money, pct_change

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .db import Database
    from .notify import Notifier
    from .prices import PriceService

logger = logging.getLogger(__name__)

PERIODS = {"day": 1, "week": 7}


def _start_ts(day: str) -> int:
    return int(_dt.datetime.combine(_dt.date.fromisoformat(day), _dt.time(), tzinfo=_dt.UTC).timestamp())


class Summaries:
    def __init__(self, db: Database, prices: PriceService | None, notifier: Notifier | None, public_url: str):
        self.db = db
        self.prices = prices
        self.notifier = notifier
        self.public_url = public_url.rstrip("/")

    def build(self, sub: str, period: str = "week") -> dict[str, Any]:
        """The summary of the ``period`` (day or week) up to now."""
        if period not in PERIODS:
            period = "week"
        today = day_of(_prices.now())
        since = days_before(today, PERIODS[period])
        since_ts = _start_ts(since)
        cards = []
        watched = self.prices is not None  # watchlists on (MTG_WATCHLISTS_ENABLED)
        views = self.prices.card_summaries(sub) if self.prices is not None else []
        if views:  # every card's points in one query, whatever the watchlist's size
            self.prices.card_histories(views, PERIODS[period] + 31)
        for view in views:
            w = view
            hist = [h for h in view["history"] if h["price"] is not None]
            if not hist:
                continue
            before = next((h["price"] for h in reversed(hist) if h["day"] <= since), hist[0]["price"])
            cards.append(
                {
                    "name": w["name"],
                    "finish": w["finish"],
                    "currency": view["currency"],
                    "from": before,
                    "to": hist[-1]["price"],
                    "change_pct": pct_change(before, hist[-1]["price"]),
                }
            )
        cards.sort(key=lambda c: -abs(c["change_pct"] or 0))
        decks = []
        for d in self.prices.watched_decks(sub) if self.prices is not None else []:
            rows = [r for r in self.prices.deck_series(sub, d["deck_id"], PERIODS[period] + 31) if r["usd"]]
            if not rows:
                continue
            before = next((r["usd"] for r in reversed(rows) if r["day"] <= since), rows[0]["usd"])
            decks.append(
                {
                    "deck_id": d["deck_id"],
                    "name": d["deck_name"],
                    "from": before,
                    "to": rows[-1]["usd"],
                    "change_pct": pct_change(before, rows[-1]["usd"]),
                }
            )
        # A report is finished only once its Forge games are too: no Forge run, or one that
        # reached a final state (the quick simulation is done when the report is stored).
        finals = tuple(FINAL_STATES)
        with self.db._lock:
            c = self.db._conn
            present = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            reports = (
                [
                    dict(r)
                    for r in c.execute(
                        "SELECT id AS report_id, deck_id, deck_name, taken_at FROM reports "
                        "WHERE owner_sub = ? AND taken_at >= ? AND (forge_state IS NULL OR forge_state IN "
                        f"({','.join('?' * len(finals))})) ORDER BY taken_at DESC LIMIT 20",
                        (sub, since_ts, *finals),
                    )
                ]
                if "reports" in present
                else []
            )
            waiting = c.execute(
                "SELECT COUNT(*) FROM proposals WHERE owner_sub = ? AND state = 'pending' "
                "AND expires_at >= ?",
                (sub, int(_prices.now())),
            ).fetchone()[0]
            applied = c.execute(
                "SELECT COUNT(*) FROM proposals WHERE owner_sub = ? AND state = 'applied' "
                "AND applied_at >= ?",
                (sub, since_ts),
            ).fetchone()[0]
        out: dict[str, Any] = {
            "period": period,
            "from_day": since,
            "to_day": today,
            "cards": cards,
            "decks": decks,
            "reports_finished": [
                {**r, "url": f"{self.public_url}/history/reports/{r['report_id']}"} for r in reports
            ],
            "proposals_waiting": waiting,
            "proposals_applied": applied,
            "watchlists": watched,
            "account_url": f"{self.public_url}/account/details#summary",
            "written_by": "the gateway, from its own data (no AI)",
        }
        out["text"] = text(out)
        return out

    async def send(self, sub: str, period: str) -> bool:
        """Build the member's summary of the ``period`` and send it through their notifications;
        True when the notification server took it. The schedule (send_due) and the Account page's
        Send it now both send this way, so the phone gets the same message either way."""
        if self.notifier is None or not self.notifier.enabled:
            return False
        summary = self.build(sub, period)
        return await self.notifier.send(
            sub,
            "summary",
            f"Your {'daily' if summary['period'] == 'day' else 'weekly'} summary",
            short_text(summary),
            "/account/details#summary",
        )

    async def send_due(self, day: str) -> int:
        """Send the summary to every member who chose one that is due on ``day`` (daily ones every
        day, weekly ones on Mondays) and has ntfy notifications on. Returns how many were sent."""
        if self.notifier is None or not self.notifier.enabled:
            return 0
        weekday = _dt.date.fromisoformat(day).weekday()
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT sub, summary_every FROM notify_prefs WHERE ntfy_on = 1 AND ntfy_topic IS NOT NULL "
                "AND summary_every != 'off' AND (summary_sent_day IS NULL OR summary_sent_day != ?)",
                (day,),
            ).fetchall()
        sent = 0
        for r in rows:
            if r["summary_every"] == "weekly" and weekday != 0:
                continue
            period = "day" if r["summary_every"] == "daily" else "week"
            try:
                ok = await self.send(r["sub"], period)
            except Exception:  # one member's summary must not stop the others
                logger.exception("summary failed for one member")
                continue
            with self.db.tx() as c:
                c.execute("UPDATE notify_prefs SET summary_sent_day = ? WHERE sub = ?", (day, r["sub"]))
            sent += ok
        return sent


def _day(day: str, today: str) -> str:
    """A day as the pages write dates: "Oct 18", with the year only when it is not this year's."""
    d = _dt.date.fromisoformat(day)
    shown = f"{d.strftime('%b')} {d.day}"
    return shown if d.year == _dt.date.fromisoformat(today).year else f"{shown}, {d.year}"


def _signed(pct: float | None) -> str:
    return "no change" if not pct else f"{pct:+g}%"


def text(s: dict[str, Any]) -> str:
    """The summary in plain sentences (what the Account page shows and get_summary returns)."""
    span = "today" if s["period"] == "day" else "this week"
    lines = [f"Summary for {_day(s['from_day'], s['to_day'])} to {_day(s['to_day'], s['to_day'])}."]
    if s.get("watchlists") is False:
        pass  # watchlists are off on this gateway: no prices to report
    elif s["cards"]:
        moves = [c for c in s["cards"] if c["change_pct"]]
        lines.append(f"Watched cards: {len(s['cards'])}, {len(moves)} changed price {span}.")
        for c in s["cards"][:5]:
            if c["change_pct"]:
                lines.append(
                    f"- {c['name']}{'' if c['finish'] == 'nonfoil' else ' (' + c['finish'] + ')'}: "
                    f"{money(c['from'], c['currency'])} to {money(c['to'], c['currency'])} "
                    f"({_signed(c['change_pct'])})"
                )
    else:
        lines.append("Watched cards: none priced yet.")
    for d in s["decks"]:
        lines.append(
            f"Deck {d['name']}: {money(d['from'])} to {money(d['to'])} ({_signed(d['change_pct'])})."
        )
    n = len(s["reports_finished"])
    lines.append(f"Deck reports finished: {n}.")
    lines.append(f"Proposals waiting for you: {s['proposals_waiting']}. Applied: {s['proposals_applied']}.")
    return "\n".join(lines)


def short_text(s: dict[str, Any]) -> str:
    """The notification's text: counts and totals only, no deck names or contents."""
    parts = []
    if s["cards"]:
        top = s["cards"][0]
        parts.append(
            f"{len(s['cards'])} watched cards"
            + (f", biggest move {top['name']} {_signed(top['change_pct'])}" if top["change_pct"] else "")
        )
    if s["decks"]:
        start = sum(d["from"] or 0 for d in s["decks"])
        end = sum(d["to"] or 0 for d in s["decks"])
        parts.append(
            f"{len(s['decks'])} watched decks worth {money(end)} ({_signed(pct_change(start, end))})"
        )
    parts.append(f"{len(s['reports_finished'])} reports finished")
    parts.append(f"{s['proposals_waiting']} proposals waiting, {s['proposals_applied']} applied")
    return "; ".join(parts) + "."


def add_summary_tool(server: MCPServer, state: AppState) -> None:
    """The read-only ``get_summary`` tool, there whether or not watchlists or notifications are on."""
    from mcp.server.auth.middleware.auth_context import get_access_token

    @server.tool(
        name="get_summary",
        title="Get my summary",
        description=(
            "The member's plain summary of the last day or week (period: day or week): watched card "
            "prices that moved and watched deck values (when the gateway has watchlists on), deck reports "
            "finished, proposals waiting and applied, plus the same as sentences in `text`. Written by the "
            "gateway from its own data, without AI; turn it into your own words for the member (for example "
            "in a scheduled task). Read-only."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def get_summary(period: Literal["day", "week"] = "week") -> dict[str, object]:
        token = get_access_token()
        if token is None or not token.subject:
            raise RuntimeError("no authenticated user on this request")
        return {"ok": True, **state.summaries.build(token.subject, period)}


__all__ = ["PERIODS", "Summaries", "add_summary_tool", "short_text", "text"]
