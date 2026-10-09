"""The four in-chat cards of cards.py in a real browser, driven by a fake host that speaks the MCP
Apps postMessage protocol. The gateway's signed links and Scryfall are answered by the test, so
the cards draw their data, the person's taps go to the assistant as model context, and hostile
text stays text. Set MTG_CARD_SHOTS=<dir> to also save screenshots at phone, fold and desktop
widths in light and dark."""

from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from mtg_gateway.cards import card_html

from .test_consent_browser import _chromium_or_skip, _launch

pytest.importorskip("playwright")

LINK = "https://mtg.test/cards/data/tok-1"
# A 5x7 grey PNG, so pictures have a size without any network.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAUAAAAHCAIAAAAbd2raAAAAFElEQVR4nGNgYGD4z8DAwMDAwMAAAAwAA/8BpYQAAAAASUVORK5CYII="
)

HOST = """<!DOCTYPE html><html><body style="margin:0;background:%s">
<iframe id="f" sandbox="allow-scripts" style="width:%dpx;height:10px;border:0"></iframe>
<script>
window.log = [];
var frame = document.getElementById("f");
window.addEventListener("message", (ev) => {
  const m = ev.data; if (!m || m.jsonrpc !== "2.0") return;
  window.log.push(m);
  const send = (x) => frame.contentWindow.postMessage(x, "*");
  if (m.method === "ui/initialize") {
    send({jsonrpc: "2.0", id: m.id, result: {
      protocolVersion: "2026-01-26", hostInfo: {name: "fake", version: "0"},
      hostCapabilities: {openLinks: {}, serverTools: {}},
      hostContext: Object.assign({theme: "%s", platform: "web", displayMode: "inline",
                    safeAreaInsets: {top: 0, right: 0, bottom: 0, left: 0}}, window.hostExtra || {})}});
  } else if (m.method === "ui/notifications/initialized") {
    send({jsonrpc: "2.0", method: "ui/notifications/tool-result", params: window.toolResult});
  } else if (m.method === "ui/notifications/size-changed") {
    frame.style.height = Math.min(1400, m.params.height) + "px";
  } else if (m.method === "ui/open-link" || m.method === "ui/update-model-context" ||
             m.method === "ui/request-display-mode") {
    send({jsonrpc: "2.0", id: m.id, result: {}});
  } else if (m.id !== undefined && m.method) {
    send({jsonrpc: "2.0", id: m.id, error: {code: -32601, message: "nope"}});
  }
});
</script></body></html>"""

PRINTINGS = {
    "ok": True,
    "oracle_id": "o-1",
    "cards": [
        {
            "name": "Sol Ring",
            "set": "cmm",
            "set_name": "Commander Masters",
            "collector_number": "411",
            "released_at": "2023-08-04",
            "finishes": ["nonfoil", "foil"],
            "scryfall_id": "s-1",
            "scryfall_uri": "https://scryfall.com/card/cmm/411",
            "image_small": "https://cards.scryfall.io/small/front/1.jpg",
        },
        {
            "name": "Sol Ring",
            "set": "sld",
            "set_name": "Secret Lair <b>Drop</b>",
            "collector_number": "1",
            "released_at": "2022-01-01",
            "finishes": ["foil"],
            "scryfall_id": "s-2",
            "scryfall_uri": "https://scryfall.com/card/sld/1",
            "image_small": "https://cards.scryfall.io/small/front/2.jpg",
        },
        {
            "name": "Sol Ring",
            "set": "cmr",
            "set_name": "Commander Legends",
            "collector_number": "472",
            "released_at": "2020-11-20",
            "finishes": ["nonfoil", "etched"],
            "scryfall_id": "s-3",
            "scryfall_uri": "https://scryfall.com/card/cmr/472",
            "image_small": "https://cards.scryfall.io/small/front/3.jpg",
        },
    ],
    "has_more": False,
    "total_cards": 3,
}

