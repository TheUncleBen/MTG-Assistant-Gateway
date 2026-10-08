from pathlib import Path

import pytest

from mtg_gateway.archidekt import parse_deck
from mtg_gateway.archidekt_csv import CsvError, parse_export, to_deck_json

FIXTURE = Path(__file__).parent / "fixtures" / "sample_deck.csv"


def test_real_export_parses_to_100_cards() -> None:
    cards = parse_export(FIXTURE.read_text(encoding="utf-8"))
    assert sum(c.quantity for c in cards) == 100
    assert len(cards) == 72
    by = {c.name: c for c in cards}
    assert by["Forest"].quantity == 15 and by["Island"].quantity == 15
    assert by["Aesi, Tyrant of Gyre Strait"].category == "Commander"
    assert by["Aesi, Tyrant of Gyre Strait"].mana_cost == "4GU"
    assert by["Acidic Slime"].mana_cost == "3GG" and by["Acidic Slime"].card_text.endswith("or land.")
    assert by["Murkfiend Liege"].mana_cost == "2G,UG,UG,U"
    assert by["Spitting Image"].mana_cost == "4G,UG,U"
    assert by["Forest"].mana_cost == ""
    assert by["Acidic Slime"].owned is False
    assert by["Acidic Slime"].price == 0.35
    assert by["Acidic Slime"].set_code == "cmr" and by["Acidic Slime"].collector_number == "421"
    assert by["Murkfiend Liege"].colors == ["Green", "Blue"]
    # every nonland has a cost; nothing was mis-split
    assert not [c.name for c in cards if (c.mana_value or 0) > 0 and not c.mana_cost]


def test_missing_prices_and_joined_header() -> None:
    text = (
        "Quantity,Name,Edition dateCategory,Price,Card text,Mana cost\n"
        '2,Sol Ring,1/1/2020,Ramp,----,"{T}: Add {C}{C}."1\n'
    )
    (card,) = parse_export(text)
    assert card.quantity == 2 and card.category == "Ramp" and card.price is None
    assert card.mana_cost == "1" and card.card_text == "{T}: Add {C}{C}."


def test_clean_export_with_separate_cost_column() -> None:
    text = 'Quantity,Name,Card text,Mana cost\n1,Counterspell,"Counter target spell.",UU\n'
    (card,) = parse_export(text)
    assert card.mana_cost == "UU" and card.card_text == "Counter target spell."


def test_rejects_non_export() -> None:
    with pytest.raises(CsvError):
        parse_export("hello,world\n1,2\n")
    with pytest.raises(CsvError):
        parse_export("Quantity,Name\n")


def test_fixture_round_trips_into_api_shape() -> None:
    cards = parse_export(FIXTURE.read_text(encoding="utf-8"))
    deck = parse_deck(to_deck_json(cards, deck_id=42, name="Sample Commander Deck"))
    assert deck.id == "42" and sum(c.quantity for c in deck.cards) == 100
    assert deck.counts_by_name()["Forest"] == 15
    again = parse_deck(to_deck_json(cards, deck_id=42, name="Sample Commander Deck"))
    assert deck.fingerprint() == again.fingerprint()


LIVE = Path(__file__).parent / "fixtures" / "live" / "archidekt_deck_sample.json"


def test_live_deck_with_null_categories_parses() -> None:
    import json

    deck = parse_deck(json.loads(LIVE.read_text(encoding="utf-8")))
    assert deck.id == "90000001" and sum(c.quantity for c in deck.cards) == 100
    assert sum(1 for c in deck.cards if c.categories == []) == 71
    assert [c.name for c in deck.cards if c.categories] == ["Aesi, Tyrant of Gyre Strait"]
    assert {c.modifier for c in deck.cards} == {"Normal", "Foil"}
    assert [c["name"] for c in deck.categories] == ["Commander"]
    assert deck.fingerprint() == parse_deck(json.loads(LIVE.read_text(encoding="utf-8"))).fingerprint()


