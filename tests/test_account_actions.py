"""0.7.17: the Archidekt account actions an assistant can propose (actions.py): like, bookmark
and follow; post, edit, delete and vote on comments; delete a deck; create a folder. Each is a
proposal, and none of them is ever applied by the assistant, whatever the approval mode:
the member applies each one with their own press. From 0.7.19 to 0.7.28 only the review page
applied one; since 0.7.29 the member's press on the in-chat card does too, with its
one-time code, except a deck deletion, which stays review page only."""

from __future__ import annotations

import json

import pytest

from mtg_gateway import actions, modes
from mtg_gateway.approve import APPROVAL_META_KEY, approval_code
from mtg_gateway.archidekt import rich_doc
from mtg_gateway.decks import DeckError

from .conftest import Stack, stack_with
from .fake_archidekt import FakeArchidekt
from .test_approval_modes import SHIPPED, _set_mode
from .test_decks_and_proxy import Browser, call, linked_user, structured

SUB = "user-1"  # the signed-in member of linked_user


def _amy_public() -> FakeArchidekt:
    ark = FakeArchidekt()
    ark.private.discard(43)  # amy's deck is public: someone else's deck to like and follow
    return ark


stack_options = stack_with(SHIPPED.but(archidekt=_amy_public))


async def _browser_apply(h, pid: str) -> dict:
    """The member's press on the signed-in review page's Apply (session cookie and CSRF token)."""
    b = Browser(h)
    try:
        await b.login()
        url = f"/proposals/{pid}"
        r = await b.http.post(url, data={**await b.review_form(url), "action": "apply"})
        assert r.status_code == 303, r.text
        err = r.headers["location"].partition("?err=")[2]
        if err:  # the review page shows the refusal by its code
            return {"ok": False, "error": err}
        return (await b.http.get(f"/api/v1/proposals/{pid}")).json()
    finally:
        await b.aclose()


async def _press(h, token: str, res) -> dict:
    """The member's own press: on the in-chat card with its one-time code (0.7.29), or, for a
    deck deletion, which has no code, on its review page."""
    p = structured(res)
    code = (res.get("_meta") or {}).get(APPROVAL_META_KEY)
    if p["risk"] == "destructive":
        assert not code, res.get("_meta")
        out = await _browser_apply(h, p["proposal_id"])
    else:
        assert code, res.get("_meta")
        out = structured(
            await call(
                h,
                token,
                "confirm_proposal",
                {"proposal_id": p["proposal_id"], "approval": code, "decision": "approve"},
            )
        )
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
    """No mode lets an assistant apply an account action."""
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


