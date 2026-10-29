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

from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from . import prefs, refusals, ui
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
from .busy import busy_body, busy_headers, busy_plan, same_url
from .cardsearch_view import CARDS_CSS
from .deckpage import image_url
from .decks import DeckError, _writing, archidekt_wait, current_client
from .pages import (
    NO_STORE,
    _csrf,
    _safe_next,
    browser_session,
    csrf_form_ok,
    csrf_header_ok,
    login_redirect,
    press_stamp,
    read_limited,
)
from .profile_page import COLLECTION_CSS as VISIBILITY_CSS
from .profile_page import collection_visibility, profile_or_none
from .schemas import CollectionAdd, CollectionRemove, card_aliases, dumped
from .theme import esc as _esc
from .theme import icon, render, stylesheet_link

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .decks import DeckService
    from .scan.service import ScanService

logger = logging.getLogger(__name__)

MAX_ITEMS_PER_CALL = 100  # each card costs one or two Archidekt calls, paced about a second apart
MAX_QUANTITY = 9_999
MAX_PAGES_FOR_EXPORT = 50  # 5,000 records
# Finding records: one unfiltered read of this many covers a binder that small; a bigger one is read
# by Archidekt's ``cardName`` filter, a name at a time, up to FILTERED_PAGES pages of it.
WHOLE_READ_SIZE = 200
FILTERED_PAGES = 5
PAGE_SIZE = COLLECTION_PAGE_SIZE
FIRST_PAGE_TTL = 60.0  # seconds the unfiltered first page is served from memory
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
            return CollectionError("not_found", "Archidekt has no such collection record")
        if kind == "auth":
            return CollectionError("auth", str(exc))
        if kind == "contract":
            return CollectionError("unavailable", "Archidekt answered in an unexpected way; try again")
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


def normalise_items(items: Any) -> list[dict[str, Any]]:
    """Validate the cards an add names (a scan item with ``card``, ``{name, set?, collector_number?,
    scryfall_id?, quantity?, finish?, condition?}`` or a plain name) into flat records the add and
    a stored proposal both accept. Raises CollectionError for anything unusable."""
    if not isinstance(items, list) or not items:
        raise CollectionError("invalid", "give a non-empty list of cards")
    if len(items) > MAX_ITEMS_PER_CALL:
        raise CollectionError("invalid", f"at most {MAX_ITEMS_PER_CALL} cards per call")
    out: list[dict[str, Any]] = []
    for i, raw in enumerate(items):
        if isinstance(raw, str):
            raw = {"name": raw}
        if not isinstance(raw, dict):
            raise CollectionError("invalid", f"card {i} must be an object")
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
            raise CollectionError("invalid", f"card {i}: give a name, or a set and collector number")
        if not spec["name"] and spec["scryfall_id"]:
            spec["name"] = "?"
        out.append({**spec, **opts})
    return out


