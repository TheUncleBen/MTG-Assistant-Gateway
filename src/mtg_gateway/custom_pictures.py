"""A custom card's picture on the gateway's own pages (parity release 2, 23 Oct).

Archidekt gives each custom card's picture as an address in the deck JSON (``frontImageUrl`` /
``backImageUrl`` of a ``customCards`` row's card). The live host is not known yet, and the pages
load pictures only from the gateway and Scryfall (their CSP), so the gateway fetches the picture
itself and serves it from its own address: ``/decks/api/custom_picture/{deck_id}/{custom_card_id}``.

The address is never taken from the request: only from the deck as Archidekt answers it for the
signed-in member. The fetch is https only (port 443), never follows a redirect, ignores proxy
settings from the environment, takes FETCH_TIMEOUT seconds at most in all, refuses a host that resolves
to a private, loopback or other non-public address, reads at most MAX_BYTES, and keeps the answer
only when its first bytes are a PNG, JPEG, GIF or WebP picture. Pictures are kept a while in memory
(at most KEEP pictures), so a deck page's pictures cost one fetch each. The host is checked when
the address is resolved, before the connection; a host that answers a public address then and
another one a moment later (DNS rebinding) is not caught, which the address coming from Archidekt
only makes acceptable.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import socket
import time
from collections import OrderedDict
from typing import Any
from urllib.parse import urlsplit

import httpx
from starlette.requests import Request
from starlette.responses import Response

from .avatars import sniff
from .decks import DeckError
from .pages import browser_session

logger = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024  # Archidekt redraws a custom card's picture as a 500 x 700 PNG
FETCH_TIMEOUT = 10.0
KEEP = 64  # pictures kept in memory
KEEP_SECONDS = 3600.0
FACES = ("front", "back")
_KEPT: OrderedDict[str, tuple[float, bytes, str]] = OrderedDict()


# The picture addresses of each member's own custom cards, as My lists last read them: the My lists
# rows' pictures are served from these, with no Archidekt read of their own.
_LISTED: dict[str, dict[int, tuple[str, str]]] = {}
LISTED_MAX = 1000  # custom cards remembered per member


def remember(sub: str, items: list[dict[str, Any]]) -> None:
    """Keep the picture addresses of ``items`` (account_lists custom card rows) for ``sub``."""
    mine = _LISTED.setdefault(sub, {})
    for i in items:
        cid = i.get("id")
        if isinstance(cid, int) and not isinstance(cid, bool):
            mine[cid] = (str(i.get("frontImageUrl") or ""), str(i.get("backImageUrl") or ""))
    while len(mine) > LISTED_MAX:
        mine.pop(next(iter(mine)))


def forget(sub: str) -> None:
    _LISTED.pop(sub, None)


def picture_url(deck: Any, custom_card_id: int, face: str) -> str:
    """The picture address of one custom card in ``deck`` (Deck.custom_cards), or ''."""
    for c in getattr(deck, "custom_cards", []) or []:
        if c.custom_card_id == custom_card_id:
            return (c.back_image_url if face == "back" else c.image_url) or ""
    return ""


def _public_host(host: str) -> bool:
    """Every address ``host`` resolves to is a public one."""
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except OSError:
        return False
    addrs = {info[4][0] for info in infos}
    return bool(addrs) and all(ipaddress.ip_address(a.split("%")[0]).is_global for a in addrs)


async def fetch(url: str, http: httpx.AsyncClient | None = None) -> tuple[bytes, str] | None:
    """The picture at ``url`` and its type, or None (not a picture, too big, not reachable)."""
    hit = _KEPT.get(url)
    if hit and hit[0] > time.monotonic():
        _KEPT.move_to_end(url)
        return hit[1], hit[2]
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return None
    try:
        port = parts.port
    except ValueError:  # not a port at all, or out of range
        return None
    if port not in (None, 443):  # the picture host's own https port only
        return None
    if not await asyncio.to_thread(_public_host, parts.hostname):
        return None
    # no proxy settings from the environment (as cimd.py), and one time limit for the whole fetch
    client = http or httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False, trust_env=False)
    buf = bytearray()
    try:
        async with asyncio.timeout(FETCH_TIMEOUT):
            async with client.stream("GET", url, headers={"Accept": "image/*"}) as resp:
                if resp.status_code != 200:
                    return None
                async for chunk in resp.aiter_bytes():
                    buf += chunk
                    if len(buf) > MAX_BYTES:
                        return None
    except TimeoutError:
        logger.info("a custom card picture took longer than %s s", FETCH_TIMEOUT)
        return None
    except httpx.HTTPError as exc:
        logger.info("could not fetch a custom card picture: %s", type(exc).__name__)
        return None
    finally:
        if http is None:
            await client.aclose()
    kind = sniff(bytes(buf))
    if kind is None:
        return None
    _KEPT[url] = (time.monotonic() + KEEP_SECONDS, bytes(buf), kind)
    _KEPT.move_to_end(url)
    while len(_KEPT) > KEEP:
        _KEPT.popitem(last=False)
    return bytes(buf), kind


def add_custom_picture_routes(server: Any, state: Any) -> None:
    async def _serve(url: str) -> Response:
        got = await fetch(url, getattr(state, "custom_picture_http", None)) if url else None
        if got is None:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        data, kind = got
        return Response(
            data,
            media_type=kind,
            headers={
                "Cache-Control": "private, max-age=3600",
                "X-Content-Type-Options": "nosniff",
                "Content-Security-Policy": "default-src 'none'; sandbox",
            },
        )

    @server.custom_route(
        "/lists/api/custom_picture/{custom_card_id}", methods=["GET"], include_in_schema=False
    )
    async def listed_picture(request: Request) -> Response:
        """One of the member's own custom cards' pictures on My lists, from the address the page's
        last read of the list gave (no Archidekt read of its own); 404 for any other card."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return Response(status_code=401, headers={"Cache-Control": "no-store"})
        raw_id = str(request.path_params["custom_card_id"])
        face = request.query_params.get("face") or "front"
        if not raw_id.isdigit() or face not in FACES:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        front, back = _LISTED.get(sub, {}).get(int(raw_id), ("", ""))
        return await _serve(back if face == "back" else front)

    @server.custom_route(
        "/decks/api/custom_picture/{deck_id}/{custom_card_id}", methods=["GET"], include_in_schema=False
    )
    async def custom_picture(request: Request) -> Response:
        """One custom card's picture in a deck the member can read, from the address Archidekt
        gives for it (``face`` front or back); 404 when there is none or it can't be fetched (the
        page then shows the card's name and type instead)."""
        sub, _sid = browser_session(state, request)
        if not sub:
            return Response(status_code=401, headers={"Cache-Control": "no-store"})
        raw_id = str(request.path_params["custom_card_id"])
        face = request.query_params.get("face") or "front"
        if not raw_id.isdigit() or face not in FACES:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        try:
            deck = await state.decks.get_deck(sub, str(request.path_params["deck_id"]))
        except DeckError:
            return Response(status_code=404, headers={"Cache-Control": "no-store"})
        return await _serve(picture_url(deck, int(raw_id), face))
