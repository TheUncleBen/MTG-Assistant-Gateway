"""Watchlists and price history.

A member watches cards (by name, or one printing by set code and collector number, in a finish)
and whole decks. Once a day, at the price_hour_utc setting, the gateway asks Scryfall for today's
prices of the watched printings only, through the shared Scryfall client (its pacer, its 429
back-off) with ``POST /cards/collection`` in batches of 75, spaced at the single-lookup pace.
It stores one price point per printing a day (USD, USD foil, USD etched, EUR, EUR foil, where
Scryfall has them; Scryfall updates its prices about once a day too) and each watched deck's value
that day. When nobody watches anything and no Binder or deck is in use the check makes no request
at all.

A watch by name alone follows the cheapest printing of the card (0.7.46): the check reads every
printing of those cards (Scryfall's search, twenty cards a request, at the single-lookup pace) and
stores, under ``min:`` and the card's oracle id, the lowest price of each finish that day, with the
printing that had it on the watch.

The same check prices the member's Binder and their own decks (binder_prices.py): the Binders in
use are read whole from Archidekt first, as their member, while the member's Archidekt allowance has
room, and every printing in them and in the decks they opened is priced with the watched ones.

A watched deck is read again once a day (one paced Archidekt read per deck, as the member sees
it), so its value follows the cards it holds; when the read fails, the last card list is used.

Optional alerts per card: a move of more than a set percentage from one day to the next, or a
crossing of a price the member sets (above or below). They go out through ntfy (notify.py) for
members who opted in. Limits per member (watch_max_cards, watch_max_decks) and how
long points are kept (price_history_days, purged with the rest in db.purge_expired) are
Settings page rows. Watching changes only the member's own gateway data, never Archidekt.
"""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .binder_prices import BINDER_PAGE_SIZE, PRICED_FOR_DAYS, BinderStore
from .decklist import clean_text
from .decks import DeckError, _clean_deck_id, owns_deck
from .theme import plural

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
CHEAPEST = "min:"  # + a card's oracle id: the price_points key of its cheapest printing each day
CHANGE_DAYS = (1, 7, 30)
# the points of a series a list row needs: the newest with a price for the row's finish, and the
# newest at or before 1, 7 and 30 days earlier (watchlist_summaries)
_PRICED_IN = {
    "nonfoil": "coalesce(usd, eur)",
    "foil": "coalesce(usd_foil, eur_foil)",
    "etched": "usd_etched",
}
_POINT_JSON = "json_array(day, usd, usd_foil, usd_etched, eur, eur_foil)"


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


def _ident(c: dict[str, Any]) -> dict[str, str] | None:
    """The Scryfall collection identifier for a watched card or deck row, or None for a row that
    names no card (no Scryfall id, no printing, no name), which the daily check skips."""
    if c.get("uid"):
        return {"id": str(c["uid"])}
    if c.get("set") and c.get("cn"):
        return {"set": str(c["set"]), "collector_number": str(c["cn"])}
    if not str(c.get("name") or "").strip():
        return None
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


# Where a watched deck's value comes from, said beside it: deck_stats' price_total is Archidekt's.
DECK_VALUE_SOURCE = (
    "Scryfall's daily prices (TCGplayer's, in US dollars) for the printing and finish of each card"
)

# Beside a card watched by name alone (no set): which printing its price is.
FOLLOWS_NAME = (
    "the cheapest printing in this finish at each daily check, which can change; watch a set and "
    "collector number to price one printing"
)


def series_key(w: dict[str, Any]) -> str | None:
    """The price_points key a watched card's prices are kept under: its cheapest printing's for a
    watch by name alone (once its oracle id is known), else its own printing's."""
    if not w.get("set_code") and w.get("oracle_id"):
        return CHEAPEST + str(w["oracle_id"])
    return w.get("scryfall_id") or None


def _decode(raw: str | None) -> dict[str, Any] | None:
    """A point read as ``json_array(day, usd, ...)``."""
    if not raw:
        return None
    day, *values = json.loads(raw)
    return {"day": day, **dict(zip(PRICE_KEYS, values, strict=True))}


def _oracle(card: dict[str, Any]) -> str:
    faces = card.get("card_faces") if isinstance(card.get("card_faces"), list) else []
    oid = card.get("oracle_id") or (faces[0].get("oracle_id") if faces and isinstance(faces[0], dict) else "")
    return str(oid or "")


