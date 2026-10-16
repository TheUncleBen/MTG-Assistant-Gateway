"""Card search in the browser: ``/cards?q=`` lists the cards whose name matches, with picture,
mana cost and type, opens any of them in the shared card viewer (static/cardview.js) and adds
one to a deck of the member's from right there.

The page owns nothing new. The names come from the scan service's card-name catalog
(scan/names.py, the same list the typed suggestions use) and the summaries from its ``peek``
(one batched Scryfall request for the names not in the day-long card cache). The deck list is
the member cache the Decks page reads (``/api/decks/mine``), and the add itself is the deck
page's own save, ``POST /api/v1/decks/{id}/edit`` (api.py): a proposal applied at once with its
snapshot, the hand-edit confirmation rule, writes-off kept for review. A member without a linked
Archidekt account sees the action disabled with the reason; nothing here bypasses an approval.
The top bar's search box (theme.py, static/suggest.js in its site mode) brings people here.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import cardquery as cq
from . import cardsearch_view as cv
from . import prefs
from .pages import NO_STORE, _csrf, browser_session, login_redirect
from .scan.service import ScanError
from .theme import esc as _esc
from .theme import icon, render, stylesheet_link

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

logger = logging.getLogger(__name__)

CARDS_CSP = (
    "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "manifest-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
MAX_QUERY = 100
SEARCH_PAGES = 200  # result pages a search can be read to; past it there is no "more"


TEXT_TTL = 6 * 3600  # seconds a card's text is kept
TEXT_MAX = 500  # cards kept at most (each a few hundred bytes)
ABSENT_TTL = 1800  # seconds a name Scryfall does not know is remembered as unknown


def _face(f: dict[str, Any]) -> dict[str, str]:
    pt = f"{f.get('power')}/{f.get('toughness')}" if f.get("power") or f.get("toughness") else ""
    return {
        "name": str(f.get("name") or ""),
        "mana": str(f.get("mana_cost") or ""),
        "type": str(f.get("type_line") or ""),
        "text": str(f.get("oracle_text") or ""),
        "pt": pt,
        "loyalty": str(f.get("loyalty") or ""),
        "flavor": str(f.get("flavor_text") or ""),
    }


def _https_on(url: Any, hosts: tuple[str, ...]) -> str:
    """``url`` when it is an https link on one of ``hosts``, else "" (links from Scryfall's card
    data are shown as they are, but only to the sites the viewer names)."""
    if not isinstance(url, str) or len(url) > 500 or re.search(r"[\\@\s\x00-\x1f\x7f]", url):
        return ""  # a backslash or userinfo reads as another host in a browser than here
    parts = urlsplit(url)
    host = parts.hostname or ""
    return url if parts.scheme == "https" and host in hosts and parts.netloc == host else ""


def card_links(card: dict[str, Any]) -> dict[str, str]:
    """EDHREC and TCGplayer pages for a card, from the links in Scryfall's own card data:
    ``related_uris.edhrec`` as is, and the TCGplayer product page that Scryfall's partner link in
    ``purchase_uris.tcgplayer`` forwards to (its ``u`` parameter), so no referral link is shown."""
    related = card.get("related_uris") or {}
    buy = card.get("purchase_uris") or {}
    out: dict[str, str] = {}
    if edhrec := _https_on(related.get("edhrec"), ("edhrec.com",)):
        out["edhrec"] = edhrec
    partner = buy.get("tcgplayer")
    if isinstance(partner, str):
        target = (parse_qs(urlsplit(partner).query).get("u") or [""])[0]
        if tcg := _https_on(target, ("www.tcgplayer.com", "tcgplayer.com")):
            out["tcgplayer"] = tcg
    return out


def back_image(card: dict[str, Any]) -> str:
    """The back face's picture of a double-faced card (each face has its own picture), else ""."""
    faces = [f for f in card.get("card_faces") or [] if isinstance(f, dict)]
    if len(faces) < 2:
        return ""
    uris = faces[1].get("image_uris") or {}
    return _https_on(uris.get("normal"), ("cards.scryfall.io",))


