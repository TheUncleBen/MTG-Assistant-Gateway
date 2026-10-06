"""Admin pages and JSON API: who uses the gateway, what they did, and how much.

Everything here is for members of MTG_ADMIN_GROUP (who also need MTG_REQUIRED_GROUP to
sign in at all). While MTG_ADMIN_GROUP is unset the admin routes answer 404, and so do they
for a signed-in member who is not in the group: nothing tells an ordinary user that an
admin area exists. The identity provider stays the source of users and groups; an admin can
disable or enable an account here, revoke its tokens and sessions, and unlink Archidekt,
never create a user or change a group. Every action writes an ``admin_<action>`` audit row.
"""

from __future__ import annotations

import datetime as _dt
import hmac
import html
import json
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, quote

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from .decks import actor_label, current_client
from .pages import BROWSER_CLIENT_ID, _csrf, _when, browser_session, login_redirect, read_limited
from .theme import render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

ACTIONS = ("disable", "enable", "revoke", "unlink")
METRIC_KINDS = ("tool", "error", "api", "admin")
MAX_JSON = 16_384
DAYS = 30


class AdminError(Exception):
    """``code`` names the failure for the ?err= redirect: the page maps it to fixed text."""

    def __init__(self, kind: str, message: str, status: int = 400, *, code: str = "failed"):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.code = code


NO_STORE = {"Cache-Control": "no-store"}
# Notices /admin/users shows for ?ok= and ?err=. Only these codes are accepted; anything else
# shows nothing, so a crafted link cannot put words of its choosing in the page's notice box.
OK_MESSAGES = {
    "disable": "Account disabled. Its tokens and sessions were revoked.",
    "enable": "Account enabled. The person must sign in again.",
    "revoke": "Tokens and browser sessions revoked.",
    "unlink": "Archidekt account unlinked; the stored session was deleted.",
}
ERR_MESSAGES = {
    "unknown_action": "Unknown action.",
    "no_such_user": "No such user.",
    "self_disable": "You cannot disable your own account.",
    "failed": "That action could not be done.",
}


def is_admin(state: Any, sub: str) -> bool:
    """True when MTG_ADMIN_GROUP is set and ``sub`` is an enabled user recorded in that group."""
    group = state.settings.admin_group
    if not group:
        return False
    user = state.db.get_user(sub)
    return bool(user) and not user.get("disabled_at") and group in user["groups"]


