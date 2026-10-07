"""The companion pages: my decks, one deck, history and activity, plus the app shell.

These are the pages the Android app wraps and the browser shows on a phone. They render on
the server from the same services the MCP tools and the JSON API use; the small amount of
JavaScript (the deck editor) lives in ``static/companion.js`` and talks to ``/api/v1``.
Layout rules asked for by the Android thread: two panes (list and detail) from 600 px up, one
column below; internal links never open a new tab; external sites (Archidekt, Scryfall) may.
"""

from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from . import deck_stats
from .archidekt import FORMAT_NAMES, Deck, parse_deck
from .deckpage import (
    DECK_CSS,
    LIST_ORDERS,
    card_image,
    deck_list_controls_html,
    deck_list_html,
    deck_page_html,
    featured,
)
from .decks import DeckError, actor_label, current_client
from .pages import BROWSER_CLIENT_ID, _badge, _csrf, _when, browser_session, login_redirect, read_limited
from .theme import icon, render
from .views import auto_category, cards_by_category

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .reports import ReportService

logger = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"
# Pages with the editor load one script from /static; everything else keeps the default CSP.
DECK_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
# Format names the settings and new-deck forms offer, one per Archidekt format id.
FORMAT_CHOICES = [FORMAT_NAMES[i] for i in sorted(FORMAT_NAMES)]
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
DECK_OK_MESSAGES = {"report": "Report saved. See it under History."}
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
            scripts=scripts,
            current=current,
            heading=heading,
            head_extra=(f"<style>{DECK_CSS}</style>" if deck_css else "")
            + ("<script src='/static/deck.js' defer></script>" if scripts else ""),
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

    async def my_decks(sub: str) -> tuple[list[dict[str, Any]], str | None]:
        """(decks, problem) where problem is a short message when the list is unavailable."""
        try:
            return await decks.list_decks(sub), None
        except DeckError as exc:
            if exc.kind == "not_linked":
                return [], "not_linked"
            return [], str(exc)

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
        q = (qp.get("q") or "").strip()[:80]
        order = qp.get("order") if qp.get("order") in LIST_ORDERS else "updated"
        view = qp.get("view") if qp.get("view") in ("grid", "list") else "grid"
        folder = (qp.get("folder") or "").strip()[:80]
        rows, problem = await my_decks(sub)
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
        open_form = (
            "<form method='get' action='/decks/open' class='openform'>"
            "<label for='ref'>Open any Archidekt deck</label>"
            "<input id='ref' type='text' name='ref' placeholder='Archidekt link or deck id' required>"
            "<button>Open</button></form>"
        )
        notice = ""
        if qp.get("err") == "bad_ref":
            notice = "<p class='notice error'>That is not an Archidekt deck link or id.</p>"
        if problem == "not_linked":
            body = link_prompt() + f"<div class='panel'>{open_form}</div>"
            return page("My decks", notice + body, sub=sub, sid=sid, current="/decks")
        covers = state.db.deck_covers([str(d["id"]) for d in rows])
        body = (
            notice
            + deck_list_controls_html(
                q=q, order=order, view=view, folders=folders, folder=folder, total=total
            )
            + (f"<p class='notice error'>{_esc(problem)}</p>" if problem else "")
            + deck_list_html(rows, covers=covers, q=q, view=view)
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
        )

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
        fmt_opts = "".join(
            f"<option value='{_esc(n)}'{' selected' if values.get('format', 'commander') == n else ''}>"
            f"{_esc(n.capitalize())}</option>"
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
            "a <code># Sideboard</code> header) or an Archidekt CSV export. Leave it empty for an "
            "empty deck.</p>"
            f"<textarea id='source' name='source' rows='12' placeholder='1 Sol Ring&#10;1 Arcane Signet'>"
            f"{_esc(values.get('source', ''))}</textarea>"
            "<div class='field'><label for='kind'>The text above is</label><select id='kind' name='kind'>"
            f"<option value='list'{' selected' if values.get('kind') != 'csv' else ''}>a decklist</option>"
            f"<option value='csv'{' selected' if values.get('kind') == 'csv' else ''}>an Archidekt CSV export"
            "</option></select></div>"
            f"<div class='actions'><button class='primary'>{icon('plus')} Create deck</button>"
            "<a class='btn' href='/decks'>Cancel</a></div>"
            "<p class='muted small'>Creating makes a proposal; the deck appears on Archidekt once you "
            "apply it on the review page.</p></form>"
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
        return page("New deck", new_deck_form(values), sub=sub, sid=sid, current="/decks")

    @server.custom_route("/decks/new", methods=["POST"], include_in_schema=False)
    async def new_deck_post(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks/new")
        data = await form(request, limit=600_000)
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
                decklist_text=source if source and data.get("kind") != "csv" else None,
                csv_text=source if source and data.get("kind") == "csv" else None,
                private=bool(data.get("private")),
            )
        except DeckError as exc:
            return page(
                "New deck", new_deck_form(values, str(exc)), sub=sub, sid=sid, status=400, current="/decks"
            )
        return RedirectResponse(f"/proposals/{p['proposal_id']}", status_code=303)

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
        collection = getattr(state, "collection", None)
        body = deck_page_html(
            deck,
            stats,
            own=own,
            csrf=_csrf(s, sid),
            writes_enabled=s.writes_enabled,
            owned=collection.store.owned_names(sub) if collection is not None else None,
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
        return RedirectResponse(f"/proposals/{p['proposal_id']}", status_code=303)

    # -- deck settings (details proposal) ---------------------------------------
    def settings_form(
        deck: Deck, csrf: str | None, values: dict[str, str] | None = None, error: str = ""
    ) -> str:
        v = values or {}
        fmt_current = v.get("deck_format", deck.format or "")
        description = v.get("description", deck.description or "")

        def sel(flag: bool) -> str:
            return " selected" if flag else ""

        fmt_opts = "".join(
            f"<option value='{_esc(n)}'{sel(fmt_current == n)}>{_esc(n.capitalize())}</option>"
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
            f"<div class='actions'><button class='primary'>{icon('check')} Review changes</button>"
            f"<a class='btn' href='/decks/{did}'>Cancel</a></div>"
            "<p class='muted small'>Saving makes a proposal; nothing changes on Archidekt until you "
            "apply it on "
            "the review page.</p></form>"
            "<section class='panel' id='categories'><h2>Categories</h2>"
            f"<ul class='plain plist'>{cats or '<li class=muted>No categories yet.</li>'}</ul>"
            "<p class='muted small'>Cards are moved between categories in the editor; a new category "
            "is created "
            "by typing its name there. Renaming, deleting and the premier / in-deck / in-price flags are not "
            "available through the gateway yet.</p></section>"
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
            return page(
                "Cannot edit this deck",
                f"<div class='panel'><p>{_esc(exc)}</p>"
                f"<a class='btn' href='/decks/{_esc(deck_id)}'>Back</a></div>",
                sub=sub,
                sid=sid,
                status={"not_found": 404, "forbidden": 403}.get(exc.kind, 400),
            )
        return page(
            f"Deck settings: {deck.name}",
            settings_form(deck, _csrf(s, sid)),
            sub=sub,
            sid=sid,
            current="/decks",
        )

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
            )
        return RedirectResponse(f"/proposals/{p['proposal_id']}", status_code=303)

    # -- browser editor ---------------------------------------------------------
    @server.custom_route("/decks/{deck_id}/edit", methods=["GET"], include_in_schema=False)
    async def edit_page(request: Request) -> Response:
        """The deck editor: quantities, categories, finishes, printings and additions become one
        proposal, which the review page applies. Own decks only; the script does the work, the
        server hands it the deck, the categories and anything to prefill (a scan session or a
        Quick add name)."""
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/edit")
        try:
            deck = await decks.get_own_deck(sub, deck_id)
        except DeckError as exc:
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
                "in_deck": deck.in_deck(c),
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
        }
        cat_opts = "".join(f"<option value='{_esc(c)}'>{_esc(c)}</option>" for c in categories)
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
                "the proposal can be reviewed but not applied.</p>"
            )
            + "</section>"
            "<div class='editbar' role='region' aria-label='Pending changes'>"
            "<button type='button' class='btn-primary review' disabled>"
            f"{icon('check')} <span class='label'>Review changes</span></button>"
            f"<button type='button' class='undo' disabled>{icon('undo')} Undo</button>"
            "<span class='count muted'>No changes yet</span>"
            "<p class='status' role='status'></p></div>"
            "<details class='panel pendingbox'><summary>Pending changes <b class='n'>0</b></summary>"
            "<div class='pending'></div><p class='muted small limit'></p></details>"
            "<section class='panel addbox'><h2>Add a card</h2>" + picker + "<form class='addcard'>"
            "<div class='field grow'><label for='addname'>Card name</label>"
            "<input id='addname' type='text' name='card' list='cardnames' placeholder='Card name' "
            "autocomplete='off' required><datalist id='cardnames'></datalist></div>"
            "<div class='field'><label for='addqty'>Qty</label>"
            "<input id='addqty' type='number' name='qty' value='1' min='1' max='99'></div>"
            "<div class='field'><label for='addcat'>Category</label><span class='sel'>"
            f"<select id='addcat' name='addcat'><option value=''>Auto</option>{cat_opts}</select>"
            "</span></div>"
            "<div class='field'><label for='addfinish'>Finish</label><span class='sel'>"
            "<select id='addfinish' name='addfinish'><option value=''>Normal</option>"
            "<option value='foil'>Foil</option></select></span></div>"
            f"<button class='btn-primary'>{icon('plus')} Add</button></form>"
            "<ul class='erows added'></ul></section>"
            "<div class='cats existing'></div>"
            "<div class='picker' hidden></div>"
            "</div>"
            + "".join(
                f"<template id='icon-{n}'>{icon(n)}</template>"
                for n in ("minus", "plus", "more", "x", "swap")
            )
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
        from .decks import deck_to_text

        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}/export")
        try:
            deck = await decks.get_any_deck(sub, deck_id)
        except DeckError as exc:
            return page(
                "Deck not found", f"<div class='card'><p>{_esc(exc)}</p></div>", sub=sub, sid=sid, status=404
            )
        main = deck_to_text(deck)
        side = deck_to_text(deck, zone="side")
        body = (
            f"<div class='card'><p><a href='/decks/{_esc(deck.id)}'>← {_esc(deck.name)}</a></p>"
            "<h2>Decklist</h2><p class='muted small'>Plain text, commander first. Paste it into Moxfield, "
            "MTGO, Arena or your assistant.</p>"
            f"<textarea rows='{min(60, main.count(chr(10)) + 2)}' readonly>{_esc(main)}</textarea>"
            + (
                f"<h2>Sideboard and maybeboard</h2><textarea rows='8' readonly>{_esc(side)}</textarea>"
                if side
                else ""
            )
            + "<div class='actions'>"
            f"<a class='btn' href='/decks/{_esc(deck.id)}/export.txt' download>Download .txt</a>"
            f"<a class='btn' href='/decks/{_esc(deck.id)}/export.json' download>Download .json</a>"
            f"<a class='btn' href='/decks/{_esc(deck.id)}/export.csv' download>Download .csv</a>"
            "</div></div>"
        )
        return page(f"Export: {deck.name}", body, sub=sub, sid=sid)

    @server.custom_route("/decks/{deck_id}/export.txt", methods=["GET"], include_in_schema=False)
    async def export_txt(request: Request) -> Response:
        from .decks import deck_to_text

        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
        try:
            deck = await decks.get_any_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return Response(str(exc), 404)
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

    @server.custom_route("/decks/{deck_id}/export.json", methods=["GET"], include_in_schema=False)
    async def export_json(request: Request) -> Response:
        from .views import deck_out

        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
        try:
            deck = await decks.get_any_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return JSONResponse({"ok": False, "error": exc.kind, "message": str(exc)}, 404)
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
            return Response(str(exc), 404)

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
            await reports.run(sub, deck_id)
        except DeckError as exc:
            code = exc.kind if exc.kind in DECK_ERR_MESSAGES else "report_failed"
            return RedirectResponse(f"/decks/{deck_id}?err={code}", status_code=303)
        return RedirectResponse(f"/history?deck_id={deck_id}", status_code=303)

    # -- history --------------------------------------------------------------
    @server.custom_route("/history", methods=["GET"], include_in_schema=False)
    async def history(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        deck_id = (request.query_params.get("deck_id") or "").strip()
        proposals = decks.list_proposals(sub)
        snapshots = decks.list_snapshots(sub)
        reps = reports.list(sub, deck_id or None, limit=50)
        if deck_id:
            proposals = [p for p in proposals if p["deck_id"] == deck_id]
            snapshots = [x for x in snapshots if x["deck_id"] == deck_id]
        events: list[tuple[int, str]] = []
        for p in proposals:
            kind = {"create_deck": "New deck", "restore": "Restore"}.get(p.get("kind", "edit"), "Edit")
            events.append(
                (
                    int(p["created_at"]),
                    f"<li><a class='name' href='/proposals/{_esc(p['id'])}'>{_esc(kind)}: "
                    f"{_esc(p['deck_name'] or p['deck_id'])}</a>"
                    f"<span class='badge {_badge(p['state'])}'>{_esc(p['state'])}</span>"
                    f"<span class='when'>{_when(p['created_at'])}</span></li>",
                )
            )
        for x in snapshots:
            backup = f" · {_ext(x['backup_url'], 'Archidekt backup')}" if x.get("backup_url") else ""
            events.append(
                (
                    int(x["taken_at"]),
                    f"<li><span class='name'>Snapshot: {_esc(x['deck_name'] or x['deck_id'])} "
                    f"<span class='muted small'>({_esc(x.get('card_count'))} cards){backup}</span></span>"
                    f"<form method='post' action='/history/restore'><input type='hidden' name='csrf' "
                    f"value='{_esc(_csrf(s, sid))}'><input type='hidden' name='snapshot_id' "
                    f"value='{_esc(x['snapshot_id'])}'><button class='secondary'>Restore…</button></form>"
                    f"<span class='when'>{_when(x['taken_at'])}</span></li>",
                )
            )
        for r in reps:
            m = r["metrics"]
            bits = [
                f"{_num(m.get('card_count'), 0)} cards",
                f"avg MV {_num(m.get('average_mana_value'))}",
            ]
            if m.get("price_total") is not None:
                bits.append(f"${_num(m.get('price_total'))}")
            if r.get("bracket_estimate"):
                bits.append(f"bracket ~{r['bracket_estimate']}")
            events.append(
                (
                    int(r["taken_at"]),
                    f"<li><a class='name' href='/history/reports/{_esc(r['report_id'])}'>Report: "
                    f"{_esc(r['deck_name'])}</a><span class='muted small'>{_esc(' · '.join(bits))}</span>"
                    f"<span class='when'>{_when(r['taken_at'])}</span></li>",
                )
            )
        events.sort(key=lambda e: e[0], reverse=True)
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
                    f"<div class='card'><h2>Trend over {len(series)} reports</h2>"
                    f"<div class='tiles'>{''.join(cards)}</div></div>"
                )
        head = f"<p><a href='/decks/{_esc(deck_id)}'>← Back to the deck</a></p>" if deck_id else ""
        body = (
            head
            + trend
            + (
                f"<div class='card'><ul class='plain plist'>{''.join(h for _t, h in events)}</ul></div>"
                if events
                else "<div class='card'><p>Nothing yet. Proposals, snapshots and deck reports appear here."
                "</p></div>"
            )
        )
        return page("History" if not deck_id else "Deck history", body, sub=sub, sid=sid)

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

    @server.custom_route("/history/reports/{rid}", methods=["GET"], include_in_schema=False)
    async def report_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        try:
            r = reports.get(sub, request.path_params["rid"])
        except DeckError as exc:
            return page("Report", f"<div class='card'><p>{_esc(exc)}</p></div>", sub=sub, sid=sid, status=404)
        body = (
            f"<div class='card'><p><a href='/decks/{_esc(r['deck_id'])}'>← {_esc(r['deck_name'])}</a> · "
            f"<a href='/history?deck_id={_esc(r['deck_id'])}'>deck history</a></p>"
            f"<p class='muted small'>Taken {_when(r['taken_at'])}</p>" + stats_strip(r["stats"]) + "</div>"
        )
        for key, title in (("goldfish", "Goldfish simulation"), ("validation", "Validation")):
            block = r.get(key)
            if not block:
                continue
            text = block.get("text") or json.dumps(block.get("data"), indent=1)
            status = "" if block.get("ok") else " <span class='badge danger'>failed</span>"
            body += f"<div class='card'><h2>{title}{status}</h2><pre>{_esc(text[:20000])}</pre></div>"
        if not r.get("goldfish") and not r.get("validation"):
            body += "<p class='muted small'>The research service was not configured when this report ran.</p>"
        return page(f"Report: {r['deck_name']}", body, sub=sub, sid=sid)

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
                {"src": "/scan/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/scan/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
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
            "if(e.request.mode!=='navigate')return;"
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
