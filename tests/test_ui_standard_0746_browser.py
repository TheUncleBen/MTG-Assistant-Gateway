"""The UI standard walk of the 0.7.46 audit, measured on every page: placeholders that fit their
box (a phone, the app, and 200% text), tap targets of 44 px in the app, the phone tab bar's
labels whole or hidden behind their icons, switches and tab rows on one row, and the stats
tables, card grids, deck rows and report lists never breaking a word in two at 200% text. On
the overflow sweep's hostile data (test_overflow_sweep_browser). Skipped without a Chromium
build."""

from __future__ import annotations

import re

import pytest

from .test_editor_browser import PNG
from .test_overflow_sweep_browser import FOLDERS, MEASURE, _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_text_zoom_browser import TEXT_200
from .waits import expect, redrawn, settled

pytest.importorskip("playwright")

APP_UA = (
    "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Mobile Safari/537.36 MTGAssistant/0.7.46"
)
APP_412 = dict(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True, user_agent=APP_UA)
WEB_360 = dict(viewport={"width": 360, "height": 800}, has_touch=True, is_mobile=True)

PAGES = [
    "/",
    "/decks",
    "/decks?view=list",
    "/decks/42",
    "/decks/42?view=grid",
    "/decks/42?view=table",
    "/decks/50",
    "/decks/50/edit",
    "/decks/42/edit",
    "/decks/42/settings",
    "/decks/new",
    "/decks/new-package",
    "/decks/42/export",
    "/decks/42/compare",
    "/decks/42/goldfish",
    "/decks/42/package",
    "/decks/42/delete",
    f"/decks?folder={FOLDERS[0]}",
    "/collection?view=grid",
    "/collection?view=list",
    "/collection/lists",
    "/collection/wants",
    "/lists/custom",
    "/lists/custom?new=1",
    "/watchlist",
    "/search",
    "/cards",
    "/precons",
    "/forum",
    "/users/alice",
    "/notifications",
    "/history",
    "/history/reports/r1",
    "/history/models",
    "/reports",
    "/proposals",
    "/activity",
    "/account",
    "/account/details",
    "/account/assistants",
    "/guide",
    "/guide?q=deck",
    "/scan",
    "/install",
    "/admin",
    "/admin/users",
    "/admin/models",
    "/admin/activity",
    "/admin/settings",
    "/admin/metrics",
]

# Shared: whether an element is shown (not display:none, hidden, inside a closed <details>...)
VISIBLE = """
  const visible = (el) => {
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || parseFloat(cs.opacity) === 0) return false;
    const r = el.getBoundingClientRect();
    if (!(r.width > 0 && r.height > 0)) return false;
    if (el.closest('[hidden],[aria-hidden=true]')) return false;
    for (let d = el.parentElement && el.parentElement.closest('details:not([open])'); d;
         d = d.parentElement && d.parentElement.closest('details:not([open])')) {
      const sm = d.querySelector(':scope > summary');
      if (!(sm && sm.contains(el))) return false;
    }
    return true;
  };
  const name = (el) => {
    let s = el.tagName.toLowerCase();
    if (el.id) s += '#' + el.id;
    else if (el.classList.length) s += '.' + [...el.classList].slice(0, 3).join('.');
    const t = (el.getAttribute('aria-label') || el.placeholder || el.innerText || el.value || '')
      .trim().slice(0, 40);
    return t ? s + ' "' + t + '"' : s;
  };
"""

# Every shown text box's placeholder, measured with the box's own font, against the box's inner width.
PLACEHOLDERS = (
    "() => {"
    + VISIBLE
    + """
  const out = [];
  const canvas = document.createElement('canvas').getContext('2d');
  for (const el of document.querySelectorAll('input[placeholder]')) {
    if (!visible(el)) continue;
    const cs = getComputedStyle(el);
    canvas.font = cs.fontStyle + ' ' + cs.fontWeight + ' ' + cs.fontSize + ' ' + cs.fontFamily;
    const need = canvas.measureText(el.placeholder).width;
    const avail = el.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    if (need > avail + 1) out.push(name(el) + ' needs ' + Math.round(need) + 'px, has ' + Math.round(avail));
  }
  return out;
}"""
)

