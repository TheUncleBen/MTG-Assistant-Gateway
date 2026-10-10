"""Typed card-name suggestions in headless Chromium (static/suggest.js, R-131): the themed
listbox replaces the browser's datalist, appears from the gateway's own catalog without a
Scryfall call per keystroke, works from the keyboard, and the search form's buttons sit in one
row at one height. Skipped without a Chromium build."""

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
from mtg_gateway.scan.scryfall import ScryfallClient

from .conftest import CLIENT_ID, CLIENT_SECRET, IDP, make_settings
from .fake_archidekt import FakeArchidekt
from .fake_scryfall import FakeScryfall
from .test_scan_browser import _chromium_path, _free_port

pytest.importorskip("playwright")

SHELOBS = ["Shelob, Child of Ungoliant", "Shelob, Dread Weaver", "Shelob's Ambush"]


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
        client = ArchidektClient(settings.archidekt_base, "test", Pacer(0.0), http=self.ark.client())
        self.app = create_app(settings, db=self.db, oidc=oidc, archidekt=client)
        self.app.state.gateway.membership = None
        self.sf = FakeScryfall()
        self.sf.catalog = SHELOBS + [f"Filler Card {i}" for i in range(1200)]
        scan = self.app.state.gateway.scan
        scan.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=self.sf.client())
        scan.names.set_names(self.sf.catalog)  # loaded, as it is a minute after start-up
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


def _scryfall_calls(server: Server) -> int:
    return sum(1 for _m, u in server.sf.requests if "/cards/autocomplete" in u)


def test_commander_box_suggests_from_a_themed_listbox(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1366, "height": 900})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        searches: list[str] = []
        page.on("request", lambda r: searches.append(r.url) if "/scan/api/search" in r.url else None)
        page.goto(f"{server.base}/search", wait_until="networkidle")
        assert page.locator("datalist").count() == 0
        box = page.locator("#s-cmd")
        assert box.get_attribute("role") == "combobox"
        box.click()
        box.press_sequentially("Shelo", delay=30)
        listbox = page.locator("[role=listbox]:visible")
        listbox.wait_for(timeout=3000)
        options = page.locator("[role=option]")
        assert options.all_inner_texts() == SHELOBS
        # the match is marked, the list is the site's own (themed) element, no datalist
        assert page.locator("[role=option] mark").first.inner_text() == "Shelo"
        # keyboard: down twice picks the second name; Enter fills the box without submitting
        box.press("ArrowDown")
        box.press("ArrowDown")
        assert page.locator("[role=option][aria-selected=true]").inner_text() == SHELOBS[1]
        box.press("Enter")
        assert box.input_value() == SHELOBS[1]
        assert page.url.endswith("/search")
        assert page.locator("[role=listbox]:visible").count() == 0
        # typing more narrows locally: a complete answer (under the limit) needs no new request
        n = len(searches)
        box.fill("")
        box.press_sequentially("Shelob's", delay=30)
        page.wait_for_timeout(400)
        assert page.locator("[role=option]").all_inner_texts() == ["Shelob's Ambush"]
        assert len(searches) <= n + 1, searches
        # nothing was asked of Scryfall for any of it
        assert _scryfall_calls(server) == 0
        assert errors == []
        # the form's buttons: one row, one height, inside the panel
        rects = page.evaluate(
            "() => [...document.querySelectorAll('#searchform .form-actions > *')].map(b => {"
            " const r = b.getBoundingClientRect();"
            " return [Math.round(r.top), Math.round(r.height), Math.round(r.right)]; })"
        )
        # Search decks, Clear and Search cards
        assert len(rects) == 3 and len({r[0] for r in rects}) == 1 and len({r[1] for r in rects}) == 1, rects
        panel_right = page.evaluate(
            "() => document.querySelector('.searchbar').getBoundingClientRect().right"
        )
        assert max(r[2] for r in rects) <= panel_right
        browser.close()


