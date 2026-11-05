"""The final polish's batch 8 (Z1 to Z5), measured in headless Chromium: five layouts whose
columns were sized in rem beside a fluid one, so at 200% text the fluid column got the scraps and
its words broke inside themselves. Each page is drawn at 320, 360, 412, 600, 720, 900, 1024, 1280
and 1440 px, at 100% text, at a 28 px root (ROOT_200), with every font doubled (TEXT_200) and
with the browser's own 16 px default doubled (BROWSER_200, a 32 px root): no word breaks in two
where it would have fitted its column, nothing is clipped or scrolls sideways, no two controls
overlap (MEASURE), every control is a 44 px target and no control covers a label. Z1: the New
deck box's Deck name beside an 11 rem Format. Z2: Admin › Users' lined-up columns, and the
email on a 320 px phone (audit 71911eba). Z3: Admin › Settings' label beside a 16 rem control,
and the radios' words on a 320 px phone (the audit). Z4: the skill page's Download button,
breaking "Downlo/ad" under the steps' overflow-wrap. Z5: the report's Deck tile, its "$310.00"
and "100 cards" broken over the lines."""

from __future__ import annotations

import re

import pytest

from .test_cardsearch_browser import _chromium
from .test_editor_browser import PNG
from .test_overflow_sweep_browser import MEASURE, _seed, server  # noqa: F401
from .test_suggest_browser import Server
from .test_text_zoom_browser import BROWSER_200, ROOT_200, TEXT_200

pytest.importorskip("playwright")

WIDTHS = [320, 360, 412, 600, 720, 900, 1024, 1280, 1440]
ZOOMS = ["none", "root", "text", "browser"]

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


# the Users table: its header cells left to right, two that overlap by their words, and its rows
USERS_TABLE = """() => {
  const t = document.querySelector('.au .ui-table');
  const heads = [];
  for (const th of t.querySelectorAll('.tr.th')) {
    const cells = [...th.children].filter(c => c.getClientRects().length && c.textContent.trim())
      .map(c => ({t: c.textContent.trim().slice(0, 20), r: c.getBoundingClientRect()}))
      .sort((a, b) => a.r.left - b.r.left);
    for (let i = 1; i < cells.length; i++)
      if (cells[i].r.left < cells[i - 1].r.right - 1) heads.push(cells[i - 1].t + ' / ' + cells[i].t);
  }
  return {heads, rows: t.querySelectorAll('.tr.mem').length,
    emails: [...t.querySelectorAll('.tr.mem .title .deck')].map(e => e.textContent.trim())};
}"""


