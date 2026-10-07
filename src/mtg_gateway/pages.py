"""Browser pages: sign-in, account linking, proposal review.

These are the only pages a person sees. They share the OIDC login with the MCP
flow (the identity provider is the single source of identity), keep a short
server-side session in a cookie, and protect every state change with a form
token bound to that session. Nothing is changed by a GET.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import secrets
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .auth_provider import BROWSER_COOKIE, LoginError, cookie_name
from .avatars import initials_svg
from .clickguard import form_stamp, guarded_form, submitted_too_soon
from .decks import DeckError, current_client, row_label, row_line
from .theme import THEME_COOKIE, render, theme_from_cookie

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

SESSION_COOKIE = "mtg_session"
BROWSER_CLIENT_ID = "__browser__"


def add_browser_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings
    secure = s.public_url.startswith("https://")
    fresh_cookie = cookie_name("mtg_fresh_login", s)
    session_cookie = cookie_name(SESSION_COOKIE, s)

    def page(
        title: str,
        body: str,
        *,
        status: int = 200,
        sub: str | None = None,
        sid: str | None = None,
        scripts: bool = False,
    ) -> Response:
        return render(
            title,
            body,
            site=s.server_name,
            status=status,
            signed_in=sub is not None,
            csrf=_csrf(s, sid),
            scripts=scripts,
        )

    def current(request: Request) -> tuple[str | None, str | None]:
        return browser_session(state, request)

    async def form(request: Request) -> dict[str, str] | Response:
        raw = await read_limited(request, MAX_FORM)
        if raw is None:
            return page("Too large", "<p>That form was too large.</p>", status=413)
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}

    def check_csrf(sid: str | None, data: dict[str, str]) -> bool:
        expected = _csrf(s, sid)
        given = data.get("csrf", "").encode()
        return bool(sid and expected and hmac.compare_digest(given, expected.encode()))

    def to_login(next_path: str) -> Response:
        return RedirectResponse(f"/login?next={_safe_next(next_path)}", status_code=302)

    # -- sign in / out ------------------------------------------------------
    @server.custom_route("/login", methods=["GET"], include_in_schema=False)
    async def login(request: Request) -> Response:
        nxt = _safe_next(request.query_params.get("next", "/account"))
        sub, _sid = current(request)
        if sub:
            return RedirectResponse(nxt, status_code=302)
        # Right after a sign-out on this device, the identity provider is asked to make the person
        # enter their credentials again, so the next person on a shared device or the Android app
        # is not silently signed back in as the previous one.
        fresh = request.cookies.get(fresh_cookie) == "1"
        try:
            url = await state.provider.start_idp_login(BROWSER_CLIENT_ID, {"next": nxt}, force_login=fresh)
        except LoginError as exc:
            return page("Sign-in unavailable", f"<p>{html.escape(str(exc))}</p>", status=exc.status)
        resp = RedirectResponse(url, status_code=302, headers={"Cache-Control": "no-store"})
        if fresh:
            resp.delete_cookie(fresh_cookie, path="/", secure=secure, httponly=True, samesite="lax")
        return resp

    @server.custom_route("/logout", methods=["GET"], include_in_schema=False)
    async def logout_confirm(request: Request) -> Response:
        # A link to /logout signs nobody out: it shows a button that posts the form token.
        sub, sid = current(request)
        if not sub:
            return RedirectResponse("/signed-out", status_code=303)
        return page(
            "Sign out",
            "<div class='card'><p>Sign out of this gateway on this device?</p>"
            "<p class='muted small'>Unsaved scan drafts on this device are cleared too.</p>"
            "<form method='post' action='/logout'>"
            f"<input type='hidden' name='csrf' value='{html.escape(_csrf(s, sid) or '')}'>"
            "<div class='actions'><button class='primary'>Sign out</button>"
            "<button name='everywhere' value='1'>Sign out on all my devices</button></div></form>"
            "<p class='muted small'>All devices signs out every browser and the Android app. "
            "Connected AI apps keep working; disconnect them on your Account page.</p></div>",
            sub=sub,
            sid=sid,
        )

    @server.custom_route("/logout", methods=["POST"], include_in_schema=False)
    async def logout(request: Request) -> Response:
        sub, sid = current(request)
        data = await form(request)
        if isinstance(data, Response):
            return data
        if not (sub and sid and check_csrf(sid, data)):
            # Without a valid form token (a cross-site post, an expired form) nothing is cleared:
            # no cookie deletion and no Clear-Site-Data, which would wipe unsaved scan drafts.
            return RedirectResponse("/logout" if sub else "/signed-out", status_code=303)
        state.db.delete_browser_session(sid)
        if data.get("everywhere") == "1":
            n = state.db.delete_browser_sessions_for(sub)
            state.db.audit(
                "signed_out_everywhere", sub=sub, client_id=BROWSER_CLIENT_ID, detail={"sessions": n}
            )
        # Not "/": the dashboard needs a session, so it would send the browser straight to sign-in.
        resp = RedirectResponse("/signed-out", status_code=303)
        resp.delete_cookie(session_cookie, path="/", secure=secure, httponly=True, samesite="lax")
        # Ask the browser to drop cached pages and site storage (scan drafts and the like) too.
        resp.headers["Clear-Site-Data"] = '"cache", "storage"'
        resp.set_cookie(
            fresh_cookie, "1", max_age=3600, path="/", secure=secure, httponly=True, samesite="lax"
        )
        return resp

    @server.custom_route("/theme", methods=["POST"], include_in_schema=False)
    async def set_theme(request: Request) -> Response:
        """Light / Dark / System from the account menu: a cookie, so every page (and the Android
        app's pages) renders the choice without script. Signed-in members only, with the form token."""
        sub, sid = current(request)
        data = await form(request)
        if isinstance(data, Response):
            return data
        back = _safe_next(data.get("next"))
        if not (sub and sid and check_csrf(sid, data)):
            return RedirectResponse(back, status_code=303)
        theme = theme_from_cookie(data.get("theme"))
        resp = RedirectResponse(back, status_code=303)
        if theme == "system":
            resp.delete_cookie(THEME_COOKIE, path="/")
        else:
            resp.set_cookie(
                THEME_COOKIE,
                theme,
                max_age=365 * 86400,
                path="/",
                secure=secure,
                httponly=True,
                samesite="lax",
            )
        return resp

    @server.custom_route("/signed-out", methods=["GET"], include_in_schema=False)
    async def signed_out(_request: Request) -> Response:
        return page(
            "Signed out",
            "<div class='card'><p>You are signed out of this gateway.</p>"
            "<div class='actions'><a class='btn btn-primary' href='/login?next=/'>Sign in again</a>"
            "</div></div>",
        )

    @server.custom_route("/data-deleted", methods=["GET"], include_in_schema=False)
    async def data_deleted(_request: Request) -> Response:
        backups = (
            "The gateway's nightly database backups made before now still hold a copy of what was "
            f"deleted until they age out, after {s.backup_keep_days} days. "
            if s.backup_dir is not None
            else ""
        )
        return page(
            "Your data was deleted",
            "<div class='card'><p>What this gateway kept for your account was deleted from its "
            "database: proposals, snapshots, test reports, scan sessions, deck covers, your Archidekt "
            "link, connected apps and your sign-in. You are signed out.</p>"
            "<p class='muted small'>Your decks on Archidekt, including any backup copies the gateway "
            "made there, are yours on Archidekt and were not touched. "
            f"{html.escape(backups)}The gateway's security log keeps "
            "a record that this account existed and was deleted until it ages out after a year.</p>"
            "<div class='actions'><a class='btn btn-primary' href='/login?next=/'>Sign in again</a>"
            "</div></div>",
        )

    # -- account ------------------------------------------------------------
    @server.custom_route("/account", methods=["GET"], include_in_schema=False)
    async def account(request: Request) -> Response:
        sub, sid = current(request)
        if not sub:
            return to_login("/account")
        notice = _notice(request.query_params.get("ok"), request.query_params.get("err"))
        return page("Account", notice + _account_body(state, sub, _csrf(s, sid)), sub=sub, sid=sid)

    @server.custom_route("/account/avatar", methods=["GET"], include_in_schema=False)
    async def account_avatar(request: Request) -> Response:
        """The signed-in member's picture for the account menu: the one their identity provider
        gave (avatars.py), else their initials. Never someone else's: there is no parameter."""
        sub, _sid = current(request)
        if not sub:
            return Response(status_code=404)
        stored = state.membership.avatars.get(sub) if state.membership is not None else None
        if stored is not None:
            body, kind = stored
        else:
            user = state.db.get_user(sub) or {}
            name = user.get("name") or user.get("preferred_username") or user.get("email") or ""
            body, kind = initials_svg(str(name), sub), "image/svg+xml"
        return Response(
            body,
            media_type=kind,
            headers={
                "Cache-Control": "private, max-age=300",
                "X-Content-Type-Options": "nosniff",
                # Opened on its own, an image (above all the SVG) can run nothing and load nothing.
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
                "Vary": "Cookie",
            },
        )

    @server.custom_route("/account", methods=["POST"], include_in_schema=False)
    async def account_post(request: Request) -> Response:
        sub, sid = current(request)
        if not sub:
            return to_login("/account")
        data = await form(request)
        if isinstance(data, Response):
            return data
        if not check_csrf(sid, data):
            return page(
                "Account",
                _err("This form expired. Reload the page and try again."),
                status=403,
                sub=sub,
                sid=sid,
            )
        action = data.get("action")
        if action in ("disconnect", "disconnect_all"):
            client_id = data.get("client_id") if action == "disconnect" else None
            n = state.db.revoke_client_for_user(sub, client_id)
            # A disconnected app's pending proposals go with it: they stay visible, rejected, and
            # can no longer be applied from the review page.
            closed = state.db.reject_client_proposals(sub, client_id)
            state.db.audit(
                "apps_disconnected",
                sub=sub,
                client_id=client_id,
                detail={"tokens": n, "by": "user", "proposals_rejected": len(closed)},
            )
            for pid in closed:
                state.db.audit(
                    "proposal_rejected",
                    sub=sub,
                    client_id=BROWSER_CLIENT_ID,
                    detail={"proposal_id": pid, "origin": "browser", "reason": "app disconnected"},
                )
            return RedirectResponse(f"/account?ok={'disconnected' if client_id else 'disconnected_all'}", 303)
        if action == "unlink":
            state.decks.unlink(sub)
            return RedirectResponse("/account?ok=unlinked", status_code=303)
        if action == "delete_data":
            if data.get("confirm") != "yes":
                return RedirectResponse("/account?err=confirm_delete", status_code=303)
            state.db.delete_member_data(sub)
            if state.membership is not None:
                state.membership.avatars.delete(sub)
            resp = RedirectResponse("/data-deleted", status_code=303)
            resp.delete_cookie(session_cookie, path="/", secure=secure, httponly=True, samesite="lax")
            resp.headers["Clear-Site-Data"] = '"cache", "storage"'
            return resp
        if action == "link":
            login_name = data.get("archidekt_login", "").strip()
            password = data.get("archidekt_password", "")
            if not login_name or not password:
                return page(
                    "Account",
                    _err("Enter your Archidekt username or email and password.")
                    + _account_body(state, sub, _csrf(s, sid)),
                    status=400,
                    sub=sub,
                    sid=sid,
                )
            try:
                await state.decks.link(sub, login_name, password)
            except DeckError as exc:
                return page(
                    "Account",
                    _err(str(exc)) + _account_body(state, sub, _csrf(s, sid)),
                    status=400,
                    sub=sub,
                    sid=sid,
                )
            return RedirectResponse("/account?ok=linked", status_code=303)
        return page("Account", _err("Unknown action."), status=400, sub=sub, sid=sid)

    # -- proposals ----------------------------------------------------------
    @server.custom_route("/proposals", methods=["GET"], include_in_schema=False)
    async def proposals(request: Request) -> Response:
        sub, sid = current(request)
        if not sub:
            return to_login("/proposals")
        rows = state.decks.list_proposals(sub)
        if not rows:
            body = (
                "<div class='card'><p>No proposals yet. Ask your assistant to propose a deck change "
                "and it will appear here for review.</p></div>"
            )
        else:
            items = "".join(
                f"<li><a class='name' href='/proposals/{html.escape(r['id'])}'>"
                f"{html.escape(r['deck_name'] or r['deck_id'])}</a>"
                f"<span class='badge {_badge(r['state'])}'>{html.escape(r['state'])}</span>"
                + (
                    f"<span class='muted small'>{html.escape(r['created_by'])}</span>"
                    if r.get("created_by")
                    else ""
                )
                + f"<span class='when'>{_when(r['created_at'])}</span></li>"
                for r in rows
            )
            body = f"<div class='card'><ul class='plain plist'>{items}</ul></div>"
        return page("Proposals", body, sub=sub, sid=sid)

    @server.custom_route("/proposals/{pid}", methods=["GET"], include_in_schema=False)
    async def proposal(request: Request) -> Response:
        pid = request.path_params["pid"]
        sub, sid = current(request)
        if not sub:
            return to_login(f"/proposals/{pid}")
        try:
            p = state.decks.describe(sub, pid)
        except DeckError:
            return page(
                "Not found", "<p>No such proposal for your account.</p>", status=404, sub=sub, sid=sid
            )
        notice = _notice(request.query_params.get("ok"), request.query_params.get("err"))
        title = (
            f"New deck: {p['deck_name']}"
            if p.get("kind") == "create_deck"
            else f"Proposal for {p['deck_name'] or p['deck_id']}"
        )
        return page(
            title,
            notice + _proposal_body(p, _csrf(s, sid), form_stamp(s.session_secret, f"proposal:{sub}:{pid}")),
            sub=sub,
            sid=sid,
            scripts=True,  # the click guard on Apply (clickguard.py)
        )

    @server.custom_route("/proposals/{pid}", methods=["POST"], include_in_schema=False)
    async def proposal_post(request: Request) -> Response:
        pid = request.path_params["pid"]
        sub, sid = current(request)
        if not sub:
            return to_login(f"/proposals/{pid}")
        data = await form(request)
        if isinstance(data, Response):
            return data
        action = data.get("action")
        if not check_csrf(sid, data) or action not in ("apply", "reject"):
            return page(
                "Proposal",
                _err("This form expired. Reload the page and try again."),
                status=403,
                sub=sub,
                sid=sid,
            )
        if action == "apply" and submitted_too_soon(
            s.session_secret, f"proposal:{sub}:{pid}", data.get("shown")
        ):
            # Faster than a person reads the page (double-clickjacking), or a stamp that is not
            # ours: nothing is applied and the review page is shown again.
            return RedirectResponse(f"/proposals/{pid}", status_code=303)
        current_client.set(BROWSER_CLIENT_ID)  # the audit rows name the browser as the actor
        try:
            if action == "reject":
                state.decks.reject(sub, pid)
            else:
                await state.decks.apply(sub, pid, via="browser")
        except DeckError as exc:
            # Only a code goes in the URL; _notice maps it to fixed text, so a crafted link cannot
            # put words of its choosing in the page's error box. Failure details are in the result.
            return RedirectResponse(f"/proposals/{pid}?err={_err_code(exc.kind)}", status_code=303)
        return RedirectResponse(
            f"/proposals/{pid}?ok={'rejected' if action == 'reject' else 'applied'}", status_code=303
        )

    # -- completion of the browser sign-in (called from /auth/callback) ----
    async def finish_browser_login(request: Request) -> Response:
        q = request.query_params
        try:
            session, identity = await state.provider.finish_idp_leg(
                state=q.get("state"),
                code=q.get("code"),
                error=q.get("error"),
                cookie_value=request.cookies.get(cookie_name(BROWSER_COOKIE, s)),
            )
        except LoginError as exc:
            return page("Sign-in failed", f"<p>{html.escape(str(exc))}</p>", status=exc.status)
        if identity is None:
            return page("Sign-in cancelled", "<p>You cancelled at the identity provider.</p>", status=200)
        sid = secrets.token_urlsafe(32)
        state.db.create_browser_session(sid, identity.sub, s.browser_session_ttl)
        state.db.audit("browser_login", sub=identity.sub)
        nxt = _safe_next(str(session["params"].get("next", "/account")))
        resp = RedirectResponse(nxt, status_code=302, headers={"Cache-Control": "no-store"})
        resp.set_cookie(
            session_cookie,
            sid,
            max_age=s.browser_session_ttl,
            path="/",
            httponly=True,
            secure=secure,
            samesite="lax",
        )
        return resp

    state.finish_browser_login = finish_browser_login  # type: ignore[attr-defined]


# -- helpers -----------------------------------------------------------------
def _csrf(s: Any, sid: str | None) -> str | None:
    if not sid:
        return None
    return hmac.new(s.session_secret.encode(), f"csrf:{sid}".encode(), hashlib.sha256).hexdigest()


def browser_session(state: Any, request: Request) -> tuple[str | None, str | None]:
    """(subject, session id) for the signed-in person, or (None, None).

    A browser session is only created after a sign-in that passed MTG_REQUIRED_GROUP. Before the
    request reaches here, MembershipMiddleware has asked the identity provider for the person's
    current groups (membership.py) and revoked everything of a removed member, and the recorded
    groups were updated; they are checked again here, so a removal at the provider, a changed
    MTG_REQUIRED_GROUP or an admin disabling the account all lock the session out.
    """
    sid = request.cookies.get(cookie_name(SESSION_COOKIE, state.settings))
    sub = state.db.get_browser_session(sid) if sid else None
    if not sub:
        return None, None
    user = state.db.get_user(sub)
    if user is None or user.get("disabled_at"):
        return None, None  # a disabled account keeps no browser session either
    if not state.settings.grants_access(user["groups"]):
        return None, None
    return sub, sid


def browser_user(state: Any, request: Request) -> str | None:
    """The signed-in person's subject, or None (see browser_session)."""
    return browser_session(state, request)[0]


def login_redirect(next_path: str) -> Response:
    from urllib.parse import quote

    return RedirectResponse(f"/login?next={quote(_safe_next(next_path), safe='/?=')}", status_code=302)


def _safe_next(value: str | None) -> str:
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return "/account"
    return value[:200]


MAX_FORM = 16_384

# Notices a redirect may ask for. Only these codes are accepted from ?ok= and ?err=; anything
# else shows nothing, so a link cannot make the page display text of its choosing.
OK_MESSAGES = {
    "linked": "Archidekt account linked. Your assistant can now read your decks.",
    "unlinked": "Archidekt account unlinked. The gateway's copy of your Archidekt session was "
    "deleted. The gateway has no way to sign that session out at Archidekt, so Archidekt keeps "
    "accepting it until it expires. If you think it was exposed, change your Archidekt password.",
    "applied": "Applied. Archidekt now matches this proposal.",
    "rejected": "Rejected. Nothing was sent to Archidekt.",
    "disconnected": "Disconnected. That app has to be connected and approved again to use your account. "
    "Its pending proposals were rejected.",
    "disconnected_all": "Every connected app was disconnected. Each has to be connected and approved again. "
    "Their pending proposals were rejected.",
}
ERR_MESSAGES = {
    "confirm_delete": "Nothing was deleted. Tick the box to confirm, then press Delete my data.",
    "failed": "This proposal could not be applied. Details are in the result below, if any.",
    "writes_disabled": "Deck writes are switched off on this gateway. The proposal is kept for review; "
    "nothing was sent to Archidekt.",
    "not_found": "No such proposal for your account.",
    "already_applied": "This proposal was already applied; nothing was sent again.",
    "not_pending": "This proposal is no longer pending, so nothing was done.",
    "stale": "The deck changed on Archidekt since this proposal was made. Nothing was sent. "
    "Ask your assistant for a new proposal from the current deck.",
    "backup_failed": "The backup copy of the deck could not be made on Archidekt, so nothing was "
    "changed. The proposal is still pending; try again once Archidekt answers.",
    "not_linked": "Your Archidekt account is not linked, or Archidekt no longer accepts the stored "
    "session. Relink it on the Account page.",
    "forbidden": "This deck belongs to another Archidekt account than the one you linked.",
    "other_account": "This new deck was proposed for another Archidekt account than the one linked now, "
    "so nothing was created. Ask for a new proposal.",
    "verify_mismatch": "Archidekt accepted the change but the deck does not fully match the proposal. "
    "A snapshot of the deck from before the change was kept; see the result below.",
}


async def read_limited(request: Request, limit: int) -> bytes | None:
    """The request body, or None once it is longer than ``limit`` bytes. Stops reading at the
    limit instead of buffering the whole body first."""
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        return None
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            return None
    return bytes(buf)


def _err_code(kind: str) -> str:
    return kind if kind in ERR_MESSAGES else "failed"


def _err(msg: str) -> str:
    return f"<div class='notice error'>{html.escape(msg)}</div>"


def _notice(ok: str | None, err: str | None) -> str:
    if ok in OK_MESSAGES:
        return f"<div class='notice ok'>{html.escape(OK_MESSAGES[ok])}</div>"
    if err in ERR_MESSAGES:
        return _err(ERR_MESSAGES[err])
    return ""


def _badge(state: str) -> str:
    return {
        "applied": "ok",
        "pending": "warn",
        "failed": "danger",
        "expired": "",
        "applying": "warn",
        "rejected": "",
    }.get(state, "")


def _when(ts: int | None) -> str:
    if not ts:
        return ""
    return time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(ts))


