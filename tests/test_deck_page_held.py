"""The deck page while Archidekt is slow or failing (0.7.46 audit D-02, D-03; owner decision C1):
a Reload shows a read younger than 30 seconds as it is, and an older one at once while the deck is
read again behind it; when Archidekt can't be asked, the member's last read of the deck is shown
read-only, with how old it is and Try again, instead of an error page."""

from __future__ import annotations

import asyncio
import time

from mtg_gateway import decks as decks_mod

from .conftest import Stack
from .test_navigation_speed import NAV, _deck_reads, _linked

RELOAD = {**NAV, "Cache-Control": "max-age=0"}


def _age(stack: Stack, seconds: float) -> None:
    decks = stack.h.app.state.gateway.decks
    decks._page_decks = {k: (t - seconds, d) for k, (t, d) in decks._page_decks.items()}
    decks._page_last = {k: (t - seconds, d) for k, (t, d) in decks._page_last.items()}


async def test_a_reload_soon_after_a_read_does_not_ask_archidekt_again(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        assert (await b.http.get("/decks/42", headers=NAV)).status_code == 200
        reads = _deck_reads(stack)
        _age(stack, decks_mod.PAGE_DECK_SECONDS + 1)  # past the plain view's 20 s, under 30 s
        page = await b.http.get("/decks/42", headers=RELOAD)
        assert page.status_code == 200 and "Sample Commander Deck" in page.text
        assert _deck_reads(stack) == reads
        assert "data-check" not in page.text
    finally:
        await b.aclose()


async def test_an_older_reload_shows_the_deck_at_once_and_loads_the_changes(stack: Stack) -> None:
    decks = stack.h.app.state.gateway.decks
    b = await _linked(stack)
    try:
        assert (await b.http.get("/decks/42", headers=NAV)).status_code == 200
        reads = _deck_reads(stack)
        _age(stack, decks_mod.PAGE_DECK_RELOAD + 1)
        stack.ark.decks[42]["name"] = "Renamed on the site"
        held = asyncio.Event()
        real = decks.get_any_deck

        async def slow_read(sub, ref, **kw):  # noqa: ANN001, ANN003
            await asyncio.wait_for(held.wait(), 5)
            return await real(sub, ref, **kw)

        decks.get_any_deck = slow_read  # type: ignore[method-assign]
        t0 = time.monotonic()
        page = await b.http.get("/decks/42", headers=RELOAD)
        assert time.monotonic() - t0 < 1.0
        assert "Sample Commander Deck" in page.text and "Renamed on the site" not in page.text
        assert "data-check='/decks/42/check'" in page.text and "data-reload='1'" in page.text
        assert "Checking Archidekt for changes" in page.text
        held.set()
        found = (await b.http.get("/decks/42/check")).json()
        assert found == {"changed": True, "failed": False}
        decks.get_any_deck = real  # type: ignore[method-assign]
        again = await b.http.get("/decks/42", headers=RELOAD)  # what static/feedback.js loads
        assert "Renamed on the site" in again.text
        assert _deck_reads(stack) == reads + 1
    finally:
        await b.aclose()


async def test_a_failed_read_behind_the_page_keeps_the_copy_and_says_so(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        assert (await b.http.get("/decks/42", headers=NAV)).status_code == 200
        _age(stack, decks_mod.PAGE_DECK_SECONDS + 1)
        stack.ark.fail_deck_reads = True
        page = await b.http.get("/decks/42", headers=NAV)
        assert page.status_code == 200 and "data-check" in page.text
        found = (await b.http.get("/decks/42/check")).json()
        assert found == {"changed": False, "failed": True}
        # the copy on show is not thrown away: the next view still draws the deck
        again = await b.http.get("/decks/42", headers=NAV)
        assert again.status_code == 200 and "Sample Commander Deck" in again.text
    finally:
        await b.aclose()


async def test_archidekt_failing_shows_the_last_read_read_only_and_dated(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        assert (await b.http.get("/decks/42", headers=NAV)).status_code == 200
        _age(stack, decks_mod.PAGE_DECK_STALE + 300)  # too old to show without a read
        stack.ark.fail_deck_reads = True
        page = await b.http.get("/decks/42", headers=NAV)
        assert page.status_code == 200, page.text[:300]
        assert "Sample Commander Deck" in page.text and "data-card='Sol Ring'" in page.text
        assert "so this is the deck as it was read 6 hours ago" in page.text
        assert "Changes are off until Archidekt can be asked again." in page.text
        assert "<a class='btn' href='/decks/42'>Try again</a>" in page.text
        assert "/decks/42/edit" not in page.text  # no Edit deck, no edit menus
        assert "HTTP 5" not in page.text
    finally:
        await b.aclose()


async def test_a_write_drops_the_view_but_not_the_copy_kept_for_a_failure(stack: Stack) -> None:
    decks = stack.h.app.state.gateway.decks
    b = await _linked(stack)
    try:
        assert (await b.http.get("/decks/42", headers=NAV)).status_code == 200
        decks._on_archidekt_write("/decks/42/modifyCards/v2/")
        assert not decks._page_decks and decks._page_last
        stack.ark.fail_deck_reads = True
        page = await b.http.get("/decks/42", headers=NAV)
        assert page.status_code == 200 and "Changes are off" in page.text
    finally:
        await b.aclose()


async def test_with_no_read_to_fall_back_on_the_busy_page_stays(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        stack.ark.fail_deck_reads = True
        page = await b.http.get("/decks/42", headers=NAV)
        assert page.status_code == 503
    finally:
        await b.aclose()


async def test_the_copy_kept_for_a_failure_is_forgotten_with_the_member(stack: Stack) -> None:
    decks = stack.h.app.state.gateway.decks
    b = await _linked(stack)
    try:
        assert (await b.http.get("/decks/42", headers=NAV)).status_code == 200
        assert decks._page_last
        decks.forget_member(next(iter(decks._page_last))[0])
        assert not decks._page_last
    finally:
        await b.aclose()
