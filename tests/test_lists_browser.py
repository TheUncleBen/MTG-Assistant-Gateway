"""The My lists page in a browser (static/lists.js): fits a phone with no sideways scroll and
44 px targets, holds together at 200% text, the rows' menus open from the ⋮, a right-click and a
press and hold, the boxes fill from the row and end on a solid red button, and the pins' copies,
notes, the tag preview, the custom card's checks and its cropped picture work in place."""

from __future__ import annotations

import io

import pytest

from .test_admin_card_models_browser import HOLD
from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_editor_browser import _link
from .test_lists_page import _seed
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

TEXT = (
    ".lp .ui-table .t, .lp .ui-table .s, .lp .ui-table .sm-only, .lp .list-head, .lp .ui-sub, .lp .btn, "
    ".lp-tabs a, .lp-chip, .ctxmenu [role=menuitem], .mtgbox h2, .mtgbox p, .mtgbox label, "
    ".mtgbox .acts button, .lp-under span"
)
TABS = ["templates", "defaults", "tags", "pins", "custom"]


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    _seed(s.ark)
    object.__setattr__(s.app.state.gateway.settings, "writes_enabled", True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _targets(page, sel: str) -> list[float]:
    return page.evaluate(
        "(sel) => [...document.querySelectorAll(sel)].filter(b => b.offsetParent !== null)"
        ".map(b => Math.min(b.getBoundingClientRect().width, b.getBoundingClientRect().height))",
        sel,
    )


@pytest.mark.parametrize("width,theme", [(390, "light"), (390, "dark"), (810, "light"), (1366, "dark")])
def test_every_tab_fits_and_its_menu_opens_three_ways(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        for slug in TABS:
            page.goto(f"{server.base}/lists/{slug}", wait_until="networkidle")
            assert page.evaluate(MEASURE) == [], slug
            assert page.evaluate(CONTRAST, TEXT) == [], slug
            sizes = _targets(page, ".lp .ui-table .kebab, .lp-step button, .lp .list-head .btn, .lp-tabs a")
            if width < 900:  # a touch screen: every target 44 px
                assert sizes and min(sizes) >= 43.5, (slug, sizes)
            # the ⋮ is a plain drop-down without scripts; with them it opens the shared menu
            assert page.locator(".lp details.rowmenu[open]").count() == 0
        page.goto(f"{server.base}/lists/templates", wait_until="networkidle")
        row = page.locator(".lp .ui-table .tr[data-row]").filter(has_text="Ramp").first
        row.locator(".kebab").click()
        menu = page.locator(".ctxmenu")
        assert menu.is_visible() and page.locator(".lp details.rowmenu[open]").count() == 0
        items = [t.strip() for t in menu.locator("[role=menuitem]").all_inner_texts()]
        assert items == ["Edit…", "Add to a deck…", "Delete…"]
        last = menu.locator("[role=menuitem]").last
        assert "danger" in (last.get_attribute("class") or "")
        if width < 900:
            assert min(_targets(page, ".ctxmenu [role=menuitem]")) >= 43.5
        assert page.evaluate(MEASURE) == []
        page.keyboard.press("Escape")
        assert menu.count() == 0
        page.wait_for_timeout(500)
        row.click(button="right")
        assert page.locator(".ctxmenu").is_visible()
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        if width < 900:
            page.evaluate(HOLD, ".lp .ui-table .tr[data-row] .title")
            page.wait_for_timeout(900)
            assert page.locator(".ctxmenu").is_visible()
            page.keyboard.press("Escape")
            page.wait_for_timeout(500)
        # Delete… fills the box from the row and ends on the solid red button
        row.locator(".kebab").click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Delete…").click()
        box = page.locator("#tpl-del")
        box.wait_for(state="visible")
        assert "Delete the template “Ramp”?" in box.inner_text()
        final = box.locator(".acts button").last
        assert final.inner_text().strip() == "Delete “Ramp”" and "danger" in (
            final.get_attribute("class") or ""
        )
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        page.keyboard.press("Escape")
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        assert server.ark.account.templates["alice"][0]["name"] == "Ramp"
        browser.close()
    assert errors == []


@pytest.mark.parametrize("zoom", ["root", "text"])
def test_the_page_holds_together_on_a_phone_at_200_percent_text(server: Server, zoom: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        for slug in TABS:
            page.goto(f"{server.base}/lists/{slug}", wait_until="networkidle")
            page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
            assert page.evaluate(MEASURE) == [], slug
        page.locator("[data-box-open='cc-box']").click()
        page.locator("#cc-box").wait_for(state="visible")
        assert page.evaluate(MEASURE) == []
        browser.close()
    assert errors == []


def test_the_boxes_and_rows_work_in_place(server: Server) -> None:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    ark = server.ark.account
    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366, "light", exe)
        # pins: one more copy, then a note saved on leaving the field
        page.goto(f"{server.base}/lists/pins", wait_until="networkidle")
        row = page.locator(".lp .ui-table .tr[data-row]").first
        row.locator(".lp-step button[aria-label='One more']").click()
        page.locator(".toast").wait_for(state="visible")
        assert "Saved on Archidekt." in page.locator(".toast").inner_text()
        assert ark.pins["alice"][0]["quantity"] == 2 and row.locator(".lp-step .n").inner_text() == "2"
        note = row.locator("textarea")
        note.fill("Trade for it")
        assert row.locator("[data-counter]").inner_text() == "12 / 256"
        page.locator("body").click(position={"x": 5, "y": 5})
        page.wait_for_timeout(400)
        assert ark.pins["alice"][0]["notes"] == "Trade for it"
        # below one copy asks first; the box removes the pin
        row.locator(".lp-step button[aria-label='One fewer']").click()
        row.locator(".lp-step button[aria-label='One fewer']").click()
        box = page.locator("#pin-rm")
        box.wait_for(state="visible")
        assert "Remove the pin on “Sol Ring”?" in box.inner_text()
        box.locator(".acts button").last.click()
        page.wait_for_load_state("networkidle")
        assert (
            ark.pins["alice"] == [] and "Pin removed on Archidekt." in page.locator(".notice.ok").inner_text()
        )

        # color tags: the picker, the hex and the preview move together; the save goes through
        page.goto(f"{server.base}/lists/tags", wait_until="networkidle")
        page.locator("[data-box-open='tag-box']").click()
        box = page.locator("#tag-box")
        box.wait_for(state="visible")
        assert box.locator("#tag-h").input_value() == "#fa890d"
        box.locator("#tag-n").fill("Keep")
        box.locator("#tag-h").fill("#1ebb6c")
        assert box.locator("#tag-c").input_value() == "#1ebb6c"
        assert box.locator("#tag-pv .nm").inner_text() == "Keep"
        assert "rgb(30, 187, 108)" in box.locator("#tag-pv").evaluate("e => getComputedStyle(e).borderColor")
        box.locator(".acts button").last.click()
        page.wait_for_load_state("networkidle")
        assert [t["name"] for t in ark.tags["alice"]] == ["Cut", "Keep"]
        assert "Color tag created on Archidekt and read back." in page.locator(".notice.ok").inner_text()

        # default categories: the main one follows the ticks
        page.goto(f"{server.base}/lists/defaults", wait_until="networkidle")
        page.locator(".lp .ui-table .tr[data-row] .kebab").first.click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Change categories…").click()
        box = page.locator("#def-box")
        box.wait_for(state="visible")
        assert box.locator("input[name=cat][value=Ramp]").is_checked()
        assert box.locator("input[name=main][value=Ramp]").is_checked()
        box.locator("input[name=main][value=Commander]").check()
        assert box.locator("input[name=cat][value=Commander]").is_checked()
        box.locator("input[name=cat][value=Commander]").uncheck()
        assert box.locator("input[name=main][value=Ramp]").is_checked()
        box.locator("input[name=cat][value=Ramp]").uncheck()
        box.locator(".acts button").last.click()
        assert "Tick at least one category" in box.locator(".boxnote").inner_text()
        assert ark.defaults["alice"][9003] == ["Ramp"]
        page.keyboard.press("Escape")
        page.locator(".mtgbox-scrim").wait_for(state="detached")

        # custom cards: the two checks before anything is sent, then the cropped picture
        page.goto(f"{server.base}/lists/custom", wait_until="networkidle")
        page.locator("[data-box-open='cc-box']").click()
        box = page.locator("#cc-box")
        box.wait_for(state="visible")
        assert box.locator(".bh h2").inner_text() == "New custom card"
        assert box.locator(".lp-pick").first.is_disabled()
        box.locator(".acts button").last.click()
        assert box.locator("#cc-err").inner_text() == "Custom cards need at least a front name."
        box.locator("#f-n").fill("Rift")
        box.locator(".acts button").last.click()
        assert box.locator("#cc-err").inner_text() == "Give the card at least one type."
        assert len(ark.customs["alice"]) == 1
        box.locator("label.lp-switch", has_text="Has a back face").click()
        assert box.locator("#cc-back").is_checked()
        assert box.locator("[data-face=b]").get_attribute("aria-selected") == "true"
        page.keyboard.press("Escape")
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        page.wait_for_timeout(500)
        page.locator(".lp .ui-table .tr[data-row] .kebab").first.click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Edit…").click()
        box.wait_for(state="visible")
        assert box.locator(".bh h2").inner_text() == "Edit “Grizzled Mentor”"
        assert box.locator("#f-st").input_value() == "Human Advisor"
        raw = io.BytesIO()
        Image.new("RGB", (800, 1000), (200, 40, 40)).save(raw, "JPEG")
        with page.expect_file_chooser() as fc:
            box.locator(".lp-pick").first.click()
        fc.value.set_files({"name": "mentor.jpg", "mimeType": "image/jpeg", "buffer": raw.getvalue()})
        cropper = box.locator(".lp-cropper")
        cropper.wait_for(state="visible")
        assert box.locator(".lp-crop[data-crop=f] img").count() == 1
        assert page.evaluate(MEASURE) == []
        cropper.locator("button", has_text="Use this picture").click()
        page.locator(".toast").wait_for(state="visible")
        assert "Picture saved on Archidekt and read back." in page.locator(".toast").inner_text()
        who, body, _d = ark.uploads[-1]
        assert who == "alice" and body.startswith(b"\x89PNG")
        assert Image.open(io.BytesIO(body)).size == (500, 700)
        assert ark.customs["alice"][0]["frontImageUrl"].startswith("https://")
        assert box.locator(".lp-crop[data-crop=f]").inner_text().strip() == "Picture set"
        page.keyboard.press("Escape")
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        page.wait_for_timeout(500)
        # the delete box: the tick unlocks the solid red button
        page.locator(".lp .ui-table .tr[data-row] .kebab").first.click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Delete…").click()
        box = page.locator("#cc-del")
        box.wait_for(state="visible")
        final = box.locator(".acts button").last
        assert final.is_disabled() and "danger" in (final.get_attribute("class") or "")
        box.locator("[data-gate]").check()
        assert final.is_enabled()
        final.click()
        page.wait_for_load_state("networkidle")
        assert ark.customs["alice"] == []
        assert "Deleted “Grizzled Mentor” on Archidekt." in page.locator(".notice.ok").inner_text()
        browser.close()
    assert errors == []