def _account_body(state: Any, sub: str, csrf: str | None) -> str:
    info = state.decks.status(sub)
    user = state.db.get_user(sub) or {}
    who = html.escape(user.get("preferred_username") or user.get("email") or sub)
    csrf_in = f"<input type='hidden' name='csrf' value='{html.escape(csrf or '')}'>"
    writes = (
        "<span class='badge ok'>enabled</span>"
        if info["writes_enabled"]
        else "<span class='badge'>disabled</span>"
    )
    out = [
        "<div class='card'><dl class='meta'>"
        f"<dt>Signed in as</dt><dd><strong>{who}</strong></dd>"
        f"<dt>Deck writes</dt><dd>{writes}</dd></dl>"
        "<p class='muted small'>Deck writes are switched on or off for the whole gateway by its "
        "operator. While they are off, proposals can be reviewed but not applied.</p>"
        "<a class='btn' href='/skill'>Get the assistant skill for Claude or ChatGPT</a> "
        "<a class='btn' href='/app'>Get the Android app</a>"
        f"<form method='post' action='/logout'>{csrf_in}"
        "<button class='inline'>Sign out</button></form> "
        "<a class='small' href='/logout'>Sign out on all my devices</a></div>"
    ]
    out.append(_apps_card(state, sub, csrf_in))
    if info["linked"]:
        out.append(
            "<div class='card'><h2>Archidekt</h2>"
            f"<p>Linked to <strong>{html.escape(info['archidekt_username'] or '')}</strong> "
            "<span class='badge ok'>active</span></p>"
            f"<p class='muted small'>Linked {_when(info['linked_at'])}. "
            f"Last used {_when(info['last_used_at']) or 'never'}.</p>"
            f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='unlink'>"
            "<button class='danger'>Unlink and delete stored session</button></form>"
            "<p class='muted small'>Unlinking deletes the gateway's copy of the session. Archidekt "
            "keeps accepting that session until it expires; the gateway cannot sign it out there.</p></div>"
        )
    else:
        out.append(
            "<div class='card'><h2>Link your Archidekt account</h2>"
            "<p>Your password is sent to Archidekt once to obtain a session and is not stored. "
            "Only the resulting session token is kept, encrypted.</p>"
            f"<form method='post' autocomplete='off'>{csrf_in}"
            "<input type='hidden' name='action' value='link'>"
            "<label for='l'>Archidekt username or email</label>"
            "<input id='l' type='text' name='archidekt_login' required autocomplete='username'>"
            "<label for='p'>Archidekt password</label>"
            "<input id='p' type='password' name='archidekt_password' required "
            "autocomplete='current-password'>"
            "<button class='primary'>Link account</button></form></div>"
        )
    s = state.settings
    out.append(_delete_card(csrf_in, s.backup_keep_days if s.backup_dir is not None else None))
    return "".join(out)


