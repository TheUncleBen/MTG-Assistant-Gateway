"""Every row list redesigned in 0.7.46 at 200% text on a 375 px phone (the app and the browser),
measured in headless Chromium (review 2, M5 and L11): no word breaks inside itself unless it is
wider than its own box, never one letter per line, a name box at least six letters wide, no row
taller than a screen; the Account hub's rows (Account details, Assistants, Binder › Lists) keep
their icon on the label's line and the chevron at the row's end. Skipped without a Chromium build."""

from __future__ import annotations

import re
import time
from pathlib import Path

import pytest

from .test_assistant_modes_browser import _app
from .test_cardsearch_browser import PNG
from .test_deck_categories import _give_categories
from .test_menus_browser import _exe, _seed_rows, _server
from .test_text_zoom_browser import TEXT_200
from .waits import redrawn, sideways

pytest.importorskip("playwright")

APP_UA = "Mozilla/5.0 (Linux; Android 16; wv) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.7.46"
LONG_LIST = "Trades for the Friday commander night and the big spring tournament"
PAGES = {
    "/watchlist": ".w-t .tr[id^='wr-'], .w-t .tr.wgrp",
    "/proposals": ".pr-table .tr:not(.th):not(.group)",
    "/history": ".hy-table .tr:not(.th):not(.daygroup):not(.group)",
    "/reports": ".rp-t .tr:not(.th):not(.group)",
    "/collection/lists": ".lp-hub .yrow",
    "/account/details": ".ygrp .yrow",
    "/account/assistants": ".ygrp .yrow",
    "/decks/42?view=text": ".deckview .stackhead, .deckview li.row",
}
NAMES = ".t, .t a, a.wcard, .lbl, .gname b, .title, .n .name, .nm"
# each row: its height, and for each name in it the words that wrap inside themselves (a word
# wider than its box may, but never into more lines than it needs: that is one letter per line)
# and the name boxes narrower than six letters
ROWS = r"""
([sel, names]) => {
  const rows = [...document.querySelectorAll(sel)].filter(r => r.getClientRects().length);
  const out = [];
  for (const r of rows) {
    const rb = r.getBoundingClientRect();
    const breaks = [], narrow = [];
    for (const n of r.querySelectorAll(names)) {
      if (!n.getClientRects().length || !n.textContent.trim()) continue;
      const fs = parseFloat(getComputedStyle(n).fontSize);
      const nb = n.getBoundingClientRect();
      const walk = document.createTreeWalker(n, NodeFilter.SHOW_TEXT);
      for (let t = walk.nextNode(); t; t = walk.nextNode()) {
        for (const m of t.textContent.matchAll(/[^\s-]+/g)) {  // a hyphen is a wrap point
          const rg = document.createRange();
          rg.setStart(t, m.index); rg.setEnd(t, m.index + m[0].length);
          const rects = [...rg.getClientRects()].filter(q => q.width > 0);
          const lines = new Set(rects.map(q => Math.round(q.top))).size;
          if (lines < 2) continue;
          const width = rects.reduce((w, q) => w + q.width, 0);
          if (width <= nb.width + 0.5) breaks.push(m[0] + ':' + lines);
          else if (lines > Math.ceil(width / Math.max(nb.width, 1)) + 1)
            breaks.push('LETTERS ' + m[0] + ':' + lines);
        }
      }
      if (nb.width < 6 * fs && n.scrollWidth > nb.width + 1)
        narrow.push(n.className + '=' + Math.round(nb.width / fs * 10) / 10 + 'em');
    }
    const text = r.textContent.replace(/\s+/g, ' ').trim().slice(0, 40);
    out.push({h: Math.round(rb.height), text, breaks, narrow});
  }
  return out;
}
"""
HUB_ROW = r"""
() => [...document.querySelectorAll('.ygrp .rows > .yrow, .ygrp .rows > details.yrow > summary')].map(row => {
  const r = (el) => el && el.getClientRects().length ? el.getBoundingClientRect() : null;
  const icon = r(row.querySelector(':scope > svg.i, :scope > .alogo, :scope > .radio'));
  const lbl = r(row.querySelector('.lbl')), ch = r(row.querySelector(':scope > .ch, :scope > details.dd'));
  const rb = r(row);
  return {text: (row.querySelector('.lbl') || row).textContent.trim().slice(0, 30), h: Math.round(rb.height),
          icon: icon && {top: icon.top, bottom: icon.bottom},
          lbl: lbl && {top: lbl.top, bottom: lbl.bottom, right: lbl.right},
          ch: ch && {left: ch.left, right: ch.right}, right: rb.right, bottom: rb.bottom};
})
"""


