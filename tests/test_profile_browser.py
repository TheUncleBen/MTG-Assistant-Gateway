"""The account-side screens of parity release 2 in a browser (static/profile.js, static/bell.js):
Account's Archidekt cards, the Collection page's Visibility line and the bell fit a phone, the
Fold closed and open and a desktop with no sideways scroll, 44 px targets on touch and 4.5:1
text, in both themes; the switches save in place with a toast; the picture box checks the file
before it is sent and shows it; Make it public… asks once more; the bell's count arrives after
the page loads and its box lists the notifications; the page holds together at 200% text."""

from __future__ import annotations

import io

import pytest

from .test_card_search_filters_browser import CONTRAST
from .test_cardsearch_browser import _chromium, _page
from .test_editor_browser import _link
from .test_overflow_sweep_browser import MEASURE
from .test_suggest_browser import Server
from .test_text_zoom_browser import ROOT_200, TEXT_200

TEXT = (
    ".acc h2, .acc .ui-row .lab, .acc .ui-row .help, .acc .ui-row label, .acc .btn, .acc button, "
    ".acc #archidekt-only p, .acc .muted, .acc .notice, .col-vis .t, .col-vis .btn, .col-vis button, "
    ".mtgbox h2, .mtgbox p, .mtgbox .acts button, .bell-row .t, .bell-row .s, .bell-badge, .hq .wdg .lab, "
    ".hq .wdg .big, .hq .wdg .det"
)
NOTES = [
    {
        "notificationType": "ForumThread",
        "text": "bob_brews replied to your thread",
        "selfId": 77,
        "seen": False,
    },
    {
        "notificationType": "DeckThread",
        "text": "carol_c commented on “Meren Graveyard Value”",
        "linkId": 42,
        "seen": False,
    },
    {"notificationType": "Contest", "text": "Your entry was reviewed", "linkId": 12, "seen": False},
    {"notificationType": "Profile", "text": "dave started following you", "linkId": "dave", "seen": True},
]


def _png(fmt: str = "PNG", size: tuple[int, int] = (64, 64)) -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", size, (10, 120, 200)).save(out, format=fmt)
    return out.getvalue()


@pytest.fixture
def server(tmp_path):
    from mtg_gateway import profile_page

    for d in (profile_page._PROFILE, profile_page._COUNT, profile_page._LIST, profile_page._LAST_ERROR):
        d.clear()  # the caches are per process, not per server
    s = Server(tmp_path)
    s.ark.account.notes["alice"] = [dict(n) for n in NOTES]
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


