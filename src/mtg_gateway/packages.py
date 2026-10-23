"""Archidekt card packages (parity release 2, REQUESTS.md section 6): a package is a deck with a
``cardPackage`` block (search keywords, a budget of $ to $$$$ or none, the formats it is meant
for). Made with the New package form's request, its block changed with the deck settings' second
request (by the block's own id), and added to a deck the way the site's Add package does it.

Unknown, and so not assumed: how long keywords may be on Archidekt (the gateway holds them to
KEYWORDS_MAX) and every field of the block Archidekt answers with (the gateway sends back the block
it read, with only the asked-for fields changed, as the site does)."""

from __future__ import annotations

from typing import Any

from .archidekt import FORMAT_IDS, FORMAT_NAMES, Deck, format_label
from .deck_stats import _SINGLETON_FORMATS, colour_letter

KEYWORDS_MAX = 200
BUDGETS = {None: "any", 1: "$", 2: "$$", 3: "$$$", 4: "$$$$"}
FIELDS = ("keywords", "budget", "intended_formats")
# Add package: where the package's cards go (the site's choices: keep the package's own
# categories, let Archidekt file them, or put them all in the Maybeboard)
CATEGORY_CHOICES = ("package", "default", "maybeboard")


def _err(text: str) -> Exception:
    from .decks import DeckError

    return DeckError("invalid", text)


def _format_id(name: Any) -> int:
    key = str(name or "").strip().lower().replace(" ", "")
    if key not in FORMAT_IDS:
        raise _err(f"intended_formats takes format names: {', '.join(sorted(FORMAT_IDS))}")
    return FORMAT_IDS[key]


def parse(raw: Any, *, partial: bool = False) -> dict[str, Any]:
    """A package's settings, checked: ``keywords`` (required unless ``partial``), ``budget`` (1 to
    4, or None for any) and ``intended_formats`` (format names). Returned in Archidekt's names."""
    if not isinstance(raw, dict) or (partial and not raw):
        raise _err("package must be an object with any of: " + ", ".join(FIELDS))
    unknown = sorted(str(k) for k in raw if k not in FIELDS)
    if unknown:
        raise _err(f"unknown package fields: {', '.join(unknown)}; use {', '.join(FIELDS)}")
    out: dict[str, Any] = {}
    if "keywords" in raw or not partial:
        words = raw.get("keywords")
        words = " ".join(words.split()) if isinstance(words, str) else ""
        if not words or len(words) > KEYWORDS_MAX:
            raise _err(
                f"A package needs keywords, 1 to {KEYWORDS_MAX} characters (for example: shocklands, "
                "fetchlands)"
            )
        out["keywords"] = words
    if "budget" in raw:
        b = raw["budget"]
        if b is not None and (isinstance(b, bool) or b not in (1, 2, 3, 4)):
            raise _err("budget is 1 to 4 ($ to $$$$), or null for any")
        out["budget"] = b
    elif not partial:
        out["budget"] = None
    if "intended_formats" in raw:
        names = raw["intended_formats"] or []
        if not isinstance(names, list) or len(names) > len(FORMAT_NAMES):
            raise _err("intended_formats is a list of format names")
        ids: list[int] = []
        for n in names:
            fid = _format_id(n)
            if fid not in ids:
                ids.append(fid)
        out["intendedFormats"] = ids
    elif not partial:
        out["intendedFormats"] = []
    return out


def block_of(deck: Deck) -> dict[str, Any] | None:
    """The deck's package block as Archidekt sent it, or None when the deck is not a package."""
    block = deck.raw.get("cardPackage")
    return block if isinstance(block, dict) and str(block.get("id") or "").isdigit() else None


def text(block: dict[str, Any]) -> str:
    """'keywords “…”, budget $$, for Commander, Modern' in the review's words."""
    formats = [format_label(FORMAT_NAMES.get(f)) for f in block.get("intendedFormats") or []]
    return (
        f"keywords “{block.get('keywords') or ''}”, budget {BUDGETS.get(block.get('budget'), 'any')}, "
        f"for {', '.join(formats) or 'any format'}"
    )


def changed(block: dict[str, Any], wanted: dict[str, Any]) -> dict[str, Any] | None:
    """The whole block to send (the one read, with ``wanted`` over it), or None when nothing
    would change."""
    new = {**block, **wanted}
    keys = ("keywords", "budget", "intendedFormats")
    if all(new.get(k) == block.get(k) for k in keys):
        return None
    return new


def same(block: dict[str, Any] | None, want: dict[str, Any]) -> bool:
    """Whether a re-read block has the settings sent."""
    return block is not None and all(
        block.get(k) == want.get(k) for k in ("keywords", "budget", "intendedFormats")
    )


