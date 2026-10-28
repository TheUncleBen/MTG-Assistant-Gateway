"""Companion pages: the deck list, the New deck box, one deck with its boxes, and its clone."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import actions, deck_extras, deck_stats, prefs, ui
from .archidekt import format_label
from .busy import busy_response
from .companion import (
    DECK_CSP,
    DECK_ERR_MESSAGES,
    DECK_OK_MESSAGES,
    DECKS_JSON_TIMEOUT,
    FORMAT_CHOICES,
    LIST_OK_MESSAGES,
    LIST_VIEWS,
)
from .deckpage import (
    DECK_BOXES,
    VIEWS,
    covers_for,
    deck_boxes_html,
    deck_count_text,
    deck_list_controls_html,
    deck_list_held_html,
    deck_page_html,
    deck_title,
    decks_unreadable_html,
    featured,
    folder_options_html,
    reread_href,
)
from .decks import MAX_PENDING_PROPOSALS, DeckError, current_client, owns_deck
from .folderview import FOLDER_CSS, arrange, filter_params, level_html, read_filters, resolve_folder
from .pages import (
    BROWSER_CLIENT_ID,
    _csrf,
    browser_session,
    login_redirect,
)
from .theme import (
    esc as _esc,
)
from .theme import (
    hub_switch,
    icon,
)
from .waiting_strip import waiting_changes

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

NEW_DECK_SCRIPTS = ("filepick.js", "newdeck.js", "newpackage.js")
NEW_DECK_EXPIRED = "This form expired. Press Create deck again."
NEW_DECK_NOT_LINKED = "Link your Archidekt account first. The deck is made in your Archidekt account."
NEW_DECK_TOO_MANY = (
    f"You already have {MAX_PENDING_PROPOSALS} changes waiting. Apply or reject some on "
    "<a href='/proposals'>Proposals</a>, then create the deck."
)
NEW_DECK_NAME = "Give the deck a name."
NEW_DECK_HELP = (
    "A decklist (<code>1 Sol Ring</code>, <code>2x Opt (cmr) [Ramp]</code>, a <code># Sideboard</code> "
    "header), an Archidekt CSV export or this gateway's .json export."
)


def new_deck_error_html(exc: DeckError) -> str:
    """The red strip at the top of the New deck box for a refusal the gateway finds before it makes
    the change: in the box's own words for the cases it knows, else the service's words."""
    if exc.kind == "not_linked":
        return (
            "<b>Link your Archidekt account first.</b> The deck is made in your Archidekt account. "
            "<a href='/account/details#archidekt'>Link it under Account details</a>"
        )
    if exc.kind == "rate_limited" and "pending proposals" in str(exc):
        return (
            f"<b>You already have {MAX_PENDING_PROPOSALS} changes waiting.</b> Apply or reject some on "
            "<a href='/proposals'>Proposals</a>, then create the deck."
        )
    text = str(exc).strip()
    return _esc(text if text.endswith((".", "!", "?")) else text + ".")


def folder_id_of(data: dict[str, str]) -> int | None:
    """The folder a form picked (``folder_id``): its id, None for the top level. A value that is
    not a folder's id is refused as a folder the member does not have."""
    fid = (data.get("folder_id") or "").strip()
    if not fid:
        return None
    if not re.fullmatch(r"[0-9]{1,12}", fid):
        raise DeckError("invalid", "That folder is not in your Archidekt account.")
    return int(fid)


