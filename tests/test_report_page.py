"""One report as a page: crumbs with All reports, the status as a label in
the meta line, six key-number widgets, the section links, flat sections, the "could not model"
groups folded with the first open, the validation label, a red-outline Remove and one orange
Play more games button; the nav marks Reports."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

from mtg_gateway import report_page as rp
from mtg_gateway import report_view as rv

from .test_history_page import store_report
from .test_report_view import GOLDFISH, VALIDATION_PASSED, report
from .test_reports_browser import FORGE_DONE
from .test_social_browser import Server


def _page(**over) -> str:
    r = report(forge=json.loads(json.dumps(FORGE_DONE)), status="complete", **over)
    r["stats"] = {**r["stats"], "price_total": 344.0, "game_changers": []}
    return rp.report_page_html(r, stats_html="<div class='tiles'>stats</div>")


def test_head_meta_and_crumbs() -> None:
    html = _page()
    assert "<a href='/reports'>All reports</a>" in html and "← Back to the deck" in html
    assert "<span id='rep-status'><span class='ui-lbl green'>" in html and ">Complete</span>" in html
    assert "<span class='k'>Solo test games</span> 300, through turn 10" in html
    assert "<span class='k'>Commander</span> Liesa, Forgotten Archangel" in html
    assert "<span class='k'>Run</span> <time class='ui-when'" in html


def test_six_widgets_from_the_report() -> None:
    html = _page()
    widgets = html.split("aria-label='Key numbers'>")[1].split("</section>")[0]
    titles = re.findall(r"</svg>([^<]+)</span><span class='big'>", widgets)
    assert titles == [
        "Forge: your deck won",
        "Commander cast",
        "40 damage dealt by turn 10",
        "Played with their effect",
        "Validation",
        "Deck",
    ], titles
    assert "40%<small>of 10 games</small>" in widgets and "class='ci-w'" in widgets
    assert "95% interval 17% to 69% · against 3 precons" in widgets
    # under 70% of the deck played with its effect: the orange warning edge
    assert "<div class='wdg accent'><span class='lab'>" in widgets and "50%<small>49 of 98 cards" in widgets
    assert "32 played without their effect · 17 left out on purpose" in widgets
    # issues found: a red edge and the count
    assert "<div class='wdg alert'>" in widgets and "2<small>issues found</small>" in widgets
    assert "$344.00<small>100 cards</small>" in widgets and "34 lands · Avg MV 3.2" in widgets
    for href in ("#rv-forge", "#rv-solo", "#rv-model", "#rv-valid", "#rv-stats"):
        assert f"href='{href}'" in widgets and f"id='{href[1:]}'" in html


def test_a_passed_validation_keeps_its_widget_green() -> None:
    html = _page(validation=VALIDATION_PASSED)
    widgets = html.split("aria-label='Key numbers'>")[1].split("</section>")[0]
    assert "<span class='big'><span class='ui-lbl green'>" in widgets and ">Passed</span>" in widgets
    assert "class='wdg alert'" not in widgets


def test_well_modelled_deck_has_no_orange_edge(monkeypatch: pytest.MonkeyPatch) -> None:
    g = rv.parse_goldfish({"ok": True, "text": GOLDFISH}, deck_size=100)
    assert rv.coverage(g)["pct"] == 50 < rp.COVERAGE_WARN  # the fixture deck: 50%, the orange edge
    monkeypatch.setattr(rp, "COVERAGE_WARN", 50)  # at or over the share: no edge
    html = rp.report_page_html(report())
    assert "Played with their effect" in html and "class='wdg accent'" not in html


def test_sections_are_flat_and_the_groups_fold() -> None:
    html = _page()
    links = html.split("aria-label='Sections of this report'>")[1].split("</nav>")[0]
    assert re.findall(r"href='#([\w-]+)'>([^<]+)", links) == [
        ("rv-forge", "Forge games"),
        ("rv-solo", "Solo test"),
        ("rv-model", "What it could not model "),
        ("rv-valid", "Validation"),
        ("rv-stats", "Deck statistics"),
        ("rv-raw", "Raw output"),
    ]
    assert "<span class='n'>74</span>" in links  # 32 + 17 + 25 cards in the groups
    assert ">10 of 10 played</span>" in html  # the Forge state as a label
    assert "<h3>About these Forge games</h3>" in html and "class='ui-table rv-seats'" in html
    assert "<div class='tr mine'>" in html and ">your deck</span>" in html
    groups = re.findall(r"<details class='rv-grp[^']*'( open)?>", html)
    assert groups[0] == " open" and all(g == "" for g in groups[1:]) and len(groups) == 4
    assert "<span class='n'>32 of 98 cards (33% of the deck)</span>" in html
    assert "class='bar-split rv-cov-bar big'" in html and "Left out on purpose <b>17</b>" in html
    assert "Validation</h2><span class='ui-lbl orange'>" in html and ">Issues found</span>" in html
    assert "<div class='card" not in html  # no boxed cards inside the page


def test_one_orange_button_and_a_red_outline_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_gateway import card_models

    monkeypatch.setattr(card_models, "SWITCHED_ON", True)
    r = report(forge={**FORGE_DONE, "more_games": 10}, status="complete")
    r["goldfish"] = {**r["goldfish"], "kept_models": [{"name": "Sol Ring", "changed_at": 1}]}
    html = rp.report_page_html(r, csrf="tok", removable=frozenset({"sol ring"}))
    assert html.count("btn-primary") == 1
    assert "<div id='forge-more' class='rv-more'>" in html and "class='btn-primary'>" in html
    assert "class='btn-outline-danger' aria-label='Remove the saved description of Sol Ring'" in html


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_the_page_marks_reports_in_the_nav_and_is_wide(server: Server) -> None:
    sid = server.sign_in_and_link()
    store_report(server.db, "user-1", "rep_nav")
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    page = h.get("/history/reports/rep_nav").text
    assert re.search(r"<a href='/reports'[^>]*aria-current=.?page", page), "Reports is the current section"
    assert not re.search(r"<a href='/history'[^>]*aria-current", page)
    assert "class='wrap ui-wide'" in page
    # the live status route draws the same label and section as the page
    live = h.get("/api/v1/reports/rep_nav/forge", headers={"Accept": "application/json"}).json()
    assert "rv-forge" in live["forge_html"] and "ui-lbl" in live["status_html"]