def adds(
    deck: Deck,
    package: Deck,
    *,
    categories: str = "package",
    skip_existing: bool = True,
    skip_outside_identity: bool = True,
    singleton: bool | None = None,
) -> tuple[Deck, list[dict[str, Any]], dict[str, list[str]]]:
    """The package's cards as add changes for ``deck`` (the site's Add package options), and what
    was left out and why. ``categories``: package (each card keeps its package category), default
    (no category given: Archidekt files it) or maybeboard. ``singleton`` None follows the deck's
    format (one copy each in a singleton format). A card the package holds in several printings
    is one add of the first printing listed, with every copy."""
    if block_of(package) is None:
        raise _err(f"“{package.name}” is not a card package.")
    if package.id == deck.id:
        raise _err("A package can't be added to itself.")
    if categories not in CATEGORY_CHOICES:
        raise _err(f"categories must be one of: {', '.join(CATEGORY_CHOICES)}")
    one_each = (deck.format or "") in _SINGLETON_FORMATS if singleton is None else bool(singleton)
    have = {c.name.casefold() for c in deck.cards}
    commanders = [c for c in deck.cards if deck.is_commander(c)]
    identity = {colour_letter(x) for c in commanders for x in c.color_identity} - {None}
    skipped: dict[str, list[str]] = {"in the deck already": [], "outside the commander's colors": []}
    merged: dict[str, dict[str, Any]] = {}
    for c in package.cards:
        if not package.in_deck(c):
            continue  # the package's own maybeboard or sideboard is not part of it
        name = c.name
        if skip_existing and name.casefold() in have:
            skipped["in the deck already"].append(name)
            continue
        if skip_outside_identity and commanders:
            colours = {colour_letter(x) for x in c.color_identity} - {None}
            if not colours <= identity:
                skipped["outside the commander's colors"].append(name)
                continue
        got = merged.get(name.casefold())
        if got is not None:
            got["quantity"] = 1 if one_each else min(99, got["quantity"] + c.quantity)
            continue
        change: dict[str, Any] = {
            "action": "add",
            "card_name": name,
            "quantity": 1 if one_each else c.quantity,
        }
        if c.set_code and c.collector_number:
            change["set_code"], change["collector_number"] = c.set_code, c.collector_number
        if categories == "package" and c.categories:
            change["categories"] = list(c.categories)
        elif categories == "maybeboard":
            change["category"], change["zone"] = "Maybeboard", "side"
        merged[name.casefold()] = change
    changes = list(merged.values())
    if not changes:
        raise _err(f"Nothing to add from “{package.name}”: every card is in the deck already or left out.")
    return package, changes, {k: v for k, v in skipped.items() if v}


def source_text(name: str, changes: list[dict[str, Any]], skipped: dict[str, list[str]]) -> str:
    """The review's line above a package's adds."""
    n = sum(int(c["quantity"]) for c in changes)
    left = "; ".join(f"left out, {why}: {', '.join(names)}" for why, names in skipped.items())
    return f"From card package “{name}”: {n} {'card' if n == 1 else 'cards'}" + (f" ({left})" if left else "")


WHOSE = ("mine", "followed", "all")
MINE_TTL = 60.0  # seconds the member's own packages are kept (My decks asks after every visit)
# (sub, filters) -> (expiry, Archidekt's answer); only "mine", the one My decks reads
_MINE: dict[tuple[str, tuple[Any, ...]], tuple[float, dict[str, Any]]] = {}


def forget(sub: str, *_: Any) -> None:
    """Drop the member's kept package list: after any write the gateway sends for them (a
    DeckService write hook) and when their link changes."""
    for key in [k for k in _MINE if k[0] == sub]:
        _MINE.pop(key, None)


async def listing(
    decks: Any,
    sub: str,
    *,
    whose: str = "mine",
    name_contains: str | None = None,
    intended_format: str | None = None,
    budget: int | None = None,
    page: int = 1,
) -> dict[str, Any]:
    """One page of card packages as the site's Packages page lists them: the member's own
    (``mine``), the ones they follow (``followed``) or everyone's public ones (``all``), most
    imported first, filtered by name, one intended format and a budget. ``{rows, more}``; each row
    has id, name, owner, url, private, size, settings and imports."""
    if whose not in WHOSE:
        raise _err(f"whose is one of: {', '.join(WHOSE)}")
    if isinstance(page, bool) or not isinstance(page, int) or not 1 <= page <= 100:
        raise _err("page must be a number from 1 to 100")
    if budget is not None and (isinstance(budget, bool) or budget not in (1, 2, 3, 4)):
        raise _err("budget is 1 to 4 ($ to $$$$), or null for any")
    name = " ".join(str(name_contains or "").split())[:100]
    fmt = None if not intended_format else _format_id(intended_format)
    import time

    from .archidekt import _owned_by

    _token, link = decks._token(sub)
    owner = link["archidekt_username"] if whose == "mine" else None
    key = (sub, (name, fmt, budget, page))
    hit = _MINE.get(key) if whose == "mine" else None
    if hit is not None and hit[0] > time.monotonic():
        body = hit[1]
    else:
        body = await decks._call(
            sub,
            lambda token, *_: decks.client.packages_list(
                token,
                owner=owner,
                bookmarks=whose == "followed",
                name=name,
                intended_format=fmt,
                budget=budget,
                page=page,
            ),
        )
        if whose == "mine":
            _MINE[key] = (time.monotonic() + MINE_TTL, body)
    rows = []
    for r in body["results"]:
        if not isinstance(r, dict) or not str(r.get("id") or "").isdigit():
            continue
        # asked for by username: the account's id decides, as for the deck list
        if whose == "mine" and not _owned_by(r.get("owner"), owner or "", link.get("archidekt_user_id")):
            continue
        block = r.get("cardPackage") if isinstance(r.get("cardPackage"), dict) else {}
        who = r.get("owner") if isinstance(r.get("owner"), dict) else {}
        rows.append(
            {
                "id": str(r["id"]),
                "name": str(r.get("name") or "Unnamed deck"),
                "owner": str(who.get("username") or ""),
                "url": f"https://archidekt.com/decks/{r['id']}",
                "private": bool(r.get("private")),
                "size": r.get("size") if isinstance(r.get("size"), int) else None,
                "settings": text(block) if block else None,
                "block": block,
                "imports": block.get("importCount"),
            }
        )
    return {"rows": rows, "more": bool(body.get("next"))}
