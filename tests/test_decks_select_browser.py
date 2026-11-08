"""Select on My decks in a browser (0.7.46 audit QG-17, QOL-17; re-audit N03): Select opens the
select mode with nothing ticked (the tick boxes show only then), the bar shows the count and
Select all and fits at phone and desktop widths
(nothing overlaps a tile's link), Move to folder… moves every ticked deck and the notice says
how many went. Set MTG_FOLDER_SHOTS to a folder to keep screenshots."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .test_cardsearch_browser import _chromium, _page
from .test_editor_browser import _link
from .test_folder_view_browser import server  # noqa: F401  (the fixture: alice's folders and decks)
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_FOLDER_SHOTS")


def _shot(page, name: str) -> None:  # noqa: ANN001
    if SHOTS:
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


def _more(bar, box_id: str):  # noqa: ANN001
    """The bar's ⋯ menu opened, and its item that opens the box ``box_id``."""
    details = bar.locator("details.bar-more")
    if not details.evaluate("d => d.open"):
        details.locator("summary").click()
    return details.locator(f".menu a[data-box-open='{box_id}']")


@pytest.mark.parametrize("width,theme,view", [(390, "dark", "grid"), (1280, "light", "list")])
def test_select_ticks_the_decks_and_the_bar_moves_them(
    server: Server,  # noqa: F811
    width: int,
    theme: str,
    view: str,
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    ark = server.ark
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks?view={view}&q=a", wait_until="networkidle")  # every folder
        page.wait_for_selector("section.fv-area", timeout=10_000)
        ticks = page.locator("#decks-list .deck .chk input[name=deck_id]")
        assert ticks.count() == 4  # the sample deck and the three seeded
        assert not ticks.first.is_visible()  # the tick boxes come with the select mode
        bar = page.locator(".ui-bar[aria-label='Selected decks']")
        assert not bar.is_visible()
        assert page.evaluate(MEASURE) == [], (view, width)
        _shot(page, f"decks-select-{view}-{width}-{theme}-before")
        select = page.locator(".decks-select")
        assert select.is_visible()
        select.click()
        bar.wait_for(state="visible", timeout=3000)
        assert ticks.first.is_visible() and page.locator("#decks-list .chk input:checked").count() == 0
        assert bar.locator(".count").inner_text() == "0 selected"
        assert select.get_attribute("aria-pressed") == "true"
        assert page.evaluate(MEASURE) == [], (view, width)
        _shot(page, f"decks-select-{view}-{width}-{theme}-mode")
        _more(bar, "decks-bulk-move").click()
        page.locator(".toast", has_text="Tick a deck first.").wait_for(timeout=3000)
        assert not bar.locator("details.bar-more").evaluate("d => d.open")
        assert page.locator("#decks-bulk-move").get_attribute("hidden") is not None
        bar.locator(".decks-all").click()
        assert bar.locator(".count").inner_text() == "4 selected"
        assert page.evaluate(MEASURE) == [], (view, width)
        _shot(page, f"decks-select-{view}-{width}-{theme}-ticked")
        left_out = int(ticks.first.get_attribute("value"))
        ticks.first.click()
        assert bar.locator(".count").inner_text() == "3 selected"
        # Move to folder…: the box names the count; Move sends the ticked decks, one write each
        _more(bar, "decks-bulk-move").click()
        page.wait_for_function("() => !document.getElementById('decks-bulk-move').hidden")
        box = page.locator("#decks-bulk-move")
        assert box.locator("[data-bulk-count]").inner_text() == "The 3 selected decks"
        box.locator("select[name=folder_id]").select_option("504")
        _shot(page, f"decks-select-{view}-{width}-{theme}-box")
        box.locator("button[value=move]").click()
        page.wait_for_url("**/decks?*ok=bulk*", timeout=10_000)
        page.wait_for_load_state("networkidle")
        notice = page.locator(".decks-bulk-notice")
        assert notice.inner_text().strip() == "3 decks moved to “Archive”."
        for did in (42, 61, 62, 63):
            if did != left_out:
                assert ark.deck_folder.get(did) == 504, did
        _shot(page, f"decks-select-{view}-{width}-{theme}-moved")
        assert page.locator(".ui-bar[aria-label='Selected decks']").is_hidden()
        assert not page.locator("#decks-list .deck .chk input[name=deck_id]").first.is_visible()
        # Cancel leaves the mode: the ticks and the boxes go
        page.locator(".decks-select").click()
        page.locator(".ui-bar[aria-label='Selected decks']").wait_for(state="visible", timeout=3000)
        page.locator("#decks-list .deck .chk input[name=deck_id]").first.click()
        assert page.locator(".ui-bar[aria-label='Selected decks'] .count").inner_text() == "1 selected"
        page.locator(".ui-bar[aria-label='Selected decks'] .decks-clear").click()
        assert page.locator(".ui-bar[aria-label='Selected decks']").is_hidden()
        assert page.locator("#decks-list .chk input:checked").count() == 0
        assert not page.locator("#decks-list .deck .chk input[name=deck_id]").first.is_visible()
        assert errors == [], errors
        browser.close()


def test_the_bar_is_one_slim_row_on_a_phone(server: Server) -> None:  # noqa: F811
    """Final audit F01: the bar was two rows. Now one: × Cancel, the count, Select all and a ⋯
    menu with Move to folder… and Visibility…, every control 44 px."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 412, "light", exe)
        page.goto(f"{server.base}/decks?q=a", wait_until="networkidle")
        page.wait_for_selector("section.fv-area", timeout=10_000)
        page.locator(".decks-select").click()
        bar = page.locator(".ui-bar[aria-label='Selected decks']")
        bar.wait_for(state="visible", timeout=3000)
        page.locator("#decks-list .deck .chk input[name=deck_id]").first.click()
        assert bar.locator(".count").inner_text() == "1 selected"
        box = bar.bounding_box()
        assert box is not None and box["height"] <= 60, box
        parts = bar.locator(":scope > *")
        rects = [parts.nth(i).bounding_box() for i in range(parts.count())]
        assert len(rects) == 4 and all(r is not None for r in rects), rects
        assert max(r["y"] + r["height"] for r in rects) - min(r["y"] for r in rects) <= 48, rects
        x, count, select_all, more = rects
        assert x["x"] < count["x"] < select_all["x"] < more["x"], rects
        assert x["width"] >= 44 and x["height"] >= 44 and more["width"] >= 44 and select_all["height"] >= 44
        assert bar.locator("button.decks-clear").get_attribute("aria-label") == "Cancel"
        assert page.evaluate(MEASURE) == []
        _shot(page, "decks-select-slim-412")
        bar.locator("details.bar-more > summary").click()
        menu = bar.locator("details.bar-more .menu")
        menu.wait_for(state="visible", timeout=3000)
        assert menu.locator("a").all_inner_texts() == ["Move to folder…", "Visibility…"]
        mbox = menu.bounding_box()
        assert mbox["y"] + mbox["height"] <= more["y"] + 1, (mbox, more)  # above the bar
        assert page.evaluate(MEASURE) == []
        menu.locator("a[data-box-open='decks-bulk-vis']").click()
        page.wait_for_function("() => !document.getElementById('decks-bulk-vis').hidden")
        assert not bar.locator("details.bar-more").evaluate("d => d.open")
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.getElementById('decks-bulk-vis').hidden")
        bar.locator("button.decks-clear").click()
        assert bar.is_hidden()
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize("width", [412, 1280])
def test_the_tick_box_leaves_the_card_count_readable(server: Server, width: int) -> None:  # noqa: F811
    """Checklist v39 screenshots: the card count moved to the cover's top left (6faae26a, clear
    of a Home tile's ⋮), under the tick box Select puts in the same corner, so “34 cards” read
    “_cards”. In select mode the count sits beside the box, whole and unoverlapped."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        page.goto(f"{server.base}/decks?q=a&view=grid", wait_until="networkidle")
        page.wait_for_selector("section.fv-area", timeout=10_000)
        page.locator(".decks-select").click()
        tick = page.locator("#decks-list .deck .chk input[name=deck_id]").first
        tick.wait_for(state="visible", timeout=3000)
        pairs = page.evaluate(
            """() => [...document.querySelectorAll('#decks-list .deck')].map(d => {
                 const c = d.querySelector('.chk'), v = d.querySelector('.thumb .views');
                 if (!c || !v) return null;
                 const a = c.getBoundingClientRect(), b = v.getBoundingClientRect();
                 return {text: v.textContent.trim(), w: b.width,
                         overlap: a.left < b.right && b.left < a.right
                                  && a.top < b.bottom && b.top < a.bottom,
                         inside: b.left >= d.getBoundingClientRect().left - .5
                                 && b.right <= d.getBoundingClientRect().right + .5};
               }).filter(Boolean)"""
        )
        assert pairs, "no tile with a card count"
        assert all(not q["overlap"] and q["inside"] and q["w"] > 0 for q in pairs), pairs
        page.locator(".ui-bar[aria-label='Selected decks'] button.decks-clear").click()
        back = page.evaluate(
            "() => { const v = document.querySelector('#decks-list .deck .thumb .views');"
            " return v.getBoundingClientRect().left - v.closest('.thumb').getBoundingClientRect().left }"
        )
        assert back < 12, back  # out of select mode the count goes back to its corner
        assert errors == [], errors
        browser.close()
