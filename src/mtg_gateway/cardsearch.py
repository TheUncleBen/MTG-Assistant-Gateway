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
import html
import logging
import time
from collections import OrderedDict
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .deckpage import DECK_CSS, mana_html
from .pages import _csrf, browser_session, login_redirect
from .scan.service import ScanError
from .theme import icon, render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

logger = logging.getLogger(__name__)

CARDS_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; "
    "manifest-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
MAX_QUERY = 100
CARDS_CSS = """
.cardsearch form.row{display:flex;flex-wrap:wrap;align-items:end;gap:.75rem 1rem;margin:0}
.cardsearch form.row .field{margin:0;flex:1 1 18rem;min-width:0}
.cardsearch form.row .search{margin:0}
.cardsearch form.row .search .suggest{flex:1 1 auto;min-width:0}
.cardsearch form.row .search .suggest > input{border-radius:var(--radius) 0 0 var(--radius);border-right:0}
.cardsearch form.row .search button{height:var(--ctl);min-height:var(--ctl);padding:0 .9rem}
.cardsearch form.row > .btn{margin:0;height:var(--ctl);white-space:nowrap}
.cardsearch .readonly{margin:.9rem 0 0;display:flex;flex-wrap:wrap;align-items:center;gap:.4rem .75rem;
  color:var(--text-muted);font-size:.93rem}
.cardsearch .readonly a{font-weight:700}
@media (max-width:600px){ .cardsearch form.row > .btn{width:100%} }
.cardgrid{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(100%,10.5rem),1fr));gap:1rem .9rem;
  list-style:none;margin:0 0 1rem;padding:0}
.cardgrid .cc{display:flex;flex-direction:column;gap:.5rem;min-width:0}
.cardgrid .pic{all:unset;display:block;cursor:pointer;position:relative;aspect-ratio:5/7;border-radius:4.5%;
  overflow:hidden;background:var(--surface-2);border:2px solid var(--card-border);box-sizing:border-box}
.cardgrid .pic:focus-visible{outline:3px solid var(--orange);outline-offset:2px}
.cardgrid .pic img{width:100%;height:100%;display:block;object-fit:cover}
.cardgrid .pic .ph{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;
  padding:.75rem;text-align:center;font-weight:700;color:var(--text-muted);overflow-wrap:anywhere}
.cardgrid .meta{display:flex;flex-direction:column;gap:.15rem;min-width:0}
.cardgrid .nm{display:flex;flex-wrap:wrap;align-items:center;gap:.2rem .4rem;font-weight:700;line-height:1.25;
  overflow-wrap:anywhere;min-width:0}
.cardgrid .ty{font-size:.86rem;color:var(--text-muted);overflow-wrap:anywhere}
.cardgrid .add{margin:0;width:100%;min-width:0}
.cardgrid .add > span{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
/* Add to deck: a small dialog over the page (a bottom sheet on phones), themed like the card viewer */
.addsheet{position:fixed;inset:0;z-index:70;display:none;align-items:center;justify-content:center;
  background:var(--scrim-strong,rgba(0,0,0,.72));padding:1rem}
.addsheet.open{display:flex}
.addsheet .box{width:min(100%,27rem);max-height:100%;overflow:auto;overscroll-behavior:contain;
  background:var(--surface);color:var(--text);border:1px solid var(--border);
  border-radius:var(--radius-panel);padding:1.25rem;box-shadow:0 12px 40px rgba(0,0,0,.5);
  display:flex;flex-direction:column;gap:.9rem}
.addsheet .head{display:flex;align-items:flex-start;justify-content:space-between;gap:.75rem;min-width:0}
.addsheet h3{margin:0;font-size:1.25rem;line-height:1.25;overflow-wrap:anywhere;min-width:0;display:flex;
  flex-wrap:wrap;align-items:center;gap:.4rem .5rem}
.addsheet .close{flex:none;margin:0;width:2.4rem;min-height:2.4rem;height:2.4rem;font-size:1.5rem;
  line-height:1;font-weight:400;border-radius:50%}
.addsheet .field{margin:0}
.addsheet .field > label{margin:0}
.addsheet .two{display:grid;grid-template-columns:6rem minmax(0,1fr);gap:.9rem}
.addsheet .msg{margin:0;font-size:.93rem;color:var(--text-muted);min-height:1.2em}
.addsheet .msg.err{color:var(--red-text);font-weight:700}
.addsheet .acts{display:flex;flex-wrap:wrap;justify-content:flex-end;gap:.5rem;margin-top:.25rem}
.addsheet .acts button{margin:0}
@media (max-width:600px){
  .addsheet{padding:0;align-items:flex-end}
  .addsheet .box{width:100%;max-height:94vh;max-height:94dvh;border-bottom:0;
    border-radius:var(--radius-panel) var(--radius-panel) 0 0;
    padding-bottom:calc(1.25rem + env(safe-area-inset-bottom))}
  .addsheet .acts button{flex:1 1 auto} }
.toast.cards-toast{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem .75rem;z-index:75}
.toast.cards-toast .msg{flex:1 1 12rem;min-width:0;overflow-wrap:anywhere}
.toast.cards-toast .btn,.toast.cards-toast button{margin:0}
.toast.cards-toast .close{width:2rem;min-height:2rem;height:2rem;padding:0;border-radius:50%;font-size:1.2rem}
"""