def add_admin_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings

    def page(title: str, body: str, *, status: int = 200, sid: str | None = None) -> Response:
        return render(
            title, body, site=s.server_name, status=status, signed_in=True, csrf=_csrf(s, sid), admin=True
        )

    def not_found(api: bool) -> Response:
        if api:
            return JSONResponse({"ok": False, "error": "not_found", "message": "no such page"}, 404)
        return render("Not found", "<p>No such page.</p>", site=s.server_name, status=404)

    def admin_user(
        request: Request, *, api: bool = False, write: bool = False
    ) -> tuple[str, str | None] | Response:
        """(admin sub, browser session id) or the response that refuses the request.

        Today the only credential is the browser session cookie (plus the X-CSRF-Token header
        for API writes, as /scan/api does). A bearer-token path for the API belongs here too:
        resolve the token to a sub, return (sub, None), and skip the CSRF check, which only
        guards cookie-authenticated writes.
        """
        if not s.admin_group:
            return not_found(api)
        sub, sid = browser_session(state, request)
        if not sub:
            if api:
                return JSONResponse(
                    {"ok": False, "error": "unauthenticated", "login": "/login?next=/admin"}, 401
                )
            return login_redirect(request.url.path)
        if not is_admin(state, sub):
            return not_found(api)
        if write and api:
            expected = _csrf(s, sid) or ""
            given = request.headers.get("x-csrf-token", "")
            if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
                return JSONResponse(
                    {"ok": False, "error": "csrf", "message": "Reload the page and retry."}, 403
                )
        return sub, sid

    async def form(request: Request) -> dict[str, str]:
        raw = await read_limited(request, MAX_JSON)
        if raw is None:
            return {}
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}

    def act(admin_sub: str, target: str, action: str | None) -> dict[str, Any]:
        """Apply one admin action to ``target`` on behalf of ``admin_sub``; raises AdminError."""
        if action not in ACTIONS:
            raise AdminError("invalid", "Unknown action.", code="unknown_action")
        user = state.db.get_user(target)
        if user is None:
            raise AdminError("not_found", "No such user.", 404, code="no_such_user")
        out: dict[str, Any] = {"action": action, "target": target}
        current_client.set(BROWSER_CLIENT_ID)
        if action == "disable":
            if target == admin_sub:
                raise AdminError("invalid", "You cannot disable your own account.", code="self_disable")
            state.db.set_user_disabled(target, True)
            out.update(state.db.revoke_all_for_user(target))
        elif action == "enable":
            state.db.set_user_disabled(target, False)
        elif action == "revoke":
            out.update(state.db.revoke_all_for_user(target))
        elif action == "unlink":
            state.decks.unlink(target)
        state.db.audit(
            f"admin_{action}",
            sub=admin_sub,
            client_id=BROWSER_CLIENT_ID,
            detail={k: v for k, v in out.items() if k != "action"},
        )
        metrics = getattr(state, "metrics", None)
        if metrics is not None:
            metrics.record("admin", action, admin_sub)
        return out

    # -- pages --------------------------------------------------------------
    @server.custom_route("/admin", methods=["GET"], include_in_schema=False)
    async def overview(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        _sub, sid = who
        return page("Admin", _nav("/admin") + _overview_body(state), sid=sid)

    @server.custom_route("/admin/users", methods=["GET"], include_in_schema=False)
    async def users(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        sub, sid = who
        notice = _notice(request.query_params.get("ok"), request.query_params.get("err"))
        return page("Users", _nav("/admin/users") + notice + _users_body(state, sub, _csrf(s, sid)), sid=sid)

    @server.custom_route("/admin/users/{sub:path}", methods=["POST"], include_in_schema=False)
    async def users_post(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        admin_sub, sid = who
        data = await form(request)
        expected = _csrf(s, sid) or ""
        if not expected or not hmac.compare_digest(data.get("csrf", "").encode(), expected.encode()):
            return page(
                "Users", _err("This form expired. Reload the page and try again."), status=403, sid=sid
            )
        try:
            out = act(admin_sub, request.path_params["sub"], data.get("action"))
        except AdminError as exc:
            return RedirectResponse(f"/admin/users?err={quote(exc.code)}", status_code=303)
        return RedirectResponse(f"/admin/users?ok={out['action']}", status_code=303)

    @server.custom_route("/admin/activity", methods=["GET"], include_in_schema=False)
    async def activity(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        _sub, sid = who
        only = request.query_params.get("sub") or None
        rows = state.db.audit_recent(200, sub=only)
        return page("Activity", _nav("/admin/activity") + _activity_body(state, rows, only), sid=sid)

    @server.custom_route("/admin/metrics", methods=["GET"], include_in_schema=False)
    async def metrics_page(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        _sub, sid = who
        return page("Metrics", _nav("/admin/metrics") + _metrics_body(state), sid=sid)

    # -- JSON API -----------------------------------------------------------
    @server.custom_route("/api/v1/admin/overview", methods=["GET"], include_in_schema=False)
    async def api_overview(request: Request) -> Response:
        who = admin_user(request, api=True)
        if isinstance(who, Response):
            return who
        return JSONResponse({"ok": True, **overview_data(state)}, headers=NO_STORE)

    @server.custom_route("/api/v1/admin/users", methods=["GET"], include_in_schema=False)
    async def api_users(request: Request) -> Response:
        who = admin_user(request, api=True)
        if isinstance(who, Response):
            return who
        return JSONResponse({"ok": True, "users": users_data(state)}, headers=NO_STORE)

    @server.custom_route("/api/v1/admin/metrics", methods=["GET"], include_in_schema=False)
    async def api_metrics(request: Request) -> Response:
        who = admin_user(request, api=True)
        if isinstance(who, Response):
            return who
        raw_days = request.query_params.get("days", str(DAYS))
        try:
            if len(raw_days) > 6 or not (raw_days.isascii() and raw_days.isdigit()):
                raise ValueError(raw_days)
            days = max(1, min(int(raw_days), 365))
        except ValueError:
            return JSONResponse({"ok": False, "error": "invalid", "message": "days must be a number"}, 400)
        return JSONResponse(
            {
                "ok": True,
                "days": days,
                "totals": state.db.metrics_totals(days),
                "series": _series(state, days),
            },
            headers=NO_STORE,
        )

    @server.custom_route("/api/v1/admin/users/{sub:path}", methods=["POST"], include_in_schema=False)
    async def api_users_post(request: Request) -> Response:
        who = admin_user(request, api=True, write=True)
        if isinstance(who, Response):
            return who
        admin_sub, _sid = who
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse({"ok": False, "error": "invalid", "message": "send JSON"}, 415)
        raw = await read_limited(request, MAX_JSON)
        if raw is None:
            return JSONResponse({"ok": False, "error": "too_large"}, 413)
        try:
            data = json.loads(raw)
        except ValueError:
            return JSONResponse({"ok": False, "error": "invalid", "message": "bad JSON"}, 400)
        action = data.get("action") if isinstance(data, dict) else None
        try:
            out = act(admin_sub, request.path_params["sub"], action if isinstance(action, str) else None)
        except AdminError as exc:
            return JSONResponse(
                {"ok": False, "error": exc.kind, "message": str(exc)}, exc.status, headers=NO_STORE
            )
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)


# -- data (shared by the pages and the API) -----------------------------------
def _series(state: Any, days: int = DAYS) -> dict[str, dict[str, int]]:
    """{kind: {day: n}} over the window, every day present (zero-filled), oldest first."""
    rows = state.db.metrics_series(days)
    today = _dt.datetime.now(_dt.UTC).date()
    day_keys = [(today - _dt.timedelta(days=days - 1 - i)).isoformat() for i in range(days)]
    kinds = sorted({r["kind"] for r in rows} | set(METRIC_KINDS[:2]))
    out = {k: dict.fromkeys(day_keys, 0) for k in kinds}
    for r in rows:
        if r["day"] in out[r["kind"]]:
            out[r["kind"]][r["day"]] = int(r["n"])
    return out


def overview_data(state: Any, days: int = DAYS) -> dict[str, Any]:
    counts = state.db.overview_counts(days)
    totals = state.db.metrics_totals(days)
    by_kind: dict[str, int] = {}
    for t in totals:
        by_kind[t["kind"]] = by_kind.get(t["kind"], 0) + int(t["n"])
    return {
        "days": days,
        **counts,
        "applies": counts["proposals"].get("applied", 0),
        "tool_calls": by_kind.get("tool", 0),
        "errors": by_kind.get("error", 0),
        "by_kind": by_kind,
        "series": _series(state, days),
    }


def users_data(state: Any) -> list[dict[str, Any]]:
    out = []
    for u in state.db.list_users():
        out.append({**u, "clients": state.db.user_clients(u["sub"]), "disabled": bool(u["disabled_at"])})
    return out


# -- HTML -------------------------------------------------------------------------
def _nav(active: str) -> str:
    links = (("/admin", "Overview"), ("/admin/users", "Users"), ("/admin/activity", "Activity"))
    links += (("/admin/metrics", "Metrics"),)
    return (
        "<div class='actions' role='navigation' aria-label='Admin'>"
        + "".join(
            f"<a class='btn{' btn-primary' if href == active else ''}' href='{href}'>{label}</a>"
            for href, label in links
        )
        + "</div>"
    )


def _err(msg: str) -> str:
    return f"<div class='notice error'>{html.escape(msg)}</div>"


def _notice(ok: str | None, err: str | None) -> str:
    if ok in OK_MESSAGES:
        return f"<div class='notice ok'>{html.escape(OK_MESSAGES[ok])}</div>"
    if err in ERR_MESSAGES:
        return _err(ERR_MESSAGES[err])
    return ""


def _who(u: dict[str, Any]) -> str:
    return html.escape(str(u.get("preferred_username") or u.get("email") or u.get("sub") or ""))


def _stat(label: str, value: Any) -> str:
    return f"<dt>{html.escape(label)}</dt><dd><strong>{html.escape(str(value))}</strong></dd>"


def _sparkline(values: list[int], *, label: str, width: int = 240, height: int = 36) -> str:
    """A 30-day line as inline SVG (no script, no external request; CSP unchanged)."""
    if not values:
        return ""
    top = max(max(values), 1)
    step = width / max(len(values) - 1, 1)
    pts = " ".join(
        f"{i * step:.1f},{height - 2 - (v / top) * (height - 4):.1f}" for i, v in enumerate(values)
    )
    return (
        f"<svg role='img' viewBox='0 0 {width} {height}' width='{width}' height='{height}' "
        "preserveAspectRatio='none'>"
        f"<title>{html.escape(label)}: {', '.join(str(v) for v in values)}</title>"
        f"<polyline fill='none' stroke='var(--orange)' stroke-width='2' points='{pts}'/></svg>"
    )


def _overview_body(state: Any) -> str:
    d = overview_data(state)
    props = d["proposals"]
    cards = [
        "<div class='card'><h2>Users</h2><dl class='meta'>"
        + _stat("Signed in at least once", d["users"])
        + _stat("Disabled", d["disabled"])
        + _stat("Linked to Archidekt", d["linked"])
        + "</dl></div>",
        f"<div class='card'><h2>Last {d['days']} days</h2><dl class='meta'>"
        + _stat("Proposals", sum(props.values()))
        + "".join(_stat(f"&nbsp;&nbsp;{st}".replace("&nbsp;", " "), n) for st, n in sorted(props.items()))
        + _stat("Applies", d["applies"])
        + _stat("Tool calls", d["tool_calls"])
        + _stat("Errors", d["errors"])
        + "</dl></div>",
    ]
    rows = []
    for kind, by_day in d["series"].items():
        values = list(by_day.values())
        rows.append(
            f"<li><span class='name'>{html.escape(kind)}</span>"
            f"{_sparkline(values, label=kind)}<span class='when'>{sum(values)}</span></li>"
        )
    cards.append(
        f"<div class='card'><h2>Activity by day</h2><ul class='plain plist'>{''.join(rows)}</ul>"
        "<p class='muted small'>Counts are per UTC day. Tool calls and errors come from the MCP "
        "middleware, API calls from the JSON API, admin actions from this page.</p></div>"
    )
    return "".join(cards)


def _users_body(state: Any, admin_sub: str, csrf: str | None) -> str:
    users = users_data(state)
    if not users:
        return "<div class='card'><p>Nobody has signed in yet.</p></div>"
    csrf_in = f"<input type='hidden' name='csrf' value='{html.escape(csrf or '')}'>"
    items = []
    for u in users:
        sub = u["sub"]
        badges = ""
        if u["disabled"]:
            badges += "<span class='badge danger'>disabled</span>"
        if u["archidekt_username"]:
            badges += "<span class='badge ok'>archidekt</span>"
        if sub == admin_sub:
            badges += "<span class='badge'>you</span>"
        apps = ", ".join(html.escape(c["name"] or c["client_id"][:40]) for c in u["clients"]) or "none"
        meta = (
            "<dl class='meta'>"
            f"<dt>Name</dt><dd>{html.escape(u.get('name') or '')}</dd>"
            f"<dt>Email</dt><dd>{html.escape(u.get('email') or '')}</dd>"
            f"<dt>Subject</dt><dd><code>{html.escape(sub)}</code></dd>"
            f"<dt>Groups</dt><dd>{html.escape(', '.join(u['groups']) or 'none')}</dd>"
            f"<dt>First sign-in</dt><dd>{_when(u['first_login_at'])}</dd>"
            f"<dt>Last seen</dt><dd>{_when(u.get('last_seen_at') or u['last_login_at'])}</dd>"
            f"<dt>Archidekt</dt><dd>{html.escape(u['archidekt_username'] or 'not linked')}</dd>"
            f"<dt>Connected apps</dt><dd>{apps}</dd>"
            f"<dt>Active tokens</dt><dd>{u['active_tokens']}</dd>"
            f"<dt>Browser sessions</dt><dd>{u['browser_sessions']}</dd>"
            + (f"<dt>Disabled</dt><dd>{_when(u['disabled_at'])}</dd>" if u["disabled"] else "")
            + "</dl>"
        )
        buttons = ""
        if u["disabled"]:
            buttons += "<button name='action' value='enable' class='primary'>Enable</button>"
        elif sub != admin_sub:
            buttons += "<button name='action' value='disable' class='danger'>Disable</button>"
        buttons += "<button name='action' value='revoke'>Revoke tokens and sessions</button>"
        if u["archidekt_username"]:
            buttons += "<button name='action' value='unlink'>Unlink Archidekt</button>"
        buttons += f"<a class='btn' href='/admin/activity?sub={quote(sub, safe='')}'>Activity</a>"
        items.append(
            f"<li><span class='name'>{_who(u)}</span>{badges}"
            f"<span class='when'>seen {_when(u.get('last_seen_at') or u['last_login_at'])}</span>"
            f"<details><summary>Details and actions</summary>{meta}"
            f"<form method='post' action='/admin/users/{quote(sub, safe='')}'>{csrf_in}"
            f"<div class='actions'>{buttons}</div></form></details></li>"
        )
    return (
        f"<div class='card'><p class='muted small'>{len(users)} user{'s' if len(users) != 1 else ''}. "
        "Users and groups come from the identity provider; disabling here refuses the account on this "
        "gateway only.</p>"
        f"<ul class='plain plist'>{''.join(items)}</ul></div>"
    )


def _activity_body(state: Any, rows: list[dict[str, Any]], only: str | None) -> str:
    head = ""
    if only:
        head = (
            f"<p class='muted small'>Showing <code>{html.escape(only)}</code> only. "
            "<a href='/admin/activity'>Show everyone</a></p>"
        )
    if not rows:
        return f"<div class='card'>{head}<p>No activity recorded.</p></div>"
    items = []
    for r in rows:
        event = str(r["event"])
        cls = "danger" if ("rejected" in event or "refused" in event or "reuse" in event) else ""
        if event.startswith("admin_"):
            cls = "warn"
        detail = ""
        if r.get("detail"):
            detail = f"<code>{html.escape(json.dumps(r['detail'], sort_keys=True)[:300])}</code>"
        cid = r.get("client_id")
        client = actor_label(cid, state.db.client_name(cid) if cid and cid != BROWSER_CLIENT_ID else None)
        items.append(
            f"<li><span class='badge {cls}'>{html.escape(event)}</span>"
            f"<span class='name'>{html.escape(r.get('sub') or '')}</span>"
            f"<span class='muted small'>{html.escape(str(client)[:60])}</span>{detail}"
            f"<span class='when'>{_when(r['at'])}</span></li>"
        )
    return f"<div class='card'>{head}<ul class='plain plist'>{''.join(items)}</ul></div>"


def _metrics_body(state: Any) -> str:
    totals = state.db.metrics_totals(DAYS)
    by_kind: dict[str, list[dict[str, Any]]] = {}
    for t in totals:
        by_kind.setdefault(t["kind"], []).append(t)
    if not totals:
        return f"<div class='card'><p>No activity counted in the last {DAYS} days.</p></div>"
    cards = []
    titles = {"tool": "Tool calls", "error": "Errors", "api": "API calls"}
    for kind in sorted(by_kind, key=lambda k: (k not in METRIC_KINDS, k)):
        rows = by_kind[kind]
        items = "".join(
            f"<li><span class='name'>{html.escape(str(t['name']))}</span>"
            f"<span class='when'>{t['n']}</span></li>"
            for t in rows
        )
        total = sum(int(t["n"]) for t in rows)
        cards.append(
            f"<div class='card'><h2>{html.escape(titles.get(kind, kind.title()))} "
            f"<span class='badge'>{total}</span></h2><ul class='plain plist'>{items}</ul></div>"
        )
    return f"<p class='muted small'>Totals over the last {DAYS} UTC days.</p>" + "".join(cards)


__all__ = ["ACTIONS", "AdminError", "add_admin_routes", "is_admin", "overview_data", "users_data"]
