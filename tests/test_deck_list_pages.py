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
