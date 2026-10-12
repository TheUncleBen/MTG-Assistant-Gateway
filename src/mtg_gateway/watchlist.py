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

from .decks import DeckError
from .history_view import _trend_svg
from .pages import _csrf, browser_session, csrf_form_ok, csrf_header_ok, login_redirect, read_limited
from .prices import money
from .theme import display_name, render
from .theme import esc as _esc

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

logger = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
MAX_FORM = 8192
WATCH_CSS = """
.watch form.row{display:grid;gap:.6rem 1rem;align-items:end;
  grid-template-columns:repeat(auto-fit,minmax(min(100%,9rem),1fr))}
.watch form.row .field{margin:0;min-width:0} .watch form.row .wide{grid-column:span 2}
@media (max-width:480px){ .watch form.row .wide{grid-column:auto} }
.watch form.row button{margin:0}
.wlist{display:grid;gap:.75rem;margin:1rem 0 0}
.wrow{border:1px solid var(--border);border-radius:3px;padding:.7rem .85rem;min-width:0}
.wrow h3{margin:0;font-size:1rem;overflow-wrap:anywhere}
.wrow .sub{color:var(--text-muted);font-size:.88rem;font-weight:400}
.wrow .now{margin:.35rem 0 0;font-variant-numeric:tabular-nums}
.wrow .now b{font-size:1.15rem}
.wrow .chg{color:var(--text-muted);font-size:.9rem;margin:.2rem 0 0}
.tspark{display:block;width:100%;height:36px;color:var(--orange-text);margin:.4rem 0 0}
.tspark polyline{fill:none;stroke:currentColor;stroke-width:2;stroke-linejoin:round;stroke-linecap:round}
.wrow details{margin-top:.4rem} .wrow details > summary{cursor:pointer;color:var(--link);min-height:2rem;
  display:flex;align-items:center}
.wrow .tbl{overflow-x:auto;max-width:100%;max-height:16rem;overflow-y:auto}
.wrow table{border-collapse:collapse;font-size:.86rem;font-variant-numeric:tabular-nums}
.wrow th,.wrow td{padding:.25rem .6rem;border-bottom:1px solid var(--border-soft);text-align:right}
.wrow th[scope=row],.wrow thead th:first-child{text-align:left}
.wrow .acts{display:flex;flex-wrap:wrap;gap:.5rem;margin-top:.5rem;align-items:center}
.wrow .acts form{margin:0}
"""

OK_NOTES = {
    "card_added": "Added to your watchlist.",
    "card_updated": "Alerts saved.",
    "card_removed": "Removed from your watchlist.",
    "deck_added": "Deck added to your watchlist.",
    "deck_removed": "Deck removed from your watchlist.",
}


def _pct(v: float | None) -> str:
    return "no change" if not v else f"{'up' if v > 0 else 'down'} {abs(v):g}%"


def _changes_html(view: dict[str, Any]) -> str:
    parts = [
        f"{label}: {_pct(view.get(key))}"
        for label, key in (
            ("1 day", "change_1d_pct"),
            ("7 days", "change_7d_pct"),
            ("30 days", "change_30d_pct"),
        )
        if view.get(key) is not None
    ]
    return f"<p class='chg'>{_esc(' · '.join(parts))}</p>" if parts else ""


def history_html(view: dict[str, Any], what: str) -> str:
    """A price over time as a sparkline (with its words for a screen reader) and as a table."""
    hist = [h for h in view.get("history", []) if h.get("price") is not None]
    cur = view.get("currency", "usd")
    if not hist:
        return "<p class='muted small'>No price yet; it comes with the next daily check.</p>"
    out = ""
    if len(hist) >= 2:
        said = (
            f"{what}: {money(hist[0]['price'], cur)} on {hist[0]['day']}, "
            f"{money(hist[-1]['price'], cur)} on {hist[-1]['day']}."
        )
        out += _trend_svg([h["price"] for h in hist], said)
    rows = "".join(
        f"<tr><th scope='row'>{_esc(h['day'])}</th><td>{_esc(money(h['price'], cur))}</td></tr>"
        for h in reversed(hist)
    )
    return out + (
        "<details><summary>Price history table</summary>"
        f"<div class='tbl' role='region' aria-label='{_esc(what)}, newest first' tabindex='0'>"
        f"<table><thead><tr><th scope='col'>Day</th><th scope='col'>Price</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div></details>"
    )


