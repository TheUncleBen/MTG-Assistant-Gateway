"""The solo test played by Forge on the report page (the report's ``forge_solo`` block, 2026-10-21
mockup 34): a heading over a divider, its label, six widgets, the turn-by-turn table with a damage
bar, "Cards Forge left out" as a table with card menus, one muted line for unfinished games,
queued, running and failed runs like the Forge section's, the jump chip, and the same polling path
as the Forge games (forge_status, forge-live.js). A report without the block renders as before."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
import pytest

from mtg_gateway import report_page as rp
from mtg_gateway import report_view as rv

from .test_history_page import store_report
from .test_report_view import report
from .test_reports_browser import FORGE_DONE
from .test_social_browser import Server

STATIC = Path(__file__).parents[1] / "src" / "mtg_gateway" / "static"

# The Goldfish thread's shape (coordinator, 21 Oct): state, error, games_requested, not_played,
# finished_at and the result's figures; ``unfinished`` games are left out of the other figures.
FORGE_SOLO = {
    "state": "done",
    "error": None,
    "games_requested": 300,
    "not_played": ["Mirror of the Forebears", "Grist, the Hunger Tide // Grist", "Pernicious Deed"],
    "finished_at": 1_760_000_000,
    "result": {
        "games": 300,
        "games_requested": 300,
        "until_turn": 10,
        "opponents": 3,
        "won": 213,
        "won_rate": 0.71,
        "won_rate_95": [0.66, 0.76],
        "median_won_turn": 8,
        "won_turn_range": [6, 10],
        "median_commander_turn": 3,
        "median_first_out_turn": 7,
        "mulligans": 150,
        "lands_by_turn": [1.0, 2.0, 2.9, 3.8, 4.6, 5.3, 5.9, 6.4, 6.8, 7.2],
        "casts_by_turn": [0.6, 1.9, 3.6, 5.6, 7.8, 10.1, 12.4, 14.6, 16.9, 19.1],
        "damage_by_turn": [0.0, 0.3, 1.8, 4.6, 8.9, 13.7, 19.8, 26.1, 31.9, 37.4],
        "unfinished": 4,
    },
}


def _page(solo=FORGE_SOLO, **over) -> str:
    r = report(forge=json.loads(json.dumps(FORGE_DONE)), status="complete", **over)
    if solo is not None:
        r["forge_solo"] = json.loads(json.dumps(solo))
    return rp.report_page_html(r, card_types={"Pernicious Deed": "Enchantment"})


def _section(html: str) -> str:
    """The solo-by-Forge section: from its opening tag to the next top-level section."""
    start = html.find("<section class='rv-sec rv-flat solo-forge' id='rv-solo-forge'")
    assert start >= 0, "no solo-by-Forge section"
    end = html.find("<section class='ui-card rv-sec", start)
    return html[start:end] if end > start else html[start:]


def test_a_report_without_the_block_renders_exactly_as_before() -> None:
    r = report(forge=json.loads(json.dumps(FORGE_DONE)), status="complete")
    before = rp.report_page_html(r)
    assert "rv-solo-forge" not in before and "Solo test by Forge" not in before
    for absent in (None, {}, "junk", []):
        assert rp.report_page_html({**r, "forge_solo": absent}) == before
    assert rp.forge_solo_section_html(None) == "" and rp.forge_solo_section_html({}) == ""
    status = rv.forge_status(r)
    assert "forge_solo_html" not in status and status["live"] is False


def test_the_section_is_a_heading_over_a_divider_with_its_label_and_jump_chip() -> None:
    html = _page()
    sec = _section(html)
    assert sec.startswith("<section class='rv-sec rv-flat solo-forge' id='rv-solo-forge'")
    assert "ui-card" not in sec.split("<div class='rv-sh'>")[0]
    assert "<h2 id='rv-solo-forge-h'>Full-rules solo test by Forge (300 games)</h2>" in sec
    assert "<span class='ui-lbl green'>" in sec and ">300 of 300 played</span>" in sec
    assert (
        "Against opponents that do nothing, up to turn 10: how fast the deck works on its own, not how it "
        "fares against interaction." in sec
    )
    # the first section link, before Forge games; the section itself sits before them
    chips = html.split("aria-label='Sections of this report'>")[1].split("</nav>")[0]
    assert chips.index("#rv-solo-forge'") < chips.index("#rv-forge'") < chips.index("#rv-solo'")
    assert ">Full-rules solo test</a>" in chips
    assert html.index("id='rv-solo-forge'") < html.index("id='rv-forge'") < html.index("id='rv-solo'")
    # the Mystic Forge solo test keeps its own section, now flat too
    assert "<section class='rv-sec rv-flat' id='rv-solo'" in html
    # the page's flat-section style: a divider on top, no panel
    assert ".rv-page .rv-flat{border-top:2px solid var(--card-border)" in rp.REPORT_PAGE_CSS


def test_six_widgets_from_the_result() -> None:
    sec = _section(_page())
    widgets = sec.split("aria-label='Solo test by Forge'>")[1].split("</section>")[0]
    titles = re.findall(r"</svg>([^<]+)</span><span class='big'>", widgets)
    assert titles == [
        "Games played",
        "Wins",
        "Median win turn",
        "Commander cast",
        "First opponent out",
        "Mulligans",
    ], titles
    assert "<span class='big'>300</span><span class='det'>up to turn 10 each</span>" in widgets
    assert "71%<small>213 of 300</small></span><span class='det'>95% interval 66% to 76%</span>" in widgets
    assert "Turn 8</span><span class='det'>wins from turn 6 to 10</span>" in widgets
    assert "Turn 3<small>median</small>" in widgets and "Turn 7<small>median</small>" in widgets
    assert "150<small>in 300 games</small></span><span class='det'>0.5 a game on average</span>" in widgets


def test_the_turn_by_turn_table_keeps_four_columns_and_draws_a_damage_bar() -> None:
    sec = _section(_page())
    assert "<h3>Turn by turn (averages, up to turn 10)</h3>" in sec
    table = sec.split("class='ui-table sf-turns'")[1].split("<h3>Cards Forge left out")[0]
    assert table.count("<div class='tr'>") == 10
    assert (
        "<div class='tr'><span class='t'>1</span><span class='n'>1.0</span><span class='n'>0.6</span>"
        in table
    )
    # no damage yet: the smallest bar, so the column still reads as one curve
    assert "<span class='n'>0.0</span><span class='sf-bc'><span class='bar' style='width:2%'" in table
    # the last turn's damage is the peak: a full bar; turn 5 about a quarter
    assert "<span class='n'>37.4</span><span class='sf-bc'><span class='bar' style='width:100%'" in table
    assert "<span class='n'>8.9</span><span class='sf-bc'><span class='bar' style='width:24%'" in table
    # the four columns stay on a phone; only the bar column goes (CSS)
    assert "--cols-sm:3rem repeat(3,minmax(0,1fr))" in table
    assert "@media (max-width:599.98px){.rv-page .sf-turns .sf-bc{display:none}" in rp.REPORT_PAGE_CSS
    # a dict keyed by turn works too, and a result with no series draws no table
    by_turn = {str(i): float(i) for i in range(1, 11)}
    solo = {**FORGE_SOLO, "result": {**FORGE_SOLO["result"], "lands_by_turn": by_turn}}
    assert "<span class='t'>10</span><span class='n'>10.0</span>" in _section(_page(solo))
    bare = {
        **FORGE_SOLO,
        "result": {k: v for k, v in FORGE_SOLO["result"].items() if not k.endswith("_by_turn")},
    }
    assert "Turn by turn" not in _section(_page(bare))


def test_cards_forge_left_out_is_a_table_with_card_menus_and_known_types() -> None:
    sec = _section(_page())
    assert (
        "<h3>Cards Forge left out <span class='n'>3</span></h3><p class='ui-hint'>Not counted above.</p>"
        in sec
    )
    table = sec.split("class='ui-table rv-cards'")[1]
    rows = re.findall(r"<div class='tr' tabindex='0' role='button' data-card='([^']+)'", table)
    # a double-faced name shows its front face
    assert rows == ["Mirror of the Forebears", "Grist, the Hunger Tide", "Pernicious Deed"]
    assert "<span class='t'>Pernicious Deed</span><span class='s'>Enchantment</span>" in table
    assert "<span class='t'>Mirror of the Forebears</span></span>" in table  # type unknown: name alone
    assert table.count("class='kebab' aria-label='Menu for ") == 3
    # the Forge games' left-out cards get the same table and words
    solo_off = report(forge={**json.loads(json.dumps(FORGE_DONE)), "not_played": ["Opt"]}, status="complete")
    forge = rp.report_page_html(solo_off)
    assert "could not play" not in forge and "so the numbers above do not include them" not in forge
    assert "<h3>Cards Forge left out <span class='n'>1</span></h3>" in forge and "data-card='Opt'" in forge
    # the page's script binds the shared menu to those rows
    js = (STATIC / "report.js").read_text()
    assert ".rv-cards .tr[data-card]" in js and "Card.open(" in js and "MtgMenu" in js


def test_one_muted_line_for_unfinished_games_only_when_there_are_any() -> None:
    sec = _section(_page())
    assert (
        "<p class='ui-hint sf-unfinished'>4 games the time limit stopped before turn 10 or before a win are "
        "left out of these numbers.</p>" in sec
    )
    none = {**FORGE_SOLO, "result": {**FORGE_SOLO["result"], "unfinished": 0}}
    assert "sf-unfinished" not in _section(_page(none))
    one = {**FORGE_SOLO, "result": {**FORGE_SOLO["result"], "unfinished": 1}}
    assert "1 game the time limit stopped before turn 10 or before a win is left out" in _section(_page(one))
    # the old shape's words are not used
    assert "stopped_slow" not in rp.REPORT_PAGE_CSS and "unread" not in sec


def test_queued_running_and_failed_runs_read_like_the_forge_section() -> None:
    queued = _section(
        _page({"state": "queued", "error": "Waiting for Forge.", "games_requested": 300, "not_played": []})
    )
    assert ">Queued</span>" in queued and "<p class='notice'>Waiting for Forge." in queued
    assert "data-forge-live aria-live='polite' data-report-id='rep_test'" in queued
    assert "This section updates by itself when the games finish." in queued
    assert "ui-widgets" not in queued and "Turn by turn" not in queued
    running = _section(_page({"state": "running", "error": "Games are going now.", "games_requested": 300}))
    assert ">Running</span>" in running and "data-forge-live" in running
    failed = _section(
        _page({"state": "failed", "error": "Forge could not start the games.", "not_played": []})
    )
    assert (
        ">Not run</span>" in failed
        and "<p class='notice warn'>Forge could not start the games.</p>" in failed
    )
    assert "data-forge-live" not in failed
    # a run that stopped after some games keeps them, says so, and is not "Not run"
    stopped = {**FORGE_SOLO, "state": "timeout", "error": "The games ran out of time before finishing."}
    stopped["result"] = {**FORGE_SOLO["result"], "games": 120}
    sec = _section(_page(stopped))
    assert ">Stopped early</span>" in sec and "(120 games)" in sec
    assert (
        "Forge stopped after 120 of 300 games. The numbers below count only the 120 played. "
        "The games ran out of time before finishing." in sec
    )
    assert "aria-label='Solo test by Forge'" in sec
    # a state the page does not know still renders, with no words made up
    odd = _section(_page({"state": "lost", "error": "Forge lost this run.", "not_played": []}))
    assert "Forge lost this run." in odd


def test_the_poll_json_carries_the_section_and_stays_live_while_it_runs() -> None:
    r = report(forge=json.loads(json.dumps(FORGE_DONE)), status="complete")
    r["forge_solo"] = {"state": "running", "error": "Games are going now.", "games_requested": 300}
    status = rv.forge_status(r)
    assert (
        status["live"] is True and status["state"] == "done"
    )  # the Forge games are done; the solo test runs
    assert status["forge_solo"] == r["forge_solo"]
    assert (
        "id='rv-solo-forge'" in status["forge_solo_html"] and "data-forge-live" in status["forge_solo_html"]
    )
    r["forge_solo"] = json.loads(json.dumps(FORGE_SOLO))
    done = rv.forge_status(r)
    assert done["live"] is False and "data-forge-live" not in done["forge_solo_html"]
    js = (STATIC / "forge-live.js").read_text()
    assert '{ sel: "#rv-solo-forge", field: "forge_solo_html" }' in js
    assert 'document.querySelector("[data-forge-live]")' in js


def test_the_export_and_markdown_still_render_with_the_block() -> None:
    r = report(forge=json.loads(json.dumps(FORGE_DONE)), status="complete", forge_solo=FORGE_SOLO)
    doc = rv.report_export_html(r)
    assert doc.startswith("<!doctype html>") and "<script" not in doc
    assert "# Deck report" in rv.report_markdown(r)


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _with_solo(server: Server, solo: dict) -> None:
    """The report service hands out the report with the solo-by-Forge block (the Goldfish thread's
    backend stores it; here it is added to what the service reads)."""
    svc = server.app.state.gateway.reports
    orig = svc.__class__.get

    def get(sub: str, rid: str) -> dict:
        out = orig(svc, sub, rid)
        out["forge_solo"] = json.loads(json.dumps(solo))
        return out

    svc.get = get


def test_the_section_refreshes_itself_until_the_solo_run_ends(server: Server) -> None:
    """The same path as the Forge games: a running solo test polls the JSON status route and the
    section is swapped in place once the run is done, without a reload."""
    from playwright.sync_api import sync_playwright

    from .test_scan_browser import _chromium_path

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = server.sign_in_and_link()
    store_report(server.db, "user-1", "rep_solo")
    with server.db.tx() as c:
        c.execute(
            "UPDATE reports SET forge_json = ?, forge_state = 'done' WHERE id = 'rep_solo'",
            (json.dumps(FORGE_DONE),),
        )
    running = {"state": "running", "error": "Games are going now.", "games_requested": 300, "not_played": []}
    _with_solo(server, running)
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    live = h.get("/api/v1/reports/rep_solo/forge", headers={"Accept": "application/json"}).json()
    assert live["live"] is True and "rv-solo-forge" in live["forge_solo_html"]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": 390, "height": 900})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        polls: list[str] = []
        page.on("request", lambda r: polls.append(r.url) if "/api/v1/reports/" in r.url else None)
        page.clock.install()
        page.goto(f"{server.base}/history/reports/rep_solo", wait_until="networkidle")
        assert page.locator("#rv-solo-forge[data-forge-live]").count() == 1
        assert page.locator(".forge[data-forge-live]").count() == 0  # the Forge games are done
        assert "updates by itself" in page.locator("#rv-solo-forge").inner_text()
        page.evaluate("window.__same_page = true")
        _with_solo(server, FORGE_SOLO)
        page.clock.run_for(16000)
        page.wait_for_function("() => !document.querySelector('#rv-solo-forge[data-forge-live]')")
        text = page.locator("#rv-solo-forge").inner_text()
        assert "300 of 300 played" in text and "Running" not in text and "Turn by turn" in text, text
        assert polls and all(u.endswith("/api/v1/reports/rep_solo/forge") for u in polls), polls
        assert page.evaluate("window.__same_page === true")
        # the card rows got the shared menu: the ⋮ opens it
        page.locator("#rv-solo-forge .rv-cards .tr").first.scroll_into_view_if_needed()
        page.locator("#rv-solo-forge .rv-cards .kebab").first.click()
        page.locator(".ctxmenu").wait_for()
        items = page.locator(".ctxmenu").inner_text()
        assert "Open card" in items and "Open on Scryfall" in items and "Copy name" in items, items
        assert not errors, errors
        # nothing wider than the phone
        assert page.evaluate("document.documentElement.scrollWidth <= document.documentElement.clientWidth")
        browser.close()


def test_a_missing_report_gets_the_small_page_look_on_every_report_route(server: Server) -> None:
    sid = server.sign_in_and_link()
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    for path in (
        "/history/reports/rep_nope",
        "/history/reports/rep_nope/export.md",
        "/history/reports/rep_nope/export.html",
    ):
        r = h.get(path)
        assert r.status_code == 404, path
        assert "<h1>Report not found</h1>" in r.text, path
        assert "This report isn't in your history. It may have been deleted." in r.text, path
        assert "<a class='btn btn-primary' href='/history'>Back to History</a>" in r.text, path
        assert "No such report for your account." not in r.text
        assert r.text.count("btn-primary") == 1
    # a real report's export still downloads
    store_report(server.db, "user-1", "rep_real", taken_at=int(time.time()) - 60)
    assert h.get("/history/reports/rep_real/export.md").status_code == 200


async def test_the_section_renders_main_s_real_solo_block() -> None:
    """The block as forge.start_solo_run and refresh really build it (not the agreed fixture):
    a won game, a lost one, one the time limit stopped (``unfinished``), a card Forge lacks. The
    page, the poll JSON, the Markdown and the Goldfish line all read it."""
    from types import SimpleNamespace

    from mtg_gateway import deckpage
    from mtg_gateway.forge import refresh, start_solo_run
    from mtg_gateway.reports import report_status

    from .test_forge import DECK_TEXT, FakeForge, _solo_view

    fake = FakeForge(missing={"Fake Sticker Card"})
    client = fake.client()
    section = await start_solo_run(client, DECK_TEXT, "Liesa", games=4, seed=7)
    slow = {**_solo_view(None, turns=6), "won_turn": None}
    fake.jobs[section["job_id"]].update(
        state="done",
        results=[
            {"game": 1, "winner": 1, "solo": _solo_view(6, turns=6)},
            {"game": 2, "winner": 1, "solo": _solo_view(8, kept=6, turns=8)},
            {"game": 3, "turn_limit": True, "solo": _solo_view(None, turns=10)},
            {"game": 4, "stopped_slow": True, "solo": slow},
        ],
    )
    done = await refresh(client, section)
    assert done["state"] == "done" and done["result"]["unfinished"] == 1 and done["result"]["games"] == 3

    r = report(forge=json.loads(json.dumps(FORGE_DONE)), status=report_status(None, None, None, done))
    r["forge_solo"] = done
    sec = _section(rp.report_page_html(r))
    assert f"{rp.SOLO_FORGE_NAME} (3 games)" in sec and "3 of 4 played" in sec
    assert "67%<small>2 of 3</small>" in sec and "Turn 7</span><span class='det'>wins from turn 6 to 8" in sec
    assert "1 game the time limit stopped before turn 10 or before a win is left out" in sec
    assert "Fake Sticker Card" in sec and "<div class='tr'><span class='t'>10</span>" in sec
    status = rv.forge_status(r)
    assert status["forge_solo"] is done and "rv-solo-forge" in status["forge_solo_html"]
    md = rv.report_markdown(r)
    assert f"## {rv.FORGE_SOLO_HEADING}" in md and "Left out: 1 game" in md

    # the Goldfish line reads ReportService.newest_forge_solo's shape of the same result
    res = done["result"]
    newest = {
        "report_id": "r1",
        "finished_at": int(time.time()),
        "won_rate": res["won_rate"],
        "games": res["games"],
        "median_won_turn": res["median_won_turn"],
    }
    line = deckpage.forge_solo_line(SimpleNamespace(id="42"), newest, csrf=None)
    assert "won 67% of 3 games, median win turn 7" in line and "/history/reports/r1" in line
