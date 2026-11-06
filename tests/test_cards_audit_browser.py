"""The in-chat cards against the UI standard (the 0.7.46 audit, UI-STANDARD-WALK D20 to D25 and
row 11), in the fake MCP Apps host of test_cards_browser, as both kinds of host: one that lists
fullscreen (Claude's web app) and one that does not (ChatGPT's phone app, where the owner saw no
Expand and "really small" pictures). Phone, fold and desktop widths, light and dark.

* The printings card never scrolls inside the chat's own scroll: it shows a page of even tiles and
  grows with Show more. Tiles read at phone width, and a Bigger pictures switch (in the card, no
  host support needed) draws one whole card across; a tap shows the picture at the card's width
  with the rules text as text.
* The picker's "which one?" row is not dimmed (4.5:1), and its tick boxes have a 44 px box.
* The deck card's long category header ends in an ellipsis and keeps its count; the stats card's
  values stay whole at 320 px; every button and link in every card is one shape (a capsule) and at
  least 44 px tall.
"""

from __future__ import annotations

import json
import re
import time

import pytest

from .test_cards_browser import DECK, LINK, ROWS, _calls, _open, _shot, png
from .test_cards_pickers_browser import RESULT, _long_list, _tiles
from .test_consent_browser import _chromium_or_skip, _launch
from .test_track_a_cards_browser import WAITING, WAITING_META
from .waits import expect, settled

pytest.importorskip("playwright")

SMALL = png(146, 204)
NORMAL = png(488, 680)
LARGE = png(672, 936)

# a host that lists fullscreen (Claude) and one that does not and lays the card out on a phone (ChatGPT)
HOSTS = {
    "claude": {"availableDisplayModes": ["inline", "fullscreen"]},
    "chatgpt": {"platform": "mobile"},
}
WIDTHS = (320, 375, 412, 1280)


def _route(page, printings: dict | None = None, deck: dict | None = None) -> None:
    def link(route):
        body = printings if printings is not None else deck
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def scryfall(route):
        url = route.request.url
        if "format=image" in url or "cards.scryfall.io" in url:
            body = LARGE if "large" in url else NORMAL if "normal" in url else SMALL
            route.fulfill(status=200, content_type="image/png", body=body)
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "object": "card",
                        "name": "Lightning Bolt",
                        "mana_cost": "{R}",
                        "type_line": "Instant",
                        "oracle_text": "Lightning Bolt deals 3 damage to any target. <b>not markup</b>",
                    }
                ),
            )

    page.route("https://mtg.test/cards/data/*", link)
    page.route("https://api.scryfall.com/**", scryfall)
    page.route("https://cards.scryfall.io/**", scryfall)


def _frame_and_body(page) -> tuple[float, float]:
    """The frame's height and the card's own; the fake host draws a frame at most 1400 px tall
    (as a chat caps a card), so a taller card's frame stops there."""
    frame = page.evaluate("() => document.getElementById('f').getBoundingClientRect().height")
    body = page.frames[1].evaluate("() => document.body.getBoundingClientRect().height")
    return frame, min(1400, body)


def _frame_follows(page, timeout: float = 5.0) -> tuple[float, float]:
    """The frame's height once it is the card's own (within 2 px): the card reports its size to
    the host a moment after it drew (one report for a burst of changes, _bridge.js), so a look
    the instant the list is in the page can still see the frame as it was (CI, a slow runner
    with two workers, saw 10 px against a 1400 px card). Waits on that, with a deadline."""
    deadline = time.monotonic() + timeout
    while True:
        frame, body = _frame_and_body(page)
        if abs(frame - body) <= 2:
            return frame, body
        if time.monotonic() >= deadline:
            raise AssertionError(f"the frame never followed the card: frame {frame}, card {body}")
        page.wait_for_timeout(20)  # fixed wait: poll interval of a loop with a deadline


