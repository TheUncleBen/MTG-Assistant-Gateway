"""Admin pages and API: gated on MTG_ADMIN_GROUP, and the disable / revoke / unlink actions bite."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import pytest
from starlette.routing import Route

from mtg_gateway import db as dbmod
from mtg_gateway.admin import add_admin_routes
from mtg_gateway.config import ConfigError, load_settings
from mtg_gateway.db import Database

from .conftest import FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser

NAVIGATE = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
ADMIN = {"sub": "admin-1", "email": "root@example.test", "name": "Root", "preferred_username": "root"}
ADMIN_GROUPS = ["mtg-users", "mtg-admins"]


class _RouteShim:
    """Registers custom routes on an already built Starlette app, the way MCPServer.custom_route
    would have before the app was built (app.py wires add_admin_routes itself in production)."""

    def __init__(self, app):
        self.app = app

    def custom_route(self, path: str, methods: list[str], name=None, include_in_schema: bool = True):
        def deco(fn):
            self.app.router.routes.insert(0, Route(path, endpoint=fn, methods=methods))
            return fn

        return deco


def _with_admin(h: Harness) -> Harness:
    if not any(getattr(r, "path", None) == "/admin" for r in h.app.router.routes):
        add_admin_routes(_RouteShim(h.app), h.app.state.gateway)  # type: ignore[arg-type]
    return h


@pytest.fixture
async def gw(tmp_path: Path, idp: FakeIdP):
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group="mtg-admins")
    async with running(_with_admin(Harness(settings, idp))) as h:
        yield h


@pytest.fixture
async def gw_no_admin(tmp_path: Path, idp: FakeIdP):
    settings = make_settings(tmp_path, required_group="mtg-users", admin_group=None)
    async with running(_with_admin(Harness(settings, idp))) as h:
        yield h


async def admin_browser(h: Harness) -> Browser:
    h.idp.user = {**ADMIN, "groups": ADMIN_GROUPS}
    b = Browser(h)
    await b.login("/admin")
    return b


async def member_token(h: Harness, sub: str = "user-2") -> tuple[dict, dict]:
    """(client, tokens) for an ordinary member, via the full OAuth flow."""
    h.idp.user = {
        "sub": sub,
        "email": f"{sub}@example.test",
        "name": sub,
        "preferred_username": sub,
        "groups": ["mtg-users"],
    }
    client = await h.register()
    return client, await h.tokens_for(client)


async def whoami(h: Harness, token: str):
    return await h.mcp(token, "tools/call", {"name": "whoami", "arguments": {}}, rid=3)


# -- gate ---------------------------------------------------------------------------
async def test_admin_routes_do_not_exist_without_admin_group(gw_no_admin: Harness) -> None:
    b = await admin_browser(gw_no_admin)
    try:
        for path in ("/admin", "/admin/users", "/admin/activity", "/admin/metrics"):
            assert (await b.http.get(path, headers=NAVIGATE)).status_code == 404, path
        r = await b.http.get("/api/v1/admin/overview")
        assert r.status_code == 404 and r.json() == {
            "ok": False,
            "error": "not_found",
            "message": "no such page",
        }
        r = await b.http.post("/api/v1/admin/users/user-2", json={"action": "disable"})
        assert r.status_code == 404
    finally:
        await b.aclose()


async def test_non_admin_member_gets_404_and_anonymous_is_sent_to_sign_in(gw: Harness) -> None:
    r = await gw.http.get("/admin", headers=NAVIGATE)
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/admin"
    assert (await gw.http.get("/api/v1/admin/users")).status_code == 401
    b = Browser(gw)  # idp default user: in mtg-users only
    try:
        await b.login("/account")
        for path in ("/admin", "/admin/users", "/admin/activity", "/admin/metrics"):
            assert (await b.http.get(path, headers=NAVIGATE)).status_code == 404, path
        assert (await b.http.get("/api/v1/admin/overview")).status_code == 404
        r = await b.http.post("/admin/users/admin-1", data={"action": "disable", "csrf": "x"})
        assert r.status_code == 404
    finally:
        await b.aclose()


async def test_admin_sees_the_pages_and_the_api(gw: Harness) -> None:
    client, tokens = await member_token(gw)
    assert (await whoami(gw, tokens["access_token"])).status_code == 200
    b = await admin_browser(gw)
    try:
        r = await b.http.get("/admin", headers=NAVIGATE)
        assert r.status_code == 200 and "Signed in at least once" in r.text and "<svg" in r.text
        assert "default-src 'none'" in r.headers["content-security-policy"]
        users = await b.http.get("/admin/users", headers=NAVIGATE)
        assert users.status_code == 200 and "user-2" in users.text and "root" in users.text
        assert "value='disable'" in users.text and "name='csrf'" in users.text
        act = await b.http.get("/admin/activity", headers=NAVIGATE)
        assert act.status_code == 200 and "tokens_issued" in act.text
        only = await b.http.get("/admin/activity?sub=user-2", headers=NAVIGATE)
        assert only.status_code == 200 and "admin-1" not in only.text.split("<ul", 1)[1]
        gw.db.metrics_increment("2026-01-01", "user-2", "tool", "whoami", 3)
        gw.app.state.gateway.db.metrics_increment(dbmod.Database._since_day(1), "user-2", "tool", "whoami")
        m = await b.http.get("/admin/metrics", headers=NAVIGATE)
        assert m.status_code == 200 and "whoami" in m.text

        o = (await b.http.get("/api/v1/admin/overview")).json()
        assert o["ok"] and o["users"] == 2 and o["linked"] == 0 and o["tool_calls"] >= 1
        assert len(o["series"]["tool"]) == 30
        u = (await b.http.get("/api/v1/admin/users")).json()
        by_sub = {x["sub"]: x for x in u["users"]}
        assert by_sub["user-2"]["active_tokens"] == 2  # access + refresh
        assert by_sub["user-2"]["clients"][0]["client_id"] == client["client_id"]
        assert by_sub["user-2"]["clients"][0]["name"] == "t"
        assert by_sub["admin-1"]["browser_sessions"] == 1 and not by_sub["admin-1"]["disabled"]
        met = (await b.http.get("/api/v1/admin/metrics?days=7")).json()
        assert met["ok"] and met["days"] == 7 and any(t["name"] == "whoami" for t in met["totals"])
        assert (await b.http.get("/api/v1/admin/metrics?days=x")).status_code == 400
    finally:
        await b.aclose()


# -- actions --------------------------------------------------------------------------
async def test_disable_cuts_off_tokens_refresh_and_sign_in_until_enabled(gw: Harness) -> None:
    client, tokens = await member_token(gw)
    assert (await whoami(gw, tokens["access_token"])).status_code == 200
    b = await admin_browser(gw)
    try:
        csrf = await b.csrf("/admin/users")
        r = await b.http.post("/admin/users/user-2", data={"csrf": csrf, "action": "disable"})
        assert r.status_code == 303 and r.headers["location"] == "/admin/users?ok=disable"
        assert gw.db.get_user("user-2")["disabled_at"]
        assert (await whoami(gw, tokens["access_token"])).status_code == 401
        refreshed = await gw.token(client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
        assert refreshed.status_code == 400 and refreshed.json()["error"] == "invalid_grant"
        # Signing in again is refused with the disabled message, and no session cookie is set.
        gw.idp.user = {
            "sub": "user-2",
            "email": "u2@example.test",
            "name": "u2",
            "preferred_username": "u2",
            "groups": ["mtg-users"],
        }
        b2 = Browser(gw)
        try:
            r = await b2.http.get("/login", params={"next": "/account"})
            cb = urlparse(await gw.idp_leg(r))
            refused = await b2.http.get(f"{cb.path}?{cb.query}")
            assert refused.status_code == 403 and "has been disabled" in refused.text
            assert "mtg_session=" not in refused.headers.get("set-cookie", "")
            # Enable: sign-in works again.
            r = await b.http.post("/admin/users/user-2", data={"csrf": csrf, "action": "enable"})
            assert r.status_code == 303 and r.headers["location"] == "/admin/users?ok=enable"
            await b2.login("/account")
        finally:
            await b2.aclose()
        page = await b.http.get("/admin/users", headers=NAVIGATE)
        assert "badge danger'>disabled" not in page.text
        events = [r["event"] for r in gw.db.audit_recent(50, sub="admin-1")]
        assert "admin_disable" in events and "admin_enable" in events
        row = next(r for r in gw.db.audit_recent(50) if r["event"] == "admin_disable")
        assert row["detail"]["target"] == "user-2" and row["detail"]["tokens"] == 2
        assert "login_rejected_disabled" in [r["event"] for r in gw.db.audit_recent(50, sub="user-2")]
    finally:
        await b.aclose()


async def test_token_whose_user_row_is_gone_is_refused(gw: Harness) -> None:
    _client, tokens = await member_token(gw)
    assert (await whoami(gw, tokens["access_token"])).status_code == 200
    gw.db._conn.execute("DELETE FROM users WHERE sub = 'user-2'")
    assert (await whoami(gw, tokens["access_token"])).status_code == 401


async def test_disabled_user_token_is_refused_at_load_and_audited_once(gw: Harness) -> None:
    _client, tokens = await member_token(gw)
    gw.db.set_user_disabled("user-2", True)  # the flag alone, no revocation: the load check must bite
    for _ in range(3):
        assert (await whoami(gw, tokens["access_token"])).status_code == 401
    refusals = [r for r in gw.db.audit_recent(50) if r["event"] == "disabled_user_refused"]
    assert len(refusals) == 1 and refusals[0]["sub"] == "user-2"
    # Browser sessions of a disabled user stop working too.
    gw.db.set_user_disabled("user-2", False)
    gw.idp.user = {
        "sub": "user-2",
        "email": None,
        "name": None,
        "preferred_username": "u2",
        "groups": ["mtg-users"],
    }
    b = Browser(gw)
    try:
        await b.login("/account")
        assert (await b.http.get("/account", headers=NAVIGATE)).status_code == 200
        gw.db.set_user_disabled("user-2", True)
        r = await b.http.get("/account", headers=NAVIGATE)
        assert r.status_code == 302 and r.headers["location"].startswith("/login")
    finally:
        await b.aclose()


async def test_revoke_drops_sessions_and_tokens(gw: Harness) -> None:
    _client, tokens = await member_token(gw)
    gw.idp.user = {
        "sub": "user-2",
        "email": None,
        "name": None,
        "preferred_username": "u2",
        "groups": ["mtg-users"],
    }
    member = Browser(gw)
    await member.login("/account")  # before the IdP is switched to the admin account
    admin = await admin_browser(gw)
    try:
        assert (await member.http.get("/account", headers=NAVIGATE)).status_code == 200
        csrf = await admin.csrf("/admin/users")
        r = await admin.http.post(
            "/api/v1/admin/users/user-2", json={"action": "revoke"}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 200, r.text
        assert r.json() == {
            "ok": True,
            "action": "revoke",
            "target": "user-2",
            "tokens": 2,
            "sessions": 1,
            "codes": 0,
        }
        r = await member.http.get("/account", headers=NAVIGATE)
        assert r.status_code == 302 and r.headers["location"].startswith("/login")
        assert (await whoami(gw, tokens["access_token"])).status_code == 401
        assert gw.db.get_user("user-2")["disabled_at"] is None  # revoke is not disable
        # A fresh sign-in works: the account is intact.
        gw.idp.user = {**gw.idp.user, "sub": "user-2", "groups": ["mtg-users"]}
        await member.login("/account")
    finally:
        await member.aclose()
        await admin.aclose()


async def test_unlink_and_the_api_guards(gw: Harness) -> None:
    await member_token(gw)
    gw.db.save_link("user-2", username="arch", user_id="9", secret_enc="x")
    assert gw.db.get_link("user-2")
    b = await admin_browser(gw)
    try:
        users = await b.http.get("/admin/users", headers=NAVIGATE)
        assert "arch" in users.text and "value='unlink'" in users.text
        # The API needs the CSRF header on writes and a JSON body.
        r = await b.http.post("/api/v1/admin/users/user-2", json={"action": "unlink"})
        assert r.status_code == 403 and r.json()["error"] == "csrf"
        csrf = await b.csrf("/admin/users")
        r = await b.http.post(
            "/api/v1/admin/users/user-2",
            content="action=unlink",
            headers={"X-CSRF-Token": csrf, "Content-Type": "text/plain"},
        )
        assert r.status_code == 415
        r = await b.http.post(
            "/api/v1/admin/users/user-2", json={"action": "explode"}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 400 and r.json()["error"] == "invalid"
        r = await b.http.post(
            "/api/v1/admin/users/nobody", json={"action": "revoke"}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 404 and r.json()["error"] == "not_found"
        r = await b.http.post(
            "/api/v1/admin/users/user-2", json={"action": "unlink"}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 200 and r.json()["ok"]
        assert gw.db.get_link("user-2") is None
        events = [r["event"] for r in gw.db.audit_recent(20)]
        assert "admin_unlink" in events and "archidekt_unlinked" in events
        # An admin cannot disable themself; a stale form token is refused.
        r = await b.http.post("/admin/users/admin-1", data={"csrf": csrf, "action": "disable"})
        assert r.status_code == 303 and r.headers["location"] == "/admin/users?err=self_disable"
        assert gw.db.get_user("admin-1")["disabled_at"] is None
        r = await b.http.post("/admin/users/user-2", data={"csrf": "nope", "action": "revoke"})
        assert r.status_code == 403
    finally:
        await b.aclose()


# -- database -------------------------------------------------------------------------
# The CREATE statements v0.1.0 shipped: today's base schema without the columns later steps add with
# ALTER TABLE (the snapshot backup columns already came that way), so those ALTERs really run here.
V010_SCHEMA = (
    dbmod.SCHEMA.replace(",\n    binding_hash TEXT\n", "\n")
    .replace(",\n    used_family TEXT\n", "\n")
    .replace(",\n    auth_time INTEGER\n", "\n")
    .replace(",\n    created_by_client TEXT\n", "\n")
)
assert V010_SCHEMA.count("binding_hash") == V010_SCHEMA.count("created_by_client") == 0


def test_v010_database_upgrades_in_place(tmp_path: Path) -> None:
    path = tmp_path / "old.sqlite"
    raw = sqlite3.connect(path)
    raw.executescript(V010_SCHEMA)
    raw.execute(
        "INSERT INTO users (sub, email, name, preferred_username, groups_json, first_login_at, last_login_at)"
        " VALUES ('u1', 'a@b', 'A', 'a', '[\"g\"]', 100, 200)"
    )
    raw.execute(
        "INSERT INTO snapshots (id, owner_sub, deck_id, taken_at, fingerprint, deck_json)"
        " VALUES ('s1', 'u1', 'd1', 1, 'fp', '{\"name\": \"Deck\", \"cards\": []}')"
    )
    raw.commit()
    assert raw.execute("PRAGMA user_version").fetchone()[0] == 0
    raw.close()

    db = Database(path)
    try:
        assert db.schema_version == dbmod.SCHEMA_VERSION == 9
        user = db.get_user("u1")
        assert user["disabled_at"] is None and user["last_seen_at"] == 200 and user["groups"] == ["g"]
        assert db.list_snapshots("u1")[0]["backup_url"] is None
        db.metrics_increment("2026-10-05", None, "tool", "whoami")
        db.metrics_increment("2026-10-05", None, "tool", "whoami", 2)
        assert db.metrics_totals(36500) == [{"kind": "tool", "name": "whoami", "n": 3}]
        assert db.set_user_disabled("u1", True) and db.get_user("u1")["disabled_at"]
        assert not db.set_user_disabled("nobody", True)
        users = db.list_users()
        assert (
            users[0]["sub"] == "u1"
            and users[0]["active_tokens"] == 0
            and users[0]["archidekt_username"] is None
        )
    finally:
        db.close()
    # Opening again is a no-op (nothing to migrate, nothing lost).
    again = Database(path)
    try:
        assert again.schema_version == dbmod.SCHEMA_VERSION and again.get_user("u1")["disabled_at"]
    finally:
        again.close()


def test_a_database_from_a_newer_gateway_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "newer.sqlite"
    Database(path).close()
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {dbmod.SCHEMA_VERSION + 1}")
    raw.close()
    with pytest.raises(RuntimeError, match="newer than this gateway supports"):
        Database(path)


def test_user_clients_and_revoke_all(tmp_path: Path) -> None:
    db = Database(tmp_path / "x.sqlite")
    try:
        db.upsert_user("u1", email=None, name=None, preferred_username="u", groups=[])
        db.save_client("c1", {"client_name": "Claude"})
        for i, kind in enumerate(("access", "refresh")):
            db.save_token(
                f"t{i}",
                kind=kind,
                client_id="c1",
                sub="u1",
                scopes=["mtg"],
                resource=None,
                family="f1",
                expires_at=2**31,
            )
        db.save_token(
            "t9", kind="access", client_id="c2", sub="u1", scopes=[], resource=None, family="f2", expires_at=1
        )  # expired: not a connected app
        db.save_token(
            "other",
            kind="access",
            client_id="c1",
            sub="u2",
            scopes=[],
            resource=None,
            family="f3",
            expires_at=2**31,
        )
        db.create_browser_session("sid", "u1", 3600)
        clients = db.user_clients("u1")
        assert [c["client_id"] for c in clients] == ["c1"] and clients[0]["name"] == "Claude"
        assert clients[0]["tokens"] == 2
        assert db.revoke_all_for_user("u1") == {"tokens": 3, "sessions": 1, "codes": 0}
        assert db.get_token("other", "access") is not None  # another user's token is untouched
        assert db.user_clients("u1") == [] and db.get_browser_session("sid") is None
        db.audit("x", sub="u1", detail={"a": 1})
        db.audit("y", sub="u2")
        assert [r["event"] for r in db.audit_recent(10)] == ["y", "x"]
        assert db.audit_for_user("u1", 10)[0]["detail"] == {"a": 1}
        counts = db.overview_counts()
        assert counts["users"] == 1 and counts["disabled"] == 0 and counts["proposals"] == {}
    finally:
        db.close()


# -- config -----------------------------------------------------------------------------
def _env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from cryptography.fernet import Fernet

    (tmp_path / "fernet").write_text(Fernet.generate_key().decode())
    (tmp_path / "session").write_text("s" * 40)
    (tmp_path / "oidc").write_text("secret")
    monkeypatch.setenv("MTG_FERNET_KEY_FILE", str(tmp_path / "fernet"))
    monkeypatch.setenv("MTG_SESSION_SECRET_FILE", str(tmp_path / "session"))
    monkeypatch.setenv("MTG_OIDC_CLIENT_SECRET_FILE", str(tmp_path / "oidc"))
    monkeypatch.setenv("MTG_PUBLIC_URL", "https://mtg.example.test")
    monkeypatch.setenv("MTG_OIDC_ISSUER", "https://auth.example.test/application/o/mtg/")
    monkeypatch.setenv("MTG_OIDC_CLIENT_ID", "abc")
    monkeypatch.setenv("MTG_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("MTG_REQUIRED_GROUP", "mtg-users")


def test_admin_group_and_assetlinks_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _env(tmp_path, monkeypatch)
    s = load_settings()
    assert s.admin_group is None and s.android_assetlinks is None
    assert s.cimd_enabled is True and s.writes_enabled is False and s.archidekt_backups is True
    monkeypatch.setenv("MTG_ADMIN_GROUP", "mtg-admins")
    monkeypatch.setenv(
        "MTG_ANDROID_ASSETLINKS", '[{"relation": ["delegate_permission/common.handle_all_urls"]}]'
    )
    monkeypatch.setenv("MTG_WRITES_ENABLED", "YES")
    monkeypatch.setenv("MTG_CIMD_ENABLED", "0")
    monkeypatch.setenv("MTG_ARCHIDEKT_BACKUPS", "")
    s = load_settings()
    assert s.admin_group == "mtg-admins"
    assert s.android_assetlinks == '[{"relation":["delegate_permission/common.handle_all_urls"]}]'
    assert s.writes_enabled is True and s.cimd_enabled is False and s.archidekt_backups is True
    for bad in ("not json", '{"a": 1}', "42"):
        monkeypatch.setenv("MTG_ANDROID_ASSETLINKS", bad)
        with pytest.raises(ConfigError, match="MTG_ANDROID_ASSETLINKS must be a JSON list"):
            load_settings()
