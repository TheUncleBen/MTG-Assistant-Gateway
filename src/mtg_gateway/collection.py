"""Owned cards: a member's collection, kept in the gateway's own database.

Archidekt has a Collection; this is the gateway's counterpart, built so a person can scan a
pile of cards and keep what they own without depending on Archidekt. Each row is one printing
in one finish (and language and condition) with a quantity. Rows carry the card facts Scryfall
gave at add time (set, number, type, mana), so the page renders without any lookup, and card
images are shown straight from Scryfall by the card's id, as the deck pages do.

Three front doors share one service: the ``/collection`` page and its JSON API (browser session
plus the form token), the ``/api/v1/collection`` routes (bearer token or browser session), and the
``*_collection`` MCP tools. Every query is scoped by the member's subject; nothing here can read
another member's cards. Nothing here talks to Archidekt.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import secrets
import time
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response

from .db import Database
from .deckpage import DECK_CSS, image_url, mana_html
from .pages import _csrf, _safe_next, browser_session, login_redirect, read_limited
from .theme import icon, render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .scan.service import ScanService

MAX_ROWS_PER_USER = 20_000
MAX_ITEMS_PER_CALL = 500
MAX_QUANTITY = 9_999
PAGE_SIZE = 60
FINISHES = ("nonfoil", "foil", "etched")
CONDITIONS = ("", "NM", "LP", "MP", "HP", "DMG")
SORTS = {
    "added": "Recently added",
    "name": "Name",
    "set": "Set",
    "mv": "Mana value",
    "qty": "Quantity",
    "type": "Type",
}
COLLECTION_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


class CollectionError(Exception):
    """User-facing failure. ``kind``: invalid, not_found, unavailable, rate_limited, busy."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def _now() -> int:
    return int(time.time())


def _clean(value: Any, limit: int) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()[:limit]