DECK = {
    "ok": True,
    "id": "42",
    "name": "Sample <b>Deck</b>",
    "owner": "alice",
    "format": "commander",
    "url": "https://archidekt.com/decks/42",
    "gateway_url": "https://mtg.test/decks/42",
    "card_count": 4,
    "side_count": 1,
    "commanders": ["Aesi, Tyrant of Gyre Strait"],
    "colour_identity": ["G", "U"],
    "curve": {"1": 1, "3": 1, "6": 1},
    "categories": [
        {
            "name": "Commander",
            "count": 1,
            "in_deck": True,
            "cards": [
                {
                    "name": "Aesi, Tyrant of Gyre Strait",
                    "qty": 1,
                    "set": "cmr",
                    "cn": "275",
                    "finish": "",
                    "uid": "u-aesi",
                    "mana": "{4}{G}{U}",
                    "mv": 6,
                    "type": "Legendary Creature",
                    "price": 1.5,
                    "in_deck": True,
                }
            ],
        },
        {
            "name": "Ramp",
            "count": 3,
            "in_deck": True,
            "cards": [
                {
                    "name": "Sol Ring",
                    "qty": 1,
                    "set": "cmm",
                    "cn": "411",
                    "finish": "foil",
                    "uid": "u-sol",
                    "mana": "{1}",
                    "mv": 1,
                    "type": "Artifact",
                    "in_deck": True,
                },
                {
                    "name": "Cultivate <img src=x onerror=alert(1)>",
                    "qty": 2,
                    "set": "",
                    "cn": "",
                    "finish": "",
                    "uid": "",
                    "mana": "{2}{G}",
                    "mv": 3,
                    "type": "Sorcery",
                    "in_deck": True,
                },
            ],
        },
        {
            "name": "Maybeboard",
            "count": 1,
            "in_deck": False,
            "cards": [
                {
                    "name": "Island",
                    "qty": 1,
                    "set": "",
                    "cn": "",
                    "finish": "",
                    "uid": "",
                    "mana": "",
                    "mv": 0,
                    "type": "Land",
                    "in_deck": False,
                }
            ],
        },
    ],
}

ROWS = [
    {
        "input_name": "Sol Ring",
        "quantity": 2,
        "status": "exact",
        "note": "",
        "suggestions": [],
        "foil": False,
        "card": {
            "name": "Sol Ring",
            "set": "cmm",
            "collector_number": "411",
            "scryfall_id": "s-1",
            "image_small": "https://cards.scryfall.io/small/front/1.jpg",
        },
    },
    {
        "input_name": "Cultivatz",
        "quantity": 1,
        "status": "ambiguous",
        "note": "two names fit",
        "suggestions": ["Cultivate", "Cultivatx"],
        "foil": False,
        "card": None,
    },
    {
        "input_name": "Islnad <img src=x onerror=alert(1)>",
        "quantity": 3,
        "status": "fuzzy",
        "note": "name corrected",
        "suggestions": [],
        "foil": True,
        "card": {"name": "Island", "set": "", "collector_number": "", "scryfall_id": "", "image_small": ""},
    },
]


def _route(page, deck: dict | None = None, printings: dict | None = None):
    def link(route):
        body = deck if deck is not None else printings
        route.fulfill(status=200, content_type="application/json", body=json.dumps(body))

    def scryfall_api(route):
        url = route.request.url
        if "format=image" in url or "cards.scryfall.io" in url:
            route.fulfill(status=200, content_type="image/png", body=PNG)
        else:
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {
                        "object": "card",
                        "name": "Sol Ring",
                        "mana_cost": "{1}",
                        "type_line": "Artifact",
                        "oracle_text": "{T}: Add {C}{C}. <b>not markup</b>",
                    }
                ),
            )

    page.route("https://mtg.test/cards/data/*", link)
    page.route("https://api.scryfall.com/**", scryfall_api)
    page.route("https://cards.scryfall.io/**", scryfall_api)


def _open(
    page, name: str, result: dict, *, width: int = 600, theme: str = "dark", host_extra: dict | None = None
):
    page.set_content(HOST % ("#262624" if theme == "dark" else "#ffffff", width, theme))
    page.evaluate("(r) => { window.toolResult = r; }", result)
    page.evaluate("(x) => { window.hostExtra = x; }", host_extra or {})
    page.evaluate("(html) => { document.getElementById('f').srcdoc = html; }", card_html(name))
    page.wait_for_function(
        "() => window.log.some(m => m.method === 'ui/notifications/initialized')", timeout=10_000
    )
    return page.frame_locator("#f")


def _calls(page, method: str) -> list[dict]:
    return [m for m in page.evaluate("() => window.log") if m.get("method") == method]


def _shot(page, name: str) -> None:
    where = os.environ.get("MTG_CARD_SHOTS")
    if where:
        Path(where).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(where) / f"{name}.png"), full_page=True)


