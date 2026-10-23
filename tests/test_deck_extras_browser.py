"""The deck-side pages of parity release 2 in headless Chromium (static/deckextras.js,
decksettings.js, newpackage.js, lists.js): the deck menu and the card menu carry the new items
and open from the ⋮, a right-click and a press and hold; every box fits a phone with no sideways
scroll, 44 px targets and readable text in both themes, holds together at 200% text, reads its
list when it opens (your packages, your templates, your decks) and works in place (the lock, the
PDF) or lands on the review page (help, a report, a package, a contest entry). Skipped without a
Chromium build."""

from __future__ import annotations

import copy

import pytest

from .test_admin_card_models_browser import HOLD
from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_deck_extras import _ramp, _templates
from .test_editor_browser import _link
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

TEXT = (
    ".ctxmenu [role=menuitem], .ctxmenu .head, .mtgbox h2, .mtgbox p, .mtgbox label, .mtgbox .acts button, "
    ".mtgbox .notice, .mtgbox .ui-lbl, .dx-strip b, .dx-strip .muted, .dx-strip .btn, .ds .ds-pkg label, "
    ".ds .ds-pkg .help, .ds .ds-pkg .lab, .ds .budget label, .ds .fmts label"
)
WORDS = " ".join(["word"] * 12)
DECK_ITEMS = [
    "Add a package…",
    "Registration PDF…",
    "Request deck help…",
    "Enter in a contest…",
    "Withdraw from a contest…",
    "Report deck…",
]


def _seed(ark) -> None:
    _templates(ark)
    _ramp(ark)
    ark.decks[42]["deckFormat"] = 3
    ark.deck_tags[42] = [{"id": 1, "tag": 1, "name": "Combo", "position": 0}]
    pkg = copy.deepcopy(ark.decks[42])
    pkg.update(
        id=50,
        name="Golgari staples",
        deckFormat=None,
        cardPackage={
            "id": 900,
            "keywords": "golgari, staples",
            "budget": 2,
            "intendedFormats": [3],
            "importCount": 0,
        },
    )
    # its cards: Arcane Signet (not in deck 42, so adding the package has something to add)
    from .fake_archidekt import CARD_DB

    signet = copy.deepcopy(pkg["cards"][0])
    signet.update(id=60000, categories=None, card={**signet["card"], **CARD_DB["arcane signet"]})
    pkg["cards"] = [signet]
    pkg["categories"] = [c for c in pkg["categories"] if c["name"] != "Commander"]
    ark.decks[50] = pkg


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path, writes_enabled=True)
    _seed(s.ark)
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _targets(page, sel: str) -> list[float]:
    return page.evaluate(
        "(sel) => [...document.querySelectorAll(sel)].filter(b => b.offsetParent !== null)"
        ".map(b => Math.min(b.getBoundingClientRect().width, b.getBoundingClientRect().height))",
        sel,
    )


def _menu_items(page) -> list[str]:
    return [t.strip() for t in page.locator(".ctxmenu [role=menuitem]").all_inner_texts()]


