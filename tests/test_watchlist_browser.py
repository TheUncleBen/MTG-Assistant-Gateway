"""The card viewer's Price history in headless Chromium (static/cardview.js): a card not watched
says so and offers Watch this card; pressing it watches the card through the page's form token
and shows today's price in a table. Skipped without a Chromium build."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .test_cardview_browser import PNG
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.app.state.gateway.scan.names.set_names(["Sol Ring", *s.sf.catalog])
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_card_viewer_watches_a_card_and_shows_its_price(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 390, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/cards?q=Sol Ring", wait_until="networkidle")
        page.locator(".cardgrid .pic[data-card='Sol Ring']").first.click()
        page.wait_for_selector(".cardview.open", timeout=3000)
        page.wait_for_load_state("networkidle")
        btn = page.get_by_role("button", name="Price history")
        btn.click()
        page.wait_for_selector("#cardview-prices >> text=not on your watchlist", timeout=3000)
        assert btn.get_attribute("aria-expanded") == "true"
        page.get_by_role("button", name="Watch this card").click()
        page.wait_for_selector("#cardview-prices table", timeout=5000)
        text = page.locator("#cardview-prices").inner_text()
        assert "$" in text and "nonfoil" in text, text
        overflow = page.evaluate(
            "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow == 0
        assert errors == []
        browser.close()
    assert [w["name"] for w in server.app.state.gateway.prices.watched_cards("user-1")] == ["Sol Ring"]
