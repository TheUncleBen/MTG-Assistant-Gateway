"""Page speed with a linked Archidekt account (T-037): the per-member deck-list cache (fresh,
stale-while-revalidate, dropped by writes), pages that render from it without an Archidekt call,
one listing per cold load, the render-first placeholder and /api/decks/mine, the collection's
first-page cache, request timing (Server-Timing) and the throttled last_used_at write."""

from __future__ import annotations

import asyncio
import re

import pytest

from mtg_gateway.decks import MemberCache

from .test_decks_and_proxy import Browser, Stack, call, linked_user, stack, structured

__all__ = ["stack"]

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
TIMING = re.compile(r"^total;dur=\d+(\.\d+)?, archidekt;dur=\d+(\.\d+)?, idp;dur=\d+(\.\d+)?$")


def _lists(stack: Stack) -> int:
    return sum(1 for m, p in stack.ark.calls if m == "GET" and p == "/api/decks/v3/")


def _collection_reads(stack: Stack) -> int:
    return sum(1 for m, p in stack.ark.calls if m == "GET" and p.startswith("/api/collection/"))


async def _linked(stack: Stack, path: str = "/") -> Browser:
    b = Browser(stack.h)
    await b.login(path)
    r = await b.link("alice", "pw-alice")
    assert r.status_code == 303, r.text
    return b


# -- the cache itself ----------------------------------------------------------------------------
async def test_member_cache_fresh_stale_and_drop() -> None:
    cache = MemberCache(fresh=60, stale=600)
    answers = [["a"], ["b"], ["c"], ["c"]]
    calls = 0

    async def fetch() -> list[str]:
        nonlocal calls
        calls += 1
        await asyncio.sleep(0)
        return answers[calls - 1]

    assert await cache.get("u1", fetch) == ["a"] and calls == 1
    assert await cache.get("u1", fetch) == ["a"] and calls == 1  # fresh: no fetch
    assert cache.peek("u1") == (["a"], "fresh") and cache.fetched_at("u1") is not None
    # stale: the old value at once, one refresh in the background
    cache.fresh = 0
    assert await cache.get("u1", fetch) == ["a"] and cache.refreshing("u1")
    await asyncio.sleep(0.01)
    assert calls == 2 and cache.peek("u1")[0] == ["b"]
    # a write drops it; the next read fetches again
    cache.fresh = 60
    cache.drop("u1")
    assert cache.peek("u1") == (None, "miss")
    assert await cache.get("u1", fetch) == ["c"] and calls == 3
    # a drop while a fetch is in flight keeps that fetch's (possibly pre-write) answer out
    cache.drop("u1")
    started = asyncio.Event()

    async def slow() -> list[str]:
        started.set()
        await asyncio.sleep(0.02)
        return ["old"]

    task = asyncio.ensure_future(cache.get("u1", slow))
    await started.wait()
    cache.drop("u1")
    assert await task == ["old"] and cache.peek("u1") == (None, "miss")
    # a reader arriving after the drop does not share the fetch that started before it
    started.clear()
    task = asyncio.ensure_future(cache.get("u1", slow))
    await started.wait()
    cache.drop("u1")
    fresh = asyncio.ensure_future(cache.get("u1", fetch))  # fetch() answers ["c"] again: calls == 4
    assert await task == ["old"]
    assert await fresh == answers[2] and calls == 4 and cache.peek("u1") == (answers[2], "fresh")
    await cache.aclose()


async def test_member_cache_wait_gives_up_but_the_fetch_lands() -> None:
    cache = MemberCache(fresh=60, stale=600)

    async def slow() -> list[str]:
        await asyncio.sleep(0.05)
        return ["late"]

    assert await cache.get("u1", slow, wait=0) is None  # the shell can render now
    assert cache.refreshing("u1")
    assert await cache.get("u1", slow) == ["late"]  # shares the fetch in flight
    assert cache.peek("u1") == (["late"], "fresh")


