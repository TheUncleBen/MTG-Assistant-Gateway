"""The history page and the report page over the ASGI stack: the lists are
narrowed and paged in SQL so a deck keeps its older entries, the chips and filters, the "When"
presets (UTC calendar days), the day and deck groupings render, snapshots keep their Restore button,
and a stored report renders as a designed page with Markdown and HTML exports."""

from __future__ import annotations

import calendar
import json
import re
import time
from pathlib import Path

import pytest

from mtg_gateway import history_view
from mtg_gateway.db import Database

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_decks_and_proxy import call, mcp_token, structured

stack_options = stack_with(COMPANION)

FIX = Path(__file__).parent / "fixtures" / "reports"
GOLDFISH = (FIX / "goldfish_run.md").read_text()
GOLDFISH_PRECON = (FIX / "goldfish_run_precon.md").read_text()
VALIDATION = (FIX / "validation_issues.md").read_text()
DAY = 86400


class _FrozenTime:
    """The ``time`` module with ``time()`` fixed, for the History page's idea of "now"."""

    def __init__(self, now: int) -> None:
        self._now = now

    def time(self) -> float:
        return float(self._now)

    def __getattr__(self, name: str) -> object:
        return getattr(time, name)


def page_clock(monkeypatch: pytest.MonkeyPatch) -> int:
    """A "now" at least eight hours and ten minutes into its UTC day, with the History page's clock
    stopped at it. Rows seeded seconds or minutes before now then fall on the page's "today", and
    seed_history's second step (32 hours back) on its "yesterday", whenever the test runs,
    including early in a UTC day or across midnight."""
    real = int(time.time())
    now = real if real % DAY >= 8 * 3600 + 600 else real - real % DAY - 1
    monkeypatch.setattr(history_view, "time", _FrozenTime(now))
    return now


