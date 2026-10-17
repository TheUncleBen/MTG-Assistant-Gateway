"""Release 0.7.29, Keep formatting (Q137 "Keep it, new words plain"): an edit to a formatted deck
description or comment keeps the bold text, links and pictures whose words did not change, writes
new words as plain text, and is approved on the review page, which draws the result."""

from __future__ import annotations

import json
import time

import pytest

from mtg_gateway import batch, formatting
from mtg_gateway.approve import APPROVAL_META_KEY
from mtg_gateway.archidekt import rich_text

from .conftest import Stack
from .test_account_actions import _browser_apply, _press
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import Browser, call, linked_user, structured

SUB = "user-1"


def _doc(*ops: dict) -> str:
    return json.dumps({"ops": list(ops)})


FORMATTED = _doc(
    {"insert": "Great "},
    {"insert": "ramp", "attributes": {"bold": True}},
    {"insert": " deck. See "},
    {"insert": "the guide", "attributes": {"link": "https://example.com/guide"}},
    {"insert": ".\nKey card: "},
    {"insert": {"card-link": "Sol Ring"}},
    {"insert": "\n"},
    {"insert": {"image": "https://example.com/p.png"}},
    {"insert": "Old plan here.\n"},
)


# -- the merge -----------------------------------------------------------------------------------


def test_untouched_words_keep_their_formatting_and_new_words_are_plain() -> None:
    old = rich_text(FORMATTED)
    new = old.replace("Great", "Fun").replace("Old plan here.", "New plan, cut the land.")
    doc, dropped = formatting.keep_formatting(FORMATTED, new)
    ops = json.loads(doc)["ops"]
    assert rich_text(doc) == new
    assert {"insert": "ramp", "attributes": {"bold": True}} in ops
    assert {"insert": "the guide", "attributes": {"link": "https://example.com/guide"}} in ops
    assert {"insert": {"card-link": "Sol Ring"}} in ops
    # the picture sat right before the changed line: kept; the new words carry no attributes
    assert {"insert": {"image": "https://example.com/p.png"}} in ops and dropped == 0
    assert all(not op.get("attributes") for op in ops if "Fun" in str(op.get("insert")))
    assert all(not op.get("attributes") for op in ops if "New plan" in str(op.get("insert")))


def test_a_changed_link_text_loses_its_link_and_a_card_link_in_changed_text_goes() -> None:
    old = rich_text(FORMATTED)
    new = old.replace("the guide", "my notes").replace("Key card: Sol Ring", "Key card: Arcane Signet")
    doc, dropped = formatting.keep_formatting(FORMATTED, new)
    assert rich_text(doc) == new and dropped == 1
    assert "example.com/guide" not in doc and "card-link" not in doc
    assert {"insert": "ramp", "attributes": {"bold": True}} in json.loads(doc)["ops"]


def test_nothing_to_keep_means_plain_text_as_before() -> None:
    plain = _doc({"insert": "Just words\n"})
    assert formatting.keep_formatting(plain, "Other words") is None
    assert formatting.keep_formatting("not a document", "x") is None
    assert formatting.keep_formatting(FORMATTED, "") is None  # clearing it
    odd = _doc({"insert": "a", "attributes": {"bold": True}}, {"retain": 3})
    assert formatting.keep_formatting(odd, "b") is None  # not a stored document


def test_a_document_without_a_last_line_break_still_merges() -> None:
    doc = _doc({"insert": "Bold", "attributes": {"bold": True}}, {"insert": " end"})
    out, _ = formatting.keep_formatting(doc, "Bold finish")
    assert rich_text(out) == "Bold finish" and json.loads(out)["ops"][-1]["insert"].endswith("\n")
    assert json.loads(out)["ops"][0] == {"insert": "Bold", "attributes": {"bold": True}}


def test_a_long_description_rewritten_throughout_stays_quick() -> None:
    ops = []
    for i in range(900):
        ops += [
            {"insert": f"line {i} "},
            {"insert": "bold", "attributes": {"bold": True}},
            {"insert": " end.\n"},
        ]
    doc = _doc(*ops)
    old = rich_text(doc)
    assert len(old) > 15_000
    started = time.monotonic()
    # reversed, no bold word is left where it was: nothing is kept, so it is plain text (None)
    assert formatting.keep_formatting(doc, old[::-1]) is None
    new = "\n".join(x.replace("end", "stop") for x in old.split("\n"))
    out, _ = formatting.keep_formatting(doc, new)
    assert rich_text(out) == new and '"bold":true' in out
    assert time.monotonic() - started < 5, "the merge must not hold an apply up"


