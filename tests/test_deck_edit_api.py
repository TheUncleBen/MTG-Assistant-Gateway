"""The deck page's own save: ``POST /api/v1/decks/{id}/edit`` runs the proposals path (propose,
hand-edit confirmation, apply with a snapshot as the member's own press), re-reads the deck and
answers with the touched rows, fresh checks and the re-rendered Legality chip and Deck checks
panel. Browser session with the CSRF header only; a deck the linked account does not own is
refused; a big removal asks first and the follow-up names the proposal."""

from __future__ import annotations

import asyncio

import pytest

from mtg_gateway import api as api_module
from mtg_gateway import decks as decks_mod

from .conftest import COLLECTION, Stack, stack_with
from .test_browse_collection import api, linked
from .test_round4_data import _app_token

stack_options = stack_with(COLLECTION)


def _deck_card(st: Stack, name: str, *, zone: str | None = None) -> list[dict]:
    rows = [c for c in st.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == name]
    if zone == "side":
        return [c for c in rows if c["categories"] == ["Maybeboard"]]
    if zone == "main":
        return [c for c in rows if c["categories"] != ["Maybeboard"]]
    return rows


def _legal_commander_deck(st: Stack) -> None:
    """Deck 42 as a legal 100-card Commander deck: the CSV fixture has no supertypes, so the
    commander gets Legendary and the basic lands Basic, as Archidekt's deck JSON carries them."""
    st.ark.decks[42]["deckFormat"] = 3
    for name, sup in (("Aesi, Tyrant of Gyre Strait", "Legendary"), ("Forest", "Basic"), ("Island", "Basic")):
        for row in _deck_card(st, name):
            row["card"]["oracleCard"]["superTypes"] = [sup]


async def test_quantity_remove_and_category_edits_answer_with_fresh_rows_and_checks(stack: Stack) -> None:  # noqa: F811
    stack.ark.decks[42]["deckFormat"] = 3  # Commander: a 100-card deck with legality to report
    b = await linked(stack)
    try:
        patches = len(stack.ark.patches)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["ok"] and d["applied"] is True and d["snapshot_id"] and d["stale"] is False
        # the quick edit keeps the gateway snapshot but makes no extra copy on Archidekt
        assert d["result"]["result"]["archidekt_backup"] == "skipped", d["result"]
        assert len(stack.ark.patches) > patches
        assert d["rows"] == [
            {
                "name": "Cultivate",
                "qty": 2,
                "zone": "main",
                "categories": d["rows"][0]["categories"],
                "relation_id": d["rows"][0]["relation_id"],
                "finish": "Normal",
                "label": ",#656565",  # archidekt.com's empty tag, sent with every modify
                "mana_value": None,
                "notes": "",
                # the row's printing, so a printing change redraws the picture (Printings…)
                "set_code": d["rows"][0]["set_code"],
                "collector_number": d["rows"][0]["collector_number"],
                "image": d["rows"][0]["image"],
                "price": d["rows"][0]["price"],
            }
        ]
        assert d["rows"][0]["set_code"] and d["rows"][0]["collector_number"]
        assert _deck_card(stack, "Cultivate")[0]["quantity"] == 2
        # the checks are computed from the re-read deck: 101 cards now, so the deck size check fails
        st = d["stats"]
        assert st["card_count"] == 101 and st["checks"]["deck_size"]["ok"] is False
        # every card is legal, but a 101-card Commander deck is not: the chip says so, as on Archidekt
        assert st["legal"] is False and st["problems"] == [] and st["format"] == "commander"
        assert "deck has 101 cards" in " ".join(st["checks"]["problems"])
        assert "class='legal bad'" in d["banner_html"] and "deck has 101 cards" in d["banner_html"]
        assert d["checks_html"].startswith("<div class='checks'><h3>Deck checks</h3>")
        assert "101 of 100" in d["checks_html"] and "class='bad'" in d["checks_html"]
        assert "Not legal in Commander" in d["legality_html"]
        # the proposal is in History as applied, with its snapshot
        listed = (await b.http.get("/api/v1/proposals")).json()["proposals"]
        assert listed[0]["id"] == d["proposal_id"] and listed[0]["state"] == "applied"

        # remove: the card leaves the deck, so no row comes back for it and the size is right again
        r = await api(
            b, "POST", "/api/v1/decks/42/edit", {"changes": [{"action": "remove", "card_name": "Cultivate"}]}
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True and d["rows"] == []
        assert _deck_card(stack, "Cultivate") == []
        assert d["stats"]["card_count"] == 99 and d["stats"]["checks"]["deck_size"]["ok"] is False

        # set_category: the row's categories change and the row says so
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_category", "card_name": "Acidic Slime", "category": "Removal"}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True
        assert d["rows"] == [
            {
                "name": "Acidic Slime",
                "qty": 1,
                "zone": "main",
                "categories": ["Removal"],
                "relation_id": d["rows"][0]["relation_id"],
                "finish": "Normal",
                "label": ",#656565",
                "mana_value": None,
                "notes": "",
                # the row's printing, so a printing change redraws the picture (Printings…)
                "set_code": d["rows"][0]["set_code"],
                "collector_number": d["rows"][0]["collector_number"],
                "image": d["rows"][0]["image"],
                "price": d["rows"][0]["price"],
            }
        ]
        assert d["rows"][0]["set_code"] and d["rows"][0]["collector_number"]
        assert _deck_card(stack, "Acidic Slime")[0]["categories"] == ["Removal"]
    finally:
        await b.aclose()


async def test_an_add_puts_a_card_back_under_every_category(stack: Stack) -> None:  # noqa: F811
    """Bug 7: the deck page's Undo of a removal names every category the row had; the add goes
    in under all of them, the primary first. The rows also carry the card's notes (read-only)."""
    row = _deck_card(stack, "Cultivate")[0]
    row.update(categories=["Ramp", "Value"], notes="fetches both")
    b = await linked(stack)
    try:
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": [{"action": "set_quantity",
                      "card_name": "Cultivate", "quantity": 2}]})  # fmt: skip
        assert r.status_code == 201 and r.json()["rows"][0]["notes"] == "fetches both", r.text
        r = await api(
            b, "POST", "/api/v1/decks/42/edit", {"changes": [{"action": "remove", "card_name": "Cultivate"}]}
        )
        assert r.status_code == 201 and _deck_card(stack, "Cultivate") == [], r.text
        add = {"action": "add", "card_name": "Cultivate", "quantity": 2, "category": "Ramp",
               "categories": ["Ramp", "Value"]}  # fmt: skip
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": [add]})
        assert r.status_code == 201, r.text
        assert [x["categories"] for x in r.json()["rows"]] == [["Ramp", "Value"]]
        back = _deck_card(stack, "Cultivate")
        assert len(back) == 1 and back[0]["categories"] == ["Ramp", "Value"] and back[0]["quantity"] == 2
        # categories go with an add only, and the first is the category
        for bad in (
            {"action": "set_quantity", "card_name": "Cultivate", "quantity": 1, "categories": ["Ramp"]},
            {"action": "add", "card_name": "Opt", "category": "Draw", "categories": ["Ramp", "Draw"]},
            {"action": "add", "card_name": "Opt", "categories": "Ramp"},
        ):
            r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": [bad]})
            assert r.status_code == 400 and r.json()["error"] == "invalid", (bad, r.text)
    finally:
        await b.aclose()


