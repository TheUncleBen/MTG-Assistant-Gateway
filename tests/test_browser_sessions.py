"""How long a sign-in lasts in a browser and in the Android app (Ben, 27 Oct 2026: asked to sign
in again after a few hours, far more often than his identity provider's "stay signed in").

A session lasts MTG_BROWSER_SESSION_TTL seconds unused (7 days by default) and is renewed by use:
once more than half of that has passed since it was made or last renewed, the next request moves
its end to a full window from now and sets the cookie again. Whatever the use, it ends
BROWSER_SESSION_MAX_AGE (30 days) after the sign-in. Both ends are kept by the server, not only by
the cookie's own expiry."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from mtg_gateway.config import ConfigError, load_settings
from mtg_gateway.db import BROWSER_SESSION_MAX_AGE

from .conftest import FakeIdP, Harness, make_settings, running
from .test_backup_and_config import _write_secrets
from .test_decks_and_proxy import Browser

DAY = 24 * 3600
SUB = "user-1"


def _max_age(set_cookie: str) -> int:
    m = re.search(r"mtg_session=[^;]+;.*?Max-Age=(\d+)", set_cookie, re.I)
    assert m, set_cookie
    return int(m.group(1))


def _session_cookies(r) -> list[str]:  # noqa: ANN001
    return [v for v in r.headers.get_list("set-cookie") if "mtg_session=" in v]


def _row(h: Harness) -> tuple[int, int]:
    with h.db.tx() as c:
        row = c.execute(
            "SELECT created_at, expires_at FROM browser_sessions WHERE sub = ?", (SUB,)
        ).fetchone()
    return int(row[0]), int(row[1])


def _move(h: Harness, *, created_ago: int, expires_in: int) -> None:
    now = int(time.time())
    with h.db.tx() as c:
        c.execute(
            "UPDATE browser_sessions SET created_at = ?, expires_at = ? WHERE sub = ?",
            (now - created_ago, now + expires_in, SUB),
        )


@pytest.fixture
async def harness(tmp_path: Path, idp: FakeIdP):
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        yield h


async def test_a_sign_in_lasts_a_week_unused(harness: Harness) -> None:
    b = Browser(harness)
    r = await b.login()
    assert _max_age(r.headers["set-cookie"]) == 7 * DAY
    created, expires = _row(harness)
    assert expires - created == 7 * DAY
    # Used soon after: nothing to renew yet, so no new cookie and no write.
    page = await b.http.get("/account/details")
    assert page.status_code == 200 and not _session_cookies(page)
    assert _row(harness) == (created, expires)
    await b.aclose()


async def test_use_after_half_the_window_renews_the_session(harness: Harness) -> None:
    b = Browser(harness)
    await b.login()
    _move(harness, created_ago=4 * DAY, expires_in=3 * DAY)  # four days in, three days left
    page = await b.http.get("/account/details")
    assert page.status_code == 200
    (cookie,) = _session_cookies(page)
    assert 7 * DAY - 5 <= _max_age(cookie) <= 7 * DAY
    assert "httponly" in cookie.lower() and "samesite=lax" in cookie.lower() and "path=/" in cookie.lower()
    created, expires = _row(harness)
    assert 7 * DAY - 5 <= expires - int(time.time()) <= 7 * DAY
    again = await b.http.get("/account/details")
    assert again.status_code == 200 and not _session_cookies(again)  # renewed once, not on every use
    await b.aclose()


async def test_a_session_never_outlives_thirty_days_from_its_sign_in(harness: Harness) -> None:
    assert BROWSER_SESSION_MAX_AGE == 30 * DAY
    b = Browser(harness)
    await b.login()
    # 27 days in, with 2 days left: renewed only up to day 30, three days from now, not seven.
    _move(harness, created_ago=27 * DAY, expires_in=2 * DAY)
    page = await b.http.get("/account/details")
    assert page.status_code == 200
    (cookie,) = _session_cookies(page)
    assert 3 * DAY - 5 <= _max_age(cookie) <= 3 * DAY
    # On day 30 it may last no longer: nothing more to renew.
    _move(harness, created_ago=30 * DAY - 60, expires_in=60)
    page = await b.http.get("/account/details")
    assert page.status_code == 200 and not _session_cookies(page)
    # A row that says it runs on (an old release, or a clock moved) is still refused past day 30.
    _move(harness, created_ago=30 * DAY + 60, expires_in=5 * DAY)
    page = await b.http.get("/account/details")
    assert page.status_code == 302 and page.headers["location"].startswith("/login")
    await b.aclose()


async def test_an_unused_session_ends_on_the_server(harness: Harness) -> None:
    b = Browser(harness)
    await b.login()
    _move(harness, created_ago=8 * DAY, expires_in=-DAY)  # unused for over a week
    page = await b.http.get("/account/details")
    assert page.status_code == 302 and not _session_cookies(page)
    await b.aclose()


async def test_signing_out_is_not_undone_by_a_renewal(harness: Harness) -> None:
    b = Browser(harness)
    await b.login()
    _move(harness, created_ago=4 * DAY, expires_in=3 * DAY)
    csrf = await b.csrf()  # renews the session (and is answered with the cookie set again)
    out = await b.http.post("/logout", data={"csrf": csrf})
    assert out.status_code in (302, 303)
    assert all("max-age=0" in v.lower() or 'mtg_session=""' in v for v in _session_cookies(out))
    assert (await b.http.get("/account/details")).status_code == 302
    await b.aclose()


def test_browser_session_ttl_is_the_unused_window(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.delenv("MTG_BROWSER_SESSION_TTL", raising=False)
    assert load_settings().browser_session_ttl == 7 * DAY
    monkeypatch.setenv("MTG_BROWSER_SESSION_TTL", "7200")  # a stack file from before 0.7.46
    assert load_settings().browser_session_ttl == 7200
    monkeypatch.setenv("MTG_BROWSER_SESSION_TTL", str(30 * DAY))
    assert load_settings().browser_session_ttl == 30 * DAY
    monkeypatch.setenv("MTG_BROWSER_SESSION_TTL", str(30 * DAY + 1))
    with pytest.raises(ConfigError):
        load_settings()
