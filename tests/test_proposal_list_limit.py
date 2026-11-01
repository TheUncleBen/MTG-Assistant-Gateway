"""list_my_proposals' limit outside 1 to MAX_PROPOSALS_PAGE is clamped into that range, as before,
and its description now says so (0.7.46 audit: limit -1 and 100000 were answered silently, though
the description said 1 to 100)."""

from __future__ import annotations

from mtg_gateway.tools_decks import MAX_PROPOSALS_PAGE

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, sse_json, structured


async def test_an_out_of_range_limit_is_brought_into_range_and_the_description_says_so(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    deck = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "set_quantity", "card_name": "Forest", "quantity": 5}]},
        )
    )
    binder = structured(
        await call(h, token, "propose_collection_changes", {"add": [{"name": "Opt", "quantity": 1}]})
    )
    assert deck["ok"] and binder["ok"], (deck, binder)
    low = structured(await call(h, token, "list_my_proposals", {"limit": -1}))
    assert len(low["proposals"]) == 1 and low["has_more"] is True, low
    high = structured(await call(h, token, "list_my_proposals", {"limit": 100000}))
    assert len(high["proposals"]) == 2 and high["has_more"] is False, high
    tools = sse_json(await h.mcp(token, "tools/list"))["result"]["tools"]
    limit = next(t for t in tools if t["name"] == "list_my_proposals")["inputSchema"]["properties"]["limit"]
    assert f"1 to {MAX_PROPOSALS_PAGE}" in limit["description"], limit
    assert "clamped" in limit["description"], limit
