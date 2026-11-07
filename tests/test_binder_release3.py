"""Archidekt parity release 3 (0.7.46): the Binder's own tags (made, renamed and deleted on Binder ›
Lists, set on entries from the Details box or an assistant's proposal, and a Tag filter), clearing
the whole Binder (a proposal only the member's press on its review page applies, in every
approval mode, with a copy kept under History first that can be put back or downloaded), and
imports of more than a hundred rows (a paced background job that waits out Archidekt's "slow
down", stops with Resume on a failure or a restart, and says what went in)."""

from __future__ import annotations

import asyncio
import csv
import io

import httpx
import pytest

from mtg_gateway import batch, collection, modes
from mtg_gateway.approve import APPROVAL_META_KEY

from .conftest import COLLECTION, Stack, stack_with
from .test_account_actions import _browser_apply
from .test_approval_modes import _set_mode
from .test_browse_collection import NAV, api, linked
from .test_decks_and_proxy import Browser, call, mcp_token, structured

stack_options = stack_with(COLLECTION)
SUB = "user-1"
PKS = (9003, 5011, 5007, 9005, 9008)  # Sol Ring, Cultivate, Command Tower, Island, Swamp


def _tags(stack: Stack, *names: str) -> dict[str, int]:
    """Binder tags for alice, straight into the fake: name -> id."""
    mine = stack.ark.collection_tag_lists.setdefault("alice", {})
    out = {}
    for n in names:
        stack.ark.next_collection_tag_id += 1
        tid = stack.ark.next_collection_tag_id
        mine[tid] = {"id": tid, "name": n, "color": "#336699"}
        out[n] = tid
    return out


def _seed(stack: Stack, n: int, *, tags: list[int] | None = None, price: float | None = None) -> list[int]:
    """``n`` records for alice, straight into the fake (five printings in turn); their ids."""
    ark = stack.ark
    ids = []
    for i in range(n):
        ark.next_coll_id += 1
        body = {"quantity": 1 + i % 3, "modifier": ("Normal", "Foil")[i % 2], "condition": 1 + i % 4}
        if tags is not None:
            body["tags"] = list(tags)
        if price is not None:
            body["purchasePrice"] = price
        row = ark._collection_row(ark.next_coll_id, ark.printing(PKS[i % 5]), body)
        ark.collections.setdefault("alice", {})[row["id"]] = row
        ids.append(row["id"])
    return ids


def _rows(stack: Stack) -> dict[int, dict]:
    return stack.ark.collections.get("alice", {})


async def _token(stack: Stack, b: Browser) -> str:
    return await mcp_token(stack.h)


