"""The Watchlist as the want list (mockup screen 8) in headless Chromium: on a phone the first
watched card is on the first screen, Select ticks every row and the bulk bar appears with the
count, a group head's tick box follows its rows, a right-click and a press and hold on a row open
its menu (Details, Move to list…, Stop watching), the ⋮ on a group head opens Rename with the
name filled in, "You own N" shows, and nothing scrolls sideways at 100% or 200% text,
in the app's own browser too. Skipped without a Chromium build."""

from __future__ import annotations

import json
import re
import time
from pathlib import Path

import httpx
import pytest

from .test_cardsearch_browser import PNG
from .test_editor_browser import _link
from .test_menus_browser import _items, _menu, _pick
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_text_zoom_browser import TEXT_200
from .waits import expect, quiet, redrawn

pytest.importorskip("playwright")

APP_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-F946B) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0 Mobile Safari/537.36 MTGAssistant/0.7.46"
)
OVERFLOW = "() => document.documentElement.scrollWidth - document.documentElement.clientWidth"


def _exe() -> str | None:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def seed(server: Server) -> str:
    """alice linked; three watched cards, two in the list “Meren wants”, one in none; Sol Ring in
    her Binder twice (read whole once, so the Watchlist knows it)."""
    sid = _link(server)
    now = int(time.time())
    with server.db.tx() as c:
        c.execute(
            "INSERT INTO watch_lists (sub, name, created_at) VALUES ('user-1', 'Meren wants', ?)", (now,)
        )
        for wid, name, lst, drop in (
            ("w_one", "Sol Ring", "Meren wants", 10),
            ("w_two", "Sol Talisman", "Meren wants", None),
            ("w_three", "Arcane Signet", "", 10),
        ):
            c.execute(
                "INSERT INTO watch_cards (id, sub, name, set_code, collector_number, finish, scryfall_id, "
                "alert_drop_pct, added_price, added_currency, list_name, created_at) "
                "VALUES (?, 'user-1', ?, '', '', 'nonfoil', ?, ?, 2.0, 'usd', ?, ?)",
                (wid, name, "s-" + wid, drop, lst, now),
            )
        c.execute(
            "INSERT INTO watch_decks (sub, deck_id, deck_name, cards_json, refreshed_at, created_at) "
            "VALUES ('user-1', '42', 'Sample Commander Deck', ?, ?, ?)",
            (json.dumps([{"qty": 100}]), now, now),
        )
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/collection").text).group(1)
    r = h.post(
        "/collection/api/add",
        headers={"X-CSRF-Token": csrf},
        json={"items": [{"name": "Sol Ring", "quantity": 2}], "source": "scan"},
    )
    assert r.status_code == 200, r.text
    # the Binder as the daily check last read it whole (binder_prices): Sol Ring, 2 copies
    server.app.state.gateway.prices.binder.replace(
        "user-1",
        [{"id": 1, "scryfall_id": "s-w_one", "name": "Sol Ring", "finish": "nonfoil", "quantity": 2}],
        complete=True,
    )
    return sid


