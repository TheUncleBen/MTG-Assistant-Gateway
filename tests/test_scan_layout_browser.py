"""The Scan page in headless Chromium, in a browser and as inside the Android app (its user agent
and its window.MtgNative, which adds the "Phone camera" button): one column at the form width,
the options row and the capture bar each one row, the capture button a circle in the middle, the
site's icons and no emoji, one orange button, 44 px targets on touch, and the row menus of the
list and of the saved scans. Needs no OCR assets, unlike test_scan_browser.py. Skipped without a
Chromium build."""

from __future__ import annotations

import json

import pytest

from .test_cardsearch_browser import _chromium
from .test_suggest_browser import Server, server  # noqa: F401

pytest.importorskip("playwright")

WIDTHS = [344, 390, 810, 1280]
# The Android app's WebView (theme.py APP_UA_MARK).
APP_UA = (
    "Mozilla/5.0 (Linux; Android 14; SM-F956B) AppleWebKit/537.36 Chrome/124.0 Mobile Safari/537.36 "
    "MTGAssistant/0.7.36"
)
# A card as the resolver returns it, for list rows without a lookup.
CARD = json.dumps(
    {
        "name": "Sol Ring",
        "set": "cmr",
        "collector_number": "472",
        "scryfall_id": "sol-ring-cmr",
        "oracle_id": "sol-ring",
        "image_small": "",
        "finishes": ["nonfoil", "foil"],
    }
)
ITEMS = (
    "() => { const s = window.__scan; s.state.items = ["
    "  {quantity: 2, name: 'Sol Ring', status: 'exact', note: '', card: " + CARD + "},"
    "  {quantity: 1, name: 'Cultivate', status: 'not_found', note: '', card: null}];"
    " s.showTab('list'); }"
)
PENDING = (
    "() => { window.__scan.state.pending = {status: 'exact', quantity: 1, card: " + CARD + "};"
    " window.__scan.showTab('camera'); }"
)
# The page as laid out now: the options row and the capture bar (every visible child on one line)
# when `bars`, the capture button (a circle, centred in its bar), sideways overflow, emoji or
# text glyphs used as icons, the targets under 44 px when `touch`, and the orange buttons.
MEASURE = """
([touch, bars]) => {
  const app = document.querySelector('#scan-app');
  const out = {problems: []};
  const vis = (el) => { const r = el.getBoundingClientRect(); const cs = getComputedStyle(el);
    return r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && !el.closest('[hidden]'); };
  const oneRow = (sel) => {
    const bar = app.querySelector(sel); if (!bar) { out.problems.push(sel + ' missing'); return; }
    const kids = [...bar.children].filter((k) => vis(k) && getComputedStyle(k).position !== 'absolute');
    const b = bar.getBoundingClientRect(), cy = b.top + b.height / 2;
    for (const k of kids) {
      const r = k.getBoundingClientRect();
      const off = Math.abs(r.top + r.height / 2 - cy) > 2;
      if (off) out.problems.push(sel + ': ' + (k.id || k.className) + ' off the row');
    }
    if (b.height > 100) out.problems.push(sel + ' is ' + b.height + 'px tall');
    if (b.right > document.documentElement.clientWidth + 1) out.problems.push(sel + ' runs past the window');
  };
  if (bars) {
    oneRow('.scan-opts'); oneRow('.controls');
    const sh = app.querySelector('#shutter'), bar = app.querySelector('.controls');
    const r = sh.getBoundingClientRect(), b = bar.getBoundingClientRect();
    if (Math.abs(r.width - r.height) > 1) out.problems.push('capture button ' + r.width + 'x' + r.height);
    const dx = Math.abs((r.left + r.width / 2) - (b.left + b.width / 2));
    if (dx > 2) out.problems.push('capture button off centre');
    const fill = getComputedStyle(sh).backgroundColor;
    if (fill !== 'rgb(250, 137, 13)') out.problems.push('capture button not orange');
  }
  const de = document.documentElement;
  const over = de.scrollWidth - de.clientWidth;
  if (over > 0) out.problems.push('scrolls sideways by ' + over + 'px');
  const text = app.textContent;
  if (/\\p{Extended_Pictographic}/u.test(text)) out.problems.push('emoji in the page');
  for (const el of app.querySelectorAll('button, label, span')) {
    const t = el.textContent.trim();
    const glyph = /^[\\u00d7\\u2715\\u26a1\\u2600\\u21ba\\u25be]$/.test(t);
    if (glyph) out.problems.push('glyph icon ' + t + ' in ' + el.className);
  }
  if (/Signed in as/.test(text)) out.problems.push('"Signed in as" is on the page');
  const targets = 'button, a.btn, label.btn, [role=tab], input:not([type=hidden]):not([type=range]),'
    + ' .sessions a';
  if (touch) for (const el of app.querySelectorAll(targets)) {
    if (!vis(el) || el.classList.contains('sr')) continue;
    const r = el.getBoundingClientRect();
    const wide = el.tagName === 'INPUT' || el.tagName === 'A' || r.width >= 43.5;
    if (r.height < 43.5 || !wide) {
      const what = el.id || el.className || el.textContent.trim().slice(0, 20);
      out.problems.push('target ' + Math.round(r.width) + 'x' + Math.round(r.height) + ': ' + what);
    }
  }
  out.orange = [...app.querySelectorAll('button, .btn, label.btn')]
    .filter((el) => vis(el) && getComputedStyle(el).backgroundColor === 'rgb(250, 137, 13)')
    .map((el) => el.id || el.textContent.trim());
  return out;
}
"""


