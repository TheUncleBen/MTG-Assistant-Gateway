"""Each member's profile picture, as their identity provider describes it.

The provider's claims arrive at sign-in and with every membership check (membership.py). These
are used, and nothing else is fetched:

- an image embedded in the claim (``data:image/png;base64,...``): Authentik 2026.8.0 to 2026.8.2
  send a picture the member uploaded to their Authentik profile this way. PNG, JPEG, GIF and WebP
  are kept after their first bytes are checked; SVG never is (it can carry script);
- an https address on Gravatar, which Authentik sends when its avatar setting uses Gravatar. The
  gateway fetches it once per change, so a member's browser never contacts Gravatar itself;
- ``mtg_picture_version`` from the optional Authentik scope mapping (docs/IDP-AUTHENTIK.md): Authentik
  2026.8.3 and newer leave uploaded pictures out of ``picture``, and the mapping sends only this
  fingerprint, so the gateway asks userinfo for the image (``mtg_picture=1``) once per change.

Any other address is ignored: the claim can come from a user-editable attribute, and fetching
whatever it names would let a member make the gateway call into its own network. Without a usable
picture the account menu shows the member's initials, drawn by the gateway.

Pictures are files under ``<data dir>/avatars`` named by a hash of the subject, so they need no
database change and can always be fetched again; "Delete my data" removes them.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hashlib
import html
import json
import logging
import re
from collections.abc import Awaitable, Callable, Coroutine
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

MAX_BYTES = 2 * 1024 * 1024
FETCH_TIMEOUT = 8.0
GRAVATAR_HOSTS = frozenset({"gravatar.com", "www.gravatar.com", "secure.gravatar.com"})
_DATA_URI = re.compile(r"data:(image/(?:png|jpeg|jpg|gif|webp));base64,(.*)", re.S | re.I)
# A picture's own first bytes decide its type; the type a claim or server states only has to agree.
_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)
_VERSION = re.compile(r"[A-Za-z0-9_-]{1,64}")
_COLOURS = ("#c2410c", "#b45309", "#15803d", "#0e7490", "#1d4ed8", "#6d28d9", "#be185d", "#4d7c0f")


def sniff(data: bytes) -> str | None:
    """The image type of ``data`` from its first bytes, or None for anything else."""
    for magic, kind in _MAGIC:
        if data.startswith(magic):
            return kind
    if len(data) > 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return None


def decode_data_uri(value: str) -> tuple[bytes, str] | None:
    m = _DATA_URI.fullmatch(value.strip())
    if not m or len(m.group(2)) > MAX_BYTES * 4 // 3 + 4:
        return None
    try:
        data = base64.b64decode(re.sub(r"\s+", "", m.group(2)), validate=True)
    except (binascii.Error, ValueError):
        return None
    kind = sniff(data)
    return (data, kind) if kind and len(data) <= MAX_BYTES else None


def gravatar_url(value: str) -> str | None:
    try:
        u = urlparse(value.strip())
    except ValueError:
        return None
    if u.scheme != "https" or (u.hostname or "").lower() not in GRAVATAR_HOSTS or u.port not in (None, 443):
        return None
    if u.username or u.password:
        return None
    return value.strip()


def initials_svg(name: str, sub: str) -> bytes:
    """A round badge with up to two initials, in a colour picked from the subject."""
    words = [w for w in re.split(r"[\s._@-]+", name) if w and w[0].isalnum()]
    letters = "".join(w[0] for w in words[:2]).upper() or "?"
    colour = _COLOURS[int(hashlib.sha256(sub.encode()).hexdigest(), 16) % len(_COLOURS)]
    return (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 40 40' width='40' height='40'>"
        f"<circle cx='20' cy='20' r='20' fill='{colour}'/>"
        "<text x='20' y='20' dy='.35em' text-anchor='middle' font-family='Inter,system-ui,sans-serif' "
        f"font-size='16' font-weight='700' fill='#fff'>{html.escape(letters[:2])}</text></svg>"
    ).encode()


class AvatarStore:
    def __init__(self, directory: Path, *, http: httpx.AsyncClient | None = None):
        self.dir = directory
        self._http = http
        self._tasks: set[asyncio.Task[None]] = set()
        self._busy: set[str] = set()
        self._dropped: set[str] = set()

    def _paths(self, sub: str) -> tuple[Path, Path]:
        key = hashlib.sha256(sub.encode()).hexdigest()
        return self.dir / f"{key}.img", self.dir / f"{key}.json"

    def _meta(self, sub: str) -> dict[str, Any]:
        try:
            meta = json.loads(self._paths(sub)[1].read_text())
        except (OSError, ValueError):
            return {}
        return meta if isinstance(meta, dict) else {}

    def get(self, sub: str) -> tuple[bytes, str] | None:
        """The member's stored picture and its type, or None."""
        img, _ = self._paths(sub)
        kind = self._meta(sub).get("type")
        try:
            data = img.read_bytes()
        except OSError:
            return None
        return (data, kind) if isinstance(kind, str) and sniff(data) == kind else None

    def delete(self, sub: str) -> None:
        if sub in self._busy:
            self._dropped.add(sub)  # a fetch under way must not bring it back
        for p in self._paths(sub):
            p.unlink(missing_ok=True)

    def _save(self, sub: str, source: str, data: bytes | None, kind: str | None) -> None:
        if sub in self._dropped:
            return
        img, meta = self._paths(sub)
        self.dir.mkdir(parents=True, exist_ok=True)
        if data is None:
            img.unlink(missing_ok=True)
        else:
            tmp = img.with_suffix(".tmp")
            tmp.write_bytes(data)
            tmp.replace(img)
        meta.write_text(json.dumps({"source": source, "type": kind}))

    async def update(
        self,
        sub: str,
        claims: dict[str, Any],
        fetch_uploaded: Callable[[], Awaitable[str | None]] | None = None,
    ) -> None:
        """Bring the stored picture in line with the provider's claims (sign-in or userinfo).
        Cheap when nothing changed (compared by hash); never raises.

        ``mtg_picture_version`` comes from the optional Authentik scope mapping for uploaded
        pictures (docs/IDP-AUTHENTIK.md): when it changes, ``fetch_uploaded`` asks for the image
        itself, once. Otherwise the standard ``picture`` claim is used."""
        version = claims.get("mtg_picture_version")
        if isinstance(version, str) and _VERSION.fullmatch(version) and fetch_uploaded is not None:
            source = hashlib.sha256(f"uploaded:{version}".encode()).hexdigest()
            try:
                if self._meta(sub).get("source") != source:
                    self._start(sub, self._fetch_uploaded(sub, source, fetch_uploaded))
            except OSError as exc:
                logger.warning("could not read a stored profile picture: %s", exc)
            return
        picture = claims.get("picture")
        value = picture if isinstance(picture, str) else ""
        source = hashlib.sha256(value.encode()).hexdigest()
        try:
            if self._meta(sub).get("source") == source:
                return
            decoded = decode_data_uri(value) if value.startswith("data:") else None
            if decoded is not None:
                self._save(sub, source, *decoded)
                return
            url = gravatar_url(value) if value else None
            if url is None:
                self._save(sub, source, None, None)  # none, or one the gateway won't use: initials
                return
            self._start(sub, self._fetch(sub, source, url))
        except OSError as exc:
            logger.warning("could not store a profile picture: %s", exc)

    def _start(self, sub: str, job: Coroutine[Any, Any, None]) -> None:
        """Run ``job`` in the background, one per member at a time (checks come every few
        seconds; a slow fetch must not be started again by each of them)."""
        if sub in self._busy:
            job.close()
            return
        self._busy.add(sub)

        async def run() -> None:
            try:
                await job
            finally:
                self._busy.discard(sub)
                self._dropped.discard(sub)

        task = asyncio.create_task(run())
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _fetch_uploaded(
        self, sub: str, source: str, fetch_uploaded: Callable[[], Awaitable[str | None]]
    ) -> None:
        try:
            value = await fetch_uploaded()
        except Exception as exc:  # the provider's trouble: nothing saved, the next check tries again
            logger.info("could not fetch an uploaded profile picture: %s", type(exc).__name__)
            return
        decoded = decode_data_uri(value) if isinstance(value, str) else None
        try:
            self._save(sub, source, *(decoded or (None, None)))
        except OSError as exc:
            logger.warning("could not store a profile picture: %s", exc)

    async def _fetch(self, sub: str, source: str, url: str) -> None:
        data: bytes | None = None
        kind: str | None = None
        client = self._http or httpx.AsyncClient(timeout=FETCH_TIMEOUT, follow_redirects=False)
        try:
            async with client.stream("GET", url, headers={"Accept": "image/*"}) as resp:
                if resp.status_code == 200:
                    buf = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buf += chunk
                        if len(buf) > MAX_BYTES:
                            break
                    else:
                        kind = sniff(bytes(buf))
                        data = bytes(buf) if kind else None
        except httpx.HTTPError as exc:
            logger.info("could not fetch a Gravatar picture: %s", type(exc).__name__)
            return  # nothing saved: the next check tries again
        finally:
            if self._http is None:
                await client.aclose()
        try:
            self._save(sub, source, data, kind if data else None)
        except OSError as exc:
            logger.warning("could not store a profile picture: %s", exc)

    async def aclose(self) -> None:
        for task in list(self._tasks):
            task.cancel()
