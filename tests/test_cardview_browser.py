"""The card viewer in headless Chromium (static/cardview.js, R-131): mana and rules symbols are
drawn as the gateway's own discs (no raw {T} codes), the dialog never scrolls sideways at any
width, the backdrop and focus behave like a dialog, and Escape closes it. Skipped without a
Chromium build."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from .test_scan_browser import _chromium_path
from .test_social_browser import Server

pytest.importorskip("playwright")

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAUAAAAHCAIAAAAbd2raAAAAFElEQVR4nGNgYGD4z8DAwMDAwMAAAAwAA/8BpYQAAAAASUVORK5CYII="
)


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("width", [390, 760, 1366, 2560])
def test_viewer_draws_symbols_and_fits(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        target = page.locator(".deckview .c[data-text*='{']").first
        assert target.count() == 1, "the sample deck has a card whose rules text carries a symbol"
        target.scroll_into_view_if_needed()
        target.click()
        page.wait_for_selector(".cardview.open", timeout=3000)
        page.wait_for_timeout(250)
        state = page.evaluate(
            "() => { const v = document.querySelector('.cardview'); const b = v.querySelector('.box');"
            " return {dialog: v.getAttribute('role'), modal: v.getAttribute('aria-modal'),"
            " boxOverflow: b.scrollWidth - b.clientWidth, pageOverflow: document.documentElement.scrollWidth"
            " - document.documentElement.clientWidth, pips: v.querySelectorAll('.rules .pip').length,"
            " raw: /\\{[A-Z0-9\\/]+\\}/.test(v.querySelector('.rules').textContent),"
            " focused: document.activeElement.className, title: v.querySelector('h3').textContent,"
            " boxRight: b.getBoundingClientRect().right, win: window.innerWidth,"
            " datalist: document.querySelectorAll('datalist').length}; }"
        )
        assert state["dialog"] == "dialog" and state["modal"] == "true"
        assert state["boxOverflow"] == 0 and state["pageOverflow"] == 0, state
        assert state["boxRight"] <= state["win"]
        assert state["pips"] >= 1 and not state["raw"], state
        assert "close" in state["focused"]
        assert state["title"]
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)
        assert page.locator(".cardview.open").count() == 0
        assert errors == []
        browser.close()
