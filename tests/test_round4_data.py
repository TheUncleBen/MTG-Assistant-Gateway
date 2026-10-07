"""Security round 4, member data and connected apps: deletion races, Archidekt link races, review
page honesty, scopes, per-app limits and the research proxy's argument bounds."""

from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path

import mcp_types as types
import pytest

from mtg_gateway import db as db_module
from mtg_gateway import decks as decks_module
from mtg_gateway.archidekt import ArchidektError, parse_deck
from mtg_gateway.db import Database
from mtg_gateway.mf_proxy import MysticForgeProxy

from .conftest import FakeIdP, Harness, make_settings, running
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, Stack, _client, call, linked_user, structured

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, apply_via_mcp=True, archidekt_base="https://ark.test/api"
    )
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h:
        yield Stack(h, ark)


@pytest.fixture
async def stack_browser_only(tmp_path: Path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, apply_via_mcp=False, archidekt_base="https://ark.test/api"
    )
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h:
        yield Stack(h, ark)


async def _app_token(h: Harness, name: str, scope: str = "mtg") -> tuple[str, str]:
    """(access token, client id) for a newly registered app of the signed-in member."""
    client = await h.register(client_name=name, scope=scope)
    code, verifier = await h.full_login(client, scope=scope)
    r = await h.token(
        client,
        grant_type="authorization_code",
        code=code,
        code_verifier=verifier,
        redirect_uri="https://client.test/cb",
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"], client["client_id"]


# -- B-2: "Delete my data" racing an apply or a report ------------------------------------------
async def test_delete_data_during_apply_leaves_no_snapshot(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Opt"}]},
        )
    )
    gw = h.app.state.gateway
    real = gw.archidekt.resolve_card
    gate, entered = asyncio.Event(), asyncio.Event()

    async def slow_resolve(*a, **kw):
        entered.set()
        await gate.wait()
        return await real(*a, **kw)

    gw.archidekt.resolve_card = slow_resolve
    b = Browser(h)
    await b.login()
    csrf = await b.csrf("/account")
    task = asyncio.create_task(call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    await entered.wait()
    r = await b.http.post("/account", data={"csrf": csrf, "action": "delete_data", "confirm": "yes"})
    assert r.headers["location"] == "/data-deleted"
    gate.set()
    out = structured(await task)
    assert not out["ok"] and ark.patches == [], out
    assert h.db.list_snapshots("user-1") == []
    await b.aclose()


async def test_delete_data_during_report_stores_no_report(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    gw = h.app.state.gateway
    real = gw.decks.get_any_deck
    gate, entered = asyncio.Event(), asyncio.Event()

    async def slow_read(*a, **kw):
        deck = await real(*a, **kw)
        entered.set()
        await gate.wait()
        return deck

    gw.decks.get_any_deck = slow_read
    b = Browser(h)
    await b.login()
    csrf = await b.csrf("/account")
    task = asyncio.create_task(call(h, token, "run_deck_report", {"deck_ref": "42", "simulate": False}))
    await entered.wait()
    await b.http.post("/account", data={"csrf": csrf, "action": "delete_data", "confirm": "yes"})
    gate.set()
    out = structured(await task)
    assert not out["ok"], out
    with h.db.tx() as c:
        assert c.execute("SELECT COUNT(*) FROM reports WHERE owner_sub = 'user-1'").fetchone()[0] == 0
    await b.aclose()


# -- B-3: a token refresh racing a relink ------------------------------------------------------
async def _start_refresh_then_relink(stack: Stack, outcome):
    h, ark = stack.h, stack.ark
    ark.access_ttl = 200  # alice's stored access token is inside the 5-minute refresh window
    token = await linked_user(stack)
    ark.access_ttl = 3600
    gw = h.app.state.gateway
    real = gw.archidekt.refresh
    gate, entered = asyncio.Event(), asyncio.Event()

    async def slow_refresh(rt):
        entered.set()
        await gate.wait()
        return await outcome(real, rt)

    gw.archidekt.refresh = slow_refresh
    task = asyncio.create_task(call(h, token, "list_my_decks"))
    await entered.wait()
    b = Browser(h)
    await b.login()
    r = await b.link("amy", "pw-amy")
    assert r.status_code == 303
    gate.set()
    out = structured(await task)
    gw.archidekt.refresh = real
    await b.aclose()
    return out


async def test_refresh_racing_relink_keeps_the_new_link(stack: Stack) -> None:
    async def ok(real, rt):
        return await real(rt)

    out = await _start_refresh_then_relink(stack, ok)
    assert not out["ok"], out  # the refreshed session belonged to the old link
    h, ark = stack.h, stack.ark
    link = h.db.get_link("user-1")
    secret = json.loads(h.app.state.gateway.decks.fernet.decrypt(link["secret_enc"].encode()))
    assert link["archidekt_username"] == "amy"
    assert ark.tokens[secret["access"]] == "amy" and secret["refresh"] == "ref-amy"


async def test_failed_refresh_of_old_link_does_not_revoke_the_new_one(stack: Stack) -> None:
    async def rejected(_real, _rt):
        raise ArchidektError("auth", "refresh token rejected")

    out = await _start_refresh_then_relink(stack, rejected)
    assert out["error"] == "not_linked", out
    link = stack.h.db.get_link("user-1")
    assert link is not None and link["archidekt_username"] == "amy"


# -- B-4: a new-deck proposal is bound to the account it was made for --------------------------
async def test_new_deck_proposal_is_bound_to_its_archidekt_account(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)  # alice
    p = structured(
        await call(h, token, "propose_new_deck", {"name": "Mine", "cards": [{"card_name": "Sol Ring"}]})
    )
    assert p["ok"], p
    b = Browser(h)
    await b.login()
    page = await b.http.get(f"/proposals/{p['proposal_id']}")
    assert "alice" in page.text  # the review page names the target account
    decks_before = set(ark.decks)
    r = await b.link("amy", "pw-amy")
    assert r.status_code == 303
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert not out["ok"] and out["error"] == "other_account", out
    assert set(ark.decks) == decks_before
    await b.aclose()


# -- B-5: a details apply re-checks the deck after the backup --------------------------------
async def test_details_apply_refuses_a_change_made_during_backup(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"name": "Gateway name"}})
    )
    assert p["ok"], p
    gw = h.app.state.gateway
    real = gw.archidekt.backup_deck

    async def backup_and_concurrent_edit(*a, **kw):
        out = await real(*a, **kw)
        ark.decks[42]["name"] = "Renamed on Archidekt meanwhile"
        ark.decks[42]["updatedAt"] = "2026-10-03T00:00:00Z"
        return out

    gw.archidekt.backup_deck = backup_and_concurrent_edit
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["error"] == "stale", out
    assert ark.decks[42]["name"] == "Renamed on Archidekt meanwhile"
    assert not [u for u in ark.updates if str(u.get("deck_id")) == "42"]