TEXT_TTL = 6 * 3600  # seconds a card's text is kept
TEXT_MAX = 500  # cards kept at most (each a few hundred bytes)
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


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

    async def fetch(self, name: str, source: Any) -> dict[str, Any]:
        """The text for ``name``: from the cache, from a fetch already in flight for it, or from
        one new ``await source(name)`` (the whole card) that every concurrent caller shares."""
        if (hit := self.get(name)) is not None:
            return hit
        k = self.key(name)
        if (waiting := self._inflight.get(k)) is not None:
            return await asyncio.shield(waiting)
        fut: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._inflight[k] = fut
        try:
            text = card_text(await source(name))
            self.put(name, text)
            fut.set_result(text)
            return text
        except BaseException as exc:
            fut.set_exception(exc)
            raise
        finally:
            self._inflight.pop(k, None)
            if fut.done() and not fut.cancelled():
                fut.exception()  # mark it retrieved: the raiser reports it, waiters get their own


def clean_query(raw: str | None) -> str:
    """The typed text, trimmed and capped (the catalog matches at least two characters)."""
    return " ".join((raw or "").split())[:MAX_QUERY]


def card_tile_html(card: dict[str, Any], *, can_add: bool, reason: str) -> str:
    """One result: the picture (a button that opens the card viewer, carrying the data attributes
    cardview.js reads), name with mana pips, type line and the Add to deck button."""
    name = str(card.get("name") or "")
    img = card.get("image_normal") or card.get("image_small") or ""
    set_code = str(card.get("set") or "").upper()
    printing = f"{set_code} {card.get('collector_number') or ''}".strip()
    legal = "commander" if card.get("legal_commander") == "legal" else ""
    attrs = (
        f" data-card='{_esc(name)}'"
        + (f" data-img='{_esc(img)}'" if img else "")
        + f" data-set='{_esc(printing)}'"
        f" data-type='{_esc(card.get('type_line') or '')}'"
        f" data-mana='{_esc(card.get('mana_cost') or '')}'"
        + (f" data-rarity='{_esc(card['rarity'])}'" if card.get("rarity") else "")
        + (f" data-legal='{legal}'" if legal else "")
        + (f" data-scry='{_esc(card['scryfall_uri'])}'" if card.get("scryfall_uri") else "")
    )
    body = (
        f"<img src='{_esc(img)}' alt='' loading='lazy' decoding='async'>"
        if img
        else f"<span class='ph'>{_esc(name)}</span>"
    )
    add = (
        f"<button type='button' class='btn add' data-add='{_esc(name)}'"
        + ("" if can_add else f" disabled title='{_esc(reason)}'")
        + f">{icon('plus')}<span>Add to deck</span></button>"
    )
    return (
        f"<li class='cc'><button type='button' class='pic'{attrs} aria-label='{_esc(name)}: show the card'>"
        f"{body}</button><div class='meta'><span class='nm'><span>{_esc(name)}</span>"
        f"{mana_html(str(card.get('mana_cost') or ''))}</span>"
        f"<span class='ty'>{_esc(card.get('type_line') or '')}</span></div>{add}</li>"
    )


