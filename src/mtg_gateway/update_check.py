"""Tells the gateway's admins when a newer release is out.

Twice a day the gateway asks GitHub for the project's newest release (the public releases API,
no token) and compares its version with its own. When the release is newer, every page shows
admins a short banner, and the admin overview shows the update steps for a Portainer or Swarm
stack and for docker compose. It is a notice only: the gateway never pulls an image, never talks
to Docker and never restarts itself; whoever runs it updates when they choose.

``MTG_UPDATE_CHECK=false`` turns the lookup off, and nothing is sent to GitHub then. The request
carries no member data: it is one anonymous GET with the gateway's version in the User-Agent.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

import httpx

from . import __version__

logger = logging.getLogger(__name__)

REPO = "TheUncleBen/MTG-Assistant-Gateway"
LATEST_API = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases"
FIRST_DELAY = 60.0  # let the gateway finish starting first
INTERVAL = 12 * 3600.0
RETRY = 3600.0  # after a failed lookup
MAX_BYTES = 1_000_000

_VERSION = re.compile(r"^v?(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$")

# The last lookup, read by every page render (theme.py) and the admin overview. Module state, like
# backup.last_run, so render() needs no handle on the app.
last: dict[str, Any] = {"enabled": False, "at": None, "ok": None, "latest": None, "url": None, "error": None}


def parse_version(value: Any) -> tuple[int, int, int] | None:
    """``0.7.12`` or ``v0.7.12`` as a comparable tuple; None for anything else (pre-releases too)."""
    if not isinstance(value, str):
        return None
    m = _VERSION.match(value.strip())
    return (int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def newer() -> dict[str, str] | None:
    """The newer release found by the last good lookup (``version``, ``url``), or None."""
    found = parse_version(last.get("latest"))
    running = parse_version(__version__)
    if not last.get("enabled") or found is None or running is None or found <= running:
        return None
    return {"version": str(last["latest"]), "url": str(last.get("url") or RELEASES_PAGE)}


def status() -> dict[str, Any]:
    """For the admin overview and /api/v1/admin/overview."""
    out: dict[str, Any] = {"running": __version__, "check": "on" if last["enabled"] else "off"}
    if last["enabled"]:
        out.update(
            latest=last["latest"],
            checked_at=last["at"],
            error=last["error"],
            update_available=newer() is not None,
        )
    return out


def read_release(data: Any) -> tuple[str, str]:
    """The version and page of GitHub's answer for ``releases/latest``; ValueError when it is not
    a release of this project with a plain MAJOR.MINOR.PATCH tag."""
    if not isinstance(data, dict):
        raise ValueError("unexpected answer")
    tag = data.get("tag_name")
    version = parse_version(tag)
    if version is None or data.get("draft") or data.get("prerelease"):
        raise ValueError("the newest release has no MAJOR.MINOR.PATCH tag")
    url = data.get("html_url")
    if not (isinstance(url, str) and url.startswith(RELEASES_PAGE + "/")):
        url = RELEASES_PAGE
    return "{}.{}.{}".format(*version), url


class UpdateChecker:
    def __init__(self, enabled: bool, http: httpx.AsyncClient | None = None):
        self.enabled = enabled
        self._http = http
        last.update(enabled=enabled)

    async def check_once(self) -> None:
        http = self._http or httpx.AsyncClient(timeout=httpx.Timeout(15.0), follow_redirects=False)
        try:
            r = await http.get(
                LATEST_API,
                headers={
                    "Accept": "application/vnd.github+json",
                    "User-Agent": f"mtg-assistant-gateway/{__version__} update-check",
                },
            )
            if r.status_code != 200:
                raise ValueError(f"GitHub answered HTTP {r.status_code}")
            if len(r.content) > MAX_BYTES:
                raise ValueError("answer too large")
            version, url = read_release(r.json())
        finally:
            if self._http is None:
                await http.aclose()
        was = newer()
        last.update(at=int(time.time()), ok=True, latest=version, url=url, error=None)
        now = newer()
        if now and now != was:
            logger.warning(
                "gateway %s is available (this one runs %s): %s. Admins see the update steps on /admin",
                now["version"],
                __version__,
                now["url"],
            )

    async def loop(self) -> None:
        delay = FIRST_DELAY
        while True:
            await asyncio.sleep(delay)
            try:
                await self.check_once()
                delay = INTERVAL
            except Exception as exc:  # a notice only: never let it disturb the gateway
                last.update(at=int(time.time()), ok=False, error=_short(exc))
                logger.info("update check failed, trying again in an hour: %s", _short(exc))
                delay = RETRY


def _short(exc: Exception) -> str:
    if isinstance(exc, ValueError):
        return str(exc)[:200]
    if isinstance(exc, httpx.TimeoutException):
        return "GitHub did not answer in time"
    if isinstance(exc, httpx.HTTPError):
        return "could not reach GitHub"
    return "unexpected error"
