"""Companion pages: the deck list, a new deck, one deck and its clone button."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import deck_stats, prefs
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
    VIEWS,
    covers_for,
    deck_list_controls_html,
    deck_page_html,
    decks_unreadable_html,
    featured,
    reread_href,
)
from .decks import DeckError, current_client, owns_deck
from .folderview import FOLDER_CSS, arrange, level_html, resolve_folder
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
    icon,
)
from .waiting_strip import waiting_changes

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState


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
            tree = await decks.folder_view(sub, wait=wait)
        except DeckError:
            return None, False
        return tree, tree is None

    # -- my decks ---------------------------------------------------------------
    @server.custom_route("/decks", methods=["GET"], include_in_schema=False)
    async def decks_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/decks")
        qp = request.query_params
        if qp.get("reread"):  # Try again on the Q64 notice: read the list again, then show it
            if prefs.from_this_site(request):  # a link on another site only shows the page
                decks.reread_deck_list(sub)
            rest = urlencode([(k, v) for k, v in qp.multi_items() if k != "reread"])
            return RedirectResponse("/decks" + (f"?{rest}" if rest else ""), 303)
        chosen, remember = prefs.view_choice(state.db, sub, request, "decks", LIST_VIEWS, "grid")
        q, order, view, folder = list_query(qp, chosen)
        rows, problem, failure = await my_decks(sub, wait=decks.deck_list_wait)
        if failure is not None and (busy := busy_response(failure, request, page, sub=sub, sid=sid)):
            return busy
        pending = rows is None  # cold start: the shell goes out now, decks.js fills the list
        unreadable = bool(getattr(rows, "unreadable", 0))  # entries Archidekt left unreadable (Q64)
        hidden = 0  # the gateway's backup copies, kept out of this list (D-03); the list is warm now
        if not pending and not problem and not unreadable:  # unreadable: the list is not kept (Q64)
            try:
                hidden = len(await decks.backup_copies(sub, wait=0.0) or [])
            except DeckError:
                hidden = 0
        tree, tree_pending = None, False
        if not pending and not problem:  # Archidekt failing on the list: no second request
            tree, tree_pending = await folder_tree(sub, wait=decks.deck_list_wait)
        pending = pending or tree_pending  # the folders are still being read: decks.js fills the list
        folder_id = resolve_folder(tree, folder)
        level = arrange(rows or [], tree, folder_id=folder_id, q=q, order=order)
        total = level.total
        if not pending:  # the toolbar keeps the folder shown (an old name address becomes its id)
            folder = str(folder_id) if folder_id is not None else ""
        unread_html = (
            decks_unreadable_html(some_shown=total > 0, again=reread_href("/decks", qp)) if unreadable else ""
        )
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
                {
                    "shape": "list",
                    "q": q,
                    "order": order,
                    "view": view,
                    "folder": request.query_params.get("folder") or "",
                }
            )
            listing = f"<div data-decks-src='{_esc(src)}' aria-busy='true'>{deck_list_skeleton(view)}</div>"
        else:
            shown = level.decks
            covers = covers_for(shown, state.db.deck_covers([str(d["id"]) for d in shown]))
            listing = (f"<p class='notice error'>{_esc(problem)}</p>" if problem else "") + unread_html
            if not (unreadable and not total):  # nothing read: the notice instead of "No decks yet"
                listing += level_html(level, covers=covers, q=q, view=view, order=order)
        body = (
            notice
            + deck_list_controls_html(
                q=q,
                order=order,
                view=view,
                folder=folder,
                total=None if unreadable and not total else total,
                pending=pending,
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
        resp = page(
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
            extra_css=FOLDER_CSS,
        )
        return prefs.remember_view(state.db, sub, resp, "decks", view, secure=secure) if remember else resp

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
            out["html"] = recent_panel_inner(
                None if problem else recent, covers, unreadable=bool(getattr(rows, "unreadable", 0))
            )
            out["count"] = len(recent)
        else:
            unreadable = bool(getattr(rows, "unreadable", 0))  # Q64, as on /decks
            tree = None
            if rows is not None:
                tree, _late = await folder_tree(sub, wait=DECKS_JSON_TIMEOUT)
            level = arrange(rows or [], tree, folder_id=resolve_folder(tree, folder), q=q, order=order)
            shown, total = level.decks, level.total
            covers = covers_for(shown, state.db.deck_covers([str(d["id"]) for d in shown]))
            out["html"] = (f"<p class='notice error'>{_esc(problem[1])}</p>" if problem else "") + (
                decks_unreadable_html(some_shown=total > 0, again=reread_href("/decks", qp))
                if unreadable
                else ""
            )
            if not (unreadable and not total):
                out["html"] += level_html(level, covers=covers, q=q, view=view, order=order)
            out["count"] = len(shown)
            out["total"] = None if unreadable and not total else total  # no count it doesn't know
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
        # The browser's Reload sends max-age=0 (no-cache for a hard reload): read Archidekt again.
        reload = re.search(r"max-age=0|no-cache", request.headers.get("cache-control", "")) is not None
        try:
            deck = await decks.page_deck(sub, deck_id, fresh=reload)
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
        own = bool(link) and owns_deck(deck, link) is True
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
        view, remember = prefs.view_choice(state.db, sub, request, "deck", VIEWS, "text")
        body = deck_page_html(
            deck,
            stats,
            own=own,
            csrf=_csrf(s, sid),
            writes_enabled=s.writes_enabled,
            forge_on=bool(s.forge_url),
            owned=owned or None,
            view=view,
            group=qp.get("group") or "category",
            sort=qp.get("sort") or "name",
            q=(qp.get("q") or "").strip()[:80],
            notice=notice,
            waiting=waiting_changes(state.db, sub, deck.id, own=own),  # waiting_strip.py (S4)
        ) + prefs.page_prefs(state.db, sub, f"{prefs.STACK}{deck.id}:", prefs.EDHPLAY_STEPS, prefs.DECKSTATS)
        resp = page(
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
            # social.js: the banner's like, bookmark and follow and the comments (Track A's file)
            extra_scripts=("social.js", "deckstats.js", "printpick.js"),
        )
        return prefs.remember_view(state.db, sub, resp, "deck", view, secure=secure) if remember else resp

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
