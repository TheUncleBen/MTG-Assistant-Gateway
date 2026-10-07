"""Security round 4, re-review: the metadata fetch budgets cannot be spent with junk client ids
and never apply to signed-in clients, the cache caps group by site and never evict
a signed-in client, the sign-in rate limit only applies to public addresses, and "Delete my data"
leaves other members' deck covers alone."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from pathlib import Path

import httpx
import pytest

from mtg_gateway import auth_provider as apmod
from mtg_gateway import cimd as cimdmod
from mtg_gateway import db as dbmod
from mtg_gateway.auth_provider import LoginStartLimiter
from mtg_gateway.cimd import CimdError, CimdThrottled, site_of
from mtg_gateway.db import Database
from tests.conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from tests.test_cimd import CLIENT_URL, DocHost, document

# -- RA-5: fetch budgets ----------------------------------------------------------------------


def test_site_groups_subdomains_by_registrable_domain():
    assert site_of("a.b.attacker.example") == site_of("attacker.example") == "attacker.example"
    assert site_of("h1.evil.co.uk") == site_of("evil.co.uk") == "evil.co.uk"
    assert site_of("x.shop.com.au") == "shop.com.au"
    assert site_of("claude.ai") == site_of("api.claude.ai") == "claude.ai"
    assert site_of("Example.COM.") == "example.com"


async def test_junk_client_ids_do_not_spend_the_fetch_budget():
    """Unresolvable hosts, private answers and IP literals are refused before anything is sent,
    so they use up nothing; a new real client on an open gateway is still fetched."""
    docs = DocHost()
    docs.serve()
    f = docs.fetcher()
    for i in range(cimdmod.GLOBAL_FETCHES_PER_MINUTE + 20):
        with pytest.raises(CimdError):
            await f.fetch(f"https://x.junk{i}.example/c")  # no such host
    docs.addresses["intranet.example"] = ["10.0.0.9"]
    for i in range(20):
        with pytest.raises(CimdError):
            await f.fetch(f"https://intranet.example/c{i}")
    info, _ = await f.fetch(CLIENT_URL)
    assert info["client_name"] == "Example Assistant"
    await f.aclose()


async def test_allowlisted_host_is_not_blocked_but_still_budgeted(monkeypatch: pytest.MonkeyPatch):
    """Round 3 (RB-2): an allowlist must not turn the gateway into an unlimited relay to the
    allowed host. A failure there blocks nothing, but new URLs still count against the budgets."""
    monkeypatch.setattr(cimdmod, "GLOBAL_FETCHES_PER_MINUTE", 100)
    monkeypatch.setattr(cimdmod, "SITE_FETCHES_PER_MINUTE", 3)

    async def nxdomain(host: str) -> list[str]:
        raise OSError("nxdomain")

    f = cimdmod.CimdFetcher(resolver=nxdomain, allowed_hosts=["client.example"])
    for i in range(100):
        with pytest.raises(CimdError):
            await f.fetch(f"https://x.junk{i}.example/c")  # not on the allowlist: refused locally
    await f.aclose()
    docs = DocHost()
    for i in range(5):
        url = f"https://client.example/c{i}.json"
        docs.serve(url, body=document(client_id=url))
    f = docs.fetcher(allowed_hosts=["client.example"])
    with pytest.raises(CimdError):
        await f.fetch("https://client.example/missing")  # a failure does not block the host
    for i in range(2):
        assert (await f.fetch(f"https://client.example/c{i}.json"))[0]["client_name"]
    with pytest.raises(CimdThrottled, match="too many"):
        await f.fetch("https://client.example/c2.json")
    assert len(docs.requests) == 3
    # A client a member signed in with is fetched whatever the budget says.
    assert (await f.fetch("https://client.example/c3.json", known=True))[0]["client_name"]
    await f.aclose()


class SlowDns(DocHost):
    async def resolve(self, host: str) -> list[str]:
        if host not in self.addresses:
            await asyncio.sleep(30)  # the attacker's DNS never answers in time
            raise OSError("timeout")
        return self.addresses[host]


@pytest.mark.parametrize("same_site", [True, False])
async def test_dns_that_never_answers_cannot_starve_a_signed_in_client(
    monkeypatch: pytest.MonkeyPatch, same_site: bool
):
    """Round 3 (RB-1): junk client ids whose DNS hangs used to hold every fetch slot (or the
    real client's site lock), so a signed-in client's refetch was told the fetcher was busy."""
    monkeypatch.setattr(cimdmod, "DNS_TIMEOUT", 0.2)
    docs = SlowDns()
    docs.serve()
    f = docs.fetcher(timeout=0.5)

    async def junk(i: int) -> None:
        host = f"j{i}.client.example" if same_site else f"j{i}.attacker{i % 20}.example"
        with contextlib.suppress(CimdError):
            await f.fetch(f"https://{host}/c")

    tasks: list[asyncio.Task] = []

    async def flood() -> None:
        i = 0
        while True:
            for _ in range(20):
                tasks.append(asyncio.create_task(junk(i)))
                i += 1
            await asyncio.sleep(0.05)

    fl = asyncio.create_task(flood())
    await asyncio.sleep(1.0)
    try:
        info, _ = await f.fetch(CLIENT_URL, known=True)
        assert info["client_name"] == "Example Assistant"
    finally:
        fl.cancel()
        for t in tasks:
            t.cancel()
        await asyncio.gather(fl, *tasks, return_exceptions=True)
        await f.aclose()


async def test_a_dns_timeout_is_remembered_against_the_host(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cimdmod, "DNS_TIMEOUT", 0.1)
    f = SlowDns().fetcher()
    with pytest.raises(CimdError, match="timed out"):
        await f.fetch("https://slow.attacker.example/a")
    with pytest.raises(CimdThrottled):
        await f.fetch("https://slow.attacker.example/b")  # refused without waiting on DNS again
    await f.aclose()


async def test_subdomain_flood_shares_one_site_budget(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cimdmod, "SITE_FETCHES_PER_MINUTE", 3)
    docs = DocHost()
    f = docs.fetcher()
    for i in range(4):
        host = f"h{i}.attacker.example"
        docs.addresses[host] = ["93.184.216.60"]
        docs.serve(f"https://{host}/c", body=document(client_id=f"https://{host}/c"))
    for i in range(3):
        await f.fetch(f"https://h{i}.attacker.example/c")
    with pytest.raises(CimdThrottled, match="too many"):
        await f.fetch("https://h3.attacker.example/c")
    await f.aclose()


def test_signed_in_client_is_never_evicted_and_only_it_is_known(tmp_path: Path):
    """The reviewer's case: 1000 documents on distinct subdomains used to push the real client's
    row out, so it became "new" again and subject to the budgets."""
    db = Database(tmp_path / "g.sqlite")
    info = {"client_id": "x", "redirect_uris": ["https://a/cb"], "client_name": "n"}
    legit = "https://claude.ai/oauth/client-metadata"
    db.save_cimd_client(legit, info, 86400)
    assert not db.cimd_client_known(legit)  # accepted, but nobody signed in through it yet
    db.mark_cimd_client_signed_in(legit)
    assert db.cimd_client_known(legit)
    for i in range(dbmod.MAX_CIMD_CLIENTS + 50):
        db.save_cimd_client(f"https://h{i}.evil.example/c", info, 86400)
    db.save_cimd_client(legit, info, 300)  # a refresh keeps the mark
    assert db.cimd_client_known(legit)
    with db.tx() as c:
        n = c.execute("SELECT COUNT(*) FROM cimd_clients").fetchone()[0]
        evil = c.execute("SELECT COUNT(*) FROM cimd_clients WHERE client_id LIKE '%evil%'").fetchone()[0]
        c.execute("UPDATE cimd_clients SET expires_at = 1")
    assert evil <= dbmod.MAX_CIMD_CLIENTS_PER_SITE and n <= dbmod.MAX_CIMD_CLIENTS + 1
    db.purge_expired()  # expired documents go, the signed-in client's mark stays
    assert db.cimd_client_known(legit) and db.get_cimd_client(legit) is None
    with db.tx() as c:
        c.execute("UPDATE cimd_clients SET signed_in_at = ?", (int(time.time()) - 200 * 86400,))
    db.purge_expired()
    assert not db.cimd_client_known(legit)  # unused for half a year: forgotten
    db.close()


async def test_sign_in_through_a_metadata_client_marks_it_known(tmp_path: Path, idp: FakeIdP):
    from tests.test_round4_web import cimd_sign_in

    docs = DocHost()
    docs.serve()
    async with running(Harness(make_settings(tmp_path), idp, cimd=docs.fetcher())) as gw:
        await cimd_sign_in(gw)
        assert gw.db.cimd_client_known(CLIENT_URL)


# -- RA-6: the sign-in rate limit only applies to public addresses ----------------------------


def _from(h: Harness, ip: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app, client=(ip, 40000)), base_url=GATEWAY)


@pytest.fixture
async def untrusted_proxy_gw(tmp_path: Path, idp: FakeIdP):
    """A gateway that trusts only loopback, so a proxy elsewhere is not believed."""
    async with running(Harness(make_settings(tmp_path, trusted_proxies=["127.0.0.1"]), idp)) as h:
        h.app.state.gateway.provider.login_limiter = LoginStartLimiter(per_minute=3)
        yield h


async def test_non_public_sources_are_not_rate_limited(untrusted_proxy_gw: Harness):
    gw = untrusted_proxy_gw
    for ip in ("100.64.3.4", "10.1.2.3", "172.18.0.5", "fd7a:115c:a1e0::1", "169.254.1.1"):
        async with _from(gw, ip) as a:
            for _ in range(10):
                a.cookies.clear()
                assert (await a.get("/login")).status_code == 302, ip
    async with _from(gw, "5.6.7.8") as a:  # a public address still is
        for _ in range(3):
            assert (await a.get("/login")).status_code == 302
        assert (await a.get("/login")).status_code == 429


async def test_many_browsers_behind_one_address_log_a_warning_once(
    untrusted_proxy_gw: Harness, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
):
    monkeypatch.setattr(apmod, "SHARED_SOURCE_WARN_BROWSERS", 5)
    with caplog.at_level(logging.WARNING, logger="mtg_gateway.auth_provider"):
        async with _from(untrusted_proxy_gw, "100.64.0.7") as a:
            for _ in range(12):
                a.cookies.clear()
                assert (await a.get("/login")).status_code == 302
    warnings = [r.getMessage() for r in caplog.records if "MTG_TRUSTED_PROXIES" in r.getMessage()]
    assert len(warnings) == 1 and "100.64.0.7" in warnings[0]


# -- RA-8: "Delete my data" leaves other members' deck covers alone ---------------------------


def test_delete_my_data_keeps_other_members_deck_covers(tmp_path: Path):
    db = Database(tmp_path / "g.sqlite")
    now = int(time.time())
    db.save_deck_cover("42", "uid-a", "Sol Ring", owner_sub="member-a")  # A's own deck
    db.save_deck_cover("77", "uid-old", "Island")  # legacy row (no owner) of A's deck
    db.save_deck_cover("55", "uid-b", "Forest")  # legacy row of B's own deck
    db.save_deck_cover("56", "uid-b2", "Swamp", owner_sub="member-b")
    with db.tx() as c:
        for pid, deck in (("p1", "42"), ("p2", "77")):  # B cloned A's decks
            c.execute(
                "INSERT INTO proposals (id, owner_sub, kind, deck_id, baseline_fingerprint, "
                "changes_json, diff_text, created_at, expires_at) "
                "VALUES (?, 'member-b', 'create_deck', ?, 'f', '{}', '', ?, ?)",
                (pid, deck, now, now + 3600),
            )
    db.save_snapshot("s1", owner_sub="member-b", deck_id="55", proposal_id=None, fingerprint="f", deck={})
    db.save_snapshot("s2", owner_sub="member-b", deck_id="42", proposal_id=None, fingerprint="f", deck={})
    out = db.delete_member_data("member-b")
    assert db.deck_covers(["42"]) and db.deck_covers(["77"])  # A's covers stay
    assert db.deck_covers(["55"]) == {} and db.deck_covers(["56"]) == {}  # B's own go
    assert out["deck_covers"] == 2
    db.close()


def test_clients_in_use_before_the_upgrade_stay_known(tmp_path: Path):
    """Round 3 (RC-1): the new signed_in_at column started empty, so Claude and ChatGPT became
    first-time clients after the upgrade and one bogus 404 could block their host."""
    import sqlite3

    path = tmp_path / "g.sqlite"
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    for i, step in enumerate(dbmod.MIGRATIONS[:9], 1):
        step(conn)
        conn.execute(f"PRAGMA user_version = {i}")
    now = int(time.time())
    used, unused = "https://claude.ai/oauth/client-metadata", "https://junk.example/c"
    for url in (used, unused):
        conn.execute(
            "INSERT INTO cimd_clients (client_id, info_json, fetched_at, expires_at) VALUES (?,?,?,?)",
            (url, "{}", now - 7200, now - 3600),
        )
    conn.execute(
        "INSERT INTO tokens (token_hash, kind, client_id, sub, scopes_json, family, expires_at, created_at)"
        " VALUES ('h', 'refresh', ?, 'user-1', '[]', 'f', ?, ?)",
        (used, now + 3600, now - 100),
    )
    conn.commit()
    conn.close()
    db = Database(path)
    assert db.cimd_client_known(used)
    assert not db.cimd_client_known(unused)


# -- Round 4 (RE-1): a lookup that never answers keeps its thread; signed-in clients keep working --


async def test_hung_lookups_of_junk_ids_do_not_reach_the_signed_in_lane(monkeypatch: pytest.MonkeyPatch):
    """Junk client ids whose DNS hangs fill threads that a timeout can't free. They used to share
    the resolver pool with signed-in clients, whose refetch then timed out and was remembered as a
    failure for a minute."""
    import socket
    import threading

    monkeypatch.setattr(cimdmod, "DNS_TIMEOUT", 0.3)
    release = threading.Event()
    real = ("93.184.216.34", 443)

    def getaddrinfo(host: str, *args: object, **kw: object) -> list:
        if host != "client.example":
            release.wait(10)  # a resolver that never answers in time
            raise OSError("timeout")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", real)]

    monkeypatch.setattr(cimdmod.socket, "getaddrinfo", getaddrinfo)
    docs = DocHost()
    docs.serve()
    http = httpx.AsyncClient(transport=httpx.MockTransport(docs.handler), follow_redirects=False)
    f = cimdmod.CimdFetcher(http=http, timeout=1.0)
    try:
        junk = [asyncio.create_task(f.fetch(f"https://x.junk{i}.example/c")) for i in range(12)]
        for result in await asyncio.gather(*junk, return_exceptions=True):
            assert isinstance(result, CimdError)
        info, _ = await f.fetch(CLIENT_URL, known=True)  # every first-time DNS thread still hung
        assert info["client_name"] == "Example Assistant"
    finally:
        release.set()
        await f.aclose()


async def test_an_unreachable_signed_in_client_is_not_remembered_as_failed(monkeypatch: pytest.MonkeyPatch):
    docs = DocHost()
    docs.serve()
    saved = docs.addresses.pop("client.example")  # its DNS fails for a moment
    f = docs.fetcher()
    with pytest.raises(cimdmod.CimdUnavailable):
        await f.fetch(CLIENT_URL, known=True)
    docs.addresses["client.example"] = saved
    # Left alone for a minute, but as "unavailable" (the caller keeps the last good copy), never
    # as "failed recently", which would refuse the client.
    with pytest.raises(cimdmod.CimdUnavailable):
        await f.fetch(CLIENT_URL, known=True)
    assert docs.requests == []
    f._known_backoff.clear()  # a minute later
    assert (await f.fetch(CLIENT_URL, known=True))[0]["client_name"]
    await f.aclose()


async def test_a_signed_in_client_keeps_its_last_good_document_while_unreachable(
    tmp_path: Path, idp: FakeIdP
):
    docs = DocHost()
    docs.serve()
    async with running(Harness(make_settings(tmp_path), idp, cimd=docs.fetcher())) as h:
        provider = h.app.state.gateway.provider
        assert (await provider._cimd_client(CLIENT_URL))["client_name"] == "Example Assistant"
        h.db.mark_cimd_client_signed_in(CLIENT_URL)
        with h.db.tx() as c:
            c.execute("UPDATE cimd_clients SET expires_at = ?", (int(time.time()) - 60,))
        del docs.addresses["client.example"]  # its server can't be reached now
        assert (await provider._cimd_client(CLIENT_URL))["client_name"] == "Example Assistant"
        # A client nobody signed in with gets no such grace.
        with h.db.tx() as c:
            c.execute("UPDATE cimd_clients SET signed_in_at = NULL")
        assert await provider._cimd_client(CLIENT_URL) is None


# -- Round 5 (RF-1, RF-2) ------------------------------------------------------------------------


@pytest.mark.parametrize("change", ["withdrawn", "invalid"])
async def test_a_refused_document_is_never_served_stale(tmp_path: Path, idp: FakeIdP, change: str):
    """The stale copy is only for an unreachable server: once the server itself withdraws the
    document or serves an unacceptable one, the old copy (old redirect URIs) is never used again,
    also not through the one-minute negative cache."""
    docs = DocHost()
    docs.serve()
    async with running(Harness(make_settings(tmp_path), idp, cimd=docs.fetcher())) as h:
        provider = h.app.state.gateway.provider
        assert await provider._cimd_client(CLIENT_URL)
        h.db.mark_cimd_client_signed_in(CLIENT_URL)
        with h.db.tx() as c:
            c.execute("UPDATE cimd_clients SET expires_at = ?", (int(time.time()) - 60,))
        if change == "withdrawn":
            del docs.docs[CLIENT_URL]
        else:
            docs.serve(body=document(client_id="https://other.example/x.json"))
        for _ in range(3):
            assert await provider._cimd_client(CLIENT_URL) is None
        assert await provider.get_client(CLIENT_URL) is None
        assert h.db.cimd_client_known(CLIENT_URL)  # still remembered as signed in, for a fixed document
        docs.serve()
        provider.cimd._failures.clear()  # past the negative cache
        assert (await provider._cimd_client(CLIENT_URL))["client_name"] == "Example Assistant"


async def test_one_hung_signed_in_host_cannot_use_up_the_lanes_dns_threads(monkeypatch: pytest.MonkeyPatch):
    import socket
    import threading

    monkeypatch.setattr(cimdmod, "DNS_TIMEOUT", 0.3)
    release = threading.Event()
    lookups: list[str] = []

    def getaddrinfo(host: str, *args: object, **kw: object) -> list:
        lookups.append(host)
        if host == "hung.example":
            release.wait(10)
            raise OSError("timeout")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(cimdmod.socket, "getaddrinfo", getaddrinfo)
    docs = DocHost()
    docs.serve()
    http = httpx.AsyncClient(transport=httpx.MockTransport(docs.handler), follow_redirects=False)
    f = cimdmod.CimdFetcher(http=http, timeout=1.0)
    try:
        for _ in range(6):
            with pytest.raises(cimdmod.CimdUnavailable):
                await f.fetch("https://hung.example/c.json", known=True)
        assert lookups.count("hung.example") == 1  # one hung thread, not one per request
        assert (await f.fetch(CLIENT_URL, known=True))[0]["client_name"]
    finally:
        release.set()
        await f.aclose()


# -- Round 6 (RG-1, RG-2) ------------------------------------------------------------------------


@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_a_struggling_server_keeps_the_last_good_document(tmp_path: Path, idp: FakeIdP, status: int):
    docs = DocHost()
    docs.serve()
    async with running(Harness(make_settings(tmp_path), idp, cimd=docs.fetcher())) as h:
        provider = h.app.state.gateway.provider
        assert await provider._cimd_client(CLIENT_URL)
        h.db.mark_cimd_client_signed_in(CLIENT_URL)
        with h.db.tx() as c:
            c.execute("UPDATE cimd_clients SET expires_at = ?", (int(time.time()) - 60,))
        docs.serve(status=status, headers={"retry-after": "3600"})
        sent = len(docs.requests)
        for _ in range(20):  # anonymous lookups while it struggles: one request, not twenty
            assert (await provider._cimd_client(CLIENT_URL))["client_name"] == "Example Assistant"
        assert len(docs.requests) == sent + 1
        # and no slot or lock was left behind by the lookups that were turned away
        assert provider.cimd._known_gate._value == cimdmod.MAX_CONCURRENT_FETCHES
        assert not provider.cimd._url_locks
        docs.serve()
        provider.cimd._known_backoff.clear()  # a minute later
        assert (await provider._cimd_client(CLIENT_URL))["client_name"] == "Example Assistant"
        assert h.db.get_cimd_client(CLIENT_URL)  # fresh again


async def test_urls_on_one_hung_host_hold_one_signed_in_dns_thread(monkeypatch: pytest.MonkeyPatch):
    import socket
    import threading

    monkeypatch.setattr(cimdmod, "DNS_TIMEOUT", 0.3)
    release = threading.Event()
    lookups: list[str] = []

    def getaddrinfo(host: str, *args: object, **kw: object) -> list:
        lookups.append(host)
        if host == "hung.example":
            release.wait(10)
            raise OSError("timeout")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(cimdmod.socket, "getaddrinfo", getaddrinfo)
    docs = DocHost()
    docs.serve()
    http = httpx.AsyncClient(transport=httpx.MockTransport(docs.handler), follow_redirects=False)
    f = cimdmod.CimdFetcher(http=http, timeout=1.0)
    try:
        hung = [asyncio.create_task(f.fetch(f"https://hung.example/c{i}.json", known=True)) for i in range(8)]
        for result in await asyncio.gather(*hung, return_exceptions=True):
            assert isinstance(result, cimdmod.CimdUnavailable | cimdmod.CimdBusy), result
        assert lookups.count("hung.example") == 1
        assert (await f.fetch(CLIENT_URL, known=True))[0]["client_name"]
    finally:
        release.set()
        await f.aclose()
