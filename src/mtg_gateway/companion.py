"""The companion pages: my decks, one deck, history and activity, plus the app shell.

These are the pages the Android app wraps and the browser shows on a phone. They render on
the server from the same services the MCP tools and the JSON API use; the small amount of
JavaScript (the deck editor) lives in ``static/companion.js`` and talks to ``/api/v1``.
Layout rules asked for by the Android thread: two panes (list and detail) from 600 px up, one
column below; internal links never open a new tab; external sites (Archidekt, Scryfall) may.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from . import deck_stats
from .archidekt import FORMAT_NAMES, Deck, featured_scryfall_id, format_label, parse_deck
from .busy import busy_json_response, busy_response, busy_text_response
from .decklist import DecklistError, parse_decklist
from .deckpage import (
    DECK_CSS,
    LIST_ORDERS,
    card_image,
    compare_page_html,
    covers_for,
    deck_list_controls_html,
    deck_list_html,
    deck_page_html,
    featured,
    precon_by_label,
)
from .decks import DeckError, actor_label, current_client
from .history_view import (
    HISTORY_CSS,
    PAGE,
    backup_copies_html,
    build_events,
    events_html,
    filter_bar_html,
    is_filtered,
    pager_html,
    read_query,
    when_since,
)
from .pages import (
    BROWSER_CLIENT_ID,
    _csrf,
    _err_code,
    _when,
    browser_session,
    login_redirect,
    read_limited,
)
from .report_view import (
    REPORT_CSS,
    report_body_html,
    report_export_html,
    report_markdown,
)
from .theme import VIZ_CSS, icon, render
from .views import auto_category, cards_by_category

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .reports import ReportService

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
ICON_HEADERS = {"Cache-Control": "public, max-age=86400", "X-Content-Type-Options": "nosniff"}
# Pages with the editor load one script from /static; everything else keeps the default CSP.
DECK_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
DECKS_JSON_TIMEOUT = 25.0  # /api/decks/mine waits this long for a cold list at most
# Format names the settings and new-deck forms offer, one per Archidekt format id.
FORMAT_CHOICES = sorted({FORMAT_NAMES[i] for i in FORMAT_NAMES}, key=lambda n: format_label(n).lower())
EDITOR_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)


def _json_for_html(data: Any) -> str:
    """JSON safe inside a <script type=application/json> block: every '<' becomes \\u003c, which
    is still valid JSON, so neither '</script>' nor '<!--' can appear in the page source."""
    return json.dumps(data).replace("<", "\\u003c")


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _ext(url: str, text: str) -> str:
    """A link to an external site (Archidekt, Scryfall) may open a new tab."""
    return f"<a href='{_esc(url)}' target='_blank' rel='noopener noreferrer'>{_esc(text)}</a>"


def _num(value: Any, digits: int = 1) -> str:
    if value is None:
        return "–"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def _bars(values: dict[str, Any], *, label: str) -> str:
    """A tiny CSS bar chart (no script): one bar per key, height relative to the max."""
    items = [(k, int(v or 0)) for k, v in values.items()]
    top = max([v for _k, v in items] + [1])
    bars = "".join(
        f"<div class='bar' title='{_esc(k)}: {v}'>"
        f"<span style='height:{max(4, round(100 * v / top))}%'></span>"
        f"<em>{_esc(k)}</em><b>{v}</b></div>"
        for k, v in items
    )
    return f"<div class='bars' role='img' aria-label='{_esc(label)}'>{bars}</div>"


def _pips(pips: dict[str, Any], sources: dict[str, Any] | None) -> str:
    names = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green", "C": "Colourless"}
    rows = []
    for col in ("W", "U", "B", "R", "G", "C"):
        p = pips.get(col)
        s = (sources or {}).get(col)
        if not p and not s:
            continue
        rows.append(
            f"<li><span class='pip pip-{col}' aria-hidden='true'></span><span>{names[col]}</span>"
            f"<b>{_num(p, 0)} pips</b>" + (f"<i>{_num(s, 0)} sources</i>" if s is not None else "") + "</li>"
        )
    return f"<ul class='plain pips'>{''.join(rows)}</ul>" if rows else ""


def _sparkline(points: list[float | None], *, width: int = 160, height: int = 36) -> str:
    """Inline SVG line for a metric over time (no script, CSP-safe)."""
    vals = [p for p in points if isinstance(p, (int, float))]
    if len(vals) < 2:
        return ""
    lo, hi = min(vals), max(vals)
    span = (hi - lo) or 1.0
    step = (width - 4) / (len(points) - 1)
    coords = []
    for i, p in enumerate(points):
        if not isinstance(p, (int, float)):
            continue
        x = 2 + i * step
        y = height - 2 - (p - lo) / span * (height - 4)
        coords.append(f"{x:.1f},{y:.1f}")
    return (
        f"<svg class='spark' viewBox='0 0 {width} {height}' width='{width}' height='{height}' "
        f"aria-hidden='true'><polyline fill='none' stroke='currentColor' stroke-width='2' "
        f"points='{' '.join(coords)}'/></svg>"
    )


def stats_strip(stats: dict[str, Any] | None) -> str:
    if not stats:
        return ""
    bracket = stats.get("bracket_estimate") or {}
    tiles = [
        ("Cards", _num(stats.get("card_count"), 0)),
        ("Lands", _num(stats.get("land_count"), 0)),
        ("Avg MV", _num(stats.get("average_mana_value"))),
        ("Price", f"${_num(stats.get('price_total'))}" if stats.get("price_total") is not None else "–"),
        ("Bracket", f"~{bracket.get('bracket')}" if bracket.get("bracket") else "–"),
        ("Game changers", _num(len(stats.get("game_changers") or []), 0)),
    ]
    tile_html = "".join(f"<div class='tile'><b>{_esc(v)}</b><span>{_esc(k)}</span></div>" for k, v in tiles)
    curve = stats.get("mana_curve") or {}
    out = [f"<div class='tiles'>{tile_html}</div>"]
    if curve:
        out.append(
            "<div class='twocol'><div><h3>Mana curve</h3>"
            + _bars(curve, label="Mana curve")
            + "</div><div><h3>Colours</h3>"
            + _pips(stats.get("colour_pips") or {}, stats.get("mana_sources"))
            + "</div></div>"
        )
    problems = stats.get("legality_problems") or []
    if problems:
        out.append(
            "<p class='notice warn'>Not legal in this format: "
            + ", ".join(_esc(p if isinstance(p, str) else p.get("name")) for p in problems[:12])
            + ("…" if len(problems) > 12 else "")
            + "</p>"
        )
    if bracket.get("basis"):
        out.append(
            "<p class='muted small'>Bracket estimate from Archidekt's card flags only: "
            + "; ".join(_esc(b) for b in bracket["basis"])
            + ".</p>"
        )
    return "".join(out)


# Notices /decks/{id} shows for ?ok= and ?err=. Only these codes are accepted; anything else shows
# nothing, so a crafted link cannot put words of its choosing in the page's notice box.
DECK_OK_MESSAGES = {
    "report": "Report saved. See it under History.",
    "saved": "Saved to Archidekt. A snapshot from just before is under History if you want to undo.",
    "created": "Created on Archidekt.",
}
INDENT = "\u2003"  # an em space per folder level in folder pickers
# Notices the settings page shows after one of its hand actions (?ok=).
SETTINGS_OK_MESSAGES = {
    "cover": "Cover image saved to Archidekt. A snapshot from just before is under History.",
    "cover_auto": "Archidekt picks the cover image again. A snapshot from just before is under History.",
    "tag_added": "Tag added on Archidekt.",
    "tag_removed": "Tag removed on Archidekt.",
    "moved": "Deck moved to that folder on Archidekt.",
}
# Notices My decks shows (?ok=).
LIST_OK_MESSAGES = {
    "deleted": "Deck deleted on Archidekt. Its last snapshot is under History; when backups are on, a copy "
    "is in your backup folder.",
    "folder_created": "Folder created on Archidekt.",
    "folder_renamed": "Folder renamed on Archidekt.",
}
DECK_ERR_MESSAGES = {
    "report_failed": "The deck report could not be made. Try again later.",
    "invalid": "The deck report could not be made: that request was not valid.",
    "not_found": "Archidekt could not find that deck.",
    "rate_limited": "Too many requests for your account are still running. Wait for them to finish "
    "and try again.",
    "unavailable": "Archidekt or the research service is unavailable right now. Try again later.",
    "not_linked": "Your Archidekt account is not linked, or Archidekt no longer accepts the stored "
    "session. Relink it on the Account page.",
    "forbidden": "Archidekt refused access to that deck.",
}


def add_companion_routes(server: MCPServer, state: AppState, reports: ReportService) -> None:
    s = state.settings

    async def apply_now(sub: str, pid: str, *, ok: str, deck_id: str | None = None) -> Response:
        """A member's own action in the app (new deck, clone, settings) is their approval: the
        proposal it made is applied at once, with its snapshot, and the deck opens with a notice.
        When Archidekt refuses or writes are off, the proposal stays pending on its review page,
        which says why and keeps the Apply button for later."""
        try:
            result = await state.decks.apply(sub, pid, via="browser")
        except DeckError as exc:
            code = _err_code(exc.kind, proposal_exists=state.db.get_proposal(pid, sub) is not None)
            return RedirectResponse(f"/proposals/{pid}?err={code}", status_code=303)
        if result.get("state") == "applying":  # a large one carries on; its page shows the progress
            return RedirectResponse(f"/proposals/{pid}?ok=applying", status_code=303)
        made = result.get("result") if isinstance(result.get("result"), dict) else {}
        target = deck_id or str(made.get("deck_id") or result.get("deck_id") or "")
        if not target.isdigit():
            return RedirectResponse(f"/proposals/{pid}?ok=applied", status_code=303)
        return RedirectResponse(f"/decks/{target}?ok={ok}", status_code=303)

    decks = state.decks

    def page(
        title: str,
        body: str,
        *,
        sub: str,
        sid: str | None,
        status: int = 200,
        two_pane: bool = False,
        csp: str | None = None,
        current: str | None = None,
        scripts: bool = False,
        heading: bool = True,
        deck_css: bool = False,
        extra_scripts: tuple[str, ...] = (),
        extra_css: str = "",
    ) -> Response:
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
            wide=two_pane,
            scripts=scripts or bool(extra_scripts),
            current=current,
            heading=heading,
            head_extra=(f"<style>{DECK_CSS}</style>" if deck_css else "")
            + (f"<style>{extra_css}</style>" if extra_css else "")
            + (
                "<script src='/static/cardview.js' defer></script>"
                "<script src='/static/deck.js' defer></script>"
                if scripts
                else ""
            )
            + "".join(f"<script src='/static/{name}' defer></script>" for name in extra_scripts),
        )
        if csp:
            resp.headers["Content-Security-Policy"] = csp
        return resp

    EXPIRED = "<p class='notice error'>This form expired. Reload and try again.</p>"

    async def form(request: Request, limit: int = 16_384) -> dict[str, str]:
        from urllib.parse import parse_qs

        raw = await read_limited(request, limit)
        if raw is None:
            return {}
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}

    def check_csrf(sid: str | None, data: dict[str, str]) -> bool:
        import hmac

        expected = _csrf(s, sid)
        given = data.get("csrf", "").encode()
        return bool(sid and expected and hmac.compare_digest(given, expected.encode()))

    async def my_decks(
        sub: str, *, wait: float | None = None
    ) -> tuple[list[dict[str, Any]] | None, str | None, DeckError | None]:
        """(decks, problem, failure) where problem is a short message when the list is unavailable
        and failure the DeckError behind it (for the shared busy page). With ``wait``, decks is
        None when the list is cold and Archidekt has not answered in time: the page then renders a
        placeholder that decks.js fills from /api/decks/mine."""
        try:
            return await decks.list_decks_quick(sub, wait=wait), None, None
        except DeckError as exc:
            if exc.kind == "not_linked":
                return [], "not_linked", exc
            return [], str(exc), exc

    def arrange_decks(
        rows: list[dict[str, Any]], *, q: str, order: str, folder: str
    ) -> tuple[list[dict[str, Any]], list[str], int]:
        """Filter and sort the member's list as the /decks controls ask: (rows, folders, total)."""
        folders = sorted({str(d.get("folder")) for d in rows if d.get("folder")}, key=str.lower)
        total = len(rows)
        if q:
            rows = [d for d in rows if q.lower() in str(d.get("name", "")).lower()]
        if folder:
            rows = [d for d in rows if d.get("folder") == folder]
        if order == "name":
            rows.sort(key=lambda d: str(d.get("name", "")).lower())
        elif order == "created":
            rows.sort(key=lambda d: str(d.get("created_at") or ""), reverse=True)
        elif order == "format":
            rows.sort(key=lambda d: (str(d.get("format_name") or ""), str(d.get("name", "")).lower()))
        return rows, folders, total

    def list_query(qp: Any) -> tuple[str, str, str, str]:
        """The deck list's query parameters, each limited to its known values: (q, order, view, folder)."""
        q = (qp.get("q") or "").strip()[:80]
        order = qp.get("order") if qp.get("order") in LIST_ORDERS else "updated"
        view = qp.get("view") if qp.get("view") in ("grid", "list") else "grid"
        folder = (qp.get("folder") or "").strip()[:80]
        return q, order, view, folder

    def deck_list_skeleton(view: str) -> str:
        return (
            f"<ul class='plain decklist {_esc(view)} skeleton' aria-hidden='true'>"
            + "<li></li>" * 6
            + "</ul><p class='sr-only' role='status'>Loading your decks</p>"
            "<noscript><p class='muted'><a href='/decks'>Reload</a> to see your decks (this page fills "
            "itself with scripts on).</p></noscript>"
        )

    def link_prompt() -> str:
        return (
            "<div class='card'><h2>Link your Archidekt account</h2><p>Your decks appear here once Archidekt "
            "is linked. Public decks can still be opened by their Archidekt link.</p>"
            "<div class='actions'><a class='btn btn-primary' href='/account'>Link Archidekt</a></div></div>"
        )

    # -- my decks ---------------------------------------------------------------
    @server.custom_route("/decks", methods=["GET"], include_in_schema=False)
    async def decks_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
        qp = request.query_params
        q, order, view, folder = list_query(qp)
        rows, problem, failure = await my_decks(sub, wait=decks.deck_list_wait)
        if failure is not None and (busy := busy_response(failure, request, page, sub=sub, sid=sid)):
            return busy
        pending = rows is None  # cold start: the shell goes out now, decks.js fills the list
        hidden = 0  # the gateway's backup copies, kept out of this list (D-03); the list is warm now
        if not pending and not problem:
            try:
                hidden = len(await decks.backup_copies(sub, wait=0.0) or [])
            except DeckError:
                hidden = 0
        rows, folders, total = arrange_decks(rows or [], q=q, order=order, folder=folder)
        open_form = (
            "<form method='get' action='/decks/open' class='openform'>"
            "<label for='ref'>Open any Archidekt deck</label>"
            "<input id='ref' type='text' name='ref' placeholder='Archidekt link or deck id' required>"
            "<button>Open</button></form>"
        )
        notice = ""
        if qp.get("err") == "bad_ref":
            notice = "<p class='notice error'>That is not an Archidekt deck link or id.</p>"
        if qp.get("ok") in LIST_OK_MESSAGES:
            notice = f"<p class='notice ok'>{_esc(LIST_OK_MESSAGES[qp['ok']])}</p>"
        if problem == "not_linked":
            body = link_prompt() + f"<div class='panel'>{open_form}</div>"
            return page("My decks", notice + body, sub=sub, sid=sid, current="/decks")
        if pending:
            src = "/api/decks/mine?" + urlencode(
                {"shape": "list", "q": q, "order": order, "view": view, "folder": folder}
            )
            listing = f"<div data-decks-src='{_esc(src)}' aria-busy='true'>{deck_list_skeleton(view)}</div>"
        else:
            covers = covers_for(rows, state.db.deck_covers([str(d["id"]) for d in rows]))
            listing = (f"<p class='notice error'>{_esc(problem)}</p>" if problem else "") + deck_list_html(
                rows, covers=covers, q=q, view=view
            )
        body = (
            notice
            + deck_list_controls_html(
                q=q, order=order, view=view, folders=folders, folder=folder, total=total, pending=pending
            )
            + listing
            + (
                f"<p class='muted small backups-note'>{hidden} backup "
                f"{'copy' if hidden == 1 else 'copies'} made before changes "
                "are kept out of this list (a deck named like a copy counts as one): "
                "<a href='/history#backups'>see them under History</a>.</p>"
                if hidden
                else ""
            )
            + f"<div class='panel'>{open_form}</div>"
        )
        return page(
            "My decks",
            body,
            sub=sub,
            sid=sid,
            two_pane=True,
            current="/decks",
            scripts=True,
            csp=DECK_CSP,  # the covers are Scryfall images
            deck_css=True,
            extra_scripts=("decks.js",),
        )

    @server.custom_route("/api/decks/mine", methods=["GET"], include_in_schema=False)
    async def my_decks_json(request: Request) -> Response:
        """The signed-in member's deck list for the pages' placeholders (decks.js): the rows, plus
        the HTML the page itself would have rendered (``shape=list`` with the /decks controls'
        q, order, view and folder; ``shape=recent`` for the home panel) so there is one renderer.
        Browser session only; a GET that changes nothing needs no CSRF token."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse(
                {"ok": False, "error": "unauthenticated"}, 401, headers={"Cache-Control": "no-store"}
            )
        qp = request.query_params
        shape = "recent" if qp.get("shape") == "recent" else "list"
        q, order, view, folder = list_query(qp)
        try:
            rows = await asyncio.wait_for(decks.list_decks(sub), DECKS_JSON_TIMEOUT)
        except DeckError as exc:
            rows, problem = None, (exc.kind, str(exc))
        except TimeoutError:
            rows, problem = None, ("unavailable", "Archidekt did not answer in time.")
        else:
            problem = None
        fetched_at = decks.decks_fetched_at(sub)
        out: dict[str, Any] = {
            "ok": problem is None,
            "shape": shape,
            "fetched_at": datetime.fromtimestamp(fetched_at, UTC).isoformat() if fetched_at else None,
        }
        if problem is not None:
            out["error"], out["message"] = problem
        if shape == "recent":
            from .home import recent_panel_inner

            recent = sorted(rows or [], key=lambda d: d.get("updated_at") or "", reverse=True)
            covers = covers_for(recent, state.db.deck_covers([str(d["id"]) for d in recent]))
            out["html"] = recent_panel_inner(None if problem else recent, covers)
            out["count"] = len(recent)
        else:
            shown, folders, total = arrange_decks(rows or [], q=q, order=order, folder=folder)
            covers = covers_for(shown, state.db.deck_covers([str(d["id"]) for d in shown]))
            out["html"] = (
                f"<p class='notice error'>{_esc(problem[1])}</p>" if problem else ""
            ) + deck_list_html(shown, covers=covers, q=q, view=view)
            out["count"], out["total"], out["folders"] = len(shown), total, folders
        for d in rows or []:
            d.setdefault("url", f"https://archidekt.com/decks/{d['id']}")
        out["decks"] = rows or []
        return JSONResponse(out, headers={"Cache-Control": "no-store"})

    @server.custom_route("/decks/open", methods=["GET"], include_in_schema=False)
    async def open_deck(request: Request) -> Response:
        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
        ref = (request.query_params.get("ref") or "").strip()
        try:
            from .decks import _clean_deck_id

            deck_id = _clean_deck_id(ref)
        except DeckError:
            return RedirectResponse("/decks?err=bad_ref", status_code=303)
        return RedirectResponse(f"/decks/{deck_id}", status_code=303)

    # -- new deck -------------------------------------------------------------
    def new_deck_form(values: dict[str, str], error: str = "") -> str:
        kind = values.get("kind") if values.get("kind") in ("csv", "json") else "list"
        fmt_opts = "".join(
            f"<option value='{_esc(n)}'{' selected' if values.get('format', 'commander') == n else ''}>"
            f"{_esc(format_label(n))}</option>"
            for n in FORMAT_CHOICES
        )
        return (
            (f"<p class='notice error'>{_esc(error)}</p>" if error else "")
            + "<form method='post' action='/decks/new' class='panel newdeck'>"
            f"<input type='hidden' name='csrf' value='{_esc(values.get('csrf'))}'>"
            "<label for='name'>Deck name</label>"
            f"<input id='name' type='text' name='name' value='{_esc(values.get('name', ''))}' "
            "placeholder='Super awesome deck name 2000' required maxlength='120'>"
            f"<div class='field'><label for='format'>Format</label><select id='format' "
            f"name='format'>{fmt_opts}"
            "</select></div>"
            "<label class='switch'><input type='checkbox' name='private' value='1'"
            + (" checked" if values.get("private", "" if "name" in values else "1") else "")
            + "><span class='track'></span>Private <span class='muted small'>(the deck is only visible to "
            "you)</span></label>"
            "<label for='source'>Cards</label>"
            "<p class='muted small'>Paste a decklist (<code>1 Sol Ring</code>, <code>2x Opt (cmr) "
            "[Ramp]</code>, "
            "a <code># Sideboard</code> header), an Archidekt CSV export or this gateway's .json export, "
            "or choose a file (.txt, .csv or .json). Leave it empty for an empty deck.</p>"
            "<div class='field filepick'><span class='lbl'>From a file</span><label class='filebtn'>"
            "<input id='file' type='file' accept='.txt,.csv,.json,text/plain,text/csv,application/json' "
            "data-fill='source' data-kind='kind'><span class='btn'>Choose a file</span></label>"
            "<span class='fname' aria-live='polite'>No file chosen</span></div>"
            f"<textarea id='source' name='source' rows='12' placeholder='1 Sol Ring&#10;1 Arcane Signet'>"
            f"{_esc(values.get('source', ''))}</textarea>"
            "<div class='field'><label for='kind'>The text above is</label><select id='kind' name='kind'>"
            f"<option value='list'{' selected' if kind == 'list' else ''}>a decklist</option>"
            f"<option value='csv'{' selected' if kind == 'csv' else ''}>an Archidekt CSV export</option>"
            f"<option value='json'{' selected' if kind == 'json' else ''}>a gateway .json export</option>"
            "</select></div>"
            f"<div class='actions'><button class='primary'>{icon('plus')} Create deck</button>"
            "<a class='btn' href='/decks'>Cancel</a></div>"
            "<p class='muted small'>The deck is created on Archidekt straight away and opens here.</p>"
            "</form>"
        )

    @server.custom_route("/decks/new", methods=["GET"], include_in_schema=False)
    async def new_deck_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks/new")
        values = {"csrf": _csrf(s, sid) or ""}
        scan_ref = (request.query_params.get("scan_session") or "").strip()[:80]
        if scan_ref and state.scan is not None:
            # "New deck from these cards" on the scan page: the scan's list is the decklist.
            from .scan.service import ScanError

            try:
                sess = state.scan.find_session(sub, scan_ref)
                values["source"] = sess.get("decklist_text") or ""
                values["name"] = str(sess.get("name") or "")[:120]
            except ScanError:
                pass
        return page(
            "New deck",
            new_deck_form(values),
            sub=sub,
            sid=sid,
            current="/decks",
            extra_scripts=("filepick.js",),
        )

    @server.custom_route("/decks/new", methods=["POST"], include_in_schema=False)
    async def new_deck_post(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks/new")
        data = await form(request, limit=4_200_000)  # a deck .json export can reach a few MB
        if not check_csrf(sid, data):
            return page("New deck", EXPIRED, sub=sub, sid=sid, status=403)
        values = {**data, "csrf": _csrf(s, sid) or ""}
        source = data.get("source", "").strip()
        current_client.set(BROWSER_CLIENT_ID)
        try:
            p = await decks.propose_new_deck(
                sub,
                name=data.get("name", ""),
                deck_format=data.get("format", "commander"),
                cards=[] if not source else None,
                decklist_text=source if source and data.get("kind") not in ("csv", "json") else None,
                csv_text=source if source and data.get("kind") == "csv" else None,
                json_text=source if source and data.get("kind") == "json" else None,
                private=bool(data.get("private")),
            )
        except DeckError as exc:
            return page(
                "New deck",
                new_deck_form(values, str(exc)),
                sub=sub,
                sid=sid,
                status=400,
                current="/decks",
                extra_scripts=("filepick.js",),
            )
        return await apply_now(sub, p["proposal_id"], ok="created")

    # -- one deck ---------------------------------------------------------------
    @server.custom_route("/decks/{deck_id}", methods=["GET"], include_in_schema=False)
    async def deck_page(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}")
        try:
            deck = await decks.get_any_deck(sub, deck_id)
        except DeckError as exc:
            if busy := busy_response(exc, request, page, sub=sub, sid=sid):
                return busy
            return page(
                "Deck not found",
                f"<div class='panel'><p>{_esc(exc)}</p>"
                "<a class='btn' href='/decks'>Back to my decks</a></div>",
                sub=sub,
                sid=sid,
                status=404 if exc.kind == "not_found" else 400,
            )
        link = state.db.get_link(sub)
        own = bool(link and deck.owner and deck.owner.lower() == str(link["archidekt_username"]).lower())
        stats = deck_stats.compute(deck)
        art = featured(deck)
        if own and art is not None:  # the deck list shows covers for the member's own decks
            state.db.save_deck_cover(deck.id, art.scryfall_uid, art.name, owner_sub=sub)
        notice = ""
        qp = request.query_params
        ok_code, err_code = qp.get("ok"), qp.get("err")
        if ok_code in DECK_OK_MESSAGES:
            notice = f"<p class='notice ok'>{_esc(DECK_OK_MESSAGES[ok_code])}</p>"
        elif err_code in DECK_ERR_MESSAGES:
            # Only known codes; the text is fixed here, so a link cannot choose the words.
            notice = f"<p class='notice error'>{_esc(DECK_ERR_MESSAGES[err_code])}</p>"
        # Archidekt marks each deck card with the copies the signed-in member owns ("owned" on the
        # card when the deck is read with their session); the gateway's green dot is that number.
        owned = {c.name.lower(): c.owned for c in deck.cards if c.owned} if link else None
        body = deck_page_html(
            deck,
            stats,
            own=own,
            csrf=_csrf(s, sid),
            writes_enabled=s.writes_enabled,
            owned=owned or None,
            view=qp.get("view") or "text",
            group=qp.get("group") or "category",
            sort=qp.get("sort") or "name",
            q=(qp.get("q") or "").strip()[:80],
            notice=notice,
        )
        return page(
            deck.name or f"Deck {deck.id}",
            body,
            sub=sub,
            sid=sid,
            two_pane=True,
            current="/decks",
            scripts=True,
            csp=DECK_CSP,
            heading=False,
            deck_css=True,
        )

    @server.custom_route("/decks/{deck_id}/clone", methods=["POST"], include_in_schema=False)
    async def clone_deck(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}")
        data = await form(request)
        if not check_csrf(sid, data):
            return page("Deck", EXPIRED, sub=sub, sid=sid, status=403)
        current_client.set(BROWSER_CLIENT_ID)
        try:
            p = await decks.propose_clone(sub, deck_id, data.get("name") or None)
        except DeckError as exc:
            code = exc.kind if exc.kind in DECK_ERR_MESSAGES else "invalid"
            return RedirectResponse(f"/decks/{deck_id}?err={code}", status_code=303)
        return await apply_now(sub, p["proposal_id"], ok="created")

    # -- deck settings (details proposal) ---------------------------------------
    def settings_form(
        deck: Deck,
        csrf: str | None,
        values: dict[str, str] | None = None,
        error: str = "",
        *,
        folders: dict[str, Any] | None = None,
        ok: str = "",
    ) -> str:
        v = values or {}
        fmt_current = v.get("deck_format", deck.format or "")
        description = v.get("description", deck.description or "")

        def sel(flag: bool) -> str:
            return " selected" if flag else ""

        fmt_opts = "".join(
            f"<option value='{_esc(n)}'{sel(fmt_current == n)}>{_esc(format_label(n))}</option>"
            for n in FORMAT_CHOICES
        )
        bracket_current = v.get("edh_bracket", str(deck.edh_bracket or ""))
        brackets = {
            "": "No bracket set",
            "1": "Exhibition (1)",
            "2": "Core (2)",
            "3": "Upgraded (3)",
            "4": "Optimized (4)",
            "5": "cEDH (5)",
        }
        bracket_opts = "".join(
            f"<option value='{k}'{' selected' if bracket_current == k else ''}>{_esc(label)}</option>"
            for k, label in brackets.items()
        )
        # a submitted form carries no key for an unchecked box, so with values a missing key is off
        private = v.get("private", "") if values else ("1" if deck.private else "")
        unlisted = v.get("unlisted", "") if values else ("1" if deck.unlisted else "")
        did = _esc(deck.id)
        cats = "".join(
            f"<li><span class='name'>{_esc(c.get('name'))}</span>"
            + ("<span class='badge warn'>premier</span>" if c.get("isPremier") else "")
            + ("" if c.get("includedInDeck", True) else "<span class='badge'>not in deck</span>")
            + ("" if c.get("includedInPrice", True) else "<span class='badge'>not in price</span>")
            + "</li>"
            for c in deck.categories
            if isinstance(c.get("name"), str)
        )
        return (
            (f"<p class='notice error'>{_esc(error)}</p>" if error else "")
            + (
                f"<p class='notice ok'>{_esc(SETTINGS_OK_MESSAGES[ok])}</p>"
                if ok in SETTINGS_OK_MESSAGES
                else ""
            )
            + f"<form method='post' action='/decks/{did}/settings' class='panel settings'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            "<h2>Main settings</h2>"
            "<label for='name'>Deck name</label>"
            f"<input id='name' type='text' name='name' value='{_esc(v.get('name', deck.name))}' "
            "required maxlength='200'>"
            f"<div class='field'><label for='deck_format'>Format</label><select id='deck_format' "
            "name='deck_format'>"
            f"{fmt_opts}</select></div>"
            "<div class='field'><label for='edh_bracket'>Commander bracket</label>"
            f"<select id='edh_bracket' name='edh_bracket'>{bracket_opts}</select>"
            "<p class='muted small'>Your own bracket. It does not influence the estimated bracket on "
            "the deck page."
            "</p></div>"
            "<label for='description'>Description</label>"
            f"<textarea id='description' name='description' rows='10'>{_esc(description)}"
            "</textarea>"
            "<label class='switch'><input type='checkbox' name='private' value='1'"
            + (" checked" if private else "")
            + "><span class='track'></span>Private <span class='muted small'>(the deck is only "
            "visible to you)"
            "</span></label>"
            "<label class='switch'><input type='checkbox' name='unlisted' value='1'"
            + (" checked" if unlisted else "")
            + "><span class='track'></span>Unlisted <span class='muted small'>(viewable with a "
            "direct link, but "
            "not shown in lists)</span></label>"
            f"<div class='actions'><button class='primary'>{icon('check')} Save changes</button>"
            f"<a class='btn' href='/decks/{did}'>Cancel</a></div>"
            "<p class='muted small'>Saved to Archidekt straight away; a snapshot from just before is "
            "kept under History.</p></form>"
            "<section class='panel' id='categories'><h2>Categories</h2>"
            f"<ul class='plain plist'>{cats or '<li class=muted>No categories yet.</li>'}</ul>"
            "<p class='muted small'>Cards are moved between categories in the editor; a new category "
            "is created "
            "by typing its name there. Renaming, deleting and the premier / in-deck / in-price flags are not "
            "available through the gateway yet.</p></section>"
            + cover_section(deck, csrf)
            + tags_section(deck, csrf)
            + folder_section(deck, csrf, folders)
            + "<section class='panel danger' id='delete'><h2>Delete this deck</h2>"
            "<p class='muted small'>Deletes the deck on Archidekt after you type its name. A snapshot is "
            "kept under History"
            + (", and a copy in your backup folder on Archidekt" if s.archidekt_backups else "")
            + ".</p>"
            f"<a class='btn btn-danger' href='/decks/{did}/delete'>{icon('trash')} Delete deck…</a></section>"
        )

    def cover_section(deck: Deck, csrf: str | None) -> str:
        """Pick the deck's cover image from its cards (Archidekt's "deck image"), or let Archidekt pick."""
        did = _esc(deck.id)
        chosen = (featured_scryfall_id(deck.featured) or "").lower()
        current = next((c for c in deck.cards if c.scryfall_uid.lower() == chosen), None) if chosen else None
        preview = card_image(current, "art_crop") if current else None
        seen: set[str] = set()
        options = []
        for c in sorted(deck.cards, key=lambda c: ("Commander" not in c.categories, c.name.lower())):
            uid = c.scryfall_uid.lower()
            if not uid or uid in seen or not card_image(c):
                continue
            seen.add(uid)
            label = c.name + (" (commander)" if "Commander" in c.categories else "")
            options.append(
                f"<option value='{_esc(uid)}'{' selected' if uid == chosen else ''}>{_esc(label)}</option>"
            )
        return (
            "<section class='panel coverbox' id='cover'><h2>Cover image</h2>"
            f"<form method='post' action='/decks/{did}/cover' class='coverform'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            + (
                f"<img class='coverart' src='{_esc(preview)}' alt='Current cover: {_esc(current.name)}'>"
                if preview and current
                else "<div class='coverart none'><span class='muted small'>Archidekt picks the image"
                "</span></div>"
            )
            + "<div class='field grow'><label for='cover_card'>Card whose art fronts the deck</label>"
            "<span class='sel'><select id='cover_card' name='card'>"
            f"<option value=''{'' if chosen else ' selected'}>Automatic (Archidekt picks)</option>"
            + "".join(options)
            + "</select></span></div>"
            f"<button class='primary'>{icon('image')} Set cover</button></form>"
            "<p class='muted small'>Shown on the deck page, in your deck lists and on Archidekt.</p>"
            "</section>"
        )

    def tags_section(deck: Deck, csrf: str | None) -> str:
        did = _esc(deck.id)
        csrf_in = f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        items = "".join(
            f"<li><span class='pill'>{_esc(r.get('name'))}</span>"
            f"<form method='post' action='/decks/{did}/tags' class='inline'>{csrf_in}"
            "<input type='hidden' name='action' value='remove'>"
            f"<input type='hidden' name='relation_id' value='{int(r['id'])}'>"
            f"<button class='mini' aria-label='Remove tag {_esc(r.get('name'))}'>{icon('x')}</button>"
            "</form></li>"
            for r in deck.tag_relations
            if isinstance(r.get("id"), int) and r.get("name")
        )
        return (
            f"<section class='panel tagbox' id='tags'><h2>Deck tags</h2>"
            f"<ul class='plain taglist'>{items or '<li class=muted>No deck tags yet.</li>'}</ul>"
            f"<form method='post' action='/decks/{did}/tags' class='addtag'>{csrf_in}"
            "<input type='hidden' name='action' value='add'>"
            "<div class='field grow'><label for='tagname'>Add a tag</label>"
            "<input id='tagname' type='text' name='name' maxlength='40' placeholder='Example: budget' "
            "required>"
            f"</div><button>{icon('tag')} Add tag</button></form>"
            "<p class='muted small'>Tags are Archidekt's public deck tags: an existing tag of that name is "
            "reused, otherwise it is created.</p></section>"
        )

    def folder_section(deck: Deck, csrf: str | None, folders: dict[str, Any] | None) -> str:
        did = _esc(deck.id)
        if not folders:
            return (
                "<section class='panel' id='folder'><h2>Folder</h2><p class='muted'>Your folders could not "
                "be read from Archidekt right now.</p></section>"
            )
        current = deck.parent_folder if deck.parent_folder is not None else folders["root_id"]
        opts = "".join(
            f"<option value='{f['id']}'{' selected' if f['id'] == current else ''}>"
            f"{_esc(INDENT * f['depth'] + f['name'])}</option>"
            for f in folders["folders"]
        )
        return (
            f"<section class='panel folderbox' id='folder'><h2>Folder</h2>"
            f"<form method='post' action='/decks/{did}/move' class='moveform'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            "<div class='field grow'><label for='folder_id'>This deck sits in</label>"
            f"<span class='sel'><select id='folder_id' name='folder_id'>{opts}</select></span></div>"
            f"<button>{icon('folder')} Move</button></form>"
            "<p class='muted small'>Folders are created and renamed from <a href='/folders'>My decks "
            "› Folders</a>.</p></section>"
        )

    async def folders_or_none(sub: str) -> dict[str, Any] | None:
        try:
            return await decks.folders(sub)
        except DeckError:
            return None

    def settings_problem(
        request: Request, deck_id: str, exc: DeckError, sub: str, sid: str | None
    ) -> Response:
        if busy := busy_response(exc, request, page, sub=sub, sid=sid):
            return busy
        return page(
            "Cannot edit this deck",
            f"<div class='panel'><p>{_esc(exc)}</p>"
            f"<a class='btn' href='/decks/{_esc(deck_id)}'>Back</a></div>",
            sub=sub,
            sid=sid,
            status={"not_found": 404, "forbidden": 403}.get(exc.kind, 400),
        )

    @server.custom_route("/decks/{deck_id}/settings", methods=["GET"], include_in_schema=False)
    async def settings_page(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/settings")
        try:
            deck = await decks.get_own_deck(sub, deck_id)
        except DeckError as exc:
            return settings_problem(request, deck_id, exc, sub, sid)
        ok = request.query_params.get("ok") or ""
        return page(
            f"Deck settings: {deck.name}",
            settings_form(deck, _csrf(s, sid), folders=await folders_or_none(sub), ok=ok),
            sub=sub,
            sid=sid,
            current="/decks",
            csp=DECK_CSP,
            deck_css=True,
        )

    async def hand_action(request: Request, deck_id: str, run: Any, *, ok: str, anchor: str) -> Response:
        """One of the settings page's own forms (cover, tags, folder): the member's click is the
        approval. On success the settings page reopens with a notice; a refusal re-renders it with
        the message next to the form."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/settings")
        data = await form(request)
        if not check_csrf(sid, data):
            return page("Deck settings", EXPIRED, sub=sub, sid=sid, status=403)
        current_client.set(BROWSER_CLIENT_ID)
        try:
            code = await run(sub, data) or ok
        except DeckError as exc:
            try:
                deck = await decks.get_own_deck(sub, deck_id)
            except DeckError as again:
                return settings_problem(request, deck_id, again, sub, sid)
            return page(
                f"Deck settings: {deck.name}",
                settings_form(deck, _csrf(s, sid), error=str(exc), folders=await folders_or_none(sub)),
                sub=sub,
                sid=sid,
                status=400,
                current="/decks",
                csp=DECK_CSP,
                deck_css=True,
            )
        return RedirectResponse(f"/decks/{_esc(deck_id)}/settings?ok={code}#{anchor}", status_code=303)

    @server.custom_route("/decks/{deck_id}/cover", methods=["POST"], include_in_schema=False)
    async def cover_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]

        async def run(sub: str, data: dict[str, str]) -> str:
            uid = (data.get("card") or "").strip() or None
            await decks.set_cover(sub, deck_id, uid)
            if uid:
                deck = await decks.get_deck(sub, deck_id)
                card = next((c for c in deck.cards if c.scryfall_uid.lower() == uid.lower()), None)
                state.db.save_deck_cover(deck.id, uid.lower(), card.name if card else "", owner_sub=sub)
            return "cover" if uid else "cover_auto"

        return await hand_action(request, deck_id, run, ok="cover", anchor="cover")

    @server.custom_route("/decks/{deck_id}/tags", methods=["POST"], include_in_schema=False)
    async def tags_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]

        async def run(sub: str, data: dict[str, str]) -> str:
            if data.get("action") == "remove":
                rid = data.get("relation_id") or ""
                if not re.fullmatch(r"[0-9]{1,12}", rid):
                    raise DeckError("invalid", "That tag could not be identified.")
                await decks.remove_tag(sub, deck_id, int(rid))
                return "tag_removed"
            await decks.add_tag(sub, deck_id, data.get("name") or "")
            return "tag_added"

        return await hand_action(request, deck_id, run, ok="tag_added", anchor="tags")

    @server.custom_route("/decks/{deck_id}/move", methods=["POST"], include_in_schema=False)
    async def move_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]

        async def run(sub: str, data: dict[str, str]) -> str:
            fid = (data.get("folder_id") or "").strip()
            if not re.fullmatch(r"[0-9]{1,12}", fid):
                raise DeckError("invalid", "Pick a folder.")
            await decks.move_deck(sub, deck_id, int(fid))
            return "moved"

        return await hand_action(request, deck_id, run, ok="moved", anchor="folder")

    # -- delete a deck (hand action, typed-name confirmation) -----------------------------------
    def delete_form(deck: Deck, csrf: str | None, error: str = "") -> str:
        did = _esc(deck.id)
        n = sum(c.quantity for c in deck.cards if deck.in_deck(c))
        undo = (
            "A snapshot of the deck is kept under History"
            + (
                ", and a private copy is made in your backup folder on Archidekt first"
                if s.archidekt_backups
                else ""
            )
            + ". Archidekt itself has no undo for a deleted deck."
        )
        return (
            (f"<p class='notice error'>{_esc(error)}</p>" if error else "")
            + f"<form method='post' action='/decks/{did}/delete' class='panel danger deleteform'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            f"<h2>Delete “{_esc(deck.name)}”?</h2>"
            f"<p>This deletes the deck ({n} cards) from your Archidekt account. {undo}</p>"
            "<label for='typed'>Type the deck's name to confirm</label>"
            f"<input id='typed' type='text' name='name' autocomplete='off' required maxlength='200' "
            f"placeholder='{_esc(deck.name)}'>"
            f"<div class='actions'><button class='danger'>{icon('trash')} Delete this deck</button>"
            f"<a class='btn' href='/decks/{did}'>Keep it</a></div></form>"
        )

    @server.custom_route("/decks/{deck_id}/delete", methods=["GET"], include_in_schema=False)
    async def delete_page(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/delete")
        try:
            deck = await decks.get_own_deck(sub, deck_id)
        except DeckError as exc:
            return settings_problem(request, deck_id, exc, sub, sid)
        return page(
            f"Delete {deck.name}",
            delete_form(deck, _csrf(s, sid)),
            sub=sub,
            sid=sid,
            current="/decks",
            deck_css=True,
        )

    @server.custom_route("/decks/{deck_id}/delete", methods=["POST"], include_in_schema=False)
    async def delete_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/delete")
        data = await form(request)
        if not check_csrf(sid, data):
            return page("Delete deck", EXPIRED, sub=sub, sid=sid, status=403)
        current_client.set(BROWSER_CLIENT_ID)
        try:
            await decks.delete_deck(sub, deck_id, data.get("name") or "")
        except DeckError as exc:
            try:
                deck = await decks.get_own_deck(sub, deck_id)
            except DeckError as again:
                return settings_problem(request, deck_id, again, sub, sid)
            return page(
                f"Delete {deck.name}",
                delete_form(deck, _csrf(s, sid), str(exc)),
                sub=sub,
                sid=sid,
                status=400,
                current="/decks",
                deck_css=True,
            )
        return RedirectResponse("/decks?ok=deleted", status_code=303)

    # -- folders (create, rename) ---------------------------------------------------------------
    def folders_form(folders: dict[str, Any] | None, csrf: str | None, error: str = "", ok: str = "") -> str:
        csrf_in = f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        if not folders:
            return (
                "<div class='panel'><p>Your folders could not be read from Archidekt right now.</p>"
                "<a class='btn' href='/decks'>Back to My decks</a></div>"
            )
        rows = folders["folders"]
        tree = "".join(
            f"<li style='--depth:{f['depth']}'><span class='name'>{icon('folder')} {_esc(f['name'])}</span>"
            + ("<span class='badge'>private</span>" if f["private"] else "")
            + (f"<a class='btn' href='/decks?folder={_esc(f['name'])}'>Show decks</a>" if f["depth"] else "")
            + "</li>"
            for f in rows
        )
        parent_opts = "".join(
            f"<option value='{f['id']}'>{_esc(INDENT * f['depth'] + f['name'])}</option>" for f in rows
        )
        rename_opts = "".join(
            f"<option value='{f['id']}'>{_esc(INDENT * (f['depth'] - 1) + f['name'])}</option>"
            for f in rows
            if f["depth"]
        )
        return (
            (f"<p class='notice error'>{_esc(error)}</p>" if error else "")
            + (f"<p class='notice ok'>{_esc(LIST_OK_MESSAGES[ok])}</p>" if ok in LIST_OK_MESSAGES else "")
            + f"<section class='panel'><h2>Your folders</h2><ul class='plain plist foldertree'>{tree}</ul>"
            "<p class='muted small'>A deck is moved between folders from its settings page.</p></section>"
            f"<form method='post' action='/folders' class='panel folderform'>{csrf_in}"
            "<input type='hidden' name='action' value='create'><h2>New folder</h2>"
            "<div class='row'><div class='field grow'><label for='fname'>Name</label>"
            "<input id='fname' type='text' name='name' maxlength='100' required></div>"
            "<div class='field'><label for='fparent'>Inside</label><span class='sel'>"
            f"<select id='fparent' name='parent_id'>{parent_opts}</select></span></div></div>"
            f"<div class='actions'><button class='primary'>{icon('plus')} Create folder</button></div></form>"
            + (
                f"<form method='post' action='/folders' class='panel folderform'>{csrf_in}"
                "<input type='hidden' name='action' value='rename'><h2>Rename a folder</h2>"
                "<div class='row'><div class='field'><label for='rfolder'>Folder</label><span class='sel'>"
                f"<select id='rfolder' name='folder_id'>{rename_opts}</select></span></div>"
                "<div class='field grow'><label for='rname'>New name</label>"
                "<input id='rname' type='text' name='name' maxlength='100' required></div></div>"
                f"<div class='actions'><button>{icon('edit')} Rename</button></div></form>"
                if rename_opts
                else ""
            )
            + "<p class='muted small'>Folders live on Archidekt; deleting one is done there.</p>"
        )

    @server.custom_route("/folders", methods=["GET"], include_in_schema=False)
    async def folders_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/folders")
        _rows, problem, failure = await my_decks(sub)
        if failure is not None and (busy := busy_response(failure, request, page, sub=sub, sid=sid)):
            return busy
        if problem == "not_linked":
            return page("Folders", link_prompt(), sub=sub, sid=sid, current="/decks")
        ok = request.query_params.get("ok") or ""
        return page(
            "Folders",
            folders_form(await folders_or_none(sub), _csrf(s, sid), ok=ok),
            sub=sub,
            sid=sid,
            current="/decks",
            deck_css=True,
        )

    @server.custom_route("/folders", methods=["POST"], include_in_schema=False)
    async def folders_post(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/folders")
        data = await form(request)
        if not check_csrf(sid, data):
            return page("Folders", EXPIRED, sub=sub, sid=sid, status=403)
        current_client.set(BROWSER_CLIENT_ID)
        try:
            if data.get("action") == "rename":
                fid = (data.get("folder_id") or "").strip()
                if not re.fullmatch(r"[0-9]{1,12}", fid):
                    raise DeckError("invalid", "Pick a folder to rename.")
                await decks.rename_folder(sub, int(fid), data.get("name") or "")
                ok = "folder_renamed"
            else:
                pid = (data.get("parent_id") or "").strip()
                parent = int(pid) if re.fullmatch(r"[0-9]{1,12}", pid) else None
                await decks.create_folder(sub, data.get("name") or "", parent)
                ok = "folder_created"
        except DeckError as exc:
            return page(
                "Folders",
                folders_form(await folders_or_none(sub), _csrf(s, sid), error=str(exc)),
                sub=sub,
                sid=sid,
                status=400,
                current="/decks",
                deck_css=True,
            )
        return RedirectResponse(f"/folders?ok={ok}", status_code=303)

    @server.custom_route("/decks/{deck_id}/settings", methods=["POST"], include_in_schema=False)
    async def settings_post(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/settings")
        data = await form(request, limit=100_000)
        if not check_csrf(sid, data):
            return page("Deck settings", EXPIRED, sub=sub, sid=sid, status=403)
        try:
            deck = await decks.get_own_deck(sub, deck_id)
        except DeckError as exc:
            return page(
                "Deck settings", f"<p class='notice error'>{_esc(exc)}</p>", sub=sub, sid=sid, status=400
            )
        details: dict[str, Any] = {
            "name": data.get("name", "").strip(),
            "description": data.get("description", "").replace("\r\n", "\n"),
            "private": bool(data.get("private")),
            "unlisted": bool(data.get("unlisted")),
        }
        if data.get("deck_format"):
            details["deck_format"] = data["deck_format"]
        bracket = data.get("edh_bracket", "")
        details["edh_bracket"] = int(bracket) if re.fullmatch(r"[1-5]", bracket) else None
        current_client.set(BROWSER_CLIENT_ID)
        try:
            p = await decks.propose_deck_details(sub, deck.id, details)
        except DeckError as exc:
            return page(
                f"Deck settings: {deck.name}",
                settings_form(deck, _csrf(s, sid), data, str(exc)),
                sub=sub,
                sid=sid,
                status=400,
                current="/decks",
                deck_css=True,
            )
        return await apply_now(sub, p["proposal_id"], ok="saved", deck_id=deck.id)

    # -- browser editor ---------------------------------------------------------
    @server.custom_route("/decks/{deck_id}/edit", methods=["GET"], include_in_schema=False)
    async def edit_page(request: Request) -> Response:
        """The deck editor: quantities, categories, finishes, printings and additions are saved to
        Archidekt in one go (as one proposal applied at once, with its snapshot; a big removal asks
        first). Own decks only; the script does the work, the
        server hands it the deck, the categories and anything to prefill (a scan session or a
        Quick add name)."""
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/edit")
        try:
            deck = await decks.get_own_deck(sub, deck_id)
        except DeckError as exc:
            if busy := busy_response(exc, request, page, sub=sub, sid=sid):
                return busy
            return page(
                "Cannot edit this deck",
                f"<div class='panel'><p>{_esc(exc)}</p>"
                f"<a class='btn' href='/decks/{_esc(deck_id)}'>Back to the deck</a></div>",
                sub=sub,
                sid=sid,
                status={"not_found": 404, "forbidden": 403}.get(exc.kind, 400),
            )

        def card_json(c: Any) -> dict[str, Any]:
            return {
                "name": c.name,
                "quantity": c.quantity,
                "categories": c.categories,
                "modifier": c.modifier or "Normal",
                "set_code": c.set_code,
                "collector_number": c.collector_number,
                "image": card_image(c, "small"),
                "mana_cost": c.mana_cost,
                "price": c.price,
                "type_line": c.type_line,
                "oracle_text": c.oracle_text,
                "pt": f"{c.power}/{c.toughness}" if c.power or c.toughness else "",
                "loyalty": c.loyalty,
                "faces": [
                    {
                        "name": f["name"],
                        "mana": f["mana_cost"],
                        "type": f["type_line"],
                        "text": f["text"],
                        "pt": f"{f['power']}/{f['toughness']}" if f["power"] or f["toughness"] else "",
                        "loyalty": f["loyalty"],
                    }
                    for f in c.faces
                ],
                "in_deck": deck.in_deck(c),
                "zone": "main" if deck.in_deck(c) else "side",
                "auto_category": auto_category(c),
            }

        grouped = cards_by_category(deck)
        groups = [
            {"name": name, "total": sum(c.quantity for c in cards), "cards": [card_json(c) for c in cards]}
            for name, cards in grouped
        ]
        categories = [c["name"] for c in deck.categories if isinstance(c.get("name"), str)]
        for name, _cards in grouped:
            if name not in categories:
                categories.append(name)
        prefill: list[dict[str, Any]] = []
        quick = (request.query_params.get("add") or "").strip()[:200]
        if quick:
            prefill.append({"action": "add", "card_name": quick, "quantity": 1})
        picker = ""
        scan_ref = (request.query_params.get("scan_session") or "").strip()[:80]
        if state.scan is not None:
            from .scan.service import ScanError

            if scan_ref:
                try:
                    prefill += list(state.scan.find_session(sub, scan_ref).get("changes") or [])
                except ScanError:
                    pass
            sessions = state.scan.list_sessions(sub)[:12]
            if sessions:
                opts = "".join(
                    f"<option value='{_esc(r['id'])}'" + (" selected" if r["id"] == scan_ref else "") + ">"
                    f"{_esc(r['name'])}</option>"
                    for r in sessions
                )
                picker = (
                    "<form method='get' class='scanpick field'>"
                    "<label for='scan_session'>Add cards from a scan</label>"
                    "<span class='sel'><select id='scan_session' name='scan_session'>"
                    f"<option value=''>none</option>{opts}</select></span>"
                    "<button>Load</button></form>"
                )
        config = {
            "deckId": deck.id,
            "deckName": deck.name,
            "csrf": _csrf(s, sid),
            "cards": [card_json(c) for c in deck.cards],
            "groups": groups,
            "categories": categories,
            "prefill": prefill,
            "canCategorise": True,
            "writesEnabled": s.writes_enabled,
            "maxChanges": 40,
            "sideCategory": deck.side_category(),
        }
        side_name = _esc(deck.side_category())
        body = (
            f"<script id='editor-config' type='application/json'>{_json_for_html(config)}</script>"
            "<div id='editor' class='editor'>"
            "<section class='panel edithead'><div class='row'><div class='info'>"
            f"<div class='kicker'>Editing</div><h1 class='deckname'>{_esc(deck.name)}</h1></div>"
            f"<a class='btn' href='/decks/{_esc(deck.id)}'>{icon('x')} Close editor</a></div>"
            + (
                ""
                if s.writes_enabled
                else "<p class='notice warn'>Deck writes are switched off on this gateway; "
                "your changes are kept as a proposal to apply once they are on.</p>"
            )
            + "</section>"
            "<div class='editbar' role='region' aria-label='Pending changes'>"
            "<button type='button' class='btn-primary review' disabled>"
            f"{icon('check')} <span class='label'>Save changes</span></button>"
            f"<button type='button' class='undo' disabled>{icon('undo')} Undo</button>"
            "<span class='count muted'>No changes yet</span>"
            + (
                # D-02: the extra copy on Archidekt is the member's choice per save; the gateway's own
                # snapshot (Restore under History) is always kept.
                "<label class='backup'><input type='checkbox' name='archidekt_backup' checked> "
                "Also keep a backup copy on Archidekt</label>"
                if s.archidekt_backups
                else ""
            )
            + "<p class='status' role='status'></p></div>"
            "<details class='panel pendingbox'><summary>Pending changes <b class='n'>0</b></summary>"
            "<div class='pending'></div><p class='muted small limit'></p></details>"
            # One search bar that adds (C-11, the "Search bar" option): chips pick where a card goes,
            # Enter adds it, "3 sol ring" adds three, and the printings of the highlighted card show
            # beside the list on wide screens (static/companion.js).
            "<section class='panel addbox'><h2>Add a card</h2>" + picker + "<form class='addcard'>"
            "<div class='targets' role='group' aria-label='Add to'>"
            "<button type='button' class='tchip on' data-cat='' data-zone='main' aria-pressed='true'>"
            "Auto</button>"
            + "".join(
                f"<button type='button' class='tchip' data-cat='{_esc(c)}' data-zone='main' "
                f"aria-pressed='false' title='The {_esc(c)} category'>{_esc(c)}</button>"
                # the side zone's own category has the zone chip below, not a second chip of the
                # same name (gate D10)
                for c in categories
                if c.casefold() != deck.side_category().casefold()
            )
            + f"<button type='button' class='tchip side' data-cat='' data-zone='side' aria-pressed='false' "
            f"aria-label='{side_name} zone' title='The {side_name} zone: kept outside the deck'>"
            f"{side_name}</button></div>"
            "<div class='field grow'><label for='addname'>Card name</label>"
            "<input id='addname' type='text' name='card' data-suggest='cards' data-suggest-submit "
            "data-suggest-rich data-suggest-qty placeholder='Type a card name; Enter adds one, "
            "“3 sol ring” adds three' autocomplete='off' required>"
            "<div class='addprints' hidden aria-label='Printings' role='group'></div></div>"
            f"<div class='field go'><button class='btn-primary'>{icon('plus')} Add</button>"
            "<label class='foil'><input type='checkbox' name='foil'> Foil</label></div>"
            "<input type='hidden' id='addcat' name='addcat' value=''>"
            "<input type='hidden' id='addzone' name='addzone' value='main'>"
            "<p class='addstatus muted small' role='status' aria-live='polite'></p></form>"
            "<details class='pastebox'><summary>Paste a list</summary>"
            "<form class='pastelist'><label for='pastetext'>One card per line, with a count in front "
            "(“2 Lightning Bolt”)</label>"
            "<textarea id='pastetext' name='text' rows='6' maxlength='20000' "
            "placeholder='4 Lightning Bolt&#10;1 Sol Ring'></textarea>"
            f"<div class='actions'><button class='btn-primary'>{icon('plus')} Add these cards</button>"
            "<span class='pastestatus muted small' role='status'></span></div></form></details>"
            "<ul class='erows added'></ul></section>"
            "<div class='cats existing'></div>"
            "<div class='picker' hidden></div>"
            "</div>"
            + "".join(
                f"<template id='icon-{n}'>{icon(n)}</template>"
                for n in ("minus", "plus", "more", "x", "swap")
            )
            + "<script src='/static/cardview.js' defer></script>"
            + "<script src='/static/companion.js' defer></script>"
        )
        return page(
            f"Edit {deck.name}",
            body,
            sub=sub,
            sid=sid,
            csp=EDITOR_CSP,
            heading=False,
            deck_css=True,
            two_pane=True,
            current="/decks",
        )

    @server.custom_route("/decks/{deck_id}/export", methods=["GET"], include_in_schema=False)
    async def export_page(request: Request) -> Response:
        from .decks import deck_to_archidekt_text, deck_to_text

        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/export")
        try:
            deck = await decks.get_any_deck(sub, deck_id)
        except DeckError as exc:
            if busy := busy_response(exc, request, page, sub=sub, sid=sid):
                return busy
            return page(
                "Deck not found", f"<div class='card'><p>{_esc(exc)}</p></div>", sub=sub, sid=sid, status=404
            )
        main = deck_to_text(deck)
        side = deck_to_text(deck, zone="side")
        arch = deck_to_archidekt_text(deck)
        did = _esc(deck.id)

        def block(key: str, title: str, blurb: str, text: str, rows: int) -> str:
            return (
                f"<div class='exportblock'><div class='head'><h2>{title}</h2>"
                f"<button type='button' class='btn small copybtn' data-copy='{key}'>"
                f"{icon('copy')} Copy</button></div>"
                f"<p class='muted small'>{blurb}</p>"
                f"<textarea id='{key}' rows='{rows}' readonly>{_esc(text)}</textarea></div>"
            )

        body = (
            f"<div class='card'><p><a href='/decks/{did}'>← {_esc(deck.name)}</a></p>"
            "<p class='muted small'>Copy a list below or download a file. The file buttons save to your "
            "Downloads folder in the app and in a browser.</p>"
            + block(
                "exp-arch",
                "Archidekt import text",
                "Every row with its printing, finish, categories and labels. Paste into Archidekt's "
                "Import dialog, or into the gateway's New deck page, to get the same deck back "
                "(sideboard and maybeboard included).",
                arch,
                min(40, arch.count(chr(10)) + 2),
            )
            + block(
                "exp-main",
                "Plain decklist",
                "Mainboard only, commander first. For Moxfield, MTGO, Arena or your assistant.",
                main,
                min(40, main.count(chr(10)) + 2),
            )
            + (
                block(
                    "exp-side",
                    "Sideboard and maybeboard",
                    "Rows Archidekt keeps outside the deck.",
                    side,
                    min(12, side.count(chr(10)) + 2),
                )
                if side
                else ""
            )
            + "<h2>Download</h2>"
            "<p class='muted small'>Archidekt text, CSV and the gateway's JSON import back here (New deck "
            "&rarr; from a file) or into Archidekt; Arena, MTGO and PDF are one-way.</p>"
            "<div class='actions'>"
            f"<a class='btn' href='/decks/{did}/export.archidekt.txt' download>Archidekt .txt</a>"
            f"<a class='btn' href='/decks/{did}/export.txt' download>Plain .txt</a>"
            f"<a class='btn' href='/decks/{did}/export.csv' download>.csv</a>"
            f"<a class='btn' href='/decks/{did}/export.json' download>.json</a>"
            f"<a class='btn' href='/decks/{did}/export.arena.txt' download>Arena .txt</a>"
            f"<a class='btn' href='/decks/{did}/export.dek' download>MTGO .dek</a>"
            f"<a class='btn' href='/decks/{did}/export.pdf' download>PDF</a>"
            "</div></div>"
        )
        return page(f"Export: {deck.name}", body, sub=sub, sid=sid, extra_scripts=("export.js",))

    @server.custom_route("/decks/{deck_id}/export.archidekt.txt", methods=["GET"], include_in_schema=False)
    async def export_archidekt_txt(request: Request) -> Response:
        """The deck in Archidekt's own import syntax (printing, finish, categories with their
        flags, labels, sideboard rows), for Archidekt's Import dialog or the gateway's New deck."""
        from .decks import deck_to_archidekt_text

        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
        try:
            deck = await decks.get_any_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return busy_text_response(exc) or Response(str(exc), 404)
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", deck.name or deck.id)[:60]
        return Response(
            deck_to_archidekt_text(deck) + "\n",
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}.archidekt.txt"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @server.custom_route("/decks/{deck_id}/export.txt", methods=["GET"], include_in_schema=False)
    async def export_txt(request: Request) -> Response:
        from .decks import deck_to_text

        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
        try:
            deck = await decks.get_any_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return busy_text_response(exc) or Response(str(exc), 404)
        text = deck_to_text(deck)
        side = deck_to_text(deck, zone="side")
        if side:
            text += "\n\n// Sideboard\n" + side
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", deck.name or deck.id)[:60]
        return Response(
            text + "\n",
            media_type="text/plain; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}.txt"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def _download(request: Request, ext: str, make: Any, media_type: str) -> Response:
        """One export-only file for a deck the member may read: ``make(deck)`` gives the body."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
        try:
            deck = await decks.get_any_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return busy_text_response(exc) or Response(str(exc), 404)
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", deck.name or deck.id)[:60]
        body = make(deck)
        return Response(
            body,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{name}{ext}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @server.custom_route("/decks/{deck_id}/export.arena.txt", methods=["GET"], include_in_schema=False)
    async def export_arena(request: Request) -> Response:
        """Arena import text (export only: Arena imports it; the gateway does not read it back)."""
        from .export_formats import to_arena

        return await _download(request, ".arena.txt", to_arena, "text/plain; charset=utf-8")

    @server.custom_route("/decks/{deck_id}/export.dek", methods=["GET"], include_in_schema=False)
    async def export_dek(request: Request) -> Response:
        """An MTGO .dek file (export only)."""
        from .export_formats import to_mtgo_dek

        return await _download(request, ".dek", to_mtgo_dek, "application/xml; charset=utf-8")

    @server.custom_route("/decks/{deck_id}/export.pdf", methods=["GET"], include_in_schema=False)
    async def export_pdf(request: Request) -> Response:
        """A printable PDF of the deck (export only)."""
        from .export_formats import to_pdf

        return await _download(request, ".pdf", to_pdf, "application/pdf")

    @server.custom_route("/decks/{deck_id}/export.json", methods=["GET"], include_in_schema=False)
    async def export_json(request: Request) -> Response:
        from .views import deck_out

        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
        try:
            deck = await decks.get_any_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return busy_json_response(exc) or JSONResponse(
                {"ok": False, "error": exc.kind, "message": str(exc)}, 404
            )
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", deck.name or deck.id)[:60]
        return JSONResponse(
            deck_out(deck),
            headers={
                "Content-Disposition": f'attachment; filename="{name}.json"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @server.custom_route("/decks/{deck_id}/export.csv", methods=["GET"], include_in_schema=False)
    async def export_csv(request: Request) -> Response:
        """A CSV with the column names Archidekt's own export uses, so the file round-trips
        through the gateway's CSV import and through Archidekt's."""
        import csv
        import io

        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
        try:
            deck = await decks.get_any_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return busy_text_response(exc) or Response(str(exc), 404)

        def cell(value: Any) -> str:
            # A cell starting like a formula is quoted the way spreadsheets expect, so a deck
            # owner's category or label cannot run as one when another member opens the export.
            s = str(value)
            return "'" + s if s[:1] in ("=", "+", "-", "@", "\t", "\r") else s

        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\r\n")
        w.writerow(
            [
                "Quantity",
                "Name",
                "Finish",
                "Edition Code",
                "Collector Number",
                "Category",
                "Secondary Categories",
                "Label",
                "Mana Value",
                "Mana Cost",
                "Types",
                "Rarity",
                "Price",
                "Scryfall ID",
            ]
        )
        for c in deck.cards:
            w.writerow(
                [
                    c.quantity,
                    cell(c.name),
                    c.modifier or "Normal",
                    c.set_code,
                    c.collector_number,
                    cell(c.categories[0]) if c.categories else "",
                    cell(",".join(cell(x) for x in c.categories[1:])),
                    cell(c.label),
                    "" if c.cmc is None else (int(c.cmc) if float(c.cmc).is_integer() else c.cmc),
                    cell(c.mana_cost),
                    cell(" ".join(c.supertypes + c.types)),
                    c.rarity,
                    "" if c.price is None else f"{c.price:.2f}",
                    c.scryfall_uid,
                ]
            )
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", deck.name or deck.id)[:60]
        return Response(
            buf.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="{name}.csv"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @server.custom_route("/decks/{deck_id}/report", methods=["POST"], include_in_schema=False)
    async def run_report(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}")
        data = await form(request)
        if not check_csrf(sid, data):
            return page(
                "Deck",
                "<p class='notice error'>This form expired. Reload and try again.</p>",
                sub=sub,
                sid=sid,
                status=403,
            )
        current_client.set(BROWSER_CLIENT_ID)
        try:
            report = await reports.run(sub, deck_id)
        except DeckError as exc:
            code = exc.kind if exc.kind in DECK_ERR_MESSAGES else "report_failed"
            return RedirectResponse(f"/decks/{deck_id}?err={code}", status_code=303)
        reused = "?reused=1" if report.get("reused") else ""
        return RedirectResponse(f"/history/reports/{_esc(report['report_id'])}{reused}", status_code=303)

    @server.custom_route("/decks/{deck_id}/playtest", methods=["GET"], include_in_schema=False)
    async def playtest_page(request: Request) -> Response:
        """Old links to the gateway's framed playtester go straight to Archidekt's own (D-13): a frame
        never carried the person's Archidekt sign-in, so private decks stayed empty in it."""
        deck_id = str(request.path_params["deck_id"])
        if not deck_id.isdigit():
            return RedirectResponse("/decks", status_code=303)
        return RedirectResponse(f"https://archidekt.com/playtester-v2/{deck_id}", status_code=303)

    @server.custom_route("/decks/{deck_id}/compare", methods=["GET"], include_in_schema=False)
    async def compare_page(request: Request) -> Response:
        """This deck against another: a preconstructed deck (the picker lists Archidekt's), any
        deck id or link, or a pasted list. The same comparison the assistant's compare_decks
        makes: what was taken out of the other deck, what was put in, what changed count, and
        the statistics' differences."""
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/compare")
        try:
            deck = await decks.get_any_deck(sub, deck_id)
        except DeckError as exc:
            if busy := busy_response(exc, request, page, sub=sub, sid=sid):
                return busy
            return page(
                "Deck not found",
                f"<div class='panel'><p>{_esc(exc)}</p>"
                "<a class='btn' href='/decks'>Back to my decks</a></div>",
                sub=sub,
                sid=sid,
                status=404 if exc.kind == "not_found" else 400,
            )
        qp = request.query_params
        other_ref = (qp.get("with") or "").strip()[:2000]
        paste = (qp.get("paste") or "").strip()[:20000]
        try:
            precons = await decks.precons(sub)
        except DeckError:
            precons = {}
        other: Deck | dict[str, int] | None = None
        other_name = ""
        error = ""
        status = 200
        if paste:
            try:
                cards = parse_decklist(paste)
                other = {c.name: c.quantity for c in cards if c.zone == "main"}
                other_name = "the pasted list"
            except DecklistError as exc:
                error, status = f"The pasted list could not be read: {_esc(exc)}", 400
        elif other_ref:
            try:
                # a precon picked by name (the suggestion list) is looked up in the precon listing
                precon_id = precon_by_label(precons, other_ref)
                other = await decks.get_any_deck(sub, str(precon_id) if precon_id else other_ref)
                other_name = other.name or f"deck {other.id}"
            except DeckError as exc:
                error, status = (
                    f"That deck could not be read: {_esc(exc)}",
                    400 if exc.kind != "not_found" else 404,
                )
        result = deck_stats.compare(other, deck) if other is not None else None
        body = compare_page_html(
            deck,
            other=other,
            other_name=other_name,
            other_ref=other_ref,
            paste=paste,
            result=result,
            precons=precons,
            error=error,
        )
        return page(
            f"Compare: {deck.name or deck.id}",
            body,
            sub=sub,
            sid=sid,
            status=status,
            two_pane=True,
            current="/decks",
            csp=DECK_CSP,
            scripts=True,
            heading=False,
            deck_css=True,
            extra_scripts=("compare.js",),
        )

    # -- history --------------------------------------------------------------
    @server.custom_route("/history", methods=["GET"], include_in_schema=False)
    async def history(request: Request) -> Response:
        """Proposals, snapshots and reports as one timeline grouped by day (or by deck), narrowed
        by deck, type, state, a "When" preset of UTC days and a search, 25 at a time. Each list is
        narrowed and paged in SQL, so a deck's own history keeps its older entries however busy
        the member's other decks are."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        query = read_query(request.query_params)
        deck_id = query["deck_id"] or None
        want = query["type"]
        states = [query["state"]] if query["state"] else None
        since = when_since(query["when"])
        # Each kind fetched up to the end of this page plus one: the lists merge by time, so the
        # page is sliced after the merge and the extra row says whether an older page exists.
        fetch = query["offset"] + PAGE + 1
        proposals = snapshots = reps = []
        if want in ("all", "changes"):
            proposals = decks.list_proposals(
                sub,
                full=True,
                deck_id=deck_id,
                states=states,
                search=query["q"] or None,
                since=since,
                limit=fetch,
            )
        if want in ("all", "snapshots") and not states:
            snapshots = decks.list_snapshots(
                sub, deck_id=deck_id, search=query["q"] or None, since=since, limit=fetch
            )
        if want in ("all", "reports") and not states:
            reps = reports.list(sub, deck_id, limit=fetch, search=query["q"] or None, since=since)
        client_ids = {r.get("created_by_client") for r in reps if r.get("created_by_client")}
        names = {cid: state.db.client_name(cid) for cid in client_ids if not cid.startswith("__")}
        csrf_in = f"<input type='hidden' name='csrf' value='{_esc(_csrf(s, sid))}'>"
        events = build_events(proposals, snapshots, reps, csrf_input=csrf_in, client_names=names)
        has_more = len(events) > query["offset"] + PAGE
        shown = events[query["offset"] : query["offset"] + PAGE]
        seen = {**state.db.history_decks(sub), **reports.decks_seen(sub)}
        # The backup copies the gateway made on Archidekt (D-03): kept out of Home and My decks,
        # listed here on the first, unfiltered page (and on a deck's own history).
        copies_panel = ""
        if (
            want == "all"
            and not query["q"]
            and not query["state"]
            and not query["when"]
            and not query["offset"]
        ):
            try:
                copies = await asyncio.wait_for(
                    decks.backup_copies(sub, deck_id=deck_id, wait=decks.deck_list_wait), DECKS_JSON_TIMEOUT
                )
            except (DeckError, TimeoutError):
                copies = []  # not linked, or Archidekt unavailable: the panel is simply absent
            copies_panel = backup_copies_html(copies, folder=s.archidekt_backup_folder, deck_id=deck_id)
        trend = ""
        if deck_id:
            series = reports.series(sub, deck_id)
            if len(series) >= 2:
                cards = [
                    f"<div class='tile'><b>{_esc(label)}</b>{_sparkline([p.get(key) for p in series])}"
                    f"<span>{_num(series[0].get(key))} → {_num(series[-1].get(key))}</span></div>"
                    for key, label in (
                        ("average_mana_value", "Avg MV"),
                        ("land_count", "Lands"),
                        ("price_total", "Price"),
                        ("salt_total", "Salt"),
                    )
                    if any(isinstance(p.get(key), (int, float)) for p in series)
                ]
                trend = (
                    f"<div class='card trend'><h2>Trend over {len(series)} reports</h2>"
                    f"<div class='tiles'>{''.join(cards)}</div></div>"
                )
        head = f"<p><a href='/decks/{_esc(deck_id)}'>← Back to the deck</a></p>" if deck_id else ""
        hint = "more below" if has_more else ""
        if shown:
            listing = (
                f"<section class='history'>{events_html(shown, group=query['group'], query=query)}</section>"
            )
            listing += pager_html(query, has_more=has_more)
        elif query["offset"]:
            listing = "<div class='card'><p>No older entries.</p></div>" + pager_html(query, has_more=False)
        elif is_filtered(query):
            listing = "<div class='card'><p>Nothing matches these filters.</p></div>"
        else:
            listing = (
                "<div class='card'><p>Nothing yet. Changes you or the assistant propose, the snapshots taken "
                "before a change is applied and deck reports appear here.</p></div>"
            )
        bar = filter_bar_html(query, seen, shown=len(shown), total_hint=hint)
        body = head + trend + bar + listing + copies_panel
        return page(
            "History" if not deck_id else "Deck history",
            body,
            sub=sub,
            sid=sid,
            current="/history",
            extra_css=HISTORY_CSS,
        )

    @server.custom_route("/history/restore", methods=["POST"], include_in_schema=False)
    async def restore(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        data = await form(request)
        if not check_csrf(sid, data):
            return page(
                "History",
                "<p class='notice error'>This form expired. Reload and try again.</p>",
                sub=sub,
                sid=sid,
                status=403,
            )
        current_client.set(BROWSER_CLIENT_ID)
        try:
            p = await decks.propose_restore(sub, data.get("snapshot_id", ""))
        except DeckError as exc:
            return page("History", f"<p class='notice error'>{_esc(exc)}</p>", sub=sub, sid=sid, status=400)
        return RedirectResponse(f"/proposals/{p['proposal_id']}", status_code=303)

    def _report_or_404(sub: str, rid: str) -> dict[str, Any] | None:
        try:
            return reports.get(sub, rid)
        except DeckError:
            return None

    @server.custom_route("/history/reports/{rid}", methods=["GET"], include_in_schema=False)
    async def report_page(request: Request) -> Response:
        """One stored report as a page a person reads: the deck, the headline numbers of the goldfish
        simulation with their confidence intervals, charts, what the simulation could not model,
        the validation verdict and the deck statistics; the service's raw text folded away."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        rid = request.path_params["rid"]
        r = _report_or_404(sub, rid)
        if r is None:
            return page(
                "Report",
                "<div class='card'><p>No such report for your account.</p></div>",
                sub=sub,
                sid=sid,
                status=404,
            )
        safe = _esc(rid)
        md = report_markdown(r)
        actions = (
            "<div class='form-actions'>"
            f"<a class='btn' href='/history/reports/{safe}/export.md' download>{icon('download')} Markdown"
            f"</a><a class='btn' href='/history/reports/{safe}/export.html' download>{icon('download')} "
            f"HTML page</a><button type='button' class='btn copybtn' data-copy='rep-md'>{icon('copy')} "
            "Copy as Markdown</button>"
            "</div>"
            f"<textarea id='rep-md' class='sr-only' readonly aria-label='The report as Markdown'>{_esc(md)}"
            "</textarea>"
        )
        body = report_body_html(
            r,
            stats_html=stats_strip(r.get("stats")),
            actions_html=actions,
            reused=request.query_params.get("reused") == "1",
        )
        return page(
            f"Report: {r['deck_name']}",
            body,
            sub=sub,
            sid=sid,
            heading=False,
            current="/history",
            extra_scripts=("export.js",),
            extra_css=REPORT_CSS,
        )

    def _report_file(r: dict[str, Any], ext: str, body: str, media_type: str) -> Response:
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", r.get("deck_name") or r["deck_id"])[:60]
        day = _when(r.get("taken_at"))[:10]
        return Response(
            body,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{name}-report-{day}{ext}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @server.custom_route("/history/reports/{rid}/export.md", methods=["GET"], include_in_schema=False)
    async def report_export_md(request: Request) -> Response:
        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        r = _report_or_404(sub, request.path_params["rid"])
        if r is None:
            return Response("No such report for your account.", 404)
        return _report_file(r, ".md", report_markdown(r), "text/markdown; charset=utf-8")

    @server.custom_route("/history/reports/{rid}/export.html", methods=["GET"], include_in_schema=False)
    async def report_export_html_file(request: Request) -> Response:
        """The report as one HTML file: inline styles and SVG, no scripts, nothing fetched."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        r = _report_or_404(sub, request.path_params["rid"])
        if r is None:
            return Response("No such report for your account.", 404)
        html_doc = report_export_html(r, stats_html=stats_strip(r.get("stats")), theme_css=VIZ_CSS)
        resp = _report_file(r, ".html", html_doc, "text/html; charset=utf-8")
        resp.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src data:"
        )
        return resp

    # -- activity ---------------------------------------------------------------
    @server.custom_route("/activity", methods=["GET"], include_in_schema=False)
    async def activity(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/activity")
        rows = state.db.audit_for_user(sub, 100)
        items = []
        for r in rows:
            detail = r.get("detail") if isinstance(r.get("detail"), dict) else {}
            # browser or app is decided by the client id, never by the app's chosen name
            who = actor_label(r.get("client_id"), detail.get("client_name"))
            extra = ", ".join(
                f"{k} {v}"
                for k, v in detail.items()
                if k in ("proposal_id", "deck_id", "via", "error", "archidekt_username", "by")
            )
            items.append(
                f"<li><span class='name'>{_esc(r['event'].replace('_', ' '))}</span>"
                + (f"<span class='badge'>{_esc(who)}</span>" if who else "")
                + f"<span class='muted small'>{_esc(extra)}</span>"
                f"<span class='when'>{_when(r['at'])}</span></li>"
            )
        body = (
            "<p class='muted small'>Everything done in your name on this gateway: sign-ins, which assistant "
            "proposed and applied what, links and unlinks, and anything an administrator did to your "
            "account.</p>"
            + (
                f"<div class='card'><ul class='plain plist'>{''.join(items)}</ul></div>"
                if items
                else "<div class='card'><p>No activity recorded yet.</p></div>"
            )
        )
        return page("My activity", body, sub=sub, sid=sid)

    # -- app shell: manifest, service worker, Android asset links ----------------
    @server.custom_route("/app.webmanifest", methods=["GET"], include_in_schema=False)
    async def manifest(_request: Request) -> Response:
        data = {
            "name": s.server_name,
            "short_name": s.server_name[:12],
            "start_url": "/decks",
            "scope": "/",
            "display": "standalone",
            "background_color": "#181818",
            "theme_color": "#111111",
            "icons": [
                {"src": "/static/gateway-icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/static/gateway-icon-512.png", "sizes": "512x512", "type": "image/png"},
            ],
            "shortcuts": [
                {"name": "My decks", "url": "/decks"},
                {"name": "Scan cards", "url": "/scan"},
                {"name": "Proposals", "url": "/proposals"},
            ],
        }
        return JSONResponse(
            data, media_type="application/manifest+json", headers={"Cache-Control": "public, max-age=3600"}
        )

    @server.custom_route("/sw.js", methods=["GET"], include_in_schema=False)
    async def service_worker(_request: Request) -> Response:
        # Network only: the pages are private and must never be served from a cache. When a page
        # can't be reached at all (no connection), answer with a plain offline page built here
        # instead of the browser's own error screen.
        js = (
            "self.addEventListener('install',()=>self.skipWaiting());"
            "self.addEventListener('activate',e=>e.waitUntil(self.clients.claim()));"
            "self.addEventListener('fetch',e=>{"
            "if(e.request.mode!=='navigate'||e.request.method!=='GET')return;"
            "e.respondWith(fetch(e.request).catch(()=>new Response(" + json.dumps(OFFLINE_PAGE) + ","
            "{status:503,headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store',"
            "'Content-Security-Policy':\"default-src 'none'; style-src 'unsafe-inline'; "
            "base-uri 'none'; frame-ancestors 'none'\"}})));"
            "});"
        )
        return Response(js, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @server.custom_route("/.well-known/assetlinks.json", methods=["GET"], include_in_schema=False)
    async def assetlinks(_request: Request) -> Response:
        raw = getattr(s, "android_assetlinks", None)
        if not raw:
            return JSONResponse([], headers={"Cache-Control": "public, max-age=3600"})
        try:
            data = json.loads(raw)
        except ValueError:
            return JSONResponse([], headers={"Cache-Control": "no-store"})
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=3600"})

    # Browsers, and apps that show a connector's icon from its site, ask for these at the root.
    @server.custom_route("/favicon.ico", methods=["GET"], include_in_schema=False)
    async def favicon(_request: Request) -> Response:
        return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon", headers=ICON_HEADERS)

    @server.custom_route("/apple-touch-icon.png", methods=["GET"], include_in_schema=False)
    async def touch_icon(_request: Request) -> Response:
        return FileResponse(STATIC_DIR / "gateway-icon-180.png", media_type="image/png", headers=ICON_HEADERS)

    @server.custom_route("/static/{path:path}", methods=["GET"], include_in_schema=False)
    async def static(request: Request) -> Response:
        rel = request.path_params["path"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", rel):
            return Response("not found", 404)
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve()) + "/") or not target.is_file():
            return Response("not found", 404)
        return FileResponse(
            target, headers={"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"}
        )


OFFLINE_PAGE = (
    "<!doctype html><html lang='en'><head><meta charset='utf-8'>"
    "<meta name='viewport' content='width=device-width, initial-scale=1'>"
    "<meta name='color-scheme' content='dark light'><title>You're offline</title>"
    "<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;"
    "font:16px/1.4 Lato,'Helvetica Neue',Arial,sans-serif;background:#181818;color:#e3e3e3;padding:1rem}"
    "@media (prefers-color-scheme: light){body{background:#f9fafb;color:#383838}}"
    "main{max-width:28rem;text-align:center}h1{font-size:1.4rem}"
    "a{display:inline-block;margin-top:1rem;padding:.6rem 1.2rem;border-radius:5px;background:#fa890d;"
    "color:#111;font-weight:700;text-decoration:none}</style></head><body><main>"
    "<h1>You're offline</h1><p>The gateway can't be reached right now. If you just pressed a button, "
    "nothing was sent. Check your connection, then try again.</p>"
    "<a href=''>Try again</a></main></body></html>"
)


def snapshot_deck(state: AppState, sub: str, snapshot_id: str) -> Deck:
    return parse_deck(state.decks.snapshot(sub, snapshot_id)["deck"])


__all__ = ["add_companion_routes", "stats_strip"]