# -- binder tags: the list ---------------------------------------------------------------------
async def test_binder_tags_are_made_renamed_and_deleted_as_a_list(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    b = await linked(stack)
    await b.aclose()
    token = await mcp_token(h)
    await _set_mode(h, "semi")

    async def propose(changes: list[dict]) -> dict:
        return await call(
            h, token, "propose_list_change", {"which_list": "collection_tags", "changes": changes}
        )

    p = structured(await propose([{"op": "create", "name": "Trade"}]))
    assert p["ok"] and p["kind"] == "lists" and p["risk"] == "low", p
    assert ark.collection_tag_lists.get("alice", {}) == {}  # a proposal changes nothing by itself
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["state"] == "applied" and out["result"]["verified"] is True, out
    (tag,) = ark.collection_tag_lists["alice"].values()
    assert tag["name"] == "Trade"
    assert ark.collection_tag_writes[0][:2] == ("POST", "/api/collection/tags/")
    # a duplicate (any case) is refused before anything is stored
    assert structured(await propose([{"op": "create", "name": "TRADE"}]))["ok"] is False

    p = structured(
        await propose([{"op": "edit", "name": "trade", "new_name": "For trade", "color": "#00ff00"}])
    )
    assert await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    assert tag | {"id": 0} == {"id": 0, "name": "For trade", "color": "#00ff00"}
    sent = ark.collection_tag_writes[-1]
    assert sent[0] == "PATCH" and sent[1] == f"/api/collection/modifyTag/{tag['id']}/", sent

    # a delete says how many entries carry the tag, and waits for the member's own press
    _seed(stack, 3, tags=[tag["id"]])
    res = await propose([{"op": "delete", "name": "For trade"}])
    p = structured(res)
    assert p["risk"] == "destructive" and p["assistant_may_apply"] is False, p
    assert "3 entries in your binder carry this tag and lose it" in p["rows"][0]["note"], p["rows"][0]
    refused = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert refused["error"] == "browser_required" and ark.collection_tag_lists["alice"]
    assert (res.get("_meta") or {}).get(APPROVAL_META_KEY)  # a list delete: the chat card may approve it
    done = await _browser_apply(h, p["proposal_id"])
    assert done["state"] == "applied", done
    assert ark.collection_tag_lists["alice"] == {} and all(not r["tags"] for r in _rows(stack).values())


async def test_the_lists_page_has_a_binder_tags_tab(stack: Stack) -> None:
    _tags(stack, "Trade", "Keep")
    b = await linked(stack)
    try:
        page = (await b.http.get("/lists/btags", headers=NAV)).text
        assert "Binder tags" in page and "Trade" in page and "Keep" in page
    finally:
        await b.aclose()


# -- binder tags on entries: the Details box, the filter, the assistant --------------------------
async def test_the_details_box_sets_an_entrys_tags_and_the_filter_shows_them(stack: Stack) -> None:
    ids = _tags(stack, "Trade", "Keep")
    rid, other, *_ = _seed(stack, 4)
    b = await linked(stack)
    try:
        page = (await b.http.get("/collection", headers=NAV)).text
        # the Tag drop-down in the results bar, and a box to tick per tag in the Details box
        assert "<select id='col-tag' name='tag'><option value='' selected>All cards</option>" in page or (
            "<select id='col-tag' name='tag'><option value=''>All cards</option>" in page
        )
        assert "name='tag:Trade' value='1' data-tag='Trade'" in page
        assert "href='/lists/btags'>Manage tags</a>" in page
        csrf = await b.csrf("/collection")
        form = {
            "csrf": csrf,
            "action": "details",
            "id": str(rid),
            "name": "Sol Ring",
            "finish": "nonfoil",
            "condition": "NM",
            "language": "EN",
            "purchase_price": "",
            "tags_shown": "1",
            "tag:Trade": "1",
        }
        r = await b.http.post("/collection", data=form)
        assert r.headers["location"].endswith("ok=updated"), r.headers["location"]
        assert _rows(stack)[rid]["tags"] == [ids["Trade"]]
        # the page's script sends them as a list (the JSON route); an unknown name is refused
        r = await api(b, "POST", f"/collection/api/rows/{other}", {"tags": ["keep"]}, csrf=csrf)
        assert r.status_code == 200 and r.json()["row"]["tags"] == ["Keep"], r.text
        assert _rows(stack)[other]["tags"] == [ids["Keep"]]
        r = await api(b, "POST", f"/collection/api/rows/{other}", {"tags": ["Nope"]}, csrf=csrf)
        assert r.status_code == 404 and "no binder tag called “Nope”" in r.json()["message"], r.text
        # the filter: only the entry tagged Trade, on the page and from the rows route
        page = (await b.http.get("/collection?tag=Trade", headers=NAV)).text
        assert "<option value='Trade' selected>Trade</option>" in page
        assert f"data-id='{rid}'" in page and f"data-id='{other}'" not in page
        assert "id='col-count' aria-live='polite'>1 entry</h2>" in page
        got = (await b.http.get("/collection/api/rows?tag=trade")).json()
        assert got["total"] == 1 and f"data-id='{rid}'" in got["html"]
        # ticking nothing takes every tag off
        form.pop("tag:Trade")
        await b.http.post("/collection", data=form)
        assert _rows(stack)[rid]["tags"] == []
        page = (await b.http.get("/collection?tag=Gone", headers=NAV)).text
        assert "You have no binder tag called “Gone”." in page
    finally:
        await b.aclose()


async def test_an_assistant_tags_entries_by_proposal_and_lists_by_tag(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    ids = _tags(stack, "Trade")
    rid, *_ = _seed(stack, 3)
    b = await linked(stack)
    await b.aclose()
    token = await mcp_token(h)
    await _set_mode(h, "auto")
    p = structured(
        await call(
            h, token, "propose_collection_changes", {"tag": [{"id": rid, "tags": ["Trade", "Binder 2"]}]}
        )
    )
    assert p["ok"], p
    want = "binder tags none -> “Trade”, “Binder 2” (new tag “Binder 2”)"
    assert p["rows"][0]["text"].endswith(want), p["rows"]
    assert _rows(stack)[rid]["tags"] == []  # nothing until it is applied
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["state"] == "applied" and out["result"]["verified"] is True, out
    made = {t["name"]: t["id"] for t in ark.collection_tag_lists["alice"].values()}
    assert sorted(_rows(stack)[rid]["tags"]) == sorted([ids["Trade"], made["Binder 2"]])
    got = structured(await call(h, token, "list_collection", {"tag": "binder 2"}))
    assert [r["id"] for r in got["cards"]] == [rid] and got["cards"][0]["tags"] == ["Trade", "Binder 2"], got
    bad = structured(await call(h, token, "list_collection", {"tag": "Nope"}))
    assert bad["ok"] is False and "Trade" in bad["message"], bad


# -- clearing the whole binder -------------------------------------------------------------------
@pytest.mark.parametrize("mode", ["manual", "semi", "auto"])
async def test_a_clear_waits_for_the_members_press_in_every_mode(stack: Stack, mode: str) -> None:
    h, ark = stack.h, stack.ark
    _seed(stack, 4)
    b = await linked(stack)
    await b.aclose()
    token = await mcp_token(h)
    await _set_mode(h, mode)
    res = await call(h, token, "propose_collection_changes", {"clear": True})
    p = structured(res)
    assert p["ok"] and p["risk"] == "destructive" and p["assistant_may_apply"] is False, p
    assert not (res.get("_meta") or {}).get(APPROVAL_META_KEY)  # no chat card code: review page only
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required", out
    # Apply all leaves it waiting too
    plan = h.app.state.gateway.decks.plan_selection(SUB, [p["proposal_id"]])
    assert [s["why"] for s in plan["skipped"]] == [batch.CLEAR_ALONE]
    assert ark.collection_clears == 0 and len(_rows(stack)) == 4
    assert modes.risk_of("action", [{"action": "clear_collection"}])[0] in modes.MEMBER_ONLY
    # alone with anything else, it is refused
    mixed = structured(
        await call(h, token, "propose_collection_changes", {"clear": True, "add": [{"name": "Sol Ring"}]})
    )
    assert mixed["ok"] is False


async def test_a_clear_keeps_a_copy_that_history_restores_and_downloads(stack: Stack, monkeypatch) -> None:
    h, ark = stack.h, stack.ark
    monkeypatch.setattr(collection, "IMPORT_PAUSE_POLL", 0.02)
    ids = _tags(stack, "Trade")
    seeded = _seed(stack, 5, tags=[ids["Trade"]], price=1.5)
    before = {(r["card"]["id"], r["modifier"], r["condition"], r["quantity"]) for r in _rows(stack).values()}
    b = await linked(stack)
    try:
        # the Binder's head has Clear binder…, which makes the proposal and opens its review page
        page = (await b.http.get("/collection", headers=NAV)).text
        assert "value='clear'>" in page and "Clear binder…" in page
        r = await b.http.post("/collection", data={"csrf": await b.csrf("/collection"), "action": "clear"})
        assert r.status_code == 303 and r.headers["location"].startswith("/proposals/"), r.headers
        pid = r.headers["location"].rsplit("/", 1)[1]
        review = (await b.http.get(r.headers["location"])).text
        assert "Clear your whole binder?" in review and "This removes <strong>5 entries" in review
        assert "Clear my binder" in review and "Archidekt itself has no undo" in review
        assert ark.collection_clears == 0
        done = await _browser_apply(h, pid)
        assert done["state"] == "applied" and done["result"]["verified"] is True, done
        assert ark.collection_clears == 1 and _rows(stack) == {}
        snap_id = done["result"]["snapshot_id"]
        # History: the copy, with Restore to my binder and its CSV, and no Test
        hist = (await b.http.get("/history?type=snapshots", headers=NAV)).text
        assert "9 cards as your binder stood just before it was cleared" in hist  # 1+2+3+1+2 copies
        assert "action='/collection/restore'" in hist and "data-deck-href='/collection'" in hist
        details = (await b.http.get(f"/history?type=snapshots&open=s-{snap_id}", headers=NAV)).text
        assert f"/collection/snapshots/{snap_id}.csv" in details and "Restore to my binder" in details
        assert f"/history/snapshots/{snap_id}/test" not in details
        got = await b.http.get(f"/collection/snapshots/{snap_id}.csv")
        assert got.status_code == 200 and "attachment" in got.headers["content-disposition"]
        lines = list(csv.DictReader(io.StringIO(got.text)))
        assert (
            len(lines) == 5 and {x["Tags"] for x in lines} == {"Trade"} and lines[0]["Price Paid"] == "1.50"
        )
        # a deck restore or a deck test of it is refused
        token = await mcp_token(h)
        bad = structured(await call(h, token, "propose_restore_snapshot", {"snapshot_id": snap_id}))
        assert bad["ok"] is False and "Restore to my binder" in bad["message"], bad
        # Restore to my binder: a background job, followed on the Binder page
        r = await b.http.post(
            "/collection/restore", data={"csrf": await b.csrf("/history"), "snapshot_id": snap_id}
        )
        assert r.status_code == 303 and r.headers["location"].startswith("/collection?import="), r.text
        jid = r.headers["location"].split("=", 1)[1]
        end = await _poll(b, f"/collection/api/import/{jid}", ("done", "failed"))
        assert end["state"] == "done" and end["done"] == 5, end
        landed = (await b.http.get(end["next"], headers=NAV, follow_redirects=True)).text
        assert "Put 9 cards back into your binder." in landed
        after = {
            (r["card"]["id"], r["modifier"], r["condition"], r["quantity"]) for r in _rows(stack).values()
        }
        assert after == before
        assert all(r["tags"] == [ids["Trade"]] and r["purchasePrice"] == 1.5 for r in _rows(stack).values())
        assert not set(seeded) & set(_rows(stack))  # new records, as Archidekt makes them
        # a second restore onto a binder that is not empty asks first
        r = await b.http.post(
            "/collection/restore", data={"csrf": await b.csrf("/history"), "snapshot_id": snap_id}
        )
        # ... in a box on the Binder page (test_polish3_0746 has the box)
        assert r.status_code == 303 and r.headers["location"] == f"/collection?restore={snap_id}", r.text
        asked = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "Your binder is not empty" in asked and "id='col-restore'" in asked
        assert "name='confirm' value='1'" in asked and len(_rows(stack)) == 5
    finally:
        await b.aclose()


async def test_a_restore_puts_back_tags_deleted_since(stack: Stack, monkeypatch) -> None:
    h, ark = stack.h, stack.ark
    monkeypatch.setattr(collection, "IMPORT_PAUSE_POLL", 0.02)
    ids = _tags(stack, "Trade")
    _seed(stack, 2, tags=[ids["Trade"]])
    b = await linked(stack)
    try:
        r = await b.http.post("/collection", data={"csrf": await b.csrf("/collection"), "action": "clear"})
        pid = r.headers["location"].rsplit("/", 1)[1]
        snap_id = (await _browser_apply(h, pid))["result"]["snapshot_id"]
        ark.collection_tag_lists["alice"] = {}  # the tag deleted on Archidekt since
        r = await b.http.post(
            "/collection/restore", data={"csrf": await b.csrf("/history"), "snapshot_id": snap_id}
        )
        jid = r.headers["location"].split("=", 1)[1]
        end = await _poll(b, f"/collection/api/import/{jid}", ("done", "failed"))
        assert end["state"] == "done", end
        (tag,) = ark.collection_tag_lists["alice"].values()
        assert tag["name"] == "Trade" and tag["color"] == "#336699"
        assert all(r["tags"] == [tag["id"]] for r in _rows(stack).values())
    finally:
        await b.aclose()


async def test_a_clear_that_fails_or_finds_the_binder_changed_clears_nothing(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    _seed(stack, 3)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "clear"})
        pid = r.headers["location"].rsplit("/", 1)[1]
        # the binder changed since the proposal: it waits for a look, nothing is sent
        _seed(stack, 1)
        out = await _browser_apply(h, pid)
        assert out.get("state") != "applied", out
        assert h.db.get_proposal(pid, SUB)["status"] == "needs_look"
        assert ark.collection_clears == 0 and len(_rows(stack)) == 4
        # Archidekt down at the clear: nothing cleared, and the proposal says so
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "clear"})
        pid = r.headers["location"].rsplit("/", 1)[1]
        ark.fail_collection_clear = True
        out = await _browser_apply(h, pid)
        assert out.get("state") != "applied" and len(_rows(stack)) == 4, out
        # an empty binder has nothing to clear
        ark.collections["alice"] = {}
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "clear"})
        assert r.headers["location"] == "/collection?err=empty", r.headers
    finally:
        await b.aclose()


