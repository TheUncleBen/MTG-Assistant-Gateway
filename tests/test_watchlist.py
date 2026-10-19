"""Watchlists and price history (prices.py), ntfy notifications (notify.py) and the members'
summaries (summary.py): the Watchlist page and the card viewer's price history, the assistant's
tools, the daily check (batched, gentle, free when nobody watches), alerts, notifications through
a fake ntfy server with a token that is never logged, the scheduled summary, the Account page's
cards, retention, limits and the upgrade from the 0.7.22 schema. Time is moved by replacing
prices.now."""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
from pathlib import Path

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from mtg_gateway import db as db_module
from mtg_gateway import notify, prices
from mtg_gateway.config import ConfigError, load_settings
from mtg_gateway.db import Database

from .conftest import FakeIdP, Stack, StackOptions, build_stack, open_stack, run_stack, stack_with
from .test_decks_and_proxy import Browser, call, mcp_token, structured

SOL_RING = "Sol Ring"  # frc 21 in the fixtures: $1.28, €1.13, non-foil only
TOKEN = "tk_secret_ntfy_token_value"


class FakeNtfy:
    """An ntfy server that records what was published and who published it."""

    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.auth: list[str] = []
        self.status = 200
        self.app = Starlette(routes=[Route("/", self.publish, methods=["POST"])])

    async def publish(self, req: Request) -> Response:
        self.auth.append(req.headers.get("authorization", ""))
        if self.status != 200:
            return Response("forbidden", status_code=self.status)
        self.messages.append(json.loads(await req.body()))
        return JSONResponse({"id": "m1", "event": "message"})


class Clock:
    """prices.now, moved a day at a time from 2026-10-12 07:00 UTC (a Monday)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.t = dt.datetime(2026, 10, 12, 7, 0, tzinfo=dt.UTC).timestamp()
        monkeypatch.setattr(prices, "now", lambda: self.t)

    def next_day(self, days: int = 1) -> None:
        self.t += days * 86400

    @property
    def day(self) -> str:
        return prices.day_of(self.t)


def _with_ntfy(st: Stack) -> None:
    """The gateway's notifier posts to a FakeNtfy (``stack.ntfy``)."""
    st.ntfy = FakeNtfy()
    st.gw.notifier._http = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=st.ntfy.app), base_url="https://ntfy.test"
    )


def _options(**over: object) -> StackOptions:
    """Writes on with the shipped approval mode, watchlists on (off by default,
    MTG_WATCHLISTS_ENABLED; see below), a fake Scryfall and ntfy; ``over`` changes settings."""
    return StackOptions(
        settings={"writes_enabled": True, "watchlists_enabled": True, **over},
        scryfall=True,
        started=_with_ntfy,
    )


stack_options = stack_with(_options(ntfy_url="https://ntfy.test", ntfy_token=TOKEN))


@pytest.fixture
async def plain(tmp_path: Path, idp: FakeIdP):
    """A gateway as a self-hoster runs it without setting anything new: no ntfy server."""
    async with open_stack(tmp_path, idp, _options()) as st:
        yield st


async def _browser(st: Stack, *, link: bool = False) -> Browser:
    b = Browser(st.h)
    await b.login("/account")
    if link:
        r = await b.link("alice", "pw-alice")
        assert r.status_code == 303, r.text
    return b


async def _post(b: Browser, path: str, data: dict) -> httpx.Response:
    return await b.http.post(path, data={"csrf": await b.csrf(path), **data})


async def _watch(b: Browser, **fields: str) -> httpx.Response:
    return await _post(b, "/watchlist", {"action": "add_card", **fields})


# -- schema ---------------------------------------------------------------------------------------


