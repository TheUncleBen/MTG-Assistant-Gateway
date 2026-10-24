"""A custom card's rows in the deck's list views (Text, Table, Brewer) say Custom exactly once at
every width, as the mark under the name (deckpage.custom_card_row): on a phone the Set column is
hidden, so the earlier Set-cell word left the rows unmarked (0.7.44 review, L16's follow-up), and
beside the name the mark cut the name short. The name keeps its room. Skipped without a Chromium
build."""

from __future__ import annotations

import pytest

from .test_cardsearch_browser import _chromium, _page
from .test_deck_custom_cards import _custom
from .test_editor_browser import _link
from .test_suggest_browser import Server
from .test_ui_pass_c_browser import _seed

VIEWS = ("text", "table", "brewer")
# the row's elements whose own text is the word Custom, on screen
VISIBLE_CUSTOM = """(row) => [...row.querySelectorAll('*')]
  .filter(e => e.childElementCount === 0 && e.textContent.trim().toLowerCase() === 'custom')
  .filter(e => { const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0
    && getComputedStyle(e).visibility !== 'hidden'; })
  .map(e => [e.className, e.title])"""
NAME_FITS = """(row) => { const n = row.querySelector('.name');
  return {fits: n.scrollWidth <= n.clientWidth, text: n.textContent}; }"""


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path, writes_enabled=True)
    ark = s.ark
    _seed(ark)  # Grizzled Mentor (41) in Ramp
    ark.account.customs["alice"].append(_custom(45, "Spirit Token Pile"))
    ark.decks[42]["customCards"].append(
        {"id": 3102, "quantity": 20, "categories": ["Artifact"], "card": _custom(45, "Spirit Token Pile")}
    )
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("width", [390, 810, 1366])
def test_a_custom_row_says_custom_once_under_its_whole_name(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, "light", exe)
        for view in VIEWS:
            page.goto(f"{server.base}/decks/42?view={view}", wait_until="networkidle")
            for cid in (41, 45):
                row = page.locator(f".deckview .row[data-custom-id='{cid}']")
                row.scroll_into_view_if_needed()
                marks = row.evaluate(VISIBLE_CUSTOM)
                assert len(marks) == 1, (view, width, cid, marks)
                assert marks[0] == ["custom-badge", "A custom card, made on Archidekt"], (view, width, marks)
                # the mark is on its own line under the name, never beside it
                name_box = row.locator(".name").bounding_box()
                mark_box = row.locator(".custom-badge").bounding_box()
                under = mark_box["y"] >= name_box["y"] + name_box["height"] - 1
                assert under, (view, width, name_box, mark_box)
                fit = row.evaluate(NAME_FITS)
                assert fit["fits"], (view, width, fit)
        assert errors == []
        browser.close()
