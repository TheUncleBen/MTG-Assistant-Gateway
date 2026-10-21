"""The Watchlist as a dashboard: widgets, the "Watch a card" panel, chips with
counts, search, finish filter and sort, the cards table with pictures, changes as an arrow and a
word, alerts as blue labels, one row opened in place with "30 days" and "Full history (N days)",
red-outline Remove and the plain "Watch deck"; the JSON unwatch behind Card search's Undo, and the
watch call taking a printing."""

from __future__ import annotations

import calendar
import json
import re
import time

from mtg_gateway import prices
from mtg_gateway.watchlist_page import Query, alert_text, change_html, day_text, watchlist_page_html

from .conftest import Stack, stack_with
from .test_watchlist import Clock, _browser, _options, _watch

stack_options = stack_with(_options())
NOW = calendar.timegm((2026, 10, 19, 12, 0, 0))
TODAY = time.strftime("%Y-%m-%d", time.gmtime(NOW))


def _hist(p0: float, days: int, step: float) -> list[dict]:
    first = NOW - (days - 1) * 86400
    return [
        {"day": time.strftime("%Y-%m-%d", time.gmtime(first + i * 86400)), "price": round(p0 + step * i, 2)}
        for i in range(days)
    ]


def _card(wid: str, name: str, *, price: float, d7: float | None, alerts=None, finish="nonfoil", days=45):
    return {
        "watch_id": wid,
        "name": name,
        "set": "mh2" if wid == "w_a" else None,
        "collector_number": "12" if wid == "w_a" else None,
        "finish": finish,
        "currency": "usd",
        "price": price,
        "day": TODAY,
        "change_1d_pct": 0.5,
        "change_7d_pct": d7,
        "change_30d_pct": -5.1,
        "alerts": alerts or {},
        "history": _hist(price - 4, days, 0.1),
        "priced": True,
    }


DATA = {
    "cards": [
        _card("w_a", "Esper Sentinel", price=13.88, d7=-2.4),
        _card("w_b", "Mana Crypt", price=102.35, d7=-8.5, alerts={"below": 150.0}),
        _card("w_c", "Rhystic Study", price=32.10, d7=-0.3, alerts={"above": 45.0, "below": 30.0}),
        _card("w_d", "Smothering Tithe", price=31.81, d7=9.3, alerts={"pct": 10.0}, finish="foil", days=20),
    ],
    "decks": [
        {
            "deck_id": "42",
            "name": "Meren Graveyard Value",
            "cards": 100,
            "currency": "usd",
            "price": 444.14,
            "day": TODAY,
            "change_1d_pct": -0.3,
            "change_7d_pct": -0.5,
            "change_30d_pct": 4.0,
            "history": _hist(420, 40, 0.5),
            "refreshed_at": NOW - 3600,
        }
    ],
    "limits": {"cards": 200, "decks": 10},
    "checked_daily_at_utc": "06:00",
}


def _page(**q) -> str:
    return watchlist_page_html(
        DATA, {"w_a": "00000000-0000-0000-0000-000000000000"}, "tok", Query(**q), now=NOW
    )


def test_words_for_changes_alerts_and_days() -> None:
    assert change_html(9.3) == "<span class='dl up'><span aria-hidden='true'>↑</span>up 9.3%</span>"
    assert change_html(-2.4) == "<span class='dl down'><span aria-hidden='true'>↓</span>down 2.4%</span>"
    assert change_html(0) == "<span class='dl'>no change</span>"
    assert alert_text({"pct": 10.0}, "usd") == "Moves more than 10%"
    assert alert_text({"below": 150.0}, "usd") == "Below $150.00"
    assert alert_text({"above": 45.0, "below": 30.0}, "usd") == "Above $45.00 · below $30.00"
    assert day_text(TODAY, TODAY) == "today" and day_text("2026-10-11", TODAY) == "Oct 11"
    assert day_text("2025-10-11", TODAY) == "Oct 11, 2025"


