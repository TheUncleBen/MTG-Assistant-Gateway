"""deck_stats over the live Archidekt fixtures and over synthetic decks."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from mtg_gateway.archidekt import FORMAT_IDS, FORMAT_NAMES, Deck, DeckCard, parse_deck
from mtg_gateway.deck_stats import (
    ARCHIDEKT_COMBO_NOTE,
    ARCHIDEKT_TURNS_NOTE,
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
    d = live("blink_sample")
    assert d.format_id == 3 and d.format == "commander"
    assert d.private is False and d.unlisted is False and d.edh_bracket is None
    assert d.created_at.startswith("2026-01-21") and d.tags == []
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
    body = json.loads((LIVE / "archidekt_deck_blink_sample.json").read_text(encoding="utf-8"))
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


def test_compute_blink_deck() -> None:
    s = compute(live("blink_sample"))
    # The three rows filed under Sideboard are the sideboard, not the deck (as archidekt.com counts
    # them: its Sideboard category is "included in deck" but kept out of the deck's size).
    assert s["card_count"] == 100 and s["distinct"] == 92
    assert s["land_count"] == 34 and s["nonland_count"] == 66
    assert s["average_mana_value"] == 2.58
    assert s["mana_curve"] == {"0": 3, "1": 12, "2": 15, "3": 22, "4": 10, "5": 3, "6": 0, "7+": 1}
    assert s["colour_pips"] == {"U": 53.5, "W": 22.5}  # Turn to Mist's {W/U} counts half each
    assert s["mana_sources"]["W"] == 24 and s["mana_sources"]["U"] == 25 and s["mana_sources"]["C"] == 19
    assert s["type_counts"]["Land"] == 34 and s["type_counts"]["Creature"] == 19
    assert s["rarity_counts"] == {"rare": 47, "common": 26, "uncommon": 20, "mythic": 7}
    assert s["price_total"] == 1758.89 and s["priced_cards"] == 91
    assert s["checks"]["deck_size"] == {"actual": 100, "expected": 100, "ok": True}
    assert s["format"] == "commander"
    assert {p["name"] for p in s["legality_problems"]} == {"Mana Crypt", "Jeweled Lotus", "Hullbreacher"}
    assert all(p["status"] == "banned" for p in s["legality_problems"])
    assert s["legality_unknown"] == 0
    assert len(s["game_changers"]) == 12 and "Rhystic Study" in s["game_changers"]
    assert s["tutors"] == 11 and s["extra_turns"] == 1 and s["mass_land_denial"] == 0
    assert s["salt_total"] == 66.02
    assert s["commanders"] == ["Brago, King Eternal"] and s["colour_identity"] == ["W", "U"]
    est = s["bracket_estimate"]
    assert est["bracket"] == 4 and est["kind"] == "estimate"
    assert est["basis"][0].startswith("12 game changer(s)")
    assert any(b.startswith("1 extra-turn card(s): ") for b in est["basis"]) and est["notes"] == []
    assert s["archidekt_bracket"] is None


def test_compute_precon_deck() -> None:
    s = compute(live("sample"))
    assert s["card_count"] == 100 and s["land_count"] == 44
    assert s["commanders"] == ["Aesi, Tyrant of Gyre Strait"] and s["colour_identity"] == ["U", "G"]
    assert s["legality_problems"] == [] and s["game_changers"] == []
    est = s["bracket_estimate"]
    assert (est["bracket"], est["basis"], est["notes"]) == (2, ["no game changers"], [])


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
    # Big, Mid (two copies) and Unknown carry no commander entry: counted in copies, as
    # deck_checks (the one legality engine) and the deck page count them
    assert s["legality_unknown"] == 4 == s["checks"]["legality"]["unknown"]


def test_compute_skips_legality_when_format_unknown() -> None:
    s = compute(deck([card("x", legalities={"commander": "banned"})], format_id=None))
    assert s["format"] is None and s["legality_problems"] == [] and s["legality_unknown"] == 0


def _filler(n: int) -> list[DeckCard]:
    """n different unflagged cards: Archidekt gives 1 only below 30 different cards."""
    return [card(f"filler {i}") for i in range(n)]


# Each rule of archidekt.com's "Estimated Bracket" (its deck page script, read 2026-10-14;
# planning BRACKET-ESTIMATE.md §3), pinned one by one.
def test_bracket_matches_archidekts_rules() -> None:
    big = _filler(40)
    gc = [card(f"gc{i}", game_changer=True) for i in range(4)]
    # nothing flagged: 2, or 1 below 30 different cards
    assert bracket_estimate(big)["bracket"] == 2
    small = bracket_estimate(_filler(29))
    assert small["bracket"] == 1 and any("fewer than 30 different cards (29)" in b for b in small["basis"])
    assert bracket_estimate(_filler(30))["bracket"] == 2
    # game changers, counted in copies: 1-3 give 3, 4 or more give 4
    assert bracket_estimate(big + gc[:1])["bracket"] == 3
    assert bracket_estimate(big + gc[:3])["bracket"] == 3
    assert bracket_estimate(big + gc)["bracket"] == 4
    assert bracket_estimate(big + [card("gc", 4, game_changer=True)])["bracket"] == 4
    # any mass land denial gives 4
    mld = bracket_estimate(big + [card("Armageddon", mass_land_denial=True)])
    assert mld["bracket"] == 4 and any("mass land denial: Armageddon" in b for b in mld["basis"])
    # one extra-turn card changes nothing; two or more give 3, with Archidekt's note
    one_turn = bracket_estimate(big + [card("Time Warp", extra_turns=True)])
    assert one_turn["bracket"] == 2 and one_turn["notes"] == []
    chain = bracket_estimate(
        big + [card("Time Warp", extra_turns=True), card("Temporal Mastery", extra_turns=True)]
    )
    assert chain["bracket"] == 3 and chain["notes"] == [ARCHIDEKT_TURNS_NOTE]
    assert ARCHIDEKT_TURNS_NOTE == "If your deck chains extra turns, your deck should be Optimized or cEDH"
    # extra turns never push past 3 on their own, even with game changers below four
    assert bracket_estimate(big + gc[:3] + [card("t", 2, extra_turns=True)])["bracket"] == 3
    # a combo piece (an atomicCombos partner in the deck) gives 4, with Archidekt's note
    combo = bracket_estimate(
        big + [card("A", oracle_id="a", atomic_combos=["b"]), card("B", oracle_id="b", atomic_combos=["a"])]
    )
    assert combo["bracket"] == 4 and combo["notes"] == [ARCHIDEKT_COMBO_NOTE]
    assert ARCHIDEKT_COMBO_NOTE == "If this is a late-game combo, your deck may be a Bracket: Upgraded"
    assert any("two-card combo pieces: A, B" in b for b in combo["basis"])
    half = bracket_estimate(big + [card("A", oracle_id="a", atomic_combos=["b"])])  # partner not in the deck
    assert half["bracket"] == 2 and not any("combo" in b for b in half["basis"])
    # the older twoCardComboIds field no longer counts (Archidekt's estimate reads atomicCombos)
    old = bracket_estimate(big + [card("A", two_card_combo_ids=["1"]), card("B", two_card_combo_ids=["1"])])
    assert old["bracket"] == 2
    # the sideboard counts with the deck
    side = bracket_estimate(big, side=[card("Armageddon", mass_land_denial=True)])
    assert side["bracket"] == 4
    # tutors are listed and never change the number
    tutors = bracket_estimate(big + [card("Demonic Tutor", tutor=True)])
    assert tutors["bracket"] == 2 and any("1 tutor(s)" in b for b in tutors["basis"])
    # never 5, whatever the deck holds
    everything = big + gc + [card("Armageddon", mass_land_denial=True), card("t", 3, extra_turns=True)]
    assert bracket_estimate(everything)["bracket"] == 4
    assert bracket_estimate(everything)["counts"] == {
        "game_changers": 4,
        "mass_land_denial": 1,
        "extra_turns": 3,
        "combo_pieces": 0,
        "different_cards": 46,
    }
    assert bracket_estimate([card("x")], "modern") == {
        "bracket": None,
        "kind": "estimate",
        "basis": ["not a Commander deck (modern)"],
    }
    assert bracket_estimate([], "commander")["bracket"] is None


def test_compute_counts_the_sideboard_in_the_estimate() -> None:
    cards = [*_filler(40), card("Armageddon", categories=["Sideboard"], mass_land_denial=True)]
    s = compute(deck(cards))
    assert s["bracket_estimate"]["bracket"] == 4 and s["archidekt_bracket"] is None


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
            "kept": 3,  # cards kept, at the after count: the three Opt
            "cut_pct": 8,  # 1 of 13
            "added_pct": 7,  # 1 of 14
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
        "added": 2,  # cards, not rows: both copies of New
        "kept": 2,
        "cut_pct": 2,  # of the precon's 43 cards
        "added_pct": 5,  # of the build's 42 cards
        "basic_land_changes": [{"name": "Island", "before": 30, "after": 28}],
    }


def test_compare_percentages_are_shares_of_each_deck() -> None:
    # A 5-card list upgraded into a 67-card deck: 65 cards put in is 97% of the new deck, not 1300%
    # of the old one; the two basics stay out of the cut and add counts.
    short = {f"Old {i}": 1 for i in range(5)}
    big = {f"New {i}": 1 for i in range(65)}
    big.update({"Island": 1, "Forest": 1})
    sm = compare(short, big)["summary"]
    assert sm["before_size"] == 5 and sm["after_size"] == 67
    assert sm["cut"] == 5 and sm["cut_pct"] == 100
    assert sm["added"] == 65 and sm["added_pct"] == 97
    assert len(compare(short, big)["added"]) == 67  # the rows still list the basics


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
    first = deck_to_text(d).splitlines()[0]  # commander first, tagged Commander, as the simulators expect
    assert first.startswith("1 Aesi [") and "Commander" in first


def _cmdr(name: str, text: str = "", **over) -> DeckCard:
    base = dict(types=["Creature"], supertypes=["Legendary"], categories=["Commander"], oracle_text=text)
    base.update(over)
    return card(name, 1, **base)


def test_checks_fail_on_banned_not_legal_and_restricted_cards() -> None:
    """A deck is never "fine" with banned cards in it: legality is part of checks.ok. A restricted
    card (Vintage) passes as one copy and fails as two."""
    d = deck(
        [
            _cmdr("Aesi", color_identity=["G", "U"], legalities={"commander": "legal"}),
            card("Flash", 1, legalities={"commander": "banned"}),
            card("Opt", 1, legalities={"commander": "legal"}),
            card("Island", 98, types=["Land"], supertypes=["Basic"], legalities={"commander": "legal"}),
        ],
        categories=[{"name": "Commander", "isPremier": True, "includedInDeck": True}],
    )
    checks = compute(d)["checks"]
    assert checks["legality"]["banned"] == ["Flash"] and checks["legality"]["ok"] is False
    assert checks["ok"] is False and any("banned" in p for p in checks["problems"])
    v = deck(
        [
            card("Black Lotus", 2, legalities={"vintage": "restricted"}),
            card("Mental Misstep", 1, legalities={"vintage": "banned"}),
            card("Island", 57, types=["Land"], supertypes=["Basic"], legalities={"vintage": "legal"}),
        ],
        format_id=5,
    )
    checks = compute(v)["checks"]
    assert checks["legality"]["restricted_violations"] == [{"name": "Black Lotus", "quantity": 2}]
    assert checks["legality"]["banned"] == ["Mental Misstep"] and checks["ok"] is False
    one = deck(
        [
            card("Black Lotus", 1, legalities={"vintage": "restricted"}),
            card("Island", 59, types=["Land"], supertypes=["Basic"], legalities={"vintage": "legal"}),
        ],
        format_id=5,
    )
    assert compute(one)["checks"]["ok"] is True


def test_two_commanders_need_a_partner_ability() -> None:
    islands = card("Island", 98, types=["Land"], supertypes=["Basic"])
    cat = [{"name": "Commander", "isPremier": True, "includedInDeck": True}]
    partners = deck([_cmdr("Thrasios", "Partner (You can have two commanders...)"),
                     _cmdr("Tymna", "Partner"), islands], categories=cat)  # fmt: skip
    assert compute(partners)["checks"]["commander_zone"]["pairing"] == "partners"
    assert compute(partners)["checks"]["ok"] is True
    named = deck([_cmdr("Pir", "Partner with Toothy (When this...)"),
                  _cmdr("Toothy", "Partner with Pir (When this...)"), islands], categories=cat)  # fmt: skip
    assert compute(named)["checks"]["commander_zone"]["pairing"] == "partners"
    wrong = deck([_cmdr("Pir", "Partner with Toothy"), _cmdr("Tymna", "Partner"), islands], categories=cat)
    checks = compute(wrong)["checks"]
    assert checks["commander_zone"]["pairing"] == "invalid" and checks["ok"] is False
    background = deck([_cmdr("Wilson", "Choose a Background"),
                       _cmdr("Raised by Giants", types=["Enchantment"], subtypes=["Background"]),
                       islands], categories=cat)  # fmt: skip
    bg = compute(background)["checks"]
    assert bg["commander_zone"]["pairing"] == "partners" and bg["commander_zone"]["ok"] is True
    assert bg["ok"] is True and "cannot_command" not in bg["commander_zone"], (
        bg
    )  # the Background may sit there
    alone = deck(
        [_cmdr("Raised by Giants", types=["Enchantment"], subtypes=["Background"]), islands], categories=cat
    )
    assert compute(alone)["checks"]["commander_zone"]["cannot_command"] == ["Raised by Giants"]
    plain = deck([_cmdr("Aesi"), _cmdr("Krenko"), islands], categories=cat)
    assert compute(plain)["checks"]["commander_zone"]["pairing"] == "invalid"


def test_format_specific_command_zones() -> None:
    cat = [{"name": "Commander", "isPremier": True, "includedInDeck": True}]
    # Oathbreaker: a planeswalker and an instant or sorcery, 60 cards
    isl = card("Island", 58, types=["Land"], supertypes=["Basic"])
    ob = deck([_cmdr("Saheeli", types=["Planeswalker"]), _cmdr("Opt", types=["Instant"], supertypes=[]), isl],
              format_id=14, categories=cat)  # fmt: skip
    checks = compute(ob)["checks"]
    assert checks["commander_zone"]["pairing"] == "oathbreaker and signature spell" and checks["ok"], checks
    isl59 = card("Island", 59, types=["Land"], supertypes=["Basic"])
    lone = deck([_cmdr("Saheeli", types=["Planeswalker"]), isl59], format_id=14, categories=cat)
    assert compute(lone)["checks"]["ok"] is False
    # Canadian Highlander: 100 singleton, no command zone at all
    can = deck([card("Island", 100, types=["Land"], supertypes=["Basic"])], format_id=25)
    assert compute(can)["checks"]["ok"] is True
    # ... at least 100 (archidekt.com sets a minimum, not an exact size)
    big = deck([card("Island", 101, types=["Land"], supertypes=["Basic"])], format_id=25)
    assert compute(big)["checks"]["ok"] is True
    # Tiny Leaders Reborn: 50 cards, a sideboard of at most 10 (archidekt.com sets no mana value
    # cap on the commander; the cards' own tlr legality covers it)
    tiny = deck([_cmdr("Aesi", cmc=6.0), card("Island", 49, types=["Land"], supertypes=["Basic"]),
                 card("Opt", 11, categories=["Sideboard"])],
                format_id=27, categories=cat)  # fmt: skip
    assert compute(tiny)["checks"]["problems"] == ["sideboard has 11 cards; at most 10"]
    # Pauper Commander: a creature ever printed at uncommon may lead, whichever printing the deck
    # uses (a mythic or rare printing of such a card is fine). The deck's data names only the
    # chosen printing's rarity, so a non-uncommon printing is reported as unverified, never as a
    # failure (for example a leader in a rare printing that was also printed at uncommon).
    pdh = deck([_cmdr("Aesi", rarity="mythic"), card("Island", 99, types=["Land"], supertypes=["Basic"])],
               format_id=17, categories=cat)  # fmt: skip
    pdh_zone = compute(pdh)["checks"]["commander_zone"]
    assert pdh_zone["ok"] is True and pdh_zone["unverified"] == ["Aesi (mythic printing)"]
    assert "any printing of it is uncommon" in pdh_zone["unverified_note"]
    assert not any("uncommon" in p for p in compute(pdh)["checks"]["problems"])
    # ... which need not be legendary, and is not held to the commons rule the other 99 follow
    # (Archidekt flags the uncommon leader itself as not legal in paupercommander).
    legal = {"paupercommander": "legal"}
    commons = card("Island", 99, types=["Land"], supertypes=["Basic"], legalities=legal)
    leader = _cmdr(
        "Third Path Iconoclast", rarity="uncommon", supertypes=[], legalities={"paupercommander": "not_legal"}
    )
    good_stats = compute(deck([leader, commons], format_id=17, categories=cat))
    good = good_stats["checks"]
    assert good["ok"] is True and good["commander_zone"]["ok"] is True, good
    assert good["legality"]["not_legal"] == []
    assert good_stats["legality_problems"] == []  # the stats list agrees with the checks
    not_creature = _cmdr("Opt", types=["Instant"], rarity="uncommon", supertypes=[])
    bad = compute(deck([not_creature, commons], format_id=17, categories=cat))["checks"]
    assert bad["commander_zone"]["cannot_command"] == ["Opt"]
    # A commander on the format's banned list fails even though it is uncommon.
    banned = _cmdr(
        "Banned Leader", rarity="uncommon", supertypes=[], legalities={"paupercommander": "banned"}
    )
    banned_stats = compute(deck([banned, commons], format_id=17, categories=cat))
    out = banned_stats["checks"]
    assert out["ok"] is False and out["legality"]["banned"] == ["Banned Leader"], out
    assert banned_stats["legality_problems"] == [{"name": "Banned Leader", "status": "banned"}]
    assert any("banned" in p for p in out["problems"])


def test_the_first_category_decides_whether_a_row_is_in_the_deck() -> None:
    """Archidekt's primary category for a row is its first one. A card filed under Creature then
    an excluded Stax counts in the deck; one filed under 1v1 Sideboard then Creature does not. A
    live 60-card Oathbreaker deck with rows like these came to 60 this way (63 under "any included
    category")."""
    cats = [
        {"name": "Creature", "includedInDeck": True},
        {"name": "Stax", "includedInDeck": False},
        {"name": "1v1 Sideboard", "includedInDeck": False},
    ]
    d = deck(
        [
            card("Strict Proctor", 1, categories=["Creature", "Stax"]),
            card("Rug of Smothering", 1, categories=["1v1 Sideboard", "Creature", "Stax"]),
            card("Opt", 1, categories=[]),
        ],
        categories=cats,
    )
    assert [c.name for c in d.main_cards] == ["Strict Proctor", "Opt"]
    assert [c.name for c in d.side_cards] == ["Rug of Smothering"]
    assert d.categories_count(["Stax", "Creature"]) is False and d.categories_count(None) is True


def test_companion_and_bracket_mismatch_checks() -> None:
    cat = [{"name": "Commander", "isPremier": True, "includedInDeck": True}]
    islands = card("Island", 99, types=["Land"], supertypes=["Basic"])
    lurrus = card("Lurrus of the Dream-Den", 1, companion=True, oracle_text="Companion — Each...",
                  categories=["Sideboard"])  # fmt: skip
    ok = deck([_cmdr("Aesi"), islands, lurrus], categories=cat)
    assert compute(ok)["checks"]["companion"] == {"count": 1, "ok": True}
    bad = deck([_cmdr("Aesi"), islands, card("Opt", 1, companion=True, oracle_text="Scry 1. Draw a card.")],
               categories=cat)  # fmt: skip
    checks = compute(bad)["checks"]
    assert checks["companion"]["ok"] is False and checks["companion"]["not_companions"] == ["Opt"]
    low = deck([_cmdr("Aesi"), card("Island", 97, types=["Land"], supertypes=["Basic"]),
                card("Time Warp", 1, extra_turns=True), card("Temporal Mastery", 1, extra_turns=True)],
               categories=cat)  # fmt: skip
    low.edh_bracket = 2
    # archidekt.com's bracket 2 allows extra-turn cards (its estimate of 3 aside); bracket 1 does not
    checks = compute(low)["checks"]
    assert checks["bracket"] == {"set": 2, "estimate": 3, "ok": True} and checks["ok"] is True
    low.edh_bracket = 1
    checks = compute(low)["checks"]
    assert checks["bracket"]["ok"] is False and checks["ok"] is False and checks["legal"] is True
    assert checks["problems"] == [
        "bracket set to 1 but the deck has extra-turn cards (Temporal Mastery, Time Warp)"
    ]


# -- Archidekt's extra stats: curve by type or category, lands-only sources, out-of-identity pips,
# subtype and keyword counts ------------------------------------------------------------------


def _extra_deck() -> Deck:
    return deck(
        [
            card("Aesi", 1, categories=["Commander"], types=["Creature"], cmc=6.0, mana_cost="{4}{G}{U}",
                 color_identity=["Green", "Blue"], subtypes=["Serpent"]),
            card("Coiling Oracle", 1, categories=["Ramp"], types=["Creature"], cmc=2.0, mana_cost="{G}{U}",
                 subtypes=["Snake", "Elf", "Druid"]),
            card("Ornithopter", 1, types=["Artifact", "Creature"], cmc=0.0, subtypes=["Thopter"],
                 keywords=["Flying"]),
            card("Bolt", 2, categories=["Removal"], types=["Instant"], cmc=1.0, mana_cost="{R}"),
            card("Fire // Ice", 1, categories=["Removal"], types=["Instant"], cmc=2.0, mana_cost="{1}{R/U}"),
            card("Sol Ring", 1, categories=["Ramp"], types=["Artifact"], cmc=1.0, mana_cost="{1}",
                 mana_production={"C": 2}),
            card("Mulldrifter", 1, categories=["Draw"], types=["Creature"], cmc=5.0, mana_cost="{4}{U}",
                 subtypes=["Elemental"], keywords=["Flying", "Evoke"]),
            card("Forest", 3, categories=["Land"], types=["Land"], supertypes=["Basic"], subtypes=["Forest"],
                 mana_production={"G": 1}),
            card("Command Tower", 1, categories=["Land"], types=["Land"], mana_production={"G": 1, "U": 1}),
        ],
        categories=[{"name": "Commander", "isPremier": True, "includedInDeck": True}],
    )  # fmt: skip


def test_curve_by_type_and_category_add_up_to_the_curve() -> None:
    s = compute(_extra_deck())
    by_type, by_cat = s["mana_curve_by_type"], s["mana_curve_by_category"]
    assert list(by_type) == list(by_cat) == list(s["mana_curve"])
    for b, n in s["mana_curve"].items():
        assert sum(by_type[b].values()) == n == sum(by_cat[b].values())
    # an Artifact Creature counts once, as a Creature; an uncategorised card under its type
    assert by_type["0"] == {"Creature": 1} and by_cat["0"] == {"Creature": 1}
    assert by_type["1"] == {"Instant": 2, "Artifact": 1}
    assert by_cat["1"] == {"Removal": 2, "Ramp": 1}
    assert by_cat["6"] == {"Commander": 1} and by_type["7+"] == {}


def test_land_only_sources_and_out_of_identity_pips() -> None:
    s = compute(_extra_deck())
    assert s["mana_sources"] == {"U": 1, "G": 4, "C": 2}
    assert s["mana_sources_lands"] == {"U": 1, "G": 4}
    ooi = s["out_of_identity"]
    assert ooi["identity"] == ["U", "G"]
    # two Bolts (a red pip each) and Fire // Ice's hybrid {R/U}, half of it outside
    assert ooi["pips"] == {"R": 2.5}
    assert ooi["cards"] == [{"name": "Bolt", "colours": ["R"]}, {"name": "Fire // Ice", "colours": ["R"]}]


def test_out_of_identity_is_none_without_a_commander() -> None:
    d = _extra_deck()
    for c in d.cards:
        c.categories = [x for x in c.categories if x != "Commander"]
    assert compute(d)["out_of_identity"] is None


def test_subtype_and_keyword_counts_count_copies() -> None:
    s = compute(_extra_deck())
    assert list(s["subtype_counts"])[0] == "Forest" and s["subtype_counts"]["Forest"] == 3
    assert s["subtype_counts"]["Snake"] == 1 and "Thopter" in s["subtype_counts"]
    assert s["keyword_counts"] == {"Flying": 2, "Evoke": 1}


def test_parse_deck_reads_archidekt_keywords() -> None:
    d = live("sample")
    boots = next(c for c in d.cards if c.name == "Swiftfoot Boots")
    assert boots.keywords == ["Equip"]
    assert compute(d)["keyword_counts"]["Landfall"] == 9


def test_stats_panel_draws_the_controls_and_focus_targets() -> None:
    from mtg_gateway.deckpage import stats_panel_html

    d = _extra_deck()
    html = stats_panel_html(d, compute(d))
    # every variant is drawn; the toolbars wait for the script (hidden); None, Bar and all sources show
    assert html.count("class='statctl'") == 2 and "aria-label='Mana curve by' hidden" in html
    for v in ("none", "type", "category"):
        assert f"data-curve='{v}'" in html
    assert "data-curve='type' hidden" in html and "data-src='lands' hidden" in html
    assert "aria-pressed='true'>None</button>" in html and "aria-pressed='true'>Bar</button>" in html
    assert "data-set='lands' aria-pressed='false'" in html
    assert "aria-label='Mana curve by type, cards per mana value: 0: 1 (1 Creature); 1: 3" in html
    assert "<svg viewBox='0 0 100 100' role='img' aria-label='Mana cost by color:" in html
    assert "aria-label='Production (lands only) by color: Blue 1 mana (20%), Green 4 mana (80%)'" in html
    assert "2.5 pips outside the commander's color identity" in html
    assert "data-focus='[&quot;bolt&quot;, &quot;fire // ice&quot;]'" in html
    assert "Quantity by subtype" in html and "Quantity by keyword" in html
    assert ">Evoke</button></td><td>1</td>" in html


# -- archidekt.com's legality rules (its deck page's checker, read 2026-10-11) -------------------

_CAT = [{"name": "Commander", "isPremier": True, "includedInDeck": True}]


def _basics(n: int) -> DeckCard:
    return card("Island", n, types=["Land"], supertypes=["Basic"])


def test_copies_are_counted_by_name_across_rows_and_the_sideboard() -> None:
    """Two rows of Sol Ring (two printings) break the singleton rule like one row of two; a
    sideboard copy counts too. The sideboard is not part of the deck's size."""
    d = deck([_cmdr("Aesi"), card("Sol Ring", 1), card("Sol Ring", 1, set_code="c21"), _basics(97),
              card("Sol Ring", 1, categories=["Sideboard"])], categories=_CAT)  # fmt: skip
    checks = compute(d)["checks"]
    assert checks["deck_size"]["actual"] == 100 and checks["deck_size"]["ok"] is True
    assert checks["singleton_violations"] == [{"name": "Sol Ring", "quantity": 3}]
    assert checks["legal"] is False
    assert checks["legal_problems"] == ["more than one copy in a singleton format: Sol Ring ×3"]


def test_cards_allowed_in_any_number_or_up_to_a_count() -> None:
    d = deck([_cmdr("Aesi"), card("Seven Dwarves", 7), card("Persistent Petitioners", 20), _basics(72)],
             categories=_CAT)  # fmt: skip
    assert compute(d)["checks"]["singleton_violations"] == []
    d = deck([_cmdr("Aesi"), card("Seven Dwarves", 8), _basics(91)], categories=_CAT)
    assert compute(d)["checks"]["singleton_violations"] == [{"name": "Seven Dwarves", "quantity": 8}]


def test_commander_must_be_legendary_and_able_to_lead() -> None:
    plain = _cmdr("Grizzly Bears", supertypes=[])
    checks = compute(deck([plain, _basics(99)], categories=_CAT))["checks"]
    assert checks["problems"] == ["a commander must be legendary: Grizzly Bears"]
    # a legendary Vehicle with power and toughness may lead; Grist may
    vehicle = _cmdr("Parhelion II", types=["Artifact"], subtypes=["Vehicle"], power="5", toughness="5")
    assert compute(deck([vehicle, _basics(99)], categories=_CAT))["checks"]["legal"] is True
    grist = _cmdr("Grist, the Hunger Tide", types=["Planeswalker"])
    assert compute(deck([grist, _basics(99)], categories=_CAT))["checks"]["legal"] is True
    # Standard Brawl lets a planeswalker lead; Commander does not
    walker = _cmdr("Saheeli", types=["Planeswalker"])
    assert compute(deck([walker, _basics(59)], format_id=13, categories=_CAT))["checks"]["legal"] is True
    zone = compute(deck([walker, _basics(99)], categories=_CAT))["checks"]["commander_zone"]
    assert zone["cannot_command"] == ["Saheeli"]


def test_partner_kinds_must_match() -> None:
    a = _cmdr("Rick", "Partner—Survivors (You can have two commanders if both have this ability.)")
    b = _cmdr("Daryl", "Partner—Survivors")
    assert compute(deck([a, b, _basics(98)], categories=_CAT))["checks"]["legal"] is True
    c = _cmdr("Tymna", "Partner")
    checks = compute(deck([a, c, _basics(98)], categories=_CAT))["checks"]
    assert checks["commander_zone"]["pairing"] == "invalid" and checks["legal"] is False


def test_pauper_edh_uses_archidekts_leader_flag() -> None:
    legal = {"paupercommander": "legal"}
    commons = card("Island", 99, types=["Land"], supertypes=["Basic"], legalities=legal)
    no = _cmdr("Big Rare", rarity="rare", supertypes=[], is_pdh_commander=False)
    checks = compute(deck([no, commons], format_id=17, categories=_CAT))["checks"]
    assert checks["legal"] is False and "not a valid Pauper EDH commander" in checks["problems"][0]
    yes = _cmdr("Rare Print", rarity="rare", supertypes=[], is_pdh_commander=True)
    checks = compute(deck([yes, commons], format_id=17, categories=_CAT))["checks"]
    assert checks["legal"] is True and "unverified" not in checks["commander_zone"]


def test_duel_commander_banned_as_commander_and_canlander_points() -> None:
    leader = _cmdr("Ezuri", legalities={"duel": "restricted"})
    checks = compute(deck([leader, _basics(99)], format_id=12, categories=_CAT))["checks"]
    assert checks["problems"] == ["banned as a commander in Duel Commander: Ezuri"]
    can = deck([card("Sol Ring", 1, canlander_points=4), card("Mana Crypt", 1, canlander_points=7),
                _basics(98)], format_id=25)  # fmt: skip
    checks = compute(can)["checks"]
    assert checks["canlander_points"] == {"total": 11, "maximum": 10, "ok": False}
    assert checks["problems"] == ["11 Canadian Highlander points; at most 10"]


def test_companion_conditions() -> None:
    gyruda = card("Gyruda, Doom of Depths", 1, companion=True, categories=["Sideboard"])
    d = deck([_cmdr("Aesi", cmc=6.0), card("Opt", 1, cmc=1.0), _basics(98), gyruda], categories=_CAT)
    checks = compute(d)["checks"]
    assert checks["companion"]["ok"] is False
    assert checks["problems"] == ["companion Gyruda, Doom of Depths: its condition is not met: Opt"]
    yorion = card("Yorion, Sky Nomad", 1, companion=True, categories=["Sideboard"])
    checks = compute(deck([_cmdr("Aesi"), _basics(99), yorion], categories=_CAT))["checks"]
    assert "exact deck size" in checks["problems"][0]


def test_bracket_set_on_the_deck_against_its_cards() -> None:
    gcs = [card(f"GC{i}", 1, game_changer=True) for i in range(4)]
    d = deck([_cmdr("Aesi"), *gcs, _basics(95)], categories=_CAT)
    d.edh_bracket = 3
    mismatches = compute(d)["checks"]["bracket"]["mismatches"]
    assert mismatches == ["more than three game changers (GC0, GC1, GC2, GC3)"]
    d = deck([_cmdr("Aesi"), card("A", 1, oracle_id="1", atomic_combos=["2"]),
              card("B", 1, oracle_id="2", atomic_combos=["1"]), _basics(97)], categories=_CAT)  # fmt: skip
    d.edh_bracket = 2
    assert compute(d)["checks"]["bracket"]["mismatches"] == ["two-card infinite combos (A, B)"]
    d.edh_bracket = 4
    assert compute(d)["checks"]["bracket"]["ok"] is True
    # a clean deck set to bracket 1 is not a mismatch (the estimate's floor of 2 aside)
    d = deck([_cmdr("Aesi"), _basics(99)], categories=_CAT)
    d.edh_bracket = 1
    assert compute(d)["checks"]["ok"] is True


def test_live_payload_carries_the_legality_fields() -> None:
    d = live("blink_sample")
    teferi = next(c for c in d.cards if c.name == "Teferi's Protection")
    assert teferi.atomic_combos == ["120"] and teferi.oracle_id == "16663"
    assert next(c for c in d.cards if c.name == "Mana Drain").canlander_points == 1
    assert next(c for c in d.cards if c.name == "Wall of Omens").is_pdh_commander is True


def test_mana_sources_stay_inside_the_commanders_colors() -> None:
    """A source of any color (Command Tower, an any-color land) counts for the commanders' colors
    only in the commander formats (backlog 13); colorless stays."""
    s = compute(live("blink_sample"))  # Brago: white and blue
    assert set(s["mana_sources"]) == {"W", "U", "C"} and set(s["mana_sources_lands"]) <= {"W", "U", "C"}
    field = card("Lotus Field", 1, types=["Land"], mana_production={"W": 3, "U": 3, "B": 3, "R": 3, "G": 3})
    mono = deck([_cmdr("Aesi", color_identity=["Blue"]), field, _basics(98)], categories=_CAT)
    assert compute(mono)["mana_sources"] == {"U": 3}
    # a constructed deck keeps every color the card can make
    sixty = deck([field, _basics(59)], format_id=2)
    assert set(compute(sixty)["mana_sources"]) == {"W", "U", "B", "R", "G"}


def test_card_rows_say_where_an_uncategorised_card_is_shown() -> None:
    """Get_deck's rows had categories [] for uncategorised cards while deck_stats counted
    them under Archidekt's auto category; the row now names it (shown_under)."""
    from mtg_gateway.views import deck_out

    d = deck(
        [
            _cmdr("Aesi"),
            card("Cultivate", 1, types=["Sorcery"], default_category="Ramp"),
            card("Opt", 1, types=["Instant"]),
            card("Sol Ring", 1, categories=["Mana"], types=["Artifact"], default_category="Ramp"),
        ],
        categories=_CAT,
    )
    rows = {r["name"]: r for r in deck_out(d, view="cards")["cards"]}
    assert rows["Cultivate"]["categories"] == [] and rows["Cultivate"]["shown_under"] == "Ramp"
    assert rows["Opt"]["shown_under"] == "Instant"  # no auto category: its type
    assert "shown_under" not in rows["Sol Ring"] and "shown_under" not in rows["Aesi"]


def test_legality_chip_and_panel_list_every_failing_rule() -> None:
    """The deck page shows every rule the deck fails, with its reason and cards, not the
    first five (the chip) or the cards' own statuses only (the panel)."""
    from mtg_gateway.deckpage import legality_chip_html, legality_panel_html

    d = deck(
        [
            _cmdr("Aesi", color_identity=["U"], legalities={"commander": "legal"}),
            card("Sol Ring", 2, legalities={"commander": "legal"}),
            card("Mana Crypt", 1, legalities={"commander": "banned"}),
            card("Bolt", 1, color_identity=["R"], legalities={"commander": "legal"}),
            card("Rule Breaker", 1, legalities={"commander": "not_legal"}),
            card("Opt", 3, legalities={"commander": "legal"}),
            card("Island", 50, types=["Land"], supertypes=["Basic"], legalities={"commander": "legal"}),
        ],
        categories=_CAT,
    )
    stats = compute(d)
    problems = stats["checks"]["legal_problems"]
    assert problems == [
        "deck has 59 cards; commander wants 100",
        "outside the commander's color identity: Bolt (R)",
        "more than one copy in a singleton format: Sol Ring ×2, Opt ×3",
        "banned: Mana Crypt",
        "not legal in commander: Rule Breaker",
    ]
    chip = legality_chip_html(d, stats)
    assert "class='legal bad'" in chip and "5 problems" in chip
    panel = legality_panel_html(d, stats)
    assert "Not legal in Commander:" in panel
    for p in problems:
        assert f"<li>{p}</li>" in panel.replace("&#x27;", "'"), p


# -- 0.7.24 consolidation: one legality engine, custom mana value, one type helper ------------------
_SIDE = [{"name": "Sideboard", "isPremier": False, "includedInDeck": False}]


def test_legality_problems_come_from_deck_checks() -> None:
    """Bug 8: deck_stats' legality_problems (the report strip, the edit API, the deck_stats tool)
    flagged a single restricted card and ignored the sideboard; it is now deck_checks' verdict."""
    from mtg_gateway.deck_stats import card_legality_problems

    lands = card("Island", 59, types=["Land"], supertypes=["Basic"], legalities={"vintage": "legal"})
    one = deck(
        [
            card("Black Lotus", 1, legalities={"vintage": "restricted"}),
            lands,
            card("Mental Misstep", 1, categories=["Sideboard"], legalities={"vintage": "banned"}),
        ],
        format_id=5,
        categories=_SIDE,
    )
    s = compute(one)
    # one restricted copy is legal; the banned card in the sideboard is counted, as on Archidekt
    assert s["legality_problems"] == [{"name": "Mental Misstep", "status": "banned"}]
    assert s["legality_problems"] == card_legality_problems(s["checks"])
    two = deck([card("Black Lotus", 2, legalities={"vintage": "restricted"}), lands], format_id=5)
    assert compute(two)["legality_problems"] == [{"name": "Black Lotus", "status": "restricted"}]
    # Archidekt's free format is not checked at all
    free = compute(deck([card("x", legalities={"custom": "banned"})], format_id=7))
    assert free["legality_problems"] == [] and free["legality_unknown"] == 0


def test_stored_statistics_are_read_with_the_deck_checks_verdict() -> None:
    """A report stored before the merge kept the old list; the stats strip reads its checks."""
    from mtg_gateway.companion import stats_strip
    from mtg_gateway.deck_stats import legality_problems_of

    old = {
        "legality_problems": [{"name": "Black Lotus", "status": "restricted"}],
        "checks": {"legality": {"banned": [], "not_legal": [], "restricted_violations": [], "unknown": 0}},
    }
    assert legality_problems_of(old) == []
    assert "Not legal" not in stats_strip({**old, "card_count": 60})
    older = {"legality_problems": [{"name": "Mana Crypt", "status": "banned"}]}  # no checks stored
    assert legality_problems_of(older) == older["legality_problems"]
    assert "Not legal in this format: Mana Crypt" in stats_strip({**older, "card_count": 100})


def test_a_pasted_list_is_held_to_deck_checks_rules_in_its_words() -> None:
    from mtg_gateway.deck_stats import compute_from_text, size_problem, validation_gateway_checks

    lst = [
        ListCard(1, "Aesi", categories=["Commander"]),
        ListCard(7, "Seven Dwarves"),
        ListCard(2, "Sol Ring"),
    ]
    lst += [ListCard(80, "Island")]
    checks = compute_from_text(lst)["checks"]
    assert checks["singleton_violations"] == [{"name": "Sol Ring", "quantity": 2}]  # the dwarves may
    assert size_problem("commander", 90) in checks["problems"]
    assert "deck has 90 cards; commander wants 100" in checks["problems"]
    three = [ListCard(1, n, categories=["Commander"]) for n in ("A", "B", "C")] + [ListCard(97, "Island")]
    assert (
        "3 cards in the Commander category (expected 1 or 2)"
        in compute_from_text(three)["checks"]["problems"]
    )
    dupes, _text = validation_gateway_checks("1 Sol Ring\n1 Sol Ring\n9 Nazgûl\n10 Nazgûl\n30 Forest")
    assert dupes == [{"name": "Sol Ring", "quantity": 2}, {"name": "Nazgûl", "quantity": 19}]


def test_custom_mana_value_drives_curve_average_and_split_curves() -> None:
    """Bug 9: Archidekt's custom mana value (customCmc) was saved but ignored by every figure."""
    from mtg_gateway.deck_stats import curve_bucket, mana_value

    big = card("Big", 1, cmc=9.0, custom_cmc=2.0, types=["Creature"])
    zero = card("Zero", 1, cmc=3.0, custom_cmc=0.0, types=["Instant"])  # 0 is a real override
    plain = card("Plain", 1, cmc=4.0, types=["Sorcery"])
    assert mana_value(big) == 2.0 and mana_value(zero) == 0.0 and mana_value(plain) == 4.0
    assert curve_bucket(big) == "2" and curve_bucket(zero) == "0"
    s = compute(deck([big, zero, plain]))
    assert s["mana_curve"]["2"] == 1 and s["mana_curve"]["7+"] == 0 and s["mana_curve"]["0"] == 1
    assert s["average_mana_value"] == 2.0
    assert s["mana_curve_by_type"]["2"] == {"Creature": 1}
    # the format rules keep the printed value (a companion's condition, Maular's exemption...)
    assert big.cmc == 9.0


def test_custom_mana_value_on_the_deck_page() -> None:
    from mtg_gateway.deckpage import _odds_groups, card_row, group_cards, sort_cards

    big = card("Big", 1, cmc=9.0, custom_cmc=2.0, types=["Creature"])
    two = card("Two", 1, cmc=2.0, types=["Creature"])
    one = card("One", 1, cmc=1.0, types=["Instant"])
    d = deck([big, two, one])
    groups = dict(group_cards(d, "mv", "name"))
    assert [c.name for c in groups["2 mana value"]] == ["Big", "Two"] and "9 mana value" not in groups
    assert [c.name for c in sort_cards([big, two, one], "mv")] == ["One", "Big", "Two"]
    assert _odds_groups(d.cards)["Mana value"] == {"2": 2, "1": 1}
    row = card_row(big, deck=d, view="table")
    assert "<span class='mv' title='Custom mana value; printed 9'>2</span>" in row
    assert "<span class='mv'>2</span>" in card_row(two, deck=d, view="table")


def test_one_type_order_and_auto_category() -> None:
    """The deck page, the stats and get_deck's shown_under share one helper."""
    from mtg_gateway import deckpage, views
    from mtg_gateway.deck_stats import PRIMARY_TYPES, auto_category, primary_category, primary_type

    assert views.auto_category is auto_category and deckpage.TYPE_ORDER == list(PRIMARY_TYPES)
    assert primary_type(card("a", types=["Artifact", "Creature"])) == "Creature"
    assert primary_type(card("k", types=["Kindred Instant"])) == "Instant"  # a word, as views had it
    assert primary_type(card("l", types=["land"])) == "Land"  # any case, as deck_stats had it
    assert primary_type(card("s", types=["Scheme"])) == "Other"
    assert auto_category(card("d", default_category="Ramp", types=["Sorcery"])) == "Ramp"
    assert primary_category(card("c", categories=["Draw", "Ramp"])) == "Draw"
    odd = card("Odd", types=["Conspiracy"])
    d = deck([odd, card("Bear", types=["Creature"])])
    assert [n for n, _ in deckpage.group_cards(d, "type", "name")] == ["Creature", "Other"]
    assert views.card_out(d, odd)["shown_under"] == "Other"


def test_list_rows_come_from_one_builder() -> None:
    """Text, Table and Brewer rows are one builder; the markup each view had is kept."""
    from mtg_gateway.deckpage import TABLE_HEAD, card_row, cards_html

    c = card("Opt", 2, cmc=1.0, mana_cost="{U}", types=["Instant"], set_code="xln",
             collector_number="65", price=0.25, oracle_text="Scry 1.\nDraw a card.")  # fmt: skip
    d = deck([c])
    text = card_row(c, deck=d, view="text")
    assert text.startswith("<li class='row' tabindex='0' role='button' data-name='opt' data-card='Opt'")
    assert "<span class='q'>2</span><span class='n'><span class='name' title='Opt'>Opt</span></span>" in text
    assert "<span class='set' title='XLN #65'>XLN</span><span class='price'>$0.25</span></li>" in text
    table = card_row(c, deck=d, view="table")
    assert "<span class='set' title='XLN #65'>XLN #65</span>" in table
    assert (
        "<span class='ty' title='Instant'>Instant</span><span class='mv'>1</span><span class='mc'>" in table
    )
    brew = card_row(c, deck=d, view="brewer")
    assert (
        brew.startswith("<li class='row brew' ") and "<span class='tx'>Scry 1.\nDraw a card.</span>" in brew
    )
    page = cards_html(d, view="table", group="none", sort="name", q="", own=False)
    assert TABLE_HEAD + table in page


def test_the_full_test_button_has_the_approved_texts() -> None:
    """The banner's approved texts, word for word: the manual page keeps the name Goldfish; the run
    is "Run full test"."""
    from mtg_gateway.deckpage import banner_html

    html = banner_html(deck([card("Opt")]), {}, own=True, csrf="t", writes_enabled=True)
    assert (
        "<button type='submit' data-busy-label='Testing…' title='The solo test (300 games, no opponents), "
        "a legality check and the deck statistics, plus Forge games against precons when Forge is on and "
        "the deck has a commander; saved under History. The solo test takes up to a minute; the result "
        "page opens when the solo test is done.'>" in html
    )
    assert " Run full test</button>" in html
    assert "Run simulation" not in html and "Simulating" not in html and "goldfish games" not in html
    assert " Goldfish</a>" in html and "Playtest</a>" in html  # names kept


def test_the_solo_test_only_run_is_offered_only_when_forge_is_on() -> None:
    """With Forge set up, More offers the same run without Forge games (forge=0), with its
    approved texts word for word."""
    from mtg_gateway.deckpage import banner_html

    off = banner_html(deck([card("Opt")]), {}, own=True, csrf="t", writes_enabled=True)
    on = banner_html(deck([card("Opt")]), {}, own=True, csrf="t", writes_enabled=True, forge_on=True)
    assert "Solo test only" not in off
    assert "name='forge' value='0'" in on
    assert (
        "<button type='submit' data-busy-label='Testing…' title='The same run without Forge games: the "
        "solo test, the legality check and the statistics'>" in on
    )
    assert " Solo test only</button>" in on and "Quick simulation" not in on
    signed_out = banner_html(
        deck([card("Opt")]), {}, own=False, csrf=None, writes_enabled=True, forge_on=True
    )
    assert "Solo test only" not in signed_out and "Run full test" not in signed_out
