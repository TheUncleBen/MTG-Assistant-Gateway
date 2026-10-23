"""Approval modes: how much a member lets their assistant do without asking.

Every member picks a mode for their own account on the Account page (never over MCP or the
API, so a tricked assistant cannot loosen it), and it governs only that member's proposals,
decks and connected apps:

* ``manual`` (the default on a fresh install): every change waits for the member's own press
  on the proposal card in the chat or on the review page, every time.
* ``semi``: the assistant may apply a *low-risk* proposal itself; anything high-risk waits for
  the member's press as in manual.
* ``auto``: the assistant may apply every proposal itself, except the ones that always wait for
  a card press (``auto_waits``): restoring a snapshot, a commander or companion change, a new deck, a
  big edit (more rows than the ``auto_big_edit_rows`` setting, 20 by default), and
  making a deck public or unlisted and a description edit that keeps formatting.

In every mode an assistant never applies a proposal that is held (a proposal it depends on was
rejected, withdrawn or failed) or that another app changed after it was made.

Every apply to one of the member's decks snapshots the deck first, so an auto-applied deck change
can be restored from the History page (a restore leaves the folder, tags and cover as they are); a
collection change, a clone and a new deck keep no snapshot. The risk tiers are decided from the
proposal's stored review rows, so a proposal is low risk only if what the review page would show is
small and safe:

* **low**: an edit to an existing deck or to the collection with at most ``max_rows`` rows
  (default 5), where every row adds, removes, changes the quantity, moves the category or
  changes the finish, printing, color tag or custom mana value of a card, no row moves more than
  ``MAX_COPIES_PER_ROW`` copies (a playset) and none touches the commander; or a clone, which
  creates a private copy and changes nothing that exists.
* **high**: everything else: more rows than that, any commander change, a new deck, restoring
  a snapshot, deck details (name, format, description, visibility).
* **consent**: the Archidekt account actions of actions.py: likes, votes, bookmarks,
  follows, posting, editing, deleting and voting on comments, creating a folder, and adding or
  removing a deck's collaborators. Each one waits for the member's own press, in every mode: on
  the card in the chat (0.7.29) or on its review page.
* **destructive**: deleting a deck. Like consent, no mode lets an assistant apply it, and
  only its review page applies it (not the card in the chat).

The operator may set the default mode for members who have not chosen (``MTG_APPROVAL_MODE_DEFAULT``)
and cap what members may choose (``MTG_APPROVAL_MODE_MAX``, default ``auto`` = no cap).
"""

from __future__ import annotations

from typing import Any

MODES = ("manual", "semi", "auto")
# Risk tiers no approval mode lets an assistant apply: acting publicly or on the member's
# account (consent) and deleting a deck (destructive) always wait for the member's own press.
MEMBER_ONLY = ("consent", "destructive")
# Stored on a closed proposal whose risk could not be judged (nothing to judge it by, or an
# error while judging): never retried, shown as a dash with no risk label.
UNJUDGED = "unjudged"
# risk_of's reason for an edit with no review rows: High only because nothing can be judged
NO_ROWS_REASON = "has no list of rows to check"
DEFAULT_MODE = "manual"
DEFAULT_MAX_ROWS = 5
DEFAULT_BIG_EDIT_ROWS = 20  # an edit with more rows than this waits for a card press in auto
MAX_COPIES_PER_ROW = 4  # a row that adds, removes or changes more copies than this is not small

# Row kinds (decks.row_line) a low-risk edit may consist of, each with the word the wording below
# uses for it, so the list risk_of checks and the list the member reads are one list.
LOW_RISK_ROW_WORDS = {
    "add": "added",
    "remove": "removed",
    "change": "quantity",
    "category": "category",
    "finish": "finish",
    "printing": "printing",
    "label": "color tag",
    "mana_value": "custom mana value",
}
LOW_RISK_ROW_KINDS = frozenset(LOW_RISK_ROW_WORDS)

# -- The approval-mode wording, written once (0.7.24). The Account page, the guide, the proposal
# results (approval_mode_note) and docs/ say what each mode applies by itself and what always
# waits; every copy comes from here, and docs/ quotes these sentences exactly (a test checks it).

