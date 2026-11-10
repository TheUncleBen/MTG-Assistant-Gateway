"""The 0.7.46 redesign's shell screens. Screen 1: the open Fold's rail without More (the four
places, a thin line, Scan, Precons, Card models and Forum, Alerts at the foot; it never scrolls at
840×700), the phone's tab bar and More sheet as they were, and a computer's top bar that lists
the same pages instead of More ▾ when it is wide enough. Screen 9: pull to refresh in the Android
app, whose page-side hook says at each touch whether that touch may start a pull, and whose
app side (android/, compiled by CI, not here) offers it on the list pages only."""

from __future__ import annotations

import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from mtg_gateway import theme

from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200
from .waits import redrawn

NAV = {"Accept": "text/html"}
APP_UA = "Mozilla/5.0 (Linux; Android 16) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.7.46"
ROOT = Path(__file__).resolve().parent.parent
ANDROID = ROOT / "android/app/src/main/kotlin/local/mtgassistantgateway/app"
STATIC = Path(theme.__file__).parent / "static"


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.fixture(scope="module")
def chromium():
    pytest.importorskip("playwright")
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        yield browser
        browser.close()


@contextmanager
def _page(
    browser, server: Server, width: int, height: int, *, touch: bool, app: bool = False, theme_: str = "dark"
):
    extra = {"user_agent": APP_UA} if app else {}
    ctx = browser.new_context(
        viewport={"width": width, "height": height}, has_touch=touch, is_mobile=touch, **extra
    )
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": server.sign_in(), "url": server.base},
            {"name": "mtg_theme", "value": theme_, "url": server.base},
        ]
    )
    try:
        yield ctx.new_page()
    finally:
        ctx.close()


def _zoom(page, zoom: str) -> None:
    if zoom == "root":
        page.evaluate(ROOT_200)
    elif zoom == "text":
        page.evaluate(TEXT_200)
    redrawn(page)


def _tabbar(page_html: str) -> str:
    return page_html.split("<nav class='tabbar' aria-label='Sections'>")[1].split("</nav>")[0]


# -- screen 1: the rail's places, from the navigation itself ---------------------------------------


