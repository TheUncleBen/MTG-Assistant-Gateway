"""Release 0.7.30, approvals: a change held after the member's screen showed it is not sent from
that screen. The waiting card's Approve and its Apply all, the proposal card's Apply all, the
review page and the Proposals page's confirm each send the status they showed; a change held
since is refused (one change) or left out and reported as not sent (several), and the rest of
the selection is applied."""

from __future__ import annotations

import asyncio
import re

from mtg_gateway.approve import APPROVAL_SET_META_KEY, APPROVALS_META_KEY

from .conftest import Stack, aged_stamp
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .test_round4_data import _app_token
from .test_track_a_card_hold import _add, _press, _propose, _qty
from .track_a_helpers import card_code

SUB = "user-1"
HELD = "It would replace #1, which was applied."
HELD_SINCE_TEXT = "Not sent: it is held for a reason that came up after you saw it. "


async def _replacement(stack: Stack) -> tuple:
    """Claude's change replaces ChatGPT's, and Claude has another change waiting."""
    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    theirs = await _propose(h, b, _add("Arcane Signet"))
    new = await _propose(h, a, _add("Delver of Secrets"), replaces=[theirs["proposal_id"]])
    other = await _propose(h, a, _add("Opt"))
    return h, a, b, theirs["proposal_id"], new["proposal_id"], other["proposal_id"]


async def _apply_theirs(h, b, theirs: str, pid: str) -> None:
    """The member applies ChatGPT's change, so Claude's replacement is held."""
    tb = await card_code(h, b, theirs)
    assert (await _press(h, b, theirs, tb))["state"] == "applied"
    assert h.db.get_proposal(pid, SUB)["status"] == "held"


async def _waiting_card(h, a) -> tuple[dict, dict]:
    res = await call(h, a, "show_waiting_changes", {"deck_id": "42"})
    return structured(res), res["_meta"]


async def test_approve_on_the_waiting_card_refuses_a_change_held_since(stack: Stack) -> None:
    h, a, b, theirs, pid, _ = await _replacement(stack)
    data, meta = await _waiting_card(h, a)
    row = next(x for x in data["waiting"] if x["proposal_id"] == pid)
    assert not row.get("status")
    code = meta[APPROVALS_META_KEY][pid]
    await _apply_theirs(h, b, theirs, pid)
    before = _qty(stack.ark, "Delver of Secrets")
    # the card sends the status its row showed: none
    got = await _press(h, a, pid, code, shown_status="")
    assert got["error"] == "now_held" and got["status_reason"] == HELD, got
    assert _qty(stack.ark, "Delver of Secrets") == before
    assert h.db.get_proposal(pid, SUB)["state"] == "pending"


async def test_apply_all_leaves_out_a_change_held_since_and_applies_the_rest(stack: Stack) -> None:
    h, a, b, theirs, pid, other = await _replacement(stack)
    data, meta = await _waiting_card(h, a)
    s = meta[APPROVAL_SET_META_KEY]
    assert sorted(s["ids"]) == sorted([pid, other])
    shown = {x["proposal_id"]: {"status": x.get("status") or ""} for x in data["waiting"]}
    await _apply_theirs(h, b, theirs, pid)
    before = _qty(stack.ark, "Delver of Secrets")
    got = structured(
        await call(h, a, "confirm_waiting", {"proposal_ids": s["ids"], "approval": s["code"], "shown": shown})
    )
    for _ in range(50):
        if h.db.get_proposal(other, SUB)["state"] not in ("pending", "applying"):
            break
        await asyncio.sleep(0.1)
    assert [c["proposal_id"] for c in got["applied"]] == [other], got
    assert h.db.get_proposal(other, SUB)["state"] == "applied"
    out = next(c for c in got["not_sent"] if c["proposal_id"] == pid)
    assert out["why"] == HELD_SINCE_TEXT + HELD + " It is still waiting."
    assert _qty(stack.ark, "Delver of Secrets") == before
    assert h.db.get_proposal(pid, SUB)["state"] == "pending"


async def test_apply_all_from_the_proposal_card_leaves_out_a_change_held_since(stack: Stack) -> None:
    h, a, b, theirs, pid, other = await _replacement(stack)
    res = await call(h, a, "get_proposal", {"proposal_id": other})
    card = structured(res)
    lines = card["deck_waiting"]["set_lines"]
    assert {x["proposal_id"] for x in lines} == {pid, other}
    s = res["_meta"][APPROVAL_SET_META_KEY]
    shown = {x["proposal_id"]: {"status": x.get("status") or ""} for x in lines}
    await _apply_theirs(h, b, theirs, pid)
    got = structured(
        await call(h, a, "confirm_waiting", {"proposal_ids": s["ids"], "approval": s["code"], "shown": shown})
    )
    assert [c["proposal_id"] for c in got["applied"]] == [other], got
    assert any(c["proposal_id"] == pid and HELD in c["why"] for c in got["not_sent"]), got
    assert h.db.get_proposal(pid, SUB)["state"] == "pending"


