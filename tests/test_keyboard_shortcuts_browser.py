"""Desktop shortcuts (QOL-18, 0.7.46 audit QG-15): / and Ctrl+K focus the site search, ? opens
the list of shortcuts, and none of them fires while typing in a field."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_deck_menu_browser import Server, _chromium, _page

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_slash_and_ctrl_k_focus_the_search_and_question_mark_lists_the_keys(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        try:
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            page.locator("body").click(position={"x": 5, "y": 400})
            page.keyboard.press("/")
            page.wait_for_function("() => document.activeElement && document.activeElement.id === 'site-q'")
            assert page.locator("#site-q").input_value() == ""  # the / itself was not typed
            # while typing, / is a character
            page.keyboard.type("a/b")
            assert page.locator("#site-q").input_value() == "a/b"
            page.keyboard.press("Escape")
            page.locator("body").click(position={"x": 5, "y": 400})
            page.keyboard.press("Control+k")
            page.wait_for_function("() => document.activeElement && document.activeElement.id === 'site-q'")
            page.keyboard.press("Escape")
            page.locator("body").click(position={"x": 5, "y": 400})
            page.keyboard.press("?")
            box = page.locator(".mtgbox[role=dialog]:visible")
            box.wait_for(timeout=3000)
            assert box.locator("h2").inner_text() == "Keyboard shortcuts"
            keys = box.locator("dl.keys kbd").all_inner_texts()
            assert "/" in keys and "Esc" in keys and "Shift+F10" in keys
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('.mtgbox[role=dialog]:not([hidden])')")
            # ? in a comment is a character, and opens nothing
            ctext = page.locator("#ctext")
            ctext.scroll_into_view_if_needed()
            ctext.click()
            page.keyboard.type("why?")
            assert ctext.input_value() == "why?"
            assert page.locator(".mtgbox[role=dialog]:visible").count() == 0
            assert errors == [], errors
        finally:
            browser.close()


def test_the_keys_wait_behind_the_card_viewer_and_work_from_a_ticked_box(server: Server) -> None:
    """/ does not open the header's search behind an open card viewer (the keys belong to the
    viewer), and a focused tick box is not "typing": / and ? still work from it (final audit)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        try:
            page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
            card = page.locator(".deckview .c").first
            card.scroll_into_view_if_needed()
            card.click()
            page.wait_for_selector(".cardview.open", timeout=3000)
            page.keyboard.press("/")
            page.wait_for_timeout(250)
            assert page.evaluate("() => document.activeElement && document.activeElement.id") != "site-q"
            assert page.locator(".cardview.open").count() == 1
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('.cardview.open')")
            # a ticked box on My decks (Select) has the focus; the keys still work from it
            page.goto(f"{server.base}/decks?view=list", wait_until="networkidle")
            page.locator(".decks-select").click()
            tick = page.locator("#decks-list .deck .chk input[name=deck_id]").first
            tick.click()
            assert tick.is_checked() and page.evaluate("() => document.activeElement.type") == "checkbox"
            page.keyboard.press("/")
            page.wait_for_function(
                "() => document.activeElement && document.activeElement.id === 'site-q'", timeout=3000
            )
            page.keyboard.press("Escape")
            tick.focus()
            page.keyboard.press("?")
            box = page.locator(".mtgbox[role=dialog]:visible")
            box.wait_for(timeout=3000)
            assert box.locator("h2").inner_text() == "Keyboard shortcuts"
            assert errors == [], errors
        finally:
            browser.close()


def test_the_keys_do_nothing_under_a_sheet_a_menu_or_a_drop_down_list(server: Server) -> None:
    """Audit F-6 (eccfa3d9): / and ? acted under an open filter sheet, menu or list (? opened the
    shortcuts box behind the sheet and moved the focus there; / opened the top search). Under any
    open layer (static/layers.js) they do nothing, from a button's focus as from anywhere."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 360)
        try:

            def nothing(where: str) -> None:
                for key in ("/", "?"):
                    page.keyboard.press(key)
                    page.wait_for_timeout(200)
                    focus = page.evaluate("() => document.activeElement && document.activeElement.id")
                    assert focus != "site-q", (where, key)
                    assert not page.evaluate("() => document.body.classList.contains('search-open')"), (
                        where,
                        key,
                    )
                    assert page.locator(".mtgbox[role=dialog]:visible").count() == 0, (where, key)

            page.goto(f"{server.base}/cards", wait_until="networkidle")
            page.locator(".spanel .sp-open").click()
            page.locator("#sp-filters.open").wait_for()
            page.locator("#sp-filters .sp-sheet-head .close").focus()
            nothing("sheet")
            assert page.locator("#sp-filters.open").count() == 1
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('#sp-filters.open')")
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            card = page.locator("[data-card]").first
            card.scroll_into_view_if_needed()
            card.evaluate(
                "c => c.dispatchEvent(new MouseEvent('contextmenu',"
                " {bubbles: true, cancelable: true, clientX: 40, clientY: 40}))"
            )
            page.locator(".ctxmenu[role=menu]").wait_for()
            nothing("menu")
            assert page.locator(".ctxmenu[role=menu]").count() == 1
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('.ctxmenu[role=menu]')")
            page.goto(f"{server.base}/decks?view=list", wait_until="networkidle")
            if page.locator(".msel-btn:visible").count() == 0:
                page.locator(".spanel .sp-open").click()
                page.locator("#sp-filters.open").wait_for()
            page.locator(".msel-btn:visible").first.click()
            page.locator(".msel-list").wait_for()
            nothing("list")
            assert page.locator(".msel-list").count() == 1
            assert errors == [], errors
        finally:
            browser.close()
