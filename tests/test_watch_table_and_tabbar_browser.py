"""The final polish's re-check on a62955eb (FINDINGS W1 and W2), measured in headless Chromium.
W1: between about 880 and 1100 px the Watchlist's Cards and Decks tables lined their columns up
and left the Card column the scraps (40 px at 900, 164 at 1024): names broke mid-word
("Okiri, Bellig/erent Banne/rkeep/er") and "CMM 1234 · Etched" left its dot at a line's end. W2:
on the phone's bottom bar at 200% text the Assistant count, sized in rem, grew over its icon,
which is sized in px like the rest of the bar."""

from __future__ import annotations

import time

import pytest

from .test_cardsearch_browser import _chromium, _page
from .test_overflow_sweep_browser import MEASURE, _seed, server  # noqa: F401
from .test_suggest_browser import Server
from .test_text_zoom_browser import BROWSER_200, ROOT_200, TEXT_200

pytest.importorskip("playwright")

# the Watchlist's rows: the name cell's width, every word broken over two lines that would have
# fit its cell whole (the sweep's 31 and 40 letter names without a space are wider than any
# column and must break; the thumbnail's own tiny caption aside), and every dot (" · ") left at
# the end of a line
WATCH_ROWS = """() => {
  const broken = [], dangling = [];
  for (const row of document.querySelectorAll('.watch-page .w-t .tr:not(.th)')) {
    const w = document.createTreeWalker(row, NodeFilter.SHOW_TEXT);
    for (let n = w.nextNode(); n; n = w.nextNode()) {
      if (!n.textContent.trim() || n.parentElement.closest('.thumb, [hidden]')) continue;
      if (!n.parentElement.getClientRects().length) continue;
      const cell = n.parentElement.closest('.title, .sub, .px, .chgs, .lbls, .act') || n.parentElement;
      const room = cell.getBoundingClientRect().width;
      const re = /\\S+/g; let m;
      while ((m = re.exec(n.textContent))) {
        const q = document.createRange(); q.setStart(n, m.index); q.setEnd(n, m.index + m[0].length);
        const rs = [...q.getClientRects()].filter(r => r.width > 0);
        const whole = rs.reduce((t, r) => t + r.width, 0);
        if (rs.length > 1 && whole <= room)
          broken.push(m[0] + ' (' + Math.round(whole) + ' of ' + Math.round(room) + 'px)');
        if (m[0] === '·') {
          const after = /\\S/g; after.lastIndex = m.index + 1;
          const nx = after.exec(n.textContent);
          if (nx) {
            const p = document.createRange(); p.setStart(n, nx.index); p.setEnd(n, nx.index + 1);
            const pr = p.getClientRects()[0];
            if (pr && rs[0] && Math.abs(pr.top - rs[0].top) > 2)
              dangling.push(n.textContent.trim().slice(0, 40));
          }
        }
      }
    }
  }
  const names = [...document.querySelectorAll('.watch-page .w-t .tr:not(.th) > .title')]
    .map(t => Math.round(t.getBoundingClientRect().width));
  return {names, broken, dangling, rows: document.querySelectorAll('.watch-page .w-t .tr:not(.th)').length};
}"""


@pytest.mark.parametrize("width", [860, 900, 960, 1024, 1100, 1280])
def test_the_watchlist_tables_keep_their_name_column_from_the_fold_to_a_desktop(
    server: Server,  # noqa: F811
    width: int,
) -> None:
    """W1: in the Cards and Decks tables at 860, 900, 960, 1024, 1100 and 1280 px the name column
    is at least about 180 px, no word breaks in two, no dot is left at a line's end, and nothing
    scrolls sideways."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        got = page.evaluate(WATCH_ROWS)
        assert got["rows"] >= 3 and len(got["names"]) == got["rows"], got  # cards and a deck
        assert min(got["names"]) >= 180, (width, got["names"])
        assert got["broken"] == [], (width, got["broken"])
        assert got["dangling"] == [], (width, got["dangling"])
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()


# the bottom bar's Assistant tab: its count's box and words, its icon's box, what a tap on the
# icon's centre lands on, and how much of the icon the count covers
TAB_BADGE = """() => {
  const a = [...document.querySelectorAll('.tabbar > a')].find(x => x.querySelector('.nb'));
  if (!a) return null;
  const b = a.querySelector('.nb'), i = a.querySelector('svg');
  const br = b.getBoundingClientRect(), ir = i.getBoundingClientRect();
  const ox = Math.max(0, Math.min(br.right, ir.right) - Math.max(br.left, ir.left));
  const oy = Math.max(0, Math.min(br.bottom, ir.bottom) - Math.max(br.top, ir.top));
  const hit = document.elementFromPoint(ir.left + ir.width / 2, ir.top + ir.height / 2);
  const ar = a.getBoundingClientRect();
  return {text: b.textContent.trim(), width: Math.round(br.width), height: Math.round(br.height),
    inside: br.left >= ar.left - 1 && br.right <= ar.right + 1 && br.top >= ar.top - 1,
    icon: {width: Math.round(ir.width), height: Math.round(ir.height)},
    fits: b.scrollWidth <= b.clientWidth + 1 && b.scrollHeight <= b.clientHeight + 1,
    covered: ox * oy / (ir.width * ir.height), centreOnBadge: !!(hit && hit.closest('.nb')),
    font: parseFloat(getComputedStyle(b).fontSize), iconFont: parseFloat(getComputedStyle(a).fontSize)};
}"""


@pytest.mark.parametrize("zoom", ["none", "browser", "root", "text"])
def test_the_tab_bars_assistant_count_never_covers_its_icon(server: Server, zoom: str) -> None:  # noqa: F811
    """W2: on a 412 px phone the Assistant tab's count (a two-digit one) sits on the icon's top
    right at 100% text and at 200% (the browser's root at 200%, a 28 px root, and every font
    doubled as Android's font scale does): its words fit its box, it is no taller than the bar's
    18 px count (a few px taller under the text-only zoom, when its box follows its digits' own
    line), it stays inside its tab, it covers at most a corner of the icon, and the icon's centre
    is not under it."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    now = int(time.time())
    for k in range(14):  # a two-digit count waiting, as the finding's "15"
        server.db.save_proposal(
            {
                "id": f"w2-{k}",
                "owner_sub": "user-1",
                "kind": "edit",
                "deck_id": "42",
                "deck_name": "Liesa Test Deck",
                "baseline_fingerprint": "f",
                "changes": [],
                "diff_text": "+1 Sol Ring",
                "expires_at": now + 3600,
                "created_by_client": "app-1",
            }
        )
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 412, "light", exe)
        page.goto(f"{server.base}/collection", wait_until="networkidle")
        if zoom != "none":
            page.evaluate({"browser": BROWSER_200, "root": ROOT_200, "text": TEXT_200}[zoom])
            page.wait_for_timeout(200)
        got = page.evaluate(TAB_BADGE)
        assert got and len(got["text"]) == 2, got
        assert got["fits"], got
        # the bar's 18 px count; under the text-only zoom its box follows its 20 px digits' own line
        assert got["height"] <= max(18, got["font"] * 1.25) and got["inside"], got
        assert got["covered"] <= 0.3 and not got["centreOnBadge"], got
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()