def _card_row(v: dict[str, Any], csrf_in: str) -> str:
    where = " ".join(p for p in ((v.get("set") or "").upper(), v.get("collector_number") or "") if p)
    sub = " · ".join(p for p in (where or "any printing", v["finish"]) if p)
    price = (
        f"<b>{_esc(money(v['price'], v['currency']))}</b> on {_esc(v['day'])}"
        if v.get("price") is not None
        else "<span class='muted'>No price yet</span>"
    )
    alerts = v.get("alerts") or {}
    wid = _esc(v["watch_id"])
    return (
        f"<li class='wrow'><h3>{_esc(v['name'])} <span class='sub'>{_esc(sub)}</span></h3>"
        f"<p class='now'>{price}</p>{_changes_html(v)}"
        f"{history_html(v, v['name'])}"
        "<details><summary>Price alert" + (" (on)" if alerts else "") + "</summary>"
        f"<form method='post' class='row'>{csrf_in}<input type='hidden' name='action' value='alerts'>"
        f"<input type='hidden' name='watch_id' value='{wid}'>"
        + "".join(
            f"<div class='field'><label for='{k[0]}-{wid}'>{label}</label><input id='{k[0]}-{wid}' "
            f"name='alert_{k}' inputmode='decimal' value='{_esc(alerts.get(k, ''))}'></div>"
            for k, label in (
                ("pct", "Moves more than (%)"),
                ("above", "Rises above"),
                ("below", "Falls below"),
            )
        )
        + "<button>Save alert</button></form>"
        "<p class='muted small'>Checked once a day against the day before. Leave a box empty for no "
        "alert. Alerts reach you through notifications (Account page).</p></details>"
        f"<div class='acts'><a class='btn small' href='/cards?q={_esc(v['name'])}'>Open card</a>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='remove_card'>"
        f"<input type='hidden' name='watch_id' value='{wid}'>"
        f"<button class='danger small'>Remove</button></form></div></li>"
    )


def _deck_row(v: dict[str, Any], csrf_in: str) -> str:
    price = (
        # The deck page's "Est cost" totals Archidekt's prices; this value is Scryfall's, so each says
        # where its prices come from.
        f"<b>{_esc(money(v['price']))}</b> on {_esc(v['day'])} <span class='muted'>(Scryfall prices)</span>"
        if v.get("price") is not None
        else "<span class='muted'>No value yet</span>"
    )
    did = _esc(v["deck_id"])
    return (
        f"<li class='wrow'><h3>{_esc(v['name'] or 'Deck ' + v['deck_id'])} "
        f"<span class='sub'>{_esc(v['cards'])} cards</span></h3>"
        f"<p class='now'>{price}</p>{_changes_html(v)}"
        f"{history_html(v, 'Value of ' + (v['name'] or 'the deck'))}"
        f"<div class='acts'><a class='btn small' href='/decks/{did}'>Open deck</a>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='remove_deck'>"
        f"<input type='hidden' name='deck_id' value='{did}'>"
        f"<button class='danger small'>Remove</button></form></div></li>"
    )


