"""Printing-aware card lookup and finish modifiers (client functions only)."""

from __future__ import annotations

import uuid

import pytest

from mtg_gateway.archidekt import ArchidektClient, ArchidektError, Pacer, finish_modifier
from tests.fake_archidekt import FakeArchidekt


async def _client() -> tuple[FakeArchidekt, ArchidektClient, str]:
    ark = FakeArchidekt()
    client = ArchidektClient("https://archidekt.com/api", "test", Pacer(0.0), http=ark.client())
    session = await client.login("alice", "pw-alice")
    return ark, client, session["access"]


async def test_set_and_number_pin_the_printing() -> None:
    _, client, token = await _client()
    card = await client.resolve_card(token, "Swamp", set_code="M21", collector_number="267")
    assert card == {
        "id": 87176,
        "name": "Swamp",
        "set_code": "m21",
        "collector_number": "267",
        "options": ["Normal", "Foil"],
        "exact_printing": True,
        "oracle_id": None,  # the fake's printing search leaves the oracle card's id out
    }


async def test_scryfall_id_pins_the_printing() -> None:
    _, client, token = await _client()
    card = await client.resolve_card(token, "Sol Ring", scryfall_id="58b26011-e103-45c4-a253-900f4e6b2eeb")
    assert card["id"] == 91043 and card["exact_printing"] is True


async def test_scryfall_id_of_another_card_is_ignored() -> None:
    _, client, token = await _client()
    card = await client.resolve_card(token, "Swamp", scryfall_id="58b26011-e103-45c4-a253-900f4e6b2eeb")
    assert card["id"] == 9008 and card["exact_printing"] is False  # name-only fallback


async def test_unknown_number_keeps_the_set() -> None:
    _, client, token = await _client()
    card = await client.resolve_card(token, "Swamp", set_code="m21", collector_number="999")
    assert card["id"] == 87176 and card["exact_printing"] is False


async def test_unknown_printing_falls_back_to_the_name() -> None:
    ark, client, token = await _client()
    card = await client.resolve_card(token, "Opt", set_code="zzz", collector_number="1")
    assert card["id"] == 9007 and card["exact_printing"] is False


async def test_bad_set_code_is_not_sent() -> None:
    ark, client, token = await _client()
    await client.resolve_card(token, "Opt", set_code="m21&x=1")
    assert [c for c in ark.calls if c[1] == "/api/cards/v2/"] == [("GET", "/api/cards/v2/")]  # name-only


async def test_name_only_skips_a_printing_still_in_preview() -> None:
    """Archidekt lists the newest set first, and a set still being previewed has a release date
    to come: a card named without a printing gets the newest released one instead."""
    ark, client, token = await _client()
    ark.newer_printings["island"] = [
        {
            "id": 99001,
            "oracleCard": {"name": "Island"},
            "edition": {"editioncode": "trk", "editiondate": "2999-01-01"},
            "collectorNumber": "319",
            "releasedAt": "2999-01-01",
        },
        {
            "id": 99002,
            "oracleCard": {"name": "Island"},
            "edition": {"editioncode": "fdn", "editiondate": "2024-11-15"},
            "collectorNumber": "279",
            "releasedAt": "2024-11-15",
        },
    ]
    card = await client.resolve_card(token, "Island")
    assert card["id"] == 99002 and card["set_code"] == "fdn", card
    # a card with only a preview printing is still found, not refused
    ark.newer_printings["wastes"] = [{**ark.newer_printings["island"][0], "oracleCard": {"name": "Wastes"}}]
    card = await client.resolve_card(token, "Wastes")
    assert card["id"] == 99001, card


def test_finish_modifier() -> None:
    assert finish_modifier(["Normal", "Foil"], foil=True) == "Foil"
    assert finish_modifier(["Normal", "Foil"]) == "Normal"
    assert finish_modifier(["Etched"], etched=True) == "Etched"
    assert finish_modifier(["Normal"], foil=True) == "Normal"  # no foil of this printing
    assert finish_modifier(["Etched"]) == "Etched"  # etched-only printing
    assert finish_modifier([], foil=True) == "Foil"


async def test_foil_printing_lands_in_the_deck() -> None:
    ark, client, token = await _client()
    card = await client.resolve_card(token, "Swamp", set_code="m21", collector_number="267")
    entry = {
        "action": "add",
        "cardid": card["id"],
        "patchId": uuid.uuid4().hex,
        "categories": ["Land"],
        "modifications": {"quantity": 1, "modifier": finish_modifier(card["options"], foil=True)},
    }
    await client.modify_card(token, "42", entry)
    deck = await client.get_deck(token, "42")
    swamp = next(c for c in deck.cards if c.card_id == 87176)
    assert (swamp.set_code, swamp.collector_number, swamp.modifier) == ("m21", "267", "Foil")


async def test_hand_picked_printing_must_exist_and_match_the_card() -> None:
    _, client, token = await _client()
    card = await client.resolve_card(
        token, "Swamp", set_code="m21", collector_number="267", require_printing=True
    )
    assert card["id"] == 87176
    with pytest.raises(ArchidektError) as other:  # a real printing, but of another card
        await client.resolve_card(
            token, "Island", set_code="m21", collector_number="267", require_printing=True
        )
    assert other.value.kind == "not_found" and "M21 #267 is Swamp, not 'Island'" in str(other.value)
    with pytest.raises(ArchidektError) as missing:  # no such printing: no name-only fallback
        await client.resolve_card(
            token, "Swamp", set_code="m21", collector_number="999", require_printing=True
        )
    assert "no printing M21 #999 of 'Swamp'" in str(missing.value)
    with pytest.raises(ArchidektError):
        await client.resolve_card(token, "Swamp", set_code="m21", require_printing=True)
