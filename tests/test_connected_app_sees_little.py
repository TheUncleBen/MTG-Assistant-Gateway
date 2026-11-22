"""A connected app reads no more about the member than the consent page and whoami say.

Both tell the member that nothing but their display name is shared with an assistant. The JSON
routes gave the same token the email address, every group, the admin flag and the whole activity
trail, other apps' events and browser sign-ins included."""

from __future__ import annotations

from .conftest import Stack
from .test_browse_collection import linked
from .test_decks_and_proxy import call, linked_user


async def test_a_bearer_caller_gets_the_name_only(stack: Stack) -> None:
    token = await linked_user(stack)
    me = (await stack.h.http.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})).json()
    assert me["ok"] is True and me["via"] == "api" and me["account"]["linked"] is True
    assert not {"email", "groups", "is_admin", "sub", "preferred_username"} & set(me), me
    assert "@" not in str(me.get("name") or "")


async def test_the_member_in_the_browser_still_sees_their_own_details(stack: Stack) -> None:
    b = await linked(stack)
    me = (await b.http.get("/api/v1/me")).json()
    assert me["via"] == "browser" and {"email", "groups", "is_admin", "sub"} <= set(me)
    await b.aclose()


async def test_a_bearer_caller_sees_only_its_own_events(stack: Stack) -> None:
    token = await linked_user(stack)
    await call(stack.h, token, "whoami")
    auth = {"Authorization": f"Bearer {token}"}
    events = (await stack.h.http.get("/api/v1/activity", headers=auth)).json()["events"]
    clients = {e.get("client_id") for e in events}
    assert "__browser__" not in clients and len(clients) <= 1, clients
    assert not any(e["event"] in ("archidekt_linked", "browser_login") for e in events)
    # the browser session still lists everything, the link made in the browser included
    b = await linked(stack)
    mine = (await b.http.get("/api/v1/activity")).json()["events"]
    assert any(e.get("client_id") == "__browser__" for e in mine)
    await b.aclose()
