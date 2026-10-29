"""Test helpers for the 0.7.29 approvals and proposals.

Since 0.7.29 auto mode no longer applies everything: a restore, a new deck, a commander
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


async def card_read(h, token: str, pid: str, **args) -> dict:
    """The newest card's own read of its proposal: it names its card number, which the read says
    (a read naming none is a card from before card numbers, which gets no code once a card has one)."""
    res = await call(h, token, "get_proposal", {"proposal_id": pid, **args})
    seq = (res.get("structuredContent") or {}).get("card_seq")
    if seq and "card_seq" not in args:
        res = await call(h, token, "get_proposal", {"proposal_id": pid, "card_seq": seq, **args})
    return res


async def card_code(h, token: str, pid: str) -> str:
    """The approval code the proposal's card gets in the app that made it."""
    res = await card_read(h, token, pid)
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
    or removes formatting (no card approves it, and Full auto
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


# 0.7.46: the seven account-action tools became one, propose_account_action. The tests written
# for them keep their own words; this turns a call to an old tool into the new tool's call.
OLD_ACCOUNT_TOOLS = frozenset(
    {
        "propose_deck_social",
        "propose_comment",
        "propose_create_folder",
        "propose_collaborator",
        "propose_deck_help",
        "propose_report",
        "propose_contest_entry",
    }
)


def account_action_args(tool: str, args: dict) -> dict:
    """The propose_account_action arguments for a call of one of the old seven tools."""
    args = dict(args)
    if tool == "propose_comment":
        args["action"] = "comment_" + args["action"]
    elif tool == "propose_create_folder":
        args["action"] = "create_folder"
    elif tool == "propose_collaborator":
        args["action"] = "collaborator_" + args["action"]
    elif tool == "propose_deck_help":
        args["action"] = "help_" + args.pop("help_action", "post")
    elif tool == "propose_report":
        args["action"] = "report"
    elif tool == "propose_contest_entry":
        args["action"] = "contest_" + args.pop("entry_action", "enter")
    return args


async def account_call(h, token: str, tool: str, args: dict | None = None) -> dict:
    """``call``, with a call of an old account-action tool made as propose_account_action."""
    if tool in OLD_ACCOUNT_TOOLS:
        return await call(h, token, "propose_account_action", account_action_args(tool, args or {}))
    return await call(h, token, tool, args or {})
