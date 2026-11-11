"""A kept card model in plain words (card_models.describe): what the member's own solo tests do with
the card, one line per part of the model, from the same vocabulary the gateway checks (Mystic Forge
dd82cd1). Every word of the vocabulary has its own words, so no program name ever reaches a page."""

from __future__ import annotations

import re

from mtg_gateway import card_models
from mtg_gateway.card_models import clean_model, describe


def test_a_model_reads_as_plain_sentences() -> None:
    model = clean_model(
        {
            "name": "Puresteel Paladin",
            "triggers": [{"on": "equipment_etb", "do": "draw", "count": 1, "if": "metalcraft"}],
            "statics": ["equip_free_if_metalcraft", {"kind": "anthem", "power": 1, "toughness": 1}],
        },
        card="Puresteel Paladin",
    )
    assert describe(model) == [
        "Whenever another Equipment of yours enters, if you control three or more artifacts: draw 1 card.",
        "Equipping costs nothing while you control three or more artifacts.",
        "Creatures you control get +1/+1.",
    ]


def test_each_part_of_a_model_has_words() -> None:
    model = clean_model(
        {
            "name": "Example",
            "triggers": [
                {
                    "on": "etb",
                    "do": "create_token",
                    "count": 2,
                    "power": 1,
                    "toughness": 1,
                    "keywords": ["flying"],
                },
                {
                    "on": "spell_cast",
                    "filter": "instant_or_sorcery",
                    "do": "damage",
                    "count": 1,
                    "target": "each_opponent",
                },
                {"on": "attack", "do": "pump", "power": 2, "toughness": 0, "count": "per_attacker"},
                {"on": "upkeep", "do": "add_mana", "pips": "{R}{R}"},
                {"on": "land_etb", "do": "tutor", "filter": "land"},
            ],
            "activated": [{"cost": "{2}{T}", "do": "ramp_land", "count": 1, "sac_self": True}],
            "statics": [
                {"kind": "cost_reduction", "filter": "color:R", "amount": 1},
                {"kind": "extra_land_drops", "count": 1},
            ],
            "grants": {"power": 2, "toughness": 2, "keywords": ["trample"]},
        },
        card="Example",
    )
    assert describe(model) == [
        "When it enters: create 2 1/1 creature tokens with flying.",
        "Whenever you cast an instant or sorcery spell: deal 1 damage to each opponent.",
        "Whenever you attack: it gets +2/+0 until end of turn, for each attacking creature.",
        "At the beginning of your upkeep: add {R}{R}.",
        "Whenever another land of yours enters: search your library for 1 land card and put it "
        "into your hand.",
        "{2}{T}, sacrifice it: put 1 land from your library onto the battlefield tapped.",
        "Red spells cost {1} less.",
        "You may play 1 extra land each turn.",
        "The creature it equips gets +2/+2 and has trample.",
    ]


def test_a_card_the_solo_test_skips_says_so() -> None:
    model = clean_model({"name": "Odd Card", "inert": True}, card="Odd Card")
    assert describe(model) == [card_models.INERT_TEXT]


def test_every_word_of_the_vocabulary_has_plain_words() -> None:
    """No vocabulary word (snake_case, as the simulator spells it) is ever shown."""
    words = {"etb", "anthem", "pump", "inert"}  # the vocabulary's words that are not English
    snake = re.compile(r"\b[a-z]+_[a-z_]+\b")
    for event in card_models.EVENTS:
        for verb in card_models.VERBS:
            raw = {
                "on": event,
                "do": verb,
                "count": 2,
                "power": 1,
                "toughness": 1,
                "target": "one_opponent",
                "pips": "{G}",
            }
            if event == "spell_cast" and verb == "tutor":
                continue
            model = clean_model({"name": "X", "triggers": [raw]}, card="X")
            for line in describe(model):
                assert not snake.search(line), line
                assert not any(w in line.split() for w in words), line
    for kind in card_models.STATIC_KINDS:
        for line in describe(clean_model({"name": "X", "statics": [kind]}, card="X")):
            assert not snake.search(line), line
    for count in card_models.SYMBOLIC_COUNTS:
        model = clean_model(
            {"name": "X", "triggers": [{"on": "attack", "do": "draw", "count": count}]}, card="X"
        )
        for line in describe(model):
            assert not snake.search(line), line
