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
``MTG_SCRYFALL_LOOKUP_INTERVAL``) for ``named``, ``autocomplete`` and printing
lookups. After a 429 every call fails fast with ``rate_limited`` until the
Retry-After window has passed; ``retry_in()`` tells callers how long that is.
Responses are cached in memory for a day so a scan session of 100 cards costs
few requests.
"""

from __future__ import annotations

import asyncio
import logging
import math
import time
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
        # MTG_SCRYFALL_LOOKUP_INTERVAL, default 0.5 s); None means "same as min_interval".
        slow = min_interval if lookup_interval is None else lookup_interval
        self.lookup_interval = max(min_interval, slow)
        self._lock = asyncio.Lock()
        self._last = 0.0
        self._retry_until = 0.0
        self.cards = _Cache(ttl=24 * 3600, max_items=5000)
        self.names = _Cache(ttl=6 * 3600, max_items=5000)
        # Cards are kept slimmed (``slim``, under 1 KB each), so 5,000 entries stay a few MB.
        # Printing lists are summaries (about 2 KB a card in memory, up to 175 a card), in a small
        # cache of their own so a picker browsing many cards cannot crowd out names; 60 lists stay
        # under about 25 MB even when every one is a full page of printings.
        self.prints_cache = _Cache(ttl=3600, max_items=60)
        self.requests = 0

    async def aclose(self) -> None:
        if self._own_http:
            await self.http.aclose()

    def retry_in(self) -> int:
        """Whole seconds until Scryfall may be called again after a 429 (0 when not backing off)."""
        return max(0, math.ceil(self._retry_until - time.monotonic()))

    def _rate_limited(self) -> ScryfallError:
        n = self.retry_in()
        return ScryfallError("rate_limited", f"Scryfall asked us to slow down; try again in {n} s")

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
        data = self._json(resp)
        if resp.status_code == 404 or data.get("object") == "error":
            kind = "ambiguous" if data.get("type") == "ambiguous" else "not_found"
            raise ScryfallError(kind, str(data.get("details") or f"No card found for {what}"))
        if data.get("object") != "card" or not data.get("name"):
            raise ScryfallError("contract", "Scryfall returned something that is not a card")
        return data

    async def named(self, name: str, *, fuzzy: bool = False, set_code: str | None = None) -> dict[str, Any]:
        """One card by name. Exact by default; ``fuzzy=True`` tolerates typos and OCR noise."""
        key = f"named:{'f' if fuzzy else 'e'}:{(set_code or '').lower()}:{name.strip().lower()}"
        if (hit := self.cards.get(key)) is not None:
            return hit
        params = {"fuzzy" if fuzzy else "exact": name.strip()}
        if set_code:
            params["set"] = set_code.lower()
        resp = await self._request("GET", "/cards/named", params=params, interval=self.lookup_interval)
        card = slim(self._card_or_error(resp, name))
        self.cards.put(key, card)
        return card

    async def by_set_number(self, set_code: str, collector_number: str) -> dict[str, Any]:
        key = f"print:{set_code.lower()}:{collector_number.lower()}"
        if (hit := self.cards.get(key)) is not None:
            return hit
        resp = await self._request(
            "GET", f"/cards/{set_code.lower()}/{collector_number}", interval=self.lookup_interval
        )
        card = slim(self._card_or_error(resp, f"{set_code} {collector_number}"))
        self.cards.put(key, card)
        return card

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

    async def prints(self, oracle_id: str) -> dict[str, Any]:
        """Every printing of one card (the ``prints_search_uri`` query), newest first, summarized.

        Returns ``{"cards": [summaries], "has_more": bool, "total_cards": int}``. One page only
        (Scryfall pages at 175 cards); ``has_more`` says when a card has more printings than that,
        so a picker can say it shows the newest 175.
        """
        key = oracle_id.lower()
        if (hit := self.prints_cache.get(key)) is not None:
            return hit
        resp = await self._request(
            "GET",
            "/cards/search",
            params={"order": "released", "dir": "desc", "q": f"oracleid:{oracle_id}", "unique": "prints"},
            interval=self.lookup_interval,
        )
        data = self._json(resp)
        if resp.status_code == 404 or data.get("object") == "error":
            raise ScryfallError("not_found", str(data.get("details") or "no printings found"))
        if data.get("object") != "list" or not isinstance(data.get("data"), list):
            raise ScryfallError("contract", "Scryfall search returned an unexpected body")
        cards = [summarize(c) for c in data["data"] if isinstance(c, dict) and c.get("object") == "card"]
        total = data.get("total_cards")
        out = {
            "cards": cards,
            "has_more": bool(data.get("has_more")),
            "total_cards": total if isinstance(total, int) else len(cards),
        }
        self.prints_cache.put(key, out)
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

    async def collection(self, identifiers: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict]]:
        """Resolve up to 75 identifiers per request. Returns (cards, not_found identifiers)."""
        found: list[dict[str, Any]] = []
        missing: list[dict[str, Any]] = []
        for i in range(0, len(identifiers), COLLECTION_MAX):
            chunk = identifiers[i : i + COLLECTION_MAX]
            resp = await self._request("POST", "/cards/collection", json={"identifiers": chunk})
            data = self._json(resp)
            if data.get("object") != "list" or not isinstance(data.get("data"), list):
                raise ScryfallError("contract", str(data.get("details") or "unexpected collection reply"))
            for card in data["data"]:
                if isinstance(card, dict) and card.get("object") == "card":
                    found.append(card)
            for ident in data.get("not_found") or []:
                if isinstance(ident, dict):
                    missing.append(ident)
        return found, missing
