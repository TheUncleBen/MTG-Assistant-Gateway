"""The top bar in headless Chromium: More ▾'s menu keeps the menu's own colours on the light
theme, and a narrow window with a mouse (600 to 800 px, an open Fold or a
half-width desktop window) keeps its text links clear of the account button without a sideways
scroll. Skipped without a Chromium build."""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_suggest_browser import server as _server

pytest.importorskip("playwright")

server = _server  # the fixture, under its own name


@pytest.fixture(scope="module")
def chromium():
    """One headless Chromium for the whole file; each test opens its own contexts and pages."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        yield browser
        browser.close()


@contextmanager
def _session(browser):
    """The shared browser for one test; the test's contexts close when it ends, pass or fail."""
    try:
        yield browser
    finally:
        for ctx in list(browser.contexts):
            ctx.close()


# After the load event: these pages fetch nothing more that changes the layout (only the
# favicon), so instead of waiting out 500 ms of network quiet on every load, wait for the fonts
# and two frames, the frame topbar.js's ResizeObserver sets --bar-room in and one more. Without
# scripts there is no observer and no frame to wait for (a frame callback never runs), so reading
# the layout is enough.
SETTLE = """async () => {
  await document.fonts.ready;
  await new Promise(r => requestAnimationFrame(() => requestAnimationFrame(r)));
}"""
SCRIPTS_RAN = "() => !!document.documentElement.style.getPropertyValue('--bar-room')"


def _settle(page) -> None:
    if page.evaluate(SCRIPTS_RAN):
        page.evaluate(SETTLE)
    else:
        page.evaluate("() => document.body.getBoundingClientRect().height")


def _open(page, url: str) -> None:
    page.goto(url, wait_until="load")
    _settle(page)


GEOMETRY = """() => {
  const r = e => { const b = e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.right)]; };
  const links = [...document.querySelectorAll('.topbar nav.site > a, .topbar nav.site > details > summary')]
    .filter(a => getComputedStyle(a).display !== 'none' && a.getClientRects().length > 0);
  return {doc: document.documentElement.scrollWidth, win: innerWidth, links: links.length,
    last: links.length ? r(links[links.length - 1])[1] : 0,
    avatar: r(document.querySelector('.topbar nav.user .acct'))[0]};
}"""
MENU_LINK = """() => {
  const d = document.querySelector('.topbar nav.site details.navmore'); d.open = true;
  const c = getComputedStyle(d.querySelector('.menu a'));
  const v = n => getComputedStyle(document.body).getPropertyValue(n).trim();
  d.open = false;
  return {color: c.color, weight: c.fontWeight, text: v('--text'), bar: v('--navbar-text')};
}"""


def _hex_to_rgb(value: str) -> str:
    v = value.lstrip("#")
    r, g, b = (int(v[i : i + 2], 16) for i in (0, 2, 4))
    return f"rgb({r}, {g}, {b})"


def _admin(server: Server) -> str:
    object.__setattr__(server.app.state.gateway.settings, "admin_group", "admins")
    sid = server.sign_in()
    server.db.upsert_user(
        "user-1", email="alice@example.test", name="Alice", preferred_username="alice", groups=["admins"]
    )
    return sid


def _zoom(page, zoom: str) -> None:
    """The text size: none, root or text (200%, as in test_text_zoom_browser), or root-150,
    text-125 and so on: the root's size or every element's text size times that percentage."""
    from .test_text_zoom_browser import ROOT_200, TEXT_200

    if zoom == "none":
        return
    kind, _, pct = zoom.partition("-")
    if not pct:
        page.evaluate(ROOT_200 if kind == "root" else TEXT_200)
    elif kind == "root":
        size = 14 * int(pct) / 100
        root = "document.documentElement.style"
        page.evaluate(f"() => {root}.setProperty('font-size', '{size}px', 'important')")
    else:
        assert "* 2)" in TEXT_200
        page.evaluate(TEXT_200.replace("* 2)", f"* {int(pct) / 100})"))


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_more_menu_links_use_the_menu_colours(chromium, server: Server, theme: str) -> None:
    sid = server.sign_in()
    with _session(chromium) as browser:
        ctx = browser.new_context(viewport={"width": 1366, "height": 800})
        ctx.add_cookies(
            [
                {"name": "mtg_session", "value": sid, "url": server.base},
                {"name": "mtg_theme", "value": theme, "url": server.base},
            ]
        )
        page = ctx.new_page()
        _open(page, f"{server.base}/decks")
        got = page.evaluate(MENU_LINK)
    # the menu's text colour, not the bar's white, and the menu's regular weight, not the bar's bold
    assert got["color"] == _hex_to_rgb(got["text"]), got
    assert got["color"] != _hex_to_rgb(got["bar"]), got
    assert got["weight"] == "400", got


def test_a_narrow_window_with_a_mouse_keeps_the_links_clear_of_the_account_button(
    chromium,
    server: Server,
) -> None:
    # an admin: the four places and More ▾ at every width (navigation round 2)
    sid = _admin(server)
    with _session(chromium) as browser:
        ctx = browser.new_context(viewport={"width": 1000, "height": 700})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        _open(page, f"{server.base}/decks")
        seen = {}
        for width in (1000, 800, 760, 720, 705, 683, 673, 640, 600):
            page.set_viewport_size({"width": width, "height": 700})
            page.wait_for_timeout(50)
            seen[width] = page.evaluate(GEOMETRY)
        page.set_viewport_size({"width": 599, "height": 700})
        page.wait_for_timeout(50)
        phone = page.evaluate(GEOMETRY)
    for width, g in seen.items():
        assert g["links"] == 5, (width, g)
        assert g["doc"] == g["win"] == width, (width, g)  # no sideways scroll
        assert g["last"] + 8 <= g["avatar"], (width, g)  # the last link ends before the account button
    assert phone["links"] == 0, phone  # the phone layout: links move to the tab bar


