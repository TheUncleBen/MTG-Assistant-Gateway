"""Stop watching is reversible (0.7.46 audit QG-09, QOL-01): the bulk bar's Stop watching and
a row's go at once and the notice offers Undo for a while; Undo brings the cards back with
their alerts, their list and the price they were added at (the same rows, so their price history
stays theirs). A token is the member's own and is spent once."""

from __future__ import annotations

import re
import time

from mtg_gateway import prices

from .conftest import Stack, stack_with
from .test_watchlist import Clock, _browser, _options, _watch
from .test_watchlist_wants import SUB, _ids

stack_options = stack_with(_options())


async def test_stop_watching_can_be_undone_from_the_notice(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    svc = stack.gw.prices
    b = await _browser(stack, link=True)
    try:
        await _watch(b, name="Sol Ring", set_code="frc", collector_number="21")
        await _watch(b, name="Sol Talisman", finish="foil")
        ids = _ids(stack)
        both = [ids["Sol Ring"], ids["Sol Talisman"]]
        csrf = await b.csrf("/watchlist")
        svc.set_alerts_many(SUB, both, alert_pct=7, alert_drop_pct=None, alert_rise_pct=None)
        svc.move_to_list(SUB, both, "Trade for")
        before = {w["id"]: dict(w) for w in svc.watched_cards(SUB)}

        r = await b.http.post(
            "/watchlist", data={"csrf": csrf, "action": "bulk", "do": "remove", "watch_id": both}
        )
        assert r.status_code == 303, r.text
        where = r.headers["location"]
        m = re.fullmatch(r"/watchlist\?ok=removed&w=[^&]+&undo=([A-Za-z0-9_-]{16,})", where)
        assert m, where
        token = m.group(1)
        assert svc.watched_cards(SUB) == []
        page = (await b.http.get(where)).text
        assert "Sol Ring and Sol Talisman are no longer watched." in page
        assert "name='token' value='" + token + "'" in page and ">Undo</button>" in page
        # the member who stopped them is the only one who can bring them back

        r = await b.http.post("/watchlist", data={"csrf": csrf, "action": "rewatch", "token": token})
        assert r.status_code == 303 and r.headers["location"].startswith("/watchlist?ok=rewatched&w="), r.text
        after = {w["id"]: dict(w) for w in svc.watched_cards(SUB)}
        assert after == before  # the same rows: alerts, list and added price included
        page = (await b.http.get(r.headers["location"])).text
        assert "Sol Ring and Sol Talisman are watched again." in page
        # spent: a second press says so, plainly
        r = await b.http.post("/watchlist", data={"csrf": csrf, "action": "rewatch", "token": token})
        assert r.status_code == 404 and "That Undo is no longer available." in r.text
    finally:
        await b.aclose()


async def test_a_single_rows_stop_watching_is_undone_the_same_way(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    svc = stack.gw.prices
    b = await _browser(stack, link=True)
    try:
        await _watch(b, name="Sol Ring", set_code="frc", collector_number="21")
        wid = _ids(stack)["Sol Ring"]
        csrf = await b.csrf("/watchlist")
        r = await b.http.post(
            "/watchlist", data={"csrf": csrf, "action": "bulk", "do": "remove", "watch_id": [wid]}
        )
        assert r.status_code == 303 and "&undo=" in r.headers["location"]
        page = (await b.http.get(r.headers["location"])).text
        assert "Sol Ring is no longer watched." in page and ">Undo</button>" in page
        token = re.search(r"name='token' value='([^']+)'", page).group(1)
        # the kept rows are forgotten after a while
        svc.forget_stopped(now=time.time() + prices.STOPPED_KEEP + 1)
        r = await b.http.post("/watchlist", data={"csrf": csrf, "action": "rewatch", "token": token})
        assert r.status_code == 404 and "That Undo is no longer available." in r.text
        assert svc.watched_cards(SUB) == []
    finally:
        await b.aclose()