async def test_a_like_already_given_says_so_and_sends_nothing(stack: Stack) -> None:
    """A like proposal for a deck the member already likes says applying changes nothing,
    and applying it sends no vote (Archidekt might toggle it off)."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _propose_and_apply(h, token, "propose_deck_social", {"deck_id": "43", "action": "like"})
    sent = len(ark.vote_calls)
    res = await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "like"})
    assert structured(res)["rows"][0]["text"].endswith("(already the case; applying changes nothing)")
    await _press(h, token, res)
    assert len(ark.vote_calls) == sent and ark.votes == {("alice", 300043): 1}


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


async def test_an_assistant_comment_is_posted_as_plain_text_only(stack: Stack) -> None:
    """A comment proposed as a Quill document with a hidden link shows its words on the
    review row, and exactly those words are posted (and edited) when the member applies it. (The
    MCP tool itself turns a JSON text argument into an object and refuses it, so the proposal is
    made here the way the tool makes it, with the text as a string.)"""
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    decks = h.app.state.gateway.decks
    ark.comments[300043] = []
    phish = json.dumps(
        {"ops": [{"insert": "See my primer", "attributes": {"link": "https://evil.example/login"}}]}
    )
    p = await actions.propose(decks, SUB, "comment_post", {"deck_id": "43", "text": phish, "reply_to": None})
    assert p["rows"][0]["after_text"] == "See my primer" and "evil" not in json.dumps(p), p
    assert (await _browser_apply(h, p["proposal_id"]))["state"] == "applied"
    (made,) = ark.comments[300043]
    assert made["text"] == rich_doc("See my primer")
    edit = {"deck_id": "43", "comment_id": made["id"], "text": phish.replace("See", "Read")}
    p = await actions.propose(decks, SUB, "comment_edit", edit)
    assert p["rows"][0]["after_text"] == "Read my primer" and "evil" not in json.dumps(p), p
    assert (await _browser_apply(h, p["proposal_id"]))["state"] == "applied"
    assert ark.comments[300043][0]["text"] == rich_doc("Read my primer")


COMMENT_NOTE = (
    "Only the words of this comment are saved: the gateway writes new text as plain text, so the "
    "links, formatting and embeds in what you sent were removed (formatting already on Archidekt "
    "stays where its words did not change). Don't tell the user a link or formatting was added."
)


async def test_the_assistant_is_told_when_a_comment_loses_its_link(stack: Stack) -> None:
    """A comment post or edit proposed as a Quill document with a link (or a card link) is
    saved as its words, and the proposal reply says so. (Made as the tool makes it: see
    test_an_assistant_comment_is_posted_as_plain_text_only.)"""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    decks = h.app.state.gateway.decks
    ark.comments[300043] = []
    phish = json.dumps(
        {"ops": [{"insert": "See my primer", "attributes": {"link": "https://evil.example/login"}}]}
    )
    p = await actions.propose(decks, SUB, "comment_post", {"deck_id": "43", "text": phish, "reply_to": None})
    assert p["note"] == COMMENT_NOTE and p["rows"][0]["after_text"] == "See my primer", p
    assert p["changes"]["text"] == "See my primer"  # the same text is written as before
    # the tool reply carries it, in its structured content and its text
    from mtg_gateway.approve import proposal_tool_result

    res = proposal_tool_result({"ok": True, **p}, decks=decks, sub=SUB, in_chat=True)
    assert res.structured_content["note"] == COMMENT_NOTE
    assert "note: " + COMMENT_NOTE in res.content[0].text
    assert (await _browser_apply(h, p["proposal_id"]))["state"] == "applied"
    (made,) = ark.comments[300043]
    card = json.dumps({"ops": [{"insert": "Read "}, {"insert": {"card-link": "Sol Ring"}}, {"insert": "\n"}]})
    p = await actions.propose(
        decks, SUB, "comment_edit", {"deck_id": "43", "comment_id": made["id"], "text": card}
    )
    assert p["note"] == COMMENT_NOTE and p["rows"][0]["after_text"] == "Read Sol Ring", p
    # a later get_proposal of it (stored as plain text) adds none
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["ok"] and "note" not in got, got


@pytest.mark.parametrize(
    "text",
    [
        "Nice deck!",
        "**Nice** deck: [see](https://example.com/x)",
        json.dumps({"ops": [{"insert": "Nice deck!\n"}]}),
    ],
)
async def test_a_comment_that_loses_nothing_gets_no_note(stack: Stack, text: str) -> None:
    """Plain text, Markdown and a document of words only lose nothing: no note, through the
    tool itself as well."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.comments[300043] = []
    p = await actions.propose(
        h.app.state.gateway.decks, SUB, "comment_post", {"deck_id": "43", "text": text, "reply_to": None}
    )
    assert "note" not in p, p
    if not text.startswith("{"):  # the MCP layer turns JSON text into an object and refuses it
        res = await call(h, token, "propose_comment", {"deck_id": "43", "action": "post", "text": text})
        assert structured(res)["ok"] and "note" not in structured(res), structured(res)


