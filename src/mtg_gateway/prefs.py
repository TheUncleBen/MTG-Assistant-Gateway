"""Remembered choices that follow the account (0.7.29).

Everything a member picks once and expects to find again on their other devices is kept on their
account (``account_prefs``, schema 13): "View as" on the deck page, My decks, card search and the
collection; the card size; the stack order of each deck and grouping; the deck stats toggles; and
the two dismissed tips (the EDHPlay steps and the scan intro). Light or dark stays on each device.

Three things stay on each device on purpose: the last deck picked in "Add to
deck", the last set used per deck in the printing picker, and a scan in progress.

The choices a device held before 0.7.29 are copied to the account once: the view cookies here,
on the first page that reads them (the cookie is then removed, so "Forget my view choices" is
not undone by another device later), and the browser-stored ones by static/prefs.js.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from .theme import CARD_SIZE, CARD_SIZE_PAGES, VIEW_COOKIE, VIEW_KINDS

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState
    from .db import Database

VIEW = "view."  # + one of theme.VIEW_KINDS
# CARD_SIZE: the one size every page used before 2026-10-28, still read by a page without its own;
# CARD_SIZE + "." + one of theme.CARD_SIZE_PAGES: that page's own size
CARD_SIZES = ("s", "m", "l")
DECKSTATS = "deckstats"
EDHPLAY_STEPS = "edhplay.steps"
SCAN_INTRO = "scan.intro"
STACK = "stack:"  # + deck id + ":" + grouping
MAX_STACK_ORDERS = 150  # the newest 150; saving one more forgets the oldest
MAX_STACK_NAMES = 300  # stacks in one order (a deck has far fewer groups)
# Set by "Forget my view choices" and kept: from then on a device's pre-0.7.29 choices (a view
# cookie, the browser's stored ones) are never copied to the account, so a device not used since
# the update can't bring the forgotten choices back. Not a choice: the pages' API can't read or
# set it.
FORGOTTEN = "prefs.forgotten"

CARD_SIZE_NAMES = (CARD_SIZE, *(f"{CARD_SIZE}.{page}" for page in CARD_SIZE_PAGES))

_STACK_NAME = re.compile(r"^stack:(\d{1,12}):([a-z0-9_-]{0,40})$")
_WORD = re.compile(r"^[a-z0-9_-]{1,40}$")


def valid(name: str, value: Any) -> bool:
    """Whether ``value`` is a well-formed choice called ``name`` (anything else is refused, so the
    store holds only what the pages read back)."""
    if name.startswith(VIEW):
        return name[len(VIEW) :] in VIEW_KINDS and isinstance(value, str) and bool(_WORD.match(value))
    if name in CARD_SIZE_NAMES:
        return value in CARD_SIZES
    if name in (EDHPLAY_STEPS, SCAN_INTRO):
        return value is True
    if name == DECKSTATS:
        return (
            isinstance(value, dict)
            and len(value) <= 12
            and all(
                isinstance(k, str)
                and _WORD.match(k)
                and (isinstance(v, bool) or (isinstance(v, str) and len(v) <= 40))
                for k, v in value.items()
            )
        )
    if _STACK_NAME.match(name):
        return (
            isinstance(value, list)
            and 0 < len(value) <= MAX_STACK_NAMES
            and all(isinstance(v, str) and len(v) <= 120 for v in value)
        )
    return False


def save(db: Database, sub: str, name: str, value: Any) -> bool:
    """Remember one choice (None forgets it). False when it is not a valid choice or the store
    refused it (MAX_PREFS_PER_ACCOUNT, MAX_PREF_BYTES)."""
    if value is None:
        if not (valid_name(name)):
            return False
        db.delete_pref(sub, name)
        return True
    if not valid(name, value):
        return False
    return db.set_pref(sub, name, value, keep_newest=(STACK, MAX_STACK_ORDERS))


def valid_name(name: str) -> bool:
    return (
        (name.startswith(VIEW) and name[len(VIEW) :] in VIEW_KINDS)
        or name in (*CARD_SIZE_NAMES, DECKSTATS, EDHPLAY_STEPS, SCAN_INTRO)
        or bool(_STACK_NAME.match(name))
    )


def forget(db: Database, sub: str) -> int:
    """ "Forget my view choices": views, card size, stack orders, stats toggles and the
    dismissed tips. Other things kept on the account (notifications, approval mode) stay."""
    n = db.delete_prefs(
        sub,
        names=(*CARD_SIZE_NAMES, DECKSTATS, EDHPLAY_STEPS, SCAN_INTRO),
        prefixes=(VIEW, STACK),
    )
    db.set_pref(sub, FORGOTTEN, True)
    return n


def copies_refused(db: Database, sub: str) -> bool:
    """True once the member used "Forget my view choices": old per-device choices aren't copied."""
    return db.get_pref(sub, FORGOTTEN) is True


def card_sizes(db: Database, sub: str, page: str) -> dict[str, Any]:
    """What theme.set_card_size needs to draw ``page``: its own size, the printings dialog's, the
    one size chosen before for every page, and the "forgotten" marker."""
    names = (CARD_SIZE, f"{CARD_SIZE}.{page}", f"{CARD_SIZE}.printings", FORGOTTEN)
    return {n: v for n in names if (v := db.get_pref(sub, n)) is not None}


