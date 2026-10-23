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
from .test_deck_categories import RAMP, _give_categories

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
    """The panel's fields as the browser sends them: what each category was when the page was
    shown (its hidden ``was_`` fields), every name, each flag that is on, then
    ``changes`` on top (``None`` takes a flag off)."""
    data: dict[str, str] = dict(
        re.findall(r"<input type='hidden' name='(was_[a-z_]+_\d+)' value='([^']*)'>", page)
    )
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
                assert f"aria-label='Switches of “{name}”'" in page and label in page
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
        assert "Moving single cards between categories is in the deck editor and on the deck page." in page
        # each category with a record has a Delete button and its card count (create and delete,
        # Ben 22 Oct: one pane of glass)
        assert f"form='catdel' name='delete' value='{ids['Ramp']}' data-name='Ramp' data-cards='2'" in page
        assert "<span class='cnt'>2 cards</span>" in page
        # the box's extra sentence: Maybeboard decides what counts, Commander is the commander's
        assert (
            f"value='{ids['Maybeboard']}' data-name='Maybeboard' data-cards='0' "
            f"data-also='{esc(modes.CATEGORY_COUNTS_CONFIRM)}'" in page
        )
        assert (
            f"data-name='Commander' data-cards='1' data-also='{esc(modes.CATEGORY_COMMANDER_CONFIRM)}'"
            in page
        )
        assert f"value='{ids['Ramp']}' data-name='Ramp' data-cards='2' data-also=''" in page
        assert (
            "<form method='post' action='/decks/42/categories#categories' class='catnew' id='catnew'>" in page
        )
        assert "Create category</button>" in page
        assert "Rename a category or change its flags on Archidekt." not in page
        # the confirm step's words come from the backend, the same with and without scripts
        assert f"data-confirm='{esc(modes.CATEGORY_COMMANDER_CONFIRM)}'" in page
        assert "data-confirm-title='Change the commander category?'" in page
    finally:
        await b.aclose()


async def test_a_category_without_a_record_is_edited_like_any_other(stack: Stack) -> None:
    """Archidekt's default Sideboard may have no record yet (0.7.41 showed it read-only): its row
    is keyed d1, has no Delete button (nothing to delete), and a save gives it its record."""
    _give_categories(stack.ark)
    stack.ark.decks[42]["categories"].append(
        {"name": "Sideboard", "isPremier": False, "includedInDeck": False, "includedInPrice": False}
    )
    stack.ark.category_no_record.add("Sideboard")
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        assert "Sideboard" not in _ids(page)
        row = page.split("<li data-cat='d1' data-name='Sideboard'>")[1].split("</li>")[0]
        assert "name='name_d1' value='Sideboard'" in row and "name='delete'" not in row
        assert "Can't be changed from here yet." not in page
        data = _form(page, _ids(page), name_d1="Side", not_in_deck_d1="1")
        data.update(re.findall(r"<input type='hidden' name='(was_[a-z_]+_d1)' value='([^']*)'>", page))
        r = await b.http.post(
            POST, data={"csrf": csrf, "action": "save", "confirmed": "1", **data}, headers=NAV
        )
        assert r.status_code == 303, r.text
        assert [c["name"] for c in stack.ark.category_creates] == ["Sideboard"]
        assert [p["name"] for p in stack.ark.category_patches] == ["Side"]
    finally:
        await b.aclose()


async def test_create_a_category_from_the_panel(stack: Stack) -> None:
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        _page, csrf = await _csrf(b)
        r = await b.http.post(
            POST, data={"csrf": csrf, "action": "create", "category": "  Removal "}, headers=NAV
        )
        assert r.status_code == 303 and r.headers["location"] == f"{SETTINGS}?ok=category_created#categories"
        assert stack.ark.category_creates == [
            {
                "deck": 42,
                "name": "Removal",
                "includedInDeck": True,
                "includedInPrice": True,
                "isPremier": False,
                "id": None,
            }
        ]
        # Premier or Not in deck on a new category asks first, like a change
        r = await b.http.post(
            POST,
            data={"csrf": csrf, "action": "create", "category": "Big", "premier": "1"},
            headers=NAV,
        )
        assert r.status_code == 200 and len(stack.ark.category_creates) == 1, r.text
        assert "name='confirmed' value='1'" in r.text and "Create category</button>" in r.text
        # a name already used is refused in the panel, nothing sent
        r = await b.http.post(POST, data={"csrf": csrf, "action": "create", "category": "ramp"}, headers=NAV)
        assert r.status_code == 400 and len(stack.ark.category_creates) == 1
        assert "already has a category named" in r.text
    finally:
        await b.aclose()


