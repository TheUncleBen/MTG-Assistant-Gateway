"""When Archidekt's deck list doesn't say who owns its decks (the owner field missing, renamed
or null), My decks says the list couldn't be read, with Try again, instead of showing an empty
list or "No decks yet"; and Try again reads the list again rather than the cached answer."""

from __future__ import annotations

import html
import json
from typing import Any

from mtg_gateway.archidekt import ArchidektClient, Pacer

from .conftest import COLLECTION, Stack, stack_with
from .test_backup_copies_hidden import UNREADABLE_OWNER_ROWS
from .test_browse_collection import NAV, linked
from .test_decks_and_proxy import call, linked_user, structured

stack_options = stack_with(COLLECTION)

NONE_READ = html.escape("Your deck list couldn't be read from Archidekt. Try again in a moment.")
SOME_READ = html.escape(
    "Part of your deck list couldn't be read from Archidekt, so some decks may be missing here. "
    "Try again in a moment."
)
# Try again asks for a real re-read (a partial list is otherwise kept for a minute)
TRY_AGAIN = "<a class='btn btn-primary' href='/decks?reread=1'>Try again</a>"
TRY_AGAIN_HOME = "<a class='btn btn-primary' href='/?reread=1'>Try again</a>"
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
        # Try again reads the list again at once (a list that could not be read is kept only briefly)
        ark._listing_row = real_row
        again = await b.http.get("/decks?reread=1", headers=NAV)
        assert again.status_code == 303 and again.headers["location"] == "/decks", again.headers
        page = await b.http.get("/decks", headers=NAV)
        assert NONE_READ not in page.text and "Sample Commander Deck" in page.text
        assert ">Try again</a>" not in page.text
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


async def test_a_partly_read_list_is_kept_briefly_and_try_again_reads_it_again(stack: Stack) -> None:  # noqa: F811
    """A list with unreadable entries is kept for about a minute, so visiting My
    decks and Home again does not re-read Archidekt each time; Try again (and a minute passing)
    reads it again. Try again on a list that read fine sends nothing more."""
    b = await linked(stack)
    ark, svc = stack.ark, stack.h.app.state.gateway.decks
    sub = _sub(stack)
    real_row = ark._listing_row
    ark.decks[44] = json.loads(json.dumps({**ark.decks[42], "id": 44, "name": "Unread deck"}))
    ark._listing_row = lambda d: {**real_row(d), "owner": None} if d["id"] == 44 else real_row(d)

    def listings() -> int:
        return sum(1 for method, path in ark.calls if method == "GET" and path == "/api/decks/v3/")

    try:
        svc.deck_lists.drop(sub)
        page = await b.http.get("/decks", headers=NAV)
        assert SOME_READ in page.text and TRY_AGAIN in page.text
        read = listings()
        assert read >= 1
        # visiting again, and the home page, use the list read a moment ago
        page = await b.http.get("/decks", headers=NAV)
        assert SOME_READ in page.text and "Sample Commander Deck" in page.text
        home = await b.http.get("/", headers=NAV)
        assert SOME_READ in home.text and TRY_AGAIN_HOME in home.text
        assert (await b.http.get("/api/decks/mine?shape=list")).json()["total"] == 1
        assert listings() == read
        # Try again reads Archidekt again, then shows the page it was pressed on
        again = await b.http.get("/decks?q=Sample&order=name&reread=1", headers=NAV)
        assert again.status_code == 303, again.status_code
        assert again.headers["location"] == "/decks?q=Sample&order=name"
        page = await b.http.get(again.headers["location"], headers=NAV)
        assert listings() == read + 1 and SOME_READ in page.text
        again = await b.http.get("/?reread=1", headers=NAV)
        assert again.status_code == 303 and again.headers["location"] == "/", again.headers
        await b.http.get("/", headers=NAV)
        assert listings() == read + 2
        # a minute on, the partial list is read again by itself (never served stale)
        at, wall, value = svc.deck_lists._entries[sub]
        svc.deck_lists._entries[sub] = (at - 61, wall, value)
        ark._listing_row = real_row
        del ark.decks[44]
        page = await b.http.get("/decks", headers=NAV)
        assert listings() == read + 3
        assert "be read from Archidekt" not in page.text and "Total decks: 1" in page.text
        # the list read fine: Try again (an old link) sends nothing more, and it is kept as usual
        again = await b.http.get("/decks?reread=1", headers=NAV)
        assert again.status_code == 303
        await b.http.get("/decks", headers=NAV)
        assert listings() == read + 3
    finally:
        ark._listing_row = real_row
        ark.decks.pop(44, None)
        await b.aclose()