def test_the_preview_is_safe_and_shows_each_links_site() -> None:
    doc = _doc(
        {"insert": "<b>x</b> "},
        {"insert": "click", "attributes": {"link": "javascript:alert(1)"}},
        {"insert": " and "},
        {"insert": "here", "attributes": {"link": "https://example.com/a?b=<c>"}},
        {"insert": "\nTitle"},
        {"insert": "\n", "attributes": {"header": 1}},
        {"insert": "one"},
        {"insert": "\n", "attributes": {"list": "ordered"}},
        {"insert": {"image": "https://tracker.example/p.png"}},
        {"insert": "\n"},
    )
    out = formatting.doc_html(doc)
    assert "&lt;b&gt;x&lt;/b&gt;" in out and "<b>" not in out
    assert "javascript:" not in out.split("(link:")[0] and "<a href='javascript" not in out
    assert "href='https://example.com/a?b=&lt;c&gt;'" in out and "(example.com)" in out
    assert "<h3 class='qh'>Title</h3>" in out and "<ol><li>one</li></ol>" in out
    assert "tracker.example" not in out and "[picture]" in out  # never loaded


# -- a description edit ---------------------------------------------------------------------------


async def test_a_description_edit_keeps_formatting_and_is_approved_on_the_review_page(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    ark.decks[42]["description"] = FORMATTED
    new = rich_text(FORMATTED).replace("Great", "Fun")
    res = await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"description": new}})
    p = structured(res)
    assert p["ok"], p
    row = next(r for r in p["rows"] if r["kind"] == "description")
    assert row["after_text"] == new and rich_text(row["after_doc"]) == new
    assert "keeps the formatting already in the description" in p["next_step"]
    # no code for the card: it asks the member to open the review page
    assert not (res.get("_meta") or {}).get(APPROVAL_META_KEY)
    stored = h.db.get_proposal(p["proposal_id"], SUB)
    assert stored["changes"]["description"] == new  # stored as its words; worked out again on apply
    assert "note" not in p  # nothing the assistant sent was lost
    # the review page draws the result with its formatting before the press
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
        assert "How it will look on Archidekt" in page and "<strong>ramp</strong>" in page
        assert "(example.com)" in page and "class='qdoc'" in page
    finally:
        await b.aclose()
    # a batch leaves it waiting, with why
    plan = h.app.state.gateway.decks.plan_selection(SUB, [p["proposal_id"]])
    assert plan["count"] == 0 and plan["skipped"][0]["why"] == batch.FORMATTING_ALONE
    out = await _browser_apply(h, p["proposal_id"])
    assert out.get("state") == "applied", out
    written = ark.decks[42]["description"]
    assert rich_text(written) == new
    ops = json.loads(written)["ops"]
    assert {"insert": "ramp", "attributes": {"bold": True}} in ops
    assert {"insert": {"card-link": "Sol Ring"}} in ops


async def test_formatting_changed_on_archidekt_since_review_needs_a_look(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    ark.decks[42]["description"] = FORMATTED
    new = rich_text(FORMATTED).replace("Great", "Fun")
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"description": new}})
    )
    # same words, the bold taken off on archidekt.com
    ark.decks[42]["description"] = FORMATTED.replace(', "attributes": {"bold": true}', "")
    out = await _browser_apply(h, p["proposal_id"])
    assert out.get("ok") is False and out.get("error") in ("stale", "needs_look"), out
    assert ark.decks[42]["description"] != _doc({"insert": new + "\n"})
    assert h.db.get_proposal(p["proposal_id"], SUB)["state"] == "pending"


