"""Binder data and prices (0.7.46, decision C5).

- An add goes into a record only when printing, finish, condition and language all match; anything
  else is a new record, and the page says which record the copies went to.
- The Binder's printings (and the member's own decks') are priced once a day with the daily price
  check's batched Scryfall call into ``price_points``: the Binder shows its value, each row its
  price, it sorts by price, and the card viewer has a price history for any owned printing.
- The Watchlist page reads a fixed number of queries whatever the watchlist's size, so the limits
  go up to 5,000 cards and 100 decks.
- A watch by name alone follows the cheapest printing.
- "Pin card" from any card menu pins it on Archidekt.
- The Add box and the Watch form remember the member's choices (finish, condition, language; finish
  and alert), and the card menus' adds and watches use them.
"""

from __future__ import annotations

import datetime as dt
import json
import re

import pytest

from mtg_gateway import prices
from mtg_gateway.config import MANAGED_SETTINGS

from .conftest import COLLECTION, Stack, stack_with
from .test_browse_collection import api, linked

stack_options = stack_with(COLLECTION)

SUB = "user-1"
SOL = 9003  # the fake Archidekt's Sol Ring (printing "xxx 1")


class Clock:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.t = dt.datetime(2026, 10, 12, 7, 0, tzinfo=dt.UTC).timestamp()
        monkeypatch.setattr(prices, "now", lambda: self.t)

    def next_day(self, days: int = 1) -> None:
        self.t += days * 86400

    @property
    def day(self) -> str:
        return prices.day_of(self.t)


def _card(stack: Stack, name: str, set_code: str, number: str) -> dict:
    return next(
        c
        for c in stack.sf.cards
        if c["name"] == name and c["set"] == set_code and c["collector_number"] == number
    )


def _record(stack: Stack, rid: int, card_id: int, **body) -> dict:
    """One record in alice's binder (quantity 1, NM English unless ``body`` says otherwise)."""
    ark = stack.ark
    row = ark._collection_row(rid, ark.printing(card_id), {"quantity": 1, **body})
    ark.collections.setdefault("alice", {})[rid] = row
    ark.next_coll_id = max(ark.next_coll_id, rid)
    return row


def _priced_record(stack: Stack, rid: int, sf: dict, quantity: int, **body) -> dict:
    """A record of a printing the fake Scryfall knows (its uid is the Scryfall id)."""
    card = {
        "id": 90000 + rid,
        "uid": sf["id"],
        "collectorNumber": sf["collector_number"],
        "edition": {"editioncode": sf["set"], "editionname": sf.get("set_name", "")},
        "oracleCard": {"name": sf["name"]},
    }
    row = stack.ark._collection_row(rid, card, {"quantity": quantity, **body})
    stack.ark.collections.setdefault("alice", {})[rid] = row
    stack.ark.next_coll_id = max(stack.ark.next_coll_id, rid)
    return row


def _sols(stack: Stack) -> list[tuple[int, int, int]]:
    """alice's Sol Ring records, oldest first: (copies, condition number, language number)."""
    return [
        (r["quantity"], r["condition"], r["language"])
        for _rid, r in sorted(stack.ark.collections.get("alice", {}).items())
        if r["card"]["oracleCard"]["name"] == "Sol Ring"
    ]


# -- a) the merge rule ---------------------------------------------------------------------------


