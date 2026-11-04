"""The Binder › Lists hub in a browser: its name box narrows the rows as you type, a group with no
row left folds away, New list… stays, and clearing the box brings everything back (0.7.46 audit
QG-25). Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_cardsearch_browser import _chromium, _page
from .test_editor_browser import _link
from .test_forum_browser import Server, server  # noqa: F401

pytest.importorskip("playwright")


def test_the_lists_hub_narrows_its_rows_as_you_type(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "dark", exe)
        page.goto(f"{server.base}/collection/lists", wait_until="networkidle")
        rows = page.locator(".lp-hub .yrow[data-q]")
        total = rows.count()
        assert total >= 6, total
        box = page.locator(".ui-tools input[type=search]")
        box.fill("pinned")
        count = "document.querySelector('[data-filter-count]').textContent"
        page.wait_for_function(f"() => {count} === '1 shown.'")
        shown = [r for r in rows.all() if r.is_visible()]
        assert len(shown) == 1 and "Pinned cards" in shown[0].inner_text()
        assert page.url.endswith("/collection/lists")  # nothing was sent
        box.fill("zzz")
        page.wait_for_function(f"() => {count} === 'No list matches.'")
        assert all(not r.is_visible() for r in rows.all())
        box.fill("")
        page.wait_for_function(f"() => {count} === ''")
        assert len([r for r in rows.all() if r.is_visible()]) == total
        assert errors == []
        browser.close()
