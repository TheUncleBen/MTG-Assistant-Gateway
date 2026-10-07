"""SQLite storage for scan sessions (one table, owned by the scan package)."""

from __future__ import annotations

import json
import time
from typing import Any

from ..db import Database

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_sessions (
    id TEXT PRIMARY KEY,
    owner_sub TEXT NOT NULL,
    name TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    source TEXT NOT NULL DEFAULT 'browser',
    items_json TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS scan_sessions_owner ON scan_sessions(owner_sub, updated_at);
"""

# Scans are drafts the member keeps for as long as they like (up to a whole deck's worth, fixed up
# before it goes to Archidekt), so nothing expires by age; only a per-member ceiling guards the
# database, and the oldest draft goes only when a new one arrives past it.
MAX_SESSIONS_PER_USER = 500


class ScanStore:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            for stmt in SCHEMA.split(";"):
                if stmt.strip():
                    c.execute(stmt)

    def save(self, row: dict[str, Any]) -> None:
        with self.db.tx() as c:
            c.execute(
                """INSERT INTO scan_sessions
                   (id, owner_sub, name, status, source, items_json, created_at, updated_at)
                   VALUES (:id, :owner_sub, :name, :status, :source, :items_json, :created_at, :updated_at)
                   ON CONFLICT(id) DO UPDATE SET name = excluded.name, status = excluded.status,
                   items_json = excluded.items_json, updated_at = excluded.updated_at
                   WHERE scan_sessions.owner_sub = excluded.owner_sub""",
                {**row, "items_json": json.dumps(row["items"], separators=(",", ":"))},
            )

    def get(self, sid: str, owner_sub: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute(
                "SELECT * FROM scan_sessions WHERE id = ? AND owner_sub = ?", (sid, owner_sub)
            ).fetchone()
        if not r:
            return None
        return {**dict(r), "items": json.loads(r["items_json"])}

    def list(self, owner_sub: str, limit: int = 50) -> list[dict[str, Any]]:
        with self.db.tx() as c:
            rows = c.execute(
                """SELECT id, name, status, source, created_at, updated_at, items_json FROM scan_sessions
                   WHERE owner_sub = ? ORDER BY updated_at DESC LIMIT ?""",
                (owner_sub, limit),
            ).fetchall()
        out = []
        for r in rows:
            items = json.loads(r["items_json"])
            out.append(
                {
                    "id": r["id"],
                    "name": r["name"],
                    "status": r["status"],
                    "source": r["source"],
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                    "item_count": len(items),
                    "card_count": sum(int(it.get("quantity", 1)) for it in items),
                    "unresolved": sum(1 for it in items if not it.get("card")),
                }
            )
        return out

    def delete(self, sid: str, owner_sub: str) -> bool:
        with self.db.tx() as c:
            cur = c.execute("DELETE FROM scan_sessions WHERE id = ? AND owner_sub = ?", (sid, owner_sub))
            return cur.rowcount > 0

    def prune(self, owner_sub: str) -> int:
        """Keep the newest MAX_SESSIONS_PER_USER sessions for a user; drafts never expire by age."""
        with self.db.tx() as c:
            cur = c.execute(
                """DELETE FROM scan_sessions WHERE owner_sub = ? AND id NOT IN (
                       SELECT id FROM scan_sessions WHERE owner_sub = ? ORDER BY updated_at DESC LIMIT ?)""",
                (owner_sub, owner_sub, MAX_SESSIONS_PER_USER),
            )
            return cur.rowcount

    def count(self) -> int:
        with self.db.tx() as c:
            return int(c.execute("SELECT COUNT(*) FROM scan_sessions").fetchone()[0])

    @staticmethod
    def now() -> int:
        return int(time.time())