def cheapest_point(cards: list[dict[str, Any]]) -> dict[str, float | None]:
    """The lowest price of each finish and currency over ``cards`` (every printing of one card)."""
    out: dict[str, float | None] = dict.fromkeys(PRICE_KEYS)
    for card in cards:
        for k, v in point_of(card).items():
            if v is not None and (out[k] is None or v < out[k]):
                out[k] = v
    return out


def cheapest_printing(cards: list[dict[str, Any]], finish: str) -> dict[str, Any] | None:
    """The printing with the lowest price in ``finish``: dollars when any printing has one in that
    finish, else euros."""
    for currency in ("usd", "eur"):
        priced = [(v, c) for c in cards if (v := price_for(point_of(c), finish, currency)) is not None]
        if priced:
            return min(priced, key=lambda x: (x[0], str(x[1].get("set")), str(x[1].get("collector_number"))))[
                1
            ]
    return None


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
        self.binder = BinderStore(db)
        # Reads a member's whole Binder from Archidekt (collection.CollectionService.read_whole,
        # set by app.py): (rows, complete). None: Binders are not read (no collection service).
        self.read_binder: Callable[[str], Awaitable[tuple[list[dict[str, Any]], bool]]] | None = None

    def _limit(self, key: str) -> int:
        """watch_max_cards / watch_max_decks as the admin Settings page has them now (0.7.29): a
        change there applies at once, without a restart."""
        live = getattr(self.decks, "live", None)
        return int(live.get(key)) if live is not None else int(getattr(self.settings, key))

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

    def _since(self, days: int) -> str:
        return days_before(day_of(now()), max(1, days) - 1)

    def _card_head(self, w: dict[str, Any]) -> dict[str, Any]:
        """What a watched card's row says whatever its prices: its name, printing, finish, alerts
        and, for a watch by name alone, the printing that was cheapest at the last check."""
        cheapest = None
        if not w.get("set_code") and w.get("cheapest_set"):
            cheapest = {"set": w["cheapest_set"], "collector_number": w.get("cheapest_cn") or ""}
        return {
            "watch_id": w["id"],
            "name": w["name"],
            "set": w["set_code"] or None,
            "collector_number": w["collector_number"] or None,
            "finish": w["finish"],
            "cheapest": cheapest,
            "alerts": {
                k: w[f"alert_{k}"] for k in ("pct", "above", "below") if w.get(f"alert_{k}") is not None
            },
            "priced": bool(series_key(w)),
        }

    def card_view(self, w: dict[str, Any], days: int = 90) -> dict[str, Any]:
        pts = self.points(series_key(w), days)
        cur = headline(pts[-1], w["finish"])[1] if pts else "usd"
        series = [(p["day"], price_for(p, w["finish"], cur)) for p in pts]
        return {
            **self._card_head(w),
            "currency": cur,
            **self._changes(series),
            "history": [{"day": d, "price": v} for d, v in series],
            **({} if w["set_code"] else {"follows": FOLLOWS_NAME}),
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

    def _deck_head(self, d: dict[str, Any]) -> dict[str, Any]:
        cards = json.loads(d.get("cards_json") or "[]")
        return {
            "deck_id": d["deck_id"],
            "name": d["deck_name"],
            "cards": sum(int(c.get("qty") or 0) for c in cards if isinstance(c, dict)),
            "currency": "usd",
            "price_source": DECK_VALUE_SOURCE,
            "refreshed_at": d.get("refreshed_at"),
        }

    def deck_view(self, sub: str, d: dict[str, Any], days: int = 90) -> dict[str, Any]:
        rows = self.deck_series(sub, d["deck_id"], days)
        return {
            **self._deck_head(d),
            **self._changes([(r["day"], r["usd"]) for r in rows]),
            "history": [{"day": r["day"], "price": r["usd"], "eur": r["eur"]} for r in rows],
        }

    # -- the Watchlist page: a fixed number of queries, whatever the list's size (0.7.46) ---------
    def card_summaries(self, sub: str) -> list[dict[str, Any]]:
        """Every watched card's row without its history: the latest price and its day, and the
        change over 1, 7 and 30 days, as card_view works them out, in one query (each point is an
        index lookup), so a list of thousands draws, filters and sorts without reading a year of
        prices per card."""
        span = self._since(366)

        def nth(n: int) -> str:
            return (
                f"(SELECT {_POINT_JSON} FROM price_points p WHERE p.scryfall_id = k.pkey AND p.day <= "
                f"date(json_extract(k.p0, '$[0]'), '-{n} day') AND p.day >= ? AND (CASE k.finish "
                + " ".join(f"WHEN '{f}' THEN {col}" for f, col in _PRICED_IN.items())
                + " END) IS NOT NULL ORDER BY p.day DESC LIMIT 1)"
            )

        newest = (
            f"(SELECT {_POINT_JSON} FROM price_points p WHERE p.scryfall_id = w.pkey AND p.day >= ? AND "
            "(CASE w.finish "
            + " ".join(f"WHEN '{f}' THEN {col}" for f, col in _PRICED_IN.items())
            + " END) IS NOT NULL ORDER BY p.day DESC LIMIT 1)"
        )
        rows = self._rows(
            f"SELECT k.*, {nth(1)} AS p1, {nth(7)} AS p7, {nth(30)} AS p30 FROM ("
            f"SELECT w.*, {newest} AS p0 FROM (SELECT watch_cards.*, CASE WHEN set_code = '' AND "
            f"oracle_id IS NOT NULL THEN '{CHEAPEST}' || oracle_id ELSE scryfall_id END AS pkey "
            "FROM watch_cards WHERE sub = ?) w) k ORDER BY k.name, k.set_code, k.finish",
            (span, span, span, span, sub),
        )
        out = []
        for r in rows:
            p0 = _decode(r["p0"])
            view = {**self._card_head(r), "currency": "usd", "price": None, "day": None, "key": r["pkey"]}
            view["scryfall_id"] = r.get("scryfall_id")
            if p0 is not None:
                price, cur = headline(p0, r["finish"])
                view.update(currency=cur, price=price, day=p0["day"])
                for n in CHANGE_DAYS:
                    before = _decode(r[f"p{n}"])
                    view[f"change_{n}d_pct"] = pct_change(price_for(before, r["finish"], cur), price)
            out.append(view)
        return out

    def card_histories(self, views: list[dict[str, Any]], days: int = 30) -> None:
        """The last ``days`` of prices of ``views`` (card_summaries rows, the ones on screen) as
        each one's ``history``, and how many days of prices each has in all (``full_days``): two
        queries for all of them."""
        keys = sorted({v["key"] for v in views if v.get("key")})
        pts: dict[str, list[dict[str, Any]]] = {}
        counts: dict[str, dict[str, int]] = {}
        since, year = self._since(days), self._since(365)
        for i in range(0, len(keys), 500):
            chunk = keys[i : i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self._rows(
                "SELECT scryfall_id, day, usd, usd_foil, usd_etched, eur, eur_foil FROM price_points "
                f"WHERE scryfall_id IN ({marks}) AND day >= ? ORDER BY day",
                (*chunk, since),
            ):
                pts.setdefault(r.pop("scryfall_id"), []).append(r)
            for r in self._rows(
                "SELECT scryfall_id, COUNT(usd) AS usd, COUNT(usd_foil) AS usd_foil, COUNT(usd_etched) AS "
                "usd_etched, COUNT(eur) AS eur, COUNT(eur_foil) AS eur_foil FROM price_points "
                f"WHERE scryfall_id IN ({marks}) AND day >= ? GROUP BY scryfall_id",
                (*chunk, year),
            ):
                counts[r.pop("scryfall_id")] = r
        for v in views:
            cur = v.get("currency") or "usd"
            v["history"] = [
                {"day": p["day"], "price": price_for(p, v["finish"], cur)}
                for p in pts.get(v.get("key") or "", [])
            ]
            usd_key, eur_key = FINISH_KEYS.get(v["finish"], FINISH_KEYS["nonfoil"])
            column = usd_key if cur == "usd" else eur_key
            v["full_days"] = int((counts.get(v.get("key") or "") or {}).get(column or "", 0) or 0)

    def deck_summaries(self, sub: str) -> list[dict[str, Any]]:
        """Every watched deck's row without its history (as card_summaries), in one query."""
        span = self._since(366)

        def nth(n: int) -> str:
            return (
                "(SELECT json_array(v.day, v.usd) FROM deck_values v WHERE v.sub = k.sub AND v.deck_id = "
                "k.deck_id AND v.usd IS NOT NULL AND v.day >= ? AND v.day <= "
                f"date(json_extract(k.p0, '$[0]'), '-{n} day') ORDER BY v.day DESC LIMIT 1)"
            )

        rows = self._rows(
            f"SELECT k.*, {nth(1)} AS p1, {nth(7)} AS p7, {nth(30)} AS p30 FROM (SELECT d.*, "
            "(SELECT json_array(v.day, v.usd) FROM deck_values v WHERE v.sub = d.sub AND "
            "v.deck_id = d.deck_id AND v.usd IS NOT NULL AND v.day >= ? ORDER BY v.day DESC LIMIT 1) "
            "AS p0 FROM watch_decks d WHERE d.sub = ?) k ORDER BY k.deck_name, k.deck_id",
            (span, span, span, span, sub),
        )
        out = []
        for r in rows:
            view = {**self._deck_head(r), "price": None, "day": None}
            if r["p0"]:
                day, price = json.loads(r["p0"])
                view.update(price=price, day=day)
                for n in CHANGE_DAYS:
                    before = json.loads(r[f"p{n}"])[1] if r[f"p{n}"] else None
                    view[f"change_{n}d_pct"] = pct_change(before, price)
            out.append(view)
        return out

    def deck_histories(self, sub: str, views: list[dict[str, Any]], days: int = 30) -> None:
        """The last ``days`` of values of ``views`` (the decks on screen) and their days in all."""
        ids = sorted({str(v["deck_id"]) for v in views})
        if not ids:
            return
        marks = ",".join("?" * len(ids))
        rows = self._rows(
            f"SELECT deck_id, day, usd, eur FROM deck_values WHERE sub = ? AND deck_id IN ({marks}) "
            "AND day >= ? ORDER BY day",
            (sub, *ids, self._since(days)),
        )
        counts = {
            r["deck_id"]: int(r["n"])
            for r in self._rows(
                f"SELECT deck_id, COUNT(usd) AS n FROM deck_values WHERE sub = ? AND deck_id IN ({marks}) "
                "AND day >= ? GROUP BY deck_id",
                (sub, *ids, self._since(365)),
            )
        }
        by: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            by.setdefault(r["deck_id"], []).append({"day": r["day"], "price": r["usd"], "eur": r["eur"]})
        for v in views:
            v["history"] = by.get(str(v["deck_id"]), [])
            v["full_days"] = counts.get(str(v["deck_id"]), 0)

    def owned_history(self, sub: str, scryfall_id: str, finish: str = "nonfoil") -> dict[str, Any] | None:
        """The card viewer's price history of a printing the member owns (in their Binder as last
        read, or in a deck of theirs the daily check prices): a year of its daily prices in
        ``finish``, dollars or else euros. None for a printing they do not own."""
        where = self.binder.owned_where(sub, scryfall_id)
        if not where:
            return None
        finish = finish if finish in FINISH_KEYS else "nonfoil"
        pts = self.binder.history(scryfall_id, self._since(365))
        cur = headline(pts[-1], finish)[1] if pts else "usd"
        series = [(p["day"], price_for(p, finish, cur)) for p in pts]
        return {
            "where": where,
            "finish": finish,
            "currency": cur,
            **self._changes(series),
            "history": [{"day": d, "price": v} for d, v in series],
        }

    def limits(self) -> dict[str, int]:
        return {"cards": self._limit("watch_max_cards"), "decks": self._limit("watch_max_decks")}

    def overview(self, sub: str, days: int = 90, *, history: bool = True) -> dict[str, Any]:
        """Every watched card and deck as list rows (card_summaries, deck_summaries), with the
        last ``days`` of prices each when ``history``."""
        s = self.settings
        cards, decks = self.card_summaries(sub), self.deck_summaries(sub)
        if history:
            self.card_histories(cards, days)
            self.deck_histories(sub, decks, days)
        for v in cards:
            v.pop("key", None)
            v.pop("scryfall_id", None)
        return {
            "cards": cards,
            "decks": decks,
            "limits": self.limits(),
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
        keep_alerts: bool = False,
        default_pct: float | None = None,
    ) -> dict[str, Any]:
        """Watch a card (or change the alerts of one already watched). The printing is looked
        up at once, so today's price shows right away (a watch by name alone: its cheapest
        printing's); when Scryfall is busy the watch is kept and priced by the next daily check.
        ``keep_alerts``: a card already watched keeps its alerts (the Watch buttons, which set
        none). ``default_pct``: the member's remembered alert, set on a new watch that names no
        alert of its own."""
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
        fresh_alerts = alerts
        if default_pct is not None and alerts == (None, None, None):
            fresh_alerts = _alerts(default_pct, None, None)
        if self._limit("watch_max_cards") <= 0:
            raise DeckError("invalid", "Watching cards is switched off on this gateway.")
        row = {"name": name, "set": set_code, "cn": number}
        card: dict[str, Any] | None = None
        note = ""
        try:
            found, _missing = await self._scry().collection([_ident(row) or {"name": name}])
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
            # by name alone the finish is checked over every printing, below
            if set_code and finish not in (card.get("finishes") or ["nonfoil"]):
                raise DeckError(
                    "invalid",
                    f"{name} ({str(card.get('set', '')).upper()}) is not printed in {finish}; "
                    f"it comes in {', '.join(card.get('finishes') or ['nonfoil'])}.",
                )
            if set_code:  # a name with only a set: keep the printing Scryfall chose
                number = number or str(card.get("collector_number") or "")
        oracle = _oracle(card) if card is not None else ""
        printings: list[dict[str, Any]] | None = None
        if oracle and not set_code:
            # by name alone: every printing, for the cheapest one's price today
            try:
                client = self._scry()
                printings = (await client.printings_of([oracle], interval=client.lookup_interval)).get(oracle)
            except Exception as exc:  # Scryfall busy: the daily check finds the cheapest
                logger.info("cheapest printing waits for the daily check: %s", exc.__class__.__name__)
                note = "Scryfall could not be asked just now; today's price comes with the next daily check."
        if printings and not any(finish in (p.get("finishes") or ["nonfoil"]) for p in printings):
            raise DeckError(
                "invalid",
                f"{name} is not printed in {finish}; it comes in "
                + ", ".join(sorted({f for p in printings for f in (p.get("finishes") or ["nonfoil"])}))
                + ".",
            )
        # the same printing in other letter case is the same watch; a watch by name of a card
        # watched in any printing (in this finish) is the watch it has, never a second one
        existing = self._rows(
            "SELECT * FROM watch_cards WHERE sub = ? AND lower(name) = lower(?) AND lower(set_code) = ? "
            "AND lower(collector_number) = lower(?) AND finish = ?",
            (sub, name, set_code, number, finish),
        )
        if not existing and not set_code:
            existing = [
                w
                for w in self.watched_cards(sub)
                if _norm(w["name"]) == _norm(name) and w["finish"] == finish
            ][:1]
        t = int(now())
        with self.db.tx() as c:
            if existing:
                wid = existing[0]["id"]
                if not keep_alerts:
                    c.execute(
                        "UPDATE watch_cards SET alert_pct = ?, alert_above = ?, alert_below = ? WHERE id = ?",
                        (*alerts, wid),
                    )
                if oracle:
                    c.execute("UPDATE watch_cards SET oracle_id = ? WHERE id = ?", (oracle, wid))
                created = False
            else:
                count = c.execute("SELECT COUNT(*) FROM watch_cards WHERE sub = ?", (sub,)).fetchone()[0]
                if count >= (most := self._limit("watch_max_cards")):
                    # 0.7.29: the limit can be lowered below what a member already watches (their
                    # watches are kept), so the message names both numbers.
                    raise DeckError(
                        "rate_limited",
                        f"You watch {plural(count, 'card')}; this gateway lets each member watch "
                        f"at most {most}. "
                        f"Remove {count - most + 1} to add another.",
                    )
                wid = "w_" + secrets.token_urlsafe(9)
                stored = c.execute(
                    "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, "
                    "scryfall_id, alert_pct, alert_above, alert_below, created_at, oracle_id) "
                    "SELECT ?,?,?,?,?,?,?,?,?,?,?,? "
                    "WHERE EXISTS (SELECT 1 FROM users WHERE sub = ?)",
                    (
                        wid,
                        sub,
                        name,
                        set_code,
                        number,
                        finish,
                        card.get("id") if card else None,
                        *fresh_alerts,
                        t,
                        oracle or None,
                        sub,
                    ),
                ).rowcount
                if not stored:
                    raise DeckError("not_found", "No gateway account for this member.")
                created = True
            if card is not None:
                self._store_point(c, str(card["id"]), point_of(card), day_of(t))
            if printings:
                self._store_cheapest(c, oracle, printings, day_of(t))
        out = self.card_view(self._rows("SELECT * FROM watch_cards WHERE id = ?", (wid,))[0])
        # the printing priced today: for a watch by name, the cheapest one in its finish
        now_card = card
        if not set_code and out.get("cheapest"):
            now_card = next(
                (
                    p
                    for p in printings or []
                    if str(p.get("set") or "") == out["cheapest"]["set"]
                    and str(p.get("collector_number") or "") == out["cheapest"]["collector_number"]
                ),
                {"set": out["cheapest"]["set"], "collector_number": out["cheapest"]["collector_number"]},
            )
        if now_card is not None:
            out["printing_now"] = {
                "set": str(now_card.get("set") or "") or None,
                "set_name": str(now_card.get("set_name") or "") or None,
                "collector_number": str(now_card.get("collector_number") or "") or None,
            }
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
        most = self._limit("watch_max_decks")
        if str(deck_id) not in {str(d) for d in have} and len(have) >= most:
            raise DeckError(
                "rate_limited",
                f"You watch {plural(len(have), 'deck')}; this gateway lets each member watch at most {most}. "
                f"Remove {len(have) - most + 1} to add another.",
            )

    async def watch_deck(self, sub: str, deck_ref: str) -> dict[str, Any]:
        """Watch a deck's value: read it now (as the member sees it), price its cards and store
        today's value."""
        deck_id = _clean_deck_id(deck_ref)
        if self._limit("watch_max_decks") <= 0:
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
    def _in_use_since(self) -> float:
        return now() - PRICED_FOR_DAYS * 86400

    def anything_watched(self) -> bool:
        """Anything to price: a watch, or a Binder or deck in use (binder_prices.PRICED_FOR_DAYS)."""
        with self.db._lock:
            watched = bool(
                self.db._conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM watch_cards) OR EXISTS (SELECT 1 FROM watch_decks)"
                ).fetchone()[0]
            )
        return watched or self.binder.anything(self._in_use_since())

    async def run_daily(self, day: str | None = None) -> dict[str, Any]:
        """Today's prices for every watched printing, each watched deck's value, the Binders and
        decks in use, the cheapest printing of each watch by name alone, then alerts. Costs nothing
        (no request) when there is nothing to price."""
        day = day or day_of(now())
        async with self._run_lock:
            # Binders not opened and decks not read for PRICED_FOR_DAYS: their copies go
            self.binder.forget_old(self._in_use_since())
            if not self.anything_watched():
                return {"day": day, "watched": False, "requests": 0}
            refreshed = await self._refresh_decks(day)
            read = await self._read_binders(day)
            before = self._scry().requests
            priced = await self._price_missing(day)
            cheapest = await self._price_cheapest(day)
            self._store_deck_values(day)
            alerts = self._alerts_due(day)
        for sub, title, message, path in alerts:
            if self.notifier is not None:
                await self.notifier.send(sub, "prices", title, message, path)
        return {
            "day": day,
            "watched": True,
            "decks_refreshed": refreshed,
            "binders_read": read,
            "priced": priced,
            "cheapest": cheapest,
            "requests": self._scry().requests - before,
            "alerts": len(alerts),
        }

    async def _read_binders(self, day: str) -> int:
        """Read each Binder in use again, whole, once a day (paced like every Archidekt read, as its
        member): only while the member's Archidekt allowance keeps a quarter back for their own
        changes, so a big Binder never crowds them out; a Binder not read keeps its last read and
        is priced as it is. A read that fails is tried again the next day."""
        if self.read_binder is None:
            return 0
        budget = getattr(self.decks, "archidekt_budget", None)
        n = 0
        for info in self.binder.in_use(self._in_use_since()):
            sub = info["sub"]
            if info.get("read_at") and day_of(info["read_at"]) == day:
                continue
            pages = int(info.get("records") or 0) // BINDER_PAGE_SIZE + 2
            if budget is not None and budget.room(sub) < pages + budget.per_window // 4:
                logger.info("a Binder is not read today: its member's Archidekt allowance is low")
                continue
            try:
                rows, complete = await self.read_binder(sub)
            except Exception as exc:
                logger.info("a Binder was not read today: %s", exc.__class__.__name__)
                continue
            self.binder.replace(sub, rows, complete=complete, now=now())
            n += 1
        return n

    def note_deck(self, sub: str, deck: Any) -> None:
        """A deck page read ``deck`` for ``sub`` (decks.read_hooks): a deck of their own has its
        printings noted, so they are priced daily (binder_prices). Someone else's deck is not."""
        if not sub:
            return
        link = self.db.get_link(sub)
        if not link or owns_deck(deck, link) is not True:
            return
        self.binder.note_deck(sub, str(deck.id), str(deck.name or ""), _deck_rows(deck), now=now())

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

    async def first_read(self, sub: str) -> bool:
        """A Binder opened before it was ever read whole: read it now, as its member, and price it
        (its records only), so its value and the price sort are there within minutes, not the
        next day. Returns whether it was read. A failure leaves it to the daily check."""
        if self.read_binder is None:
            return False
        try:
            rows, complete = await self.read_binder(sub)
        except Exception as exc:
            logger.info("a Binder was not read: %s", exc.__class__.__name__)
            return False
        self.binder.replace(sub, rows, complete=complete, now=now())
        try:
            await self._price_missing(day_of(now()), only_binder=sub)
        except Exception as exc:
            logger.info("a Binder was read but not priced: %s", exc.__class__.__name__)
        return True

    async def _price_missing(
        self, day: str, *, only_deck: tuple[str, str] | None = None, only_binder: str | None = None
    ) -> int:
        """Ask Scryfall (in batches of 75) for the watched printings, and those of the Binders and
        decks in use, with no point for ``day``, store their points, and record the printing of
        rows that had none (a name-only watch, a deck card or a Binder record without a Scryfall
        id) and the oracle id of a watch by name alone (its cheapest printing is priced by
        _price_cheapest, not here). ``only_deck`` (member, deck id): that deck's cards only;
        ``only_binder`` (member): that member's Binder only."""
        owned: list[dict[str, Any]] = []
        if only_binder:
            cards, decks = [], []
            owned = self.binder.to_price(self._in_use_since(), sub=only_binder)
        elif only_deck:
            cards = []
            decks = self._rows("SELECT * FROM watch_decks WHERE sub = ? AND deck_id = ?", only_deck)
        else:
            cards = self._rows("SELECT * FROM watch_cards")
            decks = self._rows("SELECT * FROM watch_decks")
            owned = self.binder.to_price(self._in_use_since())
        with self.db._lock:
            have = {
                r[0]
                for r in self.db._conn.execute("SELECT scryfall_id FROM price_points WHERE day = ?", (day,))
            }
        wanted: dict[tuple[str, ...], dict[str, str]] = {}
        for w in cards:
            by_name = not w["set_code"]
            if by_name and w.get("oracle_id"):
                continue  # its cheapest printing is its price
            if w["scryfall_id"] and w["scryfall_id"] in have and not by_name:
                continue
            ident = _ident(
                {
                    "uid": w["scryfall_id"],
                    "name": w["name"],
                    "set": w["set_code"],
                    "cn": w["collector_number"],
                }
            )
            if ident is None:
                logger.warning("price check: skipped watched card %s, which names no card", w["id"])
                continue
            wanted[_ident_key(ident)] = ident
        deck_rows = {d["sub"] + "\0" + d["deck_id"]: json.loads(d["cards_json"] or "[]") for d in decks}
        rows_of_decks = [(key, r) for key, rows in deck_rows.items() for r in rows]
        for key, r in [*rows_of_decks, *(("", r) for r in owned)]:
            if not isinstance(r, dict) or (r.get("uid") and r["uid"] in have):
                continue
            ident = _ident(r)
            if ident is None:
                # one bad row must not stop the day's check for every member
                logger.warning(
                    "price check: skipped a row of %s that names no card",
                    f"watched deck {key.split(chr(0), 1)[1]}" if key else "a Binder or deck",
                )
                continue
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
                if w["scryfall_id"] and (w["set_code"] or w.get("oracle_id")):
                    continue
                ident = _ident(
                    {
                        "uid": w["scryfall_id"],
                        "name": w["name"],
                        "set": w["set_code"],
                        "cn": w["collector_number"],
                    }
                )
                hit = by_key.get(_ident_key(ident)) if ident else None
                if hit:
                    c.execute(
                        "UPDATE watch_cards SET scryfall_id = coalesce(scryfall_id, ?), oracle_id = "
                        "CASE WHEN set_code = '' THEN ? ELSE oracle_id END WHERE id = ?",
                        (hit["id"], _oracle(hit) or None, w["id"]),
                    )
            for key, rows in deck_rows.items():
                changed = False
                for r in rows:
                    ident = _ident(r) if isinstance(r, dict) and not r.get("uid") else None
                    if ident is not None:
                        hit = by_key.get(_ident_key(ident))
                        if hit:
                            r["uid"], changed = str(hit["id"]), True
                if changed:
                    sub, deck_id = key.split("\0", 1)
                    c.execute(
                        "UPDATE watch_decks SET cards_json = ? WHERE sub = ? AND deck_id = ?",
                        (json.dumps(rows), sub, deck_id),
                    )
        for r in owned:  # a Binder record that carried no Scryfall id keeps the one found
            if r.get("rid") is not None and not r.get("uid") and (ident := _ident(r)) is not None:
                hit = by_key.get(_ident_key(ident))
                if hit:
                    self.binder.set_uid(r["sub"], r["rid"], str(hit["id"]))
        return len(found)

    async def _price_cheapest(self, day: str) -> int:
        """Every printing of each card watched by name alone (twenty cards a Scryfall search, at the
        single-lookup pace), for the lowest price of each finish that day and the printing that had
        it. Returns how many cards were priced."""
        watches = self._rows("SELECT * FROM watch_cards WHERE set_code = '' AND oracle_id IS NOT NULL")
        if not watches:
            return 0
        with self.db._lock:
            done = {
                r[0]
                for r in self.db._conn.execute(
                    "SELECT scryfall_id FROM price_points WHERE day = ? AND scryfall_id LIKE ?",
                    (day, CHEAPEST + "%"),
                )
            }
        oids = sorted({w["oracle_id"] for w in watches if CHEAPEST + w["oracle_id"] not in done})
        if not oids:
            return 0
        client = self._scry()
        found = await client.printings_of(oids, interval=client.lookup_interval)
        with self.db.tx() as c:
            for oid, cards in found.items():
                self._store_cheapest(c, oid, cards, day)
        return len(found)

    def _store_cheapest(self, c: Any, oracle: str, cards: list[dict[str, Any]], day: str) -> None:
        """The lowest price of each finish over ``cards`` (every printing of the card ``oracle``)
        as that day's point, and on each watch of it by name the printing that had it."""
        if not cards:
            return
        self._store_point(c, CHEAPEST + oracle, cheapest_point(cards), day)
        for w in c.execute(
            "SELECT id, finish FROM watch_cards WHERE set_code = '' AND oracle_id = ?", (oracle,)
        ).fetchall():
            best = cheapest_printing(cards, w[1])
            c.execute(
                "UPDATE watch_cards SET cheapest_id = ?, cheapest_set = ?, cheapest_cn = ? WHERE id = ?",
                (
                    str(best["id"]) if best else None,
                    str(best.get("set") or "") if best else None,
                    str(best.get("collector_number") or "") if best else None,
                    w[0],
                ),
            )

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

    def _alerts_due(self, day: str) -> list[tuple[str, str, str, str]]:
        """(member, title, message, path) for each watched card whose alert fired between its last
        point before ``day`` and today's; the path opens that card's row on the Watchlist."""
        out = []
        rows = self._rows(
            "SELECT * FROM watch_cards WHERE (scryfall_id IS NOT NULL OR oracle_id IS NOT NULL) AND "
            "(alert_pct IS NOT NULL OR alert_above IS NOT NULL OR alert_below IS NOT NULL)"
        )
        for w in rows:
            if not (key := series_key(w)):
                continue
            pts = self._rows(
                "SELECT * FROM price_points WHERE scryfall_id = ? AND day <= ? ORDER BY day DESC LIMIT 2",
                (key, day),
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
                        f"/watchlist?open={quote(str(w['id']), safe='')}#wr-{quote(str(w['id']), safe='')}",
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
    price_hour_utc (summaries right after the prices, so they include them). Which day
    each last ran is kept in the database, so a restart neither repeats nor skips one. With
    watchlists off ``prices`` is None and only the summaries run (app.py starts this only when
    watchlists or notifications are on)."""

    def __init__(self, prices: PriceService | None, summaries: Any, hour_utc: int):
        self.prices = prices
        self.summaries = summaries
        self.hour = hour_utc
        self.db = prices.db if prices is not None else summaries.db

    def _last_day(self, name: str) -> str | None:
        with self.db._lock:
            row = self.db._conn.execute("SELECT day FROM job_runs WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def _mark(self, name: str, day: str, detail: dict[str, Any]) -> None:
        with self.db.tx() as c:
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
        if self.prices is not None and self._last_day("prices") != day:
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
