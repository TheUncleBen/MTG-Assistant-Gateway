"""Select on the Binder in a browser (0.7.46 audit QG-17, QOL-17): the tick boxes show on every
tile and row, Select ticks what is on screen, the bar appears with the count and fits at phone and
desktop widths, Condition… sets every ticked row, Remove goes at once and the notice's Undo brings
the rows back. Set MTG_BINDER_SHOTS to a folder to keep screenshots."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .test_overflow_sweep_browser import MEASURE
from .test_printing_picker_browser import Server, _chromium, _page

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_BINDER_SHOTS")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _shot(page, name: str) -> None:  # noqa: ANN001
    if SHOTS:
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=True)


@pytest.mark.parametrize("width,theme,view", [(390, "dark", "grid"), (1280, "light", "list")])
def test_select_ticks_the_rows_and_the_bar_changes_and_removes_them(
    server: Server, width: int, theme: str, view: str
) -> None:
    from playwright.sync_api import sync_playwright

    from .test_collection_page_browser import _seed

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server, 4)
    rows = server.ark.collections["alice"]
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme, touch=width < 900)
        page.goto(f"{server.base}/collection?view={view}", wait_until="networkidle")
        ticks = page.locator("#cards .chk input[name=sel]")
        assert ticks.count() == 4
        assert ticks.first.is_visible()
        bar = page.locator(".ui-bar[aria-label='Selected cards']")
        assert not bar.is_visible()
        _shot(page, f"binder-select-{view}-{width}-{theme}-before")
        # Select ticks every card on screen; the bar shows the count
        select = page.locator(".col-select")
        assert select.is_visible()
        select.click()
        bar.wait_for(state="visible", timeout=3000)
        assert bar.locator(".count").inner_text() == "4 selected"
        assert select.get_attribute("aria-pressed") == "true"
        assert page.evaluate(MEASURE) == [], (view, width)
        _shot(page, f"binder-select-{view}-{width}-{theme}-ticked")
        # one untick: the count follows and Select is no longer "all"
        ticks.first.click()
        assert bar.locator(".count").inner_text() == "3 selected"
        assert select.get_attribute("aria-pressed") == "false"
        # Condition… names the count and sets it on the ticked rows (one write each, read back)
        bar.locator("a[data-box-open='col-bulk-cond']").click()
        box = page.locator("#col-bulk-cond")
        page.wait_for_function("() => !document.getElementById('col-bulk-cond').hidden")
        assert box.locator("[data-bulk-count]").inner_text() == "The 3 selected cards"
        box.locator("select[name=condition]").select_option("LP")
        _shot(page, f"binder-select-{view}-{width}-{theme}-box")
        box.locator("button[value=condition]").click()
        page.wait_for_url("**/collection?*ok=bulk*", timeout=8000)
        page.wait_for_load_state("networkidle")
        notice = page.locator("#col-notes .notice.ok")
        assert notice.inner_text().strip() == "Condition set on 3 cards."
        assert sorted(r["condition"] for r in rows.values()) == [1, 2, 2, 2]
        # Remove goes at once; the notice offers Undo, which brings the rows back
        page.locator(".col-select").click()
        page.locator("#cards .chk input[name=sel]").first.click()
        page.locator(".ui-bar[aria-label='Selected cards'] button[value=remove]").click()
        page.wait_for_url("**/collection?*ok=bulk*", timeout=8000)
        page.wait_for_load_state("networkidle")
        notice = page.locator("#col-notes .notice.ok")
        assert notice.inner_text().strip().startswith("3 cards removed from your binder.")
        assert len(rows) == 1
        _shot(page, f"binder-select-{view}-{width}-{theme}-removed")
        notice.locator("button", has_text="Undo").click()
        page.wait_for_url("**/collection?*ok=bulk*", timeout=8000)
        page.wait_for_load_state("networkidle")
        assert (
            page.locator("#col-notes .notice.ok").inner_text().strip() == "3 cards are back in your binder."
        )
        assert len(rows) == 4 and page.locator("#cards > li[data-id]").count() == 4
        assert page.locator(".ui-bar[aria-label='Selected cards']").is_hidden()
        assert errors == []
        browser.close()