# Every shown control's hit box in the app: 44 px each way. A checkbox, radio or file box inside a
# label is hit through the label, so the label's box counts; a label of a text box is not a
# control (the box is); a visually hidden textarea (a copy source) and the skip link (keyboard
# only) are not tapped.
TARGETS = (
    "() => {"
    + VISIBLE
    + """
  const out = [];
  const sel = 'a[href], button, input:not([type=hidden]), select, textarea, summary, [role=button], '
    + '[role=tab]';
  const box = (el) => {
    const r = el.getBoundingClientRect();
    let w = r.width, h = r.height;
    const a = getComputedStyle(el, '::after');  // an ::after hit area counts
    if (a.content !== 'none' && a.position === 'absolute') {
      const t = parseFloat(a.top), b = parseFloat(a.bottom), l = parseFloat(a.left), rr = parseFloat(a.right);
      h = Math.max(h, r.height - (isNaN(t) ? 0 : t) - (isNaN(b) ? 0 : b));
      w = Math.max(w, r.width - (isNaN(l) ? 0 : l) - (isNaN(rr) ? 0 : rr));
    }
    return {w, h};
  };
  for (const el of document.querySelectorAll(sel)) {
    if (!visible(el)) continue;
    if (el.matches('a.skip, .sr-only, textarea.sr-only')) continue;
    let hit = el;
    if (el.matches('input[type=checkbox], input[type=radio], input[type=file]')) {
      const lab = el.closest('label')
        || (el.id && document.querySelector('label[for="' + CSS.escape(el.id) + '"]'));
      if (lab) hit = lab;
    }
    const {w, h} = box(hit);
    if (w < 43.5 || h < 43.5) out.push(name(el) + ' ' + Math.round(w) + 'x' + Math.round(h));
  }
  return out;
}"""
)

# A word (letters only, 4 to 12 of them: the hostile data's unbroken 30-letter names have no
# good break anywhere, and a longer heading word at 200% is wider than a 360 px screen) drawn on
# two lines inside the given elements.
MID_WORD = (
    "(sel) => {"
    + VISIBLE
    + """
  const out = [], seen = new Set();
  for (const root of document.querySelectorAll(sel)) {
    if (!visible(root)) continue;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let n;
    while ((n = walker.nextNode())) {
      const re = /(?<![A-Za-z])[A-Za-z][A-Za-z'’]{3,11}(?![A-Za-z])/g;
      let m;
      while ((m = re.exec(n.nodeValue))) {
        const rg = document.createRange();
        rg.setStart(n, m.index); rg.setEnd(n, m.index + m[0].length);
        const rects = [...rg.getClientRects()].filter(x => x.width > 0);
        if (rects.length > 1 && Math.abs(rects[0].top - rects[rects.length - 1].top) > 2) {
          const key = name(root) + m[0];
          if (!seen.has(key)) { seen.add(key); out.push(name(root) + ': "' + m[0] + '"'); }
        }
      }
    }
  }
  return out;
}"""
)

# A control row's shown children on more than one line.
ROWS = (
    "(sel) => {"
    + VISIBLE
    + """
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (!visible(el)) continue;
    const kids = [...el.children].filter(visible);
    const tops = new Set(kids.map(k => Math.round(k.getBoundingClientRect().top)));
    if (tops.size > 1) out.push(name(el) + ' on ' + tops.size + ' rows');
  }
  return out;
}"""
)

