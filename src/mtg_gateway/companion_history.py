"""Companion pages: History (snapshots, restores, stored reports) and Activity."""

from __future__ import annotations

import asyncio
import calendar
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from . import activity_view, card_models_view, ui
from .companion import (
    DECKS_JSON_TIMEOUT,
    _filename,
    stats_strip,
)
from .db import SNAPSHOT_HELD_RESTORE
from .decks import DeckError, current_client, mark_gone
from .history_view import (
    DELETED_NOTICES,
    HISTORY_CSS,
    MAX_OFFSET,
    NOTHING_YET,
    PAGE,
    PAGE_SUB_TEXT,
    TYPES,
    backup_copies_html,
    build_events,
    deck_filter_groups,
    delete_box_html,
    details_panel,
    events_html,
    is_filtered,
    query_string,
    read_query,
    toolbar_html,
    trend_html,
    when_since,
)
from .history_view import _taken as _taken_text
from .pages import (
    BROWSER_CLIENT_ID,
    _csrf,
    browser_session,
    login_redirect,
)
from .report_view import (
    REPORT_CSS,
    report_export_html,
    report_markdown,
)
from .theme import (
    VIZ_CSS,
    icon,
    time_text,
)
from .theme import (
    esc as _esc,
)

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .reports import ReportService


# A missing report, or someone else's (NEW-WORDING 34): the shared small-page look.
REPORT_NOT_FOUND_TITLE = "Report not found"
REPORT_NOT_FOUND_TEXT = "This report isn't in your history. It may have been deleted."


