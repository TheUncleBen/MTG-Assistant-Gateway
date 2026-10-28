"""Reports, History and Activity say what happened, in words (0.7.46): who ran a report, no link to a
deleted one, counts instead of times, no lands among the cards that "never mattered", a done Forge
solo test with no counted game says why, a winner outside the seats is unread, a delete goes back
where it started and a refused restore has a way on, curly quotes, "No price" for a deck with none,
and one deck by its newest name."""

from __future__ import annotations

import asyncio
import json
import re
import time

from mtg_gateway import forge
from mtg_gateway.companion import stats_strip
from mtg_gateway.decks import current_client
from mtg_gateway.report_page import forge_solo_section_html, widgets_html
from mtg_gateway.report_view import forge_lines, parse_goldfish, report_sections
from mtg_gateway.reports_page import run_text, status_label

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_forge import BRACKETED, FakeForge
from .test_history_page import store_report

stack_options = stack_with(COMPANION)


def _sub(gw) -> str:  # noqa: ANN001
    return gw.db._one("SELECT sub FROM users", ())["sub"]


def _csrf(text: str) -> str:
    m = re.search(r"name='csrf' value='([0-9a-f]+)'", text)
    assert m, text
    return m.group(1)


async def _until(check) -> None:  # noqa: ANN001
    for _ in range(400):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("never happened")


# -- who ran it, and no link to a deleted report -------------------------------------------
async def test_activity_names_the_website_and_never_links_a_deleted_report(stack: Stack) -> None:
    gw = stack.h.app.state.gateway
    b = await linked_browser(stack)
    try:
        sub = _sub(gw)
        fake = FakeForge(brackets=BRACKETED)
        gw.reports.forge, gw.reports.forge_interval = fake.client(), 0.05
        current_client.set(None)  # nothing left over from the sign-in
        out = await gw.reports.run(sub, "42", simulate=False, forge=True)
        rid = out["report_id"]

        def section() -> dict:
            row = gw.db._conn.execute("SELECT forge_json FROM reports WHERE id = ?", (rid,)).fetchone()
            return json.loads(row["forge_json"])

        await _until(lambda: section().get("job_id"))
        fake.finish(section()["job_id"], [1, 2, 1, 1])
        await _until(lambda: section().get("state") == "done")
        page = (await b.http.get(f"/history/reports/{rid}", headers=NAV)).text
        current_client.set(None)
        more = await b.http.post(f"/history/reports/{rid}/forge/more", data={"csrf": _csrf(page)})
        assert more.status_code == 303, more.text
        (row,) = [e for e in gw.db.audit_for_user(sub, 50) if e["event"] == "report_more_games"]
        assert row["client_id"] == "__browser__", row
        current_client.set(None)
        gone = await b.http.post(f"/history/reports/{rid}/delete", data={"csrf": _csrf(page)})
        assert gone.status_code == 303
        (row,) = [e for e in gw.db.audit_for_user(sub, 50) if e["event"] == "report_deleted"]
        assert row["client_id"] == "__browser__" and row["detail"]["deck_id"] == "42", row
        activity = (await b.http.get("/activity", headers=NAV)).text
        assert "Report deleted" in activity
        assert f"/history/reports/{rid}'" not in activity.split("Report deleted", 1)[1][:1500]
    finally:
        await b.aclose()


# -- after a delete, back where it started; a refused restore has a way on -------------------
async def test_a_delete_from_the_report_page_goes_back_to_the_list_it_came_from(stack: Stack) -> None:
    gw = stack.h.app.state.gateway
    b = await linked_browser(stack)
    try:
        sub = _sub(gw)
        store_report(gw.db, sub, "rep_a", taken_at=int(time.time()) - 60)
        store_report(gw.db, sub, "rep_b", taken_at=int(time.time()) - 90)
        came = {**NAV, "Referer": "http://testserver/reports?show=complete"}
        page = (await b.http.get("/history/reports/rep_a", headers=came)).text
        assert "name='back' value='/reports?show=complete'" in page
        r = await b.http.post(
            "/history/reports/rep_a/delete", data={"csrf": _csrf(page), "back": "/reports?show=complete"}
        )
        assert r.headers["location"] == "/reports?show=complete&deleted=report", r.headers
        listed = (await b.http.get(r.headers["location"], headers=NAV)).text
        assert "The report was deleted." in listed
        # from the deck's page: the deck's history; never another site
        page = (
            await b.http.get(
                "/history/reports/rep_b", headers={**NAV, "Referer": "http://testserver/decks/42"}
            )
        ).text
        assert "name='back' value='/history?deck_id=42'" in page
        r = await b.http.post(
            "/history/reports/rep_b/delete",
            data={"csrf": _csrf(page), "back": "https://evil.example/reports"},
        )
        assert r.headers["location"] == "/history?deleted=report", r.headers
    finally:
        await b.aclose()


