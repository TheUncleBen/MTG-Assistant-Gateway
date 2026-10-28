"""Archidekt's forum, read as its own pages read it (parity release 2, Ben's choice (a), 23 Oct).

Archidekt has no API for the forum's sections or a section's thread list: its pages are drawn on
its server, and the gateway reads the same page data (``ArchidektClient.site_page_data``). That
is not an API, so a site update can change it; when it does, the gateway says so in plain words
(FORUM_CHANGED) and a thread still opens from its link or number. A thread itself is a comment
tree, read through the comment API the deck comments use, and a reply is a comment
(``propose_comment`` with ``thread_id``).

Shapes, from Archidekt's forum scripts (read 2026-10-22, not yet seen live):
- /forum: ``forums.results`` = [{title (the group), children: [{title, description, posts}]}].
- /forum/{group}/{section}?page=&orderBy=&q=: ``threads`` {count, results: [{id, title, type,
  archived, owner {username}, childrenCount, points, recentPost {createdAt, owner {username}}}]},
  20 threads a page; orderBy ``-updatedAt`` (most recent, the default) or ``-points``.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .archidekt import ArchidektError
from .decklist import clean_text
from .decks import DeckError, archidekt_wait

if TYPE_CHECKING:
    from .decks import DeckService

THREADS_PER_PAGE = 20  # the forum list's page size in Archidekt's scripts
MAX_PAGE = 500
# Archidekt's thread types (its scripts): only the ones a list marks
THREAD_TYPES = {1: "global", 2: "sticky", 3: "news", 6: "features", 7: "contest"}
ORDERS = {"recent": "-updatedAt", "points": "-points"}
FORUM_CHANGED = (
    "Archidekt changed how its forum pages load, so the gateway can't list the forum right now. "
    "A thread still opens from its link or number, and the forum is on Archidekt's own site."
)
FORUM_TARGET_PREFIX = "forum:"


def target(thread_id: Any) -> str:
    """The comment target of a forum thread, as the comment actions and pages name it."""
    tid = _thread_id(thread_id)
    return f"{FORUM_TARGET_PREFIX}{tid}"


def thread_of(target_id: str) -> int | None:
    """The thread number of a forum comment target, or None for a deck."""
    raw = str(target_id or "")
    if raw.startswith(FORUM_TARGET_PREFIX) and raw[len(FORUM_TARGET_PREFIX) :].isdigit():
        return int(raw[len(FORUM_TARGET_PREFIX) :])
    return None


def _thread_id(value: Any) -> int:
    raw = str(value or "").strip().rstrip("/")
    if "/forum/thread/" in raw:
        raw = raw.rsplit("/forum/thread/", 1)[1].split("?")[0].split("/")[0]
    if not raw.isdigit() or not 0 < int(raw) < 10**12:
        raise DeckError("invalid", "thread_id must be a forum thread's number or its link")
    return int(raw)


def linked(decks: DeckService, sub: str) -> bool:
    """Whether the member has an Archidekt account linked. The forum is public on Archidekt: its
    pages and its threads' comments answer an anonymous reader, so a member without a link reads
    it too (decision C8) and only replying needs the link."""
    return decks.db.get_link(sub) is not None


async def _anonymous(decks: DeckService, sub: str, fn: Any) -> Any:
    """``fn()`` as an anonymous reader, inside the member's Archidekt slot (counted against their
    budget like any other read)."""
    async with decks.archidekt_slot(sub):
        try:
            return await fn()
        except ArchidektError as exc:
            raise DeckError(exc.kind, str(exc), retry_after=archidekt_wait(exc)) from exc


async def _page(
    decks: DeckService, sub: str, path: str, params: dict[str, Any] | None = None
) -> dict[str, Any]:
    try:
        if linked(decks, sub):
            return await decks._call(sub, lambda _token, *_: decks.client.site_page_data(path, params))
        return await _anonymous(decks, sub, lambda: decks.client.site_page_data(path, params))
    except DeckError as exc:
        if exc.kind == "changed":
            raise DeckError("unavailable", FORUM_CHANGED) from exc
        raise


def _text(value: Any, limit: int = 300) -> str:
    return clean_text(str(value or ""))[:limit]


def _int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


async def sections(decks: DeckService, sub: str) -> dict[str, Any]:
    """The forum's groups and their sections, as the forum's front page lists them."""
    props = await _page(decks, sub, "/forum")
    forums = props.get("forums")
    groups = forums.get("results") if isinstance(forums, dict) else None
    if not isinstance(groups, list):
        raise DeckError("unavailable", FORUM_CHANGED)
    out = []
    for g in groups:
        if not isinstance(g, dict):
            continue
        kids = g.get("children") if isinstance(g.get("children"), list) else []
        out.append(
            {
                "group": _text(g.get("title"), 100),
                "sections": [
                    {
                        "section": _text(k.get("title"), 100),
                        "description": _text(k.get("description")),
                        "posts": _int(k.get("posts")),
                    }
                    for k in kids
                    if isinstance(k, dict) and _text(k.get("title"), 100)
                ],
            }
        )
    groups_out = [g for g in out if g["group"]]
    _remember_listing(groups_out)
    return {"groups": groups_out}


# Which group and section a thread was listed in, from the section pages read so far: Archidekt's
# thread data names no section, so a thread page's trail (Forum › group › section) is known once
# its section's list has been read in this process. Bounded: the newest IN_SECTION_MAX threads.
IN_SECTION_MAX = 5000
_IN_SECTION: dict[int, tuple[str, str]] = {}
# The (group, section) pairs of the forum's own section listing as last read (sections()), by
# their casefolded names, in Archidekt's spelling. A trail is stored only under a pair found here,
# never under the names a caller put in the URL or an assistant's get_forum (security review
# 0.7.44 S-02: one member's made-up group name must not become every member's trail). Replaced
# whole on each read; bounded like the trail.
_LISTED: dict[tuple[str, str], tuple[str, str]] = {}


def _remember_listing(groups: list[dict[str, Any]]) -> None:
    pairs: dict[tuple[str, str], tuple[str, str]] = {}
    for g in groups:
        for k in g["sections"]:
            if len(pairs) < IN_SECTION_MAX:
                pairs[(g["group"].casefold(), k["section"].casefold())] = (g["group"], k["section"])
    _LISTED.clear()
    _LISTED.update(pairs)


def listed_as(group: str, section: str) -> tuple[str, str] | None:
    """(group, section) in Archidekt's own spelling when its section listing names them, else None."""
    return _LISTED.get((group.casefold(), section.casefold()))


