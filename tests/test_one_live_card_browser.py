"""0.7.46, one live card per proposal, in a real browser with the fake MCP Apps host of
test_card_browser: a card whose proposal has a newer card in the chat, or is decided, becomes one
line with no buttons and no pictures and stops reading; a read that fails is tried again a little
later; a big proposal shows a few rows and loads the rest on Show all; Reject sends the reason."""

from __future__ import annotations

import time

import pytest

from mtg_gateway.approve import APPROVAL_META_KEY

from . import test_card_browser as tcb
from .test_card_browser import PROPOSAL, _calls, _confirms, _open
from .test_consent_browser import _chromium_or_skip, _launch

pytest.importorskip("playwright")

NEW_DECK = {
    **PROPOSAL,
    "kind": "create_deck",
    "deck_id": "new",
    "deck_name": "Atraxa",
    "version": 1,
    "card_seq": 1,
    "warnings": ["A Commander deck has 100 cards; this one has 60."],
    "rows": [{"kind": "new_deck", "name": "Atraxa", "format": "commander", "cards": 60, "private": True}]
    + [{"kind": "add", "name": f"Card {i}", "qty": 1} for i in range(7)],
    "rows_total": 61,
    "rows_summary": {"adds": 60, "removes": 0, "others": 1, "rows": 61},
}
WHOLE = {
    **NEW_DECK,
    "rows": NEW_DECK["rows"][:1] + [{"kind": "add", "name": f"Card {i}", "qty": 1} for i in range(60)],
}
WHOLE.pop("rows_total")


def _reads(page) -> list[dict]:
    return [m for m in _calls(page, "tools/call") if m["params"]["name"] == "get_proposal"]


def _browser(p, exe: str):
    return _launch(p, exe)


def test_an_older_card_steps_aside_for_the_newest_and_stops_reading() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        try:
            page = browser.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            # the read says a newer version's card (number 2) is in the chat
            newer = {**NEW_DECK, "version": 2, "card_seq": 2}
            card = _open(
                page, {"structuredContent": NEW_DECK, "_meta": {APPROVAL_META_KEY: "c1"}}, get_reply=newer
            )
            card.locator("#card.stub").wait_for(timeout=5_000)
            assert "Replaced by a newer version below" in card.locator("#status").inner_text()
            assert card.locator("#state").inner_text().lower() == "replaced"
            assert card.locator("#approve").count() == 0 and card.locator("#reject").count() == 0
            assert card.locator("ul.changes li").count() == 0 and card.locator("img").count() == 0
            assert card.locator("#summary").is_hidden()
            # it reads nothing more: shown again, or the chat coming back, changes nothing
            n = len(_reads(page))
            page.frames[1].evaluate(
                "() => { Object.defineProperty(document, 'visibilityState',"
                " {value: 'visible', configurable: true});"
                " document.dispatchEvent(new Event('visibilitychange')); }"
            )
            page.evaluate(
                "() => document.getElementById('f').contentWindow.postMessage({jsonrpc: '2.0',"
                " method: 'ui/notifications/tool-result', params: window.toolResult}, '*')"
            )
            page.wait_for_timeout(600)
            assert len(_reads(page)) == n
            assert card.locator("#approve").count() == 0
            assert _confirms(page) == []
            # the timed read asks for the first rows only, and names the card's own number
            assert _reads(page)[0]["params"]["arguments"] == {
                "proposal_id": "p-1",
                "rows_limit": 8,
                "card_seq": 1,
            }
            assert not errors, errors
        finally:
            browser.close()


def test_the_same_version_drawn_again_below_says_so_and_the_newest_keeps_its_buttons() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        try:
            page = browser.new_page()
            again = {**NEW_DECK, "card_seq": 3}
            card = _open(
                page, {"structuredContent": NEW_DECK, "_meta": {APPROVAL_META_KEY: "c1"}}, get_reply=again
            )
            card.locator("#card.stub").wait_for(timeout=5_000)
            assert "shown again in a newer card below" in card.locator("#status").inner_text()
            # the newest card itself (its own number is the newest) keeps Approve and Reject
            newest = {**NEW_DECK, "card_seq": 3}
            card = _open(
                page, {"structuredContent": newest, "_meta": {APPROVAL_META_KEY: "c3"}}, get_reply=newest
            )
            card.locator("#approve:enabled").wait_for(timeout=5_000)
            assert "stub" not in (card.locator("#card").get_attribute("class") or "")
            # a card drawn before card numbers existed (none in its result) is never stepped aside
            old = {k: v for k, v in NEW_DECK.items() if k != "card_seq"}
            card = _open(
                page, {"structuredContent": old, "_meta": {APPROVAL_META_KEY: "c0"}}, get_reply=again
            )
            card.locator("#approve:enabled").wait_for(timeout=5_000)
        finally:
            browser.close()


