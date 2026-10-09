"""Typed card-name suggestions answered by the gateway itself, in milliseconds.

Scryfall's ``/cards/autocomplete`` is accurate but every keystroke cost a round trip through the
gateway's paced Scryfall lock (one lookup each 500 ms), so a typed name took seconds to suggest.
Instead the gateway keeps Scryfall's whole card-name catalog (``/catalog/card-names``, one request
of about 700 KB, ~35,000 names) in memory for a day and matches typed text against it locally,
ranking the way Scryfall's autocomplete does: names that start with the text, then names with a
word that starts with it, then names that merely contain it, alphabetical within each group.
Accents are ignored both ways (``lim-dul`` finds ``Lim-Dûl``).

The catalog loads in the background (first use, and refreshed when older than a day); until it has
loaded, ``suggest`` answers ``None`` and the caller falls back to Scryfall's autocomplete.
"""

from __future__ import annotations

import asyncio
import logging
import time
import unicodedata
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("mtg_gateway.scan.names")

CATALOG_TTL = 24 * 3600
RETRY_AFTER_FAILURE = 600
MAX_SUGGESTIONS = 20


_JOINERS = str.maketrans("-'\u2019,", "    ")


def fold(text: str) -> str:
    """Lower-case, accent-free form for matching; hyphens, apostrophes and commas count as spaces
    so "lim dul" finds "Lim-Dûl's Vault" (static/suggest.js folds the same way)."""
    nfkd = unicodedata.normalize("NFKD", text)
    plain = "".join(c for c in nfkd if not unicodedata.combining(c)).casefold()
    return " ".join(plain.translate(_JOINERS).split())


def rank(folded: str, q: str) -> int | None:
    """0 when ``folded`` starts with ``q``, 1 when a word of it does, 2 when ``q`` only appears
    inside a word, None when it does not appear."""
    at = folded.find(q)
    if at < 0:
        return None
    if at == 0:
        return 0
    while at > 0 and folded[at - 1].isalnum():
        at = folded.find(q, at + 1)
        if at < 0:
            return 2
    return 1


def _sort_key(name: str) -> tuple[str, str]:
    # Punctuation does not order names ("Shelob, Child" before "Shelob's Ambush"): letters first.
    folded = fold(name)
    return ("".join(c for c in folded if c.isalnum() or c == " "), folded)


class NameCatalog:
    def __init__(self, scryfall: Callable[[], Any], *, ttl: float = CATALOG_TTL):
        # A getter, because the scan service's Scryfall client is replaced in tests after construction.
        self._scryfall = scryfall
        self.ttl = ttl
        self._names: list[str] = []
        self._folded: list[str] = []
        self._loaded_at = 0.0
        self._next_try = 0.0
        self._task: asyncio.Task[None] | None = None

    @property
    def loaded(self) -> bool:
        return bool(self._names)

    @property
    def size(self) -> int:
        return len(self._names)

    @property
    def age(self) -> float:
        return time.monotonic() - self._loaded_at if self._names else float("inf")

    def set_names(self, names: list[str]) -> None:
        """Install a catalog (also used by tests and for a fresh download)."""
        self._names = sorted(set(names), key=_sort_key)
        self._folded = [fold(n) for n in self._names]
        self._loaded_at = time.monotonic()

    def ensure(self) -> None:
        """Start a background (re)load when the catalog is missing or a day old. Never blocks the
        caller; a failed download is retried after ten minutes."""
        fresh = self._names and self.age < self.ttl
        if fresh or time.monotonic() < self._next_try:
            return
        if self._task is not None and not self._task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._next_try = time.monotonic() + RETRY_AFTER_FAILURE
        self._task = loop.create_task(self.load())

    def close(self) -> None:
        """Cancel a download still running (called when the gateway shuts down)."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
        self._task = None

    async def load(self) -> bool:
        try:
            names = await self._scryfall().catalog_card_names()
        except Exception as exc:  # any failure: keep the old list (or none) and retry later
            logger.warning("card-name catalog not loaded (%s); suggestions use Scryfall meanwhile", exc)
            return False
        self.set_names(names)
        self._next_try = 0.0
        logger.info("card-name catalog loaded: %d names", len(self._names))
        return True

    def suggest(self, query: str, *, limit: int = MAX_SUGGESTIONS) -> list[str] | None:
        """Up to ``limit`` names for ``query`` (at least two characters), or ``None`` when the
        catalog is not loaded yet."""
        if not self._names:
            return None
        q = fold(query)
        if len(q) < 2:
            return []
        starts: list[str] = []
        words: list[str] = []
        inside: list[str] = []
        for name, folded in zip(self._names, self._folded, strict=True):
            where = rank(folded, q)
            if where is None:
                continue
            (starts, words, inside)[where].append(name)
            if len(starts) >= limit:
                break
        return (starts + words + inside)[:limit]
