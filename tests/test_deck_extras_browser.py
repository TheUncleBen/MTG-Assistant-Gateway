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
    _amy_packages(ark)


def _amy_packages(ark) -> None:
    """Two public packages of amy's for the Followed and All chips; alice follows Amy burn."""
    for did, name, block in (
        (
            901,
            "Amy shocks",
            {"keywords": "shocklands", "budget": 2, "intendedFormats": [3], "importCount": 3},
        ),
        (902, "Amy burn", {"keywords": "burn", "budget": 1, "intendedFormats": [2], "importCount": 1}),
    ):
        ark.decks[did] = {
            "id": did,
            "name": name,
            "owner": {"username": "amy"},
            "updatedAt": "2026-10-03T00:00:00Z",
            "deckFormat": None,
            "private": False,
            "categories": [],
            "cards": [],
            "cardPackage": {"id": did + 1000, **block},
        }
    ark.bookmarks.setdefault("alice", set()).add(902)


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
        assert "7/120" in box.inner_text()  # as New deck counts
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
        # the themed control shows the first deck once the list is read, not a blank
        assert tpl.locator(".msel-btn").inner_text().strip() == "Fetches"  # the package made above
        sel.select_option(label="Sample Commander Deck")
        page.wait_for_timeout(100)
        assert tpl.locator(".msel-btn").inner_text().strip() == "Sample Commander Deck"
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TEXT) == []
        tpl.locator(".acts button", has_text="Add category").click()
        page.wait_for_url("**/proposals/**")
        assert not [c for c in ark.decks[42]["categories"] if c["name"] == "Draw"]
        browser.close()
    assert errors == []


# -- the package listing UI (plan rows 6.1 and 6.4) ----------------------------------------------------

PKG_TEXT = TEXT + ", .dx-mine .ui-chip, .dx-mine .lbl, .dx-mine .tree b, .dx-mine .tree .s, .dx-mine .btn"
TILE_TEXT = "ul.decklist .name, ul.decklist .fmt, ul.decklist .fmt .ui-lbl, #pk-note, #decks-total"


def _names(box) -> list[str]:
    return box.locator(".dx-mine .tree label b").all_inner_texts()


def _wait_names(page, box, names: list[str]) -> None:
    page.wait_for_function(
        "(want) => JSON.stringify([...document.querySelectorAll('#package .dx-mine .tree label b')]"
        ".map(b => b.textContent)) === JSON.stringify(want)",
        arg=names,
    )


@pytest.mark.parametrize("width,theme", [(390, "light"), (1366, "dark")])
def test_the_add_a_package_box_has_chips_filters_and_a_load_more_button(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        box = _open_from_deck_menu(page, "Add a package…", "package")
        _wait_names(page, box, ["Golgari staples"])
        assert box.locator(".dx-mine .lbl").inner_text() == "Your packages"
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, PKG_TEXT) == []
        if width < 900:
            assert (
                min(_targets(page, "#package .ui-chip, #package .dx-pkf input, #package .dx-pkf .msel-btn"))
                >= 43.5
            )
        # Followed: alice follows Amy burn; All: everyone's public packages
        box.locator(".ui-chip[data-whose=followed]").click()
        _wait_names(page, box, ["Amy burn"])
        assert box.locator(".dx-mine .lbl").inner_text() == "Packages you follow"
        box.locator(".ui-chip[data-whose=all]").click()
        _wait_names(page, box, ["Amy shocks", "Amy burn", "Golgari staples"])  # most imported first
        assert box.locator(".ui-chip[data-whose=all]").get_attribute("aria-pressed") == "true"
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, PKG_TEXT) == []
        # the name as you type (after a pause), the format and the budget on selection
        box.locator("#dx-pkq").fill("shocks")
        _wait_names(page, box, ["Amy shocks"])
        box.locator("#dx-pkq").fill("")
        box.locator("#dx-pkq").press("Enter")  # asks at once, never sends the form
        _wait_names(page, box, ["Amy shocks", "Amy burn", "Golgari staples"])
        assert page.url.endswith("/decks/42?view=text")
        box.locator("#dx-pkfmt").select_option("modern")
        _wait_names(page, box, ["Amy burn"])
        box.locator("#dx-pkfmt").select_option("")
        box.locator("#dx-pkbud").select_option("2")
        _wait_names(page, box, ["Amy shocks", "Golgari staples"])
        box.locator(".ui-chip[data-whose=followed]").click()
        box.locator(".dx-mine .tree p", has_text="No followed packages match.").wait_for(state="visible")
        box.locator(".ui-chip[data-whose=mine]").click()
        box.locator("#dx-pkbud").select_option("1")
        box.locator(".dx-mine .tree p", has_text="None of your packages match.").wait_for(state="visible")
        # Load more is a button, shown while the listing says there is a next page
        server.ark.account.package_page_size = 1
        box.locator("#dx-pkbud").select_option("")
        box.locator(".ui-chip[data-whose=all]").click()
        _wait_names(page, box, ["Amy shocks"])
        more = box.locator(".dx-more button")
        assert more.is_visible() and more.inner_text().strip() == "Load more"
        more.click()
        _wait_names(page, box, ["Amy shocks", "Amy burn"])
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, PKG_TEXT) == []
        more.click()
        _wait_names(page, box, ["Amy shocks", "Amy burn", "Golgari staples"])
        page.wait_for_function("() => document.querySelector('#package .dx-more').hidden")
        # a pick from any list fills the link-or-id field
        box.locator(".dx-mine .tree input[type=radio][value='901']").check()
        assert box.locator("input[name=package]").input_value() == "901"
        if width < 900:
            page.evaluate(TEXT_200)
            page.wait_for_timeout(100)
            assert page.evaluate(MEASURE) == []
        browser.close()
    assert errors == []