async def test_undo_puts_back_a_maybeboard_row_in_the_commander_zone(stack: Stack) -> None:  # noqa: F811
    """The deck page's Undo of removing a maybeboard row that also carries the commander zone's
    category puts it back as it was: the add's first category is outside the deck, so the row is
    written with every category as asked, the commander zone included. It is a commander-zone
    change, so the page asks first; the answer applies it, and the row reads back as it was."""
    stack.ark.add_side_row(42, "Sol Ring", quantity=2)
    side = _deck_card(stack, "Sol Ring", zone="side")[0]
    side["categories"] = ["Maybeboard", "Commander"]
    b = await linked(stack)
    try:
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "remove", "card_name": "Sol Ring", "zone": "side"}]},
        )
        d = r.json()
        if d.get("needs_confirm"):
            r = await api(
                b, "POST", "/api/v1/decks/42/edit", {"proposal_id": d["proposal_id"], "version": d["version"]}
            )
        assert r.status_code in (200, 201), r.text
        rows = [c for c in stack.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring"]
        assert all(c["categories"] != ["Maybeboard", "Commander"] for c in rows), rows
        undo = {"action": "add", "card_name": "Sol Ring", "quantity": 2, "zone": "side",
                "category": "Maybeboard", "categories": ["Maybeboard", "Commander"]}  # fmt: skip
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": [undo]})
        d = r.json()
        assert r.status_code == 201 and d["needs_confirm"] is True, r.text
        assert d["why"] == "This adds or changes a row with a commander-zone category.", d
        r = await api(
            b, "POST", "/api/v1/decks/42/edit", {"proposal_id": d["proposal_id"], "version": d["version"]}
        )
        assert r.status_code in (200, 201) and r.json().get("applied") is True, r.text
        back = [
            (c["quantity"], c["categories"])
            for c in stack.ark.decks[42]["cards"]
            if c["card"]["oracleCard"]["name"] == "Sol Ring"
            and c["categories"]
            and c["categories"][0] == "Maybeboard"
        ]
        assert back == [(2, ["Maybeboard", "Commander"])], back
    finally:
        await b.aclose()


async def test_a_move_puts_a_card_back_under_every_category(stack: Stack) -> None:  # noqa: F811
    """The deck page's Undo of a move of a card filed under several categories is a
    set_category with every category it had, the primary first; it files the row under all of
    them. Only set_category and add take categories, and the first is the category."""
    row = _deck_card(stack, "Cultivate")[0]
    row.update(categories=["Ramp", "Value"])
    b = await linked(stack)
    try:
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": [{"action": "set_category",
                      "card_name": "Cultivate", "category": "Lands"}]})  # fmt: skip
        assert r.status_code == 201 and _deck_card(stack, "Cultivate")[0]["categories"] == ["Lands"], r.text
        back = {"action": "set_category", "card_name": "Cultivate", "category": "Ramp",
                "categories": ["Ramp", "Value"]}  # fmt: skip
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": [back]})
        assert r.status_code == 201, r.text
        assert [x["categories"] for x in r.json()["rows"]] == [["Ramp", "Value"]]
        assert _deck_card(stack, "Cultivate")[0]["categories"] == ["Ramp", "Value"]
        for bad in (
            {
                "action": "set_category",
                "card_name": "Cultivate",
                "category": "Value",
                "categories": ["Ramp", "Value"],
            },
            {"action": "set_commander", "card_name": "Cultivate", "categories": ["Commander", "Ramp"]},
            {"action": "set_category", "card_name": "Cultivate", "categories": "Ramp"},
        ):
            r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": [bad]})
            assert r.status_code == 400 and r.json()["error"] == "invalid", (bad, r.text)
    finally:
        await b.aclose()


