"""Watchlists and price history.

A member watches cards (by name, or one printing by set code and collector number, in a finish)
and whole decks. Once a day, at ``MTG_PRICE_HOUR_UTC``, the gateway asks Scryfall for today's
prices of the watched printings only, through the shared Scryfall client (its pacer, its 429
back-off) with ``POST /cards/collection`` in batches of 75, spaced at the single-lookup pace.
It stores one price point per printing a day (USD, USD foil, USD etched, EUR, EUR foil, where
Scryfall has them; Scryfall updates its prices about once a day too) and each watched deck's value
that day. When nobody watches anything the check makes no request at all.

A watched deck is read again once a day (one paced Archidekt read per deck, as the member sees
it), so its value follows the cards it holds; when the read fails, the last card list is used.

Optional alerts per card: a move of more than a set percentage from one day to the next, or a
crossing of a price the member sets (above or below). They go out through ntfy (notify.py) for
members who opted in. Limits per member (``MTG_WATCH_MAX_CARDS``, ``MTG_WATCH_MAX_DECKS``) and how
long points are kept (``MTG_PRICE_HISTORY_DAYS``, purged with the rest in db.purge_expired) are
the operator's. Watching changes only the member's own gateway data, never Archidekt.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import re
import secrets
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .decklist import clean_text
from .decks import DeckError, _clean_deck_id

if TYPE_CHECKING:
    from .config import Settings
    from .db import Database
    from .decks import DeckService
    from .notify import Notifier
    from .scan.scryfall import ScryfallClient

logger = logging.getLogger(__name__)

FINISHES = ("nonfoil", "foil", "etched")
PRICE_KEYS = ("usd", "usd_foil", "usd_etched", "eur", "eur_foil")
# The price of each finish, in dollars and in euros (Scryfall has no etched euro price).
FINISH_KEYS = {
    "nonfoil": ("usd", "eur"),
    "foil": ("usd_foil", "eur_foil"),
    "etched": ("usd_etched", None),
}
SET_RE = re.compile(r"^[a-z0-9]{2,6}$")
NUMBER_RE = re.compile(r"^[A-Za-z0-9★-]{1,10}$")
MAX_NAME = 150
LOOP_EVERY = 900.0  # seconds between checks whether a daily job is due
FIRST_DELAY = 60.0


def now() -> float:
    """The current time; tests replace it to move through days."""
    return time.time()


def day_of(ts: float) -> str:
    return _dt.datetime.fromtimestamp(ts, _dt.UTC).date().isoformat()


def days_before(day: str, n: int) -> str:
    return (_dt.date.fromisoformat(day) - _dt.timedelta(days=n)).isoformat()


def finish_of(modifier: str | None) -> str:
    """Archidekt's modifier (Normal, Foil, Etched) as a finish."""
    m = (modifier or "").strip().lower()
    return m if m in ("foil", "etched") else "nonfoil"


