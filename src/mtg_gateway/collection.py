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

import csv
import io
import json
import re
import secrets
import time
from typing import TYPE_CHECKING, Annotated, Any

from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from .approve import CARD_TOOL_META, proposal_tool_result
from .archidekt import (
    COLLECTION_PAGE_SIZE,
    ArchidektError,
    _faces,
    _oracle_text,
    _pt,
    condition_code,
    language_code,
)
from .busy import busy_response
from .deckpage import image_url, mana_html
from .decks import DeckError, _writing, current_client
from .pages import (
    NO_STORE,
    _csrf,
    _safe_next,
    browser_session,
    csrf_form_ok,
    csrf_header_ok,
    login_redirect,
    read_limited,
)
from .schemas import CollectionAdd, CollectionRemove, card_aliases, dumped
from .theme import display_name, icon, remember_view, render, stylesheet_link, view_choice
from .theme import esc as _esc

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .decks import DeckService
    from .scan.service import ScanService

MAX_ITEMS_PER_CALL = 100  # each card costs one or two Archidekt calls, paced about a second apart
MAX_QUANTITY = 9_999
MAX_PAGES_FOR_EXPORT = 50  # 5,000 records
PAGE_SIZE = COLLECTION_PAGE_SIZE
FIRST_PAGE_TTL = 60.0  # seconds the unfiltered first page is served from memory
FINISHES = ("nonfoil", "foil", "etched")
MODIFIERS = {"nonfoil": "Normal", "foil": "Foil", "etched": "Etched"}
CONDITIONS = ("", "NM", "LP", "MP", "HP", "DMG")
# Archidekt's own collection orderings the gateway exposes (``collectionOrderBy``; only
# ``editionDate`` was seen in the site's requests, the default is Archidekt's newest-first).
SORTS = {"added": "Recently added", "edition": "Set release"}
COLLECTION_CSP = (
    "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; "
    "manifest-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
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
        return CollectionError(kind, str(exc), retry_after=getattr(exc, "retry_after", None))
    raise exc


def _finish_of(modifier: Any) -> str:
    m = str(modifier or "Normal").strip().lower()
    return "foil" if m == "foil" else "etched" if m == "etched" else "nonfoil"


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
    is_csv = "name" in first or "card name" in first
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
                "check for a quote that is not closed",
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
                "invalid", f"line {n}: quantity {_quoted(qty)} must be from 1 to {MAX_QUANTITY}"
            )
        finish_raw = cell(row, "finish").lower()
        finish = {"foil": "foil", "etched": "etched", "true": "foil", "yes": "foil"}.get(
            finish_raw, "nonfoil"
        )
        condition = cell(row, "condition").upper()
        if condition == "D":  # Archidekt's own code for Damaged
            condition = "DMG"
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
        decks.write_hooks.append(self._on_write)

    def _on_write(self, sub: str, path: str) -> None:
        if not path or path.startswith("/collection"):
            self._first_pages.pop(sub, None)

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
        that changed something never goes back to pending (Q78)."""
        try:
            out = await _writing(progress, self.decks._call(sub, fn))
        except (DeckError, ArchidektError) as exc:
            raise _wrap(exc) from exc
        if progress is not None:
            progress["sent_entries"] = int(progress.get("sent_entries") or 0) + 1
        return out

    # -- reads -----------------------------------------------------------------------------------
    async def page(
        self, sub: str, *, page: int = 1, q: str = "", sort: str = "added", page_size: int = PAGE_SIZE
    ) -> dict[str, Any]:
        """One page of the collection as Archidekt orders and filters it (``cardName`` is a name
        substring). Returns rows, count, page, total_pages and has_next."""
        uid = self._user_id(sub)
        cacheable = page == 1 and not q and page_size == PAGE_SIZE and self.first_page_ttl > 0
        if cacheable:
            hit = self._first_pages.get(sub, {}).get(sort)
            if hit is not None and time.monotonic() - hit[0] < self.first_page_ttl:
                return {**hit[1], "rows": list(hit[1]["rows"])}
        order = "editionDate" if sort == "edition" else ""
        body = await self._run(
            sub,
            lambda token: self.client.collection_page(
                token, uid, page=page, page_size=page_size, card_name=q, order_by=order
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
            self._first_pages.setdefault(sub, {})[sort] = (time.monotonic(), out)
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
        self, sub: str, items: Any, *, source: str, progress: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Add cards to the member's Archidekt Collection. Each item names a printing by
        ``scryfall_id`` (a scan result) or by ``name`` with optional ``set`` and
        ``collector_number``; ``quantity``, ``finish`` (or ``foil: true``), ``condition`` and
        ``language`` (English when not given; a new record only) are optional. A printing
        already in the collection in that finish gets its copies added to the existing record
        (whatever its language, as the site's own add is thought to do, unchecked; ``merged``
        names each such item whose language differs from the record's, with ``index`` its
        position in ``items``, ``language`` and ``existing_language``); otherwise a record is
        created. ``skipped`` items carry ``index`` too. ``progress``: a collection proposal's
        apply, which counts each record written (``_run``)."""
        self._writes_on()
        wanted = [
            (it, {k: it[k] for k in ("quantity", "finish", "condition", "language")})
            for it in normalise_items(items)
        ]
        self._user_id(sub)  # fail early when no account is linked
        existing: dict[tuple[Any, str], dict[str, Any]] = {}
        # the language of each record ``existing`` holds, as the gateway's code (a record written
        # here keeps Archidekt's own shape, so its language is kept apart)
        lang_of: dict[tuple[Any, str], str] = {}
        for rec in await self._raw_rows(sub, max_pages=10):
            card = rec.get("card") if isinstance(rec.get("card"), dict) else {}
            key = (card.get("id"), _finish_of(rec.get("modifier")))
            existing[key] = rec
            lang_of[key] = language_code(rec.get("language")) or "EN"
        added: list[dict[str, Any]] = []
        skipped: list[dict[str, Any]] = []
        merged: list[dict[str, Any]] = []
        for index, (spec, opts) in enumerate(wanted):
            try:
                printing = await self._run(
                    sub,
                    lambda token, spec=spec: self.client.resolve_card(
                        token,
                        spec["name"],
                        set_code=spec["set"] or None,
                        collector_number=spec["collector_number"] or None,
                        scryfall_id=spec["scryfall_id"] or None,
                    ),
                )
            except CollectionError as exc:
                if exc.kind in ("not_found", "contract", "invalid", "unavailable"):
                    skipped.append({"input": spec, "status": "not_found", "note": str(exc), "index": index})
                    continue
                raise
            finish = opts["finish"]
            options = printing.get("options") or []
            if options and MODIFIERS[finish] not in options and len(options) == 1:
                finish = _finish_of(options[0])  # a printing sold in one finish is that finish
            key = (printing["id"], finish)
            have = existing.get(key)
            if have is not None:
                if lang_of.get(key, "EN") != opts["language"]:
                    merged.append(
                        {
                            "input": spec,
                            "index": index,
                            "language": opts["language"],
                            "existing_language": lang_of.get(key, "EN"),
                        }
                    )
                qty = min(MAX_QUANTITY, int(have.get("quantity") or 0) + opts["quantity"])
                out = await self._run(
                    sub,
                    lambda token, have=have, qty=qty: self.client.collection_set(token, have, quantity=qty),
                    progress=progress,
                )
                rec = {**have, **out, "card": have.get("card")}
            else:
                out = await self._run(
                    sub,
                    lambda token, printing=printing, finish=finish, opts=opts: self.client.collection_add(
                        token,
                        printing["id"],
                        opts["quantity"],
                        modifier=MODIFIERS[finish],
                        # with none picked, English and Near Mint, as archidekt.com's own new
                        # record (its site code; Q60)
                        condition=opts["condition"] or "NM",
                        language=opts["language"],
                    ),
                    progress=progress,
                )
                rec = {**out, "modifier": MODIFIERS[finish], "card": self._card_stub(printing, spec, out)}
                lang_of[key] = opts["language"]
            existing[key] = rec
            row = row_out(rec)
            if spec["scryfall_id"] and not row.get("scryfall_id"):
                row["scryfall_id"] = spec["scryfall_id"]
                row["image_small"] = image_url(spec["scryfall_id"], "small")
                row["image"] = image_url(spec["scryfall_id"], "normal")
            added.append(row)
        self.decks.db.audit(
            "collection_added",
            sub=sub,
            detail={"rows": len(added), "cards": sum(int(r["quantity"]) for r in added), "source": source},
        )
        return {"added": added, "skipped": skipped, "merged": merged}

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

    async def _record(self, sub: str, rid: int) -> dict[str, Any]:
        """The raw record for an id, found by paging the collection (Archidekt's v2 list has no
        single-record read). Up to ten pages."""
        uid = self._user_id(sub)
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
        raise CollectionError("not_found", "no such card in your collection")

    async def set_quantity(self, sub: str, rid: int, quantity: int) -> dict[str, Any]:
        self._writes_on()
        rec = await self._record(sub, rid)
        if quantity <= 0:
            await self._run(sub, lambda token: self.client.collection_delete(token, [rid]))
            self.decks.db.audit("collection_removed", sub=sub, detail={"id": rid})
            return {"row": None, "removed": row_out(rec)}
        out = await self._run(sub, lambda token: self.client.collection_set(token, rec, quantity=quantity))
        return {"row": row_out({**rec, **out, "card": rec.get("card")})}

    async def update(self, sub: str, rid: int, data: dict[str, Any]) -> dict[str, Any]:
        """Change a record's details: finish, condition, language and price paid (the fields
        Archidekt's own row editor offers; tags are read-only here)."""
        self._writes_on()
        rec = await self._record(sub, rid)
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
        self, sub: str, rid: int, quantity: int | None = None, *, progress: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self._writes_on()
        rec = await self._record(sub, rid)
        have = int(rec.get("quantity") or 0)
        if quantity is None or quantity >= have:
            await self._run(sub, lambda token: self.client.collection_delete(token, [rid]), progress=progress)
            left = None
        else:
            out = await self._run(
                sub,
                lambda token: self.client.collection_set(token, rec, quantity=have - max(1, quantity)),
                progress=progress,
            )
            left = row_out({**rec, **out, "card": rec.get("card")})
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
    English, kept as Near Mint, merged into a record of another language, or not found, each with
    where it was)."""

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
    merged = [
        {
            "where": where(int(m["index"])),
            "name": name(int(m["index"])),
            "language": _language_name(str(m.get("language") or "EN")),
            "existing": _language_name(str(m.get("existing_language") or "EN")),
        }
        for m in out.get("merged") or []
        if isinstance(m.get("index"), int)
    ]
    return {
        "cards": sum(int(it.get("quantity") or 0) for i, it in enumerate(items) if i not in missing),
        "added": bool(out.get("added")),
        "kept": [{"language": k, "rows": v} for k, v in kept.items()],
        "kept_condition": [{"condition": k, "rows": v} for k, v in near_mint.items()],
        "merged": merged,
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
    merged = result.get("merged") or []
    if merged:
        langs = {m["existing"] for m in merged}
        one = len(merged) == 1
        count_as = (
            f"{'it counts' if one else 'they count'} as {next(iter(langs))}"
            if len(langs) == 1
            else "each counts in that copy's language"
        )
        entries = [{**m, "how": f"{m['language']} added to {m['existing']}"} for m in merged]
        notes.append(
            f"Added to a copy you already had in another language, so {count_as}: {rows(entries, 'how')}."
        )
    if result.get("missing"):
        notes.append(f"Not found, so not imported: {rows(result['missing'])}.")
    if result.get("added"):
        n = int(result.get("cards") or 0)
        lead, cls = f"Imported {n} {'card' if n == 1 else 'cards'} into your Archidekt collection.", "ok"
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
        f"<details class='addfold csvhelp'><summary>{icon('download')} "
        "<span class='t'>Importing the CSV on archidekt.com</span>"
        "<span class='chev'>column types</span></summary><section class='panel'>"
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
        + (f" data-price='{float(r['price']):.2f}'" if isinstance(r.get("price"), (int, float)) else "")
    )


def row_html(r: dict[str, Any], csrf: str, *, view: str) -> str:
    img = r.get("image_small") or ""
    rid = _esc(r["id"])
    attrs = _view_attrs(r)
    finish = _finish_label(r["finish"])
    badges = (f"<span class='finish' title='{_esc(finish)}'>{finish[:1]}</span>" if finish else "") + (
        f"<span class='pill cond'>{_esc(r['condition'])}</span>" if r.get("condition") else ""
    )
    csrf_in = (
        f"<input type='hidden' name='csrf' value='{_esc(csrf)}'><input type='hidden' name='id' value='{rid}'>"
    )
    stepper = (
        f"<form method='post' action='/collection' class='qty' data-id='{rid}'>{csrf_in}"
        "<button name='action' value='dec' class='mini' aria-label='One "
        f"fewer {_esc(r['name'])}' title='One fewer'>{icon('minus')}</button>"
        f"<output aria-label='Copies'>{int(r['quantity'])}</output>"
        "<button name='action' value='inc' class='mini' aria-label='One "
        f"more {_esc(r['name'])}' title='One more'>{icon('plus')}</button>"
        "</form>"
    )
    remove = (
        f"<form method='post' action='/collection' class='rm'>{csrf_in}"
        "<button name='action' value='remove' class='mini "
        f"icon-only' aria-label='Remove {_esc(r['name'])} from your "
        f"collection' title='Remove'>{icon('x')}</button></form>"
    )
    set_line = f"{_esc((r.get('set') or '').upper())} {_esc(r.get('collector_number'))}"
    details = details_html(r, csrf_in)
    if view == "grid":
        body = (
            f"<img src='{_esc(img)}' alt='{_esc(r['name'])}' loading='lazy'>"
            if img
            else f"<span class='ph'><span class='t'><span class='nm'>{_esc(r['name'])}</span></span></span>"
        )
        return (
            f"<li class='c col' data-name='{_esc(r['name'].lower())}' data-id='{rid}'{attrs}>"
            f"<button type='button' class='pic thumbbtn' aria-label='Show {_esc(r['name'])}' "
            f"title='Show the card'>{body}"
            f"<span class='qty'>{int(r['quantity'])}</span>{badges}</button>"
            f"<div class='cap'><span class='name' title='{_esc(r['name'])}'>{_esc(r['name'])}</span><span "
            f"class='set'>{set_line}</span></div>"
            f"<div class='act'>{stepper}{details}{remove}</div></li>"
        )
    return (
        f"<li class='row col' data-name='{_esc(r['name'].lower())}' data-id='{rid}'{attrs}>"
        f"<button type='button' class='thumbbtn' aria-label='Show {_esc(r['name'])}' title='Show the card'>"
        + (
            f"<img class='thumb' src='{_esc(img)}' alt='' loading='lazy'>"
            if img
            else "<span class='thumb'></span>"
        )
        + "</button>"
        + f"<span class='n'><span class='name' title='{_esc(r['name'])}'>{_esc(r['name'])}{badges}</span>"
        f"<span class='meta'>{set_line}"
        + (f" · {_esc(r.get('set_name'))}" if r.get("set_name") else "")
        + (f" · {_esc(r.get('type_line'))}" if r.get("type_line") else "")
        + "</span></span>"
        f"<span class='mc'>{mana_html(r.get('mana_cost') or '')}</span>{stepper}{details}{remove}</li>"
    )


def details_html(r: dict[str, Any], csrf_in: str) -> str:
    """A row's details menu: the finish, condition, language and price paid, saved to Archidekt
    on the member's click (tags are shown as Archidekt holds them)."""
    rid = _esc(r["id"])
    finish = r.get("finish") or "nonfoil"
    cond = (r.get("condition") or "").upper()
    lang = (r.get("lang") or "").upper()
    price = r.get("purchase_price")
    price_val = (
        "" if price is None else f"{float(price):.2f}" if isinstance(price, (int, float)) else _esc(price)
    )

    def opts(values: tuple[str, ...], current: str, labels: dict[str, str] | None = None) -> str:
        return "".join(
            f"<option value='{_esc(v)}'{' selected' if v == current else ''}>{_esc((labels or {}).get(v, v))}"
            "</option>"
            for v in values
        )

    langs = LANGUAGES if not lang or lang in LANGUAGES else (lang, *LANGUAGES)
    tags = "".join(f"<span class='pill'>{_esc(t)}</span>" for t in (r.get("tags") or [])[:8])
    return (
        f"<details class='dd rowmenu details'><summary class='btn mini' aria-label='Details of "
        f"{_esc(r['name'])}' title='Details'>{icon('more')}</summary>"
        f"<form method='post' action='/collection' class='menu detailsform' data-id='{rid}'>"
        f"{csrf_in}<input type='hidden' name='action' value='details'>"
        f"<div class='head'>{_esc(r['name'])}</div>"
        f"<label class='field'><span>Finish</span><select name='finish'>"
        f"{opts(FINISHES, finish, {'nonfoil': 'Normal', 'foil': 'Foil', 'etched': 'Etched'})}</select>"
        "</label>"
        f"<label class='field'><span>Condition</span><select name='condition'>"
        f"{opts(CONDITIONS, cond, {'': 'Not set'})}</select></label>"
        f"<label class='field'><span>Language</span><select name='language'>{opts(langs, lang or 'EN')}"
        "</select></label>"
        f"<label class='field'><span>Price paid</span><input type='number' name='purchase_price' min='0' "
        f"max='{int(MAX_PRICE)}' step='0.01' inputmode='decimal' value='{price_val}' placeholder='none'>"
        "</label>"
        + (
            f"<div class='field tags'><span>Tags</span><span class='tagline'>{tags}</span></div>"
            if tags
            else ""
        )
        + f"<div class='actions'><button class='primary'>{icon('check')} Save</button></div></form></details>"
    )