def search_box_html(q: str) -> str:
    deck_link = "/search" + ("?" + urlencode({"name": q}) if q else "")
    return (
        "<form method='get' action='/cards' class='row' role='search'>"
        "<div class='field'><label for='c-q'>Card name</label><span class='search'>"
        f"<input id='c-q' type='search' name='q' value='{_esc(q)}' maxlength='{MAX_QUERY}' "
        "placeholder='Any part of a card name' data-suggest='cards' data-suggest-rich "
        "data-suggest-submit autocomplete='off' enterkeyhint='search'>"
        f"<button type='submit' aria-label='Search cards'>{icon('search')}</button></span></div>"
        f"<a class='btn' href='{deck_link}'>{icon('decks')} Search decks instead</a></form>"
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
            scripts=True,
            head_extra=(
                # DECK_CSS carries the card viewer's own styles (the dialog, its bottom sheet on phones)
                f"<style>{DECK_CSS}{CARDS_CSS}</style><script src='/static/cardview.js' defer></script>"
                "<script src='/static/cardsearch.js' defer></script>"
            ),
        )
        resp.headers["Content-Security-Policy"] = CARDS_CSP
        return resp

    @server.custom_route("/cards", methods=["GET"], include_in_schema=False)
    async def cards_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/cards" + (f"?{request.url.query}" if request.url.query else ""))
        q = clean_query(request.query_params.get("q"))
        linked = decks.status(sub)["linked"]
        reason = "" if linked else "Link your Archidekt account on the Account page to add cards to a deck."
        body = f"<section class='panel cardsearch'>{search_box_html(q)}"
        if not linked:
            body += (
                f"<p class='readonly'>{icon('link')}<span>Cards can be read but not added: no Archidekt "
                "account is linked.</span><a href='/account'>Link it on the Account page</a></p>"
            )
        body += "</section>"
        if len(q) < 2:
            body += (
                "<section class='panel'><h2>Find a card</h2><p class='muted'>Type part of a card name: "
                "the gateway matches it against every card Scryfall knows, shows the picture, cost and "
                "type of each match, and adds one to any of your decks in a tap. The box at the top of "
                "every page does the same, and finds decks too.</p></section>"
            )
            return page("Cards", body, sub=sub, sid=sid)
        scan = state.scan
        cards: list[dict[str, Any]] = []
        problem = ""
        if scan is None:
            problem = "Card lookup is switched off on this gateway."
        else:
            try:
                names = await scan.suggest(q, owner=sub)
                cards = await scan.peek(names, owner=sub) if names else []
            except ScanError as exc:
                problem = {
                    "rate_limited": "Scryfall asked us to wait a moment. Try again shortly.",
                    "unavailable": "Scryfall is not answering right now. Try again in a moment.",
                }.get(exc.kind, f"The cards could not be looked up: {exc}")
        count = len(cards)
        body += (
            f"<div class='results-head'><h2>Cards matching “{_esc(q)}”</h2>"
            f"<p class='muted'>{count} card{'' if count == 1 else 's'}</p></div>"
        )
        if problem:
            body += f"<p class='notice error'>{_esc(problem)}</p>"
        elif not cards:
            body += (
                "<section class='panel'><p class='muted'>No card is named like that. Check the spelling, or "
                "type fewer letters.</p></section>"
            )
        else:
            body += (
                f"<ul class='cardgrid' id='cardgrid'{' data-linked' if linked else ''}>"
                + "".join(card_tile_html(c, can_add=linked, reason=reason) for c in cards)
                + "</ul>"
            )
        return page("Cards", body, sub=sub, sid=sid)
