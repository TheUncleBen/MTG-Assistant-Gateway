"""The signed-in home page: a dashboard in the shape of Archidekt's landing page.

Under the newest decks and the deck search, Quick access has one widget per section, in the
app side bar's order (Decks, Search, Scan, Collection, Proposals, History, Reports, Watchlist,
Guide, Account, then Admin for admins), each with at most two quick starts; on a phone they are
compact tiles, two a row, with the label and number only. The top navigation stays visible like
on every other page, and the assistant connection details sit in a
collapsed panel at the end, where a member who only uses the pages never has to see them. The page
is for signed-in members only; MCP clients use /mcp and the OAuth routes.
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
from .deckpage import covers_for, deck_list_held_html, decks_unreadable_html, image_url, reread_href
from .decks import DeckError, is_busy
from .pages import _csrf, browser_session, login_redirect
from .theme import display_name, icon, render
from .watchlist_page import due_line

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

log = logging.getLogger(__name__)
_esc = html.escape
RECENT = 6
DECKS_TIMEOUT = 6.0  # seconds; the home page must not hang on Archidekt


HOME_CSS = """
/* Quick access: the dashboard widgets, the label is the section's link */
.hq{grid-template-columns:repeat(auto-fill,minmax(min(100%,15.5rem),1fr))}
.hq .wdg a.lab{color:var(--text);text-decoration:none;font-size:.95rem}
.hq .wdg a.lab:hover{text-decoration:underline}
.hq .wdg a.lab svg{stroke:var(--orange-text)}
.hq .wdg .big{font-size:1.6rem}
.home .list-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:.25rem 1rem;margin:1.5rem 0 .75rem}
.home .list-head h2{margin:0}
@media (min-width:600px) and (max-width:899.98px){.hq{grid-template-columns:repeat(2,minmax(0,1fr))}}
/* phones: compact tiles, two a row, label and number only; the whole tile is the link */
@media (max-width:599.98px){
  .hq{grid-template-columns:1fr 1fr;gap:.5rem}
  .hq .wdg{position:relative;padding:.6rem .7rem;min-height:64px;justify-content:center;gap:.15rem}
  .hq .wdg .det,.hq .wdg .go{display:none}
  .hq .wdg a.lab{text-transform:none;letter-spacing:0;font-size:1rem;min-height:28px;overflow-wrap:anywhere}
  .hq .wdg a.lab::after{content:'';position:absolute;inset:0}
  .hq .wdg .big{font-size:1.15rem}
}
"""


def quick(
    href: str,
    ic: str,
    title: str,
    detail: str = "",
    *,
    big_html: str = "",
    detail_html: str = "",
    actions: tuple[str, ...] = (),
    tone: str = "",
) -> str:
    """One Quick access widget: the section's name as its link, a number when there is one, one
    line and at most two quick starts (ui.button_link)."""
    if len(actions) > 2:
        raise ValueError("a widget has at most two quick starts")
    det = detail_html or _esc(detail)
    return (
        f"<div class='wdg{' ' + tone if tone else ''}'><a class='lab' href='{_esc(href)}'>{icon(ic)}"
        f"{_esc(title)}</a>"
        + (f"<span class='big'>{big_html}</span>" if big_html else "")
        + (f"<span class='det'>{det}</span>" if det else "")
        + (f"<span class='go'>{''.join(actions)}</span>" if actions else "")
        + "</div>"
    )


def _n(n: int, one: str, many: str) -> str:
    return f"{n:,}<small>{_esc(one if n == 1 else many)}</small>"


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
    """The Proposals widget's line, the words of the Proposals page's "Waiting for you" widget:
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


def reports_widget(
    index: list[dict[str, Any]], latest: dict[str, Any] | None, going: int, *, now: float
) -> str:
    """Reports: Forge runs going as the number, the newest report as the line."""
    det = "No reports yet."
    actions: tuple[str, ...] = (ui.button_link("All reports", "/reports"),)
    if latest is not None:
        win = latest.get("win")
        how = ""
        if win and win.get("rate") is not None:
            how = f", {100 * win['rate']:.0f}% won against precons"
        ago = ui.when_text(latest.get("taken_at"), now=now)
        det = f"Latest: {latest.get('deck_name') or 'an unnamed deck'}, {ago}{how}"
        actions = (
            ui.button_link("Open latest", f"/history/reports/{latest['report_id']}"),
            ui.button_link("All reports", "/reports"),
        )
    return quick(
        "/reports",
        "report",
        "Reports",
        det,
        big_html=_n(going, "Forge going", "Forge going") if going else "",
        actions=actions,
    )


