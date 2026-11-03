"""Select on the Binder in a browser (0.7.46 audit QG-17, QOL-17; re-audit N03): Select opens
the select mode with nothing ticked (the tick boxes show only then), the bar appears with the
count and Select all and fits at phone and desktop widths, Condition… sets every ticked row,
Remove goes at once and the notice's Undo brings the rows back. Set MTG_BINDER_SHOTS to a folder
to keep screenshots."""

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


def _more(bar, box_id: str):  # noqa: ANN001
    """The bar's ⋯ menu opened, and its item that opens the box ``box_id``."""
    details = bar.locator("details.bar-more")
    if not details.evaluate("d => d.open"):
        details.locator("summary").click()
    return details.locator(f".menu a[data-box-open='{box_id}']")


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
        assert not ticks.first.is_visible()  # the tick boxes come with the select mode
        bar = page.locator(".ui-bar[aria-label='Selected cards']")
        assert not bar.is_visible()
        _shot(page, f"binder-select-{view}-{width}-{theme}-before")
        # Select opens the mode with nothing ticked: the boxes show, the bar says 0 selected
        select = page.locator(".col-select")
        assert select.is_visible()
        select.click()
        bar.wait_for(state="visible", timeout=3000)
        assert ticks.first.is_visible() and page.locator("#cards .chk input:checked").count() == 0
        assert bar.locator(".count").inner_text() == "0 selected"
        assert select.get_attribute("aria-pressed") == "true"
        assert page.evaluate(MEASURE) == [], (view, width)
        _shot(page, f"binder-select-{view}-{width}-{theme}-mode")
        # an action with nothing ticked asks for a tick (and the ⋯ menu closes); Select all ticks
        # every card on screen
        _more(bar, "col-bulk-cond").click()
        page.locator(".toast", has_text="Tick a card first.").wait_for(timeout=3000)
        assert page.locator("#col-bulk-cond").get_attribute("hidden") is not None
        assert not bar.locator("details.bar-more").evaluate("d => d.open")
        bar.locator(".col-all").click()
        assert bar.locator(".count").inner_text() == "4 selected"
        assert page.evaluate(MEASURE) == [], (view, width)
        _shot(page, f"binder-select-{view}-{width}-{theme}-ticked")
        # one untick: the count follows
        ticks.first.click()
        assert bar.locator(".count").inner_text() == "3 selected"
        # Condition… names the count and sets it on the ticked rows (one write each, read back)
        _more(bar, "col-bulk-cond").click()
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
        page.locator(".ui-bar[aria-label='Selected cards'] .col-all").click()
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
        assert not page.locator("#cards .chk input[name=sel]").first.is_visible()
        # Cancel leaves the mode: the ticks and the boxes go
        page.locator(".col-select").click()
        page.locator(".ui-bar[aria-label='Selected cards']").wait_for(state="visible", timeout=3000)
        page.locator("#cards .chk input[name=sel]").first.click()
        assert page.locator(".ui-bar[aria-label='Selected cards'] .count").inner_text() == "1 selected"
        page.locator(".ui-bar[aria-label='Selected cards'] .col-clear").click()
        assert page.locator(".ui-bar[aria-label='Selected cards']").is_hidden()
        assert page.locator("#cards .chk input:checked").count() == 0
        assert not page.locator("#cards .chk input[name=sel]").first.is_visible()
        assert page.locator(".col-select").get_attribute("aria-pressed") == "false"
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width,theme", [(412, "light"), (360, "dark")])
def test_the_bar_is_one_slim_row_on_a_phone(server: Server, width: int, theme: str) -> None:
    """Final audit F01: the bar was three rows (a fifth of the screen). Now one row: × Cancel at
    the left, the count, Select all, a ⋯ menu with Condition…, Finish… and Tags… (it opens
    upward, above the bar) and Remove, every control 44 px."""
    from playwright.sync_api import sync_playwright

    from .test_collection_page_browser import _seed

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server, 12)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme, touch=True)
        page.goto(f"{server.base}/collection", wait_until="networkidle")
        page.locator(".col-select").click()
        bar = page.locator(".ui-bar[aria-label='Selected cards']")
        bar.wait_for(state="visible", timeout=3000)
        page.locator("#cards .chk input[name=sel]").first.click()
        assert bar.locator(".count").inner_text() == "1 selected"
        box = bar.bounding_box()
        assert box is not None and box["height"] <= 60, box  # three rows were 150 px and more
        parts = bar.locator(":scope > *")
        rects = [parts.nth(i).bounding_box() for i in range(parts.count())]
        assert all(r is not None for r in rects) and len(rects) == 5, rects
        assert max(r["y"] + r["height"] for r in rects) - min(r["y"] for r in rects) <= 48, rects  # one row
        x, count, select_all, more, remove = rects
        assert x["x"] < count["x"] < select_all["x"] < more["x"] < remove["x"], rects
        assert x["width"] >= 44 and more["width"] >= 44 and remove["width"] >= 44, rects
        assert select_all["height"] >= 44, rects
        cancel = bar.locator("button.col-clear")
        assert cancel.get_attribute("aria-label") == "Cancel"
        assert bar.locator("button[value=remove]").get_attribute("aria-label") == "Remove"
        summary = bar.locator("details.bar-more > summary")
        assert summary.get_attribute("aria-label") == "More actions"
        assert page.evaluate(MEASURE) == [], width
        _shot(page, f"binder-select-slim-{width}-{theme}")
        # the ⋯ menu: the three boxes, 44 px each, above the bar; a pick closes it and opens the box
        summary.click()
        menu = bar.locator("details.bar-more .menu")
        menu.wait_for(state="visible", timeout=3000)
        items = menu.locator("a")
        assert items.all_inner_texts() == ["Condition…", "Finish…", "Tags…"]
        for i in range(3):
            assert items.nth(i).bounding_box()["height"] >= 44, i
        mbox = menu.bounding_box()
        assert mbox["y"] + mbox["height"] <= more["y"] + 1, (mbox, more)
        assert page.evaluate(MEASURE) == [], width
        _shot(page, f"binder-select-slim-{width}-{theme}-menu")
        items.nth(2).click()
        page.wait_for_function("() => !document.getElementById('col-bulk-tags').hidden")
        assert not bar.locator("details.bar-more").evaluate("d => d.open")
        # the box stays open once the menu's own history entry has gone back (the menu gives
        # its entry back before the box takes one); the phone's Back then closes the box, not
        # the page
        page.wait_for_timeout(700)
        assert not page.locator("#col-bulk-tags").evaluate("b => b.hidden")
        page.go_back()
        page.wait_for_function("() => document.getElementById('col-bulk-tags').hidden")
        assert page.url.endswith("/collection")
        # × leaves the mode
        cancel.click()
        assert bar.is_hidden() and page.locator("#cards .chk input:checked").count() == 0
        assert errors == []
        browser.close()
