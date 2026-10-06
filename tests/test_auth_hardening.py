"""Security round 3 (auth core): anonymous growth caps, the consent and review-page click guard,
token revocation scope, CIMD input handling and the SSRF address filter."""

from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from mtg_gateway import db as dbmod
from mtg_gateway.cimd import (
    CimdError,
    CimdFetcher,
    CimdThrottled,
    _address_is_public,
    check_redirect_uri,
    is_cimd_client_id,
    validate_document,
)
from mtg_gateway.clickguard import MIN_FORM_AGE, form_stamp, submitted_too_soon
from mtg_gateway.oidc import pkce_pair
from tests.conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from tests.fake_archidekt import FakeArchidekt
from tests.test_cimd import CLIENT_URL, DocHost, document
from tests.test_decks_and_proxy import Browser, Stack, _client, call, linked_user, structured


def _authorize_params(client_id: str, **over: str) -> dict[str, str]:
    _verifier, challenge = pkce_pair()
    q = {
        "client_id": client_id,
        "redirect_uri": "https://client.test/cb",
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "cs1",
    }
    q.update(over)
    return q


def _count(h: Harness, table: str, where: str = "1=1", args: tuple = ()) -> int:
    with h.db._lock:
        return h.db._conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", args).fetchone()[0]


# -- 3: anonymous registrations cannot fill the audit log --------------------------------------


async def test_register_audit_row_is_small_and_bounded(gw: Harness):
    uris = [f"https://h{i}.client.test/" + "p" * 700 for i in range(10)]
    r = await gw.http.post("/register", json={"redirect_uris": uris, "client_name": "x" * 500})
    assert r.status_code == 201, r.text
    row = gw.db.audit_recent(1)[0]
    assert row["event"] == "client_registered"
    detail = row["detail"]
    assert detail["redirect_uri_count"] == 10 and len(detail["redirect_hosts"]) == 3
    assert len(detail["client_name"]) == 60
    with gw.db._lock:
        raw = gw.db._conn.execute("SELECT detail_json FROM audit_log ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert len(raw) < 400, raw


def test_anonymous_audit_rows_are_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(dbmod, "MAX_ANONYMOUS_AUDIT_ROWS", 20)
    monkeypatch.setattr(dbmod, "ANONYMOUS_AUDIT_TRIM_EVERY", 10)
    d = dbmod.Database(tmp_path / "a.db")
    d.audit("login_ok", sub="member")  # a member's own row is never trimmed by the cap
    for i in range(95):
        d.audit("client_registered", client_id=f"c{i}", detail={"blob": "x" * 5000})
    with d._lock:
        rows = d._conn.execute("SELECT event, detail_json FROM audit_log").fetchall()
    anon = [r for r in rows if r["event"] == "client_registered"]
    assert len(anon) <= 20 + 10 and any(r["event"] == "login_ok" for r in rows)
    assert all(len(r["detail_json"]) <= dbmod.MAX_ANONYMOUS_AUDIT_DETAIL for r in anon)
    d.purge_expired()
    with d._lock:
        left = d._conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = 'client_registered'").fetchone()[
            0
        ]
    assert left == 20
    # the newest rows are the ones kept
    assert d.audit_recent(1)[0]["client_id"] == "c94"


# -- 6: anonymous /authorize and /login cannot grow login_sessions -----------------------------


