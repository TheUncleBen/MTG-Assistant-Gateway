"""A member's account-wide Archidekt lists (0.7.43, Archidekt parity release 2): category
templates, personal default categories, the color tag list, pinned cards and custom cards.

Reading a list is a plain read. Every change is a proposal of kind ``lists`` with one review row
per change, approved like any other write: semi-auto may apply a small one (``low``), and a
delete (a template, a color tag, a custom card, or all pins at once) waits for the member's own
press in every approval mode (``destructive``, modes.MEMBER_ONLY). The member's own press on the
My lists page (lists_page.py) makes the same proposal and applies it at once, as a hand edit
does. An apply reads the list again first, stops with nothing sent when a change no longer fits
it ("needs a look"), keeps the entries it changes as they were just before (the result's
``before``, shown on the change's page under History) and reads the list back afterwards.

The requests are archidekt_account.py's, which are archidekt.com's own (REQUESTS.md); none has
been run against archidekt.com yet, so the read-back decides what the member is told."""

from __future__ import annotations

import asyncio
import secrets
import threading
from typing import TYPE_CHECKING, Any

from .archidekt_account import CUSTOM_CARD_FIELDS, HEX_COLOR_RE
from .decklist import clean_text
from .decks import DeckError, _row, needs_look_error

if TYPE_CHECKING:
    from .decks import DeckService

LISTS_TARGET = "lists"  # deck_id of a lists proposal: it is about the account, not one deck
LISTS_NAME = "Your Archidekt lists"
KINDS = ("templates", "default_categories", "color_tags", "pins", "custom_cards")
# What each list is called in a sentence, and the operations each takes
NOUNS = {
    "templates": "category templates",
    "default_categories": "default categories",
    "color_tags": "color tags",
    "pins": "pinned cards",
    "custom_cards": "custom cards",
}
OPS = {
    "templates": ("create", "edit", "delete"),
    "default_categories": ("set", "clear"),
    "color_tags": ("create", "edit", "delete"),
    "pins": ("pin", "edit", "unpin", "remove_all"),
    "custom_cards": ("create", "edit", "delete"),
}
# Waits for the member's own press in every approval mode
DELETES = frozenset(
    {
        ("templates", "delete"),
        ("color_tags", "delete"),
        ("custom_cards", "delete"),
        ("pins", "remove_all"),
        # they take entries out of a list as well (security review 0.7.44)
        ("pins", "unpin"),
        ("default_categories", "clear"),
    }
)
# a Premier template, or default categories naming one, could file a card into the commander zone
# when it is added with no category of its own (security review 0.7.44): never an assistant's
PREMIER_NOTE = (
    "Premier can make a category's cards count as the deck's commanders, so this waits for your own "
    "press in every approval mode."
)
MAX_CHANGES = 30  # changes in one proposal
TEMPLATE_NAME_MAX = 60
TAG_NAME_MAX = 32  # Archidekt's own limit (its color tag form)
DEFAULT_TAG_COLOR = "#fa890d"  # Archidekt's own default (its color tag form)
PIN_NOTES_MAX = 256  # the site's notes box
PIN_MAX_COPIES = 100
MAX_PINS = 150  # the site's own count ("7 / 150")
FINISHES = ("Normal", "Foil", "Etched")
CUSTOM_TEXT_MAX = 2000
CUSTOM_SHORT_MAX = 200
# A custom card's picture is set only by the member's own upload on the page, never by a change
# an assistant proposes
PICTURE_FIELDS = ("frontImageUrl", "backImageUrl")
CUSTOM_LONG_FIELDS = ("frontText", "backText")
DEFAULT_PAGE_SIZE = 100  # Archidekt's own page of default categories


def _err(text: str) -> DeckError:
    return DeckError("invalid", text)


def _name(value: Any, what: str, limit: int) -> str:
    name = clean_text(str(value or ""))
    if not name:
        raise _err(f"{what} is required")
    if len(name) > limit:
        raise _err(f"{what} must be at most {limit} characters")
    return name


def _bool(value: Any, what: str, default: bool | None = None) -> bool:
    if value is None and default is not None:
        return default
    if not isinstance(value, bool):
        raise _err(f"{what} must be true or false")
    return value


def _color(value: Any, default: str | None = None) -> str:
    if value is None and default is not None:
        return default
    text = str(value or "").strip()
    if not HEX_COLOR_RE.fullmatch(text):
        raise _err("color must be a hex color like #fa890d")
    return text.lower()