def _open(p, server: Server, sid: str, *, width: int, height: int, theme: str, exe, ua: str | None = None):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    kw = {"viewport": {"width": width, "height": height}, "has_touch": width < 900}
    if ua:
        kw["user_agent"] = ua
    ctx = browser.new_context(**kw)
    ctx.route("https://cards.scryfall.io/**", lambda r: r.fulfill(content_type="image/png", body=PNG))
    ctx.add_cookies(
        [
            {"name": "mtg_session", "value": sid, "url": server.base},
            {"name": "mtg_theme", "value": theme, "url": server.base},
        ]
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path, writes_enabled=True, watchlists_enabled=True)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def test_on_a_phone_the_first_card_is_on_the_first_screen_and_select_works(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = seed(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width=375, height=800, theme="dark", exe=exe)
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        assert page.evaluate(OVERFLOW) == 0
        # the first watched card, on the first screen
        first = page.locator(".w-t .tr[id^='wr-']").first
        box = first.bounding_box()
        assert box is not None and box["y"] + box["height"] <= 800, box
        assert first.get_attribute("id") == "wr-w_one"  # the list “Meren wants” first, its cards by name
        heads = page.locator(".w-t .tr.group.wgrp b").all_inner_texts()
        assert heads == ["Meren wants", "Not in a list"]
        owned = page.locator("#wr-w_one .ui-lbl.owned").first.inner_text()
        assert owned == "You own\u00a02"
        assert page.locator("#wr-w_two .ui-lbl.owned").count() == 0
        # the group head's ⋮ is a script menu (never the page's drop-down, whose history entry
        # would race the box's): Rename list… opens the box with the name filled in; Delete list…
        # is red; "Not in a list" has no menu
        head = page.locator(".wgrp[data-list='Meren wants']")
        head.locator("summary.kebab").click()
        _menu(page)
        assert _items(page) == ["Rename list…", "Delete list…"]
        assert "danger" in (
            page.locator(".ctxmenu [role=menuitem]", has_text="Delete list…").get_attribute("class") or ""
        )
        assert not head.locator("details").evaluate("d => d.open")
        _pick(page, "Rename list…")
        page.locator("#w-rename:not([hidden])").wait_for(timeout=3000)
        rename = page.locator("#w-rename")
        assert rename.locator("input[name=new_name]").input_value() == "Meren wants"
        assert rename.locator("input[name=name]").input_value() == "Meren wants"  # the hidden one
        quiet(page)  # the menu's history entry lands, then the box holds its own
        assert page.locator("#w-rename:not([hidden])").count() == 1
        page.keyboard.press("Escape")
        page.wait_for_selector("#w-rename:not([hidden])", state="detached", timeout=3000)
        quiet(page)  # the box's history entry goes back (box.js) before the next opens
        page.locator(".wgrp[data-list=''] .gname").click(button="right")
        # fixed wait: a negative check: a right-click on the head of no list opens no menu within 300 ms
        page.wait_for_timeout(300)
        assert page.locator(".ctxmenu").count() == 0
        # Select (shown by the script) ticks every row; the bar says how many; Cancel clears
        bar = page.locator(".ui-bar[aria-label='Selected cards']")
        assert bar.is_hidden()
        select = page.locator("button.w-select")
        assert select.is_visible() and select.bounding_box()["height"] >= 44
        select.click()
        assert bar.is_visible() and bar.locator(".count").inner_text() == "3 selected"
        assert select.get_attribute("aria-pressed") == "true"
        assert page.locator(".w-t .tr.sel").count() == 3
        # one slim row (final audit F01): × Cancel, the count, ⋯ (Price alert…, Move to list…),
        # Stop watching (its word folds away on a narrow bar; the icon and the name stay)
        assert bar.bounding_box()["height"] <= 60
        cancel = bar.locator("button.w-clear")
        assert cancel.get_attribute("aria-label") == "Cancel" and cancel.bounding_box()["width"] >= 44
        stop = bar.locator("a[data-box-open='w-stop']")
        assert stop.get_attribute("aria-label") == "Stop watching"
        assert stop.bounding_box()["height"] >= 44 and stop.bounding_box()["width"] >= 44
        assert "btn-outline-danger" in (stop.get_attribute("class") or "")
        bar.locator("details.bar-more > summary").click()
        menu = bar.locator("details.bar-more .menu")
        menu.wait_for(state="visible", timeout=3000)
        assert menu.locator("a").all_inner_texts() == ["Price alert…", "Move to list…"]
        for action in ("Price alert…", "Move to list…"):
            assert menu.locator("a", has_text=action).bounding_box()["height"] >= 44, action
        page.keyboard.press("Escape")
        bar.locator("details.bar-more").evaluate("d => { d.open = false; }")
        # Stop watching goes at once (no box asks): the notice names the cards and offers Undo,
        # which brings them back as they were (0.7.46 audit QG-09)
        stop.click()
        page.wait_for_url("**/watchlist?ok=removed&*", timeout=8000)
        notice = page.locator(".notice.ok")
        assert "are no longer watched." in notice.inner_text()
        assert page.locator(".w-t .chk input[name=watch_id]").count() == 0  # the card rows went
        notice.locator("button", has_text="Undo").click()
        page.wait_for_url("**/watchlist?ok=rewatched&*", timeout=8000)
        assert "are watched again." in page.locator(".notice.ok").inner_text()
        assert page.locator(".w-t .chk input[name=watch_id]").count() == 3
        bar = page.locator(".ui-bar[aria-label='Selected cards']")
        select = page.locator("button.w-select")
        assert bar.is_hidden() and page.locator(".w-t .tr.sel").count() == 0
        # one tick: the bar counts it; the list's own tick box is part-way
        page.locator("#wr-w_one input[name=watch_id]").check()
        assert bar.locator(".count").inner_text() == "1 selected"
        grp = page.locator(".wgrp[data-list='Meren wants'] input.w-grpsel")
        assert grp.evaluate("i => i.indeterminate") is True
        grp.click()  # the whole list
        assert bar.locator(".count").inner_text() == "2 selected"
        assert grp.evaluate("i => i.checked") is True and grp.evaluate("i => i.indeterminate") is False
        bar.locator("button.w-clear").click()
        # a right-click on a row: its menu, with Details, Move to list… and the red Stop watching
        page.locator("#wr-w_two .title").click(button="right")
        _menu(page)
        items = _items(page)
        assert "Details" in items and "Move to list…" in items and items[-1] == "Stop watching", items
        assert "Remove" not in items
        _pick(page, "Move to list…")
        move = page.locator("#w-move:not([hidden])")
        move.wait_for(timeout=3000)
        assert bar.locator(".count").inner_text() == "1 selected"  # that row alone
        assert page.locator("#wr-w_two input[name=watch_id]").is_checked()
        assert move.locator("select option").all_inner_texts() == ["Not in a list", "Meren wants"]
        page.keyboard.press("Escape")
        page.wait_for_selector("#w-move:not([hidden])", state="detached", timeout=3000)
        quiet(page)  # the box's history entry goes back (box.js) before the next opens
        # a press and hold on a row (as Chrome on Android sends it) opens the same menu
        row = page.locator("#wr-w_three .title")
        row.scroll_into_view_if_needed()
        b = row.bounding_box()
        x, y = b["x"] + 20, b["y"] + b["height"] / 2
        cdp = page.context.new_cdp_session(page)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        page.locator(".ctxmenu").first.wait_for(timeout=3000)  # held: the hold opened the menu
        cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
        row.evaluate(
            """(t, xy) => t.dispatchEvent(new MouseEvent("contextmenu",
                {bubbles: true, cancelable: true, clientX: xy[0], clientY: xy[1]}))""",
            [x, y],
        )
        _menu(page)
        assert "Stop watching" in _items(page) and page.evaluate(OVERFLOW) == 0
        page.keyboard.press("Escape")
        page.wait_for_selector(".ctxmenu", state="detached", timeout=3000)
        assert errors == [], errors
        browser.close()


@pytest.mark.parametrize(
    ("width", "height", "theme", "ua"),
    [(375, 800, "light", APP_UA), (840, 1000, "dark", APP_UA), (1280, 900, "light", None)],
)
def test_every_surface_fits_at_100_and_200_percent_text(
    server: Server, width: int, height: int, theme: str, ua: str | None
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _exe()
    sid = seed(server)
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width=width, height=height, theme=theme, exe=exe, ua=ua)
        for path in ("/watchlist", "/watchlist?box=add", "/collection/lists", "/collection/wants"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            assert page.evaluate(OVERFLOW) == 0, (path, width)
            page.evaluate(TEXT_200)
            redrawn(page)
            assert page.evaluate(OVERFLOW) == 0, (path, width, "200%")
        # Binder › Lists: the five Archidekt lists stacked as rows, each whole on the screen, their
        # counts filled by the script; the watchlist's lists under them
        page.goto(f"{server.base}/collection/lists", wait_until="networkidle")
        rows = page.locator(".lp-hubrows a.yrow")
        assert rows.count() == 6  # five Archidekt lists and Binder tags
        for i in range(6):
            b = rows.nth(i).bounding_box()
            assert b and b["x"] >= 0 and b["x"] + b["width"] <= width and b["height"] >= 44, (i, b)
        pins_val = '.lp-hubrows a[href="/collection/wants"] .val'
        page.wait_for_function(f"() => /^\\d+$/.test(document.querySelector('{pins_val}').textContent)")
        # One Binder (0.7.47): the watchlist's lists are on the Binder page
        assert page.locator(".lp-hub .wlrow").count() == 0
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        # the heading names the page and counts the cards; Select is a real control everywhere
        assert page.locator(".ui-head h1").inner_text().startswith("Watchlist")
        assert page.locator(".ui-head h1 .cn").inner_text() == "3"
        page.locator("button.w-select").click()
        bar = page.locator(".ui-bar[aria-label='Selected cards']")
        assert bar.is_visible() and bar.locator(".count").inner_text() == "3 selected"
        assert page.evaluate(OVERFLOW) == 0
        page.locator("#wr-w_one .title").click(button="right")
        _menu(page)
        assert "Move to list…" in _items(page)
        page.keyboard.press("Escape")
        assert errors == [], errors
        browser.close()


def test_a_list_on_the_binder_page_has_rename_and_delete_in_its_menu(server: Server) -> None:
    """One Binder (0.7.47): a list is a row of the Binder page's library; opened, its List menu has
    Rename list… and Delete list…, each a box on the page."""
    from playwright.sync_api import sync_playwright

    sid = seed(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _open(p, server, sid, width=1280, height=900, theme="light", exe=exe)
        page.goto(f"{server.base}/binder", wait_until="networkidle")
        with page.expect_navigation(wait_until="networkidle"):
            page.locator(".bn-lib a.yrow", has_text="Meren wants").click()
        assert page.locator("#bn-title").inner_text() == "Meren wants"
        page.locator(".bn-listmenu > summary").click()
        items = page.locator(".bn-listmenu .menu a").all_inner_texts()
        assert [t.strip() for t in items] == ["Rename list…", "Delete list…"]
        page.locator(".bn-listmenu .menu a", has_text="Rename list…").click()
        box = page.locator("#bn-rename")
        expect(box).to_be_visible()
        assert box.locator("input[name=new_name]").input_value() == "Meren wants"
        page.keyboard.press("Escape")
        assert errors == [], errors
        browser.close()
