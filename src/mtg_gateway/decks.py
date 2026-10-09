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
import dataclasses
import json
import logging
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
    ArchidektClient,
    ArchidektError,
    Deck,
    art_url,
    finish_modifier,
    front_face,
    jwt_exp,
    parse_deck,
)
from .archidekt_csv import CsvError, parse_export
from .config import Settings
from .db import Database
from .decklist import DecklistError, ListCard, clean_category, clean_text, parse_decklist, to_text
from .timing import add_time, archidekt_time

logger = logging.getLogger(__name__)

ACTIONS = ("add", "remove", "set_quantity", "set_category", "set_commander", "set_finish", "set_printing")
COUNT_ACTIONS = ("add", "remove", "set_quantity")
CATEGORY_ACTIONS = ("set_category", "set_commander")
# Changes to how a card already in the deck is printed: its finish, or the printing itself.
PRINTING_ACTIONS = ("set_finish", "set_printing")
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
# How long a page waits for a cold deck list before it renders with a placeholder the browser
# fills from /api/decks/mine (the fetch carries on and lands in the cache either way).
DECK_LIST_COLD_WAIT = 1.5
TOUCH_LINK_INTERVAL = 60.0  # the link's last_used_at is written at most this often per member
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


class MemberCache:
    """One value per member with stale-while-revalidate: a fresh value is served as is, a stale
    one (older than ``fresh`` seconds, younger than ``stale``) is served at once while one refresh
    runs in the background, and anything older (or missing) is fetched, every waiter sharing the
    one fetch in flight. ``drop`` forgets a member's value and makes a fetch that was already in
    flight store nothing, so a write racing a read never leaves the old list behind."""

    def __init__(self, fresh: float, stale: float):
        self.fresh = fresh
        self.stale = stale
        self._entries: dict[str, tuple[float, float, Any]] = {}  # sub -> (monotonic, wall, value)
        self._refreshing: dict[str, asyncio.Task[Any]] = {}
        self._dropped: dict[str, float] = {}

    def peek(self, sub: str) -> tuple[Any, str]:
        """(value, state) where state is fresh, stale or miss (value None)."""
        hit = self._entries.get(sub)
        if hit is None:
            return None, "miss"
        age = time.monotonic() - hit[0]
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
        self._dropped[sub] = time.monotonic()

    def clear(self) -> None:
        for sub in list(self._entries):
            self.drop(sub)

    def refreshing(self, sub: str) -> bool:
        return sub in self._refreshing

    def _refresh(self, sub: str, fetch: Callable[[], Awaitable[Any]]) -> asyncio.Task[Any]:
        task = self._refreshing.get(sub)
        if task is not None:
            return task
        started = time.monotonic()

        async def run() -> Any:
            value = await fetch()
            if self._dropped.get(sub, 0.0) <= started:  # not invalidated while it was fetched
                self._entries[sub] = (time.monotonic(), time.time(), value)
            return value

        def done(t: asyncio.Task[Any]) -> None:
            self._refreshing.pop(sub, None)
            if not t.cancelled() and t.exception() is not None:
                logger.debug("member cache refresh failed for a member: %r", t.exception())

        task = asyncio.create_task(run())
        task.add_done_callback(done)
        self._refreshing[sub] = task
        if len(self._entries) > 10_000:
            self._entries.clear()
            self._dropped.clear()
        return task

    async def get(
        self, sub: str, fetch: Callable[[], Awaitable[Any]], *, wait: float | None = None
    ) -> Any | None:
        """The member's value: fresh at once; stale at once with a refresh in the background;
        otherwise fetched. With ``wait``, a fetch that takes longer answers None instead and
        carries on, so the caller can render a placeholder and come back for the value."""
        value, state = self.peek(sub)
        if state == "fresh":
            return value
        if state == "stale":
            self._refresh(sub, fetch)
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
        for task in list(self._refreshing.values()):
            task.cancel()
        await asyncio.gather(*self._refreshing.values(), return_exceptions=True)


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
    if kind == "printing":
        return f"{r['name']}: printing {r['before']} -> {r['after']}"
    if kind == "clone":
        return f"Clone '{r['source']}' ({r['cards']} cards) as '{r['name']}'"
    if kind == "commander":
        return f"Commander: {r['before']} -> {r['after']}"
    if kind == "new_deck":
        visibility = "private" if r.get("private") else "public"
        return f"New {r['format']} deck '{r['name']}' ({r['cards']} cards, {visibility})"
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
        return f"{r['field']}: {r['before']} -> {r['after']}"
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


