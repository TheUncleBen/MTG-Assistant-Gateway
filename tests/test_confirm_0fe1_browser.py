"""The confirmation round on 0fe11813 (0.7.46): the box titles at 200% text (NA1), the keyboard
shortcuts box's pairs (NA2, P-7), the tab bar after an in-place text size change (NA3), the
switches' balanced rows (NA4), the tab bar's count badge (NA5, AD2), the watchlist's "In binder"
pill (P-8), the dots between facts (P-15), text boxes and their hints (P-5, P-19, AD3), the inline
action links' hit boxes (P-11), icon buttons' centring (P-13), long values' breaks (P-14), the
band under the bar on a phone's deck page (P-16), a heading word wider than its line (P-1) and the
report charts' labels (AD1). On the overflow sweep's hostile data (test_overflow_sweep_browser).
Skipped without a Chromium build."""

from __future__ import annotations

import re

import pytest

from .test_cardsearch_browser import _chromium
from .test_editor_browser import PNG
from .test_overflow_sweep_browser import CARDS, SHEETS_LOADED, _seed, server  # noqa: F401
from .test_suggest_browser import Server
from .test_text_zoom_browser import TEXT_200
from .test_ui_standard_0746_browser import APP_UA
from .waits import redrawn

pytest.importorskip("playwright")

# a 28 px root from the first paint: the browser's text size doubled, as the scripts that size
# themselves on load see it
# (the page's own <html> is parsed after the script runs, so its style is set as it arrives)
ROOT_28 = """(() => {
  const set = () => document.documentElement &&
    document.documentElement.style.setProperty('font-size', '28px', 'important');
  set();
  new MutationObserver((m, o) => { if (document.body) o.disconnect(); set(); })
    .observe(document, { childList: true, subtree: true });
})()"""


@pytest.fixture
def site(server: Server):  # noqa: F811
    exe = _chromium()
    sid, pending = _seed(server)
    server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
    return exe, sid, pending


def _ctx(
    browser,
    server: Server,
    sid: str,
    width: int,
    *,
    root: bool = False,
    app: bool = False,  # noqa: F811
    touch: bool | None = None,
    height: int = 860,
):
    touch = width < 900 if touch is None else touch
    kw = dict(viewport={"width": width, "height": height}, has_touch=touch, reduced_motion="reduce")
    if app:
        kw.update(is_mobile=True, user_agent=APP_UA)
    ctx = browser.new_context(**kw)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"), lambda r: r.fulfill(content_type="image/png", body=PNG)
    )
    ctx.route(re.compile(r"/decks/[^/]+/check(\?|$)"), lambda r: r.abort())
    if root:
        ctx.add_init_script(ROOT_28)
    return ctx


def _goto(page, server: Server, path: str, zoom: str | None = None) -> None:  # noqa: F811
    server.db.delete_prefs("user-1", prefixes=("view.",))
    r = page.goto(f"{server.base}{path}", wait_until="networkidle")
    assert r is not None and r.status == 200, (path, r and r.status)
    page.wait_for_function(SHEETS_LOADED)
    if zoom == "text":
        page.evaluate(TEXT_200)
    elif zoom == "browser":  # the browser's own 16 px default doubled
        page.evaluate("() => document.documentElement.style.setProperty('font-size', '200%', 'important')")
    redrawn(page)


# -- NA1: a box's title above its content -------------------------------------------------------

BOX_TITLE = """() => {
  const out = [];
  for (const b of document.querySelectorAll('.mtgbox')) {
    const bh = b.querySelector(':scope > .bh'), h = bh && bh.querySelector('h2');
    if (!h || !h.getClientRects().length) continue;
    let next = bh.nextElementSibling;
    while (next && !next.getClientRects().length) next = next.nextElementSibling;
    if (!next) continue;
    const hr = h.getBoundingClientRect(), nr = next.getBoundingClientRect();
    if (hr.bottom > nr.top + 1)
      out.push('"' + h.textContent.trim().slice(0, 40) + '" runs ' + Math.round(hr.bottom - nr.top) +
        'px under ' + next.className);
  }
  return out;
}"""

TALL_BOXES = [
    "/decks/42/export",
    "/decks/42/package",
    "/decks/42/help",
    "/decks/42/delete",
    "/decks/42/registration",
    "/decks/42/contest",
    "/account/details/profile?picture=1",
]


