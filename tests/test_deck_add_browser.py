"""The deck page's Add a card in headless Chromium (0.7.48: the deck editor folded into the deck
page). A typed name opens a preview before anything is saved (picture, type line, the copies
already in the deck, Copies, Category, Printing, Finish); Add to deck saves it with the chosen
category and the card shows in place. More ways to add holds Paste a list (checked first, a
category per card, the lines that match nothing) and Add a package; a category's menu has Add a
card here; Move to in a card's menu can make a new category. Skipped without Chromium."""

from __future__ import annotations

import json
import re

import pytest

from .test_editor_browser import PNG, _link, server  # noqa: F401  (the fixture)
from .test_scan_browser import _chromium_path
from .test_suggest_browser import Server

pytest.importorskip("playwright")


def _open(p, server: Server, sid: str, width: int, scheme: str = "light"):  # noqa: ANN001, F811
    exe = EXE[0]
    browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
    ctx = browser.new_context(
        viewport={"width": width, "height": 900 if width > 500 else 800},
        color_scheme=scheme,
        has_touch=width < 500,
    )
    ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])
    ctx.route(
        re.compile(r"https://cards\.scryfall\.io/.*"),
        lambda route: route.fulfill(status=200, content_type="image/png", body=PNG),
    )
    page = ctx.new_page()
    errors: list[str] = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    edits: list[dict] = []
    page.on(
        "request",
        lambda r: (
            edits.append(json.loads(r.post_data or "{}"))
            if r.method == "POST" and r.url.endswith("/api/v1/decks/42/edit")
            else None
        ),
    )
    return browser, page, errors, edits


EXE: list[str] = []


def _skip_without_chromium() -> None:
    if not EXE:
        EXE.append(_chromium_path())
    if EXE[0] == "missing":
        pytest.skip("no Chromium available for Playwright")


@pytest.mark.parametrize("width", [390, 1366])
def test_a_typed_card_opens_a_preview_and_adds_to_the_chosen_category(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, width)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        # no way left into the old editor, and the toolbar has no bare + button
        assert page.locator("a[href$='/decks/42/edit']").count() == 0
        assert page.locator("form.quick button[type=submit], form.quick button:not([type])").count() == 0
        box = page.locator("#quick")
        box.click()
        box.press_sequentially("swam", delay=30)
        page.locator("[role=option]").first.wait_for(timeout=3000)
        box.press("Enter")
        preview = page.locator(".mtgbox.addbox-preview")
        preview.wait_for(timeout=5000)
        assert edits == []  # nothing is saved before the member says Add
        assert "Swamp" in preview.locator("h2, .title, [id]").first.inner_text() or "Swamp" in (
            preview.inner_text()
        )
        assert preview.locator(".appic img").count() == 1
        # the picture, or its name tile when it fails, keeps the card's shape (never a thin strip)
        pic = preview.locator(".appic").bounding_box()
        assert pic["height"] >= pic["width"] * 1.3, pic
        assert preview.locator(".aphead .ty").inner_text().startswith("Basic Land")
        # Category: Auto by type first, the deck's categories, and New category…
        cat = page.locator("#ap-cat")
        options = cat.evaluate("s => [...s.options].map(o => o.text)")
        assert options[0] == "Auto: Land", options
        assert "New category…" in options
        assert "Enchantment" in options, options
        cat.select_option("Enchantment")
        preview.locator("[aria-label^='One more']").click()
        assert preview.locator("#ap-qty").inner_text() == "2"
        # every control in the preview is at least 44 px tall (a finger's size)
        small = preview.evaluate(
            """b => [...b.querySelectorAll('button, select, .msel-btn')].filter(x => x.offsetParent)
                   .map(x => [x.textContent.trim() || x.getAttribute('aria-label'),
                              x.getBoundingClientRect().height])
                   .filter(x => x[1] > 2 && x[1] < 43.5)"""
        )
        assert small == [], small
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        preview.locator("button", has_text="Add to deck").click()
        page.wait_for_function("() => !document.querySelector('.mtgbox-scrim')", timeout=3000)
        page.locator(".toast, [role=status]", has_text="Added 2 × Swamp to Enchantment").first.wait_for(
            timeout=8000
        )
        assert edits[-1]["changes"] == [
            {"action": "add", "card_name": "Swamp", "quantity": 2, "category": "Enchantment"}
        ], edits
        assert "archidekt_backup" not in edits[-1]
        card = page.locator(".stack[data-group='Enchantment'] [data-card='Swamp']")
        card.first.wait_for(timeout=5000)
        assert box.input_value() == ""
        assert errors == []
        browser.close()