class DeckError(Exception):
    """A user-facing failure: the message is safe to show as is. ``extra`` holds structured
    fields the tool reply carries alongside ``error`` and ``message``."""

    def __init__(self, kind: str, message: str, **extra: Any):
        super().__init__(message)
        self.kind = kind
        self.extra = extra


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
    # "main" (the deck proper) or "side": the maybeboard and sideboard rows, which count
    # separately. A count or category change names the zone its rows are in.
    zone: str = "main"

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
        if self.set_code:
            d["set_code"] = self.set_code
        if self.collector_number:
            d["collector_number"] = self.collector_number
        if self.finish:
            d["finish"] = self.finish
        if self.zone == "side":
            d["zone"] = "side"
        return d


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
            if zone == "side":
                raise DeckError("invalid", f"change {i}: {action} works on the deck proper, not zone side")
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
        out.append(Change(action, name, qty, cat, set_code, number, finish, zone=zone))
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
        raise DeckError("invalid", "one set_finish or set_printing per card name per proposal")
    counted = {key(ch) for ch in out if ch.action in COUNT_ACTIONS}
    clash = sorted((set(categorised) | set(reprinted)) & counted)
    if clash:
        raise DeckError(
            "invalid",
            "a card cannot be added, removed or recounted and recategorised or reprinted in the same "
            "proposal: " + ", ".join(clash),
        )
    clash = sorted(set(categorised) & set(reprinted))
    if clash:
        raise DeckError(
            "invalid",
            "a card cannot be recategorised and reprinted in the same proposal: " + ", ".join(clash),
        )
    return out


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
    for c in deck.main_cards:
        by_name.setdefault(c.name.lower(), []).append(c)
        by_name.setdefault(front_face(c.name).lower(), []).append(c)
    for ch in changes:
        if ch.action not in PRINTING_ACTIONS:
            continue
        cards = list({c.relation_id: c for c in by_name.get(ch.card_name.lower(), [])}.values())
        if not cards:
            raise DeckError(
                "invalid", f"'{ch.card_name}' is not in the deck, so its printing cannot be changed"
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
                    "label": c.label,
                }
                for c in cards
            ],
        }
        finishes = sorted({r["finish"] for r in spec["rows"]})
        if ch.action == "set_finish":
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


