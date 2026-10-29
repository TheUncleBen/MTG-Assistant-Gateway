"""Removing someone at the identity provider takes effect on their next request (membership.py).

The provider never tells the gateway (Authentik keeps honouring a removed member's refresh token),
so the gateway asks its userinfo endpoint before serving any request with a session or a token."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

import pytest

from .conftest import FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser, mcp_token

SUB = "user-1"


def live(tmp_path: Path, **over: object):
    return make_settings(tmp_path, required_group="mtg-users", membership_check_ttl=0, **over)


async def whoami(h: Harness, token: str):
    return await h.mcp(token, "tools/call", {"name": "whoami", "arguments": {}}, rid=3)


def grants(h: Harness, sub: str = SUB) -> list[dict]:
    """The member's provider grants that stand for a sign-in in use (sessions', AI apps')."""
    return h.db.idp_grants_for(sub)


def live_tokens(h: Harness, sub: str = SUB) -> int:
    with h.db.tx() as c:
        return c.execute("SELECT COUNT(*) FROM tokens WHERE sub = ? AND revoked = 0", (sub,)).fetchone()[0]


def asked_to_sign_in_again(r) -> bool:
    """An AI app's request while none of the member's sign-ins is honoured: refused, tokens kept."""
    return r.status_code == 403 and r.json()["error"] == "sign_in_again"


async def test_group_removal_cuts_off_mcp_api_pages_and_refresh_at_once(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        h.db.save_link(SUB, username="alice", user_id="1", secret_enc="x")
        assert h.db.get_link(SUB) is not None
        client = await h.register()
        tokens = await h.tokens_for(client)
        assert (await whoami(h, tokens["access_token"])).status_code == 200
        assert (await b.http.get("/account/details")).status_code == 200

        idp.set_groups(SUB, ["someone-else"])  # removed at the provider; nothing else happens

        assert (await whoami(h, tokens["access_token"])).status_code == 401
        api = await h.http.get("/api/v1/me", headers={"Authorization": f"Bearer {tokens['access_token']}"})
        assert api.status_code == 401
        page = await b.http.get("/account/details")
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
        assert (await b.http.get("/account/details")).status_code == 200  # the member pages still work
        await b.aclose()


async def test_deactivated_at_the_provider_is_cut_off_when_its_tokens_are_refused(
    tmp_path: Path, idp: FakeIdP
) -> None:
    """The provider refusing its tokens can't be told from a refresh token that ran out (C2): the
    AI app is refused, its tokens are kept but give nothing until the member signs in again."""
    async with running(Harness(live(tmp_path), idp)) as h:
        token = await mcp_token(h)
        idp.disabled.add(SUB)
        assert asked_to_sign_in_again(await whoami(h, token))
        assert grants(h) == []
        assert live_tokens(h) > 0
        assert asked_to_sign_in_again(await whoami(h, token))  # still refused: nothing to ask with


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
        page = await b.http.get("/account/details", headers={"Accept": "text/html"})
        assert page.status_code == 503
        # A refresh during the outage fails, but the refresh token is not burned.
        r = await h.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"
        idp.down = False
        assert (await whoami(h, tokens["access_token"])).status_code == 200
        assert (await b.http.get("/account/details")).status_code == 200
        r = await h.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
        assert r.status_code == 200, r.text
        await b.aclose()


async def test_sessions_from_before_the_check_must_sign_in_again(tmp_path: Path, idp: FakeIdP) -> None:
    """With no provider tokens on file, nothing can be checked: the browser signs in again, the
    AI app waits with its tokens and the Archidekt link kept, and a fresh website sign-in brings
    both back."""
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        h.db.save_link(SUB, username="alice", user_id="1", secret_enc="x")
        token = await mcp_token(h)
        with h.db.tx() as c:
            c.execute("DELETE FROM idp_grants")
        assert asked_to_sign_in_again(await whoami(h, token))
        assert (await b.http.get("/account/details")).status_code == 302
        assert h.db.get_link(SUB) is not None
        # the way back in says why first
        r = await b.http.get("/login", params={"next": "/account"})
        assert r.status_code == 303 and r.headers["location"].startswith("/signed-out")
        page = await b.http.get(r.headers["location"])
        assert "The sign-in service asked you to sign in again" in page.text
        await b.login()
        assert (await b.http.get("/account/details")).status_code == 200
        assert (await whoami(h, token)).status_code == 200  # the same token, no reconnect
        await b.aclose()


async def test_without_offline_access_the_member_signs_in_again_when_the_provider_token_ends(
    tmp_path: Path,
    idp: FakeIdP,
) -> None:
    settings = live(tmp_path, oidc_scopes="openid profile email")
    async with running(Harness(settings, idp)) as h:
        token = await mcp_token(h)
        (grant,) = grants(h)
        assert grant["refresh_enc"] == ""
        assert (await whoami(h, token)).status_code == 200
        idp.access_tokens.clear()  # the provider access token runs out; there is no refresh token
        assert asked_to_sign_in_again(await whoami(h, token))
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
        (grant,) = grants(h)
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
        assert (await other.http.get("/account/details")).status_code == 302  # the other device too
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
        r = await b.http.get("/account/details", headers={"Authorization": f"Bearer {other}"})
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
        # refused, but not as a dead grant: nothing is revoked on an answer that decides nothing
        assert r.status_code == 400 and r.json()["error"] == "invalid_request", r.text
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
        assert asked_to_sign_in_again(await whoami(h, token))


async def test_huge_access_token_is_checked_in_a_post_body(tmp_path: Path, idp: FakeIdP) -> None:
    """Authentik copies the ID token's claims into its access token, so an embedded avatar can
    make it over a megabyte. A reverse proxy refuses a header that size (0.6.2 answered every page
    with "the sign-in service can't be reached"); the check now sends it in a POST body, and a
    removal is still seen on the next request."""
    idp.access_token_pad = 1_200_000
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        assert (await b.http.get("/account/details")).status_code == 200
        assert idp.userinfo_methods and set(idp.userinfo_methods) == {"POST"}
        idp.set_groups(SUB, ["someone-else"])
        page = await b.http.get("/account/details")
        assert page.status_code == 302 and page.headers["location"].startswith("/login")
        await b.aclose()


async def test_small_access_token_still_uses_the_header(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        assert (await b.http.get("/account/details")).status_code == 200
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
        assert (await b.http.get("/account/details")).status_code == 200
        client = await h.register()
        tokens = await h.tokens_for(client)
        assert (await whoami(h, tokens["access_token"])).status_code == 200
        idp.set_groups(SUB, ["someone-else"])
        assert (await whoami(h, tokens["access_token"])).status_code == 401
        page = await b.http.get("/account/details")
        assert page.status_code == 302 and page.headers["location"].startswith("/login")
        await b.aclose()


async def test_neither_group_is_still_refused_at_sign_in(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path, admin_group="mtg-admins"), idp)) as h:
        idp.user = {**idp.user, "groups": ["other"]}
        r, _ = await h.start_login(await h.register())
        cb = await h.idp_leg(r)
        page = await h.callback(cb)
        assert page.status_code == 403 and "not in the group" in page.text