# -- imports of more than a hundred rows ---------------------------------------------------------
async def _poll(b: Browser, url: str, until: tuple[str, ...], *, tries: int = 1500) -> dict:
    d: dict = {}
    for _ in range(tries):
        d = (await b.http.get(url)).json()
        if d.get("state") in until:
            return d
        await asyncio.sleep(0.02)
    raise AssertionError(f"never reached {until}: {d}")


def _big_csv(n: int) -> str:
    names = ("Sol Ring", "Cultivate", "Command Tower", "Island", "Swamp")
    lines = [
        f"1,{names[i % 5]},{'Foil' if i % 2 else 'Normal'},{('NM', 'LP', 'MP')[i % 3]}" for i in range(n)
    ]
    return "Quantity,Name,Finish,Condition\n" + "\n".join(lines)


def _copies(stack: Stack) -> int:
    return sum(int(r["quantity"]) for r in _rows(stack).values())


async def test_an_import_of_150_rows_runs_paced_and_waits_out_slow_down(stack: Stack, monkeypatch) -> None:
    from mtg_gateway.decks import RateBudget

    monkeypatch.setattr(collection, "IMPORT_PAUSE_POLL", 0.02)
    stack.gw.decks.archidekt_budget = RateBudget(10_000)  # the pausing on the allowance is tested below
    assert collection.MAX_IMPORT_ROWS >= 5_000
    b = await linked(stack)
    orig = stack.ark.transport.handler
    slowed = {"n": 0}

    def slow_down(req):
        # Archidekt answers "slow down" to three writes in the middle of the import
        if req.method in ("POST", "PATCH") and "/api/collection/v2/" in req.url.path and _copies(stack) >= 40:
            if slowed["n"] < 3:
                slowed["n"] += 1
                return httpx.Response(429, headers={"Retry-After": "0"}, json={"detail": "slow down"})
        return orig(req)

    stack.ark.transport.handler = slow_down
    try:
        csrf = await b.csrf("/collection")
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": _big_csv(150)})
        assert r.headers["location"].startswith("/collection?import="), r.headers
        jid = r.headers["location"].split("=", 1)[1]
        end = await _poll(b, f"/collection/api/import/{jid}", ("done", "failed"))
        assert end["state"] == "done" and end["done"] == 150 and end["of"] == 150, end
        assert slowed["n"] == 3 and _copies(stack) == 150
        landed = (await b.http.get(end["next"], headers=NAV, follow_redirects=True)).text
        assert "Imported 150 cards into your binder." in landed
        # one import at a time: shown once, then gone
        again = (await b.http.get("/collection", headers=NAV)).text
        assert "Imported 150 cards" not in again
    finally:
        stack.ark.transport.handler = orig
        await b.aclose()


