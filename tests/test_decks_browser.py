"""Render first, in headless Chromium: a cold /decks and home go out with a placeholder while the
deck list is still being read, and decks.js swaps the real list in from /api/decks/mine without a
page error (T-037). Skipped without Chromium."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from .test_editor_browser import PNG, _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    svc = s.app.state.gateway.decks
    svc.deck_list_wait = 0.05  # the page waits this long for a cold list...
    real_list = svc.client.list_decks

    async def slow_list(*args, **kw):  # ...and Archidekt's queue takes longer
        await asyncio.sleep(0.4)
        return await real_list(*args, **kw)

    svc.client.list_decks = slow_list
    s.start()
    try:
        yield s
    finally:
        s.stop()


DECKS_API = re.compile(r".*/api/decks/mine\?.*")


def _goto_held(page, url: str, skeleton: str) -> None:
    """Open a page whose deck list loads after it, holding that read until the placeholder has
    been seen, so a fast answer can never swap the placeholder out before the test looks."""
    held: list = []
    page.route(DECKS_API, lambda route: held.append(route))
    page.goto(url, wait_until="commit")
    page.wait_for_selector(skeleton, state="attached")
    for _ in range(200):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the page never asked for the deck list"
    for route in held:
        route.continue_()
    page.unroute(DECKS_API)


def test_cold_deck_pages_fill_their_placeholders(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    svc = server.app.state.gateway.decks
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1366, "height": 900})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # the shell first: the placeholder list and a blank total...
        _goto_held(page, f"{server.base}/decks", "ul.decklist.skeleton")
        assert page.locator("#decks-total").inner_text().startswith("Total decks:")
        # ...then the list itself, as the server renders it, and the toolbar filled in
        page.wait_for_selector("ul.decklist li.deck", timeout=10_000)
        assert page.locator("ul.decklist.skeleton").count() == 0
        assert page.locator("[aria-busy=true]").count() == 0
        assert "Sample Commander Deck" in page.locator("ul.decklist").inner_text()
        assert page.locator("#decks-total").inner_text() == "Total decks: 1"
        assert page.locator("#folder-field").count() == 0  # opening folders replaced the dropdown (F3)
        assert page.locator(".listbar a[href='/folders']").inner_text().strip() == "Manage folders"
        # home, cold again
        svc.deck_lists.drop("user-1")
        _goto_held(page, f"{server.base}/", ".recent.skeleton")
        page.wait_for_selector(".recent a", timeout=10_000)
        assert "All decks (1)" in page.locator("[data-decks-src]").inner_text()
        assert page.locator(".skeleton").count() == 0
        assert errors == [], errors
        browser.close()


def test_a_deck_list_that_cannot_be_read_says_so_with_try_again(server: Server) -> None:
    """Q64, on a cold /decks: the placeholder becomes the notice (no "No decks yet", no total), and
    Try again loads the page again, which reads Archidekt again."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    svc, ark = server.app.state.gateway.decks, server.ark
    real_row = ark._listing_row
    ark._listing_row = lambda d: {**real_row(d), "owner": None}
    svc.deck_lists.drop("user-1")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 390, "height": 844})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.set_default_timeout(10_000)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        _goto_held(page, f"{server.base}/decks", "ul.decklist.skeleton")
        note = page.wait_for_selector(".decks-unreadable[role=alert]")
        assert note.inner_text().startswith("Your deck list couldn't be read from Archidekt.")
        assert page.locator("ul.decklist").count() == 0 and "No decks yet" not in page.content()
        assert not page.locator("#decks-total").is_visible()
        again = page.get_by_role("link", name="Try again")
        box = again.bounding_box()
        assert box is not None and box["height"] >= 44, box
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        ark._listing_row = real_row  # Archidekt reads normally again
        with page.expect_navigation():
            again.click()
        page.wait_for_selector("ul.decklist li.deck")
        assert page.locator(".decks-unreadable").count() == 0
        assert page.locator("#decks-total").inner_text() == "Total decks: 1"
        assert errors == [], errors
        browser.close()


def test_home_my_decks_panel_says_the_list_could_not_be_read(server: Server) -> None:
    """Q64 on a cold home page: the My decks panel's placeholder becomes the /decks notice with
    Try again (never "No decks yet"), at phone width without sideways scroll, and Try again loads
    the page again, which reads Archidekt again."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    svc, ark = server.app.state.gateway.decks, server.ark
    real_row = ark._listing_row
    ark._listing_row = lambda d: {**real_row(d), "owner": None}
    svc.deck_lists.drop("user-1")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 344, "height": 882})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        page.set_default_timeout(10_000)
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        # the panel's own read is held until the placeholder has been seen, so a fast answer can
        # never swap it out first (review N9: a busy run missed it)
        held: list = []
        page.route(re.compile(r".*/api/decks/mine\?shape=recent$"), lambda route: held.append(route))
        page.goto(f"{server.base}/", wait_until="commit")
        page.wait_for_selector(".recent.skeleton", state="attached")
        for _ in range(200):
            if held:
                break
            page.wait_for_timeout(50)
        assert held, "the panel never asked for the deck list"
        held.pop().continue_()
        page.unroute(re.compile(r".*/api/decks/mine\?shape=recent$"))
        note = page.wait_for_selector(".home .decks-unreadable[role=alert]")
        assert note.inner_text().startswith("Your deck list couldn't be read from Archidekt.")
        assert "No decks yet" not in page.content() and page.locator(".skeleton").count() == 0
        again = page.get_by_role("link", name="Try again")
        box = again.bounding_box()
        assert box is not None and box["height"] >= 44, box
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        ark._listing_row = real_row  # Archidekt reads normally again
        with page.expect_navigation():
            again.click()
        page.wait_for_selector(".recent a", timeout=10_000)
        assert page.locator(".decks-unreadable").count() == 0
        assert "All decks (1)" in page.content()
        assert errors == [], errors
        browser.close()