@pytest.mark.parametrize("zoom", ZOOMS)
def test_the_users_table_folds_to_rows_when_the_text_is_large(server: Server, zoom: str) -> None:  # noqa: F811
    """Z2: at 1024 to 1440 px with 200% text the lined-up columns (11, 10 and 6.5 rem beside
    two fluid ones) left Member and Apps a few em: "MEMBE/R", "APP/S", "alice@e/xample./test",
    "non/e"; on a 320 px phone at 200% the email broke inside a word too (audit 71911eba: it may
    break after its @ or before a dot of its domain, never elsewhere). At every width and text
    size no word in the table breaks in two where it would have fitted the row, no two header
    cells overlap, the ⋮ is a 44 px target and nothing is clipped or scrolls sideways."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, errors, open_at = _contexts(p, server, sid, "light", exe)
        for width in WIDTHS:
            ctx, page = open_at(width)
            page.goto(f"{server.base}/admin/users", wait_until="networkidle")
            _zoom(page, zoom)
            got = page.evaluate(USERS_TABLE)
            assert got["rows"] >= 1 and "alice@example.test" in got["emails"], (width, zoom, got)
            assert got["heads"] == [], (width, zoom, got["heads"])
            broken = page.evaluate(BROKEN, [".au .ui-table .tr > span", ".ui-table"])
            assert broken == [], (width, zoom, broken)
            short = page.evaluate(SHORT, ".au .ui-table .kebab")
            assert short == [], (width, zoom, short)
            assert page.evaluate(MEASURE) == [], (width, zoom)
            ctx.close()
        assert errors == []
        browser.close()


# every setting's row: its label's box against its control's, what a tap on the label's centre
# lands on, and the words column's width in its own em
SETTING_ROWS = """() => [...document.querySelectorAll('.set-row')].filter(r => r.getClientRects().length)
  .filter(r => !r.closest('details:not([open])'))  // the scan group starts folded away
  .map(row => {
    const lab = row.querySelector('.set-label'), ctl = row.querySelector('.set-ctl');
    const text = row.querySelector('.set-text');
    // the label mid-window (a row is taller than a phone's window at 200%), under no bar
    lab.scrollIntoView({block: 'center', behavior: 'instant'});
    const lr = lab.getBoundingClientRect(), cr = ctl.getBoundingClientRect();
    const ox = Math.min(lr.right, cr.right) - Math.max(lr.left, cr.left);
    const oy = Math.min(lr.bottom, cr.bottom) - Math.max(lr.top, cr.top);
    const hit = document.elementFromPoint(lr.left + Math.min(lr.width, 20), lr.top + lr.height / 2);
    return {label: lab.textContent.trim(), overlap: ox > 1 && oy > 1,
      covered: !(hit && text.contains(hit)),
      ems: text.getBoundingClientRect().width / parseFloat(getComputedStyle(text).fontSize),
      rowEms: row.getBoundingClientRect().width / parseFloat(getComputedStyle(text).fontSize)};
  })"""


@pytest.mark.parametrize("zoom", ZOOMS)
def test_the_settings_rows_stack_the_label_over_the_control_when_the_text_is_large(
    server: Server,  # noqa: F811
    zoom: str,
) -> None:
    """Z3: at 720 px with 200% text the 16 rem control column left the words 144 px: the
    approval modes' box covered "Approval mode for new members" and the help was one word a
    line; on a 320 px phone with the browser's font doubled the radios' air, in rem, left their
    words 120 px ("change/s", audit 71911eba). At every width and text size no setting's control
    touches or covers its label, the words have at least 12 em (or the whole row, on a phone at
    200%), no word breaks in two, every radio and box is a 44 px target and nothing is clipped
    or scrolls sideways."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, errors, open_at = _contexts(p, server, sid, "light", exe)
        for width in WIDTHS:
            ctx, page = open_at(width)
            page.goto(f"{server.base}/admin/settings", wait_until="networkidle")
            _zoom(page, zoom)
            rows = page.evaluate(SETTING_ROWS)
            assert any(r["label"] == "Approval mode for new members" for r in rows), (width, zoom)
            for r in rows:
                assert not r["overlap"] and not r["covered"], (width, zoom, r)
                assert r["ems"] >= min(12, r["rowEms"] - 0.5), (width, zoom, r)
            broken = page.evaluate(
                BROKEN,
                [".set-row .set-label, .set-row .set-help, .set-choices label, .set-seg label", ".set-row"],
            )
            assert broken == [], (width, zoom, broken)
            short = page.evaluate(
                SHORT,
                ".set-choices label, .set-seg label, .set-ctl input:not([type=radio]), .set-ctl select, "
                ".set-ctl .msel-btn, .set-save button",
            )
            assert short == [], (width, zoom, short)
            assert page.evaluate(MEASURE) == [], (width, zoom)
            ctx.close()
        assert errors == []
        browser.close()


# the skill page's Download button: its box, its step's box and its words' lines
DOWNLOAD = """() => {
  const a = [...document.querySelectorAll('.inst .actions .btn')]
    .find(b => /Download the skill/.test(b.textContent));
  if (!a) return null;
  const li = a.closest('li'), r = a.getBoundingClientRect(), lr = li.getBoundingClientRect();
  const q = document.createRange(); q.selectNodeContents(a);
  const lines = new Set([...q.getClientRects()].filter(x => x.width > 0).map(x => Math.round(x.top))).size;
  return {text: a.textContent.trim(), height: Math.round(r.height), lines,
    inside: r.left >= lr.left - 1 && r.right <= lr.right + 1};
}"""