COLLECTION_CSS = """
.notice.importres{overflow-wrap:anywhere}
.notice.importres p{margin:0}
.notice.importres ul{margin:.45rem 0 0;padding-left:1.2rem;font-weight:400;line-height:1.45}
.notice.importres li+li{margin-top:.3rem}
.collbar .controls{display:grid;grid-template-columns:minmax(0,2fr) repeat(2,minmax(9rem,1fr)) auto;gap:1rem;
  align-items:end}
.collbar .field .btn{margin:0}
@media (max-width:1000px){ .collbar .controls{grid-template-columns:1fr 1fr}
  .collbar .filter{grid-column:1 / -1} }
@media (max-width:600px){ .collbar .controls{grid-template-columns:1fr} }
.coll-totals{display:flex;flex-wrap:wrap;gap:1rem 1.5rem;align-items:baseline;margin:0 0 .75rem;
  color:var(--text-muted)}
.coll-totals b{color:var(--text);font-size:1.3rem;font-variant-numeric:tabular-nums}
.coll-totals .ext{margin-left:auto}
.addbox form.addcard{display:grid;grid-template-columns:minmax(0,2fr) 5.5rem minmax(0,1fr) minmax(0,1fr) auto;
  gap:.5rem;align-items:end}
.addbox form.addcard .field{margin:0} .addbox form.addcard button{margin:0;height:var(--ctl)}
.importbox textarea{width:100%;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.9rem}
.importbox .actions{margin-top:.5rem}
@media (max-width:600px){ .addbox form.addcard{grid-template-columns:1fr 1fr}
  .addbox form.addcard .grow,.addbox form.addcard button{grid-column:1 / -1} }
/* A card is at least 170px wide so its controls row (minus, count, plus, Details, remove:
   about 158px) always fits inside it instead of touching the next card; with large text it
   grows with the text (12rem), never wider than the list, and the row wraps if it must. */
ul.collgrid{list-style:none;margin:0;padding:0;display:grid;
  grid-template-columns:repeat(auto-fill,minmax(min(100%,max(170px,12rem)),1fr));gap:1rem}
ul.collgrid .col .pic{position:relative;display:block;width:100%;box-sizing:border-box;aspect-ratio:5/7;
  border-radius:4.5%;
  overflow:hidden;
  background:var(--surface-2);
  border:2px solid var(--card-border)}
ul.collgrid .col .pic img{width:100%;height:100%;display:block;object-fit:cover}
ul.collgrid .col .pic .qty{position:absolute;top:0;left:0;width:38px;height:38px;clip-path:polygon(0 0,
  0 100%,100% 0);
  border-radius:11px 0 0 0;background:#3a3a3a;color:#fff;font-size:12px;font-weight:700;padding:5px 0 0 7px}
ul.collgrid .col .pic .ph{position:absolute;inset:0;display:flex;padding:.5rem .5rem .5rem 2.6rem;
  color:var(--text);font-size:.85rem;line-height:1.2}
ul.collgrid .col .pic .ph .nm{font-weight:700;overflow-wrap:anywhere}
ul.collgrid .col .pic .finish{position:absolute;right:6px;bottom:6px}
ul.collgrid .col .pic .cond{position:absolute;left:6px;bottom:6px;font-size:.7rem}
ul.collgrid .col .cap{display:flex;flex-direction:column;margin:.35rem 0 .25rem;min-width:0}
ul.collgrid .col .cap .name{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
ul.collgrid .col .cap .set{font-size:.8rem;color:var(--text-muted)}
ul.collgrid .col .act,ul.colllist .act{display:flex;flex-wrap:wrap;align-items:center;gap:.25rem;min-width:0}
form.qty{display:inline-flex;align-items:center;gap:.15rem;margin:0}
form.qty button.mini,form.rm button.mini{margin:0;width:2.25rem;height:2.25rem;padding:0;display:inline-flex;
  align-items:center;justify-content:center}
form.qty output{min-width:1.5rem;text-align:center;font-weight:700;font-variant-numeric:tabular-nums}
form.rm{display:inline;margin:0 0 0 auto}
ul.colllist{list-style:none;margin:0;padding:0}
ul.colllist .row{display:grid;grid-template-columns:34px minmax(0,1fr) auto auto auto auto;gap:.6rem;
  align-items:center;
  padding:.4rem 0;border-top:1px solid var(--border)}
ul.colllist .thumb{width:34px;height:48px;border-radius:3px;object-fit:cover;background:var(--surface-3);
  display:block}
ul.colllist .n{display:flex;flex-direction:column;min-width:0}
ul.colllist .n .name{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
ul.colllist .n .meta{font-size:.8rem;color:var(--text-muted);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
/* the name line carries the finish and condition badges inline after the name */
ul.colllist .n .name{display:flex;align-items:center;gap:.35rem;min-width:0}
ul.colllist .n .name .finish,ul.colllist .n .name .cond{flex:none}
ul.colllist .n .name .cond{font-size:.72rem;padding:0 .3rem;line-height:1.4}
/* the ⋮ matches the bordered −, + and × beside it */
ul.colllist details.rowmenu summary.mini,ul.collgrid details.rowmenu summary.mini{display:inline-flex;
  align-items:center;justify-content:center;width:2.25rem;height:2.25rem;min-height:2.25rem;margin:0;padding:0;
  border:1px solid var(--border);border-radius:var(--radius);background:var(--surface);color:var(--text)}
ul.colllist details.rowmenu summary.mini:hover,ul.collgrid details.rowmenu summary.mini:hover{
  background:var(--surface-2)}
/* Add and Import fold above the list; the heading opens them */
.collection details.addfold{margin:0 0 1rem}
.collection details.addfold > summary{cursor:pointer;font-weight:700;min-height:2.5rem;display:flex;
  align-items:center;gap:.5rem;list-style:none}
.collection details.addfold > summary::-webkit-details-marker{display:none}
.collection details.addfold > summary::before{content:'\\25B8';color:var(--orange)}
.collection details.addfold[open] > summary::before{content:'\\25BE'}
.collection details.addfold > summary .chev{margin-left:auto;color:var(--text-muted);font-weight:400;
  font-size:.86rem}
details.rowmenu.details .menu{min-width:min(14rem,calc(100vw - 16px));padding:.4rem 0 .6rem}
details.rowmenu.details .menu .field{display:flex;flex-direction:column;gap:.25rem;padding:.35rem .9rem}
details.rowmenu.details .menu .field select,details.rowmenu.details .menu .field input{margin:0}
details.rowmenu.details .menu .tags .tagline{display:flex;flex-wrap:wrap;gap:.3rem}
details.rowmenu.details .menu .actions{margin:.4rem .9rem 0}
details.rowmenu.details .menu .actions button{width:100%;margin:0}
@media (max-width:600px){ ul.colllist .row{grid-template-columns:34px minmax(0,
  1fr) auto auto} ul.colllist .mc{display:none}
  ul.colllist form.rm{grid-column:4;grid-row:2;justify-self:end}
  ul.colllist details.rowmenu{grid-column:3;grid-row:2;justify-self:end}
  ul.colllist form.qty{grid-column:2;grid-row:2;flex-wrap:wrap;min-width:0}
  ul.colllist .n{grid-column:2 / span 3} }
.pager{display:flex;justify-content:center;gap:.5rem;margin:1rem 0}
.pager .btn{margin:0}
.coll-empty{text-align:center;padding:2rem 1rem}
.coll-empty .actions{justify-content:center}
/* how to import the Export CSV on archidekt.com: a two-column table in the fold */
/* with large text the title wraps, even inside "archidekt.com", rather than push the page wider */
.collection details.csvhelp > summary{flex-wrap:wrap;padding-right:.35rem}
.collection details.csvhelp > summary .t{min-width:0;overflow-wrap:anywhere}
/* on a phone the side text would wrap alone against the edge: the title says enough */
@media (max-width:600px){ .collection details.csvhelp > summary .chev{display:none} }
.csvhelp .tbl{overflow-x:auto;max-width:min(100%,40rem)}
.csvhelp table{border-collapse:collapse;width:100%;font-size:.95rem}
.csvhelp th,.csvhelp td{text-align:left;vertical-align:top;padding:.4rem .5rem;overflow-wrap:anywhere;
  border-bottom:1px solid var(--border-soft)}
.csvhelp thead th{background:var(--surface-2)}
.csvhelp tbody th{font-weight:700}
"""


