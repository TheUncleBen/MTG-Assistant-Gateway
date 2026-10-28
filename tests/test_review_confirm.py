"""The one confirm for a destructive approval on the review page (0.7.46): the press on the red
outline button opens the site's shared box (ui.box, class dangerbox) instead of the in-place
second row static/proposals.js used to build; the box's solid red button submits the same guarded
form. One rule for every destructive kind: deleting a deck, taking custom cards out of a deck and a
list change that deletes. Without scripts the plain form keeps its one press."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import re
from pathlib import Path

from mtg_gateway import deckpage, proposals_view, theme

from .conftest import Stack, stack_with
from .test_approval_modes import SHIPPED, _set_mode
from .test_deck_custom_cards import _seed as seed_customs
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .test_lists_page import _seed as seed_lists

stack_options = stack_with(SHIPPED)

STATIC = Path(__file__).parents[1] / "src" / "mtg_gateway" / "static"
SUB = "user-1"


async def _review(h, pid: str) -> str:
    b = Browser(h)
    try:
        await b.login()
        return (await b.http.get(f"/proposals/{pid}")).text
    finally:
        await b.aclose()


def _scripted_form(page: str) -> str:
    """The Apply block as the browser with scripts uses it (its <noscript> copy is cut off)."""
    return page.partition("<div class='apply")[2].partition("<noscript>")[0]


async def test_deleting_a_deck_asks_in_the_shared_box_from_one_outline_button(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    d = structured(await call(h, token, "propose_delete_deck", {"deck_id": "42"}))
    page = await _review(h, d["proposal_id"])
    form = _scripted_form(page)
    # one way in: the outline button opens the box (the ellipsis says so); no submit of its own
    assert form.count("Delete this deck on Archidekt…</button>") == 1
    assert "Delete this deck on Archidekt</button>" not in form
    assert re.search(
        r"<button type='button' class='btn-outline-danger' data-box-open='confirm-delete' disabled "
        r"data-guard>Delete this deck on Archidekt…</button>",
        form,
    ), form[-1500:]
    assert "pr-ask" not in page and "pr-sure" not in page
    assert "<form id='applyf' method='post' class='guarded'" in form
    # the box: the title names the deck, the body what happens and that there is no undo
    box = re.search(r"<div class='ui-box mtgbox dangerbox' id='confirm-delete'.*?</div></div>", page, re.S)
    assert box, page
    assert "role='dialog' aria-modal='true' aria-labelledby='confirm-delete-title' hidden>" in box.group(0)
    assert "<h2 id='confirm-delete-title'>Delete “Sample Commander Deck”?</h2>" in box.group(0)
    assert (
        "This deletes <strong>“Sample Commander Deck”</strong> (100 cards) from your Archidekt account."
        in box.group(0)
    )
    assert "keeps a snapshot of it under History first" in box.group(0)
    assert "<strong>Archidekt itself has no undo.</strong>" in box.group(0)
    assert "Nothing is sent until you press Delete this deck." in box.group(0)
    # Keep it first, the one solid red button last, submitting the guarded form as apply
    assert "<button type='button' class='btn-plain' data-cancel>Keep it</button>" in box.group(0)
    assert re.search(
        r"<button type='submit' class='danger' name='action' value='apply' form='applyf'>"
        r"<svg[^>]*aria-hidden='true'.*?</svg>Delete this deck</button>",
        box.group(0),
    ), box.group(0)
    assert page.count("class='danger'") == 1  # the box's final button is the one solid red
    # without scripts the plain form keeps the one press on its own submit button
    noscript = page.partition("<div class='apply")[2].partition("<noscript>")[2]
    assert (
        "<button type='submit' name='action' value='apply' class='btn-outline-danger'>"
        "Delete this deck on Archidekt</button>" in noscript
    )
    # the change row names the deck in curly quotes, as the site writes names
    assert "Delete your deck “Sample Commander Deck” (100 cards) from your Archidekt account" in page
    assert "Delete your deck &#x27;" not in page


async def test_the_deck_delete_box_says_when_a_backup_copy_is_made_too(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    h.app.state.gateway.decks.live.set("archidekt_backups", True, by=None)
    d = structured(await call(h, token, "propose_delete_deck", {"deck_id": "42"}))
    page = await _review(h, d["proposal_id"])
    assert (
        "keeps a snapshot of it under History first, and a private copy is made in your backup folder "
        "on Archidekt" in page
    )


async def test_taking_custom_cards_out_asks_in_the_same_box(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    seed_customs(ark)
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    add = structured(
        await call(
            h,
            token,
            "propose_custom_card_changes",
            {"deck_id": "42", "changes": [{"action": "add", "custom_card_id": 41, "quantity": 3}]},
        )
    )
    structured(await call(h, token, "apply_proposal", {"proposal_id": add["proposal_id"]}))
    out = structured(
        await call(
            h,
            token,
            "propose_custom_card_changes",
            {"deck_id": "42", "changes": [{"action": "set_quantity", "custom_card_id": 41, "quantity": 2}]},
        )
    )
    assert out["risk"] == "destructive"
    page = await _review(h, out["proposal_id"])
    form = _scripted_form(page)
    assert (
        "<button type='button' class='btn-outline-danger' data-box-open='confirm-delete' disabled "
        "data-guard>Take them out on Archidekt…</button>" in form
    )
    assert "<h2 id='confirm-delete-title'>Take 1 custom card out of “Sample Commander Deck”?</h2>" in page
    assert "This takes <strong>“Grizzled Mentor”</strong> out of the deck on Archidekt" in page
    assert "Restoring a snapshot doesn't put custom cards back." in page
    assert "<button type='button' class='btn-plain' data-cancel>Keep them</button>" in page
    assert "name='action' value='apply' form='applyf'>" in page and "Take them out</button>" in page
    assert page.count("class='danger'") == 1
    assert "Delete this deck" not in page


async def test_a_list_change_that_deletes_asks_in_the_box_and_one_that_does_not_asks_once(
    stack: Stack,
) -> None:
    h, ark = stack.h, stack.ark
    seed_lists(ark)
    token = await linked_user(stack)
    gone = structured(
        await call(
            h,
            token,
            "propose_list_change",
            {
                "which_list": "templates",
                "changes": [{"op": "delete", "name": "Ramp"}, {"op": "create", "name": "Draw"}],
            },
        )
    )
    assert gone["risk"] == "destructive"
    page = await _review(h, gone["proposal_id"])
    form = _scripted_form(page)
    assert (
        "<button type='button' class='primary btn-primary' data-box-open='confirm-delete' disabled "
        "data-guard>Apply to my Archidekt lists…</button>" in form
    )
    assert "<h2 id='confirm-delete-title'>Delete from your Archidekt lists?</h2>" in page
    assert "<li>Delete the category template “Ramp”</li>" in page
    assert "The other change in this proposal is applied with it." in page
    assert "<button type='button' class='btn-plain' data-cancel>Keep them</button>" in page
    assert "name='action' value='apply' form='applyf'>" in page and "Delete 1 entry</button>" in page
    assert page.count("class='danger'") == 1
    # a list change with nothing to delete: one press on the orange button, no box
    plain = structured(
        await call(
            h,
            token,
            "propose_list_change",
            {"which_list": "templates", "changes": [{"op": "create", "name": "Draw"}]},
        )
    )
    page = await _review(h, plain["proposal_id"])
    assert "data-box-open" not in page and "confirm-delete" not in page
    assert (
        "<button type='submit' name='action' value='apply' class='primary btn-primary' disabled data-guard>"
        in page
    )


async def test_the_other_waiting_changes_link_is_a_capsule(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    d = structured(await call(h, token, "propose_delete_deck", {"deck_id": "42"}))
    structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "remove", "card_name": "Counterspell"}]},
        )
    )
    page = await _review(h, d["proposal_id"])
    assert "<a class='ui-cap' href='/proposals?deck_id=42'>Review all 2</a>" in page
    assert "<a href='/proposals?deck_id=42'>Review all" not in page


def test_the_in_place_second_press_and_its_styling_are_gone() -> None:
    js = (STATIC / "proposals.js").read_text(encoding="utf-8")
    assert "pr-ask" not in js and "pr-sure" not in js and "rejectask" not in js
    assert "MtgBox.confirm" in js  # the list bar's Reject asks in the shared box
    assert ".rejectask" not in proposals_view.PROPOSALS_CSS


def test_a_hidden_button_is_hidden_and_the_red_box_title_is_shared() -> None:
    # theme gives every button display:inline-flex, which beat the browser's own [hidden] rule
    assert "button[hidden],.btn[hidden]{display:none}" in theme.CSS
    # the red title of a delete box is the box's own look, not the deck page's
    assert ".mtgbox.dangerbox .bh h2{color:var(--danger-text)}" in theme.BOX_CSS
    assert "dangerbox" not in deckpage.DECK_CSS
