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
