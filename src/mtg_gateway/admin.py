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
import html
import json
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, quote, urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import activity_view, idp_sweep, settings_page, tool_labels, ui, update_check, upgrade_backup
from .companion import member_name
from .config import MANAGED_SETTINGS
from .decks import ADMIN_CLIENT, current_client
from .pages import (
    BROWSER_CLIENT_ID,
    _csrf,
    browser_session,
    csrf_form_ok,
    csrf_header_ok,
    login_redirect,
    read_limited,
)
from .theme import display_name, icon, plural, render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

ACTIONS = ("disable", "enable", "revoke", "unlink", "delete_data")
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
    "delete_data": "Deleted everything the gateway kept about that person.",
    "model_removed": "Card model removed.",
}
ERR_MESSAGES = {
    "unknown_action": "Unknown action.",
    "no_such_user": "No such user.",
    "self_disable": "You cannot disable your own account.",
    "self_delete": "Use Delete my data on your Account page.",
    "confirm_delete": "Tick the confirmation box to delete.",
    "failed": "That action could not be done.",
    "model_gone": "That card model was already removed.",
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

    def page(
        title: str,
        body: str,
        *,
        status: int = 200,
        sid: str | None = None,
        sid_sub: str | None = None,
        head_extra: str = "",
    ) -> Response:
        """An admin page: the 1200 px width of every admin tab (so the tab bar sits in the same
        place on each), its own heading in the body, the admin styles and script."""
        return render(
            title,
            body,
            site=s.server_name,
            status=status,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=True,
            user=display_name(state.db.get_user(sid_sub) if sid_sub else None),
            head_extra=ADMIN_HEAD + head_extra,
            heading=False,
            main_class="ui-wide",
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
            if not csrf_header_ok(s, sid, request):
                return JSONResponse(
                    {"ok": False, "error": "csrf", "message": "Reload the page and retry."}, 403
                )
        return sub, sid

    async def form(request: Request) -> dict[str, str]:
        raw = await read_limited(request, MAX_JSON)
        if raw is None:
            return {}
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}

    def act(admin_sub: str, target: str, action: str | None, *, confirmed: bool = False) -> dict[str, Any]:
        """Apply one admin action to ``target`` on behalf of ``admin_sub``; raises AdminError."""
        if action not in ACTIONS:
            raise AdminError("invalid", "Unknown action.", code="unknown_action")
        user = state.db.get_user(target)
        if user is None:
            raise AdminError("not_found", "No such user.", 404, code="no_such_user")
        out: dict[str, Any] = {"action": action, "target": target}
        # Rows written under the member's own account (the unlink) name an administrator as the
        # actor, never the member's browser.
        current_client.set(ADMIN_CLIENT)
        if action == "disable":
            if target == admin_sub:
                raise AdminError("invalid", "You cannot disable your own account.", code="self_disable")
            state.db.set_user_disabled(target, True)
            out.update(state.db.revoke_all_for_user(target))
            # A disabled account cannot sign in, so its Archidekt session is not kept either.
            state.decks.unlink(target)
        elif action == "enable":
            state.db.set_user_disabled(target, False)
        elif action == "revoke":
            out.update(state.db.revoke_all_for_user(target))
        elif action == "unlink":
            state.decks.unlink(target)
        elif action == "delete_data":
            # For a former member (or an account from an earlier identity provider): everything
            # the gateway keeps about them, as their own "Delete my data" does.
            if target == admin_sub:
                raise AdminError("invalid", "Use Delete my data on your Account page.", code="self_delete")
            if not confirmed:
                raise AdminError("invalid", "Tick the confirmation box to delete.", code="confirm_delete")
            if getattr(state, "reports", None) is not None:  # their unfinished Forge games stop too
                state.reports.forget_member(target)
            out.update(state.db.delete_member_data(target))
            state.decks.forget_member(target)
            if state.membership is not None:
                state.membership.avatars.delete(target)
        if action != "unlink":  # the unlink wrote its own row in the member's log above
            # the member's activity log shows what an administrator did to their account
            state.db.audit(
                f"admin_{action}",
                sub=target,
                client_id=ADMIN_CLIENT,
                detail={"by": "admin", **{k: v for k, v in out.items() if k != "action"}},
            )
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
        sub, sid = who
        return page("Admin: overview", _overview_body(state), sid=sid, sid_sub=sub)

    @server.custom_route("/admin/users", methods=["GET"], include_in_schema=False)
    async def users(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        sub, sid = who
        notice = _notice(request.query_params.get("ok"), request.query_params.get("err"))
        return page(
            "Admin: users",
            _users_body(state, sub, _csrf(s, sid), request.query_params, notice=notice),
            sid=sid,
            sid_sub=sub,
        )

    @server.custom_route("/admin/users/{sub:path}", methods=["POST"], include_in_schema=False)
    async def users_post(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        admin_sub, sid = who
        data = await form(request)
        if not csrf_form_ok(s, sid, data):
            return page(
                "Admin: users",
                "<div class='adm au'>"
                + _head("Admin: users", USERS_PURPOSE, "/admin/users")
                + _err("This form expired. Reload the page and try again.")
                + "</div>",
                status=403,
                sid=sid,
                sid_sub=admin_sub,
            )
        try:
            out = act(
                admin_sub,
                request.path_params["sub"],
                data.get("action"),
                confirmed=data.get("confirm") == "yes",
            )
        except AdminError as exc:
            return RedirectResponse(f"/admin/users?err={quote(exc.code)}", status_code=303)
        return RedirectResponse(f"/admin/users?ok={out['action']}", status_code=303)

    @server.custom_route("/admin/models", methods=["GET"], include_in_schema=False)
    async def models(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        sub, sid = who
        notice = _notice(request.query_params.get("ok"), request.query_params.get("err"))
        return page(
            "Admin: card models",
            _models_body(state, _csrf(s, sid), request.query_params, notice=notice),
            sid=sid,
            sid_sub=sub,
        )

    @server.custom_route("/admin/models", methods=["POST"], include_in_schema=False)
    async def models_post(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        admin_sub, sid = who
        data = await form(request)
        if not csrf_form_ok(s, sid, data):
            return page(
                "Admin: card models",
                "<div class='adm amo'>"
                + _head("Admin: card models", MODELS_PURPOSE, "/admin/models")
                + _err("This form expired. Reload the page and try again.")
                + "</div>",
                status=403,
                sid=sid,
                sid_sub=admin_sub,
            )
        key, owner = str(data.get("card_key") or ""), str(data.get("added_by") or "")
        removed = bool(key and owner) and state.db.admin_delete_card_model(key, owner, by=admin_sub)
        # back to the list as it was (its search, filter and page); the old form's page number too
        back = str(data.get("back") or "")
        if not back.startswith("/admin/models") or any(c in back for c in "\r\n"):
            back = "/admin/models"
        page_no = str(data.get("page") or "")
        if page_no.isdigit() and "?" not in back:
            back = _href("/admin/models", offset=max(0, int(page_no) - 1) * PER_PAGE)
        sep = "&" if "?" in back else "?"
        return RedirectResponse(
            f"{back}{sep}{'ok=model_removed' if removed else 'err=model_gone'}", status_code=303
        )

    @server.custom_route("/admin/activity", methods=["GET"], include_in_schema=False)
    async def activity(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        sub, sid = who
        return page(
            "Admin: activity",
            activity_view.admin_body(state, request.query_params, nav=_nav("/admin/activity")),
            sid=sid,
            sid_sub=sub,
            head_extra=activity_view.HEAD_ADMIN,
        )

    def settings_page_response(
        sub: str,
        sid: str | None,
        *,
        outcome: settings_page.Outcome | None = None,
        notice: str = "",
        focus: str | None = None,
        status: int = 200,
        locked_only: bool = False,
    ) -> Response:
        body = settings_page.settings_body(
            state.live,
            state.settings,
            csrf=_csrf(s, sid),
            who=lambda who_sub: html.escape(
                display_name(state.db.get_user(who_sub)) if who_sub else "someone"
            ),
            when=ui.when,
            outcome=outcome,
            notice=notice,
            focus=focus,
            locked_only=locked_only,
        )
        return page(
            "Admin: settings",
            f"<div class='adm as'>{_head('Admin: settings', settings_page.PURPOSE, '/admin/settings')}"
            f"{body}</div>",
            status=status,
            sid=sid,
            sid_sub=sub,
            head_extra=f"<style>{settings_page.SETTINGS_CSS}</style>"
            "<script src='/static/settings.js' defer></script>",
        )

    @server.custom_route("/admin/settings", methods=["GET"], include_in_schema=False)
    async def settings_get(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        sub, sid = who
        q = request.query_params
        notice, focus = "", None
        if q.get("open") in settings_page.FOLDED:  # a link to a setting in a folded group
            focus = q.get("open")
        group = q.get("saved")
        if group in {g for g, _t, _i in settings_page.GROUPS}:
            focus = group
            try:
                since = int(q.get("after", "0"))
            except ValueError:
                since = 0
            notice = settings_page.changes_notice(_saved_changes(state, sub, since))
        return settings_page_response(
            sub, sid, notice=notice, focus=focus, locked_only=q.get("locked") == "1"
        )

    @server.custom_route("/admin/settings", methods=["POST"], include_in_schema=False)
    async def settings_post(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        sub, sid = who
        data = await form(request)
        if not csrf_form_ok(s, sid, data):
            return page(
                "Admin: settings",
                "<div class='adm as'>"
                + _head("Admin: settings", settings_page.PURPOSE, "/admin/settings")
                + _err("This form expired. Reload the page and try again.")
                + "</div>",
                status=403,
                sid=sid,
                sid_sub=sub,
            )
        last = state.db.audit_recent(1)
        started = last[0]["id"] if last else 0
        outcome = settings_page.apply_form(state.live, data, by=sub)
        reset = data.get("reset") or ""
        group = MANAGED_SETTINGS[reset].group if reset in MANAGED_SETTINGS else data.get("group", "")
        if "_" in outcome.errors:
            notice = _err(outcome.errors["_"])
            return settings_page_response(sub, sid, notice=notice, status=400)
        if outcome.errors:
            n = len(outcome.errors)
            first = settings_page.label(next(iter(outcome.errors)))
            notice = _err(
                f"Nothing was saved. See the message under \u201c{first}\u201d."
                if n == 1
                else f"Nothing was saved. See the {n} messages in this section."
            )
            return settings_page_response(sub, sid, outcome=outcome, notice=notice, focus=group, status=400)
        if outcome.unsaved:
            # Back to default with other edits in the section: say so and keep them in the form,
            # rather than a redirect that would drop them.
            notice = settings_page.changes_notice(outcome.changes) + settings_page.unsaved_notice(
                outcome.unsaved, group
            )
            return settings_page_response(sub, sid, outcome=outcome, notice=notice, focus=group)
        anchor = f"#s-row-{reset}" if reset else f"#g-{quote(group)}"
        return RedirectResponse(
            f"/admin/settings?saved={quote(group)}&after={started}{anchor}", status_code=303
        )

    @server.custom_route("/admin/metrics", methods=["GET"], include_in_schema=False)
    async def metrics_page(request: Request) -> Response:
        who = admin_user(request)
        if isinstance(who, Response):
            return who
        sub, sid = who
        return page("Admin: metrics", _metrics_body(state, request.query_params), sid=sid, sid_sub=sub)

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
        except (ValueError, RecursionError):  # RecursionError: deeply nested arrays or objects
            return JSONResponse({"ok": False, "error": "invalid", "message": "bad JSON"}, 400)
        action = data.get("action") if isinstance(data, dict) else None
        try:
            out = act(
                admin_sub,
                request.path_params["sub"],
                action if isinstance(action, str) else None,
                confirmed=isinstance(data, dict) and data.get("confirm") is True,
            )
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
        "system": system_data(state),
    }


def system_data(state: Any) -> dict[str, Any]:
    """What an operator checks first when something seems wrong: version, database, last backup."""
    from . import __version__
    from . import backup as backup_mod

    settings = getattr(state, "settings", None)
    backup_dir = getattr(settings, "backup_dir", None)
    out: dict[str, Any] = {
        "version": __version__,
        "schema_version": state.db.schema_version,
        "database_bytes": state.db.size_bytes(),
        "backups_enabled": backup_dir is not None,
        "last_backup_at": None,
        "last_backup_error": None,
    }
    if backup_dir is not None:
        newest = backup_mod.newest_backup(backup_dir)
        if newest is not None:
            out["last_backup_at"] = int(newest[1])
        if backup_mod.last_run.get("ok") is False:
            out["last_backup_error"] = backup_mod.last_run.get("error")
            out["last_backup_error_at"] = backup_mod.last_run.get("at")
    # The copies saved before database upgrades (upgrade_backup.py), and what this start did.
    db_path = getattr(state.db, "path", None)
    if isinstance(db_path, Path) and str(db_path) != ":memory:":
        folder = getattr(state.db, "upgrade_dir", None) or upgrade_backup.folder_for(None, db_path)
        out["before_upgrade"] = upgrade_backup.summary(folder)
    out["updates"] = update_check.status()
    # The research service as the health check last found it (Docker probes /healthz regularly).
    proxy = getattr(state, "mf_proxy", None)
    if proxy is None:
        out["mystic_forge"] = "not configured"
    else:
        at, ok = proxy.last_health
        out["mystic_forge"] = ("answering" if ok else "NOT answering") if at else "not checked yet"
    # The Forge engine likewise (optional, off unless MTG_FORGE_URL is set).
    reports = getattr(state, "reports", None)
    forge = getattr(reports, "forge", None) if reports is not None else None
    if forge is None:
        out["forge"] = "not configured"
    else:
        at, ok = forge.last_health
        out["forge"] = ("answering" if ok else "NOT answering") if at else "not checked yet"
    copy_dir = getattr(settings, "backup_copy_dir", None)
    out["backup_copies_enabled"] = copy_dir is not None
    if copy_dir is not None:
        out["last_backup_copy_error"] = backup_mod.last_run.get("copy_error")
        newest = backup_mod.newest_backup(copy_dir) if copy_dir.is_dir() else None
        out["last_backup_copy_at"] = int(newest[1]) if newest else None
    # The hourly removed-member clean-up (idp_sweep.py): off and why, waiting, or its last round.
    sweep = getattr(state, "sweep", None)
    out["member_cleanup"] = sweep.status() if sweep is not None else {"state": "not started"}
    # The Archidekt request limits in force and the counters since the gateway started.
    client = getattr(state, "archidekt", None)
    limits = getattr(client, "limits", None)
    if callable(limits):
        out["archidekt"] = {
            **limits(),
            "calls_per_member_per_10_min": getattr(settings, "archidekt_calls_per_10_min", None),
        }
    return out


# -- HTML -------------------------------------------------------------------------
USERS_PURPOSE = "Everyone who has signed in. Disabling blocks this gateway only."
MODELS_PURPOSE = "Each model is used only in its owner's simulations. Only admins see who added it."
CURRENT = " aria-current='page'"
NO_NAME = activity_view.NO_NAME  # a member with neither a username nor an email on record
PER_PAGE = ui.PER_PAGE
# the member's first-sign-in and last-seen windows the Users page filters by
SEEN = (
    ("", "Seen any time"),
    ("24h", "Seen in 24 hours"),
    ("7d", "Seen in 7 days"),
    ("30d", "Seen in 30 days"),
    ("not30d", "Not seen in 30 days"),
)
SEEN_SECONDS = {"24h": 86400, "7d": 7 * 86400, "30d": 30 * 86400, "not30d": 30 * 86400}
USER_CHIPS = (
    ("all", "All"),
    ("active", "Active"),
    ("disabled", "Disabled"),
    ("linked", "Linked"),
    ("notlinked", "Not linked"),
    ("admins", "Admins"),
)
METRIC_DAYS = (7, 30, 90)
# the Metrics table's short kind word (the chip and widget word is tool_labels.METRIC_KINDS)
KIND_WORDS = {
    "tool": "Tool call",
    "error": "Error",
    "api": "API call",
    "admin": "Admin action",
    "card": "Chat card load",
}
KIND_ICONS = {"tool": "activity", "error": "alert", "api": "link", "admin": "shield", "card": "image"}

ADMIN_CSS = """
/* the admin pages (admin.py): dashboards in the 1200 px width, the tab bar in the same place on
   every tab, the System rows lined up as on Settings */
.adm .adm-nav{margin:0 0 1rem}
.adm .notice{margin:0 0 1rem}
.adm .ui-widgets .wdg .det{overflow-wrap:anywhere;min-width:0}
/* the number and its unit ("0.7.37 running") may part at 200% text: two flex items, not one word */
.adm .wdg .big{display:flex;flex-wrap:wrap;align-items:baseline;column-gap:.35rem}
.adm .wdg .big small{margin-left:0}
.adm .wdg .det .tx{flex:1 1 100%}
.adm .spark{flex:1 1 100%;display:flex;align-items:flex-end;gap:3px;height:36px;margin:.1rem 0 0}
.adm .spark i{flex:1;display:block;min-height:2px;background:var(--text-muted);border-radius:2px 2px 0 0}
.adm .spark i:last-child{background:var(--text)}
.adm .nobody,.adm .kebab{color:var(--text-muted)}
.adm .ui-table .tr.th > span:last-child{text-align:right}
.adm .ui-table .act{justify-self:end;margin:-.5rem 0}
.adm .ui-table .act .kebab{margin:0;width:44px;height:44px}
/* a widget's button names a member: a long name wraps inside the button instead of pushing the page */
.adm .wdg .go .btn,.adm .wdg .go a.btn-plain{white-space:normal;overflow-wrap:anywhere;height:auto;
  text-align:center}
.adm .ui-table .title .labs{display:inline-flex;flex-wrap:wrap;gap:.3rem;align-items:center}
.adm .ui-table .title .sub .ui-lbl,.adm .ui-table .title .sm-only .ui-lbl{height:22px;font-size:.78rem}
.adm .ui-table .sm-only{display:none}
.adm .ui-table .sub{font-weight:400;color:var(--text-muted);font-size:.86rem}
.adm .ui-table .tr > span{min-width:0;overflow-wrap:anywhere}
@media (max-width:599.98px){
  /* below 600 px the one-line summary (.sm-only) stands in for the folded line (.sub) */
  .adm .ui-table .sub{display:none}
  .adm .ui-table .sm-only{display:flex;flex-wrap:wrap;gap:.3rem .5rem;align-items:center;flex-basis:100%;
    margin-top:.25rem;color:var(--text-muted);font-size:.86rem;font-weight:400}
}
/* Overview: six widgets three a row, then System left, Updates and Activity by day right */
@media (min-width:900px){.ao .ui-widgets{grid-template-columns:repeat(3,minmax(0,1fr))}}
.ao .ao-cols{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:1rem;align-items:start}
.ao .ao-cols > div > .ui-card:last-child{margin-bottom:0}
.ao .ui-row{grid-template-columns:minmax(0,min(12rem,40%)) minmax(0,1fr);align-items:start}
.ao .ui-row .ctl .ui-lbl{white-space:normal;height:auto;min-height:22px;padding-top:2px;padding-bottom:2px}
.ao .ui-row .ctl{overflow-wrap:anywhere;font-size:.95rem}
.ao .ui-row .lab .muted{font-weight:400;margin-left:.35rem}
.ao .ui-card .notice{margin:.75rem 0 0}
.ao .ui-card details{margin:.5rem 0 0}
.ao .ui-card details > summary{display:flex;align-items:center;gap:.4rem;min-height:44px;cursor:pointer;
  font-weight:700;list-style:none}
.ao .ui-card details > summary::-webkit-details-marker{display:none}
.ao .ui-card details > summary svg.i{width:18px;height:18px;flex:none}
.ao .ui-card details[open] > summary svg.i{transform:rotate(180deg)}
.ao .ui-card details p{font-size:.93rem}
@media (max-width:899.98px){
  .ao .ao-cols{grid-template-columns:minmax(0,1fr)}
  .ao .ao-cols > div > .ui-card:last-child{margin-bottom:1rem}
}
@media (max-width:599.98px){.ao .ui-row{grid-template-columns:1fr}}
/* Users: a member's row opens their side panel (static/admin.js makes the whole row the target) */
.au .ui-table .tr.mem{cursor:pointer}
.au .ui-table .tr.mem.on{background:var(--orange-tint)}
.au .ui-table .tr.mem .title .t{font-weight:700}
.au .ui-table .labs,.au .ui-table .apps{display:flex;flex-wrap:wrap;gap:.3rem;align-items:center}
@media (max-width:899.98px){.au .ui-table .md-hide{display:none}}
@media (min-width:1100px){.au .with-panel .ui-table .md-hide{display:none}}
.au .arch .u{font-size:.86rem;color:var(--text-muted)}
.au .ui-drawer .ui-actions .btn,.au .ui-drawer .ui-actions button{margin:0}
.au .au-del{border-top:1px solid var(--border-soft);margin-top:.75rem;padding-top:.75rem}
.au .ui-drawer .note{color:var(--text-muted);font-size:.9rem}
.au .ui-drawer .ui-meta code{font-size:.8rem;overflow-wrap:anywhere}
@media (min-width:1100px){.au .with-panel .ui-table .tr.mem .arch{display:none}}
/* Metrics: one table, the share bar, a row opens its details in place */
.am .ui-table .tr.mrow{cursor:pointer}
.am .ui-table .tr.mrow:hover{background:var(--surface-2)}
.am .ui-table .tr.mrow .title .t{display:inline-block;text-decoration:none;color:var(--text);font-weight:700}
.am .am-bar{display:inline-block;width:6rem;max-width:40vw;height:8px;border-radius:4px;
  background:var(--surface-3,var(--surface-2));vertical-align:middle;overflow:hidden}
.am .am-bar i{display:block;height:100%;background:var(--orange)}
.am .am-p{color:var(--text-muted);font-size:.86rem;margin-left:.4rem}
.am .am-n{font-weight:700;font-variant-numeric:tabular-nums}
.am .more-body{grid-column:1 / -1;padding:.6rem 0 .25rem;margin-top:.45rem;
  border-top:1px solid var(--border-soft)}
.am .more-body[hidden]{display:none}
.am .more-body .ui-meta{margin:0;font-size:.9rem}
.am .ui-tools .f{flex:0 0 auto;width:10rem}
@media (max-width:599.98px){.am .ui-tools .f{width:8.8rem}}
/* Card models */
.amo .ui-tools .f{flex:0 0 auto;width:13rem}
@media (max-width:599.98px){.amo .ui-tools .f{width:9.5rem}}
.amo .ui-table .tr.model .title .t{font-weight:700}
"""
ADMIN_HEAD = f"<style>{ADMIN_CSS}</style><script src='/static/admin.js' defer></script>"


def _nav(active: str) -> str:
    """The admin pages' own tabs (all six pages): even links in full rows, the current one tinted
    and marked aria-current, not a second orange button (one orange main button per page)."""
    links = (("/admin", "Overview"), ("/admin/users", "Users"), ("/admin/activity", "Activity"))
    links += (
        ("/admin/metrics", "Metrics"),
        ("/admin/settings", "Settings"),
        ("/admin/models", "Card models"),
    )
    return (
        "<nav class='adm-nav' aria-label='Admin'>"
        + "".join(f"<a href='{href}'{CURRENT if href == active else ''}>{label}</a>" for href, label in links)
        + "</nav>"
    )


def _head(title: str, purpose: str, active: str) -> str:
    """Every admin tab in the same order: the title, its purpose line, then the tab bar."""
    return ui.page_head(title, purpose) + _nav(active)


def _href(path: str, **params: Any) -> str:
    """``path`` with the parameters that have a value (0, "" and None are left out)."""
    out = {k: v for k, v in params.items() if v not in (None, "", 0, "all")}
    return path + ("?" + urlencode(out) if out else "")


def _saved_changes(state: Any, sub: str, after: int) -> list[tuple[str, Any, Any]]:
    """The settings ``sub`` changed in audit rows after id ``after`` (the save that redirected
    here), oldest first, for the notice after a save; a key changed twice shows its first value
    and its last."""
    rows = [
        r
        for r in state.db.audit_recent(200, sub=sub)
        if r["event"] == "setting_changed" and r["id"] > after and isinstance(r.get("detail"), dict)
    ]
    out: dict[str, tuple[Any, Any]] = {}
    for r in reversed(rows):
        key = r["detail"].get("key")
        if key not in MANAGED_SETTINGS:
            continue
        before = out[key][0] if key in out else r["detail"].get("previous")
        out[key] = (before, r["detail"].get("value"))
    return [(k, b, a) for k, (b, a) in out.items() if b != a]


def _notice(ok: str | None, err: str | None) -> str:
    if ok in OK_MESSAGES:
        return ui.notice(OK_MESSAGES[ok], "ok", role="status")
    if err in ERR_MESSAGES:
        return ui.notice(ERR_MESSAGES[err], "error", role="alert")
    return ""


def _err(msg: str) -> str:
    return ui.notice(msg, "error", role="alert")


def _member_text(u: Mapping[str, Any]) -> str:
    """A member's name as plain text: username, else email, else "a member with no name" (never
    the raw subject id; that shows only in the member's details)."""
    return str(u.get("preferred_username") or u.get("email") or "") or NO_NAME


def _member_html(u: Mapping[str, Any]) -> str:
    name = str(u.get("preferred_username") or u.get("email") or "")
    return html.escape(name) if name else f"<i class='nobody'>{NO_NAME}</i>"


def _who(u: dict[str, Any]) -> str:
    return _member_html(u)


class _Html(str):
    """A value that is already HTML (escaped text plus markup such as a <time>): _row keeps it."""


def _row(label: str, value: Any, *, note: str = "") -> str:
    """One lined-up row of a panel (``.ui-row``, as on Settings): the label, then the value."""
    shown = value if isinstance(value, _Html) else html.escape(str(value))
    extra = f" <span class='muted'>{html.escape(note)}</span>" if note else ""
    lab = f"<p class='lab'>{html.escape(label)}{extra}</p>"
    return f"<div class='ui-row'>{lab}<div class='ctl'>{shown}</div></div>"


def _spark(values: list[int], *, label: str) -> str:
    """A bar chart of counts per day that fills its row (no script, no external request)."""
    if not values:
        return ""
    top = max(values) or 1
    bars = "".join(f"<i style='height:{max(4, round(v * 100 / top))}%'></i>" for v in values)
    said = ", ".join(str(v) for v in values)
    text = f"{html.escape(label)} per day, oldest first: {said}"
    return f"<span class='spark' role='img' aria-label='{text}'>{bars}</span>"


def _mode_name(mode: object) -> str:
    """An approval mode as the Guide names it, with the word its setting takes: "Semi-auto (semi)"."""
    from .modes import MODE_NAMES

    return f"{MODE_NAMES.get(str(mode), str(mode))} ({mode})"


def _service_label(status: str) -> _Html:
    """A service's state as a label: Answering (green), Not answering (red), the rest grey."""
    if status == "answering":
        return _Html(ui.label("Answering", "green", "check"))
    if status == "NOT answering":
        return _Html(ui.label("Not answering", "red", "alert"))
    return _Html(ui.label(status.capitalize(), "grey", "clock" if status == "not checked yet" else "x"))


def _proposal_labels(props: Mapping[str, int]) -> str:
    """Every proposal state with a count, as the status labels used everywhere else (Waiting,
    Applied, Failed and the rest), in the standard's order; a state the standard doesn't name
    keeps its stored word."""
    order = {k: i for i, k in enumerate(ui.STATUS_KEYS)}
    items = sorted(props.items(), key=lambda kv: (order.get(ui.ALIASES.get(kv[0], kv[0]), 99), kv[0]))
    out = []
    for state, n in items:
        if not n:
            continue
        key = ui.ALIASES.get(state, state)
        if key in ui.LABELS:
            out.append(ui.label_for(key, word=f"{ui.LABELS[key].word} {n}"))
        else:
            out.append(ui.label(f"{state} {n}", "grey", "x"))
    return "".join(out)


def _overview_body(state: Any, now: float | None = None) -> str:
    """/admin: six widgets (Members, Proposals, Tool calls, Errors, System, Updates), then the
    System rows on the left and Updates with Activity by day on the right."""
    now = time.time() if now is None else now
    d = overview_data(state)
    props = d["proposals"]
    sysd = d["system"]
    series = d["series"]
    tool_days = list(series.get("tool", {}).values())
    error_days = list(series.get("error", {}).values())
    # the System widget: the services that are set up, and whether each answers
    services = [sysd["mystic_forge"], sysd["forge"]]
    counted = [s for s in services if s != "not configured"]
    working = sum(1 for s in counted if s == "answering")
    cleanup = sysd["member_cleanup"].get("state")
    if cleanup != "off":
        counted.append("cleanup")
        working += cleanup in ("ok", "waiting")
    backup_failed = bool(sysd["last_backup_error"] or sysd.get("last_backup_copy_error"))
    system_bad = backup_failed or working < len(counted)
    if not sysd["backups_enabled"]:
        backup_line = "Nightly backup off"
    elif sysd["last_backup_at"]:
        backup_line = f"Nightly backup {ui.when(sysd['last_backup_at'], now=now)}"
    else:
        backup_line = "No nightly backup yet"
    if not sysd["backup_copies_enabled"]:
        copies_line = "copies off"
    elif sysd.get("last_backup_copy_at"):
        copies_line = f"copy {ui.when(sysd['last_backup_copy_at'], now=now)}"
    else:
        copies_line = "no copy yet"
    u = sysd["updates"]
    found = update_check.newer()
    widgets = [
        ui.widget(
            "Members",
            "user",
            int(d["users"]),
            unit="signed in",
            detail=f"{d['disabled']} disabled · {d['linked']} linked to Archidekt",
            actions=[ui.button_link("Users", "/admin/users")],
        ),
        ui.widget(
            f"Proposals, {d['days']} days",
            "proposals",
            sum(props.values()),
            detail_html=_proposal_labels(props),
            actions=[ui.button_link("Show in Activity", "/admin/activity?kind=proposals&time=30d")],
        ),
        ui.widget(
            f"Tool calls, {d['days']} days",
            "activity",
            int(d["tool_calls"]),
            detail_html=f"<span class='tx'>{tool_days[-1] if tool_days else 0} today</span>"
            + _spark(tool_days, label="Tool calls"),
            actions=[ui.button_link("Metrics", "/admin/metrics")],
        ),
        ui.widget(
            f"Errors, {d['days']} days",
            "alert",
            int(d["errors"]),
            detail=f"{error_days[-1] if error_days else 0} today",
            actions=[ui.button_link("Show errors", "/admin/activity?kind=failed")],
            tone="alert" if d["errors"] else "",
        ),
        ui.widget(
            "System",
            "box",
            working,
            unit=f"of {len(counted)} services working" if counted else "services set up",
            detail_html=f"{backup_line} · {copies_line}",
            actions=[ui.button_link("System details", "#system")],
            tone="alert" if system_bad else "",
        ),
        ui.widget(
            "Updates",
            "download",
            found["version"] if found else str(u["running"]),
            unit="available" if found else "running",
            detail=f"This gateway runs {u['running']}" if found else _update_state_text(u),
            actions=[ui.button_link("How to update", "#updates")],
            tone="accent" if found else "",
        ),
    ]
    # the System panel: every row as today, the same wording
    if not sysd["backups_enabled"]:
        backup: Any = "off (MTG_BACKUP_DIR not set)"
    elif sysd["last_backup_at"]:
        backup = _Html(ui.when(sysd["last_backup_at"], now=now))
    else:
        backup = "none yet"
    if not sysd["backup_copies_enabled"]:
        copies: Any = "off (MTG_BACKUP_COPY_DIR not set)"
    elif sysd["last_backup_copy_at"]:
        copies = _Html(ui.when(sysd["last_backup_copy_at"], now=now))
    else:
        copies = "none yet"
    size = sysd["database_bytes"]
    system = (
        "<section class='ui-card' id='system' aria-labelledby='system-h'><h2 id='system-h'>System</h2>"
        + _row("Version", sysd["version"])
        + _row("Database schema", sysd["schema_version"])
        + _row("Database size", f"{size / 1048576:.1f} MB" if size is not None else "in memory")
        + _row("Newest backup", backup)
        + _row("Newest backup copy", copies)
        + _row("Newest before-upgrade copy", _before_upgrade_text(sysd.get("before_upgrade"), now=now))
        + _row("Research service (Mystic Forge)", _service_label(sysd["mystic_forge"]))
        + _row("Simulation engine (Forge)", _service_label(sysd["forge"]))
        + _row("Removed-member clean-up", _cleanup_text(sysd["member_cleanup"], now=now))
        + _archidekt_stats(sysd.get("archidekt"))
        # the live settings; each by its name, with the setting's own word
        + _row("Default approval mode", _mode_name(state.decks.live.get("approval_mode_default")))
        + _row("Highest approval mode allowed", _mode_name(state.decks.live.get("approval_mode_max")))
        + (
            _err(f"The last nightly backup failed ({sysd['last_backup_error']}). Check the gateway's log.")
            if sysd["last_backup_error"]
            else ""
        )
        + (
            _err(f"The last backup copy failed ({sysd['last_backup_copy_error']}). Check the copy folder.")
            if sysd.get("last_backup_copy_error")
            else ""
        )
        + _cleanup_warning(sysd["member_cleanup"])
        + _went_back_notice(sysd.get("before_upgrade"))
        + "</section>"
    )
    rows = []
    for kind, by_day in series.items():
        values = list(by_day.values())
        name = tool_labels.METRIC_KINDS.get(kind, kind)
        rows.append(_row(name, _Html(_spark(values, label=name)), note=f"{sum(values):,}"))
    by_day_card = (
        "<section class='ui-card' aria-labelledby='byday-h'><h2 id='byday-h'>Activity by day</h2>"
        f"{''.join(rows)}<p class='muted small' style='margin:.5rem 0 0'>Per UTC day.</p></section>"
    )
    return (
        "<div class='adm ao'>"
        + _head("Admin: overview", "Members, the last 30 days and the system.", "/admin")
        + ui.widgets(widgets)
        + f"<div class='ao-cols'>{system}<div>{_updates_card(u, now=now)}{by_day_card}</div></div></div>"
    )


def _update_state_text(u: dict[str, Any]) -> str:
    if u["check"] == "off":
        return "Update check off"
    if u.get("error"):
        return "The last update check failed"
    if u.get("latest"):
        return "Up to date"
    return "Not checked yet"


def _before_upgrade_text(b: dict[str, Any] | None, *, now: float | None = None) -> Any:
    """The System list's line for the before-upgrade folder."""
    if not b:
        return "none (the database is in memory)"
    if not b.get("newest"):
        return "none yet (one is saved the first time a new version upgrades the database)"
    when = ui.when(b["newest_at"], now=now) if b.get("newest_at") else ""
    return _Html(f"{html.escape(str(b['newest']))} {when}, in {html.escape(str(b['folder']))}")


def _went_back_notice(b: dict[str, Any] | None) -> str:
    """A notice after this start put back the copy saved before an upgrade (an older version was
    started on a database a newer one had upgraded)."""
    event = (b or {}).get("event") or {}
    if event.get("kind") == "went-forward":
        text = (
            "This version started on a set-aside copy that was put back by hand, so it ended every "
            "sign-in: everyone signs in once more. What happened while the older version ran is not "
            "in this database: look through the Users page and disable, unlink or delete again "
            "anyone you disabled, unlinked or deleted in that time (docs/UPGRADING.md, “Going "
            "forward again with the set-aside database”)."
        )
        return f"<div class='notice warn'>{html.escape(text)}</div>"
    if event.get("kind") != "went-back":
        return ""
    text = (
        "This version started on a database that a newer version had upgraded, so it put back the "
        f"before-upgrade copy {event['file']}. Everyone has to sign in once more. Members disabled, "
        "Archidekt sessions deleted and members' data deleted since that upgrade stay so; other "
        f"changes made since then are only in the set-aside copy {event['aside']}, in {b['folder']}. "
        "Anyone you enabled again since then is disabled again: enable them on the Users page "
        "(docs/UPGRADING.md, “Going back to an older version”)."
    )
    return ui.notice(text, "warn")


def _cleanup_warning(c: dict[str, Any]) -> str:
    """A notice when the removed-member clean-up was set up but its token can't be read, as with a
    stack file that doesn't attach its secret: the one line in
    the System list and one start-up warning are easy to miss."""
    if c.get("state") != "off" or c.get("cause") != "token-unreadable":
        return ""
    text = (
        "The removed-member clean-up is off: its Authentik API token can't be read. Check that your "
        "stack file or docker-compose.yml is the current one, which attaches the "
        "mtg_authentik_api_token secret to the gateway, then redeploy."
    )
    return ui.notice(text, "warn")


def _updates_card(u: dict[str, Any], *, now: float | None = None) -> str:
    """Whether a newer gateway release is out (update_check.py) and how to move to it."""
    found = update_check.newer()
    if u["check"] == "off":
        state: Any = "off (MTG_UPDATE_CHECK=false)"
    elif u.get("error"):
        state = f"last check failed ({u['error']}), trying again within the hour"
    elif u.get("latest"):
        state = _Html(
            f"newest release {html.escape(str(u['latest']))}, checked {ui.when(u['checked_at'], now=now)}"
        )
    else:
        state = "not checked yet (the first check runs a minute after start)"
    head = (
        ui.notice(
            f"Version {html.escape(found['version'])} is available; this gateway "
            f"runs {html.escape(u['running'])}. Read <a href='{html.escape(found['url'])}' rel='noopener'>"
            "its "
            "release notes</a> first: they say when a release needs a settings change.",
            "warn",
            html=True,
        )
        if found
        else ""
    )
    tag = html.escape(found["version"]) if found else "&lt;new version&gt;"
    steps = (
        f"<details id='how-to-update'><summary>{icon('chev')}How to update</summary>"
        "<p>The gateway never updates itself. An update is a fresh install on top of your data "
        "(docs/UPGRADING.md has every step): take the newest stack file and the newest example "
        "variables file, copy your own values into the example, and deploy. Your data, backups and "
        "Docker secrets stay as they are. There is no backup to take first: before the new version "
        "upgrades the database, the gateway saves a copy of it in the before-upgrade folder, inside "
        "your backup folder.</p>"
        "<p><strong>Portainer (Swarm stack)</strong>: copy your values into the newest "
        f"<code>deploy/stack.env.example</code> (with <code>MTG_TAG</code> at <code>{tag}</code>, or "
        "<code>latest</code>), and create any secret docs/DEPLOY.md step 4 lists that you don't have "
        "yet. Then Stacks, your stack, Editor: replace the text with the newest "
        "<code>deploy/portainer-stack.yml</code>, unchanged (with the Repository build method, the "
        "update brings it), and the environment variables (Advanced mode) with your new file. Then "
        "<strong>Update the stack</strong> (if your stack uses the Repository build method, the "
        "button is called <strong>Pull and redeploy</strong>); in the box that asks “Are you "
        "sure?”, make sure <strong>Re-pull image and redeploy</strong> is on, then click "
        "<strong>Update</strong>.</p>"
        "<p><strong>Swarm without Portainer</strong>: <code>git pull</code> in your copy of the "
        "repository, copy your values into a new copy of <code>deploy/stack.env.example</code>, "
        "create any missing secret (docs/DEPLOY.md step 4), load the variables into a new shell as "
        "in docs/DEPLOY.md, then run <code>docker stack deploy -c deploy/portainer-stack.yml "
        "mtg</code> again (your stack's name in place of <code>mtg</code>).</p>"
        "<p><strong>docker compose</strong>: in the folder with <code>docker-compose.yml</code>, get "
        "the newest <code>docker-compose.yml</code>, <code>make-secrets.sh</code> and "
        "<code>.env.example</code> (docs/UPGRADING.md has the commands), copy your values from "
        f"<code>.env</code> into the example and save it as <code>.env</code> (<code>MTG_TAG={tag}</code> "
        "or <code>latest</code>), run <code>./make-secrets.sh</code>, then "
        "<code>docker compose pull</code> and <code>docker compose up -d</code>.</p>"
        "<p class='muted small'>Once the gateway is updated, members' Android apps offer the matching "
        "app update from this gateway on their own.</p></details>"
    )
    return (
        "<section class='ui-card' id='updates' aria-labelledby='updates-h'><h2 id='updates-h'>Updates</h2>"
        + _row("This gateway", u["running"])
        + _row("Update check", state)
        + head
        + steps
        + "</section>"
    )


def _archidekt_stats(a: dict[str, Any] | None) -> str:
    if not a:
        return ""
    limits = (
        f"{a['max_per_minute']} a minute for everyone, at least {a['min_interval_seconds']:g} s apart; "
        f"{a['calls_per_member_per_10_min']} per member every 10 minutes"
    )
    counts = (
        f"{a['requests']} sent, {a['cache_hits']} answered from cache, {a['retries']} retried, "
        f"{a['rate_limited']} slowed down by Archidekt"
    )
    return _row("Archidekt request limits", limits) + _row("Archidekt requests since start", counts)


def _cleanup_text(c: dict[str, Any], *, now: float | None = None) -> str:
    """The removed-member clean-up's state in one line that ends with what to do next
    (idp_sweep.AuthentikSweep.status). docs/IDP-AUTHENTIK.md section 12 explains each one."""
    st = c.get("state")
    setup = idp_sweep.SETUP
    if st == "off":
        cause = c.get("cause")
        if cause == "token-unreadable":
            why = c["reason"].removeprefix("removed-member clean-up is off: ").split("; it is optional")[0]
            if c.get("missing"):
                return (
                    f"Off: {why}. The likely cause: the mtg_authentik_api_token secret isn't attached "
                    "to the gateway, as with a stack file from before 0.7.37. Use the current stack "
                    f"file, then redeploy ({setup}, steps 6 and 7)."
                )
            return (
                f"Off: {why}. Check the mtg_authentik_api_token Docker secret, then redeploy "
                f"({setup}, steps 6 and 7)."
            )
        if cause == "no-group":
            return "Off: it needs MTG_REQUIRED_GROUP. Set it in the stack and redeploy."
        return (
            "Off. This optional hourly check deletes the saved Archidekt sign-in of people who no "
            f"longer have access. To turn it on, follow {setup}."
        )
    if st == "waiting":
        return (
            f"On. The first check runs {c['first_after_seconds'] // 60} minutes after the gateway "
            "starts; reload this page then."
        )
    if st == "ok":
        removed = int(c["removed"])
        what = (
            f"deleted the saved Archidekt sign-in of {plural(removed, 'person', 'people')} who lost access"
            if removed
            else "no one lost access"
        )
        return _Html(
            f"Working. Last check {ui.when(c['at'], now=now)}: {plural(int(c['allowed']), 'member')} with "
            f"access; {what}. Nothing for you to do."
        )
    if st == "failed":
        err = str(c["error"])
        return _Html(
            f"Not working. The last check {ui.when(c['at'], now=now)} failed and deleted nothing: "
            f"{html.escape(err)}. To fix: {html.escape(idp_sweep.next_step(err))}"
        )
    return "Not started yet. Reload this page to see its first result."


def users_data(state: Any) -> list[dict[str, Any]]:
    clients = state.db.all_user_clients()
    return [
        {**u, "clients": clients.get(u["sub"], []), "disabled": bool(u["disabled_at"])}
        for u in state.db.list_users()
    ]


# -- Users ----------------------------------------------------------------------------------------


def _status_labels(u: Mapping[str, Any], admin_sub: str, *, is_admin: bool) -> str:
    out = (
        f"<span class='ui-lbl red'>{icon('ban')}Disabled</span>"
        if u["disabled"]
        else ui.label("Active", "green", "check")
    )
    if is_admin:
        out += ui.label("Admin", "blue", "shield")
    if u["sub"] == admin_sub:
        out += ui.label("You", "grey", "user")
    return out


def _arch_label(u: Mapping[str, Any], *, name: bool = True) -> str:
    if u["archidekt_username"]:
        who = f" <span class='u'>{html.escape(u['archidekt_username'])}</span>" if name else ""
        return f"<span class='arch'>{ui.label('Linked', 'blue', 'link')}{who}</span>"
    return f"<span class='arch'>{ui.label('Not linked', 'grey', 'x')}</span>"


def _apps_html(u: Mapping[str, Any]) -> str:
    if not u["clients"]:
        return "<span class='muted'>none</span>"
    return "".join(ui.app_chip(c["name"], unnamed=True, client_id=c["client_id"]) for c in u["clients"])


def _seen_at(u: Mapping[str, Any]) -> int:
    return int(u.get("last_seen_at") or u["last_login_at"] or 0)


def _users_body(
    state: Any,
    admin_sub: str,
    csrf: str | None,
    params: Mapping[str, str],
    *,
    notice: str = "",
    now: float | None = None,
) -> str:
    """/admin/users: five widgets, then every member as a list with chips, search, the App and
    Last seen filters and 25 a page; a member opens in a side panel (their own page on a narrow
    screen) with every detail and action; the ⋮ menu holds the actions too (static/admin.js)."""
    now = time.time() if now is None else now
    path = "/admin/users"
    users = users_data(state)
    group = state.settings.admin_group
    admins = {u["sub"] for u in users if group and group in u["groups"]}
    chip = params.get("chip", "all")
    if chip not in {c for c, _t in USER_CHIPS}:
        chip = "all"
    app = str(params.get("app", ""))[:120]
    seen = params.get("seen", "") if params.get("seen", "") in SEEN_SECONDS else ""
    q = " ".join(str(params.get("q", "")).split())[:100]
    try:
        offset = max(0, int(params.get("offset", "0") or 0))
    except ValueError:
        offset = 0
    opened = str(params.get("open", ""))
    box = params.get("box", "")
    if not users:
        return (
            "<div class='adm au'>"
            + _head("Admin: users", USERS_PURPOSE, path)
            + notice
            + "<div class='ui-card'><p>Nobody has signed in yet.</p></div></div>"
        )

    # the widgets
    week = [u for u in users if _seen_at(u) >= now - 7 * 86400]
    newest = max(users, key=lambda u: int(u["first_login_at"] or 0))
    recent = max(users, key=_seen_at)
    disabled = [u for u in users if u["disabled"]]
    linked = [u for u in users if u["archidekt_username"]]
    app_members: dict[str, int] = {}
    unnamed_members = 0
    connections = 0
    for u in users:
        connections += len(u["clients"])
        names = {c["name"] for c in u["clients"]}
        for n in names:
            if n:
                app_members[n] = app_members.get(n, 0) + 1
            else:
                unnamed_members += 1
    app_bits = [
        f"{n} use {name}" if n != 1 else f"1 uses {name}"
        for name, n in sorted(app_members.items(), key=lambda kv: (-kv[1], kv[0].casefold()))
    ]
    if unnamed_members:
        app_bits.append(
            f"{unnamed_members} use an unnamed app" if unnamed_members != 1 else "1 uses an unnamed app"
        )
    if app_bits:
        app_bits[0] = app_bits[0].replace(" use ", " members use ", 1).replace("1 uses ", "1 member uses ", 1)
    top_app = max(app_members.items(), key=lambda kv: kv[1])[0] if app_members else ""
    newest_disabled = max(disabled, key=lambda u: int(u["disabled_at"] or 0)) if disabled else None
    widgets = [
        ui.widget(
            "Members",
            "user",
            len(users),
            unit="signed in",
            detail_html=f"Newest: {_member_html(newest)}, {ui.when(newest['first_login_at'], now=now)}",
            actions=[ui.button_link("Show all", path)],
        ),
        ui.widget(
            "Active in 7 days",
            "activity",
            len(week),
            unit=f"of {len(users)}",
            detail_html=f"Most recent: {_member_html(recent)}, {ui.when(_seen_at(recent), now=now)}"
            if week
            else "",
            actions=[ui.button_link("Activity", "/admin/activity?time=7d")],
        ),
        ui.widget(
            "Disabled",
            "x",
            len(disabled),
            detail_html=(
                f"{_member_html(newest_disabled)}, since {ui.when(newest_disabled['disabled_at'], now=now)}"
                if newest_disabled
                else "No account is disabled"
            ),
            actions=[ui.button_link("Show them", _href(path, chip="disabled"))],
            tone="alert" if disabled else "",
        ),
        ui.widget(
            "Linked to Archidekt",
            "link",
            len(linked),
            unit=f"of {len(users)}",
            detail=f"{len(users) - len(linked)} not linked",
            actions=[ui.button_link("Not linked", _href(path, chip="notlinked"))],
        ),
        ui.widget(
            "Connected apps",
            "layers",
            connections,
            unit="connections",
            detail=", ".join(app_bits) if app_bits else "No app is connected",
            actions=[ui.button_link(f"{top_app} only", _href(path, app=top_app))] if top_app else (),
            wide_sm=True,
        ),
    ]

    # the list: the search, app and seen filters, then the chips counted over that
    def base_ok(u: Mapping[str, Any]) -> bool:
        if q:
            low = q.casefold()
            hay = " ".join(str(u.get(k) or "") for k in ("preferred_username", "email", "name")).casefold()
            if low not in hay:
                return False
        if app == "none":
            if u["clients"]:
                return False
        elif app == "unnamed":
            if not any(not c["name"] for c in u["clients"]):
                return False
        elif app and not any(c["name"] == app for c in u["clients"]):
            return False
        if seen == "not30d":
            return _seen_at(u) < now - SEEN_SECONDS[seen]
        if seen:
            return _seen_at(u) >= now - SEEN_SECONDS[seen]
        return True

    chip_ok = {
        "all": lambda u: True,
        "active": lambda u: not u["disabled"],
        "disabled": lambda u: u["disabled"],
        "linked": lambda u: bool(u["archidekt_username"]),
        "notlinked": lambda u: not u["archidekt_username"],
        "admins": lambda u: u["sub"] in admins,
    }
    base = [u for u in users if base_ok(u)]
    chips = [
        ui.Chip(
            text,
            sum(1 for u in base if chip_ok[c](u)),
            _href(path, chip=c, app=app, seen=seen, q=q),
            current=chip == c,
        )
        for c, text in USER_CHIPS
    ]
    shown = [u for u in base if chip_ok[chip](u)]
    total = len(shown)
    offset = min(offset, max(0, (total - 1) // PER_PAGE * PER_PAGE))
    page_users = shown[offset : offset + PER_PAGE]
    app_names = sorted({c["name"] for u in users for c in u["clients"] if c["name"]}, key=str.casefold)
    app_opts = [("", "Any app")] + [(n, n) for n in app_names]
    if any(not c["name"] for u in users for c in u["clients"]):
        app_opts.append(("unnamed", ui.UNNAMED_APP))
    app_opts.append(("none", "No app"))
    tools = ui.toolbar(
        path,
        search_label="Search members",
        placeholder="Search name, email or username",
        q=q,
        filters=[
            ui.filter_select("app", "App", app_opts, app),
            ui.filter_select("seen", "Last seen", SEEN, seen),
        ],
        keep={"chip": chip if chip != "all" else ""},
    )
    list_href = _href(path, chip=chip, app=app, seen=seen, q=q, offset=offset)

    def open_href(sub: str, **more: Any) -> str:
        return _href(path, chip=chip, app=app, seen=seen, q=q, offset=offset, open=sub, **more)

    rows = []
    for u in page_users:
        sub = u["sub"]
        status = _status_labels(u, admin_sub, is_admin=sub in admins)
        when = ui.when(_seen_at(u), now=now)
        email = (
            f"<span class='deck'>{html.escape(u['email'])}</span>"
            if u.get("email") and u.get("preferred_username")
            else ""
        )
        rows.append(
            f"<div class='tr mem{' on' if sub == opened else ''}' data-open='{html.escape(open_href(sub))}' "
            f"data-name='{html.escape(_member_text(u))}' data-sub='{html.escape(sub)}' "
            f"data-act='/admin/users/{quote(sub, safe='')}'"
            f"{' data-disabled' if u['disabled'] else ''}{' data-you' if sub == admin_sub else ''}"
            f"{' data-linked' if u['archidekt_username'] else ''} "
            f"data-box-disable='{html.escape(open_href(sub, box='disable'))}' "
            f"data-box-delete='{html.escape(open_href(sub, box='delete'))}' "
            f"data-activity='/admin/activity?sub={quote(sub, safe='')}'>"
            f"<span class='title'><a class='t' href='{html.escape(open_href(sub))}'>{_member_html(u)}</a>"
            f"{email}"
            f"<span class='sub'>{status} {_arch_label(u)} {_apps_html(u)}</span>"
            f"<span class='sm-only'>{status} {_arch_label(u, name=False)} {when}</span></span>"
            f"<span class='md-hide labs'>{status}</span>"
            f"<span class='md-hide'>{_arch_label(u)}</span>"
            f"<span class='md-hide apps'>{_apps_html(u)}</span>"
            f"<span class='sm-hide'>{when}</span>"
            f"<span class='act'><a class='kebab' href='{html.escape(open_href(sub))}' "
            f"aria-label='Actions for {html.escape(_member_text(u))}' aria-haspopup='menu' title='Menu'>"
            f"{icon('more')}</a></span></div>"
        )
    if rows:
        table = (
            ui.table_open(
                "minmax(0,1.5fr) 11rem 10rem minmax(0,1fr) 6.5rem 44px",
                "minmax(0,1fr) 6.5rem 44px",
                "minmax(0,1fr) 44px",
                label="All members",
            )
            + ui.table_header(
                [
                    ui.Column("Member"),
                    ui.Column("Status", "md-hide"),
                    ui.Column("Archidekt", "md-hide"),
                    ui.Column("Apps", "md-hide"),
                    ui.Column("Last seen", "sm-hide"),
                    ui.Column("", html="<span class='sr-only'>Actions</span>"),
                ]
            )
            + "".join(rows)
            + "</div>"
        )
    else:
        table = (
            "<div class='ui-card'><p>Nobody matches these filters. "
            f"<a href='{path}'>Show everyone</a></p></div>"
        )
    newer = (
        _href(path, chip=chip, app=app, seen=seen, q=q, offset=max(0, offset - PER_PAGE))
        if offset > 0
        else None
    )
    older = (
        _href(path, chip=chip, app=app, seen=seen, q=q, offset=offset + PER_PAGE)
        if offset + PER_PAGE < total
        else None
    )
    pager = ui.pager(offset, total, newer_href=newer, older_href=older)
    list_html = (
        ui.widgets(widgets)
        + ui.list_head("All members", len(base), note="Newest sign-in first")
        + ui.chips(chips)
        + tools
        + table
        + pager
    )
    person = next((u for u in users if u["sub"] == opened), None) if opened else None
    panel = (
        _user_panel(
            person,
            admin_sub,
            csrf,
            list_href,
            open_href,
            box,
            is_admin=bool(person) and person["sub"] in admins,
            now=now,
        )
        if person
        else ""
    )
    return (
        f"<div class='adm au{' opened' if person else ''}'>"
        + _head("Admin: users", USERS_PURPOSE, path)
        + notice
        + ui.split(list_html, panel)
        + "</div>"
    )


def _user_panel(
    u: Mapping[str, Any],
    admin_sub: str,
    csrf: str | None,
    list_href: str,
    open_href: Callable[..., str],
    box: str,
    *,
    is_admin: bool,
    now: float,
) -> str:
    """One member's side panel: every detail (the subject id and the apps' client ids only here),
    the actions, and the Disable and Delete data boxes."""
    sub = u["sub"]
    you = sub == admin_sub
    name = _member_text(u)
    csrf_in = f"<input type='hidden' name='csrf' value='{html.escape(csrf or '')}'>"
    action = f"/admin/users/{quote(sub, safe='')}"
    apps = (
        "".join(
            f"<div>{ui.app_chip(c['name'], unnamed=True, client_id=c['client_id'])} "
            f"<code>{html.escape(c['client_id'])}</code></div>"
            for c in u["clients"]
        )
        or "none"
    )
    rows = [
        ("Name", html.escape(u.get("name") or "") or "<span class='muted'>none</span>"),
        ("Email", html.escape(u.get("email") or "") or "<span class='muted'>none</span>"),
        ("Subject", f"<code>{html.escape(sub)}</code>"),
        ("Groups", html.escape(", ".join(u["groups"]) or "none")),
        ("First sign-in", ui.when(u["first_login_at"], now=now)),
        ("Last seen", ui.when(_seen_at(u), now=now)),
        ("Archidekt", html.escape(u["archidekt_username"] or "not linked")),
        ("Connected apps", apps),
        ("Active tokens", str(u["active_tokens"])),
        ("Browser sessions", str(u["browser_sessions"])),
    ]
    if u["disabled"]:
        rows.append(("Disabled", ui.when(u["disabled_at"], now=now)))
    note = (
        "<p class='note'>This is you. You can't disable your own account here. To delete your data, use "
        "Delete my data on your Account page.</p>"
        if you
        else ""
    )
    buttons = ""
    if u["disabled"]:
        buttons += "<button name='action' value='enable' class='btn-plain'>Enable</button>"
    elif not you:
        buttons += ui.box_opener("Disable…", open_href(sub, box="disable"), "disable", kind="danger")
    buttons += "<button name='action' value='revoke' class='btn-plain'>Revoke tokens and sessions</button>"
    if u["archidekt_username"]:
        buttons += "<button name='action' value='unlink' class='btn-plain'>Unlink Archidekt</button>"
    buttons += ui.button_link("Activity", f"/admin/activity?sub={quote(sub, safe='')}")
    actions = f"<form method='post' action='{action}' class='ui-actions'>{csrf_in}{buttons}</form>"
    boxes = ""
    delete = ""
    if not you:
        opener = ui.box_opener("Delete data…", open_href(sub, box="delete"), "delete", kind="danger")
        delete = f"<div class='au-del'>{opener}</div>"
        boxes += ui.box(
            "disable",
            f"Disable “{name}”?",
            f"<form id='disf' method='post' action='{action}'>{csrf_in}"
            "<input type='hidden' name='action' value='disable'>"
            f"<p>“{html.escape(name)}” can't sign in or use their apps on this gateway until you enable the "
            "account again. Their tokens and sessions are revoked now. The account at the identity provider "
            "is not changed.</p></form>",
            actions=[
                ui.box_button("Cancel", cancel=True),
                ui.box_button("Disable account", kind="danger-solid", form="disf"),
            ],
            open=box == "disable" and not u["disabled"],
            close_href=open_href(sub),
        )
        boxes += ui.box(
            "delete",
            f"Delete “{name}”'s data?",
            f"<form id='delf' method='post' action='{action}'>{csrf_in}"
            "<input type='hidden' name='action' value='delete_data'>"
            f"<p>This deletes everything the gateway keeps about “{html.escape(name)}”: their settings, "
            "proposals, history, reports, card models and the stored Archidekt sign-in. "
            "Their decks on Archidekt are not "
            "touched. This can't be undone.</p>"
            "<label class='check'><input type='checkbox' name='confirm' value='yes' required> "
            "Delete everything the gateway keeps about this person</label></form>",
            actions=[
                ui.box_button("Cancel", cancel=True),
                ui.box_button("Delete data", kind="danger-solid", form="delf", attrs="data-needs-tick"),
            ],
            open=box == "delete",
            close_href=open_href(sub),
        )
    heading = _member_html(u)
    body = (
        f"<div class='pills'>{_status_labels(u, admin_sub, is_admin=is_admin)}</div>"
        + ui.meta(rows)
        + note
        + actions
        + delete
        + boxes
    )
    return ui.side_panel(heading, body, label=name, close_href=list_href, back_text="All members")


# -- Metrics ---------------------------------------------------------------------------------------


def _metrics_body(state: Any, params: Mapping[str, str]) -> str:
    """/admin/metrics: a widget per kind with its chart over the period, then one table of every
    counted name in plain words (the raw name in the row's details), 25 a page."""
    path = "/admin/metrics"
    try:
        days = int(params.get("days", str(DAYS)))
    except ValueError:
        days = DAYS
    if days not in METRIC_DAYS:
        days = DAYS
    kind = str(params.get("kind", "all"))[:40]
    q = " ".join(str(params.get("q", "")).split())[:100]
    try:
        offset = max(0, int(params.get("offset", "0") or 0))
    except ValueError:
        offset = 0
    opened = str(params.get("open", ""))
    totals = state.db.metrics_totals(days)
    series = _series(state, days)
    kind_total: dict[str, int] = {}
    for t in totals:
        kind_total[t["kind"]] = kind_total.get(t["kind"], 0) + int(t["n"])
    known = [k for k in (*METRIC_KINDS, "card")]
    kinds = known + sorted(k for k in kind_total if k not in known)
    if kind not in kinds and kind != "all":
        kind = "all"

    def words(k: str, name: str) -> str:
        return tool_labels.metric_label(k, name) if k in known else name

    def kind_name(k: str) -> str:
        return tool_labels.METRIC_KINDS.get(k, k)

    def first(k: str) -> dict[str, Any] | None:
        return next((t for t in totals if t["kind"] == k), None)

    def busiest(k: str, lead: str) -> str:
        t = first(k)
        return f"{lead}: {words(k, t['name'])}, {int(t['n']):,}" if t else ""

    def show(k: str) -> str:
        return _href(path, kind=k, days=days if days != DAYS else "", q=q)

    unit = f"in {days} days"
    widgets = [
        ui.widget(
            "Tool calls",
            "activity",
            kind_total.get("tool", 0),
            unit=unit,
            detail_html=f"<span class='tx'>{html.escape(busiest('tool', 'Busiest'))}</span>"
            + _spark(list(series.get("tool", {}).values()), label="Tool calls"),
            actions=[ui.button_link("Show them", show("tool"))],
        ),
        ui.widget(
            "Errors",
            "alert",
            kind_total.get("error", 0),
            unit=unit,
            detail_html=f"<span class='tx'>{html.escape(busiest('error', 'Most'))}</span>"
            + _spark(list(series.get("error", {}).values()), label="Errors"),
            actions=[
                ui.button_link("Show them", show("error")),
                ui.button_link("Activity", "/admin/activity?kind=failed"),
            ],
            tone="alert" if kind_total.get("error") else "",
        ),
        ui.widget(
            "API calls",
            "link",
            kind_total.get("api", 0),
            unit=unit,
            detail_html="<span class='tx'>From the app and the website</span>"
            + _spark(list(series.get("api", {}).values()) or [0] * days, label="API calls"),
            actions=[ui.button_link("Show them", show("api"))],
        ),
        ui.widget(
            "Admin actions",
            "shield",
            kind_total.get("admin", 0),
            unit=unit,
            detail_html="<span class='tx'>From the Users page and the admin API</span>"
            + _spark(list(series.get("admin", {}).values()) or [0] * days, label="Admin actions"),
            actions=[ui.button_link("Show them", show("admin"))],
        ),
    ]
    head = _head("Admin: metrics", "Usage and errors, counted per UTC day.", path) + ui.widgets(widgets)
    if not totals:
        none = f"<div class='ui-card'><p>No activity counted in the last {days} days.</p></div>"
        return f"<div class='adm am'>{head}{none}</div>"
    low = q.casefold()
    base = [
        t
        for t in totals
        if not q or low in words(t["kind"], t["name"]).casefold() or low in str(t["name"]).casefold()
    ]
    chips = [
        ui.Chip("All", len(base), _href(path, days=days if days != DAYS else "", q=q), current=kind == "all")
    ]
    for k in kinds:
        n = sum(1 for t in base if t["kind"] == k)
        if n or k in known[:4]:
            chips.append(ui.Chip(kind_name(k), n, show(k), current=kind == k))
    rows_all = [t for t in base if kind == "all" or t["kind"] == kind]
    total = len(rows_all)
    offset = min(offset, max(0, (total - 1) // PER_PAGE * PER_PAGE))
    page_rows = rows_all[offset : offset + PER_PAGE]
    tools = ui.toolbar(
        path,
        search_label="Search names",
        placeholder="Search name",
        q=q,
        filters=[
            ui.filter_select("days", "Period", [(str(n), f"Last {n} days") for n in METRIC_DAYS], str(days))
        ],
        keep={"kind": kind if kind != "all" else ""},
    )
    rows = []
    for i, t in enumerate(page_rows):
        k, name, n = str(t["kind"]), str(t["name"]), int(t["n"])
        key = f"{k}:{name}"
        is_open = opened == key
        rid = f"m-{offset + i}"
        pct = round(n * 100 / max(kind_total.get(k, n), 1))
        bar = (
            f"<span class='am-bar' title='{pct}% of {html.escape(kind_name(k).lower())}'>"
            f"<i style='width:{pct}%'></i></span>"
            f"<span class='am-p'>{pct}%</span>"
        )
        kind_lbl = ui.label(
            KIND_WORDS.get(k, k), "red" if k == "error" else "grey", KIND_ICONS.get(k, "stats")
        )
        toggle = (
            _href(
                path,
                kind=kind,
                days=days if days != DAYS else "",
                q=q,
                offset=offset,
                open="" if is_open else key,
            )
            + f"#{rid}"
        )
        more = ui.meta(
            [
                ("Recorded as", f"<code>{html.escape(name)}</code>"),
                ("Kind", html.escape(KIND_WORDS.get(k, k))),
                ("Count", f"{n:,} in {days} days"),
            ]
        )
        rows.append(
            f"<div class='tr mrow{' open' if is_open else ''}' id='{rid}' data-more>"
            f"<span class='title'><a class='t' href='{html.escape(toggle)}' role='button' "
            f"aria-expanded='{'true' if is_open else 'false'}' aria-controls='{rid}-more'>"
            f"{html.escape(words(k, name))}</a>"
            f"<span class='sub'>{kind_lbl}</span>"
            f"<span class='sm-only'>{kind_lbl} <span class='am-n'>{n:,}</span> {bar}</span></span>"
            f"<span class='md-hide'>{kind_lbl}</span><span class='sm-hide am-n'>{n:,}</span>"
            f"<span class='sm-hide'>{bar}</span>"
            f"<div class='more-body' id='{rid}-more'{'' if is_open else ' hidden'}>{more}</div></div>"
        )
    if rows:
        table = (
            ui.table_open(
                "minmax(0,1fr) 9.5rem 5rem 10rem",
                "minmax(0,1fr) 4.5rem 10rem",
                "minmax(0,1fr)",
                label="Everything counted",
            )
            + ui.table_header(
                [
                    ui.Column("Name"),
                    ui.Column("Kind", "md-hide"),
                    ui.Column("Count", "sm-hide"),
                    ui.Column("Share", "sm-hide"),
                ]
            )
            + "".join(rows)
            + "</div>"
        )
    else:
        table = (
            "<div class='ui-card'><p>Nothing counted matches these filters. "
            f"<a href='{path}'>Show everything</a></p></div>"
        )
    newer = (
        _href(path, kind=kind, days=days if days != DAYS else "", q=q, offset=max(0, offset - PER_PAGE))
        if offset > 0
        else None
    )
    older = (
        _href(path, kind=kind, days=days if days != DAYS else "", q=q, offset=offset + PER_PAGE)
        if offset + PER_PAGE < total
        else None
    )
    return (
        f"<div class='adm am'>{head}"
        + ui.list_head("Everything counted", len(base), note="Busiest first")
        + ui.chips(chips)
        + tools
        + table
        + ui.pager(offset, total, newer_href=newer, older_href=older)
        + "</div>"
    )


# -- Card models ------------------------------------------------------------------------------------


def _size_text(size: Any) -> str:
    try:
        n = int(size)
    except (TypeError, ValueError):
        return ""
    return f"{n / 1024:.1f} KB" if n >= 1024 else f"{n} B"


def _models_query(db: Any, q: str, member: str, since: int | None) -> tuple[str, list[Any]]:
    where, args = ["1"], []
    if q:
        where.append("name LIKE ? ESCAPE '\\'")
        args.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    if member:
        where.append("added_by = ?")
        args.append(member)
    if since is not None:
        where.append("updated_at >= ?")
        args.append(since)
    return " AND ".join(where), args


def _models_body(
    state: Any, csrf: str | None, params: Mapping[str, str], *, notice: str = "", now: float | None = None
) -> str:
    """/admin/models: three widgets, search by card, a member filter, the table (25 a page) and
    the Remove box (opened from the row's menu; the ?remove= address draws it open)."""
    now = time.time() if now is None else now
    path = "/admin/models"
    db = state.db
    q = " ".join(str(params.get("q", "")).split())[:100]
    member = str(params.get("member", ""))[:200]
    changed = "7d" if params.get("changed") == "7d" else ""
    try:
        offset = max(0, int(params.get("offset", "0") or 0))
        if "page" in params and "offset" not in params:  # the old address: ?page=N
            offset = max(0, int(params.get("page", "1")) - 1) * PER_PAGE
    except ValueError:
        offset = 0
    remove_key, remove_by = str(params.get("remove", "")), str(params.get("by", ""))
    head = _head("Admin: card models", MODELS_PURPOSE, path) + notice
    total_all = db.count_all_card_models()
    if not total_all:
        return (
            f"<div class='adm amo'>{head}<div class='ui-card'><p>No card models are kept yet.</p></div></div>"
        )
    week_since = int(now) - 7 * 86400
    with db._lock:
        by_member = db._conn.execute(
            "SELECT added_by, COUNT(*) AS n FROM card_models GROUP BY added_by ORDER BY n DESC, added_by"
        ).fetchall()
        week_rows = db._conn.execute(
            "SELECT DISTINCT added_by FROM card_models WHERE updated_at >= ?", (week_since,)
        ).fetchall()
        week_n = int(
            db._conn.execute(
                "SELECT COUNT(*) FROM card_models WHERE updated_at >= ?", (week_since,)
            ).fetchone()[0]
        )
        everyone = int(db._conn.execute("SELECT COUNT(*) FROM users").fetchone()[0])
        where, args = _models_query(db, q, member, week_since if changed else None)
        total = int(db._conn.execute(f"SELECT COUNT(*) FROM card_models WHERE {where}", args).fetchone()[0])
        offset = min(offset, max(0, (total - 1) // PER_PAGE * PER_PAGE))
        rows = [
            dict(r)
            for r in db._conn.execute(
                "SELECT card_key, name, added_by, added_at, updated_at, "
                "length(CAST(model_json AS BLOB)) AS size "
                f"FROM card_models WHERE {where} "
                "ORDER BY updated_at DESC, card_key, added_by LIMIT ? OFFSET ?",
                [*args, PER_PAGE, offset],
            ).fetchall()
        ]
    newest = db.list_card_models(limit=1)
    people: dict[str, str] = {}

    def who(sub: str) -> str:
        if sub not in people:
            name = member_name(db, sub)
            people[sub] = (
                "a deleted account" if name is None else (NO_NAME if "no username" in name else name)
            )
        return people[sub]

    top = by_member[0] if by_member else None
    widgets = [
        ui.widget(
            "Models kept",
            "layers",
            total_all,
            detail_html=f"Newest change: “{html.escape(str(newest[0]['name']))}”, "
            + ui.when(newest[0]["updated_at"], now=now)
            if newest
            else "",
            actions=[ui.button_link("Show all", path)],
        ),
        ui.widget(
            "Members with models",
            "user",
            len(by_member),
            unit=f"of {everyone}",
            detail=f"Most: {who(top['added_by'])}, {plural(int(top['n']), 'model')}" if top else "",
            actions=[ui.button_link(f"Show {who(top['added_by'])}'s", _href(path, member=top["added_by"]))]
            if top
            else (),
        ),
        ui.widget(
            "Changed in 7 days",
            "clock",
            week_n,
            unit="models",
            detail=("By " + _and_list([who(r["added_by"]) for r in week_rows])) if week_rows else "",
            actions=[ui.button_link("Show them", _href(path, changed="7d"))],
            wide_sm=True,
        ),
    ]
    member_opts = [("", "Every member")] + [(r["added_by"], who(r["added_by"])) for r in by_member]
    tools = ui.toolbar(
        path,
        search_label="Search card models",
        placeholder="Search card name",
        q=q,
        filters=[ui.filter_select("member", "Member", member_opts, member)],
        keep={"changed": changed},
    )
    list_href = _href(path, q=q, member=member, changed=changed, offset=offset)
    items = []
    for i, r in enumerate(rows):
        adder, name = str(r["added_by"]), str(r["name"])
        added, updated = ui.when(r["added_at"], now=now), ui.when(r["updated_at"], now=now)
        is_changed = r["updated_at"] != r["added_at"]
        remove_href = _href(
            path, q=q, member=member, changed=changed, offset=offset, remove=r["card_key"], by=adder
        )
        size = _size_text(r["size"])
        items.append(
            f"<div class='tr model' id='cm-{offset + i}' data-card='{html.escape(name)}' "
            f"data-who='{html.escape(who(adder))}' data-remove='{html.escape(remove_href)}' "
            f"data-member='{html.escape(_href(path, member=adder))}' "
            f"data-open='/cards?q={quote(name, safe='')}'>"
            f"<span class='title'><span class='t'>{html.escape(name)}</span>"
            f"<span class='sub'>added by {html.escape(who(adder))} · {added} · {size}</span>"
            f"<span class='sm-only'>{html.escape(who(adder))} · "
            f"{'changed ' + updated if is_changed else 'added ' + added}</span></span>"
            f"<span class='md-hide'>{html.escape(who(adder))}</span><span class='md-hide'>{added}</span>"
            f"<span class='sm-hide'>{updated if is_changed else '<span class=muted>—</span>'}</span>"
            f"<span class='md-hide muted'>{size}</span>"
            f"<span class='act'><a class='kebab' href='{html.escape(remove_href)}' "
            f"aria-label='Actions for {html.escape(name)}' "
            f"aria-haspopup='menu' title='Menu'>{icon('more')}</a></span></div>"
        )
    if items:
        table = (
            ui.table_open(
                "minmax(0,1fr) 11rem 6.5rem 6.5rem 4.5rem 44px",
                "minmax(0,1fr) 6.5rem 44px",
                "minmax(0,1fr) 44px",
                label="All card models",
            )
            + ui.table_header(
                [
                    ui.Column("Card"),
                    ui.Column("Added by", "md-hide"),
                    ui.Column("Added", "md-hide"),
                    ui.Column("Changed", "sm-hide"),
                    ui.Column("Size", "md-hide"),
                    ui.Column("", html="<span class='sr-only'>Actions</span>"),
                ]
            )
            + "".join(items)
            + "</div>"
        )
    else:
        table = f"<div class='ui-card'><p>No card models match. <a href='{path}'>Show all</a></p></div>"
    newer = (
        _href(path, q=q, member=member, changed=changed, offset=max(0, offset - PER_PAGE))
        if offset > 0
        else None
    )
    older = (
        _href(path, q=q, member=member, changed=changed, offset=offset + PER_PAGE)
        if offset + PER_PAGE < total
        else None
    )
    box = ""
    if remove_key and remove_by:
        with db._lock:
            found = db._conn.execute(
                "SELECT name FROM card_models WHERE card_key = ? AND added_by = ?", (remove_key, remove_by)
            ).fetchone()
        if found is not None:
            card, adder_name = str(found["name"]), who(remove_by)
            box = ui.box(
                "remove",
                f"Remove the model of “{card}”?",
                f"<form id='rmf' method='post' action='{path}'>"
                f"<input type='hidden' name='csrf' value='{html.escape(csrf or '')}'>"
                f"<input type='hidden' name='card_key' value='{html.escape(remove_key)}'>"
                f"<input type='hidden' name='added_by' value='{html.escape(remove_by)}'>"
                f"<input type='hidden' name='back' value='{html.escape(list_href)}'>"
                f"<p>“{html.escape(adder_name)}” added it. "
                f"The simulator stops using it in {html.escape(adder_name)}'s "
                "simulations. This can't be undone.</p></form>",
                actions=[
                    ui.box_button("Cancel", cancel=True),
                    ui.box_button(
                        "Remove model",
                        kind="danger-solid",
                        form="rmf",
                        attrs="aria-label='Remove the model of "
                        + f"{html.escape(card)} added by {html.escape(adder_name)}'",
                    ),
                ],
                open=True,
                close_href=list_href,
            )
    return (
        f"<div class='adm amo'>{head}"
        + ui.widgets(widgets)
        + ui.list_head("All card models", total, note="Most recently changed first")
        + tools
        + table
        + ui.pager(offset, total, newer_href=newer, older_href=older)
        + box
        + "</div>"
    )


def _and_list(names: list[str]) -> str:
    names = sorted(set(names), key=str.casefold)
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


__all__ = ["ACTIONS", "AdminError", "add_admin_routes", "is_admin", "overview_data", "users_data"]