def card_text(card: dict[str, Any]) -> dict[str, Any]:
    """What the card viewer shows beyond the picture, from a whole Scryfall card: the rules text
    (every face of a double-faced or split card), power and toughness or loyalty, flavour text,
    artist, price, EDHREC rank and the formats it is legal in (the viewer's own field names)."""
    faces = [_face(f) for f in card.get("card_faces") or [] if isinstance(f, dict)]
    own = _face(card)
    if len(faces) < 2 and faces:
        own = {**own, **{k: v for k, v in faces[0].items() if v}}
        faces = []
    legal = ",".join(sorted(k for k, v in (card.get("legalities") or {}).items() if v == "legal"))
    prices = card.get("prices") or {}
    rank = card.get("edhrec_rank")
    return {
        "name": str(card.get("name") or ""),
        "text": own["text"],
        "pt": own["pt"],
        "loyalty": own["loyalty"],
        "flavor": own["flavor"],
        "faces": faces,
        "artist": str(card.get("artist") or ""),
        "price": str(prices.get("usd") or ""),
        "rank": str(rank) if isinstance(rank, int) else "",
        "legal": legal,
        "gc": bool(card.get("game_changer")),
        "id": str(card.get("id") or ""),
        "back_img": back_image(card),
        "links": card_links(card),
    }


class TextCache:
    """Card text by name: a small LRU with a time to live, one Scryfall request per card however
    many viewers open it at once (waiters share the fetch in flight). Nothing persistent: the
    scan service's day-long card cache stays slim (no rules text) as before."""

    def __init__(self, *, ttl: float = TEXT_TTL, max_items: int = TEXT_MAX):
        self.ttl = ttl
        self.max_items = max_items
        self._items: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        self._inflight: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._absent: OrderedDict[str, tuple[float, str]] = OrderedDict()  # names Scryfall does not know

    @staticmethod
    def key(name: str) -> str:
        return " ".join(name.split()).casefold()

    def get(self, name: str) -> dict[str, Any] | None:
        k = self.key(name)
        hit = self._items.get(k)
        if hit is None:
            return None
        if hit[0] < time.monotonic():
            self._items.pop(k, None)
            return None
        self._items.move_to_end(k)
        return hit[1]

    def put(self, name: str, text: dict[str, Any]) -> None:
        k = self.key(name)
        self._items[k] = (time.monotonic() + self.ttl, text)
        self._items.move_to_end(k)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)

    def __len__(self) -> int:
        return len(self._items)

    def _missing(self, k: str) -> ScanError | None:
        hit = self._absent.get(k)
        if hit is None:
            return None
        if hit[0] < time.monotonic():
            self._absent.pop(k, None)
            return None
        return ScanError("not_found", hit[1])

    async def fetch(self, name: str, source: Any) -> dict[str, Any]:
        """The text for ``name``: from the cache, from a fetch already in flight for it, or from
        one new ``await source(name)`` (the whole card) that every concurrent caller shares. A
        name Scryfall does not know is remembered for a while too, so asking again costs no
        request (A15). When the caller whose fetch others are waiting on goes away (its page was
        closed), the fetch is cancelled with it; a waiter that is still wanted then starts its own
        instead of failing as if it had been cancelled itself (A16). Likewise a ``busy`` answer
        belongs to the caller whose lookup slot ran the fetch (its own other lookups were still
        running): a waiter, maybe another member, starts its own fetch in its own slot instead
        of taking that answer as its own."""
        k = self.key(name)
        while True:
            if (hit := self.get(name)) is not None:
                return hit
            if (missing := self._missing(k)) is not None:
                raise missing
            waiting = self._inflight.get(k)
            if waiting is None:
                break
            try:
                return await asyncio.shield(waiting)
            except ScanError as exc:
                if exc.kind == "busy":
                    continue  # the fetch's own caller was busy, not this one: ask in this one's slot
                raise
            except asyncio.CancelledError:
                task = asyncio.current_task()
                if waiting.cancelled() and not (task is not None and task.cancelling()):
                    continue  # the shared fetch went away with its caller; this one is still wanted
                raise
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._inflight[k] = fut
        try:
            text = card_text(await source(name))
            self.put(name, text)
            fut.set_result(text)
            return text
        except asyncio.CancelledError:
            fut.cancel()
            raise
        except BaseException as exc:
            if isinstance(exc, ScanError) and exc.kind == "not_found":
                self._absent[k] = (time.monotonic() + ABSENT_TTL, str(exc))
                while len(self._absent) > self.max_items:
                    self._absent.popitem(last=False)
            fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(k, None)
            if fut.done() and not fut.cancelled():
                fut.exception()  # mark it retrieved: the raiser reports it, waiters get their own


