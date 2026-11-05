"""The final polish's batch 8 (Z1 to Z5), measured in headless Chromium: five layouts whose
columns were sized in rem beside a fluid one, so at 200% text the fluid column got the scraps and
its words broke inside themselves. Each page is drawn at 360, 412, 720, 900, 1024, 1280 and
1440 px, at 100% text, at a 28 px root (ROOT_200) and with every font doubled (TEXT_200): no
word breaks in two where it would have fitted its column, nothing is clipped or scrolls sideways,
no two controls overlap (MEASURE), every control is a 44 px target and no control covers a
label. Z1: the New deck box's Deck name beside an 11 rem Format. Z2: Admin › Users' lined-up
columns. Z3: Admin › Settings' label beside a 16 rem control. Z4: the skill page's Download
button, breaking "Downlo/ad" under the steps' overflow-wrap. Z5: the report's Deck tile, its
"$310.00" and "100 cards" broken over the lines."""

from __future__ import annotations

import re

import pytest

from .test_cardsearch_browser import _chromium
from .test_editor_browser import PNG
from .test_overflow_sweep_browser import MEASURE, _seed, server  # noqa: F401
from .test_suggest_browser import Server
from .test_text_zoom_browser import BROWSER_200, ROOT_200, TEXT_200

pytest.importorskip("playwright")

WIDTHS = [360, 412, 720, 900, 1024, 1280, 1440]
ZOOMS = ["none", "root", "text"]

# every word (a run without spaces or hyphens: "Deck", "0/120", "$310.00", "(ZIP)"; "low-risk"
# may break after its hyphen) under the elements `sel` matches that is drawn on more than one
# line although it would have fitted the width of its column (the nearest `room` ancestor's
# content box), listed with both widths. A word wider than the column has nowhere else to go and
# is not counted. The browser's own hyphenation is turned off while measuring, so a word broken
# at a hyphen of the browser's own is not taken for whole
BROKEN = """([sel, roomSel]) => {
  const off = document.createElement('style');
  off.textContent = '* { hyphens: manual !important; -webkit-hyphens: manual !important; }';
  document.head.appendChild(off);
  const content = (el) => {
    const cs = getComputedStyle(el);
    return el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  };
  const out = [];
  for (const root of document.querySelectorAll(sel)) {
    const rr = root.getBoundingClientRect();
    if (!(rr.width > 0 && rr.height > 0) || root.closest('[hidden],[aria-hidden=true]')) continue;
    const col = root.closest(roomSel);
    if (!col) continue;
    const room = content(col);
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (!n.textContent.trim() || !n.parentElement.getClientRects().length) continue;
      if (n.parentElement.closest('.sr-only')) continue;
      const re = /[^\\s-]+/g; let m;
      while ((m = re.exec(n.textContent))) {
        const q = document.createRange(); q.setStart(n, m.index); q.setEnd(n, m.index + m[0].length);
        const rs = [...q.getClientRects()].filter(r => r.width > 0);
        if (rs.length < 2 || Math.abs(rs[0].top - rs[rs.length - 1].top) <= 2) continue;
        const whole = rs.reduce((t, r) => t + r.width, 0);
        if (whole <= room)
          out.push(m[0] + ' (' + Math.round(whole) + ' of ' + Math.round(room) + 'px)');
      }
    }
  }
  off.remove();
  return out;
}"""

# every shown control under `sel` that is not 44 px tall, by its words
SHORT = """(sel) => [...document.querySelectorAll(sel)]
  .filter(e => e.getClientRects().length && !e.closest('[hidden],[aria-hidden=true]') && e.type !== 'hidden')
  .filter(e => !e.matches('select[aria-hidden=true], .msel-native'))
  .filter(e => { const cs = getComputedStyle(e); return cs.visibility !== 'hidden' && cs.opacity !== '0'; })
  .filter(e => e.getBoundingClientRect().height < 44)
  .map(e => (e.getAttribute('aria-label') || e.innerText || e.value || e.tagName).trim().slice(0, 30)
    + ' ' + Math.round(e.getBoundingClientRect().height) + 'px')"""


def _zoom(page, zoom: str) -> None:  # noqa: ANN001
    if zoom != "none":
        page.evaluate({"root": ROOT_200, "text": TEXT_200, "browser": BROWSER_200}[zoom])
        page.wait_for_timeout(150)


def _contexts(p, srv: Server, sid: str, theme: str, exe: str | None):  # noqa: ANN001
    """One browser; a context per width (a phone below 900 px), the page and its script errors."""
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    errors: list[str] = []

    def open_at(width: int):  # noqa: ANN202
        ctx = browser.new_context(viewport={"width": width, "height": 860}, has_touch=width < 900)
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda r: r.fulfill(content_type="image/png", body=PNG),
        )
        ctx.add_cookies(
            [
                {"name": "mtg_session", "value": sid, "url": srv.base},
                {"name": "mtg_theme", "value": theme, "url": srv.base},
            ]
        )
        page = ctx.new_page()
        page.on("pageerror", lambda e: errors.append(str(e)))
        return ctx, page

    return browser, errors, open_at


# the New deck box: the Deck name box's width and font, its count's lines, and the box's controls
NEW_DECK = """() => {
  const box = document.getElementById('newdeck');
  const name = box.querySelector('#name');
  const lines = (el) => { const q = document.createRange(); q.selectNodeContents(el);
    return new Set([...q.getClientRects()].filter(r => r.width > 0).map(r => Math.round(r.top))).size; };
  return {hidden: box.hidden, name: Math.round(name.getBoundingClientRect().width),
    font: parseFloat(getComputedStyle(name).fontSize), count: lines(box.querySelector('.nd-cnt')),
    fields: Math.round(box.querySelector('.nd-fields').getBoundingClientRect().width)};
}"""


@pytest.mark.parametrize("zoom", ZOOMS)
def test_the_new_deck_box_stacks_its_fields_when_the_text_is_large(server: Server, zoom: str) -> None:  # noqa: F811
    """Z1: at 900 and 1024 px with 200% text the fields column kept Format's 11 rem and the
    Deck name box got about 70 px: "Deck name" one letter a line, "0/120" one character a line,
    "Folde/r" broken. At every width and text size the Deck name box is at least 8 em wide
    (its own em), its count is one line, no label or word breaks in two, every control is 44 px
    tall and nothing overlaps, is clipped or scrolls sideways."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, errors, open_at = _contexts(p, server, sid, "light", exe)
        for width in WIDTHS:
            ctx, page = open_at(width)
            page.goto(f"{server.base}/decks/new", wait_until="networkidle")
            _zoom(page, zoom)
            got = page.evaluate(NEW_DECK)
            where = (width, zoom, got)
            assert not got["hidden"], where
            assert got["name"] >= 8 * got["font"], where
            assert got["count"] == 1, where
            broken = page.evaluate(
                BROKEN, ["#newdeck .nd-form label, #newdeck .nd-cnt, #newdeck .nd-cards > .lbl", ".nd-fields"]
            )
            assert broken == [], (width, zoom, broken)
            short = page.evaluate(
                SHORT, "#newdeck input:not([type=checkbox]), #newdeck select, #newdeck button"
            )
            assert short == [], (width, zoom, short)
            assert page.evaluate(MEASURE) == [], (width, zoom)
            ctx.close()
        assert errors == []
        browser.close()
