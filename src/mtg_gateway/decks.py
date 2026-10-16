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
import contextvars
import dataclasses
import datetime
import json
import logging
import math
import pickle
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

from . import modes
from .approve import approval_code, approval_matches
from .archidekt import (
    FORMAT_IDS,
    MAX_LIST_PAGES,
    PATIENCE_SPENT,
    RATE_LIMIT_WAITS,
    ArchidektClient,
    ArchidektError,
    Deck,
    DeckList,
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
    no_retry,
    parse_deck,
    patient,
    plain_text,
    rich_text,
    same_rich_text,
    wait_note,
    written_text,
)
from .archidekt_csv import CsvError, parse_export
from .config import Settings
from .db import Database
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
)
COUNT_ACTIONS = ("add", "remove", "set_quantity")
CATEGORY_ACTIONS = ("set_category", "set_commander")
# Changes to a card already in the deck, row by row: its finish, the printing itself, its
# colour tag (Archidekt's "label", stored on a row as "Name,#rrggbb") or its custom mana value
# (Archidekt's "customCmc", the deck's own override of the card's mana value).
PRINTING_ACTIONS = ("set_finish", "set_printing", "set_label", "set_mana_value")
# Row-by-row changes that also work on the maybeboard and sideboard rows (zone "side").
ROW_TAG_ACTIONS = ("set_label", "set_mana_value")
MAX_MANA_VALUE = 15  # the range of archidekt.com's own Mana value menu (Q57)
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
}
MAX_DESCRIPTION = 20_000
# Deck organisation propose_deck_details may also change, resolved against the member's account
# when the proposal is made: the folder the deck sits in, its tags and its cover card.
ORGANISE_FIELDS = ("folder", "add_tags", "remove_tags", "cover")
# The OAuth client (or "__browser__") acting on the current request, recorded in the audit log.
# Set by the MCP tool layer and the browser pages; None when the actor is not known.
current_client: ContextVar[str | None] = ContextVar("current_client", default=None)
MAX_CHANGES = 40
# How long an apply call waits for the apply to finish before it answers "applying". The apply
# itself carries on in the background (a 100-card new deck is a few hundred paced requests, longer
# than an assistant app waits for one tool call); get_proposal and the review page show its
# progress until it ends.
APPLY_WAIT_SECONDS = 20.0
# At shutdown (a redeploy), how long a running apply may go on before it is cut off and recorded
# as interrupted: long enough for a paced 100-card apply. It runs after the server's own graceful
# wait for requests (GRACEFUL_SHUTDOWN_SECONDS in __main__), and the two together stay under the
# stack's stop_grace_period, after which Docker kills the container with nothing recorded.
SHUTDOWN_GRACE_SECONDS = 90.0
# The unused backup copy of a failed apply that changed nothing (Q77). When Archidekt refuses its
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
# A copy of a deck (clone) that failed or stopped after Archidekt made the copy (review D1): the
# copy holds the cards, so the new-deck sentence ("no cards were added") would be wrong.
COPY_MADE = (
    "The copy {deck_id} was made on Archidekt, but the gateway could not check it. Look at it on "
    "Archidekt before asking for another copy."
)
BACKUP_DELETE_MARGIN = 30
BACKUP_DELETE_BACKOFF = 600
BACKUP_DELETE_TRIES = 4
BACKUP_DELETE_EVERY = 60.0  # seconds between the loop's passes
# Review D4: before a later delete, the copy is read once and deleted only while it is as the
# gateway made it: in the backup folder, with the name and description it was given, and with
# Archidekt's updatedAt no later than its own createdAt plus the time the gateway's own writes to
# the copy took (measured as it made the copy, rounded up to whole seconds) plus this many seconds.
# One clock, Archidekt's (review L2). The slack covers a time that Archidekt might keep to whole
# seconds only (up to 1 s off each way); everything the gateway writes is in the measured part.
BACKUP_UNCHANGED_SLACK = 2
BACKUPS_REMEMBERED = 256  # backup copies made recently whose name and description are kept
BROWSER_CLIENT = "__browser__"  # the same value as pages.BROWSER_CLIENT_ID
# An administrator acting on a member's account from the admin pages (unlink): recorded in the
# member's own activity log as done by an administrator, never as the member's browser.
ADMIN_CLIENT = "__admin__"
# Pending proposals one member may hold at once, and one app (or the browser) may hold for that
# member; another is refused until some are applied, rejected or expire. The per-app cap keeps
# one connected app from filling every slot and blocking the member's other apps. Closed ones
# (expired, rejected, failed) beyond the newest MAX_CLOSED_PROPOSALS per member are deleted, so
# proposals cannot fill the disk.
MAX_PENDING_PROPOSALS = 100
MAX_PENDING_PER_CLIENT = 30
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
# read, a proposal, an apply); more is refused at once (rate_limited), so one account cannot
# queue the pacer up for every other member.
MAX_ARCHIDEKT_PER_USER = 3
# The member whose Archidekt slot the running task already holds, so nested calls (an apply's
# reads and writes, get_any_deck's private retry) do not take a second one.
_slot_holder: ContextVar[str | None] = ContextVar("_slot_holder", default=None)
# The member whose Archidekt session the current ``_call_unslotted`` runs with: how a write
# the client is about to send is tied back to the member whose caches it makes stale.
_acting_sub: ContextVar[str | None] = ContextVar("_acting_sub", default=None)
# A member's deck list is kept in memory for this long after Archidekt answered (fresh), and
# served for up to DECK_LIST_STALE seconds more while one refresh runs in the background.
DECK_LIST_FRESH = 90.0
DECK_LIST_STALE = 15 * 60.0
# A list with entries Archidekt left unreadable (Q64) is kept only this long, and never served
# stale: My decks and Home say so with Try again, which reads it again at once (review N10).
DECK_LIST_PARTIAL = 60.0
# How long a page waits for a cold deck list before it renders with a placeholder the browser
# fills from /api/decks/mine (the fetch carries on and lands in the cache either way).
DECK_LIST_COLD_WAIT = 1.5
# A deck page shows the deck as read for its last view for this long (DeckService.page_deck):
# long enough for a run of view switches and a Back, short enough that a change made on
# archidekt.com shows on the next view soon after. MTG_ARCHIDEKT_CACHE_SECONDS=0 turns it off.
PAGE_DECK_SECONDS = 20.0
PAGE_DECKS_MAX = 200  # reads kept at most, the oldest go first
TOUCH_LINK_INTERVAL = 60.0  # the link's last_used_at is written at most this often per member
# Said first in a pending proposal's next_step once the deck sync (sync.py) has seen its deck change.
OUT_OF_DATE_STEP = (
    "Out of date: the deck changed after this proposal was made, so applying it will be refused "
    "and nothing will be sent. Reject it and propose again from the current deck. "
)
# Archidekt work one member may start per ARCHIDEKT_BUDGET_WINDOW seconds (each deck read,
# proposal, apply or proxied archidekt_* research call counts once), on top of the concurrency
# cap above: a looping assistant cannot keep a steady stream of requests going on the member's
# Archidekt account and the shared pacer. MTG_ARCHIDEKT_CALLS_PER_10_MIN sets it.
ARCHIDEKT_BUDGET_WINDOW = 600


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

    def take(self, key: str) -> bool:
        """Spend one call for ``key``; False (and nothing spent) when its bucket is empty."""
        now = time.monotonic()
        tokens, at = self._buckets.get(key, (float(self.per_window), now))
        tokens = min(float(self.per_window), tokens + (now - at) * self.per_window / self.window)
        if tokens < 1:
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

    def clear(self) -> None:
        for sub in list(self._entries):
            self.drop(sub)

    async def refreshed(self, sub: str, fetch: Callable[[], Awaitable[Any]], *, wait: float) -> Any | None:
        """A value fetched now (sharing a fetch already in flight), for the deck sync: unlike
        ``get`` it never answers a failed fetch with an older value, so the error reaches the
        caller. None when the fetch takes longer than ``wait`` (it carries on and fills the cache)."""
        task = self._refresh(sub, fetch)
        done, _pending = await asyncio.wait({task}, timeout=wait)
        return task.result() if done else None

    def refreshing(self, sub: str) -> bool:
        return sub in self._refreshing

    def _refresh(self, sub: str, fetch: Callable[[], Awaitable[Any]]) -> asyncio.Task[Any]:
        running = self._refreshing.get(sub)
        started = time.monotonic()
        # A fetch that started before the last drop is not shared with a reader arriving after it:
        # it may carry the pre-write list. That fetch still finishes (and stores nothing).
        if running is not None and self._dropped.get(sub, 0.0) <= running[1]:
            return running[0]

        async def run() -> Any:
            value = await fetch()
            if self._dropped.get(sub, 0.0) <= started:  # not invalidated while it was fetched
                self._entries[sub] = (time.monotonic(), time.time(), value)
                limit = self.keep_for(value) if self.keep_for else None
                if limit is None:
                    self._limits.pop(sub, None)
                else:
                    self._limits[sub] = limit
            return value

        def done(t: asyncio.Task[Any]) -> None:
            if self._refreshing.get(sub, (None,))[0] is t:  # only its own entry, never a newer fetch
                self._refreshing.pop(sub, None)
            if not t.cancelled() and t.exception() is not None:
                logger.debug("member cache refresh failed for a member: %r", t.exception())

        # A fresh context: the refresh is nobody's request, so it must not inherit the caller's
        # Archidekt slot (_slot_holder), client id or request timing.
        task = asyncio.create_task(run(), context=contextvars.Context())
        task.add_done_callback(done)
        self._refreshing[sub] = (task, started)
        if len(self._entries) > 10_000:
            self._entries.clear()
            self._limits.clear()
            self._dropped.clear()
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
            if wait is None:
                return await asyncio.shield(task)
            done, _pending = await asyncio.wait({task}, timeout=wait)
            if not done:
                return None
            return task.result()
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
FULL_TEXT_FIELDS = ("before_text", "after_text")


def _row(kind: str, **fields: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"kind": kind}
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, str) and key not in FULL_TEXT_FIELDS:
            value = clean_text(value)
        out[key] = value
    return out


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
    if kind == "remove":
        return f"-{r['qty']} {label}{side}"
    if kind == "change":
        return f"{r['before']} -> {r['after']} {label}{side}"
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
    if kind == "printing":
        return f"{r['name']}: printing {r['before']} -> {r['after']}"
    if kind == "clone":
        return f"Clone '{r['source']}' ({r['cards']} cards) as '{r['name']}'"
    if kind == "commander":
        return f"Commander: {r['before']} -> {r['after']}"
    if kind == "new_deck":
        visibility = "private" if r.get("private") else "public"
        side = f", {r['side_cards']} more in the maybeboard or sideboard" if r.get("side_cards") else ""
        return f"New {r['format']} deck '{r['name']}' ({r['cards']} cards{side}, {visibility})"
    if kind == "restore":
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
# one caller that may apply an Archidekt account action (R-142).
REVIEW_PAGE = "review_page"
# Who rejected a proposal (DeckService.reject records it; describe() shows it as closed_by).
REJECTED_BY = ("assistant", "card", "review_page", "browser", "api", "disconnected")


