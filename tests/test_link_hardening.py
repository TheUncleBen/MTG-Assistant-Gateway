"""The 0.7.7 hardening of stored Archidekt sessions: no cookies kept between members, no response
headers in the logs, no link stored for an account disabled while it signed in, no session in a
backup file, the browser named on link entries, and the start-up and hourly wiring of the reseal,
expiry purge and removed-member clean-up."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sqlite3
import time
from pathlib import Path

import httpx
import pytest
from mcp import Client

from mtg_gateway import backup
from mtg_gateway.archidekt import ArchidektClient, Pacer, _NoCookies
from mtg_gateway.db import Database
from mtg_gateway.mf_proxy import MysticForgeProxy
from mtg_gateway.pages import BROWSER_CLIENT_ID

from .conftest import FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser, Stack, _client, fake_mystic_forge


def _jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.sig"


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP):
    from .fake_archidekt import FakeArchidekt

    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, approval_mode_default="auto", archidekt_base="https://ark.test/api"
    )
    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(fake_mystic_forge()))
    async with running(Harness(settings, idp, archidekt=_client(settings, ark), mf_proxy=proxy)) as h:
        yield Stack(h, ark)


# -- cookies --------------------------------------------------------------------------------------
async def test_a_cookie_archidekt_sets_at_sign_in_is_never_sent_again() -> None:
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("cookie"))
        if request.url.path.endswith("/rest-auth/login/"):
            return httpx.Response(
                200,
                json={"access_token": "acc-a", "refresh_token": "ref-a", "user": {"id": 1, "username": "a"}},
                headers={"set-cookie": "sessionid=member-a-cookie; Domain=ark.test; Path=/"},
            )
        return httpx.Response(200, json={"results": [], "next": None, "count": 0})

    real = ArchidektClient("https://ark.test/api", "test-agent", Pacer(0.0))
    try:
        # the shared client the gateway builds keeps no cookies...
        assert isinstance(real._http.cookies.jar, _NoCookies)
    finally:
        await real.aclose()
    # ...and that jar drops one Archidekt sets, so the next request (another member's) has none
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler), cookies=_NoCookies())
    client = ArchidektClient("https://ark.test/api", "test-agent", Pacer(0.0), http=http)
    try:
        await client.login("a", "pw-a")
        await http.get("https://ark.test/api/decks/v3/")
    finally:
        await client.aclose()
    assert seen == [None, None]
    assert len(http.cookies.jar) == 0


# -- logs -----------------------------------------------------------------------------------------
@pytest.mark.parametrize("level", ["INFO", "DEBUG"])
def test_response_headers_are_never_logged(
    level: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from mtg_gateway.__main__ import main

    from .test_backup_and_config import _write_secrets

    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_LOG_LEVEL", level)
    loggers = [logging.getLogger(n) for n in ("httpx", "httpcore")]
    before = [lg.level for lg in loggers]
    try:
        assert main(["check-config"]) == 0
        assert logging.getLogger("httpcore").level == logging.WARNING
        assert logging.getLogger("httpx").level == (logging.NOTSET if level == "DEBUG" else logging.WARNING)
    finally:
        for lg, lvl in zip(loggers, before, strict=True):
            lg.setLevel(lvl)


# -- linking --------------------------------------------------------------------------------------
@pytest.mark.parametrize("what", ["disabled", "deleted"])
async def test_an_account_disabled_or_deleted_while_signing_in_stores_nothing(
    what: str, stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    h = stack.h
    b = Browser(h)
    await b.login()
    decks = h.app.state.gateway.decks
    real = decks._link_attempt

    async def slow_sign_in(sub: str, login: str, password: str):
        session = await real(sub, login, password)
        if what == "disabled":
            h.db.set_user_disabled(sub, True)
        else:
            h.db._conn.execute("DELETE FROM users WHERE sub = ?", (sub,))
        return session

    monkeypatch.setattr(decks, "_link_attempt", slow_sign_in)
    with pytest.raises(Exception) as e:
        await decks.link("user-1", "alice", "pw-alice")
    assert getattr(e.value, "kind", None) == "forbidden"
    assert h.db._conn.execute("SELECT COUNT(*) FROM archidekt_links").fetchone()[0] == 0
    await b.aclose()


def test_save_link_for_members_only_refuses_unknown_and_disabled(tmp_path: Path) -> None:
    db = Database(tmp_path / "gw.sqlite")
    try:
        assert db.save_link("ghost", username="g", user_id=None, secret_enc="x", only_member=True) is False
        db.upsert_user("u", email=None, name=None, preferred_username=None, groups=[])
        db.set_user_disabled("u", True)
        assert db.save_link("u", username="u", user_id=None, secret_enc="x", only_member=True) is False
        assert db._conn.execute("SELECT COUNT(*) FROM archidekt_links").fetchone()[0] == 0
        db.set_user_disabled("u", False)
        assert db.save_link("u", username="u", user_id=None, secret_enc="x", only_member=True) is True
    finally:
        db.close()


async def test_a_link_from_the_account_page_is_logged_as_the_browser(stack: Stack) -> None:
    h = stack.h
    b = Browser(h)
    await b.login()
    assert (await b.link("alice", "pw-alice")).status_code == 303
    row = h.db._conn.execute("SELECT client_id FROM audit_log WHERE event = 'archidekt_linked'").fetchone()
    assert row[0] == BROWSER_CLIENT_ID
    await b.aclose()


# -- backups --------------------------------------------------------------------------------------
def test_a_backup_file_never_holds_a_stored_session(tmp_path: Path) -> None:
    db = Database(tmp_path / "gw.sqlite")
    try:
        db.upsert_user("u", email=None, name=None, preferred_username=None, groups=[])
        db.save_link("u", username="u", user_id="1", secret_enc="SESSION-MARKER-" + "z" * 200)
        out = tmp_path / "b" / "copy.sqlite"
        db.backup_to(out)
        assert b"SESSION-MARKER" not in out.read_bytes()
        assert not (tmp_path / "b" / "copy.sqlite-wal").exists()
        copy = sqlite3.connect(str(out))
        try:
            assert copy.execute("SELECT status, secret_enc FROM archidekt_links").fetchone() == (
                "revoked",
                "",
            )
        finally:
            copy.close()
        assert db.get_link("u")["secret_enc"].startswith("SESSION-MARKER")  # the live one is untouched
    finally:
        db.close()


# -- wiring ---------------------------------------------------------------------------------------
async def test_the_hourly_purge_also_runs_the_expired_session_purge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = Database(tmp_path / "gw.sqlite")
    calls: list[str] = []
    rounds = 0

    async def fake_sleep(_delay: float) -> None:
        nonlocal rounds
        rounds += 1
        if rounds > 2:
            raise asyncio.CancelledError

    def failing() -> None:
        calls.append("also")
        raise RuntimeError("boom")

    monkeypatch.setattr(backup.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(db, "purge_expired", lambda: calls.append("db"))
    try:
        with pytest.raises(asyncio.CancelledError):
            await backup.purge_loop(db, 1.0, also=failing)
    finally:
        db.close()
    assert calls == ["db", "also", "db", "also"]  # a failure is retried next round


async def test_start_up_seals_old_sessions_and_deletes_expired_ones(
    tmp_path: Path, idp: FakeIdP, caplog: pytest.LogCaptureFixture
) -> None:
    from cryptography.fernet import Fernet

    from .fake_archidekt import FakeArchidekt

    settings = make_settings(
        tmp_path,
        authentik_api_token="tok",
        authentik_api_url="https://idp.test",
        required_group="mtg-gateway-users",
    )
    h = Harness(settings, idp, archidekt=_client(settings, FakeArchidekt()))
    f = Fernet(settings.fernet_key.encode())
    now = int(time.time())
    for sub, refresh_exp in (("old", now + 999), ("expired", now - 5)):
        h.db.upsert_user(sub, email=None, name=None, preferred_username=None, groups=["mtg-gateway-users"])
        legacy = json.dumps({"access": _jwt({"exp": now - 10}), "refresh": _jwt({"exp": refresh_exp})})
        h.db.save_link(sub, username=sub, user_id="1", secret_enc=f.encrypt(legacy.encode()).decode())
    caplog.set_level(logging.INFO)
    async with running(h):
        assert h.app.state.gateway.sweep.enabled
        sealed = json.loads(f.decrypt(h.db.get_link("old")["secret_enc"].encode()))
        assert sealed["p"] == "archidekt_session" and sealed["s"] == "old"
        assert h.db.get_link("expired") is None
    assert "2 stored Archidekt session(s) sealed" in caplog.text
    assert (
        "removed-member clean-up is on: hourly, asking https://idp.test about mtg-gateway-users"
        in caplog.text
    )
    assert "tok" not in caplog.text.replace("token", "")


async def test_start_up_warns_once_when_the_sweep_token_cannot_be_read(
    tmp_path: Path, idp: FakeIdP, caplog: pytest.LogCaptureFixture
) -> None:
    settings = make_settings(
        tmp_path, required_group="g", authentik_api_token_problem="secret file for X is empty"
    )
    h = Harness(settings, idp)
    caplog.set_level(logging.WARNING)
    async with running(h):
        assert not h.app.state.gateway.sweep.enabled
    assert caplog.text.count("removed-member clean-up is off") == 1
