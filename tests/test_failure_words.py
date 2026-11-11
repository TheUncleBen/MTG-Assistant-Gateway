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
        assert "<title>Binder" in r.text and "<h1>Archidekt collection" in r.text  # its own frame
        assert "Archidekt is busy, so your binder can't be read yet." in r.text and "busy-title" not in r.text
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


@pytest.mark.parametrize(
    ("answer", "words"),
    [
        (503, "Scryfall had a problem answering. Try again later."),
        (httpx.ConnectError("down"), "Scryfall couldn't be reached. Try again later."),
        (httpx.ReadTimeout("slow"), "Scryfall didn't answer in time. Try again later."),
    ],
)
async def test_scryfall_failures_are_plain_sentences(answer: object, words: str) -> None:
    """Whole-application audit (resolve_cards, save_scan_session): "Scryfall unreachable:
    ConnectError" and "Scryfall returned 503" reached the member; now a sentence, the details in the
    log."""
    from mtg_gateway.scan.scryfall import ScryfallClient, ScryfallError

    def reply(request: httpx.Request) -> httpx.Response:
        if isinstance(answer, Exception):
            raise answer
        return httpx.Response(int(answer), json={"object": "error"})  # type: ignore[call-overload]

    client = ScryfallClient(min_interval=0, http=httpx.AsyncClient(transport=httpx.MockTransport(reply)))
    for ask in (client.named("Sol Ring"), client.autocomplete("Sol")):
        with pytest.raises(ScryfallError) as err:
            await ask
        assert str(err.value) == words and not any(raw in str(err.value) for raw in RAW), err.value


@pytest.mark.parametrize(
    ("answer", "state"),
    [
        ((429, "30"), "slow_down"),
        ((503, None), "failing"),
        ((httpx.ConnectError("down"), None), "unreachable"),
    ],
)
async def test_one_outage_reads_the_same_in_the_tools_and_on_the_home_page(
    stack: Stack, answer: tuple, state: str
) -> None:
    """0.7.46 sweep B-1: within one 429, list_my_decks said "slow down; try again later" and
    get_deck "paused for now. Try again a little later."; the home panel said "Archidekt did not
    answer in time." for a 429, a 503 and a refused connection alike. Each state now has its one
    sentence (busy.OUTAGE), and every surface says that one."""
    import html

    from mtg_gateway.busy import outage

    from .test_decks_and_proxy import call, mcp_token, structured

    b = await _linked(stack)
    token = await mcp_token(stack.h)
    svc = stack.h.app.state.gateway.decks
    sub = svc.db._one("SELECT sub FROM users", ())["sub"]
    pacer = svc.client.pacer
    first = outage(state, again=state == "slow_down")
    # what holds while it lasts: the first refusal's own words, then the pause it opened
    after = outage("slow_down", again=True) if state == "slow_down" else None
    try:
        svc.forget_member(sub)
        stack.ark.inject = [answer] * 40  # type: ignore[list-item]
        said = [structured(await call(stack.h, token, "list_my_decks"))["message"]]
        said.append(structured(await call(stack.h, token, "get_deck", {"deck_ref": "42"}))["message"])
        assert said[0] == first, said
        assert said[1] in {first, after, outage("paused", again=True)}, said
        if state == "slow_down":
            assert said == [first, first], said  # one 429, one sentence in every tool
        pacer._failures = pacer._open_until = pacer._retry_until = 0
        svc.forget_member(sub)
        stack.ark.inject = [answer] * 40  # type: ignore[list-item]
        home = (await b.http.get("/", headers=NAV)).text
        assert html.escape(first) in home, home.split("<h2>My decks</h2>", 1)[-1][:300]
        assert "did not answer in time" not in home
        pacer._failures = pacer._open_until = pacer._retry_until = 0
        svc.forget_member(sub)
        stack.ark.inject = [answer] * 40  # type: ignore[list-item]
        panel = (await b.http.get("/api/decks/mine?shape=recent")).json()
        assert html.escape(first) in panel["html"], panel
    finally:
        stack.ark.inject = []
        pacer._failures = pacer._open_until = pacer._retry_until = 0
        await b.aclose()
