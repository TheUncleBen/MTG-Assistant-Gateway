"""The Watchlist's kept rows (PriceService.card_summaries) follow exactly what they are worked out
from: a member's watched cards (alerts, lists, prices when added, the cheapest printing), the
shared price points, the member's Binder as last read (the "you own N" count), and the day. Every
write to one of those works the rows out again, by whatever path it comes; a write anywhere else (a
Binder visit, an assistant's tool call, another member's watches or Binder) leaves them kept, so
going from the Binder to the Watchlist at 5,000 cards does not pay for the whole list again."""

from __future__ import annotations

from typing import Any

from mtg_gateway import prices
from mtg_gateway.db import delete_member_rows

from .conftest import Stack
from .test_decks_and_proxy import call, mcp_token
from .test_watchlist import Clock, _browser, _post, _watch, stack_options  # noqa: F401
from .test_watchlist_rows import _priced_many

SUB = "user-1"


class Counted:
    """Counts how many times the service works a member's rows out (``_card_summaries``)."""

    def __init__(self, stack: Stack) -> None:
        self.svc = stack.gw.prices
        self.n = 0
        real = self.svc._card_summaries

        def counted(sub: str) -> list[dict[str, Any]]:
            self.n += 1
            return real(sub)

        self.svc._card_summaries = counted

    def rows(self, sub: str = SUB) -> dict[str, dict[str, Any]]:
        return {v["watch_id"]: v for v in self.svc.card_summaries(sub)}


def _binder_row(rid: int, name: str, qty: int) -> dict[str, Any]:
    return {"id": rid, "name": name, "quantity": qty, "finish": "nonfoil"}  # no Scryfall id yet


async def test_writes_elsewhere_keep_the_rows(stack: Stack) -> None:
    """A Binder visit (binder_reads), an assistant's tool call (metrics), another member's watches
    and Binder, the member's own settings and empty lists: none of them is what the rows are
    worked out from, so the next Watchlist view reuses them."""
    b = await _browser(stack, link=True)
    try:
        _priced_many(stack, 30)
        seen = Counted(stack)
        svc, db = seen.svc, stack.gw.db
        assert (await b.http.get("/watchlist")).status_code == 200 and seen.n == 1
        token = await mcp_token(stack.h)
        # the two the tester measured: the Binder page and an assistant's tool call, end to end
        assert (await b.http.get("/collection")).status_code == 200
        await call(stack.h, token, "whoami", {})
        assert (await b.http.get("/watchlist")).status_code == 200
        assert seen.n == 1
        # and the same writes, and others like them, straight
        svc.binder.seen(SUB)
        db.metrics_increment("2026-11-01", SUB, "tool", "whoami")
        db.set_pref(SUB, "watch.alerts", {"drop": 20.0})
        svc.create_list(SUB, "Trades")
        with db.tx() as c:
            c.execute(
                "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, created_at) "
                "VALUES ('wother', 'user-2', 'Card 1', 'abc', '1', 'nonfoil', 0)"
            )
            c.execute("UPDATE watch_cards SET alert_pct = 3 WHERE sub = 'user-2'")
            c.execute("DELETE FROM watch_cards WHERE sub = 'user-2'")
        svc.binder.replace("user-2", [_binder_row(1, "Card 1", 4)], complete=True)
        assert seen.rows() and seen.n == 1
        page = (await b.http.get("/watchlist")).text
        assert seen.n == 1 and "Trades" in page  # the lists are read with each view, not kept
    finally:
        await b.aclose()


