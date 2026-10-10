"""Archidekt provider adapter.

Endpoints come from the handoff's endpoint map (third-party source reading, not
Archidekt documentation). Nothing here has been run against archidekt.com; the
tests use recorded fixtures. Every response is validated and the adapter fails
closed on an unexpected shape. Requests go through one global pacer (minimum
interval between calls, Retry-After honoured, circuit breaker after repeated
failures) so a chatty assistant cannot hammer the provider.
"""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import http.cookiejar
import json
import logging
import random
import re
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from .timing import add_time, archidekt_time

logger = logging.getLogger(__name__)

TOKEN_FIELDS = ("access_token", "access", "token", "jwt")
# A member's vote on a deck as its JSON reports it ("userInput"; Archidekt's site bundle maps
# NONE:0, UP:1, DOWN:2).
VOTE_NONE, VOTE_UP, VOTE_DOWN = 0, 1, 2
# Collection "modifier" values are the deck ones: Normal, Foil, Etched.
COLLECTION_PAGE_SIZE = 100

# Archidekt's numeric deck formats as observed by the nccurry/mtg-mcp reference (reported, not verified).
# Archidekt's deckFormat ids, read from the site's own client code on 2026-10-08 (its format
# slugs are the keys; they are also the keys of each card's ``legalities``). "edh" is an alias.
FORMAT_IDS = {
    "standard": 1,
    "modern": 2,
    "commander": 3,
    "edh": 3,
    "legacy": 4,
    "vintage": 5,
    "pauper": 6,
    "custom": 7,
    "frontier": 8,
    "future": 9,
    "penny": 10,
    "1v1": 11,
    "duel": 12,
    "brawl": 13,
    "oathbreaker": 14,
    "pioneer": 15,
    "historic": 16,
    "paupercommander": 17,
    "alchemy": 18,
    "historicbrawl": 20,
    "gladiator": 21,
    "premodern": 22,
    "predh": 23,
    "timeless": 24,
    "canlander": 25,
    "competitivebrawl": 26,
    "tlr": 27,
}
# What Archidekt calls each format on screen (its own labels).
FORMAT_LABELS = {
    "commander": "Commander",
    "edh": "Commander",
    "1v1": "1v1 Commander",
    "duel": "Duel Commander",
    "brawl": "Standard Brawl",
    "historicbrawl": "Brawl",
    "competitivebrawl": "Competitive Brawl",
    "paupercommander": "Pauper EDH",
    "penny": "Penny Dreadful",
    "future": "Future Standard",
    "canlander": "Canadian Highlander",
    "predh": "PreDH",
    "tlr": "Tiny Leaders Reborn",
}


def format_label(slug: str | None) -> str:
    """The on-screen name of a format slug ("historicbrawl" -> "Brawl"); unknown -> "Custom"."""
    if not slug:
        return "Custom"
    return FORMAT_LABELS.get(slug) or slug.capitalize()


# Reverse map, one name per id (3 reads back as "commander", not "edh").
FORMAT_NAMES: dict[int, str] = {}
for _name, _fid in FORMAT_IDS.items():
    FORMAT_NAMES.setdefault(_fid, _name)
