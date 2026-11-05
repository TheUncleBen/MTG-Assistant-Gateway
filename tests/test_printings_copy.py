"""The local copy of every printing, from Scryfall's bulk data (scan/bulk.py; 10g of the 0.7.46
audit): built from the bulk file, used when Scryfall can't answer a printings request, said to be
as old as it is, kept when a download goes wrong, downloaded at most once a day, and built off the
event loop. The bulk index and file here are fakes built in the test: nothing is downloaded."""

from __future__ import annotations

import asyncio
import gc
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
AS_OF = "2026-11-03T22:06:49+00:00"  # DAY_A as the answers carry it: ISO 8601, UTC, to the second
AS_OF_B = "2026-11-04T10:06:49+00:00"
DAY_B = "2026-11-04T10:06:49.499+00:00"
LOOP_PRINTINGS = 20000  # the event-loop test's file: about a sixth of Scryfall's
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
    assert copy.as_of == AS_OF
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
        assert out["as_of"] == AS_OF and out["prices_as_of"] == AS_OF
        assert out["as_of_note"] == "As of 3 Nov 2026 \u00b7 Scryfall can't be reached"
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
    line = "As of 3 Nov 2026 \u00b7 Scryfall can't be reached"
    for args in ({"oracle_id": AESI}, {"name": "Aesi, Tyrant of Gyre Strait"}):
        out = (await call(stack.h, token, "card_printings", args))["structuredContent"]
        assert out["ok"] is True, out
        assert out["from_local_copy"] is True and out["as_of"] == AS_OF, out
        # the tools add the prices' day on a line of their own; the line is the route's word for word
        assert out["note"].split("\n")[:2] == [line, "Prices are from that day, not today."], out
        assert out["note"].split("\n")[0] == bulk.as_of_line(out["as_of"]), out
        assert [c["set"] for c in out["cards"]] == ["sld", "dsc", "plst", "cmr"], out
        if "name" in args:  # the card's own name, as the copy knows it (by id: its first printing's)
            assert out["name"] == "Aesi, Tyrant of Gyre Strait", out
    b = Browser(stack.h)
    await b.login("/scan")
    try:
        r = await b.http.get("/scan/api/prints", params={"oracle_id": AESI})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["from_local_copy"] is True and data["as_of"] == AS_OF and data["as_of_note"] == line
        assert data["as_of_note"] == bulk.as_of_line(data["as_of"])
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
        assert copy.as_of == AS_OF, why  # the old copy is the one in use
        assert await copy.prints(AESI) == before, why
        # nothing half-built left: the copy, and the kept wait before the next try (N16)
        assert sorted(p.name for p in tmp_path.iterdir()) == [COPY_FILE, COPY_FILE + ".next"], why
    # a failed download is tried again only after RETRY_AFTER_FAILURE, never at once
    _offer(sf, good, updated_at=DAY_B)
    n = len(sf.bulk_requests)
    assert await copy.refresh() is False and len(sf.bulk_requests) == n
    copy._next_try = 0.0
    assert await copy.refresh() is True and copy.as_of == AS_OF_B


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
    # big enough that even a fast machine takes well over the 0.5 s floor below (8000 printings
    # built in 0.48 s on one once the build stopped setting off full collections)
    _offer(sf, _gz(_many(sf, LOOP_PRINTINGS)))
    service = _service(sf, tmp_path)
    with _Ticker() as ticker:
        started = time.monotonic()
        assert await service.copy.refresh()
        took = time.monotonic() - started
        longest = await ticker.done()
    assert took > 0.5, f"the build took {took:.2f} s: too short to say anything about the loop"
    assert longest < 0.25, f"the event loop stalled {longest:.2f} s while the copy was built ({took:.1f} s)"
    assert service.copy.meta()["count"] == str(LOOP_PRINTINGS)


