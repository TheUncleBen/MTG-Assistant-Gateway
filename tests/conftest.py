"""Test fixtures: a fake OpenID provider and a gateway wired to it in memory."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import dataclasses
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey
from mcp import Client
from mcp.server.mcpserver import MCPServer
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from mtg_gateway import scan as scan_pkg
from mtg_gateway import theme
from mtg_gateway.app import create_app
from mtg_gateway.archidekt import ArchidektClient, Pacer
from mtg_gateway.clickguard import MIN_FORM_AGE, form_stamp
from mtg_gateway.config import Settings
from mtg_gateway.db import Database
from mtg_gateway.mf_proxy import MysticForgeProxy
from mtg_gateway.oidc import OIDCClient
from mtg_gateway.scan import scryfall as scan_scryfall

from .fake_archidekt import FakeArchidekt
from .fake_mystic_forge import fake_mystic_forge
from .fake_scryfall import FakeScryfall

IDP = "https://idp.test/application/o/mtg"
GATEWAY = "https://mtg.test"
CLIENT_ID = "gateway-client"
CLIENT_SECRET = "gateway-secret-value"


class FakeIdP:
    """Minimal OIDC provider: discovery, authorize (auto-approves), token, jwks, userinfo."""

    def __init__(self) -> None:
        self.key = RSAKey.generate_key(2048, parameters={"kid": "test-1", "alg": "RS256", "use": "sig"})
        self.user: dict[str, object] = {
            "sub": "user-1",
            "email": "alice@example.test",
            "name": "Alice",
            "preferred_username": "alice",
            "groups": ["mtg-users"],
        }
        self.pending: dict[str, dict[str, str]] = {}
        # Each token request's form fields plus "_auth_method": which client authentication
        # the gateway used (client_secret_post or client_secret_basic).
        self.token_calls: list[dict[str, str]] = []
        # Knobs for the minted ID token: claims merged in last, and user keys left out of it.
        # Lets a test move groups under a nested claim (Keycloak/Zitadel shapes) without
        # touching userinfo or the default behaviour.
        self.id_token_claims: dict[str, object] = {}
        self.id_token_omit: set[str] = set()
        # When the person last entered credentials here: set by an authorize request with
        # prompt=login (unless honours_prompt_login is off) and sent as the ID token's auth_time.
        # None sends no auth_time, as before.
        self.honours_prompt_login = True
        self.auth_time: int | None = None
        # "HS256" signs the ID token with the client secret, as Authentik does when the provider
        # has no Signing Key.
        self.id_token_alg = "RS256"
        # Extra fields for the ID token's JWS header (e.g. a certificate chain in x5c).
        self.id_token_header: dict[str, object] = {}
        # The provider's live directory: sub -> the user dict as it is *now* (the same object a
        # test assigned to ``self.user``, so editing it in place changes what userinfo says).
        # ``disabled`` subs get 401 from userinfo and invalid_grant on refresh, like a deactivated
        # Authentik user; ``down`` makes token and userinfo calls fail with 503.
        self.directory: dict[str, dict[str, object]] = {}
        self.disabled: set[str] = set()
        self.down = False
        self.access_tokens: dict[str, str] = {}
        self.refresh_tokens: dict[str, str] = {}
        self.userinfo_calls = 0
        self.userinfo_methods: list[str] = []
        # Extra characters in every minted access token: Authentik copies the ID token's claims
        # into its access token, so a big claim (an embedded avatar) makes it huge.
        self.access_token_pad = 0
        # The optional Authentik scope mapping for uploaded pictures (docs/IDP-AUTHENTIK.md):
        # sub -> data URI. Tokens and userinfo carry a short version; userinfo asked with
        # mtg_picture=1 carries the image. picture_fetches counts those asks.
        self.uploaded: dict[str, str] = {}
        self.picture_fetches = 0
        self.access_ttl: int | None = 300  # None: no expires_in in token responses
        # Provider shapes: keys userinfo leaves out (e.g. groups only in the ID token), whether a
        # refresh returns a new ID token, and an error code every refresh fails with.
        self.userinfo_omit: set[str] = set()
        self.refresh_id_token = False
        self.refresh_error: str | None = None
        # A slow provider: userinfo answers after this many seconds. ``token_status`` makes only the
        # token endpoint answer with that HTTP status (a 502 from a proxy in front of it, say).
        self.userinfo_delay = 0.0
        self.token_status: int | None = None
        self.app = Starlette(
            routes=[
                Route("/application/o/mtg/.well-known/openid-configuration", self.discovery),
                Route("/application/o/authorize/", self.authorize),
                Route("/application/o/token/", self.token, methods=["POST"]),
                Route("/application/o/mtg/jwks/", self.jwks),
                Route("/application/o/userinfo/", self.userinfo, methods=["GET", "POST"]),
            ]
        )

    async def discovery(self, _req: Request) -> Response:
        return JSONResponse(
            {
                "issuer": IDP,
                "authorization_endpoint": "https://idp.test/application/o/authorize/",
                "token_endpoint": "https://idp.test/application/o/token/",
                "jwks_uri": f"{IDP}/jwks/",
                "userinfo_endpoint": "https://idp.test/application/o/userinfo/",
            }
        )

    async def authorize(self, req: Request) -> Response:
        q = dict(req.query_params)
        assert q["client_id"] == CLIENT_ID
        assert q["code_challenge_method"] == "S256"
        code = secrets.token_urlsafe(16)
        if q.get("prompt") == "login" and self.honours_prompt_login:
            self.auth_time = int(time.time())
        self.pending[code] = q
        return RedirectResponse(f"{q['redirect_uri']}?{urlencode({'code': code, 'state': q['state']})}", 302)

    async def token(self, req: Request) -> Response:
        form = {k: str(v) for k, v in (await req.form()).items()}
        authz = req.headers.get("authorization", "")
        if authz.startswith("Basic "):
            # client_secret_basic: id and secret form-urlencoded, then base64 (RFC 6749 2.3.1).
            if "client_secret" in form:
                return JSONResponse({"error": "invalid_request"}, status_code=400)
            try:
                user, _, secret = base64.b64decode(authz[6:]).decode("ascii").partition(":")
            except ValueError:
                user = secret = ""
            form["_auth_method"] = "client_secret_basic"
            form.setdefault("client_id", unquote(user))
            ok = unquote(user) == CLIENT_ID and unquote(secret) == CLIENT_SECRET
        else:
            form["_auth_method"] = "client_secret_post"
            ok = form.get("client_id") == CLIENT_ID and form.get("client_secret") == CLIENT_SECRET
        self.token_calls.append(form)
        if self.token_status is not None:
            return JSONResponse({"error": "temporarily_unavailable"}, status_code=self.token_status)
        if self.down:
            return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
        if not ok:
            return JSONResponse({"error": "invalid_client"}, status_code=401)
        if form.get("grant_type") == "refresh_token":
            if self.refresh_error:
                return JSONResponse({"error": self.refresh_error}, status_code=401)
            sub = self.refresh_tokens.pop(form.get("refresh_token", ""), None)
            if sub is None or sub in self.disabled:
                return JSONResponse({"error": "invalid_grant"}, status_code=400)
            body = self._mint(sub, offline=True)
            if self.refresh_id_token:
                now = int(time.time())
                claims = {"iss": IDP, "aud": CLIENT_ID, "exp": now + 300, "iat": now, **self.directory[sub]}
                body["id_token"] = jwt.encode({"alg": "RS256", "kid": "test-1"}, claims, self.key)
            return JSONResponse(body)
        q = self.pending.pop(form.get("code", ""), None)
        if q is None:
            return JSONResponse({"error": "invalid_grant"}, status_code=400)
        now = int(time.time())
        claims = {
            "iss": IDP,
            "aud": CLIENT_ID,
            "exp": now + 300,
            "iat": now,
            "nonce": q["nonce"],
            **{k: v for k, v in self.user.items() if k not in self.id_token_omit},
            **self._uploaded_claims(str(self.user["sub"]), full=False),
            **({"auth_time": self.auth_time} if self.auth_time is not None else {}),
            **self.id_token_claims,
        }
        if self.id_token_alg == "HS256":
            id_token = jwt.encode({"alg": "HS256"}, claims, OctKey.import_key(CLIENT_SECRET))
        else:
            id_token = jwt.encode({"alg": "RS256", "kid": "test-1", **self.id_token_header}, claims, self.key)
        sub = str(self.user["sub"])
        self.directory[sub] = self.user
        body = self._mint(sub, offline="offline_access" in q.get("scope", "").split())
        return JSONResponse({**body, "id_token": id_token})

    def _mint(self, sub: str, *, offline: bool) -> dict[str, object]:
        access = f"idp-access-{secrets.token_urlsafe(8)}" + "x" * self.access_token_pad
        self.access_tokens[access] = sub
        body: dict[str, object] = {
            "access_token": access,
            "token_type": "Bearer",
            "expires_in": self.access_ttl,
        }
        if offline:
            refresh = f"idp-refresh-{secrets.token_urlsafe(8)}"
            self.refresh_tokens[refresh] = sub
            body["refresh_token"] = refresh
        return body

    async def jwks(self, _req: Request) -> Response:
        return JSONResponse({"keys": [self.key.as_dict(private=False)]})

    async def userinfo(self, req: Request) -> Response:
        self.userinfo_calls += 1
        if self.userinfo_delay:
            await asyncio.sleep(self.userinfo_delay)
        if self.down:
            return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
        token = req.headers.get("authorization", "").removeprefix("Bearer ")
        if len(token) > 8192:
            # nginx's default limit on one request header line (large_client_header_buffers)
            return Response("Request Header Or Cookie Too Large", status_code=400)
        if not token and req.method == "POST":
            # RFC 6750 2.2, parsed by hand: Starlette's form parser stops at 1 MB, Django's at 2.5 MB
            token = parse_qs((await req.body()).decode()).get("access_token", [""])[0]
        self.userinfo_methods.append(req.method)
        sub = self.access_tokens.get(token)
        if sub is None or sub in self.disabled:
            return Response(status_code=401)
        info = self.directory.get(sub, self.user)
        full = req.query_params.get("mtg_picture") == "1"
        self.picture_fetches += full
        return JSONResponse(
            {k: v for k, v in info.items() if k not in self.userinfo_omit}
            | self._uploaded_claims(sub, full=full)
        )

    def _uploaded_claims(self, sub: str, *, full: bool) -> dict[str, str]:
        avatar = self.uploaded.get(sub)
        if not avatar:
            return {}
        if full:
            return {"mtg_picture": avatar}
        return {"mtg_picture_version": hashlib.sha256(avatar.encode()).hexdigest()[:16]}

    def set_groups(self, sub: str, groups: list[str]) -> None:
        """Change a user's groups at the provider (as an admin would in Authentik)."""
        self.directory[sub]["groups"] = list(groups)


