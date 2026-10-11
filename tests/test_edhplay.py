"""0.7.25 (R-154): "Play on EDHPlay" on the deck page. EDHPlay has no import by link, so the press
copies the deck in EDHPlay's import format and opens edhplay.com. Ben (22:38Z 10-11): the full deck,
commander and sideboard included; (23:21Z) the maybeboard is never sent. The format was read from
EDHPlay's own help page, import dialog and import parser, then checked with live imports on a test
account (2026-10-11): its sideboard holds 15 cards (more and nothing is imported) and its import
drops tokens (export_formats.to_edhplay)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from mtg_gateway.archidekt import parse_deck
from mtg_gateway.export_formats import EDHPLAY_SIDEBOARD_MAX, EDHPLAY_URL, to_edhplay

from .fake_archidekt import FakeArchidekt
from .test_deck_menu_browser import Server, _chromium, _overflow, _page
from .test_deck_views_browser import _shot


def _deck():
    ark = FakeArchidekt()
    ark.add_side_row(42, "Cultivate", quantity=3)  # 3 more on the maybeboard
    ark.add_side_row(42, "Sol Ring", quantity=1, category="Sideboard")
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
    edh = to_edhplay(deck)
    text, commanders = edh.text, edh.commanders
    assert commanders == ["Aesi, Tyrant of Gyre Strait"]
    parts = _sections(text)
    main = parts["main"]
    assert re.fullmatch(
        r"1 Aesi, Tyrant of Gyre Strait( \([A-Z0-9]+\) [A-Za-z0-9-]+)? \[Commander\{top\}\]", main[0]
    )
    assert sum(int(line.split(" ", 1)[0]) for line in main) == 100  # commander included
    assert sum("[Commander{top}]" in line for line in text.splitlines()) == 1
    assert all(re.fullmatch(r"\d+ \S.*", line) for line in main), main
    # the sideboard follows after a blank line and the heading; the maybeboard is never sent
    assert [line.split(" (")[0] for line in parts["sideboard"]] == ["1 Sol Ring"]
    assert "\n\nSideboard\n" in text and "Maybeboard" not in text and "3 Cultivate" not in text
    assert any(line.startswith("1 Cultivate") for line in main)  # the deck's own copy stays main
    assert edh.maybeboard == 3 and edh.left_out == [] and edh.tokens == 0


def test_the_sideboard_stops_at_fifteen_copies_and_names_the_rest() -> None:
    """EDHPlay refuses the whole import when its Sideboard would pass 15 cards, so the copy holds
    the first 15 copies (the companion first) and the note names every copy left out."""
    ark = FakeArchidekt()
    ark.add_side_row(42, "Cultivate", quantity=9, category="Sideboard")
    ark.add_side_row(42, "Island", quantity=8, category="Sideboard")
    ark.add_side_row(42, "Sol Ring", quantity=1, category="Companion")
    edh = to_edhplay(parse_deck(ark.decks[42]))
    side = _sections(edh.text)["sideboard"]
    assert sum(int(line.split(" ", 1)[0]) for line in side) == EDHPLAY_SIDEBOARD_MAX == 15
    assert side[0].startswith("1 Sol Ring") and side[1].startswith("9 Cultivate")
    assert side[2].startswith("5 Island")
    assert [line.split(" (")[0] for line in edh.left_out] == ["3 Island"]


def test_printings_keep_set_and_number_when_edhplay_can_read_them() -> None:
    deck = _deck()
    card = next(c for c in deck.main_cards if not deck.is_commander(c))
    card.name, card.quantity, card.set_code, card.collector_number = "Sol Ring", 1, "c18", "222"
    assert "1 Sol Ring (C18) 222\n" in to_edhplay(deck).text
    card.collector_number = "222★"  # EDHPlay's parser can't read it: the name alone is sent
    assert "1 Sol Ring\n" in to_edhplay(deck).text


def test_double_faced_cards_keep_both_faces() -> None:
    deck = _deck()
    card = next(c for c in deck.main_cards if not deck.is_commander(c))
    card.name, card.set_code, card.collector_number = "Delver of Secrets // Insectile Aberration", "", ""
    assert "\n1 Delver of Secrets // Insectile Aberration\n" in to_edhplay(deck).text


def test_tokens_are_left_out_and_counted() -> None:
    """EDHPlay parses a Tokens section and then drops it (verified live), so nothing is sent and
    the note says to add tokens by hand."""
    ark = FakeArchidekt()
    ark.add_side_row(42, "Forest", quantity=2, category="Tokens")
    edh = to_edhplay(parse_deck(ark.decks[42]))
    assert "Tokens" not in edh.text and edh.tokens == 2 and "sideboard" not in _sections(edh.text)


def test_a_deck_without_commander_starts_with_a_line_of_several_copies() -> None:
    deck = _deck()
    deck.cards = [c for c in deck.cards if not deck.is_commander(c)]
    edh = to_edhplay(deck)
    text, commanders = edh.text, edh.commanders
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
        edh = to_edhplay(parse_deck(server.ark.decks[42]))
        assert ("maybeboard isn't sent" in note.inner_text()) == (edh.maybeboard > 0)
        assert "Left out" not in note.inner_text() and edh.left_out == []
        copied = page.evaluate("() => navigator.clipboard.readText()")
        assert copied.replace("\r\n", "\n") == edh.text
        assert _overflow(page) <= 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1366, "light")])
def test_the_note_names_what_was_left_out(server: Server, width: int, scheme: str) -> None:
    """A sideboard past 15, a maybeboard and tokens: the note says each, once, and stays up."""
    from playwright.sync_api import sync_playwright

    _chromium()
    ark = server.ark
    ark.add_side_row(42, "Cultivate", quantity=16, category="Sideboard")
    ark.add_side_row(42, "Island", quantity=2)  # maybeboard
    ark.add_side_row(42, "Forest", quantity=1, category="Tokens")
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(
            p, server, sid, width, color_scheme=scheme, permissions=["clipboard-read", "clipboard-write"]
        )
        page.context.route(re.compile(r"https://edhplay\.com/.*"), lambda route: route.fulfill(status=200))
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        with page.context.expect_page():
            page.locator(".banner a[data-edhplay]").click()
        note = page.locator(".edhplay-toast")
        note.wait_for()
        text = note.inner_text()
        assert "Left out because EDHPlay's sideboard holds only 15 cards: 1 Cultivate" in text, text
        assert text.count("The maybeboard isn't sent to EDHPlay.") == 1
        assert text.count("EDHPlay doesn't take tokens") == 1
        copied = page.evaluate("() => navigator.clipboard.readText()").replace("\r\n", "\n")
        assert "15 Cultivate" in copied and "Island" not in copied.split("Sideboard")[1]
        assert "Forest" not in copied.split("Sideboard")[1] and "Tokens" not in copied
        assert _overflow(page) <= 0 and errors == []
        _shot(page, f"edhplay-note-cut-{width}-{scheme}")
        browser.close()
