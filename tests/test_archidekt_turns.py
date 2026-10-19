"""Opening a page is not stuck behind an assistant's Archidekt work: a member's Archidekt work past
their concurrency cap waits for one of their own slots instead of being refused at once, the
member's pages first, and a page's requests take their turn in the shared pacer before the
waiting requests of assistants, applies and the deck sync. The cap, the gap between requests and
the per-minute cap stay as they are."""

from __future__ import annotations

import asyncio
import contextvars
from pathlib import Path
from typing import Any

import pytest

from mtg_gateway import archidekt as archidekt_mod
from mtg_gateway import decks as decks_mod
from mtg_gateway.archidekt import Pacer, TurnLock, page_turn, parse_deck
from mtg_gateway.db import Database
from mtg_gateway.decks import DeckError, DeckService, MemberCache

from .conftest import Stack, make_settings
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, call, linked_user


def _deck() -> Any:
    return parse_deck(FakeArchidekt().decks[42])


class _Gated:
    """get_deck that waits for ``release``; records the order the reads started in."""

    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started: list[str] = []

    async def get_deck(self, token: str | None, deck_id: str) -> Any:
        self.started.append(deck_id)
        await self.release.wait()
        return _deck()


def _service(tmp_path: Path, client: Any) -> DeckService:
    return DeckService(make_settings(tmp_path), Database(tmp_path / "t.sqlite"), client)


def _read(svc: DeckService, deck_id: str, *, page: bool = False) -> asyncio.Task[Any]:
    ctx = contextvars.copy_context()
    ctx.run(page_turn.set, page)
    return asyncio.get_running_loop().create_task(svc.get_any_deck("alice", deck_id), context=ctx)


# -- the member's slots ----------------------------------------------------------------------------
async def test_work_past_the_cap_waits_for_a_slot_instead_of_being_refused(tmp_path: Path) -> None:
    gated = _Gated()
    svc = _service(tmp_path, gated)
    held = [_read(svc, str(n)) for n in range(1, decks_mod.MAX_ARCHIDEKT_PER_USER + 1)]
    await asyncio.sleep(0.01)
    late = _read(svc, "99")
    await asyncio.sleep(0.01)
    assert not late.done()  # waiting, not refused
    assert "99" not in gated.started  # and not in the pacer: the cap still holds
    gated.release.set()
    await asyncio.gather(*held, late)
    assert gated.started[-1] == "99"
    assert svc._archidekt_in_flight == {} and svc._slot_waiters == {}


async def test_a_page_gets_the_next_slot_before_an_assistant(tmp_path: Path) -> None:
    gated = _Gated()
    svc = _service(tmp_path, gated)
    held = [_read(svc, str(n)) for n in range(1, decks_mod.MAX_ARCHIDEKT_PER_USER + 1)]
    await asyncio.sleep(0.01)
    assistant = _read(svc, "70")
    await asyncio.sleep(0.01)
    page = _read(svc, "80", page=True)
    await asyncio.sleep(0.01)
    gated.release.set()
    await asyncio.gather(*held, assistant, page)
    assert gated.started.index("80") < gated.started.index("70")


async def test_the_wait_for_a_slot_is_bounded(tmp_path: Path) -> None:
    gated = _Gated()
    svc = _service(tmp_path, gated)
    svc.archidekt_slot_wait = 0.05
    held = [_read(svc, str(n)) for n in range(1, decks_mod.MAX_ARCHIDEKT_PER_USER + 1)]
    await asyncio.sleep(0.01)
    with pytest.raises(DeckError) as err:
        await svc.get_any_deck("alice", "99")
    assert err.value.kind == "rate_limited"
    assert svc._slot_waiters == {}
    gated.release.set()
    await asyncio.gather(*held)
    assert svc._archidekt_in_flight == {}


async def test_only_so_many_may_wait(tmp_path: Path) -> None:
    gated = _Gated()
    svc = _service(tmp_path, gated)
    held = [_read(svc, str(n)) for n in range(1, decks_mod.MAX_ARCHIDEKT_PER_USER + 1)]
    await asyncio.sleep(0.01)
    waiting = [_read(svc, str(100 + n)) for n in range(decks_mod.MAX_ARCHIDEKT_WAITING_PER_USER)]
    await asyncio.sleep(0.01)
    with pytest.raises(DeckError) as err:
        await svc.get_any_deck("alice", "99")
    assert err.value.kind == "rate_limited"
    other = asyncio.create_task(svc.get_any_deck("bob", "5"))  # another member is not held up
    await asyncio.sleep(0.01)
    assert "5" in gated.started
    gated.release.set()
    await asyncio.gather(*held, *waiting, other)
    assert svc._archidekt_in_flight == {} and svc._slot_waiters == {}