def test_the_build_leaves_the_garbage_collector_nothing_to_walk(tmp_path: Path) -> None:
    """Why the loop kept turning only most of the time: the rows held a batch at a time outlived
    the young collections, piled into the oldest generation and set off full collections, which
    hold every thread for as long as the gateway's whole memory takes to walk (0.30 s on CI, while
    the build took 1.2 s). Each row is turned into plain values at once now, so the oldest
    generation hardly grows while the copy is built, whatever the size of the file."""
    sf = FakeScryfall()
    gz = tmp_path / "default-cards.jsonl.gz"
    gz.write_bytes(_gz(_many(sf, 8000)))
    gc.collect()
    base = len(gc.get_objects(2))
    grew = [0]

    def watch(phase: str, info: dict[str, Any]) -> None:
        if phase == "stop":
            grew[0] = max(grew[0], len(gc.get_objects(2)) - base)

    gc.callbacks.append(watch)
    try:
        count = bulk.build(gz, tmp_path / COPY_FILE, {"updated_at": DAY_A}, min_printings=1)
    finally:
        gc.callbacks.remove(watch)
    assert count == 8000
    assert grew[0] < 1000, f"the oldest generation grew by {grew[0]} objects while the copy was built"


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


# -- round 2 of the 0.7.46 audit: the Settings row, as_of, the kept wait, a damaged copy -------------


def _switchable(sf: FakeScryfall, folder: Path) -> tuple[ScanService, dict[str, bool]]:
    """A service whose copy follows ``state["on"]``, as it follows the Settings row."""
    state = {"on": True}
    service = _service(sf, folder)
    service.copy = PrintingsCopy(folder, lambda: service.scryfall, lambda: state["on"])
    service.copy.min_printings = 1
    return service, state


def _down() -> httpx.AsyncClient:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    return httpx.AsyncClient(transport=httpx.MockTransport(down))


async def test_turned_off_the_copy_is_deleted_and_never_downloaded(tmp_path: Path) -> None:
    """The Settings row "Keep a local copy of card printings" off: the copy and every file it left
    (a half-built .new, a download, the next-try file) are deleted at once, the daily download
    stops, and a live failure gives the error it gave before there was a copy."""
    sf = FakeScryfall()
    _offer(sf, _gz(sf.cards))
    service, state = _switchable(sf, tmp_path)
    copy = service.copy
    assert await copy.refresh() and copy.path.exists()
    for leftover in (COPY_FILE + ".new", COPY_FILE + ".download.gz", COPY_FILE + ".next"):
        (tmp_path / leftover).write_bytes(b"left over")
    assert (await copy.prints(AESI)) is not None  # the reader has the file open

    state["on"] = False
    copy.setting_changed("printings_copy")
    assert sorted(p.name for p in tmp_path.iterdir()) == [], "turned off, the copy's files are deleted"
    n = len(sf.bulk_requests)
    assert not copy.due() and await copy.refresh() is False
    assert len(sf.bulk_requests) == n, "turned off, it downloaded"
    # a file appearing while off (a build that ended meanwhile) is deleted at the next check
    (tmp_path / COPY_FILE).write_bytes(b"x")
    assert await copy.refresh() is False and not (tmp_path / COPY_FILE).exists()

    # Scryfall down while off: the old error, not the copy
    service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=_down())
    with pytest.raises(ScanError) as err:
        await service.printings(AESI)
    assert err.value.kind == "unavailable"
    assert await copy.prints(AESI) is None and await copy.oracle_of("Sol Ring") is None

    # on again: the next check downloads
    service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=sf.client())
    state["on"] = True
    copy.setting_changed("printings_copy")
    assert copy.due()
    assert await copy.refresh() is True and copy.as_of == AS_OF
    assert len(sf.bulk_requests) == n + 2  # the index and the file


async def test_the_settings_row_turns_the_copy_off_and_on(stack: Stack) -> None:
    """The row is on the admin Settings page (General), on by default with no stack variable, and
    a change there applies at once (LiveSettings.on_change)."""
    from mtg_gateway import settings_page
    from mtg_gateway.config import MANAGED_SETTINGS

    spec = MANAGED_SETTINGS["printings_copy"]
    assert spec.kind == "bool" and spec.default is True and spec.env == "" and not spec.restart
    assert settings_page.TEXT["printings_copy"] == (
        "Keep a local copy of card printings",
        "Used only when Scryfall can't be reached. About 55 MB, refreshed daily. Turning it off "
        "deletes the copy.",
    )
    live = stack.gw.decks.live
    copy = stack.gw.scan.copy
    assert copy.enabled is True
    _offer(stack.sf, _gz(stack.sf.cards))
    copy.min_printings = 1
    assert await copy.refresh() and copy.path.exists()
    live.set("printings_copy", False, by="admin-1")
    assert copy.enabled is False and not copy.path.exists()
    live.set("printings_copy", True, by="admin-1")
    assert copy.enabled is True and copy.due()
    assert await copy.refresh() and copy.path.exists()


