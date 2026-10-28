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
import logging
import sqlite3
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import upgrade_backup
from .cimd import site_of
from .modes import UNJUDGED

# Database.read_ahead: the links and client names a listing is about to ask for row by row, read
# in one query each. Context variables, so a read-ahead belongs to the request (task) that made it
# and never serves another.
_links_ahead: ContextVar[dict[str, dict[str, list[dict[str, Any]]]] | None] = ContextVar(
    "links_ahead", default=None
)
_names_ahead: ContextVar[dict[str, str | None] | None] = ContextVar("names_ahead", default=None)

logger = logging.getLogger(__name__)

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
# By default, a browser or Android app sign-in ends this long after it was made, however much it is
# used (renew_browser_session); the Settings page's "Longest website sign-in" changes it
# (Database.session_max_age), and "Website sign-in when unused" is how long it lasts unused.
BROWSER_SESSION_MAX_AGE = 30 * 86400
# Why a browser session ended, as the page its browser sees next says (pages.py, /signed-out):
# it went unused too long, it reached the longest sign-in time, the identity provider no longer
# honours its sign-in, the member is no longer in the group, they signed out on all devices from
# another one, an admin signed them out, or an admin turned their account off.
SESSION_END_REASONS = ("unused", "longest", "idp", "removed", "elsewhere", "admin", "disabled")
# How long that is remembered, and how long a session cookie outlives its session so the browser
# still brings it back to be told (the cookie is worth nothing once its session has ended).
SESSION_END_NOTICE = 30 * 86400
# The provider grant of a browser session is held by "s:" and the session's id hash (membership.py).
SESSION_HOLDER = "s:"
# A sign-in's grant waits under "login:" until its session is made; one never handed over goes then.
LOGIN_GRANT_TTL = 86400


def _expiry_reason(created: int, expires: int, longest: int) -> str:
    """Why a session whose end time has passed ended: it ran into the longest sign-in time (its end
    was capped there, give or take a minute), or else it went unused."""
    return "longest" if expires >= created + longest - 60 else "unused"


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
# Applied proposals are the record of what was changed and are kept longer, for as long as the
# audit log: they are deleted a year after they were applied.
APPLIED_PROPOSAL_RETENTION_SECONDS = 365 * 86400
# Snapshots are whole decks taken before every edit. The newest SNAPSHOTS_KEEP_PER_DECK of each
# member's deck are always kept (and any a pending restore still needs); older ones are deleted.
SNAPSHOTS_KEEP_PER_DECK = 25
# Rows deleted per transaction by the purge's plain deletes, so the lock is
# released between batches.
PURGE_BATCH = 500
# Per-day usage counters and remembered deck covers are kept this long.
METRICS_RETENTION_SECONDS = 400 * 86400
DECK_COVER_RETENTION_SECONDS = 180 * 86400
# Daily price points and watched decks' values are kept this many days (Database.price_days, set
# from the price_history_days setting by create_app).
PRICE_RETENTION_DAYS = 365
# How long a write waits for another connection (such as `mtg-gateway backup`) to finish.
BUSY_TIMEOUT_MS = 5000
# Remembered choices that follow the account: how many one account may keep and how large
# one may be, so a page script cannot grow the database without bound.
MAX_PREFS_PER_ACCOUNT = 300
MAX_PREF_BYTES = 4096
MAX_PREF_NAME = 120
# One simulator card model (card_models.model_json, schema 14): the shape Mystic Forge validated,
# at most this many bytes as JSON.
MAX_CARD_MODEL_BYTES = 4096
# Card models read in one query (get_card_models), under SQLite's limit on bound values.
_CARD_MODEL_BATCH = 500


class CardModelRefused(Exception):
    """put_card_model wrote nothing: ``why`` is "most" (the save would add a model and the member
    already keeps ``most``)."""

    def __init__(self, why: str) -> None:
        super().__init__(why)
        self.why = why


# Earlier versions kept per waiting proposal (proposal_versions): past it an amend is refused, so an
# app that keeps amending one note can't grow the database without bound.
MAX_PROPOSAL_VERSIONS = 50


#: States of a note that hold the notes depending on it. "expired" holds them too.
HOLDING_STATES = ("rejected", "withdrawn", "failed", "expired")

# A note's state as the member sees it: a waiting one past its expiry is "expired" even before
# the prune marks it so (the ``o`` alias, with the time as ``:now``).
_SEEN_STATE = "(CASE WHEN o.state = 'pending' AND o.expires_at < :now THEN 'expired' ELSE o.state END)"
_HOLDING_SQL = "(" + ", ".join(f"'{s}'" for s in HOLDING_STATES) + ")"


def _blocking_dependency(c: sqlite3.Connection, proposal_id: str) -> dict[str, Any] | None:
    """The first note ``proposal_id`` depends on that is rejected, withdrawn, failed or expired
    (its id, board number and state), or None."""
    row = c.execute(
        f"SELECT o.id, o.board_no, {_SEEN_STATE} AS state FROM proposal_links l "
        f"JOIN proposals o ON o.id = l.other_id WHERE l.proposal_id = :pid AND l.kind = 'depends_on' "
        f"AND {_SEEN_STATE} IN {_HOLDING_SQL} ORDER BY l.created_at, l.rowid LIMIT 1",
        {"pid": proposal_id, "now": int(time.time())},
    ).fetchone()
    return dict(row) if row else None


def hold_reason(dep: dict[str, Any]) -> str:
    """The stored reason of a hold: ``depends:<id>:<state>``, worded when shown."""
    return f"depends:{dep['id']}:{dep['state']}"


# replaced_hold_reason in SQL, for the lazy hold (hold_blocked)
GONE_REASON_SQL = "'replaces:' || o.id || ':applied'"


def replaced_hold_reason(proposal_id: str) -> str:
    """The stored reason of a hold on a note whose replaced note was applied on its own:
    ``replaces:<id>:applied``."""
    return f"replaces:{proposal_id}:applied"


class ProposalVersionsFull(Exception):
    """amend_proposal refused: the proposal already keeps MAX_PROPOSAL_VERSIONS earlier versions."""


# Proposal states that end a proposal without applying it. A ``replaced`` one (0.7.29, the
# assistant board) comes back when its replacement is rejected or withdrawn; all are deleted after
# CLOSED_PROPOSAL_RETENTION_SECONDS.
CLOSED_PROPOSAL_STATES = ("expired", "rejected", "failed", "withdrawn", "replaced")
# Waiting statuses of a pending proposal (0.7.29 flexible approvals): None is plain waiting.
PROPOSAL_STATUSES = ("updated", "needs_look", "held")
PROPOSAL_LINK_KINDS = ("depends_on", "replaces")


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
    # Clients already in use before this column existed: a token was issued to them, so a member
    # completed a sign-in through them. Without this they would count as first-time clients.
    conn.execute(
        "UPDATE cimd_clients SET signed_in_at = "
        "(SELECT MAX(t.created_at) FROM tokens t WHERE t.client_id = cimd_clients.client_id) "
        "WHERE signed_in_at IS NULL"
    )


def _step_11(conn: sqlite3.Connection) -> None:
    """Approval modes (modes.py): the member's own mode, NULL until they choose one on the
    Account page (then the gateway's default applies)."""
    _add_column(conn, "users", "approval_mode", "TEXT")


def _step_12(conn: sqlite3.Connection) -> None:
    """Watchlists, price history and notifications (prices.py, notify.py): the cards and decks a
    member watches, one price point per watched printing a day (shared: a price is not personal),
    each watched deck's value a day, the member's notification and summary choices, and the day
    each daily job last ran."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS watch_cards (
            id TEXT PRIMARY KEY,
            sub TEXT NOT NULL,
            name TEXT NOT NULL,
            set_code TEXT NOT NULL DEFAULT '',
            collector_number TEXT NOT NULL DEFAULT '',
            finish TEXT NOT NULL DEFAULT 'nonfoil',
            scryfall_id TEXT,
            alert_pct REAL,
            alert_above REAL,
            alert_below REAL,
            created_at INTEGER NOT NULL,
            UNIQUE (sub, name, set_code, collector_number, finish)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS watch_cards_sub ON watch_cards(sub)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS watch_decks (
            sub TEXT NOT NULL,
            deck_id TEXT NOT NULL,
            deck_name TEXT NOT NULL DEFAULT '',
            cards_json TEXT NOT NULL DEFAULT '[]',
            refreshed_at INTEGER,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (sub, deck_id)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS price_points (
            scryfall_id TEXT NOT NULL,
            day TEXT NOT NULL,
            usd REAL,
            usd_foil REAL,
            usd_etched REAL,
            eur REAL,
            eur_foil REAL,
            PRIMARY KEY (scryfall_id, day)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS price_points_day ON price_points(day)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS deck_values (
            sub TEXT NOT NULL,
            deck_id TEXT NOT NULL,
            day TEXT NOT NULL,
            usd REAL,
            eur REAL,
            cards INTEGER NOT NULL DEFAULT 0,
            priced INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (sub, deck_id, day)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS notify_prefs (
            sub TEXT PRIMARY KEY,
            ntfy_on INTEGER NOT NULL DEFAULT 0,
            ntfy_topic TEXT UNIQUE,
            on_proposals INTEGER NOT NULL DEFAULT 1,
            on_reports INTEGER NOT NULL DEFAULT 1,
            on_prices INTEGER NOT NULL DEFAULT 1,
            summary_every TEXT NOT NULL DEFAULT 'off',
            summary_sent_day TEXT,
            updated_at INTEGER NOT NULL
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS job_runs (
            name TEXT PRIMARY KEY,
            day TEXT NOT NULL,
            at INTEGER NOT NULL,
            detail_json TEXT
        )"""
    )


def snapshot_summary(deck: Any) -> tuple[str, int | None, str | None]:
    """What a snapshot list shows of a stored deck, worked out once when the snapshot is stored:
    its name, its card count (None when the deck has no card list) and the Archidekt
    account that owned the deck then (None when not recorded)."""
    if not isinstance(deck, dict):
        return "", None, None
    name = deck.get("name")
    cards = deck.get("cards")
    count: int | None = None
    if isinstance(cards, list):
        count = 0
        for c in cards:
            if isinstance(c, dict):
                try:
                    count += int(c.get("quantity", 0) or 0)
                except (TypeError, ValueError):
                    pass
    owner = deck.get("owner")
    owner_id = owner.get("id") if isinstance(owner, dict) else None
    return (str(name) if name is not None else ""), count, (str(owner_id) if owner_id is not None else None)


# Rows read at a time while schema 13 fills the new snapshot columns: a snapshot is a whole deck
# (tens to hundreds of KB), so a large file is read a slice at a time, never all at once.
_STEP_13_BATCH = 200


def _step_13(conn: sqlite3.Connection) -> None:
    """0.7.29, the one upgrade for settings and the new approvals.

    - ``settings``: values the server owner sets on the admin Settings page (config.py
      MANAGED_SETTINGS; a value set in the deployment always wins and is never stored here).
    - ``account_prefs``: remembered choices that follow the account, one row per account and
      choice name, so a new choice needs no upgrade.
    - proposals: a version, who amended it last and what that version changed, a waiting status
      with its reason, a number on the deck's board, the Archidekt account it was made under,
      past versions
      (``proposal_versions``) and links between proposals (``proposal_links``: depends on,
      replaces); and the ``(state, expires_at)`` index of the pending queries.
    - snapshots: the deck's name, card count and owner, stored when taken so lists don't parse
      every stored deck, and the Archidekt account it was taken under.
    - ``backup_deletes``: the queued deletes of unused backup copies, moved out of
      ``job_runs`` into a table of their own with the Archidekt account they belong to.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            updated_by TEXT
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS account_prefs (
            sub TEXT NOT NULL,
            name TEXT NOT NULL,
            value_json TEXT NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (sub, name)
        )"""
    )
    for column, decl in (
        ("version", "INTEGER NOT NULL DEFAULT 1"),
        ("amended_at", "INTEGER"),
        ("amended_by_client", "TEXT"),
        ("version_note", "TEXT"),
        ("status", "TEXT"),
        ("status_reason", "TEXT"),
        ("status_at", "INTEGER"),
        ("board_no", "INTEGER"),
        ("archidekt_user_id", "TEXT"),
    ):
        _add_column(conn, "proposals", column, decl)
    conn.execute("CREATE INDEX IF NOT EXISTS proposals_state_expires ON proposals(state, expires_at)")
    # The proposals waiting (or being applied) at the upgrade get their board numbers now, oldest
    # first per member and deck, so the next new one follows them instead of starting at 1.
    conn.execute(
        """UPDATE proposals SET board_no = (
             SELECT n FROM (
               SELECT id, ROW_NUMBER() OVER (
                 PARTITION BY owner_sub, deck_id ORDER BY created_at, rowid) AS n
               FROM proposals WHERE state IN ('pending', 'applying')) AS numbered
             WHERE numbered.id = proposals.id)
           WHERE state IN ('pending', 'applying')"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS proposal_versions (
            proposal_id TEXT NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
            version INTEGER NOT NULL,
            changes_json TEXT NOT NULL,
            rows_json TEXT,
            diff_text TEXT NOT NULL,
            made_by_client TEXT,
            made_at INTEGER NOT NULL,
            what_changed TEXT,
            PRIMARY KEY (proposal_id, version)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS proposal_links (
            proposal_id TEXT NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
            kind TEXT NOT NULL CHECK (kind IN ('depends_on', 'replaces')),
            other_id TEXT NOT NULL REFERENCES proposals(id) ON DELETE CASCADE,
            created_at INTEGER NOT NULL,
            PRIMARY KEY (proposal_id, kind, other_id)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS proposal_links_other ON proposal_links(other_id, kind)")
    for column, decl in (
        ("deck_name", "TEXT"),
        ("card_count", "INTEGER"),
        ("deck_owner_id", "TEXT"),
        ("archidekt_user_id", "TEXT"),
    ):
        _add_column(conn, "snapshots", column, decl)
    # Fill the summary of every snapshot taken before this upgrade, a slice at a time (the whole
    # step is one transaction, so an interrupted upgrade leaves nothing half done and runs again).
    last = -1
    while True:
        rows = conn.execute(
            "SELECT rowid, deck_json FROM snapshots WHERE rowid > ? ORDER BY rowid LIMIT ?",
            (last, _STEP_13_BATCH),
        ).fetchall()
        if not rows:
            break
        last = rows[-1][0]
        for rowid, deck_json in rows:
            try:
                deck = json.loads(deck_json)
            except (TypeError, ValueError):
                deck = None
            name, count, owner_id = snapshot_summary(deck)
            conn.execute(
                "UPDATE snapshots SET deck_name = ?, card_count = ?, deck_owner_id = ? WHERE rowid = ?",
                (name, count, owner_id, rowid),
            )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS backup_deletes (
            snapshot_id TEXT PRIMARY KEY,
            sub TEXT NOT NULL,
            archidekt_user_id TEXT,
            backup_deck_id TEXT NOT NULL,
            deck_id TEXT NOT NULL,
            proposal_id TEXT,
            due_at INTEGER NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            queued_at INTEGER NOT NULL,
            expect_json TEXT
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS backup_deletes_due ON backup_deletes(due_at)")
    conn.execute("CREATE INDEX IF NOT EXISTS backup_deletes_sub ON backup_deletes(sub)")
    # Move the queued deletes kept in job_runs since 0.7.27. Every relink drops a member's queued
    # deletes (decks.py _drop_backup_deletes_on_relink), so each one left belongs to the Archidekt
    # account on the member's link row (active or not); empty where that row recorded none.
    moved = conn.execute(
        "SELECT name, at, detail_json FROM job_runs WHERE name LIKE ? ESCAPE '\\'",
        (_like_prefix(BACKUP_DELETE_JOB),),
    ).fetchall()
    for name, at, detail_json in moved:
        try:
            d = json.loads(detail_json or "{}")
        except ValueError:
            d = {}
        if not isinstance(d, dict):
            d = {}
        snapshot_id = str(d.get("snapshot_id") or name[len(BACKUP_DELETE_JOB) :])
        sub = d.get("sub")
        backup_deck_id = d.get("backup_deck_id")
        if not sub or backup_deck_id is None:
            continue  # unusable: the old code could not have deleted anything with it either
        account = conn.execute(
            "SELECT archidekt_user_id FROM archidekt_links WHERE sub = ?", (str(sub),)
        ).fetchone()
        try:
            attempts = int(d.get("attempts") or 0)
        except (TypeError, ValueError):
            attempts = 0
        try:
            queued_at = int(d.get("queued_at") or at)
        except (TypeError, ValueError):
            queued_at = int(at)
        conn.execute(
            """INSERT OR IGNORE INTO backup_deletes (snapshot_id, sub, archidekt_user_id, backup_deck_id,
               deck_id, proposal_id, due_at, attempts, queued_at, expect_json)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                snapshot_id,
                str(sub),
                account[0] if account else None,
                str(backup_deck_id),
                str(d.get("deck_id") or ""),
                str(d["proposal_id"]) if d.get("proposal_id") is not None else None,
                int(at),
                attempts,
                queued_at,
                json.dumps(d["expect"]) if isinstance(d.get("expect"), dict) else None,
            ),
        )
    conn.execute("DELETE FROM job_runs WHERE name LIKE ? ESCAPE '\\'", (_like_prefix(BACKUP_DELETE_JOB),))