def test_a_tall_boxs_wrapped_title_stays_above_its_content(server: Server, site) -> None:  # noqa: F811
    """NA1: at 200% text a box taller than the screen whose title takes two lines and more drew
    its title under the box's content (the header shrank to 44 px in the box's column)."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, width, root, zoom in (("320 root", 320, True, None), ("412 text", 412, False, "text")):
            ctx = _ctx(browser, server, sid, width, root=root)
            page = ctx.new_page()
            for path in TALL_BOXES:
                _goto(page, server, path, zoom)
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(BOX_TITLE)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- NA2 and P-7: the keyboard shortcuts box -----------------------------------------------------

KEY_PAIRS = """() => {
  const dl = document.querySelector('.mtgbox dl.keys');
  if (!dl) return ['no shortcuts box'];
  const dts = [...dl.querySelectorAll('dt')], out = [];
  const em = parseFloat(getComputedStyle(dl).fontSize);
  dts.forEach((dt, i) => {
    let dd = dt.nextElementSibling;
    if (!dd || dd.tagName !== 'DD') { out.push('no words after ' + dt.textContent); return; }
    const k = dt.getBoundingClientRect(), d = dd.getBoundingClientRect();
    const key = dt.textContent.trim();
    const beside = Math.abs(k.top - d.top) < 0.6 * em && d.left >= k.right - 1;
    const under = d.top >= k.bottom - 1 && d.left <= k.left + 1;
    if (!beside && !under) out.push('"' + key + '" is not beside or above its words');
    // the words beside their key have 12em at least (P-7: an 89 px column broke every word)
    if (beside && d.width < 12 * em - 1) out.push('"' + key + '" words squeezed to ' + Math.round(d.width));
    // nothing of the next pair on this pair's lines
    const nx = dts[i + 1];
    if (nx) {
      const n = nx.getBoundingClientRect();
      if (n.top < Math.max(k.bottom, d.bottom) - 1)
        out.push('"' + nx.textContent.trim() + '" sits beside the words of "' + key + '"');
    }
  });
  return out;
}"""


@pytest.mark.parametrize("width", [320, 412, 1280])
def test_each_key_sits_with_its_own_words(server: Server, site, width: int) -> None:  # noqa: F811
    """NA2: at 100% text on a 412 phone and a 1280 computer the shortcuts box drew each key
    beside the previous key's words ("Esc" beside "Search cards and decks"). P-7: the words are
    never squeezed into a narrow column; where the row is too narrow, they go under their key."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, root, zoom in (("100%", False, None), ("root", True, None), ("text", False, "text")):
            ctx = _ctx(browser, server, sid, width, root=root, touch=False)
            page = ctx.new_page()
            _goto(page, server, "/decks")
            page.keyboard.press("?")
            page.wait_for_selector(".mtgbox dl.keys")
            if zoom == "text":
                page.evaluate(TEXT_200)
            redrawn(page)
            problems += [f"{width} {tag}: {f}" for f in page.evaluate(KEY_PAIRS)]
            if width == 320 and tag != "100%":
                stacked = page.evaluate(
                    "() => [...document.querySelectorAll('.mtgbox dl.keys dt')].every(dt =>"
                    " dt.nextElementSibling.getBoundingClientRect().top >= dt.getBoundingClientRect().bottom - 1)"
                )
                assert stacked, f"{width} {tag}: a key's words should go under it on a phone at 200%"
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- NA3: the tab bar after an in-place text size change ----------------------------------------

TAB_LABELS = """() => {
  const bar = document.querySelector('.tabbar');
  const tabs = [...bar.querySelectorAll(':scope > a, :scope > details.more > summary')];
  const out = [];
  if (bar.classList.contains('icons')) return out;
  const lbls = tabs.map(t => t.querySelector('.lbl')).filter(l => l && l.getBoundingClientRect().width > 1);
  // the words' own extent (a label is as wide as its tab at most; its words may run past it)
  const words = (l) => { const g = document.createRange(); g.selectNodeContents(l); return g.getBoundingClientRect(); };
  lbls.forEach((l, i) => {
    const r = words(l), tab = l.closest('a, summary').getBoundingClientRect();
    if (r.left < tab.left - 1 || r.right > tab.right + 1)
      out.push('"' + l.textContent.trim() + '" runs out of its tab (' + Math.round(r.width) + ' in ' +
        Math.round(tab.width) + ')');
    const n = lbls[i + 1];
    if (n && words(n).left < r.right + 2) out.push('"' + l.textContent.trim() + '" touches the next');
  });
  return out;
}"""


