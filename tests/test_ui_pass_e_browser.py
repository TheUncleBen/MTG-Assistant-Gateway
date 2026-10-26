"""The 0.7.44 UI review's fixes in headless Chromium (the independent review of 0.7.43): the current
option of every drop-down list at 4.5:1 (H1); the Account switches on their label's line with no
repeated words, and Price sources' help level with its first picker (M2); a big removal asking in
the shared box, ending on the solid red button, with a question left open rejected when the page
is left (M3); Enter a deck… starting on "Pick a deck…" with no packages (M4); the Approval mode
chevron and the New custom card switch at 44 px on touch (M5); the forum's phone rows on two lines
(M6); a deck comment's ⋮ at the row's edge, the vote button's ▲ and the Post reply icon (L2, L3,
L4); a press and hold whose lift leaves the menu open (L5); "Add categories" when nothing is
ticked (L6); a count on every My lists tab and the current tab kept in view at 200% text (L7);
the New color tag preview's name in the text colour (L8); the New custom card box's type fields
and Artist row (L9); the Notifications rows lined up (L14); Package settings' Budget in the
controls' column (L15); the card viewer's × and steppers at 44 px on touch (L21); and a thread's
line before Open on Archidekt (L12). Skipped without a Chromium build."""

from __future__ import annotations

import copy

import pytest

from mtg_gateway import account_lists

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_deck_custom_cards import _custom
from .test_editor_browser import _link
from .test_forum_page import _reply
from .test_overflow_sweep_browser import MEASURE
from .test_profile_browser import NOTES
from .test_suggest_browser import Server
from .test_text_zoom_browser import TEXT_200
from .test_ui_pass_c_browser import _close_box, _close_menu, _seed

BOX_TEXT = ".mtgbox h2, .mtgbox p, .mtgbox label, .mtgbox .acts button"


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path, writes_enabled=True)
    ark = s.ark
    _seed(ark)
    a = ark.account
    # a third custom card with 20 copies: the removal that asks first
    a.customs["alice"].append(_custom(45, "Spirit Token Pile"))
    ark.decks[42]["customCards"].append(
        {"id": 3102, "quantity": 20, "categories": ["Artifact"], "card": _custom(45, "Spirit Token Pile")}
    )
    # a card package of alice's (deck 50), for Enter a deck… and Package settings
    pkg = copy.deepcopy(ark.decks[42])
    pkg.update(
        id=50,
        name="Golgari staples",
        deckFormat=None,
        customCards=[],
        cardPackage={
            "id": 900,
            "keywords": "golgari, staples",
            "budget": 2,
            "intendedFormats": [3],
            "importCount": 0,
        },
    )
    pkg["categories"] = [c for c in pkg["categories"] if c["name"] != "Commander"]
    ark.decks[50] = pkg
    ark.users["alice"]["decks"].append(50)
    # deck comments on deck 42: amy's comment and alice's reply
    c1 = _reply(ark, 300042, "amy", "Try Cultivate here, it fits the ramp plan.")
    _reply(ark, 300042, "alice", "Good call, swapping it in.", parent=c1)
    # notifications: two unseen rows and one read
    a.notes["alice"] = [dict(n) for n in NOTES[:2]] + [{**NOTES[0], "text": "An older one", "seen": True}]
    s.start()
    try:
        yield s
    finally:
        s.stop()


def _box(page, sel: str):
    return page.evaluate(
        "(s) => { const r = document.querySelector(s).getBoundingClientRect();"
        " return {x: r.x, y: r.y, w: r.width, h: r.height, r: r.right, b: r.bottom}; }",
        sel,
    )


def _account(page, base: str) -> None:
    """The Account page with its Archidekt cards (a slow first read draws the page without them)."""
    page.goto(f"{base}/account/details", wait_until="networkidle")
    for _ in range(3):
        if page.locator("#pr-group_by-btn").count():
            break
        page.wait_for_timeout(1500)
        page.reload(wait_until="networkidle")
    page.locator("#pr-group_by-btn").wait_for(state="visible")


