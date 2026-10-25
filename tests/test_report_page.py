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

from mtg_gateway import card_models_view
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
    assert "32 played without their effect · 17 need opponents" in widgets
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


def test_sections_are_flat_and_the_groups_are_inset_lists() -> None:
    html = _page()
    links = html.split("aria-label='Sections of this report'>")[1].split("</nav>")[0]
    assert re.findall(r"href='#([\w-]+)'>([^<]+)", links) == [
        ("rv-forge", "Forge games"),
        ("rv-solo", "Quick solo test"),
        ("rv-model", "What the quick test leaves out "),
        ("rv-valid", "Validation"),
        ("rv-stats", "Deck statistics"),
        ("rv-raw", "Raw output"),
    ]
    assert "<span class='n'>74</span>" in links  # 32 + 17 + 25 cards in the groups
    assert ">10 of 10 played</span>" in html  # the Forge state as a label
    assert "<h3>About these Forge games</h3>" in html and "class='ui-table rv-seats'" in html
    assert "<div class='tr mine'>" in html and ">your deck</span>" in html
    model = html.split("id='rv-model'")[1].split("id='rv-valid'")[0]
    assert "rv-grp" not in model and "<details class='ig-info'" in model
    # the groups in order, each a heading, its count and Details (its explanation folded away)
    heads = re.findall(r"<h3 id='rv-g-[\w]+-h'>([^<]+)</h3><span class='n'>([^<]+)</span>", model)
    assert heads == [
        ("Played without their effect", "32 of 98 cards (33% of the deck)"),
        ("Needs opponents", "17 of 98 cards (17% of the deck)"),
        ("Played with their effect but never mattered", heads[2][1]),
        ("Shortcuts the model takes", heads[3][1]),
    ]
    assert model.count("<span class='disc'>Details") == 4
    assert model.count("<details class='ig-info'>") == 4  # all closed: no wall of text
    assert "<div class='ig-blurb'><p>The solo test found these cards and their exact text" in model
    assert "Many cards need things the solo test does not model" in model
    # the summary card: the bar, then the three counts in the groups' order, two linking to them
    rows = re.findall(r"<span class='nm'>([^<]+)</span><span class='v'><b>(\d+)</b>", model)
    assert rows == [
        ("Played with their effect", "49"),
        (rv.UNMODELED_HEADING, "32"),
        ("Needs opponents", "17"),
    ]
    assert "href='#rv-g-unmodeled'" in model and "href='#rv-g-opp'" in model
    assert "class='bar-split rv-cov-bar big'" in model and "Known: 66 of 98 cards (67% of the deck)" in model
    assert "Not in the quick model yet" not in html and "left out on purpose" not in html
    assert "Validation</h2><span class='ui-lbl orange'>" in html and ">Issues found</span>" in html
    assert "<div class='card" not in html  # no boxed cards inside the page


def test_card_lists_keep_even_rows_and_name_the_commander() -> None:
    """A list with an odd number of cards ends with an empty partner cell (the desktop grid's two
    columns stay whole), and a commander, which starts in the command zone, says so instead of
    "drawn 0%"."""
    g = {
        "deck_size": 100,
        "honesty": {
            "unrecognized": [{"name": "A", "drawn": 0.2}, {"name": "B", "drawn": 0.1}, {"name": "C"}],
            "out_of_scope": [
                {
                    "label": "Votes",
                    "cards": [{"name": "Liesa, Forgotten Archangel", "drawn": 0.0, "commander": True}],
                }
            ],
        },
    }
    html = rp.model_section_html(g)
    assert html.count("<li class='fill' aria-hidden='true'></li>") == 2  # 3 cards, then 1 card
    assert "Liesa, Forgotten Archangel</button></span><span class='v'>commander</span>" in html
    # a copy count stays on the name's line, inside its button
    two = rp.model_section_html(
        {"deck_size": 60, "honesty": {"unrecognized": [{"name": "Opt", "copies": 4}]}}
    )
    assert "class='cardlink' data-card='Opt'>4× Opt</button>" in two
    assert "drawn 0%" not in html
    # the commanders are marked from the report's own data: the run's commander (Liesa, drawn 0% in
    # the fixture) and the deck's commanders
    r = report()
    r["goldfish"] = {**r["goldfish"]}
    s = rv.report_sections(r | {"stats": {**(r.get("stats") or {}), "commanders": ["Aegis of the Gods"]}})
    cards = s["goldfish"]["honesty"]["unrecognized"]
    assert all(
        bool(c.get("commander")) == (c["name"] in {"Aegis of the Gods", "Liesa, Forgotten Archangel"})
        for c in cards
    )


