"""Select on the Binder's cards (0.7.46 audit QG-17, QOL-17): every row has a tick box, Select
ticks every row on screen, and the bar offers what one row's menu offers and makes sense in
bulk: Condition…, Finish…, Tags… and Remove, with Undo. The writes go through the same paths
and limits as a single row's (one record at a time, read back); a row that is not the
member's is refused by Archidekt and the notice says how many went."""

from __future__ import annotations

import re

from .conftest import COLLECTION, Stack, stack_with
from .test_collection_page import NAV, _seed, linked

stack_options = stack_with(COLLECTION)


def _rows(stack: Stack) -> dict[int, dict]:
    return stack.ark.collections["alice"]


async def test_the_page_has_tick_boxes_the_bar_and_its_boxes(stack: Stack) -> None:
    b = await linked(stack)
    try:
        _seed(stack, 3)
        for view in ("grid", "list"):
            page = (await b.http.get(f"/collection?view={view}", headers=NAV)).text
            for rid, row in _rows(stack).items():
                name = row["card"]["oracleCard"]["name"]
                tick = (
                    f"<input type='checkbox' name='sel' value='{rid} {name}' "
                    f"aria-label='Select {name}' form='col-bulk'>"
                )
                assert tick in page, (view, name)
            assert "<form method='post' action='/collection' id='col-bulk' class='col-bulkform'>" in page
            assert "<div class='ui-bar slim' role='group' aria-label='Selected cards' hidden>" in page
            assert "<button type='button' class='btn col-select' aria-pressed='false' hidden>" in page
            for box in ("col-bulk-cond", "col-bulk-finish", "col-bulk-tags"):
                assert f"id='{box}'" in page, box
            assert "name='do' value='remove'" in page
    finally:
        await b.aclose()


async def test_condition_finish_and_tags_are_set_on_every_ticked_row(stack: Stack) -> None:
    b = await linked(stack)
    try:
        _seed(stack, 3)
        ids = list(_rows(stack))
        csrf = await b.csrf("/collection")
        sel = [f"{rid} {_rows(stack)[rid]['card']['oracleCard']['name']}" for rid in ids[:2]]
        r = await b.http.post(
            "/collection",
            data={"csrf": csrf, "action": "bulk", "do": "condition", "condition": "LP", "sel": sel},
        )
        assert r.status_code == 303 and r.headers["location"].startswith("/collection?ok=bulk&t="), r.text
        assert [_rows(stack)[rid]["condition"] for rid in ids] == [2, 2, 1]  # LP is 2 on Archidekt
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "Condition set on 2 cards." in page
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "bulk", "do": "finish", "finish": "foil", "sel": sel}
        )
        assert r.status_code == 303, r.text
        assert [_rows(stack)[rid]["modifier"] for rid in ids] == ["Foil", "Foil", "Normal"]
        # tags: the member's own binder tags, by name; none ticked clears them
        ark = stack.ark
        ark.next_collection_tag_id += 1
        tid = ark.next_collection_tag_id
        ark.collection_tag_lists.setdefault("alice", {})[tid] = {
            "id": tid,
            "name": "trade",
            "color": "#1ebb6c",
        }
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "bulk", "do": "tags", "tags": ["trade"], "sel": sel}
        )
        assert r.status_code == 303, r.text
        assert [_rows(stack)[rid]["tags"] for rid in ids] == [[tid], [tid], []]
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "Tags set on 2 cards." in page
        # nothing ticked, or no choice: a plain message, nothing changed
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "bulk", "do": "condition", "condition": "NM"}
        )
        assert r.status_code == 303 and "err=" in r.headers["location"]
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "Tick a card first." in page
    finally:
        await b.aclose()


async def test_remove_goes_at_once_and_undo_brings_the_rows_back(stack: Stack) -> None:
    b = await linked(stack)
    try:
        _seed(stack, 3)
        rows = _rows(stack)
        ids = list(rows)
        before = {rid: dict(rows[rid]) for rid in ids}
        csrf = await b.csrf("/collection")
        sel = [f"{rid} {rows[rid]['card']['oracleCard']['name']}" for rid in ids[:2]]
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "bulk", "do": "remove", "sel": sel}
        )
        assert r.status_code == 303, r.text
        where = r.headers["location"]
        assert where.startswith("/collection?ok=bulk&t="), where
        assert list(_rows(stack)) == ids[2:]
        page = (await b.http.get(where, headers=NAV)).text
        assert "2 cards removed from your binder." in page
        m = re.search(r"name='token' value='([A-Za-z0-9_-]+)'", page)
        assert m and ">Undo</button>" in page
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "bulk_restore", "token": m.group(1)}
        )
        assert r.status_code == 303 and r.headers["location"].startswith("/collection?ok=bulk&t="), r.text
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "2 cards are back in your binder." in page
        back = _rows(stack)
        assert len(back) == 3
        for rid in ids[:2]:
            got = next(
                x for x in back.values() if x["card"]["id"] == before[rid]["card"]["id"] and x["id"] != ids[2]
            )
            assert got["modifier"] == before[rid]["modifier"] and got["condition"] == before[rid]["condition"]
        # spent
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "bulk_restore", "token": m.group(1)}
        )
        assert r.status_code == 303 and "err=" in r.headers["location"]
        assert (
            "That Undo is no longer available." in (await b.http.get(r.headers["location"], headers=NAV)).text
        )
    finally:
        await b.aclose()


async def test_a_bulk_change_has_a_limit_and_names_the_rows_that_did_not_go(stack: Stack) -> None:
    from mtg_gateway import collection

    b = await linked(stack)
    try:
        _seed(stack, 2)
        rows = _rows(stack)
        ids = list(rows)
        csrf = await b.csrf("/collection")
        too_many = [f"{900000 + i} Sol Ring" for i in range(collection.MAX_BULK_ROWS + 1)]
        r = await b.http.post(
            "/collection",
            data={"csrf": csrf, "action": "bulk", "do": "finish", "finish": "foil", "sel": too_many},
        )
        assert r.status_code == 303 and "err=" in r.headers["location"]
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert f"at most {collection.MAX_BULK_ROWS} cards at a time" in page
        # a row that is not in the binder (another member's, or gone): the rest still go
        sel = [f"{ids[0]} {rows[ids[0]]['card']['oracleCard']['name']}", "424242 Sol Ring"]
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "bulk", "do": "finish", "finish": "foil", "sel": sel}
        )
        assert r.status_code == 303, r.text
        page = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "Finish set on 1 card. 1 could not be changed:" in page
        assert rows[ids[0]]["modifier"] == "Foil"
    finally:
        await b.aclose()