TABBAR = """
() => {
  const bar = document.querySelector('.tabbar');
  if (!bar) return null;
  const tabs = [...bar.querySelectorAll(':scope > a, :scope > details > summary')];
  return {
    icons: bar.classList.contains('icons'),
    names: tabs.map(t => (t.querySelector('.lbl') || t).textContent.trim()),
    cut: tabs.filter(t => { const l = t.querySelector('.lbl'); const cs = l && getComputedStyle(l);
      return l && l.scrollWidth > l.clientWidth + 1 && cs.position !== 'absolute'
        && cs.whiteSpace === 'nowrap'; }).map(t => t.textContent.trim()),  // the rail wraps its labels
    hidden: tabs.filter(t => { const l = t.querySelector('.lbl');
      return l && getComputedStyle(l).position === 'absolute'
        && l.getBoundingClientRect().width <= 1; }).length,
  };
}
"""


def _open(server: Server, ctx, sid: str):  # noqa: F811
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return page, errors


def _goto(server: Server, page, path: str, zoom: str | None = None) -> None:  # noqa: F811
    from playwright.sync_api import Error as PlaywrightError

    server.db.delete_prefs("user-1", prefixes=("view.",))
    try:
        r = page.goto(f"{server.base}{path}", wait_until="networkidle")
    except PlaywrightError as exc:
        # a drop-down the sweep closed gives its history entry back as the next page is asked
        # for, and the browser keeps the page's own back() and drops the sweep's navigation:
        # once that back() has landed, the page is asked for again
        if "ERR_ABORTED" not in str(exc):
            raise
        page.wait_for_load_state("networkidle")
        r = page.goto(f"{server.base}{path}", wait_until="networkidle")
    assert r is not None and r.status == 200, (path, r and r.status)
    if zoom:
        page.evaluate(zoom)
    redrawn(page)


@pytest.fixture
def walk(server: Server):  # noqa: F811
    """The seeded site with its Archidekt allowance lifted: the walk reads many pages in a row."""
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid, pending = _seed(server)
    server.app.state.gateway.decks.archidekt_budget.per_window = 100_000
    return exe, sid, [*PAGES, f"/proposals/{pending}"]


def test_every_placeholder_fits_its_box(server: Server, walk) -> None:  # noqa: F811
    """On a phone, in the app and with 200% text no placeholder is cut ("Precon name, deck li…"):
    the hint is short enough for its box at every size (0.7.46 audit, 23 cut placeholders)."""
    from playwright.sync_api import sync_playwright

    exe, sid, pages = walk
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, kw, zoom in (
            ("app 412", APP_412, None),
            ("web 360", WEB_360, None),
            ("200% 360", WEB_360, TEXT_200),
        ):
            ctx = browser.new_context(**kw)
            page, errors = _open(server, ctx, sid)
            for path in pages:
                _goto(server, page, path, zoom)
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(PLACEHOLDERS)]
            ctx.close()
            assert errors == []
        browser.close()
    assert problems == [], "\n".join(problems)


# Every item of every menu on the page, the drop-downs opened (the top bar's, More, a sort menu)
# and the first row's ⋮ menu (.ctxmenu, the one shared style) opened by menus.js.
MENU_ITEMS = (
    "() => {"
    + VISIBLE
    + """
  const out = [];
  document.querySelectorAll('details.dd:not([open])').forEach(d => { d.open = true; });
  const items = '.menu a, .menu button, .menu .item, .ctxmenu a, .ctxmenu button';
  for (const el of document.querySelectorAll(items)) {
    if (!visible(el)) continue;
    const r = el.getBoundingClientRect();
    if (r.width < 43.5 || r.height < 43.5)
      out.push('menu item ' + name(el) + ' ' + Math.round(r.width) + 'x' + Math.round(r.height));
  }
  document.querySelectorAll('details.dd[open]').forEach(d => { d.open = false; });
  return out;
}"""
)


MOUSE_1280 = dict(viewport={"width": 1280, "height": 860})


