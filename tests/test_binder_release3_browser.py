"""Binder tags and Clear binder in a browser (parity release 3, 0.7.46): the Tag drop-down filters
on change, the Details box ticks the row's own tags and saves the ticked ones in place, its boxes
are 44 px tall, and nothing sticks out at phone and desktop widths, light and dark."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_overflow_sweep_browser import MEASURE
from .test_printing_picker_browser import Server, _chromium, _page

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _seed(server: Server) -> dict[str, int]:
    ark = server.ark
    tags = {}
    for name in ("Trade", "Keep"):
        ark.next_collection_tag_id += 1
        tid = ark.next_collection_tag_id
        ark.collection_tag_lists.setdefault("alice", {})[tid] = {"id": tid, "name": name, "color": "#336699"}
        tags[name] = tid
    for i, pk in enumerate((9003, 5011, 5007)):
        ark.next_coll_id += 1
        body = {"quantity": 1, "tags": [tags["Trade"]] if i == 0 else []}
        row = ark._collection_row(ark.next_coll_id, ark.printing(pk), body)
        ark.collections.setdefault("alice", {})[row["id"]] = row
    return tags


@pytest.mark.parametrize("width,theme", [(360, "light"), (412, "dark"), (1280, "light")])
def test_the_tag_filter_and_the_details_tags_fit(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme, touch=width < 900)
        page.goto(f"{server.base}/collection?view=list", wait_until="networkidle")
        assert page.evaluate(MEASURE) == [], width
        if width < 600:
            # on a phone the tag filter lives in the Filters sheet, with the size and Clear filters
            page.locator(".spanel .sp-open").click()
            page.locator("#sp-filters.open").wait_for()
            assert page.locator("#sp-filters #col-tag").is_visible()
            page.locator("#sp-filters .sp-sheet-head .close").click()
            page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
        else:
            assert page.locator("#col-tag").is_visible()
        # Clear binder… lives in the head's More menu (test_polish3_0746_browser has the menu)
        page.locator(".ui-head .acts details.colmore > summary").click()
        clear = page.locator("details.colmore button[value=clear]")
        assert clear.is_visible() and clear.bounding_box()["height"] >= 44
        assert errors == []
        browser.close()


def test_the_details_box_ticks_and_saves_tags_and_the_filter_applies_on_change(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    tags = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 412, touch=True)
        page.goto(f"{server.base}/collection?view=grid", wait_until="networkidle")
        rows = page.locator("#cards > li[data-id]")
        tagged = rows.last  # the oldest: tagged Trade
        rid = int(tagged.get_attribute("data-id"))
        tagged.locator(".kebab").click()
        menu = page.locator(".ctxmenu")
        menu.wait_for(timeout=3000)
        menu.locator("[role=menuitem]", has_text="Details…").click()
        box = page.locator("#col-details")
        page.wait_for_function("() => !document.getElementById('col-details').hidden")
        trade, keep = box.locator("input[data-tag=Trade]"), box.locator("input[data-tag=Keep]")
        assert trade.is_checked() and not keep.is_checked()
        assert box.locator("label.tagck").first.bounding_box()["height"] >= 44
        keep.check()
        trade.uncheck()
        box.locator("button.primary", has_text="Save").click()
        page.wait_for_function(
            "() => (document.querySelector('#col-notes .notice.ok') || {}).textContent"
            " && document.querySelector('#col-notes .notice.ok').textContent.includes('saved')"
        )
        assert server.ark.collections["alice"][rid]["tags"] == [tags["Keep"]]
        # the drop-down (in the phone's Filters sheet) applies on change: only the entry tagged Keep
        page.locator(".spanel .sp-open").click()
        page.locator("#sp-filters.open").wait_for()
        page.select_option("#sp-filters #col-tag", "Keep")
        page.wait_for_url("**/collection?**tag=Keep**")
        page.wait_for_load_state("networkidle")
        assert page.locator("#cards > li[data-id]").count() == 1
        assert page.locator(f"#cards > li[data-id='{rid}']").count() == 1
        assert errors == []
        browser.close()
