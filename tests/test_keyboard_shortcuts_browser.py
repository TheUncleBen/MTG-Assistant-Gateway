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
