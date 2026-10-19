"""The assistant's lists of proposals and snapshots: paging, the collection's proposals, where an
applied proposal was applied, and the cards a waiting change touches."""

from __future__ import annotations

from .conftest import Stack
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import call, linked_user, structured


def _add(card: str) -> dict:
    return {"deck_id": "42", "changes": [{"action": "add", "card_name": card}]}


async def test_list_my_proposals_pages_through_all_of_them_and_takes_the_collection(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    made = [structured(await call(h, token, "propose_deck_changes", _add(f"Card {i}"))) for i in range(25)]
    assert all(p["ok"] for p in made)
    first = structured(await call(h, token, "list_my_proposals"))
    assert len(first["proposals"]) == 20 and first["has_more"] is True and first["next_offset"] == 20
    assert first["proposals"][0]["id"] == made[-1]["proposal_id"]
    rest = structured(await call(h, token, "list_my_proposals", {"offset": first["next_offset"]}))
    assert len(rest["proposals"]) == 5 and rest["has_more"] is False and "next_offset" not in rest
    seen = {p["id"] for p in first["proposals"] + rest["proposals"]}
    assert seen == {p["proposal_id"] for p in made}
    # a limit past the most is held to it; oldest_first pages from the oldest
    big = structured(await call(h, token, "list_my_proposals", {"limit": 500, "oldest_first": True}))
    assert len(big["proposals"]) == 25 and big["proposals"][0]["id"] == made[0]["proposal_id"]
    # collection changes are filed under "collection", which narrows to them
    coll = structured(
        await call(h, token, "propose_collection_changes", {"add": [{"name": "Sol Ring", "quantity": 1}]})
    )
    assert coll["ok"], coll
    only = structured(await call(h, token, "list_my_proposals", {"deck_id": "collection"}))
    assert only["ok"], only
    assert [p["id"] for p in only["proposals"]] == [coll["proposal_id"]]
    board = structured(
        await call(h, token, "list_my_proposals", {"deck_id": "Collection", "pending_only": True})
    )
    assert [p["proposal_id"] for p in board["proposals"]] == [coll["proposal_id"]]
