"""Archidekt's social actions for the signed-in person: like a deck (a vote on the deck),
bookmark it, follow its owner, read and post comments.

These routes are a person's own clicks: every write needs the browser session and the page's
CSRF token, every call runs under that member's own linked Archidekt session, and the page asks
for a confirmation before each write. Since 0.7.17 an assistant can propose the same actions
(propose_deck_social, propose_comment; actions.py), but it can never apply one: each is applied
only by the member's own press on its review page, which then runs the code here.

Routes and bodies are the ones archidekt.com's own pages send (read from its bundle on
2026-10-07; see the notes on ``ArchidektClient.vote_deck`` and friends).
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import formatting, refusals
from .archidekt import VOTE_DOWN, VOTE_NONE, VOTE_UP, ArchidektError, Deck, QuillDoc, plain_text, rich_text
from .decklist import clean_text
from .decks import DeckError, _clean_deck_id
from .pages import NO_STORE, browser_session, csrf_header_ok, read_limited

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

MAX_COMMENT = 2000
MAX_THREAD_DEPTH = 6
FOLLOWING_PAGES = 5  # how far into the member's following list a deck page or a settings page reads
# How far a lookup of one person reads (a follow or collaborator proposal): it reads on from where
# the remembered list stopped and stops at the page that has them. Each page is one paced Archidekt
# request, so 20 pages is at most about 20 seconds and half of one minute's request budget, once
# per FOLLOWING_TTL; the deck pages, read far more often, keep to FOLLOWING_PAGES.
FOLLOWING_LOOKUP_PAGES = 20
FOLLOWING_TTL = 120.0  # seconds the following list is remembered after a read
FRESH_GAP = 15.0  # seconds between forced re-reads of the following list (a collaborator lookup)
_STATUS = {
    "csrf": 403,
    "invalid": 400,
    "not_found": 404,
    "not_linked": 409,
    "auth": 409,
    "forbidden": 403,
    "unavailable": 503,
    "rate_limited": 503,
    "busy": 429,
    "contract": 502,
    "writes_disabled": 403,
    # the write may have reached Archidekt: not a refusal of the request (social review, 0.7.29)
    "verify_mismatch": 502,
    "stale": 409,
}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


# Comment thread pages: the highest page asked for, and how long a member's read of a deck's thread
# root is reused for their next vote or comment (the root never changes for a deck).
MAX_PAGE = 1000
ROOT_TTL = 600.0
ROOT_MAX = 2000
NOT_IN_THREAD = "That comment is not in this deck's thread, or no longer on the page it was shown on."


def comment_text(text: str) -> str:
    """A comment as it will be written: plain text only (a Quill document is kept as its words
    alone, see ``plain_text``), without control characters or surrounding white space. Cleaning
    the result again changes nothing, so an approved comment is posted as its review showed it."""
    text = str(text)
    while True:
        clean = _CONTROL.sub("", plain_text(text).replace("\r\n", "\n")).strip()
        if clean == text:
            return clean
        text = clean


def _fail(kind: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": kind, "message": message}, _STATUS.get(kind, 400), headers=NO_STORE
    )


def _err(exc: Exception) -> JSONResponse:
    if isinstance(exc, (DeckError, ArchidektError)):
        kind = exc.kind
        msg = str(exc)
        if kind == "auth":
            msg = "Archidekt did not accept your linked session; relink it on the Account page."
        if kind == "contract":
            msg = "Archidekt answered in an unexpected way; try again."
        return _fail(kind, msg)
    raise exc


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def comment_out(c: dict[str, Any], depth: int = 0, *, keep_doc: bool = False) -> dict[str, Any]:
    """One comment of Archidekt's thread in the gateway's shape; replies nest to a depth limit.
    ``keep_doc``: a formatted comment also carries its stored document as ``_doc``, for an
    edit that keeps its formatting; never shown or sent to an assistant."""
    owner = c.get("owner") if isinstance(c.get("owner"), dict) else {}
    kids = c.get("children")
    results = kids.get("results") if isinstance(kids, dict) else kids if isinstance(kids, list) else []
    doc = {"_doc": c["text"]} if keep_doc and formatting.is_formatted(c.get("text")) else {}
    return {
        **doc,
        "id": _int(c.get("id")),
        "text": rich_text(c.get("text")),  # Archidekt keeps a Quill document; shown as its text
        "owner": {
            "id": _int(owner.get("id")),
            "username": str(owner.get("username") or ""),
        },
        "created_at": str(c.get("createdAt") or ""),
        "edited_at": str(c.get("editedAt") or "") or None,
        "points": _int(c.get("points")) or 0,
        "user_vote": _int(c.get("userInput")) or 0,
        "archived": c.get("archived") is True,
        "replies": [comment_out(k, depth + 1, keep_doc=keep_doc) for k in results if isinstance(k, dict)]
        if depth < MAX_THREAD_DEPTH
        else [],
        "reply_count": _int(c.get("childrenCount")) or 0,
    }


def _find(comments: list[dict[str, Any]], comment_id: int) -> dict[str, Any] | None:
    for c in comments:
        if c.get("id") == comment_id:
            return c
        hit = _find(c.get("replies") or [], comment_id)
        if hit is not None:
            return hit
    return None


def _ids(comments: list[dict[str, Any]]) -> set[int]:
    out: set[int] = set()
    for c in comments:
        if c.get("id") is not None:
            out.add(int(c["id"]))
        out |= _ids(c.get("replies") or [])
    return out


class SocialService:
    def __init__(self, state: AppState):
        self.state = state
        self.decks = state.decks
        self.client = state.decks.client
        self._following: dict[str, tuple[float, dict[int, str]]] = {}
        # the next page of the remembered following list to read, None when it was read to the end
        self._following_next: dict[str, int | None] = {}
        self._following_fresh: dict[str, float] = {}  # sub -> when a fresh read was last forced
        # (sub, deck id) -> (when, the deck's comment thread root) from that member's last read, so a
        # vote or a comment write straight after the thread loaded skips reading the whole deck
        # again (one thread read and the write, not three calls per vote)
        self._roots: dict[tuple[str, str], tuple[float, int | None]] = {}

    def _me(self, sub: str) -> tuple[str, str]:
        """(Archidekt user id, username) of the member's linked account."""
        link = self.state.db.get_link(sub)
        if link is None:
            raise DeckError("not_linked", "Link your Archidekt account on the Account page first.")
        uid = str(link.get("archidekt_user_id") or "")
        if not uid.isdigit():
            raise DeckError("not_linked", "The linked Archidekt account has no user id; relink it.")
        return uid, str(link.get("archidekt_username") or "")

    async def deck(self, sub: str, deck_id: str) -> Deck:
        return await self.decks.get_any_deck(sub, deck_id)

    async def vote(self, sub: str, deck_id: str, want: int) -> dict[str, Any]:
        self.decks._writes_switch()
        self._me(sub)
        deck = await self.deck(sub, deck_id)
        if deck.user_vote == want:  # already so: nothing is sent (Archidekt might toggle it)
            return {"vote": want, "points": deck.points}
        if want == VOTE_NONE:
            # taking a vote back names the vote taken back, as archidekt.com's like button does
            await self.decks._call(
                sub,
                lambda t: self.client.vote_deck(t, deck.id, up=deck.user_vote != VOTE_DOWN, remove=True),
            )
        else:
            await self.decks._call(sub, lambda t: self.client.vote_deck(t, deck.id, up=want == VOTE_UP))
        weight = {VOTE_UP: 1, VOTE_DOWN: -1, VOTE_NONE: 0}
        points = deck.points - weight[deck.user_vote] + weight[want]
        self.state.db.audit("deck_voted", sub=sub, detail={"deck_id": deck.id, "vote": want})
        return {"vote": want, "points": points}

    async def vote_comment(
        self, sub: str, deck_id: str, comment_id: int, want: int, page: int = 1
    ) -> dict[str, Any]:
        """Vote on one comment of the deck's thread, found on the page of the thread it was shown
        on: PUT /comments/vote/{id}/ with {up, remove}, the body archidekt.com's own comment vote
        sends (its bundle, read 2026-10-10). The new score is worked out the way the site does,
        from the thread's points and the member's earlier vote."""
        self.decks._writes_switch()
        uid, _name = self._me(sub)
        found, _thread = await self.find_comment(sub, deck_id, comment_id, page)
        if str(found["owner"]["id"]) == uid:
            raise DeckError("invalid", refusals.OWN_COMMENT_VOTE)
        if found["archived"]:
            raise DeckError("invalid", refusals.ARCHIVED_COMMENT)
        if found["user_vote"] == want:  # already so: nothing is sent (Archidekt might toggle it)
            return {"comment": comment_id, "vote": want, "points": found["points"]}
        if want == VOTE_NONE:
            up = found["user_vote"] != VOTE_DOWN  # the vote taken back, as the site sends it
            await self.decks._call(sub, lambda t: self.client.vote_comment(t, comment_id, up=up, remove=True))
        else:
            await self.decks._call(sub, lambda t: self.client.vote_comment(t, comment_id, up=want == VOTE_UP))
        weight = {VOTE_UP: 1, VOTE_DOWN: -1, VOTE_NONE: 0}
        before = found["user_vote"] if found["user_vote"] in weight else VOTE_NONE
        points = found["points"] - weight[before] + weight[want]
        self.state.db.audit(
            "comment_voted", sub=sub, detail={"deck_id": deck_id, "comment": comment_id, "vote": want}
        )
        return {"comment": comment_id, "vote": want, "points": points}

    async def bookmark(self, sub: str, deck_id: str, on: bool) -> dict[str, Any]:
        self.decks._writes_switch()
        self._me(sub)
        await self.decks._call(sub, lambda t: self.client.bookmark_deck(t, deck_id, on=on))
        self.state.db.audit("deck_bookmarked", sub=sub, detail={"deck_id": deck_id, "on": on})
        return {"bookmarked": on}

    async def following(self, sub: str) -> set[int]:
        return set(await self.following_names(sub))

    async def following_names(self, sub: str, *, fresh: bool = False) -> dict[int, str]:
        """The people the member follows on Archidekt: user id -> username, the first
        FOLLOWING_PAGES pages (cached briefly; ``fresh`` reads Archidekt again)."""
        return (await self._read_following(sub, pages=FOLLOWING_PAGES, fresh=fresh))[0]

    async def find_followed(self, sub: str, username: str, *, fresh: bool = False) -> tuple[int, str] | None:
        """(user id, username) of the person the member follows by that name, None when they are not
        in the list as far as FOLLOWING_LOOKUP_PAGES (``following_cut`` says whether it went on).
        A name not found in a remembered list is looked for again in a fresh read, at most once
        per FRESH_GAP, in case they were followed on Archidekt since."""
        want = username.casefold()

        def hit(users: dict[int, str]) -> tuple[int, str] | None:
            return next(((i, n) for i, n in users.items() if n.casefold() == want), None)

        users, remembered = await self._read_following(
            sub, pages=FOLLOWING_LOOKUP_PAGES, fresh=fresh, until=lambda u: hit(u) is not None
        )
        if hit(users) is None and remembered and not fresh:
            users, _ = await self._read_following(
                sub, pages=FOLLOWING_LOOKUP_PAGES, fresh=True, until=lambda u: hit(u) is not None
            )
        return hit(users)

    def following_cut(self, sub: str) -> bool:
        """Whether the last read of the member's following list stopped with more pages left."""
        return self._following_next.get(sub) is not None

    async def _read_following(
        self,
        sub: str,
        *,
        pages: int,
        fresh: bool = False,
        until: Callable[[dict[int, str]], bool] | None = None,
    ) -> tuple[dict[int, str], bool]:
        """The member's following list read as far as ``pages``, or until ``until`` says what is
        wanted is in it, carrying on from the remembered part (FOLLOWING_TTL) unless ``fresh``.
        Also says whether the read started from a remembered list (which may be out of date)."""
        uid, _name = self._me(sub)
        hit = self._following.get(sub)
        now = time.monotonic()
        if fresh and hit and now - self._following_fresh.get(sub, -FRESH_GAP) < FRESH_GAP:
            fresh = False  # one forced read per FRESH_GAP: a run of typos does not re-read every page
        if fresh:
            self._following_fresh[sub] = now
        remembered = bool(hit and hit[0] > now and not fresh)
        if remembered and hit:
            expires, users, page = hit[0], dict(hit[1]), self._following_next.get(sub)
        else:
            expires, users, page = now + FOLLOWING_TTL, {}, 1
        while page is not None and page <= pages and not (until and until(users)):
            body = await self.decks._call(sub, lambda t, page=page: self.client.following(t, uid, page))
            for row in body["results"]:
                if isinstance(row, dict) and _int(row.get("id")) is not None:
                    users[int(row["id"])] = str(row.get("username") or "")
            page = page + 1 if body.get("next") else None
        # pages read on from a remembered list keep its expiry: nothing is held longer than the TTL
        self._following[sub] = (expires, users)
        self._following_next[sub] = page
        return users, remembered

    # -- deck collaborators (Archidekt's "editors": people who may change the deck) -----------------

    async def collaborators(self, sub: str, deck_id: str, *, owned: bool = False) -> list[dict[str, Any]]:
        """The collaborators of the member's own deck: [{editor_id, user_id, username, added_by,
        added_at}]. ``owned``: the caller has just read the deck as the member's own, so it is
        not read again."""
        self._me(sub)
        if not owned:
            deck_id = (await self.decks.get_own_deck(sub, deck_id)).id
        rows = await self.decks._call(sub, lambda t: self.client.deck_editors(t, str(deck_id)))
        out = []
        for r in rows:
            user = r.get("user") if isinstance(r.get("user"), dict) else {}
            by = r.get("createdBy") if isinstance(r.get("createdBy"), dict) else {}
            if _int(r.get("id")) is None or _int(user.get("id")) is None:
                continue
            out.append(
                {
                    "editor_id": int(r["id"]),
                    "user_id": int(user["id"]),
                    "username": clean_text(str(user.get("username") or "")),
                    "added_by": clean_text(str(by.get("username") or "")),
                    "added_at": str(r.get("createdAt") or ""),
                }
            )
        return out

    async def add_collaborator(self, sub: str, deck_id: str, user_id: int) -> dict[str, Any]:
        self.decks._writes_switch()
        uid, _name = self._me(sub)
        if str(user_id) == uid:
            raise DeckError("invalid", "you own this deck already")
        if any(c["user_id"] == user_id for c in await self.collaborators(sub, deck_id)):
            return {"added": False, "already": True, "verified": True}
        await self.decks._call(sub, lambda t: self.client.add_deck_editor(t, deck_id, user_id))
        self.state.db.audit("collaborator_added", sub=sub, detail={"deck_id": deck_id, "user_id": user_id})
        if not any(c["user_id"] == user_id for c in await self.collaborators(sub, deck_id, owned=True)):
            raise DeckError(
                "verify_mismatch", "Archidekt accepted the request but the deck does not list them"
            )
        return {"added": True, "verified": True}

    async def remove_collaborator(self, sub: str, deck_id: str, user_id: int) -> dict[str, Any]:
        """Remove by the person's user id: the editor row is looked up on this deck first, so
        only a collaborator of this deck can be removed through it."""
        self.decks._writes_switch()
        row = next((c for c in await self.collaborators(sub, deck_id) if c["user_id"] == user_id), None)
        if row is None:
            return {"removed": False, "already": True, "verified": True}
        await self.decks._call(sub, lambda t: self.client.remove_deck_editor(t, row["editor_id"]))
        self.state.db.audit("collaborator_removed", sub=sub, detail={"deck_id": deck_id, "user_id": user_id})
        if any(c["user_id"] == user_id for c in await self.collaborators(sub, deck_id, owned=True)):
            raise DeckError("verify_mismatch", "Archidekt accepted the request but the deck still lists them")
        return {"removed": True, "verified": True}

    async def follow_state(self, sub: str, user_id: int, *, lookup: bool = False) -> dict[str, Any]:
        """Whether the member follows ``user_id``. ``lookup`` (a follow proposal) reads as far as
        FOLLOWING_LOOKUP_PAGES, as a collaborator lookup does, so the two never disagree; the deck
        page's button reads FOLLOWING_PAGES."""
        uid, _name = self._me(sub)
        if str(user_id) == uid:
            return {"self": True, "following": False}
        users, _ = await self._read_following(
            sub, pages=FOLLOWING_LOOKUP_PAGES if lookup else FOLLOWING_PAGES, until=lambda u: user_id in u
        )
        following = user_id in users
        if not following and self.following_cut(sub):
            # The list was cut short and they are not in the part read, so whether
            # the member follows them is not known; the page offers both Follow and Unfollow
            return {"self": False, "following": False, "unknown": True}
        return {"self": False, "following": following}

    async def follow(self, sub: str, user_id: int, on: bool) -> dict[str, Any]:
        self.decks._writes_switch()
        uid, _name = self._me(sub)
        if str(user_id) == uid:
            raise DeckError("invalid", refusals.FOLLOW_SELF)
        await self.decks._call(sub, lambda t: self.client.follow_user(t, user_id, on=on))
        self._following.pop(sub, None)
        self._following_next.pop(sub, None)
        self.state.db.audit("user_followed", sub=sub, detail={"user_id": user_id, "on": on})
        return {"following": on}

    def note_root(self, sub: str, deck: Deck) -> None:
        """Remember the comment thread root of a deck the member just read."""
        if len(self._roots) >= ROOT_MAX:
            self._roots.clear()
        self._roots[(sub, str(deck.id))] = (time.monotonic(), deck.comment_root)

    async def _root(self, sub: str, deck_id: str, *, reuse: bool) -> int | None:
        """The deck's comment thread root: from the member's own read in the last ROOT_TTL seconds
        when ``reuse`` (their writes), else read from the deck."""
        key = (sub, str(deck_id))
        hit = self._roots.get(key)
        if reuse and hit is not None and time.monotonic() - hit[0] < ROOT_TTL:
            return hit[1]
        deck = await self.deck(sub, deck_id)
        self.note_root(sub, deck)
        return deck.comment_root

    async def comments(
        self, sub: str, deck_id: str, page: int = 1, *, keep_doc: bool = False, reuse_root: bool = False
    ) -> dict[str, Any]:
        page = max(1, min(int(page), MAX_PAGE))
        root = await self._root(sub, deck_id, reuse=reuse_root and self.state.db.get_link(sub) is not None)
        if root is None:
            return {"root": None, "count": 0, "comments": [], "page": 1, "has_more": False}
        linked = self.state.db.get_link(sub) is not None
        if linked:
            body = await self.decks._call(sub, lambda t: self.client.comment_thread(t, root, page=page))
        else:
            async with self.decks.archidekt_slot(sub):
                body = await self.client.comment_thread(None, root, page=page)
        kids = body.get("children")
        results = kids.get("results") if isinstance(kids, dict) else kids if isinstance(kids, list) else []
        links = kids.get("links") if isinstance(kids, dict) else {}
        count = kids.get("count") if isinstance(kids, dict) else None
        comments = [comment_out(c, keep_doc=keep_doc) for c in results if isinstance(c, dict)]
        link = self.state.db.get_link(sub) if linked else None
        raw_me = str((link or {}).get("archidekt_user_id") or "")
        me = int(raw_me) if raw_me.isdigit() else None
        return {
            "root": root,
            "count": count if isinstance(count, int) else len(comments),
            "comments": comments,
            "page": page,
            "has_more": bool(isinstance(links, dict) and links.get("next")),
            # the member's own Archidekt user id, so the page can offer Edit and Delete on their comments
            "me": me,
        }

    async def find_comment(
        self, sub: str, deck_id: str, comment_id: int, page: int = 1, *, keep_doc: bool = False
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """(the comment, the thread page it is on): looked for on ``page``, the page of the thread it
        was shown on. The thread is ordered by points, so a vote since can move a comment to the
        page beside it, and a new one is on the last page: those are read only when it is not
        where it was shown."""
        thread = await self.comments(sub, deck_id, page, keep_doc=keep_doc, reuse_root=True)
        found = _find(thread["comments"], comment_id)
        near = [thread["page"] - 1, thread["page"] + 1]
        # A comment just posted has no points yet, so it sits on the last page of a long thread
        # although the page draws it at the top. A full page tells
        # the page size, and the count of comments then gives the last page.
        if thread["has_more"] and thread["comments"]:
            near.append(-(-int(thread["count"]) // len(thread["comments"])))
        seen = {thread["page"]}
        for other_page in near:
            if found is not None or other_page < 1 or other_page in seen:
                continue
            if other_page > thread["page"] and not thread["has_more"]:
                continue
            seen.add(other_page)
            other = await self.comments(sub, deck_id, other_page, keep_doc=keep_doc, reuse_root=True)
            found = _find(other["comments"], comment_id)
            if found is not None:
                thread = other
        if found is None:
            raise DeckError("not_found", NOT_IN_THREAD)
        return found, thread

    def _own_comment(self, sub: str, found: dict[str, Any]) -> dict[str, Any]:
        """``found`` when it is the member's own comment, else a refusal."""
        uid, _name = self._me(sub)
        if str(found["owner"]["id"]) != uid:
            raise DeckError("forbidden", refusals.NOT_OWN_COMMENT)
        return found

    async def edit_comment(
        self,
        sub: str,
        deck_id: str,
        comment_id: int,
        text: str,
        *,
        expect_doc: str | None = None,
        page: int = 1,
    ) -> dict[str, Any]:
        """Change the text of one of the member's own comments (Archidekt's edit); verified by
        re-reading the thread. The new words are plain, and the comment's bold text, links and
        pictures stay where their words did not change (``formatting.keep_formatting``).
        ``expect_doc``: for an approved proposal, the document its review showed ("" for plain
        text); a comment whose stored document gives another one now is not sent."""
        self.decks._writes_switch()
        text = comment_text(text)
        if not text:
            raise DeckError("invalid", refusals.EMPTY_COMMENT)
        if len(text) > MAX_COMMENT:
            raise DeckError("invalid", refusals.comment_too_long(MAX_COMMENT))
        found, thread = await self.find_comment(sub, deck_id, comment_id, page, keep_doc=True)
        self._own_comment(sub, found)
        kept = formatting.keep_formatting(found.get("_doc"), text)
        if expect_doc is not None and (kept[0] if kept else "") != expect_doc:
            raise DeckError(
                "stale",
                "This comment changed on Archidekt since the change was reviewed, so it would not "
                "look as reviewed. Nothing was sent to Archidekt.",
            )
        write: str = QuillDoc(kept[0]) if kept else text
        await self.decks._call(sub, lambda t: self.client.comment_update(t, comment_id, write))
        after = _find(
            (await self.comments(sub, deck_id, thread["page"], reuse_root=True))["comments"], comment_id
        )
        ok = after is not None and after["text"] == text
        self.state.db.audit(
            "deck_comment_edited", sub=sub, detail={"deck_id": deck_id, "comment": comment_id, "verified": ok}
        )
        if not ok:
            raise DeckError(
                "verify_mismatch", "Archidekt accepted the edit but the thread does not show it yet"
            )
        return {"comment": after}

    async def delete_comment(self, sub: str, deck_id: str, comment_id: int, page: int = 1) -> dict[str, Any]:
        """Delete one of the member's own comments; verified by re-reading the thread page it was on."""
        self.decks._writes_switch()
        found, thread = await self.find_comment(sub, deck_id, comment_id, page)
        self._own_comment(sub, found)
        await self.decks._call(sub, lambda t: self.client.comment_delete(t, comment_id))
        after = await self.comments(sub, deck_id, thread["page"], reuse_root=True)
        gone = _find(after["comments"], comment_id) is None
        self.state.db.audit(
            "deck_comment_deleted",
            sub=sub,
            detail={"deck_id": deck_id, "comment": comment_id, "verified": gone},
        )
        if not gone:
            raise DeckError(
                "verify_mismatch", "Archidekt accepted the request but the comment is still there"
            )
        return {"deleted": comment_id, "count": after["count"]}

    async def comment(
        self, sub: str, deck_id: str, text: str, parent: int | None, page: int = 1
    ) -> dict[str, Any]:
        self.decks._writes_switch()
        _uid, name = self._me(sub)
        text = comment_text(text)
        if not text:
            raise DeckError("invalid", refusals.EMPTY_COMMENT)
        if len(text) > MAX_COMMENT:
            raise DeckError("invalid", refusals.comment_too_long(MAX_COMMENT))
        if parent is None:
            root = await self._root(sub, deck_id, reuse=True)
            if root is None:
                raise DeckError("unavailable", "Archidekt reports no thread for this deck")
            target = root
        else:
            try:
                await self.find_comment(sub, deck_id, parent, page)
            except DeckError as exc:
                if exc.kind != "not_found":  # Archidekt busy, a session to relink: said as it is
                    raise
                raise DeckError("invalid", NOT_IN_THREAD) from exc
            target = parent
        created = await self.decks._call(sub, lambda t: self.client.comment_create(t, target, text))
        self.state.db.audit("deck_commented", sub=sub, detail={"deck_id": deck_id, "parent": target})
        out = comment_out(created)
        if not out["owner"]["username"]:
            out["owner"]["username"] = name
        if out["owner"].get("id") is None and _uid.isdigit():
            out["owner"]["id"] = int(_uid)  # so the page offers Edit and Delete on it at once
        return {"comment": out, "parent": target}


def add_social_routes(server: MCPServer, state: AppState) -> SocialService:
    s = state.settings
    service = SocialService(state)
    state.social = service
    state.decks.social = service  # actions.py proposes and applies through it

    def who(request: Request, *, write: bool) -> str | Response:
        sub, sid = browser_session(state, request)
        if not sub or not sid:
            return JSONResponse(
                {"ok": False, "error": "unauthenticated", "login": "/login"}, 401, headers=NO_STORE
            )
        if write:
            if not csrf_header_ok(s, sid, request):
                return _fail("csrf", "Reload the page and retry.")
        return sub

    async def body(request: Request) -> dict[str, Any] | Response:
        raw = await read_limited(request, 64_000)
        if raw is None:
            return _fail("invalid", "request too large")
        try:
            data = json.loads(raw or b"{}")
        except (ValueError, RecursionError):
            return _fail("invalid", "bad JSON")
        return data if isinstance(data, dict) else _fail("invalid", "expected an object")

    def deck_id(request: Request) -> str:
        return _clean_deck_id(request.path_params["deck_id"])

    def user_id(request: Request) -> int:
        raw = str(request.path_params["user_id"])
        if not raw.isdigit() or len(raw) > 12:
            raise DeckError("invalid", "user id is not a number")
        return int(raw)

    @server.custom_route("/social/api/decks/{deck_id}/vote", methods=["POST"], include_in_schema=False)
    async def vote(request: Request) -> Response:
        sub = who(request, write=True)
        if isinstance(sub, Response):
            return sub
        data = await body(request)
        if isinstance(data, Response):
            return data
        want = {"up": VOTE_UP, "down": VOTE_DOWN, "none": VOTE_NONE}.get(str(data.get("vote")))
        if want is None:
            return _fail("invalid", "vote must be up, down or none")
        try:
            out = await service.vote(sub, deck_id(request), want)
        except (DeckError, ArchidektError) as exc:
            return _err(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/social/api/decks/{deck_id}/bookmark", methods=["POST"], include_in_schema=False)
    async def bookmark(request: Request) -> Response:
        sub = who(request, write=True)
        if isinstance(sub, Response):
            return sub
        data = await body(request)
        if isinstance(data, Response):
            return data
        if not isinstance(data.get("on"), bool):
            return _fail("invalid", "on must be true or false")
        try:
            out = await service.bookmark(sub, deck_id(request), data["on"])
        except (DeckError, ArchidektError) as exc:
            return _err(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/social/api/users/{user_id}/follow", methods=["GET"], include_in_schema=False)
    async def follow_state(request: Request) -> Response:
        sub = who(request, write=False)
        if isinstance(sub, Response):
            return sub
        try:
            out = await service.follow_state(sub, user_id(request))
        except (DeckError, ArchidektError) as exc:
            return _err(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/social/api/users/{user_id}/follow", methods=["POST"], include_in_schema=False)
    async def follow(request: Request) -> Response:
        sub = who(request, write=True)
        if isinstance(sub, Response):
            return sub
        data = await body(request)
        if isinstance(data, Response):
            return data
        if not isinstance(data.get("on"), bool):
            return _fail("invalid", "on must be true or false")
        try:
            out = await service.follow(sub, user_id(request), data["on"])
        except (DeckError, ArchidektError) as exc:
            return _err(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/social/api/decks/{deck_id}/comments", methods=["GET"], include_in_schema=False)
    async def comments(request: Request) -> Response:
        sub = who(request, write=False)
        if isinstance(sub, Response):
            return sub
        try:
            page = max(1, min(int(request.query_params.get("page") or 1), 1000))
        except ValueError:
            page = 1
        try:
            out = await service.comments(sub, deck_id(request), page)
        except (DeckError, ArchidektError) as exc:
            return _err(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/social/api/decks/{deck_id}/comments", methods=["POST"], include_in_schema=False)
    async def comment(request: Request) -> Response:
        sub = who(request, write=True)
        if isinstance(sub, Response):
            return sub
        data = await body(request)
        if isinstance(data, Response):
            return data
        parent = data.get("parent")
        if parent is not None and _int(parent) is None:
            return _fail("invalid", "parent must be a comment id")
        if not isinstance(data.get("text"), str):
            return _fail("invalid", "text must be a string")
        try:
            out = await service.comment(sub, deck_id(request), data["text"], parent, page_of(request, data))
        except (DeckError, ArchidektError) as exc:
            return _err(exc)
        return JSONResponse({"ok": True, **out}, status_code=201, headers=NO_STORE)

    def page_of(request: Request, data: dict[str, Any] | None = None) -> int:
        """The page of the thread a comment was shown on (1 when not said)."""
        raw = (data or {}).get("page", request.query_params.get("page"))
        try:
            return max(1, min(int(raw or 1), MAX_PAGE))
        except (TypeError, ValueError):
            return 1

    def comment_id(request: Request) -> int | None:
        raw = str(request.path_params.get("comment_id") or "")
        return int(raw) if raw.isdigit() and len(raw) <= 12 else None

    @server.custom_route(
        "/social/api/decks/{deck_id}/comments/{comment_id}", methods=["PATCH"], include_in_schema=False
    )
    async def edit_comment(request: Request) -> Response:
        sub = who(request, write=True)
        if isinstance(sub, Response):
            return sub
        cid = comment_id(request)
        if cid is None:
            return _fail("invalid", "comment id must be a number")
        data = await body(request)
        if isinstance(data, Response):
            return data
        if not isinstance(data.get("text"), str):
            return _fail("invalid", "text must be a string")
        try:
            out = await service.edit_comment(
                sub, deck_id(request), cid, data["text"], page=page_of(request, data)
            )
        except (DeckError, ArchidektError) as exc:
            return _err(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route(
        "/social/api/decks/{deck_id}/comments/{comment_id}/vote", methods=["POST"], include_in_schema=False
    )
    async def vote_comment(request: Request) -> Response:
        sub = who(request, write=True)
        if isinstance(sub, Response):
            return sub
        cid = comment_id(request)
        if cid is None:
            return _fail("invalid", "comment id must be a number")
        data = await body(request)
        if isinstance(data, Response):
            return data
        want = {"up": VOTE_UP, "down": VOTE_DOWN, "none": VOTE_NONE}.get(str(data.get("vote")))
        if want is None:
            return _fail("invalid", "vote must be up, down or none")
        try:
            out = await service.vote_comment(sub, deck_id(request), cid, want, page_of(request, data))
        except (DeckError, ArchidektError) as exc:
            return _err(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route(
        "/social/api/decks/{deck_id}/comments/{comment_id}", methods=["DELETE"], include_in_schema=False
    )
    async def delete_comment(request: Request) -> Response:
        sub = who(request, write=True)
        if isinstance(sub, Response):
            return sub
        cid = comment_id(request)
        if cid is None:
            return _fail("invalid", "comment id must be a number")
        try:
            out = await service.delete_comment(sub, deck_id(request), cid, page_of(request))
        except (DeckError, ArchidektError) as exc:
            return _err(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    return service
