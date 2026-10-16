"""Gentle on Archidekt: the shared per-minute cap, bounded retries with jittered backoff for reads
only, 429s honoured (waited out only inside an apply), the read cache (a hit sends nothing
upstream; any write clears deck and search reads; signed-in reads are never cached), the honest
User-Agent, and the settings, stack files and admin page that carry the limits. The answers come
from the recording-based FakeArchidekt, with failures injected in front of it."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from mtg_gateway import archidekt as ark_mod
from mtg_gateway.archidekt import ArchidektClient, ArchidektError, Pacer
from mtg_gateway.config import ConfigError, Settings, load_settings
from tests.fake_archidekt import FakeArchidekt
from tests.test_backup_and_config import _write_secrets

ROOT = Path(__file__).resolve().parent.parent


def _client(ark: FakeArchidekt, **kw) -> ArchidektClient:
    pacer = Pacer(0.0, kw.pop("max_per_minute", 0))
    return ArchidektClient("https://archidekt.com/api", "test", pacer, http=ark.client(), **kw)


@pytest.fixture
def sleeps(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record the backoff waits instead of sleeping them; jitter returns its upper bound."""
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr(ark_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ark_mod.random, "uniform", lambda lo, hi: hi)
    return waits


def _reads(ark: FakeArchidekt, path: str) -> int:
    return sum(1 for m, p in ark.calls if m == "GET" and p == path)