def in_section(thread_id: int) -> tuple[str, str] | None:
    """(group, section) the thread was last listed in, or None when no list of it has been read."""
    return _IN_SECTION.get(thread_id)


def _remember_section(thread_id: int, group: str, section: str) -> None:
    _IN_SECTION.pop(thread_id, None)
    _IN_SECTION[thread_id] = (group, section)
    while len(_IN_SECTION) > IN_SECTION_MAX:
        del _IN_SECTION[next(iter(_IN_SECTION))]


async def threads(
    decks: DeckService,
    sub: str,
    group: str,
    section: str,
    *,
    page: int = 1,
    order: str = "recent",
    search: str = "",
) -> dict[str, Any]:
    """One section's threads, 20 a page, as the section's own page lists them."""
    group, section = _text(group, 100), _text(section, 100)
    if not group or not section or not group.strip(".") or not section.strip("."):
        # a name of dots would lead the page read out of /forum/ (security review F-03)
        raise DeckError("invalid", "Name the forum group and section (get_forum lists them).")
    if order not in ORDERS:
        raise DeckError("invalid", f"order must be one of: {', '.join(ORDERS)}")
    page = max(1, min(int(page or 1), MAX_PAGE))
    params: dict[str, Any] = {"page": page, "orderBy": ORDERS[order]}
    if search.strip():
        params["q"] = _text(search, 100)
    props = await _page(decks, sub, f"/forum/{quote(group, safe='')}/{quote(section, safe='')}", params)
    body = props.get("threads")
    rows = body.get("results") if isinstance(body, dict) else None
    if not isinstance(rows, list):
        raise DeckError("unavailable", FORUM_CHANGED)
    count = _int(body.get("count"))
    known = listed_as(group, section)  # the trail's names, only as Archidekt's listing gives them
    out = []
    for t in rows:
        if not isinstance(t, dict) or not _int(t.get("id")):
            continue
        owner = t.get("owner") if isinstance(t.get("owner"), dict) else {}
        recent = t.get("recentPost") if isinstance(t.get("recentPost"), dict) else {}
        by = recent.get("owner") if isinstance(recent.get("owner"), dict) else {}
        if known:
            _remember_section(_int(t.get("id")), *known)
        out.append(
            {
                "id": _int(t.get("id")),
                "title": _text(t.get("title"), 200),
                "type": THREAD_TYPES.get(_int(t.get("type")), ""),
                "archived": t.get("archived") is True,
                "owner": _text(owner.get("username"), 150),
                "responses": _int(t.get("childrenCount")),
                "points": _int(t.get("points")),
                "last_post": {"by": _text(by.get("username"), 150), "at": _text(recent.get("createdAt"), 40)},
            }
        )
    return {
        "group": group,
        "section": section,
        "order": order,
        "search": params.get("q", ""),
        "page": page,
        "pages": max(1, math.ceil(count / THREADS_PER_PAGE)),
        "count": count,
        "threads": out,
    }


async def thread(decks: DeckService, sub: str, thread_id: Any, *, page: int = 1) -> dict[str, Any]:
    """One thread: its first post and a page of replies, through the comment API."""
    from .social import comment_out, rich_text

    tid = _thread_id(thread_id)
    page = max(1, min(int(page or 1), MAX_PAGE))
    if linked(decks, sub):
        body = await decks._call(sub, lambda token, *_: decks.client.comment_thread(token, tid, page=page))
    else:
        body = await _anonymous(decks, sub, lambda: decks.client.comment_thread(None, tid, page=page))
    kids = body.get("children")
    results = kids.get("results") if isinstance(kids, dict) else kids if isinstance(kids, list) else []
    links = kids.get("links") if isinstance(kids, dict) else {}
    owner = body.get("owner") if isinstance(body.get("owner"), dict) else {}
    return {
        "id": tid,
        "title": _text(body.get("title"), 200),
        "text": rich_text(body.get("text")),
        "owner": _text(owner.get("username"), 150),
        "created_at": _text(body.get("createdAt"), 40),
        "points": _int(body.get("points")),
        "locked": body.get("locked") is True,
        "archived": body.get("archived") is True,
        # the thread's mark (THREAD_TYPES): a "contest" thread carries the contest strip
        "type": THREAD_TYPES.get(_int(body.get("type")), ""),
        "reply_count": _int(body.get("childrenCount")),
        "in": in_section(tid),  # (group, section) when a section listing has named it, else None
        "page": page,
        "has_more": bool(isinstance(links, dict) and links.get("next")),
        "replies": [comment_out(c) for c in results if isinstance(c, dict)],
        "url": f"https://archidekt.com/forum/thread/{tid}",
    }