def test_a_0_7_22_database_is_upgraded_and_keeps_its_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    path = tmp_path / "old.sqlite"
    monkeypatch.setattr(db_module, "MIGRATIONS", db_module.MIGRATIONS[:11])
    old = Database(path)
    assert old.schema_version == 11
    old.upsert_user("u1", email="u1@example.test", name="U", preferred_username="u", groups=[])
    old.close()
    monkeypatch.undo()
    new = Database(path)
    try:
        assert new.schema_version == db_module.SCHEMA_VERSION == 15
        assert new.get_user("u1") is not None
        tables = {r[0] for r in new._conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        assert {
            "watch_cards",
            "watch_decks",
            "price_points",
            "deck_values",
            "notify_prefs",
            "job_runs",
        } <= tables
    finally:
        new.close()


# -- the daily check ----------------------------------------------------------------------------


async def test_the_daily_check_costs_nothing_when_nobody_watches(plain: Stack, monkeypatch) -> None:
    clock = Clock(monkeypatch)
    jobs = prices.DailyJobs(plain.gw.prices, plain.gw.summaries, hour_utc=6)
    out = await jobs.run_due()
    assert out["prices"] == {"day": clock.day, "watched": False, "requests": 0}
    assert plain.sf.requests == []  # not one request to Scryfall
    assert await jobs.run_due() == {}  # done for today
    clock.t -= 2 * 3600  # 05:00 the next day... before the hour: nothing yet
    clock.next_day()
    assert await jobs.run_due() == {}


async def test_a_watched_card_is_priced_daily_and_shows_its_history(stack: Stack, monkeypatch) -> None:
    clock = Clock(monkeypatch)
    b = await _browser(stack)
    try:
        r = await _watch(b, name="sol ring", set_code="FRC", collector_number="21")
        assert r.status_code == 303 and r.headers["location"] == "/watchlist?ok=card_added", r.text
        page = (await b.http.get("/watchlist")).text
        assert "Sol Ring" in page and "FRC 21" in page and "$1.28" in page and clock.day in page
        assert "No cards watched yet" not in page
        sol = next(c for c in stack.sf.cards if c["name"] == SOL_RING and c["set"] == "frc")
        # three more days, the price moving; one batched request a day
        for usd in ("1.40", "1.10", "2.00"):
            clock.next_day()
            stack.sf.prices[sol["id"]] = {"usd": usd}
            before = len(stack.sf.collection_bodies)
            out = await stack.gw.prices.run_daily()
            assert out["requests"] == 1 and len(stack.sf.collection_bodies) == before + 1
            assert stack.sf.collection_bodies[-1] == [{"id": sol["id"]}]
        page = (await b.http.get("/watchlist")).text
        assert "$2.00" in page
        # the chart has words for a screen reader, and the same numbers are in a table
        assert "class='tspark'" in page and "role='img'" in page
        assert "Sol Ring: $1.28 on 2026-10-12, $2.00 on 2026-10-15." in page
        assert "<th scope='row'>2026-10-13</th><td>$1.40</td>" in page
        assert "1 day: up 81.8%" in page
        # the card viewer's Price history reads the same, with nothing asked of Scryfall
        asked = len(stack.sf.requests)
        api = (await b.http.get("/watchlist/api/card", params={"name": "Sol Ring"})).json()
        assert [p["price"] for p in api["watches"][0]["history"]] == [1.28, 1.4, 1.1, 2.0]
        assert len(stack.sf.requests) == asked
    finally:
        await b.aclose()


async def test_the_check_asks_scryfall_in_batches_of_75_at_the_single_lookup_pace(
    stack: Stack, monkeypatch
) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    await b.aclose()
    sub = "user-1"
    with stack.h.db.tx() as c:
        for i in range(80):
            c.execute(
                "INSERT INTO watch_cards (id, sub, name, scryfall_id, created_at) VALUES (?,?,?,?,0)",
                (f"w{i}", sub, f"Card {i}", f"00000000-0000-0000-0000-{i:012d}"),
            )
    paced: list[float | None] = []
    client = stack.gw.scan.scryfall
    real = client._request

    async def spy(method, path, *, interval=None, **kw):
        paced.append(interval)
        return await real(method, path, interval=interval, **kw)

    monkeypatch.setattr(client, "_request", spy)
    client.lookup_interval = 0.01
    out = await stack.gw.prices.run_daily()
    assert out["requests"] == 2
    assert [len(x) for x in stack.sf.collection_bodies] == [75, 5]
    assert paced == [0.01, 0.01]  # the slower pace, through the shared client and its pacer


async def test_a_scryfall_429_stops_the_check_and_it_runs_again_later(stack: Stack, monkeypatch) -> None:
    clock = Clock(monkeypatch)
    b = await _browser(stack)
    try:
        assert (await _watch(b, name="Sol Ring")).status_code == 303
    finally:
        await b.aclose()
    clock.next_day()
    jobs = prices.DailyJobs(stack.gw.prices, stack.gw.summaries, hour_utc=6)
    stack.sf.fail_with = 429
    out = await jobs.run_due()
    assert "error" in out["prices"]
    assert "summaries" in out  # a failed price check does not hold back the day's summaries
    stack.sf.fail_with = None
    stack.gw.scan.scryfall._retry_until = 0  # the back-off has passed
    out = await jobs.run_due()
    assert out["prices"]["watched"] is True  # not marked done after the failure


# -- watching ----------------------------------------------------------------------------------


async def test_watch_refusals_limits_and_unknown_cards(tmp_path: Path, idp: FakeIdP, monkeypatch) -> None:
    Clock(monkeypatch)
    async with open_stack(tmp_path, idp, _options()) as st:
        st.gw.live.set("watch_max_cards", 1, by="admin-1")  # the admin Settings page's limit
        b = await _browser(st)
        try:
            r = await _watch(b, name="No Such Card Anywhere")
            assert r.status_code == 404 and "Scryfall knows no card named" in r.text
            r = await _watch(b, name="Sol Ring", finish="foil")
            assert r.status_code == 400 and "not printed in foil" in r.text
            r = await _watch(b, name="Sol Ring", collector_number="21")
            assert r.status_code == 400 and "goes with a set code" in r.text
            assert (await _watch(b, name="Sol Ring")).status_code == 303
            r = await _watch(b, name="Command Tower")
            assert r.status_code == 429
            said = "You watch 1 card; this gateway lets each member watch at most 1. Remove 1 to add another."
            assert said in r.text
            # 0.7.29: the admin Settings page's limit applies at once, raised or lowered
            st.gw.live.set("watch_max_cards", 2, by="admin-1")
            assert (await _watch(b, name="Command Tower")).status_code == 303
            st.gw.live.set("watch_max_cards", 1, by="admin-1")
            r = await _watch(b, name="Cultivate")
            assert r.status_code == 429
            said = (
                "You watch 2 cards; this gateway lets each member watch at most 1. Remove 2 to add another."
            )
            assert said in r.text
            # no form token: refused
            r = await b.http.post("/watchlist", data={"action": "add_card", "name": "Opt"})
            assert r.status_code == 403
        finally:
            await b.aclose()


async def test_card_viewer_watch_button_needs_the_form_token(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    try:
        body = json.dumps({"name": "Sol Talisman", "finish": "foil"})
        r = await b.http.post(
            "/watchlist/api/watch", content=body, headers={"Content-Type": "application/json"}
        )
        assert r.status_code == 403
        csrf = await b.csrf("/watchlist")
        r = await b.http.post(
            "/watchlist/api/watch",
            content=body,
            headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        )
        assert r.status_code == 200, r.text
        assert r.json()["watch"]["price"] == 2.59 and r.json()["watch"]["finish"] == "foil"
    finally:
        await b.aclose()
    # the viewer offers it (static/cardview.js)
    js = (Path(prices.__file__).parent / "static" / "cardview.js").read_text(encoding="utf-8")
    assert "/watchlist/api/card?name=" in js and "/watchlist/api/watch" in js


async def test_assistant_tools_watch_list_history_and_unwatch(stack: Stack, monkeypatch) -> None:
    clock = Clock(monkeypatch)
    h = stack.h
    b = await _browser(stack, link=True)
    await b.aclose()
    token = await mcp_token(h)
    out = structured(
        await call(h, token, "watch_card", {"name": "Sol Talisman", "finish": "foil", "alert_pct": 10})
    )
    assert out["ok"] and out["watch"]["price"] == 2.59 and out["watch"]["alerts"] == {"pct": 10.0}
    deck = structured(await call(h, token, "watch_deck", {"deck_id": "42"}))
    assert deck["ok"] and deck["deck"]["name"] == "Sample Commander Deck" and deck["deck"]["cards"] > 0
    assert deck["deck"]["price"] is not None  # the cards Scryfall knows are priced at once
    clock.next_day()
    await stack.gw.prices.run_daily()
    listed = structured(await call(h, token, "list_watchlist"))
    assert [c["name"] for c in listed["cards"]] == ["Sol Talisman"]
    assert "history" not in listed["cards"][0] and listed["limits"] == {"cards": 200, "decks": 10}
    hist = structured(await call(h, token, "card_price_history", {"card": "sol talisman"}))
    assert [p["day"] for p in hist["cards"][0]["history"]] == ["2026-10-12", "2026-10-13"]
    dh = structured(await call(h, token, "card_price_history", {"deck_id": "42"}))
    assert len(dh["deck"]["history"]) == 2
    gone = structured(await call(h, token, "unwatch", {"card": "Sol Talisman", "deck_id": "42"}))
    assert gone == {"ok": True, "cards_removed": ["Sol Talisman"], "deck_removed": "Sample Commander Deck"}
    missing = structured(await call(h, token, "card_price_history", {"card": "Sol Talisman"}))
    assert missing["ok"] is False and missing["error"] == "not_found"
    # a read-only connection may not watch
    tools = (await h.mcp(token, "tools/list")).json()["result"]["tools"]
    by = {t["name"]: t for t in tools}
    assert by["list_watchlist"]["annotations"]["readOnlyHint"] is True
    assert by["get_summary"]["annotations"]["readOnlyHint"] is True
    assert by["watch_card"]["annotations"]["readOnlyHint"] is False


async def test_old_points_age_out_and_delete_my_data_removes_watches(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    try:
        assert (await _watch(b, name="Sol Ring")).status_code == 303
        db = stack.h.db
        with db.tx() as c:
            c.execute("INSERT INTO price_points (scryfall_id, day, usd) VALUES ('x', '2000-01-01', 1.0)")
        db.purge_expired()
        assert (
            db._conn.execute("SELECT COUNT(*) FROM price_points WHERE day = '2000-01-01'").fetchone()[0] == 0
        )
        assert db._conn.execute("SELECT COUNT(*) FROM price_points").fetchone()[0] == 1  # today's stays
        notify.save_prefs(db, "user-1", ntfy_on=True, ntfy_topic=notify.new_topic(db, "user-1"))
        out = db.delete_member_data("user-1")
        assert out["watch_cards"] == 1 and out["notify_prefs"] == 1
    finally:
        await b.aclose()


# -- notifications ------------------------------------------------------------------------------


async def _opt_in(b: Browser, **extra: str) -> httpx.Response:
    data = {"action": "notify_save", "ntfy_on": "1", "on_proposals": "1", "on_reports": "1", "on_prices": "1"}
    return await _post(b, "/account", {**data, **extra})


async def test_notifications_are_off_and_harmless_without_a_server(plain: Stack) -> None:
    b = await _browser(plain)
    try:
        page = (await b.http.get("/account")).text
        assert "This gateway has no notification server set up" in page
        # bug 10: no summary schedule without a server to send it through
        assert "id='summary'" not in page and "summary_every" not in page
        r = await _post(b, "/account", {"action": "summary_save", "summary_every": "daily"})
        assert r.headers["location"] == "/account?err=summary_off#summary"
        assert notify.prefs(plain.h.db, "user-1")["summary_every"] == "off"
        assert plain.gw.notifier.enabled is False
        assert plain.gw.notifier._count is None  # nothing hooked
    finally:
        await b.aclose()


async def test_opt_in_topic_test_button_and_the_token_never_logged(stack: Stack, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    b = await _browser(stack)
    try:
        r = await _opt_in(b)
        assert r.status_code == 303 and r.headers["location"] == "/account?ok=notify_saved#notifications"
        topic = notify.prefs(stack.h.db, "user-1")["ntfy_topic"]
        assert re.fullmatch(r"mtg-[A-Za-z0-9_-]{22}", topic)
        page = (await b.http.get("/account")).text
        # in a browser: no topic or server on show (the app is the only way to read them), Send test
        assert topic not in page and "https://ntfy.test" not in page and "Send test" in page
        assert "data-native='startNotifications'" not in page and "/install?for=android" in page
        assert TOKEN not in page
        r = await _post(b, "/account", {"action": "notify_test"})
        assert r.headers["location"] == "/account?ok=notify_test_sent#notifications"
        msg = stack.ntfy.messages[-1]
        assert msg["topic"] == topic and msg["click"] == "https://mtg.test/account"
        assert stack.ntfy.auth[-1] == f"Bearer {TOKEN}"
        # one test a minute per member
        sent = len(stack.ntfy.messages)
        r = await _post(b, "/account", {"action": "notify_test"})
        assert r.headers["location"] == "/account?err=notify_test_wait#notifications"
        assert len(stack.ntfy.messages) == sent
        stack.gw.notifier._last_test.clear()
        # a refusal from the server is said plainly, and logged with its status alone
        stack.ntfy.status = 403
        r = await _post(b, "/account", {"action": "notify_test"})
        assert r.headers["location"] == "/account?err=notify_test_failed#notifications"
        assert "ntfy publish refused: HTTP 403" in caplog.text
        assert TOKEN not in caplog.text
        assert TOKEN not in repr(stack.gw.settings) and TOKEN not in repr(stack.gw.notifier)
        # a topic sent with the form is ignored: the gateway makes every topic
        r = await _opt_in(b, ntfy_topic="mtg-my-own-topic-1234")
        assert notify.prefs(stack.h.db, "user-1")["ntfy_topic"] == topic
        # Disconnect my phones: a new topic
        r = await _post(b, "/account", {"action": "notify_new_topic"})
        assert r.headers["location"] == "/account?ok=notify_topic#notifications"
        assert notify.prefs(stack.h.db, "user-1")["ntfy_topic"] != topic
        # off keeps the topic; on again keeps it too
        kept = notify.prefs(stack.h.db, "user-1")["ntfy_topic"]
        r = await _post(b, "/account", {"action": "notify_save"})
        assert r.headers["location"] == "/account?ok=notify_off#notifications"
        assert "Send test" not in (await b.http.get("/account")).text
        await _opt_in(b)
        assert notify.prefs(stack.h.db, "user-1")["ntfy_topic"] == kept
    finally:
        await b.aclose()


async def test_a_proposal_waiting_for_the_member_and_a_finished_report_notify(stack: Stack) -> None:
    h = stack.h
    b = await _browser(stack, link=True)
    try:
        await _opt_in(b)
    finally:
        await b.aclose()
    token = await mcp_token(h)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Arcane Signet"}]},
        )
    )
    assert p["state"] == "pending" and p["assistant_may_apply"] is False  # manual mode: it waits
    await stack.gw.notifier.drain()
    msg = stack.ntfy.messages[-1]
    assert msg["title"] == notify.waiting_title(1, p["deck_name"])
    assert msg["sequence_id"] == "deck-42"
    assert msg["click"] == f"https://mtg.test/proposals/{p['proposal_id']}"
    assert "Arcane Signet" not in json.dumps(msg)
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": "42", "simulate": False}))
    await stack.gw.notifier.drain()
    msg = stack.ntfy.messages[-1]
    assert msg["title"] == "Deck report finished"
    assert msg["click"] == f"https://mtg.test/history/reports/{rep['report_id']}"
    # switched off one by one
    b = await _browser(stack)
    try:
        await _post(b, "/account", {"action": "notify_save", "ntfy_on": "1", "on_prices": "1"})
    finally:
        await b.aclose()
    n = len(stack.ntfy.messages)
    await call(h, token, "run_deck_report", {"deck_ref": "42", "simulate": False})
    await stack.gw.notifier.drain()
    assert len(stack.ntfy.messages) == n


async def test_a_queued_forge_run_notifies_when_it_ends(stack: Stack) -> None:
    rs = stack.gw.reports
    b = await _browser(stack)
    try:
        await _opt_in(b)
    finally:
        await b.aclose()
    with stack.h.db.tx() as c:
        c.execute(
            "INSERT INTO reports (id, owner_sub, deck_id, deck_name, fingerprint, taken_at, stats_json, "
            "forge_json, forge_state) VALUES ('rep_q', 'user-1', '42', 'D', 'f', 0, '{}', '{}', 'queued')"
        )
    assert rs._save_forge("rep_q", {"state": "running"})
    await stack.gw.notifier.drain()
    assert stack.ntfy.messages == []
    assert rs._save_forge("rep_q", {"state": "done"})
    await stack.gw.notifier.drain()
    assert [m["title"] for m in stack.ntfy.messages] == ["Deck report finished"]
    rs._save_forge("rep_q", {"state": "done"})  # saved again: not sent twice
    await stack.gw.notifier.drain()
    assert len(stack.ntfy.messages) == 1


async def test_price_alerts_reach_the_member(stack: Stack, monkeypatch) -> None:
    clock = Clock(monkeypatch)
    b = await _browser(stack)
    try:
        await _opt_in(b)
        assert (await _watch(b, name="Sol Ring", set_code="frc", collector_number="21")).status_code == 303
        wid = stack.gw.prices.watched_cards("user-1")[0]["id"]
        r = await _post(
            b, "/watchlist", {"action": "alerts", "watch_id": wid, "alert_pct": "10", "alert_above": "1.5"}
        )
        assert r.headers["location"] == "/watchlist?ok=card_updated"
        r = await _post(b, "/watchlist", {"action": "alerts", "watch_id": wid, "alert_pct": "-3"})
        assert r.status_code == 400 and "more than 0" in r.text
    finally:
        await b.aclose()
    sol = next(c for c in stack.sf.cards if c["name"] == SOL_RING and c["set"] == "frc")
    clock.next_day()
    stack.sf.prices[sol["id"]] = {"usd": "1.30"}  # +1.6%: quiet
    assert (await stack.gw.prices.run_daily())["alerts"] == 0
    clock.next_day()
    stack.sf.prices[sol["id"]] = {"usd": "1.60"}  # +23% and above $1.50
    assert (await stack.gw.prices.run_daily())["alerts"] == 1
    msg = stack.ntfy.messages[-1]
    assert msg["title"] == "Price alert" and msg["click"] == "https://mtg.test/watchlist"
    assert msg["message"] == "Sol Ring rose 23.1% and rose above $1.50: $1.30 to $1.60."


# -- summaries ----------------------------------------------------------------------------------


async def test_summary_on_the_account_page_by_tool_and_on_schedule(stack: Stack, monkeypatch) -> None:
    clock = Clock(monkeypatch)  # a Monday
    h = stack.h
    b = await _browser(stack, link=True)
    try:
        await _opt_in(b)
        r = await _post(b, "/account", {"action": "summary_save", "summary_every": "weekly"})
        assert r.headers["location"] == "/account?ok=summary_saved#summary"
        r = await _post(b, "/account", {"action": "summary_save", "summary_every": "hourly"})
        assert "err=" in r.headers["location"]
        assert (await _watch(b, name="Sol Ring", set_code="frc", collector_number="21")).status_code == 303
    finally:
        await b.aclose()
    sol = next(c for c in stack.sf.cards if c["name"] == SOL_RING and c["set"] == "frc")
    jobs = prices.DailyJobs(stack.gw.prices, stack.gw.summaries, hour_utc=6)
    for usd in ("1.30", "1.35", "1.40", "1.45", "1.50", "1.55", "1.60"):
        clock.next_day()
        stack.sf.prices[sol["id"]] = {"usd": usd}
        out = await jobs.run_due()
        # weekly: only the next Monday sends
        assert out["summaries"] == (1 if clock.day == "2026-10-19" else 0), clock.day
    msg = stack.ntfy.messages[-1]
    assert msg["title"] == "Your weekly summary" and msg["click"] == "https://mtg.test/account#summary"
    assert "1 watched cards, biggest move Sol Ring +25%" in msg["message"]
    token = await mcp_token(h)
    s = structured(await call(h, token, "get_summary", {"period": "week"}))
    assert s["ok"] and s["cards"][0]["from"] == 1.28 and s["cards"][0]["to"] == 1.6
    assert "Sol Ring: $1.28 to $1.60 (+25%)" in s["text"] and "no AI" in s["written_by"]
    b = await _browser(stack)
    try:
        page = (await b.http.get("/account")).text
        assert "Sol Ring: $1.28 to $1.60 (+25%)" in page and "selected>Every week" in page
    finally:
        await b.aclose()


async def test_summary_counts_a_report_only_once_its_forge_games_are_done(plain: Stack) -> None:
    db = plain.gw.summaries.db
    now = int(prices.now())
    with db.tx() as c:
        for rid, state in (("rep_quick", None), ("rep_playing", "running"), ("rep_played", "done")):
            c.execute(
                "INSERT INTO reports (id, owner_sub, deck_id, deck_name, fingerprint, taken_at, stats_json, "
                "forge_state) VALUES (?, 'user-1', '1', 'Deck', 'f', ?, '{}', ?)",
                (rid, now, state),
            )
    out = plain.gw.summaries.build("user-1", "day")
    assert sorted(r["report_id"] for r in out["reports_finished"]) == ["rep_played", "rep_quick"]
    assert "Deck reports finished: 2." in out["text"]


# -- configuration ------------------------------------------------------------------------------


def _env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from cryptography.fernet import Fernet

    for name, value in (("fernet", Fernet.generate_key().decode()), ("session", "s" * 40), ("oidc", "x")):
        (tmp_path / name).write_text(value)
    monkeypatch.setenv("MTG_PUBLIC_URL", "https://gateway.example.com")
    monkeypatch.setenv("MTG_OIDC_ISSUER", "https://idp.example.com/o/mtg/")
    monkeypatch.setenv("MTG_OIDC_CLIENT_ID", "c")
    monkeypatch.setenv("MTG_OIDC_CLIENT_SECRET_FILE", str(tmp_path / "oidc"))
    monkeypatch.setenv("MTG_FERNET_KEY_FILE", str(tmp_path / "fernet"))
    monkeypatch.setenv("MTG_SESSION_SECRET_FILE", str(tmp_path / "session"))
    monkeypatch.setenv("MTG_ALLOW_ANY_IDP_USER", "true")


def test_settings_default_off_and_the_token_comes_from_a_file(monkeypatch, tmp_path: Path) -> None:
    _env(monkeypatch, tmp_path)
    s = load_settings()
    assert s.ntfy_url is None and s.ntfy_token is None
    assert (s.watch_max_cards, s.watch_max_decks, s.price_history_days, s.price_hour_utc) == (200, 10, 365, 6)
    (tmp_path / "ntfy").write_text(TOKEN + "\n")
    monkeypatch.setenv("MTG_NTFY_URL", "https://ntfy.example.com/")
    monkeypatch.setenv("MTG_NTFY_TOKEN_FILE", str(tmp_path / "ntfy"))
    s = load_settings()
    assert s.ntfy_url == "https://ntfy.example.com" and s.ntfy_token == TOKEN
    assert TOKEN not in repr(s)
    monkeypatch.setenv("MTG_NTFY_URL", "ftp://ntfy.example.com")
    with pytest.raises(ConfigError, match="MTG_NTFY_URL"):
        load_settings()
    # members see the address, so it may not carry credentials
    monkeypatch.setenv("MTG_NTFY_URL", "https://user:pass@ntfy.example.com")
    with pytest.raises(ConfigError, match="user name or password"):
        load_settings()


def test_purge_keeps_the_configured_days(tmp_path: Path) -> None:
    db = Database(tmp_path / "p.sqlite")
    try:
        db.price_days = 30
        old = prices.days_before(prices.day_of(prices.time.time()), 40)
        db._conn.execute("INSERT INTO price_points (scryfall_id, day, usd) VALUES ('a', ?, 1)", (old,))
        db.purge_expired()
        assert db._conn.execute("SELECT COUNT(*) FROM price_points").fetchone()[0] == 0
    finally:
        db.close()


def test_raw_sqlite_rows_survive(tmp_path: Path) -> None:
    # sanity for the migration test above: the schema's UNIQUE topic is enforced
    db = Database(tmp_path / "u.sqlite")
    try:
        for sub in ("a", "b"):
            db.upsert_user(sub, email=None, name=None, preferred_username=None, groups=[])
        notify.save_prefs(db, "a", ntfy_topic="mtg-the-same-topic-123")
        with pytest.raises(ValueError, match="taken"):
            notify.save_prefs(db, "b", ntfy_topic="mtg-the-same-topic-123")
        with pytest.raises(sqlite3.IntegrityError):
            db._conn.execute(
                "INSERT INTO notify_prefs (sub, ntfy_topic, updated_at) "
                "VALUES ('c', 'mtg-the-same-topic-123', 0)"
            )
    finally:
        db.close()


# -- the switch (MTG_WATCHLISTS_ENABLED, off by default) -----------------------------------------

WATCH_TOOLS = {"watch_card", "watch_deck", "unwatch", "list_watchlist", "card_price_history"}


def _record_daily_jobs(monkeypatch: pytest.MonkeyPatch) -> list[tuple]:
    """Each DailyJobs app.py builds at start, as (prices, summaries)."""
    from mtg_gateway import app as app_module

    made: list[tuple] = []

    class Recorder(prices.DailyJobs):
        def __init__(self, p, s, hour_utc):
            super().__init__(p, s, hour_utc)
            made.append((p, s))

        async def loop(self) -> None:  # never runs a round in a test
            return None

    monkeypatch.setattr(app_module, "DailyJobs", Recorder)
    return made


def test_the_switch_is_off_by_default(monkeypatch, tmp_path: Path) -> None:
    _env(monkeypatch, tmp_path)
    assert load_settings().watchlists_enabled is False
    monkeypatch.setenv("MTG_WATCHLISTS_ENABLED", "true")
    assert load_settings().watchlists_enabled is True


async def test_watchlists_off_hide_the_page_links_tools_and_daily_job(
    tmp_path: Path, idp: FakeIdP, monkeypatch
) -> None:
    made = _record_daily_jobs(monkeypatch)
    built = build_stack(tmp_path, idp, _options(watchlists_enabled=False))
    h = built.h
    # a member's watch from when the switch was on stays in the database
    h.db.upsert_user("user-1", email="u@example.test", name="U", preferred_username="u", groups=[])
    with h.db.tx() as c:
        c.execute(
            "INSERT INTO watch_cards (id, sub, name, created_at) VALUES ('w1', 'user-1', 'Sol Ring', 0)"
        )
    async with run_stack(built) as st:
        assert st.gw.prices is None and made == []  # no price service, no daily job
        b = await _browser(st)
        try:
            for path in ("/watchlist", "/watchlist/api/card?name=Sol+Ring"):
                assert (await b.http.get(path)).status_code == 404, path
            r = await b.http.post("/watchlist/api/watch", json={"name": "Sol Ring"})
            assert r.status_code in (404, 405)
            page = (await b.http.get("/decks")).text
            assert "href='/watchlist'" not in page and "data-watchlist" not in page
            account = (await b.http.get("/account")).text
            assert "price alert" not in account and "watched card" not in account
        finally:
            await b.aclose()
        token = await mcp_token(h)
        names = {t["name"] for t in (await h.mcp(token, "tools/list")).json()["result"]["tools"]}
        assert not names & WATCH_TOOLS and "get_summary" in names
        s = structured(await call(h, token, "get_summary", {"period": "day"}))
        assert s["ok"] and s["watchlists"] is False and s["cards"] == [] and s["decks"] == []
        assert "Watched cards" not in s["text"] and "Proposals waiting for you" in s["text"]
        assert h.db._conn.execute("SELECT COUNT(*) FROM watch_cards").fetchone()[0] == 1


async def test_watchlists_off_with_ntfy_keep_the_summary_without_prices(
    tmp_path: Path, idp: FakeIdP, monkeypatch
) -> None:
    clock = Clock(monkeypatch)
    made = _record_daily_jobs(monkeypatch)
    async with open_stack(
        tmp_path, idp, _options(watchlists_enabled=False, ntfy_url="https://ntfy.test")
    ) as st:
        assert [p for p, _s in made] == [None]  # the daily job runs the summaries only
        b = await _browser(st)
        try:
            await _opt_in(b)
            page = (await b.http.get("/account")).text
            assert "id='summary'" in page and "summary_every" in page
            assert "watched prices" not in page and "price alert" not in page
            assert "<pre>" not in page  # no summary is built for a member who chose none
            r = await _post(b, "/account", {"action": "summary_save", "summary_every": "daily"})
            assert r.headers["location"] == "/account?ok=summary_saved#summary"
            page = (await b.http.get("/account")).text
            assert "<pre>Summary for" in page and "Watched cards" not in page
        finally:
            await b.aclose()
        clock.next_day()
        out = await prices.DailyJobs(None, st.gw.summaries, hour_utc=6).run_due()
        assert "prices" not in out and out["summaries"] == 1
        assert st.ntfy.messages[-1]["title"] == "Your daily summary"


async def test_watchlists_on_show_the_links_and_tools(stack: Stack) -> None:
    b = await _browser(stack)
    try:
        page = (await b.http.get("/decks")).text
        assert "href='/watchlist'" in page and "data-watchlist" in page
        assert (await b.http.get("/watchlist")).status_code == 200
    finally:
        await b.aclose()
    token = await mcp_token(stack.h)
    names = {t["name"] for t in (await stack.h.mcp(token, "tools/list")).json()["result"]["tools"]}
    assert WATCH_TOOLS | {"get_summary"} <= names
    js = (Path(prices.__file__).parent / "static" / "cardview.js").read_text(encoding="utf-8")
    assert 'document.body.hasAttribute("data-watchlist") ? priceButton(card) : null' in js


async def test_watchlists_on_start_the_daily_job(tmp_path: Path, idp: FakeIdP, monkeypatch) -> None:
    made = _record_daily_jobs(monkeypatch)
    async with open_stack(tmp_path, idp, _options()) as st:
        assert len(made) == 1 and made[0][0] is st.gw.prices
        assert st.gw.prices is not None


async def test_each_deck_value_names_its_price_source(stack: Stack, monkeypatch) -> None:
    """The watchlist's deck value (Scryfall's prices) and the deck page's estimated cost
    (Archidekt's) differ, so each says where its prices come from; the numbers are unchanged."""
    Clock(monkeypatch)
    b = await _browser(stack, link=True)
    try:
        assert (await _post(b, "/watchlist", {"action": "add_deck", "deck": "42"})).status_code == 303
        page = (await b.http.get("/watchlist")).text
        assert "Sample Commander Deck" in page and "(Scryfall prices)" in page
        deck = (await b.http.get("/decks/42")).text
        assert "(Archidekt prices)</span>" in deck and "<span>Est cost (Archidekt)</span>" in deck
    finally:
        await b.aclose()


@pytest.mark.parametrize(("watched", "remove"), [(["1"], "Remove 1"), (["1", "2", "3"], "Remove 3")])
def test_the_deck_limit_says_how_many_to_remove(watched: list[str], remove: str) -> None:
    """0.7.29: a lowered limit can leave a member several over, so the refusal counts them."""
    from types import SimpleNamespace

    from mtg_gateway.decks import DeckError

    svc = object.__new__(prices.PriceService)
    svc.settings, svc.decks = SimpleNamespace(watch_max_decks=1), SimpleNamespace()
    svc._rows = lambda sql, args: [{"deck_id": d} for d in watched]
    with pytest.raises(DeckError) as refused:
        svc._check_deck_limit("user-1", "99")
    noun = "deck" if len(watched) == 1 else "decks"
    assert str(refused.value) == (
        f"You watch {len(watched)} {noun}; this gateway lets each member watch at most 1. "
        f"{remove} to add another."
    )
    svc._check_deck_limit("user-1", "1")  # a deck already watched is never refused
