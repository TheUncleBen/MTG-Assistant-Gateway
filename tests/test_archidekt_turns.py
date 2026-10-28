"""Opening a page is not stuck behind an assistant's Archidekt work: a member's Archidekt work past
their concurrency cap waits for one of their own slots instead of being refused at once, the
member's pages first, and a page's requests take their turn in the shared pacer before the
waiting requests of assistants and the deck sync. The cap, the gap between requests and
the per-minute cap stay as they are."""

from __future__ import annotations

import asyncio
import contextvars
import time
from pathlib import Path
from typing import Any

import pytest

from mtg_gateway import archidekt as archidekt_mod
from mtg_gateway import decks as decks_mod
from mtg_gateway.archidekt import ArchidektError, Pacer, TurnLock, page_turn, parse_deck
from mtg_gateway.busy import BUSY_TEXT, BUSY_TEXTS, busy_plan
from mtg_gateway.db import Database
from mtg_gateway.deckpage import deck_list_held_text
from mtg_gateway.decks import DeckError, DeckService, MemberCache

from .conftest import Stack, make_settings
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, call, linked_user, mcp_token, structured


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


async def _held_call(pacer: Pacer, name: str, started: list[str], release: asyncio.Event) -> None:
    async with pacer:
        started.append(name)
        await release.wait()


def _as_page(coro: Any) -> asyncio.Task[Any]:
    ctx = contextvars.copy_context()
    ctx.run(page_turn.set, True)
    return asyncio.get_running_loop().create_task(coro, context=ctx)


async def test_a_page_call_starts_while_a_slow_background_call_is_still_out() -> None:
    """The pacer spaces the starts of calls; it does not hold its turn while Archidekt answers, so a
    page's read is not stuck behind a slow background read."""
    pacer = Pacer(0.1, 30)
    started: list[str] = []
    slow, quick = asyncio.Event(), asyncio.Event()
    quick.set()
    bg = asyncio.create_task(_held_call(pacer, "sync", started, slow))
    await asyncio.sleep(0.01)
    t0 = time.monotonic()
    await asyncio.wait_for(_as_page(_held_call(pacer, "page", started, quick)), 1.0)
    took = time.monotonic() - t0
    assert started == ["sync", "page"] and not bg.done()
    assert 0.07 <= took < 0.5, took  # the gap between the two starts, nothing more
    slow.set()
    await bg


async def test_background_calls_keep_one_of_the_two_slots_free_for_pages() -> None:
    pacer = Pacer(0.0, 30)
    started: list[str] = []
    release = asyncio.Event()
    a = asyncio.create_task(_held_call(pacer, "a", started, release))
    b = asyncio.create_task(_held_call(pacer, "b", started, release))
    await asyncio.sleep(0.05)
    assert started == ["a"] and pacer.busy()  # one background call out at a time
    p1 = _as_page(_held_call(pacer, "page1", started, release))
    p2 = _as_page(_held_call(pacer, "page2", started, release))
    await asyncio.sleep(0.05)
    assert started == ["a", "page1"]  # at most two out in all
    release.set()
    await asyncio.gather(a, b, p1, p2)
    assert sorted(started) == ["a", "b", "page1", "page2"]
    assert not pacer.busy()


async def test_the_per_minute_cap_keeps_a_share_for_pages(monkeypatch: pytest.MonkeyPatch) -> None:
    """Background work (the deck sync, assistants, applies) may use all but a sixth of the per-minute
    cap; the rest is kept for the member's pages, so a long apply never leaves a page waiting a
    minute. Pages count towards the same cap: it is never passed."""
    monkeypatch.setattr(Pacer, "WINDOW", 0.6)
    pacer = Pacer(0.0, 12)  # 2 of 12 kept for pages
    done = asyncio.Event()
    done.set()
    started: list[str] = []
    for n in range(10):
        await _held_call(pacer, f"bg{n}", started, done)
    t0 = time.monotonic()
    late = asyncio.create_task(_held_call(pacer, "bg10", started, done))
    await asyncio.sleep(0.05)
    assert "bg10" not in started  # background has used its share for this minute
    await asyncio.wait_for(_as_page(_held_call(pacer, "page1", started, done)), 0.3)
    await asyncio.wait_for(_as_page(_held_call(pacer, "page2", started, done)), 0.3)
    third = _as_page(_held_call(pacer, "page3", started, done))
    await asyncio.sleep(0.05)
    assert "page3" not in started  # the cap itself still holds for pages too
    await asyncio.gather(late, third)
    assert time.monotonic() - t0 >= 0.45


# -- the app ------------------------------------------------------------------------------------------
async def test_pages_take_the_page_turn_and_assistants_do_not(stack: Stack, monkeypatch) -> None:
    seen: list[bool] = []
    real = Pacer.enter

    async def recording(self: Pacer, *, first: bool, page: bool) -> None:
        seen.append(first)
        assert first == archidekt_mod.page_turn.get()
        await real(self, first=first, page=page)

    monkeypatch.setattr(Pacer, "enter", recording)
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


# -- My decks and Home while Archidekt can't be asked --------------------------------------------
async def _paused_after_a_read(stack: Stack) -> Browser:
    """A linked member whose deck list and folders were read once, then dropped (as a write
    does), with Archidekt now answering 429."""
    b = Browser(stack.h)
    await b.login("/account")
    r = await b.link("alice", "pw-alice")
    assert r.status_code == 303, r.text
    r = await b.http.get("/decks")
    assert r.status_code == 200 and "Sample Commander Deck" in r.text, r.text
    stack.gw.decks.deck_lists.drop("user-1")
    stack.gw.decks.folder_trees.drop("user-1")
    assert stack.ark is not None
    stack.ark.inject = [(429, "60")]
    return b


