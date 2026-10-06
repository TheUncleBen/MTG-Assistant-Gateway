"""OpenID Connect relying-party client for the identity provider.

The gateway is one confidential OIDC client of the identity provider. It runs
the authorization-code flow with PKCE, exchanges the code at the token
endpoint with its client secret (``client_secret_post`` or
``client_secret_basic``), and validates the ID token's signature, issuer,
audience, nonce and expiry against the provider's JWKS. Group membership is
read from a configurable claim path so any provider's shape works (Authentik
``groups``, Keycloak ``realm_access.roles``, Zitadel role maps, ...).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from joserfc import jwt
from joserfc.errors import JoseError
from joserfc.jwk import KeySet

logger = logging.getLogger(__name__)

ALLOWED_ALGS = ["RS256", "RS384", "RS512", "ES256", "ES384", "ES512", "PS256"]
TOKEN_AUTH_METHODS = ("client_secret_post", "client_secret_basic")
DEFAULT_GROUPS_CLAIM = "groups"
MAX_GROUPS = 200
MAX_GROUP_LEN = 200
MAX_GROUPS_CLAIM_LEN = 200


class OIDCError(Exception):
    """A failure in the identity-provider leg; the message is safe to log."""


class IdPUnavailable(OIDCError):
    """The identity provider could not be asked (network error, timeout, 5xx, bad JSON). The
    answer is unknown, so callers fail closed for the request at hand and try again later."""


@dataclass
class Identity:
    sub: str
    email: str | None
    name: str | None
    preferred_username: str | None
    groups: list[str]
    raw_claims: dict[str, Any]
    # The provider's own tokens from this sign-in, kept (encrypted) so membership can be asked
    # again on later requests. Never logged or shown (repr=False).
    idp_access_token: str | None = field(default=None, repr=False)
    idp_access_expires_at: int | None = field(default=None, repr=False)
    idp_refresh_token: str | None = field(default=None, repr=False)


@dataclass
class IdPTokens:
    """A token response from the provider (sign-in or refresh)."""

    access_token: str | None = field(default=None, repr=False)
    access_expires_at: int | None = None
    refresh_token: str | None = field(default=None, repr=False)


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return verifier, challenge


class OIDCClient:
    def __init__(
        self,
        issuer: str,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        scopes: str,
        *,
        http: httpx.AsyncClient | None = None,
        groups_claim: str = DEFAULT_GROUPS_CLAIM,
        token_auth_method: str = "client_secret_post",
    ):
        self.issuer = issuer.rstrip("/")
        self.client_id = client_id
        self._client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.scopes = scopes
        self.groups_claim = validate_groups_claim(groups_claim)
        if token_auth_method not in TOKEN_AUTH_METHODS:
            raise ValueError(f"token_auth_method must be one of {', '.join(TOKEN_AUTH_METHODS)}")
        self.token_auth_method = token_auth_method
        self._http = http or httpx.AsyncClient(timeout=httpx.Timeout(15.0))
        self._metadata: dict[str, Any] | None = None
        self._metadata_at = 0.0
        self._jwks: KeySet | None = None
        self._jwks_at = 0.0

    async def aclose(self) -> None:
        await self._http.aclose()

    async def metadata(self) -> dict[str, Any]:
        if self._metadata and time.time() - self._metadata_at < 3600:
            return self._metadata
        url = f"{self.issuer}/.well-known/openid-configuration"
        try:
            resp = await self._http.get(url)
            resp.raise_for_status()
            data = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise OIDCError(f"cannot load identity-provider metadata from {url}: {exc}") from exc
        issuer = str(data.get("issuer", "")).rstrip("/")
        if issuer != self.issuer:
            raise OIDCError(
                f"identity-provider metadata issuer {issuer!r} does not match configured {self.issuer!r}"
            )
        for key in ("authorization_endpoint", "token_endpoint", "jwks_uri"):
            if not data.get(key):
                raise OIDCError(f"identity-provider metadata lacks {key}")
        self._metadata, self._metadata_at = data, time.time()
        return data

    async def jwks(self, *, force: bool = False) -> KeySet:
        if self._jwks and not force and time.time() - self._jwks_at < 3600:
            return self._jwks
        meta = await self.metadata()
        try:
            resp = await self._http.get(meta["jwks_uri"])
            resp.raise_for_status()
            self._jwks = KeySet.import_key_set(resp.json())
        except (httpx.HTTPError, ValueError, JoseError) as exc:
            raise OIDCError(f"cannot load identity-provider signing keys: {exc}") from exc
        self._jwks_at = time.time()
        return self._jwks

    async def authorization_url(
        self, *, state: str, nonce: str, code_challenge: str, prompt: str | None = None
    ) -> str:
        meta = await self.metadata()
        params = {
            "response_type": "code",
            "client_id": self.client_id,
            "redirect_uri": self.redirect_uri,
            "scope": self.scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        if prompt:
            params["prompt"] = prompt
        return f"{meta['authorization_endpoint']}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str, nonce: str) -> Identity:
        meta = await self.metadata()
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": self.redirect_uri,
            "code_verifier": code_verifier,
        }
        headers = self._token_auth(form)
        try:
            resp = await self._http.post(meta["token_endpoint"], data=form, headers=headers)
        except httpx.HTTPError as exc:
            raise OIDCError(f"token request to identity provider failed: {exc}") from exc
        if resp.status_code != 200:
            raise OIDCError(f"identity provider rejected the code exchange (HTTP {resp.status_code})")
        try:
            body = resp.json()
        except ValueError as exc:
            raise OIDCError("identity provider returned a non-JSON token response") from exc
        id_token = body.get("id_token")
        if not isinstance(id_token, str):
            raise OIDCError("identity provider returned no id_token; is the 'openid' scope enabled?")
        claims = await self._validate_id_token(id_token, nonce)

        tokens = _token_fields(body)
        identity = Identity(
            idp_access_token=tokens.access_token,
            idp_access_expires_at=tokens.access_expires_at,
            idp_refresh_token=tokens.refresh_token,
            sub=str(claims["sub"]),
            email=_opt_str(claims.get("email")),
            name=_opt_str(claims.get("name")),
            preferred_username=_opt_str(claims.get("preferred_username")),
            groups=resolve_groups(claims, self.groups_claim),
            raw_claims=claims,
        )
        # Many providers place profile claims in the ID token, but some put
        # them only behind userinfo. Fill gaps from userinfo when a token allows it.
        access_token = body.get("access_token")
        if (
            meta.get("userinfo_endpoint")
            and isinstance(access_token, str)
            and (identity.email is None or not identity.groups)
        ):
            try:
                ui = await self._http.get(
                    meta["userinfo_endpoint"], headers={"Authorization": f"Bearer {access_token}"}
                )
                if ui.status_code == 200:
                    info = ui.json()
                    if str(info.get("sub")) == identity.sub:
                        identity.email = identity.email or _opt_str(info.get("email"))
                        identity.name = identity.name or _opt_str(info.get("name"))
                        identity.preferred_username = identity.preferred_username or _opt_str(
                            info.get("preferred_username")
                        )
                        identity.groups = identity.groups or resolve_groups(info, self.groups_claim)
            except (httpx.HTTPError, ValueError):
                logger.warning("userinfo lookup failed; continuing with ID token claims only")
        return identity

    def _token_auth(self, form: dict[str, str]) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token_auth_method == "client_secret_basic":
            headers["Authorization"] = basic_auth_header(self.client_id, self._client_secret)
        else:
            form["client_id"] = self.client_id
            form["client_secret"] = self._client_secret
        return headers

    async def refresh(self, refresh_token: str) -> IdPTokens | None:
        """Use the provider refresh token. None when the provider refuses it (HTTP 400/401: the
        user was deactivated or deleted, or the grant was revoked); IdPUnavailable when the
        answer is unknown."""
        try:
            meta = await self.metadata()
        except OIDCError as exc:
            raise IdPUnavailable(str(exc)) from exc
        form = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        headers = self._token_auth(form)
        try:
            resp = await self._http.post(meta["token_endpoint"], data=form, headers=headers)
        except httpx.HTTPError as exc:
            raise IdPUnavailable(
                f"refresh request to identity provider failed: {type(exc).__name__}"
            ) from exc
        if resp.status_code in (400, 401):
            return None
        if resp.status_code != 200:
            raise IdPUnavailable(f"identity provider answered the refresh with HTTP {resp.status_code}")
        try:
            body = resp.json()
        except ValueError as exc:
            raise IdPUnavailable("identity provider returned a non-JSON refresh response") from exc
        if not isinstance(body, dict) or not isinstance(body.get("access_token"), str):
            raise IdPUnavailable("identity provider refresh response has no access_token")
        tokens = _token_fields(body)
        tokens.refresh_token = tokens.refresh_token or refresh_token  # providers that don't rotate
        return tokens

    async def userinfo(self, access_token: str) -> dict[str, Any] | None:
        """The provider's live view of the user. None when the provider refuses the token
        (HTTP 401/403); IdPUnavailable when the answer is unknown."""
        try:
            meta = await self.metadata()
        except OIDCError as exc:
            raise IdPUnavailable(str(exc)) from exc
        endpoint = meta.get("userinfo_endpoint")
        if not endpoint:
            raise IdPUnavailable("identity-provider metadata lacks userinfo_endpoint")
        try:
            resp = await self._http.get(
                endpoint, headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"}
            )
        except httpx.HTTPError as exc:
            raise IdPUnavailable(f"userinfo request failed: {type(exc).__name__}") from exc
        if resp.status_code in (401, 403):
            return None
        if resp.status_code != 200:
            raise IdPUnavailable(f"identity provider answered userinfo with HTTP {resp.status_code}")
        try:
            info = resp.json()
        except ValueError as exc:
            raise IdPUnavailable("identity provider returned non-JSON userinfo") from exc
        if not isinstance(info, dict):
            raise IdPUnavailable("identity provider returned non-object userinfo")
        return info

    async def _validate_id_token(self, id_token: str, nonce: str) -> dict[str, Any]:
        last_exc: Exception | None = None
        for attempt in range(2):
            keys = await self.jwks(force=attempt == 1)
            try:
                token = jwt.decode(id_token, keys, algorithms=ALLOWED_ALGS)
                registry = jwt.JWTClaimsRegistry(
                    iss={"essential": True, "value": self.issuer},
                    aud={"essential": True, "value": self.client_id},
                    sub={"essential": True},
                    exp={"essential": True},
                    iat={"essential": True},
                    nonce={"essential": True, "value": nonce},
                    leeway=60,
                )
                claims = dict(token.claims)
                # joserfc compares 'iss' exactly; accept a trailing-slash variant.
                claims["iss"] = str(claims.get("iss", "")).rstrip("/")
                registry.validate(claims)
                aud = claims.get("aud")
                if isinstance(aud, list) and len(aud) > 1 and claims.get("azp") != self.client_id:
                    # OIDC Core 3.1.3.7: a token for several audiences must name this client as
                    # the authorized party, or it was issued to someone else.
                    raise OIDCError("ID token validation failed: azp does not name this client")
                return claims
            except JoseError as exc:
                last_exc = exc
                # A key-id miss can mean the provider rotated keys; refresh once.
                continue
        raise OIDCError(f"ID token validation failed: {type(last_exc).__name__}")


def basic_auth_header(client_id: str, client_secret: str) -> str:
    """HTTP Basic credentials for ``client_secret_basic`` (RFC 6749 section 2.3.1): the id and
    the secret are each form-urlencoded before being joined and base64-encoded."""
    userpass = f"{quote(client_id, safe='')}:{quote(client_secret, safe='')}"
    return "Basic " + base64.b64encode(userpass.encode("ascii")).decode("ascii")


def validate_groups_claim(path: str) -> str:
    """Check a groups-claim dot-path and return it. Raises ValueError with a plain message.

    Segments are separated by dots, so a claim name that itself contains a dot cannot be
    expressed; the path must be non-empty, have no empty segment and be at most
    ``MAX_GROUPS_CLAIM_LEN`` characters.
    """
    if not isinstance(path, str) or not path.strip():
        raise ValueError("groups claim must not be empty")
    if path != path.strip():
        raise ValueError("groups claim must not have leading or trailing whitespace")
    if len(path) > MAX_GROUPS_CLAIM_LEN:
        raise ValueError(f"groups claim must be at most {MAX_GROUPS_CLAIM_LEN} characters")
    if any(seg == "" for seg in path.split(".")):
        raise ValueError("groups claim must not start or end with a dot or contain '..'")
    return path


def resolve_groups(claims: Any, path: str) -> list[str]:
    """Read group names from ``claims`` at ``path``; fail closed to ``[]``.

    The whole path is tried as one literal claim name first (Auth0-style
    ``https://example.com/groups``), then walked as a dot-path through dicts only; a missing key
    or a non-dict on the way yields no groups. The value may be a list of strings (non-strings
    dropped), a single string, or a dict whose keys are the group names and whose values are
    truthy (Zitadel sends roles as ``{"role": {"<org>": ...}}``; a key mapped to ``false`` or
    ``null`` is not a membership). Anything else yields no groups. Names are compared exactly as
    sent (no whitespace trimming, so ``" admins "`` is never ``"admins"``); empty and blank names
    are dropped, duplicates removed in order, and the result capped at ``MAX_GROUPS`` entries of
    at most ``MAX_GROUP_LEN`` chars.
    """
    if isinstance(claims, dict) and path in claims:
        node: Any = claims[path]
    else:
        node = claims
        for segment in path.split("."):
            if not isinstance(node, dict) or segment not in node:
                return []
            node = node[segment]
    if isinstance(node, str):
        candidates: list[Any] = [node]
    elif isinstance(node, list):
        candidates = node
    elif isinstance(node, dict):
        candidates = [key for key, value in node.items() if value]
    else:
        return []
    out: list[str] = []
    seen: set[str] = set()
    for name in candidates:
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_GROUP_LEN or name in seen:
            continue
        seen.add(name)
        out.append(name)
        if len(out) >= MAX_GROUPS:
            break
    return out


def _token_fields(body: dict[str, Any]) -> IdPTokens:
    access = body.get("access_token")
    refresh = body.get("refresh_token")
    expires_in = body.get("expires_in")
    expires_at = None
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool) and expires_in > 0:
        expires_at = int(time.time()) + int(min(expires_in, 366 * 86400))
    return IdPTokens(
        access_token=access if isinstance(access, str) and access else None,
        access_expires_at=expires_at,
        refresh_token=refresh if isinstance(refresh, str) and refresh else None,
    )


def _opt_str(v: Any) -> str | None:
    return v if isinstance(v, str) and v else None
