"""0.7.17: the Archidekt account actions an assistant can propose (actions.py): like, bookmark
and follow; post, edit, delete and vote on comments; delete a deck; create a folder. Each is a
proposal, and (R-142) none of them is ever applied by the assistant, whatever the approval mode:
the member applies each one with their own press on its review page. Since 0.7.19 the in-chat
card cannot apply one either: it gets no approval code and only opens the review page."""

from __future__ import annotations

import pytest

from mtg_gateway import modes
from mtg_gateway.approve import APPROVAL_META_KEY, approval_code
from mtg_gateway.archidekt import rich_doc
from mtg_gateway.decks import DeckError

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
        r = await b.http.post(f"/proposals/{pid}", data={"csrf": csrf, "action": "apply"})
        assert r.status_code == 303, r.text
        err = r.headers["location"].partition("?err=")[2]
        if err:  # the review page shows the refusal by its code
            return {"ok": False, "error": err}
        return (await b.http.get(f"/api/v1/proposals/{pid}")).json()
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
    assert any(c["text"] == rich_doc("Edited.") for c in ark.comments[300043])  # as a Quill document
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
    # 0.7.24: the gateway refuses to make a code for it at all (approval_for refuses at the source)
    decks = h.app.state.gateway.decks
    row = h.db.get_proposal(p["proposal_id"], SUB)
    with pytest.raises(DeckError) as no_code:
        decks.approval_for(SUB, row)
    assert no_code.value.kind == "browser_required"
    # and a correctly signed code for this proposal, as an older card would have held, is refused
    # all the same, with the row's real tier (destructive for a deck deletion)
    code = approval_code(
        decks.settings.session_secret,
        proposal_id=row["id"],
        sub=SUB,
        client_id=row.get("created_by_client") or "",
        created_at=int(row["created_at"]),
    )
    out = structured(
        await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": code})
    )
    assert out["ok"] is False and out["error"] == "browser_required", out
    assert out["risk"] == ("destructive" if tool == "propose_delete_deck" else "consent"), out
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


# -- 0.7.20: deck collaborators (Archidekt's "editors"). Adding or removing one changes who can
# edit the deck, so it is an account action: only the member's press on the review page applies it.


def _amy_row() -> dict:
    return {
        "id": 901,
        "user": {"id": 78, "username": "amy", "avatar": None},
        "createdBy": {"id": 77, "username": "alice", "avatar": None},
        "createdAt": "2026-10-10T00:00:00Z",
    }


async def test_collaborators_are_listed_added_and_removed_on_the_members_press(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtg_gateway import social

    monkeypatch.setattr(social, "FRESH_GAP", 0.0)  # the member follows amy straight after the miss
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    listed = structured(await call(h, token, "get_deck_collaborators", {"deck_id": "42"}))
    assert listed == {"ok": True, "collaborators": []}
    # only people the member follows can be added, as on Archidekt's own settings page
    p = structured(
        await call(h, token, "propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"})
    )
    assert p["ok"] is False and p["error"] == "not_found" and "follow" in p["message"], p
    ark.follows["alice"] = {78}
    res = await call(h, token, "propose_collaborator", {"deck_id": "42", "action": "add", "username": "@AMY"})
    p = structured(res)
    assert p["risk"] == "consent" and p["assistant_may_apply"] is False, p
    assert p["rows"][0]["text"] == "Let amy edit your deck on Archidekt as a collaborator"
    assert "except its main settings" in p["rows"][0]["note"]
    assert ark.editors.get(42, []) == []  # nothing yet
    done = await _press(h, token, res)
    assert done["result"]["verified"] is True
    assert [r["user"]["username"] for r in ark.editors[42]] == ["amy"]
    listed = structured(await call(h, token, "get_deck_collaborators", {"deck_id": "42"}))
    assert listed["collaborators"][0]["username"] == "amy"
    assert listed["collaborators"][0]["added_by"] == "alice"
    again = structured(
        await call(h, token, "propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"})
    )
    assert again["ok"] is False and "already" in again["message"]
    res = await call(
        h, token, "propose_collaborator", {"deck_id": "42", "action": "remove", "username": "amy"}
    )
    assert structured(res)["rows"][0]["text"] == "Stop amy editing your deck on Archidekt"
    await _press(h, token, res)
    assert ark.editors[42] == []
    gone = structured(
        await call(h, token, "propose_collaborator", {"deck_id": "42", "action": "remove", "username": "amy"})
    )
    assert gone["ok"] is False and gone["error"] == "not_found"
    # someone else's deck has no collaborators the member can change
    other = structured(
        await call(h, token, "propose_collaborator", {"deck_id": "43", "action": "add", "username": "amy"})
    )
    assert other["ok"] is False


