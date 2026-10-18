"""Release 0.7.29: the deck sync's branches that had no test, driven
on a stand-in for the deck service so each branch is reached on purpose: the read cap, a slow
deck list, a busy pacer, a deck being written, the round stopping when Archidekt struggles, the
timer loop surviving a bad round, and the seen-list bound."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from mtg_gateway import sync as sync_mod
from mtg_gateway.decks import DeckError
from mtg_gateway.sync import DeckSync


class _Decks:
    """What DeckSync uses of DeckService, with every call recorded."""

    def __init__(self, waiting: dict[str, list[str]], listed: dict[str, dict[str, str]]):
        self.waiting = waiting  # sub -> deck ids with waiting proposals
        self.listed = listed  # sub -> {deck id: updatedAt} of the member's own decks
        self.calls: list[tuple[str, ...]] = []
        self.paused = False
        self.busy_decks: set[str] = set()
        self.busy_after_read: set[str] = set()
        self.list_answer: Any = "list"
        self.list_error: dict[str, str] = {}  # sub -> DeckError kind raised by the list read
        self.gone: set[str] = set()  # decks someone else owns that Archidekt says are gone
        self.client = SimpleNamespace(pacer=SimpleNamespace(paused=lambda: self.paused))
        self.db = SimpleNamespace(
            pending_baselines=self._baselines,
            get_link=lambda sub: {"sub": sub},
            needs_look_since=lambda p: None,
        )

    def _baselines(self, sub: str | None = None) -> list[dict[str, str]]:
        subs = [sub] if sub else list(self.waiting)
        return [{"owner_sub": s, "deck_id": d} for s in subs for d in self.waiting.get(s, [])]

    def archidekt_busy(self) -> bool:
        return False

    async def sync_deck_list(self, sub: str) -> list[dict[str, str]] | None:
        self.calls.append(("list", sub))
        if sub in self.list_error:
            raise DeckError(self.list_error[sub], "Archidekt says no")
        if self.list_answer is None:
            return None
        return [{"id": d, "updated_at": u} for d, u in self.listed.get(sub, {}).items()]

    def deck_busy(self, sub: str, deck_id: str) -> bool:
        return deck_id in self.busy_decks

    async def sync_read_deck(self, sub: str, deck_id: str) -> Any:
        self.calls.append(("read", deck_id))
        if deck_id in self.busy_after_read:
            self.busy_decks.add(deck_id)
        return SimpleNamespace(id=deck_id)

    async def sync_read_any_deck(self, sub: str, deck_id: str) -> Any:
        self.calls.append(("read_any", deck_id))
        if deck_id in self.gone:
            raise DeckError("not_found", "Deck not found")
        return SimpleNamespace(id=deck_id)

    def recheck_waiting(self, sub: str, deck: Any) -> dict[str, str]:
        self.calls.append(("recheck", deck.id))
        return {f"p-{deck.id}": "needs_look"}


def _sync(decks: _Decks) -> DeckSync:
    return DeckSync(decks, 30)  # type: ignore[arg-type]


async def test_the_read_cap_leaves_the_rest_for_the_next_check() -> None:
    ids = [str(i) for i in range(1, sync_mod.MAX_DECK_READS + 3)]
    decks = _Decks({"u": ids}, {"u": {d: "t1" for d in ids}})
    sync = _sync(decks)
    out = await sync.check("u")
    assert out["reads"] == sync_mod.MAX_DECK_READS and out["skipped"] == "read_cap"
    # the next check reads the decks left over, not the ones already compared at this updatedAt
    decks.calls.clear()
    out = await sync.check("u")
    assert [c[1] for c in decks.calls if c[0] == "read"] == ids[sync_mod.MAX_DECK_READS :]
    assert "skipped" not in out


async def test_a_slow_deck_list_skips_the_check() -> None:
    decks = _Decks({"u": ["1"]}, {"u": {"1": "t1"}})
    decks.list_answer = None
    assert await _sync(decks).check("u") == {"skipped": "slow"}
    assert not [c for c in decks.calls if c[0] == "read"]


async def test_a_paused_pacer_skips_before_any_request() -> None:
    decks = _Decks({"u": ["1"]}, {"u": {"1": "t1"}})
    decks.paused = True
    assert await _sync(decks).check("u") == {"skipped": "busy"}
    assert decks.calls == []


async def test_a_deck_being_written_is_left_for_a_later_check() -> None:
    decks = _Decks({"u": ["1", "2"]}, {"u": {"1": "t1", "2": "t1"}})
    decks.busy_decks = {"1"}
    decks.busy_after_read = {"2"}  # an apply started while it was read
    sync = _sync(decks)
    out = await sync.check("u")
    assert ("read", "1") not in decks.calls and ("read", "2") in decks.calls
    assert ("recheck", "2") not in decks.calls and out["flagged"] == []
    # neither was marked seen: both are compared once the writes end
    decks.busy_decks.clear()
    decks.busy_after_read.clear()
    decks.calls.clear()
    out = await sync.check("u")
    assert {c[1] for c in decks.calls if c[0] == "recheck"} == {"1", "2"}


async def test_someone_elses_deck_is_read_again_only_after_a_gap(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(sync_mod.time, "monotonic", lambda: now[0])
    decks = _Decks({"u": ["1", "99"]}, {"u": {"1": "t1"}})
    sync = _sync(decks)
    await sync.check("u")
    assert ("read_any", "99") in decks.calls
    # a page opened a few minutes later: its own deck is unchanged (same updatedAt), and the copy
    # of a public deck, which has no such signal, was read lately
    decks.calls.clear()
    now[0] += sync_mod.OPEN_GAP
    await sync.check("u")
    assert [c for c in decks.calls if c[0].startswith("read")] == []
    now[0] += sync_mod.OTHER_GAP
    await sync.check("u")
    assert [c for c in decks.calls if c[0].startswith("read")] == [("read_any", "99")]


async def test_someone_elses_deck_that_is_gone_is_left_alone_for_a_while(monkeypatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(sync_mod.time, "monotonic", lambda: now[0])
    decks = _Decks({"u": ["98", "99"]}, {"u": {}})
    decks.gone = {"98"}
    sync = _sync(decks)
    out = await sync.check("u")
    # the gone deck doesn't end the check: the next one is still compared
    assert "error" not in out and ("recheck", "99") in decks.calls and ("recheck", "98") not in decks.calls
    now[0] += sync_mod.OTHER_GAP
    decks.calls.clear()
    await sync.check("u")
    assert [c for c in decks.calls if c[0].startswith("read")] == [("read_any", "99")]
    now[0] += sync_mod.GONE_GAP
    decks.calls.clear()
    await sync.check("u")
    assert ("read_any", "98") in decks.calls


@pytest.mark.parametrize("kind", ["unavailable", "rate_limited"])
async def test_the_round_stops_when_archidekt_struggles(kind: str) -> None:
    decks = _Decks({"a": ["1"], "b": ["2"]}, {"a": {"1": "t"}, "b": {"2": "t"}})
    decks.list_error = {"a": kind}
    assert await _sync(decks).run_round() == 1
    assert [c for c in decks.calls if c[0] == "list"] == [("list", "a")]


async def test_another_error_ends_only_that_members_check() -> None:
    decks = _Decks({"a": ["1"], "b": ["2"]}, {"a": {"1": "t"}, "b": {"2": "t"}})
    decks.list_error = {"a": "auth"}
    assert await _sync(decks).run_round() == 2
    assert ("read", "2") in decks.calls


async def test_the_seen_list_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(sync_mod, "MAX_SEEN", 3)
    decks = _Decks({"u": ["1"]}, {"u": {"1": "t"}})
    sync = _sync(decks)
    sync._seen = {("x", str(i)): "t" for i in range(3)}
    await sync.check("u")
    assert sync._seen == {("u", "1"): "t"}


async def test_the_timer_waits_then_survives_a_bad_round(monkeypatch) -> None:
    decks = _Decks({}, {})
    sync = _sync(decks)
    slept: list[float] = []
    rounds = {"n": 0}

    async def fake_sleep(seconds: float) -> None:
        slept.append(seconds)
        if len(slept) >= 3:
            raise asyncio.CancelledError

    async def bad_round() -> int:
        rounds["n"] += 1
        raise RuntimeError("surprise")

    monkeypatch.setattr(sync_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(sync, "run_round", bad_round)
    with pytest.raises(asyncio.CancelledError):
        await sync.loop()
    assert slept == [sync_mod.START_DELAY, sync.interval, sync.interval] and rounds["n"] == 2
