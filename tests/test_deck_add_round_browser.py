"""The deck page's add flow after the 0.7.48 loop round's review, in headless Chromium: the
preview's Printing opens its picker on top of the box, a card added to a Sideboard goes outside the
deck, a list over 40 cards stops where a save fails and keeps what was not added, an add that makes
a row of its own offers no Undo that could remove the old row, the note names a category only when
the card went into it, More ways to add loads nothing it does not open, an empty page's text goes
with the first card, Cancel on Put them in's New category keeps each card's own category, Ctrl+Z
does nothing behind an open box, and the phone's add field and banner buttons say what they are.
Skipped without Chromium."""

from __future__ import annotations

import re

from .fake_printings import sol_ring_printings
from .test_deck_add_browser import _link, _open, _skip_without_chromium, server  # noqa: F401
from .test_suggest_browser import Server


def _preview(page, name: str):  # noqa: ANN001, ANN202
    box = page.locator("#quick")
    box.fill(name)
    box.press("Enter")
    preview = page.locator(".mtgbox.addbox-preview")
    preview.wait_for(timeout=5000)
    return preview


def _paste(page, text: str):  # noqa: ANN001, ANN202
    page.locator(".toolbar .addways").click()
    page.locator("[role=menu]:visible [role=menuitem]", has_text="Paste a list").click()
    box = page.locator(".mtgbox.batchbox")
    box.wait_for(timeout=3000)
    box.locator("textarea").fill(text)
    box.locator("button", has_text="Check the list").click()
    box.locator(".batchlist li").first.wait_for(timeout=8000)
    return box


