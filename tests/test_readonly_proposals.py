"""The proposal tools carry readOnlyHint true (owner decision, 0.7.46): an app may run them without
asking. That is right only while none of them sends anything to Archidekt, in any approval mode,
Full auto included: what they change is the gateway's own list of waiting proposals, and only
apply_proposal (or the member's press) writes. This holds the hint to that (0.7.46 review)."""

from __future__ import annotations

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, sse_json, structured

CALLS = (
    ("propose_deck_changes", {"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]}),
    ("propose_new_deck", {"name": "N", "decklist_text": "1 Sol Ring"}),
    ("propose_deck_details", {"deck_id": "42", "details": {"description": "x"}}),
    ("propose_clone_deck", {"deck_id": "42"}),
    ("propose_delete_deck", {"deck_id": "42"}),
    ("propose_list_change", {"which_list": "wants", "changes": [{"action": "add", "card_name": "Sol Ring"}]}),
    ("propose_profile_change", {"field": "bio", "value": "hi"}),
    ("propose_collection_changes", {"add": [{"name": "Sol Ring"}]}),
)


async def test_no_read_only_proposal_tool_writes_to_archidekt_even_in_full_auto(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    tools = sse_json(await h.mcp(token, "tools/list", {}, rid=3))["result"]["tools"]
    hint = {t["name"]: (t.get("annotations") or {}).get("readOnlyHint") for t in tools}
    writes = [c for c in stack.ark.calls if c[0] != "GET"]
    made: list[str] = []
    for name, args in CALLS:
        if name not in hint:
            continue
        assert hint[name] is True, name
        out = structured(await call(h, token, name, args))
        if out.get("proposal_id"):
            made.append(out["proposal_id"])
    assert len(made) >= 2, made
    # amend, withdraw and combine change only what waits on the gateway
    for name, args in (
        ("amend_proposal", {"proposal_id": made[0], "changes": [{"action": "add", "card_name": "Opt"}]}),
        ("combine_proposals", {"proposal_ids": made[:2]}),
        ("withdraw_proposal", {"proposal_id": made[0], "reason": "x"}),
    ):
        assert hint[name] is True, name
        await call(h, token, name, args)
    assert [c for c in stack.ark.calls if c[0] != "GET"] == writes
    assert hint["apply_proposal"] is False
