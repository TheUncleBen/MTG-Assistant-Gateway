"""0.7.29: the waiting changes as a pin board the assistant can read and tidy (BOARD design,
Q27j-Q27m, O9, G5, G6, G16).

Every waiting proposal ("note") has a number on its deck's board, a version and links ("depends
on #1", "replaces #2"). The assistant sees the whole board of a deck (``lines``), is told card by
card where a new request overlaps a waiting note (``overlaps``), and tidies with amend, replace,
withdraw and combine (DeckService methods below, MCP tools in tools_decks.py). The gateway does
only one thing by itself (Q27k): an exact duplicate from the same app is not saved again.

Holds (Q27j, K6): a note that depends on a note that is rejected, withdrawn or fails is held, so
no assistant applies it in any mode (the member still may, A8); the hold lifts when that note is
applied or comes back. A note replaced by another comes back when its replacement is rejected or
withdrawn (Q27l)."""

from __future__ import annotations

import logging
from typing import Any

from .db import HOLDING_STATES, hold_reason

logger = logging.getLogger(__name__)

SOFT_PER_DECK = 20  # G6, Q27o: a hint only, never a refusal
WHAT_CHANGED_MAX = 400

# How a review row reads in an overlap line ("#2 removes it").
_VERBS = {
    "add": lambda r: f"adds {r.get('qty') or 1}",
    "remove": lambda r: "removes it",
    "change": lambda r: f"changes its count from {r.get('before')} to {r.get('after')}",
    "category": lambda r: "moves its category",
    "finish": lambda r: "changes its finish",
    "printing": lambda r: "changes its printing",
    "label": lambda r: "changes its color tag",
    "mana_value": lambda r: "changes its custom mana value",
    "commander": lambda r: "changes the commander",
}


def _verb(r: dict[str, Any]) -> str:
    f = _VERBS.get(str(r.get("kind")))
    return f(r) if f else "changes it"


def _cards(rows: list[dict[str, Any]] | None) -> dict[str, dict[str, Any]]:
    """Card name (case-folded) -> its first row, for the rows that name a card."""
    out: dict[str, dict[str, Any]] = {}
    for r in rows or []:
        name = str(r.get("name") or "").strip()
        if name and r.get("kind") in _VERBS:
            out.setdefault(name.casefold(), r)
    return out


def overlaps(
    waiting: list[dict[str, Any]], rows: list[dict[str, Any]] | None, numbers: dict[str, Any]
) -> list[str]:
    """One line per card that a new request and a waiting note both touch (BOARD §2):
    "Sol Ring: #2 changes its printing; this request removes it." ``waiting`` are full proposal
    rows (with ``rows``), oldest first; ``numbers`` maps their ids to board numbers."""
    mine = _cards(rows)
    out: list[str] = []
    for note in waiting:
        theirs = _cards(note.get("rows"))
        for key, r in mine.items():
            other = theirs.get(key)
            if other is None:
                continue
            no = numbers.get(note["id"])
            label = f"#{no}" if no else "another waiting change"
            out.append(f"{r.get('name')}: {label} {_verb(other)}; this request {_verb(r)}.")
    return out


def what_changed(old_rows: list[dict[str, Any]] | None, new_rows: list[dict[str, Any]] | None) -> str:
    """The gateway's own line for an amended note (A4 rec): what the new version does that the
    old one did not, and what it no longer does, from the rows (an assistant's words never appear
    here, so a note cannot describe itself wrongly)."""
    from .decks import row_line

    def clean(rows: list[dict[str, Any]] | None) -> list[str]:
        return [row_line({k: v for k, v in r.items() if k != "guessed_from"}) for r in rows or []]

    old, new = clean(old_rows), clean(new_rows)
    bits = [f"now {line}" for line in new if line not in old]
    bits += [f"no longer {line}" for line in old if line not in new]
    text = "; ".join(bits) or "the same changes"
    return text if len(text) <= WHAT_CHANGED_MAX else text[: WHAT_CHANGED_MAX - 3] + "..."


def settle(svc: Any, sub: str, proposal_id: str, state: str) -> None:
    """After a note was applied, rejected, withdrawn, failed or came back: hold the notes that
    depend on it, or lift their hold (K6), and bring back what a rejected or withdrawn
    replacement replaced (Q27l). Never raises: a link it cannot settle is logged."""
    try:
        db = svc.db
        if state in HOLDING_STATES:
            for dep in db.dependents(sub, proposal_id):
                note = db.get_proposal(proposal_id, sub) or {}
                db.set_hold(
                    dep,
                    sub,
                    hold_reason({"id": proposal_id, "board_no": note.get("board_no"), "state": state}),
                )
            if state in ("rejected", "withdrawn"):
                for link in db.proposal_links(sub, [proposal_id]):
                    if link["proposal_id"] == proposal_id and link["kind"] == "replaces":
                        old = db.get_proposal(link["other_id"], sub)
                        if old is not None and old["state"] == "replaced":
                            if db.reopen_proposal(old["id"], sub, max_pending=svc.max_pending()):
                                settle(svc, sub, old["id"], "pending")
        elif state in ("applied", "pending"):
            for dep in db.dependents(sub, proposal_id):
                row = db.get_proposal(dep, sub)
                if row and row.get("status") == "held" and not _still_blocked(db, sub, dep):
                    db.set_hold(dep, sub, None)
    except Exception:
        logger.exception("settling the links of a proposal failed")


def status_text(db: Any, sub: str, row: dict[str, Any]) -> str:
    """A waiting note's status in words, for the pages, the card and the assistant: why it is
    "held" (Q27j; Q134 A8 "which you rejected"), what "updated" means, or why it "needs a look"
    (recheck.py)."""
    from . import recheck

    status = row.get("status")
    reason = str(row.get("status_reason") or "")
    if status == "held" and reason.startswith("depends:"):
        _tag, other, closed = (reason.split(":", 2) + ["", ""])[:3]
        no = db.proposal_board_numbers(sub, [other]).get(other)
        words = {"rejected": "you rejected", "withdrawn": "was withdrawn", "failed": "failed"}
        which = f"#{no}" if no else "another proposal"
        return f"It depends on {which}, which {words.get(closed, closed)}."
    if status == "updated":
        return "The deck changed since this was made; it still does exactly what was reviewed."
    return recheck.reason_text(reason)


def _still_blocked(db: Any, sub: str, proposal_id: str) -> bool:
    for link in db.proposal_links(sub, [proposal_id]):
        if link["proposal_id"] == proposal_id and link["kind"] == "depends_on":
            other = db.get_proposal(link["other_id"], sub)
            if other is not None and other["state"] in HOLDING_STATES:
                return True
    return False


def links_text(db: Any, sub: str, proposal_id: str) -> list[dict[str, Any]]:
    """A note's links for the board: {kind, proposal_id, board_no, state}, both directions
    ("depends_on", "replaces", "needed_by", "replaced_by")."""
    out: list[dict[str, Any]] = []
    links = db.proposal_links(sub, [proposal_id])
    others = {lk["other_id"] if lk["proposal_id"] == proposal_id else lk["proposal_id"] for lk in links}
    numbers = db.proposal_board_numbers(sub, sorted(others))
    for lk in links:
        mine = lk["proposal_id"] == proposal_id
        other = lk["other_id"] if mine else lk["proposal_id"]
        kind = lk["kind"] if mine else {"depends_on": "needed_by", "replaces": "replaced_by"}[lk["kind"]]
        row = db.get_proposal(other, sub) or {}
        out.append(
            {"kind": kind, "proposal_id": other, "board_no": numbers.get(other), "state": row.get("state")}
        )
    return out
