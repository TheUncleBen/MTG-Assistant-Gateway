"""0.7.29 flexible approvals: a waiting change checked again against the deck as it is now.

A waiting change records the deck it was made from (its baseline fingerprint). Before 0.7.29 any
change to that deck refused the apply. Now the change is worked out again on the live deck: when
that gives exactly the rows that were reviewed (the same cards, counts, categories, printings,
tags and details), the change still does what the member saw, so it may be sent, and it shows as
"Updated" with its baseline moved to the deck of now. Otherwise it stays waiting as "Needs a look"
with the first difference as its reason, and nothing is sent.

Worked out again: card edits, restores and deck details (with folder, tags and cover when the
change recorded where they were). Kept strict (any change to the deck stops it): copies of a deck
and deck deletions, and details changes stored before 0.7.29 that move a folder, tags or cover.

Everything here is local: the deck has already been read by whoever calls it (an apply, a deck
page, a new proposal, the deck sync), so a check costs no Archidekt request."""

from __future__ import annotations

from typing import Any

from . import decks as d
from .archidekt import Deck

#: Stored reasons of a "needs a look" status; shown in words by ``reason_text``.
REASON_CHANGED = "changed"  # a strict kind: the deck changed at all
REASON_DURING_APPLY = "during_apply"  # the deck changed while an apply was getting ready

ORGANISE_ROW_FIELDS = ("folder", "tags", "cover")


def organise_was(deck: Deck) -> dict[str, Any]:
    """Where a deck's folder, tags and cover are now, stored with a details change that moves
    them (``changes["organise"]["was"]``), so a later check can tell whether they moved since."""
    return {
        "folder": deck.parent_folder,
        "tags": sorted(str(r.get("name") or "").casefold() for r in deck.tag_relations),
        "cover": str(deck.raw.get("customFeatured") or "").lower(),
    }


def _clean(rows: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Rows as compared: without the scan's name-guess warning, which the save adds."""
    return [{k: v for k, v in r.items() if k != "guessed_from"} for r in rows or []]


def rows_now(svc: Any, sub: str, row: dict[str, Any], deck: Deck) -> list[dict[str, Any]] | None:
    """The review rows the stored change gives on ``deck``; None when its kind is kept strict.
    Raises DeckError when the change can no longer be worked out on that deck."""
    kind = row.get("kind") or "edit"
    if kind == "edit":
        changes = d.parse_changes(row["changes"], row_flags=True)
        return d.plan_rows(deck, changes)[2]
    if kind == "restore":
        snap = svc.db.get_snapshot(str(row["changes"]["snapshot_id"]), sub)
        if snap is None:
            raise d.DeckError("not_found", "The snapshot this proposal restores no longer exists.")
        wanted = d.parse_deck(snap["deck"])
        payload, lines = d.restore_rows(wanted, deck)
        _details, detail_lines = d.details_rows(deck, d.snapshot_details(wanted))
        head = next((r for r in row.get("rows") or [] if r.get("kind") == "restore"), None)
        first = dict(head or {})
        first["rows"] = len(payload)
        return [first, *lines, *detail_lines]
    if kind == "details":
        stored = dict(row["changes"])
        organise = stored.pop("organise", None) or {}
        if organise and "was" not in organise:
            return None  # stored before 0.7.29: no record of where they were
        wanted = d.parse_details(stored) if stored else {}
        _changes, rows = d.details_rows(deck, wanted)
        if organise and organise_was(deck) != organise["was"]:
            raise d.DeckError("stale", "The deck's folder, tags or cover changed since.")
        kept = [r for r in row.get("rows") or [] if r.get("field") in ORGANISE_ROW_FIELDS]
        return [*rows, *kept]
    return None


def check(svc: Any, sub: str, row: dict[str, Any], deck: Deck) -> tuple[str | None, str | None]:
    """(status, reason) of a waiting change against ``deck``: (None, None) when the deck is the
    one it was made from, ("updated", None) when it still does exactly what was reviewed, else
    ("needs_look", reason)."""
    if deck.fingerprint() == row.get("baseline_fingerprint"):
        return None, None
    try:
        now = rows_now(svc, sub, row, deck)
    except d.DeckError as exc:
        return "needs_look", f"refused:{exc}"
    if now is None:
        return "needs_look", REASON_CHANGED
    was = _clean(row.get("rows"))
    now = _clean(now)
    if now == was:
        return "updated", None
    return "needs_look", "rows:" + first_difference(was, now)


def first_difference(was: list[dict[str, Any]], now: list[dict[str, Any]]) -> str:
    """'<line as reviewed> | <line it would be now>' for the first row that differs (either side
    may be empty: a row gone, or a new one)."""
    for i in range(max(len(was), len(now))):
        a = was[i] if i < len(was) else None
        b = now[i] if i < len(now) else None
        if a != b:
            return f"{_line(a) if a else ''} | {_line(b) if b else ''}"
    return " | "


def _line(row: dict[str, Any]) -> str:
    """A row's line, with who can see the deck for a private or unlisted row, so a change of
    visibility alone does not read as two identical lines."""
    line = d.row_line(row)
    before, after = row.get("seen_before"), row.get("seen_after")
    if not before or not after:
        return line
    return line + (
        f", so the deck stays {after}" if before == after else f", so the deck goes from {before} to {after}"
    )


def reason_text(reason: str | None) -> str:
    """A stored "needs a look" or hold reason in words, for the pages, the card and the
    assistant."""
    r = str(reason or "")
    if r == REASON_CHANGED:
        return "The deck changed on Archidekt since this was made."
    if r == REASON_DURING_APPLY:
        return "The deck changed on Archidekt while this was being applied."
    if r.startswith("refused:"):
        return "It no longer fits the deck: " + r[len("refused:") :].strip()
    if r.startswith("rows:"):
        was, _sep, now = r[len("rows:") :].partition(" | ")
        was, now = was.strip(), now.strip()
        if was and now:
            return f"The deck changed. Reviewed: {was}. Now it would be: {now}."
        if was:
            return f"The deck changed. Reviewed: {was}. Now that line would not change anything."
        return f"The deck changed. Now it would also: {now}."
    return r