class DeckError(Exception):
    """A user-facing failure: the message is safe to show as is. ``extra`` holds structured
    fields the tool reply carries alongside ``error`` and ``message``. ``retry_after`` (seconds)
    is set when the refusal knows how long to wait (a used-up Archidekt budget); the browser
    pages put it in their Retry-After header. ``sent`` is False when the Archidekt request never
    left the gateway (the pacer refused it), so nothing can have been written whatever the kind."""

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        retry_after: int | None = None,
        sent: bool = True,
        **extra: Any,
    ):
        super().__init__(message)
        self.kind = kind
        self.extra = extra
        self.retry_after = retry_after
        self.sent = sent


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
        if self.action == "set_label":
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
        if self.zone == "side":
            d["zone"] = "side"
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


def parse_changes(raw: Any) -> list[Change]:
    if not isinstance(raw, list) or not raw:
        raise DeckError("invalid", "changes must be a non-empty list")
    if len(raw) > MAX_CHANGES:
        raise DeckError("invalid", f"at most {MAX_CHANGES} changes per proposal")
    out: list[Change] = []
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
        if action in CATEGORY_ACTIONS:
            ch = _parse_category_change(i, action, name, item)
            if zone == "side" and action == "set_commander":
                raise DeckError(
                    "invalid", f"change {i}: set_commander works on the deck proper, not zone side"
                )
            out.append(dataclasses.replace(ch, zone=zone))
            continue
        if action in PRINTING_ACTIONS:
            if zone == "side" and action not in ROW_TAG_ACTIONS:
                raise DeckError("invalid", f"change {i}: {action} works on the deck proper, not zone side")
            if action == "set_label":
                out.append(dataclasses.replace(_parse_label_change(i, name, item), zone=zone))
                continue
            if action == "set_mana_value":
                out.append(dataclasses.replace(_parse_mana_value_change(i, name, item), zone=zone))
                continue
            out.append(_parse_printing_change(i, action, name, item))
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
        if zone == "side" and set_code:
            raise DeckError(
                "invalid", f"change {i}: a pinned printing is added to the deck proper, not zone side"
            )
        flags = {}
        for flag in ("companion", "flipped_default"):
            given = item.get(flag)
            if given is None:
                continue
            if not isinstance(given, bool):
                raise DeckError("invalid", f"change {i}: {flag} must be true or false")
            if given and action != "add":
                raise DeckError("invalid", f"change {i}: {flag} applies to add only")
            flags[flag] = given
        out.append(
            Change(action, name, qty, cat, set_code, number, finish, zone=zone, categories=cats, **flags)
        )
    pinned = [ch.card_name.lower() for ch in out if ch.pinned]
    if len(pinned) != len(set(pinned)):
        raise DeckError("invalid", "one pinned printing per card name per proposal")
    # A pinned add next to a remove or set_quantity of the same card would make the writes
    # (add that printing, then shrink the rest) differ from the net count the diff shows.
    pinned_faces = {front_face(n) for n in pinned}
    for ch in out:
        if ch.action in ("remove", "set_quantity") and front_face(ch.card_name.lower()) in pinned_faces:
            raise DeckError(
                "invalid",
                f"'{ch.card_name}' has a pinned printing added and is also removed or set in the same "
                "proposal; propose the removal and the pinned add separately",
            )

    # Names compare by front face, as the plans match them, so "Fire" and "Fire // Ice" clash.
    # The zone is part of the key: a maybeboard row and a deck row of the same card are two rows.
    def key(ch: Change) -> str:
        return front_face(ch.card_name).lower() + ("" if ch.zone == "main" else " (side)")

    categorised = [key(ch) for ch in out if ch.action in CATEGORY_ACTIONS]
    if len(categorised) != len(set(categorised)):
        raise DeckError("invalid", "one set_category or set_commander per card name per proposal")
    reprinted = [key(ch) for ch in out if ch.action in PRINTING_ACTIONS]
    if len(reprinted) != len(set(reprinted)):
        raise DeckError(
            "invalid",
            "one set_finish, set_printing, set_label or set_mana_value per card name per proposal",
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


def printing_plan_rows(
    deck: Deck, changes: list[Change]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """What the set_finish and set_printing changes do to rows of the deck, as specs
    ``{name, relation_ids, quantity, categories, finish?, set_code?, collector_number?}`` plus
    their review rows. A card that is not in the deck is an error; a change that leaves the row
    as it is produces nothing."""
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
                    else ", so its printing cannot be changed"
                ),
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
            rows.append(_row("finish", name=first.name, before=" / ".join(before), after=ch.finish))
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
                {"set_code": ch.set_code, "collector_number": ch.collector_number, "finish": ch.finish}
            )
            rows.append(_row("printing", name=first.name, before=" / ".join(before), after=after))
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
        if any(r.relation_id is None for r in rows):
            raise DeckError("contract", f"Archidekt did not number the deck rows of '{ch.card_name}'.")
        new = list(ch.categories) or [ch.category or COMMANDER]
        if ch.action == "set_commander":
            new_commanders.append(rows[0].name)
        # K10: a new commander keeps its other categories, Commander first (the primary), as
        # archidekt.com's "Set Commander as primary" does
        per_row = {
            r.relation_id: (
                [COMMANDER] + [c for c in r.categories if c != COMMANDER]
                if ch.action == "set_commander"
                else new
            )
            for r in rows
        }
        if ch.action == "set_commander":
            # the order counts: Commander must come first, also when the card already has it
            # as a later category (["Ramp", "Commander"])
            changed = [r for r in rows if list(r.categories) != per_row[r.relation_id]]
        else:
            changed = [r for r in rows if sorted(r.categories) != sorted(per_row[r.relation_id])]
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
            old = sorted({", ".join(sorted(clean_text(c) for c in r.categories)) or "(none)" for r in rows})
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
                # category (A50 M2); without one the row is left with none, as before
                rest = [c.default_category]
            want[c.relation_id] = rest
        old_names = sorted(set(old_commanders), key=str.lower)
        new_names = sorted(set(new_commanders), key=str.lower)
        if [n.lower() for n in old_names] != [n.lower() for n in new_names]:
            lines.append(
                _row("commander", before=", ".join(old_names) or "(none)", after=", ".join(new_names))
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


def side_category_for(deck: Deck, ch: Change) -> str:
    """The category a maybeboard add goes in: the change's own category when the deck excludes
    it, else the deck's maybeboard category."""
    if ch.category and ch.category in deck.excluded_categories():
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


def plan_zones(
    deck: Deck, changes: list[Change]
) -> tuple[dict[str, int], dict[str, int], list[dict[str, Any]], dict[str, int], dict[str, int]]:
    """(before, after, rows, before_side, after_side): the deck proper's counts before and after the
    count changes of zone main, the review rows of every change, and the same counts for the
    maybeboard and sideboard rows (zone side). Category moves between the zones are not in the
    counts (``leaving_deck`` and ``entering_deck`` have them); the rows say what they do."""
    before = deck.counts_by_name()
    before_side = deck.side_counts_by_name()
    lookups: dict[str, dict[str, str]] = {}
    for zone, counts in (("main", before), ("side", before_side)):
        lookup = {n.lower(): n for n in counts}
        for n in counts:  # a double-faced card is also found by its front face
            lookup.setdefault(front_face(n).lower(), n)
        lookups[zone] = lookup
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
    for ch in changes:
        if ch.action in CATEGORY_ACTIONS or ch.action in PRINTING_ACTIONS:
            continue
        target = after_side if ch.zone == "side" else after
        lookup = lookups[ch.zone]
        key = lookup.get(ch.card_name.lower(), ch.card_name)
        cur = target.get(key, 0)
        if ch.action == "add":
            target[key] = cur + (ch.quantity or 1)
        elif ch.action == "remove":
            if cur == 0:
                where = "the deck" if ch.zone == "main" else "the maybeboard or sideboard"
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
            cats = {n: side_category_for(deck, chs[0]) for n, chs in adds_by_name.items()}
        pinned_names = {n for n, chs in adds_by_name.items() if any(c.pinned for c in chs)}
        side = "side" if zone == "side" else None
        for name in sorted(set(b_counts) | set(a_counts), key=str.lower):
            b, a = b_counts.get(name, 0), a_counts.get(name, 0)
            if a == b:
                continue
            printing = labels.get(name.lower()) or None
            cat = cats.get(name.lower())
            if not (cat and (b == 0 or name.lower() in pinned_names)):  # a new row is made in that category
                cat = None
            if b == 0:
                lines.append(_row("add", name=name, qty=a, printing=printing, category=cat, zone=side))
            elif a == 0:
                lines.append(_row("remove", name=name, qty=b, zone=side))
            else:
                lines.append(_row("change", name=name, before=b, after=a, printing=printing, zone=side))
    lines.extend(cat_lines)
    if not lines:
        raise DeckError("invalid", "these changes would leave the deck exactly as it is")
    return before, after, lines, before_side, after_side


MAX_RESTORE_ENTRIES = 150


def _rel_key(card: Any) -> tuple[int | None, str, tuple[str, ...]]:
    return (card.card_id, card.modifier or "Normal", tuple(sorted(card.categories)))


def _rel_fields(card: Any) -> dict[str, Any]:
    """'Sol Ring (cmr 1, Foil) [Commander]' as row fields: what makes one relation differ."""
    bits = [b for b in (card.set_code, card.collector_number) if b]
    detail = " ".join(bits)
    if (card.modifier or "Normal") != "Normal":
        detail = f"{detail}, {card.modifier}" if detail else card.modifier
    cats = ", ".join(sorted(clean_text(c) for c in card.categories)) if card.categories else None
    return {"name": card.name, "printing": f"({detail})" if detail else None, "category": cats}


def _rel_row(kind: str, card: Any, **more: Any) -> dict[str, Any]:
    return _row(kind, **_rel_fields(card), **more)


def restore_steps(snapshot: Deck, current: Deck) -> tuple[list[dict[str, Any]], list[str]]:
    """PATCH entries and diff lines that turn ``current`` into ``snapshot`` (see ``restore_rows``)."""
    payload, rows = restore_rows(snapshot, current)
    return payload, [row_line(r) for r in rows]


def restore_rows(snapshot: Deck, current: Deck) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """PATCH entries and review rows that turn ``current`` into ``snapshot`` relation by relation:
    the same printing (card id), finish (modifier) and categories, including Commander, Sideboard
    and Maybeboard, which Archidekt keeps as categories. Rows that already match are untouched."""
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
        if rows and total == qty:
            continue
        if rows:
            payload.append(_entry("modify", rows[0], qty))
            payload.extend(_entry("remove", extra, 0) for extra in rows[1:])
            lines.append(_rel_row("change", card, before=total, after=qty))
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
                    },
                }
            )
            lines.append(_rel_row("add", card, qty=qty))
    for key, rows in have.items():
        if key in want:
            continue
        payload.extend(_entry("remove", r, 0) for r in rows)
        lines.append(_rel_row("remove", rows[0], qty=sum(r.quantity for r in rows)))
    return payload, sorted(lines, key=lambda r: row_line(r).split(" ", 1)[-1].lower())


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
) -> list[dict[str, Any]]:
    """Per-card entries for PATCH modifyCards/v2, one entry per request.

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
    with the same printing and finish, which then grows by the added quantity.
    """
    by_name: dict[str, list[Any]] = {}
    for c in deck.main_cards:  # never touch maybeboard or sideboard rows
        by_name.setdefault(c.name.lower(), []).append(c)
    payload: list[dict[str, Any]] = []
    for name, qty in after.items():
        existing = by_name.get(name.lower(), [])
        current = sum(c.quantity for c in existing)
        if qty == current:
            continue
        pin = (pinned or {}).get(name)
        if pin:
            same = [
                c
                for c in existing
                if c.card_id == pin["cardid"] and (c.modifier or "Normal") == pin["modifier"]
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
                        (flags or {}).get(name, (False, False)),
                    )
                )
                qty -= int(pin["quantity"])
            if qty < sum(c.quantity for c in existing if c not in same):
                # parse_changes refuses the combinations that get here; never send writes that
                # shrink other rows below what the reviewed diff says
                raise DeckError("invalid", f"the pinned add of '{name}' does not match the planned count")
            # whatever the proposal's other changes do to this card's remaining rows
            existing = [c for c in existing if c not in same]
            if qty == sum(c.quantity for c in existing):
                continue
        if existing:
            primary, extras = existing[0], existing[1:]
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
                )
            )
    side_by_name: dict[str, list[Any]] = {}
    for c in deck.side_cards:
        side_by_name.setdefault(c.name.lower(), []).append(c)
    for name, qty in (after_side or {}).items():
        existing = side_by_name.get(name.lower(), [])
        if qty == sum(c.quantity for c in existing):
            continue
        if existing:
            primary, extras = existing[0], existing[1:]
            payload.append(_entry("modify" if qty > 0 else "remove", primary, qty))
            payload.extend(_entry("remove", extra, 0) for extra in extras)
        else:
            payload.append(
                _add_entry(
                    resolve[name],
                    qty,
                    (side_categories or {}).get(name) or [deck.side_category()],
                    (modifiers or {}).get(name, "Normal"),
                    (side_flags or {}).get(name, (False, False)),
                )
            )
    for card in deck.cards:
        if card.relation_id in (recategorise or {}):
            payload.append(_entry("modify", card, card.quantity, categories=recategorise[card.relation_id]))
    return payload