def test_set_category_categories_round_trip_through_the_stored_proposal() -> None:
    from mtg_gateway.decks import parse_changes

    ch = parse_changes([{"action": "set_category", "card_name": "Opt", "categories": ["Draw", "Cantrip"]}])[0]
    assert ch.category == "Draw" and ch.categories == ("Draw", "Cantrip")
    assert parse_changes([ch.as_dict()]) == [ch]
    one = parse_changes([{"action": "set_category", "card_name": "Opt", "category": "Draw"}])[0]
    assert "categories" not in one.as_dict()


async def test_an_add_puts_back_the_companion_and_flip_flags(stack: Stack) -> None:  # noqa: F811
    """The deck page's Undo of a removal names the row's companion and "flipped by default"
    flags; the add puts them back, as a printing change and a restore already do."""
    row = _deck_card(stack, "Cultivate")[0]
    row.update(companion=True, flippedDefault=True)
    b = await linked(stack)
    try:
        r = await api(
            b, "POST", "/api/v1/decks/42/edit", {"changes": [{"action": "remove", "card_name": "Cultivate"}]}
        )
        assert r.status_code == 201 and _deck_card(stack, "Cultivate") == [], r.text
        add = {"action": "add", "card_name": "Cultivate", "quantity": 1, "category": "Ramp",
               "companion": True, "flipped_default": True}  # fmt: skip
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": [add]})
        assert r.status_code == 201, r.text
        back = _deck_card(stack, "Cultivate")
        assert [(x["companion"], x["flippedDefault"]) for x in back] == [(True, True)]
        # an add without them stays as before; they go with an add only, as true or false
        r = await api(
            b, "POST", "/api/v1/decks/42/edit", {"changes": [{"action": "add", "card_name": "Opt"}]}
        )
        assert r.status_code == 201 and _deck_card(stack, "Opt")[0]["companion"] is False, r.text
        for bad in (
            {"action": "set_quantity", "card_name": "Cultivate", "quantity": 2, "companion": True},
            {"action": "add", "card_name": "Opt", "flipped_default": "yes"},
        ):
            r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": [bad]})
            assert r.status_code == 400 and r.json()["error"] == "invalid", (bad, r.text)
    finally:
        await b.aclose()


def test_add_flags_round_trip_through_the_stored_proposal() -> None:
    from mtg_gateway.decks import parse_changes

    ch = parse_changes(
        [{"action": "add", "card_name": "Lurrus", "companion": True, "flipped_default": True}], row_flags=True
    )[0]
    assert ch.companion and ch.flipped_default
    assert parse_changes([ch.as_dict()], row_flags=True) == [ch]
    # only the deck page's own edit reads them; every other caller drops them
    dropped = parse_changes([ch.as_dict()])[0]
    assert not dropped.companion and not dropped.flipped_default
    plain = parse_changes([{"action": "add", "card_name": "Opt"}])[0].as_dict()
    assert "companion" not in plain and "flipped_default" not in plain


def test_add_categories_round_trip_through_the_stored_proposal() -> None:
    from mtg_gateway.decks import parse_changes

    ch = parse_changes([{"action": "add", "card_name": "Opt", "categories": ["Draw", "Cantrip", "Draw"]}])[0]
    assert ch.category == "Draw" and ch.categories == ("Draw", "Cantrip")
    assert parse_changes([ch.as_dict()]) == [ch]
    assert "categories" not in parse_changes([{"action": "add", "card_name": "Opt"}])[0].as_dict()


async def test_side_zone_edits_target_the_maybeboard_row(stack: Stack) -> None:  # noqa: F811
    stack.ark.add_side_row(42, "Sol Ring", 1)
    b = await linked(stack)
    try:
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Sol Ring", "quantity": 3, "zone": "side"}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True and d["stale"] is False
        rows = {(x["zone"], x["qty"]) for x in d["rows"]}
        assert ("side", 3) in rows, d["rows"]
        assert ("main", 1) in rows, d["rows"]  # the deck's own Sol Ring row is untouched
        assert d["stats"]["side_count"] == 3 and d["stats"]["card_count"] == 100
        assert _deck_card(stack, "Sol Ring", zone="side")[0]["quantity"] == 3
        assert _deck_card(stack, "Sol Ring", zone="main")[0]["quantity"] == 1
        # a maybeboard card moved into a counted category joins the deck (zone side names the row)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {
                "changes": [
                    {"action": "set_category", "card_name": "Sol Ring", "category": "Ramp", "zone": "side"}
                ]
            },
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True
        assert [x["zone"] for x in d["rows"]] == ["main", "main"] and ["Ramp"] in [
            x["categories"] for x in d["rows"]
        ]
        assert d["stats"]["side_count"] == 0 and d["stats"]["card_count"] == 103
    finally:
        await b.aclose()


