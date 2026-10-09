"""The Android app's sign-in through the phone's browser (app_signin.py): passkeys and password
managers work there, and only the app that started the sign-in can finish it."""

from __future__ import annotations

import re
import secrets
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mtg_gateway.app_signin import AppSignins, challenge_of, intent_url
from mtg_gateway.config import ConfigError, load_settings

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from .test_admin import _env

APP_UA = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 16; wv) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.7.9"
}


@pytest.fixture
async def harness(tmp_path: Path, idp: FakeIdP):
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        yield h


def client(h: Harness) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url=GATEWAY)


def pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(32)
    return verifier, challenge_of(verifier)


async def browser_leg(
    h: Harness, browser: httpx.AsyncClient, challenge: str, next_path: str = "/decks"
) -> httpx.Response:
    """The part that runs in the phone's browser: /login with the challenge, the identity provider,
    then /auth/callback, which answers with the page that opens the app."""
    r = await browser.get("/login", params={"next": next_path, "app_challenge": challenge})
    assert r.status_code == 302, r.text
    cb = urlparse(await h.idp_leg(r))
    return await browser.get(f"{cb.path}?{cb.query}")


def code_from(page: httpx.Response) -> str:
    m = re.search(
        r"href='intent://signin\?code=([A-Za-z0-9_-]{43})#Intent;scheme=mtgassistant-signin;"
        r"package=local\.mtgassistantgateway\.app;end'",
        page.text,
    )
    assert m, page.text
    return m.group(1)


async def test_login_in_the_app_asks_for_the_browser_sign_in(harness: Harness) -> None:
    async with client(harness) as app:
        r = await app.get("/login", params={"next": "/decks"}, headers=APP_UA)
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store"
        assert "id='app-signin'" in r.text and "data-next='/decks'" in r.text
        assert "<script src='/static/app-signin.js'></script>" in r.text
        # An older app (no signInWithBrowser) follows this link to the in-app sign-in, as before.
        assert "data-inapp='/login?next=/decks&amp;inapp=1'" in r.text
        assert "script-src 'self'" in r.headers["content-security-policy"]
        old = await app.get("/login", params={"next": "/decks", "inapp": "1"}, headers=APP_UA)
        assert old.status_code == 302 and old.headers["location"].startswith("https://idp.test")
        js = await app.get("/static/app-signin.js")
        assert js.status_code == 200 and "signInWithBrowser" in js.text
    # A normal browser is not affected.
    async with client(harness) as web:
        r = await web.get("/login", params={"next": "/decks"})
        assert r.status_code == 302 and "app_challenge" not in r.headers["location"]


async def test_browser_sign_in_hands_a_code_only_the_app_can_use(harness: Harness) -> None:
    verifier, challenge = pair()
    async with client(harness) as browser, client(harness) as app:
        page = await browser_leg(harness, browser, challenge)
        assert page.status_code == 200, page.text
        # The browser gets no session of its own, and the page is not cached or leaked onwards.
        assert "mtg_session=" not in page.headers.get("set-cookie", "")
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["referrer-policy"] == "no-referrer"
        code = code_from(page)
        assert (await browser.get("/account")).status_code == 302

        done = await app.post("/login/app", data={"code": code, "verifier": verifier}, headers=APP_UA)
        assert done.status_code == 303 and done.headers["location"] == "/decks"
        assert "mtg_session=" in done.headers["set-cookie"]
        assert (await app.get("/account")).status_code == 200
        rows = harness.db.audit_recent(5)
        assert any(
            r["event"] == "browser_login" and (r["detail"] or {}).get("via") == "android_app" for r in rows
        )

        # The code works once.
        again = await httpx.AsyncClient(
            transport=httpx.ASGITransport(app=harness.app), base_url=GATEWAY
        ).post("/login/app", data={"code": code, "verifier": verifier})
        assert again.status_code == 400 and "mtg_session=" not in again.headers.get("set-cookie", "")


async def test_code_without_the_right_verifier_is_refused_and_burnt(harness: Harness) -> None:
    verifier, challenge = pair()
    async with client(harness) as browser, client(harness) as thief:
        code = code_from(await browser_leg(harness, browser, challenge))
        wrong = await thief.post("/login/app", data={"code": code, "verifier": secrets.token_urlsafe(32)})
        assert wrong.status_code == 400 and "mtg_session=" not in wrong.headers.get("set-cookie", "")
        # One wrong try burns it: the right verifier no longer helps either.
        late = await thief.post("/login/app", data={"code": code, "verifier": verifier})
        assert late.status_code == 400
        for bad in ({}, {"code": "x", "verifier": verifier}, {"code": code, "verifier": "short"}):
            assert (await thief.post("/login/app", data=bad)).status_code == 400


async def test_bad_challenge_and_fresh_sign_in(harness: Harness) -> None:
    async with client(harness) as browser:
        r = await browser.get("/login", params={"next": "/", "app_challenge": "not-a-challenge"})
        assert r.status_code == 400
        _v, challenge = pair()
        r = await browser.get("/login", params={"next": "/", "app_challenge": challenge, "fresh": "1"})
        assert r.status_code == 302
        assert parse_qs(urlparse(r.headers["location"]).query).get("prompt") == ["login"]
        r = await browser.get("/login", params={"next": "//evil.test", "app_challenge": challenge})
        assert r.status_code == 302  # next is cleaned when the app finishes (same rule as everywhere)


async def test_in_app_login_after_sign_out_passes_fresh_on(harness: Harness) -> None:
    async with client(harness) as app:
        app.cookies.set("__Host-mtg_fresh_login", "1", domain=urlparse(GATEWAY).hostname)
        r = await app.get("/login", params={"next": "/"}, headers=APP_UA)
        assert r.status_code == 200 and "data-fresh='1'" in r.text
        assert "&amp;fresh=1" in r.text


def test_codes_expire_and_are_bounded() -> None:
    now = [0.0]
    store = AppSignins(ttl=120, clock=lambda: now[0])
    verifier, challenge = pair()
    code = store.issue("sub", challenge, "/")
    now[0] = 121
    assert store.redeem(code, verifier) is None
    code = store.issue("sub", challenge, "/")
    assert store.redeem(code, verifier) is not None
    first = store.issue("sub", challenge, "/")
    for _ in range(600):
        store.issue("sub", challenge, "/")
    assert store.redeem(first, verifier) is None  # the oldest went first
    assert intent_url("abc", "a.b").endswith("scheme=mtgassistant-signin;package=a.b;end")


def test_package_setting(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _env(tmp_path, monkeypatch)
    assert load_settings().android_package == "local.mtgassistantgateway.app"
    monkeypatch.setenv("MTG_ANDROID_PACKAGE", "com.example.mtg")
    assert load_settings().android_package == "com.example.mtg"
    for bad in ("nodots", "com.example.mtg;end", "1com.example", "com..example"):
        monkeypatch.setenv("MTG_ANDROID_PACKAGE", bad)
        with pytest.raises(ConfigError, match="MTG_ANDROID_PACKAGE"):
            load_settings()