def _add_entry(
    cardid: int,
    qty: int,
    categories: list[str] | None,
    modifier: str,
    flags: tuple[bool, bool] = (False, False),
) -> dict[str, Any]:
    """A new row; ``flags`` are its companion and flippedDefault (an Undo of a removal)."""
    return {
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
    A row with no tag sends Archidekt's empty one, ``NO_LABEL``, as its editor does (Q58)."""
    mods: dict[str, Any] = {
        "quantity": qty,
        # the row's own flags, as the site sends them (its site code, read 2026-10-14): sending
        # False would clear a companion or a back-face default on any change to the row (A50)
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


class DeckService:
    def __init__(self, settings: Settings, db: Database, client: ArchidektClient):
        self.settings = settings
        self.db = db
        self.client = client
        self.fernet = Fernet(settings.fernet_key.encode())
        self._deck_locks: dict[str, asyncio.Lock] = {}
        # Applies running in the background (proposal id -> task) and what each got done so far.
        self._running: dict[str, asyncio.Task[Any]] = {}
        self._progress: dict[str, dict[str, Any]] = {}
        self._archidekt_in_flight: dict[str, int] = {}
        self._link_locks: dict[str, asyncio.Lock] = {}
        # backup copy id -> the name, description and folder id it was made with, until its
        # unused delete is queued (review D4); only the newest BACKUPS_REMEMBERED are kept
        self._backups_made: dict[str, dict[str, str]] = {}
        self.max_archidekt_per_user = MAX_ARCHIDEKT_PER_USER
        self.archidekt_budget = RateBudget(settings.archidekt_calls_per_10_min)
        # sub -> {matched card name (lower case): what the scan read}; set when scanning is on
        self.guessed_names: Callable[[str], dict[str, str]] | None = None
        # (member, proposal id) for each proposal an assistant saves; set by app.py to notify the
        # member when it waits for their approval (notify.py). Never stops the proposal.
        self.on_proposal_saved: Callable[[str, str], None] | None = None
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
        self.page_deck_seconds = min(PAGE_DECK_SECONDS, float(settings.archidekt_cache_seconds))
        # Other member caches (the collection's first page) hear about writes here: (sub, path).
        self.write_hooks: list[Callable[[str, str], None]] = []
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
        self._forget_page_decks()
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
        self.deck_lists.drop(sub)
        self.folder_trees.drop(sub)
        self._forget_page_decks()
        for hook in self.write_hooks:
            hook(sub, "")

    @asynccontextmanager
    async def archidekt_slot(self, sub: str | None) -> AsyncIterator[None]:
        """Count one unit of Archidekt work for ``sub`` while the block runs. At most
        ``max_archidekt_per_user`` run or wait in the shared pacer per member; another is refused
        at once (rate_limited) instead of queueing behind them for everyone. A task that already
        holds the member's slot (an apply's reads and writes) does not take another."""
        if not sub or _slot_holder.get() == sub:
            yield
            return
        if self._archidekt_in_flight.get(sub, 0) >= self.max_archidekt_per_user:
            raise DeckError(
                "rate_limited",
                f"{self.max_archidekt_per_user} Archidekt actions for your account are still running "
                "or waiting their turn; wait for them to finish and try again.",
            )
        refused = self.budget_refusal(sub)
        if refused:
            raise DeckError("rate_limited", refused, retry_after=self.archidekt_budget.retry_after(sub))
        self._archidekt_in_flight[sub] = self._archidekt_in_flight.get(sub, 0) + 1
        token = _slot_holder.set(sub)
        try:
            yield
        finally:
            _slot_holder.reset(token)
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
            f"the last {ARCHIDEKT_BUDGET_WINDOW // 60} minutes; wait a few minutes and try again."
        )

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
            raise DeckError(exc.kind, str(exc)) from exc
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
            default=self.settings.approval_mode_default,
            cap=self.settings.approval_mode_max,
        )

    def mode_info(self, sub: str) -> dict[str, Any]:
        mode = self.mode_of(sub)
        return {
            "approval_mode": mode,
            "approval_mode_label": modes.MODE_LABELS[mode],
            "approval_mode_note": modes.mode_help(mode, max_rows=self.settings.auto_apply_max_rows)
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

    async def _call(self, sub: str, fn: Any, *args: Any) -> Any:
        """``_call_unslotted`` inside the member's Archidekt slot (see ``archidekt_slot``)."""
        async with self.archidekt_slot(sub):
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
                raise DeckError(exc.kind, str(exc), sent=False) from exc
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
        quiet = no_retry.set(background)
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
            raise DeckError(exc.kind, str(exc), sent=exc.sent) from exc
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
    async def list_decks(self, sub: str) -> list[dict[str, Any]]:
        """The member's decks, from the in-memory list when it is fresh (DECK_LIST_FRESH) or stale
        with a refresh running (DECK_LIST_STALE), else read from Archidekt now. Every write the
        gateway sends for the member drops the list first, so the next read is live again."""
        rows = await self.list_decks_quick(sub, wait=None)
        assert rows is not None
        return rows

    async def _deck_rows(
        self, sub: str, *, wait: float | None, prefer_fresh: bool = False
    ) -> DeckList | None:
        """Every deck of the linked account from the member cache (or Archidekt), the gateway's
        backup copies marked ``backup: True``: those in the backup folder, those named like one,
        and those a snapshot here records as its copy (``db.backup_copies``). The list keeps the
        listing's ``complete`` and ``unreadable`` (see archidekt.DeckList); a list of unknown origin
        is incomplete. A list with entries whose owner could not be read is kept only briefly
        (DECK_LIST_PARTIAL): My decks says so with Try again (Q64), which reads Archidekt again
        through ``reread_deck_list``."""
        _token, row = self._token(sub)  # not linked: raise before touching the cache
        folder = self.settings.archidekt_backup_folder  # with backups on or off (Q21)

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
        unreadable = getattr(rows, "unreadable", 0)
        known = self.db.backup_copies(sub)
        out = []
        for d in rows:
            d = dict(d)
            if d["id"] in known:
                d["backup"] = True
                d["backup_of"] = known[d["id"]]["deck_id"]
                d["snapshot_id"] = known[d["id"]]["snapshot_id"]
            out.append(d)
        return DeckList(out, complete=getattr(rows, "complete", False) is True, unreadable=unreadable)

    async def list_decks_quick(self, sub: str, *, wait: float | None = None) -> list[dict[str, Any]] | None:
        """``list_decks`` that gives up waiting for a cold read after ``wait`` seconds and answers
        None; the read carries on and lands in the cache for /api/decks/mine. ``wait=None``
        waits for it. The list comes back as fresh dicts, so a caller may sort or add to it. The
        gateway's backup copies are left out (``backup_copies`` lists them, History shows them).
        It keeps the listing's ``complete`` and ``unreadable`` (archidekt.DeckList)."""
        rows = await self._deck_rows(sub, wait=wait)
        if rows is None:
            return None
        return DeckList(
            [d for d in rows if not d.get("backup")], complete=rows.complete, unreadable=rows.unreadable
        )

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
        rows = await self._deck_rows(sub, wait=wait, prefer_fresh=True)
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

    def decks_fetched_at(self, sub: str) -> float | None:
        """When the cached deck list was read from Archidekt (epoch seconds), None when none is."""
        return self.deck_lists.fetched_at(sub)

    def reread_deck_list(self, sub: str) -> None:
        """Try again on the Q64 notice: forget the member's list when the one kept has entries
        Archidekt left unreadable, so the next page reads Archidekt again (review N10). A list
        that read fine is kept, so an old Try again link costs Archidekt nothing."""
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
        if sub and self.db.get_link(sub):
            try:
                return await self.get_deck(sub, deck_id)
            except DeckError as exc:
                if exc.kind not in ("not_found", "auth", "forbidden", "not_linked"):
                    raise
        async with self.archidekt_slot(sub):
            try:
                if fresh:
                    return await self.client.get_deck(None, deck_id, fresh=True)
                return await self.client.get_deck(None, deck_id)
            except ArchidektError as exc:
                raise DeckError(exc.kind, str(exc)) from exc

    # -- the deck sync's reads (sync.py) ----------------------------------------
    def archidekt_busy(self) -> bool:
        """Whether any member's Archidekt work is running or waiting, or an apply is under way:
        the deck sync waits for that to finish, so it never holds up a member or an assistant."""
        return bool(self._archidekt_in_flight) or bool(self._running) or self.client.pacer.busy()

    def deck_busy(self, sub: str, deck_id: str) -> bool:
        """Whether an apply (or restore) on this deck is running: the sync leaves the deck alone
        until it ends, so the gateway's own write is never read halfway."""
        lock = self._deck_locks.get(f"{sub}:{deck_id}")
        return (lock is not None and lock.locked()) or self.db.deck_applying(sub, deck_id)

    async def sync_deck_list(self, sub: str, *, wait: float = 120.0) -> list[dict[str, Any]] | None:
        """The member's deck list for the deck sync: the cached one while it is fresh, else one
        listing request (shared with a page's read already under way). Read-only, outside the
        member's own Archidekt slot and budget, and not counted as the member using their link.
        None when the read takes longer than ``wait`` (it carries on and fills the cache)."""
        value, state = self.deck_lists.peek(sub)
        if state == "fresh":
            return [dict(d) for d in value]
        _token, row = self._token(sub)
        folder = self.settings.archidekt_backup_folder  # with backups on or off (Q21)

        async def fetch() -> list[dict[str, Any]]:
            return await self._call_unslotted(
                sub,
                lambda token, *_: self.client.list_decks(
                    token, row["archidekt_username"], row.get("archidekt_user_id"), backup_folder=folder
                ),
                background=True,
            )

        # A page arriving meanwhile shares this read, and with it the sync's terms (no retry, no
        # unlink); its next load reads again.
        rows = await self.deck_lists.refreshed(sub, fetch, wait=wait)
        return None if rows is None else [dict(d) for d in rows]

    async def sync_read_deck(self, sub: str, deck_id: str) -> Deck:
        """One live read of the member's deck for the deck sync (never cached, like every read
        with the member's session); outside their slot and budget, like sync_deck_list."""
        deck_id = _clean_deck_id(deck_id)
        deck: Deck = await self._call_unslotted(sub, self.client.get_deck, deck_id, background=True)
        return deck

    def _out_of_date_since(self, proposal_id: str) -> int | None:
        return self.out_of_date(proposal_id) if self.out_of_date is not None else None

    def _forget_page_decks(self) -> None:
        self._page_decks.clear()
        self._page_decks_gen += 1

    async def page_deck(self, sub: str, deck_ref: str, *, fresh: bool = False) -> Deck:
        """``get_any_deck`` for showing a deck page. Switching between the deck's views, or coming
        back to it, within PAGE_DECK_SECONDS shows the deck as last read instead of asking Archidekt
        again (each ask waits its turn in the pacer shared by every member). Any write the gateway
        sends forgets every such read, and ``fresh`` (the browser's Reload) always asks, so a change
        made on archidekt.com shows at once on a reload. Only for showing a deck: proposals and
        applies read it themselves."""
        ttl = self.page_deck_seconds
        key = (sub, _clean_deck_id(deck_ref))
        hit = self._page_decks.get(key)
        if hit is not None and not fresh and time.monotonic() - hit[0] < ttl:
            return pickle.loads(hit[1])  # a copy of its own: the page may change it
        gen = self._page_decks_gen
        # A Reload skips the client's shared 60-second read cache too (decks read without the
        # member's own link: someone else's public deck, or any deck with no account linked).
        deck = await (
            self.get_any_deck(sub, deck_ref, fresh=True) if fresh else self.get_any_deck(sub, deck_ref)
        )
        if ttl > 0 and gen == self._page_decks_gen:
            if len(self._page_decks) >= PAGE_DECKS_MAX:
                oldest = min(self._page_decks, key=lambda k: self._page_decks[k][0])
                self._page_decks.pop(oldest, None)
            self._page_decks[key] = (time.monotonic(), pickle.dumps(deck))
        return deck

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
                raise DeckError(exc.kind, str(exc)) from exc

    async def user_profile(self, sub: str | None, username: str) -> dict[str, Any] | None:
        username = re.sub(r"[^A-Za-z0-9_.@ -]", "", str(username or "")).strip()[:60]
        if not username:
            raise DeckError("invalid", "give an Archidekt username")
        async with self.archidekt_slot(sub):
            try:
                return await self.client.user_profile(username)
            except ArchidektError as exc:
                raise DeckError(exc.kind, str(exc)) from exc

    # -- proposals ------------------------------------------------------------
    async def propose(self, sub: str, deck_id: str, raw_changes: Any) -> dict[str, Any]:
        deck_id = _clean_deck_id(deck_id)
        changes = parse_changes(raw_changes)
        self._room_for_proposal(sub)
        deck = await self.get_own_deck(sub, deck_id)
        _before, _after, rows = plan_rows(deck, changes)
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
    ) -> dict[str, Any]:
        name = clean_text(name or "")
        if not name or len(name) > 120:
            raise DeckError("invalid", "name is required (up to 120 characters)")
        fmt = str(deck_format or "commander").strip().lower()
        if fmt not in FORMAT_IDS:
            raise DeckError("invalid", f"deck_format must be one of: {', '.join(sorted(FORMAT_IDS))}")
        entries = normalise_cards(
            cards=cards, decklist_text=decklist_text, csv_text=csv_text, json_text=json_text
        )
        _token, link = self._token(sub)  # must be linked before we store a proposal that needs it
        self._room_for_proposal(sub)
        # A new deck gets only category names, so only Maybeboard and Sideboard (which Archidekt
        # keeps out of the deck itself) stay apart. A custom category flagged {noDeck} is made as
        # a plain category, so its cards count in the deck, and the proposal says so.
        flagged = [
            c
            for c in entries
            if c.get("in_deck") is False and c["categories"][:1] and c["categories"][0] not in SIDE_CATEGORIES
        ]
        flags_dropped = sorted({c["categories"][0] for c in flagged})
        for c in flagged:
            del c["in_deck"]
        # Maybeboard and sideboard rows (in_deck false) are created too, but not counted as deck cards.
        total = sum(c["quantity"] for c in entries if c.get("in_deck", True))
        side = sum(c["quantity"] for c in entries) - total
        rows = [
            _row(
                "new_deck", name=name, format=fmt, cards=total, side_cards=side or None, private=bool(private)
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
        pid = secrets.token_urlsafe(12)
        self._save_proposal(
            {
                "id": pid,
                "owner_sub": sub,
                "kind": "create_deck",
                "deck_id": "new",
                "deck_name": name,
                "baseline_fingerprint": "",
                # The Archidekt account the deck is made in: shown on the review page, and the
                # apply is refused if another account is linked by then.
                "changes": {
                    "name": name,
                    "format": fmt,
                    "private": private,
                    "cards": entries,
                    "archidekt_username": link["archidekt_username"],
                    "archidekt_user_id": link.get("archidekt_user_id"),
                },
            },
            rows,
        )
        self._audit("proposal_created", sub=sub, detail={"proposal_id": pid, "kind": "create_deck"})
        out = self.describe(sub, pid)
        if flags_dropped:
            out["warnings"] = [
                f"Category {', '.join(flags_dropped)} is kept out of the deck in your list, but a new "
                "Archidekt deck gets it as a plain category, so its cards count in the deck. Move "
                "them to Maybeboard or Sideboard, or set the category's flag on archidekt.com after."
            ]
        return out

    def describe(self, sub: str, proposal_id: str) -> dict[str, Any]:
        row = self.db.get_proposal(proposal_id, sub)
        if row is None:
            raise DeckError("not_found", "No such proposal for your account.")
        state = row["state"]
        if state == "pending" and row["expires_at"] < int(time.time()):
            state = "expired"
        kind = row.get("kind", "edit")
        risk, why = modes.risk_of(kind, row.get("rows"), max_rows=self.settings.auto_apply_max_rows)
        mode = self.mode_of(sub)
        # "May apply now": the member's mode allows it for this risk AND the proposal is still
        # pending (risk, approval_mode and risk_reason keep the policy side either way).
        may_apply = (
            state == "pending" and self.settings.writes_enabled and modes.assistant_may_apply(mode, risk)
        )
        closed_by, closed_by_app = self._closer(row) if state == "rejected" else (None, None)
        # The deck sync (sync.py) saw the deck change after this proposal was made (R-153).
        out_of_date = self._out_of_date_since(row["id"]) if state == "pending" else None
        # Archidekt's waits stopped the last apply: it failed, or it changed nothing and the
        # proposal is pending again (Q78; an out-of-date one is told only that it is out of date)
        stopped = (row["result"] or {}).get("error") == "rate_limited" and (
            state == "failed" or (state == "pending" and self.settings.writes_enabled and not out_of_date)
        )
        return {
            "proposal_id": row["id"],
            "kind": kind,
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
            # When and by whom a closed proposal was closed (closed_by: assistant, card,
            # review_page, browser, api or disconnected; recorded since 0.7.22).
            **({"closed_at": row["applied_at"]} if state in ("rejected", "failed") else {}),
            **({"closed_at": row["expires_at"]} if state == "expired" else {}),
            **({"closed_by": closed_by} if closed_by else {}),
            **({"closed_by_app": closed_by_app} if closed_by_app else {}),
            # while an apply runs in the background: how far it got (see APPLY_WAIT_SECONDS)
            **(
                {"progress": _progress_view(self._progress[row["id"]])}
                if state == "applying" and row["id"] in self._progress
                else {}
            ),
            "snapshot_id": row["snapshot_id"],
            "result": row["result"],
            "review_url": f"{self.settings.public_url}/proposals/{row['id']}",
            # Which app made it ("browser", or "app: <its name> (<client id>)"), for the review page.
            "created_by": self._creator_label(row.get("created_by_client")),
            "writes_enabled": self.settings.writes_enabled,
            # The member's approval mode (modes.py) and this proposal's risk tier decide whether
            # the assistant may apply it itself or the member's own press is needed.
            "approval_mode": mode,
            "risk": risk,
            "risk_reason": why,
            "assistant_may_apply": may_apply,
            **({"out_of_date": True, "out_of_date_since": out_of_date} if out_of_date else {}),
            "next_step": (OUT_OF_DATE_STEP if out_of_date else "")
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
                partial=bool((row["result"] or {}).get("sent_entries")),
                kind=kind,
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

    def _room_for_proposal(self, sub: str) -> None:
        """Refuse a new proposal while the member already has MAX_PENDING_PROPOSALS pending, or
        the app making it has MAX_PENDING_PER_CLIENT pending for them."""
        if self.db.count_pending_proposals(sub) >= MAX_PENDING_PROPOSALS:
            raise DeckError(
                "rate_limited",
                f"You already have {MAX_PENDING_PROPOSALS} pending proposals. Apply or reject some "
                f"of them at {self.settings.public_url}/proposals (or wait for them to expire) "
                "before proposing more.",
            )
        if self.db.count_pending_for_client(sub, current_client.get()) >= MAX_PENDING_PER_CLIENT:
            raise DeckError(
                "rate_limited",
                f"This app already has {MAX_PENDING_PER_CLIENT} pending proposals for you. Apply or "
                f"reject some of them at {self.settings.public_url}/proposals (or wait for them to "
                "expire) before proposing more.",
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
                "expires_at": int(time.time()) + self.settings.proposal_ttl,
                "created_by_client": current_client.get(),
            },
            max_pending=MAX_PENDING_PROPOSALS,
            max_closed=MAX_CLOSED_PROPOSALS,
            max_pending_per_client=MAX_PENDING_PER_CLIENT,
        )
        if not saved:
            self._room_for_proposal(row["owner_sub"])
            raise DeckError("rate_limited", "Too many pending proposals; apply or reject some first.")
        if self.on_proposal_saved and current_client.get() not in (None, BROWSER_CLIENT, ADMIN_CLIENT):
            try:
                self.on_proposal_saved(row["owner_sub"], row["id"])
            except Exception:
                logger.exception("proposal notification failed")

    def list_proposals(self, sub: str, *, full: bool = False, **narrow: Any) -> list[dict[str, Any]]:
        """Newest first; ``narrow`` is passed to the database (deck_id, kinds, states, search,
        limit, offset). With ``full`` each row keeps its whole change text as ``diff_text``."""
        now = int(time.time())
        out = []
        for r in self.db.list_proposals(sub, **narrow):
            state = "expired" if r["state"] == "pending" and r["expires_at"] < now else r["state"]
            diff_text = str(r.pop("diff_text", None) or "")
            lines = diff_text.splitlines()
            summary = "; ".join(lines[:3]) + (f" (+{len(lines) - 3} more)" if len(lines) > 3 else "")
            if full:
                r["diff_text"] = diff_text
            out.append(
                {
                    **r,
                    "summary": summary[:400],
                    "state": state,
                    "created_by": self._creator_label(r.get("created_by_client")),
                    "review_url": f"{self.settings.public_url}/proposals/{r['id']}",
                    **(
                        {"out_of_date": True}
                        if state == "pending" and self._out_of_date_since(r["id"])
                        else {}
                    ),
                }
            )
        return out

    def _review_page_only(self, sub: str, row: dict[str, Any]) -> DeckError:
        """The refusal for any caller but the review page's own form that tries to apply an
        Archidekt account action (R-142), with the row's real risk tier (consent, or destructive
        for a deck deletion). Audited as ``apply_needs_user``."""
        risk, _why = modes.risk_of("action", row.get("rows"))
        self._audit(
            "apply_needs_user",
            sub=sub,
            detail={"proposal_id": row["id"], "deck_id": row["deck_id"], "risk": risk},
        )
        return DeckError(
            "browser_required",
            "This is an action on the user's Archidekt account (a like, vote, bookmark, follow, "
            "comment, folder, collaborator change or deck deletion). Each one needs the user's "
            "own approval on the review page (review_url), in every approval mode. Nothing was sent to "
            "Archidekt; do not retry.",
            review_url=f"{self.settings.public_url}/proposals/{row['id']}",
            state=row["state"],
            approval_mode=self.mode_of(sub),
            risk=risk,
        )

    def approval_for(self, sub: str, row: dict[str, Any]) -> str:
        """The in-chat card's one-time code for a proposal (approve.py), bound to the proposal,
        its owner, the app that made it and its creation time. An account action has no code
        (R-142: only its review page applies it), so asking for one is refused here, at the source."""
        if row.get("kind") == "action":
            raise DeckError(
                "browser_required",
                "An action on the user's Archidekt account has no in-chat approval code; only its "
                "review page applies it.",
                review_url=f"{self.settings.public_url}/proposals/{row['id']}",
            )
        return approval_code(
            self.settings.session_secret,
            proposal_id=row["id"],
            sub=sub,
            client_id=row.get("created_by_client") or "",
            created_at=int(row["created_at"]),
        )

    def check_approval(self, sub: str, proposal_id: str, approval: Any) -> dict[str, Any]:
        """The proposal row when ``approval`` is its code and the caller is the app that made it;
        otherwise ``invalid_approval`` (audited), and nothing else happens."""
        row = self.db.get_proposal(proposal_id, sub)
        if row is None:
            raise DeckError("not_found", "No such proposal for your account.")
        if row.get("kind") == "action":  # R-142: no code exists for it, whatever was sent
            raise self._review_page_only(sub, row)
        creator = row.get("created_by_client")
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

    async def apply(
        self, sub: str, proposal_id: str, *, via: str, archidekt_backup: bool | None = None
    ) -> dict[str, Any]:
        """``archidekt_backup=False`` (a member's own hand edit, the save bar's tick box unticked, or a
        quick edit on the deck page) skips the extra backup copy on Archidekt; the gateway's own
        snapshot is always taken. The assistant's applies always keep the copy."""
        if not self.settings.writes_enabled:
            raise DeckError(
                "writes_disabled",
                "Deck writes are switched off on this gateway (MTG_WRITES_ENABLED is false). "
                "The proposal is kept for review; nothing was sent to Archidekt.",
            )
        row = self.db.get_proposal(proposal_id, sub)
        if row is None:
            raise DeckError("not_found", "No such proposal for your account.")
        if row["state"] == "applied":
            raise DeckError(
                "already_applied",
                "This proposal was already applied; nothing was sent again.",
                state="applied",
            )
        # R-142, structural: only the review page's own form passes REVIEW_PAGE, and an account
        # action (kind "action") is refused for every other caller, whichever route it came by.
        review_page = via == REVIEW_PAGE
        if review_page:
            via = "browser"  # otherwise the review page is an ordinary browser apply
        if archidekt_backup is False and via == "browser":
            row["archidekt_backup"] = False
        creator = row.get("created_by_client")
        # "mcp" is the assistant's own apply_proposal call: allowed only when this member's
        # approval mode (modes.py) lets the assistant apply a proposal of this risk itself.
        # "app" is the member's press on the in-chat card, gated by its one-time code
        # (approve.py); "browser" is a member's own page (hand edits, the JSON API with a
        # session); REVIEW_PAGE is the review page's form; "auto" is never a caller's choice.
        if not review_page and row.get("kind") == "action":
            # R-142: an Archidekt account action (social, folder, collaborator, delete deck) is
            # applied only by the member's own press on the signed-in review page, whatever the
            # mode. Not the assistant's apply_proposal, not the REST API, and not the in-chat card
            # either: the card's press runs inside the chat app, where the model's own calls run
            # too. This is the one check that decides it, for every route.
            raise self._review_page_only(sub, row)
        if via == "mcp":
            # The risk tier (L1) decides only the assistant's own applies of ordinary proposals:
            # for an account action it is display only (what the assistant is told), because the
            # check above has already refused every caller but the review page.
            kind = row.get("kind", "edit")
            risk, why = modes.risk_of(kind, row.get("rows"), max_rows=self.settings.auto_apply_max_rows)
            mode = self.mode_of(sub)
            if not modes.assistant_may_apply(mode, risk):
                self._audit(
                    "apply_needs_user",
                    sub=sub,
                    detail={
                        "proposal_id": proposal_id,
                        "deck_id": row["deck_id"],
                        "mode": mode,
                        "risk": risk,
                    },
                )
                raise DeckError(
                    "browser_required",
                    _needs_user_message(mode, why),
                    review_url=f"{self.settings.public_url}/proposals/{proposal_id}",
                    state=row["state"],
                    approval_mode=mode,
                    risk=risk,
                )
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

        async def run() -> dict[str, Any]:
            async with self.archidekt_slot(sub):  # refused before the claim, so nothing is sent
                return await self._apply_slotted(sub, proposal_id, row, via=via)

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

    async def aclose(self, grace: float = SHUTDOWN_GRACE_SECONDS) -> None:
        """At shutdown: give running applies ``grace`` seconds to finish, then cancel the rest,
        which records each as failed ("interrupted", with what it had sent) before the database
        closes."""
        await self.deck_lists.aclose()
        running = list(self._running.values())
        if not running:
            return
        _done, pending = await asyncio.wait(running, timeout=grace)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)

    async def _apply_slotted(
        self, sub: str, proposal_id: str, row: dict[str, Any], *, via: str
    ) -> dict[str, Any]:
        if not self.db.claim_proposal(proposal_id, sub):
            raise self._not_pending(sub, proposal_id, "applied")
        lock = self._deck_locks.setdefault(f"{sub}:{row['deck_id']}", asyncio.Lock())
        # What the apply got done so far (snapshot, backup, entries sent of how many, the verify
        # result), kept in the proposal's result whichever way the apply ends, and shown by
        # describe() while it runs.
        progress: dict[str, Any] = {}
        coll = row.get("kind") == "collection"  # the messages say "your collection", not "the deck"
        clone = row.get("kind") == "clone"  # a copy holds the cards: never "no cards were added"
        self._progress[proposal_id] = progress
        # This task's own context: a rate limit is waited out instead of failing the apply halfway,
        # within one allowance for the whole apply (Q66)
        patient.set(Patience())
        wait_note.set(progress)
        apply_writes.set(progress)  # every write of the apply counts as a change (review D1, D2)
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
                if exc.kind == "rate_limited" and not _changed_anything(progress):
                    # Q78: Archidekt's waits stopped it before anything changed there, so the
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
                # (Q41, Q66): what stopped it, how far it got, and to try again later.
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
        self._audit("proposal_applied", sub=sub, detail={"proposal_id": proposal_id, "via": via})
        return result

    async def _apply_claimed(self, sub: str, row: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        if row.get("kind") == "create_deck":
            return await self._apply_create(sub, row, progress)
        if row.get("kind") == "restore":
            return await self._apply_restore(sub, row, progress)
        if row.get("kind") == "details":
            return await self._apply_details(sub, row, progress)
        if row.get("kind") == "clone":
            return await self._apply_clone(sub, row, progress)
        if row.get("kind") == "collection":
            return await self._apply_collection(sub, row, progress)
        if row.get("kind") == "action":
            from . import actions

            return await actions.apply(self, sub, row, progress)
        changes = parse_changes(row["changes"])
        deck = await self._current_deck_for(sub, row)
        _before, after, _lines, _before_side, after_side = plan_zones(deck, changes)
        recategorise, _cat_lines = category_plan(deck, changes)
        # Every printing is looked up before anything is written or backed up, so a printing
        # that does not exist (or is another card) refuses the proposal with nothing changed.
        resolve, modifiers, pinned, new_cats, side_cats = await self._resolve_adds(
            sub, deck, changes, after, after_side
        )
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
        )
        specs, _print_lines = printing_plan_rows(deck, changes)
        payload += await self._printing_entries(sub, deck, specs)
        snapshot_id = self._take_snapshot(sub, row, deck)
        progress["snapshot_id"] = snapshot_id
        backup = await self._backup_on_archidekt(sub, row, deck, snapshot_id)
        progress.update(backup)
        await self._rows_unchanged(sub, deck)  # lookups and backup take a while: check again
        await self._send(sub, deck.id, payload, progress)
        verified = await self.get_deck(sub, deck.id)
        mismatches = edit_mismatches(deck, changes, verified, specs)
        result = {
            "snapshot_id": snapshot_id,
            **backup,
            "sent_entries": len(payload),
            "verified": not mismatches,
            "mismatched_cards": mismatches,
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
        dict[str, int], dict[str, str], dict[str, dict[str, Any]], dict[str, list[str]], dict[str, list[str]]
    ]:
        """Printing ids and finishes for the cards an edit adds, keyed by the spelling used in
        ``after`` (and ``after_side`` for maybeboard adds, whose categories come back as the fifth
        value). A pinned printing (set code + collector number) is required to exist and to be
        that card: the client raises ``not_found`` otherwise and nothing is added."""
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
        for key, chs in side_adds.items():
            side_cat = side_category_for(deck, chs[0])
            # an add naming every category (an Undo of a removal) keeps the others beside it
            side_cats[key] = [side_cat] + [c for c in _all_categories(chs)[1:] if c != side_cat]
            if after_side.get(key, 0) > 0 and key.lower() not in have_side:
                card = await self._call(sub, self.client.resolve_card, key)
                resolve[key] = card["id"]
                finish = next((c.finish for c in chs if c.finish), None)
                if finish:
                    modifiers[key] = finish_modifier(
                        card["options"], foil=finish == "Foil", etched=finish == "Etched"
                    )
        for key, chs in adds.items():
            cats = _all_categories(chs)
            if cats:
                new_cats[key] = cats
            for ch in (c for c in chs if c.pinned):
                card = await self._call(
                    sub,
                    lambda token, *_, ch=ch: self.client.resolve_card(
                        token,
                        ch.card_name,
                        set_code=ch.set_code,
                        collector_number=ch.collector_number,
                        require_printing=True,
                    ),
                )
                pinned[key] = {
                    "cardid": card["id"],
                    "modifier": finish_modifier(
                        card["options"], foil=ch.finish == "Foil", etched=ch.finish == "Etched"
                    ),
                    "quantity": ch.quantity or 1,
                    "categories": new_cats.get(key, []),
                }
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
        return resolve, modifiers, pinned, new_cats, side_cats

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
        deck = await self.get_own_deck(sub, row["deck_id"])  # ownership re-checked at apply time
        if deck.fingerprint() != row["baseline_fingerprint"]:
            raise DeckError(
                "stale",
                "The deck changed on Archidekt since this proposal was made. Nothing was sent. "
                "Create a new proposal from the current deck.",
            )
        return deck

    async def _rows_unchanged(self, sub: str, deck: Deck) -> None:
        """Re-read the deck right before writing and refuse if a card row changed meanwhile.
        Rows only: the backup copy made just before may bump the deck's updated time."""
        now = await self.get_own_deck(sub, deck.id)
        if now.rows_fingerprint() != deck.rows_fingerprint():
            raise DeckError(
                "stale",
                "The deck changed on Archidekt while this change was being prepared. Nothing was "
                "sent. Create a new proposal from the current deck.",
            )

    def _take_snapshot(self, sub: str, row: dict[str, Any], deck: Deck) -> str:
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
        return snapshot_id

    async def _drop_unused_backup(
        self, sub: str, proposal_id: str, progress: dict[str, Any], *, source_deck_id: str
    ) -> None:
        """An apply that failed before it changed the deck deletes the backup copy it made a moment
        earlier on Archidekt: the deck is as it was, so the copy would only pile up in the backup
        folder. When the deck may have changed the copy is kept, as the readable way back. The
        gateway's own snapshot is always kept. Only the copy this apply made is ever deleted: never
        the deck the apply was for, and only the copy its snapshot records (review L7).

        Q77: while Archidekt refuses calls for a while (a pause running now, or the delete meets a
        429, a 5xx, a network error or the breaker) the delete is queued for backup_delete_loop
        instead, with no extra waiting here; any other refusal keeps the copy."""
        backup_id = progress.get("backup_deck_id")
        if not backup_id or _changed_anything(progress):
            return
        snap = self.db.get_snapshot(str(progress.get("snapshot_id") or ""), sub)
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
        # No waiting in the apply for this delete (Q77): an allowance already used up makes a 429
        # fail at once, and the delete is queued. It is still the apply's 429, so it does not count
        # towards the breaker (Q62).
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
        if progress.get("snapshot_id"):
            self.db.clear_snapshot_backup(progress["snapshot_id"])
        progress.pop("backup_deck_id", None)
        progress.pop("backup_url", None)
        progress["backup_deleted"] = True
        self._audit(
            "backup_deleted", sub=sub, detail={"proposal_id": proposal_id, "backup_deck_id": backup_id}
        )

    def _queue_backup_delete(
        self, sub: str, proposal_id: str, progress: dict[str, Any], source_deck_id: str, *, why: str
    ) -> None:
        """Keep the delete of an unused backup copy for later (Q77), in the database so it outlives
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
            expect=self._backups_made.pop(backup_id, None),
        )
        progress["backup_note"] = BACKUP_LATER
        self._audit(
            "backup_delete_queued",
            sub=sub,
            detail={"proposal_id": proposal_id, "backup_deck_id": backup_id, "error": why, "due": due},
        )

    async def backup_delete_loop(self) -> None:
        """Every BACKUP_DELETE_EVERY seconds, try at most one queued backup delete (Q77)."""
        while True:
            await asyncio.sleep(BACKUP_DELETE_EVERY)
            try:
                await self.delete_one_queued_backup()
            except Exception:  # never let one bad pass end the loop
                logger.exception("queued backup delete failed")

    async def delete_one_queued_backup(self) -> dict[str, Any] | None:
        """One pass of the later deletes (Q77): the one queued delete due first, across all
        members, never while the client is paused or writes are off. It runs the same checks as
        _drop_unused_backup, through the member's own link as a normal call (outside an apply its
        429 counts towards the breaker, Q62). Returns what it did, for the log and the tests."""
        if not self.settings.writes_enabled or self.client.pacer.paused():
            return None
        job = self.db.next_backup_delete(int(time.time()))
        if job is None:
            return None
        sub, sid = job["sub"], job["snapshot_id"]
        backup_id, deck_id, pid = job["backup_deck_id"], job["deck_id"], job["proposal_id"]
        snap = self.db.get_snapshot(sid, sub)
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
            # Review D3, L4: a relink drops the member's queued deletes in the same step as it
            # stores the new link, so "still queued" sees any relink, even one within the same
            # second as the old link (created_at is whole seconds); the link itself is compared too
            # (an unlink, or another account).
            return (
                not self.db.backup_delete_queued(sid) or _link_identity(self.db.get_link(sub) or {}) != linked
            )

        gone = False
        try:
            # Review D4: one read of the copy first; a 404 there means it is already gone.
            copy = await self._call(sub, self.client.get_deck, backup_id)
            if relinked():
                return {"relinked": sid}  # relinked meanwhile: the relink dropped this delete (D3)
            # Review L4: only a copy the linked account owns, by the Archidekt user id stored on
            # the link; a missing id on either side keeps the copy.
            user_id = str(link.get("archidekt_user_id") or "")
            if not user_id or not copy.owner_id or str(copy.owner_id) != user_id:
                return self._give_up_backup_delete(job, attempts, "not_owner")
            if not _backup_as_made(copy, job):
                return self._give_up_backup_delete(job, attempts, "copy_changed")
            await self._call(sub, self.client.delete_deck, backup_id)
        except DeckError as exc:
            if relinked():
                return {"relinked": sid}  # an answer for another link proves nothing (D3)
            if exc.kind == "not_found":  # already gone from Archidekt: nothing left to do
                gone = True
            elif exc.kind in BACKUP_DELETE_TRANSIENT and attempts < BACKUP_DELETE_TRIES:
                return self._retry_backup_delete(job, attempts, exc.kind)
            else:
                return self._give_up_backup_delete(job, attempts, exc.kind)
        except Exception:
            # Review D5: an unexpected error is retried on the same schedule, never left due
            # (where it would hold up every other queued delete), and counts towards the limit.
            logger.exception("queued backup delete %s failed unexpectedly", sid)
            if attempts < BACKUP_DELETE_TRIES:
                return self._retry_backup_delete(job, attempts, "internal")
            return self._give_up_backup_delete(job, attempts, "internal")
        self.db.drop_backup_delete(sid)
        self.db.clear_snapshot_backup(sid)
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
        and at least BACKUP_DELETE_BACKOFF seconds, doubling with each failed try (Q77)."""
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
        """Stop trying to delete a queued backup copy (Q77): the copy stays in the backup folder,
        the proposal's message says so again, and the audit says why."""
        sid, sub, pid = job["snapshot_id"], job["sub"], job["proposal_id"]
        self.db.drop_backup_delete(sid)
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
        """Review D3: a queued delete does not record which Archidekt account made the copy (Q84
        defers that), so after any relink an answer to it could be the other account's. The
        member's queued deletes are given up; the copies stay in the backup folder."""
        for job in self.db.queued_backup_deletes():
            if job.get("sub") == sub:
                self._give_up_backup_delete(job, int(job.get("attempts") or 0), "relinked")

    def _reword_backup_note(self, sub: str, proposal_id: str, snapshot_id: str, note: str | None) -> None:
        """A queued delete ended (Q77): the proposal's stored result, if it is still the one from the
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

    async def _backup_on_archidekt(
        self, sub: str, row: dict[str, Any], deck: Deck, snapshot_id: str
    ) -> dict[str, Any]:
        """Before any write, keep a private copy of the deck in the user's backup folder on
        Archidekt (a readable backup next to the gateway's own snapshot). When the copy cannot be
        made the edit does not proceed: the proposal goes back to pending so the user can retry."""
        if not self.settings.archidekt_backups:
            return {}
        if row.get("archidekt_backup") is False:
            return {"archidekt_backup": "skipped"}  # the member's own choice at save time (D-02)
        reason = f"before proposal {row['id']} changed this deck ({row.get('kind') or 'edit'})"
        try:
            return await self._backup_copy(sub, deck, snapshot_id, reason=reason)
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

    async def _backup_copy(self, sub: str, deck: Deck, snapshot_id: str, *, reason: str) -> dict[str, Any]:
        """Copy ``deck`` into the backup folder and record the copy on the snapshot. Raises the
        Archidekt error as a DeckError; callers decide what that means for their write."""
        name = backup_name(deck.name, time.time())[:200]
        description = (
            f"Automatic backup made by the MTG Assistant Gateway {reason}. Gateway snapshot "
            f"{snapshot_id}. To undo the change, ask the assistant to restore snapshot {snapshot_id}, or "
            f"copy this deck back by hand. Original deck: https://archidekt.com/decks/{deck.id}"
        )
        folder_name = self.settings.archidekt_backup_folder
        with backup_writes():  # the backup copy is not a change to the deck
            folder = await self._call(
                sub,
                lambda token, *_: self.client.ensure_folder(token, name=folder_name),
            )
            # Review L2: how long the gateway's own writes to the copy took (the copy POST and its
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
        self.db.set_snapshot_backup(snapshot_id, **backup)
        self._backups_made[str(copy["id"])] = {
            "name": name,
            "description": description,
            "folder_id": str(folder["id"]),
            "written_within": written_within,
        }
        while len(self._backups_made) > BACKUPS_REMEMBERED:
            self._backups_made.pop(next(iter(self._backups_made)))
        return backup

    COLLECTION_TARGET = "collection"
    COLLECTION_NAME = "Your Archidekt collection"

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

    async def propose_action(self, sub: str, action: str, **params: Any) -> dict[str, Any]:
        """A proposal (kind ``action``) for one Archidekt account action: actions.py."""
        from . import actions

        return await actions.propose(self, sub, action, params)

    async def propose_clone(self, sub: str, deck_id: str, name: str | None = None) -> dict[str, Any]:
        """A proposal (kind ``clone``) that copies any deck the member can read (their own, a
        public deck, a precon) into a new private deck in their root folder, the way Archidekt's
        Clone deck button does ("Copy of - " prefix by default). Applied with the same copy route
        the backups use (verified live 2026-10-05 on the member's own deck; a copy of someone
        else's public deck is the same request and has not been exercised live); the copy keeps
        every card, quantity, category and finish."""
        deck_id = _clean_deck_id(deck_id)
        self._room_for_proposal(sub)
        deck = await self.get_any_deck(sub, deck_id)
        new_name = clean_text(name or "") or f"Copy of - {deck.name}"
        if len(new_name) > 200:
            raise DeckError("invalid", "name must be up to 200 characters")
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
                "changes": {"name": new_name},
            },
            [_row("clone", name=new_name, source=deck.name, cards=cards)],
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
            raise DeckError(
                "stale",
                "The deck changed on Archidekt since this proposal was made. Nothing was sent. "
                "Create a new proposal from the current deck.",
            )
        name = clean_text(str((row.get("changes") or {}).get("name") or "")) or f"Copy of - {deck.name}"
        copy = await self._call(sub, lambda token, *_: self.client.copy_deck(token, deck, name=name))
        # Recorded at once, as _apply_create records its new deck (review D1): a stop from here on
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
        snapshot_id = self._take_snapshot(sub, row, deck)
        progress["snapshot_id"] = snapshot_id
        backup = await self._backup_on_archidekt(sub, row, deck, snapshot_id)
        progress.update(backup)
        await self._rows_unchanged(sub, deck)  # the backup takes a while: check again before writing
        if payload:
            await self._send(sub, deck.id, payload, progress)
        # The deck's own details as the snapshot recorded them (name, description, format, bracket,
        # private, unlisted), re-derived from the current deck so a detail changed back by hand
        # since the proposal is not sent again.
        details, _lines = details_rows(deck, snapshot_details(wanted))
        if details:
            await _writing(
                progress, self._call(sub, self.client.update_deck, deck.id, details_payload(details))
            )
        verified = await self.get_deck(sub, deck.id)
        _left, mismatches = restore_steps(wanted, verified)
        still, _lines = details_rows(verified, snapshot_details(wanted)) if details else ({}, [])
        mismatches += [f"{k} (deck detail)" for k in sorted(still)]
        result = {
            "snapshot_id": snapshot_id,
            **backup,
            "restored_snapshot_id": snap["id"],
            "sent_entries": len(payload),
            "restored_details": sorted(details),
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
        progress["resolved_cards"], progress["of_cards"] = 0, len(cards)
        for c in cards:  # resolve every printing before creating anything
            card = await self._call(
                sub,
                lambda token, *_, c=c: self.client.resolve_card(
                    token,
                    c["name"],
                    set_code=c.get("set_code") or None,
                    collector_number=c.get("collector_number") or None,
                ),
            )
            resolve[row_key(c)] = card["id"]
            progress["resolved_cards"] += 1
            finish = str(c.get("finish") or ("Foil" if c.get("foil") else ""))
            modifiers[row_key(c)] = finish_modifier(
                card["options"], foil=finish == "Foil", etched=finish == "Etched"
            )
            if c.get("set_code") and not card["exact_printing"]:
                # a list's printing that Archidekt does not have is never swapped quietly
                wanted = f"{c['set_code'].upper()} {c.get('collector_number') or ''}".strip()
                got = (
                    f"{card['set_code'].upper()} {card['collector_number']}".strip() or "its default printing"
                )
                printing_notes.append(f"{c['name']}: {wanted} not found on Archidekt, added {got} instead")
        created: Deck = await self._call(
            sub,
            lambda token, *_: self.client.create_deck(
                token,
                name=spec["name"],
                format_id=FORMAT_IDS[spec["format"]],
                private=bool(spec.get("private", True)),
            ),
        )
        self.db.finish_proposal(row["id"], state="applying", deck_id=created.id)
        progress.update(deck_id=created.id, deck_url=f"https://archidekt.com/decks/{created.id}")
        entries = new_deck_entries(cards, resolve, modifiers)
        await self._send(sub, created.id, entries, progress)
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
        result = {
            "deck_id": created.id,
            "deck_url": f"https://archidekt.com/decks/{created.id}",
            "printing_notes": printing_notes,
            "sent_entries": len(entries),
            "verified": not mismatches,
            "mismatched_cards": mismatches,
        }
        if mismatches:
            progress.update(result)  # apply() records it with the error
            raise DeckError(
                "verify_mismatch",
                f"The deck was created (id {created.id}) but these cards do not match the proposal: "
                + ", ".join(mismatches),
            )
        self.db.finish_proposal(row["id"], state="applied", result=result)
        return self.describe(sub, row["id"])

    def reject(self, sub: str, proposal_id: str, *, via: str | None = None) -> dict[str, Any]:
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
        if not self.db.reject_proposal(proposal_id, sub, closed):
            # Lost a race with an apply, or the proposal has expired: report the state as it is now.
            raise self._not_pending(sub, proposal_id, "rejected")
        self._audit(
            "proposal_rejected", sub=sub, detail={"proposal_id": proposal_id, "deck_id": row["deck_id"]}
        )
        return self.describe(sub, proposal_id)

    def snapshot(self, sub: str, snapshot_id: str) -> dict[str, Any]:
        row = self.db.get_snapshot(snapshot_id, sub)
        if row is None:
            raise DeckError("not_found", "No such snapshot for your account.")
        return row

    def list_snapshots(self, sub: str, **narrow: Any) -> list[dict[str, Any]]:
        """Newest first; ``narrow`` is passed to the database (deck_id, search, limit, offset)."""
        return self.db.list_snapshots(sub, **narrow)

    async def propose_restore(self, sub: str, snapshot_id: str) -> dict[str, Any]:
        """A proposal that puts the deck back to what a snapshot recorded, relation by relation:
        the same printings, finishes (foil, etched), categories (so the commander, sideboard and
        maybeboard too) and quantities. It is an ordinary proposal: reviewed, confirmed and
        applied like any other (which takes a fresh snapshot first), so a restore can itself be
        undone. The deck's own details (name, description, format, bracket, private, unlisted)
        go back too when the snapshot recorded them differently. Not restored: the deck's own
        category definitions (a custom category's 'counts toward the deck' setting)."""
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
        payload, lines = restore_rows(wanted, deck)
        details, detail_lines = details_rows(deck, snapshot_details(wanted))
        lines += detail_lines
        if not payload and not details:
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
            [_row("restore", snapshot_id=snap["id"], taken=taken, rows=len(payload)), *lines],
        )
        self._audit(
            "proposal_created",
            sub=sub,
            detail={"proposal_id": pid, "deck_id": deck.id, "restores_snapshot": snap["id"]},
        )
        return self.describe(sub, pid)

    # -- deck details -----------------------------------------------------------
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
        changes, rows = details_rows(deck, wanted)
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
                # the gateway's backup folder is never a place to move a deck to (Q21)
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
        return plan, rows

    async def _apply_organise(
        self, sub: str, deck_id: str, plan: dict[str, Any], progress: dict[str, Any]
    ) -> list[str]:
        """Carry out a details proposal's organisation plan with the same verified calls as the
        deck page's own buttons; each re-reads the deck. Returns the fields done. Each step marks
        ``deck_written`` as soon as its write call returns or fails in a way that may still have
        landed (review M5), not after its re-read: a step refused before it wrote anything (an
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
        return done

    async def _apply_details(self, sub: str, row: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        stored = dict(row["changes"])
        organise = stored.pop("organise", None) or {}
        wanted = parse_details(stored) if stored else {}
        deck = await self._current_deck_for(sub, row)
        changes, _lines = details_diff(deck, wanted)
        if not changes and not organise:
            raise DeckError("invalid", "The deck already has these details; nothing was sent.")
        snapshot_id = self._take_snapshot(sub, row, deck)
        progress["snapshot_id"] = snapshot_id
        backup = await self._backup_on_archidekt(sub, row, deck, snapshot_id)
        progress.update(backup)
        # The backup takes a while: check again before writing, like card edits do. The details
        # are compared rather than the fingerprint, which the backup copy may bump.
        now = await self.get_own_deck(sub, deck.id)
        if any(_current_detail(now, key) != _current_detail(deck, key) for key in DETAIL_FIELDS):
            raise DeckError(
                "stale",
                "The deck's details changed on Archidekt while this change was being prepared. "
                "Nothing was sent. Create a new proposal from the current deck.",
            )
        if changes:
            await _writing(
                progress, self._call(sub, self.client.update_deck, deck.id, details_payload(changes))
            )
        verified = await self.get_deck(sub, deck.id)
        still, _lines = details_diff(verified, wanted)
        mismatches = sorted(still)
        organised: list[str] = []
        if organise and not mismatches:
            try:
                organised = await self._apply_organise(sub, deck.id, organise, progress)
            except DeckError as exc:
                progress.update({"snapshot_id": snapshot_id, **backup, "fields": sorted(changes)})
                # Only a proposal with other details had them written (review N3): a tags-only
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

    def _hand_snapshot(self, sub: str, deck: Deck) -> str:
        snapshot_id = secrets.token_urlsafe(12)
        if not self.db.save_snapshot(
            snapshot_id,
            owner_sub=sub,
            deck_id=deck.id,
            proposal_id=None,
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
            raise DeckError(
                "writes_disabled",
                "Archidekt writes are switched off on this gateway (MTG_WRITES_ENABLED is false); "
                "nothing was sent to Archidekt.",
            )

    async def delete_deck(
        self, sub: str, deck_id: str, typed_name: str, *, progress: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Delete one of the member's own decks after they typed its exact name. A snapshot is kept
        in the gateway and, when backups are on, a copy in the backup folder on Archidekt; the
        deletion is verified by reading the deck back (it must be gone). ``progress``: a
        delete-deck action's apply, which records the snapshot and the copy in it as every apply
        does (review L1), so an apply that stops before the deck is deleted deletes the unused
        copy or queues its delete (Q77) instead of leaving it unrecorded."""
        self._writes_switch()
        deck = await self.get_own_deck(sub, deck_id)
        if clean_text(typed_name).casefold() != clean_text(deck.name).casefold():
            raise DeckError(
                "invalid", "The name you typed does not match the deck's name. Nothing was deleted."
            )
        snapshot_id = self._hand_snapshot(sub, deck)
        if progress is not None:
            progress["snapshot_id"] = snapshot_id
        backup: dict[str, Any] = {}
        if self.settings.archidekt_backups:
            try:
                backup = await self._backup_copy(sub, deck, snapshot_id, reason="before it was deleted")
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
        backup folder and everything inside it (``visible_folders`` leaves them out, Q21); the
        checks before a folder write still see them."""
        tree = await self._call(sub, self.client.folder_tree)
        return self._flatten_folders(tree)

    async def folder_view(self, sub: str, *, wait: float | None = None) -> dict[str, Any] | None:
        """``folders`` from the member cache (fresh for DECK_LIST_FRESH, then refreshed in the
        background like the deck list), for My decks. None when the tree is cold and ``wait``
        ran out; the read carries on and lands in the cache."""

        async def fetch() -> dict[str, Any]:
            return await self._call(sub, self.client.folder_tree)

        tree = await self.folder_trees.get(sub, fetch, wait=wait)
        return None if tree is None else self._flatten_folders(tree)

    def _is_backup_folder(self, name: str, depth: int) -> bool:
        """The gateway's backup folder: the folder the deck list treats as it (its exact name, as
        archidekt.list_decks compares), or the one ensure_folder would reuse (the same name in
        other capitals, directly under the top level). Also while backups are switched off: the
        folder never shows (Q21), and a deck in it is a backup copy either way."""
        wanted = self.settings.archidekt_backup_folder
        if not wanted:
            return False
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
        if found.get("backup"):  # no new folder in the gateway's backup folder (Q21)
            raise _no_such_folder(info, found["name"])
        self._not_the_backup_name(name, found["depth"] + 1)
        if any(f["parent"] == parent and f["name"].casefold() == name.casefold() for f in info["folders"]):
            raise DeckError("invalid", f"There is already a folder called '{name}' there.")
        return name, found

    def _not_the_backup_name(self, name: str, depth: int) -> None:
        """Refuse a folder name that would make the gateway take that folder for its backup folder
        (hidden from My decks, its decks listed as backup copies, Q21): the same test the folder
        list uses, _is_backup_folder (B8 a)."""
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
        if folder.get("backup"):  # the gateway's backup folder and what is in it (Q21)
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
        if hidden is not None:  # never into the gateway's backup folder (Q21), as by name
            raise _no_such_folder(info, hidden["name"])
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
                raise DeckError(exc.kind, str(exc)) from exc
        self._precon_cache = (now, listing)
        return listing


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
            # plain text only (Q70): a Quill document an assistant sends is kept as its words alone
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
        else:  # private, unlisted
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
    return getattr(deck, key)


def _detail_label(key: str, value: Any) -> str:
    if key == "name":
        return json.dumps(value, ensure_ascii=False)
    if key in ("private", "unlisted"):
        return "yes" if value else "no"
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

    Only a Commander deck has a bracket, as on archidekt.com (Q56): a bracket for a deck of
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
        if key == "description":
            # Archidekt keeps a Quill document (see rich_doc): the texts are compared, and the row
            # shows the very text the write sends (written_text), never a reading of it
            current, after = rich_text(deck.description), written_text(value)
            if after.strip() == current.strip():
                continue
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
        else:
            lines.append(
                _row(
                    "detail",
                    field=key,
                    before=_detail_label(key, _current_detail(deck, key)),
                    after=_detail_label(key, value),
                )
            )
    return changes, lines


def snapshot_details(snapshot: Deck) -> dict[str, Any]:
    """The deck details a snapshot recorded, in the shape ``details_rows`` compares: name,
    description, format (as its FORMAT_IDS key, when known), bracket, private and unlisted. The
    description is Archidekt's own stored document, so it goes back with its formatting (Q70).
    The bracket is left out when the snapshot's deck was not a Commander deck: an older release
    could set one on any format, and restoring such a deck must not meet the Q56 refusal (review
    N2); moving the deck off Commander still clears a bracket it has now (``details_rows``)."""
    out: dict[str, Any] = {
        "name": snapshot.name,
        "description": QuillDoc(snapshot.description or ""),
        "private": bool(snapshot.private),
        "unlisted": bool(snapshot.unlisted),
    }
    if snapshot.format_id == FORMAT_IDS["commander"]:
        out["edh_bracket"] = snapshot.edh_bracket
    if snapshot.format in FORMAT_IDS:
        out["deck_format"] = snapshot.format
    return out


def details_payload(changes: dict[str, Any]) -> dict[str, Any]:
    """The PATCH /decks/{id}/update/ body for validated detail changes (Archidekt's field names)."""
    out: dict[str, Any] = {}
    for key, value in changes.items():
        out[DETAIL_FIELDS[key]] = FORMAT_IDS[value] if key == "deck_format" else value
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
    return out


def _printing_mismatches(verified: Deck, specs: list[dict[str, Any]]) -> list[str]:
    """Names whose rows on the re-read deck do not carry the finish or printing a spec asked for."""
    out: list[str] = []
    for spec in specs:
        rows = [c for c in verified.main_cards if c.name.lower() == spec["name"].lower()]
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
            ok = bool(rows) and all((c.modifier or "Normal") == spec["modifier"] for c in rows)
        if not ok:
            out.append(spec["name"])
    return out


def _shown_folders(info: dict[str, Any]) -> list[dict[str, Any]]:
    """The member's folders below the top level that a deck can be put in: all but the
    gateway's backup folder and what is inside it (Q21)."""
    return [f for f in info["folders"] if f["depth"] > 0 and not f.get("backup")]


def _no_such_folder(info: dict[str, Any], name: Any, *, several: bool = False) -> DeckError:
    """The refusal of a folder that is not one to use: none (or, with ``several``, more than one)
    of the shown folders has that name; the gateway's backup folder is refused with it too."""
    names = ", ".join(sorted({f["name"] for f in _shown_folders(info)})[:30])
    what = "More than one folder" if several else "No folder"
    return DeckError("invalid", f"{what} is called '{name}'. Your folders: {names}")


def _category_mismatches(verified: Deck, want: dict[int, list[str]]) -> list[str]:
    """Names of rows whose categories on the re-read deck are not what ``category_plan`` asked
    for (a row that vanished counts too). The first category is the row's primary one (Set as
    commander puts Commander there, K10), so it must match too; the others in any order."""
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
    pause refused it, review M1) changed nothing, whatever its kind. ``progress`` None (a hand
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
    """Which link a queued backup delete started with (review D3, L4): when it was made and the
    Archidekt account it names. A token refresh changes none of these."""
    return (link.get("created_at"), link.get("archidekt_user_id"), link.get("archidekt_username"))


def _iso_time(value: str) -> float | None:
    """Seconds since the epoch for an ISO 8601 time as Archidekt writes it ("...Z" or another
    offset, with or without fractions), or None when it cannot be read. A time with no zone is
    unreadable (review L3): which clock it is on is not known."""
    try:
        when = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None
    if when.tzinfo is None:
        return None
    return when.timestamp()


def _backup_as_made(copy: Deck, job: dict[str, Any]) -> bool:
    """Whether a queued backup copy is still as the gateway made it (review D4): in the backup
    folder it was made in, with the name and description it was given, and with Archidekt's
    updatedAt no later than the copy's own createdAt plus the time the gateway's writes to it
    took plus BACKUP_UNCHANGED_SLACK (review L2). Anything missing or unreadable counts as
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


def _changed_anything(progress: dict[str, Any]) -> bool:
    """Whether an apply may have changed anything on Archidekt, by its progress or by the
    proposal's stored result (which holds it): deck rows (or collection records) sent, a deck
    created, or any write that landed or may have. The one definition of "changed nothing" for
    the apply-stopped messages, the unused backup copy and putting a proposal back to pending
    (Q78). It fails closed: besides the writes the apply paths mark themselves (``_writing``), the
    client marks every write sent inside an apply (``archidekt.apply_writes``, review D1, D2). A
    backup copy made in the backup folder is not a change to the deck."""
    return bool(progress.get("sent_entries") or progress.get("deck_id") or progress.get("deck_written"))


def _sentence(exc: Exception) -> str:
    """An error's message as a whole sentence, so the note after it starts a new one
    ("Archidekt returned HTTP 503. 1 of 3 ...", never "HTTP 503 1 of 3")."""
    text = str(exc).rstrip()
    return text if not text or text[-1] in ".!?" else text + "."


def _rate_limited_note(
    progress: dict[str, Any], *, pending: bool = False, collection: bool = False, clone: bool = False
) -> str:
    """What an apply that ran out of patience with Archidekt's 429s had changed, and what to do
    (Q41, Q66): added to PATIENCE_SPENT in the message the member sees. ``progress`` is the apply's
    progress, or the proposal's stored result (which holds it): get_proposal's next_step is built
    from the same note, so the two always agree (review N1). A backup copy whose delete was
    refused is said to stay in the backup folder, or to be deleted later (review N4, Q77).
    ``pending``: nothing changed, so the proposal is pending again (Q78). ``collection``: a
    collection proposal, whose target is "your collection". ``clone``: a copy of a deck, which
    has nothing left to try once the copy is made (review D1)."""
    if clone and progress.get("deck_id"):
        return _partial_note(progress, clone=True)
    if progress.get("sent_entries") or progress.get("deck_id"):
        note = _partial_note(progress, collection=collection)
        return f"{note} Try the rest again in a few minutes with a new proposal."
    if progress.get("deck_written"):
        where = "your collection" if collection else "the deck"
        return (
            f"Part of this change had already reached Archidekt, so check {where}. Try the rest again "
            "in a few minutes with a new proposal."
        )
    kept = f" {progress['backup_note']}" if progress.get("backup_note") else ""
    if pending:
        return (
            f"Nothing was changed on Archidekt.{kept} The proposal is still pending, so it can be "
            "approved again in a few minutes."
        )
    return f"Nothing was changed on Archidekt.{kept} Try again in a few minutes with a new proposal."


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
    copy of a deck, made whole by one request, so once it exists it holds the cards (review D1)."""
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
    specs. A finish a printing change moved on its own (P6) is not compared."""
    out: list[str] = []
    for ch in changes:
        if ch.action not in ("set_printing", "set_finish") or ch.zone == "side":
            continue
        face = front_face(ch.card_name).casefold()
        rows = [c for c in now.main_cards if front_face(c.name).casefold() == face]
        if not rows:
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
            raise DeckError("invalid", "only archidekt.com deck links are accepted") from None
        if host.lower() not in ("archidekt.com", "www.archidekt.com"):
            raise DeckError("invalid", "only archidekt.com deck links are accepted")
    parts = [p for p in raw.split("/") if p]
    if "decks" in parts and parts.index("decks") + 1 < len(parts):
        raw = parts[parts.index("decks") + 1]
    elif parts:
        raw = parts[-1]
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    if not (raw.isascii() and raw.isdigit()) or len(raw) > 12:
        raise DeckError("invalid", NOT_A_DECK_ID)
    return raw


def _needs_user_message(mode: str, why: str) -> str:
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
) -> str:
    """What the assistant should do next with a proposal in ``state``. ``rate_limited``: for an
    apply that stopped on Archidekt's waits, the same note its stored message ends with
    (``_rate_limited_note``), so the two never disagree (review N1)."""
    what = "these details" if kind == "details" else "this copy" if kind == "clone" else "this diff"
    # With the in-chat card (approve.py) the person decides on the card where the app shows one.
    card = (
        "If your app shows this proposal as a card with Approve and Reject buttons, the user decides "
        "there and the gateway tells you the outcome; do not apply it yourself. Otherwise, show"
        if in_chat
        else "Show"
    )
    if state == "pending" and not writes_enabled:
        return "Review only: deck writes are disabled on this gateway, so this cannot be applied yet."
    if state == "pending" and rate_limited:
        # Q78: the last apply stopped on Archidekt's waits before it changed anything. No "call
        # apply_proposal now", even when the member's mode lets the assistant apply: not yet.
        return rate_limited
    if state == "pending" and may_apply:
        chose = (
            "to apply deck and collection changes without asking"
            if mode == "auto"
            else f"to apply low-risk edits without asking, and this one is low risk ({risk_reason})"
        )
        return (
            f"The user chose {chose}: tell them {what} in one line and call apply_proposal now. "
            "They can also press Apply on the review page."
        )
    if state == "pending" and risk in modes.MEMBER_ONLY:
        return (
            f"Show the user {what} and the review link: this acts on their Archidekt account, so "
            "they approve it themselves on the review page, in every approval mode (the in-chat "
            "card only opens that page). Do not call apply_proposal."
        )
    if state == "pending" and mode == "semi":
        return (
            f"This proposal is high risk (it {risk_reason}), so the user's semi-automatic mode needs "
            f"their own press. {card} the user {what} and the review link: they confirm on the "
            "card or the review page. Do not call apply_proposal."
        )
    if state == "pending":
        return (
            f"{card} the user {what} and the review link: they confirm on the review page (Apply). "
            "Do not call apply_proposal: the user's approval mode asks them every time."
        )
    if state == "applying":
        return (
            "Archidekt is still being updated (progress shows how far it got; a large change takes a "
            "few minutes because the gateway paces its requests). Tell the user it is under way and "
            "check again with get_proposal in a minute. Do not apply it again or make a new proposal."
        )
    if state == "applied" and kind == "details":
        return "Done. The deck's details on Archidekt match this proposal."
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
                # get_deck's rows say in_deck: false for a maybeboard or sideboard row
                **({"in_deck": False} if row.get("in_deck") is False else {}),
            }
        )
    return out


SIDE_CATEGORIES = ("Maybeboard", "Sideboard")


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
