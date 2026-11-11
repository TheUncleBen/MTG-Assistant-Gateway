"""The Watchlist's rows (0.7.46): a long list grows with Load more instead of Newer and Older pages
on a list sorted by name or price; a price alert opens that card's row, wherever it sits in the
list; and one watched row Scryfall cannot be asked about (no name, no printing) is skipped, so the
day's price check still runs for everyone."""

from __future__ import annotations

import json
import logging
import math
import re
import time

from mtg_gateway import prices

from .conftest import Stack
from .test_watchlist import SOL_RING, Clock, _browser, _opt_in, _post, _watch, stack_options  # noqa: F401

PAGE = 25


def _many(stack: Stack, n: int) -> list[str]:
    """``n`` watched cards for user-1 straight into the table, ids w0..w{n-1}, named in order."""
    ids = []
    with stack.gw.db.tx() as c:
        for i in range(n):
            c.execute(
                "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, created_at) "
                "VALUES (?, 'user-1', ?, '', '', 'nonfoil', 0)",
                (f"w{i:03d}", f"Card {i:03d}"),
            )
            ids.append(f"w{i:03d}")
    return ids


async def test_a_long_watchlist_grows_with_load_more(stack: Stack) -> None:
    b = await _browser(stack)
    try:
        _many(stack, 60)
        page = (await b.http.get("/watchlist")).text
        assert "Newer" not in page and "Older" not in page and "rel='prev'" not in page
        assert page.count("id='wr-w") == PAGE
        more = page.split("<div class='loadmore'>")[1].split("</div>")[0]
        assert "Load more cards" in more and f"{PAGE} of 60" in more
        href = more.split("href='")[1].split("'")[0].replace("&amp;", "&")
        assert href.startswith("/watchlist?") and href.endswith(f"#wr-w{PAGE:03d}"), href
        page = (await b.http.get(href.split("#")[0])).text
        assert page.count("id='wr-w") == 2 * PAGE and "50 of 60" in page
        page = (await b.http.get("/watchlist?upto=75")).text
        assert page.count("id='wr-w") == 60 and "All 60 cards shown." in page
        # the decks' list has no dead pager under it
        assert "1–1 of 1" not in page and "of 0" not in page
    finally:
        await b.aclose()


async def test_an_alert_opens_its_cards_row_even_past_the_first_screen(stack: Stack, monkeypatch) -> None:
    clock = Clock(monkeypatch)
    b = await _browser(stack)
    try:
        _many(stack, 40)
        page = (await b.http.get("/watchlist?open=w033")).text
        assert "id='wr-w033'" in page and "<div class='tr open' id='wr-w033'>" in page
        await _opt_in(b)
        assert (await _watch(b, name="Sol Ring", set_code="frc", collector_number="21")).status_code == 303
        wid = next(w for w in stack.gw.prices.watched_cards("user-1") if w["name"] == SOL_RING)["id"]
        r = await _post(b, "/watchlist", {"action": "alerts", "watch_id": wid, "alert_above": "1.5"})
        assert r.status_code == 303
    finally:
        await b.aclose()
    sol = next(c for c in stack.sf.cards if c["name"] == SOL_RING and c["set"] == "frc")
    clock.next_day()
    stack.sf.prices[sol["id"]] = {"usd": "1.60"}
    assert (await stack.gw.prices.run_daily())["alerts"] == 1
    # the push opens through the Alerts page (marked read there), then the card's own row
    click = stack.ntfy.messages[-1]["click"]
    assert click.startswith("https://mtg.test/notifications/open/")
    aid = int(click.rsplit("/", 1)[1])
    assert stack.gw.db.gateway_alert("user-1", aid)["path"] == f"/watchlist?open={wid}#wr-{wid}"