async def test_an_import_that_stops_resumes_where_it_stopped(stack: Stack, monkeypatch) -> None:
    from mtg_gateway.decks import RateBudget

    monkeypatch.setattr(collection, "IMPORT_PAUSE_POLL", 0.02)
    stack.gw.decks.archidekt_budget = RateBudget(10_000)
    b = await linked(stack)
    orig = stack.ark.transport.handler
    down = {"on": False}

    def failing(req):
        if down["on"] and req.method in ("POST", "PATCH") and "/api/collection/v2/" in req.url.path:
            return httpx.Response(500, json={"detail": "down"})
        if (
            req.method in ("POST", "PATCH")
            and "/api/collection/v2/" in req.url.path
            and _copies(stack) >= 120
        ):
            down["on"] = True
            return httpx.Response(500, json={"detail": "down"})
        return orig(req)

    stack.ark.transport.handler = failing
    try:
        csrf = await b.csrf("/collection")
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": _big_csv(130)})
        jid = r.headers["location"].split("=", 1)[1]
        stopped = await _poll(b, f"/collection/api/import/{jid}", ("failed",))
        assert stopped["done"] < 130 and _copies(stack) == 120, stopped
        page = (await b.http.get("/collection", headers=NAV)).text
        assert "value='import_resume'" in page and "value='import_done'" in page
        assert "<progress class='iprog' max='130' value='" in page
        assert "Imported 120 cards, then the import stopped at line" in page
        assert "HTTP" not in page.split("data-import", 1)[1][:2000]
        # a second import waits for this one
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": _big_csv(3)})
        assert "err=busyimport" in r.headers["location"]
        # Archidekt is back: Resume goes on from the line it stopped at, nothing twice
        down["on"] = False
        stack.ark.transport.handler = orig
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import_resume", "job": jid})
        assert r.headers["location"] == f"/collection?import={jid}"
        end = await _poll(b, f"/collection/api/import/{jid}", ("done",))
        assert end["done"] == 130 and _copies(stack) == 130, end
    finally:
        stack.ark.transport.handler = orig
        await b.aclose()


