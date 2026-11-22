"""Up to 500 scan sessions are kept, and each one can be read by its name: the list says when it
shows only the newest, and get_scan_session finds the older ones too."""

from __future__ import annotations

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, structured


def _seed(stack: Stack, n: int) -> None:
    for i in range(n):
        stack.gw.scan.store.save(
            {
                "id": f"scan_{i:04d}",
                "owner_sub": "user-1",
                "name": f"Box {i}",
                "status": "draft",
                "source": "text",
                "items": [],
                "created_at": 1_000 + i,
                "updated_at": 1_000 + i,
            }
        )


async def test_the_oldest_of_51_sessions_is_found_by_name(stack: Stack) -> None:
    token = await linked_user(stack)
    _seed(stack, 51)
    got = structured(await call(stack.h, token, "get_scan_session", {"session": "box 0"}))
    assert got["ok"] is True and got["id"] == "scan_0000"


async def test_the_list_says_when_it_shows_only_the_newest(stack: Stack) -> None:
    token = await linked_user(stack)
    _seed(stack, 50)
    listed = structured(await call(stack.h, token, "list_scan_sessions"))
    assert len(listed["sessions"]) == 50 and "has_more" not in listed
    _seed(stack, 51)
    listed = structured(await call(stack.h, token, "list_scan_sessions"))
    assert len(listed["sessions"]) == 50 and listed["has_more"] is True
    assert listed["sessions"][0]["name"] == "Box 50" and "get_scan_session" in listed["note"]