def _show(page, width: int, value: str) -> None:
    """Pick a Show choice: on a phone the filters are a sheet behind the Filters button."""
    if width < 600:
        page.locator(".sp-open").click()
        page.locator("#f-show").wait_for(state="visible")
    page.locator("#f-show").select_option(value)
    if width < 600:
        page.keyboard.press("Escape")
        page.wait_for_timeout(200)


@pytest.mark.parametrize("width,theme", [(390, "dark"), (1366, "light")])
def test_my_decks_show_filter_marks_package_tiles_and_adds_one_to_a_deck(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        tile = page.locator("li.deck[data-deck-id='50']")
        page.locator("li.deck[data-deck-id='50'][data-package='1']").wait_for(state="visible")
        assert "Package" in tile.locator(".fmt").inner_text() and "$$" in tile.locator(".fmt").inner_text()
        assert tile.locator(".thumb .ini svg").count() == 1
        assert page.locator("li.deck[data-deck-id='42']").get_attribute("data-package") is None
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TILE_TEXT) == []
        # Show applies on selection, in place: no second listing, the address follows
        _show(page, width, "packages")
        page.locator("li.deck[data-deck-id='42']").wait_for(state="hidden")
        assert tile.is_visible() and page.locator("#decks-total").inner_text() == "1 package"
        assert "show=packages" in page.url
        assert page.locator("#fv-decks").inner_text().startswith("Packages")
        _show(page, width, "decks")
        tile.wait_for(state="hidden")
        assert page.locator("li.deck[data-deck-id='42']").is_visible()
        assert page.locator("#decks-total").inner_text() == "1 deck"
        _show(page, width, "")
        tile.wait_for(state="visible")
        assert page.locator("li.deck[data-deck-id='42']").is_visible() and "show=" not in page.url
        # the package tile's menu: Add to a deck… (a plain deck's has none), from ⋮, a right-click
        # and a press and hold
        page.locator("li.deck[data-deck-id='42'] button.kebab").click()
        assert "Add to a deck…" not in _menu_items(page)
        _close_menu(page)
        tile.locator(".name").click(button="right")
        assert "Add to a deck…" in _menu_items(page)
        _close_menu(page)
        if width < 900:
            page.evaluate(HOLD, "li.deck[data-deck-id='50'] .name")
            page.wait_for_timeout(900)
            assert "Add to a deck…" in _menu_items(page)
            _close_menu(page)
        tile.locator("button.kebab").click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Add to a deck…").click()
        box = page.locator(".mtgbox:not([hidden])")
        box.wait_for(state="visible")
        assert "Add “Golgari staples” to a deck" in box.locator("h2").inner_text()
        sel = box.locator("select")
        sel.wait_for(state="attached")
        names = sel.locator("option").all_inner_texts()
        assert "Sample Commander Deck" in names and "Golgari staples" not in names, names
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TEXT) == []
        if width < 900:
            assert min(_targets(page, ".mtgbox .acts button")) >= 43.5
        sel.select_option(label="Sample Commander Deck")
        box.locator(".acts button", has_text="Next").click()
        page.wait_for_url("**/decks/42/package?package=50")
        pkg = page.locator("#package")
        pkg.wait_for(state="visible")
        assert pkg.locator("input[name=package]").input_value() == "50"
        page.wait_for_function(
            "() => document.querySelector('#package .dx-mine input[value=\"50\"]')?.checked"
        )
        assert pkg.locator("input[name=skip_existing]").is_checked()
        browser.close()
    assert errors == []


