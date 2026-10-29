"""History, Proposals and My activity end in a Load more button, never a Newer/Older pager
(Standard 13; independent review of 0.7.46: History at 390 ended in "1–25 of 32 ← Newer ·
Older →"). The button's link is the next page, so it works without scripts; the shared
static/loadmore.js adds that page's rows in place."""

from __future__ import annotations

import re
import time

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_history_page import seed_history

stack_options = stack_with(COMPANION)


def _more(page: str) -> tuple[str, str]:
    m = re.search(
        r"<div class='loadmore'><a class='btn btn-plain' href='([^']+)' rel='next' data-load-more"
        r" data-into='([^']+)'>",
        page,
    )
    assert m, page[-3000:]
    return m.group(1).replace("&amp;", "&"), m.group(2)


async def test_history_proposals_and_activity_load_more(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        sub = stack.gw.db._one("SELECT sub FROM users", ())["sub"]
        seed_history(stack.gw.db, sub, now=int(time.time()))
        for _ in range(30):  # enough of the member's own activity for a second page
            stack.gw.db.audit("signed_in", sub=sub, client_id="__browser__")
        for path, into in (
            ("/history", ".hy-table"),
            ("/proposals?show=all", ".pr-table"),
            ("/activity", ".act-table"),
        ):
            page = (await b.http.get(path, headers=NAV)).text
            assert "ui-pager" not in page and "Older" not in page and "Newer" not in page, path
            href, got_into = _more(page)
            assert got_into == into and "offset=25" in href, (path, href)
            assert re.search(r"<p class='muted'>25 of [\d,]+</p>", page), path
            assert "/static/loadmore.js" in page, path
            nxt = (await b.http.get(href, headers=NAV)).text
            assert nxt.count("class='tr") > 1, path
    finally:
        await b.aclose()