def test_printings_card_lists_pictures_filters_and_sends_the_pick_to_the_assistant() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, printings=PRINTINGS)
            summary = {
                "ok": True,
                "oracle_id": "o-1",
                "name": "Sol Ring",
                "total_cards": 3,
                "has_more": False,
            }
            card = _open(
                page,
                "printings-card",
                {
                    "structuredContent": summary,
                    "_meta": {"mtg/card": {"link": LINK, "name": "Sol Ring", "oracle_id": "o-1", "total": 3}},
                },
            )
            card.locator(".tile").nth(2).wait_for()
            assert card.locator("#title").inner_text() == "Printings of Sol Ring"
            assert card.locator("#count").inner_text().lower() == "3 printings"
            assert card.locator(".tile").count() == 3 and card.locator(".tile img").count() == 3
            # Set names are data: the hostile one is text in the tile's label.
            assert "Secret Lair <b>Drop</b>" in card.locator(".tile").nth(1).get_attribute("aria-label")
            assert "foil only" in card.locator(".tile").nth(1).inner_text()
            # No fullscreen offered by this host: no Expand button.
            assert card.locator("#expand").get_attribute("class") == "small hidden"
            _shot(page, "printings-dark-600")
            # Filter by set name or code, and by finish.
            card.locator("#filter").fill("legends")
            assert card.locator(".tile").count() == 1 and "CMR" in card.locator(".tile").first.inner_text()
            card.locator("#filter").fill("")
            card.locator("#finish").select_option("etched")
            assert card.locator(".tile").count() == 1
            card.locator("#finish").select_option("")
            assert card.locator(".tile").count() == 3
            # Tap one: it is marked, the status says so, and the assistant hears the pick.
            card.locator(".tile").nth(0).click()
            card.locator("#status.ok").wait_for()
            assert "Picked: Sol Ring" in card.locator("#status").inner_text()
            assert card.locator(".tile.picked").count() == 1
            page.wait_for_function(
                "() => window.log.some(m => m.method === 'ui/update-model-context')", timeout=5_000
            )
            told = _calls(page, "ui/update-model-context")[-1]["params"]["content"][0]["text"]
            assert (
                'set_code "cmm"' in told and 'collector_number "411"' in told and "Commander Masters" in told
            )
            assert card.locator("a.link", has_text="Open the picked printing on Scryfall").count() == 1
            card.locator("a.link").first.click()
            page.wait_for_function("() => window.log.some(m => m.method === 'ui/open-link')", timeout=5_000)
            assert _calls(page, "ui/open-link")[0]["params"] == {"url": "https://scryfall.com/card/cmm/411"}
            # A host that offers fullscreen gets an Expand button that asks for it.
            card = _open(
                page,
                "printings-card",
                {"structuredContent": summary, "_meta": {"mtg/card": {"link": LINK}}},
                host_extra={"availableDisplayModes": ["inline", "fullscreen"]},
            )
            card.locator(".tile").first.wait_for()
            card.locator("#expand:not(.hidden)").click()
            page.wait_for_function(
                "() => window.log.some(m => m.method === 'ui/request-display-mode')", timeout=5_000
            )
            assert _calls(page, "ui/request-display-mode")[0]["params"] == {"mode": "fullscreen"}
            # Without a link (the card switched off server-side, or an old result) the card says where
            # the list is.
            card = _open(page, "printings-card", {"structuredContent": summary})
            card.locator("#status:not(.hidden)").wait_for()
            assert "assistant's message" in card.locator("#status").inner_text()
            # An error result shows the message.
            card = _open(
                page,
                "printings-card",
                {"structuredContent": {"ok": False, "error": "not_found", "message": "no card found"}},
            )
            card.locator("#status.err").wait_for()
            assert "no card found" in card.locator("#status").inner_text()
            assert not errors, errors
        finally:
            browser.close()


