"""A change to a deck row sends the row's own companion and "flipped by default" flags, as
Archidekt's site does, so a category, quantity or finish change cannot clear them."""

from mtg_gateway.archidekt import DeckCard
from mtg_gateway.decks import _entry


def _card(**kw):
    base = dict(
        relation_id=7,
        card_id=11,
        name="Lurrus of the Dream-Den",
        quantity=1,
        categories=["Companion"],
        modifier="Normal",
        set_code="iko",
        collector_number="226",
    )
    base.update(kw)
    return DeckCard(**base)


def test_a_category_change_keeps_the_companion_and_flip_flags():
    entry = _entry("modify", _card(companion=True, flipped_default=True), 1, ["Sideboard"])
    assert entry["categories"] == ["Sideboard"]
    assert entry["modifications"]["companion"] is True
    assert entry["modifications"]["flippedDefault"] is True


def test_a_plain_row_still_sends_false():
    entry = _entry("modify", _card(), 2)
    assert entry["modifications"]["companion"] is False
    assert entry["modifications"]["flippedDefault"] is False


def test_the_deck_reader_takes_flipped_default():
    from mtg_gateway import archidekt

    assert "flipped_default" in {f.name for f in archidekt.DeckCard.__dataclass_fields__.values()}


def test_a_new_commander_keeps_its_other_categories_commander_first():
    """Set as commander files the card under Commander as its primary category and keeps its
    other categories, as archidekt.com's "Set Commander as primary" does."""
    from mtg_gateway.archidekt import parse_deck
    from mtg_gateway.decks import category_plan_rows, parse_changes

    from .fake_archidekt import FakeArchidekt

    ark = FakeArchidekt()
    raw = ark._deck_for(ark.decks[42], None)
    row = next(e for e in raw["cards"] if e["card"]["oracleCard"]["name"] == "Acidic Slime")
    row["categories"] = ["Ramp", "Value"]
    deck = parse_deck(raw)
    card = next(c for c in deck.main_cards if c.name == "Acidic Slime")
    assert card.categories == ["Ramp", "Value"]
    want, _rows = category_plan_rows(
        deck, parse_changes([{"action": "set_commander", "card_name": card.name}])
    )
    assert want[card.relation_id] == ["Commander"] + [c for c in card.categories if c != "Commander"]
    old = [c for c in deck.main_cards if "Commander" in c.categories]
    for c in old:  # the old commander loses only Commander
        assert want[c.relation_id] == [x for x in c.categories if x != "Commander"]


def test_a_printing_change_keeps_the_flip_and_companion_flags():
    """A printing change is a remove plus an add: the add carries the old row's "flipped by
    default" and companion flags, so a double-faced card set to show its back keeps that."""
    import asyncio

    from mtg_gateway.archidekt import Deck
    from mtg_gateway.decks import DeckService, parse_changes, printing_plan_rows

    valki = _card(
        name="Valki, God of Lies // Tibalt, Cosmic Impostor",
        categories=["Creature"],
        set_code="khm",
        collector_number="114",
        flipped_default=True,
    )
    lurrus = _card(relation_id=8, card_id=12, companion=True)
    deck = Deck(id="1", name="t", cards=[valki, lurrus], owner="u", updated_at="", categories=[], raw={})
    changes = parse_changes(
        [
            {
                "action": "set_printing",
                "card_name": "Valki, God of Lies",
                "set_code": "khm",
                "collector_number": "999",
            },
            {
                "action": "set_printing",
                "card_name": "Lurrus of the Dream-Den",
                "set_code": "plst",
                "collector_number": "1",
            },
        ]
    )
    specs, _rows = printing_plan_rows(deck, changes)

    class Svc:
        async def _call(self, sub, fn):
            return {"id": 99, "options": ["Normal", "Foil"]}

    out = asyncio.run(DeckService._printing_entries(Svc(), "u", deck, specs))
    adds = [e for e in out if e["action"] == "add"]
    assert [(e["modifications"]["flippedDefault"], e["modifications"]["companion"]) for e in adds] == [
        (True, False),
        (False, True),
    ]


def test_a_restore_puts_the_flags_back():
    from mtg_gateway.archidekt import Deck
    from mtg_gateway.decks import restore_rows

    snap = Deck(
        id="1",
        name="t",
        cards=[_card(flipped_default=True, companion=True)],
        owner="u",
        updated_at="",
        categories=[],
        raw={},
    )
    now = Deck(id="1", name="t", cards=[], owner="u", updated_at="", categories=[], raw={})
    payload, _rows = restore_rows(snap, now)
    assert [
        (e["action"], e["modifications"]["flippedDefault"], e["modifications"]["companion"]) for e in payload
    ] == [("add", True, True)]


def test_a_card_with_commander_as_a_later_category_is_put_commander_first():
    """A card that already has Commander, but not first, is rewritten with
    Commander first, its other categories kept; the review shows it and the proposal is not
    refused as leaving the deck as it is. A card with Commander first already is left alone."""
    from mtg_gateway.archidekt import parse_deck
    from mtg_gateway.decks import category_plan_rows, parse_changes, plan_rows

    from .fake_archidekt import FakeArchidekt

    ark = FakeArchidekt()
    raw = ark._deck_for(ark.decks[42], None)
    row = next(e for e in raw["cards"] if e["card"]["oracleCard"]["name"] == "Acidic Slime")
    row["categories"] = ["Ramp", "Commander"]
    deck = parse_deck(raw)
    card = next(c for c in deck.main_cards if c.name == "Acidic Slime")
    changes = parse_changes([{"action": "set_commander", "card_name": card.name}])
    want, rows = category_plan_rows(deck, changes)
    assert want[card.relation_id] == ["Commander", "Ramp"]
    line = next(r for r in rows if r.get("name") == "Acidic Slime")
    assert (line["kind"], line["before"], line["after"]) == ("category", "Ramp, Commander", "Commander, Ramp")
    plan_rows(deck, changes)  # not refused as "these changes would leave the deck exactly as it is"
    row["categories"] = ["Commander", "Ramp"]
    deck = parse_deck(raw)
    want, _rows = category_plan_rows(deck, changes)
    assert card.relation_id not in want


def test_the_apply_check_sees_a_primary_category_archidekt_did_not_put_first() -> None:
    """Set as commander asks for Commander first: a re-read with the same categories in
    another order, so a different primary, is a mismatch; secondaries in another order are not."""
    from mtg_gateway.archidekt import Deck
    from mtg_gateway.decks import _category_mismatches

    def deck(cats: list[str]) -> Deck:
        return Deck(
            id="1", name="t", cards=[_card(categories=cats)], owner="u", updated_at="", categories=[], raw={}
        )

    rid = _card().relation_id
    assert _category_mismatches(deck(["Ramp", "Commander"]), {rid: ["Commander", "Ramp"]}) == [
        "Lurrus of the Dream-Den"
    ]
    assert (
        _category_mismatches(deck(["Commander", "Ramp", "Draw"]), {rid: ["Commander", "Draw", "Ramp"]}) == []
    )
