"""An edit that was not shown as a commander change must not end with other commanders.

The risk tiers are worked out from the deck's own categories. If Archidekt filed a card into the
commander zone some other way (a category new to the deck taking the Premier flag of the member's
category template: not confirmed either way, the fake models it), a low-risk edit an assistant
applied by itself would change the commander unseen. The read-back now ends such an apply as a
mismatch with its snapshot named."""

from __future__ import annotations

from .conftest import Stack, StackOptions, stack_with
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import call, linked_user, structured

stack_options = stack_with(StackOptions(settings={"writes_enabled": True}))


async def _move(stack: Stack, token: str, category: str) -> dict:
    p = structured(
        await call(
            stack.h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "add", "card_name": "Arcane Signet", "quantity": 1, "category": category}
                ],
            },
        )
    )
    assert p["ok"] and p["assistant_may_apply"] is True, p
    return structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))


async def test_a_new_category_that_turns_out_premier_ends_as_a_mismatch(stack: Stack) -> None:
    token = await linked_user(stack)
    await _set_mode(stack.h, "auto")
    stack.ark.premier_templates = {"Generals"}
    got = await _move(stack, token, "Generals")
    assert got.get("state") == "failed" or got.get("ok") is False, got
    assert "which cards are the commanders" in str(got) and "snapshot" in str(got).lower(), got


async def test_a_new_plain_category_applies_as_before(stack: Stack) -> None:
    token = await linked_user(stack)
    await _set_mode(stack.h, "auto")
    got = await _move(stack, token, "Rocks")
    assert got["state"] == "applied", got