def _price(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out >= 0 else None


def point_of(card: dict[str, Any]) -> dict[str, float | None]:
    prices = card.get("prices") if isinstance(card.get("prices"), dict) else {}
    return {k: _price(prices.get(k)) for k in PRICE_KEYS}


def price_for(point: dict[str, Any] | None, finish: str, currency: str = "usd") -> float | None:
    """The price of ``finish`` in ``currency`` from a stored point (None when Scryfall had none)."""
    if not point:
        return None
    usd_key, eur_key = FINISH_KEYS.get(finish, FINISH_KEYS["nonfoil"])
    key = usd_key if currency == "usd" else eur_key
    return point.get(key) if key else None


def headline(point: dict[str, Any] | None, finish: str) -> tuple[float | None, str]:
    """The price alerts and summaries follow: dollars, or euros when there is no dollar price."""
    usd = price_for(point, finish, "usd")
    if usd is not None:
        return usd, "usd"
    return price_for(point, finish, "eur"), "eur"


def money(value: float | None, currency: str = "usd") -> str:
    if value is None:
        return "no price"
    return f"${value:,.2f}" if currency == "usd" else f"€{value:,.2f}"


def pct_change(before: float | None, after: float | None) -> float | None:
    if before is None or after is None or before <= 0:
        return None
    return round((after - before) / before * 100, 1)


def _norm(name: str) -> str:
    return re.sub(r"\s+", " ", name.strip().lower())


def _ident(c: dict[str, Any]) -> dict[str, str]:
    """The Scryfall collection identifier for a watched card or deck row."""
    if c.get("uid"):
        return {"id": str(c["uid"])}
    if c.get("set") and c.get("cn"):
        return {"set": str(c["set"]), "collector_number": str(c["cn"])}
    if c.get("set"):
        return {"name": str(c["name"]), "set": str(c["set"])}
    return {"name": str(c["name"])}


def _ident_key(ident: dict[str, str]) -> tuple[str, ...]:
    if "id" in ident:
        return ("id", ident["id"].lower())
    if "collector_number" in ident:
        return ("print", ident["set"].lower(), ident["collector_number"].lower())
    return ("name", _norm(ident["name"]), ident.get("set", "").lower())


def _card_keys(card: dict[str, Any]) -> list[tuple[str, ...]]:
    """Every identifier key a Scryfall card answers (by id, printing, and its names with and
    without its set), so a found card is matched back to the row that asked for it."""
    keys: list[tuple[str, ...]] = [("id", str(card.get("id", "")).lower())]
    s, n = str(card.get("set", "")).lower(), str(card.get("collector_number", "")).lower()
    keys.append(("print", s, n))
    names = {str(card.get("name", ""))}
    names.add(str(card.get("name", "")).split(" // ")[0])
    for name in names:
        keys.append(("name", _norm(name), ""))
        keys.append(("name", _norm(name), s))
    return keys


class PriceService:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        decks: DeckService,
        scryfall: Callable[[], ScryfallClient | None],
        notifier: Notifier | None = None,
    ):
        self.settings = settings
        self.db = db
        self.decks = decks
        self._scryfall = scryfall
        self.notifier = notifier
        self._run_lock = asyncio.Lock()

    # -- reading ----------------------------------------------------------------------------------
    def _rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.db._lock:
            return [dict(r) for r in self.db._conn.execute(sql, args).fetchall()]

    def points(self, scryfall_id: str | None, days: int = 365) -> list[dict[str, Any]]:
        """A printing's stored points, oldest first, the last ``days`` days."""
        if not scryfall_id:
            return []
        since = days_before(day_of(now()), max(1, days) - 1)
        return self._rows(
            "SELECT day, usd, usd_foil, usd_etched, eur, eur_foil FROM price_points "
            "WHERE scryfall_id = ? AND day >= ? ORDER BY day",
            (scryfall_id, since),
        )

    def _changes(self, series: list[tuple[str, float | None]]) -> dict[str, Any]:
        """Latest value and its change over 1, 7 and 30 days, from (day, value) oldest first."""
        known = [(d, v) for d, v in series if v is not None]
        if not known:
            return {"price": None, "day": None}
        last_day, last = known[-1]
        out: dict[str, Any] = {"price": last, "day": last_day}
        for n in (1, 7, 30):
            cutoff = days_before(last_day, n)
            before = next((v for d, v in reversed(known) if d <= cutoff), None)
            out[f"change_{n}d_pct"] = pct_change(before, last)
        return out

    def card_view(self, w: dict[str, Any], days: int = 90) -> dict[str, Any]:
        pts = self.points(w.get("scryfall_id"), days)
        cur = headline(pts[-1], w["finish"])[1] if pts else "usd"
        series = [(p["day"], price_for(p, w["finish"], cur)) for p in pts]
        return {
            "watch_id": w["id"],
            "name": w["name"],
            "set": w["set_code"] or None,
            "collector_number": w["collector_number"] or None,
            "finish": w["finish"],
            "currency": cur,
            **self._changes(series),
            "alerts": {
                k: w[f"alert_{k}"] for k in ("pct", "above", "below") if w.get(f"alert_{k}") is not None
            },
            "history": [{"day": d, "price": v} for d, v in series],
            "priced": bool(w.get("scryfall_id")),
        }

    def watched_cards(self, sub: str) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM watch_cards WHERE sub = ? ORDER BY name, set_code, finish", (sub,))

    def watched_decks(self, sub: str) -> list[dict[str, Any]]:
        return self._rows("SELECT * FROM watch_decks WHERE sub = ? ORDER BY deck_name, deck_id", (sub,))

    def deck_series(self, sub: str, deck_id: str, days: int = 365) -> list[dict[str, Any]]:
        since = days_before(day_of(now()), max(1, days) - 1)
        return self._rows(
            "SELECT day, usd, eur, cards, priced FROM deck_values WHERE sub = ? AND deck_id = ? AND day >= ? "
            "ORDER BY day",
            (sub, deck_id, since),
        )

    def deck_view(self, sub: str, d: dict[str, Any], days: int = 90) -> dict[str, Any]:
        rows = self.deck_series(sub, d["deck_id"], days)
        cards = json.loads(d.get("cards_json") or "[]")
        return {
            "deck_id": d["deck_id"],
            "name": d["deck_name"],
            "cards": sum(int(c.get("qty") or 0) for c in cards),
            "currency": "usd",
            **self._changes([(r["day"], r["usd"]) for r in rows]),
            "history": [{"day": r["day"], "price": r["usd"], "eur": r["eur"]} for r in rows],
            "refreshed_at": d.get("refreshed_at"),
        }

    def overview(self, sub: str, days: int = 90) -> dict[str, Any]:
        s = self.settings
        return {
            "cards": [self.card_view(w, days) for w in self.watched_cards(sub)],
            "decks": [self.deck_view(sub, d, days) for d in self.watched_decks(sub)],
            "limits": {"cards": s.watch_max_cards, "decks": s.watch_max_decks},
            "checked_daily_at_utc": f"{s.price_hour_utc:02d}:00",
        }

    def find(self, sub: str, ref: str) -> list[dict[str, Any]]:
        """The member's watches matching a watch id, or a card name (every printing of it)."""
        ref = (ref or "").strip()
        rows = self._rows("SELECT * FROM watch_cards WHERE sub = ? AND id = ?", (sub, ref))
        if rows:
            return rows
        return [w for w in self.watched_cards(sub) if _norm(w["name"]) == _norm(ref)]

    # -- watching ---------------------------------------------------------------------------------
    def _scry(self) -> ScryfallClient:
        client = self._scryfall()
        if client is None:
            raise DeckError("unavailable", "Card lookup is switched off on this gateway.")
        return client

    async def watch_card(
        self,
        sub: str,
        name: str,
        *,
        set_code: str | None = None,
        collector_number: str | None = None,
        finish: str = "nonfoil",
        alert_pct: float | None = None,
        alert_above: float | None = None,
        alert_below: float | None = None,
    ) -> dict[str, Any]:
        """Watch a card (or change the alerts of one already watched). The printing is looked
        up at once, so today's price shows right away; when Scryfall is busy the watch is kept and
        priced by the next daily check."""
        name = clean_text(name or "").strip()[:MAX_NAME]
        set_code = (set_code or "").strip().lower()
        number = (collector_number or "").strip()
        finish = (finish or "nonfoil").strip().lower()
        if finish == "normal":
            finish = "nonfoil"
        if not name and not (set_code and number):
            raise DeckError("invalid", "Give the card's name (and optionally its set code and number).")
        if set_code and not SET_RE.fullmatch(set_code):
            raise DeckError("invalid", "A set code is 2 to 6 letters or digits, such as cmm.")
        if number and (not set_code or not NUMBER_RE.fullmatch(number)):
            raise DeckError("invalid", "A collector number goes with a set code, such as cmm 410.")
        if finish not in FINISHES:
            raise DeckError("invalid", "finish must be nonfoil, foil or etched.")
        alerts = _alerts(alert_pct, alert_above, alert_below)
        if self.settings.watch_max_cards <= 0:
            raise DeckError("invalid", "Watching cards is switched off on this gateway.")
        row = {"name": name, "set": set_code, "cn": number}
        card: dict[str, Any] | None = None
        note = ""
        try:
            found, _missing = await self._scry().collection([_ident(row)])
            card = found[0] if found else None
            if card is None:
                raise DeckError("not_found", f"Scryfall knows no card {self._label(name, set_code, number)}.")
        except DeckError:
            raise
        except Exception as exc:  # ScryfallError: busy or down; the daily check prices it later
            if not name:
                raise DeckError(
                    "unavailable", f"Scryfall could not be asked about this printing: {exc}"
                ) from exc
            note = "Scryfall could not be asked just now; today's price comes with the next daily check."
        if card is not None:
            name = str(card.get("name") or name)
            if finish not in (card.get("finishes") or ["nonfoil"]):
                raise DeckError(
                    "invalid",
                    f"{name} ({str(card.get('set', '')).upper()}) is not printed in {finish}; "
                    f"it comes in {', '.join(card.get('finishes') or ['nonfoil'])}.",
                )
            if set_code:  # a name with only a set: keep the printing Scryfall chose
                number = number or str(card.get("collector_number") or "")
        existing = self._rows(
            "SELECT * FROM watch_cards WHERE sub = ? AND name = ? AND set_code = ? AND collector_number = ? "
            "AND finish = ?",
            (sub, name, set_code, number, finish),
        )
        t = int(now())
        with self.db.tx() as c:
            if existing:
                wid = existing[0]["id"]
                c.execute(
                    "UPDATE watch_cards SET alert_pct = ?, alert_above = ?, alert_below = ? WHERE id = ?",
                    (*alerts, wid),
                )
                created = False
            else:
                count = c.execute("SELECT COUNT(*) FROM watch_cards WHERE sub = ?", (sub,)).fetchone()[0]
                if count >= self.settings.watch_max_cards:
                    raise DeckError(
                        "rate_limited",
                        f"You watch {count} cards, the most this gateway keeps per member. "
                        "Remove some first.",
                    )
                wid = "w_" + secrets.token_urlsafe(9)
                stored = c.execute(
                    "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, "
                    "scryfall_id, alert_pct, alert_above, alert_below, created_at) "
                    "SELECT ?,?,?,?,?,?,?,?,?,?,? "
                    "WHERE EXISTS (SELECT 1 FROM users WHERE sub = ?)",
                    (
                        wid,
                        sub,
                        name,
                        set_code,
                        number,
                        finish,
                        card.get("id") if card else None,
                        *alerts,
                        t,
                        sub,
                    ),
                ).rowcount
                if not stored:
                    raise DeckError("not_found", "No gateway account for this member.")
                created = True
            if card is not None:
                self._store_point(c, str(card["id"]), point_of(card), day_of(t))
        out = self.card_view(self._rows("SELECT * FROM watch_cards WHERE id = ?", (wid,))[0])
        out["created"] = created
        if note:
            out["note"] = note
        return out

    @staticmethod
    def _label(name: str, set_code: str, number: str) -> str:
        where = " ".join(p for p in (set_code.upper(), number) if p)
        return f"named {name!r}" + (f" in {where}" if where else "") if name else f"numbered {where}"

    def set_alerts(
        self, sub: str, watch_id: str, pct: float | None, above: float | None, below: float | None
    ) -> dict[str, Any]:
        alerts = _alerts(pct, above, below)
        with self.db.tx() as c:
            n = c.execute(
                "UPDATE watch_cards SET alert_pct = ?, alert_above = ?, alert_below = ? "
                "WHERE id = ? AND sub = ?",
                (*alerts, watch_id, sub),
            ).rowcount
        if not n:
            raise DeckError("not_found", "That card is not on your watchlist.")
        return self.card_view(self._rows("SELECT * FROM watch_cards WHERE id = ?", (watch_id,))[0])

    def unwatch_card(self, sub: str, ref: str) -> list[str]:
        rows = self.find(sub, ref)
        if not rows:
            raise DeckError("not_found", "That card is not on your watchlist.")
        with self.db.tx() as c:
            c.executemany("DELETE FROM watch_cards WHERE id = ? AND sub = ?", [(w["id"], sub) for w in rows])
        return [w["name"] for w in rows]

    def _check_deck_limit(self, sub: str, deck_id: str, c: Any = None) -> None:
        sql, args = "SELECT deck_id FROM watch_decks WHERE sub = ?", (sub,)
        if c is None:
            have = [r["deck_id"] for r in self._rows(sql, args)]
        else:
            have = [r[0] for r in c.execute(sql, args).fetchall()]
        if str(deck_id) not in {str(d) for d in have} and len(have) >= self.settings.watch_max_decks:
            raise DeckError(
                "rate_limited",
                f"You watch {len(have)} decks, the most this gateway keeps per member. Remove one first.",
            )

    async def watch_deck(self, sub: str, deck_ref: str) -> dict[str, Any]:
        """Watch a deck's value: read it now (as the member sees it), price its cards and store
        today's value."""
        deck_id = _clean_deck_id(deck_ref)
        if self.settings.watch_max_decks <= 0:
            raise DeckError("invalid", "Watching decks is switched off on this gateway.")
        self._check_deck_limit(sub, deck_id)
        deck = await self.decks.get_any_deck(sub, deck_id)
        t = int(now())
        with self.db.tx() as c:
            # counted again in the same transaction as the insert, so parallel watches cannot pass it
            self._check_deck_limit(sub, deck.id, c)
            stored = c.execute(
                "INSERT INTO watch_decks (sub, deck_id, deck_name, cards_json, refreshed_at, created_at) "
                "SELECT ?,?,?,?,?,? WHERE EXISTS (SELECT 1 FROM users WHERE sub = ?) "
                "ON CONFLICT(sub, deck_id) DO UPDATE SET deck_name = excluded.deck_name, "
                "cards_json = excluded.cards_json, refreshed_at = excluded.refreshed_at",
                (sub, deck.id, deck.name, json.dumps(_deck_rows(deck)), t, t, sub),
            ).rowcount
        if not stored:
            raise DeckError("not_found", "No gateway account for this member.")
        note = ""
        try:
            await self._price_missing(day_of(t), only_deck=(sub, deck.id))
        except Exception as exc:  # Scryfall busy: the daily check prices it
            logger.info("pricing a newly watched deck waits for the daily check: %s", exc.__class__.__name__)
            note = "Scryfall could not be asked just now; today's value comes with the next daily check."
        self._store_deck_values(day_of(t), only_deck=(sub, deck.id))
        row = self._rows("SELECT * FROM watch_decks WHERE sub = ? AND deck_id = ?", (sub, deck.id))[0]
        out = self.deck_view(sub, row)
        if note:
            out["note"] = note
        return out

    def unwatch_deck(self, sub: str, deck_ref: str) -> str:
        deck_id = _clean_deck_id(deck_ref)
        with self.db.tx() as c:
            row = c.execute(
                "SELECT deck_name FROM watch_decks WHERE sub = ? AND deck_id = ?", (sub, deck_id)
            ).fetchone()
            if row is None:
                raise DeckError("not_found", "That deck is not on your watchlist.")
            c.execute("DELETE FROM watch_decks WHERE sub = ? AND deck_id = ?", (sub, deck_id))
            c.execute("DELETE FROM deck_values WHERE sub = ? AND deck_id = ?", (sub, deck_id))
        return str(row[0])

    # -- the daily check --------------------------------------------------------------------------
    def anything_watched(self) -> bool:
        with self.db._lock:
            return bool(
                self.db._conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM watch_cards) OR EXISTS (SELECT 1 FROM watch_decks)"
                ).fetchone()[0]
            )

    async def run_daily(self, day: str | None = None) -> dict[str, Any]:
        """Today's prices for every watched printing, each watched deck's value, then alerts.
        Costs nothing (no request) when nobody watches anything."""
        day = day or day_of(now())
        async with self._run_lock:
            if not self.anything_watched():
                return {"day": day, "watched": False, "requests": 0}
            refreshed = await self._refresh_decks(day)
            before = self._scry().requests
            priced = await self._price_missing(day)
            self._store_deck_values(day)
            alerts = self._alerts_due(day)
        for sub, title, message in alerts:
            if self.notifier is not None:
                await self.notifier.send(sub, "prices", title, message, "/watchlist")
        return {
            "day": day,
            "watched": True,
            "decks_refreshed": refreshed,
            "priced": priced,
            "requests": self._scry().requests - before,
            "alerts": len(alerts),
        }

    async def _refresh_decks(self, day: str) -> int:
        """Read each watched deck again (paced like every Archidekt read), once a day: a deck
        already read on ``day`` (watched today, or a retry after Scryfall failed) is not read
        again. A deck that cannot be read keeps its last card list."""
        n = 0
        for d in self._rows("SELECT sub, deck_id, refreshed_at FROM watch_decks ORDER BY refreshed_at"):
            if day_of(d["refreshed_at"]) == day:
                continue
            try:
                deck = await self.decks.get_any_deck(d["sub"], d["deck_id"])
            except Exception as exc:
                logger.info("watched deck %s not read today: %s", d["deck_id"], exc.__class__.__name__)
                continue
            with self.db.tx() as c:
                c.execute(
                    "UPDATE watch_decks SET deck_name = ?, cards_json = ?, refreshed_at = ? "
                    "WHERE sub = ? AND deck_id = ?",
                    (deck.name, json.dumps(_deck_rows(deck)), int(now()), d["sub"], d["deck_id"]),
                )
            n += 1
        return n

    async def _price_missing(self, day: str, *, only_deck: tuple[str, str] | None = None) -> int:
        """Ask Scryfall (in batches of 75) for the watched printings with no point for ``day``,
        store their points, and record the printing of rows that had none (a name-only watch, a
        deck card without a Scryfall id). ``only_deck`` (member, deck id): that deck's cards only."""
        if only_deck:
            cards = []
            decks = self._rows("SELECT * FROM watch_decks WHERE sub = ? AND deck_id = ?", only_deck)
        else:
            cards = self._rows("SELECT * FROM watch_cards")
            decks = self._rows("SELECT * FROM watch_decks")
        with self.db._lock:
            have = {
                r[0]
                for r in self.db._conn.execute("SELECT scryfall_id FROM price_points WHERE day = ?", (day,))
            }
        wanted: dict[tuple[str, ...], dict[str, str]] = {}
        for w in cards:
            if w["scryfall_id"] and w["scryfall_id"] in have:
                continue
            ident = _ident(
                {
                    "uid": w["scryfall_id"],
                    "name": w["name"],
                    "set": w["set_code"],
                    "cn": w["collector_number"],
                }
            )
            wanted[_ident_key(ident)] = ident
        deck_rows = {d["sub"] + "\0" + d["deck_id"]: json.loads(d["cards_json"] or "[]") for d in decks}
        for rows in deck_rows.values():
            for r in rows:
                if r.get("uid") and r["uid"] in have:
                    continue
                ident = _ident(r)
                wanted[_ident_key(ident)] = ident
        if not wanted:
            return 0
        client = self._scry()
        found, _missing = await client.collection(list(wanted.values()), interval=client.lookup_interval)
        by_key: dict[tuple[str, ...], dict[str, Any]] = {}
        for card in found:
            for k in _card_keys(card):
                by_key.setdefault(k, card)
        with self.db.tx() as c:
            for card in found:
                self._store_point(c, str(card["id"]), point_of(card), day)
            for w in cards:
                if not w["scryfall_id"]:
                    ident = _ident({"name": w["name"], "set": w["set_code"], "cn": w["collector_number"]})
                    hit = by_key.get(_ident_key(ident))
                    if hit:
                        c.execute("UPDATE watch_cards SET scryfall_id = ? WHERE id = ?", (hit["id"], w["id"]))
            for key, rows in deck_rows.items():
                changed = False
                for r in rows:
                    if not r.get("uid"):
                        hit = by_key.get(_ident_key(_ident(r)))
                        if hit:
                            r["uid"], changed = str(hit["id"]), True
                if changed:
                    sub, deck_id = key.split("\0", 1)
                    c.execute(
                        "UPDATE watch_decks SET cards_json = ? WHERE sub = ? AND deck_id = ?",
                        (json.dumps(rows), sub, deck_id),
                    )
        return len(found)

    @staticmethod
    def _store_point(c: Any, scryfall_id: str, point: dict[str, Any], day: str) -> None:
        c.execute(
            "INSERT OR REPLACE INTO price_points "
            "(scryfall_id, day, usd, usd_foil, usd_etched, eur, eur_foil) VALUES (?,?,?,?,?,?,?)",
            (scryfall_id, day, *(point[k] for k in PRICE_KEYS)),
        )

    def _store_deck_values(self, day: str, *, only_deck: tuple[str, str] | None = None) -> None:
        decks = self._rows(
            "SELECT * FROM watch_decks" + (" WHERE sub = ? AND deck_id = ?" if only_deck else ""),
            only_deck or (),
        )
        if not decks:
            return
        with self.db._lock:
            points = {
                r["scryfall_id"]: dict(r)
                for r in self.db._conn.execute("SELECT * FROM price_points WHERE day = ?", (day,))
            }
        with self.db.tx() as c:
            for d in decks:
                rows = json.loads(d["cards_json"] or "[]")
                usd = eur = 0.0
                priced = total = 0
                for r in rows:
                    qty = int(r.get("qty") or 0)
                    total += qty
                    p = points.get(r.get("uid") or "")
                    u = _with_fallback(p, r.get("finish", "nonfoil"), "usd")
                    e = _with_fallback(p, r.get("finish", "nonfoil"), "eur")
                    if u is not None or e is not None:
                        priced += qty
                    usd += (u or 0.0) * qty
                    eur += (e or 0.0) * qty
                c.execute(
                    "INSERT OR REPLACE INTO deck_values (sub, deck_id, day, usd, eur, cards, priced) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (d["sub"], d["deck_id"], day, round(usd, 2), round(eur, 2), total, priced),
                )

    def _alerts_due(self, day: str) -> list[tuple[str, str, str]]:
        """(member, title, message) for each watched card whose alert fired between its last
        point before ``day`` and today's."""
        out = []
        rows = self._rows(
            "SELECT * FROM watch_cards WHERE scryfall_id IS NOT NULL AND (alert_pct IS NOT NULL OR "
            "alert_above IS NOT NULL OR alert_below IS NOT NULL)"
        )
        for w in rows:
            pts = self._rows(
                "SELECT * FROM price_points WHERE scryfall_id = ? AND day <= ? ORDER BY day DESC LIMIT 2",
                (w["scryfall_id"], day),
            )
            if len(pts) < 2 or pts[0]["day"] != day:
                continue
            cur, currency = headline(pts[0], w["finish"])
            prev = price_for(pts[1], w["finish"], currency)
            if cur is None or prev is None:
                continue
            label = w["name"] + (f" ({w['finish']})" if w["finish"] != "nonfoil" else "")
            moved = pct_change(prev, cur)
            reasons = []
            if w["alert_pct"] is not None and moved is not None and abs(moved) >= w["alert_pct"]:
                reasons.append(f"{'rose' if moved > 0 else 'fell'} {abs(moved):g}%")
            if w["alert_above"] is not None and prev < w["alert_above"] <= cur:
                reasons.append(f"rose above {money(w['alert_above'], currency)}")
            if w["alert_below"] is not None and prev > w["alert_below"] >= cur:
                reasons.append(f"fell below {money(w['alert_below'], currency)}")
            if reasons:
                out.append(
                    (
                        w["sub"],
                        "Price alert",
                        f"{label} {' and '.join(reasons)}: "
                        f"{money(prev, currency)} to {money(cur, currency)}.",
                    )
                )
        return out