def _seed(server) -> str:
    sid = _seed_rows(server)
    _give_categories(server.ark)
    _app(server, "c-claude", "Claude", "https://claude.ai/api/mcp/auth_callback", opened=True)
    _app(server, "c-code", "Claude Code", "http://localhost:33418/callback", opened=True)
    now = int(time.time())
    with server.db.tx() as c:
        c.execute("INSERT INTO watch_lists (sub, name, created_at) VALUES ('user-1', ?, ?)", (LONG_LIST, now))
        c.execute("UPDATE watch_cards SET list_name=? WHERE id='w_one'", (LONG_LIST,))
        c.execute(
            "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, scryfall_id, "
            "created_at) VALUES ('w_long', 'user-1', 'Asmoranomardicadaistinaculdacar', 'cmm', '1234', "
            "'etched', 's-long', ?)",
            (now,),
        )
    return sid


@pytest.mark.parametrize("app", [True, False], ids=["app", "browser"])
def test_every_row_list_reads_at_200_percent_text_on_a_phone(tmp_path: Path, app: bool) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _seed(server)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            kw: dict = {"has_touch": True, "is_mobile": True}
            if app:
                kw["user_agent"] = APP_UA
            ctx = browser.new_context(viewport={"width": 375, "height": 812}, **kw)
            ctx.add_cookies(
                [
                    {"name": "mtg_session", "value": sid, "url": server.base},
                    {"name": "mtg_theme", "value": "dark" if app else "light", "url": server.base},
                ]
            )
            ctx.route(
                re.compile(r"https://cards\.scryfall\.io/.*"),
                lambda r: r.fulfill(content_type="image/png", body=PNG),
            )
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for path, sel in PAGES.items():
                page.goto(f"{server.base}{path}", wait_until="networkidle")
                page.evaluate(TEXT_200)
                redrawn(page)
                over = sideways(page)
                assert over <= 0, (path, over)
                rows = page.evaluate(ROWS, [sel, NAMES])
                assert rows, (path, rows)
                for r in rows:
                    where = (path, r["text"])
                    assert r["breaks"] == [], (where, r)  # a card name wraps between its words
                    assert r["narrow"] == [], (where, r)  # and has room for six letters at least
                    assert r["h"] <= 400, (where, r["h"])  # a row stays within half a screen
                if path.startswith("/account") or path == "/collection/lists":
                    for row in page.evaluate(HUB_ROW):
                        assert row["lbl"], row
                        if row["icon"]:  # the icon beside the words, never above the label (L11)
                            assert (
                                row["icon"]["top"] >= row["lbl"]["top"] - 2
                                and row["icon"]["bottom"] <= row["bottom"] + 1
                            ), (path, row)
                        if row["ch"]:  # the chevron at the row's end, after the label
                            assert row["ch"]["left"] >= row["lbl"]["right"] - 1, (path, row)
                            assert row["ch"]["right"] >= row["right"] - 40, (path, row)
            assert errors == [], errors
            browser.close()
    finally:
        server.stop()


def test_the_watchlists_long_names_wrap_by_word_not_by_letter(tmp_path: Path) -> None:
    """The review's case (M5): a long list name and a long card name at 200% text on 375 px kept
    one letter per line, the rows over 2000 px tall; each now wraps between words (or, for one
    long word, where it must) and a row stays shorter than the screen."""
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = _seed(server)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            ctx = browser.new_context(
                viewport={"width": 375, "height": 812}, has_touch=True, is_mobile=True, user_agent=APP_UA
            )
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            ctx.route(
                re.compile(r"https://cards\.scryfall\.io/.*"),
                lambda r: r.fulfill(content_type="image/png", body=PNG),
            )
            page = ctx.new_page()
            page.goto(f"{server.base}/watchlist", wait_until="networkidle")
            page.evaluate(TEXT_200)
            redrawn(page)
            lines = page.evaluate(
                """() => [...document.querySelectorAll('.w-t .gname b, .w-t .tr .t a.wcard')].map(n => {
                  const rg = document.createRange(); rg.selectNodeContents(n);
                  const rects = [...rg.getClientRects()].filter(q => q.width > 0);
                  const tops = new Set(rects.map(q => Math.round(q.top)));
                  return {text: n.textContent.trim(), lines: tops.size, chars: n.textContent.trim().length,
                          row: Math.round(n.closest('.tr').getBoundingClientRect().height)}; })"""
            )
            group = next(x for x in lines if x["text"] == LONG_LIST)
            assert group["lines"] <= 6 and group["row"] <= 400, group  # 67 letters, 8 words: not 67 lines
            for x in lines:
                assert x["chars"] / max(x["lines"], 1) >= 6, x  # six letters a line at the least
                assert x["row"] <= 400, x
            browser.close()
    finally:
        server.stop()
