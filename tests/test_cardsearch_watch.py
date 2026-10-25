"""Watch this card from Card search: a Watch button on each result's picture ("Watching"
with a check when the card is already watched; an icon in the list view), the page hint, the
card menu (right-click, press and hold, the Menu key or Shift+F10) with Open card, Watch this card,
Art variants…, Add to deck… and Open on Scryfall, the toast with Undo, and nothing of it when
watchlists are off. A Watch button on a card already watched keeps its alerts."""

from __future__ import annotations

import json
import re
from pathlib import Path

import httpx
import pytest

from mtg_gateway import cardsearch_view as cv

from .conftest import Stack, stack_with
from .test_cardsearch_browser import CATALOG, _chromium
from .test_deck_menu_browser import PNG
from .test_suggest_browser import Server
from .test_watchlist import Clock, _browser, _options

stack_options = stack_with(_options())

SOL = {
    "name": "Sol Ring",
    "set": "c21",
    "collector_number": "263",
    "type_line": "Artifact",
    "mana_cost": "{1}",
    "image_normal": "https://cards.scryfall.io/normal/front/a/b/sol.jpg",
}
ESPER = {**SOL, "name": "Esper Sentinel", "set": "mh2", "collector_number": "12"}


def test_tiles_have_a_watch_button_only_with_watchlists_on() -> None:
    off = cv.card_tile_html(SOL, can_add=True, reason="")
    assert "wbtn" not in off and "picwrap" not in off
    on = cv.card_tile_html(SOL, can_add=True, reason="", watched=frozenset())
    assert "<span class='picwrap'><button type='button' class='pic'" in on
    assert (
        "<button type='button' class='wbtn' data-watch='Sol Ring' aria-pressed='false' "
        "aria-label='Watch Sol Ring' title='Watch Sol Ring'>" in on
    )
    assert "<span>Watch</span></button></span><div class='meta'>" in on
    watched = cv.card_tile_html(
        SOL, can_add=True, reason="", watched=cv.watched_names([{"name": "sol  RING"}])
    )
    assert "class='wbtn on'" in watched and "aria-label='Sol Ring is on your watchlist'" in watched
    assert "title='On your watchlist'" in watched and "<span>Watching</span>" in watched


def test_a_printing_search_watches_the_shown_printing_and_the_list_has_an_icon() -> None:
    tile = cv.card_tile_html(ESPER, can_add=True, reason="", printing=True, watched=frozenset())
    assert "data-watch='Esper Sentinel' data-set-code='mh2' data-number='12'" in tile
    row = cv.card_row_html(SOL, can_add=True, reason="", watched=frozenset())
    assert "<span class='acts'><button type='button' class='wbtn icon' data-watch='Sol Ring'" in row
    assert row.index("wbtn icon") < row.index("data-add=")
    assert "wbtn" not in cv.card_row_html(SOL, can_add=True, reason="")
    items = cv.results_list_html([SOL], view="grid", can_add=True, reason="", printing=False, linked=True)
    assert "wbtn" not in items


async def test_watching_a_card_again_keeps_its_alerts(stack: Stack, monkeypatch) -> None:
    Clock(monkeypatch)
    b = await _browser(stack)
    try:
        csrf = await b.csrf("/watchlist")
        hdr = {"Content-Type": "application/json", "X-CSRF-Token": csrf}
        sub = stack.gw.db._one("SELECT sub FROM users", ())["sub"]
        w = await stack.gw.prices.watch_card(sub, "Sol Ring", alert_below=1.0)
        r = await b.http.post("/watchlist/api/watch", content=json.dumps({"name": "Sol Ring"}), headers=hdr)
        assert r.status_code == 200 and r.json()["watch"]["created"] is False
        row = stack.gw.prices.find(sub, w["watch_id"])[0]
        assert row["alert_below"] == 1.0  # the Watch button did not clear the alert
    finally:
        await b.aclose()


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, watchlists_enabled=True)
    s.sf.catalog = CATALOG + [f"Filler Card {i}" for i in range(50)]
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_the_page_has_the_hint_and_the_picker_and_marks_watched_cards(server: Server) -> None:
    sid = server.sign_in()
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    page = h.get("/cards?q=sol").text
    assert (
        "Watch a card with its <b>Watch</b> button, or right-click it (press and hold on a phone) for more."
        in page
    )
    assert "/static/printpick.js" in page and "class='wbtn'" in page
    # watched as the Watch button does it, on the server's own event loop (the price service's
    # clients belong to it: driving it from another loop here could hang)
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/watchlist").text)
    assert csrf
    r = h.post(
        "/watchlist/api/watch",
        content=json.dumps({"name": "Sol Ring"}),
        headers={"Content-Type": "application/json", "X-CSRF-Token": csrf.group(1)},
    )
    assert r.status_code == 200, r.text
    page = h.get("/cards?q=sol").text
    assert "aria-label='Sol Ring is on your watchlist'" in page
    api = h.get("/cards/api/search?q=sol").json()["html"]
    assert "aria-label='Sol Ring is on your watchlist'" in api and "aria-label='Watch Sol Talisman'" in api


def test_with_watchlists_off_there_is_no_watch(tmp_path: Path) -> None:
    s = Server(tmp_path, watchlists_enabled=False)
    s.sf.catalog = CATALOG
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    s.start()
    try:
        sid = s.sign_in()
        page = httpx.Client(base_url=s.base, cookies={"mtg_session": sid}).get("/cards?q=sol").text
        assert "Sol Ring" in page
        assert "class='wbtn" not in page and "id='cs-hint'" not in page and "printpick.js" not in page
    finally:
        s.stop()


