"""What the Binder's cards and the member's decks are worth (0.7.46, decision C5).

The Binder lives on Archidekt; its value needs every record at once, which Archidekt can't sort or
total by price. So the gateway keeps the member's Binder as last read whole (``binder_records``):
once a day, while the member uses the Binder (opened it in the last PRICED_FOR_DAYS days), the
daily price check reads it whole as the member, at most BINDER_READ_PAGES pages of
BINDER_PAGE_SIZE, while their Archidekt allowance has room, and the gateway's own Binder changes
(adds, steppers, details, removals) keep it current between reads. The same check prices every
printing in it with the watchlist's batched Scryfall call into the shared ``price_points``.

A deck the member owns is noted each time a deck page reads it (``owned_decks``: its printings,
finish and copies, no Archidekt call of its own), and its printings are priced the same way while
it was read in the last PRICED_FOR_DAYS days, so the card viewer has a price history for the cards
in the member's decks too.

Prices are Scryfall's: dollars (TCGplayer) where Scryfall has them, else euros (Cardmarket). A
value adds dollars only; an entry with no dollar price for its finish is counted as unpriced,
never guessed from another finish."""

from __future__ import annotations

import json
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .db import Database

PRICED_FOR_DAYS = 60  # a Binder or deck not opened for this long is no longer read or priced
BINDER_PAGE_SIZE = 200  # records a page in the whole read (Archidekt takes up to 500)
BINDER_READ_PAGES = 50  # pages read at most: 10,000 records
# the price of each finish, in dollars and in euros (as prices.FINISH_KEYS)
_COLUMNS = {"nonfoil": ("usd", "eur"), "foil": ("usd_foil", "eur_foil"), "etched": ("usd_etched", None)}
_POINT = "day, usd, usd_foil, usd_etched, eur, eur_foil"


def unit_price(point: dict[str, Any] | None, finish: str) -> tuple[float | None, str]:
    """A printing's price in its finish from a stored point: dollars, else euros."""
    if not point:
        return None, "usd"
    usd, eur = _COLUMNS.get(finish, _COLUMNS["nonfoil"])
    if point.get(usd) is not None:
        return float(point[usd]), "usd"
    if eur and point.get(eur) is not None:
        return float(point[eur]), "eur"
    return None, "usd"


def _usd_column(finish: str) -> str:
    return _COLUMNS.get(finish, _COLUMNS["nonfoil"])[0]


def _latest(alias: str, key: str) -> str:
    """The point of ``key``'s newest day, joined as ``alias``."""
    return (
        f"LEFT JOIN price_points {alias} ON {alias}.scryfall_id = {key} AND {alias}.day = "
        f"(SELECT MAX(day) FROM price_points q WHERE q.scryfall_id = {key})"
    )


