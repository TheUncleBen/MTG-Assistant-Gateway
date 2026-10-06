"""JSON API under ``/api/v1`` for the companion pages and the Android app.

Authentication is either the gateway's own OAuth bearer token (the same tokens the MCP endpoint
accepts, so an app registers as an ordinary OAuth client) or the browser session cookie with the
``X-CSRF-Token`` header on writes. Every reply is ``{ok: true, ...}`` or
``{ok: false, error, message}``, the shape the MCP tools use, and every write goes through the
same proposal flow: nothing here talks to Archidekt directly.
"""

from __future__ import annotations

import hmac
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import deck_stats
from .decks import DeckError, current_client
from .pages import BROWSER_CLIENT_ID, _csrf, browser_session, read_limited
from .views import deck_brief, deck_out

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .reports import ReportService

logger = logging.getLogger(__name__)

MAX_JSON = 2_000_000
STATUS_FOR = {
    "invalid": 400,
    "not_found": 404,
    "forbidden": 403,
    "not_linked": 409,
    "writes_disabled": 403,
    "browser_required": 403,
    "apply_too_soon": 425,
    "not_pending": 409,
    "already_applied": 409,
    "stale": 409,
    "rate_limited": 429,
    "other_client": 403,
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
        return Caller(access.subject, access.client_id, "api")
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
    except ValueError:
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
            return ok(await decks.propose(who.sub, str(data.get("deck_id", "")), data.get("changes")), 201)
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
        if who.via == "api" and s.writes_enabled and not s.apply_via_mcp:
            p = decks.describe(who.sub, pid)
            raise DeckError(
                "browser_required",
                "This gateway applies proposals only from the review page in the browser.",
                review_url=p["review_url"],
                state=p["state"],
            )
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
        reports.delete(who.sub, request.path_params["rid"])
        return ok({})

    # -- my activity ----------------------------------------------------------
    @route("/activity", "GET")
    async def activity(request: Request, who: Caller) -> Response:
        rows = state.db.audit_for_user(who.sub, parse_limit(request.query_params.get("limit"), 50, 1000))
        return ok({"events": rows})


__all__ = ["Caller", "add_api_routes", "caller_for", "fail", "json_body"]
