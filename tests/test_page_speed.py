"""A page change is not held up by background work (Ben, 27 Oct 2026: "random 10-20 s page loads
mid-use"): with the shipped pacing (a second between starts, 30 a minute), a member's page reads
Archidekt while a slow background read (the deck sync, an assistant) is still waiting for its
answer, instead of queueing behind it."""

from __future__ import annotations

import asyncio
import time

import httpx

from mtg_gateway.archidekt import Pacer

from .conftest import AUTO_PROXY, Stack, stack_with
from .test_decks_and_proxy import Browser

stack_options = stack_with(AUTO_PROXY)

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
SLOW_SECONDS = 4.0


def _slow_searches(stack: Stack, release: asyncio.Event) -> None:
    """Deck searches (what the background call below sends) wait for ``release``: Archidekt
    answering one call slowly. Every other request is answered at once by the fake."""
    ark = stack.ark

    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/decks/v3/" and request.url.params.get("name") == "Slow":
            await asyncio.wait_for(release.wait(), SLOW_SECONDS)
        return ark.handle(request)

    client = stack.h.app.state.gateway.decks.client
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client.pacer = Pacer(1.0, 30)  # the shipped MTG_ARCHIDEKT_MIN_INTERVAL and _MAX_PER_MINUTE


async def test_a_page_does_not_wait_behind_a_slow_background_read(stack: Stack) -> None:
    b = Browser(stack.h)
    await b.login("/")
    r = await b.link("alice", "pw-alice")
    assert r.status_code == 303, r.text
    release = asyncio.Event()
    _slow_searches(stack, release)
    client = stack.h.app.state.gateway.decks.client
    try:
        background = asyncio.create_task(client.search_decks(name="Slow"))
        await asyncio.sleep(0.05)
        assert not background.done()
        t0 = time.monotonic()
        page = await b.http.get("/decks/42", headers=NAV)
        took = time.monotonic() - t0
        assert page.status_code == 200 and "Sample Commander Deck" in page.text
        # the page waited at most the gap between two starts, not for the slow answer
        assert took < 2.0, took
        assert not background.done()
        release.set()
        await background
    finally:
        release.set()
        await b.aclose()