@pytest.mark.parametrize("formatted", [True, False])
async def test_words_changed_on_archidekt_since_review_are_not_written_over(
    stack: Stack, formatted: bool
) -> None:
    """An edit that removes all the formatting, or a plain description's edit: the description's
    words changed on archidekt.com after the change was made, so it needs a look and nothing is
    sent (it used to write over the new words)."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    ark.decks[42]["description"] = FORMATTED if formatted else _doc({"insert": "Plain one\n"})
    details = {"deck_id": "42", "details": {"description": "Brand new words"}}
    p = structured(await call(h, token, "propose_deck_details", details))
    assert any(r.get("formatting_lost") for r in p["rows"]) is formatted
    edited = FORMATTED.replace("Great", "Lovely") if formatted else _doc({"insert": "Plain edited on site\n"})
    ark.decks[42]["description"] = edited
    out = await _browser_apply(h, p["proposal_id"])
    assert out.get("ok") is False and out.get("error") in ("stale", "needs_look"), out
    assert ark.decks[42]["description"] == edited
    row = h.db.get_proposal(p["proposal_id"], SUB)
    assert row["state"] == "pending" and row["status"] == "needs_look", row


async def test_new_words_already_on_archidekt_need_a_look(stack: Stack) -> None:
    """The description was changed on archidekt.com to exactly the change's new words: the change
    needs a look (nothing is sent) instead of failing."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    ark.decks[42]["description"] = _doc({"insert": "Plain one\n"})
    details = {"deck_id": "42", "details": {"description": "Brand new words"}}
    p = structured(await call(h, token, "propose_deck_details", details))
    ark.decks[42]["description"] = _doc({"insert": "Brand new words\n"})
    out = await _browser_apply(h, p["proposal_id"])
    assert out.get("ok") is False and out.get("error") in ("stale", "needs_look"), out
    row = h.db.get_proposal(p["proposal_id"], SUB)
    assert row["state"] == "pending" and row["status"] == "needs_look", row


async def test_a_plain_description_is_still_approved_on_the_card(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    ark.decks[42]["description"] = _doc({"insert": "Plain words\n"})
    res = await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"description": "New"}})
    p = structured(res)
    assert not any(r.get("after_doc") for r in p["rows"])
    assert res["_meta"][APPROVAL_META_KEY]


# -- a comment edit -------------------------------------------------------------------------------


