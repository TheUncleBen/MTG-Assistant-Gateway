"""An approval mode's row limit counts what the assistant already applied without a press.

Semi lets an assistant apply an edit of at most 5 rows, Full auto one of at most 20. Judged one
proposal at a time, an assistant could split a 20-row edit into four 5-row proposals and apply all
of it with no press from the member. The rows an assistant applied by itself to the same deck in
the last 24 hours now count toward the limit; a member's own press does not."""

from __future__ import annotations

from .conftest import Stack, StackOptions, stack_with
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import call, linked_user, structured

stack_options = stack_with(StackOptions(settings={"writes_enabled": True}))
NAMES = ["Arcane Signet", "Rampant Growth", "Sol Ring", "Forest", "Island"]


async def _propose(h, token, qty: int, names=NAMES) -> dict:  # noqa: ANN001
    changes = [{"action": "add", "card_name": n, "quantity": qty} for n in names]
    p = structured(
        await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes, "separate": True})
    )
    assert p["ok"], p
    return p


async def test_semi_split_edit_waits_for_the_member(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    first = await _propose(h, token, 1)
    assert first["assistant_may_apply"] is True
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": first["proposal_id"]}))
    assert a["state"] == "applied", a
    sent = len(ark.patches)

    second = await _propose(h, token, 2, NAMES[:1])  # one more row: 6 in all, over Semi's 5
    assert second["assistant_may_apply"] is False, second
    refused = structured(await call(h, token, "apply_proposal", {"proposal_id": second["proposal_id"]}))
    assert refused.get("error_code") == "browser_required" or refused.get("error") == "browser_required", (
        refused
    )
    assert "24 hours" in str(refused), refused
    assert len(ark.patches) == sent  # nothing more was written


async def test_rows_within_the_limit_still_apply(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    first = await _propose(h, token, 1, NAMES[:2])
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": first["proposal_id"]}))
    assert a["state"] == "applied", a
    second = await _propose(h, token, 2, NAMES[2:5])  # 2 + 3 = 5: still within Semi's limit
    assert second["assistant_may_apply"] is True, second


async def test_old_applies_no_longer_count(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    first = await _propose(h, token, 1)
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": first["proposal_id"]}))
    assert a["state"] == "applied", a
    db = stack.h.db
    with db._lock:  # the apply happened 25 hours ago
        db._conn.execute("UPDATE proposals SET applied_at = applied_at - 90000")
        db._conn.execute("UPDATE audit_log SET at = at - 90000")
        db._conn.commit()
    second = await _propose(h, token, 2, NAMES[:1])
    assert second["assistant_may_apply"] is True, second


async def test_a_package_heading_is_not_a_counted_row(stack: Stack) -> None:
    """The heading row of a package is not a card row: the proposal's own count leaves it out, so
    the count of what was applied before leaves it out too (it made the limit one row smaller)."""
    import json

    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "semi")
    first = await _propose(h, token, 1, NAMES[:2])
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": first["proposal_id"]}))
    assert a["state"] == "applied", a
    db = stack.h.db
    with db._lock:
        raw = db._conn.execute("SELECT rows_json FROM proposals WHERE id = ?", (first["proposal_id"],))
        rows = json.loads(raw.fetchone()[0])
        assert len(rows) == 2, rows
        db._conn.execute(
            "UPDATE proposals SET rows_json = ? WHERE id = ?",
            (json.dumps([{"kind": "package", "name": "Ramp"}, *rows]), first["proposal_id"]),
        )
        db._conn.commit()
    second = await _propose(h, token, 2, NAMES[2:5])  # 2 + 3 = 5, the heading is not a sixth
    assert second["assistant_may_apply"] is True, second