# -- pages served from the cache -----------------------------------------------------------------
async def test_one_listing_per_cold_load_then_none_while_warm(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        assert _lists(stack) == 0
        r = await b.http.get("/", headers=NAV)
        assert r.status_code == 200 and "Sample Commander Deck" in r.text
        assert _lists(stack) == 1  # one ownerId listing, not an ownerUsername one plus an ownerId one
        assert stack.ark.search_params[-1].get("ownerId") == "77"
        for path in ("/decks", "/", "/decks?view=list&order=name", "/decks?q=sample"):
            r = await b.http.get(path, headers=NAV)
            assert r.status_code == 200 and "Sample Commander Deck" in r.text, path
        assert _lists(stack) == 1  # every page view came from memory
        token = await linked_user(stack)
        out = structured(await call(stack.h, token, "list_my_decks"))
        assert out["ok"] and [d["id"] for d in out["decks"]] == ["42"]
        assert _lists(stack) == 2  # linked_user relinked the account: the list was dropped, read once
        out = structured(await call(stack.h, token, "list_my_decks"))
        assert out["ok"] and _lists(stack) == 2  # the tool shares the pages' list
    finally:
        await b.aclose()


async def test_a_gateway_write_drops_the_members_deck_list(stack: Stack) -> None:
    b = await _linked(stack, "/decks")
    try:
        r = await b.http.get("/decks", headers=NAV)
        assert "Total decks: 1" in r.text and _lists(stack) == 1
        csrf = await b.csrf("/decks")
        made = await b.http.post(
            "/decks/new",
            data={"csrf": csrf, "name": "Fresh brew", "format": "commander", "source": "1 Sol Ring"},
        )
        assert made.status_code == 303, made.text
        r = await b.http.get("/decks", headers=NAV)
        assert "Fresh brew" in r.text and "Total decks: 2" in r.text
        assert _lists(stack) == 2  # read again after the write, once
    finally:
        await b.aclose()


async def test_stale_list_is_served_while_a_refresh_runs(stack: Stack) -> None:
    b = await _linked(stack, "/decks")
    svc = stack.h.app.state.gateway.decks
    try:
        assert "Sample Commander Deck" in (await b.http.get("/decks", headers=NAV)).text
        stack.ark.decks[44] = dict(stack.ark.decks[42], id=44, name="Made on the site")
        svc.deck_lists.fresh = 0  # the list is now stale (but not expired)
        r = await b.http.get("/decks", headers=NAV)
        assert r.status_code == 200 and "Made on the site" not in r.text  # the stale list, at once
        await asyncio.sleep(0.05)  # the background refresh
        assert _lists(stack) == 2
        svc.deck_lists.fresh = 60
        r = await b.http.get("/decks", headers=NAV)
        assert "Made on the site" in r.text and _lists(stack) == 2
    finally:
        await b.aclose()


async def test_unlink_forgets_the_list(stack: Stack) -> None:
    b = await _linked(stack)
    svc = stack.h.app.state.gateway.decks
    try:
        await b.http.get("/", headers=NAV)
        assert svc.deck_lists.peek("user-1")[1] == "fresh"
        r = await b.http.post("/account", data={"csrf": await b.csrf(), "action": "unlink"})
        assert r.status_code == 303
        assert svc.deck_lists.peek("user-1") == (None, "miss")
        r = await b.http.get("/decks", headers=NAV)
        assert r.status_code == 200 and "Link your Archidekt account" in r.text
    finally:
        await b.aclose()


# -- render first --------------------------------------------------------------------------------
async def test_cold_pages_render_a_placeholder_and_the_json_endpoint_fills_it(stack: Stack) -> None:
    b = await _linked(stack, "/decks")
    svc = stack.h.app.state.gateway.decks
    svc.deck_list_wait = 0.02  # the page waits this long for a cold list
    real_list = svc.client.list_decks

    async def slow_list(*args, **kw):  # Archidekt (its pacer queue) takes longer than that
        await asyncio.sleep(0.1)
        return await real_list(*args, **kw)

    svc.client.list_decks = slow_list
    try:
        r = await b.http.get("/decks?order=name&view=list", headers=NAV)
        assert r.status_code == 200
        assert (
            "data-decks-src='/api/decks/mine?shape=list&amp;q=&amp;order=name&amp;view=list&amp;folder='"
            in r.text
        )
        assert "decklist list skeleton" in r.text and "Total decks: …" in r.text
        assert "id='folder-field' hidden" in r.text and "/static/decks.js" in r.text
        assert "Sample Commander Deck" not in r.text
        assert "script-src 'self'" in r.headers["content-security-policy"]
        assert "connect-src 'self'" in r.headers["content-security-policy"]
        # the script's request: the list as the page would have rendered it
        j = await b.http.get("/api/decks/mine?shape=list&order=name&view=list")
        assert j.status_code == 200 and j.headers["cache-control"] == "no-store"
        data = j.json()
        assert data["ok"] is True and data["total"] == 1 and data["count"] == 1 and data["folders"] == []
        assert data["fetched_at"] and data["decks"][0]["id"] == "42"
        assert data["decks"][0]["url"] == "https://archidekt.com/decks/42"
        assert "decklist list'" in data["html"] and "Sample Commander Deck" in data["html"]
        assert _lists(stack) == 1  # the page's own read, shared with the endpoint
        # home, cold again
        svc.deck_lists.drop("user-1")
        r = await b.http.get("/", headers=NAV)
        assert r.status_code == 200 and "data-decks-src='/api/decks/mine?shape=recent'" in r.text
        assert "recent skeleton" in r.text and "Sample Commander Deck" not in r.text
        j = await b.http.get("/api/decks/mine?shape=recent")
        data = j.json()
        assert data["ok"] and data["count"] == 1 and "All decks (1)" in data["html"]
        assert "Sample Commander Deck" in data["html"] and "class='recent'" in data["html"]
        assert _lists(stack) == 2
        # warm again: the page carries the list itself
        r = await b.http.get("/", headers=NAV)
        assert "Sample Commander Deck" in r.text and "data-decks-src" not in r.text
    finally:
        await b.aclose()


async def test_decks_json_needs_a_session_and_reports_problems(stack: Stack) -> None:
    r = await stack.h.http.get("/api/decks/mine")
    assert r.status_code == 401 and r.json()["error"] == "unauthenticated"
    b = Browser(stack.h)
    try:
        await b.login("/")
        j = await b.http.get("/api/decks/mine")  # signed in, no Archidekt link
        assert j.status_code == 200
        data = j.json()
        assert data["ok"] is False and data["error"] == "not_linked" and data["decks"] == []
        assert "notice error" in data["html"]
    finally:
        await b.aclose()


# -- the collection's first page -----------------------------------------------------------------
async def test_collection_first_page_is_cached_until_a_write(stack: Stack) -> None:
    b = await _linked(stack, "/collection")
    try:
        csrf = await b.csrf("/collection")
        n = _collection_reads(stack)
        for path in ("/collection", "/collection?view=list", "/collection"):
            assert (await b.http.get(path, headers=NAV)).status_code == 200
        assert _collection_reads(stack) == n  # the csrf() GET read it; the three views did not
        r = await b.http.get("/collection?sort=edition", headers=NAV)  # another sort: its own read
        assert r.status_code == 200 and _collection_reads(stack) == n + 1
        r = await b.http.get("/collection?q=sol", headers=NAV)  # a filter is never cached
        assert r.status_code == 200 and _collection_reads(stack) == n + 2
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "add", "name": "Sol Ring", "quantity": "2"}
        )
        assert r.status_code == 303, r.text
        after_write = _collection_reads(stack)
        r = await b.http.get("/collection", headers=NAV)
        assert "Sol Ring" in r.text and "<b>1</b> entry" in r.text
        assert _collection_reads(stack) == after_write + 1  # dropped by the write, read once more
        assert (await b.http.get("/collection", headers=NAV)).status_code == 200
        assert _collection_reads(stack) == after_write + 1
    finally:
        await b.aclose()


