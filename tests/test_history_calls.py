"""History's Archidekt budget (Standard 2): one page of History reads the member's deck list once,
and never reads a deck one by one, even when a deck in its entries is gone from Archidekt
(independent review of 0.7.46 reported two reads of a deleted deck; this holds it)."""

from __future__ import annotations

import time

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_history_page import seed_history

stack_options = stack_with(COMPANION)


async def test_history_reads_the_deck_list_once_and_no_deck_by_itself(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        sub = stack.gw.db._one("SELECT sub FROM users", ())["sub"]
        seed_history(stack.gw.db, sub, now=int(time.time()))
        stack.ark.decks.pop(43, None)  # deleted on Archidekt
        for path in ("/history", "/history?deck_id=43", "/history?group=deck", "/history?deck_id=42"):
            n0 = len(stack.ark.calls)
            r = await b.http.get(path, headers=NAV)
            assert r.status_code == 200
            calls = stack.ark.calls[n0:]
            assert not [c for c in calls if c[1].rstrip("/").split("/")[-1].isdigit()], (path, calls)
            assert len(calls) <= 1, (path, calls)
    finally:
        await b.aclose()
