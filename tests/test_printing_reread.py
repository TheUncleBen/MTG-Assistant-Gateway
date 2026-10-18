"""The deck page's edit answer says "stale" when Archidekt's re-read doesn't show a printing
or finish change yet, so the page reads once more instead of drawing the old printing under
"Saved"."""

from mtg_gateway.archidekt import Deck, DeckCard
from mtg_gateway.decks import parse_changes, printing_not_shown


def _deck(*cards: DeckCard) -> Deck:
    return Deck(id="1", name="t", cards=list(cards), owner="u", updated_at="", categories=[], raw={})


def _sol(set_code: str, number: str, modifier: str = "Normal", **kw) -> DeckCard:
    base = dict(
        relation_id=1,
        card_id=5,
        name="Sol Ring",
        quantity=1,
        categories=["Ramp"],
        modifier=modifier,
        set_code=set_code,
        collector_number=number,
    )
    base.update(kw)
    return DeckCard(**base)


def test_a_re_read_that_still_shows_the_old_printing_is_stale() -> None:
    ch = parse_changes(
        [{"action": "set_printing", "card_name": "Sol Ring", "set_code": "sld", "collector_number": "1074"}]
    )
    assert printing_not_shown(ch, _deck(_sol("cmr", "472"))) == ["Sol Ring"]
    assert printing_not_shown(ch, _deck(_sol("SLD", "1074", "Etched"))) == []  # only the finish differs
    assert printing_not_shown(ch, _deck()) == ["Sol Ring"]


def test_a_finish_change_and_other_changes() -> None:
    fin = parse_changes([{"action": "set_finish", "card_name": "Sol Ring", "finish": "foil"}])
    assert printing_not_shown(fin, _deck(_sol("cmr", "472"))) == ["Sol Ring"]
    assert printing_not_shown(fin, _deck(_sol("cmr", "472", "Foil"))) == []
    # a count change is edit_mismatches' to compare
    add = parse_changes([{"action": "add", "card_name": "Sol Ring", "quantity": 1}])
    assert printing_not_shown(add, _deck(_sol("cmr", "472"))) == []
