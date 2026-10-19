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
    # Mystic Forge 1.3.2-mag4 says how many copies of each were skipped
    counted = GOLDFISH.replace("skipped: Elbrus", "skipped: 2 Elbrus").replace(", Enduring", ", 1 Enduring")
    g = rv.parse_goldfish({"tool": "goldfish_run", "ok": True, "text": counted})
    assert g["warnings"] == [
        "Not recognized by Scryfall, skipped: 2 Elbrus, the Binding Blade, 1 Enduring Angel"
    ]


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


def test_sixty_card_run_reads_as_a_sixty_card_game() -> None:
    """A 60-card game (Mystic Forge's constructed run: no commander, 20 life) names its kill as 20
    damage and draws no commander milestones; the trend's speed numbers still read."""
    head, rest = GOLDFISH_PRECON.split("\n", 2)[1:]
    assert head.startswith("Deck: 100 cards including commander")
    head = (
        "Deck: 60 cards | Format: 60-card (no commander, 20 life, on the play) | 300 games, seed 42, "
        "through turn 10"
    )
    rest = rest.replace("Kill (40 damage)", "Kill (20 damage)")
    rest = "\n".join(line for line in rest.split("\n") if not line.startswith("- Commander cast"))
    before, js, after = rest.partition("```json\n")[0], *rest.partition("```json\n")[2].split("\n```", 1)
    payload = json.loads(js)
    payload["metrics"]["commander_cast"].update(
        histogram={}, median_among_reached=None, reached_pct={"value": 0.0, "ci": [0.0, 0.01]}
    )
    text = f"# Goldfish run\n{head}\n{before}```json\n{json.dumps(payload)}\n```{after}"
    g = rv.parse_goldfish({"ok": True, "text": text})
    assert g["format"] == "constructed" and g["commander"] is None and g["deck_size"] == 60
    tiles = {t["label"]: t for t in rv.tiles_for(g)}
    assert "20 damage dealt by turn 10" in tiles and "40 damage dealt by turn 10" not in tiles
    assert not any("Commander" in label for label in tiles)
    charts = rv.charts_for(g)
    assert [t for t, _b, _svg in charts][0] == "Reaching the milestones"
    assert "When the commander comes down" not in [t for t, _b, _svg in charts]
    assert "Commander cast" not in charts[0][2] and "20 damage dealt" in charts[0][2]
    speed = rv.speed_numbers({"ok": True, "text": text})
    assert speed["commander_cast_median_turn"] is None and speed["kill_pct"] == 62.7
    # A Commander run keeps its 40.
    assert rv.parse_goldfish({"ok": True, "text": GOLDFISH_PRECON})["format"] is None
    # The research service's validation checks a Commander deck: a 60-card report does not show
    # it (an older report still has one stored) and says why instead.
    r = report(goldfish=text, validation=VALIDATION_PASSED, stats={"card_count": 60})
    page = rv.report_body_html(r)
    assert rv.CONSTRUCTED_VALIDATION.replace("'", "&#x27;") in page and "Deck size: 100" not in page
    md = rv.report_markdown(r)
    assert rv.CONSTRUCTED_VALIDATION in md and "Deck size: 100" not in md
    commander_page = rv.report_body_html(report(goldfish=GOLDFISH_PRECON, validation=VALIDATION_PASSED))
    assert "Deck size: 100" in commander_page and rv.CONSTRUCTED_VALIDATION not in commander_page


def test_a_short_run_claims_no_turn_it_did_not_play() -> None:
    """The simulator's land-drop rate is named "through turn 5" whatever the last turn: a run that
    stopped before turn 5 does not show it."""
    g = rv.parse_goldfish({"ok": True, "text": GOLDFISH_PRECON})
    assert "Every land drop through turn 5" in {t["label"] for t in rv.tiles_for(g)}
    short = rv.parse_goldfish(
        {"ok": True, "text": GOLDFISH_PRECON.replace("through turn 10", "through turn 2")}
    )
    assert short["until_turn"] == 2
    assert "Every land drop through turn 5" not in {t["label"] for t in rv.tiles_for(short)}


