"""deck_stats over the live Archidekt fixtures and over synthetic decks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtg_gateway.archidekt import FORMAT_IDS, FORMAT_NAMES, Deck, DeckCard, parse_deck
from mtg_gateway.deck_stats import (
    bracket_estimate,
    colour_identity,
    compare,
    compute,
    counts_from_text,
    mana_pips,
)
from mtg_gateway.decklist import ListCard

LIVE = Path(__file__).parent / "fixtures" / "live"


def live(deck_id: str) -> Deck:
    return parse_deck(json.loads((LIVE / f"archidekt_deck_{deck_id}.json").read_text(encoding="utf-8")))


def card(name: str, qty: int = 1, **over) -> DeckCard:
    base = dict(
        relation_id=None,
        card_id=None,
        name=name,
        quantity=qty,
        categories=[],
        modifier="Normal",
        set_code="",
        collector_number="",
    )
    base.update(over)
    return DeckCard(**base)


def deck(cards: list[DeckCard], *, format_id: int | None = 3, categories: list[dict] | None = None) -> Deck:
    return Deck(
        id="1",
        name="t",
        owner="o",
        updated_at="",
        cards=cards,
        categories=categories or [],
        raw={},
        format_id=format_id,
        format=FORMAT_NAMES.get(format_id) if format_id is not None else None,
    )


# -- richer parse -------------------------------------------------------------


def test_parse_deck_reads_oracle_fields_from_live_payload() -> None:
    d = live("365563")
    assert d.format_id == 3 and d.format == "commander"
    assert d.private is False and d.unlisted is False and d.edh_bracket is None
    assert d.created_at.startswith("2020-01-21") and d.tags == []
    assert d.description.startswith('{"ops"')  # Quill JSON, kept as the user's own text
    pact = next(c for c in d.cards if c.name == "Pact of Negation")
    assert pact.oracle_name == "Pact of Negation" and pact.cmc == 0.0 and pact.mana_cost == "{0}"
    assert pact.colors == ["Blue"] and pact.color_identity == ["Blue"] and pact.types == ["Instant"]
    assert pact.rarity == "rare" and pact.price == 22.99  # prices.ck
    assert pact.legalities["commander"] == "legal" and pact.legalities["standard"] == "not_legal"
    assert pact.edhrec_rank == 367 and pact.salt == 1.46
    assert pact.game_changer is False and pact.mana_production is None
    assert pact.label == ",#656565" and pact.notes == "" and pact.companion is False
    assert pact.image_hash == "1783935174" and pact.scryfall_uid == "dd125949-38c4-470f-9128-b80c45621086"
    rift = next(c for c in d.cards if c.name == "Cyclonic Rift")
    assert rift.game_changer is True
    farmland = next(c for c in d.cards if c.name == "Irrigated Farmland")
    assert farmland.mana_production == {"W": 1, "U": 1} and "Land" in farmland.types
    brago = next(c for c in d.cards if c.name == "Brago, King Eternal")
    assert "Legendary" in brago.supertypes and "Commander" in brago.categories


def test_parse_deck_fingerprint_ignores_the_new_fields() -> None:
    body = json.loads((LIVE / "archidekt_deck_365563.json").read_text(encoding="utf-8"))
    before = parse_deck(body).fingerprint()
    for entry in body["cards"]:
        entry["card"]["prices"] = None
        entry["card"]["oracleCard"]["cmc"] = None
        entry["notes"] = "changed"
    body["description"] = "other"
    body["deckFormat"] = 1
    assert parse_deck(body).fingerprint() == before


def test_parse_deck_is_defensive_about_nulls_and_odd_types() -> None:
    body = {
        "id": 5,
        "name": "x",
        "deckFormat": "3",  # wrong type: unknown format
        "edhBracket": True,
        "deckTags": [{"name": "budget"}, "fun", 7, None],
        "description": None,
        "cards": [
            {
                "id": 1,
                "quantity": 2,
                "categories": None,
                "notes": None,
                "label": None,
                "companion": None,
                "card": {
                    "id": 9,
                    "prices": {"ck": None, "tcg": "4.5"},
                    "rarity": None,
                    "oracleCard": {
                        "name": "Thing",
                        "cmc": None,
                        "manaCost": None,
                        "colors": None,
                        "types": ["Creature", 3],
                        "legalities": {"commander": "legal", "pauper": None},
                        "edhrecRank": None,
                        "salt": None,
                        "gameChanger": None,
                        "manaProduction": {"W": None, "C": 0},
                        "twoCardComboIds": None,
                    },
                },
            }
        ],
    }
    d = parse_deck(body)
    assert d.format_id is None and d.format is None and d.edh_bracket is None
    assert d.tags == ["budget", "fun"] and d.description == ""
    c = d.cards[0]
    assert c.cmc is None and c.mana_cost == "" and c.colors == [] and c.types == ["Creature"]
    assert c.price is None  # a string price is not trusted
    assert c.legalities == {"commander": "legal"} and c.edhrec_rank is None and c.salt is None
    assert c.game_changer is False and c.mana_production is None and c.two_card_combo_ids == []
    assert c.notes == "" and c.label == "" and c.companion is False and c.rarity == ""


def test_format_names_reverse_format_ids() -> None:
    assert FORMAT_NAMES[3] == "commander"  # not "edh"
    for name, fid in FORMAT_IDS.items():
        assert FORMAT_IDS[FORMAT_NAMES[fid]] == FORMAT_IDS[name]


# -- compute over live fixtures -----------------------------------------------


def test_compute_brago_deck() -> None:
    s = compute(live("365563"))
    assert s["card_count"] == 103 and s["distinct"] == 95
    assert s["land_count"] == 34 and s["nonland_count"] == 69
    assert s["average_mana_value"] == 2.57
    assert s["mana_curve"] == {"0": 3, "1": 13, "2": 15, "3": 24, "4": 10, "5": 3, "6": 0, "7+": 1}
    assert s["colour_pips"] == {"U": 53.5, "W": 25.5}  # Turn to Mist's {W/U} counts half each
    assert s["mana_sources"]["W"] == 24 and s["mana_sources"]["U"] == 25 and s["mana_sources"]["C"] == 19
    assert s["type_counts"]["Land"] == 34 and s["type_counts"]["Creature"] == 19
    assert s["rarity_counts"] == {"rare": 49, "common": 26, "uncommon": 21, "mythic": 7}
    assert s["price_total"] == 1798.22 and s["priced_cards"] == 94
    assert s["format"] == "commander"
    assert {p["name"] for p in s["legality_problems"]} == {"Mana Crypt", "Jeweled Lotus", "Hullbreacher"}
    assert all(p["status"] == "banned" for p in s["legality_problems"])
    assert s["legality_unknown"] == 0
    assert len(s["game_changers"]) == 12 and "Rhystic Study" in s["game_changers"]
    assert s["tutors"] == 11 and s["extra_turns"] == 1 and s["mass_land_denial"] == 0
    assert s["salt_total"] == 67.93
    assert s["commanders"] == ["Brago, King Eternal"] and s["colour_identity"] == ["W", "U"]
    est = s["bracket_estimate"]
    assert est["bracket"] == 4 and est["kind"] == "estimate"
    assert est["basis"][0].startswith("12 game changer(s)")
    assert any("one extra-turn card" in b for b in est["basis"])
    assert s["archidekt_bracket"] is None


def test_compute_precon_deck() -> None:
    s = compute(live("sample"))
    assert s["card_count"] == 100 and s["land_count"] == 44
    assert s["commanders"] == ["Aesi, Tyrant of Gyre Strait"] and s["colour_identity"] == ["U", "G"]
    assert s["legality_problems"] == [] and s["game_changers"] == []
    assert s["bracket_estimate"] == {"bracket": 2, "kind": "estimate", "basis": ["no game changers"]}


def test_compute_leaves_maybeboard_out() -> None:
    # Live decks mark a maybeboard as a category with includedInDeck false; its rows are not counted.
    d = deck(
        [
            card("Aesi, Tyrant of Gyre Strait", categories=["Commander"], cmc=6.0, types=["Creature"]),
            card("Forest", 30, categories=["Land"], types=["Land"]),
            card("Sol Ring", categories=["Maybeboard"], cmc=1.0, types=["Artifact"]),
            card("Opt", 2, categories=["Maybeboard"], cmc=1.0, types=["Instant"]),
        ],
        categories=[
            {"name": "Maybeboard", "includedInDeck": False},
            {"name": "Land", "includedInDeck": True},
        ],
    )
    excluded = d.excluded_categories()
    side = sum(c.quantity for c in d.side_cards)
    s = compute(d)
    assert excluded == {"Maybeboard"} and side == 3
    assert s["card_count"] == sum(c.quantity for c in d.cards) - side == 31
    assert s["distinct"] == 2 and s["land_count"] == 30


# -- synthetic ----------------------------------------------------------------


@pytest.mark.parametrize(
    "cost, pips",
    [
        ("{2}{U}{U}", {"U": 2.0}),
        ("{1}{W/U}", {"W": 0.5, "U": 0.5}),
        ("{2}{G/U}{G/U}{G/U}", {"G": 1.5, "U": 1.5}),
        ("{2/W}", {"W": 1.0}),
        ("{W/P}{G/U/P}", {"W": 1.0, "G": 0.5, "U": 0.5}),
        ("{X}{C}{S}{0}", {}),
        ("", {}),
        ("not a cost", {}),
    ],
)
def test_mana_pips(cost: str, pips: dict[str, float]) -> None:
    assert mana_pips(cost) == pips


def test_colour_identity_accepts_names_and_letters_in_wubrg_order() -> None:
    cards = [card("a", color_identity=["Green", "Blue"]), card("b", color_identity=["R", "Colorless"])]
    assert colour_identity(cards) == ["U", "R", "G"]
    assert colour_identity([]) == []


def test_compute_empty_deck() -> None:
    s = compute(deck([]))
    assert s["card_count"] == 0 and s["average_mana_value"] is None and s["price_total"] == 0.0
    assert s["mana_curve"]["7+"] == 0 and s["colour_pips"] == {} and s["mana_sources"] == {}
    assert s["bracket_estimate"]["bracket"] is None and s["bracket_estimate"]["basis"] == ["no cards"]


def test_compute_curve_price_and_legality() -> None:
    cards = [
        card(
            "Island",
            10,
            types=["Land"],
            mana_production={"U": 1},
            price=0.1,
            legalities={"commander": "legal"},
        ),
        card("Big", 1, cmc=9.0, mana_cost="{7}{U}{U}", types=["Creature"], price=12.5, rarity="mythic"),
        card("Mid", 2, cmc=3.0, mana_cost="{2}{U}", types=["Artifact", "Creature"], rarity="rare"),
        card("Banned", 1, cmc=1.0, mana_cost="{U}", types=["Sorcery"], legalities={"commander": "banned"}),
        card("Unknown", 1, cmc=2.0, types=["Instant"], legalities={}),
    ]
    s = compute(deck(cards))
    assert s["card_count"] == 15 and s["distinct"] == 5
    assert s["land_count"] == 10 and s["nonland_count"] == 5
    assert s["average_mana_value"] == round((9 + 6 + 1 + 2) / 5, 2)
    assert s["mana_curve"] == {"0": 0, "1": 1, "2": 1, "3": 2, "4": 0, "5": 0, "6": 0, "7+": 1}
    assert s["colour_pips"] == {"U": 5.0}
    assert s["mana_sources"] == {"U": 10}
    assert s["type_counts"] == {"Land": 10, "Creature": 3, "Artifact": 2, "Sorcery": 1, "Instant": 1}
    assert s["rarity_counts"] == {"rare": 2, "mythic": 1}
    assert s["price_total"] == 13.5 and s["priced_cards"] == 2
    assert s["legality_problems"] == [{"name": "Banned", "status": "banned"}]
    assert s["legality_unknown"] == 3  # Big, Mid and Unknown carry no commander entry


def test_compute_skips_legality_when_format_unknown() -> None:
    s = compute(deck([card("x", legalities={"commander": "banned"})], format_id=None))
    assert s["format"] is None and s["legality_problems"] == [] and s["legality_unknown"] == 0


def test_bracket_rules() -> None:
    gc = [card(f"gc{i}", game_changer=True) for i in range(4)]
    assert bracket_estimate([card("plain")])["bracket"] == 2
    assert bracket_estimate(gc[:1])["bracket"] == 3
    assert bracket_estimate(gc[:3])["bracket"] == 3
    assert bracket_estimate(gc)["bracket"] == 4
    mld = bracket_estimate([card("Armageddon", mass_land_denial=True)])
    assert mld["bracket"] == 4 and any("mass land denial: Armageddon" in b for b in mld["basis"])
    one_turn = bracket_estimate([card("Time Warp", extra_turns=True)])
    assert one_turn["bracket"] == 2 and any("one extra-turn card" in b for b in one_turn["basis"])
    chain = bracket_estimate(
        [card("Time Warp", extra_turns=True), card("Temporal Mastery", extra_turns=True)]
    )
    assert chain["bracket"] == 4 and any("chaining" in b for b in chain["basis"])
    combo = bracket_estimate([card("A", two_card_combo_ids=["1"]), card("B", two_card_combo_ids=["1", "2"])])
    assert combo["bracket"] == 3 and any("A + B" in b for b in combo["basis"])
    half = bracket_estimate([card("A", two_card_combo_ids=["1"])])  # partner not in the deck
    assert half["bracket"] == 2 and not any("combo" in b for b in half["basis"])
    tutors = bracket_estimate([card("Demonic Tutor", tutor=True)])
    assert tutors["bracket"] == 2 and "1 tutor(s)" in tutors["basis"]
    assert bracket_estimate([card("x")], "modern") == {
        "bracket": None,
        "kind": "estimate",
        "basis": ["not a Commander deck (modern)"],
    }
    assert bracket_estimate([], "commander")["bracket"] is None


def test_counts_from_text_only_counts_the_main_zone() -> None:
    cards = [
        ListCard(quantity=2, name="Opt"),
        ListCard(quantity=1, name="Opt"),
        ListCard(quantity=1, name="Island", zone="side"),
    ]
    assert counts_from_text(cards) == {"Opt": 3}


def test_compare_dicts_and_decks() -> None:
    out = compare({"Opt": 2, "Island": 10, "Gone": 1}, {"Opt": 3, "Island": 10, "New": 1})
    assert out == {
        "added": [{"name": "New", "quantity": 1}],
        "removed": [{"name": "Gone", "quantity": 1}],
        "changed": [{"name": "Opt", "before": 2, "after": 3}],
        "summary": {
            "before_size": 13,
            "after_size": 14,
            "cut": 1,
            "added": 1,
            "kept": 1,
            "cut_pct": 8,
            "added_pct": 8,
            "basic_land_changes": [],
        },
    }
    assert "stats_delta" not in out
    before = deck([card("Island", 10, types=["Land"], price=0.1), card("Opt", 1, cmc=1.0, types=["Instant"])])
    after = deck(
        [card("Island", 9, types=["Land"], price=0.1), card("Big", 1, cmc=5.0, types=["Creature"], price=3.0)]
    )
    out = compare(before, after)
    assert out["added"] == [{"name": "Big", "quantity": 1}]
    assert out["removed"] == [{"name": "Opt", "quantity": 1}]
    assert out["changed"] == [{"name": "Island", "before": 10, "after": 9}]
    delta = out["stats_delta"]
    assert delta["card_count"] == -1 and delta["land_count"] == -1 and delta["nonland_count"] == 0
    assert delta["average_mana_value"] == 4.0 and delta["price_total"] == 2.9 and delta["priced_cards"] == 1
    assert delta["tutors"] == 0 and delta["salt_total"] == 0.0


def test_compare_mixed_deck_and_dict_has_no_stats_delta() -> None:
    d = deck([card("Opt", 1)])
    assert "stats_delta" not in compare(d, {"Opt": 1}) and compare(d, {"Opt": 1})["changed"] == []


def test_compare_leaves_the_maybeboard_out() -> None:
    cats = [{"name": "Maybeboard", "includedInDeck": False}]
    d1 = deck([card("Opt", 1), card("Maybe", 1, categories=["Maybeboard"])], categories=cats)
    d2 = deck([card("Opt", 1)], categories=cats)
    assert compare(d1, d2) == {
        "added": [],
        "removed": [],
        "changed": [],
        "summary": compare(d1, d2)["summary"],
        "stats_delta": compare(d1, d2)["stats_delta"],
    }
    assert all(v in (0, 0.0, None) for v in compare(d1, d2)["stats_delta"].values())


def test_compare_matches_faces_and_case_and_summarises_like_a_precon_diff() -> None:
    # The precon side names the front face only; basics are counted apart from the cuts and adds.
    precon = {"Delver of Secrets": 1, "sol ring": 1, "Island": 30, "Forest": 10, "Gone": 1}
    build = {
        "Delver of Secrets // Insectile Aberration": 1,
        "Sol Ring": 1,
        "Island": 28,
        "Forest": 10,
        "New": 2,
    }
    out = compare(precon, build)
    assert out["added"] == [{"name": "New", "quantity": 2}]
    assert out["removed"] == [{"name": "Gone", "quantity": 1}]
    assert out["changed"] == [{"name": "Island", "before": 30, "after": 28}]
    assert out["summary"] == {
        "before_size": 43,
        "after_size": 42,
        "cut": 1,
        "added": 1,
        "kept": 2,
        "cut_pct": 2,
        "added_pct": 2,
        "basic_land_changes": [{"name": "Island", "before": 30, "after": 28}],
    }


def test_checks_cover_what_the_archidekt_validator_checked() -> None:
    cats = [{"name": "Commander", "isPremier": True, "includedInDeck": True}]
    ok = deck(
        [
            card("Aesi", 1, categories=["Commander"], types=["Creature"], supertypes=["Legendary"],
                 color_identity=["G", "U"]),
            card("Island", 50, types=["Land"], supertypes=["Basic"], color_identity=["U"]),
            card("Forest", 48, types=["Land"], supertypes=["Basic"], color_identity=["G"]),
            card("Opt", 1, types=["Instant"], color_identity=["U"], categories=["Draw"]),
        ],
        categories=cats,
    )  # fmt: skip
    checks = compute(ok)["checks"]
    assert checks["ok"] and checks["problems"] == []
    assert checks["deck_size"] == {"actual": 100, "expected": 100, "ok": True}
    assert checks["commander_zone"] == {"count": 1, "ok": True}
    bad = deck(
        [
            card("Opt", 2, categories=["Commander"], types=["Instant"], color_identity=["U"]),
            card("Lightning Bolt", 1, types=["Instant"], color_identity=["R"]),
            card("Relentless Rats", 3, types=["Creature"], color_identity=["B"],
                 oracle_text="A deck can have any number of cards named Relentless Rats.", categories=["x"]),
            card("Island", 10, types=["Land"], supertypes=["Basic"], color_identity=["U"], 
                 categories=["Land"]),
        ],
        categories=cats,
    )  # fmt: skip
    checks = compute(bad)["checks"]
    assert not checks["ok"]
    assert checks["deck_size"]["ok"] is False and "16 cards" in checks["problems"][0]
    assert checks["commander_zone"] == {"count": 2, "ok": False, "cannot_command": ["Opt"]}
    assert checks["colour_identity_violations"] == [
        {"name": "Lightning Bolt", "outside": ["R"]},
        {"name": "Relentless Rats", "outside": ["B"]},
    ]
    assert checks["singleton_violations"] == [{"name": "Opt", "quantity": 2}]  # the Rats may repeat
    assert checks["uncategorised"] == ["Lightning Bolt"]
    # a constructed format: no commander checks; at least 60 cards, at most 4 copies, sideboard 15
    small = compute(deck([card("Opt", 4)], format_id=1))["checks"]
    assert small["singleton_violations"] == [] and small["copy_limit_violations"] == []
    assert (
        small["deck_size"] == {"actual": 4, "minimum": 60, "ok": False}
        and "at least 60" in small["problems"][0]
    )
    assert small["commander_zone"] == {"count": 0, "ok": True} and small["sideboard"]["ok"]
    sixty = compute(
        deck([card("Opt", 5), card("Island", 55, types=["Land"], supertypes=["Basic"])], format_id=1)
    )
    assert sixty["checks"]["deck_size"]["ok"] and sixty["checks"]["copy_limit_violations"] == [
        {"name": "Opt", "quantity": 5}
    ]


def test_commander_zone_is_the_premier_category_whatever_its_name() -> None:
    """Archidekt marks the commander zone with isPremier; a deck whose premier category is not
    literally named "Commander" still has its commander found, and the simulators' text gets the
    Commander marker for it (Mystic Forge finds the commander by that word)."""
    from mtg_gateway.decks import deck_to_text

    d = deck(
        [
            card("Aesi", 1, categories=["Leaders"], types=["Creature"], supertypes=["Legendary"],
                 color_identity=["G", "U"]),
            card("Island", 99, types=["Land"], supertypes=["Basic"], color_identity=["U"]),
        ],
        categories=[{"name": "Leaders", "isPremier": True, "includedInDeck": True}],
    )  # fmt: skip
    stats = compute(d)
    assert stats["commanders"] == ["Aesi"]
    assert stats["checks"]["commander_zone"] == {"count": 1, "ok": True}
    assert deck_to_text(d).splitlines()[0] == "1 Aesi"  # commander first, as the simulators expect