def test_the_tab_bar_refits_when_the_text_size_changes_in_place(server: Server, site) -> None:  # noqa: F811
    """NA3: the text size changed with no reload and no resize (a browser's text setting) left
    the tab bar's labels running into each other ("HomeDecksBinderAssistMore"); the bar re-fits
    when its labels' size changes, and shows them again when the text is back to its size."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        root = "() => document.documentElement.style.setProperty('font-size', '28px', 'important')"
        for width in (320, 412):
            ctx = _ctx(browser, server, sid, width)
            page = ctx.new_page()
            for how, zoom in (("every font doubled", TEXT_200), ("a 28 px root", root)):
                _goto(page, server, "/account")
                assert page.evaluate(TAB_LABELS) == []
                assert not page.evaluate(
                    "() => document.querySelector('.tabbar').classList.contains('icons')"
                )
                page.evaluate(zoom)
                redrawn(page)
                assert page.evaluate(TAB_LABELS) == [], (width, how)
                # back to the usual size in place: the names come back
                page.evaluate(
                    "() => { for (const e of [document.documentElement, ...document.querySelectorAll("
                    "'body, body *')]) e.style.removeProperty('font-size'); }"
                )
                redrawn(page)
                assert not page.evaluate(
                    "() => document.querySelector('.tabbar').classList.contains('icons')"
                ), (width, how)
            ctx.close()
        browser.close()


# -- NA4: switches in balanced rows of equal parts ----------------------------------------------

SEG_ROWS = """() => {
  const out = [];
  for (const s of document.querySelectorAll('.hubseg, [role=tablist]')) {
    const kids = [...s.children].filter(k => k.getClientRects().length &&
      getComputedStyle(k).position !== 'absolute');
    if (kids.length < 2) continue;
    const sr = s.getBoundingClientRect();
    const rows = new Map();
    for (const k of kids) {
      const r = k.getBoundingClientRect();
      if (r.left < sr.left - 1 || r.right > sr.right + 1) out.push(k.textContent.trim() + ' cut off');
      const t = Math.round(r.top);
      const key = [...rows.keys()].find(x => Math.abs(x - t) < 3);
      if (key === undefined) rows.set(t, [r]); else rows.get(key).push(r);
    }
    if (s.scrollWidth > s.clientWidth + 1) out.push('scrolls sideways');
    const counts = [...rows.values()].map(r => r.length);
    const name = (s.getAttribute('aria-label') || s.className) + ' (' + kids.map(k => k.textContent.trim()).join('|') + ')';
    if (Math.max(...counts) - Math.min(...counts) > 1 || (Math.max(...counts) > 1 && counts[counts.length - 1] === 1))
      out.push(name + ' rows of ' + counts.join('+'));
    const ws = kids.map(k => k.getBoundingClientRect().width);
    if (Math.max(...ws) - Math.min(...ws) > 2) out.push(name + ' parts of ' + ws.map(Math.round).join('/') + ' px');
  }
  return out;
}"""


def test_switches_keep_equal_parts_in_balanced_rows(server: Server, site) -> None:  # noqa: F811
    """NA4: the switches' parts lost their equal widths and one could sit alone on a row ("Card
    models" at 412 px, "Watchlist" at 320 px with a 28 px root). Each part is as wide as the
    others and the rows hold as many parts each (one apart at most); none scrolls or is cut."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, width, root in (
            ("412", 412, False),
            ("320 root", 320, True),
            ("1280", 1280, False),
            ("360", 360, False),
        ):
            ctx = _ctx(browser, server, sid, width, root=root)
            page = ctx.new_page()
            for path in ("/admin/users", "/admin/settings", "/watchlist", "/collection", "/decks/42/export"):
                _goto(page, server, path)
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(SEG_ROWS)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- NA5 and AD2: the tab bar's count -------------------------------------------------------------