def _clear(color: str) -> bool:
    """A see-through background: transparent, or any colour at alpha 0 (a hover fading out)."""
    return (
        color == "transparent"
        or color.startswith("rgba(")
        and color.rstrip(")").split(",")[-1].strip() == "0"
    )


def _open(p, exe, srv: Server, sid: str, width: int, app: bool, scheme: str):
    """A page at `width`, as the app draws it (its user agent and bridge object) when `app`."""
    touch = width < 1024 or app
    kw: dict = {"viewport": {"width": width, "height": 860}, "has_touch": touch, "is_mobile": touch}
    kw["color_scheme"] = scheme
    if app:
        kw["user_agent"] = APP_UA
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(**kw)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": srv.base}])
    ctx.grant_permissions(["clipboard-read", "clipboard-write"])
    if app:
        ctx.add_init_script("window.MtgNative = {openCamera: function () { window.__opened = 1; }};")
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{srv.base}/scan")
    page.wait_for_function("() => window.__scan && window.__scan.showTab")
    return browser, page, errors, touch


def _style(loc, prop: str) -> str:
    return loc.evaluate(f"e => getComputedStyle(e).{prop}")


@pytest.mark.parametrize("app", [False, True], ids=["browser", "app"])
@pytest.mark.parametrize("width", WIDTHS)
def test_one_column_one_row_bars_and_a_round_centred_capture_button(
    server: Server,  # noqa: F811
    width: int,
    app: bool,
) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        for scheme in ("light", "dark"):
            browser, page, errors, touch = _open(p, exe, server, server.sign_in(), width, app, scheme)
            problems: list[str] = []
            where = f"{width}px {'app' if app else 'browser'} {scheme}"
            # the page keeps the standard's form column, not a width of its own
            assert _style(page.locator("main.wrap"), "maxWidth") == "728px"
            assert _style(page.locator(".cam-panel"), "display") == "flex"
            # the app's phone camera button closes the options row; a browser has none
            assert page.locator("#phone-camera").count() == (1 if app else 0)
            if app:
                assert page.locator(".scan-opts > :last-child").get_attribute("id") == "phone-camera"
            # the × sits inside the Set box, and the hint is its tooltip
            box, clear = (
                page.locator(".setbox").bounding_box(),
                page.locator(".setbox .clear").bounding_box(),
            )
            assert box["x"] < clear["x"], (box, clear)
            assert clear["x"] + clear["width"] <= box["x"] + box["width"] + 0.5, (box, clear)
            assert page.locator("#set-lock").get_attribute("title") == "Set code, blank for any"
            tabs = (
                ("camera", ["shutter"]),
                ("list", ["save-btn"]),
                ("type", ["Resolve list"]),
                ("sessions", []),
            )
            for tab, orange in tabs:
                if tab == "list":
                    page.evaluate(ITEMS)
                else:
                    page.evaluate(f"() => window.__scan.showTab('{tab}')")
                m = page.evaluate(MEASURE, [touch, tab == "camera"])
                problems += [f"{where} {tab}: {x}" for x in m["problems"]]
                if m["orange"] != orange:
                    problems.append(f"{where} {tab}: orange buttons {m['orange']}, expected {orange}")
            # a match on the camera tab: Add is plain, Other printings is a full target
            page.evaluate(PENDING)
            m = page.evaluate(MEASURE, [touch, True])
            problems += [f"{where} camera with a result: {x}" for x in m["problems"]]
            if m["orange"] != ["shutter"]:
                problems.append(f"{where} camera with a result: orange buttons {m['orange']}")
            # the New scan question: a red outline asks, solid red only on Yes, both full targets
            page.evaluate(ITEMS)
            btn = page.locator("button:has-text('New scan')")
            assert _clear(_style(btn, "backgroundColor"))
            btn.click()
            bar = page.locator(".scan .confirmbar")
            assert _style(bar.locator("button:has-text('Yes')"), "backgroundColor") == "rgb(192, 24, 43)"
            if touch:
                for b in bar.locator("button").all():
                    assert b.bounding_box()["height"] >= 43.5
            bar.locator("button:has-text('Cancel')").click()
            assert problems == [], "\n".join(problems)
            assert errors == [], errors
            browser.close()


