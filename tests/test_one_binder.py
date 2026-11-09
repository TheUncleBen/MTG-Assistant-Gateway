"""One Binder (0.7.47, the owner's approved mockup round 1): the alert rules (10% under / 20% over
the price when added, the $0.50 floor, cards that follow the member's defaults), no watch limit by
default, lists that hold owned and wanted cards, and the one list of rows the Binder page draws."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from mtg_gateway import binder_library, prefs, prices
from mtg_gateway import db as db_module
from mtg_gateway.prices import alert_band

from .conftest import Stack
from .test_watchlist import Clock, _browser, _post, stack_options  # noqa: F401

SUB = "user-1"


# -- the alert rules -------------------------------------------------------------------------------


def test_the_band_is_the_percentage_or_the_floor_whichever_is_further() -> None:
    assert alert_band(10.0, 10, 20, 0.5) == (9.0, 12.0)  # the percentages are further than $0.50
    assert alert_band(2.0, 10, 20, 0.5) == (1.5, 2.5)  # a cheap card: the floor is further
    assert alert_band(2.0, 10, None, 0) == (1.8, None)
    assert alert_band(None, 10, 20, 0.5) == (None, None)  # no price when added: no band
    assert alert_band(0.3, 10, 20, 0.5) == (None, 0.8)  # never an alert at $0 or below


def test_the_shipped_defaults_are_the_owners_choice() -> None:
    assert prices.DEFAULT_ALERTS == {"drop": 10.0, "rise": 20.0, "floor": 0.5}
    assert prefs.valid(prefs.WATCH_ALERTS, {"drop": 5, "floor": 1.25})
    assert not prefs.valid(prefs.WATCH_ALERTS, {"floor": 0})


def _watch_row(
    c: sqlite3.Connection, wid: str, sid: str, *, drop=None, rise=None, sub: str = SUB, **more
) -> None:
    c.execute(
        "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, scryfall_id, "
        "alert_drop_pct, alert_rise_pct, added_price, added_currency, created_at, list_name) "
        "VALUES (?, ?, ?, 'abc', ?, 'nonfoil', ?, ?, ?, ?, 'usd', 0, ?)",
        (
            wid,
            sub,
            more.get("name", f"Card {wid}"),
            more.get("cn", "1"),
            sid,
            drop,
            rise,
            more.get("added", 10.0),
            more.get("list", ""),
        ),
    )


def test_step_23_marks_the_watches_that_follow_the_defaults(tmp_path: Path) -> None:
    """A watch with 0.7.46's shipped alert (10% under) follows the defaults and gains the new 20%
    over; one that equals the member's saved defaults follows them; one of its own keeps its own."""
    path = tmp_path / "g.db"
    d = db_module.Database(path)
    try:
        with d.tx() as c:
            for sub in ("a", "b"):
                c.execute("INSERT INTO users (sub, first_login_at, last_login_at) VALUES (?, 0, 0)", (sub,))
        d.set_pref("b", prefs.WATCH_ALERTS, {"drop": 15.0})
    finally:
        d.close()
    # back to 0.7.46's schema, with its rows
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 22")
    con.execute("DROP TABLE list_cards")
    con.execute("ALTER TABLE watch_cards DROP COLUMN uses_defaults")
    _watch_row(con, "w1", "s1", drop=10.0, sub="a")  # the old shipped default
    _watch_row(con, "w2", "s2", drop=25.0, sub="a")  # its own
    _watch_row(con, "w3", "s3", drop=15.0, sub="b")  # b's saved defaults
    _watch_row(con, "w4", "s4", drop=10.0, sub="b")  # not b's defaults
    con.commit()
    con.close()
    d = db_module.Database(path)
    try:
        assert d.schema_version == db_module.SCHEMA_VERSION == 23
        got = {
            r[0]: tuple(r[1:])
            for r in d._conn.execute(
                "SELECT id, uses_defaults, alert_drop_pct, alert_rise_pct FROM watch_cards"
            )
        }
        assert got == {
            "w1": (1, 10.0, 20.0),
            "w2": (0, 25.0, None),
            "w3": (1, 15.0, None),
            "w4": (0, 10.0, None),
        }
        assert ("list_cards", "sub") in db_module.MEMBER_TABLES
    finally:
        d.close()


