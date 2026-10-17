"""The top bar in headless Chromium: the account menu keeps the menu's own colours on the light
theme (0.7.9 gate B-1), and a narrow window with a mouse (600 to 800 px, an open Fold or a
half-width desktop window) keeps its text links clear of the account button without a sideways
scroll (gate B-2). Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_suggest_browser import server as _server

pytest.importorskip("playwright")

server = _server  # the fixture, under its own name

GEOMETRY = """() => {
  const r = e => { const b = e.getBoundingClientRect(); return [Math.round(b.left), Math.round(b.right)]; };
  const links = [...document.querySelectorAll('.topbar nav.site a')]
    .filter(a => getComputedStyle(a).display !== 'none');
  return {doc: document.documentElement.scrollWidth, win: innerWidth, links: links.length,
    last: links.length ? r(links[links.length - 1])[1] : 0,
    avatar: r(document.querySelector('.topbar details.dd > summary'))[0]};
}"""
MENU_LINK = """() => {
  const d = document.querySelector('.topbar details.dd'); d.open = true;
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
def test_account_menu_links_use_the_menu_colours(server: Server, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1366, "height": 800})
        ctx.add_cookies(
            [
                {"name": "mtg_session", "value": sid, "url": server.base},
                {"name": "mtg_theme", "value": theme, "url": server.base},
            ]
        )
        page = ctx.new_page()
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        got = page.evaluate(MENU_LINK)
        browser.close()
    # the menu's text colour, not the bar's white, and the menu's regular weight, not the bar's bold
    assert got["color"] == _hex_to_rgb(got["text"]), got
    assert got["color"] != _hex_to_rgb(got["bar"]), got
    assert got["weight"] == "400", got