async def test_every_write_the_rows_come_from_works_them_out_again(stack: Stack, monkeypatch) -> None:
    """Each kind of write to what the rows are made from: once, and the change shows."""
    b = await _browser(stack, link=True)
    try:
        _priced_many(stack, 6)
        seen = Counted(stack)
        svc, db = seen.svc, stack.gw.db
        today = prices.day_of(prices.now())

        def after(write: Any) -> dict[str, dict[str, Any]]:
            before = seen.n
            seen.rows()  # kept as they were
            assert seen.n == before + (0 if before else 1)
            start = seen.n
            write()
            rows = seen.rows()
            assert seen.n == start + 1, "the write did not work the rows out again"
            seen.rows()
            assert seen.n == start + 1, "the rows were not kept after"
            return rows

        # the watched cards, through the service
        rows = after(lambda: svc.set_alerts(SUB, "w00001", 5.0, None, None))
        assert rows["w00001"]["alerts"] == {"pct": 5.0}
        rows = after(lambda: svc.set_alerts_many(SUB, ["w00002", "w00003"], above=9))
        assert rows["w00002"]["alerts"] == {"above": 9.0} == rows["w00003"]["alerts"]
        svc.create_list(SUB, "Wants")
        rows = after(lambda: svc.move_to_list(SUB, ["w00001"], "Wants"))
        assert rows["w00001"]["list"] == "Wants"
        rows = after(lambda: svc.rename_list(SUB, "Wants", "Needs"))
        assert rows["w00001"]["list"] == "Needs"
        rows = after(lambda: svc.delete_list(SUB, "Needs"))
        assert rows["w00001"]["list"] == prices.NO_LIST
        rows = after(lambda: svc.unwatch_card(SUB, "w00005"))
        assert "w00005" not in rows
        rows = after(lambda: svc.unwatch_many(SUB, ["w00004"]))
        assert "w00004" not in rows

        # the watched cards, through the page (a watch adds its row and its first price)
        before = seen.n
        assert (await _watch(b, name="Sol Ring", set_code="frc", collector_number="21")).status_code == 303
        rows = seen.rows()
        assert seen.n == before + 1 and any(v["name"] == "Sol Ring" and v["price"] for v in rows.values())
        wid = next(k for k, v in rows.items() if v["name"] == "Sol Ring")
        r = await _post(b, "/watchlist", {"action": "alerts", "watch_id": wid, "alert_below": "0.5"})
        assert r.status_code == 303 and seen.rows()[wid]["alerts"] == {"below": 0.5} and seen.n == before + 2

        # the watched cards, by the daily check's own writes and by any statement
        def added() -> None:
            with db.tx() as c:
                c.execute(
                    "UPDATE watch_cards SET added_price = 1.5, added_currency = 'usd' WHERE id = 'w00000'"
                )

        assert after(added)["w00000"]["added_price"] == 1.5

        def cheapest() -> None:
            with db.tx() as c:
                c.execute(
                    "UPDATE watch_cards SET set_code = '', cheapest_set = 'xyz', cheapest_cn = '7' "
                    "WHERE id = 'w00002'"
                )

        assert after(cheapest)["w00002"]["cheapest"] == {"set": "xyz", "collector_number": "7"}

        # the shared prices: a new day's point, a changed one, and old ones going
        def store() -> None:
            with db.tx() as c:
                svc._store_point(c, "s-00000", prices.point_of({"prices": {"usd": "42.00"}}), today)

        assert after(store)["w00000"]["price"] == 42.0

        def change() -> None:
            with db.tx() as c:
                c.execute(
                    "UPDATE price_points SET usd = 43 WHERE scryfall_id = 's-00000' AND day = ?", (today,)
                )

        assert after(change)["w00000"]["price"] == 43.0

        def prune() -> None:
            with db.tx() as c:
                c.execute("DELETE FROM price_points WHERE scryfall_id = 's-00000'")

        assert after(prune)["w00000"]["price"] is None

        # the member's Binder as last read: the "you own N" count
        rows = after(lambda: svc.binder.replace(SUB, [_binder_row(1, "Card 1", 3)], complete=True))
        assert rows["w00001"]["owned"] == 3
        assert after(lambda: svc.binder.put(SUB, _binder_row(1, "Card 1", 5)))["w00001"]["owned"] == 5
        after(lambda: svc.binder.set_uid(SUB, 1, "uid-1"))
        assert after(lambda: svc.binder.drop(SUB, 1))["w00001"]["owned"] == 0
        svc.binder.replace(SUB, [_binder_row(2, "Card 3", 2)], complete=True)
        assert seen.rows()["w00003"]["owned"] == 2
        with db.tx() as c:
            c.execute("UPDATE binder_reads SET seen_at = 0 WHERE sub = ?", (SUB,))
        assert after(lambda: svc.binder.forget_old(1))["w00003"]["owned"] == 0

        # the day turning (the 1, 7 and 30 day changes are counted from today)
        assert after(lambda: Clock(monkeypatch)) is not None

        # and the member's data being deleted
        def delete() -> None:
            with db.tx() as c:
                delete_member_rows(c, SUB)

        assert after(delete) == {}
    finally:
        await b.aclose()


async def test_a_members_rows_are_kept_apart_from_anothers_but_prices_are_shared(stack: Stack) -> None:
    """One member's watch keeps another's rows; a price point is everyone's, so it works out the
    rows of every member again."""
    _priced_many(stack, 3)
    seen = Counted(stack)
    db = stack.gw.db
    with db.tx() as c:
        c.execute(
            "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, scryfall_id, "
            "created_at) VALUES ('wb', 'user-2', 'Card 1', 'abc', '1', 'nonfoil', 's-00001', 0)"
        )
    seen.rows(SUB), seen.rows("user-2")
    assert seen.n == 2
    seen.svc.set_alerts(SUB, "w00000", 5.0, None, None)
    assert seen.rows("user-2")["wb"]["alerts"] == {} and seen.n == 2
    seen.rows(SUB)
    assert seen.n == 3
    with db.tx() as c:
        point = prices.point_of({"prices": {"usd": "77"}})
        seen.svc._store_point(c, "s-00001", point, prices.day_of(prices.now()))
    assert seen.rows("user-2")["wb"]["price"] == 77.0 and seen.rows(SUB)["w00001"]["price"] == 77.0
    assert seen.n == 5