def from_this_site(request: Request) -> bool:
    """Whether the browser says the request came from this gateway's own pages, or was typed or
    bookmarked (``Sec-Fetch-Site``). A browser that doesn't send it counts as this site unless its
    ``Origin`` or ``Referer`` names another one. A link on another site still opens the page, but
    changes nothing kept on the account."""
    site = request.headers.get("sec-fetch-site")
    if site is not None:
        return site in ("none", "same-origin")
    here = _origin(_public_url(request))
    for header in ("origin", "referer"):
        value = request.headers.get(header)
        if value and value != "null" and _origin(value) != here:
            return False
    return True


def _public_url(request: Request) -> str:
    gateway = getattr(request.app.state, "gateway", None)
    url = getattr(getattr(gateway, "settings", None), "public_url", None)
    return url if isinstance(url, str) and url else str(request.base_url)


def _origin(url: str) -> str:
    """``scheme://host[:port]`` of a URL, lowercased; empty for one without a host."""
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}".lower()


def view_choice(
    db: Database, sub: str, request: Request, kind: str, allowed: tuple[str, ...] | list[str], default: str
) -> tuple[str, bool]:
    """The view to render for ``kind``: the address's ``view`` when it is a known one, else the
    account's, else the one this browser's cookie held before 0.7.29, else ``default``. True:
    remember it on the account (a new pick, or the cookie's value copied once). A view in a link
    from another site is shown but not remembered (:func:`from_this_site`)."""
    allowed = tuple(allowed)
    stored = db.get_pref(sub, VIEW + kind)
    asked = request.query_params.get("view")
    if asked in allowed:
        if not from_this_site(request):
            return asked, False
        return asked, asked != stored or VIEW_COOKIE + kind in request.cookies
    if stored in allowed:
        return stored, VIEW_COOKIE + kind in request.cookies
    cookie = request.cookies.get(VIEW_COOKIE + kind)
    if cookie in allowed:
        if copies_refused(db, sub):  # forgotten: the cookie goes, its view isn't copied
            return default, True
        return cookie, True
    return default, False


def remember_view(
    db: Database, sub: str, response: Response, kind: str, view: str, *, secure: bool
) -> Response:
    """Keep ``view`` on the account and remove the old per-browser cookie."""
    assert kind in VIEW_KINDS
    db.set_pref(sub, VIEW + kind, view)
    response.delete_cookie(VIEW_COOKIE + kind, path="/", secure=secure, httponly=True, samesite="lax")
    return response


def page_prefs(db: Database, sub: str | None, *names: str) -> str:
    """The choices a page's scripts read at once, as ``<script id='mtg-prefs'>`` JSON (no request,
    no flash of the default): only ``names`` (exact names, or a name ending in ':' as a prefix), and
    the "forgotten" marker, so prefs.js never shows a device's old choice it won't copy."""
    if not sub:
        return ""
    stored = db.get_prefs(sub)
    names = (*names, FORGOTTEN)
    out = {
        k: v for k, v in stored.items() if any(k == n or (n.endswith(":") and k.startswith(n)) for n in names)
    }
    blob = json.dumps(out, separators=(",", ":")).replace("<", "\\u003c")
    return f"<script id='mtg-prefs' type='application/json'>{blob}</script>"


def add_prefs_routes(server: MCPServer, state: AppState) -> None:
    """GET /api/v1/prefs and POST /api/v1/prefs for the pages' scripts. The member's own browser
    only: an assistant or an app with a token has no business with how the pages look."""
    from .api import caller_for, json_body

    async def who(request: Request, *, write: bool) -> str | Response:
        caller = await caller_for(state, request, write=write)
        if isinstance(caller, Response):
            return caller
        if caller.via != "browser":
            return JSONResponse(
                {"ok": False, "error": "browser_required", "message": "Only the gateway's pages use this."},
                403,
            )
        return caller.sub

    @server.custom_route("/api/v1/prefs", methods=["GET"], include_in_schema=False)
    async def get_prefs(request: Request) -> Response:
        sub = await who(request, write=False)
        if isinstance(sub, Response):
            return sub
        mine = {k: v for k, v in state.db.get_prefs(sub).items() if valid_name(k)}
        return JSONResponse({"ok": True, "prefs": mine}, headers={"Cache-Control": "no-store"})

    @server.custom_route("/api/v1/prefs", methods=["POST"], include_in_schema=False)
    async def set_prefs(request: Request) -> Response:
        """``{"set": {name: value or null}}`` saves each (null forgets it); ``{"copy": {...}}``
        saves only the names the account doesn't have yet (the one-time copy from this device).
        Answers which names were saved and which were refused."""
        sub = await who(request, write=True)
        if isinstance(sub, Response):
            return sub
        data = await json_body(request)
        if isinstance(data, Response):
            return data
        changes, copy = data.get("set") or {}, data.get("copy") or {}
        if not isinstance(changes, dict) or not isinstance(copy, dict) or len(changes) + len(copy) > 400:
            return JSONResponse({"ok": False, "error": "invalid", "message": "set and copy are objects"}, 400)
        have = state.db.get_prefs(sub) if copy else {}
        if copy and have.get(FORGOTTEN) is True:
            copy = {}  # forgotten: this device's old choices are dropped, not copied
        saved, refused = [], []
        for name, value in [*((n, v) for n, v in copy.items() if n not in have), *changes.items()]:
            (saved if save(state.db, sub, str(name), value) else refused).append(str(name))
        return JSONResponse(
            {"ok": True, "saved": saved, "refused": refused}, headers={"Cache-Control": "no-store"}
        )