def _with_fallback(point: dict[str, Any] | None, finish: str, currency: str) -> float | None:
    """A deck row's price: its own finish, else the plain one, else the foil one (as a deck's
    total on Archidekt counts every card it can price)."""
    for f in (finish, "nonfoil", "foil"):
        v = price_for(point, f, currency)
        if v is not None:
            return v
    return None


def _deck_rows(deck: Any) -> list[dict[str, Any]]:
    """What a watched deck's value needs from it: each card in the deck with its printing,
    finish and count (merged by printing and finish)."""
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    for c in deck.main_cards:
        row = {
            "uid": c.scryfall_uid or "",
            "name": c.name,
            "set": (c.set_code or "").lower(),
            "cn": c.collector_number or "",
            "finish": finish_of(c.modifier),
        }
        key = (row["uid"], row["name"], row["set"], row["cn"], row["finish"])
        if key in merged:
            merged[key]["qty"] += c.quantity
        else:
            merged[key] = {**row, "qty": c.quantity}
    return list(merged.values())


def _alerts(pct: Any, above: Any, below: Any) -> tuple[float | None, float | None, float | None]:
    def num(v: Any, what: str, hi: float) -> float | None:
        if v is None or v == "":
            return None
        try:
            out = float(v)
        except (TypeError, ValueError) as exc:
            raise DeckError("invalid", f"{what} must be a number.") from exc
        if not 0 < out <= hi:
            raise DeckError("invalid", f"{what} must be more than 0 and at most {hi:g}.")
        return round(out, 2)

    return (
        num(pct, "The alert percentage", 1000),
        num(above, "The price to alert above", 1_000_000),
        num(below, "The price to alert below", 1_000_000),
    )


