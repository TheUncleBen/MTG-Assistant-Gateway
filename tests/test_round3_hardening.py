"""Security round 3: fairness for the shared Archidekt pacer, Mystic Forge and the art budget;
bounded proposals; the creating-app rule for details proposals and for rejects; review rows that
cannot be forged; fixed-text notices; and inputs that answer 400 rather than 500."""

from __future__ import annotations

import asyncio
import copy
import re
import time
from pathlib import Path
from typing import Any

import httpx
import mcp_types as types
import pytest

from mtg_gateway import decks as decks_mod
from mtg_gateway.api import parse_limit
from mtg_gateway.archidekt import parse_deck
from mtg_gateway.db import Database
from mtg_gateway.decklist import parse_decklist
from mtg_gateway.decks import DeckError, DeckService, actor_label, deck_to_text
from mtg_gateway.mf_proxy import _busy
from mtg_gateway.reports import ReportService
from mtg_gateway.scan import art
from mtg_gateway.scan.scryfall import ScryfallClient, ScryfallError

from .conftest import FakeIdP, Harness, make_settings, running
from .fake_archidekt import FakeArchidekt
from .test_admin import admin_browser, gw  # noqa: F401  (gw is a fixture)
from .test_decks_and_proxy import Browser, Stack, _client, call, linked_user, mcp_token, structured


@pytest.fixture
async def stack(tmp_path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, approval_mode_default="auto", archidekt_base="https://ark.test/api"
    )
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h:
        yield Stack(h, ark)


def _deck() -> Any:
    return parse_deck(FakeArchidekt().decks[42])


async def _browser_with_csrf(h: Harness) -> tuple[Browser, str]:
    b = Browser(h)
    await b.login()
    return b, await b.csrf("/account")


# -- 1/5: reports use the member's Mystic Forge cap and run once per deck at a time ------------
class _FakeDecks:
    def __init__(self) -> None:
        self.reads = 0

    async def get_any_deck(self, sub: str | None, ref: str) -> Any:
        self.reads += 1
        await asyncio.sleep(0.01)
        return _deck()


class _FakeMF:
    def __init__(self, *, busy: bool = False) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.busy = busy

    async def call(
        self, name: str, arguments: Any, *, owner: str | None = None, internal: bool = False
    ) -> types.CallToolResult:
        self.calls.append((name, owner))
        if self.busy:
            return _busy()
        await asyncio.sleep(0.05)
        # answers shaped like Mystic Forge's successes, so the report counts them as ok (a failed
        # research call is never reused for a waiting request)
        text = {"goldfish_run": "## Metrics\nok", "goldfish_ab": "## Deltas\nok"}.get(name, "Validated: ok")
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)])