async def test_login_sessions_are_capped_and_expired_rows_dropped_on_insert(
    gw: Harness, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(dbmod, "MAX_LOGIN_SESSIONS", 15)
    for _ in range(40):
        gw.http.cookies.clear()  # a new browser each time (one browser keeps only its newest 10)
        r = await gw.http.get("/login")
        assert r.status_code == 302
    assert _count(gw, "login_sessions") == 15
    with gw.db._lock:
        gw.db._conn.execute("UPDATE login_sessions SET expires_at = 1")
    r = await gw.http.get("/login")
    assert r.status_code == 302
    assert _count(gw, "login_sessions") == 1  # expired rows went with the next insert


async def test_authorize_refuses_oversized_state_and_bad_pkce_challenge(gw: Harness):
    client = await gw.register()
    r = await gw.http.get("/authorize", params=_authorize_params(client["client_id"], state="s" * 513))
    assert r.status_code in (302, 400) and "consent" not in r.headers.get("location", "")
    r = await gw.http.get("/authorize", params=_authorize_params(client["client_id"], code_challenge="abc"))
    assert r.status_code in (302, 400) and "confirm" not in r.headers.get("location", "")
    assert _count(gw, "login_sessions") == 0
    r = await gw.http.get("/authorize", params=_authorize_params(client["client_id"], state="s" * 512))
    assert r.status_code == 302 and "/authorize/confirm" in r.headers["location"]


# -- 14: the consent POST body is capped ------------------------------------------------------


async def test_consent_post_body_is_capped(gw: Harness):
    big = b"state=x&csrf=y&action=approve&pad=" + b"a" * (64 * 1024)
    r = await gw.http.post(
        "/authorize/confirm", content=big, headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert r.status_code == 413

    async def chunks():
        for _ in range(64):
            yield b"a" * 1024

    r = await gw.http.post(
        "/authorize/confirm", content=chunks(), headers={"content-type": "application/x-www-form-urlencoded"}
    )
    assert r.status_code == 413


# -- 15: IDN client ids are refused cleanly -----------------------------------------------------

IDN_URL = "https://сlaude.ai/oauth/client.json"  # Cyrillic es


async def test_idn_cimd_client_id_is_refused_not_a_500(tmp_path: Path, idp: FakeIdP):
    docs = DocHost()
    docs.addresses["xn--laude-0ye.ai"] = ["93.184.216.34"]
    docs.addresses["сlaude.ai"] = ["93.184.216.34"]
    assert not is_cimd_client_id(IDN_URL)
    async with running(Harness(make_settings(tmp_path), idp, cimd=docs.fetcher())) as h:
        r = await h.http.get("/authorize", params=_authorize_params(IDN_URL))
        assert r.status_code == 400, r.text
        r = await h.http.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": IDN_URL,
                "code": "x",
                "code_verifier": "y",
            },
        )
        assert 400 <= r.status_code < 500 and r.json()["error"] == "invalid_client", r.text


async def test_fetch_encoding_errors_become_cimd_errors_and_are_negative_cached():
    def boom(_request: httpx.Request) -> httpx.Response:
        raise UnicodeEncodeError("ascii", "с", 0, 1, "ordinal not in range")

    async def resolve(_host: str) -> list[str]:
        return ["93.184.216.34"]

    f = CimdFetcher(http=httpx.AsyncClient(transport=httpx.MockTransport(boom)), resolver=resolve)
    with pytest.raises(CimdError) as first:
        await f.fetch(CLIENT_URL)
    assert not isinstance(first.value, CimdThrottled)
    with pytest.raises(CimdThrottled):
        await f.fetch(CLIENT_URL)


# -- 16/35: an app revoking its own token signs nobody out --------------------------------------


