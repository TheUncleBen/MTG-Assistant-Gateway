"""A report's two downloads share one outline (0.7.46): the title, a table of what was tested (with
the deck's and the report's links), a summary, the solo test, Forge, what the test leaves out,
validation with its own headings, statistics, and the research service's raw output last (a fold
in the HTML file, a link in the Markdown). No assistant tool name reaches a person, and the HTML
file prints on white paper with its links spelled out."""

from __future__ import annotations

import json
import re

from mtg_gateway import report_view as rv
from mtg_gateway import theme
from mtg_gateway.companion import stats_strip
from mtg_gateway.tool_labels import TOOLS

from .test_report_solo_forge import FORGE_SOLO
from .test_report_view import VALIDATION_ISSUES, report
from .test_reports_browser import FORGE_DONE

URL = "https://mtg.example.org/history/reports/rep_test"
CHECKS = (
    "\n\n## Gateway checks\nSingleton: no card has more than one copy.\nCopies are counted by name over "
    "every line; basic lands and the cards a deck may hold any number of are exempt. Banned cards and "
    "the other format rules: deck_stats on the deck.\n"
)


def _report(**over):
    r = report(
        validation=VALIDATION_ISSUES + CHECKS,
        forge=json.loads(json.dumps(FORGE_DONE)),
        forge_solo=json.loads(json.dumps(FORGE_SOLO)),
        status="complete",
    )
    r["goldfish"]["text"] += "\nThe stored copy is under History (get_deck_report).\n"
    r["stats"]["price_total"] = 0.0
    r.update(over)
    return r


def _html(r) -> str:
    return rv.report_export_html(
        r, stats_html=stats_strip(r.get("stats")), theme_css=theme.VIZ_CSS, report_url=URL
    )


OUTLINE = [
    "Summary",
    "Quick solo test",
    "Forge games (against precons)",
    rv.FORGE_SOLO_HEADING,
    rv.MODEL_HEADING,
    "Validation",
    "Deck statistics",
    "Raw output",
]


def _in_order(found: list[str]) -> list[str]:
    """Each OUTLINE heading's first match among ``found`` headings, in the order found."""
    return [want for h in found for want in OUTLINE if h.startswith(want)]


def test_markdown_follows_the_outline() -> None:
    md = rv.report_markdown(_report(), report_url=URL)
    heads = [ln[3:] for ln in md.splitlines() if ln.startswith("## ")]
    assert _in_order(heads) == OUTLINE, heads
    # a table of what was tested, with both links
    assert "| Deck | [Immortal Reckoning](https://archidekt.com/decks/42) |" in md
    assert f"| Report | [Open the report]({URL}) |" in md and "| Status | Complete |" in md
    # validation keeps its own headings; the names stay under their line
    val = md.split("## Validation", 1)[1].split("\n## ", 1)[0]
    assert "\n### Gateway checks\n" in val
    assert "- 2 card(s) not found on Scryfall:\n  - Enduring Angel\n  - Elbrus, the Binding Blade" in val
    # the raw output is a link, not 15 KB of JSON
    raw = md.split("## Raw output", 1)[1]
    assert URL in raw and "```" not in raw and '{"n": 300' not in md
    # no empty heading, no unknown price
    for i, ln in enumerate(md.splitlines()[:-2]):
        if ln.startswith("## "):
            assert md.splitlines()[i + 2].strip(), ln
    assert "Price (USD): 0.0" not in md


def test_html_follows_the_outline_and_prints() -> None:
    doc = _html(_report())
    heads = [re.sub(r"<[^>]+>", "", h).strip() for h in re.findall(r"<h2[^>]*>(.*?)</h2>", doc)]
    heads += ["Raw output"] if "<summary class='fold-btn'>Raw output" in doc else []
    assert _in_order(heads) == OUTLINE, heads
    assert "href='https://archidekt.com/decks/42'" in doc and f"href='{URL}'" in doc
    assert "<h3>Gateway checks</h3>" in doc
    css = doc.split("<style>", 1)[1].split("</style>", 1)[0]
    assert "@media print" in css
    printed = css[css.index("@media print") :]
    assert "--bg:#f9fafb" in printed and "attr(href)" in printed


def test_no_tool_names_in_either_download() -> None:
    names = sorted(TOOLS, key=len, reverse=True)
    word = re.compile(r"\b(" + "|".join(map(re.escape, names)) + r"|goldfish_[a-z_]+)\b")
    for text in (rv.report_markdown(_report(), report_url=URL), _html(_report())):
        assert not word.findall(text), sorted(set(word.findall(text)))


def test_golden_files() -> None:
    """The whole Markdown file and the HTML file's body for one report, looked at by hand."""
    from .test_export_standard import _golden

    r = _report(taken_at=1_761_652_800)
    _golden("report.md", rv.report_markdown(r, report_url=URL))
    doc = _html(r)
    _golden("report.body.html", doc[doc.index("<body>") : doc.index("</body>")].replace("><", ">\n<") + "\n")
