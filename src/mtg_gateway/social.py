"""Archidekt's social actions for the signed-in person: like a deck (a vote on its comment thread),
bookmark it, follow its owner, read and post comments.

These are a person's own clicks, so they exist only as browser routes: every write needs the
browser session and the page's CSRF token, every call runs under that member's own linked
Archidekt session, and no MCP tool exists for any of them. An assistant can therefore never like,
follow or comment on anyone's behalf. The page asks for a confirmation before each write.

Routes and bodies are the ones archidekt.com's own pages send (read from its bundle on
2026-10-07; see the notes on ``ArchidektClient.vote_deck`` and friends).
"""

from __future__ import annotations

import hmac
import json
import re
import time
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .archidekt import VOTE_DOWN, VOTE_NONE, VOTE_UP, ArchidektError, Deck
from .decklist import clean_text
from .decks import DeckError, _clean_deck_id
from .pages import _csrf, browser_session, read_limited

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

MAX_COMMENT = 2000
MAX_THREAD_DEPTH = 6
FOLLOWING_PAGES = 5  # how far into the member's following list the follow state is looked for
FOLLOWING_TTL = 120.0  # seconds the following list is remembered after a read
FRESH_GAP = 15.0  # seconds between forced re-reads of the following list (a collaborator lookup)
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
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
}
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _fail(kind: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"ok": False, "error": kind, "message": message}, _STATUS.get(kind, 400), headers=NO_STORE
    )


