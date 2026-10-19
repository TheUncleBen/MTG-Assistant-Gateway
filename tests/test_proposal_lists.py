"""The assistant's lists of proposals and snapshots: paging, the collection's proposals, where an
applied proposal was applied, and the cards a waiting change touches."""

from __future__ import annotations

import json

from mtg_gateway import recheck
from mtg_gateway.decks import applied_text

from .conftest import Stack
from .test_account_actions import _press
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .test_tool_reliability import mcp_token

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}


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


async def test_a_commander_change_saved_before_names_lists_its_commanders_once_the_deck_is_read(
    stack: Stack,
) -> None:
    """A commander row stored by 0.7.31 has no names; the next local check against the deck (a
    deck page view, a new proposal, an apply) fills them, so the board lists its commanders,
    and the proposal's version stays."""
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    change = {"action": "set_commander", "card_name": "Sol Ring"}
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    decks = h.app.state.gateway.decks
    row = decks.db.get_proposal(p["proposal_id"], "user-1")
    old = [{k: v for k, v in r.items() if k != "names"} for r in row["rows"]]
    with decks.db.tx() as c:
        c.execute("UPDATE proposals SET rows_json = ? WHERE id = ?", (json.dumps(old), p["proposal_id"]))
    line = structured(await call(h, token, "list_my_proposals", {"pending_only": True}))["proposals"][0]
    assert "Sol Ring" not in line["cards"]  # as 0.7.31 stored it
    decks.recheck_waiting("user-1", await decks.get_own_deck("user-1", "42"))
    line = structured(await call(h, token, "list_my_proposals", {"pending_only": True}))["proposals"][0]
    assert "Sol Ring" in line["cards"] and "Aesi, Tyrant of Gyre Strait" in line["cards"], line
    assert line["version"] == row["version"]


async def test_search_decks_count_is_null_when_archidekt_gives_minus_one(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    nothing = structured(await call(stack.h, token, "search_decks", {"commander": "Nobody At All"}))
    assert nothing["ok"] and nothing["decks"] == [] and nothing["count"] is None


def test_an_applied_collection_change_says_so_only_when_the_read_back_found_it() -> None:
    """A collection change is stored as applied even when reading the collection back finds
    mismatches, so its text says every change is there unless the read-back found otherwise. One
    applied before the read-back arrived has no "verified" and keeps the plain text."""
    for result in ({"verified": True}, {}, None):
        assert applied_text("collection", {}, result) == (
            "Your collection on Archidekt has these changes now."
        )
    assert applied_text("collection", {}, {"verified": False, "mismatches": [{"name": "Sol Ring"}]}) == (
        "Sent to Archidekt, but the read-back didn't confirm every change. See Result on the review page."
    )


async def test_an_api_apply_reads_api_and_activity_names_where_each_was_applied(stack: Stack) -> None:
    """A bearer-token apply over the REST API is checked like the assistant's but logged as the
    API's, and the Activity page says where each proposal was applied in words."""
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    edit = structured(await call(h, token, "propose_deck_changes", _add("Forest")))
    r = await h.http.post(
        f"/api/v1/proposals/{edit['proposal_id']}/apply", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.status_code == 200 and r.json()["state"] == "applied", r.text
    got = structured(await call(h, token, "get_proposal", {"proposal_id": edit["proposal_id"]}))
    assert got["applied_via"] == "api"
    await _press(h, token, await call(h, token, "propose_delete_deck", {"deck_id": "42"}))
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get("/activity", headers=NAV)).text
    finally:
        await b.aclose()
    assert "via API" in page and "via review page" in page and "review_page" not in page
