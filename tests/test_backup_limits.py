"""Backup copies on Archidekt in 0.7.29 (Q12-Q15, QUESTIONS-2 B11-B16, card Q135 items 7, 8 and 10):
an edit shares the deck's newest copy when the deck is unchanged since it or it is under 15
minutes old; copies past "Backup copies kept per deck" are deleted later with their own rule; the
record of how a copy was made lives in the database; a shared copy is never deleted for one edit;
and a deleted copy is forgotten by every snapshot that recorded it."""

from __future__ import annotations

import copy
import json
import time
from pathlib import Path

import pytest

from mtg_gateway import decks as decks_mod
from mtg_gateway.db import Database

from .conftest import Stack
from .test_background_apply import _finished
from .test_backup_delete_later import _patches_429, quick_waits  # noqa: F401
from .test_decks_and_proxy import call, linked_user, structured

SUB = "user-1"
ADDS = ["Arcane Signet", "Rampant Growth", "Opt", "Island", "Swamp", "Forest"]


async def _apply(stack: Stack, token: str, card: str, action: str = "add") -> dict:
    p = structured(
        await call(
            stack.h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": action, "card_name": card}]},
        )
    )
    assert p["ok"], p
    a = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["state"] == "applied", a
    return a


def _svc(stack: Stack):
    return stack.h.app.state.gateway.decks


def _live(stack: Stack):
    return stack.h.app.state.gateway.live


def _due_now(stack: Stack) -> None:
    with stack.h.db.tx() as c:
        c.execute("UPDATE backup_deletes SET due_at = 0")


@pytest.fixture
def no_sharing(monkeypatch) -> None:
    """Every edit past the first makes its own copy (the deck changes each time)."""
    monkeypatch.setattr(decks_mod, "BACKUP_SHARE_SECONDS", 0)


async def test_edits_within_15_minutes_share_one_copy(stack: Stack) -> None:
    token = await linked_user(stack)
    a1 = await _apply(stack, token, "Acidic Slime", "remove")
    a2 = await _apply(stack, token, "Arcane Signet")
    copies = [d for d in stack.ark.decks if d >= 100]
    assert len(copies) == 1 and a2["result"]["backup_deck_id"] == a1["result"]["backup_deck_id"]
    assert a2["result"]["backup_reason"] == "recent"
    assert "made less than 15 minutes earlier" in a2["result"]["backup_shared"]
    # each edit keeps its own snapshot; both record the one copy
    db = stack.h.db
    assert db.backup_users(SUB, a1["result"]["backup_deck_id"]) == 2
    assert a1["snapshot_id"] != a2["snapshot_id"]


@pytest.mark.usefixtures("no_sharing")
async def test_an_unchanged_deck_shares_its_copy_and_a_changed_one_gets_a_new_copy(stack: Stack) -> None:
    token = await linked_user(stack)
    a1 = await _apply(stack, token, "Arcane Signet")
    before = copy.deepcopy(stack.ark.decks[42])
    a2 = await _apply(stack, token, "Opt")  # the deck changed since the first copy: a new one
    assert a2["result"]["backup_deck_id"] != a1["result"]["backup_deck_id"]
    assert not a2["result"].get("backup_reused")
    # The member puts the deck back by hand on Archidekt: it now matches the second copy.
    stack.ark.decks[42] = before
    a3 = await _apply(stack, token, "Island")
    assert a3["result"]["backup_deck_id"] == a2["result"]["backup_deck_id"]
    assert a3["result"]["backup_reason"] == "unchanged"
    assert "the deck is unchanged since" in a3["result"]["backup_shared"]


@pytest.mark.usefixtures("no_sharing")
async def test_a_copy_the_member_changed_is_never_shared(stack: Stack) -> None:
    token = await linked_user(stack)
    before = copy.deepcopy(stack.ark.decks[42])
    a1 = await _apply(stack, token, "Arcane Signet")
    first = int(a1["result"]["backup_deck_id"])
    stack.ark.decks[first]["name"] = "My own deck now"
    stack.ark.decks[42] = before  # the deck matches the copy again; only the copy's change stops it
    a2 = await _apply(stack, token, "Opt")
    assert a2["result"]["backup_deck_id"] != str(first) and not a2["result"].get("backup_reused")


