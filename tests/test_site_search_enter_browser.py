"""Enter in the top bar's search keeps the "?" list's promise (0.7.46 re-audit QG-12, QOL-21):
it opens the highlighted suggestion, or the card whose exact name was typed while its suggestion
is on screen; any other text is the deck search as before."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .test_card_menu_suggest_browser import _context, _exe, _server

pytest.importorskip("playwright")


def _box(page, width: int):  # noqa: ANN001
    if width < 600 and not page.locator("#site-q").is_visible():
        page.locator(".topbar .searchbtn").click()
    box = page.locator("#site-q")
    box.click()
    return box


@pytest.mark.parametrize("width", [412, 1280])
def test_enter_opens_the_typed_card_or_the_highlighted_row_else_searches_decks(
    tmp_path: Path, width: int
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path, watchlists_enabled=True)
    try:
        sid = server.sign_in()
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            page = _context(browser, server, sid, width).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/account/details", wait_until="networkidle")
            # the exact name, its suggestion on screen, nothing highlighted: Enter opens the card
            box = _box(page, width)
            box.press_sequentially("sol ring", delay=30)
            row = page.locator("#site-q-list [role=option][data-name='Sol Ring']")
            row.wait_for(timeout=3000)
            assert page.locator("#site-q-list [role=option][aria-selected=true]").count() == 0
            box.press("Enter")
            page.wait_for_url(re.compile(r".*/cards\?q=Sol(%20|\+)Ring$"), timeout=5000)
            # a highlighted row: Enter opens it
            page.goto(f"{server.base}/account/details", wait_until="networkidle")
            box = _box(page, width)
            box.press_sequentially("sol", delay=30)
            row.wait_for(timeout=3000)
            box.press("ArrowDown")
            page.locator("#site-q-list [role=option][aria-selected=true]").first.wait_for(timeout=3000)
            picked = page.locator("#site-q-list [role=option][aria-selected=true]").first.get_attribute(
                "data-name"
            )
            box.press("Enter")
            page.wait_for_url(re.compile(r".*/cards\?q="), timeout=5000)
            assert page.url.endswith("q=" + picked.replace(" ", "%20")) or page.url.endswith(
                "q=" + picked.replace(" ", "+")
            ), (page.url, picked)
            # part of a name, nothing highlighted: the deck search, as typed
            page.goto(f"{server.base}/account/details", wait_until="networkidle")
            box = _box(page, width)
            box.press_sequentially("sol", delay=30)
            row.wait_for(timeout=3000)
            box.press("Enter")
            page.wait_for_url(re.compile(r".*/search\?q=sol$"), timeout=5000)
            assert errors == []
            browser.close()
    finally:
        server.stop()