@pytest.mark.parametrize(
    "width,theme", [(344, "light"), (390, "dark"), (810, "light"), (1366, "dark"), (1366, "light")]
)
def test_the_screens_fit_every_width_in_both_themes(server: Server, width: int, theme: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, width, theme, exe)
        # the bell shows Archidekt's count once the page has loaded, at every width (before the
        # list is opened: reading the list is what clears the count)
        # (the touch rail has it as its Alerts place instead of beside the picture)
        page.goto(f"{server.base}/account/details", wait_until="networkidle")
        rail = page.evaluate(
            "() => getComputedStyle(document.querySelector('.tabbar')).flexDirection === 'column'"
        )
        bell = page.locator(".tabbar a.bell" if rail else ".topbar .bell")
        badge = bell.locator(".n")
        badge.wait_for(state="visible", timeout=4000)
        assert badge.inner_text() == "3" and bell.get_attribute("aria-label") == "Alerts, 3 new"
        for path in ("/account", "/collection", "/", "/notifications"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            assert page.evaluate(MEASURE) == [], path
            assert page.evaluate(CONTRAST, TEXT) == [], path
            if width < 900:
                sizes = _targets(
                    page,
                    ".acc .ui-row .switch, .acc .ui-row .msel-btn, .acc .btn, .acc button:not(.inline), "
                    ".col-vis .btn, .col-vis button, .topbar .bell, .tabbar a.bell, .bell-row, "
                    ".hq .wdg .go .btn, .yhub .yrow, .yhub .ysignout button",
                )
                assert sizes and min(sizes) >= 43.5, (path, sizes)
        page.goto(f"{server.base}/account/details", wait_until="networkidle")
        # the picture box: the words, the file checked here, the preview, the one orange button
        page.locator("[data-box-open='picture']").click()
        box = page.locator("#picture")
        box.wait_for(state="visible")
        assert "Choose a JPEG or PNG up to 1 MB." in box.inner_text()
        use = page.locator("#pr-use")
        assert use.is_disabled()
        page.locator("#pr-file").set_input_files(
            {"name": "me.gif", "mimeType": "image/gif", "buffer": _png("GIF")}
        )
        page.wait_for_timeout(300)
        assert use.is_disabled() and "Use a JPEG or PNG picture." in page.locator("#pr-err").inner_text()
        page.locator("#pr-file").set_input_files(
            {"name": "me.png", "mimeType": "image/png", "buffer": _png()}
        )
        page.wait_for_timeout(300)
        assert use.is_enabled() and page.locator("#pr-preview img").count() == 1
        assert "me.png" in page.locator("#pr-filename").inner_text()
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        use.click()
        page.wait_for_url("**/account/details?ok=picture*")
        assert "Picture saved on Archidekt and read back." in page.locator("#profile").inner_text()
        assert server.ark.account.uploads and server.ark.account.uploads[-1][0] == "alice"
        browser.close()
    assert errors == []


def test_switches_and_drop_downs_save_in_place_and_the_bell_opens_its_box(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    me = server.ark.account.profile("alice")
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        page.goto(f"{server.base}/account/details", wait_until="networkidle")
        page.wait_for_timeout(800)  # the click guard: a press faster than a person reads is refused
        page.locator("label[class='switch']:has(#pr-newsletter)").click()
        page.locator(".toast").wait_for(state="visible", timeout=4000)
        assert "Saved on Archidekt." in page.locator(".toast").inner_text()
        assert me["profile"]["newsletterable"] is True
        page.locator("label[class='switch']:has(#pr-site_notifications)").click()
        page.wait_for_timeout(500)
        assert me["accountSettings"]["noArchidektNotifications"] is True
        page.select_option("#pr-deck_view", "grid")
        page.wait_for_timeout(500)
        assert me["accountSettings"]["deckView"] == 2
        # a refusal reverts the control and says why
        page.evaluate(
            "() => { document.querySelector('#pr-search_game')"
            ".insertAdjacentHTML('beforeend', \"<option value='moon'>Moon</option>\"); }"
        )
        page.select_option("#pr-search_game", "moon")
        page.wait_for_timeout(500)
        assert "takes one of" in page.locator(".toast.error").inner_text()
        assert page.locator("#pr-search_game").input_value() == "paper"
        # the bio and username save with the panel's notice
        page.fill("#pr-bio", "Commander player in Leeds.")
        page.locator("#pr-f button").click()
        page.wait_for_url("**/account/details?ok=profile*")
        assert "Profile saved on Archidekt: bio. Read back." in page.locator("#profile").inner_text()
        # the bell: the count, then the box with the list; the count clears once the list opened
        badge = page.locator(".topbar .bell .n")
        badge.wait_for(state="visible", timeout=4000)
        assert badge.inner_text() == "3"
        page.locator(".topbar .bell").click()
        box = page.locator(".mtgbox-scrim .mtgbox")
        box.wait_for(state="visible")
        page.locator(".bell-list").wait_for(state="visible", timeout=4000)
        rows = page.locator(".bell-row")
        assert rows.count() == 4 and page.locator(".bell-row.new").count() == 3
        assert page.locator(".bell-badge").inner_text() == "3 new"
        assert page.locator(".bell-row[href='/decks/42']").count() == 1
        assert "The count clears when you open this list." in box.inner_text()
        assert page.evaluate(MEASURE) == []
        assert page.evaluate(CONTRAST, TEXT) == []
        assert min(_targets(page, ".bell-row, .mtgbox .acts button, .mtgbox .acts .btn")) >= 43.5
        page.keyboard.press("Escape")
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        assert not badge.is_visible()
        # Home's Needs you row: nothing while the count is zero; with new alerts it shows the
        # count, and its Open alerts opens the same box
        page.goto(f"{server.base}/", wait_until="networkidle")
        page.wait_for_timeout(800)
        assert page.locator("[data-bell-row]").is_hidden()
        # a count of one, as the script keeps it for a minute between pages (the gateway keeps
        # Archidekt's count for a minute too, so a new note would not show yet)
        page.evaluate("() => sessionStorage.setItem('mtg-bell', JSON.stringify({at: Date.now(), n: 1}))")
        page.set_viewport_size({"width": 1366, "height": 860})
        page.goto(f"{server.base}/", wait_until="networkidle")
        page.locator("[data-bell-row]").wait_for(state="visible", timeout=4000)
        assert page.locator("[data-bell-row] .what").inner_text() == "1 new alert on Archidekt"
        page.locator("[data-bell-open]").click()
        page.locator(".bell-list").wait_for(state="visible", timeout=4000)
        page.keyboard.press("Escape")
        browser.close()
    assert errors == []


def test_make_it_public_asks_once_more_and_private_is_one_press(server: Server) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    me = server.ark.account.profile("alice")
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 1366, "light", exe)
        page.goto(f"{server.base}/collection", wait_until="networkidle")
        page.wait_for_timeout(800)  # the click guard
        vis = page.locator("#visibility")
        assert "Private · only you can see it" in vis.inner_text()
        page.locator("[data-box-open='col-vis']").click()
        box = page.locator("#col-vis")
        box.wait_for(state="visible")
        assert "Make your binder public?" in box.inner_text()
        final = box.locator(".acts button").last
        assert final.inner_text().strip() == "Make it public" and "primary" in (
            final.get_attribute("class") or ""
        )
        box.locator(".acts button", has_text="Keep it private").click()
        page.locator(".mtgbox-scrim").wait_for(state="detached")
        assert me["profile"]["is_collection_public"] is False
        page.locator("[data-box-open='col-vis']").click()
        box.wait_for(state="visible")
        final.click()
        page.wait_for_url("**/collection?ok=public*")
        page.wait_for_timeout(800)
        assert me["profile"]["is_collection_public"] is True
        vis = page.locator("#visibility")
        assert "Public · anyone can see it on your Archidekt profile" in vis.inner_text()
        assert "Your binder is public on Archidekt." in vis.inner_text()
        vis.locator("button", has_text="Make it private").click()
        page.wait_for_url("**/collection?ok=private*")
        assert me["profile"]["is_collection_public"] is False
        browser.close()
    assert errors == []


@pytest.mark.parametrize("zoom", ["root", "text"])
def test_account_holds_together_on_a_phone_at_200_percent_text(server: Server, zoom: str) -> None:
    from playwright.sync_api import sync_playwright

    sid = _link(server)
    exe = _chromium()
    with sync_playwright() as p:
        browser, page, errors = _page(p, server, sid, 390, "light", exe)
        for path in ("/account", "/collection", "/notifications"):
            page.goto(f"{server.base}{path}", wait_until="networkidle")
            page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
            page.wait_for_timeout(300)
            assert page.evaluate(MEASURE) == [], (path, zoom)
        page.goto(f"{server.base}/account/details", wait_until="networkidle")
        page.evaluate(ROOT_200 if zoom == "root" else TEXT_200)
        page.locator("[data-box-open='picture']").click()
        page.locator("#picture").wait_for(state="visible")
        assert page.evaluate(MEASURE) == [], zoom
        browser.close()
    assert errors == []
