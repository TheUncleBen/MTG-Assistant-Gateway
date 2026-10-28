"""Minimal Scryfall client for card resolution.

Endpoints used (all verified against https://scryfall.com/docs/api and live
on 2026-10-04):

* ``GET /cards/named?exact=`` and ``?fuzzy=`` — one card, or a 404 whose
  ``type`` is ``ambiguous`` when several cards match a fuzzy name.
* ``GET /cards/autocomplete?q=`` — up to 20 card names (``object: catalog``).
* ``GET /cards/{code}/{number}`` — one printing by set code and collector number.
* ``POST /cards/collection`` — up to 75 identifiers (``{name}``,
  ``{name, set}``, ``{set, collector_number}``, ``{id}``) in one call; the
  reply carries ``data`` and ``not_found``.

Scryfall asks clients to send ``User-Agent`` and ``Accept`` headers and to keep
request rates modest. Its current per-endpoint limits are not published in a
form we could verify: on 2026-10-04 a live run at 100 ms spacing drew 429s with
``Retry-After: 60`` after roughly 25 single-card lookups. So the client has two
pacers, shared gateway-wide: ``min_interval`` (default 100 ms) for the batched
``/cards/collection`` call and a slower ``lookup_interval`` (default 500 ms,
the scryfall_lookup_interval setting) for ``named``, ``autocomplete`` and printing
lookups. After a 429 every call fails fast with ``rate_limited`` until the
Retry-After window has passed; ``retry_in()`` tells callers how long that is.
Responses are cached in memory for a day so a scan session of 100 cards costs
few requests.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import re
import time
from datetime import UTC, datetime
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_BASE = "https://api.scryfall.com"
COLLECTION_MAX = 75


class ScryfallError(Exception):
    """kind: not_found, ambiguous, rate_limited, unavailable, contract."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def collection_ident(ident: dict[str, str]) -> dict[str, str]:
    """``ident`` as POST /cards/collection finds it: a name identifier names a split, adventure or
    two-faced card by its front face, the only name that lookup knows it by ({"name": "Fire"}
    returns Fire // Ice, {"name": "Fire // Ice"} is not_found; checked 2026-10-28). The card that
    comes back carries its full name."""
    name = ident.get("name")
    if not isinstance(name, str) or "//" not in name:
        return ident
    front = re.split(r"\s*//\s*", name, maxsplit=1)[0].strip()
    return {**ident, "name": front} if front else ident


def today() -> str:
    """Today in UTC, written as Scryfall writes ``released_at`` (YYYY-MM-DD). Tests replace it."""
    return datetime.now(UTC).date().isoformat()


# The "newest released paper printing" rule: is_released, on_paper, good_default and
# default_printing below are its one owner in the gateway (scan/service.py, the card search and the
# printing picker all call these). The research image carries its own copy of the same rule in
# docker/mystic-forge/patches/0005-scryfall-default-printing-and-finish.patch (_good_default,
# _default_printing); change both together. Removing that copy needs a new research image.


def is_released(card: dict[str, Any]) -> bool:
    """Out already: released today or earlier (a card with no date counts as released)."""
    released = card.get("released_at")
    return not isinstance(released, str) or not released or released <= today()


def on_paper(card: dict[str, Any]) -> bool:
    """Sold as a paper card. A preview has ``games: []`` until it is out; an Arena or MTGO
    printing has no ``paper``. Reads a raw card's ``games`` or a summary's ``paper`` flag."""
    games = card.get("games")
    if isinstance(games, list):
        return "paper" in games
    return card.get("paper") is not False


def good_default(card: dict[str, Any], finish: str | None = None) -> bool:
    """A printing that may stand in when no set was given: released, on paper, and in ``finish``
    when one was asked for."""
    return is_released(card) and on_paper(card) and (not finish or finish in (card.get("finishes") or []))


def default_printing(cards: list[dict[str, Any]], finish: str | None = None) -> dict[str, Any] | None:
    """The printing to use when no set was given: the newest released paper printing (in
    ``finish`` when given) of ``cards``, which are one card's printings newest first. Scryfall's
    own default is its newest printing, which can be a preview that is not out yet."""
    return next((c for c in cards if good_default(c, finish)), None)


