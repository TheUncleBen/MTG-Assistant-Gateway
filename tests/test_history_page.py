"""The history page (T-045) and the report page (T-041) over the ASGI stack: the lists are
narrowed and paged in SQL so a deck keeps its older entries, the filter bar, the "When" presets
(UTC calendar days), the day and deck groupings render, snapshots keep their Restore button,
and a stored report renders as a designed page with Markdown and HTML exports."""

from __future__ import annotations

import calendar
import json
import re
import time
from pathlib import Path

from mtg_gateway.db import Database

from .test_companion import NAV, Stack, linked_browser
from .test_companion import stack as stack  # noqa: PLC0414  (the fixture)

FIX = Path(__file__).parent / "fixtures" / "reports"
GOLDFISH = (FIX / "goldfish_run.md").read_text()
VALIDATION = (FIX / "validation_issues.md").read_text()
DAY = 86400


def seed_history(db: Database, sub: str, *, now: int) -> None:
    """30 proposals for deck 42 and 30 for deck 43, alternating and spread over 20 days, one
    snapshot per applied proposal, so the user-wide newest 20 hold only part of deck 42's."""
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
) -> None:
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
                    }
                ),
                json.dumps({"tool": "goldfish_run", "ok": True, "text": GOLDFISH}),
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
async def test_history_page_filters_groups_and_pages(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        now = int(time.time())
        seed_history(gw.db, sub, now=now)
        store_report(gw.db, sub, "rep_a", taken_at=now - 100)
        store_report(gw.db, sub, "rep_b", taken_at=now - 2 * DAY)
        # the deck's page shows its own 25 newest entries, including ones older than the user-wide 20
        page = await b.http.get("/history?deck_id=42", headers=NAV)
        assert page.status_code == 200
        body = page.text.split("<main")[1]
        assert body.count("<li class='hrow") == 25
        assert "/proposals/p28'" in body  # the 15th proposal of deck 42: past the old user-wide limit
        assert "Reap the Tides" not in body.split("<section class='history'>")[1]
        assert "Older →" in body and "← Newer" not in body
        # day groups with sticky headings, kind icons, deck links, badges, who, folded details
        assert "<h2 class='day'><span>Today</span>" in body and "Yesterday" in body
        assert "class='hrow k-report'" in body and "class='hrow k-snapshot'" in body and "k-edit" in body
        assert "href='/decks/42'" in body and "badge ok'>applied" in body and ">rejected</span>" in body
        assert "by you" in body and "by an assistant" in body and "app-1" not in body  # no raw app id
        assert "<details><summary>Details</summary>" in body
        assert "Restore (review first)" in body and "the change it was taken before" in body
        assert "Trend over 2 reports" in body and "class='tiles'" in body and "<svg class='spark'" in body
        # the filter bar: themed selects in one form, the deck list from the member's history
        assert "class='card filterbar'" in body and body.count("<select") == 5
        assert "<option value='42' selected>Immortal Reckoning</option>" in body
        assert "<option value='43'>Reap the Tides</option>" in body
        assert (
            "<label for='h-when'>When (UTC days)</label>" in body
            and "<select id='h-when' name='when'>" in body
        )
        assert (
            "<option value='' selected>Any time</option>" in body and "<option value='7d'>Last 7 days" in body
        )
        assert (
            "<label for='h-group'>Group by</label>" in body and "<select id='h-group' name='group'>" in body
        )
        assert "<option value='day' selected>Day</option><option value='deck'>Deck</option>" in body
        assert "class='form-actions'><button type='submit' class='btn-primary'>" in body
        # older page
        older = await b.http.get("/history?deck_id=42&offset=25", headers=NAV)
        obody = older.text.split("<main")[1]
        assert older.status_code == 200 and "← Newer" in obody
        assert 0 < obody.count("<li class='hrow") <= 25
        first_ids = set(re.findall(r"/proposals/(p\d\d)'", body))
        older_ids = set(re.findall(r"/proposals/(p\d\d)'", obody))
        assert first_ids and older_ids and not first_ids & older_ids
        # every deck-42 proposal is reachable across the pages
        seen = first_ids | older_ids
        more = await b.http.get("/history?deck_id=42&offset=50", headers=NAV)
        seen |= set(re.findall(r"/proposals/(p\d\d)'", more.text))
        assert seen == {f"p{i:02d}" for i in range(0, 60, 2)}
        # type, state and search filters
        changes = await b.http.get("/history?type=changes&state=applied", headers=NAV)
        cbody = changes.text.split("<main")[1]
        assert "k-snapshot" not in cbody and "k-report" not in cbody
        assert cbody.count("badge ok'>applied") == cbody.count("<li class='hrow")
        reps = await b.http.get("/history?type=reports", headers=NAV)
        rbody = reps.text.split("<main")[1]
        assert rbody.count("<li class='hrow") == 2 and rbody.count("k-report") == 2
        found = await b.http.get("/history?q=counterspell", headers=NAV)
        fbody = found.text.split("<main")[1]
        assert "Immortal Reckoning" not in fbody.split("<section class='history'>")[1]
        assert "value='counterspell'" in fbody
        nothing = await b.http.get("/history?q=zzzz-nothing", headers=NAV)
        assert "Nothing matches these filters" in nothing.text
        # odd query values fall back to the defaults instead of failing
        odd = await b.http.get(
            "/history?type=bogus&state=nope&offset=abc&when=never&group=colour", headers=NAV
        )
        assert odd.status_code == 200
        obody = odd.text.split("<main")[1]
        assert "<option value='' selected>Any time</option>" in obody
        assert "<option value='day' selected>Day</option>" in obody and "<h2 class='day'><span>Today" in obody
        assert "when=" not in obody and "group=" not in obody  # the pager links carry only real values
    finally:
        await b.aclose()


async def test_history_when_presets(stack: Stack) -> None:
    """The "When" presets narrow every kind by UTC calendar day: "Today" is midnight UTC to now,
    "Last 7 days" the seven days ending today, "This year" from 1 January; the filter is in the
    query string and the pager keeps it; an unknown preset means "Any time"."""
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        now = int(time.time())
        midnight = now - now % DAY  # the start of today, UTC
        jan1 = int(calendar.timegm((time.gmtime(now).tm_year, 1, 1, 0, 0, 0)))

        def proposal(pid: str, at: int, deck: str = "42") -> None:
            gw.db.save_proposal(
                {
                    "id": pid,
                    "owner_sub": sub,
                    "kind": "edit",
                    "deck_id": deck,
                    "deck_name": "Immortal Reckoning" if deck == "42" else "Reap the Tides",
                    "baseline_fingerprint": "f",
                    "changes": [],
                    "diff_text": f"+1 {pid}",
                    "expires_at": now + 3600,
                    "created_by_client": "__browser__",
                }
            )
            with gw.db.tx() as c:
                c.execute("UPDATE proposals SET created_at = ? WHERE id = ?", (at, pid))

        proposal("p_now", now - 5)
        proposal("p_midnight", midnight)  # the first second of today counts as today
        proposal("p_yesterday", midnight - 1, deck="43")  # the last second of yesterday does not
        proposal("p_6d", midnight - 6 * DAY)  # the seventh day back, still "Last 7 days"
        proposal("p_7d", midnight - 7 * DAY - 1)  # the day before: out
        proposal("p_29d", midnight - 29 * DAY)
        proposal("p_89d", midnight - 89 * DAY)
        proposal("p_100d", midnight - 100 * DAY)
        proposal("p_jan1", jan1)
        proposal("p_lastyear", jan1 - 1)
        gw.db.save_snapshot(
            "s_old",
            owner_sub=sub,
            deck_id="42",
            proposal_id=None,
            fingerprint="f",
            deck={"id": "42", "name": "Immortal Reckoning", "cards": []},
        )
        with gw.db.tx() as c:
            c.execute("UPDATE snapshots SET taken_at = ? WHERE id = 's_old'", (midnight - 10 * DAY,))
        store_report(gw.db, sub, "rep_old", taken_at=midnight - 10 * DAY)
        store_report(gw.db, sub, "rep_new", taken_at=now - 50)

        async def ids(query: str) -> set[str]:
            page = await b.http.get(f"/history{query}", headers=NAV)
            assert page.status_code == 200
            body = page.text.split("<main")[1]
            found = set(re.findall(r"/proposals/(p_\w+)'", body))
            if "k-snapshot" in body:
                found.add("s_old")
            found |= set(re.findall(r"/history/reports/(rep_\w+)'", body))
            return found

        everything = {
            "p_now", "p_midnight", "p_yesterday", "p_6d", "p_7d", "p_29d", "p_89d", "p_100d",
            "p_jan1", "p_lastyear", "s_old", "rep_old", "rep_new",
        }  # fmt: skip
        assert await ids("") == everything
        assert await ids("?when=today") == {"p_now", "p_midnight", "rep_new"}
        assert await ids("?when=7d") == {"p_now", "p_midnight", "p_yesterday", "p_6d", "rep_new"}
        assert await ids("?when=30d") == {
            "p_now",
            "p_midnight",
            "p_yesterday",
            "p_6d",
            "p_7d",
            "p_29d",
            "s_old",
            "rep_old",
            "rep_new",
        }
        assert "p_100d" not in await ids("?when=90d") and "p_89d" in await ids("?when=90d")
        assert "s_old" in await ids("?when=90d") and "rep_old" in await ids("?when=90d")
        year = await ids("?when=year")
        assert "p_jan1" in year and "p_lastyear" not in year
        assert await ids("?when=sometime") == everything  # unknown: "Any time"
        # the preset combines with the other filters and sits in the query string of every link
        assert await ids("?when=7d&deck_id=43") == {"p_yesterday"}
        assert await ids("?when=7d&type=reports") == {"rep_new"}
        page = await b.http.get("/history?when=7d&type=changes", headers=NAV)
        body = page.text.split("<main")[1]
        assert "<option value='7d' selected>Last 7 days</option>" in body
        assert "Backup copies" not in body  # a narrowed page has no backups panel
        for i in range(30):
            proposal(f"p_fill{i}", now - 100 - i)
        paged = await b.http.get("/history?when=today", headers=NAV)
        pbody = paged.text.split("<main")[1]
        assert "href='/history?when=today&offset=25'>Older →</a>" in pbody
        older = await b.http.get("/history?when=today&offset=25", headers=NAV)
        obody = older.text.split("<main")[1]
        assert "href='/history?when=today'>← Newer</a>" in obody and "p_yesterday" not in obody
    finally:
        await b.aclose()


async def test_history_grouped_by_deck(stack: Stack) -> None:
    """group=deck: one section per deck headed by its name (linked to the deck page and to its own
    history with the same filters), decks in the order of their newest entry, rows newest first
    with their day shown; the filters, folded details, Restore and the pager keep working."""
    b = await linked_browser(stack)
    try:
        gw = stack.h.app.state.gateway
        sub = gw.db._one("SELECT sub FROM users", ())["sub"]
        now = int(time.time())
        seed_history(gw.db, sub, now=now)  # p00 (deck 42) is the newest, p01 (deck 43) next
        store_report(gw.db, sub, "rep_a", deck_id="43", deck_name="Reap the Tides", taken_at=now - 10)
        page = await b.http.get("/history?group=deck", headers=NAV)
        assert page.status_code == 200
        body = page.text.split("<main")[1]
        listing = body.split("<section class='history'>")[1].split("</section>")[0]
        assert "<option value='deck' selected>Deck</option>" in body
        heads = re.findall(r"<h2 class='day deck'><span><a href='/decks/(\d+)'>([^<]+)</a></span>", listing)
        assert heads == [("43", "Reap the Tides"), ("42", "Immortal Reckoning")]
        assert "href='/history?deck_id=43&amp;group=deck'>only this deck</a>" in listing
        assert listing.count("<li class='hrow") == 25 and listing.count("<h2 class='day") == 2
        # each section's rows are newest first, and no row of the other deck is inside
        sec43, sec42 = listing.split("<h2 class='day deck'>")[1:]
        assert "Immortal Reckoning" not in sec43.split("</h2>")[1]
        assert "Reap the Tides" not in sec42.split("</h2>")[1]
        assert "/history/reports/rep_a'" in sec43 and sec43.index("rep_a") < sec43.index("/proposals/p01'")
        p42 = re.findall(r"/proposals/(p\d\d)'", sec42)
        assert p42 == sorted(p42)
        assert "Yesterday" not in listing and re.search(
            r"<time datetime='\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ'>\d{4}-\d\d-\d\d \d\d:\d\d UTC</time>", listing
        )
        assert "<details><summary>Details</summary>" in listing and "Restore (review first)" in listing
        assert "href='/history?group=deck&offset=25'>Older →</a>" in body
        # paging stays by entries, the filters combine, and a deck's own page is one section
        older = await b.http.get("/history?group=deck&offset=25", headers=NAV)
        obody = older.text.split("<main")[1]
        assert "← Newer" in obody and 0 < obody.count("<li class='hrow") <= 25
        own = await b.http.get("/history?group=deck&deck_id=42&type=changes&state=applied", headers=NAV)
        own_body = own.text.split("<main")[1]
        assert own_body.count("<h2 class='day") == 1 and "only this deck" not in own_body
        assert own_body.count("badge ok'>applied") == own_body.count("<li class='hrow") == 15
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
            "<dt>Commander</dt><dd>Liesa, Forgotten Archangel</dd>",
            "300, through turn 10 (seed 42)",
            "report is shown instead of running the simulation again",
            "class='tiles wide'",
            "class='ci'",
            "class='charts'",
            "<polyline class='s1'",
            "<rect class='col'",
            "What the simulation could not model",
            "32 of 98 cards (33% of the deck)",
            "Validation <span class='badge warn'>issues found</span>",
            "Deck statistics",
            "class='bars'",
            "Raw output from the research service",
            "<a class='btn' href='/history/reports/rep_real/export.md' download>",
            "<a class='btn' href='/history/reports/rep_real/export.html' download>",
            "class='btn copybtn' data-copy='rep-md'",
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
        assert md.text.startswith("# Deck report: Immortal Reckoning") and "## Metrics" in md.text
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
        assert "data-busy-label='Simulating…'" in deck.text and "takes up to a minute" in deck.text
        assert "run_deck_report" not in deck.text
    finally:
        await b.aclose()
