"""Package B2 of the UI overhaul (BUILD-PLAN, 2026-10-21): the deck page and its boxes, the New
deck box, Deck settings on the shared form rows, Compare, Goldfish and the editor's card size."""

from __future__ import annotations

import re

from .conftest import Stack
from .test_companion import NAV, linked_browser

DECK = "/decks/42"


async def test_the_deck_page_holds_its_boxes_and_one_orange_button(stack: Stack) -> None:
    """Edit deck is the one orange button; Clone opens the Clone box (a plain clone without
    scripts); the old More items sit in the no-script drop-down the menu replaces; the Export,
    Compare, Clone and Delete boxes are on the page, hidden, with real links as openers."""
    b = await linked_browser(stack)
    try:
        page = (await b.http.get(DECK, headers=NAV)).text
        assert page.count("btn-primary") == 1 and "Edit deck</a>" in page
        assert "class='btn' data-box-open='clone'" in page and "form='clonef'" in page
        assert "data-deck-name='Sample Commander Deck'" in page and "data-own='1'" in page
        assert "details class='dd deckmore'" in page
        for box in ("export", "compare", "clone", "delete"):
            assert f"id='{box}' role='dialog'" in page, box
        assert "href='/decks/42/export' data-box-open='export'" in page
        assert "href='/decks/42/delete' data-box-open='delete'" in page
        assert "Delete “Sample Commander Deck”?" in page and "data-typed='Sample Commander Deck'" in page
        assert "value='Copy of - Sample Commander Deck'" in page
        assert "Top level (no folder)" in page
        assert "Clone deck…" not in page  # the banner button keeps its old words
        assert "Deck stats" in page and "Open on Archidekt" in page
    finally:
        await b.aclose()


