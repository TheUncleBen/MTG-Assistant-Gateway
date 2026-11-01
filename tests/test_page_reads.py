"""A page does not ask Archidekt again for what the gateway already holds (0.7.46 review, speed).

Every Archidekt call waits its turn in one pacer shared by every member (30 calls started a
minute), so a page that reads again what the member saw a moment ago can hold the next page up for
up to a minute. A deck's other pages (Deck settings, Goldfish, Compare, the editor, a report's
"changed since" line) use the read the deck page keeps; the Move box uses the folder tree My decks
keeps; Alerts, Wants and My lists, the forum, profiles and a deck's collaborators are kept briefly
per member. None of them ever shows what it showed before the member's own change: every write
the gateway sends with the member's session forgets them."""

from __future__ import annotations

import asyncio
import re
import time

import httpx
import pytest

from mtg_gateway import profile_page
from mtg_gateway.archidekt import Pacer
from mtg_gateway.decks import ShortReads
from mtg_gateway.timing import speed_log

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_decks_and_proxy import Browser
from .test_history_page import store_report

stack_options = stack_with(COMPANION)


class Watch:
    """Every Archidekt request the gateway sends, and the most sent at once."""

    def __init__(self, stack: Stack, seconds: float = 0.0):
        self.asked: list[tuple[str, str]] = []
        self.out = 0
        self.most = 0
        ark = stack.ark

        async def handle(request: httpx.Request) -> httpx.Response:
            self.asked.append((request.method, request.url.path))
            self.out += 1
            self.most = max(self.most, self.out)
            try:
                if seconds:
                    await asyncio.sleep(seconds)
                return ark.handle(request)
            finally:
                self.out -= 1

        client = stack.gw.decks.client
        client._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))

    def since(self, n: int) -> list[tuple[str, str]]:
        return self.asked[n:]


def _sub(stack: Stack) -> str:
    return stack.gw.db._one("SELECT sub FROM users", ())["sub"]


async def _member(stack: Stack) -> Browser:
    """Alice, linked, following Amy (a following list of nobody is read again on purpose, so a
    follow made on archidekt.com shows at once)."""
    stack.ark.follows.setdefault("alice", set()).add(78)
    return await linked_browser(stack)


async def _view(b: Browser, path: str) -> httpx.Response:
    r = await b.http.get(path, headers=NAV)
    assert r.status_code == 200, (path, r.status_code, r.text[:500])
    return r


@pytest.fixture(autouse=True)
def _forget_alerts():
    """profile_page keeps the Alerts list per member in module memory; tests share member ids."""
    profile_page._LIST.clear()
    yield
    profile_page._LIST.clear()


async def test_a_decks_other_pages_use_the_read_the_deck_page_keeps(stack: Stack) -> None:
    b = await _member(stack)
    try:
        w = Watch(stack)
        await _view(b, "/decks/42")
        n = len(w.asked)
        for path in ("/decks/42/goldfish", "/decks/42/compare", "/decks/42/edit", "/decks/42/settings"):
            await _view(b, path)
        deck_reads = [p for _m, p in w.since(n) if re.fullmatch(r"/api/decks/42/?", p)]
        assert deck_reads == [], w.since(n)
    finally:
        await b.aclose()


SECOND_VIEWS = (
    "/decks/42/settings",
    "/decks/42/goldfish",
    "/decks/42/compare",
    "/decks/42/edit",
    "/decks/api/folders",
    "/notifications",
    "/collection/wants",
    "/lists/templates",
    "/forum",
    "/users/alice",
)


@pytest.mark.parametrize("path", SECOND_VIEWS)
async def test_a_second_view_makes_no_archidekt_call(stack: Stack, path: str) -> None:
    b = await _member(stack)
    try:
        w = Watch(stack)
        first = await b.http.get(path, headers=NAV)
        assert first.status_code == 200, first.text[:500]
        assert w.asked, "the first view reads Archidekt"
        n = len(w.asked)
        again = await b.http.get(path, headers=NAV)
        assert again.status_code == 200
        assert w.since(n) == []
    finally:
        await b.aclose()


async def test_deck_settings_asks_its_reads_at_once(stack: Stack) -> None:
    """The deck, the folder tree, the collaborators and the people followed are independent: a cold
    view asks them together (the member may have three Archidekt calls out at once), not one after
    another."""
    b = await _member(stack)
    try:
        stack.gw.decks.folder_trees.forget(_sub(stack))
        w = Watch(stack, seconds=0.3)
        await _view(b, "/decks/42/settings")
        assert len(w.asked) >= 4, w.asked
        assert w.most >= 3, (w.most, w.asked)
    finally:
        await b.aclose()