async def test_as_of_is_an_iso_date_time_with_one_wording(tmp_path: Path) -> None:
    """N14: ``as_of`` is an ISO 8601 date-time (the page writes it in the viewer's own way), and
    the tools' ``as_of_note`` says the same words for a 429, a 5xx and no answer at all."""
    from datetime import datetime

    sf = FakeScryfall()
    _offer(sf, _gz(sf.cards))
    service = _service(sf, tmp_path)
    assert await service.copy.refresh()

    def limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "12"}, json={"object": "error"})

    def failing(request: httpx.Request) -> httpx.Response:
        return httpx.Response(502, json={"object": "error"})

    notes = set()
    for http in (
        _down(),
        httpx.AsyncClient(transport=httpx.MockTransport(limited)),
        httpx.AsyncClient(transport=httpx.MockTransport(failing)),
    ):
        service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=http)
        out = await service.printings(AESI)
        when = datetime.fromisoformat(out["as_of"])
        assert when.tzinfo is not None and when.isoformat() == out["as_of"] == AS_OF
        notes.add(out["as_of_note"])
    assert notes == {"As of 3 Nov 2026 \u00b7 Scryfall can't be reached"}


async def test_a_restart_waits_for_the_next_try(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """N16: the wait after a failure, or after an index with nothing newer, is kept beside the
    copy, so a restart does not download the 80 MB file again before it is due; a file that came
    down damaged waits a day."""
    sf = FakeScryfall()
    sf.bulk_index = None  # the index fails (404)
    service = _service(sf, tmp_path)
    assert await service.copy.refresh() is False
    for _restart in range(3):
        again = PrintingsCopy(tmp_path, lambda: service.scryfall)
        assert not again.due() and await again.refresh() is False
    assert len(sf.bulk_requests) == 1, sf.bulk_requests

    # a file that comes down damaged: no copy at all, yet a restart waits a whole day
    other = tmp_path / "bad"
    sf2 = FakeScryfall()
    _offer(sf2, b"<html>not the file</html>")
    service2 = _service(sf2, other)
    assert await service2.copy.refresh() is False and not service2.copy.path.exists()
    restarted = PrintingsCopy(other, lambda: service2.scryfall)
    assert restarted._next_try - time.time() > bulk.RETRY_AFTER_BAD_FILE - 60
    for _restart in range(3):
        assert await PrintingsCopy(other, lambda: service2.scryfall).refresh() is False
    assert [u for u in sf2.bulk_requests if u.endswith(".gz")] == [f"https://data.scryfall.io{FILE}"]

    # an index with nothing newer than the copy: the wait holds across a restart too
    third = tmp_path / "same"
    sf3 = FakeScryfall()
    _offer(sf3, _gz(sf3.cards))
    service3 = _service(sf3, third)
    assert await service3.copy.refresh()
    assert not (third / (COPY_FILE + ".next")).exists()  # a good copy leaves no wait behind
    monkeypatch.setattr(bulk, "COPY_TTL", 0)  # the copy is a day old
    assert await service3.copy.refresh() is False  # unchanged: one index request
    n = len(sf3.bulk_requests)
    assert await PrintingsCopy(third, lambda: service3.scryfall).refresh() is False
    assert len(sf3.bulk_requests) == n


async def test_a_damaged_copy_is_logged_once_without_a_traceback(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """N17: a copy on disk that can't be read gives the old error, and says so in the log once
    per file, as a WARNING without a traceback, however often the picker is opened."""
    import logging

    sf = FakeScryfall()
    _offer(sf, _gz(sf.cards))
    service = _service(sf, tmp_path)
    assert await service.copy.refresh()
    service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=_down())
    data = service.copy.path.read_bytes()
    service.copy.path.write_bytes(data[:4096] + b"\0" * (len(data) - 4096))  # zero-tailed
    caplog.set_level(logging.INFO)
    for _open in range(4):
        with pytest.raises(ScanError) as err:
            await service.printings(AESI)
        assert err.value.kind == "unavailable"
    said = [r for r in caplog.records if "copy of Scryfall's printings" in r.getMessage()]
    assert len(said) == 1, [r.getMessage() for r in said]
    assert said[0].levelno == logging.WARNING and said[0].exc_info is None
    assert service.copy.due()  # a copy that can't be read is replaced at the next download
    # another damaged file: said once more
    time.sleep(0.01)
    service.copy.path.write_bytes(b"not a database at all")
    for _open in range(3):
        with pytest.raises(ScanError):
            await service.printings(AESI)
    said = [r for r in caplog.records if "copy of Scryfall's printings" in r.getMessage()]
    assert len(said) == 2 and all(r.exc_info is None for r in said), [r.getMessage() for r in said]


# -- round 3 of the 0.7.46 audit: Off during a download, and which failures wait a day -------------


def _streaming(sf: FakeScryfall, body: bytes, chunk: int, on_chunk=None, fail_after: int | None = None):
    """A client for ``sf`` whose bulk file comes in ``chunk``-byte pieces (``on_chunk(n)`` after the
    n-th); ``fail_after``: the connection drops after that many pieces. Returns (client, served)."""
    served = {"chunks": 0}

    async def pieces():
        for n, i in enumerate(range(0, len(body), chunk), 1):
            if fail_after is not None and n > fail_after:
                raise httpx.ReadError("connection reset")
            served["chunks"] = n
            yield body[i : i + chunk]
            if on_chunk is not None:
                on_chunk(n)
            await asyncio.sleep(0)

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == FILE:
            sf.bulk_requests.append(str(request.url))
            return httpx.Response(
                200,
                content=pieces(),
                headers={"content-type": "application/gzip", "content-length": str(len(body))},
            )
        return sf.handle(request)

    return httpx.AsyncClient(transport=httpx.MockTransport(handle)), served


async def test_turning_it_off_during_a_download_stops_the_transfer_at_once(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """P1: Off while the 80 MB file comes down stops reading it within a piece or two, says nothing
    is damaged, leaves no file and no wait; On again downloads at the next check."""
    import logging

    sf = FakeScryfall()
    body = _gz(_many(sf, 3000))
    _offer(sf, body)
    service, state = _switchable(sf, tmp_path)
    copy = service.copy
    pieces = len(body) // 2048 + 1

    def off_at(n: int) -> None:
        if n == 3:
            state["on"] = False
            copy.setting_changed("printings_copy")

    client, served = _streaming(sf, body, 2048, on_chunk=off_at)
    service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=client)
    caplog.set_level(logging.INFO)
    assert pieces > 20
    assert await copy.refresh() is False
    assert served["chunks"] <= 5, f"{served['chunks']} of {pieces} pieces read after Off at the 3rd"
    assert sorted(p.name for p in tmp_path.iterdir()) == [], "Off left files behind"
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING and "bulk" in r.name]
    assert warned == [], [r.getMessage() for r in warned]

    # Off then On during a download: the next check downloads, with no wait left from it
    state["on"] = True
    copy.setting_changed("printings_copy")

    def off_and_on_at(n: int) -> None:
        if n == 3:
            state["on"] = False
            copy.setting_changed("printings_copy")
            state["on"] = True
            copy.setting_changed("printings_copy")

    client, served = _streaming(sf, body, 2048, on_chunk=off_and_on_at)
    service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=client)
    assert await copy.refresh() is False and served["chunks"] <= 5
    assert copy.due(), "Off then On left a wait before the next download"
    assert not copy.next_path.exists()
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING and "bulk" in r.name]
    assert warned == [], [r.getMessage() for r in warned]
    client, served = _streaming(sf, body, 1 << 16)
    service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=client)
    assert await copy.refresh() is True and copy.as_of == AS_OF