async def test_details_apply_still_works_when_nothing_changed(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"name": "Gateway name"}})
    )
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] and out["state"] == "applied", out
    assert ark.decks[42]["name"] == "Gateway name"


# -- B-6: admin actions are attributed to an administrator in the member's log ---------------
async def test_admin_actions_show_as_admin_in_members_activity(tmp_path, idp: FakeIdP) -> None:
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, archidekt_base="https://ark.test/api", admin_group="admins"
    )
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h:
        st = Stack(h, ark)
        await linked_user(st)
        member = dict(h.idp.user)
        h.idp.user = {
            "sub": "admin-1",
            "email": "root@example.test",
            "name": "Root",
            "preferred_username": "root",
            "groups": ["admins"],
        }
        a = Browser(h)
        await a.login()
        page = await a.http.get("/admin/users")
        csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page.text).group(1)
        for action in ("unlink", "revoke"):
            r = await a.http.post("/admin/users/user-1", data={"csrf": csrf, "action": action})
            assert r.status_code == 303 and f"ok={action}" in r.headers["location"], r.text
        rows = h.db.audit_for_user("user-1", 50)
        ev = next(r for r in rows if r["event"] == "archidekt_unlinked")
        assert ev["client_id"] != "__browser__"
        assert ev["detail"]["by"] == "admin" and ev["detail"]["origin"] == "admin"
        assert any(r["event"] == "admin_revoke" and r["detail"]["by"] == "admin" for r in rows)
        await a.aclose()
        h.idp.user = member
        m = Browser(h)
        await m.login()
        act = await m.http.get("/activity")
        assert "administrator" in act.text
        assert "archidekt unlinked</span><span class='badge'>browser" not in act.text
        await m.aclose()