def _apps_card(state: Any, sub: str, csrf_in: str) -> str:
    """The assistants connected to this account, each with a Disconnect button."""
    apps = state.db.user_clients(sub)
    if not apps:
        return (
            "<div class='card'><h2>Connected apps</h2><p class='muted'>No assistant is connected "
            "to your account.</p></div>"
        )
    items = "".join(
        f"<li><span class='name'>{html.escape(a['name'] or 'Unnamed app')}</span>"
        # the name is the app's own choice; its client id tells two apps of one name apart
        f"<code class='small'>{html.escape(_short_id(a['client_id']))}</code>"
        f"<span class='when'>last signed in or refreshed {_when(a['last_issued_at'])}</span>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='disconnect'>"
        f"<input type='hidden' name='client_id' value='{html.escape(a['client_id'])}'>"
        "<button class='danger'>Disconnect</button></form></li>"
        for a in apps
    )
    return (
        "<div class='card'><h2>Connected apps</h2>"
        "<p class='muted small'>Assistants that can use your account now. Disconnect any you don't "
        "recognise; it then has to be connected and approved again.</p>"
        f"<ul class='plain plist'>{items}</ul>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='disconnect_all'>"
        "<button class='danger'>Disconnect all apps</button></form></div>"
    )


def _short_id(client_id: str) -> str:
    return client_id if len(client_id) <= 40 else client_id[:37] + "..."


