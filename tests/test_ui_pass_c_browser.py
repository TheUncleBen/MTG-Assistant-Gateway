"""The UI pass over the Forum, the Deck page's custom cards and My lists (static/forum.js,
social.js, deck.js, lists.js) in headless Chromium: the thread table's Section column and two-line
rows on a phone; a post's ⋮ menu from the button, a right-click and a press and hold, with Report
comment… opening its box; the reply box's Bold, Italic and Link buttons and the Post this reply?
box with the reply as it will appear; a contest thread's strip with Enter a deck… and Withdraw…;
a custom card's menu on the Deck page (count, Move to, Remove with Undo, Lock not offered, Report
card…) saving through the page's own endpoint; Add to deck… on My lists › Custom cards. Every
screen fits the Fold's cover, a phone and a desktop in both themes with 44 px targets and 4.5:1
text, and holds together at 200% text. Skipped without a Chromium build."""

from __future__ import annotations

import json

import pytest

from .test_admin_card_models_browser import HOLD
from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_deck_custom_cards import _custom
from .test_deck_custom_cards import _seed as _customs
from .test_deck_extras import _ramp, _templates
from .test_editor_browser import _link
from .test_forum_page import _contest, _reply
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server
from .test_text_zoom_browser import TEXT_200

TEXT = (
    ".fm .ui-table .t, .fm .ui-table .sub, .fm .ui-table .th span, .fm .fm-section, .fm .fm-post .text, "
    ".fm .fm-post .ph, .fm .comments h2, .comments .cmt p, .comments .cmt .who, .comments label, "
    ".comments .muted, .comments .rt button, .comments .pub, .fm .btn, .dx-strip b, .dx-strip .muted, "
    ".dx-strip .btn, .entries summary, .entries li, .ctxmenu [role=menuitem], .ctxmenu .head, "
    ".mtgbox h2, .mtgbox p, .mtgbox label, .mtgbox .acts button, .mtgbox .notice, .mtgbox .post-preview, "
    ".mtgbox .ui-lbl, .deckview .row .name, .deckview .custom-badge, .lp .ui-table .t, .lp-under span"
)
TARGETS = (
    ".fm a.tr, .fm .btn, .fm .fm-post .kebab, .comments .cmt .who .kebab, .comments .cmt .acts button, "
    ".comments .rt button, "
    ".comments form.newcomment button, .dx-strip .btn, .ctxmenu [role=menuitem], .mtgbox .acts button, "
    ".mtgbox .bx, .lp .ui-table .kebab"
)
SIZES = [(344, "dark"), (390, "light"), (1280, "dark"), (1280, "light")]


def _seed(ark) -> None:
    first = _reply(ark, 700002, "amy", "Arcane Signet, every time.")
    _reply(ark, 700002, "alice", "And Sol Ring.", parent=first)
    _contest(ark)
    _customs(ark)
    _templates(ark)
    _ramp(ark)
    ark.decks[42]["customCards"] = [
        {"id": 3100, "quantity": 1, "categories": ["Ramp"], "card": _custom(41, "Grizzled Mentor")}
    ]


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


def _fits(page, where: str, width: int) -> None:
    assert page.evaluate(MEASURE) == [], where
    assert page.evaluate(CONTRAST, TEXT) == [], where
    if width < 900:
        sizes = _targets(page, TARGETS)
        assert sizes and min(sizes) >= 43.5, (where, sizes)


def _items(page) -> list[str]:
    return [t.strip() for t in page.locator(".ctxmenu [role=menuitem]").all_inner_texts()]


def _close_menu(page) -> None:
    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    assert page.locator(".ctxmenu").count() == 0


