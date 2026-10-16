"""Release 0.7.29, approvals (review round 1): a second step never lands without its first, and a
combined change keeps what its parts needed. An assistant does not apply a change while one it
depends on still waits (Q27j, A9); a combined change keeps a part's hold and its links, and one
that takes in another app's change waits for the member (G5); a change that depends on one closed
outside the apply paths (a restart mid-apply, expiry) is held (K6; expiry pending a card)."""

from __future__ import annotations

from mtg_gateway.decks import BROWSER_CLIENT, current_client

from .conftest import Stack
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import call, linked_user, structured
from .test_round4_data import _app_token

SUB = "user-1"


def _add(name: str) -> list[dict]:
    return [{"action": "add", "card_name": name}]


async def _propose(h, token: str, changes: list, **more: object) -> dict:
    p = structured(
        await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes, **more})
    )
    assert p["ok"], p
    return p


async def _get(h, token: str, pid: str) -> dict:
    return structured(await call(h, token, "get_proposal", {"proposal_id": pid}))


async def test_full_auto_waits_while_the_first_step_still_waits(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    first = await _propose(h, token, _add("Opt"))
    second = await _propose(h, token, _add("Arcane Signet"), depends_on=[first["proposal_id"]])
    await _set_mode(h, "auto")
    got = await _get(h, token, second["proposal_id"])
    assert got["assistant_may_apply"] is False
    assert f"depends on #{first['board_no']}, which has not been applied yet" in got["risk_reason"]
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": second["proposal_id"]}))
    assert out.get("state") != "applied", out
    assert ark.patches == [] and h.db.get_proposal(second["proposal_id"], SUB)["state"] == "pending"
    # once the first step is applied, the second may go
    assert structured(await call(h, token, "apply_proposal", {"proposal_id": first["proposal_id"]}))["ok"]
    await h.app.state.gateway.decks.wait_applied(first["proposal_id"])
    assert (await _get(h, token, second["proposal_id"]))["assistant_may_apply"] is True


async def test_a_combined_change_keeps_a_parts_hold_and_links(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    base = await _propose(h, token, _add("Opt"))
    top = await _propose(h, token, _add("Arcane Signet"), depends_on=[base["proposal_id"]])
    other = await _propose(h, token, _add("Swamp"))
    assert structured(await call(h, token, "reject_proposal", {"proposal_id": base["proposal_id"]}))["ok"]
    comb = structured(
        await call(
            h, token, "combine_proposals", {"proposal_ids": [top["proposal_id"], other["proposal_id"]]}
        )
    )
    assert comb["ok"], comb
    row = h.db.get_proposal(comb["proposal_id"], SUB)
    assert row["status"] == "held" and row["status_reason"].startswith(f"depends:{base['proposal_id']}:")
    await _set_mode(h, "auto")
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": comb["proposal_id"]}))
    assert out.get("state") != "applied", out


async def test_a_combined_change_with_another_apps_part_waits_for_the_member(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    theirs = await _propose(h, b, _add("Opt"))
    mine = await _propose(h, a, _add("Arcane Signet"))
    comb = structured(
        await call(h, a, "combine_proposals", {"proposal_ids": [theirs["proposal_id"], mine["proposal_id"]]})
    )
    assert comb["ok"], comb
    await _set_mode(h, "auto")
    got = await _get(h, a, comb["proposal_id"])
    assert got["assistant_may_apply"] is False
    assert "combines or replaces a change another app made" in got["risk_reason"]
    out = structured(await call(h, a, "apply_proposal", {"proposal_id": comb["proposal_id"]}))
    assert out.get("state") != "applied" and ark.patches == [], out
    # the same app combining only its own changes is not stopped by this
    one = await _propose(h, a, _add("Swamp"))
    two = await _propose(h, a, _add("Island"))
    own = structured(
        await call(h, a, "combine_proposals", {"proposal_ids": [one["proposal_id"], two["proposal_id"]]})
    )
    assert "another app" not in (await _get(h, a, own["proposal_id"])).get("risk_reason", "")


async def test_a_restart_mid_apply_holds_the_dependents(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    first = await _propose(h, token, _add("Opt"))
    second = await _propose(h, token, _add("Arcane Signet"), depends_on=[first["proposal_id"]])
    assert h.db.claim_proposal(first["proposal_id"], SUB)  # an apply under way...
    assert h.db.fail_interrupted_applies() == 1  # ...and the gateway restarts
    got = await _get(h, token, second["proposal_id"])
    assert got["status"] == "held", got
    assert got["status_reason"] == f"It depends on #{first['board_no']}, which failed.", got
    board = h.db.pending_board(SUB, "42")
    assert [r["status"] for r in board if r["id"] == second["proposal_id"]] == ["held"]


async def test_an_expired_first_step_holds_its_dependents_and_does_not_block_the_member(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    first = await _propose(h, token, _add("Opt"))
    second = await _propose(h, token, _add("Arcane Signet"), depends_on=[first["proposal_id"]])
    with h.db.tx() as c:
        c.execute("UPDATE proposals SET expires_at = 1 WHERE id = ?", (first["proposal_id"],))
    # before the prune marks it, and after
    board = h.db.pending_board(SUB, "42")
    assert [r["status_reason"] for r in board] == [f"depends:{first['proposal_id']}:expired"]
    got = await _get(h, token, second["proposal_id"])
    assert got["status_reason"] == f"It depends on #{first['board_no']}, which expired.", got
    await _set_mode(h, "auto")
    assert (await _get(h, token, second["proposal_id"]))["assistant_may_apply"] is False
    # the member may still apply a held change themselves (A8)
    decks = h.app.state.gateway.decks
    reset = current_client.set(BROWSER_CLIENT)
    try:
        run = decks.apply_selection(SUB, [second["proposal_id"]], via="review_page")
    finally:
        current_client.reset(reset)
    await decks.batches.wait(run["batch_id"], 60)
    assert [x["proposal_id"] for x in run["applied"]] == [second["proposal_id"]], run["not_sent"]
    assert len(ark.patches) == 1


async def test_a_change_that_replaces_another_apps_change_waits_for_the_member(stack: Stack) -> None:
    """Q27m and G5 (the Q142 check): a new change that replaces another app's waiting change is
    not applied by an assistant in Full auto; one that replaces only its own app's change is."""
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    await _set_mode(h, "auto")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    await _set_mode(h, "manual")
    theirs = await _propose(h, b, _add("Opt"))
    await _set_mode(h, "auto")
    new = await _propose(h, a, _add("Delver of Secrets"), replaces=[theirs["proposal_id"]])
    # ChatGPT's change is not closed by Claude: it waits until the member's press
    assert "replaced" not in new and new.get("replaces_when_applied") == [theirs["proposal_id"]], new
    assert h.db.get_proposal(theirs["proposal_id"], SUB)["state"] == "pending"
    assert new["assistant_may_apply"] is False
    assert "combines or replaces a change another app made" in new["risk_reason"]
    out = structured(await call(h, a, "apply_proposal", {"proposal_id": new["proposal_id"]}))
    assert out.get("state") != "applied" and ark.patches == [], out
    assert h.db.get_proposal(theirs["proposal_id"], SUB)["state"] == "pending"
    # the member's press applies it, and only then is ChatGPT's change closed as replaced
    decks = h.app.state.gateway.decks
    reset = current_client.set(BROWSER_CLIENT)
    try:
        run = decks.apply_selection(SUB, [new["proposal_id"]], via="review_page")
    finally:
        current_client.reset(reset)
    await decks.batches.wait(run["batch_id"], 60)
    assert [x["proposal_id"] for x in run["applied"]] == [new["proposal_id"]], [
        f.get("why") for f in run["failed"]
    ]
    gone = h.db.get_proposal(theirs["proposal_id"], SUB)
    assert gone["state"] == "replaced" and gone["status_reason"] == f"replaced by #{new['board_no']}", gone
    # replacing its own app's change is not stopped by this
    await _set_mode(h, "manual")
    own = await _propose(h, a, _add("Island"))
    await _set_mode(h, "auto")
    again = await _propose(h, a, _add("Swamp"), replaces=[own["proposal_id"]])
    assert "another app" not in again.get("risk_reason", ""), again
    assert again["replaced"] == [own["proposal_id"]] and "replaces_when_applied" not in again


async def test_rejecting_a_replacement_of_another_apps_change_leaves_that_change_waiting(
    stack: Stack,
) -> None:
    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    theirs = await _propose(h, b, _add("Opt"))
    new = await _propose(h, a, _add("Ponder"), replaces=[theirs["proposal_id"]])
    assert structured(await call(h, a, "reject_proposal", {"proposal_id": new["proposal_id"]}))["ok"]
    row = h.db.get_proposal(theirs["proposal_id"], SUB)
    assert row["state"] == "pending" and row["status"] is None, row


async def test_a_change_and_its_replacement_ticked_together_are_refused_by_name(stack: Stack) -> None:
    """The Q142 check: another app's change waits until its replacement is applied, so both can
    be ticked; Apply all never applies both, and never picks one for the member."""
    from mtg_gateway.decks import DeckError

    from .test_decks_and_proxy import Browser

    h, ark = stack.h, stack.ark
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    theirs = await _propose(h, b, _add("Opt"))
    new = await _propose(h, a, _add("Delver of Secrets"), replaces=[theirs["proposal_id"]])
    pair = [theirs["proposal_id"], new["proposal_id"]]
    decks = h.app.state.gateway.decks
    want = (
        f"#{new['board_no']} replaces #{theirs['board_no']} on {theirs['deck_name']}, so they can't "
        "be applied together. Nothing was sent. Untick one of them, then apply again."
    )
    reset = current_client.set(BROWSER_CLIENT)
    try:
        try:
            decks.apply_selection(SUB, pair, via="review_page")
            raise AssertionError("both were started")
        except DeckError as exc:
            assert exc.kind == "pick_one" and str(exc) == want, str(exc)
    finally:
        current_client.reset(reset)
    # the Proposals page names the pair and sends nothing
    br = Browser(h)
    try:
        await br.login()
        csrf = await br.csrf("/proposals")
        form = {"csrf": csrf, "action": "apply", "back": "/proposals"}
        versions = {f"v_{p}": "1" for p in pair}
        r = await br.http.post("/proposals/select", data={**form, **versions, "pick": pair})
        assert r.status_code == 409 and "Untick one of them" in r.text, r.text
        r = await br.http.post(
            "/proposals/apply", data={**form, **versions, "picks": ",".join(pair), "shown": "1.x"}
        )
        assert r.status_code == 409 and "Untick one of them" in r.text
    finally:
        await br.aclose()
    assert ark.patches == []
    assert all(h.db.get_proposal(p, SUB)["state"] == "pending" for p in pair)
    # either one alone goes to the confirm
    plan = decks.plan_selection(SUB, [theirs["proposal_id"]])
    assert plan["count"] == 1