def test_widgets_and_the_watch_a_card_panel() -> None:
    page = _page()
    widgets = page.split("aria-label='At a glance'>")[1].split("</section>")[0]
    assert "4<small>of 200</small>" in widgets and "Together $180.14 today" in widgets
    assert "3<small>cards have one</small>" in widgets
    assert "Mana Crypt is below your $150.00 · checked today at 06:00 UTC" in widgets
    assert "↑9.3%<small>Smothering Tithe</small>" in widgets and "Down most: Mana Crypt, down 8.5%" in widgets
    assert "1<small>of 10</small>" in widgets and "Meren Graveyard Value up 4% in 30 days" in widgets
    assert (
        "class='wdg accent'" not in widgets and "class='wdg alert'" not in widgets
    )  # alerts are information
    assert "<h2 id='w-add-h'>Watch a card</h2>" in page
    assert "You can also watch a card from Card search: press and hold it, or right-click it." in page
    # one orange button: Watch card; Watch deck is plain
    assert page.count("btn-primary") == 1 and "<button class='btn-primary' type='submit'>Watch card" in page
    assert "<button class='btn-plain' type='submit'>Watch deck</button>" in page
    assert "Last check today at 06:00 UTC" in page


def test_a_watched_deck_with_no_name_is_an_unnamed_deck() -> None:
    unnamed = {**DATA["decks"][0], "deck_id": "4242", "name": ""}
    data = {**DATA, "decks": [unnamed]}
    page = watchlist_page_html(data, {}, "tok", Query(), now=NOW)
    assert ">Unnamed deck</a>" in page and "Value of Unnamed deck" in page
    assert "Deck 4242" not in page and ">4242<" not in page


def test_rows_show_picture_finish_changes_and_alerts() -> None:
    page = _page()
    assert "https://cards.scryfall.io/small/front/0/0/" in page  # Esper Sentinel's picture
    assert "<span class='thumb' aria-hidden='true'><span>Mana Crypt</span></span>" in page  # none: a name box
    assert "MH2 12 · Non-foil" in page and "any printing · Foil" in page
    assert "<span class='none'>No alert</span>" in page
    assert "class='ui-lbl blue'" in page and "Above $45.00 · below $30.00</span>" in page
    assert "7 days <span class='dl down'>" in page and "<span class='kv'>in 7 days</span>" in page
    assert len(re.findall(r"<div class='wx' id='wx-[\w]+' hidden>", page)) == 5  # every panel closed
    assert page.count(">Details</a>") == 5
    assert "1–4 of 4" in page and "1–1 of 1" in page


def test_the_card_size_switch_and_room_for_the_row_menu_beside_details() -> None:
    """R-175 (2026-10-21): the card size switch sits above the table; the action column is wide
    enough for Details and the row's ⋮ side by side, so the ⋮ no longer wraps under Details."""
    from mtg_gateway import watchlist_page as wp

    page = _page()
    assert "class='cardsize w-size'" in page and "data-card-size-set='m'" in page
    assert wp.TABLE_COLS.split()[-1] == "8.5rem" and wp.TABLE_MD.split()[-1] == "8.5rem"
    assert (
        ".watch-page .w-t .act{display:inline-flex;align-items:center;gap:.4rem;flex-wrap:nowrap"
        in wp.WATCH_PAGE_CSS
    )


def test_chips_search_finish_and_sort() -> None:
    def rows(page: str) -> list[str]:
        return re.findall(r"class='wcard' data-card='([^']+)'", page)

    page = _page()
    assert "aria-current='true'>All <span class='n'>4</span>" in page
    assert "With an alert <span class='n'>3</span>" in page
    assert (
        "Up in 7 days <span class='n'>1</span>" in page and "Down in 7 days <span class='n'>3</span>" in page
    )
    assert rows(page) == ["Esper Sentinel", "Mana Crypt", "Rhystic Study", "Smothering Tithe"]  # by name
    assert rows(_page(show="alert")) == ["Mana Crypt", "Rhystic Study", "Smothering Tithe"]
    assert rows(_page(show="up")) == ["Smothering Tithe"]
    assert rows(_page(q="crypt")) == ["Mana Crypt"]
    assert rows(_page(finish="foil")) == ["Smothering Tithe"]
    assert rows(_page(sort="price"))[0] == "Mana Crypt"
    assert rows(_page(sort="d7"))[:2] == ["Smothering Tithe", "Mana Crypt"]  # the biggest move first
    assert "No cards match. Clear the search or the filters to see them all." in _page(q="nothing")


