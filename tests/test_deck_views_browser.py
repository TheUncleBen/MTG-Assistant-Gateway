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
            card = page.locator(f'.deckview [data-card="{name}"]').first
            card.scroll_into_view_if_needed()
            card.click()
            assert card.get_attribute("aria-pressed") == "true"
        assert page.locator(".cardview.open").count() == 0  # a tap selects; the viewer stays shut
        assert page.locator(".bulkbar .count").inner_text() == "2 cards selected"
        # the themed dropdown shows the select's own first entry, and is enabled with cards picked
        move_btn = page.locator(".bulkbar button[aria-label^='Move the selected']")
        assert "Move to" in move_btn.inner_text() and move_btn.is_enabled()
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
            page.locator(f'.deckview [data-card="{name}"]').first.click()
        with page.expect_response(EDIT_URL) as req3:
            page.locator(".bulkbar select[aria-label^='Set the finish']").select_option("foil")
        assert json.loads(req3.value.request.post_data)["changes"] == [
            {"action": "set_finish", "card_name": n, "finish": "foil"} for n in picks
        ]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert all(r["modifier"] == "Foil" for n in picks for r in _rows(server, n))
        first = page.locator(f'.deckview [data-card="{picks[0]}"]').first
        assert first.get_attribute("data-finish") == "Foil"

        # remove, with Escape leaving selection first to show it clears
        page.locator(".bulkbar button", has_text="Select cards").click()
        page.locator(f'.deckview [data-card="{picks[0]}"]').first.click()
        page.keyboard.press("Escape")
        assert page.locator(".deckview .picked").count() == 0
        page.locator(".bulkbar button", has_text="Select cards").click()
        for name in picks:
            page.locator(f'.deckview [data-card="{name}"]').first.click()
        before = page.locator(sel).count()
        with page.expect_response(EDIT_URL):
            page.locator(".bulkbar button", has_text="Remove").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert all(not _rows(server, n) for n in picks)
        assert page.locator(sel).count() == before - 2
        assert _overflow(page) == 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1366, "light")])
