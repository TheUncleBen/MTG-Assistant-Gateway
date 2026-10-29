"""The signed-in home page: your recent decks and what needs you (decision C8, 0.7.46).

Under the greeting and New deck come the things waiting for the member, each only when there
is one: proposals waiting for their press, Forge tests running, Archidekt alerts (the bell's
count, read after the page has loaded by static/bell.js) and watched cards past a price alert.
Then the newest decks as covers (more of them than before; a row to swipe on a phone), and the
assistant connection details only while no assistant is connected. The navigation stays in the
bars, so the page repeats none of it. The page is for signed-in members only; MCP clients use
/mcp and the OAuth routes.
"""

from __future__ import annotations

import asyncio
import html
import logging
import time
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from . import prefs, ui
from .companion import DECK_CSP
from .companion_decks import NEW_DECK_CSS, NEW_DECK_SCRIPTS, new_deck_box_html
from .deckpage import covers_for, deck_list_held_html, decks_unreadable_html, image_url, reread_href
from .decks import DeckError, is_busy
from .pages import _csrf, browser_session, login_redirect
from .theme import icon, render
from .watchlist_page import due_line

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

log = logging.getLogger(__name__)
_esc = html.escape
RECENT = 8
DECKS_TIMEOUT = 6.0  # seconds; the home page must not hang on Archidekt


HOME_CSS = """
/* Needs you: one row a thing waiting for the member, the way to it at the row's end */
.home .needs{margin:0 0 1rem}
.home .needs h2{margin:0 0 .5rem}
.home .needs ul{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:.5rem}
.home .needs li{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem 1rem;padding:.6rem .9rem;
  border-radius:var(--radius);background:var(--surface);border:1px solid var(--border)}
.home .needs li.accent{border-left:4px solid var(--orange)}
.home .needs li svg.i{width:20px;height:20px;flex:none;color:var(--orange-text)}
.home .needs li .what{flex:1 1 14rem;min-width:0}
.home .needs li .what b{font-variant-numeric:tabular-nums}
.home .needs li .what small{display:block;color:var(--text-muted);overflow-wrap:anywhere}
.home .needs li .go{margin:0;flex:none}
.home .needs li[hidden]{display:none}
.home .list-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:.25rem 1rem;margin:1.5rem 0 .75rem}
.home .list-head h2{margin:0}
/* phones: the covers as one row to swipe, each cover a screen's half */
@media (max-width:599.98px){
  .home .recent{display:flex;overflow-x:auto;scroll-snap-type:x proximity;gap:.6rem;
    padding:0 0 .35rem;scrollbar-width:none}
  .home .recent::-webkit-scrollbar{display:none}
  .home .recent>*{flex:0 0 min(11rem,58vw);scroll-snap-align:start}
}
"""


def _n(n: int, one: str, many: str) -> str:
    return f"{n:,} {one if n == 1 else many}"


def _span(seconds: float) -> str:
    """How long, in the Proposals page's words: "5 min", "20 h", "6 days"."""
    seconds = max(0, int(seconds))
    if seconds < 3600:
        return f"{max(1, seconds // 60)} min"
    if seconds < 2 * 86400:
        return f"{seconds // 3600} h"
    days = seconds // 86400
    return f"{days} days"


def proposals_line(rows: list[dict[str, Any]], *, now: float) -> str:
    """The proposals row's line, the words of the Proposals page's "Waiting for you" widget:
    "6 on 4 decks, 2 not tied to a deck · oldest 6 days · next expires in 20 h · 1 needs a look"."""
    if not rows:
        return "Nothing is waiting."
    tied = [r for r in rows if str(r.get("deck_id") or "").isdigit()]
    decks = len({str(r["deck_id"]) for r in tied})
    loose = len(rows) - len(tied)
    if tied:
        first = f"{len(tied)} on {decks} deck{'' if decks == 1 else 's'}"
        if loose:
            first += f", {loose} not tied to a deck"
    else:
        first = f"{loose} not tied to a deck"
    parts = [first]
    made = [r["created_at"] for r in rows if r.get("created_at")]
    if made:
        parts.append(f"oldest {_span(now - min(made))}")
    ends = [r["expires_at"] for r in rows if r.get("expires_at")]
    if ends:
        parts.append(f"next expires in {_span(min(ends) - now)}")
    look = sum(1 for r in rows if r.get("status") == "needs_look")
    if look:
        parts.append(f"{look} need{'s' if look == 1 else ''} a look")
    return " · ".join(parts)


def need_row(ic: str, text: str, detail: str, button: str, *, accent: bool = False, attrs: str = "") -> str:
    """One Needs you row: an icon, what waits (``text`` with its count in bold, ``detail`` the
    small line under it) and the button that goes there."""
    det = f"<small>{_esc(detail)}</small>" if detail else ""
    return (
        f"<li{' class=accent' if accent else ''}{attrs}>{icon(ic)}<span class='what'>{text}{det}</span>"
        f"<span class='go'>{button}</span></li>"
    )


