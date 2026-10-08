"""Linking Archidekt: the explicit disclosure (shown before linking, acknowledged with a required
tick, readable afterwards), and the stored session's handling: sealed to its member, its real
expiry shown and enforced, a relink starting fresh, and nothing of it in the logs."""

from __future__ import annotations

import base64
import html
import json
import logging
import time
from pathlib import Path

import pytest
from mcp import Client

from mtg_gateway import link_disclosure, pages
from mtg_gateway.mf_proxy import MysticForgeProxy

from .conftest import FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser, Stack, _client, call, fake_mystic_forge, linked_user, structured


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


def _jwt(claims: dict) -> str:
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).rstrip(b"=").decode()
    return f"eyJhbGciOiJIUzI1NiJ9.{payload}.sig"


def _decks(stack: Stack):
    return stack.h.app.state.gateway.decks


def _raw(stack: Stack, sub: str) -> dict:
    row = stack.h.db.get_link(sub)
    assert row is not None
    return json.loads(_decks(stack).fernet.decrypt(row["secret_enc"].encode()))


# -- the disclosure -------------------------------------------------------------------------------
def test_disclosure_names_what_the_operator_and_admins_can_and_cannot_do() -> None:
    text = link_disclosure.plain_text()
    for must in (
        "Your password is not stored anywhere by the gateway.",
        "the key that opens it is on the same server",
        "act as you on Archidekt until it expires",
        "They cannot get your password from the session.",
        "They control the code this server runs.",
        "Link only if you trust the person who runs this server.",
        "cannot open your proposals",
        "The gateway never writes your password or your session to its logs.",
        "The gateway's own backups leave your session out",
        "about 40 days after you link",
        "Whether the session also stops working on Archidekt's side is not known",
        "Whether changing your Archidekt password ends it is not known either.",
        "deleted within about an hour if the person who runs the gateway turned on its hourly clean-up",
        "the next time you or one of your apps tries to use the gateway",
        "Archidekt's terms of service restrict automated access",
    ):
        assert must in text, must
    # never claims what is not backed by code and tests
    assert "secure" not in text.lower()
    # no switch hides it any more
    assert not hasattr(pages, "SHOW_LINK_TRUST_NOTE") and not hasattr(pages, "LINK_TRUST_NOTE")


