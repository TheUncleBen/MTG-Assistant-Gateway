"""Card size S / M / L (0.7.29) in headless Chromium: M is today's size; on
a phone the deck page shows 3, 2 or 1 cards across; on a wide screen My decks shows 6, 5 or 4
decks across; the switch next to "View as" applies at once and follows the account; the printing
picker has the same switch; the list views keep it and size their row pictures (R-175). At 360, 720 and
1280 px in both themes the switch fits, reads at 4.5:1 and is as tall as the other controls. Screenshots
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
# the size switch's save (static/prefs.js): the test waits for its answer, not a fixed time
SAVED = "**/api/v1/prefs"
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
        for size, least in (("s", 160), ("m", 220), ("l", 300)):
            _size(server, size)
            page.goto(f"{server.base}/decks?view=grid", wait_until="networkidle")
            widths = page.evaluate(
                "s => getComputedStyle(document.querySelector(s)).gridTemplateColumns.split(' ')"
                ".map(parseFloat)",
                "ul.decklist.grid",
            )
            assert all(w >= least for w in widths) and len(widths) >= 1600 // (least + 25) - 1, (size, widths)
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
        with page.expect_response(SAVED) as saved:
            page.locator(".toolbar [data-card-size-set=s]").click()
        assert saved.value.ok  # the account has it once the save answers: no fixed wait
        assert page.get_attribute("html", "data-card-size") == "s" and page.url == url  # no reload
        assert page.get_attribute(".toolbar [data-card-size-set=s]", "aria-pressed") == "true"
        assert page.evaluate(COLS, ".deckview.stacks") > before
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


# The first save the page sends reaches the network 1.2 s late, as on a slow connection.
LATE_FIRST_SAVE = """
(() => {
  const real = window.fetch; let first = true;
  window.fetch = function (url, opts) {
    if (String(url).indexOf('/api/v1/prefs') !== -1 && opts && opts.method === 'POST' && first) {
      first = false;
      return new Promise(r => setTimeout(r, 1200)).then(() => real.call(this, url, opts));
    }
    return real.apply(this, arguments);
  };
})();
"""


def test_quick_size_changes_keep_the_last_one(server: Server) -> None:
    """CI on a648672: two quick changes are saved in the order they were made, so the account keeps
    the last one even when the first save is slow (before, M could be stored before L)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _size(server, None)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.add_init_script(LATE_FIRST_SAVE)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        answered = []
        page.on("response", lambda r: answered.append(r) if "/api/v1/prefs" in r.url else None)
        page.locator(".toolbar [data-card-size-set=l]").click()
        page.locator(".toolbar [data-card-size-set=s]").click()
        assert page.get_attribute("html", "data-card-size") == "s"
        for _ in range(100):  # both saves answered (the first waits 1.2 s on purpose)
            if len(answered) >= 2:
                break
            page.wait_for_timeout(50)
        assert len(answered) == 2 and all(r.ok for r in answered)
        assert server.db.get_pref("user-1", "card.size") == "s"
        browser.close()
    assert errors == []


def test_a_save_with_an_unreadable_answer_does_not_stop_the_next_ones(server: Server) -> None:
    """The first save's answer is cut off (not JSON). The saves after it still go,
    in order, and the account keeps the last choice."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _size(server, None)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        sent: list[str] = []

        def cut_first(route) -> None:
            if route.request.method != "POST":
                route.continue_()
                return
            sent.append(route.request.post_data or "")
            if len(sent) == 1:
                route.fulfill(status=200, content_type="application/json", body='{"ok": tr')
            else:
                route.continue_()

        page.route("**/api/v1/prefs", cut_first)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        for size in ("l", "m", "s"):
            page.locator(f".toolbar [data-card-size-set={size}]").click()
        for _ in range(100):
            if len(sent) >= 3 and server.db.get_pref("user-1", "card.size") == "s":
                break
            page.wait_for_timeout(50)
        assert len(sent) == 3, sent
        assert server.db.get_pref("user-1", "card.size") == "s"
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
            # its save answers before the next click, so the next wait can't catch this one's answer
            with page.expect_response(SAVED) as saved:
                page.locator(".printpick [data-card-size-set=l]").click()
            assert saved.value.ok
            assert page.evaluate(COLS, ".printpick .ppgrid") < before
            assert page.get_attribute(".printpick [data-card-size-set=l]", "aria-pressed") == "true"
            assert page.get_attribute(".printpick [data-card-size-set=m]", "aria-pressed") == "false"
            assert page.evaluate(MEASURE) == []
            _shot(page, f"picker-large-{width}.png")
            with page.expect_response(SAVED) as saved:
                page.locator(".printpick [data-card-size-set=m]").click()
            assert saved.value.ok
            assert server.db.get_pref("user-1", "card.size") == "m"
            browser.close()
            assert errors == []


@pytest.mark.parametrize("width,theme", SIZES)
def test_collection_my_decks_and_card_search_show_the_switch_in_every_view(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _size(server, "l")
    ark = server.ark  # an empty collection shows no results bar: give it one card
    ark.collections.setdefault("alice", {})[5001] = ark._collection_row(
        5001, ark.printing(9003), {"quantity": 1}
    )
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme)
        for path, bar in (
            ("/decks?view=grid", ".rbar"),
            ("/collection?view=grid", ".spanel .rbar"),  # the switch stays in the bar on phones
            ("/cards?q=sol&view=grid", ".rbar.cards"),
        ):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            sheet = width < 600 and bar.startswith(".rbar")  # phones: the switch is in the Filters sheet
            if sheet:
                page.locator(".sp-open").click()
                page.locator("#sp-filters.open").wait_for(timeout=3000)
                bar = "#sp-filters .sheet-size"
            assert page.locator(f"{bar} .sizeseg").is_visible(), path
            assert page.get_attribute(f"{bar} [data-card-size-set=l]", "aria-pressed") == "true"
            assert (path, page.evaluate(MEASURE)) == (path, [])
            assert page.evaluate(CONTRAST, f"{bar} .sizeseg button, {bar} .cardsize .lbl") == []
            _shot(page, f"{path[1:].split('?')[0]}-{width}-{theme}.png")
            if sheet:
                page.locator(".sp-show").click()
        # R-175 (2026-10-21): the switch stays in the list views too, and sizes the row pictures
        if width >= 600:  # on phones the switch waits in the Filters sheet, as above
            page.goto(f"{server.base}/decks?view=list", wait_until="networkidle")
            assert page.locator(".rbar .sizeseg").is_visible()
            page.goto(f"{server.base}/cards?q=sol&view=grid", wait_until="networkidle")
            page.locator(".rbar.cards .segc label", has_text="List").click()
            page.wait_for_function("() => document.querySelector('.cardlist')")
            assert page.locator(".rbar.cards .cardsize").is_visible()
            large = page.evaluate(
                "() => document.querySelector('.cardlist .pic').getBoundingClientRect().width"
            )
            with page.expect_response(SAVED) as saved:
                page.locator(".rbar.cards [data-card-size-set=s]").click()
            assert saved.value.ok
            small = page.evaluate(
                "() => document.querySelector('.cardlist .pic').getBoundingClientRect().width"
            )
            assert small < large, (small, large)
        browser.close()
    assert errors == []


# Each stack name at least as wide as its text (not cut, not a sliver).
STACK_NAMES = """
() => [...document.querySelectorAll('.deckview.stacks .stackhead .title')]
  .filter(t => t.scrollWidth > t.clientWidth + 1 || t.clientWidth < 40)
  .map(t => t.textContent + ' ' + t.clientWidth + '/' + t.scrollWidth)
