"""The Binder's own tags (0.7.46, Archidekt parity release 3): the tags Archidekt keeps on
collection entries, their list, and reading the whole Binder at once.

Archidekt's collection page lets a member make tags (a name and a color), rename, recolor and
delete them, put them on entries and filter the collection by them. The gateway does the same:

* the tag list is the ``tags`` field of the collection listing (one small read), changed through
  account_lists' ``collection_tags`` list (create, edit, delete; a proposal like every other
  list change, a delete always waits for the member's own press);
* an entry's tags change through the Details box or ``propose_collection_changes``'s ``tag``
  (collection.py), sent as the record's ``tags`` field;
* the filter reads the Binder whole (``whole_records``, pages of WHOLE_PAGE_SIZE) and keeps the
  entries carrying the tag, since Archidekt's own filter parameter for tags was not recorded.

What the parity research did not record, and the owner's first live run on the TEST account
settles: the bodies of the tag create and edit (sent as the color tag list's: ``{name, color}``,
the whole tag on an edit), how a tag is written inside a record's ``tags`` (sent as tag ids), the
shape of the listing's ``tags`` (read as ``[{id, name, color}]``) and Archidekt's own tag filter.
Every write is read back, so a different answer shows as a mismatch, never as a success."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .decklist import clean_text
from .decks import DeckError
from .digits import is_digits

if TYPE_CHECKING:
    from .decks import DeckService

WHOLE_PAGE_SIZE = 500  # Archidekt's largest page (archidekt.collection_page clamps to it)
WHOLE_PAGES = 40  # 20,000 entries: a whole read stops there and says it is not the whole Binder
TAG_NAME_MAX = 32  # as the color tag list's (Archidekt's collection tag limit was not recorded)
MAX_TAGS_PER_ENTRY = 20


def tag_of(raw: Any) -> dict[str, Any] | None:
    """One tag of the listing's ``tags`` as ``{id, name, color}``, or None when it has no id."""
    if not isinstance(raw, dict):
        return None
    tid = raw.get("id")
    if isinstance(tid, bool) or not isinstance(tid, int):
        return None
    return {
        "id": tid,
        "name": clean_text(str(raw.get("name") or ""))[: TAG_NAME_MAX * 2],
        "color": str(raw.get("color") or ""),
    }


def tag_list(body: Any) -> list[dict[str, Any]]:
    """The member's collection tags from a listing's body (none when it sends none)."""
    raw = body.get("tags") if isinstance(body, dict) else None
    return [t for t in (tag_of(x) for x in raw or []) if t is not None] if isinstance(raw, list) else []


def record_tag_ids(rec: dict[str, Any]) -> list[int]:
    """The tag ids a record carries, whether Archidekt sends ids or tag objects."""
    out: list[int] = []
    for t in rec.get("tags") or []:
        tid = t.get("id") if isinstance(t, dict) else t
        if isinstance(tid, str) and is_digits(tid):
            tid = int(tid)
        if isinstance(tid, int) and not isinstance(tid, bool) and tid not in out:
            out.append(tid)
    return out


def record_tag_names(rec: dict[str, Any], by_id: dict[int, dict[str, Any]]) -> list[str]:
    """A record's tags by name: a tag object's own name, else the name its id has in the list."""
    names: list[str] = []
    for t in rec.get("tags") or []:
        if isinstance(t, dict) and t.get("name"):
            name = clean_text(str(t["name"]))
        else:
            tid = t.get("id") if isinstance(t, dict) else t
            tid = int(tid) if isinstance(tid, str) and is_digits(tid) else tid
            hit = by_id.get(tid) if isinstance(tid, int) else None
            name = hit["name"] if hit else ""
        if name and name not in names:
            names.append(name)
    return names


def name_rows(rows: list[dict[str, Any]], records: list[dict[str, Any]], tags: list[dict[str, Any]]) -> None:
    """Put each row's tags by name (rows made by collection.row_out from ``records``, in order)."""
    by_id = {t["id"]: t for t in tags}
    for row, rec in zip(rows, records, strict=False):
        row["tags"] = record_tag_names(rec, by_id)


def same_name(a: str, b: str) -> bool:
    return a.casefold() == b.casefold()


def ids_for(names: list[str], tags: list[dict[str, Any]]) -> tuple[list[int], list[str]]:
    """(the ids of the named tags, the names the member has no tag for), by name ignoring case."""
    ids: list[int] = []
    missing: list[str] = []
    for n in names:
        hit = next((t for t in tags if same_name(t["name"], n)), None)
        if hit is None:
            if n not in missing:
                missing.append(n)
        elif hit["id"] not in ids:
            ids.append(hit["id"])
    return ids, missing


def clean_names(raw: Any) -> list[str]:
    """Tag names as given (a list, or text with commas), cleaned, at most MAX_TAGS_PER_ENTRY."""
    if raw is None:
        return []
    items = raw.split(",") if isinstance(raw, str) else raw
    if not isinstance(items, list):
        raise DeckError("invalid", "Tags must be a list of tag names.")
    out: list[str] = []
    for n in items:
        name = clean_text(str(n or ""))
        if not name:
            continue
        if len(name) > TAG_NAME_MAX:
            raise DeckError("invalid", f"A tag name is at most {TAG_NAME_MAX} characters: “{name[:40]}”.")
        if not any(same_name(name, x) for x in out):
            out.append(name)
    if len(out) > MAX_TAGS_PER_ENTRY:
        raise DeckError("invalid", f"At most {MAX_TAGS_PER_ENTRY} tags on one entry.")
    return out


def user_id(decks: DeckService, sub: str) -> str:
    link = decks.db.get_link(sub)
    uid = str((link or {}).get("archidekt_user_id") or "")
    if not is_digits(uid):
        raise DeckError("not_linked", "No Archidekt account is linked. Link one on the Account page first.")
    return uid


async def read_tags(decks: DeckService, sub: str) -> list[dict[str, Any]]:
    """The member's collection tags as Archidekt lists them now (one listing of one entry)."""
    uid = user_id(decks, sub)
    body = await decks._call(
        sub, lambda token, *_: decks.client.collection_page(token, uid, page=1, page_size=1)
    )
    return tag_list(body)


async def whole_records(
    decks: DeckService, sub: str, *, max_pages: int = WHOLE_PAGES, slotted: bool = True
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], bool]:
    """(every record, the tag list, whether that was the whole Binder): pages of WHOLE_PAGE_SIZE,
    newest first, at most ``max_pages`` of them. ``slotted``: through the member's Archidekt slot
    (False inside an apply, which holds it already)."""
    uid = user_id(decks, sub)
    call = decks._call if slotted else decks._call_unslotted
    records: list[dict[str, Any]] = []
    tags: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        body = await call(
            sub,
            lambda token, *_, page=page: decks.client.collection_page(
                token, uid, page=page, page_size=WHOLE_PAGE_SIZE
            ),
        )
        if page == 1:
            tags = tag_list(body)
        records += [r for r in body["results"] if isinstance(r, dict)]
        if not body.get("next"):
            return records, tags, True
    return records, tags, False
