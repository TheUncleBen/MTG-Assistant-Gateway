"""The Proposals and History screens of the 2026-10 UI standard (P1, P2, P3, H1): a closed
proposal's risk is stored when it closes (with a one-time backfill), the waiting dashboard with
its widgets, chips, filters and selection bar, the All view paged newest first, one proposal as
a side panel or its own page, and History without proposals."""

from __future__ import annotations

import re
import time
from pathlib import Path

from mtg_gateway.db import Database

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_history_page import seed_history, store_report

stack_options = stack_with(COMPANION)

LOW_ROWS = [{"kind": "add", "name": "Sol Ring", "qty": 1, "before": 0, "after": 1}]


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "r.sqlite")
    db.upsert_user("u", email="u@example.test", name="U", preferred_username="u", groups=[])
    return db


def _save(db: Database, pid: str, *, rows: list | None = None, deck: str = "42", **extra: object) -> None:
    db.save_proposal(
        {
            "id": pid,
            "owner_sub": "u",
            "kind": "edit",
            "deck_id": deck,
            "deck_name": "Immortal Reckoning",
            "baseline_fingerprint": "f",
            "changes": [],
            "diff_text": "+1 Sol Ring",
            "expires_at": int(time.time()) + 3600,
            "created_by_client": "__browser__",
            **({"rows": rows} if rows is not None else {}),
            **extra,
        }
    )


def _risk(db: Database, pid: str) -> str | None:
    return db._one("SELECT risk FROM proposals WHERE id = ?", (pid,))["risk"]


# -- the stored risk ---------------------------------------------------------------
def test_risk_is_stored_when_a_proposal_closes_and_cleared_when_it_reopens(tmp_path: Path) -> None:
    db = _db(tmp_path)
    db.risk_source = lambda row: "low" if row.get("rows") else "high"
    _save(db, "a", rows=LOW_ROWS)
    _save(db, "b")
    assert _risk(db, "a") is None  # a waiting one is worked out on every read
    db.finish_proposal("a", state="applied", result={})
    db.close_proposal("b", "u", "withdrawn")
    assert _risk(db, "a") == "low" and _risk(db, "b") == "high"
    # once stored it no longer follows the settings
    db.risk_source = lambda row: "destructive"
    db.settle_risks()
    assert _risk(db, "a") == "low"
    # a reopened proposal waits again, so its risk is worked out on read once more
    assert db.reopen_proposal("b", "u")
    assert _risk(db, "b") is None


def test_backfill_stores_the_risk_of_closed_proposals_once(tmp_path: Path) -> None:
    db = _db(tmp_path)
    _save(db, "a", rows=LOW_ROWS)
    _save(db, "b")
    _save(db, "c")
    db.finish_proposal("a", state="applied", result={})  # no risk_source yet: as before the upgrade
    db.finish_proposal("b", state="failed", result={})
    assert _risk(db, "a") is None and _risk(db, "b") is None

    def odd(row: dict) -> str:
        if row["id"] == "b":
            raise ValueError("cannot judge")
        return "low"

    db.risk_source = odd
    assert db.settle_risks() == 1  # one odd row does not stop the rest
    assert _risk(db, "a") == "low" and _risk(db, "b") is None and _risk(db, "c") is None


def test_proposal_index_and_history_counts(tmp_path: Path) -> None:
    db = _db(tmp_path)
    now = int(time.time())
    seed_history(db, "u", now=now)
    index = db.proposal_index("u")
    assert len(index) == 60 and index[0]["id"] == "p00"  # every one, newest first
    assert {"risk", "board_no", "snapshot_id", "result"} <= set(index[0])
    counts = db.history_counts("u", deck_id=None, search="", since=None)
    assert counts["snapshots"] == 15 and counts["restores"] == 1  # p04, an applied restore
    assert db.history_counts("u", deck_id="43", search="", since=None)["snapshots"] == 0
    stats = db.snapshot_stats("u")
    assert stats["count"] == 15 and stats["decks"] == 1


