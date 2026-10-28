"""The shell's small fixes of 0.7.46 in headless Chromium: search in a narrow window with a mouse,
the phone's suggestion list as wide as its box, the bell when the session has ended, Home's deck
tiles on the Fold's cover screen, the app's brand on a phone, Back after a theme change, the tab
labels at large text, and the install page's address. Skipped without a Chromium build."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from mtg_gateway.archidekt_csv import parse_export, to_deck_json

from .test_editor_browser import _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_suggest_browser import server as _server

pytest.importorskip("playwright")

server = _server  # the fixture, under its own name
CARDS = parse_export((Path(__file__).parent / "fixtures" / "sample_deck.csv").read_text(encoding="utf-8"))
APP_UA = "Mozilla/5.0 (Linux; Android 14) MTGAssistant/0.7.46"


@pytest.fixture(scope="module")
def chromium():
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        yield browser
        browser.close()


@contextmanager
def _page(browser, server: Server, sid: str, width: int, *, touch: bool = False, **extra):
    ctx = browser.new_context(
        viewport={"width": width, "height": 800}, has_touch=touch, is_mobile=touch, **extra
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    try:
        yield ctx.new_page()
    finally:
        ctx.close()


SHOWN = "sel => { const e = document.querySelector(sel); return !!e && e.getClientRects().length > 0 }"


# -- search ----------------------------------------------------------------------------------------


def test_a_narrow_window_with_a_mouse_has_the_magnifier_and_it_opens_the_box(chromium, server) -> None:
    """600 to 899 px with a mouse (a half-width laptop window): the bar had neither the box nor the
    magnifier. The magnifier now shows and opens the box under the bar, as on a phone."""
    sid = server.sign_in()
    for width in (600, 700, 899):
        with _page(chromium, server, sid, width) as page:
            page.goto(f"{server.base}/decks", wait_until="load")
            assert page.evaluate(SHOWN, ".topbar .searchbtn"), width
            assert not page.evaluate(SHOWN, ".topbar .topsearch"), width
            page.click(".topbar .searchbtn")
            page.wait_for_function("() => document.activeElement && document.activeElement.id === 'site-q'")
            box = page.evaluate(
                "() => { const r = document.querySelector('.topbar .topsearch').getBoundingClientRect();"
                " return {left: r.left, right: r.right, top: r.top, bar:"
                " document.querySelector('.topbar').getBoundingClientRect().bottom,"
                " doc: document.documentElement.scrollWidth, win: innerWidth}; }"
            )
            assert box["top"] >= box["bar"] - 1 and box["doc"] <= box["win"], (width, box)
            # the text links stay where they were
            assert page.evaluate(SHOWN, ".topbar nav.site"), width
    # a touch screen at the same width keeps the box in the bar and no magnifier
    with _page(chromium, server, sid, 700, touch=True) as page:
        page.goto(f"{server.base}/decks", wait_until="load")
        assert page.evaluate(SHOWN, ".topbar .topsearch")
        assert not page.evaluate(SHOWN, ".topbar .searchbtn")
    # 900 px with a mouse: the box in the bar, as before
    with _page(chromium, server, sid, 900) as page:
        page.goto(f"{server.base}/decks", wait_until="load")
        assert page.evaluate(SHOWN, ".topbar .topsearch")
        assert not page.evaluate(SHOWN, ".topbar .searchbtn")


# the list, the input and the folded-out box share both edges (the input stopped 20rem in once
# suggest.js wrapped it, and its list with it, about 80 px short of the box on a phone)
LIST_AND_BOX = """() => {
  const l = document.querySelector('.topbar .suggest-list').getBoundingClientRect();
  const i = document.querySelector('#site-q').getBoundingClientRect();
  const b = document.querySelector('.topbar .topsearch .box').getBoundingClientRect();
  return {listLeft: l.left, listRight: l.right, inputLeft: i.left, inputRight: i.right,
    boxLeft: b.left, boxRight: b.right};
}"""


@pytest.mark.parametrize("width,touch", [(390, True), (344, True), (700, False)])
def test_the_folded_out_search_list_is_as_wide_as_its_box(chromium, server, width: int, touch: bool) -> None:
    sid = server.sign_in()
    with _page(chromium, server, sid, width, touch=touch) as page:
        page.goto(f"{server.base}/decks", wait_until="load")
        page.click(".topbar .searchbtn")
        page.fill("#site-q", "Shelob")
        page.wait_for_function(
            "() => { const l = document.querySelector('.topbar .suggest-list');"
            " return l && l.getClientRects().length > 0 && l.children.length > 0; }"
        )
        g = page.evaluate(LIST_AND_BOX)
        for edge in ("Left", "Right"):
            assert abs(g["list" + edge] - g["box" + edge]) <= 1, g
            assert abs(g["input" + edge] - g["box" + edge]) <= 1, g


# -- the bell after the session ended --------------------------------------------------------------


def test_the_bell_says_signed_out_when_the_session_has_ended(chromium, server) -> None:
    sid = server.sign_in()
    with _page(chromium, server, sid, 1280) as page:
        page.goto(f"{server.base}/decks", wait_until="load")
        page.wait_for_function("() => !!window.MtgBell")
        # signed out in another tab: the session is gone on the server
        server.db.delete_browser_session(sid)
        page.click(".topbar a.bell")
        page.wait_for_selector(".mtgbox:visible .notice", timeout=10_000)
        text = page.inner_text(".mtgbox:visible")
        assert "You're signed out" in text and "No connection" not in text, text
        sign_in = page.locator(".mtgbox:visible a", has_text="Sign in")
        assert sign_in.count() == 1
        assert sign_in.get_attribute("href") == "/login?next=%2Fdecks"


def test_the_bell_still_says_no_connection_when_the_gateway_cannot_be_reached(chromium, server) -> None:
    sid = server.sign_in()
    with _page(chromium, server, sid, 1280) as page:
        page.goto(f"{server.base}/decks", wait_until="load")
        page.wait_for_function("() => !!window.MtgBell")
        page.route("**/notifications", lambda route: route.abort())
        page.click(".topbar a.bell")
        page.wait_for_selector(".mtgbox:visible .notice", timeout=10_000)
        text = page.inner_text(".mtgbox:visible")
        assert "No connection" in text and "signed out" not in text, text


# -- Home's deck tiles on the Fold's cover screen --------------------------------------------------


@pytest.mark.parametrize("width", [320, 344, 390])
def test_home_keeps_two_deck_tiles_a_row_down_to_320_px(chromium, server, width: int) -> None:
    for i, did in enumerate((301, 302, 303)):
        server.ark.decks[did] = to_deck_json(CARDS[: 10 + i], deck_id=did, name=f"Deck {did}", owner="alice")
        server.ark.users["alice"]["decks"].append(did)
    sid = _link(server)
    with _page(chromium, server, sid, width, touch=True) as page:
        page.goto(f"{server.base}/", wait_until="networkidle")
        page.wait_for_selector(".home .recent:not(.skeleton) a", timeout=10_000)
        tops = page.evaluate(
            "() => [...document.querySelectorAll('.home .recent:not(.skeleton) > *')]"
            ".slice(0, 2).map(e => Math.round(e.getBoundingClientRect().top))"
        )
        assert len(tops) == 2 and tops[0] == tops[1], (width, tops)
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")


# -- the app's brand on a phone ---------------------------------------------------------------------


@pytest.mark.parametrize("width,shown", [(344, False), (390, False), (600, True), (884, True)])
def test_the_app_shows_only_the_brand_mark_on_a_phone(chromium, server, width: int, shown: bool) -> None:
    sid = server.sign_in()
    with _page(chromium, server, sid, width, touch=True, user_agent=APP_UA) as page:
        page.goto(f"{server.base}/decks", wait_until="load")
        assert page.evaluate("() => document.body.classList.contains('app')")
        assert page.evaluate(SHOWN, ".topbar .brand .mark")
        assert page.evaluate(SHOWN, ".topbar .brand .word") == shown


# -- Back after a theme change ---------------------------------------------------------------------


def test_back_after_a_theme_change_goes_to_the_page_before(chromium, server) -> None:
    sid = server.sign_in()
    with _page(chromium, server, sid, 1280, color_scheme="light") as page:
        page.goto(f"{server.base}/decks", wait_until="load")
        page.goto(f"{server.base}/account", wait_until="load")
        page.click("details.yrow summary:has-text('Theme')")
        with page.expect_navigation(wait_until="load"):
            page.click("form[action='/theme'] button[value='dark']")
        assert page.url == f"{server.base}/account"
        assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "dark"
        assert page.locator("details.yrow summary:has-text('Theme') .val").inner_text() == "Dark"
        page.go_back(wait_until="load")
        assert page.url == f"{server.base}/decks"
        # the choice stuck: the page before is dark too
        assert page.evaluate("() => document.documentElement.getAttribute('data-theme')") == "dark"
        # Layout the same way
        page.go_forward(wait_until="load")
        page.click("details.yrow summary:has-text('Layout')")
        with page.expect_navigation(wait_until="load"):
            page.click("form[action='/layout'] button[value='desktop']")
        assert page.url == f"{server.base}/account"
        assert page.locator("details.yrow summary:has-text('Layout') .val").inner_text() == "Desktop layout"
        page.go_back(wait_until="load")
        assert page.url == f"{server.base}/decks"


# -- tab and rail labels at large text -------------------------------------------------------------


@pytest.mark.parametrize("width,touch", [(390, True), (840, True)])
def test_the_tab_and_rail_labels_grow_with_the_text_size(chromium, server, width: int, touch: bool) -> None:
    sid = server.sign_in()
    with _page(chromium, server, sid, width, touch=touch) as page:
        page.goto(f"{server.base}/decks", wait_until="load")
        size = "() => parseFloat(getComputedStyle(document.querySelector('.tabbar > a')).fontSize)"
        normal = page.evaluate(size)
        page.evaluate("() => document.documentElement.style.setProperty('font-size', '28px', 'important')")
        big = page.evaluate(size)
        assert normal >= 11 and big > normal, (normal, big)
        assert big <= 16, big  # capped: the labels stay inside their tabs
        assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth")


# -- the install page's address ----------------------------------------------------------------------

CONTRAST = """() => {
  const c = document.querySelector('.inst .card p.muted a code');
  const parse = s => s.match(/[\\d.]+/g).slice(0, 4).map(Number);
  const lum = ([r, g, b]) => {
    const f = v => { v /= 255; return v <= .03928 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4; };
    return .2126 * f(r) + .7152 * f(g) + .0722 * f(b); };
  let bg = null;
  for (let e = c; e; e = e.parentElement) {
    const v = parse(getComputedStyle(e).backgroundColor); if (v.length < 4 || v[3] > 0.5) { bg = v; break; }
  }
  if (!bg) bg = parse(getComputedStyle(document.body).backgroundColor);
  const fg = parse(getComputedStyle(c).color);
  const a = lum(fg), b = lum(bg);
  const outer = getComputedStyle(c.parentElement).backgroundColor;
  return {ratio: (Math.max(a, b) + .05) / (Math.min(a, b) + .05), fg, bg, outer};
}"""


@pytest.mark.parametrize("scheme", ["light", "dark"])
def test_the_install_pages_plain_text_address_reads_clearly(chromium, server, scheme: str) -> None:
    sid = server.sign_in()
    with _page(chromium, server, sid, 390, color_scheme=scheme) as page:
        page.goto(f"{server.base}/install?for=chatgpt", wait_until="load")
        g = page.evaluate(CONTRAST)
        assert g["ratio"] >= 4.5, g
        # the address is not drawn on the waiting count's orange
        assert g["outer"] in ("rgba(0, 0, 0, 0)", "transparent"), g