async def test_an_add_merges_only_into_the_same_printing_finish_condition_and_language(stack: Stack) -> None:
    _record(stack, 1, SOL, quantity=3)  # Near Mint, English
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        # Lightly Played Japanese: its own record, never NM English
        r = await api(
            b,
            "POST",
            "/collection/api/add",
            {"items": [{"name": "Sol Ring", "condition": "LP", "language": "JA"}]},
            csrf=csrf,
        )
        assert r.status_code == 200, r.text
        assert _sols(stack) == [(3, 1, 1), (1, 2, 6)]
        added = r.json()["added"][0]
        assert added["went_to"] == "new"
        assert "new entry" in r.json()["message"] and "Lightly Played" in r.json()["message"]
        assert "Japanese" in r.json()["message"]
        # the same printing, finish, condition and language: the copies go to that record
        r = await api(
            b,
            "POST",
            "/collection/api/add",
            {"items": [{"name": "Sol Ring", "language": "JA", "condition": "LP"}]},
            csrf=csrf,
        )
        assert _sols(stack) == [(3, 1, 1), (2, 2, 6)]
        assert r.json()["added"][0]["went_to"] == "existing"
        assert "now 2 copies" in r.json()["message"]
        # English with no condition picked is written Near Mint, so it joins the NM English record
        r = await api(b, "POST", "/collection/api/add", {"items": [{"name": "Sol Ring"}]}, csrf=csrf)
        assert _sols(stack) == [(4, 1, 1), (2, 2, 6)]
        # English Lightly Played: a third record
        r = await api(
            b, "POST", "/collection/api/add", {"items": [{"name": "Sol Ring", "condition": "LP"}]}, csrf=csrf
        )
        assert _sols(stack) == [(4, 1, 1), (2, 2, 6), (1, 2, 1)]
    finally:
        await b.aclose()


async def test_add_by_name_says_which_entry_the_copies_went_to(stack: Stack) -> None:
    _record(stack, 1, SOL, quantity=3)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        r = await b.http.post(
            "/collection",
            data={
                "csrf": csrf,
                "action": "add",
                "name": "Sol Ring",
                "quantity": "2",
                "finish": "nonfoil",
                "condition": "NM",
                "language": "EN",
            },
        )
        assert r.status_code == 303, r.text
        page = (await b.http.get(r.headers["location"])).text
        assert "Added 2 copies to the “Sol Ring” entry you already had" in page, page
        assert "now 5 copies" in page
        r = await b.http.post(
            "/collection",
            data={
                "csrf": csrf,
                "action": "add",
                "name": "Sol Ring",
                "quantity": "1",
                "finish": "nonfoil",
                "condition": "MP",
                "language": "DE",
            },
        )
        page = (await b.http.get(r.headers["location"])).text
        assert "Added “Sol Ring” as a new entry" in page and "Moderately Played" in page and "German" in page
        assert _sols(stack) == [(5, 1, 1), (1, 3, 3)]
    finally:
        await b.aclose()


async def test_the_add_box_has_a_language_field(stack: Stack) -> None:
    b = await linked(stack)
    try:
        page = (await b.http.get("/collection?add=1")).text
        box = page[page.index("id='col-addform'") :]
        box = box[: box.index("</form>")]
        assert "name='language'" in box and ">Japanese<" in box
    finally:
        await b.aclose()


async def test_an_import_says_how_many_lines_went_to_entries_already_there(stack: Stack) -> None:
    from mtg_gateway.collection import import_result, import_result_html

    items = [{"name": "Sol Ring", "line": 2, "quantity": 1}, {"name": "Opt", "line": 3, "quantity": 1}]
    out = {"added": [{"went_to": "existing", "index": 0}, {"went_to": "new", "index": 1}], "skipped": []}
    text = import_result_html(import_result(items, out))
    assert (
        "Added to an entry you already had, with the same printing, finish, condition and language: "
        "line 2 (Sol Ring)." in text
    ), text
    assert "another language" not in text


# -- b) prices for the Binder and the member's decks ----------------------------------------------


