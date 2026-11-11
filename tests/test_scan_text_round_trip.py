"""The card text reader behind resolve_cards, save_scan_session and propose_collection_changes
reads the gateway's own decklist_text: a category in brackets is not a set, the markers are not
part of the name, and a line it cannot read is reported instead of dropped."""

from __future__ import annotations

from mtg_gateway.scan.service import parse_text


def _one(line: str):  # noqa: ANN202
    cards = parse_text(line)
    assert len(cards) == 1, cards
    return cards[0]


def test_a_category_in_brackets_is_not_a_set() -> None:
    c = _one("1 Sol Ring [Ramp]")
    assert (c.name, c.set_code) == ("Sol Ring", "")
    c = _one("1 Sol Ring (cmr) 436 [Ramp]")
    assert (c.name, c.set_code, c.collector_number) == ("Sol Ring", "cmr", "436")
    c = _one("1 Opt (xln) 65 *F* [Draw{top},Cantrips]")
    assert (c.name, c.set_code, c.collector_number, c.foil) == ("Opt", "xln", "65", True)


def test_a_set_in_square_brackets_is_still_read() -> None:
    c = _one("1 Fire // Ice [APC]")
    assert (c.name, c.set_code) == ("Fire // Ice", "apc")


def test_markers_are_not_part_of_the_name() -> None:
    assert _one("1 Aesi, Tyrant of Gyre Strait (cmr) 365 *F* [Commander{top}]").name == (
        "Aesi, Tyrant of Gyre Strait"
    )
    c = _one("1 Sol Ring (cmr) 472 *E*")
    assert (c.name, c.foil, c.etched) == ("Sol Ring", None, True)  # *E* is the etched finish
    assert _one("1 Sol Ring *CMDR*").name == "Sol Ring"
    assert _one("SB: 2 Negate").name == "Negate"
    assert _one("1 Sol Ring ^Have,#37d67a^").name == "Sol Ring"


def test_lines_that_are_not_cards_are_reported() -> None:
    unread: list[str] = []
    cards = parse_text("1 Sol Ring\nTotal: 100 cards\n" + "x" * 400 + "\n2 Opt", unread)
    assert [c.name for c in cards] == ["Sol Ring", "Opt"]
    assert len(unread) == 1 and unread[0].startswith("xxx")  # the footer is skipped, not a card