async def test_a_watched_row_with_no_name_does_not_stop_the_daily_check(
    stack: Stack, monkeypatch, caplog
) -> None:
    clock = Clock(monkeypatch)
    b = await _browser(stack)
    try:
        assert (await _watch(b, name="Sol Ring", set_code="frc", collector_number="21")).status_code == 303
    finally:
        await b.aclose()
    with stack.gw.db.tx() as c:
        c.execute(
            "INSERT INTO watch_decks (sub, deck_id, deck_name, cards_json, refreshed_at, created_at) "
            "VALUES ('user-2', '999', 'Broken', ?, ?, 0)",
            (json.dumps([{"qty": 1}, {"qty": 2, "set": "cmr"}]), int(prices.now())),  # read today
        )
    clock.next_day()  # Sol Ring was priced when it was watched: the next day's check prices it
    with caplog.at_level(logging.WARNING):
        out = await stack.gw.prices.run_daily()
    assert out["watched"] and out["priced"] >= 1, out
    assert any("skipped" in r.getMessage() for r in caplog.records)


# -- 0.7.46 full review: speed at the cap, Load more as a fragment, natural names (F7, F8) --------
def _priced_many(stack: Stack, n: int, days: int = 31) -> None:
    """``n`` watched printings for user-1 with ``days`` of prices each, named "Card N"."""
    with stack.gw.db.tx() as c:
        for i in range(n):
            sfid = f"s-{i:05d}"
            c.execute(
                "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, scryfall_id, "
                "alert_pct, created_at) VALUES (?, 'user-1', ?, 'abc', ?, 'nonfoil', ?, ?, 0)",
                (f"w{i:05d}", f"Card {i}", str(i), sfid, 10 if i % 7 == 0 else None),
            )
            c.executemany(
                "INSERT INTO price_points (scryfall_id, day, usd) VALUES (?, date('now', ?), ?)",
                [(sfid, f"-{d} day", round(2 + math.sin((d + i) / 3), 2)) for d in range(days)],
            )


async def test_names_sort_naturally(stack: Stack) -> None:
    """ "Card 2" before "Card 10" before "Card 11" before "Card 109": numbers in a name sort as
    numbers, on the cards, on the decks and in the names API."""
    b = await _browser(stack)
    try:
        with stack.gw.db.tx() as c:
            for i, n in enumerate((109, 11, 2, 10)):
                c.execute(
                    "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, created_at) "
                    "VALUES (?, 'user-1', ?, '', '', 'nonfoil', 0)",
                    (f"w{i}", f"Card {n}"),
                )
                c.execute(
                    "INSERT INTO watch_decks (sub, deck_id, deck_name, cards_json, refreshed_at, created_at) "
                    "VALUES ('user-1', ?, ?, '[]', NULL, 0)",
                    (str(n), f"Deck {n}"),
                )
        page = (await b.http.get("/watchlist")).text
        cards = re.findall(r"id='wr-(w\d)'", page)
        assert cards == ["w2", "w3", "w1", "w0"], cards  # 2, 10, 11, 109
        decks = re.findall(r"id='wr-d(\d+)'", page)
        assert decks == ["2", "10", "11", "109"], decks
        names = (await b.http.get("/watchlist/api/names")).json()["names"]
        assert names == ["Card 2", "Card 10", "Card 11", "Card 109"]
    finally:
        await b.aclose()


async def test_load_more_brings_only_the_new_rows(stack: Stack) -> None:
    """Load more (static/watchlist.js) asks for the rows it lacks as a fragment: ``part=cards``
    with ``from`` the rows on screen gives those rows and the next Load more, nothing else."""
    b = await _browser(stack)
    try:
        _many(stack, 60)
        r = await b.http.get("/watchlist?upto=50&from=25&part=cards")
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
        frag = r.text
        rows = re.findall(r"id='wr-(w\d{3})'", frag)
        assert rows == [f"w{i:03d}" for i in range(25, 50)], rows
        assert "<html" not in frag and "ui-widgets" not in frag and "Watch a card" not in frag
        assert "Load more cards" in frag and "50 of 60" in frag and "upto=75" in frag
        assert frag.count("class='wx'") == 25  # each row with its Details, as the page's own
        frag = (await b.http.get("/watchlist?upto=75&from=50&part=cards")).text
        assert re.findall(r"id='wr-(w\d{3})'", frag) == [f"w{i:03d}" for i in range(50, 60)]
        assert "All 60 cards shown." in frag
        frag = (await b.http.get("/watchlist?upto=100&from=75&part=cards")).text
        assert "id='wr-" not in frag and "All 60 cards shown." in frag
        with stack.gw.db.tx() as c:
            for i in range(30):
                c.execute(
                    "INSERT INTO watch_decks (sub, deck_id, deck_name, cards_json, refreshed_at, created_at) "
                    "VALUES ('user-1', ?, ?, '[]', NULL, 0)",
                    (str(1000 + i), f"Deck {i:02d}"),
                )
        frag = (await b.http.get("/watchlist?dupto=50&from=25&part=decks")).text
        assert re.findall(r"id='wr-d(\d+)'", frag) == [str(1000 + i) for i in range(25, 30)]
        assert "All 30 decks shown." in frag
    finally:
        await b.aclose()


