"""0.7.25 (R-154): "Play on EDHPlay" on the deck page. EDHPlay has no import by link, so the press
copies the deck in EDHPlay's import format and opens edhplay.com. Ben (22:38Z 10-11): the full deck,
commander and sideboard included; (23:21Z) the maybeboard is never sent. The format was read from
EDHPlay's own help page, import dialog and import parser, then checked with live imports on a test
account (2026-10-11): its sideboard holds 15 cards (more and nothing is imported) and its import
drops tokens (export_formats.to_edhplay)."""

from __future__ import annotations

import json
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


def test_a_companion_set_by_archidekts_flag_leads_the_sideboard() -> None:
    """A companion marked with Archidekt's companion flag on a Sideboard row (no Companion
    category) goes first in the Sideboard section, so a cut never drops it. EDHPlay took such a
    list and put the card in its sideboard (verified live 2026-10-14); its import never sets a
    companion, so the member picks it there."""
    ark = FakeArchidekt()
    ark.add_side_row(42, "Cultivate", quantity=2, category="Sideboard")
    ark.add_side_row(42, "Sol Ring", quantity=14, category="Sideboard")
    ark.add_side_row(42, "Yavimaya Elder", quantity=1, category="Sideboard")
    ark.decks[42]["cards"][-1]["companion"] = True
    deck = parse_deck(ark.decks[42])
    assert next(c for c in deck.side_cards if c.name == "Yavimaya Elder").companion
    edh = to_edhplay(deck)
    side = _sections(edh.text)["sideboard"]
    assert side[0].startswith("1 Yavimaya Elder") and side[1].startswith("2 Cultivate")
    assert side[2].startswith("12 Sol Ring") and edh.left_out == [(2, "Sol Ring")]
    assert edh.companion and edh.commanders == ["Aesi, Tyrant of Gyre Strait"]
    assert not to_edhplay(parse_deck(FakeArchidekt().decks[42])).companion


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


def _edhplay_commander(text: str) -> str | None:
    """The commander EDHPlay's import dialog picks from a pasted list (its parser ``tc`` in
    DeckEditorSkeleton-*.js, read 2026-10-14): a line marked ``[...Commander{top}...]``; else, with
    a Sideboard heading, a lone single line after a blank line in that section; else, with no
    Sideboard, a last single line set off by a blank line; else the first main-deck card when its
    copies (summed over lines of the same printing) are 1. The import then sends that card as the
    commander, and EDHPlay makes it the commander when the card can be one (verified live
    2026-10-14: a legendary creature first became the commander, in a Commander deck and in a
    Modern deck; a land or a non-legendary creature first stayed in the main deck)."""
    lines = [line.strip() for line in text.split("\n")]
    heads = {"sideboard", "tokens", "attractions", "stickers"}

    def parse(line: str) -> tuple[str, int, bool] | None:
        if not line or line.lower().rstrip(":") in {*heads, "deck", "maybeboard", "companion", "about"}:
            return None
        m = re.match(r"^(\d+)\s*x?\s+", line)
        qty, rest = (int(m.group(1)), line[m.end() :]) if m else (1, line)
        marked = re.search(r"\[[^\]]*Commander\{top\}[^\]]*\]", rest, re.I) is not None
        rest = re.sub(r"\[[^\]]*Commander\{top\}[^\]]*\]", "", rest, flags=re.I).strip()
        return rest, qty, marked

    side = next((i for i, line in enumerate(lines) if line.lower() == "sideboard"), -1)
    end = min([i for i, line in enumerate(lines) if line.lower() in heads] or [len(lines)])
    main: dict[str, list] = {}
    for line in lines[:end]:
        got = parse(line)
        if got:
            if got[0] in main:
                main[got[0]][0] += got[1]
                main[got[0]][1] |= got[2]
            else:
                main[got[0]] = [got[1], got[2]]

    def name(key: str) -> str:  # the card name, without "(SET) NUMBER"
        return re.sub(r"\s*\([A-Z0-9]+\)\s+[A-Z0-9-]+", "", key, count=1, flags=re.I).strip()

    marked = [key for key, (_q, m) in main.items() if m]
    if marked:
        return name(marked[0])
    if side != -1:
        rest = lines[side:]
        if "" in rest:
            after = [line for line in rest[rest.index("") :] if line]
            if len(after) == 1 and (got := parse(after[0])) and got[1] == 1:
                return name(got[0])
    keys = list(main)
    if keys and side == -1:
        main_lines = [line for line in lines[:end] if line]
        last = lines[:end].index(main_lines[-1]) if main_lines else -1
        if main[keys[-1]][0] == 1 and last > 0 and lines[last - 1] == "":
            return name(keys[-1])
    if keys and main[keys[0]][0] == 1:
        return name(keys[0])
    return None


