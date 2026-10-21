"""The Reports page as a dashboard: widgets, chips with counts, the search over
deck and commander, the deck, asked-by and when filters, the order, paging, and each row's
numbers (Forge's win rate, the solo test's lethal turn, the app that asked)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import httpx
import pytest

from .test_history_page import store_report
from .test_reports_browser import FORGE_DONE
from .test_social_browser import Server


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _seed(s: Server) -> httpx.Client:
    sid = s.sign_in_and_link()
    now = int(time.time())
    db = s.db
    db.save_client("claude-app", {"client_name": "Claude"})
    store_report(
        db,
        "user-1",
        "r_new",
        deck_id="44",
        deck_name="Atraxa Superfriends",
        taken_at=now - 600,
        stats={"commanders": ["Atraxa, Praetors' Voice"]},
    )
    store_report(
        db,
        "user-1",
        "r_mid",
        deck_id="45",
        deck_name="Krenko Goblin Swarm",
        taken_at=now - 3 * 86400,
        stats={"commanders": ["Krenko, Mob Boss"]},
    )
    store_report(
        db,
        "user-1",
        "r_old",
        deck_id="44",
        deck_name="Atraxa Superfriends",
        taken_at=now - 20 * 86400,
        stats={"commanders": ["Atraxa, Praetors' Voice"]},
        goldfish=None,
    )
    failed = {"state": "failed", "job_id": "2" * 16, "not_played": [], "error": "Forge could not start."}
    done = json.loads(json.dumps(FORGE_DONE))
    with db.tx() as c:
        c.execute(
            "UPDATE reports SET forge_json = ?, forge_state = 'done' WHERE id = 'r_new'", (json.dumps(done),)
        )
        c.execute(
            "UPDATE reports SET forge_json = ?, forge_state = 'failed', created_by_client = 'claude-app' "
            "WHERE id = 'r_mid'",
            (json.dumps(failed),),
        )
    return httpx.Client(base_url=s.base, cookies={"mtg_session": sid})


def _rows(text: str) -> int:
    return text.count("<div class='tr'>")


def test_reports_dashboard_widgets_chips_filters_and_rows(server: Server) -> None:
    h = _seed(server)
    page = h.get("/reports").text
    # the head and the four widgets
    assert "Every deck report you or your assistants ran, newest first." in page
    for title in ("Forge now", "Latest report", "Partial or failed", "Decks tested"):
        assert title in page
    assert "Nothing plays or waits now." in page
    assert "won against precons" in page  # the latest report's Forge win rate
    assert "Krenko Goblin Swarm, 3 days ago: the Forge games could not run" in page
    assert "class='wdg alert'" in page and "/reports?show=problem" in page
    assert "Trends in History" in page and "2<small>decks · 3 reports</small>" in page
    # the chips count under the filters; the table; the pager says how many
    assert "aria-current='true'>All <span class='n'>3</span>" in page
    assert "Partial or failed <span class='n'>1</span>" in page
    assert "With Forge games <span class='n'>1</span>" in page
    assert _rows(page) == 3 and "1–3 of 3" in page
    # each row: the status label, the win rate with its interval, the lethal turn, who asked
    assert "class='ui-lbl orange'" in page and ">Partial</span>" in page
    assert "95% interval" in page and "turn 10" in page
    # the column header does not assume 40 damage: a constructed deck's report counts 20
    head = page.split("class='ui-table rp-t'")[1].split("<div class='tr'>")[0]
    assert ">Lethal by<" in head and "40 damage" not in head
    assert "<span class='nm'>Claude</span>" in page and "<span class='nm'>You</span>" in page
    assert "no solo test" in page and "did not run" in page
    assert "Deck trend and history" not in page
    # chips, search (commander too), filters, order
    assert _rows(h.get("/reports?show=problem").text) == 1
    assert _rows(h.get("/reports?show=forge").text) == 1
    assert _rows(h.get("/reports?q=mob+boss").text) == 1
    assert _rows(h.get("/reports?q=atraxa").text) == 2
    assert _rows(h.get("/reports?deck_id=44").text) == 2
    assert _rows(h.get("/reports?by=claude").text) == 1
    assert _rows(h.get("/reports?by=you").text) == 2
    assert _rows(h.get("/reports?when=7").text) == 2
    assert _rows(h.get("/reports?when=today").text) <= 1
    oldest = h.get("/reports?order=oldest").text
    table = oldest[oldest.index("class='ui-table rp-t'") :]
    assert table.index("r_old") < table.index("r_new") and "Oldest first" in oldest
    none = h.get("/reports?q=nothing+like+this").text
    assert "No reports match. Clear the search or the filters to see them all." in none
    assert "<option value='claude'>Claude</option>" in page and "<option value='you'>You</option>" in page


def test_a_report_with_no_stored_name_is_an_unnamed_deck(server: Server) -> None:
    """A report stores its own deck name; when none was stored the row and the Deck filter say
    "Unnamed deck", never the deck's id."""
    h = _seed(server)
    store_report(server.db, "user-1", "r_none", deck_id="99", deck_name="", taken_at=int(time.time()) - 30)
    page = h.get("/reports").text
    assert "href='/history/reports/r_none'>Unnamed deck</a>" in page
    assert "<option value='99'>Unnamed deck</option>" in page and "Deck 99" not in page


