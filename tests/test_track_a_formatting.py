"""Release 0.7.29, Keep formatting (Q137 "Keep it, new words plain"): an edit to a formatted deck
description or comment keeps the bold text, links and pictures whose words did not change, writes
new words as plain text, and is approved on the review page, which draws the result."""

from __future__ import annotations

import json
import time

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
    for new in (old[::-1], "\n".join(x.replace("end", "stop") for x in old.split("\n"))):
        out, _ = formatting.keep_formatting(doc, new)
        assert rich_text(out) == new
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
    assert "keeps the bold text, links or pictures" in p["next_step"]
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
