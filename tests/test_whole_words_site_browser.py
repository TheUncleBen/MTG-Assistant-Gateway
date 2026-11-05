"""Every page the overflow sweep and the text-zoom tests know (and the Account details list),
with their seeded data and open states (every fold opened, the glance tiles shown, each dropdown
menu opened in turn), drawn in headless Chromium at 320, 360, 412, 600, 720, 900, 1024, 1280 and
1440 px, at 100% text, at a 28 px root (ROOT_200), with every font doubled (TEXT_200) and with
the browser's own 16 px default doubled (BROWSER_200): no word anywhere on the page (a heading,
a pill, a link, a label, a table cell, a dt or dd, a menu item…) is drawn on more than one line
where it would have fitted the box around it. The box is the nearest card, panel, tile, list
item, menu or the main column, less the padding of what lies between: a table column, a key
column of a dl or a label squeezed below its longest word breaks the word although the card has
room for it, which is the defect (final polish batch 10, AB1 to AB7: "Archidek/t" in a 22 px
Activity column, "Conditi/on" in a help table's header, a 26 px account details column,
"Assistan/t" in a "Steps for:" picker, "Preconstruct/ed" in a heading and a link, "An assistan/t"
in a 120 px Proposed by column, a "Not configur/ed" pill; and what the crawl found besides: the
Admin overview's controls and the Metrics table, the deck table view and the deck tiles, the
proposals list, the guide's topic column, the install steps' column, the report's curve labels,
the home page's connect line, the account header, the Binder grid's names). A heading may shrink
with the viewport at a larger root, so a heading's word counts as fitting when it would fit at
the body's own text size, except with every font doubled in place (TEXT_200 pins each font in px,
so no heading can shrink). A word wider than its box has nowhere else to go and is not counted;
nor is a name seeded without any break opportunity (forty letters and more), which breaks
anywhere by design, nor the longest card name there is (31 letters, hyphenated by the Binder's
list by design), nor a word in a code box. Sharded by text size and page group so the crawl
runs in parallel (about four minutes on four workers)."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

from .test_cardsearch_browser import _chromium
from .test_editor_browser import PNG
from .test_large_text_folds_browser import _zoom
from .test_overflow_sweep_browser import (
    FOLDERS,
    LONG64,
    LONG_CATS,
    LONGUSER,
    SHEETS_LOADED,
    UNBROKEN,
    UNBROKEN_2,
    _pages,
    _seed,
    server,  # noqa: F401
)
from .test_suggest_browser import Server
from .test_text_zoom_browser import BROWSER_200_PAGES

pytest.importorskip("playwright")

ZOOMS = ["none", "root", "text", "browser"]
PHONE_WIDTHS = [320, 360, 412, 600, 720]
DESKTOP_WIDTHS = [900, 1024, 1280, 1440]
SHARDS = 4

# the names seeded without a break opportunity: they break anywhere by design; and the longest
# card name there is (31 letters), which the Binder's dense list hyphenates by design
# (hyphens:auto) and its picture box can never hold
LONGEST_CARD = "Asmoranomardicadaistinaculdacar"
HOSTILE = [LONGUSER, UNBROKEN, UNBROKEN_2, FOLDERS[1], LONG64, LONG_CATS[0], LONGEST_CARD]

# the boxes a word is judged against: the nearest one around it must have had room for the word
BOXES = (
    "main, li, .card, .ui-card, .panel, .tile, .ndtile, .wdg, .wcard, details.dd > .menu, .picker, aside, "
    "nav, .tabbar, [role=dialog], .mtgbox, header, footer, .toast, .strip, .notice, .modal, .sheet"
)


def _site(srv: Server, pending: str) -> list[str]:
    """Every page the overflow sweep and the text-zoom tests crawl, each once."""
    out: list[str] = []
    more = [f"/proposals/{pending}", "/decks/44", "/decks/46", "/account/details"]
    for path in [*_pages(srv, pending), *BROWSER_200_PAGES, *more]:
        if path not in out:
            out.append(path)
    return out


# opens every fold and shows the glance tiles, as the sweeps do before measuring
OPEN_ALL = """() => {
  for (const d of document.querySelectorAll('details:not(.dd)')) d.open = true;
  for (const c of document.querySelectorAll('.glance-tgl')) c.checked = true;
}"""

# every word (a run without spaces, hyphens or dashes) in the text under the elements `rootSel`
# matches that is drawn on more than one line although it would have fitted its box: the nearest
# `boxSel` ancestor's content width less the padding, borders and margins of the elements between
# (what the word could have had, had no sibling column taken any of it), each word once per kind
# of element, with the widths. A heading's word is scaled to the body's text size first when
# `shrink` is set (a heading may follow the viewport at a larger root; with every font doubled in
# place it cannot). The browser's own hyphenation is turned off while measuring, so a word broken
# at a hyphen of the browser's own is not taken for whole
WORDS_FN = """([rootSel, boxSel, hostile, shrink]) => {
  const off = document.createElement('style');
  off.textContent = '* { hyphens: manual !important; -webkit-hyphens: manual !important; }';
  document.head.appendChild(off);
  const content = (el) => {
    const cs = getComputedStyle(el);
    return el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  };
  const body = parseFloat(getComputedStyle(document.body).fontSize);
  const name = (el) => el.tagName.toLowerCase() + (typeof el.className === 'string' && el.className.trim()
    ? '.' + el.className.trim().split(/\\s+/).slice(0, 2).join('.') : '');
  const out = [], seen = new Set(), skip = new Set(hostile);
  for (const root of document.querySelectorAll(rootSel)) {
    const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      const p = n.parentElement;
      if (!p || !n.textContent.trim() || !p.getClientRects().length) continue;
      const skipSel = 'code,pre,kbd,script,style,noscript,textarea,option,svg,.sr-only,[hidden],' +
        '[aria-hidden=true]';
      if (p.closest(skipSel)) continue;
      const cs = getComputedStyle(p);
      if (cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) continue;
      const box = p.closest(boxSel) || document.body;
      let room = content(box);
      for (let e = p; e && e !== box; e = e.parentElement) {
        const es = getComputedStyle(e);
        for (const k of ['paddingLeft', 'paddingRight', 'borderLeftWidth', 'borderRightWidth', 'marginLeft',
            'marginRight']) room -= parseFloat(es[k]) || 0;
      }
      const font = parseFloat(cs.fontSize);
      const scale = shrink && p.closest('h1,h2,h3,.ui-head') && font > body ? body / font : 1;
      const re = /[^\\s\\-\u2013\u2014]+/g; let m;
      while ((m = re.exec(n.textContent))) {
        if (m[0].length < 2 || skip.has(m[0])) continue;
        const q = document.createRange(); q.setStart(n, m.index); q.setEnd(n, m.index + m[0].length);
        const rs = [...q.getClientRects()].filter(r => r.width > 0);
        if (rs.length < 2 || Math.abs(rs[0].top - rs[rs.length - 1].top) <= 2) continue;
        const whole = rs.reduce((t, r) => t + r.width, 0);
        if (whole * scale > room) continue;
        const key = name(p) + '|' + m[0];
        if (seen.has(key)) continue;
        seen.add(key);
        out.push('"' + m[0] + '" ' + Math.round(whole) + ' of ' + Math.round(room) + 'px in ' + name(p) +
          ' (box ' + name(box) + ')');
      }
    }
  }
  off.remove();
  return out;
}"""

WORDS = WORDS_FN

# opens each dropdown menu in turn (its summary's click runs the script's placement), measures
# the words of its panel, and closes it again
MENU_WORDS = (
    """async ([boxSel, hostile, shrink]) => {
  const words = """
    + WORDS_FN
    + """;
  const out = [];
  for (const d of document.querySelectorAll('details.dd')) {
    const sm = d.querySelector(':scope > summary');
    if (!sm || !sm.getClientRects().length || !d.querySelector(':scope > .menu')) continue;
    sm.click();
    await new Promise(r => setTimeout(r, 0));
    if (!d.open) d.open = true;
    const label = (sm.getAttribute('aria-label') || sm.innerText || d.className).trim().slice(0, 24);
    for (const f of words(['details.dd[open] > .menu', boxSel, hostile, shrink]))
      out.push(f + ' [menu of ' + label + ']');
    sm.click();
    await new Promise(r => setTimeout(r, 0));
    if (d.open) d.removeAttribute('open');
  }
  return out;
}"""
)


@pytest.mark.parametrize("shard", range(SHARDS))
@pytest.mark.parametrize("zoom", ZOOMS)
def test_no_word_on_any_page_breaks_where_it_would_have_fitted(server: Server, zoom: str, shard: int) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, pending = _seed(server)
    pages = _site(server, pending)[shard::SHARDS]
    problems: list[str] = []
    errors: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for path in pages:
            for touch, widths in ((True, PHONE_WIDTHS), (False, DESKTOP_WIDTHS)):
                ctx = browser.new_context(viewport={"width": widths[0], "height": 860}, has_touch=touch)
                ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
                ctx.route(
                    re.compile(r"https://cards\.scryfall\.io/.*"),
                    lambda r: r.fulfill(content_type="image/png", body=PNG),
                )
                # a deck view's long-poll would hold "networkidle" (see the overflow sweep)
                ctx.route(re.compile(r"/decks/[^/]+/check(\?|$)"), lambda r: r.abort())
                page = ctx.new_page()
                page.on("pageerror", lambda e: errors.append(str(e)))
                server.db.delete_prefs("user-1", prefixes=("view.",))
                r = page.goto(f"{server.base}{path}", wait_until="networkidle")
                assert r is not None and r.status == 200, (path, r and r.status)
                page.wait_for_function(SHEETS_LOADED, timeout=10_000)
                _zoom(page, zoom)
                page.evaluate(OPEN_ALL)
                for w in widths:
                    page.set_viewport_size({"width": w, "height": 860})
                    page.wait_for_timeout(60)
                    found = page.evaluate(WORDS, ["body", BOXES, HOSTILE, zoom != "text"])
                    found += page.evaluate(MENU_WORDS, [BOXES, HOSTILE, zoom != "text"])
                    problems += [f"{path} @{w}px {zoom}: {f}" for f in found]
                ctx.close()
        browser.close()
    if os.environ.get("SWEEP_OUT"):
        Path(f"{os.environ['SWEEP_OUT']}-{zoom}-{shard}.txt").write_text("\n".join(problems))
    assert errors == []
    more = f"\n… {len(problems)} in all" if len(problems) > 80 else ""
    assert problems == [], "\n".join(problems[:80]) + more