def test_no_match_and_escape(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 390, "height": 800}, is_mobile=True, has_touch=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/", wait_until="networkidle")
        box = page.locator("#home-c")
        box.click()
        box.press_sequentially("zzqx", delay=20)
        none = page.locator(".suggest-list li.none")
        none.wait_for(timeout=3000)
        assert "No card" in none.inner_text()
        box.press("Escape")
        assert page.locator("[role=listbox]:visible").count() == 0
        # the list never widens the page
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 390
        # a failed answer (signed out, busy, Scryfall down) is not "no such card": the list closes
        page.route("**/scan/api/search*", lambda route: route.fulfill(status=503, body="{}"))
        box.fill("")
        box.press_sequentially("shelob d", delay=20)
        page.wait_for_timeout(600)
        assert page.locator(".suggest-list li.none").count() == 0
        assert page.locator("[role=listbox]:visible").count() == 0
        # the client folds and ranks like scan/names.py: joiners are spaces, a later word start wins
        assert page.evaluate('() => window.MtgSuggest.fold("Lim-D\u00fbl\'s Vault")') == "lim dul s vault"
        ranked = page.evaluate(
            "() => window.MtgSuggest.narrow(['Zap', 'Sunscape Apprentice', 'Apprentice Wizard'], 'ap')"
        )
        assert ranked == ["Apprentice Wizard", "Sunscape Apprentice", "Zap"]
        browser.close()


def test_no_page_renders_a_datalist(server: Server) -> None:
    sid = server.sign_in()
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/account").text).group(1)
    h.post(
        "/account",
        data={
            "csrf": csrf,
            "action": "link",
            "archidekt_login": "alice",
            "archidekt_password": "pw-alice",
            "accept_risk": "1",
        },
    )
    for path in ("/", "/search", "/decks/42", "/decks/42/edit", "/decks/42/compare", "/collection"):
        text = h.get(path).text
        assert "<datalist" not in text, path
        assert "data-suggest=" in text, path


def test_sign_out_acts_on_the_first_click_and_says_so(server: Server) -> None:
    """T-015: the Sign out button shows 'Signing out…' at once and locks itself, so a second press
    does nothing (feedback.js). The submit itself is swallowed here so the page stays inspectable;
    the server side of sign-out is covered by tests/test_web_hygiene.py."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1366, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        # runs after feedback.js's document listener (window, bubble phase): count and stop submits
        ctx.add_init_script(
            "window.__submits = 0; window.addEventListener('submit', e => { window.__submits++;"
            " e.preventDefault(); });"
        )
        page = ctx.new_page()
        page.goto(f"{server.base}/account", wait_until="networkidle")
        button = page.locator(".form-actions form[action='/logout'] button:not([name])")
        button.click()
        page.wait_for_function(  # the lock comes a tick after the form is handed to the browser
            "() => { const b = document.querySelector(\".form-actions form[action='/logout'] button\");"
            " return b.getAttribute('aria-busy') === 'true' && b.textContent.trim() === 'Signing out…'"
            " && b.disabled; }",
            timeout=3000,
        )
        button.click(force=True)  # a second press while it works
        assert page.evaluate("() => window.__submits") == 1
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 1366
        browser.close()


def test_desktop_layout_switch_gives_a_phone_the_computer_layout(server: Server) -> None:
    """T-046: with the Desktop layout cookie a phone gets a 1100 px viewport (as a browser's Desktop
    site switch would), so the width queries show the top bar instead of the bottom tab bar."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        phone = {"viewport": {"width": 390, "height": 800}, "is_mobile": True, "has_touch": True}
        ctx = browser.new_context(**phone)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        assert page.locator(".tabbar").is_visible() and not page.locator(".topbar nav a").first.is_visible()
        ctx.add_cookies([{"name": "mtg_layout", "value": "desktop", "url": server.base}])
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        assert page.evaluate("() => document.documentElement.clientWidth") == 1100
        assert not page.locator(".tabbar").is_visible() and page.locator(".topbar nav a").first.is_visible()
        # a one-pixel rounding of the 1100 -> 390 scaling is not an overflow (no element crosses the edge)
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 1101
        browser.close()
