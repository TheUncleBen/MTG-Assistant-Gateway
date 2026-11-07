"""A Binder import whose card write reached Archidekt but whose answer never came back (a timeout
after the write landed) never adds that card twice. Before, the import stopped saying "Everything
before that is in your binder. Resume carries on from there.", and Resume sent that line again:
131 copies for an import of 130. Now the gateway reads that card back: a write that landed is
counted and the import goes on; when the read fails too, the import stops saying the card may
already be in, and Resume reads it back before it sends anything for that line. A restart in the
instant between a card's write and its count is the same case: the line in flight is kept with
the job, and Resume reads it back first."""

from __future__ import annotations

import asyncio

import httpx

from mtg_gateway import collection, collection_bulk

from .conftest import COLLECTION, Stack, stack_with
from .test_binder_release3 import SUB, _big_csv, _copies, _poll
from .test_browse_collection import NAV, linked

stack_options = stack_with(COLLECTION)

WRITES = ("POST", "PATCH")
COLL = "/api/collection/v2/"


def _setup(stack: Stack, monkeypatch) -> None:
    from mtg_gateway.decks import RateBudget

    monkeypatch.setattr(collection, "IMPORT_PAUSE_POLL", 0.02)
    stack.gw.decks.archidekt_budget = RateBudget(10_000)


async def _start(b, n: int) -> str:
    csrf = await b.csrf("/collection")
    r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": _big_csv(n)})
    assert r.headers["location"].startswith("/collection?import="), r.headers
    return r.headers["location"].split("=", 1)[1]


async def _resume(b, jid: str) -> None:
    csrf = await b.csrf("/collection")
    r = await b.http.post("/collection", data={"csrf": csrf, "action": "import_resume", "job": jid})
    assert r.headers["location"] == f"/collection?import={jid}", r.headers


async def test_a_lost_answer_is_read_back_and_the_import_goes_on(stack: Stack, monkeypatch) -> None:
    """The 120th copy's write lands unanswered; Archidekt still answers reads. The card is read
    back, counted, and the import ends with exactly 130 copies."""
    _setup(stack, monkeypatch)
    b = await linked(stack)
    orig = stack.ark.transport.handler
    lost = {"n": 0}

    async def handler(req: httpx.Request) -> httpx.Response:
        before = _copies(stack)
        resp = orig(req)
        if req.method in WRITES and COLL in req.url.path and before == 119 and not lost["n"]:
            lost["n"] += 1
            raise httpx.ReadTimeout("no answer", request=req)
        return resp

    stack.ark.transport.handler = handler
    try:
        jid = await _start(b, 130)
        end = await _poll(b, f"/collection/api/import/{jid}", ("done", "failed"))
        if end["state"] == "failed":  # the member presses Resume, as the page offers
            await _resume(b, jid)
            end = await _poll(b, f"/collection/api/import/{jid}", ("done", "failed"))
        assert lost["n"] == 1
        assert _copies(stack) == 130, f"{_copies(stack)} copies (130 expected)"
        assert end["state"] == "done" and end["done"] == 130, end
    finally:
        stack.ark.transport.handler = orig
        await b.aclose()


async def test_when_the_read_back_fails_too_resume_checks_before_sending(stack: Stack, monkeypatch) -> None:
    """The write lands unanswered and Archidekt then stops answering altogether: the import stops,
    says that line may already be in the binder (never "everything before that is in"), and Resume
    reads it back before sending anything for it: 130 copies, not 131."""
    _setup(stack, monkeypatch)
    b = await linked(stack)
    orig = stack.ark.transport.handler
    down = {"on": False}

    async def handler(req: httpx.Request) -> httpx.Response:
        if down["on"] and "collection" in req.url.path:  # its reads too
            return httpx.Response(503, json={"detail": "down"})
        before = _copies(stack)
        resp = orig(req)
        if req.method in WRITES and COLL in req.url.path and before == 119:
            down["on"] = True
            raise httpx.ReadTimeout("no answer", request=req)
        return resp

    stack.ark.transport.handler = handler
    try:
        jid = await _start(b, 130)
        stopped = await _poll(b, f"/collection/api/import/{jid}", ("failed",))
        assert _copies(stack) == 120, _copies(stack)
        page = (await b.http.get("/collection", headers=NAV)).text
        strip = page.split("data-import-stopped", 1)[1][:2000]
        assert "Everything before that is in your binder" not in strip, strip
        assert "may already be in your binder" in strip, strip
        assert "Resume checks your binder for it first" in strip, strip
        assert stopped["done"] == 119, stopped
        down["on"] = False
        stack.ark.transport.handler = orig
        await _resume(b, jid)
        end = await _poll(b, f"/collection/api/import/{jid}", ("done", "failed"))
        assert _copies(stack) == 130, f"{_copies(stack)} copies (130 expected)"
        assert end["state"] == "done" and end["done"] == 130, end
    finally:
        stack.ark.transport.handler = orig
        await b.aclose()


async def test_a_restart_while_a_write_is_out_resumes_without_adding_it_twice(
    stack: Stack, monkeypatch
) -> None:
    """The gateway stops while a card's write is out: Archidekt takes it, the gateway never hears.
    After the restart, Resume reads that card back first: 130 copies, not 131."""
    _setup(stack, monkeypatch)
    b = await linked(stack)
    orig = stack.ark.transport.handler
    hang = asyncio.Event()
    out = asyncio.Event()

    async def handler(req: httpx.Request) -> httpx.Response:
        before = _copies(stack)
        resp = orig(req)
        if req.method in WRITES and COLL in req.url.path and before == 119 and not out.is_set():
            out.set()
            await hang.wait()  # landed; the answer never comes before the gateway stops
        return resp

    stack.ark.transport.handler = handler
    gw = stack.gw
    try:
        jid = await _start(b, 130)
        await asyncio.wait_for(out.wait(), 30)
        jobs = gw.collection_jobs
        task = jobs.tasks[SUB]
        task.cancel()  # the gateway stops (a restart) ...
        await asyncio.gather(task, return_exceptions=True)
        collection_bulk.BulkJobs(jobs.service)  # ... and starts again: the job is stopped, kept
        job = jobs.job(SUB)
        assert job["state"] == "stopped" and job["done"] == 119, job
        page = (await b.http.get("/collection", headers=NAV)).text
        strip = page.split("data-import-stopped", 1)[1][:2000]
        assert "may already be in your binder" in strip, strip
        stack.ark.transport.handler = orig
        await _resume(b, jid)
        end = await _poll(b, f"/collection/api/import/{jid}", ("done", "failed"))
        assert _copies(stack) == 130, f"{_copies(stack)} copies (130 expected)"
        assert end["state"] == "done" and end["done"] == 130, end
    finally:
        hang.set()
        stack.ark.transport.handler = orig
        await b.aclose()
