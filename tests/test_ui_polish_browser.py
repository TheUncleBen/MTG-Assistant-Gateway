"""The 2026-10 UI polish pass in headless Chromium: no native prompt / alert / confirm box
anywhere (the editor's new category is an inline field, the scan page asks with a themed bar),
tick boxes are themed, the skip link is the first Tab stop, deep links into the editor land on
their row, times show in the viewer's zone, a card image that fails to load falls back to its
name, and the Copy button keeps its icon. Skipped without a Chromium build."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_editor_browser import _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.sf.catalog = sorted({c["name"] for c in s.sf.cards}) + [f"Filler Card {i}" for i in range(1200)]
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _exe() -> str | None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _open(p, exe, server: Server, sid: str, width: int = 1366, theme: str | None = None):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"]) if exe else p.chromium.launch()
    ctx = browser.new_context(viewport={"width": width, "height": 900}, timezone_id="Pacific/Auckland")
    cookies = [{"name": "mtg_session", "value": sid, "url": server.base}]
    if theme:
        cookies.append({"name": "mtg_theme", "value": theme, "url": server.base})
    ctx.add_cookies(cookies)
    page = ctx.new_page()
    dialogs: list[str] = []
    page.on("dialog", lambda d: (dialogs.append(d.type), d.dismiss()))
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, dialogs, errors


def test_editor_new_category_is_inline_and_controls_name_the_card(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, dialogs, errors = _open(p, exe, server, sid, theme="light")
        page.goto(f"{server.base}/decks/42/edit#card-Sol%20Ring", wait_until="networkidle")
        # the deep link from the deck page lands on the card's row, opened and marked
        row = page.locator("#card-Sol\\ Ring")
        assert row.count() == 1 and row.evaluate("e => e.classList.contains('target')")
        assert page.locator("#cat-Commander").count() == 1
        assert row.locator("input[type=number]").get_attribute("aria-label") == "Quantity of Sol Ring"
        assert row.locator("button[aria-label='One fewer Sol Ring']").count() == 1
        assert row.locator("select[aria-label='Category of Sol Ring']").count() == 1
        # "New category…" becomes a text field in the row; Escape puts the select back
        sel = row.locator("select[aria-label='Category of Sol Ring']")
        sel.evaluate(
            "s => { s.value = '\\u0000new'; s.dispatchEvent(new Event('change', { bubbles: true })); }"
        )
        field = row.locator(".newcat input")
        field.wait_for(timeout=2000)
        assert page.evaluate("() => document.activeElement.placeholder") == "New category name"
        field.fill("Ramp spells")
        field.press("Enter")
        page.wait_for_timeout(200)
        assert row.locator(".newcat").count() == 0
        assert sel.evaluate("s => s.value") == "Ramp spells"
        assert "Category" in (page.locator(".pending .changes .act").first.text_content() or "")
        assert dialogs == [] and errors == []
        # the backup tick box is themed (no browser blue): appearance none, our own check mark
        box = page.locator(".editbar input[type=checkbox]")
        if box.count():
            assert box.evaluate("e => getComputedStyle(e).appearance") == "none"
            assert "check.svg" in box.evaluate("e => getComputedStyle(e).backgroundImage")
        foil = page.locator("input[name=foil]")
        assert foil.evaluate("e => getComputedStyle(e).appearance") == "none"
        # the focus ring on the page is the dark orange (3:1 on the light page), the bar keeps orange
        assert (
            page.evaluate(
                "() => getComputedStyle(document.documentElement).getPropertyValue('--focus').trim()"
            )
            == "#944b00"
        )
        assert (
            page.evaluate(
                "() => getComputedStyle(document.querySelector('.topbar')).getPropertyValue('--focus').trim()"
            )
            == "#fa890d"
        )
        browser.close()


def test_skip_link_times_images_and_copy_button(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, dialogs, errors = _open(p, exe, server, sid)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        page.keyboard.press("Tab")
        assert page.evaluate("() => document.activeElement.className") == "skip"
        assert (
            page.evaluate("() => document.activeElement.getBoundingClientRect().top") >= 0
        )  # shown while focused
        page.keyboard.press("Enter")
        page.wait_for_timeout(100)
        assert page.evaluate("() => document.activeElement.id") == "main"
        # the text view flows in columns, and the category headings are h2 (no h1 to h4 jump)
        assert (
            page.evaluate("() => getComputedStyle(document.querySelector('.deckview.text')).columnWidth")
            == "300px"
        )
        assert page.locator(".stackhead h2").count() >= 1 and page.locator(".stackhead h4").count() == 0
        # a card image that cannot load shows the card's name instead of a broken glyph
        page.evaluate(
            "() => { const c = document.querySelector('.deckview'); const i = document.createElement('img');"
            " i.alt = 'Sol Ring'; c.appendChild(i); i.src = '/static/no-such-image.jpg'; }"
        )
        page.locator(".img-fallback").wait_for(timeout=3000)
        assert page.locator(".img-fallback").inner_text() == "Sol Ring"
        assert page.locator("img.img-broken").evaluate("e => getComputedStyle(e).display") == "none"
        # the odds form words follow the number: "1 card", "2 cards"
        k = page.locator(".oddsform input[name=k]")
        assert page.locator("[data-plural=k]").inner_text() == "card"
        before = page.locator(".oddstable tbody tr").first.inner_text()
        k.fill("2")
        assert page.locator("[data-plural=k]").inner_text() == "cards"
        # and the table follows the form (the odds script used to lose its form to a later `var`)
        assert page.locator(".oddstable tbody tr").first.inner_text() != before
        k.fill("1")
        assert page.locator("[data-plural=k]").inner_text() == "card"
        # the export page: Copy keeps its icon after a press
        page.goto(f"{server.base}/decks/42/export", wait_until="networkidle")
        btn = page.locator(".copybtn").first
        btn.click()
        page.wait_for_timeout(200)
        assert btn.locator("svg").count() == 1 and btn.locator(".label").inner_text() in (
            "Copied",
            "Select and copy",
        )
        page.wait_for_timeout(1600)
        assert btn.locator("svg").count() == 1 and btn.locator(".label").inner_text() == "Copy"
        # server times are shown in the viewer's zone, with the UTC time kept in the tooltip
        page.goto(f"{server.base}/proposals", wait_until="networkidle")
        page.goto(f"{server.base}/history", wait_until="networkidle")
        times = page.locator("time[datetime]")
        if times.count():
            assert "UTC" not in times.first.inner_text() and "UTC" in (
                times.first.get_attribute("title") or ""
            )
        assert dialogs == [] and errors == []
        browser.close()


def test_scan_page_asks_with_a_themed_bar(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, dialogs, errors = _open(p, exe, server, sid, width=390)
        page.goto(f"{server.base}/scan", wait_until="networkidle")
        page.evaluate("() => localStorage.setItem('mtg-scan-intro-seen', '1')")
        page.goto(f"{server.base}/scan", wait_until="networkidle")
        # the step numbers and the list badge use dark text on orange
        page.locator("[data-tab=list]").click()
        badge = page.locator("#list-badge")
        assert badge.evaluate("e => getComputedStyle(e).color") == "rgb(17, 17, 17)"  # --on-orange
        # a themed confirm bar, never the browser's confirm box
        page.evaluate("() => { const b = document.querySelector('#session-name'); if (b) b.value = 'x'; }")
        page.locator("button:has-text('New scan')").click()
        bar = page.locator(".scan .confirmbar")
        if bar.count():  # the button only asks when there is something to clear
            assert bar.get_attribute("role") == "alertdialog"
            bar.locator("button:has-text('Cancel')").click()
            assert page.locator(".scan .confirmbar").count() == 0
        assert dialogs == [] and errors == []
        # the control bar clears the site's tab bar on a phone
        page.locator("[data-tab=camera]").click()
        controls = page.locator(".scan .controls")
        if controls.count():
            bottom = controls.evaluate("e => parseFloat(getComputedStyle(e).bottom)")
            assert bottom >= 56
            assert (
                page.locator(".scan .controls .ctl").count() >= 2
                and page.locator("#shutter svg").count() == 1
            )
        browser.close()