@pytest.mark.parametrize("mode", ["manual", "semi", "auto"])
@pytest.mark.parametrize("action", ["add", "remove"])
async def test_no_mode_lets_the_assistant_change_collaborators(stack: Stack, mode: str, action: str) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.follows["alice"] = {78}
    if action == "remove":
        ark.editors[42] = [_amy_row()]
    await _set_mode(h, mode)
    res = await call(h, token, "propose_collaborator", {"deck_id": "42", "action": action, "username": "amy"})
    p = structured(res)
    assert p["ok"] and p["kind"] == "action" and p["risk"] in modes.MEMBER_ONLY, p
    assert not (res.get("_meta") or {}).get(APPROVAL_META_KEY)  # the in-chat card cannot apply it
    before = [dict(r) for r in ark.editors.get(42, [])]
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required", out
    assert [dict(r) for r in ark.editors.get(42, [])] == before


async def test_settings_page_collaborators_take_one_press_and_a_confirmation(stack: Stack) -> None:
    """D9 (0.7.24): on the member's own settings page, adding or removing a collaborator is one
    press, then a confirmation page, then it is sent to Archidekt, like the page's delete and new
    folder forms. No proposal is made; the session and the page's CSRF token are required."""
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    ark.follows["alice"] = {78}
    ark.editors[42] = [_amy_row()]
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get("/decks/42/settings")).text
        assert "id='collaborators'" in page and "<span class='name'>amy</span>" in page
        assert "added by alice" in page and "aria-label='Remove amy'" in page
        assert "press Apply there" not in page
        # Q6b: one line on the rule; the people followed are offered (the site's own combobox, no
        # datalist), less current collaborators: amy, the only one followed, is one already
        assert "Only people you follow on Archidekt can be added" in page
        assert "<datalist" not in page and "data-suggest='list'" not in page
        assert "Everyone you follow on Archidekt is a collaborator already." in page
        # each form names its section, so the answer (a refusal, or the page after the confirmation)
        # opens there
        assert "action='/decks/42/collaborators#collaborators'" in page
        csrf = await b.csrf()
        # the first press only asks: nothing is sent and no proposal is stored
        r = await b.http.post(
            "/decks/42/collaborators", data={"csrf": csrf, "action": "remove", "username": "AMY"}
        )
        assert r.status_code == 200 and "Stop amy editing" in r.text and "name='confirmed'" in r.text
        assert "/decks/42/settings#collaborators" in r.text  # Cancel goes back
        assert len(ark.editors[42]) == 1 and h.db.list_proposals(SUB) == []
        # the confirmation removes them at once
        r = await b.http.post(
            "/decks/42/collaborators",
            data={"csrf": csrf, "action": "remove", "username": "amy", "confirmed": "1"},
        )
        assert r.status_code == 303 and "ok=collaborator_removed" in r.headers["location"], r.text
        assert ark.editors[42] == [] and h.db.list_proposals(SUB) == []
        shown = (await b.http.get(r.headers["location"])).text
        assert "Collaborator removed on Archidekt." in shown
        # the notice sits in the section, next to the form, not at the top of the page
        assert shown.index("Collaborator removed on Archidekt.") > shown.index("id='collaborators'")
        assert "No collaborators yet." in shown
        assert "data-suggest='list' data-options='[&quot;amy&quot;]'" in shown
        assert "data-suggest-head='People you follow'" in shown and "<datalist" not in shown
        # adding: the same two steps, someone the member follows
        r = await b.http.post(
            "/decks/42/collaborators", data={"csrf": csrf, "action": "add", "username": "amy"}
        )
        assert r.status_code == 200 and "Let amy edit" in r.text and ark.editors[42] == []
        r = await b.http.post(
            "/decks/42/collaborators",
            data={"csrf": csrf, "action": "add", "username": "amy", "confirmed": "1"},
        )
        assert r.status_code == 303 and "ok=collaborator_added" in r.headers["location"], r.text
        assert [e["user"]["id"] for e in ark.editors[42]] == [78]
        # refusals come before the confirmation and change nothing
        bad = await b.http.post(
            "/decks/42/collaborators", data={"csrf": csrf, "action": "add", "username": "nobody"}
        )
        assert bad.status_code == 400 and "nobody isn't someone you follow on Archidekt." in bad.text
        # Q6b: one message, under the box, with the person's Archidekt profile to follow them there;
        # not repeated at the top of the page
        assert bad.text.count("someone you follow on Archidekt") == 1
        assert "not among the people you follow" not in bad.text
        assert "<p class='notice error'>" not in bad.text
        msg = bad.text.index("id='collaborators-msg'")
        assert bad.text.index("id='collabname'") < msg < bad.text.index("id='collab-help'")
        assert (
            "<a class='btn' href='https://archidekt.com/u/nobody' target='_blank' "
            "rel='noreferrer noopener'>" in bad.text
        )
        assert "Open nobody on Archidekt</a>" in bad.text
        # the box keeps the name, is marked invalid and is described by the message
        assert (
            "value='nobody' aria-describedby='collab-help collaborators-msg' aria-invalid='true'" in bad.text
        )
        bad = await b.http.post(
            "/decks/42/collaborators",
            data={"csrf": csrf, "action": "add", "username": "nobody", "confirmed": "1"},
        )
        assert bad.status_code == 400 and [e["user"]["id"] for e in ark.editors[42]] == [78]
        # not the member's own deck: refused, on either step
        for extra in ({}, {"confirmed": "1"}):
            other = await b.http.post(
                "/decks/43/collaborators", data={"csrf": csrf, "action": "add", "username": "amy", **extra}
            )
            assert other.status_code in (400, 403, 404) and 43 not in ark.editors, other.text
        # no CSRF token, or a wrong one: nothing happens, confirmed or not
        for form in (
            {"action": "remove", "username": "amy", "confirmed": "1"},
            {"csrf": "nope", "action": "remove", "username": "amy", "confirmed": "1"},
        ):
            assert (await b.http.post("/decks/42/collaborators", data=form)).status_code == 403
        assert [e["user"]["id"] for e in ark.editors[42]] == [78]
    finally:
        await b.aclose()