def _hold(page, loc) -> None:
    """A finger's press and hold, as Chromium's touch emulation sends it (touchStart, 650 ms, touchEnd)."""
    loc.scroll_into_view_if_needed()
    b = loc.bounding_box()
    cdp = page.context.new_cdp_session(page)
    x, y = b["x"] + min(b["width"] / 2, 40), b["y"] + b["height"] / 2
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
    page.wait_for_timeout(650)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    page.wait_for_timeout(500)


@pytest.mark.parametrize("width,theme", [(390, "light"), (390, "dark"), (1280, "light"), (1280, "dark")])
def test_the_current_option_of_a_list_meets_contrast(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        _account(page, server.base)
        btn = page.locator("#pr-group_by-btn")
        btn.scroll_into_view_if_needed()
        btn.click()
        page.locator(".msel-list").wait_for(state="visible")
        page.wait_for_timeout(300)
        current = page.locator(".msel-list [role=option][aria-selected=true]")
        assert current.count() == 1
        # the orange words of the current option on their fill: 4.5:1 (they were 3.7:1 light, 2.6:1 dark)
        assert page.evaluate(CONTRAST, ".msel-list [role=option]") == []
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", [(390, "light"), (1280, "dark")])
def test_the_account_switch_rows_and_the_price_sources_row(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        _account(page, server.base)
        page.locator("#email").scroll_into_view_if_needed()
        # no repeated label beside the switch: the row's bold label alone, the switch's for readers
        assert page.locator("#email .sw-text").count() == 0
        assert (
            page.locator("#email .ui-row.sw .switch .sr-only").first.inner_text()
            == "Other members can contact me"
        )
        rows = page.locator("#email .ui-row.sw")
        assert rows.count() == 4
        lefts = []
        for i in range(4):
            lab = rows.nth(i).locator(".lab").bounding_box()
            sw = rows.nth(i).locator(".switch").bounding_box()
            assert sw["height"] >= 44
            # the switch sits on the label's line, at the row's end, never under the help
            assert sw["y"] <= lab["y"] + 8, (i, lab, sw)
            assert sw["x"] >= lab["x"] + lab["width"] - 1, (i, lab, sw)
            lefts.append(round(sw["x"]))
        assert max(lefts) - min(lefts) <= 1, lefts  # no zigzag
        # Price sources: the label and its help at the top, level with the first picker
        two = page.locator("#defaults .ui-row.two")
        two.scroll_into_view_if_needed()
        lab = two.locator(".lab").bounding_box()
        help_ = two.locator(".help").bounding_box()
        first = two.locator(".pr-two .msel-btn, .pr-two select:not(.msel-native)").first.bounding_box()
        if width >= 600:
            # the label level with the first picker, the help straight under the label (it sat 40 px
            # lower, beside the second picker)
            assert abs(lab["y"] - first["y"]) <= 8, (lab, first)
            assert help_["y"] <= first["y"] + first["height"] + 6, (help_, first)
        assert page.evaluate(MEASURE) == []
        browser.close()
    assert errors == []


def _reload_with_the_list_script_late(page, server: Server) -> None:
    """Reload with the strip's script (static/proposals.js) held back until the server has taken
    the owed reject, or for 1.5 s when it is not sent before the page's scripts have all run.
    Under load the reject's answer could come back before that later deferred script was
    listening for mtg:rejected, and the row stayed (the 8 s wait below timed out on 4 workers);
    cardmenu.js now settles at DOMContentLoaded, and this makes the late script happen every run."""
    cdp = page.context.new_cdp_session(page)
    held: list[str] = []
    cdp.on("Fetch.requestPaused", lambda e: held.append(e["requestId"]))
    cdp.send("Fetch.enable", {"patterns": [{"urlPattern": "*/static/proposals.js*"}]})
    page.reload(wait_until="commit")
    for _ in range(100):  # the request for the script, at most 5 s
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the deck page did not ask for static/proposals.js"
    for _ in range(30):  # the server takes the reject (the page is not told yet), at most 1.5 s
        if server.db.count_pending_proposals("user-1") == 0:
            break
        page.wait_for_timeout(50)
    page.wait_for_timeout(200)  # time for its answer to reach the page
    for rid in held:
        cdp.send("Fetch.continueRequest", {"requestId": rid})
    cdp.send("Fetch.disable")
    page.wait_for_load_state("networkidle")


@pytest.mark.parametrize("width,theme", [(390, "dark"), (1280, "light")])
def test_a_big_removal_asks_in_a_box_and_a_question_left_open_is_rejected(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    ark = server.ark

    def copies() -> int:
        return next(r["quantity"] for r in ark.decks[42]["customCards"] if r["card"]["id"] == 45)

    def ask(page) -> None:
        row = page.locator(".deckview .row[data-custom-id='45']")
        row.wait_for(state="visible")
        if width < 900:
            _hold(page, row)  # the deck page's own hold (static/deck.js), as its other cards
        else:
            row.click(button="right")
        page.locator(".ctxmenu[role=menu]").wait_for(state="visible")
        page.locator(".ctxmenu [role=menuitem]", has_text="Remove from deck").click()
        page.locator(".mtgbox-scrim .mtgbox").wait_for(state="visible", timeout=8000)
        page.wait_for_timeout(400)

    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        ask(page)
        box = page.locator(".mtgbox-scrim .mtgbox")
        assert box.locator("h2").inner_text() == "Remove 20 copies of “Spirit Token Pile”?"
        text = box.locator(".body").inner_text()
        assert "They come out of “Artifact”." in text
        assert "Restoring a snapshot doesn't put custom cards back." in text
        # the box ends on the solid red button; no orange one, no toast
        acts = box.locator(".acts button")
        assert acts.all_inner_texts() == ["Keep them", "Remove 20 cards"]
        assert acts.last.evaluate("b => b.classList.contains('danger')")
        assert box.locator(".acts .btn-primary, .acts .primary").count() == 0
        assert page.locator(".deck-toast[role=alertdialog]").count() == 0
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, BOX_TEXT) == []
        if width < 900:
            assert all(
                h >= 44 for h in acts.evaluate_all("els => els.map(e => e.getBoundingClientRect().height)")
            )
        # the question's proposal waits while the box is open; leaving the page rejects it
        assert server.db.count_pending_proposals("user-1") == 1
        _reload_with_the_list_script_late(page, server)
        # the leaving request is lost under the service worker (as on the site), so the next page
        # rejects it (static/cardmenu.js), and its row leaves the deck's Proposals box and the
        # pill's count goes (static/proposals.js)
        page.wait_for_function("() => !document.querySelector('.banner .soc.prop .nb')", timeout=8000)
        assert server.db.count_pending_proposals("user-1") == 0
        # the note was the member's own (security review 0.7.44 S-03), and it is settled now
        owed = page.evaluate(
            "() => Object.keys(localStorage).filter(k => k.startsWith('mtg_reject_owed'))"
            ".map(k => [k, localStorage.getItem(k)])"
        )
        assert all(k.startswith("mtg_reject_owed:") and v == "[]" for k, v in owed), owed
        page.reload(wait_until="networkidle")
        assert copies() == 20 and page.locator(".banner .soc.prop .nb").count() == 0
        # saying no rejects it too, and the card is back where it was
        ask(page)
        page.locator(".mtgbox .acts button", has_text="Keep them").click()
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        page.wait_for_timeout(400)
        assert server.db.count_pending_proposals("user-1") == 0
        assert page.locator(".deckview .row[data-custom-id='45']").count() == 1 and copies() == 20
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", [(390, "light"), (1280, "dark")])
def test_enter_a_deck_starts_on_a_pick_and_leaves_packages_out(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/forum/thread/700007", wait_until="networkidle")
        page.locator("#fm-contest .btn", has_text="Enter a deck").click()
        box = page.locator("#enter")
        box.wait_for(state="visible")
        page.wait_for_function(
            "() => !document.querySelector('#enter select[name=deck]').disabled", timeout=8000
        )
        page.wait_for_timeout(300)
        opts = page.evaluate(
            "() => [...document.querySelectorAll('#enter select[name=deck] option')]"
            ".map(o => [o.value, o.textContent, o.disabled, o.selected])"
        )
        assert opts[0] == ["", "Pick a deck…", True, True], opts
        names = [o[1] for o in opts[1:]]
        assert "Sample Commander Deck" in names and "Golgari staples" not in names, names  # no package
        if box.locator(".msel-btn").count():
            assert box.locator(".msel-btn .msel-txt").inner_text() == "Pick a deck…"
        # nothing picked: the entry is refused before anything is sent
        box.locator("textarea[name=summary]").fill("A deck")
        box.locator(".acts button[type=submit]").click()
        assert "Pick a deck first." in box.locator(".boxnote").inner_text()
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, BOX_TEXT) == []
        # a pick is kept when the box reopens
        page.select_option("#enter select[name=deck]", "42")
        _close_box(page)
        page.locator("#fm-contest .btn", has_text="Enter a deck").click()
        box.wait_for(state="visible")
        assert page.locator("#enter select[name=deck]").input_value() == "42"
        browser.close()
    assert errors == []


def test_the_two_new_controls_are_finger_sized_on_touch(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        # a phone: Approval mode's chevron is 44 px and each mode's name keeps its one row
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        _account(page, server.base)
        page.locator("#mode .mode-list").scroll_into_view_if_needed()
        for i in range(page.locator("#mode details.mode-info > summary").count()):
            b = page.locator("#mode details.mode-info > summary").nth(i).bounding_box()
            assert b["width"] >= 44 and b["height"] >= 44, b
        lines = page.evaluate(
            "() => [...document.querySelectorAll('#mode .mode-pick > span')]"
            ".map(s => s.getClientRects().length)"
        )
        assert lines and all(n == 1 for n in lines), lines
        assert page.evaluate(MEASURE) == []
        browser.close()
        # the Fold: the New custom card box's switch is 44 px tall outside .lp (the box moves into the scrim)
        browser, page, errors2 = _page(p, server, sid, 810, "light", exe)
        page.goto(f"{server.base}/lists/custom", wait_until="networkidle")
        page.locator(".lp .btn", has_text="New custom card").first.click()
        box = page.locator("#cc-box")
        box.wait_for(state="visible")
        page.wait_for_timeout(300)
        sw = box.locator(".lp-switch")
        sw.scroll_into_view_if_needed()
        assert sw.bounding_box()["height"] >= 44
        browser.close()
    assert errors == [] and errors2 == []


def test_phone_thread_rows_have_two_lines(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "dark", exe)
        page.goto(f"{server.base}/forum/Archidekt/General", wait_until="networkidle")
        row = page.locator(".fm a.tr[data-id='700002']")
        sub = row.locator(".sub")
        # text_content: innerText breaks between flex items even when they sit on one line
        words = " ".join(sub.text_content().split())
        assert words.startswith("amy · ") and words.count(" · ") == 2 and words.endswith("ago"), words
        # one line: owner · replies · when (the date was a third line)
        size = sub.evaluate("e => parseFloat(getComputedStyle(e).fontSize)")
        assert sub.bounding_box()["height"] <= size * 1.6
        # on the same line (the date's smaller print sits a pixel or two lower)
        assert abs(sub.locator(".fm-when").bounding_box()["y"] - sub.bounding_box()["y"]) < 3
        # the date's dot follows the replies at one space, as the first dot does (the row's flex
        # gap opened a wider space before it)
        gap = sub.evaluate(
            """e => { const w = e.querySelector('.fm-when'), r = document.createRange();
              r.setStart(w.previousSibling, w.previousSibling.length - 1);
              r.setEnd(w.previousSibling, w.previousSibling.length);
              return w.getBoundingClientRect().left - r.getBoundingClientRect().right; }"""
        )
        assert gap < 1, gap
        assert page.evaluate(MEASURE) == []
        browser.close()
    assert errors == []


def test_comment_menus_the_vote_button_and_the_post_reply_icon(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 810, "light", exe)
        # the deck page: a comment's ⋮ at the row's right edge, as on the forum; the ▲ vote button
        page.goto(f"{server.base}/decks/42", wait_until="networkidle")
        page.evaluate("() => document.getElementById('comments').scrollIntoView()")
        page.locator("#comments .cmt .who .kebab").first.wait_for(state="visible", timeout=15000)
        who = _box(page, "#comments .cmt .who")
        kebab = _box(page, "#comments .cmt .who .kebab")
        assert abs(who["r"] - kebab["r"]) <= 2, (who, kebab)
        assert kebab["w"] >= 44 and kebab["h"] >= 44
        vote = page.locator("#comments .cmt .acts button.vote").first
        assert vote.locator("svg.i path").get_attribute("d") == "M6 15l6-6 6 6"
        assert vote.get_attribute("aria-label").startswith("Vote up (")
        assert "+" not in vote.inner_text()
        # the forum: Post this reply? carries the send icon on its button
        page.goto(f"{server.base}/forum/thread/700002", wait_until="networkidle")
        page.locator("#ctext").fill("Sol Ring")
        page.locator("form.newcomment button[type=submit]").click()
        box = page.locator(".mtgbox-scrim .mtgbox")
        box.wait_for(state="visible")
        ok = box.locator(".acts button.primary")
        assert ok.inner_text().strip() == "Post reply"
        assert ok.locator("svg.i path").get_attribute("d") == "M4 12l16-8-6 16-2-7z"
        _close_box(page)
        browser.close()
    assert errors == []


def test_a_hold_opens_the_menu_and_the_lift_leaves_it_open(server: Server) -> None:
    """The lift of the finger that held lands as a click on the phone's sheet scrim (the sheet
    rises over the row); the scrim must not close the menu it just opened."""
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        for url, sel in (
            ("/forum/thread/700002", ".comments .cmt[data-owner='alice'] > p"),
            ("/lists/custom", ".lp .ui-table .tr[data-row]"),
            ("/decks/42", ".comments .cmt[data-owner='amy'] > p"),
        ):
            page.goto(f"{server.base}{url}", wait_until="networkidle")
            if url.startswith("/decks/"):
                page.evaluate("() => document.getElementById('comments').scrollIntoView()")
            loc = page.locator(sel).first
            loc.wait_for(state="visible", timeout=15000)
            _hold(page, loc)
            assert page.locator(".ctxmenu").count() == 1, url
            _close_menu(page)
        browser.close()
    assert errors == []


def test_the_add_template_box_says_add_categories_when_nothing_is_ticked(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/decks/42/settings", wait_until="networkidle")
        page.locator("[data-box-open='tplbox']").click()
        box = page.locator("#tplbox")
        box.wait_for(state="visible")
        box.locator("input[name=template]").first.wait_for(state="visible")
        go = box.locator(".acts button[type=submit]")
        assert go.inner_text().strip() == "Add categories" and go.is_disabled()
        box.locator("input[name=template]").first.check()
        assert go.inner_text().strip() == "Add 1 category" and go.is_enabled()
        browser.close()
    assert errors == []


def test_my_lists_tabs_count_and_the_current_tab_stays_in_view(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    account_lists.forget_counts("user-1")
    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        page.goto(f"{server.base}/lists/custom", wait_until="networkidle")
        # a first visit: every tab has its count, not the open one alone
        assert page.locator(".lp-tabs a .n").count() == 5
        # larger text after the load: the current tab is brought back into view
        page.evaluate(TEXT_200)
        page.wait_for_timeout(400)
        cur = page.locator(".lp-tabs a[aria-current]").bounding_box()
        assert cur["x"] >= 0 and cur["x"] + cur["width"] <= 390, cur
        assert page.evaluate(MEASURE) == []
        browser.close()
    assert errors == []


def test_the_tag_preview_words_and_the_custom_card_box_layout(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        page.goto(f"{server.base}/lists/tags", wait_until="networkidle")
        page.locator(".lp .btn", has_text="New").first.click()
        box = page.locator("#tag-box")
        box.wait_for(state="visible")
        page.wait_for_timeout(300)
        # the preview chip: the colour on its dot and border, the name in the text colour
        name = box.locator("#tag-pv .nm")
        assert name.evaluate("e => getComputedStyle(e).color") == page.evaluate(
            "() => getComputedStyle(document.body).color"
        )
        assert page.evaluate(CONTRAST, "#tag-pv .nm") == []
        _close_box(page)
        page.goto(f"{server.base}/lists/custom", wait_until="networkidle")
        page.locator(".lp .btn", has_text="New custom card").first.click()
        box = page.locator("#cc-box")
        box.wait_for(state="visible")
        page.wait_for_timeout(300)
        # the three type fields line up at their inputs
        bottoms = page.evaluate(
            "() => [...document.querySelector('#cc-face-f .lp-three').querySelectorAll('input')]"
            ".map(i => Math.round(i.getBoundingClientRect().bottom))"
        )
        assert len(bottoms) == 3 and max(bottoms) - min(bottoms) <= 1, bottoms
        # the Artist row clear of the picture's help
        help_ = _box(page, "#cc-face-f .lp-pichelp")
        artist = _box(page, "#cc-face-f .lp-img .field")
        assert artist["y"] - help_["b"] >= 10, (help_, artist)
        assert page.evaluate(MEASURE) == []
        browser.close()
    assert errors == []


def test_notification_rows_line_up(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "dark", exe)
        page.goto(f"{server.base}/notifications", wait_until="networkidle")
        assert page.locator(".bell-row.new").count() == 2
        assert page.locator(".bell-row:not(.new)").count() == 1
        xs = page.evaluate(
            "() => [...document.querySelectorAll('.bell-row i')]"
            ".map(i => Math.round(i.getBoundingClientRect().x))"
        )
        assert max(xs) - min(xs) <= 1, xs  # the read row's avatar and words under the unread rows'
        browser.close()
    assert errors == []


def test_package_settings_budget_sits_in_the_controls_column(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1280, "light", exe)
        page.goto(f"{server.base}/decks/50/settings", wait_until="networkidle")
        page.locator("#package").scroll_into_view_if_needed()
        kw = _box(page, "#pkg-kw")
        budget = _box(page, "#package .budget")
        assert abs(kw["x"] - budget["x"]) <= 1, (kw, budget)
        help_ = page.locator("#package .ui-row", has_text="Budget").locator(".help").inner_text()
        assert help_ == "How much the cards cost, roughly."
        browser.close()
    assert errors == []


def test_the_card_viewers_controls_are_finger_sized_on_touch(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        row = page.locator(".deckview .row[data-custom-id='41']")
        row.scroll_into_view_if_needed()
        row.tap()
        page.locator(".cardview.open").wait_for(timeout=5000)
        page.wait_for_timeout(400)
        close = _box(page, ".cardview .close")
        assert close["w"] >= 44 and close["h"] >= 44, close
        steps = page.evaluate(
            "() => [...document.querySelectorAll('.cardview .acts .qtyctl button')]"
            ".map(b => [Math.round(b.getBoundingClientRect().width),"
            " Math.round(b.getBoundingClientRect().height)])"
        )
        assert steps and all(w >= 44 and h >= 44 for w, h in steps), steps
        browser.close()
    assert errors == []


def test_a_threads_line_comes_before_open_on_archidekt_on_a_phone(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "dark", exe)
        page.goto(f"{server.base}/forum/thread/700002", wait_until="networkidle")
        sub = _box(page, ".fm .ui-sub")
        btn = _box(page, ".fm .fm-links .btn")
        title = _box(page, ".fm h1")
        assert title["b"] <= sub["y"] + 1 <= btn["y"], (title, sub, btn)
        assert page.locator(".fm .ui-head.phead").count() == 0
        browser.close()
    assert errors == []