def _height(card, selector: str) -> float:
    """A control's height in the card's own CSS px (its getBoundingClientRect). Playwright's
    bounding_box is the host page's content quads, float32 scaled by the screen's pixel ratio:
    at 2.625 a 44 px box reads 43.99999237 or 44.0000076 with the frame's y offset (the caption
    wrapping on the runner's fonts moves it), which is noise, not a smaller target."""
    return card.locator(selector).first.evaluate("e => e.getBoundingClientRect().height")


def _no_inner_scroll(page) -> list[str]:
    """Every element in the card that could scroll on its own (a nested scroll box inside the
    chat's scroll): overflow auto or scroll with content taller than its box, or a max-height cap."""
    return page.frames[1].evaluate(
        """() => [...document.querySelectorAll('body *')].filter(e => {
             const s = getComputedStyle(e);
             if (s.position === 'fixed') return false;
             const scrolls = /auto|scroll/.test(s.overflowY) && e.scrollHeight > e.clientHeight + 1;
             return scrolls || (s.maxHeight !== 'none' && /auto|scroll/.test(s.overflowY));
           }).map(e => e.tagName + '.' + e.className)"""
    )


def _sideways(page) -> bool:
    return page.frames[1].evaluate(
        "() => document.documentElement.scrollWidth > document.documentElement.clientWidth + 1"
    )


def _boxes(card, selector: str) -> list[dict]:
    return card.locator(selector).evaluate_all(
        "l => l.map(e => { const r = e.getBoundingClientRect();"
        " return {w: r.width, h: r.height, x: r.x, y: r.y, text: e.textContent.trim().slice(0, 30)}; })"
    )


_RGB = re.compile(r"rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)")


def _rgb(s: str) -> tuple[float, float, float, float]:
    m = _RGB.match(s)
    assert m, s
    return float(m[1]), float(m[2]), float(m[3]), float(m[4]) if m[4] is not None else 1.0


def _lum(rgb: tuple[float, float, float]) -> float:
    def ch(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = rgb
    return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b)


def _contrast(fg: tuple[float, float, float], bg: tuple[float, float, float]) -> float:
    a, b = _lum(fg), _lum(bg)
    hi, lo = max(a, b), min(a, b)
    return (hi + 0.05) / (lo + 0.05)


def _seen_contrast(card, selector: str, page_bg: str) -> float:
    """The contrast the eye sees for the text of ``selector``: its colour and the first painted
    background behind it, each faded by every ancestor's opacity, over the chat's own background."""
    got = card.locator(selector).first.evaluate(
        """e => {
          let n = e, o = 1; while (n) { o *= parseFloat(getComputedStyle(n).opacity); n = n.parentElement; }
          let b = e, bg = null;
          while (b && !bg) {
            const c = getComputedStyle(b).backgroundColor;
            if (!/rgba\\(\\d+, \\d+, \\d+, 0\\)|transparent/.test(c)) bg = c;
            b = b.parentElement;
          }
          return {fg: getComputedStyle(e).color, bg: bg, opacity: o};
        }"""
    )
    host = _rgb(page_bg)[:3]
    fg = _rgb(got["fg"])
    bg = _rgb(got["bg"]) if got["bg"] else (*host, 1.0)

    def over(top: tuple[float, ...], under: tuple[float, ...]) -> tuple[float, float, float]:
        a = top[3] * got["opacity"]
        return (
            top[0] * a + under[0] * (1 - a),
            top[1] * a + under[1] * (1 - a),
            top[2] * a + under[2] * (1 - a),
        )

    painted_bg = over(bg, host)
    painted_fg = over(fg, painted_bg)
    return _contrast(painted_fg, painted_bg)


def _page_bg(theme: str) -> str:
    return "rgb(38, 38, 36)" if theme == "dark" else "rgb(255, 255, 255)"


# -- the printings card


