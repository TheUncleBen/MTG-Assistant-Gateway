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
from .test_overflow_sweep_browser import SHEETS_LOADED, _seed, server  # noqa: F401
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


def _ctx(browser, server: Server, sid: str, width: int, *, root: bool = False, app: bool = False,  # noqa: F811
         touch: bool | None = None, height: int = 860):
    touch = width < 900 if touch is None else touch
    kw = dict(viewport={"width": width, "height": height}, has_touch=touch, reduced_motion="reduce")
    if app:
        kw.update(is_mobile=True, user_agent=APP_UA)
    ctx = browser.new_context(**kw)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(re.compile(r"https://cards\.scryfall\.io/.*"), lambda r: r.fulfill(content_type="image/png", body=PNG))
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
                assert not page.evaluate("() => document.querySelector('.tabbar').classList.contains('icons')")
                page.evaluate(zoom)
                redrawn(page)
                assert page.evaluate(TAB_LABELS) == [], (width, how)
                # back to the usual size in place: the names come back
                page.evaluate("() => { for (const e of [document.documentElement, ...document.querySelectorAll("
                              "'body, body *')]) e.style.removeProperty('font-size'); }")
                redrawn(page)
                assert not page.evaluate("() => document.querySelector('.tabbar').classList.contains('icons')"), (
                    width, how)
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
        for tag, width, root in (("412", 412, False), ("320 root", 320, True), ("1280", 1280, False),
                                 ("360", 360, False)):
            ctx = _ctx(browser, server, sid, width, root=root)
            page = ctx.new_page()
            for path in ("/admin/users", "/admin/settings", "/watchlist", "/collection", "/decks/42/export"):
                _goto(page, server, path)
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(SEG_ROWS)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- NA5 and AD2: the tab bar's count -------------------------------------------------------------

BADGE = """() => {
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
  if (b.height > Math.max(20.5, fs * 1.25)) out.push('the count is ' + Math.round(b.height) + 'px tall');
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
                problems += [f"{path} @{tag}: {f}" for f in page.evaluate(BADGE)]
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# -- P-8: the watchlist's "In binder" pill --------------------------------------------------------


@pytest.mark.parametrize("width", [320, 360, 390, 412, 430])
def test_the_in_binder_pill_keeps_to_one_line(server: Server, site, width: int) -> None:  # noqa: F811
    """P-8: "In binder · you own 3" wrapped to two lines at 390 and 412 px at 100% text."""
    from playwright.sync_api import sync_playwright

    exe, sid, _ = site
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _ctx(browser, server, sid, width)
        page = ctx.new_page()
        _goto(page, server, "/watchlist")
        lines = page.evaluate(
            "() => [...document.querySelectorAll('.owned')].filter(e => e.getClientRects().length).map(e => {"
            " const r = document.createRange(); r.selectNodeContents(e);"
            " return new Set([...r.getClientRects()].filter(x => x.width > 0).map(x => Math.round(x.top))).size; })"
        )
        assert lines and all(n == 1 for n in lines), lines
        ctx.close()
        browser.close()