def _same(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


# -- reading ----------------------------------------------------------------------------------


def _template(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": t.get("id"),
        "name": clean_text(str(t.get("name") or "")),
        "in_deck": bool(t.get("includedInDeck")),
        "in_price": bool(t.get("includedInPrice")),
        "premier": bool(t.get("isPremier")),
    }


def _card_parts(card: Any) -> dict[str, Any]:
    card = card if isinstance(card, dict) else {}
    oracle = card.get("oracleCard") if isinstance(card.get("oracleCard"), dict) else {}
    edition = card.get("edition") if isinstance(card.get("edition"), dict) else {}
    return {
        "card_id": card.get("id") if isinstance(card.get("id"), int) else None,
        "name": clean_text(str(oracle.get("name") or card.get("name") or "")),
        "set_code": str(edition.get("editioncode") or "").upper(),
        "set_name": clean_text(str(edition.get("editionname") or "")),
        "collector_number": str(card.get("collectorNumber") or ""),
        "uid": str(card.get("uid") or ""),  # the Scryfall id: the My lists page's thumbnail
    }


def _pin(p: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": p.get("id"),
        **_card_parts(p.get("card")),
        "notes": str(p.get("notes") or ""),
        "finish": str(p.get("modifier") or "Normal"),
        "quantity": int(p.get("quantity") or 1),
    }


def _default_row(c: dict[str, Any]) -> dict[str, Any]:
    oracle = c.get("oracleCard") if isinstance(c.get("oracleCard"), dict) else {}
    oid = c.get("oracleCardId", oracle.get("id"))
    names = c.get("globalCategories") if isinstance(c.get("globalCategories"), list) else []
    return {
        "oracle_id": oid if isinstance(oid, int) else None,
        "name": clean_text(str(c.get("name") or oracle.get("name") or "")),
        "categories": [clean_text(str(n)) for n in names if str(n).strip()],
    }


def _custom(c: dict[str, Any]) -> dict[str, Any]:
    out = {k: c.get(k) for k in CUSTOM_CARD_FIELDS}
    out["id"] = c.get("id")
    out["name"] = clean_text(str(c.get("frontName") or ""))
    if c.get("backName"):
        out["name"] += " // " + clean_text(str(c["backName"]))
    return out


async def _get(decks: DeckService, sub: str, fn: Any) -> Any:
    return await decks._call(sub, fn)  # an Archidekt refusal comes back as a DeckError


# Each list's total as last read, per member: the My lists page's tab bar and the Account page's
# panel show the counts they know without a read of their own (one Archidekt read per tab view).
# A list never read yet has no count there; a custom card search does not count (it is a subset).
_COUNTS: dict[str, dict[str, int]] = {}


def counts(sub: str) -> dict[str, int]:
    """The totals of ``sub``'s lists as last read, by kind (only the kinds read so far)."""
    return dict(_COUNTS.get(sub) or {})


def forget_counts(sub: str) -> None:
    _COUNTS.pop(sub, None)


async def read(decks: DeckService, sub: str, kind: str, *, page: int = 1, search: str = "") -> dict[str, Any]:
    """One list as it is on Archidekt now, in the gateway's own words."""
    if kind not in KINDS:
        raise _err(f"list must be one of: {', '.join(KINDS)}")
    got = await _read(decks, sub, kind, page=page, search=search)
    if kind != "custom_cards" or not search:
        _COUNTS.setdefault(sub, {})[kind] = int(got["total"])
    return got


async def _read(decks: DeckService, sub: str, kind: str, *, page: int, search: str) -> dict[str, Any]:
    client = decks.client
    if kind == "templates":
        rows = await _get(decks, sub, lambda token, *_: client.templates(token))
        items = [_template(t) for t in rows]
        return {"list": kind, "items": items, "total": len(items)}
    if kind == "default_categories":
        body = await _get(decks, sub, lambda token, *_: client.default_categories(token, page))
        items = [_default_row(c) for c in body.get("cards") or [] if isinstance(c, dict)]
        cats = body.get("categories") if isinstance(body.get("categories"), list) else []
        return {
            "list": kind,
            "items": items,
            "total": int(body.get("total") or len(items)),
            "page": max(1, int(page)),
            "more": bool(body.get("next")),
            "templates": [_template(t)["name"] for t in cats if isinstance(t, dict)],
            "premier": [
                _template(t)["name"] for t in cats if isinstance(t, dict) and _template(t)["premier"]
            ],
        }
    if kind == "color_tags":
        rows = await _get(decks, sub, lambda token, *_: client.color_tags(token))
        items = [
            {
                "id": t.get("id"),
                "name": clean_text(str(t.get("name") or "")),
                "color": str(t.get("color") or ""),
            }
            for t in rows
        ]
        return {"list": kind, "items": items, "total": len(items)}
    if kind == "pins":
        rows = await _get(decks, sub, lambda token, *_: client.pins(token))
        items = [_pin(p) for p in rows]
        return {"list": kind, "items": items, "total": len(items), "limit": MAX_PINS}
    body = await _get(decks, sub, lambda token, *_: client.custom_cards(token, search, page))
    items = [_custom(c) for c in body.get("results") or [] if isinstance(c, dict)]
    return {
        "list": kind,
        "items": items,
        "total": int(body.get("count") or len(items)),
        "page": max(1, int(page)),
        "more": bool(body.get("next")),
    }


# -- one change, checked against the list as it is now ------------------------------------------


def _one(items: list[dict[str, Any]], name: Any, what: str, *, key: str = "name") -> dict[str, Any]:
    want = clean_text(str(name or ""))
    if not want:
        raise _err(f"name the {what}")
    hits = [i for i in items if _same(str(i.get(key) or ""), want)]
    if not hits:
        listed = ", ".join(f"“{i[key]}”" for i in items[:20]) or "none"
        raise DeckError("not_found", f"There is no {what} called “{want}” (yours: {listed}).")
    return hits[0]


def _flags_text(t: dict[str, Any]) -> str:
    said = [
        w
        for k, w in (
            ("in_deck", "counts in the deck"),
            ("in_price", "counts in the price"),
            ("premier", "Premier"),
        )
        if t[k]
    ]
    return ", ".join(said) if said else "not in the deck or the price"


async def _resolve(decks: DeckService, sub: str, c: dict[str, Any]) -> dict[str, Any]:
    name = _name(c.get("card"), "card", 200)
    try:
        return await decks._call(
            sub,
            lambda token, *_: decks.client.resolve_card(
                token,
                name,
                set_code=c.get("set_code") or None,
                collector_number=str(c.get("collector_number") or "") or None,
            ),
        )
    except DeckError as exc:
        if exc.kind == "not_found":
            raise DeckError("not_found", f"Archidekt has no card called “{name}”.") from exc
        raise


async def _plan(  # noqa: C901, PLR0912, PLR0915
    decks: DeckService,
    sub: str,
    kind: str,
    changes: list[Any],
    now: dict[str, Any],
    picture: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(ops, review rows) for ``changes`` against the list as it is now (``now`` from read).
    ``picture``: a custom card picture the member uploaded on the page (set_picture), never
    anything an assistant sent."""
    items = now["items"]
    ops: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(op: dict[str, Any], text: str, **extra: Any) -> None:
        key = (op["op"], str(op.get("id") or op.get("oracle_id") or op.get("name") or ""))
        if op["op"] != "pin" and key in seen:
            raise _err(f"the same {NOUNS[kind][:-1]} is changed twice: give one change per entry")
        seen.add(key)
        ops.append(op)
        destructive = (kind, op["op"]) in DELETES or bool(extra.pop("premier", False))
        rows.append(_row("list", list=kind, op=op["op"], text=text, destructive=destructive or None, **extra))

    for c in changes:
        if not isinstance(c, dict):
            raise _err("each change must be an object with an op")
        op = str(c.get("op") or "")
        if op not in OPS[kind]:
            raise _err(f"op for {NOUNS[kind]} must be one of: {', '.join(OPS[kind])}")
        if kind == "templates":
            if op == "create":
                name = _name(c.get("name"), "name", TEMPLATE_NAME_MAX)
                if any(_same(t["name"], name) for t in items):
                    raise _err(f"You already have a template called “{name}”.")
                t = {
                    "name": name,
                    "in_deck": _bool(c.get("in_deck"), "in_deck", True),
                    "in_price": _bool(c.get("in_price"), "in_price", True),
                    "premier": _bool(c.get("premier"), "premier", False),
                }
                add(
                    {"op": op, **t},
                    f"New category template “{name}” ({_flags_text(t)})",
                    **({"premier": True, "note": PREMIER_NOTE} if t["premier"] else {}),
                )
                continue
            t = _one(items, c.get("name"), "category template")
            if op == "delete":
                add(
                    {"op": op, "id": t["id"], "name": t["name"]},
                    f"Delete the category template “{t['name']}”",
                    note=(
                        "“" + t["name"] + "” is also removed from every card's default categories. "
                        "Cards already in decks keep their categories."
                    ),
                )
                continue
            new = {
                "name": _name(c["new_name"], "new_name", TEMPLATE_NAME_MAX)
                if c.get("new_name")
                else t["name"],
                "in_deck": _bool(c.get("in_deck"), "in_deck", t["in_deck"]),
                "in_price": _bool(c.get("in_price"), "in_price", t["in_price"]),
                "premier": _bool(c.get("premier"), "premier", t["premier"]),
            }
            if not _same(new["name"], t["name"]) and any(_same(x["name"], new["name"]) for x in items):
                raise _err(f"You already have a template called “{new['name']}”.")
            said = [f"renamed “{new['name']}”"] if new["name"] != t["name"] else []
            said += [
                f"{w} {'on' if new[k] else 'off'}"
                for k, w in (
                    ("in_deck", "Counts in the deck"),
                    ("in_price", "Counts in the price"),
                    ("premier", "Premier"),
                )
                if new[k] != t[k]
            ]
            if not said:
                raise _err(f"That changes nothing on the template “{t['name']}”.")
            add(
                {"op": op, "id": t["id"], "was": t, **new},
                f"Category template “{t['name']}”: " + ", ".join(said),
                note=(
                    "Cards with this name in their default categories follow the change. "
                    "Cards already in decks don't change."
                    + (" " + PREMIER_NOTE if new["premier"] and not t["premier"] else "")
                ),
                premier=new["premier"] and not t["premier"],
            )
        elif kind == "default_categories":
            printing = await _resolve(decks, sub, c)
            oid = printing.get("oracle_id")
            if not isinstance(oid, int):
                raise DeckError("unavailable", f"Archidekt did not say which card “{printing['name']}” is.")
            name = printing["name"]
            if op == "clear":
                add(
                    {"op": op, "oracle_id": oid, "name": name, "categories": []},
                    f"Clear the default categories of “{name}”",
                )
                continue
            raw = c.get("categories")
            if not isinstance(raw, list) or not raw:
                raise _err(
                    "categories must list at least one of your category templates (the first is the main "
                    "one); use clear to remove them all"
                )
            names = now["templates"]
            picked: list[str] = []
            for n in raw:
                hit = next((t for t in names if _same(t, clean_text(str(n)))), None)
                if hit is None:
                    raise DeckError(
                        "not_found",
                        f"“{clean_text(str(n))}” is not one of your category templates. Default categories "
                        f"can only be template names (yours: {', '.join(names) or 'none'}).",
                    )
                if hit not in picked:
                    picked.append(hit)
            premier = [n for n in picked if any(_same(n, x) for x in now.get("premier") or [])]
            add(
                {"op": op, "oracle_id": oid, "name": name, "categories": picked},
                f"Default categories of “{name}”: " + ", ".join(picked) + " (main: " + picked[0] + ")",
                **({"premier": True, "note": f"{', '.join(premier)}: " + PREMIER_NOTE} if premier else {}),
            )
        elif kind == "color_tags":
            if op == "create":
                name = _name(c.get("name"), "name", TAG_NAME_MAX)
                if any(_same(t["name"], name) for t in items):
                    raise _err(f"You already have a color tag called “{name}”.")
                color = _color(c.get("color"), DEFAULT_TAG_COLOR)
                add({"op": op, "name": name, "color": color}, f"New color tag “{name}” ({color})")
                continue
            t = _one(items, c.get("name"), "color tag")
            if op == "delete":
                add(
                    {"op": op, "id": t["id"], "name": t["name"]},
                    f"Delete the color tag “{t['name']}”",
                    note="Cards already tagged keep their color and name; the tag only leaves your list.",
                )
                continue
            name = _name(c["new_name"], "new_name", TAG_NAME_MAX) if c.get("new_name") else t["name"]
            color = _color(c.get("color"), t["color"].lower())
            if not _same(name, t["name"]) and any(_same(x["name"], name) for x in items):
                raise _err(f"You already have a color tag called “{name}”.")
            if name == t["name"] and color == t["color"].lower():
                raise _err(f"That changes nothing on the color tag “{t['name']}”.")
            said = ([f"renamed “{name}”"] if name != t["name"] else []) + (
                [f"color {t['color']} -> {color}"] if color != t["color"].lower() else []
            )
            add(
                {"op": op, "id": t["id"], "was": t, "name": name, "color": color},
                f"Color tag “{t['name']}”: " + ", ".join(said),
                note="Cards already tagged keep their old name and color.",
            )
        elif kind == "pins":
            if op == "remove_all":
                if not items:
                    raise _err("You have no pinned cards.")
                add({"op": op}, f"Remove all {len(items)} pinned card{'s' if len(items) != 1 else ''}")
                continue
            if op == "pin":
                printing = await _resolve(decks, sub, c)
                finish = str(c.get("finish") or "Normal")
                if finish not in FINISHES:
                    raise _err("finish must be Normal, Foil or Etched")
                qty = _copies(c.get("quantity"), 1)
                notes = _notes(c.get("notes"), "")
                if len(items) + sum(1 for o in ops if o["op"] == "pin") >= MAX_PINS:
                    raise _err(f"You already have {len(items)} pinned cards, Archidekt's most is {MAX_PINS}.")
                where = (
                    f" ({printing['set_code'].upper()} {printing['collector_number']})"
                    if printing.get("set_code")
                    else ""
                )
                add(
                    {
                        "op": op,
                        "card_id": printing["id"],
                        "name": printing["name"],
                        "finish": finish,
                        "quantity": qty,
                        "notes": notes,
                    },
                    f"Pin “{printing['name']}”{where}"
                    + (f", {qty} copies" if qty != 1 else "")
                    + (f", {finish}" if finish != "Normal" else ""),
                )
                continue
            p = _pinned(items, c)
            if op == "unpin":
                add({"op": op, "id": p["id"], "name": p["name"]}, f"Unpin “{p['name']}”")
                continue
            new = {
                "quantity": _copies(c.get("quantity"), p["quantity"]),
                "notes": _notes(c.get("notes"), p["notes"]),
                "finish": str(c.get("finish") or p["finish"]),
                "card_id": p["card_id"],
            }
            if new["finish"] not in FINISHES:
                raise _err("finish must be Normal, Foil or Etched")
            if c.get("set_code"):
                printing = await _resolve(decks, sub, {**c, "card": p["name"]})
                new["card_id"] = printing["id"]
            said = []
            if new["quantity"] != p["quantity"]:
                said.append(f"copies {p['quantity']} -> {new['quantity']}")
            if new["notes"] != p["notes"]:
                said.append(f"note “{new['notes']}”" if new["notes"] else "note cleared")
            if new["finish"] != p["finish"]:
                said.append(f"finish {p['finish']} -> {new['finish']}")
            if new["card_id"] != p["card_id"]:
                said.append("another printing")
            if not said:
                raise _err(f"That changes nothing on the pin “{p['name']}”.")
            add(
                {"op": op, "id": p["id"], "name": p["name"], "was": p, **new},
                f"Pin “{p['name']}”: " + ", ".join(said),
            )
        else:  # custom cards
            if op == "create":
                fields = _custom_fields(c.get("fields"), {})
                add(
                    {"op": op, "fields": fields, "name": fields["frontName"]},
                    f"New custom card “{fields['frontName']}”",
                )
                continue
            found = await _custom_by_name(decks, sub, c)
            if op == "delete":
                add(
                    {"op": op, "id": found["id"], "name": found["name"]},
                    f"Delete the custom card “{found['name']}”",
                    note="It is also blanked in every deck that holds it, on Archidekt.",
                )
                continue
            fields = _custom_fields(c.get("fields") or ({} if picture else None), found)
            fields.update(picture or {})
            changed = [
                k
                for k in CUSTOM_CARD_FIELDS
                if (k not in PICTURE_FIELDS or k in (picture or {})) and fields.get(k) != found.get(k)
            ]
            if not changed:
                raise _err(f"That changes nothing on the custom card “{found['name']}”.")
            add(
                {
                    "op": op,
                    "id": found["id"],
                    "name": found["name"],
                    "fields": fields,
                    "was": found,
                    "pictures": sorted(picture or {}),
                },
                f"Custom card “{found['name']}”: " + ", ".join(_FIELD_WORDS.get(k, k) for k in changed),
            )
    if not ops:
        raise _err("nothing to change: give at least one change")
    return ops, rows


_FIELD_WORDS = {
    "cmc": "mana value",
    "frontName": "name",
    "frontManaCost": "mana cost",
    "frontText": "rules text",
    "frontPower": "power",
    "frontToughness": "toughness",
    "frontLoyalty": "loyalty",
    "frontTypes": "types",
    "frontSubTypes": "subtypes",
    "frontSuperTypes": "supertypes",
    "frontArtistName": "artist",
    "hasBack": "back face",
    "backName": "back name",
    "backManaCost": "back mana cost",
    "backText": "back rules text",
    "backPower": "back power",
    "backToughness": "back toughness",
    "backLoyalty": "back loyalty",
    "backTypes": "back types",
    "backSubTypes": "back subtypes",
    "backSuperTypes": "back supertypes",
    "backArtistName": "back artist",
    "setCode": "set code",
    "collectorNumber": "collector number",
}


def _copies(value: Any, default: int) -> int:
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= PIN_MAX_COPIES:
        raise _err(f"quantity must be a whole number from 1 to {PIN_MAX_COPIES} (unpin to remove it)")
    return value


def _notes(value: Any, default: str) -> str:
    if value is None:
        return default
    text = clean_text(str(value))
    if len(text) > PIN_NOTES_MAX:
        raise _err(f"notes must be at most {PIN_NOTES_MAX} characters")
    return text


def _pinned(items: list[dict[str, Any]], c: dict[str, Any]) -> dict[str, Any]:
    pid = c.get("pin_id")
    if pid is not None:
        hit = next((p for p in items if p["id"] == pid), None)
        if hit is None:
            raise DeckError("not_found", "That pin is not in your pinned cards.")
        return hit
    want = clean_text(str(c.get("card") or ""))
    hits = [p for p in items if _same(p["name"], want)]
    if not hits:
        raise DeckError("not_found", f"“{want}” is not in your pinned cards.")
    if len(hits) > 1:
        raise _err(
            f"“{want}” is pinned {len(hits)} times (pin ids "
            + ", ".join(str(p["id"]) for p in hits)
            + "): give pin_id to pick one"
        )
    return hits[0]


async def _custom_by_name(decks: DeckService, sub: str, c: dict[str, Any]) -> dict[str, Any]:
    want = clean_text(str(c.get("name") or ""))
    if not want:
        raise _err("name the custom card")
    found = await read(decks, sub, "custom_cards", search=want.split(" // ")[0])
    hits = [x for x in found["items"] if _same(x["name"], want) or _same(str(x.get("frontName") or ""), want)]
    if not hits:
        raise DeckError("not_found", f"You have no custom card called “{want}”.")
    if len(hits) > 1:
        raise _err(f"You have {len(hits)} custom cards called “{want}”: change it on the My lists page.")
    return hits[0]


def _custom_fields(raw: Any, base: dict[str, Any]) -> dict[str, Any]:
    """A custom card's text fields: ``base`` (the card as it is, for an edit) with ``raw`` on
    top. Pictures are never taken from ``raw``."""
    if raw == {} and base:  # a new picture alone (set_picture)
        return {k: base.get(k) for k in CUSTOM_CARD_FIELDS}
    if not isinstance(raw, dict) or not raw:
        raise _err(
            "fields must give the card's fields ("
            + ", ".join(k for k in CUSTOM_CARD_FIELDS if k not in PICTURE_FIELDS)
            + ")"
        )
    unknown = [k for k in raw if k not in CUSTOM_CARD_FIELDS]
    if unknown:
        raise _err(f"unknown custom card field {unknown[0]!r}")
    if any(k in PICTURE_FIELDS for k in raw):
        raise _err("A custom card's picture is uploaded on the My lists page only, by the member.")
    out = {k: base.get(k) for k in CUSTOM_CARD_FIELDS}
    for k, v in raw.items():
        if k == "hasBack":
            out[k] = _bool(v, "hasBack")
        elif k == "cmc":
            if isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 1000:
                raise _err("cmc must be a number from 0")
            out[k] = v
        elif v is None:
            out[k] = None
        else:
            text = str(v).replace("\r\n", "\n").strip() if k in CUSTOM_LONG_FIELDS else clean_text(str(v))
            if len(text) > (CUSTOM_TEXT_MAX if k in CUSTOM_LONG_FIELDS else CUSTOM_SHORT_MAX):
                raise _err(f"{k} is too long")
            out[k] = text or None
    if not out.get("frontName"):
        raise _err("frontName is required: the card's name")
    if not any(out.get(k) for k in ("frontTypes", "frontSubTypes", "frontSuperTypes")):
        raise _err("a custom card needs at least one type (frontTypes, such as Creature)")
    if out.get("backName") and not out.get("hasBack"):
        out["hasBack"] = True
    return out


# -- the proposal -------------------------------------------------------------------------------


def risk_of(rows: list[dict[str, Any]] | None, *, max_rows: int) -> tuple[str, str]:
    """The approval tier of a lists proposal (modes.risk_of hands ``lists`` proposals here)."""
    rows = rows or []
    if any(r.get("destructive") and (r.get("note") or "").endswith(PREMIER_NOTE) for r in rows):
        return (
            "destructive",
            "a Premier category in your Archidekt lists; an assistant never applies this by itself",
        )
    if any(r.get("destructive") for r in rows):
        return "destructive", "deletes from your Archidekt lists; an assistant never applies this by itself"
    if len(rows) > max_rows:
        return "high", f"changes {len(rows)} entries, more than the {max_rows} a low-risk change may have"
    return "low", f"{len(rows)} change{'s' if len(rows) != 1 else ''} to your Archidekt lists, no deletes"


async def propose(
    decks: DeckService, sub: str, kind: str, changes: Any, *, picture: dict[str, str] | None = None
) -> dict[str, Any]:
    """Check ``changes`` against the list as it is now and store them as one waiting proposal.
    ``picture`` only from set_picture: the member's own upload on the page."""
    if kind not in KINDS:
        raise _err(f"list must be one of: {', '.join(KINDS)}")
    if not isinstance(changes, list) or not changes:
        raise _err("changes must be a list of at least one change")
    if len(changes) > MAX_CHANGES:
        raise _err(f"at most {MAX_CHANGES} changes in one proposal")
    decks._room_for_proposal(sub)
    now = await read(decks, sub, kind) if kind != "custom_cards" else {"items": []}
    ops, rows = await _plan(decks, sub, kind, changes, now, picture)
    pid = secrets.token_urlsafe(12)
    decks._save_proposal(
        {
            "id": pid,
            "owner_sub": sub,
            "kind": "lists",
            "deck_id": LISTS_TARGET,
            "deck_name": LISTS_NAME,
            "baseline_fingerprint": "",
            "changes": {"list": kind, "ops": ops},
        },
        rows,
    )
    decks._audit("proposal_created", sub=sub, detail={"proposal_id": pid, "kind": "lists", "list": kind})
    return decks.describe(sub, pid)


# -- applying -----------------------------------------------------------------------------------


def _stale(text: str) -> DeckError:
    return needs_look_error(text)


async def _send(decks: DeckService, sub: str, fn: Any) -> Any:
    return await decks._call_unslotted(sub, fn)


async def apply(  # noqa: C901, PLR0912, PLR0915
    decks: DeckService, sub: str, row: dict[str, Any], progress: dict[str, Any]
) -> dict[str, Any]:
    c = row.get("changes") or {}
    kind = c.get("list")
    ops = c.get("ops") or []
    if kind not in KINDS or not isinstance(ops, list):
        raise DeckError("invalid", "This proposal names a list this gateway does not know.")
    client = decks.client
    before: list[dict[str, Any]] = []
    # 1. the list as it is now: every change must still fit it, else nothing is sent
    if kind == "custom_cards":
        for op in ops:
            if op["op"] == "create":
                continue
            found = await read(decks, sub, "custom_cards", search=str(op["name"]).split(" // ")[0])
            hit = next((x for x in found["items"] if x["id"] == op["id"]), None)
            if hit is None:
                raise _stale(f"The custom card “{op['name']}” is no longer on Archidekt.")
            if op["op"] == "edit" and any(
                hit.get(k) != op["was"].get(k) for k in CUSTOM_CARD_FIELDS if k not in PICTURE_FIELDS
            ):
                raise _stale(f"The custom card “{op['name']}” changed on Archidekt since this was reviewed.")
            before.append(hit)
    elif kind != "default_categories":
        now = await read(decks, sub, kind)
        items = now["items"]
        by_id = {i["id"]: i for i in items}
        for op in ops:
            if op["op"] in ("create", "pin", "remove_all"):
                if op["op"] == "create" and any(_same(i["name"], op["name"]) for i in items):
                    raise _stale(f"There is already an entry called “{op['name']}” on Archidekt.")
                continue
            hit = by_id.get(op["id"])
            if hit is None:
                raise _stale(f"“{op['name']}” is no longer in your {NOUNS[kind]} on Archidekt.")
            if op["op"] == "edit" and {k: hit.get(k) for k in op["was"]} != op["was"]:
                raise _stale(f"“{op['name']}” changed on Archidekt since this was reviewed.")
        if kind == "pins" and any(o["op"] == "pin" for o in ops):
            if len(items) + sum(1 for o in ops if o["op"] == "pin") > MAX_PINS:
                raise _stale(f"That would be more than Archidekt's {MAX_PINS} pinned cards.")
        before = items
    else:
        before = [{"oracle_id": o["oracle_id"], "name": o["name"]} for o in ops]
    progress["before"] = before
    # 2. the writes, one at a time
    made: dict[int, Any] = {}
    for n, op in enumerate(ops):
        kind_op = op["op"]
        try:
            if kind == "templates":
                fields = {
                    "name": op.get("name"),
                    "includedInDeck": op.get("in_deck"),
                    "includedInPrice": op.get("in_price"),
                    "isPremier": op.get("premier"),
                }
                if kind_op == "create":
                    made[n] = await _send(
                        decks, sub, lambda token, *_, f=fields: client.create_template(token, f)
                    )
                elif kind_op == "edit":
                    await _send(
                        decks,
                        sub,
                        lambda token, *_, i=op["id"], f=fields: client.update_template(token, i, f),
                    )
                else:
                    await _send(decks, sub, lambda token, *_, i=op["id"]: client.delete_template(token, i))
            elif kind == "default_categories":
                await _send(
                    decks,
                    sub,
                    lambda token, *_, o=op: client.set_default_categories(
                        token, o["oracle_id"], list(o["categories"])
                    ),
                )
            elif kind == "color_tags":
                if kind_op == "create":
                    made[n] = await _send(
                        decks,
                        sub,
                        lambda token, *_, o=op: client.create_color_tag(token, o["name"], o["color"]),
                    )
                elif kind_op == "edit":
                    tag = {"id": op["id"], "name": op["name"], "color": op["color"]}
                    await _send(decks, sub, lambda token, *_, t=tag: client.update_color_tag(token, t))
                else:
                    await _send(decks, sub, lambda token, *_, i=op["id"]: client.delete_color_tag(token, i))
            elif kind == "pins":
                if kind_op == "pin":
                    pin = {
                        "cardId": op["card_id"],
                        "notes": op["notes"],
                        "modifier": op["finish"],
                        "quantity": op["quantity"],
                    }
                    made[n] = await _send(decks, sub, lambda token, *_, p=pin: client.pin_cards(token, [p]))
                elif kind_op == "edit":
                    await _send(
                        decks,
                        sub,
                        lambda token, *_, o=op: client.update_pin(
                            token,
                            o["id"],
                            card_id=o["card_id"],
                            quantity=o["quantity"],
                            notes=o["notes"],
                            modifier=o["finish"],
                        ),
                    )
                elif kind_op == "unpin":
                    await _send(decks, sub, lambda token, *_, i=op["id"]: client.delete_pin(token, i))
                else:
                    await _send(decks, sub, lambda token, *_: client.delete_all_pins(token))
            else:
                owner = _owner(decks, sub)
                if kind_op == "create":
                    made[n] = await _send(
                        decks,
                        sub,
                        lambda token, *_, f=op["fields"], u=owner: client.create_custom_card(token, f, u),
                    )
                elif kind_op == "edit":
                    hit = next(b for b in before if b["id"] == op["id"])
                    # its pictures stay, but for one the member just uploaded on the page
                    keep = {k: hit.get(k) for k in PICTURE_FIELDS if k not in (op.get("pictures") or [])}
                    fields = {**op["fields"], **keep}
                    await _send(
                        decks,
                        sub,
                        lambda token, *_, i=op["id"], f=fields, u=owner: client.update_custom_card(
                            token, i, f, u
                        ),
                    )
                else:
                    await _send(decks, sub, lambda token, *_, i=op["id"]: client.delete_custom_card(token, i))
        except DeckError as exc:
            progress["done"] = n
            if exc.kind == "contract":  # Archidekt answered 400: it refused this one change
                raise DeckError(
                    "rejected",
                    f"Archidekt refused “{op.get('name') or kind_op}”: {exc}. "
                    + (
                        f"The {n} change{'s' if n != 1 else ''} before it were made."
                        if n
                        else "Nothing was changed."
                    ),
                ) from exc
            raise
        progress["done"] = n + 1
    # 3. the read-back
    mismatches = await _check(decks, sub, kind, ops, made)
    result = {
        "list": kind,
        "done": len(ops),
        "before": before,
        "verified": not mismatches,
        "mismatches": mismatches,
        "snapshot_id": None,
    }
    decks.db.finish_proposal(row["id"], state="applied", result=result)
    return decks.describe(sub, row["id"])


def _owner(decks: DeckService, sub: str) -> int:
    link = decks.db.get_link(sub)
    uid = str((link or {}).get("archidekt_user_id") or "")
    if not uid.isdigit():
        raise DeckError("not_linked", "The linked Archidekt account has no user id; relink it.")
    return int(uid)


async def _check(  # noqa: C901
    decks: DeckService, sub: str, kind: str, ops: list[dict[str, Any]], made: dict[int, Any]
) -> list[str]:
    """What the list read back from Archidekt does not show of ``ops``, in words."""
    out: list[str] = []
    if kind == "default_categories":
        # the cards may be on any page: read them all (100 a page), up to ten pages
        rows: dict[int, list[str]] = {}
        page = 1
        while page <= 10:
            got = await read(decks, sub, kind, page=page)
            rows.update({r["oracle_id"]: r["categories"] for r in got["items"] if r["oracle_id"] is not None})
            if not got["more"]:
                break
            page += 1
        else:
            return []  # too long a list to check every card: the writes were accepted
        for op in ops:
            if rows.get(op["oracle_id"], []) != op["categories"]:
                out.append(f"“{op['name']}” does not show the categories that were sent")
        return out
    if kind == "custom_cards":
        for n, op in enumerate(ops):
            found = await read(
                decks, sub, kind, search=str(op.get("fields", {}).get("frontName") or op["name"])
            )
            if op["op"] == "delete":
                if any(x["id"] == op["id"] for x in found["items"]):
                    out.append(f"“{op['name']}” is still there")
                continue
            cid = op.get("id") or (made.get(n) or {}).get("id")
            hit = next((x for x in found["items"] if x["id"] == cid), None)
            if hit is None or any(
                hit.get(k) != op["fields"].get(k) for k in ("frontName", "frontTypes", "frontText")
            ):
                out.append(f"“{op['name']}” does not read back as sent")
        return out
    items = (await read(decks, sub, kind))["items"]
    by_id = {i["id"]: i for i in items}
    for op in ops:
        name = op.get("name") or ""
        if op["op"] in ("delete", "unpin"):
            if op["id"] in by_id:
                out.append(f"“{name}” is still there")
        elif op["op"] == "remove_all":
            if items:
                out.append(f"{len(items)} pinned card{'s are' if len(items) != 1 else ' is'} still there")
        elif op["op"] == "pin":
            if not any(i["card_id"] == op["card_id"] for i in items):
                out.append(f"“{name}” is not pinned")
        elif kind == "templates":
            want = {
                "name": op["name"],
                "in_deck": op["in_deck"],
                "in_price": op["in_price"],
                "premier": op["premier"],
            }
            hit = (
                by_id.get(op.get("id"))
                if op["op"] == "edit"
                else next((i for i in items if _same(i["name"], op["name"])), None)
            )
            if hit is None or {k: hit[k] for k in want} != want:
                out.append(f"“{name}” does not read back as sent")
        elif kind == "color_tags":
            hit = (
                by_id.get(op.get("id"))
                if op["op"] == "edit"
                else next((i for i in items if _same(i["name"], op["name"])), None)
            )
            if hit is None or hit["name"] != op["name"] or hit["color"].lower() != op["color"]:
                out.append(f"“{name}” does not read back as sent")
        elif kind == "pins":
            hit = by_id.get(op["id"])
            if hit is None or (hit["quantity"], hit["notes"], hit["finish"]) != (
                op["quantity"],
                op["notes"],
                op["finish"],
            ):
                out.append(f"“{name}” does not read back as sent")
    return out


def applied_text(changes: dict[str, Any], result: dict[str, Any]) -> str:
    """What an applied lists change did, in one sentence (decks.applied_text)."""
    if result.get("verified") is False:
        return (
            "Sent to Archidekt, but the read-back didn't confirm every change. See Result on the review page."
        )
    return f"Your {NOUNS.get(str(changes.get('list')), 'lists')} on Archidekt have these changes now."


# -- the member's own press on the My lists page -------------------------------------------------

PICTURE_MAX_BYTES = 1_500_000  # Archidekt's own limit for a custom card picture (its upload box)
PICTURE_SIZE = (500, 700)  # the crop Archidekt's page redraws and sends
PICTURE_FORMATS = ("JPEG", "PNG", "WEBP")
PICTURE_MAX_PIXELS = 12_000_000  # the most pixels a picture is ever decoded to (security review 0.7.44)
# one picture decoded at a time, off the event loop: a decoded picture can take tens of megabytes,
# and the gateway's container is small (the profile picture shares it)
DECODE_LOCK = threading.Lock()


def jpeg_scale(img: Any, want: tuple[int, int]) -> float:
    """Ask the JPEG decoder for the picture at a fraction of its size (1/2, 1/4 or 1/8) that still
    covers ``want`` pixels, before anything is decoded; the factor from the decoded picture back
    to the file's own pixels (1.0 when nothing was saved)."""
    w, h = img.size
    if img.format == "JPEG":
        img.draft("RGB", (max(1, want[0]), max(1, want[1])))
    return w / img.size[0]


def redraw_picture(raw: bytes, crop: tuple[int, int, int, int] | None = None) -> bytes:
    """The member's picture checked and redrawn as Archidekt's page does: JPEG, PNG or WebP up to
    1.5 MB, the crop (left, top, width, height in the picture's own pixels; the middle at 5:7 when
    none) scaled to 500 x 700 and saved as a PNG. Nothing else of the file is kept. One at a time
    (DECODE_LOCK); redraw_picture_async runs it off the event loop."""
    with DECODE_LOCK:
        return _redraw(raw, crop)


async def redraw_picture_async(raw: bytes, crop: tuple[int, int, int, int] | None = None) -> bytes:
    return await asyncio.to_thread(redraw_picture, raw, crop)


def _redraw(raw: bytes, crop: tuple[int, int, int, int] | None) -> bytes:
    import io

    from PIL import Image, UnidentifiedImageError

    if not raw:
        raise _err("Choose a picture first.")
    if len(raw) > PICTURE_MAX_BYTES:
        raise _err("The picture is larger than 1.5 MB, Archidekt's limit. Choose a smaller one.")
    try:
        with Image.open(io.BytesIO(raw)) as probe:
            fmt = probe.format
            w, h = probe.size
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise _err("That file is not a picture the gateway can read. Use JPEG, PNG or WebP.") from exc
    if fmt not in PICTURE_FORMATS:
        raise _err("Use a JPEG, PNG or WebP picture.")
    if crop is None:
        cw = min(w, h * PICTURE_SIZE[0] // PICTURE_SIZE[1])
        ch = min(h, w * PICTURE_SIZE[1] // PICTURE_SIZE[0])
        crop = ((w - cw) // 2, (h - ch) // 2, cw, ch)
    left, top, cw, ch = crop
    if min(cw, ch) < 1 or left < 0 or top < 0 or left + cw > w or top + ch > h:
        raise _err("The crop is outside the picture. Crop it again.")
    with Image.open(io.BytesIO(raw)) as img:
        # the decoder's own reduction keeps the crop at least 500 x 700 (a JPEG only)
        f = jpeg_scale(img, (w * PICTURE_SIZE[0] // cw, h * PICTURE_SIZE[1] // ch))
        if img.size[0] * img.size[1] > PICTURE_MAX_PIXELS:
            raise _err("The picture has too many pixels. Choose a smaller one.")
        box = (round(left / f), round(top / f), round((left + cw) / f), round((top + ch) / f))
        art = img.crop(box).convert("RGBA").resize(PICTURE_SIZE, Image.LANCZOS)
    art.info.clear()
    out = io.BytesIO()
    art.save(out, format="PNG", optimize=True)
    return out.getvalue()


async def hand_change(decks: DeckService, sub: str, kind: str, change: dict[str, Any]) -> dict[str, Any]:
    """The member's own press on the My lists page: the same proposal an assistant makes, applied
    at once (the caller has set the browser as the client), as a hand edit is."""
    p = await decks.propose_lists(sub, kind, [change])
    return await decks.apply(sub, p["proposal_id"], via="browser")


async def set_picture(
    decks: DeckService,
    sub: str,
    name: str,
    face: str,
    raw: bytes,
    crop: tuple[int, int, int, int] | None = None,
) -> dict[str, Any]:
    """The member's own picture for one face of their custom card ``name``: checked and redrawn
    here, uploaded to Archidekt (which answers with its address), then set on the card as a hand
    edit. Only the page calls this; an assistant never sets a picture."""
    if face not in ("front", "back"):
        raise _err("Pick the front or the back.")
    png = await redraw_picture_async(raw, crop)
    found = await _custom_by_name(decks, sub, {"name": name})
    if face == "back" and not found.get("hasBack"):
        raise _err("Switch on Has a back face and save the card first.")
    decks._writes_switch()
    url = await decks._call(sub, lambda token, *_: decks.client.upload_image(token, png))
    decks._audit("custom_card_picture_uploaded", sub=sub, detail={"face": face})
    p = await decks.propose_lists(
        sub, "custom_cards", [{"op": "edit", "name": found["name"]}], picture={f"{face}ImageUrl": url}
    )
    return await decks.apply(sub, p["proposal_id"], via="browser")


async def from_templates(decks: DeckService, sub: str, changes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Category changes with ``from_template`` made into plain creates: the template's name (or the
    change's own ``category``) and its three switches, as Archidekt's Add template does. Read once."""
    names = (await read(decks, sub, "templates"))["items"]
    out = []
    for c in changes:
        want = c.get("from_template")
        if not want:
            out.append(c)
            continue
        if c.get("action") not in (None, "create"):
            raise _err("from_template goes with action create")
        extra = sorted(set(c) - {"action", "category", "from_template"})
        if extra:
            raise _err(f"from_template takes the template's switches, so leave out {', '.join(extra)}")
        t = _one(names, want, "category template")
        out.append(
            {
                "action": "create",
                "category": c.get("category") or t["name"],
                "premier": t["premier"],
                "not_in_deck": not t["in_deck"],
                "not_in_price": not t["in_price"],
            }
        )
    return out
