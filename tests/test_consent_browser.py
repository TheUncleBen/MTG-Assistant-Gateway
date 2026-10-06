"""Real-browser check of the CIMD consent page in headless Chromium (Playwright).

Chromium applies the page's form-action CSP to the redirect that follows a form
POST, so Approve (to the identity provider) and Deny (back to the client) are
exercised with real clicks. The gateway runs over HTTP on a local port, and a
second local server stands in for both the identity provider's sign-in page and
the client's redirect target, so the final navigation is a real one that the
browser either completes or refuses. Skipped without a Chromium build.
"""

from __future__ import annotations

import glob
import json
import socket
import threading
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response
from starlette.routing import Route

from mtg_gateway.app import create_app
from mtg_gateway.db import Database
from mtg_gateway.oidc import OIDCClient, pkce_pair

from .conftest import CLIENT_ID, CLIENT_SECRET, IDP, FakeIdP, make_settings
from .test_cimd import CLIENT_URL, DocHost, document

pytest.importorskip("playwright")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _chromium_path() -> str | None:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as p:
        if Path(p.chromium.executable_path).exists():
            return None
    for pattern in ("/opt/pw-browsers/chromium-*/chrome-linux/chrome", "/usr/bin/chromium*"):
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return "missing"


async def _outside(request: Request) -> Response:
    """Everything the browser is sent to off the gateway: the IdP's sign-in page, the client."""
    return PlainTextResponse(f"outside:{request.url.path}")


class _LocalIdP(FakeIdP):
    """The fake IdP, advertising a sign-in page the browser can really reach."""

    def __init__(self, authorize_url: str) -> None:
        super().__init__()
        self.authorize_url = authorize_url

    async def discovery(self, req: Request) -> Response:
        data = await super().discovery(req)
        body = json.loads(data.body)
        body["authorization_endpoint"] = self.authorize_url
        return JSONResponse(body)


