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

The catalog leaves tokens out, so ``TOKEN_NAMES`` (the tokens cards make most often) is added to it:
Add to binder, Watch a card and Add to deck then suggest "Treasure" or "Soldier" too. A token comes
after the cards that match (typing "sol" still offers Sol Ring first), unless it is the whole text.
"""

from __future__ import annotations

import asyncio
import logging
import time
import unicodedata
from collections import OrderedDict
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("mtg_gateway.scan.names")

CATALOG_TTL = 24 * 3600
RETRY_AFTER_FAILURE = 600
MAX_SUGGESTIONS = 20
# Typing extends the text one letter at a time, and every name that contains "sol r" also
# contains "sol ": the catalog positions matching the last few texts are kept, so the next
# keystroke searches only those instead of all ~35,000 names.
NARROW_KEEP = 64


# Tokens by the name Scryfall gives them: the artifacts every set makes, then the creature tokens
# seen most. Not every token: a name not here can still be typed in full.
TOKEN_NAMES = tuple(
    n.strip()
    for n in """
Treasure, Food, Clue, Blood, Map, Powerstone, Gold, Shard, Junk, Incubator, Walker, Lander,
Mutagen, Gift, Copy, Emblem, The Monarch, The Initiative, City's Blessing, Day // Night,
Energy Reserve, Poison Counter, Eldrazi Spawn, Eldrazi Scion, Servo, Thopter, Construct, Myr,
Golem, Germ, Saproling, Soldier, Spirit, Zombie, Zombie Army, Goblin, Beast, Elf Warrior,
Elf Druid, Insect, Squirrel, Rat, Bird, Cat, Dog, Wolf, Bear, Boar, Elephant, Ox, Rhino,
Dinosaur, Dragon, Angel, Demon, Devil, Human, Human Soldier, Human Warrior, Human Cleric, Knight,
Warrior, Cleric, Wizard, Rogue, Pirate, Vampire, Faerie, Faerie Rogue, Elemental, Plant,
Treefolk, Ooze, Hydra, Wurm, Snake, Fish, Octopus, Kraken, Shark, Horror, Illusion, Shapeshifter,
Satyr, Centaur, Gnome, Monk, Samurai, Ninja, Rabbit, Bat, Frog, Lizard, Mouse, Otter, Raccoon,
Hippo, Spider, Scarecrow, Gargoyle, Phyrexian Germ, Phyrexian Mite, Phyrexian Golem, Pest,
Inkling, Fractal, Spirit Cleric, Wall, Kobold, Dwarf, Orc Army, Citizen, Detective, Mercenary,
Survivor, Assassin
""".split(",")
)

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
        self._hits: OrderedDict[str, list[int]] = OrderedDict()
        self._known: set[str] | None = None
        self._token: list[bool] = []

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
        cards = set(names)
        self._names = sorted(cards | set(TOKEN_NAMES), key=_sort_key)
        self._folded = [fold(n) for n in self._names]
        self._token = [n not in cards for n in self._names]
        self._hits.clear()
        self._known = None
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

    def known(self, name: str) -> bool | None:
        """Whether ``name`` is a card in the catalog, by its full name or one face of a double-faced
        or split card; ``None`` while the catalog is not loaded, so a caller never refuses a card it
        cannot check."""
        if not self._names:
            self.ensure()
            return None
        if self._known is None:
            faces = {fold(f) for n in self._names if " // " in n for f in n.split(" // ")}
            self._known = set(self._folded) | faces
        return fold(name) in self._known or fold(name.split(" // ", 1)[0]) in self._known

    def suggest(self, query: str, *, limit: int = MAX_SUGGESTIONS) -> list[str] | None:
        """Up to ``limit`` names for ``query`` (at least two characters), or ``None`` when the
        catalog is not loaded yet."""
        if not self._names:
            return None
        q = fold(query)
        if len(q) < 2:
            return []
        hits = self._matching(q)
        starts: list[str] = []
        words: list[str] = []
        inside: list[str] = []
        tokens: list[str] = []
        for i in hits:
            if self._token[i]:
                # a token typed in full comes first; otherwise after the cards
                (starts if self._folded[i] == q else tokens).append(self._names[i])
                continue
            where = rank(self._folded[i], q)
            if where is None:  # cannot happen: _matching only keeps names containing q
                continue
            (starts, words, inside)[where].append(self._names[i])
            if len(starts) >= limit:
                break
        exact = [n for n in starts if fold(n) == q]
        rest = [n for n in starts if fold(n) != q]
        return (exact + rest + words + inside + tokens)[:limit]

    def _matching(self, q: str) -> list[int]:
        """Catalog positions of every name containing ``q``, in catalog order. Starts from the
        kept positions of the longest earlier text that ``q`` contains, else the whole catalog."""
        known = self._hits.get(q)
        if known is not None:
            self._hits.move_to_end(q)
            return known
        base: list[int] | None = None
        for prev, idx in self._hits.items():
            if prev in q and (base is None or len(idx) < len(base)):
                base = idx
        folded = self._folded
        if base is None:
            out = [i for i, f in enumerate(folded) if q in f]
        else:
            out = [i for i in base if q in folded[i]]
        self._hits[q] = out
        if len(self._hits) > NARROW_KEEP:
            self._hits.popitem(last=False)
        return out
