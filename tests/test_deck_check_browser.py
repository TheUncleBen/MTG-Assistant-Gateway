"""The deck page's /decks/{id}/check long-poll in the browser tests (conftest, DECK_CHECK_REFUSED).

The deck page asks /check, held up to 25 s, when it shows a deck as last read while it reads it
again. A "networkidle" load counts that request, so with an aged copy a deck page load waited out
the background read (8.5 s in the a6d09a11 audit's repro; up to 25 s, past the load's own limit,
on a starved runner). Every browser context the tests make now has that request refused at once,
so a load never waits on it; a test of the check itself opts out (``deck_check_live``) and sees
the strip the page draws from the real answer. Skipped without a Chromium build."""

from __future__ import annotations

import asyncio
import time

import pytest

from .test_deck_menu_browser import Server, _chromium, _page

pytest.importorskip("playwright")

SLOW_REREAD = 8.0  # the background re-read's Archidekt answer, as in the audit's repro


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _age_and_slow(server: Server) -> list[str]:
    """The deck's kept copy aged past PAGE_DECK_RELOAD (a Playwright goto counts as a reload), so
    the next view shows it at once and reads it again in the background; that re-read takes
    SLOW_REREAD seconds. Returns the log of re-reads."""
    decks = server.app.state.gateway.decks
    real = decks._read_page_deck
    reads: list[str] = []

    async def slow(sub, ref, key, *, fresh):
        if key in decks._page_decks:  # the background re-read of a kept copy
            reads.append(str(ref))
            await asyncio.sleep(SLOW_REREAD)
        return await real(sub, ref, key, fresh=fresh)

    decks._read_page_deck = slow
    now = time.monotonic()
    decks._page_decks = {k: (now - 35.0, v) for k, (_t, v) in decks._page_decks.items()}
    return reads


def test_a_deck_page_load_never_waits_on_the_check_long_poll(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        failed: list[str] = []
        page.on("requestfailed", lambda r: failed.append(r.url))
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")  # reads and keeps it
        reads = _age_and_slow(server)
        started = time.monotonic()
        page.goto(f"{server.base}/decks/42?view=table", wait_until="networkidle")
        took = time.monotonic() - started
        assert reads == ["42"], reads  # the copy was shown as last read and read again behind it
        assert page.locator("[data-check]").count() == 1  # the page asked for the check...
        assert took < SLOW_REREAD / 2, f"the load waited {took:.1f} s on the check long-poll"
        assert page.locator("[data-check]").is_hidden()  # ...and, refused, says nothing
        assert not errors, errors
        browser.close()


@pytest.mark.deck_check_live
def test_a_test_of_the_check_itself_opts_out_and_gets_the_answer(server: Server) -> None:
    """Opted out, the page's own /check request reaches the gateway, is held for the background
    re-read, and its answer (the deck was renamed on Archidekt: changed) makes the page, shown on a
    reload, load itself again with the new deck."""
    from playwright.sync_api import expect, sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="load")
        reads = _age_and_slow(server)
        server.ark.decks[42]["name"] = "Renamed on Archidekt"  # what the re-read will find
        started = time.monotonic()
        with page.expect_response(
            lambda r: r.url == f"{server.base}/decks/42/check", timeout=(SLOW_REREAD + 10) * 1000
        ) as answer:
            page.goto(f"{server.base}/decks/42?view=table", wait_until="load")
            assert "Renamed on Archidekt" not in page.content()  # shown as last read, at once
        assert answer.value.status == 200
        assert time.monotonic() - started >= SLOW_REREAD - 0.5  # it was held for the re-read
        expect(page.locator("main")).to_contain_text("Renamed on Archidekt", timeout=10_000)
        assert reads == ["42"], reads
        assert not errors, errors
        browser.close()
