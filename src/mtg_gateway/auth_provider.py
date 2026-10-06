"""The gateway's own OAuth 2.1 authorization server.

MCP clients (Claude, ChatGPT) register here, send users to /authorize, and
exchange codes at /token. /authorize does not show a login form of its own: it
forwards the user to the identity provider, and /auth/callback finishes the
flow by minting a gateway authorization code. Only gateway-minted tokens are
accepted on the MCP endpoint.
"""

from __future__ import annotations

import base64
import binascii
import contextvars
import hashlib
import hmac
import ipaddress
import logging
import secrets
import time
from typing import Any
from urllib.parse import unquote, urlparse

from mcp.server.auth.middleware.client_auth import AuthenticationError, ClientAuthenticator
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    OAuthAuthorizationServerProvider,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request

from .cimd import (
    CimdError,
    CimdFetcher,
    CimdThrottled,
    check_redirect_uri,
    is_cimd_client_id,
    sanitise_client_name,
)
from .config import Settings
from .db import Database, hash_token
from .membership import Membership, MembershipChecker
from .oidc import Identity, IdPTokens, OIDCClient, OIDCError, pkce_pair

logger = logging.getLogger(__name__)

BROWSER_COOKIE = "mtg_login"
# The browser key of the request being handled, set by LoginCookieMiddleware on /authorize and /login:
# {"key": <random per-browser secret>, "used": bool}. A login session created in that request stores
# a hash of the key, and the consent page and the callback only accept the browser holding it.
BROWSER_KEY: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "mtg_browser_key", default=None
)
CONSENT_PATH = "/authorize/confirm"
SECRET_HASH_KEY = "client_secret_hash"  # stored in place of the plaintext client_secret


def _origin(url: str) -> str:
    """scheme://host[:port] of ``url``, the form CSP sources use."""
    p = urlparse(url)
    port = f":{p.port}" if p.port else ""
    host = f"[{p.hostname}]" if p.hostname and ":" in p.hostname else (p.hostname or "")
    return f"{p.scheme}://{host}{port}"


DISABLED_MESSAGE = "Your account has been disabled on this gateway."


def cookie_name(base: str, settings: Settings) -> str:
    """``__Host-<base>`` on https: the browser then refuses the cookie unless this host set it
    itself (Secure, Path=/, no Domain), so a sibling subdomain cannot plant one."""
    return f"__Host-{base}" if settings.public_url.startswith("https://") else base


def _eq(a: str, b: str) -> bool:
    """Constant-time string comparison that is False (not a TypeError) for non-ASCII input."""
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