async def test_disclosure_is_shown_in_full_and_must_be_ticked_before_linking(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    b = Browser(h)
    await b.login()
    page = (await b.http.get("/account")).text
    assert "Link your Archidekt account" in page
    assert "<div class='notice disclosure' id='archidekt-disclosure'>" in page
    for heading, lines in link_disclosure.SECTIONS:
        assert html.escape(heading) in page
        for line in lines:
            assert html.escape(line) in page, line
    assert html.escape(link_disclosure.ACKNOWLEDGE) in page
    assert "name='accept_risk' value='1' required" in page
    # the form comes after the disclosure, so it is read first
    assert page.index("archidekt-disclosure") < page.index("name='archidekt_password'")

    csrf = await b.csrf()
    r = await b.http.post(
        "/account",
        data={"csrf": csrf, "action": "link", "archidekt_login": "alice", "archidekt_password": "pw-alice"},
    )
    assert r.status_code == 400 and "Tick the box" in r.text
    assert not any(path.endswith("/rest-auth/login/") for _m, path in ark.calls)
    assert h.db.get_link("user-1") is None
    await b.aclose()


async def test_disclosure_stays_readable_on_the_account_page_after_linking(stack: Stack) -> None:
    h = stack.h
    b = Browser(h)
    await b.login()
    assert (await b.link("alice", "pw-alice")).status_code == 303
    page = (await b.http.get("/account")).text
    assert "Linked to <strong>alice</strong>" in page
    assert "<details class='disclosure' id='archidekt-disclosure'>" in page
    assert html.escape(link_disclosure.SECTIONS[3][1][0]) in page
    assert "Unlink and delete stored session" in page
    await b.aclose()


# -- the stored session ---------------------------------------------------------------------------
async def test_stored_session_is_sealed_to_its_member(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    secret = _raw(stack, "user-1")
    assert secret["p"] == "archidekt_session" and secret["s"] == "user-1"
    assert "pw-alice" not in json.dumps(secret)
    # The same ciphertext under another member's link opens nothing.
    h.db.save_link("user-2", username="alice", user_id="77", secret_enc=h.db.get_link("user-1")["secret_enc"])
    decks = _decks(stack)
    with pytest.raises(Exception) as e:
        decks._token("user-2")
    assert getattr(e.value, "kind", None) == "not_linked"
    assert decks.status("user-2")["link_expires_at"] is None
    # ...while the owner still works
    assert structured(await call(h, token, "list_my_decks"))["ok"] is True


async def test_session_stored_before_sealing_is_refused_until_resealed(stack: Stack) -> None:
    """An unsealed blob (0.7.6 and earlier) is sealed at start to the row that holds it then;
    until then it is not used. A blob sealed to someone else is never resealed."""
    h = stack.h
    token = await linked_user(stack)
    decks = _decks(stack)
    secret = _raw(stack, "user-1")
    legacy = json.dumps({"access": secret["access"], "refresh": secret["refresh"]})
    # as on a database from 0.7.6, before the first start of a sealing gateway
    h.db._conn.execute("DELETE FROM audit_log WHERE event = 'archidekt_sessions_sealed'")
    h.db.update_link_secret("user-1", decks.fernet.encrypt(legacy.encode()).decode())
    foreign = h.db.get_link("user-1")["secret_enc"]
    sealed_to_1 = decks._seal("user-1", secret["access"], secret["refresh"])
    h.db.save_link("user-2", username="x", user_id="1", secret_enc=sealed_to_1)
    assert structured(await call(h, token, "list_my_decks"))["ok"] is False
    assert decks.reseal_legacy_links() == 1
    assert _raw(stack, "user-1")["s"] == "user-1" and h.db.get_link("user-1")["secret_enc"] != foreign
    assert h.db.get_link("user-2")["secret_enc"] == sealed_to_1  # left alone, and still unusable
    assert decks.status("user-2")["link_expires_at"] is None
    assert structured(await call(h, token, "list_my_decks"))["ok"] is True
    assert decks.reseal_legacy_links() == 0
    # Only the first start reseals: an unsealed blob found later is deleted, not trusted.
    h.db.update_link_secret("user-1", decks.fernet.encrypt(legacy.encode()).decode())
    assert decks.reseal_legacy_links() == 0
    assert h.db.get_link("user-1") is None
    assert h.db.get_link("user-2")["secret_enc"] == sealed_to_1  # sealed blobs are left alone
    events = [r[0] for r in h.db._conn.execute("SELECT event FROM audit_log").fetchall()]
    assert events.count("archidekt_sessions_sealed") == 1 and "archidekt_link_unsealed" in events


async def test_expiry_comes_from_the_refresh_token_and_is_shown(stack: Stack) -> None:
    h = stack.h
    b = Browser(h)
    await b.login()
    assert (await b.link("alice", "pw-alice")).status_code == 303
    decks = _decks(stack)
    exp = int(time.time()) + 40 * 86400
    access_exp = int(time.time()) + 3600
    access = _jwt({"exp": access_exp})
    h.db.update_link_secret(
        "user-1", decks._seal("user-1", access, _jwt({"exp": exp, "token_type": "refresh"}))
    )
    assert decks.status("user-1")["link_expires_at"] == exp
    page = (await b.http.get("/account")).text
    assert f"Archidekt's session stops working on {pages._when(exp)}" in page
    # without a refresh token, the access token's own expiry is the end
    h.db.update_link_secret("user-1", decks._seal("user-1", access, None))
    assert decks.status("user-1")["link_expires_at"] == access_exp
    # an opaque refresh token: unknown, and no date is shown
    h.db.update_link_secret("user-1", decks._seal("user-1", access, "ref-alice"))
    assert decks.status("user-1")["link_expires_at"] is None
    assert "session stops working" not in (await b.http.get("/account")).text
    await b.aclose()


async def test_expired_sessions_are_deleted_by_the_hourly_purge(stack: Stack) -> None:
    h = stack.h
    decks = _decks(stack)
    now = int(time.time())
    access = _jwt({"exp": now - 10})
    h.db.save_link(
        "user-1", username="a", user_id="1", secret_enc=decks._seal("user-1", access, _jwt({"exp": now - 5}))
    )
    h.db.save_link(
        "user-2",
        username="b",
        user_id="2",
        secret_enc=decks._seal("user-2", access, _jwt({"exp": now + 999})),
    )
    h.db.save_link("user-3", username="c", user_id="3", secret_enc=decks._seal("user-3", access, "ref-c"))
    assert decks.purge_expired_links() == 1
    row = h.db._conn.execute("SELECT status, secret_enc FROM archidekt_links WHERE sub = 'user-1'").fetchone()
    assert tuple(row) == ("revoked", "")
    assert h.db.get_link("user-2") is not None and h.db.get_link("user-3") is not None
    assert decks.purge_expired_links(now + 1000) == 1  # user-2 later; unknown expiry is kept
    assert h.db.get_link("user-3") is not None
    events = h.db._conn.execute(
        "SELECT detail_json FROM audit_log WHERE event = 'archidekt_link_expired'"
    ).fetchall()
    assert [json.loads(r[0])["reason"] for r in events] == ["session expired", "session expired"]


async def test_relink_starts_the_link_over(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    h.db._conn.execute("UPDATE archidekt_links SET created_at = 1000 WHERE sub = 'user-1'")
    assert structured(await call(h, token, "list_my_decks"))["ok"] is True
    assert h.db.get_link("user-1")["last_used_at"] is not None
    old = h.db.get_link("user-1")["secret_enc"]
    b = Browser(h)
    await b.login()
    assert (await b.link("alice", "pw-alice")).status_code == 303
    row = h.db.get_link("user-1")
    assert row["created_at"] > 1000 and row["last_used_at"] is None and row["secret_enc"] != old
    await b.aclose()


async def test_password_and_tokens_never_reach_the_logs(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    h, ark = stack.h, stack.ark
    caplog.set_level(logging.DEBUG)  # every logger, httpx included
    b = Browser(h)
    await b.login()
    assert (await b.link("alice", "wrong-pw-marker")).status_code == 400
    assert (await b.link("alice", "pw-alice")).status_code == 303
    from .test_decks_and_proxy import mcp_token

    token = await mcp_token(h)
    assert structured(await call(h, token, "list_my_decks"))["ok"] is True
    issued = set(ark.tokens)
    ark.expire_access_tokens()  # forces a refresh on the next use
    assert structured(await call(h, token, "list_my_decks"))["ok"] is True
    assert ark.refresh_calls >= 1
    csrf = await b.csrf()
    assert (await b.http.post("/account", data={"csrf": csrf, "action": "unlink"})).status_code == 303
    await b.aclose()
    logged = caplog.text
    assert logged  # the run did log at DEBUG
    assert "ref-alice" in issued
    for secret in ("pw-alice", "wrong-pw-marker", *issued, *ark.tokens):
        assert secret not in logged, secret


def test_the_guide_for_members_carries_the_same_text() -> None:
    doc = (Path(__file__).resolve().parent.parent / "docs" / "USING.md").read_text(encoding="utf-8")
    for _heading, lines in link_disclosure.SECTIONS:
        for line in lines:
            assert f"- {line}" in doc, line
    assert link_disclosure.TERMS_NOTE in doc and link_disclosure.ACKNOWLEDGE in doc
