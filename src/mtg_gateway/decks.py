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
import json
import logging
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

from .archidekt import (
    FORMAT_IDS,
    ArchidektClient,
    ArchidektError,
    Deck,
    finish_modifier,
    front_face,
    jwt_exp,
    parse_deck,
)
from .archidekt_csv import CsvError, parse_export
from .config import Settings
from .db import Database
from .decklist import DecklistError, ListCard, clean_text, parse_decklist, to_text

logger = logging.getLogger(__name__)

ACTIONS = ("add", "remove", "set_quantity", "set_category", "set_commander", "set_finish", "set_printing")
COUNT_ACTIONS = ("add", "remove", "set_quantity")
CATEGORY_ACTIONS = ("set_category", "set_commander")
# Changes to how a card already in the deck is printed: its finish, or the printing itself.
PRINTING_ACTIONS = ("set_finish", "set_printing")
COMMANDER = "Commander"
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
# The OAuth client (or "__browser__") acting on the current request, recorded in the audit log.
# Set by the MCP tool layer and the browser pages; None when the actor is not known.
current_client: ContextVar[str | None] = ContextVar("current_client", default=None)
MAX_CHANGES = 40
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


def row_label(r: dict[str, Any]) -> str:
    """'Sol Ring (CMR 1, Foil) [Ramp]': a row's card, printing and category."""
    label = str(r.get("name", ""))
    if r.get("printing"):
        label += f" {r['printing']}"
    if r.get("category"):
        label += f" [{r['category']}]"
    return label