def test_a_decided_proposals_card_is_one_line_without_pictures() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        try:
            page = browser.new_page()
            for state, words in (
                ("withdrawn", "withdrew this change"),
                ("rejected", "Already rejected"),
                ("applied", "Already applied"),
                ("expired", "expired"),
            ):
                closed = {**NEW_DECK, "state": state, "closed_at": int(time.time())}
                card = _open(
                    page,
                    {"structuredContent": NEW_DECK, "_meta": {APPROVAL_META_KEY: "c1"}},
                    get_reply=closed,
                )
                card.locator("#card.stub").wait_for(timeout=5_000)
                assert words in card.locator("#status").inner_text(), state
                assert card.locator("#approve").count() == 0 and card.locator("img").count() == 0, state
                assert card.locator("ul.changes li").count() == 0, state
                assert card.locator("a.link", has_text="Open in the app or browser").count() == 1, state
            # a card drawn with its outcome (an apply's answer) keeps what was done
            applied = {**NEW_DECK, "state": "applied", "closed_at": int(time.time())}
            card = _open(page, {"structuredContent": applied, "_meta": {}}, get_reply=applied)
            card.locator("ul.changes li").first.wait_for(timeout=5_000)
            assert "stub" not in (card.locator("#card").get_attribute("class") or "")
        finally:
            browser.close()


def test_a_read_that_fails_is_tried_again_with_growing_waits(monkeypatch) -> None:
    from playwright.sync_api import sync_playwright

    html = tcb.card_html()
    assert "var RETRY_FIRST_MS = 2000, RETRY_LAST_MS = 60000, RETRY_MAX = 6;" in html
    fast = html.replace(
        "var RETRY_FIRST_MS = 2000, RETRY_LAST_MS = 60000, RETRY_MAX = 6;",
        "var RETRY_FIRST_MS = 150, RETRY_LAST_MS = 60000, RETRY_MAX = 6;",
    )
    monkeypatch.setattr(tcb, "card_html", lambda: fast)
    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        try:
            page = browser.new_page()
            card = _open(
                page, {"structuredContent": NEW_DECK, "_meta": {APPROVAL_META_KEY: "c1"}}, get_reply="refuse"
            )
            card.locator("#title").wait_for()
            # the first read and then retries after 150, 300, 600 ms ...
            page.wait_for_function(
                "() => window.log.filter(m => m.params && m.params.name === 'get_proposal').length >= 4",
                timeout=8_000,
            )
            assert "can't check for a newer version right now" in card.locator("#status").inner_text()
            # once the read gets through, the newer card is seen and this one steps aside
            page.evaluate("(r) => { window.getReply = r; }", {**NEW_DECK, "version": 2, "card_seq": 2})
            card.locator("#card.stub").wait_for(timeout=8_000)
            # the retries stop at RETRY_MAX
            page.evaluate("() => { window.getReply = 'refuse'; }")
            n = len(_reads(page))
            page.wait_for_timeout(800)
            assert len(_reads(page)) == n
        finally:
            browser.close()


def test_a_big_proposal_shows_a_few_rows_and_loads_the_rest_on_show_all() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        try:
            page = browser.new_page()
            card = _open(
                page, {"structuredContent": NEW_DECK, "_meta": {APPROVAL_META_KEY: "c1"}}, get_reply=NEW_DECK
            )
            card.locator("#more").wait_for()
            summary = card.locator("#summary").inner_text()
            assert summary.startswith("Commander deck, 60 cards") and "1 warning" in summary, summary
            assert "100 cards" in card.locator("#note").inner_text()
            assert card.locator("ul.changes li:not(.group):not(.more-row)").count() == 6
            assert card.locator("ul.changes img.pic").count() <= 6
            assert card.locator("#more").inner_text() == "Show all 61 changes"
            page.evaluate("(r) => { window.getReply = r; }", WHOLE)
            card.locator("#more").click()
            card.locator("ul.changes li:not(.group):not(.more-row)").nth(60).wait_for(timeout=5_000)
            whole = [m for m in _reads(page) if "rows_limit" not in m["params"]["arguments"]]
            assert len(whole) == 1 and whole[0]["params"]["arguments"] == {
                "proposal_id": "p-1",
                "card_seq": 1,
            }
            assert card.locator("ul.changes img.pic").count() <= 6  # the rest as text
            assert card.locator("#approve").count() == 1
        finally:
            browser.close()


def test_reject_sends_the_reason_typed() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        try:
            page = browser.new_page()
            rejected = {**NEW_DECK, "state": "rejected", "ok": True}
            card = _open(
                page, {"structuredContent": NEW_DECK, "_meta": {APPROVAL_META_KEY: "c1"}}, rejected, NEW_DECK
            )
            card.locator("#reject:enabled").wait_for(timeout=5_000)
            card.locator("#reject").click()
            card.locator("#reason").fill("keep the Islands")
            card.locator("#reject-go").click()
            page.wait_for_function(
                "() => window.log.some(m => m.params && m.params.name === 'confirm_proposal')", timeout=5_000
            )
            args = _confirms(page)[0]["params"]["arguments"]
            assert args == {
                "proposal_id": "p-1",
                "approval": "c1",
                "decision": "reject",
                "reason": "keep the Islands",
            }
            card.locator("#card.stub").wait_for(timeout=5_000)
        finally:
            browser.close()