async def test_a_deck_changed_on_archidekt_is_judged_as_it_is_now(stack: Stack) -> None:  # noqa: F811
    """The deck is legal, then a card is added on Archidekt (outside the gateway) to make
    101: the deck page and the API both say it is not legal, though every card still is."""
    _legal_commander_deck(stack)
    b = await linked(stack)
    try:
        page = await b.http.get("/decks/42", headers={"Accept": "text/html"})
        assert page.status_code == 200 and "Size: 100" in page.text
        assert "class='legal ok'" in page.text and "Legal in Commander" in page.text
        before = (await b.http.get("/api/v1/decks/42/stats")).json()["stats"]
        assert before["card_count"] == 100 and before["checks"]["legal"] is True

        _deck_card(stack, "Forest")[0]["quantity"] += 1  # the owner's edit on archidekt.com
        stack.ark.decks[42]["updatedAt"] = "2026-10-10T12:00:00Z"

        # 0.7.23: for a few seconds a plain view shows the deck as just read (a run of view
        # switches asks Archidekt once); the browser's Reload reads it again at once
        page = await b.http.get("/decks/42", headers={"Accept": "text/html"})
        assert "Size: 100" in page.text
        # ... and once those seconds are over, a plain view still shows it at once (0.7.46) but
        # reads it again behind it, so the view after shows the deck as it is now
        decks = stack.h.app.state.gateway.decks
        decks._page_decks = {k: (t - decks.page_deck_seconds, d) for k, (t, d) in decks._page_decks.items()}
        page = await b.http.get("/decks/42", headers={"Accept": "text/html"})
        assert "Size: 100" in page.text
        await asyncio.gather(*decks._page_refreshing.values())

        page = await b.http.get("/decks/42", headers={"Accept": "text/html"})
        assert "Size: 101" in page.text and "class='legal ok'" not in page.text
        assert "class='legal bad'" in page.text and "deck has 101 cards; commander wants 100" in page.text
        assert "Not legal in Commander" in page.text and "101 of 100" in page.text
        after = (await b.http.get("/api/v1/decks/42/stats")).json()["stats"]
        assert after["card_count"] == 101 and "legality_problems" not in after
        assert after["checks"]["legality"]["banned"] == []
        assert after["checks"]["legal"] is False and after["checks"]["ok"] is False
        assert after["checks"]["legal_problems"] == ["deck has 101 cards; commander wants 100"]

        # the browser's Reload (max-age=0) of a read older than PAGE_DECK_RELOAD shows it at once
        # and reads it again behind it (owner decision, 0.7.46); the next view shows the change
        _deck_card(stack, "Forest")[0]["quantity"] += 1
        page = await b.http.get("/decks/42", headers={"Accept": "text/html"})
        assert "Size: 101" in page.text
        decks._page_decks = {
            k: (t - decks_mod.PAGE_DECK_RELOAD - 1, d) for k, (t, d) in decks._page_decks.items()
        }
        page = await b.http.get("/decks/42", headers={"Accept": "text/html", "Cache-Control": "max-age=0"})
        assert "Size: 101" in page.text and "data-reload='1'" in page.text
        await asyncio.gather(*decks._page_refreshing.values())
        page = await b.http.get("/decks/42", headers={"Accept": "text/html", "Cache-Control": "max-age=0"})
        assert "Size: 102" in page.text
    finally:
        await b.aclose()


async def test_save_anyway_keeps_the_editors_unticked_backup_box(stack: Stack) -> None:  # noqa: F811
    """The deck editor's save that asks first is applied by its "Save anyway" through the
    proposal's apply route. That route made a backup copy on Archidekt even with the editor's box
    unticked; it now takes the box (archidekt_backup) from the browser."""
    b = await linked(stack)
    try:
        deck = stack.ark.decks[42]
        names = [c["card"]["oracleCard"]["name"] for c in deck["cards"] if c["categories"] != ["Commander"]]
        for keep_copy, batch in ((False, names[:9]), (True, names[9:18])):
            changes = [{"action": "remove", "card_name": n} for n in batch]
            body = {"kind": "edit", "deck_id": "42", "changes": changes, "apply": True}
            r = await api(b, "POST", "/api/v1/proposals", {**body, "archidekt_backup": keep_copy})
            d = r.json()
            assert r.status_code == 201 and d["needs_confirm"] is True, r.text
            r = await api(
                b,
                "POST",
                f"/api/v1/proposals/{d['proposal_id']}/apply",
                {"version": d["version"], "archidekt_backup": keep_copy},
            )
            a = r.json()
            assert r.status_code == 200 and a["state"] == "applied", r.text
            if keep_copy:
                assert a["result"].get("backup_url"), a["result"]
            else:
                assert a["result"].get("archidekt_backup") == "skipped", a["result"]
                assert not a["result"].get("backup_url"), a["result"]
    finally:
        await b.aclose()