async def test_saved_defaults_change_the_cards_that_follow_them(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    try:
        with stack.gw.db.tx() as c:
            _watch_row(c, "w1", "s1", drop=10.0, rise=20.0)
            _watch_row(c, "w2", "s2", drop=30.0)
            c.execute("UPDATE watch_cards SET uses_defaults = 1 WHERE id = 'w1'")
        r = await _post(
            b, "/watchlist", {"action": "defaults", "alert_drop": "5", "alert_rise": "50", "alert_floor": "1"}
        )
        assert r.status_code == 303, r.text
        assert prefs.alert_defaults(stack.gw.db, SUB) == {"drop": 5.0, "rise": 50.0, "floor": 1.0}
        by = {w["id"]: w for w in stack.gw.prices.watched_cards(SUB)}
        assert (by["w1"]["alert_drop_pct"], by["w1"]["alert_rise_pct"]) == (5.0, 50.0)
        assert (by["w2"]["alert_drop_pct"], by["w2"]["alert_rise_pct"]) == (30.0, None)  # its own: kept
        # a card's own alert stops it following; "use my defaults" brings it back
        svc = stack.gw.prices
        svc.set_alerts_many(SUB, ["w1"], drop=12)
        assert svc.defaults_split(SUB) == (0, 2)
        assert svc.use_defaults_many(SUB, ["w1", "w2"]) == 2
        assert svc.defaults_split(SUB) == (2, 0)
        by = {w["id"]: w for w in svc.watched_cards(SUB)}
        assert (by["w2"]["alert_drop_pct"], by["w2"]["alert_rise_pct"]) == (5.0, 50.0)
        # a floor of 0 is no floor
        r = await _post(b, "/watchlist", {"action": "defaults", "alert_drop": "5", "alert_floor": "0"})
        assert r.status_code == 303 and prefs.alert_defaults(stack.gw.db, SUB) == {"drop": 5.0}
    finally:
        await b.aclose()


async def test_the_floor_keeps_a_cheap_cards_small_moves_quiet(stack: Stack, monkeypatch) -> None:
    """A one-day move needs its percentage and the floor; a crossing of a price the member typed
    (above or below) is not held back by it."""
    clock = Clock(monkeypatch)
    svc = stack.gw.prices
    with stack.gw.db.tx() as c:
        c.execute("INSERT OR IGNORE INTO users (sub, first_login_at, last_login_at) VALUES (?, 0, 0)", (SUB,))
        c.execute(
            "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, scryfall_id, "
            "alert_pct, created_at) VALUES ('w1', ?, 'Cheap', 'abc', '1', 'nonfoil', 's1', 10, 0)",
            (SUB,),
        )
    today = prices.day_of(prices.now())
    with stack.gw.db.tx() as c:
        c.execute(
            "INSERT INTO price_points (scryfall_id, day, usd) VALUES ('s1', ?, 1.00)",
            (prices.days_before(today, 1),),
        )
        c.execute("INSERT INTO price_points (scryfall_id, day, usd) VALUES ('s1', ?, 1.30)", (today,))
    assert svc._alerts_due(today) == []  # +30% but $0.30
    clock.next_day()
    day2 = prices.day_of(prices.now())
    with stack.gw.db.tx() as c:
        c.execute("INSERT INTO price_points (scryfall_id, day, usd) VALUES ('s1', ?, 1.90)", (day2,))
    [(sub, _title, message, _path)] = svc._alerts_due(day2)
    assert sub == SUB and message.startswith("Cheap rose 46.2%")


# -- no limit --------------------------------------------------------------------------------------


async def test_zero_is_no_limit_and_an_owner_can_still_set_one(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    svc = stack.gw.prices
    assert svc._limit("watch_max_cards") == 0 and svc.limits()["cards"] is None
    b = await _browser(stack)
    try:
        with stack.gw.db.tx() as c:
            for i in range(3):
                _watch_row(c, f"w{i}", f"s{i}", cn=str(i))
        assert (await svc.watch_card(SUB, "Sol Ring"))["created"]
        live = stack.gw.decks.live
        monkeypatch.setattr(live, "get", lambda k, _g=live.get: 4 if k == "watch_max_cards" else _g(k))
        with pytest.raises(Exception, match="at most 4"):
            await svc.watch_card(SUB, "Sol Talisman")
    finally:
        await b.aclose()


# -- lists hold owned and wanted cards; the rows ---------------------------------------------------


def _binder(stack: Stack, rows: list[dict]) -> None:
    stack.gw.prices.binder.replace(SUB, rows, complete=True)


def _owned(rid: int, sid: str, name: str, qty: int = 1, **more) -> dict:
    return {
        "id": rid,
        "scryfall_id": sid,
        "name": name,
        "quantity": qty,
        "finish": more.get("finish", "nonfoil"),
        "set": "abc",
        "collector_number": more.get("cn", str(rid)),
        "tags": more.get("tags", []),
        "added_at": more.get("added_at", "2026-10-01"),
    }


async def test_one_row_per_printing_owned_or_wanted(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    svc = stack.gw.prices
    try:
        with stack.gw.db.tx() as c:
            _watch_row(c, "w1", "s1", drop=10.0, rise=20.0, name="Both", list="Upgrades")
            _watch_row(c, "w2", "s2", drop=10.0, name="Wanted", cn="2")
            today = prices.day_of(prices.now())
            for sid, usd in (("s1", 8.5), ("s2", 3.0), ("s3", 1.0)):
                c.execute(
                    "INSERT INTO price_points (scryfall_id, day, usd) VALUES (?, ?, ?)", (sid, today, usd)
                )
                c.execute(
                    "INSERT INTO price_points (scryfall_id, day, usd) VALUES (?, ?, ?)",
                    (sid, prices.days_before(today, 7), usd * 2),
                )
        _binder(
            stack,
            [
                _owned(11, "s1", "Both", 2),
                _owned(12, "s1", "Both", 1),
                _owned(13, "s3", "Owned", tags=["trade"]),
            ],
        )
        binder_library.forget()
        rows = {r["name"]: r for r in binder_library.all_rows(svc, SUB)}
        assert set(rows) == {"Both", "Wanted", "Owned"}
        both = rows["Both"]
        assert both["own"] == 3 and both["watch_id"] == "w1" and both["list"] == "Upgrades"
        assert sorted(both["rids"]) == ["11 Both", "12 Both"]
        assert both["price"] == 8.5 and both["past"] is True  # under $9.00, its alert
        assert rows["Owned"]["change_7d_pct"] == -50.0 and rows["Owned"]["tags"] == ["trade"]
        assert binder_library.counts(list(rows.values())) == {"all": 3, "owned": 2, "want": 2, "alerts": 2}
        # an owned card goes into a list; a watched one moves its watch
        assert (
            svc.put_owned_in_list(
                SUB, [{"scryfall_id": "s3", "finish": "nonfoil", "name": "Owned"}], "Upgrades"
            )
            == 1
        )
        rows = {r["name"]: r for r in binder_library.all_rows(svc, SUB)}
        assert rows["Owned"]["list"] == "Upgrades"
        assert binder_library.list_counts(list(rows.values())) == {
            "Upgrades": {"all": 2, "want": 1, "owned": 2}
        }
        assert [x["count"] for x in svc.lists(SUB) if x["name"] == "Upgrades"] == [2]
        shown, seg = binder_library.view(list(rows.values()), show="owned", list_name="Upgrades")
        assert [r["name"] for r in shown] == ["Both", "Owned"] and seg == {
            "all": 2,
            "owned": 2,
            "want": 1,
            "alerts": 1,
        }
        shown, _ = binder_library.view(list(rows.values()), tag="trade")
        assert [r["name"] for r in shown] == ["Owned"]
        # watching it keeps its list; stopping keeps it there too (it is owned)
        [wid] = svc.watch_printings(
            SUB,
            [
                {
                    "scryfall_id": "s3",
                    "name": "Owned",
                    "set": "abc",
                    "collector_number": "13",
                    "finish": "nonfoil",
                }
            ],
        )
        w = next(x for x in svc.watched_cards(SUB) if x["id"] == wid)
        assert w["list_name"] == "Upgrades" and w["added_price"] == 1.0 and w["uses_defaults"] == 1
        assert (w["alert_drop_pct"], w["alert_rise_pct"]) == (10.0, 20.0)
        assert svc.owned_lists(SUB) == {}
        token, _gone = svc.stop_watching(SUB, [wid])
        assert svc.owned_lists(SUB) == {("s3", "nonfoil"): "Upgrades"}
        svc.rewatch(SUB, token)
        assert svc.owned_lists(SUB) == {}
        # rename and delete follow the owned cards too
        svc.stop_watching(SUB, [wid])
        svc.rename_list(SUB, "Upgrades", "Commander upgrades")
        assert svc.owned_lists(SUB) == {("s3", "nonfoil"): "Commander upgrades"}
        svc.delete_list(SUB, "Commander upgrades")
        assert svc.owned_lists(SUB) == {}
    finally:
        await b.aclose()


async def test_undo_puts_alerts_and_lists_back(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    svc = stack.gw.prices
    try:
        with stack.gw.db.tx() as c:
            _watch_row(c, "w1", "s1", drop=10.0, list="A")
            c.execute("UPDATE watch_cards SET uses_defaults = 1")
        before = svc.watch_state(SUB, ["w1"])
        svc.set_alerts_many(SUB, ["w1"], drop=40)
        svc.move_to_list(SUB, ["w1"], "B")
        assert svc.restore_watch_state(SUB, before) == 1
        w = svc.watched_cards(SUB)[0]
        assert (w["alert_drop_pct"], w["uses_defaults"], w["list_name"]) == (10.0, 1, "A")
        assert json.dumps(before)  # plain values, kept in memory for the Undo
    finally:
        await b.aclose()


# -- the Binder page ---------------------------------------------------------------------------------


def _seed(stack: Stack) -> None:
    with stack.gw.db.tx() as c:
        _watch_row(c, "w1", "s1", drop=10.0, rise=20.0, name="Both", list="Upgrades")
        _watch_row(c, "w2", "s2", drop=10.0, name="Wanted", cn="2")
        today = prices.day_of(prices.now())
        for sid, usd in (("s1", 8.5), ("s2", 3.0), ("s3", 1.0)):
            c.execute("INSERT INTO price_points (scryfall_id, day, usd) VALUES (?, ?, ?)", (sid, today, usd))
    _binder(stack, [_owned(11, "s1", "Both", 2), _owned(13, "s3", "Owned", tags=["trade"])])
    binder_library.forget()


async def test_the_binder_page_lists_every_card_and_its_library(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    try:
        _seed(stack)
        r = await b.http.get("/binder")
        assert r.status_code == 200, r.text
        html = r.text
        # the library: Cards, My lists (with New list…), More
        for words in (
            "All cards",
            "Owned",
            "Want",
            "Price alerts",
            "Upgrades",
            "New list…",
            "Watch settings",
        ):
            assert words in html, words
        assert "Archidekt lists" in html and "Decks I watch" in html
        # one row per card, with what Select needs
        assert html.count("class='bn-row'") == 3
        assert "data-key='w:w1'" in html and "data-rids='[&quot;11 Both&quot;]'" in html
        assert "Own 2" in html and "Alert under $9.00 · over $12.00" in html
        assert "/static/binder.js" in html
        # the nav's Binder is this page
        assert "href='/binder' aria-current='page'" in html or 'href="/binder" aria-current' in html
        # the boxes' buttons submit their forms from outside them
        for box in ("alerts", "list", "tag", "remove", "newlist"):
            assert f"id='bn-{box}-f'" in html and f"form='bn-{box}-f'" in html, box
        # a list, a search and a tag narrow the rows
        assert (await b.http.get("/binder?list=Upgrades")).text.count("class='bn-row'") == 1
        assert (await b.http.get("/binder?q=want")).text.count("class='bn-row'") == 1
        assert (await b.http.get("/binder?show=owned")).text.count("class='bn-row'") == 2
        assert (await b.http.get("/binder?tag=trade")).text.count("class='bn-row'") == 1
        part = await b.http.get("/binder?part=rows&from=1")
        assert part.text.count("class='bn-row'") == 2 and part.headers["X-More"] == "0"
    finally:
        await b.aclose()


async def test_bulk_changes_on_the_binder_page_and_their_undo(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    svc = stack.gw.prices
    try:
        _seed(stack)
        back = "/binder?show=all"

        async def post(**data: str):
            return await _post(b, "/binder", {"back": back, **data})

        # own alerts on two watches, then Undo
        r = await post(action="alerts", keys="w:w1 w:w2", mode="own", alert_drop="40")
        assert r.status_code == 303 and "ok=alerts" in r.headers["location"]
        assert {w["alert_drop_pct"] for w in svc.watched_cards(SUB)} == {40.0}
        undo = r.headers["location"].split("undo=")[1].split("&")[0]
        r = await post(action="undo", token=undo)
        assert "ok=undone" in r.headers["location"]
        assert {w["alert_drop_pct"] for w in svc.watched_cards(SUB)} == {10.0}
        # an owned card and a watched one into a new list
        r = await post(action="list", keys="o:s3:nonfoil w:w2", new_list="Trade bait")
        assert "ok=list" in r.headers["location"]
        assert svc.owned_lists(SUB) == {("s3", "nonfoil"): "Trade bait"}
        assert {w["id"]: w["list_name"] for w in svc.watched_cards(SUB)}["w2"] == "Trade bait"
        # watch the owned card (its price when added from the stored prices), then stop
        r = await post(action="watch", keys="o:s3:nonfoil")
        assert "ok=watch" in r.headers["location"]
        new = [w for w in svc.watched_cards(SUB) if w["scryfall_id"] == "s3"]
        assert len(new) == 1 and new[0]["list_name"] == "Trade bait"
        r = await post(action="unwatch", keys="w:" + new[0]["id"])
        assert "ok=unwatch" in r.headers["location"]
        assert not [w for w in svc.watched_cards(SUB) if w["scryfall_id"] == "s3"]
        # nothing picked, and a form from elsewhere
        r = await post(action="watch", keys="")
        assert "err=1" in r.headers["location"]
        r = await b.http.post("/binder", data={"csrf": "nope", "action": "watch", "keys": "o:s3:nonfoil"})
        assert "err=1" in r.headers["location"]
        # lists: make, rename, delete
        r = await post(action="newlist", name="Cube")
        assert r.headers["location"].startswith("/binder?list=Cube")
        r = await post(action="rename", name="Cube", new_name="Cube 2")
        assert "list=Cube+2" in r.headers["location"]
        r = await post(action="delete", name="Cube 2")
        assert "ok=deleted" in r.headers["location"]
        assert "Cube 2" not in [x["name"] for x in svc.lists(SUB)]
    finally:
        await b.aclose()


async def test_watch_settings_save_the_defaults_and_cards_follow(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    svc = stack.gw.prices
    try:
        _seed(stack)
        with stack.gw.db.tx() as c:
            c.execute("UPDATE watch_cards SET uses_defaults = 1 WHERE id = 'w1'")
        page = await b.http.get("/binder/settings")
        assert page.status_code == 200 and "Price alert defaults" in page.text
        assert "A card added at <b>$2.00</b>: alert under <b>$1.50</b> or over <b>$2.50</b>." in page.text
        r = await _post(
            b, "/binder/settings", {"alert_drop": "25", "alert_rise": "", "alert_pct": "", "alert_floor": "1"}
        )
        assert r.status_code == 303 and "ok=settings" in r.headers["location"]
        assert svc.defaults_of(SUB) == {"drop": 25.0, "floor": 1.0}
        w1 = next(w for w in svc.watched_cards(SUB) if w["id"] == "w1")
        assert (w1["alert_drop_pct"], w1["alert_rise_pct"]) == (25.0, None)
        r = await _post(b, "/binder/settings", {"alert_drop": "abc"})
        assert "err=1" in r.headers["location"]
    finally:
        await b.aclose()


async def test_the_lists_hub_holds_the_archidekt_lists_only(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    try:
        r = await b.http.get("/collection/lists")
        assert r.status_code == 200
        assert "Watchlist lists" not in r.text and "‹" not in r.text
        assert "href='/binder'" in r.text  # back to the Binder
    finally:
        await b.aclose()
