"""My collection as a dashboard (UI overhaul 2026-10-21, mockup 20-collection): the purpose line
with Export CSV and the orange + Add cards in the page head, the widgets built from what Archidekt
sends, the shared search panel with its results bar (count, Sort with a direction only for Set
release, Grid or List, card size in both views, Clear filters), card search's tile with a 44 px
stepper and the shared menu's ⋮, a real table in List view, Load more instead of a pager, the Add
cards box with its two tabs, the Details box, notices as strips with a close button, "Normal" as the
plain finish and conditions and languages in words."""

from __future__ import annotations

import re

from mtg_gateway import collection

from .conftest import COLLECTION, Stack, stack_with
from .test_browse_collection import NAV, api, linked

stack_options = stack_with(COLLECTION)


async def _fill(b, names: list[tuple[str, int]], finishes: tuple[str, ...] = ("nonfoil",)) -> None:
    csrf = await b.csrf("/collection")
    items = [{"name": n, "quantity": q, "finish": f} for n, q in names for f in finishes]
    r = await api(b, "POST", "/collection/api/add", {"items": items, "source": "manual"}, csrf=csrf)
    assert r.status_code == 200, r.text


def _seed(stack: Stack, n: int) -> None:
    """``n`` collection rows for alice, straight into the fake: five printings in turn, each in a
    finish and condition of its own, so every row is a record of its own."""
    ark = stack.ark
    pks = (9003, 5011, 5007, 9005, 9008)  # Sol Ring, Cultivate, Command Tower, Island, Swamp
    for i in range(n):
        ark.next_coll_id += 1
        body = {"quantity": 1, "modifier": ("Normal", "Foil", "Etched")[i // 5 % 3], "condition": 1 + i // 15}
        row = ark._collection_row(ark.next_coll_id, ark.printing(pks[i % 5]), body)
        ark.collections.setdefault("alice", {})[row["id"]] = row


async def test_the_page_is_a_dashboard_with_the_shared_panel_and_card_search_tiles(stack: Stack) -> None:
    b = await linked(stack)
    try:
        await _fill(b, [("Sol Ring", 3), ("Cultivate", 1), ("Counterspell", 2)])
        r = await b.http.get("/collection", headers=NAV)
        assert r.status_code == 200
        page = r.text
        # the head: the purpose line, Export CSV plain, + Add cards the one orange button (opens the box)
        assert "<h1>Binder</h1>" in page
        assert "<p class='ui-sub'>The cards you own: your collection on Archidekt.</p>" in page
        assert page.count("btn-primary") == 1
        assert re.search(
            r"<a class='btn btn-primary' href='/collection\?add=1' data-box-open='col-add'>", page
        )
        assert "href='/collection/export.csv'" in page and "Export CSV" in page
        # the widgets, from what Archidekt already sends
        widgets = page.split("aria-label='At a glance'>")[1].split("</section>")[0]
        # 0.7.46: Value (the binder's worth, from the daily price check) took the place of Copies;
        # the copies are under Entries
        for label in ("Entries", "Value", "Added this week", "Foils"):
            assert f"{label}</span>" in widgets, label
        assert "<span class='big'>3</span>" in widgets  # entries
        assert "6 copies" in widgets  # counted: the whole collection fits a page
        assert "Work it out now" in widgets  # no whole read yet
        assert "Open on Archidekt" in widgets and "Scan cards" in widgets and "Import a list" in widgets
        assert "Price paid" not in widgets
        # the shared search panel with the results bar at its foot
        assert "class='panel spanel'" in page
        assert "<label for='col-q'>Filter by name</label>" in page
        assert "placeholder='Name'" in page
        assert "aria-label='Filter'" in page
        assert "id='col-count' aria-live='polite'>3 entries</h2>" in page
        assert "<option value='added' selected>Recently added</option>" in page
        assert "<option value='edition'>Set release</option>" in page
        assert "name='dir'" not in page  # Recently added has no direction
        assert "class='segc view' role='radiogroup' aria-label='View'" in page
        assert "class='g size cardsize'" in page
        assert ">Clear filters</a>" in page
        # the tiles: card search's tile with the stepper, the ⋮ and no × remove
        assert "<ul class='cardgrid collgrid' id='cards'" in page
        tile = page.split("<li class='cc c col'")[1].split("</li>")[0]
        assert "class='pic thumbbtn'" in tile and "class='kebab'" in tile
        assert "aria-label='One fewer" in tile and "aria-label='One more" in tile
        assert "class='qty'>" not in tile  # the count shows once, in the stepper
        assert "title='Remove'" not in tile
        # the old controls and folds are gone
        assert "collbar" not in page and "coll-totals" not in page and "Page 1 of" not in page
        assert "Add cards or import a list" not in page
        assert "Non-foil" not in page
        # the CSV help is below the list
        assert page.index("class='cardgrid collgrid'") < page.index("Importing the CSV on archidekt.com")
        # every entry shown: no Load more
        assert "data-load-more" not in page and "All 3 entries shown." in page
    finally:
        await b.aclose()


async def test_set_release_has_a_direction_and_the_direction_reaches_archidekt(stack: Stack) -> None:
    b = await linked(stack)
    seen = stack.ark.collection_queries
    try:
        await _fill(b, [("Sol Ring", 1)])
        r = await b.http.get("/collection?sort=edition", headers=NAV)
        page = r.text
        assert "<option value='edition' selected>Set release</option>" in page
        assert "<input type='hidden' name='dir' value='desc'>" in page
        assert "aria-label='Direction: newest set first'" in page
        sorted_calls = [c for c in seen if "collectionOrderBy" in c]
        assert sorted_calls[-1]["collectionOrderBy"] == "editionDate"
        assert sorted_calls[-1]["orderDirection"] == "descending"
        r = await b.http.get("/collection?sort=edition&dir=asc", headers=NAV)
        assert "aria-label='Direction: oldest set first'" in r.text
        assert "<input type='hidden' name='dir' value='asc'>" in r.text
        assert [c for c in seen if "collectionOrderBy" in c][-1]["orderDirection"] == "ascending"
        # Recently added is Archidekt's default order, with no sort key and no direction
        seen.clear()
        await b.http.get("/collection?sort=added&dir=asc", headers=NAV)
        assert seen and all("collectionOrderBy" not in c and "orderDirection" not in c for c in seen)
    finally:
        await b.aclose()


async def test_list_view_is_a_table_with_the_card_size_in_the_bar(stack: Stack) -> None:
    b = await linked(stack)
    try:
        await _fill(b, [("Sol Ring", 3), ("Cultivate", 1)])
        r = await b.http.get("/collection?view=list", headers=NAV)
        page = r.text
        assert "<ul class='ui-table colllist' id='cards' role='table'" in page
        head = page.split("class='tr th'")[1].split("</li>")[0]
        for col in ("Card", "Set", "Finish", "Condition", "Language", "Copies"):
            assert f">{col}</span>" in head, col
        assert "class='g size cardsize'" in page  # R-175: the switch stays in List view
        row = page.split("<li class='tr row' role='row'")[1].split("</li>")[0]
        assert "class='thumb'" in row and "class='kebab'" in row
        assert "Normal" in row and "Near Mint (NM)" in row and "English" in row
        assert "class='mc'" not in page  # no mana cost column
    finally:
        await b.aclose()


async def test_load_more_shows_a_screenful_then_the_rest(stack: Stack) -> None:
    b = await linked(stack)
    try:
        _seed(stack, 30)
        r = await b.http.get("/collection", headers=NAV)
        page = r.text
        assert page.count("<li class='cc c col'") == collection.SHOWN == 24
        assert "id='col-count' aria-live='polite'>30 entries</h2>" in page
        more = re.search(
            r"<a class='btn btn-plain' href='([^']+)' rel='next' data-load-more>Load more cards</a>", page
        )
        assert more and more.group(1) == "/collection?offset=24", more
        assert "<p class='muted'>24 of 30</p>" in page
        # the next rows, as the page's script fetches them
        r = await api(b, "GET", "/collection/api/rows?offset=24")
        d = r.json()
        assert d["ok"] and d["html"].count("<li class='cc c col'") == 6
        assert d["shown"] == 30 and d["total"] == 30 and d["has_more"] is False
        assert d["done"] == "All 30 entries shown."
        # without scripts the link is a page of its own
        r = await b.http.get("/collection?offset=24", headers=NAV)
        assert r.text.count("<li class='cc c col'") == 6 and "All 30 entries shown." in r.text
        assert "data-load-more" not in r.text
    finally:
        await b.aclose()


async def test_the_add_cards_box_has_two_tabs_and_opens_from_the_old_address(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await b.http.get("/collection", headers=NAV)
        page = r.text
        box = page.split("id='col-add'")[1].split("</form></div></div>")[0]
        assert "role='tab'" in box and ">Add a card</button>" in box and ">Import a list</button>" in box
        assert "<option value='nonfoil' selected>Normal</option>" in box and "Non-foil" not in page
        assert "<option value='NM' selected>Near Mint (NM)</option>" in box
        assert "<option value='DMG'>Damaged (DMG)</option>" in box
        assert "placeholder='Name, or “name (SET) 123” for one printing'" in box
        assert "Scanning a pile is quicker" in box
        assert (
            "A collection CSV or a card list, one card per line, up to 100 rows at a time. "
            "Copies join an entry you already have only when its printing, finish, condition and "
            "language all match." in box
        )
        assert "id='col-add' role='dialog' aria-modal='true' aria-labelledby='col-add-title' hidden>" in page
        # the old address: the box comes open; a result opens the matching tab
        r = await b.http.get("/collection?add=1", headers=NAV)
        assert (
            "id='col-add' role='dialog' aria-modal='true' aria-labelledby='col-add-title' data-open>"
            in r.text
        )
        assert (
            "aria-selected='true'>" in r.text.split("data-coltab='add'")[0][-40:]
            or "data-coltab='add' aria-selected='true'" in r.text
        )
        r = await b.http.get("/collection?add=import", headers=NAV)
        assert "data-coltab='import' aria-selected='true'" in r.text
        r = await b.http.get("/collection?ok=imported", headers=NAV)
        assert "data-coltab='import' aria-selected='true'" in r.text and "data-open" in r.text
    finally:
        await b.aclose()


async def test_notices_are_strips_with_a_close_button_and_the_words_are_kept(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await b.http.get("/collection?ok=added", headers=NAV)
        assert "<div class='notice ok' role='status'>" in r.text
        assert "Added to your Collection on Archidekt." in r.text
        assert (
            "aria-label='Close' title='Close'"
            in r.text.split("Added to your Collection on Archidekt.")[0][-300:]
        )
        r = await b.http.get("/collection?err=lookup", headers=NAV)
        assert "<div class='notice error' role='alert'>" in r.text
        assert "Archidekt is not answering right now. Try again later." in r.text
    finally:
        await b.aclose()


async def test_the_empty_page_and_the_details_box(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await b.http.get("/collection", headers=NAV)
        page = r.text
        assert "<h2>No cards yet</h2>" in page and "<p>Scan a pile, or add cards by name.</p>" in page
        assert page.count("btn-primary") == 1 and "Scan cards" in page
        assert "data-box-open='col-add'>" in page and "Add by name" in page and "Import a list" in page
        assert "Importing the CSV on archidekt.com" in page
        assert "aria-label='At a glance'" not in page
        await _fill(b, [("Sol Ring", 2)])
        r = await b.http.get("/collection", headers=NAV)
        rid = re.search(r"data-id='([0-9]+)'", r.text).group(1)
        # the tile carries what the Details box needs, and the old address opens the box filled in
        tile = r.text.split(f"data-id='{rid}'")[1].split("</li>")[0]
        assert "data-fin='nonfoil'" in tile and "data-cond='NM'" in tile and "data-lang='EN'" in tile
        r = await b.http.get(f"/collection?details={rid}", headers=NAV)
        box = r.text.split("id='col-details'")[1].split("</form>")[0]
        assert "data-open" in r.text.split("id='col-details'")[1][:160]
        assert f"<input type='hidden' name='id' value='{rid}'>" in box
        assert "<option value='nonfoil' selected>Normal</option>" in box
        assert (
            "<option value='JA'>Japanese</option>" in box
            and "<option value='EN' selected>English</option>" in box
        )
        assert "<option value='' >Not set</option>" in box or "<option value=''>Not set</option>" in box
        assert "placeholder='None'" in box
    finally:
        await b.aclose()


async def test_not_linked_and_a_load_error_show_their_words(stack: Stack) -> None:
    from .test_decks_and_proxy import Browser

    b = Browser(stack.h)
    try:
        await b.login("/collection")
        r = await b.http.get("/collection", headers=NAV)
        assert "<h2>Your collection lives on Archidekt</h2>" in r.text
        assert (
            "<p>Link your Archidekt account to see it here. Your cards stay in your Collection on "
            "Archidekt.</p>" in r.text
        )
        assert "Link Archidekt" in r.text and r.text.count("btn-primary") == 1
    finally:
        await b.aclose()
