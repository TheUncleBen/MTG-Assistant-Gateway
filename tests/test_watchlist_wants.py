"""The Watchlist as a want list (0.7.46 full review, D-FINDINGS F6 and mockup screen 8): a new
watch gets the member's default price alert (shipped: a drop of 10% below the price it was added
at) and remembers that price; the member's own defaults, saved in a box; the watchlist's lists
(groups) made, renamed, deleted and drawn as group heads; the bulk bar's actions on many cards;
"In binder · you own N" from the Binder as last read; a box named in the address drawn open and
the way back to the list the member was on; the assistant's watch_card tool taking a drop alert
and a list; and the Lists hub under Binder showing the watchlist's lists."""

from __future__ import annotations

from pathlib import Path

import pytest

from mtg_gateway import db as db_module
from mtg_gateway import prefs, prices
from mtg_gateway.db import Database

from .conftest import Stack, stack_with
from .test_decks_and_proxy import call, mcp_token, structured
from .test_watchlist import SOL_RING, TOKEN, Clock, _browser, _opt_in, _options, _post, _watch

stack_options = stack_with(_options(ntfy_url="https://ntfy.test", ntfy_token=TOKEN))
SUB = "user-1"


def _ids(stack: Stack) -> dict[str, str]:
    return {w["name"]: w["id"] for w in stack.gw.prices.watched_cards(SUB)}


def _sol(stack: Stack) -> dict:
    return next(c for c in stack.sf.cards if c["name"] == SOL_RING and c["set"] == "frc")


# -- schema and defaults ----------------------------------------------------------------------------


def test_the_schema_grows_lists_and_the_price_when_added(tmp_path: Path) -> None:
    d = Database(tmp_path / "w.sqlite")
    try:
        assert d.schema_version == db_module.SCHEMA_VERSION == 21
        cols = {r[1] for r in d._conn.execute("PRAGMA table_info(watch_cards)")}
        assert {"list_name", "added_price", "added_currency", "alert_drop_pct", "alert_rise_pct"} <= cols
        lists = {r[1] for r in d._conn.execute("PRAGMA table_info(watch_lists)")}
        assert lists == {"sub", "name", "created_at"}
        assert ("watch_lists", "sub") in db_module.MEMBER_TABLES  # delete my data removes them
    finally:
        d.close()


