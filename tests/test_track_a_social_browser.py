"""Release 0.7.29, review round 2: the deck page's confirm chips (Like, Bookmark, Follow and the
comment confirms share the style) show a focus ring when focus moves to Yes by script after a
tap, which :focus-visible may not mark. Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_confirm_chip_shows_a_focus_ring_on_yes(server: Server, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            ctx = browser.new_context(
                viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True, color_scheme=scheme
            )
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/decks/43", wait_until="networkidle")
            page.locator("[data-social=vote]").click()
            chip = page.locator(".banner .social .confirm")
            chip.wait_for(timeout=5000)
            yes = chip.locator("button").first
            got = yes.evaluate(
                "e => ({focused: e === document.activeElement, ring: getComputedStyle(e).outlineStyle,"
                " width: getComputedStyle(e).outlineWidth})"
            )
            assert got["focused"] and got["ring"] != "none" and got["width"] != "0px", got
            assert server.ark.votes == {}  # nothing sent before Yes
            assert errors == []
        finally:
            browser.close()
