"""The card search's filters in headless Chromium: typing a name updates the tiles in place (no
page load) and the address follows; a type picked from the list becomes a chip that narrows the
results and flips between IS and NOT; the grid loads its next page on its own when scrolled; the
list view and the sort are part of the search; Clear filters empties everything. On a phone the
filters open as a full-screen panel that "Show cards" closes. At 360, 720 (Fold) and 1280 px,
light and dark: nothing scrolls sideways, every control is uncovered, and the picker's text and
the chips keep a 4.5:1 contrast. Set MTG_CARD_SEARCH_SHOTS=<dir> to also save screenshots.
Skipped without a Chromium build."""

from __future__ import annotations

import copy
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest

from .test_cardsearch_browser import SIZES, _chromium, _page
from .test_editor_browser import _link
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_CARD_SEARCH_SHOTS")

# Every visible control inside the form (or the open panel) inside the window and uncovered.
CONTROLS = """
(sel) => {
  const vw = document.documentElement.clientWidth, out = [];
  const root = document.querySelector(sel);
  for (const c of root.querySelectorAll('button, input:not([type=hidden]), select:not(.msel-native), summary, a')) {
    if (c.checkVisibility && !c.checkVisibility({visibilityProperty: true, opacityProperty: true})) continue;
    if (!c.getClientRects().length) continue;
    c.scrollIntoView({block: 'center', inline: 'nearest'});   // under a sticky head or foot only while scrolled
    const b = c.getBoundingClientRect();
    if (!(b.width > 0 && b.height > 0)) continue;
    const label = c.id || c.name || c.innerText.slice(0, 20) || c.className;
    if (b.left < -1 || b.right > vw + 1) out.push(label + ' outside the window');
    const x = b.left + b.width / 2, y = b.top + Math.min(b.height / 2, 12);
    const top = document.elementFromPoint(x, y);
    const own = c.closest('label');
    if (top && !(top === c || c.contains(top) || (own && own.contains(top))))
      out.push(label + ' is covered by ' + top.tagName + '.' + top.className);
  }
  window.scrollTo(0, 0);
  return out;
}
"""

# Contrast of text against the nearest background with a colour, by WCAG's formula.
CONTRAST = """
(sel) => {
  function rgb(s) { const m = s.match(/[\\d.]+/g); return m ? m.map(Number) : [0, 0, 0, 0]; }
  function lum(c) {
    const v = c.slice(0, 3).map(x => { x /= 255; return x <= 0.03928 ? x / 12.92 : Math.pow((x + 0.055) / 1.055, 2.4); });
    return 0.2126 * v[0] + 0.7152 * v[1] + 0.0722 * v[2];
  }
  function bg(el) {
    for (let e = el; e; e = e.parentElement) {
      const c = rgb(getComputedStyle(e).backgroundColor);
      if (c.length < 4 || c[3] > 0.9) return c;
    }
    return rgb(getComputedStyle(document.body).backgroundColor);
  }
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (!el.getBoundingClientRect().width || !el.innerText.trim()) continue;
    const f = lum(rgb(getComputedStyle(el).color)), b = lum(bg(el));
    const ratio = (Math.max(f, b) + 0.05) / (Math.min(f, b) + 0.05);
    if (ratio < 4.5) out.push(el.innerText.trim().slice(0, 24) + ' ' + ratio.toFixed(2));
  }
  return out;
}
"""


