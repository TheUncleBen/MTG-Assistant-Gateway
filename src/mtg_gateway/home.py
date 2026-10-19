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
from starlette.responses import RedirectResponse, Response

from . import prefs
from .companion import DECK_CSP
from .deckpage import covers_for, deck_list_held_html, decks_unreadable_html, image_url, reread_href
from .decks import DeckError, is_busy
from .pages import _csrf, browser_session, login_redirect
from .theme import display_name, icon, render
from .waiting_strip import waiting_line

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


PENDING = "pending"  # recent_decks: the list is being read; the page shows a placeholder meanwhile


async def recent_decks(state: AppState, sub: str, *, wait: float | None) -> list[dict[str, Any]] | str | None:
    """The member's newest decks; None when they cannot be read right now; PENDING when the list
    is cold and the read has not answered within ``wait`` seconds (it carries on in the cache)."""
    try:
        rows = await asyncio.wait_for(state.decks.list_decks_quick(sub, wait=wait), DECKS_TIMEOUT)
    except (DeckError, TimeoutError) as exc:
        if isinstance(exc, DeckError) and not is_busy(exc):
            return None
        try:  # Archidekt can't be asked: the list as last read (its last_read says how old)
            rows = state.decks.last_deck_list(sub)
        except DeckError:
            return None
        if rows is None:
            return None
    except Exception:  # pragma: no cover - a surprise must not take the home page down
        log.exception("home: listing decks failed")
        return None
    if rows is None:
        return PENDING
    rows.sort(key=lambda d: d.get("updated_at") or "", reverse=True)
    return rows


def recent_panel_inner(
    decks: list[dict[str, Any]] | None,
    covers: dict[str, dict[str, Any]],
    *,
    unreadable: bool = False,
    last_read: float | None = None,
) -> str:
    """The inside of the home page's My decks panel: the newest covers, or why there are none.
    /api/decks/mine?shape=recent answers with the same markup for the placeholder to swap in.
    ``unreadable``: Archidekt left entries of the list unreadable, so the panel says so with the
    /decks notice and Try again, over the covers it did read, never "No decks yet". ``last_read``:
    the list is as last read (Archidekt can't be asked), and the panel says how old it is."""
    note = (
        decks_unreadable_html(some_shown=bool(decks), panel=False, again=reread_href("/"))
        if unreadable
        else ""
    ) + (deck_list_held_html(last_read) if last_read is not None else "")
    if decks:
        return (
            "<div class='panel-head'><h2>My decks</h2>"
            f"<a class='btn' href='/decks'>All decks ({len(decks)})</a></div>"
            + note
            + recent_html(decks, covers)
        )
    if note:  # nothing read: the notice, never "No decks yet"
        return "<div class='panel-head'><h2>My decks</h2></div>" + note
    if decks is None:
        return (
            "<h2>My decks</h2><p class='muted'>Archidekt did not answer in time. "
            "<a href='/decks'>Open the deck list</a> to try again.</p>"
        )
    return (
        "<h2>My decks</h2><p class='muted'>No decks yet. "
        "<a href='/decks/new'>Create one</a>, or <a href='/scan'>scan a pile of cards</a>.</p>"
    )


def recent_skeleton() -> str:
    """The panel while the deck list is still being read: six grey covers the script replaces."""
    return (
        "<div class='panel-head'><h2>My decks</h2><a class='btn' href='/decks'>All decks</a></div>"
        "<div class='recent skeleton' aria-hidden='true'>" + "<span></span>" * RECENT + "</div>"
        "<p class='sr-only' role='status'>Loading your decks</p>"
        "<noscript><p class='muted'><a href='/'>Reload</a> to see your decks (this page fills itself "
        "with scripts on).</p></noscript>"
    )


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
        # Try again on the notice that the deck list couldn't be read: read it again
        if request.query_params.get("reread"):
            if prefs.from_this_site(request):  # a link on another site only shows the page
                state.decks.reread_deck_list(sub)
            return RedirectResponse("/", 303)
        user = state.db.get_user(sub) or {}
        admin = bool(s.admin_group and s.admin_group in (user.get("groups") or []))
        who = user.get("preferred_username") or user.get("email") or "there"
        link = state.decks.status(sub)
        pending = state.db.count_pending_proposals(sub)
        scans = len(state.scan.list_sessions(sub)) if getattr(state, "scan", None) else 0

        decks: list[dict[str, Any]] | str | None = None
        if link["linked"]:
            decks = await recent_decks(state, sub, wait=state.decks.deck_list_wait)
        covers = (
            covers_for(decks, state.db.deck_covers([str(d["id"]) for d in decks]))
            if isinstance(decks, list) and decks
            else {}
        )

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
        if decks == PENDING:
            # Cold start: the page goes out now and decks.js fills the panel from the cache.
            recent = (
                "<section class='panel' data-decks-src='/api/decks/mine?shape=recent' aria-busy='true'>"
                + recent_skeleton()
                + "</section>"
            )
        elif link["linked"]:
            assert not isinstance(decks, str)
            unreadable = bool(getattr(decks, "unreadable", 0))  # the deck list couldn't be read, as on /decks
            recent = (
                "<section class='panel'>"
                + recent_panel_inner(
                    decks, covers, unreadable=unreadable, last_read=getattr(decks, "last_read", None)
                )
                + "</section>"
            )
        else:
            recent = ""
        search = (
            "<section class='panel'><h2>Find a deck</h2>"
            "<form method='get' action='/search' class='searchbox'>"
            "<div class='field'><label for='home-q'>Deck name</label>"
            "<input id='home-q' type='search' name='q' placeholder='Deck name'></div>"
            "<div class='field'><label for='home-c'>Commander</label>"
            "<input id='home-c' type='text' name='commander' data-suggest='cards' autocomplete='off' "
            "placeholder='Commander'></div>"
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
                    tile("/precons", "box", "Precons", "Every preconstructed deck Archidekt lists, by set."),
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
                        waiting_line(pending, state.db.pending_count_by_deck(sub))
                        if pending
                        else "Every change is reviewed.",
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
            f"</a><a class='btn' href='/install?for=skill'>{icon('report')} Assistant skill</a>"
            f"<a class='btn' href='/install?for=android'>{icon('download')} Android app</a></div></details>"
        )
        resp = render(
            "Home",
            hero + recent + search + tiles + connect,
            site=s.server_name,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=admin,
            user=display_name(user),
            wide=True,
            current="/",
            heading=False,
            body_class="home",
            # decks.js fills the recent-decks panel; Home has none of deck.js's hooks
            head_extra="<script src='/static/decks.js' defer></script>",
        )
        resp.headers["Content-Security-Policy"] = DECK_CSP  # deck covers come from Scryfall
        return resp
