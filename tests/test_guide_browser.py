"""The Guide in headless Chromium (static/guide.js), one page a topic: the contents page leads to
a topic, an old one-page address (/guide#topic) lands on the topic's page, the search box lists
the matching topics as you type with the count announced and Escape clearing it, the contents
drawer folds on a phone and sits open beside the text on a computer, "Back to top" appears after
scrolling, and nothing is wider than the window at 360 and 1366 px. Skipped without a Chromium
build."""

from __future__ import annotations

import secrets
import threading
import time
from pathlib import Path

import httpx
import pytest
import uvicorn

from mtg_gateway.app import create_app
from mtg_gateway.db import Database
from mtg_gateway.oidc import OIDCClient

from .conftest import CLIENT_ID, CLIENT_SECRET, IDP, make_settings
from .test_scan_browser import _chromium_path, _free_port, _serve

pytest.importorskip("playwright")


class Server:
    def __init__(self, tmp_path: Path):
        self.port = _free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        settings = make_settings(
            tmp_path, public_url=self.base, allowed_hosts=["127.0.0.1", f"127.0.0.1:{self.port}"]
        )
        self.db = Database(settings.db_path)
        oidc = OIDCClient(IDP, CLIENT_ID, CLIENT_SECRET, settings.callback_url, settings.oidc_scopes)
        self.app = create_app(settings, db=self.db, oidc=oidc)
        self.app.state.gateway.membership = None
        self.server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="warning")
        )
        self.thread = threading.Thread(target=_serve(self.server, self.port), daemon=True)

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

    def sign_in(self) -> str:
        sid = secrets.token_urlsafe(32)
        self.db.upsert_user(
            "user-1", email="alice@example.test", name="Alice", preferred_username="alice", groups=[]
        )
        self.db.create_browser_session(sid, "user-1", 3600)
        return sid


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


_EXE: dict[str, str | None] = {}


def _chromium() -> None:
    _EXE["path"] = _chromium_path()
    if _EXE["path"] == "missing":
        pytest.skip("no Chromium available for Playwright")


def _page(p, server: Server, sid: str, width: int):
    browser = p.chromium.launch(executable_path=_EXE["path"], args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 780})
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server.base}/guide", wait_until="networkidle")
    return browser, page, errors


def _overflow(page) -> int:
    return page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth")


def test_contents_topic_old_anchor_search_and_drawer(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        for width in (360, 1366):
            browser, page, errors = _page(p, server, sid, width)
            page.goto(f"{server.base}/guide", wait_until="networkidle")
            assert _overflow(page) == 0
            assert page.locator(".gcontents .gpart").count() == 4
            # a topic from the contents
            page.locator(".gtoc a[href='/guide/how-restore']").click()
            page.wait_for_url("**/guide/how-restore")
            assert page.locator("h1", has_text="Restore a snapshot").is_visible()
            assert _overflow(page) == 0
            drawer = page.locator("details.gnav")
            if width < 900:  # a phone: the contents fold under their button, the topic comes first
                assert drawer.locator("summary").is_visible() and not drawer.get_attribute("open")
                drawer.locator("summary").click()
                assert drawer.locator("a[aria-current='page']").is_visible()
            else:  # a computer: the contents sit open beside the text
                assert not drawer.locator("summary").is_visible()
                assert drawer.locator("a[aria-current='page']").is_visible()
            # the topics before and after
            page.locator(".gturn a[rel='next']").click()
            page.wait_for_url("**/guide/how-compare")
            # an old one-page address lands on the topic's page, a heading inside one at the heading
            page.goto(f"{server.base}/guide#how-approve", wait_until="networkidle")
            page.wait_for_url("**/guide/how-approve")
            page.goto(f"{server.base}/guide#how-export-deck", wait_until="networkidle")
            page.wait_for_url("**/guide/how-export#how-export-deck")
            # the search: the list under the box as you type, the count announced, Escape clears
            box = page.locator("#guide-q")
            box.fill("F10")
            results = page.locator("#guide-results")
            results.wait_for(state="visible", timeout=4000)
            status = page.locator("#guide-status").inner_text()
            assert status.startswith(("1 topic", "2 topics", "3 topics")) and status.endswith("match"), status
            first = results.locator("a").first
            assert first.locator(".t").inner_text() == "Keyboard shortcuts"
            assert first.locator("mark").first.inner_text() == "F10"
            assert _overflow(page) == 0
            box.press("Escape")
            assert results.is_hidden() and page.locator("#guide-status").inner_text() == ""
            # Enter on the box without scripts' list: the search page, with the same list
            box.fill("F10")
            box.press("Enter")
            page.wait_for_url("**/guide/search?q=F10")
            assert page.locator(".gresults a").first.locator(".t").inner_text() == "Keyboard shortcuts"
            # Back to top on a long topic
            page.goto(f"{server.base}/guide/ref-keys", wait_until="networkidle")
            assert not page.locator(".gtop").is_visible()
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(200)
            tall = page.evaluate("() => document.body.scrollHeight > 2 * window.innerHeight")
            assert page.locator(".gtop").is_visible() == tall
            browser.close()
            assert errors == []