async def test_a_dropped_connection_to_userinfo_is_tried_once_more(tmp_path: Path, idp: FakeIdP) -> None:
    import httpx

    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        real = h.oidc._http.get
        drops: list[int] = []

        async def flaky(*args: object, **kw: object) -> httpx.Response:
            if drops:
                drops.pop()
                raise httpx.ConnectError("connection reset")
            return await real(*args, **kw)  # type: ignore[arg-type]

        h.oidc._http.get = flaky  # type: ignore[method-assign]
        drops[:] = [1]  # one drop: the retry answers, the member never notices
        assert (await b.http.get("/account/details")).status_code == 200 and not drops
        drops[:] = [1, 1]  # still down on the retry: refused, nothing revoked
        page = await b.http.get("/account/details")
        assert page.status_code == 503 and not drops
        assert grants(h)
        await b.aclose()


def _refreshes(idp: FakeIdP) -> int:
    return len([c for c in idp.token_calls if c.get("grant_type") == "refresh_token"])


async def test_the_provider_token_is_renewed_before_it_runs_out_not_during_a_request(
    tmp_path: Path, idp: FakeIdP, monkeypatch
) -> None:
    """Speed (Ben, 25 Oct): a request that finds the provider access token run out waits for a
    refresh and then userinfo, two round trips to the identity provider. The gateway renews it in
    the background shortly before it runs out instead, so the request waits for userinfo only. The
    membership check itself is unchanged: still asked before the request, every time."""
    from mtg_gateway import membership

    monkeypatch.setattr(membership, "RENEW_LEAD", 2)
    # the token is past its renewal margin 4 s after it was issued: renewed in the background
    # about 2 s in (RENEW_LEAD before that)
    idp.access_ttl = membership.EXPIRY_MARGIN + 4
    async with running(Harness(live(tmp_path), idp)) as h:
        token = await mcp_token(h)
        assert (await whoami(h, token)).status_code == 200
        before = _refreshes(idp)
        await asyncio.sleep(2.5)
        assert _refreshes(idp) == before + 1  # renewed with no request waiting on it
        during = _refreshes(idp)
        calls = idp.userinfo_calls
        assert (await whoami(h, token)).status_code == 200
        assert _refreshes(idp) == during and idp.userinfo_calls == calls + 1  # userinfo only
        # still asked every time: a removal takes effect on the next request as before
        idp.set_groups(SUB, [])
        assert (await whoami(h, token)).status_code == 401


