"""Open dynamic client registration keeps what one anonymous client may store small."""

from __future__ import annotations

from tests.conftest import Harness


async def test_registration_metadata_is_bounded(gw: Harness):
    # Too many redirect URIs, or one absurdly long one, is refused outright.
    many = [f"https://client.test/cb{i}" for i in range(11)]
    r = await gw.http.post("/register", json={"redirect_uris": many, "client_name": "x"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri", r.text
    long = "https://client.test/" + "a" * 2000
    r = await gw.http.post("/register", json={"redirect_uris": [long], "client_name": "x"})
    assert r.status_code == 400 and r.json()["error"] == "invalid_redirect_uri", r.text
    # Free-text metadata nothing uses is dropped, both from the row and from the echo.
    c = await gw.register(
        contacts=["x" * 100] * 50,
        client_uri="https://client.test/" + "u" * 1000,
        logo_uri="https://client.test/logo.png",
        software_id="s" * 2_000,
    )
    stored = gw.db.get_client(c["client_id"])
    for key in ("contacts", "client_uri", "logo_uri", "software_id"):
        assert key not in c and key not in stored, key
    # Whatever is left must still be small.
    r = await gw.http.post(
        "/register",
        json={
            "redirect_uris": ["https://client.test/cb"],
            "client_name": "x",
            "grant_types": ["authorization_code"] + ["g" * 100] * 100,
        },
    )
    assert r.status_code == 400 and r.json()["error"] == "invalid_client_metadata", r.text


async def test_typical_assistant_registrations_still_work(gw: Harness):
    """Claude- and ChatGPT-shaped registrations: a couple of redirect URIs and standard fields."""
    for body in (
        {
            "redirect_uris": [
                "https://claude.ai/api/mcp/auth_callback",
                "https://claude.com/api/mcp/auth_callback",
            ],
            "client_name": "Claude",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "client_secret_post",
            "scope": "mtg",
        },
        {
            "redirect_uris": ["https://chatgpt.com/connector_platform_oauth_redirect"],
            "client_name": "ChatGPT",
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "client_uri": "https://chatgpt.com",
        },
    ):
        r = await gw.http.post("/register", json=body)
        assert r.status_code == 201, r.text
        c = r.json()
        assert c["redirect_uris"] == body["redirect_uris"] and c["client_name"] == body["client_name"]
        assert gw.db.get_client(c["client_id"])["redirect_uris"] == body["redirect_uris"]


async def test_oversized_registration_body_is_refused_before_parsing(gw):
    big = b'{"redirect_uris": ["https://client.test/cb"], "client_name": "' + b"x" * 40_000 + b'"}'
    r = await gw.http.post("/register", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413
