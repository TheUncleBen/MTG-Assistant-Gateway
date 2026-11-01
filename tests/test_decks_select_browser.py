"""Select on My decks in a browser (0.7.46 audit QG-17, QOL-17): a tick box on every deck tile,
Select ticks what is on screen, the bar shows the count and fits at phone and desktop widths
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
        assert ticks.count() == 4 and ticks.first.is_visible()  # the sample deck and the three seeded
        bar = page.locator(".ui-bar[aria-label='Selected decks']")
        assert not bar.is_visible()
        assert page.evaluate(MEASURE) == [], (view, width)
        _shot(page, f"decks-select-{view}-{width}-{theme}-before")
        select = page.locator(".decks-select")
        assert select.is_visible()
        select.click()
        bar.wait_for(state="visible", timeout=3000)
        assert bar.locator(".count").inner_text() == "4 selected"
        assert select.get_attribute("aria-pressed") == "true"
        assert page.evaluate(MEASURE) == [], (view, width)
        _shot(page, f"decks-select-{view}-{width}-{theme}-ticked")
        left_out = int(ticks.first.get_attribute("value"))
        ticks.first.click()
        assert bar.locator(".count").inner_text() == "3 selected"
        # Move to folder…: the box names the count; Move sends the ticked decks, one write each
        bar.locator("a[data-box-open='decks-bulk-move']").click()
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
        assert errors == [], errors
        browser.close()