def test_the_reading_of_edhplays_parser_matches_its_known_answers() -> None:
    assert _edhplay_commander("1 Sol Ring\n1 Opt\n") == "Sol Ring"
    assert _edhplay_commander("2 Island\n1 Sol Ring\n") is None
    assert _edhplay_commander("1 Opt\n1 Aesi (CMR) 365 [Commander{top}]\n") == "Aesi"
    assert _edhplay_commander("1 Sol Ring (C18) 222\n") == "Sol Ring"
    assert _edhplay_commander("2 Island\n\nSideboard\n1 Opt\n") is None


def test_a_deck_without_commander_starts_with_a_line_of_several_copies() -> None:
    deck = _deck()
    deck.cards = [c for c in deck.cards if not deck.is_commander(c)]
    edh = to_edhplay(deck)
    text, commanders = edh.text, edh.commanders
    assert commanders == [] and "Commander{top}" not in text
    first = text.splitlines()[0]
    assert int(first.split(" ", 1)[0]) > 1, first  # EDHPlay would take a first single copy as commander
    assert _edhplay_commander(text) is None


@pytest.mark.parametrize("fmt", [3, 1, None])  # Commander, Standard, no format set
def test_only_a_real_commander_is_marked_in_an_all_single_copy_deck(fmt: int | None) -> None:
    """A deck with no commander set and one copy of every card (a Commander deck before its
    commander is chosen, a highlander deck, a cube): EDHPlay's import takes the first single copy
    as the commander, and a legendary creature first became the commander on EDHPlay (verified
    live 2026-10-14). The list starts with a card that can't be one, a land first."""
    deck = _deck()
    deck.format_id = fmt
    aesi = next(c for c in deck.cards if deck.is_commander(c))
    aesi.categories = ["Creature"]  # in the deck, but not in the commander zone
    aesi.supertypes = ["Legendary"]
    deck.cards = [c for c in deck.cards if c.name != "Acidic Slime"]  # Aesi sorts first
    for c in deck.cards:
        c.quantity = 1
    assert not any(deck.is_commander(c) for c in deck.cards)
    edh = to_edhplay(deck)
    assert edh.commanders == [] and "Commander{top}" not in edh.text
    assert _edhplay_commander(edh.text) != aesi.name
    first = edh.text.splitlines()[0]
    card = next(c for c in deck.cards if first.startswith(f"1 {c.name} ") or first == f"1 {c.name}")
    assert "Land" in card.types and "Creature" not in card.types and "Legendary" not in card.supertypes
    # nothing else moved: the rest of the deck keeps its order, every card once
    main = _sections(edh.text)["main"]
    assert sorted(main) == sorted(to_edhplay_lines(deck)) and main[1:] == sorted(main[1:], key=str.casefold)


def test_an_all_single_copy_deck_without_a_land_starts_with_a_non_creature() -> None:
    deck = _deck()
    deck.cards = [c for c in deck.cards if "Land" not in c.types]
    aesi = next(c for c in deck.cards if deck.is_commander(c))
    aesi.categories = ["Creature"]
    aesi.supertypes = ["Legendary"]
    for c in deck.cards:
        c.quantity = 1
    text = to_edhplay(deck).text
    first = text.splitlines()[0]
    card = next(c for c in deck.cards if first.startswith(f"1 {c.name} "))
    assert "Creature" not in card.types and "Planeswalker" not in card.types
    assert "Legendary" not in card.supertypes
    assert _edhplay_commander(text) == card.name  # what EDHPlay sends, and refuses as a commander


