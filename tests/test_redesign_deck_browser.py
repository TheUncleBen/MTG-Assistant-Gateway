"""The deck page's redesign (0.7.46 full review, mockup screens 2, 3 and 5) in headless Chromium:

- the card menu in one standard shape (screen 3; findings F3, F4, F18): the card's name and
  count in its head, then Open card, Printings…, Move to and Color tag…, an "Elsewhere" group,
  a "More" group and Remove from deck alone at the end in red; on a phone it is a bottom sheet
  with More folded, every row finger-sized, and it opens on a press and hold;
- a tap opens the card viewer with a Printing row that opens the one Printings sheet (F3), with
  its chips All, Different art and In my binder;
- the deck's ⋮ menu in groups (screen 2; F9): This deck, Share, Look into it, Community, then
  Delete deck…; one Contests… item whose box offers entering and withdrawing; the Registration
  PDF as a row of the Export box's Files pane;
- the phone's deck page (screen 5; F16, F18): the cards start near the top, there is no "No deck
  tags" line, the toolbar is one row (Quick add, View, ⋮) whose View sheet drives the hidden
  controls and whose ⋮ holds Add a package…, Undo and Redo, the select bar shows only when
  selecting, and nothing scrolls sideways at 200% text;
- the editor's steppers and the shell's brand are 44 px on a touch screen (F18).

Skipped without a Chromium build."""

from __future__ import annotations

import re

import httpx
import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_overflow_sweep_browser import MEASURE
from .test_printing_picker_browser import Server, _chromium, _page
from .waits import expect, redrawn, settled

pytest.importorskip("playwright")

MENU_TEXT = (
    ".ctxmenu [role=menuitem], .ctxmenu .head, .ctxmenu .ghead, .cardview .printrow, .cardview .acts .btn"
)
ROW = ".deckview .row[data-card='Sol Ring']"


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _items(page) -> list[str]:
    """The menu's rows as written (textContent: the page's capitals and the › are styling),
    without an open Move to list's rows."""
    return page.evaluate(
        "() => [...document.querySelectorAll('.ctxmenu [role=menuitem]')]"
        ".filter(e => !e.closest('.sub')).map(e => e.textContent.replace(/›/g, '').trim())"
    )


def _visible_items(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('.ctxmenu [role=menuitem]')]"
        ".filter(e => e.getClientRects().length && !e.closest('.sub'))"
        ".map(e => e.textContent.replace(/›/g, '').trim())"
    )


def _heads(page) -> list[str]:
    return page.evaluate(
        "() => [...document.querySelectorAll('.ctxmenu .ghead')].map(e => e.textContent.trim())"
    )


def _group_of(page, text: str) -> str:
    """The group header a card menu item sits under ("" above the first group)."""
    return page.evaluate(
        """(text) => {
          const sel = '.ctxmenu [role=menuitem], .ctxmenu [role=menuitemcheckbox]';
          const item = [...document.querySelectorAll(sel)]
            .find(e => e.textContent.replace(/›/g, '').trim().startsWith(text));
          if (!item) return null;
          let at = item.closest('.grp') || item;
          for (at = at.previousElementSibling; at; at = at.previousElementSibling) {
            if (at.classList.contains('sep')) return '';
            if (at.classList.contains('ghead')) return at.textContent.replace(/›/g, '').trim();
          }
          return '';
        }""",
        text,
    )


def _own(server: Server, sid: str, items: list[dict]) -> None:
    h = httpx.Client(base_url=server.base, cookies={"mtg_session": sid}, timeout=30)
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", h.get("/collection").text).group(1)
    r = h.post("/collection/api/add", headers={"X-CSRF-Token": csrf}, json={"items": items, "source": "scan"})
    assert r.status_code == 200, r.text


def _open_card_menu(page, width: int) -> None:
    card = page.locator(ROW).first
    card.scroll_into_view_if_needed()
    if width < 600:
        # a press and hold on a touch screen (the page's own touch handlers)
        box = card.bounding_box()
        cdp = page.context.new_cdp_session(page)
        x, y = box["x"] + 60, box["y"] + box["height"] / 2
        cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
        page.locator(".ctxmenu[role=menu]").wait_for(timeout=3000)  # the hold opened the menu
        cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    else:
        card.click(button="right")
    page.locator(".ctxmenu[role=menu]").wait_for(timeout=3000)
    settled(page)


