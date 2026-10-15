"""A50 M2: a commander that set_commander demotes, and whose only category was Commander, is
sent its default category (as archidekt.com never sends an empty list) when the gateway knows
one; without one it is sent no category, as before. Its other categories are not this test's
question."""

from mtg_gateway.archidekt import Deck, DeckCard
from mtg_gateway.decks import build_payload, category_plan, parse_changes


def _deck(*cards: DeckCard) -> Deck:
    return Deck(id="1", name="d", owner="o", updated_at="", cards=list(cards), categories=[], raw={})


def _card(rel: int, name: str, cats: list[str], default: str = "") -> DeckCard:
    return DeckCard(rel, 100 + rel, name, 1, cats, "Normal", "cmr", str(rel), default_category=default)


def _demote(deck: Deck) -> dict[int, list[str]]:
    want, _ = category_plan(deck, parse_changes([{"action": "set_commander", "card_name": "Forest"}]))
    return want


def test_a_demoted_commander_gets_its_default_category():
    deck = _deck(_card(1, "Aesi, Tyrant of Gyre Strait", ["Commander"], "Creature"), _card(2, "Forest", []))
    want = _demote(deck)
    assert want == {2: ["Commander"], 1: ["Creature"]}
    payload = build_payload(deck, deck.counts_by_name(), {}, recategorise=want)
    assert {e["deckRelationId"]: e["categories"] for e in payload} == {1: ["Creature"], 2: ["Commander"]}


def test_without_a_default_category_the_list_stays_empty():
    deck = _deck(_card(1, "Aesi, Tyrant of Gyre Strait", ["Commander"]), _card(2, "Forest", []))
    assert _demote(deck) == {2: ["Commander"], 1: []}


def test_other_categories_are_kept_and_the_default_is_not_added():
    deck = _deck(
        _card(1, "Aesi, Tyrant of Gyre Strait", ["Commander", "Ramp"], "Creature"), _card(2, "Forest", [])
    )
    assert _demote(deck)[1] == ["Ramp"]
