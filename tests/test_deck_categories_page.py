"""The Categories panel of Deck settings is editable (F7, Ben 21 Oct: option a): rename in place,
three switches per category (Premier, Not in deck, Not in price), a plain Save categories and Undo
changes, a confirm step for anything touching Premier, the Commander or a commander's category,
and the backend's own words (decks.category_line, decks.CATEGORIES_SAVED) everywhere."""

from __future__ import annotations

import re

from mtg_gateway import modes
from mtg_gateway.decks import CATEGORIES_SAVED, OPEN_TABS_NOTE
from mtg_gateway.theme import esc

from .conftest import Stack
from .test_companion import NAV, linked_browser
from .test_deck_categories import _give_categories

SETTINGS = "/decks/42/settings"
POST = "/decks/42/categories"


def _ids(page: str) -> dict[str, int]:
    """Each editable category's record id, from its row."""
    return {
        m.group(2): int(m.group(1)) for m in re.finditer(r"<li data-cat='(\d+)' data-name='([^']+)'", page)
    }


async def _csrf(b) -> tuple[str, str]:  # noqa: ANN001
    page = (await b.http.get(SETTINGS, headers=NAV)).text
    return page, re.search(r"name='csrf' value='([0-9a-f]+)'", page).group(1)


def _form(page: str, ids: dict[str, int], **changes: str | None) -> dict[str, str]:
    """The panel's fields as the browser sends them: every name, each flag that is on, then
    ``changes`` on top (``None`` takes a flag off)."""
    data: dict[str, str] = {}
    for name, cid in ids.items():
        data[f"name_{cid}"] = name
        for flag in ("premier", "not_in_deck", "not_in_price"):
            if f"name='{flag}_{cid}' value='1' checked" in page:
                data[f"{flag}_{cid}"] = "1"
    for k, v in changes.items():
        if v is None:
            data.pop(k, None)
        else:
            data[k] = v
    return data


async def test_the_panel_is_editable_with_the_approved_controls(stack: Stack) -> None:
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, _ = await _csrf(b)
        ids = _ids(page)
        assert set(ids) >= {"Commander", "Ramp", "Maybeboard"}, ids
        # one form for the panel, the member's own press: rename in place, three switches a row
        assert f"<form method='post' action='{POST}#categories' id='catform'" in page
        for name, cid in ids.items():
            assert (
                f"<input id='cat-{cid}-name' type='text' name='name_{cid}' value='{name}' maxlength='60'"
                in page
            )
            for flag, label in (
                ("premier", "Premier"),
                ("not_in_deck", "Not in deck"),
                ("not_in_price", "Not in price"),
            ):
                assert re.search(
                    rf"<input type='checkbox' name='{flag}_{cid}' value='1'( checked)?>", page
                ), flag
                assert f"aria-label='Flags of {name}'" in page and label in page
        assert f"name='premier_{ids['Commander']}' value='1' checked" in page
        assert f"name='not_in_deck_{ids['Maybeboard']}' value='1' checked" in page
        assert f"name='not_in_price_{ids['Maybeboard']}' value='1' checked" in page
        assert f"name='premier_{ids['Ramp']}' value='1' checked" not in page
        # the row that is or holds the commander says so for the page's confirm step
        assert f"<li data-cat='{ids['Commander']}' data-name='Commander' data-commander='1'>" in page
        assert f"<li data-cat='{ids['Ramp']}' data-name='Ramp'>" in page
        # plain Save categories (Save changes stays the one orange button) and Undo changes
        assert page.count("btn-primary") == 1
        assert (
            "<button class='btn' name='action' value='save'>" in page and "Save categories</button>" in page
        )
        assert "<button type='reset' class='btn'>Undo changes</button>" in page
        assert "Moving cards between categories stays in the deck editor and on the deck page." in page
        assert "Rename a category or change its flags on Archidekt." not in page
        # the confirm step's words come from the backend, the same with and without scripts
        assert f"data-confirm='{esc(modes.CATEGORY_COMMANDER_CONFIRM)}'" in page
        assert "data-confirm-title='Change the commander category?'" in page
    finally:
        await b.aclose()


async def test_a_category_without_a_record_reads_only_with_a_reason(stack: Stack) -> None:
    _give_categories(stack.ark)
    stack.ark.decks[42]["categories"].append(
        {"name": "Sideboard", "isPremier": False, "includedInDeck": False, "includedInPrice": False}
    )
    b = await linked_browser(stack)
    try:
        real = stack.h.app.state.gateway.decks.get_own_deck

        async def no_id(sub: str, deck_id: str):  # noqa: ANN202
            deck = await real(sub, deck_id)
            for c in deck.categories:
                if c["name"] == "Sideboard":
                    c["id"] = None
            return deck

        stack.h.app.state.gateway.decks.get_own_deck = no_id
        page, _ = await _csrf(b)
        assert "Sideboard" not in _ids(page)
        assert "<li class='ro' data-name='Sideboard'>" in page
        assert "name_" not in page.split("data-name='Sideboard'>")[1].split("</li>")[0]
        assert "Can't be changed from here yet." in page
        assert "<span class='ds-flag'>Not in deck</span>" in page
    finally:
        await b.aclose()