# -- B-7: deck covers go with the member's data; the page is truthful about backups ------------
async def test_delete_data_removes_deck_covers_and_mentions_backups(stack: Stack) -> None:
    h = stack.h
    await linked_user(stack)
    b = Browser(h)
    await b.login()
    h.db.save_deck_cover("42", "uid-1", "Sol Ring", owner_sub="user-1")
    h.db.save_deck_cover("99", "uid-9", "Island", owner_sub="someone-else")
    csrf = await b.csrf("/account")
    r = await b.http.post("/account", data={"csrf": csrf, "action": "delete_data", "confirm": "yes"})
    assert r.headers["location"] == "/data-deleted"
    assert h.db.deck_covers(["42"]) == {}
    assert h.db.deck_covers(["99"])  # another member's cover stays
    page = await b.http.get("/data-deleted")
    assert "backup" in page.text and "14 days" in page.text
    assert "Everything this gateway kept for your account is gone" not in page.text
    await b.aclose()


async def test_deck_covers_from_before_the_owner_column_go_by_deck_id(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    db.save_deck_cover("42", "uid-1", "Sol Ring")  # no owner recorded (older row)
    db.save_snapshot("s1", owner_sub="u", deck_id="42", proposal_id=None, fingerprint="f", deck={"cards": []})
    db.delete_member_data("u")
    assert db.deck_covers(["42"]) == {}
    db.close()


# -- B-8: failed Archidekt link attempts are limited and audited ------------------------------
async def test_link_attempts_are_limited_per_member(stack: Stack) -> None:
    h = stack.h
    b = Browser(h)
    await b.login()
    for _ in range(decks_module.MAX_LINK_FAILURES):
        r = await b.link("alice", "wrong")
        assert r.status_code == 400 and "did not accept" in r.text
    r = await b.link("alice", "pw-alice")  # even the right password waits now
    assert r.status_code == 400 and "Too many" in r.text, r.text
    assert h.db.get_link("user-1") is None
    failed = [e for e in h.db.audit_for_user("user-1", 50) if e["event"] == "archidekt_link_failed"]
    assert len(failed) == decks_module.MAX_LINK_FAILURES
    await b.aclose()


async def test_link_failure_limit_ages_out(stack: Stack) -> None:
    h = stack.h
    b = Browser(h)
    await b.login()
    for _ in range(decks_module.MAX_LINK_FAILURES):
        await b.link("alice", "wrong")
    with h.db.tx() as c:
        c.execute("UPDATE audit_log SET at = at - ? WHERE event = 'archidekt_link_failed'", (16 * 60,))
    r = await b.link("alice", "pw-alice")
    assert r.status_code == 303, r.text
    await b.aclose()


# -- B-10: applied proposals age out ---------------------------------------------------------
def test_applied_proposals_are_purged_after_the_retention(tmp_path: Path) -> None:
    db = Database(tmp_path / "g.sqlite")
    for pid in ("old", "new"):
        db.save_proposal(
            {
                "id": pid,
                "owner_sub": "u",
                "deck_id": "1",
                "baseline_fingerprint": "f",
                "changes": [],
                "diff_text": "",
                "expires_at": int(time.time()) + 3600,
            }
        )
        db.finish_proposal(pid, state="applied", result={})
    with db.tx() as c:
        c.execute(
            "UPDATE proposals SET created_at = ?, applied_at = ? WHERE id = 'old'",
            (int(time.time()) - db_module.APPLIED_PROPOSAL_RETENTION_SECONDS - 60,) * 2,
        )
    db.purge_expired()
    with db.tx() as c:
        assert {r[0] for r in c.execute("SELECT id FROM proposals")} == {"new"}
    db.close()


# -- E-2: moving cards to the maybeboard is shown as leaving the deck --------------------------
async def test_set_category_to_maybeboard_is_shown_as_removal(stack_browser_only: Stack) -> None:
    h, ark = stack_browser_only.h, stack_browser_only.ark
    ark.decks[42]["categories"].append({"name": "Maybeboard", "isPremier": False, "includedInDeck": False})
    token = await linked_user(stack_browser_only)
    before = parse_deck(ark.decks[42]).counts_by_name()
    victims = [n for n in sorted(before) if n not in ("Forest", "Island")][:3]
    gone = sum(before[n] for n in victims)
    changes = [{"action": "set_category", "card_name": n, "category": "Maybeboard"} for n in victims]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert p["ok"], p
    assert "leaves the deck" in p["diff"]
    b = Browser(h)
    await b.login()
    url = f"/proposals/{p['proposal_id']}"
    page = (await b.http.get(url)).text
    summary = re.search(r"<div class='summary'>(.*?)</div>", page).group(1)
    assert f"{len(victims)} removed" in summary and f"Net -{gone} cards" in summary, summary
    await b.http.post(url, data={"csrf": await b.csrf(url), "action": "apply"})
    await b.aclose()
    # verify agrees with the review: the cards left the deck proper, so the apply succeeded
    assert h.db.get_proposal(p["proposal_id"], "user-1")["state"] == "applied"
    after = parse_deck(ark.decks[42]).counts_by_name()
    assert all(n not in after for n in victims)


def test_skill_no_longer_says_maybeboard_rows_are_never_touched() -> None:
    text = (ROOT / "plugin" / "mtg-gateway" / "skills" / "mtg-gateway" / "SKILL.md").read_text()
    assert "Maybeboard and sideboard rows are never" not in text
    assert "set_category" in text


# -- E-3: a read-only token cannot write ---------------------------------------------------
async def test_read_scope_token_cannot_propose_or_write_via_api(stack: Stack) -> None:
    h = stack.h
    await linked_user(stack)
    for scope in ("mtg.read", "read"):
        token, _cid = await _app_token(h, f"reader-{scope}", scope=scope)
        who = structured(await call(h, token, "whoami"))
        assert who["scopes"] == [scope]
        listing = structured(await call(h, token, "list_my_decks"))
        assert listing["ok"], listing
        res = await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]},
        )
        assert res.get("isError") and "insufficient_scope" in json.dumps(res), res
        r = await h.http.post(
            "/api/v1/proposals",
            headers={"Authorization": f"Bearer {token}"},
            json={"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]},
        )
        assert r.status_code == 403 and r.json()["error"] == "insufficient_scope", r.text
        r = await h.http.get("/api/v1/proposals", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 200
    assert not h.db.list_proposals("user-1")
    # the default scope keeps writing
    token, _cid = await _app_token(h, "writer")
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Opt"}]},
        )
    )
    assert p["ok"], p


