"""Fixtures for the end-to-end suite: the deployed stack, a scripted browser and an MCP client.

Requires ``tests/e2e/run.sh up`` to have run first (it writes ``.generated/``).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import ssl
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import httpx2
import pytest
from mcp import ClientSession
from mcp.client.auth import OAuthClientProvider, TokenStorage
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthToken,
)
from playwright.async_api import Page, async_playwright, expect
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

GEN = Path(os.environ.get("E2E_GEN_DIR") or Path(__file__).parent / ".generated")
PUBLIC_URL = os.environ.get("E2E_PUBLIC_URL", "https://mtg.e2e.test")
AUTH_URL = os.environ.get("E2E_AUTH_URL", "https://auth.e2e.test")
MCP_URL = f"{PUBLIC_URL}/mcp"
CHROMIUM = os.environ.get("E2E_CHROMIUM")  # executable override; the sandbox pins one
LOCAL_CALLBACK_PORT = 18765


@dataclass
class Env:
    issuer: str
    client_id: str
    required_group: str
    users: dict[str, dict]
    ca_file: str

    @property
    def ssl_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=self.ca_file)


@pytest.fixture(scope="session")
def env() -> Env:
    if not (GEN / "authentik.json").exists():
        pytest.skip("run tests/e2e/run.sh up first")
    d = json.loads((GEN / "authentik.json").read_text())
    return Env(d["issuer"], d["client_id"], d["required_group"], d["users"], str(GEN / "ca.crt"))


@pytest.fixture
def http(env: Env) -> httpx.Client:
    # trust_env=False: never route the test hostnames through an outbound proxy.
    with httpx.Client(
        base_url=PUBLIC_URL, verify=env.ssl_context, trust_env=False, follow_redirects=False
    ) as c:
        yield c


class MemoryStorage(TokenStorage):
    def __init__(self) -> None:
        self.tokens: OAuthToken | None = None
        self.client_info: OAuthClientInformationFull | None = None

    async def get_tokens(self) -> OAuthToken | None:
        return self.tokens

    async def set_tokens(self, tokens: OAuthToken) -> None:
        self.tokens = tokens

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        return self.client_info

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self.client_info = client_info


@dataclass
class LoginResult:
    """What the scripted browser saw while signing in."""

    authorize_url: str = ""
    final_url: str = ""
    page_text: str = ""
    callback: AuthorizationCodeResult | None = None
    error: dict[str, str] = field(default_factory=dict)


class _CallbackServer:
    """Loopback redirect target, like a desktop MCP client's local listener."""

    def __init__(self) -> None:
        self.result: asyncio.Future[dict[str, list[str]]] | None = None
        self.server: asyncio.AbstractServer | None = None

    @property
    def redirect_uri(self) -> str:
        return f"http://127.0.0.1:{LOCAL_CALLBACK_PORT}/callback"

    async def start(self) -> None:
        self.result = asyncio.get_running_loop().create_future()
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", LOCAL_CALLBACK_PORT)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        line = await reader.readline()
        while (await reader.readline()).strip():
            pass
        target = line.split()[1].decode() if len(line.split()) > 1 else "/"
        params = parse_qs(urlparse(target).query)
        body = b"<html><body><h1>e2e callback received</h1></body></html>"
        writer.write(
            b"HTTP/1.1 200 OK\r\nContent-Type: text/html\r\nContent-Length: %d\r\n\r\n" % len(body) + body
        )
        await writer.drain()
        writer.close()
        if self.result and not self.result.done():
            self.result.set_result(params)

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            await self.server.wait_closed()


SIGN_IN_LOG = (
    GEN / "sign-in-retries.log"
)  # printed by the CI workflow; empty when every login went first time


async def click_guarded(page: Page, selector: str) -> None:
    """Click a button behind the gateway's click guard (the consent page's Approve/Deny, the
    review page's Apply): it unlocks only after the page has been visible and focused for a
    moment and the pointer has moved on it, so move the pointer like a person, then click."""
    button = page.locator(selector).first
    await button.wait_for(state="visible", timeout=30_000)
    await page.mouse.move(5, 5)
    await page.mouse.move(60, 90, steps=5)
    await expect(button).to_be_enabled(timeout=10_000)
    await button.click()


async def approve_gateway_consent(page: Page) -> None:
    """Every MCP client first lands on the gateway's consent page; press Approve there and wait
    until the browser has left for Authentik."""
    await page.wait_for_url(lambda u: u.startswith(f"{PUBLIC_URL}/authorize/confirm"), timeout=30_000)
    await click_guarded(page, "button[name=action][value=approve]")
    await page.wait_for_url(lambda u: not u.startswith(PUBLIC_URL), timeout=30_000)


