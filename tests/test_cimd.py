"""Client ID Metadata Documents: URL-shaped client ids fetched under SSRF guards."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mtg_gateway.cimd import (
    MAX_CONCURRENT_FETCHES,
    CimdError,
    CimdFetcher,
    CimdThrottled,
    check_redirect_uri,
    is_cimd_client_id,
    validate_document,
)
from mtg_gateway.oidc import pkce_pair
from tests.conftest import FakeIdP, Harness, make_settings, running, sse_json

CLIENT_URL = "https://client.example/oauth/client.json"
REDIRECT = "https://client.example/cb"


def document(**over: object) -> dict:
    doc = {
        "client_id": CLIENT_URL,
        "client_name": "Example Assistant",
        "redirect_uris": [REDIRECT, "http://127.0.0.1:3000/cb"],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": "mtg",
    }
    doc.update(over)
    return doc


class DocHost:
    """A fake metadata host behind httpx.MockTransport; records every request."""

    def __init__(self) -> None:
        self.docs: dict[str, httpx.Response] = {}
        self.requests: list[str] = []
        self.addresses: dict[str, list[str]] = {"client.example": ["93.184.216.34"]}

    def serve(self, url: str = CLIENT_URL, body: object | None = None, **resp: object) -> None:
        payload = json.dumps(body if body is not None else document()).encode()
        headers = {"content-type": "application/json", **resp.pop("headers", {})}  # type: ignore[arg-type]
        self.docs[url] = httpx.Response(int(resp.pop("status", 200)), content=payload, headers=headers)

    def handler(self, request: httpx.Request) -> httpx.Response:
        # The gateway connects to the checked IP and names the host in SNI and the Host header.
        host = request.headers["host"]
        assert request.url.host != host, "the connection must go to the validated address"
        assert request.extensions.get("sni_hostname") == host
        assert request.url.host in self.addresses.get(host, []), (request.url.host, host)
        url = f"https://{host}{request.url.path}"
        self.requests.append(url)
        return self.docs.get(url, httpx.Response(404))

    async def resolve(self, host: str) -> list[str]:
        if host not in self.addresses:
            raise OSError("no such host")
        return self.addresses[host]

    def fetcher(self, **kw: object) -> CimdFetcher:
        http = httpx.AsyncClient(transport=httpx.MockTransport(self.handler), follow_redirects=False)
        return CimdFetcher(http=http, resolver=self.resolve, **kw)  # type: ignore[arg-type]


@pytest.fixture
def docs() -> DocHost:
    return DocHost()


@pytest.fixture
async def gw(tmp_path: Path, idp: FakeIdP, docs: DocHost):
    async with running(Harness(make_settings(tmp_path), idp, cimd=docs.fetcher())) as h:
        yield h


async def start(
    h: Harness, client_id: str = CLIENT_URL, redirect: str = REDIRECT
) -> tuple[httpx.Response, str]:
    verifier, challenge = pkce_pair()
    r = await h.http.get(
        "/authorize",
        params={
            "client_id": client_id,
            "redirect_uri": redirect,
            "response_type": "code",
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": "cs1",
        },
    )
    return r, verifier


# -- unit level ---------------------------------------------------------------


def test_embedded_ipv4_forms_are_not_public():
    from mtg_gateway.cimd import _address_is_public

    for bad in (
        "::7f00:1",
        "::a00:1",
        "64:ff9b::a00:1",
        "64:ff9b:1::7f00:1",
        "::ffff:10.0.0.1",
        "2002:0a00:0001::1",
        "fe80::1",
        "fc00::1",
    ):
        assert not _address_is_public(bad), bad
    assert _address_is_public("64:ff9b::5db8:d822")  # NAT64 of a public address
    assert _address_is_public("2606:4700::6810:84e5")


def test_client_id_shape():
    assert is_cimd_client_id(CLIENT_URL)
    assert is_cimd_client_id("https://client.example:443/x")
    for bad in (
        "https://client.example",
        "https://client.example/",
        "http://client.example/client.json",
        "https://user:pw@client.example/client.json",
        "https://client.example:8443/client.json",
        "https://client.example/client.json#frag",
        "abc123",
    ):
        assert not is_cimd_client_id(bad), bad


def test_document_validation():
    rec = validate_document(CLIENT_URL, document())
    assert rec["token_endpoint_auth_method"] == "none" and rec["scope"] == "mtg"
    assert rec["redirect_uris"] == [REDIRECT, "http://127.0.0.1:3000/cb"]
    cases = {
        "client_id": "https://other.example/c.json",
        "client_name": "",
        "redirect_uris": ["javascript:alert(1)"],
        "token_endpoint_auth_method": "client_secret_post",
        "grant_types": ["implicit"],
    }
    for key, value in cases.items():
        with pytest.raises(CimdError):
            validate_document(CLIENT_URL, document(**{key: value}))
    with pytest.raises(CimdError):
        validate_document(CLIENT_URL, ["not", "an", "object"])


async def test_fetch_guards(docs: DocHost):
    f = docs.fetcher()
    docs.serve()
    info, ttl = await f.fetch(CLIENT_URL)
    assert info["client_name"] == "Example Assistant" and ttl == 3600

    # Private, loopback and link-local answers are never fetched.
    for addr in (
        ["10.0.0.5"],
        ["127.0.0.1"],
        ["169.254.1.1"],
        ["::1"],
        ["::ffff:192.168.1.1"],
        ["93.184.216.34", "10.1.1.1"],
    ):
        docs.addresses["client.example"] = addr
        docs.requests.clear()
        with pytest.raises(CimdError, match="public address"):
            await f._fetch(CLIENT_URL)
        assert docs.requests == []
    docs.addresses["client.example"] = ["93.184.216.34"]

    docs.serve(status=302, headers={"location": "https://elsewhere.example/x"})
    with pytest.raises(CimdError, match="HTTP 302"):
        await f._fetch(CLIENT_URL)
    docs.serve(headers={"content-type": "text/html"})
    with pytest.raises(CimdError, match="not JSON"):
        await f._fetch(CLIENT_URL)
    docs.serve(body=document(client_name="x" * 70_000))
    with pytest.raises(CimdError, match="too large"):
        await f._fetch(CLIENT_URL)
    docs.serve(headers={"cache-control": "max-age=5"})
    assert (await f._fetch(CLIENT_URL))[1] == 300  # clamped to the floor
    with pytest.raises(CimdError, match="IP address"):
        await f._fetch("https://93.184.216.34/c.json")
    with pytest.raises(CimdError, match="resolve"):
        await f._fetch("https://unknown.example/c.json")

    # A failure is remembered for a minute so a hostile client cannot make us hammer a host.
    docs.serve(status=500)
    with pytest.raises(CimdError, match="HTTP 500"):
        await f.fetch(CLIENT_URL)
    docs.serve()
    with pytest.raises(CimdError, match="recently"):
        await f.fetch(CLIENT_URL)

    restricted = docs.fetcher(allowed_hosts=["anthropic.com"])
    with pytest.raises(CimdError, match="allowlist"):
        await restricted._fetch(CLIENT_URL)
    await f.aclose()
    await restricted.aclose()


# -- through the gateway -----------------------------------------------------


async def test_metadata_advertises_cimd(gw: Harness):
    m = (await gw.http.get("/.well-known/oauth-authorization-server")).json()
    assert m["client_id_metadata_document_supported"] is True
    assert m["issuer"] == "https://mtg.test" and m["registration_endpoint"] == "https://mtg.test/register"


async def test_cimd_login_end_to_end(gw: Harness, docs: DocHost):
    docs.serve()
    r, verifier = await start(gw)
    assert r.status_code == 302, r.text
    loc = r.headers["location"]
    assert loc.startswith("https://mtg.test/authorize/confirm?state=")
    assert "mtg_login=" in r.headers.get("set-cookie", "")  # browser binding set as for any login
    login_id = parse_qs(urlparse(loc).query)["state"][0]

    page = await gw.http.get("/authorize/confirm", params={"state": login_id})
    assert page.status_code == 200
    assert "Example Assistant" in page.text and "client.example" in page.text
    assert "Warning" not in page.text  # https redirect, no localhost warning
    # Browsers apply form-action to the redirect after the POST: both destinations must be allowed.
    csp = page.headers["content-security-policy"]
    assert "form-action 'self' https://idp.test https://client.example" in csp

    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page.text).group(1)
    # Both buttons need the form token bound to this login.
    assert (await gw.http.post("/authorize/confirm", data={"state": login_id})).status_code == 403
    assert (
        await gw.http.post("/authorize/confirm", data={"state": login_id, "csrf": "x"})
    ).status_code == 403
    go = await gw.http.post("/authorize/confirm", data={"state": login_id, "csrf": csrf, "action": "approve"})
    assert go.status_code == 303, go.text
    assert go.headers["location"].startswith("https://idp.test/")
    cb = await gw.idp_leg(go)
    done = await gw.callback(cb)
    assert done.status_code == 302, done.text
    q = parse_qs(urlparse(done.headers["location"]).query)
    assert q["state"] == ["cs1"]

    # Public client: no secret at the token endpoint.
    t = await gw.http.post(
        "/token",
        data={
            "grant_type": "authorization_code",
            "client_id": CLIENT_URL,
            "code": q["code"][0],
            "code_verifier": verifier,
            "redirect_uri": REDIRECT,
        },
    )
    assert t.status_code == 200, t.text
    tokens = t.json()
    assert tokens["scope"] == "mtg"
    r = await gw.mcp(tokens["access_token"], "tools/call", {"name": "whoami", "arguments": {}}, rid=2)
    assert sse_json(r)["result"]["structuredContent"]["signed_in"] is True
    assert gw.db.get_token(tokens["access_token"], "access")["client_id"] == CLIENT_URL

    # Refresh works for the public client, and the document was fetched once (cached after).
    rt = await gw.http.post(
        "/token",
        data={
            "grant_type": "refresh_token",
            "client_id": CLIENT_URL,
            "refresh_token": tokens["refresh_token"],
        },
    )
    assert rt.status_code == 200, rt.text
    r2, _ = await start(gw)
    assert r2.status_code == 302
    assert docs.requests.count(CLIENT_URL) == 1
    assert gw.db.get_cimd_client(CLIENT_URL)["client_name"] == "Example Assistant"


async def test_cimd_localhost_redirect_shows_warning(gw: Harness, docs: DocHost):
    docs.serve()
    r, _ = await start(gw, redirect="http://127.0.0.1:3000/cb")
    assert r.status_code == 302
    login_id = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    page = await gw.http.get("/authorize/confirm", params={"state": login_id})
    assert "Warning" in page.text and "127.0.0.1" in page.text
    assert (
        "form-action 'self' https://idp.test http://127.0.0.1:3000" in page.headers["content-security-policy"]
    )


async def test_cimd_rejections(gw: Harness, docs: DocHost):
    # Redirect URI not in the document.
    docs.serve()
    r, _ = await start(gw, redirect="https://attacker.example/cb")
    assert r.status_code == 400, r.text
    # Document whose client_id does not match its URL: client unknown.
    other = "https://client.example/other.json"
    docs.serve(other, body=document())
    r, _ = await start(gw, client_id=other)
    assert r.status_code == 400 and "not found" in r.text.lower()
    assert gw.db.get_cimd_client(other) is None
    # Hosts that resolve privately are never contacted.
    private = "https://intranet.example/c.json"
    docs.addresses["intranet.example"] = ["10.0.0.9"]
    docs.serve(private, body=document(client_id=private))
    r, _ = await start(gw, client_id=private)
    assert r.status_code == 400
    assert private not in docs.requests
    # An http URL is not a metadata document id at all.
    r, _ = await start(gw, client_id="http://client.example/c.json")
    assert r.status_code == 400
    # Confirmation page refuses unknown or expired logins.
    assert (await gw.http.get("/authorize/confirm", params={"state": "nope"})).status_code == 400
    assert (
        await gw.http.post("/authorize/confirm", data={"state": "nope"})
    ).status_code == 403  # no form token


async def test_cimd_can_be_disabled(tmp_path: Path, idp: FakeIdP, docs: DocHost):
    docs.serve()
    async with running(Harness(make_settings(tmp_path, cimd_enabled=False), idp, cimd=docs.fetcher())) as h:
        m = (await h.http.get("/.well-known/oauth-authorization-server")).json()
        assert m.get("client_id_metadata_document_supported") in (None, False)
        r, _ = await start(h)
        assert r.status_code == 400 and docs.requests == []


async def test_slow_drip_host_hits_one_overall_deadline(docs: DocHost):
    """A host that answers quickly then drips bytes must not hold a fetch slot past the deadline."""
    import asyncio
    import time

    async def drip(scope, receive, send):  # minimal ASGI app: headers at once, then a byte at a time
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        for _ in range(1000):
            await send({"type": "http.response.body", "body": b"{", "more_body": True})
            await asyncio.sleep(0.1)
        await send({"type": "http.response.body", "body": b"}", "more_body": False})

    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=drip), follow_redirects=False)
    f = CimdFetcher(http=http, resolver=docs.resolve, timeout=0.5)  # type: ignore[arg-type]
    t = time.perf_counter()
    with pytest.raises(CimdError, match="timed out"):
        await f.fetch(CLIENT_URL)
    assert time.perf_counter() - t < 2.0
    # The slot is free again: a second caller is not stuck behind the dripper.
    assert f._gate._value == MAX_CONCURRENT_FETCHES  # noqa: SLF001
    assert f._site_locks == {}  # noqa: SLF001
    await f.aclose()


async def test_cimd_deny_invalidates_the_login(gw: Harness, docs: DocHost):
    docs.serve()
    r, _ = await start(gw)
    login_id = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    page = await gw.http.get("/authorize/confirm", params={"state": login_id})
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page.text).group(1)
    assert "value='deny'" in page.text

    deny = await gw.http.post("/authorize/confirm", data={"state": login_id, "csrf": csrf, "action": "deny"})
    assert deny.status_code == 303, deny.text
    back = urlparse(deny.headers["location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
    q = parse_qs(back.query)
    assert q["error"] == ["access_denied"] and q["state"] == ["cs1"] and "code" not in q

    # The pending login is gone: neither the page nor Approve work any more.
    assert (await gw.http.get("/authorize/confirm", params={"state": login_id})).status_code == 400
    again = await gw.http.post(
        "/authorize/confirm", data={"state": login_id, "csrf": csrf, "action": "approve"}
    )
    assert again.status_code == 400
    with gw.db.tx() as c:
        events = [r[0] for r in c.execute("SELECT event FROM audit_log ORDER BY at DESC LIMIT 20")]
    assert "login_denied" in events


def test_redirect_uri_hosts():
    assert check_redirect_uri("https://client.example/cb") is None
    assert check_redirect_uri("https://client.example:8443/cb") is None
    assert check_redirect_uri("http://127.0.0.1:3000/cb") is None
    assert check_redirect_uri("https://[2001:db8::1]/cb") is None
    for bad in (
        "https://*/cb",
        "https://*.example/cb",
        "https://exa mple/cb",
        "https://'self'/cb",
        "https://client.example:99999/cb",
        "https:///cb",
        "http://client.example/cb",
        "myapp://cb",
    ):
        assert check_redirect_uri(bad) is not None, bad


def test_malformed_authority_is_not_a_cimd_client_id():
    for bad in ("https://a.com:abc/x", "https://a.com:99999/x", "https://[::1/x"):
        assert not is_cimd_client_id(bad), bad


async def test_malformed_authority_client_id_is_a_client_error(gw: Harness, docs: DocHost):
    """A client_id whose port or IPv6 literal does not parse is an unknown client, not a 500."""
    for bad in ("https://a.com:abc/x", "https://[::1/x"):
        r, _ = await start(gw, client_id=bad)
        assert r.status_code == 400, (bad, r.status_code, r.text)
        t = await gw.http.post(
            "/token", data={"grant_type": "refresh_token", "client_id": bad, "refresh_token": "x"}
        )
        assert 400 <= t.status_code < 500, (bad, t.status_code, t.text)
    assert docs.requests == []


async def test_compressed_metadata_document_is_refused(docs: DocHost):
    """A gzip body would be inflated before the size cap sees it; ask for identity, refuse the rest."""
    import gzip

    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("accept-encoding"))
        bomb = gzip.compress(b" " * (8 * 1024 * 1024))  # 8 MiB of whitespace, about 8 KiB on the wire
        return httpx.Response(
            200, content=bomb, headers={"content-type": "application/json", "content-encoding": "gzip"}
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    f = CimdFetcher(http=http, resolver=docs.resolve)  # type: ignore[arg-type]
    with pytest.raises(CimdError, match="compressed"):
        await f.fetch(CLIENT_URL)
    assert seen == ["identity"]
    await f.aclose()


async def test_slow_hostile_hosts_cannot_lock_out_a_healthy_client():
    """Hanging metadata hosts may delay other sign-ins but never get a healthy client_id refused:
    each site gets one slot, a caller that never got a slot is told "busy" without that being
    remembered, and a timeout is not negative-cached."""
    import asyncio

    hang = {"evil.example"}
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        host = request.headers["host"]
        calls.append(host)
        if host in hang:
            await asyncio.sleep(30)
        url = f"https://{host}{request.url.path}"
        return httpx.Response(200, json=document(client_id=url), headers={"content-type": "application/json"})

    async def resolve(host: str) -> list[str]:
        return ["93.184.216.34"]

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    f = CimdFetcher(http=http, resolver=resolve, timeout=0.3)  # type: ignore[arg-type]

    # Many hanging fetches against one attacker site hold one slot; the healthy client still loads.
    attack = [asyncio.create_task(f.fetch(f"https://evil.example/c{i}")) for i in range(10)]
    await asyncio.sleep(0.05)
    info, _ = await f.fetch(CLIENT_URL)
    assert info["client_name"] == "Example Assistant"
    assert calls.count("evil.example") == 1
    await asyncio.gather(*attack, return_exceptions=True)

    # Every slot taken (by as many attacker sites): the healthy caller is told "busy", and nothing
    # is remembered against its URL, so it works as soon as a slot is free again.
    for _ in range(MAX_CONCURRENT_FETCHES):
        await f._gate.acquire()  # noqa: SLF001
    other = "https://other.example/client.json"
    with pytest.raises(CimdThrottled, match="busy"):
        await f.fetch(other)
    assert other not in f._failures  # noqa: SLF001
    for _ in range(MAX_CONCURRENT_FETCHES):
        f._gate.release()  # noqa: SLF001
    assert (await f.fetch(other))[0]["client_id"] == other

    # A timed-out fetch is not remembered either: once its host answers again, it works at once.
    with pytest.raises(CimdError, match="timed out"):
        await f.fetch("https://evil.example/again")
    hang.clear()
    assert (await f.fetch("https://evil.example/again"))[0]["client_name"] == "Example Assistant"
    assert f._site_locks == {} and f._gate._value == MAX_CONCURRENT_FETCHES  # noqa: SLF001
    await f.aclose()
