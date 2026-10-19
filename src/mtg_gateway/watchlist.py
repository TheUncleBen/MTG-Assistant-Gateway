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

import json
import logging
from typing import TYPE_CHECKING, Annotated, Any, Literal
from urllib.parse import parse_qs

from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import prices
from .decks import DeckError
from .pages import _csrf, browser_session, csrf_form_ok, csrf_header_ok, login_redirect, read_limited
from .theme import display_name, render
from .watchlist_page import WATCH_PAGE_CSS, Query, watchlist_page_html

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

logger = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
MAX_FORM = 8192


def watchlist_body(
    state: AppState, sub: str, csrf: str | None, query: Query | None = None, *, ok: str = "", err: str = ""
) -> str:
    """The page's body (watchlist_page.py): the member's watches with a year of prices, so the
    opened row can say how many days its full history holds."""
    svc = state.prices
    data = svc.overview(sub, days=365)
    ids = {w["id"]: w.get("scryfall_id") for w in svc.watched_cards(sub)}
    return watchlist_page_html(data, ids, csrf, query or Query(), ok=ok, err=err, now=prices.now())


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
            user=display_name(user),
        )

    @server.custom_route("/watchlist", methods=["GET"], include_in_schema=False)
    async def watchlist_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/watchlist")
        ok = request.query_params.get("ok", "")
        query = Query.parse(request.query_params)
        return page(watchlist_body(state, sub, _csrf(s, sid), query, ok=ok), sub=sub, sid=sid)

    @server.custom_route("/watchlist", methods=["POST"], include_in_schema=False)
    async def watchlist_post(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/watchlist")
        raw = await read_limited(request, MAX_FORM)
        data = {k: v[0] for k, v in parse_qs((raw or b"").decode("utf-8", "replace")).items()}
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
        try:
            if action == "add_card":
                out = await svc.watch_card(
                    sub,
                    data.get("name", ""),
                    set_code=data.get("set_code"),
                    collector_number=data.get("collector_number"),
                    finish=data.get("finish", "nonfoil"),
                )
                done = "card_added" if out.get("created") else "card_updated"
            elif action == "alerts":
                svc.set_alerts(
                    sub,
                    data.get("watch_id", ""),
                    data.get("alert_pct"),
                    data.get("alert_above"),
                    data.get("alert_below"),
                )
                done = "card_updated"
            elif action == "remove_card":
                svc.unwatch_card(sub, data.get("watch_id", ""))
                done = "card_removed"
            elif action == "add_deck":
                await svc.watch_deck(sub, data.get("deck", ""))
                done = "deck_added"
            elif action == "remove_deck":
                svc.unwatch_deck(sub, data.get("deck_id", ""))
                done = "deck_removed"
            else:
                raise DeckError("invalid", "Unknown action.")
        except DeckError as exc:
            status = 404 if exc.kind == "not_found" else 429 if exc.kind == "rate_limited" else 400
            return page(watchlist_body(state, sub, csrf, err=str(exc)), sub=sub, sid=sid, status=status)
        return RedirectResponse(f"/watchlist?ok={done}", status_code=303)

    @server.custom_route("/watchlist/api/card", methods=["GET"], include_in_schema=False)
    async def card_api(request: Request) -> Response:
        """The card viewer's Price history: the member's watches of this card (by name) with their
        daily prices. Nothing is fetched from Scryfall here."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=NO_STORE)
        name = (request.query_params.get("name") or "").strip()[:150]
        watches = [state.prices.card_view(w, 365) for w in state.prices.find(sub, name)] if name else []
        return JSONResponse({"ok": True, "name": name, "watches": watches}, headers=NO_STORE)

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
        try:
            # a printing from Card search's art picker comes with its set code and number
            out = await state.prices.watch_card(
                sub,
                str(body.get("name") or ""),
                set_code=str(body.get("set_code") or "") or None,
                collector_number=str(body.get("collector_number") or "") or None,
                finish=str(body.get("finish") or "nonfoil"),
            )
        except DeckError as exc:
            status = 404 if exc.kind == "not_found" else 429 if exc.kind == "rate_limited" else 400
            return JSONResponse(
                {"ok": False, "error": exc.kind, "message": str(exc)}, status, headers=NO_STORE
            )
        return JSONResponse({"ok": True, "watch": out}, headers=NO_STORE)

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
                {"ok": False, "error": "invalid", "message": "Send the watch's id."}, 400, headers=NO_STORE
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
            "Add a card to the member's watchlist (or change its alerts when it is already there). By name "
            "alone it follows the printing Scryfall gives for that name; set_code and collector_number "
            "follow one printing; finish is nonfoil, foil or etched. Optional alerts: alert_pct (a move of "
            "more than this percent from one day to the next), alert_above and alert_below (a price "
            "crossed, in dollars, or euros when the card has no dollar price). The gateway checks prices "
            "once a day; alerts reach the member through their notifications. Changes only the member's "
            "gateway data, never Archidekt. Returns today's price when Scryfall answered."
        ),
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
    )
    async def watch_card(
        name: Annotated[str, Field(description="The card's name.")] = "",
        set_code: Annotated[str | None, Field(description="Set code, such as cmm.")] = None,
        collector_number: Annotated[str | None, Field(description="Collector number in that set.")] = None,
        finish: Annotated[Literal["nonfoil", "foil", "etched"], Field(description="The finish.")] = "nonfoil",
        alert_pct: Annotated[float | None, Field(description="Alert on a daily move above this %.")] = None,
        alert_above: Annotated[
            float | None, Field(description="Alert when the price rises above this.")
        ] = None,
        alert_below: Annotated[
            float | None, Field(description="Alert when the price falls below this.")
        ] = None,
    ) -> dict[str, object]:
        try:
            out = await state.prices.watch_card(
                _sub(),
                name,
                set_code=set_code,
                collector_number=collector_number,
                finish=finish,
                alert_pct=alert_pct,
                alert_above=alert_above,
                alert_below=alert_below,
            )
        except DeckError as exc:
            return fail(exc)
        return {"ok": True, "watch": out, "watchlist_url": page}

    @server.tool(
        name="watch_deck",
        title="Watch a deck's value",
        description=(
            "Add a deck (Archidekt id or link, the member's own or a public one) to the member's watchlist: "
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
            "Remove a card (card: its watch_id from list_watchlist, or its name, which removes every "
            "printing of it) or a deck (deck_id) from the member's watchlist. Its price history stops "
            "growing; the gateway's stored prices age out on their own."
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
        data = state.prices.overview(_sub(), days=31)
        for item in (*data["cards"], *data["decks"]):
            item.pop("history", None)  # card_price_history has the daily points
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
        rows = state.prices.find(sub, card or "")
        if not rows:
            return fail(
                DeckError("not_found", "That card is not on your watchlist; watch it with watch_card.")
            )
        return {"ok": True, "cards": [state.prices.card_view(w, days) for w in rows]}


__all__ = ["add_watchlist_routes", "add_watchlist_tools"]
