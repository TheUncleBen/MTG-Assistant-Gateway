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
    assert edh.left_out == []


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
    assert edh.left_out == [(3, "Island")]


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


def test_tokens_are_left_out() -> None:
    """EDHPlay parses a Tokens section and then drops it (verified live), so nothing is sent and
    the note says to add tokens by hand."""
    ark = FakeArchidekt()
    before = to_edhplay(parse_deck(ark.decks[42])).text
    ark.add_side_row(42, "Forest", quantity=2, category="Tokens")
    edh = to_edhplay(parse_deck(ark.decks[42]))
    assert edh.text == before and "Tokens" not in edh.text and "sideboard" not in _sections(edh.text)


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


# Text in the note against the background it is drawn on (WCAG contrast, at least 4.5:1).
CONTRAST = """() => {
  function rgb(s) { var m = s.match(/[\\d.]+/g); return m ? m.map(Number) : [0, 0, 0, 0]; }
  function lum(c) {
    var a = c.slice(0, 3).map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
  }
  function bg(e) {
    for (; e; e = e.parentElement) {
      var c = rgb(getComputedStyle(e).backgroundColor);
      if (c.length < 4 || c[3] > 0) return c;
    }
    return [255, 255, 255];
  }
  var worst = 99;
  document.querySelectorAll(".edhplay-toast .msg span, .edhplay-toast .acts button").forEach(function (e) {
    var a = lum(rgb(getComputedStyle(e).color)), b = lum(bg(e));
    worst = Math.min(worst, (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05));
  });
  return worst;
}"""


def _press(page) -> str:
    with page.context.expect_page() as popup:
        page.locator(".banner a[data-edhplay]").click()
    popup.value.wait_for_load_state()
    note = page.locator(".edhplay-toast")
    note.wait_for()
    return note.inner_text()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (673, "light"), (1366, "light")])
def test_the_button_copies_the_list_and_shows_the_tip_once(server: Server, width: int, scheme: str) -> None:
    """The first press on a device adds a short tip on EDHPlay's import limits, kept until it is
    closed; after that a deck that fits shows only the one line."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(
            p, server, sid, width, color_scheme=scheme, permissions=["clipboard-read", "clipboard-write"]
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
        text = _press(page)
        assert opened and opened[0].startswith(EDHPLAY_URL)
        edh = to_edhplay(parse_deck(server.ark.decks[42]))
        assert edh.left_out == []
        assert page.locator(".edhplay-toast .msg span").all_inner_texts() == [
            "Deck list copied. On EDHPlay: Create Deck, then Import, paste and Import & Replace.",
            "Good to know: EDHPlay's sideboard holds 15 cards, the maybeboard isn't sent, and tokens are "
            "added on EDHPlay by hand.",
        ], text
        copied = page.evaluate("() => navigator.clipboard.readText()")
        assert copied.replace("\r\n", "\n") == edh.text
        assert page.evaluate(CONTRAST) >= 4.5
        assert _overflow(page) <= 0
        _shot(page, f"edhplay-first-tip-{width}-{scheme}")
        page.locator(".edhplay-toast .acts button", has_text="Got it").click()
        assert page.locator(".edhplay-toast").count() == 0
        # the next press, after a reload: only the one line, and it goes by itself
        page.reload(wait_until="networkidle")
        _press(page)
        assert page.locator(".edhplay-toast .msg span").count() == 1
        assert "warn" not in (page.locator(".edhplay-toast").get_attribute("class") or "")
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (673, "dark"), (1366, "light")])
def test_a_cut_sideboard_is_named_in_one_line(server: Server, width: int, scheme: str) -> None:
    """A sideboard past 15 copies: the cards left out are named in one line that stays up; the
    maybeboard and tokens are not sent and add nothing once the tip has been seen."""
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
        page.evaluate("() => localStorage.setItem('mtg.edhplay.tip', '1')")  # the tip was seen before
        _press(page)
        assert page.locator(".edhplay-toast .msg span").all_inner_texts() == [
            "Deck list copied. On EDHPlay: Create Deck, then Import, paste and Import & Replace.",
            "1 sideboard card didn't fit EDHPlay's 15-card limit: Cultivate.",
        ]
        assert "warn" in (page.locator(".edhplay-toast").get_attribute("class") or "")
        copied = page.evaluate("() => navigator.clipboard.readText()").replace("\r\n", "\n")
        assert "15 Cultivate" in copied and "Island" not in copied.split("Sideboard")[1]
        assert "Forest" not in copied.split("Sideboard")[1] and "Tokens" not in copied
        assert page.evaluate(CONTRAST) >= 4.5
        assert _overflow(page) <= 0 and errors == []
        _shot(page, f"edhplay-cut-{width}-{scheme}")
        page.wait_for_timeout(9000)
        assert page.locator(".edhplay-toast").count() == 1  # a cut list stays until it is closed
        browser.close()