async def test_revoke_ends_only_that_token_family(gw: Harness):
    client = await gw.register()
    tokens = await gw.tokens_for(client)
    b = httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url=GATEWAY)
    r = await b.get("/login", params={"next": "/account"})
    cb = await gw.idp_leg(r)
    p = urlparse(cb)
    assert (await b.get(f"{p.path}?{p.query}")).status_code == 302
    assert (await b.get("/account")).status_code == 200
    r = await gw.http.post(
        "/revoke",
        data={
            "token": tokens["access_token"],
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )
    assert r.status_code == 200
    assert (await b.get("/account")).status_code == 200  # the browser session survives
    r = await gw.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
    assert r.status_code == 400  # but the grant's whole family is gone
    await b.aclose()


# -- 17: CIMD names get the same sanitising as registered names --------------------------------


async def test_cimd_client_name_is_sanitised(tmp_path: Path, idp: FakeIdP):
    wording = "Claude ‮(verified by this gateway)⁦ It will be able to read only " + "x" * 120
    rec = validate_document(CLIENT_URL, document(client_name=wording))
    assert len(rec["client_name"]) == 60 and "‮" not in rec["client_name"]
    assert "⁦" not in rec["client_name"]
    with pytest.raises(CimdError):
        validate_document(CLIENT_URL, document(client_name="‮⁦"))
    docs = DocHost()
    docs.serve(body=document(client_name=wording))
    async with running(Harness(make_settings(tmp_path), idp, cimd=docs.fetcher())) as h:
        _v, challenge = pkce_pair()
        r = await h.http.get(
            "/authorize",
            params={
                "client_id": CLIENT_URL,
                "redirect_uri": "https://client.example/cb",
                "response_type": "code",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "state": "cs1",
            },
        )
        page = await h.http.get(r.headers["location"])
        assert page.status_code == 200 and "‮" not in page.text
        name = re.search(r"<span class='app'>([^<]*)</span>", page.text).group(1)  # type: ignore[union-attr]
        assert name == rec["client_name"] and len(name) == 60


# -- 36: redirect URIs are judged on what the OAuth models will parse ---------------------------


def test_cimd_redirect_uri_parser_confusion_is_refused():
    for bad in (
        "http://evil.example\\@localhost/cb",
        "http://evil.example\\.localhost/cb",
        "http://user@localhost/cb",
        "https://user:pw@client.example/cb",
        "http://localhost\t/cb",
    ):
        assert check_redirect_uri(bad), bad
        with pytest.raises(CimdError):
            validate_document(CLIENT_URL, document(redirect_uris=[bad]))
    for good in (
        "http://localhost:3000/cb",
        "http://127.0.0.1/cb",
        "http://[::1]:8080/cb",
        "https://a.example/cb",
    ):
        assert check_redirect_uri(good) is None, good
    assert check_redirect_uri("http://evil.example/cb")


# -- 37: the SSRF filter's non-global IPv6 ranges ------------------------------------------------


def test_site_local_and_siit_addresses_are_not_public():
    for bad in (
        "fec0::1",
        "feff::1",
        "::ffff:0:a00:1",
        "::ffff:0:7f00:1",
        "::ffff:0:c0a8:101",
        "100::1",
        "2001:db8::1",
    ):
        assert not _address_is_public(bad), bad
    for good in ("2606:4700::6810:84e5", "::ffff:0:5db8:d822", "93.184.216.34"):
        assert _address_is_public(good), good


# -- 4: consent page click guard -----------------------------------------------------------------


async def _consent(gw: Harness) -> tuple[str, str, httpx.Response]:
    client = await gw.register()
    r, _verifier = await gw.start_login(client)
    login_id = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    page = await gw.http.get("/authorize/confirm", params={"state": login_id})
    assert page.status_code == 200
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page.text).group(1)  # type: ignore[union-attr]
    return login_id, csrf, page


async def test_consent_buttons_start_disabled_behind_a_strict_csp(gw: Harness):
    _login_id, _csrf, page = await _consent(gw)
    guarded = page.text.split("<noscript>")[0]
    assert re.search(r"<button[^>]*value='approve'[^>]*disabled data-guard", guarded)
    assert re.search(r"<button[^>]*value='deny'[^>]*disabled data-guard", guarded)
    assert "<script src='/static/clickguard.js' defer></script>" in page.text
    assert "<script>" not in page.text  # no inline script
    csp = page.headers["content-security-policy"]
    assert (
        "default-src 'none'" in csp
        and "script-src 'self';" in csp
        and "unsafe" not in csp.split("style-src")[0]
    )
    assert page.headers["x-frame-options"] == "DENY"
    js = await gw.http.get("/static/clickguard.js")
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    assert "visibilitychange" in js.text and "blur" in js.text


