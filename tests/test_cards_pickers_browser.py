"""The two picker cards (printings-card, picker-card) in a real browser, driven by the fake MCP
Apps host of test_cards_browser (0.7.46 review, findings F1 to F4): a tile is a whole card big
enough to read at phone widths, sharp on a dense screen, and a tap shows it big before the one
press that sends the pick; the frame follows the card's height both ways and settles in a report
or two instead of growing on every picture; a host that shrinks a wide layout still gets readable
tiles; pictures that cannot load leave a plain list; a chat that cannot carry a pick is told so."""

from __future__ import annotations

import json

import pytest

from .test_cards_browser import HOST, LINK, ROWS, _calls, _open, _shot, png
from .test_consent_browser import _chromium_or_skip, _launch

pytest.importorskip("playwright")

# Scryfall's small and normal picture sizes, so a tile's srcset has a real choice to make
SMALL = png(146, 204)
NORMAL = png(488, 680)


def _printing(i: int) -> dict:
    sets = (
        ("cmm", "Commander Masters", ["nonfoil", "foil"]),
        ("sld", "Secret Lair", ["foil"]),
        ("cmr", "Commander Legends", ["etched"]),
    )
    code, name, fin = sets[i % 3]
    return {
        "name": "Lightning Bolt",
        "set": code,
        "set_name": name,
        "collector_number": str(100 + i),
        "released_at": "2023-08-04",
        "finishes": fin,
        "scryfall_id": f"s-{i}",
        "scryfall_uri": f"https://scryfall.com/card/{code}/{100 + i}",
        "image_small": f"https://cards.scryfall.io/small/front/{i}.jpg",
        "image_normal": f"https://cards.scryfall.io/normal/front/{i}.jpg",
    }


def _long_list(n: int = 78) -> dict:
    return {
        "ok": True,
        "oracle_id": "o-1",
        "cards": [_printing(i) for i in range(n)],
        "has_more": False,
        "total_cards": n,
    }


RESULT = {
    "structuredContent": {
        "ok": True,
        "oracle_id": "o-1",
        "name": "Lightning Bolt",
        "total_cards": 78,
        "has_more": False,
    },
    "_meta": {"mtg/card": {"link": LINK, "name": "Lightning Bolt", "oracle_id": "o-1", "total": 78}},
}


def _route(page, printings: dict, *, pictures: str = "ok", held: list | None = None) -> None:
    """The signed link answers ``printings``; pictures are ``ok`` (a real-size PNG, the normal size
    for a normal-size link), ``gone`` (404), or ``held`` (kept in ``held`` until the test lets go)."""

    def link(route):
        route.fulfill(status=200, content_type="application/json", body=json.dumps(printings))

    def image(route):
        if pictures == "gone":
            route.fulfill(status=404, body="")
        elif pictures == "held" and held is not None:
            held.append(route)
        else:
            body = (
                NORMAL if "/normal/" in route.request.url or "version=normal" in route.request.url else SMALL
            )
            route.fulfill(status=200, content_type="image/png", body=body)

    page.route("https://mtg.test/cards/data/*", link)
    page.route("https://api.scryfall.com/**", image)
    page.route("https://cards.scryfall.io/**", image)


def _tiles(card) -> list[dict]:
    return card.locator("#grid").evaluate(
        """g => [...g.querySelectorAll('.tile img')].map(i => {
             const r = i.getBoundingClientRect();
             return {w: r.width, h: r.height, x: r.x, srcset: i.srcset};
           })"""
    )


def _frame_and_body(page) -> tuple[float, float]:
    frame = page.evaluate("() => document.getElementById('f').getBoundingClientRect().height")
    body = page.frames[1].evaluate("() => document.body.getBoundingClientRect().height")
    return frame, body


def test_printings_tiles_are_whole_cards_big_enough_to_read_at_phone_widths() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            ctx = browser.new_context(
                viewport={"width": 412, "height": 900},
                device_scale_factor=2.625,
                is_mobile=True,
                has_touch=True,
            )
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, _long_list())
            for width in (320, 400):
                card = _open(page, "printings-card", RESULT, width=width, theme="light")
                card.locator(".tile img").nth(5).wait_for()
                page.wait_for_timeout(300)
                tiles = _tiles(card)
                assert len(tiles) == 8  # a page; Show more adds the next (test_cards_audit_browser)
                for t in tiles:
                    # a whole card (5:7), never a 48 px strip, and wide enough to read
                    assert t["w"] >= 100, (width, t)
                    assert abs(t["h"] - t["w"] * 7 / 5) <= 2, (width, t)
                    # the small and the normal picture, so a dense screen draws a sharp one
                    assert "146w" in t["srcset"] and "488w" in t["srcset"], t
                columns = len({round(t["x"]) for t in tiles})
                assert columns == 2, (width, columns)
                _shot(page, f"printings-{width}-light")
            # A phone host that lays the card out wide (980 px) and shrinks it to the screen: the
            # host says platform mobile, and the tiles keep to two columns wide enough to read.
            card = _open(
                page, "printings-card", RESULT, width=980, theme="light", host_extra={"platform": "mobile"}
            )
            card.locator(".tile img").nth(5).wait_for()
            tiles = _tiles(card)
            assert len({round(t["x"]) for t in tiles}) == 2
            assert all(t["w"] >= 120 * 980 / 412 for t in tiles[:6]), tiles[:2]
            # a wide host (a desktop chat) gets at most three across
            card = _open(page, "printings-card", RESULT, width=980, theme="light")
            card.locator(".tile img").nth(5).wait_for()
            assert len({round(t["x"]) for t in _tiles(card)}) <= 3
            assert not errors, errors
        finally:
            browser.close()


