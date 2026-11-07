"""One shared progress spinner (0.7.46): a CSS-only Material-style arc that grows and shrinks as it
turns, and a gentle pulse instead under prefers-reduced-motion. Every run or load in progress draws
it: the Running, Forge running and Applying labels, a busy button and a box that is loading. It is
drawn from the live status (the label is there only while the run is), never from a timer, and a
button that starts a run locks itself and says "Starting…"."""

from __future__ import annotations

import json
import re

import pytest

from mtg_gateway import report_page as rp
from mtg_gateway import report_view as rv
from mtg_gateway import theme, ui

from .test_report_view import report
from .test_reports_browser import FORGE_DONE

SPIN = "<span class='ui-spin' aria-hidden='true'></span>"
ALL_CSS = theme.CSS + theme.UI_CSS + theme.BOX_CSS


def test_the_running_labels_draw_the_shared_spinner() -> None:
    for out in (
        ui.label("Running", "orange", "spinner"),
        ui.status_label("applying"),
        ui.label("Forge running", "orange", "spinner"),
    ):
        assert SPIN in out and "<svg" not in out, out
    assert theme.SPINNER == SPIN and theme.icon("spinner").startswith("<svg")
    # the other labels keep their icons
    assert "<svg" in ui.status_label("waiting") and "ui-spin" not in ui.status_label("waiting")


def test_one_spinner_style_with_a_pulse_for_reduced_motion() -> None:
    assert ALL_CSS.count("@keyframes ui-spin-arc") == 1 and ALL_CSS.count("@keyframes ui-spin-turn") == 1
    assert ALL_CSS.count("@keyframes ui-spin-pulse") == 1
    # the old rings are gone: no second spinner anywhere in the gateway's styles
    assert "@keyframes busy" not in ALL_CSS and "mtgbox-spin" not in ALL_CSS
    assert "rotate(360deg)" not in ALL_CSS
    css = re.sub(r"/\*.*?\*/", "", ALL_CSS, flags=re.S)
    m = re.search(r"([^{}]*)\{[^{}]*animation:ui-spin-arc", css)
    assert m, "the spinner rule"
    selectors = {s.strip() for s in m.group(1).split(",")}
    assert {
        ".ui-spin",
        "button[aria-busy=true]::after",
        ".btn[aria-busy=true]::after",
        ".mtgbox .loading::before",
        ".mtgbox .progress::before",
    } <= selectors, selectors
    reduced = ALL_CSS.split("@media (prefers-reduced-motion:reduce){\n  /* the spinner pulses", 1)
    assert len(reduced) == 2 and "animation:ui-spin-pulse" in reduced[1].split("}}", 1)[0]


def _section(html: str) -> str:
    return html.split("id='rv-solo-forge'", 1)[1].split("</section>", 1)[0]


def test_the_spinner_follows_the_live_status() -> None:
    """While the run goes, its section and status draw the spinner; the poll that brings the end of
    the run brings markup without it."""
    r = report(forge=json.loads(json.dumps(FORGE_DONE)), status="complete")
    r["forge_solo"] = {"state": "running", "error": "Games are going now.", "games_requested": 300}
    page = rp.report_page_html(r)
    assert SPIN in _section(page)
    live = rv.forge_status(r)
    assert SPIN in live["forge_solo_html"]
    r["forge_solo"] = {**r["forge_solo"], "state": "done", "result": {"games": 300}}
    assert SPIN not in rv.forge_status(r)["forge_solo_html"]


def test_a_button_that_starts_a_run_says_starting() -> None:
    html = rv.more_games_html({"state": "done", "more_games": 4}, "tok", "rep_1")
    assert "<button type='submit' class='btn' data-busy-label='Starting…'>" in html


pytest.importorskip("playwright")

from .test_scan_browser import _chromium_path  # noqa: E402

PAGE = (
    "<!doctype html><html><head><style>{css}</style></head><body>"
    "<button id='b' aria-busy='true'>Starting…</button>"
    "<p>{label}</p></body></html>"
)


@pytest.mark.parametrize("motion", ["no-preference", "reduce"])
def test_the_spinner_turns_or_pulses_in_a_browser(motion: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        try:
            page = browser.new_page(reduced_motion=motion)
            page.set_content(PAGE.format(css=ALL_CSS, label=ui.label("Running", "orange", "spinner")))
            names = page.evaluate(
                """() => [getComputedStyle(document.querySelector('.ui-spin')).animationName,
                         getComputedStyle(document.getElementById('b'), '::after').animationName]"""
            )
            want = "ui-spin-pulse" if motion == "reduce" else "ui-spin-arc, ui-spin-turn"
            assert names == [want, want], names
            size = page.evaluate("() => document.querySelector('.ui-spin').offsetWidth")  # turning aside
            assert 12 <= size <= 18, size
        finally:
            browser.close()