@pytest.mark.parametrize("kw", [APP_412, MOUSE_1280], ids=["app 412", "mouse 1280"])
def test_every_control_is_a_44_px_target(server: Server, walk, kw: dict) -> None:  # noqa: F811
    """In the app (touch, 412 px) and on a computer (a mouse, 1280 px) every link, button, box and
    tab is at least 44 px each way: the stats pie and bars, type rows, Undo and Redo, the category
    picker, the Binder's menu button, the watchlist's links and links inside text (0.7.46 audit),
    and every item of every menu (the top bar's, More, the sort menus, a row's ⋮ menu), which
    were 35 px. The owner (2026-11-02): the 44 px rule holds with a mouse too."""
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
    from playwright.sync_api import sync_playwright

    exe, sid, pages = walk
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(**kw)
        page, errors = _open(server, ctx, sid)
        for path in pages:
            _goto(server, page, path)
            problems += [f"{path}: {f}" for f in page.evaluate(TARGETS)]
            # a drop-down whose items a script writes on its first opening (the editor's row
            # menu) is opened once by a tap, so the sweep measures its items too
            opened: set[str] = set()
            for summary in page.locator("details.dd:not(.more) > summary:visible").all():
                kind = summary.evaluate("s => s.parentElement.className")
                if kind in opened:
                    continue
                opened.add(kind)
                summary.click()
                settled(page)
            kebab = page.locator(".kebab").first
            if kebab.count() and kebab.is_visible() and not page.locator(".mtgbox-scrim.in").count():
                before = page.url
                kebab.click()  # a page drawn with a box open keeps its scrim over the rows
                try:
                    page.locator(".ctxmenu[role=menu], details.rowmenu[open] .menu").first.wait_for(
                        timeout=3000
                    )
                except PlaywrightTimeoutError:
                    page.wait_for_load_state("networkidle")
                    if page.url == before:
                        problems.append(f"{path}: the first ⋮ opened no menu")
                    # else the row's ⋮ is its link to the row's own page (admin's card models)
            problems += [f"{path}: {f}" for f in page.evaluate(MENU_ITEMS)]
            # a phone's sheet and a drop-down close through the history entry each pushed: the
            # next page waits for that back() to land, or the browser drops its navigation
            page.keyboard.press("Escape")
            page.wait_for_function(
                "() => !document.querySelector('.ctxmenu, details.dd[open]')"
                " && !(window.MtgDropdowns && window.MtgDropdowns.backPending())"
                " && !(window.MtgMenu && window.MtgMenu.backPending && window.MtgMenu.backPending())"
            )
            page.wait_for_load_state("networkidle")
        browser.close()
    assert errors == []
    more = f"\n… {len(problems)} in all" if len(problems) > 60 else ""
    assert problems == [], "\n".join(problems[:60]) + more


