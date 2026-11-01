"""The Collection page in a browser (UI overhaul 2026-10-21, mockup 20-collection): nothing
sticks out at 344, 390, 810 and 1280 px, light and dark; the stepper is 44 px; the card size and
Clear filters stay on phones; − on the last copy asks first and the removal shows as a strip; the
Add cards box has two tabs; the shared ⋮ menu's Details… opens the Details box, which saves in
place; the name box narrows the list as you type; Load more appends the next rows."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_overflow_sweep_browser import MEASURE
from .test_printing_picker_browser import Server, _chromium, _page

pytest.importorskip("playwright")

SIZES = [(344, "light"), (390, "dark"), (810, "light"), (1280, "dark")]


@pytest.fixture
def server(tmp_path: Path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _seed(server: Server, n: int) -> None:
    ark = server.ark
    pks = (9003, 5011, 5007, 9005, 9008)
    for i in range(n):
        ark.next_coll_id += 1
        body = {
            "quantity": 1 if i else 3,
            "modifier": ("Normal", "Foil", "Etched")[i // 5 % 3],
            "condition": 1,
        }
        row = ark._collection_row(ark.next_coll_id, ark.printing(pks[i % 5]), body)
        row["createdAt"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - i * 86400))
        ark.collections.setdefault("alice", {})[row["id"]] = row


def _box(page, sel: str) -> dict:
    b = page.locator(sel).first.bounding_box()
    assert b is not None, sel
    return b


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_page_fits_and_its_controls_are_44px_at_every_width(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server, 30)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme, touch=width < 900)
        for view in ("grid", "list"):
            page.goto(f"{server.base}/collection?view={view}", wait_until="networkidle")
            assert page.evaluate(MEASURE) == [], (view, width)
            assert (
                page.evaluate(
                    CONTRAST,
                    ".rbar .cnt, .rbar .clr, .wdg .lab, .wdg .det, #cards .name, #cards .ty, #cards .s",
                )
                == []
            )
            minus = _box(page, "#cards form.qty button[value=dec]")
            assert minus["width"] >= 44 and minus["height"] >= 44, (view, minus)
            assert _box(page, "#cards .kebab")["height"] >= (44 if width < 900 else 36)
            # the card size and Clear filters stay in the bar on phones (no Filters sheet holds them)
            assert page.locator(".rbar .size .sizeseg").is_visible()
            assert page.locator(".rbar .clr").is_visible()
            if width < 600:
                assert page.locator(".rbar .sortcw select").is_visible()
                assert not page.locator(".rbar .sortw select").is_visible()
            assert page.locator("#cards > li[data-id]").count() == 24
        assert errors == []
        browser.close()


def test_the_stepper_removes_the_last_copy_with_undo_and_the_menu_reaches_the_details_box(
    server: Server,
) -> None:
    """− on the last copy takes the row out at once and the strip offers Undo, which puts it back
    with its printing, finish, price paid and Binder tags (QOL-01, 0.7.46 audit QG-09); no
    question first."""
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server, 3)
    # the single-copy rows carry a Binder tag of the member's: Undo must bring it back too
    ark = server.ark
    ark.next_collection_tag_id += 1
    tag_id = ark.next_collection_tag_id
    tag = {"id": tag_id, "name": "trade", "color": "#1ebb6c"}
    ark.collection_tag_lists.setdefault("alice", {})[tag_id] = tag
    for row in ark.collections["alice"].values():
        if row["quantity"] == 1:
            row["tags"] = [tag_id]
    dialogs: list[str] = []
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280)
        page.on("dialog", lambda d: (dialogs.append(d.message), d.dismiss()))
        page.goto(f"{server.base}/collection?view=grid", wait_until="networkidle")
        first = page.locator("#cards > li[data-id]").last  # the oldest entry, with three copies
        rid = first.get_attribute("data-id")
        # + and − save in place
        first.locator("button[value=inc]").click()
        page.wait_for_function(
            f"() => document.querySelector(\"[data-id='{rid}'] output\").textContent === '4'"
        )
        assert server.ark.collections["alice"][int(rid)]["quantity"] == 4
        # − on a single copy: the row goes at once, no box, and the strip offers Undo
        one = page.locator("#cards > li[data-id]").first
        one_id = one.get_attribute("data-id")
        was = dict(server.ark.collections["alice"][int(one_id)])
        was["purchasePrice"] = 2.5
        server.ark.collections["alice"][int(one_id)]["purchasePrice"] = 2.5
        one.locator("button[value=dec]").click()
        page.wait_for_function(f"() => !document.querySelector(\"[data-id='{one_id}']\")")
        assert page.locator(".mtgbox[role=dialog]:visible").count() == 0 and dialogs == []
        assert int(one_id) not in server.ark.collections["alice"]
        # the shared toast at the foot of the screen (in view on a phone), with Undo
        toast = page.locator(".toast.cards-toast[role=status]")
        toast.wait_for(timeout=3000)
        assert toast.locator(".msg").inner_text().strip().startswith("Removed from your binder.")
        assert page.locator("#col-count").inner_text() == "2 entries"
        # Undo: the row is back on the page and on Archidekt, the same printing, finish and price
        toast.locator("button", has_text="Undo").click()
        page.locator(".toast.cards-toast .msg", has_text="is back in your binder.").wait_for(timeout=8000)
        assert page.locator("#cards > li[data-id]").count() == 3
        assert page.locator("#col-count").inner_text() == "3 entries"
        back = [r for r in server.ark.collections["alice"].values() if r["card"]["id"] == was["card"]["id"]]
        assert len(back) == 1 and back[0]["quantity"] == 1 and back[0]["modifier"] == was["modifier"]
        page.wait_for_function("() => !document.querySelector('#col-notes .notice.error')", timeout=3000)
        assert back[0].get("purchasePrice") in (2.5, "2.50", "2.5")
        assert back[0].get("tags") == [tag_id]  # its Binder tag is back on the row
        assert "without its tags" not in toast.inner_text()
        new_id = str(back[0]["id"])
        assert page.locator(f"#cards > li[data-id='{new_id}']").count() == 1
        # removed again: the toast says so, and its × dismisses it
        page.locator(f"#cards > li[data-id='{new_id}'] button[value=dec]").click()
        page.wait_for_function(f"() => !document.querySelector(\"[data-id='{new_id}']\")")
        toast = page.locator(".toast.cards-toast[role=status]")
        toast.wait_for(timeout=3000)
        assert page.locator("#col-count").inner_text() == "2 entries"
        toast.locator("button[aria-label=Dismiss]").click()
        assert (
            page.locator(".toast.cards-toast").count() == 0
            and page.locator("#col-notes .notice").count() == 0
        )
        # the ⋮ opens the shared menu; Details… opens the Details box filled from the row
        first.locator(".kebab").click()
        menu = page.locator(".ctxmenu")
        menu.wait_for(timeout=3000)
        items = [t.strip() for t in menu.locator("[role=menuitem]").all_inner_texts()]
        assert "Details…" in items and items[-1] == "Remove from binder"
        assert menu.locator(".mqty .lbl").inner_text() == "Quantity"
        menu.locator("[role=menuitem]", has_text="Details…").click()
        details = page.locator("#col-details")
        page.wait_for_function("() => !document.getElementById('col-details').hidden")
        assert details.locator("h2").inner_text() == "Details: " + first.locator(".name").inner_text()
        assert details.locator("input[name=id]").input_value() == rid
        details.locator("select[name=finish]").select_option("foil")
        details.locator("select[name=condition]").select_option("LP")
        details.locator("select[name=language]").select_option("JA")
        details.locator("button.primary", has_text="Save").click()
        page.wait_for_function(
            f"() => document.querySelector(\"[data-id='{rid}']\").getAttribute('data-fin') === 'foil'"
        )
        rec = server.ark.collections["alice"][int(rid)]
        assert rec["modifier"] == "Foil"
        assert (
            first.locator(".pic .fin").inner_text() == "Foil"
            and first.locator(".pic .cond").inner_text() == "LP"
        )
        assert "Japanese" in first.locator(".ty").inner_text()
        assert (
            page.locator("#col-notes .notice.ok").inner_text().strip() == "Card details saved to your binder."
        )
        page.wait_for_function("() => document.getElementById('col-details').hidden")
        assert errors == []
        browser.close()


def test_the_add_box_tabs_the_filter_and_load_more(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    _chromium()
    sid = server.sign_in_and_link()
    _seed(server, 30)
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 810, touch=True)
        page.goto(f"{server.base}/collection?view=grid", wait_until="networkidle")
        # + Add cards opens the box on Add a card; the Import tab swaps the pane and the button
        page.locator("a.btn-primary[data-box-open=col-add]").click()
        box = page.locator("#col-add")
        page.wait_for_function("() => !document.getElementById('col-add').hidden")
        assert box.locator("[data-coltab=add]").get_attribute("aria-selected") == "true"
        assert box.locator("#col-addform").is_visible() and not box.locator("#col-importform").is_visible()
        assert box.locator("button.primary:visible").inner_text().strip() == "Add"
        box.locator("[data-coltab=import]").click()
        assert box.locator("#col-importform").is_visible() and not box.locator("#col-addform").is_visible()
        assert box.locator("button.primary:visible").inner_text().strip() == "Import"
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.getElementById('col-add').hidden")
        # the widget's Import a list opens the box on the Import tab
        page.locator(".ui-widgets a[data-box-open=col-add]").click()
        page.wait_for_function("() => !document.getElementById('col-add').hidden")
        assert box.locator("[data-coltab=import]").get_attribute("aria-selected") == "true"
        page.keyboard.press("Escape")
        page.wait_for_function("() => document.getElementById('col-add').hidden")
        # the name box narrows the list as you type
        page.locator("#col-q").fill("cultiv")
        page.wait_for_timeout(100)
        shown = page.locator("#cards > li[data-id]:visible")
        assert shown.count() >= 1 and all(
            "cultivate" in n
            for n in page.locator("#cards > li[data-id]:visible").evaluate_all(
                "els => els.map(e => e.dataset.name)"
            )
        )
        page.locator("#col-q").fill("zzzz")
        # "No cards match" once Archidekt's own filter says so (test_binder_filter_browser)
        page.locator(".col-nomatch").wait_for(state="visible", timeout=3000)
        page.locator("#col-q").fill("")
        page.wait_for_function(
            "() => document.querySelectorAll('#cards > li[data-id]:not([hidden])').length === 24"
        )
        # Load more appends the rest without leaving the page, then says every entry is shown
        more = page.locator(".loadmore [data-load-more]")
        assert more.inner_text().strip() == "Load more cards"
        assert page.locator(".loadmore p").inner_text() == "24 of 30"
        more.click()
        page.wait_for_function("() => document.querySelectorAll('#cards > li[data-id]').length === 30")
        assert page.url.endswith("/collection?view=grid")
        assert page.locator(".loadmore [data-load-more]").count() == 0
        assert page.locator(".loadmore p").inner_text() == "All 30 entries shown."
        assert errors == []
        browser.close()
