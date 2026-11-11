"""Pictures of cards the scanner could not find, kept for the gateway's admin (0.7.47, T-218).

After a miss the scanner offers "Keep this picture for the admin". The picture (the card as the
page straightened it, a JPEG of up to MAX_BYTES) is kept in the database with who sent it and
when, so the admin can see on Admin › Card scanner why cards are missed, instead of a member being
told to send photos to whoever runs the gateway. A picture is kept KEEP_DAYS days, at most
MAX_ROWS in all (the oldest go first), and goes with the member's other data when they delete it
(db.MEMBER_TABLES).
"""

from __future__ import annotations

import secrets
import time
from typing import Any

from ..db import Database

KEEP_DAYS = 30
MAX_ROWS = 500
MAX_BYTES = 400_000
PER_MEMBER_PER_DAY = 50  # one member can't fill the table

SCHEMA = """
CREATE TABLE IF NOT EXISTS scan_misses (
    id TEXT PRIMARY KEY,
    owner_sub TEXT NOT NULL,
    created_at REAL NOT NULL,
    picture BLOB NOT NULL,
    note TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS scan_misses_at ON scan_misses (created_at);
"""


class MissStore:
    def __init__(self, db: Database):
        self.db = db
        with db.tx() as c:
            for stmt in SCHEMA.split(";"):
                if stmt.strip():
                    c.execute(stmt)

    def purge(self, now: float | None = None) -> int:
        now = time.time() if now is None else now
        with self.db.tx() as c:
            n = c.execute("DELETE FROM scan_misses WHERE created_at < ?", (now - KEEP_DAYS * 86400,)).rowcount
            n += c.execute(
                "DELETE FROM scan_misses WHERE id NOT IN "
                "(SELECT id FROM scan_misses ORDER BY created_at DESC LIMIT ?)",
                (MAX_ROWS,),
            ).rowcount
        return n

    def add(self, owner: str, picture: bytes, note: str = "", now: float | None = None) -> str | None:
        """Keep one picture; None when the member already sent PER_MEMBER_PER_DAY today."""
        now = time.time() if now is None else now
        with self.db.tx() as c:
            sent = c.execute(
                "SELECT COUNT(*) FROM scan_misses WHERE owner_sub = ? AND created_at > ?",
                (owner, now - 86400),
            ).fetchone()[0]
            if sent >= PER_MEMBER_PER_DAY:
                return None
            mid = secrets.token_urlsafe(9)
            c.execute(
                "INSERT INTO scan_misses (id, owner_sub, created_at, picture, note) VALUES (?,?,?,?,?)",
                (mid, owner, now, picture, note[:300]),
            )
        self.purge(now)
        return mid

    def list(self) -> list[dict[str, Any]]:
        self.purge()
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT id, owner_sub, created_at, note, length(picture) AS size FROM scan_misses "
                "ORDER BY created_at DESC"
            ).fetchall()
        return [dict(r) for r in rows]

    def picture(self, mid: str) -> bytes | None:
        with self.db.tx() as c:
            row = c.execute("SELECT picture FROM scan_misses WHERE id = ?", (mid,)).fetchone()
        return bytes(row["picture"]) if row else None

    def delete(self, mid: str) -> bool:
        with self.db.tx() as c:
            return c.execute("DELETE FROM scan_misses WHERE id = ?", (mid,)).rowcount > 0

    def count(self) -> int:
        with self.db.tx() as c:
            return int(c.execute("SELECT COUNT(*) FROM scan_misses").fetchone()[0])