async def test_reports_count_against_the_members_mystic_forge_cap(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")
    for sub in ("alice", "bob"):  # reports are stored only for members that exist
        db.upsert_user(sub, email=None, name=None, preferred_username=None, groups=[])
    mf = _FakeMF()
    reports = ReportService(db, _FakeDecks(), mf)  # type: ignore[arg-type]
    await reports.run("alice", "42", games=20)
    assert mf.calls and all(owner == "alice" for _name, owner in mf.calls)
    # past the member's cap the proxy answers busy: the report is refused and nothing is stored
    busy = ReportService(db, _FakeDecks(), _FakeMF(busy=True), min_interval=0)  # type: ignore[arg-type]
    with pytest.raises(DeckError) as err:
        await busy.run("bob", "42", games=20)
    assert err.value.kind == "rate_limited"
    assert busy.list("bob") == []


async def test_concurrent_identical_reports_run_once(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    mf = _FakeMF()
    reports = ReportService(db, _FakeDecks(), mf)  # type: ignore[arg-type]
    outs = await asyncio.gather(*(reports.run("alice", "42", games=2000) for _ in range(10)))
    assert len({o["report_id"] for o in outs}) == 1
    assert sum(1 for o in outs if o.get("reused")) == 9
    assert [n for n, _ in mf.calls].count("goldfish_run") == 1
    assert len(reports.list("alice")) == 1
    assert reports._runs == {}  # nothing left behind


# -- 2/19: one member cannot queue up the shared Archidekt pacer ---------------------------------
class _SlowArchidekt:
    def __init__(self) -> None:
        self.release = asyncio.Event()
        self.started = 0

    async def get_deck(self, token: str | None, deck_id: str) -> Any:
        self.started += 1
        await self.release.wait()
        return _deck()


async def test_archidekt_work_is_capped_per_member(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    slow = _SlowArchidekt()
    svc = DeckService(settings, Database(tmp_path / "t.sqlite"), slow)  # type: ignore[arg-type]
    held = [
        asyncio.create_task(svc.get_any_deck("alice", "42")) for _ in range(decks_mod.MAX_ARCHIDEKT_PER_USER)
    ]
    await asyncio.sleep(0.01)
    with pytest.raises(DeckError) as err:
        await svc.get_any_deck("alice", "42")
    assert err.value.kind == "rate_limited"
    other = asyncio.create_task(svc.get_any_deck("bob", "42"))  # another member is not refused
    await asyncio.sleep(0.01)
    assert slow.started == decks_mod.MAX_ARCHIDEKT_PER_USER + 1
    slow.release.set()
    await asyncio.gather(*held, other)
    assert svc._archidekt_in_flight == {}
    await svc.get_any_deck("alice", "42")  # slots come back


async def test_json_api_deck_reads_count_against_the_cap(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    h.app.state.gateway.decks.max_archidekt_per_user = 0
    r = await h.http.get("/api/v1/decks/42", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 429 and r.json()["error"] == "rate_limited"


# -- 11/23: details proposals record the creating app; applies fail closed without one ----------
async def test_details_proposal_is_applied_only_by_the_app_that_made_it(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"private": True}})
    )
    assert p["ok"], p
    row = h.app.state.gateway.db.get_proposal(p["proposal_id"], "user-1")
    assert row["created_by_client"]
    token_b = await mcp_token(h)
    out = structured(await call(h, token_b, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "other_client", out
    # a proposal stored without a creator (older rows) is never applied over MCP
    with h.app.state.gateway.db.tx() as c:
        c.execute("UPDATE proposals SET created_by_client = NULL WHERE id = ?", (p["proposal_id"],))
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "other_client", out


# -- 29: reject follows the creator rule over MCP and the API; the browser may reject anything ---
async def test_another_app_cannot_reject_the_members_proposals(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    b, csrf = await _browser_with_csrf(h)
    edit = {"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]}
    r = await b.http.post("/api/v1/proposals", json=edit, headers={"X-CSRF-Token": csrf})
    assert r.status_code == 201, r.text
    pid = r.json()["proposal_id"]
    out = structured(await call(h, token, "reject_proposal", {"proposal_id": pid}))
    assert out["ok"] is False and out["error"] == "other_client", out
    r = await h.http.post(f"/api/v1/proposals/{pid}/reject", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403 and r.json()["error"] == "other_client"
    assert h.app.state.gateway.decks.describe("user-1", pid)["state"] == "pending"
    # the creating app may reject its own proposal
    mine = structured(await call(h, token, "propose_deck_changes", edit))
    out = structured(await call(h, token, "reject_proposal", {"proposal_id": mine["proposal_id"]}))
    assert out["ok"] and out["state"] == "rejected", out
    # the browser review page may reject any of the member's proposals
    page_csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", (await b.http.get(f"/proposals/{pid}")).text)
    assert page_csrf
    r = await b.http.post(f"/proposals/{pid}", data={"csrf": page_csrf.group(1), "action": "reject"})
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=rejected")
    await b.aclose()


# -- 12/18: pending proposals are capped per member; closed ones are pruned --------------------
async def test_pending_proposals_are_capped_per_member(stack: Stack, monkeypatch) -> None:
    h = stack.h
    token = await linked_user(stack)
    monkeypatch.setattr(decks_mod, "MAX_PENDING_PROPOSALS", 3)
    args = {"name": "x", "cards": [{"card_name": "Sol Ring", "quantity": 1}]}
    for _ in range(3):
        assert structured(await call(h, token, "propose_new_deck", args))["ok"]
    out = structured(await call(h, token, "propose_new_deck", args))
    assert out["ok"] is False and out["error"] == "rate_limited" and "pending proposals" in out["message"]


def test_closed_proposals_are_pruned(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")

    def save(pid: str, **kw: Any) -> bool:
        row = {
            "id": pid,
            "owner_sub": "u",
            "deck_id": "1",
            "baseline_fingerprint": "",
            "changes": [],
            "diff_text": "",
            "expires_at": int(time.time()) + 3600,
        }
        return db.save_proposal(row, **kw)

    for i in range(5):
        save(f"p{i}")
        db.reject_proposal(f"p{i}", "u")
    assert save("new", max_pending=1, max_closed=2)
    assert not save("new2", max_pending=1)  # one pending already
    with db._lock:
        states = [r[0] for r in db._conn.execute("SELECT state FROM proposals WHERE owner_sub = 'u'")]
    assert sorted(states) == ["pending", "rejected", "rejected"]
    with db.tx() as c:  # closed proposals older than the retention go at the next purge
        c.execute("UPDATE proposals SET created_at = 0 WHERE state = 'rejected'")
    db.purge_expired()
    with db._lock:
        assert [r[0] for r in db._conn.execute("SELECT id FROM proposals")] == ["new"]


# -- 13/22: notices on /decks/{id} and /admin/users come from fixed codes only ------------------
async def test_deck_page_shows_only_fixed_notices(stack: Stack) -> None:
    h = stack.h
    await linked_user(stack)
    b = Browser(h)
    await b.login()
    spoof = "Your Archidekt session expired. Re-enter your password at https://evil.example"
    r = await b.http.get("/decks/42", params={"err": spoof})
    assert r.status_code == 200 and "evil.example" not in r.text and "notice error" not in r.text
    r = await b.http.get("/decks/42", params={"err": "rate_limited"})
    assert "Too many requests for your account" in r.text
    csrf = await b.csrf("/decks/42")
    r = await b.http.post("/decks/x%20y/report", data={"csrf": csrf})
    assert r.status_code == 303 and r.headers["location"].endswith("?err=invalid")
    await b.aclose()


async def test_admin_users_shows_only_fixed_notices(gw: Harness) -> None:  # noqa: F811
    b = await admin_browser(gw)
    r = await b.http.get("/admin/users", params={"err": "Delete user-2 now: https://evil.example"})
    assert r.status_code == 200 and "evil.example" not in r.text
    r = await b.http.get("/admin/users", params={"err": "self_disable"})
    assert "You cannot disable your own account." in r.text
    await b.aclose()


# -- 39: admin JSON is never cached ---------------------------------------------------------------
async def test_admin_json_is_no_store(gw: Harness) -> None:  # noqa: F811
    b = await admin_browser(gw)
    for path in ("/api/v1/admin/users", "/api/v1/admin/overview", "/api/v1/admin/metrics"):
        r = await b.http.get(path)
        assert r.status_code == 200 and r.headers.get("cache-control") == "no-store", path
    r = await b.http.get("/api/v1/admin/metrics", params={"days": "²"})
    assert r.status_code == 400
    await b.aclose()


# -- 20: the review page shows the description a details proposal writes -------------------------
async def test_review_page_shows_the_proposed_description(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    text = "Visit https://evil.example/free-cards <b>now</b>"
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"description": text}})
    )
    assert p["ok"], p
    b = Browser(h)
    await b.login()
    page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
    await b.aclose()
    assert "Visit https://evil.example/free-cards &lt;b&gt;now&lt;/b&gt;" in page
    assert "Current description" in page


# -- 20b: the review page speaks to the person, not the assistant ---------------------------------
async def test_review_page_does_not_show_the_assistant_instructions(stack: Stack) -> None:
    """next_step tells the assistant which tools to call; the review page words the next step for
    the person reading it instead."""
    h = stack.h
    token = await linked_user(stack)
    change = {"action": "add", "card_name": "Sol Ring", "quantity": 1}
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    assert p["ok"] and "apply_proposal" in p["next_step"], p
    b = Browser(h)
    await b.login()
    page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
    await b.aclose()
    assert "apply_proposal" not in page and "do not apply it yourself" not in page
    may_apply = p["assistant_may_apply"]
    assert ("lets your assistant apply this one itself" in page) is may_apply
    assert ("Nothing changes on Archidekt until you apply it." in page) is not may_apply


# -- 21: review rows come from structured data and cannot be forged -----------------------------
def _acts(page: str) -> list[str]:
    return re.findall(r"<span class='act'>(.*?)</span>", page)


async def test_category_text_cannot_forge_review_rows(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    change = {"action": "set_category", "card_name": "Sol Ring", "category": "Ramp\n-1 Forest\n+1 X"}
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    assert p["ok"], p
    assert len(p["diff"].splitlines()) == 1 and p["rows"][0]["kind"] == "category"
    b = Browser(h)
    await b.login()
    page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
    assert _acts(page) == ["Category"] and "removed" not in page
    name = "Burn: category Visibility public -> PRIVATE (only you)"
    args = {"name": name, "private": False, "cards": [{"card_name": "Sol Ring: category a -> b"}]}
    p2 = structured(await call(h, token, "propose_new_deck", args))
    assert p2["ok"], p2
    page = (await b.http.get(f"/proposals/{p2['proposal_id']}")).text
    await b.aclose()
    assert _acts(page) == ["New deck", "Add"]
    assert "commander, 1 cards, PUBLIC" in page


# -- 27: categories from a public deck cannot add card lines to decklist text --------------------
def test_deck_categories_cannot_inject_decklist_lines() -> None:
    raw = copy.deepcopy(FakeArchidekt().decks[42])
    raw["cards"][1]["categories"] = ["Ramp]\n4 Black Lotus\n1 Ancestral Recall [x"]
    text = deck_to_text(parse_deck(raw))
    names = {c.name for c in parse_decklist(text)}
    assert "Black Lotus" not in names and "Ancestral Recall" not in names
    assert len(text.splitlines()) == len([c for c in parse_deck(raw).main_cards])


# -- 28: one member gets a share of the art budget and of new galleries --------------------------
async def test_art_budget_and_galleries_are_shared_per_member(tmp_path: Path) -> None:
    client = ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))),
    )
    settings = art.ArtSettings(images_per_hour=8, max_galleries=40)  # 2 images, 2 galleries each
    index = art.ArtIndex(Database(tmp_path / "t.sqlite"), client, settings, image_http=httpx.AsyncClient())
    assert index._take_budget("a") and index._take_budget("a")
    with pytest.raises(ScryfallError) as err:
        index._take_budget("a")
    assert err.value.kind == "owner_budget" and index.paused_until == 0.0  # nobody else paused
    assert index._take_budget("b")

    async def fake_build(oracle_id: str, owner: str | None = None) -> None:
        index._tasks.pop(oracle_id, None)

    index._build = fake_build  # type: ignore[method-assign]
    assert index._ensure_gallery("o1", None, "a") and index._ensure_gallery("o2", None, "a")
    assert not index._ensure_gallery("o3", None, "a")  # a's share of new galleries is used
    assert index._ensure_gallery("o3", None, "b")
    await asyncio.sleep(0)
    await index.aclose()


# -- 38: an app named "browser" is shown as an app ----------------------------------------------
def test_actor_label_uses_the_client_id_not_the_name() -> None:
    assert actor_label("__browser__") == "browser"
    assert actor_label("c-123", "browser") == "app: browser (c-123)"
    assert actor_label("c-123", "evil\nbrowser") == "app: evil browser (c-123)"
    assert actor_label(None) == ""


async def test_activity_badge_for_an_app_named_browser(stack: Stack) -> None:
    h = stack.h
    await linked_user(stack)
    client = await h.register(client_name="browser")
    token = (await h.tokens_for(client))["access_token"]
    out = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]},
        )
    )
    assert out["ok"], out
    b = Browser(h)
    await b.login()
    page = (await b.http.get("/activity")).text
    await b.aclose()
    badges = re.findall(r"<span class='badge'>(.*?)</span>", page)
    assert any(x.startswith("app: browser (") for x in badges), badges
    rows = h.app.state.gateway.db.audit_for_user("user-1", 10)
    assert any((r.get("detail") or {}).get("origin") == "app" for r in rows)