def test_the_rail_lists_every_more_page_but_search_in_order_of_use(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_gateway import card_models

    monkeypatch.setattr(card_models, "SWITCHED_ON", True)
    assert [h for h, _l, _i in theme.rail_extra()] == ["/scan", "/precons", "/history/models", "/forum"]
    more = {h for _g, links in theme.more_groups() for h, _l, _i in links}
    assert more - {h for h, _l, _i in theme.rail_extra()} == {"/search"}  # the top bar's box
    # a page switched off leaves More and the rail together: nothing is listed by hand
    monkeypatch.setattr(card_models, "SWITCHED_ON", False)
    assert [h for h, _l, _i in theme.rail_extra()] == ["/scan", "/precons", "/forum"]
    # the open decisions are one constant each
    assert theme.RAIL_ALERTS_AT_FOOT is True and theme.RAIL_GROUP_LINE is True
    assert theme.TOPBAR_FULL_FROM == "70em"


def test_the_rail_markup_is_four_places_a_line_the_rest_and_alerts_at_the_foot(server: Server) -> None:
    import httpx

    c = httpx.Client(base_url=server.base, cookies={"mtg_session": server.sign_in()}, headers=NAV)
    bar = _tabbar(c.get("/precons").text)
    items = re.findall(
        r"<(a|span) (?:href='([^']+)'|class='(rail-sep[^']*)')(?: class='([^']*)')?", bar.split("<details")[0]
    )
    assert items == [
        ("a", "/", "", ""),
        ("a", "/decks", "", ""),
        ("a", "/binder", "", ""),
        ("a", "/proposals", "", ""),
        ("span", "", "rail-sep railonly", ""),
        ("a", "/scan", "", "railonly"),
        ("a", "/precons", "", "railonly"),
        ("a", "/history/models", "", "railonly"),
        ("a", "/forum", "", "railonly"),
        ("a", "/notifications", "", "railonly bell foot"),
    ]
    assert "<a href='/precons' class='railonly' aria-current=page>" in bar  # the rail marks its page
    # the phone's More sheet is still there, with its three groups
    sheet = bar.split("<div class='menu sheet'>")[1]
    assert re.findall(r"<div class='head'>([^<]+)</div>", sheet) == ["Find", "Your cards", "Community"]
    assert re.findall(r"<a href='([^']+)'", sheet) == [
        "/search",
        "/precons",
        "/scan",
        "/history/models",
        "/forum",
    ]
    assert "Collection" not in sheet and "Binder" not in sheet  # the sheet never renames Binder


def test_each_open_decision_flips_with_its_constant(server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    import httpx

    monkeypatch.setattr(theme, "RAIL_ALERTS_AT_FOOT", False)
    monkeypatch.setattr(theme, "RAIL_GROUP_LINE", False)
    monkeypatch.setattr(theme, "TOPBAR_FULL_FROM", None)
    c = httpx.Client(base_url=server.base, cookies={"mtg_session": server.sign_in()}, headers=NAV)
    page = c.get("/decks").text
    bar = _tabbar(page)
    assert "rail-sep" not in bar and "class='railonly bell'" in bar and " foot" not in bar
    site = page.split("<nav class='site' aria-label='Site'>")[1].split("</nav>")[0]
    assert "xtra" not in site and "<details class='dd navmore'>" in site


def test_the_computer_bar_carries_the_rails_pages_as_links_beside_more(server: Server) -> None:
    import httpx

    c = httpx.Client(base_url=server.base, cookies={"mtg_session": server.sign_in()}, headers=NAV)
    site = c.get("/scan").text.split("<nav class='site' aria-label='Site'>")[1].split("</nav>")[0]
    assert re.findall(r"<a class='xtra' href='([^']+)'", site) == [
        "/scan",
        "/precons",
        "/history/models",
        "/forum",
    ]
    assert "<a class='xtra' href='/scan' aria-current='page'>Scan</a>" in site
    assert "<summary class='on'>More" in site  # the same page, marked where More ▾ shows instead
    # the swap is the stylesheet's, in the bar's own em: 200% text or a narrower window keeps More ▾
    assert (
        "@container topbar (width >= 70em){ body:not(.app) .topbar nav.site a.xtra{display:inline-flex}"
        " body:not(.app) .topbar nav.site .navmore{display:none} }" in theme.CSS
    )


def test_the_guide_describes_the_rail_without_more() -> None:
    from mtg_gateway import guide

    text = guide.nav_description(html_out=False)
    assert "then Scan, Precons, Card models, Forum and Alerts, with no More." in text
    assert "shows them all in the top bar when it is wide enough" in text


# -- screen 1 in Chromium: the rail on an open Fold, the bar on a computer, the phone unchanged ----


RAIL = """() => {
  const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).display !== 'none';
  const bar = document.querySelector('.tabbar');
  const r = e => e.getBoundingClientRect();
  // the tabs, not the text-size sentinel (topbar.js)
  const items = [...bar.children].filter(e => shown(e) && !e.classList.contains('lbl-em'));
  const moreTab = document.querySelector('.tabbar details.more summary');
  const labels = items.filter(e => e.tagName === 'A').map(a => {
    const l = a.querySelector('.lbl'), lb = r(l), ab = r(a);
    const cut = l.scrollWidth > l.clientWidth + 1 || lb.bottom > ab.bottom + 1 || lb.top < ab.top - 1;
    return {text: l.textContent, cut, left: Math.round(lb.left), right: Math.round(lb.right)};
  });
  return {
    order: items.map(e => e.tagName === 'A' ? e.getAttribute('href') : e.className.split(' ')[0]),
    tops: items.map(e => Math.round(r(e).top)), bottoms: items.map(e => Math.round(r(e).bottom)),
    icons: bar.classList.contains('icons'),
    scrolls: bar.scrollHeight > bar.clientHeight + 1, railBottom: Math.round(r(bar).bottom),
    railRight: Math.round(r(bar).right), height: innerHeight,
    more: !!moreTab && shown(moreTab),
    moreItems: [...document.querySelectorAll('.tabbar details.more .menu a')]
      .map(a => a.getAttribute('href')),
    search: shown(document.querySelector('.topbar .topsearch')),
    labels, doc: document.documentElement.scrollWidth, win: innerWidth,
    sep: (() => {
      const s = document.querySelector('.tabbar .rail-sep');
      if (!s || !shown(s)) return null;
      const b = r(s);
      return {top: Math.round(b.top), height: Math.round(b.height), width: Math.round(b.width)};
    })(),
    targets: items.filter(e => e.tagName === 'A').map(a => Math.round(r(a).height)),
  };
}"""


@pytest.mark.parametrize("app", [False, True])
@pytest.mark.parametrize("zoom", ["none", "root", "text"])
@pytest.mark.parametrize("theme_", ["dark", "light"])
def test_the_open_fold_rail_holds_every_page_without_scrolling(
    chromium, server: Server, app: bool, zoom: str, theme_: str
) -> None:
    """840×700 (the mockup's short check) on the website with touch and in the app: nine tabs,
    the line after the fourth, Alerts at the foot, no More, nothing cut, no scrolling."""
    if zoom != "none" and theme_ == "light":
        pytest.skip("the text size is the same in both themes")
    with _page(chromium, server, 840, 700, touch=True, app=app, theme_=theme_) as page:
        page.goto(f"{server.base}/decks", wait_until="load")
        _zoom(page, zoom)
        got = page.evaluate(RAIL)
    assert got["order"] == [
        "/",
        "/decks",
        "/binder",
        "/proposals",
        "rail-sep",
        "/scan",
        "/precons",
        "/history/models",
        "/forum",
        "/notifications",
    ], got
    assert not got["more"], got  # More's tab, and its sheet with it, are the phone's only
    assert not got["scrolls"] and got["bottoms"][-1] <= got["height"], got
    assert got["doc"] == got["win"], got
    assert got["search"], got  # Search stays in the top bar
    # the line sits between Assistant and Scan, inset from the rail's edges
    sep = got["sep"]
    assert sep and got["bottoms"][3] < sep["top"] < got["tops"][5] and sep["height"] == 1, got
    assert 20 <= sep["width"] <= 60, got
    # Alerts at the foot: its bottom at the rail's padded foot, and the spare room above it (at
    # 200% text the nine tabs fill the 700 px, so only the usual gap is left)
    assert got["railBottom"] - got["bottoms"][-1] <= 24, got
    assert got["tops"][-1] - got["bottoms"][-2] > (24 if zoom == "none" else 3), got
    assert all(h >= 44 for h in got["targets"]), got
    # every label whole; at 200% text, where the labels grow with the text (final polish AC1) and a
    # word no longer fits the 80 px rail, the rail shows its icons alone, every name kept for screen
    # readers (static/topbar.js), as the phone's bar does: no label is cut or ends in an ellipsis
    cut = [lb["text"] for lb in got["labels"] if lb["cut"]]
    assert cut == [] or got["icons"], got["labels"]
    assert all(0 <= lb["left"] and lb["right"] <= got["railRight"] for lb in got["labels"]), got["labels"]


CURRENT = (
    "() => [...document.querySelectorAll('.tabbar > a[aria-current=page]')].map(a => a.getAttribute('href'))"
)


def test_the_fold_rail_marks_a_more_page_as_its_own_tab(chromium, server: Server) -> None:
    with _page(chromium, server, 840, 1000, touch=True, app=True) as page:
        page.goto(f"{server.base}/precons", wait_until="load")
        assert page.evaluate(CURRENT) == ["/precons"]
        page.locator(".tabbar > a[href='/forum']").click()
        page.wait_for_url("**/forum")
        assert page.evaluate(CURRENT) == ["/forum"]


@pytest.mark.parametrize("app", [False, True])
def test_the_phone_keeps_its_tab_bar_and_more_sheet(chromium, server: Server, app: bool) -> None:
    with _page(chromium, server, 375, 812, touch=True, app=app) as page:
        page.goto(f"{server.base}/decks", wait_until="load")
        got = page.evaluate(RAIL)
        assert got["order"] == ["/", "/decks", "/binder", "/proposals", "dd"], got
        assert got["more"] and got["moreItems"] == [
            "/search",
            "/precons",
            "/scan",
            "/history/models",
            "/forum",
        ], got
        assert got["sep"] is None and got["doc"] == got["win"], got
        page.locator(".tabbar details.more > summary").click()
        box = page.locator(".tabbar details.more .menu.sheet").bounding_box()
        assert box and box["x"] >= 0 and box["x"] + box["width"] <= 375, box


BAR = """() => {
  const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).display !== 'none';
  const links = [...document.querySelectorAll(
    '.topbar nav.site > a, .topbar nav.site > details > summary')].filter(shown);
  const box = e => e.getBoundingClientRect();
  const hit = (a, b) => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1
    && b.top < a.bottom - 1;
  const parts = [...document.querySelectorAll('.topbar .brand .mark, .topbar nav.site > a, '
    + '.topbar nav.site > details > summary, .topbar .topsearch, .topbar nav.user > *')]
    .filter(shown).map(box);
  return {links: links.map(a => a.textContent.trim()),
    rows: new Set(links.map(a => Math.round(box(a).top))).size,
    height: document.querySelector('.topbar').offsetHeight,
    overlap: parts.some((a, i) => parts.slice(i + 1).some(b => hit(a, b))),
    doc: document.documentElement.scrollWidth, win: innerWidth,
    search: Math.round(box(document.querySelector('.topbar .topsearch')).width)};
}"""
FULL = ["Home", "Decks", "Binder", "Assistant", "Scan", "Precons", "Card models", "Forum"]
WITH_MORE = ["Home", "Decks", "Binder", "Assistant", "More▾"]


def test_the_computer_bar_drops_more_where_the_links_fit(chromium, server: Server) -> None:
    """At 1280 (the mockup's desktop) and wider the bar lists every page on one 50 px row with
    the search box beside them; a narrower window or 200% text folds them back into More ▾."""
    with _page(chromium, server, 1280, 800, touch=False) as page:
        for width, zoom, want in [
            (1280, "none", FULL),
            (1440, "none", FULL),
            (1920, "none", FULL),
            (1024, "none", FULL),
            (900, "none", WITH_MORE),
            (700, "none", WITH_MORE),
            (600, "none", WITH_MORE),
            (1280, "root", WITH_MORE),
            (1280, "text", WITH_MORE),
            (1024, "root", WITH_MORE),
        ]:
            page.set_viewport_size({"width": width, "height": 800})
            page.goto(f"{server.base}/history", wait_until="load")
            _zoom(page, zoom)
            got = page.evaluate(BAR)
            assert got["links"] == want, (width, zoom, got)
            assert not got["overlap"] and got["doc"] == got["win"], (width, zoom, got)
            if want is FULL:
                assert (got["rows"], got["height"]) == (1, 50) and got["search"] >= 200, (width, zoom, got)
                assert (
                    page.evaluate("() => getComputedStyle(document.querySelector('.navmore')).display")
                    == "none"
                )
            else:
                page.locator(".navmore > summary").click()
                items = page.locator(".navmore .menu a").evaluate_all(
                    "els => els.map(a => a.getAttribute('href'))"
                )
                assert items == ["/search", "/precons", "/scan", "/history/models", "/forum"], (
                    width,
                    zoom,
                    items,
                )
                page.keyboard.press("Escape")


# -- screen 9: pull to refresh, the page's side ----------------------------------------------------

STUB = """window.MtgNative = { version: () => "0.7.46", pullAllowed: v => { window.__pull = v; } };"""


def test_the_page_tells_the_app_when_a_touch_may_pull(chromium, server: Server) -> None:
    """The hook answers at every touch: yes at the top of a plain page; no once scrolled, with a
    box open (the page's scrolling is locked), with a menu open, from inside a part that scrolls
    on its own (a tall pane, a strip of chips), or from a text field."""
    ctx = chromium.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, user_agent=APP_UA
    )
    ctx.add_cookies([{"name": "mtg_session", "value": server.sign_in(), "url": server.base}])
    ctx.add_init_script(STUB)
    page = ctx.new_page()
    try:
        page.goto(f"{server.base}/decks", wait_until="load")
        page.evaluate(
            """() => {
              const main = document.querySelector('main');
              const pane = document.createElement('div'); pane.id = 'pane';
              pane.style.cssText = 'height:120px;overflow-y:auto';
              pane.innerHTML = '<p id=inpane style="height:600px">pane</p>';
              const strip = document.createElement('div'); strip.id = 'strip';
              strip.style.cssText = 'overflow-x:auto;white-space:nowrap;width:200px';
              strip.innerHTML = '<span id=chip style="display:inline-block;width:900px">chips</span>';
              const tall = document.createElement('div'); tall.id = 'tall'; tall.style.height = '3000px';
              main.prepend(strip); main.prepend(pane); main.append(tall);
            }"""
        )
        may = "sel => window.mtgMayPull(document.querySelector(sel))"
        assert page.evaluate(may, "h1") is True
        assert page.evaluate(may, "#inpane") is False  # inside a pane that scrolls
        assert page.evaluate(may, "#chip") is False  # on a strip of chips
        assert page.evaluate(may, "#site-q") is False  # a text field
        # a real touch reaches the app through the bridge
        page.touchscreen.tap(200, 300)
        assert page.evaluate("() => window.__pull") is True
        page.evaluate("() => document.documentElement.classList.add('mtgbox-open')")  # a box is open
        assert page.evaluate(may, "h1") is False
        page.evaluate("() => document.documentElement.classList.remove('mtgbox-open')")
        page.evaluate(
            "() => { const m = document.createElement('div'); m.className = 'menu ctxmenu';"
            " document.body.append(m); }"
        )
        assert page.evaluate(may, "h1") is False  # a card menu is open
        page.evaluate("() => document.querySelector('.ctxmenu').remove()")
        assert page.evaluate(may, "h1") is True
        page.evaluate("() => window.scrollTo(0, 400)")
        page.touchscreen.tap(200, 300)
        assert page.evaluate("() => window.__pull") is False  # scrolled down: the page scrolls, not the pull
    finally:
        ctx.close()


def test_no_pull_while_a_box_holds_typed_words(chromium, server: Server) -> None:
    """A pull loads the page again: while a box holds words the member typed and has not sent, no
    touch may start one. A page script filling a box doesn't count; sending the form, or typing the
    box back to what the page came with, frees the pull again."""
    ctx = chromium.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, user_agent=APP_UA
    )
    ctx.add_cookies([{"name": "mtg_session", "value": server.sign_in(), "url": server.base}])
    ctx.add_init_script(STUB)
    page = ctx.new_page()
    try:
        page.goto(f"{server.base}/decks", wait_until="load")
        page.evaluate(
            """() => {
              const f = document.createElement('form'); f.id = 'f'; f.action = 'javascript:void 0';
              f.innerHTML = '<textarea id=note></textarea><input id=other>';
              document.querySelector('main').append(f);
            }"""
        )
        may = "() => window.mtgMayPull(document.querySelector('h1'))"
        assert page.evaluate(may) is True
        page.evaluate("() => { document.querySelector('#other').value = 'filled by a script'; }")
        assert page.evaluate(may) is True  # nothing typed
        page.locator("#note").type("a reply half written")
        assert page.evaluate(may) is False
        page.locator("#note").fill("")
        assert page.evaluate(may) is True  # typed back to what the page came with
        page.locator("#note").type("again")
        assert page.evaluate(may) is False
        page.evaluate("() => document.querySelector('#f').requestSubmit()")
        assert page.evaluate(may) is True  # sent
        page.locator("#note").type("more")
        page.evaluate("() => document.querySelector('#f').remove()")
        assert page.evaluate(may) is True  # the box is gone from the page
    finally:
        ctx.close()


