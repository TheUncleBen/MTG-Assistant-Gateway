"""Every card name drawn as a button (``button.cardlink[data-card]``) opens the card when it is
clicked (Ben, 2026-10-28: the report page's "Played without their effect" names did nothing): the
card viewer on a page that has it (static/cardview.js), else Card search, from static/menus.js,
which every signed-in page loads. Skipped without a Chromium build."""

from __future__ import annotations

import re
import time
from pathlib import Path

import httpx
import pytest

from .test_history_page import store_report
from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")

CARD_BUTTON = re.compile(r"<button\b[^>]*\bdata-card='[^']*'[^>]*>")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_each_page_with_card_name_buttons_loads_what_opens_them(server: Server) -> None:
    """Every page that draws a card name as a button loads the click's handler (menus.js) and
    the card viewer, and each such button is a plain button that names its card."""
    sid = server.sign_in_and_link()
    store_report(server.db, "user-1", "rep_links", taken_at=int(time.time()) - 200)
    server.ark.private.discard(43)
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=20)
    seen = 0
    for path in ("/history/reports/rep_links", "/decks/42/compare?with=43", "/decks/42", "/reports"):
        page = h.get(path).text
        buttons = [b for b in CARD_BUTTON.findall(page) if "cardlink" in b]
        if not buttons:
            continue
        seen += 1
        assert "/static/menus.js" in page and "/static/cardview.js" in page, path
        for b in buttons:
            assert "type='button'" in b and re.search(r"data-card='[^']+'", b), (path, b)
    assert seen >= 2


def test_a_card_name_on_the_report_page_opens_the_card_viewer(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    store_report(server.db, "user-1", "rep_links", taken_at=int(time.time()) - 200)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width in (390, 1280):
            ctx = browser.new_context(viewport={"width": width, "height": 900})
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            page.goto(f"{server.base}/history/reports/rep_links", wait_until="networkidle")
            link = page.locator("button.cardlink[data-card]:visible").first
            name = link.get_attribute("data-card")
            assert name
            link.click()
            viewer = page.locator(".cardview.open")
            viewer.wait_for(timeout=3000)
            assert name in viewer.inner_text()
            assert page.url.endswith("/history/reports/rep_links")  # the page stays
            page.keyboard.press("Escape")
            page.wait_for_timeout(150)
            assert page.locator(".cardview.open").count() == 0
            # the card menu's Open card shows it too
            link.click(button="right")
            page.locator(".ctxmenu[role=menu] [role=menuitem]", has_text="Open").first.click()
            page.locator(".cardview.open").wait_for(timeout=3000)
            assert name in page.locator(".cardview.open").inner_text()
            assert errors == []
            ctx.close()
        browser.close()
