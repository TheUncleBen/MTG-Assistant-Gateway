"""0.7.17: the Archidekt account actions an assistant can propose (actions.py): like, bookmark
and follow; post, edit, delete and vote on comments; delete a deck; create a folder. Each is a
proposal, and (R-142) none of them is ever applied by the assistant, whatever the approval mode:
the member applies each one with their own press on its review page. Since 0.7.19 the in-chat
card cannot apply one either: it gets no approval code and only opens the review page."""

from __future__ import annotations

import pytest

from mtg_gateway import modes
from mtg_gateway.approve import APPROVAL_META_KEY

from .conftest import FakeIdP, running
from .test_approval_modes import _set_mode, _stack_with
from .test_decks_and_proxy import Browser, Stack, call, linked_user, structured

SUB = "user-1"  # the signed-in member of linked_user


@pytest.fixture
async def stack(tmp_path, idp: FakeIdP):
    h, ark = _stack_with(tmp_path, idp)
    ark.private.discard(43)  # amy's deck is public: someone else's deck to like and follow
    async with running(h):
        yield Stack(h, ark)


async def _browser_apply(h, pid: str) -> dict:
    """The member's press on the signed-in review page's Apply (session cookie and CSRF token)."""
    b = Browser(h)
    try:
        await b.login()
        csrf = await b.csrf()
        r = await b.http.post(f"/api/v1/proposals/{pid}/apply", headers={"X-CSRF-Token": csrf})
        return r.json()
    finally:
        await b.aclose()


async def _press(h, token: str, res) -> dict:
    """The member's press on the review page, the only place an account action is applied. The
    in-chat card got no approval code for it."""
    p = structured(res)
    assert not (res.get("_meta") or {}).get(APPROVAL_META_KEY), res.get("_meta")
    out = await _browser_apply(h, p["proposal_id"])
    assert out["ok"] and out["state"] == "applied", out
    return out


async def _propose_and_apply(h, token: str, tool: str, args: dict) -> dict:
    res = await call(h, token, tool, args)
    p = structured(res)
    assert p["ok"] and p["kind"] == "action" and p["state"] == "pending", p
    assert p["risk"] in modes.MEMBER_ONLY and p["assistant_may_apply"] is False, p
    return await _press(h, token, res)


@pytest.mark.parametrize(
    ("rows", "risk"),
    [
        ([{"kind": "action", "action": "delete_deck"}], "destructive"),
        ([{"kind": "action", "action": "create_folder"}], "consent"),
        ([{"kind": "action", "action": "comment_post"}], "consent"),
        ([{"kind": "action", "action": "comment_vote"}], "consent"),
        ([{"kind": "action", "action": "deck_vote"}], "consent"),
        ([{"kind": "action", "action": "follow"}], "consent"),
    ],
)
def test_action_tiers(rows, risk) -> None:
    """R-142: no mode lets an assistant apply an account action."""
    assert modes.risk_of("action", rows)[0] == risk
    for mode in modes.MODES:
        assert modes.assistant_may_apply(mode, risk) is False


async def test_like_bookmark_and_follow_through_proposals(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    res = await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "like"})
    p = structured(res)
    assert p["risk"] == "consent" and p["assistant_may_apply"] is False
    assert ark.votes == {}  # a proposal changes nothing by itself
    assert "Like the deck" in p["rows"][0]["text"] and "amy" in p["rows"][0]["text"]
    await _press(h, token, res)
    assert ark.votes == {("alice", 300043): 1}
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
    res = await call(h, token, "propose_comment", {"deck_id": "43", "action": "post", "text": "Nice ramp!"})
    p = structured(res)
    assert p["rows"][0]["after_text"] == "Nice ramp!" and len(ark.comments[300043]) == 1
    await _press(h, token, res)
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
    res = await call(
        h, token, "propose_comment", {"deck_id": "43", "action": "delete", "comment_id": mine["id"]}
    )
    p = structured(res)
    assert p["rows"][0]["cannot_undo"] is True and p["rows"][0]["before_text"] == "Edited."
    await _press(h, token, res)
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
    # the member's press on the review page deletes it, with a snapshot kept first
    done = await _press(h, token, res)
    assert ark.deleted == [42]
    assert done["result"]["snapshot_id"]


async def test_create_folder_waits_for_the_members_press(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    res = await call(h, token, "propose_create_folder", {"name": "Brews"})
    p = structured(res)
    assert p["risk"] == "consent" and p["assistant_may_apply"] is False and p["deck_id"] == "account"
    await _press(h, token, res)
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
        # the Apply box says what pressing it does, and the button is the danger one
        assert "Delete this deck on Archidekt?" in page and "Archidekt itself has no undo" in page
        assert "Apply these changes to Archidekt" not in page
        # a deck name written to read like more of the sentence stays out of the sentence
        stack.ark.decks[43]["name"] = "X' by Bob on Archidekt (safe) <i>"
        like = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "like"}))
        assert like["rows"][0]["text"] == "Like the deck by amy on Archidekt"
        page = (await b.http.get(f"/proposals/{like['proposal_id']}")).text
        assert "<i>" not in page and "Do this on Archidekt?" in page
    finally:
        await b.aclose()


