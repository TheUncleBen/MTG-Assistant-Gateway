"""The confirmation round on 0fe11813 (0.7.46), the parts that need no browser: a count of one is
never "All 1" (P-6)."""

from __future__ import annotations

from mtg_gateway import ui


def test_all_shown_names_one_alone() -> None:
    """P-6: "All 1 waiting" for a single item; the load-more lines said "All 1 entry shown." and
    "All 1 decks shown." the same way."""
    assert ui.all_shown(1, "entry", "entries") == "1 entry shown."
    assert ui.all_shown(1, "deck") == "1 deck shown."
    assert ui.all_shown(2, "deck") == "All 2 decks shown."
    assert ui.all_shown(1234, "entry", "entries") == "All 1,234 entries shown."