def test_a_legendary_land_ranks_with_the_lands_not_the_commanders() -> None:
    """L15: a single legendary land is a land: it leads a deck of single copies with no
    commander, ahead of a legendary creature that sorts before it."""
    deck = _deck()
    aesi = next(c for c in deck.cards if deck.is_commander(c))
    aesi.categories, aesi.supertypes = ["Creature"], ["Legendary"]  # a possible commander, unmarked
    lands = [c for c in deck.cards if "Land" in c.types and "Creature" not in c.types]
    keep = lands[0]
    keep.name, keep.supertypes, keep.quantity = "Zzz Legendary Land", ["Legendary"], 1
    deck.cards = [c for c in deck.cards if c is keep or "Land" not in c.types]
    deck.cards = [c for c in deck.cards if "Creature" in c.types or c is keep]  # no other lead
    for c in deck.cards:
        c.quantity = 1
    text = to_edhplay(deck).text
    assert text.splitlines()[0].startswith("1 Zzz Legendary Land"), text.splitlines()[:3]
    assert _edhplay_commander(text) == "Zzz Legendary Land"  # sent, and refused: a land


def test_a_list_with_only_a_sideboard_starts_at_its_heading() -> None:
    """L16: no blank first line when there is no main deck to end."""
    ark = FakeArchidekt()
    ark.add_side_row(42, "Sol Ring", quantity=1, category="Sideboard")
    deck = parse_deck(ark.decks[42])
    deck.cards = [c for c in deck.cards if not deck.in_deck(c)]
    text = to_edhplay(deck).text
    assert text.startswith("Sideboard\n1 Sol Ring"), text
    assert _sections(text)["main"] == []
    # with a main deck, the blank line before the heading stays
    assert "\n\nSideboard\n" in to_edhplay(parse_deck(ark.decks[42])).text


def test_a_companion_in_a_main_deck_category_moves_to_the_sideboard() -> None:
    """L17: a row flagged as the companion but filed in a main-deck category goes to the
    Sideboard (first) with the box's companion note, as a companion does in the Arena export."""
    ark = FakeArchidekt()
    deck_json = ark.decks[42]
    row = next(c for c in deck_json["cards"] if c["card"]["oracleCard"]["name"] == "Acidic Slime")
    row["companion"] = True
    deck = parse_deck(deck_json)
    slime = next(c for c in deck.cards if c.name == "Acidic Slime")
    assert deck.in_deck(slime) and slime.companion
    edh = to_edhplay(deck)
    parts = _sections(edh.text)
    assert parts["sideboard"][0].startswith("1 Acidic Slime") and edh.companion
    assert not any(line.startswith("1 Acidic Slime") for line in parts["main"])


def to_edhplay_lines(deck) -> list[str]:
    from mtg_gateway.export_formats import _edh_line

    return [_edh_line(c) for c in deck.main_cards]


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


# Text in the dialog and the note against the background it is drawn on (WCAG contrast, 4.5:1).
CONTRAST = """() => {
  function rgb(s) { var m = s.match(/[\\d.]+/g); return m ? m.map(Number) : [0, 0, 0, 0]; }
  function lum(c) {
    var a = c.slice(0, 3).map(function (v) {
      v /= 255;
      return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4);
    });
    return 0.2126 * a[0] + 0.7152 * a[1] + 0.0722 * a[2];
  }
  function bg(e) {  // a see-through background (a notice's tint) is laid over what is under it
    var layers = [];
    for (; e; e = e.parentElement) {
      var c = rgb(getComputedStyle(e).backgroundColor);
      if (c.length < 4 || c[3] >= 1) { layers.push(c); break; }
      if (c[3] > 0) layers.push(c);
    }
    var out = layers.length && (layers[layers.length - 1].length < 4 || layers[layers.length - 1][3] >= 1)
      ? layers.pop().slice(0, 3) : [255, 255, 255];
    while (layers.length) {
      var l = layers.pop();
      out = out.map(function (v, i) { return l[i] * l[3] + v * (1 - l[3]); });
    }
    return out;
  }
  var worst = 99;
  document.querySelectorAll(".edhplay-toast .msg span, .edhplay-dialog h2, .edhplay-dialog li,"
    + " .edhplay-dialog p, .edhplay-dialog .acts .btn").forEach(function (e) {
    var a = lum(rgb(getComputedStyle(e).color)), b = lum(bg(e));
    worst = Math.min(worst, (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05));
  });
  return worst;
}"""

# What EDHPlay shows, checked on its site at 390 px on 2026-10-14: My Decks, Add Deck (a Create a
# Deck box: name, format), the new deck in the list, its Import, then Import & Add or Import & Replace.
STEPS = [
    "On EDHPlay, go to My Decks, choose Add Deck, then open the new deck.",
    "Press Import, paste, then press Import & Replace.",
]
GO = "Copy and Open EDHPlay.com"
COPIED = (
    "Deck list copied. On EDHPlay: My Decks, Add Deck, open the deck, Import, paste, then Import & Replace."
)