# -- the shared per-minute cap -------------------------------------------------------------------
async def test_the_per_minute_cap_makes_extra_requests_wait_not_fail(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(Pacer, "WINDOW", 0.4)
    ark = FakeArchidekt()
    client = _client(ark, max_per_minute=2)
    started = time.monotonic()
    for _ in range(3):
        await client.search_decks(name="Sample")
    elapsed = time.monotonic() - started
    assert _reads(ark, "/api/decks/v3/") == 3  # all three went through
    assert elapsed >= 0.35, elapsed  # the third waited for the window to free a slot


async def test_no_cap_means_no_wait() -> None:
    ark = FakeArchidekt()
    client = _client(ark)
    started = time.monotonic()
    for _ in range(5):
        await client.search_decks(name="Sample")
    assert time.monotonic() - started < 0.3


# -- retries ---------------------------------------------------------------------------------------
async def test_a_read_that_gets_5xx_is_retried_with_growing_jittered_waits(sleeps: list[float]) -> None:
    ark = FakeArchidekt()
    ark.inject = [(503, None), (502, None)]
    client = _client(ark, retries=2, backoff_base=1.0)
    out = await client.search_decks(name="Sample")
    assert out["decks"]  # the third try got the real answer
    assert _reads(ark, "/api/decks/v3/") == 3
    assert sleeps == [1.0, 2.0]  # base * 2**attempt (the jitter's upper bound here)
    assert client.stats["retries"] == 2 and client.stats["failures"] == 0


async def test_timeouts_are_retried_then_fail_loudly(sleeps: list[float]) -> None:
    ark = FakeArchidekt()
    ark.inject = [(httpx.ReadTimeout("slow"), None)] * 3
    client = _client(ark, retries=2, backoff_base=1.0)
    with pytest.raises(ArchidektError) as err:
        await client.search_decks(name="Sample")
    assert err.value.kind == "unavailable"
    assert len(ark.calls) == 3 and len(sleeps) == 2  # 1 + retries, no more
    assert client.stats["failures"] == 1


async def test_backoff_is_capped(sleeps: list[float]) -> None:
    ark = FakeArchidekt()
    ark.inject = [(500, None)] * 4
    client = _client(ark, retries=4, backoff_base=8.0)
    await client.search_decks(name="Sample")
    assert sleeps == [8.0, ark_mod.BACKOFF_CAP, ark_mod.BACKOFF_CAP, ark_mod.BACKOFF_CAP]


async def test_writes_are_never_retried(sleeps: list[float]) -> None:
    ark = FakeArchidekt()
    client = _client(ark, retries=3)
    token = (await client.login("alice", "pw-alice"))["access"]
    ark.calls.clear()
    ark.inject = [(503, None)]
    with pytest.raises(ArchidektError):
        await client.update_deck(token, "42", {"name": "Renamed"})
    assert len(ark.calls) == 1 and sleeps == []


async def test_a_429_is_not_retried_and_pauses_every_call(sleeps: list[float]) -> None:
    ark = FakeArchidekt()
    ark.inject = [(429, "30")]
    client = _client(ark, retries=3)
    with pytest.raises(ArchidektError) as err:
        await client.search_decks(name="Sample")
    assert err.value.kind == "rate_limited" and len(ark.calls) == 1 and sleeps == []
    with pytest.raises(ArchidektError) as again:
        await client.get_deck(None, "42")  # a different request: still refused, nothing sent
    assert again.value.kind == "rate_limited" and len(ark.calls) == 1
    assert client.stats["rate_limited"] == 2


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A fake clock that the patched sleep moves forward, so a Retry-After pause can run out."""
    now = [1000.0]
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(ark_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ark_mod.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ark_mod.random, "uniform", lambda lo, hi: 0.0)
    return waits


async def test_an_apply_waits_out_a_429_and_sends_the_read_again(clock: list[float]) -> None:
    ark = FakeArchidekt()
    ark.inject = [(429, "30")]
    client = _client(ark)
    note: dict = {}
    token = ark_mod.patient.set(ark_mod.Patience())
    noted = ark_mod.wait_note.set(note)
    try:
        out = await client.search_decks(name="Sample")
    finally:
        ark_mod.patient.reset(token)
        ark_mod.wait_note.reset(noted)
    assert out["decks"]
    assert _reads(ark, "/api/decks/v3/") == 2  # the 429, then the same read once the pause ran out
    assert clock == [30.0]
    assert client.stats["rate_limit_waits"] == 1 and client.stats["failures"] == 0
    assert "waiting_for_archidekt" not in note  # cleared once the wait is over


async def test_an_apply_waits_out_a_429_on_a_write_too(clock: list[float]) -> None:
    ark = FakeArchidekt()
    client = _client(ark)
    login = (await client.login("alice", "pw-alice"))["access"]
    ark.calls.clear()
    ark.inject = [(429, "5")]
    token = ark_mod.patient.set(ark_mod.Patience())
    try:
        await client.update_deck(login, "42", {"name": "Renamed"})
    finally:
        ark_mod.patient.reset(token)
    assert [m for m, _p in ark.calls] == ["PATCH", "PATCH"]  # refused once, not acted on; sent again
    assert clock == [5.0]


async def test_an_apply_gives_up_after_a_bounded_number_of_waits(clock: list[float]) -> None:
    """Q41 with Q62 (review M2): exactly RATE_LIMIT_WAITS (6) waits and 7 sends, then the plain
    rate-limit error; the breaker never cuts it short."""
    ark = FakeArchidekt()
    ark.inject = [(429, "1")] * (ark_mod.RATE_LIMIT_WAITS + 1)
    client = _client(ark)
    token = ark_mod.patient.set(ark_mod.Patience())
    try:
        with pytest.raises(ArchidektError) as err:
            await client.search_decks(name="Sample")
    finally:
        ark_mod.patient.reset(token)
    assert err.value.kind == "rate_limited"
    assert str(err.value) == ark_mod.PATIENCE_SPENT
    assert clock == [1.0] * ark_mod.RATE_LIMIT_WAITS
    assert _reads(ark, "/api/decks/v3/") == ark_mod.RATE_LIMIT_WAITS + 1


async def test_an_apply_s_sixth_wait_is_reached_and_the_seventh_send_goes_through(
    clock: list[float],
) -> None:
    """Review M2: six 429s in a row used to open the breaker after the fifth; the apply now waits
    all six out and the seventh send gets its answer."""
    ark = FakeArchidekt()
    ark.inject = [(429, "1")] * ark_mod.RATE_LIMIT_WAITS
    client = _client(ark)
    token = ark_mod.patient.set(ark_mod.Patience())
    try:
        out = await client.search_decks(name="Sample")
    finally:
        ark_mod.patient.reset(token)
    assert out["decks"]
    assert clock == [1.0] * 6 and _reads(ark, "/api/decks/v3/") == 7


async def test_an_apply_s_429s_never_open_the_breaker_for_everyone(clock: list[float]) -> None:
    """Q62 "Apply only": the apply's 429s set Archidekt's pause but do not count towards the
    breaker, so once the apply has given up, other members' calls go through as soon as the pause
    is over."""
    ark = FakeArchidekt()
    ark.inject = [(429, "1")] * 10
    client = _client(ark)
    token = ark_mod.patient.set(ark_mod.Patience())
    try:
        with pytest.raises(ArchidektError):
            await client.search_decks(name="Sample")
    finally:
        ark_mod.patient.reset(token)
    ark.inject = []
    await ark_mod.asyncio.sleep(2.0)  # Archidekt's own 1 s pause is over
    out = await client.search_decks(name="Sample")  # not an apply: refused if the breaker were open
    assert out["decks"]


async def test_outside_an_apply_429s_still_count_towards_the_breaker(clock: list[float]) -> None:
    """Q62: outside an apply nothing changes: five 429s in a row open the breaker."""
    ark = FakeArchidekt()
    ark.inject = [(429, "0")] * 5
    client = _client(ark)
    for _ in range(5):
        with pytest.raises(ArchidektError) as err:
            await client.search_decks(name="Sample")
        assert err.value.kind == "rate_limited"
    with pytest.raises(ArchidektError) as err:
        await client.search_decks(name="Sample")
    assert err.value.kind == "unavailable" and "paused after repeated failures" in str(err.value)
    assert err.value.sent is False  # refused by the pacer: nothing left the gateway
    assert _reads(ark, "/api/decks/v3/") == 5


async def test_the_wait_allowance_covers_the_whole_apply_not_each_request(clock: list[float]) -> None:
    """Q66 "Per apply" (review M3): 300 s of waiting in all across every request of one apply. Three
    requests each meeting two 429s (Retry-After 120) used to wait 720 s; now the first two waits
    use 240 s, and the next 429 asks for 120 s when 60 s are left, so the apply stops at once."""
    ark = FakeArchidekt()
    client = _client(ark)
    token = ark_mod.patient.set(ark_mod.Patience())
    try:
        ark.inject = [(429, "120"), (429, "120")]
        await client.search_decks(name="Sample")
        ark.inject = [(429, "120"), (429, "120")]
        with pytest.raises(ArchidektError) as err:
            await client.search_decks(name="Other")
    finally:
        ark_mod.patient.reset(token)
    assert err.value.kind == "rate_limited" and str(err.value) == ark_mod.PATIENCE_SPENT
    assert clock == [120.0, 120.0] and sum(clock) <= ark_mod.RATE_LIMIT_WAIT_TOTAL
    assert _reads(ark, "/api/decks/v3/") == 4  # 429, 429, the answer; then the 429 that stopped it


async def test_the_wait_count_covers_the_whole_apply_too(clock: list[float]) -> None:
    """Q66: the six waits are the apply's, not each request's."""
    ark = FakeArchidekt()
    client = _client(ark)
    token = ark_mod.patient.set(ark_mod.Patience())
    try:
        for name in ("a", "b", "c"):
            ark.inject = [(429, "1"), (429, "1")]
            await client.search_decks(name=name)
        ark.inject = [(429, "1")]
        with pytest.raises(ArchidektError) as err:
            await client.search_decks(name="d")
    finally:
        ark_mod.patient.reset(token)
    assert err.value.kind == "rate_limited" and len(clock) == 6


async def test_a_retry_after_longer_than_what_is_left_stops_the_apply_at_once(clock: list[float]) -> None:
    """Review M3 / Q66: Archidekt asks for an hour; the pacer caps the shared pause at 120 s, but an
    apply must not send again after 120 s. It stops without waiting."""
    ark = FakeArchidekt()
    ark.inject = [(429, "3600")]
    client = _client(ark)
    token = ark_mod.patient.set(ark_mod.Patience())
    try:
        with pytest.raises(ArchidektError) as err:
            await client.search_decks(name="Sample")
    finally:
        ark_mod.patient.reset(token)
    assert err.value.kind == "rate_limited" and str(err.value) == ark_mod.PATIENCE_SPENT
    assert clock == [] and _reads(ark, "/api/decks/v3/") == 1


async def test_an_apply_waits_as_long_as_archidekt_asks_past_the_shared_cap(clock: list[float]) -> None:
    """Archidekt's Retry-After is always respected: 200 s asked, within what is left, is waited in
    full, not cut to the pacer's 120 s cap."""
    ark = FakeArchidekt()
    ark.inject = [(429, "200")]
    client = _client(ark)
    token = ark_mod.patient.set(ark_mod.Patience())
    try:
        await client.search_decks(name="Sample")
    finally:
        ark_mod.patient.reset(token)
    assert clock == [200.0] and _reads(ark, "/api/decks/v3/") == 2


async def test_a_call_the_pacer_refuses_is_marked_not_sent(clock: list[float]) -> None:
    """A running Retry-After pause refuses a call before it is sent (review M1): the error says so,
    so an apply can tell that nothing was written."""
    ark = FakeArchidekt()
    client = _client(ark)
    client.pacer.record(False, 20.0)
    with pytest.raises(ArchidektError) as err:
        await client.search_decks(name="Sample")
    assert err.value.kind == "rate_limited" and err.value.sent is False and ark.calls == []


async def test_retries_count_towards_the_breaker(sleeps: list[float]) -> None:
    ark = FakeArchidekt()
    ark.inject = [(503, None)] * 5
    client = _client(ark, retries=4)
    with pytest.raises(ArchidektError):
        await client.search_decks(name="Sample")
    assert len(ark.calls) == 5
    with pytest.raises(ArchidektError) as err:
        await client.search_decks(name="Sample")
    assert "paused" in str(err.value) and len(ark.calls) == 5  # breaker open: nothing sent


# -- reading a 429's Retry-After -------------------------------------------------------------------
@pytest.mark.parametrize(
    ("value", "seconds"),
    [
        ("120", 120.0),
        (" 7 ", 7.0),
        ("0", 0.0),
        ("9" * 5000, float("inf")),  # absurd but digits: Pacer.record caps it
        (None, ark_mod.DEFAULT_RETRY_AFTER),
        ("", ark_mod.DEFAULT_RETRY_AFTER),
        ("soon", ark_mod.DEFAULT_RETRY_AFTER),
        ("-5", ark_mod.DEFAULT_RETRY_AFTER),
        ("1.5", ark_mod.DEFAULT_RETRY_AFTER),  # delay-seconds is whole digits only (RFC 9110)
        ("Wed, 99 Foo 2026 25:61:00 GMT", ark_mod.DEFAULT_RETRY_AFTER),
    ],
)
def test_retry_after_seconds_reads_delay_seconds_and_falls_back_on_garbage(value, seconds) -> None:
    assert ark_mod.retry_after_seconds(value, now=1_000_000.0) == seconds


def test_retry_after_seconds_reads_an_http_date() -> None:
    now = 1_791_000_000.0  # Sat, 03 Oct 2026 04:00:00 GMT
    ahead = "Sat, 03 Oct 2026 04:01:30 GMT"
    assert ark_mod.retry_after_seconds(ahead, now=now) == 90.0
    assert ark_mod.retry_after_seconds("Sat, 03 Oct 2026 03:59:00 GMT", now=now) == 0.0  # already past
    assert ark_mod.retry_after_seconds("Saturday, 03-Oct-26 04:01:30 GMT", now=now) == 90.0  # RFC 850 form


def test_pacer_record_caps_the_pause_and_keeps_the_longest(clock: list[float]) -> None:
    pacer = Pacer(0.0)
    assert pacer.retry_wait() == 0.0 and not pacer.paused()
    pacer.record(False, 10_000.0)
    assert pacer.retry_wait() == Pacer.MAX_RETRY_AFTER and pacer.paused()
    pacer.record(False, 5.0)  # a shorter pause never cuts a running one short
    assert pacer.retry_wait() == Pacer.MAX_RETRY_AFTER
    pacer.record(True)
    assert pacer.retry_wait() == Pacer.MAX_RETRY_AFTER  # a success does not end Archidekt's pause


def test_pacer_without_a_retry_after_sets_no_pause(clock: list[float]) -> None:
    pacer = Pacer(0.0)
    pacer.record(False, None)
    pacer.record(False, 0.0)
    assert pacer.retry_wait() == 0.0 and not pacer.paused()


async def test_retry_wait_counts_down_and_never_goes_below_zero(clock: list[float]) -> None:
    pacer = Pacer(0.0)
    pacer.record(False, 30.0)
    await ark_mod.asyncio.sleep(10.0)  # the fixture's sleep moves the clock
    assert pacer.retry_wait() == 20.0
    await ark_mod.asyncio.sleep(25.0)
    assert pacer.retry_wait() == 0.0 and not pacer.paused()


@pytest.mark.parametrize(
    ("header", "paused_for"),
    [
        ("45", 45.0),
        (None, ark_mod.DEFAULT_RETRY_AFTER),
        ("later", ark_mod.DEFAULT_RETRY_AFTER),
        ("100000", Pacer.MAX_RETRY_AFTER),
    ],
)
async def test_a_429_pauses_for_what_retry_after_says(clock: list[float], header, paused_for) -> None:
    ark = FakeArchidekt()
    ark.inject = [(429, header)]
    client = _client(ark)
    with pytest.raises(ArchidektError):
        await client.search_decks(name="Sample")
    assert client.pacer.retry_wait() == paused_for


async def test_a_429_with_an_http_date_pauses_until_that_date(clock: list[float]) -> None:
    """Bug (found in this release's checks): an HTTP-date Retry-After, which RFC 9110 allows, was
    read as garbage and paused for 30 s whatever the date said."""
    from email.utils import formatdate

    ark = FakeArchidekt()
    ark.inject = [(429, formatdate(time.time() + 90, usegmt=True))]
    client = _client(ark)
    with pytest.raises(ArchidektError):
        await client.search_decks(name="Sample")
    assert 88.0 <= client.pacer.retry_wait() <= 90.0


async def test_an_apply_shows_the_wait_while_it_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    during: list[dict] = []
    note: dict = {"sent_entries": 3}

    async def fake_sleep(seconds: float) -> None:
        during.append(dict(note))
        now[0] += seconds

    monkeypatch.setattr(ark_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ark_mod.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ark_mod.random, "uniform", lambda lo, hi: 0.4)
    ark = FakeArchidekt()
    ark.inject = [(429, "12")]
    client = _client(ark)
    token, noted = ark_mod.patient.set(ark_mod.Patience()), ark_mod.wait_note.set(note)
    try:
        await client.search_decks(name="Sample")
    finally:
        ark_mod.patient.reset(token)
        ark_mod.wait_note.reset(noted)
    assert during == [{"sent_entries": 3, "waiting_for_archidekt": 13}]  # 12 s + jitter, rounded up
    assert note == {"sent_entries": 3}  # the apply's own progress is left as it was


async def test_an_apply_waits_out_a_pause_that_was_already_running(clock: list[float]) -> None:
    """A call refused by the pacer (nothing sent) during another request's Retry-After pause is
    waited out like a 429 and then sent once."""
    ark = FakeArchidekt()
    client = _client(ark)
    client.pacer.record(False, 20.0)
    token = ark_mod.patient.set(ark_mod.Patience())
    try:
        await client.search_decks(name="Sample")
    finally:
        ark_mod.patient.reset(token)
    assert clock == [20.0] and _reads(ark, "/api/decks/v3/") == 1
    assert client.stats["rate_limited"] == 1 and client.stats["rate_limit_waits"] == 1


async def test_outside_an_apply_a_running_pause_refuses_at_once(clock: list[float]) -> None:
    ark = FakeArchidekt()
    client = _client(ark)
    client.pacer.record(False, 20.0)
    ark_mod.wait_note.set({"x": 1})  # a note alone does not make a request wait
    with pytest.raises(ArchidektError) as err:
        await client.search_decks(name="Sample")
    assert err.value.kind == "rate_limited" and "try again in 21 s" in str(err.value)
    assert clock == [] and ark.calls == []


async def test_an_apply_s_waits_never_add_up_past_the_total(monkeypatch: pytest.MonkeyPatch) -> None:
    """Bug (found in this release's checks): the jitter was added after the total was capped, so
    the waits for one request could run up to a second past RATE_LIMIT_WAIT_TOTAL each time."""
    now = [1000.0]
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(ark_mod.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(ark_mod.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(ark_mod.random, "uniform", lambda lo, hi: hi)  # the largest jitter
    ark = FakeArchidekt()
    ark.inject = [(429, "149")] * 3
    client = _client(ark)
    token = ark_mod.patient.set(ark_mod.Patience())
    try:
        with pytest.raises(ArchidektError) as err:
            await client.search_decks(name="Sample")
    finally:
        ark_mod.patient.reset(token)
    assert err.value.kind == "rate_limited"
    # 149 s + 1 s of jitter, then the second 149 s fits what is left only without its jitter
    assert waits == [150.0, 150.0] and sum(waits) == ark_mod.RATE_LIMIT_WAIT_TOTAL, waits
    # nothing is left for the third 429's 149 s, so the apply stops there (Q66)
    assert _reads(ark, "/api/decks/v3/") == 3


async def test_an_apply_no_longer_meets_the_breaker_after_five_429s_in_a_row(clock: list[float]) -> None:
    """Until Q62 each 429 of an apply also counted towards the breaker, so the fifth opened it (for
    every member, a minute) and the apply stopped as "unavailable" after five waits. Now the
    apply's 429s leave the breaker's count alone and the apply ends on its own allowance."""
    ark = FakeArchidekt()
    ark.inject = [(429, "1")] * 10
    client = _client(ark)
    token = ark_mod.patient.set(ark_mod.Patience())
    try:
        with pytest.raises(ArchidektError) as err:
            await client.search_decks(name="Sample")
    finally:
        ark_mod.patient.reset(token)
    assert err.value.kind == "rate_limited" and str(err.value) == ark_mod.PATIENCE_SPENT
    assert _reads(ark, "/api/decks/v3/") == 7 and len(clock) == 6
    assert client.pacer._failures == 0 and client.pacer._open_until == 0.0


# -- the read cache --------------------------------------------------------------------------------
async def test_a_repeat_search_is_a_cache_hit_with_no_upstream_call() -> None:
    ark = FakeArchidekt()
    client = _client(ark, cache_seconds=60)
    first = await client.search_decks(name="Sample")
    second = await client.search_decks(name="Sample")
    assert first == second and _reads(ark, "/api/decks/v3/") == 1
    assert client.stats == {**client.stats, "requests": 1, "cache_hits": 1}
    await client.search_decks(name="Other")  # different query: its own request
    assert _reads(ark, "/api/decks/v3/") == 2


async def test_a_fresh_deck_read_skips_the_shared_cache_and_refreshes_it() -> None:
    """Bug 11: the deck page's Reload asks Archidekt again for a deck read without the member's
    own link, instead of being served the shared cache's copy for up to a minute."""
    ark = FakeArchidekt()
    client = _client(ark, cache_seconds=60)
    await client.get_deck(None, "42")
    await client.get_deck(None, "42")
    assert _reads(ark, "/api/decks/42/") == 1
    ark.decks[42]["name"] = "Renamed on Archidekt"
    assert (await client.get_deck(None, "42", fresh=True)).name == "Renamed on Archidekt"
    assert _reads(ark, "/api/decks/42/") == 2
    # the fresh answer is what the cache serves from then on
    assert (await client.get_deck(None, "42")).name == "Renamed on Archidekt"
    assert _reads(ark, "/api/decks/42/") == 2


async def test_cached_values_are_copies() -> None:
    ark = FakeArchidekt()
    client = _client(ark, cache_seconds=60)
    first = await client.search_decks(name="Sample")
    first["decks"].clear()
    assert (await client.search_decks(name="Sample"))["decks"]


async def test_cache_entries_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    ark = FakeArchidekt()
    client = _client(ark, cache_seconds=60)
    now = [1000.0]
    monkeypatch.setattr(ark_mod.time, "monotonic", lambda: now[0])
    await client.get_deck(None, "42")
    now[0] += 59
    await client.get_deck(None, "42")
    assert _reads(ark, "/api/decks/42/") == 1
    now[0] += 2
    await client.get_deck(None, "42")
    assert _reads(ark, "/api/decks/42/") == 2


async def test_any_write_clears_deck_and_search_reads_but_not_cards() -> None:
    ark = FakeArchidekt()
    client = _client(ark, cache_seconds=60, card_cache_seconds=3600)
    token = (await client.login("alice", "pw-alice"))["access"]
    await client.get_deck(None, "42")
    await client.resolve_card(token, "Sol Ring")
    cards_before = _reads(ark, "/api/cards/v2/")
    await client.update_deck(token, "42", {"name": "Renamed"})
    renamed = await client.get_deck(None, "42")
    assert renamed.name == "Renamed" and _reads(ark, "/api/decks/42/") == 2
    await client.resolve_card(token, "Sol Ring")
    assert _reads(ark, "/api/cards/v2/") == cards_before  # the card catalogue is not ours to change


async def test_card_lookups_are_cached_per_member_never_shared() -> None:
    """A card-catalogue answer fetched with one member's session is reused for that member's
    repeat lookups, but never served to another member."""
    ark = FakeArchidekt()
    client = _client(ark, card_cache_seconds=3600)
    alice = (await client.login("alice", "pw-alice"))["access"]
    amy = (await client.login("amy", "pw-amy"))["access"]
    await client.resolve_card(alice, "Sol Ring")
    n = _reads(ark, "/api/cards/v2/")
    await client.resolve_card(alice, "Sol Ring")
    assert _reads(ark, "/api/cards/v2/") == n and client.stats["cache_hits"] >= 1  # her own repeat
    hits = client.stats["cache_hits"]
    await client.resolve_card(amy, "Sol Ring")
    assert _reads(ark, "/api/cards/v2/") > n and client.stats["cache_hits"] == hits  # fetched afresh
    assert not any(alice in k or amy in k for k in client._cache)  # no raw token in a key


async def test_signed_in_reads_are_never_cached() -> None:
    ark = FakeArchidekt()
    client = _client(ark, cache_seconds=60)
    token = (await client.login("alice", "pw-alice"))["access"]
    await client.get_deck(token, "42")
    await client.get_deck(token, "42")
    await client.list_decks(token, "alice")
    await client.list_decks(token, "alice")
    assert _reads(ark, "/api/decks/42/") == 2 and _reads(ark, "/api/decks/v3/") == 2
    assert client.stats["cache_hits"] == 0


async def test_failures_are_not_cached(sleeps: list[float]) -> None:
    ark = FakeArchidekt()
    ark.inject = [(503, None)]
    client = _client(ark, cache_seconds=60)
    with pytest.raises(ArchidektError):
        await client.get_deck(None, "42")
    assert (await client.get_deck(None, "42")).id == "42"


async def test_cache_off_by_zero() -> None:
    ark = FakeArchidekt()
    client = _client(ark, cache_seconds=0, card_cache_seconds=0)
    await client.search_decks(name="Sample")
    await client.search_decks(name="Sample")
    assert _reads(ark, "/api/decks/v3/") == 2


# -- honest origin ---------------------------------------------------------------------------------
async def test_requests_carry_the_gateways_own_user_agent() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={"results": [], "count": 0})

    ua = Settings.__dataclass_fields__["archidekt_user_agent"].default
    client = ArchidektClient(
        "https://archidekt.com/api",
        ua,
        Pacer(0.0),
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await client.search_decks(name="x")
    assert seen == [ua] and "github.com/TheUncleBen/MTG-Assistant-Gateway" in ua
    assert "Mozilla" not in ua  # never dressed up as a browser


# -- settings, stack files, admin page ----------------------------------------------------------------
GENTLE = {
    "MTG_ARCHIDEKT_MIN_INTERVAL": ("archidekt_min_interval", 1.0),
    "MTG_ARCHIDEKT_MAX_PER_MINUTE": ("archidekt_max_per_minute", 40),
    "MTG_ARCHIDEKT_RETRIES": ("archidekt_retries", 2),
    "MTG_ARCHIDEKT_BACKOFF_BASE": ("archidekt_backoff_base", 1.0),
    "MTG_ARCHIDEKT_CACHE_SECONDS": ("archidekt_cache_seconds", 60),
    "MTG_ARCHIDEKT_CARD_CACHE_SECONDS": ("archidekt_card_cache_seconds", 3600),
}


def test_settings_defaults_and_bounds(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in GENTLE:
        monkeypatch.delenv(name, raising=False)
    _write_secrets(tmp_path, monkeypatch)
    s = load_settings()
    for _env, (attr, default) in GENTLE.items():
        assert getattr(s, attr) == default, attr
    monkeypatch.setenv("MTG_ARCHIDEKT_MAX_PER_MINUTE", "500")
    with pytest.raises(ConfigError):
        load_settings()
    monkeypatch.setenv("MTG_ARCHIDEKT_MAX_PER_MINUTE", "20")
    monkeypatch.setenv("MTG_ARCHIDEKT_MIN_INTERVAL", "0.1")
    with pytest.raises(ConfigError):
        load_settings()


def test_a_dev_null_copy_folder_means_no_copies(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_BACKUP_DIR", str(tmp_path / "b"))
    monkeypatch.setenv("MTG_BACKUP_COPY_DIR", "/dev/null")
    s = load_settings()
    assert s.backup_copy_dir is None
    monkeypatch.setenv("MTG_BACKUP_COPY_DIR", str(tmp_path / "copies"))
    assert load_settings().backup_copy_dir == tmp_path / "copies"


@pytest.mark.parametrize("path", ["deploy/portainer-stack.yml", "deploy/compose/docker-compose.yml"])
def test_stack_files_pass_every_limit_and_the_optional_copy_mount(path: str) -> None:
    text = (ROOT / path).read_text()
    for name in GENTLE:  # empty unless the env sets it: the Settings page manages them (0.7.29)
        assert f"{name}: ${{{name}:-}}\n" in text, name
    assert "MTG_BACKUP_COPY_DIR: /backup-copy" in text
    assert "source: ${MTG_BACKUP_COPY_DIR:-/dev/null}\n        target: /backup-copy" in text


@pytest.mark.parametrize("path", ["deploy/stack.env.example", "deploy/compose/.env.example"])
def test_example_env_files_list_the_limits(path: str) -> None:
    text = (ROOT / path).read_text()
    for name, (_attr, default) in GENTLE.items():
        assert f"\n# {name}={default}\n" in text, name
    assert "#MTG_BACKUP_COPY_DIR=" in text


def test_deploy_doc_documents_every_limit() -> None:
    text = (ROOT / "docs/DEPLOY.md").read_text()
    for name, (_attr, default) in GENTLE.items():
        assert f"| `{name}` | no, *stack* | `{default}` |" in text, name


def test_entrypoint_chowns_only_a_real_copy_folder() -> None:
    text = (ROOT / "docker/entrypoint.sh").read_text()
    assert '"${MTG_BACKUP_COPY_DIR:-}"' in text and '[ -d "$d" ] && chown' in text


async def test_a_403_says_archidekt_refused_and_never_asks_to_relink() -> None:
    """T-151: a 403 is Archidekt refusing one request, with its own reason; only a 401 means the
    stored session is bad."""
    ark = FakeArchidekt()
    client = _client(ark)
    ark.inject = [(403, None)]
    with pytest.raises(ArchidektError) as err:
        await client.search_decks(name="Sample")
    assert err.value.kind == "forbidden"
    assert str(err.value) == "Archidekt refused this request: injected"
    assert "relink" not in str(err.value)
    ark.inject = [(401, None)]
    with pytest.raises(ArchidektError) as auth:
        await client.search_decks(name="Sample")
    assert auth.value.kind == "auth" and "relink" in str(auth.value)


def test_the_review_page_no_longer_blames_another_account_for_a_refusal() -> None:
    from mtg_gateway.pages import ERR_MESSAGES

    assert "another Archidekt account" not in ERR_MESSAGES["forbidden"]
    assert "no need to relink" in ERR_MESSAGES["forbidden"]
