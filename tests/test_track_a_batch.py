"""Release 0.7.29, approvals and proposals: a selection of waiting changes applied with one press
(PROPOSALS-FLEXIBLE-FLOWS.md §4 B-G; G16, Q27h, Q27i, Q134 A8, A9) and its Undo."""

from __future__ import annotations

from mtg_gateway import batch
from mtg_gateway.decks import BROWSER_CLIENT, current_client

from .conftest import Stack
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import call, linked_user, structured

SUB = "user-1"


def _qty(ark, name: str, deck: int = 42) -> int:
    return sum(
        c["quantity"]
        for c in ark.decks[deck]["cards"]
        if c["card"]["oracleCard"]["name"].split(" // ")[0] == name
    )


async def _propose(h, token: str, changes: list, deck: str = "42", **more: object) -> str:
    p = structured(
        await call(h, token, "propose_deck_changes", {"deck_id": deck, "changes": changes, **more})
    )
    assert p["ok"] and p["state"] == "pending", p
    return p["proposal_id"]


def _forest(n: int) -> list[dict]:
    return [{"action": "set_quantity", "card_name": "Forest", "quantity": n}]


def _add(name: str) -> list[dict]:
    return [{"action": "add", "card_name": name}]


async def _apply(h, ids: list[str], versions: dict | None = None) -> dict:
    """The member's one press on Apply (the Proposals page runs as the browser)."""
    decks = h.app.state.gateway.decks
    reset = current_client.set(BROWSER_CLIENT)
    try:
        run = decks.apply_selection(SUB, ids, via="browser", versions=versions)
    finally:
        current_client.reset(reset)
    await decks.batches.wait(run["batch_id"], 60)
    assert run["state"] == "done", run
    return run