# -- E-4: proposals name their app; disconnecting an app closes its pending proposals ---------
async def test_proposals_name_their_app_and_close_on_disconnect(stack_browser_only: Stack) -> None:
    h = stack_browser_only.h
    await linked_user(stack_browser_only)
    token, client_id = await _app_token(h, "Helper Bot")
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "remove", "card_name": "Acidic Slime"}]},
        )
    )
    b = Browser(h)
    await b.login()
    url = f"/proposals/{p['proposal_id']}"
    page = (await b.http.get(url)).text
    assert "Helper Bot" in page and client_id[:12] in page
    listing = (await b.http.get("/proposals")).text
    assert "Helper Bot" in listing
    account = (await b.http.get("/account")).text
    assert client_id[:12] in account
    csrf = await b.csrf("/account")
    r = await b.http.post("/account", data={"csrf": csrf, "action": "disconnect", "client_id": client_id})
    assert r.status_code == 303
    assert h.db.get_proposal(p["proposal_id"], "user-1")["state"] == "rejected"
    r = await b.http.post(url, data={"csrf": await b.csrf(url), "action": "apply"})
    assert "err=not_pending" in r.headers["location"]
    assert any(e["event"] == "proposal_rejected" for e in h.db.audit_for_user("user-1", 50))
    await b.aclose()