MAX_IMPORT_ROWS = MAX_ITEMS_PER_CALL  # one import is one add call; Archidekt is written a card at a time
IMPORT_RESULT_TTL = 300.0  # seconds an import's result waits for the page the redirect lands on
# An import runs in the background (``import_job``): while the member's Archidekt budget is below
# a quarter of it (kept for the member's own changes) it pauses, looking again this often.
IMPORT_PAUSE_POLL = 5.0
IMPORT_JOBS_MAX = 2_000
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
    return repr(value)


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
        raise CollectionError("invalid", "paste or choose a CSV or a card list first")
    if len(text) > 2_000_000:
        raise CollectionError("invalid", "that file is larger than 2 MB")
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
            raise CollectionError("invalid", f"the list could not be read: {exc}") from exc
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
        raise CollectionError("invalid", "no cards were found in that text")
    if len(items) > MAX_IMPORT_ROWS:
        raise CollectionError(
            "invalid",
            f"that is {len(items)} rows; an import adds at most {MAX_IMPORT_ROWS} at a time "
            "(Archidekt is written one card at a time). Split the file and import the rest after.",
        )
    out = normalise_items(items)
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
        raise CollectionError("invalid", "the CSV needs a Name column")

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
                f"line {n}: a cell is longer than {csv.field_size_limit():,} characters; "
                'check for a quotation mark (") that is not closed',
            ) from exc
        if row is None:
            break
        if not any(c.strip() for c in row):
            continue
        name = cell(row, "name")
        if not name:
            raise CollectionError("invalid", f"line {n}: no card name")
        qty = cell(row, "quantity") or "1"
        if not qty.isascii() or not qty.isdigit():  # str.isdigit alone accepts "²" and int() then fails
            raise CollectionError("invalid", f"line {n}: quantity {_quoted(qty)} is not a number")
        # a long digit string is past MAX_QUANTITY anyway, and int() refuses one over 4,300 digits
        if len(qty.lstrip("0")) > len(str(MAX_QUANTITY)) or not 1 <= int(qty) <= MAX_QUANTITY:
            raise CollectionError(
                "invalid", f"line {n}: quantity {_quoted(qty)} is not from 1 to {MAX_QUANTITY:,}"
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
        decks.write_hooks.append(self._on_write)
        # the Binder as last read whole, for its value and the price sort (binder_prices.py): the
        # gateway's own writes keep it current
        self.binder = BinderStore(decks.db)

    def _on_write(self, sub: str, path: str) -> None:
        if not path or path.startswith("/collection"):
            self._first_pages.pop(sub, None)
            # counted, so records an add read before another write are read again (_records_for);
            # the add's own writes keep what it read current, so they are not
            if not _own_write.get():
                self._writes[sub] = self._writes.get(sub, 0) + 1

    def _user_id(self, sub: str) -> str:
        link = self.decks.db.get_link(sub)
        if link is None:
            raise CollectionError(
                "not_linked", "No Archidekt account is linked. Link one on the Account page first."
            )
        uid = link.get("archidekt_user_id")
        if not uid or not str(uid).isdigit():
            raise CollectionError("not_linked", "The linked Archidekt account has no user id; relink it.")
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
        if cacheable:
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
        rows = [row_out(r) for r in body["results"] if isinstance(r, dict)]
        count = body.get("count")
        total_pages = body.get("totalPages")
        out = {
            "rows": rows,
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
        ``before_item()`` is awaited before each card (an import's pause while the budget is low)."""
        self._writes_on()
        wanted = [
            (it, {k: it[k] for k in ("quantity", "finish", "condition", "language")})
            for it in normalise_items(items)
        ]
        self._user_id(sub)  # fail early when no account is linked
        found = _Found()
        out = result if result is not None else {}
        out.update(added=[], skipped=[])
        for index, (spec, opts) in enumerate(wanted):
            while True:
                if before_item is not None:
                    await before_item()
                try:
                    await self._add_one(sub, index, spec, opts, found, out, progress)
                    break
                except CollectionError as exc:
                    # ``retry``: an import waits out a refusal of the member's budget (nothing sent)
                    if (
                        retry is not None
                        and getattr(exc, "busy_cause", None) == "budget"
                        and await retry(exc)
                    ):
                        continue
                    raise
            if on_item is not None:
                on_item(index + 1)
        added = out["added"]
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
    ) -> None:
        """One card of ``add``: resolve its printing, find a record of it in that finish (found),
        and add the copies there or create one. Its row lands in ``out``."""
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
        if have is not None:
            qty = min(MAX_QUANTITY, int(have.get("quantity") or 0) + opts["quantity"])
            written = await self._own_write(
                sub,
                lambda token: self.client.collection_set(token, have, quantity=qty),
                progress,
            )
            rec = {**have, **written, "card": have.get("card")}
        else:
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
                ),
                progress,
            )
            rec = {
                "condition": opts["condition"] or "NM",
                "language": lang,
                **written,
                "modifier": MODIFIERS[finish],
                "card": self._card_stub(printing, spec, written),
            }
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
        self.binder.put(sub, row)
        out["added"].append(row)

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
            raise CollectionError("not_found", "no such card in your binder")
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
        raise CollectionError("not_found", "no such card in your binder")

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
            self.binder.drop(sub, rid)
            self.decks.db.audit("collection_removed", sub=sub, detail={"id": rid})
            return {"row": None, "removed": row_out(rec)}
        out = await self._run(sub, lambda token: self.client.collection_set(token, rec, quantity=quantity))
        row = row_out({**rec, **out, "card": rec.get("card")})
        self.binder.put(sub, row)
        return {"row": row}

    async def update(self, sub: str, rid: int, data: dict[str, Any], *, name: str = "") -> dict[str, Any]:
        """Change a record's details: finish, condition, language and price paid (the fields
        Archidekt's own row editor offers; tags are read-only here)."""
        self._writes_on()
        rec = await self._record(sub, rid, name)
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
        if not changes:
            raise CollectionError("invalid", "nothing to change: give finish, condition, language or price")
        out = await self._run(sub, lambda token: self.client.collection_set(token, rec, **changes))
        row = row_out({**rec, **out, "card": rec.get("card")})
        self.binder.put(sub, row)
        read = {  # what Archidekt answered, in the gateway's terms (it keeps numbers for two)
            "purchasePrice": _price(out.get("purchasePrice")),
            "condition": condition_code(out.get("condition")) or None,
            "language": language_code(out.get("language")),
        }
        wrong = [k for k, want in changes.items() if read.get(k, out.get(k)) != want]
        self.decks.db.audit(
            "collection_updated",
            sub=sub,
            detail={"id": rid, "fields": sorted(changes), "verified": not wrong},
        )
        if wrong:
            raise CollectionError(
                "unavailable",
                "Archidekt accepted the change but answered with other values for: " + ", ".join(wrong),
            )
        return {"row": row}

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
            self.binder.drop(sub, rid)
            left = None
        else:
            out = await self._run(
                sub,
                lambda token: self.client.collection_set(token, rec, quantity=have - max(1, quantity)),
                progress=progress,
            )
            left = row_out({**rec, **out, "card": rec.get("card")})
            self.binder.put(sub, left)
        self.decks.db.audit("collection_removed", sub=sub, detail={"id": rid, "quantity": quantity})
        return {"removed": row_out(rec), "row": left}

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
            "invalid", f"card {i}: quantity must be a whole number from 1 to {MAX_QUANTITY}"
        )
    finish = _clean(raw.get("finish"), 10).lower()
    if not finish:
        finish = "foil" if raw.get("foil") is True else "nonfoil"
    if finish == "normal":
        finish = "nonfoil"
    if finish not in FINISHES:
        raise CollectionError("invalid", f"card {i}: finish must be one of {', '.join(FINISHES)}")
    condition = _clean(raw.get("condition"), 4).upper()
    if condition == "D":  # Archidekt's own code for Damaged
        condition = "DMG"
    if condition not in CONDITIONS:
        raise CollectionError("invalid", f"card {i}: condition must be one of NM, LP, MP, HP, DMG or empty")
    language = _clean(raw.get("language"), 3).upper() or "EN"
    language = ARCHIDEKT_TO_LANGUAGE.get(language, language)
    if language not in LANGUAGES:
        raise CollectionError("invalid", f"card {i}: language must be one of " + ", ".join(LANGUAGES))
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
    if result.get("stopped"):
        # an import that stopped part way: what went in, why it stopped, and what to do
        n = int(result.get("cards") or 0)
        lead = (
            f"Imported {n} {'card' if n == 1 else 'cards'}, then the import stopped at line "
            f"{int(result.get('stopped_at') or 0)} of {int(result.get('of') or 0)}: "
            f"{str(result['stopped']).rstrip('.')}. Nothing after that line was added; import the rest "
            "of the list again."
        )
        cls = "error"
    elif result.get("added"):
        n = int(result.get("cards") or 0)
        lead, cls = f"Imported {n} {'card' if n == 1 else 'cards'} into your binder.", "ok"
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
        raise CollectionError("invalid", "language must be one of " + ", ".join(LANGUAGES))
    return code