def row_line(r: dict[str, Any]) -> str:
    """The plain-text diff line for one review row."""
    kind = r.get("kind")
    label = row_label(r)
    if kind == "add":
        return f"+{r['qty']} {label}"
    if kind == "remove":
        return f"-{r['qty']} {label}"
    if kind == "change":
        return f"{r['before']} -> {r['after']} {label}"
    if kind == "category":
        line = f"{r['name']}: category {r['before']} -> {r['after']}"
        if r.get("leaves"):  # moved into a category the deck does not count (Maybeboard...)
            line += f" (leaves the deck, -{r['leaves']})"
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
        return f"description: ({f'changed, {after_len} chars' if after_len else 'cleared'})"
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
        return d


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
        if action in CATEGORY_ACTIONS:
            out.append(_parse_category_change(i, action, name, item))
            continue
        if action in PRINTING_ACTIONS:
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
        out.append(Change(action, name, qty, cat, set_code, number, finish))
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
    categorised = [front_face(ch.card_name).lower() for ch in out if ch.action in CATEGORY_ACTIONS]
    if len(categorised) != len(set(categorised)):
        raise DeckError("invalid", "one set_category or set_commander per card name per proposal")
    reprinted = [front_face(ch.card_name).lower() for ch in out if ch.action in PRINTING_ACTIONS]
    if len(reprinted) != len(set(reprinted)):
        raise DeckError("invalid", "one set_finish or set_printing per card name per proposal")
    counted = {front_face(ch.card_name).lower() for ch in out if ch.action in COUNT_ACTIONS}
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
    rows_by_name: dict[str, list[Any]] = {}
    for c in deck.main_cards:  # maybeboard and sideboard rows are never pulled into the deck
        rows_by_name.setdefault(c.name.lower(), []).append(c)
        rows_by_name.setdefault(front_face(c.name).lower(), []).append(c)
    want: dict[int, list[str]] = {}
    lines: list[dict[str, Any]] = []
    new_commanders: list[str] = []
    for ch in cat_changes:
        rows = rows_by_name.get(ch.card_name.lower())
        if not rows:
            raise DeckError("invalid", f"'{ch.card_name}' is not in the deck, so its category cannot be set")
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
            lines.append(_row("category", name=rows[0].name, before=" / ".join(old), after=new[0]))
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
    excluded = deck.excluded_categories()
    out: dict[str, int] = {}
    for c in deck.main_cards:
        cats = recategorise.get(c.relation_id) if c.relation_id is not None else None
        if cats and all(cat in excluded for cat in cats):
            out[c.name] = out.get(c.name, 0) + c.quantity
    return out


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
    before = deck.counts_by_name()
    lookup = {n.lower(): n for n in before}
    for n in before:  # a double-faced card is also found by its front face
        lookup.setdefault(front_face(n).lower(), n)
    after = dict(before)
    cats_want, cat_lines = category_plan_rows(deck, changes)
    # A set_category into a category the deck does not count (Maybeboard, Sideboard...) takes
    # those copies out of the deck proper: the review says so, and counts them as removed.
    leaving = leaving_deck(deck, cats_want)
    for r in cat_lines:
        if r.get("kind") == "category" and r.get("name") in leaving:
            r["leaves"] = leaving.pop(r["name"])
    cat_lines += [_row("remove", name=name, qty=qty) for name, qty in leaving.items()]
    _specs, print_lines = printing_plan_rows(deck, changes)
    cat_lines = cat_lines + print_lines
    for ch in changes:
        if ch.action in CATEGORY_ACTIONS or ch.action in PRINTING_ACTIONS:
            continue
        key = lookup.get(ch.card_name.lower(), ch.card_name)
        cur = after.get(key, 0)
        if ch.action == "add":
            after[key] = cur + (ch.quantity or 1)
        elif ch.action == "remove":
            if cur == 0:
                raise DeckError("invalid", f"'{ch.card_name}' is not in the deck, so it cannot be removed")
            after[key] = max(0, cur - ch.quantity) if ch.quantity else 0
        else:
            after[key] = ch.quantity or 0
        lookup.setdefault(key.lower(), key)
    adds_by_name: dict[str, list[Change]] = {}
    for ch in changes:
        if ch.action == "add":
            adds_by_name.setdefault(ch.card_name.lower(), []).append(ch)
    # the printing shows on the line only when every added copy of that card is that printing
    labels = {n: chs[0].printing_label() for n, chs in adds_by_name.items() if len(chs) == 1}
    # the category an add puts a new row in (Commander, Maybeboard...) is part of what is reviewed
    cats = {n: next((c.category for c in chs if c.category), None) for n, chs in adds_by_name.items()}
    pinned_names = {n for n, chs in adds_by_name.items() if any(c.pinned for c in chs)}
    lines: list[dict[str, Any]] = []
    for name in sorted(set(before) | set(after), key=str.lower):
        b, a = before.get(name, 0), after.get(name, 0)
        if a == b:
            continue
        printing = labels.get(name.lower()) or None
        cat = cats.get(name.lower())
        if not (cat and (b == 0 or name.lower() in pinned_names)):  # a new row is made in that category
            cat = None
        if b == 0:
            lines.append(_row("add", name=name, qty=a, printing=printing, category=cat))
        elif a == 0:
            lines.append(_row("remove", name=name, qty=b))
        else:
            lines.append(_row("change", name=name, before=b, after=a, printing=printing, category=cat))
    lines.extend(cat_lines)
    if not lines:
        raise DeckError("invalid", "these changes would leave the deck exactly as it is")
    return before, after, lines


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
) -> list[dict[str, Any]]:
    """Per-card entries for PATCH modifyCards/v2, one entry per request.

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


def new_deck_entries(
    cards: list[dict[str, Any]], resolve: dict[str, int], modifiers: dict[str, str] | None = None
) -> list[dict[str, Any]]:
    """Add entries for a freshly created deck (same reference shape as build_payload)."""
    out = []
    for c in cards:
        out.append(
            {
                "action": "add",
                "cardid": resolve[c["name"]],
                "patchId": uuid.uuid4().hex,
                "categories": list(c.get("categories") or []),
                "modifications": {
                    "quantity": int(c["quantity"]),
                    "companion": False,
                    "flippedDefault": False,
                    "modifier": (modifiers or {}).get(c["name"], "Foil" if c.get("foil") else "Normal"),
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
            categories=list(c.categories),
            foil=c.modifier.lower() == "foil",
            zone="main" if deck.in_deck(c) else "side",
        )
        for c in deck.cards
    ]
    return to_text(cards, zone=zone)


class DeckService:
    def __init__(self, settings: Settings, db: Database, client: ArchidektClient):
        self.settings = settings
        self.db = db
        self.client = client
        self.fernet = Fernet(settings.fernet_key.encode())
        self._deck_locks: dict[str, asyncio.Lock] = {}
        self._archidekt_in_flight: dict[str, int] = {}
        self._link_locks: dict[str, asyncio.Lock] = {}
        self.max_archidekt_per_user = MAX_ARCHIDEKT_PER_USER
        self.archidekt_budget = RateBudget(settings.archidekt_calls_per_10_min)

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
                f"{self.max_archidekt_per_user} Archidekt requests for your account are still running "
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
            f"Your account has used its {self.archidekt_budget.per_window} Archidekt requests for "
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
        secret = json.dumps({"access": session["access"], "refresh": session.get("refresh")})
        self.db.save_link(
            sub,
            username=session["username"],
            user_id=session.get("user_id"),
            secret_enc=self.fernet.encrypt(secret.encode()).decode(),
        )
        self._audit("archidekt_linked", sub=sub, detail={"archidekt_username": session["username"]})
        return self.status(sub)

    def unlink(self, sub: str) -> None:
        self.db.revoke_link(sub)
        self._audit("archidekt_unlinked", sub=sub)

    def status(self, sub: str) -> dict[str, Any]:
        row = self.db.get_link(sub)
        return {
            "linked": row is not None,
            "archidekt_username": row["archidekt_username"] if row else None,
            "linked_at": row["created_at"] if row else None,
            "last_used_at": row["last_used_at"] if row else None,
            "writes_enabled": self.settings.writes_enabled,
            "account_page": f"{self.settings.public_url}/account",
        }

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
        try:
            secret = json.loads(self.fernet.decrypt(row["secret_enc"].encode()).decode())
            access = secret["access"]
        except (InvalidToken, ValueError, KeyError, TypeError) as exc:
            raise DeckError("not_linked", "The stored Archidekt session could not be read. Relink.") from exc
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
            blob = json.dumps({"access": access, "refresh": refresh})
            new_enc = self.fernet.encrypt(blob.encode()).decode()
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
        self.db.touch_link(sub)
        return result

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
        _token, row = self._token(sub)
        exclude = self.settings.archidekt_backup_folder if self.settings.archidekt_backups else None
        return await self._call(
            sub,
            lambda token, *_: self.client.list_decks(
                token, row["archidekt_username"], row.get("archidekt_user_id"), exclude_folder=exclude
            ),
        )

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
        """Read a deck anonymously (public or unlisted). When that fails with
        not-found and the user has a link, retry with their session (private decks)."""
        deck_id = _clean_deck_id(deck_ref)
        async with self.archidekt_slot(sub):
            try:
                return await self.client.get_deck(None, deck_id)
            except ArchidektError as exc:
                if exc.kind not in ("not_found", "auth", "forbidden") or not sub or not self.db.get_link(sub):
                    raise DeckError(exc.kind, str(exc)) from exc
            return await self.get_deck(sub, deck_id)

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
        private: bool = True,
    ) -> dict[str, Any]:
        name = clean_text(name or "")
        if not name or len(name) > 120:
            raise DeckError("invalid", "name is required (up to 120 characters)")
        fmt = str(deck_format or "commander").strip().lower()
        if fmt not in FORMAT_IDS:
            raise DeckError("invalid", f"deck_format must be one of: {', '.join(sorted(FORMAT_IDS))}")
        entries = normalise_cards(cards=cards, decklist_text=decklist_text, csv_text=csv_text)
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
        return {
            "proposal_id": row["id"],
            "kind": row.get("kind", "edit"),
            "deck_id": row["deck_id"],
            "deck_url": f"https://archidekt.com/decks/{row['deck_id']}" if row["deck_id"].isdigit() else None,
            "deck_name": row["deck_name"],
            "state": state,
            "diff": row["diff_text"],
            # The structured rows the review page renders (None for proposals stored before them).
            "rows": row.get("rows"),
            "changes": row["changes"],
            "created_at": row["created_at"],
            "expires_at": row["expires_at"],
            "applied_at": row["applied_at"],
            "snapshot_id": row["snapshot_id"],
            "result": row["result"],
            "review_url": f"{self.settings.public_url}/proposals/{row['id']}",
            # Which app made it ("browser", or "app: <its name> (<client id>)"), for the review page.
            "created_by": self._creator_label(row.get("created_by_client")),
            "writes_enabled": self.settings.writes_enabled,
            "next_step": _next_step(
                state,
                self.settings.writes_enabled,
                via_mcp=self.settings.apply_via_mcp,
                min_age=self.settings.apply_min_age_seconds,
                partial=bool((row["result"] or {}).get("sent_entries")),
                kind=row.get("kind", "edit"),
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
        and the app that made it; refused past the member's pending cap."""
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

    def list_proposals(self, sub: str) -> list[dict[str, Any]]:
        now = int(time.time())
        out = []
        for r in self.db.list_proposals(sub):
            state = "expired" if r["state"] == "pending" and r["expires_at"] < now else r["state"]
            out.append(
                {
                    **r,
                    "state": state,
                    "created_by": self._creator_label(r.get("created_by_client")),
                    "review_url": f"{self.settings.public_url}/proposals/{r['id']}",
                }
            )
        return out

    async def apply(self, sub: str, proposal_id: str, *, via: str) -> dict[str, Any]:
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
        age = int(time.time()) - int(row["created_at"])
        if via == "mcp" and age < self.settings.apply_min_age_seconds:
            wait = self.settings.apply_min_age_seconds - age
            raise DeckError(
                "apply_too_soon",
                "This proposal was created moments ago. Show the user its change preview and review "
                "link and wait for their explicit OK in their own message. If they already said yes, "
                f"retry once after {wait} seconds. Nothing was sent to Archidekt.",
                retry_after_seconds=wait,
            )
        creator = row.get("created_by_client")
        if via == "mcp" and (not creator or creator != current_client.get()):
            # Only the assistant that proposed (and showed the user this diff) may apply it over
            # MCP; a proposal left pending cannot be picked up by another connected client.
            raise DeckError(
                "other_client",
                "This proposal was made by a different connected app, so it cannot be applied from "
                "here. The user can press Apply on the review page. Nothing was sent to Archidekt.",
                review_url=f"{self.settings.public_url}/proposals/{proposal_id}",
            )
        async with self.archidekt_slot(sub):  # refused before the claim, so nothing is sent
            return await self._apply_slotted(sub, proposal_id, row, via=via)

    async def _apply_slotted(
        self, sub: str, proposal_id: str, row: dict[str, Any], *, via: str
    ) -> dict[str, Any]:
        if not self.db.claim_proposal(proposal_id, sub):
            raise DeckError("not_pending", f"This proposal is {row['state']} and cannot be applied.")
        lock = self._deck_locks.setdefault(f"{sub}:{row['deck_id']}", asyncio.Lock())
        # What the apply got done so far (snapshot, backup, entries sent of how many, the verify
        # result), kept in the proposal's result whichever way the apply ends.
        progress: dict[str, Any] = {}
        async with lock:
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
        changes = parse_changes(row["changes"])
        deck = await self._current_deck_for(sub, row)
        _before, after, _lines = plan(deck, changes)
        recategorise, _cat_lines = category_plan(deck, changes)
        # Every printing is looked up before anything is written or backed up, so a printing
        # that does not exist (or is another card) refuses the proposal with nothing changed.
        resolve, modifiers, pinned, new_cats = await self._resolve_adds(sub, deck, changes, after)
        payload = build_payload(deck, after, resolve, new_cats, modifiers, pinned, recategorise)
        specs, _print_lines = printing_plan_rows(deck, changes)
        payload += await self._printing_entries(sub, deck, specs)
        snapshot_id = self._take_snapshot(sub, row, deck)
        progress["snapshot_id"] = snapshot_id
        backup = await self._backup_on_archidekt(sub, row, deck, snapshot_id)
        progress.update(backup)
        await self._rows_unchanged(sub, deck)  # lookups and backup take a while: check again
        await self._send(sub, deck.id, payload, progress)
        verified = await self.get_deck(sub, deck.id)
        expected = dict(after)  # the counts after the count changes, less what leaves the deck
        for name, qty in leaving_deck(deck, recategorise).items():
            expected[name] = expected.get(name, 0) - qty
        mismatches = _mismatches(verified.counts_by_name(), {n: q for n, q in expected.items() if q > 0})
        mismatches = sorted(
            set(mismatches)
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
        self, sub: str, deck: Deck, changes: list[Change], after: dict[str, int]
    ) -> tuple[dict[str, int], dict[str, str], dict[str, dict[str, Any]], dict[str, list[str]]]:
        """Printing ids and finishes for the cards an edit adds, keyed by the spelling used in
        ``after``. A pinned printing (set code + collector number) is required to exist and to be
        that card: the client raises ``not_found`` otherwise and nothing is added."""

        def key_for(name: str) -> str:
            return next((k for k in after if k.lower() == name.lower()), name)

        have = {c.name.lower() for c in deck.main_cards}
        adds: dict[str, list[Change]] = {}
        for ch in changes:
            if ch.action == "add":
                adds.setdefault(key_for(ch.card_name), []).append(ch)
        resolve: dict[str, int] = {}
        modifiers: dict[str, str] = {}
        pinned: dict[str, dict[str, Any]] = {}
        new_cats: dict[str, list[str]] = {}
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
        return resolve, modifiers, pinned, new_cats

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
        when = time.gmtime()
        name = f"{deck.name} (backup {time.strftime('%Y-%m-%d %H:%M UTC', when)})"[:200]
        description = (
            f"Automatic backup made by the MTG Assistant Gateway before proposal {row['id']} changed this "
            f"deck ({row.get('kind') or 'edit'}). Gateway snapshot {snapshot_id}. To undo the change, ask "
            f"the assistant to restore snapshot {snapshot_id}, or copy this deck back by hand. "
            f"Original deck: https://archidekt.com/decks/{deck.id}"
        )
        try:
            folder = await self._call(
                sub,
                lambda token, *_: self.client.ensure_folder(
                    token, name=self.settings.archidekt_backup_folder
                ),
            )
            copy = await self._call(
                sub,
                lambda token, *_: self.client.backup_deck(
                    token, deck, name=name, folder_id=str(folder["id"]), description=description
                ),
            )
        except DeckError as exc:
            self.db.finish_proposal(row["id"], state="pending")
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
        backup = {"backup_deck_id": str(copy["id"]), "backup_url": str(copy.get("url") or "")}
        self.db.set_snapshot_backup(snapshot_id, **backup)
        return backup

    async def propose_clone(self, sub: str, deck_id: str, name: str | None = None) -> dict[str, Any]:
        """A proposal (kind ``clone``) that copies one of the member's decks into a new private
        deck in the root folder, the way Archidekt's Clone deck button does ("Copy of - " prefix
        by default). Applied with the same copy route the backups use (verified live 2026-10-05);
        the copy keeps every card, quantity, category and finish."""
        deck_id = _clean_deck_id(deck_id)
        self._room_for_proposal(sub)
        deck = await self.get_own_deck(sub, deck_id)
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
        deck = await self._current_deck_for(sub, row)
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
        await self._send(sub, deck.id, payload, progress)
        verified = await self.get_deck(sub, deck.id)
        _left, mismatches = restore_steps(wanted, verified)
        result = {
            "snapshot_id": snapshot_id,
            **backup,
            "restored_snapshot_id": snap["id"],
            "sent_entries": len(payload),
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
        resolve: dict[str, int] = {}
        modifiers: dict[str, str] = {}
        printing_notes: list[str] = []
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
            resolve[c["name"]] = card["id"]
            modifiers[c["name"]] = finish_modifier(card["options"], foil=bool(c.get("foil")))
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
        want: dict[str, int] = {}
        for c in cards:
            want[c["name"]] = want.get(c["name"], 0) + int(c["quantity"])
        mismatches = _mismatches(verified.counts_by_name(), want)
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

    def list_snapshots(self, sub: str) -> list[dict[str, Any]]:
        return self.db.list_snapshots(sub)

    async def propose_restore(self, sub: str, snapshot_id: str) -> dict[str, Any]:
        """A proposal that puts the deck back to what a snapshot recorded, relation by relation:
        the same printings, finishes (foil, etched), categories (so the commander, sideboard and
        maybeboard too) and quantities. It is an ordinary proposal: reviewed, confirmed and
        applied like any other (which takes a fresh snapshot first), so a restore can itself be
        undone. Not restored: the deck's own category definitions (a custom category's
        'counts toward the deck' setting), name, description and format."""
        snap = self.snapshot(sub, str(snapshot_id or "").strip())
        wanted = parse_deck(snap["deck"])
        self._room_for_proposal(sub)
        deck = await self.get_own_deck(sub, snap["deck_id"])
        payload, lines = restore_rows(wanted, deck)
        if not payload:
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
                "changes": {"snapshot_id": snap["id"]},
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
        wanted = parse_details(details)
        self._room_for_proposal(sub)
        deck = await self.get_own_deck(sub, deck_id)
        changes, rows = details_rows(deck, wanted)
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

    async def _apply_details(self, sub: str, row: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        wanted = parse_details(row["changes"])
        deck = await self._current_deck_for(sub, row)
        changes, _lines = details_diff(deck, wanted)
        if not changes:
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
        fields = details_payload(changes)
        await self._call(sub, self.client.update_deck, deck.id, fields)
        verified = await self.get_deck(sub, deck.id)
        still, _lines = details_diff(verified, wanted)
        mismatches = sorted(still)
        result = {
            "snapshot_id": snapshot_id,
            **backup,
            "fields": sorted(changes),
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


def parse_details(raw: Any) -> dict[str, Any]:
    """Validate the ``details`` of propose_deck_details: a non-empty object with only known keys,
    each of the right type and range. ``deck_format`` is returned as its FORMAT_IDS key."""
    if not isinstance(raw, dict) or not raw:
        raise DeckError(
            "invalid", "details must be a non-empty object with any of: " + ", ".join(DETAIL_FIELDS)
        )
    unknown = sorted(str(k) for k in raw if k not in DETAIL_FIELDS)
    if unknown:
        raise DeckError("invalid", f"unknown details: {', '.join(unknown)}; use {', '.join(DETAIL_FIELDS)}")
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


def details_payload(changes: dict[str, Any]) -> dict[str, Any]:
    """The PATCH /decks/{id}/update/ body for validated detail changes (Archidekt's field names)."""
    out: dict[str, Any] = {}
    for key, value in changes.items():
        out[DETAIL_FIELDS[key]] = FORMAT_IDS[value] if key == "deck_format" else value
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


def _clean_deck_id(deck_id: Any) -> str:
    raw = str(deck_id).strip()
    if len(raw) > 2000:
        raise DeckError("invalid", "deck_id must be the numeric id from the Archidekt deck URL")
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
        raise DeckError("invalid", "deck_id must be the numeric id from the Archidekt deck URL")
    return raw


def _next_step(
    state: str,
    writes_enabled: bool,
    *,
    via_mcp: bool = False,
    min_age: int = 0,
    partial: bool = False,
    kind: str = "edit",
) -> str:
    what = "these details" if kind == "details" else "this copy" if kind == "clone" else "this diff"
    if state == "pending" and not writes_enabled:
        return "Review only: deck writes are disabled on this gateway, so this cannot be applied yet."
    if state == "pending" and via_mcp:
        hold = f" (the gateway refuses applies made within {min_age} s of proposing)" if min_age else ""
        return (
            f"Show the user {what} and the review link, wait for their explicit OK in their own "
            f"message, then call apply_proposal{hold}. They can also press Apply on the review page. "
            "If your app will not run apply_proposal, do not retry: send the user the review link to "
            "press Apply there."
        )
    if state == "pending":
        return f"Review {what}, then confirm on the review page (the Apply button)."
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


def normalise_cards(
    *, cards: Any = None, decklist_text: str | None = None, csv_text: str | None = None
) -> list[dict[str, Any]]:
    """Turn any of the accepted inputs into [{name, quantity, categories, foil}], main deck only."""
    for label, value in (("decklist_text", decklist_text), ("csv_text", csv_text)):
        if value is not None and not isinstance(value, str):
            raise DeckError("invalid", f"{label} must be a string")
    given = [x is not None and x != "" and x != [] for x in (cards, decklist_text, csv_text)]
    if sum(given) != 1:
        raise DeckError("invalid", "give exactly one of cards, decklist_text or csv_text")
    if csv_text and len(csv_text) > 2_000_000:
        raise DeckError("invalid", "CSV export larger than 2 MB")
    if decklist_text and len(decklist_text) > 200_000:
        raise DeckError("invalid", "decklist larger than 200 kB")
    out: list[dict[str, Any]] = []
    if csv_text:
        try:
            for c in parse_export(csv_text):
                out.append(
                    {
                        "name": c.name,
                        "quantity": c.quantity,
                        "categories": c.categories,
                        "foil": c.finish == "Foil",
                        "set_code": c.set_code,
                        "collector_number": c.collector_number,
                    }
                )
        except CsvError as exc:
            raise DeckError("invalid", f"CSV export could not be read: {exc}") from exc
    elif decklist_text:
        try:
            for c in parse_decklist(decklist_text):
                if c.zone == "main":
                    out.append(
                        {
                            "name": c.name,
                            "quantity": c.quantity,
                            "categories": c.categories,
                            "foil": c.foil,
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
            out.append(
                {
                    "name": name,
                    "quantity": qty,
                    "categories": [cat] if cat else [],
                    "foil": item.get("foil") is True,
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
