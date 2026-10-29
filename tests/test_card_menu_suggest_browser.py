"""The card menu and Watch on suggestions, in headless Chromium: the
deck editor's add box has the same Watch button (icon, "On your watchlist" for cards already watched) and
card menu as Card search, without "Add to deck…" (its rows already add to this deck); the top
bar's "Search cards and decks" suggestions get only the menu, no Watch button. With watchlists
off neither has anything of the watch. Skipped without Chromium."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from .test_editor_browser import PNG, _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


def _server(tmp_path: Path, **over: object) -> Server:
    s = Server(tmp_path, **over)
    # the fixture cards' names are suggestable, with their summaries behind /scan/api/peek
    s.sf.catalog = s.sf.names_catalog()
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    s.start()
    return s


def _exe() -> str | None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _context(browser, server: Server, sid: str, width: int):
    touch = width < 900
    ctx = browser.new_context(
        viewport={"width": width, "height": 860}, has_touch=touch, is_mobile=touch, reduced_motion="reduce"
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    return ctx


def _menu_items(page) -> list[str]:
    menu = page.locator(".ctxmenu.cs-menu")
    menu.wait_for(timeout=3000)
    return [t.strip() for t in menu.locator("[role=menuitem]").all_inner_texts()]


def _open_menu(page, row, width: int) -> None:
    if width < 900:  # press and hold: the menu opens after about half a second
        box = row.bounding_box()
        assert box
        x, y = box["x"] + 20, box["y"] + box["height"] / 2
        touch = {"pointerType": "touch", "clientX": x, "clientY": y, "isPrimary": True}
        row.dispatch_event("pointerdown", touch)
        page.locator(".ctxmenu.cs-menu").wait_for(timeout=3000)  # the list closed as the menu took the focus
    else:
        row.click(button="right")


@pytest.mark.parametrize("width", [390, 1280])
def test_the_editor_add_box_has_watch_buttons_and_the_card_menu(tmp_path: Path, width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path, watchlists_enabled=True)
    try:
        sid = _link(server)
        prices = server.app.state.gateway.prices
        asyncio.run(prices.watch_card("user-1", "Sol Ring"))
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            ctx = _context(browser, server, sid, width)
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
            box = page.locator("#addname")
            box.click()
            box.press_sequentially("sol", delay=30)
            page.locator("[role=option] .rich .ty").first.wait_for(timeout=3000)
            sol = page.locator("[role=option][data-name='Sol Ring']")
            talisman = page.locator("[role=option][data-name='Sol Talisman']")
            # the card already watched says so; the others say Watch (icon only, named for readers)
            sol_btn = sol.locator(".wbtn")
            assert sol_btn.get_attribute("aria-label") == "Sol Ring is on your watchlist"
            assert "on" in (sol_btn.get_attribute("class") or "")
            btn = talisman.locator(".wbtn.icon")
            assert btn.get_attribute("aria-label") == "Watch Sol Talisman"
            assert btn.get_attribute("title") == "Watch Sol Talisman"
            b = btn.bounding_box()
            r = talisman.bounding_box()
            assert b and r and b["x"] + b["width"] <= r["x"] + r["width"] + 0.5
            if width < 900:
                assert b["height"] >= 44 - 0.5 and b["width"] >= 44 - 0.5
            assert page.evaluate("document.documentElement.scrollWidth") <= width
            # Watch: the toast, the button turns to On your watchlist, and nothing is added to the deck
            btn.click()
            toast = page.locator(".toast.cards-toast")
            toast.wait_for(timeout=5000)
            assert toast.locator(".msg").inner_text() == "Sol Talisman added to your watchlist."
            assert toast.locator("a").get_attribute("href") == "/watchlist?q=Sol%20Talisman"
            label = talisman.locator(".wbtn").get_attribute("aria-label")
            assert label == "Sol Talisman is on your watchlist"
            assert talisman.locator(".wbtn").text_content().strip() == "On your watchlist"
            assert page.locator(".pendingbox .n").inner_text() == "0" and box.input_value() == "sol"
            assert sorted(w["name"] for w in prices.watched_cards("user-1")) == ["Sol Ring", "Sol Talisman"]
            toast.locator("button", has_text="Undo").click()
            page.locator(".toast.cards-toast", has_text="no longer").wait_for(timeout=5000)
            assert [w["name"] for w in prices.watched_cards("user-1")] == ["Sol Ring"]
            # the card menu: as Card search's, without Add to deck… (these rows add to this deck)
            page.locator(".toast.cards-toast .close").click()
            box.click()
            page.locator("[role=option][data-name='Sol Talisman']").wait_for(timeout=3000)
            _open_menu(page, page.locator("[role=option][data-name='Sol Talisman']"), width)
            assert _menu_items(page) == ["Open card", "Watch this card", "Printings…", "Open on Scryfall"]
            mb = page.locator(".ctxmenu.cs-menu").bounding_box()
            assert mb and mb["x"] >= 0 and mb["x"] + mb["width"] <= width + 1
            page.keyboard.press("Escape")
            assert page.locator(".ctxmenu").count() == 0
            assert page.locator(".pendingbox .n").inner_text() == "0"
            # from the keyboard: the highlighted row, Shift+F10 in the box
            box.click()
            page.locator("[role=option][data-name='Sol Ring']").wait_for(timeout=3000)
            box.press("ArrowDown")
            box.press("Shift+F10")
            items = _menu_items(page)
            assert items == ["Open card", "On your watchlist", "Printings…", "Open on Scryfall"]
            # Printings…: the picker's browse mode, with this deck's own pick
            page.locator(".ctxmenu.cs-menu button", has_text="Printings…").click()
            page.locator(".printpick.open").wait_for(timeout=3000)
            page.locator(".printpick .pptile").first.click()
            page.locator(".printpick .ppwatch:visible").wait_for(timeout=3000)
            use = page.locator(".printpick .ppuse:visible")
            assert use.inner_text() == "Add this printing"
            use.click()
            page.locator(".addstatus", has_text="Added 1 × Sol Ring").wait_for(timeout=3000)
            assert errors == []
            browser.close()
    finally:
        server.stop()


def test_the_editor_has_no_watch_with_watchlists_off(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path, watchlists_enabled=False)
    try:
        sid = _link(server)
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            page = _context(browser, server, sid, 1280).new_page()
            page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
            assert page.locator("#addname[data-suggest-watch]").count() == 0
            box = page.locator("#addname")
            box.click()
            box.press_sequentially("sol", delay=30)
            page.locator("[role=option] .rich .ty").first.wait_for(timeout=3000)
            assert page.locator(".suggest-list .wbtn").count() == 0
            page.locator("[role=option][data-name='Sol Ring']").click(button="right")
            assert _menu_items(page) == ["Open card", "Open on Scryfall"]
            browser.close()
    finally:
        server.stop()


@pytest.mark.parametrize("width", [390, 1280])
def test_the_top_bar_suggestions_get_the_card_menu_only(tmp_path: Path, width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path, watchlists_enabled=True)
    try:
        sid = server.sign_in()
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            page = _context(browser, server, sid, width).new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"{server.base}/account/details", wait_until="networkidle")
            if width < 600:
                page.locator(".topbar .searchbtn").click()
            box = page.locator("#site-q")
            box.click()
            box.press_sequentially("sol", delay=30)
            row = page.locator("#site-q-list [role=option][data-name='Sol Ring']")
            row.wait_for(timeout=3000)
            page.locator("#site-q-list [role=option] .rich").first.wait_for(timeout=3000)
            assert page.locator(".topbar .suggest-list .wbtn").count() == 0  # no Watch button
            _open_menu(page, row, width)
            assert _menu_items(page) == ["Open card", "Watch this card", "Open on Scryfall"]
            # a deck row ("Search decks named …") has no card menu: the browser's own stays
            page.locator(".ctxmenu.cs-menu button", has_text="Watch this card").click()
            toast = page.locator(".toast.cards-toast")
            toast.wait_for(timeout=5000)
            assert toast.locator(".msg").inner_text() == "Sol Ring added to your watchlist."
            watched = server.app.state.gateway.prices.watched_cards("user-1")
            assert [w["name"] for w in watched] == ["Sol Ring"]
            assert page.evaluate("document.documentElement.scrollWidth") <= width
            # Open card: this page has no card viewer, so it opens the card's search
            if width < 600 and not page.locator("#site-q").is_visible():
                page.locator(".topbar .searchbtn").click()
            box.click()
            row.wait_for(timeout=3000)
            _open_menu(page, row, width)
            page.locator(".ctxmenu.cs-menu button", has_text="Open card").click()
            page.wait_for_url(re.compile(r".*/cards\?q=Sol(%20|\+)Ring$"), timeout=5000)
            assert errors == []
            browser.close()
    finally:
        server.stop()


def test_the_top_bar_menu_has_no_watch_with_watchlists_off(tmp_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path, watchlists_enabled=False)
    try:
        sid = server.sign_in()
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            page = _context(browser, server, sid, 1280).new_page()
            page.goto(f"{server.base}/account/details", wait_until="networkidle")
            box = page.locator("#site-q")
            box.click()
            box.press_sequentially("sol", delay=30)
            row = page.locator("#site-q-list [role=option][data-name='Sol Ring']")
            row.wait_for(timeout=3000)
            row.click(button="right")
            assert _menu_items(page) == ["Open card", "Open on Scryfall"]
            # the Decks rows keep the browser's own menu
            page.keyboard.press("Escape")
            assert page.locator(".ctxmenu.cs-menu").count() == 0
            deck_row = page.locator("#site-q-list li.go").first
            deck_row.wait_for(timeout=3000)
            deck_row.click(button="right")
            page.wait_for_timeout(300)
            assert page.locator(".ctxmenu.cs-menu").count() == 0
            browser.close()
    finally:
        server.stop()


def test_a_pen_hold_opens_the_menu_and_a_slide_or_a_later_key_does_not_stick(server_cards) -> None:
    """A pen press and hold opens the card menu like a finger's; sliding the finger 12 px in small
    steps is a scroll, not a hold; and after a hold the swallowed tap is a moment only: Enter on
    the card then opens it as ever."""
    from playwright.sync_api import sync_playwright

    server, exe = server_cards
    sid = server.sign_in()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        page = _context(browser, server, sid, 390).new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/cards?q=sol", wait_until="networkidle")
        pic = page.locator(".cc .pic").first
        box = pic.bounding_box()
        assert box
        x, y = box["x"] + 30, box["y"] + 30
        pointer = {"clientX": x, "clientY": y, "isPrimary": True, "bubbles": True}
        menu = page.locator(".ctxmenu.cs-menu")
        # a pen
        pic.dispatch_event("pointerdown", {**pointer, "pointerType": "pen"})
        menu.wait_for(timeout=3000)
        pic.dispatch_event("pointerup", {**pointer, "pointerType": "pen"})
        page.keyboard.press("Escape")
        assert menu.count() == 0
        # a slow slide: three steps of 4 px
        pic.dispatch_event("pointerdown", {**pointer, "pointerType": "touch"})
        for i in (1, 2, 3):
            pic.dispatch_event("pointermove", {**pointer, "clientY": y + 4 * i, "pointerType": "touch"})
        page.wait_for_timeout(800)
        assert menu.count() == 0
        pic.dispatch_event("pointerup", {**pointer, "clientY": y + 12, "pointerType": "touch"})
        # a finger's hold, then the keyboard
        pic.dispatch_event("pointerdown", {**pointer, "pointerType": "touch"})
        menu.wait_for(timeout=3000)
        pic.dispatch_event("pointerup", {**pointer, "pointerType": "touch"})
        page.keyboard.press("Escape")
        assert menu.count() == 0
        page.wait_for_timeout(500)
        pic.focus()
        page.keyboard.press("Enter")
        page.locator(".cardview.open").wait_for(timeout=3000)
        assert errors == []
        browser.close()


@pytest.fixture
def server_cards(tmp_path: Path):
    from .test_cardsearch_browser import CATALOG

    s = Server(tmp_path, watchlists_enabled=True)
    s.sf.catalog = CATALOG + [f"Filler Card {i}" for i in range(50)]
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    s.start()
    try:
        yield s, _exe()
    finally:
        s.stop()


def test_the_list_stays_open_when_the_box_gets_its_focus_back_at_once(tmp_path: Path) -> None:
    """The box closes its list a moment after it loses the focus (a click elsewhere). When the focus
    comes straight back before that moment, as when Escape closes a card menu opened from the list on
    a slow phone, the list stays: the box has the focus and its suggestions again. Before, the late
    close hid the list, and the focus coming back found it still open, so it never asked again."""
    from playwright.sync_api import sync_playwright

    exe = _exe()
    server = _server(tmp_path)
    try:
        sid = server.sign_in()
        with sync_playwright() as p:
            browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
            page = _context(browser, server, sid, 1280).new_page()
            page.goto(f"{server.base}/account/details", wait_until="networkidle")
            box = page.locator("#site-q")
            box.click()
            box.press_sequentially("sol", delay=30)
            page.locator("#site-q-list li.go").first.wait_for(timeout=3000)
            # the focus leaves and comes back within one task, before the deferred close runs
            page.evaluate("() => { const q = document.getElementById('site-q'); q.blur(); q.focus(); }")
            page.wait_for_timeout(300)
            assert page.locator("#site-q-list").is_visible()
            assert page.locator("#site-q-list li.go").first.is_visible()
            # a real move away still closes it
            page.evaluate("() => document.getElementById('site-q').blur()")
            page.wait_for_timeout(300)
            assert not page.locator("#site-q-list").is_visible()
            browser.close()
    finally:
        server.stop()
