"""The deck page changes one row of a card that has another row in the same zone (Ben,
2026-10-28: Sol Ring on the Maybeboard and in "Optional Artwork", both left out of the deck). The
row's menu offers "Printings…", and a printing change or a removal sends the row's relation
id, so that row only changes; a refused removal says the card is back in its own category. In
headless Chromium; skipped without a Chromium build."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .test_printing_picker_browser import (
    EDIT_URL,
    Server,
    _chromium,
    _open_menu,
    _open_picker,
    _page,
    _server,
)

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = _server(tmp_path)
    try:
        yield s
    finally:
        s.stop()


def _rows(server: Server) -> dict[str, tuple[str, str]]:
    """Deck 42's Sol Ring rows: primary category ("" for the deck's own) -> (set, number)."""
    return {
        (c["categories"] or [""])[0]: (c["card"]["edition"]["editioncode"], c["card"]["collectorNumber"])
        for c in server.ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == "Sol Ring"
    }


def _rel(server: Server, category: str) -> int:
    return next(
        c["id"]
        for c in server.ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == "Sol Ring" and (c["categories"] or [""])[0] == category
    )


def test_a_side_row_of_a_card_with_two_changes_on_its_own(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    server.ark.add_side_row(42, "Sol Ring", 1, category="Optional Artwork")
    sid = server.sign_in_and_link()
    before = _rows(server)
    assert set(before) == {"", "Maybeboard", "Optional Artwork"}
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        maybe = page.locator(".deckview .row[data-card='Sol Ring'][data-cat='Maybeboard']")
        art = page.locator(".deckview .row[data-card='Sol Ring'][data-cat='Optional Artwork']")
        assert maybe.count() == 1 and art.count() == 1
        # a side row's menu says which category it leaves, not "Remove from deck"
        for row, words in (
            (maybe, "Remove from “Maybeboard”"),
            (art, "Remove from “Optional Artwork”"),
            (page.locator(".deckview .row[data-card='Sol Ring'][data-zone='main']"), "Remove from deck"),
        ):
            _open_menu(page, row)
            items = page.locator(".ctxmenu [role=menuitem]").all_inner_texts()
            assert words in [" ".join(t.split()) for t in items], items
            page.keyboard.press("Escape")

        # a printing for the Maybeboard row: that row only
        _open_picker(page, maybe)
        page.locator(".printpick .pptile", has_text="SLD #1075").first.click()
        with page.expect_request(EDIT_URL) as req:
            page.locator(".pppreview .ppuse").click()
        sent = json.loads(req.value.post_data)["changes"]
        assert sent == [
            {
                "action": "set_printing",
                "card_name": "Sol Ring",
                "set_code": "sld",
                "collector_number": "1075",
                "zone": "side",
                "relation_id": _rel(server, "Maybeboard"),
            }
        ], sent
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        after = _rows(server)
        assert after["Maybeboard"] == ("sld", "1075"), after
        assert after[""] == before[""] and after["Optional Artwork"] == before["Optional Artwork"], after

        # a refused removal of the "Optional Artwork" row: back in its own category
        server.ark.fail_card_writes = True
        _open_menu(page, art)
        with page.expect_request(EDIT_URL) as req:
            page.locator(".ctxmenu [role=menuitem]", has_text="Remove from “Optional Artwork”").click()
        rel = _rel(server, "Optional Artwork")
        assert json.loads(req.value.post_data)["changes"] == [
            {"action": "remove", "card_name": "Sol Ring", "zone": "side", "relation_id": rel}
        ]
        err = page.locator(".deck-toast.error .msg")
        err.wait_for(timeout=8000)
        assert err.inner_text().endswith("Sol Ring is back in “Optional Artwork”."), err.inner_text()
        server.ark.fail_card_writes = False

        # and saved: that row goes, the Maybeboard row and the deck's own stay
        art.wait_for(timeout=3000)
        _open_menu(page, art)
        page.locator(".ctxmenu [role=menuitem]", has_text="Remove from “Optional Artwork”").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert _rows(server) == {"": before[""], "Maybeboard": ("sld", "1075")}
        assert errors == [], errors
        browser.close()