async def test_a_readable_list_shows_no_notice(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        page = await b.http.get("/decks", headers=NAV)
        assert "be read from Archidekt" not in page.text and ">Try again</a>" not in page.text
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


async def test_home_my_decks_panel_says_the_list_could_not_be_read(stack: Stack) -> None:  # noqa: F811
    """The home page's My decks panel tells the same truth as /decks: the /decks notice and
    Try again, never "No decks yet", when nothing could be read; the covers it did read plus the
    "some may be missing" notice when part of it could."""
    b = await linked(stack)
    ark, svc = stack.ark, stack.h.app.state.gateway.decks
    real_row = ark._listing_row
    try:
        for shape, change in UNREADABLE.items():
            ark._listing_row = lambda d, change=change: change(real_row(d))
            svc.deck_lists.drop(_sub(stack))
            page = await b.http.get("/", headers=NAV)
            assert page.status_code == 200, shape
            assert NONE_READ in page.text and TRY_AGAIN_HOME in page.text, shape
            assert "No decks yet" not in page.text, shape
            # the cold-start placeholder's JSON brings the same panel
            data = (await b.http.get("/api/decks/mine?shape=recent")).json()
            assert NONE_READ in data["html"] and TRY_AGAIN_HOME in data["html"], shape
            assert "No decks yet" not in data["html"], shape
        # part of the list read: the covers it read, and the notice that some may be missing
        ark.decks[44] = json.loads(json.dumps({**ark.decks[42], "id": 44, "name": "Unread deck"}))
        ark._listing_row = lambda d: {**real_row(d), "owner": None} if d["id"] == 44 else real_row(d)
        svc.deck_lists.drop(_sub(stack))
        page = await b.http.get("/", headers=NAV)
        assert SOME_READ in page.text and TRY_AGAIN_HOME in page.text
        assert "Sample Commander Deck" in page.text and "Unread deck" not in page.text
        data = (await b.http.get("/api/decks/mine?shape=recent")).json()
        assert SOME_READ in data["html"] and "Sample Commander Deck" in data["html"]
        # readable again: no notice
        ark._listing_row = real_row
        del ark.decks[44]
        svc.deck_lists.drop(_sub(stack))
        page = await b.http.get("/", headers=NAV)
        assert "be read from Archidekt" not in page.text and ">Try again</a>" not in page.text
        assert "All decks (1)" in page.text
    finally:
        ark._listing_row = real_row
        ark.decks.pop(44, None)
        await b.aclose()


async def test_list_my_decks_tool_says_when_entries_could_not_be_read(stack: Stack) -> None:  # noqa: F811
    """The assistant's list_my_decks says how many entries were left out because their owner
    couldn't be read, with a note to relay; its other fields stay as they were."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)  # type: ignore[arg-type]
    svc = h.app.state.gateway.decks
    real_row = ark._listing_row
    try:
        out = structured(await call(h, token, "list_my_decks"))
        assert out["ok"] and out["count"] == 1 and out["unreadable"] == 0 and "note" not in out
        ark.decks[44] = json.loads(json.dumps({**ark.decks[42], "id": 44, "name": "Unread deck"}))
        ark._listing_row = lambda d: {**real_row(d), "owner": None} if d["id"] == 44 else real_row(d)
        svc.deck_lists.drop(_sub(stack))
        out = structured(await call(h, token, "list_my_decks"))
        assert out["ok"] and [d["id"] for d in out["decks"]] == ["42"] and out["count"] == 1
        assert out["unreadable"] == 1 and out["note"] == html.unescape(SOME_READ)
        ark._listing_row = lambda d: {**real_row(d), "owner": None}
        svc.deck_lists.drop(_sub(stack))
        out = structured(await call(h, token, "list_my_decks"))
        assert out["ok"] and out["decks"] == [] and out["count"] == 0
        assert out["unreadable"] == 2 and out["note"] == html.unescape(NONE_READ)
    finally:
        ark._listing_row = real_row
        ark.decks.pop(44, None)
