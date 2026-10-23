"""The top bar on a computer: from 1200 px (85.7em of the bar's 14 px text)
it lists Reports and Watchlist after History; narrower, Proposals and the links after it fold into
"More ▾", which lists the phone's More sheet (Proposals, History, Reports, Watchlist, My lists, Guide,
Account). Large text folds them too. Phones and the touch rail have no More ▾ in the bar."""

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


def test_the_bar_marks_a_folded_page_on_more(server: Server) -> None:
    sid = server.sign_in()
    page = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}).get("/reports").text
    nav = page.split("<nav class='site' aria-label='Site'>")[1].split("</nav>")[0]
    assert "<a href='/reports' aria-current='page' class=fold>Reports</a>" in nav
    assert "<a href='/watchlist' class=fold>Watchlist</a>" in nav
    assert "<details class='dd navmore'><summary class='on'>More<span aria-hidden='true'>▾</span>" in nav
    menu = nav.split("<div class='menu'>")[1]
    assert [x.split("</svg>")[1] for x in menu.split("</a>")[:-1]] == [
        "Proposals",
        "History",
        "Reports",
        "Watchlist",
        "My lists",
        "Guide",
        "Account",
    ]


def test_more_folds_below_1200_px_and_with_large_text(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in()
    full = ["Home", "Decks", "Search", "Collection", "Scan", "Proposals", "History", "Reports", "Watchlist"]
    folded = ["Home", "Decks", "Search", "Collection", "Scan", "More▾"]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width, zoom, touch, want in [
            (1280, False, False, full),
            (1200, False, False, full),
            (1199, False, False, folded),
            (1000, False, False, folded),
            (700, False, False, folded),
            (1280, True, False, folded),
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
            if want == folded:
                page.locator(".navmore > summary").click()
                menu = page.locator(".navmore .menu")
                box = menu.bounding_box()
                assert box and box["x"] >= 0 and box["x"] + box["width"] <= width
                menu.locator("a", has_text="Reports").click()
                page.wait_for_url("**/reports")
            ctx.close()
        browser.close()
