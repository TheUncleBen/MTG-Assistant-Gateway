"""A page's intro and its notes are two lines at most on a phone (the quality-of-life standard's
helper-text rule). The 0.7.48 review measured three to five lines on these pages; a page says what
it is in a line or two, and its rows and the Guide say the rest. Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

# page, the intro or note paragraphs on it
INTROS = [
    ("/activity", "p.ui-sub"),
    ("/history", "p.ui-sub"),
    ("/proposals", "p.ui-sub, p.ui-note"),
    ("/reports", "p.ui-sub, p.ui-note"),
    ("/search", "#s-results .panel > p.muted"),
    ("/cards", ".panel > p.muted"),
    ("/notifications", "p.ui-sub, p.bell-foot"),
    ("/lists/custom", "p.ui-sub"),
]
LINES = """(sel) => [...document.querySelectorAll(sel)]
  .filter(e => e.getClientRects().length && !e.closest('details:not([open]), dialog'))
  .map(e => { const s = getComputedStyle(e);
    const lh = parseFloat(s.lineHeight) || parseFloat(s.fontSize) * 1.4;
    return [Math.round(e.getBoundingClientRect().height / lh), e.innerText.trim().slice(0, 60)]; })"""


@pytest.mark.parametrize("width", [412, 360])
def test_page_intros_are_two_lines_at_most_on_a_phone(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _pending = _seed(server)
    long: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 915}, has_touch=True, is_mobile=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        for path, sel in INTROS:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            got = page.evaluate(LINES, sel)
            assert got, (path, sel)  # the page still has the paragraph this test is about
            long += [f"{path} @{width}: {n} lines: {text}" for n, text in got if n > 2]
        browser.close()
    assert not long, "\n".join(long)
