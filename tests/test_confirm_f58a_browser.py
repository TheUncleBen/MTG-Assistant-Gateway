"""The confirmation round on f58a98cc (0.7.46): the report charts' labels at large text (AD1, NC2),
a switch that fits on one row kept on one row (NC1), the tab bar's count kept off the bar's edge
and capped under a text-only zoom (AD2, NA5), the locks on guarded buttons growing with their
words (AD2) and a wrapped icon button's content centred (P-13). On the overflow sweep's hostile
data (test_overflow_sweep_browser). Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_confirm_0fe1_browser import BADGE, ICON_BUTTONS, SEG_NATURAL, _ctx, _goto, site  # noqa: F401
from .test_overflow_sweep_browser import server  # noqa: F401
from .test_suggest_browser import Server
from .waits import redrawn

pytest.importorskip("playwright")

# -- AD1, NC2: the report charts' labels at large text -------------------------------------------

# Every chart's shown labels: none over another, none outside the chart's own box or cut at its
# edge, the turn labels under the axis line, each at the size of the words around the chart (NC3,
# AF2), and a column chart's every value and turn shown (AF1)
CHART_LABELS = """() => {
  const out = [];
  const cut = (a, b) => Math.min(a.right, b.right) - Math.max(a.left, b.left) > 0.5 &&
    Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top) > 0.5;
  let n = 0;
  for (const s of document.querySelectorAll('.chart svg')) {
    if (!s.getClientRects().length) continue;
    n++;
    const ch = s.closest('.chart'), cr = ch.getBoundingClientRect(), sr = s.getBoundingClientRect();
    const title = ch.querySelector('h3').textContent.trim();
    const ts = [...s.querySelectorAll('text')].filter(t => getComputedStyle(t).display !== 'none' &&
      t.getClientRects().length);
    const rs = ts.map(t => t.getBoundingClientRect());
    const body = parseFloat(getComputedStyle(ch).fontSize);
    for (let i = 0; i < ts.length; i++) {
      const r = rs[i], name = title + ': "' + ts[i].textContent + '"';
      for (let j = i + 1; j < ts.length; j++)
        if (cut(r, rs[j])) out.push(name + ' runs into "' + ts[j].textContent + '"');
      if (r.left < sr.left - 1 || r.right > sr.right + 1 || r.top < sr.top - 1 || r.bottom > sr.bottom + 1)
        out.push(name + ' runs out of its chart');
      // a chart drawn wider than its place scrolls sideways (.chart-x): its labels are reached so
      if (!s.closest('.chart-x') && (r.left < cr.left - 1 || r.right > cr.right + 1))
        out.push(name + ' is cut at the edge');
      const px = parseFloat(getComputedStyle(ts[i]).fontSize) * ts[i].getScreenCTM().a;
      if (px > body + 0.5) out.push(name + ' is ' + Math.round(px) + 'px beside ' + body + 'px words');
      if (px < body - 0.5) out.push(name + ' is ' + px.toFixed(1) + 'px beside ' + body + 'px words');
    }
    if (s.querySelector('rect.col'))
      for (const t of s.querySelectorAll('text.v, text.x'))
        if (getComputedStyle(t).display === 'none')
          out.push(title + ': the column ' + (t.classList.contains('v') ? 'value' : 'turn') + ' "' +
            t.textContent + '" is left out');
    const axis = s.querySelector('line.axis');
    if (axis) {
      const ar = axis.getBoundingClientRect();
      ts.forEach((t, i) => {
        if (t.classList.contains('x') && rs[i].top < ar.bottom + 1)
          out.push(title + ': "' + t.textContent + '" sits on the axis line');
      });
    }
  }
  if (!n) out.push('no chart shown');
  return out;
}"""

CHART_PLACES = [
    ("320", 320, False, None),
    ("320 root", 320, True, None),
    ("320 text", 320, False, "text"),
    ("320 browser", 320, False, "browser"),
    ("412 root", 412, True, None),
    ("412 text", 412, False, "text"),
    ("1024 root", 1024, True, None),
    ("1024", 1024, False, None),
    ("900", 900, False, None),
    ("900 root", 900, True, None),
    ("717", 717, False, None),
    ("1280", 1280, False, None),
    ("1440", 1440, False, None),
]


@pytest.mark.parametrize("place", CHART_PLACES, ids=[p[0] for p in CHART_PLACES])
def test_the_report_charts_labels_hold_together_at_every_text_size(
    server: Server,  # noqa: F811
    site,  # noqa: F811
    place: tuple,
) -> None:
    """NC2 (from AD1): at 200% text the report charts' axis labels ran into each other ("0%" over
    "25%", "0%" over "T1"), were cut at the chart's left edge ("00%"), sat on the axis line, and
    were larger than the words around them (36 px at 1024 px). The chart makes room for its
    labels, leaves out what has no room, and caps their size at its words' size.

    NC3: on a desktop or the Fold at 100% text the labels then shrank to 7-9 px, because the size
    was worked out from the chart's width while its height is capped. The labels keep their size
    at every width.

    AF2: at 900-1024 px they were still 11.8-13.6 px beside 14 px words; they are the words' size.
    AF1: at 412 px and 200% the thinning left the 20, 17 and 5 columns of "When the commander comes
    down" with neither a value nor a turn; a column chart scrolls sideways instead."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    tag, width, root, zoom = place
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, width, root=root)
        page = ctx.new_page()
        _goto(page, server, "/history/reports/r1", zoom)
        page.evaluate("() => document.querySelectorAll('details').forEach(d => { d.open = true; })")
        redrawn(page)
        problems = page.evaluate(CHART_LABELS)
        ctx.close()
        browser.close()
    assert problems == [], f"@{tag}\n" + "\n".join(problems)


