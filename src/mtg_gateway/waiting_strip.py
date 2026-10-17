"""The deck page's "Waiting changes" strip: the member's changes waiting for approval on the deck
they are looking at (PROPOSALS-ASSISTANT-BOARD.md). Owned by the approvals work.

The deck route (companion_decks.py) asks ``waiting_changes`` for the strip's data and hands it to
``deckpage.deck_page_html`` as ``waiting``; the strip itself is drawn there. Until the strip is
built this returns nothing, so the deck page is unchanged.
"""

from __future__ import annotations

from typing import Any


def waiting_changes(db: Any, sub: str, deck_id: int | str, *, own: bool) -> list[dict[str, Any]]:
    """The strip's rows for this member and deck (``Database.pending_board`` has the data), oldest
    first; empty for now."""
    return []