def new_deck_box_html(
    csrf: str,
    values: dict[str, str] | None = None,
    *,
    error_html: str = "",
    open: bool = False,
    scan_name: str = "",
    back: str = "/decks",
    folders: dict[str, Any] | None = None,
    folder: str = "",
) -> str:
    """The New deck box (F4, a box over the page it was opened from, never a page): Deck name
    (120 characters, a live count), Format, Private, and the cards as three tabs (Empty, Paste a
    list, From a file; static/newdeck.js), with Read as under the text box. ``values`` are the
    typed values kept after a refusal (``error_html`` is its red strip); ``scan_name`` names the
    scan the list came from. Without scripts the fields show as a form, every one of them."""
    v = values or {}
    kind = v.get("kind") if v.get("kind") in ("csv", "json") else "list"
    fmt = v.get("format", "commander")
    fmt_opts = "".join(
        f"<option value='{_esc(n)}'{' selected' if fmt == n else ''}>{_esc(format_label(n))}</option>"
        for n in FORMAT_CHOICES
    )
    name = v.get("name", "")
    source = v.get("source", "")
    private = v.get("private", "" if "name" in v else "1")
    src = "paste" if source else "empty"
    tabs = "".join(
        f"<button type='button' role='tab' data-src='{key}' "
        f"aria-selected='{'true' if key == src else 'false'}'"
        + (" disabled" if key == "empty" and scan_name else "")
        + f">{icon(ic)}<span>{label}</span></button>"
        for key, label, ic in (
            ("empty", "Empty", "x"),
            ("paste", "Paste a list", "edit"),
            ("file", "From a file", "download"),
        )
    )
    body = (
        (f"<div class='notice error' role='alert'>{error_html}</div>" if error_html else "")
        + (
            ui.notice(f"{icon('camera')} From your scan “{_esc(scan_name)}”", "info", html=True)
            if scan_name
            else ""
        )
        + f"<form id='ndf' method='post' action='/decks/new' class='nd-form' data-src='{src}' novalidate>"
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        "<div class='nd-grid'><div class='nd-fields'>"
        "<div class='nd-top'><div class='field nd-name'><label for='name'>Deck name</label>"
        f"<input id='name' type='text' name='name' value='{_esc(name)}' placeholder='Deck name' "
        "required maxlength='120' autocomplete='off'>"
        f"<div class='nd-under'><span class='nd-fe' id='nd-ne' hidden>{icon('alert')} {NEW_DECK_NAME}</span>"
        f"<span class='muted small nd-cnt'>{len(name)}/120</span></div></div>"
        f"<div class='field'><label for='format'>Format</label><span class='sel'><select id='format' "
        f"name='format'>{fmt_opts}</select></span></div></div>"
        "<label class='switch'><input type='checkbox' name='private' value='1'"
        + (" checked" if private else "")
        + "><span class='track'></span>Private</label>"
        "<label class='switch'><input type='checkbox' name='theorycrafted' value='1'"
        + (" checked" if v.get("theorycrafted") else "")
        + "><span class='track'></span>Theorycrafted</label>"
        "<div class='nd-top'><div class='field nd-folder'><label for='nd-folder'>Folder</label>"
        "<span class='sel'>"
        f"<select id='nd-folder' name='folder_id'>{folder_options_html(folders, v.get('folder_id', folder))}"
        "</select></span></div>"
        "<div class='field nd-game'><label for='game'>Game type</label><span class='sel'>"
        "<select id='game' name='game'>"
        + "".join(
            f"<option value='{k}'{' selected' if v.get('game', 'any') == k else ''}>{label}</option>"
            for k, label in (("any", "Any"), ("paper", "Paper"), ("mtgo", "MTGO"), ("arena", "Arena"))
        )
        + "</select></span></div></div>"
        "<div class='nd-cards'><span class='lbl' id='nd-cards-l'>Cards</span>"
        f"<div class='segc nd-tabs' role='tablist' aria-labelledby='nd-cards-l'>{tabs}</div>"
        "<div class='nd-src'>"
        "<div class='field filepick'><span class='lbl'>From a file</span><label class='filebtn'>"
        "<input id='file' type='file' accept='.txt,.csv,.json,text/plain,text/csv,application/json' "
        "data-fill='source' data-kind='kind'><span class='btn'>Choose a file</span></label>"
        "<span class='fname' aria-live='polite'>No file chosen</span></div>"
        f"<textarea id='source' name='source' rows='8' aria-label='Cards' spellcheck='false' "
        "placeholder='1 Sol Ring&#10;2x Opt (cmr) [Ramp]&#10;# Sideboard&#10;1 Duress'>"
        f"{_esc(source)}</textarea>"
        "<div class='nd-row2'><div class='nd-as'><label for='kind' class='muted small'>Read as</label>"
        "<span class='sel'><select id='kind' name='kind'>"
        f"<option value='list'{' selected' if kind == 'list' else ''}>Decklist</option>"
        f"<option value='csv'{' selected' if kind == 'csv' else ''}>Archidekt CSV export</option>"
        f"<option value='json'{' selected' if kind == 'json' else ''}>Gateway .json export</option>"
        "</select></span></div>"
        f"<details class='nd-help'><summary class='fold-btn'>{icon('guide')} What can I paste?</summary>"
        f"<p class='small'>{NEW_DECK_HELP}</p></details></div>"
        "</div></div></div>"
        "<aside class='nd-prev' aria-label='How it will look in My decks'>"
        "<div class='ndtile'><span class='thumb noart'><span class='ini'>"
        f"{_esc((name.strip() or '?')[:1].upper())}</span><span class='views nd-pn'>0 cards</span></span>"
        f"<span class='info'><span class='name nd-pt{'' if name.strip() else ' muted'}'>"
        f"{_esc(name.strip() or 'Unnamed deck')}</span>"
        f"<span class='fmt nd-pf'>{_esc(format_label(fmt))} · {'Private' if private else 'Public'}</span>"
        "</span></div>"
        "<p class='muted small nd-pc' hidden></p></aside>"
        "</div></form>"
    )
    return ui.box(
        "newdeck",
        "New deck",
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(
                "Create deck",
                kind="primary",
                form="ndf",
                icon_name="plus",
                attrs="data-busy-label='Creating…'",
            ),
        ],
        wide=True,
        open=open,
        close_href=back,
        cls="nd-box",
    )


