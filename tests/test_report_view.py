"""report_view: the research service's Markdown read into plain data and drawn as a page. The
fixtures under tests/fixtures/reports are real simulator output (headings, JSON keys and the
honesty report as the service writes them); the garbage cases make sure nothing ever raises."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from mtg_gateway import report_view as rv

FIX = Path(__file__).parent / "fixtures" / "reports"
GOLDFISH = (FIX / "goldfish_run.md").read_text()
GOLDFISH_PRECON = (FIX / "goldfish_run_precon.md").read_text()
VALIDATION_ISSUES = (FIX / "validation_issues.md").read_text()
VALIDATION_PASSED = (FIX / "validation_passed.md").read_text()


def report(goldfish=GOLDFISH, validation=VALIDATION_ISSUES, **over):
    r = {
        "report_id": "rep_test",
        "deck_id": "42",
        "deck_name": "Immortal Reckoning",
        "taken_at": int(time.time()) - 120,
        "stats": {
            "card_count": 100,
            "land_count": 34,
            "average_mana_value": 3.2,
            "mana_curve": {"1": 9, "2": 17},
        },
        "goldfish": {"tool": "goldfish_run", "ok": True, "text": goldfish} if goldfish is not None else None,
        "validation": {"tool": "validate_decklist", "ok": True, "text": validation}
        if validation is not None
        else None,
    }
    r.update(over)
    return r


def test_goldfish_header_summary_and_metrics_are_read() -> None:
    g = rv.parse_goldfish({"tool": "goldfish_run", "ok": True, "text": GOLDFISH}, deck_size=100)
    assert g["commander"] == "Liesa, Forgotten Archangel"
    assert g["deck_size"] == 98  # the text's own count wins over the statistics' 100
    assert (g["games"], g["seed"], g["until_turn"], g["run_id"]) == (300, 42, 10, "wjyxinfngo4dg")
    assert len(g["summary"]) == 6 and g["summary"][0].startswith("Commander cast: median T5")
    m = g["metrics"]
    assert m["commander_cast"]["histogram"]["5"] == 110 and m["kill"]["reached_pct"]["value"] == 0.57
    assert g["message"] is None
    # the warning line is one sentence with back faces dropped (names hold commas, so no split)
    assert g["warnings"] == ["Not recognized by Scryfall, skipped: Elbrus, the Binding Blade, Enduring Angel"]


def test_honesty_report_comes_from_the_json_with_text_fallback() -> None:
    g = rv.parse_goldfish({"ok": True, "text": GOLDFISH})
    h = g["honesty"]
    assert len(h["unrecognized"]) == 32 and h["unrecognized"][1]["name"] == "Ajani, Caller of the Pride"
    assert h["unrecognized"][1]["drawn"] == pytest.approx(0.1667, abs=0.001)
    assert [grp["kind"] for grp in h["out_of_scope"]] == ["interaction_removal", "unmodeled_other"]
    assert h["out_of_scope"][0]["label"] == "Removal and other interaction"
    assert len(h["out_of_scope"][0]["cards"]) == 11 and len(h["low_impact"]) == 25
    # the approximations exist only in the text
    assert {a["kind"] for a in h["approximations"]} >= {"any-color", "fetch", "residual-text"}
    assert "Lotus Field" in next(a["cards"] for a in h["approximations"] if a["kind"] == "any-color")
    # the same report with its JSON removed: the honesty report is read from the text instead
    stripped = GOLDFISH.split("## Metrics")[0] + "## Honesty report" + GOLDFISH.split("## Honesty report")[1]
    g2 = rv.parse_goldfish({"ok": True, "text": stripped})
    assert len(g2["honesty"]["unrecognized"]) == 32 and g2["honesty"]["unrecognized"][1]["drawn"] == 0.17
    assert [len(x["cards"]) for x in g2["honesty"]["out_of_scope"]] == [11, 6]
    assert len(g2["honesty"]["low_impact"]) == 25


def test_validation_verdicts() -> None:
    v = rv.parse_validation({"ok": True, "text": VALIDATION_ISSUES})
    assert v["verdict"] == "issues" and v["headline"] == "Validation: ISSUES FOUND"
    # a line that is just a double-faced name shows its front face; the raw text keeps it whole
    assert v["lines"] == ["2 card(s) not found on Scryfall:", "Enduring Angel", "Elbrus, the Binding Blade"]
    assert "Enduring Angel // Angelic Enforcer" in v["raw"]
    ok = rv.parse_validation({"ok": True, "text": VALIDATION_PASSED})
    assert ok["verdict"] == "passed" and ok["lines"][0] == "All 72 unique cards verified on Scryfall."
    assert rv.parse_validation({"ok": False, "text": "service unavailable"})["verdict"] == "failed"
    assert rv.parse_validation({"ok": True, "text": "Validated 100 lines"})["verdict"] is None
    assert rv.parse_validation(None) is None


def test_tiles_and_charts_from_the_metrics() -> None:
    g = rv.parse_goldfish({"ok": True, "text": GOLDFISH_PRECON})
    tiles = {t["label"]: t for t in rv.tiles_for(g)}
    assert tiles["Commander cast (median turn)"]["value"] == "Turn 6"
    kill = tiles["40 damage dealt by turn 10"]
    assert kill["value"] == "63%" and rv._ci_text(kill["rate"]) == "57–68%"
    assert tiles["Games with a mulligan"]["value"] == "34%"
    assert tiles["Lands on turn 5 (average)"]["value"] == "4.7"
    charts = rv.charts_for(g)
    titles = [t for t, _b, _svg in charts]
    assert titles == [
        "Reaching the milestones",
        "When the commander comes down",
        "Mana available",
        "Damage per turn",
    ]
    lines = charts[0][2]
    assert lines.count("<polyline") == 4 and "class='legend'" in lines and "T10" in lines
    assert "<script" not in lines and "style=" not in lines  # colours come from the theme's classes
    cols = charts[1][2]
    assert "<rect class='col'" in cols and "T6" in cols


def test_page_speaks_plainly_and_shows_every_panel() -> None:
    html = rv.report_body_html(report(), stats_html="<div class='tiles'>stats</div>", reused=True)
    for text in (
        "Goldfish simulation",
        "class='tiles wide'",
        "class='ci'",
        "class='charts'",
        "<svg",
        "What the simulation could not model",
        "Not recognized",
        "32 of 98 cards (33% of the deck)",
        "Out of scope",
        "Removal and other interaction",
        "badge warn'>issues found",
        "Deck statistics",
        "Raw output from the research service",
        "## Metrics",
        "report is shown instead of running the simulation again",
    ):
        assert text in html, text
    assert "goldfish_" not in html and "run_deck_report" not in html
    assert "Run id: wjyxinfngo4dg" in html and "(could be annotated)" in html
    # double-faced names appear as their front face in the panels; the whole name only in the raw text
    panels = html.split("<details class='card raw")[0]
    assert "Enduring Angel" in panels and "Enduring Angel //" not in panels


def test_page_without_a_simulation() -> None:
    html = rv.report_body_html(report(goldfish=None, validation=None))
    assert "research service was not configured" in html and "<svg" not in html
    refused = {
        "tool": "goldfish_run",
        "ok": False,
        "text": "Not simulated: the goldfish simulator models Commander decks and needs one card in the "
        "deck's Commander (premier) category.",
    }
    html = rv.report_body_html(report(goldfish=refused["text"]) | {"goldfish": refused})
    assert "badge danger'>not run" in html and "needs one card" in html and "class='tiles" not in html
    html = rv.report_body_html(report(validation=VALIDATION_PASSED))
    assert "badge ok'>passed" in html


def test_markdown_and_html_exports() -> None:
    r = report()
    md = rv.report_markdown(r)
    assert md.startswith("# Deck report: Immortal Reckoning\n")
    assert "- Commander: Liesa, Forgotten Archangel" in md and "- Games: 300, through turn 10" in md
    assert "## Goldfish simulation" in md and "40 damage dealt by turn 10: 57% (95% interval 51–62%)" in md
    assert "Not recognized — 32 of 98 cards (33% of the deck):" in md and "## Validation: issues found" in md
    assert "## Simulation output" in md and "## Metrics" in md and "goldfish_" not in md
    doc = rv.report_export_html(r, stats_html="<p>stats</p>", theme_css=".tiles{display:grid}")
    assert doc.startswith("<!doctype html>") and "<script" not in doc and "href='/" not in doc
    assert "<svg" in doc and ".tiles{display:grid}" in doc and "prefers-color-scheme:dark" in doc
    assert "<title>Deck report: Immortal Reckoning</title>" in doc


@pytest.mark.parametrize(
    "junk",
    [
        None,
        {},
        [],
        "text",
        {"text": "lorem ipsum"},
        {"text": "## Metrics\n```json\n{not json"},
        {"text": 5, "data": [1, 2]},
        {"ok": True, "data": {"metrics": {"commander_cast": {"histogram": {"a": "b"}, "reached_pct": "x"}}}},
        {"ok": True, "text": "# Goldfish run\nDeck: x | Commander: | 12 games | run_id:"},
        {
            "ok": True,
            "text": "## Summary\n- a\n## Honesty report\nOut of scope\n- x: y (drawn 5%)\n"
            "Unrecognized — 2\n- Foo",
        },
        {
            "ok": True,
            "text": "## Metrics\n```json\n"
            + json.dumps({"metrics": {"damage": {"avg_by_turn": "no"}}})
            + "\n```",
        },
        {
            "ok": True,
            "text": "## Metrics\n```json\n"
            + json.dumps({"n": 0, "metrics": {"kill": {"histogram": {}}}})
            + "\n```",
        },
        {"ok": True, "text": "x" * 200_000},
    ],
)
def test_odd_input_never_raises(junk) -> None:
    g = rv.parse_goldfish(junk)
    v = rv.parse_validation(junk)
    assert (g is None) == (not isinstance(junk, dict)) and (v is None) == (not isinstance(junk, dict))
    if g:
        rv.tiles_for(g)
        rv.charts_for(g)
    r = report() | {"goldfish": junk, "validation": junk, "stats": junk if isinstance(junk, dict) else {}}
    for out in (rv.report_body_html(r), rv.report_markdown(r), rv.report_export_html(r)):
        assert isinstance(out, str) and out and "goldfish_" not in out


def test_front_face_and_plain_words() -> None:
    assert rv.front_face("Enduring Angel // Angelic Enforcer") == "Enduring Angel"
    assert (
        rv.front_face("Sol Ring") == "Sol Ring"
        and rv.front_face(None) == ""
        and rv.front_face(" // ") == "//"
    )
    assert rv.plain_words('Full report: goldfish_report("abc")') == "Run id: abc"
    assert rv.plain_words("see goldfish_annotate") == "see annotate"
    assert "goldfish_" not in rv.plain_words(GOLDFISH)
