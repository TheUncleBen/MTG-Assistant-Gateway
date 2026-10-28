"""The Android app's sign-in through the phone's browser (app_signin.py): passkeys and password
managers work there, and only the app that started the sign-in can finish it."""

from __future__ import annotations

import json
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mtg_gateway.app_page import app_package
from mtg_gateway.app_signin import FRESH_SKEW, RETURN_PATH, AppSignins, challenge_of, intent_url

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running

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
    back = await browser.get(f"{cb.path}?{cb.query}")
    if back.status_code != 303:
        return back
    # The callback hands over at the gateway's own address (RETURN_PATH): the Auth Tab the app
    # opened closes there by itself; any other browser shows the page with the button.
    to = urlparse(back.headers["location"])
    assert to.path == RETURN_PATH and not to.netloc, back.headers["location"]
    return await browser.get(f"{to.path}?{to.query}")


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
        assert re.search(r"<script src='/static/app-signin.js\?v=[0-9a-f]{12}'></script>", r.text)
        # Sign in inside the app instead: a plain link; an app without the browser sign-in is no
        # longer sent there by itself.
        assert "href='/login?next=/decks&amp;inapp=1'" in r.text and "data-inapp" not in r.text
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
        assert (await browser.get("/account/details")).status_code == 302

        done = await app.post("/login/app", data={"code": code, "verifier": verifier}, headers=APP_UA)
        assert done.status_code == 303 and done.headers["location"] == "/decks"
        assert "mtg_session=" in done.headers["set-cookie"]
        assert (await app.get("/account/details")).status_code == 200
        rows = harness.db.audit_recent(5)
        assert any(
            r["event"] == "browser_login" and (r["detail"] or {}).get("via") == "android_app" for r in rows
        )

        # The code works once.
        again = await httpx.AsyncClient(
            transport=httpx.ASGITransport(app=harness.app), base_url=GATEWAY
        ).post("/login/app", data={"code": code, "verifier": verifier}, headers=APP_UA)
        assert again.status_code == 400 and "mtg_session=" not in again.headers.get("set-cookie", "")


async def test_code_without_the_right_verifier_is_refused_and_burnt(harness: Harness) -> None:
    verifier, challenge = pair()
    async with client(harness) as browser, client(harness) as thief:
        code = code_from(await browser_leg(harness, browser, challenge))
        wrong = await thief.post(
            "/login/app", data={"code": code, "verifier": secrets.token_urlsafe(32)}, headers=APP_UA
        )
        assert wrong.status_code == 400 and "mtg_session=" not in wrong.headers.get("set-cookie", "")
        # One wrong try burns it: the right verifier no longer helps either.
        late = await thief.post("/login/app", data={"code": code, "verifier": verifier}, headers=APP_UA)
        assert late.status_code == 400
        for bad in ({}, {"code": "x", "verifier": verifier}, {"code": code, "verifier": "short"}):
            assert (await thief.post("/login/app", data=bad, headers=APP_UA)).status_code == 400


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