def aged_stamp(settings: Settings, scope: str) -> str:
    """A click-guard render stamp (``shown``) for ``scope``, old enough to be accepted: what a
    guarded form carries once a person has had it on screen for a moment."""
    return form_stamp(settings.session_secret, scope, now=time.time() - 2 * MIN_FORM_AGE)


def consent_stamp(settings: Settings, login_id: str) -> str:
    """The accepted ``shown`` stamp for the consent page of ``login_id``."""
    return aged_stamp(settings, f"consent:{login_id}")


def proposal_stamp(h: Harness, http: httpx.AsyncClient, pid: str) -> str:
    """The accepted ``shown`` stamp for proposal ``pid``'s review page, for the member signed in
    on ``http`` (the stamp is bound to that member)."""
    sid = next(c.value for c in http.cookies.jar if c.name.endswith("mtg_session") and c.value)
    sub = h.db.get_browser_session(sid)
    assert sub, "not signed in"
    return aged_stamp(h.settings, f"proposal:{sub}:{pid}")


def make_settings(tmp_path: Path, **over: object) -> Settings:
    from cryptography.fernet import Fernet

    base = dict(
        public_url=GATEWAY,
        oidc_issuer=IDP,
        oidc_client_id=CLIENT_ID,
        oidc_client_secret=CLIENT_SECRET,
        oidc_scopes="openid profile email offline_access",
        required_group=None,
        session_secret="s" * 48,
        fernet_key=Fernet.generate_key().decode(),
        data_dir=tmp_path / "data",
        backup_dir=tmp_path / "backups",
        backup_hour_utc=3,
        backup_keep_days=14,
        allowed_hosts=["mtg.test", "mtg.test:*"],
        update_check=False,  # tests never ask GitHub (test_update_check.py drives the checker itself)
    )
    base.update(over)
    if "env_set" not in over:
        # a managed setting a test passes is one its deployment set: live readers (LiveSettings)
        # then use it, as they use a stack's value (0.7.29, the live approval settings)
        from mtg_gateway.config import MANAGED_SETTINGS

        base["env_set"] = frozenset(k for k in over if k in MANAGED_SETTINGS)
    return Settings(**base)  # type: ignore[arg-type]