FORMAT_NAMES[19] = "pioneer"  # Explorer, folded into Pioneer by Archidekt
REFRESH_FIELDS = ("refresh_token", "refresh")
# Sort orders archidekt.com/search/decks offers (its Updated At, Created At, Views, Size, EDH Bracket menu).
SEARCH_ORDERS = {
    "-updatedAt": "Updated at",
    "-createdAt": "Created at",
    "-viewCount": "Views",
    "-size": "Size",
    "edhBracket": "EDH bracket",
}
_ART_UUID = re.compile(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def art_url(scryfall_uid: str) -> str:
    """The card-art URL Archidekt stores as a deck's ``featured`` cover: its art images live at
    card-images.archidekt.com/art/front/<u[0]>/<u[1]>/<uid>.webp (the pattern of every ``featured``
    in the live precon listing, 2026-10-07; a path with other characters is refused)."""
    uid = str(scryfall_uid or "").lower()
    if not re.fullmatch(r"[0-9a-f-]{36}", uid):
        raise ArchidektError("invalid", "not a Scryfall card id")
    return f"https://card-images.archidekt.com/art/front/{uid[0]}/{uid[1]}/{uid}.webp"


def featured_scryfall_id(url: Any) -> str | None:
    """The Scryfall id inside a listing's ``featured`` art URL (Archidekt names its art files by the
    card's Scryfall id, seen live 2026-10-07 on both of its image hosts), so the gateway can show the
    same art from Scryfall and never hotlink Archidekt's storage."""
    if not isinstance(url, str):
        return None
    m = _ART_UUID.search(url)
    return m.group(1) if m else None


def list_row(d: dict[str, Any]) -> dict[str, Any]:
    """One deck of a ``/decks/v3/`` listing in the gateway's list shape (the extras were verified
    in the public v3 listing 2026-10-05: colour-identity pip counts, size, bracket, tag names)."""
    fmt = d.get("deckFormat")
    colors = d.get("colors") if isinstance(d.get("colors"), dict) else {}
    raw_tags = d.get("tags") if isinstance(d.get("tags"), list) else []
    bracket = d.get("edhBracket")
    size = d.get("size")
    folder = d.get("parentFolderName")
    views = d.get("viewCount")
    return {
        "id": str(d["id"]),
        "name": str(d.get("name", "")),
        "format": fmt,
        "format_name": FORMAT_NAMES.get(fmt) if isinstance(fmt, int) else None,
        "updated_at": str(d.get("updatedAt", "")),
        "created_at": str(d.get("createdAt", "")),
        "private": bool(d.get("private", False)),
        "unlisted": bool(d.get("unlisted", False)),
        "folder": str(folder) if isinstance(folder, str) and folder else None,
        "colors": {k: v for k, v in colors.items() if k in "WUBRG" and isinstance(v, int)},
        "size": size if isinstance(size, int) and not isinstance(size, bool) else None,
        "bracket": bracket if isinstance(bracket, int) and not isinstance(bracket, bool) else None,
        "tags": [
            str(t.get("name") or t.get("tag"))
            for t in raw_tags
            if isinstance(t, dict) and (t.get("name") or t.get("tag"))
        ],
        "views": views if isinstance(views, int) and not isinstance(views, bool) else None,
        "featured_scryfall_id": featured_scryfall_id(d.get("featured")),
    }


class ArchidektError(Exception):
    """Provider failure.

    ``kind`` is one of auth, not_found, rate_limited, unavailable, contract, forbidden.
    """

    def __init__(self, kind: str, message: str, *, retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        # a GET that failed this way (timeout, network error, 5xx) may be retried after a backoff
        self.retryable = retryable


BACKUP_FOLDER_NAME = "MTG Gateway backups"
DECK_ID_RE = re.compile(r"[0-9]{1,12}")


def backup_name(deck_name: str, when: float) -> str:
    """\"<deck> (backup YYYY-MM-DD HH:MM UTC)\", readable by people and agents alike."""
    return f"{deck_name} (backup {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(when))})"


BACKUP_NAME_RE = re.compile(r" \(backup \d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC\)$")


def is_backup_name(name: str) -> bool:
    """Whether a deck name is one ``backup_name`` made (a copy moved out of the backup folder is
    still recognised by its name)."""
    return bool(BACKUP_NAME_RE.search(name or ""))


@dataclass
class DeckCard:
    relation_id: int | None
    card_id: int | None
    name: str
    quantity: int
    categories: list[str]
    modifier: str
    set_code: str
    collector_number: str
    # Oracle and printing details from Archidekt's deck JSON (all optional; the live payload
    # sends nulls freely). None of these feed Deck.fingerprint().
    oracle_name: str = ""
    cmc: float | None = None
    mana_cost: str = ""
    colors: list[str] = field(default_factory=list)
    color_identity: list[str] = field(default_factory=list)
    types: list[str] = field(default_factory=list)
    subtypes: list[str] = field(default_factory=list)
    supertypes: list[str] = field(default_factory=list)
    rarity: str = ""
    price: float | None = None
    legalities: dict[str, str] = field(default_factory=dict)
    edhrec_rank: int | None = None
    salt: float | None = None
    game_changer: bool = False
    tutor: bool = False
    extra_turns: bool = False
    mass_land_denial: bool = False
    mana_production: dict[str, int] | None = None
    two_card_combo_ids: list[str] = field(default_factory=list)
    notes: str = ""  # the user's own text: carried as is, never interpreted
    label: str = ""
    companion: bool = False
    image_hash: str = ""
    scryfall_uid: str = ""
    default_category: str = ""  # Archidekt's auto category for cards with categories null
    oracle_text: str = ""  # rules text, faces joined with " // "; empty when Archidekt sent none
    power: str = ""
    toughness: str = ""
    loyalty: str = ""
    faces: list[dict[str, str]] = field(default_factory=list)  # per face: name, mana_cost, type_line, text...
    artist: str = ""
    flavor: str = ""

    @property
    def type_line(self) -> str:
        """``Legendary Creature — Serpent`` from the super, card and sub types."""
        head = " ".join([*self.supertypes, *self.types]).strip()
        return head + (" — " + " ".join(self.subtypes) if self.subtypes else "")

    # Copies of this printing in the signed-in member's Archidekt Collection ("owned" on each
    # deck card when the deck is read with the member's session; 0 otherwise).
    owned: int = 0


@dataclass
class Deck:
    id: str
    name: str
    owner: str
    updated_at: str
    cards: list[DeckCard]
    categories: list[dict[str, Any]]
    raw: dict[str, Any] = field(repr=False)
    description: str = ""  # the user's own text (Archidekt stores Quill JSON here); never interpreted
    format_id: int | None = None
    format: str | None = None  # FORMAT_NAMES[format_id], None when unknown
    edh_bracket: int | None = None
    private: bool = False
    unlisted: bool = False
    tags: list[str] = field(default_factory=list)
    # The tag relations as the deck JSON's deckTags carries them ({id, tag, name, position}; the
    # relation id is what removes a tag) and the cover art URL (``featured``, auto or chosen).
    tag_relations: list[dict[str, Any]] = field(default_factory=list)
    featured: str = ""
    parent_folder: int | None = None  # the folder the deck sits in (``parentFolder``; None = root)
    created_at: str = ""
    # Social state as the deck JSON reports it for the session that read it: the deck's score
    # ("points"), this member's own vote (VOTE_NONE / VOTE_UP / VOTE_DOWN from "userInput"),
    # whether the member bookmarked it, the comment thread root and the owner's id.
    owner_id: str | None = None
    points: int = 0
    user_vote: int = 0
    bookmarked: bool = False
    comment_root: int | None = None
    view_count: int = 0

    def _rows(self) -> list[tuple[Any, ...]]:
        return sorted(
            (c.relation_id or 0, c.card_id or 0, c.name, c.quantity, tuple(sorted(c.categories)), c.modifier)
            for c in self.cards
        )

    def fingerprint(self) -> str:
        """Stable digest of the card relations and metadata that an edit would touch."""
        blob = json.dumps(
            {"id": self.id, "updated_at": self.updated_at, "rows": self._rows()}, sort_keys=True
        )
        return hashlib.sha256(blob.encode()).hexdigest()

    def rows_fingerprint(self) -> str:
        """Like fingerprint() without the deck's updated time, which other actions (such as
        copying the deck for a backup) may bump without changing a card."""
        blob = json.dumps({"id": self.id, "rows": self._rows()}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def excluded_categories(self) -> set[str]:
        """Category names the deck marks as not part of the deck (Maybeboard, Sideboard...)."""
        return {
            str(c.get("name"))
            for c in self.categories
            if isinstance(c.get("name"), str) and c.get("includedInDeck") is False
        }

    def premier_categories(self) -> set[str]:
        """Category names Archidekt marks premier (its commander zone), plus the literal
        "Commander" the gateway's parsers write."""
        names = {
            str(c.get("name"))
            for c in self.categories
            if isinstance(c.get("name"), str) and c.get("isPremier")
        }
        names.add("Commander")
        return names

    def is_commander(self, card: DeckCard) -> bool:
        """Whether the card sits in the deck's commander zone (a premier category)."""
        premier = self.premier_categories()
        return any(cat in premier for cat in card.categories)

    def in_deck(self, card: DeckCard) -> bool:
        """Whether a card counts as in the deck: its first category (Archidekt's primary category
        for the row) decides. Uncategorized cards count as in the deck (as Mystic Forge does)."""
        return self.categories_count(card.categories)

    def categories_count(self, categories: list[str] | None) -> bool:
        """Whether a row with these categories counts as in the deck. The first category is the
        row's primary one and decides on its own: a row filed under an excluded category first and
        an included one second sits outside the deck, the other way round it is in. This is how a
        live 60-card Oathbreaker deck with rows in several categories comes to 60 (inferred from
        the deck's data on 2026-10-08; the "any included category" rule gave 63)."""
        if not categories:
            return True
        return categories[0] not in self.excluded_categories()

    @property
    def main_cards(self) -> list[DeckCard]:
        return [c for c in self.cards if self.in_deck(c)]

    @property
    def side_cards(self) -> list[DeckCard]:
        return [c for c in self.cards if not self.in_deck(c)]

    def counts_by_name(self) -> dict[str, int]:
        """Card counts for the deck proper; maybeboard and sideboard copies are left out."""
        out: dict[str, int] = {}
        for c in self.main_cards:
            out[c.name] = out.get(c.name, 0) + c.quantity
        return out

    def side_counts_by_name(self) -> dict[str, int]:
        """Card counts of the maybeboard and sideboard rows (cards whose every category the deck
        excludes), the mirror of ``counts_by_name``."""
        out: dict[str, int] = {}
        for c in self.side_cards:
            out[c.name] = out.get(c.name, 0) + c.quantity
        return out

    def cards_in(self, zone: str) -> list[DeckCard]:
        """``main_cards`` or ``side_cards`` by zone name."""
        return self.side_cards if zone == "side" else self.main_cards

    def side_category(self) -> str:
        """The category a card goes in when it is added to the maybeboard: the deck's own
        Maybeboard when it has one, else its first excluded category, else "Maybeboard" (which
        Archidekt treats as its maybeboard by name)."""
        excluded = self.excluded_categories()
        if "Maybeboard" in excluded or not excluded:
            return "Maybeboard"
        return sorted(excluded)[0]


class _NoCookies(http.cookiejar.CookieJar):
    """A cookie jar that never keeps a cookie (see ``ArchidektClient.__init__``)."""

    def __init__(self) -> None:
        super().__init__(policy=http.cookiejar.DefaultCookiePolicy(allowed_domains=[]))


def jwt_exp(token: str) -> int | None:
    """The ``exp`` claim of a JWT, read without verifying the signature (we only use it to
    decide when to refresh; Archidekt verifies the token). None when anything is off."""
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1] + "=" * (-len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload.encode("ascii")))
        exp = claims.get("exp") if isinstance(claims, dict) else None
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        exp = int(exp)
    except (ValueError, TypeError, UnicodeDecodeError, OverflowError):
        return None
    # Anything outside 2000..2200 is not a real expiry (and would overflow date formatting).
    return exp if 946_684_800 <= exp <= 7_258_118_400 else None


def front_face(name: str) -> str:
    """'Delver of Secrets // Insectile Aberration' -> 'Delver of Secrets'; other names unchanged."""
    return name.split(" // ", 1)[0].strip()


_SCRYFALL_ID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SET_CODE_RE = re.compile(r"[A-Za-z0-9]{2,6}")


def _printing(card: dict[str, Any], *, exact: bool) -> dict[str, Any]:
    cid = card.get("id")
    if isinstance(cid, bool) or not isinstance(cid, int):
        raise ArchidektError("contract", "card search result has no integer id")
    edition = card.get("edition") if isinstance(card.get("edition"), dict) else {}
    oracle = card.get("oracleCard") if isinstance(card.get("oracleCard"), dict) else {}
    options = card.get("options")
    return {
        "id": cid,
        "name": str(oracle.get("name", "")),
        "set_code": str(edition.get("editioncode", "") or ""),
        "collector_number": str(card.get("collectorNumber", "") or ""),
        "options": [str(o) for o in options] if isinstance(options, list) else [],
        "exact_printing": exact,
    }


def finish_modifier(options: list[str], *, foil: bool = False, etched: bool = False) -> str:
    """The modifyCards ``modifier`` for the wanted finish ("Normal", "Foil" or "Etched", the
    values Archidekt's site sends; Foil and Etched verified live 2026-10-05). A finish the
    printing does not offer falls back to "Normal" when offered, else the printing's only
    finish; with no option list the wanted finish is sent as is."""
    want = "Etched" if etched else "Foil" if foil else "Normal"
    if not options or want in options:
        return want
    return "Normal" if "Normal" in options else options[0]


def _card_matches(card: dict[str, Any], name: str) -> bool:
    oracle = (
        str(card.get("oracleCard", {}).get("name", "")) if isinstance(card.get("oracleCard"), dict) else ""
    )
    want = name.casefold().strip()
    return oracle.casefold() == want or front_face(oracle).casefold() == front_face(name).casefold()


def _prefer_printing(
    hits: list[dict[str, Any]], set_code: str | None, collector_number: str | None
) -> dict[str, Any]:
    if not set_code and not collector_number:
        return hits[0]

    def score(c: dict[str, Any]) -> int:
        edition = c.get("edition") if isinstance(c.get("edition"), dict) else {}
        code = str(edition.get("editioncode", "")).casefold()
        number = str(c.get("collectorNumber", "")).casefold()
        s = 0
        if set_code and code == set_code.casefold():
            s += 2
        if collector_number and number == collector_number.casefold():
            s += 1
        return s

    return max(hits, key=score)  # stable: the first hit wins ties


def _owned_by(owner: Any, username: str, user_id: str | None) -> bool:
    """True only when a deck entry's owner field names the linked account."""
    if isinstance(owner, dict):
        if user_id and owner.get("id") is not None and str(owner["id"]) == str(user_id):
            return True
        return str(owner.get("username", "")).casefold() == username.casefold()
    if isinstance(owner, str):
        return owner.casefold() == username.casefold()
    return False


class Pacer:
    """Global request pacing shared by every user: one call at a time, a minimum
    gap between calls, at most ``max_per_minute`` calls in any 60 seconds (0: no
    such cap), Retry-After respected, and a breaker that opens for a minute after
    five consecutive failures. A call past the per-minute cap waits for a slot
    rather than failing, so a long apply slows down instead of stopping halfway."""

    MAX_RETRY_AFTER = 120.0  # a larger (or garbled) Retry-After must not stall every user for longer
    WINDOW = 60.0

    def __init__(self, min_interval: float, max_per_minute: int = 0):
        self.min_interval = min_interval
        self.max_per_minute = max(0, int(max_per_minute))
        self._sent: deque[float] = deque()  # start times of the calls in the last WINDOW seconds
        self._lock = asyncio.Lock()
        self._next_allowed = 0.0
        self._failures = 0
        self._open_until = 0.0
        self._retry_until = 0.0  # set by a 429's Retry-After: calls are refused until then

    async def __aenter__(self) -> None:
        await self._lock.acquire()
        try:
            now = time.monotonic()
            if now < self._open_until:
                raise ArchidektError(
                    "unavailable", "Archidekt requests are paused after repeated failures; try later"
                )
            if now < self._retry_until:
                raise ArchidektError(
                    "rate_limited",
                    f"Archidekt asked us to slow down; try again in {int(self._retry_until - now) + 1} s",
                )
            if now < self._next_allowed:
                await asyncio.sleep(self._next_allowed - now)
            if self.max_per_minute:
                now = time.monotonic()
                while self._sent and now - self._sent[0] >= self.WINDOW:
                    self._sent.popleft()
                if len(self._sent) >= self.max_per_minute:
                    await asyncio.sleep(self._sent[0] + self.WINDOW - now)
                    self._sent.popleft()
                self._sent.append(time.monotonic())
        except BaseException:
            # a cancelled or failed wait must never leave the shared lock held
            self._lock.release()
            raise

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: Any
    ) -> None:
        self._next_allowed = time.monotonic() + self.min_interval
        self._lock.release()

    def record(self, ok: bool, retry_after: float | None = None) -> None:
        if ok:
            self._failures = 0
            return
        self._failures += 1
        if retry_after:
            wait = min(retry_after, self.MAX_RETRY_AFTER)
            self._retry_until = max(self._retry_until, time.monotonic() + wait)
        if self._failures >= 5:
            self._open_until = time.monotonic() + 60
            self._failures = 0