def test_picker_card_keeps_drops_and_picks_then_tells_the_assistant() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, printings=PRINTINGS)
            card = _open(
                page,
                "picker-card",
                {"structuredContent": {"ok": True, "cards": []}, "_meta": {"mtg/card": {"rows": ROWS}}},
            )
            card.locator(".row").nth(2).wait_for()
            rows = card.locator("#rows > .row")
            assert rows.count() == 3 and card.locator("#count").inner_text().lower() == "3 rows"
            assert "Islnad <img src=x onerror=alert(1)>" in rows.nth(2).inner_text()  # text, not markup
            assert "corrected" in rows.nth(2).inner_text() and "foil" in rows.nth(2).inner_text()
            assert "which one?" in rows.nth(1).inner_text()
            # Four things across one line: the tick, the picture, the name and the count (the count
            # must not wrap under the tick).
            tick, qty = (
                rows.nth(0).locator("input.check").bounding_box(),
                rows.nth(0).locator(".qty").bounding_box(),
            )
            assert tick and qty and qty["x"] > tick["x"] and abs(qty["y"] - tick["y"]) < tick["height"]
            assert rows.nth(1).locator(".sugg .tile").count() == 2
            assert rows.nth(1).locator("input.check").is_disabled()  # nothing to keep until a pick
            assert "not recognised" in card.locator("#note").inner_text()
            _shot(page, "picker-dark-600")
            # Pick a suggestion, untick the fuzzy row, confirm.
            rows.nth(1).locator(".sugg .tile").first.click()
            rows = card.locator("#rows > .row")
            assert "picked" in rows.nth(1).inner_text() and rows.nth(1).locator("input.check").is_checked()
            rows.nth(2).locator("input.check").uncheck()
            card.locator("#use").click()
            card.locator("#status.ok").wait_for()
            page.wait_for_function(
                "() => window.log.some(m => m.method === 'ui/update-model-context')", timeout=5_000
            )
            told = _calls(page, "ui/update-model-context")[-1]["params"]["content"][0]["text"]
            assert '2 "Sol Ring" ("CMM") "411"' in told and '1 "Cultivate"' in told
            assert 'Left out: "Island"' in told
            assert 'the name read as "Cultivatz" is "Cultivate"' in told
            assert card.locator("#use").count() == 0  # confirmed once; the assistant has the list
            assert card.locator("input.check").first.is_disabled()
            assert not errors, errors
        finally:
            browser.close()


def test_deck_card_groups_by_category_and_shows_rules_text_on_tap() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            _route(page, deck=DECK)
            card = _open(
                page,
                "deck-card",
                {
                    "structuredContent": {"ok": True, "name": "Sample <b>Deck</b>"},
                    "_meta": {"mtg/card": {"link": LINK, "name": "Sample <b>Deck</b>"}},
                },
            )
            card.locator(".row.cardrow").nth(3).wait_for()
            assert card.locator("#title").inner_text() == "Sample <b>Deck</b>"
            assert card.locator("#count").inner_text().lower() == "4 cards + 1 aside"
            assert card.locator(".row.group").count() == 3 and card.locator(".row.cardrow").count() == 4
            assert "not in the deck" in card.locator(".row.group").nth(2).inner_text().lower()
            assert (
                "Cultivate <img src=x onerror=alert(1)>" in card.locator(".row.cardrow").nth(2).inner_text()
            )
            assert (
                "Commander:" in card.locator("#stat").inner_text()
                and card.locator(".curve span").count() == 8
            )
            assert card.locator(".pips span").count() == 2
            _shot(page, "deck-dark-600")
            # Collapse a category, find a card, switch to pictures.
            card.locator(".row.group").nth(1).click()
            assert card.locator(".row.cardrow").count() == 2
            card.locator("#filter").fill("sol")
            assert card.locator(".row.cardrow").count() == 1
            card.locator("#filter").fill("")
            card.locator("#viewbtn").click()
            card.locator("#grid .tile").first.wait_for()
            assert (
                card.locator("#grid .tile").count() >= 2 and card.locator("#viewbtn").inner_text() == "List"
            )
            _shot(page, "deck-pictures-dark-600")
            card.locator("#viewbtn").click()
            # Tap a card: the large picture and the rules text, fetched from Scryfall by the card.
            card.locator(".row.cardrow").first.click()
            card.locator("#detail:not(.hidden)").wait_for()
            card.locator("#dtext", has_text="Add {C}{C}").wait_for(timeout=5_000)
            assert "<b>not markup</b>" in card.locator("#dtext").inner_text()
            assert card.locator("#dname").inner_text().startswith("1× ")
            _shot(page, "deck-detail-dark-600")
            card.locator("#close").click()
            assert card.locator("#detail").get_attribute("class") == "detail hidden"
            card.locator("a.link", has_text="Open in the app").click()
            page.wait_for_function("() => window.log.some(m => m.method === 'ui/open-link')", timeout=5_000)
            assert _calls(page, "ui/open-link")[0]["params"] == {"url": "https://mtg.test/decks/42"}
            # The link refused (expired): the card says so instead of hanging.
            page.unroute("https://mtg.test/cards/data/*")
            page.route(
                "https://mtg.test/cards/data/*",
                lambda r: r.fulfill(
                    status=404,
                    content_type="application/json",
                    body=json.dumps(
                        {
                            "ok": False,
                            "error": "expired",
                            "message": "This card's link is no longer valid; ask for the data again.",
                        }
                    ),
                ),
            )
            card = _open(
                page,
                "deck-card",
                {"structuredContent": {"ok": True, "name": "X"}, "_meta": {"mtg/card": {"link": LINK}}},
            )
            card.locator("#status.err").wait_for()
            assert "no longer valid" in card.locator("#status").inner_text()
            assert not errors, errors
        finally:
            browser.close()