async def test_an_assistant_token_cannot_use_the_settings_page_collaborator_form(stack: Stack) -> None:
    """R-142: the direct form is the member's own click. An assistant's bearer token is not a
    browser session, so the route sends it to sign in and nothing changes on Archidekt."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.follows["alice"] = {78}
    r = await h.http.post(
        "/decks/42/collaborators",
        data={"action": "add", "username": "amy", "confirmed": "1"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code in (302, 303, 401, 403) and not ark.editors.get(42), r.text
    p = structured(
        await call(h, token, "propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"})
    )
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required" and not ark.editors.get(42)


async def test_the_json_api_does_not_apply_an_account_action(stack: Stack) -> None:
    """R-142: only the review page's own form applies an account action, not the JSON API, even
    with the member's signed-in session and CSRF token."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "like"}))
    b = Browser(h)
    try:
        await b.login()
        csrf = await b.csrf()
        r = await b.http.post(f"/api/v1/proposals/{p['proposal_id']}/apply", headers={"X-CSRF-Token": csrf})
        assert r.json()["error"] == "browser_required" and ark.votes == {}, r.text
    finally:
        await b.aclose()


async def test_a_collaborator_archidekt_did_not_add_is_reported_not_applied(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    from mtg_gateway.archidekt import ArchidektClient

    async def ignored(self, token, deck_id, user_id):  # Archidekt answers but lists nobody new
        return {}

    monkeypatch.setattr(ArchidektClient, "add_deck_editor", ignored)
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.follows["alice"] = {78}
    p = structured(
        await call(h, token, "propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"})
    )
    out = await _browser_apply(h, p["proposal_id"])
    assert out["ok"] is False, out
    again = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert again["state"] != "applied", again