def _lands(n: int, base: dict) -> list[dict]:
    out = []
    for i in range(n):
        c = copy.deepcopy(base)
        c["id"] = f"00000000-0000-4000-8000-{i:012d}"
        c["name"] = f"Test Land {i:02d}"
        c["collector_number"] = str(100 + i)
        out.append(c)
    return out


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    tower = next(c for c in s.sf.cards if c["name"] == "Command Tower")
    s.sf.extra_cards = _lands(30, tower)
    s.sf.page_size = 12
    object.__setattr__(s.app.state.gateway.settings, "writes_enabled", True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _query(url: str) -> dict[str, list[str]]:
    return parse_qs(urlparse(url).query)


def _names(page) -> list[str]:
    return page.locator("#cardgrid .cc .nm > span:first-child, #cardgrid .nmb").all_inner_texts()


def _wait_count(page, text: str) -> None:
    page.wait_for_function(
        "(t) => (document.getElementById('cs-count') || {}).textContent === t", arg=text, timeout=5000
    )


def test_typing_updates_the_tiles_in_place_and_the_address_follows(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/cards", wait_until="networkidle")
        page.evaluate("window.__same = 1")
        assert "Find a card" in page.locator("#cs-results").inner_text()
        page.locator("#c-q").press_sequentially("sol", delay=20)
        _wait_count(page, "2 cards")
        names = _names(page)
        assert names == ["Sol Ring", "Sol Talisman"], names
        assert page.evaluate("window.__same") == 1  # the tiles changed, the page did not load again
        assert _query(page.url) == {"q": ["sol"]}, page.url
        # one search for the whole word, not one per letter (the 400 ms pause)
        sol = [s for s in server.sf.searches if s.get("q", "").startswith("so")]
        assert len(sol) == 1, sol
        # a name nothing matches says so, and the old tiles go
        page.locator("#c-q").fill("zzzz")
        page.wait_for_function("() => /No card matches/.test(document.getElementById('cs-results').innerText)")
        assert page.locator("#cardgrid .cc").count() == 0
        # the address restores the search on reload
        page.locator("#c-q").fill("sol r")
        _wait_count(page, "1 card")
        page.reload(wait_until="networkidle")
        assert page.locator("#c-q").input_value() == "sol r"
        assert _names(page) == ["Sol Ring"]
        assert errors == [], errors
        browser.close()


def test_a_type_chip_narrows_and_flips_to_not_and_clear_empties_everything(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "dark", exe)
        page.goto(f"{server.base}/cards?q=mountain", wait_until="networkidle")
        _wait_count(page, "2 cards")
        box = page.locator("#p-t")
        box.click()
        box.press_sequentially("crea", delay=20)
        opt = page.locator("#o-t li.opt", has_text="Creature").first
        opt.wait_for(timeout=5000)
        opt.click()
        chip = page.locator("[data-picker=t] .chips li")
        assert chip.count() == 1 and "Creature" in chip.inner_text()
        _wait_count(page, "1 card")
        assert _names(page) == ["Mountain Goat"]
        assert _query(page.url)["t"] == ["creature"], page.url
        # IS becomes NOT: every Mountain that is not a creature
        chip.locator(".pol").click()
        assert chip.locator(".pol").inner_text() == "NOT"
        # the count stays "1 card", so wait for the tile itself to change
        page.wait_for_function(
            "() => [...document.querySelectorAll('#cardgrid .cc .nm > span:first-child')]"
            ".map(e => e.textContent).join('|') === 'Mountain'",
            timeout=5000,
        )
        assert _names(page) == ["Mountain"]
        assert _query(page.url)["t"] == ["-creature"], page.url
        # a word the list does not have is offered as typed
        box.click()
        box.press_sequentially("goat", delay=20)
        page.locator("#o-t li.opt", has_text="Use “goat”").wait_for(timeout=3000)
        box.press("Escape")
        box.fill("")
        # Clear filters empties the chips, the name and the results
        page.locator(".cs-actions .cs-clear").click()
        assert page.locator("[data-picker=t] .chips li").count() == 0
        assert page.locator("#c-q").input_value() == ""
        page.wait_for_function("() => /Find a card/.test(document.getElementById('cs-results').innerText)")
        assert urlparse(page.url).path == "/cards" and not urlparse(page.url).query, page.url
        assert errors == [], errors
        browser.close()


def test_scrolling_loads_the_next_page_and_the_list_view_and_sort_stay_in_the_search(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/cards?q=test+land", wait_until="networkidle")
        _wait_count(page, "30 cards")
        for _ in range(12):
            if page.locator("#cardgrid .cc").count() >= 30:
                break
            page.mouse.wheel(0, 4000)
            page.wait_for_timeout(250)
        names = _names(page)
        assert names == [f"Test Land {i:02d}" for i in range(30)], names
        assert page.locator("#cs-more [data-more]").is_hidden()
        # the list view: rows, chosen in the results head, kept in a cookie for the next visit
        page.locator(".seg label[title=List]").click()
        page.wait_for_selector("#cardgrid.cardlist", timeout=5000)
        assert page.evaluate(MEASURE) == []
        page.goto(f"{server.base}/cards?q=sol", wait_until="networkidle")
        assert page.locator("#cardgrid").get_attribute("class") == "cardlist"
        # a sort change runs the search again with that order
        before = len(server.sf.searches)
        page.locator("#f-order").select_option("cmc")
        page.wait_for_function("() => location.search.includes('order=cmc')", timeout=5000)
        page.wait_for_load_state("networkidle")
        assert any(s.get("order") == "cmc" for s in server.sf.searches[before:]), server.sf.searches[before:]
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_filters_fit_every_width_and_the_phone_panel_opens_and_closes(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/cards?q=sol&r=r&t=artifact", wait_until="networkidle")
        _wait_count(page, "1 card")
        assert page.evaluate(MEASURE) == []
        if width < 600:
            # the filters wait behind a button that counts the ones in use
            assert page.locator("#cs-filters").is_hidden() or not page.locator("#p-t").is_visible()
            assert page.locator(".cs-open .n").inner_text().strip() == "2"
            page.locator(".cs-open").click()
            panel = page.locator("#cs-filters.open")
            panel.wait_for(timeout=3000)
            box = panel.bounding_box()
            assert box and box["width"] >= width - 1 and box["x"] <= 1, box
            assert page.evaluate(CONTROLS, "#cs-filters.open") == []
            assert page.evaluate(MEASURE) == []
            if SHOTS:
                page.screenshot(path=str(Path(SHOTS) / f"filters-panel-{width}-{theme}.png"))
            # the panel's button names the count and closes it
            page.locator(".cs-show").click()
            page.wait_for_function("() => !document.getElementById('cs-filters').classList.contains('open')")
            assert page.evaluate("document.activeElement.classList.contains('cs-open')")
            # Escape closes it too
            page.locator(".cs-open").click()
            panel.wait_for(timeout=3000)
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.getElementById('cs-filters').classList.contains('open')")
        else:
            assert page.locator("#p-t").is_visible()
            assert page.evaluate(CONTROLS, "#cs-form") == []
        # the open More filters fits as well
        if width < 600:
            page.locator(".cs-open").click()
        page.locator(".cs-more summary").click()
        page.locator("#f-o").wait_for(state="visible", timeout=3000)
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, ".cs .chips li .nm, .cs label, .cs legend, .cs .hint, .cs summary") == []
        # the type list's options are readable in both themes
        page.locator("#p-t").click()
        page.locator("#o-t li.opt").first.wait_for(timeout=5000)
        assert page.evaluate(CONTRAST, "#o-t li.opt span") == []
        assert page.evaluate(MEASURE) == []
        if SHOTS:
            page.screenshot(path=str(Path(SHOTS) / f"filters-{width}-{theme}.png"), full_page=width >= 600)
        page.keyboard.press("Escape")
        if width < 600:
            page.keyboard.press("Escape")
        if SHOTS:
            page.locator("#cs-results").scroll_into_view_if_needed()
            page.screenshot(path=str(Path(SHOTS) / f"results-{width}-{theme}.png"))
        assert errors == [], errors
        browser.close()


def test_a_printing_tile_adds_that_printing(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/cards?q=sol+ring&prints=1", wait_until="networkidle")
        page.wait_for_selector("#cardgrid[data-printing]", timeout=5000)
        first = page.locator("#cardgrid button[data-add]").first
        code, number = first.get_attribute("data-set-code"), first.get_attribute("data-number")
        assert code and number
        first.click()
        sheet = page.locator(".addsheet.open")
        sheet.wait_for(timeout=3000)
        note = sheet.locator(".note").inner_text()
        assert f"This printing: {code.upper()} #{number}." in note, note
        assert errors == [], errors
        browser.close()
