"""Owned cards: the member's Collection on Archidekt, shown and edited through the gateway.

Nothing about the cards a person owns is stored on the gateway. The ``/collection`` page, its JSON
API and the ``*_collection`` tools read and write the member's own Archidekt Collection through
the Archidekt session they linked on the Account page (the same routes archidekt.com's collection
page uses; see ``ArchidektClient.collection_page`` and friends). A scan is a draft the member keeps
until its cards are saved here or into a deck, which removes it.

Every call runs under the member's own Archidekt session, so nothing here can read another
member's cards. Rows carry Archidekt's record id (an integer); card images come from Scryfall by
the card's Scryfall id, as the deck pages do.
"""

from __future__ import annotations

import asyncio
import csv
import io
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable, Iterable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Annotated, Any
from urllib.parse import urlencode

from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import collection_tags, prefs, refusals, ui
from . import searchpanel as sp
from .approve import CARD_TOOL_META, proposal_tool_result
from .archidekt import (
    COLLECTION_PAGE_SIZE,
    ArchidektError,
    _faces,
    _oracle_text,
    _pt,
    condition_code,
    language_code,
    page_turn,
)
from .binder_prices import BINDER_PAGE_SIZE, BINDER_READ_PAGES, BinderStore
from .busy import busy_body, busy_headers, busy_plan, held_html, outage, same_url
from .cardsearch_view import CARDS_CSS
from .deckpage import image_url
from .decks import (
    DeckError,
    _writing,
    archidekt_wait,
    current_client,
    may_have_landed,
    no_card_called,
    reloading,
)
from .pages import (
    BROWSER_CLIENT_ID,
    NO_STORE,
    _csrf,
    _safe_next,
    browser_session,
    csrf_form_ok,
    csrf_header_ok,
    login_redirect_here,
    press_stamp,
    read_limited,
)
from .profile_page import COLLECTION_CSS as VISIBILITY_CSS
from .profile_page import collection_visibility, profile_or_none
from .schemas import CollectionAdd, CollectionRemove, card_aliases, dumped
from .theme import UNPRICED_HTML, icon, render, stylesheet_link
from .theme import esc as _esc

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .decks import DeckService
    from .scan.service import ScanService

logger = logging.getLogger(__name__)

