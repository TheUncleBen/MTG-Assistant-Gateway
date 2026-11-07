"""The review page's Apply in headless Chromium while Archidekt is slow: the press answers within
the short wait with the progress, and the page then shows the outcome by itself (applied-live.js),
without a reload and without the "Applying" notice outliving the apply. Skipped without a
Chromium build."""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

import httpx
import pytest

from .test_editor_browser import _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

APPLY = "form.guarded button[value=apply]"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _hold_writes(server: Server) -> threading.Event:
    """Hold Archidekt's answer to the second card write until the returned event is set."""
    ark = server.ark
    orig = ark.transport.handler
    release = threading.Event()

    async def slow(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and len(ark.patches) >= 1:
            while not release.is_set():
                await asyncio.sleep(0.02)
        return orig(req)

    ark.transport.handler = slow
    return release


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1280, "light")])
def test_a_slow_apply_shows_progress_at_once_then_its_outcome_without_a_reload(
    server: Server, width: int, scheme: str
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    decks = server.app.state.gateway.decks
    changes = [
        {"action": "add", "card_name": "Opt", "quantity": 1},
        {"action": "add", "card_name": "Arcane Signet", "quantity": 1},
    ]
    pid = asyncio.run(decks.propose("user-1", "42", changes))["proposal_id"]
    release = _hold_writes(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe) if exe else p.chromium.launch()
        try:
            ctx = browser.new_context(viewport={"width": width, "height": 800}, color_scheme=scheme)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith(server.base) else r.abort())
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/proposals/{pid}", wait_until="networkidle")
            button = page.locator(APPLY).first
            page.mouse.move(5, 5)
            page.mouse.move(60, 90, steps=5)
            page.wait_for_function(
                f"() => {{ const b = document.querySelector('{APPLY}'); return b && !b.disabled; }}",
                timeout=10_000,
            )
            # the press lands on the progress well inside the assistant's 20 s wait (before the
            # change it waited all 20 s); the bound covers a busy machine drawing the next page
            with page.expect_navigation(url=f"**/proposals/{pid}?ok=applying", timeout=10_000):
                button.click()
            panel = page.locator("#proposal-live")
            assert panel.get_attribute("data-applied-live") is not None
            assert "Applying. Archidekt is updated a step at a time" in panel.inner_text()
            assert page.locator(APPLY).count() == 0  # no second Apply while it runs
            page.evaluate("window.__notReloaded = true")
            release.set()
            page.wait_for_function("() => !document.querySelector('[data-applied-live]')", timeout=20_000)
            text = panel.inner_text()
            assert "Done. Archidekt matches this proposal." in text, text
            assert "Applied. Archidekt now matches this proposal." in text and "Applying." not in text, text
            assert page.evaluate("window.__notReloaded === true")  # swapped in, not reloaded
            row = page.locator("#proposal-live-row")
            if row.is_visible():  # the list beside the panel (a wide window)
                assert "Applied" in row.inner_text() and "Applying" not in row.inner_text()
            assert errors == [], errors
            ctx.close()
        finally:
            release.set()
            browser.close()
    assert decks.describe("user-1", pid)["state"] == "applied"
    names = [c["card"]["oracleCard"]["name"] for c in server.ark.decks[42]["cards"]]
    assert "Opt" in names and "Arcane Signet" in names