def test_a_tap_shows_the_printing_big_and_only_use_this_printing_sends_the_pick() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, _long_list(6))
            card = _open(page, "printings-card", RESULT, width=375, theme="dark")
            card.locator(".tile").nth(5).wait_for()
            assert card.locator("#detail").is_hidden()
            # this host offers no fullscreen (ChatGPT's Android app shows no Expand, Ben 10-31): the
            # whole flow, look then pick, runs inline at the size the chat gives the card
            assert card.locator("#expand").get_attribute("class") == "small hidden"
            assert page.evaluate("() => !window.log.some(m => m.method === 'ui/request-display-mode')")
            card.locator(".tile").nth(1).click()
            # the big picture, the printing's words, and the one button that picks
            card.locator("#detail:not(.hidden)").wait_for()
            assert card.locator("#detail img.big").count() == 1
            big = card.locator("#detail img.big").evaluate(
                "i => ({w: i.getBoundingClientRect().width, srcset: i.srcset})"
            )
            assert big["w"] >= 280 and "488w" in big["srcset"], big
            assert "SLD #101" in card.locator("#dcap").inner_text()
            assert card.locator("#grid").is_hidden()
            page.wait_for_timeout(300)
            assert _calls(page, "ui/update-model-context") == []  # looking is not picking
            _shot(page, "printings-detail-375-dark")
            # Back: nothing picked
            card.locator("#back").click()
            assert card.locator("#detail").is_hidden() and card.locator(".tile.picked").count() == 0
            # pick it
            card.locator(".tile").nth(1).click()
            card.locator("#use").click()
            card.locator("#status.ok").wait_for()
            page.wait_for_function(
                "() => window.log.some(m => m.method === 'ui/update-model-context')", timeout=5_000
            )
            told = _calls(page, "ui/update-model-context")[-1]["params"]["content"][0]["text"]
            assert 'set_code "sld"' in told and 'collector_number "101"' in told
            assert (
                card.locator(".tile.picked").count() == 1
                and "SLD #101" in card.locator(".tile.picked").inner_text()
            )
            assert "Picked: Lightning Bolt" in card.locator("#status").inner_text()
            assert card.locator("#detail").is_hidden() and card.locator("#grid").is_visible()
            assert card.locator("a.link", has_text="Open the picked printing on Scryfall").count() == 1
            assert not errors, errors
        finally:
            browser.close()


def test_the_frame_follows_the_card_both_ways_and_settles_in_a_report_or_two() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            held: list = []
            _route(page, _long_list(), pictures="held", held=held)
            card = _open(page, "printings-card", RESULT, width=375, theme="light")
            card.locator(".tile").nth(7).wait_for()
            page.wait_for_timeout(800)
            before = _tiles(card)
            n0 = len(_calls(page, "ui/notifications/size-changed"))
            # the pictures arrive: every box was reserved, so nothing moves
            for route in list(held):
                route.fulfill(status=200, content_type="image/png", body=SMALL)
            held.clear()
            page.wait_for_timeout(3_000)
            for route in list(held):  # the ones asked for after the first batch
                route.fulfill(status=200, content_type="image/png", body=SMALL)
            after = _tiles(card)
            assert [(round(t["x"]), round(t["h"])) for t in after] == [
                (round(t["x"]), round(t["h"])) for t in before
            ]
            sizes = _calls(page, "ui/notifications/size-changed")
            # one or two reports in all while the list drew and its pictures loaded, never a
            # growing series (17 to 19 were measured: each one grew the frame, and the frame
            # grew the list's cap)
            assert 1 <= len(sizes) <= 3, [m["params"]["height"] for m in sizes]
            assert len(sizes) - n0 <= 1, [m["params"]["height"] for m in sizes]
            frame, body = _frame_and_body(page)
            assert abs(frame - body) <= 2, (frame, body)
            # the list never scrolls inside the chat's own scroll: it shows a page and grows with
            # Show more (a nested scroll box stuck on Android hosts)
            assert card.locator(".scroll").evaluate("s => getComputedStyle(s).overflowY") == "visible"
            assert card.locator(".scroll").evaluate("s => getComputedStyle(s).maxHeight") == "none"
            # the list filtered to nothing: the frame drops to the card
            card.locator("#filter").fill("zzz")
            page.wait_for_timeout(500)
            frame2, body2 = _frame_and_body(page)
            assert body2 < body - 100 and abs(frame2 - body2) <= 2, (frame, body, frame2, body2)
            assert not errors, errors
        finally:
            browser.close()


