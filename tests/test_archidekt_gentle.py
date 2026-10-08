"""Gentle on Archidekt: the shared per-minute cap, bounded retries with jittered backoff for reads
only, 429s honoured and never retried, the read cache (a hit sends nothing upstream; any write
clears deck and search reads; signed-in reads are never cached), the honest User-Agent, and the
settings, stack files and admin page that carry the limits. The answers come from the recording-
based FakeArchidekt, with failures injected in front of it."""

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
    for name, (_attr, default) in GENTLE.items():
        assert f"{name}: ${{{name}:-{default}}}" in text, name
    assert "MTG_BACKUP_COPY_DIR: /backup-copy" in text
    assert "source: ${MTG_BACKUP_COPY_DIR:-/dev/null}\n        target: /backup-copy" in text


@pytest.mark.parametrize("path", ["deploy/stack.env.example", "deploy/compose/.env.example"])
def test_example_env_files_list_the_limits(path: str) -> None:
    text = (ROOT / path).read_text()
    for name, (_attr, default) in GENTLE.items():
        assert f"\n{name}={default}\n" in text, name
    assert "#MTG_BACKUP_COPY_DIR=" in text


def test_deploy_doc_documents_every_limit() -> None:
    text = (ROOT / "docs/DEPLOY.md").read_text()
    for name, (_attr, default) in GENTLE.items():
        assert f"| `{name}` | no, *stack* | `{default}` |" in text, name


def test_entrypoint_chowns_only_a_real_copy_folder() -> None:
    text = (ROOT / "docker/entrypoint.sh").read_text()
    assert '"${MTG_BACKUP_COPY_DIR:-}"' in text and '[ -d "$d" ] && chown' in text