def _close_menu(page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    assert page.locator(".ctxmenu").count() == 0


def _close_box(page) -> None:
    page.keyboard.press("Escape")
    page.locator(".mtgbox-scrim").wait_for(state="detached")


def _open_from_deck_menu(page, text: str, box_id: str):
    page.locator(".banner .deckmenu").click()
    page.locator(".ctxmenu [role=menuitem]", has_text=text).click()
    box = page.locator(f"#{box_id}")
    box.wait_for(state="visible")
    page.wait_for_timeout(300)  # the box's open transition (a slight scale) ends before measuring
    return box


@pytest.mark.parametrize("width,theme", [(390, "light"), (390, "dark"), (810, "light"), (1366, "dark")])
def test_the_menus_carry_the_new_items_and_every_box_fits(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        assert page.evaluate(MEASURE) == []
        # the deck menu from its ⋮: the new items in the mockup's order
        page.locator(".banner .deckmenu").click()
        items = _menu_items(page)
        found = [x for x in items if x in DECK_ITEMS]
        assert found == DECK_ITEMS, items
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TEXT) == []
        if width < 900:
            assert min(_targets(page, ".ctxmenu [role=menuitem]")) >= 43.5
        _close_menu(page)
        # a right-click on the banner, and a press and hold on a touch screen
        page.locator(".banner h1").first.click(button="right")
        assert "Report deck…" in _menu_items(page)
        _close_menu(page)
        if width < 900:
            page.evaluate(HOLD, ".banner h1")
            page.wait_for_timeout(900)
            assert "Request deck help…" in _menu_items(page)
            _close_menu(page)
        # each box opens from the menu, fits and reads
        for text, box_id, needs in (
            ("Request deck help…", "help", "Post the request"),
            ("Registration PDF…", "pdf", "Make the PDF"),
            ("Add a package…", "package", "Review the cards"),
            ("Enter in a contest…", "contest", "Review the entry"),
            ("Withdraw from a contest…", "withdraw", "Withdraw"),
            ("Report deck…", "report", "Send report"),
        ):
            box = _open_from_deck_menu(page, text, box_id)
            if box_id == "package":  # the member's packages are read when the box opens
                box.locator(".dx-mine .tree label").first.wait_for(state="visible")
                assert "Golgari staples" in box.locator(".dx-mine").inner_text()
            assert needs in box.inner_text(), (box_id, box.inner_text())
            assert page.evaluate(MEASURE) == [], box_id
            assert page.evaluate(CONTRAST, TEXT) == [], box_id
            if width < 900:
                assert min(_targets(page, f"#{box_id} .acts button, #{box_id} input[type=text]")) >= 43.5
            _close_box(page)
        # the card menu: Lock personal categories and Report card…
        card = page.locator(".deckview .row[data-card='Sol Ring']").first
        card.scroll_into_view_if_needed()
        card.click(button="right")
        items = _menu_items(page)
        assert "Lock personal categories" in items and "Report card…" in items, items
        page.locator(".ctxmenu [role=menuitem]", has_text="Lock personal categories").click()
        lock = page.locator("#lock")
        lock.wait_for(state="visible")
        lock.locator("[data-sentence] b").wait_for(state="visible")
        # on a phone the card menu went back over its own history entry when it closed; that
        # popstate lands a moment later and must not close the box it opened
        page.wait_for_timeout(700)
        assert lock.is_visible() and page.locator(".mtgbox-scrim").count() == 1
        assert (
            "it goes into Ramp" in lock.inner_text()
            and "Left out, not a template: Artifact." in lock.inner_text()
        )
        assert "Lock personal categories for “Sol Ring”" in lock.locator("h2").inner_text()
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TEXT) == []
        _close_box(page)
        card.click(button="right")
        page.locator(".ctxmenu [role=menuitem]", has_text="Report card…").click()
        rc = page.locator("#reportcard")
        rc.wait_for(state="visible")
        page.wait_for_timeout(700)
        assert rc.is_visible() and page.locator(".mtgbox-scrim").count() == 1
        assert "Report “Sol Ring” to Archidekt's moderators" in rc.locator("h2").inner_text()
        assert rc.locator("input[name=card]").input_value() == "Sol Ring"
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TEXT) == []
        _close_box(page)
        browser.close()
    assert errors == []


