"""One Binder (0.7.47, the owner's approved mockup round 1): every card a member owns or wants as
one list of rows, for the Binder page (binder_hub.py).

Owned cards are the member's Binder as last read whole (``binder_records``, binder_prices.py),
grouped by printing and finish; wanted cards are their watches (prices.card_summaries). A printing
that is both is one row. A watch by name alone (any printing) is its own row and says how many
copies of that name the member owns. Each row carries what the page shows and what Select needs:
the binder rows behind it ("rid name" lines, for the Binder's own bulk changes) and its watch id.

A list holds owned and wanted cards: a watched card's list is its watch's (``list_name``), an
owned card's is in ``list_cards`` (prices.put_owned_in_list). Archidekt's binder tags stay
Archidekt's, as a filter.

The rows are worked out once per change of what they come from (Database.watchlist_stamp, which
counts writes to watch_cards, binder_records, list_cards and prices) and kept for a few members,
as the Watchlist's rows are; a view filters, sorts and pages them in memory.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from . import prices as _prices
from .binder_prices import unit_price
from .deckpage import image_url
from .prices import NO_LIST, alert_band, day_of, days_before, pct_change

if TYPE_CHECKING:
    from .prices import PriceService

SHOWS = ("all", "owned", "want", "alerts")
SORTS = ("price", "name", "added", "move")
KEPT = 8  # members whose rows are kept worked out
PAGE = 60  # rows a view draws at once (Load more draws the next)

_kept: dict[str, tuple[Any, list[dict[str, Any]]]] = {}


def _band(row: dict[str, Any], floor: float) -> tuple[float | None, float | None]:
    """The prices the row's alerts fire at: under (drop or below) and over (rise or above)."""
    a = row.get("alerts") or {}
    under, over = alert_band(row.get("added_price"), a.get("drop"), a.get("rise"), floor)
    if a.get("below") is not None:
        under = max(under or 0, a["below"]) or None
    if a.get("above") is not None:
        over = min(over, a["above"]) if over is not None else a["above"]
    return under, over


def _owned_groups(prices: PriceService, sub: str) -> dict[tuple[str, str], dict[str, Any]]:
    """The Binder as last read, one group per printing and finish."""
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for r in prices._rows(
        "SELECT rid, scryfall_id, name, finish, quantity, row_json FROM binder_records WHERE sub = ?", (sub,)
    ):
        try:
            rec = json.loads(r["row_json"] or "{}")
        except ValueError:
            rec = {}
        sid = r["scryfall_id"] or ""
        key = (sid or "name:" + (r["name"] or "").lower(), r["finish"] or "nonfoil")
        g = groups.get(key)
        if g is None:
            g = groups[key] = {
                "name": r["name"] or rec.get("name") or "",
                "set": (rec.get("set") or "").upper(),
                "set_name": rec.get("set_name") or "",
                "cn": rec.get("collector_number") or "",
                "finish": r["finish"] or "nonfoil",
                "scryfall_id": sid,
                "img": image_url(sid, "small") if sid else None,
                "own": 0,
                "rids": [],
                "tags": [],
                "added_at": "",
            }
        g["own"] += int(r["quantity"] or 0)
        g["rids"].append(f"{int(r['rid'])} {g['name']}")
        for t in rec.get("tags") or []:
            if t and t not in g["tags"]:
                g["tags"].append(str(t))
        g["added_at"] = max(g["added_at"], str(rec.get("added_at") or ""))
    return groups


def _owned_prices(prices: PriceService, groups: list[dict[str, Any]]) -> None:
    """Each owned group's price today and its move over a week, from the stored points."""
    ids = sorted({g["scryfall_id"] for g in groups if g["scryfall_id"]})
    since = days_before(day_of(_prices.now()), 16)
    pts: dict[str, list[dict[str, Any]]] = {}
    for i in range(0, len(ids), 500):
        chunk = ids[i : i + 500]
        marks = ",".join("?" * len(chunk))
        for p in prices._rows(
            "SELECT scryfall_id, day, usd, usd_foil, usd_etched, eur, eur_foil FROM price_points "
            f"WHERE scryfall_id IN ({marks}) AND day >= ? ORDER BY day DESC",
            (*chunk, since),
        ):
            pts.setdefault(p["scryfall_id"], []).append(p)
    for g in groups:
        series = pts.get(g["scryfall_id"]) or []
        g["price"], g["currency"], g["change_7d_pct"] = None, "usd", None
        if not series:
            continue
        unit, cur = unit_price(series[0], g["finish"])
        if unit is None:
            continue
        g["price"], g["currency"] = unit, cur
        week = days_before(series[0]["day"], 7)
        before = next((p for p in series if p["day"] <= week), None)
        if before is not None:
            was, was_cur = unit_price(before, g["finish"])
            if was is not None and was_cur == cur:
                g["change_7d_pct"] = pct_change(was, unit)