async def test_an_assistants_proposal_applied_from_the_browser_keeps_its_backup_copy(stack: Stack) -> None:  # noqa: F811
    """The browser's archidekt_backup false on the apply route is the deck editor's own box: an
    assistant's proposal applied from the browser still gets its backup copy on Archidekt."""
    b = await linked(stack)
    try:
        token, _cid = await _app_token(stack.h, "assistant-test")
        r = await stack.h.http.post(
            "/api/v1/proposals",
            json={
                "kind": "edit",
                "deck_id": "42",
                "changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 3}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        pid = r.json()["proposal_id"]
        r = await api(b, "POST", f"/api/v1/proposals/{pid}/apply", {"version": 1, "archidekt_backup": False})
        a = r.json()
        assert r.status_code == 200 and a["state"] == "applied", r.text
        assert a["result"].get("backup_url") and "archidekt_backup" not in a["result"], a["result"]
    finally:
        await b.aclose()


async def test_a_saved_choice_of_no_backup_copy_holds_when_applied_later(stack: Stack) -> None:  # noqa: F811
    """A member's own save that makes no backup copy (the deck editor with its box unticked, or
    the deck page) and is left waiting (its question unanswered, or writes off) keeps that choice
    when it is applied later from its review page or the Proposals page, which send no box."""
    b = await linked(stack)
    try:
        deck = stack.ark.decks[42]
        names = [c["card"]["oracleCard"]["name"] for c in deck["cards"] if c["categories"] != ["Commander"]]
        editor = {
            "kind": "edit",
            "deck_id": "42",
            "changes": [{"action": "remove", "card_name": n} for n in names[:9]],
            "apply": True,
            "archidekt_backup": False,
        }
        r = await api(b, "POST", "/api/v1/proposals", editor)
        asked_editor = r.json()
        page = {"changes": [{"action": "remove", "card_name": n} for n in names[9:18]]}
        r = await api(b, "POST", "/api/v1/decks/42/edit", page)
        asked_page = r.json()
        for d in (asked_editor, asked_page):
            assert d["needs_confirm"] is True, d
            waiting = (await api(b, "GET", f"/api/v1/proposals/{d['proposal_id']}")).json()
            assert waiting["state"] == "pending" and waiting["result"] is None, waiting
            r = await api(b, "POST", f"/api/v1/proposals/{d['proposal_id']}/apply", {"version": d["version"]})
            a = r.json()
            assert r.status_code == 200 and a["state"] == "applied", r.text
            assert a["result"].get("archidekt_backup") == "skipped" and not a["result"].get("backup_url"), a
    finally:
        await b.aclose()


@pytest.mark.parametrize("box", [None, False, "deck page"])
async def test_an_own_save_an_app_changed_in_any_version_keeps_its_backup_copy(stack: Stack, box) -> None:  # noqa: F811
    """A member's own save that made no backup copy, changed by an app in an earlier version and
    by the member's browser in the latest one, gets its backup copy when applied, with or without
    an unticked box sent along: the choice holds only while no app ever touched it."""
    from mtg_gateway.decks import BROWSER_CLIENT

    b = await linked(stack)
    try:
        deck = stack.ark.decks[42]
        names = [c["card"]["oracleCard"]["name"] for c in deck["cards"] if c["categories"] != ["Commander"]]
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "remove", "card_name": n} for n in names[:9]]},
        )
        d = r.json()
        assert d["needs_confirm"] is True, d
        db, pid = stack.h.db, d["proposal_id"]
        row = db.get_proposal(pid, "user-1")
        for version, client in ((1, "some-app"), (2, BROWSER_CLIENT)):  # no browser amend route: faked
            kept = dict(
                changes=row["changes"], rows=row["rows"], diff_text=row["diff_text"], by_client=client
            )
            assert db.amend_proposal(pid, "user-1", expect_version=version, **kept) == version + 1
        assert db.get_proposal(pid, "user-1")["amended_by_client"] == BROWSER_CLIENT
        if box == "deck page":  # its own question answered: the deck page's save sends no box
            r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": pid, "version": 3})
            assert r.status_code == 201 and r.json()["applied"] is True, r.text
            result = r.json()["result"]["result"]
        else:
            body = {"version": 3} if box is None else {"version": 3, "archidekt_backup": box}
            r = await api(b, "POST", f"/api/v1/proposals/{pid}/apply", body)
            assert r.status_code == 200 and r.json()["state"] == "applied", r.text
            result = r.json()["result"]
        assert result.get("backup_url") and "archidekt_backup" not in result, result
    finally:
        await b.aclose()


async def test_a_box_ticked_before_save_anyway_makes_the_backup_copy(stack: Stack) -> None:  # noqa: F811
    """The editor's first save had its box unticked; the member ticks it before pressing Save
    anyway, which sends archidekt_backup true: that save gets its backup copy."""
    b = await linked(stack)
    try:
        deck = stack.ark.decks[42]
        names = [c["card"]["oracleCard"]["name"] for c in deck["cards"] if c["categories"] != ["Commander"]]
        body = {
            "kind": "edit",
            "deck_id": "42",
            "changes": [{"action": "remove", "card_name": n} for n in names[:9]],
            "apply": True,
            "archidekt_backup": False,
        }
        d = (await api(b, "POST", "/api/v1/proposals", body)).json()
        assert d["needs_confirm"] is True, d
        r = await api(
            b,
            "POST",
            f"/api/v1/proposals/{d['proposal_id']}/apply",
            {"version": d["version"], "archidekt_backup": True},
        )
        a = r.json()
        assert r.status_code == 200 and a["state"] == "applied", r.text
        assert a["result"].get("backup_url") and "archidekt_backup" not in a["result"], a["result"]
    finally:
        await b.aclose()


