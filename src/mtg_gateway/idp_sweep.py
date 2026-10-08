"""Hourly clean-up of removed members' stored Archidekt sessions, asked of Authentik's API.

The live membership check (membership.py) deletes a removed member's stored Archidekt session the
next time they reach the gateway. Someone who never comes back would keep one on the server until
it expires. This sweep closes that gap: every hour it asks Authentik who is in MTG_REQUIRED_GROUP
(and MTG_ADMIN_GROUP) right now and deletes the stored session of every linked member who is in
neither, or whose Authentik user is deactivated or gone.

It is optional. It needs an Authentik API token (MTG_AUTHENTIK_API_TOKEN_FILE, a Docker secret)
whose user may only view groups (``authentik_core.view_group``); docs/IDP-AUTHENTIK.md has the
steps. Without one it stays off, with a single warning at start.

Authentik's answer (``GET /api/v3/core/groups/?name=...``, verified against Authentik 2026.8.3,
recorded in tests/fixtures/authentik-2026.8.3/): each group carries ``users_obj``, its direct members, each
with ``uid`` (the ``sub`` Authentik gives this gateway in its default "hashed user ID" subject
mode) and ``is_active``. Direct membership is also what the groups claim the gateway signs people
in with lists, so both agree.

It deletes nothing unless the answer is whole and makes sense (``plan``): every group found by
its exact name, every member entry well formed, and at least one person the gateway knows found
among the allowed members (else the subject mode, the token's rights or the group names are wrong,
and every link would look removed). An error, a timeout or a refused token deletes nothing,
and the next try waits longer.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

INTERVAL = 3600.0  # seconds between sweeps
MAX_BACKOFF = 6 * 3600.0  # the longest wait after failures in a row
FIRST_DELAY = 120.0  # the first sweep after start, once the gateway is up
TIMEOUT = 20.0
MAX_GROUPS_PAGE = 20


class SweepRefused(Exception):
    """The answer can't be trusted: delete nothing this round. The message is safe to log."""


@dataclass(frozen=True)
class Plan:
    remove: list[str]  # subs whose stored Archidekt session goes
    allowed: int  # active members found in the groups
    linked: int


def members(body: Any, name: str) -> list[dict[str, Any]]:
    """The direct members of the group called exactly ``name`` in one groups-list answer, each
    ``{"uid", "is_active"}``. Raises SweepRefused unless exactly one group has that name and every
    member entry is well formed."""
    if not isinstance(body, dict) or not isinstance(body.get("results"), list):
        raise SweepRefused("unexpected answer shape from Authentik")
    pagination = body.get("pagination")
    if isinstance(pagination, dict) and int(pagination.get("total_pages") or 1) > 1:
        raise SweepRefused("Authentik's answer was split into pages")
    found = [g for g in body["results"] if isinstance(g, dict) and g.get("name") == name]
    if len(found) != 1:
        raise SweepRefused(
            f"group {name!r} found {len(found)} times (is the name right, and may the token view groups?)"
        )
    users = found[0].get("users_obj")
    if not isinstance(users, list):
        raise SweepRefused(f"group {name!r} came without its members (users_obj)")
    out = []
    for u in users:
        if not isinstance(u, dict) or not isinstance(u.get("uid"), str) or not u["uid"]:
            raise SweepRefused(f"a member of {name!r} has no uid")
        if not isinstance(u.get("is_active"), bool):
            raise SweepRefused(f"a member of {name!r} has no is_active")
        out.append({"uid": u["uid"], "is_active": u["is_active"]})
    return out


def plan(groups: dict[str, list[dict[str, Any]]], linked: list[str], known: list[str]) -> Plan:
    """Which linked members to clean up, given each group's members. ``known`` is every member
    the gateway has (linked or not). Raises SweepRefused when the answer doesn't make sense."""
    allowed = {m["uid"] for ms in groups.values() for m in ms if m["is_active"]}
    if not allowed:
        raise SweepRefused("Authentik says nobody is in the groups; not trusting an empty answer")
    if not allowed.intersection(known):
        raise SweepRefused(
            "nobody the gateway knows is among the members Authentik lists: check that the "
            "provider's subject mode is 'Based on the User's hashed ID' and the group names"
        )
    remove = sorted(sub for sub in linked if sub not in allowed)
    return Plan(remove=remove, allowed=len(allowed), linked=len(linked))