# 0.7.21: the review-page-only rule is structural. DeckService.apply refuses every account action
# unless its caller is the review page's own form (decks.REVIEW_PAGE), whatever route the call came
# by: an ordinary browser caller, the in-chat card and the assistant are all refused.
ALL_ACCOUNT_ACTIONS = ACCOUNT_ACTIONS + [
    ("propose_comment", {"deck_id": "43", "action": "clear_vote", "comment_id": 555001}),
    ("propose_comment", {"deck_id": "43", "action": "edit", "comment_id": 555002, "text": "New"}),
    ("propose_comment", {"deck_id": "43", "action": "delete", "comment_id": 555002}),
    ("propose_deck_social", {"deck_id": "43", "action": "clear_vote"}),
    ("propose_deck_social", {"deck_id": "43", "action": "unbookmark"}),
    ("propose_deck_social", {"deck_id": "43", "action": "unfollow_owner"}),
    ("propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"}),
    ("propose_collaborator", {"deck_id": "42", "action": "remove", "username": "amy"}),
]


@pytest.mark.parametrize("via", ["browser", "app", "mcp", "auto", ""])
@pytest.mark.parametrize(
    ("tool", "args"),
    ALL_ACCOUNT_ACTIONS,
    ids=[f"{t}:{a.get('action', 'folder')}" for t, a in ALL_ACCOUNT_ACTIONS],
)
async def test_only_the_review_page_marker_applies_an_account_action(
    stack: Stack, via: str, tool: str, args: dict
) -> None:
    from mtg_gateway.decks import REVIEW_PAGE, DeckError

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    _seed_comment(ark)
    own = dict(ark.comments[300043][0], id=555002, text="Mine.")  # alice's own, to edit or delete
    own["owner"] = {"id": 77, "username": "alice", "avatar": None, "frame": None}
    ark.comments[300043].append(own)
    ark.follows["alice"] = {78}
    if args.get("action") == "remove" and tool == "propose_collaborator":
        ark.editors[42] = [_amy_row()]
    await _set_mode(h, "auto")
    p = structured(await call(h, token, tool, args))
    assert p["ok"] and p["kind"] == "action", p
    decks = h.app.state.gateway.decks
    before = (_archidekt_state(ark), [dict(r) for r in ark.editors.get(42, [])])
    with pytest.raises(DeckError) as refused:
        await decks.apply(SUB, p["proposal_id"], via=via)
    assert refused.value.kind == "browser_required"
    assert (_archidekt_state(ark), [dict(r) for r in ark.editors.get(42, [])]) == before
    assert decks.db.get_proposal(p["proposal_id"], SUB)["state"] == "pending"
    # positive control, in the same harness: the review page's marker applies that same proposal
    done = await decks.apply(SUB, p["proposal_id"], via=REVIEW_PAGE)
    assert done["state"] == "applied", done