def test_printings_card_shows_a_page_of_even_tiles_and_grows_with_show_more_not_an_inner_scroll() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, printings=_long_list(78))
            for host, extra in HOSTS.items():
                for theme in ("light", "dark"):
                    card = _open(page, "printings-card", RESULT, width=360, theme=theme, host_extra=extra)
                    card.locator("#more").wait_for()
                    expect(card.locator("#grid .tile img")).to_have_count(8)
                    settled(page.frames[1])
                    _frame_follows(page)
                    # a page of tiles, not all 78, and nothing in the card scrolls on its own
                    tiles = _tiles(card)
                    assert len(tiles) == 8, (host, theme, len(tiles))
                    assert _no_inner_scroll(page) == [], (host, theme, _no_inner_scroll(page))
                    assert not _sideways(page)
                    frame, body = _frame_and_body(page)
                    assert abs(frame - body) <= 2, (frame, body)
                    # even tiles: every tile the same height, in rows of two
                    heights = {round(b["h"]) for b in _boxes(card, ".tile")}
                    assert len(heights) == 1, (host, theme, heights)
                    assert len({round(t["x"]) for t in tiles}) == 2
                    # the set and number read at phone width: 13 px, not 11
                    cap = card.locator(".tile .cap b").first.evaluate(
                        "e => parseFloat(getComputedStyle(e).fontSize)"
                    )
                    assert cap >= 13, cap
                    # Show more says what is left and adds a page; the frame follows
                    more = card.locator("#more")
                    assert re.search(r"Show \d+ more", more.inner_text()) and "70 left" in more.inner_text()
                    assert _height(card, "#more") >= 44
                    more.click()
                    expect(card.locator("#grid .tile img")).to_have_count(2 * len(tiles))
                    settled(page.frames[1])
                    _frame_follows(page)
                    assert len(_tiles(card)) == 2 * len(tiles)
                    frame2, body2 = _frame_and_body(page)
                    assert abs(frame2 - body2) <= 2, (frame, body, frame2, body2)
                    tall = page.frames[1].evaluate("() => document.body.getBoundingClientRect().height")
                    assert tall > 1500, tall
                    assert _no_inner_scroll(page) == []
                    _shot(page, f"audit-printings-{host}-{theme}-360")
            # the filter starts the paging over, and a list that fits has no Show more
            card.locator("#filter").fill("legends")
            expect(card.locator("#more")).to_contain_text("18 left")
            assert len(_tiles(card)) == 8 and "18 left" in card.locator("#more").inner_text()
            card.locator("#filter").fill("")
            card.locator("#finish button[data-finish=foil]").click()
            expect(card.locator("#more")).to_contain_text("44 left")
            assert len(_tiles(card)) == 8 and "44 left" in card.locator("#more").inner_text()
            card.locator("#filter").fill("100")
            expect(card.locator("#more")).to_have_count(0)
            assert card.locator("#more").count() == 0 and len(_tiles(card)) == 1
            assert not errors, errors
        finally:
            browser.close()