def test_colour_tag_from_the_card_menu_and_undo(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        row = page.locator(".deckview .row[data-card='Cultivate']").first
        row.scroll_into_view_if_needed()
        row.click(button="right")
        page.locator(".ctxmenu [role=menuitem]", has_text="Colour tag").click()
        form = page.locator(".bulkbar .tagform")
        assert form.is_visible() and row.get_attribute("aria-pressed") == "true"
        page.locator(".bulkbar input[aria-label='Tag name']").fill("Have")
        page.locator(".bulkbar input[aria-label='Tag colour']").evaluate(
            "e => { e.value = '#2ccce4'; e.dispatchEvent(new Event('input')); }"
        )
        _shot(page, f"deck-tag-form-{width}-{scheme}")
        with page.expect_response(EDIT_URL) as r:
            page.locator(".bulkbar button", has_text="Apply tag").click()
        assert json.loads(r.value.request.post_data)["changes"] == [
            {"action": "set_label", "card_name": "Cultivate", "label": "Have", "color": "#2ccce4"}
        ]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert _rows(server, "Cultivate")[0]["label"] == "Have,#2ccce4"
        dot = row.locator(".tagdot")
        assert dot.get_attribute("title") == "Tag: Have"
        assert dot.evaluate("e => getComputedStyle(e).backgroundColor") == "rgb(44, 204, 228)"
        assert _overflow(page) == 0
        _shot(page, f"deck-tagged-{width}-{scheme}")
        # the page after a reload draws the same tag from Archidekt's row
        page.reload(wait_until="networkidle")
        dot = page.locator(".deckview .row[data-card='Cultivate'] .tagdot")
        assert dot.get_attribute("title") == "Tag: Have"
        page.locator(".bulkbar button", has_text="Select cards").click()
        page.locator(".deckview .row[data-card='Cultivate']").first.click()
        page.locator(".bulkbar button", has_text="Colour tag").click()
        with page.expect_response(EDIT_URL):
            page.locator(".bulkbar button", has_text="Take tag off").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert not _rows(server, "Cultivate")[0].get("label")
        assert page.locator(".deckview .row[data-card='Cultivate'] .tagdot").count() == 0
        # Undo puts the tag back
        with page.expect_response(EDIT_URL) as r2:
            page.locator(".deck-toast button", has_text="Undo").click()
        assert json.loads(r2.value.request.post_data)["changes"][0]["label"] == "Have"
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert _rows(server, "Cultivate")[0]["label"] == "Have,#2ccce4"
        assert errors == []
        browser.close()


def test_bulk_edge_cases_at_320(server: Server) -> None:
    """320 px with a long category name: nothing scrolls sideways. A card on the maybeboard is
    left out of Set finish (the gateway refinishes the deck itself), and removing a card whose
    name has two rows in that zone offers no Undo (one could not put both rows back)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    long_cat = "Card advantage engines and other value <img src=x onerror=alert(1)>"
    for row in server.ark.decks[42]["cards"]:
        if row["card"]["oracleCard"]["name"] == "Cultivate":
            row["categories"] = [long_cat]
    server.ark.decks[42]["categories"].append({"name": long_cat, "isPremier": False, "includedInDeck": True})
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 320, color_scheme="dark")
        page.on("dialog", lambda d: errors.append("dialog: " + d.message))
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        page.locator(".bulkbar button", has_text="Select cards").click()
        side = page.locator(".deckview .row[data-zone='side'][data-card='Sol Ring']").first
        side.click()
        assert _overflow(page) == 0
        _shot(page, "deck-bulk-320-long-category")
        page.locator(".bulkbar select[aria-label^='Set the finish']").select_option("foil")
        assert "not on the maybeboard" in page.locator(".bulkbar .status").inner_text()
        assert page.locator(".deck-toast").count() == 0  # nothing was sent
        page.locator(".bulkbar button", has_text="Cancel").click()
        # two main rows of Sol Ring: the remove goes through, without an Undo
        page.locator(".bulkbar button", has_text="Select cards").click()
        page.locator(".deckview .row[data-zone='main'][data-card='Sol Ring']").first.click()
        page.evaluate(
            "() => { const r = document.querySelector(\".row[data-zone='main'][data-card='Sol Ring']\");"
            " const twin = r.cloneNode(true); twin.classList.remove('picked'); r.after(twin); }"
        )
        with page.expect_response(EDIT_URL):
            page.locator(".bulkbar button", has_text="Remove").click()
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert page.locator(".deck-toast button", has_text="Undo").count() == 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(320, "dark"), (1366, "light")])
def test_brewer_view_and_custom_mana_value(server: Server, width: int, scheme: str) -> None:
    """0.7.20: the Brewer view (picture, name and cost, type line, rules text) and the card
    menu's Mana value form, which saves Archidekt's custom mana value and clears it again."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42?view=brewer", wait_until="networkidle")
        assert page.locator(".deckview.brewer").count() == 1
        row = page.locator(".deckview.brewer .row.brew[data-card='Cultivate']").first
        assert "Sorcery" in row.locator(".ty").inner_text()
        assert _overflow(page) == 0
        _shot(page, f"deck-brewer-{width}-{scheme}")
        row.scroll_into_view_if_needed()
        row.click(button="right")
        page.locator(".ctxmenu [role=menuitem]", has_text="Mana value").click()
        assert page.locator(".ctxmenu .mvform button", has_text="Clear").count() == 0  # nothing to clear
        page.locator(".ctxmenu .mvform input").fill("25")
        page.locator(".ctxmenu .mvform button", has_text="Save").click()
        assert "0 to 20" in page.locator(".ctxmenu .mvform .status").inner_text()
        page.locator(".ctxmenu .mvform input").fill("2")
        assert page.locator(".ctxmenu .mvform .status").inner_text() == ""  # typing clears the error
        _shot(page, f"deck-mv-form-{width}-{scheme}")
        with page.expect_response(EDIT_URL) as r:
            page.locator(".ctxmenu .mvform button", has_text="Save").click()
        assert json.loads(r.value.request.post_data)["changes"] == [
            {"action": "set_mana_value", "card_name": "Cultivate", "mana_value": 2, "zone": "main"}
        ]
        page.locator(".deck-toast .msg", has_text="Saved").wait_for(timeout=8000)
        assert _rows(server, "Cultivate")[0]["customCmc"] == 2
        assert row.get_attribute("data-mv") == "2"
        # Undo takes the override off again
        with page.expect_response(EDIT_URL) as r2:
            page.locator(".deck-toast button", has_text="Undo").click()
        assert json.loads(r2.value.request.post_data)["changes"][0]["mana_value"] is None
        page.locator(".deck-toast .msg", has_text="Undone").wait_for(timeout=8000)
        assert _rows(server, "Cultivate")[0].get("customCmc") is None
        assert errors == []
        browser.close()