def _work_out(prices: PriceService, sub: str) -> list[dict[str, Any]]:
    owned = _owned_groups(prices, sub)
    _owned_prices(prices, list(owned.values()))
    in_list = prices.owned_lists(sub)
    floor = float(prices.defaults_of(sub).get("floor") or 0)
    rows: list[dict[str, Any]] = []
    for w in prices.card_summaries(sub):
        row: dict[str, Any] = {
            "key": "w:" + w["watch_id"],
            "watch_id": w["watch_id"],
            "name": w["name"],
            "set": (w.get("set") or "").upper(),
            "set_name": "",
            "cn": w.get("collector_number") or "",
            "finish": w["finish"],
            "scryfall_id": w.get("scryfall_id") or "",
            "img": image_url(w.get("scryfall_id"), "small") if w.get("scryfall_id") else None,
            "any_printing": not w.get("set"),
            "price": w.get("price"),
            "currency": w.get("currency") or "usd",
            "change_7d_pct": w.get("change_7d_pct"),
            "alerts": w.get("alerts") or {},
            "uses_defaults": bool(w.get("uses_defaults")),
            "added_price": w.get("added_price"),
            "list": w.get("list") or NO_LIST,
            "own": 0,
            "own_any": int(w.get("owned") or 0) if not w.get("set") else 0,
            "rids": [],
            "tags": [],
            "added_at": "",
        }
        if w.get("set") and w.get("scryfall_id"):
            g = owned.pop((w["scryfall_id"], w["finish"]), None)
            if g is not None:
                row.update(
                    own=g["own"],
                    rids=g["rids"],
                    tags=g["tags"],
                    set_name=g["set_name"],
                    added_at=g["added_at"],
                )
                if row["price"] is None:
                    row.update(price=g["price"], currency=g["currency"], change_7d_pct=g["change_7d_pct"])
        row["under"], row["over"] = _band(row, floor)
        p = row["price"]
        row["past"] = p is not None and (
            (row["under"] is not None and p <= row["under"]) or (row["over"] is not None and p >= row["over"])
        )
        rows.append(row)
    for (sid, finish), g in owned.items():
        rows.append(
            {
                **g,
                "key": f"o:{sid}:{finish}",
                "watch_id": None,
                "any_printing": False,
                "alerts": {},
                "uses_defaults": False,
                "added_price": None,
                "list": in_list.get((sid, finish), NO_LIST),
                "own_any": 0,
                "under": None,
                "over": None,
                "past": False,
            }
        )
    return rows


def all_rows(prices: PriceService, sub: str) -> list[dict[str, Any]]:
    """Every row of the member's, as last worked out (a copy of the list; the rows are shared)."""
    stamp = (*prices.db.watchlist_stamp(sub), day_of(_prices.now()), prices._limit("watch_max_cards"))
    kept = _kept.get(sub)
    if kept is not None and kept[0] == stamp:
        return list(kept[1])
    rows = _work_out(prices, sub)
    while len(_kept) >= KEPT:
        _kept.pop(next(iter(_kept)))
    _kept[sub] = (stamp, rows)
    return list(rows)


def forget(sub: str | None = None) -> None:
    """Drop kept rows (a test, or a change the stamp does not count, such as saved defaults)."""
    if sub is None:
        _kept.clear()
    else:
        _kept.pop(sub, None)


def counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "all": len(rows),
        "owned": sum(1 for r in rows if r["own"]),
        "want": sum(1 for r in rows if r["watch_id"]),
        "alerts": sum(1 for r in rows if r["past"]),
    }


def list_counts(rows: list[dict[str, Any]]) -> dict[str, dict[str, int]]:
    """Per list: how many cards, how many wanted, how many owned."""
    out: dict[str, dict[str, int]] = {}
    for r in rows:
        if not r["list"]:
            continue
        c = out.setdefault(r["list"], {"all": 0, "want": 0, "owned": 0})
        c["all"] += 1
        c["want"] += 1 if r["watch_id"] else 0
        c["owned"] += 1 if r["own"] else 0
    return out


def view(
    rows: list[dict[str, Any]],
    *,
    show: str = "all",
    list_name: str | None = None,
    q: str = "",
    tag: str = "",
    sort: str = "price",
    descending: bool = True,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """The rows of one view, sorted, and the All/Owned/Want counts of the view before ``show``."""
    picked = rows
    if list_name is not None:
        picked = [r for r in picked if r["list"] == list_name]
    if q.strip():
        needle = q.strip().casefold()
        picked = [r for r in picked if needle in r["name"].casefold()]
    if tag:
        picked = [r for r in picked if tag in r["tags"]]
    seg = counts(picked)
    if show == "owned":
        picked = [r for r in picked if r["own"]]
    elif show == "want":
        picked = [r for r in picked if r["watch_id"]]
    elif show == "alerts":
        picked = [r for r in picked if r["past"]]
    name_key = lambda r: (r["name"].casefold(), r["set"], r["finish"])  # noqa: E731
    if sort == "name":
        picked = sorted(picked, key=name_key, reverse=descending)
    elif sort == "added":
        picked = sorted(picked, key=lambda r: (r["added_at"] or "", name_key(r)), reverse=descending)
    elif sort == "move":
        picked = sorted(
            picked,
            key=lambda r: (r["change_7d_pct"] is None, -abs(r["change_7d_pct"] or 0), name_key(r)),
        )
    else:
        priced = [r for r in picked if r["price"] is not None]
        unpriced = sorted((r for r in picked if r["price"] is None), key=name_key)
        priced.sort(key=name_key)
        priced.sort(key=lambda r: r["price"], reverse=descending)
        picked = priced + unpriced
    return picked, seg


def value(rows: list[dict[str, Any]]) -> float:
    """Dollars of the rows: an owned row counts its copies, a wanted-only row one copy."""
    total = 0.0
    for r in rows:
        if r["price"] is not None and r["currency"] == "usd":
            total += r["price"] * (r["own"] or 1)
    return round(total, 2)