def test_printings_card_has_a_bigger_pictures_switch_and_a_readable_detail_without_the_host() -> None:
    """ChatGPT's phone app lists no fullscreen, so Expand never shows there (the owner, 10-31): the
    card's own Bigger pictures switch draws one whole card across the card, and a tap shows the
    printing at the card's full width with its rules text, on every host."""
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
            _route(page, printings=_long_list(9))
            for host, extra in HOSTS.items():
                card = _open(page, "printings-card", RESULT, width=412, theme="light", host_extra=extra)
                card.locator(".tile img").nth(5).wait_for()
                settled(page.frames[1])
                _frame_follows(page)
                # Expand only where the host can do it; the card's own switch everywhere
                expand = card.locator("#expand")
                assert expand.is_visible() == (host == "claude"), host
                big = card.locator("#bigger")
                assert big.is_visible() and big.inner_text() == "Bigger pictures"
                assert _height(card, "#bigger") >= 44
                card_w = card.locator("#card").bounding_box()["width"]
                before = _tiles(card)
                assert len({round(t["x"]) for t in before}) == 2
                big.click()
                expect(big).to_have_text("Smaller pictures")
                settled(page.frames[1])
                after = _tiles(card)
                assert len({round(t["x"]) for t in after}) == 1, host
                # one whole card across the card: wider than 300 px on a 412 px phone, still 5:7
                assert after[0]["w"] >= 300 and after[0]["w"] >= card_w * 0.8, (host, after[0], card_w)
                assert abs(after[0]["h"] - after[0]["w"] * 7 / 5) <= 2
                # the sharpest picture Scryfall has for a whole-width tile
                assert "672w" in after[0]["srcset"], after[0]["srcset"]
                assert big.inner_text() == "Smaller pictures"
                assert _no_inner_scroll(page) == []
                _shot(page, f"audit-printings-bigger-{host}-412")
                big.click()
                expect(big).to_have_text("Bigger pictures")
                settled(page.frames[1])
                assert len({round(t["x"]) for t in _tiles(card)}) == 2
                # a tap: the picture at the card's width, and the words of the card as text
                card.locator(".tile").nth(1).click()
                card.locator("#detail:not(.hidden)").wait_for()
                pic = card.locator("#detail img.big").bounding_box()
                assert pic and pic["width"] >= card_w * 0.8, (host, pic, card_w)
                card.locator("#dtext", has_text="deals 3 damage").wait_for(timeout=5_000)
                assert "<b>not markup</b>" in card.locator("#dtext").inner_text()
                assert _height(card, "#use") >= 44
                assert _height(card, "#back") >= 44
                assert _no_inner_scroll(page) == []
                _shot(page, f"audit-printings-detail-{host}-412")
                # fixed wait: a negative check: the tap sends no model context update within 200 ms
                page.wait_for_timeout(200)
                assert _calls(page, "ui/update-model-context") == []
                card.locator("#back").click()
            # the finish chips are 44 px tap targets
            for b in _boxes(card, "#finish button"):
                assert b["h"] >= 44, b
            assert not errors, errors
        finally:
            browser.close()


# -- the picker card


def test_picker_rows_that_need_a_pick_read_at_full_contrast_and_ticks_have_a_44px_box() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, printings=_long_list(3))
            result = {"structuredContent": {"ok": True, "cards": []}, "_meta": {"mtg/card": {"rows": ROWS}}}
            for theme in ("light", "dark"):
                card = _open(page, "picker-card", result, width=360, theme=theme)
                rows = card.locator("#rows > .row")
                rows.nth(2).wait_for()
                ask = rows.nth(1)
                # the row that asks "which one?" is not dimmed or struck: it needs the person's eye
                assert "dim" not in (ask.get_attribute("class") or "")
                for sel, what in ((".pill", "which one?"), (".sub", "two names fit"), (".name", "Cultivatz")):
                    loc = ask.locator(sel).first
                    assert what in loc.inner_text()
                    assert loc.evaluate("e => getComputedStyle(e).textDecorationLine") != "line-through"
                    c = _seen_contrast(card, f"#rows > .row:nth-child(2) {sel}", _page_bg(theme))
                    assert c >= 4.5, (theme, what, round(c, 2))
                # an unticked recognised row is the dimmed one (the person's own choice)
                rows.nth(2).locator("input.check").uncheck()
                assert "dim" in (rows.nth(2).get_attribute("class") or "")
                # the tick box is a 44 px target: its label box, around a visible 22 px tick
                lab = rows.nth(0).locator("label.ck").bounding_box()
                assert lab and lab["width"] >= 44 and lab["height"] >= 44, lab
                tick = rows.nth(0).locator("input.check").bounding_box()
                assert tick and tick["width"] >= 20
                rows.nth(0).locator("label.ck").click(position={"x": 2, "y": 2})
                assert not rows.nth(0).locator("input.check").is_checked()
                assert _no_inner_scroll(page) == []
                _shot(page, f"audit-picker-{theme}-360")
            assert not errors, errors
        finally:
            browser.close()


# -- the deck and stats cards


LONG = "Rampandlandfallsynergiesunbrokencategorynamenospace"
DECK_RESULT = {"structuredContent": {"ok": True, "name": "Sample"}, "_meta": {"mtg/card": {"link": LINK}}}