def test_no_pull_on_a_drag_a_changed_choice_or_a_page_that_says_so(chromium, server: Server) -> None:
    """0.7.47: every page offers the pull (owner, 2026-11-08), so the page guards more of itself: a
    touch on something that can be dragged (the deck editor's cards) never pulls, a drop-down the
    member changed and has not sent holds it like typed words, and a page holding state of its own
    (data-no-pull on the root) refuses it."""
    ctx = chromium.new_context(
        viewport={"width": 390, "height": 844}, has_touch=True, is_mobile=True, user_agent=APP_UA
    )
    ctx.add_cookies([{"name": "mtg_session", "value": server.sign_in(), "url": server.base}])
    ctx.add_init_script(STUB)
    page = ctx.new_page()
    try:
        page.goto(f"{server.base}/decks", wait_until="load")
        page.evaluate(
            """() => {
              const main = document.querySelector('main');
              const card = document.createElement('div'); card.id = 'drag'; card.draggable = true;
              card.innerHTML = '<span id=inside>card</span>';
              const f = document.createElement('form'); f.id = 'f'; f.action = 'javascript:void 0';
              f.innerHTML = '<select id=pick><option value=a selected>a</option>'
                + '<option value=b>b</option></select>';
              main.prepend(card); main.append(f);
            }"""
        )
        may = "sel => window.mtgMayPull(document.querySelector(sel))"
        assert page.evaluate(may, "h1") is True
        assert page.evaluate(may, "#inside") is False  # inside something that can be dragged
        page.select_option("#pick", "b")
        assert page.evaluate(may, "h1") is False  # a changed choice, not sent
        page.select_option("#pick", "a")
        assert page.evaluate(may, "h1") is True  # back to what the page came with
        page.evaluate("() => document.documentElement.setAttribute('data-no-pull', '')")
        assert page.evaluate(may, "h1") is False
    finally:
        ctx.close()


