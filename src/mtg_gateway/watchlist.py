"""The Watchlist page, the card viewer's price history, and the assistant's tools for the same
(prices.py). They exist only when the operator turned watchlists on (MTG_WATCHLISTS_ENABLED);
off, nothing here is registered, so the pages answer 404 and the tools are not listed.

Both paths reach the same service: ``/watchlist`` (watch or remove cards and decks, set alerts,
see each price over time as a small chart and as a table) and the MCP tools ``watch_card``,
``watch_deck``, ``unwatch``, ``list_watchlist`` and ``card_price_history``. Watching changes
only the member's own gateway data, never Archidekt, so it needs no proposal. The Account page's
Notifications card (with the summary) is notify.py's, and ``get_summary`` is summary.py's.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import replace
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import parse_qs, quote

from pydantic import Field
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import prefs, prices
from .decks import DeckError
from .pages import _csrf, browser_session, csrf_form_ok, csrf_header_ok, login_redirect_here, read_limited
from .theme import render
from .watchlist_page import WATCH_PAGE_CSS, Query, alert_text, watchlist_fragment_html, watchlist_page_html

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

logger = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
MAX_FORM = 8192
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def said_of(names: list[str]) -> str:
    """The notice's query after Stop watching and its Undo: the names when there are up to three
    (``w``), the count otherwise (``n``)."""
    if 0 < len(names) <= 3:
        return "w=" + quote(", ".join(names)[:300])
    return f"n={len(names)}"


def watchlist_body(
    state: AppState,
    sub: str,
    csrf: str | None,
    query: Query | None = None,
    *,
    ok: str = "",
    err: str = "",
    part: str = "",
    start: int = 0,
    said: dict[str, str] | None = None,
) -> str:
    """The page's body (watchlist_page.py): every watch's latest price and changes (a fixed number
    of queries, whatever the list's size), and 30 days of prices for the rows on screen only.
    ``part`` ("cards" or "decks") with ``start`` gives Load more's fragment instead: the rows from
    ``start`` and the Load more after them."""
    svc = state.prices
    cards = svc.card_summaries(sub)
    data = {
        "cards": cards,
        "decks": svc.deck_summaries(sub),
        "lists": svc.lists(sub),
        "limits": svc.limits(),
        "checked_daily_at_utc": f"{state.settings.price_hour_utc:02d}:00",
    }
    ids = {v["watch_id"]: v.get("scryfall_id") for v in cards}
    if part:
        return watchlist_fragment_html(
            data,
            ids,
            csrf,
            query or Query(),
            part=part,
            start=start,
            now=prices.now(),
            card_history=lambda views: svc.card_histories(views, 30),
            deck_history=lambda views: svc.deck_histories(sub, views, 30),
        )
    return watchlist_page_html(
        data,
        ids,
        csrf,
        query or Query(),
        ok=ok,
        said=said,
        err=err,
        now=prices.now(),
        card_history=lambda views: svc.card_histories(views, 30),
        deck_history=lambda views: svc.deck_histories(sub, views, 30),
        defaults=prefs.choices(state.db, sub, prefs.WATCH_ADD),
        alert_defaults=prefs.alert_defaults(state.db, sub),
    )


# -- routes --------------------------------------------------------------------------------------


def add_watchlist_routes(server: MCPServer, state: AppState) -> None:
    if state.prices is None:  # watchlists are off on this gateway
        return
    s = state.settings

    def page(body: str, *, sub: str, sid: str | None, status: int = 200) -> Response:
        user = state.db.get_user(sub) or {}
        return render(
            "Watchlist",
            body,
            site=s.server_name,
            status=status,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=bool(s.admin_group and s.admin_group in (user.get("groups") or [])),
            current="/watchlist",
            heading=False,
            main_class="ui-wide",
            head_extra=f"<style>{WATCH_PAGE_CSS}</style><script src='/static/cardview.js' defer></script>"
            "<script src='/static/watchlist.js' defer></script>",
            sub=sub,
        )

    @server.custom_route("/watchlist", methods=["GET"], include_in_schema=False)
    async def watchlist_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/watchlist")
        qp = request.query_params
        ok = qp.get("ok", "")
        query = Query.parse(qp)
        said = {k: (qp.get(k) or "")[:300] for k in ("n", "w", "undo")}
        part = qp.get("part") or ""
        if part in ("cards", "decks"):
            # Load more (static/watchlist.js): only the rows from ``from``, the rows on screen
            try:
                start = max(0, min(int(qp.get("from") or 0), 10_000))
            except ValueError:
                start = 0
            body = watchlist_body(state, sub, _csrf(s, sid), query, part=part, start=start)
            return HTMLResponse(body, headers=NO_STORE)
        return page(watchlist_body(state, sub, _csrf(s, sid), query, ok=ok, said=said), sub=sub, sid=sid)

    def remember_watch(sub: str, finish: str, pct: str) -> None:
        """Keep the Watch form's finish and alert for next time (prefs.WATCH_ADD)."""
        choice: dict[str, Any] = {"finish": finish if finish in ("nonfoil", "foil", "etched") else "nonfoil"}
        try:
            if pct and 0 < float(pct) <= 1000:
                choice["alert_pct"] = round(float(pct), 2)
        except ValueError:
            pass
        prefs.save(state.db, sub, prefs.WATCH_ADD, choice)

    def alert_values(data: dict[str, str]) -> dict[str, str | None]:
        """The five alert boxes of a form as prices.set_alerts takes them (drop, rise, pct,
        above, below; an empty box is None)."""
        return {
            k: ((data.get(f"alert_{k}") or "").strip() or None)
            for k in ("pct", "above", "below", "drop", "rise")
        }

    def save_defaults(sub: str, data: dict[str, str]) -> None:
        """The member's price alert defaults (prefs.WATCH_ALERTS), checked as a card's alerts are."""
        values = prices._alerts(*(alert_values(data)[k] for k in prices.ALERT_KINDS))
        kept = {k: v for k, v in zip(prices.ALERT_KINDS, values, strict=True) if v is not None}
        floor = (data.get("alert_floor") or "").strip()
        if floor:
            try:
                f = round(float(floor), 2)
            except ValueError as exc:
                raise DeckError("invalid", "The smallest move must be an amount, such as 0.50.") from exc
            if not 0 <= f <= 1000:
                raise DeckError("invalid", "The smallest move must be from 0 to 1,000.")
            if f > 0:
                kept["floor"] = f
        elif "alert_floor" not in data:  # an older form without the box keeps the floor it had
            had = prefs.alert_defaults(state.db, sub).get("floor")
            if had:
                kept["floor"] = had
        if not prefs.save(state.db, sub, prefs.WATCH_ALERTS, kept):
            raise DeckError("invalid", "Those defaults could not be saved.")
        state.prices.apply_defaults(sub)

    def list_of(data: dict[str, str]) -> str | None:
        """The list a form names: a new list's name over the drop-down's choice; None when the
        form has neither (a watched card stays where it is)."""
        if "new_list" not in data and "list" not in data:
            return None
        return prices.clean_list_name(data.get("new_list") or data.get("list") or "")

    def back_to(data: dict[str, str], done: str, renamed: tuple[str, str] | None = None) -> RedirectResponse:
        """Back to the list the member was looking at (``back``: a Watchlist address, or the Lists
        hub's) with the notice; ``renamed`` (old, new) follows a list just renamed."""
        back = (data.get("back") or "").strip()[:300]
        if back.startswith("/collection/lists"):
            return RedirectResponse(f"/collection/lists?ok={done}", status_code=303)
        q = Query()
        if back.startswith("/watchlist?"):
            q = Query.parse({k: v[0] for k, v in parse_qs(back.split("?", 1)[1]).items()})
        if renamed and q.list == renamed[0]:
            q = replace(q, list=renamed[1])
        href = q.href(upto=0)
        return RedirectResponse(href + ("&" if "?" in href else "?") + f"ok={done}", status_code=303)

    @server.custom_route("/watchlist", methods=["POST"], include_in_schema=False)
    async def watchlist_post(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/watchlist")
        raw = await read_limited(request, MAX_FORM * 8)  # the bulk form carries up to 5,000 ids
        fields = parse_qs((raw or b"").decode("utf-8", "replace"))
        data = {k: v[0] for k, v in fields.items()}
        csrf = _csrf(s, sid) or ""
        if raw is None or not csrf_form_ok(s, sid, data):
            return page(
                watchlist_body(state, sub, csrf, err="This form expired. Reload the page and try again."),
                sub=sub,
                sid=sid,
                status=403,
            )
        action = data.get("action", "")
        svc = state.prices
        renamed: tuple[str, str] | None = None
        try:
            if action == "add_card":
                pct = (data.get("alert_pct") or "").strip()
                out = await svc.watch_card(
                    sub,
                    data.get("name", ""),
                    set_code=data.get("set_code"),
                    collector_number=data.get("cn") or data.get("collector_number"),
                    finish=data.get("finish", "nonfoil"),
                    alert_pct=pct or None,
                    defaults=prefs.alert_defaults(state.db, sub),
                    list_name=list_of(data),
                )
                if data.get("remember") == "1":
                    remember_watch(sub, data.get("finish", "nonfoil"), pct)
                done = "card_added" if out.get("created") else "card_updated"
            elif action == "alerts":
                v = alert_values(data)
                svc.set_alerts(
                    sub, data.get("watch_id", ""), v["pct"], v["above"], v["below"], v["drop"], v["rise"]
                )
                if (name := list_of(data)) is not None:
                    svc.move_to_list(sub, [data.get("watch_id", "")], name)
                done = "card_updated"
            elif action == "remove_card":
                # one row's Remove: the same keep and Undo as the bulk bar's Stop watching
                wid = data.get("watch_id", "")
                if not wid.startswith("w"):
                    wid = next((w["id"] for w in svc.find(sub, wid)), wid)
                token, rows = svc.stop_watching(sub, [wid])
                if not rows:
                    raise DeckError("not_found", "That card is not on your watchlist.")
                done = "removed&" + said_of([w["name"] for w in rows]) + f"&undo={token}"
            elif action == "add_deck":
                await svc.watch_deck(sub, data.get("deck", ""))
                done = "deck_added"
            elif action == "remove_deck":
                svc.unwatch_deck(sub, data.get("deck_id", ""))
                done = "deck_removed"
            elif action == "defaults":
                save_defaults(sub, data)
                done = "defaults_saved"
            elif action == "list_new":
                svc.create_list(sub, data.get("name", ""))
                done = "list_created"
            elif action == "list_rename":
                old = prices.clean_list_name(data.get("name", ""))
                renamed = (old, svc.rename_list(sub, old, data.get("new_name", "")))
                done = "list_renamed"
            elif action == "list_delete":
                svc.delete_list(sub, prices.clean_list_name(data.get("name", "")))
                done = "list_deleted"
            elif action == "rewatch":  # the notice's Undo of Stop watching
                done = "rewatched&" + said_of(svc.rewatch(sub, data.get("token", "")))
            elif action == "bulk":
                # the bulk bar: the ticked rows (watch_id, many) and what to do with them
                ids = fields.get("watch_id") or []
                do = data.get("do", "")
                if do == "alerts":
                    svc.set_alerts_many(sub, ids, **alert_values(data))
                    done = "alerts_set"
                elif do == "move":
                    svc.move_to_list(sub, ids, list_of(data) or "")
                    done = "moved"
                elif do == "remove":
                    token, rows = svc.stop_watching(sub, ids)
                    done = "removed&" + said_of([w["name"] for w in rows])
                    if rows:
                        done += f"&undo={token}"
                else:
                    raise DeckError("invalid", "Choose what to do with the selected cards.")
            else:
                raise DeckError("invalid", "Unknown action.")
        except DeckError as exc:
            status = 404 if exc.kind == "not_found" else 429 if exc.kind == "rate_limited" else 400
            return page(watchlist_body(state, sub, csrf, err=str(exc)), sub=sub, sid=sid, status=status)
        return back_to(data, done, renamed)

    @server.custom_route("/watchlist/api/names", methods=["GET"], include_in_schema=False)
    async def names_api(request: Request) -> Response:
        """The names of the cards the member watches (any printing): the pages' card menus say
        "On your watchlist" for them instead of offering the watch again."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
        names = state.prices.watched_names(sub)
        # asked once a minute by every page: an unchanged list is a 304 (private, revalidated
        # each time, never served stale)
        tag = '"' + hashlib.sha256("\n".join(names).encode()).hexdigest()[:32] + '"'
        headers = {"Cache-Control": "private, no-cache", "X-Content-Type-Options": "nosniff", "ETag": tag}
        if request.headers.get("if-none-match") == tag:
            return Response(status_code=304, headers=headers)
        return JSONResponse({"ok": True, "names": names}, headers=headers)

    @server.custom_route("/watchlist/api/card", methods=["GET"], include_in_schema=False)
    async def card_api(request: Request) -> Response:
        """The card viewer's Price history: the member's watches of this card (by name) with their
        daily prices, and (``sid``, the printing on show; ``finish``) the history of that printing
        when the member owns it (``owned``: in their Binder or a deck of theirs, else None).
        Nothing is fetched from Scryfall here."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
        qp = request.query_params
        name = (qp.get("name") or "").strip()[:150]
        watches = [state.prices.card_view(w, 365) for w in state.prices.find(sub, name)] if name else []
        sid = (qp.get("sid") or "").strip().lower()[:36]
        owned = (
            state.prices.owned_history(sub, sid, (qp.get("finish") or "nonfoil")[:10])
            if _UUID.fullmatch(sid)
            else None
        )
        return JSONResponse({"ok": True, "name": name, "watches": watches, "owned": owned}, headers=NO_STORE)

    @server.custom_route("/watchlist/api/deck", methods=["GET"], include_in_schema=False)
    async def deck_api(request: Request) -> Response:
        """A watched deck's full history (a year at most), for its row's "Full history"."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
        deck_id = (request.query_params.get("deck_id") or "").strip()[:20]
        rows = state.prices.deck_series(sub, deck_id, 365) if deck_id.isdigit() else []
        history = [{"day": r["day"], "price": r["usd"]} for r in rows if r["usd"] is not None]
        return JSONResponse({"ok": True, "deck_id": deck_id, "history": history}, headers=NO_STORE)

    @server.custom_route("/watchlist/api/watch", methods=["POST"], include_in_schema=False)
    async def watch_api(request: Request) -> Response:
        """The card viewer's Watch button: watch this card by name (X-CSRF-Token as the JSON API)."""
        sub, sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
        if not csrf_header_ok(s, sid, request):
            return JSONResponse(
                {"ok": False, "error": "csrf", "message": "Reload the page and retry."}, 403, headers=NO_STORE
            )
        raw = await read_limited(request, MAX_FORM)
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return JSONResponse(
                {"ok": False, "error": "invalid", "message": "Send JSON."}, 400, headers=NO_STORE
            )
        kept = prefs.choices(state.db, sub, prefs.WATCH_ADD)
        try:
            # a printing from Card search's art picker comes with its set code and number
            out = await state.prices.watch_card(
                sub,
                str(body.get("name") or ""),
                set_code=str(body.get("set_code") or "") or None,
                collector_number=str(body.get("collector_number") or "") or None,
                # a Watch that names no finish (a card menu's) takes the remembered one
                finish=str(body.get("finish") or kept.get("finish") or "nonfoil"),
                # a Watch button on a card already watched must not clear its alerts; a new
                # watch gets the member's default price alert
                keep_alerts=True,
                default_pct=kept.get("alert_pct"),
                defaults=prefs.alert_defaults(state.db, sub),
            )
        except DeckError as exc:
            status = 404 if exc.kind == "not_found" else 429 if exc.kind == "rate_limited" else 400
            return JSONResponse(
                {"ok": False, "error": exc.kind, "message": str(exc)}, status, headers=NO_STORE
            )
        return JSONResponse({"ok": True, "watch": out, "message": watched_text(out)}, headers=NO_STORE)

    @server.custom_route("/watchlist/api/unwatch", methods=["POST"], include_in_schema=False)
    async def unwatch_api(request: Request) -> Response:
        """Undo of Card search's "added to your watchlist" toast: stop watching one watch by its id
        (X-CSRF-Token as the JSON API). Only the member's own watch goes."""
        sub, sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
        if not csrf_header_ok(s, sid, request):
            return JSONResponse(
                {"ok": False, "error": "csrf", "message": "Reload the page and retry."}, 403, headers=NO_STORE
            )
        raw = await read_limited(request, MAX_FORM)
        try:
            body = json.loads(raw or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return JSONResponse(
                {"ok": False, "error": "invalid", "message": "Send JSON."}, 400, headers=NO_STORE
            )
        wid = str(body.get("watch_id") or "").strip()[:64]
        # by id only: a name would stop every printing of the card
        if not wid.startswith("w_"):
            return JSONResponse(
                {"ok": False, "error": "invalid", "message": "Send the watch's ID."}, 400, headers=NO_STORE
            )
        try:
            names = state.prices.unwatch_card(sub, wid)
        except DeckError as exc:
            status = 404 if exc.kind == "not_found" else 400
            return JSONResponse(
                {"ok": False, "error": exc.kind, "message": str(exc)}, status, headers=NO_STORE
            )
        return JSONResponse({"ok": True, "removed": names}, headers=NO_STORE)


# -- assistant tools -----------------------------------------------------------------------------


def watched_text(w: dict[str, Any]) -> str:
    """The toast for a Watch press (the card menus, the card viewer): a new watch says the price
    alert it came with, the member's defaults (independent review 2, L5), so the push that may
    follow is no surprise."""
    name = str(w.get("name") or "The card")
    if not w.get("created"):
        return f"{name} is on your watchlist."
    alerts = w.get("alerts") or {}
    if not alerts:
        return f"{name} added to your watchlist."
    said = alert_text(alerts, w.get("currency") or "usd", w.get("added_price"), w.get("floor"))
    return f"{name} added to your watchlist, with a price alert: {said[:1].lower()}{said[1:]}."


def add_watchlist_tools(server: MCPServer, state: AppState) -> None:
    if state.prices is None:  # watchlists are off: the five tools are not listed
        return
    from mcp.server.auth.middleware.auth_context import get_access_token

    def _sub() -> str:
        token = get_access_token()
        if token is None or not token.subject:
            raise RuntimeError("no authenticated user on this request")
        return token.subject

    def fail(exc: DeckError) -> dict[str, Any]:
        return {"ok": False, "error": exc.kind, "message": str(exc)}

    page = f"{state.settings.public_url}/watchlist"

    @server.tool(
        name="watch_card",
        title="Watch a card's price",
        description=(
            "Add a card to the member's watchlist, or change a watched one's list or named alerts "
            "(others kept). By name: the cheapest printing in the finish (printing_now); set_code and "
            "collector_number: one printing. Alerts: alert_pct, alert_above, alert_below (dollars, or "
            "euros with no dollar price) and alert_drop; a new watch with none gets the member's "
            "defaults. Checked daily; alerts are push notifications. Changes only gateway data. "
            "Returns today's price when Scryfall answered (printing_now: its printing)."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def watch_card(
        name: str = "",
        set_code: Annotated[str | None, Field(description="Set code, such as cmm.")] = None,
        collector_number: Annotated[str | None, Field(description="Collector number in that set.")] = None,
        finish: Literal["nonfoil", "foil", "etched"] = "nonfoil",
        alert_pct: Annotated[float | None, Field(description="Alert on a daily move above this %.")] = None,
        alert_above: Annotated[
            float | None, Field(description="Alert when the price rises above this.")
        ] = None,
        alert_below: Annotated[
            float | None, Field(description="Alert when the price falls below this.")
        ] = None,
        alert_drop: Annotated[
            float | None,
            Field(description="Alert on a drop of this % below the price when added."),
        ] = None,
        list_name: Annotated[
            str | None,
            Field(description="The watchlist list (group) for it, made when new. Empty: none."),
        ] = None,
        clear_alerts: Annotated[
            bool,
            Field(description="Turn off alerts not named; a new watch gets none."),
        ] = False,
    ) -> dict[str, object]:
        sub = _sub()
        try:
            out = await state.prices.watch_card(
                sub,
                name,
                set_code=set_code,
                collector_number=collector_number,
                finish=finish,
                alert_pct=alert_pct,
                alert_above=alert_above,
                alert_below=alert_below,
                alert_drop=alert_drop,
                clear_alerts=clear_alerts,
                defaults=prefs.alert_defaults(state.db, sub),
                list_name=list_name,
            )
        except DeckError as exc:
            return fail(exc)
        return {"ok": True, "watch": out, "watchlist_url": page}

    @server.tool(
        name="watch_deck",
        title="Watch a deck's value",
        description=(
            "Add a deck (Archidekt ID or link, the member's own or a public one) to the member's watchlist: "
            "the gateway prices its cards once a day and keeps the deck's value over time. Changes only the "
            "member's gateway data, never Archidekt."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def watch_deck(deck_id: str) -> dict[str, object]:
        try:
            out = await state.prices.watch_deck(_sub(), deck_id)
        except DeckError as exc:
            return fail(exc)
        return {"ok": True, "deck": out, "watchlist_url": page}

    @server.tool(
        name="unwatch",
        title="Remove from the watchlist",
        description=(
            "Remove a card (card: its watch_id from list_watchlist, or its name: every printing) or a "
            "deck (deck_id) from the member's watchlist. Its price history stops growing; stored "
            "prices age out on their own."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    )
    async def unwatch(card: str | None = None, deck_id: str | None = None) -> dict[str, object]:
        if not card and not deck_id:
            return fail(DeckError("invalid", "Give card (a watch_id or name) or deck_id."))
        out: dict[str, object] = {"ok": True}
        try:
            if card:
                out["cards_removed"] = state.prices.unwatch_card(_sub(), card)
            if deck_id:
                out["deck_removed"] = state.prices.unwatch_deck(_sub(), deck_id)
        except DeckError as exc:
            return fail(exc)
        return out

    @server.tool(
        name="list_watchlist",
        title="List my watchlist",
        description=(
            "The member's watched cards (latest price, its day, the change over 1, 7 and 30 days, alerts) "
            "and watched decks (latest value and its changes), with the limits and the daily check time. "
            "Prices come from Scryfall once a day. Read-only."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def list_watchlist() -> dict[str, object]:
        # no daily points: card_price_history has them
        data = state.prices.overview(_sub(), history=False)
        return {"ok": True, **data, "watchlist_url": page}

    @server.tool(
        name="card_price_history",
        title="Price history",
        description=(
            "Daily prices the gateway stored for a watched card (card: watch_id or name) or a watched "
            "deck's value (deck_id), oldest first, the last `days` days (default 90, at most 365). Only "
            "watched cards and decks have a history. Read-only."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def card_price_history(
        card: str | None = None, deck_id: str | None = None, days: int = 90
    ) -> dict[str, object]:
        sub = _sub()
        days = max(1, min(int(days), 365))
        if deck_id:
            row = [d for d in state.prices.watched_decks(sub) if d["deck_id"] == str(deck_id).strip()]
            if not row:
                return fail(
                    DeckError("not_found", "That deck is not on your watchlist; watch it with watch_deck.")
                )
            return {"ok": True, "deck": state.prices.deck_view(sub, row[0], days)}
        if not (card or "").strip():
            return fail(DeckError("invalid", "Give the card (its watch_id or name) or a deck_id."))
        rows = state.prices.find(sub, card or "")
        if not rows:
            return fail(
                DeckError("not_found", "That card is not on your watchlist; watch it with watch_card.")
            )
        return {"ok": True, "cards": [state.prices.card_view(w, days) for w in rows]}


__all__ = ["add_watchlist_routes", "add_watchlist_tools"]