def seed_history(db: Database, sub: str, *, now: int) -> None:
    """30 proposals for deck 42 and 30 for deck 43, alternating and spread over 20 days, one
    snapshot per applied proposal, so the user-wide newest 20 hold only part of deck 42's. A
    snapshot's deck names its owner, the linked account, as a deck read from Archidekt does."""
    link = db.get_link(sub)
    uid = (link or {}).get("archidekt_user_id")
    owner = {"owner": {"id": int(uid), "username": link["archidekt_username"]}} if link and uid else {}
    for i in range(60):
        deck = "42" if i % 2 == 0 else "43"
        pid = f"p{i:02d}"
        state = ("applied", "pending", "rejected", "failed")[i % 4]
        db.save_proposal(
            {
                "id": pid,
                "owner_sub": sub,
                "kind": "restore" if i == 4 else "edit",
                "deck_id": deck,
                "deck_name": "Immortal Reckoning" if deck == "42" else "Reap the Tides",
                "baseline_fingerprint": "f",
                "changes": [],
                "diff_text": f"+1 Sol Ring\n-1 Card {i}" if i % 2 == 0 else f"+1 Counterspell\n-1 Card {i}",
                "expires_at": now + 3600,
                "created_by_client": "__browser__" if i % 3 == 0 else "app-1",
            }
        )
        if state != "pending":
            db.finish_proposal(pid, state=state, result={})
        with db.tx() as c:
            c.execute("UPDATE proposals SET created_at = ? WHERE id = ?", (now - i * (DAY // 3) - 60, pid))
        if state == "applied":
            db.save_snapshot(
                f"s{i:02d}",
                owner_sub=sub,
                deck_id=deck,
                proposal_id=pid,
                fingerprint="f",
                deck={
                    "id": deck,
                    "name": "Immortal Reckoning" if deck == "42" else "Reap the Tides",
                    "cards": [],
                    **owner,
                },
            )
            with db.tx() as c:
                c.execute(
                    "UPDATE snapshots SET taken_at = ? WHERE id = ?", (now - i * (DAY // 3) - 30, f"s{i:02d}")
                )


def store_report(
    db: Database,
    sub: str,
    rid: str,
    *,
    deck_id: str = "42",
    deck_name: str = "Immortal Reckoning",
    taken_at: int | None = None,
    stats: dict | None = None,
    goldfish: str | None = GOLDFISH,
) -> None:
    """One stored report; ``stats`` overrides some of the statistics, ``goldfish=None`` stores
    a report that ran no simulation."""
    with db.tx() as c:
        c.execute(
            "INSERT INTO reports (id, owner_sub, deck_id, deck_name, fingerprint, taken_at, stats_json, "
            "goldfish_json, validation_json, created_by_client) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                rid,
                sub,
                deck_id,
                deck_name,
                "fp",
                taken_at or int(time.time()) - 300,
                json.dumps(
                    {
                        "card_count": 100,
                        "land_count": 34,
                        "average_mana_value": 3.2,
                        "price_total": 310.0,
                        "mana_curve": {"0": 0, "1": 9, "2": 17, "3": 19, "4": 6, "5": 8, "6": 3, "7+": 4},
                        "colour_pips": {"W": 35, "B": 41},
                        "mana_sources": {"W": 23, "B": 26},
                        "commanders": ["Liesa, Forgotten Archangel"],
                        **(stats or {}),
                    }
                ),
                json.dumps({"tool": "goldfish_run", "ok": True, "text": goldfish}) if goldfish else None,
                json.dumps({"tool": "validate_decklist", "ok": True, "text": VALIDATION}),
                "__browser__",
            ),
        )


# -- database ----------------------------------------------------------------------
def test_db_lists_narrow_in_sql(tmp_path: Path) -> None:
    db = Database(tmp_path / "h.sqlite")
    db.upsert_user("u", email="u@example.test", name="U", preferred_username="u", groups=[])
    now = int(time.time())
    seed_history(db, "u", now=now)
    # the old call is unchanged: the user's newest 20
    assert len(db.list_proposals("u")) == 20
    # a deck's own list is complete, in SQL, however many other decks' proposals are newer
    mine = db.list_proposals("u", limit=100, deck_id="42")
    assert len(mine) == 30 and {p["deck_id"] for p in mine} == {"42"}
    assert [p["id"] for p in mine][:3] == ["p00", "p02", "p04"]
    # offset pages
    assert [p["id"] for p in db.list_proposals("u", limit=5, deck_id="42", offset=5)] == [
        f"p{i:02d}" for i in (10, 12, 14, 16, 18)
    ]
    # kinds, states (a pending proposal past its expiry is "expired") and search
    assert [p["id"] for p in db.list_proposals("u", limit=100, kinds=["restore"])] == ["p04"]
    applied = db.list_proposals("u", limit=100, deck_id="42", states=["applied"])
    assert len(applied) == 15 and all(p["state"] == "applied" for p in applied)
    with db.tx() as c:
        c.execute("UPDATE proposals SET expires_at = ? WHERE id = 'p01'", (now - 5,))
    assert [p["id"] for p in db.list_proposals("u", limit=100, states=["expired"])] == ["p01"]
    pending = db.list_proposals("u", limit=100, states=["pending"])
    assert "p01" not in {p["id"] for p in pending} and "p05" in {p["id"] for p in pending}
    assert {p["deck_id"] for p in db.list_proposals("u", limit=100, search="counterspell")} == {"43"}
    assert {p["deck_id"] for p in db.list_proposals("u", limit=100, search="reap the")} == {"43"}
    assert db.list_proposals("u", limit=100, search="100%_") == []  # wildcards are literal
    # snapshots: per deck, searched by the stored deck's name, paged
    snaps = db.list_snapshots("u", limit=100, deck_id="42")
    assert len(snaps) == 15 and {x["deck_id"] for x in snaps} == {"42"}
    # only applied proposals (every fourth, all deck 42's) have a snapshot
    assert len(db.list_snapshots("u", limit=100, search="immortal")) == 15
    assert db.list_snapshots("u", limit=100, search="Reap") == []
    assert len(db.list_snapshots("u", limit=4, deck_id="42", offset=13)) == 2
    assert db.history_decks("u") == {"42": "Immortal Reckoning", "43": "Reap the Tides"}
    # a time window: since (inclusive) and until (exclusive), on proposals and snapshots alike
    recent = db.list_proposals("u", limit=100, since=now - 2 * DAY)
    assert {p["id"] for p in recent} == {f"p{i:02d}" for i in range(6)}  # one every 8 h: 6 in 2 days
    assert [p["id"] for p in db.list_proposals("u", limit=100, since=now - DAY, until=now - 3600)] == [
        "p01",
        "p02",
    ]
    assert db.list_proposals("u", limit=100, since=now - 59) == []  # p00 is 60 s old
    assert db.list_proposals("u", limit=100, since=now - 60) == db.list_proposals("u", limit=1)
    assert {x["snapshot_id"] for x in db.list_snapshots("u", limit=100, since=now - 2 * DAY)} == {
        "s00",
        "s04",
    }
    assert db.list_snapshots("u", limit=100, since=now - 2 * DAY, until=now - DAY) == [
        db.list_snapshots("u", limit=100)[1]
    ]


# -- pages -------------------------------------------------------------------------
def _table(body: str) -> str:
    """The History table's markup (between its opening tag and the pager)."""
    return body.split("class='ui-table hy-table'")[1].split("<nav class='ui-pager'")[0]


def _entries(body: str) -> list[str]:
    """Each entry row of the History table (not its header, day or deck rows)."""
    return [r for r in re.split(r"(?=<div class='tr)", _table(body)) if r.startswith("<div class='tr'>")]


def _ids(body: str) -> set[str]:
    """The snapshots (by id), restores (by proposal id) and reports a History page lists."""
    table = _table(body)
    return (
        set(re.findall(r"open=s-(\w+)'", table))
        | set(re.findall(r"open=p-(\w+)'", table))
        | set(re.findall(r"/history/reports/(rep_\w+)'", table))
    )


async def test_history_page_filters_groups_and_pages(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    """History lists snapshots, restores and reports (no edit proposals: those are on Proposals),
    narrowed and paged in SQL, with chips, a deck name search, the deck, When and Group by
    filters, day rows, Details and Restore (review first) on every snapshot."""
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        now = page_clock(monkeypatch)
        seed_history(gw.db, sub, now=now)
        for i in range(20):  # more snapshots of deck 42, so its own page has an older one
            gw.db.save_snapshot(
                f"x{i:02d}",
                owner_sub=sub,
                deck_id="42",
                proposal_id=None,
                fingerprint="f",
                deck={"id": "42", "name": "Immortal Reckoning", "cards": []},
            )
            with gw.db.tx() as c:
                c.execute("UPDATE snapshots SET taken_at = ? WHERE id = ?", (now - 30 * DAY - i, f"x{i:02d}"))
        store_report(gw.db, sub, "rep_a", taken_at=now - 100)
        store_report(gw.db, sub, "rep_b", taken_at=now - 2 * DAY)
        page = await b.http.get("/history?deck_id=42", headers=NAV)
        assert page.status_code == 200
        body = page.text.split("<main")[1]
        rows = _entries(body)
        assert len(rows) == 25
        # no edit proposal is listed: a snapshot links the change it was taken before
        assert "+1 Sol Ring" not in body and "Rejected" not in body
        assert "<a class='ui-cap' href='/proposals/p00'>The change</a>" in body
        assert "Older →" in body and "1–25 of 38" in body  # 15 + 20 snapshots, the restore, 2 reports
        assert "<div class='tr daygroup'>Today · " in body and "<div class='tr daygroup'>Yesterday · " in body
        snaps = [r for r in rows if "open=s-" in r]
        assert snaps and all("Restore (review first)" in r and ">Details</a>" in r for r in snaps)
        # on a phone a row is one tap to its details, where Restore sits
        assert all("class='sm-only chev'" in r for r in rows)
        assert "title='Snapshots do not record who made them'>—" in snaps[0]
        # chips with counts, a deck name search and the three filters
        assert "Everything <span class='n'>38</span>" in body and "Reports <span class='n'>2</span>" in body
        assert "placeholder='Deck name'" in body and body.count("<select") == 3
        assert "<option value='42' selected>Sample Commander Deck</option>" in body
        assert "Immortal Reckoning</option>" not in body
        assert (
            "<optgroup label='No longer on Archidekt'><option value='43'>Reap the Tides</option></optgroup>"
            in body
        )
        assert "<select name='when' aria-label='When (UTC days)'>" in body
        assert "<option value='day' selected>Group by day</option>" in body
        # older page
        older = (await b.http.get("/history?deck_id=42&offset=25", headers=NAV)).text.split("<main")[1]
        assert "← Newer" in older and "26–38 of 38" in older
        assert not _ids(body) & _ids(older) and len(_ids(body) | _ids(older)) == 38
        # chips and search
        reps = (await b.http.get("/history?type=reports", headers=NAV)).text.split("<main")[1]
        assert _ids(reps) == {"rep_a", "rep_b"}
        restores = (await b.http.get("/history?type=restores", headers=NAV)).text.split("<main")[1]
        assert _ids(restores) == {"p04"}
        found = (await b.http.get("/history?q=reap", headers=NAV)).text.split("<main")[1]
        assert "value='reap'" in found and "Nothing matches these filters" in found  # deck 43: none
        nothing = await b.http.get("/history?q=zzzz-nothing", headers=NAV)
        assert "Nothing matches these filters" in nothing.text
        # odd query values fall back to the defaults instead of failing
        odd = await b.http.get("/history?type=bogus&offset=abc&when=never&group=colour", headers=NAV)
        assert odd.status_code == 200
        obody = odd.text.split("<main")[1]
        assert "<option value='' selected>Any time</option>" in obody
        assert "<option value='day' selected>Group by day</option>" in obody
        links = obody.split("<nav class='ui-chips'")[1]  # the chips, the table and the pager
        assert "when=" not in links and "group=" not in links  # the links carry only real values
    finally:
        await b.aclose()


async def test_history_when_presets(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    """The "When" presets narrow every kind by UTC calendar day: "Today" is midnight UTC to now,
    "Last 7 days" the seven days ending today, "This year" from 1 January; the filter is in the
    query string and the pager keeps it; an unknown preset means "Any time"."""
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        now = page_clock(monkeypatch)
        midnight = now - now % DAY  # the start of today, UTC
        jan1 = int(calendar.timegm((time.gmtime(now).tm_year, 1, 1, 0, 0, 0)))

        def snapshot(sid: str, at: int, deck: str = "42") -> None:
            gw.db.save_snapshot(
                sid,
                owner_sub=sub,
                deck_id=deck,
                proposal_id=None,
                fingerprint="f",
                deck={
                    "id": deck,
                    "name": "Immortal Reckoning" if deck == "42" else "Reap the Tides",
                    "cards": [],
                },
            )
            with gw.db.tx() as c:
                c.execute("UPDATE snapshots SET taken_at = ? WHERE id = ?", (at, sid))

        snapshot("s_now", now - 5)
        snapshot("s_midnight", midnight)  # the first second of today counts as today
        snapshot("s_yesterday", midnight - 1, deck="43")  # the last second of yesterday does not
        snapshot("s_6d", midnight - 6 * DAY)  # the seventh day back, still "Last 7 days"
        snapshot("s_7d", midnight - 7 * DAY - 1)  # the day before: out
        snapshot("s_29d", midnight - 29 * DAY)
        snapshot("s_89d", midnight - 89 * DAY)
        snapshot("s_100d", midnight - 100 * DAY)
        snapshot("s_jan1", jan1)
        snapshot("s_lastyear", jan1 - 1)
        store_report(gw.db, sub, "rep_old", taken_at=midnight - 10 * DAY)
        store_report(gw.db, sub, "rep_new", taken_at=now - 50)

        async def ids(query: str) -> set[str]:
            page = await b.http.get(f"/history{query}", headers=NAV)
            assert page.status_code == 200
            body = page.text.split("<main")[1]
            return _ids(body) if "class='ui-table hy-table'" in body else set()

        everything = {
            "s_now", "s_midnight", "s_yesterday", "s_6d", "s_7d", "s_29d", "s_89d", "s_100d",
            "s_jan1", "s_lastyear", "rep_old", "rep_new",
        }  # fmt: skip
        assert await ids("") == everything
        assert await ids("?when=today") == {"s_now", "s_midnight", "rep_new"}
        assert await ids("?when=7d") == {"s_now", "s_midnight", "s_yesterday", "s_6d", "rep_new"}
        assert await ids("?when=30d") == {
            "s_now",
            "s_midnight",
            "s_yesterday",
            "s_6d",
            "s_7d",
            "s_29d",
            "rep_old",
            "rep_new",
        }
        assert "s_100d" not in await ids("?when=90d") and "s_89d" in await ids("?when=90d")
        year = await ids("?when=year")
        assert "s_jan1" in year and "s_lastyear" not in year
        assert await ids("?when=sometime") == everything  # unknown: "Any time"
        # the preset combines with the other filters and sits in the query string of every link
        assert await ids("?when=7d&deck_id=43") == {"s_yesterday"}
        assert await ids("?when=7d&type=reports") == {"rep_new"}
        body = (await b.http.get("/history?when=7d&type=snapshots", headers=NAV)).text.split("<main")[1]
        assert "<option value='7d' selected>Last 7 days</option>" in body
        for i in range(30):
            snapshot(f"s_fill{i}", now - 100 - i)
        pbody = (await b.http.get("/history?when=today", headers=NAV)).text.split("<main")[1]
        assert "href='/history?when=today&amp;offset=25' rel='next'>Older →</a>" in pbody
        obody = (await b.http.get("/history?when=today&offset=25", headers=NAV)).text.split("<main")[1]
        assert "href='/history?when=today' rel='prev'>← Newer</a>" in obody and "s_yesterday" not in obody
    finally:
        await b.aclose()


async def test_history_grouped_by_deck(stack: Stack) -> None:
    """group=deck: a row per deck headed by its name (linked to the deck page and to its own
    history with the same filters), decks in the order of their newest entry, entries newest
    first; the filters, Details, Restore and the pager keep working."""
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        now = int(time.time())
        seed_history(gw.db, sub, now=now)  # the snapshots are all deck 42's
        store_report(gw.db, sub, "rep_a", deck_id="43", deck_name="Reap the Tides", taken_at=now - 10)
        page = await b.http.get("/history?group=deck", headers=NAV)
        assert page.status_code == 200
        body = page.text.split("<main")[1]
        listing = _table(body)
        assert "<option value='deck' selected>Group by deck</option>" in body
        heads = re.findall(
            r"<div class='tr group hy-group'><span><a href='/decks/(\d+)'>([^<]+)</a>", listing
        )
        # deck 42's restore (p04, applied as the test began) is newer than deck 43's report
        assert heads == [("42", "Immortal Reckoning"), ("43", "Reap the Tides")]
        assert "<a class='ui-cap' href='/history?deck_id=43&amp;group=deck'>Only this deck</a>" in listing
        assert "open=p-p04'" in listing.split("<div class='tr'>")[1]  # the newest entry leads
        assert len(_entries(body)) == 17 and "daygroup" not in listing
        sec42, sec43 = listing.split("<div class='tr group hy-group'>")[1:]
        assert "/history/reports/rep_a'" in sec43 and "open=s-" not in sec43
        s42 = list(dict.fromkeys(re.findall(r"open=s-(s\d\d)'", sec42)))  # newest first
        assert s42 == sorted(s42) and len(s42) == 15
        assert "Restore (review first)" in sec42
        own = (await b.http.get("/history?group=deck&deck_id=42&type=snapshots", headers=NAV)).text
        own_body = own.split("<main")[1]
        assert _table(own_body).count("hy-group") == 1 and "only this deck" not in own_body
        nothing = await b.http.get("/history?group=deck&q=zzzz-nothing", headers=NAV)
        assert "Nothing matches these filters" in nothing.text
    finally:
        await b.aclose()


async def test_report_page_and_exports(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        store_report(gw.db, sub, "rep_real")
        page = await b.http.get("/history/reports/rep_real?reused=1", headers=NAV)
        assert page.status_code == 200
        body = page.text.split("<main")[1]
        for text in (
            "<h1><a href='/decks/42'>Immortal Reckoning</a></h1>",
            "<span class='k'>Commander</span> Liesa, Forgotten Archangel",
            "<span class='k'>Solo test games</span> 300, through turn 10 (seed 42)",
            "<a href='/reports'>All reports</a>",
            "Played with their effect",
            "report is shown instead of running the simulation again",
            "class='tiles wide rv-stats'",
            "class='ci'",
            "class='charts rv-charts'",
            "<polyline class='s1'",
            "<rect class='col'",
            "What the quick solo test leaves out",
            "32 of 98 cards (33% of the deck)",
            "Validation</h2><span class='ui-lbl orange'>",
            "class='wdg alert'",
            "Deck statistics",
            "class='bars'",
            "Raw output from the research service",
            "<a class='btn btn-plain' href='/history/reports/rep_real/export.md' download>",
            "<a class='btn btn-plain' href='/history/reports/rep_real/export.html' download>",
            "class='btn-plain copybtn' data-copy='rep-md'",
            "<textarea id='rep-md'",
        ):
            assert text in body, text
        assert "goldfish_" not in body and "run_deck_report" not in body
        assert "/static/export.js" in page.text  # the Copy button's script
        md = await b.http.get("/history/reports/rep_real/export.md")
        assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
        assert md.headers["content-disposition"].startswith(
            'attachment; filename="Immortal_Reckoning-report-'
        )
        assert md.text.startswith("# Deck report: Immortal Reckoning") and "## Metrics" not in md.text
        # the raw output is left on the report's page, which the file links to
        assert "https://mtg.test/history/reports/rep_real" in md.text
        # the file name carries the run's day, and Markdown carries plain times (no <time> markup)
        assert re.search(r'-report-\d{4}-\d{2}-\d{2}\.md"$', md.headers["content-disposition"])
        assert "<time" not in md.text
        assert md.headers["cache-control"] == "no-store"
        doc = await b.http.get("/history/reports/rep_real/export.html")
        assert doc.status_code == 200 and doc.headers["content-type"].startswith("text/html")
        assert doc.headers["content-disposition"].endswith('.html"')
        assert doc.text.startswith("<!doctype html>") and "<script" not in doc.text and "<svg" in doc.text
        assert (
            "href='/decks" not in doc.text
            and ".tiles{" in doc.text
            and "@media (prefers-color-scheme" in doc.text
        )
        assert "script-src" not in doc.headers["content-security-policy"]
        # someone else's or an unknown report: 404s, and the exports need a session
        assert (await b.http.get("/history/reports/rep_nope", headers=NAV)).status_code == 404
        assert (await b.http.get("/history/reports/rep_nope/export.md")).status_code == 404
        anon = await stack.h.http.get("/history/reports/rep_real/export.html")
        assert anon.status_code == 302 and anon.headers["location"].startswith("/login")
        # the deck page's button explains the wait and shows busy while the run blocks
        deck = await b.http.get("/decks/42", headers=NAV)
        assert "data-busy-label='Testing…'" in deck.text and "takes up to a minute" in deck.text
        assert "run_deck_report" not in deck.text
    finally:
        await b.aclose()


async def test_report_trends_on_a_decks_history(stack: Stack) -> None:
    """A deck's History page draws its stored reports' trend (statistics and goldfish speed,
    oldest to newest, the latest value and the change since the first, as accessible SVG plus a
    table), the assistant's list_deck_reports returns the same numbers, and one report is no
    trend."""
    h = stack.h
    b = await linked_browser(stack)
    try:
        token = await mcp_token(h)
        gw = h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        now = int(time.time())
        first_day = time.strftime("%Y-%m-%d", time.gmtime(now - 9 * DAY))
        last_day = time.strftime("%Y-%m-%d", time.gmtime(now - DAY))
        store_report(gw.db, sub, "rep_t1", taken_at=now - 9 * DAY)
        # a statistics-only report: the goldfish lines skip it, the statistics keep it
        store_report(
            gw.db, sub, "rep_t2", taken_at=now - 5 * DAY, stats={"price_total": 305.5}, goldfish=None
        )
        store_report(
            gw.db,
            sub,
            "rep_t3",
            taken_at=now - DAY,
            stats={"average_mana_value": 3.0, "price_total": 290.25, "land_count": 36},
            goldfish=GOLDFISH_PRECON,
        )
        store_report(gw.db, sub, "rep_other", deck_id="43", deck_name="Reap the Tides")

        out = structured(await call(h, token, "list_deck_reports", {"deck_id": "42"}))
        trend = out["trend"]
        assert out["ok"] and trend["reports"] == 3 and trend["deck_id"] == "42"
        assert [p["report_id"] for p in trend["series"]] == ["rep_t1", "rep_t2", "rep_t3"]
        by_key = {m["key"]: m for m in trend["metrics"]}
        # salt_total is not stored in these reports, so it is not trended (nothing invented)
        assert list(by_key) == [
            "average_mana_value",
            "land_count",
            "price_total",
            "commander_cast_median_turn",
            "kill_median_turn",
            "kill_pct",
        ]
        mv = by_key["average_mana_value"]
        assert (mv["first"], mv["latest"], mv["change"], mv["reports"]) == (3.2, 3.0, -0.2, 3)
        assert mv["first_at"] == now - 9 * DAY and mv["latest_at"] == now - DAY
        assert by_key["price_total"]["change"] == -19.75
        cast = by_key["commander_cast_median_turn"]
        assert (cast["first"], cast["latest"], cast["change"], cast["reports"]) == (5, 6, 1, 2)
        assert (by_key["kill_pct"]["first"], by_key["kill_pct"]["latest"]) == (57.0, 62.7)
        assert trend["series"][1]["kill_pct"] is None and trend["series"][2]["until_turn"] == 10
        # one report is no trend; without deck_id the tool lists as before
        assert structured(await call(h, token, "list_deck_reports", {"deck_id": "43"}))["trend"] is None
        assert "trend" not in structured(await call(h, token, "list_deck_reports", {}))

        page = await b.http.get("/history?deck_id=42", headers=NAV)
        body = page.text.split("<main")[1]
        assert page.status_code == 200 and "<h2 id='trend-h'>Report trends</h2>" in body
        assert body.count("<svg class='tspark'") == 6 and body.count("role='img'") >= 6
        assert (
            f"aria-label='Average mana value over 3 reports: 3.2 on {first_day}, 3 on {last_day}, down 0.2.'"
        ) in body
        assert "<b>$290.25</b>" in body and "Down $19.75 since $310.00 on" in body
        assert "<b>turn 6</b>" in body and "Up 1 turn since turn 5 on" in body
        assert "Up 5.7 percentage points since 57% on" in body
        # the text table: one row per report, oldest first, a dash where a report has no number
        table = body.split("<table>")[1].split("</table>")[0]
        assert table.index("rep_t1") < table.index("rep_t2") < table.index("rep_t3")
        assert "<td>–</td>" in table and "<th scope='col'>Turns simulated</th>" in table
        # one report: no trend section; nor on the all-decks page
        assert "Report trends" not in (await b.http.get("/history?deck_id=43", headers=NAV)).text
        assert "Report trends" not in (await b.http.get("/history", headers=NAV)).text
    finally:
        await b.aclose()


async def test_trends_never_mix_simulations_run_differently(stack: Stack) -> None:
    """QA-009: every trend point carries its run settings, a simulation number is compared only
    across reports run the same way (a deck that turned from a Commander game at 40 life into a
    60-card game at 20 breaks the series), and the change is listed and shown, never silent."""
    h = stack.h
    b = await linked_browser(stack)
    try:
        token = await mcp_token(h)
        gw = h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        now = int(time.time())
        store_report(gw.db, sub, "rep_c1", taken_at=now - 9 * DAY)
        sixty = {"_run": {"mode": "constructed", "games": 300}}
        store_report(gw.db, sub, "rep_s1", taken_at=now - 5 * DAY, stats=sixty, goldfish=GOLDFISH_PRECON)
        store_report(gw.db, sub, "rep_s2", taken_at=now - DAY, stats=sixty)

        out = structured(await call(h, token, "list_deck_reports", {"deck_id": "42"}))
        trend = out["trend"]
        assert [p["settings"]["mode"] for p in trend["series"]] == ["commander", "constructed", "constructed"]
        assert (
            trend["series"][0]["settings"]["games"] == 300
            and "goldfish" in trend["series"][0]["settings"]["engines"]
        )
        assert trend["settings_changes"] == [
            {
                "report_id": "rep_s1",
                "taken_at": now - 5 * DAY,
                "changed": {"mode": ["commander", "constructed"]},
            }
        ]
        by_key = {m["key"]: m for m in trend["metrics"]}
        kill = by_key["kill_pct"]
        assert kill["reports"] == 2 and kill["left_out"] == 1 and kill["since_report_id"] == "rep_s1"
        assert (kill["first"], kill["latest"]) == (62.7, 57.0)  # the 60-card reports only
        assert "left_out" not in by_key["average_mana_value"]  # the statistics do not depend on it
        # each listed report says how it ran, its status and its engine
        listed = out["reports"][0]
        assert listed["status"] == "complete" and listed["engine_label"] == "Preflight only (no rules engine)"
        assert listed["settings"]["mode"] == "constructed"
        got = structured(await call(h, token, "get_deck_report", {"report_id": "rep_s2"}))
        assert "_run" not in got["stats"] and got["settings"]["mode"] == "constructed"

        page = await b.http.get("/history?deck_id=42", headers=NAV)
        body = page.text.split("<main")[1]
        assert "The run settings changed between these reports" in body
        assert "1 earlier report ran with other settings (game or turns) and is left out." in body
        table = body.split("<table>")[1].split("</table>")[0]
        assert "<th scope='col'>Settings</th>" in table
        assert "Commander · 300 games · to turn 10" in table and "60-card · 300 games" in table
        assert "no rules engine" in table
    finally:
        await b.aclose()


async def test_a_relinked_member_keeps_the_old_accounts_history(stack: Stack) -> None:
    """After relinking to another Archidekt account, the decks of the account linked before are
    not in the new account's list, but they are not gone: they stay with the current decks and
    their snapshots keep Restore. A deck of the account linked now that left its list still is."""
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        link = gw.db.get_link(sub)
        me = {"id": int(link["archidekt_user_id"]), "username": link["archidekt_username"]}
        before = {"id": 999999, "username": "my-old-account"}
        now = int(time.time())

        def snapshot(sid: str, deck: str, name: str, owner: dict) -> None:
            gw.db.save_snapshot(
                sid,
                owner_sub=sub,
                deck_id=deck,
                proposal_id=None,
                fingerprint="f",
                deck={"id": deck, "name": name, "cards": [], "owner": owner},
            )
            with gw.db.tx() as c:
                c.execute("UPDATE snapshots SET taken_at = ? WHERE id = ?", (now - DAY, sid))

        snapshot("s_old_account", "7001", "Old Account Deck", before)
        snapshot("s_mine_gone", "7002", "Mine Deleted", me)
        gw.db.save_proposal(  # a change to the old account's deck, made before the relink
            {
                "id": "p_old_account",
                "owner_sub": sub,
                "kind": "edit",
                "deck_id": "7003",
                "deck_name": "Old Account Other",
                "baseline_fingerprint": "f",
                "changes": [],
                "diff_text": "+1 Sol Ring",
                "expires_at": now + 3600,
            }
        )
        # the old account was linked 60 days ago (the audit log names each account linked)
        gw.db.audit("archidekt_linked", sub=sub, detail={"archidekt_username": before["username"]})
        with gw.db.tx() as c:
            c.execute("UPDATE proposals SET created_at = ? WHERE id = 'p_old_account'", (now - 30 * DAY,))
            c.execute("UPDATE archidekt_links SET created_at = ? WHERE sub = ?", (now - 2 * DAY, sub))
            c.execute(
                "UPDATE audit_log SET at = ? WHERE sub = ? AND event = 'archidekt_linked' "
                "AND detail_json LIKE '%my-old-account%'",
                (now - 60 * DAY, sub),
            )
        page = await b.http.get("/history", headers=NAV)
        body = page.text.split("<main")[1]
        gone = body.split("<optgroup label='No longer on Archidekt'>")[1].split("</optgroup>")[0]
        assert "Mine Deleted" in gone
        assert "Old Account Deck" not in gone and "Old Account Other" not in gone
        assert "<option value='7001'>Old Account Deck</option>" in body
        assert "<option value='7003'>Old Account Other</option>" in body
        snaps = [r for r in _entries(body) if "open=s-" in r]
        (old_row,) = [r for r in snaps if "Old Account Deck" in r]
        (mine_row,) = [r for r in snaps if "Mine Deleted" in r]
        assert "action='/history/restore'" in old_row and "Deleted on Archidekt" not in old_row
        assert "action='/history/restore'" not in mine_row and "Deleted on Archidekt" in mine_row
    finally:
        await b.aclose()


def test_own_decks_are_the_linked_accounts_only(tmp_path: Path) -> None:
    """Which decks of the history count as the linked account's own (the only ones that can
    be called gone). A snapshot names its deck's owner; a proposal counts by the account linked
    when it was made, and every doubt leaves a deck out."""
    db = Database(tmp_path / "g.db")
    now = int(time.time())

    def at(sql: str, when: int, key: str) -> None:
        with db.tx() as c:
            c.execute(sql, (when, key))

    def proposal(pid: str, deck: str, when: int) -> None:
        db.save_proposal(
            {
                "id": pid,
                "owner_sub": "u",
                "kind": "edit",
                "deck_id": deck,
                "deck_name": deck,
                "baseline_fingerprint": "f",
                "changes": [],
                "diff_text": "x",
                "expires_at": now + 3600,
            }
        )
        at("UPDATE proposals SET created_at = ? WHERE id = ?", when, pid)

    def linked(name: str, when: int) -> None:
        db.audit("archidekt_linked", sub="u", detail={"archidekt_username": name})
        at("UPDATE audit_log SET at = ? WHERE id = (SELECT MAX(id) FROM audit_log WHERE ? != '')", when, "x")

    def own(**kw) -> set[str]:
        return db.own_history_deck_ids("u", **{"account_id": "7", "username": "Me", "linked_at": now, **kw})

    for sid, deck, owner in (("s1", "1", {"id": 7}), ("s2", "2", {"id": 8}), ("s3", "3", None)):
        db.save_snapshot(
            sid, owner_sub="u", deck_id=deck, proposal_id=None, fingerprint="f", deck={"owner": owner}
        )
        at("UPDATE snapshots SET taken_at = ? WHERE id = ?", now - 50 * DAY, sid)
    proposal("p_now", "10", now + 1)
    proposal("p_before", "11", now - 40 * DAY)
    # no link recorded at all: one account only, as before this release
    assert own() == {"1", "3", "10", "11"}
    # the same account linked (and relinked): still the member's own
    linked("me", now - 60 * DAY)
    linked("Me", now)
    assert own() == {"1", "3", "10", "11"}
    # another account was linked when "11" was changed (after "3" was snapshotted): not this one's
    linked("someone-else", now - 45 * DAY)
    assert own() == {"1", "3", "10"}
    # an empty username never matches
    assert own(username="") == {"1", "10"}


def test_deck_filter_groups_use_live_names_and_set_gone_decks_apart() -> None:
    from mtg_gateway.history_view import deck_filter_groups

    seen = {"42": "Old name", "43": "Deleted deck", "new": "Brand new", "collection": "x", "44": "Kept"}
    live = [{"id": "42", "name": "New name"}, {"id": "44", "name": ""}]
    own = {"42", "43", "44"}
    current, gone = deck_filter_groups(seen, live, listed={"42", "44"}, own=own)
    assert current == {"42": "New name", "collection": "Your Archidekt collection", "44": "Kept"}
    assert gone == {"43": "Deleted deck"}
    # an incomplete or unreadable list can't tell a deck is gone: it stays with the current ones
    current, gone = deck_filter_groups(seen, live, listed=None, own=own)
    assert gone == {} and current["43"] == "Deleted deck"
    current, gone = deck_filter_groups(seen, None, listed=None, own=own)
    assert gone == {} and current["42"] == "Old name"


def test_only_a_deck_known_as_the_members_can_be_no_longer_on_archidekt() -> None:
    """Bug (found in this release's checks): a report on someone else's public deck (or a copy
    made of it) put that deck under "No longer on Archidekt", since it is never in the member's
    own deck list. Only decks the member's history shows as theirs (a change, restore, snapshot
    or new deck) can be judged gone; any other missing deck stays with the current ones."""
    from mtg_gateway.history_view import deck_filter_groups

    seen = {"42": "Mine", "43": "Mine, deleted", "900": "Someone else's"}
    live = [{"id": "42", "name": "Mine"}]
    current, gone = deck_filter_groups(seen, live, listed={"42"}, own={"42", "43"})
    assert gone == {"43": "Mine, deleted"}
    assert current == {"42": "Mine", "900": "Someone else's"}


def test_the_gone_group_follows_the_complete_list_not_a_later_read() -> None:
    # The names come from one read of the list and the judgement from another (on_archidekt):
    # a deck the judging list holds is never called gone because the names list lacks it.
    from mtg_gateway.history_view import deck_filter_groups

    current, gone = deck_filter_groups({"42": "Mine"}, [], listed={"42"}, own={"42"})
    assert gone == {} and current == {"42": "Mine"}
