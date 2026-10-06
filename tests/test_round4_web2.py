"""Security round 4, re-review: the metadata fetch budgets cannot be spent with junk client ids
and never apply to signed-in or allowlisted clients, the cache caps group by site and never evict
a signed-in client, the sign-in rate limit only applies to public addresses, and "Delete my data"
leaves other members' deck covers alone."""

from __future__ import annotations

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


async def test_allowlisted_host_is_never_throttled(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(cimdmod, "GLOBAL_FETCHES_PER_MINUTE", 2)
    monkeypatch.setattr(cimdmod, "SITE_FETCHES_PER_MINUTE", 2)

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
        await f.fetch("https://client.example/missing")  # a failure does not block the host either
    for i in range(5):  # over both budgets, all fetched
        assert (await f.fetch(f"https://client.example/c{i}.json"))[0]["client_name"]
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
