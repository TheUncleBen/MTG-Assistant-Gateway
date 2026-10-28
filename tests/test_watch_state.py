"""A card on the watchlist says so wherever it can be watched, and a watch is never added twice
(Ben, 2026-10-28). A printing search's Watch button is "Watching" for the watched printing only;
the pages' menus learn the watched names from ``GET /watchlist/api/names``; a Watch by name of a
card already watched in any printing (in the same finish) is the watch it has, and so is the same
printing named in other letter case."""

from __future__ import annotations

import json

from mtg_gateway import cardsearch_view as cv

from .conftest import Stack, stack_with
from .test_cardsearch_watch import ESPER
from .test_watchlist import Clock, _browser, _options

stack_options = stack_with(_options())


def test_a_printing_search_marks_only_the_watched_printing() -> None:
    by_name = cv.watched_names([{"name": "Esper Sentinel", "set_code": "", "collector_number": ""}])
    this = cv.watched_names([{"name": "Esper Sentinel", "set_code": "mh2", "collector_number": "12"}])
    other = cv.watched_names([{"name": "Esper Sentinel", "set_code": "plst", "collector_number": "7"}])
    for watched, on in ((by_name, False), (this, True), (other, False)):
        tile = cv.card_tile_html(ESPER, can_add=True, reason="", printing=True, watched=watched)
        assert ("class='wbtn on'" in tile) is on, (watched, tile)
    # a search by name: any printing watched is the card watched
    for watched in (by_name, this, other):
        assert "class='wbtn on'" in cv.card_tile_html(ESPER, can_add=True, reason="", watched=watched)
    # the names alone, for the deck editor's list
    assert cv.watched_name_list(this | by_name) == ["esper sentinel"]


async def test_a_card_watched_in_one_printing_is_not_watched_again_by_name(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    try:
        csrf = await b.csrf("/watchlist")
        hdr = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
        sub = stack.gw.db._one("SELECT sub FROM users", ())["sub"]
        first = await stack.gw.prices.watch_card(sub, "Sol Ring", set_code="frc", collector_number="21")
        assert first["created"] is True
        for body in (
            {"name": "Sol Ring"},
            {"name": "sol ring"},
            {"name": "Sol Ring", "set_code": "FRC", "collector_number": "21"},
        ):
            r = await b.http.post("/watchlist/api/watch", content=json.dumps(body), headers=hdr)
            assert r.status_code == 200 and r.json()["watch"]["created"] is False, (body, r.text)
            assert r.json()["watch"]["watch_id"] == first["watch_id"]
        assert len(stack.gw.prices.watched_cards(sub)) == 1
        # another printing, named by its set and number, is another watch
        body = {"name": "Sol Ring", "set_code": "c21", "collector_number": "263"}
        r = await b.http.post("/watchlist/api/watch", content=json.dumps(body), headers=hdr)
        assert r.status_code == 200 and r.json()["watch"]["created"] is True, r.text
        # the names the pages' menus mark
        r = await b.http.get("/watchlist/api/names")
        assert r.status_code == 200 and r.json() == {"ok": True, "names": ["Sol Ring"]}
        assert "no-store" in r.headers["cache-control"]
    finally:
        await b.aclose()
