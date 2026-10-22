"""The Preconstructed decks page in a browser (UI overhaul 2026-10-21, mockup 21-precons): the
set heading sits above its tiles, Show all stays hidden while every tile fits, the name box narrows
the list as you type without leaving the page, and nothing sticks out at phone or desktop width."""

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


@pytest.mark.parametrize("width,theme", [(344, "dark"), (1280, "light")])
def test_show_all_hides_while_the_tiles_fit_and_the_name_box_filters_in_place(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    ark = server.ark
    first_deck = next(iter(ark.decks.values()))  # a second public deck, so the listing has two sets
    ark.decks[999] = {**first_deck, "id": 999, "name": "Zephyr Starter", "private": False}
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme, touch=width < 900)
        page.goto(f"{server.base}/precons?view=grid", wait_until="networkidle")
        assert page.evaluate(MEASURE) == []
        groups = page.locator("section.pre-grp")
        assert groups.count() == 2
        first = groups.first
        # the heading is above the tiles, not beside them
        head = first.locator(".pre-gh").bounding_box()
        tiles = first.locator("ul.decklist").bounding_box()
        assert head and tiles and tiles["y"] >= head["y"] + head["height"] - 1
        # one deck fits in the row: no Show all
        assert not first.locator(".pre-all").is_visible()
        assert page.locator("#pre-count").inner_text() == "2 decks in 2 sets"
        # typing narrows the list in place
        page.locator("#pre-q").fill("zephyr")
        page.wait_for_function("() => document.querySelectorAll('section.pre-grp').length === 1")
        assert "Zephyr Starter" in page.locator("section.pre-grp .decklist").inner_text()
        assert page.locator("#pre-count").inner_text() == "1 of 2 decks"
        assert "q=zephyr" in page.url
        page.locator("#pre-q").fill("zzzz")
        page.wait_for_selector(".pre-none", timeout=3000)
        assert page.locator(".pre-none").inner_text() == "No preconstructed deck matches that filter."
        assert errors == []
        browser.close()