def test_my_decks_draws_a_package_the_deck_list_left_out_and_says_when_it_cannot_tell(
    server: Server,
) -> None:
    import json

    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366, "light", exe)
        # the packages read names one the deck listing does not carry: its tile is drawn from it
        ghost = {
            "id": "777",
            "name": "Ghost package",
            "line": "alice · 3 cards · $ · Modern",
            "budget": "$",
            "formats": "Modern",
        }
        page.route(
            "**/decks/api/packages**",
            lambda r: r.fulfill(
                content_type="application/json",
                body=json.dumps({"ok": True, "packages": [ghost], "more": False}),
            ),
        )
        page.goto(f"{server.base}/decks?show=packages", wait_until="networkidle")
        extra = page.locator("li.deck[data-deck-id='777']")
        extra.wait_for(state="visible")
        assert extra.locator("a").get_attribute("href") == "/decks/777"
        assert "Ghost package" in extra.inner_text()
        assert extra.locator(".fmt").inner_text().strip() == "Package · $"  # the formats: its title
        assert extra.locator(".fmt").get_attribute("title") == "Package · $ · Modern"
        assert page.locator("li.deck[data-deck-id='42']").is_hidden()
        assert page.locator("#decks-total").inner_text() == "1 package"
        extra.locator("button.kebab").click()
        assert "Add to a deck…" in _menu_items(page)
        _close_menu(page)
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TILE_TEXT) == []
        page.unroute("**/decks/api/packages**")
        # the read fails: one muted line, every deck shown, Show still usable
        page.route("**/decks/api/packages**", lambda r: r.abort())
        page.goto(f"{server.base}/decks?show=packages", wait_until="networkidle")
        note = page.locator("#pk-note")
        note.wait_for(state="visible")
        assert (
            note.inner_text() == "Packages couldn't be told apart from decks just now; every deck is shown."
        )
        assert page.locator("li.deck[data-deck-id='42']").is_visible()
        assert page.locator("li.deck[data-deck-id='50']").is_visible()
        assert page.locator("#f-show").is_enabled()
        assert page.evaluate(CONTRAST, TILE_TEXT) == []
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", [(390, "light"), (1366, "dark")])
def test_the_new_deck_and_new_package_boxes_pick_a_folder(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        made = page.evaluate(
            "() => fetch('/decks/api/folders', {method: 'POST', credentials: 'same-origin', headers: "
            "{'Content-Type': 'application/json', "
            "'X-CSRF-Token': document.querySelector('input[name=csrf]').value}, "
            "body: JSON.stringify({name: 'Brews', parent_id: null})}).then(r => r.json())"
        )
        assert made["ok"], made
        page.goto(f"{server.base}/decks", wait_until="networkidle")
        fid = page.locator("li.folder[data-folder-id]").first.get_attribute("data-folder-id")
        for opener, box_id, sel_id in (
            ("newdeck", "newdeck", "nd-folder"),
            ("newpkg", "newpkg", "np-folder"),
        ):
            page.locator(f"[data-box-open='{opener}']").click()
            box = page.locator(f"#{box_id}")
            box.wait_for(state="visible")
            page.wait_for_timeout(300)
            sel = box.locator(f"#{sel_id}")
            assert sel.locator("option").all_inner_texts() == ["Top level (no folder)", "Brews"]
            assert sel.input_value() == ""
            sel.select_option(fid)
            page.wait_for_timeout(100)
            assert box.locator(f"#{sel_id} + .msel-btn, .msel-btn").filter(has_text="Brews").count() >= 1
            assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TEXT) == []
            _close_box(page)
        # opened from inside the folder, the box starts on it
        page.goto(f"{server.base}/decks?folder={fid}", wait_until="networkidle")
        page.locator("[data-box-open='newdeck']").click()
        page.locator("#newdeck").wait_for(state="visible")
        assert page.locator("#nd-folder").input_value() == fid
        _close_box(page)
        page.locator("[data-box-open='newpkg']").click()
        page.locator("#newpkg").wait_for(state="visible")
        assert page.locator("#np-folder").input_value() == fid
        browser.close()
    assert errors == []
