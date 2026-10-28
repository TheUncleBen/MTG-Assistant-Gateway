"""The Binder finds every record and adds up presses (0.7.46). A record is found by Archidekt's own
name filter, not by paging the newest thousand, so the oldest entry of a big binder can be changed
and adding its printing again adds to it. The stepper sends one more or one fewer, so two tabs that
each press + add two copies. An import runs in the background with its progress on the page, and
waits when the member's Archidekt budget runs low instead of failing part way."""

from __future__ import annotations

from .conftest import COLLECTION, Stack, stack_with
from .test_browse_collection import api, linked

stack_options = stack_with(COLLECTION)

SLIME = 9010  # Acidic Slime: none of the seeded rows


def _seed(stack: Stack, n: int) -> None:
    """``n`` records newer than the binder's oldest entry (id 1, two Acidic Slime)."""
    ark = stack.ark
    rows = ark.collections.setdefault("alice", {})
    rows[1] = ark._collection_row(1, ark.printing(SLIME), {"quantity": 2})
    pks = (9003, 9001, 9002, 9005, 9008)
    ark.next_coll_id = max(ark.next_coll_id, 1)
    for i in range(n):
        ark.next_coll_id += 1
        body = {
            "quantity": 1,
            "modifier": ("Normal", "Foil", "Etched")[i // 5 % 3],
            "condition": 1 + i // 15 % 5,
        }
        row = ark._collection_row(ark.next_coll_id, ark.printing(pks[i % 5]), body)
        rows[row["id"]] = row


def _slime(stack: Stack) -> dict[int, int]:
    return {
        rid: r["quantity"]
        for rid, r in stack.ark.collections["alice"].items()
        if r["card"]["oracleCard"]["name"] == "Acidic Slime"
    }


async def test_the_oldest_entry_of_a_big_binder_can_be_changed_and_added_to(stack: Stack) -> None:
    _seed(stack, 1_050)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        reads = len(stack.ark.collection_queries)
        r = await api(b, "POST", "/collection/api/rows/1", {"quantity": 3, "name": "Acidic Slime"}, csrf=csrf)
        assert r.status_code == 200, r.text
        assert r.json()["row"]["quantity"] == 3 and _slime(stack) == {1: 3}
        assert len(stack.ark.collection_queries) - reads == 1  # one read by name, not ten pages
        assert stack.ark.collection_queries[-1].get("cardName") == "Acidic Slime"
        # adding the same printing again adds to that record: no second record
        r = await api(b, "POST", "/collection/api/add", {"items": [{"name": "Acidic Slime"}]}, csrf=csrf)
        assert r.status_code == 200, r.text
        assert _slime(stack) == {1: 4}, _slime(stack)
        # the page's own forms (no script) send the name too
        page = (await b.http.get("/collection?q=Acidic")).text
        assert (
            "<input type='hidden' name='id' value='1'><input type='hidden' name='name' value='Acidic Slime'>"
            in page
        )
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "dec", "id": "1", "name": "Acidic Slime"}
        )
        assert r.status_code == 303 and "err=" not in r.headers["location"], r.headers
        assert _slime(stack) == {1: 3}
    finally:
        await b.aclose()


async def test_a_small_binder_is_read_once_for_a_whole_add(stack: Stack) -> None:
    _seed(stack, 20)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        reads = len(stack.ark.collection_queries)
        items = [{"name": n} for n in ("Acidic Slime", "Sol Ring", "Opt", "Swamp")]
        r = await api(b, "POST", "/collection/api/add", {"items": items}, csrf=csrf)
        assert r.status_code == 200, r.text
        assert len(stack.ark.collection_queries) - reads == 1
        assert _slime(stack) == {1: 3}
    finally:
        await b.aclose()


async def test_two_tabs_each_pressing_plus_add_two_copies(stack: Stack) -> None:
    _seed(stack, 3)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        # both tabs show 2 copies; each sends one more
        for _tab in range(2):
            r = await api(
                b, "POST", "/collection/api/rows/1", {"delta": 1, "name": "Acidic Slime"}, csrf=csrf
            )
            assert r.status_code == 200, r.text
        assert _slime(stack) == {1: 4}
        r = await api(b, "POST", "/collection/api/rows/1", {"delta": -4, "name": "Acidic Slime"}, csrf=csrf)
        assert r.status_code == 200 and r.json()["row"] is None and _slime(stack) == {}
        bad = await api(b, "POST", "/collection/api/rows/2", {"delta": "1"}, csrf=csrf)
        assert bad.status_code == 400
    finally:
        await b.aclose()
