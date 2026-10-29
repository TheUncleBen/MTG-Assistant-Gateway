"""Cache first, never blank (0.7.46 audit, Standard 1): a page that reads Archidekt answers within
a second even when Archidekt takes 8 seconds. It sends the site's frame with a skeleton, carries
on, and the finished page is served to the same browser session when the page asks for it again
(static/feedback.js does that). The finished page is never served to anyone else."""

from __future__ import annotations

import asyncio
import re
import time

import httpx

from mtg_gateway import latepage
from mtg_gateway.timing import speed_log

from .conftest import Stack
from .test_decks_and_proxy import Browser

PAGE = {"Accept": "text/html,application/xhtml+xml"}


def _slow(stack: Stack, seconds: float) -> list[str]:
    """Every Archidekt answer takes ``seconds``; returns the log of paths asked."""
    ark = stack.ark
    asked: list[str] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.path)
        await asyncio.sleep(seconds)
        return ark.handle(request)

    client = stack.h.app.state.gateway.decks.client
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return asked


async def _linked(stack: Stack) -> Browser:
    b = Browser(stack.h)
    await b.login("/")
    r = await b.link("alice", "pw-alice")
    assert r.status_code == 303, r.text
    return b


async def test_a_slow_page_sends_its_frame_at_once_then_the_finished_page(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        asked = _slow(stack, 3.0)
        t0 = time.monotonic()
        r = await b.http.get("/notifications", headers=PAGE)
        took = time.monotonic() - t0
        assert took < 1.5, took
        assert r.status_code == 200
        assert "Reading this page from Archidekt" in r.text and "class='topbar'" in r.text
        assert r.headers["cache-control"] == "no-store"  # Back never lands on the frame
        key = re.search(r"data-late='(/late/[A-Za-z0-9_-]+)'", r.text).group(1)
        assert re.search(r"<title>Alerts ·", r.text)
        wait = await b.http.get(key)
        assert wait.status_code == 200 and wait.json()["ready"] is True
        sent = len(asked)
        t1 = time.monotonic()
        page = await b.http.get("/notifications", headers=PAGE)
        assert time.monotonic() - t1 < 0.5
        assert page.status_code == 200 and "data-late" not in page.text
        assert "Alerts" in page.text
        assert len(asked) == sent  # the finished page, not a second read
    finally:
        await b.aclose()


async def test_the_finished_page_is_only_for_the_session_that_asked(stack: Stack) -> None:
    b = await _linked(stack)
    other = Browser(stack.h)
    await other.login("/")
    try:
        asked = _slow(stack, 2.0)
        r = await b.http.get("/notifications", headers=PAGE)
        key = re.search(r"data-late='(/late/[A-Za-z0-9_-]+)'", r.text).group(1)
        refused = await other.http.get(key)
        assert refused.status_code == 404
        await b.http.get(key)
        # the other session asks for itself (it has no Archidekt link): never the first one's page
        mine = await other.http.get("/notifications", headers=PAGE)
        assert "alice" not in mine.text.lower() or "Link" in mine.text
        sent = len(asked)
        page = await b.http.get("/notifications", headers=PAGE)  # still kept for its own session
        assert "data-late" not in page.text and len(asked) == sent
    finally:
        await b.aclose()
        await other.aclose()


async def test_a_quick_page_is_sent_as_it_is(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        r = await b.http.get("/notifications", headers=PAGE)
        assert r.status_code == 200 and "data-late" not in r.text
    finally:
        await b.aclose()


async def test_a_script_fetch_and_other_pages_are_never_deferred(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        _slow(stack, 1.2)
        r = await b.http.get("/notifications", headers={"Accept": "application/json"})
        assert "data-late" not in r.text
        assert latepage.late_page_of("/decks/42/edit") is None
        assert latepage.late_page_of("/decks/42") is not None
        assert latepage.late_page_of("/account") is None
    finally:
        await b.aclose()


async def test_the_speed_log_counts_a_late_page_when_its_content_is_there(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        _slow(stack, 1.5)
        before = len(speed_log.rows())
        r = await b.http.get("/notifications", headers=PAGE)
        key = re.search(r"data-late='(/late/[A-Za-z0-9_-]+)'", r.text).group(1)
        await b.http.get(key)
        await asyncio.sleep(0.05)
        rows = [x for x in speed_log.rows()[before:] if x.route == "/notifications"]
        assert len(rows) == 1  # the frame is not a view of its own
        assert rows[0].seconds >= 1.4 and rows[0].archidekt >= 1.4
    finally:
        await b.aclose()