NEW_PACKAGE_CSS = deck_extras.CSS + deck_extras.PACKAGE_FIELDS_CSS  # the New package box

NEW_DECK_CSS = """
/* the New deck box: the fields left, the tile preview right (a computer); a sheet on a phone */
.mtgbox.nd-box{width:min(100%,50rem)}
.nd-grid{display:grid;grid-template-columns:minmax(0,1fr) 13rem;gap:1.1rem;align-items:stretch}
.nd-fields{display:grid;grid-template-columns:minmax(0,1fr);gap:.9rem;min-width:0}
.nd-top{display:grid;grid-template-columns:minmax(0,1fr) 11rem;gap:.75rem;align-items:start}
/* the grids space the fields; the box's own .field + .field margin would set Format 12 px lower
   than Deck name (0.7.44) */
.nd-form .field,.mtgbox .body .nd-form .field + .field{margin:0;min-width:0}
.nd-form .field label{display:block;font-weight:700;margin:0 0 .25rem}
.nd-form .msel-btn{max-width:100%}
/* the three tabs wrap at large text instead of widening the sheet */

.nd-form .field input:not([type=file]),.nd-form .field select,.nd-form textarea{width:100%}
.nd-under{display:flex;justify-content:space-between;gap:.5rem;min-height:1.3rem;margin-top:.2rem}
.nd-fe{display:inline-flex;align-items:center;gap:.3rem;color:var(--danger-text);font-weight:700;font-size:.88rem}
.nd-fe[hidden]{display:none}
.nd-fe svg.i{width:16px;height:16px}
.nd-form input[aria-invalid=true]{border-color:var(--danger-text);box-shadow:0 0 0 1px var(--danger-text)}
.nd-form .switch{justify-self:start;margin:0}
.nd-cards{display:grid;gap:.6rem} .nd-cards > .lbl{font-weight:700}
/* three tabs on one row; at large text they wrap instead of widening the sheet */
.mtgbox .body .nd-tabs{display:flex;flex-wrap:wrap;width:100%;margin:0}
.mtgbox .body .nd-tabs [role=tab]{flex:1 1 0;min-width:0;gap:.35rem;display:inline-flex;align-items:center;
  justify-content:center}
.nd-tabs [role=tab] span{white-space:nowrap;overflow:hidden;text-overflow:ellipsis;min-width:0}
@media (max-width:759.98px){ .mtgbox .body .nd-tabs [role=tab]{flex:1 1 auto} }
.nd-tabs [role=tab] svg.i{width:16px;height:16px;flex:none}
.nd-src{display:grid;grid-template-columns:minmax(0,1fr);gap:.6rem}
.nd-src textarea,.nd-src .filepick{min-width:0;max-width:100%}
.nd-src[hidden]{display:none}
.nd-src textarea{min-height:9rem}
.nd-src .filepick[hidden]{display:none}
.nd-src .filepick{justify-content:center;border:2px dashed var(--control-border);
  border-radius:var(--radius-panel);
  padding:1rem;min-height:5rem}
.nd-src .filepick > .lbl{display:none}
.nd-row2{display:flex;flex-wrap:wrap;align-items:center;justify-content:space-between;gap:.5rem 1rem}
.nd-as{display:flex;align-items:center;gap:.5rem}
.nd-as label{margin:0}
.nd-help{min-width:0}
.nd-help p{margin:.2rem 0 0;max-width:30rem}
/* the tile at the top of its column, level with the fields (0.7.43: it floated mid-column) */
.nd-prev{display:flex;flex-direction:column;justify-content:flex-start;padding-left:1.1rem;
  border-left:1px solid var(--border-soft)}
.ndtile{display:block;border:1px solid var(--card-border);border-radius:var(--radius-panel);overflow:hidden;
  background:var(--surface)}
.ndtile .thumb{position:relative;display:block;height:110px;
  background:linear-gradient(135deg,#3a3a3a,#1f1f1f)}
.ndtile .ini{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-size:3rem;
  font-weight:900;color:rgba(255,255,255,.25)}
.ndtile .views{position:absolute;right:.5rem;top:.5rem;font-size:12px;color:#fff;background:rgba(0,0,0,.55);
  padding:.1rem .4rem;border-radius:3px}
.ndtile .info{display:flex;flex-direction:column;gap:.1rem;padding:.5rem .75rem;min-width:0}
.ndtile .name{font-weight:700;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ndtile .fmt{font-size:.86rem;color:var(--text-muted)}
.nd-pc{margin:.4rem 0 0;text-align:center} .nd-pc[hidden]{display:none}
.nd-box .notice{margin:0 0 .75rem}
/* without scripts the box is a panel: every field shows, the tabs are not drawn */
.ui-box.nd-box:not(.in-scrim) .nd-tabs{display:none}
@media (max-width:759.98px){ .nd-grid{grid-template-columns:minmax(0,1fr)} .nd-prev{display:none} }
@media (max-width:599.98px){ .nd-top{grid-template-columns:minmax(0,1fr)}
  .nd-tabs [role=tab] svg.i{display:none} }
"""