async def test_my_decks_shows_the_list_as_last_read_while_archidekt_is_paused(stack: Stack) -> None:
    b = await _paused_after_a_read(stack)
    try:
        r = await b.http.get("/decks")
        assert r.status_code == 200, r.text
        assert "Sample Commander Deck" in r.text
        assert "notice warn" in r.text and "this is how it was" in r.text
        assert "Archidekt is busy" not in r.text
        r = await b.http.get("/api/decks/mine", params={"shape": "list"})
        body = r.json()
        assert body["ok"] is True and "this is how it was" in body["html"]
        r = await b.http.get("/api/decks/mine", params={"shape": "recent"})
        assert "this is how it was" in r.json()["html"]
        r = await b.http.get("/")
        assert "Sample Commander Deck" in r.text and "this is how it was" in r.text
    finally:
        await b.aclose()


async def test_the_old_folders_address_goes_to_my_decks_even_while_archidekt_is_busy(stack: Stack) -> None:
    """The Folders page is gone (its work is in My decks' boxes): its address goes to My decks
    without asking Archidekt anything."""
    b = await _paused_after_a_read(stack)
    try:
        calls = len(stack.ark.calls)
        r = await b.http.get("/folders")
        assert r.status_code == 303 and r.headers["location"] == "/decks"
        assert len(stack.ark.calls) == calls
    finally:
        await b.aclose()


async def test_an_assistant_never_gets_the_list_as_last_read(stack: Stack) -> None:
    b = await _paused_after_a_read(stack)
    await b.aclose()
    token = await mcp_token(stack.h)
    out = structured(await call(stack.h, token, "list_my_decks", {}))
    assert out["ok"] is False and out["error"] == "rate_limited", out


async def test_a_list_read_fine_shows_no_notice(stack: Stack) -> None:
    b = Browser(stack.h)
    await b.login("/account")
    await b.link("alice", "pw-alice")
    try:
        r = await b.http.get("/decks")
        assert "Sample Commander Deck" in r.text and "this is how it was" not in r.text
    finally:
        await b.aclose()


async def test_the_last_read_list_is_forgotten_with_the_member(stack: Stack) -> None:
    b = await _paused_after_a_read(stack)
    try:
        stack.gw.decks.forget_member("user-1")
        assert stack.gw.decks.deck_lists.last("user-1", 3600) is None
        r = await b.http.get("/decks")
        assert "this is how it was" not in r.text
        assert r.status_code == 429  # nothing to show: the busy page, as before
    finally:
        await b.aclose()


def test_member_cache_keeps_the_last_full_value_through_drop_only() -> None:
    cache = MemberCache(60, 600)
    cache._last["alice"] = (time.time() - 10, [1])
    cache.drop("alice")
    assert cache.last("alice", 60) is not None
    assert cache.last("alice", 5) is None  # too old for that caller
    cache.forget("alice")
    assert cache.last("alice", 60) is None


async def test_a_partial_list_is_not_kept_as_last_read() -> None:
    cache = MemberCache(60, 600, keep_for=lambda v: 30.0 if v == "partial" else None)

    async def partial() -> str:
        return "partial"

    await cache.get("alice", partial)
    assert cache.last("alice", 60) is None


def test_deck_list_held_text_says_how_old() -> None:
    assert "how it was 5 minutes ago" in deck_list_held_text(1000.0, now=1300.0)
    assert "how it was 1 minute ago" in deck_list_held_text(1000.0, now=1010.0)
    assert "how it was 2 hours ago" in deck_list_held_text(0.0, now=7300.0)
    assert "how it was 1 hour ago" in deck_list_held_text(0.0, now=3700.0)


# -- the busy page says why --------------------------------------------------------------------------
def test_busy_page_names_the_members_own_limit_the_budget_and_archidekts_pause() -> None:
    own = DeckError("rate_limited", "x")
    own.busy_cause = "member_slots"
    assert busy_plan(own).text == BUSY_TEXTS["member_slots"]
    spent = DeckError("rate_limited", "x")
    spent.busy_cause = "budget"
    assert busy_plan(spent).text == BUSY_TEXTS["budget"]
    try:
        try:
            raise ArchidektError("rate_limited", "Archidekt asked us to slow down; try again later")
        except ArchidektError as inner:
            raise DeckError("rate_limited", str(inner)) from inner
    except DeckError as outer:
        assert busy_plan(outer).text == BUSY_TEXTS["archidekt"]
    assert busy_plan(DeckError("rate_limited", "x")).text == BUSY_TEXT  # it doesn't say why


async def test_the_slot_and_budget_refusals_name_their_cause(tmp_path: Path) -> None:
    gated = _Gated()
    svc = _service(tmp_path, gated)
    svc.archidekt_slot_wait = 0
    held = [_read(svc, str(n)) for n in range(1, decks_mod.MAX_ARCHIDEKT_PER_USER + 1)]
    await asyncio.sleep(0.01)
    with pytest.raises(DeckError) as err:
        await svc.get_any_deck("alice", "99")
    assert err.value.busy_cause == "member_slots"
    gated.release.set()
    await asyncio.gather(*held)
    svc.archidekt_budget = decks_mod.RateBudget(1)
    await svc.get_any_deck("alice", "1")
    with pytest.raises(DeckError) as err:
        await svc.get_any_deck("alice", "2")
    assert err.value.busy_cause == "budget"
