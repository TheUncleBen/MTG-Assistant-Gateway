"""Companion pages: History (snapshots, restores, stored reports) and Activity."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .companion import DECKS_JSON_TIMEOUT, _filename, event_detail_html, event_label, stats_strip
from .decks import DeckError, actor_label, current_client, mark_gone
from .history_view import (
    HISTORY_CSS,
    PAGE,
    backup_copies_html,
    build_events,
    deck_filter_groups,
    events_html,
    filter_bar_html,
    is_filtered,
    pager_html,
    read_query,
    trend_html,
    when_since,
)
from .pages import (
    BROWSER_CLIENT_ID,
    _csrf,
    _when,
    actor_html,
    browser_session,
    login_redirect,
)
from .report_view import (
    REPORT_CSS,
    report_body_html,
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
        # The member's Archidekt deck list, the one the backup copies panel reads too (cached; no
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
        events = build_events(proposals, snapshots, reps, csrf_input=csrf_in, client_names=names)
        has_more = len(events) > query["offset"] + PAGE
        shown = events[query["offset"] : query["offset"] + PAGE]
        current, gone = deck_filter_groups(
            {**reports.decks_seen(sub), **state.db.history_decks(sub)}, live, complete=listed is not None
        )
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
                    decks.backup_copies(sub, deck_id=deck_id, wait=decks.deck_list_wait, prefer_fresh=True),
                    DECKS_JSON_TIMEOUT,
                )
            except (DeckError, TimeoutError):
                copies = []  # not linked, or Archidekt unavailable: the panel is simply absent
            copies_panel = backup_copies_html(copies, folder=s.archidekt_backup_folder, deck_id=deck_id)
        # the deck's report trends (none with fewer than two reports)
        trend = trend_html(reports.trend(sub, deck_id)) if deck_id else ""
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
        bar = filter_bar_html(query, current, shown=len(shown), total_hint=hint, gone=gone)
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
            extra_scripts=("export.js", "forge-live.js"),
            extra_css=REPORT_CSS,
        )

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
            items.append(
                f"<li><span class='name'>{_esc(event_label(r['event']))}</span>"
                + actor_html(who)
                + f"<span class='muted small'>{event_detail_html(detail)}</span>"
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