def test_account_card_points_at_what_is_missing() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            status = {
                "linked": False,
                "archidekt_username": None,
                "writes_enabled": True,
                "account_page": "https://mtg.test/account",
                "approval_mode": "manual",
                "approval_mode_note": "Every change waits for your press. Change it on the account page.",
            }
            card = _open(page, "account-card", {"structuredContent": status})
            card.locator("#open").wait_for()
            assert card.locator("#state").inner_text().lower() == "setup needed"
            assert "link your Archidekt account" in card.locator("#summary").inner_text()
            assert card.locator("#open").inner_text() == "Link Archidekt on the Account page"
            assert "Ask me every time" in card.locator("#facts").inner_text()
            _shot(page, "account-dark-600")
            card.locator("#open").click()
            page.wait_for_function("() => window.log.some(m => m.method === 'ui/open-link')", timeout=5_000)
            assert _calls(page, "ui/open-link")[0]["params"] == {"url": "https://mtg.test/account"}
            ready = {**status, "linked": True, "archidekt_username": "alice <b>x</b>", "name": "Alice"}
            card = _open(page, "account-card", {"structuredContent": ready})
            card.locator("#open").wait_for()
            assert card.locator("#state").inner_text().lower() == "ready"
            assert (
                "alice <b>x</b>" in card.locator("#facts").inner_text()
                and "Alice" in card.locator("#facts").inner_text()
            )
            assert card.locator("#open").inner_text() == "Open the Account page"
            # whoami's shape works too.
            who = {
                "sub": "s",
                "name": "Alice",
                "groups": ["mtg-gateway-users"],
                "token_expires_in_seconds": 3000,
                "gateway_version": "0.7.9",
                "account_page": "https://mtg.test/account",
            }
            card = _open(page, "account-card", {"structuredContent": who})
            card.locator("#open").wait_for()
            assert (
                "mtg-gateway-users" in card.locator("#facts").inner_text()
                and "version 0.7.8" in card.locator("#facts").inner_text()
            )
            assert not errors, errors
        finally:
            browser.close()


@pytest.mark.skipif(not os.environ.get("MTG_CARD_SHOTS"), reason="screenshots only on request")
def test_screenshots_at_phone_fold_and_desktop_widths_light_and_dark() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            for theme in ("light", "dark"):
                for label, width in (("phone", 360), ("fold", 672), ("desktop", 1100)):
                    page = browser.new_page(viewport={"width": width + 16, "height": 900})
                    _route(page, printings=PRINTINGS)
                    card = _open(
                        page,
                        "printings-card",
                        {
                            "structuredContent": {"ok": True, "name": "Sol Ring", "total_cards": 3},
                            "_meta": {"mtg/card": {"link": LINK}},
                        },
                        width=width,
                        theme=theme,
                    )
                    card.locator(".tile").nth(2).wait_for()
                    card.locator(".tile").first.click()
                    page.wait_for_timeout(200)
                    _shot(page, f"printings-{theme}-{label}")
                    card = _open(
                        page,
                        "picker-card",
                        {"structuredContent": {"ok": True}, "_meta": {"mtg/card": {"rows": ROWS}}},
                        width=width,
                        theme=theme,
                    )
                    card.locator(".row").nth(2).wait_for()
                    page.wait_for_timeout(200)
                    _shot(page, f"picker-{theme}-{label}")
                    page.unroute("https://mtg.test/cards/data/*")
                    _route(page, deck=DECK)
                    card = _open(
                        page,
                        "deck-card",
                        {
                            "structuredContent": {"ok": True, "name": "Sample Deck"},
                            "_meta": {"mtg/card": {"link": LINK}},
                        },
                        width=width,
                        theme=theme,
                    )
                    card.locator(".row.cardrow").nth(3).wait_for()
                    page.wait_for_timeout(200)
                    _shot(page, f"deck-{theme}-{label}")
                    card.locator(".row.cardrow").first.click()
                    card.locator("#dtext", has_text="Add").wait_for()
                    _shot(page, f"deck-detail-{theme}-{label}")
                    card = _open(
                        page,
                        "account-card",
                        {
                            "structuredContent": {
                                "linked": False,
                                "writes_enabled": True,
                                "account_page": "https://mtg.test/account",
                                "approval_mode": "manual",
                            }
                        },
                        width=width,
                        theme=theme,
                    )
                    card.locator("#open").wait_for()
                    page.wait_for_timeout(200)
                    _shot(page, f"account-{theme}-{label}")
                    page.close()
        finally:
            browser.close()