def test_a_saved_scan_is_named_never_numbered(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import expect, sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors, _ = _open(p, exe, server, server.sign_in(), 390, False, "dark")
        page.evaluate(ITEMS)
        page.fill("#session-name", "Trade binder")
        page.get_by_role("button", name="Save scan").click()
        expect(page.locator("#list-msg .notice.ok")).to_contain_text("Saved as Trade binder")
        assert "scan_" not in page.locator("#list-msg").inner_text()
        assert page.locator("#summary").inner_text().endswith("saved as Trade binder")
        assert errors == []
        browser.close()


def _menu_items(page) -> list[str]:
    return page.locator(".ctxmenu.scan-menu [role=menuitem]").evaluate_all(
        "els => els.map(e => e.getAttribute('aria-label') || e.textContent.trim())"
    )


@pytest.mark.parametrize("width", [390, 1280])
def test_the_list_cards_menu_opens_every_way_and_its_items_work(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import expect, sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors, touch = _open(p, exe, server, server.sign_in(), width, False, "light")
        page.evaluate(ITEMS)
        rows = page.locator("ul.items > li")
        expect(rows).to_have_count(2)
        menu = page.locator(".ctxmenu.scan-menu")
        # right-click
        rows.first.locator(".name > span").first.click(button="right")
        expect(menu).to_be_visible()
        assert menu.locator(".head").inner_text() == "Sol Ring"
        assert _menu_items(page) == [
            "Open card",
            "Add to deck…",
            "Add to collection",
            "Change printing…",
            "One fewer",
            "One more",
            "Copy name",
            "Open on Scryfall",
            "Remove",
        ]
        # Add to deck… is the card search for the name; Watch this card only with watchlists on
        add = menu.locator("a[role=menuitem]", has_text="Add to deck…")
        assert add.get_attribute("href") == "/cards?q=Sol%20Ring"
        assert not page.evaluate("() => document.body.hasAttribute('data-watchlist')")
        page.keyboard.press("Escape")
        expect(menu).to_have_count(0)
        page.evaluate("() => document.body.setAttribute('data-watchlist', '')")
        rows.first.locator(".name > span").first.click(button="right")
        expect(menu).to_be_visible()
        assert _menu_items(page)[:4] == ["Open card", "Add to deck…", "Add to collection", "Watch this card"]
        page.evaluate("() => document.body.removeAttribute('data-watchlist')")
        page.keyboard.press("Escape")
        expect(menu).to_have_count(0)
        rows.first.locator(".name > span").first.click(button="right")
        expect(menu).to_be_visible()
        if width < 600:  # a bottom sheet over a dimmed page on a phone
            box = menu.bounding_box()
            assert box["x"] == 0 and abs(box["y"] + box["height"] - 860) < 1, box
            assert page.locator(".cs-scrim").is_visible()
        # the quantity row works in place and keeps the menu open
        menu.get_by_role("menuitem", name="One more").click()
        expect(menu).to_be_visible()
        expect(rows.first.locator("input[type=number]")).to_have_value("3")
        expect(page.locator("#list-badge")).to_have_text("4")
        # Open on Scryfall is a link in a new tab
        scry = menu.locator("a[role=menuitem]", has_text="Open on Scryfall")
        assert "scryfall.com" in (scry.get_attribute("href") or "")
        assert scry.get_attribute("target") == "_blank"
        # Escape closes it and focus goes back to the row's ⋮
        page.keyboard.press("Escape")
        expect(menu).to_have_count(0)
        assert page.evaluate("() => document.activeElement.className") == "kebab"
        # the ⋮ button, then Copy name
        rows.first.locator(".kebab").click()
        expect(menu).to_be_visible()
        menu.get_by_role("menuitem", name="Copy name").click()
        expect(menu).to_have_count(0)
        assert page.evaluate("() => navigator.clipboard.readText()") == "Sol Ring"
        expect(page.locator(".toast.cards-toast")).to_contain_text("Name copied.")
        # Shift+F10 on a focused row control opens that row's menu
        rows.nth(1).locator(".kebab").focus()
        page.keyboard.press("Shift+F10")
        expect(menu).to_be_visible()
        assert menu.locator(".head").inner_text() == "Cultivate"
        assert "Change printing…" not in _menu_items(page)  # no printing without a match
        page.keyboard.press("Escape")
        # Change printing… opens the printing picker
        rows.first.locator(".kebab").click()
        menu.get_by_role("menuitem", name="Change printing…").click()
        expect(page.locator("#picker .sheet[role=dialog]")).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.locator("#picker")).to_have_count(0)
        # Remove takes the row off the list
        rows.nth(1).locator(".kebab").click()
        menu.get_by_role("menuitem", name="Remove").click()
        expect(rows).to_have_count(1)
        expect(page.locator("#list-badge")).to_have_text("3")
        assert page.evaluate("() => window.__scan.state.items.map((i) => i.name)") == ["Sol Ring"]
        assert errors == []
        browser.close()


def test_a_press_and_hold_opens_the_cards_menu_and_leaves_nothing_stuck(server: Server) -> None:  # noqa: F811
    """Android's order on a press and hold: the touch starts, its own drag starts (cancelling the
    touch), the context menu event comes where the finger is, and no dragend follows. The menu
    opens once, the row stays as it was, and the next tap on another row's ⋮ works."""
    from playwright.sync_api import expect, sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors, _ = _open(p, exe, server, server.sign_in(), 390, True, "dark")
        page.evaluate(ITEMS)
        row = page.locator("ul.items > li").first
        box = row.locator(".name").bounding_box()
        x, y = box["x"] + 20, box["y"] + box["height"] / 2
        cdp = page.context.new_cdp_session(page)
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        page.wait_for_timeout(700)
        row.evaluate(
            """(r) => { const dt = new DataTransfer();
                 const ev = new DragEvent("dragstart", {bubbles: true, cancelable: true, dataTransfer: dt});
                 r.dispatchEvent(ev);
               }"""
        )
        cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
        row.evaluate(
            """(r, xy) => r.dispatchEvent(new MouseEvent("contextmenu",
                {bubbles: true, cancelable: true, clientX: xy[0], clientY: xy[1]}))""",
            [x, y],
        )
        menu = page.locator(".ctxmenu.scan-menu")
        expect(menu).to_have_count(1)
        assert menu.locator(".head").inner_text() == "Sol Ring"
        assert _style(row, "opacity") == "1" and (row.get_attribute("class") or "") == ""
        # the phone's Back closes it
        page.go_back()
        expect(menu).to_have_count(0)
        assert page.url.endswith("/scan")
        # a later tap works: the other row's ⋮ opens its menu, a tap outside closes it
        page.locator("ul.items > li").nth(1).locator(".kebab").tap()
        expect(menu).to_be_visible()
        assert menu.locator(".head").inner_text() == "Cultivate"
        page.touchscreen.tap(10, 10)
        expect(menu).to_have_count(0)
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [390, 1280])
def test_the_saved_scans_menu_opens_renames_and_deletes(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import expect, sync_playwright

    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors, _ = _open(p, exe, server, server.sign_in(), width, False, "dark")
        page.evaluate(
            """async () => { const a = window.__scan.api;
                 await a('/scan/api/sessions', 'POST',
                   {name: 'Binder one', items: [{quantity: 1, name: 'Sol Ring', status: 'exact'}]});
                 await a('/scan/api/sessions', 'POST',
                   {name: 'Binder two', items: [{quantity: 2, name: 'Cultivate', status: 'exact'}]});
                 window.__scan.showTab('sessions'); }"""
        )
        rows = page.locator("ul.sessions > li")
        expect(rows).to_have_count(2)
        menu = page.locator(".ctxmenu.scan-menu")
        first = rows.filter(has_text="Binder one")
        first.locator("strong").click(button="right")
        expect(menu).to_be_visible()
        assert _menu_items(page) == ["Open", "Rename…", "Delete…"]
        # Rename… asks in a box; Escape leaves the name alone
        menu.get_by_role("menuitem", name="Rename…").click()
        box = page.locator(".scan-box[role=dialog]")
        expect(box).to_be_visible()
        assert page.evaluate("() => document.activeElement.id") == "rename-input"
        if width < 600:
            b = box.bounding_box()
            assert abs(b["y"] + b["height"] - 860) < 1, b
        page.keyboard.press("Escape")
        expect(box).to_have_count(0)
        first.locator(".kebab").click()
        menu.get_by_role("menuitem", name="Rename…").click()
        page.fill("#rename-input", "Trade binder")
        box.get_by_role("button", name="Rename").click()
        expect(box).to_have_count(0)
        expect(rows.filter(has_text="Trade binder")).to_have_count(1)
        expect(rows.filter(has_text="Binder one")).to_have_count(0)
        # Delete… keeps the yes/no bar; Cancel keeps the scan, Yes deletes it
        rows.filter(has_text="Binder two").locator(".kebab").click()
        menu.get_by_role("menuitem", name="Delete…").click()
        bar = page.locator(".scan .confirmbar")
        expect(bar).to_contain_text("Delete “Binder two” from the gateway?")
        bar.get_by_role("button", name="Cancel").click()
        expect(rows).to_have_count(2)
        rows.filter(has_text="Binder two").locator(".kebab").click()
        menu.get_by_role("menuitem", name="Delete…").click()
        page.locator(".scan .confirmbar").get_by_role("button", name="Yes").click()
        expect(rows).to_have_count(1)
        # the row's own delete button is a red outline, not solid
        db = rows.first.locator(".btn-outline-danger")
        assert db.get_attribute("aria-label") == "Delete Trade binder"
        assert _clear(_style(db, "backgroundColor"))
        # Open loads the scan onto the list tab
        rows.first.locator(".kebab").click()
        menu.get_by_role("menuitem", name="Open").click()
        expect(page.locator("#session-name")).to_have_value("Trade binder")
        expect(page.locator("ul.items > li")).to_have_count(1)
        assert errors == []
        browser.close()
