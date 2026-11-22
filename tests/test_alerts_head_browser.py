"""The Alerts page's three actions on a desktop each read on one line. "Mark all read" wrapped
inside its button at 1280 px while the row had room to spare (0.7.48 review of the 0.7.47 head).
Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


@pytest.mark.parametrize("width", [1280, 884])
def test_no_alerts_action_wraps_inside_its_button(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/notifications", wait_until="networkidle")
        got = page.evaluate(
            """() => [...document.querySelectorAll('.al-acts button, .al-acts a')].map(b => {
              const r = b.getBoundingClientRect();
              return [b.innerText.trim(), Math.round(r.top), Math.round(r.height)]; })"""
        )
        browser.close()
    assert len(got) == 3 and len({top for _, top, _ in got}) == 1, got  # one row of three
    # a label on one line keeps the button at its 44 px; a wrapped one grows it
    assert all(height <= 48 for _, _, height in got), got