class Server:
    def __init__(self, tmp_path: Path):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        self.outside_port = _free_port()
        self.outside = f"http://127.0.0.1:{self.outside_port}"
        self.redirect = f"{self.outside}/cb"
        settings = make_settings(
            tmp_path, public_url=self.base, allowed_hosts=["127.0.0.1", f"127.0.0.1:{self.port}"]
        )
        self.db = Database(settings.db_path)
        self.idp = _LocalIdP(f"{self.outside}/application/o/authorize/")
        idp_http = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=self.idp.app), base_url="https://idp.test"
        )
        oidc = OIDCClient(
            IDP, CLIENT_ID, CLIENT_SECRET, settings.callback_url, settings.oidc_scopes, http=idp_http
        )
        self.docs = DocHost()
        self.docs.serve(body=document(redirect_uris=[self.redirect]))
        self.app = create_app(settings, db=self.db, oidc=oidc, cimd=self.docs.fetcher())
        outside_app = Starlette(routes=[Route("/{rest:path}", _outside, methods=["GET"])])
        self.servers = [
            uvicorn.Server(uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")),
            uvicorn.Server(
                uvicorn.Config(outside_app, host="127.0.0.1", port=self.outside_port, log_level="warning")
            ),
        ]
        self.threads = [threading.Thread(target=s.run, daemon=True) for s in self.servers]

    def start(self) -> None:
        for t in self.threads:
            t.start()
        for url in (f"{self.base}/healthz", f"{self.outside}/healthz"):
            for _ in range(100):
                try:
                    if httpx.get(url, timeout=1).status_code == 200:
                        break
                except httpx.HTTPError:
                    time.sleep(0.1)
            else:
                raise RuntimeError(f"{url} did not come up")

    def stop(self) -> None:
        for s in self.servers:
            s.should_exit = True
        for t in self.threads:
            t.join(timeout=10)

    def authorize_url(self) -> str:
        _verifier, challenge = pkce_pair()
        q = {
            "client_id": CLIENT_URL,
            "redirect_uri": self.redirect,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "cs1",
        }
        return f"{self.base}/authorize?" + "&".join(
            f"{k}={httpx.QueryParams({k: v})[k]}" for k, v in q.items()
        )


def _unlock(page) -> None:
    """Use the page like a person: the click guard (clickguard.js) enables the buttons only
    after the page has been visible and focused for a moment and the pointer has moved."""
    page.wait_for_selector("form.guarded")
    page.mouse.move(5, 5)
    page.mouse.move(60, 90, steps=5)


def _chromium_or_skip() -> str | None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _launch(p, exe: str | None):
    return p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_consent_buttons_navigate_off_site(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")

    with sync_playwright() as p:
        browser = (
            p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()
        )
        try:
            page = browser.new_page()
            console: list[str] = []
            page.on("console", lambda m: console.append(m.text))

            # Approve: the browser must end up at the identity provider's sign-in page.
            page.goto(server.authorize_url())
            assert "Example Assistant" in page.content()
            _unlock(page)
            page.get_by_role("button", name="Approve and sign in").click()
            page.wait_for_url(f"{server.outside}/application/o/authorize/**", timeout=10_000)
            assert "outside:/application/o/authorize/" in page.content()

            # Deny: the browser must end up back at the client with access_denied.
            page.goto(server.authorize_url())
            _unlock(page)
            page.get_by_role("button", name="Deny").click()
            page.wait_for_url(f"{server.outside}/cb**", timeout=10_000)
            parsed = urlparse(page.url)
            assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == server.redirect
            q = parse_qs(parsed.query)
            assert q["error"] == ["access_denied"] and q["state"] == ["cs1"]
            assert not [m for m in console if "form-action" in m or "Refused" in m], console
        finally:
            browser.close()


APPROVE = "form.guarded button[value=approve]"


def test_consent_click_guard_defeats_a_double_click_swap(server: Server) -> None:
    """Double-clickjacking: a click that lands right after the window shows the consent page
    hits a disabled button. The buttons unlock only after the page was visible and focused for
    a moment and the pointer moved, and lock again when the window loses focus."""
    from playwright.sync_api import expect, sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.goto(server.authorize_url())
            page.wait_for_selector(APPROVE)
            assert page.is_disabled(APPROVE)
            # The "second click" of a double-click, at once, where the button is.
            box = page.locator(APPROVE).bounding_box()
            assert box is not None
            page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            page.wait_for_timeout(400)
            assert page.url.startswith(f"{server.base}/authorize/confirm"), page.url
            assert page.is_disabled(APPROVE)
            # A person who keeps using the page (moving the pointer) gets working buttons.
            page.mouse.move(10, 10)
            page.mouse.move(80, 120, steps=4)
            expect(page.locator(APPROVE)).to_be_enabled(timeout=5000)
            # Losing focus (a window swap, another tab) locks them again.
            page.evaluate("window.dispatchEvent(new Event('blur'))")
            assert page.is_disabled(APPROVE)
            assert not [e for e in errors if "Content Security Policy" in e or "Refused" in e], errors
        finally:
            browser.close()


def test_consent_page_without_javascript_enforces_a_minimum_delay(server: Server) -> None:
    """With scripts off, the <noscript> form works, but the server refuses (and shows the page
    again for) a submit that arrives faster than a person can read the page."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_context(java_script_enabled=False).new_page()
            page.goto(server.authorize_url())
            assert not page.locator("form.guarded").is_visible()
            page.get_by_role("button", name="Approve and sign in").click()
            page.wait_for_load_state()
            assert page.url.startswith(f"{server.base}/authorize/confirm"), page.url  # shown again
            page.wait_for_timeout(1200)
            page.get_by_role("button", name="Approve and sign in").click()
            page.wait_for_url(f"{server.outside}/application/o/authorize/**", timeout=10_000)
        finally:
            browser.close()


def test_consent_on_a_phone_unlocks_with_a_first_tap(server: Server) -> None:
    """Phones have no hover: the first tap starts the guard's clock (and is swallowed, like the
    second click of a swapped double-click), and a later tap approves."""
    from playwright.sync_api import expect, sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_context(has_touch=True, is_mobile=True).new_page()
            page.goto(server.authorize_url())
            page.wait_for_selector(APPROVE)
            page.wait_for_timeout(1000)  # past the dwell time, so only the touch is missing
            box = page.locator(APPROVE).bounding_box()
            assert box is not None
            x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
            page.touchscreen.tap(x, y)
            page.wait_for_timeout(100)
            assert page.url.startswith(f"{server.base}/authorize/confirm"), page.url
            expect(page.locator(APPROVE)).to_be_enabled(timeout=5000)
            page.touchscreen.tap(x, y)
            page.wait_for_url(f"{server.outside}/application/o/authorize/**", timeout=10_000)
        finally:
            browser.close()
