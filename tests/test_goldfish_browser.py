"""0.7.23 in headless Chromium: the deck page's Goldfish (static/goldfish.js), played by hand in the
browser. A Commander deck starts with its commander in the command zone and a free first
mulligan; a 60-card deck has no command zone and every mulligan bottoms a card. Mouse, touch and
keyboard; phone and desktop widths, light and dark; nothing wider than the window; text contrast
at least 4.5:1. Nothing is fetched beyond the page and the card images (faked here). Skipped
without a Chromium build."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .test_deck_menu_browser import Server, _chromium, _overflow, _page
from .test_deck_views_browser import _shot

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _main_count(server: Server) -> int:
    deck = server.ark.decks[42]
    side = {
        c["name"] for c in deck["categories"] if not c.get("includedInDeck", True) and not c.get("isPremier")
    }
    return sum(r["quantity"] for r in deck["cards"] if not (r["categories"] and r["categories"][0] in side))


def _count(page, zone: str) -> int:
    return page.locator(f"#gf-{zone} .gf-card").count()


def _library(page) -> int:
    return int(re.search(r"Library (\d+)", page.locator("#gf-status").inner_text()).group(1))


# Every visible text node in the game panels against the background it is drawn on.
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
  var worst = 99, what = "";
  var sel = ".gf-head p, .gf-head a, .gf-zone h2, .gf-zone h2 span, .gf-empty, .gf-card .tag, "
    + ".gf-controls button";
  document.querySelectorAll(sel)
    .forEach(function (e) {
      if (!e.offsetParent || !e.textContent.trim()) return;
      var a = lum(rgb(getComputedStyle(e).color)), b = lum(bg(e));
      var r = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      if (r < worst) { worst = r; what = e.className + ":" + e.textContent.trim().slice(0, 30); }
    });
  return [worst, what];
}"""


def _contrast_ok(page) -> None:
    worst, what = page.evaluate(CONTRAST)
    assert worst >= 4.5, (worst, what)


def _open_card(page, zone: str, index: int = 0) -> str:
    card = page.locator(f"#gf-{zone} .gf-card").nth(index)
    cid = card.get_attribute("data-id")
    card.click()
    page.wait_for_selector(".cardview.open", timeout=3000)
    return cid


def _act(page, label: str) -> None:
    page.locator(".cardview.open .acts button", has_text=label).first.click()
    page.wait_for_function("() => !document.querySelector('.cardview.open')", timeout=3000)
    page.wait_for_timeout(80)


@pytest.mark.parametrize(("width", "scheme"), [(390, "dark"), (1366, "light")])
def test_commander_goldfish_by_mouse(server: Server, width: int, scheme: str) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    total = _main_count(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, color_scheme=scheme)
        requests: list[str] = []
        page.on("request", lambda r: requests.append(r.url))
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        page.locator(".banner a", has_text="Goldfish").click()
        page.wait_for_url(re.compile(r"/decks/42/goldfish$"))
        page.wait_for_load_state("networkidle")
        requests.clear()
        # the commander waits in the command zone; seven in hand; the rest is the library
        assert _count(page, "command") == 1
        assert (
            page.locator("#gf-command .gf-card").get_attribute("data-card") == "Aesi, Tyrant of Gyre Strait"
        )
        assert _count(page, "hand") == 7 and _library(page) == total - 8
        assert "first mulligan is free" in page.locator("#gf-hint").inner_text()
        _contrast_ok(page)
        assert _overflow(page) == 0
        # a free mulligan, then a second: keeping bottoms one card (London)
        page.locator("[data-act=mulligan]").click()
        assert _count(page, "hand") == 7 and _library(page) == total - 8
        page.locator("[data-act=mulligan]").click()
        assert "puts 1 card on the bottom" in page.locator("#gf-hint").inner_text()
        page.locator("[data-act=keep]").click()
        bottom = page.locator("[data-act=bottom]")
        assert bottom.is_visible() and bottom.is_disabled()
        first = page.locator("#gf-hand .gf-card").first
        picked = first.get_attribute("data-id")
        first.click()
        assert page.locator(f"#gf-hand .gf-card[data-id='{picked}']").get_attribute("aria-pressed") == "true"
        _contrast_ok(page)  # the "Bottom" tag on the chosen card
        assert bottom.is_enabled()
        bottom.click()
        # on the play: turn 1 has no draw
        assert "Turn 1" in page.locator("#gf-status").inner_text()
        # only the turn buttons show once the hand is kept
        shown = page.eval_on_selector_all(
            ".gf-controls button", "l => l.filter(b => b.offsetParent).map(b => b.dataset.act)"
        )
        assert shown == ["draw", "next", "untap", "reset"], shown
        assert _count(page, "hand") == 6 and _library(page) == total - 7
        # play a card, tap it, next turn untaps it and draws one
        cid = _open_card(page, "hand")
        _act(page, "Play to the battlefield")
        assert _count(page, "battlefield") == 1 and _count(page, "hand") == 5
        assert page.evaluate("() => document.activeElement.dataset.id") == cid  # focus followed the card
        _open_card(page, "battlefield")
        _act(page, "Tap")
        assert (
            page.locator(f"#gf-battlefield .gf-card[data-id='{cid}']").get_attribute("class").count("tapped")
            == 1
        )
        page.locator("[data-act=next]").click()
        assert "Turn 2" in page.locator("#gf-status").inner_text()
        assert _count(page, "hand") == 6 and _library(page) == total - 8
        assert page.locator("#gf-battlefield .gf-card.tapped").count() == 0
        # the commander is cast from the command zone and can go to exile and back
        _open_card(page, "command")
        _act(page, "Cast to the battlefield")
        assert _count(page, "command") == 0 and _count(page, "battlefield") == 2
        cmd = page.locator("#gf-battlefield .gf-card[data-card='Aesi, Tyrant of Gyre Strait']")
        cmd.click()
        page.wait_for_selector(".cardview.open", timeout=3000)
        _act(page, "Exile")
        assert _count(page, "exile") == 1
        _open_card(page, "exile")
        _act(page, "Return to the command zone")
        assert _count(page, "command") == 1 and _count(page, "exile") == 0
        _open_card(page, "hand")
        _act(page, "Put in the graveyard")
        assert _count(page, "graveyard") == 1
        page.locator("[data-act=draw]").click()
        assert _count(page, "hand") == 6
        assert _overflow(page) == 0
        _contrast_ok(page)
        _shot(page, f"goldfish-commander-{width}-{scheme}")
        # New game puts everything back
        page.locator("[data-act=reset]").click()
        assert _count(page, "hand") == 7 and _count(page, "battlefield") == 0 and _count(page, "command") == 1
        assert _library(page) == total - 8 and "Opening hand" in page.locator("#gf-status").inner_text()
        # nothing left the browser beyond the card images (faked) while playing
        assert [u for u in requests if "cards.scryfall.io" not in u] == [], requests
        assert errors == []
        browser.close()