def _step_14(conn: sqlite3.Connection) -> None:
    """0.7.30, the one small upgrade the simulator needs: ``card_models``, the models
    members taught the simulator for cards it plays without their effect. Each member keeps their
    own: one model per card per member, keyed by the card's front face,
    casefolded, and the member, and used only in that member's simulations.

    ``sim_version`` is the simulator version the model was checked with, so models checked
    with an older simulator can be found when it changes; ``oracle_hash``
    is a spare column for a fingerprint of the card's rules text.

    ``added_by`` is the member who keeps the model (never shown to other members). It has no
    foreign key, like every member table but the proposals: a member's models go with their data
    through MEMBER_TABLES.
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS card_models (
            card_key TEXT NOT NULL,
            name TEXT NOT NULL,
            model_json TEXT NOT NULL CHECK (length(CAST(model_json AS BLOB)) <= 4096),
            sim_version TEXT,
            oracle_hash TEXT,
            added_by TEXT NOT NULL,
            added_at INTEGER NOT NULL,
            updated_at INTEGER NOT NULL,
            PRIMARY KEY (card_key, added_by)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_card_models_added_by ON card_models(added_by)")


def _card_models_need_repair(conn: sqlite3.Connection) -> bool:
    """Whether ``card_models`` exists in the shape of an earlier build of schema 14 (no
    ``sim_version``, or not keyed by the card and the member)."""
    cols = {r[1]: r[5] for r in conn.execute("PRAGMA table_info(card_models)")}
    return bool(cols) and not ("sim_version" in cols and cols.get("card_key") and cols.get("added_by"))


def _repair_card_models(conn: sqlite3.Connection) -> bool:
    """Bring a ``card_models`` table written by an earlier build of schema 14 to the shape
    ``_step_14`` makes now. Step 14 changed in place before any release carried it: those
    builds kept one model per card for the whole gateway (``card_key``
    alone the key) and had no ``sim_version``, so a test database from them would fail every read
    with "no such column". Each kept model stays, kept by the member who added it, with no simulator
    version recorded. Runs at every start and does nothing once the table has the new shape, so the
    schema number stays 14. Returns whether it changed the table."""
    if not _card_models_need_repair(conn):
        return False
    conn.execute("ALTER TABLE card_models RENAME TO card_models_old")
    conn.execute("DROP INDEX IF EXISTS idx_card_models_added_by")
    _step_14(conn)
    conn.execute(
        """INSERT INTO card_models (card_key, name, model_json, sim_version, oracle_hash, added_by,
               added_at, updated_at)
           SELECT card_key, name, model_json, NULL, oracle_hash, added_by, added_at, updated_at
           FROM card_models_old"""
    )
    conn.execute("DROP TABLE card_models_old")
    return True


def _step_15(conn: sqlite3.Connection) -> None:
    """The Proposals page (UI standard): each closed proposal's risk tier (low, high, consent or
    destructive), stored when it closes, so the page's Risk filter on applied, rejected and the
    rest is a plain query. A waiting one's risk is worked out on every read (it follows the live
    settings and its amends). Proposals already closed get theirs from the one-time backfill that
    runs once the gateway's settings are loaded (Database.settle_risks)."""
    _add_column(conn, "proposals", "risk", "TEXT")


def _step_16(conn: sqlite3.Connection) -> None:
    """0.7.46, one live card per proposal: ``card_seqs`` counts, per app (client id), the cards
    drawn in that app's chat for a proposal, as a JSON object ``{client: n}``. Each card knows
    its own number, and a card that reads a higher one for its app shows a short line instead of
    its buttons ("Replaced by a newer version below"), so only the newest card asks for a press.
    Proposals made before have none: their cards keep working as before."""
    _add_column(conn, "proposals", "card_seqs", "TEXT")