async def test_only_a_file_received_whole_and_found_damaged_waits_a_day(tmp_path: Path) -> None:
    """P7: a 503 on the file, or a connection that drops part way, waits RETRY_AFTER_FAILURE (6 h)
    like any HTTP or transport failure; only a file received whole and then found damaged waits
    RETRY_AFTER_BAD_FILE (a day)."""

    def waits(folder: Path) -> float:
        return PrintingsCopy(folder, lambda: None)._next_try - time.time()  # as a restart reads it

    sf = FakeScryfall()
    body = _gz(sf.cards)
    _offer(sf, body)

    def answer_503(request: httpx.Request) -> httpx.Response:
        if request.url.path == FILE:
            return httpx.Response(503, json={"object": "error"})
        return sf.handle(request)

    cases: dict[str, tuple[httpx.AsyncClient, float]] = {
        "503 on the file": (
            httpx.AsyncClient(transport=httpx.MockTransport(answer_503)),
            bulk.RETRY_AFTER_FAILURE,
        ),
        "connection reset part way": (_streaming(sf, body, 512, fail_after=2)[0], bulk.RETRY_AFTER_FAILURE),
    }
    damaged = FakeScryfall()
    _offer(damaged, b"<html>not the file</html>")
    cases["received whole, not a gzip file"] = (damaged.client(), bulk.RETRY_AFTER_BAD_FILE)
    for why, (client, wait) in cases.items():
        folder = tmp_path / why.replace(" ", "-")
        service = _service(sf, folder)
        service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=client)
        assert await service.copy.refresh() is False, why
        assert wait - 60 < waits(folder) <= wait, (why, waits(folder) / 3600)
        assert sorted(p.name for p in folder.iterdir()) == [COPY_FILE + ".next"], why
    assert bulk.RETRY_AFTER_FAILURE == 6 * 3600 and bulk.RETRY_AFTER_BAD_FILE == 24 * 3600


