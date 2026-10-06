"""Token lifecycle and OAuth edge cases, using raw HTTP like a misbehaving client would."""

from __future__ import annotations

import base64
import hashlib
import secrets
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
from playwright.async_api import async_playwright

from .conftest import CHROMIUM, MCP_URL, PUBLIC_URL, Env, approve_gateway_consent, authentik_sign_in

LOOPBACK = "http://127.0.0.1:18766/cb"


def mcp_tools_list(http: httpx.Client, token: str) -> httpx.Response:
    return http.post(
        "/mcp",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )


def register(http: httpx.Client, redirect_uri: str = LOOPBACK) -> dict:
    r = http.post("/register", json={"client_name": "raw e2e client", "redirect_uris": [redirect_uri]})
    assert r.status_code == 201, r.text
    return r.json()


def pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


async def browser_authorize(env: Env, username: str, authorize_url: str) -> dict[str, str]:
    """Open the gateway's /authorize in a real browser, sign in, return the loopback query."""
    kwargs: dict = {"args": ["--no-proxy-server"]}
    if CHROMIUM:
        kwargs["executable_path"] = CHROMIUM
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**kwargs)
        context = await browser.new_context(ignore_https_errors=True)
        page = await context.new_page()
        # The loopback listener does not exist here; the navigation error is fine, the URL is what matters.
        captured: list[str] = []
        page.on("request", lambda req: captured.append(req.url) if req.url.startswith(LOOPBACK) else None)
        await page.goto(authorize_url)
        await approve_gateway_consent(page)
        await authentik_sign_in(page, username, env.users[username]["password"])
        for _ in range(60):
            if captured:
                break
            await page.wait_for_timeout(500)
        await browser.close()
    assert captured, "browser never reached the loopback redirect"
    return {k: v[0] for k, v in parse_qs(urlparse(captured[0]).query).items()}


@pytest.fixture
async def code_and_client(env: Env, http: httpx.Client):
    client = register(http)
    verifier, challenge = pkce()
    q = {
        "client_id": client["client_id"],
        "redirect_uri": LOOPBACK,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "raw-state-1",
        "scope": "mtg",
        "resource": MCP_URL,
    }
    params = await browser_authorize(env, "alice-test", f"{PUBLIC_URL}/authorize?{urlencode(q)}")
    assert params.get("state") == "raw-state-1"
    assert "code" in params, params
    return client, params["code"], verifier


def exchange(http: httpx.Client, client: dict, **form: str) -> httpx.Response:
    return http.post(
        "/token", data={"client_id": client["client_id"], "client_secret": client["client_secret"], **form}
    )


async def test_pkce_single_use_code_refresh_and_revocation(http: httpx.Client, code_and_client):
    client, code, verifier = code_and_client

    # Wrong PKCE verifier: refused, and the code survives for the right one.
    bad = exchange(
        http,
        client,
        grant_type="authorization_code",
        code=code,
        redirect_uri=LOOPBACK,
        code_verifier="x" * 43,
    )
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant", bad.text

    good = exchange(
        http,
        client,
        grant_type="authorization_code",
        code=code,
        redirect_uri=LOOPBACK,
        code_verifier=verifier,
    )
    assert good.status_code == 200, good.text
    tokens = good.json()
    assert tokens["token_type"].lower() == "bearer" and tokens["expires_in"] == 3600
    access, refresh = tokens["access_token"], tokens["refresh_token"]
    assert mcp_tools_list(http, access).status_code == 200

    # Refresh rotates: new pair works, old access and old refresh are dead.
    r = exchange(http, client, grant_type="refresh_token", refresh_token=refresh)
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["access_token"] != access and new["refresh_token"] != refresh
    assert mcp_tools_list(http, new["access_token"]).status_code == 200
    assert mcp_tools_list(http, access).status_code == 401
    reuse = exchange(http, client, grant_type="refresh_token", refresh_token=refresh)
    assert reuse.status_code == 400 and reuse.json()["error"] == "invalid_grant"

    # Another registered client cannot use this client's refresh token.
    other = register(http)
    stolen = exchange(http, other, grant_type="refresh_token", refresh_token=new["refresh_token"])
    assert stolen.status_code == 400, stolen.text

    # Replaying the code fails, and revokes the tokens it gave (the code leaked).
    replay = exchange(
        http,
        client,
        grant_type="authorization_code",
        code=code,
        redirect_uri=LOOPBACK,
        code_verifier=verifier,
    )
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"
    assert mcp_tools_list(http, new["access_token"]).status_code == 401

    # Revocation kills the family (already revoked here; revoking again is still a 200).
    rv = http.post(
        "/revoke",
        data={
            "token": new["access_token"],
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )
    assert rv.status_code == 200, rv.text
    assert mcp_tools_list(http, new["access_token"]).status_code == 401
    assert (
        exchange(http, client, grant_type="refresh_token", refresh_token=new["refresh_token"]).status_code
        == 400
    )


def test_authorize_rejects_foreign_resource_and_unregistered_redirect(http: httpx.Client):
    client = register(http)
    _, challenge = pkce()
    base = {
        "client_id": client["client_id"],
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "s",
    }
    # Token for another resource server: refused before the user is even sent to Authentik.
    r = http.get(
        "/authorize", params={**base, "redirect_uri": LOOPBACK, "resource": "https://evil.example/mcp"}
    )
    # Shown as a page, not redirected: a registered redirect URI can be anyone's site.
    assert r.status_code == 400 and "location" not in r.headers, r.text
    assert "invalid_target" in r.text
    # Redirect URI the client never registered.
    r = http.get("/authorize", params={**base, "redirect_uri": "https://attacker.example/cb"})
    assert r.status_code == 400, r.text
    # Missing PKCE.
    r = http.get(
        "/authorize",
        params={"client_id": client["client_id"], "response_type": "code", "redirect_uri": LOOPBACK},
    )
    assert r.status_code == 400 and "location" not in r.headers


def test_callback_refuses_a_replayed_or_foreign_browser(http: httpx.Client):
    r = http.get("/auth/callback", params={"state": "never-issued", "code": "x"})
    assert r.status_code == 400 and "expired or was already used" in r.text
    # Cookie check happens only for a live state, which is covered by the unit tests and the
    # browser flow; here we just confirm no cookie is ever set on the callback itself.
    assert "set-cookie" not in r.headers


def test_registration_requires_redirect_uris(http: httpx.Client):
    r = http.post("/register", json={"client_name": "no redirects"})
    assert r.status_code == 400, r.text
    # Finding (reported to the gateway owner): the SDK's registration handler accepts any URL
    # scheme, so a javascript: redirect URI registers. Browsers ignore javascript: in a
    # Location header, so this is low risk, but https or loopback http should be required.
    r = http.post("/register", json={"client_name": "x", "redirect_uris": ["javascript:alert(1)"]})
    if r.status_code == 201:
        pytest.xfail("gateway accepts a javascript: redirect_uri at registration (reported)")
    assert r.status_code == 400, r.text