async def test_the_binder_is_priced_daily_and_shows_its_value_row_prices_and_sorts(
    stack: Stack, monkeypatch
) -> None:
    clock = Clock(monkeypatch)
    sol = _card(stack, "Sol Ring", "frc", "21")  # $1.28
    aesi = _card(stack, "Aesi, Tyrant of Gyre Strait", "dsc", "210")  # $12.23
    _priced_record(stack, 1, sol, 2)
    _priced_record(stack, 2, aesi, 1)
    _record(stack, 3, 9007)  # Opt in a printing Scryfall does not know: no price
    b = await linked(stack)
    try:
        page = (await b.http.get("/collection")).text  # opening the Binder puts it in the daily check
        out = await stack.gw.prices.run_daily()
        assert out["binders_read"] == 1
        page = (await b.http.get("/collection")).text
        # the value: 2 x $1.28 + $12.23, and the entry with no price is counted, not guessed
        assert "Value" in page and "$14.79" in page, page
        assert "1 entry has no price" in page
        # each row: its price, and the row's value for more than one copy
        assert "$12.23" in page and "2 × $1.28" in page
        # sorted by price, highest first, from the Binder as last read (no Archidekt read)
        reads = len(stack.ark.collection_queries)
        page = (await b.http.get("/collection?sort=price&view=list")).text
        assert len(stack.ark.collection_queries) == reads
        names = re.findall(r"<span class='name' title='([^']+)'>", page)
        assert names[:3] == ["Aesi, Tyrant of Gyre Strait", "Sol Ring", "Opt"], names
        assert "Price, highest first" in page
        page = (await b.http.get("/collection?sort=price&dir=asc&view=list")).text
        names = re.findall(r"<span class='name' title='([^']+)'>", page)
        assert names[:2] == ["Sol Ring", "Aesi, Tyrant of Gyre Strait"], names
        # the next day's prices move the value
        clock.next_day()
        stack.sf.prices[sol["id"]] = {"usd": "2.00"}
        await stack.gw.prices.run_daily()
        page = (await b.http.get("/collection")).text
        assert "$16.23" in page
    finally:
        await b.aclose()


async def test_work_it_out_now_reads_the_binder_at_once_and_says_when_archidekt_fails(
    stack: Stack, monkeypatch
) -> None:
    import asyncio

    Clock(monkeypatch)
    sol = _card(stack, "Sol Ring", "frc", "21")
    _priced_record(stack, 1, sol, 3)
    b = await linked(stack)

    async def settled() -> str:
        for _ in range(100):
            page = (await b.http.get("/collection")).text
            if "Reading your whole binder" not in page:
                return page
            await asyncio.sleep(0.05)
        raise AssertionError("the read never ended")

    try:
        page = (await b.http.get("/collection")).text
        assert "Work it out now" in page and "$3.84" not in page
        csrf = await b.csrf("/collection")
        stack.ark.fail_collection_reads = True
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "value"})
        assert r.status_code == 303
        page = await settled()
        stack.ark.fail_collection_reads = False
        assert "could not be asked for your whole binder just now" in page and "Work it out now" in page
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "value"})
        page = await settled()
        assert "$3.84" in page and "3 copies" in page  # read and priced, without waiting for the daily check
    finally:
        await b.aclose()