def _price(value: Any) -> float | None:
    """The price paid, as Archidekt stores it (a number or null). Accepts "", None, a number or a
    numeric string, with at most two decimals kept."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return None
    try:
        price = round(float(str(value).strip().lstrip("$").replace(",", ".")), 2)
    except ValueError as exc:
        raise CollectionError("invalid", "price paid must be a number") from exc
    if price < 0 or price > MAX_PRICE or price != price:
        raise CollectionError("invalid", f"price paid must be between 0 and {int(MAX_PRICE)}")
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
        raise CollectionError("invalid", "that is not a collection record id")
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
PURPOSE = "The cards you own, kept in your Archidekt account (your Archidekt collection)."
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
    """The list's Price column: the row's value, and under it the copies times the unit price."""
    p = r.get("price")
    if not p:
        return "–"
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
    form (the page's script asks first, then removes) and the Details… hook (opening it opens the
    Details box)."""
    name = _esc(r["name"])
    return (
        f"<form method='post' action='/collection' class='rm' hidden>{csrf_in}<button name='action' "
        f"value='remove' aria-label='Remove {name} from your binder'>Remove</button></form>"
        "<details class='rowmenu' hidden><summary>Details</summary></details>"
    )


def row_html(r: dict[str, Any], csrf: str, *, view: str) -> str:
    """One entry: card search's tile with the stepper and ⋮ (grid), or one table row (list)."""
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
        sub = set_line + (f" · {_esc(_language_name(lang))}" if lang != "EN" else "")
        price = price_text(r)
        return (
            f"<li class='cc c col' data-name='{_esc(r['name'].lower())}' data-id='{rid}'{attrs}>"
            f"<button type='button' class='pic thumbbtn' aria-label='Show {name}' title='Show the card'>"
            f"{body}{badges}</button>"
            f"<div class='meta'><span class='nm'><span class='name' title='{name}'>{name}</span>"
            f"{_kebab(r['name'], rid)}</span><span class='ty'>{sub}</span>"
            + (f"<span class='pr'>{_esc(price)}</span>" if price else "")
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
    price = _esc(price_text(r))
    sub_md = " · ".join(x for x in (set_line, set_name, fin_word, cond_word, lang_word) if x)
    sub_sm = " · ".join(x for x in (set_line, fin_word, _esc(cond or "Not set"), lang_word) if x)
    if price:  # under 900 px the Price column is hidden: the price goes on the card's own lines
        sub_md += f"<span class='pr lgh'>{price}</span>"
        sub_sm += f" · <span class='pr'>{price}</span>"
    return (
        f"<li class='tr row' role='row' data-name='{_esc(r['name'].lower())}' data-id='{rid}'{attrs}>"
        f"<span class='cardc' role='cell'><button type='button' class='thumbbtn' aria-label='Show {name}' "
        f"title='Show the card'>{thumb}</button><span class='tx'><span class='name' title='{name}'>{name}"
        "</span>"
        + (f"<span class='s lg'>{_esc(r['type_line'])}</span>" if r.get("type_line") else "")
        + f"<span class='sub'>{sub_md}</span><span class='s smline'>{sub_sm}</span></span></span>"
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


def list_html(rows: list[dict[str, Any]], csrf: str, *, view: str) -> str:
    items = "".join(row_html(r, csrf, view=view) for r in rows)
    if view == "list":
        style = _esc(f"--cols:{TABLE_COLS};--cols-md:{TABLE_MD};--cols-sm:{TABLE_SM}")
        return (
            f"<ul class='ui-table colllist' id='cards' role='table' aria-label='Your binder' "
            f"style='{style}'>{table_head_html()}{items}</ul>"
        )
    return f"<ul class='cardgrid collgrid' id='cards'>{items}</ul>"


def _opts(values: Iterable[str], current: str, labels: dict[str, str] | None = None) -> str:
    return "".join(
        f"<option value='{_esc(v)}'{' selected' if v == current else ''}>"
        f"{_esc((labels or {}).get(v, v))}</option>"
        for v in values
    )


def _lang_opts(current: str) -> str:
    langs = LANGUAGES if not current or current in LANGUAGES else (current, *LANGUAGES)
    return _opts(langs, current or "EN", {k: _language_name(k) for k in langs})


def details_box_html(r: dict[str, Any] | None, csrf: str, *, open: bool = False) -> str:
    """The Details box: finish, condition, language and price paid, saved to Archidekt (tags are
    shown as Archidekt holds them). With ``r`` it is drawn for that row (the old ``?details=ID``
    address renders it open); without, it is the template the page's script fills from a row."""
    r = r or {}
    rid = _esc(r.get("id", ""))
    finish = r.get("finish") or "nonfoil"
    cond = (r.get("condition") or "").upper()
    lang = (r.get("lang") or "EN").upper()
    tags = [str(t) for t in (r.get("tags") or [])[:8]]
    tagline = "".join(f"<span class='pill'>{_esc(t)}</span>" for t in tags)
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
        f"<div class='field tags'{'' if tags else ' hidden'}>"
        "<span>Tags <span class='muted small'>· change them "
        f"on Archidekt</span></span><span class='tagline'>{tagline}</span></div></div></form>"
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
        "<div class='segc coltabs' role='tablist' aria-label='Add cards'>"
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
        "placeholder='Name, or “name (SET) 123” for one printing' autocomplete='off' required></label>"
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
        "<p class='muted small'>Scanning a pile is quicker: open <a href='/scan'>Scan</a>, then choose "
        "<strong>Save to binder</strong>. Everything you add is added to your binder.</p>"
        "</form>"
    )
    import_form = (
        f"<form method='post' action='/collection' id='col-importform' class='importform' role='tabpanel' "
        f"aria-labelledby='col-tab-import' data-pane='import'{'' if imp else ' hidden'}>"
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'>"
        "<input type='hidden' name='action' value='import'>"
        "<p class='muted small'>A collection CSV or a card list, one card per line, "
        f"up to {MAX_IMPORT_ROWS} rows "
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
            "–",
            detail=(
                "Archidekt could not be asked for your whole binder just now. Try again later; the "
                "daily price check tries again too."
                if failed
                else "Worked out from your whole binder with the daily price check."
            ),
            actions=[now_btn] if csrf else [],
        )
    parts = []  # the copies are under Entries
    if value.get("day"):
        when = _day_words(value["day"], now)
        parts.append("Today's prices" if when == "today" else f"Prices of {when}")
    n = int(value.get("unpriced") or 0)
    if n:
        parts.append(f"{n:,} {'entry has' if n == 1 else 'entries have'} no price")
    if not value.get("complete"):
        parts.append(f"your first {value['entries']:,} entries")
    # no entry priced: no value to show (as Scan's list value), never "$0.00"
    big = (money(value["usd"]) or "$0.00") if int(value.get("entries") or 0) > n else "–"
    return ui.widget("Value", "stats", big, detail=" · ".join(parts))


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
    prices on; ``value`` from binder_prices), Added this week (from the newest 100 entries) and
    Foils (counted only when the whole collection fits one page of 100)."""
    now = time.time() if now is None else now
    rows = whole["rows"]
    total = int(whole["count"])
    large = int(whole.get("total_pages") or 1) > 1 or bool(whole.get("has_next"))
    open_link = [_archidekt_link(arch_user)] if arch_user else []
    since = now - 7 * 86400
    week = sum(1 for r in rows if _added_within(r, since))
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
            detail="Counted from your newest 100 entries" if large else f"Since {since_day}",
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
    return ui.widgets(items)


def panel_html(*, q: str, sort: str, direction: str, view: str, count: int, priced: bool = True) -> str:
    """The shared search panel: the name box, then the results bar (count, Sort with a direction
    for Set release and Price, Grid or List, the card size, Clear filters). ``priced``: Price is
    offered (prices on)."""
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
    count_text = f"{count:,} {'entry' if count == 1 else 'entries'}"
    bar = sp.results_bar(
        count_html=sp.count_html(count_text, count_id="col-count"),
        sort_html=sort_html,
        view_html=sp.view_group(view),
        clear_html="<a class='btn clr' href='/collection'>Clear filters</a>",
    )
    return (
        "<section class='panel spanel'><form id='listform' method='get' action='/collection'>"
        + sp.name_row(
            label="Filter by name",
            name="q",
            value=q,
            placeholder="Name",
            input_id="col-q",
            search_label="Filter",
            filters_button=False,
        )
        + bar
        + "<noscript><button type='submit' class='btn apply'>Apply</button></noscript></form></section>"
    )


COLLECTION_CSS = """
.value-reading .ui-spin{margin-right:.35em}
.collection .ui-head .acts{margin-left:auto;display:inline-flex;flex-wrap:wrap;gap:.5rem;align-items:center}
.collection .ui-head .acts .btn{margin:0}
.collection .ui-head.phead{align-items:center}
@media (max-width:599.98px){ .collection .ui-head .acts{margin-left:0;width:100%}
  .collection .ui-head .acts .btn{flex:1 1 auto;justify-content:center} }