def _delete_card(csrf_in: str, backup_days: int | None = None) -> str:
    """Deleting your data is as easy to find as everything else on the page: no hiding it, no
    guilt-trip wording; one checkbox so a stray tap can't do it."""
    kept = (
        f" Nightly database backups keep a copy until they age out after {backup_days} days."
        if backup_days
        else ""
    )
    return (
        "<div class='card'><h2>Delete my data</h2>"
        "<p>Delete everything this gateway keeps for your account: proposals, snapshots, test "
        "reports, scan sessions, your Archidekt link, connected apps and your sign-in. Your decks on "
        f"Archidekt are not touched. This can't be undone.{kept}</p>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='delete_data'>"
        "<label class='check'><input type='checkbox' name='confirm' value='yes' required> "
        "Yes, delete my data from this gateway</label>"
        "<button class='danger'>Delete my data</button></form></div>"
    )


def _li(kind: str, label: str, name_html: str, qty_html: str = "") -> str:
    return (
        f"<li class='{kind}'><span class='act'>{html.escape(label)}</span>"
        f"<span class='name'>{name_html}</span><span class='qty'>{qty_html}</span></li>"
    )


def _was_now(was: Any, now: Any) -> str:
    return f"<span class='was'>{html.escape(str(was))}</span> &rarr; {html.escape(str(now))}"