BADGE = """(textZoom) => {
  const nb = document.querySelector('.tabbar > a .nb');
  if (!nb) return ['no count'];
  const tab = nb.closest('a'), svg = tab.querySelector('svg'), bar = tab.closest('.tabbar');
  const b = nb.getBoundingClientRect(), i = svg.getBoundingClientRect(), t = tab.getBoundingClientRect();
  const r = bar.getBoundingClientRect(), out = [];
  if (b.top < r.top - 0.5 || b.top < t.top - 0.5) out.push('the count rises above the bar by ' + Math.round(r.top - b.top));
  if (b.left < t.left - 0.5 || b.right > t.right + 0.5) out.push('the count runs out of its tab');
  const cx = (i.left + i.right) / 2, cy = (i.top + i.bottom) / 2;
  if (cx >= b.left && cx <= b.right && cy >= b.top && cy <= b.bottom) out.push("the count covers the icon's centre");
  const ox = Math.max(0, Math.min(b.right, i.right) - Math.max(b.left, i.left));
  const oy = Math.max(0, Math.min(b.bottom, i.bottom) - Math.max(b.top, i.top));
  // a capped size: no taller than the 22 px icon's corner allows (NA5: 25 px at a 28 px root);
  // under a text-only zoom (which scales px text too) its box follows its own line
  const fs = parseFloat(getComputedStyle(nb).fontSize);
  if (b.height > (textZoom ? Math.max(20.5, fs * 1.25) : 20.5)) out.push('the count is ' + Math.round(b.height) + 'px tall');
  if (ox * oy > 0.3 * i.width * i.height) out.push('the count covers ' + Math.round(100 * ox * oy / (i.width * i.height)) +
    '% of the icon');
  return out;
}"""


