"""The deck editor's bar on a desktop is one line: Card size sits beside its switch, not above it
(stacked, that one label made the whole bar two lines tall). Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


def test_card_size_is_on_the_bars_one_line(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        got = page.evaluate(
            """() => { const r = s => document.querySelector(s).getBoundingClientRect();
              const lbl = r('.editbar .cardsize .lbl'), seg = r('.editbar .cardsize .sizeseg'),
                    save = r('.editbar button'), bar = r('.editbar');
              return {beside: lbl.right <= seg.left + 1 && lbl.bottom > seg.top && lbl.top < seg.bottom,
                      oneLine: bar.height < 2 * save.height}; }"""
        )
        assert got == {"beside": True, "oneLine": True}, got
        browser.close()
