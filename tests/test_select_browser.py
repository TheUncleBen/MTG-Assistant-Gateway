"""The site's own dropdown lists in headless Chromium (static/select.js, R-131): every single-value
select keeps its native control for the form but opens a themed listbox; a pick sets the native
select's value and fires its change handler (the Decks filter form re-submits); the list works
from the keyboard alone; at phone width it is a bottom sheet with finger-sized rows; the deck
editor's rows are enhanced, rows added by script too, and a pick there reaches the editor's
pending changes. Skipped without a Chromium build."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from .test_editor_browser import PNG, _link
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.ark.add_side_row(42, "Sol Ring")
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _exe() -> str:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _browser(p, exe: str, server: Server, width: int, sid: str):
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(viewport={"width": width, "height": 800})
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return browser, page, errors


ENHANCED = (
    "() => [...document.querySelectorAll('select')].map(s => ({"
    " id: s.id || s.name, hidden: s.getAttribute('aria-hidden'), tab: s.getAttribute('tabindex'),"
    " btn: !!(s.nextElementSibling && s.nextElementSibling.matches('button.msel-btn[role=combobox]')),"
    " label: s.nextElementSibling ? s.nextElementSibling.textContent.trim() : null,"
    " chosen: s.options[s.selectedIndex] ? s.options[s.selectedIndex].text.trim() : null }))"
)


def test_decks_filter_opens_a_themed_list_and_a_pick_applies(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _browser(p, exe, server, 1366, sid)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        natives = page.evaluate(ENHANCED)
        assert natives and all(n["btn"] and n["hidden"] == "true" and n["tab"] == "-1" for n in natives), (
            natives
        )
        assert all(n["label"] == n["chosen"] for n in natives), natives
        assert page.locator("#msel-list").count() == 0  # nothing built until a list opens
        trigger = page.locator("#f-order-btn")
        assert trigger.get_attribute("aria-labelledby") and trigger.get_attribute("aria-expanded") == "false"
        trigger.click()
        listbox = page.locator("#msel-list[role=listbox]")
        listbox.wait_for(state="visible", timeout=3000)
        assert trigger.get_attribute("aria-expanded") == "true"
        assert "sheet" not in listbox.get_attribute("class").split()
        options = listbox.locator("[role=option]")
        assert options.count() == page.locator("#f-order option").count() > 1
        # the chosen row is marked and highlighted; focus stays on the trigger
        assert listbox.locator("[role=option].on").count() == 1
        assert page.evaluate("() => document.activeElement.id") == "f-order-btn"
        active = trigger.get_attribute("aria-activedescendant")
        assert active and listbox.locator(f"#{active}").get_attribute("aria-selected") == "true"
        before = page.locator("#f-order").input_value()
        page.keyboard.press("ArrowDown")
        with page.expect_navigation():
            page.keyboard.press("Enter")
        page.wait_for_load_state("networkidle")
        after = page.locator("#f-order").input_value()
        assert after != before and f"order={after}" in page.url
        assert (
            page.locator("#f-order-btn").inner_text() == page.locator("#f-order option:checked").inner_text()
        )
        # a click outside closes without a change; a click on a row applies
        page.locator("#f-view-btn").click()
        listbox.wait_for(state="visible", timeout=3000)
        page.mouse.click(5, 5)
        assert page.locator("#msel-list").count() == 0
        assert page.locator("#f-view").input_value() == "grid"
        page.locator("#f-view-btn").click()
        listbox.wait_for(state="visible", timeout=3000)
        with page.expect_navigation():
            listbox.locator("[role=option]", has_text="List").click()
        page.wait_for_load_state("networkidle")
        assert page.locator("#f-view").input_value() == "list" and "view=list" in page.url
        assert errors == []
        browser.close()


def test_keyboard_only(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _browser(p, exe, server, 1366, sid)
        page.goto(f"{server.base}/decks/new", wait_until="networkidle")
        # Tab reaches the trigger, never the native select
        page.locator("#name").focus()
        for _ in range(12):
            page.keyboard.press("Tab")
            if page.evaluate("() => document.activeElement.id") == "format-btn":
                break
        assert page.evaluate("() => document.activeElement.id") == "format-btn"
        page.keyboard.press("Enter")
        listbox = page.locator("#msel-list[role=listbox]")
        listbox.wait_for(state="visible", timeout=3000)
        texts = [t.strip() for t in listbox.locator("[role=option]").all_inner_texts()]
        page.keyboard.press("End")
        page.keyboard.press("Enter")
        assert page.locator("#msel-list").count() == 0
        assert page.locator("#format option:checked").inner_text().strip() == texts[-1]
        assert page.locator("#format-btn").inner_text().strip() == texts[-1]
        assert page.evaluate("() => document.activeElement.id") == "format-btn"
        # Space opens, Home then Down moves, Escape closes without a change
        page.keyboard.press("Space")
        listbox.wait_for(state="visible", timeout=3000)
        page.keyboard.press("Home")
        page.keyboard.press("ArrowDown")
        assert listbox.locator("[role=option][aria-selected=true]").inner_text().strip() == texts[1]
        page.keyboard.press("Escape")
        assert page.locator("#msel-list").count() == 0
        assert page.locator("#format option:checked").inner_text().strip() == texts[-1]
        # type-ahead: the first letters of a choice jump to it; Tab closes and moves on
        first = texts[0]
        page.keyboard.press("ArrowDown")
        listbox.wait_for(state="visible", timeout=3000)
        page.keyboard.type(first[:2].lower())
        chosen = listbox.locator("[role=option][aria-selected=true]").inner_text().strip().lower()
        assert chosen.startswith(first[:2].lower())
        page.keyboard.press("Tab")
        assert page.locator("#msel-list").count() == 0
        assert page.evaluate("() => document.activeElement.id") != "format-btn"
        assert errors == []
        browser.close()


def test_phone_width_opens_a_bottom_sheet(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _browser(p, exe, server, 360, sid)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        page.locator("#f-order-btn").click()
        listbox = page.locator("#msel-list[role=listbox].sheet")
        listbox.wait_for(state="visible", timeout=3000)
        page.wait_for_timeout(300)  # the sheet slides in
        box = page.evaluate(
            "() => { const l = document.getElementById('msel-list'); const r = l.getBoundingClientRect();"
            " return { left: r.left, right: r.right, bottom: r.bottom,"
            " scrim: !!document.querySelector('.msel-scrim'),"
            " rows: [...l.querySelectorAll('[role=option]')].map(o => o.getBoundingClientRect().height),"
            " title: (l.querySelector('.head.title') || {}).textContent || '' }; }"
        )
        assert box["left"] == 0 and box["right"] == 360 and box["bottom"] == 800, box
        assert box["scrim"] and box["rows"] and min(box["rows"]) >= 40, box
        assert "Order" in box["title"]
        assert page.evaluate("() => document.documentElement.scrollWidth") <= 360
        # a tap on the scrim closes the sheet; a tap on a row picks
        page.mouse.click(180, 20)
        assert page.locator("#msel-list").count() == 0
        page.locator("#f-order-btn").click()
        listbox.wait_for(state="visible", timeout=3000)
        with page.expect_navigation():
            listbox.locator("[role=option]").last.click()
        page.wait_for_load_state("networkidle")
        assert (
            page.locator("#f-order-btn").inner_text() == page.locator("#f-order option:checked").inner_text()
        )
        assert errors == []
        browser.close()


def test_editor_rows_are_enhanced_as_they_appear(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sf = server.sf
    sf.catalog = sorted({c["name"] for c in sf.cards}) + [f"Filler Card {i}" for i in range(1200)]
    server.app.state.gateway.scan.names.set_names(sf.catalog)
    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _browser(p, exe, server, 1366, sid)
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        rows = page.locator(".erow:not(.new)")
        assert rows.count() >= 2
        natives = page.evaluate(ENHANCED)
        row_selects = page.locator(".erow .sel select")
        assert row_selects.count() >= rows.count()  # category and finish per row (a side row: finish only)
        assert all(n["btn"] and n["hidden"] == "true" and n["tab"] == "-1" for n in natives), natives
        assert page.locator("#msel-list").count() == 0
        # the row's category label is not clipped (D4): the text fits inside the trigger
        clipped = page.evaluate(
            "() => [...document.querySelectorAll('.erow .sel .msel-btn .msel-txt')]"
            ".filter(t => t.scrollWidth > t.clientWidth + 1).length"
        )
        assert clipped == 0
        # a pick in a row's category list reaches the editor's pending changes
        cat = page.locator(".erow:not(.new) .sel select[aria-label^='Category of']").first
        trigger = cat.locator("xpath=following-sibling::button[1]")
        trigger.click()
        listbox = page.locator("#msel-list[role=listbox]")
        listbox.wait_for(state="visible", timeout=3000)
        current = cat.evaluate("s => s.value")
        target = listbox.locator("[role=option]:not(.on)").first
        wanted = target.inner_text().strip()
        assert wanted != "New category…"
        target.click()
        assert page.locator("#msel-list").count() == 0
        assert cat.evaluate("s => s.value") == wanted != current
        assert trigger.inner_text().strip() == wanted
        assert page.locator(".pendingbox .n").inner_text() == "1"
        assert "Category" in (page.locator(".pending .changes").text_content() or "")
        # a card added by script gets an enhanced row too
        box = page.locator("#addname")
        box.click()
        box.press_sequentially("oathsw", delay=30)
        page.locator(".suggest-list[role=listbox]:visible [role=option]").first.wait_for(timeout=3000)
        box.press("Enter")
        page.wait_for_timeout(300)
        new_row = page.locator(".erow.new").first
        new_sel = new_row.locator(".sel select")
        assert new_sel.count() == 1
        assert new_sel.get_attribute("aria-hidden") == "true" and new_sel.get_attribute("tabindex") == "-1"
        new_btn = new_row.locator(".sel button.msel-btn[role=combobox]")
        assert new_btn.count() == 1 and new_btn.inner_text().strip() == "Auto"
        new_btn.click()
        listbox.wait_for(state="visible", timeout=3000)
        listbox.locator("[role=option]", has_text="Land").first.click()
        assert new_sel.evaluate("s => s.value") == "Land"
        assert new_row.locator(".msel-btn").inner_text().strip() == "Land"
        assert page.locator(".pendingbox .n").inner_text() == "2"
        # one Maybeboard chip, the zone's (the category of the same name has no chip of its own, D10)
        names = page.evaluate(
            "() => [...document.querySelectorAll('.targets .tchip')].map(b => b.textContent.trim())"
        )
        assert len(names) == len(set(names)), names
        assert names.count("Maybeboard") == 1 and page.locator(".targets .tchip.side").count() == 1, names
        assert errors == []
        browser.close()


def test_natives_stay_and_triggers_take_the_focus(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _browser(p, exe, server, 1366, sid)
        for path in ("/collection", "/search", "/history", "/folders", "/decks/42/settings", "/decks/42"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            html_count = page.evaluate("() => document.querySelectorAll('select').length")
            natives = page.evaluate(ENHANCED)
            assert html_count == len(natives) and natives, path
            for n in natives:
                assert n["btn"] and (n["hidden"] == "true" or n["tab"] == "-1"), (path, n)
            focusable = page.evaluate(
                "() => [...document.querySelectorAll('button.msel-btn')].every(b => {"
                " b.focus(); return document.activeElement === b || b.disabled || !b.offsetParent"
                " || !!b.closest('details:not([open])'); })"  # the collection's folded Add box
            )
            assert focusable, path
            assert page.evaluate("() => document.documentElement.scrollWidth") <= 1366
        # a hidden native select focused by a label's click hands the focus to its trigger
        page.goto(f"{server.base}/search", wait_until="networkidle")
        page.locator("label[for=s-order]").click()
        assert page.evaluate("() => document.activeElement.id") == "s-order-btn"
        # the file picker on New deck: the input is off-screen but reachable, the button is themed
        page.goto(f"{server.base}/decks/new", wait_until="networkidle")
        pick = page.evaluate(
            "() => { const i = document.getElementById('file'); const b = i.nextElementSibling;"
            " i.focus(); const r = i.getBoundingClientRect(); const s = getComputedStyle(b);"
            " return { focused: document.activeElement === i, w: r.width, h: r.height, btn: b.className,"
            " text: b.textContent, border: s.borderStyle,"
            " name: document.querySelector('.filepick .fname').textContent }; }"
        )
        assert pick["focused"] and pick["w"] <= 1 and pick["h"] <= 1, pick
        assert pick["btn"] == "btn" and pick["text"] == "Choose a file" and pick["border"] == "solid", pick
        assert pick["name"] == "No file chosen"
        page.locator("#file").set_input_files(
            {"name": "mine.txt", "mimeType": "text/plain", "buffer": b"1 Sol Ring\n"}
        )
        page.wait_for_timeout(200)
        assert page.locator(".filepick .fname").inner_text() == "mine.txt"
        assert page.locator("#source").input_value() == "1 Sol Ring\n"
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [360, 720])
def test_a_tap_outside_the_touch_sheet_only_closes_it(server: Server, width: int) -> None:
    """Re-gate R2-1: on a touch screen the tap that closes the bottom sheet must not also land on
    what is under the finger (here the brand link, which would leave the page)."""
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 800}, has_touch=True, is_mobile=True)
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        btn = page.locator("#f-order-btn").bounding_box()
        assert btn is not None
        page.touchscreen.tap(btn["x"] + btn["width"] / 2, btn["y"] + btn["height"] / 2)
        listbox = page.locator("#msel-list[role=listbox].sheet")
        listbox.wait_for(state="visible", timeout=3000)
        page.wait_for_timeout(300)  # the sheet slides in
        brand = page.locator(".topbar .brand").bounding_box()
        assert brand is not None
        page.touchscreen.tap(brand["x"] + brand["width"] / 2, brand["y"] + brand["height"] / 2)
        page.wait_for_timeout(500)
        assert page.locator("#msel-list").count() == 0
        assert page.url.endswith("/decks"), page.url  # the brand link under the tap was not followed
        # the sheet still works after that: a row tap picks
        page.touchscreen.tap(btn["x"] + btn["width"] / 2, btn["y"] + btn["height"] / 2)
        listbox.wait_for(state="visible", timeout=3000)
        page.wait_for_timeout(300)
        row = listbox.locator("[role=option]").last.bounding_box()
        assert row is not None
        with page.expect_navigation():
            page.touchscreen.tap(row["x"] + row["width"] / 2, row["y"] + row["height"] / 2)
        page.wait_for_load_state("networkidle")
        assert "order=" in page.url
        browser.close()


@pytest.mark.parametrize("width", [360, 720, 1366])
def test_a_collection_cards_details_menu_stays_on_screen(server: Server, width: int) -> None:
    """Re-gate R2-3: in the grid view the first card's Details menu hung off the left edge of the
    window, cutting its Finish, Condition and Language fields; feedback.js shifts a panel back
    inside the window when it opens."""
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _exe()
    with sync_playwright() as p:
        browser, page, errors = _browser(p, exe, server, width, sid)
        page.goto(f"{server.base}/collection", wait_until="networkidle")
        csrf = page.locator("input[name=csrf]").first.get_attribute("value")
        added = page.evaluate(
            """async (csrf) => { const r = await fetch('/collection/api/add', { method: 'POST',
              headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
              body: JSON.stringify({ items: [{ name: 'Sol Ring', quantity: 1 }], source: 'scan' }) });
              return [r.status, await r.text()]; }""",
            csrf,
        )
        assert added[0] == 200, added
        for view in ("grid", "list"):
            page.goto(f"{server.base}/collection?view={view}", wait_until="networkidle")
            menu = page.locator("ul#cards details.rowmenu").first
            menu.locator("summary").click()
            page.wait_for_timeout(150)
            box = page.evaluate(
                "() => { const m = document.querySelector('ul#cards details.rowmenu[open] > .menu');"
                " const r = m.getBoundingClientRect(); return { left: r.left, right: r.right,"
                " width: r.width, vw: document.documentElement.clientWidth }; }"
            )
            assert box["left"] >= 0 and box["right"] <= box["vw"], (view, box)
            assert box["width"] >= 180, (view, box)  # the panel kept its size (14rem); it moved, not shrank
            # its fields are reachable: the Finish trigger sits inside the window too
            fin = page.locator("ul#cards details.rowmenu[open] .menu button.msel-btn").first.bounding_box()
            assert fin is not None and fin["x"] >= 0 and fin["x"] + fin["width"] <= width, (view, fin)
        assert errors == []
        browser.close()