def _err(exc: Exception) -> JSONResponse:
    if isinstance(exc, (DeckError, ArchidektError)):
        kind = exc.kind
        msg = str(exc)
        if kind in ("auth", "forbidden"):
            msg = "Archidekt did not accept your linked session; relink it on the Account page."
        if kind == "contract":
            msg = "Archidekt answered in an unexpected way; try again."
        return _fail(kind, msg)
    raise exc


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def comment_out(c: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """One comment of Archidekt's thread in the gateway's shape; replies nest to a depth limit."""
    owner = c.get("owner") if isinstance(c.get("owner"), dict) else {}
    kids = c.get("children")
    results = kids.get("results") if isinstance(kids, dict) else kids if isinstance(kids, list) else []
    return {
        "id": _int(c.get("id")),
        "text": str(c.get("text") or ""),
        "owner": {
            "id": _int(owner.get("id")),
            "username": str(owner.get("username") or ""),
        },
        "created_at": str(c.get("createdAt") or ""),
        "edited_at": str(c.get("editedAt") or "") or None,
        "points": _int(c.get("points")) or 0,
        "user_vote": _int(c.get("userInput")) or 0,
        "archived": c.get("archived") is True,
        "replies": [comment_out(k, depth + 1) for k in results if isinstance(k, dict)]
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
        self._following_fresh: dict[str, float] = {}  # sub -> when a fresh read was last forced

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
        self._me(sub)
        deck = await self.deck(sub, deck_id)
        if deck.comment_root is None:
            raise DeckError("unavailable", "Archidekt reports no thread for this deck, so it cannot be liked")
        if want == VOTE_NONE:
            await self.decks._call(
                sub, lambda t: self.client.vote_deck(t, deck.comment_root, up=True, remove=True)
            )
        else:
            await self.decks._call(
                sub, lambda t: self.client.vote_deck(t, deck.comment_root, up=want == VOTE_UP)
            )
        weight = {VOTE_UP: 1, VOTE_DOWN: -1, VOTE_NONE: 0}
        points = deck.points - weight[deck.user_vote] + weight[want]
        self.state.db.audit("deck_voted", sub=sub, detail={"deck_id": deck.id, "vote": want})
        return {"vote": want, "points": points}

    async def vote_comment(self, sub: str, deck_id: str, comment_id: int, want: int) -> dict[str, Any]:
        """Vote on one comment of the deck's thread (first page): PUT /comments/vote/{id}/ with
        {up, remove}, the body archidekt.com's own comment vote sends (its bundle, read 2026-10-10).
        The new score is worked out the way the site does, from the thread's points and the
        member's earlier vote."""
        uid, _name = self._me(sub)
        thread = await self.comments(sub, deck_id)
        found = _find(thread["comments"], comment_id)
        if found is None:
            raise DeckError(
                "not_found", "that comment is not in this deck's thread (or not on its first page)"
            )
        if str(found["owner"]["id"]) == uid:
            raise DeckError("invalid", "that is your own comment")
        if found["archived"]:
            raise DeckError("invalid", "that comment is archived on Archidekt")
        if found["user_vote"] == want:  # already so: nothing is sent (Archidekt might toggle it)
            return {"comment": comment_id, "vote": want, "points": found["points"]}
        if want == VOTE_NONE:
            await self.decks._call(sub, lambda t: self.client.vote_deck(t, comment_id, up=True, remove=True))
        else:
            await self.decks._call(sub, lambda t: self.client.vote_deck(t, comment_id, up=want == VOTE_UP))
        weight = {VOTE_UP: 1, VOTE_DOWN: -1, VOTE_NONE: 0}
        before = found["user_vote"] if found["user_vote"] in weight else VOTE_NONE
        points = found["points"] - weight[before] + weight[want]
        self.state.db.audit(
            "comment_voted", sub=sub, detail={"deck_id": deck_id, "comment": comment_id, "vote": want}
        )
        return {"comment": comment_id, "vote": want, "points": points}

    async def bookmark(self, sub: str, deck_id: str, on: bool) -> dict[str, Any]:
        self._me(sub)
        await self.decks._call(sub, lambda t: self.client.bookmark_deck(t, deck_id, on=on))
        self.state.db.audit("deck_bookmarked", sub=sub, detail={"deck_id": deck_id, "on": on})
        return {"bookmarked": on}

    async def following(self, sub: str) -> set[int]:
        return set(await self.following_names(sub))

    async def following_names(self, sub: str, *, fresh: bool = False) -> dict[int, str]:
        """The people the member follows on Archidekt: user id -> username (cached briefly;
        ``fresh`` reads Archidekt again)."""
        uid, _name = self._me(sub)
        hit = self._following.get(sub)
        now = time.monotonic()
        if fresh and hit and now - self._following_fresh.get(sub, -FRESH_GAP) < FRESH_GAP:
            fresh = False  # one forced read per FRESH_GAP: a run of typos does not re-read every page
        if fresh:
            self._following_fresh[sub] = now
        if hit and hit[0] > now and not fresh:
            return hit[1]
        users: dict[int, str] = {}
        page = 1
        while page <= FOLLOWING_PAGES:
            body = await self.decks._call(sub, lambda t, page=page: self.client.following(t, uid, page))
            for row in body["results"]:
                if isinstance(row, dict) and _int(row.get("id")) is not None:
                    users[int(row["id"])] = str(row.get("username") or "")
            if not body.get("next"):
                break
            page += 1
        self._following[sub] = (time.monotonic() + FOLLOWING_TTL, users)
        return users

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
        row = next((c for c in await self.collaborators(sub, deck_id) if c["user_id"] == user_id), None)
        if row is None:
            return {"removed": False, "already": True, "verified": True}
        await self.decks._call(sub, lambda t: self.client.remove_deck_editor(t, row["editor_id"]))
        self.state.db.audit("collaborator_removed", sub=sub, detail={"deck_id": deck_id, "user_id": user_id})
        if any(c["user_id"] == user_id for c in await self.collaborators(sub, deck_id, owned=True)):
            raise DeckError("verify_mismatch", "Archidekt accepted the request but the deck still lists them")
        return {"removed": True, "verified": True}

    async def follow_state(self, sub: str, user_id: int) -> dict[str, Any]:
        uid, _name = self._me(sub)
        if str(user_id) == uid:
            return {"self": True, "following": False}
        return {"self": False, "following": user_id in await self.following(sub)}

    async def follow(self, sub: str, user_id: int, on: bool) -> dict[str, Any]:
        uid, _name = self._me(sub)
        if str(user_id) == uid:
            raise DeckError("invalid", "you cannot follow yourself")
        await self.decks._call(sub, lambda t: self.client.follow_user(t, user_id, on=on))
        self._following.pop(sub, None)
        self.state.db.audit("user_followed", sub=sub, detail={"user_id": user_id, "on": on})
        return {"following": on}

    async def comments(self, sub: str, deck_id: str, page: int = 1) -> dict[str, Any]:
        deck = await self.deck(sub, deck_id)
        if deck.comment_root is None:
            return {"root": None, "count": 0, "comments": [], "page": 1, "has_more": False}
        linked = self.state.db.get_link(sub) is not None
        if linked:
            body = await self.decks._call(
                sub, lambda t: self.client.comment_thread(t, deck.comment_root, page=page)
            )
        else:
            async with self.decks.archidekt_slot(sub):
                body = await self.client.comment_thread(None, deck.comment_root, page=page)
        kids = body.get("children")
        results = kids.get("results") if isinstance(kids, dict) else kids if isinstance(kids, list) else []
        links = kids.get("links") if isinstance(kids, dict) else {}
        count = kids.get("count") if isinstance(kids, dict) else None
        comments = [comment_out(c) for c in results if isinstance(c, dict)]
        link = self.state.db.get_link(sub) if linked else None
        raw_me = str((link or {}).get("archidekt_user_id") or "")
        me = int(raw_me) if raw_me.isdigit() else None
        return {
            "root": deck.comment_root,
            "count": count if isinstance(count, int) else len(comments),
            "comments": comments,
            "page": page,
            "has_more": bool(isinstance(links, dict) and links.get("next")),
            # the member's own Archidekt user id, so the page can offer Edit and Delete on their comments
            "me": me,
        }

    def _own_comment(self, sub: str, thread: dict[str, Any], comment_id: int) -> dict[str, Any]:
        """The member's own comment ``comment_id`` from the thread's first page, else a refusal."""
        uid, _name = self._me(sub)
        found = _find(thread["comments"], comment_id)
        if found is None:
            raise DeckError(
                "not_found", "that comment is not in this deck's thread (or not on its first page)"
            )
        if str(found["owner"]["id"]) != uid:
            raise DeckError("forbidden", "only your own comments can be edited or deleted here")
        return found

    async def edit_comment(self, sub: str, deck_id: str, comment_id: int, text: str) -> dict[str, Any]:
        """Change the text of one of the member's own comments (Archidekt's edit); verified by
        re-reading the thread."""
        text = _CONTROL.sub("", text.replace("\r\n", "\n")).strip()
        if not text:
            raise DeckError("invalid", "write something first")
        if len(text) > MAX_COMMENT:
            raise DeckError("invalid", f"a comment can be at most {MAX_COMMENT} characters")
        thread = await self.comments(sub, deck_id)
        self._own_comment(sub, thread, comment_id)
        await self.decks._call(sub, lambda t: self.client.comment_update(t, comment_id, text))
        after = _find((await self.comments(sub, deck_id))["comments"], comment_id)
        ok = after is not None and after["text"] == text
        self.state.db.audit(
            "deck_comment_edited", sub=sub, detail={"deck_id": deck_id, "comment": comment_id, "verified": ok}
        )
        if not ok:
            raise DeckError(
                "verify_mismatch", "Archidekt accepted the edit but the thread does not show it yet"
            )
        return {"comment": after}

    async def delete_comment(self, sub: str, deck_id: str, comment_id: int) -> dict[str, Any]:
        """Delete one of the member's own comments; verified by re-reading the thread."""
        thread = await self.comments(sub, deck_id)
        self._own_comment(sub, thread, comment_id)
        await self.decks._call(sub, lambda t: self.client.comment_delete(t, comment_id))
        after = await self.comments(sub, deck_id)
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

    async def comment(self, sub: str, deck_id: str, text: str, parent: int | None) -> dict[str, Any]:
        _uid, name = self._me(sub)
        text = _CONTROL.sub("", text.replace("\r\n", "\n")).strip()
        if not text:
            raise DeckError("invalid", "write something first")
        if len(text) > MAX_COMMENT:
            raise DeckError("invalid", f"a comment can be at most {MAX_COMMENT} characters")
        thread = await self.comments(sub, deck_id)
        root = thread["root"]
        if root is None:
            raise DeckError("unavailable", "Archidekt reports no thread for this deck")
        target = root if parent is None else parent
        if target != root and target not in _ids(thread["comments"]):
            raise DeckError("invalid", "that comment is not in this deck's thread (or not on its first page)")
        created = await self.decks._call(sub, lambda t: self.client.comment_create(t, target, text))
        self.state.db.audit("deck_commented", sub=sub, detail={"deck_id": deck_id, "parent": target})
        out = comment_out(created)
        if not out["owner"]["username"]:
            out["owner"]["username"] = name
        return {"comment": out, "parent": target}


def add_social_routes(server: MCPServer, state: AppState) -> SocialService:
    s = state.settings
    service = SocialService(state)
    state.social = service  # type: ignore[attr-defined]
    state.decks.social = service  # actions.py proposes and applies through it

    def who(request: Request, *, write: bool) -> str | Response:
        sub, sid = browser_session(state, request)
        if not sub or not sid:
            return JSONResponse(
                {"ok": False, "error": "unauthenticated", "login": "/login"}, 401, headers=NO_STORE
            )
        if write:
            expected = _csrf(s, sid) or ""
            given = request.headers.get("x-csrf-token", "")
            if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
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
            out = await service.comment(sub, deck_id(request), data["text"], parent)
        except (DeckError, ArchidektError) as exc:
            return _err(exc)
        return JSONResponse({"ok": True, **out}, status_code=201, headers=NO_STORE)

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
            out = await service.edit_comment(sub, deck_id(request), cid, data["text"])
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
            out = await service.vote_comment(sub, deck_id(request), cid, want)
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
            out = await service.delete_comment(sub, deck_id(request), cid)
        except (DeckError, ArchidektError) as exc:
            return _err(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    return service
