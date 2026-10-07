"""Removing someone at the identity provider takes effect on their next request (membership.py).

The provider never tells the gateway (Authentik keeps honouring a removed member's refresh token),
so the gateway asks its userinfo endpoint before serving any request with a session or a token."""

from __future__ import annotations

from pathlib import Path

from .conftest import FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser, mcp_token

SUB = "user-1"


def live(tmp_path: Path, **over: object):
    return make_settings(tmp_path, required_group="mtg-users", membership_check_ttl=0, **over)


async def whoami(h: Harness, token: str):
    return await h.mcp(token, "tools/call", {"name": "whoami", "arguments": {}}, rid=3)


async def test_group_removal_cuts_off_mcp_api_pages_and_refresh_at_once(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        h.db.save_link(SUB, username="alice", user_id="1", secret_enc="x")
        assert h.db.get_link(SUB) is not None
        client = await h.register()
        tokens = await h.tokens_for(client)
        assert (await whoami(h, tokens["access_token"])).status_code == 200
        assert (await b.http.get("/account")).status_code == 200

        idp.set_groups(SUB, ["someone-else"])  # removed at the provider; nothing else happens

        assert (await whoami(h, tokens["access_token"])).status_code == 401
        api = await h.http.get("/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert api.status_code == 401
        page = await b.http.get("/account")
        assert page.status_code == 302 and page.headers["location"].startswith("/login")
        r = await h.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"
        # Everything of theirs is gone, the Archidekt session too, and it is on record.
        with h.db.tx() as c:
            assert (
                c.execute("SELECT COUNT(*) FROM tokens WHERE sub = ? AND revoked = 0", (SUB,)).fetchone()[0]
                == 0
            )
            assert c.execute("SELECT COUNT(*) FROM browser_sessions WHERE sub = ?", (SUB,)).fetchone()[0] == 0
            assert c.execute("SELECT COUNT(*) FROM idp_grants WHERE sub = ?", (SUB,)).fetchone()[0] == 0
            assert c.execute("SELECT event FROM audit_log WHERE event = 'membership_revoked'").fetchone()
        assert h.db.get_link(SUB) is None
        await b.aclose()


async def test_admin_demotion_closes_the_admin_pages_on_the_next_request(
    tmp_path: Path, idp: FakeIdP
) -> None:
    async with running(Harness(live(tmp_path, admin_group="mtg-admins"), idp)) as h:
        idp.user = {**idp.user, "groups": ["mtg-users", "mtg-admins"]}
        b = Browser(h)
        await b.login("/admin")
        assert (await b.http.get("/admin/users")).status_code == 200
        idp.set_groups(SUB, ["mtg-users"])  # still a member, no longer an admin
        assert (await b.http.get("/admin/users")).status_code == 404
        assert (await b.http.get("/account")).status_code == 200  # the member pages still work
        await b.aclose()


async def test_deactivated_at_the_provider_is_cut_off_when_its_tokens_are_refused(
    tmp_path: Path, idp: FakeIdP
) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        token = await mcp_token(h)
        idp.disabled.add(SUB)
        assert (await whoami(h, token)).status_code == 401
        assert h.db.get_idp_grant(SUB) is None


async def test_expired_provider_access_token_is_renewed_with_the_refresh_token(
    tmp_path: Path, idp: FakeIdP
) -> None:
    idp.access_ttl = 1  # inside the renewal margin: every check refreshes first
    async with running(Harness(live(tmp_path), idp)) as h:
        token = await mcp_token(h)
        before = len([c for c in idp.token_calls if c.get("grant_type") == "refresh_token"])
        assert (await whoami(h, token)).status_code == 200
        after = len([c for c in idp.token_calls if c.get("grant_type") == "refresh_token"])
        assert after == before + 1
        idp.set_groups(SUB, [])
        assert (await whoami(h, token)).status_code == 401


async def test_provider_down_refuses_the_request_without_revoking(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        client = await h.register()
        tokens = await h.tokens_for(client)
        idp.down = True
        r = await whoami(h, tokens["access_token"])
        assert r.status_code == 503 and r.json()["error"] == "idp_unavailable"
        page = await b.http.get("/account", headers={"Accept": "text/html"})
        assert page.status_code == 503
        # A refresh during the outage fails, but the refresh token is not burned.
        r = await h.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"
        idp.down = False
        assert (await whoami(h, tokens["access_token"])).status_code == 200
        assert (await b.http.get("/account")).status_code == 200
        r = await h.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
        assert r.status_code == 200, r.text
        await b.aclose()


async def test_sessions_from_before_the_check_must_sign_in_again(tmp_path: Path, idp: FakeIdP) -> None:
    """A grant from an older release (no provider tokens on file) can't be verified: it is
    revoked, the Archidekt link is kept, and a fresh sign-in works."""
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        h.db.save_link(SUB, username="alice", user_id="1", secret_enc="x")
        token = await mcp_token(h)
        with h.db.tx() as c:
            c.execute("DELETE FROM idp_grants")
        assert (await whoami(h, token)).status_code == 401
        assert (await b.http.get("/account")).status_code == 302
        assert h.db.get_link(SUB) is not None
        await b.login()
        assert (await b.http.get("/account")).status_code == 200
        await b.aclose()


async def test_without_offline_access_the_member_signs_in_again_when_the_provider_token_ends(
    tmp_path: Path,
    idp: FakeIdP,
) -> None:
    settings = live(tmp_path, oidc_scopes="openid profile email")
    async with running(Harness(settings, idp)) as h:
        token = await mcp_token(h)
        grant = h.db.get_idp_grant(SUB)
        assert grant["refresh_enc"] == ""
        assert (await whoami(h, token)).status_code == 200
        idp.access_tokens.clear()  # the provider access token runs out; there is no refresh token
        assert (await whoami(h, token)).status_code == 401
        with h.db.tx() as c:
            assert c.execute("SELECT 1 FROM audit_log WHERE event = 'membership_unverifiable'").fetchone()


async def test_checks_are_cached_for_the_configured_seconds(tmp_path: Path, idp: FakeIdP) -> None:
    settings = make_settings(tmp_path, required_group="mtg-users", membership_check_ttl=30)
    async with running(Harness(settings, idp)) as h:
        token = await mcp_token(h)
        calls = idp.userinfo_calls
        for _ in range(3):
            assert (await whoami(h, token)).status_code == 200
        assert idp.userinfo_calls == calls  # the sign-in just checked
        h.app.state.gateway.membership.forget(SUB)
        idp.set_groups(SUB, [])
        assert (await whoami(h, token)).status_code == 401


async def test_stored_provider_tokens_are_encrypted(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        await mcp_token(h)
        grant = h.db.get_idp_grant(SUB)
        raw = (tmp_path / "data").glob("*")
        assert "idp-refresh-" not in grant["refresh_enc"] and "idp-access-" not in grant["access_enc"]
        for f in raw:
            if f.is_file():
                assert b"idp-refresh-" not in f.read_bytes()


async def test_code_issued_before_delete_my_data_mints_nothing(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        client = await h.register()
        code, verifier = await h.full_login(client)
        h.db.delete_member_data(SUB)
        r = await h.token(
            client,
            grant_type="authorization_code",
            code=code,
            redirect_uri="https://client.test/cb",
            code_verifier=verifier,
        )
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant"


async def test_signing_out_makes_the_provider_ask_for_credentials_again(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        other = Browser(h)
        await other.login()
        csrf = await b.csrf("/logout")
        r = await b.http.post("/logout", data={"csrf": csrf, "everywhere": "1"})
        assert r.status_code == 303
        assert (await other.http.get("/account")).status_code == 302  # the other device too
        r = await b.http.get("/login")
        assert "prompt=login" in r.headers["location"]
        r = await b.http.get("/login")
        assert "prompt=login" not in r.headers["location"]  # only the first sign-in after
        await b.aclose()
        await other.aclose()


async def test_an_account_from_another_identity_provider_is_refused(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        with h.db.tx() as c:
            c.execute("UPDATE users SET idp_issuer = 'https://old-idp.example/' WHERE sub = ?", (SUB,))
        b2 = Browser(h)
        r = await b2.http.get("/login", params={"next": "/account"})
        cb = await h.idp_leg(r)
        r2 = await b2.http.get(cb.replace("https://mtg.test", ""))
        assert r2.status_code == 403
        await b.aclose()
        await b2.aclose()


async def test_a_removed_member_cannot_ride_on_someone_elses_token(tmp_path: Path, idp: FakeIdP) -> None:
    """Bearer token of one person plus the session cookie of another: both are checked."""
    async with running(Harness(live(tmp_path, admin_group="mtg-admins"), idp)) as h:
        idp.user = {**idp.user, "groups": ["mtg-users", "mtg-admins"]}
        b = Browser(h)
        await b.login("/admin")
        idp.user = {**idp.user, "sub": "user-2", "groups": ["mtg-users"]}
        other = await mcp_token(h)
        idp.set_groups(SUB, ["mtg-users"])  # user-1 demoted
        r = await b.http.get("/admin/users", headers={"Authorization": f"Bearer {other}"})
        assert r.status_code == 404
        idp.set_groups(SUB, [])  # user-1 removed
        r = await b.http.get("/account", headers={"Authorization": f"Bearer {other}"})
        assert r.status_code == 302
        await b.aclose()


async def test_groups_only_in_the_id_token_are_read_from_a_refresh(tmp_path: Path, idp: FakeIdP) -> None:
    idp.userinfo_omit = {"groups"}
    idp.refresh_id_token = True
    async with running(Harness(live(tmp_path), idp)) as h:
        token = await mcp_token(h)
        assert (await whoami(h, token)).status_code == 200
        assert (await whoami(h, token)).status_code == 200  # stays a member across checks
        idp.set_groups(SUB, [])
        assert (await whoami(h, token)).status_code == 401


async def test_no_groups_claim_anywhere_fails_closed(tmp_path: Path, idp: FakeIdP) -> None:
    idp.userinfo_omit = {"groups"}  # and refreshes return no ID token
    async with running(Harness(live(tmp_path), idp)) as h:
        client = await h.register()
        code, verifier = await h.full_login(client)
        r = await h.token(
            client,
            grant_type="authorization_code",
            code=code,
            code_verifier=verifier,
            redirect_uri="https://client.test/cb",
        )
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant", r.text
        with h.db.tx() as c:
            assert c.execute("SELECT 1 FROM audit_log WHERE event = 'membership_unverifiable'").fetchone()


async def test_a_misconfigured_client_secret_refuses_requests_but_revokes_nothing(
    tmp_path: Path, idp: FakeIdP
) -> None:
    idp.access_ttl = 1  # every check refreshes
    async with running(Harness(live(tmp_path), idp)) as h:
        token = await mcp_token(h)
        idp.refresh_error = "invalid_client"
        r = await whoami(h, token)
        assert r.status_code == 503
        idp.refresh_error = None
        assert (await whoami(h, token)).status_code == 200


async def test_provider_without_expires_in_or_refresh_tokens_still_works(
    tmp_path: Path, idp: FakeIdP
) -> None:
    idp.access_ttl = None
    async with running(Harness(live(tmp_path, oidc_scopes="openid profile email"), idp)) as h:
        token = await mcp_token(h)
        assert (await whoami(h, token)).status_code == 200
        idp.set_groups(SUB, [])
        assert (await whoami(h, token)).status_code == 401


async def test_a_previous_issuer_is_repinned(tmp_path: Path, idp: FakeIdP) -> None:
    old = "https://old-idp.example/application/o/mtg"
    async with running(Harness(live(tmp_path, oidc_previous_issuers=[old]), idp)) as h:
        b = Browser(h)
        await b.login()
        with h.db.tx() as c:
            c.execute("UPDATE users SET idp_issuer = ? WHERE sub = ?", (old + "/", SUB))
        b2 = Browser(h)
        await b2.login()
        assert h.db.get_user(SUB)["idp_issuer"].rstrip("/") == h.oidc.issuer.rstrip("/")
        await b.aclose()
        await b2.aclose()


async def test_a_provider_outage_does_not_burn_the_authorization_code(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        client = await h.register()
        code, verifier = await h.full_login(client)
        h.app.state.gateway.membership.forget(SUB)
        idp.down = True
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": "https://client.test/cb",
            "code_verifier": verifier,
        }
        assert (await h.token(client, **form)).status_code == 400
        idp.down = False
        r = await h.token(client, **form)
        assert r.status_code == 200, r.text


async def test_any_idp_user_without_a_groups_claim_stays_signed_in(tmp_path: Path, idp: FakeIdP) -> None:
    """MTG_ALLOW_ANY_IDP_USER with a provider that sends no groups (Google, Entra ID): there is no
    group to leave, so a missing claim must not sign everyone out; a disabled account still is."""
    idp.userinfo_omit = {"groups"}
    settings = make_settings(tmp_path, required_group=None, membership_check_ttl=0)
    async with running(Harness(settings, idp)) as h:
        token = await mcp_token(h)
        for _ in range(3):
            assert (await whoami(h, token)).status_code == 200
        idp.disabled.add(SUB)
        idp.access_tokens.clear()
        assert (await whoami(h, token)).status_code == 401


async def test_huge_access_token_is_checked_in_a_post_body(tmp_path: Path, idp: FakeIdP) -> None:
    """Authentik copies the ID token's claims into its access token, so an embedded avatar can
    make it over a megabyte. A reverse proxy refuses a header that size (0.6.2 answered every page
    with "the sign-in service can't be reached"); the check now sends it in a POST body, and a
    removal is still seen on the next request."""
    idp.access_token_pad = 1_200_000
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        assert (await b.http.get("/account")).status_code == 200
        assert idp.userinfo_methods and set(idp.userinfo_methods) == {"POST"}
        idp.set_groups(SUB, ["someone-else"])
        page = await b.http.get("/account")
        assert page.status_code == 302 and page.headers["location"].startswith("/login")
        await b.aclose()


async def test_small_access_token_still_uses_the_header(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        assert (await b.http.get("/account")).status_code == 200
        assert idp.userinfo_methods and set(idp.userinfo_methods) == {"GET"}
        await b.aclose()


async def test_admin_group_alone_grants_access_and_leaving_it_cuts_off(tmp_path: Path, idp: FakeIdP) -> None:
    """An admin needs no second group to sign in; leaving the admin group (with no users group)
    is seen on the next request like any other removal."""
    async with running(Harness(live(tmp_path, admin_group="mtg-admins"), idp)) as h:
        idp.user = {**idp.user, "groups": ["mtg-admins"]}
        b = Browser(h)
        await b.login("/admin")
        assert (await b.http.get("/admin/users")).status_code == 200
        assert (await b.http.get("/account")).status_code == 200
        client = await h.register()
        tokens = await h.tokens_for(client)
        assert (await whoami(h, tokens["access_token"])).status_code == 200
        idp.set_groups(SUB, ["someone-else"])
        assert (await whoami(h, tokens["access_token"])).status_code == 401
        page = await b.http.get("/account")
        assert page.status_code == 302 and page.headers["location"].startswith("/login")
        await b.aclose()


async def test_neither_group_is_still_refused_at_sign_in(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path, admin_group="mtg-admins"), idp)) as h:
        idp.user = {**idp.user, "groups": ["other"]}
        r, _ = await h.start_login(await h.register())
        cb = await h.idp_leg(r)
        page = await h.callback(cb)
        assert page.status_code == 403 and "not in the group" in page.text
