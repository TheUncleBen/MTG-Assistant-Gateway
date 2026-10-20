"""The phone's More sheet at 344 px (a folded Fold's cover screen) with 200% text, opened on the
Reports list, a report, the Watchlist, Card search and Home: the sheet and each of its links sit
inside the window and the page does not scroll sideways (a page that did widened the phone's layout
viewport, and the sheet ran past the window's right edge with it)."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_history_page import store_report
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

CHECK = """() => {
  const vw = window.innerWidth, out = [];
  for (const e of document.querySelectorAll('.tabbar .menu.sheet, .tabbar .menu.sheet a')) {
    const r = e.getBoundingClientRect();
    if (r.left < -0.5 || r.right > vw + 0.5)
      out.push(e.tagName + ' ' + Math.round(r.left) + '..' + Math.round(r.right));
  }
  const d = document.documentElement;
  if (d.scrollWidth > d.clientWidth) out.push('sideways scroll');
  return out;
}"""


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("zoom", ["root", "text"])
def test_the_more_sheet_stays_inside_a_344px_window_at_200_percent(server: Server, zoom: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    store_report(server.db, "user-1", "rep_sheet", deck_name="Superlongunbrokendecknamewithoutanyspaceatall")
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 344, "height": 882}, has_touch=True, is_mobile=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route("https://cards.scryfall.io/**", lambda r: r.abort())
        page = ctx.new_page()
        for path in ("/reports", "/history/reports/rep_sheet", "/watchlist", "/cards", "/"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
            page.locator(".tabbar details.more > summary").click()
            page.wait_for_timeout(150)
            problems += [f"{path}: {x}" for x in page.evaluate(CHECK)]
        browser.close()
    assert problems == [], "\n".join(problems)
