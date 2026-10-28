"""The Watchlist's rows (0.7.46): a long list grows with Load more instead of Newer and Older pages
on a list sorted by name or price; a price alert opens that card's row, wherever it sits in the
list; and one watched row Scryfall cannot be asked about (no name, no printing) is skipped, so the
day's price check still runs for everyone."""

from __future__ import annotations

import json
import logging

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
    assert stack.ntfy.messages[-1]["click"] == f"https://mtg.test/watchlist?open={wid}#wr-{wid}"


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
