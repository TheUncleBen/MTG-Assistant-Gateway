"""Every page's Filters sheet at a phone's 320 px with 200% text (polish AL1, AL2 on 043ea694):
each control stays inside the sheet (Add a format was 309 px wide in a 256 px column, its right
edge cut off), and a one-line control grows with its text as a text box does (the Rarity box
stayed 44 px while the boxes beside it grew). Two kinds of 200%: the browser's (the root font
size) and Android's text scaling (every text twice its size, as the site crawl's "text" zoom)."""

from __future__ import annotations

import pytest

from .test_cardsearch_browser import _chromium
from .test_overflow_sweep_browser import _seed, server  # noqa: F401
from .test_suggest_browser import Server

pytest.importorskip("playwright")

PAGES = ["/cards?q=s", "/search?name=a", "/precons", "/decks", "/collection"]
_SID: dict[int, str] = {}

ZOOM = """(zoom) => {
  const html = document.documentElement;
  if (zoom === 'browser') { html.style.setProperty('font-size', '200%', 'important'); return; }
  const els = [html, ...document.querySelectorAll('body, body *')];
  const fs = els.map(el => parseFloat(getComputedStyle(el).fontSize));
  els.forEach((el, i) => { if (fs[i]) el.style.setProperty('font-size', fs[i] * 2 + 'px', 'important'); });
}"""

# every shown control of the open sheet: one that runs out of the sheet's box, and a one-line
# control (a button, a drop-down, a pick-several list's button, a text box) lower than its text
# with .3em above and below, as a text box is
MEASURE = """() => {
  const sheet = document.querySelector('#sp-filters');
  const sr = sheet.getBoundingClientRect(), out = [];
  const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden'
    && getComputedStyle(e).opacity !== '0';
  const sel = 'button, summary, select:not(.msel-native), a.btn, input:not([type=checkbox]):not([type=radio])'
    + ':not([type=hidden])';
  for (const el of sheet.querySelectorAll(sel)) {
    if (!shown(el)) continue;
    const r = el.getBoundingClientRect(), cs = getComputedStyle(el), fs = parseFloat(cs.fontSize);
    const name = el.tagName.toLowerCase() + '.' + [...el.classList].join('.') + ' "' +
      (el.textContent || el.placeholder || el.name || '').trim().slice(0, 30) + '"';
    if (r.left < sr.left - 1 || r.right > sr.right + 1)
      out.push(name + ' runs from ' + Math.round(r.left) + ' to ' + Math.round(r.right) +
        ' in a sheet from ' + Math.round(sr.left) + ' to ' + Math.round(sr.right));
    if (el.matches('.close, .x, .rm, [aria-label^="Close"]')) continue;  // square icon buttons
    if (r.height < fs * 1.8 - 1)
      out.push(name + ' is ' + Math.round(r.height) + 'px tall for its ' + Math.round(fs) + 'px text');
  }
  return out;
}"""


@pytest.mark.parametrize("zoom", ["browser", "text"])
@pytest.mark.parametrize("path", PAGES)
def test_the_filters_sheet_holds_its_controls_at_200_percent_text(
    server: Server,  # noqa: F811
    path: str,
    zoom: str,
) -> None:
    from playwright.sync_api import sync_playwright

    if id(server) not in _SID:
        _SID[id(server)] = _seed(server)[0]
    sid = _SID[id(server)]
    exe = _chromium()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 320, "height": 860}, has_touch=True, is_mobile=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route("https://cards.scryfall.io/**", lambda r: r.abort())
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(server.base + path, wait_until="networkidle")
        opener = page.locator(".spanel .sp-open")
        if not opener.count() or not opener.first.is_visible():
            browser.close()
            pytest.skip(f"{path} shows no Filters button at 320 px")
        opener.first.click()
        page.locator("#sp-filters.open").wait_for(timeout=3000)
        page.evaluate(ZOOM, zoom)
        # the new sizes laid out: two frames after the change
        page.evaluate("() => new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)))")
        found = page.evaluate(MEASURE)
        assert not found, "\n".join(found)
        assert errors == [], errors
        browser.close()
