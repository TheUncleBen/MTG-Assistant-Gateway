"""ArchidektClient.list_decks follows the listing's pages (a member with more than fifty decks)
and stops at MAX_LIST_PAGES, at a short page, or when Archidekt gives no ``next`` link."""

from __future__ import annotations

from typing import Any

from mtg_gateway.archidekt import MAX_LIST_PAGES, ArchidektClient, Pacer


def _row(i: int) -> dict[str, Any]:
    return {
        "id": i,
        "name": f"Deck {i}",
        "owner": {"id": 7, "username": "alice"},
        "updatedAt": f"2026-10-{1 + i % 28:02d}T00:00:00Z",
        "deckFormat": 3,
    }


class _Paged(ArchidektClient):
    """A client whose listing answers ``pages`` in order and records the page parameters sent."""

    def __init__(self, pages: list[list[dict[str, Any]]], *, next_link: bool = True) -> None:
        super().__init__("https://archidekt.com/api", "test", Pacer(0.0))
        self.pages = pages
        self.next_link = next_link
        self.sent: list[dict[str, Any]] = []

    async def _request(self, method: str, path: str, **kw: Any) -> Any:  # type: ignore[override]
        assert method == "GET" and path == "/decks/v3/"
        params = kw["params"]
        self.sent.append(params)
        number = int(params.get("page", 1))
        results = self.pages[number - 1] if number <= len(self.pages) else []
        more = self.next_link and number < len(self.pages)
        link = "https://x/?page=2" if more else None
        return {"count": sum(map(len, self.pages)), "next": link, "results": results}


async def test_a_second_page_is_read_and_merged() -> None:
    client = _Paged([[_row(i) for i in range(50)], [_row(i) for i in range(50, 52)]])
    decks = await client.list_decks("tok", "alice", "7")
    assert len(decks) == 52 and {d["id"] for d in decks} == {str(i) for i in range(52)}
    assert [p.get("page") for p in client.sent] == [None, 2]  # page 1 sends no page parameter
    assert all(p["ownerId"] == "7" and p["pageSize"] == 50 for p in client.sent)


async def test_a_short_first_page_or_no_next_link_ends_the_listing() -> None:
    short = _Paged([[_row(i) for i in range(3)], [_row(99)]])
    assert len(await short.list_decks("tok", "alice", "7")) == 3 and len(short.sent) == 1
    no_next = _Paged([[_row(i) for i in range(50)], [_row(99)]], next_link=False)
    assert len(await no_next.list_decks("tok", "alice", "7")) == 50 and len(no_next.sent) == 1


async def test_the_page_count_is_capped_and_duplicates_dropped() -> None:
    # A listing that moves while it is read can repeat an entry on the next page: counted once.
    pages = [[_row(i) for i in range(50 * p, 50 * p + 50)] for p in range(MAX_LIST_PAGES + 2)]
    pages[1][0] = _row(0)
    client = _Paged(pages)
    decks = await client.list_decks("tok", "alice", "7")
    assert len(client.sent) == MAX_LIST_PAGES
    assert len(decks) == 50 * MAX_LIST_PAGES - 1 and len({d["id"] for d in decks}) == len(decks)


# -- whether the list can be taken as every deck of the account (History's "No longer on
# Archidekt" relies on it): anything it cannot be sure of makes it incomplete --------------------
async def test_a_whole_listing_is_complete() -> None:
    one = await _Paged([[_row(i) for i in range(3)]]).list_decks("tok", "alice", "7")
    two = await _Paged([[_row(i) for i in range(50)], [_row(50)]]).list_decks("tok", "alice", "7")
    assert one.complete is True and two.complete is True


