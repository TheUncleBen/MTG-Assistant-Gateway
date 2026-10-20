"""Home's Quick access (K8): one §3a widget per section in the app side bar's order, the label
as the section's link, at most two quick starts each; the Proposals widget in the Proposals
page's words with an orange edge while changes wait; Reports and Watchlist widgets (Watchlist
only with watchlists on); "Search Archidekt" plain, so New deck is the one orange button; on a
phone two compact tiles a row with the detail and quick starts hidden."""

from __future__ import annotations

import re
from pathlib import Path

import httpx
import pytest

from mtg_gateway import home

from .test_suggest_browser import Server

NOW = 1_800_000_000.0


def test_the_proposals_line_in_the_proposals_page_words() -> None:
    rows = [
        {"deck_id": "1", "created_at": NOW - 6 * 86400, "expires_at": NOW + 20 * 3600, "status": ""},
        {"deck_id": "1", "created_at": NOW - 3600, "expires_at": NOW + 5 * 86400, "status": "needs_look"},
        {"deck_id": "2", "created_at": NOW - 60, "expires_at": NOW + 5 * 86400, "status": ""},
        {"deck_id": "", "created_at": NOW - 60, "expires_at": NOW + 5 * 86400, "status": ""},
    ]
    assert (
        home.proposals_line(rows, now=NOW)
        == "3 on 2 decks, 1 not tied to a deck · oldest 6 days · next expires in 20 h · 1 needs a look"
    )
    assert home.proposals_line(rows[3:], now=NOW).startswith("1 not tied to a deck · oldest 1 min")
    assert home.proposals_line([], now=NOW) == "Every change is reviewed."


def test_reports_and_watchlist_widgets() -> None:
    latest = {
        "report_id": "r1",
        "deck_name": "Atraxa Superfriends",
        "taken_at": NOW - 7200,
        "win": {"rate": 0.4},
    }
    out = home.reports_widget([latest], latest, 1, now=NOW)
    assert "<a class='lab' href='/reports'>" in out and "1<small>Forge going</small>" in out
    assert "Latest: Atraxa Superfriends, 2 h ago, 40% won against precons" in out
    assert ">Open latest</a>" in out and "href='/history/reports/r1'" in out and ">All reports</a>" in out
    empty = home.reports_widget([], None, 0, now=NOW)
    assert "No reports yet." in empty and "class='big'" not in empty and "Open latest" not in empty
    data = {
        "cards": [
            {"name": "Mana Crypt", "price": 102.0, "currency": "usd", "alerts": {"below": 150.0}},
            {"name": "Sol Ring", "price": 1.0, "currency": "usd", "alerts": {}},
        ],
        "decks": [{"name": "Meren"}],
    }
    w = home.watchlist_widget(data)
    assert "2<small>cards</small> 1<small>deck</small>" in w and "Mana Crypt is below your $150.00" in w
    assert ">Open watchlist</a>" in w


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _labels(page: str) -> list[str]:
    quick = page.split("aria-label='Quick access'>")[1].split("</section>")[0]
    return re.findall(r"<a class='lab' href='[^']+'><svg[^>]*>.*?</svg>([^<]+)</a>", quick)


def test_quick_access_in_the_side_bar_order(server: Server) -> None:
    sid = server.sign_in()
    page = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}).get("/").text
    assert _labels(page) == [
        "Decks",
        "Search",
        "Scan",
        "Collection",
        "Proposals",
        "History",
        "Reports",
        "Watchlist",
        "Guide",
        "Account",
    ]
    assert (
        "<h2>Quick access</h2><span class='ui-sub'>The same sections, in the same order, as the app" in page
    )
    assert ">Card search</a>" in page and "href='/precons'>Precons</a>" in page
    # one History tile, in the one approved wording
    quick = page.split("aria-label='Quick access'>")[1].split("</section>")[0]
    tiles = re.findall(r"<a class='lab' href='/history'>.*?</div>", quick, flags=re.S)
    assert len(tiles) == 1 and "Snapshots, restores, deck reports and backup copies." in tiles[0]
    assert page.count("Snapshots, restores, deck reports and backup copies.") == 1
    # one orange button above the folded connector panel: Search Archidekt is plain
    assert page.split("<details class='connect")[0].count("btn-primary") == 1
    assert "<button class='btn-plain'>" in page and "Search Archidekt</button>" in page
    # phones: two compact tiles a row, the detail and the quick starts hidden
    assert ".hq .wdg .det,.hq .wdg .go{display:none}" in page


def test_no_watchlist_widget_with_watchlists_off(tmp_path: Path) -> None:
    s = Server(tmp_path)
    s.start()
    try:
        sid = s.sign_in()
        page = httpx.Client(base_url=s.base, cookies={"mtg_session": sid}).get("/").text
        assert "Watchlist" not in _labels(page) and "Reports" in _labels(page)
    finally:
        s.stop()
