"""Test helpers of 0.7.29 Track A (approvals and proposals).

Since 0.7.29 auto mode no longer applies everything (O7, O8): a restore, a new deck, a commander
change and a big edit wait for the member's press on the card. Tests that used the assistant's
own apply for those press the card instead, as the member would."""

from __future__ import annotations

from typing import Any

from mtg_gateway.approve import APPROVAL_META_KEY


async def call(h, token: str, tool: str, args: dict) -> dict:
    from .test_decks_and_proxy import call as _call  # here, as test_decks_and_proxy imports this module

    return await _call(h, token, tool, args)


def structured(res: dict) -> Any:
    from .test_decks_and_proxy import structured as _structured

    return _structured(res)


async def card_code(h, token: str, pid: str) -> str:
    """The approval code the proposal's card gets in the app that made it."""
    res = await call(h, token, "get_proposal", {"proposal_id": pid})
    code = (res.get("_meta") or {}).get(APPROVAL_META_KEY)
    assert code, structured(res)
    return code


async def press_approve(h, token: str, pid: str) -> dict:
    """The member's press on Approve on the proposal card in the chat."""
    code = await card_code(h, token, pid)
    return structured(
        await call(
            h, token, "confirm_proposal", {"proposal_id": pid, "approval": code, "decision": "approve"}
        )
    )


async def apply_any(h, token: str, pid: str) -> dict:
    """Apply a proposal as the tests did before 0.7.29: the assistant's own apply when the mode
    lets it, else the member's press on the card, or on the review page for a change that keeps
    or removes formatting (Q137, Q141 items 3B and 10, D-1: no card approves it, and Full auto
    waits for it)."""
    p = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    if p.get("assistant_may_apply"):
        return structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
    if any(
        isinstance(r, dict) and (r.get("after_doc") or r.get("formatting_lost")) for r in p.get("rows") or []
    ):
        return await review_page_apply(h, token, pid)
    return await press_approve(h, token, pid)


async def review_page_apply(h, token: str, pid: str) -> dict:
    """The member's press on Apply on the signed-in review page; the proposal as it is after."""
    from .test_decks_and_proxy import Browser

    b = Browser(h)
    try:
        await b.login()
        url = f"/proposals/{pid}"
        r = await b.http.post(url, data={**await b.review_form(url), "action": "apply"})
        assert r.status_code == 303, r.text
    finally:
        await b.aclose()
    return structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
