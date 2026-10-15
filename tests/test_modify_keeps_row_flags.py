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
