"""The deck editor's add-a-card flow in headless Chromium (R-131): typed names show rich
suggestions (picture, mana cost, type line) from the gateway's catalog, Enter adds the card and
keeps the box focused for the next one, the add form's controls share one row on desktop and
fold cleanly on a phone, and the printing picker opens as a dialog. Skipped without Chromium."""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest

from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAUAAAAHCAIAAAAbd2raAAAAFElEQVR4nGNgYGD4z8DAwMDAwMAAAAwAA/8BpYQAAAAASUVORK5CYII="
)


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    # the fixture cards' names are suggestable, with their summaries behind /scan/api/peek
    s.sf.catalog = sorted({c["name"] for c in s.sf.cards}) + [f"Filler Card {i}" for i in range(1200)]
    s.app.state.gateway.scan.names.set_names(s.sf.catalog)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _link(server: Server) -> str:
    import httpx

    sid = server.sign_in()
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid})
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/account").text).group(1)
    r = h.post(
        "/account",
        data={
            "csrf": csrf,
            "action": "link",
            "archidekt_login": "alice",
            "archidekt_password": "pw-alice",
            "accept_risk": "1",
        },
    )
    assert r.status_code == 303, r.text
    return sid


@pytest.mark.parametrize("width", [390, 1366])
def test_add_a_card_with_rich_suggestions(server: Server, width: int) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        assert page.locator("datalist").count() == 0
        # the add form: one row of equal-height controls on desktop; nothing wider than the page
        tops = page.evaluate(
            "() => [...document.querySelectorAll('form.addcard .field')].map(f => {"
            " const c = f.querySelector('input,select,button'); const r = c.getBoundingClientRect();"
            " return [Math.round(r.top), Math.round(r.height)]; })"
        )
        if width >= 1200:
            assert len({t[0] for t in tops}) == 1 and len({t[1] for t in tops}) == 1, tops
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        box = page.locator("#addname")
        box.click()
        box.press_sequentially("sol", delay=30)
        page.locator("[role=listbox]:visible").wait_for(timeout=3000)
        first = page.locator("[role=option]").first
        assert first.get_attribute("data-name") == "Sol Ring"
        # the row is decorated once the summaries arrive: type line and a mana disc
        page.locator("[role=option] .rich .ty").first.wait_for(timeout=3000)
        assert page.locator("[role=option] .rich .ty").first.inner_text() == "Artifact"
        assert page.locator("[role=option] .rich .pip").count() >= 1
        # the highlighted part and the rest of the name sit together, with no gap between them
        gap = first.evaluate(
            """li => { const m = li.querySelector('.rich .nm mark'), r = document.createRange();
                       r.selectNodeContents(m.nextSibling);
                       return r.getBoundingClientRect().left - m.getBoundingClientRect().right; }"""
        )
        assert abs(gap) < 1, gap
        box.press("ArrowDown")
        box.press("Enter")
        page.locator(".addstatus", has_text="Added 1 × Sol Ring").wait_for(timeout=3000)
        assert page.locator(".pendingbox .n").inner_text() == "1"
        assert page.evaluate("() => document.activeElement.id") == "addname"
        assert box.input_value() == ""
        # C-11 search bar: a count in front adds that many, a chip picks the category, and on wide
        # screens the highlighted card's printings float beside the list (a click adds that printing)
        page.locator(".targets .tchip", has_text="Land").first.click()
        assert page.evaluate("() => document.activeElement.id") == "addname"
        box.press_sequentially("3 sol r", delay=30)
        page.locator("[role=option] mark", has_text="Sol R").first.wait_for(timeout=3000)  # list for "sol r"
        assert page.locator("[role=option]").first.get_attribute("data-name") == "Sol Ring"
        if width >= 900:
            page.locator(".addprints:visible .print").first.wait_for(timeout=3000)
            page.locator(".addprints .print").first.click()
            status = page.locator(".addstatus").inner_text()
            assert "Added 3 × Sol Ring (" in status and "to Land" in status, status
        else:
            assert page.locator(".addprints:visible").count() == 0
            box.press("Enter")
            assert "Added 3 × Sol Ring to Land" in page.locator(".addstatus").inner_text()
        assert page.locator(".pendingbox .n").inner_text() == "1"  # the same card, one pending row
        assert page.locator(".addprints:visible").count() == 0 and box.input_value() == ""
        page.locator(".targets .tchip", has_text="Auto").click()
        # a card the deck does not hold yet becomes a new row with its picture and mana cost
        box.press_sequentially("oathsw", delay=30)
        page.locator("[role=option]").first.wait_for(timeout=3000)
        box.press("Enter")
        # L18: the row's mana cost may come after the row itself (a pick made before the list's
        # details arrived is filled in when they do): wait for it, not for a fixed time
        row = page.locator(".erow.new").first
        row.locator(".name .pip").first.wait_for(timeout=5000)
        assert "Oathsworn Giant" in row.inner_text()
        assert row.locator(".name .pip").count() >= 1
        # the printing picker is a dialog, closed by Escape
        page.locator(".erow:not(.new) details.rowmenu summary").first.click()
        page.locator(".erow:not(.new) .menu button", has_text="Change printing").first.click()
        picker = page.locator(".picker[role=dialog]")
        picker.wait_for(state="visible", timeout=3000)
        page.locator(".picker .print").first.wait_for(timeout=5000)
        assert page.evaluate("() => document.activeElement.className").startswith("close")
        page.keyboard.press("Escape")
        picker.wait_for(state="hidden", timeout=3000)
        assert page.locator(".picker:visible").count() == 0
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [390, 1366])
def test_a_card_picked_before_its_details_arrive_gets_them(server: Server, width: int) -> None:
    """L18's cause: Enter on a name whose picture and mana cost had not come back yet added a row
    without them, for good (the late answer only reached the closed list). The row now gets them
    when they arrive. The details are held here until after the pick, so this never races."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(
            re.compile(r"https://cards\.scryfall\.io/.*"),
            lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
        )
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        held: list = []
        page.route(re.compile(r".*/scan/api/peek\?.*"), lambda route: held.append(route))
        box = page.locator("#addname")
        box.click()
        box.press_sequentially("oathsw", delay=30)
        page.locator("[role=option]").first.wait_for(timeout=3000)
        box.press("Enter")
        row = page.locator(".erow.new").first
        row.wait_for(timeout=3000)
        assert "Oathsworn Giant" in row.inner_text() and row.locator(".name .pip").count() == 0
        page.unroute(re.compile(r".*/scan/api/peek\?.*"))
        for route in held:
            try:
                route.continue_()
            except Exception:  # the list aborted its own request when it closed
                pass
        row.locator(".name .pip").first.wait_for(timeout=5000)
        assert row.locator("img.thumb").count() == 1
        assert "Creature" in row.locator(".meta").inner_text()
        # one change in the list, and Undo takes the add back in one step (the late details are
        # not a change of their own)
        assert page.locator(".pendingbox .n").inner_text() == "1"
        page.locator("button.undo").click()
        assert page.locator(".erow.new").count() == 0
        assert errors == []
        browser.close()


# Overlapping boxes among an element's laid-out children (a child of 0 size, or hidden, is left out).
_OVERLAPS = """el => {
  const kids = [...el.children].map(c => [c, c.getBoundingClientRect()])
    .filter(([c, r]) => r.width > 0 && r.height > 0 && getComputedStyle(c).visibility !== 'hidden');
  const out = [];
  for (let i = 0; i < kids.length; i++) for (let j = i + 1; j < kids.length; j++) {
    const [a, ra] = kids[i], [b, rb] = kids[j];
    const w = Math.min(ra.right, rb.right) - Math.max(ra.left, rb.left);
    const h = Math.min(ra.bottom, rb.bottom) - Math.max(ra.top, rb.top);
    if (w > 1 && h > 1) out.push(a.className + ' / ' + b.className);
  }
  return out;
}"""


@pytest.mark.parametrize("width", [344, 1280])
def test_a_picture_that_fails_keeps_the_editor_rows_layout(server: Server, width: int) -> None:
    """A card picture that can't load (Scryfall down) in an editor row: the row keeps its layout
    (the stand-in takes the picture's own place and size, no broken glyph, nothing overlapping
    the quantity box), the same plain tile a row without a picture shows; the suggestion rows
    keep theirs too."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_path()
    if exe == "missing":
        pytest.skip("no Chromium available for Playwright")
    sid = _link(server)
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(viewport={"width": width, "height": 900 if width > 500 else 800})
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
        ctx.route(re.compile(r"https://cards\.scryfall\.io/.*"), lambda route: route.abort())
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        box = page.locator("#addname")
        box.click()
        box.press_sequentially("oathsw", delay=30)
        page.locator("[role=option] .rich .ty").first.wait_for(timeout=3000)
        page.wait_for_timeout(300)  # the suggestion pictures fail
        rich = page.locator("[role=option] .rich").first
        assert rich.evaluate(_OVERLAPS) == []
        assert page.locator("[role=option] img.img-broken:visible").count() == 0
        box.press("Enter")
        row = page.locator(".erow.new").first
        row.locator(".name .pip").first.wait_for(timeout=5000)
        row.locator(".img-fallback").wait_for(timeout=3000)
        # the stand-in is the row's one picture, in the picture's place and size
        assert row.locator("img:visible").count() == 0
        assert row.locator(".img-fallback").count() == 1
        tile = row.locator(".img-fallback").bounding_box()
        assert tile is not None and round(tile["width"]) == 40 and round(tile["height"]) == 56, tile
        # the row's picture was decorative (alt="", the name is in the row), so its stand-in is
        # hidden from screen readers rather than read out as "Image unavailable" (review N8)
        fallback = row.locator(".img-fallback")
        assert fallback.get_attribute("aria-hidden") == "true"
        assert fallback.get_attribute("role") is None and fallback.get_attribute("aria-label") is None
        assert row.evaluate(_OVERLAPS) == []
        # the quantity box is the element at its own centre (nothing drawn over it)
        assert row.locator("input").first.evaluate(
            "i => { i.scrollIntoView({block: 'center'}); const r = i.getBoundingClientRect();"
            " return document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2) === i; }"
        )
        # the deck's own rows, whose pictures failed too, keep theirs
        rows = page.locator(".erow:not(.new)")
        for i in range(min(rows.count(), 4)):
            assert rows.nth(i).evaluate(_OVERLAPS) == [], i
        assert page.locator(".erow img:visible").count() == 0
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        assert errors == []
        browser.close()
