"""The hourly removed-member clean-up (idp_sweep.py), against Authentik's real answers.

tests/fixtures/authentik-2026.8.3/ holds answers recorded from a real Authentik 2026.8.3, asked
with a token whose user holds only ``authentik_core.view_group``: the two groups, a group name
that does not exist, and the answer without that permission. In it alice is an active member of
the users group, bob a deactivated one, carol an active admin, and dave (not recorded) is in no
group. The fake below serves those files unchanged; the first test proves it."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from mtg_gateway import idp_sweep
from mtg_gateway.config import load_settings
from mtg_gateway.db import Database
from mtg_gateway.idp_sweep import AuthentikSweep, SweepRefused, members, plan

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "authentik-2026.8.3"
USERS, ADMINS = "mtg-gateway-users", "mtg-gateway-admins"
ALICE = "b31e4760f095522f8bd4d2743a4b44466b8bc93783b00ad80abefda185615c2a"
BOB = "3ae3158df9f92bf19bb280da288487a41b659e38427d3b69735b8ee08d2e3011"
CAROL_PREFIX = "2d7b2b263c4f"
DAVE = "dave-in-no-group"
TOKEN = "ak-token-marker-0123456789"
API = "https://auth.example.test"


def _recorded(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _carol() -> str:
    body = json.loads(_recorded("groups-mtg-gateway-admins.json"))
    return body["results"][0]["users_obj"][0]["uid"]


class RecordedAuthentik:
    """Serves the recorded answers byte for byte; anything else is an override."""

    def __init__(self, token: str = TOKEN):
        self.token = token
        self.requests: list[httpx.Request] = []
        self.override: dict[str, httpx.Response | Exception] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        name = request.url.params.get("name", "")
        if name in self.override:
            o = self.override[name]
            if isinstance(o, Exception):
                raise o
            return o
        if request.headers.get("authorization") != f"Bearer {self.token}":
            return httpx.Response(403, content=_recorded("groups-no-permission.json"))
        if request.url.path != "/api/v3/core/groups/":
            return httpx.Response(404)
        file = {USERS: "groups-mtg-gateway-users.json", ADMINS: "groups-mtg-gateway-admins.json"}.get(
            name, "groups-missing.json"
        )
        return httpx.Response(200, content=_recorded(file), headers={"content-type": "application/json"})

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=httpx.MockTransport(self.handler))


def _settings(token: str | None = TOKEN, **over) -> SimpleNamespace:
    base = dict(
        authentik_api_url=API,
        authentik_api_token=token,
        authentik_api_token_problem=None,
        required_group=USERS,
        admin_group=ADMINS,
    )
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def db(tmp_path: Path):
    d = Database(tmp_path / "gw.sqlite")
    for sub in (ALICE, BOB, _carol(), DAVE):
        d.upsert_user(sub, email=None, name=sub[:5], preferred_username=sub[:5], groups=[USERS])
        assert d.save_link(sub, username=sub[:5], user_id="1", secret_enc=f"sealed-{sub[:5]}")
    yield d
    d.close()


def _linked(db: Database) -> set[str]:
    return {row["sub"] for row in db.active_links()}


# -- the fake is the real thing -------------------------------------------------------------------
async def test_the_fake_serves_the_recorded_answers_and_the_parser_reads_their_real_fields() -> None:
    ak = RecordedAuthentik()
    async with ak.client() as http:
        for name, file in (
            (USERS, "groups-mtg-gateway-users.json"),
            (ADMINS, "groups-mtg-gateway-admins.json"),
        ):
            r = await http.get(
                f"{API}/api/v3/core/groups/",
                params={"name": name},
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            assert r.status_code == 200 and r.content == _recorded(file)
        r = await http.get(
            f"{API}/api/v3/core/groups/",
            params={"name": "nope"},
            headers={"Authorization": f"Bearer {TOKEN}"},
        )
        assert r.content == _recorded("groups-missing.json")
        r = await http.get(
            f"{API}/api/v3/core/groups/", params={"name": USERS}, headers={"Authorization": "Bearer wrong"}
        )
        assert r.status_code == 403 and r.content == _recorded("groups-no-permission.json")
    # the recorded answer has the fields the sweep relies on, as Authentik sends them
    raw = json.loads(_recorded("groups-mtg-gateway-users.json"))
    user = raw["results"][0]["users_obj"][0]
    assert {"uid", "is_active", "username", "pk"} <= set(user)
    assert members(raw, USERS) == [{"uid": ALICE, "is_active": True}, {"uid": BOB, "is_active": False}]
    assert members(json.loads(_recorded("groups-mtg-gateway-admins.json")), ADMINS)[0]["uid"].startswith(
        CAROL_PREFIX
    )


# -- what it removes ------------------------------------------------------------------------------
def test_plan_keeps_active_members_and_admins_and_removes_the_rest() -> None:
    groups = {
        USERS: members(json.loads(_recorded("groups-mtg-gateway-users.json")), USERS),
        ADMINS: members(json.loads(_recorded("groups-mtg-gateway-admins.json")), ADMINS),
    }
    carol = _carol()
    p = plan(groups, [ALICE, BOB, carol, DAVE], [ALICE, BOB, carol, DAVE])
    assert p.remove == sorted([BOB, DAVE]) and p.allowed == 2 and p.linked == 4


async def test_run_once_deletes_removed_members_sessions_with_an_audit_entry(db: Database) -> None:
    ak = RecordedAuthentik()
    async with ak.client() as http:
        p = await AuthentikSweep(_settings(), db, None, http=http).run_once()
    assert sorted(p.remove) == sorted([BOB, DAVE])
    assert _linked(db) == {ALICE, _carol()}
    for sub in (BOB, DAVE):
        row = db._conn.execute(
            "SELECT status, secret_enc FROM archidekt_links WHERE sub = ?", (sub,)
        ).fetchone()
        assert tuple(row) == ("revoked", "")
    rows = db._conn.execute(
        "SELECT sub, detail_json FROM audit_log WHERE event = 'archidekt_link_swept'"
    ).fetchall()
    assert sorted(r[0] for r in rows) == sorted([BOB, DAVE])
    assert json.loads(rows[0][1]) == {"reason": "not in the required or admin group at the identity provider"}
    # it asked exactly what was verified live: direct members, by exact name, with the token
    for req in ak.requests:
        assert req.url.path == "/api/v3/core/groups/"
        q = req.url.params
        assert q["include_users"] == "true" and q["include_children"] == "false"
        assert q["include_parents"] == "false" and q["include_inherited_roles"] == "false"
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
    assert sorted(r.url.params["name"] for r in ak.requests) == sorted([USERS, ADMINS])
    # a second round finds nothing left to do
    async with ak.client() as http:
        assert (await AuthentikSweep(_settings(), db, None, http=http).run_once()).remove == []


async def test_a_member_who_relinked_meanwhile_is_left_alone(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = db.active_links()  # read before dave relinks
    monkeypatch.setattr(db, "active_links", lambda: snapshot)
    db.save_link(DAVE, username="dave", user_id="1", secret_enc="sealed-new")  # relinked after the read
    async with RecordedAuthentik().client() as http:
        p = await AuthentikSweep(_settings(), db, None, http=http).run_once()
    assert p.remove == [BOB]
    assert db.get_link(DAVE)["secret_enc"] == "sealed-new"


# -- it deletes nothing unless the answer is whole and makes sense ------------------------------
def _users_body() -> dict:
    return json.loads(_recorded("groups-mtg-gateway-users.json"))


def _resp(body: object, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=body)


def _empty_group(name: str) -> dict:
    b = json.loads(_recorded("groups-mtg-gateway-admins.json"))
    b["results"][0]["name"], b["results"][0]["users_obj"] = name, []
    return b


def _two_pages() -> dict:
    b = _users_body()
    b["pagination"]["total_pages"] = 2
    return b


def _no_users_obj() -> dict:
    b = _users_body()
    del b["results"][0]["users_obj"]
    return b


def _no_uid() -> dict:
    b = _users_body()
    del b["results"][0]["users_obj"][0]["uid"]
    return b


def _string_active() -> dict:
    b = _users_body()
    b["results"][0]["users_obj"][0]["is_active"] = "true"
    return b


def _admins_no_uid() -> dict:
    b = json.loads(_recorded("groups-mtg-gateway-admins.json"))
    del b["results"][0]["users_obj"][0]["uid"]
    return b


def _twice() -> dict:
    b = _users_body()
    b["results"].append(b["results"][0])
    return b


REFUSED = {
    # an empty answer: both groups there, nobody in them (the mass-delete case)
    "both groups empty": {USERS: _resp(_empty_group(USERS)), ADMINS: _resp(_empty_group(ADMINS))},
    # a partial answer: the admins group is missing (wrong name, or the token can't see it)
    "a group not found": {ADMINS: _resp(json.loads(_recorded("groups-missing.json")))},
    "the token refused": {USERS: httpx.Response(403, content=_recorded("groups-no-permission.json"))},
    "a server error": {USERS: httpx.Response(502, text="bad gateway")},
    "a redirect": {USERS: httpx.Response(302, headers={"location": "https://evil.test/"})},
    "not JSON": {USERS: httpx.Response(200, text="<html>sign in</html>")},
    "split into pages": {USERS: _resp(_two_pages())},
    "members left out": {USERS: _resp(_no_users_obj())},
    "a member without uid": {USERS: _resp(_no_uid())},
    "is_active not a bool": {USERS: _resp(_string_active())},
    "the group twice": {USERS: _resp(_twice())},
    # only the admins group's answer is bad: still nothing, or admins would lose their links
    "admins timed out": {ADMINS: httpx.ReadTimeout("slow")},
    "admins member without uid": {ADMINS: _resp(_admins_no_uid())},
    "wrong shape": {USERS: _resp(["not", "a", "dict"])},
    "a timeout": {USERS: httpx.ReadTimeout("slow")},
    "unreachable": {USERS: httpx.ConnectError("no route")},
}


@pytest.mark.parametrize("case", sorted(REFUSED))
async def test_an_answer_that_cannot_be_trusted_deletes_nothing(
    case: str, db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    ak = RecordedAuthentik()
    ak.override = REFUSED[case]
    before = _linked(db)
    async with ak.client() as http:
        with pytest.raises(SweepRefused):
            await AuthentikSweep(_settings(), db, None, http=http).run_once()
    assert _linked(db) == before == {ALICE, BOB, _carol(), DAVE}
    assert (
        db._conn.execute("SELECT COUNT(*) FROM audit_log WHERE event = 'archidekt_link_swept'").fetchone()[0]
        == 0
    )


async def test_a_wrong_token_deletes_nothing(db: Database) -> None:
    async with RecordedAuthentik().client() as http:
        with pytest.raises(SweepRefused, match="HTTP 403"):
            await AuthentikSweep(_settings(token="wrong"), db, None, http=http).run_once()
    assert len(_linked(db)) == 4


async def test_an_answer_naming_nobody_the_gateway_knows_deletes_nothing(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The subject mode is not 'hashed user ID' (or the groups are the wrong ones): every linked
    member would look removed, so nobody is."""
    monkeypatch.setattr(db, "all_user_subs", lambda: [BOB, DAVE])
    async with RecordedAuthentik().client() as http:
        with pytest.raises(SweepRefused, match="subject mode"):
            await AuthentikSweep(_settings(), db, None, http=http).run_once()
    assert len(_linked(db)) == 4