class Harness:
    def __init__(self, settings: Settings, idp: FakeIdP, **app_kw: object):
        self.settings = settings
        self.idp = idp
        self.db = Database(settings.db_path)
        idp_http = httpx.AsyncClient(transport=httpx.ASGITransport(app=idp.app), base_url="https://idp.test")
        self.oidc = OIDCClient(
            IDP, CLIENT_ID, CLIENT_SECRET, settings.callback_url, settings.oidc_scopes, http=idp_http
        )
        self.app = create_app(settings, db=self.db, oidc=self.oidc, **app_kw)  # type: ignore[arg-type]
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=GATEWAY)

    async def register(self, redirect_uri: str = "https://client.test/cb", **extra: object) -> dict:
        r = await self.http.post(
            "/register", json={"redirect_uris": [redirect_uri], "client_name": "t", **extra}
        )
        assert r.status_code == 201, r.text
        return r.json()

    async def start_login(
        self,
        client: dict,
        *,
        redirect_uri: str = "https://client.test/cb",
        resource: str | None = None,
        state: str = "cs1",
        scope: str | None = "mtg",
    ) -> tuple[httpx.Response, str]:
        from mtg_gateway.oidc import pkce_pair

        verifier, challenge = pkce_pair()
        params = {
            "client_id": client["client_id"],
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if scope is not None:
            params["scope"] = scope
        if resource:
            params["resource"] = resource
        r = await self.http.get("/authorize", params=params)
        return r, verifier

    async def approve(self, gateway_redirect: httpx.Response) -> httpx.Response:
        """Press Approve on the gateway's consent page that /authorize redirected to; return the
        303 to the IdP. A redirect that is not to the consent page is returned unchanged."""
        loc = gateway_redirect.headers["location"]
        if not loc.startswith(f"{GATEWAY}/authorize/confirm?"):
            return gateway_redirect
        login_id = parse_qs(urlparse(loc).query)["state"][0]
        page = await self.http.get("/authorize/confirm", params={"state": login_id})
        assert page.status_code == 200, page.text
        csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page.text).group(1)  # type: ignore[union-attr]
        go = await self.http.post(
            "/authorize/confirm",
            data={
                "state": login_id,
                "csrf": csrf,
                "action": "approve",
                "shown": consent_stamp(self.settings, login_id),
            },
        )
        assert go.status_code == 303, go.text
        return go

    async def idp_leg(self, gateway_redirect: httpx.Response) -> str:
        """Drive the fake IdP from the gateway's redirect (approving the gateway's consent page
        first when it is that); return the gateway callback URL."""
        gateway_redirect = await self.approve(gateway_redirect)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.idp.app)) as c:
            r = await c.get(gateway_redirect.headers["location"])
        assert r.status_code == 302
        return r.headers["location"]

    async def callback(self, url: str, *, with_cookie: bool = True) -> httpx.Response:
        parsed = urlparse(url)
        assert parsed.path == "/auth/callback"
        if with_cookie:
            return await self.http.get(f"{parsed.path}?{parsed.query}")
        # A fresh client has no cookie jar: simulates a different browser.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url=GATEWAY) as c:
            return await c.get(f"{parsed.path}?{parsed.query}")

    async def full_login(self, client: dict, **kw: object) -> tuple[str, str]:
        """Return (gateway auth code, PKCE verifier)."""
        r, verifier = await self.start_login(client, **kw)  # type: ignore[arg-type]
        assert r.status_code == 302, r.text
        cb = await self.idp_leg(r)
        r2 = await self.callback(cb)
        assert r2.status_code == 302, r2.text
        q = parse_qs(urlparse(r2.headers["location"]).query)
        assert q["state"] == ["cs1"]
        return q["code"][0], verifier

    async def token(self, client: dict, **form: str) -> httpx.Response:
        data = {"client_id": client["client_id"], "client_secret": client["client_secret"], **form}
        return await self.http.post("/token", data=data)

    async def tokens_for(self, client: dict, redirect_uri: str = "https://client.test/cb") -> dict:
        code, verifier = await self.full_login(client)
        r = await self.token(
            client,
            grant_type="authorization_code",
            code=code,
            code_verifier=verifier,
            redirect_uri=redirect_uri,
        )
        assert r.status_code == 200, r.text
        return r.json()

    async def mcp(
        self, token: str | None, method: str, params: dict | None = None, rid: int = 1
    ) -> httpx.Response:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
            "Host": "mtg.test",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        return await self.http.post("/mcp", headers=headers, content=json.dumps(body))

    async def aclose(self) -> None:
        await self.http.aclose()


