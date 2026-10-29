"""The second 0.7.46 polish round in headless Chromium: a list row's ⋮ pinned at the right of its
title (Reports, History), Load more under the Reports table, and a deck's grid view with a long
unbroken category name at large text. Skipped without a Chromium build."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from mtg_gateway.archidekt_csv import parse_export, to_deck_json

from .test_editor_browser import _link
from .test_history_page import store_report
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

LONG_CATS = [
    "Rampandlandfallsynergiesunbrokencategorynamenospace",  # 51 characters, no space
    "Card draw and card advantage engines for the long game ahead",
]
ZOOMS = {"100": "() => {}", "root": ROOT_200, "text": TEXT_200}
SCROLLS = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


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


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@contextmanager
def _page(browser, server: Server, sid: str, width: int, *, touch: bool = True):
    ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=touch, is_mobile=touch)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        yield page
        assert errors == []
    finally:
        ctx.close()


# -- a row's ⋮ ----------------------------------------------------------------------------------------

# Every row's ⋮ against its title cell: on the title's first line (its top above the name's
# bottom) and at the cell's right edge.
KEBABS = """
(sel) => [...document.querySelectorAll(sel + ' .tr .title > .kebab.inline')].map((k) => {
  const cell = k.parentElement.getBoundingClientRect(), r = k.getBoundingClientRect();
  const t = k.parentElement.querySelector('.t').getBoundingClientRect();
  return { firstLine: r.top < t.bottom, right: r.right >= cell.right - 1,
           inside: r.right <= cell.right + 1 }; })
"""


@pytest.mark.parametrize("zoom", ["100", "root", "text"])
@pytest.mark.parametrize("path,table", [("/reports", ".rp-t")])
def test_a_rows_menu_button_sits_at_the_right_of_its_title_line(chromium, server, path, table, zoom) -> None:
    """The ⋮ added beside the title (menus.js) followed the name as a block, so on a phone it sat
    on a line of its own under the status label. It is pinned at the title's right now."""
    sid = _link(server)
    for i in range(3):
        store_report(server.db, "user-1", f"r{i}", deck_name=f"Deck {i} with a long name for the row")
    with _page(chromium, server, sid, 390) as page:
        page.goto(f"{server.base}{path}", wait_until="networkidle")
        page.evaluate(ZOOMS[zoom])
        page.wait_for_timeout(30)
        got = page.evaluate(KEBABS, table)
        assert len(got) >= 3, got
        assert all(g["firstLine"] and g["right"] and g["inside"] for g in got), got
        assert page.evaluate(SCROLLS) == 0


# -- Reports: Load more ---------------------------------------------------------------------------------


def test_reports_load_more_adds_the_next_rows_in_place(chromium, server) -> None:
    """Newer / Older paged a sorted list; Load more (the standard's button) adds the next
    reports under the ones shown, with their menus, and ends with how many there are."""
    from mtg_gateway.reports_page import PAGE

    sid = _link(server)
    n = PAGE + 3
    for i in range(n):
        store_report(server.db, "user-1", f"r{i:03d}", deck_name=f"Deck {i}", taken_at=1_700_000_000 + i)
    with _page(chromium, server, sid, 390) as page:
        page.goto(f"{server.base}/reports", wait_until="networkidle")
        assert page.locator(".ui-pager").count() == 0
        rows = page.locator(".rp-t .tr:not(.th)")
        assert rows.count() == PAGE
        more = page.locator(".reports-page .loadmore [data-load-more]")
        assert more.text_content() == "Load more reports"
        assert page.locator(".reports-page .loadmore p").text_content() == f"{PAGE} of {n}"
        more.click()
        page.wait_for_function(f"() => document.querySelectorAll('.rp-t .tr:not(.th)').length === {n}")
        assert page.url.endswith("/reports")  # in place, not the next page
        assert page.locator(".rp-t .tr:not(.th) .kebab").count() == n
        assert page.locator(".reports-page .loadmore [data-load-more]").count() == 0
        assert page.locator(".reports-page .loadmore p").text_content() == f"All {n} reports shown."
        # the oldest report came last
        assert page.locator(".rp-t .tr:not(.th)").last.locator(".title .t a").text_content() == "Deck 0"


# -- a deck's grid view with a long category name -------------------------------------------------------


@pytest.mark.parametrize("zoom", ["100", "root", "text"])
def test_the_grid_view_keeps_a_long_category_name_inside_the_screen(chromium, server, zoom) -> None:
    """A 51-letter category name with no space widened its stack past a 344 px screen at large
    text; the heading shortens with … inside its column now."""
    cards = parse_export((Path(__file__).parent / "fixtures" / "sample_deck.csv").read_text(encoding="utf-8"))
    server.ark.decks[50] = to_deck_json(cards[:20], deck_id=50, name="Long categories", owner="alice")
    server.ark.decks[50]["categories"] += [
        {"name": c, "isPremier": False, "includedInDeck": True} for c in LONG_CATS
    ]
    for k, c in enumerate(server.ark.decks[50]["cards"]):
        if k % 3 != 2:
            c["categories"] = [LONG_CATS[k % 2]]
    server.ark.users["alice"]["decks"].append(50)
    sid = _link(server)
    with _page(chromium, server, sid, 344) as page:
        page.goto(f"{server.base}/decks/50?view=grid", wait_until="networkidle")
        page.evaluate(ZOOMS[zoom])
        page.wait_for_timeout(30)
        assert page.evaluate(SCROLLS) == 0
        heads = page.locator(".deckview .stackhead .title")
        assert heads.count() >= 2
        assert page.evaluate(
            "() => [...document.querySelectorAll('.deckview .stackhead .title')].every("
            "(t) => t.getBoundingClientRect().right <= document.documentElement.clientWidth)"
        )
