"""The local copy of every printing, from Scryfall's bulk data (scan/bulk.py; 10g of the 0.7.46
audit): built from the bulk file, used when Scryfall can't answer a printings request, said to be
as old as it is, kept when a download goes wrong, downloaded at most once a day, and built off the
event loop. The bulk index and file here are fakes built in the test: nothing is downloaded."""

from __future__ import annotations

import gzip
import json
import time
import uuid
from pathlib import Path
from typing import Any

import httpx
import pytest

from mtg_gateway.db import Database
from mtg_gateway.scan import bulk
from mtg_gateway.scan.bulk import COPY_FILE, PrintingsCopy
from mtg_gateway.scan.scryfall import ScryfallClient
from mtg_gateway.scan.service import ScanError, ScanService

from .conftest import Stack, StackOptions, stack_with
from .fake_scryfall import FakeScryfall
from .test_db_lock_wait import _Ticker
from .test_decks_and_proxy import Browser, call, mcp_token

stack_options = stack_with(StackOptions(settings={}, archidekt=None, scryfall=True))

AESI = "6511f317-bd38-46d0-b800-7125a3f420da"
DAY_A = "2026-11-03T22:06:49.499+00:00"
DAY_B = "2026-11-04T10:06:49.499+00:00"
FILE = "/default-cards/default-cards-20261103220649.jsonl.gz"


def _gz(cards: list[dict[str, Any]]) -> bytes:
    return gzip.compress("".join(json.dumps(c) + "\n" for c in cards).encode())


def _many(sf: FakeScryfall, n: int) -> list[dict[str, Any]]:
    """``n`` printings: the recorded cards again and again, each with an id of its own."""
    out = []
    while len(out) < n:
        for c in sf.cards:
            out.append({**c, "id": str(uuid.uuid4())})
    return out[:n]


def _offer(sf: FakeScryfall, body: bytes, updated_at: str = DAY_A, path: str = FILE) -> None:
    """The fake's bulk index offers ``body`` as default_cards, made at ``updated_at``."""
    sf.bulk_files = {path: body}
    sf.bulk_index = [
        {"object": "bulk_data", "type": "oracle_cards", "updated_at": updated_at, "jsonl_download_uri": None},
        {
            "object": "bulk_data",
            "type": "default_cards",
            "updated_at": updated_at,
            "jsonl_download_uri": f"https://data.scryfall.io{path}",
            "compressed_size": len(body),
        },
    ]


def _service(sf: FakeScryfall, folder: Path) -> ScanService:
    service = ScanService(
        Database(":memory:"), ScryfallClient("https://scryfall.test", min_interval=0.0, http=sf.client())
    )
    service.copy = PrintingsCopy(folder, lambda: service.scryfall)
    service.copy.min_printings = 1
    return service


def _by_id(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(r.get("scryfall_id") or r.get("id")): r for r in rows}


def _oracles(sf: FakeScryfall) -> set[str]:
    return {o for c in sf.cards if (o := bulk._oracle(c))}


async def test_the_copy_builds_from_scryfalls_bulk_file(tmp_path: Path) -> None:
    sf = FakeScryfall()
    _offer(sf, _gz(sf.cards))
    service = _service(sf, tmp_path)
    copy = service.copy
    assert copy is not None and copy.as_of is None and copy.due()
    assert await copy.refresh() is True
    # the index, then the file it named: nothing else, and no dated name written in the code
    assert [u.split("?")[0] for u in sf.bulk_requests] == [
        "https://scryfall.test/bulk-data",
        f"https://data.scryfall.io{FILE}",
    ]
    assert copy.as_of == DAY_A
    assert (tmp_path / COPY_FILE).is_file()
    assert sorted(p.name for p in tmp_path.iterdir()) == [COPY_FILE]  # no download or half-built file left
    # every printing answer matches Scryfall's own, field for field
    client = service.scryfall
    for oid in _oracles(sf):
        live = await client.prints(oid)
        kept = await copy.prints(oid)
        assert kept is not None and kept["total_cards"] == live["total_cards"], oid
        assert _by_id(kept["cards"]) == _by_id(live["cards"]), oid
        assert kept["has_more"] is live["has_more"] is False
        oldest = (await copy.prints(oid, order="oldest"))["cards"]
        assert [c["released_at"] for c in oldest] == sorted(c["released_at"] for c in live["cards"]), oid
        cheapest = (await copy.prints(oid, order="cheapest"))["cards"]
        usd = [float(c["prices"]["usd"]) for c in cheapest if c["prices"].get("usd")]
        assert usd == sorted(usd), oid  # cheapest first, as Scryfall's order=usd
        assert all(c["prices"].get("usd") for c in cheapest[: len(usd)]), oid  # unpriced ones last
    live_of = await client.printings_of(sorted(_oracles(sf)))
    kept_of = copy.printings_of_now(sorted(_oracles(sf)))
    assert set(kept_of) == set(live_of)
    for oid, rows in live_of.items():
        assert _by_id(kept_of[oid]) == _by_id(rows), oid
    # filters, as Scryfall's search narrows them
    foil = (await copy.prints(AESI, finish="foil"))["cards"]
    assert foil and all("foil" in c["finishes"] for c in foil)
    one = (await copy.prints(AESI, set_code="cmr", number="365"))["cards"]
    assert [(c["set"], c["collector_number"]) for c in one] == [("cmr", "365")]
    assert await copy.oracle_of("aesi, tyrant of gyre strait") == (AESI, "Aesi, Tyrant of Gyre Strait")
    assert await copy.prints("00000000-0000-0000-0000-000000000000") is None  # no such card in it