# What a low-risk row may change: "a card added or removed, or a new quantity, ... or custom mana value".
_CHANGED = [w for k, w in LOW_RISK_ROW_WORDS.items() if k not in ("add", "remove")]
LOW_RISK_CHANGES = "a card added or removed, or a new " + ", ".join(_CHANGED[:-1]) + " or " + _CHANGED[-1]
# The Archidekt account actions of actions.py, in the member's words.
ACCOUNT_ACTIONS_TEXT = "likes, votes, bookmarks, follows, comments, new folders and collaborator changes"
# What each mode means, in the member's words, for the Account page and the docs.
MODE_LABELS = {
    "manual": "Ask me every time",
    "semi": "Apply low-risk changes without asking",
    "auto": "Apply deck and collection changes without asking",
}
# Each mode's name, as the Guide and the Account page write it
MODE_NAMES = {"manual": "Manual", "semi": "Semi-auto", "auto": "Full auto"}


def _rows(max_rows: int) -> str:
    return f"{max_rows} card row{'s' if max_rows != 1 else ''}"


def low_risk_text(max_rows: int = DEFAULT_MAX_ROWS) -> str:
    """Which proposals are low risk (risk_of), as one sentence."""
    return (
        f"A small edit to an existing deck or to your collection: at most {_rows(max_rows)}, each "
        f"{LOW_RISK_CHANGES}, no more than {MAX_COPIES_PER_ROW} copies a row, never a change to which "
        "cards are the commanders or to the categories of a row in the commander zone, and never fewer "
        "copies of a card that has a row in the commander zone and another row, both in the deck or "
        "both in the maybeboard or sideboard; and a private copy of a deck (a clone), which changes "
        "nothing that exists."
    )


# Which proposals are high risk (risk_of), as one sentence.
HIGH_RISK_TEXT = (
    "Everything else: more rows than that, a commander change, a new deck, restoring a snapshot, "
    "the deck's details (name, format, bracket, description, visibility, folder, tags and cover), "
    "and its categories (a new category, a new name, Premier, Not in deck or Not in price, or "
    "deleting one)."
)
# What no mode lets an assistant apply (MEMBER_ONLY), as one sentence.
MEMBER_ONLY_TEXT = (
    f"Actions on your Archidekt account ({ACCOUNT_ACTIONS_TEXT}) and deleting a deck are never "
    "applied by an assistant: each one waits for your own press, in every mode, on the card in the "
    "chat or on its review page (deleting a deck, and an edit of a comment that has formatting: on "
    "the review page only). An edit of a description that has formatting is also approved on its "
    "review page only."
)


def auto_waits_text(big_rows: int = DEFAULT_BIG_EDIT_ROWS) -> str:
    """What still waits for a press in auto mode, as one sentence."""
    return (
        "Restoring a snapshot, a commander or companion change, a new deck, an edit of more than "
        f"{_rows(big_rows)}, making a deck public or unlisted, a category change that touches the "
        "commander or which cards count in the deck, and any edit of a description that has "
        "formatting, such as bold text, links or pictures, still wait for your press. So does a held "
        "change, a change waiting for another change first, one another app had a hand in, one that "
        "replaces another app's change, or one that another app's waiting change would replace."
    )


def mode_help(mode: str, *, max_rows: int = DEFAULT_MAX_ROWS, big_rows: int = DEFAULT_BIG_EDIT_ROWS) -> str:
    """What ``mode`` means, in the member's words, with this gateway's row limit for a low-risk edit."""
    if mode == "manual":
        return (
            "Every change waits for your own press on the card in the chat or on the review page, "
            "actions on your Archidekt account too (deleting a deck, and any edit of a description or "
            "comment that has formatting: on the review page only). The safest choice, and the default."
        )
    if mode == "semi":
        return (
            "Your assistant applies low-risk changes itself: a small edit to a deck or your collection "
            f"(at most {_rows(max_rows)}, each {LOW_RISK_CHANGES}, no more than {MAX_COPIES_PER_ROW} "
            "copies each, never a change to which cards are the commanders or to the categories of a "
            "row in the commander zone, or fewer copies of a card that has a row in the commander zone "
            "and another row, both in the deck or both in the maybeboard or sideboard) and a private "
            "copy of a deck (a clone, which "
            "changes nothing that exists). A new deck built from a list, restores, deck details and "
            "categories, commander "
            f"changes, bigger edits, and anything on your Archidekt account ({ACCOUNT_ACTIONS_TEXT}) "
            "or deleting a deck still wait for your press. So does a held change, a change waiting for "
            "another change first, one another app had a hand in, one that replaces another app's change, "
            "or one that another app's waiting change would replace."
        )
    return (
        "Your assistant applies the deck and collection changes it proposes, without asking you. "
        + auto_waits_text(big_rows)
        + " "
        + MEMBER_ONLY_TEXT
    )


