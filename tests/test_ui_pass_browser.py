"""The 0.7.x UI pass, in headless Chromium: what it found on the member pages and the fixes, each
checked where it showed. Phones at 344 px (a folded Fold's cover screen) at 200% text, a Fold's
inner screen, touch targets, the keyboard's order on the deck page, and the focus ring on
thumbnail buttons. Skipped without a Chromium build."""

from __future__ import annotations

import re

import httpx
import pytest

from .test_editor_browser import PNG
from .test_overflow_sweep_browser import MEASURE, _seed, server  # noqa: F401
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200

pytest.importorskip("playwright")


def _exe() -> str:
    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    return exe


def _context(
    browser, srv: Server, sid: str, width: int, height: int = 900, *, touch: bool, scheme: str = "light"
):
    kw = {"has_touch": True, "is_mobile": True} if touch else {}
    ctx = browser.new_context(viewport={"width": width, "height": height}, color_scheme=scheme, **kw)
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": srv.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    return ctx


# Pages that ran over the window's edge at 200% text on a 344 px phone or a Fold's inner screen
# before the pass: the editor's quantity boxes, the Cards page's search row and filter grid, the
# Scan page's tabs and camera controls, and a long unbroken badge on the admin's activity page.
ZOOM_PAGES = ["/decks/42/edit", "/cards", "/cards?q=Sol", "/scan", "/admin/activity", "/decks/42"]


