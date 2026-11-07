"""The Android app's pull to refresh (PullRefresh.kt): the app loads the page again with its own
header (decks.PULL_HEADER), so the gateway knows the member pulled rather than guessing from the
WebView's reload headers. A pull on a deck page always reads the deck from Archidekt again, past
the deck page's 20-second view copy and the 30-second Reload rule, through the shared pacer and
the member's Archidekt slot; a pull within PAGE_DECK_PULL_FLOOR of the last read shows that read.
Binder › Lists, each list and a profile read Archidekt again on a pull too. A plain view keeps
every kept read."""

from __future__ import annotations

import contextlib

import pytest

from mtg_gateway import account_lists, profile_page
from mtg_gateway import decks as decks_mod

from .conftest import Stack, stack_with
from .test_companion import COMPANION
from .test_navigation_speed import NAV, _deck_reads, _linked
from .test_page_reads import Watch, _member, _view

stack_options = stack_with(COMPANION)

PULL = {**NAV, "X-MTG-Pull": "1"}
RELOAD = {**NAV, "Cache-Control": "max-age=0"}


@pytest.fixture(autouse=True)
def _forget_module_memory():
    profile_page._LIST.clear()
    account_lists._COUNTS.clear()
    account_lists._COUNT_FAILED.clear()
    yield
    profile_page._LIST.clear()
    account_lists._COUNTS.clear()
    account_lists._COUNT_FAILED.clear()


def _age(stack: Stack, seconds: float) -> None:
    decks = stack.h.app.state.gateway.decks
    decks._page_decks = {k: (t - seconds, d) for k, (t, d) in decks._page_decks.items()}


def test_the_pull_header_is_a_pull_and_a_reload() -> None:
    assert decks_mod.is_pull({"x-mtg-pull": "1"}) and decks_mod.is_reload({"x-mtg-pull": "1"})
    assert not decks_mod.is_pull({"x-mtg-pull": "0"}) and not decks_mod.is_pull({})
    assert not decks_mod.is_pull({"cache-control": "max-age=0"})  # a Reload is not a pull


async def test_a_pull_on_a_deck_page_reads_the_deck_now(stack: Stack) -> None:
    """Past both the 20 s view copy and the 30 s Reload rule: the member sees Archidekt's deck."""
    b = await _linked(stack)
    try:
        assert (await b.http.get("/decks/42", headers=NAV)).status_code == 200
        stack.ark.decks[42]["name"] = "Renamed on the site"
        _age(stack, decks_mod.PAGE_DECK_PULL_FLOOR + 1)  # well inside the 20 s and the 30 s
        reads = _deck_reads(stack)
        plain = await b.http.get("/decks/42", headers=NAV)
        assert "Renamed on the site" not in plain.text and _deck_reads(stack) == reads
        reload = await b.http.get("/decks/42", headers=RELOAD)  # the browser's Reload keeps its rule
        assert "Renamed on the site" not in reload.text and _deck_reads(stack) == reads
        pulled = await b.http.get("/decks/42", headers=PULL)
        assert pulled.status_code == 200
        assert "Renamed on the site" in pulled.text and _deck_reads(stack) == reads + 1
        assert "data-check" not in pulled.text  # read now, not shown stale while it is read
        # what the pull read is the copy the next plain view shows, without asking again
        again = await b.http.get("/decks/42", headers=NAV)
        assert "Renamed on the site" in again.text and _deck_reads(stack) == reads + 1
    finally:
        await b.aclose()


async def test_a_pull_right_after_a_read_reuses_it(stack: Stack) -> None:
    """A second pull (or one fired twice) within PAGE_DECK_PULL_FLOOR queues nothing in the pacer."""
    b = await _linked(stack)
    try:
        assert (await b.http.get("/decks/42", headers=PULL)).status_code == 200
        reads = _deck_reads(stack)
        for _ in range(3):
            assert (await b.http.get("/decks/42", headers=PULL)).status_code == 200
        assert _deck_reads(stack) == reads
        _age(stack, decks_mod.PAGE_DECK_PULL_FLOOR + 0.5)
        assert (await b.http.get("/decks/42", headers=PULL)).status_code == 200
        assert _deck_reads(stack) == reads + 1
    finally:
        await b.aclose()


