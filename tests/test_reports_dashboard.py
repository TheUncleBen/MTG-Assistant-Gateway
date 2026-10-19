"""The Reports page as the approved dashboard (K4): widgets, chips with counts, the search over
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
    assert "<i aria-hidden='true'>C</i>Claude" in page and "<i aria-hidden='true'>Y</i>You" in page
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
