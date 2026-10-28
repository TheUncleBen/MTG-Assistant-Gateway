"""Speed in a real browser (0.7.46 audit WP1/WP2, owner decision C1), headless Chromium against
the gateway with the fake Archidekt made slow: a slow page draws its frame at once and is then
replaced by the finished page (static/feedback.js); the bell's count and the watched names are
asked once while moving between pages within a minute (static/bell.js, static/cardmenu.js); an
older deck page on Reload is shown at once and loaded again when it changed. Skipped without a
Chromium build."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
import pytest

from .test_editor_browser import _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _slow(server: Server, seconds: float) -> None:
    ark = server.ark

    async def handle(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(seconds)
        return ark.handle(request)

    client = server.app.state.gateway.decks.client
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))


def _browser(p, exe: str):  # noqa: ANN001, ANN202
    return p.chromium.launch(executable_path=exe, args=["--no-sandbox"])


def _until(page, expression: str, timeout: float) -> None:  # noqa: ANN001
    """Poll ``expression`` (the pages' CSP forbids wait_for_function's eval) across navigations."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        try:
            if page.evaluate(expression):
                return
        except Exception:  # noqa: BLE001, S110 - the page is between two documents
            pass
        time.sleep(0.05)
    raise AssertionError(f"not true within {timeout} s: {expression}")


@pytest.mark.parametrize("width", [390, 1280])
def test_a_slow_page_draws_its_frame_then_the_page(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    _slow(server, 5.0)
    with sync_playwright() as p:
        browser = _browser(p, exe)
        ctx = browser.new_context(viewport={"width": width, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        t0 = time.monotonic()
        page.goto(f"{server.base}/notifications", wait_until="domcontentloaded")
        first = time.monotonic() - t0
        assert first < 3.0, first  # the frame, long before Archidekt's 5 s answer (load allowed for)
        assert page.locator("[data-late] .late-note").inner_text() == "Reading this page from Archidekt…"
        assert page.locator("header.topbar").count() == 1
        _until(page, "document.readyState !== 'loading' && !document.querySelector('[data-late]')", 30)
        assert page.locator("main h1").first.inner_text().startswith("Notifications")
        assert errors == []
        browser.close()


def test_the_bell_and_watched_names_are_asked_once_across_pages(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    with sync_playwright() as p:
        browser = _browser(p, exe)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        asked: list[str] = []
        page.on(
            "request",
            lambda r: (
                asked.append(r.url)
                if ("/notifications/count" in r.url or "/watchlist/api/names" in r.url)
                else None
            ),
        )
        for path in ("/", "/decks", "/decks/42", "/collection"):
            page.goto(f"{server.base}{path}", wait_until="load")
            page.wait_for_timeout(600)  # the bell asks once the page is idle
        assert sum("/notifications/count" in u for u in asked) == 1, asked
        assert sum("/watchlist/api/names" in u for u in asked) == 1, asked
        browser.close()


def test_an_older_deck_page_on_reload_shows_at_once_and_loads_the_change(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    from mtg_gateway import decks as decks_mod

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    decks = server.app.state.gateway.decks
    with sync_playwright() as p:
        browser = _browser(p, exe)
        ctx = browser.new_context(viewport={"width": 390, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/decks/42", wait_until="load")
        decks._page_decks = {
            k: (t - decks_mod.PAGE_DECK_RELOAD - 1, d) for k, (t, d) in decks._page_decks.items()
        }
        server.ark.decks[42]["name"] = "Renamed on the site"
        _slow(server, 1.5)
        t0 = time.monotonic()
        page.reload(wait_until="domcontentloaded")
        assert time.monotonic() - t0 < 1.4  # the copy on show, not the slow read
        _until(page, "document.title.indexOf('Renamed on the site') === 0", 15)
        browser.close()