async def authentik_sign_in(page: Page, username: str, password: str) -> None:
    """Fill Authentik's default identification + password stages.

    Each stage is a web component that attaches its listeners a moment after the
    field renders, so wait for the page to settle before typing.

    Authentik has, in CI, refused a correct password it had accepted for the same
    user seconds earlier (PR #27 and PR #32 e2e runs, once each). Until that is
    understood the password is offered up to three times, and every refusal is
    recorded with the flow executor's own answer so the next occurrence can be read.
    """
    executor: list[dict] = []  # the flow executor's answers to this page's POSTs

    async def record(resp) -> None:
        if "/api/v3/flows/executor/" in resp.url and resp.request.method == "POST":
            body = None
            try:
                body = await resp.json()
            except Exception:  # redirects and HTML error pages carry no JSON
                pass
            executor.append({"status": resp.status, "body": body})

    page.on("response", lambda resp: asyncio.ensure_future(record(resp)))

    async def stage(name: str, value: str) -> None:
        field = page.locator(f"input[name={name}]")
        await field.wait_for(state="visible", timeout=60_000)
        await page.wait_for_load_state("networkidle")
        await page.wait_for_timeout(500)
        for _ in range(5):
            await field.fill(value)
            # The web component can re-render after the fill and drop the value; an Enter then
            # posts an empty field ("This field is required"). Only press once the value stuck.
            if await field.input_value() == value:
                break
            await page.wait_for_timeout(250)
        await field.press("Enter")

    async def password_outcome(answers_before: int) -> str:
        """'done' when the password field is gone (redirect, or an error page), 'rejected' when
        Authentik answered the POST and marked the field invalid, 'timeout' otherwise."""
        field = page.locator("input[name=password]")
        for _ in range(120):
            if await field.count() == 0 or not await field.first.is_visible():
                return "done"
            new = [e.get("body") for e in executor[answers_before:] if isinstance(e.get("body"), dict)]
            if new and (new[-1].get("component") == "xak-flow-redirect" or new[-1].get("type") == "redirect"):
                return "done"  # accepted; the browser is on its way to the client
            # Only the newest answer counts: a stale aria-invalid from an earlier empty submit,
            # with an answer that carries no errors, is not a refusal.
            if new and new[-1].get("response_errors"):
                # get_attribute waits for the element; if the page moved on between the two
                # checks (a redirect, or the "access denied" page), the next count() says so.
                try:
                    invalid = await field.first.get_attribute("aria-invalid", timeout=1_000)
                except PlaywrightTimeoutError:
                    continue
                if invalid == "true":
                    return "rejected"
            await page.wait_for_timeout(250)
        return "timeout"

    await stage("uidField", username)
    for attempt in range(1, 4):
        if attempt > 1 and await page.locator("input[name=password]").count() == 0:
            return  # the previous attempt went through after all
        before = len(executor)
        await stage("password", password)
        outcome = await password_outcome(before)
        if outcome == "done":
            return
        answers = [
            e["body"].get("response_errors") for e in executor[before:] if isinstance(e.get("body"), dict)
        ]
        note = (
            f"{username}: Authentik {outcome} the password it was given (attempt {attempt}); "
            f"executor answers: {answers!r}"
        )
        print("warning:", note)
        try:
            with SIGN_IN_LOG.open("a", encoding="utf-8") as f:
                f.write(note + "\n")
        except OSError:
            pass
        if attempt == 3:
            raise AssertionError(note)


