"""Sign-outs (owner decision C2, 0.7.46).

- When the identity provider stops honouring one sign-in (its refresh token ran out, say), only
  that sign-in ends: the member's other devices and their AI apps' tokens are kept. An AI app is
  refused while none of the member's sign-ins is honoured and works again, with the same tokens,
  once they sign in on the website. Only a definite answer ("not in the group") revokes everything.
- Each sign-in keeps its own provider grant, and while the member is active the others are
  renewed in the background, so a device left for a while does not find its grant run out.
- The first page after a pause never waits for the provider; a write or an admin page does.
- The signed-out page says why, with Sign in.
- Guide and Install read signed out; the downloads still need a sign-in.
"""

from __future__ import annotations

import asyncio
import html
import re
import sqlite3
import time
from pathlib import Path

import httpx

from mtg_gateway import db as db_module
from mtg_gateway.db import Database

from .conftest import FakeIdP, Harness, make_settings, running
from .test_admin import _with_admin, admin_browser
from .test_browser_sessions import _move
from .test_decks_and_proxy import Browser, mcp_token

SUB = "user-1"
DAY = 86400
NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}


def live(tmp_path: Path, **over: object):
    """Every request asks the provider (no cache): what each one does is seen at once."""
    return make_settings(tmp_path, required_group="mtg-users", membership_check_ttl=0, **over)


async def whoami(h: Harness, token: str) -> httpx.Response:
    return await h.mcp(token, "tools/call", {"name": "whoami", "arguments": {}}, rid=3)


def provider_tokens(idp: FakeIdP) -> set[str]:
    return set(idp.access_tokens) | set(idp.refresh_tokens)


def end_at_provider(idp: FakeIdP, tokens: set[str]) -> None:
    """The provider stops honouring these tokens (they ran out, or its session ended)."""
    for t in tokens:
        idp.access_tokens.pop(t, None)
        idp.refresh_tokens.pop(t, None)


async def sign_in(b: Browser, idp: FakeIdP) -> set[str]:
    """Sign ``b`` in; the provider tokens that sign-in got."""
    before = provider_tokens(idp)
    await b.login()
    return provider_tokens(idp) - before


def live_tokens(h: Harness) -> int:
    with h.db.tx() as c:
        return c.execute("SELECT COUNT(*) FROM tokens WHERE sub = ? AND revoked = 0", (SUB,)).fetchone()[0]


async def reason_page(b: Browser, next_path: str = "/account") -> str:
    """Follow the way back in: /login sends a browser whose sign-in ended to the page saying why."""
    r = await b.http.get("/login", params={"next": next_path})
    assert r.status_code == 303, (r.status_code, r.headers.get("location"))
    assert r.headers["location"].startswith("/signed-out")
    page = await b.http.get(r.headers["location"])
    assert page.status_code == 200
    return html.unescape(page.text)