HOME_PLACE = """() => {
  const shown = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const box = e => { const b = e.getBoundingClientRect(); return {x: b.left, y: b.top, bottom: b.bottom}; };
  const pick = sel => [...document.querySelectorAll(sel)].find(shown);
  const home = pick(".topbar nav.site a[href='/'], .tabbar > a[href='/']");
  const decks = pick(".topbar nav.site a[href='/decks'], .tabbar > a[href='/decks']");
  const more = document.querySelector('.tabbar details.more summary');
  const tabs = [...document.querySelectorAll('.tabbar > a')].filter(shown);
  return {home: home ? box(home) : null, decks: decks ? box(decks) : null,
    homeInMore: !!document.querySelector(".tabbar details.more a[href='/']"),
    moreShown: shown(more),
    moreItems: [...document.querySelectorAll('.tabbar details.more .menu a')]
      .map(a => a.getAttribute('href')),
    tabs: tabs.map(a => a.getAttribute('href')),
    tabsBottom: Math.max(0, ...tabs.map(a => a.getBoundingClientRect().bottom)), height: innerHeight,
    doc: document.documentElement.scrollWidth, win: innerWidth};
}"""


@pytest.mark.parametrize(
    "where",
    ["desktop", "phone", "phone-app", "fold-unfolded", "fold-unfolded-app", "fold-landscape-app"],
)
def test_home_is_never_under_more(chromium, server: Server, where: str) -> None:
    """Home sits left of Decks in the top bar and the phone tab bar and above Decks in the rail,
    never under More. Navigation round 2: the phone bar and the rail carry the four places (the
    rail adds Alerts) and end with More, which holds Find and Your cards."""
    width, height, touch, app = {
        "desktop": (1280, 800, False, False),
        "phone": (390, 844, True, False),
        "phone-app": (390, 844, True, True),
        "fold-unfolded": (884, 1104, True, False),
        "fold-unfolded-app": (884, 1104, True, True),
        "fold-landscape-app": (1104, 820, True, True),
    }[where]
    sid = server.sign_in()
    with _session(chromium) as browser:
        extra = {"user_agent": "Mozilla/5.0 (Linux; Android 14) MTGAssistant/0.7.27"} if app else {}
        ctx = browser.new_context(
            viewport={"width": width, "height": height}, has_touch=touch, is_mobile=touch, **extra
        )
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        _open(page, f"{server.base}/decks")
        got = page.evaluate(HOME_PLACE)
    assert got["home"] and got["decks"], got
    assert not got["homeInMore"], got
    assert got["doc"] == got["win"], got  # no sideways scroll
    if where.startswith("fold"):  # the rail: Home above Decks, the places, Alerts, then More
        assert got["home"]["bottom"] <= got["decks"]["y"] + 1, got
        assert abs(got["home"]["x"] - got["decks"]["x"]) < 1, got
        assert got["tabs"] == ["/", "/decks", "/collection", "/proposals", "/notifications"], got
        assert got["moreShown"], got
        assert got["tabsBottom"] <= got["height"], got  # every tab on screen without scrolling the rail
    else:  # a row: Home left of Decks
        assert got["home"]["x"] < got["decks"]["x"], got
        assert abs(got["home"]["y"] - got["decks"]["y"]) < 1, got
    if where.startswith("phone"):  # the bar: the four places and More
        assert got["tabs"] == ["/", "/decks", "/collection", "/proposals"], got
    if where != "desktop":
        more = ["/search", "/precons", "/scan", "/watchlist", "/lists"]
        assert got["moreShown"] and got["moreItems"] == more, got


REPORTS_PLACE = """() => {
  const shown = e => !!e && e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const lum = c => { const [r, g, b] = c.match(/\\d+(\\.\\d+)?/g).slice(0, 3).map(v => {
      v = v / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05); };
  const bg = e => { for (; e; e = e.parentElement) { const c = getComputedStyle(e).backgroundColor;
      if (c && !c.startsWith('rgba(0, 0, 0, 0)') && c !== 'transparent') return c; }
    return getComputedStyle(document.body).backgroundColor; };
  const contrast = e => ratio(getComputedStyle(e).color, bg(e));
  const tabs = [...document.querySelectorAll('.tabbar > a')].filter(shown);
  const place = tabs.find(a => a.getAttribute('href') === '/proposals');
  const part = document.querySelector("nav.hubseg a[href='/reports']");
  const b = part ? part.getBoundingClientRect() : null;
  return {tabs: tabs.map(a => a.getAttribute('href')),
    tabsBottom: Math.max(0, ...tabs.map(a => a.getBoundingClientRect().bottom)), height: innerHeight,
    placeCurrent: !!place && place.getAttribute('aria-current') === 'page',
    placeContrast: place ? contrast(place) : null,
    partCurrent: !!part && part.getAttribute('aria-current') === 'page',
    partText: part ? part.textContent.trim() : null, partContrast: part ? contrast(part) : null,
    partInside: !!b && b.left >= -1 && b.right <= innerWidth + 1,
    doc: document.documentElement.scrollWidth, win: innerWidth};
}"""