@pytest.mark.usefixtures("no_sharing")
async def test_copies_past_the_limit_are_deleted_later_oldest_first(stack: Stack) -> None:
    """B11 (card Q135 item 7): over-limit copies have their own rule, so a copy whose edit changed
    the deck (every applied edit does) is still deleted once it is past the limit."""
    token = await linked_user(stack)
    _live(stack).set("archidekt_backup_copies", 2, by="admin-1")
    made = [(await _apply(stack, token, c))["result"]["backup_deck_id"] for c in ADDS[:4]]
    assert len(set(made)) == 4
    queued = stack.h.db.queued_backup_deletes()
    assert sorted(j["backup_deck_id"] for j in queued) == sorted(made[:2])
    assert all(j["proposal_id"] is None and j["expect"]["trim"] for j in queued)
    _due_now(stack)
    svc = _svc(stack)
    out = [await svc.delete_one_queued_backup() for _ in range(3)]
    assert [bool(o and o.get("deleted")) for o in out] == [True, True, False]
    assert {int(m) for m in made[:2]} <= set(stack.ark.deleted)
    assert not {int(m) for m in made[2:]} & set(stack.ark.deleted)
    db = stack.h.db
    assert all(db.backup_users(SUB, m) == 0 and db.backup_made(m) is None for m in made[:2])
    assert all(db.backup_users(SUB, m) == 1 for m in made[2:])


@pytest.mark.usefixtures("no_sharing")
async def test_a_queued_trim_within_the_limit_by_then_is_dropped(stack: Stack) -> None:
    token = await linked_user(stack)
    _live(stack).set("archidekt_backup_copies", 1, by="admin-1")
    for c in ADDS[:2]:
        await _apply(stack, token, c)
    assert len(stack.h.db.queued_backup_deletes()) == 1
    _live(stack).set("archidekt_backup_copies", 10, by="admin-1")  # raised meanwhile
    _due_now(stack)
    out = await _svc(stack).delete_one_queued_backup()
    assert out and out.get("within_limit") and stack.ark.deleted == []
    assert stack.h.db.queued_backup_deletes() == []


@pytest.mark.usefixtures("no_sharing")
async def test_a_copy_changed_on_archidekt_is_kept_and_no_longer_counted(stack: Stack) -> None:
    token = await linked_user(stack)
    _live(stack).set("archidekt_backup_copies", 1, by="admin-1")
    first = (await _apply(stack, token, ADDS[0]))["result"]["backup_deck_id"]
    stack.ark.decks[int(first)]["name"] = "Kept by hand"
    await _apply(stack, token, ADDS[1])
    _due_now(stack)
    out = await _svc(stack).delete_one_queued_backup()
    assert out and out.get("error") == "copy_changed" and stack.ark.deleted == []
    assert stack.h.db.backup_made(first)["kept"] is True
    # Not counted any more: the next edit's copy pushes out the second copy, not the kept one.
    second_and_third = [(await _apply(stack, token, c))["result"]["backup_deck_id"] for c in ADDS[2:3]]
    queued = [j["backup_deck_id"] for j in stack.h.db.queued_backup_deletes()]
    assert first not in queued and len(queued) == 1 and queued[0] not in second_and_third


@pytest.mark.usefixtures("no_sharing")
async def test_the_record_of_a_copy_outlives_a_restart(stack: Stack, tmp_path: Path) -> None:
    """B12 (card Q135 item 8): the over-limit delete still checks the copy as made after the
    gateway's memory of it is gone."""
    token = await linked_user(stack)
    _live(stack).set("archidekt_backup_copies", 1, by="admin-1")
    first = (await _apply(stack, token, ADDS[0]))["result"]["backup_deck_id"]
    await _apply(stack, token, ADDS[1])
    svc = _svc(stack)
    svc._backups_made.clear()  # what a restart loses
    record = stack.h.db.backup_made(first)
    assert record and record["name"] == stack.ark.decks[int(first)]["name"]
    _due_now(stack)
    out = await svc.delete_one_queued_backup()
    assert out and out.get("deleted") and int(first) in stack.ark.deleted


@pytest.mark.usefixtures("no_sharing")
async def test_a_copy_from_before_0729_follows_the_legacy_rule(stack: Stack) -> None:
    token = await linked_user(stack)
    _live(stack).set("archidekt_backup_copies", 2, by="admin-1")
    made = [(await _apply(stack, token, c))["result"]["backup_deck_id"] for c in ADDS[:2]]
    db = stack.h.db
    with db.tx() as c:  # as if both were made by 0.7.28, which kept no record
        c.execute("DELETE FROM job_runs WHERE name LIKE 'backup_made:%'")
    # The older copy was edited on Archidekt long after it was made: kept.
    stack.ark.decks[int(made[0])]["updatedAt"] = "2030-01-01T00:00:00Z"
    await _apply(stack, token, ADDS[2])
    await _apply(stack, token, ADDS[3])
    queued = {j["backup_deck_id"]: j for j in db.queued_backup_deletes()}
    assert set(queued) == set(made) and all(j["expect"].get("legacy") for j in queued.values())
    _due_now(stack)
    svc = _svc(stack)
    results = {}
    for _ in range(2):
        out = await svc.delete_one_queued_backup()
        assert out
        results[out.get("deleted") or out.get("given_up")] = out
    assert int(made[1]) in stack.ark.deleted and int(made[0]) not in stack.ark.deleted


