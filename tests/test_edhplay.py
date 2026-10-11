"""0.7.25 (R-154): "Play on EDHPlay" on the deck page. EDHPlay has no import by link, so the press
copies the whole deck in EDHPlay's import format and opens edhplay.com. Ben (22:38Z 10-11): the full
deck, commander, sideboard and maybeboard included. The format was read from EDHPlay's own help
page, import dialog and import parser on 2026-10-11 (export_formats.to_edhplay)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mtg_gateway.archidekt import parse_deck
from mtg_gateway.export_formats import EDHPLAY_URL, to_edhplay

from .fake_archidekt import FakeArchidekt
from .test_deck_menu_browser import Server, _chromium, _overflow, _page


def _deck():
    ark = FakeArchidekt()
    ark.add_side_row(42, "Cultivate", quantity=3)  # 3 more on the maybeboard
    return parse_deck(ark.decks[42])


def _sections(text: str) -> dict[str, list[str]]:
    """The copied text cut the way EDHPlay's import reads it: the main part up to the first
    heading, then each heading's lines up to the next blank line."""
    out: dict[str, list[str]] = {"main": []}
    current = "main"
    for line in text.split("\n"):
        if line in ("Sideboard", "Tokens"):
            current = line.lower()
            out[current] = []
        elif line.strip():
            out[current].append(line)
    return out


def test_the_whole_deck_is_copied_commander_first_and_marked() -> None:
    deck = _deck()
    text, commanders = to_edhplay(deck)
    assert commanders == ["Aesi, Tyrant of Gyre Strait"]
    parts = _sections(text)
    main = parts["main"]
    assert re.fullmatch(
        r"1 Aesi, Tyrant of Gyre Strait( \([A-Z0-9]+\) [A-Za-z0-9-]+)? \[Commander\{top\}\]", main[0]
    )
    assert sum(int(line.split(" ", 1)[0]) for line in main) == 100  # commander included
    assert sum("[Commander{top}]" in line for line in text.splitlines()) == 1
    assert all(re.fullmatch(r"\d+ \S.*", line) for line in main), main
    # the maybeboard (EDHPlay has none) goes to its Sideboard, after a blank line and the heading
    assert parts["sideboard"] and any(line.startswith("3 Cultivate") for line in parts["sideboard"])
    assert "\n\nSideboard\n" in text and "Maybeboard" not in text
    assert any(line.startswith("1 Cultivate") for line in main)  # the deck's own copy stays main


def test_printings_keep_set_and_number_when_edhplay_can_read_them() -> None:
    deck = _deck()
    card = next(c for c in deck.main_cards if not deck.is_commander(c))
    card.name, card.quantity, card.set_code, card.collector_number = "Sol Ring", 1, "c18", "222"
    assert "1 Sol Ring (C18) 222\n" in to_edhplay(deck)[0]
    card.collector_number = "222★"  # EDHPlay's parser can't read it: the name alone is sent
    assert "1 Sol Ring\n" in to_edhplay(deck)[0]


def test_double_faced_cards_keep_both_faces() -> None:
    deck = _deck()
    card = next(c for c in deck.main_cards if not deck.is_commander(c))
    card.name, card.set_code, card.collector_number = "Delver of Secrets // Insectile Aberration", "", ""
    assert "\n1 Delver of Secrets // Insectile Aberration\n" in to_edhplay(deck)[0]


def test_tokens_get_their_own_section() -> None:
    ark = FakeArchidekt()
    ark.add_side_row(42, "Forest", quantity=2, category="Tokens")
    text, _ = to_edhplay(parse_deck(ark.decks[42]))
    assert _sections(text)["tokens"] == [line for line in _sections(text)["tokens"] if "Forest" in line]
    assert "\n\nTokens\n" in text


def test_a_deck_without_commander_starts_with_a_line_of_several_copies() -> None:
    deck = _deck()
    deck.cards = [c for c in deck.cards if not deck.is_commander(c)]
    text, commanders = to_edhplay(deck)
    assert commanders == [] and "Commander{top}" not in text
    first = text.splitlines()[0]
    assert int(first.split(" ", 1)[0]) > 1, first  # EDHPlay would take a first single copy as commander


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.mark.parametrize("width", [390, 1366])
def test_the_button_copies_the_list_and_opens_edhplay(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(
            p, server, sid, width, permissions=["clipboard-read", "clipboard-write"]
        )
        opened: list[str] = []
        page.context.route(
            re.compile(r"https://edhplay\.com/.*"),
            lambda route: (opened.append(route.request.url), route.fulfill(status=200, body="edhplay"))[1],
        )
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        btn = page.locator(".banner a[data-edhplay]")
        assert btn.count() == 1 and btn.inner_text().strip() == "Play on EDHPlay"
        assert btn.get_attribute("href") == EDHPLAY_URL and btn.get_attribute("target") == "_blank"
        assert "noopener" in (btn.get_attribute("rel") or "")
        with page.context.expect_page() as popup:
            btn.click()
        popup.value.wait_for_load_state()
        assert opened and opened[0].startswith(EDHPLAY_URL)
        note = page.locator(".edhplay-toast")
        note.wait_for()
        assert "Deck list copied." in note.inner_text()
        assert "Create Deck with Aesi, Tyrant of Gyre Strait as the commander" in note.inner_text()
        assert "Import & Replace" in note.inner_text()
        copied = page.evaluate("() => navigator.clipboard.readText()")
        expected, _ = to_edhplay(parse_deck(server.ark.decks[42]))
        assert copied.replace("\r\n", "\n") == expected
        assert _overflow(page) <= 0
        assert errors == []
        browser.close()