# -- NC1: a switch that fits on one row stays on one row -----------------------------------------

# Each switch shown on more than one row although its parts, each at its own one-line width, fit
# its line side by side
SEG_FITS = """() => {
  const natural = SEG_NATURAL_JS;
  const out = [];
  for (const s of document.querySelectorAll('.hubseg')) {
    const kids = [...s.children].filter(k => k.getClientRects().length &&
      getComputedStyle(k).position !== 'absolute');
    if (kids.length < 2) continue;
    const tops = new Set(kids.map(k => Math.round(k.getBoundingClientRect().top / 4)));
    if (tops.size < 2) continue;
    const m = natural(s), sum = m.sum + m.gap * (m.n - 1);
    if (sum <= m.room)
      out.push((s.getAttribute('aria-label') || s.className) + ' (' + kids.map(k => k.textContent.trim())
        .join('|') + ') takes ' + tops.size + ' rows where its parts fit one (' + Math.round(sum) + ' of ' +
        Math.round(m.room) + ' px)');
  }
  return out;
}""".replace("SEG_NATURAL_JS", SEG_NATURAL)


def test_a_switch_whose_parts_fit_one_row_keeps_to_it(server: Server, site) -> None:  # noqa: F811
    """NC1 (from NA4): at 320 px the Binder's switch (Cards, Lists, Watchlist) stacked into three
    full-width rows, and the Scan page's tabs into two by two, because every part took the widest
    one's width. Parts that fit the line at their own widths stay on one row, and keep to it
    (New deck's tabs went one a row and back by turns)."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, width in (("320", 320), ("360", 360)):
            ctx = _ctx(browser, server, sid, width)
            page = ctx.new_page()
            for path in ("/watchlist", "/collection", "/collection/lists", "/scan"):
                _goto(page, server, path)
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(SEG_FITS)]
            ctx.close()
        # (the crawl: New deck's Empty, Paste a list and From a file went one a row and back by
        # turns at 900 px and 200% text, the wrapped "Paste a list" padded to centre it)
        ctx = _ctx(browser, server, sid, 900)
        page = ctx.new_page()
        _goto(page, server, "/decks/new", "browser")
        problems += [f"/decks/new @900 browser: {f}" for f in page.evaluate(SEG_FITS)]
        ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- AD2, NA5: the tab bar's count off the bar's edge, capped under a text-only zoom -------------

# The count's room under the bar's top edge, its size and how much of the icon it covers
BADGE_ROOM = """() => {
  const nb = document.querySelector('.tabbar > a .nb');
  const bar = nb.closest('.tabbar'), svg = nb.closest('a').querySelector('svg');
  const b = nb.getBoundingClientRect(), r = bar.getBoundingClientRect(), i = svg.getBoundingClientRect();
  const ox = Math.max(0, Math.min(b.right, i.right) - Math.max(b.left, i.left));
  const oy = Math.max(0, Math.min(b.bottom, i.bottom) - Math.max(b.top, i.top));
  return { room: b.top - r.top, h: b.height, cover: ox * oy / (i.width * i.height) };
}"""


@pytest.mark.parametrize("waiting", [15, 120])
def test_the_tab_bar_count_keeps_room_under_the_bar_and_its_size_under_any_zoom(
    server: Server,  # noqa: F811
    site,  # noqa: F811
    waiting: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """AD2: at 412 px and 200% text the Assistant tab's "15" touched the bar's top edge. NA5: under
    a text-only zoom (Android's font scale) the count stayed 20 px, 25 px tall, and covered up to
    36% of the icon. The count keeps a few px under the bar's edge and the same capped size (18 px
    tall) whatever scales the text, on the icon's corner."""
    from playwright.sync_api import sync_playwright

    from mtg_gateway import theme

    monkeypatch.setattr(theme, "WAITING_COUNT", lambda sub: waiting)
    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, width, root, zoom in (
            ("412", 412, False, None),
            ("412 root", 412, True, None),
            ("412 text", 412, False, "text"),
            ("320 text", 320, False, "text"),
            ("320 root", 320, True, None),
        ):
            ctx = _ctx(browser, server, sid, width, root=root)
            page = ctx.new_page()
            _goto(page, server, "/precons", zoom)
            m = page.evaluate(BADGE_ROOM)
            if m["room"] < 2.5:
                problems.append(f"@{tag}: the count is {m['room']:.1f}px under the bar's edge")
            if m["h"] > 18.5:
                problems.append(f"@{tag}: the count is {m['h']:.1f}px tall")
            if m["cover"] > 0.2:  # on the icon's corner: 15% at 100% text
                problems.append(f"@{tag}: the count covers {100 * m['cover']:.0f}% of the icon")
            problems += [f"@{tag}: {f}" for f in page.evaluate(BADGE, False)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


LOCKS = """() => [...document.querySelectorAll('form.guarded button[data-guard] .lk svg')]
  .filter(s => s.getClientRects().length).map(s => {
    const b = s.closest('button');
    return { icon: s.getBoundingClientRect().height, text: parseFloat(getComputedStyle(b).fontSize) };
  })"""


def test_a_guarded_buttons_lock_grows_with_its_words(server: Server, site) -> None:  # noqa: F811
    """AD2 (polish): the lock on a proposal's locked Apply and Reject stayed 14 px beside their
    32 px words at 200% text. The lock is as tall as its words' letters, 14 px at the least."""
    from playwright.sync_api import sync_playwright

    exe, sid, pending = site
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        sizes = {}
        for root in (False, True):
            ctx = _ctx(browser, server, sid, 412, root=root)
            page = ctx.new_page()
            _goto(page, server, f"/proposals/{pending}")
            sizes[root] = page.evaluate(LOCKS)
            ctx.close()
        browser.close()
    assert sizes[False] and sizes[True], sizes
    for s in sizes[False]:
        assert 13.5 <= s["icon"] <= s["text"] + 0.5, sizes
    for s in sizes[True]:
        assert s["icon"] >= 0.8 * s["text"], sizes


# Each shown button with words whose icon is under .85 of its words' size (AE2)
SMALL_ICONS = """() => [...document.querySelectorAll('button, .btn, [role=tab]')]
  .filter(b => b.getClientRects().length && b.textContent.trim() && b.querySelector(':scope > svg.i'))
  .map(b => [b.textContent.trim(), b.querySelector(':scope > svg.i').getBoundingClientRect().height,
    parseFloat(getComputedStyle(b).fontSize)])
  .filter(([, h, fs]) => h > 0 && h < 0.85 * fs - 0.5)
  .map(([t, h, fs]) => t + ': ' + h.toFixed(1) + 'px icon beside ' + fs + 'px words')"""


@pytest.mark.parametrize("zoom", [None, "text"], ids=["root", "text"])
def test_a_buttons_icon_grows_with_its_words(server: Server, site, zoom) -> None:  # noqa: F811
    """AE2 (polish): at 320 px and 200% text the "+" of the New deck box's "Create deck" stayed
    18 px beside 28 px words, as did the icons of other buttons a page gave a fixed size (Sign
    out, Import a list, Fit the screen). A button's icon grows with its words."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for path in ("/decks/new", "/account", "/collection/lists"):
            ctx = _ctx(browser, server, sid, 320, root=zoom is None)
            page = ctx.new_page()
            _goto(page, server, path, zoom)
            problems += [f"{path}: {f}" for f in page.evaluate(SMALL_ICONS)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- P-13: a wrapped icon button's content centred -----------------------------------------------


def test_the_profile_pictures_upload_button_is_centred(server: Server, site) -> None:  # noqa: F811
    """P-13: "Upload a picture…" on the profile page sat 58 px from its left and 90 px from its
    right at 412 px with every font doubled (35/44 at 320 px with a 28 px root); the label also
    wrapped in a button wide enough for it on one line. Its icon and label are centred together."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, width, root, zoom, app in (
            ("412 app text", 412, False, "text", True),
            ("360 text", 360, False, "text", False),
            ("320 root", 320, True, None, False),
        ):
            ctx = _ctx(browser, server, sid, width, root=root, app=app)
            page = ctx.new_page()
            for path in ("/account/details/profile", "/account/details"):
                _goto(page, server, path, zoom)
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(ICON_BUTTONS)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


def test_an_icon_button_shown_after_the_page_loaded_is_centred(server: Server, site) -> None:  # noqa: F811
    """P-13 (the crawl's check): a button hidden when the page was fitted stayed off centre once
    shown: "Delete this deck" in its box (28 px left, 77 px right at 320 px with a 28 px root),
    "Search all decks" in the filter sheet, and "HTML page" in a folded report section."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for path, zoom, root, show in (
            ("/decks/42", None, True, "() => document.querySelector('[data-box-open=delete]').click()"),
            ("/precons", None, True, "() => document.querySelector('.spanel .sp-open').click()"),
            (
                "/history/reports/r1",
                "browser",
                False,
                "() => document.querySelectorAll('details').forEach(d => { d.open = true; })",
            ),
        ):
            ctx = _ctx(browser, server, sid, 320, root=root)
            page = ctx.new_page()
            _goto(page, server, path, zoom)
            page.evaluate(show)
            redrawn(page)
            problems += [f"{path}: {f}" for f in page.evaluate(ICON_BUTTONS)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)