@pytest.mark.parametrize("width,theme", [(1280, "dark"), (840, "light")])
def test_the_card_menu_has_one_standard_shape(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme=theme)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        _open_card_menu(page, width)
        menu = page.locator(".ctxmenu[role=menu]")
        # the head: the name and the count with its − and +
        assert menu.locator(".head .nm").inner_text() == "Sol Ring"
        assert menu.locator(".headrow .qtyrow .n").inner_text() == "1"
        assert menu.locator(".headrow .qtyrow button.step").count() == 2
        items = _items(page)
        rows = [x for x in items if x not in ("−", "+")]
        assert rows[:4] == ["Open card", "Printings…", "Move to", "Color tag…"], rows
        assert _heads(page) == ["Elsewhere", "More"], _heads(page)
        for text in ("Watch this card", "Add to binder", "Pin card", "Add to another deck…"):
            assert _group_of(page, text) == "Elsewhere", (text, rows)
        for text in (
            "Mana value…",
            "Companion",
            "Finish",
            "Lock categories",
            "Edit in deck editor",
            "Copy name",
            "Open on Scryfall",
            "Report card…",
        ):
            assert _group_of(page, text) == "More", (text, rows)
        # the removal alone at the end, after a line, in red
        assert rows[-1] == "Remove from deck"
        last = menu.locator("[role=menuitem]").last
        assert "danger" in (last.get_attribute("class") or "")
        assert last.evaluate("e => e.previousElementSibling.className") == "sep"
        # retired names (C6) and the old pair of printing items are gone
        for gone in (
            "Change printing…",
            "Art variants…",
            "Watch price",
            "Add to deck…",
            "Lock personal categories",
        ):
            assert gone not in rows, (gone, rows)
        # every row's text starts at the same x, groups included
        xs = page.evaluate(
            "() => [...new Set([...document.querySelectorAll('.ctxmenu [role=menuitem]:not(.step)')]"
            ".filter(e => e.getClientRects().length && !e.closest('.sub'))"
            ".map(e => Math.round([...e.childNodes].filter(n => n.nodeType === 3 && n.textContent.trim())"
            ".map(n => { const r = document.createRange(); r.selectNodeContents(n);"
            " return r.getBoundingClientRect().left; })[0])))]"
        )
        assert len(xs) == 1, xs
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, MENU_TEXT) == []
        # Printings… opens the one sheet
        page.locator(".ctxmenu [role=menuitem]", has_text="Printings…").click()
        page.locator(".printpick.open").wait_for(timeout=4000)
        assert page.locator(".printpick h2").inner_text() == "Printings of Sol Ring"
        page.keyboard.press("Escape")
        assert errors == []
        browser.close()


