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
    sid = _admin(server)  # seven links: the six sections and Admin
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
        assert g["links"] == 7, (width, g)
        assert g["doc"] == g["win"] == width, (width, g)  # no sideways scroll
        assert g["last"] + 8 <= g["avatar"], (width, g)  # the last link ends before the account button
    assert phone["links"] == 0, phone  # the phone layout: links move to the tab bar