def sse_json(resp: httpx.Response) -> dict:
    """Extract the single JSON-RPC message from a JSON or SSE response body."""
    if resp.headers.get("content-type", "").startswith("application/json"):
        return resp.json()
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:].strip())
    raise AssertionError(f"no SSE data in response: {resp.status_code} {resp.text[:200]}")


@pytest.fixture
def idp() -> FakeIdP:
    return FakeIdP()


# Card resolution prefers printings already released "today" (scan/scryfall.py). Some fixture
# printings come out after the fixtures were recorded, so every test runs on one fixed date.
TODAY = "2026-10-11"


@pytest.fixture(autouse=True)
def _fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(scan_scryfall, "today", lambda: TODAY)


@pytest.fixture(autouse=True)
def _reset_theme_globals(monkeypatch: pytest.MonkeyPatch) -> None:
    """create_app sets these page-wide globals; put them back after each test so one test's
    settings (watchlists on, a public URL) never leak into the next."""
    monkeypatch.setattr(theme, "PUBLIC_URL", theme.PUBLIC_URL)
    monkeypatch.setattr(theme, "WATCHLISTS", theme.WATCHLISTS)


@pytest.fixture(autouse=True)
def _forget_package_lists() -> None:
    """packages.listing keeps a member's own package list for a minute; tests share member ids,
    so each test starts with that memory empty."""
    from mtg_gateway import packages

    packages._MINE.clear()