# -- E-5: the research proxy bounds the arguments of expensive tools --------------------------
class _Upstream:
    def __init__(self, seen: list):
        self.seen = seen

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def call_tool(self, name, args):
        self.seen.append((name, args))
        return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])


async def test_proxy_refuses_unbounded_simulation_arguments() -> None:
    seen: list = []
    proxy = MysticForgeProxy("http://x/mcp", client_factory=lambda: _Upstream(seen))
    for name, args in (
        ("goldfish_run", {"deck": "1 Island", "n": 10**9}),
        ("goldfish_run", {"deck": "1 Island", "n": "1000000000"}),
        ("goldfish_ab", {"deck_a": "1 Island", "deck_b": "1 Island", "n": 50_000}),
        ("goldfish_run", {"deck": "1 Island", "until_turn": 10**6}),
        ("goldfish_odds", {"params": {"deck_size": 10**9, "draws": 7, "copies": 1, "min_successes": 1}}),
        ("validate_decklist", {"decklist": "1 Island\n" * 100_000}),
    ):
        out = await proxy.call(name, args, owner="a")
        assert out.is_error and "at most" in out.content[0].text, (name, out)
    assert seen == []
    out = await proxy.call("goldfish_run", {"deck": "1 Island", "n": 300, "until_turn": 8}, owner="a")
    assert not out.is_error
    assert seen == [("goldfish_run", {"deck": "1 Island", "n": 300, "until_turn": 8})]


# -- E-6: a per-member budget of Archidekt calls -------------------------------------------
async def test_archidekt_calls_have_a_per_member_budget(tmp_path: Path, idp: FakeIdP) -> None:
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, archidekt_base="https://ark.test/api", archidekt_calls_per_10_min=5
    )
    seen: list = []
    proxy = MysticForgeProxy("http://x/mcp", client_factory=lambda: _Upstream(seen))
    async with running(Harness(settings, idp, archidekt=_client(settings, ark), mf_proxy=proxy)) as h:
        st = Stack(h, ark)
        token = await linked_user(st)  # the link itself is one call
        results = [structured(await call(h, token, "list_my_decks")) for _ in range(6)]
        assert [r["ok"] for r in results] == [True] * 4 + [False] * 2, results
        assert results[-1]["error"] == "rate_limited"
        # proxied Archidekt tools draw on the same budget
        res = await call(h, token, "archidekt_deck", {"deck": "42"})
        assert res.get("isError") and "Archidekt" in json.dumps(res), res
        assert seen == []
        # other research tools are not limited by it
        res = await call(h, token, "scryfall_named", {"name": "Sol Ring"})
        assert not res.get("isError"), res
        # another member has their own budget
        other = {
            "sub": "user-2",
            "email": "b@example.test",
            "name": "B",
            "preferred_username": "b",
            "groups": [],
        }
        token2 = await linked_user(st, user=other, ark_user="amy", pw="pw-amy")
        assert structured(await call(h, token2, "list_my_decks"))["ok"]