def test_the_widgets_say_unnamed_deck_for_a_report_with_no_name() -> None:
    from mtg_gateway import reports_page

    latest = {
        "report_id": "r9",
        "deck_id": "77",
        "deck_name": "",
        "status": "complete",
        "taken_at": 1_800_000_000,
    }
    out = reports_page.latest_widget(latest, now=1_800_000_100.0)
    assert ">Unnamed deck</a>" in out and ">Deck<" not in out and "77</a>" not in out
    trend = {"deck_id": "77", "deck_name": "", "latest": 5, "previous": None}
    tested = reports_page.decks_widget([{"deck_id": "77"}], [trend])
    assert "<li><span>Unnamed deck</span>" in tested and "<span>Deck</span>" not in tested


def test_reports_dashboard_pages_by_25(server: Server) -> None:
    sid = server.sign_in_and_link()
    now = int(time.time())
    for i in range(30):
        store_report(server.db, "user-1", f"r{i:02d}", taken_at=now - 1000 - i)
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    first = h.get("/reports").text
    assert _rows(first) == 25 and "1–25 of 30" in first and "offset=25" in first
    second = h.get("/reports?offset=25").text
    assert _rows(second) == 5 and "26–30 of 30" in second
    assert h.get("/reports?offset=50").text.count("No older reports.") == 1


@pytest.mark.parametrize("width", [390, 1280])
def test_a_filter_applies_on_pick_with_no_show_button(server: Server, width: int) -> None:
    """Ben's request (2026-10-19, 22:16Z): a drop-down filter applies as soon as an option is
    picked; the toolbar has no Show (or Search) button for a member with scripts."""
    from playwright.sync_api import sync_playwright

    from .test_scan_browser import _chromium_path

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    h = _seed(server)
    sid = h.cookies["mtg_session"]
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        touch = {"has_touch": True, "is_mobile": True} if width < 600 else {}
        ctx = browser.new_context(viewport={"width": width, "height": 900}, **touch)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        page.goto(f"{server.base}/reports", wait_until="networkidle")
        visible = page.evaluate(
            "[...document.querySelectorAll('.ui-tools button:not(.msel-btn), .ui-tools [type=submit]')]"
            ".filter(e => e.getClientRects().length).map(e => e.textContent.trim())"
        )
        assert visible == []
        page.locator(".ui-tools .msel-btn").first.click()
        page.locator(".msel-list:visible [role=option]", has_text="Atraxa Superfriends").click()
        page.wait_for_url("**deck_id=44**", timeout=5000)
        assert "Atraxa Superfriends" in page.locator(".ui-tools .msel-btn").first.inner_text()
        browser.close()
