"""Archidekt session refresh: proactive near expiry, reactive on 401, revoke only when the
refresh token is missing or rejected."""

from __future__ import annotations

import base64
import json
import time
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from mcp import Client

from mtg_gateway.archidekt import ArchidektClient, ArchidektError, Pacer, jwt_exp
from mtg_gateway.mf_proxy import MysticForgeProxy

from .conftest import FakeIdP, Harness, make_settings, running
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Stack, _client, call, fake_mystic_forge, linked_user, structured


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, approval_mode_default="auto", archidekt_base="https://ark.test/api"
    )
    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(fake_mystic_forge()))
    async with running(Harness(settings, idp, archidekt=_client(settings, ark), mf_proxy=proxy)) as h:
        yield Stack(h, ark)


def _fernet(stack: Stack) -> Fernet:
    return Fernet(stack.h.settings.fernet_key.encode())


def _jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.sig"


def test_jwt_exp_reads_the_claim_without_verifying() -> None:
    assert jwt_exp(_jwt({"exp": 1_800_000_000, "iat": 1})) == 1_800_000_000
    assert jwt_exp(_jwt({"exp": 1_800_000_000.0})) == 1_800_000_000
    assert jwt_exp(_jwt({"iat": 1})) is None
    assert jwt_exp(_jwt({"exp": "soon"})) is None
    assert jwt_exp(_jwt({"exp": True})) is None
    assert jwt_exp("opaque-token") is None
    assert jwt_exp("a.b.c") is None
    assert jwt_exp("a.!!!.c") is None
    assert jwt_exp("") is None


async def test_client_refresh_returns_new_access_and_maps_rejections_to_auth() -> None:
    ark = FakeArchidekt()
    client = ArchidektClient("https://ark.test/api", "t", Pacer(0.0), http=ark.client())
    session = await client.login("alice", "pw-alice")
    new = await client.refresh(session["refresh"])
    assert new != session["access"] and ark.tokens[new] == "alice"
    assert jwt_exp(new) is not None and jwt_exp(new) > time.time() + 3000
    assert ark.calls[-1] == ("POST", "/api/rest-auth/token/refresh/")
    with pytest.raises(ArchidektError) as e:
        await client.refresh("ref-nobody")
    assert e.value.kind == "auth"


async def test_client_refresh_treats_400_as_auth_and_bad_shape_as_contract() -> None:
    answers = iter(
        [
            httpx.Response(400, json={"refresh": ["This field may not be blank."]}),
            httpx.Response(200, json={"ok": True}),
        ]
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda req: next(answers)))
    client = ArchidektClient("https://ark.test/api", "t", Pacer(0.0), http=http)
    with pytest.raises(ArchidektError) as e:
        await client.refresh("")
    assert e.value.kind == "auth"
    with pytest.raises(ArchidektError) as e:
        await client.refresh("ref-x")
    assert e.value.kind == "contract"


def _secret(stack: Stack, sub: str = "user-1") -> dict:
    row = stack.h.db.get_link(sub)
    assert row is not None
    return json.loads(_fernet(stack).decrypt(row["secret_enc"].encode()).decode())


def _audit_events(stack: Stack, event: str) -> list[dict]:
    rows = stack.h.db._conn.execute(
        "SELECT detail_json FROM audit_log WHERE event = ? ORDER BY id", (event,)
    ).fetchall()
    return [json.loads(r[0]) if r[0] else {} for r in rows]


