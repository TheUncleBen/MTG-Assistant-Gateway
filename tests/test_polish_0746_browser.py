"""The 0.7.46 polish follow-ups in headless Chromium: the Account page's rows at large text, the
review page's facts at large text on the Fold's cover screen, the Reports page's chip row, the
report page's Forge seats at large text, the Compare page's picker on a cold start, the scanner's
result card and status line, and the Watchlist form's tick box. Skipped without a Chromium
build."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from .test_editor_browser import PNG, _link
from .test_history_page import store_report
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

LONGER = "Aesi, Tyrant of Gyre Strait: Lands Matter Big Ramp Value Pile (Budget Upgrade, v3)"


@pytest.fixture(scope="module")
def chromium():
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        yield browser
        browser.close()


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True, writes_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@contextmanager
def _page(browser, server: Server, sid: str, width: int, *, touch: bool = True, **extra):
    ctx = browser.new_context(
        viewport={"width": width, "height": 800}, has_touch=touch, is_mobile=touch, **extra
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        yield page
        assert errors == []
    finally:
        ctx.close()


ZOOMS = {"root": ROOT_200, "text": TEXT_200}
SCROLLS = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


# -- Account: the Look rows at large text ----------------------------------------------------------

# Each row's label and value (Theme: System, Layout: Fit the screen) as laid out: each one's
# words fit its own box (a box squeezed to nothing lets its words run over the other's), the two
# boxes do not overlap, and both sit inside their row.
ROWS = """
() => [...document.querySelectorAll('.ygrp .yrow')].filter((r) => r.querySelector('.val')).map((r) => {
  const box = (r.querySelector('summary') || r).getBoundingClientRect();
  const lbl = r.querySelector('.lbl'), val = r.querySelector('.val');
  const l = lbl.getBoundingClientRect(), v = val.getBoundingClientRect();
  return { label: lbl.textContent, overlap: l.right > v.left + 0.5 && v.right > l.left + 0.5,
    fits: l.width + 0.5 >= lbl.scrollWidth && v.width + 0.5 >= val.scrollWidth,
    inside: l.left >= box.left - 0.5 && v.right <= box.right + 0.5 && l.right <= box.right + 0.5 };
})
"""


@pytest.mark.parametrize("zoom", ["root", "text"])
@pytest.mark.parametrize("width", [344, 390])
def test_account_rows_keep_label_and_value_apart_at_200_percent(chromium, server, width, zoom) -> None:
    """Theme and Layout: at twice the text size the value (“Fit the screen”) overlapped the
    label; the value now wraps on its side and the label keeps its words."""
    sid = _link(server)
    with _page(chromium, server, sid, width) as page:
        page.goto(f"{server.base}/account", wait_until="networkidle")
        page.evaluate(ZOOMS[zoom])
        page.wait_for_timeout(30)
        rows = page.evaluate(ROWS)
        assert [r["label"] for r in rows] == ["Theme", "Layout"]
        assert all(not r["overlap"] and r["fits"] and r["inside"] for r in rows), rows
        assert page.evaluate(SCROLLS) == 0


# -- the review page's facts at large text on the Fold's cover screen --------------------------------


def _pending(server: Server) -> str:
    import time

    from .test_history_page import seed_history

    seed_history(server.db, "user-1", now=int(time.time()))
    return next(p["id"] for p in server.db.list_proposals("user-1", limit=100) if p["state"] == "pending")


FACTS = """
() => { const dl = document.querySelector('.ui-meta');
  return [...dl.querySelectorAll('dd')].map((d) => d.getBoundingClientRect().right <= dl.getBoundingClientRect().right + 0.5); }
"""


@pytest.mark.parametrize("zoom", ["root", "text"])
def test_the_review_facts_stay_inside_the_page_at_344_px_and_200_percent(chromium, server, zoom) -> None:
    """The facts list (Deck, Asked by, …): its value column shrank no further than the app's name
    on one line, so the whole page (and the tab bar with it) scrolled sideways by a few pixels."""
    sid = _link(server)
    pid = _pending(server)
    with _page(chromium, server, sid, 344) as page:
        page.goto(f"{server.base}/proposals/{pid}", wait_until="networkidle")
        page.evaluate(ZOOMS[zoom])
        page.wait_for_timeout(30)
        assert page.evaluate(SCROLLS) == 0
        assert all(page.evaluate(FACTS))
