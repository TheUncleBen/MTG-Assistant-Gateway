"""Admin's "Speed, last 24 h" card (owner decision C1, 0.7.46; audit SH-28): how many page views
were slow, where their time went (waiting for a turn in the shared pacer, Archidekt answering,
Archidekt failing), the slowest pages by their address pattern, never a member or a deck."""

from __future__ import annotations

import time

from mtg_gateway import timing
from mtg_gateway.admin import _speed_card
from mtg_gateway.timing import PageTime, SpeedLog

from .conftest import Harness
from .test_admin import NAVIGATE, admin_browser, gw  # noqa: F401


def _log(now: float) -> SpeedLog:
    log = SpeedLog()
    log.started = now - 3 * 86400
    log.add(PageTime(now - 2 * 86400, "/forum", 30.0, 30.0, 29.0, 0, 200))  # older than a day
    for _ in range(19):
        log.add(PageTime(now - 60, "/users/{username}", 0.4, 0.3, 0.0, 0, 200))
    log.add(PageTime(now - 30, "/users/{username}", 17.3, 17.0, 16.5, 0, 200))
    log.add(PageTime(now - 20, "/decks/{deck_id}", 9.0, 8.8, 0.2, 0, 200))
    log.add(PageTime(now - 10, "/collection", 2.0, 1.0, 0.1, 2, 429))
    return log


def test_the_summary_keeps_a_day_and_finds_the_slow_pages() -> None:
    now = time.time()
    d = _log(now).summary(now)
    assert d["views"] == 22 and d["slow"] == 2
    routes = {r["route"]: r for r in d["routes"]}
    assert "/forum" not in routes  # past the 24 h
    assert routes["/users/{username}"]["views"] == 20 and routes["/users/{username}"]["max"] == 17.3
    # the most slow views first, then the slowest: one slow view in twenty is still listed
    assert [r["route"] for r in d["routes"]][:2] == ["/users/{username}", "/decks/{deck_id}"]
    assert d["failures"] == 2


def test_the_card_says_why_each_slow_page_was_slow() -> None:
    now = time.time()
    card = _speed_card(_log(now).summary(now))
    assert "<h2 id='speed-h'>Speed, last 24 h</h2>" in card
    assert "22, 2 slower than 3 s" in card
    assert "/users/{username}" in card and "waiting for a turn with Archidekt" in card
    assert "/decks/{deck_id}" in card and "mostly Archidekt answering slowly" in card
    assert "Archidekt failed or asked to slow down 2 times" in card
    assert "a restart starts it again" in card
    empty = _speed_card(SpeedLog().summary())
    assert "No page views yet." in empty


async def test_the_overview_shows_it_with_page_patterns_only(gw: Harness, monkeypatch) -> None:  # noqa: F811
    monkeypatch.setattr(timing, "speed_log", SpeedLog())
    from mtg_gateway import admin

    log = SpeedLog()
    monkeypatch.setattr(admin, "speed_log", log)
    b = await admin_browser(gw)
    try:
        log.add(PageTime(time.time(), "/users/{username}", 5.0, 4.9, 4.0, 0, 200))
        r = await b.http.get("/admin", headers=NAVIGATE)
        assert r.status_code == 200 and "Speed, last 24 h" in r.text
        assert "/users/{username}" in r.text
        assert "shared with the same read under way" in r.text
    finally:
        await b.aclose()


async def test_every_page_view_is_counted_by_its_pattern(gw: Harness, monkeypatch) -> None:  # noqa: F811
    log = SpeedLog()
    monkeypatch.setattr(timing, "speed_log", log)
    b = await admin_browser(gw)
    try:
        await b.http.get("/admin/users", headers=NAVIGATE)
        await b.http.get("/static/feedback.js")  # a file, not a page
        routes = [r.route for r in log.rows()]
        assert "/admin/users" in routes and not any(r.startswith("/static") for r in routes)
    finally:
        await b.aclose()