async def test_a_comment_edit_keeps_its_link(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    await _press(
        h, token, await call(h, token, "propose_comment", {"deck_id": "42", "action": "post", "text": "x"})
    )
    mine = ark.comments[300042][-1]
    mine["text"] = _doc(
        {"insert": "Read "},
        {"insert": "this", "attributes": {"link": "https://example.com/t"}},
        {"insert": " first.\n"},
    )
    res = await call(
        h,
        token,
        "propose_comment",
        {"deck_id": "42", "action": "edit", "comment_id": mine["id"], "text": "Read this later."},
    )
    p = structured(res)
    row = p["rows"][0]
    assert row["after_text"] == "Read this later." and "example.com/t" in row["after_doc"]
    # the comment's original document as read (social.py's internal "_doc") stays in the gateway;
    # the merged result (rows[].after_doc) does reach the assistant, as on the card
    assert '"_doc"' not in json.dumps(p) and '"after_doc"' in json.dumps(p)
    assert not (res.get("_meta") or {}).get(APPROVAL_META_KEY)
    out = await _browser_apply(h, p["proposal_id"])
    assert out.get("state") == "applied", out
    ops = json.loads(mine["text"])["ops"]
    assert {"insert": "this", "attributes": {"link": "https://example.com/t"}} in ops
    assert rich_text(mine["text"]) == "Read this later."
    # reading the thread shows words only, as before
    read = structured(await call(h, token, "get_deck_comments", {"deck_id": "42"}))
    assert any(c["text"] == "Read this later." for c in read["comments"])
    assert '"_doc"' not in json.dumps(read)


async def test_a_comment_changed_since_its_review_is_not_sent(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    await _press(
        h, token, await call(h, token, "propose_comment", {"deck_id": "42", "action": "post", "text": "x"})
    )
    mine = ark.comments[300042][-1]
    mine["text"] = _doc({"insert": "Bold", "attributes": {"bold": True}}, {"insert": " words\n"})
    p = structured(
        await call(
            h,
            token,
            "propose_comment",
            {"deck_id": "42", "action": "edit", "comment_id": mine["id"], "text": "Bold words now"},
        )
    )
    mine["text"] = _doc({"insert": "Bold words\n"})  # edited on archidekt.com
    out = await _browser_apply(h, p["proposal_id"])
    assert out.get("ok") is False, out
    assert mine["text"] == _doc({"insert": "Bold words\n"})
    got = h.db.get_proposal(p["proposal_id"], SUB)
    assert got["state"] == "pending" and got.get("status") == "needs_look"


async def test_a_comment_rewrite_that_keeps_none_of_its_formatting_says_so(stack: Stack) -> None:
    """Review M-2, E-2 for comments: a comment edit that keeps none of its link says so on its
    review page, is approved there (not on the card) and writes plain text."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    await _press(
        h, token, await call(h, token, "propose_comment", {"deck_id": "42", "action": "post", "text": "x"})
    )
    mine = ark.comments[300042][-1]
    mine["text"] = _doc(
        {"insert": "Read "},
        {"insert": "this", "attributes": {"link": "https://example.com/t"}},
        {"insert": " first.\n"},
    )
    res = await call(
        h,
        token,
        "propose_comment",
        {"deck_id": "42", "action": "edit", "comment_id": mine["id"], "text": "Totally different."},
    )
    p = structured(res)
    row = p["rows"][0]
    assert row.get("formatting_lost") is True and "after_doc" not in row, row
    assert not (res.get("_meta") or {}).get(APPROVAL_META_KEY), res.get("_meta")
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
        assert (
            "This edit removes all the formatting from the comment, such as bold text, links and pictures: "
            "it is written as plain text.</p>" in page
        ), page
    finally:
        await b.aclose()
    plan = batch.plan(h.app.state.gateway.decks, SUB, [p["proposal_id"]])
    assert [s["why"] for s in plan["skipped"]] == [batch.FORMATTING_LOST_ALONE], plan
    out = await _browser_apply(h, p["proposal_id"])
    assert out.get("state") == "applied", out
    assert rich_text(mine["text"]) == "Totally different." and "example.com" not in mine["text"]
    # a plain comment's rewrite has nothing to lose and says nothing
    plain = structured(
        await call(
            h,
            token,
            "propose_comment",
            {"deck_id": "42", "action": "edit", "comment_id": mine["id"], "text": "Other words."},
        )
    )
    assert not plain["rows"][0].get("formatting_lost"), plain["rows"]


async def test_full_auto_waits_for_a_formatted_description_and_for_making_a_deck_public(stack: Stack) -> None:
    """Q141 item 3B (Ben approved 10-17): in Full auto, making a deck public or unlisted and a
    description edit that keeps formatting wait for the member's press; other details apply."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    ark.decks[42]["description"] = FORMATTED
    new = rich_text(FORMATTED).replace("Great", "Fun")
    kept = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"description": new}})
    )
    assert kept["assistant_may_apply"] is False, kept
    assert (
        kept["risk_reason"]
        == "keeps the description's formatting, such as bold text, links or pictures, so you see how it "
        "will look first"
    )
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": kept["proposal_id"]}))
    assert out.get("state") != "applied", out
    ark.decks[42]["private"] = True
    public = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"private": False}})
    )
    assert public.get("assistant_may_apply") is False, public
    assert public["risk_reason"] == "makes the deck public"
    name = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"name": "Auto name"}})
    )
    assert name["assistant_may_apply"] is True  # O7: other details still apply in Full auto
    # Semi-auto is unchanged: every details change waits there
    await _set_mode(h, "semi")
    assert (
        structured(await call(h, token, "get_proposal", {"proposal_id": name["proposal_id"]}))[
            "assistant_may_apply"
        ]
        is False
    )


def test_an_edit_that_keeps_no_formatting_is_plain_text() -> None:
    """Proofread of WORDING-4, option a: when every bold or linked word changes, nothing is kept,
    so the edit is plain text: no "keeps the bold text" note, and the card may approve it."""
    doc = _doc(
        {"insert": "Primer", "attributes": {"link": "https://example.com/p"}}, {"insert": " and notes\n"}
    )
    assert formatting.keep_formatting(doc, "Brand new words") is None
    assert formatting.keep_formatting(doc, "Primer and more notes") is not None  # the link's word stays


