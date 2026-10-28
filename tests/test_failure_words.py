"""Failure words (0.7.46 audit, Standard 5; D-05, C-15): when Archidekt is busy, failing or slow,
a member's page says so in plain sentences, names its own area ("collection" on the Binder), and
shows no HTTP code, exception name or number of seconds."""

from __future__ import annotations

import httpx
import pytest

from mtg_gateway.archidekt import ArchidektClient, ArchidektError, Pacer
from mtg_gateway.busy import BusyPlan, area_of, busy_body
from tests.fake_archidekt import FakeArchidekt

from .conftest import Stack
from .test_navigation_speed import NAV, _linked

RAW = ("HTTP 5", "HTTP 4", "ReadTimeout", "ConnectError", "request failed:", " seconds")


def _client(ark: FakeArchidekt) -> ArchidektClient:
    return ArchidektClient("https://archidekt.com/api", "test", Pacer(0.0), http=ark.client())


@pytest.mark.parametrize(
    ("answer", "words"),
    [
        (500, "Archidekt had a problem answering."),
        (503, "Archidekt had a problem answering."),
        (httpx.ReadTimeout("slow"), "Archidekt didn't answer in time."),
        (httpx.ConnectError("down"), "Archidekt couldn't be reached."),
    ],
)
async def test_archidekt_failures_are_plain_sentences(answer: object, words: str) -> None:
    ark = FakeArchidekt()
    ark.inject = [(answer, None)]  # type: ignore[list-item]
    with pytest.raises(ArchidektError) as err:
        await _client(ark).search_decks(name="x")
    assert str(err.value) == words


async def test_a_running_pause_says_so_without_seconds() -> None:
    ark = FakeArchidekt()
    client = _client(ark)
    ark.inject = [(429, "40")]
    with pytest.raises(ArchidektError):
        await client.search_decks(name="x")
    with pytest.raises(ArchidektError) as err:
        await client.search_decks(name="y")
    assert err.value.kind == "rate_limited" and err.value.retry_after
    assert not any(ch.isdigit() for ch in str(err.value)), str(err.value)


def test_the_busy_panel_names_its_area_and_leaves_archidekts_own_words_out() -> None:
    plan = BusyPlan(
        "rate_limited",
        "Archidekt is busy",
        "Archidekt asked the gateway to slow down, so the gateway is pausing its Archidekt requests "
        "for now. Nothing is wrong with your decks or your account; try again later.",
        429,
        30,
        "Archidekt asked the gateway to slow down, so it is pausing its Archidekt requests for 30 seconds.",
    )
    binder = busy_body(plan, "/collection")
    assert "Nothing is wrong with your collection or your account" in binder
    assert "decks" not in binder and "30" not in binder
    assert "href='/collection'>Try again</a>" in binder and "My decks" not in binder
    deck = busy_body(plan, "/decks/42")
    assert "Nothing is wrong with your decks or your account" in deck and "href='/decks'>My decks</a>" in deck
    forum = busy_body(plan, "/forum")
    assert "Nothing is wrong with your account" in forum and "href='/'>Home</a>" in forum
    assert area_of("/lists/pins")[0] == "lists"


async def test_a_busy_binder_keeps_its_frame_and_says_collection(stack: Stack) -> None:
    b = await _linked(stack)
    try:
        stack.ark.inject = [(429, "30")] * 10
        r = await b.http.get("/collection", headers=NAV)
        assert r.status_code == 429
        assert "<title>Binder" in r.text and "<h2 class='busy-title'>Archidekt is busy</h2>" in r.text
        assert "your collection" in r.text and "your decks" not in r.text
        assert "My decks" not in r.text.split("<main", 1)[1]
        for raw in RAW:
            assert raw not in r.text, raw
    finally:
        stack.ark.inject = []
        stack.h.app.state.gateway.decks.client.pacer._retry_until = 0
        await b.aclose()


@pytest.mark.parametrize("answer", [500, httpx.ReadTimeout("slow")])
async def test_member_pages_never_show_archidekts_raw_words(stack: Stack, answer: object) -> None:
    b = await _linked(stack)
    pacer = stack.h.app.state.gateway.decks.client.pacer
    try:
        for path in (
            "/decks/43",
            "/notifications",
            "/forum",
            "/users/amy",
            "/lists/templates",
            "/collection",
        ):
            stack.ark.inject = [(answer, None)] * 20  # type: ignore[list-item]
            r = await b.http.get(path, headers=NAV)
            for raw in RAW:
                assert raw not in r.text, (path, raw)
            pacer._failures = pacer._open_until = 0
    finally:
        stack.ark.inject = []
        await b.aclose()