async def test_a_refused_background_renewal_revokes_nothing_by_itself(
    tmp_path: Path, idp: FakeIdP, monkeypatch
) -> None:
    from mtg_gateway import membership

    monkeypatch.setattr(membership, "RENEW_LEAD", 2)
    idp.access_ttl = membership.EXPIRY_MARGIN + 4
    async with running(Harness(live(tmp_path), idp)) as h:
        token = await mcp_token(h)
        assert (await whoami(h, token)).status_code == 200
        idp.refresh_error = "invalid_grant"
        before = _refreshes(idp)
        await asyncio.sleep(2.5)
        assert _refreshes(idp) == before + 1
        # the background renewal decides nothing: the next request's own check does, as before
        assert h.db.audit_recent(5)[0]["event"] != "membership_unverifiable"
        idp.refresh_error = None
        idp.access_ttl = 300


async def test_a_background_renewal_never_brings_back_a_removed_grant(
    tmp_path: Path, idp: FakeIdP, monkeypatch
) -> None:
    """Security review (25 Oct): a renewal that was waiting on the provider while the member's
    tokens were revoked (or their data deleted) stores nothing."""
    from mtg_gateway import membership

    monkeypatch.setattr(membership, "RENEW_LEAD", 2)
    idp.access_ttl = membership.EXPIRY_MARGIN + 4
    async with running(Harness(live(tmp_path), idp)) as h:
        token = await mcp_token(h)
        assert (await whoami(h, token)).status_code == 200
        checker = h.app.state.gateway.membership
        real = checker.oidc.refresh

        async def slow(refresh_token: str):
            h.db.revoke_all_for_user(SUB)  # the admin's revoke lands while Authentik answers
            return await real(refresh_token)

        monkeypatch.setattr(checker.oidc, "refresh", slow)
        before = _refreshes(idp)
        await asyncio.sleep(2.5)
        assert _refreshes(idp) == before + 1
        assert grants(h) == []


# -- the check off a page's critical path (0.7.46) -----------------------------------------------
def _age_last_check(h: Harness, seconds: float) -> None:
    """Make the last check that let each of SUB's sign-ins in ``seconds`` older than it is."""
    checker = h.app.state.gateway.membership
    for holder, owner in checker._owners.items():
        if owner == SUB:
            checker._checked[holder] -= seconds


async def _background_checks(h: Harness) -> None:
    checker = h.app.state.gateway.membership
    await asyncio.gather(*list(checker._background.values()), return_exceptions=True)


async def test_a_member_checked_recently_is_not_kept_waiting_for_the_provider(
    tmp_path: Path, idp: FakeIdP
) -> None:
    """Past the cache, a page being read is answered at once and the provider asked in the
    background (C2: the first page after a pause never waits); a write waits for the answer."""
    async with running(Harness(make_settings(tmp_path, required_group="mtg-users"), idp)) as h:
        b = Browser(h)
        await b.login()
        _age_last_check(h, 10)  # past the 5 s cache, well inside the grace
        idp.userinfo_delay = 1.0
        calls = idp.userinfo_calls
        loop = asyncio.get_running_loop()
        t0 = loop.time()
        page = await b.http.get("/account/details")
        assert page.status_code == 200
        assert loop.time() - t0 < 0.5  # not the provider's second
        await _background_checks(h)
        assert idp.userinfo_calls == calls + 1  # asked once, in the background
        # After a long pause, a page being read is still answered at once ...
        _age_last_check(h, 3600)
        t0 = loop.time()
        assert (await b.http.get("/account/details")).status_code == 200
        assert loop.time() - t0 < 0.5
        await _background_checks(h)
        # ... and a write just past the cache waits for the provider's answer.
        _age_last_check(h, 10)
        t0 = loop.time()
        await b.http.post("/theme", data={"theme": "dark"})
        assert loop.time() - t0 >= 1.0
        await b.aclose()