def test_deck_card_ellipsises_a_long_category_header_and_keeps_its_count() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    deck = json.loads(json.dumps(DECK))
    deck["categories"][1]["name"] = LONG
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, deck=deck)
            result = DECK_RESULT
            card = _open(page, "deck-card", result, width=360, theme="light")
            card.locator(".row.cardrow").nth(3).wait_for()
            g = card.locator(".row.group").nth(1)
            name, count = g.locator("span").first, g.locator(".n")
            assert count.is_visible() and count.inner_text() == "3"
            gb, nb, cb = g.bounding_box(), name.bounding_box(), count.bounding_box()
            # the name stays inside the row and ends before the count; the count is whole
            assert nb["x"] + nb["width"] <= cb["x"] + 1 and cb["x"] + cb["width"] <= gb["x"] + gb["width"] + 1
            assert name.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
            assert name.evaluate("e => e.scrollWidth > e.clientWidth")  # clipped with an ellipsis, not cut
            assert gb["height"] >= 44 and _height(card, "#viewbtn") >= 44
            assert not _sideways(page) and _no_inner_scroll(page) == []
            _shot(page, "audit-deck-360-light")
            card.locator("#viewbtn").click()
            card.locator("#grid .tile").first.wait_for()
            gh = card.locator("#grid .gh").nth(1)
            assert gh.evaluate("e => getComputedStyle(e).textOverflow") == "ellipsis"
            assert not _sideways(page)
            assert not errors, errors
        finally:
            browser.close()


def test_deck_card_shows_a_page_of_rows_then_show_all() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    deck = json.loads(json.dumps(DECK))
    deck["categories"][1]["cards"] = [
        dict(deck["categories"][1]["cards"][0], name=f"Card number {i}") for i in range(60)
    ]
    deck["categories"][1]["count"] = 60
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, deck=deck)
            result = DECK_RESULT
            card = _open(page, "deck-card", result, width=360, theme="dark")
            card.locator("#more").wait_for()
            assert card.locator(".row.cardrow").count() == 30
            assert "Show all 62" in card.locator("#more").inner_text()
            assert _no_inner_scroll(page) == []
            _frame_follows(page)
            card.locator("#more").click()
            card.locator(".row.cardrow").nth(61).wait_for()
            assert card.locator("#more").count() == 0
            assert not errors, errors
        finally:
            browser.close()


STATS = {
    "ok": True,
    "deck": {"id": "7", "name": "Sample", "owner": "amy", "url": "https://archidekt.com/decks/7"},
    "stats": {
        "format": "commander",
        "card_count": 100,
        "distinct": 80,
        "land_count": 37,
        "nonland_count": 63,
        "average_mana_value": 3.12,
        "price_total": 412.5,
        "priced_cards": 80,
        "tutors": 2,
        "extra_turns": 0,
        "mass_land_denial": 0,
        "salt_total": 12.4,
        "mana_curve": {"0": 1, "1": 8, "2": 14, "3": 15, "4": 11, "5": 7, "6": 4, "7+": 3},
        "color_pips": {"U": 22, "G": 30},
        "mana_sources": {"U": 16, "G": 20, "C": 3},
        "color_identity": ["G", "U"],
        "checks": {"legal": True, "problems": []},
    },
}


def test_stats_card_values_stay_whole_at_320px() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            for theme in ("light", "dark"):
                card = _open(page, "stats-card", {"structuredContent": STATS}, width=320, theme=theme)
                card.locator("ul.facts li").nth(4).wait_for()
                # one line each: a value never breaks as "2 · 0 ·" then "0"
                lines = card.locator("ul.facts li b").evaluate_all(
                    "l => l.map(e => { const r = document.createRange(); r.selectNodeContents(e);"
                    " return [e.textContent, r.getClientRects().length]; })"
                )
                assert all(n == 1 for _, n in lines), (theme, lines)
                assert not _sideways(page)
                _shot(page, f"audit-stats-{theme}-320")
            assert not errors, errors
        finally:
            browser.close()