def test_the_package_id_comes_from_the_app_the_gateway_ships(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No setting: the package ID the build recorded next to the APK, else the project's own."""
    monkeypatch.setattr("mtg_gateway.app_page.IMAGE_APP_DIR", tmp_path / "nowhere")
    assert app_package() == "local.mtgassistantgateway.app"
    app_dir = tmp_path / "app-dist"
    app_dir.mkdir()
    (app_dir / "mtg-assistant-gateway.apk").write_bytes(b"PK")
    monkeypatch.setattr("mtg_gateway.app_page.IMAGE_APP_DIR", app_dir)
    meta = app_dir / "mtg-assistant-gateway.json"
    meta.write_text('{"package_name": "com.example.mtg"}')
    assert app_package() == "com.example.mtg"
    for bad in ("nodots", "com.example.mtg;end", "1com.example", "com..example", "a." * 120 + "b"):
        meta.write_text(json.dumps({"package_name": bad}))
        assert app_package() == "local.mtgassistantgateway.app", bad
    meta.write_text("{}")
    assert app_package() == "local.mtgassistantgateway.app"


async def test_only_the_app_can_finish_a_sign_in(harness: Harness) -> None:
    """Login CSRF: an attacker signs in as themselves, then makes a victim's browser post their
    code and verifier. A normal browser, or any post from another site, is refused."""
    verifier, challenge = pair()
    async with client(harness) as attacker, client(harness) as victim:
        code = code_from(await browser_leg(harness, attacker, challenge))
        data = {"code": code, "verifier": verifier}
        lures = (
            {},  # the victim's normal browser
            {**APP_UA, "Sec-Fetch-Site": "cross-site", "Origin": "https://evil.example"},
            {**APP_UA, "Sec-Fetch-Site": "same-site"},
        )
        for headers in lures:
            r = await victim.post("/login/app", data=data, headers=headers)
            assert r.status_code == 403 and "mtg_session=" not in r.headers.get("set-cookie", "")
        # Refused before the code is read: the app (no Sec-Fetch-Site, or "none") still finishes.
        ok = await victim.post("/login/app", data=data, headers={**APP_UA, "Sec-Fetch-Site": "none"})
        assert ok.status_code == 303 and "mtg_session=" in ok.headers["set-cookie"]


async def test_fresh_survives_backing_out_until_a_sign_in_completes(harness: Harness) -> None:
    verifier, challenge = pair()
    async with client(harness) as app, client(harness) as browser:
        app.cookies.set("__Host-mtg_fresh_login", "1", domain=urlparse(GATEWAY).hostname)
        first = await app.get("/login", params={"next": "/"}, headers=APP_UA)
        assert "mtg_fresh_login" not in first.headers.get("set-cookie", "")
        again = await app.get("/login", params={"next": "/"}, headers=APP_UA)
        assert "data-fresh='1'" in again.text  # backed out of the browser: still a fresh sign-in
        code = code_from(await browser_leg(harness, browser, challenge))
        done = await app.post("/login/app", data={"code": code, "verifier": verifier}, headers=APP_UA)
        assert done.status_code == 303
        assert any("mtg_fresh_login=" in v and "Max-Age=0" in v for v in done.headers.get_list("set-cookie"))


async def test_return_page_opens_the_app_once_and_keeps_the_button(harness: Harness) -> None:
    """The last fallback (a browser without Auth Tabs, or an older app): the page tries the app's
    intent: link once by itself (app-signin.js, once per code), and its button names the app."""
    _v, challenge = pair()
    async with client(harness) as browser:
        page = await browser_leg(harness, browser, challenge)
        assert "Open the MTG Assistant Gateway app" in page.text
        assert re.search(r"<script src='/static/app-signin.js\?v=[0-9a-f]{12}'></script>", page.text)
        assert "script-src 'self'" in page.headers["content-security-policy"]
        js = (await browser.get("/static/app-signin.js")).text
        assert 'getElementById("app-return")' in js and "sessionStorage" in js


async def test_the_callback_returns_through_the_gateways_own_address(harness: Harness) -> None:
    """Android's Auth Tab closes itself on an https address the gateway vouches for the app on
    (assetlinks.json), so the callback sends the code there instead of drawing the page itself."""
    _v, challenge = pair()
    async with client(harness) as browser:
        r = await browser.get("/login", params={"next": "/decks", "app_challenge": challenge})
        # the app's first try: the verified https Auth Tab, which needs no new sign-in at the
        # identity provider (only the app assetlinks.json names gets this address back)
        assert "prompt" not in parse_qs(urlparse(r.headers["location"]).query)
        cb = urlparse(await harness.idp_leg(r))
        back = await browser.get(f"{cb.path}?{cb.query}")
        assert back.status_code == 303, back.text
        assert back.headers["cache-control"] == "no-store"
        assert back.headers["referrer-policy"] == "no-referrer"
        assert "mtg_session=" not in back.headers.get("set-cookie", "")
        loc = urlparse(back.headers["location"])
        assert loc.path == RETURN_PATH
        code = parse_qs(loc.query)["code"][0]
        page = await browser.get(f"{loc.path}?{loc.query}")
        assert page.status_code == 200
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["referrer-policy"] == "no-referrer"
        # the page shows the code only; looking at it does not use it up
        assert code_from(page) == code
        assert code_from(await browser.get(f"{loc.path}?{loc.query}")) == code


async def test_the_return_address_refuses_anything_but_a_code(harness: Harness) -> None:
    async with client(harness) as browser:
        for q in ("", "?code=short", "?code=" + "a" * 43 + "%27"):
            r = await browser.get(RETURN_PATH + q)
            assert r.status_code == 400, q
            assert "intent://" not in r.text


async def test_assetlinks_vouch_for_the_bundled_app_without_a_setting(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no MTG_ANDROID_ASSETLINKS, the gateway vouches for the app it ships (the signing
    certificate and package ID the build recorded next to the APK): nothing to set up."""
    app_dir = tmp_path / "app-dist"
    app_dir.mkdir()
    (app_dir / "mtg-assistant-gateway.apk").write_bytes(b"PK\x03\x04" + b"\0" * 100)
    cert = "ab" * 32
    (app_dir / "mtg-assistant-gateway.json").write_text(
        json.dumps({"cert_sha256": cert, "package_name": "local.mtgassistantgateway.app"})
    )
    monkeypatch.setattr("mtg_gateway.app_page.IMAGE_APP_DIR", app_dir)
    async with running(Harness(make_settings(tmp_path), idp)) as h, client(h) as web:
        r = await web.get("/.well-known/assetlinks.json")
        assert r.status_code == 200
        assert r.json() == [
            {
                "relation": ["delegate_permission/common.handle_all_urls"],
                "target": {
                    "namespace": "android_app",
                    "package_name": "local.mtgassistantgateway.app",
                    "sha256_cert_fingerprints": [":".join(["AB"] * 32)],
                },
            }
        ]
    # no app shipped (or no certificate recorded): nothing is vouched for
    monkeypatch.setattr("mtg_gateway.app_page.IMAGE_APP_DIR", tmp_path / "nowhere")
    async with running(Harness(make_settings(tmp_path / "b"), idp)) as h, client(h) as web:
        assert (await web.get("/.well-known/assetlinks.json")).json() == []


# -- 0.7.46: the retry on the app's own scheme, when Digital Asset Links can't verify the app -------
async def scheme_leg(h: Harness, browser: httpx.AsyncClient, challenge: str, next_path: str = "/decks"):
    r = await browser.get(
        "/login", params={"next": next_path, "app_challenge": challenge, "app_return": "scheme"}
    )
    assert r.status_code == 302, r.text
    return r, await browser.get("{0.path}?{0.query}".format(urlparse(await h.idp_leg(r))))


async def test_a_sign_in_started_for_the_apps_scheme_comes_back_by_itself(harness: Harness) -> None:
    """The app's retry names its own scheme (mtgassistant-signin): the callback ends the browser
    leg with a redirect there, which the Auth Tab hands to the app that opened it and closes, no
    tap and no assetlinks.json. The code is the same one-time code, bound to the app's challenge."""
    verifier, challenge = pair()
    async with client(harness) as browser, client(harness) as app:
        start, back = await scheme_leg(harness, browser, challenge)
        # the identity provider is asked for a sign-in of its own (security note: another app on
        # the phone could start this leg too, and a silent pass-through must not hand it a session)
        assert parse_qs(urlparse(start.headers["location"]).query).get("prompt") == ["login"]
        assert back.status_code == 303, back.text
        assert back.headers["cache-control"] == "no-store"
        assert back.headers["referrer-policy"] == "no-referrer"
        assert "mtg_session=" not in back.headers.get("set-cookie", "")
        to = urlparse(back.headers["location"])
        assert (to.scheme, to.netloc, to.path) == ("mtgassistant-signin", "signin", "")
        (code,) = parse_qs(to.query)["code"]
        assert re.fullmatch(r"[A-Za-z0-9_-]{43}", code) and to.query == f"code={code}"
        # without the verifier the code is worth nothing, and it is burnt by the try
        bad = await app.post("/login/app", data={"code": code, "verifier": pair()[0]}, headers=APP_UA)
        assert bad.status_code == 400

    verifier, challenge = pair()
    async with client(harness) as browser, client(harness) as app:
        _start, back = await scheme_leg(harness, browser, challenge)
        code = parse_qs(urlparse(back.headers["location"]).query)["code"][0]
        done = await app.post("/login/app", data={"code": code, "verifier": verifier}, headers=APP_UA)
        assert done.status_code == 303 and done.headers["location"] == "/decks"
        assert "mtg_session=" in done.headers["set-cookie"]
        assert (await app.get("/account/details")).status_code == 200


async def test_only_the_apps_scheme_can_be_asked_for(harness: Harness) -> None:
    _v, challenge = pair()
    async with client(harness) as browser:
        for odd in ("https", "evil-app", "SCHEME", ""):
            r = await browser.get(
                "/login", params={"next": "/", "app_challenge": challenge, "app_return": odd}
            )
            assert r.status_code == 400, odd
        # and only with the app's challenge: a plain browser sign-in has no code to hand over
        r = await browser.get("/login", params={"next": "/", "app_return": "scheme"})
        assert r.status_code == 302 and "app_return" not in r.headers["location"]


async def test_a_scheme_leg_needs_a_sign_in_made_after_it_started(harness: Harness, idp: FakeIdP) -> None:
    """The gateway does not rely on the identity provider honouring prompt=login: the ID token's
    auth_time must be no older than the leg (a minute's clock skew allowed). Without it the leg
    ends on the app's scheme with no code at all, and the app finishes in a Custom Tab instead."""
    idp.honours_prompt_login = False  # a provider that passes the browser straight through
    for auth_time in (None, int(time.time()) - 3600, int(time.time()) - FRESH_SKEW - 30):
        idp.auth_time = auth_time
        _v, challenge = pair()
        async with client(harness) as browser:
            _start, back = await scheme_leg(harness, browser, challenge)
            assert back.status_code == 303, back.text
            assert back.headers["location"] == "mtgassistant-signin://signin?error=sign_in_not_fresh"
            assert back.headers["cache-control"] == "no-store"
            assert "mtg_session=" not in back.headers.get("set-cookie", "")
        assert not harness.app.state.gateway.app_signins._codes, auth_time  # no code was made for anyone

    # a sign-in at the provider within the skew counts (clocks differ a little)
    verifier, challenge = pair()
    idp.auth_time = int(time.time()) - FRESH_SKEW + 20
    async with client(harness) as browser, client(harness) as app:
        _start, back = await scheme_leg(harness, browser, challenge)
        (code,) = parse_qs(urlparse(back.headers["location"]).query)["code"]
        done = await app.post("/login/app", data={"code": code, "verifier": verifier}, headers=APP_UA)
        assert done.status_code == 303 and "mtg_session=" in done.headers["set-cookie"]


async def test_the_https_leg_needs_no_auth_time(harness: Harness, idp: FakeIdP) -> None:
    """The first try (the verified https Auth Tab, or a Custom Tab) asks for no new sign-in and so
    checks no auth_time: the address it returns on reaches only the app assetlinks.json names, or
    the page whose button opens only the app's package."""
    idp.honours_prompt_login = False
    idp.auth_time = int(time.time()) - 86400
    _v, challenge = pair()
    async with client(harness) as browser:
        page = await browser_leg(harness, browser, challenge)
        assert page.status_code == 200 and code_from(page)


def test_the_app_can_see_the_browsers_that_offer_auth_tabs() -> None:
    """Android 11 and later hide other apps from an app unless its manifest asks to see them: without
    these queries, CustomTabsClient finds no browser, so no Auth Tab is ever opened and every
    sign-in ends on the page that waits for a tap."""
    import xml.etree.ElementTree as ET

    ns = "{http://schemas.android.com/apk/res/android}"
    root = ET.parse(
        Path(__file__).resolve().parent.parent / "android/app/src/main/AndroidManifest.xml"
    ).getroot()
    queries = root.find("queries")
    assert queries is not None
    actions = {a.get(f"{ns}name") for a in queries.iter("action")}
    assert "android.support.customtabs.action.CustomTabsService" in actions
    assert "android.intent.action.VIEW" in actions
    # the scheme the Auth Tab returns on is the app's own, held by its main activity
    filters = root.find("application").find("activity").findall("intent-filter")
    assert any(
        d.get(f"{ns}scheme") == "mtgassistant-signin" and d.get(f"{ns}host") == "signin"
        for f in filters
        for d in f.iter("data")
    )
