"""Card size S / M / L (0.7.29, Q22, card Q135 item 7) in headless Chromium: M is today's size; on
a phone the deck page shows 3, 2 or 1 cards across; on a wide screen My decks shows 6, 5 or 4
decks across; the switch next to "View as" applies at once and follows the account; the printing
picker has the same switch; card search shows it with the Grid view only. At 360, 720 and 1280 px
in both themes the switch fits, reads at 4.5:1 and is as tall as the other controls. Screenshots
go to MTG_SIZE_SHOTS (a folder) when set. Skipped without a Chromium build."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import SIZES
from .test_overflow_sweep_browser import MEASURE
from .test_printing_picker_browser import Server, _chromium, _open_picker, _page

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_SIZE_SHOTS", "")
COLS = "s => getComputedStyle(document.querySelector(s)).gridTemplateColumns.split(' ').length"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / name), full_page=False)


def _size(server: Server, size: str | None) -> None:
    if size is None:
        server.db.delete_pref("user-1", "card.size")
    else:
        server.db.set_pref("user-1", "card.size", size)


def test_deck_page_columns_on_a_phone_and_my_decks_on_a_wide_screen(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360)
        for size, across in (("s", 3), (None, 2), ("m", 2), ("l", 1)):
            _size(server, size)
            page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
            assert page.evaluate(COLS, ".deckview.grid .cards") == across, size
            page.goto(f"{server.base}/decks/42?view=stacks", wait_until="networkidle")
            assert page.evaluate(COLS, ".deckview.stacks") == across, size
            assert (size, page.evaluate(MEASURE)) == (size, [])
        browser.close()
        browser, page, errors2 = _page(p, server, sid, 1600)
        for size, across in (("s", 6), ("m", 5), ("l", 4)):
            _size(server, size)
            page.goto(f"{server.base}/decks?view=grid", wait_until="networkidle")
            assert page.evaluate(COLS, "ul.decklist.grid") == across, size
        browser.close()
    assert errors == [] and errors2 == []


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_switch_applies_at_once_and_follows_the_account(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _size(server, None)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme)
        page.goto(f"{server.base}/decks/42?view=stacks", wait_until="networkidle")
        sw = page.locator(".toolbar .sizeseg")
        assert sw.is_visible()
        assert page.get_attribute("html", "data-card-size") == "m"
        assert page.get_attribute(".toolbar [data-card-size-set=m]", "aria-pressed") == "true"
        url = page.url
        before = page.evaluate(COLS, ".deckview.stacks")
        page.locator(".toolbar [data-card-size-set=s]").click()
        assert page.get_attribute("html", "data-card-size") == "s" and page.url == url  # no reload
        assert page.get_attribute(".toolbar [data-card-size-set=s]", "aria-pressed") == "true"
        assert page.evaluate(COLS, ".deckview.stacks") > before
        page.wait_for_function("() => true")
        page.wait_for_timeout(300)
        assert server.db.get_pref("user-1", "card.size") == "s"
        # as tall as the selects beside it (39 px with its border; 46 px on a touch screen)
        height = page.evaluate(
            "() => document.querySelector('.toolbar .sizeseg').getBoundingClientRect().height"
        )
        select = page.evaluate(
            "() => document.querySelector('.toolbar .msel-btn').getBoundingClientRect().height"
        )
        assert height >= 38.5 and abs(height - select) <= 1 or height >= 44, (height, select)
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, ".sizeseg button, .field.cardsize .lbl") == []
        _shot(page, f"deck-stacks-small-{width}-{theme}.png")
        # the text view shows no switch: nothing there is a picture
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        assert page.locator(".toolbar .sizeseg").count() == 0
        # another device opens with the account's size
        other = browser.new_context(viewport={"width": width, "height": 900})
        other.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page2 = other.new_page()
        page2.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        assert page2.get_attribute("html", "data-card-size") == "s"
        other.close()
        browser.close()
    assert errors == []


def test_the_printing_picker_has_the_same_switch(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _size(server, None)
    with sync_playwright() as p:
        for width in (360, 1280):
            browser, page, errors = _page(p, server, sid, width)
            page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
            _open_picker(page, page.locator(".deckview .row[data-card='Sol Ring'][data-zone='main']"))
            sw = page.locator(".printpick .sizeseg")
            assert sw.is_visible()
            before = page.evaluate(COLS, ".printpick .ppgrid")
            page.locator(".printpick [data-card-size-set=l]").click()
            assert page.evaluate(COLS, ".printpick .ppgrid") < before
            assert page.get_attribute(".printpick [data-card-size-set=l]", "aria-pressed") == "true"
            assert page.get_attribute(".printpick [data-card-size-set=m]", "aria-pressed") == "false"
            assert page.evaluate(MEASURE) == []
            _shot(page, f"picker-large-{width}.png")
            page.locator(".printpick [data-card-size-set=m]").click()
            page.wait_for_timeout(300)
            assert server.db.get_pref("user-1", "card.size") == "m"
            browser.close()
            assert errors == []


@pytest.mark.parametrize("width,theme", SIZES)
def test_collection_my_decks_and_card_search_show_the_switch_with_the_grid_only(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _size(server, "l")
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme)
        for path, bar in (
            ("/decks?view=grid", ".listbar"),
            ("/collection?view=grid", ".collbar"),
            ("/cards?q=sol&view=grid", ".cs-head"),
        ):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            assert page.locator(f"{bar} .sizeseg").is_visible(), path
            assert page.get_attribute(f"{bar} [data-card-size-set=l]", "aria-pressed") == "true"
            assert (path, page.evaluate(MEASURE)) == (path, [])
            assert page.evaluate(CONTRAST, ".sizeseg button, .cardsize .lbl") == []
            _shot(page, f"{path[1:].split('?')[0]}-{width}-{theme}.png")
        page.goto(f"{server.base}/decks?view=list", wait_until="networkidle")
        assert page.locator(".listbar .sizeseg").count() == 0
        page.goto(f"{server.base}/collection?view=list", wait_until="networkidle")
        assert page.locator(".collbar .sizeseg").count() == 0
        page.goto(f"{server.base}/cards?q=sol&view=grid", wait_until="networkidle")
        page.locator(".cs-head .seg label", has_text="List").click()
        page.wait_for_function("() => !document.querySelector('.cs-head .cardsize').offsetParent")
        browser.close()
    assert errors == []
