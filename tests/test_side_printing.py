"""A maybeboard or sideboard add keeps the printing it names (set code + collector number), as an
add to the deck proper does: that printing goes in as its own side row, or grows a side row of the
same printing and finish. Against the fake Archidekt only."""

from __future__ import annotations

from mtg_gateway.decks import parse_changes

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, structured


def _side_rows(ark, name: str) -> list[dict]:
    return [
        c
        for c in ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == name and "Maybeboard" in (c["categories"] or [])
    ]


def test_a_side_add_may_name_its_printing() -> None:
    (ch,) = parse_changes(
        [
            {
                "action": "add",
                "card_name": "Sol Ring",
                "zone": "side",
                "set_code": "sld",
                "collector_number": "1074",
            }
        ]
    )
    assert ch.zone == "side" and ch.pinned


def test_one_pinned_printing_per_card_in_each_zone() -> None:
    """The deck proper and the maybeboard each take a pinned add of the same card; a pinned side add
    next to a side removal of that card is still refused, a deck-proper removal is not."""
    import pytest

    from mtg_gateway.decks import DeckError

    pin = {"action": "add", "card_name": "Sol Ring", "set_code": "sld", "collector_number": "1074"}
    assert len(parse_changes([pin, {**pin, "zone": "side"}])) == 2
    with pytest.raises(DeckError, match="one pinned printing"):
        parse_changes([{**pin, "zone": "side"}, {**pin, "zone": "side", "collector_number": "1075"}])
    with pytest.raises(DeckError, match="also removed or set"):
        parse_changes(
            [{**pin, "zone": "side"}, {"action": "remove", "card_name": "Sol Ring", "zone": "side"}]
        )
    assert len(parse_changes([{**pin, "zone": "side"}, {"action": "remove", "card_name": "Sol Ring"}])) == 2


async def test_a_pinned_side_add_goes_in_as_that_printing(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    change = {
        "action": "add",
        "card_name": "Sol Ring",
        "zone": "side",
        "set_code": "SLD",
        "collector_number": "1074",
        "finish": "etched",
    }
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    assert p["ok"], p
    assert "SLD 1074, Etched" in p["diff"], p["diff"]
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"], a
    (sent,) = (pt["cards"][0] for pt in ark.patches)
    assert sent["action"] == "add" and sent["cardid"] == 124026, sent
    assert sent["categories"] == ["Maybeboard"]
    assert sent["modifications"]["modifier"] == "Etched" and sent["modifications"]["quantity"] == 1
    (row,) = _side_rows(ark, "Sol Ring")
    assert (row["card"]["id"], row["modifier"], row["quantity"]) == (124026, "Etched", 1)


async def test_a_pinned_side_add_grows_the_side_row_of_that_printing(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    ark.add_side_row(42, "Sol Ring")
    (row,) = _side_rows(ark, "Sol Ring")
    row["card"] = ark.printing(91043)  # the maybeboard already holds CMR 472
    token = await linked_user(stack)
    change = {
        "action": "add",
        "card_name": "sol ring",
        "zone": "side",
        "set_code": "cmr",
        "collector_number": "472",
    }
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    assert p["ok"], p
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"], a
    (sent,) = (pt["cards"][0] for pt in ark.patches)
    assert sent["action"] == "modify" and sent["deckRelationId"] == row["id"], sent
    assert sent["modifications"]["quantity"] == 2


async def test_a_pinned_side_add_beside_another_side_printing_is_its_own_row(stack: Stack) -> None:
    """The maybeboard's own Sol Ring keeps its printing; the picked one goes in next to it."""
    h, ark = stack.h, stack.ark
    ark.add_side_row(42, "Sol Ring")
    (old,) = _side_rows(ark, "Sol Ring")
    old_id = old["card"]["id"]
    assert old_id != 124026
    token = await linked_user(stack)
    change = {
        "action": "add",
        "card_name": "Sol Ring",
        "zone": "side",
        "set_code": "sld",
        "collector_number": "1074",
    }
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"], a
    rows = sorted((r["card"]["id"], r["quantity"]) for r in _side_rows(ark, "Sol Ring"))
    assert rows == sorted([(old_id, 1), (124026, 1)]), rows


async def test_a_pinned_maybeboard_add_never_grows_the_sideboard_row(stack: Stack) -> None:
    """The same printing under Sideboard stays as it is: the Maybeboard add is its own row, so the
    sideboard's count (which the format checks read) does not rise."""
    h, ark = stack.h, stack.ark
    ark.add_side_row(42, "Sol Ring", category="Sideboard")
    side = next(c for c in ark.decks[42]["cards"] if "Sideboard" in (c["categories"] or []))
    side["card"] = ark.printing(124026)
    side["modifier"] = "Etched"
    ark.decks[42]["categories"].append({"name": "Maybeboard", "isPremier": False, "includedInDeck": False})
    token = await linked_user(stack)
    change = {
        "action": "add",
        "card_name": "Sol Ring",
        "zone": "side",
        "category": "Maybeboard",
        "set_code": "sld",
        "collector_number": "1074",
    }
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    assert p["ok"], p
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"], a
    (sent,) = (pt["cards"][0] for pt in ark.patches)
    assert sent["action"] == "add" and sent["categories"] == ["Maybeboard"], sent
    assert side["quantity"] == 1


async def test_a_finish_on_a_side_add_stays_on_the_side_row(stack: Stack) -> None:
    """A foil maybeboard add and a plain deck add of the same new card: only the maybeboard row is
    foil (the finishes were looked up under one name for both zones, so the deck row came out foil
    too)."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    changes = [
        {"action": "add", "card_name": "Swamp", "zone": "side", "foil": True},
        {"action": "add", "card_name": "Swamp"},
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert p["ok"], p
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"], a
    sent = [pt["cards"][0] for pt in ark.patches]
    by_zone = {
        ("side" if "Maybeboard" in e["categories"] else "main"): e["modifications"]["modifier"] for e in sent
    }
    assert by_zone == {"side": "Foil", "main": "Normal"}, sent


async def test_a_pinned_side_add_typed_in_lower_case_grows_its_category_row(stack: Stack) -> None:
    """A category typed "sideboard" is the deck's Sideboard: the add grows that row of the printing
    instead of making a second one beside it."""
    h, ark = stack.h, stack.ark
    ark.add_side_row(42, "Sol Ring", category="Sideboard")
    side = next(c for c in ark.decks[42]["cards"] if "Sideboard" in (c["categories"] or []))
    side["card"] = ark.printing(124026)
    side["modifier"] = "Etched"
    token = await linked_user(stack)
    change = {
        "action": "add",
        "card_name": "Sol Ring",
        "zone": "side",
        "category": "sideboard",
        "set_code": "sld",
        "collector_number": "1074",
    }
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    assert p["ok"], p
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"], a
    (sent,) = (pt["cards"][0] for pt in ark.patches)
    assert sent["action"] == "modify" and sent["deckRelationId"] == side["id"], sent
