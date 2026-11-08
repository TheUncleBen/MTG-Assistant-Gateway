"""The Proposals bar's Apply box in headless Chromium (0.7.46): Apply from the bar and Apply from a
row's ⋮ ask in a pop-up box over the list, not on a page of their own. The list's script posts the
checked rows urlencoded, as /proposals/select reads them (parse_qs), and gets the box back as JSON;
the box's Apply carries the confirm page's click guard (locked, in the Tab order, until the box has
been seen and used), a press while locked sends nothing, and the unlocked press applies the change
once. Skipped without a Chromium build."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from .test_editor_browser import _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

ROW = ".pr-table .tr:not(.th):not(.group)"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _qty(server: Server, name: str) -> int:
    return sum(
        int(c.get("quantity") or 0)
        for c in server.ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == name
    )


def _state(server: Server, pid: str) -> str:
    return server.app.state.gateway.decks.describe("user-1", pid)["state"]


def _selects(page) -> list[dict]:
    """Every post to /proposals/select from here on: its body's type and the answer's."""
    seen: list[dict] = []

    def on(resp) -> None:
        if resp.url.endswith("/proposals/select"):
            seen.append(
                {
                    "sent": resp.request.headers.get("content-type", ""),
                    "status": resp.status,
                    "got": resp.headers.get("content-type", ""),
                }
            )

    page.on("response", on)
    return seen


def _locked_press_sends_nothing(page, server: Server, button, pid: str, url: str) -> None:
    assert button.get_attribute("aria-disabled") == "true"
    assert button.get_attribute("data-guard") is not None
    assert page.locator("#apply-box .guard-hint").is_visible()
    sent = len(server.ark.patches)
    # a click that lands at once (a window swapped in under a double-click): refused
    button.evaluate("b => b.click()")
    button.focus()
    page.keyboard.press("Enter")  # and a key press on it
    # fixed wait: a negative check: the click and the key press that come too soon send no
    # patch and leave the page within 300 ms
    page.wait_for_timeout(300)
    assert page.url == url and len(server.ark.patches) == sent and _state(server, pid) == "pending"


def _unlock_and_apply(page, button) -> None:
    page.mouse.move(200, 200)
    page.mouse.move(260, 240)
    page.wait_for_function(
        "() => !document.querySelector('#apply-box button[value=apply]').hasAttribute('aria-disabled')",
        timeout=10000,
    )
    assert not page.locator("#apply-box .guard-hint").is_visible()
    with page.expect_navigation(url="**/proposals/applied/**"):
        button.click()
    page.wait_for_function("() => !document.querySelector('[data-applied-live]')", timeout=20000)


def test_apply_from_the_bar_and_a_rows_menu_asks_in_a_guarded_box_and_applies_once(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    decks = server.app.state.gateway.decks

    async def make() -> list[str]:
        out = []
        for name in ("Opt", "Arcane Signet"):
            p = await decks.propose("user-1", "42", [{"action": "add", "card_name": name}])
            out.append(p["proposal_id"])
        return out

    opt, signet = asyncio.run(make())
    assert _qty(server, "Opt") == 0 and _qty(server, "Arcane Signet") == 0
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        try:
            ctx = browser.new_context(viewport={"width": 1280, "height": 800})
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(server.base) else r.abort())
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            selects = _selects(page)
            url = f"{server.base}/proposals"

            # 1. the bar: tick Opt, press Apply
            page.goto(url, wait_until="networkidle")
            page.locator(f"input[name=pick][value='{opt}']").check()
            page.locator(".ui-bar button[name=action][value=apply]").first.click()
            box = page.locator("#apply-box[role=dialog]:not([hidden])")
            box.wait_for(timeout=8000)
            # posted urlencoded, answered with the box (not the 403 a multipart body got)
            urlencoded = "application/x-www-form-urlencoded;charset=UTF-8"
            assert selects == [{"sent": urlencoded, "status": 200, "got": "application/json"}], selects
            assert page.url == url  # asked here, over the list
            assert box.locator("h2").inner_text().strip() == "Apply this change?"
            assert "+1 Opt" in box.inner_text() and "Arcane Signet" not in box.inner_text()
            button = box.locator(".acts button[value=apply]")
            assert button.inner_text().strip() == "Apply"
            _locked_press_sends_nothing(page, server, button, opt, url)
            _unlock_and_apply(page, button)
            assert _state(server, opt) == "applied" and _state(server, signet) == "pending"
            assert _qty(server, "Opt") == 1 and _qty(server, "Arcane Signet") == 0
            after_bar = len(server.ark.patches)
            assert after_bar >= 1

            # 2. a row's ⋮: Apply checks that row alone and presses the bar's Apply
            page.goto(url, wait_until="networkidle")
            row = page.locator(ROW, has_text="Arcane Signet").first
            row.locator("a.kebab").click()
            page.locator(".ctxmenu[role=menu]").wait_for(timeout=3000)
            page.locator(".ctxmenu [role=menuitem]", has_text="Apply").click()
            box.wait_for(timeout=8000)
            assert page.url == url
            assert box.locator("h2").inner_text().strip() == "Apply this change?"
            assert "+1 Arcane Signet" in box.inner_text()
            assert [s["status"] for s in selects] == [200, 200], selects
            button = box.locator(".acts button[value=apply]")
            _locked_press_sends_nothing(page, server, button, signet, url)
            _unlock_and_apply(page, button)
            assert _state(server, signet) == "applied"
            # each applied once: one more card each, one more write for the second change
            assert _qty(server, "Opt") == 1 and _qty(server, "Arcane Signet") == 1
            assert len(server.ark.patches) == 2 * after_bar
            assert errors == [], errors
            ctx.close()
        finally:
            browser.close()