async def test_a_comment_stored_by_0726_tells_the_assistant_in_get_proposal(stack: Stack) -> None:
    """A comment proposal stored by 0.7.26 with a formatted document is posted as its words
    when the member applies it; get_proposal tells the assistant so."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.comments[300043] = []
    res = await call(h, token, "propose_comment", {"deck_id": "43", "action": "post", "text": "x"})
    pid = structured(res)["proposal_id"]
    row = h.db.get_proposal(pid, SUB)
    phish = json.dumps({"ops": [{"insert": "Hi", "attributes": {"bold": True}}, {"insert": "\n"}]})
    changes = {**row["changes"], "text": phish}
    h.db._conn.execute("UPDATE proposals SET changes_json = ? WHERE id = ?", (json.dumps(changes), pid))
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["note"] == COMMENT_NOTE, got
    assert (await _browser_apply(h, pid))["state"] == "applied"
    assert ark.comments[300043][0]["text"] == rich_doc("Hi")
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["state"] == "applied" and got["note"] == COMMENT_NOTE, got


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


async def test_an_applied_action_says_what_it_did(stack: Stack) -> None:
    """After an account action the assistant, the review page and the card say what was done,
    not that a deck matches the proposal (which is still said for a deck edit)."""
    import html

    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    done = await _press(h, token, await call(h, token, "propose_create_folder", {"name": "Brews"}))
    assert done["applied_text"] == "The folder is created on Archidekt."
    assert done["next_step"] == "Done. The folder is created on Archidekt."
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{done['proposal_id']}?ok=applied")).text
    finally:
        await b.aclose()
    assert html.escape("Applied. The folder is created on Archidekt.") in page
    assert html.escape("Done. The folder is created on Archidekt.") in page
    assert "matches this proposal" not in page
    gone = await _press(h, token, await call(h, token, "propose_delete_deck", {"deck_id": "42"}))
    assert gone["result"]["backup_deck_id"] and gone["next_step"] == (
        "Done. The deck is deleted on Archidekt. A snapshot was kept, and a backup copy is in your "
        "backup folder."
    )


async def test_a_folder_named_like_the_backup_folder_is_refused_when_proposed(stack: Stack) -> None:
    """The assistant's new-folder proposal with the backup folder's name is
    refused when it is made, so the assistant can pick another name at once; the apply checks again."""
    import dataclasses

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    for name in ("MTG Gateway backups", "mtg gateway backups"):
        p = structured(await call(h, token, "propose_create_folder", {"name": name}))
        assert p["ok"] is False and p["message"] == (
            f"'{name}' is the name of the gateway's backup folder. Pick another name."
        ), p
    assert not any(f["name"].casefold() == "mtg gateway backups" for f in ark.folders.get("alice", []))
    # the same name in other capitals inside another folder is not the backup folder's
    await _press(h, token, await call(h, token, "propose_create_folder", {"name": "Brews"}))
    inner = structured(
        await call(h, token, "propose_create_folder", {"name": "mtg gateway backups", "inside": "Brews"})
    )
    assert inner["ok"] is True, inner
    # at apply: a name that became the backup folder's after the proposal was made is refused there
    p = structured(await call(h, token, "propose_create_folder", {"name": "Archive"}))
    assert p["ok"] is True, p
    decks = h.app.state.gateway.decks
    decks.settings = dataclasses.replace(decks.settings, archidekt_backup_folder="Archive")
    out = await _browser_apply(h, p["proposal_id"])
    assert out.get("state") != "applied", out
    assert not any(f["name"] == "Archive" for f in ark.folders["alice"])


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


# Every account action, in the modes that let an assistant apply other changes. The
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
    monkeypatch.setattr(modes, "assistant_may_apply", lambda mode, risk, **_: True)
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
async def test_the_in_chat_card_applies_an_account_action_only_with_the_members_press(
    stack: Stack, tool: str, args: dict
) -> None:
    """0.7.29: the member's press on the in-chat card applies an account action
    with its one-time code; a deck deletion gets no code and stays review page only. The
    assistant's own apply and the REST apply with its bearer token stay refused."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    _seed_comment(ark)
    res = await call(h, token, tool, args)
    p = structured(res)
    assert p["ok"] and p["kind"] == "action", p
    deletion = tool == "propose_delete_deck"
    code = (res.get("_meta") or {}).get(APPROVAL_META_KEY)
    decks = h.app.state.gateway.decks
    row = h.db.get_proposal(p["proposal_id"], SUB)
    before = _archidekt_state(ark)
    # the assistant's own apply and the REST apply with the assistant's bearer token: refused
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required", out
    r = await h.http.post(
        f"/api/v1/proposals/{p['proposal_id']}/apply", headers={"Authorization": f"Bearer {token}"}
    )
    assert r.json().get("error") == "browser_required", r.text
    assert _archidekt_state(ark) == before
    if deletion:
        assert not code, res.get("_meta")
        assert "review page" in p["next_step"] and "proposal card in the chat" not in p["next_step"]
        with pytest.raises(DeckError) as no_code:
            decks.approval_for(SUB, row)
        assert no_code.value.kind == "browser_required"
        # a correctly signed code, as a card before 0.7.19 could have held, is refused all the same
        signed = approval_code(
            decks.settings.session_secret,
            proposal_id=row["id"],
            sub=SUB,
            client_id=row.get("created_by_client") or "",
            created_at=int(row["created_at"]),
        )
        out = structured(
            await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": signed})
        )
        assert out["ok"] is False and out["error"] == "browser_required" and out["risk"] == "destructive"
        assert ark.deleted == [] and h.db.get_proposal(p["proposal_id"], SUB)["state"] == "pending"
        return
    assert code, res.get("_meta")
    assert "proposal card in the chat" in p["next_step"] and "Do not call apply_proposal" in p["next_step"]
    # a wrong code does nothing; the card's own code applies it
    bad = structured(
        await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": "x" * 40})
    )
    assert bad["ok"] is False and _archidekt_state(ark) == before, bad
    out = structured(
        await call(
            h,
            token,
            "confirm_proposal",
            {"proposal_id": p["proposal_id"], "approval": code, "decision": "approve"},
        )
    )
    assert out["ok"] and out["state"] == "applied", out
    assert _archidekt_state(ark) != before


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
    assert (res.get("_meta") or {}).get(APPROVAL_META_KEY)  # the member's press on the card may
    before = [dict(r) for r in ark.editors.get(42, [])]
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required", out
    assert [dict(r) for r in ark.editors.get(42, [])] == before