class BinderStore:
    """The member's Binder as last read whole and the printings of their decks, in the database.
    Every method is one short transaction or read; nothing here calls Archidekt or Scryfall."""

    def __init__(self, db: Database):
        self.db = db

    def _rows(self, sql: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.db._lock:
            return [dict(r) for r in self.db._conn.execute(sql, args).fetchall()]

    # -- which Binders are in use ------------------------------------------------------------------
    def seen(self, sub: str, now: float | None = None) -> None:
        """The member opened their Binder: it is read and priced daily from now on."""
        t = int(time.time() if now is None else now)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO binder_reads (sub, seen_at) SELECT ?, ? WHERE EXISTS (SELECT 1 FROM users "
                "WHERE sub = ?) ON CONFLICT(sub) DO UPDATE SET seen_at = excluded.seen_at",
                (sub, t, sub),
            )

    def info(self, sub: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM binder_reads WHERE sub = ?", (sub,))
        return rows[0] if rows else None

    def in_use(self, since: float) -> list[dict[str, Any]]:
        """The members whose Binder was opened at or after ``since``, oldest read first."""
        return self._rows(
            "SELECT * FROM binder_reads WHERE seen_at >= ? ORDER BY coalesce(read_at, 0)", (int(since),)
        )

    def anything(self, since: float) -> bool:
        with self.db._lock:
            return bool(
                self.db._conn.execute(
                    "SELECT EXISTS (SELECT 1 FROM binder_reads WHERE seen_at >= ?) OR EXISTS "
                    "(SELECT 1 FROM owned_decks WHERE read_at >= ?)",
                    (int(since), int(since)),
                ).fetchone()[0]
            )

    # -- the Binder as last read -------------------------------------------------------------------
    @staticmethod
    def _values(sub: str, r: dict[str, Any]) -> tuple[Any, ...]:
        return (
            sub,
            int(r["id"]),
            str(r.get("scryfall_id") or ""),
            str(r.get("name") or ""),
            str(r.get("finish") or "nonfoil"),
            int(r.get("quantity") or 0),
            json.dumps(r, separators=(",", ":")),
        )

    def replace(
        self, sub: str, rows: list[dict[str, Any]], *, complete: bool, now: float | None = None
    ) -> None:
        """The member's whole Binder, just read (rows as collection.row_out makes them)."""
        t = int(time.time() if now is None else now)
        with self.db.tx() as c:
            c.execute("DELETE FROM binder_records WHERE sub = ?", (sub,))
            c.executemany(
                "INSERT OR REPLACE INTO binder_records (sub, rid, scryfall_id, name, finish, quantity, "
                "row_json) VALUES (?,?,?,?,?,?,?)",
                [self._values(sub, r) for r in rows if isinstance(r.get("id"), int)],
            )
            c.execute(
                "INSERT INTO binder_reads (sub, seen_at, read_at, records, complete) VALUES (?,?,?,?,?) "
                "ON CONFLICT(sub) DO UPDATE SET read_at = excluded.read_at, records = excluded.records, "
                "complete = excluded.complete",
                (sub, t, t, len(rows), int(complete)),
            )

    def _held(self, c: Any, sub: str) -> bool:
        return (
            c.execute("SELECT 1 FROM binder_reads WHERE sub = ? AND read_at IS NOT NULL", (sub,)).fetchone()
            is not None
        )

    def put(self, sub: str, row: dict[str, Any] | None) -> None:
        """A record the gateway just wrote (an add, a stepper, a details change), kept as it is now.
        Nothing is kept for a member whose Binder was never read whole."""
        if not row or not isinstance(row.get("id"), int):
            return
        with self.db.tx() as c:
            if not self._held(c, sub):
                return
            if int(row.get("quantity") or 0) <= 0:
                c.execute("DELETE FROM binder_records WHERE sub = ? AND rid = ?", (sub, row["id"]))
                return
            old = c.execute(
                "SELECT row_json FROM binder_records WHERE sub = ? AND rid = ?", (sub, row["id"])
            ).fetchone()
            if old is not None:  # what the write's answer left out stays as last read
                kept = json.loads(old[0])
                row = {**kept, **{k: v for k, v in row.items() if v not in (None, "", [])}}
            c.execute(
                "INSERT OR REPLACE INTO binder_records (sub, rid, scryfall_id, name, finish, quantity, "
                "row_json) VALUES (?,?,?,?,?,?,?)",
                self._values(sub, row),
            )

    def drop(self, sub: str, rid: int) -> None:
        with self.db.tx() as c:
            c.execute("DELETE FROM binder_records WHERE sub = ? AND rid = ?", (sub, int(rid)))

    def set_uid(self, sub: str, rid: int, uid: str) -> None:
        """The Scryfall id the daily check found for a record that carried none."""
        with self.db.tx() as c:
            c.execute(
                "UPDATE binder_records SET scryfall_id = ? WHERE sub = ? AND rid = ? AND scryfall_id = ''",
                (uid, sub, int(rid)),
            )

    # -- what the daily check prices ---------------------------------------------------------------
    def to_price(self, since: float, *, sub: str | None = None) -> list[dict[str, Any]]:
        """Every record of the Binders in use and every printing of the decks read since ``since``,
        as rows the price check identifies (uid, else set and number, else name). ``sub``: that
        member's Binder only."""
        out: list[dict[str, Any]] = []
        for r in self._rows(
            "SELECT b.sub, b.rid, b.scryfall_id, b.row_json FROM binder_records b JOIN binder_reads s "
            "ON s.sub = b.sub WHERE s.seen_at >= ?" + (" AND b.sub = ?" if sub else ""),
            (int(since), sub) if sub else (int(since),),
        ):
            row = json.loads(r["row_json"])
            out.append(
                {
                    "sub": r["sub"],
                    "rid": r["rid"],
                    "uid": r["scryfall_id"],
                    "name": str(row.get("name") or ""),
                    "set": str(row.get("set") or ""),
                    "cn": str(row.get("collector_number") or ""),
                }
            )
        if sub:
            return out
        for d in self._rows("SELECT cards_json FROM owned_decks WHERE read_at >= ?", (int(since),)):
            out += [c for c in json.loads(d["cards_json"] or "[]") if isinstance(c, dict)]
        return out

    def forget_old(self, since: float) -> int:
        """Decks not read and Binders not opened since ``since`` are no longer priced: their
        copies go (the Binder's is read whole again when it is next opened and priced)."""
        with self.db.tx() as c:
            n = c.execute("DELETE FROM owned_decks WHERE read_at < ?", (int(since),)).rowcount
            old = [r[0] for r in c.execute("SELECT sub FROM binder_reads WHERE seen_at < ?", (int(since),))]
            for sub in old:
                c.execute("DELETE FROM binder_records WHERE sub = ?", (sub,))
            n += c.execute("DELETE FROM binder_reads WHERE seen_at < ?", (int(since),)).rowcount
            return n

    # -- the member's decks ------------------------------------------------------------------------
    def note_deck(
        self, sub: str, deck_id: str, name: str, rows: list[dict[str, Any]], now: float | None = None
    ) -> None:
        t = int(time.time() if now is None else now)
        with self.db.tx() as c:
            c.execute(
                "INSERT INTO owned_decks (sub, deck_id, deck_name, cards_json, read_at) "
                "SELECT ?,?,?,?,? WHERE EXISTS (SELECT 1 FROM users WHERE sub = ?) "
                "ON CONFLICT(sub, deck_id) DO UPDATE SET deck_name = excluded.deck_name, "
                "cards_json = excluded.cards_json, read_at = excluded.read_at",
                (sub, str(deck_id), name, json.dumps(rows, separators=(",", ":")), t, sub),
            )

    # -- reading prices ----------------------------------------------------------------------------
    def latest(self, ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        """Each printing's newest stored point (one query, whatever the number of ids)."""
        want = sorted({i for i in ids if i})
        out: dict[str, dict[str, Any]] = {}
        for i in range(0, len(want), 500):
            chunk = want[i : i + 500]
            marks = ",".join("?" * len(chunk))
            for r in self._rows(
                f"SELECT p.scryfall_id, p.{_POINT.replace(', ', ', p.')} FROM price_points p "
                f"WHERE p.scryfall_id IN ({marks}) AND p.day = "
                "(SELECT MAX(day) FROM price_points q WHERE q.scryfall_id = p.scryfall_id)",
                tuple(chunk),
            ):
                out[r.pop("scryfall_id")] = r
        return out

    def with_prices(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """``rows`` (row_out shape) with ``price``: the unit price, its currency, the row's value
        (price x copies) and the day, or None when Scryfall has no price for that finish."""
        points = self.latest(r.get("scryfall_id") or "" for r in rows)
        for r in rows:
            point = points.get(r.get("scryfall_id") or "")
            unit, cur = unit_price(point, r.get("finish") or "nonfoil")
            r["price"] = (
                None
                if unit is None
                else {
                    "unit": unit,
                    "currency": cur,
                    "value": round(unit * int(r.get("quantity") or 0), 2),
                    "day": point["day"] if point else None,
                }
            )
        return rows

    def value(self, sub: str) -> dict[str, Any] | None:
        """What the Binder as last read is worth today: the dollars of every entry Scryfall prices
        in its finish, the copies, how many entries have no dollar price, the newest price day and
        when the Binder was read. None before its first whole read."""
        info = self.info(sub)
        if info is None or info.get("read_at") is None:
            return None
        usd = 0.0
        copies = entries = unpriced = 0
        day = ""
        for r in self._rows(
            "SELECT b.finish, b.quantity, p.day, p.usd, p.usd_foil, p.usd_etched FROM binder_records b "
            + _latest("p", "b.scryfall_id")
            + " WHERE b.sub = ?",
            (sub,),
        ):
            entries += 1
            copies += int(r["quantity"] or 0)
            unit = r.get(_usd_column(r["finish"]))
            if unit is None:
                unpriced += 1
                continue
            usd += float(unit) * int(r["quantity"] or 0)
            day = max(day, r["day"] or "")
        return {
            "usd": round(usd, 2),
            "copies": copies,
            "entries": entries,
            "unpriced": unpriced,
            "day": day or None,
            "read_at": info["read_at"],
            "complete": bool(info["complete"]),
        }

    def listing(
        self, sub: str, *, q: str = "", offset: int = 0, limit: int = 24, descending: bool = True
    ) -> dict[str, Any]:
        """The Binder as last read sorted by each entry's dollar price in its finish (entries with
        none last, then by name), ``limit`` rows from ``offset``, each with its ``price``."""
        price = "CASE b.finish WHEN 'foil' THEN p.usd_foil WHEN 'etched' THEN p.usd_etched ELSE p.usd END"
        where, args = "b.sub = ?", [sub]
        if q.strip():
            where += " AND b.name LIKE ? ESCAPE '\\'"
            text = q.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            args.append(f"%{text}%")
        with self.db._lock:
            total = int(
                self.db._conn.execute(
                    f"SELECT COUNT(*) FROM binder_records b WHERE {where}", args
                ).fetchone()[0]
            )
        rows = self._rows(
            "SELECT b.row_json FROM binder_records b "
            + _latest("p", "b.scryfall_id")
            + f" WHERE {where} ORDER BY ({price}) IS NULL, ({price}) {'DESC' if descending else 'ASC'}, "
            "b.name COLLATE NOCASE, b.rid LIMIT ? OFFSET ?",
            (*args, int(limit), int(offset)),
        )
        return {"rows": self.with_prices([json.loads(r["row_json"]) for r in rows]), "total": total}

    def history(self, scryfall_id: str, since: str) -> list[dict[str, Any]]:
        """A printing's stored points from the day ``since`` on, oldest first."""
        if not scryfall_id:
            return []
        return self._rows(
            f"SELECT {_POINT} FROM price_points WHERE scryfall_id = ? AND day >= ? ORDER BY day",
            (scryfall_id, since),
        )

    def owned_where(self, sub: str, scryfall_id: str) -> list[str]:
        """Where the member keeps this printing: "binder" and/or "decks"."""
        if not scryfall_id:
            return []
        out = []
        with self.db._lock:
            if self.db._conn.execute(
                "SELECT 1 FROM binder_records WHERE sub = ? AND scryfall_id = ? LIMIT 1", (sub, scryfall_id)
            ).fetchone():
                out.append("binder")
            for (cards,) in self.db._conn.execute("SELECT cards_json FROM owned_decks WHERE sub = ?", (sub,)):
                if f'"{scryfall_id}"' in cards and any(
                    isinstance(c, dict) and c.get("uid") == scryfall_id for c in json.loads(cards or "[]")
                ):
                    out.append("decks")
                    break
        return out