def test_only_the_review_page_passes_the_marker() -> None:
    """REVIEW_PAGE is defined in decks.py and passed only by pages.py's review form, which carries
    the CSRF check and the click-timing guard; no other module may name it."""
    from pathlib import Path

    src = Path(__file__).resolve().parents[1] / "src" / "mtg_gateway"
    users = sorted(p.name for p in src.rglob("*.py") if "REVIEW_PAGE" in p.read_text(encoding="utf-8"))
    assert users == ["decks.py", "pages.py"], users


# -- 0.7.24 (consolidation review, area 3) ---------------------------------------------------------

READ_ONLY_PROPOSALS = [
    ("propose_deck_social", {"deck_id": "43", "action": "like"}),
    ("propose_comment", {"deck_id": "43", "action": "post", "text": "Hi"}),
    ("propose_delete_deck", {"deck_id": "42"}),
    ("propose_create_folder", {"name": "Brews"}),
    ("propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"}),
]


@pytest.mark.parametrize(("tool", "args"), READ_ONLY_PROPOSALS, ids=[t for t, _ in READ_ONLY_PROPOSALS])
async def test_a_read_only_token_cannot_propose_an_account_action(
    stack: Stack, tool: str, args: dict
) -> None:
    """Bug 1: the account-action tools are write tools, so a read-only token (mtg.read) is refused
    them before the tool runs, and no proposal is stored."""
    import json

    from .test_round4_data import _app_token

    h, ark = stack.h, stack.ark
    await linked_user(stack)
    ark.follows["alice"] = {78}
    token, _cid = await _app_token(h, "reader", scope="mtg.read")
    res = await call(h, token, tool, args)
    assert res.get("isError") and "insufficient_scope" in json.dumps(res), res
    assert h.db.list_proposals(SUB) == []
    # the same tool with a full-scope token still makes its proposal (the positive control)
    full, _cid = await _app_token(h, "writer")
    assert structured(await call(h, full, tool, args))["ok"] is True


def test_every_proposal_tool_is_a_write_tool() -> None:
    """Every tool that stores a proposal (propose_*) is in WRITE_TOOLS, so a new one cannot be
    forgotten again."""
    import re
    from pathlib import Path

    from mtg_gateway.app import WRITE_TOOLS

    pkg = Path(__file__).resolve().parents[1] / "src" / "mtg_gateway"
    src = "".join(p.read_text(encoding="utf-8") for p in pkg.rglob("*.py"))
    names = set(re.findall(r'name="(propose_\w+)"', src))
    assert len(names) >= 10, names
    assert names <= WRITE_TOOLS, names - WRITE_TOOLS