@pytest.mark.parametrize("width", [344, 1280])
def test_watch_toast_undo_and_the_card_menu(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 860}, has_touch=width < 900)
        ctx.route("https://cards.scryfall.io/**", lambda r: r.fulfill(content_type="image/png", body=PNG))
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/cards?q=sol", wait_until="networkidle")
        btn = page.locator(".wbtn[data-watch='Sol Ring']").first
        box = btn.bounding_box()
        assert box and box["height"] >= (44 if width < 900 else 32)
        btn.click()
        toast = page.locator(".toast.cards-toast")
        toast.wait_for(timeout=5000)
        assert toast.locator(".msg").inner_text() == "Sol Ring added to your watchlist."
        assert toast.locator("a").get_attribute("href") == "/watchlist?q=Sol%20Ring"
        page.wait_for_function(
            "() => document.querySelector(\".wbtn[data-watch='Sol Ring']\").classList.contains('on')"
        )
        assert btn.get_attribute("aria-label") == "Sol Ring is on your watchlist"
        assert server.app.state.gateway.prices.watched_cards("user-1")
        toast.locator("button", has_text="Undo").click()
        page.wait_for_function(
            "() => !document.querySelector(\".wbtn[data-watch='Sol Ring']\").classList.contains('on')"
        )
        assert server.app.state.gateway.prices.watched_cards("user-1") == []
        # the card menu: right-click (Shift+F10 from the keyboard does the same)
        pic = page.locator(".cc .pic").nth(1)
        if width < 900:
            pic.dispatch_event("contextmenu")
        else:
            pic.click(button="right")
        menu = page.locator(".ctxmenu.cs-menu")
        menu.wait_for(timeout=3000)
        items = [t.strip() for t in menu.locator("[role=menuitem]").all_inner_texts()]
        assert items == [
            "Open card",
            "Add to deck…",
            "Add to collection",
            "Watch this card",
            "Art variants…",
            "Copy name",
            "Open on Scryfall",
        ]
        mb = menu.bounding_box()
        vw = page.evaluate("document.documentElement.clientWidth")
        assert mb and mb["x"] >= 0 and mb["x"] + mb["width"] <= vw + 1
        if width < 600:
            assert abs(mb["y"] + mb["height"] - 860) < 2  # a bottom sheet on a phone
        page.keyboard.press("Escape")
        assert menu.count() == 0
        pic.focus()
        page.keyboard.press("Shift+F10")
        menu.wait_for(timeout=3000)
        menu.locator("button", has_text="Art variants…").click()
        page.locator(".printpick.open").wait_for(timeout=3000)
        assert page.evaluate("document.documentElement.scrollWidth") <= vw
        # browse mode: a printing has "Watch this printing" (no "Use this printing" off a deck)
        page.locator(".printpick .pptile").first.click()
        watch = page.locator(".printpick .ppwatch:visible")
        watch.wait_for(timeout=3000)
        assert page.locator(".printpick .ppuse").count() == 0
        watch.click()
        page.wait_for_function(
            "() => document.querySelector('.printpick .ppwatch').textContent === 'Watching'"
        )
        rows = server.app.state.gateway.prices.watched_cards("user-1")
        assert len(rows) == 1 and rows[0]["set_code"] and rows[0]["collector_number"]
        assert errors == []
        browser.close()


def test_the_deck_page_card_menu_watches_a_card_and_opens_art_variants(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    from mtg_gateway.scan.scryfall import ScryfallClient

    from . import test_deck_menu_browser as dm
    from .fake_scryfall import FakeScryfall

    dm._chromium()
    s = dm.Server(tmp_path, watchlists_enabled=True)
    s.app.state.gateway.scan.scryfall = ScryfallClient(
        "https://scryfall.test", min_interval=0.0, http=FakeScryfall().client()
    )
    s.start()
    try:
        sid = s.sign_in_and_link()
        with sync_playwright() as p:
            browser, page, errors = dm._page(p, s, sid, 1280)
            page.goto(f"{s.base}/decks/42?view=grid", wait_until="networkidle")
            card = page.locator(".deckview .c[data-card='Sol Ring'][data-zone='main']").first
            card.click(button="right")
            menu = page.locator(".ctxmenu[role=menu]")
            menu.wait_for(timeout=3000)
            labels = [t.strip() for t in menu.locator("[role=menuitem]").all_inner_texts()]
            # the two new items right under Open card
            assert labels[:3] == ["Open card", "Watch this card", "Art variants…"], labels
            menu.locator("button", has_text="Watch this card").click()
            toast = page.locator(".deck-toast", has_text="watchlist")
            toast.wait_for(timeout=5000)
            assert toast.locator(".msg").inner_text() == "Sol Ring added to your watchlist."
            assert [w["name"] for w in s.app.state.gateway.prices.watched_cards("user-1")] == ["Sol Ring"]
            toast.locator("button", has_text="Undo").click()
            page.locator(".deck-toast", has_text="no longer").wait_for(timeout=5000)
            assert s.app.state.gateway.prices.watched_cards("user-1") == []
            card.click(button="right")
            menu.wait_for(timeout=3000)
            menu.locator("button", has_text="Art variants…").click()
            page.locator(".printpick.open").wait_for(timeout=3000)
            assert errors == []
            browser.close()
    finally:
        s.stop()
