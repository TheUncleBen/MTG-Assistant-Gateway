"""Select on My decks (0.7.46 audit QG-17, QOL-17): every deck tile of the member's own has a tick
box, Select ticks every deck on screen, and the bar offers what one tile's menu offers and makes
sense in bulk: Move to folder… and Visibility…, each going through the same writes a single deck
goes through (one deck at a time, the member's own press, read back). Delete stays the deck's own
box, name typed, one deck at a time: the bar never deletes."""

from __future__ import annotations

import re

from mtg_gateway import deck_bulk

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED
from .test_decks_and_proxy import linked_user
from .test_folder_move_delete import _browser, _setup

stack_options = stack_with(SHIPPED)
NAV = {"Sec-Fetch-Mode": "navigate", "Sec-Fetch-Site": "same-origin"}


async def test_my_decks_has_tick_boxes_the_bar_and_its_boxes(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    b, _csrf = await _browser(stack)
    try:
        for view in ("grid", "list"):
            page = (await b.http.get(f"/decks?view={view}&q=deck", headers=NAV)).text  # every folder
            for did, name in ((42, stack.ark.decks[42]["name"]), (201, "Precon deck"), (202, "Loose deck")):
                tick = (
                    f"<input type='checkbox' name='deck_id' value='{did}' aria-label='Select {name}' "
                    "form='decks-bulk'>"
                )
                assert tick in page, (view, name)
            assert "<form method='post' action='/decks/bulk' id='decks-bulk' class='decks-bulkform'>" in page
            assert "<div class='ui-bar' role='group' aria-label='Selected decks' hidden>" in page
            assert "<button type='button' class='btn decks-select' aria-pressed='false' hidden>" in page
            assert "id='decks-bulk-move'" in page and "id='decks-bulk-vis'" in page
            assert f"<option value='{ids['archive']}'>Archive</option>" in page
            assert "value='delete'" not in page.split("id='decks-bulk'")[1].split("</form>")[0]
        # another member's deck tile (Home's listing of public decks) carries no tick box
        other = (await b.http.get("/decks/43", headers=NAV)).text
        assert "name='deck_id'" not in other
    finally:
        await b.aclose()


async def test_move_and_visibility_go_deck_by_deck_and_say_how_many_went(stack: Stack) -> None:
    await linked_user(stack)
    ids = _setup(stack)
    ark = stack.ark
    b, csrf = await _browser(stack)
    try:
        r = await b.http.post(
            "/decks/bulk",
            data={"csrf": csrf, "do": "move", "folder_id": str(ids["archive"]), "deck_id": ["42", "202"]},
        )
        assert r.status_code == 303 and r.headers["location"].startswith("/decks?ok=bulk&t="), r.text
        assert ark.deck_folder[42] == ids["archive"] and ark.deck_folder[202] == ids["archive"]
        assert ark.deck_folder[201] == ids["precons"]
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "2 decks moved to “Archive”." in page
        # the top level
        r = await b.http.post(
            "/decks/bulk", data={"csrf": csrf, "do": "move", "folder_id": "", "deck_id": ["42"]}
        )
        assert r.status_code == 303, r.text
        assert "1 deck moved to the top level." in (await b.http.get(r.headers["location"], headers=NAV)).text
        # visibility: the deck's details, applied as the member's own press
        r = await b.http.post(
            "/decks/bulk",
            data={"csrf": csrf, "do": "visibility", "visibility": "private", "deck_id": ["42", "201"]},
        )
        assert r.status_code == 303 and r.headers["location"].startswith("/decks?ok=bulk&t="), r.text
        assert ark.decks[42]["private"] is True and ark.decks[201]["private"] is True
        assert ark.decks[202].get("private") is not True
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "Visibility set on 2 decks." in page
        r = await b.http.post(
            "/decks/bulk",
            data={"csrf": csrf, "do": "visibility", "visibility": "unlisted", "deck_id": ["42"]},
        )
        assert r.status_code == 303, r.text
        assert ark.decks[42]["private"] is False and ark.decks[42]["unlisted"] is True
        # already so: counts as done, nothing sent
        sent = len(ark.updates)
        r = await b.http.post(
            "/decks/bulk",
            data={"csrf": csrf, "do": "visibility", "visibility": "unlisted", "deck_id": ["42"]},
        )
        assert r.status_code == 303 and "ok=bulk" in r.headers["location"]
        assert len(ark.updates) == sent
        assert "Visibility set on 1 deck." in (await b.http.get(r.headers["location"], headers=NAV)).text
        # a deck that is not the member's: named, the rest still go
        r = await b.http.post(
            "/decks/bulk",
            data={"csrf": csrf, "do": "move", "folder_id": str(ids["brews"]), "deck_id": ["43", "202"]},
        )
        assert r.status_code == 303, r.text
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert re.search(r"1 deck moved to “Brews”\. 1 could not be changed: 43 \(", page), page
        assert ark.deck_folder[202] == ids["brews"]
        # nothing ticked, no such choice, or a delete: refused plainly
        r = await b.http.post("/decks/bulk", data={"csrf": csrf, "do": "move", "folder_id": ""})
        assert r.status_code == 303 and "err=bulk" in r.headers["location"]
        assert "Tick a deck first." in (await b.http.get(r.headers["location"], headers=NAV)).text
        r = await b.http.post("/decks/bulk", data={"csrf": csrf, "do": "delete", "deck_id": ["42"]})
        assert r.status_code == 303 and "err=" in r.headers["location"] and 42 in ark.decks
        too_many = [str(900000 + i) for i in range(deck_bulk.MAX_BULK_DECKS + 1)]
        r = await b.http.post(
            "/decks/bulk", data={"csrf": csrf, "do": "move", "folder_id": "", "deck_id": too_many}
        )
        assert r.status_code == 303 and "err=bulk" in r.headers["location"]
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert f"at most {deck_bulk.MAX_BULK_DECKS} decks at a time" in page
        # the form needs the page's token
        r = await b.http.post(
            "/decks/bulk", data={"csrf": "nope", "do": "move", "folder_id": "", "deck_id": ["42"]}
        )
        assert r.status_code == 303 and "err=expired" in r.headers["location"]
    finally:
        await b.aclose()
