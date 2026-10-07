"""Security round 4 (web): pending sign-ins survive an anonymous flood, the client metadata
document cache and fetcher are bounded, deeply nested JSON is a 400, and every response carries
HSTS and a CSP that forbids framing."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mtg_gateway import cimd as cimdmod
from mtg_gateway import db as dbmod
from mtg_gateway.auth_provider import LoginStartLimiter, login_source
from mtg_gateway.cimd import CimdError, CimdThrottled, is_cimd_client_id, validate_document
from mtg_gateway.oidc import pkce_pair
from mtg_gateway.theme import NoSniffMiddleware, render
from tests.conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from tests.test_admin import _with_admin, admin_browser
from tests.test_cimd import CLIENT_URL, DocHost, document, start
from tests.test_decks_and_proxy import Browser

# Public (globally routable) addresses: only those are rate-limited (documentation ranges are not).
VICTIM_IP = "5.6.8.10"
ATTACKER_IPS = [f"5.6.7.{i}" for i in range(1, 5)]


def _from(h: Harness, ip: str) -> httpx.AsyncClient:
    """A browser (own cookie jar) whose requests arrive from ``ip``."""
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app, client=(ip, 40000)), base_url=GATEWAY)


def _authorize_params(client_id: str) -> dict[str, str]:
    _verifier, challenge = pkce_pair()
    return {
        "client_id": client_id,
        "redirect_uri": "https://client.test/cb",
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "cs1",
    }


def _count(h: Harness, table: str) -> int:
    with h.db._lock:
        return h.db._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _no_rate_limit(h: Harness) -> None:
    """Switch the per-network rate limit off, to test what the table caps do on their own."""
    h.app.state.gateway.provider.login_limiter = LoginStartLimiter(per_minute=10**9)


async def _finish_browser_login(h: Harness, browser: httpx.AsyncClient, started: httpx.Response) -> None:
    cb = urlparse(await h.idp_leg(started))
    done = await browser.get(f"{cb.path}?{cb.query}")
    assert done.status_code == 302, done.text
    assert "mtg_session=" in done.headers.get("set-cookie", "")


async def _finish_mcp_login(h: Harness, browser: httpx.AsyncClient, started: httpx.Response) -> None:
    """Approve the consent page and come back from the IdP, all in ``browser``."""
    loc = started.headers["location"]
    assert loc.startswith(f"{GATEWAY}/authorize/confirm?"), loc
    login_id = parse_qs(urlparse(loc).query)["state"][0]
    page = await browser.get("/authorize/confirm", params={"state": login_id})
    assert page.status_code == 200, page.text
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page.text).group(1)  # type: ignore[union-attr]
    go = await browser.post("/authorize/confirm", data={"state": login_id, "csrf": csrf, "action": "approve"})
    assert go.status_code == 303, go.text
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=h.idp.app)) as c:
        r = await c.get(go.headers["location"])
    cb = urlparse(r.headers["location"])
    done = await browser.get(f"{cb.path}?{cb.query}")
    assert done.status_code == 302, done.text
    q = parse_qs(urlparse(done.headers["location"]).query)
    assert done.headers["location"].startswith("https://client.test/cb") and q.get("code"), done.headers


# -- C-1: a flood of anonymous sign-in starts cannot push out other people's pending logins -----


async def test_flood_from_other_networks_does_not_evict_pending_sign_ins(
    gw: Harness, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(dbmod, "MAX_LOGIN_SESSIONS", 60)
    monkeypatch.setattr(dbmod, "MAX_LOGIN_SESSIONS_PER_CLIENT", 40, raising=False)
    _no_rate_limit(gw)
    member_client = await gw.register()
    attacker_client = await gw.register()
    victim = _from(gw, VICTIM_IP)
    # The victim starts a browser sign-in and an AI-client sign-in, then goes to the IdP.
    page_login = await victim.get("/login", params={"next": "/account"})
    assert page_login.status_code == 302
    mcp_login = await victim.get("/authorize", params=_authorize_params(member_client["client_id"]))
    assert mcp_login.status_code == 302
    # Meanwhile: cookie-less /login and /authorize with the attacker's own client and with the
    # member's client, from several addresses, far past every cap.
    for ip in ATTACKER_IPS:
        async with _from(gw, ip) as a:
            for i in range(60):
                a.cookies.clear()
                assert (await a.get("/login")).status_code == 302
                a.cookies.clear()
                cid = attacker_client["client_id"] if i % 2 else member_client["client_id"]
                assert (await a.get("/authorize", params=_authorize_params(cid))).status_code == 302
    assert _count(gw, "login_sessions") <= 60
    await _finish_browser_login(gw, victim, page_login)
    await _finish_mcp_login(gw, victim, mcp_login)
    await victim.aclose()


async def test_flood_with_one_client_only_evicts_that_clients_logins(
    gw: Harness, monkeypatch: pytest.MonkeyPatch
):
    # Everyone arrives from one address here (a proxy the gateway does not trust): the per-client
    # cap still keeps an attacker's own client from pushing out the web sign-in.
    monkeypatch.setattr(dbmod, "MAX_LOGIN_SESSIONS", 60)
    monkeypatch.setattr(dbmod, "MAX_LOGIN_SESSIONS_PER_CLIENT", 20, raising=False)
    _no_rate_limit(gw)
    victim = Browser(gw)
    started = await victim.http.get("/login", params={"next": "/account"})
    attacker_client = await gw.register()
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url=GATEWAY) as a:
        for _ in range(100):
            a.cookies.clear()
            r = await a.get("/authorize", params=_authorize_params(attacker_client["client_id"]))
            assert r.status_code == 302
    with gw.db._lock:
        per_client = gw.db._conn.execute(
            "SELECT COUNT(*) FROM login_sessions WHERE client_id = ?", (attacker_client["client_id"],)
        ).fetchone()[0]
    assert per_client == 20
    await _finish_browser_login(gw, victim.http, started)
    await victim.aclose()


async def test_one_browser_keeps_only_its_newest_pending_logins(gw: Harness):
    other = Browser(gw)
    started = await other.http.get("/login", params={"next": "/account"})
    b = Browser(gw)
    for _ in range(15):
        assert (await b.http.get("/login")).status_code == 302
    assert _count(gw, "login_sessions") == dbmod.MAX_LOGIN_SESSIONS_PER_BROWSER + 1
    await _finish_browser_login(gw, other.http, started)
    await other.aclose()
    await b.aclose()


async def test_sign_in_starts_are_rate_limited_per_network(gw: Harness):
    gw.app.state.gateway.provider.login_limiter = LoginStartLimiter(per_minute=5)
    client = await gw.register()
    async with _from(gw, ATTACKER_IPS[0]) as a:
        for _ in range(5):
            assert (await a.get("/login")).status_code == 302
        r = await a.get("/login")
        assert r.status_code == 429 and "Too many sign-in attempts" in r.text
        before = _count(gw, "login_sessions")
        r = await a.get("/authorize", params=_authorize_params(client["client_id"]))
        assert r.status_code == 400 and "temporarily_unavailable" in r.text
        assert _count(gw, "login_sessions") == before
    # Another address, and requests straight from a trusted proxy address, are not affected.
    async with _from(gw, VICTIM_IP) as v:
        assert (await v.get("/login")).status_code == 302
    for _ in range(10):
        assert (await gw.http.get("/login")).status_code == 302  # 127.0.0.1


def test_login_source_groups_ipv6_by_64_and_limiter_refills():
    assert login_source("2001:db8:1:2::5") == login_source("2001:db8:1:2:ffff::1") == "2001:db8:1:2::/64"
    assert login_source("2001:db8:1:3::5") != login_source("2001:db8:1:2::5")
    assert login_source("::ffff:198.51.100.7") == "198.51.100.7"
    assert login_source("testclient") is None and login_source(None) is None
    lim = LoginStartLimiter(per_minute=2)
    assert lim.allow("a", 0) and lim.allow("a", 0) and not lim.allow("a", 0)
    assert lim.allow("b", 0)
    assert lim.allow("a", 30.0)  # one token back after half a minute


def test_trim_prefers_the_busiest_network(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dbmod, "MAX_LOGIN_SESSIONS", 5)
    d = dbmod.Database(tmp_path / "g.sqlite")
    common = {"params": {}, "oidc_nonce": "n", "oidc_code_verifier": "v"}
    d.create_login_session("victim", client_id="c", ttl=60, source_hash="v", **common)
    for i in range(20):
        d.create_login_session(f"a{i}", client_id="c", ttl=600, source_hash="attacker", **common)
    assert d.get_login_session_exists("victim")
    assert _db_count(d) == 5
    d.purge_expired()
    assert d.get_login_session_exists("victim")
    d.close()


def _db_count(d: dbmod.Database, table: str = "login_sessions") -> int:
    with d._lock:
        return d._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


# -- C-2: the client metadata document cache is bounded ---------------------------------------


def test_metadata_document_record_is_bounded():
    with pytest.raises(CimdError, match="longer than"):
        validate_document(CLIENT_URL, document(redirect_uris=["https://client.example/" + "a" * 2100]))
    with pytest.raises(CimdError, match="at most"):
        validate_document(
            CLIENT_URL, document(redirect_uris=[f"https://client.example/{i}" for i in range(21)])
        )
    pad = "a" * 1900
    with pytest.raises(CimdError, match="too large"):
        validate_document(
            CLIENT_URL, document(redirect_uris=[f"https://client.example/{i}/{pad}" for i in range(20)])
        )
    assert validate_document(CLIENT_URL, document())["client_name"] == "Example Assistant"


async def cimd_sign_in(gw: Harness) -> None:
    """A member completes a sign-in through the metadata-document client CLIENT_URL."""
    r, _ = await start(gw)
    assert r.status_code == 302, r.text
    done = await gw.callback(await gw.idp_leg(r))
    assert done.status_code == 302 and "code=" in done.headers["location"], done.text


async def test_cimd_cache_is_capped_per_site_and_in_all(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(dbmod, "MAX_CIMD_CLIENTS", 12, raising=False)
    monkeypatch.setattr(dbmod, "MAX_CIMD_CLIENTS_PER_SITE", 5, raising=False)
    monkeypatch.setattr(cimdmod, "SITE_FETCHES_PER_MINUTE", 1000, raising=False)
    monkeypatch.setattr(cimdmod, "GLOBAL_FETCHES_PER_MINUTE", 1000, raising=False)
    docs = DocHost()
    docs.serve()
    async with running(Harness(make_settings(tmp_path), idp, cimd=docs.fetcher())) as gw:
        await cimd_sign_in(gw)  # a real client signs in once
        sites = ("attacker.example", "attacker2.example", "attacker3.example")
        for n, site in enumerate(sites):
            for i in range(30):
                host = f"h{i}.{site}"  # every document on its own subdomain
                docs.addresses[host] = [f"93.184.216.{50 + n}"]
                url = f"https://{host}/c.json"
                docs.serve(url, body=document(client_id=url, redirect_uris=[f"https://{host}/cb"]))
                r, _ = await start(gw, client_id=url, redirect=f"https://{host}/cb")
                assert r.status_code == 302, r.text
        with gw.db._lock:
            rows = [r[0] for r in gw.db._conn.execute("SELECT client_id FROM cimd_clients")]
        assert len(rows) <= 12 + 1  # the signed-in client is kept on top of the cap
        assert CLIENT_URL in rows  # never pushed out
        for site in sites:
            assert sum(1 for u in rows if (urlparse(u).hostname or "").endswith(site)) <= 5
        gw.db.purge_expired()
        assert gw.db.cimd_client_known(CLIENT_URL)


# -- C-3: the metadata fetcher is not a relay for GETs to other sites --------------------------


def test_client_id_with_query_string_is_refused():
    assert not is_cimd_client_id("https://victim.example/api/search?q=1")
    assert not is_cimd_client_id("https://victim.example/api/search?")
    assert is_cimd_client_id("https://victim.example/api/search")


async def test_failed_host_is_not_fetched_again_through_other_paths():
    docs = DocHost()
    docs.addresses["victim.example"] = ["93.184.216.40"]
    f = docs.fetcher()
    for i in range(20):
        with pytest.raises(CimdError):
            await f.fetch(f"https://victim.example/api/expensive/{i}")
    assert len([u for u in docs.requests if "victim.example" in u]) == 1
    # A client the gateway accepted before on that host is still fetched.
    good = "https://victim.example/oauth/client.json"
    docs.serve(good, body=document(client_id=good, redirect_uris=["https://victim.example/cb"]))
    with pytest.raises(CimdThrottled):
        await f.fetch(good)
    info, _ttl = await f.fetch(good, known=True)
    assert info["client_id"] == good
    await f.aclose()


async def test_new_metadata_urls_are_rate_limited_per_site(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cimdmod, "SITE_FETCHES_PER_MINUTE", 3, raising=False)
    docs = DocHost()
    f = docs.fetcher()
    for i in range(6):
        url = f"https://client.example/c/{i}.json"
        docs.serve(url, body=document(client_id=url))
    for i in range(3):
        await f.fetch(f"https://client.example/c/{i}.json")
    with pytest.raises(CimdThrottled, match="too many"):
        await f.fetch("https://client.example/c/3.json")
    await f.fetch("https://client.example/c/4.json", known=True)  # accepted before: not limited
    assert len(docs.requests) == 4
    await f.aclose()


async def test_relay_through_authorize_is_cut_off(tmp_path: Path, idp: FakeIdP):
    docs = DocHost()
    docs.addresses["victim.example"] = ["93.184.216.40"]
    async with running(Harness(make_settings(tmp_path), idp, cimd=docs.fetcher())) as gw:
        for i in range(30):
            r, _ = await start(gw, client_id=f"https://victim.example/api/expensive/search{i}?q={i}")
            assert r.status_code == 400
            r, _ = await start(gw, client_id=f"https://victim.example/api/expensive/{i}")
            assert r.status_code == 400
        assert len([u for u in docs.requests if "victim.example" in u]) <= 1
        # A real client on another host still signs in.
        docs.serve()
        r, _ = await start(gw)
        assert r.status_code == 302


async def test_known_client_still_signs_in_while_its_host_is_blocked(tmp_path: Path, idp: FakeIdP):
    docs = DocHost()
    docs.serve(headers={"cache-control": "max-age=300"})
    async with running(Harness(make_settings(tmp_path), idp, cimd=docs.fetcher())) as gw:
        await cimd_sign_in(gw)
        # Junk addresses on the client's host block new ones there for a minute...
        r, _ = await start(gw, client_id="https://client.example/junk")
        assert r.status_code == 400
        # ...but the cached document expiring does not lock the real client out.
        with gw.db._lock:
            gw.db._conn.execute("UPDATE cimd_clients SET expires_at = 1")
        r, _ = await start(gw)
        assert r.status_code == 302, r.text


# -- C-4: deeply nested JSON bodies are a 400, not a 500 ---------------------------------------


async def test_deeply_nested_json_is_a_client_error(tmp_path: Path, idp: FakeIdP):
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
    async with running(_with_admin(Harness(settings, idp))) as gw:
        b = await admin_browser(gw)
        page = await b.http.get("/scan")
        csrf = re.search(r'"csrf": "([0-9a-f]+)"', page.text).group(1)  # type: ignore[union-attr]
        b.http._transport = httpx.ASGITransport(app=gw.app, raise_app_exceptions=False)
        headers = {"content-type": "application/json", "x-csrf-token": csrf}
        deep = "[" * 15_000  # past the json module's recursion limit, under every body cap
        for path in (
            "/scan/api/sessions",
            "/scan/api/resolve",
            "/api/v1/proposals",
            "/api/v1/admin/users/user-2",
        ):
            r = await b.http.post(path, content=deep, headers=headers)
            assert r.status_code == 400, (path, r.status_code, r.text[:200])
            assert r.json()["error"] == "invalid", path
        await b.aclose()


async def test_deeply_nested_metadata_document_is_rejected():
    docs = DocHost()
    docs.serve()
    docs.docs[CLIENT_URL] = httpx.Response(
        200, content=b"[" * 15_000, headers={"content-type": "application/json"}
    )
    f = docs.fetcher()
    with pytest.raises(CimdError, match="not valid JSON"):
        await f.fetch(CLIENT_URL)
    await f.aclose()


# -- C-5 / C-6: HSTS on every response, and no page can be framed -----------------------------


async def test_every_response_carries_hsts_on_https(gw: Harness):
    for path in ("/", "/healthz", "/.well-known/oauth-authorization-server", "/static/feedback.js", "/nope"):
        r = await gw.http.get(path)
        assert r.headers.get("strict-transport-security") == "max-age=31536000", path
    big = await gw.http.post(
        "/authorize/confirm",
        content=b"a" * (64 * 1024),
        headers={"content-type": "application/x-www-form-urlencoded"},
    )
    assert big.status_code == 413 and big.headers["strict-transport-security"] == "max-age=31536000"


async def test_no_hsts_without_https():
    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    for hsts in (False, True):
        transport = httpx.ASGITransport(app=NoSniffMiddleware(app, hsts=hsts))
        async with httpx.AsyncClient(transport=transport, base_url="http://x") as c:
            r = await c.get("/")
        assert r.headers["x-content-type-options"] == "nosniff"
        assert ("strict-transport-security" in r.headers) is hsts


def test_default_page_csp_forbids_framing_and_base_changes():
    csp = render("t", "b", site="s", form_action=("https://idp.test",)).headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp
    assert csp.endswith("form-action 'self' https://idp.test")


async def test_consent_and_scan_pages_forbid_framing(gw: Harness):
    client = await gw.register()
    r, _verifier = await gw.start_login(client)
    page = await gw.http.get(r.headers["location"])
    assert page.status_code == 200
    csp = page.headers["content-security-policy"]
    assert "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp
    sw = await gw.http.get("/sw.js")
    assert "frame-ancestors 'none'" in sw.text