def test_on_a_phone_the_card_menu_is_a_sheet_that_scrolls(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 375, theme="dark", height=812, touch=True)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        _open_card_menu(page, 375)
        menu = page.locator(".ctxmenu[role=menu]")
        assert "cs-menu" in menu.get_attribute("class") and page.locator(".cs-scrim").count() == 1
        box = menu.bounding_box()
        assert box["height"] <= 812 - 8, box  # never past the window
        assert abs(box["y"] + box["height"] - 812) < 2, box  # at the bottom
        # every group is open (the mockup's phone menu); the sheet scrolls to Remove at its end
        assert menu.locator("button.ghead.fold").count() == 0
        assert _heads(page) == ["Elsewhere", "More"]
        shown = _visible_items(page)
        assert "Lock categories" in shown and shown[-1] == "Remove from deck", shown
        remove = menu.locator("[role=menuitem]", has_text="Remove from deck")
        remove.scroll_into_view_if_needed()
        rb = remove.bounding_box()
        assert rb["y"] + rb["height"] <= 812, rb
        # every row and step is finger-sized (F18)
        short = page.evaluate(
            "() => [...document.querySelectorAll("
            "'.ctxmenu [role=menuitem], .ctxmenu [role=menuitemcheckbox]')]"
            ".filter(e => e.getClientRects().length)"
            ".map(e => ({ t: e.textContent.trim().slice(0, 20), h: e.getBoundingClientRect().height,"
            " w: e.getBoundingClientRect().width })).filter(r => r.h < 44 || r.w < 44)"
        )
        assert short == [], short
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, MENU_TEXT) == []
        # the scrim closes it
        page.mouse.click(10, 10)
        expect(page.locator(".ctxmenu")).to_have_count(0)
        expect(page.locator(".cs-scrim")).to_have_count(0)
        assert page.locator(".ctxmenu").count() == 0 and page.locator(".cs-scrim").count() == 0
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [375, 1280])
def test_a_tap_opens_the_viewer_whose_printing_row_opens_the_printings_sheet(
    server: Server, width: int
) -> None:
    from playwright.sync_api import sync_playwright

    # SLD 1075 carries the same picture as the deck's CMR 472; the binder holds SLD 1074; LEA 270
    # is a summary without the picture's id (one kept from before this release carried it)
    for c in server.sf.extra_cards:
        if c["set"] == "sld" and c["collector_number"] == "1075":
            c["illustration_id"] = "art-001"
        if c["set"] == "lea":
            del c["illustration_id"]
    sid = server.sign_in_and_link()
    _own(server, sid, [{"name": "Sol Ring", "quantity": 1, "set": "sld", "collector_number": "1074"}])
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme="light", touch=width < 600)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        card = page.locator(ROW).first
        card.scroll_into_view_if_needed()
        card.click()
        view = page.locator(".cardview.open")
        view.wait_for(timeout=3000)
        expect(view.locator(".printrow .lbl")).to_have_text("Printing")
        settled(page)
        # the Printing row names the printing and opens the sheet (F3)
        row = view.locator(".printrow")
        assert row.locator(".lbl").inner_text() == "Printing"
        assert "CMR" in row.locator(".val").inner_text()
        # the actions: the count, Move to…, Remove in outline red, then the binder and the editor
        acts = [t.strip() for t in view.locator(".acts .btn").all_inner_texts()]
        assert "Move to…" in acts and "Add to binder" in acts and "Edit in deck editor" in acts, acts
        remove = view.locator(".acts .btn", has_text="Remove from deck")
        assert "btn-outline-danger" in remove.get_attribute("class")
        assert view.locator(".more a[data-scry]").inner_text().strip().startswith("Scryfall")
        if width < 600:
            short = page.evaluate(
                "() => [...document.querySelectorAll('.cardview .acts .btn, .cardview .printrow')]"
                ".map(e => e.getBoundingClientRect().height).filter(h => h < 44)"
            )
            assert short == [], short
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, MENU_TEXT) == []
        row.click()
        page.locator(".printpick.open").wait_for(timeout=4000)
        page.locator(".printpick .pptile").first.wait_for(timeout=5000)
        assert page.locator(".printpick h2").inner_text() == "Printings of Sol Ring"
        chips = page.locator(".printpick .ppscope .ui-chip")
        assert [t.strip() for t in chips.all_inner_texts()] == ["All", "Different art", "In my binder"]
        assert chips.first.get_attribute("aria-pressed") == "true"

        def codes() -> list[str]:
            return [t.split(" · ")[0] for t in page.locator(".printpick .pptile .ppcode").all_inner_texts()]

        all_codes = codes()
        all_n = len(all_codes)
        # the released paper printings in English (fake_printings and the fake Scryfall's own)
        assert {"CMR #472", "SLD #1074", "SLD #1075", "LEA #270", "4ED #325"} <= set(all_codes), all_codes
        assert not any(c.startswith(("YMID", "FUT", "STA")) for c in all_codes), all_codes
        # Different art: one printing of each other picture; the deck's own stays, marked Current
        chips.nth(1).click()
        page.wait_for_function(
            "(n) => document.querySelectorAll('.printpick .pptile').length === n", arg=all_n - 1, timeout=4000
        )
        # SLD 1075 (the same picture as the deck's) is left out; the deck's own stays, and so
        # does a printing whose summary has no picture id (LEA 270): it counts as its own art
        art = codes()
        assert "SLD #1075" not in art and "CMR #472" in art, art
        assert {"SLD #1074", "LEA #270", "4ED #325"} <= set(art) and len(art) == all_n - 1, art
        assert page.locator(".printpick .pptile.current").count() == 1
        # In my binder: the binder's printing (and the deck's own)
        chips.nth(2).click()
        page.wait_for_function(
            "() => document.querySelectorAll('.printpick .pptile').length === 2", timeout=4000
        )
        assert set(codes()) == {"CMR #472", "SLD #1074"}, codes()
        assert chips.nth(2).get_attribute("aria-pressed") == "true"
        chips.first.click()
        expect(page.locator(".printpick .pptile")).to_have_count(all_n)
        assert page.locator(".printpick .pptile").count() == all_n
        if width < 600:
            assert (
                min(
                    page.evaluate(
                        "() => [...document.querySelectorAll('.printpick .ppscope .ui-chip')]"
                        ".map(e => e.getBoundingClientRect().height)"
                    )
                )
                >= 44
            )
        assert page.evaluate(MEASURE) == []
        page.keyboard.press("Escape")
        assert errors == []
        browser.close()


