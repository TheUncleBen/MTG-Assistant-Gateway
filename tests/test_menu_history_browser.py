"""On a phone one history entry stands for "a menu is open" (static/feedback.js), so Back closes
the menu instead of leaving the page. Gate R4-1: opening a second menu straight from the first
left the second without an entry, and Back left the page. The entry has one owner now: the first
menu takes it, the last one closing gives it back, and a menu opened while that back() is still
landing takes it again. Real taps in a touch context at 390 px. Skipped without Chromium."""

from __future__ import annotations

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
    s.start()
    try:
        yield s
    finally:
        s.stop()


OPEN = "() => [...document.querySelectorAll('details.dd[open]')].map(d => d.querySelector(':scope > summary').getAttribute('aria-label') || d.querySelector(':scope > summary').innerText.trim())"  # noqa: E501
STATE = "() => ({ menu: !!(history.state && history.state.menu), open: document.querySelectorAll('details.dd[open]').length })"  # noqa: E501


def _two_menus(page):
    """The first two dropdown menus with a visible button on the page (the account menu and the
    tab bar's More at phone width), as locators of their summaries."""
    summaries = page.locator("details.dd > summary")
    found = []
    for i in range(summaries.count()):
        s = summaries.nth(i)
        if s.is_visible() and (s.bounding_box() or {}).get("width", 0) > 0:
            found.append(s)
        if len(found) == 2:
            break
    assert len(found) == 2, "the page needs two visible menus"
    return found


def _tap(page, locator):
    box = locator.bounding_box()
    page.touchscreen.tap(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
    page.wait_for_timeout(120)


def _open(server: Server):
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    p = sync_playwright().start()
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": 390, "height": 800}, has_touch=True, is_mobile=True)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server.base}/decks", wait_until="networkidle")
    return p, browser, page, errors


def test_a_menu_opened_straight_from_another_keeps_its_back_entry(server: Server) -> None:
    """Gate R4-1: open menu A, tap menu B's button; B opens with the history entry, and Back
    closes B and stays on the page."""
    p, browser, page, errors = _open(server)
    try:
        url = page.url
        a, b = _two_menus(page)
        _tap(page, a)
        assert page.evaluate(STATE) == {"menu": True, "open": 1}
        _tap(page, b)
        page.wait_for_timeout(200)  # A's back() lands and B takes the entry again
        assert page.evaluate(STATE) == {"menu": True, "open": 1}, page.evaluate(OPEN)
        page.go_back(wait_until="commit")
        page.wait_for_timeout(200)
        assert page.url == url, "Back left the page"
        assert page.evaluate(STATE) == {"menu": False, "open": 0}
        assert errors == []
    finally:
        browser.close()
        p.stop()


def test_closing_one_menu_and_opening_another_quickly_goes_back_once(server: Server) -> None:
    """Close A with its own button and open B before that back() has landed: B stays open, keeps
    the entry, and one Back closes it on the same page."""
    p, browser, page, errors = _open(server)
    try:
        url = page.url
        a, b = _two_menus(page)
        _tap(page, a)
        box_a, box_b = a.bounding_box(), b.bounding_box()
        page.touchscreen.tap(box_a["x"] + box_a["width"] / 2, box_a["y"] + box_a["height"] / 2)
        page.touchscreen.tap(box_b["x"] + box_b["width"] / 2, box_b["y"] + box_b["height"] / 2)
        page.wait_for_timeout(300)
        assert page.url == url
        assert page.evaluate(STATE) == {"menu": True, "open": 1}, page.evaluate(OPEN)
        page.go_back(wait_until="commit")
        page.wait_for_timeout(200)
        assert page.url == url, "Back left the page"
        assert page.evaluate(STATE) == {"menu": False, "open": 0}
        # one more Back now really leaves the page: the entry was given back exactly once
        assert errors == []
    finally:
        browser.close()
        p.stop()


def test_back_with_a_menu_open_closes_it_and_stays(server: Server) -> None:
    p, browser, page, errors = _open(server)
    try:
        url = page.url
        a, _b = _two_menus(page)
        _tap(page, a)
        page.go_back(wait_until="commit")
        page.wait_for_timeout(200)
        assert page.url == url and page.evaluate(STATE) == {"menu": False, "open": 0}
        # closing by Back gave nothing back twice: the page can still go forward to nothing
        assert errors == []
    finally:
        browser.close()
        p.stop()
