"""The deck's price leaves out cards filed under a category marked "Not in price".

The gateway offers that switch on the deck page and to assistants, and Archidekt's own total
follows it, so a deck with a "Proxies" category must not show a higher price here than there."""

from __future__ import annotations

import copy
import json
import pathlib

from mtg_gateway import deck_stats
from mtg_gateway.archidekt import parse_deck

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "live" / "archidekt_deck_blink_sample.json"


def _deck(in_price: bool):
    d = copy.deepcopy(json.loads(FIXTURE.read_text()))
    for row in d["cards"]:
        row["card"]["prices"] = {}
    first, second = d["cards"][:2]  # both in the deck
    first["card"]["prices"] = second["card"]["prices"] = {"ck": 10.0}
    second["categories"] = ["Proxies"]
    d["categories"] = [c for c in d["categories"] if c.get("name") != "Proxies"] + [
        {
            "id": 999,
            "name": "Proxies",
            "isPremier": False,
            "includedInDeck": True,
            "includedInPrice": in_price,
        }
    ]
    return parse_deck(d)


def test_a_card_in_a_not_in_price_category_is_left_out_of_the_total():
    stats = deck_stats.compute(_deck(in_price=False))
    assert stats["price_total"] == 10.0
    assert stats["priced_cards"] == 2 and stats["not_in_price_cards"] == 1


def test_the_same_card_counts_when_the_category_is_in_the_price():
    stats = deck_stats.compute(_deck(in_price=True))
    assert stats["price_total"] == 20.0 and "not_in_price_cards" not in stats
