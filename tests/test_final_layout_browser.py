"""Three layout flaws the final 0.7.46 fact-check saw in its screenshots, in headless Chromium
on the phone in the Android app (320, 375 and 412 px, a touch screen, the app's user agent), the
open Fold (840 px) and the desktop (1280 px), light and dark, and at 200 % text:

- the deck page's category delete box: a long category name with no spaces wraps inside the
  box (its sentence ran off the right edge);
- the toolbar's Quick add field shows the whole of its placeholder on a phone (it read
  "Quick add (nar");
- the Reports page's tiles are at most two a row on a phone, with 44 px quick starts, as the
  Proposals and History tiles are (three cramped ones shared a 412 px row).

Set MTG_FINAL_SHOTS=<dir> to save screenshots. Skipped without a Chromium build."""

from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

from .test_deck_categories import _give_categories
from .test_deck_menu_browser import _EXE, PNG, Server, _chromium
from .test_history_page import store_report
from .test_overflow_sweep_browser import MEASURE
from .test_social_browser import Server as ReportsServer
from .test_text_zoom_browser import TEXT_200

pytest.importorskip("playwright")

SHOTS = os.environ.get("MTG_FINAL_SHOTS")
APP_UA = "Mozilla/5.0 (Linux; Android 16; wv) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.7.46"
BOX = ".mtgbox-scrim .mtgbox"
LONG = "Rampandlandfallsynergiesunbrokencategorynamenospacesatall"
# (width, theme, touch) for the delete box and the Quick add field
SURFACES = [
    (320, "light", True),
    (375, "dark", True),
    (412, "light", True),
    (840, "dark", True),
    (1280, "light", False),
]

# the box's sentence and its drop-down, against the box: nothing past its right edge, and the box
# body never scrolls sideways
BOX_FIT = """() => {
  const box = document.querySelector('.mtgbox-scrim .mtgbox');
  const body = box.querySelector('.body');
  const right = box.getBoundingClientRect().right;
  const past = [...body.querySelectorAll('p, select, label')]
    .filter(e => e.getBoundingClientRect().right > right + 0.5)
    .map(e => e.tagName + ' ' + e.textContent.trim().slice(0, 30));
  return { past, sideways: body.scrollWidth - body.clientWidth, window: window.innerWidth,
           boxRight: right, docScroll: document.documentElement.scrollWidth - window.innerWidth };
}"""

# the Quick add field's placeholder, measured in the field's own font, against the room the field
# gives its text (Chromium draws the placeholder inside the content box)
QUICK_FIT = """() => {
  const input = document.querySelector('#quick');
  const cs = getComputedStyle(input);
  const c = document.createElement('canvas').getContext('2d');
  c.font = `${cs.fontStyle} ${cs.fontWeight} ${cs.fontSize} ${cs.fontFamily}`;
  const need = c.measureText(input.placeholder).width;
  const room = input.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
  return { placeholder: input.placeholder, need, room, width: input.getBoundingClientRect().width };
}"""

# the widgets' columns: how many share a row, each one's share of the row, their quick starts
WIDGETS = """() => {
  const sec = document.querySelector('.ui-widgets');
  const w = sec.getBoundingClientRect().width;
  const tiles = [...sec.querySelectorAll('.wdg')].map(e => e.getBoundingClientRect());
  const lefts = [...new Set(tiles.map(r => Math.round(r.left)))];
  return {
    columns: lefts.length,
    share: Math.min(...tiles.map(r => r.width / w)),
    targets: [...sec.querySelectorAll('.go .btn, .go button')].map(e => e.getBoundingClientRect().height),
    tiles: tiles.length,
    sideways: document.documentElement.scrollWidth - window.innerWidth,
  };
}"""


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    _give_categories(s.ark)
    s.ark.decks[42]["categories"].append(
        {"name": LONG, "isPremier": False, "includedInDeck": True, "includedInPrice": True}
    )
    for row in s.ark.decks[42]["cards"]:
        if row["card"]["oracleCard"]["name"] in ("Arcane Denial", "Acidic Slime", "Beast Within"):
            row["categories"] = ["Removal"]
    s.ark.decks[42]["categories"].append(
        {"name": "Removal", "isPremier": False, "includedInDeck": True, "includedInPrice": True}
    )
    s.start()
    try:
        yield s
    finally:
        s.stop()


@pytest.fixture
def reports(tmp_path: Path):
    s = ReportsServer(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _context(p, base: str, sid: str, width: int, theme: str, touch: bool):
    browser = p.chromium.launch(executable_path=_EXE["path"], args=["--no-sandbox"])
    kw: dict = {"has_touch": True, "is_mobile": True, "user_agent": APP_UA} if touch else {}
    ctx = browser.new_context(viewport={"width": width, "height": 812 if width < 500 else 900}, **kw)
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": sid, "url": base},
            {"name": "mtg_theme", "value": theme, "url": base},
        ]
    )
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


