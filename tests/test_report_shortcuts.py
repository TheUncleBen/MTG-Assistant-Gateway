"""The report page's "Shortcuts the solo test takes" and its card names (Ben, 2026-11-11: the
shortcuts need the most care; a card tapped in the report opened a bare, unstyled panel).

Each shortcut is a row: what it does, which way it can push the numbers, why, and its cards as
names that open the card viewer, which says what the test did with the card. The page loads the
card viewer's own styles."""

from __future__ import annotations

import re

import httpx

from mtg_gateway import report_page as rp
from mtg_gateway import report_view as rv

from .test_history_page import store_report
from .test_report_view import report
from .test_social_browser import Server, server  # noqa: F401


def _model_section(**kw) -> str:
    r = report(status="complete")
    s = rv.report_sections(r)
    return rp.model_section_html(s["goldfish"], **kw)


def test_names_with_commas_stay_whole_when_the_deck_holds_them() -> None:
    known = {"aesi, tyrant of gyre strait", "lotus field", "opt"}
    assert rv.split_card_names("Lotus Field, Aesi, Tyrant of Gyre Strait, Opt", known) == [
        "Lotus Field",
        "Aesi, Tyrant of Gyre Strait",
        "Opt",
    ]
    # nothing known: the line stays whole rather than cut in a wrong place
    assert rv.split_card_names("Aesi, Tyrant of Gyre Strait, Opt", set()) == [
        "Aesi, Tyrant of Gyre Strait, Opt"
    ]


def test_each_shortcut_says_which_way_it_pushes_and_lists_its_cards_as_cards() -> None:
    html = _model_section()
    sec = html.split("id='rv-g-approx'", 1)[1]
    assert "<h3 id='rv-g-approx-h'>Shortcuts the solo test takes</h3>" in html
    assert "Shortcuts the model takes" not in html  # "model" is the card models' word
    rows = re.findall(r"<li class='sc sc-(high|low|either)'>", sec)
    assert len(rows) == 6
    # the ones that can flatter the deck first, then the ones that can hurt it, then the rest
    order = {"high": 0, "low": 1, "either": 2}
    assert [order[r] for r in rows] == sorted(order[r] for r in rows)
    first = sec.split("<li class='sc sc-high'>", 1)[1].split("</ul></li>", 1)[0]
    assert "Treated as producing any color" in first and "Can make the deck look better" in first
    assert rv.SHORTCUT_PUSH["any-color"][1] in first
    names = re.findall(r"<button type='button' class='cardlink' data-card='([^']+)'", first)
    assert names == ["Lotus Field", "Opaline Unicorn", "Transguild Promenade"]
    # the count says how many cards the shortcuts touch
    assert "<span class='n'>6 shortcuts · 14 cards</span>" in html


def test_card_names_in_the_report_say_what_the_test_did_with_them() -> None:
    html = _model_section(models_now=frozenset({"aegis of the gods"}))
    aegis = re.search(
        r"<button type='button' class='cardlink' data-card='Aegis of the Gods'[^>]*>", html
    ).group(0)
    assert "data-ctx='Played without its effect'" in aegis and "data-ctx-text='" in aegis
    assert "data-model='/history/models#m-aegis-of-the-gods'" in aegis  # saved since: its card model
    ajani = re.search(
        r"<button type='button' class='cardlink' data-card='Ajani, Caller of the Pride'[^>]*>", html
    ).group(0)
    assert "data-model='new'" in ajani  # none saved: the viewer says how card models work
    murder = re.search(r"<button type='button' class='cardlink' data-card='Murder'[^>]*>", html).group(0)
    assert "data-ctx='Needs opponents: Removal and other interaction'" in murder
    unicorn = re.findall(r"<button type='button' class='cardlink' data-card='Opaline Unicorn'[^>]*>", html)
    assert any(
        "data-ctx='Shortcut: Mana creatures can tap for mana the turn they arrive'" in b for b in unicorn
    )


def test_the_report_page_loads_the_card_viewers_styles(server: Server) -> None:  # noqa: F811
    """The card viewer's styles live in deck.css: a page with the viewer loads them (0.7.46's report
    page did not, so a tapped card opened as bare text under the page)."""
    sid = server.sign_in_and_link()
    store_report(server.db, "user-1", "rep_css")
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    for path in ("/history/reports/rep_css", "/history/models"):
        page = h.get(path).text
        assert "/static/cardview.js" in page, path
        assert re.search(r"<link[^>]+deck\.css", page), path


def test_validation_says_its_verdict_once_and_nests_the_cards_under_their_line() -> None:
    """The label beside the heading says the verdict: the service's own "Validation: ISSUES FOUND"
    line is not repeated under it, and the cards sit under the line that names them."""
    r = report(status="complete")
    html = rp.validation_section_html(rv.report_sections(r))
    assert "ISSUES FOUND" not in html
    assert re.search(
        r"<li>2 cards not found on Scryfall:<ul><li>Enduring Angel</li>"
        r"<li>Elbrus, the Binding Blade</li></ul></li>",
        html,
    ), html


def test_deck_statistics_leave_out_a_bracket_nobody_knows() -> None:
    """A deck with no bracket set and no estimate has no bracket tile, never a bare dash."""
    from mtg_gateway.companion import stats_strip

    html = stats_strip({"card_count": 100, "land_count": 34, "average_mana_value": 3.2, "game_changers": []})
    assert "estimate" not in html and "<b>–</b>" not in html
    html = stats_strip({"card_count": 100, "bracket_estimate": {"bracket": 3}})
    assert "<b>~3</b><span>Archidekt&#x27;s estimate</span>" in html