def _open_dialog(page, *, ready: bool = True):
    page.locator(".banner a[data-edhplay]").click()
    dialog = page.locator(".edhplay-dialog")
    dialog.wait_for()
    if ready:  # the list has come: the button is on
        page.wait_for_function(
            "() => { var a = document.querySelector('.edhplay-dialog .acts a');"
            " return a && !a.hasAttribute('aria-disabled') && !a.hasAttribute('aria-busy'); }"
        )
    return dialog


def _launch(page) -> str:
    with page.context.expect_page() as popup:
        page.locator(".edhplay-dialog a", has_text=GO).click()
    popup.value.wait_for_load_state()
    # EDHPlay's tab opened in front; the deck page's tab comes back to the front, as the member's
    # return to it does. Left behind it, the page's frames and timers are throttled, and every
    # later click waited a second or two for the page to draw.
    page.bring_to_front()
    note = page.locator(".edhplay-toast")
    note.wait_for()
    return note.inner_text()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (673, "light"), (1280, "light")])
def test_the_button_opens_a_dialog_then_copies_and_launches(server: Server, width: int, scheme: str) -> None:
    """0.7.27 (T-161): every press opens a small box and opens nothing yet; Escape, the backdrop and
    × close it and give focus back to the button (no Cancel: Ben, 02:08Z 10-14), Tab stays inside
    it; its Copy and Open EDHPlay.com copies the list and opens EDHPlay. The page doesn't carry the
    list: the box asks for it as it opens. The two import steps show open until the first launch on
    this device, then folded under How to import (Ben, 01:36Z 10-14). The 0.7.25 first-time tip is
    gone, and so is the mark it kept in this browser."""
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
        page.evaluate("() => localStorage.setItem('mtg.edhplay.tip', '1')")  # left by 0.7.25 or 0.7.26
        page.reload(wait_until="networkidle")
        assert page.evaluate("() => localStorage.getItem('mtg.edhplay.tip')") is None
        btn = page.locator(".banner a[data-edhplay]")
        assert btn.count() == 1 and btn.inner_text().strip() == "Play on EDHPlay"
        assert (
            btn.get_attribute("data-list") is None
            and btn.get_attribute("data-src") == "/decks/42/edhplay.json"
        )
        assert btn.get_attribute("href") == EDHPLAY_URL and btn.get_attribute("target") == "_blank"
        assert EDHPLAY_URL == "https://edhplay.com/decks"  # straight to My Decks (Q52)
        assert "noopener" in (btn.get_attribute("rel") or "")

        dialog = _open_dialog(page)
        assert opened == [] and len(page.context.pages) == 1  # nothing opens before the second press
        assert dialog.get_attribute("role") == "dialog" and dialog.get_attribute("aria-modal") == "true"
        assert dialog.locator("h2").inner_text() == "Play on EDHPlay"
        how = dialog.locator("details.edhplay-steps")
        assert how.get_attribute("open") is not None  # not launched from here yet: the steps show
        assert how.locator("summary").inner_text() == "How to import"
        assert dialog.locator("ol li").all_inner_texts() == STEPS
        assert dialog.locator(".notice.warn").count() == 0  # nothing cut from this deck
        assert (
            dialog.locator("button", has_text="Cancel").count() == 0 and "Cancel" not in dialog.inner_text()
        )
        go = dialog.locator("a", has_text=GO)
        assert go.get_attribute("href") == EDHPLAY_URL and go.get_attribute("target") == "_blank"
        # the one action fills the row
        row = dialog.locator(".acts").bounding_box()
        assert abs(go.bounding_box()["width"] - row["width"]) <= 1
        assert page.evaluate("() => document.activeElement.textContent") == GO
        # the box sits in the middle of the window, inside it
        box = dialog.locator(".pickbox").bounding_box()
        vw = page.viewport_size["width"]
        assert box["x"] >= 0 and box["x"] + box["width"] <= vw
        assert abs((box["x"] + box["width"] / 2) - vw / 2) <= 2
        assert abs((box["y"] + box["height"] / 2) - page.viewport_size["height"] / 2) <= 2
        assert box["width"] <= 30 * 16 + 1  # a small box, not the editor's wide picker
        assert page.evaluate(CONTRAST) >= 4.5
        assert _overflow(page) <= 0
        _shot(page, f"edhplay-dialog-{width}-{scheme}")
        # Tab goes round inside the dialog: from Copy and Open to ×, How to import, back to Copy and Open
        focus = (
            "() => document.activeElement.getAttribute('aria-label') || document.activeElement.textContent"
        )
        cycle = []
        for _ in range(4):
            page.keyboard.press("Tab")
            cycle.append(page.evaluate(focus))
        assert cycle == ["Close", "How to import", GO, "Close"]
        page.keyboard.press("Shift+Tab")
        assert page.evaluate(focus) == GO
        page.keyboard.press("Shift+Tab")
        assert page.evaluate(focus) == "How to import"
        # Escape, a press on the backdrop and × each close it; focus goes back to the button
        page.keyboard.press("Escape")
        assert page.locator(".edhplay-dialog").count() == 0
        assert page.evaluate("() => document.activeElement.hasAttribute('data-edhplay')")
        _open_dialog(page)
        page.mouse.click(3, 3)
        assert page.locator(".edhplay-dialog").count() == 0
        _open_dialog(page).locator("button[aria-label='Close']").click()
        assert page.locator(".edhplay-dialog").count() == 0
        assert opened == [] and page.locator(".edhplay-toast").count() == 0
        # closing without launching keeps the steps open for next time
        assert _open_dialog(page).locator("details.edhplay-steps").get_attribute("open") is not None
        page.keyboard.press("Escape")

        _open_dialog(page)
        _launch(page)
        assert page.locator(".edhplay-dialog").count() == 0
        assert opened and opened[0].startswith(EDHPLAY_URL)
        edh = to_edhplay(parse_deck(server.ark.decks[42]))
        assert edh.left_out == []
        assert page.locator(".edhplay-toast .msg span").all_inner_texts() == [COPIED]
        assert "warn" not in (page.locator(".edhplay-toast").get_attribute("class") or "")
        copied = page.evaluate("() => navigator.clipboard.readText()")
        assert copied.replace("\r\n", "\n") == edh.text
        assert page.evaluate(CONTRAST) >= 4.5
        assert page.evaluate("() => localStorage.getItem('mtg.edhplay.tip')") is None
        # after a launch the box still opens on every press, with the steps folded away
        page.reload(wait_until="networkidle")
        dialog = _open_dialog(page)
        how = dialog.locator("details.edhplay-steps")
        assert how.get_attribute("open") is None and not dialog.locator("ol").is_visible()
        assert page.evaluate(CONTRAST) >= 4.5 and _overflow(page) <= 0
        _shot(page, f"edhplay-dialog-folded-{width}-{scheme}")
        how.locator("summary").click()
        assert dialog.locator("ol li").all_inner_texts() == STEPS
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (673, "dark"), (1280, "light")])
def test_a_cut_sideboard_is_named_in_one_line(server: Server, width: int, scheme: str) -> None:
    """A sideboard past 15 copies: the dialog names the cards left out before anything is copied,
    and so does the note after, which stays up; the maybeboard and tokens are not sent."""
    from playwright.sync_api import sync_playwright

    _chromium()
    ark = server.ark
    ark.add_side_row(42, "Cultivate", quantity=16, category="Sideboard")
    ark.add_side_row(42, "Island", quantity=2)  # maybeboard
    ark.add_side_row(42, "Forest", quantity=1, category="Tokens")
    sid = server.sign_in_and_link()
    cut = "1 sideboard card is left out to fit EDHPlay's 15-card limit: Cultivate."
    with sync_playwright() as p:
        browser, page, errors = _page(
            p, server, sid, width, color_scheme=scheme, permissions=["clipboard-read", "clipboard-write"]
        )
        page.context.route(re.compile(r"https://edhplay\.com/.*"), lambda route: route.fulfill(status=200))
        page.clock.install()  # runs as usual until the test moves it on past the note's 8 s
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        dialog = _open_dialog(page)
        assert dialog.locator(".notice.warn").inner_text() == cut
        assert "maybeboard and tokens" in dialog.locator("p.muted").inner_text()
        assert page.evaluate(CONTRAST) >= 4.5
        assert _overflow(page) <= 0
        _shot(page, f"edhplay-cut-dialog-{width}-{scheme}")
        _launch(page)
        assert page.locator(".edhplay-toast .msg span").all_inner_texts() == [
            COPIED,
            cut,
        ]
        assert "warn" in (page.locator(".edhplay-toast").get_attribute("class") or "")
        copied = page.evaluate("() => navigator.clipboard.readText()").replace("\r\n", "\n")
        assert "15 Cultivate" in copied and "Island" not in copied.split("Sideboard")[1]
        assert "Forest" not in copied.split("Sideboard")[1] and "Tokens" not in copied
        assert page.evaluate(CONTRAST) >= 4.5
        assert _overflow(page) <= 0 and errors == []
        _shot(page, f"edhplay-cut-{width}-{scheme}")
        page.clock.run_for(9000)  # past the 8 s a plain note stays, without waiting for them
        assert page.locator(".edhplay-toast").count() == 1  # a cut list stays until it is closed
        browser.close()


