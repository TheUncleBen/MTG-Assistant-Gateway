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


async def test_removing_the_only_copy_twice_says_so(stack: Stack) -> None:
    from .test_track_a_board import _mode, _propose

    token = await linked_user(stack)
    twice = [{"action": "remove", "card_name": "Sol Ring"}] * 2
    p = structured(await call(stack.h, token, "propose_deck_changes", {"deck_id": "42", "changes": twice}))
    assert p["ok"] is False
    assert "is not in the deck" not in p["message"], p
    assert "removed more than once" in p["message"] and "1 copy" in p["message"], p
    # two waiting proposals that each remove it: combining them says the same, and why
    await _mode(stack.h, "manual")
    first = await _propose(stack.h, token, [{"action": "remove", "card_name": "Sol Ring"}])
    second = await _propose(
        stack.h, token, [{"action": "remove", "card_name": "Sol Ring"}, {"action": "add", "card_name": "Opt"}]
    )
    out = structured(
        await call(
            stack.h,
            token,
            "combine_proposals",
            {"proposal_ids": [first["proposal_id"], second["proposal_id"]]},
        )
    )
    assert out["ok"] is False and "removed more than once" in out["message"], out
    assert "More than one of these proposals removes it" in out["message"], out


async def test_an_overlap_comes_before_any_apply(stack: Stack) -> None:
    token = await linked_user(stack)
    await call(
        stack.h,
        token,
        "propose_deck_changes",
        {"deck_id": "42", "changes": [{"action": "add", "name": "Opt"}]},
    )
    p = structured(
        await call(
            stack.h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [{"action": "add", "name": "Opt"}, {"action": "add", "name": "Ponder"}],
            },
        )
    )
    assert p["overlaps"], p
    assert p["next_step"].startswith("Other waiting changes on this deck touch the same cards"), p[
        "next_step"
    ]


async def test_a_category_change_and_a_card_moved_through_it_overlap(stack: Stack) -> None:
    deck = stack.ark.decks[42]
    deck["categories"].append({"id": 77, "name": "Draw", "isPremier": False, "includedInDeck": True})
    for row in deck["cards"]:
        if row["card"]["oracleCard"]["name"] == "Compulsive Research":
            row["categories"] = ["Draw"]
    token = await linked_user(stack)
    moved = structured(
        await call(
            stack.h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "set_category", "name": "Compulsive Research", "category": "Card Draw"}
                ],
            },
        )
    )
    assert moved["ok"], moved
    renamed = structured(
        await call(
            stack.h,
            token,
            "propose_deck_categories",
            {"deck_id": "42", "changes": [{"category": "Draw", "rename_to": "Cards"}]},
        )
    )
    assert renamed["ok"], renamed
    no = moved["board_no"]
    assert renamed["overlaps"] == [
        f"“Draw”: this request renames it to “Cards”; #{no} moves Compulsive Research in or out of it."
    ], renamed


async def test_the_assistants_own_apply_says_so_and_a_clone_names_its_new_deck(stack: Stack) -> None:
    from .test_approval_modes import _set_mode

    token = await linked_user(stack)
    await _set_mode(stack.h, "semi")
    p = structured(await call(stack.h, token, "propose_clone_deck", {"deck_id": "42", "name": "Copy"}))
    assert p["ok"] and p["assistant_may_apply"], p
    out = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] and out["state"] == "applied", out
    assert out["applied_via"] == "assistant", out
    new = str(out["result"]["deck_id"])
    assert new != "42"
    assert out["new_deck_id"] == new and out["new_deck_url"].endswith(f"/decks/{new}"), out
    assert out["deck_id"] == "42"  # the source, as before


async def test_a_collection_add_in_a_finish_its_printing_lacks_is_warned(stack: Stack) -> None:
    token = await linked_user(stack)
    p = structured(
        await call(
            stack.h,
            token,
            "propose_collection_changes",
            {"text": "1 Sol Ring (FRC) 21 *F*\n1 Sol Ring (C21) 263 *F*"},
        )
    )
    assert p["ok"], p
    assert p["warnings"] == [
        "Sol Ring (FRC 21) is not made in foil; Scryfall lists nonfoil. Amend the finish, or the collection "
        "will hold a printing that does not exist."
    ], p