@pytest.mark.parametrize("width", [344, 673])
def test_pages_hold_together_at_200_percent_on_the_narrowest_screens(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    exe, (sid, _pending) = _exe(), _seed(server)
    problems: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _context(browser, server, sid, width, 882, touch=True)
        page = ctx.new_page()
        for path in ZOOM_PAGES:
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            page.evaluate(ROOT_200)
            page.wait_for_timeout(80)
            # the Goldfish hand scrolls sideways on purpose; nothing else may
            found = [f for f in page.evaluate(MEASURE) if "gf-card" not in f]
            problems += [f"{path} @{width}px 200%: {f}" for f in found]
        browser.close()
    assert problems == [], "\n".join(problems)


def test_watchlist_inputs_are_styled_like_every_other_input(server: Server) -> None:  # noqa: F811
    """The Watchlist's boxes had no type attribute, so the theme's input rule skipped them and they
    showed as the browser's bare boxes (half the height of the Finish select beside them)."""
    from playwright.sync_api import sync_playwright

    exe, (sid, _pending) = _exe(), _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _context(browser, server, sid, 344, touch=True)
        page = ctx.new_page()
        page.goto(f"{server.base}/watchlist", wait_until="networkidle")
        heights = page.evaluate(
            "() => [...document.querySelectorAll("
            "'.watch-page input:not([type=hidden]), .watch-page select:not([aria-hidden=true]),"
            " .watch-page .msel-btn')].filter(e => e.getClientRects().length && !e.closest('[hidden]'))"
            ".map(e => [e.id || e.className, Math.round(e.getBoundingClientRect().height),"
            " getComputedStyle(e).borderRadius])"
        )
        browser.close()
    assert len(heights) >= 5
    assert {h for _, h, _ in heights} == {heights[0][1]}, heights  # all one height
    assert heights[0][1] >= 36, heights
    assert all(r != "0px" for _, _, r in heights), heights


def test_a_plain_fold_out_keeps_its_marker_on_a_touch_screen(server: Server) -> None:  # noqa: F811
    """The admin's "Details and actions" (a details element with no class of its own) lost its
    triangle on touch screens: the rule that makes every summary 40 px tall laid it out as a flex
    box, which drops the native marker. It is a 40 px list item now, on every such fold-out."""
    from playwright.sync_api import sync_playwright

    exe, (sid, _pending) = _exe(), _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _context(browser, server, sid, 344, touch=True)
        page = ctx.new_page()
        rows = []
        for path in ("/admin/users", "/account"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            rows += page.evaluate(
                "() => [...document.querySelectorAll('details:not(.dd) > summary')]"
                ".filter(s => s.innerText.trim() && !getComputedStyle(s, '::before').content.includes('▸'))"
                ".map(s => [s.innerText.trim().slice(0, 24), getComputedStyle(s).display,"
                " Math.round(s.getBoundingClientRect().height)])"
            )
        browser.close()
    assert any(t.startswith("Details and actions") for t, _, _ in rows), rows
    for text, display, height in rows:
        assert display == "list-item", (text, display)
        assert height >= 40, (text, height)


def test_thumbnail_buttons_show_a_focus_ring(server: Server) -> None:  # noqa: F811
    """A card thumbnail's button (the editor and the collection list) named a colour that does not
    exist for its focus ring, so a keyboard user saw no focus on it at all."""
    from playwright.sync_api import sync_playwright

    exe, (sid, _pending) = _exe(), _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _context(browser, server, sid, 1366, touch=False)
        page = ctx.new_page()
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        page.focus("button.thumbbtn")
        page.keyboard.press("Shift+Tab")
        page.keyboard.press("Tab")  # a keyboard focus, so :focus-visible applies
        ring = page.evaluate(
            "() => { const e = document.activeElement; const cs = getComputedStyle(e);"
            " return [e.className, cs.outlineStyle, cs.outlineWidth, cs.outlineColor]; }"
        )
        browser.close()
    assert ring[0] == "thumbbtn", ring
    assert ring[1] == "solid" and ring[2] == "2px", ring
    assert ring[3] not in ("rgba(0, 0, 0, 0)", "transparent"), ring


def test_tab_reaches_the_deck_owner_before_the_deck_buttons(server: Server) -> None:  # noqa: F811
    """The owner's name and avatar sit at the top of the deck page's banner (on a phone above the
    deck's name), but came last in the page's order, so Tab jumped back up to them after the
    buttons. They are first in the order now, on phones and desktops alike."""
    from playwright.sync_api import sync_playwright

    exe, (sid, _pending) = _exe(), _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for width, touch in ((344, True), (1366, False)):
            ctx = _context(browser, server, sid, width, touch=touch)
            page = ctx.new_page()
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            page.focus("main")
            order: list[str] = []
            for _ in range(40):
                page.keyboard.press("Tab")
                order.append(
                    page.evaluate("() => document.activeElement.className || document.activeElement.tagName")
                )
                focused = page.evaluate("() => document.activeElement.innerText")
                if "owner" in order[-1] or "Edit deck" in focused:
                    break
            owner_first = next((i for i, c in enumerate(order) if "owner" in c), None)
            assert owner_first is not None, order
            edit_at = page.evaluate("() => document.activeElement.innerText")
            assert "Edit deck" not in edit_at or owner_first < len(order) - 1, order
            # the owner link lies above the deck's buttons, as the order now says
            tops = page.evaluate(
                "() => [document.querySelector('.banner .owner').getBoundingClientRect().top,"
                " document.querySelector('.banner .controls').getBoundingClientRect().top]"
            )
            assert tops[0] < tops[1], tops
            ctx.close()
        browser.close()


TOUCH_TARGETS = {
    "/": ".topbar .icon-btn, a.brand, footer.site .links a, main .btn, main button,"
    " main input:not([type=hidden])",
    "/reports": ".rp-t .title .t a, .rp-t a.act",
    "/collection?view=list": ".coll-totals a.ext",
    "/guide": ".guide h2 .anchor",
    "/decks/42": ".curveaxis .mv, .rowbtn, .banner .social .soc",
    "/decks/42?view=table": "ul.rows .row",
    "/decks/42/edit": ".addbox form.addcard .go .foil",
    "/history?deck_id=42": ".hrow a.kind, .hrow a.name, .hrow details > summary, main .msel-btn,"
    " main input#h-q",
    "/history/reports/rep_touch": ".report .crumbs a, .report details summary",
    "/account": "details:not(.dd) > summary",
}


def test_small_links_and_buttons_are_44px_targets_on_a_touch_screen(server: Server) -> None:  # noqa: F811
    """The report list's deck names, the collection's Open on Archidekt link, the Guide's heading
    anchors, the mana curve's axis buttons, the stats panel's card-picking buttons and the editor's Foil
    tick were 17 to 28 px tall for a finger, and the theme's older touch rule made the top bar's
    buttons, the brand, footer links, fold-outs, list links, crumbs, the banner's social buttons and
    the table view's rows 40 px, and every standard button, input and select was 39 px (the theme's
    control height): the touch rule makes them all 44 px now."""
    import time

    from playwright.sync_api import sync_playwright

    from .test_history_page import store_report

    exe, (sid, _pending) = _exe(), _seed(server)
    store_report(server.db, "user-1", "rep_touch", taken_at=int(time.time()) - 100)
    short: list[str] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = _context(browser, server, sid, 412, 915, touch=True)
        page = ctx.new_page()
        for path, sel in TOUCH_TARGETS.items():
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            if path == "/decks/42":
                if page.locator("#stats").count():
                    page.locator("#stats").scroll_into_view_if_needed()
            found = page.evaluate(
                "(sel) => [...document.querySelectorAll(sel)].filter(e => e.getBoundingClientRect().width)"
                ".map(e => [e.className, Math.round(e.getBoundingClientRect().height)])",
                sel,
            )
            assert found, (path, sel)
            short += [f"{path} {c}: {h}px" for c, h in found if h < 44]
            assert page.evaluate("() => document.documentElement.scrollWidth <= innerWidth"), path
        browser.close()
    assert short == [], "\n".join(short)


def test_member_text_no_longer_says_verify(server: Server) -> None:  # noqa: F811
    """The proposal page's apply note said the gateway "verifies the result"; member text never
    says verify. It says the deck is read back."""
    sid, pending = _seed(server)
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    body = h.get(f"/proposals/{pending}").text
    assert "verif" not in body.lower(), re.findall(r".{40}verif.{40}", body, re.I)
    assert "reads the result back from Archidekt" in body


def test_the_commander_question_fits_a_phone_and_its_buttons_are_finger_sized(server: Server) -> None:  # noqa: F811
    """Dragging a card into the Commander stack asks "Save anyway?" in the deck page's own dialog
    strip. On a 344 px touch screen, at 100 % and 200 % text, the strip stays inside the window above
    the tab bar, nothing in it is clipped, and its buttons are 44 px targets."""
    from playwright.sync_api import sync_playwright

    from .test_text_zoom_browser import ROOT_200

    exe, (sid, _pending) = _exe(), _seed(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        for zoom in (False, True):
            ctx = _context(browser, server, sid, 344, 800, touch=True)
            page = ctx.new_page()
            page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
            if zoom:
                page.evaluate(ROOT_200)
                page.wait_for_timeout(100)
            page.drag_and_drop(
                ".deckview .row[data-card='Sol Ring'][data-zone='side']",
                ".stack[data-group='Commander'] .stackhead",
            )
            dialog = page.locator(".deck-toast[role='alertdialog']")
            dialog.wait_for(timeout=5000)
            assert "Save anyway?" in dialog.inner_text()
            m = page.evaluate(
                "() => { const d = document.querySelector(\".deck-toast[role='alertdialog']\");"
                " const r = d.getBoundingClientRect(); const bar = document.querySelector('.tabbar');"
                " return {left: r.left, right: r.right, bottom: r.bottom, top: r.top, w: innerWidth,"
                " bar: bar ? bar.getBoundingClientRect().top : innerHeight,"
                " clipped: d.scrollWidth > d.clientWidth || d.scrollHeight > d.clientHeight,"
                " buttons: [...d.querySelectorAll('button')].map(b => [b.innerText.trim() || 'close',"
                " Math.round(b.getBoundingClientRect().height)])} }"
            )
            assert m["left"] >= 0 and m["right"] <= m["w"] and m["top"] >= 0, m
            assert m["bottom"] <= m["bar"] + 1, m  # above the bottom tab bar
            assert not m["clipped"], m
            assert all(h >= 44 for _, h in m["buttons"]), m
            assert m["buttons"][0][0] == "Save anyway", m
            ctx.close()
        browser.close()