# -- storage ----------------------------------------------------------------------------------------
class CollectionStore:
    def __init__(self, db: Database):
        self.db = db

    def upsert(
        self,
        sub: str,
        card: dict[str, Any],
        *,
        quantity: int,
        finish: str,
        lang: str,
        condition: str,
        notes: str,
        source: str,
    ) -> dict[str, Any]:
        """Add ``quantity`` copies of one printing; a row that exists gets the copies added."""
        now = _now()
        with self.db.tx() as c:
            total = c.execute("SELECT COUNT(*) FROM collection_cards WHERE owner_sub = ?", (sub,)).fetchone()[
                0
            ]
            row = c.execute(
                "SELECT id, quantity FROM collection_cards WHERE owner_sub = ? AND scryfall_id = ? "
                "AND finish = ? AND lang = ? AND condition = ?",
                (sub, card["scryfall_id"], finish, lang, condition),
            ).fetchone()
            if row is None and total >= MAX_ROWS_PER_USER:
                raise CollectionError(
                    "invalid", f"your collection holds the most it can ({MAX_ROWS_PER_USER} rows)"
                )
            if row is not None:
                qty = min(MAX_QUANTITY, int(row["quantity"]) + quantity)
                c.execute(
                    "UPDATE collection_cards SET quantity = ?, updated_at = ?, "
                    "notes = CASE WHEN ? = '' THEN notes ELSE ? END WHERE id = ? AND owner_sub = ?",
                    (qty, now, notes, notes, row["id"], sub),
                )
                rid = row["id"]
            else:
                rid = "col_" + secrets.token_urlsafe(9)
                c.execute(
                    """INSERT INTO collection_cards (id, owner_sub, scryfall_id, oracle_id, name, set_code,
                       set_name,
                       collector_number, rarity, type_line, mana_cost, mana_value, color_identity, finish,
                         lang,
                       condition, quantity, notes, source, added_at, updated_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        rid,
                        sub,
                        card["scryfall_id"],
                        _clean(card.get("oracle_id"), 60),
                        _clean(card.get("name"), 200),
                        _clean(card.get("set"), 10).lower(),
                        _clean(card.get("set_name"), 120),
                        _clean(card.get("collector_number"), 20),
                        _clean(card.get("rarity"), 20),
                        _clean(card.get("type_line"), 200),
                        _clean(card.get("mana_cost"), 60),
                        card.get("mana_value") if isinstance(card.get("mana_value"), int | float) else None,
                        "".join(
                            x
                            for x in (card.get("color_identity") or [])
                            if isinstance(x, str) and x in "WUBRG"
                        ),
                        finish,
                        lang,
                        condition,
                        min(MAX_QUANTITY, quantity),
                        notes,
                        source,
                        now,
                        now,
                    ),
                )
            r = c.execute(
                "SELECT * FROM collection_cards WHERE id = ? AND owner_sub = ?", (rid, sub)
            ).fetchone()
        return _row_out(r)

    def get(self, sub: str, rid: str) -> dict[str, Any] | None:
        with self.db.tx() as c:
            r = c.execute(
                "SELECT * FROM collection_cards WHERE id = ? AND owner_sub = ?", (rid, sub)
            ).fetchone()
        return _row_out(r) if r else None

    def set_quantity(self, sub: str, rid: str, quantity: int) -> dict[str, Any] | None:
        """Set the copies of one row; 0 deletes it. Returns the row, or None when it is gone."""
        with self.db.tx() as c:
            if quantity <= 0:
                c.execute("DELETE FROM collection_cards WHERE id = ? AND owner_sub = ?", (rid, sub))
                return None
            c.execute(
                "UPDATE collection_cards SET quantity = ?, updated_at = ? WHERE id = ? AND owner_sub = ?",
                (min(MAX_QUANTITY, quantity), _now(), rid, sub),
            )
            r = c.execute(
                "SELECT * FROM collection_cards WHERE id = ? AND owner_sub = ?", (rid, sub)
            ).fetchone()
        return _row_out(r) if r else None

    def update(self, sub: str, rid: str, **fields: Any) -> dict[str, Any] | None:
        allowed = {
            k: v for k, v in fields.items() if k in ("finish", "condition", "notes", "lang") and v is not None
        }
        if not allowed:
            return self.get(sub, rid)
        sets = ", ".join(f"{k} = ?" for k in allowed)
        with self.db.tx() as c:
            c.execute(
                f"UPDATE collection_cards SET {sets}, updated_at = ? WHERE id = ? AND owner_sub = ?",
                (*allowed.values(), _now(), rid, sub),
            )
            r = c.execute(
                "SELECT * FROM collection_cards WHERE id = ? AND owner_sub = ?", (rid, sub)
            ).fetchone()
        return _row_out(r) if r else None

    def delete(self, sub: str, rid: str) -> bool:
        with self.db.tx() as c:
            return (
                c.execute("DELETE FROM collection_cards WHERE id = ? AND owner_sub = ?", (rid, sub)).rowcount
                > 0
            )

    def list(
        self,
        sub: str,
        *,
        q: str = "",
        set_code: str = "",
        finish: str = "",
        color: str = "",
        sort: str = "added",
        limit: int = PAGE_SIZE,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        where = ["owner_sub = ?"]
        args: list[Any] = [sub]
        if q:
            where.append(
                "(name LIKE ? ESCAPE '\\' OR type_line LIKE ? ESCAPE '\\' OR set_name LIKE ? ESCAPE '\\')"
            )
            like = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
            args += [like, like, like]
        if set_code:
            where.append("set_code = ?")
            args.append(set_code.lower())
        if finish in FINISHES:
            where.append("finish = ?")
            args.append(finish)
        if color:
            if color == "C":
                where.append("color_identity = ''")
            elif color in "WUBRG":
                where.append("instr(color_identity, ?) > 0")
                args.append(color)
        order = {
            "added": "updated_at DESC, name COLLATE NOCASE",
            "name": "name COLLATE NOCASE, set_code, collector_number",
            "set": "set_code, collector_number, name COLLATE NOCASE",
            "mv": "mana_value IS NULL, mana_value, name COLLATE NOCASE",
            "qty": "quantity DESC, name COLLATE NOCASE",
            "type": "type_line COLLATE NOCASE, name COLLATE NOCASE",
        }.get(sort, "updated_at DESC, name COLLATE NOCASE")
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT * FROM collection_cards WHERE "
                f"{' AND '.join(where)} ORDER BY {order} LIMIT ? OFFSET ?",
                (*args, max(1, min(limit, 500)), max(0, offset)),
            ).fetchall()
        return [_row_out(r) for r in rows]

    def totals(self, sub: str) -> dict[str, Any]:
        with self.db.tx() as c:
            r = c.execute(
                "SELECT COUNT(*) AS rows_, COALESCE(SUM(quantity), 0) AS cards, "
                "COUNT(DISTINCT CASE WHEN oracle_id = '' THEN name ELSE oracle_id END) AS distinct_ "
                "FROM collection_cards WHERE owner_sub = ?",
                (sub,),
            ).fetchone()
            sets = c.execute(
                "SELECT set_code, set_name, SUM(quantity) AS n FROM collection_cards WHERE owner_sub = ? "
                "GROUP BY set_code ORDER BY n DESC, set_code LIMIT 200",
                (sub,),
            ).fetchall()
        return {
            "rows": int(r["rows_"]),
            "cards": int(r["cards"]),
            "distinct": int(r["distinct_"]),
            "sets": [{"code": s["set_code"], "name": s["set_name"], "cards": int(s["n"])} for s in sets],
        }

    def owned_names(self, sub: str) -> dict[str, int]:
        """Lower-cased card name -> copies owned, for the deck page's owned marks."""
        with self.db.tx() as c:
            rows = c.execute(
                "SELECT lower(name) AS n, SUM(quantity) AS q FROM "
                "collection_cards WHERE owner_sub = ? GROUP BY n",
                (sub,),
            ).fetchall()
        return {r["n"]: int(r["q"]) for r in rows}

    def export_rows(self, sub: str) -> list[dict[str, Any]]:
        return self.list(sub, sort="name", limit=MAX_ROWS_PER_USER, offset=0)


def _row_out(r: Any) -> dict[str, Any]:
    d = dict(r)
    d["set"] = d.pop("set_code")
    d["color_identity"] = list(d.get("color_identity") or "")
    sid = d.get("scryfall_id") or ""
    d["image_small"] = image_url(sid, "small")
    d["image_normal"] = image_url(sid, "normal")
    d.pop("owner_sub", None)
    return d


# -- service ----------------------------------------------------------------------------------------
class CollectionService:
    def __init__(self, db: Database, scan: ScanService | None):
        self.db = db
        self.store = CollectionStore(db)
        self.scan = scan

    async def add(self, sub: str, items: Any, *, source: str) -> dict[str, Any]:
        """Add cards. Each item names a printing by ``scryfall_id`` (a scan result or a collection
        row) or by ``name`` with optional ``set`` and ``collector_number`` (looked up on Scryfall
        through the scan service; the best printing is taken). ``quantity``, ``finish`` (or
        ``foil: true``), ``condition``, ``lang`` and ``notes`` are optional."""
        if not isinstance(items, list) or not items:
            raise CollectionError("invalid", "give a non-empty list of cards")
        if len(items) > MAX_ITEMS_PER_CALL:
            raise CollectionError("invalid", f"at most {MAX_ITEMS_PER_CALL} cards per call")
        prepared: list[tuple[dict[str, Any], dict[str, Any] | None, dict[str, Any]]] = []
        to_resolve: list[tuple[int, dict[str, Any]]] = []
        for i, raw in enumerate(items):
            if isinstance(raw, str):
                raw = {"name": raw}
            if not isinstance(raw, dict):
                raise CollectionError("invalid", f"card {i} must be an object")
            card = raw.get("card") if isinstance(raw.get("card"), dict) else raw
            opts = _item_options(raw, i)
            if (
                isinstance(card.get("scryfall_id"), str)
                and _ID.fullmatch(card["scryfall_id"])
                and card.get("name")
            ):
                prepared.append((raw, card, opts))
            elif _clean(card.get("name"), 200) or (
                _clean(card.get("set"), 10) and _clean(card.get("collector_number"), 20)
            ):
                prepared.append((raw, None, opts))
                to_resolve.append((len(prepared) - 1, card))
            else:
                raise CollectionError("invalid", f"card {i}: give a name, or a set and collector number")
        skipped: list[dict[str, Any]] = []
        if to_resolve:
            if self.scan is None:
                raise CollectionError("unavailable", "card lookups are not available on this gateway")
            from .scan.service import ScanError, parse_cards

            try:
                inputs = parse_cards(
                    [
                        {
                            "name": _clean(c.get("name"), 200),
                            "set": _clean(c.get("set"), 10),
                            "collector_number": _clean(c.get("collector_number"), 20),
                        }
                        for _idx, c in to_resolve
                    ]
                )
                results = await self.scan.resolve(inputs, owner=sub)
            except ScanError as exc:
                raise CollectionError(exc.kind, str(exc)) from exc
            for (idx, _c), res in zip(to_resolve, results, strict=True):
                if res.card and res.status in ("exact", "printing", "fuzzy"):
                    raw, _none, opts = prepared[idx]
                    prepared[idx] = (raw, res.card, opts)
                    if res.status == "fuzzy":
                        opts["note"] = res.note or f"name read as {res.card.get('name')}"
                else:
                    skipped.append(
                        {
                            "input": res.input.as_dict(),
                            "status": res.status,
                            "suggestions": res.suggestions,
                            "note": res.note,
                        }
                    )
        added: list[dict[str, Any]] = []
        for _raw, card, opts in prepared:
            if card is None:
                continue
            finish = opts["finish"]
            finishes = [f for f in (card.get("finishes") or []) if isinstance(f, str)]
            if finishes and finish not in finishes and len(finishes) == 1:
                finish = finishes[0]  # a printing that exists in one finish only is that finish
            row = self.store.upsert(
                sub,
                card,
                quantity=opts["quantity"],
                finish=finish,
                lang=opts["lang"],
                condition=opts["condition"],
                notes=opts["notes"],
                source=source,
            )
            if opts.get("note"):
                row = {**row, "note": opts["note"]}
            added.append(row)
        self.db.audit(
            "collection_added",
            sub=sub,
            detail={"rows": len(added), "cards": sum(int(r["quantity"]) for r in added), "source": source},
        )
        return {"added": added, "skipped": skipped, "totals": self.store.totals(sub)}

    def remove(self, sub: str, rid: str, quantity: int | None = None) -> dict[str, Any]:
        row = self.store.get(sub, rid)
        if row is None:
            raise CollectionError("not_found", "no such card in your collection")
        if quantity is None or quantity >= int(row["quantity"]):
            self.store.delete(sub, rid)
            left = None
        else:
            left = self.store.set_quantity(sub, rid, int(row["quantity"]) - max(1, quantity))
        self.db.audit("collection_removed", sub=sub, detail={"id": rid, "quantity": quantity})
        return {"removed": row, "row": left, "totals": self.store.totals(sub)}

    def set_quantity(self, sub: str, rid: str, quantity: int) -> dict[str, Any]:
        if self.store.get(sub, rid) is None:
            raise CollectionError("not_found", "no such card in your collection")
        row = self.store.set_quantity(sub, rid, quantity)
        return {"row": row, "totals": self.store.totals(sub)}

    def update(self, sub: str, rid: str, data: dict[str, Any]) -> dict[str, Any]:
        if self.store.get(sub, rid) is None:
            raise CollectionError("not_found", "no such card in your collection")
        opts = _item_options(
            {k: v for k, v in data.items() if k in ("finish", "foil", "condition", "notes", "lang")}, 0
        )
        fields: dict[str, Any] = {}
        if "finish" in data or "foil" in data:
            fields["finish"] = opts["finish"]
        if "condition" in data:
            fields["condition"] = opts["condition"]
        if "notes" in data:
            fields["notes"] = opts["notes"]
        if "lang" in data:
            fields["lang"] = opts["lang"]
        row = self.store.update(sub, rid, **fields)
        return {"row": row, "totals": self.store.totals(sub)}

    def find_by_name(self, sub: str, name: str) -> list[dict[str, Any]]:
        name = _clean(name, 200).lower()
        return [r for r in self.store.list(sub, q=name, limit=200) if r["name"].lower() == name]

    def export_csv(self, sub: str) -> str:
        """The collection as CSV in the column layout Archidekt's collection import reads
        (Quantity, Name, Finish, Condition, Language, Edition code, Collector number, Scryfall ID,
        Date added, Notes). Reported, not verified against a live import."""
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
                "Notes",
            ]
        )
        for r in self.store.export_rows(sub):
            w.writerow(
                [
                    r["quantity"],
                    r["name"],
                    "Foil" if r["finish"] == "foil" else "Etched" if r["finish"] == "etched" else "Normal",
                    r["condition"],
                    r["lang"],
                    r["set"].upper(),
                    r["set_name"],
                    r["collector_number"],
                    r["scryfall_id"],
                    time.strftime("%Y-%m-%d", time.gmtime(r["added_at"])),
                    r["notes"],
                ]
            )
        return out.getvalue()

    def summary(self, sub: str) -> dict[str, Any]:
        return self.store.totals(sub)


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
    if condition not in CONDITIONS:
        raise CollectionError("invalid", f"card {i}: condition must be one of NM, LP, MP, HP, DMG or empty")
    lang = _clean(raw.get("lang"), 3).lower() or "en"
    if not re.fullmatch(r"[a-z]{2,3}", lang):
        raise CollectionError("invalid", f"card {i}: lang must be a two or three letter code")
    return {
        "quantity": qty,
        "finish": finish,
        "condition": condition,
        "lang": lang,
        "notes": _clean(raw.get("notes"), 300),
    }


