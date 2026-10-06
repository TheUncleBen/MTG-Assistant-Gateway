"""SQLite storage for users, OAuth clients, login sessions, codes and tokens.

Tokens and codes are stored as SHA-256 hashes; the raw value exists only in the
response sent to the client. Every row that belongs to a user carries the
user's ``sub`` (the identity provider's stable subject), and lookups filter by
it where ownership matters.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .cimd import site_of

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    sub TEXT PRIMARY KEY,
    email TEXT,
    name TEXT,
    preferred_username TEXT,
    groups_json TEXT NOT NULL DEFAULT '[]',
    first_login_at INTEGER NOT NULL,
    last_login_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS oauth_clients (
    client_id TEXT PRIMARY KEY,
    info_json TEXT NOT NULL,
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS cimd_clients (
    client_id TEXT PRIMARY KEY,
    info_json TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_sessions (
    id TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    params_json TEXT NOT NULL,
    oidc_nonce TEXT NOT NULL,
    oidc_code_verifier TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    binding_hash TEXT
);
CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash TEXT PRIMARY KEY,
    client_id TEXT NOT NULL,
    data_json TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    used_family TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh')),
    client_id TEXT NOT NULL,
    sub TEXT NOT NULL,
    scopes_json TEXT NOT NULL,
    resource TEXT,
    family TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    auth_time INTEGER
);
CREATE INDEX IF NOT EXISTS tokens_family ON tokens(family);
CREATE INDEX IF NOT EXISTS tokens_sub ON tokens(sub);
CREATE TABLE IF NOT EXISTS browser_sessions (
    id_hash TEXT PRIMARY KEY,
    sub TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS archidekt_links (
    sub TEXT PRIMARY KEY,
    archidekt_username TEXT NOT NULL,
    archidekt_user_id TEXT,
    secret_enc TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_at INTEGER NOT NULL,
    refreshed_at INTEGER NOT NULL,
    last_used_at INTEGER
);
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    owner_sub TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'edit',
    deck_id TEXT NOT NULL,
    deck_name TEXT,
    baseline_fingerprint TEXT NOT NULL,
    changes_json TEXT NOT NULL,
    diff_text TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    applied_at INTEGER,
    snapshot_id TEXT,
    result_json TEXT,
    created_by_client TEXT
);
CREATE INDEX IF NOT EXISTS proposals_owner ON proposals(owner_sub);
CREATE TABLE IF NOT EXISTS snapshots (
    id TEXT PRIMARY KEY,
    owner_sub TEXT NOT NULL,
    deck_id TEXT NOT NULL,
    proposal_id TEXT,
    taken_at INTEGER NOT NULL,
    fingerprint TEXT NOT NULL,
    deck_json TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS snapshots_owner ON snapshots(owner_sub);
CREATE TABLE IF NOT EXISTS audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at INTEGER NOT NULL,
    sub TEXT,
    client_id TEXT,
    event TEXT NOT NULL,
    detail_json TEXT
);
"""


UNUSED_CLIENT_TTL = 7 * 86400
# Retention and size caps applied by purge_expired. Registration and /authorize are open to
# anyone, so rows nobody has signed in for are capped as well as aged out.
AUDIT_RETENTION_SECONDS = 365 * 86400
MAX_LOGIN_SESSIONS = 5000
# Pending logins are also capped per client and per browser, and a full table (or a full client)
# gives up the oldest pending login of the network that holds the most of them, never simply the
# oldest overall: a flood from one address, with one client or one browser, only pushes out its
# own pending logins, not those of the people signing in at the same time (create_login_session).
MAX_LOGIN_SESSIONS_PER_CLIENT = 500
MAX_LOGIN_SESSIONS_PER_BROWSER = 10
MAX_UNUSED_CLIENTS = 2000
# Cached client metadata documents (any https URL can be named as a client id). The busiest host
# gives up its oldest rows first, so one attacker domain cannot push out a real client's row.
MAX_CIMD_CLIENTS = 1000
MAX_CIMD_CLIENTS_PER_SITE = 50
# A metadata document URL that completed a sign-in is "known": never evicted by those caps and
# never subject to the fetcher's budgets (cimd.py). The mark lasts this long after the last one.
KNOWN_CIMD_CLIENT_RETENTION_SECONDS = 180 * 86400
# Audit events any anonymous caller can cause (open registration, CIMD fetches). Their rows are
# capped by count as well as aged out, and their detail is kept short, so a flood cannot grow the
# audit log for a year. Trimmed in purge_expired and every ANONYMOUS_AUDIT_TRIM_EVERY inserts.
ANONYMOUS_AUDIT_EVENTS = ("client_registered", "cimd_rejected", "cimd_accepted")
MAX_ANONYMOUS_AUDIT_ROWS = 5000
ANONYMOUS_AUDIT_TRIM_EVERY = 100
MAX_ANONYMOUS_AUDIT_DETAIL = 1024
# Closed proposals (expired, rejected, failed) are deleted this long after they were made.
CLOSED_PROPOSAL_RETENTION_SECONDS = 30 * 86400
CLOSED_PROPOSAL_STATES = ("expired", "rejected", "failed")
# Applied proposals are the record of what was changed and are kept longer, for as long as the
# audit log: they are deleted a year after they were applied.
APPLIED_PROPOSAL_RETENTION_SECONDS = 365 * 86400
# Snapshots are whole decks taken before every edit. The newest SNAPSHOTS_KEEP_PER_DECK of each
# member's deck are always kept (and any a pending restore still needs); older ones are deleted.
SNAPSHOTS_KEEP_PER_DECK = 25
# Per-day usage counters and remembered deck covers are kept this long.
METRICS_RETENTION_SECONDS = 400 * 86400
DECK_COVER_RETENTION_SECONDS = 180 * 86400
# How long a write waits for another connection (such as `mtg-gateway backup`) to finish.
BUSY_TIMEOUT_MS = 5000


def _url_site(url: str) -> str:
    """Registrable domain of a URL client id, for the per-site cap on cached metadata documents."""
    return site_of(urlparse(url).hostname or "")


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def _step_1(conn: sqlite3.Connection) -> None:
    """The v0.1.0 schema plus the two snapshot columns added after it. Every statement is
    idempotent, so a database made by v0.1.0 (user_version 0) is brought to step 1 unchanged."""
    conn.executescript(SCHEMA)
    _add_column(conn, "snapshots", "backup_deck_id", "TEXT")
    _add_column(conn, "snapshots", "backup_url", "TEXT")


def _step_2(conn: sqlite3.Connection) -> None:
    """Admin: disabled users, last-seen stamps and the per-day metric counters."""
    _add_column(conn, "users", "disabled_at", "INTEGER")
    _add_column(conn, "users", "last_seen_at", "INTEGER")
    conn.execute("UPDATE users SET last_seen_at = last_login_at WHERE last_seen_at IS NULL")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS metrics (
            day TEXT NOT NULL,
            sub TEXT NOT NULL DEFAULT '',
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            n INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (day, sub, kind, name)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS audit_log_sub ON audit_log(sub, id)")


def _step_3(conn: sqlite3.Connection) -> None:
    """Security round 2: the browser binding of a pending login (older pending logins have none
    and are refused; they are short-lived), each grant's own sign-in time, redeemed-code
    tombstones, and the app that created a proposal."""
    _add_column(conn, "login_sessions", "binding_hash", "TEXT")
    _add_column(conn, "tokens", "auth_time", "INTEGER")
    _add_column(conn, "auth_codes", "used_family", "TEXT")
    _add_column(conn, "proposals", "created_by_client", "TEXT")


def _step_4(conn: sqlite3.Connection) -> None:
    """Security round 3: each proposal's structured review rows (the review page renders these,
    never the text diff), and an index for the per-member proposal caps."""
    _add_column(conn, "proposals", "rows_json", "TEXT")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS proposals_owner_state ON proposals(owner_sub, state, created_at)"
    )