async def test_an_own_save_stopped_and_waiting_again_keeps_its_choice(stack: Stack) -> None:  # noqa: F811
    """An own save that makes no backup copy, stopped before anything was written (stale, or by
    Archidekt's rate limit) and so waiting again with a new result, keeps its choice when it is
    applied later."""
    b = await linked(stack)
    try:
        deck = stack.ark.decks[42]
        names = [c["card"]["oracleCard"]["name"] for c in deck["cards"] if c["categories"] != ["Commander"]]
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "remove", "card_name": n} for n in names[:9]]},
        )
        d = r.json()
        assert d["needs_confirm"] is True, d
        db, pid = stack.h.db, d["proposal_id"]
        # as decks.apply records a stop that wrote nothing: pending again, its result replaced
        db.finish_proposal(pid, state="pending", result={"error": "rate_limited", "detail": "Wait."})
        assert db.get_proposal(pid, "user-1")["result"]["error"] == "rate_limited"
        r = await api(b, "POST", f"/api/v1/proposals/{pid}/apply", {"version": d["version"]})
        a = r.json()
        assert r.status_code == 200 and a["state"] == "applied", r.text
        assert a["result"].get("archidekt_backup") == "skipped" and not a["result"].get("backup_url"), a
        # a closed result is replaced as it was (no mark is carried into it)
        assert "no_backup_copy" not in db.get_proposal(pid, "user-1")["result"]
    finally:
        await b.aclose()


@pytest.mark.parametrize("stop", ["writes off", "rate limit"])
@pytest.mark.parametrize("first_box", [False, True])
async def test_the_box_at_a_stopped_save_anyway_is_the_saves_choice(  # noqa: F811
    stack: Stack, monkeypatch: pytest.MonkeyPatch, stop: str, first_box: bool
) -> None:
    """The member changes the backup box between the editor's first save and its Save anyway,
    and that press is stopped (writes off, or Archidekt's rate limit), so the save waits. Applied
    later with no box sent, it follows the box at the press, not the one at the first save."""
    import dataclasses

    import httpx

    from mtg_gateway.archidekt import ArchidektClient

    async def no_wait(self, seconds: float) -> None:
        return None

    monkeypatch.setattr(ArchidektClient, "_wait_out", no_wait)
    b = await linked(stack)
    try:
        decks = stack.h.app.state.gateway.decks
        deck = stack.ark.decks[42]
        names = [c["card"]["oracleCard"]["name"] for c in deck["cards"] if c["categories"] != ["Commander"]]
        body = {
            "kind": "edit",
            "deck_id": "42",
            "changes": [{"action": "remove", "card_name": n} for n in names[:9]],
            "apply": True,
            "archidekt_backup": first_box,
        }
        d = (await api(b, "POST", "/api/v1/proposals", body)).json()
        assert d["needs_confirm"] is True, d
        pid, pressed = d["proposal_id"], not first_box
        orig = stack.ark.transport.handler
        if stop == "writes off":
            decks.settings = dataclasses.replace(decks.settings, writes_enabled=False)
        else:

            def slow(req: httpx.Request) -> httpx.Response:
                if req.method == "PATCH" and "modifyCards" in req.url.path:
                    return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "0"})
                return orig(req)

            stack.ark.transport.handler = slow
        r = await api(
            b,
            "POST",
            f"/api/v1/proposals/{pid}/apply",
            {"version": d["version"], "archidekt_backup": pressed},
        )
        assert r.status_code >= 400, r.text
        waiting = stack.h.db.get_proposal(pid, "user-1")
        assert waiting["state"] == "pending", waiting
        if not pressed:  # stored as a true boolean, also after a stop replaced the result
            assert waiting["result"]["no_backup_copy"] is True, waiting["result"]
        decks.settings = dataclasses.replace(decks.settings, writes_enabled=True)
        stack.ark.transport.handler = orig
        r = await api(b, "POST", f"/api/v1/proposals/{pid}/apply", {"version": waiting["version"]})
        a = r.json()
        assert r.status_code == 200 and a["state"] == "applied", r.text
        if pressed:
            assert a["result"].get("backup_url"), a["result"]
        else:
            assert a["result"].get("archidekt_backup") == "skipped" and not a["result"].get("backup_url"), a
    finally:
        await b.aclose()


async def test_big_removal_asks_first_then_the_proposal_id_applies_it(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        deck = stack.ark.decks[42]
        names = [c["card"]["oracleCard"]["name"] for c in deck["cards"] if c["categories"] != ["Commander"]][
            :9
        ]
        changes = [{"action": "remove", "card_name": n} for n in names]
        before = len(stack.ark.patches)
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": changes})
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is False and d["needs_confirm"] is True and "cards" in d["why"]
        assert "rows" not in d and len(stack.ark.patches) == before  # nothing sent before the answer
        # the follow-up names the proposal (the page's "Save anyway") and gets the full answer
        assert d["version"] == 1
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": d["proposal_id"], "version": 1})
        assert r.status_code == 201, r.text
        a = r.json()
        assert a["applied"] is True and a["proposal_id"] == d["proposal_id"] and a["rows"] == []
        assert a["stats"]["card_count"] == 91 and len(stack.ark.patches) > before
        # a proposal of another kind or deck is refused by the follow-up
        other = await api(b, "POST", "/api/v1/proposals", {"kind": "clone", "deck_id": "42"})
        assert other.status_code == 201
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": other.json()["proposal_id"]})
        assert r.status_code == 400 and r.json()["error"] == "invalid"
        # confirmed: true skips the question when the page already asked
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {
                "changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}],
                "confirmed": True,
            },
        )
        assert r.status_code == 201 and r.json()["applied"] is True, r.text
    finally:
        await b.aclose()


