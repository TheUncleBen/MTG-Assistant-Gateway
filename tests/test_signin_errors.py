"""When the code exchange with the identity provider fails, the sign-in page says which setting
to check and the log names the provider's error code, without showing either one's secrets."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

from mtg_gateway.oidc import OIDCClient, OIDCError
from tests.conftest import CLIENT_ID, CLIENT_SECRET, IDP, FakeIdP, Harness, make_settings, running


async def _browser_signin(h: Harness, *, code: str | None = None) -> httpx.Response:
    """Start the browser sign-in at a page that needs it and come back from the fake IdP."""
    r = await h.http.get("/login", params={"next": "/account"})
    assert r.status_code in (302, 303), r.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.idp.app)) as c:
        back = await c.get(r.headers["location"])
    assert back.status_code == 302
    url = urlparse(back.headers["location"])
    if code is not None:
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        q["code"] = code
        url = url._replace(query=urlencode(q))
    return await h.http.get(f"{url.path}?{url.query}")


@pytest.fixture
async def h(tmp_path: Path, idp: FakeIdP):
    async with running(Harness(make_settings(tmp_path), idp)) as harness:
        yield harness


async def test_wrong_client_secret_names_the_client_settings(h: Harness, caplog: pytest.LogCaptureFixture):
    h.oidc._client_secret = "not-the-secret"
    with caplog.at_level(logging.WARNING):
        r = await _browser_signin(h)
    assert r.status_code == 502
    assert "refused the gateway&#x27;s client ID or client secret" in r.text
    assert "MTG_OIDC_CLIENT_SECRET_FILE" in r.text
    assert "not-the-secret" not in r.text and CLIENT_SECRET not in r.text
    assert "rejected the code exchange (HTTP 401, invalid_client)" in caplog.text
    assert "not-the-secret" not in caplog.text


async def test_refused_code_says_try_again_and_check_redirect(h: Harness, caplog: pytest.LogCaptureFixture):
    with caplog.at_level(logging.WARNING):
        r = await _browser_signin(h, code="made-up")
    assert r.status_code == 502
    assert "refused the sign-in code" in r.text and "redirect URI" in r.text
    assert "(HTTP 400, invalid_grant)" in caplog.text


async def test_hs256_id_token_points_at_the_signing_key(
    h: Harness, idp: FakeIdP, caplog: pytest.LogCaptureFixture
):
    idp.id_token_alg = "HS256"
    with caplog.at_level(logging.WARNING):
        r = await _browser_signin(h)
    assert r.status_code == 502
    assert "has a Signing Key" in r.text
    assert "signed with HS256, which the gateway refuses" in caplog.text
    assert h.db.get_user("user-1") is None


async def test_expired_id_token_names_the_clocks(h: Harness, idp: FakeIdP, caplog: pytest.LogCaptureFixture):
    now = int(time.time())
    idp.id_token_claims = {"iat": now - 7200, "exp": now - 3600}
    with caplog.at_level(logging.WARNING):
        r = await _browser_signin(h)
    assert r.status_code == 502
    assert "clocks are right" in r.text
    assert "ID token validation failed: ExpiredTokenError" in caplog.text


async def test_provider_down_says_try_again_later(h: Harness, idp: FakeIdP):
    idp.down = True
    r = await _browser_signin(h)
    assert r.status_code == 502
    assert "no usable answer from the identity provider" in r.text


async def test_unreachable_token_endpoint_is_unreachable():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": IDP,
                    "authorization_endpoint": "https://idp.test/a",
                    "token_endpoint": "https://idp.test/t",
                    "jwks_uri": "https://idp.test/k",
                },
            )
        raise httpx.ConnectError("refused", request=req)

    c = OIDCClient(
        IDP,
        CLIENT_ID,
        CLIENT_SECRET,
        "https://mtg.test/auth/callback",
        "openid",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(OIDCError) as err:
        await c.exchange_code("code", "verifier", "nonce")
    assert err.value.reason == "unreachable"


async def test_free_text_error_codes_are_not_logged():
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/openid-configuration"):
            return httpx.Response(
                200,
                json={
                    "issuer": IDP,
                    "authorization_endpoint": "https://idp.test/a",
                    "token_endpoint": "https://idp.test/t",
                    "jwks_uri": "https://idp.test/k",
                },
            )
        return httpx.Response(400, json={"error": "<script>alert(1)</script> and more"})

    c = OIDCClient(
        IDP,
        CLIENT_ID,
        CLIENT_SECRET,
        "https://mtg.test/auth/callback",
        "openid",
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(OIDCError) as err:
        await c.exchange_code("code", "verifier", "nonce")
    assert str(err.value) == "identity provider rejected the code exchange (HTTP 400)"
    assert err.value.reason == "other"


async def test_large_authentik_id_token_signs_in(h: Harness, idp: FakeIdP):
    """A real provider's ID token can be far over joserfc's default limits: claims from a
    property mapping (here an avatar data URI and 150 groups) and a certificate chain in the
    header. 0.6.1 refused such a token with ExceededSizeError."""
    groups = [f"team-{i:03d}-{'x' * 40}" for i in range(150)] + ["mtg-users"]
    idp.user["groups"] = groups
    idp.id_token_claims = {"picture": "data:image/png;base64," + "A" * 200_000, "groups": groups}
    idp.id_token_header = {"x5c": ["M" * 1800, "M" * 1800]}
    r = await _browser_signin(h)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == "/account"
    assert h.db.get_user("user-1") is not None


async def test_absurd_id_token_is_still_refused(h: Harness, idp: FakeIdP, caplog: pytest.LogCaptureFixture):
    idp.id_token_claims = {"picture": "A" * (3 * 1024 * 1024)}
    with caplog.at_level(logging.WARNING):
        r = await _browser_signin(h)
    assert r.status_code == 502
    assert "ExceededSizeError (Payload size exceeds" in caplog.text


async def test_id_token_rules_still_refuse_other_algorithms():
    from mtg_gateway.oidc import ALLOWED_ALGS, ID_TOKEN_REGISTRY

    assert ID_TOKEN_REGISTRY.allowed == ALLOWED_ALGS
    assert "HS256" not in ALLOWED_ALGS and "none" not in ALLOWED_ALGS