@pytest.mark.parametrize("waiting", [1, 15, 120])
def test_the_tab_bar_count_stays_inside_its_tab_and_off_the_icon(
    server: Server,  # noqa: F811
    site,
    waiting: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NA5: at a 28 px root the Assistant tab's count grew over most of its icon. AD2: with the
    bar showing icons alone at 200% on a phone, the red count rose half over the bar's top edge,
    over the page. The count keeps a capped size inside its tab, on the icon's corner only, for
    one digit, two and "99+"."""
    from playwright.sync_api import sync_playwright

    from mtg_gateway import theme

    monkeypatch.setattr(theme, "WAITING_COUNT", lambda sub: waiting)
    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, width, root, zoom, app in (
            ("412", 412, False, None, False),
            ("320 root", 320, True, None, False),
            ("412 root", 412, True, None, False),
            ("412 text", 412, False, "text", False),
            ("360 app root", 360, True, None, True),
        ):
            ctx = _ctx(browser, server, sid, width, root=root, app=app)
            page = ctx.new_page()
            for path in ("/precons", "/decks"):
                _goto(page, server, path, zoom)
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(BADGE, zoom == "text")]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- P-8: the watchlist's "In binder" pill --------------------------------------------------------


def _binder_holds_the_watched_cards(server: Server) -> None:  # noqa: F811
    """The Binder as last read whole: three copies of each watched card."""
    with server.db.tx() as c:
        for i, name in enumerate(CARDS[-2:]):
            c.execute(
                "INSERT INTO binder_records (sub, rid, scryfall_id, name, finish, quantity, row_json) "
                "VALUES ('user-1', ?, '', ?, 'nonfoil', 3, '{}')",
                (900 + i, name),
            )


@pytest.mark.parametrize("width", [320, 360, 390, 412, 430])
def test_the_in_binder_pill_keeps_to_one_line(server: Server, site, width: int) -> None:  # noqa: F811
    """P-8: "In binder · you own 3" wrapped to two lines at 390 and 412 px at 100% text."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    _binder_holds_the_watched_cards(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, width)
        page = ctx.new_page()
        _goto(page, server, "/watchlist")
        lines = page.evaluate(
            "() => [...document.querySelectorAll('.owned')].filter(e => e.getClientRects().length).map(e => {"
            " const w = document.createTreeWalker(e, NodeFilter.SHOW_TEXT), tops = [];"
            " for (let n = w.nextNode(); n; n = w.nextNode()) { const r = document.createRange();"
            "  r.selectNodeContents(n); for (const x of r.getClientRects()) if (x.width > 0) tops.push(x.top); }"
            " return tops.filter((t, i) => tops.findIndex(u => Math.abs(u - t) < 4) === i).length; })"
        )
        assert lines and all(n == 1 for n in lines), lines
        ctx.close()
        browser.close()


# -- P-15: a dot (or a count's spaced slash) between two facts never ends or starts a line -------

# Every "·" and every " / " drawn as text under ``root``: the nearest letter before it and the
# nearest after it (in its block, across inline elements) must sit on its own line. The site
# crawl (test_site_checks_browser) runs the same function on every page.
DANGLING = r"""(root) => {
  const out = [];
  const SKIP = 'code,pre,kbd,script,style,noscript,textarea,option,svg,.sr-only,[hidden],[aria-hidden=true]';
  const blockOf = (el) => {
    while (el && el !== root && getComputedStyle(el).display.startsWith('inline')) el = el.parentElement;
    return el || root;
  };
  const tw = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  const done = new Set();
  for (let n = tw.nextNode(); n; n = tw.nextNode()) {
    if (!/·| \/ /.test(n.textContent)) continue;
    const p = n.parentElement;
    if (!p || p.closest(SKIP) || !p.getClientRects().length) continue;
    const block = blockOf(p);
    if (done.has(block)) continue;
    done.add(block);
    // the block's characters in order, each with its text node and offset (shown text only)
    const chars = [];
    const bw = document.createTreeWalker(block, NodeFilter.SHOW_TEXT);
    for (let m = bw.nextNode(); m; m = bw.nextNode()) {
      const mp = m.parentElement;
      if (!mp || mp.closest(SKIP) || !mp.getClientRects().length) continue;
      if (blockOf(mp) !== block) continue;
      for (let i = 0; i < m.textContent.length; i++) chars.push([m, i, m.textContent[i]]);
    }
    const box = (k) => {
      const r = document.createRange(); r.setStart(chars[k][0], chars[k][1]); r.setEnd(chars[k][0], chars[k][1] + 1);
      const rs = [...r.getClientRects()].filter(x => x.width > 0 || x.height > 0);
      return rs.length ? rs[0] : null;
    };
    for (let k = 0; k < chars.length; k++) {
      const c = chars[k][2];
      const slash = c === '/' && k > 0 && k < chars.length - 1 && /\s/.test(chars[k - 1][2]) &&
        /\s/.test(chars[k + 1][2]);
      if (c !== '·' && !slash) continue;
      const me = box(k);
      if (!me) continue;
      let a = k - 1, b = k + 1;
      while (a >= 0 && /\s/.test(chars[a][2])) a--;
      while (b < chars.length && /\s/.test(chars[b][2])) b++;
      const half = me.height / 2;
      const ra = a >= 0 ? box(a) : null, rb = b < chars.length ? box(b) : null;
      const around = (j) => chars.slice(Math.max(0, j - 14), j + 14).map(x => x[2]).join('').replace(/\s+/g, ' ');
      if (ra && Math.abs(ra.top - me.top) > half)
        out.push('"' + c + '" starts a line: "' + around(k).trim() + '"');
      else if (rb && Math.abs(rb.top - me.top) > half)
        out.push('"' + c + '" ends a line: "' + around(k).trim() + '"');
    }
  }
  return out;
}"""

DOT_PLACES = [
    "/",
    "/account",
    "/account/details",
    "/collection",
    "/collection/lists",
    "/collection/wants",
    "/decks/42/contest",
    "/watchlist",
    "/decks/42",
    "/history",
]


@pytest.mark.parametrize("variant", ["320 root", "320 text", "360 text", "360 app root", "412 root", "412 app text"])
def test_no_dot_between_facts_dangles_at_a_line_end_or_start(server: Server, site, variant: str) -> None:  # noqa: F811
    """P-15: a "·" between facts was left at a line's end or start in seven places ("Archidekt:
    alice ·" on Account, "alice@example.test ·", "10 entries ·", "English ·", "· next expir…" on
    Home) and the counters "0 / 150" and "0 / 5000" broke after their slash."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    width = int(variant.split()[0])
    root, app, zoom = "root" in variant, "app" in variant, "text" if "text" in variant else None
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, width, root=root, app=app)
        page = ctx.new_page()
        for path in DOT_PLACES:
            _goto(page, server, path, zoom)
            problems += [f"{path}: {f}" for f in page.evaluate(f"() => ({DANGLING})(document.body)")]
        ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- P-10: the admin settings as a settings list -------------------------------------------------

# A block whose whole text is "Default" (a lone status line under a setting), and each On/Off
# setting whose control is not at the end of its name's row
SETTINGS_LIST = """() => {
  const out = [];
  for (const e of document.querySelectorAll('main *')) {
    if (!e.getClientRects().length || e.closest('.sr-only')) continue;
    if (getComputedStyle(e).display.startsWith('inline')) continue;
    if (/^Default\\.?$/.test((e.innerText || '').trim())) out.push('a lone "Default" line in ' + e.className);
  }
  for (const row of document.querySelectorAll('.set-row')) {
    if (!row.getClientRects().length || row.closest('details:not([open])')) continue;
    const sw = row.querySelector('input[type=checkbox][name^=v_], input[type=radio][value=off]');
    if (!sw) continue;
    const lab = row.querySelector('.set-label').getBoundingClientRect();
    const ctl = (sw.closest('label') || sw).getBoundingClientRect();
    if (ctl.top > lab.bottom || ctl.left < lab.right) out.push(row.id + ': its On/Off is not at the end of its row');
  }
  return out;
}"""


def test_the_admin_settings_are_a_settings_list(server: Server, site) -> None:  # noqa: F811
    """P-10: /admin/settings was form panels with 35 lone "Default" lines; it is the deck settings'
    style: each setting's name and help with its control beside them, an On/Off one a switch at
    the end of its row, and no line saying "Default"."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width in (412, 1280):
            ctx = _ctx(browser, server, sid, width)
            page = ctx.new_page()
            _goto(page, server, "/admin/settings")
            problems += [f"@{width}: {f}" for f in page.evaluate(SETTINGS_LIST)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems[:20])


# -- P-16: the deck header meets the top bar on a phone ----------------------------------------


@pytest.mark.parametrize("path", ["/decks/42", "/decks/42?view=grid", "/decks/44"])
def test_the_deck_header_meets_the_top_bar_on_a_phone(server: Server, site, path: str) -> None:  # noqa: F811
    """P-16: a 14 px band of the page's colour sat between the top bar and the deck header on a
    360 px phone (gone at 717 and 1280)."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width in (360, 412, 717, 1280):
            ctx = _ctx(browser, server, sid, width)
            page = ctx.new_page()
            _goto(page, server, path)
            gap = page.evaluate(
                "() => document.querySelector('main .banner').getBoundingClientRect().top -"
                " document.querySelector('.topbar').getBoundingClientRect().bottom"
            )
            assert abs(gap) <= 1, (width, gap)
            ctx.close()
        browser.close()


# -- P-11: an action link in a line of help is a 44 px target ------------------------------------


def test_the_inline_action_links_are_44_px_targets(server: Server, site) -> None:  # noqa: F811
    """P-11: "Manage templates" in the deck settings' Add template box was 105x15 px, and "Make
    one" in the Binder card's Details box is the same markup; every such action closing a line of
    help is a capsule with a 44 px tap area."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, 360)
        page = ctx.new_page()
        _goto(page, server, "/decks/42/settings")
        page.evaluate(
            "() => { const a = document.querySelector('a[href=\"/lists/templates\"]');"
            " const b = a.closest('.ui-box'); if (b && window.MtgBox) window.MtgBox.openElement(b.id); }"
        )
        page.wait_for_selector(".mtgbox a[href='/lists/templates']")
        redrawn(page)
        r = page.locator(".mtgbox a[href='/lists/templates']").bounding_box()
        assert r and r["height"] >= 43.5 and r["width"] >= 43.5, r
        ctx.close()
        browser.close()


# -- P-14: a long value breaks only at a sensible point -----------------------------------------

# Every word of letters joined by "_", "@" or "." (a variable's name, an e-mail address) drawn on
# more than one line: each break must come right after one of those marks
TOKEN_BREAKS = r"""() => {
  const out = [];
  const tw = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = tw.nextNode(); n; n = tw.nextNode()) {
    const p = n.parentElement;
    if (!p || !p.getClientRects().length || p.closest('.sr-only,[hidden],script,style')) continue;
    const re = /[A-Za-z0-9]+(?:[_@.][A-Za-z0-9]+)+/g;
    let m;
    while ((m = re.exec(n.textContent))) {
      let top = null;
      for (let i = 0; i < m[0].length; i++) {
        const r = document.createRange(); r.setStart(n, m.index + i); r.setEnd(n, m.index + i + 1);
        const b = [...r.getClientRects()].find(x => x.width > 0);
        if (!b) continue;
        if (top !== null && Math.abs(b.top - top) > 2 && !/[_@.]/.test(m[0][i - 1]))
          out.push('"' + m[0] + '" breaks at "' + m[0].slice(0, i) + '|' + m[0].slice(i) + '"');
        top = b.top;
      }
    }
  }
  return out;
}"""


def test_long_values_break_only_after_a_mark(server: Server, site) -> None:  # noqa: F811
    """P-14: /admin broke "MTG_BACKUP_C/OPY_DIR" and Account details "alice@example.tes/t" at 320 px
    with 200% text; a variable's name breaks only after "_", an address after "@" or "."."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, width, root, zoom in (("320 root", 320, True, None), ("360 text", 360, False, "text"),
                                       ("320 text", 320, False, "text"), ("412 root", 412, True, None)):
            ctx = _ctx(browser, server, sid, width, root=root)
            page = ctx.new_page()
            for path in ("/admin", "/account/details", "/admin/users", "/account"):
                _goto(page, server, path, zoom)
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(TOKEN_BREAKS)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- P-1: a heading word wider than its whole line shrinks to fit ---------------------------------

HEADING_WORDS = r"""() => {
  const out = [];
  for (const h of document.querySelectorAll('h1, h2, h3')) {
    if (!h.getClientRects().length || h.closest('.sr-only,[hidden]')) continue;
    const tw = document.createTreeWalker(h, NodeFilter.SHOW_TEXT);
    for (let n = tw.nextNode(); n; n = tw.nextNode()) {
      const re = /\S+/g; let m;
      while ((m = re.exec(n.textContent))) {
        const r = document.createRange(); r.setStart(n, m.index); r.setEnd(n, m.index + m[0].length);
        const rs = [...r.getClientRects()].filter(x => x.width > 0);
        if (rs.length > 1 && Math.abs(rs[0].top - rs[rs.length - 1].top) > 2) out.push('"' + m[0] + '" breaks');
      }
    }
  }
  return out;
}"""


def test_a_heading_word_wider_than_its_line_shrinks_to_fit(server: Server, site) -> None:  # noqa: F811
    """P-1: with every font doubled (a phone's font scale) a heading word wider than its whole line
    ("Preconstructed" 424 px in a 288 px line on /precons) broke bare; the heading shrinks to fit
    it, with no hyphen."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width in (320, 412):
            ctx = _ctx(browser, server, sid, width)
            page = ctx.new_page()
            for path in ("/precons", "/account/details", "/account/assistants", "/notifications"):
                _goto(page, server, path, "text")
                problems += [f"{path} @{width}: {f}" for f in page.evaluate(HEADING_WORDS)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- P-13: a wrapped icon button's content sits in its middle -------------------------------------

ICON_BUTTONS = r"""() => {
  const out = [];
  for (const b of document.querySelectorAll('button, .btn')) {
    const svg = b.querySelector(':scope > svg');
    // (an icon hidden at large text leaves a label alone, centred as any)
    if (!svg || !svg.getBoundingClientRect().width || !b.getClientRects().length || b.closest('[hidden]')) continue;
    const tops = [], rights = [];
    for (const n of b.childNodes) {
      if (n.nodeType !== 3 && !(n.nodeType === 1 && n.tagName === 'SPAN' && !n.classList.contains('sr-only'))) continue;
      const r = document.createRange(); r.selectNodeContents(n);
      for (const x of r.getClientRects()) if (x.width > 0) { tops.push(Math.round(x.top)); rights.push(x.right); }
    }
    if (new Set(tops).size < 2) continue;  // a one-line label
    const cs = getComputedStyle(b), br = b.getBoundingClientRect();
    const left = svg.getBoundingClientRect().left - br.left - parseFloat(cs.borderLeftWidth);
    const right = br.right - parseFloat(cs.borderRightWidth) - Math.max(...rights);
    if (Math.abs(left - right) > 6)
      out.push('"' + b.textContent.trim().slice(0, 30) + '" ' + Math.round(left) + ' left, ' + Math.round(right) + ' right');
  }
  return out;
}"""


def test_a_wrapped_icon_buttons_content_is_centred(server: Server, site) -> None:  # noqa: F811
    """P-13: a button whose label wraps kept its icon by the label but sat left of centre ("Open on
    Archidekt" 29 px left, 123 px right); the icon and label are centred together."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, width, root, zoom in (("320 root", 320, True, None), ("360 text", 360, False, "text"),
                                       ("412 text", 412, False, "text")):
            ctx = _ctx(browser, server, sid, width, root=root)
            page = ctx.new_page()
            for path in ("/decks/42", "/forum", "/watchlist", "/decks/42/edit", "/collection", "/decks"):
                _goto(page, server, path, zoom)
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(ICON_BUTTONS)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- AD1: the report charts' labels grow with the text ---------------------------------------------

CHART_TEXT = """() => [...document.querySelectorAll('.chart svg')].filter(s => s.getClientRects().length).map(s => {
  const vb = s.viewBox.baseVal, scale = s.getBoundingClientRect().width / vb.width;
  return [...s.querySelectorAll('text')].map(t => parseFloat(getComputedStyle(t).fontSize) * scale);
})"""


def test_the_report_charts_labels_grow_with_the_text(server: Server, site) -> None:  # noqa: F811
    """AD1: the report charts' axis and bar labels ("0%", "T1", "T10", the values) were 11 px in the
    chart's own units, so at 200% text they stayed small beside 32 px words; they grow with it."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width in (360, 1024):
            sizes = {}
            for root in (False, True):
                ctx = _ctx(browser, server, sid, width, root=root)
                page = ctx.new_page()
                _goto(page, server, "/history/reports/r1")
                page.evaluate("() => document.querySelectorAll('details').forEach(d => { d.open = true; })")
                redrawn(page)
                sizes[root] = page.evaluate(CHART_TEXT)
                ctx.close()
            assert sizes[False] and len(sizes[False]) == len(sizes[True]), sizes
            for small, large in zip(sizes[False], sizes[True], strict=True):
                assert small and len(small) == len(large)
                # each label at a 28 px root is 1.8 times its size at 14 px and more (at the chart's
                # width then)
                assert all(b / a >= 1.8 for a, b in zip(small, large, strict=True)), (width, small, large)
        browser.close()


# -- P-5, P-19, AD3: text boxes grow with their text, and their hints fit ------------------------

# Every shown text box whose text would touch or pass its edges: less room inside than its
# font's line (a fixed 44 px box at 200% text)
BOX_ROOM = """() => {
  const out = [];
  const sel = 'input:not([type]),input[type=text],input[type=search],input[type=email],input[type=number],' +
    'input[type=url],input[type=password],select';
  for (const el of document.querySelectorAll(sel)) {
    if (!el.getClientRects().length || el.closest('[hidden],.sr-only') || el.classList.contains('msel-native')) continue;
    const cs = getComputedStyle(el);
    if (cs.opacity === '0' || cs.visibility === 'hidden') continue;
    // the inside of the box against its text's line and some air over and under it
    const room = el.clientHeight;
    const line = parseFloat(cs.fontSize) * 1.5;
    if (room < line - 1) out.push((el.name || el.id || el.placeholder || el.tagName) + ': ' + Math.round(room) +
      'px for a ' + Math.round(line) + 'px line');
  }
  return out;
}"""

HINT_PLACES = [
    ("320 root", 320, True, None, False, ["/cards", "/search", "/decks/42/contest", "/lists/custom?new=1"]),
    ("320", 320, False, None, False, ["/decks/42/contest", "/lists/custom?new=1", "/cards"]),
    ("412 text", 412, False, "text", False, ["/cards", "/search", "/decks/42/contest", "/lists/custom?new=1"]),
    ("717 app root", 717, True, None, True, ["/", "/decks", "/cards"]),
    ("900 browser", 900, False, "browser", False, ["/search", "/cards", "/decks"]),
]


@pytest.mark.parametrize("place", HINT_PLACES, ids=[p[0] for p in HINT_PLACES])
def test_text_boxes_hold_their_text_and_hints_at_every_size(server: Server, site, place: tuple) -> None:  # noqa: F811
    """P-5, P-19: hints were cut at 200% text ("Creature, Goblir" in the card search's filters,
    "From", the set and Is boxes, the search box, the contest's link and words, /search's "Name or
    link") and at 320 px ("Legendary" in a custom card, the contest's link), and the top bar's
    search at 717 px in the app. AD3: a text box was 44 px tall at every size, so at 200% its text
    touched or passed its edges. Each box grows with its text from 44 px, and each hint fits."""
    from playwright.sync_api import sync_playwright

    from .test_ui_standard_0746_browser import PLACEHOLDERS

    tag, width, root, zoom, app, paths = place
    exe, sid, _ = site
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, width, root=root, app=app)
        page = ctx.new_page()
        for path in paths:
            _goto(page, server, path, zoom)
            problems += [f"{path}: {f}" for f in page.evaluate(PLACEHOLDERS) + page.evaluate(BOX_ROOM)]
            if page.locator(".sp-open").first.is_visible():  # the filter sheet on a phone, opened
                page.locator(".sp-open").first.click()
                redrawn(page)
                if page.locator(".sp-filters.open").count():
                    found = page.evaluate(PLACEHOLDERS) + page.evaluate(BOX_ROOM)
                    problems += [f"{path} sheet: {f}" for f in found]
                    page.keyboard.press("Escape")
        ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)
