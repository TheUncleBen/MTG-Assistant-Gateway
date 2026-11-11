"""The signed-in home page: your recent decks and what needs you (decision C8, 0.7.46).

Under the greeting and New deck come the things waiting for the member, each only when there
is one: proposals waiting for their press, Forge tests running, and one row for every new alert
(the bell's count, read after the page has loaded by static/bell.js: price alerts, finished
reports, Archidekt's alerts and the rest, all on the Alerts page).
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

from . import assistants, prefs, ui
from .busy import outage
from .companion import DECK_CSP
from .companion_decks import NEW_DECK_CSS, NEW_DECK_SCRIPTS, new_deck_box_html
from .deckpage import covers_for, deck_list_held_html, decks_unreadable_html, image_url, reread_href
from .decks import DeckError, is_busy
from .digits import is_digits
from .pages import _csrf, browser_session, login_redirect_here
from .theme import SPINNER, icon, render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

log = logging.getLogger(__name__)
_esc = html.escape
RECENT = 8
UPDATE_DISMISSED = "update.dismissed"  # account_prefs: the release an admin closed the update strip for
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
/* desktop (1280 px up): the deck covers take the page's width, with what needs you and the
   connection details in a column beside them, so the page's lower half is not empty (0.7.46
   re-audit D28); the covers grow from 10rem to 17rem a column (three a row at 1280 px, four at 1440) */
/* (0.7.47) only while the room holds 60 lines of text across: at 200% text a laptop's page is
   too narrow for two columns, and the side column left a screen-high hole beside the covers */
.home .home-box{container-type:inline-size}
@media (min-width:1280px){ @container (min-width:60em){
  .home .home-grid.side{display:grid;grid-template-columns:minmax(0,1fr) 24rem;grid-template-rows:auto 1fr;
    gap:1rem;align-items:start}
  .home .home-grid.side > .panel:not(.connect){grid-column:1;grid-row:1 / span 2;margin:0}
  .home .home-grid.side > .needs,.home .home-grid.side > .connect{grid-column:2;margin:0}
  .home .home-grid .recent{grid-template-columns:repeat(auto-fill,minmax(min(100%,17rem),1fr))}
}}
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
    tied = [r for r in rows if is_digits(str(r.get("deck_id") or ""))]
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
    det = f"<small>{ui.dotted(detail)}</small>" if detail else ""
    # ``ic`` "spinner": the theme's one animated ring, for a run still going, never a still icon
    mark = SPINNER if ic == "spinner" else icon(ic)
    return (
        f"<li{' class=accent' if accent else ''}{attrs}>{mark}<span class='what'>{text}{det}</span>"
        f"<span class='go'>{button}</span></li>"
    )


def running_row(running: list[dict[str, Any]]) -> str:
    """The tests still going (reports.running): one test names its step, its deck and the time so
    far, with the spinner, and opens that report; several open the Reports list. running-live.js
    asks for it again every few seconds (running_view)."""
    if not running:
        return ""
    from .running_view import _live_attrs, step_words

    attrs = _live_attrs("home", running)
    if len(running) == 1:
        one = running[0]
        text, so_far = step_words(one)
        name = one["deck_name"] or "Unnamed deck"
        return need_row(
            "spinner",
            f"<b>1 test running</b>: {_esc(text)}.",
            f"{name} · {so_far}" if so_far else name,
            ui.button_link("Open the report", f"/history/reports/{one['report_id']}"),
            attrs=attrs,
        )
    return need_row(
        "spinner",
        f"<b>{len(running)} tests running</b>: "
        + _esc("; ".join(f"{r['deck_name'] or 'Unnamed deck'}, {r['progress']}" for r in running[:3]))
        + ".",
        "",
        ui.button_link("Open reports", "/reports"),
        attrs=attrs,
    )


def ended_row(ended: list[dict[str, Any]]) -> str:
    """Home's row once the tests it showed have ended: that they did, and the way to the report."""
    if not ended:
        return ""
    from .running_view import ended_words

    words = ended_words(ended)
    head, _, rest = words.partition(".")
    if len(ended) == 1:
        go = ui.button_link("Open the report", f"/history/reports/{ended[0]['report_id']}")
        detail = ended[0].get("deck_name") or "Unnamed deck"
    else:
        go, detail = ui.button_link("Open reports", "/reports"), ""
    text = f"<b>{_esc(head)}</b>." + (f" {_esc(rest.strip())}" if rest.strip() else "")
    return need_row("check", text, str(detail), go)