def add_deck_routes(server: MCPServer, state: AppState, h: SimpleNamespace) -> None:
    """My decks, new deck, one deck and clone."""
    EXPIRED = h.EXPIRED
    apply_now = h.apply_now
    check_csrf = h.check_csrf
    deck_list_skeleton = h.deck_list_skeleton
    decks = h.decks
    form = h.form
    link_prompt = h.link_prompt
    list_query = h.list_query
    my_decks = h.my_decks
    page = h.page
    s = h.s
    secure = h.secure

    async def folder_tree(sub: str, *, wait: float) -> tuple[dict[str, Any] | None, bool]:
        """(the member's folder tree, still being read). The tree is None when Archidekt could
        not give it (My decks then lists every deck flat, with a notice) or is still coming."""
        try:
            tree = await decks.folder_view(sub, wait=wait, last_read_ok=True)
        except DeckError:
            return None, False
        return tree, tree is None

    # -- my decks ---------------------------------------------------------------
    async def my_decks_response(
        request: Request,
        sub: str,
        sid: str | None,
        *,
        new_deck: dict[str, Any] | None = None,
        new_package: dict[str, Any] | None = None,
        status: int = 200,
    ) -> Response:
        """The My decks page. ``new_deck``: the New deck box drawn open (the /decks/new address, or
        a refused form): ``values``, ``error_html``, ``scan_name``. ``new_package``: the New
        package box drawn open the same way (/decks/new-package): ``values``, ``error``."""
        qp = request.query_params
        chosen, remember = prefs.view_choice(state.db, sub, request, "decks", LIST_VIEWS, "grid")
        q, order, view, folder = list_query(qp, chosen)
        filters = read_filters(qp)
        rows, problem, failure = await my_decks(sub, wait=decks.deck_list_wait, last_read_ok=True)
        if failure is not None and (busy := busy_response(failure, request, page, sub=sub, sid=sid)):
            return busy
        pending = rows is None  # cold start: the shell goes out now, decks.js fills the list
        unreadable = bool(getattr(rows, "unreadable", 0))  # entries Archidekt left unreadable
        last_read = getattr(rows, "last_read", None)  # Archidekt can't be asked: the list as last read
        hidden = 0  # the gateway's backup copies, kept out of this list; the list is warm now
        if not pending and not problem and not unreadable and last_read is None:
            try:
                hidden = len(await decks.backup_copies(sub, wait=0.0) or [])
            except DeckError:
                hidden = 0
        tree, tree_pending = None, False
        if not pending and not problem:  # Archidekt failing on the list: no second request
            tree, tree_pending = await folder_tree(sub, wait=decks.deck_list_wait)
        pending = pending or tree_pending  # the folders are still being read: decks.js fills the list
        folder_id = resolve_folder(tree, folder)
        level = arrange(rows or [], tree, folder_id=folder_id, q=q, order=order, **filters)
        total = len(level.decks) if level.searching else level.total
        if not pending:  # the toolbar keeps the folder shown (an old name address becomes its id)
            folder = str(folder_id) if folder_id is not None else ""
        unread_html = (
            decks_unreadable_html(some_shown=total > 0, again=reread_href("/decks", qp)) if unreadable else ""
        ) + (deck_list_held_html(last_read) if last_read is not None else "")
        # the heading with its three actions: + New folder (a box, static/menus.js), New package (a
        # plain button; parity release 2) and New deck (the one orange button): the boxes' links
        # are the old addresses, which open this page with the box open
        # The switch names the page (My Decks or Packages), so the heading is for screen readers only;
        # the three actions share its row (Ben, 2026-10-25).
        packages = filters["show"] == "packages"
        # the switch keeps the folder open, as the Show filter it replaced did
        in_folder = f"folder={quote(folder, safe='')}" if folder else ""
        head = (
            "<div class='ui-head phead hubhead'>"
            f"<h1 class='sr-only'>{'Packages' if packages else 'My Decks'}</h1>"
            + hub_switch(
                "Decks",
                [
                    ("/decks" + (f"?{in_folder}" if in_folder else ""), "My Decks", ""),
                    ("/decks?show=packages" + (f"&{in_folder}" if in_folder else ""), "Packages", "packages"),
                ],
                filters["show"],
            )
            # New deck first, the one orange button, then New folder and New package (Ben,
            # 2026-10-25: "New deck / New folder / New package")
            + "<span class='acts'>"
            f"<a class='btn btn-primary' href='/decks/new' {ui.box_open_attr('newdeck')}>"
            f"{icon('plus')} New deck</a>"
            f"<button type='button' class='btn' data-new-folder>{icon('plus')} New folder</button>"
            f"<a class='btn' href='/decks/new-package' {ui.box_open_attr('newpkg')}>"
            f"{icon('package')} New package</a>"
            "</span></div>"
        )
        notice = ""
        if qp.get("err") == "bad_ref":
            notice = "<p class='notice error'>That is not an Archidekt deck link or id.</p>"
        if qp.get("ok") in LIST_OK_MESSAGES:
            notice = f"<p class='notice ok'>{_esc(LIST_OK_MESSAGES[qp['ok']])}</p>"
        nd = new_deck or {}
        # the boxes' Folder field: the member's tree as My decks has it (the top level alone while
        # it is still being read), starting on the open folder
        here = str(folder_id) if folder_id is not None else ""
        box = new_deck_box_html(
            _csrf(s, sid) or "",
            nd.get("values"),
            error_html=nd.get("error_html", ""),
            open=new_deck is not None,
            scan_name=nd.get("scan_name", ""),
            folders=tree,
            folder=here,
        )
        np = new_package or {}
        box += deck_extras.new_package_box_html(
            _csrf(s, sid) or "",
            np.get("values"),
            error=np.get("error", ""),
            open=new_package is not None,
            folders=tree,
            folder=here,
        )
        if problem == "not_linked":
            return page(
                "My decks",
                notice + link_prompt() + box,
                sub=sub,
                sid=sid,
                current="/decks",
                status=status,
                extra_scripts=NEW_DECK_SCRIPTS,
                extra_css=NEW_DECK_CSS + NEW_PACKAGE_CSS,
            )
        if pending:
            src = "/api/decks/mine?" + urlencode(
                [("shape", "list")]
                + filter_params(q, order, view, request.query_params.get("folder") or "", filters)
            )
            listing = (
                f"<div id='decks-list' data-decks-src='{_esc(src)}' aria-busy='true'>"
                f"{deck_list_skeleton(view)}</div>"
            )
        else:
            shown = level.decks
            covers = covers_for(shown, state.db.deck_covers([str(d["id"]) for d in shown]))
            listing = (f"<p class='notice error'>{_esc(problem)}</p>" if problem else "") + unread_html
            if not (unreadable and not total):  # nothing read: the notice instead of "No decks yet"
                listing += level_html(level, covers=covers, q=q, view=view, order=order)
            listing = f"<div id='decks-list'>{listing}</div>"
        body = (
            head
            + notice
            + box
            + deck_list_controls_html(
                q=q,
                order=order,
                view=view,
                folder=folder,
                total=None if unreadable and not total else total,
                pending=pending,
                direction=filters["direction"],
                colors=filters["colors"],
                colour_mode=filters["colour_mode"],
                fmt=filters["fmt"],
                show=filters["show"],
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
        )
        resp = page(
            ("Packages" if filters["show"] == "packages" else "My Decks")
            if new_deck is None and new_package is None
            else "New deck"
            if new_deck
            else "New package",
            body,
            sub=sub,
            sid=sid,
            status=status,
            two_pane=True,
            heading=False,
            current="/decks",
            scripts=True,
            csp=DECK_CSP,  # the covers are Scryfall images
            deck_css=True,
            extra_scripts=("searchpanel.js", "decks.js") + NEW_DECK_SCRIPTS,
            extra_css=FOLDER_CSS + NEW_DECK_CSS + NEW_PACKAGE_CSS,
        )
        return prefs.remember_view(state.db, sub, resp, "decks", view, secure=secure) if remember else resp

    @server.custom_route("/decks", methods=["GET"], include_in_schema=False)
    async def decks_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
        qp = request.query_params
        # Try again on the notice that the deck list couldn't be read: read it again, then show it
        if qp.get("reread"):
            if prefs.from_this_site(request):  # a link on another site only shows the page
                decks.reread_deck_list(sub)
            rest = urlencode([(k, v) for k, v in qp.multi_items() if k != "reread"])
            return RedirectResponse("/decks" + (f"?{rest}" if rest else ""), 303)
        return await my_decks_response(request, sub, sid)

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
        if not prefs.from_this_site(request):  # it asks Archidekt on the member's own allowance
            return JSONResponse(
                {"ok": False, "error": "cross_site"}, 403, headers={"Cache-Control": "no-store"}
            )
        qp = request.query_params
        shape = "recent" if qp.get("shape") == "recent" else "list"
        q, order, view, folder = list_query(qp)
        filters = read_filters(qp)
        try:
            rows = await asyncio.wait_for(decks.list_decks(sub), DECKS_JSON_TIMEOUT)
        except DeckError as exc:
            rows, problem = None, (exc.kind, str(exc))
        except TimeoutError:
            rows, problem = None, ("unavailable", "Archidekt did not answer in time.")
        else:
            problem = None
        if problem is not None and problem[0] in ("rate_limited", "unavailable"):
            # Archidekt can't be asked: the list as last read, with the notice saying how old it is
            try:
                held = decks.last_deck_list(sub)
            except DeckError:
                held = None
            if held is not None:
                rows, problem = held, None
        last_read = getattr(rows, "last_read", None)
        held_html = deck_list_held_html(last_read) if last_read is not None else ""
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
            out["html"] = recent_panel_inner(
                None if problem else recent,
                covers,
                unreadable=bool(getattr(rows, "unreadable", 0)),
                last_read=last_read,
            )
            out["count"] = len(recent)
        else:
            unreadable = bool(getattr(rows, "unreadable", 0))  # the deck list couldn't be read, as on /decks
            tree = None
            if rows is not None:
                tree, _late = await folder_tree(sub, wait=DECKS_JSON_TIMEOUT)
            level = arrange(
                rows or [], tree, folder_id=resolve_folder(tree, folder), q=q, order=order, **filters
            )
            shown, total = level.decks, (len(level.decks) if level.searching else level.total)
            covers = covers_for(shown, state.db.deck_covers([str(d["id"]) for d in shown]))
            out["html"] = (
                (f"<p class='notice error'>{_esc(problem[1])}</p>" if problem else "")
                + (
                    decks_unreadable_html(some_shown=total > 0, again=reread_href("/decks", qp))
                    if unreadable
                    else ""
                )
                + held_html
            )
            if not (unreadable and not total):
                out["html"] += level_html(level, covers=covers, q=q, view=view, order=order)
            out["count"] = len(shown)
            out["total"] = None if unreadable and not total else total  # no count it doesn't know
            out["count_text"] = deck_count_text(out["total"])
        for d in rows or []:
            d.setdefault("url", f"https://archidekt.com/decks/{d['id']}")
        if rows is not None and qp.get("packages") == "1":
            # ?packages=1 (the forum's Enter a deck… box): the member's card packages marked, one
            # more Archidekt read, so the box can leave them out; unmarked when that read fails
            out["packages_marked"] = await decks.mark_packages(sub, rows)
        out["decks"] = rows or []
        resp = JSONResponse(out, headers={"Cache-Control": "no-store"})
        if shape == "list" and qp.get("view") in LIST_VIEWS:
            # the panel's Grid or List switch redraws the list in place: it is remembered as the
            # page's own address would remember it
            return prefs.remember_view(state.db, sub, resp, "decks", view, secure=secure)
        return resp

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

    # -- new deck: a box over My decks (F4); its old address opens My decks with the box open ----
    @server.custom_route("/decks/new", methods=["GET"], include_in_schema=False)
    async def new_deck_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks/new")
        values: dict[str, str] = {}
        scan_name = ""
        scan_ref = (request.query_params.get("scan_session") or "").strip()[:80]
        if scan_ref and state.scan is not None:
            # "New deck from these cards" on the scan page: the scan's list is the decklist.
            from .scan.service import ScanError

            try:
                sess = state.scan.find_session(sub, scan_ref)
                values["source"] = sess.get("decklist_text") or ""
                values["name"] = str(sess.get("name") or "")[:120]
                scan_name = values["name"] or "your scan"
            except ScanError:
                pass
        return await my_decks_response(request, sub, sid, new_deck={"values": values, "scan_name": scan_name})

    @server.custom_route("/decks/new", methods=["POST"], include_in_schema=False)
    async def new_deck_post(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks/new")
        data = await form(request, limit=4_200_000)  # a deck .json export can reach a few MB
        values = {
            k: v
            for k, v in data.items()
            if k in ("name", "format", "private", "theorycrafted", "game", "source", "kind", "folder_id")
        }
        if not check_csrf(sid, data):
            # the box comes back with a fresh token and everything typed
            return await my_decks_response(
                request,
                sub,
                sid,
                new_deck={
                    "values": values,
                    "error_html": "<b>This form expired.</b> Press Create deck again.",
                },
                status=403,
            )
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
                theorycrafted=bool(data.get("theorycrafted")),
                game=data.get("game") or "any",
                folder_id=folder_id_of(data),
            )
        except DeckError as exc:
            return await my_decks_response(
                request,
                sub,
                sid,
                new_deck={"values": values, "error_html": new_deck_error_html(exc)},
                status=400,
            )
        return await apply_now(sub, p["proposal_id"], ok="created")

    # -- new package: the New package box over My decks (parity release 2) ------------------------
    @server.custom_route("/decks/new-package", methods=["GET"], include_in_schema=False)
    async def new_package_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks/new-package")
        return await my_decks_response(request, sub, sid, new_package={"values": {}})

    @server.custom_route("/decks/new-package", methods=["POST"], include_in_schema=False)
    async def new_package_post(request: Request) -> Response:
        """Create package: the member's own press. A new deck with the package block and no cards
        (decks.propose_new_deck with ``package``), applied at once as Create deck is."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks/new-package")
        data = await deck_extras.read_form(request, lists=("format",))
        values = {
            "name": data.get("name", ""),
            "keywords": data.get("keywords", ""),
            "budget": data.get("budget", ""),
            "formats": data.get("format_list", ""),
            "private": data.get("private", ""),
            "folder_id": data.get("folder_id", ""),
        }
        if not check_csrf(sid, data):
            return await my_decks_response(
                request,
                sub,
                sid,
                new_package={"values": values, "error": "This form expired. Press Create package again."},
                status=403,
            )
        if not values["name"].strip():
            error = deck_extras.NEW_PACKAGE_NAME
        elif not values["keywords"].strip():
            error = deck_extras.NEW_PACKAGE_KEYWORDS
        else:
            error = ""
        if error:
            return await my_decks_response(
                request, sub, sid, new_package={"values": values, "error": error}, status=400
            )
        budget = values["budget"]
        current_client.set(BROWSER_CLIENT_ID)
        try:
            p = await decks.propose_new_deck(
                sub,
                name=values["name"],
                deck_format="commander",
                cards=[],
                private=bool(values["private"]),
                package={
                    "keywords": values["keywords"],
                    "budget": int(budget) if re.fullmatch(r"[1-4]", budget) else None,
                    "intended_formats": [f for f in values["formats"].split("\n") if f],
                },
                folder_id=folder_id_of(data),
            )
        except DeckError as exc:
            return await my_decks_response(
                request, sub, sid, new_package={"values": values, "error": str(exc)}, status=400
            )
        return await apply_now(sub, p["proposal_id"], ok="created")

    # -- one deck ---------------------------------------------------------------
    async def deck_page_response(
        request: Request,
        sub: str,
        sid: str | None,
        deck_id: str,
        *,
        box: str = "",
        box_error: str = "",
        box_values: dict[str, str] | None = None,
        status: int = 200,
        templates: list[str] | None = None,
        color_tags: list[dict[str, Any]] | None = None,
    ) -> Response:
        """The deck page. ``box`` names one of its boxes (deckpage.DECK_BOXES, or one of the
        release-2 boxes, deck_extras.EXTRA_BOXES) drawn open: the old /export and /delete
        addresses render the page this way, and a refused clone or delete comes back with
        ``box_error`` in its box. ``templates``: the member's template names, read for the Lock box
        drawn open from its address."""
        # The browser's Reload sends max-age=0 (no-cache for a hard reload): read Archidekt again.
        reload = re.search(r"max-age=0|no-cache", request.headers.get("cache-control", "")) is not None
        try:
            deck = await decks.page_deck(sub, deck_id, fresh=reload)
        except DeckError as exc:
            return h.deck_problem(request, exc, sub=sub, sid=sid)
        link = state.db.get_link(sub)
        own = bool(link) and owns_deck(deck, link) is True
        if box == "delete" and not own:
            box, box_error = "", ""
        stats = deck_stats.compute(deck)
        art = featured(deck)
        if own and art is not None:  # the deck list shows covers for the member's own decks
            state.db.save_deck_cover(deck.id, art.scryfall_uid, art.name, owner_sub=sub)
        notice = ""
        qp = request.query_params
        ok_code, err_code = qp.get("ok"), qp.get("err")
        if ok_code in DECK_OK_MESSAGES:
            notice = f"<p class='notice ok'>{_esc(DECK_OK_MESSAGES[ok_code])}</p>"
        elif ok_code in deck_extras.OK_MESSAGES:
            notice = deck_extras.notice_html(ok_code, (qp.get("n") or "")[:200])
        elif err_code in DECK_ERR_MESSAGES:
            # Only known codes; the text is fixed here, so a link cannot choose the words.
            notice = f"<p class='notice error'>{_esc(DECK_ERR_MESSAGES[err_code])}</p>"
        if deck.link_notice:  # the member's link was dropped while reading this deck
            notice = f"<p class='notice error'>{_esc(deck.link_notice)}</p>" + notice
        being_made = decks.being_made(deck.id)  # a new deck whose cards are still going in
        if being_made:
            notice = f"<p class='notice'>{_esc(being_made)}</p>" + notice
        # Archidekt marks each deck card with the copies the signed-in member owns ("owned" on the
        # card when the deck is read with their session); the gateway's green dot is that number.
        owned = {c.name.lower(): c.owned for c in deck.cards if c.owned} if link else None
        view, remember = prefs.view_choice(state.db, sub, request, "deck", VIEWS, "text")
        csrf = _csrf(s, sid)
        folders = None
        if csrf and link:
            # the Clone box's folder list, from the member cache (no wait: a cold tree leaves the
            # top level as the one choice)
            folders, _late = await folder_tree(sub, wait=0.0)
        body = deck_page_html(
            deck,
            stats,
            own=own,
            csrf=csrf,
            writes_enabled=s.writes_enabled,
            forge_on=bool(s.forge_url),
            owned=owned or None,
            view=view,
            group=qp.get("group") or "category",
            sort=qp.get("sort") or "name",
            q=(qp.get("q") or "").strip()[:80],
            notice=notice,
            waiting=waiting_changes(
                state.db, sub, deck.id, own=own, risk_of=decks.proposal_risk
            ),  # waiting_strip.py
            boxes=deck_boxes_html(
                deck,
                own=own,
                csrf=csrf,
                backups=bool(state.live.get("archidekt_backups")),
                folders=folders,
                open_box=box if box in DECK_BOXES else "",
                box_error=box_error,
                box_values=box_values,
            )
            + deck_extras.boxes_html(
                deck,
                own=own,
                csrf=csrf,
                open_box=box if box in deck_extras.EXTRA_BOXES else "",
                box_error=box_error,
                box_values=box_values,
                templates=templates,
                color_tags=color_tags,
            ),
            before=deck_extras.help_strip_html(deck, actions.deck_help_of(deck)) if own and csrf else "",
        ) + prefs.page_prefs(state.db, sub, f"{prefs.STACK}{deck.id}:", prefs.EDHPLAY_STEPS, prefs.DECKSTATS)
        titles = {
            "export": "Export",
            "delete": "Delete",
            "compare": "Compare",
            "clone": "Clone",
            "help": "Request deck help",
            "delist": "Delist",
            "report": "Report",
            "reportcard": "Report",
            "reportcomment": "Report",
            "pdf": "Registration PDF",
            "package": "Add a package",
            "lock": "Lock personal categories",
            "contest": "Contest entry",
            "withdraw": "Withdraw contest entry",
        }
        title = f"{titles[box]}: {deck_title(deck)}" if box in titles else deck_title(deck)
        resp = page(
            title,
            body,
            sub=sub,
            sid=sid,
            status=status,
            two_pane=True,
            current="/decks",
            scripts=True,
            csp=DECK_CSP,
            heading=False,
            deck_css=True,
            # social.js: the banner's like, bookmark and follow and the comments; export.js: the
            # Export box's Copy buttons
            extra_scripts=("social.js", "deckstats.js", "printpick.js", "export.js", "deckextras.js"),
            extra_css=deck_extras.CSS,
        )
        return prefs.remember_view(state.db, sub, resp, "deck", view, secure=secure) if remember else resp

    h.deck_page_response = deck_page_response  # the Export and Delete addresses (companion_tools, _settings)

    @server.custom_route("/decks/{deck_id}", methods=["GET"], include_in_schema=False)
    async def deck_page(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        from .forum import thread_of

        if thread_of(deck_id) is not None:  # a forum reply's History and Changes rows link here
            return RedirectResponse(f"/forum/thread/{thread_of(deck_id)}", status_code=303)
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}")
        return await deck_page_response(request, sub, sid, deck_id)

    @server.custom_route("/decks/{deck_id}/clone", methods=["POST"], include_in_schema=False)
    async def clone_deck(request: Request) -> Response:
        """The Clone box's form: the copy's name and folder (decks.propose_clone, applied at once as
        the member's own press). A refusal reopens the page with the box, its words and the typed
        values."""
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(f"/decks/{deck_id}")
        data = await form(request)
        if not check_csrf(sid, data):
            return page("Deck", EXPIRED, sub=sub, sid=sid, status=403)
        current_client.set(BROWSER_CLIENT_ID)
        fid = (data.get("folder_id") or "").strip()
        values = {"name": data.get("name") or "", "folder_id": fid}
        try:
            if fid and not re.fullmatch(r"[0-9]{1,12}", fid):
                raise DeckError("invalid", "That folder is not in your Archidekt account.")
            p = await decks.propose_clone(
                sub, deck_id, data.get("name") or None, folder_id=int(fid) if fid else None
            )
        except DeckError as exc:
            if busy := busy_response(exc, request, page, sub=sub, sid=sid):
                return busy
            # someone else's deck (or no link) goes back to its page with the message; a folder
            # that is not the member's ("not_found" with a folder picked) stays in the box
            if exc.kind in ("not_linked", "forbidden", "auth") or (exc.kind == "not_found" and not fid):
                code = exc.kind if exc.kind in DECK_ERR_MESSAGES else "invalid"
                return RedirectResponse(f"/decks/{deck_id}?err={code}", status_code=303)
            return await deck_page_response(
                request, sub, sid, deck_id, box="clone", box_error=str(exc), box_values=values, status=400
            )
        return await apply_now(sub, p["proposal_id"], ok="created")