async def test_settings_page_collaborators_take_one_press_and_a_confirmation(stack: Stack) -> None:
    """0.7.24: on the member's own settings page, adding or removing a collaborator is one
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
        # One line on the rule; the people followed are offered (the site's own combobox, no
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
        # One message, under the box, with the person's Archidekt profile to follow them there;
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
    """The direct form is the member's own click. An assistant's bearer token is not a
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
    """Only the review page's own form applies an account action, not the JSON API, even
    with the member's signed-in session and CSRF token."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "like"}))
    b = Browser(h)
    try:
        await b.login()
        csrf = await b.csrf()
        r = await b.http.post(
            f"/api/v1/proposals/{p['proposal_id']}/apply",
            headers={"X-CSRF-Token": csrf},
            json={"version": p["version"]},
        )
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


@pytest.mark.parametrize("via", ["browser", "mcp", "auto", ""])
@pytest.mark.parametrize(
    ("tool", "args"),
    ALL_ACCOUNT_ACTIONS,
    ids=[f"{t}:{a.get('action', 'folder')}" for t, a in ALL_ACCOUNT_ACTIONS],
)
async def test_only_the_review_page_marker_applies_an_account_action(
    stack: Stack, via: str, tool: str, args: dict
) -> None:
    """Every route but the member's own presses is refused. The card's press ("app", 0.7.29)
    is tested with its code in test_the_in_chat_card_applies_an_account_action_only_with_the_members_press."""
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
    sent = len(ark.calls)
    with pytest.raises(DeckError) as refused:
        await decks.apply(SUB, p["proposal_id"], via=via)
    assert refused.value.kind == "browser_required"
    assert (_archidekt_state(ark), [dict(r) for r in ark.editors.get(42, [])]) == before
    # nothing reached Archidekt, by its request log: not even a read
    assert ark.calls[sent:] == [], ark.calls[sent:]
    assert decks.db.get_proposal(p["proposal_id"], SUB)["state"] == "pending"
    # positive control, in the same harness: the review page's marker applies that same proposal
    done = await decks.apply(SUB, p["proposal_id"], via=REVIEW_PAGE)
    assert done["state"] == "applied", done


async def test_the_refusal_list_covers_every_account_action(stack: Stack) -> None:
    """Every action type actions.py knows is in ALL_ACCOUNT_ACTIONS, so a new
    one cannot skip the refusal test above."""
    from mtg_gateway.actions import ACTIONS

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    _seed_comment(ark)
    own = dict(ark.comments[300043][0], id=555002, text="Mine.")
    own["owner"] = {"id": 77, "username": "alice", "avatar": None, "frame": None}
    ark.comments[300043].append(own)
    ark.follows["alice"] = {78}
    ark.editors[42] = [_amy_row()]
    seen = set()
    for tool, args in ALL_ACCOUNT_ACTIONS:
        if tool == "propose_collaborator" and args["action"] == "add":
            ark.editors[42] = []
        p = structured(await call(h, token, tool, args))
        assert p["ok"], (tool, args, p)
        seen.add(p["rows"][0]["action"])
        h.app.state.gateway.decks.reject(SUB, p["proposal_id"])
        ark.editors[42] = [_amy_row()]
    assert seen == set(ACTIONS), set(ACTIONS) - seen


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
    cases = [
        ("propose_delete_deck", {"deck_id": "42"}, "destructive"),
        ("propose_create_folder", {"name": "Brews"}, "consent"),
    ]
    if via == "app":  # 0.7.29: the card's press may apply a folder; only a deletion is refused
        cases = cases[:1]
    for tool, args, risk in cases:
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
    """The REST route's own refusal was removed; the shared apply refuses for it with the same
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
                f"/api/v1/proposals/{p['proposal_id']}/apply",
                headers={"X-CSRF-Token": csrf},
                json={"version": p["version"]},
            )
            assert r.status_code == 403 and r.json()["error"] == "browser_required", r.text
        finally:
            await b.aclose()
        # the assistant's apply_proposal too
        out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
        assert out["ok"] is False and out["error"] == "browser_required" and out["risk"] == risk, out
        assert h.db.get_proposal(p["proposal_id"], SUB)["state"] == "pending"
        if risk != "destructive":
            continue  # 0.7.29: the card's confirm with its code is the member's press
        # a deck deletion: the card's confirm with a correctly signed code is refused as well
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
    """Reject records "review_page" only for the review page's own form (REVIEW_PAGE), and
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
    """Bug 12: a deck deletion never gets an in-chat code, so the card's red Delete button and its
    warning (which needed one) are gone; its card only opens the review page. Since 0.7.29 the
    other account actions use the card's ordinary Approve button."""
    from mtg_gateway.approve import card_html

    html = card_html()
    for dead in ("Delete this deck on Archidekt", "Approve: do this on Archidekt", "warnline", "destroy"):
        assert dead not in html, dead
    assert "Approve on the review page" in html


