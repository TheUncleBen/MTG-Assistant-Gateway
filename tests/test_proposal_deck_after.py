"""Every deck edit says what the deck would be once applied: its legality and its price
(assistant tool review C-2/C-3, 2026-10-28: a deck named "$500 Target" ended at $860 and sat
illegal for hours, and no tool result said either). deck_after has the card count, the format's
verdict with every problem, the price total and the change, from the prices known: an added card
Scryfall has no price for is named, not guessed."""

from __future__ import annotations

from typing import Any

from .conftest import COLLECTION, Stack, stack_with
from .test_decks_and_proxy import call, linked_user, structured
from .test_tool_review import BOLT, commander_deck

stack_options = stack_with(COLLECTION)

UNPRICED: dict[str, Any] = {
    **BOLT,
    "id": "0b0b0b0b-0000-4000-8000-000000000001",
    "oracle_id": "0b0b0b0b-0000-4000-8000-000000000002",
    "name": "Unpriced Growth",
    "mana_cost": "{G}",
    "type_line": "Sorcery",
    "colors": ["G"],
    "color_identity": ["G"],
    "prices": {"usd": None},
}


async def _propose(stack: Stack, token: str, changes: list[dict[str, Any]]) -> dict[str, Any]:
    args = {"deck_id": "42", "changes": changes}
    return structured(await call(stack.h, token, "propose_deck_changes", args))


async def test_an_edit_says_the_decks_legality_and_price_once_applied(stack: Stack) -> None:
    commander_deck(stack)
    token = await linked_user(stack)
    before = structured(await call(stack.h, token, "deck_stats", {"deck_ref": "42"}))["stats"]
    raw = await call(
        stack.h,
        token,
        "propose_deck_changes",
        {"deck_id": "42", "changes": [{"action": "add", "name": "Lightning Bolt"}]},
    )
    p = structured(raw)
    after = p["deck_after"]
    assert after["card_count"] == 101 and after["legal"] is False, after
    assert any("101 cards" in x for x in after["problems"]), after
    assert after["price_change"] == 1.0, after  # Scryfall's price for the added Bolt
    assert after["price_total"] == round(before["price_total"] + 1.0, 2), (before["price_total"], after)
    assert after["unpriced_added"] == [], after
    # the plain text an app without cards shows says it as well
    assert "deck_after:" in "\n".join(c.get("text", "") for c in raw["content"]), raw


async def test_a_swap_inside_the_rules_keeps_the_verdict_and_names_an_added_card_without_a_price(
    stack: Stack,
) -> None:
    commander_deck(stack)
    stack.sf.cards.append(UNPRICED)
    token = await linked_user(stack)
    now = structured(await call(stack.h, token, "deck_stats", {"deck_ref": "42"}))["stats"]["checks"]
    p = await _propose(
        stack,
        token,
        [{"action": "add", "name": "Unpriced Growth"}, {"action": "remove", "name": "Acidic Slime"}],
    )
    after = p["deck_after"]
    # a swap inside the rules: the deck's verdict and problems as they are now, none added
    assert after["card_count"] == 100 and after["legal"] is now["legal"], (now, after)
    assert after["problems"] == now["problems"] and "legality_after" not in p, (now, after)
    assert after["unpriced_added"] == ["Unpriced Growth"], after
    # no card of this deck has a known price, before or after: no total and no change, not $0.00
    assert after["price_total"] is None and after["price_change"] is None, after
    assert "unpriced" in after["price_note"] or "no price" in after["price_note"], after


async def test_an_amend_says_it_too(stack: Stack) -> None:
    commander_deck(stack)
    token = await linked_user(stack)
    p = await _propose(stack, token, [{"action": "remove", "name": "Acidic Slime"}])
    assert p["deck_after"]["card_count"] == 99 and p["deck_after"]["legal"] is False, p["deck_after"]
    amended = structured(
        await call(
            stack.h,
            token,
            "amend_proposal",
            {
                "proposal_id": p["proposal_id"],
                "version": p["version"],
                "changes": [{"action": "add", "name": "Lightning Bolt"}],
            },
        )
    )
    assert amended["deck_after"]["card_count"] == 101, amended
