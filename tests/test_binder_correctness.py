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


async def _poll(b, url: str, until: tuple[str, ...], *, tries: int = 400) -> dict:
    import asyncio

    for _ in range(tries):
        d = (await b.http.get(url)).json()
        if d.get("state") in until:
            return d
        await asyncio.sleep(0.02)
    raise AssertionError(f"never reached {until}: {d}")


async def test_an_import_runs_in_the_background_and_waits_for_room(stack: Stack, monkeypatch) -> None:
    """30 lines cost about 60 Archidekt calls; with 40 a window the import adds what it can, keeps a
    quarter back for the member's own changes, says it is paused, then goes on when there is room."""
    from mtg_gateway import collection
    from mtg_gateway.decks import RateBudget

    monkeypatch.setattr(collection, "IMPORT_PAUSE_POLL", 0.02)
    _seed(stack, 3)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        stack.gw.decks.archidekt_budget = RateBudget(40)
        text = "Quantity,Name\n" + "\n".join("1,Acidic Slime" for _ in range(30))  # 30 rows, kept apart
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": text})
        assert r.status_code == 303, r.text
        where = r.headers["location"]
        assert where.startswith("/collection?import="), where  # back at once, with the job
        jid = where.split("=", 1)[1]
        page = (await b.http.get(where)).text
        assert f"data-import='{jid}'" in page and "lines done" in page
        paused = await _poll(b, f"/collection/api/import/{jid}", ("paused",))
        assert "Paused" in paused["text"] and 0 < paused["done"] < 30, paused
        assert "minute" not in paused["text"] and "second" not in paused["text"]
        # the member still has room for their own changes while it waits
        assert (
            stack.gw.decks.archidekt_budget.room(next(iter(stack.gw.decks.archidekt_budget._buckets))) >= 10
        )
        r = await api(b, "POST", "/collection/api/rows/1", {"delta": 1, "name": "Acidic Slime"}, csrf=csrf)
        assert r.status_code == 200, r.text
        stack.gw.decks.archidekt_budget = RateBudget(1_000)  # room again
        done = await _poll(b, f"/collection/api/import/{jid}", ("done", "failed"))
        assert done["state"] == "done" and done["done"] == 30, done
        assert _slime(stack) == {1: 33}  # 2, 30 imported and the member's own +1
        landed = await b.http.get(done["next"])
        assert "Imported 30 cards into your binder." in landed.text
    finally:
        await b.aclose()


async def test_an_import_that_stops_says_what_was_added(stack: Stack, monkeypatch) -> None:
    from mtg_gateway import collection

    monkeypatch.setattr(collection, "IMPORT_PAUSE_POLL", 0.02)
    _seed(stack, 3)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        writes = {"n": 0}
        orig = stack.ark.transport.handler

        def failing(req):
            if req.method in ("PUT", "PATCH") and "/collection/" in req.url.path:
                writes["n"] += 1
                if writes["n"] > 4:
                    import httpx

                    return httpx.Response(500, json={"detail": "down"})
            return orig(req)

        stack.ark.transport.handler = failing
        text = "Quantity,Name\n" + "\n".join("1,Acidic Slime" for _ in range(10))
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": text})
        jid = r.headers["location"].split("=", 1)[1]
        done = await _poll(b, f"/collection/api/import/{jid}", ("done", "failed"))
        assert done["state"] == "failed", done
        landed = (await b.http.get(done["next"])).text
        assert "Imported 4 cards" in landed and "stopped" in landed, landed[:2000]
        assert "HTTP" not in done["text"] and "500" not in done["text"]
    finally:
        stack.ark.transport.handler = orig
        await b.aclose()


async def test_the_add_box_takes_up_to_9999_copies(stack: Stack) -> None:
    b = await linked(stack)
    try:
        page = (await b.http.get("/collection?add=1")).text
        assert "id='addqty' type='number' name='quantity' value='1' min='1' max='9999'" in page
        csrf = await b.csrf("/collection")
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "add", "name": "Acidic Slime", "quantity": "1500"}
        )
        assert r.status_code == 303 and "ok=added" in r.headers["location"], r.headers
        assert list(_slime(stack).values()) == [1500]
        # a count typed before the name still counts when Copies is left at 1
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "add", "name": "3 Acidic Slime", "quantity": "1"}
        )
        assert list(_slime(stack).values()) == [1503]
    finally:
        await b.aclose()