def _parse_category_change(i: int, action: str, name: str, item: dict[str, Any]) -> Change:
    """``set_category`` ({action, card_name, category}) and ``set_commander`` ({action, card_name});
    neither takes a quantity or a printing."""
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
    if action == "set_commander":
        if cat and cat != COMMANDER:
            raise DeckError("invalid", f"change {i}: set_commander always files the card under Commander")
        return Change(action, name, category=COMMANDER)
    if not cat or len(cat) > MAX_CATEGORY:
        raise DeckError("invalid", f"change {i}: set_category needs a category (up to {MAX_CATEGORY} chars)")
    return Change(action, name, category=cat)


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

    ``set_category`` moves every row of the card to exactly that one category. ``set_commander``
    files the card under Commander and takes Commander off every other row that has it (keeping
    that row's other categories), so after the change the commanders are exactly the cards named
    by this proposal's set_commander changes (two for partners). Rows that already have the
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
        new = [ch.category or COMMANDER]
        if ch.action == "set_commander":
            new_commanders.append(rows[0].name)
        changed = [r for r in rows if sorted(r.categories) != new]
        for r in changed:
            want[r.relation_id] = new
        if ch.action == "set_category" and changed:
            old = sorted({", ".join(sorted(clean_text(c) for c in r.categories)) or "(none)" for r in rows})
            lines.append(
                _row(
                    "category",
                    name=rows[0].name,
                    before=" / ".join(old),
                    after=new[0],
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
            want[c.relation_id] = [cat for cat in c.categories if cat != COMMANDER]
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
            cats = {n: next((c.category for c in chs if c.category), None) for n, chs in adds_by_name.items()}
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
                        "companion": False,
                        "flippedDefault": False,
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
) -> list[dict[str, Any]]:
    """Per-card entries for PATCH modifyCards/v2, one entry per request.

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
                    _add_entry(pin["cardid"], int(pin["quantity"]), pin.get("categories"), pin["modifier"])
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
                    resolve[name], qty, (categories or {}).get(name), (modifiers or {}).get(name, "Normal")
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
                )
            )
    for card in deck.cards:
        if card.relation_id in (recategorise or {}):
            payload.append(_entry("modify", card, card.quantity, categories=recategorise[card.relation_id]))
    return payload


def _add_entry(cardid: int, qty: int, categories: list[str] | None, modifier: str) -> dict[str, Any]:
    return {
        "action": "add",
        "cardid": cardid,
        "patchId": uuid.uuid4().hex,
        "categories": list(categories or []),
        "modifications": {
            "quantity": qty,
            "companion": False,
            "flippedDefault": False,
            "modifier": modifier,
        },
    }


def _entry(action: str, card: Any, qty: int, categories: list[str] | None = None) -> dict[str, Any]:
    """One modifyCards entry for an existing row; ``categories`` replaces the row's own when given."""
    return {
        "action": action,
        "cardid": card.card_id,
        "deckRelationId": card.relation_id,
        "patchId": uuid.uuid4().hex,
        "categories": list(card.categories if categories is None else categories),
        "modifications": {
            "quantity": qty,
            "companion": False,
            "flippedDefault": False,
            "modifier": card.modifier or "Normal",
        },
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
        self.max_archidekt_per_user = MAX_ARCHIDEKT_PER_USER
        self.archidekt_budget = RateBudget(settings.archidekt_calls_per_10_min)
        # sub -> {matched card name (lower case): what the scan read}; set when scanning is on
        self.guessed_names: Callable[[str], dict[str, str]] | None = None
        # Set by collection.py: applies a ``collection`` proposal's stored changes against the
        # member's Archidekt Collection and returns the result rows (DeckService itself knows only
        # decks). None while the collection pages are not loaded.
        self.collection_apply: Any = None
        self._precon_cache: tuple[float, dict[str, list[dict[str, Any]]]] | None = None
        # The member's deck list (list_decks), served from memory between Archidekt reads and
        # dropped by every write the gateway sends for that member.
        self.deck_lists = MemberCache(DECK_LIST_FRESH, DECK_LIST_STALE)
        self.deck_list_wait = DECK_LIST_COLD_WAIT
        # Other member caches (the collection's first page) hear about writes here: (sub, path).
        self.write_hooks: list[Callable[[str, str], None]] = []
        listeners = getattr(self.client, "write_listeners", None)  # a test double may lack it
        if listeners is not None:
            listeners.append(self._on_archidekt_write)
        self._touched: dict[str, float] = {}  # sub -> when last_used_at was last written

    def _on_archidekt_write(self, path: str) -> None:
        """A write is about to go to Archidekt with some member's session: whatever the gateway
        remembers for that member about what the write changes is stale from now on."""
        sub = _acting_sub.get()
        if not sub:
            return
        if not path.startswith("/collection"):
            self.deck_lists.drop(sub)
        for hook in self.write_hooks:
            hook(sub, path)

    def forget_member(self, sub: str) -> None:
        """Drop everything cached for a member (their link changed or their data was deleted)."""
        self.deck_lists.drop(sub)
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
            raise DeckError("rate_limited", refused)
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
            "approval_mode_note": modes.MODE_HELP[mode] + " Change it on the account page.",
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

    async def _call_unslotted(self, sub: str, fn: Any, *args: Any) -> Any:
        """Run ``fn(token, *args)`` with the user's Archidekt session. The access token is
        refreshed ahead of time when its ``exp`` is unreadable or within five minutes, and once
        more if Archidekt still answers 401. The new pair is stored under the same link (same
        Fernet, same username and user id). The link is revoked only when no refresh token is
        stored, the refresh itself is rejected, or a freshly refreshed token is rejected too."""
        token, row = self._token(sub)
        refresh = (row.get("_secret") or {}).get("refresh")
        # The stored session this call works with. Every write below is conditional on the link
        # still holding exactly it, so a refresh or a failure that races an unlink or a relink
        # (possibly to another Archidekt account) never touches the newer link.
        stored = {"secret_enc": row["secret_enc"]}

        def expire(reason: str) -> DeckError:
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
                raise DeckError(exc.kind, str(exc)) from exc
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
            self._audit("archidekt_session_refreshed", sub=sub, detail={"reason": reason})
            return access

        exp = jwt_exp(token)
        if exp is None or exp - time.time() < 300:
            token = await refreshed("expiring")
        acting = _acting_sub.set(sub)
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
            raise DeckError(exc.kind, str(exc)) from exc
        finally:
            _acting_sub.reset(acting)
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

    async def list_decks_quick(self, sub: str, *, wait: float | None = None) -> list[dict[str, Any]] | None:
        """``list_decks`` that gives up waiting for a cold read after ``wait`` seconds and answers
        None; the read carries on and lands in the cache for /api/decks/mine. ``wait=None``
        waits for it. The list comes back as fresh dicts, so a caller may sort or add to it."""
        _token, row = self._token(sub)  # not linked: raise before touching the cache
        exclude = self.settings.archidekt_backup_folder if self.settings.archidekt_backups else None

        async def fetch() -> list[dict[str, Any]]:
            return await self._call(
                sub,
                lambda token, *_: self.client.list_decks(
                    token, row["archidekt_username"], row.get("archidekt_user_id"), exclude_folder=exclude
                ),
            )

        rows = await self.deck_lists.get(sub, fetch, wait=wait)
        return None if rows is None else [dict(d) for d in rows]

    def decks_fetched_at(self, sub: str) -> float | None:
        """When the cached deck list was read from Archidekt (epoch seconds), None when none is."""
        return self.deck_lists.fetched_at(sub)

    async def get_deck(self, sub: str, deck_id: str) -> Deck:
        deck_id = _clean_deck_id(deck_id)
        deck: Deck = await self._call(sub, self.client.get_deck, deck_id)
        return deck

    async def get_own_deck(self, sub: str, deck_id: str) -> Deck:
        """Fetch with the user's session and insist the linked account owns the deck."""
        deck = await self.get_deck(sub, deck_id)
        _token, link = self._token(sub)
        if not deck.owner:
            raise DeckError("contract", "Archidekt did not say who owns this deck, so it cannot be edited.")
        if deck.owner.lower() != str(link["archidekt_username"]).lower():
            raise DeckError(
                "forbidden",
                f"Deck {deck.id} belongs to {deck.owner}, not to your linked account. "
                "Use get_deck to read it.",
            )
        return deck

    async def get_any_deck(self, sub: str | None, deck_ref: str) -> Deck:
        """Read a deck. A member with a linked account reads with their session first, which is
        how Archidekt reports the deck as that person sees it (private decks, the copies they own
        of each card, their vote and bookmark); when that fails, or there is no link, the deck is
        read anonymously (public or unlisted)."""
        deck_id = _clean_deck_id(deck_ref)
        if sub and self.db.get_link(sub):
            try:
                return await self.get_deck(sub, deck_id)
            except DeckError as exc:
                if exc.kind not in ("not_found", "auth", "forbidden", "not_linked"):
                    raise
        async with self.archidekt_slot(sub):
            try:
                return await self.client.get_deck(None, deck_id)
            except ArchidektError as exc:
                raise DeckError(exc.kind, str(exc)) from exc

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
        total = sum(c["quantity"] for c in entries)
        rows = [_row("new_deck", name=name, format=fmt, cards=total, private=bool(private))]
        rows += [
            _row("add", name=c["name"], qty=c["quantity"], category=", ".join(c["categories"]) or None)
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
        return self.describe(sub, pid)

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
        may_apply = self.settings.writes_enabled and modes.assistant_may_apply(mode, risk)
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
            "next_step": (
                # an apply that stopped at the backup (possibly after its caller had its answer)
                "The last attempt to apply this stopped before anything changed: the backup copy "
                "could not be made on Archidekt. "
                if state == "pending" and (row["result"] or {}).get("error") == "backup_failed"
                else ""
            )
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
            ),
        }

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
                }
            )
        return out

    def approval_for(self, sub: str, row: dict[str, Any]) -> str:
        """The in-chat card's one-time code for a proposal (approve.py), bound to the proposal,
        its owner, the app that made it and its creation time."""
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
            raise DeckError("already_applied", "This proposal was already applied; nothing was sent again.")
        if archidekt_backup is False and via == "browser":
            row["archidekt_backup"] = False
        creator = row.get("created_by_client")
        # "mcp" is the assistant's own apply_proposal call: allowed only when this member's
        # approval mode (modes.py) lets the assistant apply a proposal of this risk itself.
        # "app" is the member's press on the in-chat card, gated by its one-time code
        # (approve.py); "browser" is the review page; "auto" is never a caller's choice.
        if via == "mcp":
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
                    _needs_user_message(mode, risk, why),
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
            raise DeckError("not_pending", f"This proposal is {row['state']} and cannot be applied.")
        lock = self._deck_locks.setdefault(f"{sub}:{row['deck_id']}", asyncio.Lock())
        # What the apply got done so far (snapshot, backup, entries sent of how many, the verify
        # result), kept in the proposal's result whichever way the apply ends, and shown by
        # describe() while it runs.
        progress: dict[str, Any] = {}
        self._progress[proposal_id] = progress
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
                self.db.finish_proposal(
                    proposal_id, state="failed", result={**progress, "error": exc.kind, "detail": str(exc)}
                )
                self._audit(
                    "proposal_failed",
                    sub=sub,
                    detail={"proposal_id": proposal_id, "error": exc.kind, **_sent(progress)},
                )
                if (
                    progress.get("sent_entries") or progress.get("deck_id")
                ) and exc.kind != "verify_mismatch":
                    raise DeckError(
                        exc.kind, f"{exc} {_partial_note(progress)}", **{**exc.extra, **_sent(progress)}
                    ) from exc
                raise
            except BaseException as exc:  # cancelled or unexpected: never leave a proposal in applying
                kind = "internal" if isinstance(exc, Exception) else "interrupted"
                self.db.finish_proposal(proposal_id, state="failed", result={**progress, "error": kind})
                self._audit(
                    "proposal_failed",
                    sub=sub,
                    detail={"proposal_id": proposal_id, "error": kind, **_sent(progress)},
                )
                if not isinstance(exc, Exception):
                    raise
                logger.exception("apply failed")
                raise DeckError(
                    "internal", f"Applying failed unexpectedly. {_partial_note(progress)}"
                ) from exc
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
            deck, after, resolve, new_cats, modifiers, pinned, recategorise, after_side, side_cats
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
        # The counts after the count changes, less what a category move takes out of the deck
        # proper (into the maybeboard) and plus what it brings in; the side counts the other way.
        expected = dict(after)
        expected_side = dict(after_side)
        for name, qty in leaving_deck(deck, recategorise).items():
            expected[name] = expected.get(name, 0) - qty
            expected_side[name] = expected_side.get(name, 0) + qty
        for name, qty in entering_deck(deck, recategorise).items():
            expected[name] = expected.get(name, 0) + qty
            expected_side[name] = expected_side.get(name, 0) - qty
        mismatches = _mismatches(verified.counts_by_name(), {n: q for n, q in expected.items() if q > 0})
        mismatches = sorted(
            set(mismatches)
            | set(
                _mismatches(verified.side_counts_by_name(), {n: q for n, q in expected_side.items() if q > 0})
            )
            | set(_category_mismatches(verified, recategorise))
            | set(_printing_mismatches(verified, specs))
        )
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
            side_cats[key] = [side_category_for(deck, chs[0])]
            if after_side.get(key, 0) > 0 and key.lower() not in have_side:
                card = await self._call(sub, self.client.resolve_card, key)
                resolve[key] = card["id"]
                finish = next((c.finish for c in chs if c.finish), None)
                if finish:
                    modifiers[key] = finish_modifier(
                        card["options"], foil=finish == "Foil", etched=finish == "Etched"
                    )
        for key, chs in adds.items():
            cat = next((c.category for c in chs if c.category), None)
            if cat:
                new_cats[key] = [cat]
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
                    if r.get("label"):
                        entry["modifications"]["label"] = r["label"]
                    out.append(entry)
                spec["modifier"] = next(iter(spec["modifiers"])) if len(spec["modifiers"]) == 1 else None
                for rid in spec["relation_ids"]:
                    out.append(_entry("remove", rows[rid], 0))
            else:
                spec["modifier"] = spec["finish"]
                for rid in spec["relation_ids"]:
                    entry = _entry("modify", rows[rid], rows[rid].quantity)
                    entry["modifications"]["modifier"] = spec["finish"]
                    out.append(entry)
        return out

    async def _send(
        self, sub: str, deck_id: str, payload: list[dict[str, Any]], progress: dict[str, Any]
    ) -> None:
        """One PATCH per entry, counting in ``progress`` how many went out, so an apply that
        fails partway says how far it got."""
        progress["sent_entries"], progress["of_entries"] = 0, len(payload)
        for entry in payload:
            await self._call(sub, self.client.modify_card, deck_id, entry)
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
        when = time.gmtime()
        name = f"{deck.name} (backup {time.strftime('%Y-%m-%d %H:%M UTC', when)})"[:200]
        description = (
            f"Automatic backup made by the MTG Assistant Gateway {reason}. Gateway snapshot "
            f"{snapshot_id}. To undo the change, ask the assistant to restore snapshot {snapshot_id}, or "
            f"copy this deck back by hand. Original deck: https://archidekt.com/decks/{deck.id}"
        )
        folder = await self._call(
            sub,
            lambda token, *_: self.client.ensure_folder(token, name=self.settings.archidekt_backup_folder),
        )
        copy = await self._call(
            sub,
            lambda token, *_: self.client.backup_deck(
                token, deck, name=name, folder_id=str(folder["id"]), description=description
            ),
        )
        backup = {"backup_deck_id": str(copy["id"]), "backup_url": str(copy.get("url") or "")}
        self.db.set_snapshot_backup(snapshot_id, **backup)
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
            await self._call(sub, self.client.update_deck, deck.id, details_payload(details))
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
        (the review page, or the API with the session cookie) any of their proposals; from a
        connected app (``via`` "mcp" or "api", or any non-browser client) only the proposals that
        app made itself, the same rule as apply."""
        row = self.db.get_proposal(proposal_id, sub)
        if row is None:
            raise DeckError("not_found", "No such proposal for your account.")
        actor = current_client.get()
        from_browser = actor == BROWSER_CLIENT and via in (None, "browser")
        creator = row.get("created_by_client")
        if not from_browser and (not creator or creator != actor):
            raise DeckError(
                "other_client",
                "This proposal was made by a different connected app (or in the browser), so it "
                "cannot be rejected from here. The user can reject it on the review page.",
                review_url=f"{self.settings.public_url}/proposals/{proposal_id}",
            )
        if not self.db.reject_proposal(proposal_id, sub):
            # Lost a race with an apply, or the proposal has expired: report the state as it is now.
            now_state = self.describe(sub, proposal_id)["state"]
            raise DeckError("not_pending", f"This proposal is {now_state}, so it cannot be rejected.")
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
        deck = await self.get_own_deck(sub, snap["deck_id"])
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
                hits = [f for f in info["folders"] if f["depth"] > 0 and f["name"].casefold() == want]
                if len(hits) != 1:
                    names = ", ".join(sorted({f["name"] for f in info["folders"] if f["depth"] > 0})[:30])
                    what = "No folder" if not hits else "More than one folder"
                    raise DeckError("invalid", f"{what} is called '{raw['folder']}'. Your folders: {names}")
                target = hits[0]
            current = deck.parent_folder if deck.parent_folder is not None else root
            if target["id"] != current:
                plan["folder_id"] = target["id"]
                before = by_id.get(current, {}).get("name", "unknown")
                rows.append(_row("detail", field="folder", before=before, after=target["name"]))
        on_deck = {str(r.get("name") or "").casefold(): r for r in deck.tag_relations}
        add = [self._tag_name(n) for n in _names(raw.get("add_tags"), "add_tags")]
        add = [n for n in dict.fromkeys(add) if n.casefold() not in on_deck]
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
                )
            )
        if "cover" in raw:
            pick = clean_text(str(raw["cover"] or "")).casefold()
            card = next((c for c in deck.cards if c.name.casefold() == pick and c.scryfall_uid), None)
            if card is None:
                raise DeckError("invalid", f"'{raw['cover']}' is not a card in this deck.")
            if card.scryfall_uid.lower() not in str(deck.raw.get("featured") or "").lower():
                plan["cover_uid"] = card.scryfall_uid
                rows.append(_row("detail", field="cover", before="current", after=card.name))
        return plan, rows

    async def _apply_organise(self, sub: str, deck_id: str, plan: dict[str, Any]) -> list[str]:
        """Carry out a details proposal's organisation plan with the same verified calls as the
        deck page's own buttons; each re-reads the deck. Returns the fields done."""
        done: list[str] = []
        if "folder_id" in plan:
            await self.move_deck(sub, deck_id, int(plan["folder_id"]))
            done.append("folder")
        for name in plan.get("add_tags") or []:
            await self.add_tag(sub, deck_id, name)
            done.append(f"tag +{name}")
        for rel in plan.get("remove_tags") or []:
            await self.remove_tag(sub, deck_id, int(rel["id"]))
            done.append(f"tag -{rel['name']}")
        if "cover_uid" in plan:
            await self.set_cover(sub, deck_id, plan["cover_uid"])
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
            await self._call(sub, self.client.update_deck, deck.id, details_payload(changes))
        verified = await self.get_deck(sub, deck.id)
        still, _lines = details_diff(verified, wanted)
        mismatches = sorted(still)
        organised: list[str] = []
        if organise and not mismatches:
            try:
                organised = await self._apply_organise(sub, deck.id, organise)
            except DeckError as exc:
                progress.update({"snapshot_id": snapshot_id, **backup, "fields": sorted(changes)})
                raise DeckError(
                    exc.kind,
                    f"{exc} The deck's other details were applied. A snapshot ({snapshot_id}) of the "
                    "deck before the change was kept.",
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

    async def delete_deck(self, sub: str, deck_id: str, typed_name: str) -> dict[str, Any]:
        """Delete one of the member's own decks after they typed its exact name. A snapshot is kept
        in the gateway and, when backups are on, a copy in the backup folder on Archidekt; the
        deletion is verified by reading the deck back (it must be gone)."""
        deck = await self.get_own_deck(sub, deck_id)
        if clean_text(typed_name).casefold() != clean_text(deck.name).casefold():
            raise DeckError(
                "invalid", "The name you typed does not match the deck's name. Nothing was deleted."
            )
        snapshot_id = self._hand_snapshot(sub, deck)
        backup: dict[str, Any] = {}
        if self.settings.archidekt_backups:
            try:
                backup = await self._backup_copy(sub, deck, snapshot_id, reason="before it was deleted")
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

    async def set_cover(self, sub: str, deck_id: str, scryfall_uid: str | None) -> dict[str, Any]:
        """Set the deck's cover image to the art of one of its cards (any zone), or back to
        Archidekt's automatic pick with ``None``. Verified by re-reading the deck."""
        deck = await self.get_own_deck(sub, deck_id)
        uid = str(scryfall_uid or "").strip().lower()
        if uid:
            if not any(c.scryfall_uid.lower() == uid for c in deck.cards):
                raise DeckError("invalid", "Pick a card that is in this deck for its cover.")
            try:
                url = art_url(uid)
            except ArchidektError as exc:
                raise DeckError("invalid", "That card has no usable art id.") from exc
            fields = {"featured": url, "customFeatured": ""}
        else:
            url = ""
            fields = {"customFeatured": ""}
        if (deck.raw.get("featured") or "") == url and not (deck.raw.get("customFeatured") or ""):
            return {"deck_id": deck.id, "featured": deck.featured, "changed": False}
        snapshot_id = self._hand_snapshot(sub, deck)
        await self._call(sub, self.client.update_deck, deck.id, fields)
        verified = await self.get_deck(sub, deck.id)
        ok = (
            (verified.raw.get("featured") or "") == url
            if uid
            else not (verified.raw.get("customFeatured") or "")
        )
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
        }

    # Folders -----------------------------------------------------------------------------------
    async def folders(self, sub: str) -> dict[str, Any]:
        """The member's folder tree flattened, root first: ``{root_id, folders: [{id, name, depth,
        parent, private}]}``. The gateway's backup folder is listed like any other."""
        tree = await self._call(sub, self.client.folder_tree)
        out: list[dict[str, Any]] = []

        def walk(node: dict[str, Any], depth: int, parent: int | None) -> None:
            if len(out) >= self.MAX_FOLDERS or not isinstance(node.get("id"), int):
                return
            out.append(
                {
                    "id": node["id"],
                    # Archidekt's top-level folder has a technical name; the pages call it by what it is
                    "name": "Top level (no folder)"
                    if depth == 0
                    else clean_text(str(node.get("name") or "")) or "Folder",
                    "depth": depth,
                    "parent": parent,
                    "private": bool(node.get("private")),
                }
            )
            children = node.get("children") or []
            for child in sorted(
                (c for c in children if isinstance(c, dict)), key=lambda c: str(c.get("name") or "").lower()
            ):
                walk(child, depth + 1, node["id"])

        walk(tree, 0, None)
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

    async def create_folder(self, sub: str, name: str, parent_id: int | None = None) -> dict[str, Any]:
        name = self._folder_name(name)
        info = await self.folders(sub)
        parent = int(parent_id) if parent_id is not None else int(info["root_id"])
        if parent not in {f["id"] for f in info["folders"]}:
            raise DeckError("not_found", "That parent folder is not in your Archidekt account.")
        if any(f["parent"] == parent and f["name"].casefold() == name.casefold() for f in info["folders"]):
            raise DeckError("invalid", f"There is already a folder called '{name}' there.")
        made = await self._call(sub, lambda token, *_: self.client.create_folder(token, name, parent))
        after, folder = await self._folder_in(sub, int(made["id"]))
        self._audit("folder_created", sub=sub, detail={"folder_id": folder["id"], "parent": parent})
        return folder

    async def rename_folder(self, sub: str, folder_id: int, name: str) -> dict[str, Any]:
        name = self._folder_name(name)
        info, folder = await self._folder_in(sub, int(folder_id))
        if folder["depth"] == 0:
            raise DeckError("invalid", "The top-level folder cannot be renamed.")
        if folder["name"] == name:
            return folder
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

    async def move_deck(self, sub: str, deck_id: str, folder_id: int | None) -> dict[str, Any]:
        """Move one of the member's decks into a folder (``None`` = the top-level folder); verified
        by re-reading the deck's ``parentFolder``."""
        deck = await self.get_own_deck(sub, deck_id)
        info = await self.folders(sub)
        root = int(info["root_id"])
        target = int(folder_id) if folder_id is not None else root
        if target not in {f["id"] for f in info["folders"]}:
            raise DeckError("not_found", "That folder is not in your Archidekt account.")
        current = deck.parent_folder if deck.parent_folder is not None else root
        if current == target:
            return {"deck_id": deck.id, "folder_id": target, "changed": False}
        await self._call(
            sub,
            lambda token, *_: self.client.mass_update(
                token,
                [
                    {
                        "id": int(deck.id),
                        "type": "deck",
                        "patch": {"parentFolder": target},
                        "parentFolderId": current,
                    }
                ],
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

    async def add_tag(self, sub: str, deck_id: str, name: str) -> dict[str, Any]:
        """Tag one of the member's decks: an existing Archidekt tag of that exact name is reused,
        else one is created. Verified by re-reading the deck's tags."""
        name = self._tag_name(name)
        deck = await self.get_own_deck(sub, deck_id)
        if any(str(r.get("name") or "").casefold() == name.casefold() for r in deck.tag_relations):
            raise DeckError("invalid", f"This deck already has the tag '{name}'.")
        if len(deck.tag_relations) >= self.MAX_TAGS:
            raise DeckError("invalid", f"A deck has at most {self.MAX_TAGS} tags here.")
        found = await self._call(sub, lambda token, *_: self.client.search_tags(token, name))
        tag_id = next(
            (
                t["id"]
                for t in found
                if str(t.get("name") or "").casefold() == name.casefold() and isinstance(t.get("id"), int)
            ),
            None,
        )
        snapshot_id = self._hand_snapshot(sub, deck)
        if tag_id is None:
            tag_id = int((await self._call(sub, lambda token, *_: self.client.create_tag(token, name)))["id"])
        position = f"M-{500000 + 10000 * len(deck.tag_relations)}"
        await self._call(sub, lambda token, *_: self.client.add_deck_tag(token, deck.id, tag_id, position))
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

    async def remove_tag(self, sub: str, deck_id: str, relation_id: int) -> dict[str, Any]:
        deck = await self.get_own_deck(sub, deck_id)
        rel = next((r for r in deck.tag_relations if r.get("id") == int(relation_id)), None)
        if rel is None:
            raise DeckError("not_found", "That tag is not on this deck.")
        snapshot_id = self._hand_snapshot(sub, deck)
        await self._call(sub, lambda token, *_: self.client.remove_deck_tag(token, int(relation_id)))
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
            out[key] = value
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
    text in full, so the review page shows exactly what an apply will write."""
    changes: dict[str, Any] = {}
    lines: list[dict[str, Any]] = []
    for key, value in wanted.items():
        new = FORMAT_IDS[value] if key == "deck_format" else value
        if new == _current_detail(deck, key):
            continue
        changes[key] = value
        if key == "description":
            current = str(deck.description or "")
            lines.append(
                _row(
                    "description",
                    before_len=len(current),
                    after_len=len(value or ""),
                    before_text=current,
                    after_text=str(value or ""),
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
    description, format (as its FORMAT_IDS key, when known), bracket, private and unlisted."""
    out: dict[str, Any] = {
        "name": snapshot.name,
        "description": snapshot.description or "",
        "edh_bracket": snapshot.edh_bracket,
        "private": bool(snapshot.private),
        "unlisted": bool(snapshot.unlisted),
    }
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


def _category_mismatches(verified: Deck, want: dict[int, list[str]]) -> list[str]:
    """Names of rows whose categories on the re-read deck are not what ``category_plan`` asked
    for (a row that vanished counts too)."""
    rows = {c.relation_id: c for c in verified.cards if c.relation_id is not None}
    out = set()
    for rel, cats in want.items():
        row = rows.get(rel)
        if row is None or sorted(row.categories) != sorted(cats):
            out.add(row.name if row else f"deck row {rel}")
    return sorted(out)


def _same_account(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Whether two link rows name the same Archidekt account (user id when both have one)."""
    if a.get("archidekt_user_id") and b.get("archidekt_user_id"):
        return str(a["archidekt_user_id"]) == str(b["archidekt_user_id"])
    return str(a.get("archidekt_username") or "").lower() == str(b.get("archidekt_username") or "").lower()


def _sent(progress: dict[str, Any]) -> dict[str, Any]:
    return {k: progress[k] for k in ("sent_entries", "of_entries", "snapshot_id", "deck_id") if k in progress}


def _progress_view(progress: dict[str, Any]) -> dict[str, Any]:
    """What a running apply has done so far, for get_proposal and the review page."""
    keys = ("resolved_cards", "of_cards", "sent_entries", "of_entries", "snapshot_id", "deck_id", "deck_url")
    return {k: progress[k] for k in keys if k in progress}


def _partial_note(progress: dict[str, Any]) -> str:
    """What a failed apply had already changed on Archidekt, for the message the user sees."""
    sent = int(progress.get("sent_entries") or 0)
    if not sent and progress.get("deck_id"):
        return f"The new deck {progress['deck_id']} was created on Archidekt, but no cards were added."
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


def _needs_user_message(mode: str, risk: str, why: str) -> str:
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
) -> str:
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
    if state == "pending" and may_apply:
        chose = (
            "to apply every change without asking"
            if mode == "auto"
            else f"to apply low-risk edits without asking, and this one is low risk ({risk_reason})"
        )
        return (
            f"The user chose {chose}: tell them {what} in one line and call apply_proposal now. "
            "They can also press Apply on the review page."
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
    if state == "failed" and partial:
        return (
            "Part of this change reached Archidekt before it failed (see result.sent_entries). Check "
            "the deck; result.snapshot_id can be restored with propose_restore_snapshot."
        )
    if state == "failed":
        return "Nothing further was changed. Create a new proposal if you still want these edits."
    if state == "rejected":
        return "Rejected by the user. Nothing was changed; create a new proposal for different edits."
    return "This proposal is no longer actionable."


def cards_from_json(text: str) -> list[dict[str, Any]]:
    """Rows from the gateway's own deck JSON (what get_deck returns and the Export page's .json
    download holds): an object with ``cards`` (or a bare list), each ``{name, quantity, categories,
    set, collector_number, finish}``. Only those six fields are read; the rest of a deck read
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
            }
        )
    return out


def normalise_cards(
    *,
    cards: Any = None,
    decklist_text: str | None = None,
    csv_text: str | None = None,
    json_text: str | None = None,
) -> list[dict[str, Any]]:
    """Turn any of the accepted inputs into [{name, quantity, categories, foil, finish, ...}].
    Sideboard and maybeboard rows are kept under their category (Archidekt stores them as
    categories outside the deck), so a pasted list, CSV or the gateway's JSON round-trips whole."""
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
