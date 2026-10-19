"""The assistant's lists of proposals and snapshots: paging, the collection's proposals, where an
applied proposal was applied, and the cards a waiting change touches."""

from __future__ import annotations

from mtg_gateway import recheck

from .conftest import Stack
from .test_account_actions import _press
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


async def test_a_deck_deletion_snapshot_names_its_proposal_and_snapshots_page(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    edit = structured(await call(h, token, "propose_deck_changes", _add("Forest")))
    applied = structured(await call(h, token, "apply_proposal", {"proposal_id": edit["proposal_id"]}))
    assert applied["state"] == "applied", applied
    res = await call(h, token, "propose_delete_deck", {"deck_id": "42"})
    deletion = structured(res)
    done = await _press(h, token, res)
    snaps = structured(await call(h, token, "list_snapshots"))
    assert snaps["has_more"] is False
    gone = next(s for s in snaps["snapshots"] if s["snapshot_id"] == done["result"]["snapshot_id"])
    assert gone["proposal_id"] == deletion["proposal_id"]
    one = structured(await call(h, token, "list_snapshots", {"limit": 1}))
    assert len(one["snapshots"]) == 1 and one["has_more"] is True and one["next_offset"] == 1
    two = structured(await call(h, token, "list_snapshots", {"limit": 1, "offset": 1}))
    assert two["snapshots"][0]["snapshot_id"] != one["snapshots"][0]["snapshot_id"]
    assert structured(await call(h, token, "list_snapshots", {"deck_id": "43"}))["snapshots"] == []


async def test_get_proposal_says_where_an_applied_proposal_was_applied(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    edit = structured(await call(h, token, "propose_deck_changes", _add("Forest")))
    assert "applied_via" not in edit  # only an applied one says it
    await call(h, token, "apply_proposal", {"proposal_id": edit["proposal_id"]})
    got = structured(await call(h, token, "get_proposal", {"proposal_id": edit["proposal_id"]}))
    assert got["state"] == "applied" and got["applied_via"] == "assistant"
    res = await call(h, token, "propose_delete_deck", {"deck_id": "42"})
    await _press(h, token, res)
    got = structured(await call(h, token, "get_proposal", {"proposal_id": structured(res)["proposal_id"]}))
    assert got["state"] == "applied" and got["applied_via"] == "review_page"


async def test_the_board_lists_the_commanders_a_commander_change_names(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "set_commander", "card_name": "Sol Ring"}]},
        )
    )
    assert p["ok"], p
    line = structured(await call(h, token, "list_my_proposals", {"pending_only": True}))["proposals"][0]
    # the old commander's name holds a comma, so it is listed whole, not split at it
    assert "Sol Ring" in line["cards"] and "Aesi, Tyrant of Gyre Strait" in line["cards"]
    # a commander row saved before it listed its names still compares equal to one worked out now
    row = next(r for r in p["rows"] if r["kind"] == "commander")
    old = {k: v for k, v in row.items() if k != "names"}
    assert recheck._clean([old]) == recheck._clean([row])