def test_page_speaks_plainly_and_shows_every_panel() -> None:
    html = rv.report_body_html(report(), stats_html="<div class='tiles'>stats</div>", reused=True)
    for text in (
        "Solo test (300 games, no opponents)",
        "class='tiles wide rv-stats'",
        "class='ci'",
        "class='charts rv-charts'",
        "<svg",
        "What the solo test could not model",
        "Played without their effect",
        "32 of 98 cards (33% of the deck)",
        "Left out on purpose",
        "Removal and other interaction",
        "Issues found</span>",
        "Deck statistics",
        "Raw output from the research service",
        "## Metrics",
        "report is shown instead of running the simulation again",
    ):
        assert text in html, text
    assert "goldfish_" not in html and "run_deck_report" not in html
    assert "Run id: wjyxinfngo4dg" in html and "(could be annotated)" in html
    # double-faced names appear as their front face in the panels; the whole name only in the raw text
    panels = html.split("<details class='ui-card rv-det rv-raw")[0]
    assert "Enduring Angel" in panels and "Enduring Angel //" not in panels


def test_page_without_a_simulation() -> None:
    html = rv.report_body_html(report(goldfish=None, validation=None))
    assert "research service was not configured" in html and "role='img'" not in html  # no chart
    refused = {
        "tool": "goldfish_run",
        "ok": False,
        "text": "No solo test: the solo test plays Commander decks and needs one card in the "
        "deck's Commander (premier) category.",
    }
    html = rv.report_body_html(report(goldfish=refused["text"]) | {"goldfish": refused})
    assert "ui-lbl red'" in html and ">Not run</span>" in html and "needs one card" in html
    assert "class='tiles" not in html
    html = rv.report_body_html(report(validation=VALIDATION_PASSED))
    assert "ui-lbl green'" in html and ">Passed</span>" in html


def test_markdown_and_html_exports() -> None:
    r = report()
    md = rv.report_markdown(r)
    assert md.startswith("# Deck report: Immortal Reckoning\n")
    assert "- Commander: Liesa, Forgotten Archangel" in md and "- Games: 300, through turn 10" in md
    assert (
        "## Solo test (300 games, no opponents)" in md
        and "40 damage dealt by turn 10: 57% (95% interval 51–62%)" in md
    )
    assert (
        "Played without their effect: 32 of 98 cards (33% of the deck)." in md
        and "## Validation: issues found" in md
    )
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


def test_no_causal_claim_and_one_coverage_figure() -> None:
    """QA-005: "a fair hint the card underperforms" claimed a cause a goldfish game cannot show; it
    is gone. The panel now leads with one coverage figure (the deck less the cards not recognized
    and out of scope: 98 - 32 - 17 = 49), for Commander games only (60-card decks hold copies)."""
    html = rv.report_body_html(report())
    assert "underperforms" not in html and "says nothing about how the card does" in html
    assert (
        "Played with their effect: 49 of 98 cards (50% of the deck). Known: 66 of 98 cards (67% of the "
        "deck), counting the cards played with their effect and those left out on purpose. The rest are "
        "listed below." in html
    )
    md = rv.report_markdown(report())
    assert "underperforms" not in md and "Played with their effect: 49 of 98 cards (50% of the deck)" in md
    g = rv.parse_goldfish({"ok": True, "text": GOLDFISH})
    assert (
        rv.coverage({**g, "format": "constructed"}) is None and rv.coverage({**g, "deck_size": None}) is None
    )


def test_preflight_only_label_and_overall_status() -> None:
    """A report without Forge games (Forge off, or left out of this report) says "Preflight only
    (no rules engine)" where the Forge games would be. QA-010: its overall status is in the head."""
    html = rv.report_body_html(report(status="complete"))  # Forge was not set up
    assert ">Preflight only (no rules engine)</h2>" in html and "was not set up" in html
    assert "id='rep-status'><span class='ui-lbl green'>" in html and ">Complete</span>" in html
    skipped = {"state": "skipped", "reason": "not_commander", "note": "Forge plays Commander games only."}
    html = rv.report_body_html(report(forge=skipped, status="complete"))
    assert ">Preflight only (no rules engine)</h2>" in html and "Commander games only." in html
    assert "Forge games" not in html and "not run" not in html.split("Solo test (")[0]
    full = {"state": "skipped", "error": "You already have a Forge run going and 2 reports waiting."}
    html = rv.report_body_html(report(forge=full, status="partial"))
    assert "Preflight only" in html and "notice warn'>You already have" in html
    assert "id='rep-status'><span class='ui-lbl orange'>" in html and ">Partial</span>" in html
    running = rv.report_body_html(report(forge={"state": "running"}, status="running"))
    assert "Preflight only" not in running and ">Forge games (against precons)</h2>" in running
    assert "id='rep-status'><span class='ui-lbl orange'>" in running and ">Forge going</span>" in running
    md = rv.report_markdown(report(forge=skipped, status="complete"))
    assert "## Preflight only (no rules engine)" in md and "- Status: complete" in md
    assert "## Preflight only (no rules engine)" in rv.report_markdown(report())
    assert "## Forge games (against precons)" in rv.report_markdown(
        report(forge={"state": "running"}, status="running")
    )