async def test_a_binder_change_from_the_gateway_keeps_the_value_current(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    sol = _card(stack, "Sol Ring", "frc", "21")
    _priced_record(stack, 1, sol, 1)
    b = await linked(stack)
    try:
        await b.http.get("/collection")
        await stack.gw.prices.run_daily()
        csrf = await b.csrf("/collection")
        r = await api(b, "POST", "/collection/api/rows/1", {"delta": 2, "name": "Sol Ring"}, csrf=csrf)
        assert r.status_code == 200, r.text
        page = (await b.http.get("/collection")).text
        assert "$3.84" in page  # 3 x $1.28, without another whole read
        r = await api(b, "DELETE", "/collection/api/rows/1?name=Sol%20Ring", {}, csrf=csrf)
        assert r.status_code == 200, r.text
        held = stack.h.db._conn.execute(
            "SELECT COUNT(*) FROM binder_records WHERE sub = ?", (SUB,)
        ).fetchone()[0]
        assert held == 0  # the removed record left the Binder as last read too
    finally:
        await b.aclose()


async def test_the_card_viewer_has_a_price_history_for_an_owned_printing(stack: Stack, monkeypatch) -> None:
    clock = Clock(monkeypatch)
    sol = _card(stack, "Sol Ring", "frc", "21")
    _priced_record(stack, 1, sol, 1)
    b = await linked(stack)
    try:
        await b.http.get("/collection")
        await stack.gw.prices.run_daily()
        clock.next_day()
        stack.sf.prices[sol["id"]] = {"usd": "1.50"}
        await stack.gw.prices.run_daily()
        asked = len(stack.sf.requests)
        got = (await b.http.get("/watchlist/api/card", params={"name": "Sol Ring", "sid": sol["id"]})).json()
        assert got["ok"] and got["watches"] == []
        owned = got["owned"]
        assert owned["where"] == ["binder"]
        assert [h["price"] for h in owned["history"]] == [1.28, 1.5]
        assert len(stack.sf.requests) == asked  # nothing asked of Scryfall
        # a printing nobody owns or watches: nothing kept
        got = (
            await b.http.get(
                "/watchlist/api/card", params={"name": "Opt", "sid": "0" * 8 + "-0000-0000-0000-" + "0" * 12}
            )
        ).json()
        assert got["owned"] is None
    finally:
        await b.aclose()


async def test_a_deck_the_member_opens_is_priced_too(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await linked(stack)
    try:
        r = await b.http.get("/decks/42")  # alice's own deck
        assert r.status_code == 200
        held = stack.h.db._conn.execute(
            "SELECT deck_id, cards_json FROM owned_decks WHERE sub = ?", (SUB,)
        ).fetchall()
        assert [h[0] for h in held] == ["42"]
        rows = json.loads(held[0][1])
        assert rows and all({"name", "qty", "finish"} <= set(r) for r in rows)
        out = await stack.gw.prices.run_daily()
        assert out["priced"] > 0
    finally:
        await b.aclose()


async def test_nothing_is_read_or_priced_for_a_binder_nobody_opened(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    sol = _card(stack, "Sol Ring", "frc", "21")
    _priced_record(stack, 1, sol, 1)
    b = await linked(stack)
    await b.aclose()
    reads = len(stack.ark.collection_queries)
    out = await stack.gw.prices.run_daily()
    assert out == {"day": out["day"], "watched": False, "requests": 0}
    assert len(stack.ark.collection_queries) == reads


# -- c) the Watchlist's size ----------------------------------------------------------------------


def test_the_limits_are_5000_cards_and_100_decks() -> None:
    assert (
        MANAGED_SETTINGS["watch_max_cards"].default == 5000 and MANAGED_SETTINGS["watch_max_cards"].hi == 5000
    )
    assert (
        MANAGED_SETTINGS["watch_max_decks"].default == 100 and MANAGED_SETTINGS["watch_max_decks"].hi == 100
    )


def _seed_watches(stack: Stack, n: int, days: int = 40) -> None:
    today = dt.date.today()
    with stack.h.db.tx() as c:
        for i in range(n):
            sid = f"00000000-0000-4000-8000-{i:012d}"
            c.execute(
                "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, scryfall_id, "
                "created_at) VALUES (?,?,?,?,?,?,?,0)",
                (f"w_{i:05d}", SUB, f"Card {i:05d}", "abc", str(i + 1), "nonfoil", sid),
            )
            for d in range(days):
                c.execute(
                    "INSERT INTO price_points (scryfall_id, day, usd) VALUES (?,?,?)",
                    (sid, (today - dt.timedelta(days=d)).isoformat(), 1 + i / 100 + d / 1000),
                )
        for i in range(n // 10):
            c.execute(
                "INSERT INTO watch_decks (sub, deck_id, deck_name, cards_json, refreshed_at, created_at) "
                "VALUES (?,?,?,?,0,0)",
                (SUB, str(1000 + i), f"Deck {i}", "[]"),
            )
            for d in range(days):
                c.execute(
                    "INSERT INTO deck_values (sub, deck_id, day, usd, eur, cards, priced) "
                    "VALUES (?,?,?,?,?,100,100)",
                    (SUB, str(1000 + i), (today - dt.timedelta(days=d)).isoformat(), 100.0 + d, 90.0),
                )


async def _queries(stack: Stack, b, path: str) -> tuple[int, str]:
    seen: list[str] = []
    conn = stack.h.db._conn
    conn.set_trace_callback(seen.append)
    try:
        r = await b.http.get(path)
    finally:
        conn.set_trace_callback(None)
    assert r.status_code == 200, r.text
    return sum(1 for q in seen if "price_points" in q or "deck_values" in q), r.text


async def test_the_watchlist_page_reads_a_fixed_number_of_queries_whatever_its_size(stack: Stack) -> None:
    b = await linked(stack)
    try:
        _seed_watches(stack, 30)
        few, page = await _queries(stack, b, "/watchlist")
        assert "Card 00000" in page and "Load more cards" in page
        with stack.h.db.tx() as c:
            for table in ("watch_cards", "watch_decks", "price_points", "deck_values"):
                c.execute(f"DELETE FROM {table}")
        _seed_watches(stack, 300)
        many, page = await _queries(stack, b, "/watchlist")
        assert many == few, (few, many)
        assert "You may watch up to 5000 cards and 100 decks" in page or "5,000" in page
        # 25 rows a screen; the decks have their own Load more
        assert page.count("class='tr' id='wr-w_") == 25
        assert page.count("id='wr-d") == 25 and "Load more decks" in page
        # the chips still count every card and a sort still covers them all
        assert "All</span>" in page or ">All" in page
        _n, page = await _queries(stack, b, "/watchlist?sort=price")
        first = re.findall(r"id='wr-(w_\d+)'", page)[0]
        assert first == "w_00299"
    finally:
        await b.aclose()


async def test_a_deck_s_full_history_comes_from_its_own_api(stack: Stack) -> None:
    b = await linked(stack)
    try:
        _seed_watches(stack, 10, days=60)
        page = (await b.http.get("/watchlist")).text
        assert "data-full=" not in page  # a year of values per deck is not in the page any more
        got = (await b.http.get("/watchlist/api/deck", params={"deck_id": "1000"})).json()
        assert got["ok"] and len(got["history"]) == 60
    finally:
        await b.aclose()


# -- d) "any printing" is the cheapest printing ---------------------------------------------------


async def test_a_watch_by_name_follows_the_cheapest_printing(stack: Stack, monkeypatch) -> None:
    clock = Clock(monkeypatch)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/watchlist")
        r = await b.http.post(
            "/watchlist", data={"csrf": csrf, "action": "add_card", "name": "Aesi, Tyrant of Gyre Strait"}
        )
        assert r.status_code == 303, r.text
        w = stack.gw.prices.watched_cards(SUB)[0]
        view = stack.gw.prices.card_view(w)
        # cmr 365 is foil only; the Secret Lair reversible printing at $11.50 is cheaper than
        # plst CMR-365 at $12.18 and dsc 210 at $12.23
        assert view["price"] == 11.5 and view["cheapest"] == {"set": "sld", "collector_number": "1873"}
        page = (await b.http.get("/watchlist")).text
        assert "Cheapest printing (now SLD 1873)" in page
        clock.next_day()
        dsc = _card(stack, "Aesi, Tyrant of Gyre Strait", "dsc", "210")
        stack.sf.prices[dsc["id"]] = {"usd": "10.00"}
        out = await stack.gw.prices.run_daily()
        assert out["cheapest"] == 1
        view = stack.gw.prices.card_view(stack.gw.prices.watched_cards(SUB)[0])
        assert view["price"] == 10.0 and view["cheapest"]["set"] == "dsc"
        assert [h["price"] for h in view["history"]] == [11.5, 10.0]
    finally:
        await b.aclose()


# -- e) Pin card ----------------------------------------------------------------------------------


async def test_pin_card_from_a_card_menu_pins_it_on_archidekt(stack: Stack) -> None:
    b = await linked(stack)
    try:
        csrf = await b.csrf("/lists/pins")
        r = await b.http.post(
            "/lists/pins",
            data={"csrf": csrf, "action": "pin", "card": "Sol Ring"},
            headers={"Accept": "application/json"},
        )
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True and "Pinned" in r.json()["notice"]
        pins = stack.ark.account.pins.get("alice") or []
        assert [p["card"]["oracleCard"]["name"] for p in pins] == ["Sol Ring"]
        page = (await b.http.get("/lists/pins")).text
        assert "Pin one from any card's menu" in page or "Pin card" in page
    finally:
        await b.aclose()


def test_every_card_menu_offers_pin_card() -> None:
    from pathlib import Path

    static = Path(__file__).parents[1] / "src" / "mtg_gateway" / "static"
    menu = (static / "cardmenu.js").read_text()
    assert '"Pin card"' in menu and "/lists/pins" in menu
    deck = (static / "deck.js").read_text()
    assert "Pin card" in deck


# -- f) remembered Add and Watch choices ----------------------------------------------------------


async def test_the_add_box_remembers_finish_condition_and_language(stack: Stack) -> None:
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        r = await b.http.post(
            "/collection",
            data={
                "csrf": csrf,
                "action": "add",
                "name": "Opt",
                "quantity": "1",
                "finish": "foil",
                "condition": "LP",
                "language": "JA",
                "remember": "1",
            },
        )
        assert r.status_code == 303
        page = (await b.http.get("/collection?add=1")).text
        box = page[page.index("id='col-addform'") :]
        box = box[: box.index("</form>")]
        assert "<option value='foil' selected>" in box
        assert "<option value='LP' selected>" in box
        assert "<option value='JA' selected>" in box
        assert "name='remember' value='1' checked" in box
        # a card menu's "Add to binder" (no choices of its own) uses them
        r = await api(
            b,
            "POST",
            "/collection/api/add",
            {"items": [{"name": "Swamp", "quantity": 1}], "source": "manual"},
            csrf=csrf,
        )
        rec = next(
            r for r in stack.ark.collections["alice"].values() if r["card"]["oracleCard"]["name"] == "Swamp"
        )
        assert (rec["condition"], rec["language"]) == (2, 6)
        # unticked, the choice is used once and not kept
        await b.http.post(
            "/collection",
            data={
                "csrf": csrf,
                "action": "add",
                "name": "Opt",
                "quantity": "1",
                "finish": "nonfoil",
                "condition": "HP",
                "language": "EN",
            },
        )
        page = (await b.http.get("/collection?add=1")).text
        box = page[page.index("id='col-addform'") :]
        assert "<option value='LP' selected>" in box[: box.index("</form>")]
    finally:
        await b.aclose()


async def test_the_watch_form_remembers_finish_and_alert_and_menus_use_them(
    stack: Stack, monkeypatch
) -> None:
    Clock(monkeypatch)
    b = await linked(stack)
    try:
        csrf = await b.csrf("/watchlist")
        r = await b.http.post(
            "/watchlist",
            data={
                "csrf": csrf,
                "action": "add_card",
                "name": "Sol Ring",
                "finish": "nonfoil",
                "alert_pct": "15",
                "remember": "1",
            },
        )
        assert r.status_code == 303, r.text
        w = stack.gw.prices.watched_cards(SUB)[0]
        assert w["alert_pct"] == 15
        page = (await b.http.get("/watchlist")).text
        form = page[page.index("id='w-add'") :]
        form = form[: form.index("</form>")]
        assert "name='alert_pct'" in form and "value='15'" in form and "checked" in form
        # the card viewer's or a menu's Watch (no alert of its own) gets the remembered alert
        r = await b.http.post(
            "/watchlist/api/watch",
            content=json.dumps({"name": "Aesi, Tyrant of Gyre Strait"}),
            headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.text
        assert r.json()["watch"]["alerts"] == {"pct": 15}
    finally:
        await b.aclose()


async def test_a_binder_not_opened_for_60_days_is_no_longer_kept(stack: Stack, monkeypatch) -> None:
    clock = Clock(monkeypatch)
    _priced_record(stack, 1, _card(stack, "Sol Ring", "frc", "21"), 1)
    b = await linked(stack)
    try:
        await b.http.get("/collection")
    finally:
        await b.aclose()
    await stack.gw.prices.run_daily()
    held = "SELECT COUNT(*) FROM binder_records WHERE sub = ?"
    assert stack.h.db._conn.execute(held, (SUB,)).fetchone()[0] == 1
    clock.next_day(61)
    reads = len(stack.ark.collection_queries)
    await stack.gw.prices.run_daily()
    assert stack.h.db._conn.execute(held, (SUB,)).fetchone()[0] == 0
    assert stack.h.db._conn.execute("SELECT COUNT(*) FROM binder_reads").fetchone()[0] == 0
    assert len(stack.ark.collection_queries) == reads  # not read again