class DailyJobs:
    """The daily price check and the members' scheduled summaries, run once a day at
    ``MTG_PRICE_HOUR_UTC`` (summaries right after the prices, so they include them). Which day
    each last ran is kept in the database, so a restart neither repeats nor skips one."""

    def __init__(self, prices: PriceService, summaries: Any, hour_utc: int):
        self.prices = prices
        self.summaries = summaries
        self.hour = hour_utc

    def _last_day(self, name: str) -> str | None:
        with self.prices.db._lock:
            row = self.prices.db._conn.execute("SELECT day FROM job_runs WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def _mark(self, name: str, day: str, detail: dict[str, Any]) -> None:
        with self.prices.db.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO job_runs (name, day, at, detail_json) VALUES (?,?,?,?)",
                (name, day, int(now()), json.dumps(detail)),
            )

    async def run_due(self) -> dict[str, Any]:
        """Run what is due now; returns what ran (empty when nothing was due)."""
        t = now()
        day = day_of(t)
        if _dt.datetime.fromtimestamp(t, _dt.UTC).hour < self.hour:
            return {}
        out: dict[str, Any] = {}
        if self._last_day("prices") != day:
            try:
                out["prices"] = await self.prices.run_daily(day)
            except Exception as exc:  # Scryfall down or busy: try again at the next round
                logger.warning("daily price check failed, trying again later: %s", exc.__class__.__name__)
                out["prices"] = {"error": exc.__class__.__name__}
            else:
                self._mark("prices", day, out["prices"])
        if self.summaries is not None and self._last_day("summaries") != day:
            out["summaries"] = await self.summaries.send_due(day)
            self._mark("summaries", day, {"sent": out["summaries"]})
        return out

    async def loop(self) -> None:
        await asyncio.sleep(FIRST_DELAY)
        while True:
            try:
                await self.run_due()
            except Exception:  # never let it stop the gateway
                logger.exception("daily jobs failed")
            await asyncio.sleep(LOOP_EVERY)
