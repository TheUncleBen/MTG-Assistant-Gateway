"""The deck page, the deck editor and My decks after the 0.7.46 audit: side stacks after every
in-deck stack, a plain refusal of a name that is no card, My decks tiles without "No deck tags"
and the owner's own picture, New package the orange button on the Packages tab, the banner of a
deck still being made marked for its menu, and Deck settings' Save that stays on the page."""

from __future__ import annotations

import re
from pathlib import Path

from mtg_gateway import deck_stats, views
from mtg_gateway.archidekt import parse_deck
from mtg_gateway.archidekt_csv import parse_export, to_deck_json
from mtg_gateway.deckpage import banner_html, deck_card_html

from .conftest import Stack
from .test_browse_collection import api, linked
from .test_companion import NAV, linked_browser

FIXTURE = Path(__file__).parent / "fixtures" / "sample_deck.csv"


def _deck_with_side_rows():
    """Deck 42's cards: Commander, a deck category (Ramp), cards with no category (automatic
    stacks such as Sorcery), and two side categories listed early in Archidekt's category list."""
    raw = to_deck_json(parse_export(FIXTURE.read_text()), deck_id=42, name="Sample", owner="alice")
    raw["categories"] = [
        {"name": "Commander", "isPremier": True, "includedInDeck": True},
        {"name": "Maybeboard", "isPremier": False, "includedInDeck": False},
        {"name": "Sideboard", "isPremier": False, "includedInDeck": False},
        {"name": "Ramp", "isPremier": False, "includedInDeck": True},
    ]
    rows = raw["cards"]
    for i, row in enumerate(rows):
        name = row["card"]["oracleCard"]["name"]
        if name.startswith("Aesi"):
            row["categories"] = ["Commander"]
        elif name == "Cultivate":
            row["categories"] = ["Ramp"]
        elif i % 7 == 1:
            row["categories"] = ["Maybeboard"]
        elif i % 11 == 2:
            row["categories"] = ["Sideboard"]
        else:
            row["categories"] = []
    return parse_deck(raw)


def test_side_stacks_come_after_every_stack_in_the_deck() -> None:
    """0.7.46 audit D-14: Maybeboard and Sideboard sat between Commander and the automatic
    stacks, as the deck's own category order ranked them before anything else."""
    deck = _deck_with_side_rows()
    names = [name for name, _cards in views.cards_by_category(deck)]
    assert names[0] == "Commander" and "Sorcery" in names
    side = {i for i, n in enumerate(names) if n in deck.excluded_categories()}
    main = {i for i, n in enumerate(names) if n not in deck.excluded_categories()}
    assert side and max(main) < min(side), names
    assert names[-2:] == ["Maybeboard", "Sideboard"]


async def test_a_name_that_is_no_card_is_refused_in_plain_words(stack: Stack) -> None:
    """0.7.46 audit D-09: the refusal said "No Magic card is called 'Rampant'. Check the spelling;
    nothing was proposed." (straight quotes, the word proposed). Quick add sends the name to the
    deck page's own save, which answers in the page's words."""
    stack.h.app.state.gateway.scan.names.set_names(["Rampant Growth", "Cultivate"])
    b = await linked(stack)
    try:
        r = await api(
            b, "POST", "/api/v1/decks/42/edit", {"changes": [{"action": "add", "card_name": "Rampant"}]}
        )
        assert r.status_code == 400, r.text
        assert r.json()["error"] == "unknown_cards"
        assert r.json()["message"] == "No card is called “Rampant”."
    finally:
        await b.aclose()


def test_my_decks_tiles_say_nothing_about_missing_tags_or_the_owner() -> None:
    """0.7.46 audit D-24: every tile on My decks printed "No deck tags" and the member's own
    picture. A tile with tags still shows them; a listing of other people's decks keeps the picture."""
    d = {"id": "42", "name": "Sample", "owner": "alice", "format_name": "commander"}
    mine = deck_card_html(d, cover=None, selected=False, own=True)
    assert "No deck tags" not in mine and "class='notags'" not in mine
    assert "class='avatar" not in mine and "class='tags'" not in mine
    tagged = deck_card_html({**d, "tags": ["budget", "fun"]}, cover=None, selected=False, own=True)
    assert "<span class='tags'><span class='pill'>budget</span><span class='pill'>fun</span></span>" in tagged
    theirs = deck_card_html({**d, "owner": "bob"}, cover=None, selected=False, show_owner=True)
    assert "class='avatar sm'" in theirs and "by bob" in theirs and "No deck tags" not in theirs


async def test_new_package_is_the_orange_button_on_the_packages_tab(stack: Stack) -> None:
    """0.7.46 audit D-25: New deck stayed the one orange button on the Packages tab."""
    b = await linked_browser(stack)
    try:
        decks = (await b.http.get("/decks", headers=NAV)).text
        assert "<a class='btn btn-primary' href='/decks/new' " in decks
        assert "<a class='btn' href='/decks/new-package' " in decks
        packages = (await b.http.get("/decks?show=packages", headers=NAV)).text
        assert "<a class='btn' href='/decks/new' " in packages
        assert "<a class='btn btn-primary' href='/decks/new-package' " in packages
    finally:
        await b.aclose()


def test_the_banner_of_a_deck_still_being_made_is_marked_for_its_menu() -> None:
    """The deck menu (static/deck.js) leaves out Rename, Move to folder, Add a package, Deck
    settings and Delete deck while the deck's cards are still going in."""
    deck = _deck_with_side_rows()
    stats = deck_stats.compute(deck)
    made = banner_html(deck, stats, own=True, csrf="abc", writes_enabled=True, making=False)
    making = banner_html(deck, stats, own=True, csrf="abc", writes_enabled=True, making=True)
    assert "data-making='1'" in making and "data-making" not in made


async def test_save_changes_stays_on_deck_settings_with_a_saved_note(stack: Stack) -> None:
    """0.7.46 audit D-27: after Save changes the member was taken to the deck page."""
    ark = stack.ark
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/decks/42/settings", headers=NAV)).text
        csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page).group(1)
        was = dict(re.findall(r"<input type='hidden' name='(was_[a-z_]+)' value='([^']*)'>", page))
        form = {**was, "csrf": csrf, "name": "Renamed deck", "description": "", "game": "any"}
        r = await b.http.post("/decks/42/settings", data=form, headers=NAV)
        assert r.status_code == 303, r.text
        assert r.headers["location"] == "/decks/42/settings?ok=saved#ds-main"
        assert ark.decks[42]["name"] == "Renamed deck"
        again = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "Saved to Archidekt. A snapshot from just before is under History." in again
        assert "value='Renamed deck'" in again
    finally:
        await b.aclose()