def test_plan_refuses_to_remove_many_links_at_once() -> None:
    """One member still matching (say after a subject-mode change) must not let every other
    link go."""
    groups = {USERS: [{"uid": ALICE, "is_active": True}]}
    others = [f"sub-{i}" for i in range(49)]
    with pytest.raises(SweepRefused, match="49 of 50"):
        plan(groups, [ALICE, *others], [ALICE, *others])
    with pytest.raises(SweepRefused):
        plan(groups, [ALICE, *others[:9]], [ALICE])  # 9 of 10
    with pytest.raises(SweepRefused):
        plan(groups, [ALICE, *others[:4]], [ALICE])  # 4 of 5
    # a few at a time, or a small share, still go
    assert len(plan(groups, [ALICE, *others[:3]], [ALICE]).remove) == 3
    keep = [{"uid": f"k-{i}", "is_active": True} for i in range(16)]
    many = [ALICE, *[f"k-{i}" for i in range(16)], *others[:4]]
    assert len(plan({USERS: [groups[USERS][0], *keep]}, many, [ALICE]).remove) == 4  # 4 of 21


def test_plan_refuses_an_empty_allowed_set_even_with_groups_found() -> None:
    with pytest.raises(SweepRefused, match="empty"):
        plan({USERS: [], ADMINS: [{"uid": ALICE, "is_active": False}]}, [ALICE, BOB], [ALICE, BOB])


