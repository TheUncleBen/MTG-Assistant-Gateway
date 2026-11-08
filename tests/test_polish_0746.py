"""The 0.7.46 polish follow-ups that need no browser: the Compare page's picker on a cold start
(the loading box, and the chips /api/decks/mine?shape=pick answers with), and the Binder page
reading the collection and the profile side by side."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx

from mtg_gateway import deckpage
from mtg_gateway.archidekt import parse_deck
from mtg_gateway.archidekt_csv import parse_export, to_deck_json

from .fake_archidekt import FakeArchidekt
from .test_editor_browser import _link
from .test_suggest_browser import Server

LONGER = "Aesi, Tyrant of Gyre Strait: Lands Matter Big Ramp Value Pile (Budget Upgrade, v3)"
CARDS = parse_export((Path(__file__).parent / "fixtures" / "sample_deck.csv").read_text(encoding="utf-8"))


def _server(tmp_path: Path) -> Server:
    """alice with a second deck (44), so the Compare page has one of hers to pick."""
    s = Server(tmp_path, writes_enabled=True)
    s.ark.decks[44] = to_deck_json(CARDS[:20], deck_id=44, name=LONGER, owner="alice")
    s.ark.users["alice"]["decks"].append(44)
    s.start()
    return s


# -- the Compare page's picker --------------------------------------------------------------------------


def test_the_picker_draws_a_loading_box_while_the_deck_list_is_read() -> None:
    deck = parse_deck(FakeArchidekt().decks[42])
    html = deckpage.compare_picker_html(deck, None, [], other_ref="44", folded=False)
    assert "<h3 class='cmp-sub' data-pick-head>My decks</h3>" in html
    assert (
        "<div class='cmp-chips skeleton' aria-busy='true' aria-label='Reading your decks' "
        "data-pick-src='/api/decks/mine?shape=pick&amp;deck=42&amp;with=44'>" in html
    )
    # the list as read: the same chips, no box
    rows = [{"id": 44, "name": LONGER, "updated_at": "2026-10-20"}, {"id": 42, "name": "This one"}]
    html = deckpage.compare_picker_html(deck, rows, [], other_ref="44", folded=False)
    assert "data-pick-src" not in html and "skeleton" not in html
    assert "<a class='ui-chip cmp-pick' href='/decks/42/compare?with=44' aria-current='true'>Aesi" in html
    assert "This one" not in html
    # nothing of hers to pick, and no snapshot: nothing
    assert (
        deckpage.compare_picker_html(deck, [{"id": 42, "name": "This one"}], [], other_ref="", folded=False)
        == ""
    )


def test_the_cold_page_holds_the_box_and_the_pick_shape_answers_its_chips(tmp_path: Path) -> None:
    s = _server(tmp_path)
    try:
        sid = _link(s)
        h = httpx.Client(base_url=s.base, cookies={"mtg_session": sid}, timeout=30)
        _slow(s, 0.3)  # Archidekt takes a moment, as it does: the deck list was never read
        s.ark.calls.clear()
        t = time.monotonic()
        page = h.get("/decks/42/compare").text
        assert "data-pick-src='/api/decks/mine?shape=pick&amp;deck=42&amp;with='" in page
        assert "class='ui-chip cmp-pick'" not in page
        assert time.monotonic() - t < 0.9  # the deck's own read and the precons; never the list's
        r = h.get("/api/decks/mine?shape=pick&deck=42&with=44", headers={"Sec-Fetch-Site": "same-origin"})
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["ok"] is True and data["shape"] == "pick" and data["count"] == 1
        assert data["html"] == (
            "<div class='cmp-chips'><a class='ui-chip cmp-pick' href='/decks/42/compare?with=44' "
            f"aria-current='true'>{LONGER}</a></div>"
        )
        # the deck list was read once between the two, and no folder tree for the chips
        assert s.ark.calls.count(("GET", "/api/decks/v3/")) == 1
        assert not any("folders" in path for _m, path in s.ark.calls), s.ark.calls
        # the list as read: the page draws the chips itself
        page = h.get("/decks/42/compare").text
        assert "data-pick-src" not in page and "class='ui-chip cmp-pick'" in page
    finally:
        s.stop()


# -- the Binder page's two reads -------------------------------------------------------------------------


def test_the_binder_reads_the_collection_and_the_profile_side_by_side(tmp_path: Path) -> None:
    """On a cold start the page read the collection, then the profile (for the Visibility line),
    one after the other: two Archidekt waits in a row. Both start together now."""
    s = Server(tmp_path, writes_enabled=True)
    s.start()
    try:
        sid = _link(s)
        h = httpx.Client(base_url=s.base, cookies={"mtg_session": sid}, timeout=30)
        _slow(s, 0.3)  # every answer takes this long
        s.ark.calls.clear()
        t = time.monotonic()
        r = h.get("/collection")
        took = time.monotonic() - t
        assert r.status_code == 200
        assert [p for _m, p in s.ark.calls] == ["/api/collection/77/v2/", "/api/users/77/private/"]
        assert took < 0.5, took
    finally:
        s.stop()


def _slow(server: Server, seconds: float) -> None:
    ark = server.ark

    async def handle(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(seconds)
        return ark.handle(request)

    client = server.app.state.gateway.decks.client
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