# The printing picker's filters and sorts, as Scryfall's search writes them: ``is:foil``,
# ``frame:2015``, ``border:borderless``; ``order=usd`` with ``dir=asc`` is cheapest first.
PRINT_FINISHES = ("nonfoil", "foil", "etched")
PRINT_FRAMES = ("1993", "1997", "2003", "2015", "future")
PRINT_BORDERS = ("black", "white", "borderless", "silver", "gold")
PRINT_ORDERS = {"newest": ("released", "desc"), "oldest": ("released", "asc"), "cheapest": ("usd", "asc")}
PRINT_PAGES = 20  # 3,500 printings, more than any card has (a basic land has about 1,000)


def summarize(card: dict[str, Any]) -> dict[str, Any]:
    """The subset of a Scryfall card object the gateway stores and returns."""
    faces = card.get("card_faces") or []
    image_uris = card.get("image_uris") or (faces[0].get("image_uris") if faces else None) or {}
    mana_cost = card.get("mana_cost")
    if mana_cost is None and faces:
        mana_cost = " // ".join(f.get("mana_cost", "") for f in faces if f.get("mana_cost"))
    out = {
        "name": card.get("name"),
        "scryfall_id": card.get("id"),
        # Reversible cards carry the oracle id on their faces only.
        "oracle_id": card.get("oracle_id") or (faces[0].get("oracle_id") if faces else None),
        "set": card.get("set"),
        "set_name": card.get("set_name"),
        "collector_number": card.get("collector_number"),
        "rarity": card.get("rarity"),
        "type_line": card.get("type_line"),
        "mana_cost": mana_cost or "",
        "mana_value": card.get("cmc"),
        "color_identity": card.get("color_identity") or [],
        "layout": card.get("layout"),
        "lang": card.get("lang"),
        "released_at": card.get("released_at"),
        "image_small": image_uris.get("small"),
        "image_normal": image_uris.get("normal"),
        "image_art": image_uris.get("art_crop"),
        "scryfall_uri": card.get("scryfall_uri"),
        "finishes": [f for f in (card.get("finishes") or []) if isinstance(f, str)],
    }
    legal = card.get("legalities") or {}
    if legal:
        out["legal_commander"] = legal.get("commander")
    # Flags only when they apply, so most summaries stay as they were.
    if not is_released(card):
        out["unreleased"] = True
    if not on_paper(card):
        out["paper"] = False
    return out


def _cn(number: str) -> str:
    """A collector number for Scryfall's ``cn:``: as it is when it is plain letters and digits,
    else in double quotes (numbers such as ``1★``)."""
    return number if re.fullmatch(r"[A-Za-z0-9]+", number) else f'"{number}"'


def browse_row(card: dict[str, Any]) -> dict[str, Any]:
    """A summary plus what the card search's list view and the printing picker show: prices,
    power, toughness and loyalty, and the printing's frame, border and treatments."""
    out = summarize(card)
    faces = [f for f in card.get("card_faces") or [] if isinstance(f, dict)]
    front = faces[0] if faces else {}
    prices = card.get("prices") if isinstance(card.get("prices"), dict) else {}
    out["prices"] = {
        k: str(prices[k])
        for k in ("usd", "usd_foil", "usd_etched", "eur", "eur_foil", "tix")
        if isinstance(prices.get(k), str) and prices[k]
    }
    for key in ("power", "toughness", "loyalty"):
        value = card.get(key, front.get(key))
        if isinstance(value, str) and value:
            out[key] = value
    out["frame"] = str(card.get("frame") or "")
    out["border"] = str(card.get("border_color") or "")
    out["frame_effects"] = [e for e in card.get("frame_effects") or [] if isinstance(e, str)]
    out["full_art"] = bool(card.get("full_art"))
    out["promo"] = bool(card.get("promo"))
    out["digital"] = bool(card.get("digital"))
    out["set_type"] = str(card.get("set_type") or "")
    rank = card.get("edhrec_rank")
    if isinstance(rank, int) and not isinstance(rank, bool):
        out["edhrec_rank"] = rank
    return out


