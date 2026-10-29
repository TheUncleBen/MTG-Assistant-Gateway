"""The review page after the independent review of 0.7.46:

- a proposal marked Needs a look offers no Apply (or Delete) that can only be refused: Reject and
  the way to bring it up to date, and no refusal tells the member to "apply" it again;
- a change the member made on the website that Archidekt didn't answer waits again with Try
  again, as an assistant's does, instead of failing; a website change that fails says why and
  never tells the member to ask an assistant;
- a closed proposal with no risk shows no lone dash;
- a waiting deck edit says the deck's price once applied, as an assistant is told (deck_after)."""

from __future__ import annotations

import html
import re

from mtg_gateway.decks import BROWSER_CLIENT, current_client

from .conftest import Stack
from .test_account_actions import _browser_apply
from .test_assistant_flow import _add, _mode, _propose
from .test_decks_and_proxy import Browser, call, linked_user, structured

SUB = "user-1"


async def _needs_look(stack: Stack, pid: str) -> None:
    """The deck changes on Archidekt after the proposal was made (one more Forest), and the
    re-check marks the waiting proposal Needs a look, as a deck page view would."""
    ark = stack.ark
    forest = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Forest")
    forest["quantity"] += 1
    ark.decks[42]["updatedAt"] = "2026-10-05T00:00:00Z"
    decks = stack.h.app.state.gateway.decks
    decks.deck_lists.clear()
    decks.recheck_waiting(SUB, await decks.get_own_deck(SUB, "42"))
    assert stack.h.db.get_proposal(pid, SUB)["status"] == "needs_look"


async def _page(h, path: str) -> str:
    b = Browser(h)
    try:
        await b.login()
        return (await b.http.get(path)).text
    finally:
        await b.aclose()


def _text(page: str) -> str:
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", page).split()))


async def test_a_stale_delete_offers_no_delete_and_no_refusal_says_apply(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_delete_deck", {"deck_id": "42"}))
    await _needs_look(stack, p["proposal_id"])
    url = f"/proposals/{p['proposal_id']}"
    page = await _page(h, url)
    text = _text(page)
    assert "Needs a look" in text
    assert "Delete this deck on Archidekt" not in page and "id='applyf'" not in page
    assert "Apply will be refused" not in text
    assert "Reject this proposal" in page
    assert "Ask your assistant to update it" in text
    # a press sent anyway (an old tab) is refused, and the words never say to apply it again
    b = Browser(h)
    try:
        await b.login()
        form = {**await b.review_form(url), "version": "1", "action": "apply"}  # the old tab's version
        r = await b.http.post(url, data=form)
        assert r.status_code == 303 and r.headers["location"].endswith("?err=stale"), r.headers
    finally:
        await b.aclose()
    assert ark.deleted == []
    page = await _page(h, f"{url}?err=stale")
    notice = _text(page.split("class='notice error'", 1)[1].split("</div>", 1)[0])
    assert "then apply" not in notice and "Nothing was sent" in notice, notice
    # the assistant hears the same: amend or reject, not apply
    got = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert "then apply" not in got.get("message", ""), got


async def test_a_website_change_archidekt_didnt_answer_waits_again_with_try_again(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    decks = h.app.state.gateway.decks
    mark = current_client.set(BROWSER_CLIENT)
    try:
        p = await decks.propose(SUB, "42", [{"action": "add", "card_name": "Opt", "quantity": 1}])
    finally:
        current_client.reset(mark)
    ark.fail_card_writes = True
    out = await _browser_apply(h, p["proposal_id"])
    assert out.get("error") == "failed", out
    row = h.db.get_proposal(p["proposal_id"], SUB)
    assert row["state"] == "pending" and row["result"]["try_again"] is True, row
    page = await _page(h, f"/proposals/{p['proposal_id']}?err=failed")
    text = _text(page)
    assert "Archidekt didn't answer, so nothing was changed" in text
    assert ">Try again</button>" in page or ">Try again<" in page
    assert "Ask your assistant" not in text
    assert ark.patches == []


async def test_a_website_change_that_fails_says_why_and_names_no_assistant(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    decks = h.app.state.gateway.decks
    mark = current_client.set(BROWSER_CLIENT)
    try:
        p = await decks.propose(SUB, "42", [{"action": "add", "card_name": "Opt", "quantity": 1}])
    finally:
        current_client.reset(mark)
    ark.decks.pop(42)  # gone from Archidekt: no retry fixes that
    out = await _browser_apply(h, p["proposal_id"])
    assert out.get("error"), out
    page = await _page(h, f"/proposals/{p['proposal_id']}?err=failed")
    text = _text(page)
    assert "Details are in the result below, if any" not in text
    assert "Nothing else was changed" not in text
    assert "Ask your assistant" not in text and "Nothing was changed on Archidekt" in text
    assert text.count("could not be applied") <= 1


async def test_an_assistants_failed_change_still_points_to_the_assistant(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = await _propose(h, token, [_add("Opt")])
    ark.decks.pop(42)
    out = await _browser_apply(h, p["proposal_id"])
    assert out.get("error"), out
    text = _text(await _page(h, f"/proposals/{p['proposal_id']}?err=failed"))
    assert "Ask your assistant for a new proposal" in text and "Nothing else was changed" not in text


async def test_a_closed_proposal_with_no_risk_shows_no_lone_dash(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    p = await _propose(h, token, [_add("Opt")])
    h.db._conn.execute(
        "UPDATE proposals SET state = 'failed', risk = 'unjudged' WHERE id = ?", (p["proposal_id"],)
    )
    page = await _page(h, f"/proposals/{p['proposal_id']}")
    pills = page.split("<div class='pills'>", 1)[1].split("</div>", 1)[0]
    assert "ui-none" not in pills and "Risk not judged" not in pills, pills


async def test_a_waiting_edit_says_the_price_once_applied(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    p = await _propose(h, token, [_add("Opt")])
    after = p["deck_after"]
    page = await _page(h, f"/proposals/{p['proposal_id']}")
    line = _text(page.split("pr-after'>", 1)[1].split("</p>", 1)[0])
    assert line.startswith("Once applied:") and f"{after['card_count']} cards" in line, line
    if after["price_total"] is None:
        assert "no price is known" in line.lower(), line
    else:
        assert f"${after['price_total']:,.2f}" in line, line
    if after["unpriced_added"]:
        assert "Opt" in line, line
