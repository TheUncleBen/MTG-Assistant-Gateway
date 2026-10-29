"""Live membership: ask the identity provider, not a copy from the last sign-in.

Removing someone from MTG_REQUIRED_GROUP (or MTG_ADMIN_GROUP) at the identity provider must take
effect on their next request, but the provider tells nobody: Authentik, for one, keeps honouring
that person's refresh token and reports their tokens active (verified against Authentik 2026.2.2).
Only its userinfo endpoint answers with the groups as they are now.

So the gateway keeps the provider's own access and refresh tokens (a "grant", encrypted with the
Fernet key) from each sign-in, and, before serving any request that carries a session cookie or a
bearer token, asks userinfo for that person's current groups.

Each sign-in keeps its own grant (0.7.46): every browser and the Android app (``s:`` and the
session's id hash), and the member's AI apps together (``ai:`` and the member, from their latest
AI-app sign-in). A request with a session cookie is checked with that session's own grant; one
with a bearer token with any of the member's grants, the one the provider let in last first, since
the AI apps' gateway tokens are the member's, not one device's. A session from before 0.7.46 has
no grant of its own and is checked like a bearer token until it ends.

The answer is cached for MTG_MEMBERSHIP_CHECK_TTL seconds (5 by default, 0 = every request) to
spare the identity provider from page bursts. Past that, a page being read (GET or HEAD outside
/admin, with a session cookie) is answered at once and the provider asked in the background when
it let that sign-in in before: the first page after a while away never waits for the provider.
After a restart that "before" is the yes on file, honoured while it is younger than
STORED_YES_MAX_AGE (a day). Everything else waits for the answer: a write, an admin page, an
assistant's request, and every request once the provider could not be reached (until it answers
again). So a member removed at the provider is refused on their next write, admin page or
assistant request, and can read their own pages only for the moment the background check takes.
With MTG_MEMBERSHIP_CHECK_TTL=0 every request waits for the provider's answer. Outcomes:

- still allowed: the groups are recorded, so the admin check sees a removal at once too;
- no longer in the required group (or the provider names someone else): every token, browser
  session and the Archidekt link are revoked (REVOKED);
- this sign-in is no longer honoured (refresh refused with invalid_grant, userinfo refused, no
  provider tokens on file, no groups claim anywhere): that sign-in alone ends (SIGN_IN). A session
  is signed out and its browser told why; the member's other sessions, their AI apps' tokens and
  the Archidekt link are kept. A bearer token is refused while none of the member's grants is
  honoured, and works again once they sign in on the website: an expired provider refresh token
  can't be told apart from a deactivated account, so only a definite answer revokes. Nothing is
  gained by holding the kept tokens: they are refused until the member signs in at the provider
  again, and still end at their own times;
- the provider cannot be reached, or refuses the gateway itself (invalid_client and the like):
  the request is refused (fail closed) and nothing is revoked, so service resumes when it is back.

While the member is active, each of their sign-ins' grants is kept fresh in the background: the
one in use is renewed shortly before its provider access token runs out, and any other one not
renewed for KEEP_ALIVE is renewed then, so a device left a few days does not find its provider
refresh token run out. A refused renewal decides nothing by itself; the next check of that sign-in
does.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from collections.abc import Awaitable, Callable
from enum import Enum
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from .avatars import AvatarStore
from .db import SESSION_HOLDER, hash_token
from .oidc import IdPTokens, IdPUnavailable, groups_claim_present, resolve_groups
from .timing import add_time, idp_time

logger = logging.getLogger(__name__)

# The provider access token is renewed this long before its stated expiry.
EXPIRY_MARGIN = 30
# After a check that let a sign-in in, the gateway renews its provider access token in the
# background this long before a request would have to (EXPIRY_MARGIN before its expiry), so no
# request waits for a refresh and then userinfo: only for userinfo. Only for a token that runs out
# within RENEW_WINDOW of that check: an idle sign-in's own renewal happens at most once.
RENEW_LEAD = 10
RENEW_WINDOW = 15 * 60
# While a member is active (a check let one of their sign-ins in), each of their other sign-ins'
# grants that was not renewed for this long is renewed in the background, so the provider's refresh
# token of a device left for a while does not run out while its gateway sign-in is still live.
KEEP_ALIVE = 86400
MAX_CACHED = 10_000
# After a restart, a page being read goes ahead on the provider's last yes on file (the grant's
# checked_at) only while that yes is younger than this; an older one waits for the provider's
# answer (security review 2, R2-03), so a member removed while the gateway was down reads nothing.
STORED_YES_MAX_AGE = 86400
# Who holds a grant besides a browser session (SESSION_HOLDER): the member's AI apps, and a sign-in
# on its way to the session it is for.
ASSISTANT_HOLDER = "ai:"
LOGIN_HOLDER = "login:"


# What an AI app (or a phone) is told while none of the member's sign-ins is honoured by the
# provider: its tokens are kept and work again once the member signs in on the website.
SIGN_IN_AGAIN = "Sign in again at {url}, then try again. Your AI apps stay connected."


def session_holder(session_id: str) -> str:
    """The grant holder of the browser session ``session_id`` (the Android app's too)."""
    return SESSION_HOLDER + hash_token(session_id)


def assistant_holder(sub: str) -> str:
    """The grant holder of the member's AI apps."""
    return ASSISTANT_HOLDER + sub


def login_holder(login_id: str) -> str:
    """Where a sign-in's grant waits until it is handed to its session or AI apps."""
    return LOGIN_HOLDER + hash_token(login_id)


class Membership(Enum):
    ALLOWED = "allowed"
    REVOKED = "revoked"  # not a member (any more); everything of theirs was revoked
    SIGN_IN = "sign_in"  # this sign-in is no longer honoured: it signs in again; nothing else ends
    UNAVAILABLE = "unavailable"  # the provider could not be asked; refuse this request only


class MembershipChecker:
    def __init__(self, settings: Any, db: Any, oidc: Any):
        self.settings = settings
        self.db = db
        self.oidc = oidc
        self.fernet = Fernet(settings.fernet_key.encode())
        self._checked: dict[str, float] = {}  # holder -> when the provider last let it in (asked)
        self._owners: dict[str, str] = {}  # holder -> member, for the holders in _checked
        self._unreachable: dict[str, float] = {}  # holders whose last check couldn't reach the provider
        self._locks: dict[str, asyncio.Lock] = {}
        self._renewals: dict[str, asyncio.Task[None]] = {}
        self._refreshing: set[str] = set()  # renewals asking the provider right now
        self._background: dict[str, asyncio.Task[Membership]] = {}  # checks run after a request
        self._warned_no_refresh = False
        # The member's profile picture follows the provider's answer (avatars.py).
        self.avatars = AvatarStore(settings.data_dir / "avatars")

    # -- storage ----------------------------------------------------------------
    def store(self, holder: str, sub: str, tokens: IdPTokens) -> None:
        """Keep the provider tokens from a sign-in or refresh (encrypted) under ``holder``."""
        if not tokens.refresh_token and not self._warned_no_refresh:
            self._warned_no_refresh = True
            logger.warning(
                "the identity provider issued no refresh token (offline_access not in MTG_OIDC_SCOPES "
                "or not allowed for the client): members must sign in again whenever its access "
                "token runs out (docs/IDP-AUTHENTIK.md, docs/IDP-OTHERS.md)"
            )
        self.db.save_idp_grant(
            holder,
            sub,
            refresh_enc=self._seal(tokens.refresh_token),
            access_enc=self._seal(tokens.access_token),
            access_expires_at=int(tokens.access_expires_at or 0),
            signed_in=True,
        )
        self._remember(holder, sub, time.monotonic())

    def adopt(self, old: str, new: str) -> bool:
        """Hand the grant a sign-in stored under ``old`` to the holder it was made for: the new
        browser session, or the member's AI apps (replacing the grant of their last AI sign-in)."""
        if not self.db.move_idp_grant(old, new):
            return False
        sub, asked = self._owners.pop(old, None), self._checked.pop(old, None)
        stale = self._renewals.pop(new, None)
        if stale is not None:
            stale.cancel()  # planned for the grant just replaced
        if sub is not None and asked is not None:
            self._remember(new, sub, asked)
        else:
            self._checked.pop(new, None)
        return True

    def forget(self, sub: str) -> None:
        """Ask the provider about every sign-in of ``sub`` on their next request."""
        for holder in [h for h, owner in self._owners.items() if owner == sub]:
            self._checked.pop(holder, None)
            self._owners.pop(holder, None)

    def _remember(self, holder: str, sub: str, asked: float) -> None:
        if len(self._checked) >= MAX_CACHED and holder not in self._checked:
            self._checked.clear()
            self._owners.clear()
        self._checked[holder] = asked
        self._owners[holder] = sub
        self._unreachable.pop(holder, None)

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
    async def check(self, sub: str, holder: str | None = None, *, page: bool = False) -> Membership:
        """Is ``sub`` still allowed in, according to the identity provider right now? ``holder``
        names the sign-in making the request (a browser session's, session_holder); without one,
        any of the member's sign-ins the provider still honours answers (a bearer token, a phone's
        notification key). ``page``: the request only reads a member page, so with an earlier
        answer on file it never waits for the provider (the module's docstring)."""
        started = time.perf_counter()
        try:
            return await self._check(sub, holder, page)
        finally:
            add_time(idp_time, time.perf_counter() - started)

    async def _check(self, sub: str, holder: str | None, page: bool) -> Membership:
        if holder is None:
            return await self._check_member(sub, page)
        if holder not in self._checked and self.db.get_idp_grant(holder) is None:
            # A session from before each sign-in kept its own grant: the member's grants answer
            # for it until it ends; when none is honoured any more it signs in again.
            outcome = await self._check_member(sub, page)
            if outcome is Membership.SIGN_IN:
                return self._sign_in_again(sub, holder, "no_idp_grant")
            return outcome
        how = self._fresh(sub, holder, page)
        if how == "fresh":
            return Membership.ALLOWED
        if how == "background" and self._check_in_background(holder, sub, holder):
            return Membership.ALLOWED
        return await self._check_now(sub, holder)

    def _fresh(self, sub: str, holder: str, page: bool) -> str | None:
        """ "fresh": the provider's last answer for ``holder`` is reused; "background": the request
        is answered and the provider asked after it; None: the request waits for the answer."""
        ttl = self.settings.membership_check_ttl
        if not ttl or holder in self._unreachable:
            return None  # every request asks, or the provider was out: fail closed until it answers
        last = self._checked.get(holder)
        if last is None:
            if not page:
                return None
            # Not asked since the gateway started: the provider's last yes on file will do for a
            # page being read, while it is younger than STORED_YES_MAX_AGE.
            grant = self.db.get_idp_grant(holder)
            if grant is None or grant.get("sub") != sub or not grant.get("checked_at"):
                return None
            if time.time() - int(grant["checked_at"]) >= STORED_YES_MAX_AGE:
                return None
            return "background"
        if time.monotonic() - last < ttl:
            return "fresh"
        return "background" if page else None

    async def _check_member(self, sub: str, page: bool) -> Membership:
        """Any of the member's sign-ins the provider still honours answers for them."""
        holders = [g["holder"] for g in self.db.idp_grants_for(sub)]
        ways = [self._fresh(sub, h, page) for h in holders]
        if "fresh" in ways:
            return Membership.ALLOWED
        if "background" in ways and self._check_in_background("member:" + sub, sub, None):
            return Membership.ALLOWED
        return await self._check_member_now(sub, holders)

    async def _check_member_now(self, sub: str, holders: list[str] | None = None) -> Membership:
        if holders is None:
            holders = [g["holder"] for g in self.db.idp_grants_for(sub)]
        if not holders:
            user = self.db.get_user(sub)
            if user is None or user.get("disabled_at"):
                return Membership.REVOKED
            return Membership.SIGN_IN  # nothing to ask with: refused until they sign in again
        for holder in holders:
            outcome = await self._check_now(sub, holder)
            if outcome is not Membership.SIGN_IN:
                return outcome
        return Membership.SIGN_IN

    def _check_in_background(self, key: str, sub: str, holder: str | None) -> bool:
        """Ask the provider after this request: one such check per sign-in (or member) at a time.
        Its own context, so its time is not counted against the request (timing.py). False when
        no check is under way for it and none can start: the request then waits for the answer."""
        running = self._background.get(key)
        if running is not None and not running.done():
            return True
        if len(self._background) >= MAX_CACHED:
            return False  # never a task per member without bound, and never a page unchecked
        work = self._check_now(sub, holder) if holder is not None else self._check_member_now(sub)
        task = asyncio.get_running_loop().create_task(work, context=contextvars.Context())
        self._background[key] = task

        def done(t: asyncio.Task[Membership]) -> None:
            if self._background.get(key) is t:
                self._background.pop(key, None)
            if not t.cancelled() and t.exception() is not None:
                logger.warning("background membership check failed: %r", t.exception())

        task.add_done_callback(done)
        return True

    async def _check_now(self, sub: str, holder: str) -> Membership:
        ttl = self.settings.membership_check_ttl
        lock = self._locks.setdefault(holder, asyncio.Lock())
        async with lock:
            last = self._checked.get(holder)  # a concurrent request may have just checked
            if ttl and last is not None and time.monotonic() - last < ttl:
                return Membership.ALLOWED
            asked = time.monotonic()  # the answer is as of the moment it was asked for
            outcome = await self._ask(sub, holder)
            if outcome is Membership.ALLOWED:
                self._remember(holder, sub, asked)
                self.db.mark_idp_grant_checked(holder)
                self._plan_renewal(sub, holder)
                self._keep_alive(sub, holder)
            else:
                self._checked.pop(holder, None)
                self._owners.pop(holder, None)
                if outcome is Membership.UNAVAILABLE:
                    while len(self._unreachable) >= MAX_CACHED:  # the oldest go; dicts keep order
                        del self._unreachable[next(iter(self._unreachable))]
                    self._unreachable[holder] = asked
                else:
                    self._unreachable.pop(holder, None)
                if outcome is Membership.REVOKED:
                    self.forget(sub)
        if len(self._locks) > MAX_CACHED:
            # only the free ones: a lock still held (a check, a renewal) must stay the one used
            for key in [k for k, v in self._locks.items() if not v.locked()]:
                del self._locks[key]
        return outcome

    async def _ask(self, sub: str, holder: str) -> Membership:
        user = self.db.get_user(sub)
        if user is None or user.get("disabled_at"):
            # Handled by the existing checks (unknown or disabled users are refused everywhere).
            return Membership.REVOKED
        grant = self.db.get_idp_grant(holder)
        if grant is None or grant.get("sub") != sub:
            # Ended meanwhile (or never kept): this sign-in signs in again.
            return self._sign_in_again(sub, holder, "no_idp_grant")
        access = self._open(grant["access_enc"])
        refresh = self._open(grant["refresh_enc"])
        expires_at = int(grant["access_expires_at"] or 0)
        claim = self.settings.oidc_groups_claim
        info: dict[str, Any] | None = None
        id_claims: dict[str, Any] | None = None
        refreshed = False
        current = access  # the provider access token that got the answer
        try:
            # Without a refresh token (or a stated expiry) the access token is simply tried: the
            # provider's own 401 says when it has run out.
            if access and (not refresh or not expires_at or expires_at - EXPIRY_MARGIN > time.time()):
                info = await self.oidc.userinfo(access)
            if info is None:
                if not refresh:
                    # No refresh token (offline_access not granted): the provider's access token
                    # has run out, so the person signs in again.
                    return self._sign_in_again(sub, holder, "no_idp_refresh_token")
                tokens = await self._renew(holder, sub, refresh)
                if tokens is None:
                    return self._sign_in_again(sub, holder, "idp_refused_refresh")
                id_claims, refreshed, current = tokens.id_claims, True, tokens.access_token
                info = await self.oidc.userinfo(tokens.access_token or "")
                if info is None:
                    return self._sign_in_again(sub, holder, "idp_refused_userinfo")
            if not groups_claim_present(info, claim) and not refreshed and refresh:
                # Some providers put groups only in the ID token: a refresh returns a fresh one.
                refresh = self._open((self.db.get_idp_grant(holder) or {}).get("refresh_enc", "")) or refresh
                tokens = await self._renew(holder, sub, refresh)
                if tokens is None:
                    return self._sign_in_again(sub, holder, "idp_refused_refresh")
                id_claims, current = tokens.id_claims, tokens.access_token or current
        except IdPUnavailable as exc:
            logger.warning("membership check could not reach the identity provider: %s", exc)
            return Membership.UNAVAILABLE
        if str(info.get("sub")) != sub or (id_claims is not None and str(id_claims.get("sub")) != sub):
            return self._revoke(sub, "idp_subject_mismatch", None)
        if groups_claim_present(info, claim):
            source: dict[str, Any] = info
        elif id_claims is not None and groups_claim_present(id_claims, claim):
            source = id_claims
        elif not self.settings.required_group:
            # MTG_ALLOW_ANY_IDP_USER with a provider that sends no groups at all (Google, Entra
            # ID without a groups claim): there is no group to be removed from. The provider still
            # vouched for the person just now; they hold no group, so no admin rights either.
            if user.get("groups"):
                self.db.set_user_groups(sub, [])
                self.db.audit("groups_changed", sub=sub, detail={"groups": []})
            return Membership.ALLOWED
        else:
            # Neither userinfo nor a fresh ID token says which groups the person is in: removal
            # can't be seen, so fail closed (MTG_OIDC_GROUPS_CLAIM or the provider is misconfigured).
            logger.warning(
                "membership check found no %r claim in userinfo or a refreshed ID token; "
                "the member must sign in again (see docs/IDP-OTHERS.md)",
                claim,
            )
            return self._sign_in_again(sub, holder, "no_groups_claim")
        groups = resolve_groups(source, claim)
        if not self.settings.grants_access(groups):
            return self._revoke(sub, "not_in_group", groups)
        if groups != user.get("groups"):
            self.db.set_user_groups(sub, groups)
            self.db.audit("groups_changed", sub=sub, detail={"groups": groups[:50]})
        await self.avatars.update(sub, info, self.picture_fetcher(current))
        return Membership.ALLOWED

    # -- renewing in the background ------------------------------------------------
    def _plan_renewal(self, sub: str, holder: str) -> None:
        """Renew ``holder``'s provider access token in the background RENEW_LEAD seconds before a
        request would have to, when that falls within RENEW_WINDOW. Replaces a renewal planned
        before. The membership check is not changed by this: it still asks before the request."""
        grant = self.db.get_idp_grant(holder) or {}
        expires_at = int(grant.get("access_expires_at") or 0)
        if not expires_at or not grant.get("refresh_enc"):
            return
        delay = expires_at - EXPIRY_MARGIN - RENEW_LEAD - time.time()
        if delay <= 0 or delay > RENEW_WINDOW:
            return  # too close to gain anything (a request soon renews it as before), or too far
        self._start_renewal(sub, holder, delay, keep_alive=False)

    def _keep_alive(self, sub: str, active: str) -> None:
        """The member is active (``active`` was just let in): renew each of their other sign-ins'
        grants that was not renewed for KEEP_ALIVE, one at a time each, in the background."""
        cutoff = time.time() - KEEP_ALIVE
        for grant in self.db.idp_grants_for(sub):
            holder = grant["holder"]
            if holder == active or not grant.get("refresh_enc") or int(grant["updated_at"]) > cutoff:
                continue
            running = self._renewals.get(holder)
            if running is not None and not running.done():
                continue  # one is planned already
            self._start_renewal(sub, holder, 0.0, keep_alive=True)

    def _start_renewal(self, sub: str, holder: str, delay: float, *, keep_alive: bool) -> None:
        old = self._renewals.pop(holder, None)
        if old is not None and old is not asyncio.current_task():
            old.cancel()  # never asking the provider: that holds the lock this caller holds or waits on
        if len(self._renewals) >= MAX_CACHED:
            return  # never a task per member without bound; requests then renew as before
        task = asyncio.create_task(self._renew_later(sub, holder, delay, keep_alive=keep_alive))
        self._renewals[holder] = task
        task.add_done_callback(lambda t: self._renewals.get(holder) is t and self._renewals.pop(holder, None))

    async def _renew_later(self, sub: str, holder: str, delay: float, *, keep_alive: bool = False) -> None:
        await asyncio.sleep(delay)
        lock = self._locks.setdefault(holder, asyncio.Lock())
        async with lock:  # never beside a check, which may rotate the same refresh token
            user = self.db.get_user(sub)
            grant = self.db.get_idp_grant(holder) or {}
            if user is None or user.get("disabled_at") or grant.get("sub") != sub:
                return
            if keep_alive:
                if int(grant.get("updated_at") or 0) > time.time() - KEEP_ALIVE:
                    return  # renewed meanwhile
            else:
                expires_at = int(grant.get("access_expires_at") or 0)
                if not expires_at or expires_at - EXPIRY_MARGIN - time.time() > RENEW_LEAD + 1:
                    return  # a check renewed it meanwhile
            old = grant.get("refresh_enc") or ""
            refresh = self._open(old)
            if not refresh:
                return
            # From here a refresh may rotate the token at the provider: shutdown lets it finish
            # (aclose) so the new one is kept.
            self._refreshing.add(holder)
            try:
                tokens = await self.oidc.refresh(refresh)
            except IdPUnavailable as exc:
                logger.info("renewing the provider token in the background failed: %s", exc)
                return
            finally:
                self._refreshing.discard(holder)
            # A refused refresh decides nothing here: the next request's own check of this sign-in
            # asks the provider and acts on its answer. Written only over the grant renewed: one
            # revoked or deleted meanwhile (an admin, a sign-out, Delete my data) stays gone.
            if tokens is not None:
                self.db.replace_idp_grant(
                    holder,
                    old_refresh_enc=old,
                    refresh_enc=self._seal(tokens.refresh_token),
                    access_enc=self._seal(tokens.access_token),
                    access_expires_at=int(tokens.access_expires_at or 0),
                )

    async def aclose(self, grace: float = 5.0) -> None:
        """At shutdown: renewals still waiting are cancelled; one already asking the provider gets
        ``grace`` seconds to finish, so a refresh token the provider just rotated is not lost."""
        for check in list(self._background.values()):
            check.cancel()
        await asyncio.gather(*self._background.values(), return_exceptions=True)
        self._background.clear()
        tasks = dict(self._renewals)
        self._renewals.clear()
        busy = [t for holder, t in tasks.items() if holder in self._refreshing]
        for t in tasks.values():
            if t not in busy:
                t.cancel()
        if busy:
            await asyncio.wait(busy, timeout=grace)
        for t in tasks.values():
            t.cancel()
        await asyncio.gather(*tasks.values(), return_exceptions=True)

    def picture_fetcher(self, access: str | None) -> Callable[[], Awaitable[str | None]] | None:
        """Fetch the member's uploaded picture with the provider token that just worked."""
        if not access:
            return None
        return lambda: self.oidc.uploaded_picture(access)

    async def _renew(self, holder: str, sub: str, refresh: str) -> IdPTokens | None:
        """Use the provider refresh token and keep what it returns (it may rotate)."""
        tokens = await self.oidc.refresh(refresh)
        if tokens is not None:
            self.db.save_idp_grant(
                holder,
                sub,
                refresh_enc=self._seal(tokens.refresh_token),
                access_enc=self._seal(tokens.access_token),
                access_expires_at=int(tokens.access_expires_at or 0),
            )
        return tokens

    def _sign_in_again(self, sub: str, holder: str, reason: str) -> Membership:
        """The provider no longer honours this sign-in (its tokens were refused, or there are
        none): its grant goes, and a browser session holding it ends, its browser told why. The
        member's other sign-ins, their AI apps' tokens and their Archidekt link are kept: this
        can be no more than a provider refresh token that ran out, which the provider answers just
        as it answers for a deactivated account. A kept token is refused while none of the member's
        sign-ins is honoured (_check_member), so it gives nobody holding it anything until the
        member signs in at the provider again."""
        self.db.delete_idp_grant(holder)
        website = holder.startswith(SESSION_HOLDER)
        ended = website and self.db.end_browser_session(holder[len(SESSION_HOLDER) :], "idp")
        self._checked.pop(holder, None)
        self._owners.pop(holder, None)
        self.db.audit(
            "membership_unverifiable",
            sub=sub,
            detail={
                "reason": reason,
                "sign_in": "website" if website else "assistant",
                "signed_out": int(ended),
            },
        )
        logger.warning(
            "membership check: the identity provider no longer honours one of a member's %s sign-ins "
            "(%s); that sign-in ends and they sign in again. Their other sign-ins and AI apps are kept",
            "website" if website else "AI-app",
            reason,
        )
        return Membership.SIGN_IN

    def _revoke(self, sub: str, reason: str, groups: list[str] | None) -> Membership:
        """The provider said the person is out (not in the group, or someone else): every token
        and browser session of ``sub`` is revoked, and their Archidekt link as well."""
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
