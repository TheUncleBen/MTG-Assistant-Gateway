"""The 0.7.46 polish follow-ups in headless Chromium: the Account page's rows at large text, the
review page's facts at large text on the Fold's cover screen, the Reports page's chip row, the
report page's Forge seats at large text, the Compare page's picker on a cold start, the scanner's
result card and status line, and the Watchlist form's tick box. Skipped without a Chromium
build."""

from __future__ import annotations

import json
import re
from contextlib import contextmanager
from pathlib import Path

import pytest

from .test_editor_browser import PNG, _link
from .test_history_page import store_report
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

pytest.importorskip("playwright")

LONGER = "Aesi, Tyrant of Gyre Strait: Lands Matter Big Ramp Value Pile (Budget Upgrade, v3)"


@pytest.fixture(scope="module")
def chromium():
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        yield browser
        browser.close()


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True, writes_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


@contextmanager
def _page(browser, server: Server, sid: str, width: int, *, touch: bool = True, **extra):
    ctx = browser.new_context(
        viewport={"width": width, "height": 800}, has_touch=touch, is_mobile=touch, **extra
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    try:
        yield page
        assert errors == []
    finally:
        ctx.close()


ZOOMS = {"root": ROOT_200, "text": TEXT_200}
SCROLLS = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


# -- Account: the Look rows at large text ----------------------------------------------------------

# Each row's label and value (Theme: System, Layout: Fit the screen) as laid out: each one's
# words fit its own box (a box squeezed to nothing lets its words run over the other's), the two
# boxes do not overlap, and both sit inside their row.
ROWS = """
() => [...document.querySelectorAll('.ygrp .yrow')].filter((r) => r.querySelector('.val')).map((r) => {
  const box = (r.querySelector('summary') || r).getBoundingClientRect();
  const lbl = r.querySelector('.lbl'), val = r.querySelector('.val');
  const l = lbl.getBoundingClientRect(), v = val.getBoundingClientRect();
  return { label: lbl.textContent, overlap: l.right > v.left + 0.5 && v.right > l.left + 0.5,
    fits: l.width + 0.5 >= lbl.scrollWidth && v.width + 0.5 >= val.scrollWidth,
    inside: l.left >= box.left - 0.5 && v.right <= box.right + 0.5 && l.right <= box.right + 0.5 };
})
"""


@pytest.mark.parametrize("zoom", ["root", "text"])
@pytest.mark.parametrize("width", [344, 390])
def test_account_rows_keep_label_and_value_apart_at_200_percent(chromium, server, width, zoom) -> None:
    """Theme and Layout: at twice the text size the value (“Fit the screen”) overlapped the
    label; the value now wraps on its side and the label keeps its words."""
    sid = _link(server)
    with _page(chromium, server, sid, width) as page:
        page.goto(f"{server.base}/account", wait_until="networkidle")
        page.evaluate(ZOOMS[zoom])
        page.wait_for_timeout(30)
        rows = page.evaluate(ROWS)
        assert [r["label"] for r in rows] == ["Theme", "Layout"]
        assert all(not r["overlap"] and r["fits"] and r["inside"] for r in rows), rows
        assert page.evaluate(SCROLLS) == 0


# -- the review page's facts at large text on the Fold's cover screen --------------------------------


def _pending(server: Server) -> str:
    import time

    from .test_history_page import seed_history

    seed_history(server.db, "user-1", now=int(time.time()))
    return next(p["id"] for p in server.db.list_proposals("user-1", limit=100) if p["state"] == "pending")


FACTS = """
() => { const dl = document.querySelector('.ui-meta');
  const right = dl.getBoundingClientRect().right;
  return [...dl.querySelectorAll('dd')].map((d) => d.getBoundingClientRect().right <= right + 0.5); }
"""


@pytest.mark.parametrize("zoom", ["root", "text"])
def test_the_review_facts_stay_inside_the_page_at_344_px_and_200_percent(chromium, server, zoom) -> None:
    """The facts list (Deck, Asked by, …): its value column shrank no further than the app's name
    on one line, so the whole page (and the tab bar with it) scrolled sideways by a few pixels."""
    sid = _link(server)
    pid = _pending(server)
    with _page(chromium, server, sid, 344) as page:
        page.goto(f"{server.base}/proposals/{pid}", wait_until="networkidle")
        page.evaluate(ZOOMS[zoom])
        page.wait_for_timeout(30)
        assert page.evaluate(SCROLLS) == 0
        assert all(page.evaluate(FACTS))


# -- Reports: the switch and the chip row -------------------------------------------------------------

# The Proposals / History / Reports switch and the chip row: each scrolls inside its own box when
# its parts are wider than the screen (scrollLeft moves), and the page never scrolls sideways.
STRIPS = """
() => ['.hubseg', '.ui-chips'].map((sel) => { const c = document.querySelector(sel);
  c.scrollLeft = 9999; return { sel, wider: c.scrollWidth > c.clientWidth + 1, scrolls: c.scrollLeft > 0,
    inside: c.getBoundingClientRect().right <= document.documentElement.clientWidth + 0.5 }; })
"""


@pytest.mark.parametrize("zoom", ["root", "text"])
@pytest.mark.parametrize("width", [344, 390, 600, 640, 840])
def test_reports_strips_scroll_inside_and_never_the_page(chromium, server, width, zoom) -> None:
    """At 600 and 640 px (the Fold's inner screen beside the rail) with the text at twice its size
    the switch's three parts pushed the page sideways; the chip row is the site's scroller."""
    sid = _link(server)
    store_report(server.db, "user-1", "r1", deck_name=LONGER)
    with _page(chromium, server, sid, width) as page:
        page.goto(f"{server.base}/reports", wait_until="networkidle")
        page.evaluate(ZOOMS[zoom])
        page.wait_for_timeout(30)
        assert page.evaluate(SCROLLS) == 0
        strips = page.evaluate(STRIPS)
        assert all(s["inside"] and (s["scrolls"] or not s["wider"]) for s in strips), strips


# -- the report page: Forge's seats at large text ------------------------------------------------------

# Every cell of the seats table sits inside the table, and each deck's name has room for more
# than a letter a line: its box is at least a quarter of the row.
SEATS = """
() => { const t = document.querySelector('.rv-seats'), tb = t.getBoundingClientRect();
  return [...t.querySelectorAll('.tr:not(.th)')].map((tr) => {
    const shown = (c) => getComputedStyle(c).display !== 'none';
    const cells = [...tr.children].filter(shown);
    const title = tr.querySelector('.title').getBoundingClientRect();
    const within = (c) => { const r = c.getBoundingClientRect();
      return r.left >= tb.left - 0.5 && r.right <= tb.right + 0.5; };
    return { inside: cells.every(within), room: title.width >= tr.getBoundingClientRect().width / 4 }; }); }
"""


@pytest.mark.parametrize("zoom", ["100", "root", "text"])
@pytest.mark.parametrize("width", [320, 344, 390, 673, 840])
def test_forge_seats_fit_the_table_at_large_text(chromium, server, width, zoom) -> None:
    """With the text at twice its size the win-rate column (sized in rem) left the deck's name a
    few pixels; the name's words ran over the rate. The rate now goes under the name on a table
    narrower than 20rem."""
    from .test_reports_browser import FORGE_DONE

    sid = _link(server)
    store_report(server.db, "user-1", "rep_forge", deck_name=LONGER)
    with server.db.tx() as c:
        c.execute(
            "UPDATE reports SET forge_json = ?, forge_state = 'done' WHERE id = 'rep_forge'",
            (json.dumps(FORGE_DONE),),
        )
    with _page(chromium, server, sid, width) as page:
        page.goto(f"{server.base}/history/reports/rep_forge", wait_until="networkidle")
        if zoom != "100":
            page.evaluate(ZOOMS[zoom])
        page.wait_for_timeout(30)
        assert page.evaluate(SCROLLS) == 0
        rows = page.evaluate(SEATS)
        assert len(rows) == 4 and all(r["inside"] and r["room"] for r in rows), rows


# -- the Compare page's picker on a cold start ----------------------------------------------------------


def _slow(server: Server, seconds: float) -> None:
    import asyncio

    import httpx

    ark = server.ark

    async def handle(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(seconds)
        return ark.handle(request)

    client = server.app.state.gateway.decks.client
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(handle))


@pytest.mark.parametrize("width", [344, 1280])
def test_compare_lists_my_decks_on_a_cold_start_and_wraps_a_long_name(chromium, server, width) -> None:
    """Before the deck list was read, the picker showed only snapshots. Now My decks is drawn as a
    loading box and filled from /api/decks/mine; a long deck name wraps inside its chip."""
    from mtg_gateway.archidekt_csv import parse_export, to_deck_json

    cards = parse_export((Path(__file__).parent / "fixtures" / "sample_deck.csv").read_text(encoding="utf-8"))
    server.ark.decks[44] = to_deck_json(cards[:20], deck_id=44, name=LONGER, owner="alice")
    server.ark.users["alice"]["decks"].append(44)
    sid = _link(server)
    _slow(server, 0.3)
    with _page(chromium, server, sid, width, touch=width < 1024) as page:
        held: list = []  # the chips' answer, held back until the loading box has been looked at
        page.route(re.compile(r".*/api/decks/mine\?shape=pick.*"), lambda route: held.append(route))
        page.goto(f"{server.base}/decks/42/compare", wait_until="load")
        box = page.locator(".cmp-mine [data-pick-src]")
        assert box.get_attribute("aria-busy") == "true" and "skeleton" in (box.get_attribute("class") or "")
        assert box.get_attribute("data-pick-src") == "/api/decks/mine?shape=pick&deck=42&with="
        assert len(held) == 1
        held[0].continue_()
        chip = page.locator(".cmp-mine a.cmp-pick[href='/decks/42/compare?with=44']")
        chip.wait_for(state="visible", timeout=10_000)
        assert chip.text_content() == LONGER
        assert page.locator(".cmp-mine [data-pick-src]").count() == 0
        assert page.locator(".cmp-mine h3", has_text="My decks").count() == 1
        page.wait_for_timeout(30)
        assert page.evaluate(SCROLLS) == 0
        chip.click()
        page.wait_for_url(re.compile(r"/decks/42/compare\?with=44$"))


# -- the scanner ------------------------------------------------------------------------------------------

SCAN_CARD = json.dumps(
    {
        "name": "Okiri, Belligerent Bannerkeeper of the Greatest Grand Army",
        "set": "cmr",
        "collector_number": "472",
        "scryfall_id": "okiri-cmr",
        "oracle_id": "okiri",
        "image_small": "https://cards.scryfall.io/small/okiri.png",
        "type_line": "Legendary Creature — Human Soldier",
        "set_name": "Commander Legends",
        "finishes": ["nonfoil", "foil"],
    }
)
RESULT = """
() => { const res = document.querySelector('.result');
  const w = (el) => el.getBoundingClientRect().width;
  return { card: w(res), name: w(res.querySelector('h3')), img: w(res.querySelector('img')) }; }
"""


def _scan(page, server: Server) -> None:
    page.goto(f"{server.base}/scan")
    page.wait_for_function("() => window.__scan && window.__scan.showTab")


@pytest.mark.parametrize("zoom", ["100", "root", "text"])
@pytest.mark.parametrize("width", [344, 390])
def test_the_scan_result_card_keeps_room_for_the_name_at_large_text(chromium, server, width, zoom) -> None:
    """The card's picture is sized in rem, so at twice the text size it took two thirds of the
    card and left the name a letter a line. It takes a third at most now."""
    sid = server.sign_in()
    with _page(chromium, server, sid, width) as page:
        _scan(page, server)
        if zoom != "100":
            page.evaluate(ZOOMS[zoom])
        page.evaluate(
            "() => { window.__scan.state.pending = {status: 'exact', quantity: 1, card: " + SCAN_CARD + "};"
            " window.__scan.showTab('camera'); }"
        )
        got = page.evaluate(RESULT)
        assert got["name"] >= 1.5 * got["img"] and got["name"] >= got["card"] * 0.4, got
        assert got["img"] <= got["card"] / 3 + 1, got
        assert page.evaluate(SCROLLS) == 0


# Text recognition that takes a moment to get ready, then reads "Sol Ring" from any picture.
SLOW_OCR = """
() => { window.Tesseract = { createWorker: async () => {
    await new Promise((r) => setTimeout(r, 400));
    const read = (c, o, out) => ({ data: { text: out && out.blocks ? '0472 R\\nCMR • EN' : 'Sol Ring',
      confidence: 92, blocks: [] } });
    return { setParameters: async () => {}, recognize: async (...a) => read(...a) }; } };
  window.__status = []; const s = document.querySelector('#cam-status');
  new MutationObserver(() => window.__status.push(s.textContent))
    .observe(s, { childList: true, characterData: true, subtree: true }); }
"""
SCAN_ONE = """
async () => { const cv = document.createElement('canvas'); cv.width = 400; cv.height = 560;
  cv.getContext('2d').fillStyle = '#fff'; cv.getContext('2d').fillRect(0, 0, 400, 560);
  await window.__scan.scanRegion(cv, {x: 20, y: 20, w: 300, h: 40}, {x: 12, y: 518, w: 176, h: 34}, null);
  return window.__status; }
"""


def test_ready_never_writes_over_a_scan_that_waited_for_text_recognition(chromium, server) -> None:
    """A scan started while the engine was still loading got "Ready. Hold the card flat…" in the
    middle of its own messages (Reading the card name… / Ready / Reading the set and number…)."""
    sid = server.sign_in()
    with _page(chromium, server, sid, 390) as page:
        _scan(page, server)
        page.evaluate("() => window.__scan.showTab('camera')")
        # the engine not loaded yet (the page may have started a real load: forgotten)
        page.evaluate("() => { window.__scan.state.ocr = null; window.__scan.state.ocrState = 'idle'; }")
        page.evaluate(SLOW_OCR)
        statuses = page.evaluate(SCAN_ONE)
        assert "Reading the set and number…" in statuses, statuses
        assert statuses[-1].startswith("Matched. Tap Add, or pick another card."), statuses
        assert not any(s.startswith("Ready") for s in statuses), statuses
        assert page.evaluate("() => window.__scan.state.ocrState") == "ready"
        # nothing waiting: the engine's own Ready line shows
        page.evaluate("() => { window.__scan.state.ocr = null; window.__scan.state.ocrState = null; }")
        page.evaluate("async () => { await window.__scan.ensureOcr(); }")
        assert page.locator("#cam-status").text_content() == "Ready. Hold the card flat and tap Scan."


PRICED = json.dumps(
    {
        "name": "Sol Ring",
        "scryfall_id": "sol-cmr",
        "set": "cmr",
        "collector_number": "472",
        "prices": {"usd": "1.50", "usd_foil": "4.00"},
    }
)
FOIL_ONLY_PLAIN = json.dumps(
    {
        "name": "Arcane Signet",
        "scryfall_id": "sig-cmr",
        "set": "cmr",
        "collector_number": "299",
        "prices": {"usd": "0.40"},
    }
)


def test_the_running_count_shows_the_scans_value_from_known_prices(chromium, server) -> None:
    """Quantity times Scryfall's price for the finish, over the lines that have one; lines with no
    card, or no price in their finish, are counted and said, never guessed."""
    sid = server.sign_in()
    with _page(chromium, server, sid, 390) as page:
        _scan(page, server)
        page.evaluate("() => { window.__scan.state.items = []; window.__scan.showTab('camera'); }")
        tally = page.locator("#scan-tally")
        page.evaluate("() => window.__scan.addItem({card: " + PRICED + "}, 2)")
        assert tally.text_content().strip() == "2 cards · $3.00"
        page.evaluate("() => window.__scan.addItem({card: " + PRICED + ", foil: true}, 1)")
        assert tally.text_content().strip() == "3 cards · $7.00"
        # a foil whose printing has only a non-foil price, and a line with no card: unpriced
        page.evaluate("() => window.__scan.addItem({card: " + FOIL_ONLY_PLAIN + ", foil: true}, 1)")
        page.evaluate("() => window.__scan.addItem({name: 'Cultivate', status: 'not_found'}, 2)")
        assert tally.text_content().strip() == "6 cards · $7.00 (3 without a price)"
        assert tally.get_attribute("aria-label") == (
            "6 cards, worth $7.00 (3 without a price), in this scan. Open the list"
        )
        page.evaluate("() => window.__scan.showTab('list')")
        assert page.locator("#summary").text_content() == (
            "6 cards, 4 distinct, 1 unresolved · $7.00 (3 without a price) · not saved yet"
        )
        # the resolve answer carries the printing's prices for this
        prices = page.evaluate(
            "async () => (await window.__scan.api('/scan/api/resolve', 'POST', "
            "{ cards: [{ name: 'Sol Ring' }] })).cards[0].card.prices"
        )
        assert prices == {"usd": "1.28", "eur": "1.13", "tix": "0.02"}


# -- the Watchlist's Watch form ----------------------------------------------------------------------

TICK = """
() => { const l = document.querySelector('.w-addrow label.check'), r = l.getBoundingClientRect();
  const i = l.querySelector('input').getBoundingClientRect();
  const holds = i.top >= r.top && i.bottom <= r.bottom && i.left >= r.left && i.right <= r.right;
  return { height: r.height, width: r.width, holds }; }
"""


@pytest.mark.parametrize("width", [390, 840, 1280])
def test_the_remember_tick_box_is_a_44_px_target_across_its_label(chromium, server, width) -> None:
    """“Use this finish and alert next time…”: the whole label is the target, 44 px tall, and a
    tap at its far end toggles the box."""
    sid = _link(server)
    with _page(chromium, server, sid, width, touch=width < 1024) as page:
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        got = page.evaluate(TICK)
        assert got["height"] >= 44 and got["width"] >= 200 and got["holds"], got
        box = page.locator(".w-addrow label.check input")
        assert box.is_checked()
        label = page.locator(".w-addrow label.check")
        label.scroll_into_view_if_needed()  # on a phone it starts under the tab bar
        b = label.bounding_box()
        page.mouse.click(b["x"] + b["width"] - 4, b["y"] + b["height"] / 2)
        assert not box.is_checked()