def _step_5(conn: sqlite3.Connection) -> None:
    """Deck covers: the Scryfall id of each deck's featured card, remembered whenever a deck is
    read, so the deck list can show its art without reading every deck again."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS deck_covers (
            deck_id TEXT PRIMARY KEY,
            scryfall_uid TEXT NOT NULL,
            card_name TEXT NOT NULL DEFAULT '',
            updated_at INTEGER NOT NULL
        )"""
    )


def _step_6(conn: sqlite3.Connection) -> None:
    """Production readiness: indexes for the nightly purge and the snapshot lists, which
    otherwise scan whole tables as they grow."""
    for sql in (
        "CREATE INDEX IF NOT EXISTS audit_log_event ON audit_log(event, id)",
        "CREATE INDEX IF NOT EXISTS audit_log_at ON audit_log(at)",
        "CREATE INDEX IF NOT EXISTS browser_sessions_sub ON browser_sessions(sub)",
        "CREATE INDEX IF NOT EXISTS browser_sessions_expires ON browser_sessions(expires_at)",
        "CREATE INDEX IF NOT EXISTS tokens_expires ON tokens(expires_at)",
        "CREATE INDEX IF NOT EXISTS snapshots_owner_taken ON snapshots(owner_sub, taken_at)",
        "CREATE INDEX IF NOT EXISTS snapshots_owner_deck ON snapshots(owner_sub, deck_id, taken_at)",
    ):
        conn.execute(sql)


def _step_7(conn: sqlite3.Connection) -> None:
    """Security round 4: the identity provider's own tokens for each member (Fernet-encrypted), so
    the gateway can ask the provider on later requests whether the member is still allowed in."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS idp_grants (
            sub TEXT PRIMARY KEY,
            refresh_enc TEXT NOT NULL DEFAULT '',
            access_enc TEXT NOT NULL DEFAULT '',
            access_expires_at INTEGER NOT NULL DEFAULT 0,
            updated_at INTEGER NOT NULL
        )"""
    )
    # The identity provider (issuer) each member first signed in with: a subject from another
    # provider is a different person even when the strings match.
    _add_column(conn, "users", "idp_issuer", "TEXT")


