"""Card search layout, in headless Chromium: on a computer every card of a grid row is as tall as
the tallest and their "Add to deck" buttons share one bottom edge, also for the cards "Load more"
appends; the filter controls do not wrap by accident at 344, 390, 673 and 1280 px (the six colour
pills on one row with the colour mode beside them where there is room, a fixed grid of pills on
phones, the S M L size control on the View row). Skipped without Chromium."""

from __future__ import annotations

from pathlib import Path

import pytest

from .test_cardsearch_browser import CATALOG, _chromium
from .test_deck_menu_browser import PNG
from .test_suggest_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True)
    s.sf.catalog = CATALOG + [f"Filler Card {i}" for i in range(50)]
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _page(p, server: Server, width: int, exe: str | None):
    sid = server.sign_in()
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": width, "height": 860}, has_touch=width < 900)
    ctx.route("https://cards.scryfall.io/**", lambda r: r.fulfill(content_type="image/png", body=PNG))
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


ROWS_JS = """
() => {
  const r = e => e.getBoundingClientRect();
  const rows = {};
  for (const li of document.querySelectorAll('#cardgrid .cc')) {
    const top = Math.round(r(li).top);
    (rows[top] = rows[top] || []).push({h: Math.round(r(li).height),
      add: Math.round(r(li.querySelector('.add')).bottom), bottom: Math.round(r(li).bottom)});
  }
  return Object.keys(rows).map(Number).sort((a, b) => a - b).map(k => rows[k]);
}
"""


def test_add_to_deck_buttons_share_a_row_bottom_edge_on_a_computer(server: Server) -> None:
    """At 1280 px the grid rows are equal-height and every Add to deck sits on the row's bottom
    edge, including the cards Load more appends (the fake Scryfall pages four at a time here)."""
    from playwright.sync_api import sync_playwright

    server.sf.page_size = 4
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, 1280, exe)
        page.goto(f"{server.base}/cards?q=s", wait_until="networkidle")
        # the page's end is near: the second page comes by itself, the third once Load more is in view
        page.wait_for_function("() => document.querySelectorAll('#cardgrid .cc').length >= 8", timeout=5000)
        page.locator("#cs-more").scroll_into_view_if_needed()
        page.wait_for_function("() => document.querySelectorAll('#cardgrid .cc').length >= 9", timeout=5000)
        rows = page.evaluate(ROWS_JS)
        assert len(rows) >= 2 and len(rows[0]) == 7  # the first row holds cards from two pages
        names = page.locator("#cardgrid .cc .nm").all_inner_texts()
        assert any(len(n) > 30 for n in names[:7])  # a long name among them, taller than the rest
        for row in rows:
            assert len({c["h"] for c in row}) == 1, row
            assert len({c["add"] for c in row}) == 1, row
            assert all(abs(c["add"] - c["bottom"]) <= 1 for c in row), row
        assert errors == []
        browser.close()


FILTERS_JS = """
() => {
  const r = e => { const b = e.getBoundingClientRect(); return {t: Math.round(b.top), l: Math.round(b.left),
    r: Math.round(b.right), b: Math.round(b.bottom)}; };
  const pills = [...document.querySelectorAll('.cs-main .colors .tog')].map(r);
  const modeEl = document.querySelector('.cs-main .colors .msel-btn') || document.querySelector('select.cmp');
  const mode = r(modeEl);
  const ident = [...document.querySelectorAll('.cs-main .ident .tog')].map(r);
  const seg = r(document.querySelector('.cs-head .seg'));
  const size = r(document.querySelector('.cs-head .cardsize'));
  const label = document.querySelector('.cs-head .cardsize .lbl');
  return {pills, mode, ident, seg, size, label: label && getComputedStyle(label).display !== 'none',
    sw: document.documentElement.scrollWidth, vw: document.documentElement.clientWidth};
}
"""


@pytest.mark.parametrize("width", [344, 390, 673, 1280])
def test_filter_controls_keep_their_rows(server: Server, width: int) -> None:
    """From 600 px the six colour pills (White to Colorless) share one row, as do the commander
    colours; the colour mode sits to the right of the pills at 1280 px and under them at 673 px.
    On phones the pills form a grid: two across at 344 px, three across at 390 px. The S M L size
    control stays on the View row at every width; nothing scrolls sideways."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, width, exe)
        page.goto(f"{server.base}/cards?q=s", wait_until="networkidle")
        if width < 600:
            page.locator(".cs-open").click()
            page.locator(".cs-filters.open").wait_for(timeout=3000)
        got = page.evaluate(FILTERS_JS)
        assert got["sw"] <= got["vw"], got
        pills, mode = got["pills"], got["mode"]
        assert len(pills) == 6
        if width >= 600:
            assert len({p["t"] for p in pills}) == 1, pills
            assert len({p["t"] for p in got["ident"]}) == 1, got["ident"]
            if width >= 1000:
                assert mode["t"] < pills[5]["b"] and mode["l"] > pills[5]["r"], (pills, mode)
            else:
                assert mode["t"] >= pills[5]["b"], (pills, mode)
        else:
            across = 2 if width < 360 else 3
            assert len({p["l"] for p in pills}) == across, pills
            assert len({p["t"] for p in pills}) == 6 // across, pills
            assert mode["t"] >= pills[5]["b"] and mode["r"] - mode["l"] >= pills[5]["r"] - pills[0]["l"] - 1
        assert abs(got["seg"]["t"] - got["size"]["t"]) <= 1, got
        assert got["label"] is (width >= 360)
        assert errors == []
        browser.close()
