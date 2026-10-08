"""The deck page's social buttons in headless Chromium: on someone else's deck, Like asks for a
confirmation first and then records the vote on Archidekt under the member's own linked session.

The buttons live in the deck banner, outside the card list, so they must work whatever the deck
view is and whether or not the deck is the member's own (deck.js once returned early on other
people's decks, which is exactly where Like and Follow matter). Skipped without a Chromium build.
"""

from __future__ import annotations

import re
import secrets
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from mtg_gateway.app import create_app
from mtg_gateway.archidekt import ArchidektClient, Pacer
from mtg_gateway.db import Database
from mtg_gateway.oidc import OIDCClient

from .conftest import CLIENT_ID, CLIENT_SECRET, IDP, make_settings
from .fake_archidekt import FakeArchidekt
from .test_scan_browser import _chromium_path, _free_port


class Server:
    def __init__(self, tmp_path: Path):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        settings = make_settings(
            tmp_path,
            public_url=self.base,
            allowed_hosts=["127.0.0.1", f"127.0.0.1:{self.port}"],
            archidekt_base="https://ark.test/api",
        )
        self.db = Database(settings.db_path)
        oidc = OIDCClient(IDP, CLIENT_ID, CLIENT_SECRET, settings.callback_url, settings.oidc_scopes)
        self.ark = FakeArchidekt()
        self.ark.private.discard(43)  # amy's deck is public: someone else's deck to like
        client = ArchidektClient(settings.archidekt_base, "test", Pacer(0.0), http=self.ark.client())
        self.app = create_app(settings, db=self.db, oidc=oidc, archidekt=client)
        self.app.state.gateway.membership = None
        self.server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def start(self) -> None:
        self.thread.start()
        for _ in range(100):
            try:
                if httpx.get(f"{self.base}/healthz", timeout=1).status_code == 200:
                    return
            except httpx.HTTPError:
                time.sleep(0.1)
        raise RuntimeError("gateway did not start")

    def stop(self) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=10)

    def sign_in_and_link(self) -> str:
        sid = secrets.token_urlsafe(32)
        self.db.upsert_user(
            "user-1", email="alice@example.test", name="Alice", preferred_username="alice", groups=[]
        )
        self.db.create_browser_session(sid, "user-1", 3600)
        h = httpx.Client(base_url=self.base, cookies={"mtg_session": sid})
        csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/account").text).group(1)
        r = h.post(
            "/account",
            data={
                "csrf": csrf,
                "action": "link",
                "archidekt_login": "alice",
                "archidekt_password": "pw-alice",
            },
        )
        assert r.status_code == 303, r.text
        return sid


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_like_on_someone_elses_deck_confirms_then_votes(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/decks/43", wait_until="networkidle")
        like = page.locator("[data-social=vote]")
        assert like.get_attribute("data-state") == "0"
        like.click()
        chip = page.locator(".banner .social .confirm")
        chip.wait_for(timeout=5000)
        assert "Like this deck on Archidekt?" in chip.inner_text()
        assert server.ark.votes == {}  # nothing sent before the confirmation
        chip.locator("button").last.click()  # "No" puts the button back, nothing sent
        page.wait_for_timeout(100)
        assert page.locator(".banner .social .confirm").count() == 0 and server.ark.votes == {}
        like.click()
        page.locator(".banner .social .confirm button").first.click()
        page.wait_for_function("() => document.querySelector('[data-social=vote]').dataset.state === '1'")
        assert server.ark.votes == {("alice", 300043): 1}
        assert like.inner_text().strip().endswith("Liked") and "1" in like.inner_text()
        # Follow the owner: the button learns the current state from Archidekt, then asks too.
        follow = page.locator("[data-social=follow]")
        page.wait_for_function(
            "() => document.querySelector('[data-social=follow]').dataset.state !== 'unknown'"
        )
        follow.click()
        page.locator(".banner .social .confirm button").first.click()
        page.wait_for_function("() => document.querySelector('[data-social=follow]').dataset.state === '1'")
        assert server.ark.follows == {"alice": {78}}
        assert errors == []
        browser.close()


def test_back_closes_the_card_viewer_instead_of_leaving_the_deck(server: Server) -> None:
    """Opening a card pushes a history entry, so the Android Back button (which the app turns
    into history.back() while the page can go back) and the browser's Back close the viewer and
    stay on the deck page. Closing from the page goes back over that entry, so a later Back leaves
    the deck page as it would have before the card was opened."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        depth = page.evaluate("() => history.length")
        page.locator("[data-card]").first.click()
        viewer = page.locator(".cardview.open")
        viewer.wait_for(timeout=5000)
        assert page.evaluate("() => history.length") == depth + 1
        page.go_back()
        page.wait_for_function("() => !document.querySelector('.cardview.open')")
        assert page.url == f"{server.base}/decks/42"  # still on the deck
        # Close from the page: the entry is used up, so Back now leaves the deck page.
        page.locator("[data-card]").first.click()
        viewer.wait_for(timeout=5000)
        page.locator(".cardview .close").click()
        page.wait_for_function("() => !document.querySelector('.cardview.open')")
        assert page.url == f"{server.base}/decks/42"
        page.go_back()
        page.wait_for_url(f"{server.base}/decks")
        assert errors == []
        browser.close()