# -- timing --------------------------------------------------------------------------------------
async def test_every_response_carries_server_timing(stack: Stack) -> None:
    r = await stack.h.http.get("/healthz")
    assert TIMING.match(r.headers["server-timing"]), r.headers["server-timing"]
    b = await _linked(stack)
    try:
        for path in ("/", "/decks", "/collection", "/api/decks/mine"):
            r = await b.http.get(path, headers=NAV)
            assert r.status_code == 200 and TIMING.match(r.headers["server-timing"]), path
        r = await stack.h.http.get("/api/decks/mine")
        assert r.status_code == 401 and TIMING.match(r.headers["server-timing"])
    finally:
        await b.aclose()


async def test_request_log_line_has_no_query_string(stack: Stack, caplog: pytest.LogCaptureFixture) -> None:
    b = await _linked(stack)
    try:
        with caplog.at_level("INFO", logger="mtg_gateway.requests"):
            r = await b.http.get("/decks?q=secret-name", headers=NAV)
            assert r.status_code == 200
        lines = [rec.getMessage() for rec in caplog.records if rec.name == "mtg_gateway.requests"]
        assert any(re.match(r"GET /decks 200 \d+ms archidekt=\d+ms idp=\d+ms$", ln) for ln in lines), lines
        assert not any("secret-name" in ln or "user-1" in ln for ln in lines)
        # a path with a line break in it cannot forge a second log line
        with caplog.at_level("INFO", logger="mtg_gateway.requests"):
            r = await b.http.get("/decks/%0AGET%20/admin%20200%0A", headers=NAV)
            assert r.status_code in (302, 303, 404), r.status_code
        lines = [rec.getMessage() for rec in caplog.records if rec.name == "mtg_gateway.requests"]
        assert all("\n" not in ln for ln in lines), lines
        assert any("/decks/?GET /admin 200?" in ln for ln in lines), lines
    finally:
        await b.aclose()


async def test_archidekt_time_is_counted_per_request(stack: Stack) -> None:
    b = await _linked(stack, "/decks")
    svc = stack.h.app.state.gateway.decks
    try:
        svc.deck_lists.drop("user-1")
        cold = await b.http.get("/decks", headers=NAV)
        warm = await b.http.get("/decks", headers=NAV)

        def archidekt_ms(resp) -> float:
            return float(re.search(r"archidekt;dur=([\d.]+)", resp.headers["server-timing"]).group(1))

        assert archidekt_ms(cold) > 0 and archidekt_ms(warm) == 0
    finally:
        await b.aclose()


# -- last_used_at off the hot path ---------------------------------------------------------------
async def test_touch_link_is_written_at_most_once_a_minute(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = await _linked(stack, "/decks")
    db = stack.h.app.state.gateway.db
    writes: list[str] = []
    real = db.touch_link
    monkeypatch.setattr(db, "touch_link", lambda sub: (writes.append(sub), real(sub)))
    svc = stack.h.app.state.gateway.decks
    try:
        for _ in range(3):
            svc.deck_lists.drop("user-1")
            assert (await b.http.get("/decks", headers=NAV)).status_code == 200
        assert _lists(stack) == 3 and writes == ["user-1"]
        assert db.get_link("user-1")["last_used_at"] is not None
    finally:
        await b.aclose()