def test_a_name_no_card_has_says_so_and_saves_nothing(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        box = page.locator("#quick")
        box.fill("Zzyzx Notacard")
        box.press("Enter")
        page.locator(".toast", has_text="No card is named").first.wait_for(timeout=5000)
        assert page.locator(".toast a", has_text="Search cards").count() == 1
        assert page.locator(".mtgbox.addbox-preview").count() == 0 and edits == []
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [390, 1366])
def test_paste_a_list_is_checked_then_added_with_a_category_each(server: Server, width: int) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, width)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        # More ways to add: the toolbar's button on a wide screen, the ⋮ on a phone
        if width < 600:
            page.locator(".toolbar .toolmore").click()
        else:
            page.locator(".toolbar .addways").click()
        menu = page.locator("[role=menu]:visible")
        menu.wait_for(timeout=3000)
        labels = menu.locator("[role=menuitem]").all_inner_texts()
        assert any("Paste a list" in t for t in labels) and any("Add a package" in t for t in labels), labels
        menu.locator("[role=menuitem]", has_text="Paste a list").click()
        box = page.locator(".mtgbox.batchbox")
        box.wait_for(timeout=3000)
        box.locator("textarea").fill("2 Sol Ring\n1 Zzyzx Notacard\nSideboard\n")
        if width > 600:
            # what was typed survives a reload: Paste a list opens with it again
            page.reload(wait_until="networkidle")
            page.locator(".toolbar .addways").click()
            page.locator("[role=menu]:visible [role=menuitem]", has_text="Paste a list").click()
            box.wait_for(timeout=3000)
            assert box.locator("textarea").input_value().startswith("2 Sol Ring"), "paste lost on reload"
            # a list put back is checked at once (no second press)
        else:
            box.locator("button", has_text="Check the list").click()
        box.locator(".batchlist li").first.wait_for(timeout=8000)
        rows = box.locator(".batchlist li:not(.miss)")
        assert rows.count() == 1 and "Sol Ring" in rows.first.inner_text()
        assert box.locator(".batchlist li.miss").count() == 1
        assert "no card has this name" in box.locator(".batchlist li.miss").inner_text()
        assert edits == []
        box.locator("#batch-cat-0").select_option("Artifact")
        box.locator("button", has_text="Add 2 cards").click()
        page.wait_for_function("() => !document.querySelector('.mtgbox-scrim')", timeout=3000)
        page.locator(".toast, [role=status]", has_text="Added 2 cards").first.wait_for(timeout=8000)
        sent = edits[-1]["changes"]
        assert len(sent) == 1 and sent[0]["card_name"] == "Sol Ring" and sent[0]["quantity"] == 2, sent
        assert sent[0]["category"] == "Artifact", sent
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        assert errors == []
        browser.close()