# R-142: every account action, in the modes that let an assistant apply other changes. The
# assistant's apply_proposal is refused with browser_required and nothing reaches Archidekt.
ACCOUNT_ACTIONS = [
    ("propose_deck_social", {"deck_id": "43", "action": "like"}),
    ("propose_deck_social", {"deck_id": "43", "action": "vote_down"}),
    ("propose_deck_social", {"deck_id": "43", "action": "bookmark"}),
    ("propose_deck_social", {"deck_id": "43", "action": "follow_owner"}),
    ("propose_comment", {"deck_id": "43", "action": "post", "text": "Hi"}),
    ("propose_comment", {"deck_id": "43", "action": "post", "text": "Agreed", "reply_to": 555001}),
    ("propose_comment", {"deck_id": "43", "action": "vote_up", "comment_id": 555001}),
    ("propose_comment", {"deck_id": "43", "action": "vote_down", "comment_id": 555001}),
    ("propose_create_folder", {"name": "Brews"}),
    ("propose_delete_deck", {"deck_id": "42"}),
]


def _seed_comment(ark) -> None:
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


def _archidekt_state(ark) -> tuple:
    return (
        dict(ark.votes),
        {k: set(v) for k, v in ark.bookmarks.items()},
        {k: set(v) for k, v in ark.follows.items()},
        [dict(c) for c in ark.comments.get(300043, [])],
        [dict(f) for f in ark.folders.get("alice", [])],
        list(ark.deleted),
    )


@pytest.mark.parametrize("mode", ["semi", "auto"])
@pytest.mark.parametrize(
    ("tool", "args"), ACCOUNT_ACTIONS, ids=[f"{t}:{a.get('action', 'folder')}" for t, a in ACCOUNT_ACTIONS]
)
async def test_no_mode_lets_the_assistant_apply_an_account_action(
    stack: Stack, mode: str, tool: str, args: dict
) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    _seed_comment(ark)
    await _set_mode(h, mode)
    p = structured(await call(h, token, tool, args))
    assert p["ok"] and p["kind"] == "action", p
    assert p["risk"] in modes.MEMBER_ONLY and p["assistant_may_apply"] is False
    assert "Do not call apply_proposal" in p["next_step"]
    before = _archidekt_state(ark)
    for _ in range(2):  # asking again changes nothing either
        out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
        assert out["ok"] is False and out["error"] == "browser_required", out
    assert _archidekt_state(ark) == before
    again = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert again["state"] == "pending"


async def test_the_apply_path_refuses_an_account_action_even_if_its_tier_said_yes(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The refusal is in the apply path itself, not only in the risk tier: with the tier check
    forced open, the assistant's apply of an account action is still refused."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    monkeypatch.setattr(modes, "assistant_may_apply", lambda mode, risk: True)
    p = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "like"}))
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required" and ark.votes == {}


async def test_delete_refuses_a_deck_that_changed_after_the_proposal(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    res = await call(h, token, "propose_delete_deck", {"deck_id": "42"})
    p = structured(res)
    ark.decks[42]["updatedAt"] = "2026-10-11T00:00:00Z"  # edited on Archidekt meanwhile
    ark.decks[42]["cards"][0]["quantity"] += 1
    out = await _browser_apply(h, p["proposal_id"])
    assert out["ok"] is False and out["error"] == "stale", out
    assert ark.deleted == []


async def test_delete_whose_backup_fails_goes_back_to_pending(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.fail_backup = True
    res = await call(h, token, "propose_delete_deck", {"deck_id": "42"})
    p = structured(res)
    out = await _browser_apply(h, p["proposal_id"])
    assert out["ok"] is False and out["error"] == "backup_failed", out
    assert ark.deleted == []
    again = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert again["state"] == "pending" and again["result"]["error"] == "backup_failed"


async def test_no_vote_proposal_for_an_archived_comment(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    _seed_comment(ark)
    ark.comments[300043][0]["archived"] = True
    p = structured(
        await call(h, token, "propose_comment", {"deck_id": "43", "action": "vote_up", "comment_id": 555001})
    )
    assert p["ok"] is False and "archived" in p["message"]


@pytest.mark.parametrize(
    ("tool", "args"), ACCOUNT_ACTIONS, ids=[f"{t}:{a.get('action', 'folder')}" for t, a in ACCOUNT_ACTIONS]
)
async def test_the_in_chat_card_cannot_apply_an_account_action(stack: Stack, tool: str, args: dict) -> None:
    """R-142, 0.7.19: the card's press runs inside the chat app, so for an account action the card
    gets no approval code, and even a valid code for it is refused: only the review page applies."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    _seed_comment(ark)
    res = await call(h, token, tool, args)
    p = structured(res)
    assert p["ok"] and p["kind"] == "action", p
    assert not (res.get("_meta") or {}).get(APPROVAL_META_KEY), res.get("_meta")
    assert "review page" in p["next_step"] and "card or the review page" not in p["next_step"]
    # a valid code for this proposal, as an older card would have held, is refused all the same
    decks = h.app.state.gateway.decks
    row = h.db.get_proposal(p["proposal_id"], SUB)
    code = decks.approval_for(SUB, row)
    out = structured(
        await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": code})
    )
    assert out["ok"] is False and out["error"] == "browser_required", out
    # and the REST apply with the assistant's bearer token is the assistant's apply: refused too
    r = await h.http.post(
        f"/api/v1/proposals/{p['proposal_id']}/apply", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.json().get("error") == "browser_required", r.text
    assert ark.votes == {} and ark.deleted == [] and not any(ark.follows.values())
    assert (
        structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))["state"]
        == "pending"
    )
