"""The deck page's notes after an instant save (independent review of 0.7.46), in headless
Chromium at 390, 840 and 1280 px, light and dark, and at 200 % text:

- a Quick add that breaks the format's rules (a second copy in a Commander deck) says so in its
  note, with Undo, as the same change made as a proposal is marked high risk;
- the deck's cost stays "–" after a save while no card has a price (never "$0.00");
- a name that is no card is refused without a Try again that can only fail the same way, and
  offers a card search instead;
- the Pin card note says "Open Wants" as a button, and two notes never cover each other."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from .test_deck_menu_browser import Server, _chromium
from .test_deck_views_browser import _error, _menu_page, _open_menu

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_TOAST_SHOTS")
SIZES = [
    (390, 844, "dark", False),
    (840, 900, "light", False),
    (1280, 800, "light", False),
    (390, 844, "light", True),
]


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _commander(server: Server) -> None:
    """Deck 42 as a legal 100-card Commander deck (as test_deck_edit_api._legal_commander_deck)."""
    deck = server.ark.decks[42]
    deck["deckFormat"] = 3
    for row in deck["cards"]:
        name = row["card"]["oracleCard"]["name"]
        if name == "Aesi, Tyrant of Gyre Strait":
            row["card"]["oracleCard"]["superTypes"] = ["Legendary"]
        elif name in ("Forest", "Island"):
            row["card"]["oracleCard"]["superTypes"] = ["Basic"]


OVERLAP = """() => {
  const ts = Array.from(document.querySelectorAll('.toast')).filter(t => t.getClientRects().length);
  const r = ts.map(t => t.getBoundingClientRect());
  for (let i = 0; i < r.length; i++) for (let j = i + 1; j < r.length; j++) {
    const a = r[i], b = r[j];
    if (a.left < b.right && b.left < a.right && a.top < b.bottom && b.top < a.bottom) return ts.length;
  }
  return 0;
}"""

# the link colour a plain <a> gets, compared with an element's own
IS_BLUE = """(el) => {
  const probe = document.createElement('a'); probe.href = '#'; document.body.appendChild(probe);
  const link = getComputedStyle(probe).color; probe.remove();
  return getComputedStyle(el).color === link;
}"""


@pytest.mark.parametrize(("width", "height", "scheme", "big"), SIZES)
def test_deck_page_notes_after_instant_saves(
    server: Server, width: int, height: int, scheme: str, big: bool
) -> None:
    from playwright.sync_api import sync_playwright

    _commander(server)
    server.app.state.gateway.scan.names.set_names(["Cultivate"])
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _menu_page(p, server, width, height, scheme)
        if big:
            page.add_style_tag(content="html{font-size:200% !important}")
        cost = page.locator(".banner span", has_text="Est cost:")
        assert "–" in cost.inner_text() and "$0.00" not in cost.inner_text()
        box = page.locator("#quick")
        # a second copy in a singleton format: saved, and the note says which rule it breaks
        box.fill("Cultivate")
        box.press("Enter")
        note = page.locator(".deck-toast .msg", has_text="Added Cultivate")
        note.wait_for(timeout=8000)
        text = note.inner_text()
        assert "breaks its format's rules" in text and "more than one copy" in text, text
        assert "warn" in (page.locator(".deck-toast").get_attribute("class") or "")
        assert page.locator(".deck-toast button", has_text="Undo").count() == 1
        # no card has a price: the cost stays a dash after the save, in the banner and the tile
        assert "$0.00" not in cost.inner_text() and "–" in cost.inner_text()
        assert "$0.00" not in page.locator(".stats .tiles").inner_text()
        # no such card: refused, no Try again (it would fail the same way), a search instead
        box.fill("Zzyzx")
        box.press("Enter")
        assert "Zzyzx" in _error(page)
        assert page.locator(".deck-toast.error button", has_text="Try again").count() == 0
        search = page.locator(".deck-toast.error a.btn", has_text="Search cards")
        assert search.get_attribute("href") == "/cards?q=Zzyzx"
        assert not search.evaluate(IS_BLUE)
        # Pin card while the red note is up: the two notes never cover each other
        menu = _open_menu(page, page.locator(".deckview .row[data-card='Cultivate']").first)
        menu.locator("[role=menuitem]", has_text="Pin card").click()
        pinned = page.locator(".cards-toast")
        pinned.wait_for(timeout=8000)
        page.locator(".cards-toast .msg", has_text="Pinned").wait_for(timeout=8000)
        assert page.locator(".deck-toast.error").is_visible()
        assert page.evaluate(OVERLAP) == 0
        wants = pinned.locator("a", has_text="Open Wants")
        assert wants.get_attribute("href") == "/collection/wants"
        assert "btn" in (wants.get_attribute("class") or "") and not wants.evaluate(IS_BLUE)
        wb = wants.bounding_box()
        assert wb is not None and wb["height"] >= (44 if width < 900 else 32)
        top = pinned.bounding_box()
        assert top is not None and top["y"] >= 0
        assert (
            page.evaluate("document.documentElement.scrollWidth - document.documentElement.clientWidth") <= 0
        )
        if SHOTS:
            page.screenshot(
                path=str(Path(SHOTS) / f"deck-toasts-{width}-{scheme}{'-z200' if big else ''}.png")
            )
        assert errors == []
        browser.close()