def test_budget_refills_over_time(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(decks_module.time, "monotonic", lambda: now[0])
    budget = decks_module.RateBudget(per_window=2, window=600)
    assert budget.take("u") and budget.take("u") and not budget.take("u")
    now[0] += 300  # half a window refills one call
    assert budget.take("u") and not budget.take("u")


# -- E-7: per-app pending cap; report deletes from apps are limited to their own reports --------
async def test_one_app_cannot_fill_every_pending_slot(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(decks_module, "MAX_PENDING_PER_CLIENT", 2)
    h = stack.h
    await linked_user(stack)
    noisy, _ = await _app_token(h, "noisy")
    quiet, _ = await _app_token(h, "quiet")
    change = {"deck_id": "42", "changes": [{"action": "add", "card_name": "Opt"}]}
    assert structured(await call(h, noisy, "propose_deck_changes", change))["ok"]
    assert structured(await call(h, noisy, "propose_deck_changes", change))["ok"]
    refused = structured(await call(h, noisy, "propose_deck_changes", change))
    assert refused["error"] == "rate_limited", refused
    assert structured(await call(h, quiet, "propose_deck_changes", change))["ok"]


async def test_apps_delete_only_their_own_reports(stack: Stack) -> None:
    h = stack.h
    await linked_user(stack)
    a, _ = await _app_token(h, "app-a")
    other, _ = await _app_token(h, "app-b")
    rep = structured(await call(h, a, "run_deck_report", {"deck_ref": "42", "simulate": False}))
    assert rep["ok"], rep
    rid = rep["report_id"]
    r = await h.http.delete(f"/api/v1/reports/{rid}", headers={"Authorization": f"Bearer {other}"})
    assert r.status_code == 404, r.text
    r = await h.http.delete(f"/api/v1/reports/{rid}", headers={"Authorization": f"Bearer {a}"})
    assert r.status_code == 200, r.text
    # the member's browser may delete any of their reports
    rep = structured(await call(h, other, "run_deck_report", {"deck_ref": "42", "simulate": False}))
    b = Browser(h)
    await b.login()
    csrf = await b.csrf("/account")
    r = await b.http.delete(f"/api/v1/reports/{rep['report_id']}", headers={"X-CSRF-Token": csrf})
    assert r.status_code == 200, r.text
    await b.aclose()


async def test_concurrent_refreshes_of_one_link_both_succeed(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    ark.access_ttl = 200  # inside the refresh window: each request refreshes first
    token = await linked_user(stack)
    ark.access_ttl = 3600
    gw = h.app.state.gateway
    real = gw.archidekt.refresh
    gate = asyncio.Event()
    entered = 0

    async def slow_refresh(rt):
        nonlocal entered
        entered += 1
        await gate.wait()
        return await real(rt)

    gw.archidekt.refresh = slow_refresh
    tasks = [asyncio.create_task(call(h, token, "list_my_decks")) for _ in range(2)]
    while entered < 2:
        await asyncio.sleep(0.01)
    gate.set()
    outs = [structured(await t) for t in tasks]
    assert all(o["ok"] for o in outs), outs
    assert h.db.get_link("user-1")["archidekt_username"] == "alice"


# -- RD-5: only a token whose every scope is read-only is read-only ----------------------------
@pytest.mark.parametrize(
    ("scopes", "writes"),
    [
        (["read", "write"], True),
        (["mtg", "read"], True),
        ([], True),
        (None, True),
        (["claudeai"], True),
        (["openid", "profile", "read"], True),
        (["mtg.read"], False),
        (["read"], False),
        (["mtg.read", "read"], False),
    ],
)
def test_scopes_allow_writes(scopes, writes) -> None:
    assert decks_module.scopes_allow_writes(scopes) is writes


# -- RD-7: parallel link attempts cannot get past the failure limit ----------------------------
async def test_parallel_link_attempts_respect_the_failure_limit(stack: Stack) -> None:
    h = stack.h
    gw = h.app.state.gateway
    b = Browser(h)
    await b.login()
    for _ in range(decks_module.MAX_LINK_FAILURES - 1):
        await b.link("alice", "wrong")
    real = gw.archidekt.login
    tried = 0

    async def slow_login(*a, **kw):
        nonlocal tried
        tried += 1
        await asyncio.sleep(0.05)
        return await real(*a, **kw)

    gw.archidekt.login = slow_login
    results = await asyncio.gather(
        *(gw.decks.link("user-1", "alice", "wrong") for _ in range(3)), return_exceptions=True
    )
    assert tried == 1, results
    assert sorted(r.kind for r in results) == ["auth", "rate_limited", "rate_limited"]
    await b.aclose()
