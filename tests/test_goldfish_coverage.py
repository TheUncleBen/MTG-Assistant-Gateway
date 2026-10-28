"""The solo test's coverage at the top of a report (assistant tool review, 2026-10-28): a kill rate
said with no word that a third of the deck, the commander among it, was played without its effect.
The research service sends its result as text only, with the metrics in a JSON block."""

from __future__ import annotations

import json

from mtg_gateway.reports import _commander_aside, solo_coverage

HONESTY = {
    "out_of_scope": {"interaction_removal": [{"name": "Doom Blade", "drawn_pct": 0.18}]},
    "unrecognized": [
        {"name": "Aegis of the Gods", "drawn_pct": 0.11},
        {"name": "Urza, Chief Artificer", "drawn_pct": 0.0},
        {"name": "Sun Titan", "drawn_pct": 0.18},
    ],
    "modeled_low_impact": [],
}


def _text(honesty: dict) -> str:
    payload = {"n": 30, "seed": 1, "until_turn": 10, "run_id": "abc", "metrics": {"honesty": honesty}}
    return "\n".join(
        [
            "# Goldfish run",
            "Deck: 100 cards including commander | Commander: Urza, Chief Artificer | 30 games, seed 1, "
            "through turn 10 | run_id: abc",
            "",
            "## Summary",
            "- Kill (40 damage): reached 53.3% by T10",
            "",
            "## Metrics",
            "```json",
            json.dumps(payload),
            "```",
            "",
            "## Honesty report",
            "Unrecognized — 3 cards (candidates for annotation; see goldfish_annotate):",
            "- Aegis of the Gods (drawn 11%), Urza, Chief Artificer (drawn 0%), Sun Titan (drawn 18%)",
        ]
    )


def _block() -> dict:
    # what the gateway keeps of the research service's answer: its text, no structured data
    return {"tool": "goldfish_run", "ok": True, "text": _text(HONESTY)}


def test_the_commander_is_set_aside_in_a_text_only_result() -> None:
    gf = _block()
    _commander_aside(gf, "Urza, Chief Artificer")
    assert "Urza, Chief Artificer (drawn 0%)" not in gf["text"]
    assert "Unrecognized — 2 cards" in gf["text"]
    assert "Commander — Urza, Chief Artificer is cast from the command zone" in gf["text"]
    block = gf["text"].split("```json\n", 1)[1].split("\n```", 1)[0]
    honesty = json.loads(block)["metrics"]["honesty"]
    assert [c["name"] for c in honesty["unrecognized"]] == ["Aegis of the Gods", "Sun Titan"]
    assert honesty["commander_note"].startswith("Urza, Chief Artificer is cast")


def test_coverage_leads_the_report() -> None:
    gf = _block()
    _commander_aside(gf, "Urza, Chief Artificer")
    gf["quantities"] = {"Aegis of the Gods": 1, "Sun Titan": 1, "Doom Blade": 1}
    cov = solo_coverage(gf, deck_size=100)
    assert cov is not None
    assert (cov["modeled"], cov["deck_size"], cov["played_without_effect"]) == (97, 100, 2)
    assert cov["commander_modeled"] is False
    assert cov["note"].startswith("Played with their effect: 97 of 100 cards.")
    assert "2 cards were played without their effect" in cov["note"]
    assert "the commander's abilities beyond combat are not modeled" in cov["note"]
    assert "floor" in cov["note"]
    # saved card models are named: another assistant's model changes this run too
    gf["kept_models"] = [{"name": "Aetherflux Reservoir"}]
    assert "1 saved card model (Aetherflux Reservoir)" in solo_coverage(gf, deck_size=100)["note"]
    # no simulation, no coverage
    assert solo_coverage({"tool": "goldfish_run", "ok": False, "text": "x"}, deck_size=100) is None


def test_an_ab_says_each_commander_is_cast_from_the_command_zone() -> None:
    from mtg_gateway.reports import _ab_commanders_aside, _list_commander

    text = "\n".join(
        [
            "# Goldfish A/B",
            "## Deltas (A − B)",
            "- kill: Δ +0.1",
            "## Honesty report — Deck A",
            "Out of scope — 2 cards, counted by class:",
            "- political: Galadriel, Light of Valinor (drawn 0%), Ghostly Prison (drawn 12%)",
            "Unrecognized — 0 cards (candidates for annotation; see goldfish_annotate):",
            "- (none)",
            "## Honesty report — Deck B",
            "- political: Galadriel, Light of Valinor (drawn 4%)",
        ]
    )
    gf = {"tool": "goldfish_ab", "ok": True, "text": text}
    deck = "1 Galadriel, Light of Valinor [Commander]\n1 Ghostly Prison\n"
    assert _list_commander(deck) == "Galadriel, Light of Valinor"
    _ab_commanders_aside(gf, (_list_commander(deck), None))
    a, b = gf["text"].split("## Honesty report — Deck B")
    assert "Galadriel, Light of Valinor (drawn 0%)" not in a
    assert "Galadriel, Light of Valinor (commander, cast from the command zone;" in a
    assert "Ghostly Prison (drawn 12%)" in a
    # deck B's commander is another card: its line is left as it is
    assert "Galadriel, Light of Valinor (drawn 4%)" in b