RULINGS_TTL = 24 * 3600  # rulings change rarely
RULINGS_MAX = 300


class RulingsCache:
    """Rulings by Scryfall card id: a small LRU with a time to live (each list is a few KB)."""

    def __init__(self, *, ttl: float = RULINGS_TTL, max_items: int = RULINGS_MAX):
        self.ttl = ttl
        self.max_items = max_items
        self._items: OrderedDict[str, tuple[float, list[dict[str, str]]]] = OrderedDict()

    def get(self, card_id: str) -> list[dict[str, str]] | None:
        hit = self._items.get(card_id)
        if hit is None or hit[0] < time.monotonic():
            self._items.pop(card_id, None)
            return None
        self._items.move_to_end(card_id)
        return hit[1]

    def put(self, card_id: str, rows: list[dict[str, str]]) -> None:
        self._items[card_id] = (time.monotonic() + self.ttl, rows)
        self._items.move_to_end(card_id)
        while len(self._items) > self.max_items:
            self._items.popitem(last=False)


def clean_query(raw: str | None) -> str:
    """The typed text, trimmed and capped (the catalog matches at least two characters)."""
    return " ".join((raw or "").split())[:MAX_QUERY]


START_HTML = (
    "<section class='panel'><h2>Find a card</h2><p class='muted'>Type part of a card name, or pick "
    "any filter: the cards that match show here as you go. Every field of Scryfall's advanced search "
    "is under Filters and More filters. Paper cards only, unless you pick Arena or Magic Online under "
    "Games.</p></section>"
)
NO_MATCH_HTML = (
    "<section class='panel'><p class='muted'>No card matches these filters. Check the spelling, or "
    "take a filter away.</p></section>"
)