# -- off, warnings, the token, the loop -----------------------------------------------------------
def test_off_without_a_token_and_says_why_only_when_something_is_wrong(tmp_path: Path) -> None:
    s = AuthentikSweep(_settings(token=None), None, None)
    assert not s.enabled and s.why_off() is None  # not configured: optional, no warning
    s = AuthentikSweep(_settings(token=None, authentik_api_token_problem="secret file is empty"), None, None)
    assert not s.enabled and "secret file is empty" in (s.why_off() or "")
    s = AuthentikSweep(_settings(required_group=None), None, None)
    assert not s.enabled and "MTG_REQUIRED_GROUP" in (s.why_off() or "")
    assert AuthentikSweep(_settings(), None, None).enabled


def _write_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from .test_backup_and_config import _write_secrets as write

    write(tmp_path, monkeypatch)


def test_config_reads_the_token_file_and_a_bad_one_does_not_stop_the_gateway(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.delenv("MTG_AUTHENTIK_API_TOKEN_FILE", raising=False)
    monkeypatch.delenv("MTG_AUTHENTIK_API_URL", raising=False)
    s = load_settings()
    assert s.authentik_api_token is None and s.authentik_api_token_problem is None
    assert s.authentik_api_url == "https://auth.example.test"
    monkeypatch.setenv("MTG_AUTHENTIK_API_TOKEN_FILE", str(tmp_path / "missing"))
    s = load_settings()
    assert s.authentik_api_token is None and "cannot read" in (s.authentik_api_token_problem or "")
    (tmp_path / "tok").write_text(TOKEN + "\n")
    monkeypatch.setenv("MTG_AUTHENTIK_API_TOKEN_FILE", str(tmp_path / "tok"))
    s = load_settings()
    assert s.authentik_api_token == TOKEN and TOKEN not in repr(s)
    monkeypatch.setenv("MTG_AUTHENTIK_API_URL", "http://auth.internal")
    from mtg_gateway.config import ConfigError

    with pytest.raises(ConfigError, match="https"):
        load_settings()
    monkeypatch.setenv("MTG_AUTHENTIK_API_URL", "https://auth.internal/")
    assert load_settings().authentik_api_url == "https://auth.internal"


async def test_the_token_never_reaches_the_logs(db: Database, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)
    ak = RecordedAuthentik()
    async with ak.client() as http:
        await AuthentikSweep(_settings(), db, None, http=http).run_once()
        ak.override = {USERS: httpx.Response(403, content=_recorded("groups-no-permission.json"))}
        with pytest.raises(SweepRefused) as e:
            await AuthentikSweep(_settings(), db, None, http=http).run_once()
    assert TOKEN not in str(e.value)
    assert TOKEN not in caplog.text


async def test_the_loop_backs_off_after_failures_and_deletes_nothing(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    waits: list[float] = []
    outcomes = iter(["refused", "refused", "boom", "ok", "refused"] + ["refused"] * 6)

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)
        if len(waits) > 11:
            raise asyncio.CancelledError

    sweep = AuthentikSweep(_settings(), None, None)

    async def fake_run() -> idp_sweep.Plan:
        o = next(outcomes)
        if o == "refused":
            raise SweepRefused("Authentik answered HTTP 502")
        if o == "boom":
            raise RuntimeError("unexpected")
        return idp_sweep.Plan(remove=[], allowed=1, linked=1)

    monkeypatch.setattr(idp_sweep.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(sweep, "run_once", fake_run)
    with pytest.raises(asyncio.CancelledError):
        await sweep.loop()
    h = idp_sweep.INTERVAL
    assert waits[:6] == [idp_sweep.FIRST_DELAY, h, 2 * h, 4 * h, h, h]
    assert waits[-1] == idp_sweep.MAX_BACKOFF
    assert "nothing deleted: Authentik answered HTTP 502" in caplog.text