async def test_the_members_own_write_forgets_every_kept_read(stack: Stack) -> None:
    b = await _member(stack)
    sub = _sub(stack)
    gw = stack.gw
    try:
        w = Watch(stack)
        paths = ("/decks/42/settings", "/decks/api/folders", "/notifications", "/forum", "/users/alice")
        for path in paths:
            await _view(b, path)
        n = len(w.asked)
        for path in paths:
            await _view(b, path)
        assert w.since(n) == []
        for path in paths:
            await _view(b, path)  # kept from the view before
            # any write the gateway sends with the member's session (here a bookmark)
            await gw.decks._call(sub, lambda token: gw.decks.client.bookmark_deck(token, "42", on=True))
            n = len(w.asked)
            await _view(b, path)
            assert w.since(n), f"{path} was shown from before the member's write"
    finally:
        await b.aclose()


async def test_a_read_under_way_during_the_members_write_keeps_nothing() -> None:
    kept = ShortReads(60.0)
    gate = asyncio.Event()
    calls = 0

    async def fetch() -> dict:
        nonlocal calls
        calls += 1
        await gate.wait()
        return {"n": calls}

    reading = asyncio.ensure_future(kept.get("m1", ("k",), fetch))
    await asyncio.sleep(0)
    kept.drop("m1")  # the member's write went out while the read was under way
    gate.set()
    assert (await reading) == {"n": 1}
    assert (await kept.get("m1", ("k",), fetch)) == {"n": 2}  # read again, not the old copy
    assert (await kept.get("m1", ("k",), fetch)) == {"n": 2}  # and that one is kept
    assert calls == 2
    # another member's write leaves this member's copy alone; a write of no known member does not
    kept.drop("m2")
    assert (await kept.get("m1", ("k",), fetch)) == {"n": 2}
    kept.clear()
    gate.set()
    assert (await kept.get("m1", ("k",), fetch)) == {"n": 3}


async def test_kept_reads_are_copies_and_expire() -> None:
    kept = ShortReads(0.2)

    async def fetch() -> dict:
        return {"rows": [1]}

    first = await kept.get("m1", ("k",), fetch)
    first["rows"].append(2)  # a page changing what it got never changes the kept copy
    assert (await kept.get("m1", ("k",), fetch)) == {"rows": [1]}
    await asyncio.sleep(0.25)
    asked = []

    async def counted() -> dict:
        asked.append(1)
        return {"rows": [3]}

    assert (await kept.get("m1", ("k",), counted)) == {"rows": [3]} and asked == [1]


# -- the report page never waits on Archidekt ----------------------------------------------------
async def test_a_report_page_draws_at_once_and_its_changes_line_comes_after(stack: Stack) -> None:
    b = await _member(stack)
    sub = _sub(stack)
    store_report(stack.h.db, sub, "rep_fast")
    try:
        Watch(stack, seconds=1.5)
        t0 = time.monotonic()
        r = await _view(b, "/history/reports/rep_fast")
        assert time.monotonic() - t0 < 1.0
        assert "data-changes-url='/history/reports/rep_fast/changes'" in r.text
        changes = await b.http.get("/history/reports/rep_fast/changes")
        assert changes.status_code == 200
        # the read the page started is kept for the deck's pages: the next view draws the line at once
        t1 = time.monotonic()
        again = await _view(b, "/history/reports/rep_fast")
        assert time.monotonic() - t1 < 1.0
        assert "data-changes-url" not in again.text
    finally:
        await b.aclose()


# -- one member clicking about once a second never stalls on the shared pacer ---------------------
CLICKS = (
    "/",
    "/decks",
    "/decks/42",
    "/decks/42/settings",
    "/decks/42/goldfish",
    "/decks/42/compare",
    "/decks/42/edit",
    "/collection/wants",
    "/notifications",
    "/forum",
    "/users/alice",
    "/lists/templates",
    "/decks/api/folders",
    "/decks/42",
    "/decks/42/settings",
    "/decks/42/compare",
    "/decks/42/goldfish",
    "/forum",
    "/notifications",
    "/decks",
)


async def test_one_member_clicking_through_pages_stays_under_the_shared_cap(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """40 page views, a few a second, behind the production pacer (one start a second outside a
    page's burst, 30 a minute for everyone together): before 0.7.46's review fix each view read
    Archidekt again, the cap was reached in about 30 s and the next page waited for a slot for
    up to a minute. The kept reads keep one member far under it."""
    gw = stack.gw
    # the kept deck read counts as fresh for the run, so a slow test machine does not turn the
    # second round's views into background re-reads (that path has its own tests)
    monkeypatch.setattr(gw.decks, "page_deck_seconds", 300.0)
    b = await _member(stack)
    try:
        gw.decks.client.pacer = Pacer(1.0, 30)
        w = Watch(stack, seconds=0.05)
        k0 = len(speed_log.rows())
        slowest = 0.0
        for path in CLICKS + CLICKS:
            t0 = time.monotonic()
            await _view(b, path)
            slowest = max(slowest, time.monotonic() - t0)
            await asyncio.sleep(0.3)
        assert len(w.asked) < 30, w.asked
        waits = [r.pacer for r in speed_log.rows()[k0:]]
        assert max(waits) < 1.5, sorted(waits)[-5:]
        assert slowest < 3.0, slowest
    finally:
        await b.aclose()
