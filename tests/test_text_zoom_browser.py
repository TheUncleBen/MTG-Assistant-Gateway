"""Large text. With the text at twice its size (the root font size at 200%, and
every element's own font size doubled, as a phone's font scale or a browser's text-only zoom
does it) the main pages at phone widths (320 and 360 px) and on a Fold's inner screen (673 px) must not
scroll sideways, clip a control or let two controls overlap, and every dropdown menu still opens
inside the window. The same checks as the overflow sweep (test_overflow_sweep_browser), on
the same hostile data. Skipped without a Chromium build."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

from mtg_gateway import timing

from .test_editor_browser import PNG
from .test_overflow_sweep_browser import FOLDERS, MEASURE, MENUS, _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

WIDTHS = [320, 360, 673, 717]

# The pages a member uses most, plus the ones with forms and tables.
PAGES = [
    "/",
    "/decks",
    "/decks/42",
    "/decks/42?view=grid",
    "/decks/42?view=table",
    "/decks/50",
    "/decks/42/edit",
    "/decks/42/settings",
    "/decks/new",
    "/decks/42/export",
    "/decks/42/compare",
    f"/decks?folder={FOLDERS[0]}",
    "/collection?view=grid",
    "/collection?view=list",
    "/search",
    "/precons",
    "/users/alice",
    "/history",
    "/history/reports/r1",
    "/activity",
    "/proposals",
    "/account",
    "/guide",
    "/guide/how-signout",
    "/guide/ref-tools",
    "/guide/exp-approvals",
    "/scan",
    "/app",
    "/install",
    "/admin",
    "/collection/lists",
    "/lists/btags",
    "/watchlist",
    "/notifications",
    "/forum",
]

# Root font size at 200% (the pages' own 14px doubled): everything sized in rem doubles.
ROOT_200 = "() => { document.documentElement.style.setProperty('font-size', '28px', 'important'); }"

# Text-only zoom: every element's computed font size doubled (read all first, then set), the way
# Android's font scale (WebView textZoom) and a browser's text-only zoom enlarge text in px too.
TEXT_200 = """
() => {
  const els = [document.documentElement, ...document.querySelectorAll('body, body *')];
  const sizes = els.map(el => parseFloat(getComputedStyle(el).fontSize));
  els.forEach((el, i) => {
    if (sizes[i]) el.style.setProperty('font-size', (sizes[i] * 2) + 'px', 'important');
  });
}
"""


@pytest.mark.parametrize("zoom", ["root", "text"])
def test_main_pages_hold_together_at_200_percent_text(server: Server, zoom: str) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    # Admin's Speed card with slow pages under their longest address patterns: the card's labels
    # must wrap at 200% text on a phone (a loaded run used to leave such rows behind by chance)
    now = time.time()
    for route in ("/history/reports/{rid}/head-to-head", "/decks/api/decks/{deck_id}/delete-info"):
        timing.speed_log.add(timing.PageTime(now, route, 12.5, 12.0, 11.0, 0, 200, 0.4))
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 360, "height": 800}, has_touch=True, is_mobile=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for path in [*PAGES, f"/proposals/{pending}"]:
            for w in WIDTHS:
                ctx.clear_cookies(name=re.compile(r"^mtg_view_"))
                server.db.delete_prefs("user-1", prefixes=("view.",))  # on the account from 0.7.29
                page.set_viewport_size({"width": w, "height": 800})
                r = page.goto(f"{server.base}{path}", wait_until="networkidle")
                assert r is not None and r.status == 200, (path, r and r.status)
                page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
                page.wait_for_timeout(30)
                found = page.evaluate(MEASURE) + page.evaluate(MENUS)
                problems += [f"{path} @{w}px {zoom}: {f}" for f in found]
        browser.close()
    if os.environ.get("SWEEP_OUT"):
        Path(os.environ["SWEEP_OUT"]).write_text("\n".join(problems))
    assert errors == []
    more = f"\n… {len(problems)} in all" if len(problems) > 80 else ""
    assert problems == [], "\n".join(problems[:80]) + more


# The Scan page's preview: the box keeps its 3:4 shape and the dashed card guide (with the card
# name box at its top) stays inside it, with and without a result. The page is one column at
# every width and text size.
SCAN_PREVIEW = """
() => {
  const panel = document.querySelector('.scan .cam-panel'), cam = panel.querySelector('.camera');
  const guide = cam.querySelector('.guide');
  const out = [];
  for (const result of [false, true]) {
    panel.classList.toggle('has-result', result);
    const c = cam.getBoundingClientRect(), g = guide.getBoundingClientRect();
    out.push({result, cols: getComputedStyle(panel).display, ratio: c.width / c.height,
              inside: g.top >= c.top - 1 && g.bottom <= c.bottom + 1 && g.left >= c.left - 1
                      && g.right <= c.right + 1});
  }
  panel.classList.remove('has-result');
  return out;
}
"""


@pytest.mark.parametrize("touch", [True, False], ids=["touch", "mouse"])
def test_the_scan_preview_keeps_its_shape_and_the_card_guide_inside_it(
    server: Server,  # noqa: F811
    touch: bool,
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, _ = _seed(server)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 360, "height": 800}, has_touch=touch, is_mobile=touch)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        for zoom in ("none", "root", "text"):
            for w in (360, 620, 673, 699, 700, 717, 900, 1280):
                page.set_viewport_size({"width": w, "height": 800})
                page.goto(f"{server.base}/scan", wait_until="networkidle")
                if zoom != "none":
                    page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
                page.wait_for_timeout(30)
                for m in page.evaluate(SCAN_PREVIEW):
                    where = f"{w}px {zoom} {'with' if m['result'] else 'without'} a result"
                    if m["cols"] == "grid":
                        problems.append(f"{where}: {m['cols']}, expected one column")
                    if abs(m["ratio"] - 0.75) > 0.01:
                        problems.append(f"{where}: preview ratio {m['ratio']:.2f}, not 3:4")
                    if not m["inside"]:
                        problems.append(f"{where}: the card guide runs outside the preview")
        browser.close()
    assert problems == [], "\n".join(problems)


# Words that split inside themselves although they would have fitted on a line of their list or
# panel: the headings, the deck and card names and the stat labels of the lists. A word that
# cannot fit its list or panel at all (an unbroken 31-letter card name at 56 px, a panel's
# heading at 42 px on a 320 px phone) is not counted: nothing but a hyphen could help it. The
# headings and names ask the browser to hyphenate (hyphens:auto, where it has a dictionary); the
# measure turns that off first, so a word broken at a hyphen is not mistaken for a split and a
# word that only a hyphen saved from a split counts as one.
WHOLE_WORDS = (
    "h1, h2, ul.rows .name, ul.decklist .name, ul.colllist .name, ul.collgrid .name, .rv-dstats .tile span, "
    ".wdg .lab, .gnav .part, .ui-head"
)
MID_WORD_IN_COLUMN = """
(sel) => {
  const off = document.createElement('style');
  off.textContent = '* { hyphens: manual !important; -webkit-hyphens: manual !important; }';
  document.head.appendChild(off);
  const inner = (el) => {
    const cs = getComputedStyle(el);
    return el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  };
  const columnOf = (el) => {
    const box = el.parentElement && el.parentElement.closest(
      'ul.rows, ul.decklist, ul.colllist, ul.collgrid, .tiles, .ui-widgets, .panel, .card, .ui-card, main');
    return inner(box || document.querySelector('main'));
  };
  const out = [], seen = new Set();
  const name = (el) => el.tagName.toLowerCase() + (el.className && typeof el.className === 'string'
    ? '.' + el.className.split(' ').slice(0, 2).join('.') : '');
  for (const root of document.querySelectorAll(sel)) {
    const rr = root.getBoundingClientRect();
    if (!(rr.width > 0 && rr.height > 0)) continue;
    if (root.closest('[hidden],[aria-hidden=true]')) continue;
    const column = columnOf(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      const p = n.parentElement;
      if (!p || p.closest('code,pre,kbd')) continue;
      const re = /(?<![A-Za-z])[A-Za-z][A-Za-z'’]{3,}(?![A-Za-z])/g;
      let m;
      while ((m = re.exec(n.nodeValue))) {
        const rg = document.createRange();
        rg.setStart(n, m.index); rg.setEnd(n, m.index + m[0].length);
        const rects = [...rg.getClientRects()].filter(x => x.width > 0);
        if (rects.length < 2 || Math.abs(rects[0].top - rects[rects.length - 1].top) <= 2) continue;
        const need = rects.reduce((a, x) => a + x.width, 0);
        if (need > column) continue;
        const key = name(root) + m[0];
        if (seen.has(key)) continue;
        seen.add(key);
        out.push(name(root) + ': "' + m[0] + '" needs ' + Math.round(need) + ' px, the column is ' +
          Math.round(column));
      }
    }
  }
  off.remove();
  return out;
}
"""


def test_names_and_headings_keep_whole_words_at_200_percent_on_narrow_phones(server: Server) -> None:  # noqa: F811
    """With the text at twice its size on a 320 and a 360 px phone, a heading, a deck or card
    name in a list or row, or a stat label wraps between words, never inside one, wherever the
    word would have fitted the page's column ("Mulldrifter" split in a 130 px name cell beside
    an empty mana column, "Description" in a panel's heading)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 360, "height": 800}, has_touch=True, is_mobile=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        for path in [*PAGES, f"/proposals/{pending}", "/decks/44", "/decks/46"]:
            for w in (320, 360):
                ctx.clear_cookies(name=re.compile(r"^mtg_view_"))
                server.db.delete_prefs("user-1", prefixes=("view.",))
                page.set_viewport_size({"width": w, "height": 800})
                r = page.goto(f"{server.base}{path}", wait_until="networkidle")
                assert r is not None and r.status == 200, (path, r and r.status)
                page.evaluate(TEXT_200)
                page.wait_for_timeout(30)
                problems += [f"{path} @{w}px: {f}" for f in page.evaluate(MID_WORD_IN_COLUMN, WHOLE_WORDS)]
        browser.close()
    assert errors == []
    more = f"\n… {len(problems)} in all" if len(problems) > 80 else ""
    assert problems == [], "\n".join(problems[:80]) + more