def test_the_preview_printing_opens_its_picker_on_top(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    server.sf.extra_cards = sol_ring_printings(0)
    sid = _link(server)
    with sync_playwright() as p:
        for width in (390, 1366):
            browser, page, errors, edits = _open(p, server, sid, width)
            page.goto(f"{server.base}/decks/42", wait_until="networkidle")
            preview = _preview(page, "Sol Ring")
            preview.locator("#ap-print").click()
            page.locator(".printpick.open .pptile").first.wait_for(timeout=5000)
            # the picker is what a press lands on, not the box under it
            top = page.evaluate(
                """() => {
                const r = document.querySelector('.printpick.open .pptile').getBoundingClientRect();
                const e = document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2);
                return !!(e && e.closest('.printpick')); }"""
            )
            assert top, width
            # Escape closes the picker only; the preview stays, ready to add
            page.keyboard.press("Escape")
            page.wait_for_function("() => !document.querySelector('.printpick.open')", timeout=3000)
            assert preview.is_visible() and edits == []
            assert errors == []
            browser.close()


def test_an_add_to_a_sideboard_goes_outside_the_deck(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        preview = _preview(page, "Swamp")
        page.locator("#ap-cat").select_option("New category…")
        preview.locator(".newcat input").fill("Sideboard")
        preview.locator("button", has_text="Add to deck").click()
        page.locator(".toast", has_text="Added Swamp").first.wait_for(timeout=8000)
        assert edits[-1]["changes"][0]["zone"] == "side", edits
        assert errors == []
        browser.close()


def test_a_list_over_40_stops_where_a_save_fails_and_keeps_the_rest(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        # 41 lines of 99: one name the gateway sends as 41 changes (99 at most each), so two saves
        box = _paste(page, "99 Swamp\n" * 41)
        failed = []

        def refuse(route) -> None:  # noqa: ANN001
            failed.append(1)
            route.fulfill(status=502, content_type="text/plain", body="bad gateway")

        page.route(re.compile(r".*/api/v1/decks/42/edit$"), refuse)
        box.locator("button", has_text="Add 4059 cards").click()
        page.locator(".toast", has_text="Add not saved").first.wait_for(timeout=8000)
        page.wait_for_timeout(1500)
        assert len(failed) == 1, failed  # the second part was never sent
        toast = page.locator(".toast", has_text="Add not saved").first.inner_text()
        assert "4059 cards of the list weren't added; Paste a list holds them." in toast, toast
        assert page.locator(".toast", has_text=re.compile(r"Added \d")).count() == 0
        kept = page.evaluate("() => sessionStorage.getItem('mtg-paste-42')")
        assert kept == "99 Swamp\n" * 41, kept
        assert errors == []
        browser.close()


def test_an_add_in_another_printing_offers_no_undo_that_could_take_the_old_row(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    server.sf.extra_cards = sol_ring_printings(0)
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        assert page.locator("#cards [data-card='Sol Ring'][data-zone='main']").count() == 1
        preview = _preview(page, "Sol Ring")
        assert "the copies keep its category and finish" in preview.locator(".aphave").inner_text()
        preview.locator("#ap-print").click()
        tile = page.locator(".printpick.open .pptile", has_text="SLD #1074")
        tile.wait_for(timeout=5000)
        tile.click()
        page.locator(".printpick .pppreview .ppuse").click()
        page.wait_for_function("() => !document.querySelector('.printpick.open')", timeout=3000)
        preview.locator("button", has_text="Add to deck").click()
        toast = page.locator(".toast", has_text="Added Sol Ring").first
        toast.wait_for(timeout=8000)
        assert edits[-1]["changes"][0].get("set_code"), edits
        assert toast.locator("button", has_text="Undo").count() == 0
        assert errors == []
        browser.close()


def test_an_add_that_grows_a_row_names_no_category_it_did_not_use(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        _preview(page, "Sol Ring")
        page.locator("#ap-cat").select_option("Creature")
        page.locator(".mtgbox.addbox-preview button", has_text="Add to deck").click()
        toast = page.locator(".toast", has_text="Added Sol Ring").first
        toast.wait_for(timeout=8000)
        assert "Added Sol Ring. Snapshot kept under History." in toast.inner_text(), toast.inner_text()
        assert toast.locator("button", has_text="Undo").count() == 1
        assert errors == []
        browser.close()


def test_more_ways_to_add_loads_no_package_list_for_its_menu(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        asked: list[str] = []
        page.on("request", lambda r: asked.append(r.url) if "package" in r.url else None)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        asked.clear()
        page.locator(".toolbar .addways").click()
        page.locator("[role=menu]:visible").wait_for(timeout=3000)
        page.wait_for_timeout(800)
        assert asked == [], asked
        assert errors == []
        browser.close()


def test_the_first_card_on_an_empty_page_replaces_its_text(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42?q=zzzqqq", wait_until="networkidle")
        preview = _preview(page, "Swamp")
        preview.locator("button", has_text="Add to deck").click()
        page.locator(".toast", has_text="Added Swamp").first.wait_for(timeout=8000)
        page.locator("#cards .stack [data-card='Swamp']").first.wait_for(timeout=5000)
        assert page.locator("#cards .deck-empty").count() == 0
        # the new stack comes before "+ Add category", which stays last
        assert page.evaluate(
            "() => document.querySelector('#cards').lastElementChild.classList.contains('addcat')"
        )
        assert errors == []
        browser.close()


def test_cancel_on_put_them_in_keeps_each_cards_category(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        box = _paste(page, "1 Swamp\n2 Sol Ring\n")
        box.locator("#batch-cat-0").select_option("Artifact")
        before = box.locator("#batch-cat-1").input_value()
        box.locator("#batch-all").select_option("New category…")
        box.locator(".newcat input").wait_for(timeout=3000)
        box.locator(".newcat button[aria-label='Cancel']").click()
        assert box.locator("#batch-cat-0").input_value() == "Artifact"
        assert box.locator("#batch-cat-1").input_value() == before
        assert edits == [] and errors == []
        browser.close()


def test_ctrl_z_does_nothing_behind_an_open_box(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1366)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        preview = _preview(page, "Swamp")
        preview.locator("button", has_text="Add to deck").click()
        page.locator(".toast", has_text="Added Swamp").first.wait_for(timeout=8000)
        n = len(edits)
        _preview(page, "Sol Ring")
        # the focus rests on the box's Add to deck (a button, where the page's keys otherwise work)
        page.wait_for_function("() => document.activeElement && document.activeElement.closest('.mtgbox')")
        page.keyboard.press("Control+z")
        page.wait_for_timeout(800)
        assert len(edits) == n, edits[n:]
        assert errors == []
        browser.close()


def test_the_phone_add_field_and_banner_buttons_say_what_they_are(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 390)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        label = page.locator("label[for='quick']")
        assert label.is_visible() and label.bounding_box()["width"] > 50
        for word in ("Like", "Bookmark", "Comments", "Proposals"):
            assert (
                page.locator(".banner .social .soc", has_text=word).first.locator("span").first.is_visible()
            ), word
        # View and ⋮ line up with the field, not with its label
        field = page.locator("#quick").bounding_box()
        view = page.locator(".toolbar .viewbtn").bounding_box()
        assert abs((field["y"] + field["height"]) - (view["y"] + view["height"])) <= 2, (field, view)
        assert errors == []
        browser.close()


def test_the_grid_toolbar_keeps_the_add_row_first_on_a_laptop(server: Server) -> None:  # noqa: F811
    from playwright.sync_api import sync_playwright

    _skip_without_chromium()
    sid = _link(server)
    with sync_playwright() as p:
        browser, page, errors, edits = _open(p, server, sid, 1280)
        page.goto(f"{server.base}/decks/42?view=grid", wait_until="networkidle")
        add = page.locator("form.quick").bounding_box()
        views = page.locator("#viewform").bounding_box()
        assert add["y"] < views["y"], (add, views)  # the add row is above, not beside a blank
        assert page.locator("#viewform .filter").bounding_box()["y"] < views["y"] + 100
        assert errors == []
        browser.close()