async def test_a_live_failure_serves_printings_from_the_copy_with_its_date(tmp_path: Path) -> None:
    sf = FakeScryfall()
    _offer(sf, _gz(sf.cards))
    service = _service(sf, tmp_path)
    assert await service.copy.refresh()
    live = await service.printings(AESI)
    assert "from_local_copy" not in live and "as_of" not in live  # Scryfall answered

    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "12"}, json={"object": "error"})

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"object": "error"})

    def no_card(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"object": "error", "details": "no printings found"})

    for handler in (down, failing, limited):
        service.scryfall = ScryfallClient(
            "https://scryfall.test",
            min_interval=0.0,
            http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        )
        out = await service.printings(AESI)
        assert out["from_local_copy"] is True, handler
        assert out["as_of"] == DAY_A and out["prices_as_of"] == DAY_A
        assert out["as_of_note"] == "Printings and prices as of 3 Nov (Scryfall can't be reached right now)"
        assert "verif" not in out["as_of_note"].lower()
        assert _by_id(out["cards"]) == _by_id(live["cards"])
        assert [c["scryfall_id"] for c in out["cards"]] == [c["scryfall_id"] for c in live["cards"]]
    # Scryfall saying there is no such card is its answer: the copy does not stand in for it
    service.scryfall = ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(no_card)),
    )
    with pytest.raises(ScanError) as err:
        await service.printings(AESI)
    assert err.value.kind == "not_found"
    # without a copy, the failure is as before
    service.copy = PrintingsCopy(tmp_path / "none", lambda: service.scryfall)
    service.scryfall = ScryfallClient(
        "https://scryfall.test", min_interval=0.0, http=httpx.AsyncClient(transport=httpx.MockTransport(down))
    )
    with pytest.raises(ScanError) as err:
        await service.printings(AESI)
    assert err.value.kind == "unavailable"


async def test_the_tool_and_the_picker_say_the_printings_are_from_the_copy(stack: Stack) -> None:
    """card_printings (by oracle id and by name) and the printing picker's /scan/api/prints, while
    Scryfall can't be reached: answered from the copy, with as_of and the plain line; the picker's
    answer is never kept by the browser."""
    sf = stack.sf
    service = stack.gw.scan
    assert service.copy is not None and service.copy.path.parent == stack.h.settings.data_dir
    _offer(sf, _gz(sf.cards))
    service.copy.min_printings = 1
    assert await service.copy.refresh()

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    service.scryfall = ScryfallClient(
        "https://scryfall.test", min_interval=0.0, http=httpx.AsyncClient(transport=httpx.MockTransport(down))
    )
    token = await mcp_token(stack.h)
    line = "Printings and prices as of 3 Nov (Scryfall can't be reached right now)"
    for args in ({"oracle_id": AESI}, {"name": "Aesi, Tyrant of Gyre Strait"}):
        out = (await call(stack.h, token, "card_printings", args))["structuredContent"]
        assert out["ok"] is True, out
        assert out["from_local_copy"] is True and out["as_of"] == DAY_A, out
        assert out["note"].startswith(line), out
        assert [c["set"] for c in out["cards"]] == ["sld", "dsc", "plst", "cmr"], out
        if "name" in args:  # the card's own name, as the copy knows it (by id: its first printing's)
            assert out["name"] == "Aesi, Tyrant of Gyre Strait", out
    b = Browser(stack.h)
    await b.login("/scan")
    try:
        r = await b.http.get("/scan/api/prints", params={"oracle_id": AESI})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["from_local_copy"] is True and data["as_of"] == DAY_A and data["as_of_note"] == line
        assert r.headers["cache-control"] == "no-store"
    finally:
        await b.aclose()


