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
from .test_overflow_sweep_browser import CARDS, MEASURE, _seed, server  # noqa: F401
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
  // each header row's shown cells, left to right: two that overlap are listed by their words
  const heads = [];
  for (const th of document.querySelectorAll('.watch-page .w-t .tr.th')) {
    const cells = [...th.children].filter(c => c.getClientRects().length && c.textContent.trim())
      .map(c => ({t: c.textContent.trim().slice(0, 20), r: c.getBoundingClientRect()}))
      .sort((a, b) => a.r.left - b.r.left);
    for (let i = 1; i < cells.length; i++)
      if (cells[i].r.left < cells[i - 1].r.right - 1) heads.push(cells[i - 1].t + ' / ' + cells[i].t);
  }
  return {names, broken, dangling, heads,
    rows: document.querySelectorAll('.watch-page .w-t .tr:not(.th)').length};
}"""


@pytest.mark.parametrize(
    "width,zoom",
    [(w, "none") for w in (860, 900, 960, 1024, 1100, 1280, 1440)]
    + [(w, z) for w in (1100, 1280, 1440) for z in ("root", "text")],
)
def test_the_watchlist_tables_keep_their_name_column_from_the_fold_to_a_desktop(
    server: Server,  # noqa: F811
    width: int,
    zoom: str,
) -> None:
    """W1: in the Cards and Decks tables at 860, 900, 960, 1024, 1100, 1280 and 1440 px the name
    column is at least about 180 px, no word breaks in two, no dot is left at a line's end, and
    nothing scrolls sideways. Audit D3 on 099f600a: the same from 1100 px up at 200% text (a 28 px
    root, and every font doubled), where the lined-up columns, sized in rem, left the name column
    0 px and the header cells ran over each other: the folded layout stays until the name column
    has its room, whatever the width and the text size."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        if zoom != "none":
            page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
            page.wait_for_timeout(200)
        got = page.evaluate(WATCH_ROWS)
        assert got["rows"] >= 3 and len(got["names"]) == got["rows"], got  # cards and a deck
        assert min(got["names"]) >= 180, (width, zoom, got["names"])
        assert got["heads"] == [], (width, zoom, got["heads"])
        assert got["broken"] == [], (width, zoom, got["broken"])
        assert got["dangling"] == [], (width, zoom, got["dangling"])
        assert page.evaluate(MEASURE) == [] and errors == []
        browser.close()


# the labels in the rows' facts line: each one's box against its cell's, and whether its words fit
LABELS = """() => [...document.querySelectorAll('.watch-page .w-t .sub .ui-lbl')].map(l => {
  const r = l.getBoundingClientRect(), c = l.closest('.title').getBoundingClientRect();
  return {text: l.textContent.trim(), fits: l.scrollWidth <= l.clientWidth + 1,
    inside: r.left >= c.left - 1 && r.right <= c.right + 1, height: Math.round(r.height)};
})"""


@pytest.mark.parametrize("zoom", ["none", "browser", "root", "text"])
@pytest.mark.parametrize("width", [320, 360, 375])
def test_the_watched_rows_labels_fit_a_phone_at_200_percent_text(
    server: Server,  # noqa: F811
    width: int,
    zoom: str,
) -> None:
    """On 7cd39e30 (the merge of fin-ui11 and fin-ui12) the 200% sweep found /watchlist at 320 px
    scrolling sideways by 16 px, the bottom bar's More tab pushed out of the window: once the
    Binder had been read (the sweep's earlier pages), each row carried "In binder · you own 1",
    a label kept to one line (polish B8), wider than the Card column at 200% text; its overflow
    widened the page. Now a label wider than its cell wraps at a space, never inside a word, and
    the dot keeps its words; at 100% every label is still one line (the rows fold, the name over
    the whole width, where the thumbnail and the row's end would leave it 55 px: 320 to 390 px),
    and nothing scrolls."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with server.db.tx() as c:  # the Binder as last read whole: the watched cards, one copy each
        for i, name in enumerate(CARDS[-2:]):
            c.execute(
                "INSERT INTO binder_records (sub, rid, scryfall_id, name, finish, quantity, row_json) "
                "VALUES ('user-1', ?, '', ?, 'nonfoil', 1, '{}')",
                (900 + i, name),
            )
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        if zoom != "none":
            page.evaluate({"browser": BROWSER_200, "root": ROOT_200, "text": TEXT_200}[zoom])
            page.wait_for_timeout(200)
        labels = page.evaluate(LABELS)
        owned = [lb for lb in labels if lb["text"].startswith("In binder")]
        assert len(owned) == 2 and all(lb["text"].endswith("you own 1") for lb in owned), labels
        assert all(lb["fits"] and lb["inside"] for lb in labels), (width, zoom, labels)
        if zoom == "none":  # one line each, the 24 px pill (polish B8)
            assert all(lb["height"] <= 26 for lb in labels), labels
        got = page.evaluate(WATCH_ROWS)
        assert got["rows"] >= 3, got
        assert got["broken"] == [] and got["dangling"] == [], (width, zoom, got)
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