@pytest.mark.parametrize("zoom", ZOOMS)
def test_the_skill_download_button_wraps_at_spaces_only(server: Server, zoom: str) -> None:  # noqa: F811
    """Z4: at 360 and 900 px with 200% text the steps' overflow-wrap:anywhere reached the
    "Download the skill (ZIP)" button, which broke "Downlo/ad". The audit's shots had the
    browser's own 16 px default doubled (BROWSER_200, a 32 px root), where the button's padding
    and icon leave "Download" no room at 360 and 900 px; at the pages' 28 px root this machine's
    font fits it there and breaks it at 320 px. At 320 px and the usual widths, at 100% and all
    three kinds of 200% text, the button wraps at spaces only, stays inside its step, is at
    least 44 px tall and nothing on the page is clipped or scrolls sideways."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, errors, open_at = _contexts(p, server, sid, "light", exe)
        for width in WIDTHS:
            ctx, page = open_at(width)
            page.goto(f"{server.base}/skill", wait_until="networkidle")
            _zoom(page, zoom)
            got = page.evaluate(DOWNLOAD)
            assert got and got["text"] == "Download the skill (ZIP)", (width, zoom, got)
            assert got["inside"] and got["height"] >= 44, (width, zoom, got)
            broken = page.evaluate(BROKEN, [".inst .actions .btn", "li"])
            assert broken == [], (width, zoom, broken)
            assert page.evaluate(MEASURE) == [], (width, zoom)
            ctx.close()
        assert errors == []
        browser.close()


# the report's key numbers: each tile's width against the row's, its big value's number (the
# text before the unit) and unit as lines, and whether the value fits its tile
KEY_NUMBERS = """() => {
  const row = document.querySelector('.rv-page .rv-w');
  const rw = row.getBoundingClientRect().width;
  const lines = (q) =>
    new Set([...q.getClientRects()].filter(r => r.width > 0).map(r => Math.round(r.top))).size;
  return [...row.querySelectorAll('.wdg')].map(w => {
    const big = w.querySelector('.big'), small = big.querySelector('small');
    const q = document.createRange(); q.selectNodeContents(big);
    if (small) q.setEndBefore(small);
    const wr = w.getBoundingClientRect(), br = big.getBoundingClientRect();
    const sq = document.createRange(); if (small) sq.selectNodeContents(small);
    const cs = getComputedStyle(w);
    const inner = w.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    // the unit's words laid end to end: wider than the tile, and only then, it may wrap
    const whole = small ? [...sq.getClientRects()].reduce((t, r) => t + r.width, 0) : 0;
    return {label: w.querySelector('.lab').textContent.trim(), share: wr.width / rw,
      number: q.toString().trim(), numberLines: lines(q),
      unit: small ? small.textContent.trim() : '', unitLines: small ? lines(sq) : 1,
      unitFits: whole <= inner + 1,
      fits: big.scrollWidth <= big.clientWidth + 1 && br.right <= wr.right + 1 && br.left >= wr.left - 1};
  });
}"""


@pytest.mark.parametrize(
    "zoom,theme",
    [("none", "light"), ("root", "light"), ("text", "light"), ("browser", "light"), ("text", "dark")],
)
def test_the_reports_deck_tile_keeps_its_value_and_unit_whole(
    server: Server,  # noqa: F811
    zoom: str,
    theme: str,
) -> None:
    """Z5: at 720 to 1024 px with 200% text the report's three-a-row key numbers gave the Deck
    tile about 10 em: "$310./00", "100 / cards", "2 issues / found". At every width and text
    size each tile's big value is one line, its unit stays together on one line (it wraps only
    when wider than the tile), nothing is clipped; at 200% text from 720 to 1024 px the tiles
    go one a row."""
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid, _pending = _seed(server)
    with sync_playwright() as p:
        browser, errors, open_at = _contexts(p, server, sid, theme, exe)
        for width in WIDTHS:
            ctx, page = open_at(width)
            page.goto(f"{server.base}/history/reports/r1", wait_until="networkidle")
            _zoom(page, zoom)
            tiles = page.evaluate(KEY_NUMBERS)
            deck = next((t for t in tiles if t["label"] == "Deck"), None)
            assert deck and deck["number"] == "$310.00" and deck["unit"] == "100 cards", (width, zoom, tiles)
            for t in tiles:
                assert t["numberLines"] <= 1 and t["fits"], (width, zoom, t)
                if t["unitFits"]:
                    assert t["unitLines"] <= 1, (width, zoom, t)
                if zoom != "none" and 720 <= width <= 1024:
                    assert t["share"] > 0.99, (width, zoom, t)
            broken = page.evaluate(BROKEN, [".rv-page .rv-w .wdg .big", ".wdg"])
            assert broken == [], (width, zoom, broken)
            assert page.evaluate(MEASURE) == [], (width, zoom)
            ctx.close()
        assert errors == []
        browser.close()
