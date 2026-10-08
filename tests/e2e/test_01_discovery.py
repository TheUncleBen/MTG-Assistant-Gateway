"""What an MCP client sees before anyone signs in: discovery, bearer challenge, anonymous surface."""

from __future__ import annotations

from pathlib import Path

import httpx

from .conftest import MCP_URL, PUBLIC_URL

# The version the gateway reports is the repository's VERSION file (docs/VERSIONS.md).
VERSION = (Path(__file__).resolve().parents[2] / "VERSION").read_text().strip()


def test_protected_resource_metadata_points_at_the_gateway(http: httpx.Client):
    r = http.get("/.well-known/oauth-protected-resource/mcp")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["resource"] == MCP_URL
    assert body["authorization_servers"] == [PUBLIC_URL]
    assert body["bearer_methods_supported"] == ["header"]


def test_authorization_server_metadata(http: httpx.Client):
    r = http.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200, r.text
    m = r.json()
    assert m["issuer"] == PUBLIC_URL
    for key in ("authorization_endpoint", "token_endpoint", "registration_endpoint", "revocation_endpoint"):
        assert m[key].startswith(PUBLIC_URL + "/"), key
    assert m["code_challenge_methods_supported"] == ["S256"]
    assert set(m["grant_types_supported"]) == {"authorization_code", "refresh_token"}
    assert "client_secret_post" in m["token_endpoint_auth_methods_supported"]


def test_mcp_without_token_gets_bearer_challenge(http: httpx.Client):
    for method in ("GET", "POST"):
        r = http.request(method, "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        assert r.status_code == 401, (method, r.text)
        challenge = r.headers["www-authenticate"]
        assert challenge.startswith("Bearer ")
        assert 'error="invalid_token"' in challenge
        assert f'resource_metadata="{PUBLIC_URL}/.well-known/oauth-protected-resource/mcp"' in challenge


def test_garbage_token_is_rejected(http: httpx.Client):
    r = http.post(
        "/mcp",
        headers={"Authorization": "Bearer not-a-real-token", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
    )
    assert r.status_code == 401
    assert 'error="invalid_token"' in r.headers["www-authenticate"]


def test_anonymous_surface_is_only_what_the_docs_promise(http: httpx.Client):
    """Nothing but health, discovery and the OAuth entry points answers anonymously."""
    expected = {
        "/": 302,  # the dashboard sends a browser to sign in
        "/healthz": 200,
        "/.well-known/oauth-authorization-server": 200,
        "/.well-known/oauth-protected-resource/mcp": 200,
        "/mcp": 401,
        "/authorize": 400,  # needs client_id etc.
        "/register": 405,  # POST only
        "/token": 405,
        "/revoke": 405,
        "/auth/callback": 400,  # needs state
        "/admin": 404,
        "/data": 404,
        "/backups": 404,
        "/mtg-gateway.sqlite": 404,
        "/metrics": 404,
        "/docs": 404,
        "/openapi.json": 404,
    }
    got = {path: http.get(path).status_code for path in expected}
    assert got == expected


def test_health_and_landing_page_leak_nothing(http: httpx.Client):
    # The deployed stack runs Mystic Forge next to the gateway, so the research service is up too.
    assert http.get("/healthz").json() == {"status": "ok", "version": VERSION, "mystic_forge": "ok"}
    landing = http.get("/")
    assert landing.status_code == 302 and landing.headers["location"] == "/login?next=/"
    assert "auth.e2e.test" not in landing.text  # the identity provider is not advertised


def test_unknown_host_header_is_refused_at_the_edge(env):
    """NPM only routes configured hostnames; the stand-in edge does the same (connection closed)."""
    with httpx.Client(verify=env.ssl_context, trust_env=False) as c:
        try:
            r = c.get(f"{PUBLIC_URL}/healthz", headers={"Host": "other.e2e.test"})
        except httpx.HTTPError:
            return  # nginx returned 444 (closed without a response): refused
        assert r.status_code >= 400