async def test_proactive_refresh_when_access_token_is_about_to_expire(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    ark.access_ttl = 120  # the login token is already inside the five-minute window
    token = await linked_user(stack)
    old_access = _secret(stack)["access"]
    assert ark.refresh_calls == 0
    ark.access_ttl = 3600  # the refreshed token is a normal hour-long one
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"] is True, out
    assert ark.refresh_calls == 1
    secret = _secret(stack)
    assert secret["access"] != old_access and secret["refresh"] == "ref-alice"
    assert ark.tokens[secret["access"]] == "alice"
    events = _audit_events(stack, "archidekt_session_refreshed")
    assert len(events) == 1 and events[0]["reason"] == "expiring"
    # the stored token is good for an hour now: no refresh on the next call
    assert structured(await call(h, token, "list_my_decks"))["ok"] is True
    assert ark.refresh_calls == 1
    row = h.db.get_link("user-1")
    assert row is not None and row["archidekt_username"] == "alice" and row["archidekt_user_id"] == "77"


async def test_unreadable_access_token_is_refreshed_before_use(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    row = h.db.get_link("user-1")
    blob = json.dumps({"access": "opaque-not-a-jwt", "refresh": "ref-alice"})
    h.db.save_link(
        "user-1",
        username=row["archidekt_username"],
        user_id=row["archidekt_user_id"],
        secret_enc=_fernet(stack).encrypt(blob.encode()).decode(),
    )
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"] is True, out
    assert ark.refresh_calls == 1
    assert _secret(stack)["access"] != "opaque-not-a-jwt"


async def test_reactive_refresh_on_401_then_retry_succeeds(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    old_access = _secret(stack)["access"]
    ark.expire_access_tokens()  # the provider forgot the access token but still honours the refresh token
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"] is True, out
    assert ark.refresh_calls == 1
    assert [c for c in ark.calls if c[1] == "/api/decks/v3/"], ark.calls
    assert _secret(stack)["access"] != old_access
    assert h.db.get_link("user-1") is not None
    events = _audit_events(stack, "archidekt_session_refreshed")
    assert len(events) == 1 and events[0]["reason"] == "rejected"
    assert _audit_events(stack, "archidekt_link_expired") == []


async def test_expired_access_token_by_clock_is_refreshed(stack: Stack) -> None:
    """The fake rejects an access token whose exp has passed, like the real site."""
    h, ark = stack.h, stack.ark
    ark.access_ttl = 1
    token = await linked_user(stack)
    time.sleep(1.1)
    ark.access_ttl = 3600
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"] is True, out
    assert ark.refresh_calls >= 1 and h.db.get_link("user-1") is not None


async def test_refresh_failure_revokes_the_link(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.tokens.clear()  # the provider forgot the whole session, refresh token included
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"] is False and out["error"] == "not_linked" and "Relink" in out["message"]
    assert ark.refresh_calls == 1
    assert h.db.get_link("user-1") is None
    events = _audit_events(stack, "archidekt_link_expired")
    assert len(events) == 1 and events[0]["reason"] == "rejected, refresh rejected"
    assert _audit_events(stack, "archidekt_session_refreshed") == []


async def test_no_refresh_token_revokes_as_before(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    ark.issue_refresh = False
    token = await linked_user(stack)
    assert _secret(stack)["refresh"] is None
    ark.expire_access_tokens()
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"] is False and out["error"] == "not_linked"
    assert ark.refresh_calls == 0
    assert h.db.get_link("user-1") is None
    events = _audit_events(stack, "archidekt_link_expired")
    assert len(events) == 1 and events[0]["reason"] == "rejected, no refresh token"


async def test_refresh_outage_is_reported_not_treated_as_relink(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.expire_access_tokens()
    real = ark.handle

    def outage(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/rest-auth/token/refresh/":
            return httpx.Response(503, json={"detail": "down"})
        return real(request)

    ark.transport.handler = outage
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"] is False and out["error"] == "unavailable", out
    assert h.db.get_link("user-1") is not None  # a provider outage is not a dead link


async def test_refresh_that_races_an_unlink_does_not_resurrect_the_link(stack: Stack) -> None:
    """An unlink (by the user or an admin) while a refresh is in flight must win: the refreshed
    session is thrown away instead of being saved over the revoked row."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.expire_access_tokens()
    service = h.app.state.gateway.decks
    real_refresh = service.client.refresh

    async def refresh_then_unlink(refresh_token: str) -> str:
        access = await real_refresh(refresh_token)
        h.db.revoke_link("user-1")  # the unlink lands while the refresh is in flight
        return access

    service.client.refresh = refresh_then_unlink  # type: ignore[method-assign]
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"] is False and out["error"] == "not_linked", out
    assert h.db.get_link("user-1") is None
    assert _audit_events(stack, "archidekt_session_refreshed") == []
