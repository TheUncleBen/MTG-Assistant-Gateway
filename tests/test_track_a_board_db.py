"""Release 0.7.29, approvals and proposals: the database side of the board. A claim that checks the
version (G16), the re-check record (updated, needs a look), holds from the notes a note depends on
(Q27j), exact duplicates (Q27k) and the board number of a note that comes back (A1)."""

from __future__ import annotations

import time
from pathlib import Path

from mtg_gateway.db import Database

WEEK = 7 * 86400


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "x.sqlite")
    db.upsert_user("m1", email=None, name=None, preferred_username="m1", groups=[])
    db.save_link("m1", username="m1", user_id="78", secret_enc="x")
    return db


def _proposal(db: Database, pid: str, *, deck: str = "1", client: str = "app-1", **more: object) -> bool:
    return db.save_proposal(
        {
            "id": pid,
            "owner_sub": "m1",
            "deck_id": deck,
            "deck_name": "D",
            "baseline_fingerprint": "fp",
            "changes": [{"op": "add", "name": pid}],
            "diff_text": f"+1 {pid}",
            "expires_at": int(time.time()) + WEEK,
            "created_by_client": client,
            **more,
        }
    )


def _state(db: Database, pid: str, state: str) -> None:
    with db.tx() as c:
        c.execute("UPDATE proposals SET state = ? WHERE id = ?", (state, pid))


def test_a_claim_with_a_version_refuses_a_note_amended_since(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        assert _proposal(db, "a")
        assert db.amend_proposal(
            "a", "m1", expect_version=1, changes=[], rows=None, diff_text="v2", by_client="x"
        )
        assert not db.claim_proposal("a", "m1", expect_version=1)
        assert db.get_proposal("a", "m1")["state"] == "pending"
        assert db.claim_proposal("a", "m1", expect_version=2)
        assert not db.claim_proposal("a", "m1", expect_version=2)  # already being applied
    finally:
        db.close()


def test_a_recheck_moves_the_baseline_only_when_the_note_still_fits(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        assert _proposal(db, "a")
        assert db.rebase_proposal("a", "m1", was="fp", fingerprint="fp2", status="updated")
        p = db.get_proposal("a", "m1")
        assert (p["baseline_fingerprint"], p["status"]) == ("fp2", "updated")
        # a check made against the old baseline (a race) changes nothing
        assert not db.rebase_proposal("a", "m1", was="fp", fingerprint="fp3", status="needs_look")
        assert db.rebase_proposal("a", "m1", was="fp2", fingerprint="fp3", status="needs_look", reason="r")
        p = db.get_proposal("a", "m1")
        assert (p["baseline_fingerprint"], p["status"], p["status_reason"]) == ("fp2", "needs_look", "r")
        # a version check, and a hold is never overwritten by a re-check
        assert not db.rebase_proposal("a", "m1", was="fp2", fingerprint="fp4", status="updated", version=2)
        assert db.set_hold("a", "m1", "depends:x:rejected")
        assert not db.rebase_proposal("a", "m1", was="fp2", fingerprint="fp4", status="updated")
        assert db.get_proposal("a", "m1")["status"] == "held"
    finally:
        db.close()


def test_holds_follow_the_notes_a_note_depends_on(tmp_path: Path) -> None:
    """K6: an amend keeps a hold while the note it depends on is rejected, withdrawn or failed, and
    a note that comes back while that note is still one of those is held again."""
    db = _db(tmp_path)
    try:
        for pid in ("base", "top"):
            assert _proposal(db, pid)
        assert db.link_proposals("top", "m1", "depends_on", "base")
        assert db.dependents("m1", "base") == ["top"]
        for state in ("withdrawn", "failed", "rejected"):
            _state(db, "base", state)
            _state(db, "top", "pending")
            assert db.set_hold("top", "m1", f"depends:base:{state}")
            v = db.get_proposal("top", "m1")["version"]
            assert db.amend_proposal(
                "top", "m1", expect_version=v, changes=[], rows=None, diff_text="n", by_client="a"
            )
            assert db.get_proposal("top", "m1")["status"] == "held", state
            # withdrawn and brought back: still held, with the reason naming the note and its state
            assert db.close_proposal("top", "m1", "withdrawn")
            assert db.reopen_proposal("top", "m1")
            p = db.get_proposal("top", "m1")
            assert (p["status"], p["status_reason"]) == ("held", f"depends:base:{state}"), state
        # the note it depends on is waiting again: the amend and the reopen clear the hold
        _state(db, "base", "pending")
        v = db.get_proposal("top", "m1")["version"]
        assert db.amend_proposal(
            "top", "m1", expect_version=v, changes=[], rows=None, diff_text="n", by_client="a"
        )
        assert db.get_proposal("top", "m1")["status"] is None
        assert db.set_hold("top", "m1", "depends:base:rejected")
        assert db.close_proposal("top", "m1", "withdrawn")
        assert db.reopen_proposal("top", "m1")
        assert db.get_proposal("top", "m1")["status"] is None
        # lifting a hold never clears another status
        assert db.set_proposal_status("top", "m1", "needs_look", "r")
        assert not db.set_hold("top", "m1", None)
        assert db.get_proposal("top", "m1")["status"] == "needs_look"
        assert db.dependents("m1", "top") == []
    finally:
        db.close()


def test_only_an_exact_duplicate_from_the_same_app_is_found(tmp_path: Path) -> None:
    db = _db(tmp_path)
    try:
        assert _proposal(db, "a")
        changes = [{"op": "add", "name": "a"}]
        assert db.find_duplicate("m1", deck_id="1", kind="edit", changes=changes, client="app-1") == "a"
        assert db.find_duplicate("m1", deck_id="1", kind="edit", changes=changes, client="app-2") is None
        assert db.find_duplicate("m1", deck_id="2", kind="edit", changes=changes, client="app-1") is None
        assert db.find_duplicate("m1", deck_id="1", kind="details", changes=changes, client="app-1") is None
        assert (
            db.find_duplicate(
                "m1", deck_id="1", kind="edit", changes=[{"op": "add", "name": "b"}], client="app-1"
            )
            is None
        )
        _state(db, "a", "rejected")
        assert db.find_duplicate("m1", deck_id="1", kind="edit", changes=changes, client="app-1") is None
    finally:
        db.close()


def test_a_note_that_comes_back_keeps_its_number_unless_another_took_it(tmp_path: Path) -> None:
    """A1: numbers stay unique on a deck's board."""
    db = _db(tmp_path)
    try:
        for pid in ("a", "b"):
            assert _proposal(db, pid)
        assert db.proposal_board_numbers("m1", ["a", "b"]) == {"a": 1, "b": 2}
        assert db.close_proposal("b", "m1", "withdrawn")
        assert db.reopen_proposal("b", "m1")  # nobody took 2
        assert db.proposal_board_numbers("m1", ["b"]) == {"b": 2}
        assert db.close_proposal("b", "m1", "withdrawn")
        assert db.close_proposal("a", "m1", "withdrawn")
        assert _proposal(db, "c")  # the board was empty, so c is #1
        assert db.proposal_board_numbers("m1", ["c"]) == {"c": 1}
        assert db.reopen_proposal("a", "m1")  # its 1 is taken: next free
        assert db.reopen_proposal("b", "m1")  # its 2 is taken by a now
        nums = db.proposal_board_numbers("m1", ["a", "b", "c"])
        assert nums == {"a": 2, "b": 3, "c": 1}
    finally:
        db.close()
