"""0.7.47 audit AO2: pull to refresh reaches every app page now, so a page that holds work a pull
would lose says so (window.mtgHolds, static/topbar.js): the editor while it has staged changes, the
deck page while a save is on its way. In headless Chromium with the app's user agent and a stub of
its bridge. Skipped without Chromium."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_deck_menu_browser import Server, _chromium, _page
from .test_deck_views_browser import _held, _open_menu

pytest.importorskip("playwright")

APP_UA = "Mozilla/5.0 (Linux; Android 16) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.7.47"
STUB = """window.MtgNative = { version: () => "0.7.47", pullAllowed: v => { window.__pull = v; } };"""
MAY = "() => window.mtgMayPull(document.querySelector('main') || document.body)"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _app_page(p, server: Server):
    sid = server.sign_in_and_link()
    browser, page, errors = _page(p, server, sid, 1280, user_agent=APP_UA)
    page.context.add_init_script(STUB)
    return browser, page, errors


def test_the_editor_holds_the_pull_while_changes_are_staged(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _app_page(p, server)
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        assert page.evaluate(MAY) is True
        page.locator("li.erow button.mini[title='One more']").first.click()  # staged, not saved
        assert page.evaluate(MAY) is False
        assert errors == []
        browser.close()


def test_the_deck_page_holds_the_pull_while_a_save_is_on_its_way(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _app_page(p, server)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        assert page.evaluate(MAY) is True
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        held = _held(server)
        try:
            menu = _open_menu(page, row)
            menu.locator(".qtyrow .step").nth(1).click()
            page.locator(".deck-toast .msg", has_text="Saving Cultivate").wait_for(timeout=5000)
            page.keyboard.press("Escape")
            page.evaluate("() => window.scrollTo(0, 0)")
            assert page.evaluate(MAY) is False
        finally:
            held.set()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=10_000)
        page.evaluate("() => window.scrollTo(0, 0)")
        page.wait_for_function(MAY, timeout=5000)
        assert errors == []
        browser.close()
