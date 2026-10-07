"""Test fixtures: a fake OpenID provider and a gateway wired to it in memory."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import re
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlencode, urlparse

import httpx
import pytest
from joserfc import jwt
from joserfc.jwk import OctKey, RSAKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, RedirectResponse, Response
from starlette.routing import Route

from mtg_gateway.app import create_app
from mtg_gateway.config import Settings
from mtg_gateway.db import Database
from mtg_gateway.oidc import OIDCClient

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
        # "HS256" signs the ID token with the client secret, as Authentik does when the provider
        # has no Signing Key.
        self.id_token_alg = "RS256"
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
        self.access_ttl: int | None = 300  # None: no expires_in in token responses
        # Provider shapes: keys userinfo leaves out (e.g. groups only in the ID token), whether a
        # refresh returns a new ID token, and an error code every refresh fails with.
        self.userinfo_omit: set[str] = set()
        self.refresh_id_token = False
        self.refresh_error: str | None = None
        self.app = Starlette(
            routes=[
                Route("/application/o/mtg/.well-known/openid-configuration", self.discovery),
                Route("/application/o/authorize/", self.authorize),
                Route("/application/o/token/", self.token, methods=["POST"]),
                Route("/application/o/mtg/jwks/", self.jwks),
                Route("/application/o/userinfo/", self.userinfo),
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
            **self.id_token_claims,
        }
        if self.id_token_alg == "HS256":
            id_token = jwt.encode({"alg": "HS256"}, claims, OctKey.import_key(CLIENT_SECRET))
        else:
            id_token = jwt.encode({"alg": "RS256", "kid": "test-1"}, claims, self.key)
        sub = str(self.user["sub"])
        self.directory[sub] = self.user
        body = self._mint(sub, offline="offline_access" in q.get("scope", "").split())
        return JSONResponse({**body, "id_token": id_token})

    def _mint(self, sub: str, *, offline: bool) -> dict[str, object]:
        access = f"idp-access-{secrets.token_urlsafe(8)}"
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
        if self.down:
            return JSONResponse({"error": "temporarily_unavailable"}, status_code=503)
        token = req.headers.get("authorization", "").removeprefix("Bearer ")
        sub = self.access_tokens.get(token)
        if sub is None or sub in self.disabled:
            return Response(status_code=401)
        info = self.directory.get(sub, self.user)
        return JSONResponse({k: v for k, v in info.items() if k not in self.userinfo_omit})

    def set_groups(self, sub: str, groups: list[str]) -> None:
        """Change a user's groups at the provider (as an admin would in Authentik)."""
        self.directory[sub]["groups"] = list(groups)


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
        apply_min_age_seconds=0,  # tests apply right after proposing; the guard has its own test
    )
    base.update(over)
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
            "/authorize/confirm", data={"state": login_id, "csrf": csrf, "action": "approve"}
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
