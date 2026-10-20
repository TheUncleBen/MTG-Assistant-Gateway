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
    s = Server(tmp_path, watchlists_enabled=True)
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


def test_card_viewer_has_no_price_buttons_with_watchlists_off(tmp_path: Path) -> None:
    """MTG_WATCHLISTS_ENABLED off (the default): the viewer offers no Price history."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    server = Server(tmp_path)
    server.app.state.gateway.scan.names.set_names(["Sol Ring", *server.sf.catalog])
    server.start()
    try:
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
            assert page.get_by_role("button", name="Rulings").count() == 1
            assert page.get_by_role("button", name="Price history").count() == 0
            assert page.locator("#cardview-prices").count() == 0
            assert errors == []
            browser.close()
    finally:
        server.stop()


def _seed_watchlist(server: Server) -> None:
    """Two watched cards (one with 45 days of prices) and a deck with 40 days of values."""
    import json
    import math
    import time

    now = int(time.time())
    with server.db.tx() as c:
        for i, (wid, name, sfid, days) in enumerate(
            (("w_one", "Sol Ring", "s-one", 45), ("w_two", "Opt", "s-two", 12))
        ):
            c.execute(
                "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, scryfall_id, "
                "alert_pct, alert_above, alert_below, created_at) "
                "VALUES (?, 'user-1', ?, '', '', 'nonfoil', ?, ?, NULL, NULL, ?)",
                (wid, name, sfid, 10 if i == 0 else None, now),
            )
            for d in range(days):
                c.execute(
                    "INSERT INTO price_points (scryfall_id, day, usd) VALUES (?, date('now', ?), ?)",
                    (sfid, f"-{d} day", round(2 + math.sin(d / 3), 2)),
                )
        c.execute(
            "INSERT INTO watch_decks (sub, deck_id, deck_name, cards_json, refreshed_at, created_at) "
            "VALUES ('user-1', '42', 'Meren Graveyard Value', ?, ?, ?)",
            (json.dumps([{"qty": 100}]), now, now),
        )
        for d in range(40):
            c.execute(
                "INSERT INTO deck_values (sub, deck_id, day, usd, eur) "
                "VALUES ('user-1', '42', date('now', ?), ?, 1)",
                (f"-{d} day", 400 + d),
            )


@pytest.mark.parametrize("width", [344, 390, 673, 1280])
def test_watchlist_rows_open_in_place_and_draw_the_full_history(server: Server, width: int) -> None:
    """On the Watchlist, "Details" opens one row in place (the other closes, no page load), "Full history (45
    days)" draws the year's points from /watchlist/api/card and "30 days" brings the month back; a
    deck draws its own; nothing scrolls sideways at 100% or 200% text, in light and dark."""
    from playwright.sync_api import sync_playwright

    from .test_text_zoom_browser import TEXT_200

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    _seed_watchlist(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for scheme in ("light", "dark"):
            ctx = browser.new_context(viewport={"width": width, "height": 900}, color_scheme=scheme)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            ctx.route(re.compile(r"https://cards\.scryfall\.io/.*"), lambda route: route.abort())
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            page.goto(f"{server.base}/watchlist", wait_until="networkidle")
            page.evaluate("window.__same = true")
            sol = page.locator("#wr-w_one .more")
            assert sol.inner_text() == "Details" and sol.bounding_box()["height"] >= 36
            sol.click()
            assert page.locator("#wx-w_one").is_visible() and sol.inner_text() == "Close"
            assert sol.get_attribute("aria-expanded") == "true"
            page.locator("#wr-w_two .more").click()
            assert page.locator("#wx-w_two").is_visible() and not page.locator("#wx-w_one").is_visible()
            page.locator("#wr-w_one .more").click()
            chart = page.locator("#wx-w_one .pcl")
            short = chart.get_attribute("aria-label")
            with page.expect_response(lambda r: "/watchlist/api/card" in r.url):
                page.locator("#wx-w_one button[data-range=full]").click()
            page.wait_for_function(
                "() => document.querySelector('#wx-w_one h3').textContent === 'Price, full history'"
            )
            assert page.locator("#wx-w_one .wx-tbl tbody tr").count() == 45
            assert chart.count() == 0 or page.locator("#wx-w_one .pcl").get_attribute("aria-label") != short
            page.locator("#wx-w_one button[data-range='30']").click()
            assert page.locator("#wx-w_one .wx-tbl tbody tr").count() == 30
            # a deck's full history is in its row
            page.locator("#wr-d42 .more").click()
            page.locator("#wx-d42 button[data-range=full]").click()
            page.wait_for_function(
                "() => document.querySelectorAll('#wx-d42 .wx-tbl tbody tr').length === 40"
            )
            assert page.evaluate("window.__same === true")  # all in place
            for zoom in (None, TEXT_200):
                if zoom:
                    page.evaluate(zoom)
                    page.wait_for_timeout(100)
                out = page.evaluate(
                    """() => [document.documentElement.scrollWidth, innerWidth,
                      [...document.querySelectorAll('main *')].filter(e => e.getClientRects().length
                        && e.getBoundingClientRect().right > innerWidth + 1 && !e.closest('.tbl'))
                        .map(e => e.tagName + '.' + e.className).slice(0, 5)]"""
                )
                assert out[0] <= out[1] and not out[2], (scheme, zoom, out)
            assert errors == [], errors
            ctx.close()
        browser.close()