def add_cardsearch_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings
    decks = state.decks
    texts = TextCache()
    state.card_texts = texts  # type: ignore[attr-defined]  # tests look at the cache

    @server.custom_route("/cards/api/text", methods=["GET"], include_in_schema=False)
    async def text_api(request: Request) -> Response:
        """The rules text and the rest the viewer shows for one card (``name=``, exact), read
        from Scryfall on demand through the paced client and kept for a while (TextCache)."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
        name = clean_query(request.query_params.get("name"))
        if len(name) < 2 or len(name) > MAX_QUERY:
            return JSONResponse(
                {"ok": False, "error": "invalid", "message": "name is required"}, 400, headers=NO_STORE
            )
        scan = state.scan
        if scan is None:
            return JSONResponse(
                {"ok": False, "error": "unavailable", "message": "Card lookup is switched off."},
                503,
                headers=NO_STORE,
            )

        async def source(n: str) -> dict[str, Any]:
            return await scan.named_raw(n, owner=sub)

        try:
            text = await texts.fetch(name, source)
        except ScanError as exc:
            status = {"not_found": 404, "rate_limited": 429, "busy": 429}.get(exc.kind, 503)
            return JSONResponse(
                {"ok": False, "error": exc.kind, "message": str(exc)}, status, headers=NO_STORE
            )
        return JSONResponse(
            {"ok": True, "card": text},
            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )

    rulings_cache = RulingsCache()
    state.card_rulings = rulings_cache  # type: ignore[attr-defined]  # tests look at the cache

    @server.custom_route("/cards/api/rulings", methods=["GET"], include_in_schema=False)
    async def rulings_api(request: Request) -> Response:
        """The rulings for one card (``name=``, exact), read from Scryfall when the viewer's Rulings
        button is pressed: the card's id comes from the text cache above, the rulings are kept a
        day."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
        name = clean_query(request.query_params.get("name"))
        if len(name) < 2:
            return JSONResponse(
                {"ok": False, "error": "invalid", "message": "name is required"}, 400, headers=NO_STORE
            )
        scan = state.scan
        if scan is None:
            return JSONResponse(
                {"ok": False, "error": "unavailable", "message": "Card lookup is switched off."},
                503,
                headers=NO_STORE,
            )

        async def source(n: str) -> dict[str, Any]:
            return await scan.named_raw(n, owner=sub)

        try:
            text = await texts.fetch(name, source)
            card_id = str(text.get("id") or "")
            rows = rulings_cache.get(card_id)
            if rows is None:
                rows = await scan.rulings(card_id, owner=sub)
                rulings_cache.put(card_id, rows)
        except ScanError as exc:
            status = {"not_found": 404, "rate_limited": 429, "busy": 429}.get(exc.kind, 503)
            return JSONResponse(
                {"ok": False, "error": exc.kind, "message": str(exc)}, status, headers=NO_STORE
            )
        return JSONResponse(
            {"ok": True, "name": text.get("name") or name, "rulings": rows},
            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )

    def page(title: str, body: str, *, sub: str, sid: str | None, status: int = 200) -> Response:
        user = state.db.get_user(sub) or {}
        admin = bool(s.admin_group and s.admin_group in (user.get("groups") or []))
        resp = render(
            title,
            body,
            site=s.server_name,
            status=status,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=admin,
            wide=True,
            current="/search",
            head_extra=(
                # deck.css carries the card viewer's own styles (the dialog, its bottom sheet on phones)
                f"{stylesheet_link('deck.css')}<style>{cv.CARDS_CSS}</style>"
                "<script src='/static/cardview.js' defer></script>"
                "<script src='/static/cardsearch.js' defer></script>"
            ),
        )
        resp.headers["Content-Security-Policy"] = CARDS_CSP
        return resp

    def problem_text(exc: ScanError) -> str:
        return {
            "rate_limited": "Scryfall asked us to wait a moment. Try again shortly.",
            "unavailable": "Scryfall is not answering right now. Try again in a moment.",
            "busy": "Your other card lookups are still running. Try again in a moment.",
            "invalid": f"Scryfall could not read that search: {exc}",
            "too_long": "This search is too long. Remove some filters or words.",
            "off": str(exc),
        }.get(exc.kind, f"The cards could not be looked up: {exc}")

    async def run_search(f: cq.Filters, page_no: int, sub: str) -> dict[str, Any]:
        scan = state.scan
        if scan is None:
            raise ScanError("off", "Card lookup is switched off on this gateway.")
        query = cq.scryfall_query(f)
        if len(query) > cq.MAX_QUERY:
            # refused whole: a cut search would lose its last filters (paper only among them)
            raise ScanError("too_long", f"the search is {len(query)} characters, over {cq.MAX_QUERY}")
        out = await scan.search_cards(query, order=f.order, direction=f.dir, page=page_no, owner=sub)
        if page_no >= SEARCH_PAGES:
            out = {**out, "has_more": False}  # the page number stops here, so no page follows
        return out

    def add_reason(sub: str) -> tuple[bool, str]:
        linked = decks.status(sub)["linked"]
        return (
            linked,
            "" if linked else "Link your Archidekt account on the Account page to add cards to a deck.",
        )

    def view_of(request: Request, sub: str) -> tuple[str, bool]:
        return prefs.view_choice(state.db, sub, request, "cards", cv.VIEWS, "grid")

    @server.custom_route("/cards/api/search", methods=["GET"], include_in_schema=False)
    async def search_api(request: Request) -> Response:
        """One page of results for the filters in the address, drawn as the page draws them
        (cardsearch_view.py): ``{ok, html, total, has_more, page, query}``. ``items_only`` (any
        page after the first) leaves out the list around the rows, so the page appends them."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
        f = cq.parse(request.query_params)
        view, remember = view_of(request, sub)
        try:
            page_no = max(1, min(int(request.query_params.get("page") or 1), SEARCH_PAGES))
        except ValueError:
            page_no = 1
        if not f.chosen():
            return JSONResponse(
                {"ok": True, "html": "", "total": 0, "has_more": False, "page": 1, "empty": True},
                headers=NO_STORE,
            )
        try:
            out = await run_search(f, page_no, sub)
        except ScanError as exc:
            status = {"rate_limited": 429, "busy": 429, "invalid": 400, "too_long": 400}.get(exc.kind, 503)
            return JSONResponse(
                {"ok": False, "error": exc.kind, "message": problem_text(exc)}, status, headers=NO_STORE
            )
        linked, reason = add_reason(sub)
        printing = f.shows_printings()
        if page_no > 1:
            html_out = cv.results_items_html(
                out["cards"], view=view, can_add=linked, reason=reason, printing=printing
            )
        else:
            html_out = (
                cv.results_list_html(
                    out["cards"], view=view, can_add=linked, reason=reason, printing=printing, linked=linked
                )
                if out["cards"]
                else NO_MATCH_HTML
            )
        resp = JSONResponse(
            {
                "ok": True,
                "html": html_out,
                "total": out.get("total_cards"),
                "has_more": out.get("has_more", False),
                "page": page_no,
                "count": len(out["cards"]),
                "warnings": out.get("warnings", []),
                "query": cq.scryfall_query(f),
            },
            headers=NO_STORE,
        )
        return (
            prefs.remember_view(
                state.db, sub, resp, "cards", view, secure=s.public_url.startswith("https://")
            )
            if remember
            else resp
        )

    @server.custom_route("/cards/api/options", methods=["GET"], include_in_schema=False)
    async def options_api(request: Request) -> Response:
        """The pickers' lists (sets, blocks, types from Scryfall, a day at a time; the criteria
        are Scryfall's fixed list), read when a picker is first opened."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
        scan = state.scan
        if scan is None:
            return JSONResponse(
                {"ok": False, "error": "unavailable", "message": "Card lookup is switched off."},
                503,
                headers=NO_STORE,
            )
        try:
            out = await scan.search_options(owner=sub)
        except ScanError as exc:
            status = {"rate_limited": 429, "busy": 429}.get(exc.kind, 503)
            return JSONResponse(
                {"ok": False, "error": exc.kind, "message": problem_text(exc)}, status, headers=NO_STORE
            )
        out["criteria"] = [{"code": v, "name": label} for v, label in cq.CRITERIA]
        cache = "no-store" if out.get("partial") else "private, max-age=3600"
        return JSONResponse(
            {"ok": True, **out}, headers={"Cache-Control": cache, "X-Content-Type-Options": "nosniff"}
        )

    @server.custom_route("/cards", methods=["GET"], include_in_schema=False)
    async def cards_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/cards" + (f"?{request.url.query}" if request.url.query else ""))
        f = cq.parse(request.query_params)
        view, remember = view_of(request, sub)
        linked, reason = add_reason(sub)
        body = f"<section class='panel cs-panel'>{cv.filters_html(f, view=view)}"
        if not linked:
            body += (
                f"<p class='readonly'>{icon('link')}<span>Cards can be read but not added: no Archidekt "
                "account is linked.</span><a href='/account'>Link it on the Account page</a></p>"
            )
        body += "</section>"
        results = ""
        total: int | None = None
        has_more = False
        status_html = ""
        if f.chosen():
            try:
                out = await run_search(f, 1, sub)
            except ScanError as exc:
                status_html = f"<p class='notice error'>{_esc(problem_text(exc))}</p>"
            else:
                total = out.get("total_cards")
                has_more = bool(out.get("has_more"))
                results = (
                    cv.results_list_html(
                        out["cards"],
                        view=view,
                        can_add=linked,
                        reason=reason,
                        printing=f.shows_printings(),
                        linked=linked,
                    )
                    if out["cards"]
                    else NO_MATCH_HTML
                )
        else:
            results = START_HTML
        more = (
            "<div class='cs-more-wrap' id='cs-more'"
            + ("" if has_more else " hidden")
            + "><button type='button' class='btn' data-more>Load more cards</button></div>"
        )
        body += (
            "<section class='cs-out'>"
            + cv.results_head_html(f, view=view, total=total, shown=f.chosen())
            + f"<div id='cs-status' class='cs-status' role='status'>{status_html}</div>"
            + f"<div id='cs-results' class='cs-results' data-linked='{'1' if linked else ''}'>{results}</div>"
            + more
            + f"<template id='cs-start'>{START_HTML}</template>"
            + "</section>"
        )
        resp = page("Cards", body, sub=sub, sid=sid)
        return (
            prefs.remember_view(
                state.db, sub, resp, "cards", view, secure=s.public_url.startswith("https://")
            )
            if remember
            else resp
        )