# Tests stay offline. A gateway built without a Scryfall client of its own (create_app makes one)
# gets one that answers from the recorded fixtures, so its start-up download of the card-name
# catalog never reaches api.scryfall.com; and any request that still heads for a host other than
# this machine is refused and fails the test that made it (SCAN_LIVE=1 lets the opt-in live
# check in test_scan.py through). The guard sits at every way out: httpx's transports, a socket's
# connect, and the name lookup before it (so not even a DNS query leaves). A refusal from a server
# thread or a background task after its test has ended fails the next test's set-up, or the run's
# last test, so none goes unnoticed. The end-to-end suite (tests/e2e/run.sh test, which sets
# E2E_PUBLIC_URL) talks to its deployed stack by name, so the guard stands aside for it.
OUTBOUND: list[str] = []
E2E = bool(os.environ.get("E2E_PUBLIC_URL"))
ONLINE = bool(os.environ.get("SCAN_LIVE")) or E2E


def _this_machine(host: str | bytes | None) -> bool:
    if ONLINE:
        return True
    host = (host.decode() if isinstance(host, bytes) else host or "").strip("[]")
    if host in ("", "localhost", "testserver"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _reserved(host: str | None) -> bool:
    """A name under the reserved .test or .example domains (RFC 2606, such as the fake identity
    provider idp.test or a fake custom card picture at cdn.example) never resolves, so a request
    to it fails as it would offline: refused, not counted."""
    name = (host or "").rstrip(".").lower()
    return name.endswith(".test") or name.endswith(".example")


def _refuse(where: str, host: str | None = None) -> None:
    if not _reserved(host):
        OUTBOUND.append(where)
    raise httpx.ConnectError(f"tests stay offline, refused: {where}")


_real_async_send = httpx.AsyncHTTPTransport.handle_async_request
_real_send = httpx.HTTPTransport.handle_request
_real_connect = socket.socket.connect
_real_getaddrinfo = socket.getaddrinfo


async def _offline_async_send(self: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
    if not _this_machine(request.url.host):
        _refuse(str(request.url), request.url.host)
    return await _real_async_send(self, request)


def _offline_send(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
    if not _this_machine(request.url.host):
        _refuse(str(request.url), request.url.host)
    return _real_send(self, request)


def _offline_connect(self: socket.socket, address: object) -> None:
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _this_machine(address[0]):  # type: ignore[index]
        OUTBOUND.append(f"{address}")
        raise OSError(f"tests stay offline, refused: {address}")
    return _real_connect(self, address)  # type: ignore[arg-type]


def _offline_getaddrinfo(host: str | bytes | None, *args: object, **kwargs: object) -> list:
    name = host.decode() if isinstance(host, bytes) else str(host)
    if not _this_machine(host) and name != socket.gethostname():
        if not _reserved(name):
            OUTBOUND.append(f"name lookup: {name}")
        raise socket.gaierror(socket.EAI_NONAME, f"tests stay offline, refused: {name}")
    return _real_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]


httpx.AsyncHTTPTransport.handle_async_request = _offline_async_send  # type: ignore[method-assign]
httpx.HTTPTransport.handle_request = _offline_send  # type: ignore[method-assign]
socket.socket.connect = _offline_connect  # type: ignore[method-assign]
socket.getaddrinfo = _offline_getaddrinfo  # type: ignore[assignment]

try:
    from playwright.sync_api import BrowserType
except ImportError:  # the browser tests skip
    BrowserType = None

if BrowserType is not None and not ONLINE:
    # The browser tests' Chromium finds no host but this machine and uses no proxy: what a page
    # loads from elsewhere (card pictures) a test serves itself with page.route, or it fails.
    _real_launch = BrowserType.launch

    def _offline_launch(self, **kwargs):
        if self.name == "chromium":
            rules = "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE localhost, EXCLUDE 127.0.0.1"
            kwargs["args"] = [*(kwargs.get("args") or []), "--no-proxy-server", rules]
        return _real_launch(self, **kwargs)

    BrowserType.launch = _offline_launch  # type: ignore[method-assign]


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch):
    if E2E:
        yield
        return
    real = scan_pkg.ScryfallClient

    def recorded(*args, **kwargs):
        if kwargs.get("http") is None:
            kwargs.update(base="https://scryfall.test", http=FakeScryfall().client())
            args = args[1:]
        return real(*args, **kwargs)

    early = _unreported()
    assert early == [], f"something reached the network after an earlier test ended: {early}"
    monkeypatch.setattr(scan_pkg, "ScryfallClient", recorded)
    yield
    reached = _unreported()
    assert reached == [], f"a test reached the network: {reached}"


_REPORTED = [0]


def _unreported() -> list[str]:
    """The refusals not yet reported by a test (OUTBOUND only grows)."""
    new = OUTBOUND[_REPORTED[0] :]
    _REPORTED[0] = len(OUTBOUND)
    return new


@pytest.fixture(autouse=True, scope="session")
def _offline_to_the_end():
    """A refusal after the last test of the run (a server thread still finishing) fails it too."""
    yield
    late = _unreported()
    assert late == [], f"something reached the network after the last test ended: {late}"


@contextlib.asynccontextmanager
async def running(h: Harness):
    """Run the app lifespan in one dedicated task (anyio cancel scopes must
    be entered and exited by the same task, and pytest fixtures are not)."""
    started = asyncio.Event()
    stop = asyncio.Event()

    async def runner() -> None:
        async with h.app.router.lifespan_context(h.app):
            started.set()
            await stop.wait()

    task = asyncio.create_task(runner())
    await started.wait()
    try:
        yield h
    finally:
        stop.set()
        await task
        await h.aclose()


@pytest.fixture
async def gw(tmp_path: Path, idp: FakeIdP):
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        yield h


# -- the ``stack`` fixture: a running gateway with its fakes ---------------------------------------
# Many modules test against one running gateway with a fake Archidekt behind it. They share the
# ``stack`` fixture below; what differs between them (settings, a Mystic Forge proxy, a fake
# Scryfall to inspect, something set up once it runs) is spelled out in a StackOptions the module
# picks with ``stack_options = stack_with(...)``. A module that picks nothing gets AUTO.


class Stack:
    """A running gateway (``h``) and the fakes behind it: ``ark`` (Archidekt, None without one) and
    ``sf`` (the Scryfall the scan service asks, when the options swapped one in). A ``started``
    hook may add more (the watchlist tests' ``ntfy``)."""

    def __init__(
        self,
        h: Harness,
        ark: FakeArchidekt | None = None,
        sf: FakeScryfall | None = None,
        options: StackOptions | None = None,
    ):
        self.h = h
        self.ark = ark
        self.sf = sf
        self.options = options or AUTO

    @property
    def gw(self) -> Any:
        return self.h.app.state.gateway


@dataclasses.dataclass(frozen=True)
class StackOptions:
    """How a stack is built.

    ``settings``: passed to make_settings as they are (plus ``archidekt_base`` when there is an
    Archidekt); anything left out keeps the shipped default.
    ``archidekt``: makes the fake Archidekt the gateway's client talks to; None for no client.
    ``mystic_forge``: makes the fake Mystic Forge server behind the gateway's proxy; None for none.
    ``scryfall``: once started, the scan service asks a FakeScryfall of its own (``stack.sf``).
    ``started``: called with the stack once the gateway runs (and Scryfall is swapped)."""

    settings: Mapping[str, object] = dataclasses.field(
        default_factory=lambda: {"writes_enabled": True, "approval_mode_default": "auto"}
    )
    archidekt: Callable[[], FakeArchidekt] | None = FakeArchidekt
    mystic_forge: Callable[[], MCPServer] | None = None
    scryfall: bool = False
    started: Callable[[Stack], None] | None = None

    def but(self, **changes: Any) -> StackOptions:
        return dataclasses.replace(self, **changes)


ARK_BASE = "https://ark.test/api"
# Writes on, applies allowed over MCP; no Mystic Forge.
AUTO = StackOptions()
# The same with the fake Mystic Forge behind the proxy (test_decks_and_proxy).
AUTO_PROXY = AUTO.but(mystic_forge=fake_mystic_forge)
# Writes on with the shipped approval mode, the fake Mystic Forge, and a fake Scryfall to inspect
# (test_browse_collection).
COLLECTION = StackOptions(settings={"writes_enabled": True}, mystic_forge=fake_mystic_forge, scryfall=True)


def build_stack(tmp_path: Path, idp: FakeIdP, options: StackOptions = AUTO) -> Stack:
    """The stack's gateway built but not yet running (a test can set up its database first)."""
    ark = options.archidekt() if options.archidekt else None
    over = {"archidekt_base": ARK_BASE} if ark is not None else {}
    settings = make_settings(tmp_path, **over, **options.settings)
    kw: dict[str, object] = {}
    if ark is not None:
        client = ArchidektClient(settings.archidekt_base, "test-agent", Pacer(0.0), http=ark.client())
        kw["archidekt"] = client
    if options.mystic_forge is not None:
        server = options.mystic_forge
        kw["mf_proxy"] = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(server()))
    return Stack(Harness(settings, idp, **kw), ark, options=options)


@contextlib.asynccontextmanager
async def run_stack(st: Stack):
    """Run a built stack for the length of the block."""
    async with running(st.h):
        if st.options.scryfall:
            st.sf = FakeScryfall()
            st.gw.scan.scryfall = scan_scryfall.ScryfallClient(
                "https://scryfall.test", min_interval=0.0, http=st.sf.client()
            )
        if st.options.started is not None:
            st.options.started(st)
        yield st


@contextlib.asynccontextmanager
async def open_stack(tmp_path: Path, idp: FakeIdP, options: StackOptions = AUTO):
    async with run_stack(build_stack(tmp_path, idp, options)) as st:
        yield st


def stack_with(options: StackOptions):
    """A module's ``stack_options`` fixture: ``stack_options = stack_with(COLLECTION)``."""

    @pytest.fixture
    def stack_options() -> StackOptions:
        return options

    return stack_options


@pytest.fixture
def stack_options() -> StackOptions:
    return AUTO


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP, stack_options: StackOptions):
    async with open_stack(tmp_path, idp, stack_options) as st:
        yield st