def test_schema_21_adds_the_lists_to_a_schema_20_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Step 21 is the want-list step: a database at schema 20 (assistant_modes) has no lists yet,
    and opening it adds them and keeps the card it watched, in no list."""
    path = tmp_path / "w20.sqlite"
    monkeypatch.setattr(db_module, "MIGRATIONS", db_module.MIGRATIONS[:20])
    old = Database(path)
    try:
        assert old.schema_version == 20
        names = {r[0] for r in old._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert "assistant_modes" in names and "watch_lists" not in names
        with old.tx() as c:
            c.execute(
                "INSERT INTO users (sub, groups_json, first_login_at, last_login_at) VALUES (?, '[]', 1, 1)",
                (SUB,),
            )
            c.execute(
                "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, created_at) "
                "VALUES ('w1', ?, 'Sol Ring', 'frc', '21', 'nonfoil', 1)",
                (SUB,),
            )
    finally:
        old.close()
    monkeypatch.undo()
    d = Database(path, upgrade_dir=tmp_path / "before-upgrade")
    try:
        assert d.schema_version == db_module.SCHEMA_VERSION == 21
        lists = {r[1] for r in d._conn.execute("PRAGMA table_info(watch_lists)")}
        assert lists == {"sub", "name", "created_at"}
        row = d._conn.execute("SELECT list_name, added_price FROM watch_cards WHERE id = 'w1'").fetchone()
        assert tuple(row) == ("", None)
    finally:
        d.close()


def test_the_shipped_default_is_one_easy_to_flip_constant() -> None:
    assert prices.DEFAULT_ALERTS == {"drop": 10.0}
    assert set(prices.DEFAULT_ALERTS) <= set(prices.ALERT_KINDS)
    assert prices.ALERT_COLUMNS["drop"] == "alert_drop_pct" and prices.ALERT_COLUMNS["pct"] == "alert_pct"
    # drop is a share of the price when added: at most 100%
    with pytest.raises(Exception, match="at most 100"):
        prices._alerts(None, None, None, 120, None)
    assert prices._alerts(None, None, None, "12.5", "40") == (None, None, None, 12.5, 40.0)


async def test_a_new_watch_gets_the_default_alert_and_the_price_it_was_added_at(
    stack: Stack, monkeypatch
) -> None:
    clock = Clock(monkeypatch)
    b = await _browser(stack)
    try:
        await _opt_in(b)
        r = await _watch(b, name="Sol Ring", set_code="frc", collector_number="21", finish="nonfoil")
        assert r.status_code == 303, r.text
        w = stack.gw.prices.watched_cards(SUB)[0]
        assert w["alert_drop_pct"] == 10 and w["alert_pct"] is None
        assert w["added_price"] == 1.28 and w["added_currency"] == "usd"
        page = (await b.http.get("/watchlist")).text
        assert "Drops 10% below $1.28</span>" in page  # the row's alert label
        assert "With a price alert <span class='n'>1</span>" in page
        api = (await b.http.get("/watchlist/api/card", params={"name": "Sol Ring"})).json()
        assert api["watches"][0]["alerts"] == {"drop": 10.0} and api["watches"][0]["added_price"] == 1.28
    finally:
        await b.aclose()
    sol = _sol(stack)
    clock.next_day()
    stack.sf.prices[sol["id"]] = {"usd": "1.20"}  # down 6%: not yet
    assert (await stack.gw.prices.run_daily())["alerts"] == 0
    clock.next_day()
    stack.sf.prices[sol["id"]] = {"usd": "1.10"}  # down 14% from $1.28: the alert
    assert (await stack.gw.prices.run_daily())["alerts"] == 1
    msg = stack.ntfy.messages[-1]
    assert msg["message"] == "Sol Ring fell 10% below the $1.28 it was added at: $1.20 to $1.10."
    clock.next_day()
    stack.sf.prices[sol["id"]] = {"usd": "1.05"}  # already below: not again
    assert (await stack.gw.prices.run_daily())["alerts"] == 0


async def test_a_watch_added_while_scryfall_was_busy_takes_its_first_price_as_the_reference(
    stack: Stack, monkeypatch
) -> None:
    clock = Clock(monkeypatch)
    svc = stack.gw.prices
    b = await _browser(stack)
    try:
        await _watch(b, name="Sol Ring", set_code="frc", collector_number="21")
    finally:
        await b.aclose()
    wid = svc.watched_cards(SUB)[0]["id"]
    with stack.h.db.tx() as c:  # as a watch made while Scryfall could not be asked
        c.execute("UPDATE watch_cards SET added_price = NULL, added_currency = NULL WHERE id = ?", (wid,))
    clock.next_day()
    stack.sf.prices[_sol(stack)["id"]] = {"usd": "1.50"}
    await svc.run_daily()
    w = svc.watched_cards(SUB)[0]
    assert w["added_price"] == 1.28 and w["added_currency"] == "usd"  # its first priced day


async def test_alert_defaults_are_the_members_own_once_saved(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack, link=True)
    try:
        assert prefs.alert_defaults(stack.h.db, SUB) == {"drop": 10.0}
        page = (await b.http.get("/watchlist?box=defaults")).text
        box = page.split("id='w-defaults'")[1].split("</form>")[0]
        assert "data-open" in box.split(">")[0]
        assert "name='alert_drop' inputmode='decimal' maxlength='10' value='10'" in box  # not "10.0"
        assert "Price alert: Drops 10% below the price when added (your defaults)." in page
        r = await _post(
            b,
            "/watchlist",
            {
                "action": "defaults",
                "alert_drop": "",
                "alert_rise": "25",
                "alert_pct": "15",
                "back": "/watchlist",
            },
        )
        assert r.status_code == 303 and r.headers["location"] == "/watchlist?ok=defaults_saved", r.text
        assert prefs.alert_defaults(stack.h.db, SUB) == {"rise": 25.0, "pct": 15.0}
        page = (await b.http.get(r.headers["location"])).text
        assert "Price alert defaults saved. They apply to every card you watch from now on." in page
        assert "name='alert_rise' inputmode='decimal' maxlength='10' value='25'" in page
        assert "name='alert_drop' inputmode='decimal' maxlength='10' value=''" in page
        # a new watch takes them, from the form and from the card viewer's Watch button
        await _watch(b, name="Sol Ring", set_code="frc", collector_number="21")
        csrf = await b.csrf("/watchlist")
        r = await b.http.post(
            "/watchlist/api/watch",
            json={"name": "Sol Talisman"},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.text
        assert r.json()["watch"]["alerts"] == {"rise": 25.0, "pct": 15.0}
        by = {w["name"]: w for w in stack.gw.prices.watched_cards(SUB)}
        assert by["Sol Ring"]["alert_rise_pct"] == 25 and by["Sol Ring"]["alert_drop_pct"] is None
        # a bad default is refused like a bad alert
        r = await _post(b, "/watchlist", {"action": "defaults", "alert_drop": "150"})
        assert r.status_code == 400 and "at most 100" in r.text
        # no alert at all is a default too
        r = await _post(b, "/watchlist", {"action": "defaults"})
        assert r.status_code == 303 and prefs.alert_defaults(stack.h.db, SUB) == {}
    finally:
        await b.aclose()


# -- lists (groups) -----------------------------------------------------------------------------------


async def test_lists_are_made_renamed_deleted_and_drawn_as_groups(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    svc = stack.gw.prices
    b = await _browser(stack, link=True)
    try:
        r = await _post(b, "/watchlist", {"action": "list_new", "name": "  Meren wants "})
        assert r.status_code == 303 and r.headers["location"] == "/watchlist?ok=list_created", r.text
        assert svc.lists(SUB) == [{"name": "Meren wants", "count": 0}]
        r = await _post(b, "/watchlist", {"action": "list_new", "name": ""})
        assert r.status_code == 400 and "Give the list a name" in r.text
        # a watch straight into the list, one into none
        await _watch(b, name="Sol Ring", set_code="frc", collector_number="21", list="Meren wants")
        await _watch(b, name="Sol Talisman", finish="foil")
        ids = _ids(stack)
        assert svc.lists(SUB) == [{"name": "Meren wants", "count": 1}]
        page = (await b.http.get("/watchlist")).text
        # the group heads: the named list first, the cards in no list last
        heads = page.split("class='tr group wgrp'")[1:]
        assert len(heads) == 2
        assert "Meren wants" in heads[0] and "1 card · $1.28" in heads[0]
        assert "Not in a list" in heads[1] and "1 card" in heads[1]
        assert (
            page.index("<b>Meren wants</b>")
            < page.index(f"id='wr-{ids['Sol Ring']}'")
            < page.index("<b>Not in a list</b>")
            < page.index(f"id='wr-{ids['Sol Talisman']}'")
        )
        sol_row = page.split(f"id='wr-{ids['Sol Ring']}'")[1].split(">")[0]
        assert "data-list='Meren wants'" in sol_row
        # the head's menu: Rename list… and Delete list…, red
        assert "Rename list…" in heads[0] and "Delete list…" in heads[0] and "class='danger'" in heads[0]
        # the list as a filter, and "Not in a list" with "-"
        one = (await b.http.get("/watchlist", params={"list": "Meren wants"})).text
        assert "Sol Ring" in one and f"id='wr-{ids['Sol Talisman']}'" not in one
        none = (await b.http.get("/watchlist?list=-")).text
        assert f"id='wr-{ids['Sol Talisman']}'" in none and f"id='wr-{ids['Sol Ring']}'" not in none
        # the row's Details moves it to another list; renaming carries the cards along
        r = await _post(
            b,
            "/watchlist",
            {
                "action": "list_rename",
                "name": "Meren wants",
                "new_name": "Trade for",
                "back": "/watchlist?list=Meren+wants",
            },
        )
        assert (
            r.status_code == 303 and r.headers["location"] == "/watchlist?list=Trade+for&ok=list_renamed"
        ), r.text
        assert svc.lists(SUB) == [{"name": "Trade for", "count": 1}]
        assert svc.watched_cards(SUB)[0]["list_name"] == "Trade for"
        r = await _post(b, "/watchlist", {"action": "list_rename", "name": "Nope", "new_name": "X"})
        assert r.status_code == 404
        r = await _post(
            b,
            "/watchlist",
            {"action": "alerts", "watch_id": ids["Sol Talisman"], "alert_pct": "5", "list": "Trade for"},
        )
        assert r.status_code == 303
        assert svc.lists(SUB) == [{"name": "Trade for", "count": 2}]
        # deleting a list keeps its cards, now in no list; the cards' alerts stay
        r = await _post(b, "/watchlist", {"action": "list_delete", "name": "Trade for"})
        assert r.status_code == 303 and r.headers["location"] == "/watchlist?ok=list_deleted"
        assert svc.lists(SUB) == [] and {w["list_name"] for w in svc.watched_cards(SUB)} == {""}
        assert {w["name"]: w["alert_pct"] for w in svc.watched_cards(SUB)}["Sol Talisman"] == 5
        page = (await b.http.get("/watchlist")).text
        assert "class='tr group wgrp'" not in page  # no lists: no group heads
        # the limits: a name is at most 40 characters, a member has at most 50 lists
        long = await _post(b, "/watchlist", {"action": "list_new", "name": "x" * 41})
        assert long.status_code == 303 and svc.lists(SUB)[0]["name"] == "x" * 40
        for i in range(1, prices.MAX_LISTS):
            svc.create_list(SUB, f"List {i}")
        assert len(svc.lists(SUB)) == prices.MAX_LISTS
        r = await _post(b, "/watchlist", {"action": "list_new", "name": "One more"})
        assert r.status_code == 400 and "50" in r.text
    finally:
        await b.aclose()


async def test_the_bulk_bar_sets_alerts_moves_and_removes_many(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    svc = stack.gw.prices
    b = await _browser(stack, link=True)
    try:
        await _watch(b, name="Sol Ring", set_code="frc", collector_number="21")
        await _watch(b, name="Sol Talisman", finish="foil")
        ids = _ids(stack)
        both = [ids["Sol Ring"], ids["Sol Talisman"]]
        page = (await b.http.get("/watchlist")).text
        # every row has a tick box for the hidden bulk form; the bar is there, hidden until a tick
        for name, wid in ids.items():
            tick = f"name='watch_id' value='{wid}' aria-label='Select {name}' form='w-bulk'>"
            assert f"<input type='checkbox' {tick}" in page, name
        assert "<form method='post' id='w-bulk' class='w-bulkform'>" in page
        assert "<div class='ui-bar' role='group' aria-label='Selected cards' hidden>" in page
        assert "name='do' value='alerts'" in page and "name='do' value='move'" in page
        stop = page.split("id='w-stop'")[1].split("</div></div>")[0]
        assert "name='do' value='remove'" in stop and "danger" in stop and "Keep them" in stop
        csrf = await b.csrf("/watchlist")
        r = await b.http.post(
            "/watchlist",
            data={
                "csrf": csrf,
                "action": "bulk",
                "do": "alerts",
                "alert_pct": "7",
                "alert_drop": "",
                "watch_id": both,
            },
        )
        assert r.status_code == 303 and r.headers["location"] == "/watchlist?ok=alerts_set", r.text
        assert {w["alert_pct"] for w in svc.watched_cards(SUB)} == {7}
        assert {w["alert_drop_pct"] for w in svc.watched_cards(SUB)} == {None}
        r = await b.http.post(
            "/watchlist",
            data={"csrf": csrf, "action": "bulk", "do": "move", "new_list": "Trade for", "watch_id": both},
        )
        assert r.status_code == 303 and r.headers["location"] == "/watchlist?ok=moved", r.text
        assert svc.lists(SUB) == [{"name": "Trade for", "count": 2}]
        # nothing ticked, or no choice: refused with a plain message
        r = await b.http.post(
            "/watchlist", data={"csrf": csrf, "action": "bulk", "do": "move", "list": "Trade for"}
        )
        assert r.status_code == 400 and "Select at least one card first." in r.text
        r = await b.http.post("/watchlist", data={"csrf": csrf, "action": "bulk", "watch_id": both[0]})
        assert r.status_code == 400 and "Choose what to do" in r.text
        # another member's watch is not theirs to change
        with stack.h.db.tx() as c:
            c.execute("INSERT OR IGNORE INTO users (sub) VALUES ('user-2')")
            c.execute(
                "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, created_at) "
                "VALUES ('w_other', 'user-2', 'Sol Ring', 'frc', '21', 'nonfoil', 1)"
            )
        r = await b.http.post(
            "/watchlist",
            data={"csrf": csrf, "action": "bulk", "do": "remove", "watch_id": ["w_other", both[0]]},
        )
        assert r.status_code == 303 and r.headers["location"] == "/watchlist?ok=removed"
        assert [w["name"] for w in svc.watched_cards(SUB)] == ["Sol Talisman"]
        assert svc.watched_cards("user-2")[0]["id"] == "w_other"
        assert svc.lists(SUB) == [{"name": "Trade for", "count": 1}]
    finally:
        await b.aclose()


async def test_a_watched_card_the_member_owns_says_so(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    svc = stack.gw.prices
    b = await _browser(stack, link=True)
    try:
        await _watch(b, name="Sol Ring", set_code="frc", collector_number="21")
        await _watch(b, name="Sol Talisman")
        page = (await b.http.get("/watchlist")).text
        assert "In binder" not in page  # nothing known of the Binder yet
        # the Binder as last read whole: two Sol Ring records in two printings, 3 copies in all
        svc.binder.replace(
            SUB,
            [
                {
                    "id": 1,
                    "scryfall_id": _sol(stack)["id"],
                    "name": "Sol Ring",
                    "finish": "nonfoil",
                    "quantity": 2,
                },
                {"id": 2, "scryfall_id": "", "name": "SOL RING", "finish": "foil", "quantity": 1},
                {"id": 3, "scryfall_id": "", "name": "Cultivate", "finish": "nonfoil", "quantity": 4},
            ],
            complete=True,
        )
        page = (await b.http.get("/watchlist")).text
        ids = _ids(stack)
        sol = page.split(f"id='wr-{ids['Sol Ring']}'")[1].split(f"id='wr-{ids['Sol Talisman']}'")[0]
        assert "In binder · you own 3</span>" in sol and "class='ui-lbl green owned'" in sol
        # the label is drawn twice in a row (phone line, desktop column); not on Sol Talisman's
        assert (
            page.count("In binder") == 2
            and "In binder" not in page.split(f"id='wr-{ids['Sol Talisman']}'")[1]
        )
        cards = svc.card_summaries(SUB)
        assert {c["name"]: c["owned"] for c in cards} == {"Sol Ring": 3, "Sol Talisman": 0}
    finally:
        await b.aclose()


async def test_a_box_in_the_address_draws_open_and_the_way_back_is_kept(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack, link=True)
    try:
        for key, box_id in (
            ("add", "w-add"),
            ("deck", "w-deck"),
            ("defaults", "w-defaults"),
            ("newlist", "w-newlist"),
        ):
            page = (await b.http.get(f"/watchlist?box={key}")).text
            head = page.split(f"id='{box_id}'")[1].split(">")[0]
            assert "data-open" in head and "hidden" not in head, key
            others = [
                i
                for _k, i in (("add", "w-add"), ("deck", "w-deck"), ("defaults", "w-defaults"))
                if i != box_id
            ]
            for other in others:
                assert "hidden" in page.split(f"id='{other}'")[1].split(">")[0], (key, other)
        # the form's hidden back: a save returns to the list, sort and filter the member was on
        page = (await b.http.get("/watchlist?list=-&sort=d7&show=alert&box=add")).text
        assert "name='back' value='/watchlist?show=alert&amp;sort=d7&amp;list=-'" in page
        r = await _post(
            b,
            "/watchlist",
            {"action": "list_new", "name": "Soon", "back": "/watchlist?show=alert&sort=d7&list=-"},
        )
        assert r.status_code == 303
        assert r.headers["location"] == "/watchlist?show=alert&sort=d7&list=-&ok=list_created", r.headers
        # from the Lists hub, back to the hub
        r = await _post(b, "/watchlist", {"action": "list_new", "name": "Later", "back": "/collection/lists"})
        assert r.status_code == 303 and r.headers["location"] == "/collection/lists?ok=list_created"
        hub = (await b.http.get("/collection/lists?ok=list_created")).text
        assert "List created." in hub
        # the five Archidekt lists as rows (not the tab bar's sideways strip), counts filled after
        arch = hub.split("<h2>Your Archidekt lists</h2>")[1].split("</section>")[0]
        assert arch.count("<a class='yrow' href=") == 5 and "lp-tabs" not in arch
        assert "<div class='rows lp-hubrows' data-counts='/lists/api/counts'>" in arch
        assert "href='/collection/wants'" in arch and "Pinned cards" in arch
        rows = hub.split("<h2>Watchlist lists</h2>")[1].split("</section>")[0]
        assert "<span>Soon</span>" in rows and "<span>Later</span>" in rows
        assert "href='/watchlist?list=Soon'" in rows and "aria-label='Menu for the list Soon'" in rows
        assert "href='/watchlist?box=rename&amp;list=Soon'" in rows and "Delete list…" in rows
        assert "New list…" in rows and "data-box-open='wl-new'" in rows
        assert "Not in a list" not in rows  # nothing watched outside a list yet
    finally:
        await b.aclose()


async def test_the_assistant_watches_into_a_list_with_a_drop_alert(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    h = stack.h
    b = await _browser(stack, link=True)
    await b.aclose()
    token = await mcp_token(h)
    out = structured(
        await call(
            h, token, "watch_card", {"name": "Sol Talisman", "alert_drop": 25, "list_name": "Meren wants"}
        )
    )
    assert out["ok"], out
    assert out["watch"]["alerts"] == {"drop": 25.0} and out["watch"]["list"] == "Meren wants"
    assert out["watch"]["added_price"] == out["watch"]["price"] == 1.22  # the cheapest printing today
    plain = structured(await call(h, token, "watch_card", {"name": "Sol Ring"}))
    assert plain["ok"] and plain["watch"]["alerts"] == {"drop": 10.0} and plain["watch"]["list"] == ""
    listed = structured(await call(h, token, "list_watchlist"))
    assert {c["name"]: c["list"] for c in listed["cards"]} == {"Sol Ring": "", "Sol Talisman": "Meren wants"}
    assert listed["lists"] == [{"name": "Meren wants", "count": 1}]
    tools = (await h.mcp(token, "tools/list")).json()["result"]["tools"]
    schema = next(t for t in tools if t["name"] == "watch_card")["inputSchema"]["properties"]
    assert "alert_drop" in schema and "list_name" in schema
