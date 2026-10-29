"""Speed policy (owner decision, 0.7.46): a member's page reads may start up to PAGE_BURST at once
instead of one a second, inside the same per-minute total; background work keeps the one-second
gap; identical cacheable reads in flight at the same time are sent to Archidekt once."""

from __future__ import annotations

import asyncio
import contextvars
import time
from typing import Any

import httpx
import pytest

from mtg_gateway.archidekt import PAGE_BURST, ArchidektClient, ArchidektError, Pacer, page_turn
from tests.fake_archidekt import FakeArchidekt


async def _held_call(
    pacer: Pacer, name: str, started: list[tuple[str, float]], release: asyncio.Event
) -> None:
    async with pacer:
        started.append((name, time.monotonic()))
        await release.wait()


def _as_page(coro: Any) -> asyncio.Task[Any]:
    ctx = contextvars.copy_context()
    ctx.run(page_turn.set, True)
    return asyncio.get_running_loop().create_task(coro, context=ctx)


async def test_page_reads_burst_at_once_then_keep_the_gap() -> None:
    assert PAGE_BURST == 5
    pacer = Pacer(0.5, 30)
    started: list[tuple[str, float]] = []
    release = asyncio.Event()
    t0 = time.monotonic()
    pages = [_as_page(_held_call(pacer, f"p{n}", started, release)) for n in range(PAGE_BURST + 1)]
    await asyncio.sleep(0.2)
    # five page reads are out at once (before: one, then one every half second, two out at most)
    assert [n for n, _ in started] == [f"p{n}" for n in range(PAGE_BURST)]
    assert all(at - t0 < 0.15 for _, at in started)
    release.set()  # five are out at most; the sixth then waits for the bucket to refill: the gap
    await asyncio.gather(*pages)
    assert len(started) == PAGE_BURST + 1
    assert started[-1][1] - t0 >= 0.45


async def test_background_work_keeps_the_gap_and_one_slot_after_a_burst() -> None:
    pacer = Pacer(0.3, 30)
    started: list[tuple[str, float]] = []
    release = asyncio.Event()
    release.set()
    for n in range(PAGE_BURST):
        await _as_page(_held_call(pacer, f"p{n}", started, release))
    t0 = time.monotonic()
    await _held_call(pacer, "sync", started, release)  # the bucket is empty: wait for a refill
    assert time.monotonic() - t0 >= 0.25
    t1 = time.monotonic()
    await _held_call(pacer, "sync2", started, release)  # and the gap after it
    assert time.monotonic() - t1 >= 0.25
    held = asyncio.Event()
    a = asyncio.create_task(_held_call(pacer, "a", started, held))
    b = asyncio.create_task(_held_call(pacer, "b", started, held))
    await asyncio.sleep(1.0)
    assert [n for n, _ in started].count("b") == 0  # background still has one call out at a time
    held.set()
    await asyncio.gather(a, b)


async def test_a_burst_never_passes_the_per_minute_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Pacer, "WINDOW", 1.5)
    pacer = Pacer(0.05, 6)
    started: list[tuple[str, float]] = []
    release = asyncio.Event()
    release.set()
    t0 = time.monotonic()
    await asyncio.gather(*(_as_page(_held_call(pacer, f"p{n}", started, release)) for n in range(9)))
    times = sorted(at for _, at in started)
    for i, at in enumerate(times):
        assert sum(1 for other in times[i:] if other - at < Pacer.WINDOW - 0.01) <= 6
    assert time.monotonic() - t0 >= 1.4  # the seventh waited for the window


# -- single-flight -----------------------------------------------------------------------------------
def _slow_client(ark: FakeArchidekt, delay: float, **kw: Any) -> ArchidektClient:
    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(delay)
        return ark.handle(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(slow))
    return ArchidektClient("https://archidekt.com/api", "test", Pacer(0.0), http=http, cache_seconds=60, **kw)


def _reads(ark: FakeArchidekt, path: str) -> int:
    return sum(1 for m, p in ark.calls if m == "GET" and p == path)


async def test_three_members_asking_for_the_same_search_at_once_cause_one_read() -> None:
    ark = FakeArchidekt()
    client = _slow_client(ark, 0.2)
    got = await asyncio.gather(*(client.search_decks(name="Sample") for _ in range(3)))
    assert _reads(ark, "/api/decks/v3/") == 1
    assert got[0]["decks"] and got[0] == got[1] == got[2] and got[0] is not got[1]  # each its own copy
    assert client.stats["shared"] == 2


async def test_a_shared_read_failing_fails_every_waiter_and_is_not_kept() -> None:
    async def broken(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.1)
        return httpx.Response(500, text="no")

    client = ArchidektClient(
        "https://archidekt.com/api",
        "test",
        Pacer(0.0),
        http=httpx.AsyncClient(transport=httpx.MockTransport(broken)),
        cache_seconds=60,
    )
    out = await asyncio.gather(*(client.search_decks(name="x") for _ in range(3)), return_exceptions=True)
    assert all(isinstance(e, ArchidektError) and e.kind == "unavailable" for e in out)
    assert client.stats["requests"] == 1 and not client._flights


async def test_a_waiter_does_not_join_a_read_that_started_before_a_write() -> None:
    ark = FakeArchidekt()
    client = _slow_client(ark, 0.2)
    first = asyncio.create_task(client.search_decks(name="Sample"))
    await asyncio.sleep(0.05)
    client._writes_begun += 2  # a write began and ended meanwhile
    await client.search_decks(name="Sample")
    await first
    assert _reads(ark, "/api/decks/v3/") == 2


async def test_member_reads_of_their_own_decks_are_never_shared() -> None:
    ark = FakeArchidekt()
    client = _slow_client(ark, 0.1)
    await asyncio.gather(*(client.get_deck("tok", "42") for _ in range(2)))
    assert _reads(ark, "/api/decks/42/") == 2


async def test_a_cancelled_first_reader_hands_the_read_on() -> None:
    ark = FakeArchidekt()
    client = _slow_client(ark, 0.2)
    first = asyncio.create_task(client.search_decks(name="Sample"))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(client.search_decks(name="Sample"))
    await asyncio.sleep(0.05)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    got = await asyncio.wait_for(second, 2)
    assert got["decks"]
    assert not client._flights
