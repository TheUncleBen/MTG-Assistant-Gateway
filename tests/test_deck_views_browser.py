"""0.7.18 in headless Chromium: the deck page's Table and Scroll views; putting stacks in your own
order (arrow keys on a stack's handle, kept in this browser per deck and grouping, Reset puts the
deck's order back); selecting several cards and moving, refinishing or removing them in one save
with one Undo. Screenshots go to MTG_SHOTS when it is set. Skipped without a Chromium build."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from .test_deck_menu_browser import EDIT_URL, Server, _chromium, _overflow, _page

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_SHOTS")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), full_page=False)


def _rows(server: Server, name: str) -> list[dict]:
    return [c for c in server.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == name]


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1366, "light")])
def test_table_and_scroll_views(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42?view=table", wait_until="networkidle")
        assert page.locator(".deckview.table").count() == 1
        assert page.locator(".deckview.table .th").count() >= 1
        row = page.locator(".deckview.table .row[data-card='Cultivate']").first
        assert "Sorcery" in row.locator(".ty").inner_text()
        assert _overflow(page) == 0
        _shot(page, f"deck-table-{width}-{scheme}")
        row.click()
        page.wait_for_selector(".cardview.open", timeout=3000)
        page.keyboard.press("Escape")

        page.goto(f"{server.base}/decks/42?view=scroll", wait_until="networkidle")
        strip = page.locator(".deckview.scroll .cards").first
        assert strip.evaluate("e => getComputedStyle(e).overflowX") == "auto"
        # a group wider than the window scrolls sideways in its own strip
        wide = (
            "() => [...document.querySelectorAll('.deckview.scroll .cards')]"
            ".some(e => e.scrollWidth > e.clientWidth)"
        )
        assert page.evaluate(wide)
        assert _overflow(page) == 0  # ... inside itself, never the page
        _shot(page, f"deck-scroll-{width}-{scheme}")
        assert errors == []
        browser.close()


def test_stack_order_by_keyboard_is_kept_and_reset(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        names = page.eval_on_selector_all(".deckview .stack", "l => l.map(s => s.dataset.group)")
        assert len(names) >= 3
        grip = page.locator(f".stack[data-group='{names[1]}'] .stackgrip")
        grip.focus()
        page.keyboard.press("ArrowUp")
        now = page.eval_on_selector_all(".deckview .stack", "l => l.map(s => s.dataset.group)")
        assert now[:2] == [names[1], names[0]]
        assert grip.evaluate("e => document.activeElement === e")
        assert "position 1 of" in page.locator("p.sr-only[aria-live]").inner_text()
        page.reload(wait_until="networkidle")
        kept = page.eval_on_selector_all(".deckview .stack", "l => l.map(s => s.dataset.group)")
        assert kept == now
        # the order is per grouping: another grouping keeps its own
        page.locator(".stack-reset").click()
        back = page.eval_on_selector_all(".deckview .stack", "l => l.map(s => s.dataset.group)")
        assert back == names
        page.reload(wait_until="networkidle")
        assert page.eval_on_selector_all(".deckview .stack", "l => l.map(s => s.dataset.group)") == names
        assert page.locator(".stack-reset").count() == 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme", "view"), [(390, "dark", "text"), (1366, "light", "grid")])
def test_bulk_move_finish_and_remove(server: Server, width: int, scheme: str, view: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    picks = ["Cultivate", "Kodama's Reach"]
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42?view={view}", wait_until="networkidle")
        sel = ".deckview .c, .deckview .row"
        page.locator(".bulkbar button", has_text="Select cards").click()
        tools = page.locator(".bulkbar .bulktools")
        assert tools.is_visible()
        assert page.locator(".bulkbar select").first.is_disabled()
        for name in picks:
            card = page.locator(f".deckview [data-card=\"{name}\"]").first
            card.scroll_into_view_if_needed()
            card.click()
            assert card.get_attribute("aria-pressed") == "true"
        assert page.locator(".cardview.open").count() == 0  # a tap selects; the viewer stays shut
        assert page.locator(".bulkbar .count").inner_text() == "2 cards selected"
        page.locator(".bulkbar").scroll_into_view_if_needed()
        _shot(page, f"deck-bulk-{view}-{width}-{scheme}")

        # move both in one save, with one Undo
        with page.expect_response(EDIT_URL) as req:
            page.locator(".bulkbar select[aria-label^='Move']").select_option("Maybeboard")
        changes = json.loads(req.value.request.post_data)["changes"]
        assert sorted(c["card_name"] for c in changes) == sorted(picks)
        assert all(c["action"] == "set_category" and c["category"] == "Maybeboard" for c in changes)
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert all(_rows(server, n)[0]["categories"] == ["Maybeboard"] for n in picks)
        assert page.locator(".deckview .picked").count() == 0 and not tools.is_visible()
        with page.expect_response(EDIT_URL) as req2:
            page.locator(".deck-toast button", has_text="Undo").click()
        assert len(json.loads(req2.value.request.post_data)["changes"]) == 2
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert all(_rows(server, n)[0]["categories"] != ["Maybeboard"] for n in picks)

        # finish: one change per name; the badge redraws from the answer
        page.locator(".bulkbar button", has_text="Select cards").click()
        for name in picks:
            page.locator(f".deckview [data-card=\"{name}\"]").first.click()
        with page.expect_response(EDIT_URL) as req3:
            page.locator(".bulkbar select[aria-label^='Set the finish']").select_option("foil")
        assert json.loads(req3.value.request.post_data)["changes"] == [
            {"action": "set_finish", "card_name": n, "finish": "foil"} for n in picks
        ]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert all(r["modifier"] == "Foil" for n in picks for r in _rows(server, n))
        first = page.locator(f".deckview [data-card=\"{picks[0]}\"]").first
        assert first.get_attribute("data-finish") == "Foil"

        # remove, with Escape leaving selection first to show it clears
        page.locator(".bulkbar button", has_text="Select cards").click()
        page.locator(f".deckview [data-card=\"{picks[0]}\"]").first.click()
        page.keyboard.press("Escape")
        assert page.locator(".deckview .picked").count() == 0
        page.locator(".bulkbar button", has_text="Select cards").click()
        for name in picks:
            page.locator(f".deckview [data-card=\"{name}\"]").first.click()
        before = page.locator(sel).count()
        with page.expect_response(EDIT_URL):
            page.locator(".bulkbar button", has_text="Remove").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert all(not _rows(server, n) for n in picks)
        assert page.locator(sel).count() == before - 2
        assert _overflow(page) == 0
        assert errors == []
        browser.close()