async def test_a_damaged_or_cut_off_download_keeps_the_old_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = FakeScryfall()
    good = _gz(sf.cards)
    _offer(sf, good)
    service = _service(sf, tmp_path)
    copy = service.copy
    assert await copy.refresh()
    before = await copy.prints(AESI)
    monkeypatch.setattr(bulk, "COPY_TTL", 0)  # due again at once
    damaged = {
        "cut off": good[: len(good) // 2],
        "not gzip": b"<html>not the file</html>",
        "not JSON": gzip.compress(b'{"object": "card", "name": "Sol R\n'),
        "too short": _gz(sf.cards[:1]),
    }
    copy.min_printings = 2
    for why, body in damaged.items():
        _offer(sf, body, updated_at=DAY_B)
        copy._next_try = 0.0
        assert await copy.refresh() is False, why
        assert copy.as_of == DAY_A, why  # the old copy is the one in use
        assert await copy.prints(AESI) == before, why
        assert sorted(p.name for p in tmp_path.iterdir()) == [COPY_FILE], why  # nothing half-built left
    # a failed download is tried again only after RETRY_AFTER_FAILURE, never at once
    _offer(sf, good, updated_at=DAY_B)
    n = len(sf.bulk_requests)
    assert await copy.refresh() is False and len(sf.bulk_requests) == n
    copy._next_try = 0.0
    assert await copy.refresh() is True and copy.as_of == DAY_B


async def test_the_refresh_is_skipped_while_the_copy_is_younger_than_a_day(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sf = FakeScryfall()
    _offer(sf, _gz(sf.cards))
    service = _service(sf, tmp_path)
    assert await service.copy.refresh()
    n = len(sf.bulk_requests)
    _offer(sf, _gz(sf.cards), updated_at=DAY_B)  # Scryfall has a newer file (every ~12 hours)
    assert not service.copy.due()
    assert await service.copy.refresh() is False
    # a restart: the copy on disk says when it was downloaded
    again = PrintingsCopy(tmp_path, lambda: service.scryfall)
    assert not again.due() and await again.refresh() is False
    assert len(sf.bulk_requests) == n, "a copy younger than a day asked Scryfall again"
    assert 0 < again.age() < 60
    # a day later the index is read; a file no newer than the copy is not downloaded again
    monkeypatch.setattr(bulk, "COPY_TTL", 0)
    _offer(sf, _gz(sf.cards), updated_at=DAY_A)
    assert await again.refresh() is False
    assert [u.split("?")[0] for u in sf.bulk_requests[n:]] == ["https://scryfall.test/bulk-data"]


def test_the_copy_is_downloaded_at_most_once_a_day_and_never_in_a_starts_way() -> None:
    assert bulk.COPY_TTL >= 24 * 3600  # Scryfall updates its bulk files about every 12 hours
    assert bulk.RETRY_AFTER_FAILURE >= 3600
    assert bulk.START_DELAY >= 30


async def test_the_event_loop_keeps_turning_while_the_copy_builds(tmp_path: Path) -> None:
    """The download and the build run off the event loop (a worker thread; the file is written a
    megabyte at a time in one too): other requests are answered meanwhile. The ticker pattern of
    test_db_lock_jobs: the longest the loop went without turning while the copy was built."""
    sf = FakeScryfall()
    _offer(sf, _gz(_many(sf, 8000)))
    service = _service(sf, tmp_path)
    with _Ticker() as ticker:
        started = time.monotonic()
        assert await service.copy.refresh()
        took = time.monotonic() - started
        longest = await ticker.done()
    assert took > 0.5, f"the build took {took:.2f} s: too short to say anything about the loop"
    assert longest < 0.25, f"the event loop stalled {longest:.2f} s while the copy was built ({took:.1f} s)"
    assert service.copy.meta()["count"] == "8000"


async def test_a_gateway_under_test_downloads_nothing(stack: Stack) -> None:
    """The copy downloads through the scan service's own Scryfall client, as the card-name
    catalog does; in tests that client answers from the recorded fixtures, which offer no bulk
    data, so nothing is downloaded and no copy is made."""
    copy = stack.gw.scan.copy
    assert copy is not None
    assert await copy.refresh() is False
    assert stack.sf.bulk_requests == ["https://scryfall.test/bulk-data"]  # answered 404 by the fake
    assert not copy.path.exists()
    assert stack.sf.requests == []  # the other tests' request counts are not touched