# -- one button language across the cards


def _button_shapes(card) -> dict[str, set[str]]:
    """Every visible button or link-button in the card (a picture tile is a picture, not a button
    in this sense), by role, with its shape (border radius) and height."""
    return card.locator("button:not(.tile):not(.picbtn), a.link, a.cap").evaluate_all(
        """l => { const out = {}; l.forEach(e => {
            const r = e.getBoundingClientRect(); if (!r.width) return;
            const s = getComputedStyle(e);
            const role = e.classList.contains('primary') ? 'primary'
              : e.classList.contains('danger') ? 'danger' : e.tagName === 'A' ? 'link' : 'secondary';
            (out[role] = out[role] || []).push({radius: s.borderTopLeftRadius, h: Math.round(r.height),
              text: e.textContent.trim().slice(0, 24)});
          }); return out; }"""
    )


def test_every_card_uses_one_button_shape_per_role_at_44px() -> None:
    """The audit saw a filled rectangle, a bordered rectangle and a grey capsule in one row of the
    waiting card, and a white rectangle, a dark rectangle and a capsule on the proposal card. Every
    button and link-button is now the same capsule, in every card, 44 px tall, with the role shown
    by its fill: filled for the one primary action, bordered for the rest, red for Reject."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, printings=_long_list(3))
            seen: dict[str, set[str]] = {}
            cases = [
                ("printings-card", RESULT),
                ("picker-card", {"structuredContent": {"ok": True}, "_meta": {"mtg/card": {"rows": ROWS}}}),
                ("stats-card", {"structuredContent": STATS}),
                ("waiting-card", {"structuredContent": WAITING, "_meta": WAITING_META}),
            ]
            for name, result in cases:
                card = _open(page, name, result, width=320, theme="light")
                settled(page.frames[1])
                _frame_follows(page)
                if name == "waiting-card":
                    card.locator("#apply-all").wait_for()
                    expect(
                        card.locator("#apply-all")
                    ).to_be_enabled()  # armed: the buttons in their resting look
                    settled(page.frames[1])
                if name == "printings-card":
                    card.locator(".tile").first.click()
                    card.locator("#use").wait_for()
                shapes = _button_shapes(card)
                for role, items in shapes.items():
                    for it in items:
                        assert it["h"] >= 44, (name, role, it)
                        seen.setdefault(role, set()).add(it["radius"])
                _shot(page, f"audit-buttons-{name}-320")
            # one radius for every role in every card: a capsule (half of 44 px or rounder)
            assert seen.keys() >= {"primary", "secondary", "danger", "link"}, seen
            radii = set().union(*seen.values())
            assert len(radii) == 1, (seen, radii)
            assert float(next(iter(radii)).replace("px", "")) >= 22
            assert not errors, errors
        finally:
            browser.close()


def test_cards_fit_every_width_light_and_dark_on_both_hosts() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page(viewport={"width": 1300, "height": 900})
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, printings=_long_list(30))
            picker = {"structuredContent": {"ok": True}, "_meta": {"mtg/card": {"rows": ROWS}}}
            for host, extra in HOSTS.items():
                for theme in ("light", "dark"):
                    for width in WIDTHS:
                        card = _open(
                            page, "printings-card", RESULT, width=width, theme=theme, host_extra=extra
                        )
                        card.locator("#more").wait_for()
                        settled(page.frames[1])
                        _frame_follows(page)
                        assert not _sideways(page), (host, theme, width)
                        assert _no_inner_scroll(page) == [], (host, theme, width)
                        _frame_follows(page)
                        if width == 1280:
                            assert len({round(t["x"]) for t in _tiles(card)}) <= 3
                        card = _open(page, "picker-card", picker, width=width, theme=theme, host_extra=extra)
                        card.locator("#rows > .row").nth(2).wait_for()
                        settled(page.frames[1])
                        _frame_follows(page)
                        assert not _sideways(page) and _no_inner_scroll(page) == [], (host, theme, width)
            assert not errors, errors
        finally:
            browser.close()
