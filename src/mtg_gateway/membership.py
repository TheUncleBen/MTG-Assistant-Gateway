"""Live membership: ask the identity provider, not a copy from the last sign-in.

Removing someone from MTG_REQUIRED_GROUP (or MTG_ADMIN_GROUP) at the identity provider must take
effect on their next request, but the provider tells nobody: Authentik, for one, keeps honouring
that person's refresh token and reports their tokens active (verified against Authentik 2026.2.2).
Only its userinfo endpoint answers with the groups as they are now.

So the gateway keeps the provider's own access and refresh tokens from each sign-in (encrypted
with the Fernet key) and, before serving any request that carries a session cookie or a bearer
token, asks userinfo for that person's current groups. The answer is cached for
MTG_MEMBERSHIP_CHECK_TTL seconds (5 by default, 0 = every request) to spare the identity provider
from page bursts; that is the worst-case delay. Outcomes:

- still allowed: the groups are recorded, so the admin check sees a removal at once too;
- no longer in the required group, deactivated or deleted (userinfo and refresh refused), or no
  provider tokens on file: every token, browser session and the Archidekt link are revoked;
- the provider cannot be reached: the request is refused (fail closed) and nothing is revoked,
  so service resumes when the provider is back.
"""

from __future__ import annotations

import asyncio
import logging
import time
from enum import Enum
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .oidc import IdPTokens, IdPUnavailable, resolve_groups

logger = logging.getLogger(__name__)

# The provider access token is renewed this long before its stated expiry.
EXPIRY_MARGIN = 30
MAX_CACHED = 10_000


class Membership(Enum):
    ALLOWED = "allowed"
    REVOKED = "revoked"  # not a member (any more); everything of theirs was revoked
    UNAVAILABLE = "unavailable"  # the provider could not be asked; refuse this request only


class MembershipChecker:
    def __init__(self, settings: Any, db: Any, oidc: Any):
        self.settings = settings
        self.db = db
        self.oidc = oidc
        self.fernet = Fernet(settings.fernet_key.encode())
        self._checked: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    # -- storage ----------------------------------------------------------------
    def store(self, sub: str, tokens: IdPTokens) -> None:
        """Keep the provider tokens from a sign-in or refresh (encrypted)."""
        self.db.save_idp_grant(
            sub,
            refresh_enc=self._seal(tokens.refresh_token),
            access_enc=self._seal(tokens.access_token),
            access_expires_at=int(tokens.access_expires_at or 0),
        )
        self._checked[sub] = time.monotonic()

    def forget(self, sub: str) -> None:
        self._checked.pop(sub, None)

    def _seal(self, value: str | None) -> str:
        return self.fernet.encrypt(value.encode()).decode() if value else ""

    def _open(self, value: str) -> str | None:
        if not value:
            return None
        try:
            return self.fernet.decrypt(value.encode()).decode()
        except InvalidToken:
            return None

    # -- the check ----------------------------------------------------------------
    async def check(self, sub: str) -> Membership:
        """Is ``sub`` still allowed in, according to the identity provider right now?"""
        ttl = self.settings.membership_check_ttl
        last = self._checked.get(sub)
        if ttl and last is not None and time.monotonic() - last < ttl:
            return Membership.ALLOWED
        lock = self._locks.setdefault(sub, asyncio.Lock())
        async with lock:
            last = self._checked.get(sub)  # a concurrent request may have just checked
            if ttl and last is not None and time.monotonic() - last < ttl:
                return Membership.ALLOWED
            outcome = await self._ask(sub)
            if outcome is Membership.ALLOWED:
                if len(self._checked) >= MAX_CACHED:
                    self._checked.clear()
                self._checked[sub] = time.monotonic()
            else:
                self._checked.pop(sub, None)
        if len(self._locks) > MAX_CACHED:
            self._locks.clear()
        return outcome

    async def _ask(self, sub: str) -> Membership:
        user = self.db.get_user(sub)
        if user is None or user.get("disabled_at"):
            # Handled by the existing checks (unknown or disabled users are refused everywhere).
            return Membership.REVOKED
        grant = self.db.get_idp_grant(sub)
        if grant is None:
            # Signed in before this check existed (or the grant was cleared): sign in again once.
            return self._revoke(sub, "no_idp_grant", None, removed=False)
        access = self._open(grant["access_enc"])
        info: dict[str, Any] | None = None
        try:
            if access and int(grant["access_expires_at"] or 0) - EXPIRY_MARGIN > time.time():
                info = await self.oidc.userinfo(access)
            if info is None:
                refresh = self._open(grant["refresh_enc"])
                if not refresh:
                    # The provider gave no refresh token (offline_access not granted): its access
                    # token has run out, so the person signs in again.
                    return self._revoke(sub, "no_idp_refresh_token", None, removed=False)
                tokens = await self.oidc.refresh(refresh)
                if tokens is None:
                    return self._revoke(sub, "idp_refused_refresh", None, removed=False)
                self.db.save_idp_grant(
                    sub,
                    refresh_enc=self._seal(tokens.refresh_token),
                    access_enc=self._seal(tokens.access_token),
                    access_expires_at=int(tokens.access_expires_at or 0),
                )
                info = await self.oidc.userinfo(tokens.access_token or "")
                if info is None:
                    return self._revoke(sub, "idp_refused_userinfo", None, removed=False)
        except IdPUnavailable as exc:
            logger.warning("membership check could not reach the identity provider: %s", exc)
            return Membership.UNAVAILABLE
        if str(info.get("sub")) != sub:
            return self._revoke(sub, "idp_subject_mismatch", None)
        groups = resolve_groups(info, self.settings.oidc_groups_claim)
        required = self.settings.required_group
        if required and required not in groups:
            return self._revoke(sub, "not_in_group", groups)
        if groups != user.get("groups"):
            self.db.set_user_groups(sub, groups)
            self.db.audit("groups_changed", sub=sub, detail={"groups": groups[:50]})
        return Membership.ALLOWED

    def _revoke(self, sub: str, reason: str, groups: list[str] | None, *, removed: bool = True) -> Membership:
        """Revoke every token and browser session of ``sub``. ``removed`` means the provider said
        the person is out (not merely unverifiable): their Archidekt link is revoked as well."""
        if not removed:
            n = sum(self.db.revoke_all_for_user(sub).values())
            self.db.audit("membership_unverifiable", sub=sub, detail={"reason": reason, "revoked": n})
            return Membership.REVOKED
        if groups is None:
            # Keep the recorded groups minus the required one, so every stored-groups check
            # (pages, API, refresh, admin) refuses as well.
            user = self.db.get_user(sub) or {}
            groups = [g for g in user.get("groups") or [] if g not in self._privileged()]
        n = self.db.drop_member(sub, groups)
        self.db.audit("membership_revoked", sub=sub, detail={"reason": reason, "revoked": n})
        logger.info("membership check revoked a member's access (%s)", reason)
        return Membership.REVOKED

    def _privileged(self) -> set[str]:
        return {g for g in (self.settings.required_group, self.settings.admin_group) if g}