async def test_a_refused_restore_offers_a_way_back(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        csrf = await b.csrf("/account/details")
        r = await b.http.post(
            "/history/restore", data={"csrf": csrf, "snapshot_id": "snap_nope"}, headers=NAV
        )
        assert r.status_code == 400
        main = r.text.split("<main", 1)[1]
        assert "href='/history?open=s-snap_nope'" in main and "Back to the snapshot" in main, main
        assert "href='/history'" in main and "Back to History" in main
    finally:
        await b.aclose()


# -- counts, never times; the status of a partial run ----------------------------------------
def test_unfinished_forge_runs_are_told_in_counts() -> None:
    assert (
        run_text({"waiting": True, "position": 2, "expected_start_seconds": 600})
        == "Waiting: number 2 in line"
    )
    assert (
        run_text({"games_done": 3, "games_requested": 10, "expected_seconds": 420}) == "3 of 10 games played"
    )
    lines = forge_lines(
        {"state": "running", "games_requested": 10, "expected_seconds": 900, "result": {"games": 2}}
    )
    assert not any("minute" in line for line in lines), lines


def test_a_failed_solo_test_reads_partial_while_forge_plays() -> None:
    assert ">Partial, Forge running<" in status_label("running", research="failed")
    assert ">Partial, Forge waiting<" in status_label("queued", research="failed")
    assert ">Solo test running<" in status_label("running", research="running")
    assert ">Forge running<" in status_label("running")


# -- what the solo test's lists say ---------------------------------------------------------
def test_lands_and_mana_makers_never_read_as_cards_that_never_mattered() -> None:
    honesty = {"modeled_low_impact": ["Plains", "Charcoal Diamond", "Orzhov Guildgate", "Lightning Greaves"]}
    text = (
        "# Goldfish run\n\n## Metrics\n```json\n"
        + json.dumps({"n": 10, "metrics": {"honesty": honesty}})
        + "\n```"
    )
    block = {"tool": "goldfish_run", "ok": True, "text": text}
    # a report made before the gateway kept its mana cards: the basic lands at least
    assert parse_goldfish(block)["honesty"]["low_impact"] == [
        "Charcoal Diamond",
        "Orzhov Guildgate",
        "Lightning Greaves",
    ]
    kept = {**block, "mana_cards": ["Charcoal Diamond", "Orzhov Guildgate", "Plains"]}
    assert parse_goldfish(kept)["honesty"]["low_impact"] == ["Lightning Greaves"]


async def test_a_run_keeps_which_of_its_cards_make_mana(stack: Stack) -> None:
    gw = stack.h.app.state.gateway
    b = await linked_browser(stack)
    await b.aclose()
    out = await gw.reports.run(_sub(gw), "42", forge=False)
    mana = out["goldfish"]["mana_cards"]
    deck = await gw.decks.get_any_deck(_sub(gw), "42")
    lands = {c.name for c in deck.cards if "Land" in c.types and deck.in_deck(c)}
    assert lands and lands <= set(mana), (lands, mana)


def test_a_done_forge_solo_test_with_no_counted_game_says_why() -> None:
    html = forge_solo_section_html(
        {"state": "done", "result": {"games": 0, "unfinished": 3, "games_requested": 3}}
    )
    assert (
        "No game reached a result, so there are no numbers. 3 games were stopped by the time limit." in html
    )
    assert ">Done<" not in html


def test_a_winner_outside_the_seats_is_unread() -> None:
    job = {"results": [{"winner": 0}, {"winner": 1}, {"winner": 5}], "games_requested": 3}
    out = forge.summarise(job, ["Mine", "Them"])
    assert out["ended"]["won"] == 1 and out["ended"]["unread"] == 2, out["ended"]
    assert [g["ended"] for g in out["game_list"]] == ["unread", "won", "unread"]


# -- quotes, prices and names --------------------------------------------------------------
async def test_history_names_the_backup_folder_in_curly_quotes(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/history", headers=NAV)).text
        assert 'In the folder "' not in page and "In the folder “" in page
    finally:
        await b.aclose()


def test_a_deck_with_no_prices_reads_no_price() -> None:
    stats = {"card_count": 100, "land_count": 36, "price_total": 0.0, "priced_cards": 0}
    assert ">$0<" not in stats_strip(stats) and "No price" in stats_strip(stats)
    r = {"report_id": "rep_x", "stats": stats}
    widgets = widgets_html(r, report_sections(r))
    assert "$0.00" not in widgets and "No price" in widgets, widgets
    assert "$310.00" in widgets_html(
        {"stats": {**stats, "price_total": 310.0, "priced_cards": 90}}, report_sections({})
    )


async def test_history_and_activity_name_a_deck_by_its_newest_name(stack: Stack) -> None:
    gw = stack.h.app.state.gateway
    b = await linked_browser(stack)
    try:
        sub = _sub(gw)
        gw.db.save_snapshot(
            "snap_old",
            owner_sub=sub,
            deck_id="42",
            proposal_id=None,
            fingerprint="f",
            deck={"id": "42", "name": "Old Name", "cards": []},
        )
        with gw.db.tx() as c:
            c.execute("UPDATE snapshots SET taken_at = ? WHERE id = 'snap_old'", (int(time.time()) - 3000,))
        store_report(gw.db, sub, "rep_new", deck_name="New Name", taken_at=int(time.time()) - 60)
        assert gw.db.history_decks(sub)["42"] == "New Name"
    finally:
        await b.aclose()