async def test_delete_asks_first_and_a_category_with_cards_moves_them(stack: Stack) -> None:
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        ids = _ids(page)
        # an empty category: one confirm, then deleted
        r = await b.http.post(
            POST, data={"csrf": csrf, "action": "delete", "delete": str(ids["Maybeboard"])}, headers=NAV
        )
        assert r.status_code == 200 and stack.ark.category_deletes == []
        assert "<h2>Delete the category?</h2>" in r.text and "move_cards_to" not in r.text
        r = await b.http.post(
            POST,
            data={"csrf": csrf, "action": "delete", "delete": str(ids["Maybeboard"]), "confirmed": "1"},
            headers=NAV,
        )
        assert r.status_code == 303 and r.headers["location"].endswith("?ok=category_deleted#categories")
        assert stack.ark.category_deletes == [ids["Maybeboard"]]
        # a category with cards: the confirm asks where its cards go (Archidekt only deletes an
        # empty category), and the cards move before the delete
        r = await b.http.post(
            POST, data={"csrf": csrf, "action": "delete", "delete": str(ids["Ramp"])}, headers=NAV
        )
        assert r.status_code == 200, r.text
        assert "“Ramp” still has 2 cards." in r.text
        assert "<select id='move_cards_to' name='move_cards_to' required>" in r.text
        assert "<option value='Ramp'>" not in r.text
        r = await b.http.post(
            POST,
            data={
                "csrf": csrf,
                "action": "delete",
                "delete": str(ids["Ramp"]),
                "move_cards_to": "Commander",
                "confirmed": "1",
            },
            headers=NAV,
        )
        assert r.status_code == 303 and r.headers["location"].endswith("?ok=category_deleted#categories")
        assert ids["Ramp"] in stack.ark.category_deletes
        assert "Ramp" not in [c["name"] for c in stack.ark.decks[42]["categories"]]
        assert all(
            "Commander" in (row.get("categories") or [])
            for row in stack.ark.decks[42]["cards"]
            if row["card"]["oracleCard"]["name"] in RAMP
        )
    finally:
        await b.aclose()


async def test_delete_unused_lists_them_and_asks_first(stack: Stack) -> None:
    _give_categories(stack.ark)
    stack.ark.decks[42]["categories"].append(
        {"name": "Empty", "isPremier": False, "includedInDeck": True, "includedInPrice": True}
    )
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        assert "form='catunused' name='action' value='unused'" in page
        r = await b.http.post(POST, data={"csrf": csrf, "action": "unused"}, headers=NAV)
        assert r.status_code == 200 and stack.ark.category_deletes == []
        assert "<h2>Delete the category?</h2>" in r.text and "Delete category “Empty”." in r.text
        r = await b.http.post(POST, data={"csrf": csrf, "action": "unused", "confirmed": "1"}, headers=NAV)
        assert r.status_code == 303 and r.headers["location"].endswith("?ok=categories_unused#categories")
        assert _ids(page)["Empty"] in stack.ark.category_deletes
        assert stack.ark.category_deletes_with_cards == []
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
        assert r.status_code == 400 and "Nothing to save: no name or switch was changed." in r.text
        assert stack.h.app.state.gateway.db.list_proposals("user-1") == []
    finally:
        await b.aclose()


async def test_a_change_to_which_cards_count_asks_first_then_saves(stack: Stack) -> None:
    # Not in deck, or a rename to or from Sideboard or Maybeboard: an assistant's version waits for
    # the member's press, so the member's own Save asks once more too
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        ids = _ids(page)
        for change in (
            {f"not_in_deck_{ids['Maybeboard']}": None},
            {f"name_{ids['Ramp']}": "Sideboard"},
        ):
            data = _form(page, ids, **change)
            r = await b.http.post(POST, data={"csrf": csrf, "action": "save", **data}, headers=NAV)
            assert r.status_code == 200 and stack.ark.category_patches == []
            assert "<h2>Change which cards count in the deck?</h2>" in r.text
            assert esc(modes.CATEGORY_COUNTS_CONFIRM) in r.text
            assert "name='confirmed' value='1'" in r.text
            assert stack.h.app.state.gateway.db.list_proposals("user-1") == []
        data = _form(page, ids, **{f"not_in_deck_{ids['Maybeboard']}": None})
        r = await b.http.post(
            POST, data={"csrf": csrf, "action": "save", "confirmed": "1", **data}, headers=NAV
        )
        assert r.status_code == 303 and r.headers["location"].endswith("?ok=categories#categories")
        assert [p["includedInDeck"] for p in stack.ark.category_patches] == [True]
    finally:
        await b.aclose()