@pytest.mark.parametrize("zoom", ["none", "root", "text"])
@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize(
    "where",
    ["phone", "fold-folded", "phone-app", "fold-unfolded", "fold-unfolded-app", "fold-landscape-app"],
)
def test_reports_is_in_the_assistant_switch_and_marks_assistant(
    chromium, server: Server, where: str, theme: str, zoom: str
) -> None:
    """Reports (0.7.30) is the third part of the Assistant place's switch (navigation round 2): on
    /reports the phone bar's or the rail's Assistant is the current place and the switch's
    Reports the current part, readable, every tab on screen, at normal and 200% text, light and
    dark."""
    width, height, app = {
        "phone": (390, 844, False),
        "fold-folded": (344, 882, False),
        "phone-app": (390, 844, True),
        "fold-unfolded": (673, 841, False),
        "fold-unfolded-app": (884, 1104, True),
        "fold-landscape-app": (1104, 820, True),
    }[where]
    sid = server.sign_in()
    with _session(chromium) as browser:
        extra = {"user_agent": "Mozilla/5.0 (Linux; Android 14) MTGAssistant/0.7.30"} if app else {}
        ctx = browser.new_context(
            viewport={"width": width, "height": height}, has_touch=True, is_mobile=True, **extra
        )
        ctx.add_cookies(
            [
                {"name": "mtg_session", "value": sid, "url": server.base},
                {"name": "mtg_theme", "value": theme, "url": server.base},
            ]
        )
        page = ctx.new_page()
        _open(page, f"{server.base}/reports")
        _zoom(page, zoom)
        _settle(page)
        got = page.evaluate(REPORTS_PLACE)
    assert got["doc"] == got["win"], got  # no sideways scroll
    assert "/reports" not in got["tabs"] and got["placeCurrent"], got
    assert got["placeContrast"] >= 4.5, got
    assert got["partCurrent"] and got["partText"] == "Reports", got
    assert got["partContrast"] >= 4.5, got
    assert got["tabsBottom"] <= got["height"], got
    if zoom == "none":  # at 200% text the switch may scroll sideways inside itself
        assert got["partInside"], got


# The top bar with large text (review note, 0.7.29): with a mouse, the eight text links of an
# admin used to run to 1036 px at 200% text, past a 717 px window and under the account button.
# Now they keep one row while they fit and otherwise wrap in a row of their own under the brand
# and the account side.
NAV_FIT = """() => {
  const box = e => e.getBoundingClientRect();
  const shown = [...document.querySelectorAll('.topbar nav.site > a, .topbar nav.site > details > summary')]
    .filter(a => getComputedStyle(a).display !== 'none' && a.getClientRects().length > 0);
  const right = box(document.querySelector('.topbar .right'));
  const hit = (a, b) => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1
    && b.top < a.bottom - 1;
  const lum = c => { const [r, g, b] = c.match(/\\d+(\\.\\d+)?/g).slice(0, 3).map(v => {
      v = v / 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; });
    return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
  const ratio = (a, b) => { const [x, y] = [lum(a), lum(b)].sort((p, q) => q - p);
    return (x + 0.05) / (y + 0.05); };
  const bar = getComputedStyle(document.querySelector('.topbar')).backgroundColor;
  return {doc: document.documentElement.scrollWidth, win: innerWidth, links: shown.length,
    outside: shown.filter(a => box(a).left < -1 || box(a).right > innerWidth + 1).map(a => a.textContent),
    underRight: shown.filter(a => hit(box(a), right)).map(a => a.textContent),
    overlap: shown.some((a, i) => shown.slice(i + 1).some(b => hit(box(a), box(b)))),
    rows: new Set(shown.map(a => Math.round(box(a).top))).size,
    contrast: Math.min(...shown.map(a => ratio(getComputedStyle(a).color, bar))),
    height: document.querySelector('.topbar').offsetHeight};
}"""


@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_top_bar_links_fit_at_every_width_and_text_size(chromium, server: Server, theme: str) -> None:
    from .test_text_zoom_browser import ROOT_200, TEXT_200

    sid = _admin(server)  # an admin: Admin is on the Account page, so the bar is the same
    problems: list[str] = []
    with _session(chromium) as browser:
        for touch in (False, True):
            kw = {"has_touch": True, "is_mobile": True} if touch else {}
            ctx = browser.new_context(viewport={"width": 717, "height": 800}, **kw)
            ctx.add_cookies(
                [
                    {"name": "mtg_session", "value": sid, "url": server.base},
                    {"name": "mtg_theme", "value": theme, "url": server.base},
                ]
            )
            page = ctx.new_page()
            for path in ("/history", "/decks", "/reports"):
                for width in (344, 360, 599, 600, 673, 717, 800, 899, 900, 1000, 1200, 1280, 1440):
                    for zoom in ("none", "root", "text"):
                        page.set_viewport_size({"width": width, "height": 800})
                        _open(page, f"{server.base}{path}")
                        if zoom != "none":
                            page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
                        _settle(page)
                        g = page.evaluate(NAV_FIT)
                        where = f"{path} @{width}px {'touch' if touch else 'mouse'} {zoom}: {g}"
                        if g["doc"] != g["win"] or g["outside"] or g["underRight"] or g["overlap"]:
                            problems.append(where)
                        if g["links"] and g["contrast"] < 4.5:
                            problems.append(f"contrast {where}")
                        # a mouse from 600 px shows the four places and More ▾ (navigation
                        # round 2); at the normal size on one row in the 50 px bar, as before
                        want = 5
                        if not touch and width >= 600 and g["links"] != want:
                            problems.append(f"links {where}")
                        if (
                            not touch
                            and width >= 600
                            and zoom == "none"
                            and (g["rows"], g["height"]) != (1, 50)
                        ):
                            problems.append(f"one row {where}")
            ctx.close()
    assert problems == [], "\n".join(problems)