def test_the_two_buttons_sit_together_under_the_cards() -> None:
    """The request and My card models: two even buttons in one row under the cards played without
    their effect (stacked on a phone)."""
    r = report()
    model = rp.solo_section_html(
        r, rv.report_sections(r), removable=frozenset(), csrf="", models_opener=card_models_view.opener()
    )[1]
    actions = model.split("<div class='ig-actions two'>")[1].split("<section")[0]
    assert actions.index("data-copy='ask-model'") < actions.index("My card models")
    assert ".rv-page .ig-actions.two{grid-template-columns:repeat(2,minmax(0,1fr))}" in rp.REPORT_PAGE_CSS


def test_one_orange_button_and_a_red_outline_remove(monkeypatch: pytest.MonkeyPatch) -> None:
    from mtg_gateway import card_models

    monkeypatch.setattr(card_models, "SWITCHED_ON", True)
    r = report(forge={**FORGE_DONE, "more_games": 10}, status="complete")
    r["goldfish"] = {**r["goldfish"], "kept_models": [{"name": "Sol Ring", "changed_at": 1}]}
    html = rp.report_page_html(r, csrf="tok", removable=frozenset({"sol ring"}))
    assert html.count("btn-primary") == 1
    assert "<div id='forge-more' class='rv-more'>" in html and "class='btn-primary'>" in html
    # the red-outline Remove opens the page's confirm box first (POPUP-INVENTORY: a remove asks first);
    # without scripts the button still submits its form, as before
    assert (
        "class='btn-outline-danger' data-box-open='rm-model-sol-ring' "
        "aria-label='Remove the saved description of Sol Ring'" in html
    )
    assert "id='rm-model-sol-ring' role='dialog'" in html
    assert "<h2 id='rm-model-sol-ring-title'>Remove the saved description of “Sol Ring”?</h2>" in html
    assert "<button type='submit' class='danger' form='rm-model-sol-ring-f'>" in html
    assert html.count("class='danger'") == 1  # the box's final button is the one solid red


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
    # a report is under Assistant (Proposals, History, Reports): the place is marked in the nav
    assert re.search(r"<a href='/proposals'[^>]*aria-current=.?page", page), "Assistant is the current place"
    assert not re.search(r"<a href='/history'[^>]*aria-current", page)
    assert "class='wrap ui-wide'" in page
    # the live status route draws the same label and section as the page
    live = h.get("/api/v1/reports/rep_nav/forge", headers={"Accept": "application/json"}).json()
    assert "rv-forge" in live["forge_html"] and "ui-lbl" in live["status_html"]


def test_long_lists_show_six_rows_then_show_all_and_categories_open_on_tap() -> None:
    """Ben, 10-25 (less scrolling): a list of more than 8 rows shows its first 6 and a Show all
    capsule for the rest; the cards that need opponents sit in their categories, each a row that
    opens; Details and Show all are capsules with a chevron, never bare blue text."""
    model = _page().split("id='rv-model'")[1].split("id='rv-valid'")[0]
    unmodeled = model.split("id='rv-g-unmodeled'")[1].split("<section")[0]
    first = unmodeled.split("<details class='ig-more'>")[0]
    assert first.count("class='cardlink'") == rp.SHOW_FIRST
    assert "<summary><span class='disc'>Show all 32 cards</span>" in unmodeled
    opp = model.split("id='rv-g-opp'")[1].split("<section")[0]
    cats = re.findall(r"<details class='ig-dis'><summary><span class='nm'>([^<]+)</span>", opp)
    assert cats and "Removal and other interaction" in cats and "ig-sub" not in opp
    assert model.count("<span class='disc'>Details</span>") == 4 and "class='more'" not in model
    short = rp.model_section_html(
        {"deck_size": 100, "honesty": {"unrecognized": [{"name": f"C{i}"} for i in range(8)]}}
    )
    assert "ig-more" not in short  # 8 rows or fewer: all shown
