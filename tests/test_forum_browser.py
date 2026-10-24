"""The Forum pages in a browser (static/forum.js over static/social.js): /forum, a section's
thread table, a thread with its replies and a locked thread fit a phone, the Fold closed and
open and a desktop with no sideways scroll, 44 px targets on touch and 4.5:1 text, in both
themes, and hold together at 200% text; Load more is a button that appends the next page's rows;
the order choice applies on selection; a thread row has its menu by right-click; the thread's
replies are drawn from the thread the page already read (no second read) and a reply is posted
through the deck page's own comment panel after the Post this reply? box; the Add a package box's Color
tag select fills with the member's tags as it opens. Skipped without a Chromium build."""

from __future__ import annotations

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_editor_browser import _link
from .test_forum_page import _reply
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server
from .test_text_zoom_browser import TEXT_200

TEXT = (
    ".fm h1, .fm .ui-sub, .fm .crumbs a, .fm .ui-chip, .fm .ui-table .t, .fm .ui-table .sub, "
    ".fm .ui-table .th span, .fm .fm-why span, .fm .btn, .fm .fm-post .text, .fm .fm-post .ph, "
    ".fm .fm-post .pf, .fm .notice, .fm .comments h2, .comments .cmt p, .comments .cmt .who, "
    ".comments .cmt .acts button, .comments label, .comments .muted, .fm .loadmore .btn, .fm .fm-n, "
    ".fm .list-head h2, .fm form.fm-open label, .ctxmenu [role=menuitem], .ctxmenu .head"
)
TARGETS = (
    ".fm a.tr, .fm .btn, .fm .ui-chip, .fm .crumbs a, .fm .msel-btn, .fm input[type=search], "
    ".fm .comments form.newcomment button, .fm form.fm-open input, .fm form.fm-open .btn"
)
SIZES = [(344, "light"), (390, "dark"), (673, "light"), (1280, "dark"), (1280, "light")]


def _seed(ark) -> None:
    for i in range(25):
        ark.forum_threads[710000 + i] = {
            "title": f"Thread {i}",
            "section": "General",
            "type": 0,
            "owner": "amy",
        }
    ark.forum_page_size = 20
    first = _reply(ark, 700002, "amy", "Arcane Signet, every time.")
    _reply(ark, 700002, "alice", "And Sol Ring.", parent=first)
    ark.account.tags["alice"] = [{"id": 21, "name": "Cut", "color": "#ff4d4f"}]
    for row in ark.decks[42]["cards"]:
        if row["card"]["oracleCard"]["name"] == "Sol Ring":
            row["label"] = "Have,#37d67a"


@pytest.fixture
def server(tmp_path):
    s = Server(tmp_path)
    _seed(s.ark)
    object.__setattr__(s.app.state.gateway.settings, "writes_enabled", True)
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


