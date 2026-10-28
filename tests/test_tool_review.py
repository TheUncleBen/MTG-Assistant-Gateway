"""Fixes from the assistant tool review of the live gateway (2026-10-28): a proposal that would
make a legal deck illegal, a change that does nothing, the research tools' own lists fed back,
input refusals reported as such."""

from __future__ import annotations

from typing import Any

from .conftest import COLLECTION, Stack, stack_with
from .test_decks_and_proxy import call, linked_user, structured

stack_options = stack_with(COLLECTION)

BOLT: dict[str, Any] = {
    "object": "card",
    "id": "e3285e6b-3e79-4d7c-bf96-d920f973b80b",
    "oracle_id": "4457ed35-7c10-48c8-9776-456485fdf070",
    "name": "Lightning Bolt",
    "set": "m10",
    "collector_number": "146",
    "type_line": "Instant",
    "mana_cost": "{R}",
    "cmc": 1.0,
    "oracle_text": "Lightning Bolt deals 3 damage to any target.",
    "colors": ["R"],
    "color_identity": ["R"],
    "legalities": {"commander": "legal"},
    "games": ["paper"],
    "finishes": ["nonfoil", "foil"],
    "released_at": "2009-07-17",
    "prices": {"usd": "1.00"},
}


def commander_deck(stack: Stack) -> None:
    """Deck 42 as a Commander deck of Aesi (green and blue), 100 cards, legal."""
    deck = stack.ark.decks[42]
    deck["deckFormat"] = 3
    for row in deck["cards"]:
        oracle = row["card"]["oracleCard"]
        if oracle["name"].startswith("Aesi"):
            oracle["colorIdentity"] = ["Green", "Blue"]
    stack.sf.cards.append(BOLT)


async def test_an_add_that_breaks_the_deck_is_high_risk_and_says_why(stack: Stack) -> None:
    commander_deck(stack)
    token = await linked_user(stack)
    p = structured(
        await call(
            stack.h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "name": "Lightning Bolt"}]},
        )
    )
    assert p["ok"], p
    assert p["risk"] == "high", p
    assert "break" in p["risk_reason"] and "color identity" in p["risk_reason"], p["risk_reason"]
    assert p["assistant_may_apply"] is False, p
    after = p["legality_after"]
    assert any("Lightning Bolt (R)" in w for w in after), after
    assert any("101 cards" in w for w in after), after
    row = next(r for r in p["rows"] if r.get("name") == "Lightning Bolt")
    assert row["breaks"], row
    # the review's own line says it, wherever the diff is shown
    assert "breaks the deck's rules" in p["diff"], p["diff"]


async def test_a_change_inside_the_rules_stays_low_risk(stack: Stack) -> None:
    commander_deck(stack)
    token = await linked_user(stack)
    p = structured(
        await call(
            stack.h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "add", "name": "Sol Talisman"},
                    {"action": "remove", "name": "Acidic Slime"},
                ],
            },
        )
    )
    assert p["ok"] and p["risk"] == "low", p
    assert "legality_after" not in p, p
    # a second copy of a card in a singleton format is one
    p = structured(
        await call(
            stack.h,
            token,
            "amend_proposal",
            {
                "proposal_id": p["proposal_id"],
                "version": p["version"],
                "changes": [
                    {"action": "add", "name": "Cultivate"},
                    {"action": "remove", "name": "Acidic Slime"},
                ],
            },
        )
    )
    assert p["ok"] and p["risk"] == "high", p
    assert p["legality_after"] == ["more than one copy in a singleton format: Cultivate"], p


async def test_a_change_that_does_nothing_is_said_not_hidden(stack: Stack) -> None:
    token = await linked_user(stack)
    p = structured(
        await call(
            stack.h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "remove", "name": "Acidic Slime"},
                    # "Normal" is nonfoil, and the deck's Sol Ring already is
                    {"action": "set_finish", "name": "Sol Ring", "finish": "Normal"},
                ],
            },
        )
    )
    assert p["ok"], p
    assert p["unchanged"] == [{"action": "set_finish", "name": "Sol Ring"}], p
    assert any("Sol Ring" in w and "already" in w for w in p["warnings"]), p


# What edhrec_average_deck prints: markdown headings, the commander under "## Commander".
AVERAGE_DECK = (
    "# Average Deck for Aesi, Tyrant of Gyre Strait\n\n## Commander\n1 Aesi, Tyrant of Gyre Strait\n\n"
    "## Land\n99 Island\n\nTotal: 100 cards\n"
)


async def test_the_research_tools_own_list_runs_with_its_commander(stack: Stack) -> None:
    token = await linked_user(stack)
    rep = structured(await call(stack.h, token, "run_deck_report", {"deck_ref": AVERAGE_DECK, "games": 20}))
    assert rep["ok"] and rep["goldfish"]["ok"], rep
    assert "Commander: 1 Aesi, Tyrant of Gyre Strait" in rep["goldfish"]["text"], rep["goldfish"]


async def test_a_simulation_refused_for_its_input_says_so_in_its_code(stack: Stack) -> None:
    token = await linked_user(stack)
    # about 100 cards and no commander: refused before anything is simulated
    rep = await call(stack.h, token, "run_deck_report", {"deck_ref": "99 Island\n1 Sol Ring\n", "games": 20})
    out = structured(rep)
    assert out["ok"] is False and out["error"] == "simulation_failed", out
    assert out["error_code"] == "INVALID_ARGUMENT" and out["retryable"] is False, out
    # two decks of different sizes: the research service refuses the paired games
    lst = "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n// Lands\n99 Island\n"
    out = structured(
        await call(
            stack.h,
            token,
            "compare_decks",
            {"a_list": lst, "b_list": lst.replace("99 Island", "102 Island"), "simulate": True, "games": 20},
        )
    )
    assert out["ok"] is False and "same size" in out["message"], out
    assert out["error_code"] == "INVALID_ARGUMENT", out