@pytest.mark.parametrize("width", [375, 1280])
def test_the_deck_menu_is_grouped_and_the_contests_and_the_pdf_have_new_homes(
    server: Server, width: int
) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme="dark", touch=width < 600)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        page.locator(".banner .deckmenu").click()
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        assert _heads(page) == ["This deck", "Share", "Look into it", "Community"], _heads(page)
        items = _items(page)
        want = [
            "Rename…",
            "Move to folder…",
            "Deck settings",
            "Share…",
            "Export…",
            "Open on Archidekt",
            "Deck stats",
            "Deck reports",
            "History",
            "Compare with…",
            "Watch deck price",
            "Deck help…",
            "Contests…",
            "Report deck…",
            "Delete deck…",
        ]
        assert [x for x in items if x in want] == want, items
        for gone in (
            "Add a package…",
            "Registration PDF…",
            "Enter in a contest…",
            "Withdraw from a contest…",
            "Request deck help…",
            "History and snapshots",
            "Watch price",
        ):
            assert gone not in items, (gone, items)
        assert items[-1] == "Delete deck…"
        assert "danger" in menu.locator("[role=menuitem]").last.get_attribute("class")
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, MENU_TEXT) == []
        # Contests…: one box with the two ways
        menu.locator("[role=menuitem]", has_text="Contests…").click()
        box = page.locator(".mtgbox[role=dialog]:not([hidden])")
        box.wait_for(timeout=3000)
        assert box.locator("h2").inner_text() == "Contests: “Sample Commander Deck”"
        rows = [t.strip().split("\n")[0] for t in box.locator(".sharerow").all_inner_texts()]
        assert rows == ["Enter this deck in a contest…", "Withdraw from a contest…"], rows
        box.locator(".sharerow", has_text="Enter this deck in a contest…").click()
        page.locator("#contest").wait_for(state="visible", timeout=3000)
        page.keyboard.press("Escape")
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        # the Registration PDF: a row of the Export box's Files pane
        page.locator(".banner .deckmenu").click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Export…").click()
        page.locator("#export").wait_for(state="visible")
        page.locator("#export [role=tab][data-tab='exp-files']").click()
        pdf = page.locator("#export .regpdf")
        assert pdf.inner_text().strip() == "Registration PDF…"
        pdf.click()
        page.locator("#pdf").wait_for(state="visible", timeout=3000)
        assert "Make the PDF" in page.locator("#pdf").inner_text()
        page.keyboard.press("Escape")
        assert errors == []
        browser.close()