async def test_apply_all_with_a_shown_hold_still_applies_it(stack: Stack) -> None:
    """A change the list showed as held is applied by the member's press, as before."""
    h, a, b, theirs, pid, other = await _replacement(stack)
    await _apply_theirs(h, b, theirs, pid)
    data, meta = await _waiting_card(h, a)
    s = meta[APPROVAL_SET_META_KEY]
    shown = {
        x["proposal_id"]: {"status": x.get("status") or "", "status_at": x.get("status_at")}
        for x in data["waiting"]
    }
    assert shown[pid]["status"] == "held" and isinstance(shown[pid]["status_at"], int)
    before = _qty(stack.ark, "Delver of Secrets")
    got = structured(
        await call(h, a, "confirm_waiting", {"proposal_ids": s["ids"], "approval": s["code"], "shown": shown})
    )
    assert sorted(c["proposal_id"] for c in got["applied"]) == sorted([pid, other]), got
    assert _qty(stack.ark, "Delver of Secrets") == before + 1


async def test_a_malformed_shown_map_is_dropped(stack: Stack) -> None:
    """Only a map of the set's own changes, in the expected shape, counts; anything else is
    treated as if the card sent none."""
    from mtg_gateway.tools_decks import _shown_map

    assert _shown_map(["held"], ["p1"]) == {}
    assert _shown_map({"p1": "held", "p9": {"status": "held"}}, ["p1"]) == {}
    assert _shown_map({"p1": {"status": "held" * 10, "status_at": True}}, ["p1"]) == {
        "p1": {"status": ("held" * 10)[:20], "status_at": None}
    }
    assert _shown_map({"p1": {"status": None, "status_at": 5}}, ["p1"]) == {
        "p1": {"status": "", "status_at": 5}
    }


async def test_the_review_page_refuses_a_change_held_after_it_was_opened(stack: Stack) -> None:
    h, a, b, theirs, pid, _ = await _replacement(stack)
    br = Browser(h)
    try:
        await br.login()
        url = f"/proposals/{pid}"
        form = await br.review_form(url)
        assert form["seen_status"] == ""
        await _apply_theirs(h, b, theirs, pid)
        before = _qty(stack.ark, "Delver of Secrets")
        r = await br.http.post(url, data={**form, "action": "apply"})
        assert r.status_code == 303 and r.headers["location"] == f"{url}?err=now_held", r.text
        assert _qty(stack.ark, "Delver of Secrets") == before
        assert h.db.get_proposal(pid, SUB)["state"] == "pending"
        page = (await br.http.get(r.headers["location"])).text
        assert (
            "This change is held for a reason that came up after you opened this page, so nothing was sent."
            in page
        )
        assert f"<p class='ui-why pr-held'>{HELD}</p>" in page  # under the Held label
        # the Held row says why; the row after it does not say it again
        assert "<p class='ui-why pr-waits'>It needs your press.</p>" in page, page
        assert "This one would replace" not in page
        # the page now shows the hold: the member's press applies it
        form = await br.review_form(url)
        assert form["seen_status"] == "held"
        r = await br.http.post(url, data={**form, "action": "apply"})
        assert r.headers["location"] == f"{url}?ok=applied", r.headers
        assert _qty(stack.ark, "Delver of Secrets") == before + 1
    finally:
        await br.aclose()


async def test_the_proposals_confirm_leaves_out_a_change_held_after_it_was_drawn(stack: Stack) -> None:
    h, a, b, theirs, pid, other = await _replacement(stack)
    br = Browser(h)
    try:
        await br.login()
        csrf = await br.csrf("/proposals")
        r = await br.http.post(
            "/proposals/select",
            content=f"csrf={csrf}&action=apply&pick={pid}&pick={other}&v_{pid}=1&v_{other}=1&back=/proposals",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200, r.text
        confirm = r.text
        hidden = dict(re.findall(r"name='((?:picks|[vst]_[A-Za-z0-9_-]+))' value='([^']*)'", confirm))
        assert hidden[f"s_{pid}"] == "" and hidden[f"s_{other}"] == ""
        await _apply_theirs(h, b, theirs, pid)
        before = _qty(stack.ark, "Delver of Secrets")
        r = await br.http.post(
            "/proposals/apply",
            data={
                **hidden,
                "csrf": csrf,
                "action": "apply",
                "shown": aged_stamp(h.settings, f"select:{SUB}"),
                "back": "/proposals",
            },
        )
        assert r.status_code == 303 and r.headers["location"].startswith("/proposals/applied/"), r.text
        result = (await br.http.get(r.headers["location"])).text
        assert HELD_SINCE_TEXT + HELD in result, result
        assert h.db.get_proposal(other, SUB)["state"] == "applied"
        assert h.db.get_proposal(pid, SUB)["state"] == "pending"
        assert _qty(stack.ark, "Delver of Secrets") == before
    finally:
        await br.aclose()