def needs_you_html(
    *,
    pending: int,
    board: list[dict[str, Any]],
    running: list[dict[str, Any]],
    linked: bool,
    now: float,
) -> str:
    """Needs you: the rows for what waits, each only when there is something; nothing at all
    when nothing waits (the alerts row shows once bell.js has a count above zero). With a
    link, Review it (them) is the page's orange button; without one, Link Archidekt keeps it.
    ``running``: the tests still going (reports.running)."""
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
    if running:
        rows.append(running_row(running))
    # every new alert in one row (0.7.47: price alerts, Archidekt's, finished reports, ... are
    # all on the Alerts page): the count comes after the page has loaded (static/bell.js fills
    # data-bell-n and shows the row when the count is above zero)
    rows.append(
        need_row(
            "bell",
            "<b><span data-bell-n>0</span> new <span data-bell-word>alerts</span></b>",
            "",
            f"<a class='btn btn-plain' href='/notifications' data-bell-open>{icon('bell')} Open alerts</a>",
            attrs=" data-bell-row hidden",
        )
    )
    # only the alerts row, which waits for bell.js's count: the whole section waits with it, so
    # nothing to do shows no heading over nothing (bell.js shows the section with the row)
    wait = " data-bell-section hidden" if len(rows) == 1 else ""
    return (
        f"<section class='needs' aria-labelledby='needs-h'{wait}><h2 id='needs-h'>Needs you</h2>"
        f"<ul>{''.join(rows)}</ul></section>"
    )


PENDING = "pending"  # recent_decks: the list is being read; the page shows a placeholder meanwhile


class Unread(str):
    """recent_decks: the list couldn't be read; the text is the one sentence that says why, the
    same the tools and the deck list say for that state (busy.OUTAGE; 0.7.46 sweep B-1)."""


async def recent_decks(state: AppState, sub: str, *, wait: float | None) -> list[dict[str, Any]] | str | None:
    """The member's newest decks; an Unread (the reason) when they cannot be read right now;
    PENDING when the list is cold and the read has not answered within ``wait`` seconds (it
    carries on in the cache)."""
    try:
        rows = await asyncio.wait_for(state.decks.list_decks_quick(sub, wait=wait), DECKS_TIMEOUT)
    except (DeckError, TimeoutError) as exc:
        why = Unread(str(exc) if isinstance(exc, DeckError) else outage("timeout"))
        if isinstance(exc, DeckError) and not is_busy(exc):
            return why
        try:  # Archidekt can't be asked: the list as last read (its last_read says how old)
            rows = state.decks.last_deck_list(sub)
        except DeckError:
            return why
        if rows is None:
            return why
    except Exception:  # pragma: no cover - a surprise must not take the home page down
        log.exception("home: listing decks failed")
        return Unread(outage("unavailable"))
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
    why: str | None = None,
) -> str:
    """The inside of the home page's My decks panel: the newest covers, or why there are none.
    /api/decks/mine?shape=recent answers with the same markup for the placeholder to swap in.
    ``unreadable``: Archidekt left entries of the list unreadable, so the panel says so with the
    /decks notice and Try again, over the covers it did read, never "No decks yet". ``last_read``:
    the list is as last read (Archidekt can't be asked), and the panel says how old it is.
    ``why``: with no decks, the sentence that says why they couldn't be read (the refusal's own,
    busy.OUTAGE's for its state)."""
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
            f"<h2>My decks</h2><p class='muted'>{html.escape(why or outage('unavailable'))} "
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
            f"<span title='{name}'>{name}</span></a>"
        )
    return "<div class='recent'>" + "".join(out) + "</div>"


def home_grid(needs: str, recent: str, connect: str) -> str:
    """Needs you, the My decks panel and the connection details, in that order. On a desktop
    (1280 px up) the panel takes the page's width with the other two in a column beside it,
    when there is a panel and something to put beside it (class ``side``); otherwise they
    follow each other as on a phone."""
    side = " side" if recent and (needs or connect) else ""
    return f"<div class='home-box'><div class='home-grid{side}'>{needs}{recent}{connect}</div></div>"