@pytest.mark.parametrize("via", ["mcp", "app", "browser"])
async def test_the_refusal_reports_the_rows_real_tier(stack: Stack, via: str) -> None:
    """Bug 12: the shared apply's refusal (and its audit row) names the row's own tier:
    destructive for a deck deletion, consent for the other account actions."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    decks = h.app.state.gateway.decks
    for tool, args, risk in (
        ("propose_delete_deck", {"deck_id": "42"}, "destructive"),
        ("propose_create_folder", {"name": "Brews"}, "consent"),
    ):
        p = structured(await call(h, token, tool, args))
        with pytest.raises(DeckError) as refused:
            await decks.apply(SUB, p["proposal_id"], via=via)
        assert refused.value.kind == "browser_required"
        assert refused.value.extra["risk"] == risk and refused.value.extra["review_url"] == p["review_url"]
        audit = [
            r
            for r in h.db.audit_for_user(SUB)
            if r["event"] == "apply_needs_user" and (r["detail"] or {}).get("proposal_id") == p["proposal_id"]
        ]
        assert audit and audit[0]["detail"]["risk"] == risk, audit
    assert ark.deleted == [] and all(f["name"] != "Brews" for f in ark.folders.get("alice", []))


@pytest.mark.parametrize("mode", ["manual", "semi", "auto"])
async def test_the_rest_apply_refuses_an_account_action_in_every_mode(stack: Stack, mode: str) -> None:
    """D2: the REST route's own refusal was removed; the shared apply refuses for it with the same
    403 browser_required and the review_url, for a bearer token and for the browser session alike."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, mode)
    for tool, args, risk in (
        ("propose_delete_deck", {"deck_id": "42"}, "destructive"),
        ("propose_deck_social", {"deck_id": "43", "action": "like"}, "consent"),
    ):
        p = structured(await call(h, token, tool, args))
        r = await h.http.post(
            f"/api/v1/proposals/{p['proposal_id']}/apply", headers={"Authorization": f"Bearer {token}"}
        )
        body = r.json()
        assert r.status_code == 403 and body["error"] == "browser_required", r.text
        assert body["review_url"] == p["review_url"] and body["risk"] == risk, body
        b = Browser(h)
        try:
            await b.login()
            csrf = await b.csrf()
            r = await b.http.post(
                f"/api/v1/proposals/{p['proposal_id']}/apply", headers={"X-CSRF-Token": csrf}
            )
            assert r.status_code == 403 and r.json()["error"] == "browser_required", r.text
        finally:
            await b.aclose()
        # the assistant's apply_proposal and the card's confirm (with a correctly signed code) too
        out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
        assert out["ok"] is False and out["error"] == "browser_required" and out["risk"] == risk, out
        row = h.db.get_proposal(p["proposal_id"], SUB)
        code = approval_code(
            h.app.state.gateway.decks.settings.session_secret,
            proposal_id=row["id"],
            sub=SUB,
            client_id=row.get("created_by_client") or "",
            created_at=int(row["created_at"]),
        )
        out = structured(
            await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": code})
        )
        assert out["ok"] is False and out["error"] == "browser_required" and out["risk"] == risk, out
        assert h.db.get_proposal(p["proposal_id"], SUB)["state"] == "pending"
    assert ark.deleted == [] and ark.votes == {}


async def test_reject_attribution_uses_the_review_page_marker(stack: Stack) -> None:
    """D2: reject records "review_page" only for the review page's own form (REVIEW_PAGE), and
    "browser" for the member's other pages; nothing is inferred from a missing ``via``."""
    from mtg_gateway.decks import BROWSER_CLIENT, REVIEW_PAGE, current_client

    h = stack.h
    token = await linked_user(stack)
    decks = h.app.state.gateway.decks
    ids = [
        structured(await call(h, token, "propose_create_folder", {"name": n}))["proposal_id"]
        for n in ("A1", "A2", "A3")
    ]
    reset = current_client.set(BROWSER_CLIENT)
    try:
        assert decks.reject(SUB, ids[0], via=REVIEW_PAGE)["closed_by"] == "review_page"
        assert decks.reject(SUB, ids[1], via="browser")["closed_by"] == "browser"
        assert decks.reject(SUB, ids[2])["closed_by"] == "browser"
    finally:
        current_client.reset(reset)


def test_the_card_has_no_branch_for_an_action_with_a_code() -> None:
    """Bug 12: an account action never gets an in-chat code, so the card's red Delete button and
    its warning (which needed one) are gone; such a card only opens the review page."""
    from mtg_gateway.approve import card_html

    html = card_html()
    for dead in ("Delete this deck on Archidekt", "Approve: do this on Archidekt", "warnline", "destroy"):
        assert dead not in html, dead
    assert "Approve on the review page" in html