def add_history_routes(
    server: MCPServer, state: AppState, reports: ReportService, h: SimpleNamespace
) -> None:
    """History, restore, stored reports and Activity."""
    check_csrf = h.check_csrf
    decks = h.decks
    form = h.form
    page = h.page
    s = h.s

    # -- history --------------------------------------------------------------
    @server.custom_route("/history", methods=["GET"], include_in_schema=False)
    async def history(request: Request) -> Response:
        """Snapshots, applied restores and reports as one list, newest first,
        grouped by day (or by deck), narrowed by deck, a "When" preset of UTC days and a deck-name
        search, 25 at a time, under a row of widgets; each row's Details beside the list or on its
        own page; then the backup copies on Archidekt. Each kind is narrowed and paged in SQL, so
        a deck's own history keeps its older entries however busy the member's other decks are.
        Proposals are on the Proposals page."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        query = read_query(request.query_params)
        deck_id = query["deck_id"] or None
        want = query["type"]
        since = when_since(query["when"])
        search = query["q"] or None
        now = time.time()
        # Each kind fetched up to the end of this page plus one: the lists merge by time, so the
        # page is sliced after the merge.
        fetch = query["offset"] + PAGE + 1
        restores: list[dict[str, Any]] = []
        snapshots: list[dict[str, Any]] = []
        reps: list[dict[str, Any]] = []
        if want in ("all", "restores"):
            restores = decks.list_proposals(
                sub,
                full=True,
                deck_id=deck_id,
                kinds=["restore"],
                states=["applied"],
                search=search,
                since=since,
                limit=fetch,
            )
            for r in restores:  # when the snapshot it put back was taken (its first review row)
                full = state.db.get_proposal(r["id"], sub) or {}
                head = (full.get("rows") or [{}])[0]
                r["taken"] = head.get("taken") if head.get("kind") == "restore" else None
        if want in ("all", "snapshots"):
            snapshots = decks.list_snapshots(sub, deck_id=deck_id, search=search, since=since, limit=fetch)
        if want in ("all", "reports"):
            reps = reports.list(sub, deck_id, limit=fetch, search=search, since=since)
        client_ids = {r.get("created_by_client") for r in reps if r.get("created_by_client")}
        names = {cid: state.db.client_name(cid) for cid in client_ids if not cid.startswith("__")}
        # The member's Archidekt deck list, the one the backup copies table reads too (cached; no
        # request per snapshot or deck): a deck or a backup copy deleted on Archidekt is marked, a
        # deleted deck's snapshots offer no Restore, and the deck filter shows live names.
        try:
            listed = await asyncio.wait_for(
                decks.on_archidekt(sub, wait=decks.deck_list_wait), DECKS_JSON_TIMEOUT
            )
            live = await asyncio.wait_for(decks.list_decks_quick(sub, wait=0), DECKS_JSON_TIMEOUT)
        except (DeckError, TimeoutError):
            listed, live = None, None  # not linked, or Archidekt unavailable: shown as recorded
        if snapshots:
            mark_gone(snapshots, listed)
        csrf_in = f"<input type='hidden' name='csrf' value='{_esc(_csrf(s, sid))}'>"
        events = build_events(restores, snapshots, reps, csrf_input=csrf_in, client_names=names)
        shown = events[query["offset"] : query["offset"] + PAGE]
        seen = {**reports.decks_seen(sub), **state.db.history_decks(sub)}
        current, gone = deck_filter_groups(
            seen,
            live,
            listed=listed["ids"] if listed else None,
            own=state.db.own_history_deck_ids(
                sub,
                account_id=listed["account_id"],
                username=listed["username"],
                linked_at=listed["linked_at"],
            )
            if listed
            else set(),
        )
        counts = state.db.history_counts(sub, deck_id=deck_id, search=search, since=since)
        counts["all"] = counts["snapshots"] + counts["restores"] + counts["reports"]
        total = counts[want]
        # The backup copies the gateway made on Archidekt: kept out of Home and My decks, listed
        # under the list (and on a deck's own history), counted in their widget.
        try:
            copies = await asyncio.wait_for(
                decks.backup_copies(sub, deck_id=deck_id, wait=decks.deck_list_wait, prefer_fresh=True),
                DECKS_JSON_TIMEOUT,
            )
        except (DeckError, TimeoutError):
            copies = []  # not linked, or Archidekt unavailable: the table is simply absent
        # The table sits under the first, unfiltered page (and a deck's own); the widget counts always.
        unfiltered = want == "all" and not search and not query["when"] and not query["offset"]
        copies_panel = (
            backup_copies_html(
                copies, folder=decks.backup_folder, deck_id=deck_id, names={**seen, **current}, now=now
            )
            if unfiltered
            else ""
        )
        # the deck's report trends (none with fewer than two reports)
        trend = trend_html(reports.trend(sub, deck_id)) if deck_id else ""
        back = (
            "<p class='hy-back'>" + ui.cap_link("← Back to the deck", f"/decks/{deck_id}") + "</p>"
            if deck_id
            else ""
        )
        if shown:
            listing = events_html(shown, group=query["group"], query=query, now=int(now))
            newer = (
                "/history" + query_string(query, offset=max(0, query["offset"] - PAGE), open="")
                if query["offset"]
                else None
            )
            older = (
                "/history" + query_string(query, offset=query["offset"] + PAGE, open="")
                if query["offset"] + PAGE < total and query["offset"] + PAGE <= MAX_OFFSET
                else None
            )
            listing += ui.pager(query["offset"], max(total, len(shown)), newer_href=newer, older_href=older)
        elif query["offset"]:
            listing = "<div class='ui-card hy-empty'><p>No older entries.</p></div>" + ui.pager(
                0, 0, newer_href=None, older_href=None
            )
            listing += (
                f"<p><a class='btn btn-plain' href='/history{_esc(query_string(query, offset=0))}'>"
                f"{ui.NEWER}</a></p>"
            )
        elif is_filtered(query):
            listing = "<div class='ui-card hy-empty'><p>Nothing matches these filters.</p></div>"
        else:
            listing = f"<div class='ui-card hy-empty'><p>{_esc(NOTHING_YET)}</p></div>"
        chips = ui.chips(
            ui.Chip(
                text,
                counts[t],
                "/history" + query_string(query, type=t, offset=0, open=""),
                current=t == want,
            )
            for t, text in TYPES
        )
        if is_filtered({**query, "type": "all"}):
            chips = chips.replace(
                "</nav>",
                f"<a class='ui-chip' href='/history"
                f"{_esc(query_string({'deck_id': query['deck_id']}) if deck_id else '')}'>"
                f"{icon('x')}Clear filters</a></nav>",
                1,
            )
        head = (
            f"<div class='list-head'><h2>{_esc(dict(TYPES)[want])} "
            f"<span class='ui-sub'>{counts[want]:,}</span></h2>"
            f"<span class='ui-sub'>Newest first</span></div>"
        )
        list_html = (
            ui.assistant_head(
                "/history",
                "History" if not deck_id else "Deck history",
                sub_html=f"{_esc(PAGE_SUB_TEXT)} " + ui.cap_link("Proposals", "/proposals") + ".",
            )
            + back
            + _history_widgets(sub, deck_id, copies, now=now)
            + trend
            + head
            + chips
            + toolbar_html(query, current, gone=gone)
            + listing
            + copies_panel
        )
        opened = next((e for e in shown if e["key"] == query["open"]), None) if query["open"] else None
        extra = ""
        if opened and opened["kind"] in ("report", "snapshot"):
            # Delete…: the shared confirm box, open when the address asks (a row menu's Delete…)
            oid = opened["key"][2:]
            held = state.db.snapshot_held(oid, sub) or "" if opened["kind"] == "snapshot" else ""
            opener, box = delete_box_html(
                opened["kind"],
                f"/history/{opened['kind']}s/{oid}/delete",
                csrf_in,
                box_id="hy-del",
                held=held,
                open=request.query_params.get("box") == "delete",
                close_href="/history" + query_string(query, open=opened["key"]),
            )
            extra = f"<div class='ui-actions'>{opener}</div>{box}"
        deleted = DELETED_NOTICES.get(request.query_params.get("deleted") or "", "")
        if deleted:  # the address names what went; the words are the page's own
            list_html = f"<p class='notice ok' role='status'>{_esc(deleted)}</p>" + list_html
        body = ui.split(list_html, details_panel(opened, query, extra_html=extra) if opened else "")
        return page(
            "History" if not deck_id else "Deck history",
            body,
            sub=sub,
            sid=sid,
            current="/history",
            extra_css=HISTORY_CSS,
            heading=False,
            main_class="ui-wide",
        )

    def _taken_ts(text: Any) -> int | None:
        return _taken_text(str(text or "")) if text else None

    def _history_widgets(
        sub: str, deck_id: str | None, copies: list[dict[str, Any]] | None, *, now: float
    ) -> str:
        """Snapshots kept, the last restore, reports this month and the backup copies."""
        st = state.db.snapshot_stats(sub, deck_id)
        newest = f" · newest {ui.when_text(st['newest'], now=now)}" if st["newest"] else ""
        w1 = ui.widget(
            "Snapshots kept",
            "bookmark",
            st["count"],
            unit=f"on {st['decks']} deck{'' if st['decks'] == 1 else 's'}",
            detail=f"Up to {state.db.snapshots_keep()} per deck{newest}",
            actions=[ui.button_link("Restore a deck…", "/history?type=snapshots&group=deck")]
            if st["count"]
            else [],
        )
        last = decks.list_proposals(sub, deck_id=deck_id, kinds=["restore"], states=["applied"], limit=1)
        if last:
            r = last[0]
            full = state.db.get_proposal(r["id"], sub) or {}
            head = (full.get("rows") or [{}])[0]
            taken = _taken_ts(head.get("taken")) if head.get("kind") == "restore" else None
            at = int(r.get("applied_at") or r["created_at"])
            back_to = f", back to {ui.when_text(taken, now=now)}" if taken else ""
            w2 = ui.widget(
                "Last restore",
                "undo",
                ui.when_text(at, now=now),
                detail=f"{r.get('deck_name') or r['deck_id']}{back_to}",
                actions=[ui.button_link("Open it", f"/proposals/{r['id']}")],
            )
        else:
            w2 = ui.widget("Last restore", "undo", "—", detail="No restore yet.")
        t = time.gmtime(now)
        month = int(calendar.timegm((t.tm_year, t.tm_mon, 1, 0, 0, 0)))
        n_month = state.db.history_counts(sub, deck_id=deck_id, since=month)["reports"]
        latest = reports.list(sub, deck_id, limit=1)
        w3 = ui.widget(
            "Reports",
            "report",
            n_month,
            unit="this month",
            detail=(
                f"Latest: {latest[0].get('deck_name') or 'an unnamed deck'}, "
                f"{ui.when_text(latest[0].get('taken_at'), now=now)}"
                if latest
                else "No report yet."
            ),
            actions=[ui.button_link("All reports", "/reports")],
        )
        n_copies = len(copies or [])
        w4 = ui.widget(
            "Backup copies",
            "clone",
            n_copies,
            unit="on Archidekt",
            detail=f'In the folder "{decks.backup_folder}"',
            # to the table, which is on the unfiltered page
            actions=[
                ui.button_link("See them", "/history" + query_string({"deck_id": deck_id or ""}) + "#backups")
            ]
            if n_copies
            else [],
        )
        return ui.widgets([w1, w2, w3, w4])

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

    def _expired(sub: str, sid: str | None) -> Response:
        return page(
            "History",
            "<p class='notice error'>This form expired. Reload and try again.</p>",
            sub=sub,
            sid=sid,
            status=403,
        )

    @server.custom_route("/history/reports/{rid}/delete", methods=["POST"], include_in_schema=False)
    async def report_delete(request: Request) -> Response:
        """Delete one of the member's reports: the confirm box's solid red button on History or on
        the report's page. The member's own only (any of theirs, whichever app ran it)."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        data = await form(request)
        if not check_csrf(sid, data):
            return _expired(sub, sid)
        try:
            reports.delete(sub, request.path_params["rid"])
        except DeckError:
            return _not_found(sub, sid)
        return RedirectResponse("/history?deleted=report", status_code=303)

    @server.custom_route("/history/snapshots/{snap}/delete", methods=["POST"], include_in_schema=False)
    async def snapshot_delete(request: Request) -> Response:
        """Delete one of the member's snapshots, unless a waiting restore puts the deck back to it
        or it holds the record of a backup copy the gateway may still delete (Database.snapshot_held)."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        data = await form(request)
        if not check_csrf(sid, data):
            return _expired(sub, sid)
        snap = request.path_params["snap"]
        found = state.db.get_snapshot(snap, sub)
        if found is None:
            return page(
                "Snapshot not found",
                "<div class='card'><p>This snapshot isn't in your history. It may have been deleted.</p>"
                "<div class='actions'><a class='btn btn-primary' href='/history'>Back to History</a></div>"
                "</div>",
                sub=sub,
                sid=sid,
                status=404,
            )
        held = state.db.snapshot_held(snap, sub)
        if held or not state.db.delete_snapshot(snap, sub):
            return page(
                "History",
                f"<p class='notice error'>{_esc(held or SNAPSHOT_HELD_RESTORE)}</p>"
                "<p><a class='btn btn-plain' href='/history'>Back to History</a></p>",
                sub=sub,
                sid=sid,
                status=409,
            )
        state.db.audit(
            "snapshot_deleted", sub=sub, detail={"snapshot_id": snap, "deck_id": found.get("deck_id")}
        )
        return RedirectResponse("/history?deleted=snapshot", status_code=303)

    def _report_or_404(sub: str, rid: str) -> dict[str, Any] | None:
        try:
            return reports.get(sub, rid)
        except DeckError:
            return None

    def _not_found(sub: str, sid: str | None) -> Response:
        """A missing report, or someone else's: the shared small-page look, with the way back."""
        return page(
            REPORT_NOT_FOUND_TITLE,
            "<div class='card'>"
            f"<p>{REPORT_NOT_FOUND_TEXT}</p>"
            "<div class='actions'><a class='btn btn-primary' href='/history'>Back to History</a></div></div>",
            sub=sub,
            sid=sid,
            status=404,
        )

    def _card_types(names: list[Any]) -> dict[str, str]:
        """The type lines the gateway already holds for these cards (the scanner's Scryfall cache,
        read in memory): nothing is fetched for a report page. Unknown cards show their name alone."""
        scan = getattr(state, "scan", None)
        client = getattr(scan, "scryfall", None)
        cache = getattr(client, "cards", None)
        if cache is None or not hasattr(cache, "get"):
            return {}
        out: dict[str, str] = {}
        for raw in names:
            name = str(raw or "").strip()
            if not name:
                continue
            for key in (f"named:e::{name.lower()}", f"named:f::{name.lower()}"):
                hit = cache.get(key)
                if isinstance(hit, dict) and hit.get("type_line"):
                    out[name] = str(hit["type_line"])
                    break
        return out

    @server.custom_route("/history/reports/{rid}", methods=["GET"], include_in_schema=False)
    async def report_page(request: Request) -> Response:
        """One stored report as a page a person reads: the deck, the headline numbers of the goldfish
        simulation with their confidence intervals, charts, what the quick solo test leaves out,
        the validation verdict and the deck statistics; the service's raw text folded away."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        rid = request.path_params["rid"]
        r = _report_or_404(sub, rid)
        if r is None:
            return _not_found(sub, sid)
        safe = _esc(rid)
        md = report_markdown(r, report_url=_report_url(r))
        # the export buttons as plain buttons at the header's end
        actions = (
            f"<a class='btn btn-plain' href='/history/reports/{safe}/export.md' download>{icon('download')} "
            f"Markdown</a><a class='btn btn-plain' href='/history/reports/{safe}/export.html' download>"
            f"{icon('download')} HTML page</a><button type='button' class='btn-plain copybtn' "
            f"data-copy='rep-md'>{icon('copy')}<span class='label'>Copy as Markdown</span></button>"
            f"<textarea id='rep-md' class='sr-only' readonly aria-label='The report as Markdown'>{_esc(md)}"
            "</textarea>"
        )
        csrf_in = f"<input type='hidden' name='csrf' value='{_esc(_csrf(s, sid) or '')}'>"
        opener, delete_box = delete_box_html(
            "report",
            f"/history/reports/{safe}/delete",
            csrf_in,
            box_id="rep-del",
            open=request.query_params.get("box") == "delete",
            close_href=f"/history/reports/{safe}",
        )
        actions += opener
        # the saved descriptions this run played that are still the member's own;
        # imported here so the module's shared import block stays as it is (ownership table)
        from .report_view import MODEL_NOTICES, front_face, kept_models

        used = [u["name"] for u in kept_models(r)]
        mine = frozenset(
            front_face(str(k.get("name") or "")).casefold()
            for k in reports.models.for_cards(sub, used).values()
        )
        # only while descriptions are saved: the notice comes from the address, which anyone can edit
        notice = (
            MODEL_NOTICES.get(request.query_params.get("models") or "", "")
            if reports.models.available
            else ""
        )
        kind = "notice error" if request.query_params.get("models") == "error" else "notice"
        # Play more games that started nothing says why (the address holds a code, never the text)
        refused = _more_notices().get(request.query_params.get("more") or "", "")
        if refused:
            notice, kind = _esc(refused), "notice warn"
        from .report_page import REPORT_PAGE_CSS, report_page_html

        # the member's own saved descriptions, as a box the mentions open (card_models_view)
        my_models = (
            card_models_view.box_html(
                reports.models.mine(sub), csrf=_csrf(s, sid) or "", back=f"/history/reports/{safe}"
            )
            if reports.models.available
            else ""
        )
        left_out: list[Any] = []
        for block in (r.get("forge"), r.get("forge_solo")):
            if isinstance(block, dict) and isinstance(block.get("not_played"), list):
                left_out += block["not_played"]
        body = report_page_html(
            r,
            stats_html=stats_strip(r.get("stats")),
            actions_html=actions,
            notice_html=f"<p class='{kind}'>{notice}</p>" if notice else "",
            reused=request.query_params.get("reused") == "1",
            removable=mine,
            csrf=_csrf(s, sid) or "",
            card_types=_card_types(left_out) if left_out else {},
            my_models=my_models + delete_box,
        )
        return page(
            f"Report: {r['deck_name']}",
            body,
            sub=sub,
            sid=sid,
            heading=False,
            current="/reports",
            viewer_only=True,  # the card viewer for "Open card" on the left-out cards (report.js)
            extra_scripts=("export.js", "forge-live.js", "report.js", "cardmodels.js"),
            extra_css=REPORT_CSS + REPORT_PAGE_CSS + card_models_view.CSS,
            main_class="ui-wide",
        )

    # -- My card models: the member's own saved descriptions (card_models_view) -------------------
    @server.custom_route(card_models_view.PAGE, methods=["GET"], include_in_schema=False)
    async def my_models_page(request: Request) -> Response:
        """The box as a page of its own (the opener's address without scripts): the list open,
        ``?remove=`` a card's confirm open, ``?models=`` the notice after a Remove."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(card_models_view.PAGE)
        if not reports.models.available:
            return Response(status_code=404)
        qp = request.query_params
        mine = reports.models.mine(sub)
        remove = str(qp.get("remove") or "")[:286]
        if remove and remove.casefold() not in {m["name"].casefold() for m in mine}:
            remove = ""  # only a card of the member's own list gets a confirm
        from .report_view import MODEL_NOTICES

        notice = MODEL_NOTICES.get(qp.get("models") or "", "")
        kind = "error" if qp.get("models") == "error" else "ok"
        body = (ui.notice(notice, kind, role="status") if notice else "") + card_models_view.box_html(
            mine, csrf=_csrf(s, sid) or "", back=card_models_view.PAGE, open=True, remove=remove
        )
        return page(
            card_models_view.TITLE,
            body,
            sub=sub,
            sid=sid,
            heading=False,
            current="/reports",
            viewer_only=True,
            extra_scripts=("cardmodels.js",),
            extra_css=card_models_view.CSS,
        )

    @server.custom_route(card_models_view.REMOVE_ACTION, methods=["POST"], include_in_schema=False)
    async def my_model_remove(request: Request) -> Response:
        """Remove from My card models: the same removal and words as the report page's, then back to
        the page the box was opened from (one of the member's own pages) with the notice."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect(card_models_view.PAGE)
        if not reports.models.available:
            return Response(status_code=404)
        data = await form(request)
        if not check_csrf(sid, data):
            return page(
                card_models_view.TITLE,
                "<p class='notice error'>This form expired. Reload and try again.</p>",
                sub=sub,
                sid=sid,
                status=403,
            )
        back = card_models_view.safe_back(data.get("back"))
        outcome = _remove_model(sub, str(data.get("card") or ""))
        sep = "&" if "?" in back else "?"
        return RedirectResponse(f"{back}{sep}models={outcome}", status_code=303)

    def _remove_model(sub: str, card: str) -> str:
        """Remove the member's own saved description of ``card``: removed, gone or error."""
        from .card_models import StoreError

        try:
            return "removed" if reports.models.remove(sub, card) else "gone"
        except StoreError:
            return "error"

    @server.custom_route("/history/reports/{rid}/models/remove", methods=["POST"], include_in_schema=False)
    async def report_model_remove(request: Request) -> Response:
        """The adder removes a saved card description from a report it played in.
        Only the member's own goes; the report itself keeps its record of what it played."""
        sub, sid = browser_session(state, request)
        rid = request.path_params["rid"]
        if not sub:
            return login_redirect("/history")
        # nothing to remove while kept models are switched off (card_models.SWITCHED_ON)
        if not reports.models.available:
            return Response(status_code=404)
        data = await form(request)
        if not check_csrf(sid, data):
            return page(
                "Report",
                "<p class='notice error'>This form expired. Reload and try again.</p>",
                sub=sub,
                sid=sid,
                status=403,
            )
        if _report_or_404(sub, rid) is None:
            return _not_found(sub, sid)
        outcome = _remove_model(sub, str(data.get("card") or ""))
        return RedirectResponse(f"/history/reports/{_esc(rid)}?models={outcome}", status_code=303)

    def _more_notices() -> dict[str, str]:
        from .reports import MORE_REFUSALS

        return MORE_REFUSALS

    @server.custom_route("/history/reports/{rid}/forge/more", methods=["POST"], include_in_schema=False)
    async def report_more_games(request: Request) -> Response:
        """Play more games: another round of Forge games against the same opponents, added to the
        report's earlier ones. A press that starts nothing comes back to the report saying why."""
        sub, sid = browser_session(state, request)
        rid = request.path_params["rid"]
        if not sub:
            return login_redirect("/history")
        data = await form(request)
        if not check_csrf(sid, data):
            return page(
                "Report",
                "<p class='notice error'>This form expired. Reload and try again.</p>",
                sub=sub,
                sid=sid,
                status=403,
            )
        try:
            await reports.play_more(sub, rid)
        except DeckError as exc:
            if exc.kind == "not_found":
                return _not_found(sub, sid)
            code = next((k for k, v in _more_notices().items() if v == str(exc)), "")
            if not code:
                raise
            return RedirectResponse(f"/history/reports/{_esc(rid)}?more={code}", status_code=303)
        return RedirectResponse(f"/history/reports/{_esc(rid)}", status_code=303)

    def _report_file(r: dict[str, Any], ext: str, body: str, media_type: str) -> Response:
        name = _filename(r.get("deck_name") or r["deck_id"])
        day = time_text(r.get("taken_at"))[:10]
        return Response(
            body,
            media_type=media_type,
            headers={
                "Content-Disposition": f'attachment; filename="{name}-report-{day}{ext}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def _report_url(r: dict[str, Any]) -> str:
        """The report's page on this gateway, for the downloads' links."""
        return f"{state.settings.public_url}/history/reports/{quote(str(r.get('report_id') or ''), safe='')}"

    @server.custom_route("/history/reports/{rid}/export.md", methods=["GET"], include_in_schema=False)
    async def report_export_md(request: Request) -> Response:
        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        r = _report_or_404(sub, request.path_params["rid"])
        if r is None:
            return _not_found(sub, _sid)
        md = report_markdown(r, report_url=_report_url(r))
        return _report_file(r, ".md", md, "text/markdown; charset=utf-8")

    @server.custom_route("/history/reports/{rid}/export.html", methods=["GET"], include_in_schema=False)
    async def report_export_html_file(request: Request) -> Response:
        """The report as one HTML file: inline styles and SVG, no scripts, nothing fetched."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/history")
        r = _report_or_404(sub, request.path_params["rid"])
        if r is None:
            return _not_found(sub, _sid)
        html_doc = report_export_html(
            r, stats_html=stats_strip(r.get("stats")), theme_css=VIZ_CSS, report_url=_report_url(r)
        )
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
        body = activity_view.member_body(state, sub, request.query_params)
        return page(
            "My activity",
            body,
            sub=sub,
            sid=sid,
            heading=False,
            main_class="ui-wide",
            extra_css=activity_view.MEMBER_CSS,
        )
