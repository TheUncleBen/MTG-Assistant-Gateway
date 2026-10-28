"""The top bar on a computer (navigation round 2, Ben 2026-10-25): the four places (Home, Decks,
Binder, Assistant) at every width and text size, then "More ▾" with its three groups (Find: Search
and Precons; Your cards: Scan and My card models; Community: Forum). History and Reports mark
Assistant; a page under More marks More. Phones and the touch rail have no More ▾ in the bar."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200

pytest.importorskip("playwright")

SHOWN = """() => [...document.querySelectorAll('.topbar nav.site > a, .topbar nav.site > details > summary')]
  .filter(e => e.getClientRects().length).map(e => e.textContent.trim())"""


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_the_bar_marks_the_place_and_a_page_under_more(server: Server) -> None:
    sid = server.sign_in()
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    nav = h.get("/reports").text.split("<nav class='site' aria-label='Site'>")[1].split("</nav>")[0]
    assert "<a href='/proposals' aria-current='page'>Assistant</a>" in nav
    assert "<details class='dd navmore'><summary>More<span aria-hidden='true'>▾</span>" in nav
    menu = nav.split("<div class='menu'>")[1]
    assert [x.split("</div>")[0] for x in menu.split("<div class='head'>")[1:]] == [
        "Find",
        "Your cards",
        "Community",
    ]
    assert [x.split("</svg>")[1] for x in menu.split("</a>")[:-1]] == [
        "Search decks and cards",
        "Precons",
        "Scan",
        "My card models",
        "Forum",
    ]
    nav = h.get("/scan").text.split("<nav class='site' aria-label='Site'>")[1].split("</nav>")[0]
    assert "<summary class='on'>More" in nav and "<a href='/scan' aria-current=page>" in nav


def test_the_four_places_and_more_at_every_width_and_large_text(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    bar = ["Home", "Decks", "Binder", "Assistant", "More▾"]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width, zoom, touch, want in [
            (1440, False, False, bar),
            (1280, False, False, bar),
            (1000, False, False, bar),
            (700, False, False, bar),
            (1280, True, False, bar),
            (390, False, True, []),
            (673, False, True, []),
        ]:
            kw = {"has_touch": True, "is_mobile": True} if touch else {}
            ctx = browser.new_context(viewport={"width": width, "height": 800}, **kw)
            ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
            page = ctx.new_page()
            page.goto(f"{server.base}/", wait_until="networkidle")
            if zoom:
                page.evaluate(ROOT_200)
            assert page.evaluate(SHOWN) == want, (width, zoom, touch)
            assert page.evaluate("document.documentElement.scrollWidth") <= width, (width, zoom)
            if want:
                page.locator(".navmore > summary").click()
                menu = page.locator(".navmore .menu")
                box = menu.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= width
                menu.locator("a", has_text="Forum").click()
                page.wait_for_url("**/forum")
            ctx.close()
        browser.close()