async def test_folders_page_new_folder_takes_one_press_and_a_confirmation(stack: Stack) -> None:
    """D9 (0.7.24): creating a folder on the Folders page asks once before it is sent."""
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    b = Browser(h)
    try:
        await b.login()
        csrf = await b.csrf()
        before = [dict(f) for f in ark.folders.get("alice", [])]
        r = await b.http.post("/folders", data={"csrf": csrf, "action": "create", "name": "Brews"})
        assert r.status_code == 200 and "Create the folder “Brews” in your top level" in r.text, r.text
        assert "name='confirmed'" in r.text and ark.folders.get("alice", []) == before
        bad = await b.http.post("/folders", data={"csrf": csrf, "action": "create", "name": ""})
        assert bad.status_code == 400 and ark.folders.get("alice", []) == before
        r = await b.http.post(
            "/folders", data={"csrf": csrf, "action": "create", "name": "Brews", "confirmed": "1"}
        )
        assert r.status_code == 303 and "ok=folder_created" in r.headers["location"]
        assert any(f["name"] == "Brews" for f in ark.folders.get("alice", []))
        nope = await b.http.post(
            "/folders", data={"csrf": "nope", "action": "create", "name": "X", "confirmed": "1"}
        )
        assert nope.status_code == 403 and all(f["name"] != "X" for f in ark.folders.get("alice", []))
    finally:
        await b.aclose()


async def test_settings_page_collaborators_respect_the_writes_switch(stack: Stack) -> None:
    """0.7.24 review: the hand collaborator form used to make a proposal, which MTG_WRITES_ENABLED
    held back; applied at once now, it checks the switch itself and sends nothing."""
    import dataclasses

    h, ark = stack.h, stack.ark
    await linked_user(stack)
    ark.follows["alice"] = {78}
    ark.editors[42] = [_amy_row()]
    decks = h.app.state.gateway.decks
    decks.settings = dataclasses.replace(decks.settings, writes_enabled=False)
    b = Browser(h)
    try:
        await b.login()
        csrf = await b.csrf()
        r = await b.http.post(
            "/decks/42/collaborators",
            data={"csrf": csrf, "action": "remove", "username": "amy", "confirmed": "1"},
        )
        assert r.status_code == 400 and "switched off" in r.text, r.text
        assert len(ark.editors[42]) == 1
    finally:
        await b.aclose()


async def test_hand_delete_and_new_folder_obey_the_writes_switch(stack: Stack) -> None:
    """0.7.25 (Ben's decision 22:20Z 10-11): with MTG_WRITES_ENABLED false, deck delete (the deck
    settings page) and new folder (the Folders page, both its first press and its confirmation)
    are refused like every other Archidekt write, and nothing is sent."""
    import dataclasses

    h, ark = stack.h, stack.ark
    await linked_user(stack)
    decks = h.app.state.gateway.decks
    decks.settings = dataclasses.replace(decks.settings, writes_enabled=False)
    before = [dict(f) for f in ark.folders.get("alice", [])]
    b = Browser(h)
    try:
        await b.login()
        csrf = await b.csrf()
        calls = len(ark.calls)
        r = await b.http.post("/decks/42/delete", data={"csrf": csrf, "name": "Sample Commander Deck"})
        assert r.status_code == 400 and "switched off" in r.text, r.text
        assert ark.deleted == [] and 42 in ark.decks
        for confirmed in ("", "1"):
            r = await b.http.post(
                "/folders",
                data={"csrf": csrf, "action": "create", "name": "Brews", "confirmed": confirmed},
            )
            assert r.status_code == 400 and "switched off" in r.text, r.text
        assert ark.folders.get("alice", []) == before
        assert all(m == "GET" for m, _p in ark.calls[calls:]), ark.calls[calls:]
    finally:
        await b.aclose()
    with pytest.raises(DeckError) as exc:
        await decks.delete_deck(SUB, "42", "Sample Commander Deck")
    assert exc.value.kind == "writes_disabled"
    with pytest.raises(DeckError) as exc:
        await decks.create_folder(SUB, "Brews")
    assert exc.value.kind == "writes_disabled"
    # switched back on, both work again
    decks.settings = dataclasses.replace(decks.settings, writes_enabled=True)
    folder = await decks.create_folder(SUB, "Brews")
    assert folder["name"] == "Brews"


