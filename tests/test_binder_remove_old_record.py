"""A binder removal keeps the card's name, so its apply finds the record wherever it sits.

The stored removal used to hold only the record's id; the apply then looked through the newest
1,000 records, ten reads long, and a record older than that could never be removed."""

from __future__ import annotations

from mtg_gateway.decks import BROWSER_CLIENT, REVIEW_PAGE, current_client

from .conftest import Stack, StackOptions, stack_with
from .fake_archidekt import CARD_DB
from .test_decks_and_proxy import call, linked_user, structured

stack_options = stack_with(StackOptions(settings={"writes_enabled": True}))


async def test_a_record_older_than_the_newest_thousand_is_removed(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    rows = ark.collections.setdefault("alice", {})
    sol = {**CARD_DB["sol ring"], "edition": {"editioncode": "cmr"}, "collectorNumber": "472"}
    rows[1] = ark._collection_row(1, sol, {"quantity": 2})
    filler = {**CARD_DB["forest"], "edition": {"editioncode": "m21"}, "collectorNumber": "1"}
    for rid in range(2, 1102):  # 1,100 newer records
        rows[rid] = ark._collection_row(rid, filler, {"quantity": 1})
    p = structured(await call(h, token, "propose_collection_changes", {"remove": [{"name": "Sol Ring"}]}))
    assert p["ok"], p
    stored = h.db.get_proposal(p["proposal_id"], "user-1")["changes"]["remove"]
    assert stored[0]["name"] == "Sol Ring"
    before = len(ark.collection_queries)
    current_client.set(BROWSER_CLIENT)
    out = await stack.gw.decks.apply("user-1", p["proposal_id"], via=REVIEW_PAGE)
    assert out["state"] == "applied", out
    assert 1 not in rows
    asked = ark.collection_queries[before:]
    assert asked and asked[0].get("cardName")  # found by a name search, not by paging