def _deck_page(page, server: Server) -> None:
    # "load", then the stacks themselves: a view load after the page's kept read has aged starts
    # the /decks/{id}/check long-poll, which "networkidle" would wait out (audit, section 6)
    page.goto(f"{server.base}/decks/42?view=text", wait_until="load")
    page.locator(".stackhead .kebab").first.wait_for(timeout=5000)


def _shot(page, name: str) -> None:
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"))


def _open_delete_box(page, name: str):
    k = page.locator(f".stack[data-group='{name}'] .stackhead .kebab")
    k.scroll_into_view_if_needed()
    k.click()
    menu = page.locator(".ctxmenu[role=menu]")
    menu.wait_for(timeout=3000)
    menu.locator("[role=menuitem]", has_text="Delete…").click()
    box = page.locator(BOX)
    box.wait_for(timeout=3000)
    page.wait_for_timeout(200)
    return box


@pytest.mark.parametrize(("width", "theme", "touch"), SURFACES)
@pytest.mark.parametrize("big", [False, True], ids=["text100", "text200"])
def test_the_delete_box_wraps_a_long_unbroken_category_name(
    server: Server, width: int, theme: str, touch: bool, big: bool
) -> None:
    """Deleting Removal with its cards going to the long-named category: the sentence that names
    it wraps inside the box instead of running off its right edge, at every width and at 200% text."""
    from playwright.sync_api import sync_playwright

    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _context(p, server.base, sid, width, theme, touch)
        _deck_page(page, server)
        if big:
            page.evaluate(TEXT_200)
        box = _open_delete_box(page, "Removal")
        select = box.locator("select#catmove")
        select.select_option(LONG)
        page.wait_for_timeout(100)
        sentence = box.locator(".body p").first.inner_text()
        assert sentence == f"Its 3 cards stay in the deck, in “{LONG}”.", sentence
        fit = page.evaluate(BOX_FIT)
        _shot(page, f"delete-box-{width}-{theme}{'-text200' if big else ''}")
        assert fit["past"] == [], fit
        assert fit["sideways"] == 0 and fit["docScroll"] <= 0, fit
        assert fit["boxRight"] <= fit["window"], fit
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize(("width", "theme", "touch"), SURFACES)
def test_the_quick_add_field_shows_its_whole_placeholder(
    server: Server, width: int, theme: str, touch: bool
) -> None:
    """The Quick add field's placeholder fits inside the field on every surface, the toolbar's
    row with it, and nothing scrolls sideways."""
    from playwright.sync_api import sync_playwright

    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _context(p, server.base, sid, width, theme, touch)
        _deck_page(page, server)
        fit = page.evaluate(QUICK_FIT)
        _shot(page, f"quick-add-{width}-{theme}")
        assert fit["need"] <= fit["room"], fit
        assert page.evaluate(MEASURE) == []
        # the field is a finger-sized target on touch screens, and the + beside it too
        if touch:
            for sel in ("#quick", ".toolbar .quickrow button:not(.addpkg)"):
                assert page.locator(sel).first.bounding_box()["height"] >= 44, sel
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize("width", [320, 375, 412])
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_the_reports_tiles_are_two_a_row_on_a_phone(reports: ReportsServer, width: int, theme: str) -> None:
    """The Reports page's four tiles on a phone: at most two share a row, each at least two fifths
    of the row (never three cramped ones), every quick start 44 px tall, nothing sideways; as the
    Proposals tiles are laid out."""
    from playwright.sync_api import sync_playwright

    sid = reports.sign_in_and_link()
    now = int(time.time())
    store_report(reports.db, "user-1", "rep_done", taken_at=now - 3 * 86400)
    store_report(reports.db, "user-1", "rep_new", deck_id="43", deck_name="Aesi, Tyrant", taken_at=now - 500)
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _context(p, reports.base, sid, width, theme, True)
        page.goto(f"{reports.base}/reports", wait_until="networkidle")
        page.locator("label.glance-lbl").click()  # the tiles open on a tap on a phone
        got = page.evaluate(WIDGETS)
        _shot(page, f"reports-tiles-{width}-{theme}")
        assert got["tiles"] == 4, got
        assert got["columns"] <= 2, got
        assert got["share"] >= 0.4, got
        assert got["targets"] and min(got["targets"]) >= 44, got
        assert got["sideways"] <= 0, got
        page.goto(f"{reports.base}/proposals", wait_until="networkidle")
        page.locator("label.glance-lbl").click()
        same = page.evaluate(WIDGETS)
        assert same["columns"] <= 2 and same["share"] >= 0.4, same
        assert errors == [], errors
        browser.close()