# -- browser page and JSON -----------------------------------------------------------------------------
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
_STATUS = {"invalid": 400, "not_found": 404, "unavailable": 503, "rate_limited": 503, "busy": 429}


def _esc(v: Any) -> str:
    return html.escape("" if v is None else str(v), quote=True)


def _fail(exc: CollectionError) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": exc.kind, "message": str(exc)}, _STATUS.get(exc.kind, 400), headers=NO_STORE
    )


def _finish_label(f: str) -> str:
    return {"foil": "Foil", "etched": "Etched"}.get(f, "")


def row_html(r: dict[str, Any], csrf: str, *, view: str) -> str:
    img = r.get("image_small") or ""
    rid = _esc(r["id"])
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
        f"fewer {_esc(r['name'])}'>{icon('minus')}</button>"
        f"<output aria-label='Copies'>{int(r['quantity'])}</output>"
        "<button name='action' value='inc' class='mini' aria-label='One "
        f"more {_esc(r['name'])}'>{icon('plus')}</button>"
        "</form>"
    )
    remove = (
        f"<form method='post' action='/collection' class='rm'>{csrf_in}"
        "<button name='action' value='remove' class='mini "
        f"icon-only' aria-label='Remove {_esc(r['name'])} from your "
        f"collection' title='Remove'>{icon('x')}</button></form>"
    )
    set_line = f"{_esc((r.get('set') or '').upper())} {_esc(r.get('collector_number'))}"
    if view == "grid":
        body = (
            f"<img src='{_esc(img)}' alt='{_esc(r['name'])}' loading='lazy'>"
            if img
            else f"<span class='ph'><span class='t'><span class='nm'>{_esc(r['name'])}</span></span></span>"
        )
        return (
            f"<li class='c col' data-name='{_esc(r['name'].lower())}' data-id='{rid}'>"
            f"<div class='pic'>{body}<span class='qty'>{int(r['quantity'])}</span>{badges}</div>"
            f"<div class='cap'><span class='name'>{_esc(r['name'])}</span><span "
            f"class='set'>{set_line}</span></div>"
            f"<div class='act'>{stepper}{remove}</div></li>"
        )
    return (
        f"<li class='row col' data-name='{_esc(r['name'].lower())}' data-id='{rid}'>"
        + (
            f"<img class='thumb' src='{_esc(img)}' alt='' loading='lazy'>"
            if img
            else "<span class='thumb'></span>"
        )
        + f"<span class='n'><span class='name'>{_esc(r['name'])}</span>{badges}"
        f"<span class='meta'>{set_line} · {_esc(r.get('set_name'))}"
        + (f" · {_esc(r.get('type_line'))}" if r.get("type_line") else "")
        + "</span></span>"
        f"<span class='mc'>{mana_html(r.get('mana_cost') or '')}</span>{stepper}{remove}</li>"
    )