def watchlist_widget(data: dict[str, Any]) -> str:
    """Watchlist: how many cards and decks, and today's first alert past its line."""
    cards, decks = data.get("cards") or [], data.get("decks") or []
    due = [d for d in (due_line(v) for v in cards if v.get("alerts")) if d]
    return quick(
        "/watchlist",
        "stats",
        "Watchlist",
        due[0] if due else "",
        big_html=_n(len(cards), "card", "cards") + " " + _n(len(decks), "deck", "decks"),
        actions=(ui.button_link("Open watchlist", "/watchlist"),),
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
        name = _esc(d.get("name") or "Unnamed deck")
        out.append(
            f"<a href='/decks/{did}' data-deck-id='{did}' data-deck-name='{name}'{style}>"
            f"<span>{name}</span></a>"
        )
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
            # plain: the hero's New deck is the page's one orange button
            f"<button class='btn-plain'>{icon('search')} Search Archidekt</button></form></section>"
        )
        now = time.time()
        board = state.db.pending_board(sub) if pending else []
        items = [
            quick(
                "/decks",
                "decks",
                "Decks",
                "Your decks as stacks, grid or text, with stats and edits.",
                big_html=_n(len(decks), "deck", "decks") if isinstance(decks, list) else "",
                actions=(ui.button_link("All decks", "/decks"),),
            ),
            quick(
                "/search",
                "search",
                "Search",
                "Public decks by name, commander, format or owner.",
                actions=(ui.button_link("Card search", "/cards"), ui.button_link("Precons", "/precons")),
            ),
            quick(
                "/scan",
                "scan",
                "Scan",
                "Point your phone at cards to build a list, a deck or your collection.",
                big_html=_n(scans, "open", "open") if scans else "",
                actions=(ui.button_link("Start a scan", "/scan"),),
            ),
            quick(
                "/collection",
                "collection",
                "Collection",
                "The cards you own, on your Archidekt account; they get a green dot on every deck.",
                actions=(ui.button_link("Open collection", "/collection"),),
            ),
            quick(
                "/proposals",
                "proposals",
                "Proposals",
                proposals_line(board, now=now),
                big_html=_n(pending, "waiting", "waiting") if pending else "",
                actions=(ui.button_link("Review them" if pending else "Open proposals", "/proposals"),),
                tone="accent" if pending else "",
            ),
            quick(
                "/history",
                "history",
                "History",
                "Snapshots, restores, deck reports and backup copies.",
                actions=(ui.button_link("Open history", "/history"),),
            ),
        ]
        if state.reports is not None:
            index = state.reports.index(sub)
            latest = (state.reports.summaries(sub, [index[0]["report_id"]]) or [None])[0] if index else None
            items.append(reports_widget(index, latest, len(state.reports.forge_queue(sub)), now=now))
        if state.prices is not None:
            items.append(watchlist_widget(state.prices.overview(sub, days=31)))
        items += [
            quick(
                "/guide",
                "guide",
                "Guide",
                "What you can do here, with or without an assistant.",
                actions=(ui.button_link("Open guide", "/guide"),),
            ),
            quick(
                "/account",
                "account",
                "Account",
                "Your Archidekt link, notifications and the Android app.",
                actions=(ui.button_link("Open account", "/account"),),
            ),
        ]
        if admin:
            items.append(
                quick(
                    "/admin",
                    "settings",
                    "Admin",
                    "Members, activity and the system.",
                    actions=(ui.button_link("Open admin", "/admin"),),
                )
            )
        tiles = (
            ui.list_head("Quick access", note="The same sections, in the same order, as the app's side bar")
            + "<section class='ui-widgets hq' aria-label='Quick access'>"
            + "".join(items)
            + "</section>"
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
            head_extra=f"<style>{HOME_CSS}</style><script src='/static/decks.js' defer></script>",
        )
        resp.headers["Content-Security-Policy"] = DECK_CSP  # deck covers come from Scryfall
        return resp