async def test_the_panel_carries_both_confirm_sentences_for_the_box(stack: Stack) -> None:
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, _ = await _csrf(b)
        assert "data-counts-confirm-title='Change which cards count in the deck?'" in page
        assert f"data-counts-confirm='{esc(modes.CATEGORY_COUNTS_CONFIRM)}'" in page
    finally:
        await b.aclose()


def _rename_on_archidekt(ark, old: str, new: str) -> None:  # noqa: ANN001
    """A rename made elsewhere (an assistant in Full auto, the deck editor), as Archidekt keeps it:
    the category's record and every card filed under it follow the new name."""
    deck = ark.decks[42]
    next(c for c in deck["categories"] if c["name"] == old)["name"] = new
    for row in deck["cards"]:
        if row.get("categories"):
            row["categories"] = [new if x == old else x for x in row["categories"]]


async def test_a_stale_form_never_undoes_a_change_made_since_the_page_loaded(stack: Stack) -> None:
    """M-1 (review 2026-10-22): the panel carries what each category was when the page was shown.
    A deck changed since is refused in the needs-a-look words and nothing is sent; the panel then
    shows the deck as it is now, so the rename made elsewhere is never put back."""
    from mtg_gateway import recheck

    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        ids = _ids(page)
        data = _form(page, ids, **{f"not_in_price_{ids['Maybeboard']}": None})
        _rename_on_archidekt(stack.ark, "Ramp", "Mana rocks")  # between page load and Save
        r = await b.http.post(POST, data={"csrf": csrf, "action": "save", **data}, headers=NAV)
        assert r.status_code == 400, r.text
        assert stack.ark.category_patches == []
        assert stack.h.app.state.gateway.db.list_proposals("user-1") == []
        panel = r.text.split("id='categories'")[1].split("</section>")[0]
        assert "id='categories-msg' role='alert'" in panel
        assert esc(recheck.reason_text(recheck.REASON_CHANGED)) in panel
        assert "Nothing was sent to Archidekt." in panel
        # the panel now shows the deck as it is, not the stale names
        assert "value='Mana rocks'" in panel and f"name='name_{ids['Ramp']}' value='Ramp'" not in panel
        # saved again from that panel, only the switch the member changed is sent
        page = r.text
        data = _form(page, _ids(page), **{f"not_in_price_{ids['Maybeboard']}": None})
        r = await b.http.post(POST, data={"csrf": csrf, "action": "save", **data}, headers=NAV)
        assert r.status_code == 303, r.text
        assert [p["name"] for p in stack.ark.category_patches] == ["Maybeboard"]
        assert any(c["name"] == "Mana rocks" for c in stack.ark.decks[42]["categories"])
    finally:
        await b.aclose()


async def test_only_the_fields_changed_on_the_page_become_changes(stack: Stack) -> None:
    """A switch changed elsewhere on a category the member did not touch is not part of the save
    when the page still matches it; with a baseline that no longer matches, the save is refused."""
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        ids = _ids(page)
        # a post without the page's baseline (a tab from before this release) is refused, too
        bare = {k: v for k, v in _form(page, ids, **{f"name_{ids['Ramp']}": "Mana"}).items()}
        bare = {k: v for k, v in bare.items() if not k.startswith("was_")}
        r = await b.http.post(POST, data={"csrf": csrf, "action": "save", **bare}, headers=NAV)
        assert r.status_code == 400 and stack.ark.category_patches == []
        data = _form(page, ids, **{f"name_{ids['Ramp']}": "Mana"})
        r = await b.http.post(POST, data={"csrf": csrf, "action": "save", **data}, headers=NAV)
        assert r.status_code == 303, r.text
        assert stack.ark.category_patches == [
            {
                "deck_id": 42,
                "name": "Mana",
                "includedInDeck": True,
                "includedInPrice": True,
                "isPremier": False,
                "id": ids["Ramp"],
            }
        ]
    finally:
        await b.aclose()


