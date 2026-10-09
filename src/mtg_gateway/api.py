"""JSON API under ``/api/v1`` for the companion pages and the Android app.

Authentication is either the gateway's own OAuth bearer token (the same tokens the MCP endpoint
accepts, so an app registers as an ordinary OAuth client) or the browser session cookie with the
``X-CSRF-Token`` header on writes. Every reply is ``{ok: true, ...}`` or
``{ok: false, error, message}``, the shape the MCP tools use, and every write goes through the
same proposal flow: nothing here talks to Archidekt directly.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import deck_stats, modes
from .decks import DeckError, current_client, edit_mismatches, parse_changes, scopes_allow_writes
from .pages import BROWSER_CLIENT_ID, _csrf, browser_session, read_limited
from .views import deck_brief, deck_out

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .reports import ReportService

logger = logging.getLogger(__name__)

MAX_JSON = 2_000_000
# The deck page's own edit (POST /decks/{id}/edit) re-reads the deck after the apply; when
# Archidekt's read still shows the old rows it waits this long and reads once more.
EDIT_REREAD_DELAY = 1.5
STATUS_FOR = {
    "invalid": 400,
    "not_found": 404,
    "forbidden": 403,
    "not_linked": 409,
    "writes_disabled": 403,
    "browser_required": 403,
    "not_pending": 409,
    "already_applied": 409,
    "stale": 409,
    "rate_limited": 429,
    "other_client": 403,
    "other_account": 409,
    "unavailable": 503,
    "contract": 502,
    "auth": 409,
    "backup_failed": 502,
    "verify_mismatch": 502,
}


@dataclass(frozen=True)
class Caller:
    sub: str
    actor: str  # OAuth client id, or "__browser__"
    via: str  # "api" for a bearer token, "browser" for the cookie
    scopes: tuple[str, ...] = ()  # the bearer token's OAuth scopes (none for the browser)


def fail(exc: DeckError) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": exc.kind, "message": str(exc), **exc.extra}, STATUS_FOR.get(exc.kind, 400)
    )


def _unauth(message: str = "Sign in to use the API.") -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": "unauthenticated", "message": message, "login": "/login"},
        401,
        headers={"WWW-Authenticate": 'Bearer realm="mtg-gateway"'},
    )


async def caller_for(state: AppState, request: Request, *, write: bool) -> Caller | Response:
    """Who is calling: a bearer token holder or the signed-in browser. ``write`` demands the
    CSRF header for the cookie path (a bearer token is its own proof against CSRF)."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        access = await state.provider.load_access_token(token) if token else None
        if access is None or not access.subject:
            return _unauth("The access token is missing, expired or revoked.")
        # load_access_token refuses disabled members and anyone outside MTG_REQUIRED_GROUP.
        return Caller(access.subject, access.client_id, "api", tuple(access.scopes or ()))
    sub, sid = browser_session(state, request)
    if not sub or not sid:
        return _unauth()
    if write:
        expected = _csrf(state.settings, sid) or ""
        given = request.headers.get("X-CSRF-Token", "")
        if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
            return JSONResponse({"ok": False, "error": "csrf", "message": "Reload the page and retry."}, 403)
    return Caller(sub, BROWSER_CLIENT_ID, "browser")


async def json_body(request: Request) -> dict[str, Any] | Response:
    raw = await read_limited(request, MAX_JSON)
    if raw is None:
        return JSONResponse({"ok": False, "error": "too_large", "message": "request body over 2 MB"}, 413)
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except (ValueError, RecursionError):  # RecursionError: deeply nested arrays or objects
        return JSONResponse({"ok": False, "error": "invalid", "message": "body must be JSON"}, 400)
    if not isinstance(data, dict):
        return JSONResponse({"ok": False, "error": "invalid", "message": "body must be a JSON object"}, 400)
    return data


def parse_limit(raw: str | None, default: int, maximum: int) -> int:
    """A ``?limit=`` value: ASCII digits only (at most 6 of them), clamped to 1..maximum; anything
    else is the default. Never raises, so a crafted value cannot turn into a 500."""
    if not raw or len(raw) > 6 or not (raw.isascii() and raw.isdigit()):
        return default
    return max(1, min(int(raw), maximum))