def _close_box(page) -> None:
    page.keyboard.press("Escape")
    page.locator(".mtgbox-scrim").wait_for(state="detached")
    page.wait_for_timeout(300)


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_thread_table_and_the_posts_menus_fit_and_open_every_way(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/forum/Archidekt/General", wait_until="networkidle")
        _fits(page, "section", width)
        row = page.locator(".fm a.tr[data-id='700002']")
        # no Section column or chip: the page's heading names the section (every row is in it)
        assert page.locator(".fm .ui-table .th span", has_text="Section").count() == 0
        assert row.locator(".fm-section").count() == 0 and "General" not in row.inner_text()
        if width >= 900:
            # the Last activity column keeps the row as tall as the others: who and when on one line
            last = row.locator(".fm-last")
            assert last.locator(".fm-by").inner_text() == "amy" and last.locator("time").count() == 1
            assert last.evaluate("e => e.getBoundingClientRect().height") <= row.locator(".title").evaluate(
                "e => e.getBoundingClientRect().height"
            )
        # the thread: the first post's ⋮ opens its menu; so do a right-click and a press and hold
        page.goto(f"{server.base}/forum/thread/700002", wait_until="networkidle")
        page.locator(".comments .cmt .who .kebab").first.wait_for(state="visible")
        _fits(page, "thread", width)
        post = page.locator(".fm-post")
        post.locator(".kebab").click()
        page.locator(".ctxmenu").wait_for(state="visible")
        assert _items(page) == ["Reply", "Copy link", "Open on Archidekt", "Report comment…"]
        assert page.locator(".ctxmenu .head").inner_text() == "amy's post"
        _fits(page, "post menu", width)
        _close_menu(page)
        post.locator(".text").click(button="right")
        page.locator(".ctxmenu").wait_for(state="visible")
        assert _items(page)[0] == "Reply"
        _close_menu(page)
        if width < 900:
            page.evaluate(HOLD, ".fm-post .text")
            page.wait_for_timeout(900)
            assert page.locator(".ctxmenu").count() == 1
            _close_menu(page)
        # Report comment… fills and opens the box for the first post
        post.locator(".kebab").click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Report comment…").click()
        rc = page.locator("#reportcomment")
        rc.wait_for(state="visible")
        page.wait_for_timeout(400)
        assert rc.locator("h2").inner_text() == "Report amy's comment to Archidekt's moderators"
        assert rc.locator("input[name=comment_id]").input_value() == "700002"
        _fits(page, "report box", width)
        _close_box(page)
        # a reply's ⋮: someone else's offers Vote up, Vote down and Report comment…, the member's own
        # Edit and Delete
        amy = page.locator(".comments .cmt[data-owner='amy']").first
        amy.locator("> .who .kebab").click()
        page.locator(".ctxmenu").wait_for(state="visible")
        assert _items(page) == ["Reply", "Copy link", "Vote up", "Vote down", "Report comment…"]
        _close_menu(page)
        mine = page.locator(".comments .cmt[data-owner='alice']").first
        mine.scroll_into_view_if_needed()
        mine.locator("> p").first.click(button="right")
        page.locator(".ctxmenu").wait_for(state="visible")
        assert _items(page) == ["Reply", "Copy link", "Edit…", "Delete…"]
        _fits(page, "reply menu", width)
        _close_menu(page)
        amy.locator("> .who .kebab").click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Report comment…").click()
        rc.wait_for(state="visible")
        page.wait_for_timeout(400)
        assert rc.locator("input[name=comment_id]").input_value() != "700002"
        assert rc.locator("input[name=who]").input_value() == "amy"
        _close_box(page)
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", [(344, "light"), (390, "dark"), (1280, "light")])
def test_the_text_style_buttons_and_the_post_this_reply_box(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    ark = server.ark
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/forum/thread/700002", wait_until="networkidle")
        page.locator(".comments .cmt .who .kebab").first.wait_for(state="visible")
        page.wait_for_timeout(800)  # the click guard
        ta = page.locator("#ctext")
        ta.fill("Sol Ring")
        page.evaluate("() => document.getElementById('ctext').setSelectionRange(0, 8)")
        page.locator(".rt button[data-mark=bold]").click()
        assert ta.input_value() == "**Sol Ring**"
        page.evaluate("() => { const t = document.getElementById('ctext'); t.setSelectionRange(12, 12); }")
        ta.press("End")
        ta.type(" and ")
        page.locator(".rt button[data-mark=italic]").click()
        assert ta.input_value() == "**Sol Ring** and _italic words_"
        ta.press("End")
        ta.type(" see ")
        page.locator(".rt button[data-mark=link]").click()
        link = page.locator(".mtgbox-scrim .mtgbox")
        link.wait_for(state="visible")
        assert link.locator("h2").inner_text() == "Add a link"
        link.locator(".acts button", has_text="Add link").click()
        assert "Give a web address that starts with http:// or https://." in link.inner_text()
        page.fill("#rt-url", "https://archidekt.com/decks/42")
        link.locator(".acts button", has_text="Add link").click()
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        assert (
            ta.input_value()
            == "**Sol Ring** and _italic words_ see [archidekt.com/decks/42](https://archidekt.com/decks/42)"
        )
        _fits(page, "toolbar", width)
        page.locator("form.newcomment button[type=submit]").click()
        box = page.locator(".mtgbox-scrim .mtgbox")
        box.wait_for(state="visible")
        page.wait_for_timeout(300)
        assert box.locator("h2").inner_text() == "Post this reply?"
        pv = box.locator(".post-preview")
        assert (
            pv.locator("strong").inner_text() == "Sol Ring"
            and pv.locator("em").inner_text() == "italic words"
        )
        assert pv.locator("a").get_attribute("href") == "https://archidekt.com/decks/42"
        assert (
            "Posted publicly under “alice” in this thread on Archidekt. You can edit or delete it afterwards."
            in box.inner_text()
        )
        assert box.locator(".acts button", has_text="Keep editing").count() == 1
        _fits(page, "post box", width)
        if width < 900:
            page.evaluate(TEXT_200)
            page.wait_for_timeout(50)
            assert page.evaluate(MEASURE) == []
        box.locator(".acts button", has_text="Keep editing").click()
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        assert ta.input_value().startswith("**Sol Ring**")  # still in the box
        assert len(ark.comments[700002]) == 2
        page.locator("form.newcomment button[type=submit]").click()
        box.wait_for(state="visible")
        box.locator(".acts button", has_text="Post reply").click()
        page.locator(".comments .cmt", has_text="Sol Ring and italic words").wait_for(
            state="visible", timeout=4000
        )
        doc = json.loads(ark.comments[700002][-1]["text"])
        assert {"insert": "Sol Ring", "attributes": {"bold": True}} in doc["ops"]
        assert {"insert": "italic words", "attributes": {"italic": True}} in doc["ops"]
        assert any(
            op.get("attributes", {}).get("link") == "https://archidekt.com/decks/42" for op in doc["ops"]
        )
        assert ta.input_value() == ""
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", SIZES)
def test_a_contest_thread_enters_and_withdraws_a_deck(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    ark = server.ark
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/forum/thread/700007", wait_until="networkidle")
        strip = page.locator("#fm-contest")
        assert strip.is_visible() and "Contest · No decks entered yet" in strip.inner_text()
        page.locator(".comments .thread p.muted", has_text="No replies yet. Be the first.").wait_for(
            state="visible"
        )
        _fits(page, "contest", width)
        strip.locator(".btn", has_text="Enter a deck…").click()
        box = page.locator("#enter")
        box.wait_for(state="visible")
        page.wait_for_timeout(300)
        assert box.locator("h2").inner_text() == "Enter a deck in “October deck contest”"
        page.wait_for_function("() => !document.querySelector('#enter select[name=deck]').disabled")
        # the list starts on "Pick a deck…" (0.7.44, M4); the member picks the deck
        assert box.locator(".msel-btn").inner_text().strip() == "Pick a deck…"
        box.locator("select[name=deck]").select_option(label="Sample Commander Deck")
        _fits(page, "enter box", width)
        if width < 900:
            page.evaluate(TEXT_200)
            page.wait_for_timeout(50)
            assert page.evaluate(MEASURE) == []
            page.reload(wait_until="networkidle")
            strip.locator(".btn", has_text="Enter a deck…").click()
            box.wait_for(state="visible")
            page.wait_for_function("() => !document.querySelector('#enter select[name=deck]').disabled")
            box.locator("select[name=deck]").select_option(label="Sample Commander Deck")
        # the summary is checked before anything is sent
        box.locator(".acts button", has_text="Review the entry").click()
        assert "A summary is required." in box.inner_text()
        assert ark.account.entries.get(700007, []) == []
        page.fill("#fm-es", "Simic ramp, big sea monsters")
        assert box.locator("#fm-es-c").inner_text().startswith("28 / ")
        box.locator(".acts button", has_text="Review the entry").click()
        page.wait_for_url("**/proposals/**")
        assert ark.account.entries.get(700007, []) == []  # the member applies it on the review page
        # once entered (the review page's press), the strip shows the entry and Withdraw…
        ark.account.entries[700007] = [
            {
                "id": 5,
                "deck": {"id": 42, "name": "Sample Commander Deck"},
                "owner": {"id": ark._user_id("alice"), "username": "alice"},
            }
        ]
        page.goto(f"{server.base}/forum/thread/700007", wait_until="networkidle")
        assert "Your entry: Sample Commander Deck" in strip.inner_text()
        assert (
            page.locator(".entries li", has_text="Sample Commander Deck")
            .locator(".ui-lbl", has_text="You")
            .count()
            == 1
        )
        _fits(page, "entered", width)
        strip.locator(".btn", has_text="Withdraw…").click()
        wd = page.locator("#withdraw")
        wd.wait_for(state="visible")
        page.wait_for_timeout(300)
        assert wd.locator("h2").inner_text() == "Withdraw “Sample Commander Deck” from this contest?"
        _fits(page, "withdraw box", width)
        wd.locator(".acts button", has_text="Withdraw").click()
        page.wait_for_url("**/proposals/**")
        assert len(ark.account.entries[700007]) == 1
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", SIZES)
def test_a_custom_cards_menu_on_the_deck_page(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    ark = server.ark

    def row() -> dict:
        return next(r for r in ark.decks[42]["customCards"] if r["card"]["id"] == 41)

    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        card = page.locator(".deckview .row[data-custom-id='41']")
        card.scroll_into_view_if_needed()
        # one Custom mark, under the name (the Set column is hidden on a phone, so its cell is blank)
        assert card.locator(".set").inner_text() == "" and card.locator(".custom-badge").count() == 1
        assert (
            card.locator("..")
            .locator("xpath=ancestor::section[contains(@class,'stack')]")
            .get_attribute("data-group")
            == "Ramp"
        )
        _fits(page, "deck", width)
        card.click(button="right")
        menu = page.locator(".ctxmenu[role=menu]")
        menu.wait_for(state="visible")
        items = [t.split("\n")[0] for t in _items(page)]
        assert items[0] == "Open card" and "Move to" in items and "Remove from deck" in items, items
        # no Lock personal categories (Archidekt keeps no default categories for a custom card)
        assert "Lock personal categories" not in items and "Copy name" in items
        # Archidekt's own custom card menu offers the colour tag and the mana value
        assert "Color tag…" in items and "Mana value…" in items, items
        # ... and no report of the member's own card (kept on another member's deck)
        for gone in (
            "Change printing…",
            "Edit in deck editor",
            "Open on Scryfall",
            "Add to deck…",
            "Report card…",
        ):
            assert gone not in items, items
        assert menu.locator(".head").inner_text() == "Grizzled Mentor"
        # the deck page's own card menu (static/deck.js), as the deck's other cards have it: its
        # fit and contrast are checked; its item heights are the menu's own, not this pass's
        assert page.evaluate(MEASURE) == [] and page.evaluate(CONTRAST, TEXT) == []
        # one more copy: shown at once, saved through the page's own custom card endpoint
        menu.locator(".qtyrow .step", has_text="+").click()
        assert menu.locator(".qtyrow .n").inner_text() == "2"
        page.wait_for_function(
            "() => document.querySelector(\".row[data-custom-id='41']\").getAttribute('data-qty') === '2'"
        )
        page.wait_for_timeout(1200)
        assert row()["quantity"] == 2
        page.locator(".deck-toast", has_text="Saved Grizzled Mentor: 2 copies.").wait_for(state="visible")
        # Move to: the card files under Artifact, from the menu opened afresh (a page scroll, such as
        # the saved message settling on a narrow screen, closes an open menu by design)
        _close_menu(page) if page.locator(".ctxmenu").count() else None
        card.click(button="right")
        menu.wait_for(state="visible")
        menu.locator(".more").click()
        menu.locator(".sub button", has_text="Artifact").click()
        page.wait_for_timeout(1200)
        assert row()["categories"] == ["Artifact"] and row()["quantity"] == 2
        assert page.locator(".stack[data-group='Artifact'] .row[data-custom-id='41']").count() == 1
        _close_menu(page) if page.locator(".ctxmenu").count() else None
        # Remove from deck, then Undo
        if width < 900:
            # a press and hold on a touch screen (the deck page's own hold, as its other cards)
            card = page.locator(".deckview .row[data-custom-id='41']")
            card.scroll_into_view_if_needed()
            b = card.bounding_box()
            x, y = b["x"] + 60, b["y"] + b["height"] / 2
            cdp = page.context.new_cdp_session(page)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y}]})
            page.wait_for_timeout(600)
            cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
        else:
            card.click(button="right")
        menu.wait_for(state="visible")
        menu.locator("[role=menuitem]", has_text="Remove from deck").click()
        page.locator(".deck-toast", has_text="Removed Grizzled Mentor.").wait_for(
            state="visible", timeout=4000
        )
        assert page.locator(".row[data-custom-id='41']").count() == 0
        assert not [r for r in ark.decks[42]["customCards"] if r["card"]["id"] == 41]
        page.locator(".deck-toast button", has_text="Undo").click()
        page.locator(".deck-toast", has_text="Grizzled Mentor is back in Artifact.").wait_for(
            state="visible", timeout=4000
        )
        assert page.locator(".stack[data-group='Artifact'] .row[data-custom-id='41']").count() == 1
        assert row()["quantity"] == 2 and row()["categories"] == ["Artifact"]
        # Color tag…: the form in the menu; the tag shows at once and is saved as Archidekt's label
        card = page.locator(".deckview .row[data-custom-id='41']")
        card.click(button="right")
        menu.wait_for(state="visible")
        menu.locator("[role=menuitem]", has_text="Color tag…").click()
        form = menu.locator("form.mvform")
        form.wait_for(state="visible")
        assert form.locator("label").all_inner_texts() == ["Tag name", "Color"]
        assert "Archidekt's color tag on this card, in this deck only." in form.inner_text()
        assert form.locator(".acts .btn").all_inner_texts() == ["Apply tag", "Cancel"]  # no tag yet
        _fits(page, "color tag form", width)
        form.locator("button[type=submit]").click()
        assert form.locator(".status.err").inner_text() == "A tag needs a name, without commas."
        form.locator("#ct-input").fill("Have")
        form.locator("button[type=submit]").click()
        page.locator(".deck-toast", has_text="Tagged Grizzled Mentor Have.").wait_for(
            state="visible", timeout=4000
        )
        assert menu.count() == 0
        assert card.get_attribute("data-label") == "Have,#37d67a" and card.locator(".tagdot").count() == 1
        assert row()["label"] == "Have,#37d67a" and row()["quantity"] == 2
        # Mana value…: the same box as a normal card's, saved for the custom card
        card.click(button="right")
        menu.wait_for(state="visible")
        menu.locator("[role=menuitem]", has_text="Mana value…").click()
        form = menu.locator("form.mvform")
        form.wait_for(state="visible")
        assert form.locator("label").first.inner_text() == "Custom mana value"
        form.locator("#mv-input").fill("3")
        form.locator("button[type=submit]").click()
        page.locator(".deck-toast", has_text="Mana value of Grizzled Mentor saved: 3.").wait_for(
            state="visible", timeout=4000
        )
        assert card.get_attribute("data-mv") == "3" and row()["customCmc"] == 3
        assert row()["label"] == "Have,#37d67a"  # the tag stays
        # and taken off again: the card's own value, the tag's button
        card.click(button="right")
        menu.wait_for(state="visible")
        menu.locator("[role=menuitem]", has_text="Mana value…").click()
        menu.locator("form.mvform .acts .btn", has_text="Clear").click()
        page.locator(".deck-toast", has_text="Grizzled Mentor uses its own mana value again.").wait_for(
            state="visible", timeout=4000
        )
        assert card.get_attribute("data-mv") is None and row()["customCmc"] is None
        card.click(button="right")
        menu.wait_for(state="visible")
        menu.locator("[role=menuitem]", has_text="Color tag…").click()
        form = menu.locator("form.mvform")
        assert form.locator("#ct-input").input_value() == "Have"
        assert form.locator("#ct-colour").input_value() == "#37d67a"
        form.locator(".acts .btn", has_text="Take tag off").click()
        page.locator(".deck-toast", has_text="Tag taken off Grizzled Mentor.").wait_for(
            state="visible", timeout=4000
        )
        assert card.get_attribute("data-label") is None and card.locator(".tagdot").count() == 0
        assert row()["label"] == ",#656565"  # Archidekt's "no tag"
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", SIZES)
def test_a_custom_cards_picture_on_the_deck_page(server: Server, width: int, theme: str, monkeypatch) -> None:
    """The custom card's picture, through the gateway's own route, wherever a normal card shows its
    picture; a card without one keeps its name-and-type placeholder, whose type line stops short
    of the Custom mark."""
    import httpx
    from playwright.sync_api import sync_playwright

    from mtg_gateway import custom_pictures

    from .test_custom_pictures import _png

    custom_pictures._KEPT.clear()
    picture = _png()  # a picture Chromium can draw (the stub PNG the Scryfall routes answer is not one)
    monkeypatch.setattr(custom_pictures, "_public_host", lambda host: host == "cdn.example")

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://cdn.example/41.png":
            return httpx.Response(200, content=picture)
        return httpx.Response(404)

    gw = server.app.state.gateway
    gw.custom_picture_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    ark = server.ark
    ark.decks[42]["customCards"].append(
        {"id": 3101, "quantity": 1, "categories": ["Ramp"], "card": _custom(44, "Old Ranger")}
    )
    gw.decks.client._cache.clear()
    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/42?view=stacks", wait_until="networkidle")
        mentor = page.locator(".deckview .c.custom[data-custom-id='41']")
        ranger = page.locator(".deckview .c.custom[data-custom-id='44']")
        mentor.scroll_into_view_if_needed()
        # Grizzled Mentor has a picture: drawn over the placeholder, from the gateway's address
        pic = mentor.locator("img.pic")
        page.wait_for_function(
            "() => { const i = document.querySelector(\".c.custom[data-custom-id='41'] img.pic\");"
            " return i && i.complete && i.naturalWidth > 0; }"
        )
        assert pic.get_attribute("src") == "/decks/api/custom_picture/42/41?face=front"
        assert mentor.get_attribute("data-img") == "/decks/api/custom_picture/42/41?face=front"
        assert pic.bounding_box()["height"] >= mentor.bounding_box()["height"] - 6  # the card's border
        assert mentor.locator(".custom-badge").is_visible() and mentor.locator(".qty").is_visible()
        # Old Ranger has none (404): the picture is taken off, the placeholder stays, no viewer picture
        page.wait_for_function(
            "() => !document.querySelector(\".c.custom[data-custom-id='44'] img\")", timeout=4000
        )
        assert ranger.get_attribute("data-img") is None
        ph = ranger.locator(".ph")
        assert (
            ph.is_visible()
            and "Old Ranger" in ph.inner_text()
            and "Creature — Human Advisor" in ph.inner_text()
        )
        # the type line stops short of the Custom mark (it ran under it)
        assert (
            page.evaluate(
                """() => { const c = document.querySelector(".c.custom[data-custom-id='44']");
              const b = c.querySelector('.custom-badge').getBoundingClientRect();
              const r = document.createRange(); r.selectNodeContents(c.querySelector('.ph .ty'));
              return [...r.getClientRects()].filter(t => t.width > 0 && t.right > b.left + 0.5
                && t.left < b.right - 0.5 && t.bottom > b.top + 0.5 && t.top < b.bottom - 0.5)
                .map(t => [t.left, t.top, t.right, t.bottom, b.left, b.top]); }"""
            )
            == []
        ), "the type line runs under the Custom mark"
        # the next card in the stack covers the picture's card, its count, tag and mark included
        assert page.evaluate(
            """() => { const c = document.querySelector(".c.custom[data-custom-id='41']");
              const n = c.nextElementSibling; if (!n) return true;
              const b = c.querySelector('.custom-badge').getBoundingClientRect();
              const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
              return !!hit && n.contains(hit); }"""
        ), "the Custom mark shows through the card stacked over it"
        _fits(page, "stacks", width)
        # the list view's hover picture and the viewer draw the same picture
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        row41 = page.locator(".deckview .row[data-custom-id='41']")
        assert (
            row41.locator(".hover img").get_attribute("src") == "/decks/api/custom_picture/42/41?face=front"
        )
        if width >= 900:
            # a hover fetches the picture; Old Ranger's 404 takes it off and the hover shows nothing
            row44 = page.locator(".deckview .row[data-custom-id='44']")
            row44.scroll_into_view_if_needed()
            row44.locator(".name").hover()
            page.wait_for_function(
                "() => !document.querySelector(\".row[data-custom-id='44'] .hover img\")", timeout=4000
            )
            assert not row44.locator(".hover").is_visible()
            row41.locator(".name").hover()
            page.wait_for_function(
                "() => { const i = document.querySelector(\".row[data-custom-id='41'] .hover img\");"
                " return i && i.complete && i.naturalWidth > 0; }"
            )
            assert row41.locator(".hover").is_visible()
        row41.scroll_into_view_if_needed()
        row41.click()
        viewer = page.locator(".cardview.open")
        viewer.wait_for(state="visible")
        page.wait_for_function(
            "() => { const i = document.querySelector('.cardview.open .pane img');"
            " return i && i.complete && i.naturalWidth > 0; }"
        )
        assert (
            viewer.locator(".pane img").first.get_attribute("src")
            == "/decks/api/custom_picture/42/41?face=front"
        )
        page.keyboard.press("Escape")
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", SIZES)
def test_custom_card_pictures_on_my_lists(server: Server, width: int, theme: str, monkeypatch) -> None:
    """My lists › Custom cards: a thumbnail on each row and the card's current picture in its Edit
    box, through the gateway's own route; a picture it can't serve leaves the slot empty and the
    frame's words."""
    import httpx
    from playwright.sync_api import sync_playwright

    from mtg_gateway import custom_pictures

    from .test_custom_pictures import _png

    custom_pictures._KEPT.clear()
    monkeypatch.setattr(custom_pictures, "_public_host", lambda host: host == "cdn.example")
    picture = _png()

    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == "https://cdn.example/41.png":
            return httpx.Response(200, content=picture)
        return httpx.Response(404)

    server.app.state.gateway.custom_picture_http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/lists/custom", wait_until="networkidle")
        rows = page.locator(".lp .ui-table .tr[data-row]")
        mentor = rows.filter(has_text="Grizzled Mentor").first
        ranger = rows.filter(has_text="Old Ranger").first
        page.wait_for_function(
            "() => { const i = document.querySelector(\".tr[data-name='Grizzled Mentor'] img.lp-thumb\");"
            " return i && i.complete && i.naturalWidth > 0; }"
        )
        thumb = mentor.locator("img.lp-thumb")
        assert thumb.get_attribute("src") == "/lists/api/custom_picture/41?face=front"
        assert thumb.bounding_box()["width"] == 34
        # Old Ranger's picture can't be served: a labelled tile (no empty slot, no name tile) and
        # the row says so
        page.wait_for_function(
            "() => !document.querySelector(\".tr[data-name='Old Ranger'] img\")", timeout=4000
        )
        tile = ranger.locator("span.lp-thumb.lp-thumb-none")
        assert tile.count() == 1 and ranger.locator(".img-fallback").count() == 0
        assert tile.get_attribute("role") == "img" and tile.locator("svg.i").count() == 1
        assert (
            tile.get_attribute("aria-label") == "Old Ranger, Creature — Human Advisor (picture unavailable)"
        )
        assert ranger.locator(".title").inner_text().startswith("Old Ranger")
        assert ranger.locator(".lp-nopic").first.inner_text().strip() == "· picture unavailable"
        _fits(page, "custom rows", width)
        # the Edit box: the current picture over the frame; then Old Ranger's frame with its words
        mentor.locator(".kebab").click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Edit…").click()
        box = page.locator("#cc-box")
        box.wait_for(state="visible")
        page.wait_for_function(
            "() => { const i = document.querySelector(\"#cc-box .lp-crop[data-crop='f'] img.lp-cur\");"
            " return i && i.complete && i.naturalWidth > 0; }"
        )
        crop = box.locator(".lp-crop[data-crop='f']")
        cur = crop.locator("img.lp-cur")
        assert cur.get_attribute("src") == "/lists/api/custom_picture/41?face=front"
        assert abs(cur.bounding_box()["width"] - crop.bounding_box()["width"]) <= 6  # the frame's border
        _fits(page, "edit box", width)
        _close_box(page)
        ranger.locator(".kebab").click()
        page.locator(".ctxmenu [role=menuitem]", has_text="Edit…").click()
        box.wait_for(state="visible")
        page.wait_for_timeout(800)
        crop = box.locator(".lp-crop[data-crop='f']")
        assert crop.locator("img").count() == 0 and crop.locator(".img-fallback").count() == 0
        # the frame says which card it is, as the deck's viewer does, and the one line why
        assert crop.locator(".lp-nm").inner_text() == "Old Ranger"
        assert crop.locator(".lp-ty").inner_text() == "Creature — Human Advisor"
        assert crop.locator(".lp-why").inner_text() == "Picture set, but it can't be shown right now"
        _close_box(page)
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", SIZES)
def test_add_to_deck_on_my_lists_custom_cards(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    ark = server.ark
    ark.decks[42]["customCards"] = []
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/lists/custom", wait_until="networkidle")
        row = page.locator(".lp .ui-table .tr[data-row]").filter(has_text="Grizzled Mentor").first
        row.locator(".kebab").click()
        page.locator(".ctxmenu").wait_for(state="visible")
        assert _items(page) == ["Edit…", "Add to deck…", "Delete…"]
        page.locator(".ctxmenu [role=menuitem]", has_text="Add to deck…").click()
        box = page.locator("#cc-add")
        box.wait_for(state="visible")
        page.wait_for_timeout(300)
        assert box.locator("h2").inner_text() == "Add “Grizzled Mentor” to a deck"
        page.wait_for_function("() => !document.querySelector('#cc-add select[name=deck]').disabled")
        # the deck's categories come in once a deck is picked (the first deck is picked for you)
        page.wait_for_function("() => !document.querySelector('#cc-add select[name=category]').disabled")
        cats = box.locator("select[name=category] option").all_inner_texts()
        assert cats[0] == "The deck's default" and "Ramp" in cats and "Commander" in cats, cats
        _fits(page, "add box", width)
        if width < 900:
            page.evaluate(TEXT_200)
            page.wait_for_timeout(50)
            assert page.evaluate(MEASURE) == []
            page.reload(wait_until="networkidle")
            row.locator(".kebab").click()
            page.locator(".ctxmenu [role=menuitem]", has_text="Add to deck…").click()
            box.wait_for(state="visible")
            page.wait_for_function("() => !document.querySelector('#cc-add select[name=category]').disabled")
        # the copies are checked before anything is sent
        page.fill("#cc-add-qty", "0")
        box.locator(".acts button", has_text="Add to deck").click()
        assert "Copies must be a whole number from 1 to 99." in box.inner_text()
        assert ark.decks[42]["customCards"] == []
        page.fill("#cc-add-qty", "2")
        box.locator("select[name=category]").select_option(label="Ramp")
        box.locator(".acts button", has_text="Add to deck").click()
        page.wait_for_url("**/lists/custom?ok=card_added**")
        assert (
            "Added “Grizzled Mentor” to the deck on Archidekt and read back."
            in page.locator(".notice.ok").inner_text()
        )
        rows = ark.decks[42]["customCards"]
        assert len(rows) == 1 and rows[0]["quantity"] == 2 and rows[0]["categories"] == ["Ramp"]
        _fits(page, "added", width)
        browser.close()
    assert errors == []