def test_the_tab_bar_keeps_its_names_whole_or_shows_icons_alone(server: Server, walk) -> None:  # noqa: F811
    """At 200% text on a 360 px phone the tab bar's labels no longer fit ("Assi…"): the bar then
    shows the icons alone, each tab still named for a screen reader (Home, Decks, Assistant,
    Binder, Account). At normal size the labels are shown whole."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = walk
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(**WEB_360)
        page, errors = _open(server, ctx, sid)
        _goto(server, page, "/decks")
        page.evaluate("() => document.fonts.ready")
        redrawn(page)  # the bar measured its labels with the loaded fonts
        normal = page.evaluate(TABBAR)
        assert normal and not normal["icons"] and normal["cut"] == [], normal
        assert {"Home", "Decks", "Assistant", "Binder"} <= set(normal["names"]), normal
        # a phone applies its font scale as the page loads, where the bar measures its labels; it
        # measures again on a resize, which stands in for that here
        page.evaluate(TEXT_200)
        page.evaluate("() => window.dispatchEvent(new Event('resize'))")
        redrawn(page)
        large = page.evaluate(TABBAR)
        assert large["icons"] and large["cut"] == [], large
        assert large["hidden"] == len(large["names"]), large
        assert large["names"] == normal["names"], "the names stay, for screen readers"
        # the Fold's rail at the same 200% text: its labels grow with the text too (final polish AC1)
        # and do not fit its 80 px, so it shows the icons alone as well, nothing cut
        page.set_viewport_size({"width": 717, "height": 800})
        redrawn(page)
        back = page.evaluate(TABBAR)
        assert back["cut"] == [], back
        assert back["icons"] and back["names"] == normal["names"], back
        browser.close()
    assert errors == []


# the rows that wrapped at 412 px: Budget, the deck toolbar's main row, the search's sort row
ONE_ROW = ".budget, .toolbar .primary, .spanel .rbar .sort"
# the switches and tab rows (Export's formats, Admin's tabs, the New deck box's tabs, the Binder's
# add box tabs, the Lists tabs): a part that doesn't fit takes the next row whole; none scrolls
# with a part cut off at its edge (final polish P-9)
SWITCHES = ".hubseg, [role=tablist]"
CUT_PARTS = (
    "(sel) => {"
    + VISIBLE
    + """
  const out = [];
  for (const el of document.querySelectorAll(sel)) {
    if (!visible(el)) continue;
    const box = el.getBoundingClientRect();
    if (el.scrollWidth > el.clientWidth + 1) out.push(name(el) + ' scrolls sideways');
    for (const k of [...el.children].filter(visible)) {
      const r = k.getBoundingClientRect();
      if (r.left < box.left - 1 || r.right > box.right + 1) out.push(name(el) + ': a part is cut off');
    }
  }
  return out;
}"""
)


def test_switches_and_tab_rows_stay_on_one_row_in_the_app(server: Server, walk) -> None:  # noqa: F811
    """At 412 px in the app the Budget row, the deck toolbar's main row and the sort row never wrap
    onto a second line; a segmented switch or a tab row shows every part whole, taking a second
    row for a part that doesn't fit rather than scrolling with a part cut off (final polish P-9).
    The deck toolbar's ⋮ stays beside its button at 360 px too."""
    from playwright.sync_api import sync_playwright

    exe, sid, pages = walk
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, kw in (("app 412", APP_412), ("web 360", WEB_360)):
            ctx = browser.new_context(**kw)
            page, errors = _open(server, ctx, sid)
            for path in pages:
                _goto(server, page, path)
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(ROWS, ONE_ROW)]
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(CUT_PARTS, SWITCHES)]
            ctx.close()
            assert errors == []
        browser.close()
    assert problems == [], "\n".join(problems)


# where the audit saw words broken in two at 200% text: the stats tables ("Commander",
# "Enchantment"), the Binder's grid, the deck list's rows, the report's lists, the headings
WORDS_IN = "table.qty, ul.collgrid .name, ul.decklist .name, .rv-page .ig-list, h1, h2, label, .crumbs"


def test_no_word_breaks_in_two_at_200_percent_text(server: Server, walk) -> None:  # noqa: F811
    """With 200% text on a 360 px phone and in the app a table cell, grid tile, row or heading
    wraps between words, never inside one ("Comm-ander", "Push notifi-cations")."""
    from playwright.sync_api import sync_playwright

    exe, sid, pages = walk
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for tag, kw, zoom in (
            ("200% 360", WEB_360, TEXT_200),
            ("200% app 412", APP_412, TEXT_200),
            ("web 390", dict(WEB_360, viewport={"width": 390, "height": 800}), None),
        ):
            ctx = browser.new_context(**kw)
            page, errors = _open(server, ctx, sid)
            for path in pages:
                _goto(server, page, path, zoom)
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(MID_WORD, WORDS_IN)]
            ctx.close()
            assert errors == []
        browser.close()
    assert problems == [], "\n".join(problems)