def _step_8(conn: sqlite3.Connection) -> None:
    """Security round 4: the (hashed) network each pending login came from, so a flood of
    anonymous sign-in starts only pushes out pending logins from the flooding network."""
    _add_column(conn, "login_sessions", "source_hash", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS login_sessions_client ON login_sessions(client_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS login_sessions_binding ON login_sessions(binding_hash)")


def _step_9(conn: sqlite3.Connection) -> None:
    """Security round 4: the member whose deck page stored each deck cover, so "Delete my data"
    removes their covers too (older rows have none and go by the member's deck ids)."""
    _add_column(conn, "deck_covers", "owner_sub", "TEXT")
    conn.execute("CREATE INDEX IF NOT EXISTS deck_covers_owner ON deck_covers(owner_sub)")


def _step_10(conn: sqlite3.Connection) -> None:
    """Security round 4: when a client described by a metadata document last completed a sign-in,
    so the cache caps never evict it and the fetch budgets never apply to it."""
    _add_column(conn, "cimd_clients", "signed_in_at", "INTEGER")


# Applied in order; ``PRAGMA user_version`` records how many have run. Append, never edit.
MIGRATIONS = [_step_1, _step_2, _step_3, _step_4, _step_5, _step_6, _step_7, _step_8, _step_9, _step_10]
SCHEMA_VERSION = len(MIGRATIONS)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, path: Path | str):
        self.path = Path(path)
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._anonymous_audit_inserts = 0
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if str(self.path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            try:
                self._migrate()
            except Exception:
                self._conn.close()
                raise

    def _migrate(self) -> None:
        """Run the migration steps this database has not seen (``PRAGMA user_version``)."""
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version > len(MIGRATIONS):
            # A newer gateway wrote this file. An older image would skip columns it doesn't know,
            # such as users.disabled_at, and let disabled people back in, so refuse instead.
            raise RuntimeError(
                f"database schema version {version} is newer than this gateway supports "
                f"({len(MIGRATIONS)}); run the newer image or restore a backup taken before the upgrade"
            )
        for number, step in enumerate(MIGRATIONS, start=1):
            if version >= number:
                continue
            if number == 1:
                # executescript commits on its own, so step 1 can't share a transaction. Every
                # statement in it is idempotent, so an interrupted run simply repeats it.
                step(self._conn)
                self._conn.execute(f"PRAGMA user_version = {number}")
                continue
            # A step and its version bump commit together, or neither does.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                step(self._conn)
                self._conn.execute(f"PRAGMA user_version = {number}")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")

    @property
    def schema_version(self) -> int:
        with self._lock:
            return int(self._conn.execute("PRAGMA user_version").fetchone()[0])

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    @contextmanager
    def tx(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield self._conn
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def _one(self, sql: str, args: tuple[Any, ...] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, args).fetchone()

    # users -----------------------------------------------------------------
    def upsert_user(
        self,
        sub: str,
        *,
        email: str | None,
        name: str | None,
        preferred_username: str | None,
        groups: list[str],
        issuer: str | None = None,
    ) -> None:
        now = int(time.time())
        with self.tx() as c:
            c.execute(
                """INSERT INTO users
                   (sub, email, name, preferred_username, groups_json, first_login_at, last_login_at,
                    last_seen_at, idp_issuer)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(sub) DO UPDATE SET email=excluded.email, name=excluded.name,
                     preferred_username=excluded.preferred_username, groups_json=excluded.groups_json,
                     last_login_at=excluded.last_login_at, last_seen_at=excluded.last_seen_at,
                     idp_issuer=COALESCE(users.idp_issuer, excluded.idp_issuer)""",
                (sub, email, name, preferred_username, json.dumps(groups), now, now, now, issuer),
            )

    def touch_user(self, sub: str) -> None:
        """Stamp last_seen_at (a token refresh: the one cheap, infrequent moment a session passes by)."""
        with self.tx() as c:
            c.execute("UPDATE users SET last_seen_at = ? WHERE sub = ?", (int(time.time()), sub))

    def set_user_disabled(self, sub: str, disabled: bool) -> bool:
        """Mark a user disabled (refused at token load, refresh, sign-in and browser session) or
        enable them again. False if there is no such user."""
        with self.tx() as c:
            cur = c.execute(
                "UPDATE users SET disabled_at = ? WHERE sub = ?",
                (int(time.time()) if disabled else None, sub),
            )
            return cur.rowcount > 0

    def list_users(self) -> list[dict[str, Any]]:
        """Every user the identity provider has signed in, newest sign-in first, with their active
        Archidekt link, unexpired unrevoked tokens and live browser sessions counted."""
        now = int(time.time())
        with self._lock:
            rows = self._conn.execute(
                """SELECT u.sub, u.name, u.email, u.preferred_username, u.groups_json, u.first_login_at,
                          u.last_login_at, u.last_seen_at, u.disabled_at,
                          l.archidekt_username,
                          (SELECT COUNT(*) FROM tokens t WHERE t.sub = u.sub AND t.revoked = 0
                              AND t.expires_at >= ?) AS active_tokens,
                          (SELECT COUNT(*) FROM browser_sessions b WHERE b.sub = u.sub
                              AND b.expires_at >= ?) AS browser_sessions
                   FROM users u
                   LEFT JOIN archidekt_links l ON l.sub = u.sub AND l.status = 'active'
                   ORDER BY u.last_login_at DESC, u.sub""",
                (now, now),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["groups"] = json.loads(d.pop("groups_json") or "[]")
            out.append(d)
        return out

    def user_clients(self, sub: str) -> list[dict[str, Any]]:
        """The OAuth clients holding an unexpired, unrevoked token for ``sub``: the apps connected
        to the account, each with its registered name and the time of its newest token."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT client_id, MAX(created_at) AS last_issued_at, COUNT(*) AS tokens FROM tokens
                   WHERE sub = ? AND revoked = 0 AND expires_at >= ?
                   GROUP BY client_id ORDER BY last_issued_at DESC""",
                (sub, int(time.time())),
            ).fetchall()
        return [
            {
                "client_id": r["client_id"],
                "name": self.client_name(r["client_id"]),
                "last_issued_at": r["last_issued_at"],
                "tokens": r["tokens"],
            }
            for r in rows
        ]

    def revoke_client_for_user(self, sub: str, client_id: str | None = None) -> int:
        """Revoke the tokens ``sub`` gave one connected app (or every app when ``client_id`` is
        None). The person's own browser sessions are left alone."""
        with self.tx() as c:
            if client_id is None:
                cur = c.execute("UPDATE tokens SET revoked = 1 WHERE sub = ? AND revoked = 0", (sub,))
            else:
                cur = c.execute(
                    "UPDATE tokens SET revoked = 1 WHERE sub = ? AND client_id = ? AND revoked = 0",
                    (sub, client_id),
                )
            return cur.rowcount

    def revoke_all_for_user(self, sub: str) -> dict[str, int]:
        """Revoke every token of ``sub`` and delete their browser sessions and pending codes."""
        with self.tx() as c:
            tokens = c.execute("UPDATE tokens SET revoked = 1 WHERE sub = ? AND revoked = 0", (sub,)).rowcount
            sessions = c.execute("DELETE FROM browser_sessions WHERE sub = ?", (sub,)).rowcount
            # The provider tokens go too: with no sessions left there is nothing to re-check, and a
            # fresh sign-in stores new ones.
            c.execute("DELETE FROM idp_grants WHERE sub = ?", (sub,))
            codes = c.execute(
                "DELETE FROM auth_codes WHERE json_extract(data_json, '$.subject') = ?"
                " AND used_family IS NULL",  # redeemed codes stay as replay tombstones
                (sub,),
            ).rowcount
        return {"tokens": tokens, "sessions": sessions, "codes": codes}

    def get_user(self, sub: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM users WHERE sub = ?", (sub,))
        if row is None:
            return None
        d = dict(row)
        d["groups"] = json.loads(d.pop("groups_json"))
        return d

    # oauth clients ---------------------------------------------------------
    def save_client(self, client_id: str, info: dict[str, Any]) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO oauth_clients (client_id, info_json, created_at) VALUES (?, ?, ?)",
                (client_id, json.dumps(info), int(time.time())),
            )

    def get_client(self, client_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT info_json FROM oauth_clients WHERE client_id = ?", (client_id,))
        return json.loads(row["info_json"]) if row else None

    def client_name(self, client_id: str | None) -> str | None:
        """The registered or published name of an OAuth client, for the audit log; None if unknown."""
        if not client_id:
            return None
        info = self.get_client(client_id)
        if info is None:
            row = self._one("SELECT info_json FROM cimd_clients WHERE client_id = ?", (client_id,))
            info = json.loads(row["info_json"]) if row else None
        name = info.get("client_name") if isinstance(info, dict) else None
        return str(name)[:200] if name else None

    # client id metadata documents (cached) ---------------------------------
    def save_cimd_client(self, client_id: str, info: dict[str, Any], ttl: int) -> None:
        now = int(time.time())
        with self.tx() as c:
            # Anyone can make the gateway accept documents at many URLs of their own site, so the
            # cache is capped on every insert: per site, then in all (cimd.py caps each record).
            # Clients that completed a sign-in are never evicted.
            self._purge_cimd_clients(c, now)
            site = _url_site(client_id)
            self._trim_cimd_clients(c, MAX_CIMD_CLIENTS_PER_SITE - 1, site=site, skip=client_id)
            self._trim_cimd_clients(c, MAX_CIMD_CLIENTS - 1, skip=client_id)
            c.execute(
                "INSERT INTO cimd_clients (client_id, info_json, fetched_at, expires_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(client_id) DO UPDATE SET info_json = excluded.info_json, "
                "fetched_at = excluded.fetched_at, expires_at = excluded.expires_at",
                (client_id, json.dumps(info), now, now + ttl),
            )

    def mark_cimd_client_signed_in(self, client_id: str) -> None:
        """Record that a client described by this metadata document completed a sign-in."""
        with self.tx() as c:
            c.execute(
                "UPDATE cimd_clients SET signed_in_at = ? WHERE client_id = ?", (int(time.time()), client_id)
            )

    def cimd_client_known(self, client_id: str) -> bool:
        """True when a client with this metadata document URL has completed a sign-in here (its
        cached document may have expired since)."""
        row = self._one(
            "SELECT 1 FROM cimd_clients WHERE client_id = ? AND signed_in_at IS NOT NULL", (client_id,)
        )
        return row is not None

    @staticmethod
    def _purge_cimd_clients(c: sqlite3.Connection, now: int) -> int:
        """Drop cached documents a day past expiry, unless their client signed in recently."""
        return c.execute(
            "DELETE FROM cimd_clients WHERE expires_at < ? AND (signed_in_at IS NULL OR signed_in_at < ?)",
            (now - 86400, now - KNOWN_CIMD_CLIENT_RETENTION_SECONDS),
        ).rowcount

    def get_cimd_client(self, client_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT info_json FROM cimd_clients WHERE client_id = ? AND expires_at >= ?",
            (client_id, int(time.time())),
        )
        return json.loads(row["info_json"]) if row else None

    # login sessions (browser leg with the identity provider) ---------------
    def create_login_session(
        self,
        session_id: str,
        *,
        client_id: str,
        params: dict[str, Any],
        oidc_nonce: str,
        oidc_code_verifier: str,
        ttl: int,
        binding_hash: str | None = None,
        source_hash: str | None = None,
    ) -> None:
        """Store a pending login. ``binding_hash`` names the browser that started it and
        ``source_hash`` the network it came from (auth_provider), both used by the caps below."""
        now = int(time.time())
        with self.tx() as c:
            # /authorize and /login are anonymous and each stores one row, so expired rows are
            # deleted and the caps applied on every insert, not only by the nightly purge (the
            # table is that small, so these statements are cheap). One browser keeps its newest
            # few; a client, and then the whole table, gives up rows of the busiest network.
            c.execute("DELETE FROM login_sessions WHERE expires_at < ?", (now,))
            if binding_hash:
                c.execute(
                    """DELETE FROM login_sessions WHERE rowid IN (
                           SELECT rowid FROM login_sessions WHERE binding_hash = ?
                           ORDER BY expires_at DESC, rowid DESC LIMIT -1 OFFSET ?)""",
                    (binding_hash, MAX_LOGIN_SESSIONS_PER_BROWSER - 1),
                )
            self._trim_login_sessions(c, MAX_LOGIN_SESSIONS_PER_CLIENT - 1, client_id=client_id)
            self._trim_login_sessions(c, MAX_LOGIN_SESSIONS - 1)
            c.execute(
                """INSERT INTO login_sessions (id, client_id, params_json, oidc_nonce, oidc_code_verifier,
                   expires_at, binding_hash, source_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    client_id,
                    json.dumps(params),
                    oidc_nonce,
                    oidc_code_verifier,
                    now + ttl,
                    binding_hash,
                    source_hash,
                ),
            )

    def get_login_session_exists(self, session_id: str) -> bool:
        row = self._one(
            "SELECT 1 FROM login_sessions WHERE id = ? AND expires_at >= ?", (session_id, int(time.time()))
        )
        return row is not None

    def get_login_session_client_id(self, session_id: str) -> str | None:
        row = self._one(
            "SELECT client_id FROM login_sessions WHERE id = ? AND expires_at >= ?",
            (session_id, int(time.time())),
        )
        return row["client_id"] if row else None

    def get_login_session(self, session_id: str) -> dict[str, Any] | None:
        """Return a login session without consuming it (the CIMD confirmation page)."""
        row = self._one(
            "SELECT * FROM login_sessions WHERE id = ? AND expires_at >= ?", (session_id, int(time.time()))
        )
        if row is None:
            return None
        d = dict(row)
        d["params"] = json.loads(d.pop("params_json"))
        return d

    def pop_login_session(self, session_id: str) -> dict[str, Any] | None:
        """Return and delete a login session (single use)."""
        with self.tx() as c:
            row = c.execute("SELECT * FROM login_sessions WHERE id = ?", (session_id,)).fetchone()
            if row is None:
                return None
            c.execute("DELETE FROM login_sessions WHERE id = ?", (session_id,))
        d = dict(row)
        if d["expires_at"] < int(time.time()):
            return None
        d["params"] = json.loads(d.pop("params_json"))
        return d

    # authorization codes ---------------------------------------------------
    def save_auth_code(self, code: str, *, client_id: str, data: dict[str, Any], expires_at: int) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO auth_codes (code_hash, client_id, data_json, expires_at) VALUES (?, ?, ?, ?)",
                (hash_token(code), client_id, json.dumps(data), expires_at),
            )

    def get_auth_code(self, code: str, client_id: str) -> dict[str, Any] | None:
        """The code's data, or None; a code already redeemed comes back as {"used_family": ...}."""
        row = self._one(
            "SELECT data_json, used_family FROM auth_codes WHERE code_hash = ? AND client_id = ?",
            (hash_token(code), client_id),
        )
        if row is None:
            return None
        if row["used_family"]:
            return {"used_family": row["used_family"]}
        return json.loads(row["data_json"])

    def use_auth_code(self, code: str, family: str) -> bool:
        """Mark a code redeemed for ``family`` (kept until it expires, so a replay can revoke
        that family). False when it was already used: the update is the single-use check."""
        with self.tx() as c:
            cur = c.execute(
                "UPDATE auth_codes SET used_family = ? WHERE code_hash = ? AND used_family IS NULL",
                (family, hash_token(code)),
            )
            return cur.rowcount > 0

    # tokens ----------------------------------------------------------------
    def save_token(
        self,
        token: str,
        *,
        kind: str,
        client_id: str,
        sub: str,
        scopes: list[str],
        resource: str | None,
        family: str,
        expires_at: int,
        auth_time: int | None = None,
    ) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO tokens (token_hash, kind, client_id, sub, scopes_json, resource, family,
                   expires_at, revoked, created_at, auth_time) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?)""",
                (
                    hash_token(token),
                    kind,
                    client_id,
                    sub,
                    json.dumps(scopes),
                    resource,
                    family,
                    expires_at,
                    int(time.time()),
                    auth_time,
                ),
            )

    def get_token(self, token: str, kind: str, *, include_revoked: bool = False) -> dict[str, Any] | None:
        sql = "SELECT * FROM tokens WHERE token_hash = ? AND kind = ?"
        if not include_revoked:
            sql += " AND revoked = 0"
        row = self._one(sql, (hash_token(token), kind))
        if row is None:
            return None
        d = dict(row)
        d["scopes"] = json.loads(d.pop("scopes_json"))
        return d

    def revoke_family(self, family: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE tokens SET revoked = 1 WHERE family = ?", (family,))

    def revoke_generation(self, family: str, created_at: int) -> None:
        """Revoke the tokens of ``family`` issued at or before ``created_at`` (a rotation)."""
        with self.tx() as c:
            c.execute(
                "UPDATE tokens SET revoked = 1 WHERE family = ? AND created_at <= ?", (family, created_at)
            )

    def fail_interrupted_applies(self) -> int:
        """At startup, mark every proposal still 'applying' as failed. There is one gateway process,
        so an apply left running by the previous one (a crash, a redeploy) can't still be going.
        The result points at the snapshot taken before it and the deck, so the member can check."""
        with self.tx() as c:
            return c.execute(
                """UPDATE proposals SET state = 'failed',
                   result_json = COALESCE(result_json, json_object('error', 'interrupted',
                       'detail', 'the gateway restarted before the apply finished; check the deck on '
                                 || 'Archidekt and restore the snapshot if it is only partly changed',
                       'snapshot_id', snapshot_id, 'deck_id', deck_id))
                   WHERE state = 'applying'"""
            ).rowcount

    def purge_expired(self) -> int:
        now = int(time.time())
        with self.tx() as c:
            n = c.execute("DELETE FROM tokens WHERE expires_at < ?", (now - 86400,)).rowcount
            n += c.execute("DELETE FROM auth_codes WHERE expires_at < ?", (now,)).rowcount
            n += c.execute("DELETE FROM login_sessions WHERE expires_at < ?", (now,)).rowcount
            n += c.execute("DELETE FROM browser_sessions WHERE expires_at < ?", (now,)).rowcount
            n += self._purge_cimd_clients(c, now)
            # Registration is open, so every client that registered and never finished a login,
            # or whose tokens have all expired and been purged, is dropped after a week.
            n += c.execute(
                """DELETE FROM oauth_clients WHERE created_at < ?
                   AND client_id NOT IN (SELECT client_id FROM tokens)
                   AND client_id NOT IN (SELECT client_id FROM auth_codes)
                   AND client_id NOT IN (SELECT client_id FROM login_sessions)""",
                (now - UNUSED_CLIENT_TTL,),
            ).rowcount
            n += self._prune_caps(c, now)
            n += self._prune_snapshots(c)
            n += c.execute(
                "DELETE FROM metrics WHERE day < ?", (self._since_day(METRICS_RETENTION_SECONDS // 86400),)
            ).rowcount
            n += c.execute(
                "DELETE FROM deck_covers WHERE updated_at < ?", (now - DECK_COVER_RETENTION_SECONDS,)
            ).rowcount
            n += c.execute(
                "UPDATE proposals SET state = 'expired' WHERE state = 'pending' AND expires_at < ?", (now,)
            ).rowcount
            # Closed proposals carry nothing the user can act on: drop them after a month.
            n += c.execute(
                "DELETE FROM proposals WHERE state IN ('expired', 'rejected', 'failed') AND created_at < ?",
                (now - CLOSED_PROPOSAL_RETENTION_SECONDS,),
            ).rowcount
            n += c.execute(
                "DELETE FROM proposals WHERE state = 'applied' AND COALESCE(applied_at, created_at) < ?",
                (now - APPLIED_PROPOSAL_RETENTION_SECONDS,),
            ).rowcount
            # An apply interrupted by a restart leaves 'applying' behind; an hour after it started treat
            # it as failed and point at the snapshot (and the created deck id) in the result.
            n += c.execute(
                """UPDATE proposals SET state = 'failed',
                   result_json = COALESCE(result_json, json_object('error', 'interrupted',
                       'detail', 'the apply did not finish; check the snapshot and the deck on Archidekt',
                       'snapshot_id', snapshot_id, 'deck_id', deck_id))
                   WHERE state = 'applying' AND COALESCE(applied_at, created_at) < ?""",
                (now - 3600,),
            ).rowcount
        return n

    @staticmethod
    def _prune_caps(c: sqlite3.Connection, now: int) -> int:
        """Age out the audit log and cap the tables anonymous requests can grow.

        A flood of /register or /authorize calls inside the normal expiry windows would
        otherwise grow the database without bound. The oldest rows go first.
        """
        n = c.execute("DELETE FROM audit_log WHERE at < ?", (now - AUDIT_RETENTION_SECONDS,)).rowcount
        n += Database._trim_anonymous_audit(c)
        n += Database._trim_login_sessions(c, MAX_LOGIN_SESSIONS)
        n += Database._trim_cimd_clients(c, MAX_CIMD_CLIENTS)
        n += c.execute(
            """DELETE FROM oauth_clients WHERE client_id IN (
                   SELECT client_id FROM oauth_clients
                   WHERE client_id NOT IN (SELECT client_id FROM tokens)
                     AND client_id NOT IN (SELECT client_id FROM auth_codes)
                     AND client_id NOT IN (SELECT client_id FROM login_sessions)
                   ORDER BY created_at DESC, rowid DESC
                   LIMIT -1 OFFSET ?)""",
            (MAX_UNUSED_CLIENTS,),
        ).rowcount
        return n

    @staticmethod
    def _prune_snapshots(c: sqlite3.Connection) -> int:
        """Keep the newest SNAPSHOTS_KEEP_PER_DECK snapshots of each member's deck, and any
        snapshot a pending or running proposal (a restore) still points at; delete the rest."""
        return c.execute(
            """DELETE FROM snapshots WHERE id IN (
                   SELECT id FROM (
                       SELECT id, ROW_NUMBER() OVER (
                           PARTITION BY owner_sub, deck_id ORDER BY taken_at DESC, rowid DESC) AS n
                       FROM snapshots)
                   WHERE n > ?)
               AND id NOT IN (SELECT json_extract(changes_json, '$.snapshot_id') FROM proposals
                              WHERE kind = 'restore' AND state IN ('pending', 'applying')
                                AND json_extract(changes_json, '$.snapshot_id') IS NOT NULL)""",
            (SNAPSHOTS_KEEP_PER_DECK,),
        ).rowcount

    @staticmethod
    def _trim_login_sessions(c: sqlite3.Connection, keep: int, *, client_id: str | None = None) -> int:
        """Delete login sessions (of ``client_id``, or all) until at most ``keep`` are left.

        Rows go from the network (``source_hash``) holding the most of them, oldest first, so a
        flood from one network gives up its own rows before anyone else's. Rows with no source
        (made before it was recorded) count as one network."""
        where, args = ("client_id = ?", (client_id,)) if client_id is not None else ("1", ())
        n = 0
        while True:
            total = c.execute(f"SELECT COUNT(*) FROM login_sessions WHERE {where}", args).fetchone()[0]
            over = total - max(0, keep)
            if over <= 0:
                return n
            groups = c.execute(
                f"""SELECT COALESCE(source_hash, '') AS src, COUNT(*) AS k FROM login_sessions
                    WHERE {where} GROUP BY src ORDER BY k DESC, MIN(expires_at) ASC LIMIT 2""",
                args,
            ).fetchall()
            top, top_count = groups[0][0], groups[0][1]
            runner_up = groups[1][1] if len(groups) > 1 else 0
            # Level the busiest network down to the next one (at least one row per round).
            take = min(over, max(1, top_count - runner_up))
            n += c.execute(
                f"""DELETE FROM login_sessions WHERE rowid IN (
                       SELECT rowid FROM login_sessions WHERE {where} AND COALESCE(source_hash, '') = ?
                       ORDER BY expires_at ASC, rowid ASC LIMIT ?)""",
                (*args, top, take),
            ).rowcount

    @staticmethod
    def _trim_cimd_clients(
        c: sqlite3.Connection, keep: int, *, site: str | None = None, skip: str | None = None
    ) -> int:
        """Delete cached metadata documents (of ``site``, or all; never ``skip`` and never a client
        that completed a sign-in) until at most ``keep`` evictable ones are left: from the site
        with the most rows, the least recently fetched first."""
        rows = [
            (r[0], r[1], _url_site(r[0]))
            for r in c.execute(
                "SELECT client_id, fetched_at FROM cimd_clients WHERE signed_in_at IS NULL "
                "ORDER BY fetched_at, rowid"
            )
            if r[0] != skip
        ]
        if site is not None:
            rows = [r for r in rows if r[2] == site]
        over = len(rows) - max(0, keep)
        if over <= 0:
            return 0
        by_host: dict[str, list[str]] = {}
        for client_id, _at, h in rows:
            by_host.setdefault(h, []).append(client_id)  # oldest first
        doomed: list[str] = []
        for _ in range(over):
            busiest = max(by_host, key=lambda h: len(by_host[h]))
            doomed.append(by_host[busiest].pop(0))
        c.executemany("DELETE FROM cimd_clients WHERE client_id = ?", [(d,) for d in doomed])
        return len(doomed)

    @staticmethod
    def _trim_anonymous_audit(c: sqlite3.Connection) -> int:
        """Keep only the newest MAX_ANONYMOUS_AUDIT_ROWS rows of the anonymous audit events."""
        marks = ",".join("?" for _ in ANONYMOUS_AUDIT_EVENTS)
        return c.execute(
            f"""DELETE FROM audit_log WHERE id IN (
                   SELECT id FROM audit_log WHERE event IN ({marks})
                   ORDER BY id DESC LIMIT -1 OFFSET ?)""",
            (*ANONYMOUS_AUDIT_EVENTS, MAX_ANONYMOUS_AUDIT_ROWS),
        ).rowcount

    # audit -----------------------------------------------------------------
    def audit(
        self,
        event: str,
        *,
        sub: str | None = None,
        client_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        detail_json = json.dumps(detail) if detail else None
        anonymous = event in ANONYMOUS_AUDIT_EVENTS
        if anonymous and detail_json and len(detail_json) > MAX_ANONYMOUS_AUDIT_DETAIL:
            detail_json = json.dumps({"truncated": detail_json[: MAX_ANONYMOUS_AUDIT_DETAIL - 64]})
        with self.tx() as c:
            c.execute(
                "INSERT INTO audit_log (at, sub, client_id, event, detail_json) VALUES (?, ?, ?, ?, ?)",
                (int(time.time()), sub, client_id, event, detail_json),
            )
            if anonymous:
                self._anonymous_audit_inserts += 1
                if self._anonymous_audit_inserts % ANONYMOUS_AUDIT_TRIM_EVERY == 0:
                    self._trim_anonymous_audit(c)

    def audit_recent(self, limit: int = 100, sub: str | None = None) -> list[dict[str, Any]]:
        """Newest audit rows first, optionally only those of one user."""
        limit = max(1, min(int(limit), 1000))
        sql = "SELECT id, at, sub, client_id, event, detail_json FROM audit_log"
        args: tuple[Any, ...] = ()
        if sub is not None:
            sql += " WHERE sub = ?"
            args = (sub,)
        sql += " ORDER BY id DESC LIMIT ?"
        with self._lock:
            rows = self._conn.execute(sql, (*args, limit)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            raw = d.pop("detail_json")
            try:
                d["detail"] = json.loads(raw) if raw else None
            except ValueError:
                d["detail"] = {"raw": raw}
            out.append(d)
        return out

    def audit_for_user(self, sub: str, limit: int = 100) -> list[dict[str, Any]]:
        return self.audit_recent(limit, sub=sub)

    def count_audit(self, sub: str, event: str, since: int) -> int:
        """How many ``event`` rows ``sub`` has at or after ``since`` (e.g. failed link attempts)."""
        row = self._one(
            "SELECT COUNT(*) FROM audit_log WHERE sub = ? AND event = ? AND at >= ?", (sub, event, since)
        )
        return int(row[0]) if row else 0

    # metrics (per-day counters; see metrics.Metrics) ------------------------
    def metrics_increment(self, day: str, sub: str | None, kind: str, name: str, n: int = 1) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO metrics (day, sub, kind, name, n) VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(day, sub, kind, name) DO UPDATE SET n = n + excluded.n""",
                (day, sub or "", kind[:40], name[:120], int(n)),
            )

    @staticmethod
    def _since_day(days: int) -> str:
        days = max(1, min(int(days), 3650))
        today = _dt.datetime.now(_dt.UTC).date()
        return (today - _dt.timedelta(days=days - 1)).isoformat()

    def metrics_totals(self, days: int = 30) -> list[dict[str, Any]]:
        """Sum per (kind, name) over the last ``days`` days (today included), largest first."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT kind, name, SUM(n) AS n FROM metrics WHERE day >= ? GROUP BY kind, name "
                "ORDER BY n DESC, kind, name",
                (self._since_day(days),),
            ).fetchall()
        return [dict(r) for r in rows]

    def metrics_series(self, days: int = 30, kind: str | None = None) -> list[dict[str, Any]]:
        """Per-day sums over the last ``days`` days as rows {day, kind, n}, oldest first. Days
        with no activity are absent; ``kind`` narrows to one counter kind."""
        sql = "SELECT day, kind, SUM(n) AS n FROM metrics WHERE day >= ?"
        args: tuple[Any, ...] = (self._since_day(days),)
        if kind is not None:
            sql += " AND kind = ?"
            args += (kind,)
        sql += " GROUP BY day, kind ORDER BY day, kind"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def overview_counts(self, days: int = 30) -> dict[str, Any]:
        """Headline numbers for the admin overview: users, disabled, linked, and proposals by state
        created in the last ``days`` days."""
        since = int(time.time()) - max(1, int(days)) * 86400
        with self._lock:
            users = self._conn.execute(
                "SELECT COUNT(*) AS n, SUM(disabled_at IS NOT NULL) AS disabled FROM users"
            ).fetchone()
            linked = self._conn.execute(
                "SELECT COUNT(*) AS n FROM archidekt_links WHERE status = 'active'"
            ).fetchone()
            props = self._conn.execute(
                "SELECT state, COUNT(*) AS n FROM proposals WHERE created_at >= ? GROUP BY state", (since,)
            ).fetchall()
        return {
            "users": int(users["n"] or 0),
            "disabled": int(users["disabled"] or 0),
            "linked": int(linked["n"] or 0),
            "proposals": {r["state"]: int(r["n"]) for r in props},
        }

    # browser sessions ------------------------------------------------------
    def create_browser_session(self, session_id: str, sub: str, ttl: int) -> None:
        now = int(time.time())
        with self.tx() as c:
            c.execute(
                "INSERT INTO browser_sessions (id_hash, sub, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (hash_token(session_id), sub, now, now + ttl),
            )

    def get_browser_session(self, session_id: str) -> str | None:
        row = self._one(
            "SELECT sub FROM browser_sessions WHERE id_hash = ? AND expires_at >= ?",
            (hash_token(session_id), int(time.time())),
        )
        return row["sub"] if row else None

    def delete_browser_session(self, session_id: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM browser_sessions WHERE id_hash = ?", (hash_token(session_id),))

    def delete_browser_sessions_for(self, sub: str) -> int:
        with self.tx() as c:
            return c.execute("DELETE FROM browser_sessions WHERE sub = ?", (sub,)).rowcount

    # Tables a member's own rows live in, and the column naming the member. reports and
    # scan_sessions belong to optional features and may not exist in this file.
    MEMBER_TABLES = (
        ("proposals", "owner_sub"),
        ("snapshots", "owner_sub"),
        ("reports", "owner_sub"),
        ("scan_sessions", "owner_sub"),
        ("archidekt_links", "sub"),
        ("tokens", "sub"),
        ("browser_sessions", "sub"),
        ("idp_grants", "sub"),
        ("metrics", "sub"),
        ("users", "sub"),
    )

    def delete_member_data(self, sub: str) -> dict[str, int]:
        """Delete everything the gateway keeps about one member, at their request: proposals,
        snapshots, reports, scan sessions, the Archidekt link, every app grant and browser session,
        usage counters, the covers of their decks and the user record. The security audit log is
        kept (it ages out after a year) and records the deletion itself. Signing in again starts a
        fresh, empty account."""
        out: dict[str, int] = {}
        with self.tx() as c:
            present = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
            # Deck covers are keyed by deck and shared: only the member's own go. Those their deck
            # pages stored carry their owner_sub; a cover stored before the owner was recorded
            # (owner_sub NULL) goes when it is of a deck the member has a snapshot of, since
            # snapshots are taken only of decks the member edits, which are their own. Proposals
            # and reports can name other members' decks (a clone's source), so they are not used,
            # and a cover recorded for another member is never touched. Taken before those rows go.
            deck_ids = (
                {
                    r[0]
                    for r in c.execute("SELECT DISTINCT deck_id FROM snapshots WHERE owner_sub = ?", (sub,))
                }
                if "snapshots" in present
                else set()
            )
            covers = c.execute("DELETE FROM deck_covers WHERE owner_sub = ?", (sub,)).rowcount
            for deck_id in deck_ids:
                covers += c.execute(
                    "DELETE FROM deck_covers WHERE deck_id = ? AND owner_sub IS NULL", (deck_id,)
                ).rowcount
            out["deck_covers"] = covers
            for table, column in self.MEMBER_TABLES:
                if table in present:
                    out[table] = c.execute(f"DELETE FROM {table} WHERE {column} = ?", (sub,)).rowcount
            c.execute(
                "INSERT INTO audit_log (at, sub, client_id, event, detail_json) VALUES (?, ?, NULL, ?, ?)",
                (int(time.time()), sub, "member_data_deleted", json.dumps(out)),
            )
        return out

    def drop_member(self, sub: str, groups: list[str]) -> int:
        """The IdP just showed ``sub`` is no longer in the required group: record the groups it
        sent and revoke every token and browser session of ``sub`` now, instead of when the next
        re-sign-in comes due. Only an existing user is updated (no rows for strangers)."""
        with self.tx() as c:
            c.execute("UPDATE users SET groups_json = ? WHERE sub = ?", (json.dumps(groups), sub))
            # Their Archidekt session goes too: someone who is no longer a member should not
            # leave a usable Archidekt login behind on this server.
            c.execute("UPDATE archidekt_links SET status = 'revoked', secret_enc = '' WHERE sub = ?", (sub,))
        return sum(self.revoke_all_for_user(sub).values())

    def set_user_issuer(self, sub: str, issuer: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE users SET idp_issuer = ? WHERE sub = ?", (issuer, sub))

    def set_user_groups(self, sub: str, groups: list[str]) -> None:
        """Record the groups the identity provider reported just now."""
        with self.tx() as c:
            c.execute("UPDATE users SET groups_json = ? WHERE sub = ?", (json.dumps(groups), sub))

    # identity-provider grants ------------------------------------------------
    def save_idp_grant(self, sub: str, *, refresh_enc: str, access_enc: str, access_expires_at: int) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO idp_grants (sub, refresh_enc, access_enc, access_expires_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(sub) DO UPDATE SET refresh_enc=excluded.refresh_enc,
                     access_enc=excluded.access_enc, access_expires_at=excluded.access_expires_at,
                     updated_at=excluded.updated_at""",
                (sub, refresh_enc, access_enc, access_expires_at, int(time.time())),
            )

    def get_idp_grant(self, sub: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM idp_grants WHERE sub = ?", (sub,))
        return dict(row) if row else None

    # archidekt links -------------------------------------------------------
    def save_link(self, sub: str, *, username: str, user_id: str | None, secret_enc: str) -> None:
        now = int(time.time())
        with self.tx() as c:
            c.execute(
                """INSERT INTO archidekt_links (sub, archidekt_username, archidekt_user_id, secret_enc,
                   status, created_at, refreshed_at) VALUES (?, ?, ?, ?, 'active', ?, ?)
                   ON CONFLICT(sub) DO UPDATE SET archidekt_username=excluded.archidekt_username,
                     archidekt_user_id=excluded.archidekt_user_id, secret_enc=excluded.secret_enc,
                     status='active', refreshed_at=excluded.refreshed_at""",
                (sub, username, user_id, secret_enc, now, now),
            )

    def update_link_secret(self, sub: str, secret_enc: str, *, only_secret: str | None = None) -> bool:
        """Replace the stored session of an *active* link. False when the link was revoked or
        removed in the meantime, so a refresh that raced an unlink cannot bring it back. With
        ``only_secret``, only while the link still holds that session: a refresh that raced a
        relink (perhaps to another Archidekt account) cannot overwrite the new link."""
        sql = (
            "UPDATE archidekt_links SET secret_enc = ?, refreshed_at = ? WHERE sub = ? AND status = 'active'"
        )
        args: tuple[Any, ...] = (secret_enc, int(time.time()), sub)
        if only_secret is not None:
            sql += " AND secret_enc = ?"
            args += (only_secret,)
        with self.tx() as c:
            cur = c.execute(sql, args)
        return cur.rowcount == 1

    def get_link(self, sub: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM archidekt_links WHERE sub = ? AND status = 'active'", (sub,))
        return dict(row) if row else None

    def touch_link(self, sub: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE archidekt_links SET last_used_at = ? WHERE sub = ?", (int(time.time()), sub))

    def revoke_link(self, sub: str, *, only_secret: str | None = None) -> bool:
        """Revoke the member's link (blanking the stored session). With ``only_secret``, only
        while the link still holds that session, so a failure seen by a request that started
        before a relink cannot revoke the new link. True when a row was changed."""
        sql = "UPDATE archidekt_links SET status = 'revoked', secret_enc = '' WHERE sub = ?"
        args: tuple[Any, ...] = (sub,)
        if only_secret is not None:
            sql += " AND status = 'active' AND secret_enc = ?"
            args += (only_secret,)
        with self.tx() as c:
            return c.execute(sql, args).rowcount > 0

    # proposals and snapshots -----------------------------------------------
    def save_proposal(
        self,
        row: dict[str, Any],
        *,
        max_pending: int | None = None,
        max_closed: int | None = None,
        max_pending_per_client: int | None = None,
    ) -> bool:
        """Insert a pending proposal. With ``max_pending``, refuse it (return False) when the owner
        already has that many live pending proposals, and with ``max_pending_per_client`` when the
        app making it (``created_by_client``) already has that many for the owner; with
        ``max_closed``, delete the owner's closed proposals (expired, rejected, failed) beyond the
        newest ``max_closed``."""
        now = int(time.time())
        with self.tx() as c:
            if max_pending is not None:
                (pending,) = c.execute(
                    "SELECT COUNT(*) FROM proposals "
                    "WHERE owner_sub = ? AND state = 'pending' AND expires_at >= ?",
                    (row["owner_sub"], now),
                ).fetchone()
                if pending >= max_pending:
                    return False
            if max_pending_per_client is not None:
                mine = self.count_pending_for_client(row["owner_sub"], row.get("created_by_client"), c=c)
                if mine >= max_pending_per_client:
                    return False
            c.execute(
                """INSERT INTO proposals (id, owner_sub, kind, deck_id, deck_name, baseline_fingerprint,
                   changes_json, diff_text, state, created_at, expires_at, created_by_client, rows_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?)""",
                (
                    row["id"],
                    row["owner_sub"],
                    row.get("kind", "edit"),
                    row["deck_id"],
                    row.get("deck_name"),
                    row["baseline_fingerprint"],
                    json.dumps(row["changes"]),
                    row["diff_text"],
                    now,
                    row["expires_at"],
                    row.get("created_by_client"),
                    json.dumps(row["rows"]) if row.get("rows") is not None else None,
                ),
            )
            if max_closed is not None:
                c.execute(
                    """DELETE FROM proposals WHERE id IN (
                           SELECT id FROM proposals WHERE owner_sub = ?
                             AND (state IN ('expired', 'rejected', 'failed')
                                  OR (state = 'pending' AND expires_at < ?))
                           ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?)""",
                    (row["owner_sub"], now, max_closed),
                )
        return True

    def count_pending_proposals(self, owner_sub: str) -> int:
        """Live pending proposals of one owner (not yet expired)."""
        row = self._one(
            "SELECT COUNT(*) FROM proposals WHERE owner_sub = ? AND state = 'pending' AND expires_at >= ?",
            (owner_sub, int(time.time())),
        )
        return int(row[0]) if row else 0

    def count_pending_for_client(
        self, owner_sub: str, client_id: str | None, *, c: sqlite3.Connection | None = None
    ) -> int:
        """Live pending proposals one app (or the browser) made for the owner."""
        sql = (
            "SELECT COUNT(*) FROM proposals WHERE owner_sub = ? AND state = 'pending' AND expires_at >= ? "
            "AND created_by_client IS ?"
        )
        args = (owner_sub, int(time.time()), client_id)
        row = c.execute(sql, args).fetchone() if c is not None else self._one(sql, args)
        return int(row[0]) if row else 0

    def reject_client_proposals(self, owner_sub: str, client_id: str | None = None) -> list[str]:
        """Reject the owner's pending proposals made by one app (every app, not the browser, when
        ``client_id`` is None): the app was disconnected. Returns the rejected ids."""
        browser = "__browser__"  # decks.BROWSER_CLIENT
        with self.tx() as c:
            if client_id is None:
                where = "created_by_client IS NOT NULL AND created_by_client != ?"
                args: tuple[Any, ...] = (owner_sub, browser)
            else:
                where = "created_by_client = ?"
                args = (owner_sub, client_id)
            ids = [
                r[0]
                for r in c.execute(
                    f"SELECT id FROM proposals WHERE owner_sub = ? AND state = 'pending' AND {where}", args
                )
            ]
            for pid in ids:
                c.execute(
                    "UPDATE proposals SET state = 'rejected', applied_at = ? "
                    "WHERE id = ? AND state = 'pending'",
                    (int(time.time()), pid),
                )
        return ids

    def get_proposal(self, proposal_id: str, owner_sub: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM proposals WHERE id = ? AND owner_sub = ?", (proposal_id, owner_sub))
        if row is None:
            return None
        d = dict(row)
        d["changes"] = json.loads(d.pop("changes_json"))
        d["result"] = json.loads(d["result_json"]) if d.get("result_json") else None
        rows_json = d.pop("rows_json", None)
        d["rows"] = json.loads(rows_json) if rows_json else None
        return d

    def list_proposals(self, owner_sub: str, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, kind, deck_id, deck_name, state, created_at, expires_at, applied_at, "
                "created_by_client FROM proposals WHERE owner_sub = ? ORDER BY created_at DESC LIMIT ?",
                (owner_sub, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    def reject_proposal(self, proposal_id: str, owner_sub: str) -> bool:
        """Move pending -> rejected for the owner; False if it was not pending."""
        with self.tx() as c:
            cur = c.execute(
                "UPDATE proposals SET state = 'rejected', applied_at = ? WHERE id = ? AND owner_sub = ? "
                "AND state = 'pending' AND expires_at >= ?",
                (int(time.time()), proposal_id, owner_sub, int(time.time())),
            )
            return cur.rowcount == 1

    def claim_proposal(self, proposal_id: str, owner_sub: str) -> bool:
        """Move pending -> applying atomically; False if it was not pending (duplicate apply)."""
        with self.tx() as c:
            now = int(time.time())
            # applied_at marks when the apply started; finish_proposal overwrites it when it ends.
            cur = c.execute(
                "UPDATE proposals SET state = 'applying', applied_at = ? WHERE id = ? AND owner_sub = ? "
                "AND state = 'pending' AND expires_at >= ?",
                (now, proposal_id, owner_sub, now),
            )
            return cur.rowcount == 1

    def finish_proposal(
        self,
        proposal_id: str,
        *,
        state: str,
        snapshot_id: str | None = None,
        result: dict[str, Any] | None = None,
        deck_id: str | None = None,
    ) -> None:
        with self.tx() as c:
            # Mid-apply updates (state 'applying', recording the snapshot or the created deck) keep
            # the apply start time that claim_proposal set; the purge relies on it.
            c.execute(
                "UPDATE proposals SET state = ?, "
                "applied_at = CASE WHEN ? = 'applying' THEN applied_at ELSE ? END, "
                "snapshot_id = COALESCE(?, snapshot_id), "
                "result_json = COALESCE(?, result_json), deck_id = COALESCE(?, deck_id) WHERE id = ?",
                (
                    state,
                    state,
                    int(time.time()) if state == "applied" else None,
                    snapshot_id,
                    json.dumps(result) if result is not None else None,
                    deck_id,
                    proposal_id,
                ),
            )

    def save_snapshot(
        self,
        snapshot_id: str,
        *,
        owner_sub: str,
        deck_id: str,
        proposal_id: str | None,
        fingerprint: str,
        deck: dict[str, Any],
        while_applying: bool = False,
    ) -> bool:
        """Store a snapshot. With ``while_applying``, only while ``proposal_id`` of ``owner_sub``
        is still being applied and the member still exists: an apply that outlives "Delete my
        data" stores nothing for the deleted member. False when nothing was stored."""
        values = (
            snapshot_id,
            owner_sub,
            deck_id,
            proposal_id,
            int(time.time()),
            fingerprint,
            json.dumps(deck),
        )
        sql = """INSERT INTO snapshots (id, owner_sub, deck_id, proposal_id, taken_at, fingerprint,
                 deck_json) SELECT ?, ?, ?, ?, ?, ?, ?"""
        if while_applying:
            sql += """ WHERE EXISTS (SELECT 1 FROM users WHERE sub = ?)
                       AND EXISTS (SELECT 1 FROM proposals WHERE id = ? AND owner_sub = ?
                                   AND state = 'applying')"""
            values += (owner_sub, proposal_id, owner_sub)
        with self.tx() as c:
            return c.execute(sql, values).rowcount == 1

    def list_snapshots(self, owner_sub: str, limit: int = 20) -> list[dict[str, Any]]:
        """Newest first. Each row carries the deck name and card count read from the stored deck,
        never the deck itself (a snapshot is a whole deck; get_snapshot returns one)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, deck_id, proposal_id, taken_at, deck_json, backup_deck_id, backup_url "
                "FROM snapshots WHERE owner_sub = ? ORDER BY taken_at DESC, rowid DESC LIMIT ?",
                (owner_sub, limit),
            ).fetchall()
        out = []
        for r in rows:
            deck = json.loads(r["deck_json"])
            cards = deck.get("cards") if isinstance(deck, dict) else None
            out.append(
                {
                    "snapshot_id": r["id"],
                    "deck_id": r["deck_id"],
                    "deck_name": str(deck.get("name", "")) if isinstance(deck, dict) else "",
                    "proposal_id": r["proposal_id"],
                    "taken_at": r["taken_at"],
                    "backup_deck_id": r["backup_deck_id"],
                    "backup_url": r["backup_url"],
                    "card_count": sum(int(c.get("quantity", 0)) for c in cards if isinstance(c, dict))
                    if isinstance(cards, list)
                    else None,
                }
            )
        return out

    def set_snapshot_backup(self, snapshot_id: str, *, backup_deck_id: str, backup_url: str) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE snapshots SET backup_deck_id = ?, backup_url = ? WHERE id = ?",
                (backup_deck_id, backup_url, snapshot_id),
            )

    def get_snapshot(self, snapshot_id: str, owner_sub: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM snapshots WHERE id = ? AND owner_sub = ?", (snapshot_id, owner_sub))
        if row is None:
            return None
        d = dict(row)
        d["deck"] = json.loads(d.pop("deck_json"))
        return d

    # backup ----------------------------------------------------------------
    # -- deck covers --------------------------------------------------------------------------
    def save_deck_cover(
        self, deck_id: str, scryfall_uid: str, card_name: str = "", *, owner_sub: str | None = None
    ) -> None:
        with self.tx() as c:
            c.execute(
                "INSERT INTO deck_covers (deck_id, scryfall_uid, card_name, updated_at, owner_sub) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(deck_id) DO UPDATE SET scryfall_uid = excluded.scryfall_uid, "
                "card_name = excluded.card_name, updated_at = excluded.updated_at, "
                "owner_sub = COALESCE(excluded.owner_sub, deck_covers.owner_sub)",
                (str(deck_id), scryfall_uid, card_name[:200], int(time.time()), owner_sub),
            )

    def deck_covers(self, deck_ids: list[str]) -> dict[str, dict[str, Any]]:
        if not deck_ids:
            return {}
        ids = [str(d) for d in deck_ids][:500]
        marks = ",".join("?" * len(ids))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT deck_id, scryfall_uid, card_name FROM deck_covers WHERE deck_id IN ({marks})", ids
            ).fetchall()
        return {r["deck_id"]: {"scryfall_uid": r["scryfall_uid"], "card_name": r["card_name"]} for r in rows}

    def backup_to(self, dest: Path) -> None:
        """Copy the database to ``dest`` with SQLite's online backup API and check the copy.

        A file database is read through a second connection: in WAL mode it sees one consistent
        snapshot while the gateway keeps serving requests, instead of holding the shared lock (and
        every request waiting on it) for the whole copy. Raises if the copy fails its integrity check.
        """
        dest.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(str(dest))
        try:
            if str(self.path) == ":memory:":
                with self._lock:
                    self._conn.backup(target)
            else:
                source = sqlite3.connect(str(self.path))
                try:
                    source.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
                    source.backup(target)
                finally:
                    source.close()
            # The copy must not need the WAL beside it: make it a plain rollback-journal file.
            target.execute("PRAGMA journal_mode=DELETE")
            result = target.execute("PRAGMA quick_check").fetchone()[0]
            if result != "ok":
                raise RuntimeError(f"backup copy failed its integrity check: {result}")
        finally:
            target.close()

    def integrity_ok(self) -> bool:
        """PRAGMA quick_check on the live database (read-only, cheap on a small file)."""
        with self._lock:
            return self._conn.execute("PRAGMA quick_check").fetchone()[0] == "ok"

    def size_bytes(self) -> int | None:
        if str(self.path) == ":memory:":
            return None
        total = 0
        for suffix in ("", "-wal"):
            try:
                total += Path(f"{self.path}{suffix}").stat().st_size
            except OSError:
                pass
        return total