def watchlist_body(state: AppState, sub: str, csrf: str | None, *, ok: str = "", err: str = "") -> str:
    svc = state.prices
    data = svc.overview(sub)
    csrf_in = f"<input type='hidden' name='csrf' value='{_esc(csrf or '')}'>"
    notice = ""
    if ok in OK_NOTES:
        notice = f"<div class='notice ok' role='status'>{_esc(OK_NOTES[ok])}</div>"
    if err:
        notice = f"<div class='notice error' role='alert'>{_esc(err)}</div>"
    lim = data["limits"]
    cards = "".join(_card_row(v, csrf_in) for v in data["cards"])
    decks = "".join(_deck_row(v, csrf_in) for v in data["decks"])
    return (
        notice
        + "<p class='muted'>Prices come from Scryfall (TCGplayer in dollars, Cardmarket in euros), checked "
        f"once a day at {_esc(data['checked_daily_at_utc'])} UTC for the cards and decks you watch here. "
        f"You may watch up to {lim['cards']} cards and {lim['decks']} decks. Your assistant can do the same "
        "with its watch tools.</p>"
        "<section class='card watch' aria-labelledby='wc-h'><h2 id='wc-h'>Cards</h2>"
        f"<form method='post' class='row'>{csrf_in}<input type='hidden' name='action' value='add_card'>"
        "<div class='field wide'><label for='w-name'>Card name</label>"
        "<input id='w-name' name='name' maxlength='150' autocomplete='off' data-suggest='cards'></div>"
        "<div class='field'><label for='w-set'>Set code (optional)</label>"
        "<input id='w-set' name='set_code' maxlength='6' autocomplete='off'></div>"
        "<div class='field'><label for='w-cn'>Number (optional)</label>"
        "<input id='w-cn' name='collector_number' maxlength='10' autocomplete='off'></div>"
        "<div class='field'><label for='w-fin'>Finish</label><select id='w-fin' name='finish'>"
        "<option value='nonfoil'>Non-foil</option><option value='foil'>Foil</option>"
        "<option value='etched'>Etched</option></select></div>"
        "<button class='primary'>Watch card</button></form>"
        "<p class='muted small'>A name alone follows the printing Scryfall gives for that name; add a set "
        "code and number to follow one printing.</p>"
        + (f"<ul class='plain wlist'>{cards}</ul>" if cards else "<p class='muted'>No cards watched yet.</p>")
        + "</section>"
        "<section class='card watch' aria-labelledby='wd-h'><h2 id='wd-h'>Decks</h2>"
        f"<form method='post' class='row'>{csrf_in}<input type='hidden' name='action' value='add_deck'>"
        "<div class='field wide'><label for='w-deck'>Archidekt deck link or number</label>"
        "<input id='w-deck' name='deck' maxlength='200' autocomplete='off' required></div>"
        "<button class='primary'>Watch deck</button></form>"
        "<p class='muted small'>A watched deck is read again once a day, so its value follows its cards. "
        "Its value uses Scryfall's prices, so it can differ from the deck page's estimated cost, which "
        "uses Archidekt's.</p>"
        + (f"<ul class='plain wlist'>{decks}</ul>" if decks else "<p class='muted'>No decks watched yet.</p>")
        + "</section>"
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
            head_extra=f"<style>{WATCH_CSS}</style>",
            user=display_name(user),
        )

    @server.custom_route("/watchlist", methods=["GET"], include_in_schema=False)
    async def watchlist_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/watchlist")
        ok = request.query_params.get("ok", "")
        return page(watchlist_body(state, sub, _csrf(s, sid), ok=ok), sub=sub, sid=sid)

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
            out = await state.prices.watch_card(
                sub, str(body.get("name") or ""), finish=str(body.get("finish") or "nonfoil")
            )
        except DeckError as exc:
            status = 404 if exc.kind == "not_found" else 429 if exc.kind == "rate_limited" else 400
            return JSONResponse(
                {"ok": False, "error": exc.kind, "message": str(exc)}, status, headers=NO_STORE
            )
        return JSONResponse({"ok": True, "watch": out}, headers=NO_STORE)


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


__all__ = ["add_watchlist_routes", "add_watchlist_tools", "history_html"]