# Shown next to the semi and auto choices, because turning them on has a real cost.
AUTO_WARNING = (
    "An assistant can be tricked by text it reads (a web page, a deck description, a pasted "
    "list) into making changes you did not ask for. If you let your assistant apply changes "
    "without asking, such a change can land on Archidekt without your press. A change to one of "
    "your decks still keeps a snapshot you can restore from the History page; a collection change "
    "keeps none. Restoring a snapshot does not put back a deck's folder, tags or cover."
)


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


# A change to the deck's own categories (kind "categories", decks.category_change_rows): touching
# the commander zone (Premier, the Commander category or one holding the commander) or which cards
# count in the deck always waits for the member's press, in Full auto too (auto_waits).
CATEGORY_COMMANDER_REASON = "changes the commander"
CATEGORY_COUNTS_REASON = "changes which cards count in the deck"
CATEGORY_REASON = "changes the deck's categories"
# The deck settings page's confirm box for a commander category change (NEW-WORDING.md)
CATEGORY_COMMANDER_CONFIRM = (
    "Cards in a Premier category are the deck's commanders. Changing it can change which cards "
    "Archidekt treats as the commander, the deck's legality and its colours."
)
# ... and for a change to which cards count in the deck (Not in deck, or a rename to or from
# Sideboard or Maybeboard): an assistant's version of either waits, so the member's own asks too
CATEGORY_COUNTS_CONFIRM = (
    "Cards in a Not in deck category, or in one named Sideboard or Maybeboard, don't count in the "
    "deck. Changing it can change the deck's card count and its legality."
)
# ... and for deleting a category (archidekt.com asks "Are you sure" too): the words name each one
CATEGORY_DELETE_CONFIRM = (
    "Deleting a category takes it off the deck on Archidekt. A snapshot from just before is kept "
    "under History, and Restore there makes it again."
)
CATEGORY_CONFIRM_TITLES = {
    CATEGORY_COMMANDER_CONFIRM: "Change the commander category?",
    CATEGORY_COUNTS_CONFIRM: "Change which cards count in the deck?",
    CATEGORY_DELETE_CONFIRM: "Delete the category?",
}


def category_wait(rows: list[dict[str, Any]] | None) -> str | None:
    """Why a categories change waits for the member's press in every mode, or None."""
    if rows is None:
        return "has no list of rows to check"
    rows = [r for r in rows if isinstance(r, dict)]
    if any(r.get("commander") or "premier_after" in r for r in rows):
        return CATEGORY_COMMANDER_REASON
    if any(r.get("counts") or "not_in_deck_after" in r for r in rows):
        return CATEGORY_COUNTS_REASON
    return None


COMMANDER_COPIES_REASON = "changes the commander's copies"
COMPANION_REASON = "changes the deck's companion"
COMMANDER_ZONE_REASON = "adds or changes a row with a commander-zone category"
# Fewer copies of a card whose commander-zone row is in the maybeboard or sideboard, with another
# row there: no commander (its row is not in the deck), but the write folds its rows like a
# commander's, so it waits as well (the safe default; it could apply as low risk instead).
ZONE_COPIES_REASON = "changes the copies of a card with a row in the commander zone"


def commander_reason(rows: list[dict[str, Any]]) -> str:
    """Why a proposal's commander rows make it a commander change: the commanders change, a
    row in the commander zone changes its categories (``moved``), or the commanders stay and a
    commander's other rows go (``stays``)."""
    rows_c = [r for r in rows if r.get("kind") == "commander"]
    if not rows_c or any(not (r.get("stays") or r.get("moved")) for r in rows_c):
        return "changes the commander"
    if any(r.get("moved") for r in rows_c):
        return COMMANDER_ZONE_REASON
    stays = [x for r in rows_c for x in r.get("stays") or [] if isinstance(x, dict)]
    if stays and all(x.get("zone") == "side" for x in stays):
        return ZONE_COPIES_REASON
    return COMMANDER_COPIES_REASON