_CARD_KEYS = (
    "object",
    "id",
    "oracle_id",
    "name",
    "set",
    "set_name",
    "collector_number",
    "rarity",
    "type_line",
    "mana_cost",
    "cmc",
    "color_identity",
    "layout",
    "lang",
    "released_at",
    "scryfall_uri",
    "finishes",
    "games",
)
_FACE_KEYS = ("name", "mana_cost", "oracle_id")
_IMAGE_KEYS = ("small", "normal", "art_crop")


def slim(card: dict[str, Any]) -> dict[str, Any]:
    """A raw card cut down to the fields ``summarize`` and the resolver read. A full Scryfall
    card is about 18 KB in memory; this is under 1 KB, so the lookup cache stays a few MB."""
    out = {k: card[k] for k in _CARD_KEYS if k in card}
    if isinstance(card.get("image_uris"), dict):
        out["image_uris"] = {k: card["image_uris"][k] for k in _IMAGE_KEYS if k in card["image_uris"]}
    faces = card.get("card_faces")
    if isinstance(faces, list):
        out["card_faces"] = []
        for f in faces:
            if not isinstance(f, dict):
                continue
            face = {k: f[k] for k in _FACE_KEYS if k in f}
            if isinstance(f.get("image_uris"), dict):
                face["image_uris"] = {k: f["image_uris"][k] for k in _IMAGE_KEYS if k in f["image_uris"]}
            out["card_faces"].append(face)
    legal = card.get("legalities")
    if isinstance(legal, dict) and "commander" in legal:
        out["legalities"] = {"commander": legal["commander"]}
    return out


def _retry_after(value: str | None, default: float = 5.0, cap: float = 120.0) -> float:
    """Seconds to back off after a 429 (Retry-After in seconds; HTTP dates are not expected)."""
    try:
        return min(max(float(value or default), 1.0), cap)
    except ValueError:
        return default


