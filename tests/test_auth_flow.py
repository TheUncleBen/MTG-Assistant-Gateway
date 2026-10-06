from __future__ import annotations

import json
import re
import time
from urllib.parse import parse_qs, urlparse

import pytest

from tests.conftest import GATEWAY, FakeIdP, Harness, make_settings, running, sse_json


async def test_metadata_advertises_endpoints(gw: Harness):
    r = await gw.http.get("/.well-known/oauth-authorization-server")
    assert r.status_code == 200
    m = r.json()
    assert m["issuer"] == GATEWAY
    assert m["authorization_endpoint"] == f"{GATEWAY}/authorize"
    assert m["token_endpoint"] == f"{GATEWAY}/token"
    assert m["registration_endpoint"] == f"{GATEWAY}/register"
    assert "S256" in m["code_challenge_methods_supported"]

    pr = await gw.http.get("/.well-known/oauth-protected-resource/mcp")
    assert pr.status_code == 200
    assert pr.json()["resource"] == f"{GATEWAY}/mcp"
    assert pr.json()["authorization_servers"] == [GATEWAY]


async def test_mcp_requires_bearer_token(gw: Harness):
    r = await gw.mcp(None, "initialize")
    assert r.status_code == 401
    assert "resource_metadata=" in r.headers["www-authenticate"]
    r = await gw.mcp("not-a-real-token", "initialize")
    assert r.status_code == 401


async def test_full_login_and_whoami(gw: Harness, idp: FakeIdP):
    client = await gw.register()
    tokens = await gw.tokens_for(client)
    assert tokens["token_type"] == "Bearer"
    assert tokens["refresh_token"]

    # The gateway authenticated to the IdP as a confidential client with PKCE.
    call = idp.token_calls[-1]
    assert call["client_id"] == "gateway-client" and call["code_verifier"]

    r = await gw.mcp(
        tokens["access_token"],
        "initialize",
        {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}},
    )
    assert r.status_code == 200, r.text

    r = await gw.mcp(tokens["access_token"], "tools/call", {"name": "whoami", "arguments": {}}, rid=2)
    assert r.status_code == 200, r.text
    msg = sse_json(r)
    assert "error" not in msg, msg
    result = msg["result"]
    assert result.get("isError") is not True, result
    sc = result["structuredContent"]
    assert sc["sub"] == "user-1"
    assert sc["email"] == "alice@example.test"
    assert sc["groups"] == ["mtg-users"]
    assert sc["client_id"] == client["client_id"]


async def test_two_users_get_distinct_identities(gw: Harness, idp: FakeIdP):
    client = await gw.register()
    t1 = await gw.tokens_for(client)
    idp.user = {**idp.user, "sub": "user-2", "email": "bob@example.test", "name": "Bob", "groups": []}
    t2 = await gw.tokens_for(client)
    r1 = sse_json(await gw.mcp(t1["access_token"], "tools/call", {"name": "whoami", "arguments": {}}))
    r2 = sse_json(await gw.mcp(t2["access_token"], "tools/call", {"name": "whoami", "arguments": {}}))
    assert r1["result"]["structuredContent"]["sub"] == "user-1"
    assert r2["result"]["structuredContent"]["sub"] == "user-2"


async def test_code_is_single_use_and_pkce_checked(gw: Harness):
    client = await gw.register()
    code, verifier = await gw.full_login(client)
    bad = await gw.token(
        client,
        grant_type="authorization_code",
        code=code,
        code_verifier="wrong" * 10,
        redirect_uri="https://client.test/cb",
    )
    assert bad.status_code == 400 and bad.json()["error"] == "invalid_grant"
    ok = await gw.token(
        client,
        grant_type="authorization_code",
        code=code,
        code_verifier=verifier,
        redirect_uri="https://client.test/cb",
    )
    assert ok.status_code == 200
    replay = await gw.token(
        client,
        grant_type="authorization_code",
        code=code,
        code_verifier=verifier,
        redirect_uri="https://client.test/cb",
    )
    assert replay.status_code == 400 and replay.json()["error"] == "invalid_grant"


async def test_refresh_rotates_and_revokes_old_family(gw: Harness):
    client = await gw.register()
    t1 = await gw.tokens_for(client)
    r = await gw.token(client, grant_type="refresh_token", refresh_token=t1["refresh_token"])
    assert r.status_code == 200
    t2 = r.json()
    assert t2["access_token"] != t1["access_token"]
    # Old access token and old refresh token are dead; the new pair works.
    assert (await gw.mcp(t1["access_token"], "tools/list")).status_code == 401
    assert (await gw.mcp(t2["access_token"], "tools/list")).status_code == 200
    again = await gw.token(client, grant_type="refresh_token", refresh_token=t1["refresh_token"])
    assert again.status_code == 400
    # Replaying the old refresh token also revokes t2: see test_refresh_reuse_revokes_the_successor_family.