async def test_a_removal_ends_page_reads_after_one_page_and_refuses_the_first_write(
    tmp_path: Path, idp: FakeIdP
) -> None:
    async with running(Harness(make_settings(tmp_path, required_group="mtg-users"), idp)) as h:
        b = Browser(h)
        await b.login()
        _age_last_check(h, 10)
        idp.set_groups(SUB, ["someone-else"])
        # The request that starts the background check is still served ...
        assert (await b.http.get("/account/details")).status_code == 200
        await _background_checks(h)
        # ... and the check's answer applies to the next one.
        page = await b.http.get("/account/details")
        assert page.status_code == 302 and page.headers["location"].startswith("/login")
        await b.aclose()

        # The very first write past the cache is refused: it waited for the provider.
        b = Browser(h)
        idp.set_groups(SUB, ["mtg-users"])
        await b.login()
        csrf = await b.csrf("/logout")
        _age_last_check(h, 10)
        idp.set_groups(SUB, ["someone-else"])
        await b.http.post("/theme", data={"csrf": csrf, "theme": "dark"})
        assert not h.app.state.gateway.membership._background  # it waited, nothing runs after
        with h.db.tx() as c:
            assert c.execute("SELECT COUNT(*) FROM browser_sessions WHERE sub = ?", (SUB,)).fetchone()[0] == 0
        assert "mtg_theme" not in b.http.cookies  # the write was refused
        await b.aclose()


async def test_an_assistant_of_a_removed_member_is_refused_on_its_next_request(
    tmp_path: Path, idp: FakeIdP
) -> None:
    """An AI app's request past the cache waits for the provider, however recently it let the
    member in, so a member taken out of the group is cut off on the next tool call (the e2e test
    of the same, against a real Authentik)."""
    async with running(Harness(make_settings(tmp_path, required_group="mtg-users"), idp)) as h:
        b = Browser(h)
        await b.login()
        client = await h.register()
        tokens = await h.tokens_for(client)
        assert (await whoami(h, tokens["access_token"])).status_code == 200
        _age_last_check(h, 6)  # just past the 5 s cache, as the e2e test waits
        idp.set_groups(SUB, ["someone-else"])
        assert (await whoami(h, tokens["access_token"])).status_code == 401
        assert live_tokens(h) == 0
        await b.aclose()


async def test_a_page_waits_for_the_provider_when_no_background_check_can_start(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The background checks are bounded; at the bound a page waits for the answer rather than
    being served with nobody asking the provider."""
    from mtg_gateway import membership

    async with running(Harness(make_settings(tmp_path, required_group="mtg-users"), idp)) as h:
        b = Browser(h)
        await b.login()
        monkeypatch.setattr(membership, "MAX_CACHED", 0)
        _age_last_check(h, 10)
        idp.set_groups(SUB, ["someone-else"])
        page = await b.http.get("/account/details")
        assert page.status_code == 302 and page.headers["location"].startswith("/login")
        await b.aclose()


async def test_no_grace_when_every_request_is_checked(tmp_path: Path, idp: FakeIdP) -> None:
    """MTG_MEMBERSHIP_CHECK_TTL=0 still asks before every request, as it says."""
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        idp.set_groups(SUB, ["someone-else"])
        page = await b.http.get("/account/details")
        assert page.status_code == 302
        await b.aclose()


async def test_a_provider_error_on_renewal_signs_nobody_out(
    tmp_path: Path, idp: FakeIdP, caplog: pytest.LogCaptureFixture
) -> None:
    """The provider's access token ran out and its token endpoint answers 502 (or 503, or 500):
    the answer is unknown, so the request is refused for now and every session is kept."""
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        idp.access_tokens.clear()  # userinfo refuses the old access token: a renewal is needed
        for status in (502, 503, 504, 500):
            idp.token_status = status
            page = await b.http.get("/account/details", headers={"Accept": "text/html"})
            assert page.status_code == 503, status
        with h.db.tx() as c:
            assert c.execute("SELECT COUNT(*) FROM browser_sessions WHERE sub = ?", (SUB,)).fetchone()[0] == 1
            events = [r[0] for r in c.execute("SELECT event FROM audit_log").fetchall()]
        assert not [e for e in events if e.startswith("membership_")], events
        idp.token_status = None
        assert (await b.http.get("/account/details")).status_code == 200
        # A definite refusal (the grant is gone at the provider) does sign the member out, and the
        # log says why.
        idp.access_tokens.clear()
        idp.refresh_tokens.clear()
        with caplog.at_level(logging.INFO, logger="mtg_gateway.membership"):
            assert (await b.http.get("/account/details")).status_code == 302
        assert any("idp_refused_refresh" in r.getMessage() for r in caplog.records), caplog.text
        await b.aclose()