def connect_html(mcp_url: str) -> str:
    """Connect an AI assistant: the connector address and the ways in, shown only while no
    assistant is connected (afterwards the Account page has it)."""
    return (
        # the row says what it is for, not the connector address (0.7.46 audit QG-20); the
        # address is inside, with Copy, for someone setting up a connector by hand
        "<details class='connect panel'><summary>Connect an AI assistant "
        "<span class='muted addr'>Claude or ChatGPT, with Guided setup</span></summary>"
        "<p>Claude and ChatGPT talk to this gateway through its connector address. Guided setup walks "
        "you through it; the assistant can then read your decks, research cards and propose edits "
        "you approve here.</p>"
        f"<p class='addrline'><code>{_esc(mcp_url)}</code> "
        f"<button type='button' class='btn small' data-copy-text='{_esc(mcp_url)}' "
        "data-copy-said='Connector address copied.'>Copy address</button></p>"
        f"<div class='actions'><a class='btn' href='/install'>{icon('download')} Guided setup"
        f"</a><a class='btn' href='/install?for=skill'>{icon('report')} Assistant skill</a>"
        f"<a class='btn' href='/install?for=android'>{icon('download')} Android app</a></div></details>"
    )


def add_home_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings

    @server.custom_route("/home/dismiss", methods=["POST"], include_in_schema=False)
    async def dismiss(request: Request) -> Response:
        """A notice's × (assistants.dismiss_form): gone for good, on every device. An
        assistant's notice is its page seen, and its New assistant alert is read too."""
        sub, sid = browser_session(state, request)
        if sub is None:
            return login_redirect_here(request, "/")
        from urllib.parse import parse_qs

        from .pages import csrf_form_ok, read_limited

        raw = await read_limited(request, 4096)
        data = {k: v[0] for k, v in parse_qs((raw or b"").decode("utf-8", "replace")).items()}
        if csrf_form_ok(s, sid, data) and data.get("what") == "assistant":
            key = (data.get("key") or "")[:200]
            state.db.mark_assistant_opened(sub, key)
            ids = [
                a["id"]
                for a in state.db.gateway_alerts(sub, kinds=("account",))
                if str(a["ukey"] or "").startswith(assistants.alert_prefix(key))
            ]
            if ids:
                state.db.set_gateway_alerts_read(sub, ids)
                if state.notifier is not None:
                    state.notifier.withdraw_alerts(sub, ids)
        elif csrf_form_ok(s, sid, data) and data.get("what") == "update":
            state.db.set_pref(sub, UPDATE_DISMISSED, (data.get("key") or "")[:20])
        back = data.get("next") if str(data.get("next") or "").startswith("/") else "/"
        if back.startswith("//"):
            back = "/"
        return RedirectResponse(back, 303)

    @server.custom_route("/", methods=["GET"], include_in_schema=False)
    async def index(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if sub is None:
            return login_redirect_here(request, "/")
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
                "<a class='btn btn-primary' href='/account/details/archidekt'>"
                f"{icon('account')} Link Archidekt</a>"
                if not link["linked"]
                else f"<a class='{new_deck_cls}' href='/decks/new' data-box-open='newdeck'>{icon('plus')} "
                "New deck</a>"
            )
            + "</div>"
        )
        running = state.reports.running(sub) if state.reports is not None else []
        needs = needs_you_html(
            pending=pending,
            board=board,
            running=running,
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
            why = decks if isinstance(decks, Unread) else None
            shown = None if isinstance(decks, str) else decks
            unreadable = bool(getattr(shown, "unreadable", 0))  # the deck list couldn't be read, as on /decks
            recent = (
                "<section class='panel'>"
                + recent_panel_inner(
                    shown, covers, unreadable=unreadable, last_read=getattr(shown, "last_read", None), why=why
                )
                + "</section>"
            )
        else:
            recent = ""
        # the connection details only while no assistant is connected (Account details lists them after)
        connect = connect_html(s.mcp_url) if not state.db.user_clients(sub) else ""
        # an assistant connected for the first time: one notice under the greeting, naming the
        # default it starts with (assistants.py), until its page is opened
        joined = assistants.home_notice(
            state.db, sub, state.decks.mode_of(sub), now=now, csrf=_csrf(s, sid) or ""
        )
        # the New deck box (F4): the hero's New deck and the empty panel's "Create one" open it here
        csrf = _csrf(s, sid)
        newdeck = new_deck_box_html(csrf, None, back="/") if link["linked"] and csrf else ""
        resp = render(
            "Home",
            hero + joined + home_grid(needs, recent, connect) + newdeck,
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
            head_extra=f"<style>{HOME_CSS}{assistants.HOME_CSS if joined else ''}"
            f"{NEW_DECK_CSS if newdeck else ''}</style>"
            "<script src='/static/decks.js' defer></script>"
            + ("<script src='/static/running-live.js' defer></script>" if running else "")
            + "".join(
                f"<script src='/static/{n}' defer></script>" for n in (NEW_DECK_SCRIPTS if newdeck else ())
            ),
        )
        resp.headers["Content-Security-Policy"] = DECK_CSP  # deck covers come from Scryfall
        return resp