COLLECTION_CSS = """
.collbar .controls{display:grid;grid-template-columns:minmax(0,2fr) repeat(3,minmax(9rem,1fr)) auto;gap:1rem;
  align-items:end}
.collbar .field .btn{margin:0}
@media (max-width:1000px){ .collbar .controls{grid-template-columns:1fr 1fr}
  .collbar .filter{grid-column:1 / -1} }
@media (max-width:600px){ .collbar .controls{grid-template-columns:1fr} }
.coll-totals{display:flex;flex-wrap:wrap;gap:1rem 1.5rem;align-items:baseline;margin:0 0 .75rem;
  color:var(--text-muted)}
.coll-totals b{color:var(--text);font-size:1.3rem;font-variant-numeric:tabular-nums}
.addbox form.addcard{display:grid;grid-template-columns:minmax(0,2fr) 5.5rem minmax(0,1fr) minmax(0,1fr) auto;
  gap:.5rem;align-items:end}
.addbox form.addcard .field{margin:0} .addbox form.addcard button{margin:0;height:var(--ctl)}
@media (max-width:600px){ .addbox form.addcard{grid-template-columns:1fr 1fr}
  .addbox form.addcard .grow,.addbox form.addcard button{grid-column:1 / -1} }
ul.collgrid{list-style:none;margin:0;padding:0;display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:1rem}
ul.collgrid .col .pic{position:relative;aspect-ratio:5/7;border-radius:4.5%;overflow:hidden;
  background:var(--surface-2);
  border:2px solid var(--card-border)}
ul.collgrid .col .pic img{width:100%;height:100%;display:block;object-fit:cover}
ul.collgrid .col .pic .qty{position:absolute;top:0;left:0;width:38px;height:38px;clip-path:polygon(0 0,
  0 100%,100% 0);
  border-radius:11px 0 0 0;background:#3a3a3a;color:#fff;font-size:12px;font-weight:700;padding:5px 0 0 7px}
ul.collgrid .col .pic .finish{position:absolute;right:6px;bottom:6px}
ul.collgrid .col .pic .cond{position:absolute;left:6px;bottom:6px;font-size:.7rem}
ul.collgrid .col .cap{display:flex;flex-direction:column;margin:.35rem 0 .25rem;min-width:0}
ul.collgrid .col .cap .name{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
ul.collgrid .col .cap .set{font-size:.8rem;color:var(--text-muted)}
ul.collgrid .col .act,ul.colllist .act{display:flex;align-items:center;gap:.35rem}
form.qty{display:inline-flex;align-items:center;gap:.15rem;margin:0}
form.qty button.mini,form.rm button.mini{margin:0;width:2.25rem;height:2.25rem;padding:0;display:inline-flex;
  align-items:center;justify-content:center}
form.qty output{min-width:2rem;text-align:center;font-weight:700;font-variant-numeric:tabular-nums}
form.rm{display:inline;margin:0 0 0 auto}
ul.colllist{list-style:none;margin:0;padding:0}
ul.colllist .row{display:grid;grid-template-columns:34px minmax(0,1fr) auto auto auto;gap:.6rem;
  align-items:center;
  padding:.4rem 0;border-top:1px solid var(--border)}
ul.colllist .thumb{width:34px;height:48px;border-radius:3px;object-fit:cover;background:var(--surface-3);
  display:block}
ul.colllist .n{display:flex;flex-direction:column;min-width:0}
ul.colllist .n .name{font-weight:700;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
ul.colllist .n .meta{font-size:.8rem;color:var(--text-muted);white-space:nowrap;overflow:hidden;
  text-overflow:ellipsis}
ul.colllist .n .finish,ul.colllist .n .cond{margin-left:.35rem;vertical-align:middle}
@media (max-width:600px){ ul.colllist .row{grid-template-columns:34px minmax(0,
  1fr) auto} ul.colllist .mc{display:none}
  ul.colllist form.rm{grid-column:3;grid-row:2;justify-self:end} ul.colllist form.qty{grid-column:2;
    grid-row:2} }
.pager{display:flex;justify-content:center;gap:.5rem;margin:1rem 0}
.pager .btn{margin:0}
.coll-empty{text-align:center;padding:2rem 1rem}
.coll-empty .actions{justify-content:center}
.sets{display:flex;flex-wrap:wrap;gap:.35rem;margin:.5rem 0 0}
"""