def _change_rows(rows: list[dict[str, Any]] | None, diff: str) -> tuple[str, dict[str, int], int]:
    """Render a proposal's structured review rows (``decks.row_line`` describes each kind) as
    list items; return (html, counts by kind, net card delta).

    Rows are rendered from their fields, never from the text diff, so no card name, category or
    deck name can pass itself off as another row. A proposal stored before rows existed shows its
    diff lines verbatim, one plain item per line.
    """
    out: list[str] = []
    counts = {"add": 0, "del": 0, "chg": 0, "cat": 0}
    net = 0
    if rows is None:
        for line in filter(None, (ln.strip() for ln in diff.splitlines())):
            counts["chg"] += 1
            out.append(_li("chg", "Change", html.escape(line)))
        return "".join(out), counts, net
    for r in rows:
        kind = r.get("kind")
        name = html.escape(row_label(r))
        if kind == "add":
            qty = int(r.get("qty") or 0)
            counts["add"] += 1
            net += qty
            out.append(_li("add", "Add", name, f"+{qty}"))
        elif kind == "remove":
            qty = int(r.get("qty") or 0)
            counts["del"] += 1
            net -= qty
            out.append(_li("del", "Remove", name, f"-{qty}"))
        elif kind == "change":
            before, after = int(r.get("before") or 0), int(r.get("after") or 0)
            delta = after - before
            counts["chg"] += 1
            net += delta
            out.append(
                _li(
                    "chg",
                    "Change",
                    name,
                    f"<span class='was'>{before}</span> &rarr; {after} "
                    f"<span class='sr-only'>copies, </span>({'+' if delta > 0 else ''}{delta})",
                )
            )
        elif kind == "category" and r.get("leaves"):
            # Moved into a category the deck does not count (Maybeboard, Sideboard): the copies
            # leave the deck proper, so this is shown and counted as a removal.
            qty = int(r.get("leaves") or 0)
            counts["del"] += 1
            net -= qty
            out.append(
                _li(
                    "del",
                    "Move out of deck",
                    name,
                    _was_now(r.get("before", ""), r.get("after", "")) + f" (leaves the deck, -{qty})",
                )
            )
        elif kind == "category":
            counts["cat"] += 1
            out.append(_li("chg", "Category", name, _was_now(r.get("before", ""), r.get("after", ""))))
        elif kind == "commander":
            counts["cat"] += 1
            out.append(
                _li(
                    "chg",
                    "Commander",
                    html.escape(str(r.get("after", ""))),
                    _was_now(r.get("before", ""), r.get("after", "")),
                )
            )
        elif kind == "finish":
            counts["cat"] += 1
            out.append(_li("chg", "Finish", name, _was_now(r.get("before", ""), r.get("after", ""))))
        elif kind == "printing":
            counts["cat"] += 1
            out.append(_li("chg", "Printing", name, _was_now(r.get("before", ""), r.get("after", ""))))
        elif kind == "clone":
            out.append(
                _li(
                    "add",
                    "Clone",
                    html.escape(str(r.get("name", ""))),
                    html.escape(f"copy of {r.get('source', '')}, {r.get('cards', 0)} cards, private"),
                )
            )
        elif kind == "new_deck":
            visibility = "private" if r.get("private") else "PUBLIC"
            out.append(
                _li(
                    "chg",
                    "New deck",
                    html.escape(str(r.get("name", ""))),
                    html.escape(f"{r.get('format', '')}, {r.get('cards', 0)} cards, {visibility}"),
                )
            )
        elif kind == "restore":
            out.append(
                _li(
                    "chg",
                    "Restore",
                    html.escape(f"snapshot {r.get('snapshot_id', '')}"),
                    html.escape(f"taken {r.get('taken', '')}, {r.get('rows', 0)} rows"),
                )
            )
        else:
            counts["chg"] += 1
            out.append(_li("chg", "Change", html.escape(row_line(r))))
    return "".join(out), counts, net