class AuthentikSweep:
    def __init__(self, settings: Any, db: Any, decks: Any, http: httpx.AsyncClient | None = None):
        self.settings = settings
        self.db = db
        self.decks = decks
        self.api = settings.authentik_api_url
        self._token = settings.authentik_api_token
        self._http = http
        self.groups = [g for g in (settings.required_group, settings.admin_group) if g]
        self.last: dict[str, Any] = {"ok": None, "at": None, "removed": 0, "error": None}

    @property
    def enabled(self) -> bool:
        return bool(self._token and self.api and self.settings.required_group)

    def why_off(self) -> str | None:
        """The one start-up warning when the sweep is off for a reason the operator can fix."""
        if self.enabled:
            return None
        if self.settings.authentik_api_token_problem:
            return (
                "removed-member clean-up is off: the Authentik API token can't be read "
                f"({self.settings.authentik_api_token_problem}); it is optional (docs/IDP-AUTHENTIK.md)"
            )
        if self._token and not self.settings.required_group:
            return "removed-member clean-up is off: it needs MTG_REQUIRED_GROUP"
        return None

    async def _group(self, http: httpx.AsyncClient, name: str) -> list[dict[str, Any]]:
        try:
            r = await http.get(
                f"{self.api}/api/v3/core/groups/",
                params={
                    "name": name,
                    "include_users": "true",
                    "include_children": "false",
                    "include_parents": "false",
                    "include_inherited_roles": "false",
                    "page_size": str(MAX_GROUPS_PAGE),
                },
                headers={"Authorization": f"Bearer {self._token}", "Accept": "application/json"},
                timeout=TIMEOUT,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise SweepRefused(f"Authentik could not be reached ({type(exc).__name__})") from exc
        if r.status_code != 200:
            raise SweepRefused(f"Authentik answered HTTP {r.status_code}")
        try:
            body = r.json()
        except ValueError as exc:
            raise SweepRefused("Authentik's answer was not JSON") from exc
        return members(body, name)

    async def run_once(self) -> Plan:
        """One sweep. Raises SweepRefused (nothing deleted) when Authentik can't be asked or its
        answer can't be trusted."""
        http = self._http or httpx.AsyncClient(timeout=TIMEOUT)
        try:
            answers = {name: await self._group(http, name) for name in self.groups}
        finally:
            if self._http is None:
                await http.aclose()
        links = await asyncio.to_thread(self.db.active_links)
        known = await asyncio.to_thread(self.db.all_user_subs)
        p = plan(answers, [row["sub"] for row in links], known)
        by_sub = {row["sub"]: row["secret_enc"] for row in links}
        removed: list[str] = []
        for sub in p.remove:
            # only_secret: a member who relinked meanwhile is left alone (seen next round)
            if self.db.revoke_link(sub, only_secret=by_sub[sub]):
                self.db.audit(
                    "archidekt_link_swept",
                    sub=sub,
                    detail={"reason": "not in the required or admin group at the identity provider"},
                )
                removed.append(sub)
        if removed:
            logger.info("removed-member clean-up deleted %d stored Archidekt session(s)", len(removed))
        return Plan(remove=removed, allowed=p.allowed, linked=p.linked)

    async def loop(self) -> None:
        delay = FIRST_DELAY
        failures = 0
        while True:
            await asyncio.sleep(delay)
            try:
                p = await self.run_once()
                self.last.update(ok=True, at=int(time.time()), removed=len(p.remove), error=None)
                failures, delay = 0, INTERVAL
            except SweepRefused as exc:
                failures += 1
                delay = min(INTERVAL * (2 ** (failures - 1)), MAX_BACKOFF)
                self.last.update(ok=False, at=int(time.time()), error=str(exc))
                logger.warning("removed-member clean-up skipped, nothing deleted: %s", exc)
            except Exception:
                failures += 1
                delay = min(INTERVAL * (2 ** (failures - 1)), MAX_BACKOFF)
                self.last.update(ok=False, at=int(time.time()), error="unexpected error")
                logger.exception("removed-member clean-up failed, nothing more deleted this round")