def needs_you_html(
    *,
    pending: int,
    board: list[dict[str, Any]],
    forge_running: int,
    watch_hits: list[str],
    linked: bool,
    now: float,
) -> str:
    """Needs you: the rows for what waits, each only when there is something; nothing at all
    when nothing waits and no alert can come (the bell's row needs a linked account). With a
    link, Review it (them) is the page's orange button; without one, Link Archidekt keeps it."""
    rows = []
    if pending:
        rows.append(
            need_row(
                "proposals",
                f"<b>{_n(pending, 'proposal', 'proposals')}</b> waiting for you",
                proposals_line(board, now=now),
                ui.button_link(
                    "Review it" if pending == 1 else "Review them",
                    "/proposals",
                    kind="primary" if linked else "plain",
                ),
                accent=True,
            )
        )
    if forge_running:
        rows.append(
            need_row(
                "report",
                f"<b>{_n(forge_running, 'Forge test', 'Forge tests')}</b> running",
                "The report lands under Reports when the games are done.",
                ui.button_link("Open reports", "/reports"),
            )
        )
    if linked:
        # Archidekt's unseen alerts: the count comes after the page has loaded (static/bell.js
        # fills data-bell-n and shows the row when the count is above zero)
        rows.append(
            need_row(
                "bell",
                "<b><span data-bell-n>0</span> new <span data-bell-word>alerts</span></b> on Archidekt",
                "",
                f"<a class='btn btn-plain' href='/notifications' data-bell-open>{icon('bell')} "
                "Open alerts</a>",
                attrs=" data-bell-row hidden",
            )
        )
    if watch_hits:
        rows.append(
            need_row(
                "stats",
                f"<b>{_n(len(watch_hits), 'watched card', 'watched cards')}</b> past a price alert",
                " · ".join(watch_hits[:2]),
                ui.button_link("Open watchlist", "/watchlist"),
            )
        )
    if not rows:
        return ""
    return (
        "<section class='needs' aria-labelledby='needs-h'><h2 id='needs-h'>Needs you</h2>"
        f"<ul>{''.join(rows)}</ul></section>"
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
        "<a href='/decks/new' data-box-open='newdeck'>Create one</a>, or <a href='/scan'>scan a pile of "
        "cards</a>.</p>"
    )


def recent_skeleton() -> str:
    """The panel while the deck list is still being read: grey covers the script replaces."""
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
        name = _esc(d.get("name") or "Unnamed deck")
        out.append(
            f"<a href='/decks/{did}' data-deck-id='{did}' data-deck-name='{name}'{style}>"
            f"<span>{name}</span></a>"
        )
    return "<div class='recent'>" + "".join(out) + "</div>"


def connect_html(mcp_url: str) -> str:
    """Connect an AI assistant: the connector address and the ways in, shown only while no
    assistant is connected (afterwards the Account page has it)."""
    return (
        "<details class='connect panel'><summary>Connect an AI assistant "
        f"<span class='muted addr'>{_esc(mcp_url)}</span></summary>"
        "<p>Claude and ChatGPT talk to this gateway through its connector URL. Add it as a custom "
        "connector and sign in when asked; the assistant can then read your decks, research cards and "
        "propose edits you approve here.</p>"
        f"<pre>{_esc(mcp_url)}</pre>"
        f"<div class='actions'><a class='btn' href='/install'>{icon('download')} Guided setup"
        f"</a><a class='btn' href='/install?for=skill'>{icon('report')} Assistant skill</a>"
        f"<a class='btn' href='/install?for=android'>{icon('download')} Android app</a></div></details>"
    )


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
        # the name the sign-in provider gives ("Alice"), else the username
        who = user.get("name") or user.get("preferred_username") or user.get("email") or "there"
        link = state.decks.status(sub)
        pending = state.db.count_pending_proposals(sub)

        decks: list[dict[str, Any]] | str | None = None
        if link["linked"]:
            decks = await recent_decks(state, sub, wait=state.decks.deck_list_wait)
        covers = (
            covers_for(decks, state.db.deck_covers([str(d["id"]) for d in decks]))
            if isinstance(decks, list) and decks
            else {}
        )

        now = time.time()
        board = state.db.pending_board(sub) if pending else []
        # New deck is the page's orange button, unless proposals wait: then Review them is
        new_deck_cls = "btn" if pending else "btn btn-primary"
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
                "<a class='btn btn-primary' href='/account/details#archidekt'>"
                f"{icon('account')} Link Archidekt</a>"
                if not link["linked"]
                else f"<a class='{new_deck_cls}' href='/decks/new' data-box-open='newdeck'>{icon('plus')} "
                "New deck</a>"
            )
            + "</div>"
        )
        forge_running = len(state.reports.forge_queue(sub)) if state.reports is not None else 0
        hits: list[str] = []
        if state.prices is not None:
            data = state.prices.overview(sub, history=False)
            hits = [d for d in (due_line(v) for v in data.get("cards") or [] if v.get("alerts")) if d]
        needs = needs_you_html(
            pending=pending,
            board=board,
            forge_running=forge_running,
            watch_hits=hits,
            linked=bool(link["linked"]),
            now=now,
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
        # the connection details only while no assistant is connected (Account details lists them after)
        connect = connect_html(s.mcp_url) if not state.db.user_clients(sub) else ""
        # the New deck box (F4): the hero's New deck and the empty panel's "Create one" open it here
        csrf = _csrf(s, sid)
        newdeck = new_deck_box_html(csrf, None, back="/") if link["linked"] and csrf else ""
        resp = render(
            "Home",
            hero + needs + recent + connect + newdeck,
            site=s.server_name,
            signed_in=True,
            csrf=csrf,
            admin=admin,
            sub=sub,
            wide=True,
            current="/",
            heading=False,
            body_class="home",
            # decks.js fills the recent-decks panel; Home has none of deck.js's hooks
            head_extra=f"<style>{HOME_CSS}{NEW_DECK_CSS if newdeck else ''}</style>"
            "<script src='/static/decks.js' defer></script>"
            + "".join(
                f"<script src='/static/{n}' defer></script>" for n in (NEW_DECK_SCRIPTS if newdeck else ())
            ),
        )
        resp.headers["Content-Security-Policy"] = DECK_CSP  # deck covers come from Scryfall
        return resp