def _deck_reads(server: Server) -> int:
    return sum(1 for method, path in server.ark.calls if method == "GET" and "/decks/42/" in path)


def test_the_list_comes_from_its_own_address_with_the_deck_pages_access(server: Server) -> None:
    """GET /decks/{id}/edhplay.json: signed in, the deck as the deck page reads it (the read the
    page just made, so no new Archidekt request), never stored by the browser; signed out, 401;
    a deck the member can't read, the deck page's refusal."""
    import httpx

    sid = server.sign_in_and_link()
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    page = h.get("/decks/42")
    assert page.status_code == 200 and "data-list=" not in page.text
    assert "data-src='/decks/42/edhplay.json'" in page.text
    reads = _deck_reads(server)
    r = h.get("/decks/42/edhplay.json")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.headers["content-type"].startswith("application/json")
    edh = to_edhplay(parse_deck(server.ark.decks[42]))
    assert r.json() == {
        "ok": True,
        "list": edh.text,
        "left_out": "",
        "left_count": 0,
        "companion": False,
        "no_commander": False,
    }
    assert _deck_reads(server) == reads  # the page's own read, kept a short while
    server.ark.add_side_row(42, "Cultivate", quantity=17, category="Sideboard")
    h.get("/decks/42", headers={"Cache-Control": "max-age=0"})  # the browser's Reload reads again
    d = h.get("/decks/42/edhplay.json").json()
    assert d["left_count"] == 2 and d["left_out"] == "2 Cultivate" and "15 Cultivate" in d["list"]
    assert d["companion"] is False and d["no_commander"] is False

    out = httpx.get(f"{server.base}/decks/42/edhplay.json", follow_redirects=False)
    assert out.status_code == 401 and out.json() == {"ok": False, "error": "unauthenticated"}
    assert h.get("/decks/43").status_code == 404  # amy's private deck: the page refuses it
    other = h.get("/decks/43/edhplay.json")
    assert other.status_code == 404 and other.json()["ok"] is False and other.json()["message"]


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1280, "light")])
def test_the_box_waits_for_the_list_and_says_when_it_cannot_have_it(
    server: Server, width: int, scheme: str
) -> None:
    """While the list is on its way the button shows it is busy and a press opens nothing; when it
    can't be had the box says why, with Try again, and the button stays off; Try again fetches it."""
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
        held: list = []  # the list's request, held until let go
        page.route("**/edhplay.json", lambda route: held.append(route))
        dialog = _open_dialog(page, ready=False)
        go = dialog.locator(".acts a")
        assert go.get_attribute("aria-busy") == "true" and go.get_attribute("aria-disabled") == "true"
        assert page.evaluate("() => document.activeElement.textContent") == GO
        _shot(page, f"edhplay-loading-{width}-{scheme}")
        go.click(force=True)  # not ready: nothing opens, nothing is copied, the box stays
        page.wait_for_timeout(300)
        assert opened == [] and len(page.context.pages) == 1 and page.locator(".edhplay-dialog").count() == 1
        held.pop().continue_()
        page.wait_for_function("() => !document.querySelector('.edhplay-dialog .acts a[aria-disabled]')")
        assert go.get_attribute("aria-busy") is None
        page.keyboard.press("Escape")
        page.unroute("**/edhplay.json")

        # no connection: the reason and Try again, inside the focus loop; the button stays off
        page.route("**/edhplay.json", lambda route: route.abort())
        dialog = _open_dialog(page, ready=False)
        err = dialog.locator(".notice.error")
        err.wait_for()
        assert err.get_attribute("role") == "alert"
        assert err.locator("span").inner_text() == "The deck list couldn't be loaded: no connection."
        assert go.get_attribute("aria-disabled") == "true" and go.get_attribute("aria-busy") is None
        assert page.evaluate(CONTRAST) >= 4.5 and _overflow(page) <= 0
        box = dialog.locator(".pickbox").bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= page.viewport_size["width"]
        _shot(page, f"edhplay-error-{width}-{scheme}")
        focus = (
            "() => document.activeElement.getAttribute('aria-label') || document.activeElement.textContent"
        )
        cycle = []
        for _ in range(4):
            page.keyboard.press("Tab")
            cycle.append(page.evaluate(focus))
        assert cycle == ["Close", "How to import", "Try again", GO]
        go.click(force=True)
        assert opened == []
        # the gateway answers again: Try again fetches the list and the button turns on
        page.unroute("**/edhplay.json")
        err.locator("button", has_text="Try again").click()
        page.wait_for_function("() => !document.querySelector('.edhplay-dialog .acts a[aria-disabled]')")
        assert dialog.locator(".notice.error").count() == 0
        assert page.evaluate("() => document.activeElement.textContent") == GO
        _launch(page)
        copied = page.evaluate("() => navigator.clipboard.readText()").replace("\r\n", "\n")
        assert copied == to_edhplay(parse_deck(server.ark.decks[42])).text and opened

        # signed out meanwhile: the box says so
        page.context.clear_cookies()
        dialog = _open_dialog(page, ready=False)
        dialog.locator(".notice.error").wait_for()
        assert (
            dialog.locator(".notice.error span").inner_text()
            == "You're signed out. Reload the page to sign in."
        )
        assert errors == []
        browser.close()


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1280, "light")])
def test_the_box_says_what_to_set_on_edhplay(server: Server, width: int, scheme: str) -> None:
    """EDHPlay's import never sets a companion, and with no commander marked it sets none: the
    box says so for such decks only (Ben, Q54 and Q55 10-14); deck 42 has a commander and no
    companion, so it says neither."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    lines = ".edhplay-state .notice:not(.warn):not(.error)"
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        dialog = _open_dialog(page)
        page.wait_for_function("() => !document.querySelector('.edhplay-dialog .acts a[aria-busy]')")
        assert dialog.locator(lines).count() == 0
        page.keyboard.press("Escape")
        # the commander leaves the command zone and a flagged companion joins the sideboard
        for row in server.ark.decks[42]["cards"]:
            cats = [c for c in (row.get("categories") or []) if c != "Commander"]
            row["categories"] = cats or ["Uncategorized"]
        server.ark.add_side_row(42, "Yavimaya Elder", quantity=1, category="Sideboard")
        server.ark.decks[42]["cards"][-1]["companion"] = True
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        page.reload(wait_until="networkidle")
        dialog = _open_dialog(page)
        page.wait_for_function("() => !document.querySelector('.edhplay-dialog .acts a[aria-busy]')")
        assert dialog.locator(lines).all_inner_texts() == [
            "Choose your commander on EDHPlay.",
            "Set your companion on EDHPlay.",
        ]
        assert page.evaluate(CONTRAST) >= 4.5  # the notice lines are among the dialog's p
        assert _overflow(page) <= 0 and errors == []
        _shot(page, f"edhplay-set-on-edhplay-{width}-{scheme}")
        browser.close()


def _answer(status: int, text: str):
    return lambda route: route.fulfill(status=status, content_type="application/json", body=text)


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1280, "light")])
def test_the_box_shows_only_the_approved_messages(server: Server, width: int, scheme: str) -> None:
    """M8, Q67 (approved only): whatever the gateway answers, the box shows one of the three Q53
    messages, never the gateway's own text. Q69: signed out, retrying can't help, so there is no
    Try again; the two load failures keep it."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    busy = "Archidekt is busy right now; try again in a minute."
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        logged: list[str] = []
        page.on("console", lambda m: logged.append(m.text))
        answers = [
            (503, {"ok": False, "error": "unavailable", "message": busy}),
            (404, {"ok": False, "error": "not_found", "message": "That deck is private on Archidekt."}),
            (200, {"ok": False, "message": "<b>odd</b>"}),
            (500, "not json"),
        ]
        for status, body in answers:
            page.route(
                "**/edhplay.json", _answer(status, body if isinstance(body, str) else json.dumps(body))
            )
            dialog = _open_dialog(page, ready=False)
            err = dialog.locator(".notice.error")
            err.wait_for()
            assert err.locator("span").inner_text() == "The deck list couldn't be loaded.", status
            assert err.locator("button", has_text="Try again").count() == 1
            assert busy not in dialog.inner_text() and "private" not in dialog.inner_text()
            if status == 503:
                _shot(page, f"m8-edhplay-error-{width}-{scheme}")
            page.keyboard.press("Escape")
            page.unroute("**/edhplay.json")
        assert any(busy in line for line in logged)  # the reason goes to the console only
        # signed out: the message, and nothing to retry
        page.route(
            "**/edhplay.json",
            lambda route: route.fulfill(
                status=401, content_type="application/json", body='{"ok": false, "error": "unauthenticated"}'
            ),
        )
        dialog = _open_dialog(page, ready=False)
        err = dialog.locator(".notice.error")
        err.wait_for()
        assert err.locator("span").inner_text() == "You're signed out. Reload the page to sign in."
        assert (
            err.locator("button").count() == 0 and dialog.locator("button", has_text="Try again").count() == 0
        )
        focus = (
            "() => document.activeElement.getAttribute('aria-label') || document.activeElement.textContent"
        )
        cycle = []
        for _ in range(3):
            page.keyboard.press("Tab")
            cycle.append(page.evaluate(focus))
        assert cycle == ["Close", "How to import", GO]
        assert page.evaluate(CONTRAST) >= 4.5 and _overflow(page) <= 0
        _shot(page, f"q69-edhplay-signed-out-{width}-{scheme}")
        assert errors == []
        browser.close()