def test_an_opened_row_has_its_chart_range_alert_and_remove() -> None:
    page = _page(open="w_b")
    panel = page.split("id='wx-w_b'>")[1].split("<div class='tr")[0]
    assert "aria-expanded='true'" in page and ">Close</a>" in page
    assert "<h3>Price, last 30 days</h3>" in panel
    assert ">30 days</button>" in panel and ">Full history (45 days)</button>" in panel
    assert "class='pcl'" in panel and "<div class='pc-x' aria-hidden='true'>" in panel
    assert "Price history table" in panel and panel.count("<tr><th scope='row'>") == 30
    assert "Price alert <span class='ui-lbl blue'>" in panel and ">On</span>" in panel
    assert "<button class='btn-plain' type='submit'>Save alert</button>" in panel
    assert "class='btn btn-plain' href='/cards?q=Mana%20Crypt'>Open card</a>" in panel
    assert "<button class='btn-outline-danger' type='submit' form='rm-w_b'" in panel
    # 20 days kept: no Full history button
    tithe = _page(open="w_d").split("id='wx-w_d'>")[1]
    assert "Full history" not in tithe.split("</form>")[0]
    deck = _page(open="d42").split("id='wx-d42'>")[1]
    assert "<h3>Value, last 30 days</h3>" in deck and "Full history (40 days)" in deck
    assert "data-full='" in deck and ">Open deck</a>" in deck and "btn-outline-danger" in deck


def test_details_keeps_the_chip_search_filters_and_page() -> None:
    """Without the script, Details is a link to the page with that row open; it carries the
    list's query state, so the filters and the page survive the open (as on History)."""
    page = _page(show="alert", q="a", finish="nonfoil", sort="price", offset=0)
    assert "href='/watchlist?show=alert&q=a&finish=nonfoil&sort=price&open=w_b#wr-w_b'" in page
    paged = _page(offset=25)
    assert "href='/watchlist?offset=25&open=d42#wr-d42'" in paged
    plain = _page()
    assert "href='/watchlist?open=w_a#wr-w_a'" in plain


async def test_unwatch_api_is_the_undo_and_the_watch_api_takes_a_printing(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    try:
        csrf = await b.csrf("/watchlist")
        hdr = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
        r = await b.http.post(
            "/watchlist/api/watch",
            content=json.dumps({"name": "Sol Ring", "set_code": "frc", "collector_number": "21"}),
            headers=hdr,
        )
        assert r.status_code == 200, r.text
        watch = r.json()["watch"]
        assert watch["set"] == "frc" and watch["collector_number"] == "21"
        wid = watch["watch_id"]
        body = json.dumps({"watch_id": wid})
        # the form token, as the watch call
        r = await b.http.post(
            "/watchlist/api/unwatch", content=body, headers={"Content-Type": "application/json"}
        )
        assert r.status_code == 403
        # a name is refused: it would stop every printing
        r = await b.http.post(
            "/watchlist/api/unwatch", content=json.dumps({"watch_id": "Sol Ring"}), headers=hdr
        )
        assert r.status_code == 400
        r = await b.http.post("/watchlist/api/unwatch", content=body, headers=hdr)
        assert r.status_code == 200 and r.json() == {"ok": True, "removed": ["Sol Ring"]}
        assert stack.gw.prices.watched_cards(stack.gw.db._one("SELECT sub FROM users", ())["sub"]) == []
        r = await b.http.post("/watchlist/api/unwatch", content=body, headers=hdr)
        assert r.status_code == 404
    finally:
        await b.aclose()


async def test_the_page_reads_a_year_and_shows_the_new_layout(stack: Stack, monkeypatch) -> None:
    clock = Clock(monkeypatch)
    b = await _browser(stack)
    try:
        assert (await _watch(b, name="sol ring", set_code="FRC", collector_number="21")).status_code == 303
        page = (await b.http.get("/watchlist")).text
        assert "class='wrap ui-wide'" in page and "/static/watchlist.js" in page
        assert "class='ui-table w-t'" in page and "FRC 21 · Non-foil" in page
        assert "<h2>Cards <span class='ui-sub'>1</span></h2>" in page
        assert "$1.28<small>today</small>" in page and clock.day
        assert prices.money(1.28) == "$1.28"
    finally:
        await b.aclose()
