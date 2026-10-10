"""0.7.17: the Archidekt account actions an assistant can propose (actions.py): like, bookmark
and follow; post, edit, delete and vote on comments; delete a deck; create a folder. Each is a
proposal the member approves like any other write, and deleting a deck is never applied by the
assistant, whatever the approval mode."""

from __future__ import annotations

import pytest

from mtg_gateway import modes
from mtg_gateway.approve import APPROVAL_META_KEY

from .conftest import FakeIdP, running
from .test_approval_modes import _set_mode, _stack_with
from .test_decks_and_proxy import Stack, call, linked_user, structured


@pytest.fixture
async def stack(tmp_path, idp: FakeIdP):
    h, ark = _stack_with(tmp_path, idp)
    ark.private.discard(43)  # amy's deck is public: someone else's deck to like and follow
    async with running(h):
        yield Stack(h, ark)


async def _propose_and_apply(h, token: str, tool: str, args: dict) -> dict:
    p = structured(await call(h, token, tool, args))
    assert p["ok"] and p["kind"] == "action" and p["state"] == "pending", p
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] and out["state"] == "applied", out
    return out


@pytest.mark.parametrize(
    ("rows", "risk"),
    [
        ([{"kind": "action", "action": "delete_deck"}], "destructive"),
        ([{"kind": "action", "action": "create_folder"}], "low"),
        ([{"kind": "action", "action": "comment_post"}], "high"),
        ([{"kind": "action", "action": "deck_vote"}], "high"),
    ],
)
def test_action_tiers(rows, risk) -> None:
    assert modes.risk_of("action", rows)[0] == risk
    assert modes.assistant_may_apply("auto", risk) is (risk != "destructive")
    assert modes.assistant_may_apply("semi", risk) is (risk == "low")


async def test_like_bookmark_and_follow_through_proposals(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    p = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "like"}))
    assert p["risk"] == "high" and p["assistant_may_apply"] is True
    assert ark.votes == {}  # a proposal changes nothing by itself
    assert "Like the deck" in p["rows"][0]["text"] and "amy" in p["rows"][0]["text"]
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["state"] == "applied" and ark.votes == {("alice", 300043): 1}
    await _propose_and_apply(h, token, "propose_deck_social", {"deck_id": "43", "action": "bookmark"})
    assert 43 in ark.bookmarks["alice"]
    await _propose_and_apply(h, token, "propose_deck_social", {"deck_id": "43", "action": "follow_owner"})
    assert 78 in ark.follows["alice"]
    # your own deck's owner is you
    p = structured(await call(h, token, "propose_deck_social", {"deck_id": "42", "action": "follow_owner"}))
    assert p["ok"] is False and "yourself" in p["message"]
    p = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "hug"}))
    assert p["ok"] is False and p["error"] == "invalid"


async def test_comments_post_edit_vote_delete(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    ark.comments[300043] = [
        {
            "id": 555001,
            "text": "Try Cultivate.",
            "owner": {"id": 78, "username": "amy", "avatar": None, "frame": None},
            "parent": 300043,
            "originalPost": 300043,
            "createdAt": "2026-10-10T12:00:00Z",
            "editedAt": None,
            "points": 0,
            "userInput": 0,
            "childrenCount": 0,
            "children": {"count": 0, "results": []},
            "archived": False,
            "locked": False,
            "type": 4,
        }
    ]
    p = structured(
        await call(h, token, "propose_comment", {"deck_id": "43", "action": "post", "text": "Nice ramp!"})
    )
    assert p["rows"][0]["after_text"] == "Nice ramp!" and len(ark.comments[300043]) == 1
    await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    read = structured(await call(h, token, "get_deck_comments", {"deck_id": "43"}))
    mine = next(c for c in read["comments"] if c["text"] == "Nice ramp!")
    assert mine["owner"]["id"] == read["own_user_id"]
    await _propose_and_apply(
        h,
        token,
        "propose_comment",
        {"deck_id": "43", "action": "edit", "comment_id": mine["id"], "text": "Edited."},
    )
    assert any(c["text"] == "Edited." for c in ark.comments[300043])
    await _propose_and_apply(
        h, token, "propose_comment", {"deck_id": "43", "action": "vote_up", "comment_id": 555001}
    )
    assert ark.votes[("alice", 555001)] == 1
    # not your comment: no edit; your own: no vote
    p = structured(
        await call(
            h,
            token,
            "propose_comment",
            {"deck_id": "43", "action": "edit", "comment_id": 555001, "text": "x"},
        )
    )
    assert p["ok"] is False and p["error"] == "forbidden"
    p = structured(
        await call(
            h, token, "propose_comment", {"deck_id": "43", "action": "vote_up", "comment_id": mine["id"]}
        )
    )
    assert p["ok"] is False
    p = structured(
        await call(
            h, token, "propose_comment", {"deck_id": "43", "action": "delete", "comment_id": mine["id"]}
        )
    )
    assert p["rows"][0]["cannot_undo"] is True and p["rows"][0]["before_text"] == "Edited."
    await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    assert all(c["id"] != mine["id"] for c in ark.comments[300043])


async def test_delete_deck_waits_for_the_members_press_even_in_auto(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    res = await call(h, token, "propose_delete_deck", {"deck_id": "42"})
    p = structured(res)
    assert p["risk"] == "destructive" and p["assistant_may_apply"] is False
    assert p["rows"][0]["destructive"] is True and "Archidekt itself has no undo" in p["rows"][0]["note"]
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required" and ark.deleted == []
    # someone else's deck cannot be proposed for deletion at all
    other = structured(await call(h, token, "propose_delete_deck", {"deck_id": "43"}))
    assert other["ok"] is False
    # the member's press on the card deletes it, with a snapshot kept first
    code = res["_meta"][APPROVAL_META_KEY]
    done = structured(
        await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": code})
    )
    assert done["ok"] and done["state"] == "applied" and ark.deleted == [42]
    assert done["result"]["snapshot_id"]


async def test_create_folder_is_low_risk(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    p = structured(await call(h, token, "propose_create_folder", {"name": "Brews"}))
    assert p["risk"] == "low" and p["assistant_may_apply"] is True and p["deck_id"] == "account"
    await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    assert any(f["name"] == "Brews" for f in ark.folders["alice"])
    p = structured(await call(h, token, "propose_create_folder", {"name": "Inner", "inside": "Brews"}))
    assert "in 'Brews'" in p["rows"][0]["text"]
    p = structured(await call(h, token, "propose_create_folder", {"name": "X", "inside": "Nope"}))
    assert p["ok"] is False and "Brews" in p["message"]
    p = structured(await call(h, token, "propose_create_folder", {"name": "Brews"}))
    assert p["ok"] is False and "already" in p["message"]


async def test_review_page_shows_the_action(stack: Stack) -> None:
    from .test_decks_and_proxy import Browser

    h = stack.h
    token = await linked_user(stack)
    p = structured(
        await call(
            h, token, "propose_comment", {"deck_id": "43", "action": "post", "text": "<b>hi</b> there"}
        )
    )
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
        assert (
            "Post a public comment" in page
            and "&lt;b&gt;hi&lt;/b&gt; there" in page
            and "<b>hi</b>" not in page
        )
        d = structured(await call(h, token, "propose_delete_deck", {"deck_id": "42"}))
        page = (await b.http.get(f"/proposals/{d['proposal_id']}")).text
        assert "Can&#x27;t be undone on Archidekt" in page or "Can't be undone on Archidekt" in page
        assert "destructive" in page
    finally:
        await b.aclose()