async def test_an_edit_that_removes_all_formatting_waits_and_says_so(stack: Stack) -> None:
    """D-1, safe default pending Ben's card (coordinator 03:19Z 10-17): a rewrite of a formatted
    description keeps none of its bold text, links or pictures, so Full auto waits, the review
    page says they go, and a plain description's edit still applies."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    ark.decks[42]["description"] = FORMATTED
    res = await call(
        h, token, "propose_deck_details", {"deck_id": "42", "details": {"description": "Brand new words"}}
    )
    p = structured(res)
    assert p["assistant_may_apply"] is False, p
    assert (
        p["risk_reason"]
        == "removes all of the description's formatting, such as bold text, links and pictures"
    ), p
    assert any(r.get("formatting_lost") for r in p["rows"]), p["rows"]
    # E-2: approved on its review page only, as at 4103450 (the card can't approve it)
    assert not (res.get("_meta") or {}).get(APPROVAL_META_KEY), res.get("_meta")
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out.get("state") != "applied" and ark.patches == [], out
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
        assert "This edit removes all the formatting from the description, such as bold text" in page
        assert "or card links will be removed with it." in page, page
    finally:
        await b.aclose()
    ark.decks[42]["description"] = "Plain words only"
    plain = structured(
        await call(
            h, token, "propose_deck_details", {"deck_id": "42", "details": {"description": "Other words"}}
        )
    )
    assert plain["assistant_may_apply"] is True, plain
    assert not any(r.get("formatting_lost") for r in plain["rows"]), plain["rows"]


STATES = {"private": (True, False), "unlisted": (False, True), "public": (False, False)}


@pytest.mark.parametrize("old", list(STATES))
@pytest.mark.parametrize("new", list(STATES))
async def test_full_auto_waits_for_a_move_to_unlisted_or_public(stack: Stack, old: str, new: str) -> None:
    """Review of 4103450 (High) and D-3 (coordinator 03:19Z 10-17, Ben's Q141 3B words "making a
    deck public or unlisted"): Full auto compares who can see the deck before and after, so any
    move to unlisted or public waits for the member's press, public to unlisted too, and a move to
    private applies. A deck marked both private and unlisted is taken as private (not checked on
    Archidekt)."""
    if old == new:
        pytest.skip("no change")
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    ark.decks[42]["private"], ark.decks[42]["unlisted"] = STATES[old]
    if STATES[old][0]:
        ark.private.add(42)
    else:
        ark.private.discard(42)
    private, unlisted = STATES[new]
    p = structured(
        await call(
            h,
            token,
            "propose_deck_details",
            {"deck_id": "42", "details": {"private": private, "unlisted": unlisted}},
        )
    )
    assert p["ok"], p
    if new != "private":
        assert p["assistant_may_apply"] is False and p["risk_reason"] == f"makes the deck {new}", p
        refused = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
        assert refused.get("state") != "applied", refused
    else:
        assert p["assistant_may_apply"] is True, p


def _set_visibility(ark, private: bool, unlisted: bool) -> None:
    """On the fake Archidekt only, without moving updatedAt (whether Archidekt moves it for these
    is not checked)."""
    ark.decks[42]["private"], ark.decks[42]["unlisted"] = private, unlisted
    (ark.private.add if private else ark.private.discard)(42)


async def test_full_auto_judges_who_sees_the_deck_again_when_it_applies(stack: Stack) -> None:
    """Delta review 2, E-1 (the reviewer's probe): "turn unlisted off" on a private and unlisted
    deck keeps it private, so Full auto may apply it; once the member makes the deck non-private
    on Archidekt (updatedAt not moved), the same change would make it public. It is judged again on
    the deck just read: nothing is sent, and it needs a look with the move in words (E-3)."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    _set_visibility(ark, True, True)
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"unlisted": False}})
    )
    assert p["ok"], p
    _set_visibility(ark, False, True)
    await _set_mode(h, "auto")
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out.get("state") != "applied", out
    assert ark.patches == [] and ark.decks[42]["unlisted"] is True, ark.patches
    row = h.db.get_proposal(p["proposal_id"], SUB)
    assert row["state"] == "pending" and row["status"] == "needs_look", row
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert "so the deck stays private" in got["status_reason"], got["status_reason"]
    assert "so the deck goes from unlisted to public" in got["status_reason"], got["status_reason"]


async def test_a_description_that_gained_formatting_is_judged_again(stack: Stack) -> None:
    """Delta review 2, E-1 for E-2: a plain description's edit may apply in Full auto, but if the
    description gained bold text or links on Archidekt since, the edit would now remove them: it
    needs a look and nothing is sent."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    ark.decks[42]["description"] = "Plain words only"
    p = structured(
        await call(
            h, token, "propose_deck_details", {"deck_id": "42", "details": {"description": "Brand new words"}}
        )
    )
    assert p["ok"], p
    ark.decks[42]["description"] = FORMATTED
    await _set_mode(h, "auto")
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out.get("state") != "applied" and ark.patches == [], out
    assert h.db.get_proposal(p["proposal_id"], SUB)["status"] == "needs_look"
