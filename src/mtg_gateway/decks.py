"""Per-user Archidekt access: account links, deck reads, and the propose/apply flow.

Every entry point takes the gateway subject (``sub``) of the signed-in user and
only touches rows that belong to that subject. Provider session tokens are
stored Fernet-encrypted; the Archidekt password is used once for the login
exchange and never stored.

Writes are two-step. ``propose`` fetches the deck, records a fingerprint of its
current state and stores a human-readable diff. ``apply`` is refused unless
writes are enabled, re-fetches the deck, rejects the proposal if the deck
changed in the meantime, snapshots it, sends the change, then re-fetches and
checks the result. The modifyCards payload shape is taken from third-party
source reading and is not verified against Archidekt (see ``build_payload``).
"""

from __future__ import annotations

import asyncio
import contextlib
import contextvars
import dataclasses
import datetime
import hashlib
import heapq
import json
import logging
import math
import pickle
import re
import secrets
import time
import uuid
import weakref
from collections import Counter
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from functools import wraps
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

from . import batch, board, formatting, modes, refusals
from .approve import approval_code, approval_matches, approval_set_code, card_seq_of
from .archidekt import (
    FORMAT_IDS,
    GAME_IDS,
    GAME_LABELS,
    MAX_LIST_PAGES,
    NOT_ACTED_ON,
    PATIENCE_SPENT,
    RATE_LIMIT_WAITS,
    VOTE_DOWN,
    VOTE_NONE,
    ArchidektClient,
    ArchidektError,
    Deck,
    DeckCard,
    DeckList,
    Pacer,
    Patience,
    QuillDoc,
    _owned_by,
    apply_writes,
    art_url,
    backup_name,
    backup_writes,
    finish_modifier,
    front_face,
    jwt_exp,
    list_row,
    no_retry,
    page_turn,
    parse_deck,
    patient,
    plain_text,
    rich_text,
    same_rich_text,
    wait_note,
    written_text,
)
from .archidekt_csv import CsvError, parse_export
from .busy import BUSY_TEXTS
from .config import LiveSettings, Settings
from .db import MAX_PROPOSAL_VERSIONS, Database, ProposalVersionsFull
from .decklist import (
    DecklistError,
    ListCard,
    clean_category,
    clean_text,
    front_faces,
    parse_decklist,
    to_text,
)
from .timing import add_time, archidekt_time

logger = logging.getLogger(__name__)

ACTIONS = (
    "add",
    "remove",
    "set_quantity",
    "set_category",
    "set_commander",
    "set_finish",
    "set_printing",
    "set_label",
    "set_mana_value",
    "set_companion",
    "set_back_face",
)
COUNT_ACTIONS = ("add", "remove", "set_quantity")
# The row switches: action -> (the change's field, the spec's key, Archidekt's field, the words)
SWITCH_ACTIONS = ("set_companion", "set_back_face")
SWITCHES = {
    "set_companion": ("companion", "companion", "companion", "companion"),
    "set_back_face": ("back_face", "flipped_default", "flippedDefault", "show back face"),
}
CATEGORY_ACTIONS = ("set_category", "set_commander")
# Changes to a card already in the deck, row by row: its finish, the printing itself, its
# colour tag (Archidekt's "label", stored on a row as "Name,#rrggbb") or its custom mana value
# (Archidekt's "customCmc", the deck's own override of the card's mana value).
# set_companion and set_back_face (parity release 1, 2026-10-22) switch the row's Companion and
# "show back face by default" (Archidekt's companion and flippedDefault), as its card menu does.
PRINTING_ACTIONS = ("set_finish", "set_printing", "set_label", "set_mana_value", *SWITCH_ACTIONS)
# Row-by-row changes that also work on the maybeboard and sideboard rows (zone "side").
ROW_TAG_ACTIONS = ("set_label", "set_mana_value", *SWITCH_ACTIONS)
# Changes that may name one row of the card by its relation id (the deck page sends it): that
# row only is removed, counted, moved or given another printing or finish, in whichever zone it
# is, never the card's other rows (a card in the deck and in "Optional Artwork", say).
ROW_TARGET_ACTIONS = ("remove", "set_quantity", "set_category", "set_printing", "set_finish")
MAX_MANA_VALUE = 15  # the range of archidekt.com's own Mana value menu
LABEL_COLOUR = re.compile(r"#[0-9a-fA-F]{6}")
MAX_LABEL = 40
COMMANDER = "Commander"
# The purpose named inside every stored Archidekt session (see DeckService._seal).
SESSION_PURPOSE = "archidekt_session"
SEALED_MARKER = "archidekt_sessions_sealed"  # audit event: the one-time reseal has run
MAX_CATEGORY = 60
# Deck details propose_deck_details may change, with their Archidekt field names.
DETAIL_FIELDS = {
    "name": "name",
    "description": "description",
    "deck_format": "deckFormat",
    "edh_bracket": "edhBracket",
    "private": "private",
    "unlisted": "unlisted",
    # deck settings' Theorycrafted and Game Type (parity release 1, 2026-10-22)
    "theorycrafted": "theorycrafted",
    "game": "game",
}
# Game Type as members and assistants name it: Any (Archidekt's null) or one of GAME_IDS
GAME_CHOICES = ("any", *GAME_IDS)
MAX_DESCRIPTION = 20_000
# Deck organisation propose_deck_details may also change, resolved against the member's account
# when the proposal is made: the folder the deck sits in, its tags and its cover card.
ORGANISE_FIELDS = ("folder", "add_tags", "remove_tags", "cover", "package")
# The OAuth client (or "__browser__") acting on the current request, recorded in the audit log.
# Set by the MCP tool layer and the browser pages; None when the actor is not known.
current_client: ContextVar[str | None] = ContextVar("current_client", default=None)
MAX_CHANGES = 40
MAX_PACKAGE_PAGES = 4  # the member's own card packages read for My decks, 50 a page
# How long an apply call waits for the apply to finish before it answers "applying". The apply
# itself carries on in the background (a 100-card new deck is a few hundred paced requests, longer
# than an assistant app waits for one tool call); get_proposal and the review page show its
# progress until it ends.
APPLY_WAIT_SECONDS = 20.0
# A new deck's cards go to Archidekt this many to a request (modifyCards takes a list), instead of
# one request per card: a 125-card deck is three writes, not 125.
CARD_WRITE_BATCH = 50
# At shutdown (a redeploy), how long a running apply may go on before it is cut off and recorded
# as interrupted: long enough for a paced 100-card apply. It runs after the server's own graceful
# wait for requests (GRACEFUL_SHUTDOWN_SECONDS in __main__), and the two together stay under the
# stack's stop_grace_period, after which Docker kills the container with nothing recorded.
SHUTDOWN_GRACE_SECONDS = 90.0
# The unused backup copy of a failed apply that changed nothing. When Archidekt refuses its
# delete for a while (a pause is running, a 429, a 5xx, a network error, the breaker), the delete
# is queued and tried again later by backup_delete_loop, one at a time across all members, never
# while the client is paused: first when the pause ends plus BACKUP_DELETE_MARGIN, then after each
# failure at least the pause left and at least BACKUP_DELETE_BACKOFF seconds, doubling, until
# BACKUP_DELETE_TRIES later attempts have failed. Any other refusal keeps the copy at once.
BACKUP_KEPT = "The unused backup copy could not be deleted; it stays in the backup folder."
BACKUP_LATER = (
    "The unused backup copy will be deleted from the backup folder by itself once Archidekt allows it."
)
BACKUP_DELETE_TRANSIENT = frozenset({"rate_limited", "unavailable"})
# A copy of a deck (clone) that failed or stopped after Archidekt made the copy: the
# copy holds the cards, so the new-deck sentence ("no cards were added") would be wrong.
COPY_MADE = (
    "The copy {deck_id} was made on Archidekt, but the gateway could not check it. Look at it on "
    "Archidekt before asking for another copy."
)
BACKUP_DELETE_MARGIN = 30
BACKUP_DELETE_BACKOFF = 600
BACKUP_DELETE_TRIES = 4
BACKUP_DELETE_EVERY = 60.0  # seconds between the loop's passes
# Before a later delete, the copy is read once and deleted only while it is as the
# gateway made it: in the backup folder, with the name and description it was given, and with
# Archidekt's updatedAt no later than its own createdAt plus the time the gateway's own writes to
# the copy took (measured as it made the copy, rounded up to whole seconds) plus this many seconds.
# One clock, Archidekt's. The slack covers a time that Archidekt might keep to whole
# seconds only (up to 1 s off each way); everything the gateway writes is in the measured part.
BACKUP_UNCHANGED_SLACK = 2
BACKUPS_REMEMBERED = 256  # backup copies made recently whose name and description are kept
# An edit reuses the deck's newest backup copy instead of
# making another when the deck is unchanged since that copy, or the copy is under this many
# seconds old. Every edit still takes its own snapshot.
BACKUP_SHARE_SECONDS = 15 * 60
# A delete pressed this soon after its box opened uses the deck and backup folder the box read
# (Decks.prepare_delete): the backup copy itself is Archidekt's own copy of the deck as it is then.
DELETE_PREP_SECONDS = 60
DELETE_PREP_REMEMBERED = 256
BROWSER_CLIENT = "__browser__"  # the same value as pages.BROWSER_CLIENT_ID
# Kept in a member's own proposal's result while it waits: its save asked for no backup copy.
NO_BACKUP_KEY = "no_backup_copy"
# An administrator acting on a member's account from the admin pages (unlink): recorded in the
# member's own activity log as done by an administrator, never as the member's browser.
ADMIN_CLIENT = "__admin__"
# Pending proposals one member may hold at once, and one app (or the browser) may hold for that
# member; another is refused until some are applied, rejected or expire. The per-app cap keeps
# one connected app from filling every slot and blocking the member's other apps. Closed ones
# (expired, rejected, failed) beyond the newest MAX_CLOSED_PROPOSALS per member are deleted, so
# proposals cannot fill the disk.
MAX_PENDING_PROPOSALS = 100
MAX_SELECTION = 100  # changes applied with one press (batch.py): every waiting one a member may have
MAX_CLOSED_PROPOSALS = 100
# Failed Archidekt sign-ins (wrong username or password) one member may make from the account
# page in LINK_FAILURE_WINDOW seconds; further attempts are refused until the oldest ages out,
# so the page cannot be used to guess Archidekt passwords from the gateway's address.
MAX_LINK_FAILURES = 5
LINK_FAILURE_WINDOW = 15 * 60
# OAuth scopes. "mtg" (the default every client gets) allows everything below. A token issued
# only for a read-only scope ("mtg.read", or a bare "read") may read decks, proposals, snapshots
# and reports but not propose, apply, reject, run reports or save scans.
READ_ONLY_SCOPES = frozenset({"mtg.read", "read"})
FULL_SCOPE = "mtg"
# Archidekt work one member may have running or queued in the shared pacer at once (a deck
# read, a proposal, an apply), so one account cannot queue the pacer up for every other member.
# More waits outside the pacer for one of the member's own slots, for up to ARCHIDEKT_SLOT_WAIT
# seconds, the member's own pages first; past that wait, or with MAX_ARCHIDEKT_WAITING_PER_USER
# already waiting, it is refused (rate_limited).
MAX_ARCHIDEKT_PER_USER = 3
ARCHIDEKT_SLOT_WAIT = 20.0
MAX_ARCHIDEKT_WAITING_PER_USER = 8
# The member whose Archidekt slot the running task already holds, so nested calls (an apply's
# reads and writes, get_any_deck's private retry) do not take a second one.
_slot_holder: ContextVar[str | None] = ContextVar("_slot_holder", default=None)
# The member whose Archidekt session the current ``_call_unslotted`` runs with: how a write
# the client is about to send is tied back to the member whose caches it makes stale.
_acting_sub: ContextVar[str | None] = ContextVar("_acting_sub", default=None)
# Set by the deck page's one-click save (api.edit_deck) around its propose and apply (0.7.46 audit
# D-01): the proposal's own read of the deck is kept here and the apply works on it when it is
# under ONE_CLICK_REUSE seconds old, instead of reading the deck again (the read right before the
# write, _rows_unchanged, is still made); the apply's verify read is kept here too, and the page
# draws from it. A +1 then costs a read, the check before the write, the write and the verify.
one_click: ContextVar[dict[str, Any] | None] = ContextVar("one_click", default=None)
ONE_CLICK_REUSE = 2.0
# A member's deck list is kept in memory for this long after Archidekt answered (fresh), and
# served for up to DECK_LIST_STALE seconds more while one refresh runs in the background.
DECK_LIST_FRESH = 90.0
DECK_LIST_STALE = 24 * 3600.0
# A list with entries Archidekt left unreadable is kept only this long, and never served
# stale: My decks and Home say so with Try again, which reads it again at once.
DECK_LIST_PARTIAL = 60.0
# How long a page waits for a cold deck list before it renders with a placeholder the browser
# fills from /api/decks/mine (the fetch carries on and lands in the cache either way).
DECK_LIST_COLD_WAIT = 1.5
# A deck page shows the deck as read for its last view for this long (DeckService.page_deck):
# long enough for a run of view switches and a Back, short enough that a change made on
# archidekt.com shows on the next view soon after. archidekt_cache_seconds 0 turns it off.
PAGE_DECK_SECONDS = 20.0
# After that, and up to this long after the read, a view shows the deck as last read at once and
# reads it again in the background, so a page change never waits for Archidekt for a deck the
# member saw recently; the next view shows what that read found. A write to the deck drops it.
PAGE_DECK_STALE = 6 * 3600.0
# The browser's Reload (and the app's pull to refresh) reads the deck again only when the copy on
# show is older than this (owner decision, 0.7.46): a Reload right after a read shows it at once.
PAGE_DECK_RELOAD = 30.0
# When Archidekt can't be asked (busy, paused, unreachable), the deck page shows the member's last
# read of the deck, read-only and dated, for up to this long after that read (0.7.46).
PAGE_DECK_LAST = 24 * 3600.0
_DECK_WRITE_PATH = re.compile(r"^/decks/(\d+)/")
# While Archidekt cannot be asked (a 429 pause, the breaker, the member's Archidekt work still
# running), My decks and Home show the member's deck list as last read, for up to this long, with a
# notice saying how old it is, instead of the busy page.
DECK_LIST_LAST = 24 * 3600.0
PAGE_DECKS_MAX = 200  # reads kept at most, the oldest go first
TOUCH_LINK_INTERVAL = 60.0  # the link's last_used_at is written at most this often per member
# Said first in a pending proposal's next_step once the deck sync (sync.py) has seen its deck change.
# The reason beside a closed proposal's stored risk tier (the live one may no longer agree).
STORED_RISK_REASON = "judged when it closed"
OUT_OF_DATE_STEP = (
    "Needs a look: the deck changed after this proposal was made and it would now do something "
    "other than what was reviewed, so applying it will be refused and nothing will be sent. Amend "
    "it to fit the deck as it is now, or reject it. "
)


# A selection with a change and the change that replaces it.
PICK_ONE = (
    "{new} replaces {old} on {deck}, so they can't be applied together. Nothing was sent. "
    "Uncheck one of them, then apply again."
)


def _made_by_another_app(row: dict[str, Any], client: str | None) -> bool:
    """Whether a note was made or changed by an app other than ``client``."""
    apps = {row.get("created_by_client"), row.get("amended_by_client")} - {None, ""}
    return any(a != client for a in apps)


# Undo refused: the deck changed after its preview was shown.
UNDO_CHANGED = (
    "The deck changed after you opened this Undo, so nothing was sent. Open the change again and "
    "press Undo to see what it would put back now."
)


def undo_seen(deck: Deck) -> str:
    """What an Undo preview was drawn against: the deck's cards and details, without its updated
    time, which a backup copy or another action may bump without changing what Undo puts back."""
    blob = json.dumps(
        {"rows": deck.rows_fingerprint(), "details": snapshot_details(deck)}, sort_keys=True, default=str
    )
    return hashlib.sha256(blob.encode()).hexdigest()


# Said first in a held proposal's next_step.
HELD_STEP = (
    "Held: a proposal this one depends on was closed without being applied (status_reason says how). "
    "An assistant never applies it in any mode; the user may still apply it themselves, or it can be "
    "amended. "
)
# the card's press reached a change that was held after the card showed it
NOW_HELD = (
    "This change is now held, so nothing was sent to Archidekt. {reason} The user can read why on "
    "the card and still apply it there."
)
# The same, for a proposal whose replaced proposal was applied on its own.
HELD_REPLACED_STEP = (
    "Held: the proposal this one would replace was applied on its own (status_reason says which), "
    "so applying this one too would change the deck twice. An assistant never applies it in any "
    "mode; the user may still apply it themselves, or it can be amended or withdrawn. "
)
# Archidekt work one member may start per ARCHIDEKT_BUDGET_WINDOW seconds (each deck read,
# proposal, apply or proxied archidekt_* research call counts once), on top of the concurrency
# cap above: a looping assistant cannot keep a steady stream of requests going on the member's
# Archidekt account and the shared pacer. MTG_ARCHIDEKT_CALLS_PER_10_MIN sets it.
ARCHIDEKT_BUDGET_WINDOW = 600


def is_busy(exc: BaseException) -> bool:
    """Whether ``exc`` says Archidekt cannot be asked right now (asked us to slow down, the
    member's Archidekt work is still running, the budget is used up, or Archidekt can't be
    reached), as opposed to something about the deck or the account."""
    return isinstance(exc, DeckError) and exc.kind in ("rate_limited", "unavailable")


def scopes_allow_writes(scopes: list[str] | tuple[str, ...] | None) -> bool:
    """False only for a token that has scopes and every one of them is read-only (see
    READ_ONLY_SCOPES). A token with no scope, the default "mtg" scope, or any scope that is not a
    read-only one ("read write", "openid read", "claudeai"...) keeps full access, as before scopes
    were checked, so existing connections are unaffected."""
    have = set(scopes or ())
    return not have or not have <= READ_ONLY_SCOPES


class RateBudget:
    """A token bucket per key: ``per_window`` calls, refilled evenly over ``window`` seconds."""

    def __init__(self, per_window: int, window: float = ARCHIDEKT_BUDGET_WINDOW):
        self.per_window = max(1, int(per_window))
        self.window = float(window)
        self._buckets: dict[str, tuple[float, float]] = {}

    def take(self, key: str, *, keep: int = 0) -> bool:
        """Spend one call for ``key``; False (and nothing spent) when its bucket is empty, or when
        spending would leave fewer than ``keep`` calls in it."""
        now = time.monotonic()
        tokens, at = self._buckets.get(key, (float(self.per_window), now))
        tokens = min(float(self.per_window), tokens + (now - at) * self.per_window / self.window)
        if tokens < 1 + keep:
            self._buckets[key] = (tokens, now)
            return False
        self._buckets[key] = (tokens - 1, now)
        if len(self._buckets) > 10_000:  # forget full buckets, which hold nothing worth keeping
            self._buckets = {
                k: v
                for k, v in self._buckets.items()
                if v[0] + (now - v[1]) * self.per_window / self.window < self.per_window
            }
        return True

    def room(self, key: str) -> float:
        """The calls ``key`` could spend now, spending none (a collection import keeps some back)."""
        tokens, at = self._buckets.get(key, (float(self.per_window), time.monotonic()))
        return min(float(self.per_window), tokens + (time.monotonic() - at) * self.per_window / self.window)

    def retry_after(self, key: str) -> int:
        """Whole seconds until ``key`` may spend a call again (at least 1): what a refusal's
        Retry-After header says."""
        tokens, at = self._buckets.get(key, (float(self.per_window), time.monotonic()))
        tokens += (time.monotonic() - at) * self.per_window / self.window
        if tokens >= 1:
            return 1
        return max(1, math.ceil((1 - tokens) * self.window / self.per_window - 1e-6))


class MemberCache:
    """One value per member with stale-while-revalidate: a fresh value is served as is, a stale
    one (older than ``fresh`` seconds, younger than ``stale``) is served at once while one refresh
    runs in the background, and anything older (or missing) is fetched, every waiter sharing the
    one fetch in flight. ``drop`` forgets a member's value and makes a fetch that was already in
    flight store nothing, so a write racing a read never leaves the old list behind."""

    def __init__(self, fresh: float, stale: float, *, keep_for: Callable[[Any], float | None] | None = None):
        self.fresh = fresh
        self.stale = stale
        # ``keep_for(value)``: a shorter life for some values, in seconds, never served stale
        self.keep_for = keep_for
        self._entries: dict[str, tuple[float, float, Any]] = {}  # sub -> (monotonic, wall, value)
        self._limits: dict[str, float] = {}  # sub -> that shorter life, for the value kept now
        self._refreshing: dict[str, tuple[asyncio.Task[Any], float]] = {}  # task, when it started
        self._dropped: dict[str, float] = {}
        # sub -> (wall time, value): the last value read in full, kept through ``drop`` (a write
        # makes it out of date, not wrong to show as last read); ``forget`` removes it
        self._last: dict[str, tuple[float, Any]] = {}
        # the deck sync's fetches (``refreshed``): read on the sync's terms, no retry and no unlink,
        # so a page that shared one which failed reads again on its own terms
        self._background: weakref.WeakSet[asyncio.Task[Any]] = weakref.WeakSet()

    def peek(self, sub: str) -> tuple[Any, str]:
        """(value, state) where state is fresh, stale or miss (value None)."""
        hit = self._entries.get(sub)
        if hit is None:
            return None, "miss"
        age = time.monotonic() - hit[0]
        limit = self._limits.get(sub)
        if limit is not None:  # a value kept briefly: fresh, then gone (never served stale)
            if age < limit:
                return hit[2], "fresh"
            self._entries.pop(sub, None)
            self._limits.pop(sub, None)
            return None, "miss"
        if age < self.fresh:
            return hit[2], "fresh"
        if age < self.stale:
            return hit[2], "stale"
        self._entries.pop(sub, None)
        return None, "miss"

    def fetched_at(self, sub: str) -> float | None:
        hit = self._entries.get(sub)
        return hit[1] if hit else None

    def drop(self, sub: str) -> None:
        self._entries.pop(sub, None)
        self._limits.pop(sub, None)
        self._dropped[sub] = time.monotonic()

    def forget(self, sub: str) -> None:
        """``drop``, and the last value too (the member's link changed or their data was deleted)."""
        self.drop(sub)
        self._last.pop(sub, None)

    def last(self, sub: str, max_age: float) -> tuple[float, Any] | None:
        """(when read, epoch seconds; value) of the last value read in full, if younger than
        ``max_age``; for a page to show as last read while a fetch cannot be made."""
        hit = self._last.get(sub)
        if hit is None or time.time() - hit[0] >= max_age:
            return None
        return hit

    def clear(self) -> None:
        for sub in list(self._entries):
            self.drop(sub)

    async def refreshed(self, sub: str, fetch: Callable[[], Awaitable[Any]], *, wait: float) -> Any | None:
        """A value fetched now (sharing a fetch already in flight), for the deck sync: unlike
        ``get`` it never answers a failed fetch with an older value, so the error reaches the
        caller. None when the fetch takes longer than ``wait`` (it carries on and fills the cache)."""
        task = self._refresh(sub, fetch, background=True)
        done, _pending = await asyncio.wait({task}, timeout=wait)
        return task.result() if done else None

    def refreshing(self, sub: str) -> bool:
        return sub in self._refreshing

    def _refresh(
        self, sub: str, fetch: Callable[[], Awaitable[Any]], *, background: bool = False
    ) -> asyncio.Task[Any]:
        running = self._refreshing.get(sub)
        started = time.monotonic()
        # A fetch that started before the last drop is not shared with a reader arriving after it:
        # it may carry the pre-write list. That fetch still finishes (and stores nothing).
        if running is not None and not running[0].done() and self._dropped.get(sub, 0.0) <= running[1]:
            return running[0]

        for_page = page_turn.get() and not background

        async def run() -> Any:
            page_turn.set(for_page)  # a page's read keeps its turn (the context below is fresh)
            value = await fetch()
            if self._dropped.get(sub, 0.0) <= started:  # not invalidated while it was fetched
                self._entries[sub] = (time.monotonic(), time.time(), value)
                limit = self.keep_for(value) if self.keep_for else None
                if limit is None:
                    self._limits.pop(sub, None)
                    self._last[sub] = (time.time(), value)
                else:
                    self._limits[sub] = limit
            return value

        def done(t: asyncio.Task[Any]) -> None:
            if self._refreshing.get(sub, (None,))[0] is t:  # only its own entry, never a newer fetch
                self._refreshing.pop(sub, None)
            if not t.cancelled() and t.exception() is not None:
                logger.debug("member cache refresh failed for a member: %r", t.exception())

        # A fresh context: the refresh is nobody's request, so it must not inherit the caller's
        # Archidekt slot (_slot_holder), client id or request timing; only a page's turn is kept.
        task = asyncio.create_task(run(), context=contextvars.Context())
        task.add_done_callback(done)
        if background:
            self._background.add(task)
        self._refreshing[sub] = (task, started)
        if len(self._entries) > 10_000:
            self._entries.clear()
            self._limits.clear()
            self._dropped.clear()
            self._last.clear()
        return task

    async def get(
        self,
        sub: str,
        fetch: Callable[[], Awaitable[Any]],
        *,
        wait: float | None = None,
        prefer_fresh: bool = False,
    ) -> Any | None:
        """The member's value: fresh at once; stale at once with a refresh in the background;
        otherwise fetched. With ``wait``, a fetch that takes longer answers None instead and
        carries on, so the caller can render a placeholder and come back for the value.
        ``prefer_fresh`` (with ``wait``) waits that long for the refresh of a stale value before
        falling back to it: the same one request, for a page that judges what is still there."""
        value, state = self.peek(sub)
        if state == "fresh":
            return value
        if state == "stale":
            task = self._refresh(sub, fetch)
            if prefer_fresh and wait:
                started = time.perf_counter()
                done, _pending = await asyncio.wait({task}, timeout=wait)
                add_time(archidekt_time, time.perf_counter() - started)
                if done and not task.cancelled() and task.exception() is None:
                    return task.result()
            return value
        task = self._refresh(sub, fetch)
        # The fetch runs in its own task (its own context), so the time this request spends
        # waiting for it is what counts as its Archidekt time (timing.py).
        started = time.perf_counter()
        try:
            for attempt in (1, 2):
                try:
                    if wait is None:
                        return await asyncio.shield(task)
                    done, _pending = await asyncio.wait({task}, timeout=wait)
                    if not done:
                        return None
                    return task.result()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # a page that joined the deck sync's read, which failed: one read of its own,
                    # with the page's retry and error handling, before the error is shown
                    if attempt == 2 or task not in self._background:
                        raise
                    task = self._refresh(sub, fetch)
            return None  # not reached
        finally:
            add_time(archidekt_time, time.perf_counter() - started)

    async def aclose(self) -> None:
        tasks = [task for task, _started in self._refreshing.values()]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


# -- review rows ----------------------------------------------------------------
# Every proposal stores its change as structured rows (kind, name, before, after...) next to the
# plain-text diff. The review page renders the rows and never re-parses the text, so a card name,
# category or deck name cannot make one row look like another. The text lines come from the rows.
FULL_TEXT_FIELDS = ("before_text", "after_text", "after_doc")  # after_doc: the Quill document


def commander_stays_text(stay: dict[str, Any]) -> str:
    """The commander row's text when the commanders stay and a commander's other rows go."""
    qty = int(stay.get("qty") or 0)  # at least 1: a card whose copies all go doesn't stay
    copies = "1 copy" if qty == 1 else f"{qty} copies"
    if stay.get("zone") == "side":  # rows outside the deck proper fold among themselves
        return (
            f"{stay.get('name')} stays in the commander zone, now with {copies}. "
            "Its other rows in the maybeboard or sideboard are removed."
        )
    return (
        f"{stay.get('name')} stays in the commander zone, now with {copies}. "
        "Its other rows in the deck are removed."
    )


def commander_moved_text(moved: dict[str, Any]) -> str:
    """The commander row's text for a row in, or moved into or out of, the commander zone whose
    categories change while the commanders stay."""
    if moved.get("added"):  # plan_zones refuses the adds that get here; kept so one would wait
        return (
            f"This add asks for a commander-zone category ({moved.get('after')}), so it counts as a "
            "commander change."
        )
    return (
        f"{moved.get('name')}'s categories change from {moved.get('before')} to {moved.get('after')}. "
        "One of them is the commander zone, so this counts as a commander change."
    )


def commander_row_texts(r: dict[str, Any]) -> list[str]:
    """The texts of a commander row that keeps the commanders (``stays``, ``moved``)."""
    stays = r.get("stays") if isinstance(r.get("stays"), list) else []
    moved = r.get("moved") if isinstance(r.get("moved"), list) else []
    return [commander_stays_text(x) for x in stays] + [commander_moved_text(x) for x in moved]


def _row(kind: str, **fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str) and key not in FULL_TEXT_FIELDS:
            value = clean_text(value)
        out[key] = value
    return out


def _commander_names(*lists: list[str]) -> list[str] | None:
    """The cards a commander row names, before and after. Its before and after are joined with
    ", ", which many card names contain, so the names are kept apart for the board's card list."""
    return sorted({n for names in lists for n in names}, key=str.lower) or None


def _split_names(text: str, known: set[str]) -> list[str] | None:
    """``text`` (names joined with ", ") split back into the ``known`` names it joins, taking the
    longest known name at each step, since a name may itself hold ", "; None when some part is
    no known name."""
    parts = text.split(", ")
    out: list[str] = []
    i = 0
    while i < len(parts):
        j = next((j for j in range(len(parts), i, -1) if ", ".join(parts[i:j]) in known), None)
        if j is None:
            return None
        out.append(", ".join(parts[i:j]))
        i = j
    return out


def fill_commander_names(
    rows: list[dict[str, Any]] | None, deck: Deck, changes: Any
) -> list[dict[str, Any]] | None:
    """The rows with ``names`` filled in on a commander row saved before rows listed them (0.7.31),
    from the deck's card names and the names the stored changes give, as its before and after
    join them; None when there is nothing to fill or a name can't be told apart."""
    if not any(r.get("kind") == "commander" and not r.get("names") for r in rows or []):
        return None
    known = {c.name for c in deck.cards} | {
        str(ch["card_name"]) for ch in changes or [] if isinstance(ch, dict) and ch.get("card_name")
    }
    out = []
    for r in rows or []:
        if r.get("kind") == "commander" and not r.get("names"):
            sides = [str(r.get(k) or "") for k in ("before", "after")]
            split = [[] if s in ("", "(none)") else _split_names(s, known) for s in sides]
            if any(s is None for s in split):
                return None
            r = {**r, "names": _commander_names(*split)}  # type: ignore[arg-type]
        out.append(r)
    return out


def row_cards(rows: list[dict[str, Any]] | None) -> list[str]:
    """Every card the rows name, a commander row's too, sorted."""
    return sorted({str(n) for r in rows or [] for n in [r.get("name"), *(r.get("names") or [])] if n})


def _printing_label(item: dict[str, Any]) -> str | None:
    """'(CMR 472, Foil)' for a collection item that names a printing or finish, else None."""
    card = item.get("card") or {}
    code = str(item.get("set") or card.get("set") or "").upper()
    number = str(item.get("collector_number") or card.get("collector_number") or "")
    finish = str(item.get("finish") or ("foil" if item.get("foil") is True else "") or "")
    bits = [
        b
        for b in (f"{code} {number}".strip(), finish.capitalize() if finish and finish != "nonfoil" else "")
        if b
    ]
    return f"({', '.join(bits)})" if bits else None


def row_label(r: dict[str, Any]) -> str:
    """'Sol Ring (CMR 1, Foil) [Ramp]': a row's card, printing and category, and a warning when
    a scan guessed the name from what it misread."""
    label = str(r.get("name", ""))
    if r.get("printing"):
        label += f" {r['printing']}"
    if r.get("category"):
        label += f" [{r['category']}]"
    if r.get("guessed_from"):
        label += f" (name guessed from '{r['guessed_from']}'; check it)"
    if r.get("tag"):  # the colour tag an add puts on its new row
        label += f", color tag {clean_text(str(r['tag']))}"
    if r.get("breaks"):  # a format rule the change would break (DeckService._judge_rules)
        label += " (breaks the deck's rules: " + "; ".join(clean_text(str(b)) for b in r["breaks"]) + ")"
    return label


DESCRIPTION_EXCERPT = 300  # characters of a new deck description shown on its diff line


def tag_change_text(r: dict[str, Any]) -> str:
    """'adds ramp, landfall; removes budget' for a tags row that names them; '' otherwise."""
    if r.get("field") != "tags":
        return ""
    bits = []
    if r.get("added"):
        bits.append("adds " + ", ".join(clean_text(str(n)) for n in r["added"]))
    if r.get("removed"):
        bits.append("removes " + ", ".join(clean_text(str(n)) for n in r["removed"]))
    return "; ".join(bits)


def row_line(r: dict[str, Any]) -> str:
    """The plain-text diff line for one review row."""
    kind = r.get("kind")
    label = row_label(r)
    side = " (maybeboard/sideboard)" if r.get("zone") == "side" else ""
    if kind == "add":
        return f"+{r['qty']} {label}{side}"
    if kind in ("remove", "change") and r.get("companion_card"):
        return row_line({**r, "companion_card": None}) + " (the deck's companion)"
    if kind == "remove":
        return f"-{r['qty']} {label}{side}"
    if kind == "change":
        if r.get("fold"):  # a commander's rows fold into its commander-zone row
            return f"{r['before']} -> {r['after']} {label}{side}. {commander_stays_text(r['fold'])}"
        return f"{r['before']} -> {r['after']} {label}{side}"
    if kind == "deck_category":  # the deck's own category: renamed or its flags changed
        return category_line(r)
    if kind == "custom_card":  # one of the member's custom cards in the deck
        return custom_card_line(r)
    if kind == "category":
        line = f"{r['name']}: category {r['before']} -> {r['after']}"
        if r.get("leaves"):  # moved into a category the deck does not count (Maybeboard...)
            line += f" (leaves the deck, -{r['leaves']})"
        if r.get("enters"):  # a maybeboard or sideboard row moved into the deck proper
            line += f" (enters the deck, +{r['enters']})"
        return line
    if kind == "finish":
        return f"{r['name']}: finish {r['before']} -> {r['after']}"
    if kind == "label":
        return f"{r['name']}: color tag {r['before']} -> {r['after']}{side}"
    if kind == "mana_value":
        return f"{r['name']}: custom mana value {r['before']} -> {r['after']}{side}"
    if kind == "companion":
        return f"{r['name']}: companion {r['before']} -> {r['after']}{side}"
    if kind == "back_face":
        return f"{r['name']}: show back face {r['before']} -> {r['after']}{side}"
    if kind == "printing":
        return f"{r['name']}: printing {r['before']} -> {r['after']}"
    if kind == "clone":
        where = f" in folder “{r['folder']}”" if r.get("folder") else ""
        n = int(r.get("cards") or 0)
        return f"Clone “{r['source']}” ({n} {'card' if n == 1 else 'cards'}) as “{r['name']}”{where}"
    if kind == "commander":
        if r.get("stays") or r.get("moved"):
            return " ".join(commander_row_texts(r))
        return f"Commander: {r['before']} -> {r['after']}"
    if kind == "new_deck":
        visibility = "private" if r.get("private") else "public"
        side = f", {r['side_cards']} more in the maybeboard or sideboard" if r.get("side_cards") else ""
        more = (", theorycrafted" if r.get("theorycrafted") else "") + (
            f", {r['game']}" if r.get("game") else ""
        )
        where = f" in folder “{r['folder']}”" if r.get("folder") else ""
        if r.get("package"):
            what = f"{r['cards']} cards{side}, {visibility}; {r['package']}"
            return f"New card package '{r['name']}'{where} ({what})"
        return f"New {r['format']} deck '{r['name']}'{where} ({r['cards']} cards{side}, {visibility}{more})"
    if kind == "restore":
        if "changes" in r:  # the review lines under it (a restore saved before counted write entries)
            n = int(r["changes"])
            count = f"{n} change{'' if n == 1 else 's'}"
            return f"Restore to snapshot {r['snapshot_id']} taken {r['taken']} ({count})"
        return f"Restore to snapshot {r['snapshot_id']} taken {r['taken']} ({r['rows']} rows)"
    if kind == "description":
        after_len = int(r.get("after_len") or 0)
        if not after_len:
            return "description: (cleared)"
        # The new text on the one diff line, so the user approves words they can read (in full on
        # the review page). clean_text keeps it to one line: it can never pose as another row.
        text = clean_text(r.get("after_text") or "")
        shown = text if len(text) <= DESCRIPTION_EXCERPT else text[: DESCRIPTION_EXCERPT - 1] + "…"
        return (
            f'description: (changed, {after_len} chars) "{shown}"'
            if shown
            else (f"description: (changed, {after_len} chars)")
        )
    if kind == "detail":
        line = f"{r['field']}: {r['before']} -> {r['after']}"
        said = tag_change_text(r)
        return f"{line} ({said})" if said else line
    return str(r.get("text", ""))


def actor_label(client_id: str | None, name: str | None = None) -> str:
    """Who acted, for activity lists. The member's own browser is told apart by its client id,
    never by a name: an app may register as "browser", and then shows as "app: browser (id)"."""
    if not client_id:
        return ""
    if client_id == BROWSER_CLIENT:
        return "browser"
    if client_id == ADMIN_CLIENT:
        return "administrator"
    short = clean_text(client_id)
    short = short if len(short) <= 40 else short[:37] + "..."
    shown = clean_text(name or "")[:60]
    return f"app: {shown} ({short})" if shown else f"app: {short}"


# The ``via`` of DeckService.apply that only the review page's own form (pages.py) passes: the
# one caller that may apply an Archidekt account action.
REVIEW_PAGE = "review_page"
# Who rejected a proposal (DeckService.reject records it; describe() shows it as closed_by).
REJECTED_BY = ("assistant", "card", "review_page", "browser", "api", "disconnected")
# The reason the member typed when rejecting on the card, kept with the rejection (at most this long)
REJECT_REASON_MAX = 500
OLDER_CARDS = 50  # older cards of a proposal whose code is answered "use the newest card" (not "invalid")
# What an amend of a waiting new deck may change (DeckService.amend_new_deck), as propose_new_deck
# takes it (folder_id: the folder, looked up by name in the tool)
NEW_DECK_FIELDS = (
    "name",
    "deck_format",
    "cards",
    "decklist_text",
    "csv_text",
    "json_text",
    "private",
    "theorycrafted",
    "game",
    "package",
    "folder_id",
)


def archidekt_wait(exc: BaseException) -> int | None:
    """How long the gateway is pausing its Archidekt requests after an ArchidektError (Archidekt's
    Retry-After, capped as the pacer caps it, or what is left of that pause) in whole seconds, at
    least 1, for the busy page's Retry-After header. A Retry-After of 0 (or a date already past)
    pauses nothing, so it is 1, never the busy page's default."""
    asked = getattr(exc, "retry_after", None)
    if asked is None:
        return None
    return max(1, math.ceil(min(asked, Pacer.MAX_RETRY_AFTER)))


# Before a proposal that overlaps another waiting change (board.overlaps) is applied.
OVERLAP_STEP = (
    "Other waiting changes on this deck touch the same cards or categories (overlaps). Before "
    "anything is applied, combine them (combine_proposals), amend one (amend_proposal) or withdraw "
    "one (withdraw_proposal), so one does not undo or break the other. Then:"
)


def _new_deck_fields(kind: str, result: Any) -> dict[str, Any]:
    """``new_deck_id`` and ``new_deck_url`` of a clone that made its copy."""
    new = result.get("deck_id") if kind == "clone" and isinstance(result, dict) else None
    if new is None or not str(new).isdigit():
        return {}
    return {"new_deck_id": str(new), "new_deck_url": f"https://archidekt.com/decks/{new}"}


class DeckError(Exception):
    """A user-facing failure: the message is safe to show as is. ``extra`` holds structured
    fields the tool reply carries alongside ``error`` and ``message``. ``retry_after`` (seconds)
    is set when the refusal knows how long to wait (a used-up Archidekt budget); the browser
    pages put it in their Retry-After header. ``sent`` is False when the Archidekt request never
    left the gateway (the pacer refused it), so nothing can have been written whatever the kind.
    ``busy_cause`` names why a rate_limited refusal was made, for the busy page (busy.py):
    "member_slots" (the member's own Archidekt work fills their slots) or "budget" (the member's
    MTG_ARCHIDEKT_CALLS_PER_10_MIN is used up); None otherwise."""

    busy_cause: str | None = None

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        retry_after: int | None = None,
        sent: bool = True,
        http_status: int | None = None,
        **extra: Any,
    ):
        super().__init__(message)
        self.kind = kind
        self.extra = extra
        self.retry_after = retry_after
        self.sent = sent
        # ArchidektError.status, for a contract error from Archidekt (not ``status``: a waiting
        # proposal's status, "needs_look" and the rest, travels in ``extra`` under that name)
        self.http_status = http_status


@dataclass(frozen=True)
class Change:
    action: str
    card_name: str
    quantity: int | None = None
    category: str | None = None
    # An add may name the exact printing (set code + collector number, as a scan or the user
    # picked it) and the finish ("Normal", "Foil" or "Etched"). A named printing is pinned: it
    # must exist on Archidekt and be this card, or the proposal is refused when applied.
    set_code: str | None = None
    collector_number: str | None = None
    finish: str | None = None
    # set_label: the colour tag as Archidekt stores it ("Have,#37d67a"); "" takes the tag off
    label: str | None = None
    # set_mana_value: the custom mana value (0 to 15); None takes the override off
    mana_value: int | None = None
    # set_companion and set_back_face: the switch on (True) or off (False)
    on: bool | None = None
    # "main" (the deck proper) or "side": the maybeboard and sideboard rows, which count
    # separately. A count or category change names the zone its rows are in.
    zone: str = "main"
    # add and set_category: every category the row goes in, in order (the first is ``category``,
    # the row's primary one). The deck page's Undo of a removal or a move sends it, so a card
    # filed under several categories comes back under all of them.
    categories: tuple[str, ...] = ()
    # add: the row's companion and "flipped by default" flags. The deck page's Undo of a removal
    # sends them, so a companion or a card set to show its back comes back as it was.
    companion: bool = False
    flipped_default: bool = False
    # ROW_TARGET_ACTIONS: the one row this changes (``target_rows`` sets the card and zone from it)
    relation_id: int | None = None

    @property
    def pinned(self) -> bool:
        return bool(self.set_code and self.collector_number)

    def printing_label(self) -> str:
        """'(M21 267, Foil)' or '' - the part of a diff line that says which printing."""
        bits = []
        if self.pinned:
            bits.append(f"{(self.set_code or '').upper()} {self.collector_number}")
        if self.finish and self.finish != "Normal":
            bits.append(self.finish)
        return f" ({', '.join(bits)})" if bits else ""

    def as_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"action": self.action, "card_name": self.card_name}
        if self.quantity is not None:
            d["quantity"] = self.quantity
        if self.category:
            d["category"] = self.category
        if self.categories:
            d["categories"] = list(self.categories)
        if self.set_code:
            d["set_code"] = self.set_code
        if self.collector_number:
            d["collector_number"] = self.collector_number
        if self.finish:
            d["finish"] = self.finish
        if self.action == "set_label" or (self.action == "add" and self.label):
            name, colour = split_label(self.label or "")
            d["label"] = name
            if colour:
                d["color"] = colour
        if self.action == "set_mana_value":
            d["mana_value"] = self.mana_value
        if self.companion:
            d["companion"] = True
        if self.flipped_default:
            d["flipped_default"] = True
        if self.action in SWITCHES:
            d[SWITCHES[self.action][0]] = bool(self.on)
        if self.zone == "side":
            d["zone"] = "side"
        if self.relation_id is not None:
            d["relation_id"] = self.relation_id
        return d


def split_label(label: str) -> tuple[str, str]:
    """('Have', '#37d67a') from Archidekt's 'Have,#37d67a'; a colour that is not a plain hex
    colour is left out, so it can never reach a style attribute."""
    name, _, colour = (label or "").rpartition(",") if "," in (label or "") else (label or "", "", "")
    colour = colour.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{3}", colour):  # #fff is #ffffff
        colour = "#" + "".join(c * 2 for c in colour[1:])
    return name.strip(), colour.lower() if LABEL_COLOUR.fullmatch(colour) else ""


def label_text(label: str) -> str:
    """How a review row names a colour tag: 'Have (#37d67a)', or 'no tag'."""
    name, colour = split_label(label)
    if not name:
        return "no tag"
    return f"{name} ({colour})" if colour else name


ZONES = ("main", "side")
_SET_CODE = re.compile(r"[A-Za-z0-9]{2,6}")
_COLLECTOR_NUMBER = re.compile(r"[A-Za-z0-9★†\-]{1,10}")
FINISHES = {"normal": "Normal", "foil": "Foil", "etched": "Etched"}


def parse_changes(raw: Any, *, row_flags: bool = False) -> list[Change]:
    """The changes of a proposal, checked. ``row_flags``: an add may carry ``companion`` and
    ``flipped_default`` (the deck page's Undo of a removal, and its stored proposal when applied).
    Any other caller's flags are dropped, never written: an assistant's proposal reads "+1 Opt" in
    its review and on the chat card, so it must write no more than that."""
    if not isinstance(raw, list) or not raw:
        raise DeckError("invalid", "changes must be a non-empty list")
    if len(raw) > MAX_CHANGES:
        raise DeckError("invalid", f"at most {MAX_CHANGES} changes per proposal")
    out: list[Change] = []
    rels: list[int | None] = []  # each change's row (relation_id), in order
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise DeckError("invalid", f"change {i} must be an object")
        action = str(item.get("action", "")).strip().lower()
        if action not in ACTIONS:
            raise DeckError("invalid", f"change {i}: action must be one of {', '.join(ACTIONS)}")
        name = clean_text(item.get("card_name", ""))
        if not name or len(name) > 200:
            raise DeckError("invalid", f"change {i}: card_name is required")
        zone = str(item.get("zone") or "main").strip().lower()
        if zone not in ZONES:
            raise DeckError("invalid", f"change {i}: zone must be main or side")
        rel = item.get("relation_id")
        if rel is not None:
            if action not in ROW_TARGET_ACTIONS:
                raise DeckError(
                    "invalid", f"change {i}: relation_id goes with {', '.join(ROW_TARGET_ACTIONS)} only"
                )
            if isinstance(rel, str) and rel.isdigit():
                rel = int(rel)
            if isinstance(rel, bool) or not isinstance(rel, int) or rel <= 0:
                raise DeckError(
                    "invalid", f"change {i}: relation_id must be the number of one of the card's rows"
                )
        rels.append(rel)
        if action in CATEGORY_ACTIONS:
            ch = _parse_category_change(i, action, name, item)
            if zone == "side" and action == "set_commander":
                raise DeckError(
                    "invalid", f"change {i}: set_commander works on the deck proper, not zone side"
                )
            out.append(dataclasses.replace(ch, zone=zone))
            continue
        if action in PRINTING_ACTIONS:
            # a printing or finish change in zone side names its row, or the card has one row
            # there (printing_plan_rows refuses it otherwise)
            if action == "set_label":
                out.append(dataclasses.replace(_parse_label_change(i, name, item), zone=zone))
                continue
            if action == "set_mana_value":
                out.append(dataclasses.replace(_parse_mana_value_change(i, name, item), zone=zone))
                continue
            if action in SWITCHES:
                out.append(dataclasses.replace(_parse_switch_change(i, action, name, item), zone=zone))
                continue
            out.append(dataclasses.replace(_parse_printing_change(i, action, name, item), zone=zone))
            continue
        qty = item.get("quantity")
        if action == "remove":
            qty = None if qty is None else qty
        if action in ("add", "set_quantity") and qty is None:
            qty = 1 if action == "add" else None
        if qty is not None:
            if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0 or qty > 99:
                raise DeckError("invalid", f"change {i}: quantity must be an integer from 0 to 99")
        if action == "set_quantity" and qty is None:
            raise DeckError("invalid", f"change {i}: set_quantity needs a quantity")
        cat = clean_text(item.get("category") or "") or None
        if cat and len(cat) > MAX_CATEGORY:
            raise DeckError("invalid", f"change {i}: category is longer than {MAX_CATEGORY} characters")
        cats = _parse_add_categories(i, action, item.get("categories"))
        if cats:
            if cat and cat != cats[0]:
                raise DeckError("invalid", f"change {i}: category must be the first of categories")
            cat = cats[0]
        set_code = str(item.get("set_code") or item.get("set") or "").strip().lower() or None
        number = str(item.get("collector_number") or "").strip() or None
        finish_raw = item.get("finish") or item.get("modifier")
        if finish_raw is None and item.get("foil") is True:
            finish_raw = "foil"
        finish = FINISHES.get(str(finish_raw).strip().lower()) if finish_raw else None
        if finish_raw and finish is None:
            raise DeckError("invalid", f"change {i}: finish must be normal, foil or etched")
        if (set_code or number or finish) and action != "add":
            raise DeckError("invalid", f"change {i}: set_code, collector_number and finish apply to add only")
        if bool(set_code) != bool(number):
            raise DeckError("invalid", f"change {i}: set_code and collector_number go together")
        if set_code and not _SET_CODE.fullmatch(set_code):
            raise DeckError("invalid", f"change {i}: set_code must be a set code such as cmr")
        if number and not _COLLECTOR_NUMBER.fullmatch(number):
            raise DeckError("invalid", f"change {i}: collector_number looks wrong")
        flags = {}
        for flag in ("companion", "flipped_default") if row_flags else ():
            given = item.get(flag)
            if given is None:
                continue
            if not isinstance(given, bool):
                raise DeckError("invalid", f"change {i}: {flag} must be true or false")
            if given and action != "add":
                raise DeckError("invalid", f"change {i}: {flag} applies to add only")
            flags[flag] = given
        label = None
        if item.get("label") not in (None, ""):
            if action != "add":
                raise DeckError("invalid", f"change {i}: label goes with add or set_label only")
            label = _parse_label_change(i, name, {"label": item["label"], "color": item.get("color")}).label
        out.append(
            Change(
                action,
                name,
                qty,
                cat,
                set_code,
                number,
                finish,
                zone=zone,
                categories=cats,
                label=label,
                **flags,
            )
        )
    out = [dataclasses.replace(ch, relation_id=rel) if rel else ch for ch, rel in zip(out, rels, strict=True)]
    # Per zone: the deck proper and the maybeboard or sideboard each take one pinned printing of a card.
    pinned = [(ch.zone, ch.card_name.lower()) for ch in out if ch.pinned]
    if len(pinned) != len(set(pinned)):
        raise DeckError(
            "invalid",
            "one pinned printing per card name in each zone (deck or maybeboard/sideboard) per proposal",
        )
    # A pinned add next to a remove or set_quantity of the same card in the same zone would make
    # the writes (add that printing, then shrink the rest) differ from the net count the diff shows.
    pinned_faces = {(zone, front_face(n)) for zone, n in pinned}
    for ch in out:
        if (
            ch.action in ("remove", "set_quantity")
            and (
                ch.zone,
                front_face(ch.card_name.lower()),
            )
            in pinned_faces
        ):
            raise DeckError(
                "invalid",
                f"'{ch.card_name}' has a pinned printing added and is also removed or set in the same "
                "zone in the same proposal; propose the removal and the pinned add separately",
            )

    # Names compare by front face, as the plans match them, so "Fire" and "Fire // Ice" clash.
    # The zone is part of the key: a maybeboard row and a deck row of the same card are two rows.
    def key(ch: Change) -> str:
        # a change that names its row is about that row only
        row = f" #{ch.relation_id}" if ch.relation_id else ""
        return front_face(ch.card_name).lower() + ("" if ch.zone == "main" else " (side)") + row

    categorised = [key(ch) for ch in out if ch.action in CATEGORY_ACTIONS]
    if len(categorised) != len(set(categorised)):
        raise DeckError("invalid", "one set_category or set_commander per card name per proposal")
    reprinted = [key(ch) for ch in out if ch.action in PRINTING_ACTIONS]
    if len(reprinted) != len(set(reprinted)):
        raise DeckError(
            "invalid",
            "one set_finish, set_printing, set_label, set_mana_value, set_companion or set_back_face per "
            "card name per proposal",
        )
    counted = {key(ch) for ch in out if ch.action in COUNT_ACTIONS}
    clash = sorted((set(categorised) | set(reprinted)) & counted)
    if clash:
        raise DeckError(
            "invalid",
            "a card cannot be added, removed or recounted and recategorized or reprinted in the same "
            "proposal: " + ", ".join(clash),
        )
    clash = sorted(set(categorised) & set(reprinted))
    if clash:
        raise DeckError(
            "invalid",
            "a card cannot be recategorized and reprinted in the same proposal: " + ", ".join(clash),
        )
    return out


MAX_ADD_CATEGORIES = 10


def _all_categories(chs: list[Change]) -> list[str]:
    """The categories a new row made by these adds of one card goes in: the first add's full
    ``categories`` when one names them, else the first ``category`` given (or none)."""
    named = next((c.categories for c in chs if c.categories), ())
    if named:
        return list(named)
    cat = next((c.category for c in chs if c.category), None)
    return [cat] if cat else []


def _parse_add_categories(i: int, action: str, raw: Any) -> tuple[str, ...]:
    """An add's or set_category's ``categories``: the row's categories in order (each one line,
    as ``category``), duplicates dropped. Only those two take them."""
    if raw is None or raw == []:
        return ()
    if action not in ("add", "set_category"):
        raise DeckError("invalid", f"change {i}: categories apply to add and set_category only")
    if not isinstance(raw, list) or len(raw) > MAX_ADD_CATEGORIES or not all(isinstance(c, str) for c in raw):
        raise DeckError(
            "invalid", f"change {i}: categories must be a list of up to {MAX_ADD_CATEGORIES} names"
        )
    out: list[str] = []
    for c in raw:
        cat = clean_text(c)
        if not cat or len(cat) > MAX_CATEGORY:
            raise DeckError("invalid", f"change {i}: each category is 1 to {MAX_CATEGORY} characters")
        if cat not in out:
            out.append(cat)
    return tuple(out)


def _parse_label_change(i: int, name: str, item: dict[str, Any]) -> Change:
    """``set_label`` ({action, card_name, label, color?, zone?}) puts a colour tag on every row of
    the card in that zone, as Archidekt's own editor does: a name of up to 40 characters and a
    colour as #rrggbb (grey when none is given). An empty label takes the tag off."""
    for key in ("quantity", "category", "set_code", "set", "collector_number", "finish", "modifier", "foil"):
        if item.get(key) not in (None, "", False):
            raise DeckError("invalid", f"change {i}: set_label takes only card_name, label, color and zone")
    raw = item.get("label")
    if raw is None:
        raise DeckError("invalid", f"change {i}: set_label needs a label (an empty one takes the tag off)")
    text = clean_text(str(raw))
    colour = str(item.get("color") or item.get("colour") or "").strip()
    if "," in text and not colour:  # Archidekt's own form, "Have,#37d67a"
        text, colour = text.rsplit(",", 1)[0].strip(), text.rsplit(",", 1)[1].strip()
    if "," in text or len(text) > MAX_LABEL:
        raise DeckError("invalid", f"change {i}: a label is at most {MAX_LABEL} characters, without commas")
    if not text:
        return Change("set_label", name, label="")
    if colour and not LABEL_COLOUR.fullmatch(colour):
        raise DeckError("invalid", f"change {i}: color must be a color like #37d67a")
    return Change("set_label", name, label=f"{text},{(colour or DEFAULT_LABEL_COLOUR).lower()}")


DEFAULT_LABEL_COLOUR = "#656565"
# The tag archidekt.com sends for a row with none: no name, the default grey (its site code).
NO_LABEL = "," + DEFAULT_LABEL_COLOUR


def _parse_mana_value_change(i: int, name: str, item: dict[str, Any]) -> Change:
    """``set_mana_value`` ({action, card_name, mana_value, zone?}) sets Archidekt's custom mana
    value on every row of the card in that zone, a whole number from 0 to 15 as Archidekt's own
    editor takes it; ``mana_value`` null takes the override off, so the card's own counts again."""
    for key in ("quantity", "category", "set_code", "set", "collector_number", "finish", "label", "color"):
        if item.get(key) not in (None, "", False):
            raise DeckError(
                "invalid", f"change {i}: set_mana_value takes only card_name, mana_value and zone"
            )
    if "mana_value" not in item:
        raise DeckError(
            "invalid", f"change {i}: set_mana_value needs mana_value (null takes the override off)"
        )
    raw = item.get("mana_value")
    if raw is None or raw == "":
        return Change("set_mana_value", name, mana_value=None)
    if isinstance(raw, bool) or not isinstance(raw, (int, float, str)):
        raise DeckError(
            "invalid", f"change {i}: mana_value must be a whole number from 0 to {MAX_MANA_VALUE}"
        )
    try:
        value = float(str(raw).strip())
    except ValueError:
        value = -1.0
    if not value.is_integer() or not 0 <= value <= MAX_MANA_VALUE:
        raise DeckError(
            "invalid", f"change {i}: mana_value must be a whole number from 0 to {MAX_MANA_VALUE}"
        )
    return Change("set_mana_value", name, mana_value=int(value))


def _parse_switch_change(i: int, action: str, name: str, item: dict[str, Any]) -> Change:
    """``set_companion`` ({action, card_name, companion: true or false, zone?}) and
    ``set_back_face`` ({action, card_name, back_face: true or false, zone?}) switch Archidekt's
    Companion and "show back face by default" on every row of the card in that zone."""
    field = SWITCHES[action][0]
    extra = {k for k, v in item.items() if v not in (None, "", False)} - {
        "action",
        "card_name",
        "zone",
        field,
    }
    if extra:
        raise DeckError("invalid", f"change {i}: {action} takes only card_name, {field} and zone")
    if not isinstance(item.get(field), bool):
        raise DeckError("invalid", f"change {i}: {action} needs {field}: true or false")
    return Change(action, name, on=item[field])


def mana_value_text(value: float | None) -> str:
    """'3' for a custom mana value, 'none' when the row has no override."""
    return "none" if value is None else str(int(value)) if float(value).is_integer() else str(value)


def _parse_printing_change(i: int, action: str, name: str, item: dict[str, Any]) -> Change:
    """``set_finish`` ({action, card_name, finish}) changes the finish of every copy of a card
    already in the deck; ``set_printing`` ({action, card_name, set_code, collector_number,
    finish?}) swaps every copy for that printing (same total, same categories)."""
    for key in ("quantity", "category"):
        if item.get(key) not in (None, "", False):
            raise DeckError("invalid", f"change {i}: {action} takes no {key}")
    finish_raw = item.get("finish") or item.get("modifier")
    if finish_raw is None and item.get("foil") is True:
        finish_raw = "foil"
    finish = FINISHES.get(str(finish_raw).strip().lower()) if finish_raw else None
    if finish_raw and finish is None:
        raise DeckError("invalid", f"change {i}: finish must be normal, foil or etched")
    if action == "set_finish":
        if not finish:
            raise DeckError("invalid", f"change {i}: set_finish needs a finish (normal, foil or etched)")
        return Change(action, name, finish=finish)
    set_code = str(item.get("set_code") or item.get("set") or "").strip().lower() or None
    number = str(item.get("collector_number") or "").strip() or None
    if not (set_code and number):
        raise DeckError("invalid", f"change {i}: set_printing needs set_code and collector_number")
    if not _SET_CODE.fullmatch(set_code):
        raise DeckError("invalid", f"change {i}: set_code must be a set code such as cmr")
    if not _COLLECTOR_NUMBER.fullmatch(number):
        raise DeckError("invalid", f"change {i}: collector_number looks wrong")
    return Change(action, name, set_code=set_code, collector_number=number, finish=finish)


def target_rows(deck: Deck, changes: list[Change]) -> list[Change]:
    """The changes, each one that names a row (its relation id) set to that row's card and zone:
    the row says where it is, whatever zone the change was sent with. A row that is no longer in
    the deck (or holds another card) is refused."""
    if not any(ch.relation_id for ch in changes):
        return changes
    rows = {c.relation_id: c for c in deck.cards if c.relation_id is not None}
    out = []
    for ch in changes:
        if ch.relation_id:
            row = rows.get(ch.relation_id)
            if row is None or front_face(row.name).casefold() != front_face(ch.card_name).casefold():
                raise DeckError(
                    "invalid",
                    f"That row of '{ch.card_name}' is no longer in the deck. Reload the deck and try again.",
                )
            ch = dataclasses.replace(ch, card_name=row.name, zone="main" if deck.in_deck(row) else "side")
        out.append(ch)
    return out


def row_counts(deck: Deck, changes: list[Change]) -> dict[int, int]:
    """The count each row named by a remove or set_quantity (``target_rows`` first) ends at:
    ``{relation id: copies}``, 0 for a row that goes."""
    rows = {c.relation_id: c for c in deck.cards if c.relation_id is not None}
    out: dict[int, int] = {}
    for ch in changes:
        if ch.action not in ("remove", "set_quantity") or not ch.relation_id:
            continue
        cur = out.get(ch.relation_id, rows[ch.relation_id].quantity)
        if ch.action == "remove":
            out[ch.relation_id] = max(0, cur - ch.quantity) if ch.quantity else 0
        else:
            out[ch.relation_id] = ch.quantity or 0
    return out


def _primary(row: Any) -> str | None:
    """A row's own category (its first), as a review row names it."""
    return clean_text((row.categories or [""])[0] or "") or None


def printing_plan_rows(
    deck: Deck, changes: list[Change]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """What the set_finish and set_printing changes do to rows of the deck, as specs
    ``{name, relation_ids, quantity, categories, finish?, set_code?, collector_number?}`` plus
    their review rows. A card that is not in the deck is an error; a change that leaves the row
    as it is produces nothing."""
    changes = target_rows(deck, changes)
    specs: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    by_name: dict[str, list[Any]] = {}
    side_by_name: dict[str, list[Any]] = {}
    for c in deck.main_cards:
        by_name.setdefault(c.name.lower(), []).append(c)
        by_name.setdefault(front_face(c.name).lower(), []).append(c)
    for c in deck.side_cards:
        side_by_name.setdefault(c.name.lower(), []).append(c)
        side_by_name.setdefault(front_face(c.name).lower(), []).append(c)
    for ch in changes:
        if ch.action not in PRINTING_ACTIONS:
            continue
        pool = side_by_name if ch.zone == "side" else by_name
        cards = list({c.relation_id: c for c in pool.get(ch.card_name.lower(), [])}.values())
        if ch.relation_id:  # that row only
            cards = [c for c in cards if c.relation_id == ch.relation_id]
        if not cards:
            raise DeckError(
                "invalid",
                f"'{ch.card_name}' is not in the deck"
                + (" (maybeboard or sideboard)" if ch.zone == "side" else "")
                + (
                    ", so it cannot be tagged"
                    if ch.action == "set_label"
                    else ", so its mana value cannot be set"
                    if ch.action == "set_mana_value"
                    else f", so its {SWITCHES[ch.action][3]} cannot be switched"
                    if ch.action in SWITCHES
                    else ", so its printing cannot be changed"
                ),
            )
        if ch.zone == "side" and ch.action not in ROW_TAG_ACTIONS and len(cards) > 1:
            # the maybeboard, the sideboard and any other category left out of the deck are one
            # zone: a printing or finish for all of them at once is never what was meant
            raise DeckError(
                "invalid",
                f"'{ch.card_name}' has more than one maybeboard or sideboard row; "
                "name the row to change (relation_id)",
            )
        if any(c.relation_id is None or c.card_id is None for c in cards):
            raise DeckError("contract", f"Archidekt did not number the deck rows of '{ch.card_name}'.")
        first = cards[0]
        # One entry per existing row, so a card filed in two categories, a companion or a row
        # with a label keeps each of those on the new printing.
        spec: dict[str, Any] = {
            "name": first.name,
            "relation_ids": [c.relation_id for c in cards],
            "quantity": sum(c.quantity for c in cards),
            "categories": list(first.categories),
            "rows": [
                {
                    "relation_id": c.relation_id,
                    "quantity": c.quantity,
                    "categories": list(c.categories),
                    "finish": ch.finish or (c.modifier or "Normal"),
                    "companion": c.companion,
                    "flipped_default": c.flipped_default,
                    "label": c.label,
                    "custom_cmc": c.custom_cmc,
                }
                for c in cards
            ],
        }
        finishes = sorted({r["finish"] for r in spec["rows"]})
        if ch.action in SWITCHES:
            _field, key, _ark, words = SWITCHES[ch.action]
            if ch.action == "set_back_face" and "//" not in first.name:
                raise DeckError("invalid", f"'{first.name}' has no back face to show.")
            if all(bool(getattr(c, key)) == ch.on for c in cards):
                continue
            spec[key] = bool(ch.on)
            spec["zone"] = ch.zone
            rows.append(
                _row(
                    "companion" if ch.action == "set_companion" else "back_face",
                    name=first.name,
                    before=" / ".join(sorted({"on" if getattr(c, key) else "off" for c in cards})),
                    after="on" if ch.on else "off",
                    zone="side" if ch.zone == "side" else None,
                )
            )
            specs.append(spec)
            continue
        if ch.action == "set_mana_value":
            if all(_same_mana_value(c.custom_cmc, ch.mana_value) for c in cards):
                continue
            before = sorted({mana_value_text(c.custom_cmc) for c in cards})
            spec["mana_value"] = ch.mana_value
            spec["zone"] = ch.zone
            rows.append(
                _row(
                    "mana_value",
                    name=first.name,
                    before=" / ".join(before),
                    after=mana_value_text(ch.mana_value),
                    zone="side" if ch.zone == "side" else None,
                )
            )
            specs.append(spec)
            continue
        if ch.action == "set_label":
            want = ch.label or ""
            if all(_same_label(c.label, want) for c in cards):
                continue
            before = sorted({label_text(c.label) for c in cards})
            spec["label"] = want
            spec["zone"] = ch.zone
            rows.append(
                _row(
                    "label",
                    name=first.name,
                    before=" / ".join(before),
                    after=label_text(want),
                    colour=split_label(want)[1] or None,
                    zone="side" if ch.zone == "side" else None,
                )
            )
        elif ch.action == "set_finish":
            before = sorted({c.modifier or "Normal" for c in cards})
            if before == [ch.finish]:
                continue
            spec["finish"] = ch.finish
            spec["zone"] = ch.zone
            rows.append(
                _row(
                    "finish",
                    name=first.name,
                    before=" / ".join(before),
                    after=ch.finish,
                    category=_primary(first) if ch.relation_id or ch.zone == "side" else None,
                    zone="side" if ch.zone == "side" else None,
                )
            )
        else:
            before = sorted({f"{c.set_code.upper()} {c.collector_number}" for c in cards})
            after = f"{(ch.set_code or '').upper()} {ch.collector_number}"
            if finishes != ["Normal"]:
                after += ", " + " / ".join(finishes)
            same_printing = all(
                c.set_code.lower() == ch.set_code and c.collector_number == ch.collector_number for c in cards
            )
            same_finish = ch.finish is None or all((c.modifier or "Normal") == ch.finish for c in cards)
            if same_printing and same_finish:
                continue
            spec.update(
                {
                    "set_code": ch.set_code,
                    "collector_number": ch.collector_number,
                    "finish": ch.finish,
                    "zone": ch.zone,
                }
            )
            rows.append(
                _row(
                    "printing",
                    name=first.name,
                    before=" / ".join(before),
                    after=after,
                    category=_primary(first) if ch.relation_id or ch.zone == "side" else None,
                    zone="side" if ch.zone == "side" else None,
                )
            )
        specs.append(spec)
    return specs, rows


def _same_mana_value(have: float | None, want: int | None) -> bool:
    """A row already carries the custom mana value, or neither side has one."""
    return (have is None and want is None) or (have is not None and want is not None and float(have) == want)


def _same_label(have: str, want: str) -> bool:
    """A row already carries the tag: same name and colour, or no tag on either side."""
    a, b = split_label(have), split_label(want)
    if not a[0] and not b[0]:
        return True
    return a[0] == b[0] and a[1] == b[1]


def _parse_category_change(i: int, action: str, name: str, item: dict[str, Any]) -> Change:
    """``set_category`` ({action, card_name, category, categories?}) and ``set_commander``
    ({action, card_name}); neither takes a quantity or a printing. A set_category's
    ``categories`` files the card under all of them (the deck page's Undo of a move sends it)."""
    for key in ("quantity", "set_code", "set", "collector_number", "finish", "modifier", "foil"):
        if item.get(key) not in (None, "", False):
            raise DeckError(
                "invalid",
                f"change {i}: {action} takes only card_name"
                + (" and category" if action == "set_category" else ""),
            )
    # One line, like every other name and category in a diff (no newlines, bidi or control
    # characters), so a category can never add rows of its own to the review page.
    cat = clean_text(item.get("category") or "")
    cats = _parse_add_categories(i, action, item.get("categories"))  # never with set_commander
    if action == "set_commander":
        if cat and cat != COMMANDER:
            raise DeckError("invalid", f"change {i}: set_commander always files the card under Commander")
        return Change(action, name, category=COMMANDER)
    if cats:
        if cat and cat != cats[0]:
            raise DeckError("invalid", f"change {i}: category must be the first of categories")
        cat = cats[0]
    if not cat or len(cat) > MAX_CATEGORY:
        raise DeckError("invalid", f"change {i}: set_category needs a category (up to {MAX_CATEGORY} chars)")
    return Change(action, name, category=cat, categories=cats)


def category_plan(deck: Deck, changes: list[Change]) -> tuple[dict[int, list[str]], list[str]]:
    """Rows whose categories the set_category and set_commander changes rewrite, as
    ``{relation id: new categories}``, plus their diff lines (see ``category_plan_rows``)."""
    want, rows = category_plan_rows(deck, changes)
    return want, [row_line(r) for r in rows]


def category_plan_rows(
    deck: Deck, changes: list[Change]
) -> tuple[dict[int, list[str]], list[dict[str, Any]]]:
    """Rows whose categories the set_category and set_commander changes rewrite, as
    ``{relation id: new categories}``, plus their review rows.

    ``set_category`` moves every row of the card to exactly that one category (or, with
    ``categories``, exactly those, the first the primary one). ``set_commander``
    files the card under Commander as its primary category, keeping its other categories,
    and takes Commander off every other row that has it (keeping that row's other
    categories), so after the change the commanders are exactly the cards named by this
    proposal's set_commander changes (two for partners). Rows that already have the
    wanted categories are left alone; a card that is not in the deck is an error.
    """
    changes = target_rows(deck, changes)
    cat_changes = [ch for ch in changes if ch.action in CATEGORY_ACTIONS]
    if not cat_changes:
        return {}, []
    # A change names the zone of the rows it moves: "main" rows are the deck proper, "side" rows
    # the maybeboard and sideboard. Moving a side row into a counted category pulls it into the
    # deck (``entering_deck``); moving a deck row into an excluded one takes it out (``leaving_deck``).
    rows_by_zone: dict[str, dict[str, list[Any]]] = {"main": {}, "side": {}}
    for zone in ZONES:
        for c in deck.cards_in(zone):
            rows_by_zone[zone].setdefault(c.name.lower(), []).append(c)
            rows_by_zone[zone].setdefault(front_face(c.name).lower(), []).append(c)
    want: dict[int, list[str]] = {}
    lines: list[dict[str, Any]] = []
    new_commanders: list[str] = []
    for ch in cat_changes:
        rows = rows_by_zone[ch.zone].get(ch.card_name.lower())
        if not rows:
            where = "the deck" if ch.zone == "main" else "the maybeboard or sideboard"
            raise DeckError("invalid", f"'{ch.card_name}' is not in {where}, so its category cannot be set")
        rows = list({r.relation_id: r for r in rows}.values())
        if ch.relation_id:  # that row only
            rows = [r for r in rows if r.relation_id == ch.relation_id]
        if any(r.relation_id is None for r in rows):
            raise DeckError("contract", f"Archidekt did not number the deck rows of '{ch.card_name}'.")
        new = list(ch.categories) or [ch.category or COMMANDER]
        if ch.action == "set_commander":
            new_commanders.append(rows[0].name)
        # A new commander keeps its other categories, Commander first (the primary), as
        # archidekt.com's "Set Commander as primary" does
        per_row = {
            r.relation_id: (
                [COMMANDER] + [c for c in r.categories if c != COMMANDER]
                if ch.action == "set_commander"
                else new
            )
            for r in rows
        }
        # the order counts: the first category is the row's primary one, which decides whether the
        # card counts in the deck (and a new commander's Commander must come first, also when the
        # card already has it as a later category, ["Ramp", "Commander"])
        changed = [r for r in rows if list(r.categories) != per_row[r.relation_id]]
        for r in changed:
            want[r.relation_id] = per_row[r.relation_id]
        # a card that is a commander already is not in the commander row below, so its
        # reordering gets a category row of its own
        reordered = [r for r in changed if ch.action == "set_commander" and COMMANDER in r.categories]
        if reordered:
            lines.append(
                _row(
                    "category",
                    name=rows[0].name,
                    before=" / ".join(
                        sorted({", ".join(clean_text(c) for c in r.categories) for r in reordered})
                    ),
                    after=" / ".join(
                        sorted({", ".join(clean_text(c) for c in per_row[r.relation_id]) for r in reordered})
                    ),
                )
            )
        if ch.action == "set_category" and changed:
            old = sorted({", ".join(clean_text(c) for c in r.categories) or "(none)" for r in rows})
            lines.append(
                _row(
                    "category",
                    name=rows[0].name,
                    before=" / ".join(old),
                    after=", ".join(new),
                    zone="side" if ch.zone == "side" else None,
                )
            )
    if new_commanders:
        wanted_lower = {n.lower() for n in new_commanders}
        old_commanders: list[str] = []
        for c in deck.main_cards:
            if COMMANDER not in c.categories:
                continue
            old_commanders.append(c.name)
            if c.name.lower() in wanted_lower or c.relation_id in want:
                continue
            if c.relation_id is None:
                raise DeckError("contract", f"Archidekt did not number the deck rows of '{c.name}'.")
            rest = [cat for cat in c.categories if cat != COMMANDER]
            if not rest and c.default_category and c.default_category != COMMANDER:
                # archidekt.com never sends an empty list: it falls back to the card's default
                # category; without one the row is left with none, as before
                rest = [c.default_category]
            want[c.relation_id] = rest
        old_names = sorted(set(old_commanders), key=str.lower)
        new_names = sorted(set(new_commanders), key=str.lower)
        if [n.lower() for n in old_names] != [n.lower() for n in new_names]:
            lines.append(
                _row(
                    "commander",
                    before=", ".join(old_names) or "(none)",
                    after=", ".join(new_names),
                    names=_commander_names(old_names, new_names),
                )
            )
    return want, lines


def leaving_deck(deck: Deck, recategorise: dict[int, list[str]]) -> dict[str, int]:
    """Copies per card name that ``recategorise`` (from ``category_plan``) moves out of the deck
    proper: rows counted in the deck now whose new categories are all ones the deck excludes."""
    out: dict[str, int] = {}
    for c in deck.main_cards:
        cats = recategorise.get(c.relation_id) if c.relation_id is not None else None
        if cats and not deck.categories_count(cats):
            out[c.name] = out.get(c.name, 0) + c.quantity
    return out


def entering_deck(deck: Deck, recategorise: dict[int, list[str]]) -> dict[str, int]:
    """The mirror of ``leaving_deck``: copies per card name that ``recategorise`` moves from the
    maybeboard or sideboard into the deck proper (a side row given a category the deck counts)."""
    out: dict[str, int] = {}
    for c in deck.side_cards:
        cats = recategorise.get(c.relation_id) if c.relation_id is not None else None
        if cats and deck.categories_count(cats):
            out[c.name] = out.get(c.name, 0) + c.quantity
    return out


def is_outside_category(deck: Deck, category: str) -> bool:
    """Whether a row whose first category is this one sits outside the deck: one the deck does
    not count (``Deck.categories_count``), or Maybeboard or Sideboard by name in any case (the
    add refusal's test, ``_refuse_adds_written_otherwise``)."""
    return not deck.categories_count([category]) or category.casefold() in ("maybeboard", "sideboard")


def _side_categories(deck: Deck, chs: list[Change]) -> list[str]:
    """The categories a new maybeboard or sideboard row made by these adds of one card is written
    with, and reviewed as: ``side_category_for`` first, then the adds' other categories."""
    side_cat = side_category_for(deck, chs[0])
    return [side_cat] + [c for c in _all_categories(chs)[1:] if c != side_cat]


def side_category_for(deck: Deck, ch: Change) -> str:
    """The category a maybeboard add goes in: the change's own category when a row under it sits
    outside the deck, else the deck's maybeboard category."""
    if ch.category and is_outside_category(deck, ch.category):
        return ch.category
    return deck.side_category()


def plan(deck: Deck, changes: list[Change]) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """Return (before counts, after counts, diff lines) for the deck plus changes. The lines
    include those of ``category_plan`` for set_category and set_commander changes; the after
    counts are those of the count changes only (``leaving_deck`` has what a recategorisation
    moves out of the deck proper), which is what ``build_payload`` needs."""
    before, after, rows = plan_rows(deck, changes)
    return before, after, [row_line(r) for r in rows]


def plan_rows(
    deck: Deck, changes: list[Change]
) -> tuple[dict[str, int], dict[str, int], list[dict[str, Any]]]:
    """``plan`` with structured review rows in place of the text lines."""
    before, after, rows, _before_side, _after_side = plan_zones(deck, changes)
    return before, after, rows


async def unknown_card_names(
    deck: Deck,
    changes: list[Change],
    known: Callable[[list[str]], Awaitable[dict[str, bool | None]]] | None,
) -> list[str]:
    """The names added by ``changes`` that are no Magic card at all (``known``: the scan service's
    are_cards). Archidekt looks each name up only when the proposal is applied, so without this a
    misspelt card waits as a proposal and fails later. A card already in the deck is never
    looked up, and a name that cannot be judged (no catalog yet, Scryfall unreachable, or the check
    fails) passes."""
    if known is None:
        return []
    have = {c.name.casefold() for c in deck.cards} | {front_face(c.name).casefold() for c in deck.cards}
    names: list[str] = []
    for ch in changes:
        name = ch.card_name
        if ch.action != "add" or name.casefold() in have or front_face(name).casefold() in have:
            continue
        if name not in names:
            names.append(name)
    if not names:
        return []
    try:
        found = await known(names)
    except Exception:
        logger.exception("card-name check failed")
        return []
    return [n for n in names if found.get(n) is False]


# Scryfall's colour letters as Archidekt's oracle cards spell them (``colorIdentity``), so an added
# card is judged against its commander in one spelling.
_COLOUR_WORDS = {"W": "White", "U": "Blue", "B": "Black", "R": "Red", "G": "Green"}
_COLOUR_LETTER = {
    **{w.casefold(): k for k, w in _COLOUR_WORDS.items()},
    **{k.casefold(): k for k in _COLOUR_WORDS},
}


def _with_card_facts(card: DeckCard, facts: dict[str, Any] | None, words: bool) -> DeckCard:
    """An added card's row (no Archidekt card data yet) with what its legality turns on, from
    Scryfall's card: colour identity, types, legalities and rules text. As it is without them."""
    if not isinstance(facts, dict):
        return card
    ident = [str(x) for x in facts.get("color_identity") or [] if isinstance(x, str)]
    if words:
        ident = [_COLOUR_WORDS.get(x.upper(), x) for x in ident]
    type_line = str(facts.get("type_line") or "").split("//", 1)[0]
    head, _, tail = type_line.partition("—")
    words_ = head.split()
    supers = [w for w in words_ if w in ("Basic", "Legendary", "Snow", "World")]
    legal = facts.get("legalities") if isinstance(facts.get("legalities"), dict) else {}
    return dataclasses.replace(
        card,
        color_identity=ident,
        types=[w for w in words_ if w not in supers],
        supertypes=supers,
        subtypes=tail.split(),
        legalities={str(k): str(v) for k, v in legal.items() if isinstance(v, str)},
        oracle_text=str(facts.get("oracle_text") or ""),
    )


def _new_problems(before: dict[str, Any], after: dict[str, Any]) -> list[dict[str, Any]]:
    """The format rules a change breaks that the deck kept before (deck_stats.deck_checks on the
    deck now and as it would be): ``{"problem", "cards"}`` each, the cards it concerns (none for
    the deck's size). A rule the deck already broke is not counted again."""
    out: list[dict[str, Any]] = []

    def names(checks: dict[str, Any], key: str) -> list[str]:
        return [str(r["name"]) for r in checks.get(key) or [] if isinstance(r, dict) and r.get("name")]

    size_b, size_a = before.get("deck_size") or {}, after.get("deck_size") or {}
    if size_b.get("ok", True) and not size_a.get("ok", True):
        want = size_a.get("expected") or size_a.get("minimum")
        how = "" if size_a.get("expected") else "at least "
        problem = f"the deck would have {size_a.get('actual')} cards, not {how}{want}"
        out.append({"problem": problem, "cards": []})
    had = set(names(before, "colour_identity_violations"))
    outside = [
        r
        for r in after.get("colour_identity_violations") or []
        if isinstance(r, dict) and r.get("name") not in had
    ]
    if outside:
        out.append(
            {
                "problem": "outside the commander's color identity: "
                + ", ".join(f"{r['name']} ({', '.join(_letters(r.get('outside')))})" for r in outside),
                "cards": [str(r["name"]) for r in outside],
            }
        )
    had = set(names(before, "singleton_violations"))
    extra = [n for n in names(after, "singleton_violations") if n not in had]
    if extra:
        problem = "more than one copy in a singleton format: " + ", ".join(extra)
        out.append({"problem": problem, "cards": extra})
    for key, words in (("banned", "banned"), ("not_legal", "not legal in this format")):
        had = set((before.get("legality") or {}).get(key) or [])
        new = [str(n) for n in (after.get("legality") or {}).get(key) or [] if n not in had]
        if new:
            out.append({"problem": f"{words}: " + ", ".join(new), "cards": new})
    return out


def _letters(colours: Any) -> list[str]:
    """Colours as WUBRG letters ("Red" -> "R"), in that order."""
    have = {_COLOUR_LETTER.get(str(c).casefold(), str(c)) for c in colours or []}
    return [c for c in "WUBRG" if c in have] + sorted(have - set("WUBRG"))


def mark_breaks(rows: list[dict[str, Any]], problems: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The review rows with ``breaks`` on each row a new problem concerns (a problem of the deck
    as a whole, its size, on the first row that adds copies), so the review, the diff and the
    risk all say it."""
    out = [dict(r) for r in rows]

    def adds(r: dict[str, Any]) -> bool:
        if r.get("kind") == "add":
            return True
        try:
            return r.get("kind") == "change" and int(r.get("after") or 0) > int(r.get("before") or 0)
        except (TypeError, ValueError):
            return False

    for p in problems:
        wanted = {str(n).casefold() for n in p["cards"]}
        hit = [
            r
            for r in out
            if wanted
            and r.get("zone") != "side"
            and (
                str(r.get("name") or "").casefold() in wanted
                or front_face(str(r.get("name") or "")).casefold() in {front_face(n) for n in wanted}
            )
        ]
        if not hit:
            hit = [r for r in out if adds(r) and r.get("zone") != "side"][:1]
        for r in hit:
            r["breaks"] = [*(r.get("breaks") or []), p["problem"]]
    return out


def unchanged_changes(changes: list[Change], rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The finish, printing, tag, mana value and switch changes that would leave their card as it
    is (printing_plan_rows gives them no row), so the result says so instead of dropping them."""
    named = {str(r.get("name") or "").casefold() for r in rows} | {
        front_face(str(r.get("name") or "")).casefold() for r in rows
    }
    out: list[dict[str, str]] = []
    for ch in changes:
        if ch.action not in PRINTING_ACTIONS:
            continue
        if ch.card_name.casefold() in named or front_face(ch.card_name).casefold() in named:
            continue
        out.append({"action": ch.action, "name": ch.card_name})
    return out


def _refuse_adds_written_otherwise(deck: Deck, changes: list[Change], main_lookup: dict[str, str]) -> None:
    """Refuse an add whose written row would not be what its review row says (the safe default:
    nothing is proposed, and the message says what to do instead):

    * an add to the maybeboard or sideboard that asks for the commander zone: such a row is
      written under the deck's maybeboard category (``side_category_for``), without it;
    * an add to the deck proper whose first category is outside the deck (Maybeboard,
      Sideboard or another category the deck does not count): the copies would be planned in
      the deck and read back outside it, so the apply would fail its check;
    * an add that asks for the commander zone for a card already in the deck with no row
      there: the add raises the count of the card's row (``build_payload``), whose categories
      stay as they are."""
    premier = deck.premier_keys()
    main_rows: dict[str, list[Any]] = {}
    for c in deck.main_cards:
        main_rows.setdefault(c.name.lower(), []).append(c)
    for ch in changes:
        if ch.action != "add":
            continue
        cats = _all_categories([ch])
        if ch.zone == "side":
            # a row whose first category is outside the deck is written with every category as
            # asked (side_category_for keeps that first one), the commander zone included: the
            # Undo of removing such a row puts it back as it was
            if _in_premier(premier, cats) and not is_outside_category(deck, cats[0]):
                raise DeckError(
                    "invalid",
                    f"'{ch.card_name}': a card added to the maybeboard or sideboard cannot go in the "
                    f"commander zone in the same step ({', '.join(cats)}). Add it without the commander "
                    "zone; you can then change its categories.",
                )
            continue
        primary = cats[0] if cats else ""
        if cats and is_outside_category(deck, primary):
            raise DeckError(
                "invalid",
                f"'{ch.card_name}': its first category, {primary}, is outside the deck, so the "
                "copies would not count in it. Add it to the maybeboard instead; you can then change "
                "its categories.",
            )
        name = main_lookup.get(ch.card_name.lower()) or main_lookup.get(front_face(ch.card_name).lower())
        rows = main_rows.get(name.lower(), []) if name else []
        if rows and _in_premier(premier, cats) and not any(_in_premier(premier, r.categories) for r in rows):
            raise DeckError(
                "invalid",
                f"'{ch.card_name}' is already in the deck, so adding it raises the count of its row "
                "there and cannot put it in the commander zone. Set its category to move it.",
            )


def plan_zones(
    deck: Deck, changes: list[Change]
) -> tuple[dict[str, int], dict[str, int], list[dict[str, Any]], dict[str, int], dict[str, int]]:
    """(before, after, rows, before_side, after_side): the deck proper's counts before and after the
    count changes of zone main, the review rows of every change, and the same counts for the
    maybeboard and sideboard rows (zone side). Category moves between the zones are not in the
    counts (``leaving_deck`` and ``entering_deck`` have them); the rows say what they do."""
    changes = target_rows(deck, changes)
    before = deck.counts_by_name()
    before_side = deck.side_counts_by_name()
    lookups: dict[str, dict[str, str]] = {}
    for zone, counts in (("main", before), ("side", before_side)):
        lookup = {n.lower(): n for n in counts}
        for n in counts:  # a double-faced card is also found by its front face
            lookup.setdefault(front_face(n).lower(), n)
        lookups[zone] = lookup
    _refuse_adds_written_otherwise(deck, changes, lookups["main"])
    after = dict(before)
    after_side = dict(before_side)
    cats_want, cat_lines = category_plan_rows(deck, changes)
    # A set_category into a category the deck does not count (Maybeboard, Sideboard...) takes
    # those copies out of the deck proper: the review says so, and counts them as removed. The
    # other way round, a side row given a counted category enters the deck.
    leaving = leaving_deck(deck, cats_want)
    entering = entering_deck(deck, cats_want)
    for r in cat_lines:
        if r.get("kind") != "category":
            continue
        if r.get("name") in leaving and r.get("zone") != "side":
            r["leaves"] = leaving.pop(r["name"])
        if r.get("name") in entering and r.get("zone") == "side":
            r["enters"] = entering.pop(r["name"])
    cat_lines += [_row("remove", name=name, qty=qty) for name, qty in leaving.items()]
    cat_lines += [_row("add", name=name, qty=qty) for name, qty in entering.items()]
    _specs, print_lines = printing_plan_rows(deck, changes)
    cat_lines = cat_lines + print_lines
    # A remove or set_quantity that names its row changes that row's count only: the zone's total
    # moves by as much, and the review has a row of its own for it, under the row's category.
    by_rel = {c.relation_id: c for c in deck.cards if c.relation_id is not None}
    counted = row_counts(deck, changes)
    row_lines: list[dict[str, Any]] = []
    by_row: set[tuple[str, str]] = set()
    for rid, n in counted.items():
        r = by_rel[rid]
        zone = "main" if deck.in_deck(r) else "side"
        key = lookups[zone].get(r.name.lower(), r.name)
        target = after_side if zone == "side" else after
        target[key] = target.get(key, 0) - r.quantity + n
        by_row.add((zone, key.lower()))
        side = "side" if zone == "side" else None
        if n == 0:
            row_lines.append(_row("remove", name=r.name, qty=r.quantity, category=_primary(r), zone=side))
        elif n != r.quantity:
            row_lines.append(
                _row("change", name=r.name, before=r.quantity, after=n, category=_primary(r), zone=side)
            )
    for ch in changes:
        if ch.action in CATEGORY_ACTIONS or ch.action in PRINTING_ACTIONS or ch.relation_id:
            continue
        target = after_side if ch.zone == "side" else after
        lookup = lookups[ch.zone]
        key = lookup.get(ch.card_name.lower(), ch.card_name)
        if (ch.zone, key.lower()) in by_row:
            raise DeckError(
                "invalid",
                f"'{ch.card_name}' has a change for one of its rows and another for the card as a whole; "
                "propose them separately",
            )
        cur = target.get(key, 0)
        if ch.action == "add":
            target[key] = cur + (ch.quantity or 1)
        elif ch.action == "remove":
            if cur == 0:
                where = "the deck" if ch.zone == "main" else "the maybeboard or sideboard"
                had = (before_side if ch.zone == "side" else before).get(key, 0)
                if had:  # an earlier change in this list already took every copy
                    copies = f"{had} {'copy' if had == 1 else 'copies'}"
                    raise DeckError(
                        "invalid",
                        f"'{ch.card_name}' is removed more than once in these changes, and {where} has "
                        f"only {copies}. Remove it once.",
                    )
                raise DeckError("invalid", f"'{ch.card_name}' is not in {where}, so it cannot be removed")
            target[key] = max(0, cur - ch.quantity) if ch.quantity else 0
        else:
            target[key] = ch.quantity or 0
        lookup.setdefault(key.lower(), key)
    lines: list[dict[str, Any]] = []
    for zone, b_counts, a_counts in (("main", before, after), ("side", before_side, after_side)):
        adds_by_name: dict[str, list[Change]] = {}
        for ch in changes:
            if ch.action == "add" and ch.zone == zone:
                adds_by_name.setdefault(ch.card_name.lower(), []).append(ch)
        # the printing shows on the line only when every added copy of that card is that printing
        labels = {n: chs[0].printing_label() for n, chs in adds_by_name.items() if len(chs) == 1}
        # the category an add puts a new row in (Commander, Maybeboard...) is part of what is reviewed
        if zone == "main":
            cats = {n: ", ".join(_all_categories(chs)) or None for n, chs in adds_by_name.items()}
        else:
            cats = {n: ", ".join(_side_categories(deck, chs)) for n, chs in adds_by_name.items()}
        pinned_names = {n for n, chs in adds_by_name.items() if any(c.pinned for c in chs)}
        # the colour tag an add puts on its new row is part of what is reviewed (security review F-05)
        tags = {n: next((c.label for c in chs if c.label), "") for n, chs in adds_by_name.items()}
        side = "side" if zone == "side" else None
        # a maybeboard or sideboard card's own category, when all its rows there share one
        # ("Optional Artwork"), names where a removal or a count change happens
        own: dict[str, set[str]] = {}
        for c in deck.side_cards if side else []:
            own.setdefault(c.name.lower(), set()).add(_primary(c) or "")
        for name in sorted(set(b_counts) | set(a_counts), key=str.lower):
            b, a = b_counts.get(name, 0), a_counts.get(name, 0)
            if a == b or (zone, name.lower()) in by_row:
                continue
            where = own.get(name.lower(), set())
            where_cat = next(iter(where)) if len(where) == 1 else None
            printing = labels.get(name.lower()) or None
            cat = cats.get(name.lower())
            if not (cat and (b == 0 or name.lower() in pinned_names)):  # a new row is made in that category
                cat = None
            if b == 0:
                row = _row("add", name=name, qty=a, printing=printing, category=cat, zone=side)
                if tags.get(name.lower()):
                    row["tag"] = label_text(tags[name.lower()])
                lines.append(row)
            elif a == 0:
                lines.append(_row("remove", name=name, qty=b, category=where_cat, zone=side))
            else:
                lines.append(
                    _row(
                        "change",
                        name=name,
                        before=b,
                        after=a,
                        printing=printing,
                        category=where_cat if not cat else None,
                        zone=side,
                    )
                )
    lines.extend(row_lines)
    lines.extend(cat_lines)
    if not lines:
        raise DeckError("invalid", "these changes would leave the deck exactly as it is")
    # Cutting the companion card's copies changes the deck's companion as surely as
    # set_companion does, so the row says so and waits in every mode (security review 0.7.42).
    companions = {(c.name.lower(), "main" if deck.in_deck(c) else "side") for c in deck.cards if c.companion}
    for r in lines:
        cut = r.get("kind") == "remove" or (
            r.get("kind") == "change" and int(r.get("after") or 0) < int(r.get("before") or 0)
        )
        if cut and (str(r.get("name", "")).lower(), r.get("zone") or "main") in companions:
            r["companion_card"] = True
    # A commander change waits for the member's press in every mode (modes.risk_of, auto_waits).
    # set_commander writes its own row; a card moved into or out of Commander, the commander
    # removed or a card added under Commander changes the commanders as well, so the whole list's
    # commanders before and after are compared and the difference gets a commander row too.
    if not any(r.get("kind") == "commander" for r in lines):
        was, now, unsure, moved, folds = _commanders_before_after(
            deck, changes, cats_want, {"main": after, "side": after_side}
        )
        if [n.lower() for n in was] != [n.lower() for n in now]:
            lines.append(
                _row(
                    "commander",
                    before=", ".join(was) or "(none)",
                    after=", ".join(now) or "(none)",
                    names=_commander_names(was, now),
                )
            )
        elif unsure or moved:
            # the commanders stay: what changes is the card's other rows (see build_payload), or
            # the categories of a row in the commander zone, which counts as a commander change
            lines.append(
                _row(
                    "commander",
                    before=", ".join(was),
                    after=", ".join(now),
                    names=_commander_names(was, now),
                    stays=unsure or None,
                    moved=moved or None,
                )
            )
        # A raised commander with other rows in the same zone folds them into its commander-zone
        # row as well; that is no commander change, but the review says what happens to the rows.
        for fold in folds:
            for r in lines:
                if (
                    r.get("kind") == "change"
                    and str(r.get("name", "")).lower() == fold["name"].lower()
                    and (r.get("zone") or "main") == fold["zone"]
                ):
                    r["fold"] = fold
    return before, after, lines, before_side, after_side


def _commanders_before_after(
    deck: Deck, changes: list[Change], cats_want: dict[int, list[str]], after: dict[str, dict[str, int]]
) -> tuple[list[str], list[str], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """The deck's commanders now and once ``changes`` are applied: cards with a row that is in
    the deck (``Deck.categories_count``: its primary category is not excluded) and in its
    commander zone (every category the deck marks premier, plus "Commander",
    ``Deck.premier_keys``, compared without case). The changes are the rows ``cats_want``
    (``category_plan_rows``) gives new categories, cards whose copies all go (``after``, per
    zone), and adds.

    The third value lists each card with a row in the commander zone and other rows in the same
    zone that loses copies (``name``, ``qty``, its copies after, and ``zone``): ``build_payload`` removes its
    other rows and keeps that many in its commander-zone row (``_rows_kept_first``), but such a
    change always gets a commander row, so the member sees it whatever the rows do. The fifth
    lists the same for a card whose copies go up, which is no commander change.

    The fourth lists every row with a commander-zone category before or after whose categories
    change (``name``, ``before`` and ``after`` categories), and every add with a commander-zone
    category that makes no commander (``added``): any of those counts as a commander change
    too, even when the commanders' names stay the same."""
    premier = deck.premier_keys()

    def commander(categories: Any) -> bool:
        cats = [c for c in categories or () if isinstance(c, str)]
        # a primary Maybeboard or Sideboard is outside the deck even on a deck that has not
        # marked that category yet (a new one is excluded when Archidekt makes it)
        primary_out = bool(cats) and cats[0].casefold() in ("maybeboard", "sideboard")
        return _in_premier(premier, cats) and deck.categories_count(cats) and not primary_out

    was: dict[str, str] = {}
    now: dict[str, str] = {}
    unsure: list[dict[str, Any]] = []
    moved: list[dict[str, Any]] = []
    folds: list[dict[str, Any]] = []
    for zone in ZONES:
        left = {n.lower() for n, q in after[zone].items() if q > 0}
        rows: dict[str, list[Any]] = {}
        for c in deck.cards_in(zone):
            rows.setdefault(c.name.lower(), []).append(c)
            if commander(c.categories):
                was[c.name.lower()] = c.name
            cats = cats_want.get(c.relation_id, c.categories) if c.relation_id is not None else c.categories
            if commander(cats) and c.name.lower() in left:
                now[c.name.lower()] = c.name
            if list(cats or []) != list(c.categories or []) and (
                _in_premier(premier, c.categories) or _in_premier(premier, cats)
            ):
                moved.append(
                    {
                        "name": c.name,
                        "before": ", ".join(c.categories or []) or "(none)",
                        "after": ", ".join(cats or []) or "(none)",
                    }
                )
        counts = {n.lower(): q for n, q in after[zone].items()}
        for key, cards in rows.items():
            if len(cards) > 1 and any(_in_premier(premier, c.categories) for c in cards):
                total = sum(c.quantity for c in cards)
                stay = {"name": cards[0].name, "qty": counts.get(key, total), "zone": zone}
                if counts.get(key, total) < total:
                    unsure.append(stay)
                elif counts.get(key, total) > total:
                    folds.append(stay)
    for ch in changes:
        cats = _all_categories([ch])
        if ch.action == "add" and _in_premier(premier, cats):
            if ch.zone == "main" and commander(cats):
                now.setdefault(ch.card_name.lower(), ch.card_name)
            else:
                moved.append({"name": ch.card_name, "added": True, "after": ", ".join(cats)})
    return (
        sorted(was.values(), key=str.lower),
        sorted(now.values(), key=str.lower),
        unsure,
        moved,
        folds,
    )


def _in_premier(premier: set[str], categories: Any) -> bool:
    """Whether any of ``categories`` is in the commander zone (``premier``: casefolded names)."""
    return any(isinstance(c, str) and c.casefold() in premier for c in categories or ())


def _rows_kept_first(deck: Deck, rows: list[Any]) -> list[Any]:
    """A card's rows in the order ``build_payload`` folds them: its commander-zone rows first, so
    a lowered count keeps the commander-zone row and removes the others."""
    premier = deck.premier_keys()
    return sorted(rows, key=lambda c: not _in_premier(premier, c.categories))


MAX_RESTORE_ENTRIES = 150


def _rel_key(card: Any) -> tuple[int | None, str, tuple[str, ...]]:
    # the categories in their order: the first is the row's primary one, which decides whether
    # the card is in the deck (Deck.categories_count), so an order change is a change
    return (card.card_id, card.modifier or "Normal", tuple(card.categories))


def _rel_fields(card: Any) -> dict[str, Any]:
    """'Sol Ring (cmr 1, Foil) [Commander]' as row fields: what makes one relation differ."""
    bits = [b for b in (card.set_code, card.collector_number) if b]
    detail = " ".join(bits)
    if (card.modifier or "Normal") != "Normal":
        detail = f"{detail}, {card.modifier}" if detail else card.modifier
    # in their order, the primary first: a restore that only reorders them shows the difference
    cats = ", ".join(clean_text(c) for c in card.categories) if card.categories else None
    return {"name": card.name, "printing": f"({detail})" if detail else None, "category": cats}


def _rel_row(kind: str, card: Any, **more: Any) -> dict[str, Any]:
    return _row(kind, **_rel_fields(card), **more)


def restore_steps(snapshot: Deck, current: Deck) -> tuple[list[dict[str, Any]], list[str]]:
    """PATCH entries and diff lines that turn ``current`` into ``snapshot`` (see ``restore_rows``)."""
    payload, rows = restore_rows(snapshot, current)
    return payload, [row_line(r) for r in rows]


def restore_rows(snapshot: Deck, current: Deck) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """PATCH entries and review rows that turn ``current`` into ``snapshot`` relation by relation:
    the same printing (card id), finish (modifier) and categories in their order, including
    Commander, Sideboard and Maybeboard, which Archidekt keeps as categories. A matching row is
    then given the snapshot's quantity, colour tag and custom mana value; rows that already match
    in all of them are untouched. The verification after a restore runs this again on the deck
    read back, so anything left over is a mismatch."""
    # The deck's categories go back first, by record id (names and flags, category_restore_plan),
    # so a category renamed since is renamed back in place and its cards are not moved one by one
    # into a new category with default flags; the cards are then compared with the deck as it
    # reads once those writes have landed.
    _bodies, category_lines = category_restore_plan(snapshot, current)
    if _bodies:
        current = deck_with_categories(current, _bodies)
    want: dict[tuple[Any, ...], tuple[Any, int]] = {}
    for c in snapshot.cards:
        if c.card_id is None:
            raise DeckError(
                "invalid", f"The snapshot does not record which printing of '{c.name}' was in the deck."
            )
        key = _rel_key(c)
        want[key] = (c, want.get(key, (c, 0))[1] + c.quantity)
    have: dict[tuple[Any, ...], list[Any]] = {}
    for c in current.cards:
        have.setdefault(_rel_key(c), []).append(c)
    payload: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    for key, (card, qty) in want.items():
        rows = have.get(key, [])
        total = sum(r.quantity for r in rows)
        label = getattr(card, "label", "") or ""
        cmc = getattr(card, "custom_cmc", None)
        same_label = all(_same_label(r.label, label) for r in rows)
        same_cmc = all(_same_mana_value(r.custom_cmc, cmc) for r in rows)
        # the row's Companion and show back face switches go back too (parity release 1)
        switched = [
            (key, ark, bool(getattr(card, key, False)))
            for _f, key, ark, _w in SWITCHES.values()
            if any(bool(getattr(r, key, False)) != bool(getattr(card, key, False)) for r in rows)
        ]
        if rows and total == qty and same_label and same_cmc and not switched:
            continue
        if rows:
            entry = _entry("modify", rows[0], qty)
            entry["modifications"]["label"] = label or NO_LABEL
            if cmc is not None or rows[0].custom_cmc is not None:
                entry["modifications"]["customCmc"] = cmc  # null takes an override off
            for _key, ark, value in switched:
                entry["modifications"][ark] = value
            payload.append(entry)
            payload.extend(_entry("remove", extra, 0) for extra in rows[1:])
            if total != qty:
                lines.append(_rel_row("change", card, before=total, after=qty))
            if not same_label:
                lines.append(
                    _row(
                        "label",
                        name=card.name,
                        before=" / ".join(sorted({label_text(r.label) for r in rows})),
                        after=label_text(label),
                        colour=split_label(label)[1] or None,
                    )
                )
            if not same_cmc:
                lines.append(
                    _row(
                        "mana_value",
                        name=card.name,
                        before=" / ".join(sorted({mana_value_text(r.custom_cmc) for r in rows})),
                        after=mana_value_text(cmc),
                    )
                )
            for key, _ark, value in switched:
                lines.append(
                    _row(
                        "companion" if key == "companion" else "back_face",
                        name=card.name,
                        before=" / ".join(sorted({"on" if getattr(r, key, False) else "off" for r in rows})),
                        after="on" if value else "off",
                    )
                )
        else:
            payload.append(
                {
                    "action": "add",
                    "cardid": card.card_id,
                    "patchId": uuid.uuid4().hex,
                    "categories": list(card.categories),
                    "modifications": {
                        "quantity": qty,
                        # the snapshot row's own flags, so a restore puts them back too
                        "companion": bool(getattr(card, "companion", False)),
                        "flippedDefault": bool(getattr(card, "flipped_default", False)),
                        "modifier": card.modifier or "Normal",
                        "label": label or NO_LABEL,
                        **({"customCmc": cmc} if cmc is not None else {}),
                    },
                }
            )
            lines.append(_rel_row("add", card, qty=qty))
    for key, rows in have.items():
        if key in want:
            continue
        payload.extend(_entry("remove", r, 0) for r in rows)
        lines.append(_rel_row("remove", rows[0], qty=sum(r.quantity for r in rows)))
    return payload, category_lines + sorted(lines, key=lambda r: row_line(r).split(" ", 1)[-1].lower())


def build_payload(
    deck: Deck,
    after: dict[str, int],
    resolve: dict[str, int],
    categories: dict[str, list[str]] | None = None,
    modifiers: dict[str, str] | None = None,
    pinned: dict[str, dict[str, Any]] | None = None,
    recategorise: dict[int, list[str]] | None = None,
    after_side: dict[str, int] | None = None,
    side_categories: dict[str, list[str]] | None = None,
    flags: dict[str, tuple[bool, bool]] | None = None,
    side_flags: dict[str, tuple[bool, bool]] | None = None,
    side_pinned: dict[str, dict[str, Any]] | None = None,
    side_modifiers: dict[str, str] | None = None,
    labels: dict[str, str] | None = None,
    side_labels: dict[str, str] | None = None,
    rows_to: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Per-card entries for PATCH modifyCards/v2, one entry per request.

    ``rows_to`` (from ``row_counts``): the count each row named by its relation id ends at; that
    row is written, the card's other rows are left as they are.

    ``labels`` and ``side_labels`` (from ``add_labels``) give a new row its colour tag, as the
    site's add sends one (``label`` in its modifications); a row that is already there and only
    grows keeps its own tag.

    ``flags`` and ``side_flags`` (from ``add_flags``) give a new row its companion and
    flippedDefault flags, as the deck page's Undo of a removal asks; a row that is already
    there keeps its own.

    ``after_side`` are the maybeboard and sideboard counts after the zone-side changes (as
    ``plan_zones`` returns them); those rows are modified, removed or added (in
    ``side_categories[name]``) the same way, by themselves, never mixed with the deck proper's rows
    of the same card.

    ``recategorise`` (from ``category_plan``) maps a relation id to the categories that row
    should have: it becomes a ``modify`` entry with the quantity unchanged and ``categories``
    replaced. A modify carrying ``categories`` is verified live only together with a quantity
    change; a change of categories alone is unverified, so ``apply`` checks the categories on
    the re-read deck.

    Shape as sent by the nccurry/mtg-mcp reference (reported from its source,
    not verified against Archidekt): ``action`` add, modify or remove; ``cardid``
    (the printing id); ``deckRelationId`` for an existing row; ``categories``;
    ``modifications`` with quantity, modifier, companion and flippedDefault.
    ``apply`` re-fetches the deck afterwards and reports a mismatch rather than
    trusting the response.

    ``modifiers`` names the finish of a card added by name; ``pinned`` holds, per card name,
    the exact printing an add asked for (``cardid``, ``modifier``, ``quantity``,
    ``categories``): that printing goes in as its own row unless the deck already has a row
    with the same printing and finish, which then grows by the added quantity. ``side_pinned`` does
    the same for a maybeboard or sideboard add, among that card's rows there (its ``categories``
    are the side categories the add goes in), and ``side_modifiers`` names a side add's finish.
    """
    by_name: dict[str, list[Any]] = {}
    for c in deck.main_cards:  # never touch maybeboard or sideboard rows
        by_name.setdefault(c.name.lower(), []).append(c)
    payload: list[dict[str, Any]] = []

    def by_row(existing: list[Any], qty: int) -> bool:
        """Write the rows named by relation id (True: the card's count is all theirs)."""
        hit = [c for c in existing if c.relation_id in (rows_to or {})]
        if not hit:
            return False
        for c in hit:
            n = (rows_to or {})[c.relation_id]
            if n != c.quantity:
                payload.append(_entry("modify" if n > 0 else "remove", c, n))
        rest = sum(c.quantity for c in existing if c not in hit)
        if qty - sum((rows_to or {})[c.relation_id] for c in hit) != rest:
            raise DeckError("invalid", "a row's count does not match the planned count; nothing was sent")
        return True

    for name, qty in after.items():
        existing = by_name.get(name.lower(), [])
        current = sum(c.quantity for c in existing)
        if by_row(existing, qty):
            continue
        if qty == current:
            continue
        pin = (pinned or {}).get(name)
        if pin:
            qty, existing = _pinned_entries(
                payload, name, qty, existing, pin, (flags or {}).get(name), label=(labels or {}).get(name)
            )
            if qty == sum(c.quantity for c in existing):
                continue
        if existing:
            primary, *extras = _rows_kept_first(deck, existing)
            payload.append(_entry("modify" if qty > 0 else "remove", primary, qty))
            payload.extend(_entry("remove", extra, 0) for extra in extras)
        else:
            payload.append(
                _add_entry(
                    resolve[name],
                    qty,
                    (categories or {}).get(name),
                    (modifiers or {}).get(name, "Normal"),
                    (flags or {}).get(name, (False, False)),
                    (labels or {}).get(name),
                )
            )
    side_by_name: dict[str, list[Any]] = {}
    for c in deck.side_cards:
        side_by_name.setdefault(c.name.lower(), []).append(c)
    for name, qty in (after_side or {}).items():
        existing = side_by_name.get(name.lower(), [])
        if by_row(existing, qty):
            continue
        if qty == sum(c.quantity for c in existing):
            continue
        pin = (side_pinned or {}).get(name)
        if pin:
            qty, existing = _pinned_entries(
                payload,
                name,
                qty,
                existing,
                pin,
                (side_flags or {}).get(name),
                side=True,
                label=(side_labels or {}).get(name),
            )
            if qty == sum(c.quantity for c in existing):
                continue
        if existing:
            primary, *extras = _rows_kept_first(deck, existing)
            payload.append(_entry("modify" if qty > 0 else "remove", primary, qty))
            payload.extend(_entry("remove", extra, 0) for extra in extras)
        else:
            payload.append(
                _add_entry(
                    resolve[name],
                    qty,
                    (side_categories or {}).get(name) or [deck.side_category()],
                    (side_modifiers or {}).get(name, "Normal"),
                    (side_flags or {}).get(name, (False, False)),
                    (side_labels or {}).get(name),
                )
            )
    for card in deck.cards:
        if card.relation_id in (recategorise or {}):
            payload.append(_entry("modify", card, card.quantity, categories=recategorise[card.relation_id]))
    return payload


def _pinned_entries(
    payload: list[dict[str, Any]],
    name: str,
    qty: int,
    existing: list[Any],
    pin: dict[str, Any],
    flags: tuple[bool, bool] | None,
    side: bool = False,
    label: str | None = None,
) -> tuple[int, list[Any]]:
    """Append the write for a pinned add of ``name`` (one zone's rows in ``existing``): the row
    with the same printing and finish grows, or the printing goes in as a new row. In the
    maybeboard or sideboard (``side``) only a row filed under the add's own category grows, so a
    Maybeboard add never adds to the Sideboard's count. Returns the count still to reach and the
    card's other rows, for the zone's remaining changes."""
    # categories compare as Archidekt does, ignoring case: a typed "sideboard" is the Sideboard
    want_cat = str((pin.get("categories") or [""])[0] or "").casefold() if side else None
    same = [
        c
        for c in existing
        if c.card_id == pin["cardid"]
        and (c.modifier or "Normal") == pin["modifier"]
        and (want_cat is None or str((c.categories or [""])[0] or "").casefold() == want_cat)
    ]
    if same:
        payload.append(_entry("modify", same[0], same[0].quantity + int(pin["quantity"])))
        qty -= same[0].quantity + int(pin["quantity"])
    else:
        payload.append(
            _add_entry(
                pin["cardid"],
                int(pin["quantity"]),
                pin.get("categories"),
                pin["modifier"],
                flags or (False, False),
                label,
            )
        )
        qty -= int(pin["quantity"])
    rest = [c for c in existing if c not in same]
    if qty < sum(c.quantity for c in rest):
        # parse_changes refuses the combinations that get here; never send writes that
        # shrink other rows below what the reviewed diff says
        raise DeckError("invalid", f"the pinned add of '{name}' does not match the planned count")
    # whatever the proposal's other changes do to this card's remaining rows
    return qty, rest


def _add_entry(
    cardid: int,
    qty: int,
    categories: list[str] | None,
    modifier: str,
    flags: tuple[bool, bool] = (False, False),
    label: str | None = None,
) -> dict[str, Any]:
    """A new row; ``flags`` are its companion and flippedDefault (an Undo of a removal); ``label``
    its colour tag, when the add names one."""
    entry: dict[str, Any] = {
        "action": "add",
        "cardid": cardid,
        "patchId": uuid.uuid4().hex,
        "categories": list(categories or []),
        "modifications": {
            "quantity": qty,
            "companion": flags[0],
            "flippedDefault": flags[1],
            "modifier": modifier,
        },
    }
    if label:
        entry["modifications"]["label"] = label
    return entry


def add_labels(changes: list[Change], counts: dict[str, int], zone: str) -> dict[str, str]:
    """The colour tag each add in ``zone`` asks for, keyed by the spelling in ``counts``, as
    ``add_flags``; only names with a tag."""
    out: dict[str, str] = {}
    for ch in changes:
        if ch.action == "add" and ch.zone == zone and ch.label:
            out[next((k for k in counts if k.lower() == ch.card_name.lower()), ch.card_name)] = ch.label
    return out


def label_mismatches(changes: list[Change], deck: Deck, verified: Deck) -> list[str]:
    """Adds that asked for a colour tag whose new rows came back without it (rows the deck already
    had keep their own tag, so only new rows are checked)."""
    had = {c.relation_id for c in deck.cards}
    out = []
    for ch in changes:
        if ch.action != "add" or not ch.label:
            continue
        name = ch.card_name.lower()
        new = [c for c in verified.cards if c.relation_id not in had and c.name.lower() == name]
        if new and not all(_same_label(c.label, ch.label) for c in new):
            out.append(f"the color tag on {ch.card_name}")
    return out


def add_flags(changes: list[Change], counts: dict[str, int], zone: str) -> dict[str, tuple[bool, bool]]:
    """The companion and flippedDefault flags the adds in ``zone`` ask for, keyed by the spelling
    in ``counts`` (``after`` or ``after_side``, as build_payload reads them); only names with a
    flag set."""
    out: dict[str, tuple[bool, bool]] = {}
    for ch in changes:
        if ch.action != "add" or ch.zone != zone or not (ch.companion or ch.flipped_default):
            continue
        key = next((k for k in counts if k.lower() == ch.card_name.lower()), ch.card_name)
        was = out.get(key, (False, False))
        out[key] = (was[0] or ch.companion, was[1] or ch.flipped_default)
    return out


def _entry(action: str, card: Any, qty: int, categories: list[str] | None = None) -> dict[str, Any]:
    """One modifyCards entry for an existing row; ``categories`` replaces the row's own when given.
    A row's colour tag and custom mana value go along when it has them, as Archidekt's own editor
    sends them with every change (its site code, read 2026-10-10), so an edit does not drop them.
    A row with no tag sends Archidekt's empty one, ``NO_LABEL``, as its editor does."""
    mods: dict[str, Any] = {
        "quantity": qty,
        # the row's own flags, as the site sends them (its site code, read 2026-10-14): sending
        # False would clear a companion or a back-face default on any change to the row
        "companion": bool(getattr(card, "companion", False)),
        "flippedDefault": bool(getattr(card, "flipped_default", False)),
        "modifier": card.modifier or "Normal",
        "label": getattr(card, "label", "") or NO_LABEL,
    }
    if getattr(card, "custom_cmc", None) is not None:
        mods["customCmc"] = card.custom_cmc
    return {
        "action": action,
        "cardid": card.card_id,
        "deckRelationId": card.relation_id,
        "patchId": uuid.uuid4().hex,
        "categories": list(card.categories if categories is None else categories),
        "modifications": mods,
    }


# -- the member's own custom cards in a deck (kind "custom_cards", 2026-10-23) -------------------
# Archidekt's deck JSON carries them in ``customCards`` beside ``cards`` (Deck.custom_cards). The
# add is the shape archidekt.com's own deck page sends (_app.js module 71983 ``add``: customCardId
# in place of cardid); a count change and a remove follow its ``prepareSave``, which sends
# customCardId and deckRelationId on every modify and remove. Read from the site's scripts, not
# tried on a live deck yet: the read-back after the write checks every change.
CUSTOM_CARD_ACTIONS = ("add", "remove", "set_quantity")
MAX_CUSTOM_CARD_CHANGES = 20
MAX_CUSTOM_COPIES = 99


def parse_custom_card_changes(raw: Any) -> list[dict[str, Any]]:
    """``[{"action": "add" | "remove" | "set_quantity", "custom_card_id": int, "quantity"?: 1-99,
    "category"?: str, "label"?: str, "color"?: str, "mana_value"?: int | None}]`` as stored: an
    add's quantity defaults to 1 and a set_quantity's to the count the deck has; no category means
    none is sent (Archidekt files the card itself) for an add, and the row keeps its own otherwise.
    ``label`` puts a colour tag on the card as set_label does (an empty one takes it off), and
    ``mana_value`` its custom mana value as set_mana_value does (null takes it off): both as
    Archidekt's own card menu sets them on a custom card. Left out, the row keeps its own."""
    if not isinstance(raw, list) or not raw:
        raise DeckError("invalid", "changes must be a non-empty list")
    if len(raw) > MAX_CUSTOM_CARD_CHANGES:
        raise DeckError("invalid", f"at most {MAX_CUSTOM_CARD_CHANGES} custom card changes at once")
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise DeckError("invalid", f"change {i}: must be an object")
        action = item.get("action")
        if action not in CUSTOM_CARD_ACTIONS:
            raise DeckError("invalid", f"change {i}: action must be one of {', '.join(CUSTOM_CARD_ACTIONS)}")
        cid = item.get("custom_card_id")
        if isinstance(cid, str) and cid.strip().isdigit():
            cid = int(cid.strip())
        if not isinstance(cid, int) or isinstance(cid, bool) or cid < 1:
            raise DeckError("invalid", f"change {i}: custom_card_id must be the custom card's id (a number)")
        if cid in seen:
            raise DeckError(
                "invalid", f"change {i}: custom card {cid} is named twice; give one change per card"
            )
        seen.add(cid)
        qty = item.get("quantity")
        label: str | None = None
        mana: dict[str, Any] = {}
        if action == "remove":
            qty = None
            if item.get("label") is not None or "mana_value" in item:
                raise DeckError("invalid", f"change {i}: a remove takes no label or mana_value")
        else:
            if item.get("label") is not None:
                label = _parse_label_change(i, "", {"label": item["label"], "color": item.get("color")}).label
            if "mana_value" in item:
                mana = {
                    "mana_value": _parse_mana_value_change(
                        i, "", {"mana_value": item["mana_value"]}
                    ).mana_value
                }
            qty = 1 if qty is None and action == "add" else qty
        if qty is None and action == "set_quantity":
            pass  # the count the deck has (custom_card_rows)
        elif action != "remove":
            if not isinstance(qty, int) or isinstance(qty, bool) or not 1 <= qty <= MAX_CUSTOM_COPIES:
                raise DeckError(
                    "invalid", f"change {i}: quantity must be a whole number from 1 to {MAX_CUSTOM_COPIES}"
                )
        cat = item.get("category")
        if cat is not None and not isinstance(cat, str):
            raise DeckError("invalid", f"change {i}: category must be text")
        cat = clean_text(cat or "")
        if len(cat) > 60:
            raise DeckError("invalid", f"change {i}: a category name is at most 60 characters")
        if action == "remove" and cat:
            raise DeckError("invalid", f"change {i}: a remove takes no category")
        out.append(
            {
                "action": action,
                "custom_card_id": cid,
                "quantity": qty,
                "category": cat or None,
                "label": label,
                **mana,
            }
        )
    return out


def custom_card_rows(
    deck: Deck, wanted: list[dict[str, Any]], names: dict[int, str]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(plan, review rows) for custom card changes on ``deck``. ``names``: the member's own
    custom cards that may be added, by id (an add of any other id is refused). One row per
    change: the card's name, its count before and after, and its category."""
    premier = {c.casefold() for c in deck.premier_categories()}
    by_id: dict[int, list[Any]] = {}
    for c in deck.custom_cards:
        if c.custom_card_id is not None:
            by_id.setdefault(c.custom_card_id, []).append(c)
    plan: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    for w in wanted:
        cid, action, cat = int(w["custom_card_id"]), w["action"], w.get("category")
        have = by_id.get(cid, [])
        if len(have) > 1:
            raise DeckError(
                "invalid",
                f"“{have[0].name}” is in this deck in {len(have)} rows; change it on Archidekt.",
            )
        cur = have[0] if have else None
        if action == "add":
            if cid not in names:
                raise DeckError(
                    "not_found",
                    f"Custom card {cid} is not one of your custom cards, so it can't be added. Only "
                    "your own custom cards (My lists) go in a deck.",
                )
            if cur is not None:
                raise DeckError(
                    "invalid",
                    f"“{cur.name}” is already in this deck ({cur.quantity}); use set_quantity to change "
                    "its count.",
                )
            name, before, after = names[cid], 0, int(w["quantity"])
            was: list[str] = []
            now = [cat] if cat else []
            label_was, cmc_was = "", None
        else:
            if cur is None:
                raise DeckError("invalid", f"Custom card {cid} isn't in this deck.")
            name, before = cur.name, cur.quantity
            after = 0 if action == "remove" else int(w["quantity"] or before)
            was = list(cur.categories)
            now = [cat] if cat else list(was)
            label_was, cmc_was = cur.label or "", cur.custom_cmc
        label_now = label_was if w.get("label") is None else w["label"]
        cmc_now = w["mana_value"] if "mana_value" in w else cmc_was
        same_label = _same_label(label_was, label_now)
        same_cmc = _same_mana_value(cmc_was, cmc_now)
        if action == "set_quantity" and after == before and now == was and same_label and same_cmc:
            raise DeckError("invalid", f"“{name}” already has {before} in this deck; nothing would change.")
        plan.append(
            {
                "action": action,
                "custom_card_id": cid,
                "quantity": after,
                "categories": now,
                "label": label_now,
                "mana_value": cmc_now,
            }
        )
        row: dict[str, Any] = {
            "kind": "custom_card",
            "action": action,
            "custom_card_id": cid,
            "name": name,
            "before": before,
            "after": after,
            "category_before": ", ".join(was),
            "category": ", ".join(now),
        }
        if action != "remove" and not same_label:
            row["label_before"], row["label"] = label_text(label_was), label_text(label_now)
        if action != "remove" and not same_cmc:
            row["mana_value_before"], row["mana_value"] = mana_value_text(cmc_was), mana_value_text(cmc_now)
        if any(c.casefold() in premier for c in was + now):
            row["commander_zone"] = True
        rows.append(row)
    return plan, rows


def custom_card_line(r: dict[str, Any]) -> str:
    """A ``custom_card`` review row in words: the same line on the review page, the chat card,
    History and Activity."""
    name = clean_text(str(r.get("name") or ""))
    count = f"{int(r.get('before') or 0)} -> {int(r.get('after') or 0)}"
    was, now = str(r.get("category_before") or ""), str(r.get("category") or "")
    if r.get("action") == "add":
        where = f"category “{now}”" if now else "no category given (Archidekt files it)"
        return f"Add custom card “{name}”: {count}, {where}{_custom_extras(r)}"
    if r.get("action") == "remove":
        return f"Remove custom card “{name}”: {count}" + (f", from “{was}”" if was else "")
    if was != now:
        where = f", category “{was or 'none'}” -> “{now}”"
    else:
        where = f", category “{now}”" if now else ""
    return f"Custom card “{name}”: {count}{where}{_custom_extras(r)}"


def _custom_extras(r: dict[str, Any]) -> str:
    out = ""
    if "label" in r:
        out += f", color tag {clean_text(str(r.get('label_before')))} -> {clean_text(str(r.get('label')))}"
    if "mana_value" in r:
        out += f", mana value {r.get('mana_value_before')} -> {r.get('mana_value')}"
    return out


def custom_card_entry(p: dict[str, Any], row: Any) -> dict[str, Any]:
    """One modifyCards entry for a planned custom card change; ``row``: the deck's custom row
    (Deck.custom_cards) for a count change or remove, None for an add. No ``cardid``, as the
    site leaves it out for a custom card."""
    if p["action"] == "add":
        return {
            "action": "add",
            "customCardId": p["custom_card_id"],
            "categories": list(p["categories"]),  # none given: none sent (a gateway choice)
            "patchId": uuid.uuid4().hex,
            "modifications": {
                "quantity": p["quantity"],
                "modifier": "Normal",
                "customCmc": p.get("mana_value"),
                "companion": False,
                "flippedDefault": False,
                "label": p.get("label") or NO_LABEL,
            },
        }
    return {
        "action": "remove" if p["action"] == "remove" else "modify",
        "customCardId": p["custom_card_id"],
        "deckRelationId": row.relation_id,
        "patchId": uuid.uuid4().hex,
        "categories": list(p["categories"]),
        "modifications": {
            "quantity": p["quantity"] or row.quantity,
            "modifier": row.modifier or "Normal",
            "customCmc": p["mana_value"] if "mana_value" in p else row.custom_cmc,
            "companion": bool(row.companion),
            "flippedDefault": bool(row.flipped_default),
            "label": (p["label"] if "label" in p else row.label) or NO_LABEL,
        },
    }


def custom_card_mismatches(plan: list[dict[str, Any]], names: dict[int, str], verified: Deck) -> list[str]:
    """What the deck read back after the write does not show: each card's count, and its
    category when one was named (an add with none is filed by Archidekt, so any is fine)."""
    by_id: dict[int, list[Any]] = {}
    for c in verified.custom_cards:
        if c.custom_card_id is not None:
            by_id.setdefault(c.custom_card_id, []).append(c)
    out: list[str] = []
    for p in plan:
        have = by_id.get(p["custom_card_id"], [])
        name = have[0].name if have else names.get(p["custom_card_id"], f"custom card {p['custom_card_id']}")
        total = sum(c.quantity for c in have)
        if total != p["quantity"]:
            out.append(f"“{name}” ({total} in the deck, not {p['quantity']})")
        elif (
            p["quantity"]
            and p["categories"]
            and not any(sorted(c.categories) == sorted(p["categories"]) for c in have)
        ):
            got = ", ".join(have[0].categories) or "none"
            out.append(f"“{name}” (category “{got}”, not “{', '.join(p['categories'])}”)")
        elif (
            p["quantity"] and have and "label" in p and not _same_label(have[0].label or "", p["label"] or "")
        ):
            out.append(
                f"“{name}” (color tag {label_text(have[0].label or '')}, not {label_text(p['label'] or '')})"
            )
        elif (
            p["quantity"]
            and have
            and "mana_value" in p
            and not _same_mana_value(have[0].custom_cmc, p["mana_value"])
        ):
            out.append(
                f"“{name}” (mana value {mana_value_text(have[0].custom_cmc)}, "
                f"not {mana_value_text(p['mana_value'])})"
            )
    return out


def row_key(c: dict[str, Any]) -> tuple[str, str, str]:
    """One list row's identity: name plus the printing it names, so two printings of the same
    card (a deck's Sol Ring and its etched Sol Ring) stay two rows through a new-deck import."""
    return (
        str(c.get("name", "")).casefold(),
        str(c.get("set_code") or "").casefold(),
        str(c.get("collector_number") or ""),
    )


def new_deck_entries(
    cards: list[dict[str, Any]],
    resolve: dict[tuple[str, str, str], int],
    modifiers: dict[tuple[str, str, str], str] | None = None,
) -> list[dict[str, Any]]:
    """Add entries for a freshly created deck (same reference shape as build_payload);
    ``resolve`` and ``modifiers`` are keyed by ``row_key``."""
    out = []
    for c in cards:
        key = row_key(c)
        out.append(
            {
                "action": "add",
                "cardid": resolve[key],
                "patchId": uuid.uuid4().hex,
                "categories": list(c.get("categories") or []),
                "modifications": {
                    "quantity": int(c["quantity"]),
                    "companion": False,
                    "flippedDefault": False,
                    "modifier": (modifiers or {}).get(
                        key, c.get("finish") or ("Foil" if c.get("foil") else "Normal")
                    ),
                    # the list's colour tag, in Archidekt's modifications.label as its editor sends it
                    **({"label": c["label"]} if c.get("label") else {}),
                },
            }
        )
    return out


def deck_to_text(deck: Deck, *, zone: str = "main") -> str:
    """Plain decklist text for the research and simulation tools (mainboard by default)."""
    cards = [
        ListCard(
            quantity=c.quantity,
            name=c.name,
            set_code=c.set_code,
            collector_number=c.collector_number,
            # the simulators find the commander by the literal "Commander" category, so a
            # premier category with another name is written as Commander as well
            categories=list(c.categories)
            + (["Commander"] if deck.is_commander(c) and "Commander" not in c.categories else []),
            foil=c.modifier.lower() in ("foil", "etched"),
            finish=c.modifier.capitalize() if c.modifier.lower() in ("foil", "etched") else "",
            zone="main" if deck.in_deck(c) else "side",
        )
        for c in deck.cards
    ]
    return to_text(cards, zone=zone)


_FINISH_MARKS = {"foil": " *F*", "etched": " *E*"}


def deck_to_archidekt_text(deck: Deck) -> str:
    """The deck in Archidekt's own import syntax, one line per row, sorted:
    ``1x Name (set) 123 *F* [Category{top}] ^Label,#hex^``. ``{top}`` marks the commander
    (premier) category, ``{noDeck}{noPrice}`` a category outside the deck (maybeboard), so the
    text pastes back into Archidekt's import dialog as the same deck, finishes and labels kept."""
    premier = {
        str(c.get("name")) for c in deck.categories if isinstance(c.get("name"), str) and c.get("isPremier")
    }
    excluded = deck.excluded_categories()
    lines: list[tuple[str, str, str, str]] = []  # sorted by card name, as Archidekt's export is
    for c in deck.cards:
        line = f"{c.quantity}x {clean_text(c.name.replace(chr(94), chr(32)))}"  # ^ would open a label
        if c.set_code:
            line += f" ({clean_text(c.set_code)})"
            if c.collector_number:
                line += f" {clean_text(c.collector_number)}"
        line += _FINISH_MARKS.get(c.modifier.lower(), "")
        cats = []
        for cat in c.categories:
            name = clean_category(cat)
            if not name:
                continue
            flags = "{top}" if cat in premier else "{noDeck}{noPrice}" if cat in excluded else ""
            cats.append(name + flags)
        if cats:
            line += " [" + ",".join(cats) + "]"
        label = clean_text(c.label).replace("^", " ").strip()
        if label and not label.startswith(","):  # Archidekt stores labels as "name,#colour"
            line += f" ^{label}^"
        lines.append((c.name.casefold(), c.set_code, c.collector_number, line))
    lines.sort()
    return "\n".join(line for *_, line in lines) + ("\n" if lines else "")


class _Duplicate(Exception):
    """_save_proposal found an exact duplicate: the same app's waiting note on the same deck
    with exactly these changes. Nothing new is saved; ``_dedupe`` answers with that note."""

    def __init__(self, proposal_id: str):
        super().__init__(proposal_id)
        self.proposal_id = proposal_id


def _dedupe(fn: Any) -> Any:
    """For the propose methods: an exact duplicate of a waiting note of the same app returns that
    note ("already waiting as #2") instead of a second one."""

    @wraps(fn)
    async def run(self: DeckService, sub: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        try:
            return await fn(self, sub, *args, **kwargs)
        except _Duplicate as dup:
            out = self.describe(sub, dup.proposal_id)
            no = out.get("board_no")
            out["duplicate_of"] = dup.proposal_id
            out["note"] = (
                (f"Already waiting as #{no}" if no else "Already waiting")
                + ": this exact change was proposed before and has not been decided, so no second "
                "copy was made."
            )
            return out

    return run


class DeckService:
    def __init__(self, settings: Settings, db: Database, client: ArchidektClient):
        self.settings = settings
        self.db = db
        self.client = client
        self.fernet = Fernet(settings.fernet_key.encode())
        self._deck_locks: dict[str, asyncio.Lock] = {}
        # deck id -> the progress of the new-deck apply still putting its cards in (_apply_create):
        # every other change to that deck is refused until it ends (being_made)
        self._being_made: dict[str, tuple[dict[str, Any], list[dict[str, Any]]]] = {}
        # the folders (with all inside) a member's running folder delete will delete: nothing is
        # moved or made in them meanwhile
        self._deleting: dict[str, set[int]] = {}
        # Applies running in the background (proposal id -> task) and what each got done so far.
        self._running: dict[str, asyncio.Task[Any]] = {}
        self._progress: dict[str, dict[str, Any]] = {}
        # Selections applied with one press (batch.py) and their result panels.
        self.batches = batch.Batches()
        self._archidekt_in_flight: dict[str, int] = {}
        self._link_locks: dict[str, asyncio.Lock] = {}
        # backup copy id -> the name, description and folder id it was made with, until its
        # unused delete is queued; only the newest BACKUPS_REMEMBERED are kept
        self._backups_made: dict[str, dict[str, str]] = {}
        # delete boxes open now (prepare_delete): (member, deck id) -> what the box read
        self._delete_prep: dict[tuple[str, str], dict[str, Any]] = {}
        self._prep_tasks: set[asyncio.Task[None]] = set()
        self.max_archidekt_per_user = MAX_ARCHIDEKT_PER_USER
        self.archidekt_slot_wait = ARCHIDEKT_SLOT_WAIT
        # sub -> waiters for one of its slots: (0 for a page, else 1; arrival; future)
        self._slot_waiters: dict[str, list[tuple[int, int, asyncio.Future[None]]]] = {}
        self._slot_seq = 0
        self.archidekt_budget = RateBudget(settings.archidekt_calls_per_10_min)
        # the dashboard settings with a deployment value winning (config.MANAGED_SETTINGS); read a
        # managed key through here, not through ``settings``, so a dashboard change applies
        self.live = LiveSettings(settings, db)
        # A proposal's risk tier is stored when it closes (the Proposals page's Risk filter); the
        # ones closed before that was stored get theirs now, once (Database.settle_risks).
        db.risk_source = self.stored_risk
        db.settle_risks()
        # sub -> {matched card name (lower case): what the scan read}; set when scanning is on
        self.guessed_names: Callable[[str], dict[str, str]] | None = None
        # names -> whether each is a Magic card (None: cannot tell); set when scanning is on, from
        # the card-name catalog the gateway keeps in memory (Scryfall asked only for a few names it
        # lacks), so the check costs no Archidekt call
        self.known_cards: Callable[[list[str]], Awaitable[dict[str, bool | None]]] | None = None
        # identifiers -> (Scryfall cards, not found); set when scanning is on. A proposal's added
        # cards are looked up once, to judge what the change does to the deck's legality.
        self.card_collection: Callable[[list[dict[str, str]]], Awaitable[tuple[list, list]]] | None = None
        # Set by collection.py: applies a ``collection`` proposal's stored changes against the
        # member's Archidekt Collection and returns the result rows (DeckService itself knows only
        # decks). None while the collection pages are not loaded.
        self.collection_apply: Any = None
        self._precon_cache: tuple[float, dict[str, list[dict[str, Any]]]] | None = None
        # Archidekt's tag list as the settings page's tag box looked it up: typed text (folded) ->
        # (until when, tag names), shared by members (it is one list), oldest dropped past the size
        self._tag_lookups: dict[str, tuple[float, list[str]]] = {}
        # The member's deck list (list_decks), served from memory between Archidekt reads and
        # dropped by every write the gateway sends for that member.
        self.deck_lists = MemberCache(
            DECK_LIST_FRESH,
            DECK_LIST_STALE,
            keep_for=lambda rows: DECK_LIST_PARTIAL if getattr(rows, "unreadable", 0) else None,
        )
        self.deck_list_wait = DECK_LIST_COLD_WAIT
        # The member's folder tree as My decks shows it (folder_view), kept like the deck list and
        # dropped by the same writes. The Folders page and every folder write read Archidekt live.
        self.folder_trees = MemberCache(DECK_LIST_FRESH, DECK_LIST_STALE)
        # (member, deck id) -> (when read, the deck): what a deck page showed, for the next view of
        # it (page_deck). Every write the gateway sends empties it; the counter tells a read that
        # was under way during a write not to keep what it got.
        self._page_decks: dict[tuple[str, str], tuple[float, bytes]] = {}
        self._page_decks_gen = 0
        # deck id -> when a write to it last dropped its kept pages: a read under way then keeps nothing
        self._page_decks_dropped: dict[str, float] = {}
        self._page_refreshing: dict[tuple[str, str], asyncio.Task[Any]] = {}  # background re-reads
        # what the last background re-read of a kept deck page found: {"changed", "failed"}
        self._page_checked: dict[tuple[str, str], dict[str, bool]] = {}
        # (member, deck id) -> (wall time read, the deck): the member's last good read of a deck page,
        # kept through writes, shown read-only and dated while Archidekt can't be asked
        self._page_last: dict[tuple[str, str], tuple[float, bytes]] = {}
        self.page_deck_seconds = min(PAGE_DECK_SECONDS, float(settings.archidekt_cache_seconds))
        # Other member caches (the collection's first page) hear about writes here: (sub, path).
        self.write_hooks: list[Callable[[str, str], None]] = []
        from . import packages

        self.write_hooks.append(packages.forget)  # My decks' kept package list
        listeners = getattr(self.client, "write_listeners", None)  # a test double may lack it
        if listeners is not None:
            listeners.append(self._on_archidekt_write)
        self._touched: dict[str, float] = {}  # sub -> when last_used_at was last written
        # Set by app.py when the deck sync (sync.py) runs: proposal id -> when the sync saw that
        # the proposal's deck no longer matches it (None while it has not), shown with the proposal.
        self.out_of_date: Callable[[str], int | None] | None = None
        self.social: Any = None  # social.SocialService, set by add_social_routes; actions.py uses it

    def _on_archidekt_write(self, path: str) -> None:
        """A write is about to go to Archidekt with some member's session: whatever the gateway
        remembers for that member about what the write changes is stale from now on."""
        # Deck pages of every member: a collaborator's change shows on the owner's next view too.
        # A write to one deck drops that deck's pages only; any other write (the collection, which
        # changes what every deck shows as owned, a folder) drops them all.
        one = _DECK_WRITE_PATH.match(path)
        if one is not None:
            self._forget_page_deck(one.group(1))
        else:
            self._forget_page_decks()
        self._delete_prep.clear()  # a delete box's read may be of the deck this changes
        sub = _acting_sub.get()
        if not sub:
            return
        if not path.startswith("/collection"):
            self.deck_lists.drop(sub)
            self.folder_trees.drop(sub)
        for hook in self.write_hooks:
            hook(sub, path)

    def forget_member(self, sub: str) -> None:
        """Drop everything cached for a member (their link changed or their data was deleted)."""
        self.deck_lists.forget(sub)
        self.folder_trees.forget(sub)
        self._forget_page_decks()
        for key in [k for k in self._page_last if k[0] == sub]:
            del self._page_last[key]
        for key in [k for k in self._delete_prep if k[0] == sub]:
            del self._delete_prep[key]
        # the Account page's profile, the bell and My lists' counts are the old account's too
        # (security review 0.7.43)
        from . import account_lists, custom_pictures, packages, profile_page

        profile_page.forget_all(sub)
        account_lists.forget_counts(sub)
        packages.forget(sub)
        custom_pictures.forget(sub)
        for hook in self.write_hooks:
            hook(sub, "")

    @asynccontextmanager
    async def archidekt_slot(self, sub: str | None, *, wait: float | None = None) -> AsyncIterator[None]:
        """Count one unit of Archidekt work for ``sub`` while the block runs. At most
        ``max_archidekt_per_user`` run or wait in the shared pacer per member; another waits
        outside the pacer for one of them to end (see ARCHIDEKT_SLOT_WAIT), so it never queues
        the pacer up for everyone. ``wait`` shortens that wait (0: refused at once unless a slot is
        free), for a best-effort read. A task that already holds the member's slot (an apply's
        reads and writes) does not take another."""
        if not sub or _slot_holder.get() == sub:
            yield
            return
        await self._take_slot(sub, wait=wait)
        try:
            refused = self.budget_refusal(sub)
            if refused:
                err = DeckError("rate_limited", refused, retry_after=self.archidekt_budget.retry_after(sub))
                err.busy_cause = "budget"
                raise err
            token = _slot_holder.set(sub)
            try:
                yield
            finally:
                _slot_holder.reset(token)
        finally:
            self._give_slot(sub)

    def _slots_full(self, sub: str) -> DeckError:
        err = DeckError(
            "rate_limited",
            f"{self.max_archidekt_per_user} Archidekt actions for your account are still running "
            "or waiting their turn; wait for them to finish and try again.",
        )
        err.busy_cause = "member_slots"
        return err

    async def _take_slot(self, sub: str, *, wait: float | None = None) -> None:
        """One of the member's slots: at once when one is free and nobody is waiting, else the
        next one to end, the member's pages before the rest; refused after
        ``archidekt_slot_wait`` seconds (or ``wait``, when shorter) or with
        MAX_ARCHIDEKT_WAITING_PER_USER already waiting."""
        waiters = self._slot_waiters.get(sub, [])
        if self._archidekt_in_flight.get(sub, 0) < self.max_archidekt_per_user and not waiters:
            self._archidekt_in_flight[sub] = self._archidekt_in_flight.get(sub, 0) + 1
            return
        limit = self.archidekt_slot_wait if wait is None else min(wait, self.archidekt_slot_wait)
        if self.max_archidekt_per_user <= 0 or limit <= 0 or len(waiters) >= MAX_ARCHIDEKT_WAITING_PER_USER:
            raise self._slots_full(sub)
        fut: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._slot_seq += 1
        entry = (0 if page_turn.get() else 1, self._slot_seq, fut)
        heapq.heappush(self._slot_waiters.setdefault(sub, waiters), entry)
        try:
            await asyncio.wait_for(fut, limit)  # the slot is handed over counted
        except BaseException as exc:
            if fut.done() and not fut.cancelled():
                self._give_slot(sub)  # handed a slot just as this waiter gave up: pass it on
            else:
                self._drop_waiter(sub, entry)
            if isinstance(exc, TimeoutError):
                raise self._slots_full(sub) from None
            raise

    def _drop_waiter(self, sub: str, entry: tuple[int, int, asyncio.Future[None]]) -> None:
        waiters = self._slot_waiters.get(sub)
        if waiters is None:
            return
        if entry in waiters:
            waiters.remove(entry)
            heapq.heapify(waiters)
        if not waiters:
            self._slot_waiters.pop(sub, None)

    def _give_slot(self, sub: str) -> None:
        """A slot of the member's ended: hand it to the next waiter, else count it free."""
        waiters = self._slot_waiters.get(sub)
        while waiters:
            _line, _seq, fut = heapq.heappop(waiters)
            if not fut.done():
                if not waiters:
                    self._slot_waiters.pop(sub, None)
                fut.set_result(None)
                return
        self._slot_waiters.pop(sub, None)
        left = self._archidekt_in_flight.get(sub, 1) - 1
        if left > 0:
            self._archidekt_in_flight[sub] = left
        else:
            self._archidekt_in_flight.pop(sub, None)

    def budget_refusal(self, sub: str) -> str | None:
        """Spend one unit of the member's Archidekt budget; the refusal to show when it is used up
        (None when the call may go ahead). Also called by the research proxy for its
        archidekt_* tools, which reach Archidekt through Mystic Forge."""
        if self.archidekt_budget.take(sub):
            return None
        return (
            f"Your account has used its {self.archidekt_budget.per_window} Archidekt actions for "
            f"the last {ARCHIDEKT_BUDGET_WINDOW // 60} minutes; try again later."
        )

    def _sync_spend(self, sub: str) -> None:
        """Spend one unit of the member's Archidekt budget for a deck sync read, only while at
        least a quarter of the budget stays for the member's own actions; DeckError("budget")
        otherwise, which ends that member's check until the next one."""
        if not self.archidekt_budget.take(sub, keep=self.archidekt_budget.per_window // 4):
            raise DeckError("budget", "The deck sync waits while the member's Archidekt budget is low.")

    # -- account links --------------------------------------------------------
    async def _link_attempt(self, sub: str, login: str, password: str) -> dict[str, Any]:
        """One Archidekt sign-in for ``link``, refused past MAX_LINK_FAILURES recent failures."""
        since = int(time.time()) - LINK_FAILURE_WINDOW
        if self.db.count_audit(sub, "archidekt_link_failed", since) >= MAX_LINK_FAILURES:
            raise DeckError(
                "rate_limited",
                f"Too many failed Archidekt sign-ins in the last {LINK_FAILURE_WINDOW // 60} minutes. "
                "Wait a while, check your Archidekt username and password, then try again.",
            )
        try:
            async with self.archidekt_slot(sub):
                session: dict[str, Any] = await self.client.login(login, password)
        except ArchidektError as exc:
            if exc.kind in ("auth", "forbidden", "contract"):
                # counted against MAX_LINK_FAILURES; the attempted login name is not recorded
                self._audit("archidekt_link_failed", sub=sub, detail={"error": exc.kind})
                raise DeckError("auth", "Archidekt did not accept that username and password.") from exc
            raise DeckError(exc.kind, str(exc), retry_after=archidekt_wait(exc)) from exc
        return session

    async def link(self, sub: str, login: str, password: str) -> dict[str, Any]:
        # One attempt per member at a time: the failure count is checked and the failure recorded
        # under the same lock, so parallel attempts cannot all pass the check before any fails.
        # (One small lock per member who ever linked; members are a known, signed-in set.)
        async with self._link_locks.setdefault(sub, asyncio.Lock()):
            session = await self._link_attempt(sub, login, password)
        # The sign-in took a moment: an admin may have disabled the account, the member may have
        # been removed from the group or deleted their data meanwhile. Store nothing then.
        user = self.db.get_user(sub)
        refused = DeckError(
            "forbidden", "Your account can no longer link Archidekt here; nothing was stored."
        )
        allowed = user is not None and self.settings.grants_access(user.get("groups") or [])
        if not allowed or user.get("disabled_at"):
            raise refused
        if not self.db.save_link(
            sub,
            username=session["username"],
            user_id=session.get("user_id"),
            secret_enc=self._seal(sub, session["access"], session.get("refresh")),
            only_member=True,
        ):
            raise refused
        self._audit("archidekt_linked", sub=sub, detail={"archidekt_username": session["username"]})
        self._drop_backup_deletes_on_relink(sub)
        self.forget_member(sub)
        return self.status(sub)

    def unlink(self, sub: str) -> None:
        self.db.revoke_link(sub)
        self.forget_member(sub)
        self._audit("archidekt_unlinked", sub=sub)

    def status(self, sub: str) -> dict[str, Any]:
        row = self.db.get_link(sub)
        return {
            "linked": row is not None,
            "archidekt_username": row["archidekt_username"] if row else None,
            "linked_at": row["created_at"] if row else None,
            "last_used_at": row["last_used_at"] if row else None,
            "link_expires_at": self._expiry(sub, row["secret_enc"]) if row else None,
            "writes_enabled": self.settings.writes_enabled,
            "account_page": f"{self.settings.public_url}/account",
            **self.mode_info(sub),
        }

    def mode_of(self, sub: str) -> str:
        """The approval mode governing this member (modes.py): their own choice from the Account
        page, or the gateway's default while they have not chosen, never above the cap. Read by
        ``sub`` only, so one member's choice never reaches another's proposals or apps."""
        user = self.db.get_user(sub) or {}
        return modes.effective_mode(
            user.get("approval_mode"),
            default=str(self.live.get("approval_mode_default")),
            cap=str(self.live.get("approval_mode_max")),
        )

    def mode_info(self, sub: str) -> dict[str, Any]:
        mode = self.mode_of(sub)
        return {
            "approval_mode": mode,
            "approval_mode_label": modes.MODE_LABELS[mode],
            "approval_mode_note": modes.mode_help(
                mode,
                max_rows=int(self.live.get("auto_apply_max_rows")),
                big_rows=int(self.live.get("auto_big_edit_rows")),
            )
            + " Change it on the account page.",
        }

    # The stored session is sealed to the member it belongs to: the encrypted blob names its
    # purpose and the member's subject, and is refused under any other member's link, so a
    # ciphertext copied between rows (or any other value sealed with the same key) opens nothing.
    def _seal(self, sub: str, access: str, refresh: str | None) -> str:
        blob = {"p": SESSION_PURPOSE, "s": sub, "access": access, "refresh": refresh}
        return self.fernet.encrypt(json.dumps(blob).encode()).decode()

    def _open(self, sub: str, secret_enc: str, *, legacy: bool = False) -> dict[str, Any] | None:
        """The stored session of ``sub`` (``{"access", "refresh", ...}``), or None when it cannot
        be read or was not sealed to ``sub``. ``legacy`` also accepts a blob from before sealing
        (no purpose or subject), for ``reseal_legacy_links`` only."""
        try:
            secret = json.loads(self.fernet.decrypt(secret_enc.encode()).decode())
        except (InvalidToken, ValueError, TypeError, UnicodeDecodeError):
            return None
        if not isinstance(secret, dict):
            return None
        sealed = secret.get("p") == SESSION_PURPOSE and secret.get("s") == sub
        is_legacy = "p" not in secret and "s" not in secret
        if not (sealed or (legacy and is_legacy)):
            return None
        return secret

    def _expiry(self, sub: str, secret_enc: str) -> int | None:
        """When the stored session stops working: the refresh token's own ``exp`` (Archidekt's
        refresh token is not rotated, so this is fixed at link time), or the access token's when
        no refresh token is stored. None when unknown."""
        secret = self._open(sub, secret_enc)
        if secret is None:
            return None
        refresh, access = secret.get("refresh"), secret.get("access")
        if isinstance(refresh, str) and refresh:
            return jwt_exp(refresh)
        return jwt_exp(access) if isinstance(access, str) else None

    def reseal_legacy_links(self) -> int:
        """At the first start of a sealing gateway, seal every stored session written before
        sealing to the member whose row holds it then; returns how many were resealed. An
        unsealed blob carries no owner, so this cannot tell whether it was moved between rows
        before that start. It runs once (an ``archidekt_sessions_sealed`` audit entry marks it):
        an unsealed blob found at any later start (copied in from an old disk image, or written
        by an older image after a rollback) is not trusted and is deleted, and that member links
        again."""
        first = not self.db.audit_seen(SEALED_MARKER)
        n = dropped = 0
        for row in self.db.active_links():
            secret = self._open(row["sub"], row["secret_enc"], legacy=True)
            if secret is None or "p" in secret:
                continue
            access, refresh = secret.get("access"), secret.get("refresh")
            if first and isinstance(access, str) and access:
                sealed = self._seal(row["sub"], access, refresh if isinstance(refresh, str) else None)
                if self.db.update_link_secret(row["sub"], sealed, only_secret=row["secret_enc"]):
                    n += 1
            elif self.db.revoke_link(row["sub"], only_secret=row["secret_enc"]):
                self.db.audit(
                    "archidekt_link_unsealed", sub=row["sub"], detail={"reason": "session not sealed"}
                )
                dropped += 1
        if first:
            self.db.audit(SEALED_MARKER, detail={"resealed": n})
        if dropped:
            logger.warning("%d stored Archidekt session(s) were not sealed and were deleted", dropped)
        return n

    def purge_expired_links(self, now: float | None = None) -> int:
        """Delete stored sessions that can no longer work (their refresh token has expired), so
        nothing usable or not lingers on the server past that date. Run with the hourly purge."""
        now = time.time() if now is None else now
        n = 0
        for row in self.db.active_links():
            exp = self._expiry(row["sub"], row["secret_enc"])
            if (
                exp is not None
                and exp < now
                and self.db.revoke_link(row["sub"], only_secret=row["secret_enc"])
            ):
                self.db.audit("archidekt_link_expired", sub=row["sub"], detail={"reason": "session expired"})
                n += 1
        return n

    def _token(self, sub: str) -> tuple[str, dict[str, Any]]:
        """The stored access token and the link row. The row carries the decrypted session as
        ``row["_secret"]`` (``{"access", "refresh"}``) for _call's refresh path; it is never
        returned to callers of the service."""
        row = self.db.get_link(sub)
        if row is None:
            raise DeckError(
                "not_linked",
                f"No Archidekt account is linked. Sign in at {self.settings.public_url}/account to link one.",
            )
        secret = self._open(sub, row["secret_enc"])
        access = secret.get("access") if secret is not None else None
        if not isinstance(access, str) or not access:
            raise DeckError("not_linked", "The stored Archidekt session could not be read. Relink.")
        row["_secret"] = secret
        return access, row

    async def _call(self, sub: str, fn: Any, *args: Any, slot_wait: float | None = None) -> Any:
        """``_call_unslotted`` inside the member's Archidekt slot (see ``archidekt_slot``, which
        ``slot_wait`` goes to)."""
        async with self.archidekt_slot(sub, wait=slot_wait):
            return await self._call_unslotted(sub, fn, *args)

    async def _call_unslotted(self, sub: str, fn: Any, *args: Any, background: bool = False) -> Any:
        """Run ``fn(token, *args)`` with the user's Archidekt session. The access token is
        refreshed ahead of time when its ``exp`` is unreadable or within five minutes, and once
        more if Archidekt still answers 401. The new pair is stored under the same link (same
        Fernet, same username and user id). The link is revoked only when no refresh token is
        stored, the refresh itself is rejected, or a freshly refreshed token is rejected too.

        ``background`` (the deck sync, sync.py): nobody is using the link, so it is never revoked
        (a rejected session ends the check as ``not_linked`` and the member's next own request
        decides), it is not marked as used, a refresh is audited as the sync's, and every GET is
        sent once with no retry."""
        token, row = self._token(sub)
        refresh = (row.get("_secret") or {}).get("refresh")
        # The stored session this call works with. Every write below is conditional on the link
        # still holding exactly it, so a refresh or a failure that races an unlink or a relink
        # (possibly to another Archidekt account) never touches the newer link.
        stored = {"secret_enc": row["secret_enc"]}

        def expire(reason: str) -> DeckError:
            if background:
                return DeckError("not_linked", "Archidekt did not accept the stored session.")
            if self.db.revoke_link(sub, only_secret=stored["secret_enc"]):
                self._audit("archidekt_link_expired", sub=sub, detail={"reason": reason})
            return DeckError(
                "not_linked",
                "Archidekt no longer accepts the stored session. "
                f"Relink at {self.settings.public_url}/account.",
            )

        async def refreshed(reason: str) -> str:
            if not isinstance(refresh, str) or not refresh:
                raise expire(f"{reason}, no refresh token")
            try:
                access = await self.client.refresh(refresh)
            except ArchidektError as exc:
                if exc.kind == "auth":
                    raise expire(f"{reason}, refresh rejected") from exc
                # the session could not be refreshed, so the call it was for was never sent
                raise DeckError(exc.kind, str(exc), sent=False, retry_after=archidekt_wait(exc)) from exc
            new_enc = self._seal(sub, access, refresh)
            if not self.db.update_link_secret(sub, new_enc, only_secret=stored["secret_enc"]):
                # Unlinked, revoked or relinked while the refresh was in flight: never resurrect
                # the old link or overwrite a newer one with this session. A link to the same
                # Archidekt account (another request refreshed it first) is simply used as stored.
                try:
                    now_access, now_row = self._token(sub)
                except DeckError:
                    now_row = None
                if now_row is not None and _same_account(now_row, row):
                    stored["secret_enc"] = now_row["secret_enc"]
                    return now_access
                raise DeckError(
                    "not_linked",
                    "Your Archidekt link changed while this request ran. Nothing was sent; try again"
                    f" (the link is managed at {self.settings.public_url}/account).",
                )
            stored["secret_enc"] = new_enc
            detail = {"reason": reason, **({"by": "deck sync"} if background else {})}
            self._audit("archidekt_session_refreshed", sub=sub, detail=detail)
            return access

        exp = jwt_exp(token)
        if exp is None or exp - time.time() < 300:
            token = await refreshed("expiring")
        acting = _acting_sub.set(sub)
        # a caller that asked for no retries itself keeps them off (lists_page.read_counts)
        quiet = no_retry.set(background or no_retry.get())
        try:
            try:
                result = await fn(token, *args)
            except ArchidektError as exc:
                if exc.kind != "auth":
                    raise
                token = await refreshed("rejected")
                result = await fn(token, *args)
        except ArchidektError as exc:
            if exc.kind == "auth":
                raise expire("rejected after refresh") from exc
            # the wait Archidekt asked for reaches the busy page's Retry-After header
            raise DeckError(
                exc.kind, str(exc), sent=exc.sent, retry_after=archidekt_wait(exc), http_status=exc.status
            ) from exc
        finally:
            no_retry.reset(quiet)
            _acting_sub.reset(acting)
        if not background:  # the deck sync's background reads are not the member using their link
            self._touch(sub)
        return result

    def _touch(self, sub: str) -> None:
        """Record that the link was used, at most once a minute: the write (a SQLite transaction)
        is off the path of every other Archidekt call."""
        now = time.monotonic()
        if now - self._touched.get(sub, -TOUCH_LINK_INTERVAL) < TOUCH_LINK_INTERVAL:
            return
        self._touched[sub] = now
        if len(self._touched) > 10_000:
            self._touched = {sub: now}
        self.db.touch_link(sub)

    def _audit(self, event: str, *, sub: str | None = None, detail: dict[str, Any] | None = None) -> None:
        """Audit row that also names the OAuth client acting on this request (id and registered
        name), so the log says which assistant or browser session made each change."""
        client_id = current_client.get()
        # Whether the actor was the member's own browser is recorded from the client id, never
        # from a name: an app may register any name it likes, "browser" included.
        if client_id == BROWSER_CLIENT:
            detail = {**(detail or {}), "origin": "browser"}
        elif client_id == ADMIN_CLIENT:
            detail = {**(detail or {}), "origin": "admin", "by": "admin"}
        elif client_id:
            name = self.db.client_name(client_id)
            detail = {
                **(detail or {}),
                "origin": "app",
                **({"client_name": clean_text(name)[:80]} if name else {}),
            }
        self.db.audit(event, sub=sub, client_id=client_id, detail=detail)

    # -- reads ----------------------------------------------------------------
    async def list_decks(self, sub: str, *, folder_names: bool = False) -> list[dict[str, Any]]:
        """The member's decks, from the in-memory list when it is fresh (DECK_LIST_FRESH) or stale
        with a refresh running (DECK_LIST_STALE), else read from Archidekt now. Every write the
        gateway sends for the member drops the list first, so the next read is live again.
        ``folder_names``: see ``_deck_rows``."""
        rows = await self.list_decks_quick(sub, wait=None, folder_names=folder_names)
        assert rows is not None
        return rows

    async def _deck_rows(
        self, sub: str, *, wait: float | None, prefer_fresh: bool = False, folder_names: bool = False
    ) -> DeckList | None:
        """Every deck of the linked account from the member cache (or Archidekt), the gateway's
        backup copies marked ``backup: True``: those in the backup folder, those named like one,
        and those a snapshot here records as its copy (``db.backup_copies``). The list keeps the
        listing's ``complete`` and ``unreadable`` (see archidekt.DeckList); a list of unknown origin
        is incomplete. A list with entries whose owner could not be read is kept only briefly
        (DECK_LIST_PARTIAL): My decks says so with Try again, which reads Archidekt again
        through ``reread_deck_list``.

        ``folder_names`` (list_my_decks): ``folder`` is filled in on rows that carry only the
        folder's id, from the same one read of the folder tree the backup marks use; when that
        tree can't be read now the rows keep ``folder`` None and the list's
        ``folder_names_unread`` is True."""
        _token, row = self._token(sub)  # not linked: raise before touching the cache
        folder = self.backup_folder  # with backups on or off

        async def fetch() -> list[dict[str, Any]]:
            return await self._call(
                sub,
                lambda token, *_: self.client.list_decks(
                    token, row["archidekt_username"], row.get("archidekt_user_id"), backup_folder=folder
                ),
            )

        rows = await self.deck_lists.get(sub, fetch, wait=wait, prefer_fresh=prefer_fresh)
        if rows is None:
            return None
        # Archidekt's owner listing can leave a folder's name out and give only its id
        unnamed = any(d.get("folder_id") is not None and not d.get("folder") for d in rows)
        info = None
        if unnamed:
            info = await self._folder_tree_now(sub, wait=wait)
        out = self._marked(sub, rows, self._backup_folder_ids(sub, info))
        if folder_names and unnamed:
            if info is None:
                out.folder_names_unread = True
            else:
                names = {f["id"]: f["name"] for f in info["folders"] if f["depth"] > 0}
                for d in out:
                    if d.get("folder_id") is not None and not d.get("folder"):
                        d["folder"] = names.get(d["folder_id"])
        return out

    def _marked(
        self, sub: str, rows: list[dict[str, Any]], backup_ids: set[int], last_read: float | None = None
    ) -> DeckList:
        """``rows`` as fresh dicts, the backup copies marked: those in a folder of ``backup_ids``
        and those a snapshot here records (see ``_deck_rows``)."""
        unreadable = getattr(rows, "unreadable", 0)
        known = self.db.backup_copies(sub)
        out = []
        for d in rows:
            d = dict(d)
            if d.get("folder_id") in backup_ids:
                d["backup"] = True  # in the backup folder, which the listing gave by id only
            if d["id"] in known:
                d["backup"] = True
                d["backup_of"] = known[d["id"]]["deck_id"]
                d["snapshot_id"] = known[d["id"]]["snapshot_id"]
            out.append(d)
        return DeckList(
            out, complete=getattr(rows, "complete", False) is True, unreadable=unreadable, last_read=last_read
        )

    def last_deck_list(self, sub: str) -> DeckList | None:
        """For My decks and Home when Archidekt cannot be asked (``is_busy``): the member's deck
        list as last read (at most DECK_LIST_LAST old), backup copies left out, its ``last_read``
        set; None when there is none. Never for an assistant or for judging what is gone: a page
        shows it with a notice saying how old it is."""
        self._token(sub)  # not linked (any more): nothing to show
        held = self.deck_lists.last(sub, DECK_LIST_LAST)
        if held is None:
            return None
        tree = self.folder_trees.last(sub, DECK_LIST_LAST)  # never a read: Archidekt is busy
        backup_ids = (
            {f["id"] for f in self._flatten_folders(tree[1]).get("folders", []) if f.get("backup")}
            if tree is not None and tree[1] is not None
            else set()
        )
        rows = self._marked(sub, held[1], backup_ids, last_read=held[0])
        return DeckList(
            [d for d in rows if not d.get("backup")], complete=False, unreadable=0, last_read=held[0]
        )

    async def _folder_tree_now(self, sub: str, *, wait: float | None) -> dict[str, Any] | None:
        """The member's folder tree flattened, from the member cache or read now, for naming deck
        rows' folders; a best-effort read, so outside a page it takes one of the member's
        Archidekt slots only when one is free (no wait behind their other work). None when it
        can't be read now or ``wait`` ran out; never the tree as last read, which may be out of
        date."""
        slot_wait = None if page_turn.get() else 0.0

        async def fetch() -> dict[str, Any]:
            return await self._call(sub, self.client.folder_tree, slot_wait=slot_wait)

        try:
            tree = await self.folder_trees.get(sub, fetch, wait=wait)
        except DeckError:
            return None
        return None if tree is None else self._flatten_folders(tree)

    def _backup_folder_ids(self, sub: str, info: dict[str, Any] | None) -> set[int]:
        """The ids of the backup folder and the folders inside it, for deck rows that carry their
        folder's id but not its name (archidekt.list_decks recognises the backup folder by name):
        from ``info`` (``_folder_tree_now``), else from the tree as last read (the backup folder's
        id doesn't change). Empty when neither is there (the name check and the snapshots' own
        records still apply)."""
        if info is None:
            held = self.folder_trees.last(sub, DECK_LIST_LAST)
            info = self._flatten_folders(held[1]) if held is not None and held[1] is not None else None
        return {f["id"] for f in (info or {}).get("folders", []) if f.get("backup")}

    async def mark_packages(self, sub: str, rows: list[dict[str, Any]]) -> bool:
        """Mark the member's card packages in a copy of their deck list (``package``: the settings
        in words, None on a plain deck), from the Packages page's own listing, and add any package
        the deck listing left out. Read only when asked (list_my_decks, /api/decks/mine?packages=1),
        never on the deck list's own cold load, which stays one Archidekt listing. False when the
        package listing couldn't be read: the rows are left unmarked and the list as it was."""
        from . import packages

        _token, link = self._token(sub)
        username, user_id = link["archidekt_username"], link.get("archidekt_user_id")

        for d in rows:
            d.setdefault("package", None)
        found: list[dict[str, Any]] = []
        try:
            for page in range(1, MAX_PACKAGE_PAGES + 1):
                body = await self._call(
                    sub,
                    lambda token, *_, p=page: self.client.packages_list(token, owner=username, page=p),
                )
                found += [
                    r for r in body["results"] if isinstance(r, dict) and str(r.get("id") or "").isdigit()
                ]
                if not body.get("next"):
                    break
        except (DeckError, ArchidektError) as exc:
            logger.info("deck list: card packages not read (%s)", exc)
            return False
        by_id = {str(d["id"]): d for d in rows}
        for r in found:
            block = r.get("cardPackage") if isinstance(r.get("cardPackage"), dict) else {}
            words = packages.text(block) if block else None
            have = by_id.get(str(r["id"]))
            if have is not None:
                have["package"] = words
            elif _owned_by(r.get("owner"), username, user_id):
                extra = list_row(r)
                extra.update(owner=username, package=words)
                rows.append(extra)
        rows.sort(key=lambda d: d.get("updated_at") or "", reverse=True)
        return True

    async def list_decks_quick(
        self, sub: str, *, wait: float | None = None, folder_names: bool = False
    ) -> list[dict[str, Any]] | None:
        """``list_decks`` that gives up waiting for a cold read after ``wait`` seconds and answers
        None; the read carries on and lands in the cache for /api/decks/mine. ``wait=None``
        waits for it. The list comes back as fresh dicts, so a caller may sort or add to it. The
        gateway's backup copies are left out (``backup_copies`` lists them, History shows them).
        It keeps the listing's ``complete`` and ``unreadable`` (archidekt.DeckList), and
        ``folder_names_unread`` (see ``_deck_rows``)."""
        rows = await self._deck_rows(sub, wait=wait, folder_names=folder_names)
        if rows is None:
            return None
        out = DeckList(
            [d for d in rows if not d.get("backup")], complete=rows.complete, unreadable=rows.unreadable
        )
        out.folder_names_unread = rows.folder_names_unread
        return out

    async def backup_copies(
        self,
        sub: str,
        *,
        deck_id: str | None = None,
        wait: float | None = None,
        prefer_fresh: bool = False,
    ) -> list[dict[str, Any]] | None:
        """The gateway's backup copies on Archidekt (decks kept out of the member's lists), newest
        first; with ``deck_id``, only the copies of that deck (those a snapshot here records, or
        whose name starts with that deck's name). None when the list is cold and ``wait`` ran out.
        A copy deleted on Archidekt is not in the list, so it is not listed here either."""
        rows = await self._deck_rows(sub, wait=wait, prefer_fresh=prefer_fresh)
        if rows is None:
            return None
        copies = [d for d in rows if d.get("backup")]
        if deck_id:
            names = {str(d.get("name", "")) for d in rows if d["id"] == str(deck_id)}

            def of_this_deck(d: dict[str, Any]) -> bool:
                if d.get("backup_of"):
                    return d["backup_of"] == str(deck_id)
                return any(str(d.get("name", "")).startswith(n + " (backup ") for n in names)

            copies = [d for d in copies if of_this_deck(d)]
        return copies

    async def on_archidekt(self, sub: str, *, wait: float | None = None) -> dict[str, Any] | None:
        """What the member's deck list (the cached one, no extra request) says is on Archidekt:
        ``{"ids", "read_at", "account_id", "username", "linked_at"}``: every deck and backup copy
        id, when the list was read, and the Archidekt account it is of and when it was linked. None
        when the list cannot tell that something is gone: still cold, read by username (which may
        leave private decks out), or not ``complete`` (archidekt.DeckList: cut off at
        MAX_LIST_PAGES pages, an entry whose owner could not be read or was someone else, or a
        count that moved between pages). None means "unknown": nothing is marked gone. A list
        older than DECK_LIST_FRESH is refreshed first, waiting up to ``wait`` for it."""
        return self._listed(sub, await self._deck_rows(sub, wait=wait, prefer_fresh=True))

    def _listed(self, sub: str, rows: Any) -> dict[str, Any] | None:
        """``on_archidekt``'s answer for the deck list ``rows`` (the cached list's rows)."""
        _token, link = self._token(sub)
        read_at = self.deck_lists.fetched_at(sub)
        if rows is None or read_at is None or not link.get("archidekt_user_id"):
            return None
        if not rows.complete or len(rows) >= MAX_LIST_PAGES * 50:
            return None
        return {
            "ids": {str(d["id"]) for d in rows},
            "read_at": read_at,
            # the list is this account's alone: history of an account linked before is not judged
            "account_id": str(link["archidekt_user_id"]),
            "username": str(link.get("archidekt_username") or ""),
            "linked_at": int(link.get("created_at") or 0),
        }

    def backup_gone(self, sub: str, backup_id: str) -> bool:
        """Whether the member's deck list as held now (no request) says the backup copy
        ``backup_id`` is gone from Archidekt, judged as History marks it (``mark_gone``); False
        when it can't tell."""
        copy = self.db.backup_copies(sub).get(str(backup_id))
        if copy is None:
            return False
        snaps = self.db.list_snapshots(sub, 50, deck_id=str(copy["deck_id"]))
        snap = next((x for x in snaps if str(x.get("backup_deck_id") or "") == str(backup_id)), None)
        rows, _fresh = self.deck_lists.peek(sub)
        try:
            listed = self._listed(sub, rows)
        except DeckError:  # not linked any more
            return False
        return snap is not None and bool(mark_gone([snap], listed)[0].get("backup_gone"))

    def decks_fetched_at(self, sub: str) -> float | None:
        """When the cached deck list was read from Archidekt (epoch seconds), None when none is."""
        return self.deck_lists.fetched_at(sub)

    def reread_deck_list(self, sub: str) -> None:
        """Try again on the notice that the deck list has unreadable entries: forget the member's
        list when the one kept has entries Archidekt left unreadable, so the next page reads
        Archidekt again. A list that read fine is kept, so an old Try again link costs Archidekt
        nothing."""
        rows, _state = self.deck_lists.peek(sub)
        if getattr(rows, "unreadable", 0):
            self.deck_lists.drop(sub)

    async def get_deck(self, sub: str, deck_id: str) -> Deck:
        deck_id = _clean_deck_id(deck_id)
        deck: Deck = await self._call(sub, self.client.get_deck, deck_id)
        return deck

    async def get_own_deck(self, sub: str, deck_id: str) -> Deck:
        """Fetch with the user's session and insist the linked account owns the deck."""
        deck = await self.get_deck(sub, deck_id)
        _token, link = self._token(sub)
        owned = owns_deck(deck, link)
        if owned is None:
            raise DeckError("contract", "Archidekt did not say who owns this deck, so it cannot be edited.")
        if not owned:
            raise DeckError(
                "forbidden",
                f"Deck {deck.id} belongs to {deck.owner or 'another Archidekt account'}, not to your "
                "linked account. "
                "Use get_deck to read it.",
            )
        return deck

    async def get_any_deck(self, sub: str | None, deck_ref: str, *, fresh: bool = False) -> Deck:
        """Read a deck. A member with a linked account reads with their session first, which is
        how Archidekt reports the deck as that person sees it (private decks, the copies they own
        of each card, their vote and bookmark); when that fails, or there is no link, the deck is
        read anonymously (public or unlisted), through the client's shared read cache unless
        ``fresh`` (the deck page's Reload) asks Archidekt again."""
        deck_id = _clean_deck_id(deck_ref)
        linked: DeckError | None = None
        if sub and self.db.get_link(sub):
            try:
                return await self.get_deck(sub, deck_id)
            except DeckError as exc:
                if exc.kind not in ("not_found", "auth", "forbidden", "not_linked"):
                    raise
                if exc.kind == "not_linked":
                    linked = exc
        async with self.archidekt_slot(sub):
            try:
                if fresh:
                    deck = await self.client.get_deck(None, deck_id, fresh=True)
                else:
                    deck = await self.client.get_deck(None, deck_id)
                if linked is not None and sub and self.db.get_link(sub) is None:
                    # read without the link this request dropped: the member still hears of it (a
                    # copy, as the client's shared read cache may hold this deck)
                    return dataclasses.replace(deck, link_notice=str(linked))
                return deck
            except ArchidektError as exc:
                if linked is not None and exc.kind in ("not_found", "auth", "forbidden"):
                    # the member's link stopped working on this request: that, and how to link
                    # again, is what they need to hear, not why the read without it failed
                    raise DeckError(linked.kind, str(linked)) from exc
                raise DeckError(exc.kind, str(exc), retry_after=archidekt_wait(exc)) from exc

    # -- the deck sync's reads (sync.py) ----------------------------------------
    def archidekt_busy(self) -> bool:
        """Whether any member's Archidekt work is running or waiting, or an apply is under way:
        the deck sync waits for that to finish, so it never holds up a member or an assistant."""
        return bool(self._archidekt_in_flight) or bool(self._running) or self.client.pacer.busy()

    def being_made(self, deck_id: str) -> str | None:
        """The notice for a deck whose new-deck apply is still putting its cards in, else None."""
        made = self._being_made.get(str(deck_id))
        if made is None:
            return None
        progress, entries = made
        sent = int(progress.get("sent_entries") or 0)
        cards = sum(int(e["modifications"]["quantity"]) for e in entries[:sent])
        total = sum(int(e["modifications"]["quantity"]) for e in entries)
        return f"This deck is still being made: {cards} of {total} cards added so far."

    def refuse_while_made(self, deck_id: str) -> None:
        """Refuse a change to a deck that is still being made (its apply would race the edit)."""
        notice = self.being_made(deck_id)
        if notice is not None:
            raise DeckError("deck_being_made", f"{notice} Try again once it is done.")

    def deck_busy(self, sub: str, deck_id: str) -> bool:
        """Whether an apply (or restore) on this deck is running: the sync leaves the deck alone
        until it ends, so the gateway's own write is never read halfway."""
        lock = self._deck_locks.get(f"{sub}:{deck_id}")
        return (lock is not None and lock.locked()) or self.db.deck_applying(sub, deck_id)

    async def sync_deck_list(self, sub: str, *, wait: float = 120.0) -> list[dict[str, Any]] | None:
        """The member's deck list for the deck sync: the cached one while it is fresh, else one
        listing request (shared with a page's read already under way). Read-only, outside the
        member's own Archidekt slot, and not counted as the member using their link; it spends
        from their budget (:meth:`_sync_spend`). None when the read takes longer than ``wait`` (it
        carries on and fills the cache)."""
        value, state = self.deck_lists.peek(sub)
        if state == "fresh":
            return [dict(d) for d in value]
        self._sync_spend(sub)
        _token, row = self._token(sub)
        folder = self.backup_folder  # with backups on or off

        async def fetch() -> list[dict[str, Any]]:
            return await self._call_unslotted(
                sub,
                lambda token, *_: self.client.list_decks(
                    token, row["archidekt_username"], row.get("archidekt_user_id"), backup_folder=folder
                ),
                background=True,
            )

        # A page arriving meanwhile shares this read, read on the sync's terms (no retry, no
        # unlink); when it fails, the page reads once more on its own terms (MemberCache.get).
        rows = await self.deck_lists.refreshed(sub, fetch, wait=wait)
        return None if rows is None else [dict(d) for d in rows]

    async def sync_read_deck(self, sub: str, deck_id: str) -> Deck:
        """One live read of the member's deck for the deck sync (never cached, like every read
        with the member's session); outside their slot, spending from their budget, like
        sync_deck_list."""
        deck_id = _clean_deck_id(deck_id)
        self._sync_spend(sub)
        deck: Deck = await self._call_unslotted(sub, self.client.get_deck, deck_id, background=True)
        return deck

    async def sync_read_any_deck(self, sub: str, deck_id: str) -> Deck:
        """``sync_read_deck`` for a deck that is not the member's own (the source of a waiting
        copy): read as ``get_any_deck`` reads it (the member's session first, else
        anonymously), so it compares with the deck the proposal was made from; outside the
        member's slot, spending from their budget. Each request is sent once, never retried."""
        deck_id = _clean_deck_id(deck_id)
        self._sync_spend(sub)
        try:
            deck: Deck = await self._call_unslotted(sub, self.client.get_deck, deck_id, background=True)
            return deck
        except DeckError as exc:
            if exc.kind not in ("not_found", "auth", "forbidden", "not_linked"):
                raise
        quiet = no_retry.set(True)
        try:
            return await self.client.get_deck(None, deck_id, fresh=True)
        except ArchidektError as exc:
            raise DeckError(exc.kind, str(exc), retry_after=archidekt_wait(exc)) from exc
        finally:
            no_retry.reset(quiet)

    def proposal_risk(self, sub: str, row: dict[str, Any]) -> tuple[str, str]:
        """(risk, why) of a proposal as the pages show it: its tier from its kind and review rows
        under the live settings, raised to high when another app changed it after it was made.
        ``row`` is a whole stored proposal (get_proposal). A waiting one is worked out on every
        read; a closed one keeps what was stored when it closed (``stored_risk``)."""
        kind = row.get("kind") or "edit"
        risk, why = modes.risk_of(kind, row.get("rows"), max_rows=int(self.live.get("auto_apply_max_rows")))
        if risk not in modes.MEMBER_ONLY and self.rewritten_by_another_app(sub, row):
            return "high", "was changed by another app after it was made"
        return risk, why

    @staticmethod
    def unjudged(row: dict[str, Any]) -> bool:
        """An edit with no review rows (stored before they existed): nothing to judge its risk by.
        It still waits for the member's own press, as a high-risk one does, but shows no tier."""
        return (row.get("kind") or "edit") in ("edit", "collection") and row.get("rows") is None

    def stored_risk(self, row: dict[str, Any]) -> str:
        """The risk tier stored on a proposal when it closes (Database.risk_source). A closed edit
        with no review rows (stored before they existed) has nothing to judge it by: it keeps no
        tier (``modes.UNJUDGED``) rather than reading High for want of evidence."""
        if self.unjudged(row):
            return modes.UNJUDGED
        return self.proposal_risk(str(row.get("owner_sub") or ""), row)[0]

    def _assistant_policy(self, sub: str, row: dict[str, Any]) -> tuple[str, str, str, bool]:
        """(risk, why, mode, allowed): the proposal's risk tier and reason, the member's approval
        mode, and whether that mode lets the assistant apply it itself. On top of the tiers
        (modes.py): auto mode still waits for the member's press on a restore, a new deck, a
        commander change or a big edit (more rows than ``auto_big_edit_rows``); and in
        every mode an assistant never applies a proposal another app changed after it was made
        or one that is held."""
        kind = row.get("kind", "edit")
        risk, why = modes.risk_of(kind, row.get("rows"), max_rows=int(self.live.get("auto_apply_max_rows")))
        mode = self.mode_of(sub)
        # Any version another app wrote, not only the last (one that
        # ChatGPT rewrote and Claude changed again still had another app's hand in it)
        if risk not in modes.MEMBER_ONLY and self.rewritten_by_another_app(sub, row):
            return "high", "was changed by another app after it was made", mode, False
        if risk not in modes.MEMBER_ONLY and self.press_reason(sub, row):
            # it waits for the member's own press whatever the mode; its risk tier stays what it
            # is, and press_reason says why
            return risk, why, mode, False
        wait = modes.auto_waits(
            kind,
            row.get("rows"),
            big_rows=int(self.live.get("auto_big_edit_rows")),
            changes=row.get("changes") if isinstance(row.get("changes"), dict) else None,
        )
        if mode == "auto" and wait and risk not in modes.MEMBER_ONLY:
            why = wait  # what the assistant is told: why auto mode still waits for the user
        return risk, why, mode, modes.assistant_may_apply(mode, risk, auto_wait=wait)

    def press_reason(self, sub: str, row: dict[str, Any]) -> str | None:
        """Why this waiting note needs the member's own press in every mode, or None: a hold and a
        first step not applied; another app's change taken in (a safe default), a change that
        would replace this one and a replaced change applied on its own. The member may still
        press."""
        if row.get("state") != "pending":
            return None
        replaced = next(
            (c for c in board.hold_causes(row.get("status_reason")) if c.startswith("replaces:")), None
        )
        if row.get("status") == "held" and replaced:
            other = replaced.split(":", 2)[1]
            no = self.db.proposal_board_numbers(sub, [other]).get(other)
            return f"would replace {f'#{no}' if no else 'a change'}, which was applied"
        if self._takes_over_another_app(sub, row):
            return "includes or replaces a change another app made or changed"
        later = self._replaced_by_waiting(sub, row["id"])
        if later is not None:
            return f"would be replaced by {board.number(later)}, which also waits"
        if row.get("status") == "held":
            return "is held because a change it depends on didn't go through"
        first = self._first_step_not_applied(sub, row["id"])
        if first is not None:
            if first.get("board_no"):
                return f"waits for #{first['board_no']}, which is not applied"
            return "waits for a change it depends on, which is not applied"
        return None

    def _replaced_by_waiting(self, sub: str, proposal_id: str) -> dict[str, Any] | None:
        return board.replaced_by_waiting(self.db, sub, proposal_id)

    def replace_links(self, sub: str, row: dict[str, Any]) -> dict[str, Any]:
        """What the member reads about a replace that waits."""
        note = board.replace_note(self.db, sub, row)
        return {"replace_note": note} if note else {}

    def _takes_over_another_app(self, sub: str, row: dict[str, Any]) -> bool:
        """Whether this note replaces (or was combined from) a note that another app made or
        changed."""
        creator = row.get("created_by_client")
        for link in self.db.proposal_links(sub, [row["id"]]):
            if link["proposal_id"] == row["id"] and link["kind"] == "replaces":
                if _made_by_another_app(self.db.get_proposal(link["other_id"], sub) or {}, creator):
                    return True
        return False

    def _first_step_not_applied(self, sub: str, proposal_id: str) -> dict[str, Any] | None:
        return board.first_step_not_applied(self.db, sub, proposal_id)

    def _status_fields(self, sub: str, row: dict[str, Any]) -> dict[str, Any]:
        """A waiting proposal's status and its reason in words: "updated" (re-checked, still the
        change that was reviewed), "needs_look" (it would now do something else) or "held" (a
        proposal it depends on was rejected, withdrawn or failed)."""
        status = row.get("status")
        text = board.status_text(self.db, sub, row)
        return {"status": status, "status_reason": text, "status_at": row.get("status_at")}

    def _forget_page_decks(self) -> None:
        self._page_decks.clear()
        self._page_decks_gen += 1

    def _forget_page_deck(self, deck_id: str) -> None:
        for key in [k for k in self._page_decks if k[1] == deck_id]:
            del self._page_decks[key]
        if len(self._page_decks_dropped) >= PAGE_DECKS_MAX:
            self._page_decks_dropped.clear()
            self._page_decks_gen += 1  # forgetting the drops forgets the reads under way too
        self._page_decks_dropped[deck_id] = time.monotonic()

    def _keep_page_deck(self, key: tuple[str, str], deck: Deck, gen: int, started: float) -> None:
        """Keep what a view read, unless a write to that deck (or any write to all) came meanwhile."""
        if self.page_deck_seconds <= 0 or gen != self._page_decks_gen:
            return
        if self._page_decks_dropped.get(key[1], -1.0) >= started:
            return
        if key not in self._page_decks and len(self._page_decks) >= PAGE_DECKS_MAX:
            oldest = min(self._page_decks, key=lambda k: self._page_decks[k][0])
            self._page_decks.pop(oldest, None)
        kept = pickle.dumps(deck)
        self._page_decks[key] = (time.monotonic(), kept)
        if key not in self._page_last and len(self._page_last) >= PAGE_DECKS_MAX:
            oldest = min(self._page_last, key=lambda k: self._page_last[k][0])
            self._page_last.pop(oldest, None)
        self._page_last[key] = (time.time(), kept)

    async def _read_page_deck(self, sub: str, deck_ref: str, key: tuple[str, str], *, fresh: bool) -> Deck:
        gen, started = self._page_decks_gen, time.monotonic()
        # A Reload skips the client's shared 60-second read cache too (decks read without the
        # member's own link: someone else's public deck, or any deck with no account linked).
        deck = await (
            self.get_any_deck(sub, deck_ref, fresh=True) if fresh else self.get_any_deck(sub, deck_ref)
        )
        if sub:
            self.recheck_waiting(sub, deck)  # a deck page view checks its waiting proposals
        self._keep_page_deck(key, deck, gen, started)
        return deck

    def _refresh_page_deck(
        self, sub: str, deck_ref: str, key: tuple[str, str], *, fresh: bool = False
    ) -> None:
        """Read a kept deck page again after it was shown: one such read per deck page at a time,
        in its own context (nobody's request, so no request's Archidekt slot or timing). What it
        found waits in ``_page_checked`` for the page (``page_check``)."""
        running = self._page_refreshing.get(key)
        if running is not None and not running.done():
            return
        shown = self._page_decks.get(key)
        self._page_checked.pop(key, None)

        async def run() -> None:
            try:
                deck = await self._read_page_deck(sub, deck_ref, key, fresh=fresh)
            except DeckError as exc:
                if exc.kind not in ("unavailable", "rate_limited"):
                    # gone, made private, or the link changed: the next view reads it itself
                    self._page_decks.pop(key, None)
                # Archidekt can't be asked: the copy on show stays (the page says how old it is)
                self._page_checked[key] = {"changed": False, "failed": True}
                logger.info("background re-read of a deck page failed: %s", exc.kind)
                return
            changed = shown is None or pickle.dumps(deck) != shown[1]  # anything the page shows
            if len(self._page_checked) >= PAGE_DECKS_MAX:
                self._page_checked.clear()
            self._page_checked[key] = {"changed": changed, "failed": False}

        task = asyncio.get_running_loop().create_task(run(), context=contextvars.Context())
        self._page_refreshing[key] = task
        task.add_done_callback(
            lambda t: self._page_refreshing.get(key) is t and self._page_refreshing.pop(key)
        )

    async def page_deck(self, sub: str, deck_ref: str, *, fresh: bool = False) -> Deck:
        """The deck of ``page_deck_view``, for a page that shows no notice about it."""
        deck, _how = await self.page_deck_view(sub, deck_ref, fresh=fresh)
        return deck

    async def page_deck_view(
        self, sub: str, deck_ref: str, *, fresh: bool = False
    ) -> tuple[Deck, dict[str, Any]]:
        """``get_any_deck`` for showing a deck page, and how it was got. Switching between the
        deck's views, or coming back to it, within PAGE_DECK_SECONDS shows the deck as last read
        instead of asking Archidekt again (each ask waits its turn in the pacer shared by every
        member); up to PAGE_DECK_STALE it shows the deck as last read at once and reads it again in
        the background (``{"checking": True}``; ``page_check`` says what that read found). A write
        the gateway sends to the deck forgets every such read of it (any other write forgets them
        all). ``fresh`` (the browser's Reload, the app's pull) shows a read younger than
        PAGE_DECK_RELOAD as it is, and an older one at once while it is read again
        (``{"checking": True, "reload": True}``), skipping the client's shared read cache.
        When the deck has to be read and Archidekt can't be asked (busy, paused, unreachable), the
        member's last good read of it within PAGE_DECK_LAST comes back with ``{"held_at": wall
        time of that read, "refused": the error}``, for a read-only page that says how old it is.
        Only for showing a deck: proposals and applies read it themselves."""
        ttl = self.page_deck_seconds
        key = (sub, _clean_deck_id(deck_ref))
        hit = self._page_decks.get(key)
        if hit is not None and ttl > 0:
            age = time.monotonic() - hit[0]
            if age < (max(ttl, PAGE_DECK_RELOAD) if fresh else ttl):
                return pickle.loads(hit[1]), {}  # a copy of its own: the page may change it
            if age < PAGE_DECK_STALE:
                self._refresh_page_deck(sub, deck_ref, key, fresh=fresh)
                return pickle.loads(hit[1]), {"checking": True, "reload": fresh, "read_at": time.time() - age}
        try:
            return await self._read_page_deck(sub, deck_ref, key, fresh=fresh), {}
        except DeckError as exc:
            last = self._page_last.get(key) if exc.kind in ("unavailable", "rate_limited") else None
            if last is None or time.time() - last[0] > PAGE_DECK_LAST:
                raise
            return pickle.loads(last[1]), {"held_at": last[0], "refused": exc}

    async def page_check(self, sub: str, deck_ref: str, *, wait: float) -> dict[str, bool] | None:
        """What the background re-read of a deck page found (``{"changed", "failed"}``), waiting
        up to ``wait`` seconds for it; None when none ran for this member's view of the deck."""
        key = (sub, _clean_deck_id(deck_ref))
        running = self._page_refreshing.get(key)
        if running is not None and not running.done():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(asyncio.shield(running), wait)
        return self._page_checked.pop(key, None)

    async def research_deck_text(self, sub: str, deck_ref: str) -> str:
        """The decklist text a research tool (goldfish_annotate) is given in place of a deck id or
        link: read as the member sees the deck (get_any_deck), so their own private decks work,
        where Mystic Forge reads Archidekt anonymously. Commander first, as the simulators expect;
        double-faced cards by their front face, the one name Scryfall's collection lookup finds.
        Archidekt answers "not found" both for a deck that does not exist and for someone else's
        private deck, so the refusal says which of those is possible for this member."""
        try:
            deck = await self.get_any_deck(sub, deck_ref)
        except DeckError as exc:
            if exc.kind != "not_found":
                raise
            deck_id = _clean_deck_id(deck_ref)
            link = self.db.get_link(sub)
            if link:
                raise DeckError(
                    "not_found",
                    f"Archidekt shows no deck {deck_id} to your linked account "
                    f"({link['archidekt_username']}) or to the public: the id is wrong, or the deck is "
                    "someone else's private deck, which only its owner can read.",
                ) from exc
            raise DeckError(
                "not_found",
                f"Archidekt has no public deck {deck_id}: the id is wrong, or the deck is private. If it "
                f"is your own private deck, link your Archidekt account at {self.settings.public_url}"
                "/account and try again.",
            ) from exc
        if not any(deck.is_commander(c) for c in deck.cards):
            raise DeckError(
                "invalid",
                f"Deck {deck.id} has no card in a Commander (premier) category, so its commander is not "
                "known. Put the commander in that category, or paste the list with the commander first.",
            )
        return front_faces(deck_to_text(deck))

    async def search_decks(self, sub: str | None, **query: Any) -> dict[str, Any]:
        """Search Archidekt's public decks anonymously (see ArchidektClient.search_decks). Counts
        against the member's Archidekt budget like any other read."""
        async with self.archidekt_slot(sub):
            try:
                return await self.client.search_decks(**query)
            except ArchidektError as exc:
                raise DeckError(exc.kind, str(exc), retry_after=archidekt_wait(exc)) from exc

    async def user_profile(self, sub: str | None, username: str) -> dict[str, Any] | None:
        username = re.sub(r"[^A-Za-z0-9_.@ -]", "", str(username or "")).strip()[:60]
        if not username:
            raise DeckError("invalid", "give an Archidekt username")
        async with self.archidekt_slot(sub):
            try:
                return await self.client.user_profile(username)
            except ArchidektError as exc:
                raise DeckError(exc.kind, str(exc), retry_after=archidekt_wait(exc)) from exc

    # -- proposals ------------------------------------------------------------
    @_dedupe
    async def propose(
        self,
        sub: str,
        deck_id: str,
        raw_changes: Any,
        *,
        row_flags: bool = False,
        source_row: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """``row_flags`` only for the deck page's own save (parse_changes). ``source_row``: a line
        put first that says where the changes come from (a card package's, propose_package)."""
        deck_id = _clean_deck_id(deck_id)
        changes = parse_changes(raw_changes, row_flags=row_flags)
        self._room_for_proposal(sub)
        deck = await self.get_own_deck(sub, deck_id)
        if (kept := one_click.get()) is not None:  # for the apply that follows at once (D-01)
            kept["read"] = (time.monotonic(), deck.id, pickle.dumps(deck))
        self.recheck_waiting(sub, deck)  # the deck's other waiting proposals, at no cost
        _before, _after, rows = plan_rows(deck, changes)
        await self._refuse_unknown_cards(deck, changes)
        rows, legality = await self._judge_rules(deck, changes, rows)
        if source_row is not None:
            rows = [source_row, *rows]
        pid = secrets.token_urlsafe(12)
        self._save_proposal(
            {
                "id": pid,
                "owner_sub": sub,
                "deck_id": deck.id,
                "deck_name": deck.name,
                "baseline_fingerprint": deck.fingerprint(),
                "changes": [c.as_dict() for c in changes],
            },
            rows,
        )
        self._audit("proposal_created", sub=sub, detail={"proposal_id": pid, "deck_id": deck.id})
        out = self._board_note(sub, self.describe(sub, pid), deck.id, rows, pid)
        return {**out, **self._rules_note(changes, rows, legality)}

    async def _judge_rules(
        self, deck: Deck, changes: list[Change], rows: list[dict[str, Any]]
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """The review rows with the format rules the change would break that the deck keeps now
        marked on them (``breaks``, which makes the proposal high risk), and those rules in words.
        The deck as it would be is checked with deck_stats.deck_checks; an added card is looked up
        on Scryfall first (one request) for what its legality turns on. A lookup that fails judges
        the added cards by name only: their colors and legality are then not checked."""
        from . import deck_stats
        from .reports import deck_after

        if not deck.format:
            return rows, []
        after = deck_after(deck, changes)
        new = {c.name for c in after.cards if c.relation_id is None}
        if new and self.card_collection is not None:
            try:
                found, _missing = await self.card_collection([{"name": n} for n in sorted(new)][:75])
            except Exception:
                logger.warning("card lookup for a proposal's legality failed", exc_info=True)
                found = []
            facts: dict[str, dict[str, Any]] = {}
            for card in found:
                full = str(card.get("name") or "")
                facts.setdefault(full.casefold(), card)
                facts.setdefault(front_face(full).casefold(), card)
            words = any(len(x) > 1 for c in deck.cards if deck.is_commander(c) for x in c.color_identity)
            after = dataclasses.replace(
                after,
                cards=[
                    _with_card_facts(c, facts.get(c.name.casefold()), words) if c.relation_id is None else c
                    for c in after.cards
                ],
            )
        problems = _new_problems(deck_stats.compute(deck)["checks"], deck_stats.compute(after)["checks"])
        if not problems:
            return rows, []
        return mark_breaks(rows, problems), [p["problem"] for p in problems]

    @staticmethod
    def _rules_note(changes: list[Change], rows: list[dict[str, Any]], legality: list[str]) -> dict[str, Any]:
        """What a propose or amend result adds: ``legality_after`` (the rules the change would
        break, already in the risk) and ``unchanged`` (changes that leave their card as it is), each
        with a warning to tell the user."""
        out: dict[str, Any] = {}
        warnings: list[str] = []
        if legality:
            out["legality_after"] = legality
            warnings += [f"Once applied, the deck would break its format's rules: {p}." for p in legality]
        same = unchanged_changes(changes, rows)
        if same:
            out["unchanged"] = same
            warnings += [
                f"“{u['name']}” is already that way, so its {u['action']} changes nothing and is left out."
                for u in same
            ]
        if warnings:
            out["warnings"] = warnings
        return out

    @_dedupe
    async def propose_package(
        self,
        sub: str,
        deck_id: str,
        package_ref: str,
        *,
        categories: str = "package",
        skip_existing: bool = True,
        skip_outside_identity: bool = True,
        singleton: bool | None = None,
        color_tag: str | None = None,
        color: str | None = None,
    ) -> dict[str, Any]:
        """Add a card package's cards to one of the member's decks, as the site's Add package does:
        its cards as ordinary adds (each reviewed like any other), with a line saying where they
        come from. The package is read as anyone reads a deck now; the read that counts an import
        on Archidekt is sent only when the change is applied. ``color_tag`` names a colour tag for
        every new row: one the deck uses or one of the member's color tags, or any name with
        ``color``."""
        from . import packages

        own = await self.get_own_deck(sub, _clean_deck_id(deck_id))
        tag = await self._package_tag(sub, own, color_tag, color) if (color_tag or "").strip() else None
        pkg_deck, changes, skipped = packages.adds(
            own,
            await self.get_any_deck(sub, package_ref),
            categories=categories,
            skip_existing=skip_existing,
            skip_outside_identity=skip_outside_identity,
            singleton=singleton,
            color_tag=tag,
        )
        line = _row(
            "package",
            package_id=pkg_deck.id,
            name=pkg_deck.name,
            text=packages.source_text(pkg_deck.name, changes, skipped),
            cards=[c["card_name"] for c in changes],
            skipped=skipped,
        )
        return await self.propose(sub, deck_id, changes, source_row=line)

    async def _package_tag(self, sub: str, deck: Deck, name: str | None, color: str | None) -> str:
        """The colour tag an Add package names, as Archidekt stores it ("Name,#rrggbb"): the colour
        given, else the one the deck already uses for that name, else the member's color tag of
        that name (My lists)."""
        from . import account_lists

        text = clean_text(str(name or ""))
        if color:
            return str(_parse_label_change(0, "package", {"label": text, "color": color}).label)
        for c in deck.cards:
            have, colour = split_label(c.label)
            if have.casefold() == text.casefold() and colour:
                return f"{have},{colour}"
        mine = await account_lists.read(self, sub, "color_tags")
        for t in mine.get("items") or []:
            if str(t.get("name") or "").casefold() == text.casefold():
                return str(_parse_label_change(0, "package", {"label": t["name"], "color": t["color"]}).label)
        raise DeckError(
            "invalid",
            f"No color tag named “{text}” on this deck or in your color tags. Pick one, or give its color.",
        )

    async def plan_new_deck(
        self,
        sub: str,
        *,
        name: str,
        deck_format: str,
        cards: Any = None,
        decklist_text: str | None = None,
        csv_text: str | None = None,
        json_text: str | None = None,
        private: bool = True,
        theorycrafted: bool = False,
        game: str = "any",
        package: dict[str, Any] | None = None,
        folder_id: int | None = None,
        entries: list[dict[str, Any]] | None = None,
        parsed_package: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A new deck (or card package) worked out and checked, saving nothing: ``{"name",
        "changes", "rows", "cards", "side_cards"}``, where ``changes`` is what the proposal stores
        and ``rows`` its review lines. Shared by propose_new_deck, an amend of a waiting new deck
        and check_new_deck. No deck is read (there is none yet); a folder is looked up in the
        member's folder tree. ``entries`` and ``parsed_package``: the card list and package
        settings as a waiting proposal already stores them (an amend that keeps them)."""
        name = clean_text(name or "")
        if not name or len(name) > 120:
            raise DeckError("invalid", "name is required (up to 120 characters)")
        fmt = str(deck_format or "commander").strip().lower()
        if fmt not in FORMAT_IDS:
            raise DeckError("invalid", f"deck_format must be one of: {', '.join(sorted(FORMAT_IDS))}")
        # the New deck form's Theorycrafted and Game type, checked as deck settings checks them
        extra = parse_details({"theorycrafted": theorycrafted, "game": game or "any"})
        # a card package (packages.py): made with the New package form's request, no format
        pkg = parsed_package
        if package is not None:
            from . import packages

            pkg = packages.parse(package)
        if entries is None:
            if pkg is not None and cards is None and not (decklist_text or csv_text or json_text):
                cards = []  # an empty package, as the New package form makes one
            entries = normalise_cards(
                cards=cards, decklist_text=decklist_text, csv_text=csv_text, json_text=json_text
            )
        _token, link = self._token(sub)  # must be linked before we store a proposal that needs it
        # an optional folder of the member's own, checked as propose_clone checks it
        folder = None
        if folder_id is not None:
            if isinstance(folder_id, bool) or not str(folder_id).isdigit():
                raise DeckError("invalid", "That folder is not in your Archidekt account.")
            _info, folder = await self._folder_in(sub, int(folder_id))
            if folder.get("backup"):
                raise DeckError("not_found", "That folder is not in your Archidekt account.")
        # A custom category flagged {noDeck} or {noPrice} keeps its switches (archidekt.com's
        # Create Category): it is made with them after the deck and before any card goes in.
        # Maybeboard and Sideboard are Archidekt's own and already sit outside the deck.
        flags: dict[str, dict[str, Any]] = {}
        for c in entries:
            cat = c["categories"][0] if c["categories"] else ""
            if not cat or cat in SIDE_CATEGORIES:
                continue
            f = flags.setdefault(
                cat, {"name": cat, "includedInDeck": True, "includedInPrice": True, "isPremier": False}
            )
            if c.get("in_deck") is False:
                f["includedInDeck"] = False
            if c.get("no_price"):
                f["includedInPrice"] = False
        category_flags = [f for f in flags.values() if not (f["includedInDeck"] and f["includedInPrice"])]
        # each is one Create Category call: held to what propose_deck_categories allows
        if len(category_flags) > MAX_CATEGORY_CHANGES:
            raise DeckError(
                "invalid",
                f"The list flags {len(category_flags)} categories {{noDeck}} or {{noPrice}}; a new deck "
                f"can make at most {MAX_CATEGORY_CHANGES} with their switches.",
            )
        seen: dict[str, str] = {}
        for f in category_flags:
            if len(f["name"]) > MAX_CATEGORY:
                raise DeckError(
                    "invalid", f"A category name is 1 to {MAX_CATEGORY} characters: “{f['name'][:40]}…”."
                )
            other = seen.setdefault(f["name"].casefold(), f["name"])
            if other != f["name"]:
                raise DeckError(
                    "invalid",
                    f"The categories “{other}” and “{f['name']}” differ only in capitals; use one spelling.",
                )
        # Maybeboard and sideboard rows (in_deck false) are created too, but not counted as deck cards.
        total = sum(c["quantity"] for c in entries if c.get("in_deck", True))
        side = sum(c["quantity"] for c in entries) - total
        rows = [
            _row(
                "new_deck",
                name=name,
                format=fmt,
                cards=total,
                side_cards=side or None,
                private=bool(private),
                theorycrafted=extra["theorycrafted"] or None,
                game=GAME_LABELS.get(extra["game"]),
                package=_package_text(pkg),
                folder=str(folder.get("name") or "") if folder else None,
            )
        ]
        rows += [
            _row(
                "add",
                name=c["name"],
                qty=c["quantity"],
                category=", ".join(c["categories"]) or None,
                zone=None if c.get("in_deck", True) else "side",
            )
            for c in entries
        ]
        for f in category_flags:
            row = _row(
                "deck_category", name=f["name"], create=True, counts=True if not f["includedInDeck"] else None
            )
            _flag_rows(row, None, f)
            rows.append(row)
        changes = {
            "name": name,
            "format": fmt,
            "private": private,
            "cards": entries,
            # The Archidekt account the deck is made in: shown on the review page, and the
            # apply is refused if another account is linked by then.
            "archidekt_username": link["archidekt_username"],
            "archidekt_user_id": link.get("archidekt_user_id"),
            **({"category_flags": category_flags} if category_flags else {}),
            **({"theorycrafted": True} if extra["theorycrafted"] else {}),
            **({"game": extra["game"]} if extra["game"] != "any" else {}),
            **({"package": pkg} if pkg else {}),
            **({"folder_id": int(folder["id"])} if folder else {}),
        }
        return {"name": name, "changes": changes, "rows": rows, "cards": total, "side_cards": side}

    def waiting_new_deck_named(self, sub: str, name: str, *, package: bool) -> dict[str, Any] | None:
        """This app's own waiting new deck (or, with ``package``, card package) of this name, the
        names compared trimmed and in any capitals: the one a second propose_new_deck of the same
        name updates in place instead of stacking another proposal beside it."""
        client = current_client.get()
        if client in (None, BROWSER_CLIENT, ADMIN_CLIENT):
            return None
        want = clean_text(name or "").strip().casefold()
        for line in reversed(self.db.pending_board(sub, "new")):
            if line.get("kind") != "create_deck" or line.get("created_by_client") != client:
                continue
            if str(line.get("deck_name") or "").strip().casefold() != want:
                continue
            row = self.db.get_proposal(line["id"], sub)
            changes = (row or {}).get("changes")
            if row is not None and isinstance(changes, dict) and bool(changes.get("package")) == package:
                return row
        return None

    def _replaceable_new_deck(self, sub: str, proposal_id: Any) -> dict[str, Any]:
        """The waiting new deck or card package ``replaces`` names: the member's own, still waiting."""
        row = self.db.get_proposal(str(proposal_id or ""), sub)
        if row is None or row["state"] != "pending" or row["expires_at"] < int(time.time()):
            raise DeckError(
                "invalid",
                f"{proposal_id} is not a waiting proposal of this user, so nothing was replaced or proposed.",
            )
        if row.get("kind") != "create_deck":
            raise DeckError(
                "invalid",
                "replaces names a waiting new deck or card package only. To change a waiting deck "
                "edit, use amend_proposal; nothing was proposed.",
            )
        return row

    def _already_waiting(self, sub: str, proposal_id: str, what: str) -> dict[str, Any]:
        out = self.describe(sub, proposal_id)
        no = out.get("board_no")
        out["duplicate_of"] = proposal_id
        out["note"] = (
            f"Already waiting as #{no}" if no else "Already waiting"
        ) + f": {what} was proposed before and has not been decided, so no second copy was made."
        return out

    async def _update_new_deck(
        self, sub: str, row: dict[str, Any], plan: dict[str, Any], *, version: int | None = None
    ) -> dict[str, Any]:
        """A waiting new deck changed in place to ``plan`` (its next version): an amend
        (``version``: the one the assistant last saw), or a second propose_new_deck of the same
        name. An unchanged plan changes nothing and says so. At the version limit a propose makes
        a new proposal that replaces it; an amend is refused (versions_full)."""
        pid = row["id"]
        if version is None and plan["changes"] == row.get("changes"):
            return self._already_waiting(sub, pid, "exactly this new deck")
        rows = plan["rows"]
        try:
            new = self.db.amend_proposal(
                pid,
                sub,
                expect_version=int(version if version is not None else row.get("version") or 1),
                changes=plan["changes"],
                rows=rows,
                diff_text="\n".join(row_line(r) for r in rows),
                by_client=current_client.get(),
                what_changed=board.what_changed(row.get("rows"), rows),
                deck_name=plan["name"],
            )
        except ProposalVersionsFull:
            if version is not None:  # an amend asked for this one: the assistant decides
                raise DeckError(
                    "versions_full",
                    f"This proposal already keeps {MAX_PROPOSAL_VERSIONS} earlier versions, so it cannot "
                    "be amended again. Call propose_new_deck with the whole new deck and pass replaces "
                    "with this one's id; nothing was changed.",
                    proposal_id=pid,
                ) from None
            made = await self._save_new_deck(sub, plan)
            if made.get("duplicate_of"):
                return made
            done, _later = self.mark_replaced(sub, made["proposal_id"], [pid])
            return {**self.describe(sub, made["proposal_id"]), **({"replaced": done} if done else {})}
        if new is None:
            now = self.db.get_proposal(pid, sub)
            if now is not None and now["state"] == "pending":
                raise self._changed(now)
            raise self._not_pending(sub, pid, "amended")
        self.db.restart_expiry(pid, sub, int(time.time()) + self.expiry_seconds())
        self._audit(
            "proposal_amended", sub=sub, detail={"proposal_id": pid, "deck_id": "new", "version": new}
        )
        return self.describe(sub, pid)

    async def _save_new_deck(self, sub: str, plan: dict[str, Any]) -> dict[str, Any]:
        """A new deck proposal stored as ``plan`` says (an exact repeat of this app's waiting one
        returns that one)."""
        self._room_for_proposal(sub)
        pid = secrets.token_urlsafe(12)
        try:
            self._save_proposal(
                {
                    "id": pid,
                    "owner_sub": sub,
                    "kind": "create_deck",
                    "deck_id": "new",
                    "deck_name": plan["name"],
                    "baseline_fingerprint": "",
                    "changes": plan["changes"],
                },
                plan["rows"],
            )
        except _Duplicate as dup:
            return self._already_waiting(sub, dup.proposal_id, "this exact change")
        self._audit("proposal_created", sub=sub, detail={"proposal_id": pid, "kind": "create_deck"})
        return self.describe(sub, pid)

    async def propose_new_deck(
        self,
        sub: str,
        *,
        name: str,
        deck_format: str,
        cards: Any = None,
        decklist_text: str | None = None,
        csv_text: str | None = None,
        json_text: str | None = None,
        private: bool = True,
        theorycrafted: bool = False,
        game: str = "any",
        package: dict[str, Any] | None = None,
        folder_id: int | None = None,
        replaces: str | None = None,
    ) -> dict[str, Any]:
        """A new deck (or card package) proposal. One proposal per new deck: while this app's own
        new deck of the same name (any capitals) still waits, it is updated in place to its next
        version instead (``updated_in_place``); ``replaces`` names a waiting new deck or package of
        the member's that this one takes the place of, the same way. Cards already drawn for it in
        the chat then step aside for the newest one."""
        target = self._replaceable_new_deck(sub, replaces) if replaces else None
        plan = await self.plan_new_deck(
            sub,
            name=name,
            deck_format=deck_format,
            cards=cards,
            decklist_text=decklist_text,
            csv_text=csv_text,
            json_text=json_text,
            private=private,
            theorycrafted=theorycrafted,
            game=game,
            package=package,
            folder_id=folder_id,
        )
        if target is None:
            target = self.waiting_new_deck_named(
                sub, plan["name"], package=bool(plan["changes"].get("package"))
            )
        if target is None:
            return await self._save_new_deck(sub, plan)
        before = int(target.get("version") or 1)
        out = await self._update_new_deck(sub, target, plan)
        if out.get("duplicate_of") or out.get("proposal_id") != target["id"]:
            return out
        no = out.get("board_no")
        what = "card package" if plan["changes"].get("package") else "new deck"
        why = "replaces names it" if replaces else f"one named “{plan['name']}” was already waiting"
        return {
            **out,
            "updated_in_place": True,
            "replaced_version": before,
            "note": (
                f"Updated your waiting {what}{f' #{no}' if no else ''} to version {out['version']} instead "
                f"of making a second proposal, because {why}. The user approves this one proposal; "
                "older cards for it in the chat now say a newer version is below."
            ),
        }

    async def unknown_new_deck_cards(self, entries: list[dict[str, Any]]) -> list[str]:
        """The names in a new deck's list that are no Magic card at all (check_new_deck), as
        check_plan looks them up: a name that can't be judged passes."""
        names = list(dict.fromkeys(str(c.get("name") or "") for c in entries if c.get("name")))
        if not names or self.known_cards is None:
            return []
        try:
            found = await self.known_cards(names)
        except Exception:
            logger.exception("card-name check failed")
            return []
        return [n for n in names if found.get(n) is False]

    async def _refuse_unknown_cards(self, deck: Deck, changes: list[Change]) -> None:
        unknown = await unknown_card_names(deck, changes, self.known_cards)
        if unknown:
            raise DeckError(
                "unknown_cards",
                "No Magic card is called "
                + ", ".join(f"'{n}'" for n in unknown)
                + ". Check the spelling; nothing was proposed.",
                unknown_cards=unknown,
            )

    def keep_no_backup(self, sub: str, proposal_id: str) -> None:
        """Record on a member's own save (the deck page, Card search, the deck editor with its
        backup box unticked) that it makes no backup copy on Archidekt, so an apply later (writes
        switched on, or from its review page) keeps that choice. Only the member's own proposals:
        an assistant's always keeps its copy."""
        row = self.db.get_proposal(proposal_id, sub)
        if row is None or row.get("created_by_client") != BROWSER_CLIENT:
            return
        self.db.edit_proposal_result(proposal_id, sub, lambda r: {**(r or {}), NO_BACKUP_KEY: True})

    def keep_backup_box(self, sub: str, proposal_id: str, version: int | None, *, keep: bool) -> None:
        """Record the backup box of a press on a member's own save that still waits at ``version``
        (the deck editor's Save anyway): unticked marks it as keep_no_backup does, ticked takes
        the mark off."""
        row = self.db.get_proposal(proposal_id, sub)
        if (
            row is None
            or row.get("created_by_client") != BROWSER_CLIENT
            or row["state"] != "pending"
            or (version is not None and int(row.get("version") or 1) != int(version))
        ):
            return

        def mark(r: Any) -> dict[str, Any]:
            out = {k: v for k, v in (r or {}).items() if k != NO_BACKUP_KEY}
            return out if keep else {**out, NO_BACKUP_KEY: True}

        self.db.edit_proposal_result(proposal_id, sub, mark)

    def _applied_backup_gone(self, sub: str, row: dict[str, Any]) -> bool:
        """An applied deck deletion whose backup copy the held deck list says is gone (the text
        is rebuilt on every view, after the member may have deleted or moved the copy)."""
        changes = row.get("changes") if isinstance(row.get("changes"), dict) else {}
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        if row.get("kind") != "action" or changes.get("action") != "delete_deck":
            return False
        return bool(result.get("backup_deck_id")) and self.backup_gone(sub, str(result["backup_deck_id"]))

    def describe(self, sub: str, proposal_id: str) -> dict[str, Any]:
        # A note closed without settling its links (a restart, a cancel, expiry) holds its
        # dependents before anything about them is shown or decided.
        self.db.hold_blocked(sub)
        row = self.db.get_proposal(proposal_id, sub)
        if row is None:
            raise DeckError("not_found", "No such proposal for your account.")
        state = row["state"]
        if state == "pending" and row["expires_at"] < int(time.time()):
            state = "expired"
        kind = row.get("kind", "edit")
        risk, why, mode, allowed = self._assistant_policy(sub, row)
        if state not in ("pending", "applying"):
            stored = row.get("risk")
            if stored in ("low", "high", *modes.MEMBER_ONLY):
                # stored when it closed: the tier it was judged by then, so the reason worked out
                # under today's settings may contradict it and is not shown
                risk, why = stored, STORED_RISK_REASON
            elif stored == modes.UNJUDGED:
                risk, why = None, ""  # nothing to judge it by: no risk label, a dash
        if risk == "high" and why == modes.NO_ROWS_REASON:
            # the same while it waits, or closed before its tier was stored; `allowed` above still
            # keeps a waiting one for the member's own press. A High with a reason (another app
            # changed it) stays High.
            risk, why = None, ""
        press = self.press_reason(sub, row) if risk not in modes.MEMBER_ONLY else None
        # "May apply now": the member's mode allows it for this risk AND the proposal is still
        # pending (risk, approval_mode and risk_reason keep the policy side either way).
        # A proposal that needs a look would be refused as it stands: the assistant amends it.
        may_apply = (
            state == "pending"
            and self.settings.writes_enabled
            and allowed
            and row.get("status") != "needs_look"
        )
        closed_by, closed_by_app = self._closer(row) if state == "rejected" else (None, None)
        reject_reason = (
            str((row["result"] or {}).get("reject_reason") or "")
            if state == "rejected" and isinstance(row["result"], dict)
            else ""
        )
        # Checked against the deck as it is now (after an apply, a deck page view, a new
        # proposal or the deck sync), it would no longer do what was reviewed.
        status = row.get("status") if state == "pending" else None
        out_of_date = int(row.get("status_at") or row["created_at"]) if status == "needs_look" else None
        status_info = self._status_fields(sub, row) if status else {}
        # Archidekt's waits stopped the last apply: it failed, or it changed nothing and the
        # proposal is pending again (an out-of-date one is told only that it is out of date)
        stopped = (row["result"] or {}).get("error") == "rate_limited" and (
            state == "failed" or (state == "pending" and self.settings.writes_enabled and not out_of_date)
        )
        applied = (
            applied_text(
                kind, row.get("changes"), row["result"], backup_gone=self._applied_backup_gone(sub, row)
            )
            if state == "applied"
            else ""
        )
        return {
            "proposal_id": row["id"],
            "kind": kind,
            # what an applied account action or collection change did (the pages and the card say it)
            **({"applied_text": applied} if applied else {}),
            "deck_id": row["deck_id"],
            "deck_url": f"https://archidekt.com/decks/{row['deck_id']}" if row["deck_id"].isdigit() else None,
            "deck_name": row["deck_name"],
            "state": state,
            "diff": row["diff_text"],
            # The structured rows the review page renders (None for proposals stored before them).
            "rows": row.get("rows"),
            # cards whose name a scan guessed from a misread name: confirm each with the user
            **(
                {
                    "guessed_names": [
                        {"name": r["name"], "read_as": r["guessed_from"]}
                        for r in row["rows"]
                        if r.get("guessed_from")
                    ]
                }
                if any(r.get("guessed_from") for r in row.get("rows") or [])
                else {}
            ),
            "changes": row["changes"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "applied_at": row["applied_at"],
            # Where an applied proposal was applied (assistant, card, review_page, browser or api),
            # from the audit log, which keeps a year; None when the log no longer has it.
            **({"applied_via": self._applied_via(sub, row["id"])} if state == "applied" else {}),
            # a clone's copy is a deck of its own: deck_id above stays the deck it was copied from
            **_new_deck_fields(kind, row.get("result")),
            # When and by whom a closed proposal was closed (closed_by: assistant, card,
            # review_page, browser, api or disconnected; recorded since 0.7.22).
            **({"closed_at": row["applied_at"]} if state in ("rejected", "failed") else {}),
            **({"closed_at": row["expires_at"]} if state == "expired" else {}),
            **({"closed_by": closed_by} if closed_by else {}),
            **({"closed_by_app": closed_by_app} if closed_by_app else {}),
            **({"reject_reason": reject_reason} if reject_reason else {}),
            # while an apply runs in the background: how far it got (see APPLY_WAIT_SECONDS)
            **(
                {"progress": _progress_view(self._progress[row["id"]])}
                if state == "applying" and row["id"] in self._progress
                else {}
            ),
            "snapshot_id": row["snapshot_id"],
            "result": {k: v for k, v in row["result"].items() if k != NO_BACKUP_KEY} or None
            if isinstance(row["result"], dict)
            else row["result"],
            "review_url": f"{self.settings.public_url}/proposals/{row['id']}",
            # Which app made it ("browser", or "app: <its name> (<client id>)"), for the review page.
            "created_by": self._creator_label(row.get("created_by_client")),
            "created_by_client": row.get("created_by_client"),
            "writes_enabled": self.settings.writes_enabled,
            # The member's approval mode (modes.py) and this proposal's risk tier decide whether
            # the assistant may apply it itself or the member's own press is needed.
            "approval_mode": mode,
            "risk": risk,
            "risk_reason": why,
            "assistant_may_apply": may_apply,
            # why it waits for the member's own press in every mode (shown on the card and the
            # review page, and told to the assistant), when it does
            **({"press_reason": press} if press and state == "pending" else {}),
            **self.replace_links(sub, {**row, "state": state}),
            **({"out_of_date": True, "out_of_date_since": out_of_date} if out_of_date else {}),
            # 0.7.29: every note has a version (an amend makes the next one) and a number on its
            # deck's board; a waiting one may be "updated", "needs_look" or "held" (with why).
            "version": int(row.get("version") or 1),
            # An amended note says which app changed it and what changed
            **(
                {
                    "changed_by": self._creator_label(row["amended_by_client"]),
                    "changed_by_apps": self.changed_by_apps(sub, row),
                    "what_changed": str(row.get("version_note") or ""),
                }
                if int(row.get("version") or 1) > 1 and row.get("amended_by_client")
                else {}
            ),
            "board_no": row.get("board_no"),
            # how many cards this app's chat has drawn for it: a card with a lower number of its
            # own steps aside for the newest (approve.py)
            **({"card_seq": seq} if (seq := _card_seq(row, current_client.get())) else {}),
            **status_info,
            "next_step": (OUT_OF_DATE_STEP + status_info["status_reason"] + " " if out_of_date else "")
            + (
                "".join(
                    step
                    for step, kind in ((HELD_STEP, "depends:"), (HELD_REPLACED_STEP, "replaces:"))
                    if any(c.startswith(kind) for c in board.hold_causes(row.get("status_reason")))
                )
                or HELD_STEP
                if status == "held"
                else ""
            )
            + (
                # an apply that stopped at the backup (possibly after its caller had its answer)
                "The last attempt to apply this stopped before anything changed: the backup copy "
                "could not be made on Archidekt. "
                if state == "pending" and (row["result"] or {}).get("error") == "backup_failed"
                else ""
            )
            + (PATIENCE_SPENT + " " if stopped else "")
            + _next_step(
                state,
                self.settings.writes_enabled,
                may_apply=may_apply,
                mode=mode,
                risk=risk,
                risk_reason=why,
                in_chat=self.settings.apply_in_chat,
                partial=bool(
                    (row["result"] or {}).get("sent_entries") or (row["result"] or {}).get("categories_saved")
                ),
                kind=kind,
                card_may_approve=self.card_may_approve(row),
                press_reason=press or "",
                policy_allows=allowed,
                opens_first=formatting_review_only(row) if formatting.keeps_formatting(row) else "",
                rate_limited=(
                    _rate_limited_note(
                        row["result"] or {},
                        pending=state == "pending",
                        collection=kind == "collection",
                        clone=kind == "clone",
                    )
                    if stopped
                    else ""
                ),
                closed_by=closed_by,
                closed_by_app=closed_by_app,
                applied=applied,
            ),
        }

    def _closer(self, row: dict[str, Any]) -> tuple[str | None, str | None]:
        """Who rejected a proposal, as reject() recorded it in the result: the kind of closer and
        the connected app's name (None for proposals rejected before this was recorded)."""
        result = row.get("result") if isinstance(row.get("result"), dict) else {}
        by = result.get("rejected_by")
        if by not in REJECTED_BY:
            return None, None
        client = result.get("client")
        app = client and client not in (BROWSER_CLIENT, ADMIN_CLIENT)
        name = clean_text(self.db.client_name(client) or "")[:60] if app else ""
        return by, name or None

    def _applied_via(self, sub: str, proposal_id: str) -> str | None:
        """Where apply() was called from, in the words closed_by uses: the assistant's
        apply_proposal ("mcp"), the card ("app", one change or its Apply all), the review page
        (one change or its Apply all), the member's other pages ("browser", also the review page
        before its applies were logged apart) or the API."""
        via = self.db.applied_via(sub, proposal_id)
        if via is None:
            return None
        return {
            "mcp": "assistant",
            "app": "card",
            REVIEW_PAGE: "review_page",
            "browser": "browser",
            "api": "api",
        }.get(via, "api")

    def _changed(self, row: dict[str, Any]) -> DeckError:
        """The refusal of an apply of a version that is no longer the proposal's."""
        return DeckError(
            "changed",
            "There is a newer version of this change, so nothing was sent to Archidekt. Open it again "
            "to see what it does now.",
            review_url=f"{self.settings.public_url}/proposals/{row['id']}",
            state=row["state"],
            version=int(row.get("version") or 1),
        )

    def _not_pending(self, sub: str, proposal_id: str, doing: str) -> DeckError:
        """The refusal for an apply or reject of a proposal that is no longer pending, carrying its
        state now (and when and by whom it was closed), so a card can show it and drop its buttons."""
        p = self.describe(sub, proposal_id)
        extra = {k: p[k] for k in ("state", "closed_at", "closed_by", "closed_by_app") if p.get(k)}
        return DeckError(
            "not_pending",
            f"This proposal is already {p['state']}, so it cannot be {doing}. Nothing was sent to "
            "Archidekt; ask for a new proposal if the change is still wanted.",
            review_url=p["review_url"],
            **extra,
        )

    def _creator_label(self, client_id: str | None) -> str:
        if not client_id:
            return ""
        name = None if client_id in (BROWSER_CLIENT, ADMIN_CLIENT) else self.db.client_name(client_id)
        return actor_label(client_id, name)

    def expiry_seconds(self) -> int:
        """How long a new (or amended) proposal waits: the admin setting
        ``proposal_expiry_days`` (7 by default; a deployment value wins). Changing it does
        not move proposals already waiting."""
        return int(self.live.get("proposal_expiry_days")) * 86400

    def _room_for_proposal(self, sub: str) -> None:
        """Refuse a new proposal while the member already has MAX_PENDING_PROPOSALS pending (the
        one limit, fixed in code; there is no longer a limit per app)."""
        if self.db.count_pending_proposals(sub) >= MAX_PENDING_PROPOSALS:
            raise DeckError(
                "rate_limited",
                f"You already have {MAX_PENDING_PROPOSALS} pending proposals. Apply or reject some "
                f"of them at {self.settings.public_url}/proposals (or wait for them to expire) "
                "before proposing more.",
            )

    def _save_proposal(self, row: dict[str, Any], rows: list[dict[str, Any]]) -> None:
        """Store a proposal with its review rows, its text diff (made from the rows), its expiry
        and the app that made it; refused past the member's pending cap. An added card that one
        of the member's scans matched from a misread name (a new row, or more copies) is marked
        (``guessed_from``), so the
        review page, the in-chat card and the diff all say the name was a guess."""
        try:  # only a warning: a scan store that fails must not stop the proposal
            guesses = self.guessed_names(row["owner_sub"]) if self.guessed_names else {}
        except Exception:
            logger.exception("reading scan guesses failed")
            guesses = {}
        client = current_client.get()
        if client not in (None, BROWSER_CLIENT, ADMIN_CLIENT):
            dup = self.db.find_duplicate(
                row["owner_sub"],
                deck_id=str(row["deck_id"]),
                kind=str(row.get("kind") or "edit"),
                changes=row["changes"],
                client=client,
            )
            if dup is not None:
                raise _Duplicate(dup)
        for r in rows:
            adds = r.get("kind") == "add" or (
                r.get("kind") == "change" and int(r.get("after") or 0) > int(r.get("before") or 0)
            )
            read = guesses.get(str(r.get("name", "")).lower()) if adds else None
            if read and not r.get("guessed_from"):
                r["guessed_from"] = clean_text(read)[:120]
        saved = self.db.save_proposal(
            {
                **row,
                "rows": rows,
                "diff_text": "\n".join(row_line(r) for r in rows),
                "expires_at": int(time.time()) + self.expiry_seconds(),
                "created_by_client": current_client.get(),
            },
            max_pending=MAX_PENDING_PROPOSALS,
            max_closed=MAX_CLOSED_PROPOSALS,
        )
        if not saved:
            self._room_for_proposal(row["owner_sub"])
            raise DeckError("rate_limited", "Too many pending proposals; apply or reject some first.")

    def waiting_by_deck(self, sub: str) -> dict[str, tuple[str, int, str]]:
        """What waits for the member's own press, per deck: ``{deck_id: (deck name, count, the
        newest one's id)}``, with new decks under ``"new"``. Counted: pending proposals an
        assistant made that the member's mode does not let it apply itself (held, Updated and
        Needs a look ones included). Made on the gateway's own pages, or by the gateway, they are
        the member's own and not counted. Reads only; the member's notification messages follow
        it (notify.py)."""
        now = int(time.time())
        out: dict[str, tuple[str, int, str]] = {}
        listed = self.db.list_proposals(sub, limit=MAX_PENDING_PROPOSALS, states=("pending",))
        for r in listed:
            if r.get("created_by_client") in (None, BROWSER_CLIENT, ADMIN_CLIENT) or r["expires_at"] < now:
                continue
            deck_id = str(r.get("deck_id") or "")
            if r.get("kind") == "create_deck":
                deck_id = "new"  # every new deck under one key: it has no deck id yet
            elif not deck_id.isdigit():
                continue
            row = self.db.get_proposal(r["id"], sub)
            if row is None or row["state"] != "pending":
                continue
            if self._assistant_policy(sub, row)[3]:
                continue
            name, count, newest = out.get(deck_id, (str(r.get("deck_name") or ""), 0, r["id"]))
            out[deck_id] = (name, count + 1, newest)
        return out

    def list_proposals(self, sub: str, *, full: bool = False, **narrow: Any) -> list[dict[str, Any]]:
        """Newest first; ``narrow`` is passed to the database (deck_id, kinds, states, search,
        limit, offset, oldest_first). With ``full`` each row keeps its whole change text as ``diff_text``."""
        now = int(time.time())
        out = []
        rows = self.db.list_proposals(sub, **narrow)
        # the links and app names every row asks for, read once for the whole list
        with self.db.read_ahead(sub, [r["id"] for r in rows], [r.get("created_by_client") for r in rows]):
            for r in rows:
                out.append(self._proposal_listed(sub, r, now, full))
        return out

    def _proposal_listed(self, sub: str, r: dict[str, Any], now: int, full: bool) -> dict[str, Any]:
        """One row of list_proposals."""
        state = "expired" if r["state"] == "pending" and r["expires_at"] < now else r["state"]
        diff_text = str(r.pop("diff_text", None) or "")
        lines = diff_text.splitlines()
        summary = "; ".join(lines[:3]) + (f" (+{len(lines) - 3} more)" if len(lines) > 3 else "")
        if full:
            r["diff_text"] = diff_text
        return {
            **r,
            "summary": summary[:400],
            "state": state,
            "created_by": self._creator_label(r.get("created_by_client")),
            "created_by_client": r.get("created_by_client"),
            "review_url": f"{self.settings.public_url}/proposals/{r['id']}",
            **({"out_of_date": True} if state == "pending" and r.get("status") == "needs_look" else {}),
            **(self._status_fields(sub, r) if state == "pending" and r.get("status") else {"status": None}),
        }

    def _review_page_only(self, sub: str, row: dict[str, Any]) -> DeckError:
        """The refusal for any caller but the review page's own form that tries to apply an
        Archidekt account action, with the row's real risk tier (consent, or destructive
        for a deck deletion). Audited as ``apply_needs_user``."""
        risk, _why = modes.risk_of(row.get("kind") or "edit", row.get("rows"))
        self._audit(
            "apply_needs_user",
            sub=sub,
            detail={"proposal_id": row["id"], "deck_id": row["deck_id"], "risk": risk},
        )
        if formatting.keeps_formatting(row):  # approved where its formatting is shown
            return DeckError(
                "browser_required",
                formatting_review_only(row) + " Nothing was sent to Archidekt; do not retry.",
                review_url=f"{self.settings.public_url}/proposals/{row['id']}",
                state=row["state"],
                approval_mode=self.mode_of(sub),
                risk=risk,
            )
        where = (
            "on the review page (review_url)"
            if not self.card_may_approve(row)
            else "on the proposal card in the chat or on the review page (review_url)"
        )
        return DeckError(
            "browser_required",
            "This is an action on the user's Archidekt account (a like, vote, bookmark, follow, "
            "comment, folder, collaborator change or deck deletion). Each one needs the user's "
            f"own approval {where}, in every approval mode. Nothing was sent to Archidekt; do not retry.",
            review_url=f"{self.settings.public_url}/proposals/{row['id']}",
            state=row["state"],
            approval_mode=self.mode_of(sub),
            risk=risk,
        )

    @staticmethod
    def card_may_approve(row: dict[str, Any]) -> bool:
        """Whether the in-chat card may approve this proposal: everything but a deck deletion,
        which only its review page applies, and a change that keeps formatting.
        Account actions (likes, comments, folders, collaborators...) may since 0.7.29:
        the member's press on the card, never the assistant's own apply."""
        if row.get("kind") == "action" and (row.get("changes") or {}).get("action") == "delete_deck":
            return False
        # A change that keeps a description's or comment's formatting is approved where its
        # formatting is shown, the review page; the card asks the member to open it there
        return not formatting.keeps_formatting(row)

    def rewritten_by_another_app(self, sub: str, row: dict[str, Any]) -> bool:
        """Whether an app other than the one that made this note changed it, in any version
        (such a change is approved on its own, never in "Apply all")."""
        if int(row.get("version") or 1) <= 1:
            return False
        maker = row.get("created_by_client")
        apps = {row.get("amended_by_client")} | {
            v.get("made_by_client") for v in self.db.proposal_versions(row["id"], sub)
        }
        return any(a and a != maker for a in apps)

    def changed_by_apps(self, sub: str, row: dict[str, Any]) -> list[str]:
        """The apps that changed this note after it was made, each once at its latest change, so
        the last one named made the change the line describes (actor labels, as in "Changed
        by Claude, then ChatGPT")."""
        if int(row.get("version") or 1) <= 1:
            return []
        makers = [
            v.get("made_by_client")
            for v in self.db.proposal_versions(row["id"], sub)
            if int(v["version"]) > 1
        ]
        out: list[str] = []
        for c in [*makers, row.get("amended_by_client")]:
            label = self._creator_label(c) if c else ""
            if label:
                if label in out:
                    out.remove(label)
                out.append(label)
        return out

    def in_apply_all(self, sub: str, row: dict[str, Any]) -> bool:
        """Whether a card's "Apply all" may include this note: one the card may approve, and not
        rewritten by another app."""
        return self.card_may_approve(row) and not self.rewritten_by_another_app(sub, row)

    def approval_for(
        self, sub: str, row: dict[str, Any], *, version: int | None = None, card_seq: int | None = None
    ) -> str:
        """The in-chat card's one-time code for a proposal (approve.py), bound to the proposal,
        its owner, the app that made it, its creation time, its version and the number of the
        newest card drawn for it in that app's chat (``card_seq`` names another). A deck deletion has
        no code (only its review page applies it), so asking for one is refused here, at
        the source."""
        if not self.card_may_approve(row):
            raise DeckError(
                "browser_required",
                formatting_review_only(row)
                if formatting.keeps_formatting(row)
                else "Deleting a deck has no in-chat approval code; only its review page applies it.",
                review_url=f"{self.settings.public_url}/proposals/{row['id']}",
            )
        return approval_code(
            self.settings.session_secret,
            proposal_id=row["id"],
            sub=sub,
            client_id=row.get("created_by_client") or "",
            created_at=int(row["created_at"]),
            version=int(version if version is not None else row.get("version") or 1),
            card_seq=card_seq if card_seq is not None else _card_seq(row, row.get("created_by_client")),
        )

    def check_approval(self, sub: str, proposal_id: str, approval: Any) -> dict[str, Any]:
        """The proposal row when ``approval`` is its code and the caller is the app that made it;
        otherwise ``invalid_approval`` (audited), and nothing else happens. A code of one of its
        earlier versions is ``changed``: the card was drawn before an amend."""
        row = self.db.get_proposal(proposal_id, sub)
        if row is None:
            raise DeckError("not_found", "No such proposal for your account.")
        if not self.card_may_approve(row):  # no code exists for it, whatever was sent
            raise self._review_page_only(sub, row)
        creator = row.get("created_by_client")
        version = int(row.get("version") or 1)
        mine = bool(creator and creator == current_client.get())
        stale = mine and not approval_matches(self.approval_for(sub, row), approval)
        # the cards drawn before the newest (0: one drawn before cards were numbered)
        seqs = [0, *range(max(1, _card_seq(row, creator) - OLDER_CARDS), _card_seq(row, creator))]
        if stale and any(approval_matches(self.approval_for(sub, row, card_seq=n), approval) for n in seqs):
            raise DeckError(
                "not_newest_card",
                "A newer card for this change is further down the chat, so nothing was sent to "
                "Archidekt from this one. Use the newest card for this change.",
                review_url=f"{self.settings.public_url}/proposals/{proposal_id}",
                state=row["state"],
                version=version,
            )
        if (
            stale
            and version > 1
            and any(
                approval_matches(self.approval_for(sub, row, version=v, card_seq=n), approval)
                for v in range(1, version)
                for n in [*seqs, _card_seq(row, creator)]
            )
        ):
            raise DeckError(
                "changed",
                "There is a newer version of this change than this card shows, so nothing was sent to "
                "Archidekt. Open it again to see what it does now.",
                review_url=f"{self.settings.public_url}/proposals/{proposal_id}",
                state=row["state"],
                version=version,
            )
        if (
            not creator
            or creator != current_client.get()
            or not approval_matches(self.approval_for(sub, row), approval)
        ):
            self._audit(
                "approval_refused", sub=sub, detail={"proposal_id": proposal_id, "deck_id": row["deck_id"]}
            )
            raise DeckError(
                "invalid_approval",
                "This approval code does not belong to this proposal, this app and this account. "
                "Nothing was sent to Archidekt. Only the Approve button on the proposal card or the "
                "review page can apply it.",
                review_url=f"{self.settings.public_url}/proposals/{proposal_id}",
            )
        return row

    def set_approval_for(self, sub: str, proposal_ids: list[str]) -> str:
        """The "Apply all" code of a card for these proposals as they are now (approve.py)."""
        items = []
        for pid in proposal_ids:
            row = self.db.get_proposal(str(pid), sub)
            if row is not None:
                items.append((row["id"], int(row.get("version") or 1)))
        return approval_set_code(
            self.settings.session_secret, sub=sub, client_id=current_client.get() or "", items=items
        )

    def check_set_approval(self, sub: str, proposal_ids: list[str], approval: Any) -> list[str]:
        """The proposals of an "Apply all" press when ``approval`` is the card's code for exactly
        these proposals at the versions it showed, all made by the app calling; otherwise
        ``changed`` (one was amended since the card was drawn) or ``invalid_approval`` (audited),
        and nothing else happens."""
        ids = list(dict.fromkeys(str(p) for p in proposal_ids))[:MAX_SELECTION]
        client = current_client.get()
        rows = [self.db.get_proposal(pid, sub) for pid in ids]
        if not ids or any(
            r is None or not client or r.get("created_by_client") != client or not self.in_apply_all(sub, r)
            for r in rows
        ):
            self._audit("approval_refused", sub=sub, detail={"proposal_ids": ids[:20], "set": True})
            raise DeckError(
                "invalid_approval",
                "This approval code does not belong to these proposals, this app and this account. "
                "Nothing was sent to Archidekt.",
                review_url=f"{self.settings.public_url}/proposals",
            )
        if approval_matches(self.set_approval_for(sub, ids), approval):
            return ids
        if any(int(r.get("version") or 1) > 1 for r in rows if r is not None):
            raise DeckError(
                "changed",
                "There is a newer version of one of these changes than this card shows, so nothing was "
                "sent to Archidekt. Show the waiting changes again.",
                review_url=f"{self.settings.public_url}/proposals",
            )
        self._audit("approval_refused", sub=sub, detail={"proposal_ids": ids[:20], "set": True})
        raise DeckError(
            "invalid_approval",
            "This approval code does not belong to these proposals, this app and this account. "
            "Nothing was sent to Archidekt.",
            review_url=f"{self.settings.public_url}/proposals",
        )

    def waiting_board(self, sub: str, deck_id: str | None, *, in_chat: bool) -> tuple[dict[str, Any], dict]:
        """show_waiting_changes: the waiting changes (of one deck, or all), oldest first,
        with what the card needs in _meta: a one-time code for each of this app's own (Approve
        and Reject on each) and the "Apply all" code for all of them. Another app's changes are
        listed with their review link only."""
        from . import approve

        lines = self.board_lines(sub, deck_id)
        client = current_client.get()
        mine = [x for x in lines if x["own"]]
        apps: dict[str, int] = {}
        for x in lines:
            if not x["own"]:
                label = x.get("created_by") or ""
                name = label[5:].rsplit(" (", 1)[0] if label.startswith("app: ") else "you"
                apps[name] = apps.get(name, 0) + 1
        clean = None if deck_id is None else _clean_deck_id(deck_id)
        data: dict[str, Any] = {
            "ok": True,
            "deck_id": clean,
            "deck_name": lines[0]["deck_name"] if clean and lines else None,
            "waiting": lines,
            "count": len(lines),
            "own_count": len(mine),
            "other_apps": [{"app": a, "count": n} for a, n in sorted(apps.items())],
            "review_all_url": f"{self.settings.public_url}/proposals" + (f"#deck-{clean}" if clean else ""),
            "writes_enabled": self.settings.writes_enabled,
        }
        meta: dict[str, Any] = {}
        if in_chat and client and self.settings.writes_enabled:
            codes = {}
            for x in mine:
                row = self.db.get_proposal(x["proposal_id"], sub)
                if row is not None and self.card_may_approve(row):
                    codes[row["id"]] = self.approval_for(sub, row)
            if codes:
                meta[approve.APPROVALS_META_KEY] = codes
            # A change another app rewrote is approved on its own, not in Apply all
            in_set = [
                pid for pid in codes if self.in_apply_all(sub, self.db.get_proposal(pid, sub) or {"id": pid})
            ]
            if len(in_set) >= 2:
                # said only where an Apply all is offered, by number
                out = [pid for pid in codes if pid not in in_set]
                data["left_out"] = len(out)
                data["left_out_nos"] = [
                    x["board_no"] for x in lines if x["proposal_id"] in out and x.get("board_no")
                ]
                for x in lines:
                    if x["proposal_id"] in out:
                        x["not_in_apply_all"] = True
                meta[approve.APPROVAL_SET_META_KEY] = {
                    "ids": in_set,
                    "code": self.set_approval_for(sub, in_set),
                }
        return data, meta

    def selection_result(self, run: dict[str, Any]) -> dict[str, Any]:
        """A selection's outcome for a card and the assistant: what was applied, not sent (and
        why), failed, and where Undo is (the result page)."""

        def brief(c: dict[str, Any]) -> dict[str, Any]:
            return {
                k: c.get(k)
                for k in ("proposal_id", "board_no", "deck_id", "deck_name", "summary", "why")
                if c.get(k) is not None
            }

        return {
            "ok": True,
            "batch_id": run["batch_id"],
            "state": run["state"],
            "summary": batch.summary_text(run),
            "applied": [brief(c) for c in run["applied"]],
            "not_sent": [brief(c) for c in run["not_sent"]],
            "failed": [brief(c) for c in run["failed"]],
            "result_url": f"{self.settings.public_url}/proposals/applied/{run['batch_id']}",
        }

    async def apply(
        self,
        sub: str,
        proposal_id: str,
        *,
        via: str,
        archidekt_backup: bool | None = None,
        version: int | None = None,
        shown_status: str | None = None,
        shown_status_at: int | None = None,
        logged_via: str | None = None,
    ) -> dict[str, Any]:
        """``logged_via`` is what the audit row records in place of ``via`` (the REST API's bearer applies,
        which are checked as ``mcp`` but logged as ``api``). ``archidekt_backup=False`` (a member's own hand
        edit, the save bar's tick box unticked, or a quick edit on the deck page) skips the extra backup copy
        on Archidekt; the gateway's own snapshot is always taken. The assistant's applies always keep the
        copy. ``version`` is the version the caller showed: the apply claims only that version, and a proposal
        amended since is refused with ``changed`` and nothing sent. ``shown_status`` is the status the chat
        card showed with the press: a change held since is refused with ``now_held`` and nothing sent, so the
        member sees the hold even when the card could not read the change again first; with
        ``shown_status_at`` (when that status was set), so is a held change that was held for a new cause
        since."""
        if not self.settings.writes_enabled:
            raise DeckError("writes_disabled", refusals.WRITES_OFF_KEPT)
        row = self.db.get_proposal(proposal_id, sub)
        if row is None:
            raise DeckError("not_found", "No such proposal for your account.")
        if row["state"] == "applied":
            raise DeckError(
                "already_applied",
                "This proposal was already applied; nothing was sent again.",
                state="applied",
            )
        if row["state"] == "pending":
            self.refuse_while_made(str(row.get("deck_id") or ""))
        # Structural: only the review page's own form passes REVIEW_PAGE, and an account
        # action (kind "action") is refused for every other caller, whichever route it came by.
        review_page = via == REVIEW_PAGE
        if review_page:
            via = "browser"  # otherwise the review page is an ordinary browser apply
        # Only the member's own save that no app changed in any version (G5) may skip the backup
        # copy: by the box sent now, else by the choice its save recorded (keep_no_backup).
        own = row.get("created_by_client") == BROWSER_CLIENT and not self.rewritten_by_another_app(sub, row)
        if own and via == "browser":
            if archidekt_backup is False or (
                archidekt_backup is None and (row.get("result") or {}).get(NO_BACKUP_KEY)
            ):
                row["archidekt_backup"] = False
        creator = row.get("created_by_client")
        # "mcp" is the assistant's own apply_proposal call: allowed only when this member's
        # approval mode (modes.py) lets the assistant apply a proposal of this risk itself.
        # "app" is the member's press on the in-chat card, gated by its one-time code
        # (approve.py); "browser" is a member's own page (hand edits, the JSON API with a
        # session); REVIEW_PAGE is the review page's form; "auto" is never a caller's choice.
        if version is not None and int(row.get("version") or 1) != int(version):
            raise self._changed(row)
        if (
            not review_page
            and row.get("kind") == "action"
            and not (via == "app" and self.card_may_approve(row))
        ):
            # An Archidekt account action (social, folder, collaborator, delete deck) is
            # applied only by the member's own press, whatever the mode: on the signed-in review
            # page, or (0.7.29) on the in-chat card with its one-time code, except a deck
            # deletion (review page only). Never the assistant's apply_proposal and not the
            # REST API. This is the one check that decides it, for every route.
            raise self._review_page_only(sub, row)
        if via == "mcp":
            self._assistant_may_apply(sub, row)
        if via in ("mcp", "app") and (not creator or creator != current_client.get()):
            # Only the assistant that proposed (and showed the user this diff) may apply it over
            # MCP; a proposal left pending cannot be picked up by another connected client.
            raise DeckError(
                "other_client",
                "This proposal was made by a different connected app, so it cannot be applied from "
                "here. The user can press Apply on the review page. Nothing was sent to Archidekt.",
                review_url=f"{self.settings.public_url}/proposals/{proposal_id}",
            )
        if proposal_id in self._running:  # already under way: report it, never start it twice
            return self.describe(sub, proposal_id)

        expect = int(row.get("version") or 1)

        async def run() -> dict[str, Any]:
            async with self.archidekt_slot(sub):  # refused before the claim, so nothing is sent
                return await self._apply_slotted(
                    sub,
                    proposal_id,
                    row,
                    # the audit row keeps the review page apart, for get_proposal's applied_via
                    via=REVIEW_PAGE if review_page else via,
                    logged_via=logged_via,
                    version=expect,
                    shown_status=shown_status,
                    shown_status_at=shown_status_at,
                )

        # The apply runs as its own task: a caller that gives up (an app's tool timeout, a closed
        # browser tab) cannot cancel it halfway. The caller waits up to APPLY_WAIT_SECONDS for the
        # outcome, then gets the proposal as it stands ("applying", with progress).
        task = asyncio.create_task(run())
        self._running[proposal_id] = task

        def done(t: asyncio.Task[Any]) -> None:
            self._running.pop(proposal_id, None)
            self._progress.pop(proposal_id, None)
            if not t.cancelled():
                t.exception()  # retrieved here; the apply already recorded it on the proposal

        task.add_done_callback(done)
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=APPLY_WAIT_SECONDS)
        except TimeoutError:
            return self.describe(sub, proposal_id)

    def _assistant_may_apply(self, sub: str, row: dict[str, Any]) -> None:
        """Refuse (browser_required) the assistant's own apply of a proposal its member's mode
        doesn't let it apply. The risk tier decides only the assistant's own applies of
        ordinary proposals: for an account action it is display only (what the assistant is
        told), because apply has already refused every caller but the review page."""
        risk, why, mode, allowed = self._assistant_policy(sub, row)
        if allowed:
            return
        press = self.press_reason(sub, row) if risk not in modes.MEMBER_ONLY else None
        self._audit(
            "apply_needs_user",
            sub=sub,
            detail={"proposal_id": row["id"], "deck_id": row["deck_id"], "mode": mode, "risk": risk},
        )
        raise DeckError(
            "browser_required",
            _needs_press_message(press) if press else _needs_user_message(mode, why),
            review_url=f"{self.settings.public_url}/proposals/{row['id']}",
            state=row["state"],
            approval_mode=mode,
            risk=risk,
        )

    def _now_held(self, sub: str, row: dict[str, Any]) -> DeckError:
        """A press on a card that showed the change before it was held: nothing is sent, and the
        reply carries the hold and the other lines the card's note is drawn from, as they are now,
        so the card shows no line the hold made untrue."""
        now = self.describe(sub, row["id"])
        return DeckError(
            "now_held",
            NOW_HELD.format(reason=now.get("status_reason") or "").strip(),
            review_url=f"{self.settings.public_url}/proposals/{row['id']}",
            state="pending",
            version=int(row.get("version") or 1),
            status=now.get("status"),
            status_reason=now.get("status_reason") or "",
            status_at=now.get("status_at"),
            press_reason=now.get("press_reason") or "",
            replace_note=now.get("replace_note") or "",
            assistant_may_apply=bool(now.get("assistant_may_apply")),
        )

    async def wait_applied(self, proposal_id: str) -> None:
        """Until a running apply of ``proposal_id`` has ended (its outcome is on the proposal)."""
        task = self._running.get(proposal_id)
        if task is not None:
            await asyncio.gather(asyncio.shield(task), return_exceptions=True)

    def plan_selection(
        self,
        sub: str,
        proposal_ids: list[str],
        versions: dict[str, int] | None = None,
        shown: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """What applying these waiting changes together would do (batch.plan): the confirm.
        Refused when it holds a change and one that replaces it (another app's change, left
        waiting until its replacement is applied): the member picks one."""
        self.db.hold_blocked(sub)
        self._refuse_replaced_pairs(sub, [str(p) for p in proposal_ids][:MAX_SELECTION])
        return batch.plan(self, sub, [str(p) for p in proposal_ids][:MAX_SELECTION], versions, shown)

    def _refuse_replaced_pairs(self, sub: str, picks: list[str]) -> None:
        chosen = set(picks)
        for link in self.db.proposal_links(sub, sorted(chosen)):
            new, old = link["proposal_id"], link["other_id"]
            if link["kind"] != "replaces" or new not in chosen or old not in chosen:
                continue
            row = self.db.get_proposal(old, sub) or {}
            if row.get("state") != "pending":
                continue
            nos = self.db.proposal_board_numbers(sub, [new, old])
            raise DeckError(
                "pick_one",
                PICK_ONE.format(
                    new=f"#{nos[new]}" if nos.get(new) else "One change",
                    old=f"#{nos[old]}" if nos.get(old) else "another",
                    deck=row.get("deck_name") or "this deck",
                ),
            )

    def apply_selection(
        self,
        sub: str,
        proposal_ids: list[str],
        *,
        via: str,
        versions: dict[str, int] | None = None,
        shown: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Start applying a selection (batch.py), the member's one press: each deck in turn, its
        changes oldest first, each re-checked just before it is sent. Returns the run, whose
        ``state`` is "applying" until every change has been tried."""
        if not self.settings.writes_enabled:
            raise DeckError(
                "writes_disabled",
                refusals.WRITES_OFF_KEPT if len(proposal_ids) == 1 else refusals.WRITES_OFF_KEPT_MANY,
            )
        the_plan = self.plan_selection(sub, proposal_ids, versions, shown)
        if not the_plan["count"]:
            only = the_plan["skipped"][0] if len(the_plan["skipped"]) == 1 else {}
            if only.get("replaced_by"):
                # one change its own app replaced: say by which
                old = f"#{only['board_no']}" if only.get("board_no") else "This change"
                raise DeckError("invalid", batch.REPLACED_ALONE.format(old=old, new=only["replaced_by"]))
            raise DeckError("invalid", "None of the selected changes can be applied together.")
        return self.batches.start(self, sub, the_plan, via=via)

    async def undo_preview(
        self, sub: str, snapshot_id: str, *, applied: list[str], after_at: int
    ) -> dict[str, Any]:
        """What "Undo" of an apply (one change or a selection, flow G) would put back: the deck
        read now against the snapshot taken before that apply, and whether it also reverts what
        came after it (later applies through the gateway, or edits made on Archidekt)."""
        snap = self.snapshot(sub, str(snapshot_id))
        deck = await self.get_own_deck(sub, snap["deck_id"])
        wanted = parse_deck(snap["deck"])
        payload, lines = restore_rows(wanted, deck)
        details, detail_lines = details_rows(deck, snapshot_details(wanted))
        later = [
            r
            for r in self.db.list_proposals(sub, 100, deck_id=str(snap["deck_id"]), states=("applied",))
            if r["id"] not in applied and int(r.get("applied_at") or 0) >= int(after_at)
        ]
        last = None
        for pid in applied:
            row = self.db.get_proposal(pid, sub)
            if row is not None and (row.get("result") or {}).get("after_fingerprint"):
                last = row["result"]["after_fingerprint"]
        outside = bool(last) and not later and deck.fingerprint() != last
        return {
            "deck_id": str(snap["deck_id"]),
            "deck_name": deck.name,
            "snapshot_id": snap["id"],
            "rows": lines + detail_lines,
            "nothing": not payload and not details and not category_restore_plan(wanted, deck)[0],
            "later": len(later),
            "outside": outside,
            "warning": undo_warning(len(later), outside),
            # the press is refused if the deck is no longer the one this showed (the same check for Undo)
            "fingerprint": undo_seen(deck),
        }

    async def undo(self, sub: str, snapshot_id: str, *, seen: str | None = None) -> dict[str, Any]:
        """The member's "Undo": a restore of the snapshot, applied at once by their own press (the
        restore is an ordinary proposal, so it can itself be undone). ``seen`` is the deck's
        fingerprint when the preview was shown: a deck changed since is refused before anything
        is made, as the preview's list and warning no longer hold."""
        if seen is not None:
            snap = self.snapshot(sub, str(snapshot_id))
            deck = await self.get_own_deck(sub, snap["deck_id"])
            if undo_seen(deck) != seen:
                raise DeckError("changed", UNDO_CHANGED)
        made = await self.propose_restore(sub, snapshot_id)
        return await self.apply(sub, made["proposal_id"], via="browser")

    async def aclose(self, grace: float = SHUTDOWN_GRACE_SECONDS) -> None:
        """At shutdown: give running applies ``grace`` seconds to finish, then cancel the rest,
        which records each as failed ("interrupted", with what it had sent) before the database
        closes."""
        await self.deck_lists.aclose()
        for task in [*self._prep_tasks, *self._page_refreshing.values()]:
            task.cancel()
        await asyncio.gather(*self._prep_tasks, *self._page_refreshing.values(), return_exceptions=True)
        await self.batches.aclose()  # no further change of a selection starts
        running = list(self._running.values())
        if not running:
            return
        _done, pending = await asyncio.wait(running, timeout=grace)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _apply_slotted(
        self,
        sub: str,
        proposal_id: str,
        row: dict[str, Any],
        *,
        via: str,
        version: int | None = None,
        shown_status: str | None = None,
        shown_status_at: int | None = None,
        logged_via: str | None = None,
    ) -> dict[str, Any]:
        if shown_status is not None:
            # checked just before the claim, with nothing awaited in between, so no hold can be
            # set after this check and before the claim
            self.db.hold_blocked(sub)
            now = self.db.get_proposal(proposal_id, sub)
            held = now is not None and now["state"] == "pending" and now.get("status") == "held"
            # held for a cause the card did not show: every new cause moves status_at
            moved = (
                shown_status_at is not None and int(now.get("status_at") or 0) != shown_status_at
                if held
                else False
            )
            if held and (shown_status != "held" or moved):
                raise self._now_held(sub, now)
        if via == "mcp":
            # The policy is decided again just before the claim, with nothing awaited in between:
            # a hold, a mode change or another app's amend since apply's own check still stops it.
            self.db.hold_blocked(sub)
            now = self.db.get_proposal(proposal_id, sub)
            if now is not None and now["state"] == "pending":
                self._assistant_may_apply(sub, now)
        if not self.db.claim_proposal(proposal_id, sub, expect_version=version):
            now = self.db.get_proposal(proposal_id, sub)
            if now is not None and now["state"] == "pending" and version is not None:
                raise self._changed(now)  # amended between the check and the claim
            raise self._not_pending(sub, proposal_id, "applied")
        # The content is read again after the claim, so what is applied is exactly the
        # version that was claimed (an amend refuses a proposal that is being applied).
        fresh = self.db.get_proposal(proposal_id, sub)
        if fresh is not None:
            row = {**fresh, **({"archidekt_backup": False} if row.get("archidekt_backup") is False else {})}
        lock = self._deck_locks.setdefault(f"{sub}:{row['deck_id']}", asyncio.Lock())
        # What the apply got done so far (snapshot, backup, entries sent of how many, the verify
        # result), kept in the proposal's result whichever way the apply ends, and shown by
        # describe() while it runs.
        progress: dict[str, Any] = {}
        coll = row.get("kind") == "collection"  # the messages say "your collection", not "the deck"
        clone = row.get("kind") == "clone"  # a copy holds the cards: never "no cards were added"
        self._progress[proposal_id] = progress
        # This task's own context: a rate limit is waited out instead of failing the apply halfway,
        # within one allowance for the whole apply
        patient.set(Patience())
        wait_note.set(progress)
        apply_writes.set(progress)  # every write of the apply counts as a change
        try:
            await lock.acquire()  # another apply on this deck may hold it
        except BaseException:  # cancelled while waiting (shutdown): never leave it in applying
            self.db.finish_proposal(proposal_id, state="failed", result={"error": "interrupted"})
            raise
        try:
            try:
                result = await self._apply_claimed(sub, row, progress)
            except DeckError as exc:
                if exc.kind == "backup_failed":  # nothing was sent; the proposal is pending again
                    raise
                await self._drop_unused_backup(sub, proposal_id, progress, source_deck_id=row["deck_id"])
                if exc.kind == "stale" and not _changed_anything(progress):
                    # The proposal no longer does what was reviewed on the deck as it is now.
                    # Nothing was sent, so it waits again as "needs a look" (its snapshot stays).
                    self.db.finish_proposal(
                        proposal_id,
                        state="pending",
                        result={**progress, "error": exc.kind, "detail": str(exc)},
                    )
                    self.db.rebase_proposal(
                        proposal_id,
                        sub,
                        was=row["baseline_fingerprint"],
                        fingerprint=row["baseline_fingerprint"],
                        status="needs_look",
                        reason=getattr(exc, "recheck_reason", None) or "changed",
                    )
                    self._audit(
                        "proposal_apply_stopped",
                        sub=sub,
                        detail={"proposal_id": proposal_id, "error": exc.kind, "state": "pending"},
                    )
                    if isinstance(exc.extra, dict) and "state" not in exc.extra:
                        exc.extra.update(state="pending", status="needs_look")
                    raise
                if exc.kind == "rate_limited" and not _changed_anything(progress):
                    # Archidekt's waits stopped it before anything changed there, so the
                    # proposal is pending again, to be approved again later (re-checked against the
                    # live deck then, with a new snapshot and backup). Its snapshot stays.
                    detail = f"{_sentence(exc)} {_rate_limited_note(progress, pending=True, collection=coll)}"
                    self.db.finish_proposal(
                        proposal_id,
                        state="pending",
                        result={**progress, "error": exc.kind, "detail": detail},
                    )
                    self._audit(
                        "proposal_apply_stopped",
                        sub=sub,
                        detail={"proposal_id": proposal_id, "error": exc.kind, "state": "pending"},
                    )
                    raise DeckError(
                        exc.kind, detail, **{**exc.extra, **_sent(progress), "state": "pending"}
                    ) from exc
                # One plain message for an apply that ran out of patience with Archidekt's 429s
                #: what stopped it, how far it got, and to try again later.
                # A failure after part of the change landed says how much (_partial_note); the
                # stored detail is the same message the caller gets, so the chat card after a slow
                # Approve and the review page say it too.
                partial = (
                    bool(progress.get("sent_entries") or progress.get("deck_id"))
                    and exc.kind != "verify_mismatch"
                )
                detail = (
                    f"{_sentence(exc)} {_rate_limited_note(progress, collection=coll, clone=clone)}"
                    if exc.kind == "rate_limited"
                    else f"{_sentence(exc)} {_partial_note(progress, collection=coll, clone=clone)}"
                    if partial
                    else str(exc)
                )
                self.db.finish_proposal(
                    proposal_id, state="failed", result={**progress, "error": exc.kind, "detail": detail}
                )
                board.settle(self, sub, proposal_id, "failed")  # holds its dependents
                self._audit(
                    "proposal_failed",
                    sub=sub,
                    detail={"proposal_id": proposal_id, "error": exc.kind, **_sent(progress)},
                )
                if exc.kind == "rate_limited":
                    raise DeckError(exc.kind, detail, **{**exc.extra, **_sent(progress)}) from exc
                if partial:
                    raise DeckError(exc.kind, detail, **{**exc.extra, **_sent(progress)}) from exc
                raise
            except BaseException as exc:  # cancelled or unexpected: never leave a proposal in applying
                kind = "internal" if isinstance(exc, Exception) else "interrupted"
                unexpected = (
                    f"Applying failed unexpectedly. {_partial_note(progress, collection=coll, clone=clone)}"
                )
                stored = {**progress, "error": kind, **({"detail": unexpected} if kind == "internal" else {})}
                self.db.finish_proposal(proposal_id, state="failed", result=stored)
                board.settle(self, sub, proposal_id, "failed")
                self._audit(
                    "proposal_failed",
                    sub=sub,
                    detail={"proposal_id": proposal_id, "error": kind, **_sent(progress)},
                )
                if not isinstance(exc, Exception):
                    raise
                logger.exception("apply failed")
                raise DeckError("internal", unexpected) from exc
        finally:
            lock.release()
        self._audit(
            "proposal_applied", sub=sub, detail={"proposal_id": proposal_id, "via": logged_via or via}
        )
        board.settle(self, sub, proposal_id, "applied")  # lifts the holds it caused
        if isinstance(result, dict) and "applied_via" in result:
            # the result was described before the audit row above existed
            result["applied_via"] = self._applied_via(sub, proposal_id)
        return result

    async def _apply_claimed(self, sub: str, row: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        if row.get("kind") == "create_deck":
            return await self._apply_create(sub, row, progress)
        if row.get("kind") == "restore":
            return await self._apply_restore(sub, row, progress)
        if row.get("kind") == "details":
            return await self._apply_details(sub, row, progress)
        if row.get("kind") == "categories":
            return await self._apply_categories(sub, row, progress)
        if row.get("kind") == "custom_cards":
            return await self._apply_custom_cards(sub, row, progress)
        if row.get("kind") == "clone":
            return await self._apply_clone(sub, row, progress)
        if row.get("kind") == "collection":
            return await self._apply_collection(sub, row, progress)
        if row.get("kind") == "action":
            from . import actions

            return await actions.apply(self, sub, row, progress)
        if row.get("kind") == "lists":
            from . import account_lists

            return await account_lists.apply(self, sub, row, progress)
        changes = parse_changes(row["changes"], row_flags=True)  # stored: flags only from the deck page
        deck = await self._current_deck_for(sub, row)
        _before, after, _lines, _before_side, after_side = plan_zones(deck, changes)
        recategorise, _cat_lines = category_plan(deck, changes)
        # Every printing is looked up before anything is written or backed up, so a printing
        # that does not exist (or is another card) refuses the proposal with nothing changed.
        (
            resolve,
            modifiers,
            pinned,
            new_cats,
            side_cats,
            side_pinned,
            side_modifiers,
        ) = await self._resolve_adds(sub, deck, changes, after, after_side)
        payload = build_payload(
            deck,
            after,
            resolve,
            new_cats,
            modifiers,
            pinned,
            recategorise,
            after_side,
            side_cats,
            add_flags(changes, after, "main"),
            add_flags(changes, after_side, "side"),
            side_pinned,
            side_modifiers,
            add_labels(changes, after, "main"),
            add_labels(changes, after_side, "side"),
            row_counts(deck, target_rows(deck, changes)),
        )
        specs, _print_lines = printing_plan_rows(deck, changes)
        payload += await self._printing_entries(sub, deck, specs)
        snapshot_id = self._take_snapshot(sub, row, deck)
        progress["snapshot_id"] = snapshot_id
        backup = await self._backup_shared(sub, row, deck, snapshot_id)
        progress.update(backup)
        await self._rows_unchanged(sub, deck)  # lookups and backup take a while: check again
        for r in row.get("rows") or []:
            if r.get("kind") == "package":
                # the site reads a package it adds with this one read, which counts one import of
                # it on Archidekt: sent here, at the apply, never when the change was proposed
                await self._call(
                    sub, lambda token, *_, i=str(r["package_id"]): self.client.package_cards(token, i)
                )
        await self._send(sub, deck.id, payload, progress)
        verified = await self.get_deck(sub, deck.id)
        if (kept := one_click.get()) is not None:
            kept["verified"] = pickle.loads(pickle.dumps(verified))  # the page draws from it (D-01)
        self.recheck_waiting(sub, verified)  # the deck's other waiting proposals, at no cost
        mismatches = edit_mismatches(deck, changes, verified, specs)
        mismatches += label_mismatches(changes, deck, verified)
        result = {
            "snapshot_id": snapshot_id,
            **backup,
            "sent_entries": len(payload),
            "verified": not mismatches,
            "mismatched_cards": mismatches,
            "after_fingerprint": verified.fingerprint(),  # Undo: did the deck change since?
        }
        if mismatches:
            progress.update(result)  # apply() records it with the error
            raise DeckError(
                "verify_mismatch",
                "Archidekt accepted the request but the deck does not match the proposal for: "
                + ", ".join(mismatches)
                + f". A snapshot ({snapshot_id}) of the deck before the change was kept.",
            )
        self.db.finish_proposal(row["id"], state="applied", result=result)
        return self.describe(sub, row["id"])

    async def _resolve_adds(
        self,
        sub: str,
        deck: Deck,
        changes: list[Change],
        after: dict[str, int],
        after_side: dict[str, int] | None = None,
    ) -> tuple[
        dict[str, int],
        dict[str, str],
        dict[str, dict[str, Any]],
        dict[str, list[str]],
        dict[str, list[str]],
        dict[str, dict[str, Any]],
        dict[str, str],
    ]:
        """Printing ids and finishes for the cards an edit adds, keyed by the spelling used in
        ``after`` (and ``after_side`` for maybeboard adds, whose categories come back as the fifth
        value, their pinned printings as the sixth and their finishes as the seventh, kept apart
        from the deck proper's so a finish on one zone's add never reaches the other's). A pinned
        printing (set code + collector number) is required to exist and to be that card: the
        client raises ``not_found`` otherwise and nothing is added."""
        after_side = after_side or {}

        def key_for(name: str, counts: dict[str, int]) -> str:
            return next((k for k in counts if k.lower() == name.lower()), name)

        have = {c.name.lower() for c in deck.main_cards}
        have_side = {c.name.lower() for c in deck.side_cards}
        adds: dict[str, list[Change]] = {}
        side_adds: dict[str, list[Change]] = {}
        for ch in changes:
            if ch.action == "add" and ch.zone == "side":
                side_adds.setdefault(key_for(ch.card_name, after_side), []).append(ch)
            elif ch.action == "add":
                adds.setdefault(key_for(ch.card_name, after), []).append(ch)
        resolve: dict[str, int] = {}
        modifiers: dict[str, str] = {}
        pinned: dict[str, dict[str, Any]] = {}
        new_cats: dict[str, list[str]] = {}
        side_cats: dict[str, list[str]] = {}
        side_pinned: dict[str, dict[str, Any]] = {}
        side_modifiers: dict[str, str] = {}

        async def pin_for(ch: Change, cats: list[str]) -> dict[str, Any]:
            card = await self._call(
                sub,
                lambda token, *_: self.client.resolve_card(
                    token,
                    ch.card_name,
                    set_code=ch.set_code,
                    collector_number=ch.collector_number,
                    require_printing=True,
                ),
            )
            return {
                "cardid": card["id"],
                "modifier": finish_modifier(
                    card["options"], foil=ch.finish == "Foil", etched=ch.finish == "Etched"
                ),
                "quantity": ch.quantity or 1,
                "categories": cats,
            }

        for key, chs in side_adds.items():
            # an add naming every category (an Undo of a removal) keeps the others beside it
            side_cats[key] = _side_categories(deck, chs)
            for ch in (c for c in chs if c.pinned):
                side_pinned[key] = await pin_for(ch, side_cats[key])
            plain = [c for c in chs if not c.pinned]
            if (
                after_side.get(key, 0) > 0
                and key.lower() not in have_side
                and (key not in side_pinned or plain)
            ):
                card = await self._call(sub, self.client.resolve_card, key)
                resolve[key] = card["id"]
                finish = next((c.finish for c in plain if c.finish), None)
                if finish:
                    side_modifiers[key] = finish_modifier(
                        card["options"], foil=finish == "Foil", etched=finish == "Etched"
                    )
        for key, chs in adds.items():
            cats = _all_categories(chs)
            if cats:
                new_cats[key] = cats
            for ch in (c for c in chs if c.pinned):
                pinned[key] = await pin_for(ch, new_cats.get(key, []))
        for name, qty in after.items():
            plain = [c for c in adds.get(name, []) if not c.pinned]
            if qty > 0 and name.lower() not in have and (name not in pinned or plain):
                card = await self._call(sub, self.client.resolve_card, name)
                resolve[name] = card["id"]
                finish = next((c.finish for c in plain if c.finish), None)
                if finish:
                    modifiers[name] = finish_modifier(
                        card["options"], foil=finish == "Foil", etched=finish == "Etched"
                    )
        return resolve, modifiers, pinned, new_cats, side_cats, side_pinned, side_modifiers

    async def _printing_entries(
        self, sub: str, deck: Deck, specs: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """modifyCards entries for the set_finish and set_printing specs of ``printing_plan_rows``.
        A finish change modifies each row in place with the new modifier. A printing change
        removes the card's rows and adds the chosen printing with the same total and categories;
        the printing is looked up first (``not_found`` refuses the proposal with nothing sent).
        ``spec["cardid"]`` and ``spec["modifier"]`` record what was sent, for verification."""
        rows = {c.relation_id: c for c in deck.cards if c.relation_id is not None}
        out: list[dict[str, Any]] = []
        for spec in specs:
            switch = next(((k, a) for _f, k, a, _w in SWITCHES.values() if k in spec), None)
            if switch is not None:
                # Archidekt's card menu sends the row's companion or flippedDefault in
                # modifications (its site code, read 2026-10-22; not tried live: the re-read checks)
                for rid in spec["relation_ids"]:
                    entry = _entry("modify", rows[rid], rows[rid].quantity)
                    entry["modifications"][switch[1]] = spec[switch[0]]
                    out.append(entry)
                continue
            if "mana_value" in spec:
                # Archidekt's own editor sends modifications.customCmc (its site code, read
                # 2026-10-10); null takes the override off (not verified live: the re-read checks it)
                for rid in spec["relation_ids"]:
                    entry = _entry("modify", rows[rid], rows[rid].quantity)
                    entry["modifications"]["customCmc"] = spec["mana_value"]
                    entry["modifications"]["companion"] = bool(rows[rid].companion)  # kept as it is
                    out.append(entry)
                continue
            if "label" in spec:
                # Archidekt's own editor sends the tag in modifications.label, "Name,#rrggbb"
                # (its site code, read 2026-10-10); taking a tag off sends its empty one, NO_LABEL
                # (not verified live: the re-read after the write checks it)
                for rid in spec["relation_ids"]:
                    entry = _entry("modify", rows[rid], rows[rid].quantity)
                    entry["modifications"]["label"] = spec["label"] or NO_LABEL
                    entry["modifications"]["companion"] = bool(rows[rid].companion)  # kept as it is
                    out.append(entry)
                continue
            if spec.get("set_code"):
                card = await self._call(
                    sub,
                    lambda token, *_, spec=spec: self.client.resolve_card(
                        token,
                        spec["name"],
                        set_code=spec["set_code"],
                        collector_number=spec["collector_number"],
                        require_printing=True,
                    ),
                )
                # The adds go out before the removes, so a failure partway leaves a duplicate
                # row rather than a hole; each old row becomes one new row with its own
                # categories, finish (carried over unless the change names one) and companion flag.
                spec["cardid"] = card["id"]
                spec["modifiers"] = {}
                for r in spec["rows"]:
                    modifier = finish_modifier(
                        card["options"], foil=r["finish"] == "Foil", etched=r["finish"] == "Etched"
                    )
                    if modifier != r["finish"]:
                        raise DeckError(
                            "invalid",
                            f"{spec['name']} ({spec['set_code'].upper()} {spec['collector_number']}) does "
                            f"not come in {r['finish']}; it offers "
                            f"{', '.join(card['options']) or 'no finish'}. Name the finish in the change, "
                            "or pick another printing. Nothing was changed.",
                        )
                    spec["modifiers"][modifier] = spec["modifiers"].get(modifier, 0) + int(r["quantity"])
                    entry = _add_entry(card["id"], int(r["quantity"]), r["categories"], modifier)
                    entry["modifications"]["companion"] = bool(r.get("companion"))
                    # a double-faced card set to show its back keeps that on the new printing
                    entry["modifications"]["flippedDefault"] = bool(r.get("flipped_default"))
                    if r.get("label"):
                        entry["modifications"]["label"] = r["label"]
                    if r.get("custom_cmc") is not None:
                        entry["modifications"]["customCmc"] = r["custom_cmc"]
                    out.append(entry)
                spec["modifier"] = next(iter(spec["modifiers"])) if len(spec["modifiers"]) == 1 else None
                for rid in spec["relation_ids"]:
                    out.append(_entry("remove", rows[rid], 0))
            else:
                spec["modifier"] = spec["finish"]
                for rid in spec["relation_ids"]:
                    entry = _entry("modify", rows[rid], rows[rid].quantity)
                    entry["modifications"]["modifier"] = spec["finish"]
                    entry["modifications"]["companion"] = bool(rows[rid].companion)  # kept as it is
                    out.append(entry)
        return out

    async def _send_batched(
        self, sub: str, deck_id: str, payload: list[dict[str, Any]], progress: dict[str, Any]
    ) -> None:
        """A new deck's add entries, CARD_WRITE_BATCH to a PATCH, counting in ``progress`` how many
        went out. A batch Archidekt refuses as a request (HTTP 400) goes again one card at a time.
        A batch Archidekt took but answered with a body that could not be read (a 2xx) may be in
        the deck already: only its cards the deck does not have go again, one at a time, so none
        is doubled."""
        progress["sent_entries"], progress["of_entries"] = 0, len(payload)
        for start in range(0, len(payload), CARD_WRITE_BATCH):
            batch = payload[start : start + CARD_WRITE_BATCH]
            try:
                await _writing(progress, self._call(sub, self.client.modify_cards, deck_id, batch))
            except DeckError as exc:
                taken = (
                    exc.kind == "contract" and exc.http_status is not None and 200 <= exc.http_status < 300
                )
                if not taken and (exc.kind != "contract" or exc.http_status != 400 or len(batch) == 1):
                    raise
                if taken:
                    logger.warning(
                        "Archidekt's answer to %d cards could not be read; reading the deck", len(batch)
                    )
                    again = await self._entries_missing(sub, deck_id, payload[:start], batch)
                else:
                    logger.warning(
                        "Archidekt refused %d cards in one request; sending them one at a time", len(batch)
                    )
                    again = batch
                progress["sent_entries"] += len(batch) - len(again)
                for entry in again:
                    await _writing(progress, self._call(sub, self.client.modify_card, deck_id, entry))
                    progress["sent_entries"] += 1
                continue
            progress["sent_entries"] += len(batch)

    async def _entries_missing(
        self, sub: str, deck_id: str, before: list[dict[str, Any]], batch: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """The add entries of ``batch`` the new deck has no row for, by printing and first
        category, once the rows of the entries sent ``before`` it are counted out."""

        def key(card_id: Any, categories: Any) -> tuple[Any, str]:
            return card_id, str((list(categories or []) or [""])[0]).casefold()

        deck = await self.get_deck(sub, deck_id)
        have = Counter(key(c.card_id, c.categories) for c in deck.cards)
        for entry in before:
            have[key(entry.get("cardid"), entry.get("categories"))] -= 1
        missing = []
        for entry in batch:
            k = key(entry.get("cardid"), entry.get("categories"))
            if have[k] > 0:
                have[k] -= 1
            else:
                missing.append(entry)
        return missing

    async def _send(
        self, sub: str, deck_id: str, payload: list[dict[str, Any]], progress: dict[str, Any]
    ) -> None:
        """One PATCH per entry, counting in ``progress`` how many went out, so an apply that
        fails partway says how far it got."""
        progress["sent_entries"], progress["of_entries"] = 0, len(payload)
        for entry in payload:
            await _writing(progress, self._call(sub, self.client.modify_card, deck_id, entry))
            progress["sent_entries"] += 1

    async def _current_deck_for(self, sub: str, row: dict[str, Any]) -> Deck:
        """The live deck an apply works on. A deck changed since the proposal was made is fine
        while the proposal, worked out again on it, gives exactly the rows that were reviewed
        (recheck.py); otherwise the apply is refused with nothing sent and the proposal goes
        back to waiting as "needs a look" (``_apply_slotted``)."""
        from . import recheck

        kept = one_click.get()
        read = kept.pop("read", None) if kept is not None else None
        if (
            read is not None
            and read[1] == str(row["deck_id"])
            and time.monotonic() - read[0] < ONE_CLICK_REUSE
        ):
            deck = pickle.loads(read[2])  # the one-click save's own read, owned, a moment ago
        else:
            deck = await self.get_own_deck(sub, row["deck_id"])  # ownership re-checked at apply time
        status, reason = recheck.check(self, sub, row, deck)
        if status == "needs_look":
            raise needs_look_error(reason)
        return deck

    async def _rows_unchanged(self, sub: str, deck: Deck) -> None:
        """Re-read the deck right before writing and refuse if a card row changed meanwhile.
        Rows only: the backup copy made just before may bump the deck's updated time."""
        from . import recheck

        now = await self.get_own_deck(sub, deck.id)
        if now.rows_fingerprint() != deck.rows_fingerprint():
            raise needs_look_error(recheck.REASON_DURING_APPLY)

    def recheck_waiting(
        self, sub: str, deck: Deck, *, renamed: dict[str, str] | None = None
    ) -> dict[str, str]:
        """Check every waiting proposal of the member on this deck against it (after an
        apply, on a deck page view, on a new proposal and in the deck sync) and record the
        outcome: "updated" (its baseline moves to this deck) or "needs_look" with the reason.
        ``renamed`` (old name -> new name, after a categories apply or a restore that renamed
        categories): a card edit naming an old name needs a look whatever its rows say
        (recheck.renamed_reason). Held proposals keep their hold. Local only: no Archidekt
        request. Returns id -> status for the proposals whose status was recorded."""
        from . import recheck

        out: dict[str, str] = {}
        try:
            board = self.db.pending_board(sub, deck.id)
        except Exception:  # a check must never break the page or apply that triggered it
            logger.exception("re-check of waiting proposals failed")
            return out
        for line in board:
            row = self.db.get_proposal(line["id"], sub)
            # a commander change saved before its row listed the commanders lists them from here on
            # (the board's card list reads them), now that the deck is at hand
            filled = row and fill_commander_names(row.get("rows"), deck, row.get("changes"))
            if (
                row
                and filled
                and self.db.fill_proposal_rows(row["id"], sub, filled, int(row.get("version") or 1))
            ):
                row["rows"] = filled
            if line.get("status") == "held":
                continue
            if row is None or not row.get("baseline_fingerprint"):
                continue
            try:
                status, reason = recheck.check(self, sub, row, deck)
            except Exception:
                logger.exception("re-check of a waiting proposal failed")
                continue
            was_renamed = recheck.renamed_reason(row, renamed or {})
            if was_renamed:
                status, reason = "needs_look", was_renamed
            if status is None:
                continue
            if self.db.rebase_proposal(
                row["id"],
                sub,
                was=row["baseline_fingerprint"],
                fingerprint=deck.fingerprint(),
                status=status,
                reason=reason,
                version=row.get("version"),
            ):
                out[row["id"]] = status
        return out

    def _take_snapshot(self, sub: str, row: dict[str, Any], deck: Deck) -> str:
        shared = batch.shared_snapshots.get()
        if shared is not None and str(deck.id) in shared:
            # One snapshot per deck per selection, taken before its first change touched it
            snapshot_id = shared[str(deck.id)]["snapshot_id"]
            self.db.finish_proposal(row["id"], state="applying", snapshot_id=snapshot_id)
            return snapshot_id
        snapshot_id = secrets.token_urlsafe(12)
        saved = self.db.save_snapshot(
            snapshot_id,
            owner_sub=sub,
            deck_id=deck.id,
            proposal_id=row["id"],
            fingerprint=deck.fingerprint(),
            deck=deck.raw,
            while_applying=True,
        )
        if not saved:
            # The member deleted their data (or this proposal) while the apply was running.
            raise DeckError(
                "not_found",
                "This proposal was deleted, with your gateway data, while it was being applied. "
                "Nothing was sent to Archidekt.",
            )
        self.db.finish_proposal(row["id"], state="applying", snapshot_id=snapshot_id)
        if shared is not None:
            shared[str(deck.id)] = {"snapshot_id": snapshot_id}
        return snapshot_id

    async def _drop_unused_backup(
        self, sub: str, proposal_id: str, progress: dict[str, Any], *, source_deck_id: str
    ) -> None:
        """An apply that failed before it changed the deck deletes the backup copy it made a moment
        earlier on Archidekt: the deck is as it was, so the copy would only pile up in the backup
        folder. When the deck may have changed the copy is kept, as the readable way back. The
        gateway's own snapshot is always kept. Only the copy this apply made is ever deleted: never
        the deck the apply was for, and only the copy its snapshot records.

        While Archidekt refuses calls for a while (a pause running now, or the delete meets a
        429, a 5xx, a network error or the breaker) the delete is queued for backup_delete_loop
        instead, with no extra waiting here; any other refusal keeps the copy."""
        backup_id = progress.get("backup_deck_id")
        if not backup_id or _changed_anything(progress):
            return
        if progress.get("backup_reused") or self.db.backup_users(sub, str(backup_id)) > 1:
            return  # a copy an earlier edit also relies on is never deleted for this one
        snap = self.db.get_snapshot(str(progress.get("snapshot_id") or ""), sub)
        shared = batch.shared_snapshots.get()
        for made in (shared or {}).values():
            if str((made.get("backup") or {}).get("backup_deck_id")) == str(backup_id):
                made.pop("backup")  # the selection's next change on this deck makes its own
        if (
            not str(backup_id).isdigit()
            or str(backup_id) == str(source_deck_id)
            or snap is None
            or str(snap.get("backup_deck_id") or "") != str(backup_id)
        ):
            self._audit(
                "backup_delete_skipped",
                sub=sub,
                detail={"proposal_id": proposal_id, "backup_deck_id": str(backup_id)},
            )
            return
        if self.client.pacer.paused():  # the call would only be refused: queue it straight away
            self._queue_backup_delete(sub, proposal_id, progress, source_deck_id, why="paused")
            return
        # No waiting in the apply for this delete: an allowance already used up makes a 429
        # fail at once, and the delete is queued. It is still the apply's 429, so it does not count
        # towards the breaker.
        spent = Patience()
        spent.waits = RATE_LIMIT_WAITS
        allowance = patient.set(spent)
        try:
            with backup_writes():
                await self._call(sub, self.client.delete_deck, str(backup_id))
        except DeckError as exc:
            if exc.kind in BACKUP_DELETE_TRANSIENT:
                self._queue_backup_delete(sub, proposal_id, progress, source_deck_id, why=exc.kind)
                return
            progress["backup_note"] = BACKUP_KEPT
            self._audit(
                "backup_delete_failed",
                sub=sub,
                detail={"proposal_id": proposal_id, "backup_deck_id": backup_id, "error": exc.kind},
            )
            return
        finally:
            patient.reset(allowance)
        self.db.clear_backup_everywhere(sub, str(backup_id))
        progress.pop("backup_deck_id", None)
        progress.pop("backup_url", None)
        progress["backup_deleted"] = True
        self._audit(
            "backup_deleted", sub=sub, detail={"proposal_id": proposal_id, "backup_deck_id": backup_id}
        )

    def _queue_backup_delete(
        self, sub: str, proposal_id: str, progress: dict[str, Any], source_deck_id: str, *, why: str
    ) -> None:
        """Keep the delete of an unused backup copy for later, in the database so it outlives
        a restart and a re-apply of the proposal (which rewrites its result)."""
        due = int(time.time() + self.client.pacer.pause_left()) + BACKUP_DELETE_MARGIN
        backup_id = str(progress["backup_deck_id"])
        self.db.queue_backup_delete(
            str(progress["snapshot_id"]),
            sub=sub,
            backup_deck_id=backup_id,
            deck_id=str(source_deck_id),
            proposal_id=proposal_id,
            due=due,
            expect=self._backups_made.pop(backup_id, None) or self.db.backup_made(backup_id),
        )
        progress["backup_note"] = BACKUP_LATER
        self._audit(
            "backup_delete_queued",
            sub=sub,
            detail={"proposal_id": proposal_id, "backup_deck_id": backup_id, "error": why, "due": due},
        )

    async def backup_delete_loop(self) -> None:
        """Every BACKUP_DELETE_EVERY seconds, try at most one queued backup delete."""
        while True:
            await asyncio.sleep(BACKUP_DELETE_EVERY)
            try:
                await self.delete_one_queued_backup()
            except Exception:  # never let one bad pass end the loop
                logger.exception("queued backup delete failed")

    async def delete_one_queued_backup(self) -> dict[str, Any] | None:
        """One pass of the later deletes: the one queued delete due first, across all
        members, never while the client is paused or writes are off. It runs the same checks as
        _drop_unused_backup, through the member's own link as a normal call (outside an apply its
        429 counts towards the breaker). Returns what it did, for the log and the tests."""
        if not self.settings.writes_enabled or self.client.pacer.paused():
            return None
        job = self.db.next_backup_delete(int(time.time()))
        if job is None:
            return None
        sub, sid = job["sub"], job["snapshot_id"]
        backup_id, deck_id, pid = job["backup_deck_id"], job["deck_id"], job["proposal_id"]
        snap = self.db.get_snapshot(sid, sub)
        expect = job.get("expect") if isinstance(job.get("expect"), dict) else {}
        trim = bool(expect.get("trim"))
        if trim and not self._over_backup_limit(sub, deck_id, backup_id):
            self.db.drop_backup_delete(sid)
            self._audit(
                "backup_delete_skipped",
                sub=sub,
                detail={"backup_deck_id": backup_id, "later": True, "within_limit": True},
            )
            return {"skipped": sid, "within_limit": True}
        proposal = self.db.get_proposal(pid, sub) if pid else None
        changed = (
            proposal is not None
            and proposal.get("snapshot_id") == sid
            and _changed_anything(proposal.get("result") or {})
        )
        if (
            not backup_id.isdigit()
            or backup_id == deck_id
            or snap is None
            or str(snap.get("backup_deck_id") or "") != backup_id
            or str(snap.get("deck_id") or "") != deck_id
            or changed
        ):
            self.db.drop_backup_delete(sid)
            self._audit(
                "backup_delete_skipped",
                sub=sub,
                detail={"proposal_id": pid, "backup_deck_id": backup_id, "later": True},
            )
            return {"skipped": sid}
        attempts = int(job["attempts"]) + 1
        link = self.db.get_link(sub) or {}
        linked = _link_identity(link)

        def relinked() -> bool:
            # A relink drops the member's queued deletes in the same step as it
            # stores the new link, so "still queued" sees any relink, even one within the same
            # second as the old link (created_at is whole seconds); the link itself is compared too
            # (an unlink, or another account).
            return (
                not self.db.backup_delete_queued(sid) or _link_identity(self.db.get_link(sub) or {}) != linked
            )

        gone = False
        try:
            # One read of the copy first; a 404 there means it is already gone.
            copy = await self._call(sub, self.client.get_deck, backup_id)
            if relinked():
                return {"relinked": sid}  # relinked meanwhile: the relink dropped this delete
            # Only a copy the linked account owns, by the Archidekt user id stored on
            # the link; a missing id on either side keeps the copy.
            user_id = str(link.get("archidekt_user_id") or "")
            if not user_id or not copy.owner_id or str(copy.owner_id) != user_id:
                return self._give_up_backup_delete(job, attempts, "not_owner")
            if not _backup_as_made(copy, job):
                if trim:  # the member changed it: kept, and no longer counted
                    self.db.record_backup_made(
                        backup_id, {**(self.db.backup_made(backup_id) or {}), "kept": True}
                    )
                return self._give_up_backup_delete(job, attempts, "copy_changed")
            await self._call(sub, self.client.delete_deck, backup_id)
        except DeckError as exc:
            if relinked():
                return {"relinked": sid}  # an answer for another link proves nothing
            if exc.kind == "not_found":  # already gone from Archidekt: nothing left to do
                gone = True
            elif exc.kind in BACKUP_DELETE_TRANSIENT and attempts < BACKUP_DELETE_TRIES:
                return self._retry_backup_delete(job, attempts, exc.kind)
            else:
                return self._give_up_backup_delete(job, attempts, exc.kind)
        except Exception:
            # An unexpected error is retried on the same schedule, never left due
            # (where it would hold up every other queued delete), and counts towards the limit.
            logger.exception("queued backup delete %s failed unexpectedly", sid)
            if attempts < BACKUP_DELETE_TRIES:
                return self._retry_backup_delete(job, attempts, "internal")
            return self._give_up_backup_delete(job, attempts, "internal")
        self.db.drop_backup_delete(sid)
        self.db.clear_backup_everywhere(sub, backup_id)
        self._reword_backup_note(sub, pid, sid, None)
        self._audit(
            "backup_deleted",
            sub=sub,
            detail={
                "proposal_id": pid,
                "backup_deck_id": backup_id,
                "later": True,
                **({"already_gone": True} if gone else {}),
            },
        )
        return {"deleted": sid, **({"already_gone": True} if gone else {})}

    def _retry_backup_delete(self, job: dict[str, Any], attempts: int, error: str) -> dict[str, Any]:
        """A later delete's try failed for a passing reason: try again after at least the pause left
        and at least BACKUP_DELETE_BACKOFF seconds, doubling with each failed try."""
        sid = job["snapshot_id"]
        wait = max(
            self.client.pacer.pause_left() + BACKUP_DELETE_MARGIN,
            BACKUP_DELETE_BACKOFF * 2 ** (attempts - 1),
        )
        due = int(time.time() + wait)
        self.db.reschedule_backup_delete(sid, due=due, attempts=attempts)
        self._audit(
            "backup_delete_failed",
            sub=job["sub"],
            detail={
                "proposal_id": job["proposal_id"],
                "backup_deck_id": job["backup_deck_id"],
                "error": error,
                "later": True,
                "attempts": attempts,
                "due": due,
            },
        )
        return {"retry": sid, "attempts": attempts, "due": due}

    def _give_up_backup_delete(self, job: dict[str, Any], attempts: int, error: str) -> dict[str, Any]:
        """Stop trying to delete a queued backup copy: the copy stays in the backup folder,
        the proposal's message says so again, and the audit says why."""
        sid, sub, pid = job["snapshot_id"], job["sub"], job["proposal_id"]
        self.db.drop_backup_delete(sid)
        backup_id = str(job["backup_deck_id"])
        if error != "copy_changed" and backup_id.isdigit():
            # The copy stays where it is and is no longer counted, so
            # no later trim queues it again (one more Archidekt read and give-up per new copy),
            # and its restore point is no longer held past the newest kept.
            record = self.db.backup_made(backup_id) or {"sub": sub, "deck_id": str(job["deck_id"])}
            self.db.record_backup_made(backup_id, {**record, "kept": True, "given_up": error})
        self._reword_backup_note(sub, pid, sid, BACKUP_KEPT)
        self._audit(
            "backup_delete_given_up",
            sub=sub,
            detail={
                "proposal_id": pid,
                "backup_deck_id": job["backup_deck_id"],
                "error": error,
                "attempts": attempts,
            },
        )
        return {"given_up": sid, "error": error}

    def _drop_backup_deletes_on_relink(self, sub: str) -> None:
        """A queued delete does not record which Archidekt account made the copy (not
        yet), so after any relink an answer to it could be the other account's. The
        member's queued deletes are given up; the copies stay in the backup folder."""
        for job in self.db.queued_backup_deletes():
            if job.get("sub") == sub:
                self._give_up_backup_delete(job, int(job.get("attempts") or 0), "relinked")

    def _reword_backup_note(self, sub: str, proposal_id: str, snapshot_id: str, note: str | None) -> None:
        """A queued delete ended: the proposal's stored result, if it is still the one from the
        apply that queued it, no longer says the copy will be deleted by itself. ``note`` None: the
        copy is gone, so the sentence goes; otherwise the sentence becomes ``note``."""

        def reword(result: Any) -> dict[str, Any] | None:
            if (
                not isinstance(result, dict)
                or str(result.get("snapshot_id") or "") != snapshot_id
                or result.get("backup_note") != BACKUP_LATER
            ):
                return None
            out = dict(result)
            if note is None:
                for key in ("backup_note", "backup_deck_id", "backup_url"):
                    out.pop(key, None)
                out["backup_deleted"] = True
            else:
                out["backup_note"] = note
            if isinstance(out.get("detail"), str):
                out["detail"] = (
                    out["detail"].replace(f" {BACKUP_LATER}", "").replace(BACKUP_LATER, "")
                    if note is None
                    else out["detail"].replace(BACKUP_LATER, note)
                )
            return out

        if proposal_id:
            self.db.edit_proposal_result(proposal_id, sub, reword)

    async def _backup_shared(
        self, sub: str, row: dict[str, Any], deck: Deck, snapshot_id: str
    ) -> dict[str, Any]:
        """The backup copy before a change, which the selection's later changes to the same
        deck share: the copy the first one made is remembered for them."""
        made = await self._backup_on_archidekt(sub, row, deck, snapshot_id)
        shared = (batch.shared_snapshots.get() or {}).get(str(deck.id))
        if shared is not None and made.get("backup_deck_id") and not made.get("backup_shared"):
            shared["backup"] = dict(made)
        return made

    async def _backup_on_archidekt(
        self, sub: str, row: dict[str, Any], deck: Deck, snapshot_id: str
    ) -> dict[str, Any]:
        """Before any write, keep a private copy of the deck in the user's backup folder on
        Archidekt (a readable backup next to the gateway's own snapshot). When the copy cannot be
        made the edit does not proceed: the proposal goes back to pending so the user can retry."""
        if not self.live.get("archidekt_backups"):
            return {}
        if row.get("archidekt_backup") is False:
            return {"archidekt_backup": "skipped"}  # the member's own choice at save time
        shared = (batch.shared_snapshots.get() or {}).get(str(deck.id))
        if shared is not None and shared.get("backup"):
            # The selection's first change on this deck made the copy; the later ones share it
            # (without its id, so a later change that fails never deletes it)
            return {"backup_url": shared["backup"].get("backup_url", ""), "backup_shared": True}
        reason = f"before proposal {row['id']} changed this deck ({row.get('kind') or 'edit'})"
        try:
            reused = await self._reuse_backup(sub, deck, snapshot_id)
            if reused:
                return reused
            made = await self._backup_copy(sub, deck, snapshot_id, reason=reason)
        except DeckError as exc:
            # Kept on the proposal so get_proposal and the review page say why it is pending again,
            # also when the apply was answered "applying" before the backup failed.
            self.db.finish_proposal(
                row["id"], state="pending", result={"error": "backup_failed", "detail": str(exc)}
            )
            self._audit(
                "backup_failed",
                sub=sub,
                detail={"proposal_id": row["id"], "deck_id": deck.id, "error": exc.kind},
            )
            raise DeckError(
                "backup_failed",
                f"The backup copy of '{deck.name}' could not be made on Archidekt ({exc}). Nothing was "
                "changed; the proposal is still pending, so it can be applied again once Archidekt "
                "answers.",
            ) from exc
        self._trim_backups(sub, deck.id)
        return made

    async def _reuse_backup(self, sub: str, deck: Deck, snapshot_id: str) -> dict[str, Any] | None:
        """The deck's newest backup copy, recorded on this snapshot too, when the sharing rule lets
        this edit share it: the deck is unchanged since the copy was made, or the copy is under
        BACKUP_SHARE_SECONDS old. The copy is read once first (one call instead of the three a new
        copy costs) and shared only while it is still as the gateway made it. None: make a new one."""
        copies = self.db.deck_backup_copies(sub, deck.id)
        if not copies:
            return None
        newest = copies[0]
        backup_id = str(newest["backup_deck_id"])
        made = self.db.backup_made(backup_id)
        if not made or made.get("kept") or not backup_id.isdigit() or backup_id == str(deck.id):
            return None
        if self.db.backup_copy_queued(sub, backup_id):
            return None  # an unused or over-limit copy waiting to be deleted is never shared
        made_at = int(made.get("made_at") or 0)
        recent = 0 <= time.time() - made_at < BACKUP_SHARE_SECONDS
        if not recent:
            origin = self.db.get_snapshot(str(newest["snapshot_id"]), sub)
            if origin is None or _content_key(parse_deck(origin["deck"])) != _content_key(deck):
                return None
        try:
            copy = await self._call(sub, self.client.get_deck, backup_id)
        except DeckError:
            return None  # gone or unreadable: make a new copy
        link = self.db.get_link(sub) or {}
        user_id = str(link.get("archidekt_user_id") or "")
        if not user_id or str(copy.owner_id or "") != user_id or not _backup_as_made(copy, {"expect": made}):
            return None
        url = str(newest.get("backup_url") or "")
        self.db.set_snapshot_backup(snapshot_id, backup_deck_id=backup_id, backup_url=url)
        return {
            "backup_deck_id": backup_id,
            "backup_url": url,
            "backup_reused": True,
            "backup_made_at": made_at,
            "backup_reason": "recent" if recent else "unchanged",
            "backup_shared": backup_shared_note(made_at, recent=recent),
        }

    def _trim_backups(self, sub: str, deck_id: str) -> list[str]:
        """Queue the deletes of this deck's backup copies past "Backup copies kept per deck":
        the newest are kept, the rest go through the later-delete queue, one a minute
        for everyone, with its checks (writes switch, owner, the copy as made). A copy the member
        changed on Archidekt is kept and not counted. Only copies the gateway recorded on its own
        snapshots, with its record of how it made them, are ever considered: a copy made before
        0.7.29 has no such record and is left alone, not counted. Returns the
        copies queued."""
        keep = int(self.live.get("archidekt_backup_copies"))
        queued: list[str] = []
        counted = 0
        copies = [
            (c, self.db.backup_made(str(c["backup_deck_id"])) or {})
            for c in self.db.deck_backup_copies(sub, deck_id)
        ]
        for c, made in copies:
            backup_id, sid = str(c["backup_deck_id"]), str(c["snapshot_id"] or "")
            if not made or made.get("kept"):
                continue
            counted += 1
            if counted <= keep or not backup_id.isdigit() or backup_id == str(deck_id) or not sid:
                continue
            if self.db.backup_delete_queued(sid):
                continue
            self.db.queue_backup_delete(
                sid,
                sub=sub,
                backup_deck_id=backup_id,
                deck_id=str(deck_id),
                proposal_id=None,
                due=int(time.time()) + BACKUP_DELETE_MARGIN,
                expect={**made, "trim": True},
            )
            queued.append(backup_id)
        if queued:
            self._audit("backup_trim_queued", sub=sub, detail={"deck_id": str(deck_id), "copies": queued})
        return queued

    def _over_backup_limit(self, sub: str, deck_id: str, backup_id: str) -> bool:
        """Whether a copy queued by _trim_backups is still past the limit now (the limit may have
        been raised, or newer copies deleted, since)."""
        keep = int(self.live.get("archidekt_backup_copies"))
        counted = 0
        for c in self.db.deck_backup_copies(sub, deck_id):
            made = self.db.backup_made(str(c["backup_deck_id"])) or {}
            if not made or made.get("kept"):
                continue
            counted += 1
            if str(c["backup_deck_id"]) == str(backup_id):
                return counted > keep
        return False

    async def _backup_copy(
        self, sub: str, deck: Deck, snapshot_id: str, *, reason: str, folder: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """Copy ``deck`` into the backup folder and record the copy on the snapshot. Raises the
        Archidekt error as a DeckError; callers decide what that means for their write. ``folder``:
        the backup folder a delete box found moments ago (prepare_delete), else it is looked up."""
        name = backup_name(deck.name, time.time())[:200]
        description = (
            f"Automatic backup made by the MTG Assistant Gateway {reason}. Gateway snapshot "
            f"{snapshot_id}. To undo the change, ask the assistant to restore snapshot {snapshot_id}, or "
            f"copy this deck back by hand. Original deck: https://archidekt.com/decks/{deck.id}"
        )
        folder_name = self.backup_folder
        with backup_writes():  # the backup copy is not a change to the deck
            if folder is None:
                folder = await self._call(
                    sub,
                    lambda token, *_: self.client.ensure_folder(token, name=folder_name),
                )
            self.db.record_backup_folder(sub, folder["id"])
            # How long the gateway's own writes to the copy took (the copy POST and its
            # description PATCH, which come back together): Archidekt's createdAt and every
            # updatedAt those writes set fall inside it, whatever Archidekt's clock says.
            started = time.monotonic()
            copy = await self._call(
                sub,
                lambda token, *_: self.client.backup_deck(
                    token, deck, name=name, folder_id=str(folder["id"]), description=description
                ),
            )
            written_within = math.ceil(time.monotonic() - started)
        backup = {"backup_deck_id": str(copy["id"]), "backup_url": str(copy.get("url") or "")}
        made = {
            "name": name,
            "description": description,
            "folder_id": str(folder["id"]),
            "written_within": written_within,
        }
        # The exact record goes into the database with the snapshot's link to the copy,
        # in one transaction, so a restart never loses it and a copy made by this version is
        # never judged by the rule for older copies
        self.db.set_snapshot_backup(
            snapshot_id,
            **backup,
            made={
                **made,
                "made_at": int(time.time()),
                "deck_id": str(deck.id),
                "snapshot_id": snapshot_id,
                "sub": sub,
            },
        )
        self._backups_made[str(copy["id"])] = made
        while len(self._backups_made) > BACKUPS_REMEMBERED:
            self._backups_made.pop(next(iter(self._backups_made)))
        return backup

    COLLECTION_TARGET = "collection"
    COLLECTION_NAME = "Your Archidekt collection"

    @_dedupe
    async def propose_collection(
        self,
        sub: str,
        adds: list[dict[str, Any]],
        removes: list[dict[str, Any]],
        *,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """A proposal (kind ``collection``) that adds cards to, or removes cards from, the member's
        Archidekt Collection. Like every write the assistant can start it waits for the member's
        approval (or their approval mode); the review rows are ``add`` and ``remove`` rows, so the
        same tiers apply as to a deck edit. ``adds`` are the items collection.add accepts (already
        validated), ``removes`` are ``{"id", "name", "quantity"}`` records resolved by the caller."""
        self._room_for_proposal(sub)
        if not adds and not removes:
            raise DeckError("invalid", "nothing to change: give cards to add or remove")
        rows = [
            _row(
                "add",
                name=str(it.get("name") or (it.get("card") or {}).get("name") or "card"),
                qty=int(it.get("quantity") or 1),
                printing=_printing_label(it),
            )
            for it in adds
        ] + [
            _row("remove", name=str(r["name"]), qty=int(r["quantity"]), printing=r.get("printing"))
            for r in removes
        ]
        pid = secrets.token_urlsafe(12)
        self._save_proposal(
            {
                "id": pid,
                "owner_sub": sub,
                "kind": "collection",
                "deck_id": self.COLLECTION_TARGET,
                "deck_name": self.COLLECTION_NAME,
                "baseline_fingerprint": "",
                "changes": {
                    **{k: v for k, v in (extra or {}).items() if v},
                    "add": adds,
                    "remove": [{"id": r["id"], "quantity": r["quantity"]} for r in removes],
                },
            },
            rows,
        )
        self._audit("proposal_created", sub=sub, detail={"proposal_id": pid, "kind": "collection"})
        return self.describe(sub, pid)

    async def _apply_collection(
        self, sub: str, row: dict[str, Any], progress: dict[str, Any]
    ) -> dict[str, Any]:
        if self.collection_apply is None:
            raise DeckError("unavailable", "the collection pages are not loaded on this gateway")
        changes = row.get("changes") or {}
        applied = await self.collection_apply(sub, changes, progress)
        # verified comes from collection_apply's read-back of the records it touched
        result = {**applied, "verified": applied.get("verified") is True, "snapshot_id": None}
        self.db.finish_proposal(row["id"], state="applied", result=result)
        return self.describe(sub, row["id"])

    @_dedupe
    async def propose_lists(
        self, sub: str, kind: str, changes: Any, *, picture: dict[str, str] | None = None
    ) -> dict[str, Any]:
        """A proposal (kind ``lists``) for changes to one of the member's account-wide Archidekt
        lists: account_lists.py."""
        from . import account_lists

        return await account_lists.propose(self, sub, kind, changes, picture=picture)

    @_dedupe
    async def propose_action(self, sub: str, action: str, **params: Any) -> dict[str, Any]:
        """A proposal (kind ``action``) for one Archidekt account action: actions.py."""
        from . import actions

        return await actions.propose(self, sub, action, params)

    @_dedupe
    async def propose_clone(
        self, sub: str, deck_id: str, name: str | None = None, *, folder_id: int | None = None
    ) -> dict[str, Any]:
        """A proposal (kind ``clone``) that copies any deck the member can read (their own, a
        public deck, a precon) into a new private deck in their root folder, or in ``folder_id``
        (one of the member's own folders, checked here), the way Archidekt's Clone deck button
        does ("Copy of - " prefix by default). Applied with the same copy route the backups use
        (verified live 2026-10-05 on the member's own deck; a copy of someone else's public deck
        is the same request and has not been exercised live); the copy keeps every card,
        quantity, category and finish."""
        deck_id = _clean_deck_id(deck_id)
        self._room_for_proposal(sub)
        deck = await self.get_any_deck(sub, deck_id)
        new_name = clean_text(name or "") or f"Copy of - {deck.name}"
        if len(new_name) > 200:
            raise DeckError("invalid", "name must be up to 200 characters")
        changes: dict[str, Any] = {"name": new_name}
        folder_name = None
        if folder_id is not None:
            if isinstance(folder_id, bool) or not str(folder_id).isdigit():
                raise DeckError("invalid", "That folder is not in your Archidekt account.")
            _info, folder = await self._folder_in(sub, int(folder_id))
            changes["folder_id"] = int(folder["id"])
            folder_name = str(folder.get("name") or "")
        pid = secrets.token_urlsafe(12)
        cards = sum(c.quantity for c in deck.cards)
        self._save_proposal(
            {
                "id": pid,
                "owner_sub": sub,
                "kind": "clone",
                "deck_id": deck.id,
                "deck_name": deck.name,
                "baseline_fingerprint": deck.fingerprint(),
                "changes": changes,
            },
            [_row("clone", name=new_name, source=deck.name, cards=cards, folder=folder_name)],
        )
        self._audit(
            "proposal_created", sub=sub, detail={"proposal_id": pid, "deck_id": deck.id, "kind": "clone"}
        )
        return self.describe(sub, pid)

    async def _apply_clone(self, sub: str, row: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        # The source may be someone else's public deck, so it is read, not owned; a change to it
        # since the proposal still stops the copy (the review showed another list).
        deck = await self.get_any_deck(sub, row["deck_id"])
        if deck.fingerprint() != row["baseline_fingerprint"]:
            raise needs_look_error(None)
        name = clean_text(str((row.get("changes") or {}).get("name") or "")) or f"Copy of - {deck.name}"
        folder_id = (row.get("changes") or {}).get("folder_id")
        folder = str(int(folder_id)) if folder_id is not None else None
        copy = await self._call(
            sub, lambda token, *_: self.client.copy_deck(token, deck, name=name, folder_id=folder)
        )
        # Recorded at once, as _apply_create records its new deck: a stop from here on
        # has made the copy, so the proposal stays failed and is never copied a second time.
        progress["deck_id"] = str(copy["id"])
        verified = await self.get_deck(sub, str(copy["id"]))
        want = deck.counts_by_name()
        mismatches = _mismatches(verified.counts_by_name(), want)
        result = {
            "deck_id": str(copy["id"]),
            "deck_url": str(copy.get("url") or f"https://archidekt.com/decks/{copy['id']}"),
            "name": verified.name or name,
            "verified": not mismatches,
            "mismatched_cards": mismatches,
        }
        if mismatches:
            progress.update(result)
            raise DeckError(
                "verify_mismatch",
                f"Archidekt made the copy ({result['deck_url']}) but it does not match the source for: "
                + ", ".join(mismatches),
            )
        self.db.finish_proposal(row["id"], state="applied", result=result)
        return self.describe(sub, row["id"])

    async def _apply_restore(self, sub: str, row: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        snap = self.db.get_snapshot(str(row["changes"]["snapshot_id"]), sub)
        if snap is None:
            raise DeckError("not_found", "The snapshot this proposal restores no longer exists.")
        wanted = parse_deck(snap["deck"])
        deck = await self._current_deck_for(sub, row)
        payload, _lines = restore_steps(wanted, deck)
        bodies, _cat_rows = category_restore_plan(wanted, deck)
        stuck: list[str] = []
        snapshot_id = self._take_snapshot(sub, row, deck)
        progress["snapshot_id"] = snapshot_id
        backup = await self._backup_shared(sub, row, deck, snapshot_id)
        progress.update(backup)
        await self._rows_unchanged(sub, deck)  # the backup takes a while: check again before writing
        if bodies:
            # The categories first, one write at a time by record id (names and flags), then the
            # cards worked out again on the deck as Archidekt reads back: had a rename not taken
            # its cards along (only a live test can say), they are moved back by card writes.
            await self._categories_unchanged(sub, deck)
            await self._send_categories(
                sub, deck.id, [b for b in bodies if not b.get("after_cards")], progress
            )
            after = await self.get_own_deck(sub, deck.id)
            try:
                payload, _lines = restore_steps(wanted, after)
            except DeckError as exc:
                # written already: what can't be worked out now is reported with the mismatches
                # (N-2), and no card is moved on a deck that no longer matches the plan
                payload, stuck = [], [_sentence(exc).rstrip(".")]
        if payload:
            await self._send(sub, deck.id, payload, progress)
        # categories added since the snapshot go once the cards are back, each only if empty then
        # only those the member reviewed: one made since the review is no row they saw
        reviewed = {
            r.get("id")
            for r in row.get("rows") or []
            if r.get("kind") == "deck_category" and r.get("delete") and r.get("id") is not None
        }
        late = [b for b in bodies if b.get("after_cards") and b["id"] in reviewed]
        kept = await self._delete_empty_categories(sub, deck.id, late, progress) if late else []
        # The deck's own details as the snapshot recorded them (name, description, format, bracket,
        # private, unlisted), re-derived from the current deck so a detail changed back by hand
        # since the proposal is not sent again.
        details, _lines = details_rows(deck, snapshot_details(wanted))
        if details:
            await _writing(
                progress, self._call(sub, self.client.update_deck, deck.id, details_payload(details))
            )
        verified = await self.get_deck(sub, deck.id)
        self.recheck_waiting(
            sub, verified, renamed=_renames([b for b in bodies if "op" not in b], category_states(deck))
        )
        try:
            _left, mismatches = restore_steps(wanted, verified)
        except DeckError as exc:  # the writes were sent: a check that can't be made is a mismatch
            mismatches = [_sentence(exc).rstrip(".")]
        # a category made since the review stays, as no reviewed row deletes it
        try:
            left_rows = category_restore_plan(wanted, verified)[1]
        except DeckError:  # restore_steps above reports what can't be worked out
            left_rows = []
        unreviewed = {row_line(r) for r in left_rows if r.get("delete") and r.get("id") not in reviewed}
        mismatches = stuck + [m for m in mismatches if m not in stuck and m not in unreviewed]
        mismatches += [f"“{b['name']}” (category still has cards, so it was kept)" for b in kept]
        still, _lines = details_rows(verified, snapshot_details(wanted)) if details else ({}, [])
        mismatches += [f"{k} (deck detail)" for k in sorted(still)]
        result = {
            "snapshot_id": snapshot_id,
            **backup,
            "restored_snapshot_id": snap["id"],
            "sent_entries": len(payload),
            "restored_details": sorted(details),
            **({"categories_saved": progress.get("categories_saved", [])} if bodies else {}),
            "verified": not mismatches,
            "mismatched_cards": mismatches,
        }
        if mismatches:
            progress.update(result)  # apply() records it with the error
            raise DeckError(
                "verify_mismatch",
                "Archidekt accepted the request but the deck does not match the snapshot for: "
                + "; ".join(mismatches)
                + f". A snapshot ({snapshot_id}) of the deck before the restore was kept.",
            )
        self.db.finish_proposal(row["id"], state="applied", result=result)
        return self.describe(sub, row["id"])

    async def _apply_create(self, sub: str, row: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        spec = row["changes"]
        cards = spec["cards"]
        _token, link = self._token(sub)
        if spec.get("archidekt_username") and not _same_account(link, spec):
            raise DeckError(
                "other_account",
                f"This new deck was proposed for Archidekt account {spec['archidekt_username']}, but "
                f"{link['archidekt_username']} is linked now. Nothing was created; ask for a new "
                "proposal if the deck should go to this account.",
            )
        resolve: dict[tuple[str, str, str], int] = {}
        modifiers: dict[tuple[str, str, str], str] = {}
        printing_notes: list[str] = []
        found: dict[tuple[str, str, str], dict[str, Any]] = {}  # each printing looked up once
        progress["resolved_cards"], progress["of_cards"] = 0, len(cards)
        for c in cards:  # resolve every printing before creating anything
            card = found.get(row_key(c))
            repeat = card is not None
            if card is None:
                card = await self._call(
                    sub,
                    lambda token, *_, c=c: self.client.resolve_card(
                        token,
                        c["name"],
                        set_code=c.get("set_code") or None,
                        collector_number=c.get("collector_number") or None,
                    ),
                )
                found[row_key(c)] = card
            resolve[row_key(c)] = card["id"]
            progress["resolved_cards"] += 1
            finish = str(c.get("finish") or ("Foil" if c.get("foil") else ""))
            modifiers[row_key(c)] = finish_modifier(
                card["options"], foil=finish == "Foil", etched=finish == "Etched"
            )
            if c.get("set_code") and not card["exact_printing"] and not repeat:
                # a list's printing that Archidekt does not have is never swapped quietly
                wanted = f"{c['set_code'].upper()} {c.get('collector_number') or ''}".strip()
                got = (
                    f"{card['set_code'].upper()} {card['collector_number']}".strip() or "its default printing"
                )
                printing_notes.append(f"{c['name']}: {wanted} not found on Archidekt, added {got} instead")
        if spec.get("package"):  # a card package: the New package form's one request
            made = await self._call(
                sub,
                lambda token, *_: self.client.create_package(
                    token,
                    {
                        "name": spec["name"],
                        "private": bool(spec.get("private", True)),
                        "cardPackage": spec["package"],
                        "parent_folder": spec.get("folder_id"),
                    },
                ),
            )
            created = Deck(str(made["id"]), spec["name"], "", "", [], [], {})
        else:
            created = await self._call(
                sub,
                lambda token, *_: self.client.create_deck(
                    token,
                    name=spec["name"],
                    format_id=FORMAT_IDS[spec["format"]],
                    private=bool(spec.get("private", True)),
                    theorycrafted=bool(spec.get("theorycrafted")),
                    game=GAME_IDS.get(str(spec.get("game") or "")),
                    folder_id=spec.get("folder_id"),
                ),
            )
        self.db.finish_proposal(row["id"], state="applying", deck_id=created.id)
        progress.update(deck_id=created.id, deck_url=f"https://archidekt.com/decks/{created.id}")
        entries = new_deck_entries(cards, resolve, modifiers)
        # From here the deck exists: until its cards are in, it is held under its own id too, and any
        # other change to it (the member's own edits on its page) is refused with how far it got
        lock = self._deck_locks.setdefault(f"{sub}:{created.id}", asyncio.Lock())
        await lock.acquire()
        self._being_made[str(created.id)] = (progress, entries)
        try:
            # each custom category with its switches first, so the cards go into it as it should be
            for f in spec.get("category_flags") or []:
                await self._call(
                    sub,
                    lambda token, *_, f=f: self.client.create_category(token, created.id, {**f, "id": None}),
                )
            await self._send_batched(sub, created.id, entries, progress)
        finally:
            self._being_made.pop(str(created.id), None)
            lock.release()
        verified = await self.get_deck(sub, created.id)
        want_folder = spec.get("folder_id")
        if want_folder is not None and verified.parent_folder != want_folder:
            # the create request names the folder as archidekt.com's form does; when the deck is not
            # in it (not tried live), the folder page's own Move request puts it there
            await self._call(
                sub,
                lambda token, *_: self.client.mass_update(
                    token, [{"type": "deck", "id": int(created.id), "patch": {"parentFolder": want_folder}}]
                ),
            )
            verified = await self.get_deck(sub, created.id)
        # Every row counts here, maybeboard and sideboard rows included: Archidekt keeps them
        # outside the deck proper, and the import sent them all the same.
        want: dict[str, int] = {}
        for c in cards:
            want[c["name"]] = want.get(c["name"], 0) + int(c["quantity"])
        got: dict[str, int] = {}
        for vc in verified.cards:
            got[vc.name] = got.get(vc.name, 0) + vc.quantity
        mismatches = _mismatches(got, want) + _new_deck_printing_mismatches(verified, cards, entries)
        states = {x["name"]: x for x in category_states(verified)}
        for f in spec.get("category_flags") or []:
            got_cat = states.get(f["name"])
            if got_cat is None or any(got_cat[k] != f[k] for k in ("includedInDeck", "includedInPrice")):
                mismatches.append(f"category “{f['name']}” (its switches)")
        if want_folder is not None and verified.parent_folder != want_folder:
            mismatches.append("the folder")
        saved_otherwise: list[str] = []  # a package's settings as Archidekt saved them
        if spec.get("package"):
            from . import packages

            block = packages.block_of(verified)
            if block is None:  # not in the first read of a package just made: read it once more
                block = packages.block_of(await self.get_deck(sub, created.id))
            saved_otherwise = packages.differences(block, spec["package"])
        result = {
            "deck_id": created.id,
            "deck_url": f"https://archidekt.com/decks/{created.id}",
            "printing_notes": printing_notes,
            "sent_entries": len(entries),
            "verified": not (mismatches or saved_otherwise),
            "mismatched_cards": mismatches + saved_otherwise,
        }
        if mismatches or saved_otherwise:
            progress.update(result)  # apply() records it with the error
            differently = f"Archidekt saved it differently: {'; '.join(saved_otherwise)}."
            raise DeckError(
                "verify_mismatch",
                f"Made, but {differently}"
                if not mismatches
                else f"The deck was created (deck ID {created.id}) but these cards do not match the "
                "proposal: " + ", ".join(mismatches) + (f". {differently}" if saved_otherwise else ""),
            )
        self.db.finish_proposal(row["id"], state="applied", result=result)
        return self.describe(sub, row["id"])

    def reject(
        self, sub: str, proposal_id: str, *, via: str | None = None, reason: str | None = None
    ) -> dict[str, Any]:
        """Close a pending proposal without applying it (owner only). From the member's browser
        (the review page's form, which passes REVIEW_PAGE, or the API with the session cookie) any
        of their proposals; from a connected app (``via`` "mcp", "app" for its card, "api", or any
        non-browser client) only the proposals that app made itself, the same rule as apply."""
        row = self.db.get_proposal(proposal_id, sub)
        if row is None:
            raise DeckError("not_found", "No such proposal for your account.")
        actor = current_client.get()
        from_browser = actor == BROWSER_CLIENT and via in (None, "browser", REVIEW_PAGE)
        creator = row.get("created_by_client")
        if not from_browser and (not creator or creator != actor):
            raise DeckError(
                "other_client",
                "This proposal was made by a different connected app (or in the browser), so it "
                "cannot be rejected from here. The user can reject it on the review page.",
                review_url=f"{self.settings.public_url}/proposals/{proposal_id}",
            )
        # Who closed it, kept in its result so describe() can say so: the member's own pages, the
        # card's Reject (via "app"), the assistant's reject_proposal (via "mcp") or the API.
        if from_browser:
            by = "review_page" if via == REVIEW_PAGE else "browser"
        else:
            by = {"app": "card", "mcp": "assistant"}.get(via or "", "api")
        closed = {"rejected_by": by, **({"client": actor} if actor and not from_browser else {})}
        # the member's own words from the card's "why", kept as plain text and shown where the
        # rejection is shown (escaped there)
        why = clean_text(str(reason or ""))[:REJECT_REASON_MAX].strip()
        if why:
            closed["reject_reason"] = why
        if not self.db.reject_proposal(proposal_id, sub, closed):
            # Lost a race with an apply, or the proposal has expired: report the state as it is now.
            raise self._not_pending(sub, proposal_id, "rejected")
        self._audit(
            "proposal_rejected", sub=sub, detail={"proposal_id": proposal_id, "deck_id": row["deck_id"]}
        )
        board.settle(self, sub, proposal_id, "rejected")  # holds, replaced notes back
        return self.describe(sub, proposal_id)

    # -- 0.7.29: the board of waiting notes and the assistant's tidy-up tools (board.py) ----------
    @staticmethod
    def max_pending() -> int:
        return MAX_PENDING_PROPOSALS

    def board_lines(self, sub: str, deck_id: str | None = None) -> list[dict[str, Any]]:
        """Every waiting note of the member (of one deck), oldest first, with no cap: its
        number, date, app, version, status, summary, cards and links, and whether the
        app asking made it (``own``: it may apply or withdraw only those)."""
        caller = current_client.get()
        out = []
        lines_ = self.db.pending_board(sub, None if deck_id is None else clean_target(deck_id))
        clients = [
            c for line in lines_ for c in (line.get("created_by_client"), line.get("amended_by_client"))
        ]
        with self.db.read_ahead(sub, [line["id"] for line in lines_], clients):
            self._board_rows(sub, lines_, caller, out)
        return out

    def _board_rows(
        self, sub: str, lines_: list[dict[str, Any]], caller: Any, out: list[dict[str, Any]]
    ) -> None:
        for line in lines_:
            row = self.db.get_proposal(line["id"], sub) or {}
            lines = str(line.get("diff_text") or "").splitlines()
            summary = "; ".join(lines[:3]) + (f" (+{len(lines) - 3} more)" if len(lines) > 3 else "")
            amender = line.get("amended_by_client")
            out.append(
                {
                    "proposal_id": line["id"],
                    "board_no": line.get("board_no"),
                    "kind": line.get("kind"),
                    "deck_id": line["deck_id"],
                    "deck_name": line.get("deck_name"),
                    "created_at": line["created_at"],
                    "expires_at": line["expires_at"],
                    "created_by": self._creator_label(line.get("created_by_client")),
                    "created_by_client": line.get("created_by_client"),
                    "own": bool(caller) and line.get("created_by_client") == caller,
                    "version": int(line.get("version") or 1),
                    # the risk word on the cards (Low, High, Your press, Delete)
                    "risk": self.proposal_risk(sub, row)[0] if row else None,
                    **(
                        {
                            "changed_by": self._creator_label(amender),
                            "changed_by_apps": self.changed_by_apps(sub, row or line),
                            "changed_at": line.get("amended_at"),
                            "what_changed": line.get("version_note"),
                        }
                        if amender
                        else {}
                    ),
                    **(self._status_fields(sub, line) if line.get("status") else {"status": None}),
                    "summary": summary[:400],
                    "cards": row_cards(row.get("rows"))[:60],
                    "links": board.links_text(self.db, sub, line["id"]),
                    # the same link in the member's words, for the cards
                    "link_note": board.link_note(self.db, sub, line),
                    "review_url": f"{self.settings.public_url}/proposals/{line['id']}",
                    # a deck deletion, or a change that keeps formatting: never on a card
                    "review_page_only": not self.card_may_approve(row) if row else False,
                }
            )
        return out

    def _board_note(
        self, sub: str, out: dict[str, Any], deck_id: str, rows: Any, exclude: str
    ) -> dict[str, Any]:
        """A propose or amend result with the deck's other waiting notes, the overlaps card by
        card and, from SOFT_PER_DECK on, the soft hint to tidy up (never refused)."""
        others = [
            r
            for r in (self.db.get_proposal(x["id"], sub) for x in self.db.pending_board(sub, deck_id))
            if r is not None and r["id"] != exclude
        ]
        numbers = {r["id"]: r.get("board_no") for r in others}
        found = board.overlaps(others, rows, numbers)
        extra: dict[str, Any] = {
            "waiting_on_deck": [
                {
                    "proposal_id": r["id"],
                    "board_no": r.get("board_no"),
                    "summary": str(r.get("diff_text") or "")[:200],
                }
                for r in others
            ]
        }
        if found:
            extra["overlaps"] = found
            if out.get("next_step"):
                # whatever the approval mode allows, the overlap comes first
                extra["next_step"] = OVERLAP_STEP + " " + str(out["next_step"])
        if len(others) + 1 >= board.SOFT_PER_DECK:
            extra["hint"] = (
                f"This deck has {len(others) + 1} changes waiting. Combine or amend them "
                "(combine_proposals, amend_proposal) before adding more, so the user can review them."
            )
        return {**out, **extra}

    async def amend(self, sub: str, proposal_id: str, raw_changes: Any, *, version: int) -> dict[str, Any]:
        """Replace a waiting note's changes with a new version (any app may,
        labelled). It keeps its number and date; its expiry starts again; the gateway writes
        what changed. Worked out on the deck as it is now, like a new proposal. Card edits,
        deck details and new decks (card packages too) only; another kind is withdrawn and
        proposed again instead."""
        row = self.db.get_proposal(proposal_id, sub)
        if row is None:
            raise DeckError("not_found", "No such proposal for your account.")
        if row["state"] != "pending" or row["expires_at"] < int(time.time()):
            raise self._not_pending(sub, proposal_id, "amended")
        kind = row.get("kind") or "edit"
        if kind == "create_deck":
            return await self.amend_new_deck(sub, row, raw_changes, version=version)
        if kind not in ("edit", "details"):
            raise DeckError(
                "invalid",
                "Only a deck edit, a deck details change or a new deck can be amended. Withdraw this "
                "one with withdraw_proposal and make a new proposal instead; nothing was changed.",
            )
        deck = await self.get_own_deck(sub, row["deck_id"])
        self.recheck_waiting(sub, deck)
        note: dict[str, Any] = {}
        if kind == "edit":
            changes = parse_changes(raw_changes)
            _before, _after, rows = plan_rows(deck, changes)
            await self._refuse_unknown_cards(deck, changes)
            rows, legality = await self._judge_rules(deck, changes, rows)
            note = self._rules_note(changes, rows, legality)
            stored: Any = [c.as_dict() for c in changes]
            # a card package's line stays first while any of its cards is still added: its import
            # is counted at apply, and its risk rule holds (security review 0.7.43)
            from . import packages

            lines = (
                packages.amended_line(r, stored) for r in row.get("rows") or [] if r.get("kind") == "package"
            )
            rows = [*(r for r in lines if r is not None), *rows]
        else:
            raw = dict(raw_changes) if isinstance(raw_changes, dict) else raw_changes
            organise_raw = (
                {k: raw.pop(k) for k in ORGANISE_FIELDS if k in raw} if isinstance(raw, dict) else {}
            )
            wanted = parse_details(raw) if raw or not organise_raw else {}
            stored, rows = details_rows(deck, wanted)
            stored = stored_details(stored)
            organise, organise_rows = await self._organise_plan(sub, deck, organise_raw)
            if organise:
                stored = {**stored, "organise": organise}
                rows = [*rows, *organise_rows]
            if not stored:
                raise DeckError("invalid", "The deck already has these details; nothing would change.")
        try:
            new = self.db.amend_proposal(
                proposal_id,
                sub,
                expect_version=int(version),
                changes=stored,
                rows=rows,
                diff_text="\n".join(row_line(r) for r in rows),
                by_client=current_client.get(),
                what_changed=board.what_changed(row.get("rows"), rows),
                baseline_fingerprint=deck.fingerprint(),
            )
        except ProposalVersionsFull:
            raise DeckError(
                "versions_full",
                f"This proposal already keeps {MAX_PROPOSAL_VERSIONS} earlier versions, so it cannot "
                "be amended again. "
                + (
                    "Make a new proposal with propose_deck_changes and pass replaces with this one's id"
                    if kind == "edit"
                    else "Withdraw it with withdraw_proposal and make a new one with propose_deck_details"
                )
                + "; nothing was changed.",
                proposal_id=proposal_id,
            ) from None
        if new is None:
            now = self.db.get_proposal(proposal_id, sub)
            if now is not None and now["state"] == "pending":
                raise self._changed(now)
            raise self._not_pending(sub, proposal_id, "amended")
        self.db.restart_expiry(proposal_id, sub, int(time.time()) + self.expiry_seconds())
        self._audit(
            "proposal_amended",
            sub=sub,
            detail={"proposal_id": proposal_id, "deck_id": row["deck_id"], "version": new},
        )
        out = self._board_note(sub, self.describe(sub, proposal_id), row["deck_id"], rows, proposal_id)
        return {**out, **note}

    async def amend_new_deck(
        self, sub: str, row: dict[str, Any], raw: Any, *, version: int
    ) -> dict[str, Any]:
        """A waiting new deck (or card package) changed in place: ``raw`` holds the fields to change
        (NEW_DECK_FIELDS, as propose_new_deck takes them; a card list replaces the whole list),
        and every field left out keeps what the proposal has. Worked out as propose_new_deck works
        it out; no deck is read, since none exists yet."""
        if not isinstance(raw, dict) or not raw:
            raise DeckError(
                "invalid",
                "For a new deck, pass new_deck with the fields to change: name, deck_format, the whole "
                "new card list (cards, decklist_text, csv_text or json_text), private, theorycrafted, "
                "game, package or folder. Nothing was changed.",
            )
        unknown = sorted(str(k) for k in raw if k not in NEW_DECK_FIELDS)
        if unknown:
            raise DeckError(
                "invalid",
                f"A new deck has no field {', '.join(unknown)}; use {', '.join(NEW_DECK_FIELDS)}. "
                "Nothing was changed.",
            )
        had = row.get("changes") if isinstance(row.get("changes"), dict) else {}
        given = {k: v for k, v in raw.items() if v is not None}
        new_list = any(k in given for k in ("cards", "decklist_text", "csv_text", "json_text"))
        plan = await self.plan_new_deck(
            sub,
            name=given.get("name", had.get("name") or row.get("deck_name") or ""),
            deck_format=given.get("deck_format", had.get("format") or "commander"),
            cards=given.get("cards"),
            decklist_text=given.get("decklist_text"),
            csv_text=given.get("csv_text"),
            json_text=given.get("json_text"),
            private=bool(given.get("private", had.get("private", True))),
            theorycrafted=bool(given.get("theorycrafted", had.get("theorycrafted", False))),
            game=str(given.get("game", had.get("game") or "any")),
            package=given.get("package"),
            folder_id=given.get("folder_id", had.get("folder_id")),
            entries=None if new_list else list(had.get("cards") or []),
            parsed_package=None if "package" in given else had.get("package"),
        )
        return await self._update_new_deck(sub, row, plan, version=version)

    def withdraw(self, sub: str, proposal_id: str, reason: str) -> dict[str, Any]:
        """Close one of the calling app's own waiting notes because the user's newer request
        makes it pointless (only its own). It stays visible as withdrawn, with the
        reason (``bring_back`` can reopen it, but no page or tool offers that)."""
        row = self.db.get_proposal(proposal_id, sub)
        if row is None:
            raise DeckError("not_found", "No such proposal for your account.")
        caller = current_client.get()
        if not caller or row.get("created_by_client") != caller:
            raise DeckError(
                "other_client",
                "Only the app that made a proposal may withdraw it. Tell the user; they can reject it "
                "on the review page. Nothing was changed.",
                review_url=f"{self.settings.public_url}/proposals/{proposal_id}",
            )
        why = clean_text(str(reason or ""))[:200]
        if not why:
            raise DeckError("invalid", "reason is required: why the user's request makes it pointless")
        if not self.db.close_proposal(proposal_id, sub, "withdrawn", why, by={"client": caller}):
            raise self._not_pending(sub, proposal_id, "withdrawn")
        self._audit(
            "proposal_withdrawn", sub=sub, detail={"proposal_id": proposal_id, "deck_id": row["deck_id"]}
        )
        board.settle(self, sub, proposal_id, "withdrawn")
        return self.describe(sub, proposal_id)

    def bring_back(self, sub: str, proposal_id: str) -> dict[str, Any]:
        """A withdrawn or replaced note back to waiting while it has not expired. Nothing in
        the gateway calls it (only a test does): no page or tool offers it, and a replaced note
        comes back through ``board.settle`` when its replacement is rejected or withdrawn."""
        if not self.db.reopen_proposal(proposal_id, sub, max_pending=MAX_PENDING_PROPOSALS):
            row = self.db.get_proposal(proposal_id, sub)
            if row is None:
                raise DeckError("not_found", "No such proposal for your account.")
            if row["state"] in ("withdrawn", "replaced") and row["expires_at"] >= int(time.time()):
                self._room_for_proposal(sub)
            raise self._not_pending(sub, proposal_id, "brought back")
        self._audit("proposal_reopened", sub=sub, detail={"proposal_id": proposal_id})
        board.settle(self, sub, proposal_id, "pending")
        return self.describe(sub, proposal_id)

    def mark_replaced(self, sub: str, new_id: str, old_ids: list[str]) -> tuple[list[str], list[str]]:
        """Close each old note as replaced by ``new_id`` (kept, linked, and back if the new one is
        rejected). A note another app made or changed is only linked: it stays waiting
        until the member's press applies ``new_id``, and then
        board.settle closes it. Returns (the ids closed, the ids left waiting)."""
        no = self.db.proposal_board_numbers(sub, [new_id]).get(new_id)
        client = current_client.get()
        done, later = [], []
        for old in old_ids:
            if _made_by_another_app(self.db.get_proposal(old, sub) or {}, client):
                if self.db.link_proposals(new_id, sub, "replaces", old):
                    later.append(old)
                continue
            if self.db.link_proposals(new_id, sub, "replaces", old) and self.db.close_proposal(
                old,
                sub,
                "replaced",
                f"replaced by #{no}" if no else "replaced",
                by={"client": current_client.get()},
            ):
                done.append(old)
                board.settle(self, sub, old, "withdrawn")
        return done, later

    def check_replaceable(self, sub: str, deck_id: str, old_ids: Any) -> list[str]:
        """The notes a new proposal will replace (any app's, labelled): waiting, on the
        same deck. Refused before anything is saved."""
        if not isinstance(old_ids, list) or not old_ids or len(old_ids) > MAX_CHANGES:
            raise DeckError("invalid", "replaces must be a non-empty list of waiting proposal ids")
        out = []
        for pid in dict.fromkeys(str(x) for x in old_ids):
            row = self.db.get_proposal(pid, sub)
            if row is None or row["state"] != "pending" or row["expires_at"] < int(time.time()):
                raise DeckError("invalid", f"{pid} is not a waiting proposal of this user")
            if str(row["deck_id"]) != str(deck_id):
                raise DeckError(
                    "invalid",
                    f"{pid} is for another deck; a proposal replaces proposals on its own deck only",
                )
            out.append(pid)
        return out

    async def propose_linked(
        self, sub: str, deck_id: str, raw_changes: Any, *, replaces: Any = None, depends_on: Any = None
    ) -> dict[str, Any]:
        """``propose`` with the board's links: the waiting notes it replaces (kept,
        marked replaced, back if this one is rejected) and the ones it depends on.
        Both are checked before anything is saved."""
        deck = _clean_deck_id(deck_id)
        old = self.check_replaceable(sub, deck, replaces) if replaces else []
        if depends_on and (not isinstance(depends_on, list) or len(depends_on) > MAX_CHANGES):
            raise DeckError("invalid", "depends_on must be a list of proposal ids")
        for other in depends_on or []:
            if self.db.get_proposal(str(other), sub) is None:
                raise DeckError("invalid", f"{other} is not a proposal of this user")
        out = await self.propose(sub, deck, raw_changes)
        if out.get("duplicate_of"):
            return out
        pid = out["proposal_id"]
        self.link_depends(sub, pid, depends_on)
        done, later = self.mark_replaced(sub, pid, old)
        if not depends_on and not done and not later:
            return out
        fresh = self.describe(sub, pid)
        return {
            **out,
            **fresh,
            **({"replaced": done} if done else {}),
            # another app's changes: closed only once the member's press applies this one
            **({"replaces_when_applied": later} if later else {}),
        }

    async def combine(self, sub: str, proposal_ids: Any) -> dict[str, Any]:
        """One note of several waiting card edits on one deck, their changes oldest first; the old
        ones are marked replaced by it."""
        if not isinstance(proposal_ids, list) or len(set(map(str, proposal_ids))) < 2:
            raise DeckError("invalid", "proposal_ids must name two or more waiting proposals")
        self.db.hold_blocked(sub)  # a hold set when read counts here too
        client = current_client.get()
        rows = []
        for pid in dict.fromkeys(str(x) for x in proposal_ids):
            row = self.db.get_proposal(pid, sub)
            if row is None or row["state"] != "pending" or row["expires_at"] < int(time.time()):
                raise DeckError("invalid", f"{pid} is not a waiting proposal of this user")
            if (row.get("kind") or "edit") != "edit":
                raise DeckError("invalid", f"{pid} is not a deck edit; only card edits can be combined")
            # Only this app's own waiting changes that no other app changed, and never a held one
            no = f"#{row['board_no']}" if row.get("board_no") else pid
            if row.get("created_by_client") != client or self.rewritten_by_another_app(sub, row):
                raise DeckError(
                    "invalid",
                    f"{no} was made or changed by another app, so it can't be combined. Ask the user "
                    "to approve or reject it, or leave it separate.",
                )
            if row.get("status") == "held":
                raise DeckError(
                    "invalid",
                    f"{no} is held, so it can't be combined. Ask the user about it, or leave it separate.",
                )
            rows.append(row)
        if len({str(r["deck_id"]) for r in rows}) != 1:
            raise DeckError("invalid", "Only proposals for the same deck can be combined.")
        rows.sort(key=lambda r: (r["created_at"], r.get("board_no") or 0))
        changes = [c for r in rows for c in r["changes"]]
        parts = [r["id"] for r in rows]
        try:
            out = await self.propose_linked(sub, rows[0]["deck_id"], changes, replaces=parts)
        except DeckError as exc:
            if "removed more than once" not in str(exc):
                raise
            raise DeckError(
                exc.kind,
                f"{exc} More than one of these proposals removes it: amend or withdraw one of them, "
                "then combine.",
            ) from None
        if out.get("duplicate_of"):
            return out
        pid = out["proposal_id"]
        # The combined note keeps what held its parts back: it waits for the notes they
        # depended on, and a held part makes it held, so no assistant applies it on its own. (A
        # part another app made: _assistant_policy reads it from the "replaces" links.)
        carried = False
        for link in self.db.proposal_links(sub, parts):
            if (
                link["proposal_id"] in parts
                and link["kind"] == "depends_on"
                and link["other_id"] not in parts
            ):
                carried = self.db.link_proposals(pid, sub, "depends_on", link["other_id"]) or carried
        held = next((r for r in rows if r.get("status") == "held" and r.get("status_reason")), None)
        if held is not None:
            carried = self.db.set_hold(pid, sub, str(held["status_reason"])) or carried
        return {**out, **self.describe(sub, pid)} if carried else out

    async def check_plan(self, sub: str, deck_id: str, raw_changes: Any) -> dict[str, Any]:
        """A planned deck edit checked before it is proposed, so the assistant fixes it or
        amends a waiting note instead of drawing a card for a proposal it then redoes. Saves
        nothing and asks Archidekt nothing: the link, the change list, the card names (propose's
        check without the deck, so a name already in it is looked up too), the room for another
        proposal, an exact repeat of this app's waiting note, and the deck's waiting notes with
        the cards they share with the plan. Whatever needs the deck itself (a card not in it, a
        deck that is not the member's or does not exist) is found only when the proposal reads it."""
        deck = _clean_deck_id(deck_id)
        problems: list[dict[str, Any]] = []
        changes: list[Change] = []
        try:
            self._token(sub)
        except DeckError as exc:
            problems.append({"error": exc.kind, "message": str(exc)})
        try:
            changes = parse_changes(raw_changes)
        except DeckError as exc:
            problems.append({"error": exc.kind, "message": str(exc)})
        unknown: list[str] = []
        adds = list(dict.fromkeys(c.card_name for c in changes if c.action == "add"))
        if adds and self.known_cards is not None:
            try:
                found = await self.known_cards(adds)
                unknown = [n for n in adds if found.get(n) is False]
            except Exception:
                logger.exception("card-name check failed")
        if unknown:
            problems.append(
                {
                    "error": "unknown_cards",
                    "message": "No Magic card is called "
                    + ", ".join(f"'{n}'" for n in unknown)
                    + (
                        " (unless the deck already has it)."
                        if len(unknown) == 1
                        else " (unless the deck already has them)."
                    ),
                    "unknown_cards": unknown,
                }
            )
        client = current_client.get()
        dup = (
            self.db.find_duplicate(
                sub, deck_id=deck, kind="edit", changes=[c.as_dict() for c in changes], client=client
            )
            if changes and client not in (None, BROWSER_CLIENT, ADMIN_CLIENT)
            else None
        )
        names = {c.card_name.casefold() for c in changes}
        waiting, overlaps, others_share = [], [], False
        amend: tuple[dict[str, Any], bool] | None = None
        for line in self.board_lines(sub, deck):
            shared = [n for n in line.get("cards") or [] if str(n).casefold() in names]
            mine = bool(line.get("own")) and line.get("kind") == "edit" and line.get("status") != "held"
            waiting.append(
                {k: line.get(k) for k in ("proposal_id", "board_no", "version", "own", "status", "summary")}
                | {"shares_cards": shared}
            )
            label = f"#{line.get('board_no')}" if line.get("board_no") else "a waiting change"
            overlaps += [f"{n}: {label} changes it too." for n in shared]
            others_share = others_share or bool(shared and not mine)
            # the app's own waiting edit, the one sharing cards first: amend it instead
            if mine and line["proposal_id"] != dup and (amend is None or (shared and not amend[1])):
                amend = (line, bool(shared))
        # a new proposal needs room; an amend or an exact repeat does not
        full: DeckError | None = None
        try:
            self._room_for_proposal(sub)
        except DeckError as exc:
            full = exc
        if full is not None and dup is None and amend is None:
            problems.append({"error": full.kind, "message": str(full)})
        out: dict[str, Any] = {
            "deck_id": deck,
            "changes": len(changes),
            "problems": problems,
            "waiting_on_deck": waiting,
            **({"overlaps": overlaps} if overlaps else {}),
        }
        if problems:
            out["suggest"] = "fix"
            out["next_step"] = (
                "Fix each problem first; a proposal with them would be refused (a name in "
                "unknown_cards passes only if the deck already has it)."
            )
        elif dup:
            no = (self.db.get_proposal(dup, sub) or {}).get("board_no")
            out |= {"suggest": "already_waiting", "duplicate_of": dup}
            out["next_step"] = (
                f"This exact change already waits as #{no}. Don't propose it again."
                if no
                else "This exact change already waits. Don't propose it again."
            )
        elif amend is not None:
            line, shares = amend
            row = self.db.get_proposal(line["proposal_id"], sub) or {}
            out |= {
                "suggest": "amend",
                # its whole stored list: an amend replaces it, so it goes in again with the plan
                "amend": {
                    "proposal_id": line["proposal_id"],
                    "version": line["version"],
                    "changes": row.get("changes") or [],
                },
            }
            no = line.get("board_no")
            out["next_step"] = (
                (f"Your #{no}" if no else "Your waiting change")
                + " already waits on this deck"
                + (" and changes some of the same cards" if shares else "")
                + ". If this belongs to the same request, call amend_proposal on it once with "
                "amend.changes and this plan merged into one list, one change per card (an amend "
                "replaces the whole list). If it replaces it, propose with replaces. Only a separate "
                "request gets its own proposal."
                + (" The waiting limit is reached, so only an amend is possible now." if full else "")
            )
        else:
            out["suggest"] = "propose"
            out["next_step"] = (
                "Nothing found against it, but other waiting changes share cards with it (overlaps): "
                "propose it in one call and tell the user."
                if others_share
                else "Nothing found against it: propose it in one call."
            )
        return out

    def link_depends(self, sub: str, proposal_id: str, others: Any) -> None:
        """Record that a new note depends on waiting notes: it is held if one of them is
        later rejected, withdrawn or fails."""
        if not others:
            return
        if not isinstance(others, list) or len(others) > MAX_CHANGES:
            raise DeckError("invalid", "depends_on must be a list of proposal ids")
        for other in dict.fromkeys(str(x) for x in others):
            if not self.db.link_proposals(proposal_id, sub, "depends_on", other):
                raise DeckError("invalid", f"{other} is not a proposal of this user")

    def snapshot(self, sub: str, snapshot_id: str) -> dict[str, Any]:
        row = self.db.get_snapshot(snapshot_id, sub)
        if row is None:
            raise DeckError("not_found", "No such snapshot for your account.")
        return row

    def list_snapshots(self, sub: str, **narrow: Any) -> list[dict[str, Any]]:
        """Newest first; ``narrow`` is passed to the database (deck_id, search, limit, offset)."""
        return self.db.list_snapshots(sub, **narrow)

    @_dedupe
    async def propose_restore(self, sub: str, snapshot_id: str) -> dict[str, Any]:
        """A proposal that puts the deck back to what a snapshot recorded, relation by relation:
        the same printings, finishes (foil, etched), categories (so the commander, sideboard and
        maybeboard too) and quantities. It is an ordinary proposal: reviewed, confirmed and
        applied like any other (which takes a fresh snapshot first), so a restore can itself be
        undone. The deck's own details (name, description, format, bracket, private, unlisted)
        go back too when the snapshot recorded them differently, and so do the deck's categories:
        each one still on the deck gets the name and the flags (Premier, Not in deck, Not in price)
        the snapshot recorded, by its record id (``category_restore_plan``)."""
        snap = self.snapshot(sub, str(snapshot_id or "").strip())
        wanted = parse_deck(snap["deck"])
        self._room_for_proposal(sub)
        try:
            deck = await self.get_own_deck(sub, snap["deck_id"])
        except DeckError as exc:
            if exc.kind != "not_found":
                raise
            # The deck was deleted on Archidekt: a restore rewrites that deck, so there is nothing
            # to put the snapshot back onto. The snapshot itself is kept here.
            raise DeckError(
                "not_found",
                f"Deck {snap['deck_id']} is no longer on your Archidekt account (deleted there?), so "
                f"snapshot {snap['id']} cannot be restored onto it. Nothing was changed, and the "
                "snapshot with its card list is still kept in the gateway.",
            ) from exc
        self.recheck_waiting(sub, deck)
        payload, lines = restore_rows(wanted, deck)
        details, detail_lines = details_rows(deck, snapshot_details(wanted))
        lines += detail_lines
        if not payload and not details and not category_restore_plan(wanted, deck)[0]:
            raise DeckError("invalid", "The deck already matches this snapshot; nothing to restore.")
        if len(payload) > MAX_RESTORE_ENTRIES:
            raise DeckError(
                "invalid",
                f"Restoring this snapshot would touch {len(payload)} deck rows, more than the "
                f"{MAX_RESTORE_ENTRIES} a single restore may send. Restore it in parts with "
                "propose_deck_changes, or by hand from the snapshot.",
            )
        taken = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(int(snap["taken_at"])))
        pid = secrets.token_urlsafe(12)
        self._save_proposal(
            {
                "id": pid,
                "owner_sub": sub,
                "kind": "restore",
                "deck_id": deck.id,
                "deck_name": deck.name,
                "baseline_fingerprint": deck.fingerprint(),
                "changes": {"snapshot_id": snap["id"], "details": details},
            },
            [_row("restore", snapshot_id=snap["id"], taken=taken, changes=len(lines)), *lines],
        )
        self._audit(
            "proposal_created",
            sub=sub,
            detail={"proposal_id": pid, "deck_id": deck.id, "restores_snapshot": snap["id"]},
        )
        # the other waiting changes it shares cards with, as a card edit is told
        return self._board_note(sub, self.describe(sub, pid), deck.id, lines, pid)

    # -- deck details -----------------------------------------------------------
    @_dedupe
    async def propose_deck_details(self, sub: str, deck_id: str, details: Any) -> dict[str, Any]:
        """A proposal (kind ``details``) that changes the deck's own settings rather than its cards:
        any of ``name`` (1-200 chars), ``description`` (plain text, up to 20000 chars),
        ``deck_format`` (commander, modern, ...), ``edh_bracket`` (1-5, or null to clear),
        ``private`` and ``unlisted`` (booleans). Fields already set that way are dropped, and a
        proposal that would change nothing is refused. Applied with one PATCH
        /decks/{id}/update/ (verified live for description; the other fields are what Archidekt's
        site sends on that route, unverified) and checked by re-reading the deck."""
        deck_id = _clean_deck_id(deck_id)
        raw = dict(details) if isinstance(details, dict) else details
        organise_raw = {k: raw.pop(k) for k in ORGANISE_FIELDS if k in raw} if isinstance(raw, dict) else {}
        wanted = parse_details(raw) if raw or not organise_raw else {}
        self._room_for_proposal(sub)
        deck = await self.get_own_deck(sub, deck_id)
        self.recheck_waiting(sub, deck)
        changes, rows = details_rows(deck, wanted)
        changes = stored_details(changes)
        organise, organise_rows = await self._organise_plan(sub, deck, organise_raw)
        if organise:
            changes = {**changes, "organise": organise}
            rows = [*rows, *organise_rows]
        if not changes:
            raise DeckError("invalid", "The deck already has these details; nothing would change.")
        pid = secrets.token_urlsafe(12)
        # Saved with the creating app like every other kind, so only that app may apply it over MCP.
        self._save_proposal(
            {
                "id": pid,
                "owner_sub": sub,
                "kind": "details",
                "deck_id": deck.id,
                "deck_name": deck.name,
                "baseline_fingerprint": deck.fingerprint(),
                "changes": changes,
            },
            rows,
        )
        self._audit(
            "proposal_created",
            sub=sub,
            detail={"proposal_id": pid, "deck_id": deck.id, "kind": "details", "fields": sorted(changes)},
        )
        return self.describe(sub, pid)

    async def _organise_plan(
        self, sub: str, deck: Deck, raw: dict[str, Any]
    ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Resolve the organisation fields of a details proposal against the account now: the
        folder by name (an existing folder; "" for the top level), tags to add (names) and to take
        off (names of tags on the deck), and the cover (a card in the deck). Returns the
        stored plan and its review rows; what is already that way is left out."""
        plan: dict[str, Any] = {}
        rows: list[dict[str, Any]] = []
        if not raw:
            return plan, rows
        if "folder" in raw:
            want = clean_text(str(raw["folder"] or "")).casefold()
            info = await self.folders(sub)
            root = int(info["root_id"])
            by_id = {f["id"]: f for f in info["folders"]}
            if not want or want in ("top", "top level", "none"):
                target = by_id[root]
            else:
                # the gateway's backup folder is never a place to move a deck to
                hits = [f for f in _shown_folders(info) if f["name"].casefold() == want]
                if len(hits) != 1:
                    raise _no_such_folder(info, raw["folder"], several=bool(hits))
                target = hits[0]
            current = deck.parent_folder if deck.parent_folder is not None else root
            if target["id"] != current:
                plan["folder_id"] = target["id"]
                before = by_id.get(current, {}).get("name", "unknown")
                rows.append(_row("detail", field="folder", before=before, after=target["name"]))
        on_deck = {str(r.get("name") or "").casefold(): r for r in deck.tag_relations}
        add: list[str] = []
        looked_up: set[str] = set()
        for given in _names(raw.get("add_tags"), "add_tags"):
            n = self._tag_name(given)
            if n.casefold() in on_deck or n.casefold() in looked_up:
                continue
            looked_up.add(n.casefold())
            found = (await self._archidekt_tag(sub, n))[1]  # refused now, not at apply
            # Archidekt's own name, once: two names of one tag ("Ramp", "ramp") would make the
            # second add fail halfway through an apply
            if found.casefold() not in on_deck and found.casefold() not in {a.casefold() for a in add}:
                add.append(found)
        remove = []
        for name in _names(raw.get("remove_tags"), "remove_tags"):
            rel = on_deck.get(self._tag_name(name).casefold())
            if rel is None:
                raise DeckError("invalid", f"The deck has no tag '{name}'.")
            remove.append({"id": int(rel["id"]), "name": str(rel.get("name") or name)})
        if len(deck.tag_relations) - len(remove) + len(add) > self.MAX_TAGS:
            raise DeckError("invalid", f"A deck has at most {self.MAX_TAGS} tags here.")
        if add:
            plan["add_tags"] = add
        if remove:
            plan["remove_tags"] = remove
        if add or remove:
            before = [str(r.get("name") or "") for r in deck.tag_relations]
            gone = {r["name"].casefold() for r in remove}
            after = [n for n in before if n.casefold() not in gone] + add
            rows.append(
                _row(
                    "detail",
                    field="tags",
                    before=", ".join(before) or "none",
                    after=", ".join(after) or "none",
                    added=add,
                    removed=[r["name"] for r in remove],
                )
            )
        if "cover" in raw:
            pick = clean_text(str(raw["cover"] or "")).casefold()
            card = next((c for c in deck.cards if c.name.casefold() == pick and c.scryfall_uid), None)
            if card is None:
                raise DeckError("invalid", f"'{raw['cover']}' is not a card in this deck.")
            chosen = str(deck.raw.get("customFeatured") or "").lower()
            if not _cover_is(deck, card.scryfall_uid.lower()):
                plan["cover_uid"] = card.scryfall_uid
                # the card whose art is the chosen cover now, by name: "automatic" when Archidekt
                # picks it, and "a card not in this deck" for art of a card the deck no longer holds
                was = next(
                    (c.name for c in deck.cards if c.scryfall_uid and c.scryfall_uid.lower() in chosen), ""
                )
                before = was or ("a card not in this deck" if chosen else "automatic")
                rows.append(_row("detail", field="cover", before=before, after=card.name))
        if "package" in raw:
            from . import packages

            block = packages.block_of(deck)
            if block is None:
                raise DeckError(
                    "invalid", "This deck is not a card package; package settings are for packages"
                )
            new = packages.changed(block, packages.parse(raw["package"], partial=True))
            if new is not None:
                plan["package"] = {"id": int(block["id"]), "block": new}
                rows.append(
                    _row("detail", field="package", before=packages.text(block), after=packages.text(new))
                )
        if plan:
            from .recheck import organise_was

            plan["was"] = organise_was(deck)  # a later check tells whether they moved since
        return plan, rows

    async def _apply_organise(
        self, sub: str, deck_id: str, plan: dict[str, Any], progress: dict[str, Any]
    ) -> list[str]:
        """Carry out a details proposal's organisation plan with the same verified calls as the
        deck page's own buttons; each re-reads the deck. Returns the fields done. Each step marks
        ``deck_written`` as soon as its write call returns or fails in a way that may still have
        landed, not after its re-read: a step refused before it wrote anything (an
        unknown tag, Archidekt's 403 or 429) leaves the deck as it was, and a re-read that fails
        after the write never makes the change look unwritten."""
        done: list[str] = []
        if "folder_id" in plan:
            await self.move_deck(sub, deck_id, int(plan["folder_id"]), progress=progress)
            done.append("folder")
        for name in plan.get("add_tags") or []:
            await self.add_tag(sub, deck_id, name, progress=progress)
            done.append(f"tag +{name}")
        for rel in plan.get("remove_tags") or []:
            await self.remove_tag(sub, deck_id, int(rel["id"]), progress=progress)
            done.append(f"tag -{rel['name']}")
        if "cover_uid" in plan:
            await self.set_cover(sub, deck_id, plan["cover_uid"], progress=progress)
            done.append("cover")
        if "package" in plan:
            await self.set_package(sub, deck_id, plan["package"], progress=progress)
            done.append("package")
        return done

    async def _apply_details(self, sub: str, row: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        stored = dict(row["changes"])
        organise = stored.pop("organise", None) or {}
        wanted = parse_details(stored) if stored else {}
        deck = await self._current_deck_for(sub, row)
        changes, now_rows = details_rows(deck, wanted)
        # A description that keeps formatting is written as its review drew it, or not at all
        # (the deck's fingerprint is its cards, so the re-check does not see a description edit)
        if _description_doc(now_rows) != _description_doc(row.get("rows")):
            raise needs_look_error(DESCRIPTION_CHANGED)
        # words changed on Archidekt since the review are never written over, formatted or not
        was, now_text = _description_before(row.get("rows")), _description_before(now_rows)
        # (no description row now: Archidekt already has the new words, changed there since)
        if was is not None and (now_text is None or was != now_text):
            raise needs_look_error(DESCRIPTION_CHANGED)
        if not changes and not organise:
            raise DeckError("invalid", "The deck already has these details; nothing was sent.")
        # What Full auto and the review judged (who can see the deck,
        # whether a description edit removes its formatting) is judged again on the deck just read,
        # not trusted from when the change was made: Archidekt may not move updatedAt for these
        if formatting.loses_formatting({"rows": now_rows}) != formatting.loses_formatting(row):
            raise needs_look_error(DESCRIPTION_CHANGED)
        was_seen = [
            (r.get("seen_before"), r.get("seen_after")) for r in row.get("rows") or [] if r.get("seen_after")
        ]
        now_seen = [(r.get("seen_before"), r.get("seen_after")) for r in now_rows if r.get("seen_after")]
        if was_seen and was_seen != now_seen:
            from . import recheck

            raise needs_look_error("rows:" + recheck.first_difference(row.get("rows") or [], now_rows))
        snapshot_id = self._take_snapshot(sub, row, deck)
        progress["snapshot_id"] = snapshot_id
        backup = await self._backup_shared(sub, row, deck, snapshot_id)
        progress.update(backup)
        # The backup takes a while: check again before writing, like card edits do. The details
        # are compared rather than the fingerprint, which the backup copy may bump.
        now = await self.get_own_deck(sub, deck.id)
        if any(_current_detail(now, key) != _current_detail(deck, key) for key in DETAIL_FIELDS):
            from . import recheck

            raise needs_look_error(recheck.REASON_DURING_APPLY)
        if changes:
            await _writing(
                progress, self._call(sub, self.client.update_deck, deck.id, details_payload(changes))
            )
        verified = await self.get_deck(sub, deck.id)
        if not organise:  # the organise steps change the deck again after this read
            self.recheck_waiting(sub, verified)
        still, _lines = details_diff(verified, wanted)
        mismatches = sorted(still)
        organised: list[str] = []
        if organise and not mismatches:
            try:
                organised = await self._apply_organise(sub, deck.id, organise, progress)
            except DeckError as exc:
                progress.update({"snapshot_id": snapshot_id, **backup, "fields": sorted(changes)})
                # Only a proposal with other details had them written: a tags-only
                # one wrote nothing before this step.
                applied = " The deck's other details were applied." if changes else ""
                raise DeckError(
                    exc.kind,
                    f"{exc}{applied} A snapshot ({snapshot_id}) of the deck before the change was kept.",
                ) from exc
        result = {
            "snapshot_id": snapshot_id,
            **backup,
            "fields": sorted(changes) + organised,
            "verified": not mismatches,
            "mismatched_fields": mismatches,
        }
        if mismatches:
            progress.update(result)  # apply() records it with the error
            raise DeckError(
                "verify_mismatch",
                "Archidekt accepted the request but the deck's details do not match the proposal for: "
                + ", ".join(mismatches)
                + f". A snapshot ({snapshot_id}) of the deck before the change was kept.",
            )
        self.db.finish_proposal(row["id"], state="applied", result=result)
        return self.describe(sub, row["id"])

    # -- the deck's own categories: rename and flags (kind "categories") -------------------------
    @_dedupe
    async def propose_categories(
        self, sub: str, deck_id: str, changes: Any, *, delete_unused: bool = False
    ) -> dict[str, Any]:
        """A proposal (kind ``categories``) that renames categories of the member's own deck and
        sets their flags (Premier, Not in deck, Not in price), one review row per category. The
        one way in for the deck settings page (the member's own Save categories, applied at once
        with ``apply(via="browser")`` as the page's other saves are), the assistant
        (``propose_deck_categories``, then the usual review and apply) and History's Restore,
        which puts names and flags back through ``propose_restore``.

        ``changes``: entries as ``parse_category_changes`` takes them: a change (``{"category": its
        name now (or "id": its record id), "rename_to"?: str, "premier"?: bool, "not_in_deck"?:
        bool, "not_in_price"?: bool}``), a new category (``"action": "create"``) or a delete
        (``"action": "delete"``, with ``"move_cards_to"`` for one that still has cards: Archidekt
        only deletes an empty category). ``delete_unused`` adds a delete for every empty category
        (Archidekt's "Delete unused categories"). Refused before anything is proposed: a category
        the deck doesn't have, a new name the deck already has, a delete of a category with cards
        and nowhere to move them, and a change that changes nothing. Moving single cards between
        categories stays a card edit (``propose_deck_changes``).

        Applied (``_apply_categories``) with a backup copy first when backups are on, a snapshot,
        the deck checked again just before writing, one category write at a time through the
        pacer, and the deck read back to check every name, flag and card. A change touching the
        commander, or which cards count in the deck, is high risk and waits for the member's press
        in every approval mode (modes.auto_waits)."""
        deck_id = _clean_deck_id(deck_id)
        wanted = parse_category_changes(changes) if changes or not delete_unused else []
        self._room_for_proposal(sub)
        deck = await self.get_own_deck(sub, deck_id)
        self.recheck_waiting(sub, deck)
        if delete_unused:
            named = {w.get("id") for w in wanted} | {w["category"].casefold() for w in wanted}
            unused = [
                u
                for u in unused_category_changes(deck)
                if u["id"] not in named and u["category"].casefold() not in named
            ]
            if not unused and not wanted:
                raise DeckError("invalid", "The deck has no unused categories; nothing would change.")
            # one proposal deletes the first ones up to the cap; the rest are left for the next
            room = MAX_CATEGORY_CHANGES - len(wanted)
            wanted += unused[:room] if room > 0 else unused
            if len(wanted) > MAX_CATEGORY_CHANGES:
                raise DeckError("invalid", f"at most {MAX_CATEGORY_CHANGES} categories can change at once")
        plan, rows = category_change_rows(deck, wanted)
        pid = secrets.token_urlsafe(12)
        self._save_proposal(
            {
                "id": pid,
                "owner_sub": sub,
                "kind": "categories",
                "deck_id": deck.id,
                "deck_name": deck.name,
                "baseline_fingerprint": deck.fingerprint(),
                # every category as it is now: the fingerprint is the card rows, so a flag changed
                # on Archidekt since is caught by this, just before the apply writes
                "changes": {"categories": plan, "was": category_states(deck)},
            },
            rows,
        )
        self._audit(
            "proposal_created",
            sub=sub,
            detail={"proposal_id": pid, "deck_id": deck.id, "kind": "categories", "count": len(plan)},
        )
        # a card edit waiting on the deck may move cards into or out of these categories
        return self._board_note(sub, self.describe(sub, pid), deck.id, rows, pid)

    async def _categories_unchanged(self, sub: str, deck: Deck) -> Deck:
        """Re-read the deck just before category writes and refuse (needs a look, nothing sent)
        when a card row or any category's name, flags or record id changed since ``deck`` was
        read: the writes go by record id, so a stale id must never reach Archidekt."""
        from . import recheck

        now = await self.get_own_deck(sub, deck.id)
        if now.rows_fingerprint() != deck.rows_fingerprint() or category_states(now) != category_states(deck):
            raise needs_look_error(recheck.REASON_DURING_APPLY)
        return now

    async def _send_categories(
        self, sub: str, deck_id: str, bodies: list[dict[str, Any]], progress: dict[str, Any]
    ) -> None:
        """One category write per category, in order, through the pacer: ``op`` "create" (POST
        createCategory), "delete" (DELETE by record id) or an update (PATCH by record id, the
        default). Stops at the first failure with the names already saved in the message (and in
        ``categories_saved``)."""
        saved = progress.setdefault("categories_saved", [])
        for body in bodies:
            op = body.get("op", "update")
            if op == "create":
                fn: Any = self.client.create_category
                args: tuple[Any, ...] = (deck_id, body)
            elif op == "delete":
                fn, args = self.client.delete_category, (deck_id, int(body["id"]))
            else:
                fn, args = self.client.update_category, (deck_id, body)
            try:
                await _writing(progress, self._call(sub, fn, *args))
            except DeckError as exc:
                done = ", ".join(f"“{n}”" for n in saved)
                note = (
                    f" Saved before it stopped: {done}; the other categories were not changed."
                    if saved
                    else " No category was changed."
                )
                raise DeckError(exc.kind, _sentence(exc) + note, **exc.extra) from exc
            saved.append(body["name"] + (" (deleted)" if op == "delete" else ""))

    async def _delete_empty_categories(
        self, sub: str, deck_id: str, bodies: list[dict[str, Any]], progress: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """Send the category deletes in ``bodies``, each just after a fresh read shows it empty,
        as archidekt.com's own pages only ever delete an empty category (what its server does with
        one that still has cards is unknown, so the gateway never asks). The deck is read again
        before every delete: a card filed meanwhile (on archidekt.com, by a collaborator) keeps
        its category. Returns the bodies that were not empty, which are not sent."""
        full = []
        for b in bodies:
            fresh = await self.get_own_deck(sub, deck_id)
            names = {s["id"]: s["name"] for s in category_states(fresh) if s["id"] is not None}
            name = names.get(b["id"])
            if name is None:
                continue  # already gone
            if any(name in c.categories for c in fresh.cards):
                full.append({**b, "name": name})
                continue
            await self._send_categories(sub, deck_id, [{**b, "name": name}], progress)
        return full

    async def _apply_categories(
        self, sub: str, row: dict[str, Any], progress: dict[str, Any]
    ) -> dict[str, Any]:
        from . import recheck

        stored = row["changes"]
        plan = list(stored.get("categories") or [])
        # the deck it was made from (its card rows, recheck.py: kept strict for this kind) ...
        deck = await self._current_deck_for(sub, row)
        # ... and every category as it was then, record ids included
        if category_states(deck) != stored.get("was"):
            raise needs_look_error(recheck.REASON_CHANGED)
        snapshot_id = self._take_snapshot(sub, row, deck)
        progress["snapshot_id"] = snapshot_id
        backup = await self._backup_shared(sub, row, deck, snapshot_id)
        progress.update(backup)
        # The backup takes a while: read again and resolve each category's name to its record id
        # on the deck of now, just before writing
        now = await self._categories_unchanged(sub, deck)
        states = category_states(now)
        ids = {s["name"]: s["id"] for s in states}
        for p in plan:
            op = p.get("op", "update")
            if op == "create" and p.get("was") is None:
                if any(x["name"].casefold() == p["to"]["name"].casefold() for x in states):
                    raise needs_look_error(recheck.REASON_DURING_APPLY)
            elif op == "create" or p["id"] is None:  # a default getting its record: still without one
                if p["was"]["name"] not in ids or ids[p["was"]["name"]] is not None:
                    raise needs_look_error(recheck.REASON_DURING_APPLY)
            elif ids.get(p["was"]["name"]) != p["id"]:
                raise needs_look_error(recheck.REASON_DURING_APPLY)
        creates = [{**p["to"], "op": "create"} for p in plan if p.get("op") == "create"]
        later = [p for p in plan if p.get("op", "update") == "update" and p["id"] is None]
        first = creates + [
            dict(p["to"]) for p in plan if p.get("op", "update") == "update" and p["id"] is not None
        ]
        deletes = [p for p in plan if p.get("op") == "delete"]
        progress["of_categories"] = len(plan)
        await self._send_categories(sub, deck.id, first, progress)
        if later:
            # a default renamed as it got its record: the rename goes by the id Archidekt gave it
            made = {x["name"]: x["id"] for x in category_states(await self.get_own_deck(sub, deck.id))}
            plan = [dict(p) for p in plan]
            for p in plan:
                if p.get("op", "update") == "update" and p["id"] is None:
                    if made.get(p["was"]["name"]) is None:
                        raise DeckError(
                            "verify_mismatch",
                            f"Archidekt did not keep a record for “{p['was']['name']}”, so it was not "
                            "renamed.",
                        )
                    p["id"], p["made"] = made[p["was"]["name"]], True
                    p["to"] = {**p["to"], "id": p["id"]}
                    p["was"] = {**p["was"], "id": p["id"]}
            later_names = {q["was"]["name"] for q in later}
            renames = [
                dict(p["to"])
                for p in plan
                if p.get("op", "update") == "update" and p["was"]["name"] in later_names
            ]
            await self._send_categories(sub, deck.id, renames, progress)
            first += renames
        # a deleted category's cards go to the category picked for them first, one card write
        # per deck row (the categories they end up with were worked out when it was proposed)
        rows = {c.relation_id: c for c in now.cards if c.relation_id is not None}
        moves = [m for p in deletes for m in p.get("moves") or []]
        if moves:
            payload = [
                _entry("modify", rows[m["relation_id"]], rows[m["relation_id"]].quantity, m["to"])
                for m in moves
            ]
            await self._send(sub, deck.id, payload, progress)
        kept = await self._delete_empty_categories(
            sub,
            deck.id,
            [{"op": "delete", "id": p["id"], "name": p["was"]["name"]} for p in deletes],
            progress,
        )
        verified = await self.get_deck(sub, deck.id)
        updates = [b for b in first if b.get("op", "update") == "update"]
        self.recheck_waiting(sub, verified, renamed=_renames(updates, category_states(deck)))
        mismatches = category_mismatches(deck, plan, verified)
        for b in kept:
            note = f"“{b['name']}” (still has cards on Archidekt, so it was not deleted)"
            mismatches = [m for m in mismatches if not m.startswith(f"“{b['name']}” (still there")] + [note]
        result = {
            "snapshot_id": snapshot_id,
            **backup,
            "categories_saved": list(progress.get("categories_saved") or []),
            "verified": not mismatches,
            "mismatched_categories": mismatches,
            "after_fingerprint": verified.fingerprint(),  # Undo: did the deck change since?
        }
        if mismatches:
            progress.update(result)  # apply() records it with the error
            raise DeckError(
                "verify_mismatch",
                "Archidekt accepted the change but the deck does not show it for: "
                + "; ".join(mismatches)
                + f". A snapshot ({snapshot_id}) of the deck from just before was kept: Restore under "
                "History puts the categories back.",
            )
        self.db.finish_proposal(row["id"], state="applied", result=result)
        return self.describe(sub, row["id"])

    # -- the member's own custom cards in a deck (kind "custom_cards") ----------------------------
    MAX_CUSTOM_LIST_PAGES = 20

    async def own_custom_cards(self, sub: str, ids: list[int]) -> dict[int, str]:
        """The member's own custom cards among ``ids``, id -> name, read from their custom card
        list on Archidekt (My lists), page by page until every id is found or the list ends."""
        from . import account_lists

        want, found = set(ids), {}
        for page in range(1, self.MAX_CUSTOM_LIST_PAGES + 1):
            got = await account_lists.read(self, sub, "custom_cards", page=page)
            for item in got.get("items") or []:
                if isinstance(item.get("id"), int) and item["id"] in want:
                    found[item["id"]] = str(item.get("name") or "")
            if want <= found.keys() or not got.get("more"):
                break
        return found

    @_dedupe
    async def propose_custom_cards(self, sub: str, deck_id: str, changes: Any) -> dict[str, Any]:
        """A proposal (kind ``custom_cards``) that adds the member's own custom cards to one of
        their decks, removes custom cards from it or sets their count, one review row per card.
        The one way in for the assistant (``propose_custom_card_changes``) and the pages (the
        member's own press, applied at once with ``apply(via="browser")`` as other hand edits are).

        ``changes``: as ``parse_custom_card_changes`` takes them. Refused before anything is
        stored: an add of a custom card that is not one of the member's own (read from their
        custom card list), one already in the deck, a count change or remove of one that isn't,
        and a change that changes nothing.

        Applied (``_apply_custom_cards``) with a snapshot and, when backups are on, a backup copy
        first, the deck checked again just before writing, one modifyCards entry per change, and
        the deck read back to check every count and named category. The gateway's snapshots and
        Restore cover a deck's cards and details, not its custom card rows (a gateway choice): so
        this kind has no Undo."""
        deck_id = _clean_deck_id(deck_id)
        wanted = parse_custom_card_changes(changes)
        self._room_for_proposal(sub)
        deck = await self.get_own_deck(sub, deck_id)
        self.recheck_waiting(sub, deck)
        adds = [w["custom_card_id"] for w in wanted if w["action"] == "add"]
        names = await self.own_custom_cards(sub, adds) if adds else {}
        _plan, rows = custom_card_rows(deck, wanted, names)
        pid = secrets.token_urlsafe(12)
        self._save_proposal(
            {
                "id": pid,
                "owner_sub": sub,
                "kind": "custom_cards",
                "deck_id": deck.id,
                "deck_name": deck.name,
                "baseline_fingerprint": deck.fingerprint(),
                # the added cards' names as the member's list had them, so the rows can be worked
                # out again on the deck of later (recheck.rows_now) without another list read
                "changes": {"custom_cards": wanted, "names": {str(k): v for k, v in names.items()}},
            },
            rows,
        )
        self._audit(
            "proposal_created",
            sub=sub,
            detail={"proposal_id": pid, "deck_id": deck.id, "kind": "custom_cards", "count": len(rows)},
        )
        return self.describe(sub, pid)

    async def _apply_custom_cards(
        self, sub: str, row: dict[str, Any], progress: dict[str, Any]
    ) -> dict[str, Any]:
        from . import recheck

        stored = row["changes"]
        wanted = list(stored.get("custom_cards") or [])
        names = {int(k): str(v) for k, v in (stored.get("names") or {}).items()}

        def plan_on(deck: Deck, reason: str) -> list[dict[str, Any]]:
            # the custom rows are not in the deck's fingerprint: worked out again on the deck of
            # now, they must give exactly the rows that were reviewed
            try:
                plan, rows = custom_card_rows(deck, wanted, names)
            except DeckError as exc:
                raise needs_look_error(reason) from exc
            if recheck._clean(rows) != recheck._clean(row.get("rows")):
                raise needs_look_error(reason)
            return plan

        deck = await self._current_deck_for(sub, row)
        plan_on(deck, recheck.REASON_CHANGED)
        snapshot_id = self._take_snapshot(sub, row, deck)
        progress["snapshot_id"] = snapshot_id
        backup = await self._backup_shared(sub, row, deck, snapshot_id)
        progress.update(backup)
        # the backup takes a while: read again just before writing
        now = await self.get_own_deck(sub, deck.id)
        if now.rows_fingerprint() != deck.rows_fingerprint():
            raise needs_look_error(recheck.REASON_DURING_APPLY)
        plan = plan_on(now, recheck.REASON_DURING_APPLY)
        rows_by_id = {c.custom_card_id: c for c in now.custom_cards if c.custom_card_id is not None}
        payload = [custom_card_entry(p, rows_by_id.get(p["custom_card_id"])) for p in plan]
        await self._send(sub, deck.id, payload, progress)
        verified = await self.get_deck(sub, deck.id)
        self.recheck_waiting(sub, verified)
        mismatches = custom_card_mismatches(plan, names, verified)
        result = {
            "snapshot_id": snapshot_id,
            **backup,
            "sent_entries": len(payload),
            "verified": not mismatches,
            "mismatched_cards": mismatches,
            "after_fingerprint": verified.fingerprint(),
        }
        if mismatches:
            progress.update(result)  # apply() records it with the error
            raise DeckError(
                "verify_mismatch",
                "Archidekt accepted the request but the deck does not match the proposal for: "
                + ", ".join(mismatches)
                + f". A snapshot ({snapshot_id}) of the deck's cards and details before the change "
                "was kept; it does not hold custom cards.",
            )
        self.db.finish_proposal(row["id"], state="applied", result=result)
        return self.describe(sub, row["id"])

    # -- hand actions (the member's own browser; the assistant reaches folder, tag and cover only
    # through a details proposal, which calls these after the member approves) ----------------
    # Deleting a deck, picking its cover, moving it between folders and tagging it are done on the
    # web pages only, like Archidekt's own buttons. Each takes a gateway snapshot first (deletion
    # also keeps the Archidekt backup copy when backups are on) and re-reads to verify.

    MAX_TAGS = 20
    MAX_FOLDERS = 200
    TAG_LOOKUP_SECONDS = 300.0
    TAG_LOOKUP_ENTRIES = 512
    TAG_LOOKUP_NAMES = 20

    def _hand_snapshot(self, sub: str, deck: Deck, proposal_id: str | None = None) -> str:
        snapshot_id = secrets.token_urlsafe(12)
        if not self.db.save_snapshot(
            snapshot_id,
            owner_sub=sub,
            deck_id=deck.id,
            proposal_id=proposal_id,
            fingerprint=deck.fingerprint(),
            deck=deck.raw,
        ):
            raise DeckError("unavailable", "The snapshot could not be stored; nothing was sent to Archidekt.")
        return snapshot_id

    def _writes_switch(self) -> None:
        """Refuse a hand write when MTG_WRITES_ENABLED is false, as every proposal is refused. The
        deck settings page and the Folders page call it: deck delete and new folder (0.7.25), the
        cover, tags, deck move and folder rename (0.7.26); likes, bookmarks, follows, comments and
        collaborators (social.py) and collection edits (collection.py) too."""
        if not self.settings.writes_enabled:
            raise DeckError("writes_disabled", refusals.WRITES_OFF)

    async def prepare_delete(self, sub: str, deck_id: str) -> Deck:
        """The delete box opening: read the deck (its name and card count for the box) and, while
        the member types the name, find the backup folder. A delete_deck within
        DELETE_PREP_SECONDS uses both, so pressing Delete waits for four Archidekt calls instead of
        six. Only a look-up: the folder is still made by the delete when it is missing."""
        deck = await self.get_own_deck(sub, deck_id)
        key = (sub, str(deck.id))
        entry: dict[str, Any] = {
            "at": time.monotonic(),
            "deck": deck,
            "folder": None,
            "link": self._link_id(sub),
        }
        self._delete_prep[key] = entry
        while len(self._delete_prep) > DELETE_PREP_REMEMBERED:
            self._delete_prep.pop(next(iter(self._delete_prep)))
        if self.live.get("archidekt_backups"):
            name = self.backup_folder

            async def find() -> None:
                try:
                    entry["folder"] = await self._call(
                        sub, lambda token, *_: self.client.find_folder(token, name=name)
                    )
                    entry["folder_name"] = name
                except DeckError:
                    pass  # the delete looks it up again

            task = asyncio.create_task(find(), context=contextvars.Context())
            self._prep_tasks.add(task)
            task.add_done_callback(self._prep_tasks.discard)
        return deck

    def _prepared(self, sub: str, deck_id: str) -> dict[str, Any] | None:
        """The delete box's read, used once: while it is under DELETE_PREP_SECONDS old, nothing the
        gateway sent to Archidekt since could have changed the deck (_on_archidekt_write forgets
        every box's read) and the member is still linked to the Archidekt account it was read with."""
        entry = self._delete_prep.pop((sub, str(deck_id)), None)
        if entry is None or time.monotonic() - entry["at"] > DELETE_PREP_SECONDS:
            return None
        if entry.get("link") != self._link_id(sub):
            return None
        return entry

    def _link_id(self, sub: str) -> str:
        link = self.db.get_link(sub) or {}
        return str(link.get("archidekt_user_id") or "")

    async def delete_deck(
        self,
        sub: str,
        deck_id: str,
        typed_name: str,
        *,
        progress: dict[str, Any] | None = None,
        proposal_id: str | None = None,
    ) -> dict[str, Any]:
        """Delete one of the member's own decks after they typed its exact name. A snapshot is kept
        in the gateway and, when backups are on, a copy in the backup folder on Archidekt; the
        deletion is verified by reading the deck back (it must be gone). ``progress``: a
        delete-deck action's apply, which records the snapshot and the copy in it as every apply
        does, so an apply that stops before the deck is deleted deletes the unused
        copy or queues its delete instead of leaving it unrecorded. ``proposal_id``: that action's
        proposal, which the snapshot names so the snapshot list says what it was taken for."""
        self._writes_switch()
        # the member's own press right after their delete box (never an assistant's proposal,
        # which has no box): the deck and the backup folder the box read moments ago
        prepared = self._prepared(sub, _clean_deck_id(deck_id)) if progress is None else None
        deck = prepared["deck"] if prepared else await self.get_own_deck(sub, deck_id)
        folder = None
        if prepared and prepared.get("folder") and prepared.get("folder_name") == self.backup_folder:
            folder = prepared["folder"]
        # a deck with no name shows as "Unnamed deck", so that is what the member types
        expected = clean_text(deck.name) or "Unnamed deck"
        if clean_text(typed_name).casefold() != expected.casefold():
            raise DeckError(
                "invalid", "The name you typed does not match the deck's name. Nothing was deleted."
            )
        snapshot_id = self._hand_snapshot(sub, deck, proposal_id)
        if progress is not None:
            progress["snapshot_id"] = snapshot_id
        backup: dict[str, Any] = {}
        if self.live.get("archidekt_backups"):
            try:
                backup = await self._backup_copy(
                    sub, deck, snapshot_id, reason="before it was deleted", folder=folder
                )
                if progress is not None:
                    progress.update(backup)
            except DeckError as exc:
                self._audit(
                    "backup_failed", sub=sub, detail={"deck_id": deck.id, "error": exc.kind, "hand": "delete"}
                )
                raise DeckError(
                    "backup_failed",
                    f"The backup copy of '{deck.name}' could not be made on Archidekt ({exc}). The deck was "
                    "not deleted.",
                ) from exc
        await self._call(sub, self.client.delete_deck, deck.id)
        gone = False
        try:
            await self.get_deck(sub, deck.id)
        except DeckError as exc:
            gone = exc.kind in ("not_found", "forbidden")
        self._audit(
            "deck_deleted",
            sub=sub,
            detail={"deck_id": deck.id, "snapshot_id": snapshot_id, "verified": gone, **backup},
        )
        if not gone:
            raise DeckError(
                "verify_mismatch",
                f"Archidekt accepted the request but '{deck.name}' can still be read. Check it on Archidekt.",
            )
        return {"deck_id": deck.id, "name": deck.name, "snapshot_id": snapshot_id, **backup}

    async def set_package(
        self, sub: str, deck_id: str, plan: dict[str, Any], *, progress: dict[str, Any] | None = None
    ) -> None:
        """A card package's keywords, budget and formats: the deck settings' second request, by the
        package block's own id, with the whole block. Verified by re-reading the deck."""
        from . import packages

        self._writes_switch()
        deck = await self.get_own_deck(sub, deck_id)
        block = packages.block_of(deck)
        if block is None or int(block["id"]) != int(plan["id"]):
            raise DeckError("invalid", "The deck is no longer that card package; its settings were not sent.")
        want = plan["block"]
        pid = int(block["id"])
        await _writing(
            progress, self._call(sub, lambda token, *_: self.client.update_package(token, pid, want))
        )
        verified = await self.get_deck(sub, deck.id)
        differ = packages.differences(packages.block_of(verified), want)
        if differ:
            raise DeckError(
                "verify_mismatch", f"Sent, but Archidekt saved it differently: {'; '.join(differ)}."
            )

    async def set_cover(
        self, sub: str, deck_id: str, scryfall_uid: str | None, *, progress: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Set the deck's cover image to the art of one of its cards (any zone), or back to
        Archidekt's automatic pick with ``None``. Verified by re-reading the deck. ``progress``:
        a details apply's, marked written as the write returns (see ``_apply_organise``)."""
        self._writes_switch()
        deck = await self.get_own_deck(sub, deck_id)
        uid = str(scryfall_uid or "").strip().lower()
        card_name = ""
        if uid:
            card = next((c for c in deck.cards if c.scryfall_uid.lower() == uid), None)
            if card is None:
                raise DeckError("invalid", "Pick a card that is in this deck for its cover.")
            card_name = card.name
            try:
                url = art_url(uid, card.image_hash)
            except ArchidektError as exc:
                raise DeckError("invalid", "That card has no usable art id.") from exc
        else:
            url = ""
        # The chosen cover is customFeatured, as archidekt.com's own "Set Deck Image" sends it;
        # "" hands the choice back to Archidekt. A URL is compared by the art id inside it.
        if _cover_is(deck, uid):
            return {"deck_id": deck.id, "featured": deck.featured, "changed": False, "card_name": card_name}
        snapshot_id = self._hand_snapshot(sub, deck)
        await _writing(progress, self._call(sub, self.client.update_deck, deck.id, {"customFeatured": url}))
        verified = await self.get_deck(sub, deck.id)
        ok = _cover_is(verified, uid)
        self._audit(
            "deck_cover_changed",
            sub=sub,
            detail={"deck_id": deck.id, "snapshot_id": snapshot_id, "auto": not uid, "verified": ok},
        )
        if not ok:
            raise DeckError(
                "verify_mismatch",
                "Archidekt accepted the request but the deck's cover did not change as asked. "
                f"A snapshot ({snapshot_id}) from before was kept.",
            )
        return {
            "deck_id": deck.id,
            "featured": verified.featured,
            "changed": True,
            "snapshot_id": snapshot_id,
            "card_name": card_name,
        }

    # Folders -----------------------------------------------------------------------------------
    async def folders(self, sub: str) -> dict[str, Any]:
        """The member's folder tree flattened, root first: ``{root_id, folders: [{id, name, depth,
        parent, private, backup}]}``, read from Archidekt now. ``backup`` marks the gateway's
        backup folder and everything inside it (``visible_folders`` leaves them out); the
        checks before a folder write still see them."""
        tree = await self._call(sub, self.client.folder_tree)
        return self._flatten_folders(tree)

    async def folder_view(
        self, sub: str, *, wait: float | None = None, last_read_ok: bool = False
    ) -> dict[str, Any] | None:
        """``folders`` from the member cache (fresh for DECK_LIST_FRESH, then refreshed in the
        background like the deck list), for My decks. None when the tree is cold and ``wait``
        ran out; the read carries on and lands in the cache. ``last_read_ok`` (My decks, which
        says how old its list is): while Archidekt can't be asked, the tree as last read, up to
        DECK_LIST_LAST old; never for an assistant."""

        async def fetch() -> dict[str, Any]:
            return await self._call(sub, self.client.folder_tree)

        try:
            tree = await self.folder_trees.get(sub, fetch, wait=wait)
        except DeckError as exc:
            # Archidekt can't be asked: the tree as last read, as the deck list (last_deck_list)
            held = self.folder_trees.last(sub, DECK_LIST_LAST) if last_read_ok and is_busy(exc) else None
            if held is None:
                raise
            tree = held[1]
        return None if tree is None else self._flatten_folders(tree)

    @property
    def backup_folder(self) -> str:
        """The name of the members' backup folder on Archidekt, as the Settings page has it now."""
        return str(self.live.get("archidekt_backup_folder"))

    def _is_backup_folder(self, name: str, depth: int) -> bool:
        """The gateway's backup folder: the folder the deck list treats as it (its exact name, as
        archidekt.list_decks compares), or the one ensure_folder would reuse (the same name in
        other capitals, directly under the top level). Also while backups are switched off: the
        folder never shows, and a deck in it is a backup copy either way."""
        wanted = self.backup_folder
        return name == wanted or (depth == 1 and name.casefold() == wanted.casefold())

    def _flatten_folders(self, tree: dict[str, Any]) -> dict[str, Any]:
        out: list[dict[str, Any]] = []

        def walk(node: dict[str, Any], depth: int, parent: int | None, hidden: bool) -> None:
            if len(out) >= self.MAX_FOLDERS or not isinstance(node.get("id"), int):
                return
            raw = clean_text(str(node.get("name") or ""))
            hidden = hidden or (depth > 0 and self._is_backup_folder(raw, depth))
            out.append(
                {
                    "id": node["id"],
                    # Archidekt's top-level folder has a technical name; the pages call it by what it is
                    "name": "Top level (no folder)" if depth == 0 else raw or "Folder",
                    "depth": depth,
                    "parent": parent,
                    "private": bool(node.get("private")),
                    "backup": hidden,
                }
            )
            children = node.get("children") or []
            for child in sorted(
                (c for c in children if isinstance(c, dict)), key=lambda c: str(c.get("name") or "").lower()
            ):
                walk(child, depth + 1, node["id"], hidden)

        walk(tree, 0, None, False)
        return {"root_id": tree["id"], "folders": out}

    async def folder_named(self, sub: str, name: str) -> int | None:
        """The id of the member's one folder called ``name`` (any case), None for the top level
        ("", "top", "top level", "none"); refused as Deck settings' folder field refuses it."""
        want = clean_text(str(name or "")).casefold()
        if not want or want in ("top", "top level", "none"):
            return None
        info = await self.folders(sub)
        hits = [f for f in _shown_folders(info) if f["name"].casefold() == want]
        if len(hits) != 1:
            raise _no_such_folder(info, name, several=bool(hits))
        return int(hits[0]["id"])

    async def _folder_in(self, sub: str, folder_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
        info = await self.folders(sub)
        for f in info["folders"]:
            if f["id"] == folder_id:
                return info, f
        raise DeckError("not_found", "That folder is not in your Archidekt account.")

    @staticmethod
    def _folder_name(name: Any) -> str:
        clean = clean_text(str(name or ""))
        if not clean or len(clean) > 100:
            raise DeckError("invalid", "A folder name is 1 to 100 characters.")
        return clean

    async def check_new_folder(
        self, sub: str, name: str, parent_id: int | None = None
    ) -> tuple[str, dict[str, Any]]:
        """(clean name, parent folder) for a new folder, refused as create_folder would refuse it;
        nothing is created. The Folders page's confirmation asks about what this returns."""
        self._writes_switch()
        name = self._folder_name(name)
        info = await self.folders(sub)
        parent = int(parent_id) if parent_id is not None else int(info["root_id"])
        found = next((f for f in info["folders"] if f["id"] == parent), None)
        if found is None:
            raise DeckError("not_found", "That parent folder is not in your Archidekt account.")
        if found.get("backup"):  # no new folder in the gateway's backup folder
            raise _no_such_folder(info, found["name"])
        self._not_being_deleted(sub, parent)
        self._not_the_backup_name(name, found["depth"] + 1)
        if any(f["parent"] == parent and f["name"].casefold() == name.casefold() for f in info["folders"]):
            raise DeckError("invalid", f"There is already a folder called “{name}” there.")
        return name, found

    def _not_the_backup_name(self, name: str, depth: int) -> None:
        """Refuse a folder name that would make the gateway take that folder for its backup folder
        (hidden from My decks, its decks listed as backup copies): the same test the folder
        list uses, _is_backup_folder.

        Frozen interface (0.7.29): the folder actions (actions.py) rely on this signature and
        behaviour; a change goes through the owner of the folder helpers."""
        if self._is_backup_folder(name, depth):
            raise DeckError(
                "invalid", f"'{name}' is the name of the gateway's backup folder. Pick another name."
            )

    async def create_folder(self, sub: str, name: str, parent_id: int | None = None) -> dict[str, Any]:
        name, found = await self.check_new_folder(sub, name, parent_id)
        parent = int(found["id"])
        made = await self._call(sub, lambda token, *_: self.client.create_folder(token, name, parent))
        after, folder = await self._folder_in(sub, int(made["id"]))
        self._audit("folder_created", sub=sub, detail={"folder_id": folder["id"], "parent": parent})
        return folder

    async def rename_folder(self, sub: str, folder_id: int, name: str) -> dict[str, Any]:
        self._writes_switch()
        name = self._folder_name(name)
        info, folder = await self._folder_in(sub, int(folder_id))
        if folder["depth"] == 0:
            raise DeckError("invalid", "The top-level folder cannot be renamed.")
        if folder.get("backup"):  # the gateway's backup folder and what is in it
            raise _no_such_folder(info, folder["name"])
        if folder["name"] == name:
            return folder
        self._not_the_backup_name(name, folder["depth"])
        await self._call(
            sub,
            lambda token, *_: self.client.mass_update(
                token, [{"id": folder["id"], "type": "folder", "patch": {"name": name}}]
            ),
        )
        _after, now = await self._folder_in(sub, folder["id"])
        self._audit(
            "folder_renamed", sub=sub, detail={"folder_id": folder["id"], "verified": now["name"] == name}
        )
        if now["name"] != name:
            raise DeckError(
                "verify_mismatch", "Archidekt accepted the request but the folder's name did not change."
            )
        return now

    async def move_deck(
        self, sub: str, deck_id: str, folder_id: int | None, *, progress: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Move one of the member's decks into a folder (``None`` = the top-level folder); verified
        by re-reading the deck's ``parentFolder``. ``progress`` as for ``set_cover``."""
        self._writes_switch()
        deck = await self.get_own_deck(sub, deck_id)
        info = await self.folders(sub)
        root = int(info["root_id"])
        target = int(folder_id) if folder_id is not None else root
        if target not in {f["id"] for f in info["folders"]}:
            raise DeckError("not_found", "That folder is not in your Archidekt account.")
        current = deck.parent_folder if deck.parent_folder is not None else root
        if current == target:
            return {"deck_id": deck.id, "folder_id": target, "changed": False}
        hidden = next((f for f in info["folders"] if f["id"] == target and f.get("backup")), None)
        if hidden is not None:  # never into the gateway's backup folder, as by name
            raise _no_such_folder(info, hidden["name"])
        self._not_being_deleted(sub, target)
        # the item archidekt.com's folder page sends for its "Move" (its bundle, read 2026-10-14)
        await _writing(
            progress,
            self._call(
                sub,
                lambda token, *_: self.client.mass_update(
                    token, [{"type": "deck", "id": int(deck.id), "patch": {"parentFolder": target}}]
                ),
            ),
        )
        verified = await self.get_deck(sub, deck.id)
        now = verified.parent_folder if verified.parent_folder is not None else root
        self._audit(
            "deck_moved", sub=sub, detail={"deck_id": deck.id, "folder_id": target, "verified": now == target}
        )
        if now != target:
            raise DeckError(
                "verify_mismatch", "Archidekt accepted the request but the deck is not in that folder."
            )
        return {"deck_id": deck.id, "folder_id": target, "changed": True}

    # Moving and deleting a folder: the member's own presses on My decks only, never an
    # assistant's tool or proposal. Both are kinds of Archidekt writes the gateway did not send
    # before; the routes are those of archidekt.com's own folder page (its bundle, read
    # 2026-10-20) and no session has sent them live yet, so each is checked by reading back.
    FOLDER_PAGES = 40  # pages of one folder's listing read before the count counts as unknown
    TOO_MUCH_TO_LIST = "The folder holds more than the gateway can list, so it can't be deleted here."

    @staticmethod
    def _inside(info: dict[str, Any], folder_id: int) -> list[dict[str, Any]]:
        """Every folder below ``folder_id`` in the flattened tree, at any depth."""
        below: list[dict[str, Any]] = []
        parents = {int(folder_id)}
        for f in info["folders"]:  # parents come before their children (``_flatten_folders``)
            if f["parent"] in parents:
                below.append(f)
                parents.add(f["id"])
        return below

    def _movable_folder(self, info: dict[str, Any], folder: dict[str, Any]) -> None:
        if folder["depth"] == 0:
            raise DeckError("invalid", "Your top level can't be moved or deleted.")
        if folder.get("backup"):  # the gateway's backup folder and what is in it
            raise _no_such_folder(info, folder["name"])

    def _not_being_deleted(self, sub: str, folder_id: int, *, moving: bool = False) -> None:
        """Refused while a running folder delete holds ``folder_id``: as the place something goes
        into, or (``moving``) as the folder being moved."""
        if folder_id in self._deleting.get(sub, set()):
            if moving:
                raise DeckError("busy", "That folder is being deleted, so it can't be moved now.")
            raise DeckError("busy", "That folder is being deleted, so nothing can go into it now.")

    async def move_folder(self, sub: str, folder_id: int, parent_id: int | None) -> dict[str, Any]:
        """Put one of the member's folders, with everything inside it, into another folder
        (``None`` = the top level). Refused into itself or one of its own folders, for the
        backup folder, and into the backup folder. Checked by reading the tree back."""
        self._writes_switch()
        info, folder = await self._folder_in(sub, int(folder_id))
        self._movable_folder(info, folder)
        target_id = int(parent_id) if parent_id is not None else int(info["root_id"])
        target = next((f for f in info["folders"] if f["id"] == target_id), None)
        if target is None:
            raise DeckError("not_found", "That folder is not in your Archidekt account.")
        if target_id == folder["id"] or target_id in {f["id"] for f in self._inside(info, folder["id"])}:
            raise DeckError("invalid", "A folder can't go inside itself or one of its own folders.")
        if target.get("backup"):
            raise _no_such_folder(info, target["name"])
        if any(f.get("backup") for f in self._inside(info, folder["id"])):
            raise DeckError(
                "backup_inside",
                f"The gateway's backup folder is inside “{folder['name']}”, so it can't be moved. Move "
                f"the backup folder out of “{folder['name']}” on Archidekt first.",
            )
        self._not_being_deleted(sub, target_id)
        self._not_being_deleted(sub, folder["id"], moving=True)
        if folder["parent"] == target_id:
            return {**folder, "changed": False}
        if any(
            f["parent"] == target_id and f["name"].casefold() == folder["name"].casefold()
            for f in info["folders"]
        ):
            raise DeckError("invalid", f"There is already a folder called “{folder['name']}” there.")
        # a folder sitting directly under the top level with the backup folder's name would be
        # taken for it
        self._not_the_backup_name(folder["name"], target["depth"] + 1)
        await self._call(
            sub,
            lambda token, *_: self.client.mass_update(
                token, [{"type": "folder", "id": folder["id"], "patch": {"parentFolder": target_id}}]
            ),
        )
        _after, now = await self._folder_in(sub, folder["id"])
        ok = now["parent"] == target_id
        self._audit(
            "folder_moved", sub=sub, detail={"folder_id": folder["id"], "parent": target_id, "verified": ok}
        )
        if not ok:
            raise DeckError("verify_mismatch", "Archidekt accepted the request but the folder did not move.")
        return {**now, "changed": True}

    async def _folder_items(
        self, sub: str, folder_id: int
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], set[int]]:
        """(decks, card packages, the ids of the folders) directly in one folder, every page of its
        listing read now. Anything that can't be read completely raises: a delete never goes
        ahead on a part list."""
        decks: list[dict[str, Any]] = []
        packages: list[dict[str, Any]] = []
        subfolders: set[int] = set()
        for page in range(1, self.FOLDER_PAGES + 1):
            body = await self._call(
                sub, lambda token, *_, page=page: self.client.folder_details(token, folder_id, page)
            )
            for d in body["decks"]:
                item = {"id": str(d["id"]), "name": clean_text(str(d.get("name") or ""))}
                (packages if d.get("cardPackage") else decks).append(item)
            subs = body.get("subfolders")
            if not isinstance(subs, list) or not all(isinstance(f, dict) and "id" in f for f in subs):
                raise DeckError("unavailable", self.TOO_MUCH_TO_LIST)
            subfolders |= {int(f["id"]) for f in subs}
            if not body.get("next"):
                return decks, packages, subfolders
        raise DeckError("unavailable", self.TOO_MUCH_TO_LIST)

    async def folder_contents(self, sub: str, folder_id: int) -> dict[str, Any]:
        """What deleting a folder would delete on Archidekt: its decks and card packages, those of
        every folder inside it, and those folders. Refused for the top level, the backup folder,
        and a folder with the backup folder anywhere inside it (one delete would take the backup
        copies with it)."""
        info, folder = await self._folder_in(sub, int(folder_id))
        self._movable_folder(info, folder)
        below = self._inside(info, folder["id"])
        # the backup folder is known by its name; one renamed on Archidekt by the gateway's
        # record of the folders it put backup copies in
        recorded = self.db.backup_folder_ids(sub)
        if folder["id"] in recorded:
            raise DeckError(
                "backup_inside",
                f"“{folder['name']}” is the folder the gateway keeps backup copies of your decks in, "
                "renamed on Archidekt. Deleting it would delete the backup copies, so it can't be "
                "deleted here.",
            )
        if any(f.get("backup") or f["id"] in recorded for f in below):
            raise DeckError(
                "backup_inside",
                f"The gateway's backup folder is inside “{folder['name']}”, and deleting it would delete "
                "the backup copies too. Move the backup folder out of "
                f"“{folder['name']}” on Archidekt first.",
            )
        decks: list[dict[str, Any]] = []
        packages: list[dict[str, Any]] = []
        for f in [folder, *below]:
            d, p, subs = await self._folder_items(sub, f["id"])
            decks += d
            packages += p
            # the folder tree is read up to a limit: a folder Archidekt lists inside that the tree
            # left out would be deleted unseen, with no snapshot of its decks
            if subs != {g["id"] for g in below if g["parent"] == f["id"]}:
                raise DeckError("unavailable", self.TOO_MUCH_TO_LIST)
        # any other folder holding backup copies the gateway recorded making (moved there on
        # Archidekt) is not deleted like any other folder either
        made = self.db.backup_copies(sub)
        if any(d["id"] in made for d in decks):
            raise DeckError(
                "backup_inside",
                f"“{folder['name']}” holds backup copies the gateway made of your decks, and deleting it "
                "would delete them too. Move those decks out of it on Archidekt first.",
            )
        return {"folder": folder, "decks": decks, "packages": packages, "folders": below}

    LIMIT_WAITS = 30  # waits for the member's own Archidekt limit within one folder delete

    async def _waiting_out_limits(self, state: dict[str, Any], run: Any) -> Any:
        """``await run()``, waiting and trying again while the member's own Archidekt limit (their
        actions per 10 minutes, or their actions already running) refuses it: a folder delete
        makes several calls for every deck inside, more than the limit lets through at once. The
        wait shows in ``state["step"]``, which is put back afterwards."""
        for _ in range(self.LIMIT_WAITS):
            try:
                return await run()
            except DeckError as exc:
                # Archidekt does not act on a request it answers with a 429, so sending it again
                # can't do anything twice
                if exc.kind != "rate_limited":
                    raise
                wait = max(1, min(int(exc.retry_after or 5), 60))
                step = state.get("step")
                if exc.busy_cause == "budget":
                    state["step"] = BUSY_TEXTS["budget"]
                elif exc.busy_cause == "member_slots":
                    state["step"] = (
                        "Your account already has several Archidekt actions running or waiting, so "
                        "the folder delete is waiting for a free turn."
                    )
                else:
                    state["step"] = (
                        "Archidekt asked the gateway to slow down, so it is pausing the folder delete "
                        f"for {wait} second{'' if wait == 1 else 's'}."
                    )
                await asyncio.sleep(wait)
                state["step"] = step
        return await run()

    async def delete_folder(
        self,
        sub: str,
        folder_id: int,
        typed_name: str,
        shown: dict[str, Any],
        *,
        progress: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Delete one of the member's folders with everything inside it, after they typed its name
        (case ignored) in a box that showed the counts in ``shown`` ({decks, packages, folders}).
        Everything inside is listed again first and must still match those counts. Every deck
        gets a snapshot, and a backup copy when backups are on, before anything is deleted; the
        first failure stops the delete. Then one Archidekt call deletes the folder with all it
        holds (it can't be stopped or partly undone), and the folder and its decks are read back
        to check they are gone. Card packages get no snapshot or copy. ``progress`` (the job's)
        gets ``step``, ``done`` and ``total`` as it goes."""
        self._writes_switch()
        state = progress if progress is not None else {}
        state.update(step="Reading what is inside the folder", done=0, total=0)
        inside = await self.folder_contents(sub, int(folder_id))
        folder = inside["folder"]
        if clean_text(typed_name).casefold() != folder["name"].casefold():
            raise DeckError(
                "invalid", "The name you typed does not match the folder's name. Nothing was deleted."
            )
        now = {k: len(inside[k]) for k in ("decks", "packages", "folders")}
        if any(_count_of(shown.get(k)) != now[k] for k in now):
            raise DeckError(
                "changed",
                "What is inside the folder changed since you looked. Nothing was deleted; check the new "
                "counts and confirm again.",
                counts=now,
            )
        held = {folder["id"], *(f["id"] for f in inside["folders"])}
        if self._deleting.get(sub):
            raise DeckError("busy", "A folder of yours is already being deleted. Wait for it to finish.")
        self._deleting[sub] = held
        try:
            return await self._delete_folder_held(sub, inside, now, state)
        finally:
            self._deleting.pop(sub, None)

    async def _delete_folder_held(
        self, sub: str, inside: dict[str, Any], now: dict[str, int], state: dict[str, Any]
    ) -> dict[str, Any]:
        """delete_folder once the counts matched, with nothing moved into the folder meanwhile."""
        folder = inside["folder"]
        snapshots: list[dict[str, Any]] = []
        state.update(total=len(inside["decks"]))
        backups = bool(self.live.get("archidekt_backups"))
        for n, item in enumerate(inside["decks"], 1):
            state["step"] = (
                f"Snapshot and backup copy {n} of {len(inside['decks'])}"
                if backups
                else f"Snapshot {n} of {len(inside['decks'])}"
            )
            try:
                deck = await self._waiting_out_limits(
                    state, lambda deck_id=item["id"]: self.get_own_deck(sub, deck_id)
                )
                snapshot_id = self._hand_snapshot(sub, deck)
                made: dict[str, Any] = {"deck_id": deck.id, "name": deck.name, "snapshot_id": snapshot_id}
                if backups:
                    made.update(
                        await self._waiting_out_limits(
                            state,
                            lambda deck=deck, snapshot_id=snapshot_id: self._backup_copy(
                                sub, deck, snapshot_id, reason="before its folder was deleted"
                            ),
                        )
                    )
            except DeckError as exc:
                self._audit(
                    "folder_delete_stopped",
                    sub=sub,
                    detail={
                        "folder_id": folder["id"],
                        "deck_id": item["id"],
                        "error": exc.kind,
                        "made": len(snapshots),
                    },
                )
                which = f"The deck “{item['name']}”" if item["name"] else "An unnamed deck"
                why = str(exc).rstrip(".") or "Archidekt did not answer"
                kept = "; the snapshots and backup copies already made are kept." if snapshots else "."
                raise DeckError(
                    "backup_failed", f"{which} could not be saved first ({why}). Nothing was deleted{kept}"
                ) from exc
            snapshots.append(made)
            state["done"] = n
        # read again just before the step that can't be undone: anything that arrived while the
        # snapshots were made would be deleted without one
        state["step"] = "Reading what is inside the folder"
        again = await self._waiting_out_limits(state, lambda: self.folder_contents(sub, folder["id"]))
        if any(
            {x["id"] for x in again[k]} != {x["id"] for x in inside[k]}
            for k in ("decks", "packages", "folders")
        ):
            raise DeckError(
                "changed",
                "What is inside the folder changed while the decks were being saved. Nothing was "
                "deleted; the snapshots and backup copies already made are kept. Check the new counts "
                "and confirm again.",
                counts={k: len(again[k]) for k in ("decks", "packages", "folders")},
            )
        state["step"] = "Deleting the folder on Archidekt"
        state["delete_sent"] = True  # from here on a failure may have deleted something
        try:
            await self._waiting_out_limits(
                state,
                lambda: self._call(sub, lambda token, *_: self.client.delete_folders(token, [folder["id"]])),
            )
        except DeckError as exc:
            if not exc.sent or exc.kind in NOT_ACTED_ON:
                state["delete_sent"] = False
                raise DeckError(
                    exc.kind, f"{str(exc).rstrip('.')}. Nothing was deleted.", sent=False
                ) from exc
            raise DeckError(
                "unknown",
                "Archidekt did not say whether the folder was deleted. Check it on Archidekt; every "
                "deck's snapshot is under History.",
            ) from exc
        state["step"] = "Checking that everything is gone"
        try:
            after = await self._waiting_out_limits(state, lambda: self.folders(sub))
            listed = {
                str(d.get("id")) for d in await self._waiting_out_limits(state, lambda: self.list_decks(sub))
            }
        except DeckError as exc:
            # the delete was sent: the answer must not read as if nothing happened
            raise DeckError(
                exc.kind, f"{str(exc).rstrip('.')}. Check the folder on Archidekt to see what was deleted."
            ) from exc
        left_folders = [
            f for f in [folder, *inside["folders"]] if any(g["id"] == f["id"] for g in after["folders"])
        ]
        left_decks = [d for d in inside["decks"] if d["id"] in listed]
        ok = not left_folders and not left_decks
        self._audit(
            "folder_deleted",
            sub=sub,
            detail={
                "folder_id": folder["id"],
                "decks": now["decks"],
                "packages": now["packages"],
                "folders": now["folders"],
                "snapshots": [s["snapshot_id"] for s in snapshots],
                "verified": ok,
            },
        )
        if not ok:
            names = [f["name"] for f in left_folders] + [d["name"] or "Unnamed deck" for d in left_decks]
            raise DeckError(
                "verify_mismatch",
                "Archidekt accepted the request but these are still there: "
                + ", ".join(names[:10])
                + ". Check them on Archidekt. Every deck's snapshot is under History.",
            )
        return {"folder": folder, "counts": now, "snapshots": snapshots}

    # Tags --------------------------------------------------------------------------------------
    @staticmethod
    def _tag_name(name: Any) -> str:
        clean = clean_text(str(name or "")).strip("#").strip()
        if not clean or len(clean) > 40:
            raise DeckError("invalid", "A tag is 1 to 40 characters.")
        return clean

    async def _archidekt_tag(self, sub: str, name: str) -> tuple[int, str]:
        """The id and exact name of Archidekt's tag called ``name``. Archidekt keeps one list of
        deck tags and its own site only picks from it, so a name it doesn't have is refused here,
        with its closest tags, instead of being sent to be created."""
        found = await self._call(sub, lambda token, *_: self.client.search_tags(token, name))
        for t in found:
            if str(t.get("name") or "").casefold() == name.casefold() and isinstance(t.get("id"), int):
                return t["id"], str(t["name"])
        close = [str(t.get("name")) for t in found if t.get("name")][:8]
        raise DeckError(
            "invalid",
            f"Archidekt has no deck tag called '{name}'. Tags are picked from Archidekt's own list"
            + (f"; close ones: {', '.join(close)}." if close else "; try a shorter or different word."),
        )

    async def tag_suggestions(self, sub: str, text: Any) -> list[str]:
        """Archidekt's tags for what the member is typing in the settings page's tag box (its own
        site's tag picker lookup, ``search_tags``): one paced request per new text of at least two
        characters, then remembered for TAG_LOOKUP_SECONDS, since the box asks as the member types
        and Archidekt's terms ask for gentle use."""
        q = clean_text(str(text or "")).strip("#").strip()[:40]
        if len(q) < 2:
            return []
        key = q.casefold()
        now = time.monotonic()
        hit = self._tag_lookups.get(key)
        if hit and hit[0] > now:
            return list(hit[1])
        found = await self._call(sub, lambda token, *_: self.client.search_tags(token, q))
        names: list[str] = []
        for t in found:
            name = str(t.get("name") or "").strip()
            if name and name.casefold() not in {n.casefold() for n in names}:
                names.append(name)
        names = names[: self.TAG_LOOKUP_NAMES]
        self._tag_lookups.pop(key, None)
        self._tag_lookups[key] = (now + self.TAG_LOOKUP_SECONDS, names)
        while len(self._tag_lookups) > self.TAG_LOOKUP_ENTRIES:
            self._tag_lookups.pop(next(iter(self._tag_lookups)))
        return list(names)

    async def add_tag(
        self, sub: str, deck_id: str, name: str, *, progress: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Tag one of the member's decks with one of Archidekt's tags (see ``_archidekt_tag``).
        Verified by re-reading the deck's tags. ``progress`` as for ``set_cover``."""
        self._writes_switch()
        name = self._tag_name(name)
        deck = await self.get_own_deck(sub, deck_id)
        if any(str(r.get("name") or "").casefold() == name.casefold() for r in deck.tag_relations):
            raise DeckError("invalid", f"This deck already has the tag '{name}'.")
        if len(deck.tag_relations) >= self.MAX_TAGS:
            raise DeckError("invalid", f"A deck has at most {self.MAX_TAGS} tags here.")
        tag_id, name = await self._archidekt_tag(sub, name)
        snapshot_id = self._hand_snapshot(sub, deck)
        position = next_tag_position([r.get("position") for r in deck.tag_relations])
        await _writing(
            progress,
            self._call(sub, lambda token, *_: self.client.add_deck_tag(token, deck.id, tag_id, position)),
        )
        verified = await self.get_deck(sub, deck.id)
        ok = any(str(r.get("name") or "").casefold() == name.casefold() for r in verified.tag_relations)
        self._audit(
            "deck_tag_added",
            sub=sub,
            detail={"deck_id": deck.id, "tag_id": tag_id, "snapshot_id": snapshot_id, "verified": ok},
        )
        if not ok:
            raise DeckError(
                "verify_mismatch", "Archidekt accepted the request but the tag is not on the deck."
            )
        return {"deck_id": deck.id, "tags": verified.tag_relations, "snapshot_id": snapshot_id}

    async def remove_tag(
        self, sub: str, deck_id: str, relation_id: int, *, progress: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Take a tag off one of the member's decks; verified by re-reading the deck's tags.
        ``progress`` as for ``set_cover``."""
        self._writes_switch()
        deck = await self.get_own_deck(sub, deck_id)
        rel = next((r for r in deck.tag_relations if r.get("id") == int(relation_id)), None)
        if rel is None:
            raise DeckError("not_found", "That tag is not on this deck.")
        snapshot_id = self._hand_snapshot(sub, deck)
        await _writing(
            progress, self._call(sub, lambda token, *_: self.client.remove_deck_tag(token, int(relation_id)))
        )
        verified = await self.get_deck(sub, deck.id)
        ok = all(r.get("id") != int(relation_id) for r in verified.tag_relations)
        self._audit(
            "deck_tag_removed",
            sub=sub,
            detail={
                "deck_id": deck.id,
                "relation_id": int(relation_id),
                "snapshot_id": snapshot_id,
                "verified": ok,
            },
        )
        if not ok:
            raise DeckError(
                "verify_mismatch", "Archidekt accepted the request but the tag is still on the deck."
            )
        return {"deck_id": deck.id, "tags": verified.tag_relations, "snapshot_id": snapshot_id}

    # Precons -----------------------------------------------------------------------------------
    PRECON_TTL = 3600.0

    async def precons(self, sub: str | None) -> dict[str, list[dict[str, Any]]]:
        """Archidekt's preconstructed deck listing, grouped by set, cached for an hour (it changes
        a few times a year). Anonymous: the listing is public."""
        now = time.monotonic()
        cached = self._precon_cache
        if cached and now - cached[0] < self.PRECON_TTL:
            return cached[1]
        async with self.archidekt_slot(sub):
            try:
                listing = await self.client.precons()
            except ArchidektError as exc:
                if cached:
                    return cached[1]
                raise DeckError(exc.kind, str(exc), retry_after=archidekt_wait(exc)) from exc
        self._precon_cache = (now, listing)
        return listing


def _package_text(block: dict[str, Any] | None) -> str | None:
    if not block:
        return None
    from . import packages

    return packages.text(block)


def parse_details(raw: Any) -> dict[str, Any]:
    """Validate the ``details`` of propose_deck_details: a non-empty object with only known keys,
    each of the right type and range. ``deck_format`` is returned as its FORMAT_IDS key."""
    if not isinstance(raw, dict) or not raw:
        raise DeckError(
            "invalid", "details must be a non-empty object with any of: " + ", ".join(DETAIL_FIELDS)
        )
    unknown = sorted(str(k) for k in raw if k not in DETAIL_FIELDS)
    if unknown:
        known = ", ".join([*DETAIL_FIELDS, *ORGANISE_FIELDS])
        raise DeckError("invalid", f"unknown details: {', '.join(unknown)}; use {known}")
    out: dict[str, Any] = {}
    for key, value in raw.items():
        if key == "name":
            name = clean_text(value) if isinstance(value, str) else ""
            if not name or len(name) > 200:
                raise DeckError("invalid", "name must be 1 to 200 characters")
            out[key] = name
        elif key == "description":
            if not isinstance(value, str) or len(value) > MAX_DESCRIPTION:
                raise DeckError(
                    "invalid", f"description must be a string of up to {MAX_DESCRIPTION} characters"
                )
            # plain text only: a Quill document an assistant sends is kept as its words alone
            out[key] = plain_text(value)
        elif key == "deck_format":
            fmt = str(value or "").strip().lower()
            if fmt not in FORMAT_IDS:
                raise DeckError("invalid", f"deck_format must be one of: {', '.join(sorted(FORMAT_IDS))}")
            out[key] = fmt
        elif key == "edh_bracket":
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 5
            ):
                raise DeckError("invalid", "edh_bracket must be an integer from 1 to 5, or null to clear it")
            out[key] = value
        elif key == "game":
            game = value.strip().lower() if isinstance(value, str) else None
            if game not in GAME_CHOICES:
                raise DeckError("invalid", f"game must be one of: {', '.join(GAME_CHOICES)}")
            out[key] = game
        else:  # private, unlisted, theorycrafted
            if not isinstance(value, bool):
                raise DeckError("invalid", f"{key} must be true or false")
            out[key] = value
    return out


def _names(value: Any, field: str) -> list[str]:
    """A list of tag names from a details field (a list of strings, or one string)."""
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or len(value) > 20 or not all(isinstance(v, str) for v in value):
        raise DeckError("invalid", f"{field} must be a list of up to 20 tag names")
    return value


def _current_detail(deck: Deck, key: str) -> Any:
    if key == "deck_format":
        return deck.format_id
    if key == "game":
        return next((k for k, v in GAME_IDS.items() if v == deck.game), "any")
    return getattr(deck, key)


def _detail_label(key: str, value: Any) -> str:
    if key == "name":
        return json.dumps(value, ensure_ascii=False)
    if key in ("private", "unlisted", "theorycrafted"):
        return "yes" if value else "no"
    if key == "game":
        return GAME_LABELS.get(value, "Any")
    if value is None:
        return "none"
    return str(value)


def details_diff(deck: Deck, wanted: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """The subset of ``wanted`` that differs from ``deck`` and one diff line per field. A long
    description is never printed in a line: it says only that it changed and how long it is (the
    review rows carry the full text, see ``details_rows``)."""
    changes, rows = details_rows(deck, wanted)
    return changes, [row_line(r) for r in rows]


def details_rows(deck: Deck, wanted: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """``details_diff`` with review rows. A description row holds the current and the proposed
    text in full, so the review page shows exactly what an apply will write.

    Only a Commander deck has a bracket, as on archidekt.com: a bracket for a deck of
    another format is refused, and moving a deck off Commander clears the bracket it had."""
    commander = FORMAT_IDS["commander"]
    fmt_after = FORMAT_IDS[wanted["deck_format"]] if "deck_format" in wanted else deck.format_id
    if fmt_after != commander:
        if wanted.get("edh_bracket") is not None:
            raise DeckError(
                "invalid",
                "only a Commander deck has a bracket (as on archidekt.com); set the format to "
                "commander, or leave edh_bracket out",
            )
        if "deck_format" in wanted and deck.edh_bracket is not None:
            wanted = {**wanted, "edh_bracket": None}
    changes: dict[str, Any] = {}
    lines: list[dict[str, Any]] = []
    for key, value in wanted.items():
        new = FORMAT_IDS[value] if key == "deck_format" else value
        kept: tuple[str, int] | None = None
        if key == "description":
            # Archidekt keeps a Quill document (see rich_doc): the texts are compared, and the row
            # shows the very text the write sends (written_text), never a reading of it
            current, after = rich_text(deck.description), written_text(value)
            if after.strip() == current.strip():
                continue
            # New words are plain, and the parts of a formatted description whose words did
            # not change keep their bold, links and pictures; the row carries the document that is
            # written, which the review page draws before the member approves
            if not isinstance(value, QuillDoc):
                kept = formatting.keep_formatting(deck.description, after)
                if kept is not None:
                    value = QuillDoc(kept[0])
        elif new == _current_detail(deck, key):
            continue
        changes[key] = value
        if key == "description":
            lines.append(
                _row(
                    "description",
                    before_len=len(current),
                    after_len=len(after),
                    before_text=current,
                    after_text=after,
                    **({"after_doc": kept[0], "dropped_embeds": kept[1]} if kept else {}),
                    # a safe default: an edit of a formatted description that keeps none of it says so and
                    # what goes, is approved on its review page only, and Full auto waits
                    **(
                        {"formatting_lost": True, "dropped_embeds": formatting.embeds(deck.description)}
                        if not kept
                        and not isinstance(value, QuillDoc)
                        and formatting.is_formatted(deck.description)
                        else {}
                    ),
                )
            )
        elif key == "deck_format":
            lines.append(_row("detail", field="format", before=deck.format or "unknown", after=value))
        elif key == "edh_bracket":
            lines.append(
                _row(
                    "detail",
                    field="bracket",
                    before=_detail_label(key, deck.edh_bracket),
                    after=_detail_label(key, value),
                )
            )
        elif key in ("theorycrafted", "game"):
            lines.append(
                _row(
                    "detail",
                    field=key,
                    before=_detail_label(key, _current_detail(deck, key)),
                    after=_detail_label(key, value),
                )
            )
        else:
            lines.append(
                _row(
                    "detail",
                    field=key,
                    before=_detail_label(key, _current_detail(deck, key)),
                    after=_detail_label(key, value),
                    # who can see the deck before and after, from both flags (modes.auto_waits:
                    # Full auto waits for a move to unlisted or public)
                    seen_before=visibility(bool(deck.private), bool(deck.unlisted)),
                    seen_after=visibility(
                        bool(wanted.get("private", deck.private)), bool(wanted.get("unlisted", deck.unlisted))
                    ),
                )
            )
    return changes, lines


def visibility(private: bool, unlisted: bool) -> str:
    """Who can see a deck: "private" (only its owner; taken to win over unlisted, not checked on
    Archidekt), "unlisted" (anyone with the link) or "public"."""
    return "private" if private else "unlisted" if unlisted else "public"


# A description edit that keeps formatting whose description changed since its review: its
# "needs a look" reason, in words (recheck.reason_text shows any other text as it is)
DESCRIPTION_CHANGED = (
    "The description changed on Archidekt since this was reviewed, so it would not look as reviewed."
)


def _description_doc(rows: list[dict[str, Any]] | None) -> str:
    """The document a details change writes for the description when it keeps formatting, else ""."""
    return next((str(r.get("after_doc") or "") for r in rows or [] if r.get("kind") == "description"), "")


def _description_before(rows: list[dict[str, Any]] | None) -> str | None:
    """The description's words before a details change, as its review row recorded them, or None
    when the change does not touch the description (or was stored without them)."""
    return next(
        (str(r["before_text"]) for r in rows or [] if r.get("kind") == "description" and "before_text" in r),
        None,
    )


def stored_details(changes: dict[str, Any]) -> dict[str, Any]:
    """Details changes as a proposal stores them: a description as its plain words, also when
    ``details_rows`` made it a document that keeps formatting. The apply works the document
    out again on the deck as it is then, and the re-check compares it with the reviewed one."""
    if isinstance(changes.get("description"), QuillDoc):
        return {**changes, "description": written_text(changes["description"])}
    return changes


def snapshot_details(snapshot: Deck) -> dict[str, Any]:
    """The deck details a snapshot recorded, in the shape ``details_rows`` compares: name,
    description, format (as its FORMAT_IDS key, when known), bracket, private and unlisted. The
    description is Archidekt's own stored document, so it goes back with its formatting.
    The bracket is left out when the snapshot's deck was not a Commander deck: an older release
    could set one on any format, and restoring such a deck must not meet the refusal of a bracket
    on a deck that is not Commander; moving the deck off Commander still clears a bracket it has
    now (``details_rows``)."""
    out: dict[str, Any] = {
        "name": snapshot.name,
        "description": QuillDoc(snapshot.description or ""),
        "private": bool(snapshot.private),
        "unlisted": bool(snapshot.unlisted),
        "theorycrafted": bool(snapshot.theorycrafted),
        "game": _current_detail(snapshot, "game"),
    }
    if snapshot.format_id == FORMAT_IDS["commander"]:
        out["edh_bracket"] = snapshot.edh_bracket
    if snapshot.format in FORMAT_IDS:
        out["deck_format"] = snapshot.format
    return out


# -- the deck's own categories: rename and flags (kind "categories", 2026-10-21) ---------------
# Archidekt's category record: its name and three flags. The gateway, its pages and the
# assistant name the flags as the deck settings page labels them: "Premier", "Not in deck" and
# "Not in price" (pressed means on), so not_in_deck is includedInDeck turned around.
CATEGORY_FLAGS = (
    ("premier", "Premier", "isPremier", False),
    ("not_in_deck", "Not in deck", "includedInDeck", True),
    ("not_in_price", "Not in price", "includedInPrice", True),
)
MAX_CATEGORY_CHANGES = 30
# Names Archidekt treats by name, not only by flag: a row filed first under "Sideboard" is out of
# the deck's count (Deck.categories_count), and "Maybeboard" is the maybeboard by name.
SIZE_CATEGORY_NAMES = frozenset({"sideboard", "maybeboard"})
# A category change (a rename or a switch) does not reach an Archidekt editor already open on the
# deck (the site tells its open tabs over its own live connection, which the gateway does not use).
OPEN_TABS_NOTE = "Archidekt tabs already open on this deck show the old categories until they are reloaded."
# The deck settings page's notice after its own Save categories (NEW-WORDING.md).
CATEGORIES_SAVED = "Categories saved to Archidekt. A snapshot from just before is under History."
CATEGORY_GONE = "The deck has no category “{name}”."
CATEGORY_TAKEN = "The deck already has a category named “{name}”. Pick another name."


def category_state(c: dict[str, Any]) -> dict[str, Any]:
    """One category record as the gateway compares it: id (None when Archidekt sent none), name
    and the three flags in Archidekt's own terms. A flag Archidekt left out reads as its
    default (not premier, counted in the deck and in the price)."""
    cid = c.get("id")
    return {
        "id": cid if isinstance(cid, int) and not isinstance(cid, bool) else None,
        "name": str(c.get("name") or ""),
        "isPremier": c.get("isPremier") is True,
        "includedInDeck": c.get("includedInDeck") is not False,
        "includedInPrice": c.get("includedInPrice") is not False,
    }


# Every Archidekt deck has these two, not in the deck or the price, even before its answer lists
# them (a new deck's lists no category at all, Ben 2026-10-28).
DEFAULT_CATEGORIES = ("Maybeboard", "Sideboard")


def category_states(deck: Deck) -> list[dict[str, Any]]:
    """Every category of the deck as ``category_state`` reads it, in id then name order: what a
    categories change records when it is made and checks again just before it writes. A default
    category (DEFAULT_CATEGORIES) the answer leaves out is there without a record, like one
    Archidekt sends without its id: a change gives it its record."""
    out = [
        category_state(c) for c in deck.categories if isinstance(c, dict) and isinstance(c.get("name"), str)
    ]
    have = {s["name"].casefold() for s in out}
    out += [
        category_state({"name": name, "includedInDeck": False, "includedInPrice": False})
        for name in DEFAULT_CATEGORIES
        if name.casefold() not in have
    ]
    return sorted(out, key=lambda s: (s["id"] or 0, s["name"]))


def _holds_commander(deck: Deck, name: str) -> bool:
    """Whether a card in the deck's commander zone is filed under the category ``name``."""
    return any(name in c.categories and deck.is_commander(c) for c in deck.cards)


def deck_categories(deck: Deck) -> list[dict[str, Any]]:
    """The deck's categories as the deck settings page and the assistant see them: ``id``,
    ``name``, the flags ``premier``, ``not_in_deck`` and ``not_in_price``, ``cards`` (copies filed
    under it), ``commander`` (it is or holds the commander zone: changing it asks first and is
    never applied by an assistant on its own), ``editable`` (always: one without a record, Archidekt's
    default Sideboard or Maybeboard until someone creates it, is given its record by the change)
    and ``record`` (Archidekt sent its record id)."""
    out = []
    for s in category_states(deck):
        copies = sum(c.quantity for c in deck.cards if s["name"] in c.categories)
        out.append(
            {
                "id": s["id"],
                "name": s["name"],
                "premier": s["isPremier"],
                "not_in_deck": not s["includedInDeck"],
                "not_in_price": not s["includedInPrice"],
                "cards": copies,
                "commander": _touches_commander(deck, s),
                "editable": True,
                "record": s["id"] is not None,
            }
        )
    return sorted(out, key=lambda c: (not c["premier"], c["name"].casefold()))


def _touches_commander(deck: Deck, s: dict[str, Any], new_name: str | None = None) -> bool:
    """Whether changing category ``s`` (a ``category_state``) touches the commander: it is
    premier, it is named Commander (which the gateway treats as the commander zone by name), it
    holds a commander, or it is renamed to Commander."""
    names = {s["name"].casefold(), (new_name or "").casefold()}
    return bool(s["isPremier"]) or COMMANDER.casefold() in names or _holds_commander(deck, s["name"])


def _flag_value(s: dict[str, Any], key: str) -> bool:
    """A gateway flag (premier, not_in_deck, not_in_price) of a ``category_state``."""
    _key, _label, field, inverted = next(f for f in CATEGORY_FLAGS if f[0] == key)
    return (not s[field]) if inverted else bool(s[field])


CATEGORY_ACTIONS_ALL = ("change", "create", "delete")
CATEGORY_HAS_CARDS = (
    "“{name}” still has {n} card{s}. Archidekt only deletes an empty category: pick a category to "
    "move {them} to first."
)
CATEGORY_NO_RECORD = "“{name}” has no record on Archidekt yet, so there is nothing to delete."


def _category_name(i: int, raw: Any) -> str:
    new = clean_text(str(raw or ""))
    if not new or len(new) > MAX_CATEGORY:
        raise DeckError("invalid", f"change {i}: a category name is 1 to {MAX_CATEGORY} characters")
    if "," in new:  # a card's categories travel as one comma-separated list in exports
        raise DeckError("invalid", f"change {i}: a category name can't have a comma")
    return new


def parse_category_changes(raw: Any) -> list[dict[str, Any]]:
    """Validate a categories change: a list of entries, each with ``action`` (default "change"):

    - ``change``: ``{"category": its name now, or "id": its record id, "rename_to"?: new name,
      "premier"?, "not_in_deck"?, "not_in_price"?: true or false}``;
    - ``create``: ``{"action": "create", "category": the new name, and the three switches?}``
      (archidekt.com's Create Category);
    - ``delete``: ``{"action": "delete", "category" or "id", "move_cards_to"?: another category}``.
      Archidekt only deletes an empty category; ``move_cards_to`` moves its cards there first."""
    if not isinstance(raw, list) or not raw:
        raise DeckError("invalid", "changes must be a non-empty list of category changes")
    if len(raw) > MAX_CATEGORY_CHANGES:
        raise DeckError("invalid", f"at most {MAX_CATEGORY_CHANGES} categories can change at once")
    out = []
    flag_keys = [f[0] for f in CATEGORY_FLAGS]
    allowed = {
        "change": {"action", "category", "id", "rename_to", *flag_keys},
        "create": {"action", "category", *flag_keys},
        "delete": {"action", "category", "id", "move_cards_to"},
    }
    for i, item in enumerate(raw, 1):
        if not isinstance(item, dict):
            raise DeckError("invalid", f"change {i}: each change is an object")
        action = str(item.get("action") or "change").strip().lower()
        if action not in CATEGORY_ACTIONS_ALL:
            raise DeckError("invalid", f"change {i}: action must be change, create or delete")
        unknown = {k for k, v in item.items() if v is not None} - allowed[action]
        if unknown:
            raise DeckError("invalid", f"change {i}: unknown field {sorted(unknown)[0]} for {action}")
        cid = item.get("id")
        if cid is not None and (isinstance(cid, bool) or not isinstance(cid, int)):
            raise DeckError("invalid", f"change {i}: id must be a whole number")
        name = clean_text(str(item.get("category") or ""))
        entry: dict[str, Any] = {"action": action, "id": cid, "category": name}
        if action == "create":
            entry["category"] = _category_name(i, item.get("category"))
        elif cid is None and not name:
            raise DeckError("invalid", f"change {i}: name the category to {action}")
        if action == "change" and item.get("rename_to") is not None:
            entry["rename_to"] = _category_name(i, item["rename_to"])
        if action == "delete" and item.get("move_cards_to") is not None:
            entry["move_cards_to"] = _category_name(i, item["move_cards_to"])
        for key in flag_keys if action != "delete" else ():
            if key in item and item[key] is not None:
                if not isinstance(item[key], bool):
                    raise DeckError("invalid", f"change {i}: {key} must be true or false")
                entry[key] = item[key]
        out.append(entry)
    return out


def unused_category_changes(deck: Deck, limit: int | None = None) -> list[dict[str, Any]]:
    """archidekt.com's "Delete unused categories": a delete for every category with a record and
    no card filed under it (``deck_categories``' cards 0); the first ``limit`` of them when given
    (one proposal changes at most MAX_CATEGORY_CHANGES categories)."""
    return [
        {"action": "delete", "id": c["id"], "category": c["name"]}
        for c in deck_categories(deck)
        if c["id"] is not None and not c["cards"]
    ][:limit]


def _find_category(states: list[dict[str, Any]], w: dict[str, Any]) -> dict[str, Any] | None:
    if w.get("id") is not None:
        return next((x for x in states if x["id"] == w["id"]), None)
    return next((x for x in states if x["name"] == w["category"]), None) or next(
        (x for x in states if x["name"].casefold() == w["category"].casefold()), None
    )


def _flag_rows(row: dict[str, Any], was: dict[str, Any] | None, to: dict[str, Any]) -> None:
    """The ``<flag>_before``/``<flag>_after`` keys of a review row: each switch that changes, or
    for a new category (``was`` None) each switch that is on."""
    for key, _label, _field, _inv in CATEGORY_FLAGS:
        after = _flag_value(to, key)
        if was is None:
            if after:
                row[f"{key}_after"] = True
            continue
        before = _flag_value(was, key)
        if before != after:
            row[f"{key}_before"], row[f"{key}_after"] = before, after


def category_change_rows(
    deck: Deck, wanted: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(plan, review rows) of a categories change on ``deck``, refused here before anything is
    proposed or sent when it can't be done as Archidekt would. The plan's entries, in the order
    they are written:

    - ``{"op": "create", "id": None, "was": None | the record-less category, "to": the category}``:
      a new category (Create Category), or Archidekt's default Sideboard or Maybeboard, which has
      no record until someone creates one, getting its record with the switches asked for;
    - ``{"op": "update", "id", "was", "to"}``: a rename and/or switches, by record id;
    - ``{"op": "delete", "id", "was", "move_to"?, "moves": [{relation_id, name, from, to}]}``: a
      delete, after its cards (if any) are moved to ``move_to``. Archidekt itself only deletes an
      empty category, so a category with cards and no ``move_cards_to`` is refused.

    Refused too: a category the deck doesn't have, the same category twice, a new name the deck
    already has or another change gives (archidekt.com's own editor doesn't stop a rename onto
    one, and the server may merge the two), and moving cards to the category being deleted or to
    one this change deletes. A change that changes nothing is dropped."""
    states = category_states(deck)
    plan: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seen: set[Any] = set()
    final_names: dict[Any, str] = {}  # record id (or "new:<name>") -> its name after this change
    deleted: set[int] = set()
    for w in wanted:
        if w.get("action") == "create":
            continue
        s = _find_category(states, w)
        if s is None:
            raise DeckError("invalid", CATEGORY_GONE.format(name=w.get("category") or w.get("id")))
        key = s["id"] if s["id"] is not None else "name:" + s["name"].casefold()
        if key in seen:
            raise DeckError("invalid", f"“{s['name']}” is listed twice. Name each category once.")
        seen.add(key)
        if w.get("action") == "delete" and s["id"] is not None:
            deleted.add(s["id"])
    taken = {x["name"].casefold() for x in states}
    new_names: set[str] = set()

    def claim(name: str, own: dict[str, Any] | None) -> None:
        others = {x["name"].casefold() for x in states if own is None or x is not own}
        if name.casefold() in others or name.casefold() in new_names:
            raise DeckError("invalid", CATEGORY_TAKEN.format(name=name))
        new_names.add(name.casefold())

    creates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    updates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    deletes: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for w in wanted:
        action = w.get("action") or "change"
        if action == "create":
            name = w["category"]
            record_less = next(
                (x for x in states if x["id"] is None and x["name"].casefold() == name.casefold()), None
            )
            if record_less is not None and record_less["name"].casefold() not in new_names:
                # Archidekt's default Sideboard or Maybeboard: creating it is giving it its record,
                # with the switches a new category gets (each off unless asked for)
                flags = {key: False for key, _label, _field, _inv in CATEGORY_FLAGS}
                w = {**flags, **w, "action": "change", "category": record_less["name"]}
                updates.append((w, record_less))
                new_names.add(name.casefold())
                continue
            if name.casefold() in taken or name.casefold() in new_names:
                raise DeckError("invalid", CATEGORY_TAKEN.format(name=name))
            new_names.add(name.casefold())
            creates.append((w, {}))
            final_names["new:" + name.casefold()] = name
            continue
        s = _find_category(states, w)
        assert s is not None  # checked above
        if action == "delete":
            if s["id"] is None:
                raise DeckError("invalid", CATEGORY_NO_RECORD.format(name=s["name"]))
            deletes.append((w, s))
        else:
            if w.get("rename_to") and w["rename_to"] != s["name"]:
                claim(w["rename_to"], s)
                final_names[s["id"] if s["id"] is not None else "name:" + s["name"]] = w["rename_to"]
            updates.append((w, s))

    for w, _ in creates:
        to = {
            "id": None,
            "name": w["category"],
            "isPremier": False,
            "includedInDeck": True,
            "includedInPrice": True,
        }
        for key, _label, field, inverted in CATEGORY_FLAGS:
            if key in w:
                to[field] = (not w[key]) if inverted else w[key]
        plan.append({"op": "create", "id": None, "was": None, "to": to})
        row = _row(
            "deck_category",
            name=to["name"],
            create=True,
            commander=(to["isPremier"] or to["name"].casefold() == COMMANDER.casefold()) or None,
            counts=(to["name"].casefold() in SIZE_CATEGORY_NAMES) or None,
        )
        _flag_rows(row, None, to)
        rows.append(row)

    for w, s in updates:
        to = dict(s)
        new = w.get("rename_to")
        if new and new != s["name"]:
            to["name"] = new
        for key, _label, field, inverted in CATEGORY_FLAGS:
            if key in w:
                to[field] = (not w[key]) if inverted else w[key]
        record = s["id"] is None  # Archidekt's default Sideboard or Maybeboard: created with it
        if to == s:
            continue
        copies = sum(c.quantity for c in deck.cards if s["name"] in c.categories)
        names = {s["name"].casefold(), to["name"].casefold()}
        if record:
            # its record first, under the name its cards are filed under; a new name then goes by
            # the record id read back (``_apply_categories``)
            plan.append({"op": "create", "id": None, "was": s, "to": {**to, "name": s["name"]}})
            if to["name"] != s["name"]:
                plan.append({"op": "update", "id": None, "was": {**to, "name": s["name"]}, "to": to})
        else:
            plan.append({"op": "update", "id": s["id"], "was": s, "to": to})
        row = _row(
            "deck_category",
            id=s["id"],
            name=s["name"],
            rename_to=to["name"] if to["name"] != s["name"] else None,
            cards=copies,
            record=record or None,
            commander=_touches_commander(deck, s, to["name"]) or None,
            # renamed to or from a name Archidekt counts by name (Sideboard, Maybeboard)
            counts=(bool(names & SIZE_CATEGORY_NAMES) and to["name"] != s["name"]) or None,
        )
        _flag_rows(row, s, to)
        rows.append(row)

    by_name = {x["name"].casefold(): x for x in states}
    renamed = {p["was"]["name"]: p["to"]["name"] for p in plan if p["op"] == "update"}
    created = {p["to"]["name"].casefold(): p["to"] for p in plan if p["op"] == "create" and p["was"] is None}
    for w, s in deletes:
        filed = [c for c in deck.cards if s["name"] in c.categories]
        copies = sum(c.quantity for c in filed)
        move_to: str | None = None
        moves: list[dict[str, Any]] = []
        target_state: dict[str, Any] | None = None
        if w.get("move_cards_to"):
            want = w["move_cards_to"]
            target_state = by_name.get(want.casefold()) or created.get(want.casefold())
            if target_state is None:
                raise DeckError("invalid", CATEGORY_GONE.format(name=want))
            if target_state.get("id") is not None and target_state["id"] == s["id"]:
                raise DeckError(
                    "invalid", f"“{s['name']}” can't move its cards to itself. Pick another category."
                )
            if target_state.get("id") in deleted:
                raise DeckError(
                    "invalid",
                    f"“{target_state['name']}” is deleted by this change too, so “{s['name']}” can't move "
                    "its cards there. Pick a category that stays.",
                )
            tid = target_state.get("id")
            move_to = final_names.get(tid if tid is not None else "name:" + target_state["name"]) or (
                final_names.get("new:" + target_state["name"].casefold()) or target_state["name"]
            )
        if filed and move_to is None:
            n = copies
            raise DeckError(
                "invalid",
                CATEGORY_HAS_CARDS.format(
                    name=s["name"], n=n, s="s" if n != 1 else "", them="them" if n != 1 else "it"
                ),
                category=s["name"],
                cards=n,
            )
        # Archidekt's default boards decide what counts: deleting one is high even when empty
        counts = s["name"].casefold() in SIZE_CATEGORY_NAMES
        for c in filed:
            if c.relation_id is None:
                raise DeckError("contract", f"Archidekt did not number the deck rows of '{c.name}'.")
            new_cats: list[str] = []
            for x in c.categories:
                y = move_to if x == s["name"] else renamed.get(x, x)
                if y and y not in new_cats:
                    new_cats.append(y)
            moves.append(
                {"relation_id": c.relation_id, "name": c.name, "from": list(c.categories), "to": new_cats}
            )
            if c.categories and c.categories[0] == s["name"]:
                t_in = (target_state or {}).get("includedInDeck", True) is not False
                if (
                    t_in != s["includedInDeck"]
                    or {
                        s["name"].casefold(),
                        str(move_to).casefold(),
                    }
                    & SIZE_CATEGORY_NAMES
                ):
                    counts = True
        plan.append({"op": "delete", "id": s["id"], "was": s, "move_to": move_to, "moves": moves})
        rows.append(
            _row(
                "deck_category",
                id=s["id"],
                name=s["name"],
                delete=True,
                cards=copies,
                move_to=move_to,
                # cards moved into Commander or a Premier category become commanders (S-01)
                commander=_touches_commander(deck, s)
                or bool(
                    moves
                    and target_state
                    and (target_state.get("isPremier") or str(move_to).casefold() == COMMANDER.casefold())
                )
                or None,
                counts=counts or None,
            )
        )
    if not plan:
        raise DeckError("invalid", "The deck's categories are already this way; nothing would change.")
    return plan, rows


def category_line(r: dict[str, Any]) -> str:
    """A ``deck_category`` review row in words: the same line on the review page, the chat card,
    the Changes list, History and Activity."""
    name = r.get("name", "")
    flags = [
        f"{label} {'on' if r[f'{key}_after'] else 'off'}"
        for key, label, _field, _inv in CATEGORY_FLAGS
        if f"{key}_after" in r
    ]
    if r.get("delete"):
        n = int(r.get("cards") or 0)
        if n and r.get("move_to"):
            move = f": its {n} card{'s move' if n != 1 else ' moves'} to “{r['move_to']}” first"
        else:
            move = ""
        why = " (added since the snapshot)" if r.get("since") else ""
        return f"Delete category “{name}”{why}{move}. {OPEN_TABS_NOTE}"
    if r.get("create"):
        why = " (deleted since the snapshot)" if r.get("since") else ""
        return (
            f"New category “{name}”{why}" + (": " + ", ".join(flags) if flags else "") + f". {OPEN_TABS_NOTE}"
        )
    bits = []
    if r.get("rename_to"):
        n = int(r.get("cards") or 0)
        cards = f" (its {n} card{'s go' if n != 1 else ' goes'} with it)" if n else ""
        bits.append(f"rename to “{r['rename_to']}”{cards}")
    bits += flags
    return f"Category “{name}”: " + ", ".join(bits) + f". {OPEN_TABS_NOTE}"


def category_restore_plan(snapshot: Deck, current: Deck) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The category writes and review rows that put ``current``'s categories back to what
    ``snapshot`` recorded. Each write is a whole category: by record id for one still on the deck
    (its name and flags back), ``op`` "create" for one deleted since (made again with its name and
    flags, before the cards go back into it), and ``op`` "delete" for one added since, sent only
    once the cards are back and only if it is empty then (``after_cards``). A snapshot taken
    before category ids were kept puts none back. Refused when a name to put back is now another
    category's name."""
    now = {s["id"]: s for s in category_states(current) if s["id"] is not None}
    now_names = {s["name"].casefold(): s for s in category_states(current)}
    bodies: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    recorded: set[int] = set()
    snap_names: set[str] = set()
    for c in snapshot.categories:
        if not isinstance(c, dict) or not isinstance(c.get("name"), str):
            continue
        was = category_state(c)
        snap_names.add(was["name"].casefold())
        if was["id"] is None:
            continue
        recorded.add(was["id"])
        cur = now.get(was["id"])
        if cur is None:
            same = now_names.get(was["name"].casefold())
            if "includedInPrice" not in c:
                was["includedInPrice"] = was["includedInDeck"]
            if same is not None:
                # deleted since and made again (by a card filed under its name, or by hand): the
                # one there now gets the recorded switches back
                cur = same
                was = {**was, "id": same["id"], "name": same["name"]}
                if same["id"] is None:
                    body = {"op": "create", **{k: v for k, v in was.items() if k != "id"}, "id": None}
                    if {k: same[k] for k in same if k != "id"} == {k: was[k] for k in was if k != "id"}:
                        continue
                    bodies.append(body)
                    row = _row("deck_category", name=same["name"], record=True, cards=None)
                    _flag_rows(row, same, was)
                    rows.append(row)
                    continue
            else:
                bodies.append({"op": "create", **{k: v for k, v in was.items() if k != "id"}, "id": None})
                row = _row(
                    "deck_category",
                    name=was["name"],
                    create=True,
                    since=True,
                    commander=(was["isPremier"] or was["name"].casefold() == COMMANDER.casefold()) or None,
                )
                _flag_rows(row, None, was)
                rows.append(row)
                continue
        if "includedInPrice" not in c:  # recorded before Archidekt sent it: left as it is now
            was["includedInPrice"] = cur["includedInPrice"]
        if was == cur:
            continue
        if was["name"] != cur["name"] and any(
            x["id"] != cur["id"] and x["name"].casefold() == was["name"].casefold() for x in now.values()
        ):
            raise DeckError(
                "invalid",
                f"The category “{cur['name']}” can't get its name “{was['name']}” back: another "
                f"category on the deck has that name now. Rename or delete “{was['name']}” first.",
            )
        bodies.append(dict(was))
        row = _row(
            "deck_category",
            id=cur["id"],
            name=cur["name"],
            rename_to=was["name"] if was["name"] != cur["name"] else None,
            cards=sum(k.quantity for k in current.cards if cur["name"] in k.categories),
            commander=_touches_commander(current, cur, was["name"]) or None,
        )
        _flag_rows(row, cur, was)
        rows.append(row)
    if recorded:  # ids were kept, so a category the snapshot doesn't have was added since
        back = {b["name"].casefold() for b in bodies}
        # ... unless the snapshot's own cards are filed under it: the restore puts them back there
        filed = {x.casefold() for c in snapshot.cards for x in c.categories}
        for cur in category_states(current):
            if (
                cur["id"] is None
                or cur["id"] in recorded
                or cur["name"].casefold() in snap_names
                or cur["name"].casefold() in back
                or cur["name"].casefold() in filed
            ):
                continue
            bodies.append({"op": "delete", "id": cur["id"], "name": cur["name"], "after_cards": True})
            rows.append(
                _row(
                    "deck_category",
                    id=cur["id"],
                    name=cur["name"],
                    delete=True,
                    since=True,
                    commander=_touches_commander(current, cur) or None,
                )
            )
    return bodies, rows


def deck_with_categories(deck: Deck, bodies: list[dict[str, Any]]) -> Deck:
    """``deck`` as it reads once these category writes have landed, as the research expects them
    to: each category renamed in place, its cards following the new name, its flags as sent; a
    created one there (without a record id until Archidekt reads it back); a delete sent only
    after the cards (``after_cards``) not yet done. Used to work out what a restore still has to
    do to the cards; the apply then works that out again on the deck read back after the writes,
    so a live Archidekt that behaves otherwise is met there."""
    raw = json.loads(json.dumps(deck.raw))
    by_id = {b["id"]: b for b in bodies if b.get("op", "update") == "update"}
    renames: dict[str, str] = {}
    cats = raw.get("categories") or []
    for c in cats:
        b = by_id.get(c.get("id")) if isinstance(c, dict) else None
        if b is None:
            continue
        if c.get("name") != b["name"]:
            renames[str(c.get("name"))] = b["name"]
        c.update({k: b[k] for k in ("name", "isPremier", "includedInDeck", "includedInPrice")})
    for b in bodies:
        if b.get("op") == "create":
            hit = next((c for c in cats if isinstance(c, dict) and c.get("name") == b["name"]), None)
            if hit is None:
                hit = {"name": b["name"]}
                cats.append(hit)
            hit.update({k: b[k] for k in ("isPremier", "includedInDeck", "includedInPrice")})
    raw["categories"] = cats
    for row in raw.get("cards") or []:
        if isinstance(row, dict) and isinstance(row.get("categories"), list):
            row["categories"] = [renames.get(x, x) if isinstance(x, str) else x for x in row["categories"]]
    return parse_deck(raw)


def category_mismatches(before: Deck, plan: list[dict[str, Any]], after: Deck) -> list[str]:
    """What the deck read back after a categories apply doesn't show: a name or flag that is not
    as sent, a new category that is not there, a deleted one still there, and cards that did not
    follow a rename or a move (by deck row)."""
    now = {s["id"]: s for s in category_states(after) if s["id"] is not None}
    named = {s["name"]: s for s in category_states(after)}
    rows_after = {c.relation_id: c for c in after.cards if c.relation_id is not None}
    out: list[str] = []
    # a create that added a second category of a name (e.g. a record-less Maybeboard given its
    # record as a new one) is reported as such: only a live run can say what Archidekt does
    counts: dict[str, int] = {}
    first: dict[str, str] = {}
    for s in category_states(after):
        key = s["name"].casefold()
        counts[key] = counts.get(key, 0) + 1
        first.setdefault(key, s["name"])
    touched = {str((p.get("to") or p.get("was") or {}).get("name", "")).casefold() for p in plan}
    out += [f"“{first[k]}” (on the deck twice)" for k in sorted(touched) if counts.get(k, 0) > 1]
    # a record created only so it can be renamed is checked by its rename (by the final name)
    renamed_new = {
        p["was"]["name"]
        for p in plan
        if p.get("op", "update") == "update" and (p.get("id") is None or p.get("made"))
    }
    for p in plan:
        op = p.get("op", "update")
        if op == "create" and p["to"]["name"] in renamed_new:
            continue
        if op == "delete":
            if p["id"] in now:
                out.append(f"“{p['was']['name']}” (still there)")
            stuck = [
                m["name"]
                for m in p.get("moves") or []
                if m["relation_id"] in rows_after and rows_after[m["relation_id"]].categories != m["to"]
            ]
            if stuck:
                out.append(f"“{p['was']['name']}” (cards that did not move: {', '.join(sorted(set(stuck)))})")
            continue
        want = p["to"]
        got = named.get(want["name"]) if op == "create" or p.get("id") is None else now.get(p["id"])
        if got is None:
            out.append(f"“{want['name']}” (category not found)")
            continue
        wrong = [
            label for key, label, _f, _i in CATEGORY_FLAGS if _flag_value(got, key) != _flag_value(want, key)
        ]
        if got["name"] != want["name"]:
            wrong.insert(0, "name")
        old = (p.get("was") or {}).get("name", want["name"])
        if old != want["name"]:
            stuck = [
                c.name
                for c in before.cards
                if old in c.categories
                and c.relation_id in rows_after
                and want["name"] not in rows_after[c.relation_id].categories
            ]
            if stuck:
                wrong.append(f"cards that did not follow: {', '.join(sorted(set(stuck)))}")
        if wrong:
            out.append(f"“{want['name']}” ({'; '.join(wrong)})")
    return out


def details_payload(changes: dict[str, Any]) -> dict[str, Any]:
    """The PATCH /decks/{id}/update/ body for validated detail changes (Archidekt's field names)."""
    out: dict[str, Any] = {}
    for key, value in changes.items():
        if key == "deck_format":
            value = FORMAT_IDS[value]
        elif key == "game":
            value = GAME_IDS.get(value)  # "any" is Archidekt's null
        out[DETAIL_FIELDS[key]] = value
    return out


def _new_deck_printing_mismatches(
    verified: Deck, cards: list[dict[str, Any]], entries: list[dict[str, Any]]
) -> list[str]:
    """Names (with "printing or finish") whose rows on the re-read new deck do not carry the
    printing id and finish the import sent, every zone counted. Counts are compared per printing
    and finish, so a Foil row that came back Normal, or a pinned printing swapped for another, is
    reported instead of passing on name and quantity alone."""
    want: dict[tuple[int, str], int] = {}
    names: dict[tuple[int, str], str] = {}
    for c, e in zip(cards, entries, strict=True):
        key = (int(e["cardid"]), str(e["modifications"].get("modifier") or "Normal"))
        want[key] = want.get(key, 0) + int(e["modifications"]["quantity"])
        names.setdefault(key, str(c["name"]))
    got: dict[tuple[int, str], int] = {}
    for vc in verified.cards:
        if vc.card_id is None:
            continue
        key = (int(vc.card_id), vc.modifier or "Normal")
        got[key] = got.get(key, 0) + vc.quantity
    out: list[str] = []
    for key, qty in want.items():
        if got.get(key, 0) != qty:
            label = f"{names[key]} (printing or finish)"
            if label not in out:
                out.append(label)
    # The colour tags, counted per printing and tag the same way, so a tag Archidekt dropped (or
    # one it added) is reported instead of the deck passing on its cards alone.
    want_tags: dict[tuple[int, tuple[str, str]], int] = {}
    tag_names: dict[int, str] = {}
    for c, e in zip(cards, entries, strict=True):
        key = (int(e["cardid"]), _tag_key(c.get("label") or ""))
        want_tags[key] = want_tags.get(key, 0) + int(e["modifications"]["quantity"])
        tag_names.setdefault(int(e["cardid"]), str(c["name"]))
    got_tags: dict[tuple[int, tuple[str, str]], int] = {}
    for vc in verified.cards:
        if vc.card_id is not None and int(vc.card_id) in tag_names:
            key = (int(vc.card_id), _tag_key(vc.label))
            got_tags[key] = got_tags.get(key, 0) + vc.quantity
    for key in sorted(set(want_tags) | set(got_tags)):
        if want_tags.get(key, 0) != got_tags.get(key, 0):
            label = f"{tag_names[key[0]]} (label)"
            if label not in out:
                out.append(label)
    return out


def _tag_key(label: str) -> tuple[str, str]:
    """A colour tag as compared after an import: its name and colour, or nothing for no tag."""
    name, colour = split_label(label)
    return (name, colour) if name else ("", "")


def _printing_mismatches(verified: Deck, specs: list[dict[str, Any]]) -> list[str]:
    """Names whose rows on the re-read deck do not carry the finish or printing a spec asked for."""
    out: list[str] = []
    for spec in specs:
        pool = verified.side_cards if spec.get("zone") == "side" else verified.main_cards
        rows = [c for c in pool if c.name.lower() == spec["name"].lower()]
        switch = next((k for _f, k, _a, _w in SWITCHES.values() if k in spec), None)
        if switch is not None:
            pool = verified.side_cards if spec.get("zone") == "side" else verified.main_cards
            set_rows = [c for c in pool if c.name.lower() == spec["name"].lower()]
            if not set_rows or not all(bool(getattr(c, switch)) == spec[switch] for c in set_rows):
                out.append(spec["name"])
            continue
        if "mana_value" in spec:
            pool = verified.side_cards if spec.get("zone") == "side" else verified.main_cards
            set_rows = [c for c in pool if c.name.lower() == spec["name"].lower()]
            if not set_rows or not all(_same_mana_value(c.custom_cmc, spec["mana_value"]) for c in set_rows):
                out.append(spec["name"])
            continue
        if "label" in spec:
            pool = verified.side_cards if spec.get("zone") == "side" else verified.main_cards
            tagged = [c for c in pool if c.name.lower() == spec["name"].lower()]
            if not tagged or not all(_same_label(c.label, spec["label"]) for c in tagged):
                out.append(spec["name"])
            continue
        if spec.get("cardid") is not None:
            new_rows = [c for c in rows if c.card_id == spec["cardid"]]
            got: dict[str, int] = {}
            for c in new_rows:
                got[c.modifier or "Normal"] = got.get(c.modifier or "Normal", 0) + c.quantity
            want_cats = sorted(
                tuple(r["categories"]) for r in spec.get("rows", []) for _ in range(r["quantity"])
            )
            got_cats = sorted(tuple(c.categories) for c in new_rows for _ in range(c.quantity))
            ok = (
                sum(c.quantity for c in new_rows) == int(spec["quantity"])
                and got == spec.get("modifiers", {spec.get("modifier"): int(spec["quantity"])})
                and (not spec.get("rows") or want_cats == got_cats)
            )
        else:
            # a finish changes its rows in place: those rows (a change on one row leaves the rest)
            mine = [c for c in rows if c.relation_id in spec.get("relation_ids", [])] or rows
            ok = bool(mine) and all((c.modifier or "Normal") == spec["modifier"] for c in mine)
        if not ok:
            out.append(spec["name"])
    return out


def _count_of(value: Any) -> int:
    """A count a page sent back (a folder delete's shown counts), -1 when it isn't one."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _shown_folders(info: dict[str, Any]) -> list[dict[str, Any]]:
    """The member's folders below the top level that a deck can be put in: all but the
    gateway's backup folder and what is inside it.

    Frozen interface (0.7.29): the deck moves rely on this signature and behaviour; a change
    goes through the owner of the folder helpers."""
    return [f for f in info["folders"] if f["depth"] > 0 and not f.get("backup")]


def _no_such_folder(info: dict[str, Any], name: Any, *, several: bool = False) -> DeckError:
    """The refusal of a folder that is not one to use: none (or, with ``several``, more than one)
    of the shown folders has that name; the gateway's backup folder is refused with it too.

    Frozen interface (0.7.29): the deck moves rely on this signature and behaviour; a change
    goes through the owner of the folder helpers."""
    names = ", ".join(sorted({f["name"] for f in _shown_folders(info)})[:30])
    what = "More than one folder" if several else "No folder"
    return DeckError("invalid", f"{what} is called '{name}'. Your folders: {names}")


def _category_mismatches(verified: Deck, want: dict[int, list[str]]) -> list[str]:
    """Names of rows whose categories on the re-read deck are not what ``category_plan`` asked
    for (a row that vanished counts too). The first category is the row's primary one (Set as
    commander puts Commander there), so it must match too; the others in any order."""
    rows = {c.relation_id: c for c in verified.cards if c.relation_id is not None}
    out = set()
    for rel, cats in want.items():
        row = rows.get(rel)
        if (
            row is None
            or sorted(row.categories) != sorted(cats)
            or list(row.categories)[:1] != list(cats)[:1]
        ):
            out.add(row.name if row else f"deck row {rel}")
    return sorted(out)


def _same_account(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two link rows name the same Archidekt account (user id when both have one)."""
    if a.get("archidekt_user_id") and b.get("archidekt_user_id"):
        return str(a["archidekt_user_id"]) == str(b["archidekt_user_id"])
    return str(a.get("archidekt_username") or "").lower() == str(b.get("archidekt_username") or "").lower()


def owns_deck(deck: Deck, link: dict[str, Any]) -> bool | None:
    """Whether the linked account owns ``deck``: True when the deck's owner names it (the same
    rule as the deck list's, archidekt._owned_by: by id when both ids are known, as a username
    can change hands; by username only when an id is missing, and an empty username never
    matches), False when the owner can be read and is someone else, None when Archidekt's answer
    does not say who owns the deck. None is never "someone else's": a page shows the deck as it
    would any deck it cannot edit, and an edit is refused as unknown."""
    if not deck.owner and not deck.owner_id:
        return None
    return _owned_by(
        {"id": deck.owner_id, "username": deck.owner},
        str(link.get("archidekt_username") or ""),
        str(link.get("archidekt_user_id") or "") or None,
    )


def _sent(progress: dict[str, Any]) -> dict[str, Any]:
    return {k: progress[k] for k in ("sent_entries", "of_entries", "snapshot_id", "deck_id") if k in progress}


# Failures that mean Archidekt did not act on the write: refused before it was sent, or answered
# 403 or 429 (neither changes anything).
NOTHING_WRITTEN = frozenset(
    {"invalid", "forbidden", "not_found", "not_linked", "writes_disabled", "rate_limited", "stale"}
)


async def _writing(progress: dict[str, Any] | None, work: Awaitable[Any]) -> Any:
    """Await one write of an apply; ``progress["deck_written"]`` is set once the deck may have
    changed (the write succeeded, or failed in a way that may still have landed). A failure whose
    request never left the gateway (``sent`` False: the pacer's breaker or a running Retry-After
    pause refused it) changed nothing, whatever its kind. ``progress`` None (a hand
    action outside an apply) only awaits the write."""
    if progress is None:
        return await work
    try:
        out = await work
    except DeckError as exc:
        if exc.kind not in NOTHING_WRITTEN and exc.sent:
            progress["deck_written"] = True
        raise
    progress["deck_written"] = True
    return out


def _link_identity(link: dict[str, Any]) -> tuple[Any, ...]:
    """Which link a queued backup delete started with: when it was made and the
    Archidekt account it names. A token refresh changes none of these."""
    return (link.get("created_at"), link.get("archidekt_user_id"), link.get("archidekt_username"))


def _iso_time(value: str) -> float | None:
    """Seconds since the epoch for an ISO 8601 time as Archidekt writes it ("...Z" or another
    offset, with or without fractions), or None when it cannot be read. A time with no zone is
    unreadable: which clock it is on is not known."""
    try:
        when = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if when.tzinfo is None:
        return None
    return when.timestamp()


def backup_shared_note(made_at: int, *, recent: bool) -> str:
    """What the result says when an edit shares an earlier backup copy."""
    when = datetime.datetime.fromtimestamp(made_at, tz=datetime.UTC).strftime("%Y-%m-%d %H:%M UTC")
    why = "made less than 15 minutes before this edit" if recent else "the deck hasn't changed since then"
    return f"No new backup copy: this edit shares the one made {when} ({why})."


def _content_key(deck: Deck) -> str:
    """What a backup copy holds of a deck: its card rows and its details (not Archidekt's updated
    time, which a copy or any write bumps). Equal keys: the deck is unchanged."""
    details = {key: _current_detail(deck, key) for key in DETAIL_FIELDS}
    blob = json.dumps({"rows": deck.rows_fingerprint(), "details": details}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def _backup_as_made(copy: Deck, job: dict[str, Any]) -> bool:
    """Whether a queued backup copy is still as the gateway made it: in the backup
    folder it was made in, with the name and description it was given, and with Archidekt's
    updatedAt no later than the copy's own createdAt plus the time the gateway's writes to it
    took plus BACKUP_UNCHANGED_SLACK. Anything missing or unreadable counts as
    changed, so the copy is kept."""
    expect = job.get("expect")
    if not isinstance(expect, dict):
        return False
    updated = _iso_time(copy.updated_at)
    created = _iso_time(copy.created_at)
    within = expect.get("written_within")
    return (
        str(copy.parent_folder) == str(expect.get("folder_id"))
        and copy.name == expect.get("name")
        and same_rich_text(copy.description, str(expect.get("description") or ""))
        and updated is not None
        and created is not None
        and isinstance(within, int)
        and not isinstance(within, bool)
        and within >= 0
        and updated <= created + within + BACKUP_UNCHANGED_SLACK
    )


def undo_warning(later: int, outside: bool) -> str:
    """Flow G step 4: Undo warns once when it puts back more than that apply."""
    if later:
        word = "change" if later == 1 else "changes"
        return f"This also reverts {later} later {word} to this deck."
    if outside:
        return "This also reverts the edits made to this deck on Archidekt since."
    return ""


def _renames(bodies: list[dict[str, Any]], was: list[dict[str, Any]]) -> dict[str, str]:
    """Old name -> new name of the categories that ``bodies`` (category writes by record id)
    rename, against the category states ``was`` from before they were sent."""
    names = {s["id"]: s["name"] for s in was if s.get("id") is not None}
    out = {}
    for b in bodies:
        old = names.get(b.get("id"))
        if old is not None and old != b.get("name"):
            out[old] = str(b.get("name"))
    return out


def needs_look_error(reason: str | None) -> DeckError:
    """The refusal of an apply whose proposal no longer does what was reviewed on the deck as it
    is now. Nothing was sent, and ``_apply_slotted`` puts the proposal back to waiting as
    "needs a look" with ``reason`` (its stored form, recheck.py)."""
    from . import recheck

    exc = DeckError(
        "stale",
        f"Not applied: {recheck.reason_text(reason or recheck.REASON_CHANGED)} Nothing was sent to "
        "Archidekt. The change is "
        "still waiting, marked Needs a look: review it again, then apply, amend or reject it.",
        state="pending",
        status="needs_look",
    )
    exc.recheck_reason = reason or recheck.REASON_CHANGED  # type: ignore[attr-defined]
    return exc


def _changed_anything(progress: dict[str, Any]) -> bool:
    """Whether an apply may have changed anything on Archidekt, by its progress or by the
    proposal's stored result (which holds it): deck rows (or collection records) sent, a deck
    created, or any write that landed or may have. The one definition of "changed nothing" for
    the apply-stopped messages, the unused backup copy and putting a proposal back to pending.
    It fails closed: besides the writes the apply paths mark themselves (``_writing``), the
    client marks every write sent inside an apply (``archidekt.apply_writes``). A
    backup copy made in the backup folder is not a change to the deck."""
    return bool(progress.get("sent_entries") or progress.get("deck_id") or progress.get("deck_written"))


def _sentence(exc: Exception) -> str:
    """An error's message as a whole sentence, so the note after it starts a new one
    ("Archidekt had a problem answering. 1 of 3 ...", never "answering 1 of 3")."""
    text = str(exc).rstrip()
    return text if not text or text[-1] in ".!?" else text + "."


def _rate_limited_note(
    progress: dict[str, Any], *, pending: bool = False, collection: bool = False, clone: bool = False
) -> str:
    """What an apply that ran out of patience with Archidekt's 429s had changed, and what to do:
    added to PATIENCE_SPENT in the message the member sees. ``progress`` is the apply's
    progress, or the proposal's stored result (which holds it): get_proposal's next_step is built
    from the same note, so the two always agree. A backup copy whose delete was
    refused is said to stay in the backup folder, or to be deleted later.
    ``pending``: nothing changed, so the proposal is pending again. ``collection``: a
    collection proposal, whose target is "your collection". ``clone``: a copy of a deck, which
    has nothing left to try once the copy is made."""
    if clone and progress.get("deck_id"):
        return _partial_note(progress, clone=True)
    if progress.get("sent_entries") or progress.get("deck_id"):
        note = _partial_note(progress, collection=collection)
        return f"{note} Try the rest again later with a new proposal."
    if progress.get("deck_written"):
        where = "your collection" if collection else "the deck"
        return (
            f"Part of this change had already reached Archidekt, so check {where}. Try the rest again "
            "later with a new proposal."
        )
    kept = f" {progress['backup_note']}" if progress.get("backup_note") else ""
    if pending:
        return (
            f"Nothing was changed on Archidekt.{kept} The proposal is still pending, so it can be "
            "approved again later."
        )
    return f"Nothing was changed on Archidekt.{kept} Try again later with a new proposal."


def next_tag_position(positions: list[Any]) -> str:
    """The ``position`` for a tag added after the deck's others, worked out as archidekt.com's deck
    settings does it (its bundle, read 2026-10-14): "M-500000" for a deck's first tag, otherwise
    the last tag's "<letter>-<number>" with 10000 added, moving on to the next letter at 0 once
    the number would pass 999999. The last tag is the one with the highest position. After "Z"
    there is no next letter (the site's code read does not say what it does there), so a tag
    after "Z-990000" or later goes halfway to "Z-999999": it still sorts after every other tag,
    and never wraps to "Z-0", which would sort first."""
    parsed: list[tuple[str, int]] = []
    for p in positions:
        m = re.fullmatch(r"([0-9A-Za-z])-(\d{1,7})", str(p or ""))
        if m:
            parsed.append((m.group(1).upper(), int(m.group(2))))
    if not parsed:
        return "M-500000" if not positions else f"M-{500000 + 10000 * len(positions)}"
    letter, number = max(parsed)
    if number + 10000 < 1000000:
        return f"{letter}-{number + 10000}"
    if letter == "9":
        return "A-0"
    if letter == "Z":
        return f"Z-{min(999999, (number + 1000000) // 2)}"
    return f"{chr(ord(letter) + 1)}-0"


def _cover_is(deck: Deck, uid: str) -> bool:
    """Whether the deck's chosen cover is the art of the card with Scryfall id ``uid`` ("" = no
    chosen cover: Archidekt picks it)."""
    chosen = str(deck.raw.get("customFeatured") or "").lower()
    return uid in chosen if uid else not chosen


def _progress_view(progress: dict[str, Any]) -> dict[str, Any]:
    """What a running apply has done so far, for get_proposal and the review page."""
    keys = (
        "resolved_cards",
        "of_cards",
        "sent_entries",
        "of_entries",
        "snapshot_id",
        "deck_id",
        "deck_url",
        "waiting_for_archidekt",
    )
    return {k: progress[k] for k in keys if k in progress}


def _partial_note(progress: dict[str, Any], *, collection: bool = False, clone: bool = False) -> str:
    """What a failed apply had already changed on Archidekt, for the message the user sees.
    ``collection``: a collection proposal's apply, which counts collection records. ``clone``: a
    copy of a deck, made whole by one request, so once it exists it holds the cards."""
    if clone and progress.get("deck_id"):
        return COPY_MADE.format(deck_id=progress["deck_id"])
    sent = int(progress.get("sent_entries") or 0)
    if not sent and progress.get("deck_id"):
        return f"The new deck {progress['deck_id']} was created on Archidekt, but no cards were added."
    if collection:
        if not sent:
            return "No collection records were changed."
        return (
            f"{sent} of {progress.get('of_entries', '?')} collection changes had already been sent, "
            "so your collection is partly changed."
        )
    if not sent:
        return "No deck rows were changed."
    note = (
        f"{sent} of {progress.get('of_entries', '?')} deck row changes had already been sent, "
        "so the deck is partly changed."
    )
    if progress.get("snapshot_id"):
        note += (
            f" Snapshot {progress['snapshot_id']} holds the deck as it was before; "
            "propose_restore_snapshot puts it back."
        )
    elif progress.get("deck_id"):
        note += f" The new deck is {progress['deck_id']} on Archidekt."
    return note


def edit_mismatches(
    before: Deck, changes: list[Change], now: Deck, specs: list[dict[str, Any]] | None = None
) -> list[str]:
    """Names (with "printing or finish" where that is what differs) whose rows on ``now`` do not
    show ``changes`` applied to ``before``: the apply's verify, and the edit endpoint's test of
    whether a re-read has caught up with the write. Empty when the deck matches. ``specs`` are
    the printing specs the apply resolved (``_printing_entries`` fills in each one's modifier);
    without them the printing changes are not compared."""
    _before, after, _rows, _before_side, after_side = plan_zones(before, changes)
    recategorise, _lines = category_plan(before, changes)
    # The counts after the count changes, less what a category move takes out of the deck
    # proper (into the maybeboard) and plus what it brings in; the side counts the other way.
    expected = dict(after)
    expected_side = dict(after_side)
    for name, qty in leaving_deck(before, recategorise).items():
        expected[name] = expected.get(name, 0) - qty
        expected_side[name] = expected_side.get(name, 0) + qty
    for name, qty in entering_deck(before, recategorise).items():
        expected[name] = expected.get(name, 0) + qty
        expected_side[name] = expected_side.get(name, 0) - qty
    return sorted(
        set(_mismatches(now.counts_by_name(), {n: q for n, q in expected.items() if q > 0}))
        | set(_mismatches(now.side_counts_by_name(), {n: q for n, q in expected_side.items() if q > 0}))
        | set(_category_mismatches(now, recategorise))
        | set(_printing_mismatches(now, specs or []))
    )


def printing_not_shown(changes: list[Change], now: Deck) -> list[str]:
    """Names whose rows on ``now`` don't carry yet the printing (set and number) or finish a
    set_printing or set_finish change asked for: the edit endpoint's test of whether a re-read has
    caught up with a printing change, which ``edit_mismatches`` leaves out without the apply's
    specs. A finish a printing change moved on its own is not compared."""
    out: list[str] = []
    for ch in changes:
        if ch.action not in ("set_printing", "set_finish"):
            continue
        face = front_face(ch.card_name).casefold()
        rows = [c for c in now.cards_in(ch.zone) if front_face(c.name).casefold() == face]
        if not rows:
            out.append(ch.card_name)
            continue
        if ch.relation_id:
            # one row: a finish stays on that row; a printing makes a new row, so any row shows it
            same = [c for c in rows if c.relation_id == ch.relation_id]
            if ch.action == "set_finish":
                if not same or (same[0].modifier or "Normal") != ch.finish:
                    out.append(ch.card_name)
            elif not any(
                str(c.set_code or "").lower() == str(ch.set_code or "").lower()
                and str(c.collector_number or "") == str(ch.collector_number or "")
                for c in rows
            ):
                out.append(ch.card_name)
            continue
        for c in rows:
            if ch.action == "set_printing" and (
                str(c.set_code or "").lower() != str(ch.set_code or "").lower()
                or str(c.collector_number or "") != str(ch.collector_number or "")
            ):
                out.append(ch.card_name)
                break
            if ch.finish and (c.modifier or "Normal") != ch.finish:
                out.append(ch.card_name)
                break
    return sorted(set(out))


def _mismatches(got: dict[str, int], want: dict[str, int]) -> list[str]:
    """Names whose counts differ, compared case-insensitively (the assistant may type
    'arcane signet' while Archidekt returns the oracle spelling)."""

    def fold(counts: dict[str, int]) -> dict[str, tuple[str, int]]:
        out: dict[str, tuple[str, int]] = {}
        for name, qty in counts.items():  # entries differing only in case or back face are one card
            key = front_face(name).casefold()
            out[key] = (out.get(key, (name, 0))[0], out.get(key, (name, 0))[1] + qty)
        return out

    g, w = fold(got), fold(want)
    out = []
    for key in set(g) | set(w):
        if g.get(key, ("", 0))[1] != w.get(key, ("", 0))[1]:
            out.append((g.get(key) or w.get(key))[0])
    return sorted(out)


# Said by every tool that takes a deck id or a deck reference, so it names neither parameter.
NOT_A_DECK_ID = (
    "that is not an Archidekt deck: give the deck's number from its URL, or its archidekt.com link"
)


def _clean_deck_id(deck_id: Any) -> str:
    raw = str(deck_id).strip()
    if len(raw) > 2000:
        raise DeckError("invalid", NOT_A_DECK_ID)
    first = raw.split("/", 1)[0]
    if "://" in raw or "." in first or ":" in first:
        # Anything that names a host (with or without a scheme) must name Archidekt.
        try:
            host = urlparse(raw if "://" in raw else "https://" + raw).hostname or ""
        except ValueError:  # e.g. "http://[::1": an unterminated IPv6 host
            raise DeckError("invalid", "Only archidekt.com deck links are accepted.") from None
        if host.lower() not in ("archidekt.com", "www.archidekt.com"):
            raise DeckError("invalid", "Only archidekt.com deck links are accepted.")
    parts = [p for p in raw.split("/") if p]
    if "decks" in parts and parts.index("decks") + 1 < len(parts):
        raw = parts[parts.index("decks") + 1]
    elif parts:
        raw = parts[-1]
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    if not (raw.isascii() and raw.isdigit()) or len(raw) > 12:
        raise DeckError("invalid", NOT_A_DECK_ID)
    return raw


def clean_target(deck_id: Any) -> str:
    """A deck id, or "collection" for the member's collection proposals, which are filed under
    that name instead of a deck number."""
    if str(deck_id).strip().casefold() == DeckService.COLLECTION_TARGET:
        return DeckService.COLLECTION_TARGET
    if str(deck_id).strip().casefold() == "lists":  # account_lists.LISTS_TARGET
        return "lists"
    return _clean_deck_id(deck_id)


def _needs_press_message(press: str) -> str:
    return (
        f"This proposal {press}, so it needs the user's own press on the proposal card or the review "
        "page, in every approval mode. Nothing was sent to Archidekt; do not retry."
    )


def _needs_user_message(mode: str, why: str) -> str:
    if mode == "auto":
        return (
            f"This proposal {why}, so even in the user's automatic approval mode it needs their own "
            "press on the proposal card or the review page. Nothing was sent to Archidekt; do not retry."
        )
    if mode == "semi":
        return (
            f"This proposal is high risk (it {why}), so in the user's semi-automatic approval mode it "
            "needs their own press on the proposal card or the review page. Nothing was sent to "
            "Archidekt; do not retry."
        )
    return (
        "The user's approval mode asks them every time: this proposal needs their own press on the "
        "proposal card or the review page. Nothing was sent to Archidekt; do not retry."
    )


def formatting_review_only(row: dict[str, Any]) -> str:
    """Why a change that keeps formatting, or removes all of it, is approved on
    its review page only."""
    noun = "comment" if row.get("kind") == "action" else "description"
    if formatting.loses_formatting(row):
        return (
            f"It removes all of the {noun}'s formatting (such as bold text, links and pictures), which "
            "only the review page shows, so the user approves it there (review_url), not on a card in "
            "the chat."
        )
    return (
        f"It keeps the formatting already in the {noun} (such as bold text, links or pictures), which "
        "only the review page shows, so the user approves it there (review_url), not on a card in the "
        "chat."
    )


def applied_text(
    kind: str, changes: dict[str, Any] | None, result: dict[str, Any] | None, *, backup_gone: bool = False
) -> str:
    """What an applied account action or collection change did, in one sentence for the member
    (the pages put "Done." or "Applied." before it). Empty for a deck change, whose pages keep
    saying the deck matches the proposal. ``backup_gone``: a deck deletion's backup copy is no
    longer on Archidekt (DeckService.backup_gone), so the text doesn't say it is there."""
    c = changes if isinstance(changes, dict) else {}
    r = result if isinstance(result, dict) else {}
    if kind == "collection":
        # a collection change is stored as applied even when its read-back finds mismatches; one
        # applied before the read-back arrived has no "verified" and keeps the plain text
        if r.get("verified") is False:
            return (
                "Sent to Archidekt, but the read-back didn't confirm every change. See Result on the "
                "review page."
            )
        return "Your collection on Archidekt has these changes now."
    if kind == "lists":
        from .account_lists import applied_text as lists_text

        return lists_text(c, r)
    if kind != "action":
        return ""
    action = c.get("action")
    on = c.get("on") is not False
    vote = c.get("vote")
    if action == "create_folder":
        return "The folder is created on Archidekt."
    if action == "deck_vote":
        if vote == VOTE_NONE:
            return "Your vote on the deck is taken back on Archidekt."
        return (
            "The deck is voted down on Archidekt." if vote == VOTE_DOWN else "The deck is liked on Archidekt."
        )
    if action == "deck_bookmark":
        return "The deck is bookmarked on Archidekt." if on else "Your bookmark is removed on Archidekt."
    if action == "follow":
        return "You follow them on Archidekt now." if on else "You no longer follow them on Archidekt."
    if action == "comment_post":
        return (
            "Your reply is posted on Archidekt."
            if c.get("parent")
            else "Your comment is posted on Archidekt."
        )
    if action == "comment_edit":
        return "Your comment is changed on Archidekt."
    if action == "comment_delete":
        return "Your comment is deleted on Archidekt."
    if action == "comment_vote":
        if vote == VOTE_NONE:
            return "Your vote on the comment is taken back on Archidekt."
        return "Your vote on the comment is saved on Archidekt."
    if action == "delete_deck":
        # delete_deck keeps a snapshot first, and a backup copy when backups are on
        if r.get("backup_deck_id") and not backup_gone:
            return (
                "The deck is deleted on Archidekt. A snapshot was kept, and a backup copy was made in "
                "your backup folder."
            )
        return "The deck is deleted on Archidekt. A snapshot of it was kept."
    if action == "collaborator_add":
        return "They can edit your deck on Archidekt now."
    if action == "collaborator_remove":
        return "They can no longer edit your deck on Archidekt."
    if action == "deck_help":
        if r.get("verified") is False:
            return "Sent to Archidekt, but reading the deck back didn't show the request as sent."
        return {
            "delist": "Your help request is taken down on Archidekt.",
            "edit": "Your help request is changed on Archidekt.",
        }.get(str(c.get("help_action")), "Your help request is listed on Archidekt for 7 days.")
    if action == "report":
        return "Your report is sent to Archidekt's moderators."
    return ""


def _next_step(
    state: str,
    writes_enabled: bool,
    *,
    may_apply: bool = False,
    mode: str = "manual",
    risk: str = "high",
    risk_reason: str = "",
    in_chat: bool = False,
    partial: bool = False,
    kind: str = "edit",
    closed_by: str | None = None,
    closed_by_app: str | None = None,
    rate_limited: str = "",
    card_may_approve: bool = True,
    press_reason: str = "",
    policy_allows: bool = False,
    opens_first: str = "",
    applied: str = "",
) -> str:
    """What the assistant should do next with a proposal in ``state``. ``rate_limited``: for an
    apply that stopped on Archidekt's waits, the same note its stored message ends with
    (``_rate_limited_note``), so the two never disagree. ``applied``: ``applied_text`` for an
    account action or collection change."""
    what = (
        "these details"
        if kind == "details"
        else "these category changes"
        if kind == "categories"
        else "these custom card changes"
        if kind == "custom_cards"
        else "this copy"
        if kind == "clone"
        else "this diff"
    )
    # With the in-chat card (approve.py) the person decides on the card where the app shows one.
    card = (
        "If your app shows this proposal as a card with Approve and Reject buttons, the user decides "
        "there and the gateway tells you the outcome; do not apply it yourself. Otherwise, show"
        if in_chat
        else "Show"
    )
    if state == "pending" and not writes_enabled:
        return "Review only: Archidekt writes are switched off on this gateway, so this can't be applied yet."
    if state == "pending" and rate_limited:
        # The last apply stopped on Archidekt's waits before it changed anything. No "call
        # apply_proposal now", even when the member's mode lets the assistant apply: not yet.
        return rate_limited
    if state == "pending" and may_apply:
        chose = (
            "to apply changes without asking"
            if mode == "auto"
            else f"to apply low-risk changes without asking, and this one is low risk ({risk_reason})"
        )
        return (
            f"The user chose {chose}: tell them {what} in one line and call apply_proposal now. "
            "They can also press Apply on the review page."
        )
    if state == "pending" and opens_first:  # a change that keeps formatting
        return f"Show the user {what} and the review link. {opens_first} Do not call apply_proposal."
    if state == "pending" and risk in modes.MEMBER_ONLY:
        if not card_may_approve:  # a deck deletion: its review page only
            return (
                f"Show the user {what} and the review link: deleting a deck is approved only on its "
                "review page, in every approval mode (the in-chat card only opens that page). Do "
                "not call apply_proposal."
            )
        if kind == "custom_cards":  # the member-only tier, as no snapshot puts custom cards back
            return (
                f"Show the user {what} and the review link: it takes custom cards out of the deck, "
                "which a snapshot can't put back, so they approve it themselves, in every approval "
                "mode, on the proposal card in the chat (where your app shows it) or on the review "
                "page. Do not call apply_proposal."
            )
        return (
            f"Show the user {what} and the review link: this acts on their Archidekt account, so "
            "they approve it themselves, in every approval mode, on the proposal card in the chat "
            "(where your app shows it) or on the review page. Do not call apply_proposal."
        )
    if state == "pending" and press_reason:
        return (
            f"{card} the user {what} and the review link: it {press_reason}, so it waits for the "
            "user's own press in every approval mode (they confirm on the card or the review page). "
            "Do not call apply_proposal."
        )
    if state == "pending" and mode == "auto" and not policy_allows and risk_reason:
        return (
            f"The user's Full auto mode still waits for their own press on this one, because it "
            f"{risk_reason}. {card} the user {what} and the review link. Do not call apply_proposal."
        )
    if state == "pending" and mode == "semi" and not policy_allows:
        return (
            f"This proposal is high risk (it {risk_reason}), so the user's semi-automatic mode needs "
            f"their own press. {card} the user {what} and the review link: they confirm on the "
            "card or the review page. Do not call apply_proposal."
        )
    if state == "pending":
        return (
            f"{card} the user {what} and the review link: they confirm on the review page (Apply). "
            "Do not call apply_proposal"
            + (": the user's approval mode asks them every time." if mode == "manual" else ".")
        )
    if state == "applying":
        return (
            "Archidekt is still being updated (progress shows how far it got; a large change takes a "
            "few minutes because the gateway paces its requests). Tell the user it is under way and "
            "check again with proposal_status in a minute. Do not apply it again or make a new proposal."
        )
    if state == "applied" and applied:
        return f"Done. {applied}"
    if state == "applied" and kind == "details":
        return "Done. The deck's details on Archidekt match this proposal."
    if state == "applied" and kind == "categories":
        return "Done. The deck's categories on Archidekt match this proposal."
    if state == "applied" and kind == "custom_cards":
        return "Done. The deck's custom cards on Archidekt match this proposal."
    if state == "applied" and kind == "clone":
        return "Done. The copy exists on Archidekt (see result.deck_url) and matches the source deck."
    if state == "applied":
        return "Done. The deck on Archidekt matches this proposal."
    if state == "failed" and rate_limited:
        return rate_limited
    if state == "failed" and partial:
        return (
            "Part of this change reached Archidekt before it failed (see result.sent_entries). Check "
            "the deck; result.snapshot_id can be restored with propose_restore_snapshot."
        )
    if state == "failed":
        return "Nothing further was changed. Create a new proposal if you still want these edits."
    if state == "rejected":
        app = closed_by_app or "a connected app"
        who = {
            "assistant": (
                f"Rejected through {app} (the assistant's reject_proposal), not by the user's own press."
            ),
            "card": "Rejected by the user on the proposal card.",
            "review_page": "Rejected by the user on the review page.",
            "browser": "Rejected by the user in the gateway's web pages.",
            "api": f"Rejected through {app} (the gateway's API).",
            "disconnected": "Rejected when the user disconnected the app that made it.",
        }.get(closed_by or "", "Rejected.")
        return f"{who} Nothing was changed; create a new proposal for different edits."
    return "This proposal is no longer actionable."


def cards_from_json(text: str) -> list[dict[str, Any]]:
    """Rows from the gateway's own deck JSON (what get_deck returns and the Export page's .json
    download holds): an object with ``cards`` (or a bare list), each ``{name, quantity, categories,
    set, collector_number, finish, in_deck}``. Only those fields are read; the rest of a deck read
    (stats, prices, text) is ignored. Raises DeckError when the text is not that shape."""
    if len(text) > 4_000_000:
        raise DeckError("invalid", "deck JSON larger than 4 MB")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise DeckError("invalid", f"deck JSON could not be read: {exc}") from exc
    rows = data.get("cards") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise DeckError(
            "invalid", "deck JSON must be an object with a cards list (the gateway's .json export)"
        )
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict):
            raise DeckError("invalid", f"card {i} in the deck JSON must be an object")
        qty = row.get("quantity", 1)
        if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1 or qty > 99:
            raise DeckError("invalid", f"card {i}: quantity must be a whole number from 1 to 99")
        cats_raw = row.get("categories") or []
        if not isinstance(cats_raw, list) or not all(isinstance(c, str) for c in cats_raw):
            raise DeckError("invalid", f"card {i}: categories must be a list of names")
        name_len = len(str(row.get("name") or ""))
        if name_len > 200 or len(cats_raw) > 10 or any(len(c) > MAX_CATEGORY for c in cats_raw):
            raise DeckError(
                "invalid", f"card {i}: name (200), categories (10 of up to {MAX_CATEGORY}) too long"
            )
        finish = str(row.get("finish") or "").strip().capitalize()
        set_code = str(row.get("set") or row.get("set_code") or "").strip().lower()
        number = str(row.get("collector_number") or "").strip()
        if (set_code and not _SET_CODE.fullmatch(set_code)) or (
            number and not _COLLECTOR_NUMBER.fullmatch(number)
        ):
            raise DeckError("invalid", f"card {i}: set or collector_number looks wrong")
        out.append(
            {
                "name": str(row.get("name") or ""),
                "quantity": qty,
                "categories": [str(c) for c in cats_raw],
                "foil": finish in ("Foil", "Etched"),
                "finish": finish if finish in ("Foil", "Etched") else "",
                "set_code": set_code,
                "collector_number": number,
                "label": import_label(row.get("label")),
                # get_deck's rows say in_deck: false for a maybeboard or sideboard row
                **({"in_deck": False} if row.get("in_deck") is False else {}),
            }
        )
    return out


SIDE_CATEGORIES = ("Maybeboard", "Sideboard")


def import_label(raw: Any) -> str:
    """A list's colour tag ("Have,#37d67a", or a bare "Have") in the form Archidekt stores, or ""
    for none. A bare name gets the default grey, as set_label gives it; a tag with no name (the
    empty one Archidekt exports for an untagged row) is none. A colour that is not #rrggbb is
    left grey rather than refusing the whole list."""
    name, colour = split_label(clean_text(str(raw or "")))
    if not name:
        return ""
    return f"{name[:MAX_LABEL]},{colour or DEFAULT_LABEL_COLOUR}"


_card_seq = card_seq_of  # the newest card's number for this proposal in an app's chat


def normalise_cards(
    *,
    cards: Any = None,
    decklist_text: str | None = None,
    csv_text: str | None = None,
    json_text: str | None = None,
) -> list[dict[str, Any]]:
    """Turn any of the accepted inputs into [{name, quantity, categories, foil, finish, ...}].
    Sideboard and maybeboard rows are kept under their category (Archidekt stores them as
    categories outside the deck), so a pasted list, CSV or the gateway's JSON round-trips whole.
    Such a row also gets ``in_deck: False`` (and ``no_price: True`` for Archidekt's {noPrice}),
    so the proposal counts it apart from the deck proper."""
    for label, value in (("decklist_text", decklist_text), ("csv_text", csv_text), ("json_text", json_text)):
        if value is not None and not isinstance(value, str):
            raise DeckError("invalid", f"{label} must be a string")
    given = [x is not None and x != "" and x != [] for x in (cards, decklist_text, csv_text, json_text)]
    if cards == [] and not any(given):
        return []  # an empty deck: the New deck box's Empty tab, as archidekt.com's New deck makes one
    if sum(given) != 1:
        raise DeckError("invalid", "give exactly one of cards, decklist_text, csv_text or json_text")
    if csv_text and len(csv_text) > 2_000_000:
        raise DeckError("invalid", "CSV export larger than 2 MB")
    if decklist_text and len(decklist_text) > 200_000:
        raise DeckError("invalid", "decklist larger than 200 kB")
    out: list[dict[str, Any]] = []
    if json_text:
        out = cards_from_json(json_text)
    elif csv_text:
        try:
            for c in parse_export(csv_text):
                out.append(
                    {
                        "name": c.name,
                        "quantity": c.quantity,
                        "categories": c.categories,
                        "foil": c.finish in ("Foil", "Etched"),
                        "finish": c.finish if c.finish in ("Foil", "Etched") else "",
                        "set_code": c.set_code,
                        "collector_number": c.collector_number,
                        "label": import_label(c.label),
                        # the same rule as parse_deck_export: these two categories sit outside the deck
                        **({"in_deck": False} if c.category in ("Maybeboard", "Sideboard") else {}),
                    }
                )
        except CsvError as exc:
            raise DeckError("invalid", f"CSV export could not be read: {exc}") from exc
    elif decklist_text:
        try:
            for c in parse_decklist(decklist_text):
                out.append(
                    {
                        "name": c.name,
                        "quantity": c.quantity,
                        # a sideboard row without a category of its own goes to Archidekt's
                        # Sideboard or Maybeboard category, as its section header said
                        "categories": c.categories or ([c.board] if c.zone == "side" and c.board else []),
                        "foil": c.foil,
                        "finish": c.finish,
                        "set_code": c.set_code,
                        "collector_number": c.collector_number,
                        "label": import_label(c.label),
                        **({"in_deck": False} if c.zone == "side" else {}),
                        **({"no_price": True} if c.no_price else {}),
                    }
                )
        except DecklistError as exc:
            raise DeckError("invalid", f"decklist could not be read: {exc}") from exc
    else:
        if not isinstance(cards, list):
            raise DeckError("invalid", "cards must be a list of {card_name, quantity, category?}")
        for i, item in enumerate(cards):
            if not isinstance(item, dict):
                raise DeckError("invalid", f"card {i} must be an object")
            name = clean_text(item.get("card_name") or item.get("name") or "")
            qty = item.get("quantity", 1)
            if not name or not isinstance(qty, int) or isinstance(qty, bool) or qty < 1 or qty > 99:
                raise DeckError("invalid", f"card {i}: card_name and a quantity from 1 to 99 are required")
            cat = clean_text(item.get("category") or "")
            if len(name) > 200 or len(cat) > MAX_CATEGORY:
                raise DeckError("invalid", f"card {i}: card_name (200) or category (60) is too long")
            set_code = str(item.get("set_code") or item.get("set") or "").strip().lower()
            number = str(item.get("collector_number") or "").strip()
            if (set_code and not _SET_CODE.fullmatch(set_code)) or (
                number and not _COLLECTOR_NUMBER.fullmatch(number)
            ):
                raise DeckError("invalid", f"card {i}: set_code or collector_number looks wrong")
            finish_raw = str(item.get("finish") or "").strip().lower()
            if finish_raw and finish_raw not in FINISHES:
                raise DeckError("invalid", f"card {i}: finish must be normal, foil or etched")
            finish = FINISHES.get(finish_raw, "")
            out.append(
                {
                    "name": name,
                    "quantity": qty,
                    "categories": [cat] if cat else [],
                    "foil": item.get("foil") is True or finish in ("Foil", "Etched"),
                    "finish": finish if finish != "Normal" else "",
                    "set_code": set_code,
                    "collector_number": number,
                }
            )
    for c in out:  # names and categories from a CSV cell can hold newlines; each is one diff line
        c["name"] = clean_text(c["name"])
        c["categories"] = [cat for cat in (clean_text(x) for x in c["categories"]) if cat]
        if not c["name"]:
            raise DeckError("invalid", "every card needs a name")
    if not out:
        raise DeckError("invalid", "no cards to add")
    if sum(c["quantity"] for c in out) > 400 or len(out) > 300:
        raise DeckError(
            "invalid", "that is more cards than a proposal may create at once (300 rows, 400 cards)"
        )
    return out


def mark_gone(snapshots: list[dict[str, Any]], listed: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Reconcile History's snapshots with the member's Archidekt deck list (``on_archidekt``):
    a snapshot taken before the list was read whose deck is not in it gets ``deck_gone`` (Restore
    cannot work: it rewrites that deck), one whose Archidekt backup copy is not in it gets
    ``backup_gone`` (deleted on Archidekt; Restore still works, it uses the snapshot kept here).
    Without a list that can tell, nothing is marked. The list is of the account linked now, so a
    snapshot of another account's deck (one linked before) is never marked: its deck and copy
    are not in this list whether they still exist or not."""
    if not listed:
        return snapshots
    ids, read_at = listed["ids"], float(listed["read_at"])
    account = listed.get("account_id")
    for x in snapshots:
        if float(x.get("taken_at") or 0) >= read_at:
            continue  # newer than the list: it cannot know
        if account is not None and x.get("account_id") != account:
            continue  # another account's deck (or one whose owner was not recorded)
        if str(x.get("deck_id") or "") not in ids:
            x["deck_gone"] = True
        if x.get("backup_deck_id") and str(x["backup_deck_id"]) not in ids:
            x["backup_gone"] = True
    return snapshots