def _detail_rows(rows: list[dict[str, Any]] | None, diff: str) -> tuple[str, int]:
    """A details proposal's fields, before and after. The description is shown in full (both the
    current and the proposed text), so the user sees exactly what an apply will write."""
    items: list[str] = []
    if rows is None:
        for line in filter(None, (ln.strip() for ln in diff.splitlines())):
            items.append(_li("chg", "Change", html.escape(line)))
        return "".join(items), len(items)
    for r in rows:
        if r.get("kind") == "description":
            after = str(r.get("after_text") or "")
            before = str(r.get("before_text") or "")
            summary = f"changed, {len(after)} chars" if after else "cleared"
            full = (
                "<div class='description-change'>"
                "<p><strong>New description</strong></p>"
                f"<pre class='diff'>{html.escape(after) if after else '(empty)'}</pre>"
                "<details><summary>Current description</summary>"
                f"<pre class='diff'>{html.escape(before) if before else '(empty)'}</pre></details></div>"
            )
            items.append(_li("chg", "description", html.escape(summary) + full))
        elif r.get("kind") == "detail":
            items.append(
                _li(
                    "chg",
                    str(r.get("field", "")).replace("_", " "),
                    _was_now(r.get("before", ""), r.get("after", "")),
                )
            )
        else:
            items.append(_li("chg", "Change", html.escape(row_line(r))))
    return "".join(items), len(items)