async def test_revocation_endpoint(gw: Harness):
    client = await gw.register()
    t = await gw.tokens_for(client)
    r = await gw.http.post(
        "/revoke",
        data={
            "token": t["access_token"],
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )
    assert r.status_code == 200
    assert (await gw.mcp(t["access_token"], "tools/list")).status_code == 401


async def test_callback_requires_browser_cookie(gw: Harness):
    client = await gw.register()
    r, _ = await gw.start_login(client)
    assert r.status_code == 302
    assert "mtg_login=" in r.headers.get("set-cookie", "")
    cb = await gw.idp_leg(r)
    r2 = await gw.callback(cb, with_cookie=False)
    assert r2.status_code == 403


async def test_callback_state_is_single_use(gw: Harness):
    client = await gw.register()
    r, _ = await gw.start_login(client)
    cb = await gw.idp_leg(r)
    first = await gw.callback(cb)
    assert first.status_code == 302
    second = await gw.callback(cb)
    assert second.status_code == 400


async def test_idp_denial_is_relayed_to_client(gw: Harness):
    client = await gw.register()
    r, _ = await gw.start_login(client)
    cb = await gw.idp_leg(r)
    state = parse_qs(urlparse(cb).query)["state"][0]
    r2 = await gw.callback(f"/auth/callback?state={state}&error=access_denied")
    assert r2.status_code == 302
    q = parse_qs(urlparse(r2.headers["location"]).query)
    assert q["error"] == ["access_denied"] and q["state"] == ["cs1"]


async def test_unknown_resource_is_rejected(gw: Harness):
    client = await gw.register()
    r, _ = await gw.start_login(client, resource="https://other.test/mcp")
    # A page, not a redirect: a registered redirect URI can be anyone's site (no open redirect).
    assert r.status_code == 400 and "location" not in r.headers
    assert "invalid_target" in r.text and "client.test" in r.text


async def test_unregistered_redirect_uri_rejected(gw: Harness):
    client = await gw.register()
    r, _ = await gw.start_login(client, redirect_uri="https://evil.test/cb")
    assert r.status_code == 400


async def test_required_group_enforced(tmp_path, idp: FakeIdP):
    settings = make_settings(tmp_path, required_group="mtg-users")
    async with running(Harness(settings, idp)) as h:
        client = await h.register()
        await h.tokens_for(client)  # user-1 is in mtg-users
        idp.user = {**idp.user, "sub": "user-3", "groups": ["other"]}
        r, _ = await h.start_login(client)
        cb = await h.idp_leg(r)
        r2 = await h.callback(cb)
        assert r2.status_code == 403


async def test_refused_sign_in_cuts_off_a_removed_member(tmp_path, idp: FakeIdP):
    settings = make_settings(tmp_path, required_group="mtg-users")
    async with running(Harness(settings, idp)) as h:
        client = await h.register()
        t = await h.tokens_for(client)
        assert (await h.mcp(t["access_token"], "tools/list")).status_code == 200
        # Removed from the group at the IdP, the same person signs in again and is refused...
        idp.user = {**idp.user, "groups": ["other"]}
        r, _ = await h.start_login(client)
        assert (await h.callback(await h.idp_leg(r))).status_code == 403
        # ...and the apps they connected earlier lose access at once.
        assert (await h.mcp(t["access_token"], "tools/list")).status_code == 401
        assert h.db.get_user("user-1")["groups"] == ["other"]


async def test_wrong_host_header_rejected_on_mcp(gw: Harness):
    client = await gw.register()
    t = await gw.tokens_for(client)
    r = await gw.http.post(
        "/mcp",
        headers={
            "Host": "attacker.test",
            "Authorization": f"Bearer {t['access_token']}",
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        },
        content='{"jsonrpc":"2.0","id":1,"method":"tools/list"}',
    )
    assert r.status_code == 421


async def test_health_and_index(gw: Harness):
    assert (await gw.http.get("/healthz")).json()["status"] == "ok"
    r = await gw.http.get("/")  # the dashboard needs a browser sign-in (tests/test_page_gate.py)
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/"


@pytest.mark.parametrize("path", ["/mcp"])
async def test_id_token_cannot_be_used_as_access_token(gw: Harness, idp: FakeIdP, path: str):
    """A token from the identity provider is not a gateway token."""
    import time

    from joserfc import jwt

    now = int(time.time())
    idp_token = jwt.encode(
        {"alg": "RS256", "kid": "test-1"},
        {
            "iss": "https://idp.test/application/o/mtg",
            "aud": "gateway-client",
            "sub": "user-1",
            "exp": now + 300,
            "iat": now,
        },
        idp.key,
    )
    r = await gw.mcp(idp_token, "tools/list")
    assert r.status_code == 401


async def test_register_rejects_dangerous_redirect_uris(gw: Harness):
    for bad in ("javascript:alert(1)", "http://evil.example/cb", "data:text/html,x"):
        r = await gw.http.post("/register", json={"redirect_uris": [bad], "client_name": "x"})
        assert r.status_code == 400, (bad, r.text)
        assert r.json()["error"] in ("invalid_redirect_uri", "invalid_client_metadata"), (bad, r.text)
    # https anywhere and http on loopback (RFC 8252 native apps) are fine.
    await gw.register("https://client.test/cb")
    await gw.register("http://127.0.0.1:49152/cb")
    await gw.register("http://localhost/cb")


async def test_client_secret_is_stored_hashed_and_still_checked(gw: Harness):
    client = await gw.register()
    row = gw.db.get_client(client["client_id"])
    assert "client_secret" not in row
    assert row["client_secret_hash"] and client["client_secret"] not in json.dumps(row)
    # The right secret works, a wrong one does not, and the registration still returned it once.
    code, verifier = await gw.full_login(client)
    wrong = {**client, "client_secret": "nope"}
    r = await gw.token(
        wrong,
        grant_type="authorization_code",
        code=code,
        code_verifier=verifier,
        redirect_uri="https://client.test/cb",
    )
    assert r.status_code == 401, r.text
    assert (await gw.tokens_for(client))["access_token"]


def test_unused_clients_are_purged(tmp_path):
    from mtg_gateway.db import Database

    db = Database(tmp_path / "t.sqlite")
    old = int(time.time()) - 8 * 86400
    db.save_client("stale", {"client_id": "stale"})
    db.save_client("live", {"client_id": "live"})
    db.save_client("fresh", {"client_id": "fresh"})
    with db.tx() as c:
        c.execute("UPDATE oauth_clients SET created_at = ? WHERE client_id IN ('stale', 'live')", (old,))
    db.save_token(
        "tok",
        kind="refresh",
        client_id="live",
        sub="u",
        scopes=[],
        resource=None,
        family="f",
        expires_at=int(time.time()) + 3600,
    )
    db.purge_expired()
    assert db.get_client("stale") is None
    assert db.get_client("live") and db.get_client("fresh")
    db.close()


async def test_omitted_scope_gets_the_registered_default(gw: Harness):
    client = await gw.register()
    code, verifier = await gw.full_login(client, scope=None)
    r = await gw.token(
        client,
        grant_type="authorization_code",
        code=code,
        code_verifier=verifier,
        redirect_uri="https://client.test/cb",
    )
    assert r.status_code == 200, r.text
    assert r.json()["scope"] == "mtg"
    r = await gw.mcp(r.json()["access_token"], "tools/call", {"name": "whoami", "arguments": {}}, rid=2)
    assert sse_json(r)["result"]["structuredContent"]["scopes"] == ["mtg"]


async def test_refresh_reuse_revokes_the_successor_family(gw: Harness):
    client = await gw.register()
    t1 = await gw.tokens_for(client)
    t2 = (await gw.token(client, grant_type="refresh_token", refresh_token=t1["refresh_token"])).json()
    assert (await gw.mcp(t2["access_token"], "tools/list")).status_code == 200
    # Replaying the rotated-out refresh token is reuse: everything descended from it dies too.
    again = await gw.token(client, grant_type="refresh_token", refresh_token=t1["refresh_token"])
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert (await gw.mcp(t2["access_token"], "tools/list")).status_code == 401
    assert (
        await gw.token(client, grant_type="refresh_token", refresh_token=t2["refresh_token"])
    ).status_code == 400


async def test_refresh_rechecks_group_and_sign_in_age(tmp_path, idp: FakeIdP):
    """A refresh is refused, and the token family revoked, once the user's recorded groups no
    longer hold the required group or their last sign-in is older than MTG_REAUTH_INTERVAL."""
    settings = make_settings(tmp_path, required_group="mtg-users", reauth_interval=3600)
    async with running(Harness(settings, idp)) as h:
        client = await h.register()
        t1 = await h.tokens_for(client)
        ok = await h.token(client, grant_type="refresh_token", refresh_token=t1["refresh_token"])
        assert ok.status_code == 200
        t2 = ok.json()
        # The operator (or a later sign-in) recorded the user without the group.
        with h.db.tx() as c:
            c.execute("UPDATE users SET groups_json = '[\"other\"]' WHERE sub = 'user-1'")
        r = await h.token(client, grant_type="refresh_token", refresh_token=t2["refresh_token"])
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        assert (await h.mcp(t2["access_token"], "tools/list")).status_code == 401  # family revoked

        # A fresh sign-in (user-1 is in mtg-users again) works until it is older than the interval.
        t3 = await h.tokens_for(client)
        assert (
            await h.token(client, grant_type="refresh_token", refresh_token=t3["refresh_token"])
        ).status_code == 200
        t4 = await h.tokens_for(client)
        with h.db.tx() as c:
            c.execute("UPDATE tokens SET auth_time = auth_time - 3601")
        # Signing in again with another app does not extend this app's sign-in.
        await h.tokens_for(await h.register())
        r = await h.token(client, grant_type="refresh_token", refresh_token=t4["refresh_token"])
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        with h.db.tx() as c:
            rows = c.execute(
                "SELECT detail_json FROM audit_log WHERE event = 'refresh_rejected' ORDER BY id"
            ).fetchall()
        assert [json.loads(r[0])["reason"] for r in rows] == ["group", "reauth_due"]


async def test_registered_client_needs_consent_before_the_idp(gw: Harness, idp: FakeIdP):
    """Open registration plus the IdP's silent consent must not hand a code to whoever registered:
    the gateway's own consent page, naming the client and the host the code goes to, comes first."""
    calls_before = len(idp.token_calls)
    evil = await gw.register(redirect_uri="https://evil.example/cb", client_name="Claude")
    r, _verifier = await gw.start_login(evil, redirect_uri="https://evil.example/cb")
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(f"{GATEWAY}/authorize/confirm?state="), loc  # not straight to the IdP
    login_id = parse_qs(urlparse(loc).query)["state"][0]
    page = await gw.http.get("/authorize/confirm", params={"state": login_id})
    assert page.status_code == 200
    assert "evil.example" in page.text and "chose for itself" in page.text
    assert page.headers["x-frame-options"] == "DENY"
    # Without Approve no code exists: the IdP was never visited and nothing reached the client.
    assert len(idp.token_calls) == calls_before
    deny_csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page.text).group(1)
    deny = await gw.http.post(
        "/authorize/confirm", data={"state": login_id, "csrf": deny_csrf, "action": "deny"}
    )
    assert parse_qs(urlparse(deny.headers["location"]).query)["error"] == ["access_denied"]