def test_the_stats_tables_keep_their_words_whole_on_the_narrowest_phones(server: Server, walk) -> None:  # noqa: F811
    """The deck page's stats panel sits in one column on a phone, its type and rarity tables beside
    nothing: with 200% text on a 360 px and on a 320 px phone a type name ("Enchantment",
    "Creature") wraps whole, never inside the word (the tables take half the panel's padding where
    the panel is that narrow in its own text, deckpage.py)."""
    from playwright.sync_api import sync_playwright

    exe, sid, _pages = walk
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width in (360, 320):
            ctx = browser.new_context(**dict(WEB_360, viewport={"width": width, "height": 800}))
            page, errors = _open(server, ctx, sid)
            for path in ("/decks/42", "/decks/42?view=grid", "/decks/42?view=table"):
                _goto(server, page, path, TEXT_200)
                assert page.locator("#stats table.qty td", has_text="Enchantment").count() >= 1, path
                split = page.evaluate(MID_WORD, "#stats table.qty")
                problems += [f"{path} @200% {width}: {f}" for f in split]
                assert page.evaluate(MEASURE) == [], (path, width)
            ctx.close()
            assert errors == []
        browser.close()
    assert problems == [], "\n".join(problems)


# the filter rows (Proposals' four selects, My activity's three, Admin activity's four)
FILTER_ROWS = """
() => [...document.querySelectorAll('.ui-tools')].map(t => {
  const fs = [...t.querySelectorAll('.f')].filter(f => f.getBoundingClientRect().width > 0);
  const w = t.getBoundingClientRect().width;
  return {n: fs.length, rows: new Set(fs.map(f => Math.round(f.getBoundingClientRect().top))).size,
          wide: fs.every(f => f.getBoundingClientRect().width >= w * .9)};
})
"""


def test_filter_rows_stack_to_one_column_at_large_text(server: Server, walk) -> None:  # noqa: F811
    """A page's filter selects sit two a row on a phone; with 200% text each takes a row of its
    own, so no choice is cut to "All…" (0.7.46 audit D31)."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = walk
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(**WEB_360)
        page, errors = _open(server, ctx, sid)
        for path in ("/proposals", "/activity", "/admin/activity"):
            _goto(server, page, path)
            normal = [t for t in page.evaluate(FILTER_ROWS) if t["n"] >= 2]
            assert normal and all(t["rows"] < t["n"] for t in normal), (path, normal)
            page.evaluate(TEXT_200)
            redrawn(page)
            large = [t for t in page.evaluate(FILTER_ROWS) if t["n"] >= 2]
            assert all(t["rows"] == t["n"] and t["wide"] for t in large), (path, large)
        browser.close()
    assert errors == []


def test_a_hold_on_a_binder_tiles_picture_opens_its_menu(server: Server, walk) -> None:  # noqa: F811
    """On a touch screen a press and hold on a Binder tile's picture (most of the tile) opens the
    row's menu, as a hold on a deck tile does, and does not open the card view on the lift
    (0.7.46 audit D08). A short tap still opens the card view."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = walk
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(**APP_412)
        page, errors = _open(server, ctx, sid)
        _goto(server, page, "/collection?view=grid")
        pic = page.locator("ul.collgrid li.c button.thumbbtn").first
        pic.scroll_into_view_if_needed()
        box = pic.bounding_box()
        x, y = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
        cdp = page.context.new_cdp_session(page)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        page.locator(".ctxmenu[role=menu]").wait_for(timeout=3000)  # the hold opened the menu
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        # fixed wait: a negative check: the lift opens no card view within 300 ms
        page.wait_for_timeout(300)
        assert page.locator(".cardview.open").count() == 0, "the lift opened the card view too"
        page.keyboard.press("Escape")
        expect(page.locator(".ctxmenu")).to_have_count(0)
        # fixed wait: the hold's lift swallows a tap in the grid for 400 ms (cardmenu.js's swallow
        # timer, which the page does not show); the tap below must come after it
        page.wait_for_timeout(600)
        assert page.locator(".ctxmenu").count() == 0
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        page.wait_for_selector(".cardview.open", timeout=3000)
        assert page.locator(".ctxmenu").count() == 0
        browser.close()
    assert errors == []