async def test_a_selection_runs_oldest_first_with_one_snapshot_and_one_backup(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    ids = [await _propose(h, token, _add(n)) for n in ("Arcane Signet", "Swamp", "Opt")]
    before = set(ark.decks)
    run = await _apply(h, ids[::-1])  # checked newest first: still applied oldest first
    assert [a["proposal_id"] for a in run["applied"]] == ids, [
        x["why"] for x in run["not_sent"] + run["failed"]
    ]
    assert run["not_sent"] == [] and run["failed"] == []
    snaps = {h.db.get_proposal(i, SUB)["snapshot_id"] for i in ids}
    assert len(snaps) == 1 and None not in snaps  # one snapshot per deck per selection (G16)
    assert len(set(ark.decks) - before) == 1  # and one backup copy on Archidekt
    assert run["undo"] == [
        {
            "deck_id": "42",
            "deck_name": run["undo"][0]["deck_name"],
            "snapshot_id": snaps.pop(),
            "applied": ids,
            "after_at": run["undo"][0]["after_at"],
        }
    ]
    assert batch.summary_text(run) == "Applied 3."


async def test_a_change_that_no_longer_fits_is_skipped_and_the_rest_go_on(stack: Stack) -> None:
    """Q27h "Skip it": #2 was made against 15 Forests; after #1 it would do something else."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    one = await _propose(h, token, _forest(14))
    two = await _propose(h, token, _forest(13))
    three = await _propose(h, token, _add("Opt"))
    run = await _apply(h, [one, two, three])
    assert [a["proposal_id"] for a in run["applied"]] == [one, three], run
    assert [a["proposal_id"] for a in run["not_sent"]] == [two]
    assert run["not_sent"][0]["why"].startswith("Not applied: The deck changed. Reviewed: 15 -> 13 Forest")
    row = h.db.get_proposal(two, SUB)
    assert row["state"] == "pending" and row["status"] == "needs_look"
    assert _qty(ark, "Forest") == 14 and _qty(ark, "Opt") == 1
    assert batch.summary_text(run) == "Applied 2. 1 not sent."


async def test_the_confirm_lists_the_order_and_what_stays_waiting(stack: Stack) -> None:
    """Q27i "Note only": a newer change may go first; the confirm says the older one stays."""
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    old = await _propose(h, token, _add("Opt"))
    a = await _propose(h, token, _add("Swamp"))
    b = await _propose(h, token, _add("Arcane Signet"))
    plan = h.app.state.gateway.decks.plan_selection(SUB, [b, a])
    assert [d["deck_id"] for d in plan["decks"]] == ["42"]
    deck = plan["decks"][0]
    assert [c["proposal_id"] for c in deck["changes"]] == [a, b]
    assert [c["board_no"] for c in deck["changes"]] == [2, 3]
    assert deck["older_waiting"] == 1 and deck["older_text"] == "1 older change to this deck stays waiting."
    assert plan["count"] == 2 and plan["rows"] == 2
    assert deck["changes"][0]["summary"] == "+1 Swamp"
    assert old not in [c["proposal_id"] for c in deck["changes"]]


async def test_a_restore_and_a_deck_deletion_never_run_with_other_changes(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    first = await _propose(h, token, _add("Opt"))
    await _apply(h, [first])
    snap = h.db.get_proposal(first, SUB)["snapshot_id"]
    restore = structured(await call(h, token, "propose_restore_snapshot", {"snapshot_id": snap}))[
        "proposal_id"
    ]
    edit = await _propose(h, token, _add("Delver of Secrets"))
    gone = structured(await call(h, token, "propose_delete_deck", {"deck_id": "42"}))["proposal_id"]
    plan = h.app.state.gateway.decks.plan_selection(SUB, [restore, edit, gone])
    assert [c["proposal_id"] for c in plan["decks"][0]["changes"]] == [edit]
    whys = {s["proposal_id"]: s["why"] for s in plan["skipped"]}
    assert whys == {restore: batch.RESTORE_ALONE, gone: batch.DELETE_ALONE}
    run = await _apply(h, [restore, edit, gone])
    assert [a["proposal_id"] for a in run["applied"]] == [edit]
    assert ark.deleted == [] and h.db.get_proposal(restore, SUB)["state"] == "pending"
    # the deck changed since the restore was made, so it would now do more: it waits for a look
    run = await _apply(h, [restore])
    assert run["applied"] == [] and h.db.get_proposal(restore, SUB)["status"] == "needs_look", run
    # alone, a restore made against the deck as it is runs as one selection
    again = structured(await call(h, token, "propose_restore_snapshot", {"snapshot_id": snap}))
    run = await _apply(h, [again["proposal_id"]])
    assert again["proposal_id"] != restore
    assert [a["proposal_id"] for a in run["applied"]] == [again["proposal_id"]], run["not_sent"]


async def test_a_change_waits_for_the_change_it_depends_on(stack: Stack) -> None:
    """A9: a dependent waits while what it depends on is still waiting or was not sent."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    base = await _propose(h, token, _add("Opt"))
    top = await _propose(h, token, _forest(14), depends_on=[base])
    run = await _apply(h, [top])
    assert run["applied"] == [] and run["not_sent"][0]["why"] == batch.waits_for_text(1), run
    assert h.db.get_proposal(top, SUB)["state"] == "pending"
    # both selected: the one it depends on goes first, then it
    run = await _apply(h, [top, base])
    assert [a["proposal_id"] for a in run["applied"]] == [base, top], run
    assert _qty(ark, "Forest") == 14


async def test_a_dependent_of_a_change_not_sent_in_the_selection_waits_too(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    base = await _propose(h, token, _forest(14))
    top = await _propose(h, token, _add("Opt"), depends_on=[base])
    forest = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Forest")
    forest["quantity"] = 12
    run = await _apply(h, [base, top])
    assert [a["proposal_id"] for a in run["not_sent"]] == [base, top], run
    assert run["not_sent"][1]["why"] == batch.waits_for_text(1)
    assert _qty(ark, "Opt") == 0


async def test_the_member_may_apply_a_held_change_and_the_confirm_says_why(stack: Stack) -> None:
    """A8: holds stop assistants, never the member; the confirm names the hold."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    base = await _propose(h, token, _add("Opt"))
    top = await _propose(h, token, _add("Delver of Secrets"), depends_on=[base])
    assert structured(await call(h, token, "reject_proposal", {"proposal_id": base}))["ok"]
    assert h.db.get_proposal(top, SUB)["status"] == "held"
    plan = h.app.state.gateway.decks.plan_selection(SUB, [top])
    assert plan["decks"][0]["changes"][0]["held"] == "Held: it depends on #1, which you rejected."
    run = await _apply(h, [top])
    assert [a["proposal_id"] for a in run["applied"]] == [top], run
    assert _qty(ark, "Delver of Secrets") == 1


async def test_a_change_amended_after_it_was_seen_is_not_sent(stack: Stack) -> None:
    """G16: the selection carries the version the member saw."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    pid = await _propose(h, token, _add("Opt"))
    out = structured(
        await call(
            h,
            token,
            "amend_proposal",
            {"proposal_id": pid, "version": 1, "changes": _add("Delver of Secrets")},
        )
    )
    assert out["ok"], out
    run = await _apply(h, [pid], versions={pid: 1})
    why = run["not_sent"][0]["why"]
    assert run["applied"] == [] and why.startswith("There is a newer version of this change"), run
    assert _qty(ark, "Opt") == 0 and _qty(ark, "Delver of Secrets") == 0


async def test_undo_puts_the_deck_back_and_warns_about_later_changes(stack: Stack) -> None:
    """Flow G: Undo restores the snapshot taken before the selection; it warns once when that
    would also revert a later change."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    decks = h.app.state.gateway.decks
    ids = [await _propose(h, token, _add(n)) for n in ("Opt", "Delver of Secrets")]
    run = await _apply(h, ids)
    undo = run["undo"][0]
    look = await decks.undo_preview(
        SUB, undo["snapshot_id"], applied=undo["applied"], after_at=undo["after_at"]
    )
    assert look["warning"] == "" and look["later"] == 0 and not look["outside"], look
    assert {r["name"].split(" // ")[0] for r in look["rows"] if r.get("name")} >= {"Opt", "Delver of Secrets"}
    # an edit made on Archidekt afterwards
    ark.decks[42]["cards"][0]["quantity"] += 1
    look = await decks.undo_preview(
        SUB, undo["snapshot_id"], applied=undo["applied"], after_at=undo["after_at"]
    )
    assert look["warning"] == "This also reverts the edits made to this deck on Archidekt since.", look
    # a later change through the gateway
    later = await _propose(h, token, _add("Swamp"))
    await _apply(h, [later])
    look = await decks.undo_preview(
        SUB, undo["snapshot_id"], applied=undo["applied"], after_at=undo["after_at"]
    )
    assert look["warning"] == "This also reverts 1 later change to this deck.", look
    reset = current_client.set(BROWSER_CLIENT)
    try:
        done = await decks.undo(SUB, undo["snapshot_id"])
    finally:
        current_client.reset(reset)
    assert done["state"] == "applied", done
    assert _qty(ark, "Opt") == 0 and _qty(ark, "Delver of Secrets") == 0 and _qty(ark, "Swamp") == 0


async def test_a_selection_needs_writes_and_something_to_apply(stack: Stack) -> None:
    import dataclasses

    import pytest

    from mtg_gateway.decks import DeckError

    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    decks = h.app.state.gateway.decks
    pid = await _propose(h, token, _add("Opt"))
    assert structured(await call(h, token, "reject_proposal", {"proposal_id": pid}))["ok"]
    with pytest.raises(DeckError) as nothing:
        decks.apply_selection(SUB, [pid], via="browser")
    assert nothing.value.kind == "invalid"
    decks.settings = dataclasses.replace(decks.settings, writes_enabled=False)
    with pytest.raises(DeckError) as off:
        decks.apply_selection(SUB, [pid], via="browser")
    assert off.value.kind == "writes_disabled"