async def test_a_cancelled_waiter_leaves_no_slot_behind(tmp_path: Path) -> None:
    gated = _Gated()
    svc = _service(tmp_path, gated)
    held = [_read(svc, str(n)) for n in range(1, decks_mod.MAX_ARCHIDEKT_PER_USER + 1)]
    await asyncio.sleep(0.01)
    gone = _read(svc, "98")
    await asyncio.sleep(0.01)
    gone.cancel()
    with pytest.raises(asyncio.CancelledError):
        await gone
    assert svc._slot_waiters == {}
    gated.release.set()
    await asyncio.gather(*held)
    assert svc._archidekt_in_flight == {}
    await svc.get_any_deck("alice", "42")  # every slot came back


async def test_a_waiter_cancelled_as_it_is_handed_a_slot_passes_it_on(tmp_path: Path) -> None:
    gated = _Gated()
    svc = _service(tmp_path, gated)
    svc.max_archidekt_per_user = 1
    first = _read(svc, "1")
    await asyncio.sleep(0.01)
    a, b = _read(svc, "2"), _read(svc, "3")
    await asyncio.sleep(0.01)
    svc._give_slot("alice")  # hand the slot to ``a``...
    a.cancel()  # ...which is cancelled before it runs again
    with pytest.raises(asyncio.CancelledError):
        await a
    gated.release.set()
    await asyncio.gather(b)
    assert "3" in gated.started and "2" not in gated.started
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)


# -- the shared pacer --------------------------------------------------------------------------------
async def test_turn_lock_serves_pages_first_then_in_order() -> None:
    lock = TurnLock()
    await lock.acquire()
    order: list[str] = []

    async def take(name: str, first: bool) -> None:
        await lock.acquire(first=first)
        order.append(name)
        lock.release()

    tasks = []
    for name, first in (("app1", False), ("app2", False), ("page1", True), ("page2", True)):
        tasks.append(asyncio.create_task(take(name, first)))
        await asyncio.sleep(0)
    lock.release()
    await asyncio.gather(*tasks)
    assert order == ["page1", "page2", "app1", "app2"]
    assert not lock.locked()


async def test_turn_lock_waiter_cancelled_after_hand_over_passes_it_on() -> None:
    lock = TurnLock()
    await lock.acquire()
    a = asyncio.create_task(lock.acquire())
    b = asyncio.create_task(lock.acquire())
    await asyncio.sleep(0)
    lock.release()  # handed to ``a``
    a.cancel()
    with pytest.raises(asyncio.CancelledError):
        await a
    await b  # ``a`` passed it on
    assert lock.locked()
    lock.release()
    assert not lock.locked()


async def test_a_page_request_goes_before_waiting_assistant_requests() -> None:
    pacer = Pacer(0.05)
    order: list[str] = []

    async def send(name: str) -> None:
        async with pacer:
            order.append(name)

    await send("first")  # the next call must wait out the gap: the others queue meanwhile
    tasks = [asyncio.create_task(send(f"app{n}")) for n in range(3)]
    await asyncio.sleep(0)
    ctx = contextvars.copy_context()
    ctx.run(page_turn.set, True)
    tasks.append(asyncio.get_running_loop().create_task(send("page"), context=ctx))
    await asyncio.gather(*tasks)
    # app0 had already taken the pacer and is waiting out the gap; the page goes next
    assert order == ["first", "app0", "page", "app1", "app2"]


async def test_member_cache_refresh_keeps_a_pages_turn_but_not_the_syncs() -> None:
    cache = MemberCache(60, 600)
    seen: list[bool] = []

    async def fetch() -> list[int]:
        seen.append(page_turn.get())
        return [1]

    page_turn.set(True)
    try:
        await cache.get("alice", fetch)
        cache.drop("alice")
        await cache.refreshed("alice", fetch, wait=1.0)  # the deck sync's read
    finally:
        page_turn.set(False)
    await cache.get("bob", fetch)
    assert seen == [True, False, False]


# -- the app ------------------------------------------------------------------------------------------
async def test_pages_take_the_page_turn_and_assistants_do_not(stack: Stack, monkeypatch) -> None:
    seen: list[bool] = []
    real = Pacer.__aenter__

    async def recording(self: Pacer) -> None:
        seen.append(archidekt_mod.page_turn.get())
        await real(self)

    monkeypatch.setattr(Pacer, "__aenter__", recording)
    token = await linked_user(stack)
    seen.clear()
    out = await call(stack.h, token, "get_deck", {"deck_ref": "42"})
    assert seen and not any(seen), out
    seen.clear()
    b = Browser(stack.h)
    await b.login("/account")
    try:
        r = await b.http.get("/decks/42")
        assert r.status_code == 200, r.text
        r = await b.http.get("/api/decks/mine")
        assert r.status_code == 200, r.text
    finally:
        await b.aclose()
    assert seen and all(seen)