def add_collection(server: MCPServer, state: AppState) -> CollectionService:
    service = CollectionService(state.decks, getattr(state, "scan", None))
    state.collection = service  # type: ignore[attr-defined]
    add_collection_routes(server, state, service)
    add_collection_tools(server, service)
    return service


def add_collection_routes(server: MCPServer, state: AppState, service: CollectionService) -> None:
    s = state.settings

    def page(
        title: str, body: str, *, sub: str, sid: str | None, status: int = 200, scripts: bool = False
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
            user=display_name(user),
            wide=True,
            body_class="collection",
            current="/collection",
            head_extra=f"{stylesheet_link('deck.css')}<style>{COLLECTION_CSS}</style>"
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

    def notice(code: str | None, *, sub: str = "", token: str | None = None) -> str:
        if code in ("imported", "nothingimported", "toomany", "unreadable") and token:
            result = take_result(sub, token)
            if result is not None:
                return import_result_html(result)
        msgs = {
            "added": "Added to your Archidekt collection.",
            "removed": "Removed from your Archidekt collection.",
            "updated": "Card details saved to your Archidekt collection.",
            "nothing": "Nothing was added: no card matched. Check the name or pick a suggestion.",
            "nothingimported": "Nothing was imported: no card in the list was found.",
            "imported": "Imported into your Archidekt collection.",
            "toomany": f"An import adds at most {MAX_IMPORT_ROWS} rows at a time. Split the file and "
            "import the rest after.",
            "unreadable": "That text is not a collection CSV or a card list the gateway can read.",
            "expired": "This form expired. Reload the page and try again.",
            "invalid": "That was not a valid request.",
            "lookup": "Archidekt is not answering right now. Try again in a moment.",
            "link": "Link your Archidekt account on the Account page first.",
            "writes": "Archidekt writes are switched off on this gateway, so nothing was changed.",
        }
        if not code or code not in msgs:
            return ""
        cls = "ok" if code in ("added", "removed", "updated", "imported") else "error"
        return f"<p class='notice {cls}'>{_esc(msgs[code])}</p>"

    def problem(exc: CollectionError) -> str:
        if exc.kind in ("not_linked", "auth"):
            return (
                "<section class='panel coll-empty'><h2>Your collection lives on Archidekt</h2>"
                "<p>The gateway shows and edits the Collection of the Archidekt account you link; nothing "
                "about your cards is stored here. Link your account to see it.</p>"
                f"<div class='actions'><a class='btn btn-primary' href='/account'>{icon('account')} "
                "Link Archidekt</a></div></section>"
            )
        return (
            f"<section class='panel'><p class='notice error'>{_esc(exc)}</p>"
            "<p class='muted'>Your collection is read live from Archidekt; when Archidekt is slow or "
            "down the page cannot show it.</p></section>"
        )

    @server.custom_route("/collection", methods=["GET"], include_in_schema=False)
    async def collection_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/collection")
        qp = request.query_params
        q = _clean(qp.get("q"), 80)
        sort = qp.get("sort") if qp.get("sort") in SORTS else "added"
        view, remember = view_choice(request, "collection", ("grid", "list"), "grid")
        try:
            page_no = max(1, min(int(qp.get("page") or 1), 10_000))
        except ValueError:
            page_no = 1
        csrf = _csrf(s, sid) or ""
        # the Add/Import fold opens after an add or import, or on ?add
        open_add = bool(qp.get("add")) or (qp.get("ok") or qp.get("err")) in (
            "added",
            "nothing",
            "nothingimported",
            "imported",
            "toomany",
            "unreadable",
        )
        try:
            out = await service.page(sub, page=page_no, q=q, sort=sort)
        except CollectionError as exc:
            if busy := busy_response(exc, request, page, sub=sub, sid=sid):
                return busy
            status = 200 if exc.kind in ("not_linked", "auth") else _STATUS.get(exc.kind, 400)
            return page(
                "My collection",
                notice(qp.get("err"), sub=sub, token=qp.get("r")) + problem(exc),
                sub=sub,
                sid=sid,
                status=status,
            )
        rows = out["rows"]
        link = state.db.get_link(sub) or {}
        arch_user = str(link.get("archidekt_user_id") or "")

        def sel(name: str, options: dict[str, str], cur: str, label: str, ic: str) -> str:
            opts = "".join(
                f"<option value='{_esc(k)}'{' selected' if k == cur else ''}>{_esc(v)}</option>"
                for k, v in options.items()
            )
            return (
                f"<div class='field'><label for='f-{name}'>{_esc(label)}</label><span class='sel'>{icon(ic)}"
                f"<select id='f-{name}' name='{name}'>{opts}</select></span></div>"
            )

        controls = (
            "<section class='panel collbar'><form method='get' "
            "action='/collection' class='controls' id='listform'>"
            "<div class='field filter grow'><label for='q'>Filter</label><span class='search'>"
            f"<input id='q' type='search' name='q' value='{_esc(q)}' placeholder='Card name'>"
            f"<button type='submit' aria-label='Filter' title='Filter'>{icon('search')}</button></span></div>"
            + sel("view", {"grid": "Grid", "list": "List"}, view, "View as", "grid")
            + sel("sort", SORTS, sort, "Sort by", "sort")
            + "<div class='field'><span class='lbl'>&nbsp;</span>"
            f"<a class='btn' href='/collection/export.csv'>{icon('download')} Export CSV</a></div>"
            "<noscript><button type='submit' class='apply'>Apply</button></noscript></form></section>"
        )
        add_box = (
            "<section class='panel addbox'><h2>Add a card</h2>"
            "<form method='post' action='/collection' class='addcard'><input "
            f"type='hidden' name='csrf' value='{_esc(csrf)}'>"
            "<input type='hidden' name='action' value='add'>"
            "<div class='field grow'><label for='addname'>Card name</label>"
            "<input id='addname' type='text' name='name' data-suggest='cards' "
            "placeholder='Card name, or “name (SET) 123” for one printing' "
            "autocomplete='off' required></div>"
            "<div class='field'><label for='addqty'>Copies</label><input id='addqty' "
            "type='number' name='quantity' value='1' min='1' max='999'></div>"
            "<div class='field'><label for='addfinish'>Finish</label><span "
            "class='sel'><select id='addfinish' name='finish'>"
            "<option value='nonfoil'>Non-foil</option><option value='foil'>Foil</option><option "
            "value='etched'>Etched</option></select></span></div>"
            "<div class='field'><label for='addcond'>Condition</label><span "
            "class='sel'><select id='addcond' name='condition'>"
            "<option selected>NM</option><option>LP</option><option>MP</option>"
            "<option>HP</option><option>DMG</option>"
            "</select></span></div>"
            f"<button class='btn-primary'>{icon('plus')} Add</button></form>"
            "<p class='muted small'>Scanning a pile is quicker: open <a href='/scan'>Scan</a>, then choose "
            "<strong>Save to collection</strong>. Everything you add lands in your Collection on "
            "Archidekt.</p></section>"
            "<section class='panel addbox importbox'><h2>Import a list</h2>"
            "<form method='post' action='/collection' class='importform'><input "
            f"type='hidden' name='csrf' value='{_esc(csrf)}'>"
            "<input type='hidden' name='action' value='import'>"
            "<p class='muted small'>A collection CSV (this page's Export CSV, or one with Archidekt's "
            f"column names) or a card list, one card per line, up to {MAX_IMPORT_ROWS} rows at a time. "
            "Copies of a printing you already own in that finish are added to it.</p>"
            "<div class='field filepick'><span class='lbl'>From a file</span><label class='filebtn'>"
            "<input id='importfile' type='file' accept='.csv,.txt,text/csv,text/plain' "
            "data-fill='importtext'><span class='btn'>Choose a file</span></label>"
            "<span class='fname' aria-live='polite'>No file chosen</span></div>"
            "<textarea id='importtext' name='text' rows='6' placeholder='Quantity,Name,Finish,Edition Code,"
            "Collector Number&#10;2,Card name,Normal,SET,123'></textarea>"
            f"<div class='actions'><button class='btn-primary'>{icon('plus')} Import</button></div></form>"
            "</section>"
        )
        arch_link = (
            f"<a class='ext' href='https://archidekt.com/collection/v2/{_esc(arch_user)}' target='_blank' "
            f"rel='noreferrer noopener'>{icon('external')} Open on Archidekt</a>"
            if arch_user
            else ""
        )
        totals_html = (
            f"<div class='coll-totals' id='totals'><span><b>{int(out['count'])}</b> "
            f"{'entry' if out['count'] == 1 else 'entries'} in your Archidekt collection</span>"
            f"{arch_link}</div>"
            "<p class='sr-only' id='live' role='status' aria-live='polite'></p>"
        )
        if not rows and not q:
            body = (
                "<section class='panel coll-empty'><h2>No cards yet</h2>"
                "<p>Your collection is the list of cards you own, kept on Archidekt. "
                "Scan a pile with your phone, or add cards by name. "
                "Cards you own show a green dot on every deck page.</p>"
                "<div class='actions'><a class='btn btn-primary' "
                f"href='/scan'>{icon('camera')} Scan cards</a>"
                f"<a class='btn' href='#addname'>{icon('plus')} Add by name</a></div></section>"
            )
        elif not rows:
            body = "<section class='panel'><p class='muted'>No cards match that filter.</p></section>"
        else:
            items = "".join(row_html(r, csrf, view=view) for r in rows)
            body = f"<ul class='{'collgrid' if view == 'grid' else 'colllist'}' id='cards'>{items}</ul>"
        from urllib.parse import urlencode

        qs = {k: v for k, v in (("q", q), ("sort", sort), ("view", view)) if v}

        def link_to(n: int) -> str:
            return "/collection?" + urlencode({**qs, "page": n})

        pager = ""
        if page_no > 1 or out["has_next"]:
            pager = "<nav class='pager' aria-label='Pages'>"
            if page_no > 1:
                pager += f"<a class='btn' href='{_esc(link_to(page_no - 1))}'>Previous</a>"
            pager += f"<span class='muted small'>Page {page_no} of {int(out['total_pages'])}</span>"
            if out["has_next"]:
                pager += f"<a class='btn' href='{_esc(link_to(page_no + 1))}'>Next</a>"
            pager += "</nav>"
        resp = page(
            "My collection",
            notice(qp.get("ok") or qp.get("err"), sub=sub, token=qp.get("r"))
            + totals_html
            + controls
            + export_help_html()
            + f"<details class='addfold'{' open' if open_add else ''}><summary>{icon('plus')} Add cards or "
            "import a list<span class='chev'>name, file or scan</span></summary>"
            + add_box
            + "</details>"
            + body
            + pager,
            sub=sub,
            sid=sid,
            scripts=True,
        )
        if remember:
            remember_view(resp, "collection", view, secure=s.public_url.startswith("https://"))
        return resp

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
                qty = data.get("quantity", "1")
                parsed = parse_text(f"{qty if str(qty).isdigit() else 1} {text}") if text else []
                if not parsed:
                    return RedirectResponse(
                        f"{back}{'&' if '?' in back else '?'}err=invalid", status_code=303
                    )
                p = parsed[0]
                out = await service.add(
                    sub,
                    [
                        {
                            "name": p.name,
                            "set": p.set_code,
                            "collector_number": p.collector_number,
                            "quantity": p.quantity,
                            "finish": data.get("finish", "nonfoil"),
                            "condition": data.get("condition", ""),
                        }
                    ],
                    source="manual",
                )
                code = "ok=added" if out["added"] else "err=nothing"
            elif action == "import":
                try:
                    items = parse_collection_import(data.get("text", ""))
                except CollectionError as exc:
                    code = "err=toomany" if "at most" in str(exc) else "err=unreadable"
                    code += "&r=" + keep_result(sub, {"refused": str(exc)[:REFUSAL_CHARS]})
                    return RedirectResponse(f"{back}{'&' if '?' in back else '?'}{code}", status_code=303)
                out = await service.add(sub, items, source="manual")
                code = "ok=imported" if out["added"] else "err=nothingimported"
                code += "&r=" + keep_result(sub, import_result(items, out))
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
                )
                code = "ok=updated"
            elif action in ("inc", "dec", "remove"):
                rid = _rid(data.get("id"))
                if action == "remove":
                    await service.remove(sub, rid)
                    code = "ok=removed"
                else:
                    rec = await service._record(sub, rid)
                    delta = 1 if action == "inc" else -1
                    await service.set_quantity(sub, rid, int(rec.get("quantity") or 0) + delta)
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
            out = await service.add(who[0], data.get("items") or data.get("cards"), source=source)
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
            if "quantity" in data:
                qty = data.get("quantity")
                if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0 or qty > MAX_QUANTITY:
                    raise CollectionError("invalid", "quantity must be a whole number")
                out = await service.set_quantity(who[0], rid, qty)
            else:
                out = await service.update(who[0], rid, data)
        except CollectionError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/collection/api/rows/{rid}", methods=["DELETE"], include_in_schema=False)
    async def api_delete(request: Request) -> Response:
        who = api_user(request, write=True)
        if isinstance(who, Response):
            return who
        try:
            out = await service.remove(who[0], _rid(request.path_params["rid"]))
        except CollectionError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

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
            "The signed-in user's Collection on Archidekt (the account they linked): the cards they "
            "own, one row per printing and finish with a quantity, 100 a page in Archidekt's "
            "newest-first order. Optional `query` filters by card name; `sort` is added or edition "
            "(set release); `page` pages. Returns cards, count and total_pages. Read-only."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": True},
    )
    async def list_collection(query: str | None = None, sort: str = "added", page: int = 1) -> dict[str, Any]:
        sub = _sub()
        try:
            out = await service.page(
                sub,
                page=max(1, min(int(page or 1), 10_000)),
                q=_clean(query, 80),
                sort=sort if sort in SORTS else "added",
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
                rec = row_out(await service._record(sub, _rid(entry["id"])))
                rows = [rec]
            elif entry.get("name"):
                rows = await service.find_by_name(sub, str(entry["name"]))
                if not rows:
                    raise CollectionError(
                        "not_found", f"no card named '{_clean(str(entry['name']), 60)}' in your collection"
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
                gone = await service.remove(sub, _rid(r["id"]), int(r["quantity"]), progress=progress)
                result["removed"].append(gone["removed"])
                expected[_rid(r["id"])] = int(gone["row"]["quantity"]) if gone.get("row") else None
        except CollectionError as exc:
            raise DeckError(exc.kind, str(exc)) from exc
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
            return False, [f"could not read the collection back: {exc}"]
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
        return proposal_tool_result(data, decks=service.decks, sub=_sub(), in_chat=in_chat)

    @server.tool(
        name="propose_collection_changes",
        title="Propose changes to my collection",
        description=(
            "Propose adding cards to, or removing cards from, the user's Archidekt Collection. Like a "
            "deck edit this is a proposal: show its diff and review_url; it is applied by the user "
            "(Approve on the proposal card or the review page) or by you when assistant_may_apply is "
            "true, with apply_proposal. add: a list of {name, set_code?, collector_number?, quantity?, "
            "finish?, condition?, language?} or plain names (a card named without set_code and "
            "collector_number is added in a printing Archidekt picks, as a nonfoil unless finish says "
            "otherwise; language: the card's language code, EN default, or ES, FR, DE, IT, PT, JA, KO, "
            "RU, ZHS, ZHT); text: one "
            "card per line, e.g. '2 Sol Ring (CMR) 472 *F*'; scan_session: the id or name of a scan "
            "session, every matched card in it is added and the session is removed once applied; remove: "
            "a list of {id (from list_collection) | name, quantity?} (no quantity removes every copy). At "
            "most 100 cards each way per proposal."
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
        try:
            # the source and scan id travel with the proposal so the apply can drop the scan
            p = await service.decks.propose_collection(
                sub, adds, removals, extra={"source": source, "scan_session_id": session_id}
            )
        except DeckError as exc:
            return _result({"ok": False, "error": exc.kind, "message": str(exc), **exc.extra})
        return _result({"ok": True, **p, **({"warnings": warnings} if warnings else {})})
