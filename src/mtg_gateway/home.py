"""The signed-in home page: a dashboard in the shape of Archidekt's landing page.

Every section of the gateway is one tile (decks with their newest covers, deck search, scanning,
the collection on the member's Archidekt account, proposals, history and the guide), the top
navigation stays visible like on every other page, and the assistant connection details sit in a
collapsed panel at the end, where a member who only uses the pages never has to see them. The page
is for signed-in members only; MCP clients use /mcp and the OAuth routes.
"""

from __future__ import annotations

import asyncio
import html
import logging
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import Response

from .companion import DECK_CSP
from .deckpage import image_url
from .decks import DeckError
from .pages import _csrf, browser_session, login_redirect
from .theme import icon, render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

log = logging.getLogger(__name__)
_esc = html.escape
RECENT = 6
DECKS_TIMEOUT = 6.0  # seconds; the home page must not hang on Archidekt


def tile(
    href: str, ic: str, title: str, detail: str, *, number: int | str | None = None, cta: bool = False
) -> str:
    n = f"<span class='n'>{_esc(str(number))}</span>" if number is not None else ""
    return (
        f"<a class='tile{' cta' if cta else ''}' href='{href}'>"
        f"<span class='ti'>{icon(ic)}{_esc(title)}</span>{n}<span class='d'>{_esc(detail)}</span></a>"
    )


async def recent_decks(state: AppState, sub: str) -> list[dict[str, Any]] | None:
    """The member's newest decks, or None when they cannot be read right now."""
    try:
        rows = await asyncio.wait_for(state.decks.list_decks(sub), DECKS_TIMEOUT)
    except (DeckError, TimeoutError):
        return None
    except Exception:  # pragma: no cover - a surprise must not take the home page down
        log.exception("home: listing decks failed")
        return None
    rows.sort(key=lambda d: d.get("updated_at") or "", reverse=True)
    return rows


def recent_html(decks: list[dict[str, Any]], covers: dict[str, dict[str, Any]]) -> str:
    out = []
    for d in decks[:RECENT]:
        did = _esc(str(d["id"]))
        art = image_url((covers.get(str(d["id"])) or {}).get("scryfall_uid"), "art_crop")
        style = f" style=\"background-image:url('{_esc(art)}')\"" if art else ""
        out.append(f"<a href='/decks/{did}'{style}><span>{_esc(d.get('name') or 'Deck')}</span></a>")
    return "<div class='recent'>" + "".join(out) + "</div>"


def add_home_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings

    @server.custom_route("/", methods=["GET"], include_in_schema=False)
    async def index(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if sub is None:
            return login_redirect("/")
        user = state.db.get_user(sub) or {}
        admin = bool(s.admin_group and s.admin_group in (user.get("groups") or []))
        who = user.get("preferred_username") or user.get("email") or "there"
        link = state.decks.status(sub)
        pending = state.db.count_pending_proposals(sub)
        scans = len(state.scan.list_sessions(sub)) if getattr(state, "scan", None) else 0

        decks: list[dict[str, Any]] | None = None
        if link["linked"]:
            decks = await recent_decks(state, sub)
        covers = state.db.deck_covers([str(d["id"]) for d in decks]) if decks else {}

        hero = (
            f"<div class='hero'><div><h1>Hi {_esc(who)}</h1>"
            + (
                "<p class='sub'>Archidekt account "
                f"<strong>{_esc(link['archidekt_username'] or '')}</strong> is linked.</p>"
                if link["linked"]
                else "<p class='sub'>Link your Archidekt account to see and edit your decks here.</p>"
            )
            + "</div>"
            + (
                f"<a class='btn btn-primary' href='/account'>{icon('account')} Link Archidekt</a>"
                if not link["linked"]
                else f"<a class='btn btn-primary' href='/decks/new'>{icon('plus')} New deck</a>"
            )
            + "</div>"
        )
        if decks:
            recent = (
                "<section class='panel'><div class='panel-head'><h2>My decks</h2>"
                f"<a class='btn' href='/decks'>All decks ({len(decks)})</a></div>"
                + recent_html(decks, covers)
                + "</section>"
            )
        elif link["linked"] and decks is None:
            recent = (
                "<section class='panel'><h2>My decks</h2><p class='muted'>Archidekt did not answer in time. "
                "<a href='/decks'>Open the deck list</a> to try again.</p></section>"
            )
        elif link["linked"]:
            recent = (
                "<section class='panel'><h2>My decks</h2><p class='muted'>No decks yet. "
                "<a href='/decks/new'>Create one</a>, or <a href='/scan'>scan a pile of cards</a>.</p>"
                "</section>"
            )
        else:
            recent = ""
        search = (
            "<section class='panel'><h2>Find a deck</h2>"
            "<form method='get' action='/search' class='searchbox'>"
            "<div class='field'><label for='home-q'>Deck name</label>"
            "<input id='home-q' type='search' name='q' placeholder='Deck name'></div>"
            "<div class='field'><label for='home-c'>Commander</label>"
            "<input id='home-c' type='text' name='commander' list='cardnames' autocomplete='off' "
            "placeholder='Commander'><datalist id='cardnames'></datalist></div>"
            f"<button class='btn-primary'>{icon('search')} Search Archidekt</button></form></section>"
        )
        tiles = (
            "<div class='tiles'>"
            + "".join(
                [
                    tile(
                        "/decks",
                        "decks",
                        "Decks",
                        "Your decks as stacks, grid or text, with stats and edits.",
                    ),
                    tile("/search", "search", "Search", "Public decks by name, commander, format or owner."),
                    tile(
                        "/scan",
                        "camera",
                        "Scan",
                        "Point your phone at cards to build a list, a deck or your collection.",
                        number=scans or None,
                    ),
                    tile(
                        "/collection",
                        "collection",
                        "Collection",
                        "The cards you own, on your Archidekt account; they get a green dot on every deck.",
                    ),
                    tile(
                        "/proposals",
                        "proposals",
                        "Proposals",
                        "Deck changes waiting for your approval." if pending else "Every change is reviewed.",
                        number=pending,
                        cta=bool(pending),
                    ),
                    tile("/history", "history", "History", "Snapshots, applied changes and deck reports."),
                    tile("/guide", "guide", "Guide", "What you can do here, with or without an assistant."),
                ]
                + (
                    [tile("/admin", "settings", "Admin", "Members, activity and the system.")]
                    if admin
                    else []
                )
            )
            + "</div>"
        )
        connect = (
            "<details class='connect panel'><summary>Connect an AI assistant "
            f"<span class='muted addr'>{_esc(s.mcp_url)}</span></summary>"
            "<p>Claude and ChatGPT talk to this gateway through its connector URL. Add it as a custom "
            "connector and sign in when asked; the assistant can then read your decks, research cards and "
            "propose edits you approve here.</p>"
            f"<pre>{_esc(s.mcp_url)}</pre>"
            f"<div class='actions'><a class='btn btn-primary' href='/install'>{icon('download')} Guided setup"
            f"</a><a class='btn' href='/skill'>{icon('report')} Assistant skill</a>"
            f"<a class='btn' href='/app'>{icon('download')} Android app</a></div></details>"
        )
        resp = render(
            "Home",
            hero + recent + search + tiles + connect,
            site=s.server_name,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=admin,
            wide=True,
            scripts=True,
            current="/",
            heading=False,
            body_class="home",
            head_extra="<script src='/static/deck.js' defer></script>",
        )
        resp.headers["Content-Security-Policy"] = DECK_CSP  # deck covers come from Scryfall
        return resp