async def test_a_listing_cut_off_at_the_page_cap_is_incomplete() -> None:
    """Bug (found in this release's checks): the service judged a cut-off listing by its length
    (200), so one that lost a row to a repeat (199 left) was taken as whole."""
    pages = [[_row(i) for i in range(50 * p, 50 * p + 50)] for p in range(MAX_LIST_PAGES + 1)]
    pages[1][0] = _row(0)
    decks = await _Paged(pages).list_decks("tok", "alice", "7")
    assert len(decks) == 50 * MAX_LIST_PAGES - 1 and decks.complete is False
    full = [[_row(i) for i in range(50 * p, 50 * p + 50)] for p in range(MAX_LIST_PAGES)]
    exact = await _Paged(full).list_decks("tok", "alice", "7")  # no next page after the last
    assert exact.complete is True


UNREADABLE_OWNERS = [None, {}, {"id": None, "username": None}, {"name": "alice"}, 7, ["alice"], ""]


async def test_an_entry_whose_owner_cannot_be_read_makes_the_list_incomplete() -> None:
    for owner in UNREADABLE_OWNERS:
        odd = {**_row(2), "owner": owner}
        decks = await _Paged([[_row(1), odd]]).list_decks("tok", "alice", "7")
        assert [d["id"] for d in decks] == ["1"], owner  # still never shown as the member's
        assert decks.complete is False, owner
    renamed = {k: v for k, v in _row(2).items() if k != "owner"} | {"creator": {"id": 7}}
    decks = await _Paged([[_row(1), renamed]]).list_decks("tok", "alice", "7")
    assert decks.complete is False


async def test_a_listing_that_returns_other_peoples_decks_is_incomplete() -> None:
    # Archidekt ignoring the owner filter (as it does the old owner= parameter) would return
    # everyone's decks: the member's own may then be anywhere, or past the pages read.
    other = {**_row(2), "owner": {"id": 8, "username": "amy"}}
    decks = await _Paged([[_row(1), other]]).list_decks("tok", "alice", "7")
    assert [d["id"] for d in decks] == ["1"] and decks.complete is False


async def test_a_listing_whose_count_moves_between_pages_is_incomplete() -> None:
    # A deck deleted (or made) on Archidekt while the pages are read shifts the rows across the
    # page boundary, so one deck can be skipped with no sign but the changed count.
    client = _Paged([[_row(i) for i in range(50)], [_row(50)]])
    counts = iter([52, 51])
    real = client._request

    async def moving(method: str, path: str, **kw: Any) -> Any:
        body = await real(method, path, **kw)
        return {**body, "count": next(counts)}

    client._request = moving  # type: ignore[method-assign]
    assert (await client.list_decks("tok", "alice", "7")).complete is False


async def test_a_listing_without_a_count_is_judged_by_its_rows() -> None:
    client = _Paged([[_row(i) for i in range(50)], [_row(50)]])
    real = client._request

    async def no_count(method: str, path: str, **kw: Any) -> Any:
        body = await real(method, path, **kw)
        return {k: v for k, v in body.items() if k != "count"}

    client._request = no_count  # type: ignore[method-assign]
    assert (await client.list_decks("tok", "alice", "7")).complete is True


async def test_a_deck_seen_on_two_pages_makes_the_list_incomplete() -> None:
    """L3: the listing shifted between pages (a deck came back again), so another deck may have
    been skipped, even when the count did not move."""
    pages = [[_row(i) for i in range(50)], [_row(49), _row(50)]]
    client = _Paged(pages)
    real = client._request

    async def steady(method: str, path: str, **kw: Any) -> Any:
        return {**(await real(method, path, **kw)), "count": 51}

    client._request = steady  # type: ignore[method-assign]
    decks = await client.list_decks("tok", "alice", "7")
    assert len(decks) == 51 and decks.complete is False


async def test_fewer_decks_than_archidekts_count_make_the_list_incomplete() -> None:
    """L3: Archidekt's count says more decks exist than came back."""
    client = _Paged([[_row(i) for i in range(3)]])
    real = client._request

    async def more(method: str, path: str, **kw: Any) -> Any:
        return {**(await real(method, path, **kw)), "count": 4}

    client._request = more  # type: ignore[method-assign]
    decks = await client.list_decks("tok", "alice", "7")
    assert len(decks) == 3 and decks.complete is False
