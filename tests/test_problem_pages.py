"""A dead end says what happened, once, in a sentence, with a way back (0.7.46 audit QG-19,
QG-21): a missing deck, a wrong deck address, a missing proposal, a missing forum thread, and a
proposal whose content failed the checks."""

from __future__ import annotations

from .conftest import Stack
from .test_companion import NAV, linked_browser


async def test_a_wrong_deck_address_is_one_capitalised_sentence_with_the_way_back(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        r = await b.http.get("/decks/abc", headers=NAV)
        assert r.status_code in (400, 404)
        assert (
            "<title>That is not an Archidekt deck" in r.text and "<h1>That is not an Archidekt deck" in r.text
        )
        # the sentence is the title and the heading, not a paragraph under the heading as well
        assert r.text.count("<p>That is not an Archidekt deck") == 0
        assert r.text.count("<p>this is not an Archidekt deck") == 0
        assert "class='btn btn-primary' href='/decks'" in r.text
        r = await b.http.get("/decks/999999", headers=NAV)
        assert r.status_code == 404 and "<p>This deck could not be found.</p>" not in r.text
    finally:
        await b.aclose()


async def test_a_missing_proposal_and_thread_have_a_way_back(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        r = await b.http.get("/proposals/nope", headers=NAV)
        assert r.status_code == 404
        assert "This proposal could not be found." in r.text and "No such proposal" not in r.text
        assert "href='/proposals'" in r.text and "Back to Assistant" in r.text
        r = await b.http.get("/forum/thread/999999", headers=NAV)
        assert r.status_code == 404, r.status_code
        assert "That thread could not be found." in r.text and "could not find that deck" not in r.text
        assert "Back to the forum" in r.text
        r = await b.http.get("/forum/thread/not-a-thread", headers=NAV)
        assert r.status_code == 400 and "That is not a thread" in r.text and "thread_id" not in r.text
    finally:
        await b.aclose()


def test_a_validators_refusal_is_said_in_the_members_words() -> None:
    from mtg_gateway.pages import _page_step

    p = {
        "state": "failed",
        "result": {"detail": "changes must be a non-empty list"},
        "created_by": "assistant",
        "actor": "app-1",
    }
    text = _page_step(p)
    assert "empty or damaged" in text and "Ask your assistant for a new one" in text
    assert "non-empty" not in text and "must be" not in text
    p["result"] = {"detail": "Archidekt refused the change: the deck is locked"}
    assert "the deck is locked" in _page_step(p)