@pytest.mark.parametrize("zoom", ["root", "text"])
def test_the_deck_menu_and_a_box_hold_together_on_a_phone_at_200_percent_text(
    server: Server, zoom: str
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        # the zoom is applied once per page (TEXT_200 doubles what is there: twice would be 400%),
        # after the menu or the box is on the page, as a browser's text zoom reaches both
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        page.locator(".banner .deckmenu").click()
        page.locator(".ctxmenu").wait_for(state="visible")
        page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
        page.wait_for_timeout(50)
        assert page.evaluate(MEASURE) == []
        assert "Request deck help…" in _menu_items(page)
        _close_menu(page)
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        _open_from_deck_menu(page, "Request deck help…", "help")
        page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
        page.wait_for_timeout(50)
        assert page.evaluate(MEASURE) == []
        _close_box(page)
        browser.close()
    assert errors == []


def test_the_boxes_work_in_place_or_land_on_the_review_page(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    ark = server.ark
    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366, "light", exe)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        # the lock: applied in place, said in a toast
        card = page.locator(".deckview .row[data-card='Sol Ring']").first
        card.click(button="right")
        page.locator(".ctxmenu [role=menuitem]", has_text="Lock personal categories").click()
        lock = page.locator("#lock")
        lock.locator("[data-sentence] b").wait_for(state="visible")
        lock.locator(".acts button", has_text="Lock").click()
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        page.locator(".toast", has_text="Default categories for “Sol Ring” saved").wait_for(state="visible")
        assert ark.account.defaults["alice"] == {9003: ["Ramp"]}
        # the PDF: the link appears in the box, nothing stored
        pdf = _open_from_deck_menu(page, "Registration PDF…", "pdf")
        pdf.locator("input[name=first_name]").fill("Ben")
        pdf.locator("input[name=last_name]").fill("Smith")
        pdf.locator("input[name=event]").fill("FNM")
        pdf.locator(".acts button", has_text="Make the PDF").click()
        link = pdf.locator(".dx-pdf-done a")
        link.wait_for(state="visible")
        assert link.get_attribute("href") == "https://storage.example.com/pdf/1.pdf"
        assert ark.account.pdfs[0]["event"] == "FNM"
        _close_box(page)
        # help: the page's own check, then the review page
        help_box = _open_from_deck_menu(page, "Request deck help…", "help")
        help_box.locator(".acts button", has_text="Post the request").click()
        assert "Each box needs 10 to 200 words." in help_box.locator(".boxnote").inner_text()
        help_box.locator("textarea[name=goals]").fill(WORDS)
        help_box.locator("textarea[name=issues]").fill(WORDS)
        assert "12 words (10 to 200)" in help_box.inner_text()
        help_box.locator(".acts button", has_text="Post the request").click()
        page.wait_for_url("**/proposals/**")
        assert ark.account.help == {}  # the review page's press, not the form's
        # a package: its radio fills the field, then the review page
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        pkg = _open_from_deck_menu(page, "Add a package…", "package")
        pkg.locator(".dx-mine .tree label").first.wait_for(state="visible")
        pkg.locator(".acts button", has_text="Review the cards").click()
        assert "Pick one of your packages" in pkg.locator(".boxnote").inner_text()
        pkg.locator(".dx-mine .tree input[type=radio]").first.check()
        assert pkg.locator("input[name=package]").input_value() == "50"
        pkg.locator(".acts button", has_text="Review the cards").click()
        page.wait_for_url("**/proposals/**")
        assert "Golgari staples" in page.content()
        # a report from the card menu
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        card = page.locator(".deckview .row[data-card='Sol Ring']").first
        card.click(button="right")
        page.locator(".ctxmenu [role=menuitem]", has_text="Report card…").click()
        rc = page.locator("#reportcard")
        rc.locator(".acts button", has_text="Send report").click()
        assert "Say what is wrong first." in rc.locator(".boxnote").inner_text()
        rc.locator("textarea[name=message]").fill("Wrong art")
        rc.locator(".acts button", has_text="Send report").click()
        page.wait_for_url("**/proposals/**")
        assert ark.account.reports == []
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", [(390, "dark"), (1366, "light")])
def test_deck_settings_package_panel_and_add_template(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    ark = server.ark
    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/50/settings", wait_until="networkidle")
        panel = page.locator("#package")
        assert panel.is_visible() and "Save package" in panel.inner_text()
        assert panel.locator("input[name=keywords]").input_value() == "golgari, staples"
        assert panel.locator("input[name=budget][value='2']").is_checked()
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TEXT) == []
        if width < 900:
            assert min(_targets(page, "#package .budget label, #package .fmts label")) >= 43.5
        panel.get_by_text("$$$", exact=True).click()
        panel.locator(".fmts input[value='pauper']").check()
        panel.locator("button", has_text="Save package").click()
        page.wait_for_url("**/settings?ok=package**")
        assert "Package settings saved to Archidekt and read back" in page.content()
        block = ark.decks[50]["cardPackage"]
        assert block["budget"] == 3 and len(block["intendedFormats"]) == 2
        # Add template…: the list is read when the box opens; Ramp is a category already
        page.locator("[data-box-open='tplbox']").click()
        box = page.locator("#tplbox")
        box.wait_for(state="visible")
        box.locator("input[name=template]").first.wait_for(state="visible")
        names = box.locator("input[name=template]").evaluate_all("els => els.map(e => e.value)")
        assert names == ["Draw"], names
        go = box.locator(".acts button[type=submit]")
        assert go.is_disabled()
        box.locator("input[name=template][value='Draw']").check()
        assert go.inner_text().strip() == "Add 1 category" and go.is_enabled()
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TEXT) == []
        go.click()
        page.wait_for_url("**/settings?ok=template_added**")
        assert [c["name"] for c in ark.decks[50]["categories"] if c["name"] == "Draw"] == ["Draw"]
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", [(390, "light"), (1366, "dark")])
def test_new_package_on_my_decks_and_add_to_a_deck_on_my_lists(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    ark = server.ark
    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        opener = page.locator("[data-box-open='newpkg']")
        assert "btn-primary" not in (opener.get_attribute("class") or "")  # New deck stays the orange one
        opener.click()
        box = page.locator("#newpkg")
        box.wait_for(state="visible")
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TEXT) == []
        if width < 900:
            assert (
                min(_targets(page, "#newpkg .acts button, #newpkg .budget label, #newpkg .fmts label"))
                >= 43.5
            )
        # the pills and the budget choices keep their shape inside a box (whose labels are block)
        assert (
            page.evaluate(
                "() => [...document.querySelectorAll('#newpkg .fmts label, #newpkg .budget label')]"
                ".map(l => getComputedStyle(l).display).filter(d => !/flex$/.test(d))"
            )
            == []
        )
        go = box.locator(".acts button", has_text="Create package")
        go.click()
        assert "Give the package a name." in box.locator(".boxnote").inner_text()
        box.locator("input[name=name]").fill("Fetches")
        assert "7 / 120" in box.inner_text()
        go.click()
        assert "Give it at least one keyword." in box.locator(".boxnote").inner_text()
        box.locator("input[name=keywords]").fill("fetchlands")
        box.locator(".budget label", has_text="$$").first.click()
        box.locator(".fmts label", has_text="Commander").first.click()
        go.click()
        page.wait_for_url("**/decks/*?ok=created")
        made = next(d for d in ark.decks.values() if d["name"] == "Fetches")
        assert made["cardPackage"]["keywords"] == "fetchlands" and made["cards"] == []
        # My lists: Add to a deck… from the row's menu, the decks read when the box opens
        page.goto(f"{server.base}/lists/templates", wait_until="networkidle")
        row = page.locator(".lp .ui-table .tr[data-row]").filter(has_text="Draw").first
        row.locator(".kebab").click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Add to a deck…").click()
        tpl = page.locator("#tpl-deck")
        tpl.wait_for(state="visible")
        assert "Add “Draw” to a deck" in tpl.locator("h2").inner_text()
        sel = tpl.locator("select[name=deck]")
        page.wait_for_function("() => !document.querySelector('#tpl-deck select[name=deck]').disabled")
        sel.select_option(label="Sample Commander Deck")
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TEXT) == []
        tpl.locator(".acts button", has_text="Add category").click()
        page.wait_for_url("**/proposals/**")
        assert not [c for c in ark.decks[42]["categories"] if c["name"] == "Draw"]
        browser.close()
    assert errors == []