async def test_a_pull_on_a_deck_page_goes_through_the_pacer_and_the_members_slot(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    b = await _linked(stack)
    try:
        assert (await b.http.get("/decks/42", headers=NAV)).status_code == 200
        _age(stack, decks_mod.PAGE_DECK_PULL_FLOOR + 1)
        decks = stack.h.app.state.gateway.decks
        pacer = decks.client.pacer
        turns: list[bool] = []
        slots: list[str] = []
        enter, slot = pacer.enter, decks.archidekt_slot

        async def counted(*a, **k):  # noqa: ANN002, ANN003, ANN202
            turns.append(True)
            return await enter(*a, **k)

        @contextlib.asynccontextmanager
        async def counted_slot(sub, **k):  # noqa: ANN001, ANN003, ANN202
            slots.append(sub)
            async with slot(sub, **k):
                yield

        monkeypatch.setattr(pacer, "enter", counted)
        monkeypatch.setattr(decks, "archidekt_slot", counted_slot)
        reads = _deck_reads(stack)
        assert (await b.http.get("/decks/42", headers=PULL)).status_code == 200
        assert _deck_reads(stack) == reads + 1
        assert turns, "the pull's read took its turn in the pacer"
        assert slots and all(slots), "the pull's read counted against the member's Archidekt slot"
    finally:
        await b.aclose()


async def test_a_pull_while_archidekt_cannot_be_asked_shows_the_last_read(stack: Stack) -> None:
    """A refused pull is no worse than a plain view: the last read, read-only and dated."""
    b = await _linked(stack)
    try:
        assert (await b.http.get("/decks/42", headers=NAV)).status_code == 200
        _age(stack, decks_mod.PAGE_DECK_PULL_FLOOR + 1)
        stack.ark.inject.extend([(503, None)] * 6)
        r = await b.http.get("/decks/42", headers=PULL)
        assert r.status_code == 200 and "Sample Commander Deck" in r.text
    finally:
        stack.ark.inject.clear()
        await b.aclose()


PULLED_VIEWS = ("/lists/templates", "/lists/defaults", "/lists/tags", "/lists/custom", "/users/alice")


@pytest.mark.parametrize("path", PULLED_VIEWS)
async def test_a_pull_on_a_list_or_a_profile_reads_archidekt_again(stack: Stack, path: str) -> None:
    b = await _member(stack)
    try:
        w = Watch(stack)
        await _view(b, path)
        n = len(w.asked)
        await _view(b, path)
        assert w.since(n) == [], "a plain second view is served from the kept read"
        r = await b.http.get(path, headers=PULL)
        assert r.status_code == 200, r.text[:500]
        assert w.since(n), f"a pull of {path} showed the kept copy"
    finally:
        await b.aclose()


async def test_a_pull_on_binder_lists_reads_the_counts_again(stack: Stack) -> None:
    """The hub draws the counts it knows and static/lists.js asks for the rest (COUNTS_API): after a
    pull it asks for all of them again."""
    from mtg_gateway.lists_page import COUNTS_API

    b = await _member(stack)
    try:
        w = Watch(stack)
        await _view(b, "/collection/lists")
        assert (await b.http.get(COUNTS_API)).json()["ok"]
        n = len(w.asked)
        assert n, "the first counts are read"
        hub = await _view(b, "/collection/lists")
        assert f"data-counts='{COUNTS_API}'" not in hub.text  # every count known: nothing to ask
        assert (await b.http.get(COUNTS_API)).json()["ok"] and w.since(n) == []
        pulled = await b.http.get("/collection/lists", headers=PULL)
        assert pulled.status_code == 200 and f"data-counts='{COUNTS_API}'" in pulled.text
        got = (await b.http.get(COUNTS_API)).json()
        assert got["ok"] and got["counts"], got
        assert len(w.since(n)) >= len(account_lists.KINDS), w.since(n)
        m = len(w.asked)
        assert (await b.http.get(COUNTS_API)).json()["ok"]  # read once per pull, not per ask
        assert w.since(m) == []
    finally:
        await b.aclose()