def risk_of(
    kind: str, rows: list[dict[str, Any]] | None, *, max_rows: int = DEFAULT_MAX_ROWS
) -> tuple[str, str]:
    """``("low" | "high", reason)`` for a proposal, from its kind and stored review rows."""
    if kind == "clone":
        return "low", "copies a deck and changes nothing that exists"
    if kind == "action":
        from .actions import risk_of as action_risk

        return action_risk(rows)
    if kind == "create_deck":
        return "high", "creates a new deck"
    if kind == "restore":
        return "high", "restores a snapshot"
    if kind == "details":
        return "high", "changes the deck's details"
    if kind == "categories":
        return "high", category_wait(rows) or CATEGORY_REASON
    if kind not in ("edit", "collection"):
        return "high", "not a plain deck or collection edit"
    if rows is None:  # stored before review rows existed: nothing to judge it by
        return "high", NO_ROWS_REASON
    kinds = [str(r.get("kind")) for r in rows]
    if any(k == "commander" for k in kinds):
        return "high", commander_reason(rows)
    if any(r.get("companion_card") for r in rows):
        return "high", COMPANION_REASON
    if any(k not in LOW_RISK_ROW_KINDS for k in kinds):
        return (
            "high",
            "contains a change that is not a card add, remove, quantity, category, finish, printing, "
            "color tag or custom mana value",
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
    when it can go straight to Archidekt (with its snapshot, as every apply to a deck takes one).
    Restores never come here: they have their own review page."""
    if kind == "categories":  # the deck settings page's Save categories
        # every reason an assistant's version would wait on (category_wait) asks here too
        wait = category_wait(rows or [])
        if wait == CATEGORY_COMMANDER_REASON:
            return CATEGORY_COMMANDER_CONFIRM
        if wait:
            return CATEGORY_COUNTS_CONFIRM
        deletes = any(isinstance(r, dict) and r.get("delete") for r in rows or [])
        return CATEGORY_DELETE_CONFIRM if deletes else None
    if kind != "edit" or not rows:
        return None
    if any(r.get("kind") == "commander" for r in rows):
        why = commander_reason(rows)
        if why == COMMANDER_ZONE_REASON:
            return "This adds or changes a row with a commander-zone category."
        if why == COMMANDER_COPIES_REASON:
            return "This changes the copies of the deck's commander."
        if why == ZONE_COPIES_REASON:
            return "This changes the copies of a card with a row in the commander zone."
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


def auto_waits(
    kind: str,
    rows: list[dict[str, Any]] | None,
    *,
    big_rows: int = DEFAULT_BIG_EDIT_ROWS,
    changes: dict[str, Any] | None = None,
) -> str | None:
    """Why a proposal waits for the member's press even in auto mode, or None when auto
    applies it: a restore, a new deck, a commander change, or an edit (deck or collection) of
    more than ``big_rows`` rows; and of the deck's details making it public or
    unlisted (from any other visibility, a safe default), a description
    edit that keeps formatting (seen on its review page first) and one that removes all of
    it (the same). Other details changes and clones apply in auto."""
    if kind == "restore":
        return "restores a snapshot"
    if kind == "create_deck":
        return "creates a new deck"
    if kind in ("edit", "collection"):
        if rows is None:
            return "has no list of rows to check"
        if any(str(r.get("kind")) == "commander" for r in rows):
            return commander_reason(rows)
        if any(str(r.get("kind")) == "companion" or r.get("companion_card") for r in rows):
            return COMPANION_REASON  # it changes what the deck is legal with (parity release 1)
        if len(rows) > big_rows:
            return f"changes {len(rows)} rows, more than the {big_rows} Full auto applies by itself"
        return None
    if kind == "details":
        wanted = changes if isinstance(changes, dict) else {}
        rank = {"private": 0, "unlisted": 1, "public": 2}
        seen = next(
            (
                r
                for r in rows or []
                if isinstance(r, dict) and r.get("seen_after") in rank and r.get("seen_before") in rank
            ),
            None,
        )
        if seen is not None:
            # any move to unlisted or public waits, public to unlisted too; to private applies
            if seen["seen_after"] != "private" and seen["seen_after"] != seen["seen_before"]:
                return f"makes the deck {seen['seen_after']}"
        elif wanted.get("private") is False or wanted.get("unlisted") is False:
            return "may let more people see the deck"  # stored before the rows said who sees it
        elif wanted.get("unlisted") is True:
            return "makes the deck unlisted"
        if any(isinstance(r, dict) and r.get("after_doc") for r in rows or []):
            return (
                "keeps the description's formatting, such as bold text, links or pictures, so you see how "
                "it will look first"
            )
        if any(isinstance(r, dict) and r.get("formatting_lost") for r in rows or []):
            return "removes all of the description's formatting, such as bold text, links and pictures"
        return None
    if kind == "categories":
        # a rename or Not in price applies in Full auto, as other deck details do; the commander
        # zone and which cards count in the deck never do
        return category_wait(rows)
    if kind == "clone":
        return None
    return "is not a deck or collection change"


def assistant_may_apply(mode: str, risk: str, *, auto_wait: str | None = None) -> bool:
    """Whether the member's mode lets their assistant apply a proposal of this risk itself.
    ``auto_wait`` (``auto_waits``) is why auto mode still waits for the member's press."""
    if risk in MEMBER_ONLY:  # the member's own press on each one, in every mode
        return False
    return (mode == "auto" and not auto_wait) or (mode == "semi" and risk == "low")
