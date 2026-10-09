"""The simulation's words and names: double-faced cards reach the research service by their front
face, the commander is not filed as an unrecognised card, and the text never sends anyone to a
hidden tool (owner test round, T-041, 2026-10-09)."""

from __future__ import annotations

import copy

from mtg_gateway.decklist import front_faces
from mtg_gateway.mf_proxy import rewrite_text
from mtg_gateway.reports import _commander_aside, _front_face


def test_front_faces_keeps_everything_but_the_back_face() -> None:
    text = (
        "1 Enduring Angel // Angelic Enforcer (mid) 18 *F* [Creatures,Commander]\n"
        "2 Fire // Ice\n"
        "1 Liesa, Forgotten Archangel [Commander]\n"
        "1 Plains (znr) 266\n"
        "// Lands\n"
    )
    assert front_faces(text) == (
        "1 Enduring Angel (mid) 18 *F* [Creatures,Commander]\n"
        "2 Fire\n"
        "1 Liesa, Forgotten Archangel [Commander]\n"
        "1 Plains (znr) 266\n"
        "// Lands\n"
    )
    assert front_faces("") == "" and front_faces("3 Opt") == "3 Opt"
    assert _front_face("Enduring Angel // Angelic Enforcer") == "Enduring Angel"
    assert _front_face(None) is None


GOLDFISH = {
    "tool": "goldfish_run",
    "ok": True,
    "data": {
        "metrics": {
            "honesty": {
                "unrecognized": [
                    {"name": "Aegis of the Gods", "drawn_pct": {"value": 0.11}},
                    {"name": "Liesa, Forgotten Archangel", "drawn_pct": {"value": 0.0}},
                    {"name": "Sun Titan", "drawn_pct": {"value": 0.18}},
                ]
            }
        }
    },
    "text": (
        "## Honesty report\n"
        "Out of scope — 2 cards, inert, counted by class (the sim cannot value these):\n"
        "- interaction_removal: Doom Blade (drawn 18%), Murder (drawn 12%)\n"
        "Unrecognized — 3 cards (candidates for annotation; see goldfish_annotate):\n"
        "- Aegis of the Gods (drawn 11%), Liesa, Forgotten Archangel (drawn 0%), Sun Titan (drawn 18%)\n"
        "Modeled, low-impact — drawn but never fired:\n"
        "- Plains\n"
    ),
}


def test_the_commander_is_taken_out_of_the_unrecognized_list() -> None:
    gf = copy.deepcopy(GOLDFISH)
    _commander_aside(gf, "Liesa, Forgotten Archangel")
    honesty = gf["data"]["metrics"]["honesty"]
    assert [c["name"] for c in honesty["unrecognized"]] == ["Aegis of the Gods", "Sun Titan"]
    assert honesty["commander_note"].startswith("Liesa, Forgotten Archangel is cast from the command zone")
    lines = gf["text"].split("\n")
    assert lines[3] == "Unrecognized — 2 cards (candidates for annotation; see goldfish_annotate):"
    assert lines[4] == "- Aegis of the Gods (drawn 11%), Sun Titan (drawn 18%)"
    assert lines[5] == "Commander — " + honesty["commander_note"]
    assert lines[6:] == ["Modeled, low-impact — drawn but never fired:", "- Plains", ""]
    # Another commander, a failed run or a result without the list: nothing changes.
    for untouched in (
        (copy.deepcopy(GOLDFISH), "Sol Ring"),
        ({**copy.deepcopy(GOLDFISH), "ok": False}, "Liesa, Forgotten Archangel"),
        ({"tool": "goldfish_run", "ok": True, "text": "# Goldfish run"}, "Liesa, Forgotten Archangel"),
    ):
        before = copy.deepcopy(untouched[0])
        _commander_aside(*untouched)
        assert untouched[0] == before
    # The commander alone in the list: the list empties and the count says so.
    alone = copy.deepcopy(GOLDFISH)
    alone["data"]["metrics"]["honesty"]["unrecognized"] = [{"name": "Liesa, Forgotten Archangel"}]
    alone["text"] = (
        alone["text"]
        .replace("3 cards", "1 card")
        .replace(
            "- Aegis of the Gods (drawn 11%), Liesa, Forgotten Archangel (drawn 0%), Sun Titan (drawn 18%)",
            "- Liesa, Forgotten Archangel (drawn 0%)",
        )
    )
    _commander_aside(alone, "Liesa, Forgotten Archangel")
    assert "Unrecognized — 0 cards" in alone["text"] and "\n- (none)\n" in alone["text"]
    # A text line the parse cannot reproduce (an entry without a "(drawn N%)" tail): the data
    # is still fixed, the text is left alone rather than losing an entry.
    odd = copy.deepcopy(GOLDFISH)
    odd["text"] = odd["text"].replace("Sun Titan (drawn 18%)", "Sun Titan")
    before_text = odd["text"]
    _commander_aside(odd, "Liesa, Forgotten Archangel")
    assert odd["text"] == before_text
    assert "commander_note" in odd["data"]["metrics"]["honesty"]


def test_goldfish_report_is_never_named_in_a_result() -> None:
    # goldfish_report is a hidden Mystic Forge tool; its text used to send the assistant to it.
    out = rewrite_text('Full report: goldfish_report("wjyxinfngo4dg")\nValidation')
    assert "goldfish_report" not in out and out.startswith("This is the full report")
    assert out.endswith("(get_deck_report).\nValidation")
