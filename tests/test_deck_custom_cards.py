"""The member's own custom cards in a deck (kind "custom_cards", 2026-10-23).

Archidekt's deck JSON carries a deck's custom cards in ``customCards`` beside ``cards``, and its
deck page adds one with PATCH modifyCards, ``customCardId`` in place of ``cardid`` (read from its
site scripts; not tried live yet). The gateway keeps them out of ``Deck.cards`` and the deck's
fingerprint, adds only the member's own custom cards, and reads the deck back after every write."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import pytest

from mtg_gateway import modes
from mtg_gateway.archidekt import parse_deck
from mtg_gateway.decks import BROWSER_CLIENT, DeckError, current_client, custom_card_line

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED, _set_mode
from .test_decks_and_proxy import call, linked_user, structured

stack_options = stack_with(SHIPPED)

SUB = "user-1"  # the signed-in member of linked_user


def _custom(cid: int, name: str, owner: int = 77, **more) -> dict:
    return {
        "id": cid,
        "owner": owner,
        "frontName": name,
        "frontTypes": "Creature",
        "frontSubTypes": "Human Advisor",
        "frontImageUrl": f"https://cdn.example/{cid}.png",
        "hasBack": False,
        "setCode": "CST",
        "collectorNumber": "001",
        **more,
    }


def _seed(ark) -> None:  # noqa: ANN001
    ark.account.customs["alice"] = [_custom(41, "Grizzled Mentor"), _custom(44, "Old Ranger")]
    ark.account.customs["amy"] = [_custom(51, "Amy's Secret", owner=78)]


def _rows(ark, deck_id: int = 42) -> dict[int, dict]:  # noqa: ANN001
    return {r["card"]["id"]: r for r in ark.decks[deck_id].get("customCards") or []}


def _svc(stack: Stack):  # noqa: ANN202
    return stack.h.app.state.gateway.decks


async def _propose(h, token: str, changes: list[dict], deck_id: str = "42") -> dict:  # noqa: ANN001
    return structured(
        await call(h, token, "propose_custom_card_changes", {"deck_id": deck_id, "changes": changes})
    )


async def _apply(h, token: str, p: dict) -> dict:  # noqa: ANN001
    return structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))


# -- reading ----------------------------------------------------------------------------------------
def test_custom_rows_are_read_apart_from_the_cards() -> None:
    body = {
        "id": 9,
        "name": "D",
        "cards": [],
        "updatedAt": "x",
        "customCards": [
            {
                "id": 3001,
                "quantity": 2,
                "modifier": "Normal",
                "label": "Tech,#00ff00",
                "categories": ["Ramp"],
                "companion": False,
                "card": _custom(41, "Grizzled Mentor", frontSuperTypes="Legendary"),
            },
            {
                "id": 3002,
                "quantity": 1,
                "categories": None,
                "card": _custom(44, "Day", hasBack=True, backName="Night"),
            },
            "junk",
            {"id": 3003, "quantity": "two", "card": _custom(45, "Bad count")},
            {"id": 3004, "quantity": 1},  # no card object
        ],
    }
    deck = parse_deck(body)
    assert deck.cards == []
    assert [(c.relation_id, c.custom_card_id, c.name, c.quantity) for c in deck.custom_cards] == [
        (3001, 41, "Grizzled Mentor", 2),
        (3002, 44, "Day // Night", 1),
    ]
    first = deck.custom_cards[0]
    assert first.categories == ["Ramp"] and first.label == "Tech,#00ff00"
    assert first.type_line == "Legendary Creature — Human Advisor"
    assert first.image_url == "https://cdn.example/41.png"
    assert deck.custom_cards[1].categories == []
    # custom rows never move the fingerprint: no card edit, snapshot or restore sees them
    assert deck.fingerprint() == parse_deck({**body, "customCards": []}).fingerprint()
    # an odd customCards value never breaks the deck read
    assert parse_deck({**body, "customCards": {"x": 1}}).custom_cards == []


# -- risk ----------------------------------------------------------------------------------------
def test_tiers() -> None:
    row = {"kind": "custom_card", "action": "add", "name": "X", "before": 0, "after": 2, "category": ""}
    assert modes.risk_of("custom_cards", [row])[0] == "low"
    assert modes.risk_of("custom_cards", [row] * 6)[0] == "high"  # more than the low-risk row cap
    assert modes.risk_of("custom_cards", [{**row, "after": 5}])[0] == "high"  # more than a playset
    zone = {**row, "commander_zone": True}
    assert modes.risk_of("custom_cards", [zone]) == ("high", modes.COMMANDER_ZONE_REASON)
    assert modes.auto_waits("custom_cards", [zone]) == modes.COMMANDER_ZONE_REASON
    assert modes.auto_waits("custom_cards", [row]) is None
    assert modes.risk_of("custom_cards", None)[0] == "high"
    assert modes.hand_edit_confirm("custom_cards", [row]) is None
    gone = {**row, "action": "remove", "before": 12, "after": 0}
    assert modes.hand_edit_confirm("custom_cards", [gone]) == "This removes 12 cards from the deck."


def test_review_lines() -> None:
    row = {"kind": "custom_card", "action": "add", "name": "Mentor", "before": 0, "after": 2, "category": ""}
    assert custom_card_line(row) == "Add custom card “Mentor”: 0 -> 2, no category given (Archidekt files it)"
    assert (
        custom_card_line({**row, "category": "Ramp"}) == "Add custom card “Mentor”: 0 -> 2, category “Ramp”"
    )
    moved = {**row, "action": "set_quantity", "before": 2, "after": 3, "category_before": "Ramp"}
    assert custom_card_line({**moved, "category": "Draw"}) == (
        "Custom card “Mentor”: 2 -> 3, category “Ramp” -> “Draw”"
    )
    assert custom_card_line({**moved, "category": "Ramp"}) == "Custom card “Mentor”: 2 -> 3, category “Ramp”"
    gone = {**moved, "action": "remove", "after": 0}
    assert custom_card_line(gone) == "Remove custom card “Mentor”: 2 -> 0, from “Ramp”"


# -- propose and apply ---------------------------------------------------------------------------
async def test_add_your_own_custom_card_then_count_then_remove(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    _seed(ark)
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    p = await _propose(h, token, [{"action": "add", "custom_card_id": 41, "quantity": 2, "category": "Tech"}])
    assert p["ok"] and p["kind"] == "custom_cards" and p["risk"] == "low" and p["assistant_may_apply"], p
    assert p["diff"] == "Add custom card “Grizzled Mentor”: 0 -> 2, category “Tech”"
    assert not _rows(ark)  # a proposal changes nothing by itself
    out = await _apply(h, token, p)
    assert out["state"] == "applied" and out["result"]["verified"] is True, out
    assert out["result"]["snapshot_id"]
    sent = ark.patches[-1]["cards"][0]
    assert sent["action"] == "add" and sent["customCardId"] == 41 and "cardid" not in sent, sent
    assert sent["categories"] == ["Tech"] and sent["modifications"]["quantity"] == 2
    row = _rows(ark)[41]
    assert row["quantity"] == 2 and row["categories"] == ["Tech"]

    # get_deck lists it apart from the cards, by its custom card id
    deck = structured(await call(h, token, "get_deck", {"deck_ref": "42", "view": "summary"}))
    assert deck["custom_cards"] == [
        {"id": 41, "name": "Grizzled Mentor", "quantity": 2, "categories": ["Tech"]}
    ]

    p = await _propose(h, token, [{"action": "set_quantity", "custom_card_id": 41, "quantity": 3}])
    assert p["diff"] == "Custom card “Grizzled Mentor”: 2 -> 3, category “Tech”", p
    out = await _apply(h, token, p)
    assert out["state"] == "applied", out
    sent = ark.patches[-1]["cards"][0]
    assert sent["action"] == "modify" and sent["deckRelationId"] == row["id"] and sent["customCardId"] == 41
    assert sent["categories"] == ["Tech"] and _rows(ark)[41]["quantity"] == 3

    p = await _propose(h, token, [{"action": "remove", "custom_card_id": 41}])
    assert p["diff"] == "Remove custom card “Grizzled Mentor”: 3 -> 0, from “Tech”", p
    assert (await _apply(h, token, p))["state"] == "applied"
    assert ark.patches[-1]["cards"][0]["action"] == "remove"
    assert 41 not in _rows(ark)


async def test_an_add_without_a_category_sends_none(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    _seed(ark)
    token = await linked_user(stack)
    p = await _propose(h, token, [{"action": "add", "custom_card_id": 44}])
    assert p["ok"] and "no category given" in p["diff"], p
    current_client.set(BROWSER_CLIENT)  # the member's own press on a page
    out = await _svc(stack).apply(SUB, p["proposal_id"], via="browser")
    assert out["state"] == "applied", out
    assert ark.patches[-1]["cards"][0]["categories"] == []
    assert _rows(ark)[44]["quantity"] == 1


async def test_someone_elses_custom_card_is_refused(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    _seed(ark)
    token = await linked_user(stack)
    p = await _propose(h, token, [{"action": "add", "custom_card_id": 51}])
    assert p["ok"] is False and "not one of your custom cards" in p["message"], p
    p = await _propose(h, token, [{"action": "remove", "custom_card_id": 41}])
    assert p["ok"] is False and "isn't in this deck" in p["message"], p
    # someone else's deck is not the member's to change
    p = await _propose(h, token, [{"action": "add", "custom_card_id": 41}], deck_id="43")
    assert p["ok"] is False, p
    assert not ark.patches


async def test_many_copies_or_the_commander_zone_wait(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    _seed(ark)
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    p = await _propose(h, token, [{"action": "add", "custom_card_id": 41, "quantity": 7}])
    assert p["risk"] == "high" and p["assistant_may_apply"] is False, p
    p = await _propose(h, token, [{"action": "add", "custom_card_id": 44, "category": "Commander"}])
    assert p["risk"] == "high" and p["assistant_may_apply"] is False, p


async def test_an_add_that_does_not_land_is_caught(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    _seed(ark)
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    ark.custom_adds_dropped = True
    p = await _propose(h, token, [{"action": "add", "custom_card_id": 41, "quantity": 2}])
    out = await _apply(h, token, p)
    assert out["ok"] is False and out["error"] == "verify_mismatch", out
    assert "“Grizzled Mentor” (0 in the deck, not 2)" in out["message"], out


async def test_a_custom_row_changed_since_needs_a_look(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    _seed(ark)
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    p = await _propose(h, token, [{"action": "add", "custom_card_id": 41}])
    # meanwhile the card was added on Archidekt: the add would now do something else
    ark.decks[42]["customCards"] = [
        {"id": 3100, "quantity": 1, "categories": None, "card": _custom(41, "Grizzled Mentor")}
    ]
    out = await _apply(h, token, p)
    assert out["ok"] is False and out["error"] == "stale", out
    assert not ark.patches


async def test_bad_changes_are_refused_before_anything_is_stored(stack: Stack) -> None:
    _seed(stack.ark)
    await linked_user(stack)
    svc = _svc(stack)
    for bad in (
        [],
        [{"action": "move", "custom_card_id": 41}],
        [{"action": "add", "custom_card_id": "x"}],
        [{"action": "add", "custom_card_id": 41, "quantity": 0}],
        [{"action": "set_quantity", "custom_card_id": 41}],
        [{"action": "add", "custom_card_id": 41}, {"action": "add", "custom_card_id": 41}],
    ):
        with pytest.raises(DeckError):
            await svc.propose_custom_cards(SUB, "42", bad)
