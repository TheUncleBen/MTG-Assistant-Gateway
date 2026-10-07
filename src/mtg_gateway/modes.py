"""Approval modes: how much a member lets their assistant do without asking.

Every member picks a mode for their own account on the Account page (never over MCP or the
API, so a tricked assistant cannot loosen it), and it governs only that member's proposals,
decks and connected apps:

* ``manual`` (the default on a fresh install): every change waits for the member's own press
  on the proposal card in the chat or on the review page, every time.
* ``semi``: the assistant may apply a *low-risk* proposal itself; anything high-risk waits for
  the member's press as in manual.
* ``auto``: the assistant may apply every proposal itself.

Every apply still snapshots the deck first, so an auto-applied change can be undone from the
History page. The risk tiers are decided from the proposal's stored review rows, so a
proposal is low risk only if what the review page would show is small and safe:

* **low**: an edit to an existing deck with at most ``max_rows`` rows (default 5), where every
  row adds, removes, changes the quantity, moves the category or changes the finish or
  printing of a card, no row moves more than ``MAX_COPIES_PER_ROW`` copies (a playset) and none
  touches the commander; or a clone, which creates a private copy and changes nothing that
  exists.
* **high**: everything else: more rows than that, any commander change, a new deck, restoring
  a snapshot, and deck details (name, format, description, visibility).

The operator may set the default mode for members who have not chosen (``MTG_APPROVAL_MODE_DEFAULT``)
and cap what members may choose (``MTG_APPROVAL_MODE_MAX``, default ``auto`` = no cap).
"""

from __future__ import annotations

from typing import Any

MODES = ("manual", "semi", "auto")
DEFAULT_MODE = "manual"
DEFAULT_MAX_ROWS = 5
MAX_COPIES_PER_ROW = 4  # a row that adds, removes or changes more copies than this is not small

# What each mode means, in the member's words, for the Account page and the docs.
MODE_LABELS = {
    "manual": "Ask me every time",
    "semi": "Apply small, low-risk edits without asking",
    "auto": "Apply every change without asking",
}
MODE_HELP = {
    "manual": (
        "Every change waits for your own press on the card in the chat or on the review page. "
        "The safest choice, and the default."
    ),
    "semi": (
        "Your assistant applies small edits itself (a few cards added, removed, moved or changed, "
        "never the commander). New decks, restores, deck details, commander changes and bigger "
        "edits still wait for your press."
    ),
    "auto": "Your assistant applies every change it proposes, without asking you.",
}
# Shown next to the semi and auto choices, because turning them on has a real cost.
AUTO_WARNING = (
    "An assistant can be tricked by text it reads (a web page, a deck description, a pasted "
    "list) into making changes you did not ask for. With either auto mode such a change lands "
    "on Archidekt without your press. Every change still keeps a snapshot you can restore from "
    "the History page."
)

# Row kinds (decks.row_line) a low-risk edit may consist of.
LOW_RISK_ROW_KINDS = frozenset({"add", "remove", "change", "category", "finish", "printing"})


def valid_mode(mode: Any) -> bool:
    return isinstance(mode, str) and mode in MODES


def rank(mode: str) -> int:
    return MODES.index(mode)


def effective_mode(chosen: str | None, *, default: str, cap: str) -> str:
    """The mode that governs a member: their choice (or the operator's default while they have
    not chosen), never above the operator's cap."""
    mode = chosen if valid_mode(chosen) else default
    if not valid_mode(mode):
        mode = DEFAULT_MODE
    if not valid_mode(cap):
        cap = "auto"
    return mode if rank(mode) <= rank(cap) else cap


def risk_of(
    kind: str, rows: list[dict[str, Any]] | None, *, max_rows: int = DEFAULT_MAX_ROWS
) -> tuple[str, str]:
    """``("low" | "high", reason)`` for a proposal, from its kind and stored review rows."""
    if kind == "clone":
        return "low", "copies a deck and changes nothing that exists"
    if kind == "create_deck":
        return "high", "creates a new deck"
    if kind == "restore":
        return "high", "restores a snapshot"
    if kind == "details":
        return "high", "changes the deck's details"
    if kind not in ("edit", "collection"):
        return "high", "not a plain deck or collection edit"
    if rows is None:  # stored before review rows existed: nothing to judge it by
        return "high", "has no review rows"
    kinds = [str(r.get("kind")) for r in rows]
    if any(k == "commander" for k in kinds):
        return "high", "changes the commander"
    if any(k not in LOW_RISK_ROW_KINDS for k in kinds):
        return (
            "high",
            "contains a change that is not a card add, remove, quantity, category, finish or printing",
        )
    if len(rows) > max_rows:
        return "high", f"changes {len(rows)} rows, more than the {max_rows} a low-risk edit may have"
    for r in rows:
        if _copies_moved(r) > MAX_COPIES_PER_ROW:
            return (
                "high",
                f"moves {_copies_moved(r)} copies of {r.get('name')}, more than {MAX_COPIES_PER_ROW}",
            )
    return "low", f"{len(rows)} card row{'s' if len(rows) != 1 else ''}, no commander change"


# A hand edit in the app is the member's own approval, so it applies at once; only these get an
# explicit confirmation first, as Archidekt itself asks before destructive steps.
HAND_EDIT_CONFIRM_COPIES = 10  # removing more copies than this in one go
HAND_EDIT_CONFIRM_ROWS = 8  # or removing this many distinct cards


def hand_edit_confirm(kind: str, rows: list[dict[str, Any]] | None) -> str | None:
    """Why a member's own edit in the app should ask "are you sure" before it is applied, or None
    when it can go straight to Archidekt (with its snapshot, as every apply takes one)."""
    if kind == "restore":
        return "This replaces the whole deck with the snapshot."
    if kind != "edit" or not rows:
        return None
    if any(r.get("kind") == "commander" for r in rows):
        return "This changes the deck's commander."
    removed_rows = [r for r in rows if r.get("kind") == "remove" or _removes(r)]
    copies = sum(_removes(r) for r in rows)
    if len(removed_rows) >= HAND_EDIT_CONFIRM_ROWS:
        return f"This removes {len(removed_rows)} different cards from the deck."
    if copies > HAND_EDIT_CONFIRM_COPIES:
        return f"This removes {copies} cards from the deck."
    return None


def _removes(row: dict[str, Any]) -> int:
    """How many copies a review row takes out of the deck (0 when it adds or only recategorises)."""
    try:
        if row.get("kind") == "remove":
            return abs(int(row.get("qty") or 0))
        if row.get("kind") == "change":
            return max(0, int(row.get("before") or 0) - int(row.get("after") or 0))
    except (TypeError, ValueError):
        return HAND_EDIT_CONFIRM_COPIES + 1
    return 0


def _copies_moved(row: dict[str, Any]) -> int:
    """How many copies a review row adds, removes or changes (0 for rows without a count)."""
    try:
        if row.get("kind") in ("add", "remove"):
            return abs(int(row.get("qty") or 0))
        if row.get("kind") == "change":
            return abs(int(row.get("after") or 0) - int(row.get("before") or 0))
    except (TypeError, ValueError):
        return MAX_COPIES_PER_ROW + 1  # a count that cannot be read is not small
    return 0


def assistant_may_apply(mode: str, risk: str) -> bool:
    """Whether the member's mode lets their assistant apply a proposal of this risk itself."""
    return mode == "auto" or (mode == "semi" and risk == "low")