"""

# The first letter of each picture-less tile's name, outside the quantity corner's triangle.
UNDER_CORNER = """
() => [...document.querySelectorAll('.deckview .c')].filter(c => c.querySelector('.ph .nm')).map(c => {
  const nm = c.querySelector('.ph .nm'), q = c.querySelector('.qty').getBoundingClientRect();
  const r = document.createRange();
  r.setStart(nm.firstChild, 0); r.setEnd(nm.firstChild, 1);
  const a = r.getBoundingClientRect();
  // the triangle is x + y < 38 from the tile's corner; the letter's top left must be outside it
  return (a.left - q.left) + (a.top - q.top) < q.width ? nm.textContent : null;
}).filter(Boolean)
"""


@pytest.mark.parametrize("width", [344, 360, 673, 1280])
def test_small_cards_keep_stack_names_and_card_names_readable(server: Server, width: int) -> None:
    """Review round 1, findings 3 and 8: at S the stack names show whole on a phone and a Fold,
    and a tile without a picture starts its name below the quantity corner."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _size(server, "s")
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        page.goto(f"{server.base}/decks/42?view=stacks", wait_until="networkidle")
        assert page.locator(".deckview.stacks .stackhead .title").count() >= 3
        assert page.evaluate(STACK_NAMES) == []
        assert page.evaluate(MEASURE) == []
        for view in ("stacks", "grid", "scroll"):
            page.goto(f"{server.base}/decks/42?view={view}", wait_until="networkidle")
            assert page.locator(".deckview .c .ph .nm").count() > 0, view
            assert page.evaluate(UNDER_CORNER) == [], view
        _shot(page, f"small-names-{width}.png")
        browser.close()
    assert errors == []


# Each deck tile's ⋮ against its picture: the gap from the picture's bottom (the colour bar's
# bottom) to the ⋮'s top, and from the ⋮'s bottom to the info area's.
KEBAB = """
() => [...document.querySelectorAll('ul.decklist.grid li.deck')].map(li => {
  const k = li.querySelector(':scope > .kebab').getBoundingClientRect();
  const t = li.querySelector('.thumb').getBoundingClientRect();
  const i = li.querySelector('.info').getBoundingClientRect();
  return {gap: k.top - t.bottom, below: i.bottom - k.bottom};
})
"""


@pytest.mark.parametrize("width", [375, 1280])
def test_the_deck_tiles_menu_button_sits_below_the_colour_bar_at_every_size(
    server: Server, width: int
) -> None:
    """Ben, 2026-10-28: the ⋮ overlapped the colour bar: its place was worked out for the M picture
    alone. At S, M and L it sits under the picture, in the name's area."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width)
        for size in ("s", "m", "l"):
            _size(server, size)
            page.goto(f"{server.base}/decks?view=grid", wait_until="networkidle")
            tiles = page.evaluate(KEBAB)
            assert tiles, size
            for tile in tiles:
                assert tile["gap"] >= 0, (size, tile)
                assert tile["below"] >= 0, (size, tile)
        browser.close()
    assert errors == []