async def test_a_restart_stops_a_running_import_with_resume(stack: Stack, monkeypatch) -> None:
    from mtg_gateway import collection_bulk
    from mtg_gateway.decks import RateBudget

    monkeypatch.setattr(collection, "IMPORT_PAUSE_POLL", 0.02)
    b = await linked(stack)
    gw = stack.gw
    try:
        csrf = await b.csrf("/collection")
        gw.decks.archidekt_budget = RateBudget(40)  # little room: the import pauses part way
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": _big_csv(120)})
        jid = r.headers["location"].split("=", 1)[1]
        await _poll(b, f"/collection/api/import/{jid}", ("paused",))
        page = (await b.http.get("/collection", headers=NAV)).text  # the strip follows it
        assert f"data-import='{jid}'" in page and "<progress class='iprog' max='120'" in page
        assert "Paused at" in page
        jobs = gw.collection_jobs
        task = jobs.tasks[SUB]
        task.cancel()  # the gateway stops (a restart) ...
        await asyncio.gather(task, return_exceptions=True)
        collection_bulk.BulkJobs(jobs.service)  # ... and starts again: the job is stopped, kept
        job = jobs.job(SUB)
        assert job["state"] == "stopped" and "restarted" in job["result"]["stopped"], job
        page = (await b.http.get("/collection", headers=NAV)).text
        assert "value='import_resume'" in page
        gw.decks.archidekt_budget = RateBudget(10_000)
        await b.http.post("/collection", data={"csrf": csrf, "action": "import_resume", "job": jid})
        end = await _poll(b, f"/collection/api/import/{jid}", ("done",))
        assert end["done"] == 120 and _copies(stack) == 120, end
    finally:
        await b.aclose()


async def test_an_import_job_goes_with_delete_my_data(stack: Stack) -> None:
    from mtg_gateway import db as dbmod

    assert ("binder_jobs", "sub") in dbmod.MEMBER_TABLES