async def test_the_old_export_and_delete_addresses_open_the_deck_page_with_the_box(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        export = await b.http.get(f"{DECK}/export", headers=NAV)
        assert export.status_code == 200 and "<title>Export: Sample Commander Deck" in export.text
        assert (
            "id='export' role='dialog' aria-modal='true' aria-labelledby='export-title' data-open>"
            in export.text
        )
        assert "aria-label='Archidekt import text'" in export.text and ".archidekt.txt" in export.text
        assert "export.pdf" in export.text and "Downloads are made in this browser" in export.text
        delete = await b.http.get(f"{DECK}/delete", headers=NAV)
        assert delete.status_code == 200 and "<title>Delete: Sample Commander Deck" in delete.text
        assert (
            "id='delete' role='dialog' aria-modal='true' aria-labelledby='delete-title' data-open>"
            in delete.text
        )
        assert "Delete this deck</button>" in delete.text
    finally:
        await b.aclose()


async def test_a_wrong_name_keeps_the_delete_box_open_on_the_deck_page(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        csrf = await b.csrf()
        r = await b.http.post(f"{DECK}/delete", data={"csrf": csrf, "name": "Not it"})
        assert r.status_code == 400 and "aria-labelledby='delete-title' data-open>" in r.text
        assert "<div class='notice error' role='alert'>" in r.text
        assert 42 in stack.ark.decks
    finally:
        await b.aclose()


async def test_clone_takes_a_name_and_a_folder(stack: Stack) -> None:
    ark = stack.ark
    ark.folders["alice"] = [{"id": 501, "name": "Brews", "private": False}]
    b = await linked_browser(stack)
    try:
        page = (await b.http.get(DECK, headers=NAV)).text
        assert "<option value='501'>" in page and "Brews</option>" in page
        csrf = await b.csrf()
        r = await b.http.post(f"{DECK}/clone", data={"csrf": csrf, "name": "My copy", "folder_id": "501"})
        assert r.status_code == 303 and r.headers["location"].endswith("?ok=created"), r.text
        new_id = int(r.headers["location"].split("/")[2].split("?")[0])
        assert ark.decks[new_id]["name"] == "My copy" and ark.deck_folder.get(new_id) == 501
        # a folder that is not the member's: the box comes back with the refusal
        bad = await b.http.post(f"{DECK}/clone", data={"csrf": csrf, "name": "Nope", "folder_id": "999"})
        assert bad.status_code == 400 and "aria-labelledby='clone-title' data-open>" in bad.text
        assert "value='Nope'" in bad.text
    finally:
        await b.aclose()


async def test_a_deck_problem_is_one_sentence_and_back_to_my_decks(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        r = await b.http.get("/decks/999999", headers=NAV)
        assert r.status_code == 404 and "<title>This deck could not be found." in r.text
        assert "Back to my decks</a>" in r.text and "class='btn btn-primary' href='/decks'" in r.text
        r = await b.http.get("/decks/43/goldfish", headers=NAV)  # someone else's private deck
        assert r.status_code in (403, 404)
        assert "This deck is private, so it can" in r.text or "This deck could not be found." in r.text
    finally:
        await b.aclose()


async def test_new_deck_is_a_box_over_my_decks_and_home(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        decks = (await b.http.get("/decks", headers=NAV)).text
        assert "href='/decks/new' data-box-open='newdeck'" in decks
        assert "id='newdeck' role='dialog' aria-modal='true' aria-labelledby='newdeck-title' hidden>" in decks
        assert "newdeck.js" in decks and "filepick.js" in decks
        opened = (await b.http.get("/decks/new", headers=NAV)).text
        assert "<title>New deck" in opened and "aria-labelledby='newdeck-title' data-open>" in opened
        assert "role='tab' data-src='paste'" in opened and "Gateway .json export" in opened
        assert "data-busy-label='Creating…'" in opened and "Create deck</button>" in opened
        assert "Super awesome deck name 2000" not in opened
        home = (await b.http.get("/", headers=NAV)).text
        assert "href='/decks/new' data-box-open='newdeck'" in home and "id='newdeck'" in home
        # a refusal comes back inside the box, the typed values kept
        csrf = await b.csrf()
        r = await b.http.post("/decks/new", data={"csrf": csrf, "name": "", "format": "commander"})
        assert r.status_code == 400 and "aria-labelledby='newdeck-title' data-open>" in r.text
        assert "<div class='notice error' role='alert'>" in r.text
        expired = await b.http.post("/decks/new", data={"csrf": "nope", "name": "X", "format": "commander"})
        assert expired.status_code == 403 and "This form expired." in expired.text
    finally:
        await b.aclose()


async def test_deck_settings_use_the_shared_form_rows(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        page = (await b.http.get(f"{DECK}/settings", headers=NAV)).text
        assert "<title>Deck settings" in page and "<h1>Deck settings</h1>" in page
        assert "<p class='ui-sub'>“Sample Commander Deck”</p>" in page
        assert "class='back' href='/decks/42'>← Sample Commander Deck</a>" in page
        assert page.count("btn-primary") == 1 and "Save changes</button>" in page
        assert "Commander only. Changing the format clears it." in page
        assert "<label for='private'>Private</label>" in page and "The deck is only visible to you." in page
        assert "<label for='unlisted'>Unlisted</label>" in page
        assert "Manage folders on <a href='/decks'>My decks</a>." in page
        # categories read-only, in the new look, with the honest line
        assert "id='categories'" in page and "class='ds-flag premier'>Premier</span>" in page
        assert "Rename a category or change its flags on Archidekt." in page
        assert "not available through the gateway yet" not in page
        # delete: red outline opening the typed-name box the page holds
        assert "class='btn btn-outline-danger' href='/decks/42/delete' data-box-open='delete'" in page
        assert "when backups are on." in page and "id='delete' role='dialog'" in page
        assert "id='ds-delete'" in page
        assert "class='panel settings'" not in page
    finally:
        await b.aclose()


async def test_an_unnamed_deck_reads_unnamed_deck_everywhere(stack: Stack) -> None:
    stack.ark.decks[42]["name"] = ""
    b = await linked_browser(stack)
    try:
        page = (await b.http.get(DECK, headers=NAV)).text
        assert "<title>Unnamed deck" in page and "data-deck-name='Unnamed deck'" in page
        assert "Delete “Unnamed deck”?" in page and "value='Copy of - Unnamed deck'" in page
        settings = (await b.http.get(f"{DECK}/settings", headers=NAV)).text
        assert "<p class='ui-sub'>“Unnamed deck”</p>" in settings
        assert "<title>Goldfish: Unnamed deck" in (await b.http.get(f"{DECK}/goldfish", headers=NAV)).text
    finally:
        await b.aclose()


async def test_goldfish_intro_and_forge_solo_line(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        page = (await b.http.get(f"{DECK}/goldfish", headers=NAV)).text
        assert "Commander: first mulligan free · on the play · Nothing is saved or sent to Archidekt" in page
        assert "class='gf-size'" in page and "data-card-size-set='m'" in page
        assert "Keep this hand, or take a mulligan" not in page
        # no newest_forge_solo on the report service: the plain "No solo test yet." line
        assert "No solo test yet." in page and "Run full test" in page
        reports = stack.h.app.state.gateway.reports

        def newest(sub: str, deck_id: str) -> dict:
            return {
                "report_id": 7,
                "finished_at": 1760000000,
                "won_rate": 0.42,
                "games": 300,
                "median_won_turn": 9,
            }

        reports.newest_forge_solo = newest  # type: ignore[attr-defined]
        try:
            page = (await b.http.get(f"{DECK}/goldfish", headers=NAV)).text
        finally:
            del reports.newest_forge_solo
        assert "won 42% of 300 games, median win turn 9." in page
        assert "href='/history/reports/7'" in page and "Open the report" in page
        assert "No solo test yet." not in page
    finally:
        await b.aclose()


async def test_compare_page_has_a_heading_one_orange_button_and_no_size_control(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        page = (await b.http.get(f"{DECK}/compare?with=Sample+Commander+Deck", headers=NAV)).text
        assert "<h1>Compare decks</h1>" in page
        assert "What “Sample Commander Deck” took out of another deck, put in and changed." in page
        assert len(re.findall(r"class='(?:btn-primary|primary)'", page)) == 1
        assert "cardsize" not in page and "Statistics: this deck minus “Sample Commander Deck”</h2>" in page
        assert "Taken out of “Sample Commander Deck” <span class='n'>0</span>" in page
        assert "Mystic Forge" not in page and "card_size" not in page
    finally:
        await b.aclose()


async def test_the_editor_toolbar_has_the_card_size_and_a_plain_add(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        page = (await b.http.get(f"{DECK}/edit", headers=NAV)).text
        assert "class='field cardsize'" in page and "data-card-size-set='l'" in page
        assert "btn-primary'>" not in page.split("class='editbar'", 1)[1].split("</form>", 1)[0]
    finally:
        await b.aclose()
