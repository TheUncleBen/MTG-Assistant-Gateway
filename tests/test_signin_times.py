"""The four sign-in times on the admin Settings page (owner, 28 Oct 2026), in one "Sign-in" group:
how long a website sign-in lasts unused, the longest a website sign-in lasts however much it is
used, and how long an assistant's access token and refresh token last. Each applies to the next
sign-in or token at once, with no restart. A stack value (MTG_BROWSER_SESSION_TTL,
MTG_ACCESS_TOKEN_TTL, MTG_REFRESH_TOKEN_TTL) locks its row; the longest time has no stack value.
The unused time may not be longer than the longest time."""

from __future__ import annotations

import html
import time
from pathlib import Path

import pytest

from mtg_gateway import settings_page
from mtg_gateway.config import MANAGED_SETTINGS, SettingError, load_settings

from .conftest import FakeIdP, Harness, make_settings, running
from .test_admin import _with_admin, admin_browser
from .test_backup_and_config import _write_secrets
from .test_browser_sessions import SUB, _max_age, _move, _session_cookies
from .test_decks_and_proxy import Browser
from .test_settings_page import _live, _post, _words

DAY = 24 * 3600
KEYS = ("browser_session_ttl", "browser_session_max_age", "access_token_ttl", "refresh_token_ttl")


@pytest.fixture
async def gw(tmp_path: Path, idp: FakeIdP):
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
    async with running(_with_admin(Harness(settings, idp))) as h:
        yield h


def test_the_four_rows_are_one_sign_in_group_with_todays_defaults() -> None:
    assert [k for k, s in MANAGED_SETTINGS.items() if s.group == "signin"] == list(KEYS)
    assert ("signin", "Sign-in") == settings_page.GROUPS[
        [g for g, _t, _i in settings_page.GROUPS].index("signin")
    ][:2]
    shown = {k: settings_page.shown(MANAGED_SETTINGS[k], MANAGED_SETTINGS[k].default) for k in KEYS}
    assert shown == {
        "browser_session_ttl": "7 days",
        "browser_session_max_age": "30 days",
        "access_token_ttl": "1 hour",
        "refresh_token_ttl": "30 days",
    }
    envs = {k: MANAGED_SETTINGS[k].env for k in KEYS}
    assert envs == {
        "browser_session_ttl": "MTG_BROWSER_SESSION_TTL",
        "browser_session_max_age": "",
        "access_token_ttl": "MTG_ACCESS_TOKEN_TTL",
        "refresh_token_ttl": "MTG_REFRESH_TOKEN_TTL",
    }
    assert not any(MANAGED_SETTINGS[k].restart for k in KEYS)  # read per sign-in and per token


async def test_the_page_shows_the_rows_and_a_saved_unused_time_applies_to_the_next_sign_in(
    gw: Harness,
) -> None:
    b = await admin_browser(gw)
    try:
        page = await b.http.get("/admin/settings")
        words = _words(page.text)
        for name in (
            "Website sign-in when unused",
            "Longest website sign-in",
            "Assistant access",
            "Assistant refresh",
        ):
            assert name in words, name
        r = await _post(
            b,
            {
                "group": "signin",
                "v_browser_session_ttl": "2",
                "v_browser_session_max_age": "10",
                "v_access_token_ttl": "2",
                "v_refresh_token_ttl": "5",
            },
        )
        assert r.status_code == 303, html.unescape(r.text)
    finally:
        await b.aclose()
    live = _live(gw)
    assert live.get("browser_session_ttl") == 2 and live.seconds("browser_session_ttl") == 2 * DAY
    # the next sign-in lasts two days unused, with no restart
    m = Browser(gw)
    try:
        r = await m.login()
        assert _max_age(r.headers["set-cookie"]) == 2 * DAY
        with gw.db.tx() as c:
            (made,) = c.execute(
                "SELECT expires_at - created_at FROM browser_sessions ORDER BY rowid DESC"
            ).fetchone()
        assert made == 2 * DAY
    finally:
        await m.aclose()
    # and the next assistant tokens last 2 hours and 5 days
    client = await gw.register()
    tokens = await gw.tokens_for(client)
    assert tokens["expires_in"] == 2 * 3600
    with gw.db.tx() as c:
        rows = dict(c.execute("SELECT kind, MAX(expires_at) FROM tokens GROUP BY kind").fetchall())
    now = int(time.time())
    assert abs(rows["access"] - (now + 2 * 3600)) <= 5
    assert abs(rows["refresh"] - (now + 5 * DAY)) <= 5