@pytest.mark.parametrize("width,theme", SIZES)
def test_the_forum_pages_fit_every_width_in_both_themes(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    ark = server.ark
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/forum", wait_until="networkidle")
        assert page.locator(".fm .ui-chip").count() == 0  # the sections are the tables' rows alone
        _fits(page, "/forum", width)
        # a section: the table, then Load more appends the next page's rows in place
        page.goto(f"{server.base}/forum/Archidekt/General", wait_until="networkidle")
        assert page.locator(".fm .fm-threads a.tr").count() == 20
        _fits(page, "section", width)
        more = page.locator(".fm .loadmore [data-load-more]")
        assert "20 of 28" in more.inner_text()
        more.click()
        page.locator(".fm .fm-threads a.tr").nth(27).wait_for(state="visible", timeout=4000)
        assert page.locator(".fm .fm-threads a.tr").count() == 28
        assert "All 28 shown." in page.locator(".fm .loadmore").inner_text()
        assert page.url.endswith("/forum/Archidekt/General")  # the page did not change
        _fits(page, "section, loaded", width)
        # the row's menu: a right-click
        page.locator(".fm .fm-threads a.tr").first.click(button="right")
        menu = page.locator(".ctxmenu")
        menu.wait_for(state="visible")
        assert "Open on Archidekt" in menu.inner_text() and "Copy link" in menu.inner_text()
        _fits(page, "row menu", width)
        page.keyboard.press("Escape")
        menu.wait_for(state="detached")
        # the order choice applies on selection
        page.select_option(".fm .ui-tools select[name=order]", "points")
        page.wait_for_url("**/forum/Archidekt/General?*order=points*")
        assert ark.site_pages[-1][1]["orderBy"] == "-points"
        # a thread: the first post, the replies drawn from the page's own read, nested, with Reply
        before = ark.calls.count(("GET", "/api/comments/700002/"))
        page.goto(f"{server.base}/forum/thread/700002", wait_until="networkidle")
        assert page.locator("h1").inner_text() == "Best mana rocks?"
        assert "First post of Best mana rocks?" in page.locator(".fm-post .text").inner_text()
        page.locator(".comments .cmt .acts button", has_text="Reply").first.wait_for(state="visible")
        assert page.locator(".comments .cmt").count() == 2
        assert page.locator(".comments .cmt .replies .cmt").count() == 1
        assert ark.calls.count(("GET", "/api/comments/700002/")) == before + 1
        _fits(page, "thread", width)
        # 200% text
        page.evaluate(TEXT_200)
        page.wait_for_timeout(200)
        assert page.evaluate(MEASURE) == [], "thread at 200% text"
        # a locked thread: the note, no reply box, no Reply on the replies either
        page.goto(f"{server.base}/forum/thread/700003", wait_until="networkidle")
        assert "takes no new replies" in page.locator(".fm .notice").inner_text()
        assert page.locator("form.newcomment").count() == 0
        assert page.locator(".comments .cmt .acts button").count() == 0
        _fits(page, "locked thread", width)
        # when Archidekt's forum pages change: the plain words and the way to a thread
        ark.forum_page_changed = True
        page.goto(f"{server.base}/forum", wait_until="networkidle")
        assert "Archidekt changed how its forum pages load" in page.locator(".fm .notice").inner_text()
        _fits(page, "changed", width)
        page.fill("#fm-thread", "https://archidekt.com/forum/thread/700001")
        page.locator(".fm form.fm-open .btn").click()
        page.wait_for_url("**/forum/thread/700001")
        assert page.locator("h1").inner_text() == "Welcome to the forum"
        ark.forum_page_changed = False
        browser.close()
    assert errors == []


def test_a_reply_is_confirmed_once_and_lands_in_the_thread(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    ark = server.ark
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        page.goto(f"{server.base}/forum/thread/700002", wait_until="networkidle")
        page.locator(".comments .cmt .acts button", has_text="Reply").first.wait_for(state="visible")
        page.wait_for_timeout(800)  # the click guard
        page.fill("#ctext", "Mind Stone too.")
        page.locator("form.newcomment button[type=submit]").click()
        # the mockup's Post this reply? box: the reply as it will appear, asked once more
        box = page.locator(".mtgbox-scrim .mtgbox")
        box.wait_for(state="visible")
        assert box.locator("h2").inner_text() == "Post this reply?"
        assert box.locator(".post-preview").inner_text() == "Mind Stone too."
        assert "Posted publicly under “alice” in this thread on Archidekt." in box.inner_text()
        assert box.locator(".acts button", has_text="Keep editing").count() == 1
        assert [c["text"] for c in ark.comments[700002]] == ["Arcane Signet, every time.", "And Sol Ring."]
        box.locator(".acts button", has_text="Post reply").click()
        page.locator(".comments .cmt", has_text="Mind Stone too.").wait_for(state="visible", timeout=4000)
        assert "Mind Stone too." in ark.comments[700002][-1]["text"]  # kept as Archidekt's document
        assert ark.comments[700002][-1]["owner"]["username"] == "alice"
        assert page.locator("#ctext").input_value() == ""
        browser.close()
    assert errors == []


@pytest.mark.parametrize("width,theme", [(344, "dark"), (1280, "light")])
def test_the_package_box_color_tag_fills_with_my_tags_as_it_opens(
    server: Server, width: int, theme: str
) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    ark = server.ark
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        page.goto(f"{server.base}/decks/42?view=text", wait_until="networkidle")
        assert ("GET", "/api/decks/colorTags/") not in ark.calls  # not on the deck page's load
        page.evaluate("() => window.MtgDeckExtras.open('package')")
        box = page.locator("#package")
        box.wait_for(state="visible")
        sel = box.locator("#dx-ptag")
        page.locator("#dx-ptag option[value='Cut,#ff4d4f']").wait_for(state="attached", timeout=4000)
        assert ("GET", "/api/decks/colorTags/") in ark.calls
        assert page.evaluate("() => [...document.querySelectorAll('#dx-ptag option')].map(o => o.text)") == [
            "None",
            "Have",
            "Cut",
        ]
        assert box.locator(".dx-tagdot").is_hidden()
        sel.select_option("Cut,#ff4d4f")
        dot = box.locator(".dx-tagdot")
        assert dot.is_visible()
        assert dot.evaluate("d => getComputedStyle(d).backgroundColor") == "rgb(255, 77, 79)"
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, "#package label, #package .acts button, #package p") == []
        if width < 900:
            assert min(_targets(page, "#package .msel-btn, #package .acts button")) >= 43.5
        browser.close()
    assert errors == []