_NODES = "() => document.getElementsByTagName('*').length"


def test_a_card_that_steps_aside_keeps_next_to_nothing() -> None:
    """Ten old cards in a chat should cost next to nothing: a card that steps aside drops its list,
    pictures, summary, note and footer (the elements left are the card's own frame)."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _browser(p, exe)
        try:
            page = browser.new_page()
            card = _open(
                page, {"structuredContent": NEW_DECK, "_meta": {APPROVAL_META_KEY: "c1"}}, get_reply=NEW_DECK
            )
            card.locator("#more").wait_for()
            page.evaluate("(r) => { window.getReply = r; }", WHOLE)
            card.locator("#more").click()
            card.locator("ul.changes li:not(.group):not(.more-row)").nth(60).wait_for(timeout=5_000)
            frame = page.frames[1]
            full = frame.evaluate(_NODES)
            page.evaluate("(r) => { window.getReply = r; }", {**NEW_DECK, "version": 2, "card_seq": 2})
            frame.evaluate(
                "() => { Object.defineProperty(document, 'visibilityState',"
                " {value: 'visible', configurable: true});"
                " document.dispatchEvent(new Event('visibilitychange')); }"
            )
            card.locator("#card.stub").wait_for(timeout=5_000)
            stub = frame.evaluate(_NODES)
            assert card.locator("ul.changes li").count() == 0 and card.locator("img").count() == 0
            assert card.locator("#summary").inner_text() == "" and card.locator("#note").inner_text() == ""
            assert stub * 4 < full, (stub, full)
        finally:
            browser.close()


def test_the_gateway_refusing_an_older_cards_approve_makes_it_step_aside() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    refused = {
        "ok": False,
        "error": "not_newest_card",
        "message": "A newer card for this change is further down the chat, so nothing was sent to "
        "Archidekt from this one. Use the newest card for this change.",
        "state": "pending",
        "version": 1,
    }
    with sync_playwright() as p:
        browser = _browser(p, exe)
        try:
            page = browser.new_page()
            card = _open(
                page, {"structuredContent": NEW_DECK, "_meta": {APPROVAL_META_KEY: "c1"}}, refused, NEW_DECK
            )
            card.locator("#approve:enabled").click()
            card.locator("#card.stub").wait_for(timeout=5_000)
            assert "Use the newest card" in card.locator("#status").inner_text()
            assert card.locator("#approve").count() == 0
            # its reads named its own number
            assert all(m["params"]["arguments"].get("card_seq") == 1 for m in _reads(page))
        finally:
            browser.close()


def _show_again(page) -> None:
    page.frames[1].evaluate(
        "() => { Object.defineProperty(document, 'visibilityState', {value: 'visible', configurable: true});"
        " document.dispatchEvent(new Event('visibilitychange')); }"
    )


def test_a_change_the_assistant_may_apply_has_apply_now_and_follows_its_state() -> None:
    """Semi or Full auto: the card offers Apply now and Reject (the assistant may still apply it),
    changes its button when the approval mode changes, and shows the change applied when the
    assistant applies it."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    auto = {
        **PROPOSAL,
        "assistant_may_apply": True,
        "approval_mode": "semi",
        "risk": "low",
        "risk_reason": "1 row",
    }
    with sync_playwright() as p:
        browser = _browser(p, exe)
        try:
            page = browser.new_page()
            card = _open(
                page, {"structuredContent": auto, "_meta": {APPROVAL_META_KEY: "c1"}}, get_reply=auto
            )
            card.locator("#approve:enabled").wait_for(timeout=5_000)
            assert card.locator("#approve").inner_text() == "Apply now"
            assert card.locator("#reject").inner_text() == "Reject"
            assert "apply it here with Apply now" in card.locator("#note").inner_text()
            # the member switched to Manual: the same card now asks for an Approve
            page.evaluate(
                "(r) => { window.getReply = r; }",
                {**auto, "assistant_may_apply": False, "approval_mode": "manual"},
            )
            _show_again(page)
            card.locator("#approve", has_text="Approve: apply to Archidekt").wait_for(timeout=5_000)
            # the assistant applied it: the card shows that, with nothing left to press
            page.evaluate(
                "(r) => { window.getReply = r; }",
                {**auto, "state": "applied", "applied_at": int(time.time())},
            )
            _show_again(page)
            card.locator("#state", has_text="Applied").wait_for(timeout=5_000)
            assert card.locator("#approve").count() == 0 and card.locator("#reject").count() == 0
        finally:
            browser.close()