def _proposal_body(p: dict[str, Any], csrf: str | None, shown: str = "") -> str:
    if p.get("kind") == "details":
        rows, total = _detail_rows(p.get("rows"), p["diff"])
        summary = [f"<span>{total} deck detail{'s' if total != 1 else ''} changed; no cards change</span>"]
        heading, aria = "Deck details that will change", "Deck details"
    else:
        rows, counts, net = _change_rows(p.get("rows"), p["diff"])
        total = sum(counts.values())
        summary = [f"<span>{total} change{'s' if total != 1 else ''}</span>"]
        if counts["add"]:
            summary.append(f"<span class='add'>{counts['add']} added</span>")
        if counts["del"]:
            summary.append(f"<span class='del'>{counts['del']} removed</span>")
        if counts["chg"]:
            summary.append(f"<span class='chg'>{counts['chg']} quantity changed</span>")
        if counts["cat"]:
            summary.append(f"<span class='chg'>{counts['cat']} recategorised</span>")
        summary.append(f"<span>Net {'+' if net > 0 else ''}{net} card{'s' if abs(net) != 1 else ''}</span>")
        heading, aria = "What will change", "Card changes"
    raw = "<pre class='diff'>" + html.escape(p["diff"]) + "</pre>"
    meta = (
        "<dl class='meta'>"
        f"<dt>State</dt><dd><span class='badge {_badge(p['state'])}'>{html.escape(p['state'])}</span></dd>"
        + (
            "<dt>Deck</dt><dd>Creates a new deck</dd>"
            if p.get("kind") == "create_deck" and p["deck_id"] == "new"
            else f"<dt>Deck</dt><dd><code>{html.escape(p['deck_id'])}</code></dd>"
        )
        + (
            f"<dt>Archidekt account</dt><dd>{html.escape(str(p['changes']['archidekt_username']))}</dd>"
            if p.get("kind") == "create_deck"
            and isinstance(p.get("changes"), dict)
            and p["changes"].get("archidekt_username")
            else ""
        )
        + (f"<dt>Proposed by</dt><dd>{html.escape(p['created_by'])}</dd>" if p.get("created_by") else "")
        + f"<dt>Created</dt><dd>{_when(p['created_at'])}</dd>"
        f"<dt>Expires</dt><dd>{_when(p['expires_at'])}</dd></dl>"
    )
    body = (
        f"<div class='card'>{meta}<h2>{heading}</h2>"
        f"<div class='summary'>{''.join(summary)}</div>"
        f"<ul class='changes' aria-label='{aria}'>{rows}</ul>"
        f"<details class='raw'><summary>Plain-text diff</summary>{raw}</details>"
        f"<p class='muted'>{html.escape(p['next_step'])}</p>"
    )
    if p["state"] == "pending" and p["writes_enabled"]:
        body += (
            "<div class='apply'><h2>Apply to Archidekt?</h2>"
            "<p><strong>Applying writes these changes to your deck on Archidekt.</strong></p>"
            "<p class='small'>The gateway re-checks that the deck is unchanged, keeps a snapshot of it from "
            "just before the edit, then applies and verifies the result. Nothing happens until you tap "
            "Apply.</p>"
            # Apply and Reject stay disabled until the click guard sees the page in use: a
            # connected app must not get its own proposal applied by a double-click on a page it
            # controls that swaps this window in (clickguard.py).
            + guarded_form(
                hidden={"csrf": csrf or "", **({"shown": shown} if shown else {})},
                buttons=[
                    ("apply", "Apply these changes to Archidekt", "primary btn-lg"),
                    ("reject", "Reject this proposal", "danger"),
                ],
                extra="<a class='btn' href='/proposals'>Not now, back to proposals</a>",
            )
            + "</div>"
        )
    elif p["state"] == "pending":
        body += (
            "<form method='post' class='reject'>"
            f"<input type='hidden' name='csrf' value='{html.escape(csrf or '')}'>"
            "<input type='hidden' name='action' value='reject'>"
            "<button class='btn'>Reject this proposal</button></form>"
        )
    if p.get("result"):
        url = p["result"].get("deck_url") if isinstance(p["result"], dict) else None
        if url:
            link = (
                f"<a class='btn btn-primary' href='{html.escape(url)}' rel='noreferrer'>"
                "Open the deck on Archidekt</a>"
            )
            body += f"<p>{link}</p>"
        body += f"<h2>Result</h2><pre>{html.escape(_pretty(p['result']))}</pre>"
    return body + "</div>"


def _pretty(obj: Any) -> str:
    import json

    return json.dumps(obj, indent=2, sort_keys=True)