def test_add_a_card_here_starts_the_preview_in_that_category(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=stacks", wait_until="networkidle")
        page.locator(".stack[data-group='Artifact'] .stackhead .kebab").click()
        page.locator("[role=menuitem]:visible", has_text="Add a card here").click()
        chip = page.locator("form.quick .addto")
        chip.wait_for(timeout=3000)
        assert "Artifact" in chip.inner_text()
        assert page.evaluate("() => document.activeElement.id") == "quick"
        box = page.locator("#quick")
        box.press_sequentially("sol r", delay=30)
        page.locator("[role=option]").first.wait_for(timeout=3000)
        box.press("Enter")
        page.locator(".mtgbox.addbox-preview").wait_for(timeout=5000)
        assert page.locator("#ap-cat").input_value() == "Artifact"
        page.keyboard.press("Escape")
        page.wait_for_function("() => !document.querySelector('.mtgbox-scrim')", timeout=3000)
        chip.click()  # the chip's × stops adding there
        assert page.locator("form.quick .addto").count() == 0
        assert edits == [] and errors == []
        browser.close()


def test_move_to_can_make_a_new_category(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        card = page.locator(".stack [data-card='Cultivate']").first
        card.click(button="right")
        page.locator("[role=menu] button", has_text="Move to").first.click()
        page.locator("[role=menu] button", has_text="New category…").click()
        box = page.locator(".mtgbox-scrim .mtgbox[role=dialog]")
        box.wait_for(timeout=3000)
        box.locator("input").fill("Value")
        box.locator("button", has_text="Move here").click()
        page.locator(".stack[data-group='Value'] [data-card='Cultivate']").wait_for(timeout=8000)
        assert edits[-1]["changes"][0]["action"] == "set_category"
        assert edits[-1]["changes"][0]["category"] == "Value", edits
        assert errors == []
        browser.close()


def test_add_to_deck_saves_once_and_a_typed_new_category_counts(server: Server) -> None:  # noqa: F811
    """0.7.48 audit: Enter pressed again while the preview fades out never adds the card twice,
    and a name typed in New category… (not yet confirmed with ✓) is the category the card goes
    to; with no name typed, Add to deck says so and sends nothing."""
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        box = page.locator("#quick")
        box.fill("Swamp")
        box.press("Enter")
        preview = page.locator(".mtgbox.addbox-preview")
        preview.wait_for(timeout=5000)
        page.locator("#ap-cat").select_option("New category…")
        name = preview.locator(".newcat input")
        name.wait_for(timeout=3000)
        add = preview.locator("button", has_text="Add to deck")
        add.click()
        preview.locator(".note, [role=alert]", has_text="Type a name for the new category").first.wait_for(
            timeout=3000
        )
        assert edits == []
        name.fill("Mana")
        # three Enters on Add to deck as fast as a held key: one save
        add.focus()
        page.keyboard.press("Enter")
        page.keyboard.press("Enter")
        page.keyboard.press("Enter")
        page.locator(".toast, [role=status]", has_text="Added Swamp").first.wait_for(timeout=8000)
        page.locator(".stack[data-group='Mana'] [data-card='Swamp']").first.wait_for(timeout=5000)
        adds = [e for e in edits if any(c.get("action") == "add" for c in e.get("changes", []))]
        assert len(adds) == 1, edits
        assert adds[0]["changes"][0]["category"] == "Mana", adds
        assert errors == []
        browser.close()


def test_paste_a_list_says_which_lines_it_left_out(server: Server) -> None:  # noqa: F811
    """0.7.48 audit: a line longer than any card line, or past the 250 the gateway checks at once,
    is named in the box's note, never dropped quietly."""
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        page.locator(".toolbar .addways").click()
        page.locator("[role=menu]:visible [role=menuitem]", has_text="Paste a list").click()
        box = page.locator(".mtgbox.batchbox")
        box.wait_for(timeout=3000)
        box.locator("textarea").fill("2 Sol Ring\n1 " + "x" * 320 + "\n")
        box.locator("button", has_text="Check the list").click()
        box.locator(".batchlist li").first.wait_for(timeout=8000)
        box.get_by_text("Left out: 1 line too long to be a card line.").wait_for(timeout=3000)
        assert edits == [] and errors == []
        browser.close()


@pytest.mark.parametrize("width", [390, 1366])
def test_the_add_row_keeps_its_box_wide_and_cancel_leaves_it_empty(server: Server, width: int) -> None:  # noqa: F811
    """0.7.48 polish: on a wide screen the name box and More ways to add share one row and the
    box takes the room; on a phone the Add a card here chip never squeezes the box under 8rem.
    Cancel on the preview empties the box, so no suggestion list reopens over the toolbar."""
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, width)
        page.goto(f"{server.base}/decks/42?view=stacks", wait_until="networkidle")
        quick = page.locator("#quick")
        if width > 600:
            ways = page.locator(".toolbar .addways").bounding_box()
            box = quick.bounding_box()
            assert abs(ways["y"] - box["y"]) < 2, (ways, box)  # one row
            assert box["width"] >= 300, box
        page.locator(".stack[data-group='Artifact'] .stackhead .kebab").click()
        page.locator("[role=menuitem]:visible", has_text="Add a card here").click()
        page.locator("form.quick .addto").wait_for(timeout=3000)
        assert quick.bounding_box()["width"] >= 8 * 14 - 1, quick.bounding_box()
        assert page.evaluate("() => document.documentElement.scrollWidth") <= width
        quick.fill("Swamp")
        quick.press("Enter")
        page.locator(".mtgbox.addbox-preview").wait_for(timeout=5000)
        page.locator(".mtgbox.addbox-preview button", has_text="Cancel").click()
        page.wait_for_function("() => !document.querySelector('.mtgbox-scrim')", timeout=3000)
        assert quick.input_value() == ""
        assert page.evaluate("() => document.activeElement.id") == "quick"
        assert page.locator(".suggest [role=listbox]:visible, .suggest ul:visible").count() == 0
        assert edits == [] and errors == []
        browser.close()


@pytest.mark.parametrize("how", ["filter", "add-link"])
def test_a_deck_page_with_no_cards_shown_still_adds(server: Server, how: str) -> None:  # noqa: F811
    """0.7.48 review: an empty deck (or a filter that matches nothing) still draws the cards'
    container, so Add to the deck opens its preview (it did a plain search for the name) and the
    Scan page's and old editor's links (?add=, ?scan_session=) still open their boxes."""
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        extra = "&add=Swamp" if how == "add-link" else ""
        page.goto(f"{server.base}/decks/42?q=zzzqqq{extra}", wait_until="networkidle")
        assert page.locator("#cards.deckview .deck-empty").inner_text() == "No cards match the filter."
        if how == "filter":
            box = page.locator("#quick")
            box.fill("Swamp")
            box.press("Enter")
        page.locator(".mtgbox.addbox-preview").wait_for(timeout=5000)
        assert "/cards" not in page.url and edits == []
        assert errors == []
        browser.close()


def test_undo_after_a_list_takes_each_card_back_once(server: Server) -> None:  # noqa: F811
    """0.7.48 review: a list line over 99 copies goes as two changes of one name (as a scan's foil
    and normal copies do); Undo removes the card once, where it sent two removes the save refused."""
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        assert page.locator("#cards [data-card='Swamp']").count() == 0
        page.locator(".toolbar .addways").click()
        page.locator("[role=menu]:visible [role=menuitem]", has_text="Paste a list").click()
        box = page.locator(".mtgbox.batchbox")
        box.wait_for(timeout=3000)
        box.locator("textarea").fill("150 Swamp\n")
        box.locator("button", has_text="Check the list").click()
        box.locator(".batchlist li").first.wait_for(timeout=8000)
        box.locator("button", has_text="Add 150 cards").click()
        toast = page.locator(".toast", has_text="Added 150 cards").first
        toast.wait_for(timeout=8000)
        assert [len(e["changes"]) for e in edits] == [2], edits
        toast.locator("button", has_text="Undo").click()
        # the save takes the one remove; 150 cards out is big, so it asks first
        ask = page.locator(".mtgbox[role=dialog]:not([hidden])", has_text="Remove 150 cards?")
        ask.wait_for(timeout=8000)
        assert edits[-1]["changes"] == [{"action": "remove", "card_name": "Swamp"}], edits[-1]
        ask.locator("button", has_text="Remove 150 cards").click()
        page.wait_for_function("() => !document.querySelector('#cards [data-card=\"Swamp\"]')", timeout=8000)
        assert errors == []
        browser.close()
