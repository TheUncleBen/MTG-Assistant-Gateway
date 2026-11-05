"""The admin Overview's Speed card in headless Chromium at 360, 412, 1280 and 1440 px, at 100% and
200% text (a 28 px root, and every font doubled). Final polish AA1 on 71911eba: a page address in
the label column ("/decks/{deck_id}/export") broke mid-word at 1280 and 1440 px, "expor" then "t",
because the labels wrap anywhere. An address may break after a slash; anywhere else only as the
last resort, when one segment alone is wider than the column."""

from __future__ import annotations

import time

import pytest

from mtg_gateway import admin
from mtg_gateway.timing import PageTime, SpeedLog

from .test_admin_pages_browser import server  # noqa: F401
from .test_cardsearch_browser import _chromium, _page
from .test_overflow_sweep_browser import MEASURE
from .test_settings_page_browser import _admin
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

ROUTES = ["/decks/{deck_id}/export", "/history/reports/{rid}/head-to-head", "/users/{username}"]

# every line break inside a run of letters in the Speed card's labels, with the letter before
# the break: a break after a slash is fine, any other is a word broken in two
BREAKS = """() => {
  const out = [];
  for (const lab of document.querySelectorAll('#speed .ui-row .lab')) {
    const w = document.createTreeWalker(lab, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const s = n.textContent;
      let top = null;
      for (let i = 0; i < s.length; i++) {
        if (/\\s/.test(s[i])) { top = null; continue; }
        const q = document.createRange(); q.setStart(n, i); q.setEnd(n, i + 1);
        const r = [...q.getClientRects()].find(r => r.width > 0);
        if (!r) continue;
        if (top !== null && Math.abs(r.top - top) > 2 && s[i - 1] !== '/')
          out.push(s.slice(Math.max(0, i - 8), i) + '|' + s.slice(i, i + 8));
        top = r.top;
      }
    }
  }
  return {breaks: out, labels: document.querySelectorAll('#speed .ui-row .lab').length};
}"""


@pytest.mark.parametrize("zoom", ["none", "root", "text"])
@pytest.mark.parametrize("width", [360, 412, 1280, 1440])
def test_the_speed_cards_addresses_break_only_after_a_slash(
    server: Server,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
    width: int,
    zoom: str,
) -> None:
    from playwright.sync_api import sync_playwright

    log = SpeedLog()
    now = time.time()
    for route in ROUTES:
        log.add(PageTime(now - 30, route, 9.0, 8.8, 0.2, 0, 200))
    monkeypatch.setattr(admin, "speed_log", log)
    exe = _chromium()
    sid = _admin(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        page.goto(f"{server.base}/admin", wait_until="networkidle")
        if zoom != "none":
            page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
            page.wait_for_timeout(150)
        for route in ROUTES:
            assert page.locator("#speed .ui-row .lab", has_text=route).count() == 1, route
        got = page.evaluate(BREAKS)
        assert got["labels"] >= len(ROUTES) + 3, got
        assert got["breaks"] == [], (width, zoom, got["breaks"])
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()