/* notices are strips at the top of the page with a × to close */
#col-notes .notice{display:flex;align-items:flex-start;gap:.5rem;margin:0 0 .75rem;overflow-wrap:anywhere}
#col-notes .notice .nx{all:unset;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;
  width:var(--ctl);height:var(--ctl);margin:-.5rem 0 -.5rem -.4rem;border-radius:50%;color:inherit;flex:none}
#col-notes .notice .nx:hover{background:rgba(0,0,0,.08)}
#col-notes .notice .nx:focus-visible{outline:2px solid var(--focus)}
#col-notes .notice .nx svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2;
  stroke-linecap:round}
#col-notes .notice p{margin:0}
#col-notes .notice ul{margin:.45rem 0 0;padding-left:1.2rem;font-weight:400;line-height:1.45}
#col-notes .notice li+li{margin-top:.3rem}
.collection .ui-widgets{margin:0 0 1rem}
.collection .ui-widgets a.ext{display:inline-flex;align-items:center}
/* the results bar: the card size and Clear filters stay on phones (no Filters sheet holds them) */
.collection .spanel .rbar .dirbtn{margin:0;width:var(--ctl);height:var(--ctl);padding:0;display:inline-flex;
  align-items:center;justify-content:center;border:1px solid var(--control-border);
  border-radius:var(--radius);
  background:var(--surface);color:var(--text)}