async def test_consent_page_refuses_browser_logins_and_foreign_tokens(gw: Harness):
    # A browser-page login (no MCP client) never has a consent page.
    r = await gw.http.get("/login", params={"next": "/account"})
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    assert (await gw.http.get("/authorize/confirm", params={"state": state})).status_code == 400
    key = gw.http.cookies.get("__Host-mtg_login")
    csrf = gw.app.state.gateway.provider.consent_csrf(state, key)
    post = await gw.http.post("/authorize/confirm", data={"state": state, "csrf": csrf, "action": "approve"})
    assert post.status_code == 400
    # The form token of one login does not approve another.
    client = await gw.register()
    a, _ = await gw.start_login(client)
    b, _ = await gw.start_login(client)
    sa = parse_qs(urlparse(a.headers["location"]).query)["state"][0]
    sb = parse_qs(urlparse(b.headers["location"]).query)["state"][0]
    wrong = gw.app.state.gateway.provider.consent_csrf(sa, gw.http.cookies.get("__Host-mtg_login"))
    post = await gw.http.post("/authorize/confirm", data={"state": sb, "csrf": wrong, "action": "approve"})
    assert post.status_code == 403


async def test_registered_client_name_is_trimmed(gw: Harness):
    c = await gw.register(client_name="Claude‮" + "x" * 300)
    stored = gw.db.get_client(c["client_id"])["client_name"]
    assert len(stored) == 60 and "‮" not in stored


async def test_replayed_code_revokes_the_tokens_it_gave(gw: Harness):
    client = await gw.register()
    code, verifier = await gw.full_login(client)
    form = dict(
        grant_type="authorization_code",
        code=code,
        code_verifier=verifier,
        redirect_uri="https://client.test/cb",
    )
    first = await gw.token(client, **form)
    assert first.status_code == 200
    assert (await gw.token(client, **form)).status_code == 400
    assert (await gw.mcp(first.json()["access_token"], "tools/list")).status_code == 401
