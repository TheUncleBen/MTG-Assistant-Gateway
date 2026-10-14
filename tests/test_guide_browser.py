"""The Guide page in headless Chromium (static/guide.js): the contents rail marks the section in
view as the reader scrolls, the search box filters the sections and the rail, announces the
count and lists links to the matching sections (Enter goes to the first), Escape clears it, the
drawer folds on a phone, "Back to top" appears after scrolling, and nothing is wider than the
window at 360 and 1366 px. Skipped without a Chromium build."""

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
from .test_scan_browser import _chromium_path, _free_port

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


def _wait_current(page, target: str) -> None:
    page.wait_for_function(f"() => document.querySelector(\".gnav a[href='{target}'][aria-current='true']\")")


def _current(page) -> str | None:
    return page.evaluate(
        "() => { const a = document.querySelector(\".gnav a[aria-current='true']\");"
        " return a ? a.getAttribute('href') : null; }"
    )


def test_rail_follows_the_reader_search_filters_and_escape_clears(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        assert _overflow(page) == 0
        # the rail is open on a wide screen and the first section is current before any scroll
        assert page.locator("details.gnav[open]").count() == 1
        assert page.locator("details.gnav > summary").is_visible() is False
        assert _current(page) == "#tut-start"
        assert page.locator(".gtop").is_visible() is False
        # jump to a how-to section from the rail: its entry becomes current and its part lights up
        page.locator(".gnav a[href='#how-restore']").click()
        _wait_current(page, "#how-restore")
        assert page.url.endswith("#how-restore")
        # scrolling on by hand moves the mark to the section under the reader's eye
        page.locator("#exp-fresh").evaluate("el => el.scrollIntoView({block: 'start'})")
        _wait_current(page, "#exp-fresh")
        assert page.locator(".gnav a.part.in").get_attribute("href") == "#explanation"
        page.locator(".gnav a[href='#how-restore']").click()
        _wait_current(page, "#how-restore")
        assert page.locator(".gnav a.part.in").get_attribute("href") == "#howto"
        assert page.locator(".gnav a[aria-current='true']").count() == 1
        assert page.locator(".gtop").is_visible() is True
        # the search box is there (the script unhid it) and filters sections, parts and the rail
        box = page.locator("#guide-q")
        assert box.is_visible() and page.locator("label[for='guide-q']").inner_text() == "Search the guide"
        box.fill("F10")
        page.wait_for_function("() => document.getElementById('guide-status').textContent.includes('match')")
        status = page.locator("#guide-status").inner_text()
        assert status.startswith(("2 of", "3 of")) and "F10" in status, status
        assert page.locator("#ref-keys").is_visible() and page.locator("#how-add-cards").is_visible()
        assert page.locator("#exp-playtest").is_hidden() and page.locator("#tutorials").is_hidden()
        assert page.locator(".gnav a[href='#exp-playtest']").is_hidden()
        assert page.locator(".gnav a[href='#tutorials']").is_hidden()
        assert page.locator(".gnav a[href='#ref-keys']").is_visible()
        assert page.locator("#guide-status").get_attribute("role") == "status"
        # under the box, a link to each matching section with its title and the words around the
        # first match, in the guide's order
        results = page.locator("#guide-results")
        assert results.is_visible()
        # typing did not scroll the matches away to follow the rail's current entry
        first_top = results.locator("a").first.evaluate("el => el.getBoundingClientRect().top")
        assert 0 < first_top < 500, first_top
        hrefs = results.locator("a").evaluate_all("els => els.map(a => a.getAttribute('href'))")
        shown = page.locator(".gsec:visible").evaluate_all("els => els.map(s => '#' + s.id)")
        assert hrefs == shown and "#ref-keys" in hrefs and "#how-add-cards" in hrefs, hrefs
        keys = results.locator("a[href='#ref-keys']")
        assert keys.locator(".t").inner_text() == "Keyboard shortcuts"
        assert keys.locator("mark").first.inner_text() == "F10"
        assert _overflow(page) == 0
        # Down moves into the list; Enter in the box goes to the first match
        box.press("ArrowDown")
        assert page.evaluate("() => document.activeElement.getAttribute('href')") == hrefs[0]
        box.focus()
        box.press("Enter")
        page.wait_for_function(f"() => location.hash === '{hrefs[0]}'")
        _wait_current(page, hrefs[0])
        # a click on another match lands on that section
        page.evaluate("() => window.scrollTo(0, 0)")
        keys.click()
        page.wait_for_function("() => location.hash === '#ref-keys'")
        _wait_current(page, "#ref-keys")
        # (with only a few sections left the page may end before the heading reaches the top)
        top = page.locator("#ref-keys").evaluate("el => el.getBoundingClientRect().top")
        assert 0 <= top < 450, top
        # nothing matches: the status says so and the empty-state line shows
        box.fill("zzqxv")
        page.wait_for_function("() => document.getElementById('guide-status').textContent.startsWith('No')")
        assert page.locator(".nomatch").is_visible()
        assert page.locator(".gsec:visible").count() == 0
        assert page.locator("#guide-results").is_hidden()
        assert page.locator("#guide-results li").count() == 0
        # Escape clears: every section and rail entry is back, the status is empty
        box.press("Escape")
        page.wait_for_function("() => document.getElementById('guide-q').value === ''")
        assert page.locator("#guide-status").inner_text() == ""
        assert page.locator(".nomatch").is_hidden()
        assert page.locator("#guide-results").is_hidden()
        assert page.locator("#exp-playtest").is_visible()
        assert page.locator(".gnav a[href='#tutorials']").is_visible()
        total = page.locator(".gsec").count()
        assert page.locator(".gsec:visible").count() == total >= 25
        # Back to top
        page.locator("#exp-privacy").evaluate("el => el.scrollIntoView({block: 'start'})")
        page.wait_for_function("() => window.scrollY > 600")
        page.locator(".gtop").click()
        page.wait_for_function("() => window.scrollY < 120")  # #top sits under the page heading
        assert page.url.endswith("#top")
        assert _overflow(page) == 0
        assert errors == []
        browser.close()


def test_phone_drawer_folds_and_nothing_overflows_at_360(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360)
        assert _overflow(page) == 0
        drawer = page.locator("details.gnav")
        assert drawer.get_attribute("open") is None  # folded by the script on a phone
        summary = page.locator("details.gnav > summary")
        assert summary.is_visible() and "Contents" in summary.inner_text()
        summary.click()
        assert drawer.get_attribute("open") is not None
        assert _overflow(page) == 0
        page.locator(".gnav a[href='#how-scan']").click()
        page.wait_for_function("() => !document.querySelector('details.gnav').open")
        _wait_current(page, "#how-scan")
        # the reference tables wrap inside their scroll box; the page itself never scrolls sideways
        page.locator("#ref-assistant").evaluate("el => el.scrollIntoView({block: 'start'})")
        page.wait_for_timeout(100)
        assert _overflow(page) == 0
        page.evaluate("() => window.scrollTo(0, 0)")
        page.locator("#guide-q").fill("snapshot")
        page.wait_for_function("() => document.getElementById('guide-status').textContent.includes('match')")
        assert page.locator("#exp-approvals").is_visible() and page.locator("#how-layout").is_hidden()
        assert _overflow(page) == 0
        # with the contents drawer folded, the matches are still listed right under the box as
        # links, each with a marked snippet; a tap goes to that section and leaves the drawer shut
        assert drawer.get_attribute("open") is None
        hit = page.locator("#guide-results a[href='#exp-approvals']")
        assert hit.is_visible()
        assert hit.locator("mark").first.inner_text().lower() == "snapshot"
        assert _overflow(page) == 0
        hit.click()
        page.wait_for_function("() => location.hash === '#exp-approvals'")
        _wait_current(page, "#exp-approvals")
        top = page.locator("#exp-approvals").evaluate("el => el.getBoundingClientRect().top")
        assert 0 <= top < 200, top
        assert drawer.get_attribute("open") is None
        # with the drawer open, a tap on a match folds it again
        page.evaluate("() => window.scrollTo(0, 0)")
        summary.click()
        assert drawer.get_attribute("open") is not None
        page.locator("#guide-results a").first.click()
        page.wait_for_function("() => !document.querySelector('details.gnav').open")
        assert errors == []
        browser.close()
