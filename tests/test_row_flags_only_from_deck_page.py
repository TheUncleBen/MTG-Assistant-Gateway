"""Review 2 N1: an add's companion and "flipped by default" flags come only from the deck page's
own edit (its Undo of a removal). The proposal review and the chat card don't show them, so an
assistant's proposal, or one made through ``/api/v1/proposals``, drops them: the card is added
without either flag."""

from __future__ import annotations

from mtg_gateway.approve import APPROVAL_META_KEY

from .conftest import Stack, StackOptions, stack_with
from .test_browse_collection import api, linked
from .test_decks_and_proxy import call, linked_user, structured

stack_options = stack_with(StackOptions(settings={"writes_enabled": True, "approval_mode_default": "manual"}))

_ADD = {"action": "add", "card_name": "Opt", "quantity": 1, "companion": True, "flipped_default": True}


def _flags(st: Stack, name: str) -> list[tuple[bool, bool]]:
    return [
        (bool(c.get("companion")), bool(c.get("flippedDefault")))
        for c in st.ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == name
    ]


async def test_an_assistant_proposal_cannot_set_the_flags(stack: Stack) -> None:
    token = await linked_user(stack)
    res = await call(stack.h, token, "propose_deck_changes", {"deck_id": "42", "changes": [_ADD]})
    p = structured(res)
    assert p["ok"] is True, p
    assert "companion" not in str(p) and "flipped" not in str(p), p
    done = structured(
        await call(
            stack.h,
            token,
            "confirm_proposal",
            {"proposal_id": p["proposal_id"], "approval": res["_meta"][APPROVAL_META_KEY]},
        )
    )
    assert done["ok"] is True and done["state"] == "applied", done
    assert _flags(stack, "Opt") == [(False, False)]


async def test_a_proposal_through_the_api_cannot_set_the_flags(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await api(
            b,
            "POST",
            "/api/v1/proposals",
            {"kind": "edit", "deck_id": "42", "changes": [_ADD], "apply": True},
        )
        assert r.status_code == 201, r.text
        assert _flags(stack, "Opt") == [(False, False)]
    finally:
        await b.aclose()
