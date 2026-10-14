"""Q64: when Archidekt's deck list doesn't say who owns its decks (the owner field missing, renamed
or null), My decks says the list couldn't be read, with Try again, instead of showing an empty
list or "No decks yet"; and Try again reads the list again rather than the cached answer."""

from __future__ import annotations

import html
import json
from typing import Any

from mtg_gateway.archidekt import ArchidektClient, Pacer

from .test_backup_copies_hidden import UNREADABLE_OWNER_ROWS
from .test_browse_collection import NAV, Stack, linked, stack  # noqa: F401 - fixture

NONE_READ = html.escape("Your deck list couldn't be read from Archidekt. Try again in a moment.")
SOME_READ = html.escape(
    "Part of your deck list couldn't be read from Archidekt, so some decks may be missing here. "
    "Try again in a moment."
)
TRY_AGAIN = "<a class='btn btn-primary' href=''>Try again</a>"
UNREADABLE = {k: v for k, v in UNREADABLE_OWNER_ROWS.items() if k != "owner filter ignored"}


def _sub(st: Stack) -> str:
    return st.h.app.state.gateway.db._one("SELECT sub FROM users", ())["sub"]


async def test_my_decks_says_the_list_could_not_be_read(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    ark, svc = stack.ark, stack.h.app.state.gateway.decks
    real_row = ark._listing_row
    try:
        for shape, change in UNREADABLE.items():
            ark._listing_row = lambda d, change=change: change(real_row(d))
            svc.deck_lists.drop(_sub(stack))
            page = await b.http.get("/decks", headers=NAV)
            assert page.status_code == 200, shape
            assert NONE_READ in page.text and TRY_AGAIN in page.text, shape
            assert "No decks yet" not in page.text, shape
            assert "Total decks: 0" not in page.text, shape  # a count it doesn't know
            assert "href='/decks/new'" in page.text, shape  # New deck still there
            # the placeholder's JSON (a cold start) brings the same notice, and no total
            data = (await b.http.get("/api/decks/mine?shape=list")).json()
            assert NONE_READ in data["html"] and TRY_AGAIN in data["html"], shape
            assert "No decks yet" not in data["html"] and data["total"] is None, shape
        # Try again reads the list again at once (the answer that could not be read is not kept)
        ark._listing_row = real_row
        page = await b.http.get("/decks", headers=NAV)
        assert NONE_READ not in page.text and "Sample Commander Deck" in page.text
        assert TRY_AGAIN not in page.text
    finally:
        ark._listing_row = real_row
        await b.aclose()


async def test_my_decks_shows_what_it_read_and_says_some_may_be_missing(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    ark, svc = stack.ark, stack.h.app.state.gateway.decks
    real_row = ark._listing_row
    # a second deck of alice's whose owner field comes back null
    ark.decks[44] = json.loads(json.dumps({**ark.decks[42], "id": 44, "name": "Unread deck"}))

    def row(d: dict[str, Any]) -> dict[str, Any]:
        out = real_row(d)
        return {**out, "owner": None} if d["id"] == 44 else out

    ark._listing_row = row
    try:
        svc.deck_lists.drop(_sub(stack))
        page = await b.http.get("/decks", headers=NAV)
        assert SOME_READ in page.text and TRY_AGAIN in page.text
        assert "Sample Commander Deck" in page.text and "Unread deck" not in page.text
        data = (await b.http.get("/api/decks/mine?shape=list")).json()
        assert SOME_READ in data["html"] and "Sample Commander Deck" in data["html"]
        assert data["total"] == 1
    finally:
        ark._listing_row = real_row
        await b.aclose()


async def test_a_readable_list_shows_no_notice(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        page = await b.http.get("/decks", headers=NAV)
        assert "be read from Archidekt" not in page.text and TRY_AGAIN not in page.text
        assert "Total decks: 1" in page.text
    finally:
        await b.aclose()


async def test_the_list_counts_entries_whose_owner_cannot_be_read() -> None:
    class One(ArchidektClient):
        def __init__(self, rows: list[dict[str, Any]]) -> None:
            super().__init__("https://archidekt.com/api", "test", Pacer(0.0))
            self.rows = rows

        async def _request(self, method: str, path: str, **kw: Any) -> Any:  # type: ignore[override]
            return {"count": len(self.rows), "next": None, "results": self.rows}

    mine = {"id": 1, "name": "A", "owner": {"id": 7, "username": "alice"}, "updatedAt": "2026-10-01"}
    other = {**mine, "id": 2, "owner": {"id": 8, "username": "amy"}}
    null = {**mine, "id": 3, "owner": None}
    assert (await One([mine]).list_decks("t", "alice", "7")).unreadable == 0
    assert (await One([mine, other]).list_decks("t", "alice", "7")).unreadable == 0  # readable, not hers
    listed = await One([mine, null, {**null, "id": 4}]).list_decks("t", "alice", "7")
    assert listed.unreadable == 2 and [d["id"] for d in listed] == ["1"]