def test_sixty_card_goldfish_by_keyboard_and_touch(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    deck = server.ark.decks[42]
    deck["deckFormat"] = 2  # Modern
    deck["categories"] = [c for c in deck["categories"] if not c.get("isPremier")]
    for row in deck["cards"]:
        if row["categories"] == ["Commander"]:
            row["categories"] = None
    sid = server.sign_in_and_link()
    total = _main_count(server)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 412, has_touch=True, is_mobile=True)
        page.goto(f"{server.base}/decks/42/goldfish", wait_until="networkidle")
        assert page.locator("#gf-command").count() == 0
        assert _count(page, "hand") == 7 and _library(page) == total - 7
        assert "free" not in page.locator("#gf-hint").inner_text()
        # keyboard: one mulligan, keep, choose the bottom card with Space, confirm with Enter
        page.locator("[data-act=mulligan]").focus()
        page.keyboard.press("Enter")
        assert "puts 1 card on the bottom" in page.locator("#gf-hint").inner_text()
        page.locator("[data-act=keep]").focus()
        page.keyboard.press("Enter")
        page.locator("#gf-hand .gf-card").first.focus()
        page.keyboard.press("Space")
        assert page.locator("#gf-hand .gf-card[aria-pressed=true]").count() == 1
        page.locator("[data-act=bottom]").focus()
        page.keyboard.press("Enter")
        assert _count(page, "hand") == 6 and "Turn 1" in page.locator("#gf-status").inner_text()
        # keyboard into the viewer: Enter opens it, Tab reaches the action, Escape closes
        page.locator("#gf-hand .gf-card").first.focus()
        page.keyboard.press("Enter")
        page.wait_for_selector(".cardview.open", timeout=3000)
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('.cardview.open')", timeout=3000)
        assert _count(page, "hand") == 6
        # touch: a tap opens the card, a tap on the action plays it
        page.locator("#gf-hand .gf-card").first.tap()
        page.wait_for_selector(".cardview.open", timeout=3000)
        page.locator(".cardview.open .acts button", has_text="Play to the battlefield").tap()
        page.wait_for_function("() => !document.querySelector('.cardview.open')", timeout=3000)
        page.wait_for_timeout(80)
        assert _count(page, "battlefield") == 1
        page.locator("[data-act=next]").tap()
        assert _count(page, "hand") == 6 and "Turn 2" in page.locator("#gf-status").inner_text()
        assert _overflow(page) == 0
        _contrast_ok(page)
        _shot(page, "goldfish-60-412")
        assert errors == []
        browser.close()