async def test_save_anyway_applies_only_the_version_the_question_showed(stack: Stack) -> None:  # noqa: F811
    """Another app may amend the proposal the page's question was about; the answer sends the
    version the question showed, so an amended one is refused with nothing sent, and an answer
    without a version is refused."""
    b = await linked(stack)
    try:
        names = [
            c["card"]["oracleCard"]["name"]
            for c in stack.ark.decks[42]["cards"]
            if c["categories"] != ["Commander"]
        ][:9]
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "remove", "card_name": n} for n in names]},
        )
        d = r.json()
        assert r.status_code == 201 and d["needs_confirm"] is True and d["version"] == 1, r.text
        decks = stack.h.app.state.gateway.decks
        await decks.amend(
            "user-1",
            d["proposal_id"],
            [{"action": "remove", "card_name": "Aesi, Tyrant of Gyre Strait"}],
            version=1,
        )
        before = len(stack.ark.patches)
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": d["proposal_id"]})
        assert r.status_code == 400 and r.json()["error"] == "invalid", r.text
        assert r.json()["message"] == "Send the version of the proposal the page showed."
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": d["proposal_id"], "version": "1"})
        assert r.status_code == 400 and r.json()["message"] == "version must be a whole number", r.text
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": d["proposal_id"], "version": 1})
        assert r.status_code == 409 and r.json()["error"] == "changed", r.text
        assert len(stack.ark.patches) == before
        assert any(
            c["card"]["oracleCard"]["name"] == "Aesi, Tyrant of Gyre Strait"
            for c in stack.ark.decks[42]["cards"]
        )
    finally:
        await b.aclose()


async def test_edit_needs_the_browser_session_and_the_csrf_header(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        body = {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}]}
        r = await b.http.post("/api/v1/decks/42/edit", json=body)
        assert r.status_code == 403 and r.json()["error"] == "csrf"
        r = await api(b, "POST", "/api/v1/decks/42/edit", body, csrf="0" * 64)
        assert r.status_code == 403 and r.json()["error"] == "csrf"
        # an app's bearer token proposes through /api/v1/proposals instead
        token, _cid = await _app_token(stack.h, "deck-page-test")
        r = await stack.h.http.post(
            "/api/v1/decks/42/edit", json=body, headers={"Authorization": f"Bearer {token}"}
        )
        assert r.status_code == 403 and r.json()["error"] == "browser_required", r.text
        assert _deck_card(stack, "Cultivate")[0]["quantity"] == 1
        # the body is checked like a proposal's
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"changes": []})
        assert r.status_code == 400 and r.json()["error"] == "invalid"
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": 7})
        assert r.status_code == 400 and r.json()["error"] == "invalid"
        r = await api(b, "POST", "/api/v1/decks/42/edit", {})
        assert r.status_code == 400 and r.json()["error"] == "invalid"
    finally:
        await b.aclose()


async def test_someone_elses_deck_is_refused(stack: Stack) -> None:  # noqa: F811
    stack.ark.private.discard(43)  # amy's deck can be read, but alice does not own it
    b = await linked(stack)
    try:
        before = len(stack.ark.patches)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/43/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Acidic Slime", "quantity": 2}]},
        )
        assert r.status_code == 403 and r.json()["error"] == "forbidden", r.text
        assert len(stack.ark.patches) == before
    finally:
        await b.aclose()


async def test_a_lagging_re_read_is_read_again_and_marked_stale(
    stack: Stack,  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The page is answered from the apply's own verify read (0.7.46 audit D-01), which has seen
    the change (an apply whose verify read lags fails as verify_mismatch): no read of the
    endpoint's own, so a later read that lags cannot make the answer stale."""
    monkeypatch.setattr(api_module, "EDIT_REREAD_DELAY", 0.01)
    b = await linked(stack)
    try:
        decks = stack.h.app.state.gateway.decks
        real = decks.get_own_deck
        reads: list[str] = []

        async def lagging(sub: str, deck_id: str):
            deck = await real(sub, deck_id)
            reads.append(deck_id)
            # the propose and the check before the write read the deck; any read after them
            # would see the old count
            if len(reads) >= 3:
                for c in deck.cards:
                    if c.name == "Cultivate":
                        c.quantity = 1
            return deck

        monkeypatch.setattr(decks, "get_own_deck", lagging)
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True and d["stale"] is False, d
        assert d["rows"][0]["qty"] == 2 and len(reads) == 2, reads
        assert _deck_card(stack, "Cultivate")[0]["quantity"] == 2  # the write itself landed
    finally:
        await b.aclose()


async def test_an_assistants_pending_proposal_is_not_applied_by_the_page(stack: Stack) -> None:  # noqa: F811
    """0.7.9: the follow-up with a proposal_id answers only the question this endpoint
    asked. A proposal an app made (pending, with its Approve card and its backup copy) is refused
    here and stays pending; nothing reaches Archidekt."""
    b = await linked(stack)
    try:
        token, _cid = await _app_token(stack.h, "assistant-test")
        before = len(stack.ark.patches)
        r = await stack.h.http.post(
            "/api/v1/proposals",
            json={
                "kind": "edit",
                "deck_id": "42",
                "changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 3}],
            },
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 201, r.text
        pid = r.json()["proposal_id"]
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": pid})
        assert r.status_code == 400 and r.json()["error"] == "invalid", r.text
        assert "assistant" in r.json()["message"] and r.json()["review_url"] == f"/proposals/{pid}"
        assert len(stack.ark.patches) == before and _deck_card(stack, "Cultivate")[0]["quantity"] == 1
        r = await api(b, "GET", f"/api/v1/proposals/{pid}")
        assert r.status_code == 200 and r.json()["state"] == "pending", r.text
        # the page's own question, once answered, cannot be answered twice
        names = [
            c["card"]["oracleCard"]["name"]
            for c in stack.ark.decks[42]["cards"]
            if c["categories"] != ["Commander"]
        ][:9]
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "remove", "card_name": n} for n in names]},
        )
        assert r.status_code == 201 and r.json()["needs_confirm"] is True, r.text
        own = r.json()["proposal_id"]
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": own, "version": 1})
        assert r.status_code == 201 and r.json()["applied"] is True, r.text
        r = await api(b, "POST", "/api/v1/decks/42/edit", {"proposal_id": own, "version": 1})
        assert r.status_code == 400 and "applied" in r.json()["message"], r.text
    finally:
        await b.aclose()


