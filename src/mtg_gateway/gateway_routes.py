"""Gateway routes: the connection consent page, the sign-in callback, /healthz and the app config."""

from __future__ import annotations

import asyncio
import html
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import smallpage
from .app import APP_CONFIG_PATH, AppState, logger
from .auth_provider import (
    CONSENT_PATH,
    LoginError,
)
from .clickguard import form_stamp, guarded_form, submitted_too_soon
from .pages import BROWSER_CLIENT_ID
from .theme import render


def add_gateway_routes(state: AppState, h: SimpleNamespace) -> None:
    """Register the consent, sign-in callback, health and app config routes."""
    login_cookie = h.login_cookie
    s = h.s
    server = h.server

    def small(title: str, body: str, status: int = 200, **kw: object) -> Response:
        """One of the small pages (smallpage.py), in the signed-out shell."""
        return render(
            title, body, site=s.server_name, status=status, heading=False, head_extra=smallpage.HEAD, **kw
        )

    def login_error(exc: LoginError, *, assistant: bool = True) -> Response:
        # An assistant (Claude, ChatGPT, Codex) was connecting: the way forward is back in the app.
        body = smallpage.login_error(exc.kind, str(exc), assistant=assistant)
        return small(
            smallpage.login_error_title(exc.kind), body, exc.status, signin_link=exc.kind != "unavailable"
        )

    @server.custom_route(CONSENT_PATH, methods=["GET"], include_in_schema=False)
    async def consent(request: Request) -> Response:
        login_id = request.query_params.get("state", "")
        browser_key = request.cookies.get(login_cookie)
        info = await state.provider.consent_details(login_id, browser_key) if login_id else None
        if info is None:
            return small(smallpage.EXPIRED_TITLE, smallpage.link_expired_app(), 400)
        warning = ""
        if info["loopback"]:
            warning = (
                "<div class='notice warn' role='alert'><strong>Warning: this application runs on this "
                "computer</strong><br>It uses a localhost address, and anyone can claim a well-known "
                "application's name that way. Approve only if you started this sign-in yourself a moment "
                "ago.</div>"
            )
        # The member signs in at the identity provider after this page, so it cannot name their
        # own approval mode yet; it is honest about what the mode may allow.
        able = (
            "read your decks and your collection, and propose changes on your Archidekt account. "
            "Nothing reaches Archidekt until you confirm on the proposal card or the review page on "
            "this site, unless the approval mode on your account page lets it apply changes without "
            "asking (a change to one of your decks keeps a snapshot; a collection change keeps none)."
        )
        # The app's name in a tinted strip (F8); every sentence at normal size, the click-guard
        # hint the only small print; Deny stays plain (F9), Approve is the page's one orange button.
        who = (
            f"<span class='host'>identified by {html.escape(info['client_host'])}</span>"
            if info["client_host"]
            else "<span class='host'>a name the application chose for itself</span>"
        )
        body = smallpage.panel(
            "Connect an application",
            "<p>An application wants to connect to your account.</p>"
            f"<div class='notice sio-who'><span class='app'>{html.escape(info['client_name'])}</span>"
            f"{who}</div>"
            f"<dl class='ui-meta'><dt>It will be able to</dt><dd>{able}</dd>"
            f"<dt>After sign-in you go to</dt><dd><code>{html.escape(info['redirect_host'])}</code></dd></dl>"
            "<p>Approve only if you started connecting this application yourself a "
            "moment ago, and the address above is the one you expect (for example claude.ai or "
            "chatgpt.com). If someone sent you this link, choose Deny.</p>"
            f"{warning}"
            "<p>Approving opens your identity provider's sign-in page. Nothing is "
            "connected unless you approve here and sign in there. Deny leaves the application "
            "unconnected.</p>"
            # Approve and Deny stay disabled until the click guard sees the page in use (a
            # double-click on a hostile page that swaps this window in must not approve).
            + guarded_form(
                action=CONSENT_PATH,
                hidden={
                    "state": login_id,
                    "csrf": state.provider.consent_csrf(login_id, browser_key),
                    "shown": form_stamp(s.session_secret, f"consent:{login_id}"),
                },
                buttons=[("approve", "Approve and sign in", "primary"), ("deny", "Deny", "btn-plain")],
            ),
        )
        return small("Connect an application", body, form_action=info["form_action"])

    @server.custom_route(CONSENT_PATH, methods=["POST"], include_in_schema=False)
    async def consent_continue(request: Request) -> Response:
        # BodyLimitMiddleware caps this body (8 KiB) before it is read; the form is under 1 KB.
        raw = await request.body()
        data = {k: v[0] for k, v in parse_qs(raw[:4096].decode("utf-8", "replace")).items()}
        login_id = data.get("state", "")
        action = data.get("action", "")
        # The login key cookie is SameSite=Lax, so a cross-site form post arrives without it.
        browser_key = request.cookies.get(login_cookie)
        if not state.provider.check_consent_csrf(login_id, data.get("csrf"), browser_key) or action not in (
            "approve",
            "deny",
        ):
            return small(smallpage.EXPIRED_TITLE, smallpage.link_expired_app(), 403)
        if submitted_too_soon(s.session_secret, f"consent:{login_id}", data.get("shown")):
            # Faster than a person reads the page (or a stamp that is not ours): show it again.
            return RedirectResponse(
                f"{CONSENT_PATH}?{urlencode({'state': login_id})}",
                status_code=303,
                headers={"Cache-Control": "no-store"},
            )
        try:
            if action == "deny":
                target = await state.provider.deny_login(login_id, browser_key)
            else:
                target = await state.provider.continue_login(login_id, browser_key)
        except LoginError as exc:
            return login_error(exc)
        return RedirectResponse(target, status_code=303, headers={"Cache-Control": "no-store"})

    @server.custom_route("/auth/callback", methods=["GET"], include_in_schema=False)
    async def auth_callback(request: Request) -> Response:
        q = request.query_params
        client = state.db.get_login_session_client_id(q["state"]) if q.get("state") else None
        if client == BROWSER_CLIENT_ID:
            return await state.finish_browser_login(request)  # type: ignore[attr-defined]
        try:
            target = await state.provider.complete_login(
                state=q.get("state"),
                code=q.get("code"),
                error=q.get("error"),
                cookie_value=request.cookies.get(login_cookie),
            )
        except LoginError as exc:
            # With no login session left to say which flow it was, the website's words
            return login_error(exc, assistant=client is not None)
        # The login key stays: it is this browser's, and another sign-in may be pending in it.
        return RedirectResponse(target, status_code=302, headers={"Cache-Control": "no-store"})

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def healthz(_request: Request) -> Response:
        try:
            state.db.get_user("__healthcheck__")
        except Exception:  # only on a broken database file; the detail stays in the log
            logger.exception("health check: database unavailable")
            return JSONResponse({"status": "error", "detail": "database unavailable"}, status_code=503)
        # The research service (simulations, card research) and the optional Forge engine are
        # reported, not required: the gateway's own pages and Archidekt tools still work without
        # them, so an outage of either makes the status "degraded" (still HTTP 200, so the
        # container is not restarted for it). Only the status: anyone can ask, and the version and
        # the services' names would tell a stranger which project this is. The admin page's System
        # card shows each service.
        forge = state.reports.forge if state.reports is not None else None
        probes = [svc.healthy() for svc in (state.mf_proxy, forge) if svc is not None]
        # at the same time, so two slow services still answer within one probe's deadline
        down = not all(await asyncio.gather(*probes))
        return JSONResponse({"status": "degraded" if down else "ok"})

    @server.custom_route(APP_CONFIG_PATH, methods=["GET"], include_in_schema=False)
    async def app_config(_request: Request) -> Response:
        # Public: the Android app reads it at start to pin the one sign-in origin it keeps inside
        # the app (the consent page's Approve goes there, Deny goes to the client's site). Only an
        # origin the /login redirect shows anyone anyway; nothing about members or secrets.
        return JSONResponse(
            {"idp_origin": await state.provider.idp_origin()},
            headers={"Cache-Control": "public, max-age=300"},
        )
