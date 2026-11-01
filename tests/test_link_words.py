"""One wording for each Archidekt link case (whole-application audit, row 12): every tool that
needs a linked account says the same thing when none is linked, a read Archidekt shows only to a
signed-in reader says to link rather than repeating Archidekt's own words, and a failed backup copy
says to relink when only relinking helps, never that the edit can wait for Archidekt."""

from __future__ import annotations

from mtg_gateway import refusals

from .conftest import Stack
from .test_decks_and_proxy import GATEWAY, call, linked_user, mcp_token, structured


async def test_every_tool_that_needs_a_link_says_so_in_one_wording(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)  # signed in, never linked
    words = refusals.not_linked(GATEWAY)
    assert words == f"No Archidekt account is linked. Link one at {GATEWAY}/account."
    for name, args in [
        ("list_my_decks", {}),
        ("list_collection", {}),
        ("get_deck_collaborators", {"deck_id": "42"}),
        ("get_deck_comments", {"deck_id": "42"}),  # a public deck
    ]:
        out = structured(await call(h, token, name, args))
        assert out["ok"] is False and out["error"] == "not_linked", (name, out)
        assert out["message"] == words, (name, out)


async def test_a_backup_archidekt_no_longer_accepts_says_relink(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "remove", "card_name": "Acidic Slime"}]},
        )
    )
    ark.fail_backup = 401
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] is False and a["error"] == "backup_failed", a
    assert "Relink your Archidekt account" in a["message"], a
    assert "once Archidekt answers" not in a["message"], a
    assert ark.patches == []