def test_the_hook_waits_for_an_app_that_has_it(chromium, server: Server) -> None:
    """A browser, or an older app without the bridge method, gets no hook at all."""
    with _page(chromium, server, 390, 844, touch=True, app=True) as page:
        page.goto(f"{server.base}/decks", wait_until="load")
        assert page.evaluate("() => typeof window.mtgMayPull") == "undefined"


# -- screen 9: the app's side (Kotlin, compiled and unit-tested by CI's android-check job) --------


def test_the_app_offers_pull_to_refresh_on_every_page_but_a_few() -> None:
    """Owner, 2026-11-08: every page (0.7.46: list pages only). The scanner, the sign-in pages and a
    goldfish game never offer it; the page itself refuses a pull while something is unsent."""
    rule = (ANDROID / "PullRefresh.kt").read_text(encoding="utf-8")
    body = rule.split("object PullRefresh")[1]
    assert 'private val NEVER = setOf("/login", "/logout", "/signed-out", "/data-deleted")' in body
    assert 'private val NEVER_UNDER = listOf("/scan/", "/login/", "/auth/")' in body
    assert 'path == "/scan"' in body and 'Regex("^/decks/\\\\d+/goldfish$")' in body
    assert "/edit" not in body and "DECK_PAGE" not in body
    test = (ROOT / "android/app/src/test/kotlin/local/mtgassistantgateway/app/PullRefreshTest.kt").read_text(
        encoding="utf-8"
    )
    assert '"/decks/12345/edit"' in test and '"/scan"' in test and '"/decks/12345/goldfish"' in test
    assert "everyPageOffersIt" in test and "theScannerSignInAndAGameNeverOfferIt" in test