def test_the_phone_deck_page_starts_with_the_cards_near_the_top(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 375, theme="dark", height=812, touch=True)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        assert "No deck tags" not in page.locator(".banner").inner_text()
        first = page.evaluate(
            "() => document.querySelector('.deckview .stack').getBoundingClientRect().top + window.scrollY"
        )
        assert first <= 430, first  # the mockup's 294 with a thumbnail; this deck has no art
        # the banner's buttons in their short form
        shown = page.evaluate(
            "() => [...document.querySelectorAll('.banner .btn')].filter(e => e.getClientRects().length)"
            ".map(e => ({ t: e.innerText.trim(), h: e.getBoundingClientRect().height }))"
        )
        words = {b["t"] for b in shown}
        assert {"Edit", "Clone"} <= words, shown
        assert all(b["h"] >= 44 for b in shown), shown
        assert page.locator(".banner .btn .lg").first.is_hidden()
        # the toolbar: one row with Quick add, View and ⋮; the select bar waits for Select cards
        toolbar = page.locator(".toolbar.compact")
        assert toolbar.count() == 1
        assert (
            page.locator(".toolbar form.views").is_hidden() and page.locator(".toolbar .addpkg").is_hidden()
        )
        assert page.locator(".bulkbar").is_hidden()
        assert page.locator(".movehint").inner_text() == "Hold a card to move it to another category."
        for sel in (".toolbar .viewbtn", ".toolbar .toolmore", ".toolbar .quickrow button:not(.addpkg)"):
            b = page.locator(sel).first
            assert b.is_visible() and b.bounding_box()["height"] >= 44, sel
        assert page.evaluate(MEASURE) == []
        # the View sheet mirrors the hidden controls
        page.locator(".toolbar .viewbtn").click()
        sheet = page.locator(".mtgbox[role=dialog]:not([hidden])")
        sheet.wait_for(timeout=3000)
        assert sheet.locator("h2").inner_text() == "View"
        labels = [
            t.strip()
            for t in sheet.locator(
                ".viewsheet .vrow > label:not(.switch), .viewsheet .vrow > .lbl"
            ).all_inner_texts()
        ]
        # the text view has no card size; the picture views add a Card size row
        assert labels == ["Layout", "Group by", "Sort by", "Filter", "Select cards"], labels
        sheet.locator("#vs-q").fill("sol")
        expect(page.locator("#q")).to_have_value("sol")
        assert page.locator("#q").input_value() == "sol"
        assert page.locator(ROW).first.is_visible()
        sheet.locator(".viewsheet .switch").click()
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        assert page.locator(".bulkbar.on").is_visible()
        # the ⋮: Add a package…, Undo and Redo (nothing to undo yet)
        page.locator(".toolbar .toolmore").click()
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(timeout=3000)
        assert _items(page) == ["Add a package…", "Undo", "Redo"], _items(page)
        assert menu.locator("[role=menuitem]", has_text="Undo").get_attribute("disabled") is not None
        menu.locator("[role=menuitem]", has_text="Add a package…").click()
        page.locator("#package").wait_for(state="visible", timeout=3000)
        page.keyboard.press("Escape")
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        # 200% text: nothing scrolls sideways, the toolbar wraps
        page.evaluate("() => { document.documentElement.style.fontSize = '200%'; }")
        redrawn(page)
        assert page.evaluate(MEASURE) == []
        assert errors == []
        browser.close()


def test_the_editor_steppers_and_the_brand_are_finger_sized(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = server.sign_in_and_link()
    _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 375, theme="light", height=812, touch=True)
        page.goto(f"{server.base}/decks/42/edit", wait_until="networkidle")
        small = page.evaluate(
            "() => [...document.querySelectorAll('.erow .qty button.mini, .erow .qty input, .topbar .brand, "
            ".editbar .backup, .bulkbar .histtools button')]"
            ".filter(e => e.getClientRects().length)"
            ".map(e => ({ t: (e.getAttribute('aria-label') || e.textContent).trim().slice(0, 16),"
            " h: e.getBoundingClientRect().height, w: e.getBoundingClientRect().width }))"
            ".filter(r => r.h < 44 || r.w < 44)"
        )
        assert small == [], small
        assert page.evaluate(MEASURE) == []
        assert errors == []
        browser.close()