async def test_the_longest_time_caps_the_session_on_the_server_and_its_renewal(gw: Harness) -> None:
    _live(gw).set("browser_session_max_age", 10, by="admin-1")
    b = Browser(gw)
    try:
        await b.login()
        # 8 days in, with 2 days left: renewed only up to day 10, two days from now
        _move(gw, created_ago=8 * DAY, expires_in=DAY)
        page = await b.http.get("/account/details")
        assert page.status_code == 200
        (cookie,) = _session_cookies(page)
        assert 2 * DAY - 5 <= _max_age(cookie) <= 2 * DAY
        # past day 10 it is refused, whatever its row says
        _move(gw, created_ago=10 * DAY + 60, expires_in=5 * DAY)
        page = await b.http.get("/account/details")
        assert page.status_code == 302 and page.headers["location"].startswith("/login")
    finally:
        await b.aclose()
    # a new sign-in is never made longer than the longest time
    _live(gw).set("browser_session_max_age", 3, by="admin-1")
    _live(gw).set("browser_session_ttl", 3, by="admin-1")
    assert gw.db.session_max_age() == 3 * DAY
    gw.db.create_browser_session("x" * 43, SUB, 7 * DAY)
    with gw.db.tx() as c:
        made = c.execute(
            "SELECT expires_at - created_at FROM browser_sessions ORDER BY created_at DESC"
        ).fetchone()
    assert made[0] <= 3 * DAY


async def test_an_unused_time_longer_than_the_longest_is_refused(gw: Harness) -> None:
    b = await admin_browser(gw)
    try:
        r = await _post(
            b, {"group": "signin", "v_browser_session_ttl": "20", "v_browser_session_max_age": "10"}
        )
        assert r.status_code == 400
        text = html.unescape(r.text)
        assert "Must not be longer than “Longest website sign-in” (10 days)." in text
        live = _live(gw)
        assert live.get("browser_session_ttl") == 7 and live.get("browser_session_max_age") == 30
        # lowering only the longest time below the unused one is refused the same way
        r = await _post(
            b, {"group": "signin", "v_browser_session_ttl": "7", "v_browser_session_max_age": "5"}
        )
        assert r.status_code == 400
        assert "Must not be shorter than “Website sign-in when unused” (7 days)." in html.unescape(r.text)
    finally:
        await b.aclose()


async def test_a_stack_value_locks_its_row(tmp_path: Path, idp: FakeIdP) -> None:
    settings = make_settings(
        tmp_path,
        required_group="mtg-users",
        admin_group="mtg-admins",
        browser_session_ttl=2 * DAY,
        access_token_ttl=1800,
        env_set=frozenset({"browser_session_ttl", "access_token_ttl"}),
    )
    async with running(_with_admin(Harness(settings, idp))) as h:
        live = _live(h)
        assert live.locked("browser_session_ttl") and live.get("browser_session_ttl") == 2
        assert live.seconds("browser_session_ttl") == 2 * DAY
        assert live.seconds("access_token_ttl") == 1800 and live.get("access_token_ttl") == 0.5
        assert not live.locked("browser_session_max_age")
        with pytest.raises(SettingError):
            live.set("browser_session_ttl", 3, by="admin-1")
        b = await admin_browser(h)
        try:
            page = html.unescape((await b.http.get("/admin/settings")).text)
            row = page.split("id='s-row-browser_session_ttl'", 1)[1].split("id='s-row-", 1)[0]
            assert "Set in your stack" in row and "MTG_BROWSER_SESSION_TTL" in _words(row)
        finally:
            await b.aclose()


def test_the_stack_ranges_stay_as_they_were(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_ACCESS_TOKEN_TTL", "60")
    monkeypatch.setenv("MTG_REFRESH_TOKEN_TTL", "3600")
    s = load_settings()
    assert s.access_token_ttl == 60 and s.refresh_token_ttl == 3600
    assert s.env_set >= {"access_token_ttl", "refresh_token_ttl"}