async def test_an_alert_link_past_the_first_screen_finds_the_card_by_its_name(stack: Stack) -> None:
    """An alert's link (``open=``) to a row past the first screenful searches the list for that
    card's name instead of drawing every row before it (at 5,000 cards that was the whole list)."""
    b = await _browser(stack)
    try:
        _many(stack, 60)
        page = (await b.http.get("/watchlist?open=w058")).text
        assert "<div class='tr open' id='wr-w058'>" in page
        assert page.count("id='wr-w") == 1 and "value='Card 058'" in page
        assert "Load more cards" not in page
        assert "<span class='n'>60</span>" in page  # the chips still count the whole list
    finally:
        await b.aclose()


async def test_the_watchlist_is_quick_at_five_thousand_cards(stack: Stack) -> None:
    """At the cap (5,000 cards, 31 days of prices each) the rows are worked out once and kept
    until something changes, so the page and its fragments come back well under 300 ms."""
    from mtg_gateway import watchlist
    from mtg_gateway.watchlist_page import Query

    _priced_many(stack, 5000)
    gw = stack.gw
    svc = gw.prices
    rows = svc.card_summaries("user-1")
    assert len(rows) == 5000 and rows[0]["price"] is not None and rows[0]["change_7d_pct"] is not None

    def quickest(fn):  # the best of three, so a busy test machine does not count against the page
        best, out = 1e9, None
        for _ in range(3):
            t = time.perf_counter()
            out = fn()
            best = min(best, time.perf_counter() - t)
        return best, out

    kept, again = quickest(lambda: svc.card_summaries("user-1"))
    assert again == rows and kept < 0.1, kept
    took, body = quickest(lambda: watchlist.watchlist_body(gw, "user-1", "tok", Query()))
    assert body.count("class='wx'") == 25 and took < 0.3, took
    took, frag = quickest(
        lambda: watchlist.watchlist_body(gw, "user-1", "tok", Query(upto=50), part="cards", start=25)
    )
    assert frag.count("class='wx'") == 25 and took < 0.3, took
    # the rows follow every change: an alert set, a card removed, and a change made behind the
    # service's back
    svc.set_alerts("user-1", "w00001", 5.0, None, None)
    one = next(v for v in svc.card_summaries("user-1") if v["watch_id"] == "w00001")
    assert one["alerts"] == {"pct": 5.0}
    svc.unwatch_card("user-1", "w00001")
    assert all(v["watch_id"] != "w00001" for v in svc.card_summaries("user-1"))
    with gw.db.tx() as c:
        c.execute("DELETE FROM watch_cards WHERE sub = 'user-1'")
    assert svc.card_summaries("user-1") == []


async def test_the_names_api_answers_not_modified_to_a_matching_etag(stack: Stack) -> None:
    """The names the card menus ask for once a minute come with an ETag: an unchanged list is a
    304 with no body."""
    b = await _browser(stack)
    try:
        _many(stack, 3)
        r = await b.http.get("/watchlist/api/names")
        assert r.status_code == 200 and r.headers.get("etag")
        assert "no-store" not in r.headers.get("cache-control", "")
        r2 = await b.http.get("/watchlist/api/names", headers={"If-None-Match": r.headers["etag"]})
        assert r2.status_code == 304
        with stack.gw.db.tx() as c:
            c.execute("DELETE FROM watch_cards WHERE id = 'w000'")
        r3 = await b.http.get("/watchlist/api/names", headers={"If-None-Match": r.headers["etag"]})
        assert r3.status_code == 200 and r3.json()["names"] == ["Card 001", "Card 002"]
    finally:
        await b.aclose()