class LoginError(Exception):
    """A login failure to show the user in the browser (no sensitive detail)."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# Longest OAuth ``state`` accepted on /authorize (it is stored with the pending login).
MAX_STATE_LEN = 512

# Sign-in starts (GET /login, GET /authorize) one network may make per minute. A person needs a
# few; the limit keeps one address from churning the pending-login table (db.py caps it and evicts
# the busiest network's rows first). Only public (globally routable) addresses are limited. A
# private, loopback, link-local, CGNAT (100.64/10) or ULA address, or one in MTG_TRUSTED_PROXIES,
# is usually the reverse proxy itself (not trusted, or sending no X-Forwarded-For): every visitor
# then looks the same, and a limit would let anyone lock everyone out. The per-browser and
# per-client caps on pending logins still apply to them.
LOGIN_STARTS_PER_MINUTE = 30
# This many different browsers starting sign-ins from one address logs a warning (once per
# address) that the address may be an untrusted proxy.
SHARED_SOURCE_WARN_BROWSERS = 50
TOO_MANY_LOGINS = "Too many sign-in attempts from your network. Wait a minute and try again."


class LoginThrottled(Exception):
    """This network started too many sign-ins in the last minute."""


def login_source(ip: str | None) -> str | None:
    """The network a request came from, as pending logins are grouped and rate-limited: the
    IPv4 address, or the /64 of an IPv6 address (one subscriber usually holds a whole /64)."""
    try:
        addr = ipaddress.ip_address(ip or "")
    except ValueError:
        return None
    if isinstance(addr, ipaddress.IPv6Address):
        if addr.ipv4_mapped is not None:
            return str(addr.ipv4_mapped)
        return str(ipaddress.ip_network(f"{addr}/64", strict=False))
    return str(addr)


class LoginStartLimiter:
    """In-memory token bucket per network: ``per_minute`` sign-in starts, refilled evenly."""

    MAX_TRACKED = 10_000

    def __init__(self, per_minute: int = LOGIN_STARTS_PER_MINUTE):
        self.per_minute = per_minute
        self._buckets: dict[str, tuple[float, float]] = {}  # source -> (tokens, at)

    def allow(self, source: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        rate = self.per_minute / 60.0
        tokens, at = self._buckets.get(source, (float(self.per_minute), now))
        tokens = min(float(self.per_minute), tokens + (now - at) * rate)
        if tokens < 1:
            self._buckets[source] = (tokens, now)
            return False
        self._buckets[source] = (tokens - 1, now)
        if len(self._buckets) > self.MAX_TRACKED:  # bounded even under abuse: drop refilled buckets
            full = 60.0  # a bucket untouched for a minute is full again
            self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < full}
            if len(self._buckets) > self.MAX_TRACKED:
                self._buckets.clear()
        return True


class GatewayAuthProvider(OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]):
    def __init__(
        self,
        settings: Settings,
        db: Database,
        oidc: OIDCClient,
        *,
        cimd: CimdFetcher | None = None,
        membership: MembershipChecker | None = None,
    ):
        self.settings = settings
        self.db = db
        self.oidc = oidc
        self.membership = membership if membership is not None else MembershipChecker(settings, db, oidc)
        self.cimd = cimd if cimd is not None else CimdFetcher(allowed_hosts=settings.cimd_allowed_hosts)
        self.login_limiter = LoginStartLimiter()
        self._source_browsers: dict[str, set[str]] = {}  # source -> browser keys seen (bounded)
        self._warned_sources: set[str] = set()
        self._trusted_nets: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
        for entry in settings.trusted_proxies:
            try:
                self._trusted_nets.append(ipaddress.ip_network(entry, strict=False))
            except ValueError:
                pass  # "*" (trust everyone): no address is then a proxy of its own

    # -- clients ------------------------------------------------------------
    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        info = self.db.get_client(client_id)
        if info:
            info = {k: v for k, v in info.items() if k not in (SECRET_HASH_KEY, "client_secret")}
            return OAuthClientInformationFull.model_validate(info)
        if self.settings.cimd_enabled and is_cimd_client_id(client_id):
            info = await self._cimd_client(client_id)
            if info:
                return OAuthClientInformationFull.model_validate(info)
        return None

    async def _cimd_client(self, url: str) -> dict[str, Any] | None:
        """A client described by a Client ID Metadata Document, from cache or fetched now."""
        cached = self.db.get_cimd_client(url)
        if cached:
            return cached
        try:
            # A URL accepted before skips the fetcher's per-host block and rate limits (cimd.py).
            info, ttl = await self.cimd.fetch(url, known=self.db.cimd_client_known(url))
        except CimdThrottled:
            return None
        except CimdError as exc:
            logger.info("rejected client id metadata document %s: %s", url[:120], exc)
            self.db.audit("cimd_rejected", client_id=url[:200], detail={"reason": str(exc)[:200]})
            return None
        self.db.save_cimd_client(url, info, ttl)
        self.db.audit("cimd_accepted", client_id=url[:200], detail={"client_name": info["client_name"]})
        return info

    @staticmethod
    def is_cimd_client(client: OAuthClientInformationFull) -> bool:
        return is_cimd_client_id(client.client_id)

    def client_secret_hash(self, client_id: str) -> str | None:
        info = self.db.get_client(client_id)
        if not info:
            return None
        if info.get("client_secret"):  # row written before secrets were hashed
            return hash_token(info["client_secret"])
        return info.get(SECRET_HASH_KEY)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        # Registration is open and anonymous, so what one client may store is kept small.
        max_uris, max_uri_len, max_bytes = 10, 2000, 8 * 1024  # a real registration is under 1 KB
        uris = client_info.redirect_uris or []
        if len(uris) > max_uris:
            raise RegistrationError("invalid_redirect_uri", f"at most {max_uris} redirect URIs")
        for uri in uris:
            if len(str(uri)) > max_uri_len:
                raise RegistrationError(
                    "invalid_redirect_uri", f"redirect URI longer than {max_uri_len} characters"
                )
            problem = check_redirect_uri(str(uri))
            if problem:
                raise RegistrationError("invalid_redirect_uri", problem)
        # Free-text and URL metadata nothing here uses is dropped (RFC 7591 lets the server
        # ignore it); clearing it on the model keeps the 201 echo equal to what was registered.
        for field in (
            "client_uri",
            "logo_uri",
            "contacts",
            "tos_uri",
            "policy_uri",
            "jwks_uri",
            "jwks",
            "software_id",
            "software_version",
        ):
            setattr(client_info, field, None)
        if len(client_info.model_dump_json(exclude_none=True)) > max_bytes:
            raise RegistrationError("invalid_client_metadata", "client metadata is too large")
        # The plaintext secret goes back to the client once; only its hash is stored
        # (the same treatment tokens get), so a copy of the database yields no usable secret.
        info = client_info.model_dump(mode="json", exclude_none=True)
        if isinstance(info.get("client_name"), str):
            # Shown on the consent page: printable characters only, and short enough that it
            # cannot pass itself off as the page's own wording.
            info["client_name"] = sanitise_client_name(info["client_name"])
        secret = info.pop("client_secret", None)
        if secret:
            info[SECRET_HASH_KEY] = hash_token(secret)
        self.db.save_client(client_info.client_id, info)
        # Anonymous callers can register in a loop, so the audit row holds only bounded fields
        # (the sanitised name, how many redirect URIs and at most three of their hosts), and
        # db.audit's per-event row cap keeps the number of such rows bounded too.
        hosts: list[str] = []
        for uri in uris:
            host = (urlparse(str(uri)).hostname or "")[:100]
            if host and host not in hosts and len(hosts) < 3:
                hosts.append(host)
        self.db.audit(
            "client_registered",
            client_id=client_info.client_id,
            detail={
                "client_name": info.get("client_name"),
                "redirect_uri_count": len(uris),
                "redirect_hosts": hosts,
            },
        )

    # -- authorize: hand the browser to the identity provider ---------------
    @staticmethod
    def binding_hash(browser_key: str) -> str:
        return hash_token(f"login-binding:{browser_key}")

    def _new_login_binding(self) -> str | None:
        """Hash of the browser key of the request creating a login (None outside such a request,
        which leaves the login unusable). Marks the key used so the middleware sets its cookie."""
        holder = BROWSER_KEY.get()
        if holder is None:
            return None
        holder["used"] = True
        return self.binding_hash(holder["key"])

    def _admit_login(self) -> str | None:
        """Hash of the network of the request creating a login (None outside such a request), after
        charging it one sign-in start. Raises LoginThrottled when that network is over its limit."""
        holder = BROWSER_KEY.get()
        ip = holder.get("client_ip") if holder else None
        source = login_source(ip)
        if source is None:
            return None
        addr = ipaddress.ip_address(ip or "")  # valid: login_source parsed it
        if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
            addr = addr.ipv4_mapped
        trusted = any(addr in net for net in self._trusted_nets)
        if holder is not None and not trusted:
            self._note_shared_source(str(addr), str(holder.get("key", "")))
        if addr.is_global and not trusted and not self.login_limiter.allow(source):
            raise LoginThrottled(source)
        return hash_token(f"login-source:{source}")

    def _note_shared_source(self, ip: str, browser_key: str) -> None:
        """Warn once when many different browsers start sign-ins from one address that is not a
        trusted proxy: it is probably the reverse proxy, and without MTG_TRUSTED_PROXIES every
        visitor shares its address (one rate limit, one group for the pending-login caps)."""
        if ip in self._warned_sources:
            return
        seen = self._source_browsers.setdefault(ip, set())
        seen.add(hash_token(browser_key)[:16])
        if len(seen) >= SHARED_SOURCE_WARN_BROWSERS:
            logger.warning(
                "%d different browsers started sign-ins from %s. If that is your reverse proxy, add "
                "its address to MTG_TRUSTED_PROXIES so the gateway sees each visitor's own address.",
                len(seen),
                ip,
            )
            self._warned_sources.add(ip)
            del self._source_browsers[ip]
            if len(self._warned_sources) > 1000:
                self._warned_sources.clear()
        elif len(self._source_browsers) > 1000:  # bounded even under abuse
            self._source_browsers.clear()

    def same_browser(self, session: dict[str, Any], browser_key: str | None) -> bool:
        """True when ``browser_key`` (the login cookie) is the one of the browser that started
        ``session``. The key is a random secret of that browser, never derived from the state, so
        no other browser can be given it (login CSRF and consent-bypass defence)."""
        expected = session.get("binding_hash")
        if not expected or not browser_key:
            return False
        return _eq(self.binding_hash(browser_key), expected)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        # /authorize is anonymous and every call stores a login session, so the free-form
        # parameters it stores are bounded: state to MAX_STATE_LEN, and the PKCE challenge to
        # the 43-128 characters RFC 7636 allows.
        if params.state is not None and len(params.state) > MAX_STATE_LEN:
            raise AuthorizeError("invalid_request", f"state longer than {MAX_STATE_LEN} characters")
        if not 43 <= len(params.code_challenge or "") <= 128:
            raise AuthorizeError("invalid_request", "code_challenge must be 43-128 characters (RFC 7636)")
        if params.resource and params.resource.rstrip("/") != self.settings.mcp_url:
            raise AuthorizeError(
                "invalid_target", "unknown resource; this server only issues tokens for its own MCP endpoint"
            )
        if params.resource:
            params.resource = self.settings.mcp_url  # stored normalised, as the bearer check expects
        if not params.scopes:
            # A client that sends no scope gets the scope it registered with (the
            # registration default is "mtg"), not an empty token.
            params.scopes = (client.scope or "mtg").split()
        try:
            source_hash = self._admit_login()
        except LoginThrottled as exc:
            raise AuthorizeError("temporarily_unavailable", TOO_MANY_LOGINS) from exc
        login_id = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        verifier, _challenge = pkce_pair()  # the challenge is derived again on Approve
        self.db.create_login_session(
            login_id,
            client_id=client.client_id,
            params=params.model_dump(mode="json"),
            oidc_nonce=nonce,
            oidc_code_verifier=verifier,
            ttl=self.settings.login_ttl,
            binding_hash=self._new_login_binding(),
            source_hash=source_hash,
        )
        # Every MCP client goes through the gateway's own consent page before the IdP. The gateway
        # is one OIDC client of the IdP, so the IdP's consent (or its silent "implicit consent")
        # cannot tell which MCP client asked: without this page anyone could register a client
        # with their own redirect URI, send a signed-in member the /authorize link and receive a
        # code for that member's account (the MCP spec's "confused deputy"). The page names the
        # client and the host the code goes to, and continues to the IdP only on Approve.
        return f"{self.settings.public_url}{CONSENT_PATH}?state={login_id}"

    async def _idp_url(self, login_id: str, nonce: str, challenge: str) -> str:
        try:
            return await self.oidc.authorization_url(state=login_id, nonce=nonce, code_challenge=challenge)
        except OIDCError as exc:
            logger.error("identity provider unreachable during authorize: %s", exc)
            raise AuthorizeError("temporarily_unavailable", "identity provider unavailable") from exc

    def consent_csrf(self, login_id: str, browser_key: str | None) -> str:
        """Token bound to one pending MCP login and the browser that started it, carried by the
        consent form's buttons."""
        return hmac.new(
            self.settings.session_secret.encode(),
            f"consent:{login_id}:{self.binding_hash(browser_key or '')}".encode(),
            hashlib.sha256,
        ).hexdigest()

    def check_consent_csrf(self, login_id: str, token: str | None, browser_key: str | None) -> bool:
        return bool(login_id and token and browser_key) and _eq(
            self.consent_csrf(login_id, browser_key), token or ""
        )

    async def _consent_session(
        self, login_id: str, browser_key: str | None, *, pop: bool = False
    ) -> dict[str, Any] | None:
        """The pending MCP login behind a consent page, or None. Browser-page logins, logins whose
        client is gone and logins started in another browser never have one: an attacker who
        starts a login cannot have someone else's browser approve or finish it."""
        session = self.db.get_login_session(login_id)
        if session is None or not self.same_browser(session, browser_key):
            return None
        if await self.get_client(session["client_id"]) is None:
            return None
        if pop:
            return self.db.pop_login_session(login_id)
        return session

    async def deny_login(self, login_id: str, browser_key: str | None) -> str:
        """The user declined on the consent page: invalidate the pending login and send the
        browser back to the client with access_denied, as for a cancellation at the IdP."""
        session = await self._consent_session(login_id, browser_key, pop=True)
        if session is None:
            raise LoginError(
                "This sign-in link has expired or was already used. Start again from your AI client."
            )
        params = AuthorizationParams.model_validate(session["params"])
        self.db.audit(
            "login_denied", client_id=session["client_id"][:200], detail={"by": "user", "where": "consent"}
        )
        return construct_redirect_uri(
            str(params.redirect_uri),
            error="access_denied",
            error_description="the user declined on the gateway's confirmation page",
            state=params.state,
        )

    async def idp_origin(self) -> str:
        """Origin of the identity provider's sign-in page: where /login and the consent page's
        Approve send the browser. The issuer's origin when the provider's metadata can't be read."""
        try:
            return _origin(str((await self.oidc.metadata())["authorization_endpoint"]))
        except OIDCError:
            return _origin(self.settings.oidc_issuer)  # Approve will report the IdP outage itself

    async def consent_details(self, login_id: str, browser_key: str | None) -> dict[str, Any] | None:
        """What the consent page shows for a pending MCP login, or None if there is none."""
        session = await self._consent_session(login_id, browser_key)
        if session is None:
            return None
        client = await self.get_client(session["client_id"])
        if client is None:
            return None
        redirect = str(AuthorizationParams.model_validate(session["params"]).redirect_uri)
        host = urlparse(redirect).hostname or ""
        # Chromium applies the page's form-action CSP to the redirect that follows the POST, so
        # the consent page must allow exactly where Approve (the IdP) and Deny (the client) go.
        idp_origin = await self.idp_origin()
        return {
            # Sanitised again here so a name stored before the rule existed (a cached
            # metadata document, an old registration) is shown under the same rule.
            "client_name": sanitise_client_name(client.client_name or "").strip() or "an application",
            # A URL client id names the host that published the client's description; a
            # self-registered client has no such host, only the name it chose for itself.
            "client_host": urlparse(session["client_id"]).hostname or ""
            if self.is_cimd_client(client)
            else "",
            "redirect_host": host,
            "redirect_uri": redirect,
            "loopback": host in ("localhost", "127.0.0.1", "::1"),
            "form_action": tuple(dict.fromkeys((idp_origin, _origin(redirect)))),
        }

    async def continue_login(self, login_id: str, browser_key: str | None) -> str:
        """After the user confirms, send the browser to the identity provider."""
        session = await self._consent_session(login_id, browser_key)
        if session is None:
            raise LoginError(
                "This sign-in link has expired or was already used. Start again from your AI client."
            )
        verifier = session["oidc_code_verifier"]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode().rstrip("=")
        )
        try:
            return await self._idp_url(login_id, session["oidc_nonce"], challenge)
        except AuthorizeError as exc:
            raise LoginError("The identity provider is unavailable. Try again in a minute.", 503) from exc

    async def finish_idp_leg(
        self, *, state: str | None, code: str | None, error: str | None, cookie_value: str | None
    ) -> tuple[dict[str, Any], Identity | None]:
        """Validate the identity provider's redirect and return the login session
        plus the verified identity (None when the user cancelled at the provider)."""
        if not state:
            raise LoginError("Missing state parameter.")
        expired = "This sign-in link has expired or was already used. Start again from your AI client."
        pending = self.db.get_login_session(state)
        if pending is None:
            raise LoginError(expired)
        # Checked before the session is used up, so another browser cannot burn someone's login.
        if not self.same_browser(pending, cookie_value):
            raise LoginError(
                "This sign-in was started in a different browser. Start again from your AI client.", 403
            )
        session = self.db.pop_login_session(state)
        if session is None:
            raise LoginError(expired)
        if error:
            self.db.audit("login_denied", client_id=session["client_id"], detail={"error": error})
            return session, None
        if not code:
            raise LoginError("Missing code parameter.")
        try:
            identity = await self.oidc.exchange_code(
                code, session["oidc_code_verifier"], session["oidc_nonce"]
            )
        except OIDCError as exc:
            logger.warning("identity provider exchange failed: %s", exc)
            raise LoginError(
                "Sign-in could not be completed with the identity provider. Try again.", 502
            ) from exc

        known = self.db.get_user(identity.sub)
        pinned = (known or {}).get("idp_issuer")
        if pinned and pinned.rstrip("/") != self.oidc.issuer.rstrip("/"):
            if pinned.rstrip("/") in self.settings.oidc_previous_issuers:
                # The same provider under its old address (MTG_OIDC_PREVIOUS_ISSUERS): re-pin.
                self.db.set_user_issuer(identity.sub, self.oidc.issuer)
                self.db.audit("issuer_repinned", sub=identity.sub, client_id=session["client_id"])
            else:
                # The same subject string from a different identity provider is not the same
                # person: never hand them the earlier member's decks, apps or Archidekt link.
                self.db.audit("login_rejected_issuer", sub=identity.sub, client_id=session["client_id"])
                raise LoginError(
                    "This account belongs to a different sign-in provider than the one this gateway "
                    "uses now. Ask the gateway's admin to delete the old account's data, then sign in "
                    "again.",
                    403,
                )

        if self.settings.required_group and self.settings.required_group not in identity.groups:
            self.db.audit("login_rejected_group", sub=identity.sub, client_id=session["client_id"])
            self.db.drop_member(identity.sub, identity.groups)  # their apps and browser sessions too
            raise LoginError("Your account is not in the group that may use this service.", 403)

        if known and known.get("disabled_at"):
            self.db.audit("login_rejected_disabled", sub=identity.sub, client_id=session["client_id"])
            self.db.delete_browser_sessions_for(identity.sub)
            raise LoginError(DISABLED_MESSAGE, 403)

        self._record_user(identity)
        # The provider's own tokens, so later requests can ask it again (membership.py).
        self.membership.store(
            identity.sub,
            IdPTokens(
                access_token=identity.idp_access_token,
                access_expires_at=identity.idp_access_expires_at,
                refresh_token=identity.idp_refresh_token,
            ),
        )
        return session, identity

    async def complete_login(
        self, *, state: str | None, code: str | None, error: str | None, cookie_value: str | None
    ) -> str:
        """Finish the identity-provider leg and return the MCP client's redirect URL."""
        session, identity = await self.finish_idp_leg(
            state=state, code=code, error=error, cookie_value=cookie_value
        )
        params = AuthorizationParams.model_validate(session["params"])
        client_redirect = str(params.redirect_uri)
        if identity is None:
            return construct_redirect_uri(
                client_redirect,
                error="access_denied",
                error_description="sign-in was cancelled or denied",
                state=params.state,
            )
        gw_code = secrets.token_urlsafe(32)
        expires_at = int(time.time()) + self.settings.auth_code_ttl
        auth_code = AuthorizationCode(
            code=gw_code,
            scopes=params.scopes or [],
            expires_at=expires_at,
            client_id=session["client_id"],
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource or self.settings.mcp_url,
            subject=identity.sub,
        )
        self.db.save_auth_code(
            gw_code,
            client_id=session["client_id"],
            data=auth_code.model_dump(mode="json", exclude={"code"}),
            expires_at=expires_at,
        )
        self.db.audit("login_ok", sub=identity.sub, client_id=session["client_id"])
        if is_cimd_client_id(session["client_id"]):
            # A member signed in through it: from now on its cached document is never evicted
            # and refetching it is never throttled (cimd.py, db.py).
            self.db.mark_cimd_client_signed_in(session["client_id"])
        return construct_redirect_uri(client_redirect, code=gw_code, state=params.state)

    async def start_idp_login(
        self, client_id: str, params: dict[str, Any], *, force_login: bool = False
    ) -> str:
        """Create a login session for a non-MCP caller (the browser pages) and return the IdP URL.
        ``force_login`` asks the provider to make the person enter their credentials again
        (``prompt=login``), used right after they signed out on this device."""
        try:
            source_hash = self._admit_login()
        except LoginThrottled as exc:
            raise LoginError(TOO_MANY_LOGINS, 429) from exc
        login_id = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(24)
        verifier, challenge = pkce_pair()
        self.db.create_login_session(
            login_id,
            client_id=client_id,
            params=params,
            oidc_nonce=nonce,
            oidc_code_verifier=verifier,
            ttl=self.settings.login_ttl,
            binding_hash=self._new_login_binding(),
            source_hash=source_hash,
        )
        try:
            return await self.oidc.authorization_url(
                state=login_id, nonce=nonce, code_challenge=challenge, prompt="login" if force_login else None
            )
        except OIDCError as exc:
            raise LoginError(
                "The identity provider is unavailable right now. Try again shortly.", 502
            ) from exc

    async def _require_member(self, sub: str) -> None:
        """Refuse the token request unless the identity provider says ``sub`` is still in."""
        user = self.db.get_user(sub)
        if user is None:
            # Deleted their data (or never signed in): a code or refresh token left over from
            # before must not mint new tokens.
            raise TokenError("invalid_grant", "sign in again to keep using this service")
        if user.get("disabled_at"):
            raise TokenError("invalid_grant", "this account has been disabled on this gateway")
        outcome = await self.membership.check(sub)
        if outcome is Membership.UNAVAILABLE:
            raise TokenError("invalid_request", "the identity provider can't be reached; try again shortly")
        if outcome is Membership.REVOKED:
            raise TokenError("invalid_grant", "sign in again to keep using this service")

    def _recheck_membership(self, sub: str, client_id: str, family: str, auth_time: int) -> None:
        """A refresh is the one moment a long-lived session passes through the gateway, so it is
        where access is re-checked without the identity provider. Refused, and the whole token
        family revoked, when the user is gone, when the required group is no longer among the
        groups recorded at their last sign-in (so a changed MTG_REQUIRED_GROUP bites within an
        access-token lifetime), or when this app's own sign-in (``auth_time``, not the person's
        latest sign-in anywhere) is older than MTG_REAUTH_INTERVAL. The
        client then re-runs the authorization flow, where Authentik's current group membership
        decides; a removed member is cut off by then at the latest."""
        user = self.db.get_user(sub)
        reason = None
        if user is None:
            reason = "unknown_user"
        elif user.get("disabled_at"):
            reason = "disabled"
        elif self.settings.required_group and self.settings.required_group not in user["groups"]:
            reason = "group"
        elif auth_time + self.settings.reauth_interval < int(time.time()):
            reason = "reauth_due"
        if reason is None:
            self.db.touch_user(sub)
            return
        self.db.revoke_family(family)
        self.db.audit("refresh_rejected", sub=sub, client_id=client_id, detail={"reason": reason})
        if reason == "disabled":
            raise TokenError("invalid_grant", "this account has been disabled on this gateway")
        raise TokenError("invalid_grant", "sign in again to keep using this service")

    def _record_user(self, identity: Identity) -> None:
        self.db.upsert_user(
            identity.sub,
            email=identity.email,
            name=identity.name,
            preferred_username=identity.preferred_username,
            groups=identity.groups,
            issuer=self.oidc.issuer,
        )

    # -- codes and tokens ---------------------------------------------------
    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        data = self.db.get_auth_code(authorization_code, client.client_id)
        if data is None:
            return None
        if "used_family" in data:
            # A redeemed code presented again: it leaked, so the tokens it gave are revoked too
            # (OAuth 2.1 section 4.1.3).
            self.db.revoke_family(data["used_family"])
            self.db.audit("code_reuse_detected", client_id=client.client_id)
            return None
        return AuthorizationCode.model_validate({**data, "code": authorization_code})

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        family = secrets.token_urlsafe(16)
        if not authorization_code.subject:
            raise TokenError("invalid_grant", "authorization code has no subject")
        # Asked before the code is used up, so a brief identity-provider outage doesn't burn it
        # (a retry would otherwise look like a replayed code).
        await self._require_member(authorization_code.subject)
        # Single use: marking it used is the check, so a replayed code fails here.
        if not self.db.use_auth_code(authorization_code.code, family):
            raise TokenError("invalid_grant", "authorization code already used")
        return self._issue(
            client.client_id,
            authorization_code.subject,
            authorization_code.scopes,
            authorization_code.resource,
            family,
            auth_time=int(time.time()),
        )

    async def load_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: str
    ) -> RefreshToken | None:
        row = self.db.get_token(refresh_token, "refresh", include_revoked=True)
        if row is None or row["client_id"] != client.client_id:
            return None
        if row["revoked"]:
            # OAuth 2.1 reuse detection: a rotated-out refresh token presented again means
            # it leaked (or the client is broken). Kill the whole lineage, successors included.
            self.db.revoke_family(row["family"])
            self.db.audit("refresh_reuse_detected", sub=row["sub"], client_id=row["client_id"])
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["sub"],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        if refresh_token.subject:
            # Ask the identity provider first: a removed member's whole family is revoked here,
            # and when the provider can't be reached this refresh token stays usable for a retry.
            await self._require_member(refresh_token.subject)
        row = self.db.get_token(refresh_token.token, "refresh")
        if row is None:
            raise TokenError("invalid_grant", "refresh token is not valid")
        # Rotate: retire the current generation (this refresh token and the access token
        # issued with it) and issue the next one in the same family, so a replay of any
        # retired token can still be traced to, and revoke, its successors.
        self.db.revoke_generation(row["family"], row["created_at"])
        if not refresh_token.subject:
            raise TokenError("invalid_grant", "refresh token has no subject")
        user = self.db.get_user(refresh_token.subject or "")
        # Families issued before per-app sign-in times keep the old rule (the person's last sign-in).
        auth_time = int(row["auth_time"] or (user or {}).get("last_login_at") or row["created_at"])
        self._recheck_membership(refresh_token.subject, client.client_id, row["family"], auth_time)
        if is_cimd_client_id(client.client_id):
            # Still in use by a member: keep it known (and kept) past the 180-day purge window.
            self.db.mark_cimd_client_signed_in(client.client_id)
        return self._issue(
            client.client_id,
            refresh_token.subject,
            scopes,
            refresh_token.resource,
            row["family"],
            auth_time=auth_time,
        )

    def _issue(
        self,
        client_id: str,
        sub: str,
        scopes: list[str],
        resource: str | None,
        family: str,
        *,
        auth_time: int,
    ) -> OAuthToken:
        now = int(time.time())
        access = secrets.token_urlsafe(32)
        refresh = secrets.token_urlsafe(32)
        resource = resource or self.settings.mcp_url
        self.db.save_token(
            access,
            kind="access",
            client_id=client_id,
            sub=sub,
            scopes=scopes,
            resource=resource,
            family=family,
            expires_at=now + self.settings.access_token_ttl,
            auth_time=auth_time,
        )
        self.db.save_token(
            refresh,
            kind="refresh",
            client_id=client_id,
            sub=sub,
            scopes=scopes,
            resource=resource,
            family=family,
            expires_at=now + self.settings.refresh_token_ttl,
            auth_time=auth_time,
        )
        self.db.audit("tokens_issued", sub=sub, client_id=client_id)
        return OAuthToken(
            access_token=access,
            token_type="Bearer",
            expires_in=self.settings.access_token_ttl,
            scope=" ".join(scopes) if scopes else None,
            refresh_token=refresh,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = self.db.get_token(token, "access")
        if row is None or row["expires_at"] < int(time.time()):
            return None
        user = self.db.get_user(row["sub"])
        if user is None:
            # Every token is issued to a signed-in person; one whose user row is gone is refused,
            # including when no group is required.
            return None
        if user.get("disabled_at"):
            # Revoking the family makes the refusal (and its audit row) happen once per token: the
            # next presentation of this token, or of its refresh token, fails as "revoked" above.
            self.db.revoke_family(row["family"])
            self.db.audit("disabled_user_refused", sub=row["sub"], client_id=row["client_id"])
            return None
        group = self.settings.required_group
        if group and group not in (user.get("groups") or []):
            # Checked on every request, not only at refresh. The recorded groups are the identity
            # provider's live answer from MembershipMiddleware (at most MTG_MEMBERSHIP_CHECK_TTL
            # seconds old), so a member taken out of the group loses /mcp on their next request.
            self.db.revoke_family(row["family"])
            self.db.audit("not_in_group_refused", sub=row["sub"], client_id=row["client_id"])
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=row["scopes"],
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["sub"],
            claims={"iss": self.settings.public_url},
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        kind = "refresh" if isinstance(token, RefreshToken) else "access"
        row = self.db.get_token(token.token, kind)
        if row:
            # Only this grant's token family ends. The member's browser sessions are left alone:
            # an app revoking its own token (a disconnect, or a hostile app) must not sign the
            # member out of the web pages and the Android app. Browser sessions are wiped on the
            # admin disable/revoke paths and when the member loses the required group.
            self.db.revoke_family(row["family"])
            self.db.audit("tokens_revoked", sub=row["sub"], client_id=row["client_id"])


class HashedSecretAuthenticator(ClientAuthenticator):
    """Authenticate /token and /revoke callers against the stored secret hash.

    The SDK's authenticator compares the presented secret with ``client.client_secret``;
    this gateway never stores that plaintext, so the comparison is done here against
    the SHA-256 the provider kept at registration.
    """

    def __init__(self, provider: GatewayAuthProvider):
        super().__init__(provider)
        self.gateway_provider = provider

    async def authenticate_request(self, request: Request) -> OAuthClientInformationFull:
        form_data = await request.form()
        client_id = form_data.get("client_id")
        if not client_id or not isinstance(client_id, str):
            raise AuthenticationError("Missing client_id")
        client = await self.provider.get_client(client_id)
        if not client:
            raise AuthenticationError("Invalid client_id")

        presented: str | None = None
        method = client.token_endpoint_auth_method
        if method == "client_secret_basic":
            header = request.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                raise AuthenticationError("Missing or invalid Basic authentication in Authorization header")
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                basic_id, presented = decoded.split(":", 1)
            except (ValueError, UnicodeDecodeError, binascii.Error) as exc:
                raise AuthenticationError("Invalid Basic authentication header") from exc
            if unquote(basic_id) != client_id:
                raise AuthenticationError("Client ID mismatch in Basic auth")
            presented = unquote(presented)
        elif method == "client_secret_post":
            raw = form_data.get("client_secret")
            presented = raw if isinstance(raw, str) else None
        elif method == "none":
            presented = None
        else:
            raise AuthenticationError(f"Unsupported auth method: {method}")

        stored = self.gateway_provider.client_secret_hash(client_id)
        if method != "none":
            if not stored:
                raise AuthenticationError(
                    "Client is registered for secret-based authentication but has no stored secret"
                )
            if not presented:
                raise AuthenticationError("Client secret is required")
            if not hmac.compare_digest(hash_token(presented).encode(), stored.encode()):
                raise AuthenticationError("Invalid client_secret")
            if client.client_secret_expires_at and client.client_secret_expires_at < int(time.time()):
                raise AuthenticationError("Client secret has expired")
        return client
