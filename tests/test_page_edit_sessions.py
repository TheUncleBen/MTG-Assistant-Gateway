"""Edits on the deck page come in runs of taps (independent review of 0.7.46: every Quick add,
+ or − took a snapshot of its own, so 25 taps pushed the snapshot from before an assistant's big
change out of the newest 25 kept). A run of the member's own page edits to one deck, each within
db.PAGE_SESSION_SECONDS of the last with nothing else changing the deck, shares the snapshot from
before its first edit and is one History entry; page snapshots are counted apart from all others,
so taps never push out an assistant's; Undo keeps working through the run."""

from __future__ import annotations

from mtg_gateway import db as db_mod

from .conftest import COLLECTION, Stack, stack_with
from .test_browse_collection import api, linked
from .test_round4_data import _app_token

stack_options = stack_with(COLLECTION)


def _qty(st: Stack, name: str) -> int:
    return sum(
        int(c["quantity"])
        for c in st.ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == name and c["categories"] != ["Maybeboard"]
    )


async def _edit(b, card: str, qty: int) -> dict:
    r = await api(
        b,
        "POST",
        "/api/v1/decks/42/edit",
        {"changes": [{"action": "set_quantity", "card_name": card, "quantity": qty}]},
    )
    assert r.status_code == 201 and r.json()["applied"] is True, r.text
    return r.json()


def _snapshots(st: Stack) -> list[dict]:
    conn = st.gw.db._conn
    return [dict(r) for r in conn.execute("SELECT id, proposal_id FROM snapshots WHERE deck_id = '42'")]


def _age(st: Stack, seconds: int) -> None:
    """Every applied proposal and snapshot so far as if made ``seconds`` earlier."""
    conn = st.gw.db._conn
    with st.gw.db._lock:
        conn.execute(
            "UPDATE proposals SET applied_at = applied_at - ?, created_at = created_at - ?",
            (seconds, seconds),
        )
        conn.execute("UPDATE snapshots SET taken_at = taken_at - ?", (seconds,))
        conn.commit()


async def _assistant_edit(st: Stack, b, card: str, qty: int) -> str:
    token, _cid = await _app_token(st.h, "assistant-test")
    r = await st.h.http.post(
        "/api/v1/proposals",
        json={
            "kind": "edit",
            "deck_id": "42",
            "changes": [{"action": "set_quantity", "card_name": card, "quantity": qty}],
        },
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 201, r.text
    pid = r.json()["proposal_id"]
    r = await api(b, "POST", f"/api/v1/proposals/{pid}/apply", {"version": 1})
    assert r.status_code == 200 and r.json()["state"] == "applied", r.text
    return r.json()["snapshot_id"] if r.json().get("snapshot_id") else r.json()["result"]["snapshot_id"]


async def test_a_run_of_page_edits_shares_one_snapshot_and_is_one_history_entry(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        first = await _edit(b, "Cultivate", 2)
        second = await _edit(b, "Cultivate", 3)
        third = await _edit(b, "Forest", _qty(stack, "Forest") + 1)
        assert first["snapshot_id"] == second["snapshot_id"] == third["snapshot_id"]
        assert len(_snapshots(stack)) == 1
        # the one snapshot is the deck as it stood before the first edit
        snap = stack.gw.db.get_snapshot(
            first["snapshot_id"], stack.gw.db._one("SELECT owner_sub FROM snapshots")[0]
        )
        names = [
            c["card"]["oracleCard"]["name"] for c in snap["deck"]["cards"] for _ in range(int(c["quantity"]))
        ]
        assert names.count("Cultivate") == 1, names.count("Cultivate")
        r = await b.http.get("/history?type=changes")
        assert r.status_code == 200
        assert r.text.count("edits on the deck page") == 1, r.text
        assert "3 edits on the deck page" in r.text
        owner = stack.gw.db._one("SELECT owner_sub FROM snapshots")[0]
        assert stack.gw.db.history_counts(owner)["changes"] == 1  # the chip and Load more count it once
    finally:
        await b.aclose()


async def test_a_pause_an_assistant_or_a_change_on_archidekt_starts_a_new_run(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        one = (await _edit(b, "Cultivate", 2))["snapshot_id"]
        _age(stack, db_mod.PAGE_SESSION_SECONDS + 5)
        two = (await _edit(b, "Cultivate", 3))["snapshot_id"]
        assert two != one  # a pause past the window
        mid = await _assistant_edit(stack, b, "Cultivate", 4)
        three = (await _edit(b, "Cultivate", 5))["snapshot_id"]
        assert three not in (two, mid)  # an assistant's change between: its own snapshot, then a new run
        # a change made on Archidekt itself between two taps
        row = next(c for c in stack.ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Forest")
        row["quantity"] = int(row["quantity"]) + 2
        stack.ark.decks[42]["updatedAt"] = "2026-10-09T00:00:00Z"
        four = (await _edit(b, "Cultivate", 6))["snapshot_id"]
        assert four != three
        assert len(_snapshots(stack)) == 5
    finally:
        await b.aclose()


async def test_page_edits_never_push_out_the_snapshot_before_an_assistants_change(stack: Stack) -> None:  # noqa: F811
    stack.gw.db.snapshots_source = lambda: 5  # the Settings page's "Restore points kept per deck"
    b = await linked(stack)
    try:
        before_big = await _assistant_edit(stack, b, "Cultivate", 4)
        for n in range(8):  # eight sittings on the deck page, each past the last one's window
            _age(stack, db_mod.PAGE_SESSION_SECONDS + 5)
            await _edit(b, "Cultivate", 5 + 2 * n)
            await _edit(b, "Cultivate", 6 + 2 * n)
        stack.gw.db.purge_expired()
        ids = {s["id"] for s in _snapshots(stack)}
        assert before_big in ids
        assert len(ids) == 1 + 5, len(ids)  # the assistant's, and the newest 5 sittings
    finally:
        await b.aclose()


async def test_undo_works_through_a_run(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        was = _qty(stack, "Cultivate")
        a = await _edit(b, "Cultivate", was + 2)
        # the page's Undo sends the inverse change, as one more edit of the run
        u = await _edit(b, "Cultivate", was)
        assert _qty(stack, "Cultivate") == was
        assert u["snapshot_id"] == a["snapshot_id"]
        assert len(_snapshots(stack)) == 1
    finally:
        await b.aclose()