.collection .spanel .rbar .dirbtn:hover{background:var(--surface-2)}
.collection .spanel .rbar .dirbtn svg{width:18px;height:18px;fill:none;stroke:currentColor;stroke-width:2}
.collection .spanel .rbar .sortcw select{min-width:0}
.collection .spanel .apply{margin:.5rem 0 0}
@media (max-width:599.98px){
  .collection .spanel .rbar .size,.collection .spanel .rbar .clr{display:inline-flex}
  .collection .spanel .rbar .brka{display:block}
  .collection .spanel .rbar .sort:has(.sortcw) .dirbtn{display:none}
  .collection .spanel .rbar .clr{margin-left:auto} }
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
/* two tiles a row on a narrow phone at S and M (one at L) */
@media (max-width:420px){
  html:not([data-card-size=l]) ul.collgrid{grid-template-columns:repeat(2,minmax(0,1fr))}
  ul.collgrid .cc .kebab{width:38px} }
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
/* the table (List view) */
ul.colllist{list-style:none;margin:0 0 1rem;padding:0}
ul.colllist .tr{padding:.45rem .75rem}
ul.colllist .cardc{display:flex;align-items:center;gap:.6rem;min-width:0}
ul.colllist .cardc .tx{display:flex;flex-direction:column;min-width:0}
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
/* the import's progress strip: the words take the room, Refresh keeps its own width (wraps on phone) */
.notice.importing{display:flex;flex-wrap:wrap;align-items:center;gap:.5rem .75rem}
.notice.importing .itext{flex:1 1 14rem;min-width:0}
.notice.importing .btn{flex:0 0 auto;margin:0}
ul.colllist .thumb{width:34px;height:48px;border-radius:3px;object-fit:cover;background:var(--surface-3);
  display:block}
ul.colllist .setc{min-width:0;overflow-wrap:anywhere}
ul.colllist .qc{justify-self:start}
ul.colllist .th .qc{justify-self:start}
@media (max-width:899.98px){ ul.colllist .cardc .lg{display:none} }
@media (max-width:599.98px){
  ul.colllist .tr.row{grid-template-rows:auto auto;align-items:start}
  ul.colllist .cardc{grid-column:1;grid-row:1}
  ul.colllist .act{grid-column:2;grid-row:1}
  ul.colllist .qc{grid-column:1 / span 2;grid-row:2;margin-top:.35rem;
    padding-left:calc(34px * var(--thumb-scale,1) + .6rem)}
  ul.colllist .sub{display:none} ul.colllist .smline{display:block} }