# -- (a) a sign-in the provider no longer honours ends alone ------------------------------------
async def test_one_unhonoured_sign_in_ends_alone_and_keeps_the_ai_apps(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        laptop, phone = Browser(h), Browser(h)
        await sign_in(laptop, idp)
        token = await mcp_token(h)
        phone_tokens = await sign_in(phone, idp)  # the latest sign-in
        h.db.save_link(SUB, username="alice", user_id="1", secret_enc="x")
        end_at_provider(idp, phone_tokens)  # the provider ends the phone's sign-in only

        page = await phone.http.get("/account/details")
        assert page.status_code == 302 and page.headers["location"].startswith("/login")
        # The laptop, the AI app and the Archidekt link carry on.
        assert (await laptop.http.get("/account/details")).status_code == 200
        assert (await whoami(h, token)).status_code == 200
        assert h.db.get_link(SUB) is not None
        assert "The sign-in service asked you to sign in again" in await reason_page(phone)
        with h.db.tx() as c:
            row = c.execute(
                "SELECT detail_json FROM audit_log WHERE event = 'membership_unverifiable'"
            ).fetchone()
        assert '"sign_in": "website"' in row[0]
        await laptop.aclose()
        await phone.aclose()


async def test_ai_apps_wait_with_their_tokens_until_the_member_signs_in_again(
    tmp_path: Path, idp: FakeIdP
) -> None:
    """None of the member's sign-ins honoured (an expired provider refresh token looks just like a
    deactivated account): the AI app is refused, its tokens give nothing and are not burned, and
    the same tokens work again after a website sign-in. No reconnecting the app."""
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await sign_in(b, idp)
        client = await h.register()
        tokens = await h.tokens_for(client)
        bearer = {"Authorization": f"Bearer {tokens['access_token']}"}
        end_at_provider(idp, provider_tokens(idp))

        r = await whoami(h, tokens["access_token"])
        assert r.status_code == 403 and r.json()["error"] == "sign_in_again"
        assert "Sign in again at https://mtg.test" in r.json()["message"]
        api = await h.http.get("/api/v1/me", headers=bearer)
        assert api.status_code == 403 and api.json()["error"] == "sign_in_again"
        # A refresh is refused without invalid_grant (which makes an app drop its tokens), and the
        # refresh token is not used up.
        r = await h.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
        assert r.status_code == 400 and r.json()["error"] == "invalid_request"
        assert "sign in again" in r.json()["error_description"].lower()
        assert live_tokens(h) == 2
        assert (await b.http.get("/account/details")).status_code == 302  # the website too

        assert "The sign-in service asked" in await reason_page(b)
        await b.login()
        assert (await whoami(h, tokens["access_token"])).status_code == 200
        r = await h.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
        assert r.status_code == 200, r.text
        await b.aclose()


async def test_a_removal_still_ends_everything_at_once(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        b = Browser(h)
        await sign_in(b, idp)
        token = await mcp_token(h)
        idp.set_groups(SUB, ["someone-else"])
        assert (await whoami(h, token)).status_code == 401
        assert live_tokens(h) == 0
        assert "no longer in the group" in await reason_page(b)
        await b.aclose()


async def test_a_bearer_token_cannot_vouch_for_itself(tmp_path: Path, idp: FakeIdP) -> None:
    """Security: with every sign-in ended at the provider, nothing an AI app (or whoever holds its
    tokens) can send brings access back; only a sign-in at the provider does."""
    async with running(Harness(live(tmp_path), idp)) as h:
        client = await h.register()
        tokens = await h.tokens_for(client)
        end_at_provider(idp, provider_tokens(idp))
        for _ in range(3):
            assert (await whoami(h, tokens["access_token"])).status_code == 403
            r = await h.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
            assert r.status_code == 400
        assert h.db.idp_grants_for(SUB) == []
        # a new AI sign-in is a sign-in at the provider: it works, with tokens of its own
        fresh = await h.tokens_for(await h.register())
        assert (await whoami(h, fresh["access_token"])).status_code == 200


# -- (b) a grant per sign-in, kept fresh while the member is active ------------------------------
async def test_each_sign_in_keeps_its_own_grant(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(live(tmp_path), idp)) as h:
        a, b = Browser(h), Browser(h)
        await sign_in(a, idp)
        await sign_in(b, idp)
        await mcp_token(h)
        await mcp_token(h)  # a second AI sign-in replaces the first's grant
        holders = sorted(g["holder"].split(":")[0] for g in h.db.idp_grants_for(SUB))
        assert holders == ["ai", "s", "s"]
        await a.aclose()
        await b.aclose()


async def test_an_active_member_keeps_their_other_devices_grants_fresh(tmp_path: Path, idp: FakeIdP) -> None:
    from mtg_gateway import membership

    async with running(Harness(live(tmp_path), idp)) as h:
        laptop, phone = Browser(h), Browser(h)
        await sign_in(laptop, idp)
        phone_tokens = await sign_in(phone, idp)
        (phone_refresh,) = [t for t in phone_tokens if t.startswith("idp-refresh-")]
        with h.db.tx() as c:  # neither renewed for two days
            c.execute("UPDATE idp_grants SET updated_at = updated_at - ?", (2 * DAY,))
        assert (await laptop.http.get("/account/details")).status_code == 200  # the member is active
        checker = h.app.state.gateway.membership
        await _keep_alives(checker)
        used = [c.get("refresh_token") for c in idp.token_calls if c.get("grant_type") == "refresh_token"]
        assert phone_refresh in used  # the phone's grant was renewed in the background
        assert phone_refresh not in idp.refresh_tokens  # rotated: the provider holds a new one
        assert (await phone.http.get("/account/details")).status_code == 200  # with the new one
        await _keep_alives(checker)
        # (the phone's request renewed the laptop's two-day-old grant the same way)
        # Renewed once, not on every request: both are fresh now.
        n = _refresh_calls(idp)
        for _ in range(3):
            assert (await laptop.http.get("/account/details")).status_code == 200
            assert (await phone.http.get("/account/details")).status_code == 200
        await _keep_alives(checker)
        assert _refresh_calls(idp) == n
        assert membership.KEEP_ALIVE == DAY
        await laptop.aclose()
        await phone.aclose()


async def _keep_alives(checker: object) -> None:
    """Let the renewals started just now finish. Not by awaiting every renewal task: those planned
    ahead of an access token's expiry sleep for minutes."""
    await asyncio.sleep(0.2)
    for _ in range(100):
        if not checker._refreshing:  # type: ignore[attr-defined]
            return
        await asyncio.sleep(0.02)


def _refresh_calls(idp: FakeIdP) -> int:
    return len([c for c in idp.token_calls if c.get("grant_type") == "refresh_token"])


# -- the first page after a pause never waits ---------------------------------------------------
def _age(h: Harness, seconds: float) -> None:
    checker = h.app.state.gateway.membership
    owners = getattr(checker, "_owners", {SUB: SUB})  # before 0.7.46: one entry per member
    for holder, owner in owners.items():
        if owner == SUB:
            checker._checked[holder] -= seconds


async def _settle(h: Harness) -> None:
    checker = h.app.state.gateway.membership
    await asyncio.gather(*list(checker._background.values()), return_exceptions=True)


async def test_the_first_page_after_a_long_pause_does_not_wait(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(make_settings(tmp_path, required_group="mtg-users"), idp)) as h:
        b = Browser(h)
        await b.login()
        loop = asyncio.get_running_loop()
        idp.userinfo_delay = 1.0
        calls = idp.userinfo_calls
        _age(h, 3600)  # an hour away
        t0 = loop.time()
        assert (await b.http.get("/account/details")).status_code == 200
        assert loop.time() - t0 < 0.5
        await _settle(h)
        assert idp.userinfo_calls == calls + 1  # asked, after the page
        # After a restart too: the provider's last yes is on file.
        checker = h.app.state.gateway.membership
        checker._checked.clear()
        getattr(checker, "_owners", {}).clear()
        t0 = loop.time()
        assert (await b.http.get("/account/details")).status_code == 200
        assert loop.time() - t0 < 0.5
        await _settle(h)
        await b.aclose()


async def test_after_a_restart_an_old_yes_on_file_is_not_enough(tmp_path: Path, idp: FakeIdP) -> None:
    """Security review 2, R2-03: after a restart a page went ahead on the provider's last yes on
    file however old it was. Older than STORED_YES_MAX_AGE, the page waits for the provider's
    answer; a removed member then reads nothing."""
    from mtg_gateway.membership import STORED_YES_MAX_AGE

    async with running(Harness(make_settings(tmp_path, required_group="mtg-users"), idp)) as h:
        b = Browser(h)
        await b.login()
        loop = asyncio.get_running_loop()
        checker = h.app.state.gateway.membership
        checker._checked.clear()  # a restart
        getattr(checker, "_owners", {}).clear()
        with h.db.tx() as c:
            c.execute(
                "UPDATE idp_grants SET checked_at = ? WHERE sub = ?",
                (int(time.time()) - STORED_YES_MAX_AGE - 60, SUB),
            )
        idp.userinfo_delay = 1.0
        t0 = loop.time()
        assert (await b.http.get("/account/details")).status_code == 200
        assert loop.time() - t0 >= 1.0  # waited for the provider's answer
        # and a member removed while the gateway was down reads nothing at all
        idp.userinfo_delay = 0.0
        checker._checked.clear()
        getattr(checker, "_owners", {}).clear()
        with h.db.tx() as c:
            c.execute(
                "UPDATE idp_grants SET checked_at = ? WHERE sub = ?",
                (int(time.time()) - STORED_YES_MAX_AGE - 60, SUB),
            )
        idp.set_groups(SUB, ["someone-else"])
        r = await b.http.get("/account/details")
        assert r.status_code == 302
        assert "no longer in the group" in await reason_page(b)
        await b.aclose()
    assert STORED_YES_MAX_AGE == 86400


async def test_writes_and_admin_pages_still_wait_and_an_outage_fails_closed(
    tmp_path: Path, idp: FakeIdP
) -> None:
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
    async with running(_with_admin(Harness(settings, idp))) as h:
        idp.user = {**idp.user, "groups": ["mtg-users", "mtg-admins"]}
        b = Browser(h)
        await b.login("/admin")
        loop = asyncio.get_running_loop()
        idp.userinfo_delay = 1.0
        _age(h, 10)  # past the 5 s cache
        t0 = loop.time()
        await b.http.get("/admin/users")
        assert loop.time() - t0 >= 1.0  # an admin page waits for the answer
        idp.userinfo_delay = 0.0
        # The provider goes down while the member is away: the page that starts the check is
        # answered, and once the check found the provider out, the next one waits (503).
        _age(h, 10)  # past the 5 s cache
        idp.down = True
        assert (await b.http.get("/account/details", headers=NAV)).status_code == 200
        await _settle(h)
        assert (await b.http.get("/account/details", headers=NAV)).status_code == 503
        idp.down = False
        assert (await b.http.get("/account/details", headers=NAV)).status_code == 200
        await b.aclose()


async def test_a_removed_member_reads_at_most_the_page_that_starts_the_check(
    tmp_path: Path, idp: FakeIdP
) -> None:
    async with running(Harness(make_settings(tmp_path, required_group="mtg-users"), idp)) as h:
        b = Browser(h)
        await b.login()
        _age(h, 3600)
        idp.set_groups(SUB, ["someone-else"])
        assert (await b.http.get("/account/details")).status_code == 200
        await _settle(h)
        page = await b.http.get("/account/details")
        assert page.status_code == 302
        assert "no longer in the group" in await reason_page(b)
        await b.aclose()


# -- (c) the signed-out page says why -------------------------------------------------------------
async def test_the_signed_out_page_says_why_for_time_limits(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        _move(h, created_ago=8 * DAY, expires_in=-DAY)  # last used eight days ago
        assert (await b.http.get("/decks", headers=NAV)).status_code == 302
        text = await reason_page(b, "/decks")
        assert "Your sign-in on this device ended because it wasn't used for 7 days." in text
        assert "href='/login?next=/decks'" in text  # Sign in comes back to the same page
        # Told once: the cookie is gone and Sign in goes to the provider.
        r = await b.http.get("/login", params={"next": "/decks"})
        assert r.status_code == 302 and r.headers["location"].startswith("https://idp.test/")

        await b.login()
        _move(h, created_ago=30 * DAY + 60, expires_in=DAY)
        assert "reached the longest time a sign-in lasts (30 days)" in await reason_page(b)

        # The hourly clean-up deletes ended sessions and keeps why.
        await b.login()
        _move(h, created_ago=10 * DAY, expires_in=-2 * DAY)
        h.db.purge_expired()
        with h.db.tx() as c:
            assert c.execute("SELECT COUNT(*) FROM browser_sessions").fetchone()[0] == 0
        assert "wasn't used for 7 days" in await reason_page(b)
        await b.aclose()


async def test_the_signed_out_page_says_why_for_sign_outs_elsewhere(tmp_path: Path, idp: FakeIdP) -> None:
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
    async with running(_with_admin(Harness(settings, idp))) as h:
        here, there = Browser(h), Browser(h)
        await here.login()
        await there.login()
        csrf = await there.csrf("/logout")
        await there.http.post("/logout", data={"csrf": csrf, "everywhere": "1"})
        assert "You signed out on all your devices from another device." in await reason_page(here)
        # the device that signed out is told nothing more than that it is signed out
        page = await there.http.get("/signed-out")
        assert "You are signed out of this gateway." in page.text

        await here.login()
        admin = await admin_browser(h)
        csrf = await admin.csrf("/admin/users")
        r = await admin.http.post(
            "/api/v1/admin/users/user-1", json={"action": "revoke"}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 200, r.text
        assert "An admin of this gateway signed you out on every device." in await reason_page(here)
        h.idp.user = {**h.idp.user, "sub": SUB, "groups": ["mtg-users"]}
        await here.login()
        r = await admin.http.post("/admin/users/user-1", data={"csrf": csrf, "action": "disable"})
        assert r.status_code == 303
        assert "An admin turned off your account" in await reason_page(here)
        await here.aclose()
        await there.aclose()
        await admin.aclose()


async def test_the_signed_out_page_leaves_a_live_session_alone(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        b = Browser(h)
        await b.login()
        page = await b.http.get("/signed-out")
        assert "mtg_session" not in page.headers.get("set-cookie", "")
        assert (await b.http.get("/account/details")).status_code == 200
        # a crafted address can't make it say anything: only the session's own record does
        page = await b.http.get("/signed-out?reason=removed&why=removed")
        assert "no longer in the group" not in page.text
        await b.aclose()


async def test_the_session_cookie_outlives_its_session_to_be_told_why(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        r = await Browser(h).login()
        m = re.search(r"Max-Age=(\d+)", r.headers["set-cookie"])
        assert m and int(m.group(1)) == 7 * DAY + db_module.SESSION_END_NOTICE


# -- (d) Guide and Install read signed out ---------------------------------------------------------
def _main(page: str) -> str:
    m = re.search(r"<main class='[^']*' id='main' tabindex='-1'>.*?</main>", page, re.S)
    assert m, page[:500]
    return m.group(0)


async def test_the_guide_reads_signed_out_and_holds_nothing_of_a_member(tmp_path: Path, idp: FakeIdP) -> None:
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        anon = await h.http.get("/guide", headers=NAV)
        assert anon.status_code == 200
        assert "class='btn signin' href='/login'" in anon.text  # the way in, in the top bar
        assert "name='csrf'" not in anon.text and "/account/avatar" not in anon.text
        # the contents page and a page a topic (decision C8), every one of them read signed out
        topics = sorted(set(re.findall(r"href='(/guide/[a-z0-9-]+)'", _main(anon.text))))
        assert len(topics) >= 10, topics
        b = Browser(h)
        await b.login()
        member = await b.http.get("/guide", headers=NAV)
        assert member.status_code == 200 and "/account/avatar" in member.text
        # the same guide either way: only the navigation around it differs
        assert _main(anon.text) == _main(member.text)
        for path in topics:
            out, inn = await h.http.get(path, headers=NAV), await b.http.get(path, headers=NAV)
            assert out.status_code == 200 and inn.status_code == 200, path
            assert "name='csrf'" not in out.text and "/account/avatar" not in out.text, path
            assert "<div class='guide' id='top'>" in out.text, path  # the topic itself, not a redirect
            assert _main(out.text) == _main(inn.text), path
        await b.aclose()


async def test_install_reads_signed_out_and_the_downloads_still_need_a_sign_in(
    tmp_path: Path, idp: FakeIdP
) -> None:
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        page = await h.http.get("/install", headers=NAV)
        assert page.status_code == 200 and "Guided setup" in page.text
        assert "Connector URL" in page.text
        skill = await h.http.get("/install?for=skill", headers=NAV)
        assert skill.status_code == 200
        assert "Sign in to see them" in skill.text and "id='gpt-text'" not in skill.text
        # an AI agent fetching it still gets the Markdown
        md = await h.http.get("/install")
        assert md.headers["content-type"].startswith("text/") and "<html" not in md.text.lower()
        assert (await h.http.get("/app")).headers["location"] == "/install?for=android"
        assert (await h.http.get("/skill")).headers["location"] == "/install?for=skill"
        r = await h.http.get("/skill/mtg-gateway.zip")
        assert r.status_code == 302 and r.headers["location"].startswith("/login")
        r = await h.http.get("/app/latest.json")
        assert r.status_code in (401, 404)


async def test_sign_in_unavailable_no_longer_loops_through_home(
    tmp_path: Path, idp: FakeIdP, monkeypatch
) -> None:
    from mtg_gateway.oidc import OIDCError

    async with running(Harness(make_settings(tmp_path), idp)) as h:

        async def down(**_kw: object) -> str:
            raise OIDCError("discovery failed")

        monkeypatch.setattr(h.oidc, "authorization_url", down)
        page = await h.http.get("/login", headers=NAV)
        assert "Sign-in unavailable" in page.text
        assert "href='/guide'" in page.text and ">Home<" not in page.text
        assert "class='btn signin'" not in page.text  # one way in on the page, not two


# -- storage -----------------------------------------------------------------------------------------
def test_the_upgrade_keeps_each_members_grant_for_their_ai_apps(tmp_path: Path) -> None:
    path = tmp_path / "gw.db"
    Database(path).close()
    raw = sqlite3.connect(path)
    raw.executescript(
        """DROP TABLE idp_grants; DROP TABLE session_ends;
           CREATE TABLE idp_grants (sub TEXT PRIMARY KEY, refresh_enc TEXT NOT NULL DEFAULT '',
               access_enc TEXT NOT NULL DEFAULT '', access_expires_at INTEGER NOT NULL DEFAULT 0,
               updated_at INTEGER NOT NULL);
           INSERT INTO idp_grants VALUES ('u1', 'r-enc', 'a-enc', 123, 456);
           PRAGMA user_version = 16;"""
    )
    raw.close()
    db = Database(path)
    try:
        grant = db.get_idp_grant("ai:u1")
        assert grant is not None
        assert (grant["sub"], grant["refresh_enc"], grant["access_enc"]) == ("u1", "r-enc", "a-enc")
        assert db.session_end_reason("anything") is None
    finally:
        db.close()


def test_the_clean_up_drops_grants_nobody_holds(tmp_path: Path) -> None:
    db = Database(tmp_path / "gw.db")
    try:
        now = int(time.time())
        db.create_browser_session("sid-1", "u1", 3600)
        held = "s:" + db_module.hash_token("sid-1")
        for holder in (held, "s:" + db_module.hash_token("gone"), "login:old", "login:new", "ai:u1"):
            db.save_idp_grant(holder, "u1", refresh_enc="r", access_enc="a", access_expires_at=0)
        with db.tx() as c:
            c.execute("UPDATE idp_grants SET updated_at = ? WHERE holder = 'login:old'", (now - 2 * DAY,))
        db.purge_expired()
        with db.tx() as c:
            left = sorted(r[0] for r in c.execute("SELECT holder FROM idp_grants"))
        assert left == sorted([held, "login:new", "ai:u1"])
        # signing out on the device takes its grant along
        db.delete_browser_session("sid-1")
        assert db.get_idp_grant(held) is None
    finally:
        db.close()
