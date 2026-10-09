"""Browser-only paths a redirect-following HTTP client would get wrong: the consent page for a
client that identifies by URL (Client ID Metadata Document), Approve and Deny, and signing out.

Every step here runs in Chromium, so a page whose Content-Security-Policy or markup stops the
browser from following a redirect fails the test, as it would fail a person.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .conftest import PUBLIC_URL, Env, authentik_sign_in, browser_page, click_guarded, gateway_browser_sign_in
from .test_03_tokens import LOOPBACK, MCP_URL, mcp_tools_list, pkce

CIMD_CLIENT_ID = "https://cimd.e2e.test/claude-e2e.json"


def run(coro):
    return asyncio.run(coro)


def authorize_url(challenge: str, state: str) -> str:
    q = {
        "client_id": CIMD_CLIENT_ID,
        "redirect_uri": LOOPBACK,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": "mtg",
        "resource": MCP_URL,
    }
    return f"{PUBLIC_URL}/authorize?{urlencode(q)}"


async def consent_page(page, url: str) -> str:
    """Open /authorize for the CIMD client and return the consent page's text."""
    await page.goto(url)
    await page.wait_for_url(lambda u: u.startswith(f"{PUBLIC_URL}/authorize/confirm"), timeout=30_000)
    text = await page.inner_text("body")
    assert "E2E CIMD Assistant" in text and "cimd.e2e.test" in text, text
    assert "127.0.0.1" in text and "Warning" in text, text  # loopback redirect: the spec's warning
    return text


def loopback_hits(page) -> list[str]:
    hits: list[str] = []
    page.on("request", lambda req: hits.append(req.url) if req.url.startswith(LOOPBACK) else None)
    return hits


async def wait_for(hits: list[str], page) -> dict[str, str]:
    for _ in range(60):
        if hits:
            return {k: v[0] for k, v in parse_qs(urlparse(hits[0]).query).items()}
        await page.wait_for_timeout(500)
    raise AssertionError(f"the browser never reached the client's redirect; it is at {page.url!r}")


def test_cimd_client_approve_in_the_browser_then_token_and_tools(env: Env, http: httpx.Client):
    verifier, challenge = pkce()

    async def go() -> dict[str, str]:
        async with browser_page() as page:
            hits = loopback_hits(page)
            await consent_page(page, authorize_url(challenge, "cimd-approve"))
            # Approve: the gateway answers the form POST with a redirect to Authentik (another
            # origin). A page policy that forbids that leaves the browser on the consent page.
            await click_guarded(page, "button[name=action][value=approve]")
            await page.wait_for_url(
                lambda u: u.startswith(env.issuer.split("/application/")[0]), timeout=30_000
            )
            await authentik_sign_in(page, "alice-test", env.users["alice-test"]["password"])
            return await wait_for(hits, page)

    params = run(go())
    assert params.get("state") == "cimd-approve" and "code" in params, params
    # Public client: no secret, PKCE only, client_id is the document URL.
    r = http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CIMD_CLIENT_ID,
            "code": params["code"],
            "redirect_uri": LOOPBACK,
            "code_verifier": verifier,
        },
    )
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert tokens["token_type"] == "Bearer" and tokens.get("refresh_token")
    listed = mcp_tools_list(http, tokens["access_token"])
    assert listed.status_code == 200 and "whoami" in listed.text and "get_deck" in listed.text, listed.text[
        :300
    ]


def test_cimd_client_deny_in_the_browser_returns_access_denied(env: Env, http: httpx.Client):
    _verifier, challenge = pkce()

    async def go() -> tuple[dict[str, str], str, int]:
        async with browser_page() as page:
            hits = loopback_hits(page)
            text = await consent_page(page, authorize_url(challenge, "cimd-deny"))
            assert "Deny" in text, text
            consent_url = page.url
            await click_guarded(page, "button[name=action][value=deny]")
            params = await wait_for(hits, page)
            # The pending login is gone: the consent link is dead afterwards. (A fresh tab, because
            # the first one is still landing on the client's loopback address, which nobody serves.)
            tab = await page.context.new_page()
            again = await tab.goto(consent_url)
            return params, await tab.inner_text("body"), again.status

    params, after, status = run(go())
    assert params.get("error") == "access_denied" and params.get("state") == "cimd-deny", params
    assert "code" not in params
    assert status == 400 and "expired, was already used" in after, after


def test_cimd_rejects_a_document_that_is_not_json(http: httpx.Client):
    _verifier, challenge = pkce()
    q = {
        "client_id": "https://cimd.e2e.test/not-json.json",
        "redirect_uri": LOOPBACK,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "x",
    }
    r = http.get(f"/authorize?{urlencode(q)}")
    assert r.status_code == 400 and r.json()["error"] == "invalid_request", r.text
    assert "not found" in r.json()["error_description"], r.text  # the document was rejected, so no client


def test_sign_out_in_the_browser_ends_the_session_and_clears_site_data(env: Env):
    async def go():
        async with browser_page() as page:
            await gateway_browser_sign_in(page, env, "alice-test", "/account")
            # The account page carries its own Sign out button (the one in the account menu is
            # hidden until the menu opens).
            assert "Sign out" in await page.inner_text("main")
            async with page.expect_response(lambda r: r.url == f"{PUBLIC_URL}/logout") as info:
                await page.click("main >> text=Sign out")
            logout = await info.value
            assert logout.status == 303
            assert (await logout.header_value("clear-site-data")) == '"storage"'
            await page.wait_for_url(f"{PUBLIC_URL}/signed-out", timeout=30_000)
            assert "Sign out" not in await page.inner_text("body")
            # The session is gone on the server too: the account page sends the browser through
            # /login again (Authentik may still remember the person and sign them straight back in,
            # which is its single sign-on doing its job; what matters is that the gateway asked).
            resp = await page.goto(f"{PUBLIC_URL}/account")
            chain, req = [], resp.request
            while req is not None:
                chain.append(req.url)
                req = req.redirected_from
            assert any(u.startswith(f"{PUBLIC_URL}/login") for u in chain), chain

    run(go())