async def test_cover_tags_move_and_folder_rename_obey_the_writes_switch(stack: Stack) -> None:
    """0.7.26 (Ben's yes to Q1, 02:43Z 10-12): with MTG_WRITES_ENABLED false the cover, tags, deck
    move and folder rename are refused like the other deck settings and Folders page writes, and
    nothing is sent."""
    import dataclasses

    h, ark = stack.h, stack.ark
    await linked_user(stack)
    decks = h.app.state.gateway.decks
    folder = await decks.create_folder(SUB, "Brews")  # while writes are still on
    decks.settings = dataclasses.replace(decks.settings, writes_enabled=False)
    b = Browser(h)
    try:
        await b.login()
        csrf = await b.csrf()
        calls = len(ark.calls)
        for path, data in (
            ("/decks/42/cover", {"card": ""}),
            ("/decks/42/tags", {"action": "add", "name": "budget"}),
            ("/decks/42/tags", {"action": "remove", "relation_id": "1"}),
            ("/decks/42/move", {"folder_id": str(folder["id"])}),
            ("/folders", {"action": "rename", "folder_id": str(folder["id"]), "name": "Old brews"}),
        ):
            r = await b.http.post(path, data={"csrf": csrf, **data})
            assert r.status_code == 400 and "switched off" in r.text, (path, r.status_code, r.text[:300])
        assert all(m == "GET" for m, _p in ark.calls[calls:]), ark.calls[calls:]
    finally:
        await b.aclose()
    for pending in (
        decks.set_cover(SUB, "42", None),
        decks.add_tag(SUB, "42", "budget"),
        decks.remove_tag(SUB, "42", 1),
        decks.move_deck(SUB, "42", None),
        decks.rename_folder(SUB, int(folder["id"]), "Old brews"),
    ):
        with pytest.raises(DeckError) as exc:
            await pending
        assert exc.value.kind == "writes_disabled"


async def test_likes_bookmarks_follows_comments_and_collection_edits_obey_the_writes_switch(
    stack: Stack,
) -> None:
    """R-160: with MTG_WRITES_ENABLED false the member's own likes, bookmarks, follows, comments,
    collaborators and collection edits are refused too, and nothing is sent to Archidekt."""
    import dataclasses

    from mtg_gateway.collection import CollectionError

    h, ark = stack.h, stack.ark
    await linked_user(stack)
    decks = h.app.state.gateway.decks
    social = decks.social
    collection = h.app.state.gateway.collection
    decks.settings = dataclasses.replace(decks.settings, writes_enabled=False)
    calls = len(ark.calls)
    for pending in (
        social.vote(SUB, "42", 1),
        social.vote_comment(SUB, "42", 1, 1),
        social.bookmark(SUB, "42", True),
        social.follow(SUB, 7, True),
        social.comment(SUB, "42", "hello", None),
        social.edit_comment(SUB, "42", 1, "edited"),
        social.delete_comment(SUB, "42", 1),
        social.add_collaborator(SUB, "42", 7),
        social.remove_collaborator(SUB, "42", 7),
    ):
        with pytest.raises(DeckError) as exc:
            await pending
        assert exc.value.kind == "writes_disabled"
    for pending in (
        collection.add(SUB, [{"name": "Sol Ring"}], source="test"),
        collection.set_quantity(SUB, 1, 2),
        collection.update(SUB, 1, {"condition": "NM"}),
        collection.remove(SUB, 1),
    ):
        with pytest.raises(CollectionError) as cexc:
            await pending
        assert cexc.value.kind == "writes_disabled"
    assert ark.calls[calls:] == []  # refused before anything, reads included, was sent
    b = Browser(h)
    try:
        await b.login()
        csrf = await b.csrf()
        r = await b.http.post(
            "/social/api/decks/42/bookmark", json={"on": True}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 403 and r.json()["error"] == "writes_disabled", r.text
    finally:
        await b.aclose()
