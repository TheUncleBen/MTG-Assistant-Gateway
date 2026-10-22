"""propose_clone takes an optional folder of the member's own: the copy lands there instead of
the root folder, keeps the "Copy of - X" name by default, and a folder that is not the member's
is refused before anything is proposed (UI overhaul 2026-10-21, package C)."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import pytest

from mtg_gateway.decks import DeckError

from .conftest import Stack
from .test_decks_and_proxy import linked_user

SUB = "user-1"  # the signed-in member of linked_user


async def _apply(stack: Stack, pid: str) -> dict:
    """Applied as the member's own press in the browser is (companion.apply_now)."""
    out = await stack.h.app.state.gateway.decks.apply(SUB, pid, via="browser")
    assert out.get("state") == "applied", out
    return out


async def test_a_clone_into_a_folder_lands_there_with_the_default_name(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    decks = h.app.state.gateway.decks
    folder = await decks.create_folder(SUB, "Brews")
    p = await decks.propose_clone(SUB, "42", folder_id=int(folder["id"]))
    assert p["changes"]["folder_id"] == int(folder["id"]), p
    assert p["rows"][0]["folder"] == "Brews", p["rows"]
    before = set(ark.decks)
    await _apply(stack, p["proposal_id"])
    (new_id,) = set(ark.decks) - before
    assert ark.decks[new_id]["name"] == "Copy of - Sample Commander Deck"
    assert ark.deck_folder[new_id] == int(folder["id"])


async def test_a_clone_without_a_folder_still_goes_to_the_root_folder(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    decks = h.app.state.gateway.decks
    p = await decks.propose_clone(SUB, "42", "Plain copy")
    assert "folder_id" not in p["changes"], p
    before = set(ark.decks)
    await _apply(stack, p["proposal_id"])
    (new_id,) = set(ark.decks) - before
    assert ark.decks[new_id]["name"] == "Plain copy"
    assert ark.deck_folder[new_id] == 1000 + ark.users["alice"]["id"]


async def test_a_clone_into_a_folder_that_is_not_the_members_is_refused(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    decks = h.app.state.gateway.decks
    before = decks.db.count_pending_proposals(SUB)
    calls = len(ark.calls)
    with pytest.raises(DeckError) as exc:
        await decks.propose_clone(SUB, "42", folder_id=987654)
    assert exc.value.kind == "not_found"
    assert decks.db.count_pending_proposals(SUB) == before
    assert all(m == "GET" for m, _p in ark.calls[calls:]), ark.calls[calls:]