@dataclass
class McpClient:
    """An MCP client that signs in through a real browser, like Claude or ChatGPT would."""

    env: Env
    username: str
    storage: MemoryStorage = field(default_factory=MemoryStorage)
    login: LoginResult = field(default_factory=LoginResult)
    client_name: str = "e2e test client"

    def oauth(self, callback: _CallbackServer) -> OAuthClientProvider:
        password = self.env.users[self.username]["password"]

        async def redirect_handler(url: str) -> None:
            kwargs: dict = {"args": ["--no-proxy-server"]}
            if CHROMIUM:
                kwargs["executable_path"] = CHROMIUM
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(**kwargs)
                context = await browser.new_context(ignore_https_errors=True)
                page = await context.new_page()
                await page.goto(url)
                self.login.authorize_url = url
                await approve_gateway_consent(page)
                await authentik_sign_in(page, self.username, password)
                # Either the loopback callback page or an error page from Authentik/the gateway.
                try:
                    await page.wait_for_url(lambda u: u.startswith(callback.redirect_uri), timeout=20_000)
                except Exception:
                    pass
                await page.wait_for_load_state("domcontentloaded")
                self.login.final_url = page.url
                self.login.page_text = await page.inner_text("body")
                await browser.close()

        async def callback_handler() -> AuthorizationCodeResult:
            assert callback.result is not None
            try:
                params = await asyncio.wait_for(callback.result, timeout=5)
            except TimeoutError as exc:
                raise LoginNotCompleted(self.login) from exc
            if "error" in params:
                self.login.error = {k: v[0] for k, v in params.items()}
                raise LoginNotCompleted(self.login)
            self.login.callback = AuthorizationCodeResult(
                code=params["code"][0], state=params.get("state", [None])[0], iss=params.get("iss", [None])[0]
            )
            return self.login.callback

        return OAuthClientProvider(
            server_url=MCP_URL,
            client_metadata=OAuthClientMetadata(
                client_name=self.client_name,
                redirect_uris=[callback.redirect_uri],
                grant_types=["authorization_code", "refresh_token"],
                response_types=["code"],
                token_endpoint_auth_method="client_secret_post",
            ),
            storage=self.storage,
            redirect_handler=redirect_handler,
            callback_handler=callback_handler,
        )

    @contextlib.asynccontextmanager
    async def session(self) -> AsyncIterator[ClientSession]:
        """An initialised MCP session. The first one signs in through the browser; later ones reuse
        the tokens kept in ``storage``, as a real client would."""
        callback = _CallbackServer()
        await callback.start()
        try:
            async with httpx2.AsyncClient(
                auth=self.oauth(callback), verify=self.env.ssl_context, trust_env=False, timeout=600
            ) as hc:
                async with streamable_http_client(MCP_URL, http_client=hc) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
        except BaseException as exc:  # the SDK wraps auth failures in task groups
            if self.login.callback is None and self.login.final_url and self.storage.tokens is None:
                raise LoginNotCompleted(self.login) from exc
            raise
        finally:
            await callback.stop()

    async def call(self, session: ClientSession, name: str, arguments: dict | None = None) -> dict:
        """Call a tool and return its JSON result (tool errors come back as {"ok": False, ...})."""
        result = await session.call_tool(name, arguments or {})
        text = result.content[0].text if result.content else ""
        if result.is_error:
            return {"ok": False, "error": "tool_error", "message": text}
        return json.loads(text)

    async def whoami(self) -> dict:
        """Full client flow: discovery, registration, browser login, token, initialize, tools/list, whoami."""
        callback = _CallbackServer()
        await callback.start()
        try:
            return await self._whoami(callback)
        except BaseException as exc:  # the SDK wraps auth failures in task groups
            if self.login.callback is None and self.login.final_url:
                raise LoginNotCompleted(self.login) from exc
            raise
        finally:
            await callback.stop()

    async def _whoami(self, callback: _CallbackServer) -> dict:
        if True:
            async with httpx2.AsyncClient(
                auth=self.oauth(callback), verify=self.env.ssl_context, trust_env=False, timeout=60
            ) as hc:
                async with streamable_http_client(MCP_URL, http_client=hc) as (read, write):
                    async with ClientSession(read, write) as session:
                        init = await session.initialize()
                        assert init.server_info.name == "mtg-gateway", init
                        tools = await session.list_tools()
                        assert "whoami" in [t.name for t in tools.tools]
                        result = await session.call_tool("whoami", {})
                        assert not result.is_error, result
                        return json.loads(result.content[0].text)


class LoginNotCompleted(Exception):
    def __init__(self, login: LoginResult):
        super().__init__(
            f"login did not reach the client callback; browser ended at {login.final_url!r} "
            f"showing: {login.page_text[:300]!r}"
        )
        self.login = login


async def gateway_browser_sign_in(page: Page, env: Env, username: str, next_path: str = "/account") -> None:
    """Sign in to the gateway's own pages (account, proposals) through Authentik, like a person would."""
    await page.goto(f"{PUBLIC_URL}/login?next={next_path}")
    await authentik_sign_in(page, username, env.users[username]["password"])
    await page.wait_for_url(lambda u: u.startswith(PUBLIC_URL + next_path), timeout=30_000)
    await page.wait_for_load_state("domcontentloaded")


@contextlib.asynccontextmanager
async def browser_page() -> AsyncIterator[Page]:
    kwargs: dict = {"args": ["--no-proxy-server"]}
    if CHROMIUM:
        kwargs["executable_path"] = CHROMIUM
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(**kwargs)
        context = await browser.new_context(ignore_https_errors=True)
        try:
            yield await context.new_page()
        finally:
            await browser.close()


@pytest.fixture
def mcp_client(env: Env):
    def make(username: str, **kw) -> McpClient:
        return McpClient(env, username, **kw)

    return make