def test_pictures_that_cannot_load_leave_a_plain_list_of_set_and_number() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, _long_list(8), pictures="gone")
            card = _open(page, "printings-card", RESULT, width=375, theme="light")
            card.locator("#grid.list").wait_for(timeout=5_000)
            assert "Pictures could not load" in card.locator("#note").inner_text()
            assert card.locator(".tile img").count() == 0 and card.locator(".tile").count() == 8
            assert "CMM #100" in card.locator(".tile").first.inner_text()
            # a pick still works from the list
            card.locator(".tile").first.click()
            card.locator("#use").click()
            card.locator("#status.ok").wait_for()
            _shot(page, "printings-nopics-375-light")
            # the recognized-cards picker: the rows keep their words, without empty picture boxes
            card = _open(
                page,
                "picker-card",
                {"structuredContent": {"ok": True, "cards": []}, "_meta": {"mtg/card": {"rows": ROWS}}},
                width=375,
            )
            card.locator("#card.nopics").wait_for(timeout=5_000)
            assert "Pictures could not load" in card.locator("#note").inner_text()
            assert card.locator("#rows img").evaluate_all(
                "l => l.every(i => getComputedStyle(i).display === 'none')"
            )
            assert "CMM #411" in card.locator("#rows > .row").first.inner_text()
            assert not errors, errors
        finally:
            browser.close()


def test_picker_rows_keep_their_suggestions_bright_and_a_picture_opens_big() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, _long_list(3))
            card = _open(
                page,
                "picker-card",
                {"structuredContent": {"ok": True, "cards": []}, "_meta": {"mtg/card": {"rows": ROWS}}},
                width=358,
            )
            rows = card.locator("#rows > .row")
            rows.nth(2).wait_for()
            # the row that needs a pick is never dimmed as a whole: its suggestion tiles read at full strength
            ask = rows.nth(1)
            assert ask.evaluate("r => getComputedStyle(r).opacity") == "1"
            # the tile's own opacity times every ancestor's: what the eye sees
            seen = (
                "t => { let n = t, o = 1; while (n) { o *= parseFloat(getComputedStyle(n).opacity);"
                " n = n.parentElement; } return o; }"
            )
            assert ask.locator(".sugg .tile").first.evaluate(seen) == 1
            # the thumbnail is a whole card, 48 px wide at least
            pic = rows.nth(0).locator("img.pic").bounding_box()
            assert pic and pic["width"] >= 48 and abs(pic["height"] - pic["width"] * 7 / 5) <= 2, pic
            sugg = ask.locator(".sugg .tile img").first.bounding_box()
            assert sugg and sugg["width"] >= 100 and abs(sugg["height"] - sugg["width"] * 7 / 5) <= 2, sugg
            _shot(page, "picker-358-dark")
            # a tap on a picture shows the card big, in the list's place, and picks nothing
            rows.nth(0).locator("button.picbtn").click()
            card.locator("#detail:not(.hidden)").wait_for()
            assert (
                card.locator("#detail img.big").count() == 1
                and "Sol Ring" in card.locator("#dcap").inner_text()
            )
            assert card.locator("#rows").is_hidden()
            _shot(page, "picker-detail-358-dark")
            card.locator("#back").click()
            assert card.locator("#rows").is_visible() and card.locator("input.check").first.is_checked()
            page.wait_for_timeout(200)
            assert _calls(page, "ui/update-model-context") == []
            assert not errors, errors
        finally:
            browser.close()


DEAF_HOST = HOST.replace(
    'm.method === "ui/open-link" || m.method === "ui/update-model-context" ||',
    'm.method === "ui/open-link" ||',
)


def test_a_chat_that_cannot_carry_the_pick_is_told_what_to_say() -> None:
    """A host without ui/update-model-context answers the request with an error: the card says
    what to tell the assistant instead of showing the pick as sent."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, _long_list(3))
            page.set_content(DEAF_HOST % ("#ffffff", 375, "light"))
            page.evaluate("(r) => { window.toolResult = r; window.hostExtra = {}; }", RESULT)
            from mtg_gateway.cards import card_html

            page.evaluate(
                "(html) => { document.getElementById('f').srcdoc = html; }",
                card_html("printings-card", "https://mtg.test"),
            )
            page.wait_for_function(
                "() => window.log.some(m => m.method === 'ui/notifications/initialized')", timeout=10_000
            )
            card = page.frame_locator("#f")
            card.locator(".tile").nth(2).wait_for()
            card.locator(".tile").first.click()
            card.locator("#use").click()
            card.locator("#status", has_text="tell your assistant: use CMM #100").wait_for(timeout=5_000)
            assert not errors, errors
        finally:
            browser.close()