class _Cache:
    def __init__(self, ttl: float, max_items: int):
        self.ttl = ttl
        self.max_items = max_items
        self._items: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Any | None:
        hit = self._items.get(key)
        if hit is None:
            return None
        if hit[0] < time.monotonic():
            self._items.pop(key, None)
            return None
        return hit[1]

    def put(self, key: str, value: Any) -> None:
        if len(self._items) >= self.max_items:
            oldest = sorted(self._items.items(), key=lambda kv: kv[1][0])[: self.max_items // 10 or 1]
            for k, _ in oldest:
                self._items.pop(k, None)
        self._items[key] = (time.monotonic() + self.ttl, value)


class ScryfallClient:
    def __init__(
        self,
        base: str = DEFAULT_BASE,
        *,
        user_agent: str = "mtg-assistant-gateway/0.1",
        min_interval: float = 0.1,
        lookup_interval: float | None = None,
        timeout: float = 15.0,
        http: httpx.AsyncClient | None = None,
    ):
        self.base = base.rstrip("/")
        self._own_http = http is None
        self.http = http or httpx.AsyncClient(timeout=timeout)
        self.headers = {"User-Agent": user_agent, "Accept": "application/json"}
        self.min_interval = min_interval
        # Single-card lookups are paced more slowly than the batch endpoint (the gateway passes
        # scryfall_lookup_interval, default 0.5 s); None means "same as min_interval".
        slow = min_interval if lookup_interval is None else lookup_interval
        self.lookup_interval = max(min_interval, slow)
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._retry_until = 0.0
        self.cards = _Cache(ttl=24 * 3600, max_items=5000)
        self.names = _Cache(ttl=6 * 3600, max_items=5000)
        # Exact names Scryfall answered 404 for, so a misspelt name tried again and again (a
        # proposal retried) is asked once in ten minutes; only a 404 is kept (_card_or_error).
        self.misses = _Cache(ttl=600, max_items=2000)
        # Cards are kept slimmed (``slim``, under 1 KB each), so 5,000 entries stay a few MB.
        # Printing lists are summaries (about 2 KB a card in memory, up to 175 a page), in a small
        # cache of their own so a picker browsing many cards cannot crowd out names; 60 pages stay
        # under about 25 MB even when every one is full. A later page or a filtered page of a card
        # with more than 175 printings is a page of its own here.
        self.prints_cache = _Cache(ttl=3600, max_items=60)
        # Card search pages (up to 175 browse rows, about 1.5 KB each in memory): 40 pages stay
        # under about 10 MB; a quarter of an hour is long enough for paging back and a reload.
        self.search_cache = _Cache(ttl=900, max_items=40)
        # The set list and the type catalogs for the card search's pickers, a day at a time.
        self.catalogs = _Cache(ttl=24 * 3600, max_items=20)
        self.requests = 0

    async def aclose(self) -> None:
        if self._own_http:
            await self.http.aclose()

    def retry_in(self) -> int:
        """Whole seconds until Scryfall may be called again after a 429 (0 when not backing off)."""
        return max(0, math.ceil(self._retry_until - time.monotonic()))

    def _rate_limited(self) -> ScryfallError:
        n = max(1, self.retry_in())
        return ScryfallError(
            "rate_limited",
            f"Scryfall asked the gateway to slow down, so it is pausing its Scryfall lookups for {n} "
            f"second{'' if n == 1 else 's'}. Try again after that.",
        )

    async def _request(
        self, method: str, path: str, *, interval: float | None = None, **kw: Any
    ) -> httpx.Response:
        async with self._lock:
            if self._retry_until > time.monotonic():
                raise self._rate_limited()
            wait = self._last + (self.min_interval if interval is None else interval) - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            try:
                resp = await self.http.request(method, f"{self.base}{path}", headers=self.headers, **kw)
            except httpx.HTTPError as exc:
                raise ScryfallError("unavailable", f"Scryfall unreachable: {exc.__class__.__name__}") from exc
            finally:
                self._last = time.monotonic()
                self.requests += 1
        if resp.status_code == 429:
            self._retry_until = time.monotonic() + _retry_after(resp.headers.get("retry-after"))
            logger.warning("Scryfall 429 on %s; pausing all lookups for %d s", path, self.retry_in())
            raise self._rate_limited()
        if resp.status_code >= 500:
            raise ScryfallError("unavailable", f"Scryfall returned {resp.status_code}")
        return resp

    @staticmethod
    def _json(resp: httpx.Response) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError as exc:
            raise ScryfallError("contract", "Scryfall returned a non-JSON body") from exc
        if not isinstance(data, dict):
            raise ScryfallError("contract", "Scryfall returned an unexpected body")
        return data

    def _card_or_error(self, resp: httpx.Response, what: str) -> dict[str, Any]:
        """The card in ``resp``. Only a 404 says the name is unknown (or ambiguous); any other
        error (400, 403, 422) says nothing about the card, so it is ``unavailable``, never
        ``not_found``, and no cache takes it for an unknown name."""
        data = self._json(resp)
        if resp.status_code == 404:
            kind = "ambiguous" if data.get("type") == "ambiguous" else "not_found"
            raise ScryfallError(kind, str(data.get("details") or f"No card found for {what}"))
        if resp.status_code >= 400 or data.get("object") == "error":
            raise ScryfallError("unavailable", f"Scryfall returned {resp.status_code} for {what}")
        if data.get("object") != "card" or not data.get("name"):
            raise ScryfallError("contract", "Scryfall returned something that is not a card")
        return data

    async def named(self, name: str, *, fuzzy: bool = False, set_code: str | None = None) -> dict[str, Any]:
        """One card by name. Exact by default; ``fuzzy=True`` tolerates typos and OCR noise."""
        key = f"named:{'f' if fuzzy else 'e'}:{(set_code or '').lower()}:{name.strip().lower()}"
        if (hit := self.cards.get(key)) is not None:
            return hit
        if (missed := self.misses.get(key)) is not None:
            raise ScryfallError("not_found", missed)
        params = {"fuzzy" if fuzzy else "exact": name.strip()}
        if set_code:
            params["set"] = set_code.lower()
        resp = await self._request("GET", "/cards/named", params=params, interval=self.lookup_interval)
        try:
            raw = self._card_or_error(resp, name)
        except ScryfallError as exc:
            if exc.kind == "not_found" and not fuzzy:
                self.misses.put(key, str(exc))
            raise
        card = slim(raw)
        self.cards.put(key, card)
        return card

    async def named_raw(self, name: str) -> dict[str, Any]:
        """One card by exact name, the whole Scryfall object, not cached here: the caller wants
        the fields ``slim`` drops (rules text, power and toughness, loyalty, flavour) and keeps
        them in its own small cache (cardsearch.py). Paced like every lookup."""
        resp = await self._request(
            "GET", "/cards/named", params={"exact": name.strip()}, interval=self.lookup_interval
        )
        return self._card_or_error(resp, name)

    async def rulings(self, card_id: str) -> list[dict[str, str]]:
        """GET /cards/{id}/rulings: the card's rulings ({date, text, source} each, oldest first as
        Scryfall lists them). Not cached here; the card viewer keeps them (cardsearch.py)."""
        if not re.fullmatch(r"[0-9a-f-]{36}", card_id or ""):
            raise ScryfallError("not_found", "No such card id")
        resp = await self._request("GET", f"/cards/{card_id}/rulings", interval=self.lookup_interval)
        data = self._json(resp)
        if resp.status_code == 404 or data.get("object") == "error":
            raise ScryfallError("not_found", str(data.get("details") or "No rulings found"))
        rows = data.get("data")
        if data.get("object") != "list" or not isinstance(rows, list):
            raise ScryfallError("contract", "Scryfall rulings returned an unexpected body")
        return [
            {
                "date": str(r.get("published_at") or ""),
                "text": str(r.get("comment") or ""),
                "source": "Wizards of the Coast" if r.get("source") == "wotc" else "Scryfall",
            }
            for r in rows
            if isinstance(r, dict) and r.get("comment")
        ]

    async def autocomplete(self, query: str) -> list[str]:
        q = query.strip()
        if len(q) < 2:
            return []
        key = f"ac:{q.lower()}"
        if (hit := self.names.get(key)) is not None:
            return hit
        resp = await self._request(
            "GET", "/cards/autocomplete", params={"q": q}, interval=self.lookup_interval
        )
        data = self._json(resp)
        names = data.get("data")
        if data.get("object") != "catalog" or not isinstance(names, list):
            raise ScryfallError("contract", "Scryfall autocomplete returned an unexpected body")
        out = [n for n in names if isinstance(n, str)]
        self.names.put(key, out)
        return out

    async def catalog_card_names(self) -> list[str]:
        """Every card name Scryfall knows (``GET /catalog/card-names``, about 700 KB, a ``catalog``
        object with ``data``: the full name of every card, double-faced ones as ``Front // Back``).
        One request; the caller keeps the list and refreshes it daily (see names.py)."""
        resp = await self._request("GET", "/catalog/card-names", interval=self.lookup_interval)
        data = self._json(resp)
        names = data.get("data")
        if data.get("object") != "catalog" or not isinstance(names, list):
            raise ScryfallError("contract", "Scryfall's card-name catalog has an unexpected shape")
        out = [n for n in names if isinstance(n, str) and n]
        if len(out) < 1000:
            raise ScryfallError("contract", f"Scryfall's card-name catalog is too short ({len(out)})")
        return out

    async def prints(
        self,
        oracle_id: str,
        *,
        page: int = 1,
        finish: str | None = None,
        frame: str | None = None,
        border: str | None = None,
        order: str = "newest",
        set_code: str | None = None,
        number: str | None = None,
    ) -> dict[str, Any]:
        """One page of every printing of one card (the ``prints_search_uri`` query), summarized
        with what the printing picker shows (``browse_row``: prices, frame, border).

        Returns ``{"cards": [rows], "has_more": bool, "total_cards": int, "page": int}``. Scryfall
        pages at 175 cards and says ``has_more`` with a ``next_page`` link, which is this same
        search with ``page`` one higher: the next page is asked for as ``page + 1``. ``finish``
        (PRINT_FINISHES), ``frame`` (PRINT_FRAMES) and ``border`` (PRINT_BORDERS) narrow the
        search on Scryfall's side and ``order`` (PRINT_ORDERS) sorts it, so a card with more
        printings than one page is filtered and sorted over all of them; the caller checks the
        values. Every page of every filter is kept in ``prints_cache`` (the plain first page under
        the oracle id alone, as before). ``set_code`` with ``number`` asks for that one printing
        (Scryfall's ``set:`` and ``cn:``), checked by the caller.
        """
        if order not in PRINT_ORDERS:
            raise ScryfallError("invalid", "order must be newest, oldest or cheapest")
        terms = " ".join(
            t
            for t in (
                f"is:{finish}" if finish else "",
                f"frame:{frame}" if frame else "",
                f"border:{border}" if border else "",
                f"set:{set_code} cn:{_cn(number)}" if set_code and number else "",
            )
            if t
        )
        key = oracle_id.lower()
        if page != 1 or terms or order != "newest":
            key = f"{key}|{page}|{terms}|{order}"
        if (hit := self.prints_cache.get(key)) is not None:
            return hit
        sort, direction = PRINT_ORDERS[order]
        params: dict[str, Any] = {
            "order": sort,
            "dir": direction,
            "q": f"oracleid:{oracle_id}" + (f" {terms}" if terms else ""),
            "unique": "prints",
            # a token's or an emblem's printings: Scryfall's search leaves these extras out
            # otherwise (one card's oracle id only ever matches that card's own printings)
            "include_extras": "true",
        }
        if page > 1:
            params["page"] = page
        resp = await self._request("GET", "/cards/search", params=params, interval=self.lookup_interval)
        data = self._json(resp)
        if resp.status_code == 404 or data.get("object") == "error":
            if resp.status_code == 404 and (terms or page > 1):
                # a filter that matches nothing (or a page past the end): an empty page, not a
                # card that does not exist
                empty: dict[str, Any] = {"cards": [], "has_more": False, "total_cards": 0, "page": page}
                self.prints_cache.put(key, empty)
                return empty
            raise ScryfallError("not_found", str(data.get("details") or "no printings found"))
        if data.get("object") != "list" or not isinstance(data.get("data"), list):
            raise ScryfallError("contract", "Scryfall search returned an unexpected body")
        cards = [browse_row(c) for c in data["data"] if isinstance(c, dict) and c.get("object") == "card"]
        total = data.get("total_cards")
        out = {
            "cards": cards,
            "has_more": bool(data.get("has_more")) and page < PRINT_PAGES,
            "total_cards": total if isinstance(total, int) else len(cards),
            "page": page,
        }
        self.prints_cache.put(key, out)
        return out

    async def search(
        self, query: str, *, order: str = "name", direction: str = "auto", page: int = 1
    ) -> dict[str, Any]:
        """One page of ``GET /cards/search`` (the card search page): ``{"cards": [browse rows],
        "has_more", "total_cards", "warnings"}``. A search that matches nothing is an empty page
        (Scryfall answers it with a 404); a search Scryfall cannot read is ``invalid`` with
        Scryfall's own explanation. Pages are kept a while (``search_cache``), so paging back, a
        second member's identical search and a reload cost no request."""
        q = query.strip()
        if not q:
            raise ScryfallError("invalid", "nothing to search for")
        key = f"{order}|{direction}|{page}|{q}"
        if (hit := self.search_cache.get(key)) is not None:
            return hit
        params: dict[str, Any] = {"q": q, "order": order, "dir": direction}
        if page > 1:
            params["page"] = page
        resp = await self._request("GET", "/cards/search", params=params, interval=self.lookup_interval)
        data = self._json(resp)
        if data.get("object") == "error":
            if resp.status_code == 404:
                out: dict[str, Any] = {"cards": [], "has_more": False, "total_cards": 0, "warnings": []}
                self.search_cache.put(key, out)
                return out
            raise ScryfallError("invalid", str(data.get("details") or "Scryfall could not read that search"))
        if data.get("object") != "list" or not isinstance(data.get("data"), list):
            raise ScryfallError("contract", "Scryfall search returned an unexpected body")
        total = data.get("total_cards")
        warnings = data.get("warnings")
        out = {
            "cards": [
                browse_row(c) for c in data["data"] if isinstance(c, dict) and c.get("object") == "card"
            ],
            "has_more": bool(data.get("has_more")),
            "total_cards": total if isinstance(total, int) else None,
            "warnings": [str(w) for w in warnings if isinstance(w, str)]
            if isinstance(warnings, list)
            else [],
        }
        self.search_cache.put(key, out)
        return out

    async def sets_list(self) -> list[dict[str, Any]]:
        """Every set (``GET /sets``, one request of about 1 MB), newest first as Scryfall lists them,
        cut to what the card search's set and block pickers show; kept a day."""
        if (hit := self.catalogs.get("sets")) is not None:
            return hit
        resp = await self._request("GET", "/sets", interval=self.lookup_interval)
        data = self._json(resp)
        rows = data.get("data")
        if data.get("object") != "list" or not isinstance(rows, list):
            raise ScryfallError("contract", "Scryfall's set list has an unexpected shape")
        out = [
            {
                "code": str(r["code"]).lower(),
                "name": str(r.get("name") or r["code"]),
                "type": str(r.get("set_type") or ""),
                "released": str(r.get("released_at") or ""),
                "block_code": str(r.get("block_code") or "").lower(),
                "block": str(r.get("block") or ""),
                "digital": bool(r.get("digital")),
            }
            for r in rows
            if isinstance(r, dict) and isinstance(r.get("code"), str) and r["code"]
        ]
        self.catalogs.put("sets", out)
        return out

    async def catalog(self, name: str) -> list[str]:
        """One of Scryfall's catalogs (``GET /catalog/{name}``, e.g. ``creature-types``); kept a day."""
        if not re.fullmatch(r"[a-z-]{3,40}", name):
            raise ScryfallError("invalid", "no such catalog")
        key = f"catalog:{name}"
        if (hit := self.catalogs.get(key)) is not None:
            return hit
        resp = await self._request("GET", f"/catalog/{name}", interval=self.lookup_interval)
        data = self._json(resp)
        values = data.get("data")
        if data.get("object") != "catalog" or not isinstance(values, list):
            raise ScryfallError("contract", f"Scryfall's {name} catalog has an unexpected shape")
        out = [v for v in values if isinstance(v, str) and v]
        self.catalogs.put(key, out)
        return out

    async def commander_names(self, query: str, *, limit: int = 10) -> list[str]:
        """Names of cards that can be a commander whose name contains ``query`` (Scryfall's
        ``is:commander name:...`` search), alphabetical, at most ``limit``. Empty when none match."""
        q = " ".join(query.replace('"', " ").split())[:120]
        if len(q) < 2:
            return []
        key = f"cmd:{q.lower()}"
        if (hit := self.names.get(key)) is not None:
            return hit[:limit]
        resp = await self._request(
            "GET",
            "/cards/search",
            params={"q": f'is:commander name:"{q}"', "order": "name", "unique": "cards"},
            interval=self.lookup_interval,
        )
        data = self._json(resp)
        if resp.status_code == 404 or data.get("object") == "error":
            out: list[str] = []
        elif data.get("object") != "list" or not isinstance(data.get("data"), list):
            raise ScryfallError("contract", "Scryfall search returned an unexpected body")
        else:
            out = [
                str(c["name"]) for c in data["data"] if isinstance(c, dict) and isinstance(c.get("name"), str)
            ]
        self.names.put(key, out)
        return out[:limit]

    async def collection(
        self, identifiers: list[dict[str, str]], *, interval: float | None = None
    ) -> tuple[list[dict[str, Any]], list[dict]]:
        """Resolve up to 75 identifiers per request. Returns (cards, not_found identifiers).
        ``interval`` spaces the requests further apart than the batch pacer (the daily price
        check uses the single-lookup pace: it is in no hurry)."""
        found: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for i in range(0, len(identifiers), COLLECTION_MAX):
            asked = identifiers[i : i + COLLECTION_MAX]
            # A two-part card goes by its front face (collection_ident); a miss is handed back as
            # the caller asked for it.
            chunk = [collection_ident(ident) for ident in asked]
            sent = {json.dumps(c, sort_keys=True): a for c, a in zip(chunk, asked, strict=True)}
            resp = await self._request(
                "POST", "/cards/collection", json={"identifiers": chunk}, interval=interval
            )
            data = self._json(resp)
            if data.get("object") != "list" or not isinstance(data.get("data"), list):
                raise ScryfallError("contract", str(data.get("details") or "unexpected collection reply"))
            for card in data["data"]:
                if isinstance(card, dict) and card.get("object") == "card":
                    found.append(card)
            for ident in data.get("not_found") or []:
                if isinstance(ident, dict):
                    missing.append(sent.get(json.dumps(ident, sort_keys=True), ident))
        return found, missing
