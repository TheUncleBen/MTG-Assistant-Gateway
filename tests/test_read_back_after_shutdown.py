"""An apply cut off by a normal shutdown keeps its apply time, as one cut off by a crash does.

The read-back after a restart looks for interrupted applies of the last two days. The tidy
shutdown path used to clear the apply time, so the read-back fell back to the day the proposal
was made and skipped one made more than two days before it was applied."""

from __future__ import annotations

import time
from pathlib import Path

from mtg_gateway.db import Database
from mtg_gateway.decks import READ_BACK_WITHIN_SECONDS


def test_a_cancelled_apply_of_an_older_proposal_is_read_back(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    now = int(time.time())
    for pid in ("cancelled", "crashed"):
        db.save_proposal(
            {
                "id": pid,
                "owner_sub": "u1",
                "deck_id": "42",
                "baseline_fingerprint": "f",
                "changes": [],
                "diff_text": "",
                "expires_at": now + 7 * 86400,
                "rows": [],
                "archidekt_user_id": None,
            }
        )
        with db.tx() as c:  # made three days ago
            c.execute("UPDATE proposals SET created_at = ? WHERE id = ?", (now - 3 * 86400, pid))
        assert db.claim_proposal(pid, "u1")
        db.finish_proposal(pid, state="applying", snapshot_id="snap-" + pid)
        db.note_apply_progress(pid, {"writes_started": True, "sent_entries": 1})
    db.finish_proposal(
        "cancelled",
        state="failed",
        result={"writes_started": True, "sent_entries": 1, "error": "interrupted"},
    )
    db.fail_interrupted_applies()
    back = {r["id"] for r in db.interrupted_to_read_back(now - int(READ_BACK_WITHIN_SECONDS))}
    assert back == {"cancelled", "crashed"}


def test_an_ordinary_failure_still_clears_the_apply_time(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    now = int(time.time())
    db.save_proposal(
        {
            "id": "p",
            "owner_sub": "u1",
            "deck_id": "42",
            "baseline_fingerprint": "f",
            "changes": [],
            "diff_text": "",
            "expires_at": now + 7 * 86400,
            "rows": [],
            "archidekt_user_id": None,
        }
    )
    assert db.claim_proposal("p", "u1")
    db.finish_proposal("p", state="failed", result={"error": "conflict"})
    row = db._conn.execute("SELECT applied_at FROM proposals WHERE id = 'p'").fetchone()
    assert row[0] is None