async def test_consent_submit_faster_than_a_person_is_shown_again(gw: Harness):
    login_id, csrf, page = await _consent(gw)
    shown = re.search(r"name='shown' value='([^']+)'", page.text).group(1)  # type: ignore[union-attr]
    r = await gw.http.post(
        "/authorize/confirm", data={"state": login_id, "csrf": csrf, "action": "approve", "shown": shown}
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/authorize/confirm?state=")
    forged = f"{int(time.time() * 1000) - 5000}.{'0' * 32}"
    r = await gw.http.post(
        "/authorize/confirm", data={"state": login_id, "csrf": csrf, "action": "approve", "shown": forged}
    )
    assert r.status_code == 303 and r.headers["location"].startswith("/authorize/confirm?")
    aged = form_stamp(gw.settings.session_secret, f"consent:{login_id}", now=time.time() - 2 * MIN_FORM_AGE)
    r = await gw.http.post(
        "/authorize/confirm", data={"state": login_id, "csrf": csrf, "action": "approve", "shown": aged}
    )
    assert r.status_code == 303 and r.headers["location"].startswith("https://idp.test/")


def test_form_stamp_rules():
    secret = "s" * 48
    now = time.time()
    assert not submitted_too_soon(secret, "x", None)
    assert submitted_too_soon(secret, "x", form_stamp(secret, "x", now), now)
    assert not submitted_too_soon(secret, "x", form_stamp(secret, "x", now - 1), now)
    assert submitted_too_soon(secret, "y", form_stamp(secret, "x", now - 1), now)  # other scope
    assert submitted_too_soon(secret, "x", "garbage", now)


# -- 24/26: proposal review page click guard -------------------------------------------------------


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(tmp_path, writes_enabled=True, archidekt_base="https://ark.test/api")
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h:
        yield Stack(h, ark)


async def test_review_page_apply_is_guarded(stack: Stack):
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Arcane Signet"}]},
        )
    )
    pid = p["proposal_id"]
    b = Browser(h)
    await b.login()
    url = f"/proposals/{pid}"
    page = await b.http.get(url)
    guarded = page.text.split("<noscript>")[0]
    assert re.search(r"<button[^>]*value='apply'[^>]*disabled data-guard", guarded)
    assert "<script src='/static/clickguard.js' defer></script>" in page.text
    assert "script-src 'self'" in page.headers["content-security-policy"]
    csrf = await b.csrf(url)
    shown = re.search(r"name='shown' value='([^']+)'", page.text).group(1)  # type: ignore[union-attr]
    # Submitted the instant the page appeared (a double-click swap): nothing is applied.
    r = await b.http.post(url, data={"csrf": csrf, "action": "apply", "shown": shown})
    assert r.status_code == 303 and r.headers["location"] == url and ark.patches == []
    assert (await b.http.get(url)).text.count("disabled data-guard") >= 1
    sub = h.db.list_users()[0]["sub"]
    aged = form_stamp(h.settings.session_secret, f"proposal:{sub}:{pid}", now=time.time() - 2 * MIN_FORM_AGE)
    r = await b.http.post(url, data={"csrf": csrf, "action": "apply", "shown": aged})
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=applied")
    assert len(ark.patches) == 1
    await b.aclose()


def test_guard_script_ships_in_the_wheel():
    """Without clickguard.js in the installed package the consent buttons would never unlock."""
    import tomllib

    data = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())
    assert "static/*" in data["tool"]["setuptools"]["package-data"]["mtg_gateway"]


def test_an_app_cannot_take_the_browser_name() -> None:
    from mtg_gateway.cimd import sanitise_client_name

    assert sanitise_client_name("browser") == "browser (app)"
    assert sanitise_client_name(" Browser ") == "Browser (app)"
    assert sanitise_client_name("Browser helper") == "Browser helper"