MAX_ITEMS_PER_CALL = 100  # each card costs one or two Archidekt calls, paced about a second apart
MAX_QUANTITY = 9_999
MAX_BULK_ROWS = 200  # Select's bar changes at most this many rows in one press (one write each)
BULK_UNDO_TTL = 15 * 60  # seconds a bulk removal can be undone
MAX_PAGES_FOR_EXPORT = 50  # 5,000 records
# Finding records: one unfiltered read of this many covers a binder that small; a bigger one is read
# by Archidekt's ``cardName`` filter, a name at a time, up to FILTERED_PAGES pages of it.
WHOLE_READ_SIZE = 200
FILTERED_PAGES = 5
PAGE_SIZE = COLLECTION_PAGE_SIZE
# the newest records added since a held Binder page was read that it keeps to show (held_added);
# every one is counted
HELD_ADDED_ROWS = COLLECTION_PAGE_SIZE
_ADD_ONLY = ("previous_quantity", "added_quantity", "went_to", "index")  # what an add said, not the row
FIRST_PAGE_TTL = 60.0  # seconds the unfiltered first page is served from memory
WHOLE_TTL = 60.0  # and the Binder read whole for a tag filter (CollectionService.whole)
IMPORT_PAUSE_POLL = 5.0  # seconds a paused import or restore waits before looking at the allowance again
FINISHES = ("nonfoil", "foil", "etched")
MODIFIERS = {"nonfoil": "Normal", "foil": "Foil", "etched": "Etched"}
CONDITIONS = ("", "NM", "LP", "MP", "HP", "DMG")
# the conditions' names, as an import may write them out, upper-cased with single spaces
CONDITION_NAMES = {
    "NEAR MINT": "NM",
    "LIGHTLY PLAYED": "LP",
    "MODERATELY PLAYED": "MP",
    "HEAVILY PLAYED": "HP",
    "DAMAGED": "DMG",
}
# Archidekt's own collection orderings the gateway exposes (``collectionOrderBy``; only
# ``editionDate`` was seen in the site's requests, the default is Archidekt's newest-first).
SORTS = {"added": "Recently added", "edition": "Set release", "price": "Price"}
COLLECTION_CSP = (
    "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "manifest-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
log = logging.getLogger(__name__)
# set while an add sends its own write (CollectionService._own_write)
_own_write: ContextVar[bool] = ContextVar("collection_own_write", default=False)
_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class CollectionError(Exception):
    """User-facing failure. ``kind``: invalid, not_found, not_linked, unavailable, rate_limited,
    busy, auth, forbidden, writes_disabled. ``retry_after`` copies DeckError's (seconds to wait,
    when known)."""

    def __init__(self, kind: str, message: str, *, retry_after: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after


def _clean(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


def _wrap(exc: Exception) -> CollectionError:
    if isinstance(exc, CollectionError):
        return exc
    if isinstance(exc, (DeckError, ArchidektError)):
        kind = exc.kind
        if kind == "not_found":
            return CollectionError("not_found", "Archidekt has no such collection record.")
        if kind == "auth":
            return CollectionError("auth", str(exc))
        if kind == "contract":
            return CollectionError("unavailable", "Archidekt answered in an unexpected way; try again.")
        # whole seconds, at least 1: a raw ArchidektError carries a float, which the busy page would
        # otherwise drop for its 60 s default
        err = CollectionError(kind, str(exc), retry_after=archidekt_wait(exc))
        # a refusal of the member's own Archidekt budget: nothing was sent (an import waits on it)
        err.busy_cause = getattr(exc, "busy_cause", None)
        return err
    raise exc


def _finish_of(modifier: Any) -> str:
    m = str(modifier or "Normal").strip().lower()
    return "foil" if m == "foil" else "etched" if m == "etched" else "nonfoil"


async def _finish_warnings(adds: list[dict[str, Any]], scan: Any) -> list[str]:
    """One warning per added card whose exact printing (set and collector number) Scryfall does not
    list in the finish asked for, e.g. a foil of a printing made in nonfoil only. Nothing when the
    lookup is not available or fails: the proposal is made either way."""
    pinned = [a for a in adds if a.get("set") and a.get("collector_number")]
    if not pinned or scan is None:
        return []
    idents = list({(a["set"], a["collector_number"]): None for a in pinned})
    try:
        found, _missing = await scan.scryfall.collection(
            [{"set": s, "collector_number": n} for s, n in idents]
        )
    except Exception:
        logger.warning("finish check of a collection add failed", exc_info=True)
        return []
    finishes = {
        (str(c.get("set") or "").lower(), str(c.get("collector_number") or "")): c.get("finishes") or []
        for c in found
    }
    out = []
    for a in pinned:
        made = finishes.get((a["set"], a["collector_number"]))
        if made and a.get("finish") not in made:
            out.append(
                f"{a.get('name') or 'A card'} ({a['set'].upper()} {a['collector_number']}) is not made in "
                f"{a['finish']}; Scryfall lists {', '.join(made)}. Amend the finish, or the binder "
                "will hold a printing that does not exist."
            )
    return out


def normalise_items(
    items: Any, *, limit: int = MAX_ITEMS_PER_CALL, trusted: bool = False
) -> list[dict[str, Any]]:
    """Validate the cards an add names (a scan item with ``card``, ``{name, set?, collector_number?,
    scryfall_id?, quantity?, finish?, condition?}`` or a plain name) into flat records the add and
    a stored proposal both accept. Raises CollectionError for anything unusable. ``limit``: the
    most cards (an import takes more, a card at a time). ``trusted``: the gateway's own binder
    snapshot (a restore), whose Archidekt printing id (``card_id``), tag ids (``tag_ids``) and
    price paid (``purchase_price``) are kept; never anything an assistant or a file sent."""
    if not isinstance(items, list) or not items:
        raise CollectionError("invalid", "Give a non-empty list of cards.")
    if len(items) > limit:
        raise CollectionError("invalid", f"At most {limit} cards per call.")
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(items):
        if isinstance(raw, str):
            raw = {"name": raw}
        if not isinstance(raw, dict):
            raise CollectionError("invalid", f"Card {i} must be an object.")
        card = raw.get("card") if isinstance(raw.get("card"), dict) else raw
        opts = _item_options(raw, i)
        sid = str(card.get("scryfall_id") or "").strip().lower()
        spec = {
            "name": _clean(card.get("name"), 200),
            "set": _clean(card.get("set") or card.get("set_code"), 10).lower(),
            "collector_number": _clean(card.get("collector_number"), 20),
            "scryfall_id": sid if _ID.fullmatch(sid) else "",
        }
        if not spec["name"] and not spec["scryfall_id"] and not (spec["set"] and spec["collector_number"]):
            raise CollectionError("invalid", f"Card {i}: give a name, or a set and collector number.")
        if not spec["name"] and spec["scryfall_id"]:
            spec["name"] = "?"
        if trusted:
            cid = raw.get("card_id")
            if isinstance(cid, int) and not isinstance(cid, bool) and cid > 0:
                spec["card_id"] = cid
            spec["tag_ids"] = [t for t in raw.get("tag_ids") or [] if isinstance(t, int) and t > 0]
            price = raw.get("purchase_price")
            if isinstance(price, (int, float)) and not isinstance(price, bool):
                spec["purchase_price"] = float(price)
        out.append({**spec, **opts})
    return out


# An import's lines: it runs in the background a card at a time (collection_bulk), up to the
# parser's 2 MB of text
MAX_IMPORT_ROWS = 5_000
IMPORT_FORM_BYTES = 6_500_000  # 2 MB of text, form-encoded (a quote or a comma takes three bytes)
IMPORT_RESULT_TTL = 300.0  # seconds an import's result waits for the page the redirect lands on
IMPORT_RESULTS_MAX = 2_000
QUOTED_CELL_CHARS = 20  # a cell quoted in a refusal is cut to this
REFUSAL_CHARS = 300  # and a kept refusal to this, whatever made it (none is longer today)
_IMPORT_COLUMNS = {
    "quantity": ("quantity", "count", "qty"),
    "name": ("name", "card name", "card"),
    "finish": ("finish", "modifier", "foil"),
    "condition": ("condition",),
    "set": ("edition code", "set code", "set", "edition"),
    "collector_number": ("collector number", "collector_number", "number"),
    "scryfall_id": ("scryfall id", "scryfall_id", "scryfall uuid"),
    "language": ("language", "lang"),
}


def _quoted(value: str) -> str:
    """A cell quoted in a refusal, cut to QUOTED_CELL_CHARS so a huge cell can't make a huge
    message (each refusal is kept for the page, up to IMPORT_RESULTS_MAX of them)."""
    if len(value) > QUOTED_CELL_CHARS:
        value = value[:QUOTED_CELL_CHARS] + "…"
    return f"“{value}”"


def parse_collection_import(text: str) -> list[dict[str, Any]]:
    """Cards to add from pasted or uploaded text: the gateway's own collection CSV (the Export CSV
    button), a CSV with Archidekt's collection column names (Quantity, Name, Finish, Condition,
    Language, Edition Code, Collector Number, Scryfall ID; headers are matched by name, extra
    columns are ignored; Language and Condition take the gateway's codes or Archidekt's, an empty
    Language is English) or a plain list (``2 Sol Ring (CMR) 436 *F*``). Returns items for ``add``;
    a CSV row's item also carries ``line`` (the physical line its row starts on, so a quoted cell
    with a line break and blank lines are counted) and, when its Language is one Archidekt's
    collection cannot store, ``language_given`` (the value as given; the item is English), and when
    its Condition is not one the gateway knows, ``condition_given`` (the item is NM). Raises
    CollectionError when nothing usable is found or there are more than MAX_IMPORT_ROWS rows."""
    text = text.lstrip("\ufeff")
    if not text.strip():
        raise CollectionError("invalid", "Paste or choose a CSV or a card list first.")
    if len(text) > 2_000_000:
        raise CollectionError("invalid", "That file is larger than 2 MB.")
    items: list[dict[str, Any]] = []
    reader = csv.reader(io.StringIO(text))
    first: list[str] = []
    try:
        for row in reader:
            if any(c.strip() for c in row):
                first = [c.strip().lower() for c in row]
                break
    except csv.Error:
        pass  # no CSV header: a plain list's own check says what is wrong with it
    is_csv = any(n in first for n in _IMPORT_COLUMNS["name"])  # any Name column the reader takes
    if is_csv:
        _read_csv_rows(reader, first, items)
    else:
        from .decklist import DecklistError, parse_decklist

        try:
            parsed = parse_decklist(text.strip())
        except DecklistError as exc:
            raise CollectionError("invalid", f"The list could not be read: {refusals.sentence(exc)}") from exc
        for c in parsed:
            items.append(
                {
                    "name": c.name,
                    "set": c.set_code.lower(),
                    "collector_number": c.collector_number,
                    "quantity": c.quantity,
                    "finish": c.finish.lower() if c.finish else "nonfoil",
                    "condition": "",
                }
            )
    if not items:
        raise CollectionError("invalid", "No cards were found in that text.")
    if len(items) > MAX_IMPORT_ROWS:
        raise CollectionError(
            "invalid",
            f"That is {len(items):,} rows; an import adds at most {MAX_IMPORT_ROWS:,} at a time. Split the "
            "file and import the rest after.",
        )
    out = normalise_items(items, limit=MAX_IMPORT_ROWS)
    for item, raw in zip(out, items, strict=True):
        for key in ("line", "language_given", "condition_given"):
            if key in raw:
                item[key] = raw[key]
    return out


def _read_csv_rows(reader: Any, first: list[str], items: list[dict[str, Any]]) -> None:
    """The rows after a CSV's header row ``first`` (lower-cased), appended to ``items``. Each
    row's ``line`` is the physical line it starts on: ``reader.line_num`` counts the lines read,
    so a quoted cell with a line break and blank lines are counted."""
    keys: dict[str, int] = {}
    for key, names in _IMPORT_COLUMNS.items():
        for n in names:
            if n in first and key not in keys:
                keys[key] = first.index(n)
    if "name" not in keys:
        raise CollectionError("invalid", "The CSV needs a Name column.")

    def cell(row: list[str], key: str) -> str:
        i = keys.get(key)
        return row[i].strip() if i is not None and i < len(row) else ""

    while True:
        n = reader.line_num + 1
        try:
            row = next(reader, None)
        except csv.Error as exc:
            # a cell over the csv module's limit (131,072 characters), as an unclosed quote makes
            # of the rest of the file: named by the line its row starts on
            raise CollectionError(
                "invalid",
                f"Line {n}: a cell is longer than {csv.field_size_limit():,} characters; "
                'check for a quotation mark (") that is not closed.',
            ) from exc
        if row is None:
            break
        if not any(c.strip() for c in row):
            continue
        name = cell(row, "name")
        if not name:
            raise CollectionError("invalid", f"Line {n}: no card name.")
        qty = cell(row, "quantity") or "1"
        if not qty.isascii() or not qty.isdigit():  # str.isdigit alone accepts "²" and int() then fails
            raise CollectionError("invalid", f"Line {n}: quantity {_quoted(qty)} is not a number.")
        # a long digit string is past MAX_QUANTITY anyway, and int() refuses one over 4,300 digits
        if len(qty.lstrip("0")) > len(str(MAX_QUANTITY)) or not 1 <= int(qty) <= MAX_QUANTITY:
            raise CollectionError(
                "invalid", f"Line {n}: quantity {_quoted(qty)} is not from 1 to {MAX_QUANTITY:,}."
            )
        finish_raw = cell(row, "finish").lower()
        finish = {"foil": "foil", "etched": "etched", "true": "foil", "yes": "foil"}.get(
            finish_raw, "nonfoil"
        )
        condition = cell(row, "condition").upper()
        if condition == "D":  # Archidekt's own code for Damaged
            condition = "DMG"
        # a condition written out ("Lightly Played") is its code, so it isn't kept as Near Mint
        condition = CONDITION_NAMES.get(" ".join(re.split(r"[\s_-]+", condition)), condition)
        items.append(
            {
                "name": name,
                "set": cell(row, "set").lower(),
                "collector_number": cell(row, "collector_number"),
                "scryfall_id": cell(row, "scryfall_id"),
                "quantity": int(qty),
                "finish": finish,
                "condition": condition if condition in CONDITIONS else "NM",
                "language": _import_language(cell(row, "language")) or "EN",
                "line": n,
            }
        )
        if _import_language(cell(row, "language")) is None:
            items[-1]["language_given"] = cell(row, "language")[:40]
        if condition not in CONDITIONS:  # kept as NM, and the result names the line
            items[-1]["condition_given"] = cell(row, "condition")[:40]


def _import_language(value: str) -> str | None:
    """A CSV row's language as the gateway's code: the gateway's own codes, Archidekt's (JP, KR,
    SP, CS, CT) and the English names (Japanese, ...) are read; an empty cell is English. None for a
    language Archidekt's collection cannot store (Phyrexian, Hebrew, ... or a value it does not
    know): ``parse_collection_import`` keeps that row as English and records the value given."""
    code = value.strip().upper()
    if not code:
        return "EN"
    code = ARCHIDEKT_TO_LANGUAGE.get(code, code)
    if code in LANGUAGES:
        return code
    by_name = {name.casefold(): c for c, name in LANGUAGE_NAMES.items()}
    return by_name.get(value.strip().casefold())


def row_out(rec: dict[str, Any]) -> dict[str, Any]:
    """A collection record in the gateway's flat shape. The record shape is the one Archidekt's
    collection page reads (id, quantity, modifier, language, condition, tags, purchasePrice, card
    with oracleCard and edition); fields the live payload leaves out stay empty."""
    card = rec.get("card") if isinstance(rec.get("card"), dict) else {}
    oracle = card.get("oracleCard") if isinstance(card.get("oracleCard"), dict) else {}
    edition = card.get("edition") if isinstance(card.get("edition"), dict) else {}
    uid = str(card.get("uid") or "")
    types = [str(t) for t in (oracle.get("types") or []) if isinstance(t, str)]
    subtypes = [str(t) for t in (oracle.get("subTypes") or []) if isinstance(t, str)]
    type_line = " ".join(types) + (" — " + " ".join(subtypes) if subtypes else "")
    cmc = oracle.get("cmc")
    qty = rec.get("quantity")
    return {
        "id": rec.get("id"),
        "archidekt_card_id": card.get("id"),
        "name": str(oracle.get("name") or card.get("displayName") or ""),
        "quantity": qty if isinstance(qty, int) and not isinstance(qty, bool) else 0,
        "finish": _finish_of(rec.get("modifier")),
        "condition": condition_code(rec.get("condition")),  # Archidekt sends a number (CONDITION_IDS)
        "lang": language_code(rec.get("language")),  # and a number here too (LANGUAGE_IDS)
        "set": str(edition.get("editioncode") or "").lower(),
        "set_name": str(edition.get("editionname") or ""),
        "collector_number": str(card.get("collectorNumber") or ""),
        "rarity": str(card.get("rarity") or ""),
        "type_line": type_line.strip(),
        "oracle_text": _oracle_text(oracle),
        "power": _pt(oracle.get("power")),
        "toughness": _pt(oracle.get("toughness")),
        "loyalty": _pt(oracle.get("loyalty")),
        "faces": _faces(oracle),
        "mana_cost": str(oracle.get("manaCost") or ""),
        "mana_value": cmc if isinstance(cmc, (int, float)) and not isinstance(cmc, bool) else None,
        "color_identity": [str(c) for c in (oracle.get("colorIdentity") or []) if isinstance(c, str)],
        "scryfall_id": uid if _ID.fullmatch(uid) else "",
        "image_small": image_url(uid, "small"),
        "image": image_url(uid, "normal"),
        "tags": [str(t.get("name") if isinstance(t, dict) else t) for t in (rec.get("tags") or [])],
        "purchase_price": rec.get("purchasePrice"),
        "added_at": str(rec.get("createdAt") or ""),
    }


def front_name(name: str) -> str:
    """A card's front face ("Delver of Secrets" for "Delver of Secrets // Insectile Aberration"):
    a ``cardName`` filter by it matches the record whichever way Archidekt names it."""
    return str(name or "").split(" // ")[0].strip()


def record_key(rec: dict[str, Any]) -> tuple[Any, str, str, str]:
    """What makes two records the same entry (0.7.46): the printing (Archidekt's card id), the
    finish, the condition ("" when none is set) and the language (English when none is set)."""
    card = rec.get("card") if isinstance(rec.get("card"), dict) else {}
    return (
        card.get("id"),
        _finish_of(rec.get("modifier")),
        condition_code(rec.get("condition")) or "",
        language_code(rec.get("language")) or "EN",
    )


def in_flight(index: int, name: str, key: tuple[Any, ...], before: int, target: int) -> dict[str, Any]:
    """One card's write as it goes out (CollectionService.add): its line, its card's name, the
    entry it goes to (record_key), the copies that entry held before and holds once it lands.
    What read_back needs to tell whether it landed; kept as JSON with an import."""
    return {"index": index, "name": name, "key": list(key), "before": before, "target": target}


def _may_have_landed(exc: CollectionError) -> bool:
    """Whether a card write that failed may still have reached the binder: Archidekt never
    answered it (a timeout, a 5xx), as opposed to refusing it or the call never leaving."""
    cause = exc.__cause__
    return isinstance(cause, (DeckError, ArchidektError)) and may_have_landed(cause)  # type: ignore[arg-type]


class _Found:
    """The records an add or a record lookup has read so far, by record_key."""

    def __init__(self) -> None:
        self.records: list[dict[str, Any]] = []
        self._ids: set[Any] = set()
        self.existing: dict[tuple[Any, str, str, str], dict[str, Any]] = {}
        self.names: set[str] = set()  # front faces already read by the cardName filter
        self.tried_whole = False
        self.whole = False  # one read held the whole binder
        self.stamp: int | None = None  # the member's collection writes when these were read
        # printings resolved so far, by (name folded, set, number, Scryfall id): kept through a
        # re-read, so an import resolves each distinct card once
        self.printings: dict[tuple[str, str, str, str], dict[str, Any]] = getattr(self, "printings", {})

    def take(self, results: Any) -> None:
        for rec in results if isinstance(results, list) else []:
            if not isinstance(rec, dict) or rec.get("id") in self._ids:
                continue
            self._ids.add(rec.get("id"))
            self.records.append(rec)
            # newest first: the oldest record of an entry is the one copies go to
            self.existing[record_key(rec)] = rec


class CollectionService:
    """The member's Archidekt Collection. Every method runs under the member's own Archidekt
    session through ``DeckService._call`` (token refresh, one call at a time per member)."""

    def __init__(self, decks: DeckService, scan: ScanService | None):
        self.decks = decks
        self.client = decks.client
        self.scan = scan
        # The unfiltered first page per member and sort, kept FIRST_PAGE_TTL seconds: the page
        # most views of /collection show (view changes never reach Archidekt). Any collection
        # write the gateway sends for the member drops it (decks.write_hooks).
        self._first_pages: dict[str, dict[str, tuple[float, dict[str, Any]]]] = {}
        self.first_page_ttl = FIRST_PAGE_TTL
        self._writes: dict[str, int] = {}  # collection writes sent per member (_on_write)
        # The Binder read whole for a tag filter, per member (WHOLE_TTL seconds; any collection
        # write drops it): (when, records, tags, complete)
        self._whole: dict[str, tuple[float, list[dict[str, Any]], list[dict[str, Any]], bool]] = {}
        decks.write_hooks.append(self._on_write)
        # the Binder as last read whole, for its value and the price sort (binder_prices.py): the
        # gateway's own writes keep it current
        self.binder = BinderStore(decks.db)

    def _on_write(self, sub: str, path: str) -> None:
        if not path or path.startswith("/collection"):
            self._first_pages.pop(sub, None)
            self._whole.pop(sub, None)
            # counted, so records an add read before another write are read again (_records_for);
            # the add's own writes keep what it read current, so they are not
            if not _own_write.get():
                self._writes[sub] = self._writes.get(sub, 0) + 1

    def _wrote(
        self,
        sub: str,
        rid: Any,
        row: dict[str, Any] | None,
        fields: tuple[str, ...] = ("quantity",),
        *,
        new: bool = False,
    ) -> None:
        """A record the gateway just wrote: ``row`` as it is now (row_out shape), or None when it
        was removed. The Binder as last read whole (binder_prices) keeps it, and so do the Binder
        pages the member last read (decks.short_reads, shown while Archidekt can't be asked): a
        row of theirs takes the ``fields`` the write set, a removed row goes. A ``new`` record (an
        add made it) is not put among their rows, since where it would sit in a held page isn't
        known (a sort, a filter, a later page): it is kept beside them (held_added), for the page
        to put first in Recently added or count as not shown, once and with the gateway's own
        price. A record that isn't new but isn't on a held page (further down, filtered out) is
        left out of it, as before."""
        if row is None:
            self.binder.drop(sub, rid)
        else:
            self.binder.put(sub, row)
        if not isinstance(rid, int):
            return
        changed = {k: row[k] for k in fields if k in row} if row is not None else None

        def amend(held: Any) -> None:
            if not isinstance(held, dict):
                return
            got = held.get("got") if isinstance(held.get("got"), dict) else {}
            out = got.get("out") if isinstance(got.get("out"), dict) else {}
            whole = held.get("whole") if isinstance(held.get("whole"), dict) else {}
            hit = False
            for where in (got, out, whole):
                rows = where.get("rows")
                if not isinstance(rows, list):
                    continue
                mine = [r for r in rows if isinstance(r, dict) and r.get("id") == rid]
                for r in mine:
                    hit = True
                    if changed is None:
                        rows.remove(r)
                    else:
                        r.update(changed)
                        if r.get("price") is not None:  # the row's value, for its copies now
                            self.binder.with_prices([r])
                if mine and changed is None and where is got:
                    for k in ("total", "shown"):
                        if isinstance(got.get(k), int):
                            got[k] = max(0, got[k] - len(mine))
            added = held.get("added") if isinstance(held.get("added"), dict) else None
            if changed is None and not (added is not None and rid in added["ids"]):
                # a record the read held (on this page or not) is gone: held_view counts it out
                gone = held.setdefault("removed", [])
                if rid not in gone:
                    gone.append(rid)
            if added is not None and rid in added["ids"]:  # one added since: kept as it is now
                if changed is None:
                    added["ids"].remove(rid)
                    added["rows"].pop(str(rid), None)
                elif str(rid) in added["rows"]:
                    added["rows"][str(rid)].update(changed)
            elif new and row is not None and not hit:
                added = held.setdefault("added", {"ids": [], "rows": {}})
                added["ids"].append(rid)
                added["rows"][str(rid)] = {k: v for k, v in row.items() if k not in _ADD_ONLY}
                while len(added["rows"]) > HELD_ADDED_ROWS:  # the newest are the ones shown
                    added["rows"].pop(next(iter(added["rows"])))
            if hit:
                held["amended"] = True

        self.decks.short_reads.amend_last(sub, "binder", amend)

    def _user_id(self, sub: str) -> str:
        link = self.decks.db.get_link(sub)
        if link is None:
            raise CollectionError("not_linked", refusals.not_linked(self.decks.settings.public_url))
        uid = link.get("archidekt_user_id")
        if not uid or not str(uid).isdigit():
            raise CollectionError("not_linked", refusals.no_user_id(self.decks.settings.public_url))
        return str(uid)

    def _writes_on(self) -> None:
        """Collection edits obey MTG_WRITES_ENABLED like every other Archidekt write."""
        try:
            self.decks._writes_switch()
        except DeckError as exc:
            raise CollectionError(exc.kind, str(exc)) from exc

    async def _run(self, sub: str, fn: Any, *, progress: dict[str, Any] | None = None) -> Any:
        """``progress``: a write of a collection proposal's apply, recorded there (one record
        changed, or a write that may have landed) so a stop says what had changed and a proposal
        that changed something never goes back to pending."""
        try:
            out = await _writing(progress, self.decks._call(sub, fn))
        except (DeckError, ArchidektError) as exc:
            raise _wrap(exc) from exc
        if progress is not None:
            progress["sent_entries"] = int(progress.get("sent_entries") or 0) + 1
        return out

    # -- reads -----------------------------------------------------------------------------------
    async def page(
        self,
        sub: str,
        *,
        page: int = 1,
        q: str = "",
        sort: str = "added",
        page_size: int = PAGE_SIZE,
        descending: bool = True,
    ) -> dict[str, Any]:
        """One page of the collection as Archidekt orders and filters it (``cardName`` is a name
        substring; ``descending`` is the Set release direction, Archidekt's default order has
        none). Returns rows, count, page, total_pages and has_next."""
        uid = self._user_id(sub)
        cacheable = page == 1 and not q and page_size == PAGE_SIZE and self.first_page_ttl > 0
        key = sort if descending else f"{sort}:asc"
        if cacheable and not reloading((id(self), sub, key)):  # a reload reads the page again
            hit = self._first_pages.get(sub, {}).get(key)
            if hit is not None and time.monotonic() - hit[0] < self.first_page_ttl:
                return {**hit[1], "rows": list(hit[1]["rows"])}
        order = "editionDate" if sort == "edition" else ""
        body = await self._run(
            sub,
            lambda token: self.client.collection_page(
                token, uid, page=page, page_size=page_size, card_name=q, order_by=order, descending=descending
            ),
        )
        records = [r for r in body["results"] if isinstance(r, dict)]
        rows = [row_out(r) for r in records]
        tags = collection_tags.tag_list(body)
        collection_tags.name_rows(rows, records, tags)
        count = body.get("count")
        total_pages = body.get("totalPages")
        out = {
            "rows": rows,
            "tags": tags,
            "count": count if isinstance(count, int) else len(rows),
            "page": body.get("page") if isinstance(body.get("page"), int) else page,
            "total_pages": total_pages if isinstance(total_pages, int) else 1,
            "has_next": bool(body.get("next")),
        }
        if cacheable:
            if len(self._first_pages) > 10_000:
                self._first_pages.clear()
            self._first_pages.setdefault(sub, {})[key] = (time.monotonic(), out)
            return {**out, "rows": list(rows)}
        return out

    async def _raw_rows(self, sub: str, *, max_pages: int) -> list[dict[str, Any]]:
        """Archidekt's records, newest first, over up to ``max_pages`` pages."""
        uid = self._user_id(sub)
        rows: list[dict[str, Any]] = []
        page = 1
        while page <= max_pages:
            body = await self._run(
                sub, lambda token, page=page: self.client.collection_page(token, uid, page=page)
            )
            rows += [r for r in body["results"] if isinstance(r, dict)]
            if not body.get("next"):
                break
            page += 1
        return rows

    async def all_rows(self, sub: str, *, max_pages: int = MAX_PAGES_FOR_EXPORT) -> list[dict[str, Any]]:
        return [row_out(r) for r in await self._raw_rows(sub, max_pages=max_pages)]

    async def read_whole(self, sub: str) -> tuple[list[dict[str, Any]], bool]:
        """The whole Binder for its value and the price sort (the daily price check, as the
        member): pages of BINDER_PAGE_SIZE, at most BINDER_READ_PAGES of them. Returns the rows
        and whether that was all of it."""
        uid = self._user_id(sub)
        rows: list[dict[str, Any]] = []
        for page in range(1, BINDER_READ_PAGES + 1):
            body = await self._run(
                sub,
                lambda token, page=page: self.client.collection_page(
                    token, uid, page=page, page_size=BINDER_PAGE_SIZE
                ),
            )
            rows += [row_out(r) for r in body["results"] if isinstance(r, dict)]
            if not body.get("next"):
                return rows, True
        return rows, False

    async def summary(self, sub: str) -> dict[str, Any]:
        """Record count from Archidekt's own listing (one call); copies are summed over the first
        page only when the collection fits in one page, else left out."""
        out = await self.page(sub, page=1)
        totals: dict[str, Any] = {"rows": out["count"]}
        if out["total_pages"] <= 1:
            totals["cards"] = sum(int(r["quantity"]) for r in out["rows"])
        return totals

    async def tags(self, sub: str) -> list[dict[str, Any]]:
        """The member's collection tags, from the first page (served from memory a short while)."""
        return list((await self.page(sub))["tags"])

    async def whole(self, sub: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
        """(every record, the tag list, whether that is the whole Binder), read in pages of 500 and
        kept WHOLE_TTL seconds; any collection write the gateway sends drops it."""
        hit = self._whole.get(sub)
        fresh = hit is not None and time.monotonic() - hit[0] < WHOLE_TTL
        if hit is not None and fresh and not reloading((id(self), sub, "whole")):
            return hit[1], hit[2], hit[3]
        try:
            records, tags, complete = await collection_tags.whole_records(self.decks, sub)
        except (DeckError, ArchidektError) as exc:
            raise _wrap(exc) from exc
        if len(self._whole) > 1_000:
            self._whole.clear()
        self._whole[sub] = (time.monotonic(), records, tags, complete)
        return records, tags, complete

    async def tagged(
        self, sub: str, tag: str, *, q: str = "", offset: int = 0, limit: int = PAGE_SIZE
    ) -> dict[str, Any]:
        """The entries carrying the tag ``tag`` (by name, ignoring case), newest first, with a name
        filter ``q``: Archidekt's own tag filter was not recorded, so the Binder is read whole
        (``whole``) and filtered here. ``rows`` from ``offset``, ``total``, ``complete`` (False when
        the Binder is larger than a whole read takes) and ``known`` (the member has that tag)."""
        records, tags, complete = await self.whole(sub)
        want = next((t for t in tags if collection_tags.same_name(t["name"], tag)), None)
        if want is None:
            return {"rows": [], "total": 0, "complete": complete, "tags": tags, "known": False}
        by_id = {t["id"]: t for t in tags}
        needle = q.casefold()
        hits = []
        for rec in records:
            names = collection_tags.record_tag_names(rec, by_id)
            if not any(collection_tags.same_name(n, want["name"]) for n in names):
                continue
            row = row_out(rec)
            row["tags"] = names
            if needle and needle not in row["name"].casefold():
                continue
            hits.append(row)
        return {
            "rows": hits[offset : offset + limit],
            "total": len(hits),
            "complete": complete,
            "tags": tags,
            "known": True,
        }

    async def find_by_name(self, sub: str, name: str) -> list[dict[str, Any]]:
        want = _clean(name, 200).casefold()
        out = await self.page(sub, q=want[:80], page_size=200)
        return [r for r in out["rows"] if r["name"].casefold() == want]

    # -- writes ----------------------------------------------------------------------------------
    async def add(
        self,
        sub: str,
        items: Any,
        *,
        source: str,
        progress: dict[str, Any] | None = None,
        result: dict[str, Any] | None = None,
        on_item: Callable[[int], None] | None = None,
        retry: Callable[[CollectionError], Awaitable[bool]] | None = None,
        before_item: Callable[[], Awaitable[None]] | None = None,
        found: _Found | None = None,
        limit: int = MAX_ITEMS_PER_CALL,
        trusted: bool = False,
        first_index: int = 0,
        before_write: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Add cards to the member's Archidekt Collection. Each item names a printing by
        ``scryfall_id`` (a scan result) or by ``name`` with optional ``set`` and
        ``collector_number``; ``quantity``, ``finish`` (or ``foil: true``), ``condition`` and
        ``language`` (English when not given) are optional. The copies go into a record only
        when its printing, finish, condition and language all match (0.7.46: no condition picked
        is Near Mint, so it also joins a record with none set); otherwise a record is created.
        Each ``added`` row carries ``index`` (its position in ``items``) and ``went_to``
        ("existing" or "new"); ``skipped`` items carry ``index`` too. ``progress``: a collection proposal's
        apply, which counts each record written (``_run``). ``result``: the dict the rows go
        into as they are written (an import keeps what was added when it stops part way);
        ``on_item(n)`` is told after each card; ``retry(exc)`` decides, after a refusal of the
        member's Archidekt budget, whether to try that card again (an import waits for room);
        ``before_item()`` is awaited before each card (an import's pause while the budget is low).
        A long import (collection_bulk) passes its own ``found`` (what it has read of the Binder,
        kept between its chunks), ``limit``, ``trusted`` (a restore: normalise_items) and
        ``first_index`` (its chunk's first line); the rows' ``index`` count from it.
        ``before_write(card)`` is told, just before a card's write is sent, what that write is
        (``in_flight``), so an import keeps it and a restart or a lost answer never adds it twice.

        Outside a proposal's apply, a card whose write Archidekt may have taken without answering
        (a timeout after it landed) is read back (``read_back``): when it landed it is counted as
        added, when it did not the failure stands, and when the read fails too the error carries
        the card as ``in_flight``, so whoever sends it again reads it back first."""
        self._writes_on()
        wanted = [
            (it, {k: it[k] for k in ("quantity", "finish", "condition", "language")})
            for it in normalise_items(items, limit=limit, trusted=trusted)
        ]
        self._user_id(sub)  # fail early when no account is linked
        found = found if found is not None else _Found()
        out = result if result is not None else {}
        out.setdefault("added", [])
        out.setdefault("skipped", [])
        start = len(out["added"])
        for n, (spec, opts) in enumerate(wanted):
            index = first_index + n
            while True:
                if before_item is not None:
                    await before_item()
                try:
                    await self._add_one(sub, index, spec, opts, found, out, progress, before_write)
                    break
                except CollectionError as exc:
                    # ``retry``: an import waits out a refusal of the member's budget, or Archidekt
                    # asking it to slow down (nothing was sent either way); it decides which
                    if retry is not None and await retry(exc):
                        continue
                    raise
            if on_item is not None:
                on_item(n + 1)
        added = out["added"][start:]
        self.decks.db.audit(
            "collection_added",
            sub=sub,
            detail={"rows": len(added), "cards": sum(int(r["quantity"]) for r in added), "source": source},
        )
        return out

    async def _records_for(self, sub: str, name: str, found: _Found) -> None:
        """Make ``found`` hold every record that could be ``name``: the whole binder in one read when
        it is small, else Archidekt's ``cardName`` filter for that name (a front face matches both
        faces' names). Never pages through the whole binder, so no record is out of reach."""
        uid = self._user_id(sub)
        if found.stamp is not None and found.stamp != self._writes.get(sub, 0):
            # another write (another tab, the member's own + while an import runs) came after these
            # were read: read again rather than write copies counted from an old quantity
            found.__init__()
        if found.stamp is None:
            found.stamp = self._writes.get(sub, 0)
        if found.whole:
            return
        if not found.tried_whole:
            found.tried_whole = True
            body = await self._run(
                sub, lambda token: self.client.collection_page(token, uid, page=1, page_size=WHOLE_READ_SIZE)
            )
            found.take(body["results"])
            if not body.get("next"):
                found.whole = True
                return
        key = front_name(name).casefold()
        if not key or key in found.names:
            return
        found.names.add(key)
        for page in range(1, FILTERED_PAGES + 1):
            body = await self._run(
                sub,
                lambda token, page=page: self.client.collection_page(
                    token, uid, page=page, page_size=WHOLE_READ_SIZE, card_name=front_name(name)[:80]
                ),
            )
            found.take(body["results"])
            if not body.get("next"):
                break

    async def _own_write(self, sub: str, fn: Any, progress: dict[str, Any] | None) -> dict[str, Any]:
        """An add's write, not counted as another write (the add updates ``found`` itself)."""
        mark = _own_write.set(True)
        try:
            return await self._run(sub, fn, progress=progress)
        finally:
            _own_write.reset(mark)

    async def _add_one(
        self,
        sub: str,
        index: int,
        spec: dict[str, Any],
        opts: dict[str, Any],
        found: _Found,
        out: dict[str, Any],
        progress: dict[str, Any] | None,
        before_write: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """One card of ``add``: resolve its printing, find a record of it in that finish (found),
        and add the copies there or create one. Its row lands in ``out``. A printing already
        resolved in this add (``found.printings``: the same name, set, number and Scryfall id) and
        a restore's own Archidekt printing id (``card_id``) cost no lookup."""
        ident = (spec["name"].casefold(), spec["set"], spec["collector_number"], spec["scryfall_id"])
        printing = found.printings.get(ident)
        if printing is None and spec.get("card_id"):
            printing = {
                "id": spec["card_id"],
                "name": spec["name"],
                "set_code": spec["set"],
                "collector_number": spec["collector_number"],
                "options": [],
            }
        if printing is None:
            try:
                printing = await self._run(
                    sub,
                    lambda token: self.client.resolve_card(
                        token,
                        spec["name"],
                        set_code=spec["set"] or None,
                        collector_number=spec["collector_number"] or None,
                        scryfall_id=spec["scryfall_id"] or None,
                    ),
                )
            except CollectionError as exc:
                if exc.kind in ("not_found", "contract", "invalid", "unavailable"):
                    out["skipped"].append(
                        {"input": spec, "status": "not_found", "note": str(exc), "index": index}
                    )
                    return
                raise
            if len(found.printings) < 5_000:
                found.printings[ident] = printing
        finish = opts["finish"]
        options = printing.get("options") or []
        if options and MODIFIERS[finish] not in options and len(options) == 1:
            finish = _finish_of(options[0])  # a printing sold in one finish is that finish
        await self._records_for(sub, str(printing.get("name") or spec["name"]), found)
        cond, lang = opts["condition"], opts["language"]
        # no condition picked is written Near Mint, so it joins a Near Mint record, else one with
        # none set; a condition picked joins only its own
        keys = [(printing["id"], finish, c, lang) for c in ((cond,) if cond else ("NM", ""))]
        key = next((k for k in keys if k in found.existing), keys[0])
        have = found.existing.get(key)
        before = int(have.get("quantity") or 0) if have is not None else 0
        qty = min(MAX_QUANTITY, before + opts["quantity"]) if have is not None else opts["quantity"]
        card = in_flight(index, str(printing.get("name") or spec["name"]), key, before, qty)
        if before_write is not None:
            before_write(card)
        try:
            rec = await self._write_one(sub, have, qty, printing, spec, opts, finish, progress)
        except CollectionError as exc:
            if progress is not None or not _may_have_landed(exc):
                raise
            try:
                landed = await self.read_back(sub, card)
            except Exception as again:  # the read failed too: whoever sends it again reads first
                exc.in_flight = card  # type: ignore[attr-defined]
                raise exc from again
            if landed is None:
                raise  # read back as it was: the write did not land, the failure stands
            rec = (
                landed
                if isinstance(landed.get("card"), dict)
                else self._landed_stub(have, printing, spec, qty)
            )
        found.existing[record_key(rec)] = rec
        row = row_out(rec)
        # the row's quantity is what the record holds now; these say what this add changed
        row["previous_quantity"] = before
        row["added_quantity"] = int(row["quantity"]) - before
        row["went_to"] = "existing" if have is not None else "new"
        row["index"] = index
        if spec["scryfall_id"] and not row.get("scryfall_id"):
            row["scryfall_id"] = spec["scryfall_id"]
            row["image_small"] = image_url(spec["scryfall_id"], "small")
            row["image"] = image_url(spec["scryfall_id"], "normal")
        self._wrote(sub, row.get("id"), row, new=have is None)
        out["added"].append(row)

    async def read_back(self, sub: str, card: dict[str, Any]) -> dict[str, Any] | None:
        """Whether the write ``card`` (``in_flight``) landed, by the member's binder as it is
        now: the record it went to, or None when that entry still holds the copies it held before
        (or, for a new entry, there is none). Anything else counts as landed, so a card is never
        sent twice; Archidekt's errors are raised as they are."""
        found = _Found()
        found.tried_whole = True
        await self._records_for(sub, str(card["name"]), found)
        key = tuple(card["key"])
        rec = found.existing.get(key)  # type: ignore[call-overload]
        qty = int(rec.get("quantity") or 0) if rec is not None else 0
        if qty == int(card["before"]):
            return None
        return rec if rec is not None else {"quantity": card["target"]}

    def _landed_stub(
        self, have: dict[str, Any] | None, printing: dict[str, Any], spec: dict[str, Any], qty: int
    ) -> dict[str, Any]:
        """The record for a write read back as landed when the entry it went to is no longer
        there to show (deleted meanwhile): as the write left it."""
        if have is not None:
            return {**have, "quantity": qty}
        return {"quantity": qty, "card": self._card_stub(printing, spec, {})}

    async def _write_one(
        self,
        sub: str,
        have: dict[str, Any] | None,
        qty: int,
        printing: dict[str, Any],
        spec: dict[str, Any],
        opts: dict[str, Any],
        finish: str,
        progress: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """The one write of a card of ``add``: its copies onto the entry ``have`` (``qty`` in all),
        or a new entry. Returns the record as written."""
        if have is not None:
            written = await self._own_write(
                sub,
                lambda token: self.client.collection_set(token, have, quantity=qty),
                progress,
            )
            return {**have, **written, "card": have.get("card")}
        written = await self._own_write(
            sub,
            lambda token: self.client.collection_add(
                token,
                printing["id"],
                opts["quantity"],
                modifier=MODIFIERS[finish],
                # with none picked, English and Near Mint, as archidekt.com's own new
                # record (its site code)
                condition=opts["condition"] or "NM",
                language=opts["language"],
                tags=spec.get("tag_ids") or None,
                purchase_price=spec.get("purchase_price"),
            ),
            progress,
        )
        return {
            "condition": opts["condition"] or "NM",
            "language": opts["language"],
            **written,
            "modifier": MODIFIERS[finish],
            "card": self._card_stub(printing, spec, written),
        }

    @staticmethod
    def _card_stub(printing: dict[str, Any], spec: dict[str, Any], rec: dict[str, Any]) -> dict[str, Any]:
        """The card object for a freshly created record: Archidekt's create answer may carry only
        the card id, so the printing we resolved fills the name, set and number."""
        card = rec.get("card") if isinstance(rec.get("card"), dict) else {}
        return {
            **card,
            "id": card.get("id") or printing["id"],
            "uid": card.get("uid") or spec["scryfall_id"],
            "collectorNumber": card.get("collectorNumber") or printing.get("collector_number"),
            "edition": card.get("edition") or {"editioncode": printing.get("set_code") or ""},
            "oracleCard": card.get("oracleCard") or {"name": printing.get("name") or spec["name"]},
        }

    async def _record(self, sub: str, rid: int, name: str = "") -> dict[str, Any]:
        """The raw record for an id (Archidekt's v2 list has no single-record read). With the
        card's ``name`` (the page sends it) one read by Archidekt's ``cardName`` filter finds it
        wherever it sits in the binder; without one, the newest ten pages are looked through."""
        uid = self._user_id(sub)
        if name.strip():
            found = _Found()
            found.tried_whole = True
            await self._records_for(sub, name, found)
            for rec in found.records:
                if rec.get("id") == rid:
                    return rec
            raise CollectionError("not_found", "Your binder has no card with that ID.")
        page = 1
        while page <= 10:
            body = await self._run(
                sub, lambda token, page=page: self.client.collection_page(token, uid, page=page)
            )
            for rec in body["results"]:
                if isinstance(rec, dict) and rec.get("id") == rid:
                    return rec
            if not body.get("next"):
                break
            page += 1
        raise CollectionError("not_found", "Your binder has no card with that ID.")

    async def set_quantity(
        self, sub: str, rid: int, quantity: int | None = None, *, delta: int = 0, name: str = ""
    ) -> dict[str, Any]:
        """Set a record's copies, or (``delta``) add to or take from what Archidekt holds now, so
        two tabs or devices that each press + add two copies, never one. 0 or less removes it."""
        self._writes_on()
        rec = await self._record(sub, rid, name)
        if quantity is None:
            quantity = min(MAX_QUANTITY, int(rec.get("quantity") or 0) + delta)
        if quantity <= 0:
            await self._run(sub, lambda token: self.client.collection_delete(token, [rid]))
            self._wrote(sub, rid, None)
            self.decks.db.audit("collection_removed", sub=sub, detail={"id": rid})
            return {"row": None, "removed": await self.removed_out(sub, rec)}
        out = await self._run(sub, lambda token: self.client.collection_set(token, rec, quantity=quantity))
        row = row_out({**rec, **out, "card": rec.get("card")})
        self._wrote(sub, rid, row)
        return {"row": row}

    async def update(
        self,
        sub: str,
        rid: int,
        data: dict[str, Any],
        *,
        name: str = "",
        progress: dict[str, Any] | None = None,
        raise_wrong: bool = True,
    ) -> dict[str, Any]:
        """Change a record's details: finish, condition, language, price paid and tags (the fields
        Archidekt's own row editor offers). ``tags``: the entry's tags by name, all of them (the
        member's binder tags; a name they have no tag for is refused). ``progress``: a collection
        proposal's apply (``_run``); with ``raise_wrong`` False, an answer that does not show the
        change is returned as ``wrong`` (the fields) instead of refused."""
        self._writes_on()
        rec = await self._record(sub, rid, name)
        tags: list[dict[str, Any]] = []
        tag_ids: list[int] | None = None
        if "tags" in data:
            try:
                names = collection_tags.clean_names(data["tags"])
            except DeckError as exc:
                raise CollectionError("invalid", str(exc)) from exc
            tags = await self.tags(sub)
            tag_ids, missing = collection_tags.ids_for(names, tags)
            if missing:
                raise CollectionError(
                    "not_found",
                    f"You have no binder tag called “{missing[0]}”. Make it first under Binder › Lists › "
                    "Binder tags.",
                )
            if sorted(tag_ids) == sorted(collection_tags.record_tag_ids(rec)):
                tag_ids = None  # the same tags: nothing to send for them
        opts = _item_options({k: v for k, v in data.items() if k in ("finish", "foil", "condition")}, 0)
        changes: dict[str, Any] = {}
        if "finish" in data or "foil" in data:
            changes["modifier"] = MODIFIERS[opts["finish"]]
        if "condition" in data:
            changes["condition"] = opts["condition"] or None
        if "language" in data or "lang" in data:
            changes["language"] = _language(data.get("language", data.get("lang")))
        if "purchase_price" in data:
            changes["purchasePrice"] = _price(data["purchase_price"])
        if tag_ids is not None:
            changes["tags"] = tag_ids
        if not changes:
            if "tags" in data:  # tags alone, and they are already so
                row = row_out(rec)
                collection_tags.name_rows([row], [rec], tags)
                return {"row": row}
            raise CollectionError("invalid", "Nothing to change: give finish, condition, language or price.")
        if not tags and rec.get("tags"):
            # the row's tags by name (the first page is usually still in memory: no read)
            tags = await self.tags(sub)
        out = await self._run(
            sub, lambda token: self.client.collection_set(token, rec, **changes), progress=progress
        )
        merged = {**rec, **out, "card": rec.get("card")}
        row = row_out(merged)
        collection_tags.name_rows([row], [merged], tags)
        self._wrote(sub, rid, row, ("finish", "condition", "lang", "purchase_price", "tags"))
        read = {  # what Archidekt answered, in the gateway's terms (it keeps numbers for two)
            "purchasePrice": _price(out.get("purchasePrice")),
            "condition": condition_code(out.get("condition")) or None,
            "language": language_code(out.get("language")),
            "tags": sorted(collection_tags.record_tag_ids(out)) if "tags" in out else None,
        }
        if "tags" in changes:
            changes = {**changes, "tags": sorted(changes["tags"])}
        wrong = [k for k, want in changes.items() if read.get(k, out.get(k)) != want]
        self.decks.db.audit(
            "collection_updated",
            sub=sub,
            detail={"id": rid, "fields": sorted(changes), "verified": not wrong},
        )
        if wrong and raise_wrong:
            raise CollectionError(
                "unavailable",
                "Archidekt accepted the change but answered with other values for: " + ", ".join(wrong) + ".",
            )
        return {"row": row, "wrong": wrong}

    async def remove(
        self,
        sub: str,
        rid: int,
        quantity: int | None = None,
        *,
        progress: dict[str, Any] | None = None,
        name: str = "",
    ) -> dict[str, Any]:
        self._writes_on()
        rec = await self._record(sub, rid, name)
        have = int(rec.get("quantity") or 0)
        if quantity is None or quantity >= have:
            await self._run(sub, lambda token: self.client.collection_delete(token, [rid]), progress=progress)
            self._wrote(sub, rid, None)
            left = None
        else:
            out = await self._run(
                sub,
                lambda token: self.client.collection_set(token, rec, quantity=have - max(1, quantity)),
                progress=progress,
            )
            left = row_out({**rec, **out, "card": rec.get("card")})
            self._wrote(sub, rid, left)
        self.decks.db.audit("collection_removed", sub=sub, detail={"id": rid, "quantity": quantity})
        return {"removed": await self.removed_out(sub, rec), "row": left}

    async def removed_out(self, sub: str, rec: dict[str, Any]) -> dict[str, Any]:
        """A removed record for the page's Undo: its tags by name (Archidekt sends ids), so the
        row that comes back gets them again (0.7.46 audit QG-09)."""
        row = row_out(rec)
        if rec.get("tags"):
            try:
                collection_tags.name_rows([row], [rec], await self.tags(sub))
            except DeckError:
                pass  # the tag names could not be read: the row comes back without them
        return row

    async def export_csv(self, sub: str) -> str:
        """The collection as CSV: Quantity, Name, Finish, Condition, Language, Edition Code,
        Edition Name, Collector Number, Scryfall ID, Date Added. Archidekt's collection import
        does not read these column names: on archidekt.com the person picks each column's type
        (Quantity, Card name, Foil/Variant, Condition, Language, Edition code, Edition name,
        Collector number, Scryfall ID, and Ignore for Date Added) and leaves "Skip first line" on
        (read from its site's import page; see ``EXPORT_COLUMN_TYPES``). Condition and Language are
        written in Archidekt's codes (D for DMG; JP, KR, SP, CS, CT for JA, KO, ES, ZHS, ZHT),
        which the gateway's own import reads back too; the pages keep showing the gateway's."""
        out = io.StringIO()
        w = csv.writer(out)
        w.writerow(
            [
                "Quantity",
                "Name",
                "Finish",
                "Condition",
                "Language",
                "Edition Code",
                "Edition Name",
                "Collector Number",
                "Scryfall ID",
                "Date Added",
            ]
        )
        for r in await self.all_rows(sub):
            w.writerow(
                [
                    r["quantity"],
                    r["name"],
                    MODIFIERS[r["finish"]],
                    ARCHIDEKT_CONDITION.get(r["condition"], r["condition"]),
                    LANGUAGE_TO_ARCHIDEKT.get(r["lang"], r["lang"]),
                    r["set"].upper(),
                    r["set_name"],
                    r["collector_number"],
                    r["scryfall_id"],
                    r["added_at"][:10],
                ]
            )
        return out.getvalue()


def _item_options(raw: dict[str, Any], i: int) -> dict[str, Any]:
    qty = raw.get("quantity", 1)
    if isinstance(qty, str) and qty.strip().isdigit():
        qty = int(qty)
    if not isinstance(qty, int) or isinstance(qty, bool) or qty < 1 or qty > MAX_QUANTITY:
        raise CollectionError(
            "invalid", f"Card {i}: quantity must be a whole number from 1 to {MAX_QUANTITY}."
        )
    finish = _clean(raw.get("finish"), 10).lower()
    if not finish:
        finish = "foil" if raw.get("foil") is True else "nonfoil"
    if finish == "normal":
        finish = "nonfoil"
    if finish not in FINISHES:
        raise CollectionError("invalid", f"Card {i}: finish must be one of {', '.join(FINISHES)}.")
    condition = _clean(raw.get("condition"), 4).upper()
    if condition == "D":  # Archidekt's own code for Damaged
        condition = "DMG"
    if condition not in CONDITIONS:
        raise CollectionError("invalid", f"Card {i}: condition must be one of NM, LP, MP, HP, DMG or empty.")
    language = _clean(raw.get("language"), 3).upper() or "EN"
    language = ARCHIDEKT_TO_LANGUAGE.get(language, language)
    if language not in LANGUAGES:
        raise CollectionError("invalid", f"Card {i}: language must be one of " + ", ".join(LANGUAGES) + ".")
    return {"quantity": qty, "finish": finish, "condition": condition, "language": language}


# The languages Archidekt's collection takes (its site's own list of eleven, read 2026-10-14; see
# LANGUAGE_IDS), in the order the details menu offers them. Archidekt has no Phyrexian.
LANGUAGES = ("EN", "ES", "FR", "DE", "IT", "PT", "JA", "KO", "RU", "ZHS", "ZHT")
# Archidekt's site writes five language codes and one condition code differently from the
# gateway (its bundle's tables, see LANGUAGE_IDS / CONDITION_IDS): the collection CSV export
# writes Archidekt's, the import reads both.
LANGUAGE_TO_ARCHIDEKT = {"JA": "JP", "KO": "KR", "ES": "SP", "ZHS": "CS", "ZHT": "CT"}
ARCHIDEKT_TO_LANGUAGE = {v: k for k, v in LANGUAGE_TO_ARCHIDEKT.items()}
# Names for the import's result: Archidekt's eleven, then printed languages its collection has no
# code for (an import keeps those rows as English and names them).
LANGUAGE_NAMES = {
    "EN": "English",
    "ES": "Spanish",
    "FR": "French",
    "DE": "German",
    "IT": "Italian",
    "PT": "Portuguese",
    "JA": "Japanese",
    "KO": "Korean",
    "RU": "Russian",
    "ZHS": "Chinese (Simplified)",
    "ZHT": "Chinese (Traditional)",
}
OTHER_LANGUAGE_NAMES = {
    "PH": "Phyrexian",
    "HE": "Hebrew",
    "LA": "Latin",
    "AR": "Arabic",
    "SA": "Sanskrit",
    "GRC": "Ancient Greek",
}
ARCHIDEKT_CONDITION = {"DMG": "D"}
# The column type to pick on archidekt.com's collection import for each column of the export, by
# the labels its import page offers (its site's own list, read 2026-10-14). It maps columns by
# position, not by name, and skips the first line by default.
EXPORT_COLUMN_TYPES = (
    ("Quantity", "Quantity"),
    ("Name", "Card name"),
    ("Finish", "Foil/Variant"),
    ("Condition", "Condition"),
    ("Language", "Language"),
    ("Edition Code", "Edition code"),
    ("Edition Name", "Edition name"),
    ("Collector Number", "Collector number"),
    ("Scryfall ID", "Scryfall ID"),
    ("Date Added", "Ignore"),
)


def _language_name(code: str) -> str:
    code = code.strip()
    return LANGUAGE_NAMES.get(code.upper()) or OTHER_LANGUAGE_NAMES.get(code.upper()) or code


def import_result(items: list[dict[str, Any]], out: dict[str, Any]) -> dict[str, Any]:
    """What an import did, for the Collection page's notice: ``items`` as parse_collection_import
    returned them, ``out`` as ``add`` returned it. Plain data (copies added, and the rows kept as
    English, kept as Near Mint, added to an entry already there, or not found, each with where it
    was)."""

    def where(i: int) -> str:
        item = items[i] if 0 <= i < len(items) else {}
        return f"line {item['line']}" if isinstance(item.get("line"), int) else f"row {i + 1}"

    def name(i: int) -> str:
        return str(items[i].get("name") or "?") if 0 <= i < len(items) else "?"

    missing = sorted({int(s["index"]) for s in out.get("skipped") or [] if isinstance(s.get("index"), int)})
    kept: dict[str, list[dict[str, str]]] = {}
    for i, item in enumerate(items):
        if item.get("language_given") and i not in missing:
            lang = _language_name(str(item["language_given"]))
            kept.setdefault(lang, []).append({"where": where(i), "name": name(i)})
    near_mint: dict[str, list[dict[str, str]]] = {}
    for i, item in enumerate(items):
        if item.get("condition_given") and i not in missing:
            given = str(item["condition_given"]).strip()
            near_mint.setdefault(given, []).append({"where": where(i), "name": name(i)})
    # rows whose copies joined an entry already there: same printing, finish, condition and
    # language (the merge rule of 0.7.46)
    joined = [
        {"where": where(int(a["index"])), "name": name(int(a["index"]))}
        for a in out.get("added") or []
        if a.get("went_to") == "existing" and isinstance(a.get("index"), int)
    ]
    return {
        "cards": sum(int(it.get("quantity") or 0) for i, it in enumerate(items) if i not in missing),
        "added": bool(out.get("added")),
        "kept": [{"language": k, "rows": v} for k, v in kept.items()],
        "kept_condition": [{"condition": k, "rows": v} for k, v in near_mint.items()],
        "joined": joined,
        "missing": [{"where": where(i), "name": name(i)} for i in missing],
    }


def import_result_html(result: dict[str, Any]) -> str:
    """The Collection page's notice for an import: a refusal (``refused``: the parser's reason) or
    what ``import_result`` found. Everything is escaped."""
    if result.get("refused"):
        reason = str(result["refused"]).strip().rstrip(".")
        # the parser's refusal is a sentence of its own ("Line 3: ..."); here it goes on after a colon
        if reason[1:2].islower():
            reason = reason[:1].lower() + reason[1:]
        return f"<p class='notice error'>{_esc(f'Nothing was imported: {reason}.')}</p>"

    def rows(entries: list[dict[str, str]], extra: str = "") -> str:
        return ", ".join(f"{e['where']} ({e['name']}{(', ' + e[extra]) if extra else ''})" for e in entries)

    notes: list[str] = []
    for group in result.get("kept") or []:
        lang = group["language"]
        # a language Archidekt lacks is named; anything else is quoted as it was typed
        said = lang if lang in OTHER_LANGUAGE_NAMES.values() else f"“{lang}”"
        notes.append(
            f"Kept as English, because {said} is not a language Archidekt stores for collection cards: "
            f"{rows(group['rows'])}."
        )
    for group in result.get("kept_condition") or []:
        # a Condition the gateway does not know (Archidekt's are NM, LP, MP, HP and D), as typed
        notes.append(
            f"Kept as Near Mint, because “{group['condition']}” is not a condition Archidekt stores for "
            f"collection cards: {rows(group['rows'])}."
        )
    joined = result.get("joined") or []
    if joined:
        notes.append(
            f"Added to {'an entry' if len(joined) == 1 else 'entries'} you already had, with the same "
            f"printing, finish, condition and language: {rows(joined)}."
        )
    if result.get("missing"):
        notes.append(f"Not found, so not imported: {rows(result['missing'])}.")
    restore = bool(result.get("restore"))
    if result.get("stopped"):
        # a job that stopped part way: what went in, why it stopped, and what to do
        n = int(result.get("cards") or 0)
        where = "entry" if restore else "line"
        lead = (
            f"{'Put back' if restore else 'Imported'} {n:,} {'card' if n == 1 else 'cards'}, then the "
            f"{'restore' if restore else 'import'} stopped at {where} "
            f"{int(result.get('stopped_at') or 0):,} of {int(result.get('of') or 0):,}: "
            f"{str(result['stopped']).rstrip('.')}. Nothing after that {where} was added"
            + ("." if restore else "; import the rest of the list again.")
        )
        cls = "error"
    elif result.get("added"):
        n = int(result.get("cards") or 0)
        lead = (
            f"Put {n:,} {'card' if n == 1 else 'cards'} back into your binder."
            if restore
            else f"Imported {n:,} {'card' if n == 1 else 'cards'} into your binder."
        )
        cls = "ok"
    else:
        lead, cls = "Nothing was imported: no card in the list was found.", "error"
    items = "".join(f"<li>{_esc(n)}</li>" for n in notes)
    return (
        f"<div class='notice {cls} importres' role='status'><p>{_esc(lead)}</p>"
        + (f"<ul>{items}</ul>" if items else "")
        + "</div>"
    )


def export_help_html() -> str:
    """The Collection page's fold that says how to import the Export CSV on archidekt.com."""
    rows = "".join(
        f"<tr><th scope='row'>{_esc(col)}</th><td>{_esc(kind)}</td></tr>" for col, kind in EXPORT_COLUMN_TYPES
    )
    return (
        f"<details class='csvhelp'><summary class='fold-btn'>{icon('download')} "
        "<span class='t'>Importing the CSV on archidekt.com: column types</span></summary>"
        "<section class='panel'>"
        "<p>Archidekt does not read the column names. When you import the file on archidekt.com, pick "
        "each column's type as below and leave <strong>Skip first line</strong> on.</p>"
        "<div class='tbl'><table><thead><tr><th scope='col'>Column in this file</th>"
        "<th scope='col'>Type to pick on archidekt.com</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
        "<p class='muted small'>The file writes Condition and Language in Archidekt's own codes. "
        "Condition: NM Near Mint, LP Lightly Played, MP Moderately Played, HP Heavily Played and "
        "D Damaged. Language: EN English, DE German, FR French, IT Italian, PT Portuguese, "
        "RU Russian, JP Japanese, KR Korean, SP Spanish, CS Chinese (Simplified) and "
        "CT Chinese (Traditional).</p></section></details>"
    )


MAX_PRICE = 99999.0


def _language(value: Any) -> str:
    code = _clean(value, 3).upper()
    if code not in LANGUAGES:
        raise CollectionError("invalid", "language must be one of " + ", ".join(LANGUAGES) + ".")
    return code


def _price(value: Any) -> float | None:
    """The price paid, as Archidekt stores it (a number or null). Accepts "", None, a number or a
    numeric string, with at most two decimals kept."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        price = round(float(str(value).strip().lstrip("$").replace(",", ".")), 2)
    except ValueError as exc:
        raise CollectionError("invalid", "Price paid must be a number.") from exc
    if price < 0 or price > MAX_PRICE or price != price:
        raise CollectionError("invalid", f"Price paid must be between 0 and {int(MAX_PRICE)}.")
    return price


# -- browser page and JSON -----------------------------------------------------------------------------
_STATUS = {
    "invalid": 400,
    "not_found": 404,
    "not_linked": 409,
    "auth": 409,
    "forbidden": 403,
    "writes_disabled": 403,
    "unavailable": 503,
    "rate_limited": 503,
    "busy": 429,
}


def _fail(exc: CollectionError) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": exc.kind, "message": str(exc)}, _STATUS.get(exc.kind, 400), headers=NO_STORE
    )


def _finish_label(f: str) -> str:
    return {"foil": "Foil", "etched": "Etched"}.get(f, "")


def _rid(value: Any) -> int:
    s = str(value or "").strip()
    if not s.isdigit() or len(s) > 12:
        raise CollectionError("invalid", "That is not a collection record ID.")
    return int(s)


# -- the page's pieces (UI overhaul 2026-10-21, mockup 20-collection) ----------------------------
SHOWN = 24  # a screenful: the rows a page shows before its Load more button
CONDITION_LABELS = {
    "": "Not set",
    "NM": "Near Mint (NM)",
    "LP": "Lightly Played (LP)",
    "MP": "Moderately Played (MP)",
    "HP": "Heavily Played (HP)",
    "DMG": "Damaged (DMG)",
}
DIRECTION_WORDS = {"desc": "newest set first", "asc": "oldest set first"}
PRICE_WORDS = {"desc": "highest first", "asc": "lowest first"}
CONDITION_WORDS = {
    "NM": "Near Mint",
    "LP": "Lightly Played",
    "MP": "Moderately Played",
    "HP": "Heavily Played",
    "DMG": "Damaged",
}
PURPOSE = "The cards you own (your Archidekt collection)."
TABLE_COLS = "minmax(0,1fr) 9rem 5.5rem 10.5rem 6.5rem 7.5rem 8.5rem var(--ctl)"
TABLE_MD = "minmax(0,1fr) 8.5rem var(--ctl)"
TABLE_SM = "minmax(0,1fr) var(--ctl)"
ARCHIDEKT_COLLECTION = "https://archidekt.com/collection/v2/"


def _view_attrs(r: dict[str, Any]) -> str:
    """The data attributes ``static/cardview.js`` reads, from a collection row."""
    pt = f"{r.get('power') or ''}/{r.get('toughness') or ''}" if r.get("power") or r.get("toughness") else ""
    faces = [
        {
            "name": f["name"],
            "mana": f["mana_cost"],
            "type": f["type_line"],
            "text": f["text"],
            "pt": f"{f['power']}/{f['toughness']}" if f["power"] or f["toughness"] else "",
            "loyalty": f["loyalty"],
        }
        for f in r.get("faces") or []
    ]
    return (
        f" data-card='{_esc(r['name'])}'"
        + (f" data-img='{_esc(r['image_small'])}'" if r.get("image_small") else "")
        + f" data-set='{_esc((r.get('set') or '').upper())} {_esc(r.get('collector_number'))}'"
        f" data-type='{_esc(r.get('type_line') or '')}' data-mana='{_esc(r.get('mana_cost') or '')}'"
        f" data-finish='{_esc(_finish_label(r.get('finish')))}'"
        + (f" data-text='{_esc(r['oracle_text'])}'" if r.get("oracle_text") else "")
        + (f" data-pt='{_esc(pt)}'" if pt else "")
        + (f" data-loyalty='{_esc(r['loyalty'])}'" if r.get("loyalty") else "")
        + (f" data-faces='{_esc(json.dumps(faces, separators=(',', ':')))}'" if faces else "")
        + (f" data-rarity='{_esc(r['rarity'])}'" if r.get("rarity") else "")
        + (
            f" data-price='{r['price']['unit']:.2f}'"
            if r.get("price") and r["price"].get("currency") == "usd"
            else ""
        )
    )


def money(value: float | None, currency: str = "usd") -> str:
    return "" if value is None else (f"${value:,.2f}" if currency == "usd" else f"€{value:,.2f}")


def price_text(r: dict[str, Any]) -> str:
    """A row's price as the Binder shows it: "$1.28", or for more copies the row's value and the
    unit price ("$2.56 · 2 × $1.28"); "" when Scryfall has no price for its finish."""
    p = r.get("price")
    if not p:
        return ""
    unit = money(p["unit"], p["currency"])
    qty = int(r.get("quantity") or 0)
    return unit if qty <= 1 else f"{money(p['value'], p['currency'])} · {qty} × {unit}"


def price_cell(r: dict[str, Any]) -> str:
    """The list's Price column: the row's value, and under it the copies times the unit price;
    "unknown" when Scryfall has no price for its finish."""
    p = r.get("price")
    if not p:
        return UNPRICED_HTML
    qty = int(r.get("quantity") or 0)
    unit = money(p["unit"], p["currency"])
    if qty <= 1:
        return _esc(unit)
    return f"{_esc(money(p['value'], p['currency']))}<span class='s'>{qty} × {_esc(unit)}</span>"


def where_words(r: dict[str, Any]) -> str:
    """An entry in words: "CMR 472, Foil, Lightly Played, Japanese" (Normal is not said)."""
    parts = [
        _set_line(r),
        _finish_label(r.get("finish") or ""),
        CONDITION_WORDS.get((r.get("condition") or "").upper(), ""),
        _language_name((r.get("lang") or "EN").upper()),
    ]
    return ", ".join(p for p in parts if p)


def added_text(rows: list[dict[str, Any]]) -> str:
    """What an add did, in words: which entry each card went to (0.7.46)."""
    if not rows:
        return ""
    if len(rows) == 1:
        r = rows[0]
        n = int(r.get("added_quantity") or 0)
        if r.get("went_to") == "existing":
            q = int(r.get("quantity") or 0)
            return (
                f"Added {n:,} {'copy' if n == 1 else 'copies'} to the “{r['name']}” entry you already had "
                f"({where_words(r)}): now {q:,} {'copy' if q == 1 else 'copies'}."
            )
        return f"Added “{r['name']}” as a new entry ({where_words(r)})" + (
            f", {n:,} copies." if n > 1 else "."
        )
    old = sum(1 for r in rows if r.get("went_to") == "existing")
    new = len(rows) - old
    said = []
    if old:
        said.append(f"{old} to {'an entry' if old == 1 else 'entries'} you already had")
    if new:
        said.append(f"{new} as {'a new entry' if new == 1 else 'new entries'}")
    return f"Added {len(rows)} cards: " + " and ".join(said) + "."


def _set_line(r: dict[str, Any]) -> str:
    return f"{(r.get('set') or '').upper()} {r.get('collector_number') or ''}".strip()


def _price_value(r: dict[str, Any]) -> str:
    price = r.get("purchase_price")
    if price is None or price == "":
        return ""
    return f"{float(price):.2f}" if isinstance(price, (int, float)) else str(price)


def _row_data(r: dict[str, Any]) -> str:
    """What the Details box and the Remove box read from a row: its finish, condition, language,
    price paid, tags and set line."""
    tags = [str(t) for t in (r.get("tags") or [])[:8]]
    return (
        f" data-fin='{_esc(r.get('finish') or 'nonfoil')}'"
        f" data-cond='{_esc((r.get('condition') or '').upper())}'"
        f" data-lang='{_esc((r.get('lang') or 'EN').upper())}' data-paid='{_esc(_price_value(r))}'"
        f" data-setline='{_esc(_set_line(r))}'"
        + (f" data-setname='{_esc(r['set_name'])}'" if r.get("set_name") else "")
        + (f" data-tags='{_esc(json.dumps(tags, separators=(',', ':')))}'" if tags else "")
    )


def _kebab(name: str, rid: str) -> str:
    """The row's ⋮: static/cardmenu.js opens the shared card menu on it; without scripts it is the
    link to the row's Details box."""
    return (
        f"<a class='kebab' href='/collection?details={rid}' aria-label='Menu for {_esc(name)}' "
        f"aria-haspopup='menu' title='Menu'>{icon('more')}</a>"
    )


def _stepper(r: dict[str, Any], csrf_in: str, rid: str) -> str:
    name = _esc(r["name"])
    return (
        f"<form method='post' action='/collection' class='qty stepper' data-id='{rid}'>{csrf_in}"
        f"<button name='action' value='dec' aria-label='One fewer {name}' title='One fewer'>{icon('minus')}"
        f"</button><output aria-label='Copies'>{int(r['quantity'])}</output>"
        f"<button name='action' value='inc' aria-label='One more {name}' title='One more'>{icon('plus')}"
        "</button></form>"
    )


def _shims(r: dict[str, Any], csrf_in: str) -> str:
    """Hidden pieces the shared card menu (static/menus.js) reaches for: the Remove from collection
    form (the page's script removes at once, with Undo) and the Details… hook (opening it opens the
    Details box)."""
    name = _esc(r["name"])
    return (
        f"<form method='post' action='/collection' class='rm' hidden>{csrf_in}<button name='action' "
        f"value='remove' aria-label='Remove {name} from your binder'>Remove</button></form>"
        "<details class='rowmenu' hidden><summary>Details</summary></details>"
    )


BULK_FORM = "col-bulk"


def _tick(r: dict[str, Any]) -> str:
    """The row's tick box for Select (0.7.46 audit QG-17): the whole 44 px cell is the target; its
    value carries the card's name beside the id, so the write finds the record in one read."""
    name = str(r.get("name") or "")
    return ui.check_cell("sel", f"{r['id']} {name}", f"Select {name}", form=BULK_FORM)


def tag_colors_of(tags: list[dict[str, Any]] | None) -> dict[str, str]:
    """A tag name -> its colour (``#rrggbb``), from the listing's tag list."""
    out: dict[str, str] = {}
    for t in tags or []:
        name, color = str(t.get("name") or ""), str(t.get("color") or "")
        if name and re.fullmatch(r"#[0-9a-fA-F]{6}", color):
            out[name] = color.lower()
    return out


def _tag_color(name: str, colors: dict[str, str] | None) -> str:
    for k, v in (colors or {}).items():
        if collection_tags.same_name(k, name):
            return v
    return ""


def tag_chip(name: str, colors: dict[str, str] | None) -> str:
    """One tag as a chip: a colour dot (the tag's colour on Archidekt, in the ``--tagc`` custom
    property; grey when it has none) beside the name in the page's own text colour, so the chip
    reads in both themes whatever the colour."""
    color = _tag_color(name, colors)
    style = f" style='--tagc:{color}'" if color else ""
    return (
        f"<span class='pill tag'{style} title='{_esc(name)}'><i class='sw' aria-hidden='true'></i>"
        f"<span class='t'>{_esc(name)}</span></span>"
    )


def tag_chips(names: list[str], colors: dict[str, str] | None) -> str:
    """A row's tags as chips (``.tagrow``), at most TAG_CHIPS of them and "+N" for the rest."""
    shown = [str(t) for t in names[:TAG_CHIPS]]
    more = len(names) - len(shown)
    out = "".join(tag_chip(t, colors) for t in shown)
    if more > 0:
        rest = _esc(", ".join(str(t) for t in names[TAG_CHIPS:]))
        out += f"<span class='pill tag more' title='{rest}'>+{more}</span>"
    return f"<span class='tagrow'>{out}</span>" if out else ""


TAG_CHIPS = 4  # chips a tile or row shows before "+N"


def _added_mark(r: dict[str, Any]) -> str:
    """A held Binder tile's mark on a card added through the gateway since it was read
    (held_view); the list puts it in the name cell (row_html)."""
    return " · Added since" if r.get("held_added") else ""


def row_html(r: dict[str, Any], csrf: str, *, view: str, tag_colors: dict[str, str] | None = None) -> str:
    """One entry: card search's tile with the stepper and ⋮ (grid), or one table row (list).
    ``tag_colors``: the member's tag colours by name (tag_colors_of), for the row's tag chips."""
    img = r.get("image_small") or ""
    rid = _esc(r["id"])
    name = _esc(r["name"])
    attrs = _view_attrs(r) + _row_data(r)
    finish = _finish_label(r["finish"])
    cond = (r.get("condition") or "").upper()
    lang = (r.get("lang") or "EN").upper()
    # the card's name goes with its id: the record is found by Archidekt's name filter (_record)
    csrf_in = (
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'><input type='hidden' name='id' value='{rid}'>"
        f"<input type='hidden' name='name' value='{name}'>"
    )
    stepper = _stepper(r, csrf_in, rid)
    shims = _shims(r, csrf_in)
    set_line = _esc(_set_line(r))
    if view == "grid":
        badges = (f"<span class='fin'>{finish}</span>" if finish else "") + (
            f"<span class='cond' title='{_esc(CONDITION_LABELS.get(cond, cond))}'>{_esc(cond)}</span>"
            if cond
            else ""
        )
        body = (
            f"<img src='{_esc(img)}' alt='' loading='lazy' decoding='async'>"
            if img
            else f"<span class='ph'>{name}</span>"
        )
        sub = set_line + (f" · {_esc(_language_name(lang))}" if lang != "EN" else "") + _added_mark(r)
        price = price_text(r)
        chips = tag_chips([str(t) for t in (r.get("tags") or [])], tag_colors)
        return (
            f"<li class='cc c col' data-name='{_esc(r['name'].lower())}' data-id='{rid}'{attrs}>"
            f"<button type='button' class='pic thumbbtn' aria-label='Show {name}' title='Show the card'>"
            f"{body}{badges}</button>"
            f"<div class='meta'><span class='nm'>{_tick(r)}<span class='name' title='{name}'>{name}</span>"
            f"{_kebab(r['name'], rid)}</span><span class='ty'>{sub}</span>"
            + f"<span class='pr'>{_esc(price) if price else UNPRICED_HTML}</span>"
            + chips
            + f"</div><div class='act'>{stepper}</div>{shims}</li>"
        )
    fin_word = finish or "Normal"
    cond_word = _esc(CONDITION_LABELS.get(cond, cond or "Not set"))
    lang_word = _esc(_language_name(lang))
    set_name = _esc(r.get("set_name") or "")
    thumb = (
        f"<img class='thumb' src='{_esc(img)}' alt='' loading='lazy'>"
        if img
        else "<span class='thumb'></span>"
    )
    # under 900 px the Price column is hidden: the price goes on the card's own lines; a price
    # nobody knows says so there too, as the column does (audit N7, N12)
    price = _esc(price_text(r)) or UNPRICED_HTML
    sub_md = " · ".join(x for x in (set_line, set_name, fin_word, cond_word, lang_word) if x)
    sub_sm = " · ".join(x for x in (set_line, fin_word, _esc(cond or "Not set"), lang_word) if x)
    sub_md += f"<span class='pr lgh'>{price}</span>"
    sub_sm += f" · <span class='pr'>{price}</span>"
    return (
        f"<li class='tr row' role='row' data-name='{_esc(r['name'].lower())}' data-id='{rid}'{attrs}>"
        f"<span class='cardc' role='cell'>{_tick(r)}<button type='button' class='thumbbtn' "
        f"aria-label='Show {name}' "
        f"title='Show the card'>{thumb}</button><span class='tx'><span class='name' title='{name}'>{name}"
        "</span>"
        # the held Binder's mark, in the name cell's muted small line: shown at every width, where
        # the lines under it (.sub, .smline, .lg) each show at some widths only
        + ("<span class='s'>Added since</span>" if r.get("held_added") else "")
        + (f"<span class='s lg'>{_esc(r['type_line'])}</span>" if r.get("type_line") else "")
        + f"<span class='sub'>{sub_md}</span><span class='s smline'>{sub_sm}</span>"
        + tag_chips([str(t) for t in (r.get("tags") or [])], tag_colors)
        + "</span></span>"
        f"<span class='md-hide setc' role='cell'>{set_line}"
        + (f"<span class='s'>{set_name}</span>" if set_name else "")
        + f"</span><span class='md-hide' role='cell'>{fin_word}</span>"
        f"<span class='md-hide' role='cell'>{cond_word}</span>"
        f"<span class='md-hide' role='cell'>{lang_word}</span>"
        f"<span class='md-hide prc' role='cell'>{price_cell(r)}</span>"
        f"<span class='qc' role='cell'>{stepper}</span><span class='act' role='cell'>{_kebab(r['name'], rid)}"
        f"</span>{shims}</li>"
    )


def table_head_html() -> str:
    cells = (
        ("Card", ""),
        ("Set", "md-hide"),
        ("Finish", "md-hide"),
        ("Condition", "md-hide"),
        ("Language", "md-hide"),
        ("Price", "md-hide prc"),
        ("Copies", "qc"),
        ("", "act"),
    )
    spans = "".join(
        f"<span role='columnheader'{' class=' + chr(39) + c + chr(39) if c else ''}>{t}</span>"
        for t, c in cells
    )
    return f"<li class='tr th' role='row'>{spans}</li>"


def _tag_colors_attr(tag_colors: dict[str, str] | None) -> str:
    """The tag colours on the list, for the page's script (it redraws a row's chips after a save)."""
    return f" data-tag-colors='{_esc(json.dumps(tag_colors, separators=(',', ':')))}'" if tag_colors else ""


def list_html(
    rows: list[dict[str, Any]], csrf: str, *, view: str, tag_colors: dict[str, str] | None = None
) -> str:
    items = "".join(row_html(r, csrf, view=view, tag_colors=tag_colors) for r in rows)
    colors = _tag_colors_attr(tag_colors)
    if view == "list":
        style = _esc(f"--cols:{TABLE_COLS};--cols-md:{TABLE_MD};--cols-sm:{TABLE_SM}")
        return (
            f"<ul class='ui-table colllist' id='cards' role='table' aria-label='Your binder' "
            f"style='{style}'{colors}>{table_head_html()}{items}</ul>"
        )
    return f"<ul class='cardgrid collgrid' id='cards'{colors}>{items}</ul>"


def _opts(values: Iterable[str], current: str, labels: dict[str, str] | None = None) -> str:
    return "".join(
        f"<option value='{_esc(v)}'{' selected' if v == current else ''}>"
        f"{_esc((labels or {}).get(v, v))}</option>"
        for v in values
    )


def _lang_opts(current: str) -> str:
    langs = LANGUAGES if not current or current in LANGUAGES else (current, *LANGUAGES)
    return _opts(langs, current or "EN", {k: _language_name(k) for k in langs})


def tag_tick(name: str, colors: dict[str, str] | None, *, field: str, checked: bool, form: str = "") -> str:
    """One binder tag to tick, drawn as a chip: the box, the tag's colour dot and its name."""
    color = _tag_color(name, colors)
    style = f" style='--tagc:{color}'" if color else ""
    f = f" form='{_esc(form)}'" if form else ""
    value = "1" if field.startswith("tag:") else _esc(name)
    return (
        f"<label class='tagck'{style}><input type='checkbox' name='{_esc(field)}' value='{value}' "
        f"data-tag='{_esc(name)}'{f}{' checked' if checked else ''}><i class='sw' aria-hidden='true'></i>"
        f"<span class='t'>{_esc(name)}</span></label>"
    )


def _tag_boxes(all_tags: list[str], on: list[str], colors: dict[str, str] | None = None) -> str:
    """The Details box's Tags: one chip to tick per binder tag the member has (``tag:NAME`` fields,
    so the form works without scripts), each with its colour, in a box that scrolls when the
    member has many, and the link to make or change them."""
    if not all_tags:
        return (
            "<div class='field tags'><span>Tags</span><span class='muted small'>No binder tags yet. "
            "<a href='/lists/btags'>Make one</a></span></div>"
        )
    ticks = "".join(
        tag_tick(t, colors, field=f"tag:{t}", checked=any(collection_tags.same_name(t, x) for x in on))
        for t in all_tags
    )
    return (
        "<fieldset class='field tags'><legend>Tags <a class='small' href='/lists/btags'>Manage tags</a>"
        "</legend><input type='hidden' name='tags_shown' value='1'>"
        f"<span class='tagline ticks'>{ticks}</span></fieldset>"
    )


def details_box_html(
    r: dict[str, Any] | None,
    csrf: str,
    *,
    open: bool = False,
    all_tags: list[str] | None = None,
    tag_colors: dict[str, str] | None = None,
) -> str:
    """The Details box: finish, condition, language, price paid and tags, saved to Archidekt.
    ``all_tags``: the member's binder tags, each a box to tick (None: the row's tags are only
    shown). With ``r`` it is drawn for that row (the old ``?details=ID`` address renders it open);
    without, it is the template the page's script fills from a row."""
    r = r or {}
    rid = _esc(r.get("id", ""))
    finish = r.get("finish") or "nonfoil"
    cond = (r.get("condition") or "").upper()
    lang = (r.get("lang") or "EN").upper()
    tags = [str(t) for t in (r.get("tags") or [])[:8]]
    tagline = "".join(tag_chip(t, tag_colors) for t in tags)
    sub = " · ".join(x for x in (_esc(_set_line(r)), _esc(r.get("set_name") or "")) if x)
    body = (
        f"<form method='post' action='/collection' id='col-detailsform' class='detailsform' data-id='{rid}'>"
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'><input type='hidden' name='id' value='{rid}'>"
        f"<input type='hidden' name='name' value='{_esc(r.get('name', ''))}'>"
        f"<p class='muted small dsub'>{sub}</p><div class='fl'>"
        f"<label class='field'><span>Finish</span><select name='finish'>{_opts(FINISHES, finish, MODIFIERS)}"
        "</select></label>"
        f"<label class='field'><span>Condition</span><select name='condition'>"
        f"{_opts(CONDITIONS, cond, CONDITION_LABELS)}</select></label>"
        f"<label class='field'><span>Language</span>"
        f"<select name='language'>{_lang_opts(lang)}</select></label>"
        f"<label class='field'><span>Price paid</span><input type='number' name='purchase_price' min='0' "
        f"max='{int(MAX_PRICE)}' step='0.01' inputmode='decimal' value='{_esc(_price_value(r))}' "
        "placeholder='None'></label>"
        + (
            _tag_boxes(all_tags, [str(t) for t in (r.get("tags") or [])], tag_colors)
            if all_tags is not None
            else f"<div class='field tags'{'' if tags else ' hidden'}><span>Tags</span>"
            f"<span class='tagline'>{tagline}</span></div>"
        )
        + "</div></form>"
    )
    title = f"Details: {r['name']}" if r.get("name") else "Details"
    return ui.box(
        "col-details",
        title,
        body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(
                "Remove from binder",
                kind="danger",
                form="col-detailsform",
                name="action",
                value="remove",
                icon_name="trash",
            ),
            ui.box_button(
                "Save",
                kind="primary",
                form="col-detailsform",
                name="action",
                value="details",
                icon_name="check",
            ),
        ],
        open=open,
        close_href="/collection",
    )


def add_box_html(
    csrf: str, *, tab: str = "add", open: bool = False, defaults: dict[str, Any] | None = None
) -> str:
    """The Add cards box: two tabs, Add a card (name, copies, finish, condition, language, and
    "Use these next time": the member's remembered choices, prefs.BINDER_ADD) and Import a list
    (a file or pasted text). Its forms post to the page as before."""
    imp = tab == "import"
    d = defaults or {}
    finish = d.get("finish") if d.get("finish") in FINISHES else "nonfoil"
    cond = d.get("condition") if d.get("condition") in CONDITIONS[1:] else "NM"
    lang = d.get("language") if d.get("language") in LANGUAGES else "EN"
    tabs = (
        "<div class='hubseg segc coltabs' role='tablist' aria-label='Add cards'>"
        "<button type='button' role='tab' id='col-tab-add' data-coltab='add' "
        f"aria-selected='{str(not imp).lower()}' "
        f"aria-controls='col-addform'>{icon('plus')}Add a card</button>"
        "<button type='button' role='tab' id='col-tab-import' data-coltab='import' "
        f"aria-selected='{str(imp).lower()}' "
        f"aria-controls='col-importform'>{icon('list')}Import a list</button></div>"
    )
    add_form = (
        f"<form method='post' action='/collection' id='col-addform' class='addcard' role='tabpanel' "
        f"aria-labelledby='col-tab-add' data-pane='add'{' hidden' if imp else ''}>"
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        "<input type='hidden' name='action' value='add'>"
        "<div class='fl'><label class='field span'><span>Name</span>"
        "<input id='addname' type='text' name='name' data-suggest='cards' "
        "placeholder='Name' autocomplete='off' required aria-describedby='addname-help'></label>"
        "<p class='muted small' id='addname-help'>“Name (SET) 123” adds one printing.</p>"
        "<label class='field'><span>Copies</span><input id='addqty' type='number' name='quantity' value='1' "
        f"min='1' max='{MAX_QUANTITY}'></label>"
        f"<label class='field'><span>Finish</span><select id='addfinish' name='finish'>"
        f"{_opts(FINISHES, finish, MODIFIERS)}</select></label>"
        f"<label class='field'><span>Condition</span><select id='addcond' name='condition'>"
        f"{_opts(CONDITIONS[1:], cond, CONDITION_LABELS)}</select></label>"
        f"<label class='field'><span>Language</span><select id='addlang' name='language'>"
        f"{_lang_opts(lang)}</select></label>"
        "<label class='check span'><input type='checkbox' name='remember' value='1' checked>"
        "<span>Use this finish, condition and language next time</span></label></div>"
        "<p class='muted small'>Copies join an entry you already have only when its printing, finish, "
        "condition and language all match; anything else becomes a new entry.</p>"
        "<p class='muted small'>Scanning a pile is quicker: open "
        + ui.cap_link("Scan", "/scan", icon_name="camera")
        + ", then choose "
        "<strong>Save to binder</strong>. Everything you add is added to your binder.</p>"
        "</form>"
    )
    import_form = (
        f"<form method='post' action='/collection' id='col-importform' class='importform' role='tabpanel' "
        f"aria-labelledby='col-tab-import' data-pane='import'{'' if imp else ' hidden'} "
        "data-draft='binder-import'>"
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        "<input type='hidden' name='action' value='import'>"
        "<p class='muted small'>A collection CSV or a card list, one card per line, "
        f"up to {MAX_IMPORT_ROWS:,} rows "
        "at a time. Copies join an entry you already have only when its printing, finish, condition and "
        "language all match.</p>"
        "<div class='field filepick'><span class='lbl'>From a file</span><label class='filebtn'>"
        "<input id='importfile' type='file' accept='.csv,.txt,text/csv,text/plain' data-fill='importtext'>"
        "<span class='btn'>Choose a file</span></label>"
        "<span class='fname' aria-live='polite'>No file chosen</span>"
        "</div><textarea id='importtext' name='text' rows='6' placeholder='Quantity,Name,Finish,Edition Code,"
        "Collector Number&#10;2,Card name,Normal,SET,123'></textarea></form>"
    )
    return ui.box(
        "col-add",
        "Add cards",
        tabs + add_form + import_form,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(
                "Add",
                kind="primary",
                form="col-addform",
                icon_name="plus",
                attrs="data-pane-btn='add'" + (" hidden" if imp else ""),
            ),
            ui.box_button(
                "Import",
                kind="primary",
                form="col-importform",
                icon_name="plus",
                attrs="data-pane-btn='import'" + ("" if imp else " hidden"),
            ),
        ],
        open=open,
        close_href="/collection",
    )


def _archidekt_link(arch_user: str) -> str:
    if not arch_user:
        return ""
    return (
        f"<a class='btn btn-plain ext' href='{ARCHIDEKT_COLLECTION}{_esc(arch_user)}' target='_blank' "
        f"rel='noreferrer noopener'>{icon('external')}Open on Archidekt</a>"
    )


def _added_within(row: dict[str, Any], since: float) -> bool:
    raw = str(row.get("added_at") or "")
    if not raw:
        return False
    try:
        import datetime as dt

        when = dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if when.tzinfo is None:
            when = when.replace(tzinfo=dt.UTC)
        return when.timestamp() >= since
    except ValueError:
        return False


def added_this_week(rows: list[dict[str, Any]], total: int, since: float, *, large: bool) -> int | str:
    """Added this week as the Binder knows it (0.7.46 audit C5), live and held alike: ``rows``
    newest first (Archidekt's default order, as the first page comes; a held copy puts the cards
    added since first), ``total`` the binder's entries. A binder of more than one page is counted
    from its newest PAGE_SIZE entries only: when the oldest of those is from before ``since``,
    every older entry is too, so the count is whole; when it isn't, how many more were added this
    week isn't known and the count says so ("100+"), never a number the gateway doesn't have. A
    held copy whose read entries were removed since knows fewer than PAGE_SIZE of the newest, and
    says only those ("99+")."""
    if not large and total <= PAGE_SIZE:
        return sum(1 for r in rows if _added_within(r, since))
    newest = rows[:PAGE_SIZE]
    week = sum(1 for r in newest if _added_within(r, since))
    if total <= len(newest) or (newest and not _added_within(newest[-1], since)):
        return week
    return f"{week:,}+"


def _day_words(day: str | None, now: float) -> str:
    """A price day as the Binder says it: "today", "yesterday" or "Oct 12"."""
    if not day:
        return ""
    import datetime as dt

    today = dt.datetime.fromtimestamp(now, dt.UTC).date()
    try:
        d = dt.date.fromisoformat(day)
    except ValueError:
        return day
    if d == today:
        return "today"
    if d == today - dt.timedelta(days=1):
        return "yesterday"
    return d.strftime("%b %d").replace(" 0", " ") + ("" if d.year == today.year else f", {d.year}")


def value_widget(
    value: dict[str, Any] | None, *, reading: bool, now: float, csrf: str = "", failed: bool = False
) -> str:
    """The Binder's worth: today's dollars over every entry Scryfall prices in its finish
    (binder_prices.BinderStore.value), with the copies, the price day and the entries with no
    price; before the first whole read, what is happening instead, and "Work it out now" (one
    whole read of the Binder, as the member, then its prices)."""
    if value is None:
        if reading:
            # a progress line the page follows by itself (static/collection.js polls
            # /collection/api/value and swaps in the finished widget); Refresh is for no script
            return ui.widget(
                "Value",
                "stats",
                "…",
                detail_html=(
                    "<span class='value-reading' data-value-reading role='status'>"
                    "<span class='ui-spin' aria-hidden='true'></span>Reading your whole binder to work "
                    "out its value. This updates by itself.</span>"
                ),
                actions=[ui.button_link("Refresh", "/collection", icon_name="refresh")],
            )
        now_btn = (
            "<form method='post' action='/collection' class='inline'>"
            f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
            "<input type='hidden' name='action' value='value'>"
            f"<button type='submit' class='btn btn-plain'>{icon('refresh')}Work it out now</button></form>"
        )
        return ui.widget(
            "Value",
            "stats",
            "",
            big_html=UNPRICED_HTML,
            detail=(
                "Archidekt could not be asked for your whole binder just now. Try again later; the "
                "daily price check tries again too."
                if failed
                else "Worked out from your whole binder with the daily price check."
            ),
            actions=[now_btn] if csrf else [],
        )
    # no entry priced: no value to show (as Scan's list value), never "$0.00"
    priced = int(value.get("entries") or 0) > int(value.get("unpriced") or 0)
    # only dollar prices count (a euro price is not converted), and "$" alone could be any dollar
    usd = ", in US dollars" if priced else ""
    parts = []  # the copies are under Entries
    if value.get("day"):
        when = _day_words(value["day"], now)
        parts.append(("Today's prices" if when == "today" else f"Prices of {when}") + usd)
    elif priced:
        parts.append("In US dollars")
    n = int(value.get("unpriced") or 0)
    if n:
        parts.append(f"{n:,} {'entry has' if n == 1 else 'entries have'} no price")
    if not value.get("complete"):
        parts.append(f"your first {value['entries']:,} entries")
    # read whole but no price came yet (Scryfall was not answering, or none of the printings is
    # priced): say so
    big = (money(value["usd"]) or "$0.00") if priced else ""
    if n and not priced and not value.get("day"):
        parts.append("Prices come with the daily price check.")
    return ui.widget(
        "Value", "stats", big, big_html="" if priced else UNPRICED_HTML, detail_html=ui.facts(parts)
    )


def widgets_html(
    whole: dict[str, Any],
    arch_user: str,
    *,
    now: float | None = None,
    value: dict[str, Any] | None = None,
    reading: bool = False,
    priced: bool = True,
    csrf: str = "",
    failed: bool = False,
) -> str:
    """The widgets, from what Archidekt already sends with the first page: Entries (with the copies
    when known: a collection of 100 entries or fewer, or the Binder as last read whole), Value (with
    prices on; ``value`` from binder_prices), Added this week (from the newest 100 entries, "100+"
    when all of those are this week's: added_this_week) and
    Foils (counted only when the whole collection fits one page of 100)."""
    now = time.time() if now is None else now
    rows = whole["rows"]
    total = int(whole["count"])
    large = int(whole.get("total_pages") or 1) > 1 or bool(whole.get("has_next"))
    open_link = [_archidekt_link(arch_user)] if arch_user else []
    since = now - 7 * 86400
    # from the newest PAGE_SIZE entries past one page, live and held alike (added_this_week)
    windowed = large or total > PAGE_SIZE
    week = added_this_week(rows, total, since, large=large)
    import datetime as dt

    since_day = dt.datetime.fromtimestamp(since, dt.UTC).strftime("%b %d").replace(" 0", " ")
    copies = None if large else sum(int(r.get("quantity") or 0) for r in rows)
    if copies is None and value is not None and value.get("complete"):
        copies = int(value["copies"])
    items = [
        ui.widget(
            "Entries",
            "collection",
            total,
            detail=f"{copies:,} {'copy' if copies == 1 else 'copies'}" if copies is not None else "",
            actions=open_link,
        )
    ]
    if priced:
        items.append(value_widget(value, reading=reading, now=now, csrf=csrf, failed=failed))
    items.append(
        ui.widget(
            "Added this week",
            "clock",
            week,
            detail=f"Counted from your newest {PAGE_SIZE:,} entries" if windowed else f"Since {since_day}",
            actions=[
                ui.button_link("Scan cards", "/scan", icon_name="camera"),
                ui.box_opener(
                    "Import a list", "/collection?add=import", "col-add", icon_name="list", cls="imp"
                ),
            ],
            wide_sm=large,
        )
    )
    if not large:
        foils = sum(1 for r in rows if (r.get("finish") or "nonfoil") != "nonfoil")
        items.append(ui.widget("Foils", "sun", foils, detail="Foil or etched entries"))
    line = f"{total:,} {'entry' if total == 1 else 'entries'} · {week} added this week"
    return ui.widgets(items, line=line)


def bulk_html(csrf: str, back: str, tags: list[str], tag_colors: dict[str, str] | None = None) -> str:
    """Select's bar (0.7.46 audit QG-17): hidden until Select opens the select mode (nothing
    ticked; re-audit N03); the rows' tick boxes and the boxes' fields belong to one hidden form, so
    a press sends the ticked rows with the box's choice. Select all ticks every row on screen;
    Condition…, Finish… and Tags… open a box; Remove goes at once (the notice offers Undo);
    Cancel clears the ticks and closes the mode."""
    csrf_in = (
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        f"<input type='hidden' name='next' value='{_esc(back)}'>"
    )
    form = (
        f"<form method='post' action='/collection' id='{BULK_FORM}' class='col-bulkform'>{csrf_in}"
        "<input type='hidden' name='action' value='bulk'></form>"
    )
    # one slim row (final audit F01): × Cancel, the count, Select all, ⋯ (Condition…, Finish…,
    # Tags…), Remove
    actions = ["<button type='button' class='btn col-all'>Select all</button>"]
    more = [
        ui.menu_opener("Condition…", "/collection", "col-bulk-cond", icon_name="check"),
        ui.menu_opener("Finish…", "/collection", "col-bulk-finish", icon_name="sun"),
        ui.menu_opener("Tags…", "/collection", "col-bulk-tags", icon_name="tag"),
    ]
    end = [
        f"<button type='submit' class='btn btn-outline-danger' form='{BULK_FORM}' name='do' value='remove'"
        f" aria-label='Remove' title='Remove'>{icon('trash')}<span class='lbl'>Remove</span></button>"
    ]
    bar = ui.selection_bar(
        "0 selected", actions, label="Selected cards", hidden=True, cancel_cls="col-clear", more=more, end=end
    )
    cond = ui.box(
        "col-bulk-cond",
        "Set the condition",
        "<p><span data-bulk-count>The selected cards</span> get this condition on Archidekt.</p>"
        f"<label class='field'><span>Condition</span><select name='condition' form='{BULK_FORM}'>"
        f"{_opts(CONDITIONS, 'NM', CONDITION_LABELS)}</select></label>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Set condition", kind="primary", form=BULK_FORM, name="do", value="condition"),
        ],
        label="Set the condition",
    )
    fin = ui.box(
        "col-bulk-finish",
        "Set the finish",
        "<p><span data-bulk-count>The selected cards</span> get this finish on Archidekt. A printing with "
        "no such finish is refused and says so.</p>"
        f"<label class='field'><span>Finish</span><select name='finish' form='{BULK_FORM}'>"
        f"{_opts(FINISHES, 'nonfoil', MODIFIERS)}</select></label>",
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button("Set finish", kind="primary", form=BULK_FORM, name="do", value="finish"),
        ],
        label="Set the finish",
    )
    if tags:
        ticks = "".join(tag_tick(t, tag_colors, field="tags", checked=False, form=BULK_FORM) for t in tags)
        tag_body = (
            "<p><span data-bulk-count>The selected cards</span> get these tags, and no others. "
            "Tick none to clear their tags.</p>"
            f"<div class='tagline ticks'>{ticks}</div>"
        )
    else:
        tag_body = (
            "<p>You have no binder tags yet. Make one under Binder › Lists › Binder tags, then tag "
            "cards here.</p>"
        )
    tag_box = ui.box(
        "col-bulk-tags",
        "Set the tags",
        tag_body,
        actions=[
            ui.box_button("Cancel", cancel=True),
            ui.box_button(
                "Set tags", kind="primary", form=BULK_FORM, name="do", value="tags", disabled=not tags
            ),
        ],
        label="Set the tags",
    )
    return form + bar + cond + fin + tag_box


def restore_box_html(snap_id: str, csrf: str, *, now: int, copies: int) -> str:
    """The question before a copy of the Binder is put back onto a Binder that is not empty
    (``/collection?restore=ID``, where /collection/restore sends the member): what the restore adds
    and what it never does, the one orange button and the way out. A box the Binder page holds
    open, as every confirm box is; without scripts it is a panel at the top of the page."""
    entries = f"{now:,} {'entry' if now == 1 else 'entries'}"
    cards = f"{copies:,} {'card' if copies == 1 else 'cards'}"
    words = (
        f"<p>It holds {entries} now. Restoring adds the {cards} of this copy to them: a card already "
        "there gets more copies, nothing is replaced or removed.</p>"
        if now
        else f"<p>Restoring puts the {cards} of this copy back into your empty binder.</p>"
    )
    body = (
        words + "<p class='small muted'>The restore runs here on the Binder page, where you can follow it; "
        "each card goes back with its finish, condition, language, tags and price paid.</p>"
        f"<form method='post' action='/collection/restore' id='col-restoref'>"
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        f"<input type='hidden' name='snapshot_id' value='{_esc(snap_id)}'>"
        "<input type='hidden' name='confirm' value='1'></form>"
    )
    return ui.box(
        "col-restore",
        "Your binder is not empty" if now else "Put this copy back?",
        body,
        actions=[
            "<a class='btn btn-plain' href='/history?type=snapshots'>Keep my binder as it is</a>",
            ui.box_button(
                "Add them to my binder" if now else "Put them back",
                kind="primary",
                form="col-restoref",
                icon_name="plus",
            ),
        ],
        open=True,
        close_href="/collection",
    )


def panel_html(
    *,
    q: str,
    sort: str,
    direction: str,
    view: str,
    count: int,
    priced: bool = True,
    tags: list[str] | None = None,
    tag: str = "",
) -> str:
    """The shared search panel: the name box with the Filters button, the filter groups (the Tag
    drop-down, ``tags``: the member's binder tags, ``tag`` chosen), then the results bar (count,
    Sort with a direction for Set release and Price, Grid or List, the card size, Clear filters).
    On a phone the groups, the card size and Clear filters wait in the Filters sheet (as My decks
    and card search), so the bar is two rows and the first card is on the first screen (final
    audit N11). ``priced``: Price is offered (prices on)."""
    sorts = {k: v for k, v in SORTS.items() if priced or k != "price"}
    sort_opts = "".join(
        f"<option value='{k}'{' selected' if k == sort else ''}>{v}</option>" for k, v in sorts.items()
    )
    dir_html = ""
    if sort in ("edition", "price"):
        flip = "asc" if direction == "desc" else "desc"
        words = (PRICE_WORDS if sort == "price" else DIRECTION_WORDS)[direction]
        dir_html = (
            f"<input type='hidden' name='dir' value='{direction}'>"
            f"<button type='submit' class='icob dirbtn' name='dir' value='{flip}' "
            f"aria-label='Direction: {words}' title='Direction: {words}'>{icon('sort')}</button>"
        )
    combined = (
        ("added", "Recently added", sort == "added"),
        ("edition:desc", "Set release, newest first", sort == "edition" and direction == "desc"),
        ("edition:asc", "Set release, oldest first", sort == "edition" and direction == "asc"),
        ("price:desc", "Price, highest first", sort == "price" and direction == "desc"),
        ("price:asc", "Price, lowest first", sort == "price" and direction == "asc"),
    )
    sortc = "".join(
        f"<option value='{v}'{' selected' if on else ''}>{t}</option>"
        for v, t, on in combined
        if priced or not v.startswith("price")
    )
    sort_html = (
        "<span class='g sort'><label class='lbl' for='col-sort'>Sort by</label><span class='sortw'>"
        f"<select class='sortf' id='col-sort' name='sort'>{sort_opts}</select></span>{dir_html}"
        f"<span class='sortcw'><select class='sortc' aria-label='Sort by'>{sortc}</select></span></span>"
    )
    groups = []
    if tags:
        chosen = next((t for t in tags if collection_tags.same_name(t, tag)), "")
        groups.append(
            sp.select_group(
                label="Tag",
                name="tag",
                options=[("", "All cards"), *((t, t) for t in tags)],
                value=chosen,
                input_id="col-tag",
                cls="tagg",
            )
        )
    # The phone sheet is titled by what it holds: "Filters" with filter groups, "View" when
    # only the card-size choice is in it (0.7.46 audit).
    sheet_word = "Filters" if groups else "View"
    sheet = sp.filters_sheet(
        sp.filter_grid(groups) if groups else "",
        title=sheet_word,
        show_html="<button type='submit' class='btn-primary sp-show'>Show cards</button>",
        clear_html="<a class='btn sp-clear' href='/collection'>Clear filters</a>",
    )
    count_text = f"{count:,} {'entry' if count == 1 else 'entries'}"
    # Select (the page's script shows it): opens the select mode, nothing ticked (re-audit N03)
    select = (
        "<button type='button' class='btn col-select' aria-pressed='false' hidden>"
        f"{icon('check')}Select</button>"
    )
    bar = sp.results_bar(
        count_html=sp.count_html(count_text, count_id="col-count") + select,
        sort_html=sort_html,
        view_html=sp.view_group(view),
        # sp-clear: the panel's script hides it until a filter is set (polish C4)
        clear_html="<a class='btn sp-clear clr' href='/collection'>Clear filters</a>",
    )
    return (
        "<section class='panel spanel'>"
        "<form id='listform' method='get' action='/collection' autocomplete='off'>"
        + sp.name_row(
            label="Filter by name",
            name="q",
            value=q,
            placeholder="Name",
            input_id="col-q",
            search_label="Filter",
            filters_word=sheet_word,
        )
        + sheet
        + bar
        + "<noscript><button type='submit' class='btn apply'>Apply</button></noscript></form></section>"
    )


COLLECTION_CSS = """
.value-reading .ui-spin{margin-right:.35em}
.collection .ui-head .acts{margin-left:auto;display:inline-flex;flex-wrap:wrap;gap:.5rem;align-items:center}
.collection .ui-head .acts .btn{margin:0}
/* the head's More menu: Export CSV, then Clear binder… in red; its summary is a 44 px button */
.collection .ui-head .acts details.colmore{margin:0}
.collection .ui-head .acts details.colmore > summary.btn{margin:0;display:inline-flex;align-items:center;
  gap:.35rem}
.collection .ui-head .acts details.colmore .menu{min-width:min(12rem,calc(100vw - 16px))}
.collection .ui-head .acts details.colmore .menu a,.collection .ui-head .acts details.colmore .menu button{
  min-height:44px}
.collection .ui-head .acts details.colmore form.clearf{margin:0}
.collection .ui-head .acts details.colmore form.clearf button{width:100%}
.mtgbox .body fieldset.tags{border:0;padding:0;margin:0;grid-column:1/-1;min-width:0}
.collection .ui-head.phead{align-items:center}
/* on a phone Add cards takes the row and More ends it (one row above the list, as the other
   tabs: final audit N11, F10) */
@media (max-width:599.98px){ .collection .ui-head .acts{margin-left:0;width:100%;flex-wrap:nowrap}
  .collection .ui-head .acts .btn{flex:1 1 auto;justify-content:center;min-width:0}
  .collection .ui-head .acts details.colmore{flex:0 0 auto;order:2}
  .collection .ui-head .acts details.colmore > summary.btn{width:auto;box-sizing:border-box} }
/* a tag as a chip: the tag's colour as a dot (--tagc) beside its name in the page's own text colour,
   so it reads in both themes whatever colour the member picked */
.pill.tag{display:inline-flex;align-items:center;gap:.3rem;padding:.1rem .5rem;
  border-radius:999px;border-color:var(--border-soft);background:var(--surface-2);color:var(--text);
  font-weight:600;font-size:.8rem;line-height:1.5;max-width:100%;min-width:0}
/* one line: a long name ends in an ellipsis (the whole name is the hover text and the Details
   box's), never three lines inside a pill (polish B6) */
.pill.tag > .t{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.pill.tag .sw,.tagck .sw{display:inline-block;width:.65rem;height:.65rem;border-radius:50%;flex:none;
  background:var(--tagc,var(--text-muted));box-shadow:inset 0 0 0 1px rgba(0,0,0,.2)}
.pill.tag.more{padding:.1rem .5rem}
.tagrow{display:flex;flex-wrap:wrap;gap:.25rem;margin-top:.3rem;min-width:0}
ul.collgrid .meta .tagrow{margin-top:.2rem}
ul.colllist .cardc .tagrow{margin-top:.25rem}
/* the Details box's tags and the Tags… box: chips to tick (44 px), in a box that scrolls when the
   member has many */
.mtgbox .body .tagline.ticks{display:flex;flex-wrap:wrap;gap:.4rem;max-height:13rem;overflow:auto;
  padding:.4rem;overscroll-behavior:contain;border:1px solid var(--border-soft);border-radius:var(--radius)}
/* (label.tagck: deck.css makes a box field's labels block; the chip is a flex row) */
.mtgbox .body label.tagck,.mtgbox .body .field label.tagck{display:inline-flex;align-items:center;gap:.4rem;
  min-height:44px;margin:0;padding:0 .8rem 0 .5rem;border:1px solid var(--control-border);
  border-radius:999px;background:var(--surface);cursor:pointer;max-width:100%;box-sizing:border-box;
  font-weight:600}
.mtgbox .body label.tagck:hover{background:var(--surface-2)}
.mtgbox .body label.tagck:has(input:checked){background:var(--orange-tint);border-color:var(--orange)}
.mtgbox .body label.tagck:has(input:focus-visible){outline:2px solid var(--focus);outline-offset:2px}
.mtgbox .body label.tagck input,.mtgbox .body .fl .tags label.tagck input{width:20px;height:20px;
  min-height:0;margin:0;flex:none;accent-color:var(--orange-ctl)}
.mtgbox .body label.tagck .sw{width:.75rem;height:.75rem}
.mtgbox .body label.tagck .t{min-width:0;overflow-wrap:anywhere}
/* notices are strips at the top of the page with a × to close. The frame beside the words (the
   strip's side padding, the × and the gap after it) is sized in px, as the × is, so large text
   widens the words' column and not the frame: at 360 px and 200% text the frame in rem took
   101 px and "Bannerkeeper" broke in two in a 227 px column (final polish Z6); it takes 70 */
#col-notes .notice{display:flex;align-items:flex-start;gap:7px;margin:0 0 .75rem;padding-left:12px;
  padding-right:12px;overflow-wrap:anywhere}
#col-notes .notice .nx{all:unset;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;
  width:var(--ctl);height:var(--ctl);margin:-.5rem 0 -.5rem -5px;border-radius:50%;color:inherit;flex:none}
#col-notes .notice .nx:hover{background:rgba(0,0,0,.08)}
#col-notes .notice .nx:focus-visible{outline:2px solid var(--focus)}
#col-notes .notice .nx svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2;
  stroke-linecap:round}
#col-notes .notice .nt{flex:1 1 0;min-width:0}
#col-notes .notice p{margin:0}
/* the added-cards line is one target: its whole body (the words beside the ×) opens the
   newest-first view, with "Show them first" underlined as the link's words (audit C4: a 108×19 px
   inline link; D7 and N4: a 44 px box in the line reached the other lines, then grew its line and
   the strip by 25 px). The body's box is the strip's full height, overhanging the strip's padding
   as the × does, and 44 px at the least (less than the × takes), so the strip is no taller than
   its words and their leading is even */
#col-notes .notice a.nt{display:block;box-sizing:border-box;color:inherit;text-decoration:none;
  min-height:44px;margin:-.7rem 0;padding:.7rem 0;border-radius:4px}
#col-notes .notice a.nt .u{text-decoration:underline}
#col-notes .notice a.nt:hover .u{text-decoration-thickness:2px}
#col-notes .notice a.nt:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
#col-notes .notice ul{margin:.45rem 0 0;padding-left:1.2rem;font-weight:400;line-height:1.45}
#col-notes .notice li+li{margin-top:.3rem}
.collection .ui-widgets{margin:0 0 1rem}
.collection .ui-widgets a.ext{display:inline-flex;align-items:center}
/* the results bar: Set release's direction button beside the sort */
.collection .spanel .rbar .dirbtn{margin:0;width:var(--ctl);height:var(--ctl);padding:0;display:inline-flex;
  align-items:center;justify-content:center;border:1px solid var(--control-border);
  border-radius:var(--radius);
  background:var(--surface);color:var(--text)}
.collection .spanel .rbar .dirbtn:hover{background:var(--surface-2)}
.collection .spanel .rbar .dirbtn svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2}
.collection .spanel .rbar .sortcw select{min-width:0}
.collection .spanel .apply{margin:.5rem 0 0}
@media (max-width:599.98px){
  .collection .spanel .rbar .brka{display:block}
  .collection .spanel .rbar .sort:has(.sortcw) .dirbtn{display:none} }
/* the tiles: card search's, with the stepper under the name; the finish and condition on the
   picture; no mana row, so the name block is one row */
ul.collgrid{margin:0 0 1rem}
ul.collgrid > li[hidden],ul.colllist > li[hidden]{display:none}
ul.collgrid .nm{grid-template-rows:auto}
ul.collgrid .nm .name{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;
  min-height:2.5em;min-width:0}
ul.collgrid .pic .fin,ul.collgrid .pic .cond{position:absolute;bottom:6px;font-size:.72rem;font-weight:700;
  line-height:1.5;padding:0 .4rem;border-radius:4px;background:rgba(0,0,0,.72);color:#fff}
ul.collgrid .pic .fin{right:6px} ul.collgrid .pic .cond{left:6px}
ul.collgrid .act{display:flex;align-items:center;min-width:0}
/* the tiles of a row share a height (the grid's), and the stepper sits at every tile's foot, so
   steppers line up across a row whatever a tile's tags (polish B7) */
ul.collgrid > li.cc{display:flex;flex-direction:column}
ul.collgrid > li.cc[hidden]{display:none}  /* the name box narrows the tiles (collection.js narrow) */
ul.collgrid > li.cc > .meta{flex:1 1 auto}
ul.collgrid > li.cc > .act{margin-top:auto}
/* two tiles a row on a narrow phone at S and M (one at L); with larger text (the columns are
   sized in rem) one tile a row, so a card's name is never broken inside a word */
@media (max-width:420px){
  html:not([data-card-size=l]) ul.collgrid{
    grid-template-columns:repeat(auto-fill,minmax(min(100%,max(128px,9rem)),1fr))}
  ul.collgrid .cc .kebab{width:44px}
}
/* Select (0.7.46 audit QG-17): a tick box on every tile and row, shown while the select mode is
   on (the script adds .selecting to the list's section; without the script they always show) */
.collection .col-bulkform{display:none}
.collection.selectable:not(.selecting) .chk{display:none}
.collection .chk{display:grid;place-items:center;width:44px;height:44px;cursor:pointer;flex:0 0 auto}
.collection .chk input{width:20px;height:20px;margin:0;accent-color:var(--orange-ctl)}
ul.collgrid .nm{grid-template-columns:auto minmax(0,1fr) auto;align-items:center}
ul.collgrid .nm .chk{grid-column:1;grid-row:1;margin-left:-.4rem}
ul.collgrid .nm .name{grid-column:2;grid-row:1}
ul.collgrid .nm .kebab{grid-column:3;grid-row:1}
/* two tiles a row on a narrow phone left the name 81 px between Select's tick box and the ⋮ (two
   44 px targets), breaking "Bannerkeeper": the name takes the first row whole and the tick box
   and the ⋮ share the row under it */
@media (max-width:420px){
  html:not([data-card-size=l]) .collection:is(.selecting,:not(.selectable)) ul.collgrid .nm{
    grid-template-columns:auto minmax(0,1fr) auto;grid-template-rows:auto auto}
  html:not([data-card-size=l]) .collection:is(.selecting,:not(.selectable)) ul.collgrid .nm .name{
    grid-column:1 / -1;grid-row:1}
  html:not([data-card-size=l]) .collection:is(.selecting,:not(.selectable)) ul.collgrid .nm .chk{
    grid-column:1;grid-row:2}
  html:not([data-card-size=l]) .collection:is(.selecting,:not(.selectable)) ul.collgrid .nm .kebab{
    grid-column:3;grid-row:2} }
/* and the same on a tile narrow in its own text (under 10.5em: the 140 px tiles of a 320 px phone,
   where 85 px beside the ⋮ alone broke "Bannerkeeper"; 294 px at 200% text), whatever the Select
   state: at 412 px the 186 px tiles keep the ⋮ beside the name (polish B7) */
ul.collgrid > li.cc{container-type:inline-size}
@container (max-width:10.5em){
  ul.collgrid .nm{grid-template-columns:auto minmax(0,1fr) auto;grid-template-rows:auto auto}
  ul.collgrid .nm .name{grid-column:1 / -1;grid-row:1}
  ul.collgrid .nm .chk{grid-column:1;grid-row:2}
  ul.collgrid .nm .kebab{grid-column:3;grid-row:2} }
#col-notes .notice form.inline{display:inline-flex;margin:0 0 0 .5rem;vertical-align:middle}
#col-notes .notice form.inline .btn{margin:0}
ul.collgrid .cc.sel .pic,ul.colllist li.sel{outline:3px solid var(--orange-ctl);outline-offset:-3px}
ul.colllist .cardc .chk{margin-left:-.4rem}
.collection .col-select[aria-pressed=true]{background:var(--orange-tint);color:var(--orange-text)}
/* the 44 px stepper */
.collection form.qty{display:inline-flex;align-items:center;gap:0;margin:0}
.collection main form.qty > button{all:unset;cursor:pointer;box-sizing:border-box;margin:0;
  width:44px;height:44px;
  min-height:44px;padding:0;display:inline-flex;align-items:center;justify-content:center;
  border:1px solid var(--control-border);border-radius:var(--radius);background:var(--surface);
  color:var(--text)}
.collection main form.qty > button:hover{background:var(--surface-2)}
.collection main form.qty > button:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
.collection main form.qty > button[disabled]{opacity:.5;cursor:default}
.collection main form.qty > button svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2;
  stroke-linecap:round}
.collection form.qty output{min-width:2rem;text-align:center;font-weight:700;
  font-variant-numeric:tabular-nums}
/* the table (List view): its columns are rem widths, so it folds to the narrower layouts by its own
   width in rem (container queries beside the theme's pixel ones): at 200% text a desktop window
   holds the two-column layout, never a sideways scroll */
ul.colllist{list-style:none;margin:0 0 1rem;padding:0;container-type:inline-size}
@container (max-width:64rem){
  ul.colllist .tr{grid-template-columns:var(--cols-md)}
  ul.colllist .md-hide{display:none}
  ul.colllist .sub{display:flex}
  ul.colllist .cardc .lg{display:none}
  ul.colllist .cardc .sub .lgh{display:block} }
ul.colllist .tr{padding:.45rem .75rem}
ul.colllist .cardc{display:flex;align-items:center;gap:.6rem;min-width:0}
ul.colllist .cardc .tx{display:flex;flex-direction:column;min-width:0;flex:1 1 auto}
ul.colllist .cardc .name{font-weight:700;overflow-wrap:anywhere}
ul.colllist .s{display:block;color:var(--text-muted);font-size:.84rem;overflow-wrap:anywhere}
ul.colllist .smline{display:none}
ul.colllist .thumbbtn{all:unset;cursor:pointer;display:flex;align-items:center;justify-content:center;
  flex:none;min-width:44px;min-height:48px}
ul.colllist .thumbbtn:focus-visible{outline:2px solid var(--focus);outline-offset:2px}
/* prices (with the watchlists on): a tile's under its set line; the list's own column from 900 px,
   on the card's lines below that */
ul.collgrid .meta .pr{display:block;font-size:.86rem;font-weight:700;font-variant-numeric:tabular-nums;
  color:var(--text);overflow-wrap:anywhere}
ul.colllist .prc{font-variant-numeric:tabular-nums;font-weight:700;overflow-wrap:anywhere}
ul.colllist .pr{font-variant-numeric:tabular-nums}
ul.colllist .sub .lgh{display:block;font-weight:700}
ul.colllist .prc .s{font-weight:400}
@media (min-width:900px){ ul.colllist .sub .lgh{display:none} }
.collection .wdg form.inline{display:inline;margin:0}
.collection .wdg form.inline .btn{margin:0}
/* the import's progress strip: the words, then the bar across the strip (the theme's orange on
   the strip's own surface), then Refresh, or on a stop Resume and Stop here in a row */
.notice.importing{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem .75rem}
.notice.importing .itext{flex:1 1 14rem;min-width:0}
.notice.importing .btn{flex:0 0 auto;margin:0}
.notice.importing .iprog{flex:1 1 100%;width:100%;height:8px;margin:0;border:0;border-radius:4px;
  appearance:none;-webkit-appearance:none;background:var(--surface-3);overflow:hidden;display:block}
.notice.importing .iprog::-webkit-progress-bar{background:transparent}
.notice.importing .iprog::-webkit-progress-value{background:var(--orange);border-radius:4px;
  transition:width .4s ease}
.notice.importing .iprog::-moz-progress-bar{background:var(--orange);border-radius:4px}
.notice.importing .iacts{display:flex;flex-wrap:wrap;gap:.5rem;flex:1 1 100%}
.notice.importing .iacts form{margin:0;display:contents}
.notice.importing .iacts .btn{margin:0}
ul.colllist .thumb{width:34px;height:48px;border-radius:3px;object-fit:cover;background:var(--surface-3);
  display:block}
ul.colllist .setc{min-width:0;overflow-wrap:anywhere}
ul.colllist .qc{justify-self:start}
ul.colllist .th .qc{justify-self:start}
@media (max-width:899.98px){ ul.colllist .cardc .lg{display:none} }
@media (max-width:599.98px){
  /* the two-column layout (the name's cell and the ⋮): the container rule above would keep the
     three-column one, which left the name 32 px at 360 px (re-audit N01) */
  ul.colllist .tr.row{grid-template-columns:var(--cols-sm);grid-template-rows:auto auto;align-items:start}
  ul.colllist .cardc{grid-column:1;grid-row:1}
  ul.colllist .act{grid-column:2;grid-row:1}
  ul.colllist .qc{grid-column:1 / span 2;grid-row:2;margin-top:.35rem;
    padding-left:calc(34px * var(--thumb-scale,1) + .6rem)}
  ul.colllist .sub{display:none} ul.colllist .smline{display:block} }
/* a narrow list in its own text (a 320 px phone, or 200% text on any phone): the name and its
   lines go under the tick box and the picture, across the whole cell, so a name wraps between
   words and never inside one */
@container (max-width:22em){
  ul.colllist .tr.row{grid-template-columns:minmax(0,1fr) var(--ctl)}
  ul.colllist .cardc{flex-wrap:wrap;grid-column:1 / -1}
  ul.colllist .cardc .tx{flex-basis:100%}
  ul.colllist .act{grid-column:2;grid-row:2;align-self:center}
  ul.colllist .qc{grid-column:1;padding-left:0} }
.loadmore{display:flex;flex-direction:column;align-items:center;gap:.4rem;margin:.5rem 0 1.25rem}
.loadmore .btn{margin:0;min-width:min(12rem,calc(100vw - 2rem))}
.loadmore p{margin:0}
.col-nomatch[hidden]{display:none}
.coll-empty{text-align:center;padding:2rem 1rem}
.coll-empty .actions{justify-content:center}
.coll-empty .actions .btn{margin:0}
/* the Add cards and Details boxes */
.mtgbox .body .coltabs [role=tab] svg{width:16px;height:16px;fill:none;stroke:currentColor;stroke-width:2;
  margin-right:.3rem}
.mtgbox .body .fl{display:grid;grid-template-columns:1fr 1fr;gap:.6rem .75rem}
.mtgbox .body .fl .field{display:flex;flex-direction:column;gap:.25rem;margin:0;min-width:0}
.mtgbox .body .fl .field > span:first-child{font-weight:700;font-size:.9rem}
.mtgbox .body .fl .field select,.mtgbox .body .fl .field input{margin:0;width:100%;box-sizing:border-box}
.mtgbox .body .fl .span,.mtgbox .body .fl .tags{grid-column:1 / -1}
.mtgbox .body .fl .check{display:flex;align-items:center;gap:.5rem;min-height:44px;margin:0;cursor:pointer}
.mtgbox .body .fl .check input{width:22px;height:22px;margin:0;flex:none}
.mtgbox .body .tagline{display:flex;flex-wrap:wrap;gap:.3rem}
.mtgbox .body .dsub{margin:0 0 .6rem}
.mtgbox .body .importform{display:grid;gap:.6rem}
.mtgbox .body .importform p{margin:0}
.mtgbox .body textarea{width:100%;box-sizing:border-box;font-family:ui-monospace,Menlo,Consolas,monospace;
  font-size:.9rem;margin:0}
.mtgbox .body .filepick{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem}
.mtgbox .body .filepick .lbl{font-weight:700;font-size:.9rem}
.mtgbox .body .filepick .btn{margin:0}
@media (max-width:420px){ .mtgbox .body .fl{grid-template-columns:1fr} }
/* how to import the Export CSV on archidekt.com: a fold under the list with a two-column table */
.collection details.csvhelp{margin:0 0 1rem}
.collection details.csvhelp > summary .t{min-width:0;overflow-wrap:anywhere}
.csvhelp .tbl{overflow-x:auto;max-width:min(100%,40rem)}
.csvhelp table{border-collapse:collapse;width:100%;font-size:.95rem}
/* break-word: a column is at least its longest word wide and the box scrolls sideways when the
   columns do not fit, instead of "Conditi/on" (batch 10, AB2) */
.csvhelp th,.csvhelp td{text-align:left;vertical-align:top;padding:.4rem .5rem;overflow-wrap:break-word;
  border-bottom:1px solid var(--border-soft)}
.csvhelp thead th{background:var(--surface-2)}
.csvhelp tbody th{font-weight:700}
"""


def _price_clock() -> float:
    """The daily price check's clock (prices.now), which says when a Binder was last opened."""
    from . import prices

    return prices.now()


def add_collection(server: MCPServer, state: AppState) -> CollectionService:
    service = CollectionService(state.decks, getattr(state, "scan", None))
    state.collection = service  # type: ignore[attr-defined]
    add_collection_routes(server, state, service)
    add_collection_tools(server, service)
    return service


def add_collection_routes(server: MCPServer, state: AppState, service: CollectionService) -> None:
    s = state.settings

    def page(
        title: str,
        body: str,
        *,
        sub: str,
        sid: str | None,
        status: int = 200,
        scripts: bool = False,
        heading: bool = True,
    ) -> Response:
        user = state.db.get_user(sub) or {}
        admin = bool(s.admin_group and s.admin_group in (user.get("groups") or []))
        resp = render(
            title,
            body,
            site=s.server_name,
            status=status,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=admin,
            sub=sub,
            wide=True,
            heading=heading,
            body_class="collection",
            current="/collection",
            head_extra=f"{stylesheet_link('deck.css')}<style>{CARDS_CSS}{COLLECTION_CSS}{VISIBILITY_CSS}</style>"
            + (
                "<script src='/static/cardview.js' defer></script>"
                "<script src='/static/searchpanel.js' defer></script>"
                "<script src='/static/deck.js' defer></script><script "
                "src='/static/collection.js' defer></script>"
                "<script src='/static/filepick.js' defer></script>"
                if scripts
                else ""
            ),
        )
        resp.headers["Content-Security-Policy"] = COLLECTION_CSP
        return resp

    async def form(request: Request) -> dict[str, str]:
        from urllib.parse import parse_qs

        raw = await read_limited(request, IMPORT_FORM_BYTES)  # an import pastes up to 2 MB of text
        if raw is None:
            return {}
        fields = parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True)
        # the bar's ticked rows (sel) and the Tags box's ticks come several times: kept one a line
        return {k: ("\n".join(v) if k in ("sel", "tags") else v[0]) for k, v in fields.items()}

    def check_csrf(sid: str | None, data: dict[str, str]) -> bool:
        return csrf_form_ok(s, sid, data)

    def api_user(request: Request, *, write: bool) -> tuple[str, str] | Response:
        sub, sid = browser_session(state, request)
        if not sub or not sid:
            return JSONResponse(
                {"ok": False, "error": "unauthenticated", "login": "/login?next=/collection"},
                401,
                headers=NO_STORE,
            )
        if write:
            if not csrf_header_ok(s, sid, request):
                return JSONResponse(
                    {"ok": False, "error": "csrf", "message": "Reload the page and retry."},
                    403,
                    headers=NO_STORE,
                )
        elif not prefs.from_this_site(request):  # a read asks Archidekt on the member's own allowance
            return JSONResponse({"ok": False, "error": "cross_site"}, 403, headers=NO_STORE)
        return sub, sid

    async def json_body(request: Request) -> dict[str, Any] | Response:
        raw = await read_limited(request, 2_000_000)
        if raw is None:
            return JSONResponse({"ok": False, "error": "too_large"}, 413, headers=NO_STORE)
        try:
            data = json.loads(raw or b"{}")
        except (ValueError, RecursionError):
            return JSONResponse(
                {"ok": False, "error": "invalid", "message": "bad JSON"}, 400, headers=NO_STORE
            )
        if not isinstance(data, dict):
            return JSONResponse(
                {"ok": False, "error": "invalid", "message": "expected an object"}, 400, headers=NO_STORE
            )
        return data

    # An import's result, kept for the page the redirect lands on: by a random token in the URL
    # (never the text itself), for the member who imported, IMPORT_RESULT_TTL seconds, read once.
    results: dict[str, tuple[str, float, dict[str, Any]]] = {}

    def keep_result(sub: str, result: dict[str, Any]) -> str:
        now = time.monotonic()
        for k in [k for k, (_, t, _) in results.items() if now - t > IMPORT_RESULT_TTL]:
            results.pop(k, None)
        while len(results) >= IMPORT_RESULTS_MAX:
            results.pop(next(iter(results)))
        token = secrets.token_urlsafe(12)
        results[token] = (sub, now, result)
        return token

    def take_result(sub: str, token: str | None) -> dict[str, Any] | None:
        hit = results.get(token or "")
        if hit is None or hit[0] != sub:
            return None
        results.pop(token or "", None)
        return hit[2] if time.monotonic() - hit[1] <= IMPORT_RESULT_TTL else None

    # -- Select's bar (0.7.46 audit QG-17) -------------------------------------------------------
    # The ticked rows go one record at a time through the same writes a row's menu uses (update,
    # remove, each read back); a row that could not be changed is named, the rest still go. A
    # removal's rows are kept for Undo a while, by a token that is the member's own.
    bulk_undos: dict[str, tuple[str, float, list[dict[str, Any]]]] = {}
    BULK_DOES = ("condition", "finish", "tags", "remove")
    BULK_SAID = {"condition": "Condition set", "finish": "Finish set", "tags": "Tags set"}
    STOP_KINDS = ("not_linked", "auth", "writes_disabled", "unavailable", "rate_limited", "busy")

    def bulk_rows(data: dict[str, str]) -> list[tuple[int, str]]:
        """The ticked rows: ``sel`` lines of "id name" (the name finds the record in one read)."""
        rows: list[tuple[int, str]] = []
        for line in (data.get("sel") or "").split("\n"):
            rid, _, name = line.strip().partition(" ")
            if rid.isdigit() and 0 < int(rid) < 10**12:
                rows.append((int(rid), _clean(name, 200)))
        return rows

    def bulk_notice(sub: str, kind: str, message: str, *, undo: str = "") -> str:
        token = keep_result(sub, {"message": message, "kind": kind, "undo": undo})
        return f"{'ok' if kind == 'ok' else 'err'}=bulk&t={token}"

    def _cards(n: int) -> str:
        return f"{n} card" if n == 1 else f"{n} cards"

    def _keep_undo(sub: str, items: list[dict[str, Any]]) -> str:
        now = time.monotonic()
        for k in [k for k, (_, t, _) in bulk_undos.items() if now - t > BULK_UNDO_TTL]:
            bulk_undos.pop(k, None)
        while len(bulk_undos) >= IMPORT_RESULTS_MAX:
            bulk_undos.pop(next(iter(bulk_undos)))
        token = secrets.token_urlsafe(18)
        bulk_undos[token] = (sub, now, items)
        return token

    async def bulk_change(sub: str, data: dict[str, str]) -> str:
        """One press of the bar: the redirect's notice code."""
        doing = data.get("do", "")
        rows = bulk_rows(data)
        if doing not in BULK_DOES:
            return "err=invalid"
        if not rows:
            return bulk_notice(sub, "error", "Tick a card first.")
        if len(rows) > MAX_BULK_ROWS:
            return bulk_notice(
                sub,
                "error",
                f"Select changes at most {MAX_BULK_ROWS} cards at a time. Tick fewer and go on with "
                "the rest after.",
            )
        fields: dict[str, Any] = {}
        if doing == "condition":
            if data.get("condition", "") not in CONDITIONS:
                return "err=invalid"
            fields["condition"] = data.get("condition", "")
        elif doing == "finish":
            if data.get("finish") not in FINISHES:
                return "err=invalid"
            fields["finish"] = data["finish"]
        elif doing == "tags":
            fields["tags"] = [t for t in (data.get("tags") or "").split("\n") if t.strip()]
        done = 0
        failed: list[str] = []
        kept: list[dict[str, Any]] = []
        tags: list[dict[str, Any]] | None = None
        for rid, name in rows:
            try:
                if doing == "remove":
                    out = await service.remove(sub, rid, name=name)
                    row = out["removed"]
                    if row.get("tags"):
                        if tags is None:
                            tags = await service.tags(sub)
                        ids, _missing = collection_tags.ids_for([str(t) for t in row["tags"]], tags)
                    else:
                        ids = []
                    kept.append(
                        {
                            "name": row["name"],
                            "set": row.get("set") or "",
                            "collector_number": row.get("collector_number") or "",
                            "scryfall_id": row.get("scryfall_id") or "",
                            "card_id": row.get("archidekt_card_id"),
                            "quantity": row.get("quantity") or 1,
                            "finish": row.get("finish") or "nonfoil",
                            "condition": row.get("condition") or "",
                            "language": row.get("lang") or "EN",
                            "tag_ids": ids,
                            "purchase_price": row.get("purchase_price"),
                        }
                    )
                else:
                    await service.update(sub, rid, fields, name=name)
                done += 1
            except CollectionError as exc:
                if exc.kind in STOP_KINDS:
                    if done or failed:
                        # part went: the page says so before the refusal's own notice
                        exc.args = (f"{_cards(done)} changed before Archidekt stopped answering: {exc}",)
                    raise
                failed.append(f"{name or rid} ({exc})")
        if doing == "remove":
            text = f"{_cards(done)} removed from your binder."
            undo = _keep_undo(sub, kept) if kept else ""
        else:
            text = f"{BULK_SAID[doing]} on {_cards(done)}."
            undo = ""
        if failed:
            text += f" {len(failed)} could not be changed: " + "; ".join(failed[:5])
            if len(failed) > 5:
                text += f"; and {len(failed) - 5} more"
            text += "."
        return bulk_notice(sub, "ok" if done else "error", text, undo=undo)

    async def bulk_restore(sub: str, token: str | None) -> str:
        """Undo of a bar's Remove: the kept rows added back (their printing, tags and price paid)."""
        hit = bulk_undos.pop(token or "", None)
        if hit is None or hit[0] != sub or time.monotonic() - hit[1] > BULK_UNDO_TTL:
            return bulk_notice(sub, "error", "That Undo is no longer available.")
        out = await service.add(sub, hit[2], source="manual", trusted=True, limit=MAX_BULK_ROWS)
        n = len(out["added"])
        text = f"{_cards(n)} are back in your binder." if n != 1 else "1 card is back in your binder."
        if out["skipped"]:
            text += f" {len(out['skipped'])} could not be added back."
        return bulk_notice(sub, "ok" if n else "error", text)

    # -- an import or a restore, in the background (collection_bulk) -------------------------------
    # One job per member at a time, its state in the database: the page polls
    # /collection/api/import/{id} for its strip, and a job that stopped (or was stopped by a
    # restart) offers Resume.
    from . import collection_bulk

    jobs = collection_bulk.BulkJobs(service)
    state.collection_jobs = jobs  # type: ignore[attr-defined]
    tasks: set[asyncio.Task[Any]] = set()
    # "Work it out now" on the Value widget: the member's first whole read, in the background
    # (PriceService.first_read), one per member at a time
    first_reads: set[str] = set()
    first_read_failed: set[str] = set()  # the last "Work it out now" could not read the Binder

    def start_first_read(sub: str) -> None:
        if sub in first_reads or state.prices is None:
            return
        first_reads.add(sub)
        first_read_failed.discard(sub)

        async def run() -> None:
            ok = False
            try:
                ok = await state.prices.first_read(sub)
            finally:
                first_reads.discard(sub)
                if not ok:
                    if len(first_read_failed) > 10_000:
                        first_read_failed.clear()
                    first_read_failed.add(sub)

        task = asyncio.create_task(run())
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    def import_text(job: dict[str, Any]) -> str:
        done, of = int(job["done"]), int(job["total"])
        what = (
            "Putting your binder back" if job["kind"] == "restore" else "Importing your list into your binder"
        )
        noun = "entries" if job["kind"] == "restore" else "lines"
        if job["state"] == "paused":
            return (
                f"Paused at {done:,} of {of:,} {noun}: your account has used most of its Archidekt actions "
                f"for now, or {outage('slow_down')} It goes on by itself when there is room, and keeps some "
                "back for your own changes."
            )
        if job["state"] == "stopped":
            reason = str((job.get("result") or {}).get("stopped") or "it could not go on").rstrip(".")
            restore = job["kind"] == "restore"
            n = int(job_result(job).get("cards") or 0)
            line = "entry" if restore else "line"
            said = (
                f"{'Put back' if restore else 'Imported'} {n:,} {'card' if n == 1 else 'cards'}, then the "
                f"{'restore' if restore else 'import'} stopped at {line} {done + 1:,} of {of:,}: {reason}."
            )
            if (job.get("result") or {}).get("in_flight"):
                # its write was out when it stopped: Archidekt may have taken it (collection_bulk)
                return (
                    f"{said} That {line}'s card was being sent when it stopped, so it may already be "
                    "in your binder; everything before it is. Resume checks your binder for it first, "
                    "so it is never added twice."
                )
            return f"{said} Everything before that is in your binder. Resume carries on from there."
        if job["state"] == "done":
            return f"Finished: {done:,} of {of:,} {noun} done."
        return f"{what}: {done:,} of {of:,} {noun} done. You can leave this page; it goes on."

    def import_strip(job: dict[str, Any], csrf: str) -> str:
        jid = _esc(job["id"])
        done, of = int(job["done"]), max(1, int(job["total"]))
        bar = (
            f"<progress class='iprog' max='{of}' value='{done}' aria-label='Lines done'>"
            f"{done} of {of}</progress>"
        )
        if job["state"] == "stopped":
            hidden = (
                f"<input type='hidden' name='csrf' value='{_esc(csrf)}'><input type='hidden' name='job' "
                f"value='{jid}'>"
            )
            return (
                f"<div class='notice warn importing stopped' role='alert' data-import-stopped='{jid}'>"
                f"<span class='itext'>{_esc(import_text(job))}</span>{bar}"
                "<span class='iacts'>"
                f"<form method='post' action='/collection' class='inline'>{hidden}"
                "<button type='submit' name='action' value='import_resume' class='btn btn-primary'>"
                f"{icon('refresh')}Resume</button></form>"
                f"<form method='post' action='/collection' class='inline'>{hidden}"
                "<button type='submit' name='action' value='import_done' class='btn'>"
                "Stop here and show what went in</button></form></span></div>"
            )
        return (
            f"<div class='notice ok importing' role='status' data-import='{jid}'>"
            f"<span class='ui-spin' aria-hidden='true'></span><span class='itext'>{_esc(import_text(job))}"
            f"</span>{bar} "
            + ui.button_link("Refresh", f"/collection?import={jid}", icon_name="refresh")
            + "</div>"
        )

    def job_result(job: dict[str, Any]) -> dict[str, Any]:
        """The page's notice for a job that ended or stopped (import_result_html)."""
        items = job["items"]
        result = job.get("result") or {}
        done = int(job["done"])
        got = import_result(items[:done], result)
        if job["state"] != "done":
            got.update(
                stopped=str(result.get("stopped") or "you stopped it"),
                stopped_at=done + 1,
                of=int(job["total"]),
            )
        if job["kind"] == "restore":
            got["restore"] = True
        return got

    def held_view(held: dict[str, Any], lq: dict[str, Any], view: str | None) -> dict[str, Any]:
        """A held Binder page brought up to the member's own writes since it was read, with every
        count the page shows from one place: ``got`` and ``whole`` as the page draws them (the
        listing's rows and total; the widgets' count and rows), the cards added since to put
        first (``added``: newest first, marked) and the line about the ones it can't place.

        The binder holds the entries read, less the ones removed since (on this page or not), plus
        the ones added since. An unfiltered listing is the whole binder, so its total is that
        number in any order; a filtered one keeps its own total as read (less the removals it
        showed), plus the added cards it shows. The added cards go first only where the listing
        is newest first from its first entry: Recently added newest first, the price sort before
        the first daily price check (listing falls back to Recently added, highest first meaning
        newest first), and a tag filter (always newest first). A name or tag filter shows the
        added cards that match it, as CollectionService.tagged matches. Never a row the page
        already holds; a price only from the gateway's own."""
        got, whole = dict(held["got"]), dict(held["whole"])
        added = held.get("added") if isinstance(held.get("added"), dict) else {}
        have = {r.get("id") for r in got.get("rows") or [] if isinstance(r, dict)}
        ids = [i for i in added.get("ids") or [] if i not in have]
        kept = added.get("rows") or {}
        removed = len(held.get("removed") or [])
        binder = max(0, int(whole.get("count") or 0) - removed + len(ids))
        filtered = bool(lq["q"] or lq["tag"])
        needle = lq["q"].casefold()

        def fits(r: dict[str, Any]) -> bool:
            if needle and needle not in str(r.get("name") or "").casefold():
                return False
            return not lq["tag"] or any(
                collection_tags.same_name(str(t), lq["tag"]) for t in r.get("tags") or []
            )

        # newest first from the top: the order Archidekt would put a new record first in
        fallback = lq["sort"] == "price" and got.get("out") is not None and not lq["tag"]
        newest_order = bool(lq["tag"]) or (lq["dir"] == "desc" and (lq["sort"] == "added" or fallback))
        newest = newest_order and not lq["offset"]
        rows_kept = [kept[str(i)] for i in reversed(ids) if str(i) in kept]
        matching = [r for r in rows_kept if fits(r)]
        unknown = len(ids) - len(rows_kept)  # beyond what is kept: counted, never shown
        shown_rows: list[dict[str, Any]] = []
        if newest:
            shown_rows = [{**r, "held_added": True} for r in matching]
            for r in shown_rows:
                r.pop("price", None)
            if priced():
                service.binder.with_prices(shown_rows)
            out_n = unknown
        else:
            out_n = len(matching) + unknown
        got["rows"] = shown_rows + list(got.get("rows") or [])
        if isinstance(got.get("shown"), int):
            got["shown"] += len(shown_rows)
        got["total"] = int(got.get("total") or 0) + len(shown_rows) if filtered else binder
        # the widgets: the binder's count, and the rows they count from with the added ones first
        rows_now = [{**r, "held_added": True} for r in rows_kept] + list(whole.get("rows") or [])
        whole["count"] = binder
        whole["rows"] = rows_now
        if len(rows_now) < binder:  # some it can't see: copies and foils aren't counted
            whole["has_next"] = True
        line = ""
        if out_n:
            cards = f"{out_n:,} {'card' if out_n == 1 else 'cards'} you added"
            verb = "isn\u2019t" if out_n == 1 else "aren\u2019t"
            if newest:
                text = f"{cards} since {verb} shown here: the newest {HELD_ADDED_ROWS:,} are."
                line = strip(text, "info")
            else:
                where = "on this page" if newest_order else "in this order"  # a later page of it
                # the sort named outright: an address without one opens in the remembered sort
                from urllib.parse import urlencode

                qs = {k: lq[k] for k in ("q", "tag") if lq[k]} | {"sort": "added", "dir": "desc"}
                first = "/collection?" + urlencode(qs | ({"view": view} if view else {}))
                line = strip(
                    f"{_esc(cards)} {verb} shown {where}. <span class='u'>Show them first</span>",
                    "info",
                    html=True,
                    link=first,
                )
        return {"got": got, "whole": whole, "added": shown_rows, "line": line, "binder": binder}

    def strip(text: str, kind: str, *, html: bool = False, remember: str = "", link: str = "") -> str:
        """A notice strip at the top of the page, with a × to close (the page's script hides it).
        ``remember``: a name the script keeps the close under on this browser, so the strip stays
        closed on later visits (the price-sort note, re-audit N10). The strip is a flex row of two:
        the × and the body (``.nt``: the words, with an import result's list or a bulk Undo), so the
        body takes the room beside the × whatever it holds (audit C1: as loose text and a list it
        was two items, the × on a row of its own or the list beside the words). ``link``: the body
        is one link to that address (the added-cards line, audit N4)."""
        x = (
            "<button type='button' class='nx' aria-label='Close' title='Close' data-dismiss>"
            f"{icon('x')}</button>"
        )
        body = text if html else _esc(text)
        if link:
            body = f"<a class='nt' href='{_esc(link)}'>{body}</a>"
        else:
            body = f"<div class='nt'>{body}</div>"
        out = ui.notice(
            f"{x}{body}",
            kind,
            html=True,
            role="alert" if kind == "error" else "status",
        )
        if remember:
            out = out.replace("<div class='notice", f"<div data-remember='{_esc(remember)}' class='notice", 1)
        return out

    def page_address(qp: Any) -> str:
        """The Binder's address as looked at (its view, sort and filters), without the notice
        a redirect put in it: where a form on the page (Undo) comes back to (audit F-9)."""
        keep = [(k, v) for k, v in qp.multi_items() if k not in ("ok", "err", "t", "r", "add")]
        return "/collection" + (f"?{urlencode(keep)}" if keep else "")

    def notice(
        code: str | None, *, sub: str = "", token: str | None = None, csrf: str = "", back: str = ""
    ) -> str:
        if code == "bulk" and token:
            said = take_result(sub, token)
            if said is None or not said.get("message"):
                return ""
            text = _esc(str(said["message"]))
            undo = str(said.get("undo") or "")
            if undo and csrf:
                text += (
                    f" <form method='post' action='/collection' class='inline'>"
                    f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
                    "<input type='hidden' name='action' value='bulk_restore'>"
                    f"<input type='hidden' name='token' value='{_esc(undo)}'>"
                    f"<input type='hidden' name='next' value='{_esc(back or '/collection')}'>"
                    "<button type='submit' class='btn small'>Undo</button></form>"
                )
            return strip(text, "ok" if said.get("kind") == "ok" else "error", html=True)
        if code == "added" and token:
            said = take_result(sub, token)
            if said is not None and said.get("message"):
                return strip(str(said["message"]), "ok")
        if code in ("imported", "nothingimported", "toomany", "unreadable") and token:
            result = take_result(sub, token)
            if result is not None:
                html = import_result_html(result)
                kind = "error" if "notice error" in html[:40] else "ok"
                inner = re.sub(r"^<(p|div)[^>]*>", "", html.rstrip())
                inner = re.sub(r"</(p|div)>$", "", inner)
                return strip(inner, kind, html=True)
        msgs = {
            "added": "Added to your binder.",
            "removed": "Removed from your binder.",
            "updated": "Card details saved to your binder.",
            "nothing": "Nothing was added: no card matched. Check the name or pick a suggestion.",
            "nothingimported": "Nothing was imported: no card in the list was found.",
            "imported": "Imported into your binder.",
            "toomany": f"An import adds at most {MAX_IMPORT_ROWS:,} rows at a time. Split the file and "
            "import the rest after.",
            "busyimport": "One import runs at a time. This one goes on (or waits for Resume); start the "
            "next when it has ended.",
            "empty": "Your binder is already empty.",
            "unreadable": "That text is not a collection CSV or a card list the gateway can read.",
            "expired": "This form expired. Reload the page and try again.",
            "invalid": "That was not a valid request.",
            "lookup": outage("unavailable", again=True),
            "link": "Link your Archidekt account on the Account page first.",
            "writes": refusals.WRITES_OFF,
        }
        if not code or code not in msgs:
            return ""
        return strip(msgs[code], "ok" if code in ("added", "removed", "updated", "imported") else "error")

    def head(actions_html: str = "", *, sub: str = "", total: int | None = None) -> str:
        # Binder's switch (Cards | Lists | Watchlist) names the page, with the counts it knows:
        # this page's total, and how many cards the member watches (one count query)
        badges: dict[str, int] = {}
        if total is not None:
            badges["/collection"] = total
        if sub and priced():
            badges["/watchlist"] = state.prices.watch_count(sub)
        return ui.binder_head(
            "/collection", "Binder: Cards", PURPOSE, actions_html=actions_html, badges=badges
        )

    def problem(exc: CollectionError) -> str:
        if exc.kind in ("not_linked", "auth"):
            return (
                "<section class='panel coll-empty'><h2>Your binder lives on Archidekt</h2>"
                "<p>Link your Archidekt account to see it here. Your cards stay on Archidekt (your "
                "Archidekt collection).</p><div class='actions'>"
                + ui.button_link(
                    "Link Archidekt", "/account/details/archidekt", kind="primary", icon_name="account"
                )
                + "</div></section>"
            )
        return (
            f"<div id='col-notes'>{strip(str(exc), 'error')}</div>"
            "<section class='panel'><p class='muted'>Your binder is read live from Archidekt; when "
            "Archidekt is slow or down the page cannot show it.</p></section>"
        )

    def listing_query(qp: Any, request: Request | None = None, sub: str | None = None) -> dict[str, Any]:
        """The list's address: name filter, sort and direction, offset (the old ``page`` too).
        With ``request`` and ``sub``, the sort is the one remembered on the account when the
        address names none, and a pick from this site is remembered (prefs.sort_choice)."""
        q = _clean(qp.get("q"), 80)
        if request is not None and sub:
            sort, direction = prefs.sort_choice(
                state.db,
                sub,
                request,
                "collection",
                sorts=SORTS,
                default="added",
                dirs=DIRECTION_WORDS,
                default_dir="desc",
            )
        else:
            sort = qp.get("sort") if qp.get("sort") in SORTS else "added"
            direction = qp.get("dir") if qp.get("dir") in DIRECTION_WORDS else "desc"
        try:
            offset = max(0, min(int(qp.get("offset") or 0), 1_000_000))
        except ValueError:
            offset = 0
        if not offset and qp.get("page"):
            try:
                offset = (max(1, min(int(qp.get("page")), 10_000)) - 1) * PAGE_SIZE
            except ValueError:
                offset = 0
        tag = _clean(qp.get("tag"), collection_tags.TAG_NAME_MAX)
        return {"q": q, "sort": sort, "dir": direction, "offset": offset, "tag": tag}

    def link_to(lq: dict[str, Any], view: str | None, offset: int, *, api: bool = False) -> str:
        from urllib.parse import urlencode

        qs: dict[str, Any] = {}
        if lq["q"]:
            qs["q"] = lq["q"]
        if lq.get("tag"):
            qs["tag"] = lq["tag"]
        if lq["sort"] != "added":
            qs["sort"] = lq["sort"]
            if lq["dir"] != "desc":
                qs["dir"] = lq["dir"]
        if view:
            qs["view"] = view
        if offset:
            qs["offset"] = offset
        base = "/collection/api/rows" if api else "/collection"
        return f"{base}?{urlencode(qs)}" if qs else base

    def priced() -> bool:
        """Prices are kept (the watchlists are on: state.prices)."""
        return getattr(state, "prices", None) is not None

    def by_price(sub: str, lq: dict[str, Any]) -> bool:
        """The address asks for the price sort and the Binder was read whole: the rows come from
        that read, not from Archidekt."""
        if lq["sort"] != "price" or not priced():
            return False
        info = service.binder.info(sub)
        return info is not None and info.get("read_at") is not None

    async def listing(sub: str, lq: dict[str, Any]) -> dict[str, Any]:
        """The rows the address asks for: a screenful (SHOWN) from the Archidekt page that holds
        the offset, with what Load more needs (shown, total, has_more). Sorted by price: from the
        Binder as last read whole (binder_prices), with no Archidekt read; before that first read,
        in the default order. Rows carry their price when prices are kept."""
        offset = lq["offset"]
        if lq.get("tag"):
            # one tag: the Binder read whole and filtered here (CollectionService.tagged), newest
            # first whatever the sort
            got = await service.tagged(sub, lq["tag"], q=lq["q"], offset=offset, limit=SHOWN)
            rows = service.binder.with_prices([dict(r) for r in got["rows"]]) if priced() else got["rows"]
            shown = offset + len(rows)
            return {
                "out": None,
                "rows": rows,
                "total": got["total"],
                "shown": shown,
                "has_more": shown < got["total"],
                "tag": got,
            }
        if by_price(sub, lq):
            got = service.binder.listing(
                sub, q=lq["q"], offset=offset, limit=SHOWN, descending=lq["dir"] == "desc"
            )
            total = got["total"]
            shown = offset + len(got["rows"])
            return {
                "out": None,
                "rows": got["rows"],
                "total": total,
                "shown": shown,
                "has_more": shown < total,
            }
        if lq["sort"] == "price":
            lq = {**lq, "sort": "added"}
        page_no = offset // PAGE_SIZE + 1
        start = offset % PAGE_SIZE
        out = await service.page(
            sub, page=page_no, q=lq["q"], sort=lq["sort"], descending=lq["dir"] == "desc"
        )
        rows = out["rows"][start : start + SHOWN]
        if priced():
            rows = service.binder.with_prices([dict(r) for r in rows])
        total = int(out["count"])
        shown = offset + len(rows)
        has_more = start + SHOWN < len(out["rows"]) or bool(out["has_next"])
        return {"out": out, "rows": rows, "total": total, "shown": shown, "has_more": has_more}

    def more_html(lq: dict[str, Any], view: str | None, got: dict[str, Any]) -> str:
        total = got["total"]
        if got["has_more"]:
            return ui.load_more(
                link_to(lq, view, got["shown"]), text="Load more cards", shown=got["shown"], total=total
            )
        return ui.load_more("", done=f"All {total:,} {'entry' if total == 1 else 'entries'} shown.")

    @server.custom_route("/collection", methods=["GET"], include_in_schema=False)
    async def collection_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/collection")
        qp = request.query_params
        lq = listing_query(qp, request, sub)
        view, remember = prefs.view_choice(state.db, sub, request, "collection", ("grid", "list"), "grid")
        view_in_link = view if qp.get("view") in ("grid", "list") else None
        csrf = _csrf(s, sid) or ""
        # the Add cards box comes open on the old address and after an add or import
        result = qp.get("ok") or qp.get("err")
        add_tab = (
            "import"
            if qp.get("add") == "import"
            or result
            in (
                "nothingimported",
                "imported",
                "toomany",
                "unreadable",
            )
            else "add"
        )
        open_add = bool(qp.get("add")) or result in (
            "added",
            "nothing",
            "nothingimported",
            "imported",
            "toomany",
            "unreadable",
        )
        notes = notice(result, sub=sub, token=qp.get("r") or qp.get("t"), csrf=csrf, back=page_address(qp))
        # prices kept: opening the Binder puts it in the daily price check (binder_prices)
        if priced():
            service.binder.seen(sub, _price_clock())
            value = service.binder.value(sub)
            reading = sub in first_reads
            if lq["sort"] == "price" and value is None:
                notes += strip(
                    "Price sort starts after the daily price check; until then, newest first.",
                    "ok",
                    remember="price-sort",
                )
        else:
            value, reading = None, False
        # the member's import or restore: its strip while it runs or after it stopped, and once it
        # has ended, what it did (shown once, wherever the member opens the Binder next)
        job = jobs.job(sub)
        if job is not None and job["state"] == "done":
            jobs.finish(sub)
            said = job_result(job)
            code = "ok=imported" if said.get("added") else "err=nothingimported"
            return RedirectResponse(f"/collection?{code}&r={keep_result(sub, said)}", status_code=303)
        if job is not None:
            notes = import_strip(job, _csrf(s, sid) or "") + notes
        elif qp.get("import"):
            notes = (
                strip("That import is no longer running. The cards it added are in your binder.", "ok")
                + notes
            )
        # the profile (the Visibility line by the title) is read beside the collection, not after
        # it: one wait on Archidekt for the page, not two in a row
        profile_read = asyncio.ensure_future(profile_or_none(state.decks, sub))
        # the member's last good read of this listing, kept through their writes: shown dated
        # while Archidekt can't be asked (decks.short_reads.last), as the deck page shows a deck
        last_key = ("binder", json.dumps(lq, sort_keys=True, default=str))
        held: tuple[float, Any] | None = None
        added_rows: list[dict[str, Any]] = []  # cards added since the held copy was read, shown first
        added_line = ""  # and the line about the ones it can't place
        try:
            got = await listing(sub, lq)
            whole = (
                got["out"]
                if got["out"] is not None
                and not lq["q"]
                and lq["sort"] in ("added", "price")
                and not lq["offset"]
                else await service.page(sub)
            )
        except CollectionError as exc:
            notes_html = f"<div id='col-notes'>{notes}</div>" if notes else ""
            if (plan := busy_plan(exc)) is not None and (held := state.decks.short_reads.last(sub, last_key)):
                got, whole = held[1]["got"], held[1]["whole"]
                # the member's own writes since it was read (CollectionService._wrote)
                amended = bool(held[1].get("amended"))
                seen = held_view(held[1], lq, view_in_link)
                got, whole = seen["got"], seen["whole"]
                added_rows, added_line = seen["added"], seen["line"]
                notes = (
                    held_html(
                        exc,
                        held[0],
                        "your binder",
                        same_url(request),
                        amended=amended,
                        added=bool(added_rows),
                    )
                    + added_line
                    + notes
                )
            elif plan is not None:
                # the Binder keeps its title and notes; the busy strip sits where the list goes
                busy = page(
                    "Binder",
                    head() + notes_html + busy_body(plan, same_url(request), "your binder"),
                    sub=sub,
                    sid=sid,
                    status=plan.status,
                    heading=False,
                )
                busy.headers.update(busy_headers(plan))
                return busy
            else:
                status = 200 if exc.kind in ("not_linked", "auth") else _STATUS.get(exc.kind, 400)
                return page(
                    "Binder",
                    head() + notes_html + problem(exc),
                    sub=sub,
                    sid=sid,
                    status=status,
                    heading=False,
                )
        else:
            state.decks.short_reads.keep_last(sub, last_key, {"got": got, "whole": whole})
        rows = got["rows"]
        tag_names = [str(t["name"]) for t in whole.get("tags") or [] if t.get("name")]
        tag_colors = tag_colors_of(whole.get("tags"))
        if got.get("tag") is not None and not got["tag"]["known"]:
            notes += strip(f"You have no binder tag called “{lq['tag']}”.", "error")
        elif got.get("tag") is not None and not got["tag"]["complete"]:
            most = collection_tags.WHOLE_PAGES * collection_tags.WHOLE_PAGE_SIZE
            notes += strip(
                "Your binder is larger than the gateway reads at once: the tag filter shows the "
                f"tagged cards among your newest {most:,}.",
                "ok",
            )
        link = state.db.get_link(sub) or {}
        arch_user = str(link.get("archidekt_user_id") or "")
        live = "<p class='sr-only' id='live' role='status' aria-live='polite'></p>"
        details_row: dict[str, Any] | None = None
        if qp.get("details"):
            try:
                rid = _rid(qp.get("details"))
                details_row = next((r for r in rows if int(r["id"]) == rid), None)
                if details_row is None:
                    rec = await service._record(sub, rid)
                    details_row = row_out(rec)
                    collection_tags.name_rows([details_row], [rec], list(whole.get("tags") or []))
            except CollectionError:
                details_row = None
        details_box = details_box_html(
            details_row, csrf, open=details_row is not None, all_tags=tag_names, tag_colors=tag_colors
        )
        restore_box = ""
        if qp.get("restore"):
            snap = binder_copy(sub, str(qp.get("restore")))
            if snap is None:
                notes += strip("That copy of your binder is no longer kept.", "error")
            else:
                copies = sum(int(i["quantity"]) for i in collection_bulk.restore_items(snap))
                restore_box = restore_box_html(str(snap["id"]), csrf, now=int(whole["count"]), copies=copies)
        add_box = add_box_html(
            csrf, tab=add_tab, open=open_add, defaults=prefs.choices(state.db, sub, prefs.BINDER_ADD)
        )
        # the Visibility line by the title (profile_page): the profile as read a short while ago
        vis = collection_visibility(
            await profile_read,
            csrf,
            sub,
            stamp=press_stamp(state.settings, sub),
            open_box=bool(qp.get("visibility")),
            ok=qp.get("ok") or "",
        )
        if not rows and not lq["q"] and not lq["offset"] and not lq["tag"] and not added_line:
            body = (
                head(sub=sub, total=0)
                + vis
                + f"<div id='col-notes'>{notes}</div>"
                + "<section class='panel coll-empty'><h2>No cards yet</h2>"
                "<p>Scan a pile, or add cards by name.</p><div class='actions'>"
                + ui.button_link("Scan cards", "/scan", kind="primary", icon_name="camera")
                + ui.box_opener("Add by name", "/collection?add=1", "col-add", icon_name="plus")
                + ui.box_opener(
                    "Import a list", "/collection?add=import", "col-add", icon_name="list", cls="imp"
                )
                + "</div></section>"
                + live
                + export_help_html()
                + add_box
                + restore_box
            )
        else:
            # The head's More menu (the deck page's pattern): Export CSV, then the destructive
            # Clear binder… as its red last item. Clear binder is a proposal; its review page holds
            # the typed confirmation (actions.py). Add cards stays the one orange button.
            clear_form = (
                "<form method='post' action='/collection' class='clearf'>"
                f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
                f"<button type='submit' class='danger' name='action' value='clear'>{icon('trash')} "
                "Clear binder…</button></form>"
            )
            more = (
                "<details class='dd colmore'><summary class='btn' aria-haspopup='menu'>"
                f"{icon('more')} More</summary><div class='menu'>"
                f"<a href='/collection/export.csv'>{icon('download')} Export CSV</a>{clear_form}"
                "</div></details>"
            )
            acts = more + ui.box_opener(
                "Add cards", "/collection?add=1", "col-add", kind="primary", icon_name="plus"
            )
            cards = (
                list_html(rows, csrf, view=view, tag_colors=tag_colors)
                if rows
                else (
                    f"<ul class='{'cardgrid collgrid' if view == 'grid' else 'ui-table colllist'}' "
                    "id='cards' hidden></ul>"
                )
            )
            body = (
                head(acts, sub=sub, total=int(whole["count"]))
                + vis
                + f"<div id='col-notes'>{notes}</div>"
                + widgets_html(
                    whole,
                    arch_user,
                    value=value,
                    reading=reading,
                    priced=priced(),
                    csrf=csrf,
                    failed=sub in first_read_failed,
                )
                + panel_html(
                    q=lq["q"],
                    sort=lq["sort"],
                    direction=lq["dir"],
                    view=view,
                    count=got["total"],
                    priced=priced(),
                    tags=tag_names,
                    tag=lq["tag"],
                )
                + live
                + cards
                + f"<p class='muted col-nomatch'{' hidden' if rows or added_line else ''}>"
                "No cards match that filter.</p>"
                + more_html(lq, view_in_link, got)
                + bulk_html(csrf, same_url(request), tag_names, tag_colors)
                + export_help_html()
                + add_box
                + details_box
                + restore_box
            )
        resp = page("Binder", body, sub=sub, sid=sid, scripts=True, heading=False)
        if remember:
            prefs.remember_view(
                state.db, sub, resp, "collection", view, secure=s.public_url.startswith("https://")
            )
        return resp

    @server.custom_route("/collection/api/rows", methods=["GET"], include_in_schema=False)
    async def api_rows(request: Request) -> Response:
        """The next screenful for the Load more button, or (``offset`` 0) the first screenful of a
        name filter as the member types: the rows as the page draws them, the count line and the
        Load more block to put in place of the page's."""
        who = api_user(request, write=False)
        if isinstance(who, Response):
            return who
        sub, sid = who
        qp = request.query_params
        lq = listing_query(qp, request, sub)
        view = qp.get("view") if qp.get("view") in ("grid", "list") else "grid"
        try:
            got = await listing(sub, lq)
        except CollectionError as exc:
            return _fail(exc)
        csrf = _csrf(s, sid) or ""
        try:
            tags = got["out"]["tags"] if got["out"] is not None else await service.tags(sub)
        except CollectionError:
            tags = []
        colors = tag_colors_of(tags)
        html = "".join(row_html(r, csrf, view=view, tag_colors=colors) for r in got["rows"])
        total = got["total"]
        return JSONResponse(
            {
                "ok": True,
                "html": html,
                "shown": got["shown"],
                "total": total,
                "has_more": got["has_more"],
                "next": link_to(lq, view, got["shown"]) if got["has_more"] else "",
                "count_text": f"{total:,} {'entry' if total == 1 else 'entries'}",
                "more": more_html(lq, view if qp.get("view") in ("grid", "list") else None, got),
                "done": ""
                if got["has_more"]
                else f"All {total:,} {'entry' if total == 1 else 'entries'} shown.",
            },
            headers=NO_STORE,
        )

    def remember_add(sub: str, data: dict[str, Any]) -> None:
        """Keep the Add box's finish, condition and language for next time (prefs.BINDER_ADD)."""
        choice = {
            "finish": data.get("finish") if data.get("finish") in FINISHES else "nonfoil",
            "condition": data.get("condition") if data.get("condition") in CONDITIONS[1:] else "NM",
            "language": data.get("language") if data.get("language") in LANGUAGES else "EN",
        }
        prefs.save(state.db, sub, prefs.BINDER_ADD, choice)

    def with_defaults(sub: str, items: Any) -> Any:
        """A card menu's or the scan page's add asks no finish, condition or language: each item
        that names none gets the member's remembered ones (prefs.BINDER_ADD)."""
        kept = prefs.choices(state.db, sub, prefs.BINDER_ADD)
        if not kept or not isinstance(items, list):
            return items
        out = []
        for item in items:
            if isinstance(item, dict):
                skip = {"finish"} if "foil" in item else set()
                item = {**{k: v for k, v in kept.items() if k not in skip and not item.get(k)}, **item}
            out.append(item)
        return out

    @server.custom_route("/collection", methods=["POST"], include_in_schema=False)
    async def collection_post(request: Request) -> Response:
        """The page's own forms (no script needed): add by name, one more, one fewer, remove."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/collection")
        data = await form(request)
        back = _safe_next(data.get("next") or "/collection")
        if not back.startswith("/collection"):
            back = "/collection"
        if not check_csrf(sid, data):
            return RedirectResponse(f"{back}{'&' if '?' in back else '?'}err=expired", status_code=303)
        action = data.get("action", "")
        try:
            if action == "add":
                from .scan.service import parse_text

                text = _clean(data.get("name"), 200)
                qty = str(data.get("quantity", "1")).strip()
                # Copies up to MAX_QUANTITY (the text's own reader stops at 999); a count typed
                # before the name ("3 Sol Ring") counts when Copies is left at 1
                copies = min(MAX_QUANTITY, max(1, int(qty))) if qty.isdigit() else 1
                parsed = parse_text(text) if text else []
                if not parsed:
                    return RedirectResponse(
                        f"{back}{'&' if '?' in back else '?'}err=invalid", status_code=303
                    )
                p = parsed[0]
                if copies == 1:
                    copies = p.quantity
                out = await service.add(
                    sub,
                    [
                        {
                            "name": p.name,
                            "set": p.set_code,
                            "collector_number": p.collector_number,
                            "quantity": copies,
                            "finish": data.get("finish", "nonfoil"),
                            "condition": data.get("condition", ""),
                            "language": data.get("language", "EN"),
                        }
                    ],
                    source="manual",
                )
                if data.get("remember") == "1":
                    remember_add(sub, data)
                code = "ok=added" if out["added"] else "err=nothing"
                if out["added"]:
                    code += "&r=" + keep_result(sub, {"message": added_text(out["added"])})
            elif action == "value":
                if priced():
                    service.binder.seen(sub, _price_clock())
                    start_first_read(sub)
                code = ""
            elif action == "import":
                try:
                    items = parse_collection_import(data.get("text", ""))
                except CollectionError as exc:
                    code = "err=toomany" if "at most" in str(exc) else "err=unreadable"
                    code += "&r=" + keep_result(sub, {"refused": str(exc)[:REFUSAL_CHARS]})
                    return RedirectResponse(f"{back}{'&' if '?' in back else '?'}{code}", status_code=303)
                # in the background: the page shows its progress (a hundred lines are minutes of
                # paced Archidekt calls, too long for one request)
                service._user_id(sub)  # not linked: said at once
                service._writes_on()
                have = jobs.job(sub)
                if have is not None and have["state"] in ("running", "paused", "stopped"):
                    # one at a time: the one there is goes on (or waits for Resume or Done)
                    return RedirectResponse(
                        f"/collection?err=busyimport&import={have['id']}", status_code=303
                    )
                job = jobs.start(sub, items)
                return RedirectResponse(f"/collection?import={job['id']}", status_code=303)
            elif action in ("import_resume", "import_done"):
                have = jobs.job(sub)
                if have is None or have["id"] != data.get("job"):
                    code = ""
                elif action == "import_resume":
                    service._writes_on()
                    jobs.resume(sub)
                    return RedirectResponse(f"/collection?import={have['id']}", status_code=303)
                else:
                    ended = jobs.finish(sub)
                    code = ""
                    if ended is not None:
                        code = "err=imported&r=" + keep_result(sub, job_result(ended))
            elif action == "clear":
                # the whole binder: a proposal that only the member's press on its review page
                # applies, in every approval mode (actions.py clear_collection)
                current_client.set(BROWSER_CLIENT_ID)
                try:
                    made = await service.decks.propose_action(sub, "clear_collection")
                except DeckError as exc:
                    if "already empty" in str(exc):
                        return RedirectResponse("/collection?err=empty", status_code=303)
                    raise CollectionError(exc.kind, str(exc)) from exc
                return RedirectResponse(f"/proposals/{made['proposal_id']}", status_code=303)
            elif action == "details":
                rid = _rid(data.get("id"))
                fields: dict[str, Any] = {
                    "finish": data.get("finish", "nonfoil"),
                    "condition": data.get("condition", ""),
                    "language": data.get("language", "EN"),
                    "purchase_price": data.get("purchase_price", ""),
                }
                if data.get("tags_shown") == "1":  # the boxes were on the form: the ticked ones
                    fields["tags"] = [k[4:] for k, v in data.items() if k.startswith("tag:") and v]
                await service.update(sub, rid, fields, name=_clean(data.get("name"), 200))
                code = "ok=updated"
            elif action == "bulk":
                code = await bulk_change(sub, data)
            elif action == "bulk_restore":
                code = await bulk_restore(sub, data.get("token"))
            elif action in ("inc", "dec", "remove"):
                rid = _rid(data.get("id"))
                name = _clean(data.get("name"), 200)
                if action == "remove":
                    await service.remove(sub, rid, name=name)
                    code = "ok=removed"
                else:
                    await service.set_quantity(sub, rid, delta=1 if action == "inc" else -1, name=name)
                    code = ""
            else:
                code = "err=invalid"
        except CollectionError as exc:
            if exc.kind in ("not_linked", "auth"):
                code = "err=link"
            elif exc.kind in ("unavailable", "rate_limited", "busy"):
                code = "err=lookup"
            elif exc.kind == "writes_disabled":
                code = "err=writes"
            else:
                code = "err=invalid"
        sep = "&" if "?" in back else "?"
        return RedirectResponse(f"{back}{sep}{code}" if code else back, status_code=303)

    @server.custom_route("/collection/export.csv", methods=["GET"], include_in_schema=False)
    async def export_csv(request: Request) -> Response:
        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/collection")
        try:
            text = await service.export_csv(sub)
        except CollectionError as exc:
            return RedirectResponse(
                "/collection?err=" + ("link" if exc.kind in ("not_linked", "auth") else "lookup"),
                status_code=303,
            )
        return Response(
            text,
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="collection.csv"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    def binder_copy(sub: str, snap_id: str) -> dict[str, Any] | None:
        """A copy of the member's Binder kept before a clear (collection_bulk.clear), else None."""
        snap = state.db.get_snapshot(snap_id[:60], sub) if snap_id else None
        return snap if collection_bulk.is_binder_snapshot(snap) else None

    def restore_refused(sub: str, sid: str | None, text: str, status: int = 400) -> Response:
        return page(
            "Restore not started",
            f"<div class='card'><p class='notice error'>{_esc(text)}</p><div class='actions'>"
            "<a class='btn btn-primary' href='/history?type=snapshots'>Back to History</a>"
            "<a class='btn' href='/collection'>Open your binder</a></div></div>",
            sub=sub,
            sid=sid,
            status=status,
        )

    @server.custom_route("/collection/restore", methods=["POST"], include_in_schema=False)
    async def restore_binder(request: Request) -> Response:
        """History's Restore to my binder: every entry of a copy kept before a clear added back,
        as a background job with the import's strip on the Binder page (resumable, paced). A Binder
        that is not empty asks first: the copies are added to what is there, never replace it."""
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/history")
        data = await form(request)
        if not check_csrf(sid, data):
            return restore_refused(sub, sid, "This form expired. Reload History and try again.", 403)
        snap_id = str(data.get("snapshot_id") or "")
        snap = binder_copy(sub, snap_id)
        if snap is None:
            return restore_refused(sub, sid, "That copy of your binder is no longer kept.", 404)
        try:
            service._user_id(sub)
            service._writes_on()
            have = jobs.job(sub)
            if have is not None and have["state"] in ("running", "paused", "stopped"):
                return RedirectResponse(f"/collection?err=busyimport&import={have['id']}", status_code=303)
            items = collection_bulk.restore_items(snap)
            if not items:
                return restore_refused(sub, sid, "That copy of your binder holds no cards.")
            if data.get("confirm") != "1":
                now = int((await service.page(sub))["count"])
                if now:  # the question is a box on the Binder page (restore_box_html)
                    return RedirectResponse(f"/collection?restore={snap_id}", status_code=303)
        except CollectionError as exc:
            if exc.kind == "writes_disabled":
                return restore_refused(sub, sid, refusals.WRITES_OFF, 503)
            return restore_refused(sub, sid, str(exc), _STATUS.get(exc.kind, 400))
        job = jobs.start(sub, items, kind="restore", snapshot_id=snap_id)
        return RedirectResponse(f"/collection?import={job['id']}", status_code=303)

    @server.custom_route("/collection/snapshots/{snap_id}.csv", methods=["GET"], include_in_schema=False)
    async def binder_copy_csv(request: Request) -> Response:
        """A copy of the Binder kept before a clear, as a CSV (History's Download CSV)."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect_here(request, "/history")
        snap = binder_copy(sub, str(request.path_params["snap_id"]))
        if snap is None:
            return Response("That copy of your binder is no longer kept.", status_code=404)
        day = time.strftime("%Y-%m-%d", time.gmtime(int(snap.get("taken_at") or 0)))
        return Response(
            collection_bulk.snapshot_csv(snap),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="binder-before-clear-{day}.csv"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    # -- JSON for the page script and the scan page ----------------------------------------------
    @server.custom_route("/collection/api/add", methods=["POST"], include_in_schema=False)
    async def api_add(request: Request) -> Response:
        who = api_user(request, write=True)
        if isinstance(who, Response):
            return who
        data = await json_body(request)
        if isinstance(data, Response):
            return data
        source = data.get("source") if data.get("source") in ("scan", "manual", "assistant") else "manual"
        try:
            items = data.get("items") or data.get("cards")
            if source == "manual":
                items = with_defaults(who[0], items)
            out = await service.add(who[0], items, source=source)
            out["message"] = added_text(out.get("added") or [])
            if source == "scan" and service.scan is not None and isinstance(data.get("scan_session"), str):
                # The scan was the draft for these cards; saved, it has done its job.
                service.scan.store.delete(data["scan_session"][:60], who[0])
        except CollectionError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/collection/api/rows/{rid}", methods=["POST"], include_in_schema=False)
    async def api_update(request: Request) -> Response:
        who = api_user(request, write=True)
        if isinstance(who, Response):
            return who
        data = await json_body(request)
        if isinstance(data, Response):
            return data
        try:
            rid = _rid(request.path_params["rid"])
            name = _clean(data.get("name"), 200)  # the card's name: one filtered read finds the record
            if "delta" in data:
                # the stepper: one more or one fewer than Archidekt holds now (two tabs add up)
                delta = data.get("delta")
                if not isinstance(delta, int) or isinstance(delta, bool) or abs(delta) > MAX_QUANTITY:
                    raise CollectionError("invalid", "Delta must be a whole number.")
                out = await service.set_quantity(who[0], rid, delta=delta, name=name)
            elif "quantity" in data:
                qty = data.get("quantity")
                if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0 or qty > MAX_QUANTITY:
                    raise CollectionError("invalid", "quantity must be a whole number.")
                out = await service.set_quantity(who[0], rid, qty, name=name)
            else:
                out = await service.update(
                    who[0], rid, {k: v for k, v in data.items() if k != "name"}, name=name
                )
        except CollectionError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/collection/api/rows/{rid}", methods=["DELETE"], include_in_schema=False)
    async def api_delete(request: Request) -> Response:
        who = api_user(request, write=True)
        if isinstance(who, Response):
            return who
        try:
            name = _clean(request.query_params.get("name"), 200)
            out = await service.remove(who[0], _rid(request.path_params["rid"]), name=name)
        except CollectionError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/collection/api/import/{jid}", methods=["GET"], include_in_schema=False)
    async def api_import(request: Request) -> Response:
        """An import's progress, for its strip on the page (static/collection.js polls it)."""
        who = api_user(request, write=False)
        if isinstance(who, Response):
            return who
        job = jobs.job(who[0])
        if job is None or job["id"] != request.path_params["jid"]:
            return JSONResponse({"ok": False, "error": "not_found", "message": "no such import"}, 404)
        nxt = "/collection" if job["state"] == "stopped" else ""
        if job["state"] == "done":
            # ended: its result goes with the address the page's script opens (shown once)
            jobs.finish(who[0])
            said = job_result(job)
            code = "ok=imported" if said.get("added") else "err=nothingimported"
            nxt = f"/collection?{code}&r={keep_result(who[0], said)}"
        return JSONResponse(
            {
                "ok": True,
                # "failed": a stop (the page then offers Resume); the page's script reloads on both
                "state": "failed" if job["state"] == "stopped" else job["state"],
                "done": job["done"],
                "of": job["total"],
                "text": import_text(job),
                "next": nxt,
            },
            headers=NO_STORE,
        )

    @server.custom_route("/collection/api/value", methods=["GET"], include_in_schema=False)
    async def api_value(request: Request) -> Response:
        """The Value widget while "Work it out now" reads the whole Binder, for the page to follow
        (static/collection.js): whether the read still runs, and the widget as it stands."""
        who = api_user(request, write=False)
        if isinstance(who, Response):
            return who
        sub = who[0]
        if not priced():
            return JSONResponse({"ok": True, "reading": False, "html": ""}, headers=NO_STORE)
        reading = sub in first_reads
        html_out = value_widget(
            service.binder.value(sub),
            reading=reading,
            now=time.time(),
            csrf=_csrf(s, who[1]) or "",
            failed=sub in first_read_failed,
        )
        return JSONResponse({"ok": True, "reading": reading, "html": html_out}, headers=NO_STORE)

    @server.custom_route("/collection/api/summary", methods=["GET"], include_in_schema=False)
    async def api_summary(request: Request) -> Response:
        who = api_user(request, write=False)
        if isinstance(who, Response):
            return who
        try:
            out = await service.summary(who[0])
        except CollectionError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)


def add_collection_tools(server: MCPServer, service: CollectionService) -> None:
    from mcp.server.auth.middleware.auth_context import get_access_token

    def _sub() -> str:
        token = get_access_token()
        if token is None or not token.subject:
            raise RuntimeError("no authenticated user on this request")
        # The proposing client is recorded on the proposal, and only that client may apply it
        # over MCP: without this the assistant could never apply its own collection proposal.
        current_client.set(token.client_id)
        page_turn.set(False)  # an app's request, not a member's page (archidekt.page_turn)
        return token.subject

    def _err(exc: CollectionError) -> dict[str, Any]:
        out: dict[str, Any] = {"ok": False, "error": exc.kind, "message": str(exc)}
        if exc.retry_after:
            out["retry_after"] = exc.retry_after
        return out

    @server.tool(
        name="list_collection",
        title="List the cards I own",
        description=(
            "The user's binder, their linked Archidekt collection: one row per printing and finish "
            "with a quantity, 100 a page, newest first. query filters by card name; sort: added, "
            "edition or price (highest first, from the daily price check, once the member opened their "
            "Binder); page; tag: one binder tag's cards. Returns cards, count, total_pages and tags. "
            "Read-only."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def list_collection(
        query: str | None = None, sort: str = "added", page: int = 1, tag: str | None = None
    ) -> dict[str, Any]:
        sub = _sub()
        page_no = max(1, min(int(page or 1), 10_000))
        if tag and _clean(tag, 64):
            try:
                got = await service.tagged(
                    sub, _clean(tag, 64), q=_clean(query, 80), offset=(page_no - 1) * PAGE_SIZE
                )
            except CollectionError as exc:
                return _err(exc)
            if not got["known"]:
                names = ", ".join(f"“{t['name']}”" for t in got["tags"]) or "none"
                return {
                    "ok": False,
                    "error": "not_found",
                    "message": f"No binder tag “{tag}” (yours: {names}).",
                }
            pages = max(1, -(-got["total"] // PAGE_SIZE))
            return {
                "ok": True,
                "cards": got["rows"],
                "count": got["total"],
                "page": page_no,
                "total_pages": pages,
                "has_next": page_no < pages,
                "tags": got["tags"],
                **(
                    {}
                    if got["complete"]
                    else {"note": "Your binder is larger than one whole read; counted so far."}
                ),
            }
        info = service.binder.info(sub) if sort == "price" else None
        if info is not None and info.get("read_at") is not None:
            got = service.binder.listing(
                sub, q=_clean(query, 80), offset=(page_no - 1) * PAGE_SIZE, limit=PAGE_SIZE
            )
            pages = max(1, -(-got["total"] // PAGE_SIZE))
            return {
                "ok": True,
                "cards": got["rows"],
                "count": got["total"],
                "page": page_no,
                "total_pages": pages,
                "has_next": page_no < pages,
                "read_at": info["read_at"],
            }
        try:
            out = await service.page(
                sub,
                page=page_no,
                q=_clean(query, 80),
                sort=sort if sort in ("added", "edition") else "added",
            )
        except CollectionError as exc:
            return _err(exc)
        return {"ok": True, "cards": out["rows"], **{k: v for k, v in out.items() if k != "rows"}}

    async def _removals(sub: str, remove: Any) -> list[dict[str, Any]]:
        """Resolve ``remove`` entries ({id | name, quantity?}) against the collection now, so the
        review rows name the cards and copies that will go."""
        if remove is None:
            return []
        if not isinstance(remove, list) or len(remove) > 100:
            raise CollectionError("invalid", "remove must be a list of up to 100 entries.")
        out: list[dict[str, Any]] = []
        for entry in remove:
            if isinstance(entry, str):
                entry = {"name": entry}
            if not isinstance(entry, dict):
                raise CollectionError("invalid", "Each remove entry is {id | name, quantity?}.")
            qty = entry.get("quantity")
            if qty is not None and (not isinstance(qty, int) or isinstance(qty, bool) or qty < 1):
                raise CollectionError("invalid", "quantity must be a whole number of 1 or more.")
            if entry.get("id") is not None and str(entry["id"]).strip():
                rec = row_out(await service._record(sub, _rid(entry["id"]), _clean(entry.get("name"), 200)))
                rows = [rec]
            elif entry.get("name"):
                rows = await service.find_by_name(sub, str(entry["name"]))
                if not rows:
                    raise CollectionError(
                        "not_found", f"Your binder has no card called “{_clean(str(entry['name']), 60)}”."
                    )
            else:
                raise CollectionError("invalid", "Each remove entry needs id or name.")
            left = qty
            for r in rows:
                if left is not None and left <= 0:
                    break
                take = int(r["quantity"]) if left is None else min(left, int(r["quantity"]))
                out.append(
                    {
                        "id": int(r["id"]),
                        "name": r["name"],
                        "quantity": take,
                        "printing": f"({r['set'].upper()} {r['collector_number']}"
                        f"{', Foil' if r.get('finish') and r['finish'] != 'nonfoil' else ''})"
                        if r.get("set")
                        else None,
                    }
                )
                if left is not None:
                    left -= take
        return out

    async def _tagging(sub: str, tag: Any) -> list[dict[str, Any]]:
        """Resolve ``tag`` entries ({id | name, tags}) against the binder now: one row per record,
        with its tags before, after, and the names the member has no tag for yet (made at apply)."""
        if tag is None:
            return []
        if not isinstance(tag, list) or len(tag) > MAX_ITEMS_PER_CALL:
            raise CollectionError("invalid", f"tag must be a list of up to {MAX_ITEMS_PER_CALL} entries.")
        have = await service.tags(sub)
        out: list[dict[str, Any]] = []
        for entry in tag:
            if not isinstance(entry, dict):
                raise CollectionError("invalid", "Each tag entry is {id | name, tags}.")
            try:
                names = collection_tags.clean_names(entry.get("tags"))
            except DeckError as exc:
                raise CollectionError("invalid", str(exc)) from exc
            if entry.get("id") is not None and str(entry["id"]).strip():
                recs = [await service._record(sub, _rid(entry["id"]), _clean(entry.get("name"), 200))]
            elif entry.get("name"):
                want = _clean(entry["name"], 200).casefold()
                found = _Found()
                found.tried_whole = True
                await service._records_for(sub, want, found)
                recs = [r for r in found.records if row_out(r)["name"].casefold() == want]
                if not recs:
                    raise CollectionError(
                        "not_found", f"Your binder has no card called “{_clean(str(entry['name']), 60)}”."
                    )
            else:
                raise CollectionError("invalid", "Each tag entry needs id or name.")
            by_id = {t["id"]: t for t in have}
            for rec in recs:
                r = row_out(rec)
                before = collection_tags.record_tag_names(rec, by_id)
                if [n.casefold() for n in before] == [n.casefold() for n in names]:
                    continue
                out.append(
                    {
                        "id": int(r["id"]),
                        "name": r["name"],
                        "printing": f"({r['set'].upper()} {r['collector_number']})" if r.get("set") else None,
                        "tags": names,
                        "before": before,
                        "new": collection_tags.ids_for(names, have)[1],
                    }
                )
        if tag and not out:
            raise CollectionError("invalid", "Those entries already have exactly those binder tags.")
        return out

    async def _apply_tags(
        sub: str, entries: list[dict[str, Any]], progress: dict[str, Any], result: dict[str, Any]
    ) -> list[str]:
        """The tag changes of an approved proposal: a tag that does not exist yet is made first
        (its color Archidekt's color tag default), then each entry's tags are set. Mismatches in
        Archidekt's answers are returned, not raised."""
        from .account_lists import DEFAULT_TAG_COLOR

        wanted = {n.casefold(): n for e in entries for n in e.get("tags") or []}
        have = await collection_tags.read_tags(service.decks, sub)
        made: list[str] = []
        for key, name in wanted.items():
            if any(t["name"].casefold() == key for t in have):
                continue
            await service._run(
                sub,
                lambda token, n=name: service.client.create_collection_tag(token, n, DEFAULT_TAG_COLOR),
                progress=progress,
            )
            made.append(name)
        result["tags_made"] = made
        wrong: list[str] = []
        for e in entries:
            got = await service.update(
                sub,
                _rid(e["id"]),
                {"tags": list(e.get("tags") or [])},
                name=str(e.get("name") or ""),
                progress=progress,
                raise_wrong=False,
            )
            result["tagged"].append({"id": e["id"], "name": got["row"]["name"], "tags": got["row"]["tags"]})
            if got.get("wrong"):
                wrong.append(
                    f"{got['row']['name']}: Archidekt's answer does not show the tags that were sent"
                )
        return wrong

    async def apply_changes(sub: str, changes: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        """Apply an approved ``collection`` proposal: the stored adds go through service.add (the
        same path as the page), the stored removals by record id. A scan the adds came from is
        removed once every card went in. Called by DeckService under its Archidekt slot."""
        adds = changes.get("add") or []
        tag_entries = changes.get("tag") or []
        result: dict[str, Any] = {"added": [], "skipped": [], "removed": [], "tagged": []}
        expected: dict[Any, int | None] = {}  # record id -> copies it should now hold (None: gone)
        # each record written counts one (CollectionService._run), of every add, removal and tagging
        progress["of_entries"] = len(adds) + len(changes.get("remove") or []) + len(tag_entries)
        tag_wrong: list[str] = []
        try:
            if adds:
                source = str(changes.get("source") or "assistant")
                out = await service.add(sub, adds, source=source, progress=progress)
                result["added"], result["skipped"] = out["added"], out["skipped"]
                for row in out["added"]:
                    expected[row["id"]] = int(row["quantity"])
                session_id = changes.get("scan_session_id")
                if session_id and service.scan is not None and not out["skipped"]:
                    service.scan.store.delete(str(session_id), sub)
            for r in changes.get("remove") or []:
                gone = await service.remove(
                    sub, _rid(r["id"]), int(r["quantity"]), progress=progress, name=str(r.get("name") or "")
                )
                left = int(gone["row"]["quantity"]) if gone.get("row") else None
                before = int(gone["removed"]["quantity"])
                # as an added row: quantity is what the record holds now (0 once it is gone),
                # with the copies it held before and the copies this removal took
                result["removed"].append(
                    {
                        **gone["removed"],
                        "quantity": left or 0,
                        "previous_quantity": before,
                        "removed_quantity": before - (left or 0),
                    }
                )
                expected[_rid(r["id"])] = left
            if tag_entries:
                tag_wrong = await _apply_tags(sub, tag_entries, progress, result)
        except CollectionError as exc:
            raise DeckError(exc.kind, str(exc), retry_after=archidekt_wait(exc)) from exc
        result["verified"], result["mismatches"] = await _read_back(sub, expected)
        if tag_wrong:
            result["verified"] = False
            result["mismatches"] = list(result["mismatches"]) + tag_wrong
        return result

    async def _read_back(sub: str, expected: dict[Any, int | None]) -> tuple[bool, list[str]]:
        """Read the collection again, as every deck apply reads its deck again, and compare each
        record the apply touched with the copies it should now hold. Not verified when the
        re-read fails or a record is out of reach (past the pages read)."""
        if not expected:
            return True, []
        try:
            rows = await service._raw_rows(sub, max_pages=10)
        except CollectionError as exc:
            return False, [f"could not read the binder back: {exc}"]
        have = {r.get("id"): r for r in rows}
        all_read = len(rows) < 10 * COLLECTION_PAGE_SIZE  # else a missing record may sit further on
        wrong: list[str] = []
        for rid, want in expected.items():
            rec = have.get(rid)
            if rec is None and not all_read:
                wrong.append(f"record {rid}: past the first 10 pages, not read back")
                continue
            got = int(rec.get("quantity") or 0) if rec is not None else None
            if got != want:
                name = row_out(rec)["name"] if rec is not None else f"record {rid}"
                wrong.append(
                    f"{name}: expected {want if want is not None else 'gone'}, found {got or 'none'}"
                )
        return not wrong, wrong

    service.decks.collection_apply = apply_changes

    in_chat = bool(getattr(service.decks.settings, "apply_in_chat", False))

    def _result(data: dict[str, Any]) -> Any:
        return proposal_tool_result(data, decks=service.decks, sub=_sub(), in_chat=in_chat, drawn=in_chat)

    @server.tool(
        name="propose_collection_changes",
        title="Propose changes to my binder",
        description=(
            "Propose adding cards to, or removing them from, the user's binder (their Archidekt "
            "collection): its diff and review_url; the user applies it, or you do with apply_proposal "
            "when assistant_may_apply is true. add: cards or names (no set_code and collector_number: "
            "Archidekt picks the printing); text: one per line, e.g. '2 Sol Ring (CMR) 472 *F*'; "
            "scan_session: a scan's matched cards (removed once applied); remove: {id "
            "(list_collection) | name, quantity?} (no quantity: every copy); tag: {id | name, tags} "
            "sets those rows' binder tags. At most 100 cards each way. Applied rows have quantity, "
            "previous_quantity and added_ or removed_quantity. clear: true, alone, proposes clearing "
            "the whole binder."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
        meta=dict(CARD_TOOL_META) if in_chat else None,
    )
    async def propose_collection_changes(
        add: list[CollectionAdd | str] | None = None,
        text: Annotated[str | None, Field(description="Or cards to add as text, one per line.")] = None,
        scan_session: Annotated[
            str | None, Field(description="Or a scan session's ID or name to add.")
        ] = None,
        remove: list[CollectionRemove | str] | None = None,
        tag: list[dict[str, Any]] | None = None,
        clear: bool = False,
    ) -> Any:
        sub = _sub()
        if clear:
            if add or text or scan_session or remove or tag:
                return _result(
                    {
                        "ok": False,
                        "error": "invalid",
                        "message": "clear goes alone: propose other changes apart",
                    }
                )
            try:
                return _result({"ok": True, **(await service.decks.propose_action(sub, "clear_collection"))})
            except DeckError as exc:
                return _result({"ok": False, "error": exc.kind, "message": str(exc), **exc.extra})
        tag_in = tag
        items: list[Any] = [card_aliases(dumped(a)) for a in add or []]
        remove = [dumped(r) for r in remove] if remove is not None else None
        source = "assistant"
        session_id = None
        warnings: list[str] = []
        try:
            # applied to the linked Archidekt account: without one it never could be (0.7.46 audit)
            service.decks._token(sub)
        except DeckError as exc:
            return _result({"ok": False, "error": exc.kind, "message": str(exc)})
        try:
            if text:
                from .scan.service import parse_text

                items += [
                    {
                        "name": c.name,
                        "set": c.set_code,
                        "collector_number": c.collector_number,
                        "quantity": c.quantity,
                        "foil": c.foil is True,
                    }
                    for c in parse_text(text)
                ]
            if scan_session:
                if service.scan is None:
                    raise CollectionError("unavailable", "Scanning is not available on this gateway.")
                from .scan.service import ScanError

                try:
                    sess = service.scan.find_session(sub, scan_session)
                except ScanError as exc:
                    raise CollectionError(exc.kind, str(exc)) from exc
                items += [
                    {**it, "card": it["card"], "foil": it.get("foil") is True}
                    for it in sess.get("items", [])
                    if it.get("card")
                ]
                warnings += [
                    f"{it.get('note') or it['card'].get('name')}; confirm it with the user"
                    for it in sess.get("items", [])
                    if it.get("card") and it.get("status") == "fuzzy"
                ]
                source = "scan"
                session_id = sess.get("id")
            adds = normalise_items(items) if items else []
            removals = await _removals(sub, remove)
            tagging = await _tagging(sub, tag_in)
        except CollectionError as exc:
            return _result(_err(exc))
        warnings += await _finish_warnings(adds, service.scan)
        # the names checked as a deck edit's adds are (0.7.46 audit): no card at all refuses the
        # change; one Archidekt doesn't have, or nothing could check, makes it high risk, so no
        # assistant applies it unasked. A scanned card (scryfall_id) is a card already.
        names = await service.decks.check_new_names(
            sub, [str(a.get("name") or "") for a in adds if not a.get("scryfall_id")]
        )
        if names["unknown"]:
            return _result(
                {
                    "ok": False,
                    "error": "unknown_cards",
                    "message": no_card_called(names["unknown"], meant=names["meant"])
                    + " Nothing was proposed.",
                    "unknown_cards": names["unknown"],
                    **({"did_you_mean": names["meant"]} if names["meant"] else {}),
                }
            )
        note: dict[str, Any] = {}
        if names["missing"]:
            note["missing_on_archidekt"] = names["missing"]
            warnings.append(
                "Archidekt doesn't have "
                + ", ".join(f"“{n}”" for n in names["missing"])
                + (
                    ", so applying leaves it out."
                    if len(names["missing"]) == 1
                    else ", so applying leaves them out."
                )
            )
        if names["unchecked"]:
            note["unchecked_names"] = names["unchecked"]
            warnings.append(
                "Couldn't check that "
                + ", ".join(f"“{n}”" for n in names["unchecked"])
                + (" is a card" if len(names["unchecked"]) == 1 else " are cards")
                + ", so this change waits for the user."
            )
        try:
            # the source and scan id travel with the proposal so the apply can drop the scan
            p = await service.decks.propose_collection(
                sub,
                adds,
                removals,
                extra={"source": source, "scan_session_id": session_id},
                tags=tagging,
                missing=names["missing"],
                unchecked=names["unchecked"],
            )
        except DeckError as exc:
            return _result({"ok": False, "error": exc.kind, "message": str(exc), **exc.extra})
        made = {"ok": True, **p, **note, **({"warnings": warnings} if warnings else {})}
        return _result(made)