def test_the_app_wires_the_pull_layer_to_the_page_and_the_bridge() -> None:
    main = (ANDROID / "MainActivity.kt").read_text(encoding="utf-8")
    rule = (ANDROID / "PullRefresh.kt").read_text(encoding="utf-8")
    assert "refresh.setOnChildScrollUpCallback { _, _ -> !pagePull || web.canScrollVertically(-1) }" in main
    # a pull loads the page with its own header (decks.PULL_HEADER), a GET of the page's address
    assert "refresh.setOnRefreshListener { pull() }" in main
    assert "web.loadUrl(PullRefresh.target(url), PullRefresh.HEADERS)" in main
    assert 'const val HEADER = "X-MTG-Pull"' in rule and 'mapOf(HEADER to "1")' in rule
    from mtg_gateway.decks import PULL_HEADER

    assert PULL_HEADER == "X-MTG-Pull".lower()
    assert "refresh.isEnabled = allowed && PullRefresh.offered(origin, url)" in main
    assert "fun pullAllowed(allowed: Boolean)" in main and main.count("pullDone()") >= 4
    assert "refresh.setProgressBackgroundColorSchemeResource(R.color.accent)" in main  # the orange disc
    # the renderer-gone rebuild puts the new page back inside the pull layer, not beside it
    assert "val holder = view.parent as ViewGroup" in main
    layout = (ROOT / "android/app/src/main/res/layout/activity_main.xml").read_text(encoding="utf-8")
    assert layout.index("<androidx.swiperefreshlayout.widget.SwipeRefreshLayout") < layout.index("<WebView")
    gradle = (ROOT / "android/app/build.gradle.kts").read_text(encoding="utf-8")
    assert 'implementation("androidx.swiperefreshlayout:swiperefreshlayout:1.1.0")' in gradle
    js = (STATIC / "topbar.js").read_text(encoding="utf-8")
    assert 'typeof app.pullAllowed !== "function"' in js and "app.pullAllowed(ok)" in js