.loadmore{display:flex;flex-direction:column;align-items:center;gap:.4rem;margin:.5rem 0 1.25rem}
.loadmore .btn{margin:0;min-width:12rem}
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
.csvhelp th,.csvhelp td{text-align:left;vertical-align:top;padding:.4rem .5rem;overflow-wrap:anywhere;
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

        raw = await read_limited(request, 400_000)  # an import pastes a CSV of up to 100 rows
        if raw is None:
            return {}
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}

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

    # -- an import, in the background -------------------------------------------------------------
    # One import per member at a time, kept in memory by a random id (a restart forgets it; the
    # cards it added stay on Archidekt). The page polls /collection/api/import/{id} for its strip.
    imports: dict[str, dict[str, Any]] = {}
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
        done, of = job["done"], job["of"]
        if job["state"] == "paused":
            return (
                f"Paused at {done} of {of} lines: your account has used most of its Archidekt actions "
                "for now. The import goes on by itself when there is room, and keeps some back for "
                "your own changes."
            )
        if job["state"] in ("done", "failed"):
            return f"The import has ended: {done} of {of} lines done."
        return (
            f"Importing your list into your binder: {done} of {of} lines done. "
            "You can leave this page; the import goes on."
        )

    def import_strip(job: dict[str, Any]) -> str:
        jid = _esc(job["id"])
        return (
            f"<div class='notice ok importing' role='status' data-import='{jid}'>"
            f"<span class='ui-spin' aria-hidden='true'></span><span class='itext'>{_esc(import_text(job))}"
            f"</span> {ui.button_link('Refresh', f'/collection?import={jid}', icon_name='refresh')}</div>"
        )

    def start_import(sub: str, items: list[dict[str, Any]]) -> dict[str, Any]:
        running = imports.get(sub)
        if running is not None and running["state"] in ("running", "paused"):
            return running
        if len(imports) >= IMPORT_JOBS_MAX:
            for k in [k for k, j in imports.items() if j["state"] in ("done", "failed")]:
                imports.pop(k, None)
        job = {"id": secrets.token_urlsafe(9), "state": "running", "done": 0, "of": len(items), "next": ""}
        imports[sub] = job
        task = asyncio.create_task(import_job(sub, items, job))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return job

    async def import_job(sub: str, items: list[dict[str, Any]], job: dict[str, Any]) -> None:
        """Add ``items`` a card at a time (service.add), counting lines done. While the member's
        Archidekt budget is below a quarter of it the job waits (paused), so the member's own
        changes still go through; a budget refusal that slips past is waited out the same way
        (nothing was sent). Ends with the page's import notice, or what went in before a stop."""

        def low() -> bool:
            budget = service.decks.archidekt_budget
            return budget.room(sub) < budget.per_window // 4 + 4  # a card costs up to three calls

        async def room() -> None:
            while low():
                job["state"] = "paused"
                await asyncio.sleep(IMPORT_PAUSE_POLL)
            job["state"] = "running"

        async def wait(_exc: CollectionError) -> bool:
            job["state"] = "paused"
            await asyncio.sleep(IMPORT_PAUSE_POLL)
            await room()
            return True

        def counted(n: int) -> None:
            job["done"] = n

        out: dict[str, Any] = {}
        try:
            await service.add(
                sub, items, source="manual", result=out, on_item=counted, retry=wait, before_item=room
            )
        except Exception as exc:  # anything: the strip must end, and say what went in
            if not isinstance(exc, CollectionError):
                log.exception("collection import failed")
            reason = (
                str(exc)
                if isinstance(exc, CollectionError)
                and exc.kind not in ("unavailable", "rate_limited", "busy")
                else "Archidekt is not answering right now"
            )
            result = import_result(items[: job["done"]], out)
            result.update(stopped=reason, stopped_at=job["done"] + 1, of=job["of"])
            job["next"] = "/collection?err=imported&r=" + keep_result(sub, result)
            job["state"] = "failed"
            return
        code = "ok=imported" if out.get("added") else "err=nothingimported"
        job["next"] = f"/collection?{code}&r=" + keep_result(sub, import_result(items, out))
        job["state"] = "done"

    def strip(text: str, kind: str, *, html: bool = False) -> str:
        """A notice strip at the top of the page, with a × to close (the page's script hides it)."""
        x = (
            "<button type='button' class='nx' aria-label='Close' title='Close' data-dismiss>"
            f"{icon('x')}</button>"
        )
        return ui.notice(
            x + (text if html else _esc(text)), kind, html=True, role="alert" if kind == "error" else "status"
        )

    def notice(code: str | None, *, sub: str = "", token: str | None = None) -> str:
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
            "toomany": f"An import adds at most {MAX_IMPORT_ROWS} rows at a time. Split the file and "
            "import the rest after.",
            "unreadable": "That text is not a collection CSV or a card list the gateway can read.",
            "expired": "This form expired. Reload the page and try again.",
            "invalid": "That was not a valid request.",
            "lookup": "Archidekt is not answering right now. Try again later.",
            "link": "Link your Archidekt account on the Account page first.",
            "writes": refusals.WRITES_OFF,
        }
        if not code or code not in msgs:
            return ""
        return strip(msgs[code], "ok" if code in ("added", "removed", "updated", "imported") else "error")

    def head(actions_html: str = "") -> str:
        # Binder's switch (Cards | Wants | Watchlist) names the page
        return ui.binder_head("/collection", "Binder: Cards", PURPOSE, actions_html=actions_html)

    def problem(exc: CollectionError) -> str:
        if exc.kind in ("not_linked", "auth"):
            return (
                "<section class='panel coll-empty'><h2>Your binder lives on Archidekt</h2>"
                "<p>Link your Archidekt account to see it here. Your cards stay on Archidekt (your "
                "Archidekt collection).</p><div class='actions'>"
                + ui.button_link(
                    "Link Archidekt", "/account/details#archidekt", kind="primary", icon_name="account"
                )
                + "</div></section>"
            )
        return (
            f"<div id='col-notes'>{strip(str(exc), 'error')}</div>"
            "<section class='panel'><p class='muted'>Your binder is read live from Archidekt; when "
            "Archidekt is slow or down the page cannot show it.</p></section>"
        )

    def listing_query(qp: Any) -> dict[str, Any]:
        """The list's address: name filter, sort and direction, offset (the old ``page`` too)."""
        q = _clean(qp.get("q"), 80)
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
        return {"q": q, "sort": sort, "dir": direction, "offset": offset}

    def link_to(lq: dict[str, Any], view: str | None, offset: int, *, api: bool = False) -> str:
        from urllib.parse import urlencode

        qs: dict[str, Any] = {}
        if lq["q"]:
            qs["q"] = lq["q"]
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
            return login_redirect("/collection")
        qp = request.query_params
        lq = listing_query(qp)
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
        notes = notice(result, sub=sub, token=qp.get("r"))
        # prices kept: opening the Binder puts it in the daily price check (binder_prices)
        if priced():
            service.binder.seen(sub, _price_clock())
            value = service.binder.value(sub)
            reading = sub in first_reads
            if lq["sort"] == "price" and value is None:
                notes += strip(
                    "Sorting by price needs your binder's value, worked out with the daily price "
                    "check. Until then, cards are shown in the order you added them.",
                    "ok",
                )
        else:
            value, reading = None, False
        if qp.get("import"):
            job = imports.get(sub)
            if job is not None and job["id"] == qp.get("import"):
                if job["state"] in ("done", "failed") and job["next"]:
                    return RedirectResponse(job["next"], status_code=303)
                notes = import_strip(job) + notes
            else:
                notes = (
                    strip(
                        "That import is no longer running. The cards it added are in your binder.",
                        "ok",
                    )
                    + notes
                )
        # the profile (the Visibility line by the title) is read beside the collection, not after
        # it: one wait on Archidekt for the page, not two in a row
        profile_read = asyncio.ensure_future(profile_or_none(state.decks, sub))
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
            if (plan := busy_plan(exc)) is not None:
                # the Binder keeps its title and notes; the busy words sit where the list goes
                body = head() + notes_html + f"<h2 class='busy-title'>{_esc(plan.title)}</h2>"
                busy = page(
                    "Binder",
                    body + busy_body(plan, same_url(request)),
                    sub=sub,
                    sid=sid,
                    status=plan.status,
                    heading=False,
                )
                busy.headers.update(busy_headers(plan))
                return busy
            status = 200 if exc.kind in ("not_linked", "auth") else _STATUS.get(exc.kind, 400)
            return page(
                "Binder",
                head() + notes_html + problem(exc),
                sub=sub,
                sid=sid,
                status=status,
                heading=False,
            )
        rows = got["rows"]
        link = state.db.get_link(sub) or {}
        arch_user = str(link.get("archidekt_user_id") or "")
        live = "<p class='sr-only' id='live' role='status' aria-live='polite'></p>"
        details_row: dict[str, Any] | None = None
        if qp.get("details"):
            try:
                rid = _rid(qp.get("details"))
                details_row = next((r for r in rows if int(r["id"]) == rid), None)
                if details_row is None:
                    details_row = row_out(await service._record(sub, rid))
            except CollectionError:
                details_row = None
        details_box = details_box_html(details_row, csrf, open=details_row is not None)
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
        if not rows and not lq["q"] and not lq["offset"]:
            body = (
                head()
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
            )
        else:
            acts = ui.button_link(
                "Export CSV", "/collection/export.csv", icon_name="download"
            ) + ui.box_opener("Add cards", "/collection?add=1", "col-add", kind="primary", icon_name="plus")
            cards = (
                list_html(rows, csrf, view=view)
                if rows
                else (
                    f"<ul class='{'cardgrid collgrid' if view == 'grid' else 'ui-table colllist'}' "
                    "id='cards' hidden></ul>"
                )
            )
            body = (
                head(acts)
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
                )
                + live
                + cards
                + f"<p class='muted col-nomatch'{' hidden' if rows else ''}>No cards match that filter.</p>"
                + more_html(lq, view_in_link, got)
                + export_help_html()
                + add_box
                + details_box
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
        lq = listing_query(qp)
        view = qp.get("view") if qp.get("view") in ("grid", "list") else "grid"
        try:
            got = await listing(sub, lq)
        except CollectionError as exc:
            return _fail(exc)
        csrf = _csrf(s, sid) or ""
        html = "".join(row_html(r, csrf, view=view) for r in got["rows"])
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
            return login_redirect("/collection")
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
                job = start_import(sub, items)
                return RedirectResponse(f"/collection?import={job['id']}", status_code=303)
            elif action == "details":
                rid = _rid(data.get("id"))
                await service.update(
                    sub,
                    rid,
                    {
                        "finish": data.get("finish", "nonfoil"),
                        "condition": data.get("condition", ""),
                        "language": data.get("language", "EN"),
                        "purchase_price": data.get("purchase_price", ""),
                    },
                    name=_clean(data.get("name"), 200),
                )
                code = "ok=updated"
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
            return login_redirect("/collection")
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
                    raise CollectionError("invalid", "delta must be a whole number")
                out = await service.set_quantity(who[0], rid, delta=delta, name=name)
            elif "quantity" in data:
                qty = data.get("quantity")
                if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0 or qty > MAX_QUANTITY:
                    raise CollectionError("invalid", "quantity must be a whole number")
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
        job = imports.get(who[0])
        if job is None or job["id"] != request.path_params["jid"]:
            return JSONResponse({"ok": False, "error": "not_found", "message": "no such import"}, 404)
        return JSONResponse(
            {
                "ok": True,
                "state": job["state"],
                "done": job["done"],
                "of": job["of"],
                "text": import_text(job),
                "next": job["next"],
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
            "The signed-in user's binder, their Archidekt collection (the account they linked): the "
            "cards they own, one row per printing and finish with a quantity, 100 a page in Archidekt's "
            "newest-first order. Optional `query` filters by card name; `sort` is added, edition "
            "(set release) or price (highest first, from the binder as the daily price check last "
            "read it, with each card's price; only once the member opened their Binder); `page` pages. "
            "Returns cards, count and total_pages. Read-only."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def list_collection(query: str | None = None, sort: str = "added", page: int = 1) -> dict[str, Any]:
        sub = _sub()
        page_no = max(1, min(int(page or 1), 10_000))
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
            raise CollectionError("invalid", "remove must be a list of up to 100 entries")
        out: list[dict[str, Any]] = []
        for entry in remove:
            if isinstance(entry, str):
                entry = {"name": entry}
            if not isinstance(entry, dict):
                raise CollectionError("invalid", "each remove entry is {id | name, quantity?}")
            qty = entry.get("quantity")
            if qty is not None and (not isinstance(qty, int) or isinstance(qty, bool) or qty < 1):
                raise CollectionError("invalid", "quantity must be a whole number of 1 or more")
            if entry.get("id") is not None and str(entry["id"]).strip():
                rec = row_out(await service._record(sub, _rid(entry["id"]), _clean(entry.get("name"), 200)))
                rows = [rec]
            elif entry.get("name"):
                rows = await service.find_by_name(sub, str(entry["name"]))
                if not rows:
                    raise CollectionError(
                        "not_found", f"no card named '{_clean(str(entry['name']), 60)}' in your binder"
                    )
            else:
                raise CollectionError("invalid", "each remove entry needs id or name")
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

    async def apply_changes(sub: str, changes: dict[str, Any], progress: dict[str, Any]) -> dict[str, Any]:
        """Apply an approved ``collection`` proposal: the stored adds go through service.add (the
        same path as the page), the stored removals by record id. A scan the adds came from is
        removed once every card went in. Called by DeckService under its Archidekt slot."""
        adds = changes.get("add") or []
        result: dict[str, Any] = {"added": [], "skipped": [], "removed": []}
        expected: dict[Any, int | None] = {}  # record id -> copies it should now hold (None: gone)
        # each record written counts one (CollectionService._run), of every add and removal
        progress["of_entries"] = len(adds) + len(changes.get("remove") or [])
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
        except CollectionError as exc:
            raise DeckError(exc.kind, str(exc), retry_after=archidekt_wait(exc)) from exc
        result["verified"], result["mismatches"] = await _read_back(sub, expected)
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
            "Propose adding cards to, or removing cards from, the user's binder (their Archidekt "
            "collection). Like a deck edit this is a proposal: show its diff and review_url; it is "
            "applied by the user "
            "(Apply on the proposal card or the review page) or by you when assistant_may_apply is "
            "true, with apply_proposal. add: a list of {name, set_code?, collector_number?, quantity?, "
            "finish?, condition?, language?} or plain names (a card named without set_code and "
            "collector_number is added in a printing Archidekt picks, as a nonfoil unless finish says "
            "otherwise; language: the card's language code, EN default, or ES, FR, DE, IT, PT, JA, KO, "
            "RU, ZHS, ZHT); text: one "
            "card per line, e.g. '2 Sol Ring (CMR) 472 *F*'; scan_session: the id or name of a scan "
            "session, every matched card in it is added and the session is removed once applied; remove: "
            "a list of {id (from list_collection) | name, quantity?} (no quantity removes every copy). At "
            "most 100 cards each way per proposal. Once applied, each row of the result's added and "
            "removed lists has quantity (the copies the record holds now, 0 when it is gone), "
            "previous_quantity, and added_quantity or removed_quantity (what this proposal changed)."
        ),
        annotations={"readOnlyHint": True, "destructiveHint": False, "openWorldHint": True},
        meta=dict(CARD_TOOL_META) if in_chat else None,
    )
    async def propose_collection_changes(
        add: Annotated[list[CollectionAdd | str] | None, Field(description="Cards to add.")] = None,
        text: Annotated[str | None, Field(description="Or cards to add as text, one per line.")] = None,
        scan_session: Annotated[
            str | None, Field(description="Or a scan session's id or name to add.")
        ] = None,
        remove: Annotated[list[CollectionRemove | str] | None, Field(description="Cards to remove.")] = None,
    ) -> Any:
        sub = _sub()
        items: list[Any] = [card_aliases(dumped(a)) for a in add or []]
        remove = [dumped(r) for r in remove] if remove is not None else None
        source = "assistant"
        session_id = None
        warnings: list[str] = []
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
                    raise CollectionError("unavailable", "scanning is not available on this gateway")
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
        except CollectionError as exc:
            return _result(_err(exc))
        warnings += await _finish_warnings(adds, service.scan)
        try:
            # the source and scan id travel with the proposal so the apply can drop the scan
            p = await service.decks.propose_collection(
                sub, adds, removals, extra={"source": source, "scan_session_id": session_id}
            )
        except DeckError as exc:
            return _result({"ok": False, "error": exc.kind, "message": str(exc), **exc.extra})
        made = {"ok": True, **p, **({"warnings": warnings} if warnings else {})}
        return _result(made)