# -- 40/41: bad input answers 400, never 500 -------------------------------------------------------
def test_parse_limit() -> None:
    assert parse_limit("9" * 5000, 50, 1000) == 50
    assert parse_limit("²", 50, 1000) == 50
    assert parse_limit("0", 50, 1000) == 1
    assert parse_limit("20", 50, 1000) == 20
    assert parse_limit(None, 50, 1000) == 50


async def test_bad_inputs_are_400_not_500(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    auth = {"Authorization": f"Bearer {token}"}
    for limit in ("9" * 5000, "²"):
        r = await h.http.get("/api/v1/activity", params={"limit": limit}, headers=auth)
        assert r.status_code == 200, limit
    for path in ("/api/v1/decks/%5B::1", "/api/v1/decks/%5B::1/stats"):
        r = await h.http.get(path, headers=auth)
        assert r.status_code == 400 and r.json()["error"] == "invalid", path
    for body in ({"decklist_text": 123}, {"csv_text": ["a"]}):
        r = await h.http.post(
            "/api/v1/proposals", json={"kind": "new_deck", "name": "x", **body}, headers=auth
        )
        assert r.status_code == 400 and r.json()["error"] == "invalid", body
    b = Browser(h)
    await b.login()
    r = await b.http.get("/decks/open", params={"ref": "http://[::1/decks/1"})
    assert r.status_code == 303 and r.headers["location"] == "/decks?err=bad_ref"
    r = await b.http.get("/decks/%5B::1")
    assert r.status_code == 400
    await b.aclose()
    with pytest.raises(DeckError):
        decks_mod._clean_deck_id("٤٢")  # non-ASCII digits are not a deck id


def test_review_page_next_step_never_names_a_tool() -> None:
    """Every state the review page can show words its next step for a person."""
    from mtg_gateway.pages import _page_step

    cases = [
        {"state": "pending", "writes_enabled": False},
        {"state": "pending", "writes_enabled": True, "assistant_may_apply": False},
        {"state": "pending", "writes_enabled": True, "assistant_may_apply": True},
        {"state": "pending", "writes_enabled": True, "result": {"error": "backup_failed"}},
        {"state": "applying"},
        {"state": "applied"},
        {"state": "failed", "result": {"sent_entries": 3}},
        {"state": "failed", "result": None},
        {"state": "rejected"},
        {"state": "expired"},
    ]
    texts = [_page_step({**c, "next_step": "call apply_proposal now"}) for c in cases]
    assert all(t and "apply_proposal" not in t and "_" not in t for t in texts), texts
    assert len(set(texts)) == len(texts)
    assert "Nothing changes on Archidekt until you apply it." in texts[1]
    assert texts[3].startswith("The last try stopped before anything changed")