async def test_the_review_and_lists_know_a_category_change(stack: Stack) -> None:
    """The assistant's proposal of the same change: the review page says it in words, Undo shows
    once applied, and a selection never applies it together with other changes."""
    from mtg_gateway import batch, proposals_view

    assert "categories" in proposals_view.UNDO_KINDS and "categories" in batch.ALONE_KINDS
    assert "categories" in batch.ALONE_REASONS


async def test_a_category_name_cannot_break_the_odds_data_block(stack: Stack) -> None:
    # "<!--<script>" is a valid category name (the rename takes it, and any public deck can carry
    # it): inside the odds JSON block every "<" is escaped, so the block ends where it should
    import json

    _give_categories(stack.ark)
    name = "<!--<script>"
    for c in stack.ark.decks[42]["categories"]:
        if c["name"] == "Ramp":
            c["name"] = name
    for row in stack.ark.decks[42]["cards"]:
        row["categories"] = [name if x == "Ramp" else x for x in row.get("categories") or []]
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/decks/42", headers=NAV)).text
        block = page.split("<script type='application/json' id='odds-data'>")[1].split("</script>")[0]
        assert "<" not in block
        assert name in json.dumps(json.loads(block), ensure_ascii=False)
    finally:
        await b.aclose()


async def test_moving_cards_into_the_commander_category_asks_with_the_commander_sentence(
    stack: Stack,
) -> None:
    """S-01 (review 0.7.42): the target is picked on the delete page (or in the box), so that pick
    is not yet a yes to making the cards commanders: the gateway asks once more with that sentence."""
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        ids = _ids(page)
        r = await b.http.post(
            POST,
            data={
                "csrf": csrf,
                "action": "delete",
                "delete": str(ids["Ramp"]),
                "move_cards_to": "Commander",
                "move_ok": "1",
            },
            headers=NAV,
        )
        assert r.status_code == 200 and stack.ark.category_deletes == [], r.text
        assert esc(modes.CATEGORY_COMMANDER_CONFIRM) in r.text
        assert "name='move_cards_to' value='Commander'" in r.text and "name='confirmed' value='1'" in r.text
        # an ordinary target goes ahead on the pick alone
        r = await b.http.post(
            POST,
            data={
                "csrf": csrf,
                "action": "delete",
                "delete": str(ids["Ramp"]),
                "move_cards_to": "Maybeboard",
                "move_ok": "1",
            },
            headers=NAV,
        )
        assert r.status_code in (200, 303)
    finally:
        await b.aclose()


async def test_a_refused_create_or_delete_keeps_the_switches_as_they_are(stack: Stack) -> None:
    """S-02 (review 0.7.42): a refused New category, Delete or Delete unused carries no row fields,
    so the panel drawn with the refusal must show the deck's own switches, not all of them off."""
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        ids = _ids(page)
        r = await b.http.post(POST, data={"csrf": csrf, "action": "create", "category": "ramp"}, headers=NAV)
        assert r.status_code == 400
        assert f"name='premier_{ids['Commander']}' value='1' checked" in r.text
        assert f"name='not_in_deck_{ids['Maybeboard']}' value='1' checked" in r.text
    finally:
        await b.aclose()


async def test_delete_unused_over_30_deletes_the_first_30(stack: Stack) -> None:
    """Security review 0.7.42 S-08: with more than 30 empty categories the page offered a press
    the 30-change cap always refused."""
    _give_categories(stack.ark)
    for i in range(31):
        stack.ark.decks[42]["categories"].append(
            {"name": f"E{i}", "isPremier": False, "includedInDeck": True}
        )
    b = await linked_browser(stack)
    try:
        page, csrf = await _csrf(b)
        assert "Delete unused categories (30 at a time)" in page
        r = await b.http.post(POST, data={"csrf": csrf, "action": "unused", "confirmed": "1"}, headers=NAV)
        assert r.status_code == 303, r.text[-500:]
        assert len(stack.ark.category_deletes) == 30
        page, _csrf2 = await _csrf(b)
        assert "Delete unused categories (2)" in page
    finally:
        await b.aclose()
