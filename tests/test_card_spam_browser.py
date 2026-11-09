"""0.7.47, no card spam in the chat (Ben's v39 feedback: ChatGPT drew ten or more full proposal
cards, most for changes already withdrawn or updated, and the chat lagged). A proposal card drawn
waiting draws one line, with no list, no pictures and no buttons, until its first read says it is
still the newest card of a change that still waits. So a chat with many cards loads the pictures
of the one that still asks for something; an older or closed card never draws its list at all.
A read that fails, or none in time, draws the card whole from its own result, as before."""

from __future__ import annotations

import pytest

from mtg_gateway.approve import APPROVAL_META_KEY

from .test_card_browser import _open
from .test_consent_browser import _chromium_or_skip, _launch
from .test_one_live_card_browser import NEW_DECK

pytest.importorskip("playwright")

NEWER = {**NEW_DECK, "version": 2, "__meta": {"mtg/card_seq": 2}}  # a newer card is below
RESULT = {"structuredContent": NEW_DECK, "_meta": {APPROVAL_META_KEY: "c1", "mtg/card_seq": 1}}


def _nothing_heavy(card) -> None:
    """One line: no change rows, no pictures, no buttons."""
    assert card.locator("ul.changes li").count() == 0
    assert card.locator("img").count() == 0
    assert card.locator("#approve").count() == 0 and card.locator("#reject").count() == 0


def _held(page) -> int:
    return page.evaluate("() => window.held.length")


def test_a_card_drawn_waiting_shows_one_line_until_its_first_read_answers() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            card = _open(page, RESULT, get_reply="hold")
            page.wait_for_function("() => window.held.length > 0", timeout=5_000)
            card.locator("#card.stub").wait_for(timeout=5_000)
            assert "Checking" in card.locator("#status").inner_text()
            _nothing_heavy(card)
            # the read says a newer card for this change is below: the list is never drawn
            page.evaluate("(r) => window.answerHeld(r)", NEWER)
            card.locator("#state", has_text="Older card").wait_for(timeout=5_000)
            _nothing_heavy(card)
        finally:
            browser.close()


@pytest.mark.parametrize("state", ["withdrawn", "rejected", "applied", "expired", "replaced"])
def test_a_card_whose_change_closed_before_it_was_drawn_never_draws_its_list(state: str) -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            card = _open(page, RESULT, get_reply="hold")
            page.wait_for_function("() => window.held.length > 0", timeout=5_000)
            _nothing_heavy(card)
            page.evaluate("(r) => window.answerHeld(r)", {**NEW_DECK, "state": state})
            card.locator("#state").filter(has_not_text="Waiting").wait_for(timeout=5_000)
            assert "stub" in (card.locator("#card").get_attribute("class") or "")
            _nothing_heavy(card)
        finally:
            browser.close()


def test_the_newest_card_of_a_waiting_change_draws_whole_once_checked() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            card = _open(page, RESULT, get_reply="hold")
            page.wait_for_function("() => window.held.length > 0", timeout=5_000)
            _nothing_heavy(card)
            page.evaluate("() => window.answerHeld(null)")  # the same change, still waiting
            card.locator("ul.changes li").first.wait_for(timeout=5_000)
            card.locator("#approve").wait_for(timeout=5_000)
            assert "stub" not in (card.locator("#card").get_attribute("class") or "")
            assert "Checking" not in card.locator("#status").inner_text()
        finally:
            browser.close()


def test_a_read_that_is_refused_draws_the_card_whole_from_its_result() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            card = _open(page, RESULT, get_reply="refuse")
            card.locator("ul.changes li").first.wait_for(timeout=5_000)
            card.locator("#approve").wait_for(timeout=5_000)
            assert "stub" not in (card.locator("#card").get_attribute("class") or "")
        finally:
            browser.close()


def test_a_read_that_never_answers_draws_the_card_whole_after_a_short_wait() -> None:
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            card = _open(page, RESULT, get_reply="hold", check_ms=300)
            card.locator("ul.changes li").first.wait_for(timeout=5_000)
            card.locator("#approve").wait_for(timeout=5_000)
            # the read answering late still steps an older card aside
            page.evaluate("(r) => window.answerHeld(r)", NEWER)
            card.locator("#state", has_text="Older card").wait_for(timeout=5_000)
            _nothing_heavy(card)
        finally:
            browser.close()


def test_ten_cards_in_one_chat_load_the_pictures_of_the_one_that_still_waits() -> None:
    """Ben's chat: one turn drew many cards for one deck; only the newest still waits."""
    from playwright.sync_api import sync_playwright

    exe = _chromium_or_skip()
    with sync_playwright() as p:
        browser = _launch(p, exe)
        try:
            page = browser.new_page()
            drawn_rows = 0
            for n in range(1, 11):
                newest = n == 10
                result = {
                    "structuredContent": {**NEW_DECK, "version": n},
                    "_meta": {APPROVAL_META_KEY: f"c{n}", "mtg/card_seq": n},
                }
                # what the gateway answers each card's read once the turn is over: version 10, card 10
                reply = {**NEW_DECK, "version": 10, "__meta": {"mtg/card_seq": 10}}
                card = _open(page, result, get_reply="hold")
                page.wait_for_function("() => window.held.length > 0", timeout=5_000)
                # a frame's whole life, drawn rows counted every time the list changes
                frame = page.frames[-1]
                frame.evaluate(
                    """() => { const seen = () => document.querySelectorAll('ul.changes li, img').length;
                       window.rowsSeen = seen();
                       new MutationObserver(() => { window.rowsSeen = Math.max(window.rowsSeen, seen()); })
                         .observe(document.body, {subtree: true, childList: true}); }"""
                )
                page.evaluate("(r) => window.answerHeld(r)", reply)
                if newest:
                    card.locator("ul.changes li").first.wait_for(timeout=5_000)
                else:
                    card.locator("#state", has_text="Older card").wait_for(timeout=5_000)
                drawn_rows += frame.evaluate("() => window.rowsSeen") if not newest else 0
            assert drawn_rows == 0
        finally:
            browser.close()