def test_copies_and_sixty_card_coverage_come_from_the_kept_quantities() -> None:
    """The cards played without their effect show their copies; a 60-card game kept with
    its quantities gets a coverage figure counted in copies (none without them, as before)."""
    g0 = rv.parse_goldfish({"ok": True, "text": GOLDFISH})
    name = g0["honesty"]["unrecognized"][0]["name"]
    quantities = {rv.front_face(name): 3, "Not In The Report": 2, "bad": "x", "neg": -1, "flag": True}
    g = rv.parse_goldfish({"ok": True, "text": GOLDFISH, "quantities": quantities})
    assert g["quantities"] == {rv.front_face(name).casefold(): 3, "not in the report": 2}
    first = g["honesty"]["unrecognized"][0]
    assert first["copies"] == 3 and g["honesty"]["unrecognized"][1]["copies"] is None
    assert f"<li>3× {rv.front_face(name)}" in rv.honesty_html(g)
    # 98 cards less 34 copies played without their effect (32 cards, one of them 3 times) less 17 = 47
    assert rv.coverage(g) == {"modeled": 47, "deck_size": 98, "pct": 48, "known": 64, "known_pct": 65}
    con = {**g, "format": "constructed", "deck_size": 60}
    assert rv.coverage({**con, "quantities": {}}) is None
    assert rv.coverage(con)["deck_size"] == 60
    md = rv.report_markdown(
        report(goldfish=None)
        | {"goldfish": {"tool": "goldfish_run", "ok": True, "text": GOLDFISH, "quantities": quantities}}
    )
    assert f"3× {rv.front_face(name)}" in md


def test_ask_button_only_on_the_signed_in_page() -> None:
    """The cards played without their effect get a copy-a-request button on the signed-in page (the
    same words for Claude and ChatGPT, no tool names on the page); exports carry no button."""
    r = report(deck_url="https://archidekt.com/decks/42")
    html = rv.report_body_html(r)
    assert "data-copy='ask-model'" in html and rv.ASK_LABEL in html and "id='ask-model-status'" in html
    assert "Immortal Reckoning" in html.split("id='ask-model'")[1] and "goldfish_" not in html
    assert "never from memory" in html
    assert "ask-model" not in rv.report_export_html(r, stats_html="", theme_css="")
    assert rv.ASK_LABEL not in rv.report_markdown(r)
    assert rv.ask_html(r, []) == ""
    # names are escaped in the hidden text
    assert "&lt;b&gt;" in rv.ask_html(r | {"deck_name": "<b>"}, [{"name": "X"}])


def test_the_copied_request_also_names_the_other_groups_as_context() -> None:
    """Ben, 0.7.31 test round: the request for the assistant covers not only the cards played
    without their effect but also those left out on purpose, those that never mattered and the
    shortcuts, so the assistant can say where any of them matters."""
    r = report(deck_url="https://archidekt.com/decks/42")
    g = rv.parse_goldfish(r["goldfish"], deck_size=100)
    h = g["honesty"]
    assert h.get("out_of_scope") and h.get("low_impact") and h.get("approximations")
    text = rv.report_body_html(r).split("id='ask-model'")[1].split("</textarea>")[0]
    assert "left out on purpose" in text
    assert rv.front_face(h["out_of_scope"][0]["cards"][0]["name"]) in text
    assert "never mattered: " + rv.front_face(h["low_impact"][0]) in text
    assert h["approximations"][0]["label"] in text
    assert "would play differently in a real game" in text
    # nothing to add: the request ends as before
    plain = rv.ask_prompt("D", "u", [{"name": "X"}], honesty={})
    assert plain.endswith("which cards you could not describe.")


def test_left_out_groups_count_copies_as_their_heading_does() -> None:
    """A group under "Left out on purpose" counts copies, like the heading."""
    g = {
        "deck_size": 60,
        "honesty": {
            "out_of_scope": [
                {
                    "kind": "interaction_removal",
                    "label": "Removal",
                    "cards": [{"name": "Murder", "copies": 4}],
                }
            ],
            "unrecognized": [],
            "low_impact": [],
        },
    }
    html = rv.honesty_html(g)
    assert "<h4>Removal <small>4</small></h4>" in html
