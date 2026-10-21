"""Card search layout, in headless Chromium: on a computer every card of the grid is as tall as
every other and their "Add to deck" buttons share one bottom edge, also for the cards "Load more"
appends (which never move the buttons already on screen, and get their ⋮ menu too); the first
answer fills the screen and a few rows, then only Load more asks for more; the filter controls do
not wrap by accident at 344, 390, 673 and 1280 px (the six colour pills on one row with the
colour mode beside them where there is room, a fixed grid of pills on phones, the S M L size
control on the View row). Skipped without Chromium."""

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


ADDS_JS = """
() => [...document.querySelectorAll('#cardgrid .cc')].map(li => {
  const b = li.querySelector('.add').getBoundingClientRect();
  return [Math.round(b.top + scrollY), Math.round(b.left)];
})
"""


def _lands(server: Server, n: int) -> None:
    """``n`` lands of one search, some with a long name, two with a long type line and mana cost:
    tiles that would be taller than the rest if their text set their height."""
    import copy

    tower = next(c for c in server.sf.cards if c["name"] == "Command Tower")
    for i in range(n):
        c = copy.deepcopy(tower)
        c["id"] = f"00000000-0000-4000-8000-{i:012d}"
        c["name"] = f"Test Land {i:02d}" + (" with a much longer name than the others" if i % 9 == 8 else "")
        c["collector_number"] = str(100 + i)
        if i in (10, 25):
            c["type_line"] = "Legendary Land Creature — Elemental Spirit Wizard Horror Beast Dragon"
            c["mana_cost"] = "{W}{U}{B}{R}{G}{C}{X}{2}{W}{U}"
        server.sf.extra_cards.append(c)


def test_add_to_deck_buttons_share_a_row_bottom_edge_on_a_computer(server: Server) -> None:
    """At 1280 px every tile is as tall as every other and each Add to deck sits on its row's
    bottom edge, including the cards Load more appends: an appended card, however long its name
    or type line, never moves the Add to deck buttons already on screen."""
    from playwright.sync_api import sync_playwright

    _lands(server, 80)
    server.sf.page_size = 12
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, 1280, exe)
        page.goto(f"{server.base}/cards?q=test+land", wait_until="networkidle")
        page.wait_for_timeout(600)
        before = page.evaluate(ADDS_JS)
        assert len(before) % 7  # the last row is part-filled, so Load more adds cards to it
        page.locator("#cs-more [data-more]").click()
        page.wait_for_function(f"() => document.querySelectorAll('#cardgrid .cc').length > {len(before)}")
        after = page.evaluate(ADDS_JS)
        assert after[: len(before)] == before
        page.wait_for_function(
            f"() => document.querySelectorAll('#cardgrid .cc .kebab').length === {len(after)}"
        )
        rows = page.evaluate(ROWS_JS)
        assert len({c["h"] for row in rows for c in row}) == 1, rows
        for row in rows:
            assert len({c["add"] for c in row}) == 1, row
            assert all(abs(c["add"] - c["bottom"]) <= 1 for c in row), row
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_card_search_fills_the_screen_then_waits_for_load_more(server: Server, width: int) -> None:
    """The first answer fills the screen and a few rows more, then stops: scrolling to the end
    loads nothing, and only the Load more button asks for the next page."""
    from playwright.sync_api import sync_playwright

    _lands(server, 120)
    server.sf.page_size = 6
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, width, exe)
        page.goto(f"{server.base}/cards?q=test+land", wait_until="networkidle")
        page.wait_for_timeout(800)
        shown = page.locator("#cardgrid .cc").count()
        assert shown > 6  # more than the first page: the screen was filled
        more = page.locator("#cs-more [data-more]")
        assert more.is_visible()
        # its top is below the screen's foot: a few rows past it are already there
        assert page.evaluate("document.querySelector('#cs-more').getBoundingClientRect().top") > 860
        for _ in range(6):
            page.mouse.wheel(0, 5000)
            page.wait_for_timeout(250)
        page.wait_for_load_state("networkidle")
        assert page.locator("#cardgrid .cc").count() == shown  # scrolling never loads
        more.click()
        page.wait_for_function(f"() => document.querySelectorAll('#cardgrid .cc').length === {shown + 6}")
        page.wait_for_timeout(600)
        assert page.locator("#cardgrid .cc").count() == shown + 6  # one page, nothing more by itself
        assert errors == []
        browser.close()


FILTERS_JS = """
() => {
  const r = e => { const b = e.getBoundingClientRect(); return {t: Math.round(b.top), l: Math.round(b.left),
    r: Math.round(b.right), b: Math.round(b.bottom)}; };
  const groups = document.querySelectorAll('.spanel .colors');
  const pills = [...groups[0].querySelectorAll('.tog')].map(r);
  const mode = r(groups[0].querySelector('.msel-btn') || groups[0].querySelector('select'));
  const ident = [...groups[1].querySelectorAll('.tog')].map(r);
  const seg = r(document.querySelector('.rbar .segc.view'));
  const sizeEl = document.querySelector('.rbar .cardsize'), size = r(sizeEl);
  const sheetSize = document.querySelector('#sp-filters .sheet-size .cardsize');
  const label = document.querySelector('.rbar .cardsize .lbl');
  const rbar = r(document.querySelector('.rbar')), panel = r(document.querySelector('.spanel'));
  return {pills, mode, ident, seg, size, sizeShown: !!sizeEl.offsetParent,
    sheetSize: !!(sheetSize && sheetSize.offsetParent),
    label: label && getComputedStyle(label).display !== 'none', rbar, panel,
    sw: document.documentElement.scrollWidth, vw: document.documentElement.clientWidth};
}
"""


@pytest.mark.parametrize("width", [344, 390, 673, 810, 1024, 1280])
def test_filter_controls_keep_their_rows(server: Server, width: int) -> None:
    """From 600 px the six colour pills (White to Colorless) share one row, as do the commander
    colours; the colour mode sits to the right of the pills where the group is wide enough and
    under them where it is not. On phones the pills form two rows of three. The results bar sits
    inside the panel, with the S M L size control on the view's row from 600 px and in the Filters
    sheet on phones; nothing scrolls sideways."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, width, exe)
        page.goto(f"{server.base}/cards?q=s", wait_until="networkidle")
        if width < 600:
            page.locator(".sp-open").click()
            page.locator("#sp-filters.open").wait_for(timeout=3000)
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
            # the results bar is the panel's foot, the size control on the view's row
            assert got["panel"]["t"] < got["rbar"]["t"] and got["rbar"]["b"] <= got["panel"]["b"], got
            assert abs(got["seg"]["t"] - got["size"]["t"]) <= 1, got
            assert got["sizeShown"], got
        else:
            assert len({p["l"] for p in pills}) == 3, pills
            assert len({p["t"] for p in pills}) == 2, pills
            assert mode["t"] >= pills[5]["b"] and mode["r"] - mode["l"] >= pills[5]["r"] - pills[0]["l"] - 1
            assert not got["sizeShown"] and got["sheetSize"], got
        assert errors == []
        browser.close()