# The bar is sticky, so a control that Tab or Shift+Tab reaches must not
# end up under it; at 200% text with a mouse the bar has two rows and is about 147 px tall, and a
# fixed 60 px of scroll room hid 10 of the Settings page's controls (WCAG 2.4.11). The scroll room
# now follows the bar's height (static/topbar.js).
FOCUSED = """() => {
  const el = document.activeElement, bar = document.querySelector('.topbar').getBoundingClientRect();
  if (!el || el === document.body || el.closest('.topbar') || el.matches('a.skip')) return null;
  const r = el.getBoundingClientRect();
  if (!r.width && !r.height) return null;
  return {what: (el.outerHTML || '').slice(0, 80), top: r.top, bottom: r.bottom, bar: bar.bottom,
          win: innerHeight};
}"""


ZOOMS = ["none", "root-125", "root-150", "root-175", "root", "text-125", "text-150", "text-175", "text"]


@pytest.mark.parametrize("script", [True, False])
@pytest.mark.parametrize("zoom", ZOOMS)
def test_a_focused_control_is_never_hidden_under_the_top_bar(
    chromium, server: Server, zoom: str, script: bool
) -> None:
    """With scripts off, the stylesheet's own room covers the bar at every text size from 100% to
    200% (5.5rem fell short between 125% and 175%). 920 px
    at 150% text is where the bar is tallest for its text size (183 px, about 8.7rem)."""
    sid = _admin(server)
    hidden: list[str] = []
    with _session(chromium) as browser:
        for width, height in ((1000, 700), (920, 700), (717, 700), (600, 700), (1280, 800)):
            ctx = browser.new_context(viewport={"width": width, "height": height}, java_script_enabled=script)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            for path in ("/admin/settings", "/history", "/reports"):
                _open(page, f"{server.base}{path}")
                _zoom(page, zoom)
                _settle(page)
                seen = 0
                # forwards from the top, then backwards from the bottom
                for key, start in (("Tab", "Home"), ("Shift+Tab", "End")):
                    page.evaluate("document.activeElement && document.activeElement.blur()")
                    # at once, not with the Home or End key: that scroll is animated, and a
                    # control measured while it runs would read as hidden
                    page.evaluate(SCROLL_TO, start)
                    if key == "Shift+Tab":
                        page.evaluate("document.querySelector('footer.site a:last-of-type')?.focus()")
                    for _ in range(40):
                        page.keyboard.press(key)
                        g = page.evaluate(FOCUSED)
                        if g is None:
                            continue
                        seen += 1
                        # some of it shows below the bar; a control taller than the room left is
                        # allowed to run past the window's foot
                        if g["bottom"] <= g["bar"] + 1 or g["top"] < g["bar"] - 1:
                            hidden.append(f"{path} @{width}px {zoom} {key}: {g}")
                assert seen > 20, (path, width, zoom, seen)
            ctx.close()
    assert hidden == [], "\n".join(hidden[:20])


SCROLL_TO = """edge => window.scrollTo({top: edge === 'Home' ? 0 : document.documentElement.scrollHeight,
                                     behavior: 'instant'})"""


# Tab goes through the top bar in the order it reads, left to right and top
# to bottom, at every text size: with the links on a row of their own under search and account,
# Tab went from the links back up to search and account.
TAB_STOP = """() => {
  const el = document.activeElement;
  if (!el || !el.closest('.topbar')) return null;
  const r = el.getBoundingClientRect();
  return {what: (el.outerHTML || '').slice(0, 60), top: r.top, bottom: r.bottom, left: r.left,
          right: r.right};
}"""


