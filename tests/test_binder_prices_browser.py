"""Binder prices in a browser (0.7.46, decision C5): "Work it out now" reads the Binder and
prices it; the Value widget, the row prices and the price sort fit at 390, 840 and 1280 px, light
and dark; the Add box's Language field and "Use … next time" box are 44 px tall; the card viewer's
Price history draws the owned printing's chart; "Pin card" in a card menu pins it on Archidekt."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_overflow_sweep_browser import MEASURE
from .test_printing_picker_browser import Server, _chromium, _page

pytest.importorskip("playwright")

SIZES = [(390, "dark"), (840, "light"), (1280, "dark"), (1280, "light")]
SHOTS = os.environ.get("MTG_BINDER_SHOTS")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _seed(server: Server) -> None:
    """Three entries Scryfall prices (Sol Ring x2, Aesi, Command Tower) and one it does not."""
    ark = server.ark
    picks = [("Sol Ring", "frc", "21", 2), ("Aesi, Tyrant of Gyre Strait", "dsc", "210", 1)]
    for rid, (name, set_code, number, qty) in enumerate(picks, 1):
        sf = next(
            c
            for c in server.sf.cards
            if c["name"] == name and c["set"] == set_code and c["collector_number"] == number
        )
        card = {
            "id": 90000 + rid,
            "uid": sf["id"],
            "collectorNumber": number,
            "edition": {"editioncode": set_code, "editionname": sf.get("set_name", "")},
            "oracleCard": {"name": name},
        }
        ark.collections.setdefault("alice", {})[rid] = ark._collection_row(rid, card, {"quantity": qty})
    ark.collections["alice"][3] = ark._collection_row(3, ark.printing(9007), {"quantity": 1})
    ark.next_coll_id = max(ark.next_coll_id, 3)


def _shot(page, name: str, width: int, theme: str) -> None:
    if SHOTS:
        page.screenshot(path=str(Path(SHOTS) / f"{name}-{width}-{theme}.png"), full_page=True)


@pytest.mark.parametrize("width,theme", SIZES)
def test_binder_value_prices_and_add_box_fit(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme, touch=width < 900)
        page.goto(f"{server.base}/collection", wait_until="networkidle")
        now_btn = page.locator(".wdg button", has_text="Work it out now")
        b = now_btn.bounding_box()
        assert b is not None and b["height"] >= (44 if width < 900 else 36)
        with page.expect_navigation(wait_until="networkidle"):
            now_btn.click()
        for _ in range(100):
            if "$14.79" in page.content():
                break
            page.wait_for_timeout(100)
            page.reload(wait_until="networkidle")
        widgets = page.locator(".ui-widgets").inner_text()
        assert "$14.79" in widgets and "1 entry has no price" in widgets, widgets
        for view in ("grid", "list"):
            page.goto(f"{server.base}/collection?sort=price&view={view}", wait_until="networkidle")
            assert page.evaluate(MEASURE) == [], (view, width)
            assert page.evaluate(CONTRAST, ".wdg .det, .wdg .big, #cards .pr, #cards .prc") == []
            names = page.locator("#cards .name").all_inner_texts()
            assert names[:2] == ["Aesi, Tyrant of Gyre Strait", "Sol Ring"], names
            assert "2 × $1.28" in page.locator("#cards").inner_text()
            _shot(page, f"binder-price-{view}", width, theme)
        # the Add box: Language and "Use … next time"
        page.goto(f"{server.base}/collection?add=1", wait_until="networkidle")
        assert page.locator("#addlang").is_visible()
        check = page.locator("#col-addform label.check").bounding_box()
        assert check and check["height"] >= 44
        assert page.evaluate(MEASURE) == []
        _shot(page, "binder-addbox", width, theme)
        # the card viewer: the owned printing's price history
        page.goto(f"{server.base}/collection?view=list&sort=price", wait_until="networkidle")
        page.locator("#cards .thumbbtn").nth(1).click()
        page.locator(".cardview button", has_text="Price history").click()
        owned = page.locator(".cardview .pv-owned")
        owned.wait_for(timeout=5000)
        assert "This printing, in your binder" in owned.inner_text()
        ob = owned.bounding_box()
        assert ob is not None and ob["width"] > 200 and ob["height"] < 400, ob
        assert "The chart shows once there are two days of prices." in owned.inner_text()  # one day so far
        assert "$1.28" in owned.inner_text()
        assert page.evaluate(MEASURE) == []
        _shot(page, "binder-viewer", width, theme)
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_pin_card_from_the_card_menu(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, touch=width < 900)
        page.goto(f"{server.base}/cards?q=sol", wait_until="networkidle")
        pic = page.locator(".cc .pic").first
        if width < 900:
            pic.dispatch_event("contextmenu")
        else:
            pic.click(button="right")
        menu = page.locator(".ctxmenu.cs-menu")
        menu.wait_for(timeout=3000)
        item = menu.locator("[role=menuitem]", has_text="Pin card")
        ib = item.bounding_box()
        assert ib is not None and ib["height"] >= (44 if width < 900 else 32)
        item.click()
        toast = page.locator(".toast")
        toast.wait_for(timeout=5000)
        page.wait_for_function("() => /Pinned/.test(document.querySelector('.toast').textContent)")
        assert "on Archidekt." in toast.inner_text()
        pins = server.ark.account.pins.get("alice") or []
        assert len(pins) == 1
        _shot(page, "pin-toast", width, "light")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(390, "dark"), (840, "light"), (1280, "light")])
def test_the_value_being_worked_out_follows_the_read_by_itself(
    server: Server, width: int, theme: str
) -> None:
    """While the whole Binder is read, the Value widget shows a spinning progress line with no time
    in it, and the finished value replaces it with nothing pressed and no reload."""
    import threading

    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme, touch=width < 900)
        page.goto(f"{server.base}/collection", wait_until="networkidle")
        gate = threading.Event()
        server.ark.hold_whole_binder_reads = gate
        try:
            with page.expect_navigation(wait_until="domcontentloaded"):
                page.locator(".wdg button", has_text="Work it out now").click()
            line = page.locator("[data-value-reading]")
            line.wait_for(timeout=5000)
            assert "updates by itself" in line.inner_text() and "minute" not in line.inner_text()
            assert page.locator("[data-value-reading] .ui-spin").count() == 1
            assert page.evaluate(MEASURE) == []
            _shot(page, "binder-value-reading", width, theme)
            loads = page.evaluate("performance.getEntriesByType('navigation').length")
            gate.set()
            for _ in range(100):
                if "$14.79" in page.locator(".ui-widgets").inner_text():
                    break
                page.wait_for_timeout(100)
            assert "$14.79" in page.locator(".ui-widgets").inner_text()
            assert page.locator("[data-value-reading]").count() == 0
            assert page.evaluate("performance.getEntriesByType('navigation').length") == loads
            assert page.evaluate(CONTRAST, ".wdg .det, .wdg .big") == []
        finally:
            gate.set()
        assert errors == []
        browser.close()