async def test_folders_page_new_folder_takes_one_press_and_a_confirmation(stack: Stack) -> None:
    """0.7.24: creating a folder on the Folders page asks once before it is sent."""
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
    """0.7.25: with MTG_WRITES_ENABLED false, deck delete (the deck
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
    """0.7.26: with MTG_WRITES_ENABLED false the cover, tags, deck
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
    """With MTG_WRITES_ENABLED false the member's own likes, bookmarks, follows, comments,
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


# -- an action whose write landed, then whose check met Archidekt's long wait ---------------------------
async def _own_comment(h, token: str, ark) -> int:
    """Post a comment of the member's own on deck 43 (through a proposal) and return its id."""
    res = await call(h, token, "propose_comment", {"deck_id": "43", "action": "post", "text": "Mine."})
    await _press(h, token, res)
    return next(c["id"] for c in ark.comments[300043] if c["text"] == rich_doc("Mine."))


async def _collaborator_amy(h, token: str, ark, *, added: bool) -> None:
    ark.follows["alice"] = {78}
    if added:
        await _propose_and_apply(
            h, token, "propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"}
        )


@pytest.mark.parametrize(
    "case", ["collaborator_add", "collaborator_remove", "comment_edit", "comment_delete", "create_folder"]
)
async def test_an_action_whose_write_landed_is_never_put_back_to_pending(
    stack: Stack, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    """The action's write reached Archidekt, then the re-read that checks it met a
    429 longer than an apply may wait. Something changed, so the proposal stays failed and never
    says "Nothing was changed" (every write of an apply counts, fail closed)."""
    import httpx

    from mtg_gateway import social
    from mtg_gateway.archidekt import ArchidektClient

    async def no_wait(self, seconds: float) -> None:
        return None

    monkeypatch.setattr(ArchidektClient, "_wait_out", no_wait)
    monkeypatch.setattr(social, "FRESH_GAP", 0.0)
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    if case == "collaborator_add":
        await _collaborator_amy(h, token, ark, added=False)
        tool, args = "propose_collaborator", {"deck_id": "42", "action": "add", "username": "amy"}
        method, path, landed = "POST", "/editors/", lambda: len(ark.editors.get(42, [])) == 1
    elif case == "collaborator_remove":
        await _collaborator_amy(h, token, ark, added=True)
        tool, args = "propose_collaborator", {"deck_id": "42", "action": "remove", "username": "amy"}
        method, path, landed = "DELETE", "/decks/editors/", lambda: ark.editors[42] == []
    elif case in ("comment_edit", "comment_delete"):
        mine = await _own_comment(h, token, ark)
        if case == "comment_edit":
            tool = "propose_comment"
            args = {"deck_id": "43", "action": "edit", "comment_id": mine, "text": "Changed."}
            method, path = "PATCH", "/comments/"
            landed = lambda: any(c["text"] == rich_doc("Changed.") for c in ark.comments[300043])  # noqa: E731
        else:
            tool, args = "propose_comment", {"deck_id": "43", "action": "delete", "comment_id": mine}
            method, path = "DELETE", "/comments/"
            landed = lambda: all(c["id"] != mine for c in ark.comments[300043])  # noqa: E731
    else:
        tool, args = "propose_create_folder", {"name": "Brews"}
        method, path = "POST", "/decks/folders/"
        landed = lambda: any(f["name"] == "Brews" for f in ark.folders["alice"])  # noqa: E731
    p = structured(await call(h, token, tool, args))
    assert p["ok"] and p["state"] == "pending", p
    orig = ark.transport.handler
    wrote = {"x": False}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == method and path in req.url.path:
            wrote["x"] = True
            return orig(req)
        if wrote["x"] and req.method == "GET":
            return httpx.Response(429, json={"detail": "slow"}, headers={"Retry-After": "3600"})
        return orig(req)

    ark.transport.handler = handler
    try:
        out = await _browser_apply(h, p["proposal_id"])
    finally:
        ark.transport.handler = orig
        h.app.state.gateway.decks.client.pacer._retry_until = 0
    assert wrote["x"] and landed(), case
    row = h.db.get_proposal(p["proposal_id"], SUB)
    assert out["ok"] is False and row["state"] == "failed", (case, out, row["result"])
    assert row["result"]["error"] == "rate_limited", row["result"]
    assert "Nothing was changed" not in row["result"]["detail"], row["result"]["detail"]
    assert "Part of this change had already reached Archidekt" in row["result"]["detail"]


async def test_follow_and_unfollow_someone_by_username(stack: Stack) -> None:
    """The assistant can follow or unfollow anyone by their Archidekt username. It
    is an account action like the rest: only the member's press applies it, and the person is
    found with one read, through their public decks."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    before = len(ark.calls)
    res = await call(h, token, "propose_deck_social", {"action": "follow_user", "username": "@amy"})
    p = structured(res)
    assert p["ok"] and p["kind"] == "action" and p["assistant_may_apply"] is False, p
    assert p["rows"][0]["text"] == "Follow amy on Archidekt", p["rows"]
    assert [c for c in ark.calls[before:] if "/decks/v3/" in c[1]], ark.calls[before:]
    refused = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert refused["ok"] is False and 78 not in ark.follows.get("alice", set()), refused
    await _press(h, token, res)
    assert 78 in ark.follows["alice"]
    res = await call(h, token, "propose_deck_social", {"action": "unfollow_user", "username": "amy"})
    assert structured(res)["rows"][0]["text"] == "Stop following amy on Archidekt"
    await _press(h, token, res)
    assert 78 not in ark.follows["alice"]
    # yourself, someone with no public decks, and no name at all are refused, with nothing proposed
    for name, kind in (("alice", "invalid"), ("nobody-here", "not_found"), ("", "invalid")):
        out = structured(
            await call(h, token, "propose_deck_social", {"action": "follow_user", "username": name})
        )
        assert out["ok"] is False and out["error"] == kind, (name, out)
    ark.private.add(43)  # amy's only deck private: she can't be found
    out = structured(
        await call(h, token, "propose_deck_social", {"action": "follow_user", "username": "amy"})
    )
    assert out["error"] == "not_found" and "public decks" in out["message"], out


async def test_apply_all_on_the_card_lists_account_actions_before_the_press(stack: Stack) -> None:
    """Likes, follows and the like can be in one "Apply all" press, and the proposal
    card's confirm lists each one first (set_lines), as the waiting card does."""
    from mtg_gateway.approve import APPROVAL_SET_META_KEY

    h = stack.h
    token = await linked_user(stack)
    await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "like"})
    res = await call(h, token, "propose_deck_social", {"deck_id": "43", "action": "follow_owner"})
    info = structured(res)["deck_waiting"]
    ids = res["_meta"][APPROVAL_SET_META_KEY]["ids"]
    lines = {x["proposal_id"]: x["summary"] for x in info["set_lines"]}
    assert len(ids) == 2 and set(lines) == set(ids), (ids, lines)
    assert any("Like" in s for s in lines.values()) and any("Follow" in s for s in lines.values()), lines