@pytest.mark.parametrize("zoom", ZOOMS)
def test_tab_goes_through_the_top_bar_in_the_order_it_reads(chromium, server: Server, zoom: str) -> None:
    sid = _admin(server)
    problems: list[str] = []
    with _session(chromium) as browser:
        ctx = browser.new_context(viewport={"width": 1000, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        for width in (600, 673, 717, 800, 899, 900, 920, 1000, 1280, 1440):
            page.set_viewport_size({"width": width, "height": 800})
            _open(page, f"{server.base}/history")
            _zoom(page, zoom)
            _settle(page)
            stops = []
            for _ in range(20):
                page.keyboard.press("Tab")
                g = page.evaluate(TAB_STOP)
                if g is None and stops:
                    break
                if g is not None:
                    stops.append(g)
            where = f"@{width}px {zoom}"
            if len(stops) < 8:  # the brand, five links, More ▾ and the account button at least
                problems.append(f"{where}: {len(stops)} stops")
            for a, b in zip(stops, stops[1:], strict=False):
                row = b["top"] < a["bottom"] - 1 and a["top"] < b["bottom"] - 1
                if b["bottom"] <= a["top"] + 1 or (row and b["right"] <= a["left"] + 1):
                    problems.append(f"{where}: {a['what']} then {b['what']}")
    assert problems == [], "\n".join(problems)


# With a mouse, where the bar keeps one row beside the search box, the last links
# (History, Admin) ran under the search box, so a click there landed in the box. Nothing in the
# bar may sit over anything else, at any width or text size, for a member or an admin.
BAR_OVERLAP = """() => {
  const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const parts = [...document.querySelectorAll(
    '.topbar .brand .mark, .topbar nav.site > a, .topbar nav.site > details > summary, ' +
    '.topbar .topsearch, .topbar nav.user > *')]
    .filter(shown).map(e => ({what: (e.textContent || e.className || e.tagName).trim().slice(0, 20),
                              box: e.getBoundingClientRect()}));
  const hit = (a, b) => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1
    && b.top < a.bottom - 1;
  const out = [];
  parts.forEach((p, i) => parts.slice(i + 1).forEach(q => {
    if (hit(p.box, q.box)) out.push(`${p.what} / ${q.what}`); }));
  const outside = parts.filter(p => p.box.left < -1 || p.box.right > innerWidth + 1).map(p => p.what);
  return {overlap: out, outside, doc: document.documentElement.scrollWidth, win: innerWidth,
          links: document.querySelectorAll('.topbar nav.site > a, .topbar nav.site summary').length};
}"""


@pytest.mark.parametrize("script", [True, False])
@pytest.mark.parametrize("role", ["member", "admin"])
def test_nothing_in_the_top_bar_sits_over_anything_else(
    chromium, server: Server, role: str, script: bool
) -> None:
    sid = _admin(server) if role == "admin" else server.sign_in()
    problems: list[str] = []
    with _session(chromium) as browser:
        ctx = browser.new_context(viewport={"width": 1000, "height": 800}, java_script_enabled=script)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        widths = (344, 390, 600, 673, 717, 800, 899, 900, 920, 960, 1000, 1100, 1200, 1280, 1440, 1600, 1920)
        for width in widths:
            for zoom in [*ZOOMS, "root-110", "text-110"]:
                page.set_viewport_size({"width": width, "height": 800})
                _open(page, f"{server.base}/history")
                _zoom(page, zoom)
                _settle(page)
                g = page.evaluate(BAR_OVERLAP)
                if g["overlap"] or g["outside"] or g["doc"] != g["win"]:
                    problems.append(f"@{width}px {zoom}: {g}")
    assert problems == [], "\n".join(problems[:40]) + f"\n… {len(problems)} in all"


LONG_NAME = "The Very Long Name Of Our Friends' Deck Gateway"
BRAND_AND_SEARCH = """() => {
  const word = document.querySelector('.topbar .brand .word');
  const search = document.querySelector('.topbar .topsearch');
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  return {cut: word.scrollWidth > word.clientWidth + 1, search: search.getBoundingClientRect().width / rem};
}"""


def test_a_long_server_name_gives_way_before_the_search_box_and_the_links(chromium, server: Server) -> None:
    """The order the one-row bar gives up room in: the search box's growth past its 20rem, then
    the brand's word (an ellipsis), then the search box down to 9rem; the links never."""
    sid = _admin(server)
    settings = server.app.state.gateway.settings
    short_name = settings.server_name
    problems: list[str] = []
    with _session(chromium) as browser:
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()

        def measure(name: str, width: int, zoom: str) -> dict:
            object.__setattr__(settings, "server_name", name)
            page.set_viewport_size({"width": width, "height": 800})
            _open(page, f"{server.base}/history")
            _zoom(page, zoom)
            _settle(page)
            g = page.evaluate(BAR_OVERLAP)
            if g["overlap"] or g["outside"] or g["doc"] != g["win"]:
                problems.append(f"@{width}px {zoom} {name!r}: {g}")
            return page.evaluate(BRAND_AND_SEARCH)

        try:
            for width in (900, 960, 1000, 1100, 1280, 1440, 1920):
                for zoom in ("none", "root-110", "root-125", "text-125"):
                    # The search box's full width depends on the browser's fonts, so it is
                    # measured with the usual short name at the same width and text size.
                    full = measure(short_name, width, zoom)["search"]
                    b = measure(LONG_NAME, width, zoom)
                    # the box grows into the room the four places leave (up to 40rem), so a
                    # long name takes from that growth first; below its own 20rem the name gives
                    # way before the box does
                    if b["search"] < 9 - 0.05 or (b["search"] < min(full, 20) - 0.05 and not b["cut"]):
                        problems.append(f"@{width}px {zoom}: search box shrank first: {b}, full {full}")
            full_1280 = measure(short_name, 1280, "none")["search"]
            at_1280 = measure(LONG_NAME, 1280, "none")
        finally:
            object.__setattr__(settings, "server_name", short_name)
    assert problems == [], "\n".join(problems)
    # at 1280 the four places leave room: the long name fits whole and the box keeps at least
    # its own 20rem
    assert at_1280["search"] >= min(full_1280, 20) - 0.05, (at_1280, full_1280)


def test_without_scripts_the_scroll_room_is_large_only_where_the_bar_wraps(chromium, server: Server) -> None:
    """60 px over the one-row bar, 9.5rem where its links wrap."""
    sid = _admin(server)
    seen = {}
    with _session(chromium) as browser:
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, java_script_enabled=False)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        for width, zoom in ((390, "none"), (717, "none"), (1280, "none"), (920, "root-150"), (717, "text")):
            page.set_viewport_size({"width": width, "height": 800})
            _open(page, f"{server.base}/history")
            _zoom(page, zoom)
            seen[(width, zoom)] = page.evaluate(
                "() => [getComputedStyle(document.documentElement).scrollPaddingTop,"
                " document.querySelector('.topbar').offsetHeight,"
                " parseFloat(getComputedStyle(document.documentElement).fontSize)]"
            )
    for (width, zoom), (room, bar, rem) in seen.items():
        if bar == 50:
            assert room == "60px", (width, zoom, room)
        else:
            assert abs(float(room[:-2]) - 9.5 * rem) < 0.5 and float(room[:-2]) >= bar + 1, (
                width,
                zoom,
                room,
                bar,
            )
    assert seen[(1280, "none")][1] == 50 and seen[(920, "root-150")][1] > 50  # both kinds were seen


# The search box's placeholder, whole, beside the links at the normal text size (at
# 920 px it read "Search cards and d"). Large text may shrink the box further, by design.
PLACEHOLDER_FIT = """() => {
  const i = document.querySelector('.topbar .topsearch input');
  const cs = getComputedStyle(i);
  const c = document.createElement('canvas').getContext('2d');
  c.font = cs.font;
  return {text: c.measureText(i.placeholder).width,
    room: i.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight),
    bar: document.querySelector('.topbar').offsetHeight};
}"""


@pytest.mark.parametrize("role", ["member", "admin"])
def test_the_search_placeholder_fits_beside_the_links_at_normal_size(
    chromium, server: Server, role: str
) -> None:
    sid = _admin(server) if role == "admin" else server.sign_in()
    problems: list[str] = []
    with _session(chromium) as browser:
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        for width in (900, 920, 940, 960, 980, 1000, 1010, 1023, 1024, 1040, 1100, 1280, 1920):
            page.set_viewport_size({"width": width, "height": 800})
            _open(page, f"{server.base}/history")
            m = page.evaluate(PLACEHOLDER_FIT)
            if m["text"] > m["room"] + 0.5 or m["bar"] != 50:
                problems.append(f"@{width}px: {m}")
    assert problems == [], "\n".join(problems)


# With a font about 15% wider than Lato (DejaVu Sans, a monospace font) and large
# text, an admin's one-row bar ran past the window (up to 59 px at 125%, 1024 to 1082 px; and,
# found while fixing it, up to 48 px at 110% to 150% between 660 and 898 px). The
# browser may lack DejaVu Sans (it then falls back), so the page's own font with wider letter
# spacing stands in for a wide font too.
WIDE_FONTS = ("'DejaVu Sans'", "monospace", "letter-spacing")
WIDE_BANDS = (
    ("root-110", range(640, 700, 6)),
    ("root-125", range(720, 800, 6)),
    ("root-150", range(880, 900, 4)),
    ("root-125", range(1000, 1104, 4)),
    ("text-125", range(1000, 1104, 4)),
    ("root-140", range(1100, 1250, 10)),
    ("root-150", range(1180, 1330, 10)),
)


@pytest.mark.parametrize("font", WIDE_FONTS)
def test_a_wide_font_at_large_text_keeps_the_bar_inside_the_window(
    chromium, server: Server, font: str
) -> None:
    sid = _admin(server)
    rule = "*{letter-spacing:.05em}" if font == "letter-spacing" else f"*{{font-family:{font} !important}}"
    problems: list[str] = []
    with _session(chromium) as browser:
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        for zoom, widths in WIDE_BANDS:
            for width in widths:
                page.set_viewport_size({"width": width, "height": 800})
                _open(page, f"{server.base}/history")
                page.evaluate(
                    "r => { const s = document.createElement('style'); s.textContent = r;"
                    " document.head.append(s); }",
                    rule,
                )
                _zoom(page, zoom)
                _settle(page)
                g = page.evaluate(BAR_OVERLAP)
                if g["overlap"] or g["outside"] or g["doc"] != g["win"]:
                    problems.append(f"@{width}px {zoom}: {g}")
    assert problems == [], "\n".join(problems[:40]) + f"\n… {len(problems)} in all"


# Where the brand's word gives way, a stub such as "M." (920 px, an admin) read
# oddly. The word now shows at least 3em ("MTG…") or none of it, and the brand keeps its height
# with the mark in the middle either way.
BRAND_WORD = """() => {
  const brand = document.querySelector('.topbar .brand'), word = brand.querySelector('.word');
  const b = brand.getBoundingClientRect(), w = word.getBoundingClientRect();
  const m = brand.querySelector('.mark').getBoundingClientRect();
  return {shown: getComputedStyle(word).display !== 'none' && w.top < b.bottom && w.bottom > b.top,
    width: w.width, em: parseFloat(getComputedStyle(word).fontSize), height: b.height,
    bar: document.querySelector('.topbar').offsetHeight,
    middle: Math.abs((m.top + m.bottom) / 2 - (b.top + b.bottom) / 2)};
}"""


@pytest.mark.parametrize("touch", [False, True], ids=["mouse", "touch"])
@pytest.mark.parametrize("role", ["member", "admin"])
def test_the_brand_word_shows_at_least_three_letters_or_none(
    chromium, server: Server, role: str, touch: bool
) -> None:
    sid = _admin(server) if role == "admin" else server.sign_in()
    problems: list[str] = []
    heights = set()
    with _session(chromium) as browser:
        ctx = browser.new_context(viewport={"width": 1280, "height": 800}, has_touch=touch)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        for width in (*range(900, 1024, 6), 1023, 1024, 1100, 1280):
            for zoom in ("none", "root-125"):
                page.set_viewport_size({"width": width, "height": 800})
                _open(page, f"{server.base}/history")
                _zoom(page, zoom)
                _settle(page)
                g = page.evaluate(BRAND_WORD)
                if (g["shown"] and g["width"] < 3 * g["em"] - 0.5) or g["middle"] > 0.6:
                    problems.append(f"@{width}px {zoom}: {g}")
                if g["bar"] == 50:
                    heights.add((zoom, round(g["height"], 1)))
    assert problems == [], "\n".join(problems)
    assert len(heights) == 2, heights  # in one row, one height per text size, word shown or not


# The app has no text links in its bar, so the one-row rules for the links leave it alone (review
# At 344 px, the Fold's cover screen, they cut the gateway's name one letter earlier).
# Besides the styles, where the name is cut. The bar's left side ends one gap before
# the account side and holds the brand (and the links' empty row after it); a long name is cut
# where the brand's room ends, and the usual name only when its whole width does not fit there.
APP_BAR = """() => {
  const q = sel => document.querySelector(sel), box = sel => q(sel).getBoundingClientRect();
  const cs = sel => getComputedStyle(q(sel)), word = q('.topbar .brand .word');
  return {app: document.body.classList.contains('app'), left: cs('.topbar .left').display,
    pad: cs('.topbar .right').paddingLeft, shown: cs('.topbar .brand .word').display,
    doc: document.documentElement.scrollWidth, win: innerWidth,
    leftEnd: box('.topbar .left').right, brandEnd: box('.topbar .brand').right,
    wordStart: box('.topbar .brand .word').left, wordEnd: box('.topbar .brand .word').right,
    rightStart: box('.topbar .right').left, gap: parseFloat(cs('.topbar .wrap').columnGap),
    nav: q('.topbar nav.site').getClientRects().length > 0,
    navStart: box('.topbar nav.site').left, navEnd: box('.topbar nav.site').right,
    leftGap: parseFloat(cs('.topbar .left').columnGap), margin: parseFloat(cs('.topbar .brand').marginRight),
    full: word.scrollWidth, cut: word.scrollWidth > word.clientWidth + 1};
}"""


@pytest.mark.parametrize("width", [344, 360, 412, 884])
def test_the_app_bar_keeps_its_own_spacing(chromium, server: Server, width: int) -> None:
    sid = server.sign_in()
    settings = server.app.state.gateway.settings
    short_name = settings.server_name
    seen = {}
    with _session(chromium) as browser:
        try:
            for name in (short_name, LONG_NAME):
                object.__setattr__(settings, "server_name", name)
                ctx = browser.new_context(
                    viewport={"width": width, "height": 800},
                    has_touch=True,
                    is_mobile=True,
                    user_agent="Mozilla/5.0 (Linux; Android 14) MTGAssistant/0.7.27",
                )
                ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
                page = ctx.new_page()
                _open(page, f"{server.base}/decks")
                seen[name] = page.evaluate(APP_BAR)
                ctx.close()
        finally:
            object.__setattr__(settings, "server_name", short_name)
    for name, got in seen.items():
        assert got["app"] and got["shown"] != "none", (name, got)
        # the brand gets the room the old way
        assert got["left"] == "flex" and got["pad"] == "0px", (name, got)
        assert got["doc"] == got["win"], (name, got)
        assert got["leftEnd"] <= got["rightStart"] - got["gap"] + 0.5, (name, got)
        # where the brand's room ends: the left side's end (the places are in the tab bar or the
        # rail, so the bar has no links, navigation round 2)
        assert not got["nav"], (name, got)
        end = got["leftEnd"] - got["margin"]
        assert got["brandEnd"] <= end + 0.5, (name, got)  # a whole name may end sooner
        assert got["cut"] == (got["full"] > end - got["wordStart"] + 0.5), (name, got)
        if got["cut"]:
            assert abs(got["wordEnd"] - end) <= 0.5, (name, got)
    # at 884 px the brand has about 400 px, about as wide as the long name itself (418 px in Inter,
    # 374 px in serif), so whether it is cut there depends on the font; the check above covers it
    if width < 800:
        assert seen[LONG_NAME]["cut"], seen[LONG_NAME]


# The Account page on a phone with 200% text, scripts on and off (the bottom tab bar at 344
# and 360 px; 320 px too): nothing reaches past the window. The causes found
# were the link disclosure's "/decks/{deck_id}" (shown open without scripts) and the
# "Notifications" heading at 320 px; both now break instead.
@pytest.mark.parametrize("script", [True, False], ids=["script", "no-script"])
def test_the_account_page_fits_a_phone_at_200_percent_text(chromium, server: Server, script: bool) -> None:
    sid = server.sign_in()
    problems: list[str] = []
    with _session(chromium) as browser:
        for width in (320, 344, 360):
            for zoom in ("root", "text"):
                ctx = browser.new_context(
                    viewport={"width": width, "height": 800}, java_script_enabled=script
                )
                ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
                page = ctx.new_page()
                _open(page, f"{server.base}/account/details")
                _zoom(page, zoom)
                _settle(page)
                got = page.evaluate(
                    "() => ({doc: document.documentElement.scrollWidth,"
                    " win: document.documentElement.clientWidth,"
                    " tabbar: document.querySelector('.tabbar').getBoundingClientRect().right,"
                    " disclosure: !!document.querySelector('#archidekt-disclosure')})"
                )
                if got["doc"] > got["win"] or got["tabbar"] > got["win"] + 0.5 or not got["disclosure"]:
                    problems.append(f"@{width}px {zoom}: {got}")
                ctx.close()
    assert problems == [], "\n".join(problems)


# The magnifier and the text start of the top bar's search box: the icon must end before the
# text (the input's left padding) begins, so it never sits on "Search cards and decks".
SEARCH_ICON = """() => {
  const i = document.querySelector('.topbar .topsearch input');
  if (!i || !i.getClientRects().length) return null;
  const s = document.querySelector('.topbar .topsearch .box > svg.i');
  const ir = i.getBoundingClientRect(), sr = s.getBoundingClientRect();
  const textStart = ir.left + parseFloat(getComputedStyle(i).paddingLeft);
  return {clear: sr.right + 2 <= textStart, iconRight: sr.right, textStart, wrapped: !!i.closest('.suggest'),
    height: ir.height, radius: getComputedStyle(i).borderTopLeftRadius};
}"""


@pytest.mark.parametrize("theme", ["light", "dark"])
@pytest.mark.parametrize("script", [True, False])
def test_the_search_boxs_magnifier_sits_clear_of_its_text(
    chromium, server: Server, script: bool, theme: str
) -> None:
    """0.7.43: the box's rules addressed the input through the .suggest wrapper the script puts
    around it, so without scripts (and before they run) the input had no room for the magnifier,
    which sat on "Search". In the bar (desktop), on the touch rail (Fold) and, with scripts, opened
    under the phone's bar, on every page, the icon ends before the text begins."""
    sid = server.sign_in()
    problems: list[str] = []
    with _session(chromium) as browser:
        for width, touch in ((1366, False), (1000, False), (810, True), (390, True)):
            if width == 390 and not script:
                continue  # without scripts the phone's magnifier is a link to /search
            ctx = browser.new_context(
                viewport={"width": width, "height": 800},
                java_script_enabled=script,
                color_scheme=theme,
                has_touch=touch,
                is_mobile=touch,
            )
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            for path in ("/history", "/account", "/search"):
                _open(page, f"{server.base}{path}")
                if width == 390:
                    page.locator(".topbar .searchbtn").click()
                    page.locator(".topbar .topsearch input").wait_for(state="visible", timeout=3000)
                m = page.evaluate(SEARCH_ICON)
                if m is None:
                    problems.append(f"@{width}px {path} scripts={script}: no search box")
                elif not m["clear"] or m["wrapped"] != script or m["height"] != (44 if touch else 36):
                    problems.append(f"@{width}px {path} scripts={script}: {m}")
            ctx.close()
    assert problems == [], "\n".join(problems)


# Counts layout reads made right after a write to <html>'s style in the same script run: each one
# makes the browser restyle and lay out the whole page there and then, before the frame does it
# again (a write on <html> reaches every element).
FORCED = """
(() => {
  const html = () => document.documentElement && document.documentElement.style;
  let dirty = false;
  window.__forced = 0;
  const S = CSSStyleDeclaration.prototype;
  for (const k of ['setProperty', 'removeProperty']) {
    const f = S[k];
    S[k] = function (...a) {
      if (this === html()) { dirty = true; queueMicrotask(() => { dirty = false; }); }
      return f.apply(this, a);
    };
  }
  const read = (proto, k) => {
    const f = proto[k];
    proto[k] = function (...a) { if (dirty) window.__forced++; return f.apply(this, a); };
  };
  read(Element.prototype, 'getBoundingClientRect');
  const gcs = window.getComputedStyle;
  window.getComputedStyle = function (...a) { if (dirty) window.__forced++; return gcs.apply(this, a); };
})();
"""


@pytest.mark.parametrize("width", [390, 1366])
def test_the_bars_room_is_measured_before_it_is_written(chromium, server: Server, width: int) -> None:
    """topbar.js reads every size first and then writes its rooms on <html>: a read after a write
    restyled and laid out a 100-card deck page again (the phone's longest task on load)."""
    sid = server.sign_in()
    with _session(chromium) as browser:
        ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=width < 1000)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.add_init_script(FORCED)
        page = ctx.new_page()
        _open(page, f"{server.base}/decks/42")
        page.evaluate(SETTLE)
        page.evaluate("() => window.dispatchEvent(new Event('resize'))")
        page.evaluate(SETTLE)
        assert page.evaluate(SCRIPTS_RAN)
        assert page.evaluate("() => window.__forced") == 0


def test_the_search_box_starts_from_20rem_whatever_the_fonts(chromium, server: Server) -> None:
    """The box grows from 20rem, not from the input's own width, which follows the browser's
    fonts: on CI's fonts (25 Oct 2026) the long name kept its whole word at 1100 px while the box
    sat at 16rem. A smaller input font stands in for those fonts here."""
    sid = _admin(server)
    settings = server.app.state.gateway.settings
    short_name = settings.server_name
    seen = []
    with _session(chromium) as browser:
        ctx = browser.new_context(viewport={"width": 1100, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        try:
            object.__setattr__(settings, "server_name", LONG_NAME)
            for css in ("", ".topbar .topsearch input{font-size:11px}"):
                _open(page, f"{server.base}/history")
                if css:
                    page.add_style_tag(content=css)
                _settle(page)
                seen.append((css, page.evaluate(BRAND_AND_SEARCH)))
        finally:
            object.__setattr__(settings, "server_name", short_name)
    # at 1100 px the brand's word has the room to give: the box keeps its 20rem
    assert all(b["search"] >= 20 - 0.05 and b["cut"] for _css, b in seen), seen
