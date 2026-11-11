"""A report row's facts read as words: "Won 62%" for a rate, "Forge no games" when none were
played (it read "Won no Forge")."""

from __future__ import annotations

from mtg_gateway import reports_page


def _row(**more) -> str:
    r = {"report_id": "r1", "deck_name": "Deck", "deck_id": "42", "status": "complete", "taken_at": 1, **more}
    return reports_page.row_html(r, {}, now=100.0)


def test_a_rate_reads_won():
    assert "Won <b>62%</b>" in _row(win={"rate": 0.62, "games": 40, "enough": True})


def test_no_forge_games_reads_as_words():
    html = _row()
    assert "Forge <b>no games</b>" in html and "Won <b>" not in html and "no Forge" not in html


def test_the_other_states_are_about_forge_too():
    assert "Forge <b>playing</b>" in _row(status="running")
    assert "Forge <b>4 games, too few</b>" in _row(win={"rate": 0.5, "games": 4, "enough": False})