# the backdrop over the top bar, the phone tab bar and the Fold rail: what a tap in each one's
# middle lands on (the box's own backdrop when it covers them)
UNDER = """() => [".topbar", ".tabbar"].map(sel => {
  const e = document.querySelector(sel);
  if (!e) return [sel, "none"];
  const r = e.getBoundingClientRect();
  if (!r.width || !r.height) return [sel, "hidden"];
  const hit = document.elementFromPoint(r.left + r.width / 2, r.top + Math.min(r.height / 2, 30));
  return [sel, hit && hit.classList.contains("edhplay-dialog") ? "covered" : (hit && hit.className) || "?"];
})"""


@pytest.mark.parametrize(
    ("width", "height", "scheme", "touch"),
    [
        (344, 882, "dark", True),
        (390, 844, "light", True),
        (673, 841, "dark", True),
        (1280, 800, "light", False),
    ],
)
def test_the_box_covers_the_bars_and_takes_the_old_note_down(
    server: Server, width: int, height: int, scheme: str, touch: bool
) -> None:
    """L10: the backdrop dims the top bar, the phone's tab bar and the Fold's rail too, and a
    "Deck list copied" note left from an earlier launch is gone when the box opens again. L9: the
    box's x and How to import are finger-sized (44 px)."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    with sync_playwright() as p:
        browser, page, errors = _page(
            p,
            server,
            sid,
            width,
            color_scheme=scheme,
            permissions=["clipboard-read", "clipboard-write"],
            has_touch=touch,
            is_mobile=touch,
        )
        page.set_viewport_size({"width": width, "height": height})
        page.context.route(re.compile(r"https://edhplay\.com/.*"), lambda route: route.fulfill(status=200))
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        _open_dialog(page)
        _launch(page)
        assert page.locator(".edhplay-toast").is_visible()
        dialog = _open_dialog(page)
        assert page.locator(".edhplay-toast").count() == 0
        bars = dict(page.evaluate(UNDER))
        assert bars[".topbar"] == "covered", bars
        if width < 900:  # the tab bar (phone) or the rail (Fold, open)
            assert bars[".tabbar"] == "covered", bars
        for target in (dialog.locator("button[aria-label='Close']"), dialog.locator("summary")):
            box = target.bounding_box()
            assert box["height"] >= 44 and box["width"] >= 44, box
        dialog.locator("summary").click()  # folded after a launch: open it for the picture
        assert page.evaluate(CONTRAST) >= 4.5 and _overflow(page) <= 0
        _shot(page, f"l10-edhplay-reopened-{width}-{scheme}")
        assert errors == []
        browser.close()
