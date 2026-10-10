"""The browser pages need a signed-in member; the machine-facing routes stay open."""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

import pytest

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser

REPO_PLUGIN = Path(__file__).resolve().parent.parent / "plugin"
NAVIGATE = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}


@pytest.fixture
async def harness(tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(REPO_PLUGIN))
    async with running(Harness(make_settings(tmp_path, required_group="mtg-users"), idp)) as h:
        yield h


async def test_anonymous_browser_is_sent_to_sign_in(harness: Harness) -> None:
    r = await harness.http.get("/", headers=NAVIGATE)
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/"
    r = await harness.http.get("/install?for=claude", headers=NAVIGATE)
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/install?for=claude"
    for path in ("/account", "/proposals", "/scan", "/skill"):
        r = await harness.http.get(path, headers=NAVIGATE)
        assert r.status_code == 302 and r.headers["location"].startswith("/login?next="), path


async def test_machine_routes_stay_open(harness: Harness) -> None:
    assert (await harness.http.get("/healthz")).status_code == 200
    meta = await harness.http.get("/.well-known/oauth-authorization-server")
    assert meta.status_code == 200 and meta.json()["issuer"].rstrip("/") == GATEWAY
    assert (await harness.http.get("/install.md")).status_code == 200
    assert (await harness.http.get("/plugin/marketplace.json")).status_code == 200
    assert (await harness.http.get("/plugin/mtg-gateway.zip")).status_code == 200
    assert (await harness.http.get("/mcp")).status_code == 401
    # An AI agent handed the install page's address gets the public Markdown steps, not a login.
    agent = await harness.http.get("/install?for=claude-code")
    assert agent.status_code == 200
    assert agent.headers["content-type"].startswith("text/plain")
    assert "claude plugin install mtg-gateway@mtg-gateway" in agent.text
    assert "<html" not in agent.text


async def test_member_sees_the_dashboard(harness: Harness) -> None:
    b = Browser(harness)
    try:
        await b.login("/")
        r = await b.http.get("/", headers=NAVIGATE)
        assert r.status_code == 200 and f"{GATEWAY}/mcp" in r.text
        page = await b.http.get("/install", headers=NAVIGATE)
        assert page.status_code == 200 and "Which app will you use?" in page.text
    finally:
        await b.aclose()


async def test_signed_in_outsider_is_refused(harness: Harness, idp: FakeIdP) -> None:
    idp.user = {**idp.user, "sub": "user-2", "groups": ["someone-else"]}
    b = Browser(harness)
    try:
        r = await b.http.get("/login", params={"next": "/"})
        assert r.status_code == 302
        cb = urlparse(await harness.idp_leg(r))
        refused = await b.http.get(f"{cb.path}?{cb.query}")
        assert refused.status_code == 403
        assert "not in the group" in refused.text
        assert "mtg_session=" not in refused.headers.get("set-cookie", "")
        r = await b.http.get("/", headers=NAVIGATE)
        assert r.status_code == 302 and r.headers["location"] == "/login?next=/"
    finally:
        await b.aclose()


async def test_session_without_the_group_on_record_is_locked_out(harness: Harness) -> None:
    b = Browser(harness)
    try:
        await b.login("/")
        assert (await b.http.get("/")).status_code == 200
        # As if the recorded groups no longer include MTG_REQUIRED_GROUP (or the setting changed).
        harness.db.upsert_user(
            "user-1", email="alice@example.test", name="Alice", preferred_username="alice", groups=[]
        )
        for path in ("/", "/account", "/skill", "/scan"):
            r = await b.http.get(path, headers=NAVIGATE)
            assert r.status_code == 302 and r.headers["location"].startswith("/login?next="), path
        assert (await b.http.get("/scan/api/sessions")).status_code == 401
    finally:
        await b.aclose()