async def test_save_applies_at_once_and_the_panel_says_so(stack: Stack) -> None:
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        ids = _ids(page)
        data = _form(page, ids, **{f"name_{ids['Ramp']}": "Mana", f"not_in_price_{ids['Maybeboard']}": None})
        r = await b.http.post(POST, data={"csrf": csrf, "action": "save", **data}, headers=NAV)
        assert r.status_code == 303 and r.headers["location"] == f"{SETTINGS}?ok=categories#categories", (
            r.text
        )
        assert sorted(p["name"] for p in stack.ark.category_patches) == ["Mana", "Maybeboard"]
        after = (await b.http.get(r.headers["location"], headers=NAV)).text
        panel = after.split("id='categories'")[1].split("</section>")[0]
        assert CATEGORIES_SAVED in panel and "value='Mana'" in panel
        # the record everywhere is the backend's line: on the review page with Undo, in History
        pid = stack.h.app.state.gateway.db.list_proposals("user-1")[0]["id"]
        review = (await b.http.get(f"/proposals/{pid}", headers=NAV)).text
        line = "Category “Ramp”: rename to “Mana” (its 2 cards go with it). " + OPEN_TABS_NOTE
        assert line in review and "Category “Maybeboard”: Not in price off" in review
        assert "2 category changes; no cards change" in review
        assert "Net " not in review and ">2 changes<" not in review
        assert "Undo this change</button>" in review
        history = (await b.http.get("/history", headers=NAV)).text  # the snapshot, linking the change
        assert f"<a href='/proposals/{pid}'>the change</a>" in history
    finally:
        await b.aclose()


async def test_touching_the_commander_asks_first_then_saves(stack: Stack) -> None:
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        ids = _ids(page)
        data = _form(page, ids, **{f"premier_{ids['Commander']}": None})
        r = await b.http.post(POST, data={"csrf": csrf, "action": "save", **data}, headers=NAV)
        assert r.status_code == 200 and stack.ark.category_patches == []
        assert "<h2>Change the commander category?</h2>" in r.text
        assert esc(modes.CATEGORY_COMMANDER_CONFIRM) in r.text
        assert "name='confirmed' value='1'" in r.text and "Save categories</button>" in r.text
        assert f"href='{SETTINGS}#categories'>Cancel</a>" in r.text
        # nothing waits in the lists after a Cancel: the question made no proposal
        assert stack.h.app.state.gateway.db.list_proposals("user-1") == []
        r = await b.http.post(
            POST, data={"csrf": csrf, "action": "save", "confirmed": "1", **data}, headers=NAV
        )
        assert r.status_code == 303 and r.headers["location"].endswith("?ok=categories#categories")
        assert [p["isPremier"] for p in stack.ark.category_patches] == [False]
    finally:
        await b.aclose()


async def test_a_refusal_shows_in_the_panel_with_the_typed_values(stack: Stack) -> None:
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        ids = _ids(page)
        data = _form(page, ids, **{f"name_{ids['Ramp']}": "commander"})
        r = await b.http.post(POST, data={"csrf": csrf, "action": "save", **data}, headers=NAV)
        assert r.status_code == 400 and stack.ark.category_patches == []
        panel = r.text.split("id='categories'")[1].split("</section>")[0]
        assert "id='categories-msg' role='alert'" in panel
        assert "The deck already has a category named “commander”. Pick another name." in panel
        assert f"name='name_{ids['Ramp']}' value='commander'" in panel  # kept to fix
        # nothing changed: said plainly, nothing proposed
        r = await b.http.post(POST, data={"csrf": csrf, "action": "save", **_form(page, ids)}, headers=NAV)
        assert r.status_code == 400 and "Nothing to save: no name or flag was changed." in r.text
        assert stack.h.app.state.gateway.db.list_proposals("user-1") == []
    finally:
        await b.aclose()


async def test_the_review_and_lists_know_a_category_change(stack: Stack) -> None:
    """The assistant's proposal of the same change: the review page says it in words, Undo shows
    once applied, and a selection never applies it together with other changes."""
    from mtg_gateway import batch, proposals_view

    assert "categories" in proposals_view.UNDO_KINDS and "categories" in batch.ALONE_KINDS
    assert "categories" in batch.ALONE_REASONS
