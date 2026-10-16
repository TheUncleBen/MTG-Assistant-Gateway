"""How the bracket shows on the deck page (bracket_view.py): the bracket set on the deck is the
deck's bracket; Archidekt's estimate from the cards shows in the banner only when none is set, is
named in the checks row only on a real mismatch, and keeps Archidekt's own notes (Q49, O14, G8)."""

from __future__ import annotations

from types import SimpleNamespace

from mtg_gateway.bracket_view import (
    ESTIMATE_LABEL,
    banner_bracket_html,
    checks_bracket_row,
    deck_card_bracket_text,
    stats_bracket_html,
)

EST = {
    "bracket": 4,
    "kind": "estimate",
    "basis": ["no game changers", "two-card combo pieces: <A>, B"],
    "notes": ["If this is a late-game combo, your deck may be a Bracket: Upgraded"],
}


def test_label_is_the_one_ben_chose() -> None:
    assert ESTIMATE_LABEL == "Archidekt's estimate from cards"  # O14 "Archidekt label"


def test_banner_shows_one_bracket() -> None:
    stats = {"bracket_estimate": EST}
    set_deck = SimpleNamespace(edh_bracket=2)
    html = banner_bracket_html(set_deck, stats)
    assert html == "<span class='bracket-set'>Bracket: Core (2)</span>"
    assert "estimate" not in html
    unset = banner_bracket_html(SimpleNamespace(edh_bracket=None), stats)
    assert unset == "<span class='bracket-est'>Archidekt's estimate from cards: Optimized (4)</span>"
    assert banner_bracket_html(SimpleNamespace(edh_bracket=None), {}) == ""


def test_checks_row_names_the_estimate_only_on_a_mismatch() -> None:
    assert checks_bracket_row({"bracket": {"set": None}}) is None
    fine = checks_bracket_row({"bracket": {"set": 3, "estimate": 2, "ok": True}})
    assert fine == ("Bracket", "set on the deck to Upgraded (3)", True)
    bad = checks_bracket_row(
        {"bracket": {"set": 2, "estimate": 3, "ok": False, "mismatches": ["game changers (<Rift>)"]}}
    )
    assert bad is not None and bad[2] is False
    assert bad[1] == (
        "set on the deck to Core (2) · not allowed there: game changers (&lt;Rift&gt;) · "
        "Archidekt's estimate from cards: Upgraded (3)"
    )


def test_stats_box_keeps_the_class_deck_js_swaps_and_archidekts_notes() -> None:
    unset = stats_bracket_html({"bracket_estimate": EST, "archidekt_bracket": None})
    assert unset.startswith("<div class='bracket'>") and "set on the deck" not in unset
    assert "Archidekt's estimate from cards: <b>Optimized (4)</b>" in unset
    assert (
        "<li>Archidekt notes: “If this is a late-game combo, your deck may be a Bracket: Upgraded”</li>"
        in unset
    )
    assert "&lt;A&gt;" in unset and "<A>" not in unset
    both = stats_bracket_html({"bracket_estimate": EST, "archidekt_bracket": 3})
    assert both.index("Bracket: Upgraded (3)</b> · set on the deck") < both.index(ESTIMATE_LABEL)
    only_set = stats_bracket_html({"bracket_estimate": {"bracket": None}, "archidekt_bracket": 5})
    assert "Bracket: cEDH (5)" in only_set and ESTIMATE_LABEL not in only_set
    assert stats_bracket_html({"bracket_estimate": {"bracket": None}, "archidekt_bracket": None}) == ""


def test_deck_list_line_unchanged() -> None:
    assert deck_card_bracket_text(4) == " - Bracket: Optimized (4)"
    assert deck_card_bracket_text(None) == ""