# Read caching. Anonymous reads of public decks and deck searches are cached for everyone.
# Card-catalogue reads (``/cards/v2/``) are sent with the member's session, so each one is cached
# for that member only: an answer fetched with one member's token is never served to another.
# A member's reads of decks are never cached, so proposals, applies and drift checks always see
# the live deck. Any write the gateway sends clears the deck and search entries.
CARD_PATH = "/cards/v2/"
CACHE_MAX_ENTRIES = 500
BACKOFF_CAP = 10.0  # seconds; the longest single wait between retries
MAX_LIST_PAGES = 4  # deck-list pages of 50 followed for one member (200 decks)


class ArchidektClient:
    """Every Archidekt request goes through ``_request``: paced by the shared Pacer, sent with the
    gateway's own honest User-Agent (no disguise), read caches as described above, and bounded
    retries with jittered exponential backoff for GETs that failed on a timeout, a network error
    or a 5xx. A 429 is never retried: its Retry-After pauses every call. Writes are never
    retried, so a slow answer can never apply a change twice."""

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        pacer: Pacer,
        *,
        http: httpx.AsyncClient | None = None,
        retries: int = 0,
        backoff_base: float = 1.0,
        cache_seconds: float = 0.0,
        card_cache_seconds: float = 0.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.user_agent = user_agent
        self.pacer = pacer
        self.retries = max(0, int(retries))
        self.backoff_base = max(0.0, float(backoff_base))
        self.cache_seconds = max(0.0, float(cache_seconds))
        self.card_cache_seconds = max(0.0, float(card_cache_seconds))
        self._cache: dict[str, tuple[float, Any]] = {}
        self.stats = {"requests": 0, "cache_hits": 0, "retries": 0, "rate_limited": 0, "failures": 0}
        # Called with the path of every write about to be sent (decks.py drops the member caches
        # that write makes stale: the deck list, the collection's first page).
        self.write_listeners: list[Callable[[str], None]] = []
        # No cookie jar: the client is shared by every member and by anonymous reads, so a cookie
        # Archidekt set for one member's sign-in must never ride along on anyone else's request
        # (or outlive an unlink). Each request carries only its own member's bearer token.
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(20.0), cookies=_NoCookies())

    async def aclose(self) -> None:
        await self._http.aclose()

    # -- plumbing -----------------------------------------------------------
    def _cache_key(
        self, method: str, path: str, token: str | None, params: dict[str, Any] | None
    ) -> str | None:
        """The cache key for a cacheable read, None for anything else."""
        if method != "GET":
            return None
        if path.startswith(CARD_PATH):
            ttl = self.card_cache_seconds
        elif token is None and path.startswith("/decks/"):
            ttl = self.cache_seconds
        else:
            return None
        if ttl <= 0:
            return None
        key = path + "?" + json.dumps(sorted((params or {}).items()), default=str)
        if token is not None:
            # keyed by a fingerprint of the session, never the token itself
            key += "#" + hashlib.sha256(token.encode()).hexdigest()[:32]
        return key

    def _cache_get(self, key: str, path: str) -> tuple[bool, Any]:
        hit = self._cache.get(key)
        if hit is None:
            return False, None
        ttl = self.card_cache_seconds if path.startswith(CARD_PATH) else self.cache_seconds
        if time.monotonic() - hit[0] >= ttl:
            self._cache.pop(key, None)
            return False, None
        return True, hit[1]

    def _cache_put(self, key: str, value: Any) -> None:
        if len(self._cache) >= CACHE_MAX_ENTRIES:
            for old in sorted(self._cache, key=lambda k: self._cache[k][0])[: CACHE_MAX_ENTRIES // 5]:
                self._cache.pop(old, None)
        self._cache[key] = (time.monotonic(), value)

    def forget_deck_reads(self) -> None:
        """Drop every cached deck and search read (the card catalogue stays)."""
        self._cache = {k: v for k, v in self._cache.items() if k.startswith(CARD_PATH)}

    def limits(self) -> dict[str, Any]:
        """The configured limits and the counters since start, for the admin page (no secrets)."""
        return {
            "min_interval_seconds": self.pacer.min_interval,
            "max_per_minute": self.pacer.max_per_minute,
            "retries": self.retries,
            "backoff_base_seconds": self.backoff_base,
            "cache_seconds": self.cache_seconds,
            "card_cache_seconds": self.card_cache_seconds,
            **self.stats,
        }

    def _backoff(self, attempt: int) -> float:
        """Full jitter: a random wait up to base * 2**attempt, capped at BACKOFF_CAP."""
        return random.uniform(0, min(BACKOFF_CAP, self.backoff_base * (2**attempt)))

    async def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        scheme: str = "JWT",
        json_body: Any | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        key = self._cache_key(method, path, token, params)
        if key is not None:
            found, value = self._cache_get(key, path)
            if found:
                self.stats["cache_hits"] += 1
                logger.debug("archidekt cache hit %s", path)
                return copy.deepcopy(value)
        if method != "GET" and not path.startswith("/rest-auth/"):
            # cleared before the write is sent: a write that fails halfway may still have landed
            self.forget_deck_reads()
            for listener in self.write_listeners:
                listener(path)
        attempts = 1 + (self.retries if method == "GET" else 0)
        for attempt in range(attempts):
            try:
                value = await self._send(
                    method, path, token=token, scheme=scheme, json_body=json_body, params=params
                )
            except ArchidektError as exc:
                if exc.kind == "rate_limited":
                    self.stats["rate_limited"] += 1
                retryable = exc.kind == "unavailable" and exc.retryable
                if not retryable or attempt + 1 >= attempts:
                    self.stats["failures"] += 1
                    raise
                self.stats["retries"] += 1
                await asyncio.sleep(self._backoff(attempt))
                continue
            if key is not None:
                self._cache_put(key, copy.deepcopy(value))
            elif method != "GET" and not path.startswith("/rest-auth/"):
                # and again once the write has landed: a list fetched while it was under way could
                # otherwise be kept as fresh with the old rows
                for listener in self.write_listeners:
                    listener(path)
            return value
        raise AssertionError("unreachable")  # pragma: no cover

    async def _send(
        self,
        method: str,
        path: str,
        *,
        token: str | None,
        scheme: str,
        json_body: Any | None,
        params: dict[str, Any] | None,
    ) -> Any:
        """One request through the pacer: no retry and no cache here."""
        headers = {"Accept": "application/json", "User-Agent": self.user_agent}
        if token:
            headers["Authorization"] = f"{scheme} {token}"
        started = time.perf_counter()
        try:
            return await self._paced_send(method, path, headers, json_body, params)
        finally:
            add_time(archidekt_time, time.perf_counter() - started)  # pacer wait and transfer alike

    async def _paced_send(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        json_body: Any | None,
        params: dict[str, Any] | None,
    ) -> Any:
        async with self.pacer:
            self.stats["requests"] += 1
            try:
                resp = await self._http.request(
                    method, f"{self.base_url}{path}", headers=headers, json=json_body, params=params
                )
            except httpx.HTTPError as exc:
                self.pacer.record(False)
                raise ArchidektError(
                    "unavailable", f"Archidekt request failed: {type(exc).__name__}", retryable=True
                ) from exc
            if resp.status_code == 429:
                ra = resp.headers.get("Retry-After")
                self.pacer.record(False, float(ra) if ra and ra.isdigit() else 30.0)
                raise ArchidektError("rate_limited", "Archidekt asked us to slow down; try again in a minute")
            if resp.status_code in (401, 403):
                self.pacer.record(True)
                raise ArchidektError(
                    "auth" if resp.status_code == 401 else "forbidden",
                    "Archidekt rejected the stored session; relink your account",
                )
            if resp.status_code == 404:
                self.pacer.record(True)
                raise ArchidektError("not_found", "Archidekt could not find that deck")
            if resp.status_code >= 500:
                self.pacer.record(False)
                raise ArchidektError(
                    "unavailable", f"Archidekt returned HTTP {resp.status_code}", retryable=True
                )
            if resp.status_code >= 400:
                self.pacer.record(True)
                raise ArchidektError("contract", f"Archidekt rejected the request (HTTP {resp.status_code})")
            self.pacer.record(True)
            if resp.status_code == 204 or not resp.content:
                return None
            try:
                return resp.json()
            except ValueError as exc:
                raise ArchidektError("contract", "Archidekt returned a non-JSON body") from exc

    # -- auth ---------------------------------------------------------------
    async def login(self, username_or_email: str, password: str) -> dict[str, Any]:
        """Exchange credentials once for provider session tokens. The password is
        used here and nowhere else; callers must not store it."""
        key = "email" if "@" in username_or_email else "username"
        body = await self._request(
            "POST", "/rest-auth/login/", json_body={key: username_or_email, "password": password}
        )
        if not isinstance(body, dict):
            raise ArchidektError("contract", "unexpected login response shape")
        access = next((body[k] for k in TOKEN_FIELDS if isinstance(body.get(k), str) and body[k]), None)
        if access is None:
            raise ArchidektError("contract", "Archidekt login did not return a recognized token field")
        refresh = next((body[k] for k in REFRESH_FIELDS if isinstance(body.get(k), str)), None)
        user = body.get("user") if isinstance(body.get("user"), dict) else {}
        return {
            "access": access,
            "refresh": refresh,
            "user_id": str(user.get("id")) if user.get("id") is not None else None,
            "username": str(user.get("username") or username_or_email),
        }

    async def refresh(self, refresh_token: str) -> str:
        """Trade a refresh token for a new access token: POST /rest-auth/token/refresh/
        {"refresh"} -> {"access", "access_token_expiration"} (verified live 2026-10-05; the
        refresh token is not rotated). A rejected refresh token (400 or 401) is ``auth``: the
        link is dead and the user has to sign in again."""
        try:
            body = await self._request(
                "POST", "/rest-auth/token/refresh/", json_body={"refresh": refresh_token}
            )
        except ArchidektError as exc:
            if exc.kind in ("auth", "forbidden", "contract"):
                raise ArchidektError("auth", "Archidekt rejected the stored refresh token; relink") from exc
            raise
        if not isinstance(body, dict):
            raise ArchidektError("contract", "unexpected token refresh response shape")
        access = next((body[k] for k in TOKEN_FIELDS if isinstance(body.get(k), str) and body[k]), None)
        if access is None:
            raise ArchidektError("contract", "Archidekt token refresh did not return an access token")
        return access

    # -- reads --------------------------------------------------------------
    async def list_decks(
        self,
        token: str,
        username: str,
        user_id: str | None = None,
        *,
        backup_folder: str | None = None,
    ) -> list[dict[str, Any]]:
        """The linked user's decks. Archidekt honours ``ownerId`` and ``ownerUsername`` (verified
        live 2026-10-04 and 2026-10-05: each returned exactly the account's decks, private ones
        included, when sent with the usual ``JWT`` scheme; ``Bearer`` is treated as signed out and
        leaves the private decks out; the older ``owner``/``ownerexact`` parameters are ignored
        and return everyone's decks). One listing is sent: by ``ownerId`` when the linked
        account's id is known (the id cannot change, a username can), else by ``ownerUsername``.
        Up to MAX_LIST_PAGES pages of 50 are followed, so a member with more than fifty decks
        sees them all. Every entry is still checked against the linked account on our side, and
        an entry whose owner cannot be read is dropped rather than shown as the user's. Entries in
        the folder named ``backup_folder``, or named like the gateway's backup copies, come back
        with ``backup: True``: the deck pages keep them out of the member's lists and show them
        under History instead."""
        flt: dict[str, Any] = {"ownerId": user_id} if user_id else {"ownerUsername": username}
        decks: list[dict[str, Any]] = []
        seen: set[str] = set()
        dropped = 0
        for page in range(1, MAX_LIST_PAGES + 1):
            params = {**flt, "orderBy": "-updatedAt", "pageSize": 50}
            if page > 1:
                params["page"] = page
            body = await self._request("GET", "/decks/v3/", token=token, params=params)
            results = body.get("results") if isinstance(body, dict) else None
            if not isinstance(results, list):
                raise ArchidektError("contract", "unexpected deck list shape")
            for d in results:
                if not isinstance(d, dict) or "id" not in d:
                    raise ArchidektError("contract", "unexpected deck entry shape")
                if not _owned_by(d.get("owner"), username, user_id):
                    dropped += 1
                    continue
                deck_id = str(d["id"])
                if deck_id in seen:
                    continue
                seen.add(deck_id)
                row = list_row(d)
                if (backup_folder and row["folder"] == backup_folder) or is_backup_name(row["name"]):
                    row["backup"] = True  # the gateway's backup copies: listed under History only
                row["owner"] = username
                decks.append(row)
            if not body.get("next") or len(results) < 50:
                break
        if dropped:
            logger.info("deck list: dropped %d entries not owned by the linked account", dropped)
        decks.sort(key=lambda d: d["updated_at"], reverse=True)
        return decks

    async def search_decks(
        self,
        *,
        name: str = "",
        commander: str = "",
        owner: str = "",
        deck_format: int | None = None,
        colors: str = "",
        order_by: str = "-updatedAt",
        page: int = 1,
    ) -> dict[str, Any]:
        """Public deck search, the way archidekt.com/search/decks queries its own API (read from the
        site's requests on 2026-10-07, each parameter checked live against ``/api/decks/v3/``):
        ``name`` (substring of the deck name), ``commanderName`` (the commander's name; the site's
        /commanders/ pages send it with ``deckFormat=3``), ``ownerUsername`` (exact owner),
        ``deckFormat`` (the numeric format), ``colors=W,U&colorIdentity=true`` (identity within
        those colours; reported from the site's query builder, the result sample looked right) and
        ``orderBy`` (``-updatedAt``, ``-createdAt``, ``-viewCount``, ``-size``, ``edhBracket``).
        Archidekt returns 60 rows a page whatever ``pageSize`` says and caps ``count`` at 1000, so
        only ``page`` is sent. Anonymous: only public decks come back."""
        params: dict[str, Any] = {"orderBy": order_by if order_by in SEARCH_ORDERS else "-updatedAt"}
        if name:
            params["name"] = name[:120]
        if commander:
            params["commanderName"] = commander[:120]
            if deck_format is None:
                deck_format = 3
        if owner:
            # a profile sends the whole username here; Archidekt's real username limit is unverified
            params["ownerUsername"] = owner[:120]
        if deck_format is not None:
            params["deckFormat"] = int(deck_format)
        if colors:
            params["colors"] = ",".join(c for c in "WUBRG" if c in colors.upper())
            params["colorIdentity"] = "true"
        if page > 1:
            params["page"] = int(page)
        body = await self._request("GET", "/decks/v3/", params=params)
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise ArchidektError("contract", "unexpected deck search shape")
        rows = []
        for d in results:
            if not isinstance(d, dict) or "id" not in d:
                raise ArchidektError("contract", "unexpected deck entry shape")
            if d.get("private"):
                continue  # never list a private deck, whatever the listing says
            row = list_row(d)
            owner_obj = d.get("owner") if isinstance(d.get("owner"), dict) else {}
            row["owner"] = str(owner_obj.get("username") or "")
            row["owner_id"] = str(owner_obj["id"]) if owner_obj.get("id") is not None else None
            rows.append(row)
        count = body.get("count") if isinstance(body.get("count"), int) else None
        return {"decks": rows, "count": count, "has_more": bool(body.get("next")), "page": page}

    async def user_profile(self, username: str) -> dict[str, Any] | None:
        """A public profile by username, through the deck listing (``/api/users/{id}/`` needs the
        numeric id, which the listing's owner block carries). None when the user has no public
        decks or does not exist."""
        found = await self.search_decks(owner=username, order_by="-updatedAt")
        if not found["decks"]:
            return None
        first = found["decks"][0]
        return {"username": first["owner"], "id": first.get("owner_id"), "decks": found}

    async def get_deck(self, token: str | None, deck_id: str) -> Deck:
        """Fetch a deck. With ``token=None`` this is an anonymous read of a public deck."""
        body = await self._request("GET", f"/decks/{deck_id}/", token=token)
        return parse_deck(body)

    async def create_deck(
        self,
        token: str,
        *,
        name: str,
        format_id: int,
        description: str = "",
        private: bool = True,
        unlisted: bool = False,
    ) -> Deck:
        """POST /decks/v2/ (route and body per the nccurry reference). Live Archidekt creates the
        deck but answers with a body that is not a full deck (verified 2026-10-05). Then this
        returns an empty Deck carrying only the new id, so the caller records the id before
        filling the deck and verifies it by reading it back."""
        body = await self._request(
            "POST",
            "/decks/v2/",
            token=token,
            json_body={
                "name": name,
                "description": description,
                "deckFormat": format_id,
                "edhBracket": None,
                "parentFolder": None,
                "private": private,
                "unlisted": unlisted,
                "theorycrafted": False,
                "game": None,
                "cardPackage": None,
                "extras": {
                    "decksToInclude": [],
                    "commandersToAdd": [],
                    "forceCardsToSingleton": False,
                    "ignoreCardsOutOfCommanderIdentity": True,
                },
            },
        )
        if isinstance(body, dict) and isinstance(body.get("cards"), list) and "id" in body:
            return parse_deck(body)
        new_id = body.get("id") if isinstance(body, dict) else None
        if (
            isinstance(new_id, bool)
            or not isinstance(new_id, (int, str))
            or not DECK_ID_RE.fullmatch(str(new_id))
        ):
            keys = sorted(body)[:20] if isinstance(body, dict) else type(body).__name__
            raise ArchidektError("contract", f"deck create response has no deck id (fields: {keys})")
        return Deck(id=str(new_id), name=name, owner="", updated_at="", cards=[], categories=[], raw=body)

    # -- backups ------------------------------------------------------------
    async def ensure_folder(self, token: str, name: str = BACKUP_FOLDER_NAME) -> dict[str, str]:
        """``{id, name}`` of the private folder ``name`` directly under the account's root folder, created
        there if missing. Routes per Archidekt's own site code: GET /decks/folderTree/ (root
        with ``children``) and POST /decks/folders/ {name, private, parentFolder} (verified
        live 2026-10-05)."""
        tree = await self._request("GET", "/decks/folderTree/", token=token)
        if not isinstance(tree, dict) or not isinstance(tree.get("id"), int):
            raise ArchidektError("contract", "unexpected folder tree shape")
        for child in tree.get("children") or []:
            if isinstance(child, dict) and child.get("name") == name and isinstance(child.get("id"), int):
                return {"id": str(child["id"]), "name": name}
        made = await self._request(
            "POST",
            "/decks/folders/",
            token=token,
            json_body={"name": name, "private": True, "parentFolder": tree["id"]},
        )
        if not isinstance(made, dict) or not isinstance(made.get("id"), int):
            raise ArchidektError("contract", "unexpected folder create response")
        return {"id": str(made["id"]), "name": name}

    async def backup_deck(
        self, token: str, deck: Deck, *, name: str, folder_id: str, description: str = ""
    ) -> dict[str, str]:
        """Copy ``deck`` into a new private deck in ``folder_id``; returns ``{id, url, name}``.

        POST /decks/copy/ (the site's own copy route; ``parent_folder`` is snake_case there)
        copies every card with quantity, categories and modifier, and the deck's categories,
        commander included (verified live 2026-10-05). The copy route ignores ``description``,
        so it is set afterwards with PATCH /decks/{id}/update/; a failure there leaves a valid,
        named backup and is not raised."""
        if not DECK_ID_RE.fullmatch(str(folder_id)):
            raise ArchidektError("contract", "backup folder id is not a number")
        body = await self._request(
            "POST",
            "/decks/copy/",
            token=token,
            json_body={
                "name": name,
                "deckFormat": deck.raw.get("deckFormat") or 3,
                "edhBracket": None,
                "description": description,
                "featured": "",
                "playmat": "",
                "copyId": int(deck.id),
                "private": True,
                "unlisted": False,
                "theorycrafted": False,
                "game": None,
                "parent_folder": int(folder_id),
                "cardPackage": None,
                "extras": {},
            },
        )
        new_id = body.get("id") if isinstance(body, dict) else None
        if isinstance(new_id, bool) or not isinstance(new_id, int):
            raise ArchidektError("contract", "deck copy response has no deck id")
        if description:
            try:
                await self._request(
                    "PATCH", f"/decks/{new_id}/update/", token=token, json_body={"description": description}
                )
            except ArchidektError as exc:
                logger.info("backup %s: description not set (%s)", new_id, exc.kind)
        return {"id": str(new_id), "url": f"https://archidekt.com/decks/{new_id}", "name": name}

    async def copy_deck(
        self, token: str, deck: Deck, *, name: str, private: bool = True, folder_id: str | None = None
    ) -> dict[str, str]:
        """Clone ``deck`` into a new deck named ``name`` (private by default) in ``folder_id``, or
        the account's root folder when None: the same POST /decks/copy/ the backups use (verified
        live 2026-10-05), so the copy keeps cards, quantities, categories and finishes."""
        if folder_id is None:
            tree = await self._request("GET", "/decks/folderTree/", token=token)
            if not isinstance(tree, dict) or not isinstance(tree.get("id"), int):
                raise ArchidektError("contract", "unexpected folder tree shape")
            folder_id = str(tree["id"])
        if not DECK_ID_RE.fullmatch(str(folder_id)):
            raise ArchidektError("contract", "folder id is not a number")
        body = await self._request(
            "POST",
            "/decks/copy/",
            token=token,
            json_body={
                "name": name,
                "deckFormat": deck.raw.get("deckFormat") or 3,
                "edhBracket": deck.raw.get("edhBracket"),
                "description": deck.description or "",
                "featured": "",
                "playmat": "",
                "copyId": int(deck.id),
                "private": private,
                "unlisted": False,
                "theorycrafted": False,
                "game": None,
                "parent_folder": int(folder_id),
                "cardPackage": None,
                "extras": {},
            },
        )
        new_id = body.get("id") if isinstance(body, dict) else None
        if isinstance(new_id, bool) or not isinstance(new_id, int):
            raise ArchidektError("contract", "deck copy response has no deck id")
        return {"id": str(new_id), "url": f"https://archidekt.com/decks/{new_id}", "name": name}

    # -- writes -------------------------------------------------------------
    async def resolve_card_id(
        self,
        token: str,
        name: str,
        *,
        set_code: str | None = None,
        collector_number: str | None = None,
        scryfall_id: str | None = None,
    ) -> int:
        """Archidekt printing id for a card; see resolve_card."""
        card = await self.resolve_card(
            token, name, set_code=set_code, collector_number=collector_number, scryfall_id=scryfall_id
        )
        return card["id"]

    async def resolve_card(
        self,
        token: str,
        name: str,
        *,
        set_code: str | None = None,
        collector_number: str | None = None,
        scryfall_id: str | None = None,
        require_printing: bool = False,
    ) -> dict[str, Any]:
        """Find an Archidekt printing for a card: ``{id, name, set_code, collector_number,
        options, exact_printing}``. ``options`` lists the finishes Archidekt offers for that
        printing ("Normal", "Foil", "Etched"); pick the deck modifier with finish_modifier.

        A printing is pinned server-side when the caller knows it (verified live 2026-10-05):
        ``uids=<Scryfall id>`` (Archidekt's ``uid`` is the Scryfall id), else ``edition=<set code,
        lower case>`` with ``collectorNumber=``. When that finds nothing for this name the
        lookup falls back to the name alone, and ``exact_printing`` is false.

        Name-only: ``/cards/v2/?name=`` is a substring search, so a plain page of 25 misses "Opt"
        or "Swamp" (verified live 2026-10-04: 177 and 878 matches). ``exact=true`` returns only
        exact oracle names, but nothing for a double-faced card's front face ("Delver of Secrets"
        is stored as "Delver of Secrets // Insectile Aberration"), so that falls back to the
        substring search matched on the front face.

        With ``require_printing`` (a printing the user picked by hand) there is no fallback: the
        set and collector number must name a printing of this very card, else ``not_found`` with
        a message saying what that printing is, if anything."""
        if require_printing:
            return await self._require_printing(token, name, set_code, collector_number)
        if scryfall_id and _SCRYFALL_ID_RE.fullmatch(scryfall_id.strip().lower()):
            hits = [
                c
                for c in await self._cards(token, {"uids": scryfall_id.strip().lower()})
                if _card_matches(c, name)
            ]
            if hits:
                return _printing(hits[0], exact=True)
        if set_code and _SET_CODE_RE.fullmatch(set_code.strip()):
            params: dict[str, Any] = {"name": name, "edition": set_code.strip().lower(), "pageSize": 50}
            number = (collector_number or "").strip()
            if number:
                hits = [
                    c
                    for c in await self._cards(token, {**params, "collectorNumber": number})
                    if _card_matches(c, name)
                ]
                if hits:
                    return _printing(hits[0], exact=True)
            hits = [c for c in await self._cards(token, params) if _card_matches(c, name)]
            if hits:
                return _printing(_prefer_printing(hits, set_code, collector_number), exact=not number)
        exact = await self._card_search(token, name, exact=True, page_size=25)
        hits = [c for c in exact if _card_matches(c, name)]
        if not hits:
            loose = await self._card_search(token, name, exact=False, page_size=50)
            hits = [c for c in loose if _card_matches(c, name)]
        if not hits:
            raise ArchidektError("not_found", f"no Archidekt printing found for '{name}'")
        return _printing(_prefer_printing(hits, set_code, collector_number), exact=False)

    async def _require_printing(
        self, token: str, name: str, set_code: str | None, collector_number: str | None
    ) -> dict[str, Any]:
        code, number = (set_code or "").strip(), (collector_number or "").strip()
        label = f"{code.upper()} #{number}"
        if not _SET_CODE_RE.fullmatch(code) or not number or len(number) > 10:
            raise ArchidektError(
                "not_found", f"'{name}': a set code and collector number are needed, got {label!r}"
            )
        params = {"edition": code.lower(), "collectorNumber": number, "pageSize": 10}
        found = await self._cards(token, params)
        hits = [c for c in found if _card_matches(c, name)]
        if hits:
            return _printing(hits[0], exact=True)
        others = sorted({_printing(c, exact=True)["name"] for c in found if isinstance(c.get("id"), int)})
        if others:
            raise ArchidektError(
                "not_found", f"{label} is {', '.join(others)}, not '{name}'; nothing was added"
            )
        raise ArchidektError("not_found", f"Archidekt has no printing {label} of '{name}'; nothing was added")

    async def _cards(self, token: str, params: dict[str, Any]) -> list[dict]:
        body = await self._request("GET", "/cards/v2/", token=token, params=params)
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise ArchidektError("contract", "unexpected card search shape")
        return [c for c in results if isinstance(c, dict)]

    async def _card_search(self, token: str, name: str, *, exact: bool, page_size: int) -> list[dict]:
        params: dict[str, Any] = {"name": name, "pageSize": page_size}
        if exact:
            params["exact"] = "true"
        body = await self._request("GET", "/cards/v2/", token=token, params=params)
        results = body.get("results") if isinstance(body, dict) else None
        if not isinstance(results, list):
            raise ArchidektError("contract", "unexpected card search shape")
        return [c for c in results if isinstance(c, dict)]

    async def modify_card(self, token: str, deck_id: str, entry: dict[str, Any]) -> Any:
        """PATCH /decks/{id}/modifyCards/v2/ with {"cards": [entry]}, one card per call, as the
        nccurry reference does (reported shape, unverified against Archidekt)."""
        return await self._request(
            "PATCH", f"/decks/{deck_id}/modifyCards/v2/", token=token, json_body={"cards": [entry]}
        )

    # -- the member's Archidekt Collection ---------------------------------------------------
    # Routes and bodies are the ones archidekt.com's own collection page sends (read from its
    # bundle on 2026-10-07): GET /collection/{userId}/v2/ lists (checked live, with
    # ``cardName``, ``collectionOrderBy`` and ``orderDirection`` accepted and ``page`` paging);
    # POST /collection/v2/ creates one record and PATCH /collection/v2/{id}/ replaces one (both
    # answer OPTIONS with the field list: game, quantity, card, modifier, language, condition,
    # purchasePrice); DELETE /collection/bulk/ {"ids": [...]} removes records. The write calls
    # are taken from the bundle and the OPTIONS schemas; this session could not exercise them.

    async def collection_page(
        self,
        token: str,
        user_id: str,
        *,
        page: int = 1,
        page_size: int = COLLECTION_PAGE_SIZE,
        card_name: str = "",
        order_by: str = "",
        descending: bool = True,
    ) -> dict[str, Any]:
        """One page of the member's own collection: {"results": [...], "count", "page",
        "totalPages", "next", "previous", "tags", "isPublic", "owner"}."""
        if not str(user_id).isdigit():
            raise ArchidektError("contract", "the linked account has no Archidekt user id; relink")
        params: dict[str, Any] = {"page": max(1, int(page)), "pageSize": max(1, min(int(page_size), 500))}
        if card_name:
            params["cardName"] = card_name
        if order_by:
            params["collectionOrderBy"] = order_by
            params["orderDirection"] = "descending" if descending else "ascending"
        body = await self._request("GET", f"/collection/{user_id}/v2/", token=token, params=params)
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ArchidektError("contract", "unexpected collection listing shape")
        return body

    async def collection_add(
        self,
        token: str,
        card_id: int,
        quantity: int,
        *,
        modifier: str = "Normal",
        condition: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """POST /collection/v2/: one new record for ``card_id`` (an Archidekt printing id)."""
        body: dict[str, Any] = {
            "game": 1,
            "quantity": int(quantity),
            "card": int(card_id),
            "modifier": modifier,
        }
        if condition:
            body["condition"] = condition
        if language:
            body["language"] = language
        resp = await self._request("POST", "/collection/v2/", token=token, json_body=body)
        if not isinstance(resp, dict):
            raise ArchidektError("contract", "unexpected collection create response shape")
        return resp

    async def collection_set(self, token: str, record: dict[str, Any], **changes: Any) -> dict[str, Any]:
        """PATCH /collection/v2/{id}/ with the record's full body (the site sends every field on an
        edit) and ``changes`` applied: quantity, modifier, condition, language, purchasePrice."""
        rid = record.get("id")
        if isinstance(rid, bool) or not isinstance(rid, int):
            raise ArchidektError("contract", "collection record has no integer id")
        card = record.get("card") if isinstance(record.get("card"), dict) else {}
        card_id = card.get("id", record.get("card"))
        if isinstance(card_id, bool) or not isinstance(card_id, int):
            raise ArchidektError("contract", "collection record has no card id")
        body: dict[str, Any] = {
            "game": record.get("game") or 1,
            "id": rid,
            "quantity": record.get("quantity", 1),
            "card": card_id,
            "modifier": record.get("modifier") or "Normal",
        }
        for key in ("language", "condition", "tags", "purchasePrice"):
            if record.get(key) is not None:
                body[key] = record[key]
        # None clears purchasePrice and condition (the site sends null for "no price" and "no
        # condition"); for the other fields None means "leave as is".
        body.update(
            {k: v for k, v in changes.items() if v is not None or k in ("purchasePrice", "condition")}
        )
        resp = await self._request("PATCH", f"/collection/v2/{rid}/", token=token, json_body=body)
        if not isinstance(resp, dict):
            raise ArchidektError("contract", "unexpected collection update response shape")
        return resp

    async def collection_delete(self, token: str, ids: list[int]) -> None:
        """DELETE /collection/bulk/ {"ids": [...]}: remove whole records."""
        clean = [int(i) for i in ids if isinstance(i, int) and not isinstance(i, bool)]
        if not clean:
            return
        await self._request("DELETE", "/collection/bulk/", token=token, json_body={"ids": clean})

    # -- social actions (a person's own clicks; no assistant tool calls these) -----------------
    # Routes from archidekt.com's deck page bundle (2026-10-07): bookmarks are POST/DELETE
    # /decks/{id}/bookmarks/ (OPTIONS answers "Bookmark" with POST); a deck's like is a vote on
    # its comment root, PUT /comments/vote/{root}/ {"up": bool, "remove": bool}; follow is POST
    # /users/follow/ {"followId", "unfollow"}; the thread is GET /comments/{root}/?page=&orderBy=
    # (checked live) and POST /comments/createComment/ {"parent", "text"} (OPTIONS lists text and
    # parent). Writes are reported from the bundle, not exercised by this session.

    async def vote_deck(self, token: str, comment_root: int, *, up: bool, remove: bool = False) -> Any:
        return await self._request(
            "PUT", f"/comments/vote/{int(comment_root)}/", token=token, json_body={"up": up, "remove": remove}
        )

    async def bookmark_deck(self, token: str, deck_id: str, *, on: bool) -> Any:
        if not DECK_ID_RE.fullmatch(str(deck_id)):
            raise ArchidektError("contract", "deck id is not a number")
        if on:
            return await self._request("POST", f"/decks/{deck_id}/bookmarks/", token=token, json_body={})
        return await self._request("DELETE", f"/decks/{deck_id}/bookmarks/", token=token)

    async def follow_user(self, token: str, user_id: int, *, on: bool) -> Any:
        return await self._request(
            "POST", "/users/follow/", token=token, json_body={"followId": int(user_id), "unfollow": not on}
        )

    async def following(self, token: str, user_id: str, page: int = 1) -> dict[str, Any]:
        """GET /users/{id}/following/: {"count", "next", "previous", "results": [{id, username,
        avatar, following}]} (checked live 2026-10-07)."""
        if not str(user_id).isdigit():
            raise ArchidektError("contract", "user id is not a number")
        body = await self._request(
            "GET", f"/users/{user_id}/following/", token=token, params={"page": max(1, int(page))}
        )
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ArchidektError("contract", "unexpected following list shape")
        return body

    async def comment_thread(
        self, token: str | None, comment_root: int, *, page: int = 1, order_by: str = "-points"
    ) -> dict[str, Any]:
        """GET /comments/{root}/: the root with its ``children`` {"count", "results": [...]}; each
        comment has id, text, owner {id, username, avatar}, parent, createdAt, editedAt, points,
        userInput, childrenCount and its own children (checked live 2026-10-07)."""
        body = await self._request(
            "GET",
            f"/comments/{int(comment_root)}/",
            token=token,
            params={"page": max(1, int(page)), "orderBy": order_by},
        )
        if not isinstance(body, dict):
            raise ArchidektError("contract", "unexpected comment thread shape")
        return body

    async def comment_create(self, token: str, parent: int, text: str) -> dict[str, Any]:
        resp = await self._request(
            "POST", "/comments/createComment/", token=token, json_body={"parent": int(parent), "text": text}
        )
        if not isinstance(resp, dict):
            raise ArchidektError("contract", "unexpected comment create response shape")
        return resp

    async def update_deck(self, token: str, deck_id: str, fields: dict[str, Any]) -> dict[str, Any]:
        """PATCH /decks/{id}/update/ with the deck's own details: any of ``name``, ``description``,
        ``deckFormat`` (int, see FORMAT_IDS), ``edhBracket`` (int or None), ``private`` and
        ``unlisted`` (bool), and the cover: ``featured`` (a card-art URL, see ``art_url``) with
        ``customFeatured`` ``""`` (the site sends both when a member picks a card; ``{"customFeatured":
        ""}`` alone is its "Autoselect deck image"). Only the keys given are sent. The route with
        ``description`` is verified live (backup_deck uses it); the other keys are what Archidekt's
        own site bundle sends on the same route (reported 2026-10-07, not verified). Callers re-read
        the deck afterwards and compare every field rather than trusting the response, which only
        has to be a JSON object."""
        if not DECK_ID_RE.fullmatch(str(deck_id)):
            raise ArchidektError("contract", "deck id is not a number")
        allowed = (
            "name",
            "description",
            "deckFormat",
            "edhBracket",
            "private",
            "unlisted",
            "featured",
            "customFeatured",
        )
        body = {k: fields[k] for k in allowed if k in fields}
        if not body:
            raise ArchidektError("contract", "no deck details to update")
        resp = await self._request("PATCH", f"/decks/{deck_id}/update/", token=token, json_body=body)
        if not isinstance(resp, dict):
            raise ArchidektError("contract", "unexpected deck update response shape")
        return resp

    # -- a member's own hand actions on their decks: delete, folders, tags, comments ---------------
    # Routes read from archidekt.com's bundle on 2026-10-07 (the deck service, folder service, tag
    # service and comment service classes). None of these writes was exercised by the sessions that
    # wrote them; every caller re-reads what it changed and reports a mismatch instead of trusting
    # the response.

    async def delete_deck(self, token: str, deck_id: str) -> None:
        """DELETE /decks/{id}/ (the site's "Delete deck", reported)."""
        if not DECK_ID_RE.fullmatch(str(deck_id)):
            raise ArchidektError("contract", "deck id is not a number")
        await self._request("DELETE", f"/decks/{deck_id}/", token=token)

    async def folder_tree(self, token: str) -> dict[str, Any]:
        """GET /decks/folderTree/: the account's root folder with ``children`` (verified live
        2026-10-05 by ensure_folder)."""
        tree = await self._request("GET", "/decks/folderTree/", token=token)
        if not isinstance(tree, dict) or not isinstance(tree.get("id"), int):
            raise ArchidektError("contract", "unexpected folder tree shape")
        return tree

    async def create_folder(
        self, token: str, name: str, parent_id: int, *, private: bool = False
    ) -> dict[str, Any]:
        """POST /decks/folders/ {name, private, parentFolder} (verified live 2026-10-05)."""
        made = await self._request(
            "POST",
            "/decks/folders/",
            token=token,
            json_body={"name": name, "private": private, "parentFolder": int(parent_id)},
        )
        if not isinstance(made, dict) or not isinstance(made.get("id"), int):
            raise ArchidektError("contract", "unexpected folder create response")
        return made

    async def mass_update(self, token: str, items: list[dict[str, Any]]) -> Any:
        """PATCH /massUpdate/ {"items": [{id, type: "deck" | "folder", patch: {...}, parentFolderId?}]}:
        the folder page's move (patch ``parentFolder``) and rename (patch ``name``) (reported)."""
        return await self._request("PATCH", "/massUpdate/", token=token, json_body={"items": items})

    async def deck_tags(self, token: str | None, deck_id: str) -> list[dict[str, Any]]:
        """GET /decks/{id}/tagRelations/: ``results`` of {id (the relation), tag, deck, name,
        position} (reported; the public precon listing carries the same shape under ``tags``)."""
        if not DECK_ID_RE.fullmatch(str(deck_id)):
            raise ArchidektError("contract", "deck id is not a number")
        body = await self._request("GET", f"/decks/{deck_id}/tagRelations/", token=token)
        results = body.get("results") if isinstance(body, dict) else body
        if not isinstance(results, list):
            raise ArchidektError("contract", "unexpected tag relation listing shape")
        return [r for r in results if isinstance(r, dict)]

    async def search_tags(self, token: str, query: str) -> list[dict[str, Any]]:
        """GET /decks/tags/v2/?q=: the site's tag picker lookup ({id, name} rows; reported)."""
        body = await self._request("GET", "/decks/tags/v2/", token=token, params={"q": query[:60]})
        results = body.get("results") if isinstance(body, dict) else body
        if not isinstance(results, list):
            raise ArchidektError("contract", "unexpected tag search shape")
        return [r for r in results if isinstance(r, dict)]

    async def create_tag(self, token: str, name: str) -> dict[str, Any]:
        """POST /decks/tags/ {name}: a new global deck tag (reported; the body is inferred from the
        site's picker, which creates a tag by its typed name)."""
        made = await self._request("POST", "/decks/tags/", token=token, json_body={"name": name})
        if not isinstance(made, dict) or not isinstance(made.get("id"), int):
            raise ArchidektError("contract", "unexpected tag create response")
        return made

    async def add_deck_tag(self, token: str, deck_id: str, tag_id: int, position: str) -> dict[str, Any]:
        """POST /decks/tagRelations/ {deck, tag, position} (reported)."""
        made = await self._request(
            "POST",
            "/decks/tagRelations/",
            token=token,
            json_body={"deck": int(deck_id), "tag": int(tag_id), "position": position},
        )
        if not isinstance(made, dict):
            raise ArchidektError("contract", "unexpected tag relation response")
        return made

    async def remove_deck_tag(self, token: str, relation_id: int) -> None:
        """DELETE /decks/tagRelations/{id}/ (reported)."""
        await self._request("DELETE", f"/decks/tagRelations/{int(relation_id)}/", token=token)

    async def comment_update(self, token: str, comment_id: int, text: str) -> dict[str, Any]:
        """PATCH /comments/{id}/ {text}: edit one's own comment (the site sends text, archived and
        locked; only the text is sent here; reported)."""
        resp = await self._request(
            "PATCH", f"/comments/{int(comment_id)}/", token=token, json_body={"text": text}
        )
        if not isinstance(resp, dict):
            raise ArchidektError("contract", "unexpected comment update response shape")
        return resp

    async def comment_delete(self, token: str, comment_id: int) -> None:
        """DELETE /comments/{id}/: the site's "hard delete" of a comment (reported)."""
        await self._request("DELETE", f"/comments/{int(comment_id)}/", token=token)

    async def precons(self) -> dict[str, list[dict[str, Any]]]:
        """GET /decks/precons/ (anonymous; checked live 2026-10-07): ``{"Set name (CODE)": [deck
        listing rows]}`` in the site's order, newest set first; each row has the ``/decks/v3/``
        listing shape plus ``tags`` and the owner Archidekt_Precons."""
        body = await self._request("GET", "/decks/precons/", token=None)
        if not isinstance(body, dict) or not all(isinstance(v, list) for v in body.values()):
            raise ArchidektError("contract", "unexpected precon listing shape")
        return {str(k): [d for d in v if isinstance(d, dict) and "id" in d] for k, v in body.items()}


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _str_list(value: Any) -> list[str]:
    return [str(v) for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _card_price(prices: Any) -> float | None:
    """Card Kingdom's normal-finish price (``prices.ck``) when present, else TCGplayer's
    (``prices.tcg``); both are Archidekt's reported figures, not verified by us. The finish
    the deck row uses is ignored here (foil prices live under ``ckfoil``/``tcgfoil``)."""
    if not isinstance(prices, dict):
        return None
    for key in ("ck", "tcg"):
        p = _number(prices.get(key))
        if p is not None and p > 0:
            return p
    return None


def _mana_production(value: Any) -> dict[str, int] | None:
    """``{"W": 1, "U": null, ...}`` -> ``{"W": 1}``; None when nothing is produced."""
    if not isinstance(value, dict):
        return None
    out = {str(k): int(v) for k, v in value.items() if isinstance(v, int) and not isinstance(v, bool) and v}
    return out or None


def _pt(value: Any) -> str:
    """Power, toughness or loyalty as Archidekt sends it ('' or None when absent)."""
    return "" if value is None or isinstance(value, bool) else str(value).strip()


def _type_line_of(face: dict[str, Any]) -> str:
    head = " ".join([*_str_list(face.get("superTypes")), *_str_list(face.get("types"))]).strip()
    subs = _str_list(face.get("subTypes"))
    return head + (" — " + " ".join(subs) if subs else "")


def _faces(oracle: dict[str, Any]) -> list[dict[str, str]]:
    """Each face of a multi-faced card as the pages show it; [] for a one-faced card."""
    faces = oracle.get("faces")
    out: list[dict[str, str]] = []
    for face in faces if isinstance(faces, list) else []:
        if not isinstance(face, dict):
            continue
        out.append(
            {
                "name": str(face.get("name") or ""),
                "mana_cost": str(face.get("manaCost") or ""),
                "type_line": _type_line_of(face),
                "text": str(face.get("text") or "").strip(),
                "power": _pt(face.get("power")),
                "toughness": _pt(face.get("toughness")),
                "loyalty": _pt(face.get("loyalty")),
            }
        )
    return out


def _oracle_text(oracle: dict[str, Any]) -> str:
    """The card's rules text as Archidekt carries it: ``text`` for one-faced cards, else each
    face's name, mana cost and text joined with ``//`` (multi-faced cards have empty top-level
    text and the real data in ``faces``)."""
    text = oracle.get("text")
    if isinstance(text, str) and text.strip():
        return text.strip()
    faces = oracle.get("faces")
    parts: list[str] = []
    for face in faces if isinstance(faces, list) else []:
        if not isinstance(face, dict):
            continue
        head = " ".join(x for x in (str(face.get("name") or ""), str(face.get("manaCost") or "")) if x)
        body = str(face.get("text") or "").strip()
        parts.append(f"{head}: {body}" if head and body else head or body)
    return " // ".join(x for x in parts if x)


def parse_deck(body: Any) -> Deck:
    if not isinstance(body, dict) or "id" not in body or not isinstance(body.get("cards"), list):
        raise ArchidektError("contract", "unexpected deck shape")
    cards: list[DeckCard] = []
    for entry in body["cards"]:
        if not isinstance(entry, dict):
            raise ArchidektError("contract", "unexpected card entry shape")
        if entry.get("deletedAt"):
            continue  # soft-deleted relation still present in the payload
        card = entry.get("card") if isinstance(entry.get("card"), dict) else {}
        oracle = card.get("oracleCard") if isinstance(card.get("oracleCard"), dict) else {}
        edition = card.get("edition") if isinstance(card.get("edition"), dict) else {}
        qty = entry.get("quantity", 1)
        if not isinstance(qty, int):
            raise ArchidektError("contract", "card quantity is not an integer")
        legalities = oracle.get("legalities") if isinstance(oracle.get("legalities"), dict) else {}
        rank = oracle.get("edhrecRank")
        combos = oracle.get("twoCardComboIds") if isinstance(oracle.get("twoCardComboIds"), list) else []
        cards.append(
            DeckCard(
                relation_id=entry.get("id") if isinstance(entry.get("id"), int) else None,
                card_id=card.get("id") if isinstance(card.get("id"), int) else None,
                name=str(oracle.get("name", "?")),
                quantity=qty,
                # Live decks send "categories": null for cards with no category set.
                categories=[str(c) for c in (entry.get("categories") or []) if isinstance(c, str)],
                modifier=str(entry.get("modifier", "") or ""),
                set_code=str(edition.get("editioncode", "") or ""),
                collector_number=str(card.get("collectorNumber", "") or ""),
                oracle_name=str(oracle.get("name", "") or ""),
                cmc=_number(oracle.get("cmc")),
                mana_cost=str(oracle.get("manaCost", "") or ""),
                colors=_str_list(oracle.get("colors")),
                color_identity=_str_list(oracle.get("colorIdentity")),
                types=_str_list(oracle.get("types")),
                subtypes=_str_list(oracle.get("subTypes")),
                supertypes=_str_list(oracle.get("superTypes")),
                rarity=str(card.get("rarity", "") or ""),
                price=_card_price(card.get("prices")),
                legalities={str(k): str(v) for k, v in legalities.items() if isinstance(v, str)},
                edhrec_rank=rank if isinstance(rank, int) and not isinstance(rank, bool) else None,
                salt=_number(oracle.get("salt")),
                game_changer=oracle.get("gameChanger") is True,
                tutor=oracle.get("tutor") is True,
                extra_turns=oracle.get("extraTurns") is True,
                mass_land_denial=oracle.get("massLandDenial") is True,
                mana_production=_mana_production(oracle.get("manaProduction")),
                two_card_combo_ids=[str(i) for i in combos if i is not None],
                notes=str(entry.get("notes") or ""),
                label=str(entry.get("label") or ""),
                companion=entry.get("companion") is True,
                image_hash=str(card.get("scryfallImageHash") or ""),
                scryfall_uid=str(card.get("uid") or ""),
                default_category=str(oracle.get("defaultCategory") or ""),
                oracle_text=_oracle_text(oracle),
                power=_pt(oracle.get("power")),
                toughness=_pt(oracle.get("toughness")),
                loyalty=_pt(oracle.get("loyalty")),
                faces=_faces(oracle),
                artist=str(card.get("artist") or ""),
                flavor=str(card.get("flavor") or ""),
                owned=_int(card.get("owned")),
            )
        )
    owner = body.get("owner") if isinstance(body.get("owner"), dict) else {}
    fmt = body.get("deckFormat")
    format_id = fmt if isinstance(fmt, int) and not isinstance(fmt, bool) else None
    bracket = body.get("edhBracket")
    tags: list[str] = []
    relations: list[dict[str, Any]] = []
    raw_tags = body.get("deckTags")
    for t in raw_tags if isinstance(raw_tags, list) else []:
        if isinstance(t, str):
            tags.append(t)
        elif isinstance(t, dict) and isinstance(t.get("name"), str):
            tags.append(t["name"])
            relations.append({k: t.get(k) for k in ("id", "tag", "name", "position") if k in t})
    featured = body.get("customFeatured") or body.get("featured")
    return Deck(
        id=str(body["id"]),
        name=str(body.get("name", "")),
        owner=str(owner.get("username", "")),
        updated_at=str(body.get("updatedAt", "")),
        cards=cards,
        categories=[c for c in (body.get("categories") or []) if isinstance(c, dict)],
        raw=body,
        description=body.get("description") if isinstance(body.get("description"), str) else "",
        format_id=format_id,
        format=FORMAT_NAMES.get(format_id) if format_id is not None else None,
        edh_bracket=bracket if isinstance(bracket, int) and not isinstance(bracket, bool) else None,
        private=body.get("private") is True,
        unlisted=body.get("unlisted") is True,
        tags=tags,
        tag_relations=relations,
        featured=str(featured) if isinstance(featured, str) else "",
        parent_folder=body["parentFolder"]
        if isinstance(body.get("parentFolder"), int) and not isinstance(body.get("parentFolder"), bool)
        else None,
        created_at=str(body.get("createdAt") or ""),
        owner_id=str(owner["id"]) if owner.get("id") is not None else None,
        points=_int(body.get("points")),
        user_vote=_int(body.get("userInput")) if _int(body.get("userInput")) in (0, 1, 2) else 0,
        bookmarked=body.get("bookmarked") is True,
        comment_root=_int(body.get("commentRoot")) or None,
        view_count=_int(body.get("viewCount")),
    )