Handler = Callable[[Request, Caller], Awaitable[Response]]


def add_api_routes(server: MCPServer, state: AppState, reports: ReportService) -> None:
    s = state.settings
    decks = state.decks

    def route(path: str, method: str, *, write: bool = False) -> Callable[[Handler], Handler]:
        """Register ``fn(request, caller)`` at ``/api/v1``+path with auth and error mapping."""

        def deco(fn: Handler) -> Handler:
            async def wrapped(request: Request) -> Response:
                who = await caller_for(state, request, write=write)
                if isinstance(who, Response):
                    return who
                if write and who.via == "api" and not scopes_allow_writes(who.scopes):
                    return JSONResponse(
                        {
                            "ok": False,
                            "error": "insufficient_scope",
                            "message": "This app was connected read-only (scope mtg.read); it cannot "
                            "change anything. Reconnect it with the mtg scope to propose changes.",
                        },
                        403,
                        headers={"WWW-Authenticate": 'Bearer error="insufficient_scope", scope="mtg"'},
                    )
                current_client.set(who.actor)
                if state.metrics is not None:
                    state.metrics.record("api", f"{method} {path}", who.sub)
                try:
                    resp = await fn(request, who)
                except DeckError as exc:
                    return fail(exc)
                resp.headers.setdefault("Cache-Control", "no-store")
                return resp

            server.custom_route("/api/v1" + path, methods=[method], include_in_schema=False)(wrapped)
            return fn

        return deco

    def ok(payload: dict[str, Any], status: int = 200) -> JSONResponse:
        return JSONResponse({"ok": True, **payload}, status)

    # -- identity -------------------------------------------------------------
    @route("/me", "GET")
    async def me(_request: Request, who: Caller) -> Response:
        user = state.db.get_user(who.sub) or {}
        return ok(
            {
                "sub": who.sub,
                "name": user.get("name"),
                "preferred_username": user.get("preferred_username"),
                "email": user.get("email"),
                "groups": user.get("groups", []),
                "is_admin": bool(s.admin_group and s.admin_group in (user.get("groups") or [])),
                "account": decks.status(who.sub),
                "via": who.via,
            }
        )

    # -- decks ----------------------------------------------------------------
    @route("/decks", "GET")
    async def list_decks(request: Request, who: Caller) -> Response:
        rows = await decks.list_decks(who.sub)
        q = (request.query_params.get("q") or "").strip().lower()
        fmt = (request.query_params.get("format") or "").strip().lower()
        folder = request.query_params.get("folder")
        if q:
            rows = [d for d in rows if q in str(d.get("name", "")).lower()]
        if fmt:
            rows = [d for d in rows if str(d.get("format_name") or d.get("format") or "").lower() == fmt]
        if folder is not None:
            rows = [d for d in rows if (d.get("folder") or "") == folder]
        for d in rows:
            d.setdefault("url", f"https://archidekt.com/decks/{d['id']}")
        return ok({"decks": rows, "count": len(rows)})

    @route("/decks/{deck_id}", "GET")
    async def get_deck(request: Request, who: Caller) -> Response:
        deck = await decks.get_any_deck(who.sub, request.path_params["deck_id"])
        with_cards = request.query_params.get("cards", "1") != "0"
        return ok(deck_out(deck, with_cards=with_cards))

    @route("/decks/{deck_id}/stats", "GET")
    async def stats(request: Request, who: Caller) -> Response:
        deck = await decks.get_any_deck(who.sub, request.path_params["deck_id"])
        return ok({"deck": deck_brief(deck), "stats": deck_stats.compute(deck)})

    @route("/decks/{deck_id}/history", "GET")
    async def deck_history(request: Request, who: Caller) -> Response:
        deck_id = str(request.path_params["deck_id"])
        proposals = [p for p in decks.list_proposals(who.sub) if p["deck_id"] == deck_id]
        snapshots = [x for x in decks.list_snapshots(who.sub) if x["deck_id"] == deck_id]
        return ok(
            {
                "deck_id": deck_id,
                "proposals": proposals,
                "snapshots": snapshots,
                "reports": reports.list(who.sub, deck_id),
                "series": reports.series(who.sub, deck_id),
            }
        )

    @route("/compare", "GET")
    async def compare(request: Request, who: Caller) -> Response:
        a = request.query_params.get("a", "")
        b = request.query_params.get("b", "")
        if not a or not b:
            raise DeckError("invalid", "give a and b: deck ids or URLs, or snapshot ids")
        deck_a = await _deck_or_snapshot(who.sub, a)
        deck_b = await _deck_or_snapshot(who.sub, b)
        return ok({"a": deck_brief(deck_a), "b": deck_brief(deck_b), **deck_stats.compare(deck_a, deck_b)})

    async def _deck_or_snapshot(sub: str, ref: str) -> Any:
        from .archidekt import parse_deck

        if ref.startswith("snap_") or (not ref.isdigit() and "/" not in ref and "." not in ref):
            return parse_deck(decks.snapshot(sub, ref.removeprefix("snap_"))["deck"])
        return await decks.get_any_deck(sub, ref)

    # -- the deck page's own edits -------------------------------------------
    @route("/decks/{deck_id}/edit", "POST", write=True)
    async def edit_deck(request: Request, who: Caller) -> Response:
        """One edit made on the deck page itself (the card menu's quantity and category items,
        the viewer's buttons, a drag between stacks). It runs the proposals path exactly: propose,
        the hand-edit confirmation rule, apply with its snapshot as the member's own press; then
        the deck is read again from Archidekt and the answer carries the touched rows, the
        freshly computed checks and the re-rendered Legality chip and Deck checks panel, so the
        page redraws in place. Body: ``{changes: [1..40]}`` (``confirmed: true`` after a
        needs_confirm answer) or ``{proposal_id}`` to apply the proposal a needs_confirm answer
        named; ``{refresh: true, names: [...]}`` only reads the deck again (the page's follow-up
        when an answer was stale). Browser session only; apps propose through /api/v1/proposals."""
        from .archidekt import front_face, parse_deck
        from .deckpage import checks_panel_html, legality_chip_html, legality_panel_html

        if who.via != "browser":
            raise DeckError(
                "browser_required",
                "This is the deck page's own save; apps propose through /api/v1/proposals.",
            )
        data = await json_body(request)
        if isinstance(data, Response):
            return data
        deck_id = str(request.path_params["deck_id"])

        def view(deck: Any, touched: set[str], *, stale: bool) -> dict[str, Any]:
            """The rows of the touched cards and the page fragments, from one read of the deck."""
            stats = deck_stats.compute(deck)
            checks = stats.get("checks") or {}
            return {
                "stale": stale,
                "rows": [
                    {
                        "name": c.name,
                        "qty": c.quantity,
                        "zone": "main" if deck.in_deck(c) else "side",
                        "categories": list(c.categories),
                        "relation_id": c.relation_id,
                    }
                    for c in deck.cards
                    if front_face(c.name).casefold() in touched
                ],
                "stats": {
                    "legal": bool(deck.format) and not stats.get("legality_problems"),
                    "problems": stats.get("legality_problems") or [],
                    "checks": checks,
                    "checks_ok": bool(checks.get("ok", True)),
                    "card_count": stats.get("card_count", 0),
                    "distinct": stats.get("distinct", 0),
                    "land_count": stats.get("land_count", 0),
                    "side_count": sum(c.quantity for c in deck.side_cards),
                    "price_total": stats.get("price_total"),
                    "salt_total": stats.get("salt_total"),
                    "format": deck.format,
                },
                "banner_html": legality_chip_html(deck, stats),
                "checks_html": checks_panel_html(deck, stats),
                "legality_html": legality_panel_html(deck, stats),
            }

        if data.get("refresh") is True:
            # The page's one follow-up after a stale answer: the deck read again, nothing written.
            names = data.get("names")
            if not isinstance(names, list) or len(names) > 40 or not all(isinstance(n, str) for n in names):
                raise DeckError("invalid", "names must be a list of up to 40 card names")
            deck = await decks.get_own_deck(who.sub, deck_id)
            return ok({"applied": None, **view(deck, {front_face(n).casefold() for n in names}, stale=False)})
        pid = data.get("proposal_id")
        if pid is not None:
            if not isinstance(pid, str) or not pid:
                raise DeckError("invalid", "proposal_id must be a string")
            p = decks.describe(who.sub, pid)
            if p.get("kind") != "edit" or str(p.get("deck_id")) != deck_id.strip():
                raise DeckError("invalid", "That proposal is not an edit of this deck.")
        else:
            p = await decks.propose(who.sub, deck_id, data.get("changes"))
            why = modes.hand_edit_confirm("edit", p.get("rows"))
            if why and data.get("confirmed") is not True:
                pid = p["proposal_id"]
                return ok({"applied": False, "needs_confirm": True, "why": why, "proposal_id": pid}, 201)
        pid = p["proposal_id"]
        if not s.writes_enabled:
            # kept for review, like every proposal while writes are off; the page goes there
            return ok({"applied": False, "proposal_id": pid, "review_url": f"/proposals/{pid}"}, 201)
        result = await decks.apply(who.sub, pid, via="browser")
        applied = result.get("state") == "applied"
        out: dict[str, Any] = {
            "applied": applied,
            "proposal_id": pid,
            "snapshot_id": result.get("snapshot_id"),
            "result": result,
        }
        if not applied:  # a large edit still running: the page says so and offers the review page
            return ok(out, 202)
        changes = parse_changes(result.get("changes") or p.get("changes"))
        # The apply verified the deck once; this read is the one the page draws from. Archidekt's
        # reads have been seen to lag a write, so a deck that does not show the change yet is
        # read once more after a short pause, and the answer says when it still has not caught up.
        deck = await decks.get_own_deck(who.sub, deck_id)
        stale = False
        try:
            before = parse_deck(decks.snapshot(who.sub, str(result.get("snapshot_id")))["deck"])
            if edit_mismatches(before, changes, deck):
                await asyncio.sleep(EDIT_REREAD_DELAY)
                deck = await decks.get_own_deck(who.sub, deck_id)
                stale = bool(edit_mismatches(before, changes, deck))
        except DeckError:  # no snapshot to compare with: answer with the read as it is
            logger.info("edit %s: could not compare the re-read deck with its snapshot", pid)
        out.update(view(deck, {front_face(ch.card_name).casefold() for ch in changes}, stale=stale))
        return ok(out, 201)

    # -- proposals ------------------------------------------------------------
    @route("/proposals", "GET")
    async def list_proposals(request: Request, who: Caller) -> Response:
        rows = decks.list_proposals(who.sub)
        state_filter = request.query_params.get("state")
        if state_filter:
            rows = [p for p in rows if p["state"] == state_filter]
        return ok({"proposals": rows})

    @route("/proposals", "POST", write=True)
    async def create_proposal(request: Request, who: Caller) -> Response:
        data = await json_body(request)
        if isinstance(data, Response):
            return data
        kind = str(data.get("kind") or ("new_deck" if "name" in data and "deck_id" not in data else "edit"))
        if kind == "edit":
            p = await decks.propose(who.sub, str(data.get("deck_id", "")), data.get("changes"))
            # The member's own edit in the app is their approval (``apply: true``): it is applied at
            # once with its snapshot, unless the change is big enough to ask first (modes.hand_edit_confirm),
            # when the page shows the reason and sends ``confirmed: true``. Bearer tokens (apps,
            # assistants) cannot use this: their proposals wait for the approval mode as always.
            if who.via == "browser" and data.get("apply") is True:
                why = modes.hand_edit_confirm("edit", p.get("rows"))
                if why and data.get("confirmed") is not True:
                    return ok({**p, "applied": False, "needs_confirm": True, "why": why}, 201)
                result = await decks.apply(who.sub, p["proposal_id"], via="browser")
                # a large edit may still be running ("applying"); result carries its progress
                return ok({**p, "applied": result.get("state") == "applied", "result": result}, 201)
            return ok(p, 201)
        if kind == "new_deck":
            return ok(
                await decks.propose_new_deck(
                    who.sub,
                    name=str(data.get("name") or ""),
                    deck_format=str(data.get("format") or data.get("deck_format") or "commander"),
                    cards=data.get("cards"),
                    decklist_text=data.get("decklist_text"),
                    csv_text=data.get("csv_text"),
                    private=data.get("private", True) is not False,
                ),
                201,
            )
        if kind == "restore":
            return ok(await decks.propose_restore(who.sub, str(data.get("snapshot_id") or "")), 201)
        if kind == "details":
            return ok(
                await decks.propose_deck_details(who.sub, str(data.get("deck_id", "")), data.get("details")),
                201,
            )
        if kind == "clone":
            name = data.get("name")
            if name is not None and not isinstance(name, str):
                raise DeckError("invalid", "name must be a string")
            return ok(await decks.propose_clone(who.sub, str(data.get("deck_id", "")), name), 201)
        raise DeckError("invalid", "kind must be edit, new_deck, restore, details or clone")

    @route("/proposals/{pid}", "GET")
    async def get_proposal(request: Request, who: Caller) -> Response:
        return ok(decks.describe(who.sub, request.path_params["pid"]))

    @route("/proposals/{pid}/apply", "POST", write=True)
    async def apply(request: Request, who: Caller) -> Response:
        pid = request.path_params["pid"]
        # A bearer token applies under the member's approval mode, exactly like apply_proposal
        # over MCP (decks.apply decides); the browser session is the member's own press.
        return ok(await decks.apply(who.sub, pid, via="browser" if who.via == "browser" else "mcp"))

    @route("/proposals/{pid}/reject", "POST", write=True)
    async def reject(request: Request, who: Caller) -> Response:
        return ok(decks.reject(who.sub, request.path_params["pid"], via=who.via))

    # -- snapshots ------------------------------------------------------------
    @route("/snapshots", "GET")
    async def list_snapshots(request: Request, who: Caller) -> Response:
        rows = decks.list_snapshots(who.sub)
        deck_id = request.query_params.get("deck_id")
        if deck_id:
            rows = [x for x in rows if x["deck_id"] == deck_id]
        return ok({"snapshots": rows})

    @route("/snapshots/{sid}", "GET")
    async def get_snapshot(request: Request, who: Caller) -> Response:
        from .archidekt import parse_deck

        snap = decks.snapshot(who.sub, request.path_params["sid"])
        deck = parse_deck(snap["deck"])
        return ok(
            {
                "snapshot_id": snap["id"],
                "proposal_id": snap.get("proposal_id"),
                "taken_at": snap["taken_at"],
                "backup_url": snap.get("backup_url"),
                "deck": deck_out(deck),
            }
        )

    # -- reports --------------------------------------------------------------
    @route("/reports", "GET")
    async def list_reports(request: Request, who: Caller) -> Response:
        return ok({"reports": reports.list(who.sub, request.query_params.get("deck_id"))})

    @route("/reports", "POST", write=True)
    async def run_report(request: Request, who: Caller) -> Response:
        data = await json_body(request)
        if isinstance(data, Response):
            return data
        games = data.get("games", 300)
        return ok(
            await reports.run(
                who.sub,
                str(data.get("deck_id") or data.get("deck_ref") or ""),
                simulate=data.get("simulate", True) is not False,
                games=games if isinstance(games, int) else 300,
            ),
            201,
        )

    @route("/reports/{rid}", "GET")
    async def get_report(request: Request, who: Caller) -> Response:
        return ok(reports.get(who.sub, request.path_params["rid"]))

    @route("/reports/{rid}", "DELETE", write=True)
    async def delete_report(request: Request, who: Caller) -> Response:
        # an app deletes only reports it ran; the member's browser any of theirs
        reports.delete(who.sub, request.path_params["rid"], client_id=who.actor if who.via == "api" else None)
        return ok({})

    # -- my activity ----------------------------------------------------------
    @route("/activity", "GET")
    async def activity(request: Request, who: Caller) -> Response:
        rows = state.db.audit_for_user(who.sub, parse_limit(request.query_params.get("limit"), 50, 1000))
        return ok({"events": rows})


__all__ = ["Caller", "add_api_routes", "caller_for", "fail", "json_body"]