async def test_a_contest_that_does_not_exist_is_refused_when_proposed(stack: Stack) -> None:
    token = await linked_user(stack)
    out = structured(
        await call(
            stack.h,
            token,
            "propose_contest_entry",
            {"deck_id": "42", "contest": "999999999", "summary": "Sea monsters"},
        )
    )
    assert out["ok"] is False and out["error"] == "not_found", out
    assert "no forum thread 999999999" in out["message"], out
    stack.ark.forum_threads[881] = {"title": "Contest", "section": "Contests", "type": 7, "owner": "amy"}
    p = structured(
        await call(
            stack.h,
            token,
            "propose_contest_entry",
            {"deck_id": "42", "contest": "881", "summary": "Sea monsters"},
        )
    )
    assert p["ok"], p
    assert "Enter your deck “" in p["diff"] and " by " not in p["diff"].splitlines()[0], p["diff"]


async def test_check_deck_changes_reports_every_problem_with_a_suggestion(stack: Stack) -> None:
    stack.sf.cards.append(BOLT)
    stack.sf.fuzzy["lightnig bolt"] = BOLT
    stack.h.app.state.gateway.scan.names.set_names(["Opt", "Lightning Bolt", "Sol Ring"])
    token = await linked_user(stack)
    out = structured(
        await call(
            stack.h,
            token,
            "check_deck_changes",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "add", "name": "Opt", "set_code": "m19", "collector_number": "65"},
                    {"action": "remove", "name": "Opt"},
                    {"action": "add", "name": "Lightnig Bolt"},
                ],
            },
        )
    )
    errors = [p["error"] for p in out["problems"]]
    assert "unknown_cards" in errors and len(errors) >= 2, out
    unknown = next(p for p in out["problems"] if p["error"] == "unknown_cards")
    assert unknown["unknown_cards"] == ["Lightnig Bolt"], unknown
    assert unknown["did_you_mean"] == {"Lightnig Bolt": "Lightning Bolt"}, unknown
    assert "did you mean 'Lightning Bolt'" in unknown["message"], unknown


async def test_deck_stats_identity_is_the_commanders(stack: Stack) -> None:
    commander_deck(stack)
    deck = stack.ark.decks[42]
    bolt = {**deck["cards"][0], "id": 99999, "categories": []}
    bolt["card"] = {
        **bolt["card"],
        "oracleCard": {
            **bolt["card"]["oracleCard"],
            "name": "Lightning Bolt",
            "colorIdentity": ["Red"],
            "manaCost": "{R}",
        },
    }
    deck["cards"].append(bolt)
    token = await linked_user(stack)
    stats = structured(await call(stack.h, token, "deck_stats", {"deck_ref": "42"}))["stats"]
    assert stats["colour_identity"] == ["U", "G"], stats["colour_identity"]
    assert "R" in stats["cards_colour_identity"], stats["cards_colour_identity"]
    assert stats["out_of_identity"]["identity"] == ["U", "G"]


async def test_a_registration_sheet_for_an_illegal_deck_is_warned(stack: Stack) -> None:
    commander_deck(stack)
    token = await linked_user(stack)
    legal = structured(await call(stack.h, token, "get_registration_pdf", {"deck_id": "42"}))
    assert legal["ok"] and "warnings" not in legal, legal
    stack.ark.decks[42]["cards"][-1]["quantity"] += 3  # 103 cards
    out = structured(await call(stack.h, token, "get_registration_pdf", {"deck_id": "42"}))
    assert out["ok"] and out["pdf_url"], out
    assert out["warnings"] == [
        "The deck breaks its format's rules: the deck has 103 cards, not 100.",
        "The deck breaks its format's rules: more than one copy in a singleton format: Yavimaya Elder.",
    ], out


async def test_the_board_lists_a_category_change_under_categories(stack: Stack) -> None:
    stack.ark.decks[42]["categories"].append(
        {"id": 77, "name": "Draw", "isPremier": False, "includedInDeck": True}
    )
    token = await linked_user(stack)
    p = structured(
        await call(
            stack.h,
            token,
            "propose_deck_categories",
            {"deck_id": "42", "changes": [{"category": "Draw", "rename_to": "Cards"}]},
        )
    )
    assert p["ok"], p
    board = structured(await call(stack.h, token, "list_my_proposals", {"pending_only": True}))
    line = next(x for x in board["proposals"] if x["proposal_id"] == p["proposal_id"])
    assert line["cards"] == [] and line["categories"] == ["Draw"], line