async def test_a_one_click_edit_costs_four_archidekt_calls(stack: Stack) -> None:  # noqa: F811
    """0.7.46 audit D-01: a "+1" from the deck page cost six Archidekt calls (three reads before
    the write, the verify, then two reads for the page). Now: the proposal's read (reused by the
    apply), the check right before the write, the write and the verify, which the page draws from."""
    b = await linked(stack)
    try:
        csrf = await b.csrf("/account/details")  # the form token, read before the count starts
        stack.ark.calls.clear()
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}]},
            csrf=csrf,
        )
        assert r.status_code == 201 and r.json()["applied"] is True, r.text
        deck_calls = [(m, p) for m, p in stack.ark.calls if p.startswith("/api/decks/42/")]
        assert [m for m, _p in deck_calls] == ["GET", "GET", "PATCH", "GET"], deck_calls
        assert len(stack.ark.calls) <= 4, stack.ark.calls
        # the page is answered from the verify read: it shows the new count
        assert r.json()["rows"][0]["qty"] == 2 and r.json()["stale"] is False
    finally:
        await b.aclose()


async def test_a_read_too_old_to_reuse_is_made_again(stack: Stack, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(decks_mod, "ONE_CLICK_REUSE", 0.0)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/account/details")  # the form token, read before the count starts
        stack.ark.calls.clear()
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}]},
            csrf=csrf,
        )
        assert r.status_code == 201, r.text
        gets = [m for m, p in stack.ark.calls if p == "/api/decks/42/" and m == "GET"]
        assert len(gets) == 4  # propose, apply, check before the write, verify
    finally:
        await b.aclose()


async def test_a_save_that_breaks_the_rules_says_so_and_an_unpriced_deck_has_no_price(stack: Stack) -> None:  # noqa: F811
    """Independent review of 0.7.46: a Quick add of a second copy in a Commander deck said only
    "Added", while the same change as a proposal is high risk for breaking the singleton rule. The
    page's answer names the rules the save broke (``warnings``), and says how many cards have a
    price (``priced_cards``), so a deck with none shows "–", not "$0.00"."""
    _legal_commander_deck(stack)
    b = await linked(stack)
    try:
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "add", "card_name": "Cultivate", "quantity": 1}]},
        )
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["applied"] is True
        assert any(
            w.startswith("The deck now breaks its format's rules:")
            and "more than one copy" in w
            and "Cultivate" in w
            for w in d["warnings"]
        ), d.get("warnings")
        assert d["stats"]["priced_cards"] == 0 and d["stats"]["price_total"] in (0, 0.0, None)
        # a save that breaks nothing new carries no warning
        r = await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 1}]},
        )
        assert r.status_code == 201, r.text
        assert not r.json().get("warnings")
    finally:
        await b.aclose()


def test_deck_after_has_no_price_total_when_nothing_is_priced() -> None:
    from mtg_gateway.archidekt import Deck
    from mtg_gateway.decks import deck_after_block

    after = Deck(
        id="1", name="d", owner="o", updated_at="", cards=[], categories=[], raw={}, format="commander"
    )
    out = deck_after_block(
        {"price_total": 0.0, "priced_cards": 0}, {"price_total": 0.0, "priced_cards": 0}, after
    )
    assert out["price_total"] is None and out["price_change"] is None
    out = deck_after_block(
        {"price_total": 1.0, "priced_cards": 1}, {"price_total": 3.5, "priced_cards": 2}, after
    )
    assert out["price_total"] == 3.5 and out["price_change"] == 2.5
    # the only priced card removed: no total now, but the change is known
    out = deck_after_block(
        {"price_total": 1.0, "priced_cards": 1}, {"price_total": 0.0, "priced_cards": 0}, after
    )
    assert out["price_total"] is None and out["price_change"] == -1.0
