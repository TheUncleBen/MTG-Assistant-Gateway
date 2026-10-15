"""A50: a change to a deck row sends the row's own companion and "flipped by default" flags, as
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
    """K10: Set as commander files the card under Commander as its primary category and keeps its
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