def _step_17(conn: sqlite3.Connection) -> None:
    """0.7.46, sign-outs: the identity provider's tokens are kept per sign-in, not one set per
    member. ``holder`` names whose they are: ``s:`` and a browser session's id hash (a browser or
    the Android app), ``ai:`` and the member (their latest AI-app sign-in), ``login:`` and a login
    id's hash (a sign-in not yet handed to its session). A grant the provider stops honouring then
    ends only the sign-in that holds it. ``checked_at`` is when the provider last let that holder
    in, so the first page after a restart is not kept waiting either. The one grant per member kept
    until now becomes their ``ai:`` grant, which their sessions from before this step use (they
    have none of their own) until they end.

    ``session_ends`` records why a browser session ended (``reason``), by its id hash only, so the
    page a returning browser sees can say why it was signed out (pages.py, /signed-out)."""
    if "holder" not in _columns(conn, "idp_grants"):
        conn.execute("ALTER TABLE idp_grants RENAME TO idp_grants_old")
        conn.execute(
            """CREATE TABLE idp_grants (
                holder TEXT PRIMARY KEY,
                sub TEXT NOT NULL,
                refresh_enc TEXT NOT NULL DEFAULT '',
                access_enc TEXT NOT NULL DEFAULT '',
                access_expires_at INTEGER NOT NULL DEFAULT 0,
                updated_at INTEGER NOT NULL,
                checked_at INTEGER
            )"""
        )
        conn.execute(
            """INSERT INTO idp_grants (holder, sub, refresh_enc, access_enc, access_expires_at, updated_at)
               SELECT 'ai:' || sub, sub, refresh_enc, access_enc, access_expires_at, updated_at
               FROM idp_grants_old"""
        )
        conn.execute("DROP TABLE idp_grants_old")
    conn.execute("CREATE INDEX IF NOT EXISTS idp_grants_sub ON idp_grants(sub)")
    conn.execute(
        """CREATE TABLE IF NOT EXISTS session_ends (
            id_hash TEXT PRIMARY KEY,
            reason TEXT NOT NULL,
            ended_at INTEGER NOT NULL
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS session_ends_at ON session_ends(ended_at)")


def _step_18(conn: sqlite3.Connection) -> None:
    """0.7.46, Binder prices (binder_prices.py, prices.py): what the daily price check prices for a
    member beyond their watchlist, and the cheapest printing of a watch by name alone.

    - ``binder_records``: the member's Binder as last read whole from Archidekt (once a day while
      they use the Binder), one row per record: the printing, finish and copies the value and the
      price sort need, and the row as the page draws it (``row_json``). The gateway's own Binder
      changes keep it current between reads.
    - ``binder_reads``: per member, when they last opened the Binder (``seen_at``: only the Binders
      in use are read and priced), when it was last read whole and how many records that read held.
    - ``owned_decks``: the printings in each of the member's own decks as last read (any deck
      page view), so the card viewer has a price history for deck cards too.
    - ``watch_cards``: the card's Scryfall oracle id, and for a watch by name alone the printing
      that was cheapest at the last check (its points are kept under ``min:`` and the oracle id).
    """
    conn.execute(
        """CREATE TABLE IF NOT EXISTS binder_records (
            sub TEXT NOT NULL,
            rid INTEGER NOT NULL,
            scryfall_id TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            finish TEXT NOT NULL DEFAULT 'nonfoil',
            quantity INTEGER NOT NULL DEFAULT 0,
            row_json TEXT NOT NULL DEFAULT '{}',
            PRIMARY KEY (sub, rid)
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS binder_reads (
            sub TEXT PRIMARY KEY,
            seen_at INTEGER,
            read_at INTEGER,
            records INTEGER NOT NULL DEFAULT 0,
            complete INTEGER NOT NULL DEFAULT 0
        )"""
    )
    conn.execute(
        """CREATE TABLE IF NOT EXISTS owned_decks (
            sub TEXT NOT NULL,
            deck_id TEXT NOT NULL,
            deck_name TEXT NOT NULL DEFAULT '',
            cards_json TEXT NOT NULL DEFAULT '[]',
            read_at INTEGER NOT NULL,
            PRIMARY KEY (sub, deck_id)
        )"""
    )
    for column in ("oracle_id", "cheapest_id", "cheapest_set", "cheapest_cn"):
        _add_column(conn, "watch_cards", column, "TEXT")


# Applied in order; ``PRAGMA user_version`` records how many have run. Append, never edit.
MIGRATIONS = [
    _step_1,
    _step_2,
    _step_3,
    _step_4,
    _step_5,
    _step_6,
    _step_7,
    _step_8,
    _step_9,
    _step_10,
    _step_11,
    _step_12,
    _step_13,
    _step_14,
    _step_15,
    _step_16,
    _step_17,
    _step_18,
]
SCHEMA_VERSION = len(MIGRATIONS)


def hash_token(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _like(search: str) -> str:
    """A LIKE pattern matching ``search`` anywhere, with its wildcards escaped (ESCAPE '\\')."""
    text = str(search).strip()[:200]
    text = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{text}%"


def _marks(values: tuple[Any, ...] | list[Any]) -> str:
    return ",".join("?" * len(values))


def _like_prefix(prefix: str) -> str:
    """A LIKE pattern matching what starts with ``prefix``, its wildcards escaped (ESCAPE '\\')."""
    return prefix.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


# job_runs rows recording how each backup copy was made (record_backup_made), by the copy's id.
BACKUP_MADE_JOB = "backup_made:"
# Why a snapshot can't be deleted now (Database.snapshot_held), in the member's words.
# The proposals History lists as changes to a deck once applied (owner decision C7: History is the
# deck's timeline); account-wide ones (lists, collection, Archidekt actions) are not a deck's.
DECK_CHANGE_KINDS = ("edit", "restore", "details", "categories", "create_deck")
SNAPSHOT_HELD_RESTORE = (
    "A restore that is waiting puts the deck back to this snapshot, so it can't be deleted. Reject "
    "or apply that restore first."
)
SNAPSHOT_HELD_BACKUP = (
    "The gateway still needs this snapshot to tidy up its backup copy on Archidekt, so it can't be "
    "deleted yet."
)
# job_runs rows recording each Archidekt folder the gateway put a member's backup copies in
# (record_backup_folder), by member and folder id: a renamed backup folder stays known as one
BACKUP_FOLDER_JOB = "backup_folder:"
# job_runs rows of the queued backup deletes before schema 13 moved them to backup_deletes:
# this prefix and the snapshot id (read only by _step_13)
BACKUP_DELETE_JOB = "backup_delete:"


# Tables a member's own rows live in, and the column naming the member. reports and scan_sessions
# belong to optional features and may not exist in this file.
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
    ("watch_cards", "sub"),
    ("watch_decks", "sub"),
    ("deck_values", "sub"),
    ("binder_records", "sub"),  # their Binder as last read, for its value
    ("binder_reads", "sub"),
    ("owned_decks", "sub"),  # the printings of their decks, for the price history
    ("notify_prefs", "sub"),
    ("account_prefs", "sub"),
    ("card_models", "added_by"),  # their simulator card models go too
    ("backup_deletes", "sub"),  # their queued backup deletes go with their snapshots
    ("users", "sub"),
)


def delete_member_rows(c: sqlite3.Connection, sub: str) -> dict[str, int]:
    """Delete one member's rows (Database.delete_member_data) inside the caller's transaction on
    ``c``; going back to a before-upgrade copy uses it too (upgrade_backup.go_back)."""
    out: dict[str, int] = {}
    present = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    # Deck covers are keyed by deck and shared: only the member's own go. Those their deck
    # pages stored carry their owner_sub; a cover stored before the owner was recorded
    # (owner_sub NULL) goes when it is of a deck the member has a snapshot of, since
    # snapshots are taken only of decks the member edits, which are their own. Proposals
    # and reports can name other members' decks (a clone's source), so they are not used,
    # and a cover recorded for another member is never touched. Taken before those rows go.
    deck_ids = (
        {r[0] for r in c.execute("SELECT DISTINCT deck_id FROM snapshots WHERE owner_sub = ?", (sub,))}
        if "snapshots" in present
        else set()
    )
    covers = c.execute("DELETE FROM deck_covers WHERE owner_sub = ?", (sub,)).rowcount
    for deck_id in deck_ids:
        covers += c.execute(
            "DELETE FROM deck_covers WHERE deck_id = ? AND owner_sub IS NULL", (deck_id,)
        ).rowcount
    out["deck_covers"] = covers
    # The records of their backup copies (each holds the deck's
    # name and URL) go now, not at the next hourly clean-up: those their snapshots name,
    # and those recorded for them. Taken before the snapshots go.
    out["backup_records"] = (
        c.execute(
            """DELETE FROM job_runs WHERE substr(name, 1, ?) = ?
            AND (CASE WHEN json_valid(detail_json) THEN json_extract(detail_json, '$.sub') END = ?
                 OR substr(name, ? + 1) IN (
                     SELECT backup_deck_id FROM snapshots
                     WHERE owner_sub = ? AND backup_deck_id IS NOT NULL))""",
            (len(BACKUP_MADE_JOB), BACKUP_MADE_JOB, sub, len(BACKUP_MADE_JOB), sub),
        ).rowcount
        if "snapshots" in present
        else 0
    )
    # and the records of the folders the gateway put their copies in
    if "job_runs" in present:
        out["backup_folders"] = c.execute(
            "DELETE FROM job_runs WHERE substr(name, 1, ?) = ?",
            (len(BACKUP_FOLDER_JOB) + len(sub) + 1, f"{BACKUP_FOLDER_JOB}{sub}:"),
        ).rowcount
    for table, column in MEMBER_TABLES:
        if table in present:
            out[table] = c.execute(f"DELETE FROM {table} WHERE {column} = ?", (sub,)).rowcount
    c.execute(
        "INSERT INTO audit_log (at, sub, client_id, event, detail_json) VALUES (?, ?, NULL, ?, ?)",
        (int(time.time()), sub, "member_data_deleted", json.dumps(out)),
    )
    return out


class Database:
    def __init__(self, path: Path | str, *, upgrade_dir: Path | None = None):
        """``upgrade_dir`` is the before-upgrade folder (upgrade_backup.py); None puts it next to
        the database file."""
        self.path = Path(path)
        self.upgrade_dir = upgrade_dir
        # Days the before-upgrade copies are kept (the app wires in backup_keep_days); None
        # keeps them until newer ones push them out.
        self.upgrade_keep_days: int | None = None
        if str(self.path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._anonymous_audit_inserts = 0
        self.price_days = PRICE_RETENTION_DAYS
        # Snapshots kept per deck; None means SNAPSHOTS_KEEP_PER_DECK (the app wires the live
        # setting in, snapshots_keep()).
        self.snapshots_per_deck: int | None = None
        self.snapshots_source: Callable[[], int] | None = None
        # the longest a sign-in lasts, in seconds, from the Settings page (create_app); None: the default
        self.session_max_age_source: Callable[[], int] | None = None
        # A closed proposal's risk tier from its stored row (get_proposal's shape), stored when it
        # closes (settle_risks); the app wires in DeckService.stored_risk. None stores nothing.
        self.risk_source: Callable[[dict[str, Any]], str] | None = None
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            if str(self.path) != ":memory:":
                self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
            self._conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
            # Deleted or blanked rows (an unlinked Archidekt session) are overwritten on disk, not
            # just marked free; SQLite's default for this depends on how it was built.
            self._conn.execute("PRAGMA secure_delete=ON")
            try:
                self._migrate()
                upgrade_backup.went_forward(self._conn)
            except Exception:
                self._conn.close()
                raise

    @property
    def upgrade_folder(self) -> Path | None:
        """The before-upgrade folder (upgrade_backup.py), or None for a database in memory."""
        if str(self.path) == ":memory:":
            return None
        return self.upgrade_dir if self.upgrade_dir is not None else self.path.parent / upgrade_backup.FOLDER

    def scrub_upgrade_copies(self, sub: str, what: str) -> int:
        """End in the before-upgrade and set-aside copies what just ended for ``sub`` here
        (upgrade_backup.scrub_member): "link", "account", "phone" or "data"."""
        folder = self.upgrade_folder
        if folder is None:
            return 0
        return upgrade_backup.scrub_member(folder, sub, what, delete_member_rows, len(MIGRATIONS))

    def reconcile_upgrade_copies(self) -> int:
        """Bring the copies in line with this database (upgrade_backup.reconcile): at start, so
        what ended under an older version, or in a change cut short, is ended in them too."""
        folder = self.upgrade_folder
        if folder is None:
            return 0
        with self._lock:
            return upgrade_backup.reconcile(folder, self._conn, delete_member_rows, len(MIGRATIONS))

    def age_out_upgrade_copies(self) -> int:
        """Delete the before-upgrade and set-aside copies older than ``upgrade_keep_days``."""
        folder = self.upgrade_folder
        if folder is None or self.upgrade_keep_days is None:
            return 0
        return upgrade_backup.age_out(folder, self.upgrade_keep_days)

    def _migrate(self) -> None:
        """Run the migration steps this database has not seen (``PRAGMA user_version``), after
        saving a copy of a database that already holds data (upgrade_backup.py)."""
        version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        on_disk = str(self.path) != ":memory:"
        folder = (
            self.upgrade_dir if self.upgrade_dir is not None else self.path.parent / upgrade_backup.FOLDER
        )
        if version > len(MIGRATIONS):
            # A newer gateway wrote this file. An older image would skip columns it doesn't know,
            # such as users.disabled_at, and let disabled people back in, so it puts back the copy
            # saved before that upgrade, or refuses.
            if not on_disk or not upgrade_backup.go_back(
                self._conn, self.path, folder, version, len(MIGRATIONS), delete_member_rows
            ):
                raise upgrade_backup.DatabaseUpgradeError(
                    f"database schema version {version} is newer than this gateway supports "
                    f"({len(MIGRATIONS)}), and {folder} has no copy of the database from before it was "
                    "upgraded. Run the newer version again, or put a copy back by hand "
                    "(docs/UPGRADING.md, “Going back to an older version”)."
                )
            self._conn.execute("PRAGMA journal_mode=WAL")
            version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
        if version < len(MIGRATIONS) and on_disk and upgrade_backup.has_data(self._conn):
            # a set-aside copy put back on a newer version: its sign-ins end, and its mark stays
            # out of the copy saved now
            upgrade_backup.went_forward(self._conn)
            upgrade_backup.save_before_upgrade(self._conn, self.path, folder, version, len(MIGRATIONS))
        if version < 1:
            # executescript commits on its own, so step 1 can't share a transaction. Every
            # statement in it is idempotent, so an interrupted run simply repeats it.
            MIGRATIONS[0](self._conn)
            self._conn.execute("PRAGMA user_version = 1")
            version = 1
        if version < len(MIGRATIONS):
            # Every other step runs in one transaction with the version bump: the upgrade happens
            # whole or not at all, so a failed or killed one leaves the database as it was.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                # read again under the lock: another process may have upgraded it meanwhile
                version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
                for number, step in enumerate(MIGRATIONS[version:], start=version + 1):
                    step(self._conn)
                    self._conn.execute(f"PRAGMA user_version = {number}")
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            self._conn.execute("COMMIT")
        # an earlier build of schema 14 (see _repair_card_models); its own transaction, as a step
        if not _card_models_need_repair(self._conn):
            return
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            _repair_card_models(self._conn)
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

    def watch_proposals(self, callback: Callable[[str, str | None], None]) -> None:
        """Call ``callback(owner_sub, deck_id)`` whenever one of a member's proposals is saved,
        removed, or changes state, status, expiry or content, and ``callback(sub, None)`` when
        the member's approval mode changes. It runs inside the write, so it must only take note.
        The triggers are TEMP: they live on this connection and nothing is written to the file."""

        def changed(sub: Any, deck_id: Any) -> None:
            try:
                callback(str(sub), None if deck_id is None else str(deck_id))
            except Exception:
                logger.exception("proposal watch failed")

        watched = "state, status, expires_at, version, rows_json, changes_json, amended_by_client"
        with self._lock:
            self._conn.create_function("mtg_proposal_changed", 2, changed)
            for name, event, ref in (
                ("added", "INSERT", "NEW"),
                ("removed", "DELETE", "OLD"),
                ("changed", f"UPDATE OF {watched}", "NEW"),
            ):
                self._conn.execute(
                    f"CREATE TEMP TRIGGER IF NOT EXISTS mtg_proposal_{name} AFTER {event} ON proposals "
                    f"BEGIN SELECT mtg_proposal_changed({ref}.owner_sub, {ref}.deck_id); END"
                )
            self._conn.execute(
                "CREATE TEMP TRIGGER IF NOT EXISTS mtg_mode_changed AFTER UPDATE OF approval_mode ON users "
                "BEGIN SELECT mtg_proposal_changed(NEW.sub, NULL); END"
            )

    def pending_proposal_decks(self) -> list[tuple[str, str]]:
        """(owner_sub, deck_id) of every pending proposal not yet expired, one row per pair: the
        decks whose waiting count an approval setting can change."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT DISTINCT owner_sub, deck_id FROM proposals"
                " WHERE state = 'pending' AND expires_at >= ?",
                (int(time.time()),),
            ).fetchall()
        return [(str(r[0]), str(r[1])) for r in rows]

    def set_approval_mode(self, sub: str, mode: str | None) -> bool:
        """Record the member's own approval mode (modes.py), or None to go back to the gateway's
        default. False if there is no such user. Only the Account page calls this: the mode is
        never settable over MCP or the API."""
        with self.tx() as c:
            cur = c.execute("UPDATE users SET approval_mode = ? WHERE sub = ?", (mode, sub))
            return cur.rowcount > 0

    def set_user_disabled(self, sub: str, disabled: bool) -> bool:
        """Mark a user disabled (refused at token load, refresh, sign-in and browser session) or
        enable them again. False if there is no such user."""
        with self.tx() as c:
            cur = c.execute(
                "UPDATE users SET disabled_at = ? WHERE sub = ?",
                (int(time.time()) if disabled else None, sub),
            )
        if disabled and cur.rowcount > 0:
            self.scrub_upgrade_copies(sub, "account")
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

    def member_used_client(self, sub: str, client_id: str | None) -> bool:
        """Whether ``sub`` ever gave ``client_id`` a token (revoked or not): the Account page names
        a just-disconnected app only when it was the member's own."""
        if not client_id:
            return False
        return (
            self._one("SELECT 1 FROM tokens WHERE sub = ? AND client_id = ? LIMIT 1", (sub, client_id))
            is not None
        )

    def all_user_clients(self) -> dict[str, list[dict[str, Any]]]:
        """user_clients for every member at once (the admin Users page): one query
        for the tokens and one name lookup for all their apps, instead of one per member and app."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT sub, client_id, MAX(created_at) AS last_issued_at, COUNT(*) AS tokens FROM tokens
                   WHERE revoked = 0 AND expires_at >= ?
                   GROUP BY sub, client_id ORDER BY sub, last_issued_at DESC""",
                (int(time.time()),),
            ).fetchall()
        names = self.client_names({r["client_id"] for r in rows})
        out: dict[str, list[dict[str, Any]]] = {}
        for r in rows:
            out.setdefault(r["sub"], []).append(
                {
                    "client_id": r["client_id"],
                    "name": names.get(r["client_id"]),
                    "last_issued_at": r["last_issued_at"],
                    "tokens": r["tokens"],
                }
            )
        return out

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

    def revoke_all_for_user(self, sub: str, reason: str = "admin") -> dict[str, int]:
        """Revoke every token of ``sub`` and delete their browser sessions and pending codes.
        ``reason`` is what a browser signed out this way is told (SESSION_END_REASONS)."""
        with self.tx() as c:
            tokens = c.execute("UPDATE tokens SET revoked = 1 WHERE sub = ? AND revoked = 0", (sub,)).rowcount
            sessions = self._end_sessions(c, "sub = ?", (sub,), reason)
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
        ahead = _names_ahead.get()
        if ahead is not None and client_id in ahead:
            return ahead[client_id]
        info = self.get_client(client_id)
        if info is None:
            row = self._one("SELECT info_json FROM cimd_clients WHERE client_id = ?", (client_id,))
            info = json.loads(row["info_json"]) if row else None
        name = info.get("client_name") if isinstance(info, dict) else None
        return str(name)[:200] if name else None

    def client_names(self, client_ids: Iterable[str | None]) -> dict[str, str]:
        """client_name for many clients in a few queries (registered clients first, then cached
        metadata documents, as client_name does). Unknown or nameless clients are left out."""
        wanted = sorted({c for c in client_ids if c})
        infos: dict[str, Any] = {}
        for table in ("oauth_clients", "cimd_clients"):
            left = [c for c in wanted if c not in infos]
            for i in range(0, len(left), 500):
                part = left[i : i + 500]
                with self._lock:
                    for row in self._conn.execute(
                        f"SELECT client_id, info_json FROM {table} WHERE client_id IN ({_marks(part)})", part
                    ):
                        infos[row["client_id"]] = json.loads(row["info_json"])
        out: dict[str, str] = {}
        for cid, info in infos.items():
            name = info.get("client_name") if isinstance(info, dict) else None
            if name:
                out[cid] = str(name)[:200]
        return out

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

    def expire_cimd_client(self, client_id: str) -> None:
        """The document's server refused it: the stored copy is never used again (the row, and
        when the client last signed in, stay until the purge)."""
        with self.tx() as c:
            c.execute("UPDATE cimd_clients SET expires_at = 0 WHERE client_id = ?", (client_id,))

    def get_cimd_client(self, client_id: str, *, stale_for: int = 0) -> dict[str, Any] | None:
        """The cached document, if still fresh (or expired less than ``stale_for`` seconds ago)."""
        row = self._one(
            "SELECT info_json FROM cimd_clients WHERE client_id = ? AND expires_at >= ?",
            (client_id, int(time.time()) - stale_for),
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
            ids = [r[0] for r in c.execute("SELECT id FROM proposals WHERE state = 'applying'")]
            n = c.execute(
                """UPDATE proposals SET state = 'failed',
                   result_json = COALESCE(result_json, json_object('error', 'interrupted',
                       'detail', 'the gateway restarted before the apply finished; check the deck on '
                                 || 'Archidekt and restore the snapshot if it is only partly changed',
                       'snapshot_id', snapshot_id, 'deck_id', deck_id))
                   WHERE state = 'applying'"""
            ).rowcount
        self.settle_risks(ids)  # only the ones failed just now
        return n

    def purge_expired(self) -> int:
        """Delete what has expired and apply the retention caps.

        Each step runs in its own short transaction, and the plain time-based deletes go in
        batches of PURGE_BATCH rows, so a large purge never holds the lock (and every other
        request) for its whole run (0.7.29). Every step is idempotent: a purge cut
        short by an error or a restart is finished by the next one."""
        now = int(time.time())
        n = self.age_out_upgrade_copies()
        n += self._delete_batched("tokens", "expires_at < ?", (now - 86400,))
        n += self._delete_batched("auth_codes", "expires_at < ?", (now,))
        n += self._delete_batched("login_sessions", "expires_at < ?", (now,))
        with self.tx() as c:
            # each expired session leaves a note of why it ended for its browser's next visit
            longest = self.session_max_age()
            for key, created, expires in c.execute(
                "SELECT id_hash, created_at, expires_at FROM browser_sessions WHERE expires_at < ?", (now,)
            ).fetchall():
                c.execute(
                    "INSERT OR IGNORE INTO session_ends (id_hash, reason, ended_at) VALUES (?, ?, ?)",
                    (key, _expiry_reason(int(created), int(expires), longest), int(expires)),
                )
        n += self._delete_batched("browser_sessions", "expires_at < ?", (now,))
        n += self._delete_batched("session_ends", "ended_at < ?", (now - SESSION_END_NOTICE,))
        with self.tx() as c:
            # provider grants of sessions that ended (expired, or ended by a path that kept no
            # grant in step), and sign-ins never handed to a session
            n += c.execute(
                """DELETE FROM idp_grants WHERE holder LIKE 's:%'
                   AND substr(holder, 3) NOT IN (SELECT id_hash FROM browser_sessions)"""
            ).rowcount
            n += c.execute(
                "DELETE FROM idp_grants WHERE holder LIKE 'login:%' AND updated_at < ?",
                (now - LOGIN_GRANT_TTL,),
            ).rowcount
        with self.tx() as c:
            n += self._purge_cimd_clients(c, now)
        # Registration is open, so every client that registered and never finished a login,
        # or whose tokens have all expired and been purged, is dropped after a week.
        n += self._delete_batched(
            "oauth_clients",
            """created_at < ?
               AND client_id NOT IN (SELECT client_id FROM tokens)
               AND client_id NOT IN (SELECT client_id FROM auth_codes)
               AND client_id NOT IN (SELECT client_id FROM login_sessions)""",
            (now - UNUSED_CLIENT_TTL,),
        )
        n += self._delete_batched("audit_log", "at < ?", (now - AUDIT_RETENTION_SECONDS,))
        with self.tx() as c:
            n += self._prune_caps(c, now)
        with self.tx() as c:
            n += self._prune_snapshots(c, self.snapshots_keep())
            # records of how backup copies were made, for copies no snapshot records any more
            n += c.execute(
                """DELETE FROM job_runs WHERE name LIKE ? ESCAPE '\\'
                   AND substr(name, ?) NOT IN
                       (SELECT backup_deck_id FROM snapshots WHERE backup_deck_id IS NOT NULL)""",
                (_like_prefix(BACKUP_MADE_JOB), len(BACKUP_MADE_JOB) + 1),
            ).rowcount
        n += self._delete_batched(
            "metrics", "day < ?", (self._since_day(METRICS_RETENTION_SECONDS // 86400),)
        )
        n += self._delete_batched("deck_covers", "updated_at < ?", (now - DECK_COVER_RETENTION_SECONDS,))
        oldest_day = self._since_day(self.price_days)
        n += self._delete_batched("price_points", "day < ?", (oldest_day,))
        n += self._delete_batched("deck_values", "day < ?", (oldest_day,))
        with self.tx() as c:
            expiring = "SELECT id FROM proposals WHERE state = 'pending' AND expires_at < ?"
            closed = [r[0] for r in c.execute(expiring, (now,))]
            n += c.execute(
                "UPDATE proposals SET state = 'expired' WHERE state = 'pending' AND expires_at < ?", (now,)
            ).rowcount
        # Closed proposals carry nothing the user can act on: drop them after a month.
        n += self._delete_batched(
            "proposals",
            f"state IN ({_marks(CLOSED_PROPOSAL_STATES)}) AND created_at < ?",
            (*CLOSED_PROPOSAL_STATES, now - CLOSED_PROPOSAL_RETENTION_SECONDS),
        )
        n += self._delete_batched(
            "proposals",
            "state = 'applied' AND COALESCE(applied_at, created_at) < ?",
            (now - APPLIED_PROPOSAL_RETENTION_SECONDS,),
        )
        # A restart already fails every unfinished apply (fail_interrupted_applies); this is a
        # backstop for one left behind otherwise. Applies run in the background and a large one
        # under a busy pacer takes many minutes, so only one started six hours ago counts as
        # stuck: mark it failed and point at the snapshot (and the created deck id).
        with self.tx() as c:
            stuck = (
                "SELECT id FROM proposals WHERE state = 'applying' AND COALESCE(applied_at, created_at) < ?"
            )
            closed += [r[0] for r in c.execute(stuck, (now - 6 * 3600,))]
            n += c.execute(
                """UPDATE proposals SET state = 'failed',
                   result_json = COALESCE(result_json, json_object('error', 'interrupted',
                       'detail', 'the apply did not finish; check the snapshot and the deck on Archidekt',
                       'snapshot_id', snapshot_id, 'deck_id', deck_id))
                   WHERE state = 'applying' AND COALESCE(applied_at, created_at) < ?""",
                (now - 6 * 3600,),
            ).rowcount
        # only the proposals that expired or failed just now: the full scan is the one-time
        # backfill at startup (DeckService wires it in), never a tick's work
        self.settle_risks(closed)
        return n

    def _delete_batched(self, table: str, where: str, args: tuple[Any, ...]) -> int:
        """DELETE FROM ``table`` WHERE ``where``, PURGE_BATCH rows per transaction (the lock is
        released between batches). ``table`` and ``where`` are fixed strings from this module."""
        n = 0
        while True:
            with self.tx() as c:
                done = c.execute(
                    f"DELETE FROM {table} WHERE rowid IN (SELECT rowid FROM {table} WHERE {where} LIMIT ?)",
                    (*args, PURGE_BATCH),
                ).rowcount
            n += done
            if done < PURGE_BATCH:
                return n

    def snapshots_keep(self) -> int:
        """Snapshots kept per member's deck: the live setting when one is wired in, else the
        module default."""
        if self.snapshots_source is not None:
            try:
                return int(self.snapshots_source())
            except Exception:  # never let a settings problem stop the purge
                logger.exception("snapshots per deck setting unreadable; the default applies")
        return int(self.snapshots_per_deck or SNAPSHOTS_KEEP_PER_DECK)

    @staticmethod
    def _prune_caps(c: sqlite3.Connection, now: int) -> int:
        """Cap the tables anonymous requests can grow (the audit log's age limit runs in
        purge_expired, in batches).

        A flood of /register or /authorize calls inside the normal expiry windows would
        otherwise grow the database without bound. The oldest rows go first.
        """
        n = Database._trim_anonymous_audit(c)
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
    def _prune_snapshots(c: sqlite3.Connection, keep: int = SNAPSHOTS_KEEP_PER_DECK) -> int:
        """Keep the newest ``keep`` snapshots of each member's deck, and any snapshot a pending or
        running proposal (a restore) still points at; delete the rest. Also kept:
        the snapshot each recorded Archidekt backup copy was made with, and any snapshot with a
        queued backup delete, so the gateway never loses its record of a copy it may delete.
        Only a copy the gateway may still delete holds
        one: a copy the member changed on Archidekt is kept and no longer
        counted, and a copy made before 0.7.29 has no record and is never deleted, so
        neither holds its snapshot past the newest ``keep``."""
        return c.execute(
            """DELETE FROM snapshots WHERE id IN (
                   SELECT id FROM (
                       SELECT id, ROW_NUMBER() OVER (
                           PARTITION BY owner_sub, deck_id ORDER BY taken_at DESC, rowid DESC) AS n
                       FROM snapshots)
                   WHERE n > ?)
               AND id NOT IN (SELECT json_extract(changes_json, '$.snapshot_id') FROM proposals
                              WHERE kind = 'restore' AND state IN ('pending', 'applying')
                                AND json_extract(changes_json, '$.snapshot_id') IS NOT NULL)
            AND id NOT IN (SELECT snapshot_id FROM backup_deletes)
            AND id NOT IN (SELECT (SELECT s2.id FROM snapshots s2
                                   WHERE s2.owner_sub = s.owner_sub AND s2.deck_id = s.deck_id
                                     AND s2.backup_deck_id = s.backup_deck_id
                                   ORDER BY s2.taken_at, s2.rowid LIMIT 1)
                           FROM snapshots s WHERE s.backup_deck_id IS NOT NULL
                             AND EXISTS (SELECT 1 FROM job_runs j WHERE j.name = ? || s.backup_deck_id
                                         AND coalesce(json_extract(j.detail_json, '$.kept'), 0) != 1))""",
            (keep, BACKUP_MADE_JOB),
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
    def session_max_age(self) -> int:
        """The longest a sign-in lasts after it was made, in seconds, as the Settings page has it."""
        if self.session_max_age_source is not None:
            try:
                return int(self.session_max_age_source())
            except Exception:
                logger.exception("could not read the longest sign-in time; the default applies")
        return BROWSER_SESSION_MAX_AGE

    def create_browser_session(self, session_id: str, sub: str, ttl: int) -> None:
        now = int(time.time())
        with self.tx() as c:
            c.execute(
                "INSERT INTO browser_sessions (id_hash, sub, created_at, expires_at) VALUES (?, ?, ?, ?)",
                (hash_token(session_id), sub, now, now + min(ttl, self.session_max_age())),
            )

    def get_browser_session(self, session_id: str) -> str | None:
        now = int(time.time())
        row = self._one(
            "SELECT sub FROM browser_sessions WHERE id_hash = ? AND expires_at >= ? AND created_at > ?",
            (hash_token(session_id), now, now - self.session_max_age()),
        )
        return row["sub"] if row else None

    def renew_browser_session(self, session_id: str, ttl: int) -> int | None:
        """Sliding renewal: once more than half of ``ttl`` has passed since the session was made or
        last renewed, its end moves to ``ttl`` from now, never past ``session_max_age`` after the
        sign-in. The seconds the session now has left when it was renewed, else None (not due,
        or no such live session). Reads only, unless a renewal is due."""
        now = int(time.time())
        key = hash_token(session_id)
        longest = self.session_max_age()
        row = self._one(
            "SELECT created_at, expires_at FROM browser_sessions WHERE id_hash = ? AND expires_at >= ? "
            "AND created_at > ?",
            (key, now, now - longest),
        )
        if row is None or int(row["expires_at"]) - now >= ttl / 2:
            return None
        until = min(now + ttl, int(row["created_at"]) + longest)
        if until <= int(row["expires_at"]):
            return None  # already as long as it may last
        with self.tx() as c:
            done = c.execute(
                "UPDATE browser_sessions SET expires_at = ? WHERE id_hash = ? AND expires_at = ?",
                (until, key, row["expires_at"]),
            ).rowcount
        return until - now if done else None

    def delete_browser_session(self, session_id: str) -> None:
        """The member signed out on this device: the session and its provider grant go, with no
        record of why (the browser's cookie goes in the same answer)."""
        key = hash_token(session_id)
        with self.tx() as c:
            c.execute("DELETE FROM browser_sessions WHERE id_hash = ?", (key,))
            c.execute("DELETE FROM idp_grants WHERE holder = ?", (SESSION_HOLDER + key,))

    def delete_browser_sessions_for(self, sub: str, reason: str = "elsewhere") -> int:
        """End every browser session of ``sub``; each browser is told ``reason`` when it comes back."""
        with self.tx() as c:
            return self._end_sessions(c, "sub = ?", (sub,), reason)

    def end_browser_session(self, id_hash: str, reason: str) -> bool:
        """End one browser session by its id hash (the identity provider no longer honours its
        sign-in); the browser is told ``reason`` when it comes back. False: it had ended already."""
        with self.tx() as c:
            return self._end_sessions(c, "id_hash = ?", (id_hash,), reason) > 0

    @staticmethod
    def _end_sessions(c: sqlite3.Connection, where: str, args: tuple[Any, ...], reason: str) -> int:
        """Delete the browser sessions ``where`` matches, with their provider grants, and record
        why they ended (session_ends) for the page their browser sees next."""
        assert reason in SESSION_END_REASONS, reason
        now = int(time.time())
        rows = [r[0] for r in c.execute(f"SELECT id_hash FROM browser_sessions WHERE {where}", args)]
        for key in rows:
            c.execute(
                "INSERT OR REPLACE INTO session_ends (id_hash, reason, ended_at) VALUES (?, ?, ?)",
                (key, reason, now),
            )
            c.execute("DELETE FROM idp_grants WHERE holder = ?", (SESSION_HOLDER + key,))
        c.execute(f"DELETE FROM browser_sessions WHERE {where}", args)
        return len(rows)

    def session_end_reason(self, session_id: str) -> str | None:
        """Why the browser session ``session_id`` is no longer live (one of SESSION_END_REASONS),
        or None when it is live or nothing is known of it. Only the browser holding the session's
        cookie can ask, so the answer tells nobody else anything."""
        now = int(time.time())
        key = hash_token(session_id)
        row = self._one("SELECT created_at, expires_at FROM browser_sessions WHERE id_hash = ?", (key,))
        longest = self.session_max_age()
        if row is not None:
            created, expires = int(row["created_at"]), int(row["expires_at"])
            if created <= now - longest:
                return "longest"
            if expires < now:
                return _expiry_reason(created, expires, longest)
            return None
        row = self._one("SELECT reason FROM session_ends WHERE id_hash = ?", (key,))
        return str(row["reason"]) if row is not None else None

    MEMBER_TABLES = MEMBER_TABLES

    def delete_member_data(self, sub: str) -> dict[str, int]:
        """Delete everything the gateway keeps about one member, at their request: proposals,
        snapshots, reports, scan sessions, the Archidekt link, every app grant and browser session,
        usage counters, the covers of their decks and the user record. The security audit log is
        kept (it ages out after a year) and records the deletion itself. Signing in again starts a
        fresh, empty account."""
        with self.tx() as c:
            out = delete_member_rows(c, sub)
        self.flush_wal()
        self.scrub_upgrade_copies(sub, "data")
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
        self.flush_wal()
        self.scrub_upgrade_copies(sub, "account")
        return sum(self.revoke_all_for_user(sub, "removed").values())

    def set_user_issuer(self, sub: str, issuer: str) -> None:
        with self.tx() as c:
            c.execute("UPDATE users SET idp_issuer = ? WHERE sub = ?", (issuer, sub))

    def set_user_groups(self, sub: str, groups: list[str]) -> None:
        """Record the groups the identity provider reported just now."""
        with self.tx() as c:
            c.execute("UPDATE users SET groups_json = ? WHERE sub = ?", (json.dumps(groups), sub))

    # identity-provider grants ------------------------------------------------
    # One per sign-in, named by its holder (membership.py): "s:" and a browser session's id hash,
    # "ai:" and the member (their latest AI-app sign-in), "login:" and a login's id hash (a sign-in
    # on its way to its session).
    def save_idp_grant(
        self,
        holder: str,
        sub: str,
        *,
        refresh_enc: str,
        access_enc: str,
        access_expires_at: int,
        signed_in: bool = False,
    ) -> None:
        """Keep a grant. ``signed_in``: from a sign-in the provider just let through, so it counts
        as the provider's last yes (checked_at); a renewal leaves that as it was."""
        now = int(time.time())
        with self.tx() as c:
            c.execute(
                """INSERT INTO idp_grants
                     (holder, sub, refresh_enc, access_enc, access_expires_at, updated_at, checked_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(holder) DO UPDATE SET sub=excluded.sub, refresh_enc=excluded.refresh_enc,
                     access_enc=excluded.access_enc, access_expires_at=excluded.access_expires_at,
                     updated_at=excluded.updated_at,
                     checked_at=COALESCE(excluded.checked_at, idp_grants.checked_at)""",
                (holder, sub, refresh_enc, access_enc, access_expires_at, now, now if signed_in else None),
            )

    def replace_idp_grant(
        self, holder: str, *, old_refresh_enc: str, refresh_enc: str, access_enc: str, access_expires_at: int
    ) -> bool:
        """save_idp_grant for a renewal in the background (membership.py): only over the grant it
        renewed, never bringing back one revoked or deleted meanwhile. False: nothing written."""
        with self.tx() as c:
            cur = c.execute(
                """UPDATE idp_grants SET refresh_enc=?, access_enc=?, access_expires_at=?, updated_at=?
                   WHERE holder = ? AND refresh_enc = ?""",
                (refresh_enc, access_enc, access_expires_at, int(time.time()), holder, old_refresh_enc),
            )
            return cur.rowcount == 1

    def get_idp_grant(self, holder: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM idp_grants WHERE holder = ?", (holder,))
        return dict(row) if row else None

    def idp_grants_for(self, sub: str) -> list[dict[str, Any]]:
        """The member's grants that stand for a sign-in in use (their sessions' and their AI apps'),
        the one the provider let in last first."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM idp_grants WHERE sub = ? AND (holder LIKE 's:%' OR holder LIKE 'ai:%')
                   ORDER BY COALESCE(checked_at, 0) DESC, updated_at DESC""",
                (sub,),
            ).fetchall()
        return [dict(r) for r in rows]

    def move_idp_grant(self, old: str, new: str) -> bool:
        """Hand a sign-in's grant to the holder it was made for (its new session, the member's AI
        apps), replacing what that holder had. False: there was no such grant (nothing moved)."""
        with self.tx() as c:
            if c.execute("SELECT 1 FROM idp_grants WHERE holder = ?", (old,)).fetchone() is None:
                return False
            c.execute("DELETE FROM idp_grants WHERE holder = ?", (new,))
            c.execute("UPDATE idp_grants SET holder = ? WHERE holder = ?", (new, old))
            return True

    def delete_idp_grant(self, holder: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM idp_grants WHERE holder = ?", (holder,))

    def mark_idp_grant_checked(self, holder: str) -> None:
        """The provider let this holder in just now."""
        with self.tx() as c:
            c.execute("UPDATE idp_grants SET checked_at = ? WHERE holder = ?", (int(time.time()), holder))

    # archidekt links -------------------------------------------------------
    def save_link(
        self, sub: str, *, username: str, user_id: str | None, secret_enc: str, only_member: bool = False
    ) -> bool:
        """Store a new link. A relink replaces the old row whole: its dates start again, and the
        session it held is flushed from the write-ahead log at once (as an unlink does). With
        ``only_member``, only while ``sub`` is a known user who is not disabled, so a link that
        finishes after the account was disabled or its data deleted stores nothing. True when
        stored."""
        now = int(time.time())
        with self.tx() as c:
            replaced = c.execute(
                "SELECT 1 FROM archidekt_links WHERE sub = ? AND secret_enc != ''", (sub,)
            ).fetchone()
            cur = c.execute(
                """INSERT INTO archidekt_links (sub, archidekt_username, archidekt_user_id, secret_enc,
                   status, created_at, refreshed_at)
                   SELECT ?, ?, ?, ?, 'active', ?, ? WHERE ? = 0
                     OR EXISTS (SELECT 1 FROM users WHERE sub = ? AND disabled_at IS NULL)
                   ON CONFLICT(sub) DO UPDATE SET archidekt_username=excluded.archidekt_username,
                     archidekt_user_id=excluded.archidekt_user_id, secret_enc=excluded.secret_enc,
                     status='active', created_at=excluded.created_at,
                     refreshed_at=excluded.refreshed_at, last_used_at=NULL""",
                (sub, username, user_id, secret_enc, now, now, 1 if only_member else 0, sub),
            )
            stored = cur.rowcount > 0
        if replaced:
            self.flush_wal()
            if stored:  # the replaced session is not kept in the copies either
                self.scrub_upgrade_copies(sub, "link")
        return stored

    def audit_seen(self, event: str) -> bool:
        """Whether the audit log holds any entry of ``event`` (a one-time marker)."""
        with self._lock:
            return (
                self._conn.execute("SELECT 1 FROM audit_log WHERE event = ? LIMIT 1", (event,)).fetchone()
                is not None
            )

    def all_user_subs(self) -> list[str]:
        """Every member's subject (for the removed-member clean-up's sanity check)."""
        with self._lock:
            return [r[0] for r in self._conn.execute("SELECT sub FROM users").fetchall()]

    def active_links(self) -> list[dict[str, Any]]:
        """``sub`` and ``secret_enc`` of every active link (for the startup reseal and the hourly
        expiry sweep)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT sub, secret_enc FROM archidekt_links WHERE status = 'active' AND secret_enc != ''"
            ).fetchall()
        return [dict(r) for r in rows]

    def set_link_username(self, sub: str, username: str) -> None:
        """The linked account's new username, after the member changed it on Archidekt through the
        gateway (profile.py); the account's id, which ownership is judged by, stays."""
        with self.tx() as c:
            c.execute("UPDATE archidekt_links SET archidekt_username = ? WHERE sub = ?", (username, sub))

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
            changed = c.execute(sql, args).rowcount > 0
        if changed:
            self.flush_wal()
        if only_secret is None:  # an unlink: the copies saved at upgrades keep no session either
            self.scrub_upgrade_copies(sub, "link")
        return changed

    def flush_wal(self) -> None:
        """Write the WAL back into the database file and truncate it, so an old copy of a row just
        blanked (an Archidekt session) does not linger in the -wal file. Cheap on a small file."""
        if str(self.path) == ":memory:":
            return
        with self._lock:
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:  # busy readers: the next checkpoint catches up
                pass

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
            # The number on the deck's board (0.7.29): one more than the highest of the owner's live
            # waiting proposals on that deck, those being applied included (an apply can put one
            # back to waiting), so a board that empties starts again at 1, and an amend keeps it.
            # The Archidekt account is the one linked now, unless given.
            account = (
                "?"
                if "archidekt_user_id" in row
                else "(SELECT archidekt_user_id FROM archidekt_links WHERE sub = ? AND status = 'active')"
            )
            c.execute(
                f"""INSERT INTO proposals (id, owner_sub, kind, deck_id, deck_name, baseline_fingerprint,
                   changes_json, diff_text, state, created_at, expires_at, created_by_client, rows_json,
                   board_no, archidekt_user_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?,
                     (SELECT COALESCE(MAX(board_no), 0) + 1 FROM proposals
                      WHERE owner_sub = ? AND deck_id = ?
                        AND (state = 'applying' OR (state = 'pending' AND expires_at >= ?))),
                     {account})""",
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
                    row["owner_sub"],
                    row["deck_id"],
                    now,
                    row["archidekt_user_id"] if "archidekt_user_id" in row else row["owner_sub"],
                ),
            )
            if max_closed is not None:
                c.execute(
                    f"""DELETE FROM proposals WHERE id IN (
                           SELECT id FROM proposals WHERE owner_sub = ?
                             AND (state IN ({_marks(CLOSED_PROPOSAL_STATES)})
                                  OR (state = 'pending' AND expires_at < ?))
                           ORDER BY created_at DESC, rowid DESC LIMIT -1 OFFSET ?)""",
                    (row["owner_sub"], *CLOSED_PROPOSAL_STATES, now, max_closed),
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
                    "UPDATE proposals SET state = 'rejected', applied_at = ?, "
                    "result_json = json_patch(COALESCE(result_json, '{}'), ?) "
                    "WHERE id = ? AND state = 'pending'",
                    (int(time.time()), json.dumps({"rejected_by": "disconnected"}), pid),
                )
        self.settle_risks(ids)
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

    def list_proposals(
        self,
        owner_sub: str,
        limit: int = 20,
        *,
        deck_id: str | None = None,
        kinds: list[str] | tuple[str, ...] | None = None,
        states: list[str] | tuple[str, ...] | None = None,
        search: str | None = None,
        since: int | None = None,
        until: int | None = None,
        offset: int = 0,
        oldest_first: bool = False,
    ) -> list[dict[str, Any]]:
        """Newest first (``oldest_first``: oldest first, so paging runs from the oldest). ``deck_id``,
        ``kinds`` (edit, create_deck, restore), ``states`` (pending,
        applied, failed, rejected, expired: a pending proposal past its expiry counts as expired),
        ``search`` (a case-insensitive match on the deck name or the change summary) and a time
        window (``since`` inclusive, ``until`` exclusive, epoch seconds) narrow the list in SQL,
        so a deck with many proposals keeps its older ones on its own page."""
        sql = (
            "SELECT id, kind, deck_id, deck_name, state, created_at, expires_at, applied_at, "
            "created_by_client, diff_text, version, board_no, status, status_reason, status_at, "
            "amended_by_client FROM proposals WHERE owner_sub = ?"
        )
        args: list[Any] = [owner_sub]
        if deck_id:
            sql += " AND deck_id = ?"
            args.append(str(deck_id))
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args.extend(str(k) for k in kinds)
        if states:
            now = int(time.time())
            parts = []
            for st in states:
                if st == "expired":
                    parts.append("(state = 'pending' AND expires_at < ?)")
                    args.append(now)
                elif st == "pending":
                    parts.append("(state = 'pending' AND expires_at >= ?)")
                    args.append(now)
                else:
                    parts.append("state = ?")
                    args.append(str(st))
            sql += " AND (" + " OR ".join(parts) + ")"
        if search:
            like = _like(search)
            sql += " AND (deck_name LIKE ? ESCAPE '\\' OR diff_text LIKE ? ESCAPE '\\')"
            args += [like, like]
        if since is not None:
            sql += " AND created_at >= ?"
            args.append(int(since))
        if until is not None:
            sql += " AND created_at < ?"
            args.append(int(until))
        order = "ASC" if oldest_first else "DESC"
        sql += f" ORDER BY created_at {order}, rowid {order} LIMIT ? OFFSET ?"
        args += [max(0, int(limit)), max(0, int(offset))]
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def proposal_index(self, owner_sub: str) -> list[dict[str, Any]]:
        """Every kept proposal of the owner (the Proposals page: waiting and past, no cap but the
        retention), newest first. Rows carry what a list row shows (no changes or review rows):
        the state as stored, the ``risk`` stored when a proposal closed and the result (who
        closed it). The page narrows them itself, risk and app included."""
        sql = (
            "SELECT id, kind, deck_id, deck_name, state, created_at, expires_at, applied_at, "
            "created_by_client, amended_by_client, diff_text, version, board_no, status, status_reason, "
            "status_at, risk, snapshot_id, result_json FROM proposals WHERE owner_sub = ? "
            "ORDER BY created_at DESC, rowid DESC"
        )
        with self._lock:
            rows = self._conn.execute(sql, (owner_sub,)).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            raw = d.pop("result_json", None)
            try:
                d["result"] = json.loads(raw) if raw else None
            except ValueError:
                d["result"] = None
            out.append(d)
        return out

    def history_counts(
        self,
        owner_sub: str,
        *,
        deck_id: str | None = None,
        search: str | None = None,
        since: int | None = None,
    ) -> dict[str, int]:
        """How many snapshots, applied changes (``changes``, DECK_CHANGE_KINDS; ``restores`` the
        restores among them) and tests (``tests``, also ``reports``) the History page holds under its
        filters (deck, a search, a start time): its chips and its pager. Each count narrows as the
        rows it counts do (list_snapshots, list_proposals, ReportService.list): the search matches
        a deck's name, and for a restore its change summary too."""

        def narrow(time_col: str, *, diff: bool = False) -> tuple[str, list[Any]]:
            where, args = " WHERE owner_sub = ?", [owner_sub]
            if deck_id:
                where += " AND deck_id = ?"
                args.append(str(deck_id))
            if search:
                like = _like(search)
                if diff:
                    where += " AND (deck_name LIKE ? ESCAPE '\\' OR diff_text LIKE ? ESCAPE '\\')"
                    args += [like, like]
                else:
                    where += " AND deck_name LIKE ? ESCAPE '\\'"
                    args.append(like)
            if since is not None:
                where += f" AND {time_col} >= ?"
                args.append(int(since))
            return where, args

        out = {}
        with self._lock:
            w, a = narrow("taken_at")
            out["snapshots"] = int(self._conn.execute("SELECT COUNT(*) FROM snapshots" + w, a).fetchone()[0])
            has_reports = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reports'"
            ).fetchone()
            out["reports"] = (
                int(self._conn.execute("SELECT COUNT(*) FROM reports" + w, a).fetchone()[0])
                if has_reports
                else 0
            )
            w, a = narrow("created_at", diff=True)
            kinds = ",".join("?" * len(DECK_CHANGE_KINDS))
            out["changes"] = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM proposals" + w + f" AND kind IN ({kinds}) AND state = 'applied'",
                    [*a, *DECK_CHANGE_KINDS],
                ).fetchone()[0]
            )
            out["tests"] = out["reports"]
            out["restores"] = int(
                self._conn.execute(
                    "SELECT COUNT(*) FROM proposals" + w + " AND kind = 'restore' AND state = 'applied'", a
                ).fetchone()[0]
            )
        return out

    def snapshot_stats(self, owner_sub: str, deck_id: str | None = None) -> dict[str, int]:
        """The History page's "Snapshots kept" widget: how many, on how many decks, the newest."""
        sql = "SELECT COUNT(*), COUNT(DISTINCT deck_id), MAX(taken_at) FROM snapshots WHERE owner_sub = ?"
        args: list[Any] = [owner_sub]
        if deck_id:
            sql += " AND deck_id = ?"
            args.append(str(deck_id))
        with self._lock:
            n, decks, newest = self._conn.execute(sql, args).fetchone()
        return {"count": int(n or 0), "decks": int(decks or 0), "newest": int(newest or 0)}

    def applied_via(self, owner_sub: str, proposal_id: str) -> str | None:
        """Where a proposal's apply was called from (``via`` of its ``proposal_applied`` audit row),
        or None when the audit log no longer has the row."""
        row = self._one(
            "SELECT json_extract(detail_json, '$.via') FROM audit_log WHERE sub = ? "
            "AND event = 'proposal_applied' AND json_valid(detail_json) "
            "AND json_extract(detail_json, '$.proposal_id') = ? ORDER BY id DESC LIMIT 1",
            (owner_sub, proposal_id),
        )
        return str(row[0]) if row and row[0] is not None else None

    def history_decks(self, owner_sub: str) -> dict[str, str]:
        """The decks that appear in the member's proposals, snapshots and reports of their own decks:
        id -> the newest name any of them recorded, for a history filter and Activity."""
        with self._lock:
            # the reports table belongs to reports.ReportService, which may not have made it yet
            reports = self._conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'reports'"
            ).fetchone()
            sql = (
                "SELECT deck_id, deck_name, created_at AS at FROM proposals WHERE owner_sub = ? "
                "UNION ALL SELECT deck_id, deck_name, taken_at FROM snapshots WHERE owner_sub = ? "
            )
            args: tuple[Any, ...] = (owner_sub, owner_sub)
            if reports:
                # a pasted list's, a proposal's or a comparison's report names something else
                sql += (
                    "UNION ALL SELECT deck_id, deck_name, taken_at FROM reports WHERE owner_sub = ? "
                    "AND deck_id != '' AND json_extract(stats_json, '$._run.source') IS NULL "
                )
                args += (owner_sub,)
            rows = self._conn.execute(sql + "ORDER BY at", args).fetchall()
        out: dict[str, str] = {}
        for r in rows:
            out[str(r["deck_id"])] = str(r["deck_name"] or out.get(str(r["deck_id"])) or "")
        return out

    def own_history_deck_ids(
        self, owner_sub: str, *, account_id: str, username: str, linked_at: int
    ) -> set[str]:
        """The decks the member's history shows as decks of the Archidekt account linked now
        (``account_id`` and ``username``, linked at ``linked_at``), the only ones its deck list
        can call gone: snapshots of a deck that account owned (a stored deck names its owner),
        and proposals that change or make one of that account's decks (edit, details, restore,
        new deck). A clone's source, an account action's deck or a report's deck may be anyone's,
        so they are never in it, and neither is a deck of an account linked before: its history
        stays usable (its snapshots keep Restore) and it is never called gone.

        A proposal (or a snapshot whose deck names no owner) does not record the account, so it
        counts when it was made under the link of now (at or after ``linked_at``), or under an
        earlier link of the same account (the member's "archidekt_linked" audit entries name each
        account linked). One made before any recorded link counts only while no other account was
        ever linked. Every doubt leaves a deck out, so a deck is never called gone wrongly."""
        with self._lock:
            snaps = self._conn.execute(
                "SELECT deck_id FROM snapshots WHERE owner_sub = ? AND deck_owner_id = ?",
                (owner_sub, str(account_id)),
            ).fetchall()
            props = self._conn.execute(
                "SELECT deck_id, created_at FROM proposals WHERE owner_sub = ? "
                "AND kind IN ('edit', 'details', 'categories', 'custom_cards', 'restore', 'create_deck') "
                "UNION ALL SELECT deck_id, taken_at FROM snapshots WHERE owner_sub = ? "
                "AND deck_owner_id IS NULL",
                (owner_sub, owner_sub),
            ).fetchall()
            links = self._conn.execute(
                "SELECT at, detail_json FROM audit_log WHERE sub = ? AND event = 'archidekt_linked' "
                "ORDER BY at, id",
                (owner_sub,),
            ).fetchall()
        me = str(username or "").casefold()

        def named(detail: Any) -> str:
            try:
                d = json.loads(detail or "{}")
            except ValueError:
                return ""
            return str(d.get("archidekt_username") or "").casefold() if isinstance(d, dict) else ""

        linked = [(int(r["at"]), named(r["detail_json"])) for r in links]
        only_me = bool(me) and all(name == me for _at, name in linked)
        out = {str(r["deck_id"]) for r in snaps}
        for r in props:
            at = int(r["created_at"])
            if at >= int(linked_at):
                out.add(str(r["deck_id"]))
                continue
            earlier = [name for when, name in linked if when <= at]
            if (earlier[-1] == me and bool(me)) if earlier else only_me:
                out.add(str(r["deck_id"]))
        return out

    def settle_risks(self, ids: Iterable[str] | None = None) -> int:
        """Store the risk tier of closed proposals that have none yet (``ids``, or every one: the
        one-time backfill at upgrade, which the app runs once its settings are loaded), worked out
        by ``risk_source`` from the stored row with the settings in force now. A closed proposal's
        risk can no longer change, so the Proposals page filters on the stored value. A
        proposal that can't be judged (``risk_source`` raises or answers no tier) stores
        ``modes.UNJUDGED`` so it is never looked at again; the pages show it as a dash. Returns
        how many got a tier."""
        if self.risk_source is None:
            return 0
        sql = (
            "SELECT id, owner_sub FROM proposals WHERE risk IS NULL AND state NOT IN ('pending', 'applying')"
        )
        args: list[Any] = []
        if ids is not None:
            wanted = [str(i) for i in ids]
            if not wanted:
                return 0
            sql += f" AND id IN ({_marks(wanted)})"
            args.extend(wanted)
        with self._lock:
            todo = [(r["id"], r["owner_sub"]) for r in self._conn.execute(sql, args).fetchall()]
        n = 0
        for pid, owner in todo:
            row = self.get_proposal(pid, owner)
            if row is None:
                continue
            try:
                risk = self.risk_source(row)
            except Exception:  # never let one odd row stop a close or the backfill
                logger.exception("risk of proposal %s could not be worked out", pid)
                risk = UNJUDGED
            if risk not in ("low", "high", "consent", "destructive"):
                risk = UNJUDGED
            with self.tx() as c:
                stored = c.execute(
                    "UPDATE proposals SET risk = ? WHERE id = ? AND risk IS NULL "
                    "AND state NOT IN ('pending', 'applying')",
                    (risk, pid),
                ).rowcount
            if risk != UNJUDGED:
                n += stored
        return n

    def reject_proposal(
        self, proposal_id: str, owner_sub: str, closed_by: dict[str, Any] | None = None
    ) -> bool:
        """Move pending -> rejected for the owner; False if it was not pending. ``closed_by``
        (who rejected it: ``{"rejected_by": ..., "client": ...}``) is merged into its result."""
        patch = json.dumps(closed_by) if closed_by else None
        with self.tx() as c:
            cur = c.execute(
                "UPDATE proposals SET state = 'rejected', applied_at = ?, result_json = CASE WHEN ? IS NULL "
                "THEN result_json ELSE json_patch(COALESCE(result_json, '{}'), ?) END "
                "WHERE id = ? AND owner_sub = ? AND state = 'pending' AND expires_at >= ?",
                (int(time.time()), patch, patch, proposal_id, owner_sub, int(time.time())),
            )
            done = cur.rowcount == 1
        if done:
            self.settle_risks([proposal_id])
        return done

    def pending_baselines(self, owner_sub: str | None = None) -> list[dict[str, Any]]:
        """Live pending proposals that recorded the deck as it was when they were made (a
        non-empty ``baseline_fingerprint``: edits, details, restores, clones, deck deletes), oldest
        first, for the deck sync (sync.py); all members' when ``owner_sub`` is None."""
        sql = (
            "SELECT id, owner_sub, deck_id, baseline_fingerprint FROM proposals "
            "WHERE state = 'pending' AND expires_at >= ? AND baseline_fingerprint != ''"
        )
        args: tuple[Any, ...] = (int(time.time()),)
        if owner_sub is not None:
            sql += " AND owner_sub = ?"
            args += (owner_sub,)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY created_at", args).fetchall()
        return [dict(r) for r in rows]

    def deck_applying(self, owner_sub: str, deck_id: str) -> bool:
        """Whether one of the member's proposals for this deck is being applied right now."""
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM proposals WHERE owner_sub = ? AND deck_id = ? AND state = 'applying' LIMIT 1",
                (owner_sub, deck_id),
            ).fetchone()
        return row is not None

    # 0.7.29 flexible approvals and the assistant board -----------------------------------------------
    def pending_board(self, owner_sub: str, deck_id: str | None = None) -> list[dict[str, Any]]:
        """Every live waiting proposal of the owner (of one deck when ``deck_id``), oldest first,
        with no cap: the deck's board. Rows carry what a board line needs; the whole
        proposal comes from get_proposal."""
        sql = (
            "SELECT id, kind, deck_id, deck_name, created_at, expires_at, created_by_client, diff_text, "
            "version, amended_at, amended_by_client, version_note, status, status_reason, status_at, "
            "board_no, archidekt_user_id FROM proposals "
            "WHERE owner_sub = ? AND state = 'pending' AND expires_at >= ?"
        )
        self.hold_blocked(owner_sub)
        args: list[Any] = [owner_sub, int(time.time())]
        if deck_id is not None:
            sql += " AND deck_id = ?"
            args.append(str(deck_id))
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY created_at, rowid", args).fetchall()
        return [dict(r) for r in rows]

    def pending_count_by_deck(self, owner_sub: str) -> dict[str, int]:
        """How many live waiting proposals each of the owner's decks has (the Home tile)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT deck_id, COUNT(*) AS n FROM proposals WHERE owner_sub = ? AND state = 'pending' "
                "AND expires_at >= ? GROUP BY deck_id",
                (owner_sub, int(time.time())),
            ).fetchall()
        return {str(r["deck_id"]): int(r["n"]) for r in rows}

    def amend_proposal(
        self,
        proposal_id: str,
        owner_sub: str,
        *,
        expect_version: int,
        changes: Any,
        rows: list[dict[str, Any]] | None,
        diff_text: str,
        by_client: str | None,
        what_changed: str | None = None,
        baseline_fingerprint: str | None = None,
        deck_name: str | None = None,
    ) -> int | None:
        """Replace a waiting proposal's content with a new version, keeping its id, date and board
        number. The version being replaced is kept in ``proposal_versions`` with
        who made it. Only while it is pending, unexpired and still at ``expect_version`` (so two
        amends, or an amend and an apply that claimed it first, cannot both win). Its waiting
        status is cleared, except a hold that a rejected note it depends on put there: that
        stays until the link is gone, so the new version can't apply by itself. Returns the new
        version, or None when it isn't waiting, has expired or is at another version. Raises
        ProposalVersionsFull once it keeps MAX_PROPOSAL_VERSIONS earlier versions. ``deck_name``:
        a new deck's new name (a waiting new deck may be renamed)."""
        now = int(time.time())
        with self.tx() as c:
            row = c.execute(
                "SELECT version, changes_json, rows_json, diff_text, created_by_client, created_at, "
                "amended_by_client, amended_at, version_note, status, status_reason FROM proposals "
                "WHERE id = ? AND owner_sub = ? "
                "AND state = 'pending' AND expires_at >= ? AND version = ?",
                (proposal_id, owner_sub, now, int(expect_version)),
            ).fetchone()
            if row is None:
                return None
            (kept,) = c.execute(
                "SELECT COUNT(*) FROM proposal_versions WHERE proposal_id = ?", (proposal_id,)
            ).fetchone()
            if kept >= MAX_PROPOSAL_VERSIONS:
                raise ProposalVersionsFull(proposal_id)
            # A hold stays while a note it depends on is still rejected, withdrawn or failed.
            held = row["status"] == "held" and _blocking_dependency(c, proposal_id) is not None
            old = int(row["version"])
            c.execute(
                """INSERT INTO proposal_versions (proposal_id, version, changes_json, rows_json, diff_text,
                   made_by_client, made_at, what_changed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal_id,
                    old,
                    row["changes_json"],
                    row["rows_json"],
                    row["diff_text"],
                    row["amended_by_client"] if old > 1 else row["created_by_client"],
                    row["amended_at"] if old > 1 and row["amended_at"] else row["created_at"],
                    row["version_note"],
                ),
            )
            c.execute(
                "UPDATE proposals SET changes_json = ?, rows_json = ?, diff_text = ?, version = ?, "
                "amended_at = ?, amended_by_client = ?, version_note = ?, status = ?, "
                "status_reason = ?, status_at = ?, "
                "baseline_fingerprint = COALESCE(?, baseline_fingerprint), "
                "deck_name = COALESCE(?, deck_name) WHERE id = ?",
                (
                    json.dumps(changes),
                    json.dumps(rows) if rows is not None else None,
                    diff_text,
                    old + 1,
                    now,
                    by_client,
                    what_changed,
                    "held" if held else None,
                    row["status_reason"] if held else None,
                    now,
                    baseline_fingerprint,
                    deck_name,
                    proposal_id,
                ),
            )
            return old + 1

    def next_card_seq(self, proposal_id: str, owner_sub: str, client: str | None) -> int:
        """Count one more card drawn for this proposal in ``client``'s chat and return its number
        (1 for the first). Each app counts its own cards: a card drawn in another app's chat never
        makes this app's newest card look old."""
        key = str(client or "")
        with self.tx() as c:
            row = c.execute(
                "SELECT card_seqs FROM proposals WHERE id = ? AND owner_sub = ?", (proposal_id, owner_sub)
            ).fetchone()
            if row is None:
                return 0
            try:
                seqs = json.loads(row[0]) if row[0] else {}
            except ValueError:
                seqs = {}
            if not isinstance(seqs, dict):
                seqs = {}
            n = int(seqs.get(key) or 0) + 1
            seqs[key] = n
            c.execute("UPDATE proposals SET card_seqs = ? WHERE id = ?", (json.dumps(seqs), proposal_id))
            return n

    def proposal_versions(self, proposal_id: str, owner_sub: str) -> list[dict[str, Any]]:
        """The earlier versions of one of the owner's proposals, oldest first (the current one is
        the proposal itself)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT v.version, v.changes_json, v.rows_json, v.diff_text, v.made_by_client, v.made_at, "
                "v.what_changed FROM proposal_versions v JOIN proposals p ON p.id = v.proposal_id "
                "WHERE v.proposal_id = ? AND p.owner_sub = ? ORDER BY v.version",
                (proposal_id, owner_sub),
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["changes"] = json.loads(d.pop("changes_json"))
            rows_json = d.pop("rows_json")
            d["rows"] = json.loads(rows_json) if rows_json else None
            out.append(d)
        return out

    def set_proposal_status(
        self, proposal_id: str, owner_sub: str, status: str | None, reason: str | None = None
    ) -> bool:
        """Set a waiting proposal's status (None: plain waiting; PROPOSAL_STATUSES) and its
        reason. Only while it is pending. True when it was changed."""
        if status is not None and status not in PROPOSAL_STATUSES:
            raise ValueError(f"unknown proposal status {status!r}")
        with self.tx() as c:
            return (
                c.execute(
                    "UPDATE proposals SET status = ?, status_reason = ?, status_at = ? "
                    "WHERE id = ? AND owner_sub = ? AND state = 'pending'",
                    (status, reason, int(time.time()), proposal_id, owner_sub),
                ).rowcount
                == 1
            )

    def close_proposal(
        self, proposal_id: str, owner_sub: str, state: str, reason: str | None = None, *, by: Any = None
    ) -> bool:
        """Move a live waiting proposal to ``withdrawn`` or ``replaced`` with its reason (kept
        visible and restorable while unexpired). ``by`` is merged into its result as
        ``closed_by``. True when it was changed."""
        if state not in ("withdrawn", "replaced"):
            raise ValueError(f"close_proposal does not set {state!r}")
        patch = json.dumps({"closed_by": by}) if by is not None else None
        now = int(time.time())
        with self.tx() as c:
            done = (
                c.execute(
                    "UPDATE proposals SET state = ?, status_reason = ?, status_at = ?, result_json = CASE "
                    "WHEN ? IS NULL THEN result_json ELSE json_patch(COALESCE(result_json, '{}'), ?) END "
                    "WHERE id = ? AND owner_sub = ? AND state = 'pending' AND expires_at >= ?",
                    (state, reason, now, patch, patch, proposal_id, owner_sub, now),
                ).rowcount
                == 1
            )
        if done:
            self.settle_risks([proposal_id])
        return done

    def reopen_proposal(self, proposal_id: str, owner_sub: str, *, max_pending: int | None = None) -> bool:
        """Bring a withdrawn or replaced proposal back to waiting while it is unexpired (a
        replaced note returns when its replacement is rejected). With ``max_pending``, refused when
        the owner already has that many live waiting proposals, as save_proposal. True when
        it was changed."""
        now = int(time.time())
        with self.tx() as c:
            if max_pending is not None:
                (pending,) = c.execute(
                    "SELECT COUNT(*) FROM proposals "
                    "WHERE owner_sub = ? AND state = 'pending' AND expires_at >= ?",
                    (owner_sub, now),
                ).fetchone()
                if pending >= max_pending:
                    return False
            # A note it depends on that is still rejected, withdrawn or failed holds it again.
            blocking = _blocking_dependency(c, proposal_id)
            changed = (
                c.execute(
                    "UPDATE proposals SET state = 'pending', status = ?, status_reason = ?, "
                    "status_at = ?, risk = NULL WHERE id = ? AND owner_sub = ? "
                    "AND state IN ('withdrawn', 'replaced') "
                    "AND expires_at >= ?",
                    (
                        "held" if blocking else None,
                        hold_reason(blocking) if blocking else None,
                        now,
                        proposal_id,
                        owner_sub,
                        now,
                    ),
                ).rowcount
                == 1
            )
            if changed:
                # Its board number stays unless another live note on the deck took it meanwhile;
                # then it gets the next free one, so a board never shows one number twice.
                c.execute(
                    """UPDATE proposals SET board_no = (
                         SELECT COALESCE(MAX(o.board_no), 0) + 1 FROM proposals o
                         WHERE o.owner_sub = proposals.owner_sub AND o.deck_id = proposals.deck_id
                           AND o.id != proposals.id
                           AND (o.state = 'applying' OR (o.state = 'pending' AND o.expires_at >= ?)))
                       WHERE id = ? AND (board_no IS NULL OR EXISTS (
                         SELECT 1 FROM proposals o WHERE o.owner_sub = proposals.owner_sub
                           AND o.deck_id = proposals.deck_id AND o.id != proposals.id
                           AND o.board_no = proposals.board_no
                           AND (o.state = 'applying' OR (o.state = 'pending' AND o.expires_at >= ?))))""",
                    (now, proposal_id, now),
                )
            return changed

    def link_proposals(self, proposal_id: str, owner_sub: str, kind: str, other_id: str) -> bool:
        """Record that ``proposal_id`` depends on, or replaces, ``other_id``. Both
        must be the owner's and different. True when stored (a repeat is a no-op, still True)."""
        if kind not in PROPOSAL_LINK_KINDS:
            raise ValueError(f"unknown proposal link {kind!r}")
        if proposal_id == other_id:
            return False
        with self.tx() as c:
            (owned,) = c.execute(
                "SELECT COUNT(*) FROM proposals WHERE owner_sub = ? AND id IN (?, ?)",
                (owner_sub, proposal_id, other_id),
            ).fetchone()
            if owned != 2:
                return False
            if _links_ahead.get() is not None:
                _links_ahead.set(None)  # a listing's read-ahead no longer tells the whole story
            c.execute(
                "INSERT OR IGNORE INTO proposal_links (proposal_id, kind, other_id, created_at) "
                "VALUES (?, ?, ?, ?)",
                (proposal_id, kind, other_id, int(time.time())),
            )
            return True

    @contextmanager
    def read_ahead(
        self, owner_sub: str, proposal_ids: Iterable[str], client_ids: Iterable[str | None] = ()
    ) -> Iterator[None]:
        """Within the block, ``proposal_links(owner_sub, [one of proposal_ids])`` and
        ``client_name(one of client_ids)`` answer from one query each made here, so a listing
        of N rows asks the database a fixed number of times instead of several per row. A link
        written inside the block drops the read-ahead (link_proposals), so nothing stale is served."""
        ids = [str(i) for i in proposal_ids]
        by_id: dict[str, list[dict[str, Any]]] = {i: [] for i in ids}
        # proposal_links reads at most 500 ids at once: read in slices of that size
        links = [
            lk for i in range(0, len(ids), 500) for lk in self.proposal_links(owner_sub, ids[i : i + 500])
        ]
        for link in links:
            for side in ("proposal_id", "other_id"):
                if link[side] in by_id and link not in by_id[link[side]]:
                    by_id[link[side]].append(link)
        names = self.client_names(client_ids)
        wanted = {c for c in client_ids if c}
        links_token = _links_ahead.set({owner_sub: by_id})
        names_token = _names_ahead.set({c: names.get(c) for c in wanted})
        try:
            yield
        finally:
            _links_ahead.reset(links_token)
            _names_ahead.reset(names_token)

    def proposal_links(self, owner_sub: str, proposal_ids: list[str]) -> list[dict[str, Any]]:
        """Every link from or to the given proposals of the owner: {proposal_id, kind, other_id,
        created_at}, oldest first."""
        ids = [str(i) for i in proposal_ids][:500]
        if not ids:
            return []
        ahead = _links_ahead.get()
        if ahead is not None and len(ids) == 1 and ids[0] in ahead.get(owner_sub, {}):
            return [dict(r) for r in ahead[owner_sub][ids[0]]]
        marks = _marks(ids)
        with self._lock:
            rows = self._conn.execute(
                f"""SELECT l.proposal_id, l.kind, l.other_id, l.created_at FROM proposal_links l
                    JOIN proposals p ON p.id = l.proposal_id AND p.owner_sub = ?
                    WHERE l.proposal_id IN ({marks}) OR l.other_id IN ({marks})
                    ORDER BY l.created_at, l.rowid""",
                (owner_sub, *ids, *ids),
            ).fetchall()
        return [dict(r) for r in rows]

    def claim_proposal(self, proposal_id: str, owner_sub: str, *, expect_version: int | None = None) -> bool:
        """Move pending -> applying atomically; False if it was not pending (duplicate apply), or
        with ``expect_version`` when it is at another version now (amended since it was read: the
        apply then reads its content again after a successful claim)."""
        with self.tx() as c:
            now = int(time.time())
            # applied_at marks when the apply started; finish_proposal overwrites it when it ends.
            sql = (
                "UPDATE proposals SET state = 'applying', applied_at = ? WHERE id = ? AND owner_sub = ? "
                "AND state = 'pending' AND expires_at >= ?"
            )
            args: tuple[Any, ...] = (now, proposal_id, owner_sub, now)
            if expect_version is not None:
                sql += " AND version = ?"
                args += (int(expect_version),)
            cur = c.execute(sql, args)
            return cur.rowcount == 1

    def rebase_proposal(
        self,
        proposal_id: str,
        owner_sub: str,
        *,
        was: str,
        fingerprint: str,
        status: str | None,
        reason: str | None = None,
        version: int | None = None,
    ) -> bool:
        """Record that a waiting proposal was checked against the deck as it is now (fingerprint):
        it still does what was reviewed ("updated": its baseline moves to that deck) or it would
        now do something else ("needs_look" with the reason; the baseline stays). Only while it is
        pending, still has the baseline ``was`` (and ``version``), and is not held (a hold stays
        until its dependency is settled). True when it was changed."""
        if status is not None and status not in PROPOSAL_STATUSES:
            raise ValueError(f"unknown proposal status {status!r}")
        sql = (
            "UPDATE proposals SET baseline_fingerprint = ?, status = ?, status_reason = ?, status_at = ? "
            "WHERE id = ? AND owner_sub = ? AND state = 'pending' AND baseline_fingerprint = ? "
            "AND COALESCE(status, '') != 'held'"
        )
        args: tuple[Any, ...] = (
            fingerprint if status == "updated" else was,
            status,
            reason,
            int(time.time()),
            proposal_id,
            owner_sub,
            was,
        )
        if version is not None:
            sql += " AND version = ?"
            args += (int(version),)
        with self.tx() as c:
            return c.execute(sql, args).rowcount == 1

    def fill_proposal_rows(
        self, proposal_id: str, owner_sub: str, rows: list[dict[str, Any]], version: int
    ) -> bool:
        """Store ``rows`` in place of a waiting proposal's review rows, only while it is pending at
        ``version`` (an amend since keeps its own rows). For details the stored rows lacked, never
        a change to what the proposal does: the version stays. True when it was stored."""
        with self.tx() as c:
            return (
                c.execute(
                    "UPDATE proposals SET rows_json = ? WHERE id = ? AND owner_sub = ? AND state = 'pending' "
                    "AND version = ?",
                    (json.dumps(rows), proposal_id, owner_sub, int(version)),
                ).rowcount
                == 1
            )

    def dependents(self, owner_sub: str, proposal_id: str) -> list[str]:
        """The owner's live waiting proposals that depend on ``proposal_id``."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT p.id FROM proposal_links l JOIN proposals p ON p.id = l.proposal_id "
                "WHERE l.other_id = ? AND l.kind = 'depends_on' AND p.owner_sub = ? "
                "AND p.state = 'pending' AND p.expires_at >= ? ORDER BY p.created_at, p.rowid",
                (proposal_id, owner_sub, int(time.time())),
            ).fetchall()
        return [str(r[0]) for r in rows]

    def hold_blocked(self, owner_sub: str) -> int:
        """Hold each live waiting note of the owner that depends on a rejected, withdrawn, failed
        or expired note, adding the cause to one held already. The apply paths settle their
        own links; this catches the ones that close a note without them: a restart mid-apply, a
        shutdown cancel, the six-hour backstop and expiry. Returns how
        many it held."""
        first = (
            f"SELECT {{}} FROM proposal_links l JOIN proposals o ON o.id = l.other_id "
            f"WHERE l.proposal_id = proposals.id AND l.kind = 'depends_on' "
            f"AND {_SEEN_STATE} IN {_HOLDING_SQL} ORDER BY l.created_at, l.rowid LIMIT 1"
        )
        reason = first.format("'depends:' || o.id || ':' || " + _SEEN_STATE)
        # a waiting note whose replaced note was applied on its own: applying it too
        # would change the deck twice
        gone = (
            "SELECT {} FROM proposal_links l JOIN proposals o ON o.id = l.other_id "
            "WHERE l.proposal_id = proposals.id AND l.kind = 'replaces' AND o.state = 'applied' "
            "ORDER BY l.created_at, l.rowid LIMIT 1"
        )
        args = {"sub": owner_sub, "now": int(time.time())}
        live = (
            "WHERE owner_sub = :sub AND state = 'pending' AND expires_at >= :now "
            "AND COALESCE(status, '') <> 'held' "
        )
        # one held already gets the new cause added to the ones it records (board.add_hold), so
        # the member sees it and status_at moves, by at least a second even within the same one
        again = "WHERE owner_sub = :sub AND state = 'pending' AND expires_at >= :now AND status = 'held' "

        def added(cause: str) -> str:
            return (
                "UPDATE proposals SET status_at = MAX(:now, COALESCE(status_at, 0) + 1), "
                f"status_reason = status_reason || '|' || ({cause}) "
                f"{again}AND instr('|' || COALESCE(status_reason, '') || '|', '|' || ({cause}) || '|') = 0 "
            )

        with self.tx() as c:
            held = c.execute(
                f"UPDATE proposals SET status = 'held', status_at = :now, status_reason = ({reason}) "
                f"{live}AND EXISTS ({first.format('1')})",
                args,
            ).rowcount
            held += c.execute(
                "UPDATE proposals SET status = 'held', status_at = :now, status_reason = "
                f"({gone.format(GONE_REASON_SQL)}) {live}AND EXISTS ({gone.format('1')})",
                args,
            ).rowcount
            c.execute(f"{added(reason)}AND EXISTS ({first.format('1')})", args)
            c.execute(f"{added(gone.format(GONE_REASON_SQL))}AND EXISTS ({gone.format('1')})", args)
            return held

    def set_hold(self, proposal_id: str, owner_sub: str, reason: str | None) -> bool:
        """Hold a waiting proposal (``reason``) or lift its hold (None: back to plain waiting).
        Lifting touches only a held one, so another status is never cleared by it."""
        now = int(time.time())
        with self.tx() as c:
            if reason is None:
                cur = c.execute(
                    "UPDATE proposals SET status = NULL, status_reason = NULL, status_at = ? "
                    "WHERE id = ? AND owner_sub = ? AND state = 'pending' AND status = 'held'",
                    (now, proposal_id, owner_sub),
                )
            else:
                # a new hold, or a new cause of one, always moves status_at forward (a press that
                # showed the earlier hold in the same second still counts as not having seen it)
                cur = c.execute(
                    "UPDATE proposals SET status = 'held', status_reason = ?, "
                    "status_at = MAX(?, COALESCE(status_at, 0) + 1) "
                    "WHERE id = ? AND owner_sub = ? AND state = 'pending'",
                    (reason, now, proposal_id, owner_sub),
                )
            return cur.rowcount == 1

    def find_duplicate(
        self, owner_sub: str, *, deck_id: str, kind: str, changes: Any, client: str | None
    ) -> str | None:
        """A live waiting proposal of the owner, made by the same app, on the same deck, of the
        same kind and with exactly these stored changes (only exact duplicates are skipped).
        One marked "needs a look" is not: it no longer does what was reviewed, and asking again is
        how the assistant makes it fresh from the deck as it is now."""
        row = self._one(
            "SELECT id FROM proposals WHERE owner_sub = ? AND deck_id = ? AND kind = ? "
            "AND changes_json = ? AND created_by_client IS ? AND state = 'pending' AND expires_at >= ? "
            "AND (status IS NULL OR status != 'needs_look') "
            "ORDER BY created_at, rowid LIMIT 1",
            (owner_sub, str(deck_id), kind, json.dumps(changes), client, int(time.time())),
        )
        return str(row[0]) if row else None

    def restart_expiry(self, proposal_id: str, owner_sub: str, expires_at: int) -> bool:
        """A waiting proposal's expiry starts again (an amend). Only while it is pending."""
        with self.tx() as c:
            return (
                c.execute(
                    "UPDATE proposals SET expires_at = ? "
                    "WHERE id = ? AND owner_sub = ? AND state = 'pending'",
                    (int(expires_at), proposal_id, owner_sub),
                ).rowcount
                == 1
            )

    def needs_look_since(self, proposal_id: str) -> int | None:
        """When a live waiting proposal was marked "needs a look"; None otherwise."""
        row = self._one(
            "SELECT status_at FROM proposals WHERE id = ? AND state = 'pending' AND expires_at >= ? "
            "AND status = 'needs_look'",
            (proposal_id, int(time.time())),
        )
        return int(row[0] or 0) or None if row else None

    def proposal_board_numbers(self, owner_sub: str, ids: list[str]) -> dict[str, int | None]:
        """id -> board number, for the owner's proposals among ``ids``."""
        ids = [str(i) for i in ids][:500]
        if not ids:
            return {}
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, board_no FROM proposals WHERE owner_sub = ? AND id IN ({_marks(ids)})",
                (owner_sub, *ids),
            ).fetchall()
        return {str(r[0]): r[1] for r in rows}

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
            # the apply start time that claim_proposal set; the purge relies on it. A proposal that
            # waits again keeps its own save's "no backup copy" mark (decks.NO_BACKUP_KEY) under
            # the new result; a null mark is dropped (json_patch).
            c.execute(
                "UPDATE proposals SET state = ?, "
                "applied_at = CASE WHEN ? = 'applying' THEN applied_at ELSE ? END, "
                "snapshot_id = COALESCE(?, snapshot_id), "
                "result_json = CASE WHEN ? = 'pending' AND ? IS NOT NULL THEN json_patch(?, json_object("
                "'no_backup_copy', CASE WHEN json_extract(result_json, '$.no_backup_copy') "
                "THEN json('true') END)) "
                "ELSE COALESCE(?, result_json) END, deck_id = COALESCE(?, deck_id) WHERE id = ?",
                (
                    state,
                    state,
                    int(time.time()) if state == "applied" else None,
                    snapshot_id,
                    state,
                    json.dumps(result) if result is not None else None,
                    json.dumps(result) if result is not None else None,
                    json.dumps(result) if result is not None else None,
                    deck_id,
                    proposal_id,
                ),
            )
        if state not in ("pending", "applying"):
            self.settle_risks([proposal_id])

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
        name, count, owner_id = snapshot_summary(deck)
        values = (
            snapshot_id,
            owner_sub,
            deck_id,
            proposal_id,
            int(time.time()),
            fingerprint,
            json.dumps(deck),
            name,
            count,
            owner_id,
            owner_sub,
        )
        # The Archidekt account on the member's active link now, read in the same statement.
        sql = """INSERT INTO snapshots (id, owner_sub, deck_id, proposal_id, taken_at, fingerprint,
                 deck_json, deck_name, card_count, deck_owner_id, archidekt_user_id)
                 SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        (SELECT archidekt_user_id FROM archidekt_links WHERE sub = ? AND status = 'active')"""
        if while_applying:
            sql += """ WHERE EXISTS (SELECT 1 FROM users WHERE sub = ?)
                       AND EXISTS (SELECT 1 FROM proposals WHERE id = ? AND owner_sub = ?
                                   AND state = 'applying')"""
            values += (owner_sub, proposal_id, owner_sub)
        with self.tx() as c:
            return c.execute(sql, values).rowcount == 1

    def list_snapshots(
        self,
        owner_sub: str,
        limit: int = 20,
        *,
        deck_id: str | None = None,
        search: str | None = None,
        since: int | None = None,
        until: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Newest first. Each row carries the deck name, card count and owner stored with the
        snapshot, never the deck itself (a snapshot is a whole deck; get_snapshot returns
        one). ``deck_id``, ``search`` (on the deck's name) and a time window (``since`` inclusive,
        ``until`` exclusive, epoch seconds) narrow the list in SQL."""
        sql = (
            "SELECT id, deck_id, proposal_id, taken_at, deck_name, card_count, deck_owner_id, "
            "archidekt_user_id, backup_deck_id, backup_url, "
            # Taken for an apply that stopped before it changed anything: its proposal is
            # open again or was closed without being applied (only a pending one is rejected or
            # expires), or a later apply of it took another snapshot.
            "(SELECT 1 FROM proposals p WHERE p.id = snapshots.proposal_id "
            "AND p.owner_sub = snapshots.owner_sub AND (p.state IN ('pending', 'expired', 'rejected') "
            "OR (p.snapshot_id IS NOT NULL AND p.snapshot_id != snapshots.id))) AS stopped_attempt "
            "FROM snapshots WHERE owner_sub = ?"
        )
        args: list[Any] = [owner_sub]
        if deck_id:
            sql += " AND deck_id = ?"
            args.append(str(deck_id))
        if search:
            sql += " AND deck_name LIKE ? ESCAPE '\\'"
            args.append(_like(search))
        if since is not None:
            sql += " AND taken_at >= ?"
            args.append(int(since))
        if until is not None:
            sql += " AND taken_at < ?"
            args.append(int(until))
        sql += " ORDER BY taken_at DESC, rowid DESC LIMIT ? OFFSET ?"
        args += [max(0, int(limit)), max(0, int(offset))]
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [
            {
                "snapshot_id": r["id"],
                "deck_id": r["deck_id"],
                "deck_name": r["deck_name"] or "",
                "proposal_id": r["proposal_id"],
                "taken_at": r["taken_at"],
                "backup_deck_id": r["backup_deck_id"],
                "backup_url": r["backup_url"],
                # the Archidekt account that owned the deck then (None when not recorded)
                "account_id": r["deck_owner_id"],
                # the Archidekt account linked when it was taken (None before schema 13)
                "linked_account_id": r["archidekt_user_id"],
                "card_count": r["card_count"],
                **({"stopped_attempt": True} if r["stopped_attempt"] else {}),
            }
            for r in rows
        ]

    def backup_copies(self, owner_sub: str) -> dict[str, dict[str, Any]]:
        """The Archidekt deck copies the gateway made for a member, by the copy's deck id:
        {backup_deck_id: {deck_id, snapshot_id, taken_at}} (the newest snapshot wins a repeat)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, deck_id, taken_at, backup_deck_id FROM snapshots "
                "WHERE owner_sub = ? AND backup_deck_id IS NOT NULL ORDER BY taken_at",
                (owner_sub,),
            ).fetchall()
        return {
            str(r["backup_deck_id"]): {
                "deck_id": r["deck_id"],
                "snapshot_id": r["id"],
                "taken_at": r["taken_at"],
            }
            for r in rows
        }

    def set_snapshot_backup(
        self, snapshot_id: str, *, backup_deck_id: str, backup_url: str, made: dict[str, Any] | None = None
    ) -> None:
        """Record the backup copy on the snapshot. ``made``: a copy just made, whose record of how
        it was made (record_backup_made) is written in the same transaction, so no restart or crash
        leaves a snapshot naming a copy made by this version without its exact record (which would
        make it look like a copy made before 0.7.29, which is never deleted)."""
        with self.tx() as c:
            c.execute(
                "UPDATE snapshots SET backup_deck_id = ?, backup_url = ? WHERE id = ?",
                (backup_deck_id, backup_url, snapshot_id),
            )
            if made is not None:
                c.execute(
                    "INSERT OR REPLACE INTO job_runs (name, day, at, detail_json) VALUES (?, ?, ?, ?)",
                    (
                        BACKUP_MADE_JOB + str(backup_deck_id),
                        self._since_day(0),
                        int(time.time()),
                        json.dumps(made),
                    ),
                )

    def clear_snapshot_backup(self, snapshot_id: str) -> None:
        """The snapshot's Archidekt backup copy was deleted (an apply that failed before it wrote)."""
        with self.tx() as c:
            c.execute(
                "UPDATE snapshots SET backup_deck_id = NULL, backup_url = NULL WHERE id = ?", (snapshot_id,)
            )

    def clear_backup_everywhere(self, owner_sub: str, backup_deck_id: str) -> int:
        """A backup copy was deleted on Archidekt: every snapshot of the member that records it
        forgets it (a copy can be shared by several snapshots), and so
        does the record of how it was made."""
        with self.tx() as c:
            n = c.execute(
                "UPDATE snapshots SET backup_deck_id = NULL, backup_url = NULL "
                "WHERE owner_sub = ? AND backup_deck_id = ?",
                (owner_sub, str(backup_deck_id)),
            ).rowcount
            c.execute("DELETE FROM job_runs WHERE name = ?", (BACKUP_MADE_JOB + str(backup_deck_id),))
        return n

    def backup_users(self, owner_sub: str, backup_deck_id: str) -> int:
        """How many of the member's snapshots record this backup copy."""
        row = self._one(
            "SELECT COUNT(*) AS n FROM snapshots WHERE owner_sub = ? AND backup_deck_id = ?",
            (owner_sub, str(backup_deck_id)),
        )
        return int(row["n"]) if row else 0

    def deck_backup_copies(self, owner_sub: str, deck_id: str) -> list[dict[str, Any]]:
        """The backup copies the member's snapshots of ``deck_id`` record, newest first: one entry
        per copy with its url, the snapshot it was made with (the oldest that records it) and
        that snapshot's time."""
        with self._lock:
            rows = self._conn.execute(
                """SELECT backup_deck_id, MAX(backup_url) AS backup_url, MIN(taken_at) AS made_at,
                          (SELECT s2.id FROM snapshots s2
                           WHERE s2.owner_sub = s.owner_sub AND s2.deck_id = s.deck_id
                             AND s2.backup_deck_id = s.backup_deck_id
                           ORDER BY s2.taken_at, s2.rowid LIMIT 1) AS snapshot_id
                   FROM snapshots s WHERE owner_sub = ? AND deck_id = ? AND backup_deck_id IS NOT NULL
                   GROUP BY backup_deck_id ORDER BY made_at DESC, backup_deck_id DESC""",
                (owner_sub, str(deck_id)),
            ).fetchall()
        return [dict(r) for r in rows]

    def record_backup_made(self, backup_deck_id: str, record: dict[str, Any]) -> None:
        """How the gateway made a backup copy (name, description, folder, the time its writes took,
        when, for whom): kept in the database so the checks before a later delete outlive a
        restart. One job_runs row per copy; schema 13 is unchanged."""
        now = int(time.time())
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO job_runs (name, day, at, detail_json) VALUES (?, ?, ?, ?)",
                (BACKUP_MADE_JOB + str(backup_deck_id), self._since_day(0), now, json.dumps(record)),
            )

    def record_backup_folder(self, sub: str, folder_id: str | int) -> None:
        """Remember that the gateway put ``sub``'s backup copies in the Archidekt folder
        ``folder_id``, so the folder is known as the backup folder after a rename on Archidekt,
        even once no copy is left inside. Kept until the member's data is deleted."""
        with self.tx() as c:
            c.execute(
                "INSERT OR REPLACE INTO job_runs (name, day, at, detail_json) VALUES (?, ?, ?, ?)",
                (f"{BACKUP_FOLDER_JOB}{sub}:{int(folder_id)}", self._since_day(0), int(time.time()), "{}"),
            )

    def backup_folder_ids(self, sub: str) -> set[int]:
        """The Archidekt folders the gateway put ``sub``'s backup copies in (record_backup_folder,
        and the folder each recorded copy was made in)."""
        prefix = f"{BACKUP_FOLDER_JOB}{sub}:"
        with self._lock:
            names = self._conn.execute(
                "SELECT substr(name, ?) FROM job_runs WHERE substr(name, 1, ?) = ?",
                (len(prefix) + 1, len(prefix), prefix),
            ).fetchall()
            made = self._conn.execute(
                """SELECT folder_id FROM (
                       SELECT CASE WHEN json_valid(detail_json)
                                   THEN json_extract(detail_json, '$.folder_id') END AS folder_id,
                              CASE WHEN json_valid(detail_json)
                                   THEN json_extract(detail_json, '$.sub') END AS sub
                       FROM job_runs WHERE substr(name, 1, ?) = ?)
                   WHERE sub = ?""",
                (len(BACKUP_MADE_JOB), BACKUP_MADE_JOB, sub),
            ).fetchall()
        out: set[int] = set()
        for (v,) in [*names, *made]:
            try:
                out.add(int(v))
            except (TypeError, ValueError):
                continue
        return out

    def backup_made(self, backup_deck_id: str) -> dict[str, Any] | None:
        row = self._one(
            "SELECT detail_json FROM job_runs WHERE name = ?", (BACKUP_MADE_JOB + str(backup_deck_id),)
        )
        if row is None or not row["detail_json"]:
            return None
        try:
            out = json.loads(row["detail_json"])
        except ValueError:
            return None
        return out if isinstance(out, dict) else None

    # Later deletes of unused backup copies: one row per snapshot whose copy waits to be
    # deleted (``due_at`` the time it is due), so the queue outlives a restart and a re-apply of
    # the proposal. Each row records the Archidekt account linked when it was queued.
    _BACKUP_DELETE_COLUMNS = (
        "snapshot_id, sub, archidekt_user_id, backup_deck_id, deck_id, proposal_id, due_at, attempts, "
        "queued_at, expect_json"
    )

    @staticmethod
    def _backup_delete_row(r: sqlite3.Row) -> dict[str, Any]:
        """A queued delete as the backup code reads it (the shape it had in job_runs, plus the
        Archidekt account)."""
        d: dict[str, Any] = {
            "sub": r["sub"],
            "snapshot_id": r["snapshot_id"],
            "backup_deck_id": r["backup_deck_id"],
            "deck_id": r["deck_id"],
            "proposal_id": r["proposal_id"],
            "attempts": r["attempts"],
            "queued_at": r["queued_at"],
            "archidekt_user_id": r["archidekt_user_id"],
        }
        if r["expect_json"]:
            d["expect"] = json.loads(r["expect_json"])
        d["due"] = r["due_at"]
        return d

    def queue_backup_delete(
        self,
        snapshot_id: str,
        *,
        sub: str,
        backup_deck_id: str,
        deck_id: str,
        proposal_id: str | None,
        due: int,
        expect: dict[str, Any] | None = None,
    ) -> None:
        """``expect``: the copy as the gateway made it (name, description, folder id), which the
        later delete checks before it deletes. The Archidekt account is the one on the
        member's active link now, read in the same transaction."""
        with self.tx() as c:
            c.execute(
                f"""INSERT OR REPLACE INTO backup_deletes ({self._BACKUP_DELETE_COLUMNS})
                   VALUES (?, ?, (SELECT archidekt_user_id FROM archidekt_links
                                  WHERE sub = ? AND status = 'active'), ?, ?, ?, ?, 0, ?, ?)""",
                (
                    snapshot_id,
                    sub,
                    sub,
                    str(backup_deck_id),
                    str(deck_id),
                    proposal_id,
                    int(due),
                    int(time.time()),
                    json.dumps(expect) if expect else None,
                ),
            )

    def next_backup_delete(self, now: int) -> dict[str, Any] | None:
        """The queued backup delete due first (at or before ``now``), or None."""
        row = self._one(
            f"SELECT {self._BACKUP_DELETE_COLUMNS} FROM backup_deletes WHERE due_at <= ? "
            "ORDER BY due_at, snapshot_id LIMIT 1",
            (int(now),),
        )
        return self._backup_delete_row(row) if row else None

    def queued_backup_deletes(self) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                f"SELECT {self._BACKUP_DELETE_COLUMNS} FROM backup_deletes ORDER BY due_at, snapshot_id"
            ).fetchall()
        return [self._backup_delete_row(r) for r in rows]

    def reschedule_backup_delete(self, snapshot_id: str, *, due: int, attempts: int) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE backup_deletes SET due_at = ?, attempts = ? WHERE snapshot_id = ?",
                (int(due), int(attempts), snapshot_id),
            )

    def backup_delete_queued(self, snapshot_id: str) -> bool:
        """Whether the backup delete of ``snapshot_id`` is still queued (a relink or "Delete my
        data" drops it)."""
        return self._one("SELECT 1 FROM backup_deletes WHERE snapshot_id = ?", (snapshot_id,)) is not None

    def backup_copy_queued(self, owner_sub: str, backup_deck_id: str) -> bool:
        """Whether a delete of this backup copy is queued for the member (from any snapshot)."""
        return (
            self._one(
                "SELECT 1 FROM backup_deletes WHERE sub = ? AND backup_deck_id = ?",
                (owner_sub, str(backup_deck_id)),
            )
            is not None
        )

    def drop_backup_delete(self, snapshot_id: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM backup_deletes WHERE snapshot_id = ?", (snapshot_id,))

    def edit_proposal_result(
        self, proposal_id: str, owner_sub: str, edit: Callable[[Any], dict[str, Any] | None]
    ) -> bool:
        """Replace a proposal's stored result with ``edit(result)``, read and written in one
        transaction (an apply may be rewriting it); ``edit`` returns None to leave it. The state
        is never changed. True when it was replaced."""
        with self.tx() as c:
            row = c.execute(
                "SELECT result_json FROM proposals WHERE id = ? AND owner_sub = ?", (proposal_id, owner_sub)
            ).fetchone()
            if row is None:
                return False
            new = edit(json.loads(row["result_json"]) if row["result_json"] else None)
            if new is None:
                return False
            c.execute("UPDATE proposals SET result_json = ? WHERE id = ?", (json.dumps(new), proposal_id))
            return True

    def snapshot_held(self, snapshot_id: str, owner_sub: str) -> str | None:
        """Why the member's snapshot can't be deleted now, or None when it can: a waiting (or
        running) restore puts the deck back to it, or it holds the gateway's record of a backup
        copy on Archidekt the gateway may still delete (``_prune_snapshots`` keeps both too)."""
        with self._lock:
            return self._snapshot_held(self._conn, snapshot_id, owner_sub)

    @staticmethod
    def _snapshot_held(c: sqlite3.Connection, snapshot_id: str, owner_sub: str) -> str | None:
        if c.execute(
            "SELECT 1 FROM proposals WHERE owner_sub = ? AND kind = 'restore' AND state IN ('pending', "
            "'applying') AND json_extract(changes_json, '$.snapshot_id') = ? LIMIT 1",
            (owner_sub, snapshot_id),
        ).fetchone():
            return SNAPSHOT_HELD_RESTORE
        if (
            c.execute("SELECT 1 FROM backup_deletes WHERE snapshot_id = ? LIMIT 1", (snapshot_id,)).fetchone()
            or c.execute(
                "SELECT 1 FROM snapshots s WHERE s.id = ? AND s.owner_sub = ? AND "
                "s.backup_deck_id IS NOT NULL AND s.id = (SELECT s2.id FROM snapshots s2 WHERE "
                "s2.owner_sub = s.owner_sub AND s2.deck_id = s.deck_id AND "
                "s2.backup_deck_id = s.backup_deck_id ORDER BY s2.taken_at, s2.rowid LIMIT 1) AND "
                "EXISTS (SELECT 1 FROM job_runs j WHERE j.name = ? || s.backup_deck_id AND "
                "coalesce(json_extract(j.detail_json, '$.kept'), 0) != 1)",
                (snapshot_id, owner_sub, BACKUP_MADE_JOB),
            ).fetchone()
        ):
            return SNAPSHOT_HELD_BACKUP
        return None

    def delete_snapshot(self, snapshot_id: str, owner_sub: str) -> bool:
        """Delete one of the member's snapshots (the caller checked ``snapshot_held`` first; both
        holds are checked again in the same step). False when there is no such snapshot of theirs,
        or it is held."""
        with self.tx() as c:
            if self._snapshot_held(c, snapshot_id, owner_sub):
                return False
            return (
                c.execute(
                    "DELETE FROM snapshots WHERE id = ? AND owner_sub = ?", (snapshot_id, owner_sub)
                ).rowcount
                > 0
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
        # The copy is made and cleaned in memory first, so no file ever holds a sign-in, not even
        # for a moment (a crash mid-backup leaves nothing in the backup folder that has one).
        mem = sqlite3.connect(":memory:")
        try:
            if str(self.path) == ":memory:":
                with self._lock:
                    self._conn.backup(mem)
            else:
                source = sqlite3.connect(str(self.path))
                try:
                    source.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
                    source.backup(mem)
                finally:
                    source.close()
            # No sign-in leaves the server in a backup: every member's Archidekt session and the
            # identity provider's tokens are blanked in the copy (and VACUUM leaves no freed page
            # holding them). A restored backup therefore asks members to sign in and relink.
            mem.execute("PRAGMA secure_delete=ON")
            mem.execute(
                "UPDATE archidekt_links SET secret_enc = '', status = 'revoked' WHERE secret_enc != ''"
            )
            mem.execute("UPDATE idp_grants SET refresh_enc = '', access_enc = ''")
            # A member's notification topic works as their phone's key: a restored backup asks
            # phones to connect again rather than keep a key anyone holding a backup could read.
            mem.execute("UPDATE notify_prefs SET ntfy_topic = NULL WHERE ntfy_topic IS NOT NULL")
            mem.commit()
            mem.execute("VACUUM")
            target = sqlite3.connect(str(dest))
            try:
                mem.backup(target)
                # The copy must not need a WAL beside it: a plain rollback-journal file.
                target.execute("PRAGMA journal_mode=DELETE")
                result = target.execute("PRAGMA quick_check").fetchone()[0]
                if result != "ok":
                    raise RuntimeError(f"backup copy failed its integrity check: {result}")
            finally:
                target.close()
        finally:
            mem.close()

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

    # settings the server owner manages (config.LiveSettings, the admin Settings page) ------------
    def stored_settings(self) -> dict[str, Any]:
        """Every value stored from the admin Settings page, by key (config.MANAGED_SETTINGS). A
        row that no longer parses is left out, so the code default applies."""
        with self._lock:
            rows = self._conn.execute("SELECT key, value_json FROM settings").fetchall()
        out: dict[str, Any] = {}
        for r in rows:
            try:
                out[str(r["key"])] = json.loads(r["value_json"])
            except ValueError:
                continue
        return out

    def save_setting(self, key: str, value: Any, *, by: str | None) -> None:
        """Store one setting's value (already checked by config.LiveSettings) and who set it."""
        with self.tx() as c:
            c.execute(
                "INSERT INTO settings (key, value_json, updated_at, updated_by) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json, "
                "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
                (key, json.dumps(value), int(time.time()), by),
            )

    def clear_setting(self, key: str) -> bool:
        """Forget a stored value, so the code default applies again. True when one was stored."""
        with self.tx() as c:
            return c.execute("DELETE FROM settings WHERE key = ?", (key,)).rowcount > 0

    def setting_changes(self) -> dict[str, dict[str, Any]]:
        """When each stored setting was last changed and by whom: {key: {updated_at, updated_by}}."""
        with self._lock:
            rows = self._conn.execute("SELECT key, updated_at, updated_by FROM settings").fetchall()
        return {str(r["key"]): {"updated_at": r["updated_at"], "updated_by": r["updated_by"]} for r in rows}

    # remembered choices that follow the account ----------------------------------------------
    def get_prefs(self, sub: str) -> dict[str, Any]:
        """Every remembered choice of one account, by name."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT name, value_json FROM account_prefs WHERE sub = ?", (sub,)
            ).fetchall()
        out: dict[str, Any] = {}
        for r in rows:
            try:
                out[str(r["name"])] = json.loads(r["value_json"])
            except ValueError:
                continue
        return out

    def get_pref(self, sub: str, name: str, default: Any = None) -> Any:
        row = self._one("SELECT value_json FROM account_prefs WHERE sub = ? AND name = ?", (sub, name))
        if row is None:
            return default
        try:
            return json.loads(row["value_json"])
        except ValueError:
            return default

    def set_pref(
        self, sub: str, name: str, value: Any, *, keep_newest: tuple[str, int] | None = None
    ) -> bool:
        """Remember one choice for the account. Refused (False) for a name that is empty or over
        MAX_PREF_NAME characters, a value over MAX_PREF_BYTES as JSON, an account that is not a
        known user, and a new name once the account keeps MAX_PREFS_PER_ACCOUNT choices.
        ``keep_newest=(prefix, n)``: a new name starting with ``prefix`` first forgets the oldest
        choices with that prefix so at most ``n`` remain with it (stack orders)."""
        name = str(name)
        raw = json.dumps(value, separators=(",", ":"))
        if not name or len(name) > MAX_PREF_NAME or len(raw.encode("utf-8")) > MAX_PREF_BYTES:
            return False
        with self.tx() as c:
            if c.execute("SELECT 1 FROM users WHERE sub = ?", (sub,)).fetchone() is None:
                return False
            known = c.execute(
                "SELECT 1 FROM account_prefs WHERE sub = ? AND name = ?", (sub, name)
            ).fetchone()
            if known is None and keep_newest is not None and name.startswith(keep_newest[0]):
                prefix, n = keep_newest
                c.execute(
                    "DELETE FROM account_prefs WHERE sub = ? AND rowid IN (SELECT rowid FROM account_prefs "
                    "WHERE sub = ? AND name LIKE ? ESCAPE '\\' ORDER BY updated_at DESC, rowid DESC "
                    "LIMIT -1 OFFSET ?)",
                    (sub, sub, _like_prefix(prefix), max(0, n - 1)),
                )
            if known is None:
                (count,) = c.execute("SELECT COUNT(*) FROM account_prefs WHERE sub = ?", (sub,)).fetchone()
                if count >= MAX_PREFS_PER_ACCOUNT:
                    return False
            c.execute(
                "INSERT INTO account_prefs (sub, name, value_json, updated_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(sub, name) DO UPDATE SET value_json = excluded.value_json, "
                "updated_at = excluded.updated_at",
                (sub, name, raw, int(time.time())),
            )
        return True

    def delete_prefs(self, sub: str, *, names: Iterable[str] = (), prefixes: Iterable[str] = ()) -> int:
        """Forget the account's choices with these names or name prefixes. Returns how many."""
        names, prefixes = list(names), list(prefixes)
        where = ["name = ?"] * len(names) + ["name LIKE ? ESCAPE '\\'"] * len(prefixes)
        if not where:
            return 0
        with self.tx() as c:
            return c.execute(
                f"DELETE FROM account_prefs WHERE sub = ? AND ({' OR '.join(where)})",
                (sub, *names, *(_like_prefix(p) for p in prefixes)),
            ).rowcount

    def delete_pref(self, sub: str, name: str) -> bool:
        with self.tx() as c:
            return c.execute("DELETE FROM account_prefs WHERE sub = ? AND name = ?", (sub, name)).rowcount > 0

    # -- simulator card models (schema 14) -----------------------------------------------------
    # Decided: each member keeps their own models; the member and an admin may
    # remove one (delete_card_model, admin_delete_card_model; the admin list is list_card_models);
    # a member's models go with their data (MEMBER_TABLES). put_card_model
    # enforces, when given ``most``, the cap of 200 per member in the same transaction as its write,
    # so two saves at once can't both pass the check. Each model records
    # the simulator version it was checked with (``sim_version``);
    # ``oracle_hash`` is kept as a spare column.

    def get_card_models(self, keys: list[str], sub: str) -> list[dict[str, Any]]:
        """``sub``'s models for these card keys (front face, casefolded), in no particular order;
        a key they keep no model for is simply absent."""
        wanted = list(dict.fromkeys(str(k) for k in keys if k))
        out: list[dict[str, Any]] = []
        for i in range(0, len(wanted), _CARD_MODEL_BATCH):
            part = wanted[i : i + _CARD_MODEL_BATCH]
            with self._lock:
                rows = self._conn.execute(
                    "SELECT card_key, name, model_json, sim_version, oracle_hash, added_by, added_at, "
                    f"updated_at FROM card_models WHERE added_by = ? AND card_key IN ({_marks(part)})",
                    [sub, *part],
                ).fetchall()
            out += [dict(r) for r in rows]
        return out

    def put_card_model(
        self,
        card_key: str,
        name: str,
        model_json: str,
        sub: str,
        now: int,
        *,
        sim_version: str | None = None,
        oracle_hash: str | None = None,
        most: int | None = None,
    ) -> bool:
        """Keep ``model_json`` as ``sub``'s model of the card, replacing theirs if they keep one;
        other members' models of the card are never touched. ``added_at`` stays that of their first
        model of the card. True when a model was added (it now counts toward ``sub``), False when
        theirs was replaced. ``sim_version`` (the simulator version the model was checked with) and
        ``oracle_hash`` are written as given, so a save without them clears them.

        With ``most``, a save that would add a model is refused once ``sub`` keeps that many,
        checked in the write's own transaction, so two saves at once can't both pass. Raises
        CardModelRefused when nothing is written, and ValueError for an empty key or member, or a
        model over MAX_CARD_MODEL_BYTES."""
        if not card_key or not sub:
            raise ValueError("a card model needs a card key and a member")
        if len(model_json.encode("utf-8")) > MAX_CARD_MODEL_BYTES:
            raise ValueError(f"a card model is at most {MAX_CARD_MODEL_BYTES} bytes")
        with self.tx() as c:
            row = c.execute(
                "SELECT 1 FROM card_models WHERE card_key = ? AND added_by = ?", (card_key, sub)
            ).fetchone()
            if row is None and most is not None:
                (have,) = c.execute("SELECT COUNT(*) FROM card_models WHERE added_by = ?", (sub,)).fetchone()
                if have >= most:
                    raise CardModelRefused("most")
            if row is None:
                c.execute(
                    "INSERT INTO card_models (card_key, name, model_json, sim_version, oracle_hash, "
                    "added_by, added_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (card_key, name, model_json, sim_version, oracle_hash, sub, int(now), int(now)),
                )
                return True
            c.execute(
                "UPDATE card_models SET name = ?, model_json = ?, sim_version = ?, oracle_hash = ?, "
                "updated_at = ? WHERE card_key = ? AND added_by = ?",
                (name, model_json, sim_version, oracle_hash, int(now), card_key, sub),
            )
            return False

    def delete_card_model(self, card_key: str, sub: str) -> bool:
        """Delete ``sub``'s model of the card. True when a model was deleted."""
        with self.tx() as c:
            return (
                c.execute(
                    "DELETE FROM card_models WHERE card_key = ? AND added_by = ?", (card_key, sub)
                ).rowcount
                > 0
            )

    def admin_delete_card_model(self, card_key: str, added_by: str, *, by: str) -> bool:
        """An admin removes any member's model: ``added_by``'s model of the card.
        Audited as ``card_model_removed`` under the admin, with the card and whose model it was.
        True when a model was deleted."""
        with self.tx() as c:
            row = c.execute(
                "SELECT name, added_by FROM card_models WHERE card_key = ? AND added_by = ?",
                (card_key, added_by),
            ).fetchone()
            if row is None:
                return False
            c.execute("DELETE FROM card_models WHERE card_key = ? AND added_by = ?", (card_key, added_by))
        self.audit(
            "card_model_removed",
            sub=by,
            detail={"card": str(row["name"]), "card_key": card_key, "added_by": str(row["added_by"])},
        )
        return True

    def list_card_models(self, *, limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
        """The kept models for the admin list, the most recently changed first:
        card_key, name, added_by, added_at, updated_at and size (bytes of model_json). Holds member
        ids: only for an admin's page, never another member's."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT card_key, name, added_by, added_at, updated_at, "
                "length(CAST(model_json AS BLOB)) AS size FROM card_models "
                "ORDER BY updated_at DESC, card_key, added_by LIMIT ? OFFSET ?",
                (max(0, int(limit)), max(0, int(offset))),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_all_card_models(self) -> int:
        """How many models are kept, for everyone."""
        row = self._one("SELECT COUNT(*) AS n FROM card_models", ())
        return int(row["n"]) if row else 0

    def card_models_of(self, sub: str) -> list[dict[str, Any]]:
        """``sub``'s own kept models for their My card models list: card_key, name, added_at,
        updated_at, the most recently changed first. Never another member's."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT card_key, name, added_at, updated_at FROM card_models WHERE added_by = ? "
                "ORDER BY updated_at DESC, name COLLATE NOCASE",
                (sub,),
            ).fetchall()
        return [dict(r) for r in rows]

    def count_card_models(self, sub: str) -> int:
        """How many of the kept models are ``sub``'s."""
        row = self._one("SELECT COUNT(*) AS n FROM card_models WHERE added_by = ?", (sub,))
        return int(row["n"]) if row else 0