def test_one_wording_for_the_copys_day_on_the_tools_and_the_pages() -> None:
    """A2: one function says how old the copy is, for the tools and the pages alike: the day in
    English whatever the locale, and with no readable day the copy itself; never "and prices"
    (the pages show no prices from the copy). The tools' separate prices line says the same day."""
    assert bulk.as_of_line(AS_OF) == "As of 3 Nov 2026 \u00b7 Scryfall can't be reached"
    late = "2026-01-21T23:30:00-05:00"  # the day in UTC, as Scryfall dates its file
    assert bulk.as_of_line(late) == "As of 22 Jan 2026 \u00b7 Scryfall can't be reached"
    for unread in (None, "", "soon"):
        assert bulk.as_of_line(unread) == "From the gateway's own copy \u00b7 Scryfall can't be reached"
        assert bulk.prices_line(unread) == "Prices are from that copy, not today."
    assert bulk.prices_line(AS_OF) == "Prices are from that day, not today."
    for line in (bulk.as_of_line(AS_OF), bulk.as_of_line(None)):
        assert "price" not in line.lower()


async def test_a_stopped_download_says_why_it_stopped(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A3: a download stopped by Off says the copy was turned off; one stopped by the gateway
    shutting down says that instead, never that the copy was turned off."""
    import logging

    sf = FakeScryfall()
    body = _gz(_many(sf, 3000))
    _offer(sf, body)
    caplog.set_level(logging.INFO, logger="mtg_gateway.scan.bulk")

    service, state = _switchable(sf, tmp_path / "off")
    copy = service.copy

    def off_at(n: int) -> None:
        if n == 3:
            state["on"] = False
            copy.setting_changed("printings_copy")

    client, _ = _streaming(sf, body, 2048, on_chunk=off_at)
    service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=client)
    assert await copy.refresh() is False
    said = [r.getMessage() for r in caplog.records if "stopped" in r.getMessage()]
    assert said == ["download of Scryfall's printings stopped: the local copy was turned off"], said

    caplog.clear()
    service, _ = _switchable(sf, tmp_path / "shutdown")
    closing = service.copy

    def shut_at(n: int) -> None:
        if n == 3:
            closing.close()

    client, served = _streaming(sf, body, 2048, on_chunk=shut_at)
    service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=client)
    assert await closing.refresh() is False and served["chunks"] <= 5
    said = [r.getMessage() for r in caplog.records if "stopped" in r.getMessage()]
    assert said == ["download of Scryfall's printings stopped: the gateway is shutting down"], said
    warned = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warned == [], [r.getMessage() for r in warned]