def add_collection(server: MCPServer, state: AppState) -> CollectionService:
    service = CollectionService(state.db, getattr(state, "scan", None))
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
            wide=True,
            current="/collection",
            scripts=scripts,
            head_extra=f"<style>{DECK_CSS}{COLLECTION_CSS}</style>"
            + (
                "<script src='/static/deck.js' defer></script><script "
                "src='/static/collection.js' defer></script>"
                if scripts
                else ""
            ),
        )
        resp.headers["Content-Security-Policy"] = COLLECTION_CSP
        return resp

    async def form(request: Request) -> dict[str, str]:
        from urllib.parse import parse_qs

        raw = await read_limited(request, 64_000)
        if raw is None:
            return {}
        return {k: v[0] for k, v in parse_qs(raw.decode("utf-8", "replace"), keep_blank_values=True).items()}

    def check_csrf(sid: str | None, data: dict[str, str]) -> bool:
        import hmac

        expected = _csrf(s, sid)
        return bool(
            sid and expected and hmac.compare_digest(data.get("csrf", "").encode(), expected.encode())
        )

    def api_user(request: Request, *, write: bool) -> tuple[str, str] | Response:
        sub, sid = browser_session(state, request)
        if not sub or not sid:
            return JSONResponse(
                {"ok": False, "error": "unauthenticated", "login": "/login?next=/collection"},
                401,
                headers=NO_STORE,
            )
        if write:
            import hmac

            expected = _csrf(s, sid) or ""
            given = request.headers.get("x-csrf-token", "")
            if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
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

    def notice(code: str | None) -> str:
        msgs = {
            "added": "Added to your collection.",
            "removed": "Removed from your collection.",
            "nothing": "Nothing was added: no card matched. Check the name or pick a suggestion.",
            "expired": "This form expired. Reload the page and try again.",
            "invalid": "That was not a valid request.",
            "lookup": "Card lookup is unavailable right now. Try again in a moment.",
        }
        if not code or code not in msgs:
            return ""
        cls = "ok" if code in ("added", "removed") else "error"
        return f"<p class='notice {cls}'>{_esc(msgs[code])}</p>"

    @server.custom_route("/collection", methods=["GET"], include_in_schema=False)
    async def collection_page(request: Request) -> Response:
        sub, sid = browser_session(state, request)
        if not sub:
            return login_redirect("/collection")
        qp = request.query_params
        q = _clean(qp.get("q"), 80)
        sort = qp.get("sort") if qp.get("sort") in SORTS else "added"
        view = qp.get("view") if qp.get("view") in ("grid", "list") else "grid"
        set_code = _clean(qp.get("set"), 10).lower()
        finish = qp.get("finish") if qp.get("finish") in FINISHES else ""
        color = qp.get("color") if qp.get("color") in ("W", "U", "B", "R", "G", "C") else ""
        try:
            page_no = max(1, min(int(qp.get("page") or 1), 10_000))
        except ValueError:
            page_no = 1
        rows = service.store.list(
            sub,
            q=q,
            set_code=set_code,
            finish=finish,
            color=color,
            sort=sort,
            limit=PAGE_SIZE + 1,
            offset=(page_no - 1) * PAGE_SIZE,
        )
        more = len(rows) > PAGE_SIZE
        rows = rows[:PAGE_SIZE]
        totals = service.store.totals(sub)
        csrf = _csrf(s, sid) or ""
        sets = totals["sets"]
        set_opts = "".join(
            f"<option value='{_esc(x['code'])}'{' selected' if x['code'] == set_code else ''}>"
            f"{_esc((x['code'] or '?').upper())} · {_esc(x['name'] or '')} ({x['cards']})</option>"
            for x in sets[:150]
        )

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
            f"<input id='q' type='search' name='q' value='{_esc(q)}' placeholder='Card name, type or set'>"
            f"<button type='submit' aria-label='Filter'>{icon('search')}</button></span></div>"
            + sel("view", {"grid": "Grid", "list": "List"}, view, "View as", "grid")
            + sel("sort", SORTS, sort, "Sort by", "sort")
            + (
                f"<div class='field'><label for='f-set'>Set</label><span class='sel'>{icon('decks')}"
                "<select id='f-set' name='set'><option value=''>All "
                f"sets</option>{set_opts}</select></span></div>"
                if sets
                else ""
            )
            + sel(
                "finish",
                {"": "Any finish", "nonfoil": "Non-foil", "foil": "Foil", "etched": "Etched"},
                finish,
                "Finish",
                "layers",
            )
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
            "<input id='addname' type='text' name='name' list='cardnames' "
            "placeholder='Card name, or “name (SET) 123” for one printing' "
            "autocomplete='off' required><datalist id='cardnames'></datalist></div>"
            "<div class='field'><label for='addqty'>Copies</label><input id='addqty' "
            "type='number' name='quantity' value='1' min='1' max='999'></div>"
            "<div class='field'><label for='addfinish'>Finish</label><span "
            "class='sel'><select id='addfinish' name='finish'>"
            "<option value='nonfoil'>Non-foil</option><option value='foil'>Foil</option><option "
            "value='etched'>Etched</option></select></span></div>"
            "<div class='field'><label for='addcond'>Condition</label><span "
            "class='sel'><select id='addcond' name='condition'>"
            "<option value=''>Any</option><option>NM</option><option>LP</option><option>MP</option>"
            "<option>HP</option><option>DMG</option>"
            "</select></span></div>"
            f"<button class='btn-primary'>{icon('plus')} Add</button></form>"
            "<p class='muted small'>Scanning a pile is quicker: open <a href='/scan'>Scan</a>, then choose "
            "<strong>Save to collection</strong>.</p></section>"
        )
        totals_html = (
            f"<div class='coll-totals' id='totals'><span><b>{totals['cards']}</b> cards</span>"
            f"<span><b>{totals['distinct']}</b> distinct</span><span><b>{len(sets)}</b> sets</span></div>"
            "<p class='sr-only' id='live' role='status' aria-live='polite'></p>"
        )
        if not rows and not (q or set_code or finish or color):
            body = (
                "<section class='panel coll-empty'><h2>No cards yet</h2>"
                "<p>Your collection is the list of cards you own. "
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
        qs = {
            k: v
            for k, v in (
                ("q", q),
                ("sort", sort),
                ("view", view),
                ("set", set_code),
                ("finish", finish),
                ("color", color),
            )
            if v
        }
        from urllib.parse import urlencode

        def link(n: int) -> str:
            return "/collection?" + urlencode({**qs, "page": n})

        pager = ""
        if page_no > 1 or more:
            pager = "<nav class='pager' aria-label='Pages'>"
            if page_no > 1:
                pager += f"<a class='btn' href='{_esc(link(page_no - 1))}'>Previous</a>"
            pager += f"<span class='muted small'>Page {page_no}</span>"
            if more:
                pager += f"<a class='btn' href='{_esc(link(page_no + 1))}'>Next</a>"
            pager += "</nav>"
        return page(
            "My collection",
            notice(qp.get("ok") or qp.get("err")) + totals_html + controls + body + pager + add_box,
            sub=sub,
            sid=sid,
            scripts=True,
        )

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
            elif action in ("inc", "dec", "remove"):
                rid = _clean(data.get("id"), 40)
                row = service.store.get(sub, rid)
                if row is None:
                    raise CollectionError("not_found", "no such card")
                if action == "remove":
                    service.remove(sub, rid)
                    code = "ok=removed"
                else:
                    delta = 1 if action == "inc" else -1
                    service.set_quantity(sub, rid, int(row["quantity"]) + delta)
                    code = ""
            else:
                code = "err=invalid"
        except CollectionError as exc:
            code = "err=lookup" if exc.kind in ("unavailable", "rate_limited", "busy") else "err=invalid"
        sep = "&" if "?" in back else "?"
        return RedirectResponse(f"{back}{sep}{code}" if code else back, status_code=303)

    @server.custom_route("/collection/export.csv", methods=["GET"], include_in_schema=False)
    async def export_csv(request: Request) -> Response:
        sub, _sid = browser_session(state, request)
        if not sub:
            return login_redirect("/collection")
        return Response(
            service.export_csv(sub),
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
        rid = request.path_params["rid"]
        try:
            if "quantity" in data:
                qty = data.get("quantity")
                if not isinstance(qty, int) or isinstance(qty, bool) or qty < 0 or qty > MAX_QUANTITY:
                    raise CollectionError("invalid", "quantity must be a whole number")
                out = service.set_quantity(who[0], rid, qty)
            else:
                out = service.update(who[0], rid, data)
        except CollectionError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/collection/api/rows/{rid}", methods=["DELETE"], include_in_schema=False)
    async def api_delete(request: Request) -> Response:
        who = api_user(request, write=True)
        if isinstance(who, Response):
            return who
        try:
            out = service.remove(who[0], request.path_params["rid"])
        except CollectionError as exc:
            return _fail(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/collection/api/summary", methods=["GET"], include_in_schema=False)
    async def api_summary(request: Request) -> Response:
        who = api_user(request, write=False)
        if isinstance(who, Response):
            return who
        return JSONResponse({"ok": True, **service.summary(who[0])}, headers=NO_STORE)


def add_collection_tools(server: MCPServer, service: CollectionService) -> None:
    from mcp.server.auth.middleware.auth_context import get_access_token

    def _sub() -> str:
        token = get_access_token()
        if token is None or not token.subject:
            raise RuntimeError("no authenticated user on this request")
        return token.subject

    def _err(exc: CollectionError) -> dict[str, Any]:
        return {"ok": False, "error": exc.kind, "message": str(exc)}

    @server.tool(
        name="list_collection",
        title="List the cards I own",
        description=(
            "The signed-in user's collection on this gateway: the cards they own, one row per printing and "
            "finish with a quantity. Optional `query` filters by name, type or set name; `set` by set code; "
            "`sort` is added, name, set, mv, qty or type; `limit` up to 500 (default 100), `offset` to page. "
            "Also returns totals. Nothing here is read from Archidekt."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False},
    )
    async def list_collection(
        query: str | None = None,
        set: str | None = None,  # noqa: A002 - the tool argument is named for the user
        sort: str = "added",
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        sub = _sub()
        rows = service.store.list(
            sub,
            q=_clean(query, 80),
            set_code=_clean(set, 10),
            sort=sort if sort in SORTS else "added",
            limit=max(1, min(int(limit or 100), 500)),
            offset=max(0, int(offset or 0)),
        )
        return {"ok": True, "cards": rows, "count": len(rows), "totals": service.summary(sub)}

    @server.tool(
        name="add_to_collection",
        title="Add cards to my collection",
        description=(
            "Record cards the user owns. Give `cards` (list of {name, set?, collector_number?, quantity?, "
            "finish? (nonfoil|foil|etched), condition? "
            "(NM|LP|MP|HP|DMG), notes?} or plain names), `text` (one "
            "card per line, e.g. '2 Sol Ring (CMR) 472 *F*') or `scan_session` (the id or name of a scan "
            "session: every resolved card in it is added). Names "
            "are matched on Scryfall; cards that could not "
            "be matched come back in `skipped` with suggestions. Does not touch Archidekt."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True},
    )
    async def add_to_collection(
        cards: list[dict[str, Any] | str] | None = None,
        text: str | None = None,
        scan_session: str | None = None,
    ) -> dict[str, Any]:
        sub = _sub()
        items: list[Any] = list(cards or [])
        source = "assistant"
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
                source = "scan"
            out = await service.add(sub, items, source=source)
        except CollectionError as exc:
            return _err(exc)
        return {"ok": True, **out}

    @server.tool(
        name="remove_from_collection",
        title="Remove cards from my collection",
        description=(
            "Take cards out of the user's collection. Give `id` "
            "(a collection row id from list_collection) or "
            "`name` (every printing of that name), and optionally `quantity` to remove only some copies. "
            "Only after the user asked for it. Does not touch Archidekt."
        ),
        annotations={"readOnlyHint": False, "destructiveHint": True, "openWorldHint": False},
    )
    async def remove_from_collection(
        id: str | None = None,  # noqa: A002 - the tool argument is named for the user
        name: str | None = None,
        quantity: int | None = None,
    ) -> dict[str, Any]:
        sub = _sub()
        try:
            if id:
                return {"ok": True, **service.remove(sub, _clean(id, 40), quantity)}
            if not name:
                raise CollectionError("invalid", "give id or name")
            rows = service.find_by_name(sub, name)
            if not rows:
                raise CollectionError("not_found", f"no card named '{_clean(name, 60)}' in your collection")
            removed = []
            left = quantity
            for r in rows:
                if left is not None and left <= 0:
                    break
                take = None if left is None else min(left, int(r["quantity"]))
                removed.append(service.remove(sub, r["id"], take)["removed"])
                if left is not None:
                    left -= take or 0
            return {"ok": True, "removed": removed, "totals": service.summary(sub)}
        except CollectionError as exc:
            return _err(exc)