def test_csv_fixture_matches_live_shape() -> None:
    import json

    live = parse_deck(json.loads(LIVE.read_text(encoding="utf-8")))
    cards = parse_export(FIXTURE.read_text(encoding="utf-8"))
    built = parse_deck(to_deck_json(cards, deck_id=90000001))

    def key(d):  # noqa: ANN001
        return sorted(
            (c.name, c.quantity, c.set_code, c.collector_number, c.modifier, tuple(c.categories))
            for c in d.cards
        )

    assert key(built) == key(live)


def _deck_with_maybeboard() -> dict:
    def entry(rid: int, name: str, qty: int, cats: list[str] | None) -> dict:
        return {
            "id": rid,
            "quantity": qty,
            "categories": cats,
            "modifier": "Normal",
            "card": {
                "id": rid + 500,
                "collectorNumber": "1",
                "edition": {"editioncode": "cmr"},
                "oracleCard": {"name": name},
            },
        }

    return {
        "id": 7,
        "name": "Side test",
        "owner": {"username": "alice"},
        "updatedAt": "x",
        "categories": [
            {"name": "Commander", "isPremier": True, "includedInDeck": True},
            {"name": "Maybeboard", "isPremier": False, "includedInDeck": False},
            {"name": "Sideboard", "isPremier": False, "includedInDeck": False},
            {"name": "Ramp", "isPremier": False, "includedInDeck": True},
        ],
        "cards": [
            entry(1, "Aesi, Tyrant of Gyre Strait", 1, ["Commander"]),
            entry(2, "Forest", 10, None),
            entry(3, "Sol Ring", 1, ["Ramp"]),
            entry(4, "Sol Ring", 1, ["Maybeboard"]),
            entry(5, "Negate", 2, ["Sideboard"]),
            entry(6, "Cultivate", 1, ["Ramp", "Maybeboard"]),  # first category Ramp: in the deck
            entry(7, "Harmonize", 1, ["Maybeboard", "Ramp"]),  # first category Maybeboard: out
        ],
    }


def test_maybeboard_and_sideboard_are_not_counted() -> None:
    from mtg_gateway.decks import Change, build_payload, deck_to_text, plan

    deck = parse_deck(_deck_with_maybeboard())
    # A row's first category decides (Archidekt's primary category): Cultivate is in, Harmonize out.
    assert sum(c.quantity for c in deck.main_cards) == 13
    assert sum(c.quantity for c in deck.side_cards) == 4
    assert deck.counts_by_name() == {
        "Aesi, Tyrant of Gyre Strait": 1,
        "Forest": 10,
        "Sol Ring": 1,
        "Cultivate": 1,
    }
    # a remove hits the mainboard copy, never the maybeboard one
    _before, after, lines = plan(deck, [Change("remove", "Sol Ring")])
    assert lines == ["-1 Sol Ring"]
    payload = build_payload(deck, after, {})
    assert [e["deckRelationId"] for e in payload] == [3]
    text = deck_to_text(deck)
    assert text.splitlines() == [
        "1 Aesi, Tyrant of Gyre Strait (cmr) 1 [Commander]",
        "10 Forest (cmr) 1",
        "1 Sol Ring (cmr) 1 [Ramp]",
        "1 Cultivate (cmr) 1 [Ramp,Maybeboard]",
    ]
    assert deck_to_text(deck, zone="side").splitlines() == [
        "1 Sol Ring (cmr) 1 [Maybeboard]",
        "2 Negate (cmr) 1 [Sideboard]",
        "1 Harmonize (cmr) 1 [Maybeboard,Ramp]",
    ]


def test_wide_or_malformed_csv_is_refused_quickly():
    import time

    from mtg_gateway.archidekt_csv import CsvError, parse_export

    wide = "Quantity,Name" + "," * 1_000_000 + "\n" + "1,a\n" * 200_000
    start = time.monotonic()
    with pytest.raises(CsvError):
        parse_export(wide)
    assert time.monotonic() - start < 5
    # Under the row cap, so the column cap is what refuses it.
    with pytest.raises(CsvError, match="columns"):
        parse_export("Quantity,Name" + "," * 200_000 + "\n" + "1,a\n" * 4_000)
    with pytest.raises(CsvError):
        parse_export('Quantity,Name\n1,"' + "x" * 200_000 + '"\n')  # over csv's field limit