@pytest.mark.usefixtures("quick_waits")
async def test_a_shared_copy_is_never_deleted_for_a_failed_edit(stack: Stack) -> None:
    """B16: the second edit shares the first's copy and then stops before it changes anything
    (Archidekt answers every card write with a 429); the copy the first edit relies on stays,
    and no delete of it is queued."""
    token = await linked_user(stack)
    a1 = await _apply(stack, token, ADDS[0])
    shared = a1["result"]["backup_deck_id"]
    p = structured(
        await call(
            stack.h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Opt"}]},
        )
    )
    seen = _patches_429(stack.ark)
    await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    row = await _finished(stack.h, p["proposal_id"])
    assert row["state"] == "pending" and seen["patches"] and row["result"]["backup_reused"], row
    assert "backup_note" not in row["result"]
    assert int(shared) not in stack.ark.deleted and int(shared) in stack.ark.decks
    assert stack.h.db.queued_backup_deletes() == []
    assert stack.h.db.backup_users(SUB, shared) == 2


def test_a_deleted_copy_is_forgotten_by_every_snapshot(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    for sid in ("s1", "s2", "s3"):
        db.save_snapshot(sid, owner_sub=SUB, deck_id="42", proposal_id=None, fingerprint="f", deck={})
    db.set_snapshot_backup("s1", backup_deck_id="500", backup_url="https://archidekt.com/decks/500")
    db.set_snapshot_backup("s2", backup_deck_id="500", backup_url="https://archidekt.com/decks/500")
    db.set_snapshot_backup("s3", backup_deck_id="501", backup_url="https://archidekt.com/decks/501")
    db.record_backup_made("500", {"name": "x"})
    assert db.clear_backup_everywhere(SUB, "500") == 2
    assert db.get_snapshot("s1", SUB)["backup_deck_id"] is None
    assert db.get_snapshot("s2", SUB)["backup_deck_id"] is None
    assert db.get_snapshot("s3", SUB)["backup_deck_id"] == "501"
    assert db.backup_made("500") is None
    assert db.clear_backup_everywhere("someone-else", "501") == 0
    db.close()


def test_pruning_restore_points_keeps_the_ones_a_copy_or_a_queued_delete_needs(tmp_path: Path) -> None:
    """B13 (card Q135 item 9): at the default settings the oldest restore point holding a copy is
    past the 25 kept, so pruning it would leave the copy with no record of how it was made."""
    db = Database(tmp_path / "g.sqlite")
    db.snapshots_per_deck = 3
    now = int(time.time())
    for i in range(8):
        db.save_snapshot(f"s{i}", owner_sub=SUB, deck_id="42", proposal_id=None, fingerprint="f", deck={})
        with db.tx() as c:
            c.execute("UPDATE snapshots SET taken_at = ? WHERE id = ?", (now - 1000 + i, f"s{i}"))
    db.set_snapshot_backup("s0", backup_deck_id="500", backup_url="u")
    db.set_snapshot_backup("s6", backup_deck_id="500", backup_url="u")  # shared later
    db.record_backup_made("500", {"name": "x"})
    db.queue_backup_delete("s1", sub=SUB, backup_deck_id="501", deck_id="42", proposal_id=None, due=0)
    db.purge_expired()
    with db.tx() as c:
        left = {r[0] for r in c.execute("SELECT id FROM snapshots")}
    assert left == {"s0", "s1", "s5", "s6", "s7"}
    # a copy the member changed (kept, no longer counted) holds no snapshot past the newest
    db.record_backup_made("500", {"name": "x", "kept": True})
    db.purge_expired()
    with db.tx() as c:
        left = {r[0] for r in c.execute("SELECT id FROM snapshots")}
    assert left == {"s1", "s5", "s6", "s7"}
    db.close()


@pytest.mark.usefixtures("no_sharing")
async def test_the_deck_itself_is_never_queued(stack: Stack) -> None:
    token = await linked_user(stack)
    _live(stack).set("archidekt_backup_copies", 1, by="admin-1")
    a = await _apply(stack, token, ADDS[0])
    # A snapshot that (wrongly) records the deck itself as its copy is never trimmed.
    stack.h.db.set_snapshot_backup(a["snapshot_id"], backup_deck_id="42", backup_url="u")
    await _apply(stack, token, ADDS[1])
    assert all(j["backup_deck_id"] != "42" for j in stack.h.db.queued_backup_deletes())


async def test_backups_switched_off_on_the_settings_page_make_no_copy(stack: Stack) -> None:
    token = await linked_user(stack)
    _live(stack).set("archidekt_backups", False, by="admin-1")
    a = await _apply(stack, token, ADDS[0])
    assert "backup_deck_id" not in a["result"] and not [d for d in stack.ark.decks if d >= 100]


def test_the_shared_note_reads_as_a_sentence() -> None:
    made = 1_790_000_000
    assert decks_mod.backup_shared_note(made, recent=True).endswith("(made less than 15 minutes earlier).")
    assert json.dumps(decks_mod.backup_shared_note(made, recent=False)).count("UTC") == 1
