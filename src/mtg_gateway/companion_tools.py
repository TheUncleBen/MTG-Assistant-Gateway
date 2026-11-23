"""Companion pages: the browser editor, exports, test runs, Playtest, Goldfish and Compare."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import quote, urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import card_models_view, deck_stats, prefs, ui
from .archidekt import Deck, parse_deck
from .busy import _headers, busy_body, busy_json_response, busy_plan, busy_text_response
from .companion import (
    DECK_CSP,
    DECK_ERR_MESSAGES,
    DECKS_JSON_TIMEOUT,
    TEXT_PLAIN,
    _filename,
)
from .decklist import DecklistError, parse_decklist
from .deckpage import (
    COMPARE_CSS,
    GOLDFISH_CSS,
    GOLDFISH_GUIDE_PREF,
    compare_page_html,
    deck_check_html,
    deck_title,
    goldfish_page_html,
    precon_by_label,
)
from .decks import DeckError, current_client, is_busy, split_label
from .digits import is_digits
from .history_view import DELETED_NOTICES
from .pages import (
    BROWSER_CLIENT_ID,
    _csrf,
    browser_session,
    csrf_header_ok,
    login_redirect_here,
    read_limited,
)
from .reports import NO_FORGE_TO_SHARE, PAIR_NO_FORGE
from .reports_page import CHIPS as REPORTS_CHIPS
from .reports_page import PAGE as REPORTS_PAGE
from .reports_page import REPORTS_PAGE_CSS, reports_page_html
from .reports_page import Query as ReportsQuery
from .reports_page import in_chip as reports_in_chip
from .reports_page import matches as reports_matches
from .theme import (
    esc as _esc,
)
from .theme import (
    time_text,
)

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .reports import ReportService

# The Compare press when the deck lost its commander after the page loaded
COMPARE_NO_COMMANDER = (
    "Forge plays only decks with a commander, and the deck you are comparing from has none now, so no "
    "report was made."
)


def _checking(deck: Deck, how: dict[str, Any]) -> str:
    """The deck page's strip for a deck shown as last read while it is read again
    (decks.page_deck_live's ``checking``): static/feedback.js says when that read found a change."""
    return deck_check_html(deck.id, how.get("read_at"), reload=False) if how.get("checking") else ""


def add_deck_tool_routes(
    server: MCPServer, state: AppState, reports: ReportService, h: SimpleNamespace
) -> None:
    """Editor, exports, reports, Playtest, Goldfish and Compare."""
    check_csrf = h.check_csrf
    decks = h.decks
    form = h.form
    page = h.page
    s = h.s

    # -- the old deck editor's address ---------------------------------------------
    @server.custom_route("/decks/{deck_id}/edit", methods=["GET"], include_in_schema=False)
    async def edit_page(request: Request) -> Response:
        """The deck editor's old address. 0.7.48 folded the editor into the deck page itself (Add
        a card with its preview, every card's menu, drags between categories, More ways to add),
        so the address opens the deck page: ``add=`` opens the named card's preview there and
        ``scan_session=`` that scan's review (static/deck.js), and a ``#card-…`` or ``#cat-…``
        part still jumps to its card or category (the browser keeps it through the redirect)."""
        deck_id = request.path_params["deck_id"]
        keep = {
            k: (request.query_params.get(k) or "").strip()[:200]
            for k in ("add", "scan_session")
            if (request.query_params.get(k) or "").strip()
        }
        target = f"/decks/{quote(str(deck_id), safe='')}" + (f"?{urlencode(keep)}" if keep else "")
        return RedirectResponse(target, status_code=303)

    @server.custom_route("/decks/{deck_id}/export", methods=["GET"], include_in_schema=False)
    async def export_page(request: Request) -> Response:
        """The old Export page's address (F3): the deck page with the Export box open."""
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, f"/decks/{deck_id}/export")
        return await h.deck_page_response(request, sub, sid, deck_id, box="export")

    @server.custom_route("/decks/{deck_id}/export.archidekt.txt", methods=["GET"], include_in_schema=False)
    async def export_archidekt_txt(request: Request) -> Response:
        """The deck in Archidekt's own import syntax (printing, finish, categories with their
        flags, labels, sideboard rows), for Archidekt's Import dialog or the gateway's New deck."""
        from .decks import deck_to_archidekt_text

        return await _download(
            request, ".archidekt.txt", lambda deck: deck_to_archidekt_text(deck) + "\n", TEXT_PLAIN
        )

    @server.custom_route("/decks/{deck_id}/export.txt", methods=["GET"], include_in_schema=False)
    async def export_txt(request: Request) -> Response:
        """The plain list (export_formats.to_plain): names under Commander, Deck and Sideboard,
        no Archidekt marks. The Archidekt .txt is the one that keeps printings and categories."""
        from .export_formats import to_plain

        return await _download(request, ".txt", to_plain, TEXT_PLAIN)

    async def _download(request: Request, ext: str, make: Any, media_type: str) -> Response:
        """One export-only file for a deck the member may read: ``make(deck)`` gives the body."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/decks")
        if not prefs.from_this_site(request):  # a link on another site: Archidekt is not asked
            return Response(
                "Open this from the gateway's own pages.", 403, headers={"Cache-Control": "no-store"}
            )
        try:
            deck = await decks.get_any_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return busy_text_response(exc) or Response(str(exc), 404)
        name = _filename(deck.name or deck.id)
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

        return await _download(request, ".arena.txt", to_arena, TEXT_PLAIN)

    @server.custom_route("/decks/{deck_id}/edhplay.json", methods=["GET"], include_in_schema=False)
    async def export_edhplay(request: Request) -> Response:
        """The deck in EDHPlay's import format, for the deck page's Play on EDHPlay box (deck.js),
        which asks for it when the box opens. The deck as the deck page read it (page_deck: the same
        read, kept a short while, so no new Archidekt request while it is fresh), for anyone who may
        open that page. ``left_out`` names the sideboard copies past EDHPlay's 15; ``companion`` says
        the deck sends one, which EDHPlay's import leaves for the member to set, and
        ``no_commander`` that no commander is marked, so the member chooses one there."""
        from .export_formats import to_edhplay

        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=headers)
        if not prefs.from_this_site(request):  # a link on another site: Archidekt is not asked
            return JSONResponse({"ok": False, "error": "cross_site"}, 403, headers=headers)
        try:
            deck = await decks.page_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return busy_json_response(exc) or JSONResponse(
                {"ok": False, "error": exc.kind, "message": str(exc)},
                404 if exc.kind == "not_found" else 400,
                headers=headers,
            )
        edh = to_edhplay(deck)
        return JSONResponse(
            {
                "ok": True,
                "list": edh.text,
                "left_out": ", ".join(name if n == 1 else f"{n} {name}" for n, name in edh.left_out),
                "left_count": sum(n for n, _name in edh.left_out),
                "companion": edh.companion,
                "no_commander": not edh.commanders,
            },
            headers=headers,
        )

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
            return login_redirect_here(request, "/decks")
        if not prefs.from_this_site(request):  # a link on another site: Archidekt is not asked
            return Response(
                "Open this from the gateway's own pages.", 403, headers={"Cache-Control": "no-store"}
            )
        try:
            deck = await decks.get_any_deck(sub, request.path_params["deck_id"])
        except DeckError as exc:
            return busy_json_response(exc) or JSONResponse(
                {"ok": False, "error": exc.kind, "message": str(exc)}, 404
            )
        name = _filename(deck.name or deck.id)
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

        def make(deck: Deck) -> str:
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
                        # the tag's name only, as Archidekt's own export writes it ("Have", not
                        # "Have,#37d67a"); an untagged row (",#656565") is blank
                        cell(split_label(c.label)[0]),
                        "" if c.cmc is None else (int(c.cmc) if float(c.cmc).is_integer() else c.cmc),
                        cell(c.mana_cost),
                        cell(" ".join(c.supertypes + c.types)),
                        c.rarity,
                        "" if c.price is None else f"{c.price:.2f}",
                        c.scryfall_uid,
                    ]
                )
            return buf.getvalue()

        return await _download(request, ".csv", make, "text/csv; charset=utf-8")

    @server.custom_route("/decks/{deck_id}/report", methods=["POST"], include_in_schema=False)
    async def run_report(request: Request) -> Response:
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, f"/decks/{deck_id}")
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
        # forge=0 runs the report without Forge games (a quick goldfish-only run)
        no_forge = str(data.get("forge", "")).strip().lower() in ("0", "false", "off", "no")
        try:
            # answered once the report is stored: its page opens and fills in while the run goes on
            report = await reports.start(sub, deck_id, forge=False if no_forge else None)
        except DeckError as exc:
            code = exc.kind if exc.kind in DECK_ERR_MESSAGES else "report_failed"
            return RedirectResponse(f"/decks/{deck_id}?err={code}", status_code=303)
        reused = "?reused=1" if report.get("reused") else ""
        return RedirectResponse(f"/history/reports/{_esc(report['report_id'])}{reused}", status_code=303)

    @server.custom_route("/reports", methods=["GET"], include_in_schema=False)
    async def reports_page(request: Request) -> Response:
        """Every stored report of the member, newest first, with what Forge plays for them now.
        The gateway's own database only."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/reports")
        query = ReportsQuery.parse(request.query_params)
        now = time.time()
        index = reports.index(sub)
        apps = state.db.client_names({str(r["created_by_client"] or "") for r in index})
        passing = [r for r in index if reports_matches(r, query, apps, now)]
        if query.order:
            passing.reverse()
        counts = {key: sum(1 for r in passing if reports_in_chip(r, key)) for key, _t in REPORTS_CHIPS}
        shown = [r for r in passing if reports_in_chip(r, query.show)]
        ids = [r["report_id"] for r in shown[query.offset : query.offset + REPORTS_PAGE]]
        rows = reports.summaries(sub, ids)
        latest = None
        if index:
            got = [r for r in rows if r["report_id"] == index[0]["report_id"]]
            latest = got[0] if got else (reports.summaries(sub, [index[0]["report_id"]]) or [None])[0]
        # the newest three decks tested: their newest lethal turn and the one before (two reports each)
        # the newest three decks tested: the lethal turn of each one's newest report and the one before
        two: dict[str, list[dict[str, Any]]] = {}
        for r in index:
            if r.get("source_label"):  # a list's, a proposal's or a comparison's report: no deck trend
                continue
            if r["deck_id"] in two or len(two) < 3:
                two.setdefault(r["deck_id"], [])
                if len(two[r["deck_id"]]) < 2:
                    two[r["deck_id"]].append(r)
        nums = reports.headlines(sub, [r["report_id"] for pair in two.values() for r in pair])
        trends = [
            {
                "deck_name": pair[0]["deck_name"],
                "latest": (nums.get(pair[0]["report_id"]) or {}).get("kill_median_turn"),
                "previous": (nums.get(pair[1]["report_id"]) or {}).get("kill_median_turn")
                if len(pair) > 1
                else None,
            }
            for pair in two.values()
        ]
        body = reports_page_html(
            index,
            rows,
            query=query,
            total=len(shown),
            counts=counts,
            runs=reports.forge_queue(sub),
            decks=reports.decks_seen(sub),
            apps=apps,
            latest=latest,
            trends=trends,
            now=now,
            # the address names what went; the words are the page's own
            notice=DELETED_NOTICES.get(request.query_params.get("deleted") or "", ""),
            csrf=_csrf(s, sid) or "",
        )
        return page(
            "Assistant: Reports",
            body,
            sub=sub,
            sid=sid,
            current="/reports",
            heading=False,
            main_class="ui-wide",
            extra_css=REPORTS_PAGE_CSS,
            extra_scripts=("reports.js",),
        )

    @server.custom_route("/decks/{deck_id}/goldfish", methods=["GET"], include_in_schema=False)
    async def goldfish_page(request: Request) -> Response:
        """Goldfish by hand: the deck shuffled, an opening hand with London mulligans, then draw,
        play, tap and next turn, played in the browser (static/goldfish.js) on the deck read for
        this page. The page sends nothing back and keeps nothing."""
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, f"/decks/{deck_id}/goldfish")
        try:
            deck, how = await decks.page_deck_live(sub, deck_id)
        except DeckError as exc:
            return h.deck_problem(request, exc, sub=sub, sid=sid)
        # The deck's newest Forge solo test, for the line under the intro (the Goldfish thread's
        # ReportService.newest_forge_solo; nothing is shown while that method is not there).
        newest = getattr(reports, "newest_forge_solo", None)
        forge_solo = None
        if callable(newest):
            try:
                forge_solo = newest(sub, str(deck.id))
            except Exception:  # noqa: BLE001 - a report problem never breaks the goldfish
                forge_solo = None
        # Card models: the page's button by the solo line (card_models_view), and the notice
        # after a Remove that came back here
        extra = ""
        if getattr(getattr(reports, "models", None), "available", False):
            from .report_view import MODEL_NOTICES

            note = MODEL_NOTICES.get(request.query_params.get("models") or "", "")
            kind = "error" if request.query_params.get("models") == "error" else "ok"
            extra = (ui.notice(note, kind, role="status") if note else "") + (
                f"<div class='gf-models'>{card_models_view.opener()}</div>"
            )
        return page(
            f"Goldfish: {deck_title(deck)}",
            goldfish_page_html(
                deck,
                guide_hidden=state.db.get_pref(sub, GOLDFISH_GUIDE_PREF) == "hidden",
                forge_solo=forge_solo if isinstance(forge_solo, dict) else None,
                csrf=_csrf(s, sid),
                extra_head=_checking(deck, how) + extra,
            ),
            sub=sub,
            sid=sid,
            two_pane=True,
            current="/decks",
            csp=DECK_CSP,
            viewer_only=True,  # the card viewer only: none of deck.js's hooks are on this page
            heading=False,
            deck_css=True,  # the card viewer's styles
            extra_css=GOLDFISH_CSS + card_models_view.CSS,
            extra_scripts=("goldfish.js",),
        )

    @server.custom_route("/goldfish/guide", methods=["POST"], include_in_schema=False)
    async def goldfish_guide(request: Request) -> Response:
        """Remember whether the member hid the Goldfish page's guide, for their account on every
        device. Browser session and its CSRF header only."""
        sub, sid = browser_session(state, request)
        if not sub or not csrf_header_ok(state.settings, sid, request):
            return JSONResponse({"ok": False, "error": "forbidden", "message": "Sign in again."}, 403)
        try:
            data = json.loads(await read_limited(request, 1024) or b"{}")
        except (ValueError, TypeError):
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("hidden"), bool):
            return JSONResponse(
                {"ok": False, "error": "invalid", "message": "hidden must be true or false."}, 400
            )
        if data["hidden"]:
            saved = state.db.set_pref(sub, GOLDFISH_GUIDE_PREF, "hidden")
        else:
            state.db.delete_pref(sub, GOLDFISH_GUIDE_PREF)
            saved = True
        return JSONResponse({"ok": saved, "hidden": data["hidden"]}, 200 if saved else 409)

    @server.custom_route("/decks/{deck_id}/compare/forge", methods=["POST"], include_in_schema=False)
    async def compare_forge(request: Request) -> Response:
        """Both decks' Forge games against the same opponents. This deck's report runs
        first, with precons picked for the higher of the two decks' brackets (so the
        order doesn't matter); the other deck's seats them (``same_opponents_as``), as the
        assistants' run_deck_report does with compare_with. The other deck is read once, before
        either report is made, and both runs use that read. Both run without the solo test, so
        starting them is quick; the second report opens and links the first."""
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, f"/decks/{deck_id}/compare")
        data = await form(request)
        if not check_csrf(sid, data):
            return page(
                "Compare",
                "<p class='notice error'>This form expired. Reload and try again.</p>",
                sub=sub,
                sid=sid,
                status=403,
            )
        other = str(data.get("with") or "").strip()
        query = f"?with={other}" if is_digits(other) else ""
        back = f"<a class='btn' href='/decks/{_esc(deck_id)}/compare{query}'>Back to the comparison</a>"
        current_client.set(BROWSER_CLIENT_ID)
        hold = None
        try:
            if not is_digits(other):
                raise DeckError("invalid", "Pick the other deck on the Compare page first.")
            # both decks' games start or wait, or neither: the room for both is checked and kept in
            # one step, before either deck is read, so presses at once can't both take it
            hold, full = reports.reserve_pair(sub) if reports.forge is not None else (None, None)
            if full is not None:
                return page(
                    "Compare",
                    f"<div class='panel'><p class='notice error'>{_esc(full)}</p>{back}</div>",
                    sub=sub,
                    sid=sid,
                    status=409,
                )
            other_deck = await reports.pair_deck(sub, other)
            # without Forge on the gateway no pair is asked for: the first report says why it has no
            # Forge games and the page below names that deck (compare_with is refused then)
            pair = other_deck if reports.forge is not None else None
            first = await reports.run(
                sub, deck_id, simulate=False, forge=True, pair_with=pair, pair_hold=hold
            )
            try:
                second = await reports.run(
                    sub,
                    other,
                    simulate=False,
                    forge=True,
                    deck=other_deck,
                    same_opponents_as=first["report_id"],
                    pair_hold=hold,
                )
            except DeckError as exc:
                if str(exc) != NO_FORGE_TO_SHARE:
                    raise
                # the member picked no report here: say which deck got no Forge games, and why it may be
                name = first.get("deck_name") or "the first deck"
                return page(
                    "Compare",
                    "<div class='panel'><p class='notice error'>"
                    f"Forge could not start games for {_esc(name)}, so there are no opponents to share. "
                    f"<a href='/history/reports/{_esc(first['report_id'])}'>Its report</a> is on the "
                    "<a href='/reports'>Reports</a> page. If your Forge queue is full, wait for a run to "
                    "finish, then try again."
                    f"</p>{back}</div>",
                    sub=sub,
                    sid=sid,
                    status=409,
                )
        except DeckError as exc:
            if (plan := busy_plan(exc)) is not None:
                # the shared busy page, but Try again opens the Compare page: this route takes POST only
                compare_url = f"/decks/{deck_id}/compare{query}"
                busy = page("Compare", busy_body(plan, compare_url), sub=sub, sid=sid, status=plan.status)
                busy.headers.update(_headers(plan))
                return busy
            # the deck lost its commander after the page loaded: plain words, not the assistant's
            text = COMPARE_NO_COMMANDER if str(exc) == PAIR_NO_FORGE else str(exc)
            return page(
                "Compare",
                f"<div class='panel'><p class='notice error'>{_esc(text)}</p>{back}</div>",
                sub=sub,
                sid=sid,
                status=404 if exc.kind == "not_found" else 400,
            )
        finally:
            reports.release_pair(sub, hold)
        # the pair side by side (owner decision C7); each report links to it
        return RedirectResponse(f"/history/reports/{_esc(second['report_id'])}/head-to-head", status_code=303)

    @server.custom_route("/decks/{deck_id}/compare", methods=["GET"], include_in_schema=False)
    async def compare_page(request: Request) -> Response:
        """This deck against another: a preconstructed deck (the picker lists Archidekt's), any
        deck id or link, one of the member's decks or this deck's snapshots (listed under the box,
        ``with=snap_<id>`` for a snapshot), or a pasted list. The same comparison the assistant's
        compare_decks makes: what was taken out of the other deck, what was put in, what changed
        count, and the statistics' differences; then the paired solo test of both (kept as a
        report) and, for two Commander decks, Forge games against the same opponents."""
        deck_id = request.path_params["deck_id"]
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, f"/decks/{deck_id}/compare")
        try:
            deck, how = await decks.page_deck_live(sub, deck_id)
        except DeckError as exc:
            return h.deck_problem(request, exc, sub=sub, sid=sid)
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
        elif other_ref.startswith("snap_"):
            # one of the member's snapshots (the picker under the box): the deck as it stood then
            snap = state.db.get_snapshot(other_ref[5:], sub)
            if snap:
                other = parse_deck(snap["deck"])
                other_name = f"{other.name or 'this deck'} (snapshot, {time_text(snap['taken_at'])})"
            else:
                error, status = "That snapshot isn't in your history. It may have been deleted.", 404
        elif other_ref:
            try:
                # a precon picked by name (the suggestion list) is looked up in the precon listing
                precon_id = precon_by_label(precons, other_ref)
                other = await decks.get_any_deck(sub, str(precon_id) if precon_id else other_ref)
                other_name = other.name or f"deck {other.id}"
            except DeckError as exc:
                if is_busy(exc):  # Archidekt can't be asked now: the shared busy page, as for this deck
                    return h.deck_problem(request, exc, sub=sub, sid=sid)
                error, status = (
                    f"That deck could not be read: {_esc(exc)}",
                    400 if exc.kind != "not_found" else 404,
                )
        result = deck_stats.compare(other, deck) if other is not None else None
        try:  # the member's decks for the picker, as cached: the page never waits on Archidekt for it
            mine = await asyncio.wait_for(decks.list_decks_quick(sub, wait=0), DECKS_JSON_TIMEOUT)
        except (DeckError, TimeoutError):
            mine = None
        if mine is None:  # cold or Archidekt unavailable: the list as last read, if any
            try:
                mine = decks.last_deck_list(sub)
            except DeckError:
                mine = None
        body = _checking(deck, how) + compare_page_html(
            deck,
            other=other,
            other_name=other_name,
            other_ref=other_ref,
            paste=paste,
            result=result,
            precons=precons,
            error=error,
            csrf=_csrf(s, sid),
            forge_on=reports.forge is not None,
            mine=mine,
            snapshots=decks.list_snapshots(sub, deck_id=str(deck.id), limit=8),
            ab_on=reports.mf is not None,
        )
        return page(
            f"Compare: {deck_title(deck)}",
            body,
            sub=sub,
            sid=sid,
            status=status,
            two_pane=True,
            current="/decks",
            csp=DECK_CSP,
            viewer_only=True,  # the card viewer only: none of deck.js's hooks are on this page
            heading=False,
            deck_css=True,
            extra_css=COMPARE_CSS,
            extra_scripts=("compare.js",),
        )