def test_a_narrow_window_with_a_mouse_keeps_the_links_clear_of_the_account_button(
    server: Server,
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _admin(server)  # eight links: Home, the six sections and Admin
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1000, "height": 700})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        seen = {}
        for width in (1000, 800, 760, 720, 705, 683, 673, 640, 600):
            page.set_viewport_size({"width": width, "height": 700})
            page.wait_for_timeout(50)
            seen[width] = page.evaluate(GEOMETRY)
        page.set_viewport_size({"width": 599, "height": 700})
        page.wait_for_timeout(50)
        phone = page.evaluate(GEOMETRY)
        browser.close()
    for width, g in seen.items():
        assert g["links"] == 8, (width, g)
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
def test_home_is_never_under_more(server: Server, where: str) -> None:
    """Home sits left of Decks in the top bar and the phone tab bar and above Decks in the rail,
    never under More; the rail (an unfolded Fold, the app from 600 px) shows every page as a tab
    and has no More."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    width, height, touch, app = {
        "desktop": (1280, 800, False, False),
        "phone": (390, 844, True, False),
        "phone-app": (390, 844, True, True),
        "fold-unfolded": (884, 1104, True, False),
        "fold-unfolded-app": (884, 1104, True, True),
        "fold-landscape-app": (1104, 820, True, True),
    }[where]
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        extra = {"user_agent": "Mozilla/5.0 (Linux; Android 14) MTGAssistant/0.7.27"} if app else {}
        ctx = browser.new_context(
            viewport={"width": width, "height": height}, has_touch=touch, is_mobile=touch, **extra
        )
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        got = page.evaluate(HOME_PLACE)
        browser.close()
    assert got["home"] and got["decks"], got
    assert not got["homeInMore"], got
    assert got["doc"] == got["win"], got  # no sideways scroll
    if where.startswith("fold"):  # the rail: Home above Decks, every page a tab, no More
        assert got["home"]["bottom"] <= got["decks"]["y"] + 1, got
        assert abs(got["home"]["x"] - got["decks"]["x"]) < 1, got
        assert not got["moreShown"], got
        for href in ("/", "/decks", "/search", "/scan", "/collection", "/proposals", "/history", "/guide"):
            assert href in got["tabs"], (href, got)
        assert got["tabsBottom"] <= got["height"], got  # every tab on screen without scrolling the rail
    else:  # a row: Home left of Decks
        assert got["home"]["x"] < got["decks"]["x"], got
        assert abs(got["home"]["y"] - got["decks"]["y"]) < 1, got
    if where.startswith("phone"):  # the bar: Home, Decks, Scan, Collection and More
        assert got["tabs"] == ["/", "/decks", "/scan", "/collection"], got
        assert got["moreShown"] and "/proposals" in got["moreItems"], got


# The top bar with large text (review note, 0.7.29): with a mouse, the eight text links of an
# admin used to run to 1036 px at 200% text, past a 717 px window and under the account button.
# Now they keep one row while they fit and otherwise wrap in a row of their own under the brand
# and the account side. /reports is Track C's page (0.7.30) and is not on this branch yet.
NAV_FIT = """() => {
  const box = e => e.getBoundingClientRect();
  const shown = [...document.querySelectorAll('.topbar nav.site a')]
    .filter(a => getComputedStyle(a).display !== 'none');
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
def test_the_top_bar_links_fit_at_every_width_and_text_size(server: Server, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    from .test_text_zoom_browser import ROOT_200, TEXT_200

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _admin(server)  # eight links: the most the bar carries
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
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
            for path in ("/history", "/decks"):
                for width in (344, 360, 599, 600, 673, 717, 800, 899, 900, 1000, 1280, 1440):
                    for zoom in ("none", "root", "text"):
                        page.set_viewport_size({"width": width, "height": 800})
                        page.goto(f"{server.base}{path}", wait_until="networkidle")
                        if zoom != "none":
                            page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
                        page.wait_for_timeout(30)
                        g = page.evaluate(NAV_FIT)
                        where = f"{path} @{width}px {'touch' if touch else 'mouse'} {zoom}: {g}"
                        if g["doc"] != g["win"] or g["outside"] or g["underRight"] or g["overlap"]:
                            problems.append(where)
                        if g["links"] and g["contrast"] < 4.5:
                            problems.append(f"contrast {where}")
                        # a mouse from 600 px shows the links; at the normal size on one row in
                        # the 50 px bar, as before
                        if not touch and width >= 600 and g["links"] != 8:
                            problems.append(f"links {where}")
                        if (
                            not touch
                            and width >= 600
                            and zoom == "none"
                            and (g["rows"], g["height"]) != (1, 50)
                        ):
                            problems.append(f"one row {where}")
            ctx.close()
        browser.close()
    assert problems == [], "\n".join(problems)


# Review round 9 (R9-1): the bar is sticky, so a control that Tab or Shift+Tab reaches must not
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
def test_a_focused_control_is_never_hidden_under_the_top_bar(server: Server, zoom: str, script: bool) -> None:
    """With scripts off, the stylesheet's own room covers the bar at every text size from 100% to
    200% (review round 10; milestone review M-2: 5.5rem fell short between 125% and 175%). 920 px
    at 150% text is where the bar is tallest for its text size (183 px, about 8.7rem)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _admin(server)
    hidden: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width, height in ((1000, 700), (920, 700), (717, 700), (600, 700), (1280, 800)):
            ctx = browser.new_context(viewport={"width": width, "height": height}, java_script_enabled=script)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            for path in ("/admin/settings", "/history"):
                page.goto(f"{server.base}{path}", wait_until="networkidle")
                _zoom(page, zoom)
                page.wait_for_timeout(50)
                seen = 0
                # forwards from the top, then backwards from the bottom
                for key, start in (("Tab", "Home"), ("Shift+Tab", "End")):
                    page.evaluate("document.activeElement && document.activeElement.blur()")
                    page.keyboard.press(start)
                    page.wait_for_timeout(50)
                    if key == "Shift+Tab":
                        page.evaluate("document.querySelector('footer.site a:last-of-type')?.focus()")
                    for _ in range(40):
                        page.keyboard.press(key)
                        page.wait_for_timeout(30)
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
        browser.close()
    assert hidden == [], "\n".join(hidden[:20])


# Milestone review M-1: Tab goes through the top bar in the order it reads, left to right and top
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
def test_tab_goes_through_the_top_bar_in_the_order_it_reads(server: Server, zoom: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _admin(server)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1000, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        for width in (600, 673, 717, 800, 899, 900, 920, 1000, 1280, 1440):
            page.set_viewport_size({"width": width, "height": 800})
            page.goto(f"{server.base}/history", wait_until="networkidle")
            _zoom(page, zoom)
            page.wait_for_timeout(30)
            stops = []
            for _ in range(20):
                page.keyboard.press("Tab")
                g = page.evaluate(TAB_STOP)
                if g is None and stops:
                    break
                if g is not None:
                    stops.append(g)
            where = f"@{width}px {zoom}"
            if len(stops) < 10:  # the brand, eight links and the account button at least
                problems.append(f"{where}: {len(stops)} stops")
            for a, b in zip(stops, stops[1:], strict=False):
                row = b["top"] < a["bottom"] - 1 and a["top"] < b["bottom"] - 1
                if b["bottom"] <= a["top"] + 1 or (row and b["right"] <= a["left"] + 1):
                    problems.append(f"{where}: {a['what']} then {b['what']}")
        browser.close()
    assert problems == [], "\n".join(problems)


# Review R12-1: with a mouse, where the bar keeps one row beside the search box, the last links
# (History, Admin) ran under the search box, so a click there landed in the box. Nothing in the
# bar may sit over anything else, at any width or text size, for a member or an admin.
BAR_OVERLAP = """() => {
  const shown = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
  const parts = [...document.querySelectorAll(
    '.topbar .brand .mark, .topbar nav.site a, .topbar .topsearch, .topbar nav.user > *')]
    .filter(shown).map(e => ({what: (e.textContent || e.className || e.tagName).trim().slice(0, 20),
                              box: e.getBoundingClientRect()}));
  const hit = (a, b) => a.left < b.right - 1 && b.left < a.right - 1 && a.top < b.bottom - 1
    && b.top < a.bottom - 1;
  const out = [];
  parts.forEach((p, i) => parts.slice(i + 1).forEach(q => {
    if (hit(p.box, q.box)) out.push(`${p.what} / ${q.what}`); }));
  const outside = parts.filter(p => p.box.left < -1 || p.box.right > innerWidth + 1).map(p => p.what);
  return {overlap: out, outside, doc: document.documentElement.scrollWidth, win: innerWidth,
          links: document.querySelectorAll('.topbar nav.site a').length};
}"""


@pytest.mark.parametrize("script", [True, False])
@pytest.mark.parametrize("role", ["member", "admin"])
def test_nothing_in_the_top_bar_sits_over_anything_else(server: Server, role: str, script: bool) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _admin(server) if role == "admin" else server.sign_in()
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1000, "height": 800}, java_script_enabled=script)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        widths = (344, 390, 600, 673, 717, 800, 899, 900, 920, 960, 1000, 1100, 1200, 1280, 1440, 1600, 1920)
        for width in widths:
            for zoom in [*ZOOMS, "root-110", "text-110"]:
                page.set_viewport_size({"width": width, "height": 800})
                page.goto(f"{server.base}/history", wait_until="networkidle")
                _zoom(page, zoom)
                page.wait_for_timeout(20)
                g = page.evaluate(BAR_OVERLAP)
                if g["overlap"] or g["outside"] or g["doc"] != g["win"]:
                    problems.append(f"@{width}px {zoom}: {g}")
        browser.close()
    assert problems == [], "\n".join(problems[:40]) + f"\n… {len(problems)} in all"


LONG_NAME = "The Very Long Name Of Our Friends' Deck Gateway"
FULL_SEARCH = 18.8  # rem: the search box measured 18.86 to 18.98rem with room to spare (flex-basis 20rem)
BRAND_AND_SEARCH = """() => {
  const word = document.querySelector('.topbar .brand .word');
  const search = document.querySelector('.topbar .topsearch');
  const rem = parseFloat(getComputedStyle(document.documentElement).fontSize);
  return {cut: word.scrollWidth > word.clientWidth + 1, search: search.getBoundingClientRect().width / rem};
}"""


def test_a_long_server_name_gives_way_before_the_search_box_and_the_links(server: Server) -> None:
    """The order the one-row bar gives up room in (review R12-1, 83ec5a7): the brand's word first
    (an ellipsis), then the search box down to 9rem; the links never."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _admin(server)
    object.__setattr__(server.app.state.gateway.settings, "server_name", LONG_NAME)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        for width in (900, 960, 1000, 1100, 1280, 1440, 1920):
            for zoom in ("none", "root-110", "root-125", "text-125"):
                page.set_viewport_size({"width": width, "height": 800})
                page.goto(f"{server.base}/history", wait_until="networkidle")
                _zoom(page, zoom)
                page.wait_for_timeout(20)
                g = page.evaluate(BAR_OVERLAP)
                if g["overlap"] or g["outside"] or g["doc"] != g["win"]:
                    problems.append(f"@{width}px {zoom}: {g}")
                b = page.evaluate(BRAND_AND_SEARCH)
                if b["search"] < 9 - 0.05 or (b["search"] < FULL_SEARCH and not b["cut"]):
                    problems.append(f"@{width}px {zoom}: search box shrank first: {b}")
        page.set_viewport_size({"width": 1280, "height": 800})
        page.goto(f"{server.base}/history", wait_until="networkidle")
        at_1280 = page.evaluate(BRAND_AND_SEARCH)
        browser.close()
    assert problems == [], "\n".join(problems)
    assert at_1280["cut"] and at_1280["search"] >= FULL_SEARCH, at_1280  # the name is cut, not the box
