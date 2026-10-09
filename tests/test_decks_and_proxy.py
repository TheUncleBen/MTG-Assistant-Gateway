"""Phases 1 to 3 against fakes: Mystic Forge proxy, browser login, account
linking, propose and apply, isolation between users."""

from __future__ import annotations

import json
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from mtg_gateway.archidekt import ArchidektClient, Pacer
from mtg_gateway.mf_proxy import MysticForgeProxy

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running, sse_json
from .fake_archidekt import FakeArchidekt


class _GoldfishRunInput(BaseModel):
    """The shape of Mystic Forge's goldfish_run input (server.py GoldfishRunInput, extra forbidden):
    one ``params`` object. A flat call fails validation exactly as it does against the real service."""

    model_config = ConfigDict(extra="forbid")
    deck: str = Field(min_length=1)
    n: int = Field(default=300, ge=1, le=5000)
    seed: int | None = None
    until_turn: int = Field(default=10, ge=1, le=30)
    opponents: int = Field(default=1, ge=1, le=5)
    mulligan: dict[str, object] | None = None
    annotations: list[dict[str, object]] | None = None
    combos: list[object] | None = None


class _GoldfishAbInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deck_a: str = Field(min_length=1)
    deck_b: str = Field(min_length=1)
    n: int = Field(default=300, ge=1, le=5000)
    seed: int | None = None
    until_turn: int = Field(default=10, ge=1, le=30)
    annotations: list[dict[str, object]] | None = None
    annotations_a: list[dict[str, object]] | None = None
    annotations_b: list[dict[str, object]] | None = None
    combos: list[object] | None = None
    allow_different_commanders: bool = False


class _ValidateDecklistInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decklist: str = Field(min_length=1)
    commander: str | None = None


def fake_mystic_forge() -> MCPServer:
    mf = MCPServer(name="mystic-forge-fake")

    @mf.tool(name="scryfall_named", description="Look up a card by name")
    async def scryfall_named(name: str) -> dict[str, object]:
        return {"name": name, "oracle_text": "fake oracle text"}

    # The simulators, with Mystic Forge's one-model signature and its renderers' headings. A
    # commander the fake does not know is answered the way Mystic Forge does: a sentence, not an
    # error.
    @mf.tool(name="goldfish_run", description="goldfish simulation")
    async def goldfish_run(params: _GoldfishRunInput) -> str:
        first = params.deck.strip().splitlines()[0]
        if "Unknown" in first:
            return f"Commander '{first}' was not recognized by Scryfall; fix the name and rerun."
        return f"# Goldfish run\nCommander: {first} | {params.n} games\n\n## Metrics\n```json\n{{}}\n```"

    @mf.tool(name="goldfish_ab", description="paired goldfish A/B")
    async def goldfish_ab(params: _GoldfishAbInput) -> str:
        return f"# Goldfish A/B\n{params.n} paired games\n\n## Deltas (A − B)\n(none)"

    @mf.tool(name="validate_decklist", description="validate a decklist")
    async def validate_decklist(params: _ValidateDecklistInput) -> str:
        # Like Mystic Forge, which reads names through Scryfall's collection lookup: a
        # double-faced card named with both faces is "not found".
        both = [line for line in params.decklist.splitlines() if " // " in line]
        if both:
            return "# Validation: ISSUES FOUND\n\n**not found on Scryfall:**\n" + "\n".join(both)
        return f"Validated {len(params.decklist.splitlines())} lines"

    @mf.tool(name="watchlist_list", description="stateful, must stay hidden")
    async def watchlist_list() -> list[str]:
        return ["secret"]

    @mf.tool(name="unknown_future_tool", description="not on either list")
    async def unknown_future_tool() -> str:
        return "hidden"

    return mf


def _client(settings, ark: FakeArchidekt) -> ArchidektClient:
    return ArchidektClient(settings.archidekt_base, "test-agent", Pacer(0.0), http=ark.client())


class Stack:
    def __init__(self, h: Harness, ark: FakeArchidekt):
        self.h = h
        self.ark = ark


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, approval_mode_default="auto", archidekt_base="https://ark.test/api"
    )
    client = _client(settings, ark)
    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(fake_mystic_forge()))
    async with running(Harness(settings, idp, archidekt=client, mf_proxy=proxy)) as h:
        yield Stack(h, ark)


@pytest.fixture
async def stack_ro(tmp_path: Path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(tmp_path, writes_enabled=False, archidekt_base="https://ark.test/api")
    client = _client(settings, ark)
    async with running(Harness(settings, idp, archidekt=client)) as h:
        yield Stack(h, ark)


# -- helpers -----------------------------------------------------------------
async def mcp_token(h: Harness) -> str:
    client = await h.register()
    return (await h.tokens_for(client))["access_token"]


async def call(h: Harness, token: str, name: str, args: dict | None = None) -> dict:
    r = await h.mcp(token, "tools/call", {"name": name, "arguments": args or {}}, rid=7)
    assert r.status_code == 200, r.text
    return sse_json(r)["result"]


def structured(result: dict) -> dict:
    return result["structuredContent"]


class Browser:
    """A browser with its own cookie jar against the gateway."""

    def __init__(self, h: Harness):
        self.h = h
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=h.app), base_url=GATEWAY)

    async def login(self, next_path: str = "/account") -> httpx.Response:
        r = await self.http.get("/login", params={"next": next_path})
        assert r.status_code == 302, r.text
        assert "mtg_login=" in r.headers.get("set-cookie", "")
        cb = await self.h.idp_leg(r)
        p = urlparse(cb)
        r2 = await self.http.get(f"{p.path}?{p.query}")
        assert r2.status_code == 302, r2.text
        assert "mtg_session=" in r2.headers.get("set-cookie", "")
        assert r2.headers["location"] == next_path
        return r2

    async def csrf(self, path: str = "/account") -> str:
        r = await self.http.get(path)
        assert r.status_code == 200, r.text
        m = re.search(r"name='csrf' value='([0-9a-f]+)'", r.text)
        assert m, r.text
        return m.group(1)

    async def link(self, login: str, password: str, *, csrf: str | None = None) -> httpx.Response:
        csrf = csrf if csrf is not None else await self.csrf()
        return await self.http.post(
            "/account",
            data={
                "csrf": csrf,
                "action": "link",
                "archidekt_login": login,
                "archidekt_password": password,
                "accept_risk": "1",
            },
        )

    async def aclose(self) -> None:
        await self.http.aclose()


# -- Phase 1: proxy -----------------------------------------------------------
async def test_proxy_lists_only_allowlisted_tools_and_forwards_calls(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    r = await h.mcp(token, "tools/list")
    names = {t["name"] for t in sse_json(r)["result"]["tools"]}
    assert "scryfall_named" in names
    assert "watchlist_list" not in names and "unknown_future_tool" not in names
    assert {
        "whoami",
        "account_status",
        "propose_deck_changes",
        "apply_proposal",
        "parse_deck_export",
    } <= names
    proxied = next(t for t in sse_json(r)["result"]["tools"] if t["name"] == "scryfall_named")
    assert proxied["annotations"]["readOnlyHint"] is True
    out = await call(h, token, "scryfall_named", {"name": "Sol Ring"})
    assert out.get("isError") is not True
    assert "fake oracle text" in str(out)
    blocked = await call(h, token, "watchlist_list")
    assert blocked.get("isError") is True


async def test_proxied_tools_need_a_gateway_token(stack: Stack) -> None:
    r = await stack.h.mcp(None, "tools/call", {"name": "scryfall_named", "arguments": {"name": "x"}})
    assert r.status_code == 401


# -- Phase 2: browser login and account link -----------------------------------
async def test_browser_login_link_status_unlink(stack: Stack) -> None:
    h = stack.h
    b = Browser(h)
    await b.login()
    page = await b.http.get("/account")
    assert "Link your Archidekt account" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]

    bad = await b.link("alice", "wrong")
    assert bad.status_code == 400 and "did not accept" in bad.text
    assert not h.db.get_link("user-1")

    ok = await b.link("alice", "pw-alice")
    assert ok.status_code == 303 and ok.headers["location"] == "/account?ok=linked"
    row = h.db.get_link("user-1")
    assert row and row["archidekt_username"] == "alice"
    # encrypted, no password
    assert "pw-alice" not in row["secret_enc"] and "tok-alice" not in row["secret_enc"]
    page = await b.http.get("/account?ok=linked")
    assert "Linked to <strong>alice</strong>" in page.text

    token = await mcp_token(h)
    status = structured(await call(h, token, "account_status"))
    assert (
        status["linked"] is True
        and status["archidekt_username"] == "alice"
        and status["writes_enabled"] is True
    )

    decks = structured(await call(h, token, "list_my_decks"))
    assert decks["ok"] and [d["id"] for d in decks["decks"]] == ["42"]
    deck = structured(await call(h, token, "get_deck", {"deck_ref": "https://archidekt.com/decks/42/reap"}))
    assert deck["ok"] and deck["card_count"] == 100 and deck["name"] == "Sample Commander Deck"

    unlink = await b.http.post("/account", data={"csrf": await b.csrf(), "action": "unlink"})
    assert unlink.status_code == 303
    assert h.db.get_link("user-1") is None
    status = structured(await call(h, token, "account_status"))
    assert status["linked"] is False
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"] is False and out["error"] == "not_linked"

    # Logout ends the session and tells the browser to drop cached pages and site storage.
    out_resp = await b.http.post("/logout", data={"csrf": await b.csrf()})
    assert out_resp.status_code == 303
    assert out_resp.headers["clear-site-data"] == '"storage"'
    assert out_resp.headers["location"] == "/signed-out"
    signed_out = await b.http.get("/signed-out")
    assert signed_out.status_code == 200 and "Sign out" not in signed_out.text
    assert (await b.http.get("/account")).status_code == 302  # back to sign-in
    await b.aclose()


async def test_account_form_requires_csrf_and_session(stack: Stack) -> None:
    h = stack.h
    anon = Browser(h)
    r = await anon.http.get("/account")
    assert r.status_code == 302 and r.headers["location"].startswith("/login?next=")
    r = await anon.http.post(
        "/account", data={"action": "link", "archidekt_login": "alice", "archidekt_password": "x"}
    )
    assert r.status_code == 302  # sent to login, nothing linked
    b = Browser(h)
    await b.login()
    r = await b.link("alice", "pw-alice", csrf="deadbeef")
    assert r.status_code == 403
    assert h.db.get_link("user-1") is None
    r = await b.http.get("/login", params={"next": "https://evil.test/"})
    assert r.status_code == 302 and "evil" not in r.headers["location"]
    await anon.aclose()
    await b.aclose()


# -- Phase 3: proposals -----------------------------------------------------------
async def linked_user(
    stack: Stack, user: dict | None = None, ark_user: str = "alice", pw: str = "pw-alice"
) -> str:
    h = stack.h
    if user:
        h.idp.user = user
    b = Browser(h)
    await b.login()
    r = await b.link(ark_user, pw)
    assert r.status_code == 303, r.text
    await b.aclose()
    return await mcp_token(h)


async def test_propose_then_apply_happy_path(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    changes = [
        {"action": "add", "card_name": "Arcane Signet", "quantity": 1, "category": "Ramp"},
        {"action": "remove", "card_name": "Acidic Slime"},
        {"action": "set_quantity", "card_name": "Forest", "quantity": 14},
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert p["ok"] and p["state"] == "pending"
    assert set(p["diff"].splitlines()) == {"-1 Acidic Slime", "15 -> 14 Forest", "+1 Arcane Signet [Ramp]"}
    assert p["review_url"] == f"{GATEWAY}/proposals/{p['proposal_id']}"
    assert ark.patches == []  # nothing written yet

    listing = structured(await call(h, token, "list_my_proposals"))
    assert [x["id"] for x in listing["proposals"]] == [p["proposal_id"]]

    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"], a
    assert a["state"] == "applied" and a["result"]["verified"] is True and a["snapshot_id"]
    assert len(ark.patches) == 3  # one PATCH per card, as the reference client does
    sent = [p["cards"][0] for p in ark.patches]
    assert all(len(p["cards"]) == 1 for p in ark.patches)
    added = next(e for e in sent if e["action"] == "add")
    assert added["cardid"] == 9001 and added["modifications"]["quantity"] == 1
    assert added["categories"] == ["Ramp"]  # the change's category is applied, not dropped
    removed = next(e for e in sent if e["action"] == "remove")
    assert removed["modifications"]["quantity"] == 0 and "deckRelationId" in removed
    modified = next(e for e in sent if e["action"] == "modify")
    assert modified["modifications"]["quantity"] == 14 and modified["modifications"]["modifier"] == "Normal"
    counts = {c["card"]["oracleCard"]["name"]: c["quantity"] for c in ark.decks[42]["cards"]}
    assert counts["Arcane Signet"] == 1 and counts["Forest"] == 14 and "Acidic Slime" not in counts

    snap = h.db.get_snapshot(a["snapshot_id"], "user-1")
    assert snap and snap["deck"]["name"] == "Sample Commander Deck"
    assert sum(c["quantity"] for c in snap["deck"]["cards"]) == 100  # pre-change state kept

    again = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert again["ok"] is False and again["error"] == "already_applied"
    assert len(ark.patches) == 3


async def test_apply_rejects_stale_deck(stack: Stack) -> None:
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
    ark.bump(42)
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] is False and a["error"] == "stale"
    assert ark.patches == []
    assert (
        structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))["state"]
        == "failed"
    )


async def test_apply_reports_verify_mismatch_and_keeps_snapshot(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.fail_patch_silently = True
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "set_quantity", "card_name": "Island", "quantity": 16}]},
        )
    )
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] is False and a["error"] == "verify_mismatch" and "Island" in a["message"]
    row = h.db.get_proposal(p["proposal_id"], "user-1")
    assert row["state"] == "failed" and row["snapshot_id"]


async def test_invalid_changes_are_rejected_before_any_fetch(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    before = len(ark.calls)
    # An action outside the schema's list is refused by the input schema itself.
    out = await call(
        h,
        token,
        "propose_deck_changes",
        {"deck_id": "42", "changes": [{"action": "explode", "card_name": "x"}]},
    )
    assert out.get("isError") and "action" in out["content"][0]["text"]
    assert len(ark.calls) == before
    out = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "abc", "changes": [{"action": "add", "card_name": "x"}]},
        )
    )
    assert out["error"] == "invalid"
    out = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "remove", "card_name": "Not In Deck"}]},
        )
    )
    assert out["error"] == "invalid" and "not in the deck" in out["message"]


async def test_writes_disabled_blocks_apply_but_allows_proposals(stack_ro: Stack) -> None:
    h, ark = stack_ro.h, stack_ro.ark
    token = await linked_user(stack_ro)
    status = structured(await call(h, token, "account_status"))
    assert status["writes_enabled"] is False
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Arcane Signet"}]},
        )
    )
    assert p["ok"] and "disabled" in p["next_step"]
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] is False and a["error"] == "writes_disabled"
    assert ark.patches == []
    # the review page shows the diff but no apply button
    b = Browser(h)
    await b.login(f"/proposals/{p['proposal_id']}")
    page = await b.http.get(f"/proposals/{p['proposal_id']}")
    assert (
        page.status_code == 200 and "+1 Arcane Signet" in page.text and "Apply these changes" not in page.text
    )
    await b.aclose()


async def test_browser_apply_with_csrf(stack: Stack) -> None:
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
    b = Browser(h)
    await b.login()
    url = f"/proposals/{p['proposal_id']}"
    page = await b.http.get(url)
    assert "Apply these changes" in page.text
    r = await b.http.post(url, data={"csrf": "nope", "action": "apply"})
    assert r.status_code == 403 and ark.patches == []
    r = await b.http.post(url, data={"csrf": await b.csrf(url), "action": "apply"})
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=applied")
    assert len(ark.patches) == 1  # one card added
    page = await b.http.get(url + "?ok=applied")
    assert "Applied." in page.text and ">applied<" in page.text
    await b.aclose()


async def test_browser_reject_closes_a_pending_proposal(stack: Stack) -> None:
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
    b = Browser(h)
    await b.login()
    url = f"/proposals/{p['proposal_id']}"
    page = await b.http.get(url)
    assert "Reject this proposal" in page.text
    r = await b.http.post(url, data={"csrf": "nope", "action": "reject"})
    assert r.status_code == 403
    r = await b.http.post(url, data={"csrf": await b.csrf(url), "action": "reject"})
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=rejected")
    page = await b.http.get(url + "?ok=rejected")
    assert "Rejected." in page.text and ">rejected<" in page.text and "Reject this proposal" not in page.text
    # Nothing was sent, the assistant sees the state, and a later apply is refused.
    assert ark.patches == []
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["state"] == "rejected" and "Rejected by the user" in got["next_step"]
    r = await b.http.post(url, data={"csrf": await b.csrf(url), "action": "apply"})
    assert r.status_code == 303 and "err=" in r.headers["location"] and ark.patches == []
    r = await b.http.post(url, data={"csrf": await b.csrf(url), "action": "reject"})
    assert r.status_code == 303 and "err=" in r.headers["location"]
    await b.aclose()


async def test_users_are_isolated(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token_a = await linked_user(stack)
    p = structured(
        await call(
            h,
            token_a,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Arcane Signet"}]},
        )
    )
    amy = {
        "sub": "user-2",
        "email": "amy@example.test",
        "name": "Amy",
        "preferred_username": "amy",
        "groups": [],
    }
    token_b = await linked_user(stack, amy, "amy", "pw-amy")
    assert structured(await call(h, token_b, "list_my_proposals"))["proposals"] == []
    out = structured(await call(h, token_b, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "not_found"
    out = structured(await call(h, token_b, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and ark.patches == []
    decks = structured(await call(h, token_b, "list_my_decks"))
    assert [d["id"] for d in decks["decks"]] == ["43"]
    # Alice's deck is public, so Amy can read it; its owner field says it is not hers.
    deck = structured(await call(h, token_b, "get_deck", {"deck_ref": "42"}))
    assert deck["ok"] and deck["owner"] == "alice"
    assert structured(await call(h, token_b, "account_status"))["archidekt_username"] == "amy"
    # Amy's browser cannot open Alice's proposal page
    b = Browser(h)
    await b.login()
    r = await b.http.get(f"/proposals/{p['proposal_id']}")
    assert r.status_code == 404
    await b.aclose()


async def test_expired_archidekt_session_marks_link_for_relink(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.tokens.clear()  # provider forgot our session
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"] is False and out["error"] == "not_linked" and "Relink" in out["message"]
    assert h.db.get_link("user-1") is None


async def test_parse_deck_export_tool(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    csv_text = (Path(__file__).parent / "fixtures" / "sample_deck.csv").read_text(encoding="utf-8")
    out = structured(await call(h, token, "parse_deck_export", {"csv_text": csv_text}))
    assert out["ok"] and out["card_count"] == 100 and "Commander" in out["categories"]
    assert out["decklist_text"].startswith("1 Aesi, Tyrant of Gyre Strait (cmr) 365 *F* [Commander]\n")
    assert len(out["decklist_text"].splitlines()) == 72 and out["sideboard_text"] == ""


# -- ingest and import ------------------------------------------------------------
async def test_get_deck_reads_public_decks_without_a_link(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)  # signed in, nothing linked
    out = structured(await call(h, token, "get_deck", {"deck_ref": "https://archidekt.com/decks/42/reap"}))
    assert out["ok"] and out["card_count"] == 100 and out["url"] == "https://archidekt.com/decks/42"
    assert out["decklist_text"].startswith("1 Aesi, Tyrant of Gyre Strait (cmr) 365 *F* [Commander]\n")
    assert "15 Forest (cmr) 510" in out["decklist_text"] and out["sideboard_text"] == ""
    assert not any(line.split(" ", 1)[0].isalpha() for line in out["decklist_text"].splitlines())
    private = structured(await call(h, token, "get_deck", {"deck_ref": "43"}))
    assert private["ok"] is False and private["error"] == "not_found"


async def test_get_deck_falls_back_to_own_session_for_private_decks(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(
        stack,
        {
            "sub": "user-2",
            "email": "amy@example.test",
            "name": "Amy",
            "preferred_username": "amy",
            "groups": [],
        },
        "amy",
        "pw-amy",
    )
    out = structured(await call(h, token, "get_deck", {"deck_ref": "43"}))
    assert out["ok"] and out["name"] == "Amy's deck"


async def test_parse_decklist_tool(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    text = (
        "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n15 Forest\n1x Sol Ring (CMR) 436 [Ramp]\nSB: 1 Negate\n"
    )
    out = structured(await call(h, token, "parse_decklist", {"text": text}))
    assert out["ok"] and out["card_count"] == 17 and out["sideboard_count"] == 1
    # commander first, with its category so the text reads back as a Commander deck; no headers
    assert out["decklist_text"].splitlines()[0] == "1 Aesi, Tyrant of Gyre Strait [Commander]"
    assert "Negate" not in out["decklist_text"] and out["sideboard_text"] == "1 Negate\n"
    assert "Commander\n" not in out["decklist_text"] and "Sideboard" not in out["decklist_text"]
    names = {c["name"]: c for c in out["cards"]}
    assert names["Sol Ring"]["set_code"] == "cmr" and names["Sol Ring"]["categories"] == ["Ramp"]
    assert names["Aesi, Tyrant of Gyre Strait"]["categories"] == ["Commander"]
    bad = structured(await call(h, token, "parse_decklist", {"text": "\n\n"}))
    assert bad["ok"] is False


async def test_propose_new_deck_from_decklist_and_apply(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    text = "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n2 Forest\n1 Sol Ring [Ramp]\nSB: 1 Island\n"
    p = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": "Test import", "deck_format": "commander", "decklist_text": text},
        )
    )
    assert p["ok"] and p["kind"] == "create_deck" and p["deck_id"] == "new" and p["state"] == "pending"
    assert p["diff"].splitlines()[0] == "New commander deck 'Test import' (5 cards, private)"
    assert "+1 Sol Ring [Ramp]" in p["diff"] and "+1 Island [Sideboard]" in p["diff"]
    assert len(ark.decks) == 2  # nothing created yet
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"], a
    assert a["state"] == "applied" and a["result"]["verified"] is True
    new_id = int(a["result"]["deck_id"])
    assert a["deck_id"] == str(new_id) and a["result"]["deck_url"] == f"https://archidekt.com/decks/{new_id}"
    deck = ark.decks[new_id]
    assert deck["name"] == "Test import" and deck["deckFormat"] == 3 and deck["private"] is True
    counts = {c["card"]["oracleCard"]["name"]: c["quantity"] for c in deck["cards"]}
    # the SB: row lands in Archidekt's Sideboard category instead of being dropped
    assert counts == {"Aesi, Tyrant of Gyre Strait": 1, "Forest": 2, "Sol Ring": 1, "Island": 1}
    sol = next(c for c in deck["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    assert sol["categories"] == ["Ramp"]
    island = next(c for c in deck["cards"] if c["card"]["oracleCard"]["name"] == "Island")
    assert island["categories"] == ["Sideboard"]
    # the review page shows the new deck link
    b = Browser(h)
    await b.login()
    page = await b.http.get(f"/proposals/{p['proposal_id']}")
    assert "New deck: Test import" in page.text and f"archidekt.com/decks/{new_id}" in page.text
    await b.aclose()


async def test_new_deck_apply_reads_deck_back_when_create_returns_no_cards(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    ark.create_returns_full_deck = False
    token = await linked_user(stack)
    p = structured(
        await call(
            h, token, "propose_new_deck", {"name": "Short reply", "decklist_text": "1 Sol Ring\n2 Forest"}
        )
    )
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["state"] == "applied" and a["result"]["verified"] is True, a
    new_id = int(a["result"]["deck_id"])
    assert ("GET", f"/api/decks/{new_id}/") in ark.calls  # verified by reading it back
    counts = {c["card"]["oracleCard"]["name"]: c["quantity"] for c in ark.decks[new_id]["cards"]}
    assert counts == {"Sol Ring": 1, "Forest": 2}


async def test_new_deck_id_is_kept_when_read_back_fails(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    ark.create_returns_full_deck = False
    ark.fail_deck_reads = True
    token = await linked_user(stack)
    p = structured(
        await call(h, token, "propose_new_deck", {"name": "No read", "decklist_text": "1 Sol Ring"})
    )
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] is False
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["state"] == "failed" and got["deck_id"] == str(ark.next_deck_id), got
    assert got["deck_url"] == f"https://archidekt.com/decks/{ark.next_deck_id}"


async def test_propose_new_deck_from_csv_and_cards(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    csv_text = (Path(__file__).parent / "fixtures" / "sample_deck.csv").read_text(encoding="utf-8")
    p = structured(await call(h, token, "propose_new_deck", {"name": "From CSV", "csv_text": csv_text}))
    assert p["ok"] and "(100 cards, private)" in p["diff"]
    p2 = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {
                "name": "From cards",
                "deck_format": "modern",
                "cards": [{"card_name": "Sol Ring", "quantity": 4}],
            },
        )
    )
    assert p2["ok"] and p2["diff"].startswith("New modern deck 'From cards' (4 cards")
    bad = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": "x", "cards": [{"card_name": "Forest"}], "decklist_text": "1 Sol Ring"},
        )
    )
    assert bad["ok"] is False and bad["error"] == "invalid"
    bad = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": "x", "deck_format": "pauper edh", "decklist_text": "1 Sol Ring"},
        )
    )
    assert bad["error"] == "invalid" and "deck_format" in bad["message"]


async def test_propose_new_deck_requires_link_and_apply_respects_switch(stack_ro: Stack) -> None:
    h, ark = stack_ro.h, stack_ro.ark
    token = await mcp_token(h)
    out = structured(await call(h, token, "propose_new_deck", {"name": "x", "decklist_text": "1 Sol Ring"}))
    assert out["ok"] is False and out["error"] == "not_linked"
    token = await linked_user(stack_ro)
    p = structured(await call(h, token, "propose_new_deck", {"name": "x", "decklist_text": "1 Sol Ring"}))
    assert p["ok"]
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["error"] == "writes_disabled" and len(ark.decks) == 2


async def test_propose_refuses_decks_the_linked_account_does_not_own(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    ark.private.discard(43)  # Amy's deck is public now
    token = await linked_user(stack)  # linked as alice
    readable = structured(await call(h, token, "get_deck", {"deck_ref": "43"}))
    assert readable["ok"] and readable["owner"] == "amy"
    out = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "43", "changes": [{"action": "add", "card_name": "Arcane Signet"}]},
        )
    )
    assert out["ok"] is False and out["error"] == "forbidden" and "amy" in out["message"]
    assert structured(await call(h, token, "list_my_proposals"))["proposals"] == []


@pytest.fixture
async def stack_browser_only(tmp_path: Path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(tmp_path, writes_enabled=True, archidekt_base="https://ark.test/api")
    client = _client(settings, ark)
    async with running(Harness(settings, idp, archidekt=client)) as h:
        yield Stack(h, ark)


async def test_apply_over_mcp_is_refused_by_default(stack_browser_only: Stack) -> None:
    h, ark = stack_browser_only.h, stack_browser_only.ark
    token = await linked_user(stack_browser_only)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Arcane Signet"}]},
        )
    )
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required"
    assert out["review_url"] == p["review_url"] and ark.patches == []
    assert "review page" in p["next_step"]
    # the browser page still applies
    b = Browser(h)
    await b.login()
    url = f"/proposals/{p['proposal_id']}"
    r = await b.http.post(url, data={"csrf": await b.csrf(url), "action": "apply"})
    assert r.status_code == 303 and len(ark.patches) == 1
    await b.aclose()


async def test_verification_ignores_name_case(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "add", "card_name": "arcane signet"},
                    {"action": "remove", "card_name": "acidic slime"},
                ],
            },
        )
    )
    assert p["ok"], p
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["state"] == "applied", a
    p2 = structured(
        await call(h, token, "propose_new_deck", {"name": "lower", "decklist_text": "1 sol ring\n2 forest\n"})
    )
    a2 = structured(await call(h, token, "apply_proposal", {"proposal_id": p2["proposal_id"]}))
    assert a2["ok"] and a2["state"] == "applied" and a2["result"]["verified"], a2
    # 42, 43, the new deck; the edit's backup copy sits in the backup folder
    assert len([d for d in ark.decks if d not in ark.deck_folder]) == 3


async def test_deck_refs_from_other_sites_are_rejected(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    out = structured(await call(h, token, "get_deck", {"deck_ref": "https://www.moxfield.com/decks/42"}))
    assert out["ok"] is False and out["error"] == "invalid" and "archidekt.com" in out["message"]
    assert structured(await call(h, token, "get_deck", {"deck_ref": "https://www.archidekt.com/decks/42/x"}))[
        "ok"
    ]
    assert structured(await call(h, token, "get_deck", {"deck_ref": "archidekt.com/decks/42"}))["ok"]
    for ref in ("moxfield.com/decks/42", "evil.test:8443/decks/42", "www.moxfield.com/decks/42"):
        assert structured(await call(h, token, "get_deck", {"deck_ref": ref}))["error"] == "invalid", ref


async def test_propose_new_deck_input_caps(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    out = structured(
        await call(h, token, "propose_new_deck", {"name": "x", "decklist_text": "1 Sol Ring\n" * 20_000})
    )
    assert out["ok"] is False and out["error"] == "invalid" and "200 kB" in out["message"]
    out = structured(
        await call(h, token, "propose_new_deck", {"name": "x", "cards": [{"card_name": "a" * 201}]})
    )
    assert out["error"] == "invalid" and "too long" in out["message"]


async def test_an_app_revoking_its_token_keeps_browser_sessions(stack: Stack) -> None:
    """/revoke ends only that grant's token family; the member stays signed in on the web
    (an app must not be able to sign the member out of every browser)."""
    h = stack.h
    client = await h.register()
    tokens = await h.tokens_for(client)
    b = Browser(h)
    await b.login()
    assert (await b.http.get("/account")).status_code == 200
    r = await h.http.post(
        "/revoke",
        data={
            "token": tokens["refresh_token"],
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )
    assert r.status_code == 200, r.text
    r = await b.http.get("/account")
    assert r.status_code == 200
    await b.aclose()


@pytest.fixture
async def stack_manual(tmp_path: Path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path,
        writes_enabled=True,
        approval_mode_default="manual",
        archidekt_base="https://ark.test/api",
    )
    client = _client(settings, ark)
    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(fake_mystic_forge()))
    async with running(Harness(settings, idp, archidekt=client, mf_proxy=proxy)) as h:
        yield Stack(h, ark)


async def test_manual_mode_needs_the_users_own_press(stack_manual: Stack) -> None:
    """In the default (manual) approval mode an assistant cannot apply over MCP at all: the
    proposal says so, apply_proposal answers browser_required, and the review page applies."""
    h, ark = stack_manual.h, stack_manual.ark
    token = await linked_user(stack_manual)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Arcane Signet"}]},
        )
    )
    assert p["approval_mode"] == "manual" and p["assistant_may_apply"] is False
    assert "Do not call apply_proposal" in p["next_step"]
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required" and ark.patches == []
    assert out["review_url"].endswith(p["proposal_id"]) and out["approval_mode"] == "manual"
    assert structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))["state"] == (
        "pending"
    )
    # The browser page is the member's own press.
    b = Browser(h)
    await b.login()
    url = f"/proposals/{p['proposal_id']}"
    r = await b.http.post(url, data={"csrf": await b.csrf(url), "action": "apply"})
    assert r.status_code == 303 and len(ark.patches) == 1
    await b.aclose()


async def test_list_my_decks_filters_to_the_linked_owner(stack: Stack) -> None:
    """Archidekt ignores the old owner= filter and returns everyone's decks (verified live
    2026-10-04). The gateway asks with ownerUsername and still checks each entry's owner."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.ignore_owner_username = True  # worst case: the site returns strangers' decks too
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"] and [d["id"] for d in out["decks"]] == ["42"]
    ark.ignore_owner_username = False
    out = structured(await call(h, token, "list_my_decks"))
    assert [d["id"] for d in out["decks"]] == ["42"]
    assert all(c[1].endswith("/decks/v3/") for c in ark.calls if "decks/v3" in c[1])


async def test_list_my_decks_includes_private_decks(stack: Stack) -> None:
    """The linked user's private decks are listed too. If the site's ownerUsername listing leaves
    them out, the authenticated ownerId listing picks them up; strangers never see them."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.decks[44] = dict(ark.decks[42], id=44, name="Secret brew", updatedAt="2026-10-03T00:00:00Z")
    ark.private.add(44)
    ark.username_listing_hides_private = True
    out = structured(await call(h, token, "list_my_decks"))
    assert out["ok"], out
    by_id = {d["id"]: d for d in out["decks"]}
    assert set(by_id) == {"42", "44"}, by_id
    assert by_id["44"]["private"] is True and by_id["42"]["private"] is False
    assert out["decks"][0]["id"] == "44"  # newest first across both listings
    assert "43" not in by_id  # Amy's private deck is never shown to Alice
    # Both listings went out signed in with the JWT scheme; a Bearer header would be anonymous.
    assert ark.list_auth_schemes and set(ark.list_auth_schemes) == {"JWT"}


async def test_apply_resolves_common_and_double_faced_cards(stack: Stack) -> None:
    """Opt and Swamp drown in a substring search; a double-faced card is stored under its full
    'Front // Back' name. All three must resolve and verify (bugs found live 2026-10-04)."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    changes = [
        {"action": "add", "card_name": "Opt"},
        {"action": "add", "card_name": "Swamp", "quantity": 2},
        {"action": "add", "card_name": "Delver of Secrets"},
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert p["ok"], p
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"], a
    assert a["state"] == "applied" and a["result"]["verified"] is True
    ids = sorted(e["cards"][0]["cardid"] for e in ark.patches)
    assert ids == [9007, 9008, 9009]
    counts = {c["card"]["oracleCard"]["name"]: c["quantity"] for c in ark.decks[42]["cards"]}
    assert counts["Opt"] == 1 and counts["Swamp"] == 2
    assert counts["Delver of Secrets // Insectile Aberration"] == 1
    # A second edit finds the double-faced card by its front face instead of adding a duplicate.
    p2 = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "remove", "card_name": "Delver of Secrets"}]},
        )
    )
    assert p2["ok"] and p2["diff"].splitlines() == ["-1 Delver of Secrets // Insectile Aberration"]


async def test_resolve_card_id_prefers_named_printing() -> None:
    from mtg_gateway.archidekt import ArchidektClient, Pacer

    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        if request.url.params.get("exact") == "true":
            return httpx.Response(
                200,
                json={
                    "results": [
                        {"id": 1, "oracleCard": {"name": "Sol Ring"}, "edition": {"editioncode": "mar"}},
                        {
                            "id": 2,
                            "oracleCard": {"name": "Sol Ring"},
                            "edition": {"editioncode": "cmr"},
                            "collectorNumber": "490",
                        },
                    ]
                },
            )
        return httpx.Response(200, json={"results": []})

    client = ArchidektClient(
        "https://ark.test/api",
        "ua",
        Pacer(0.0),
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    assert await client.resolve_card_id("t", "Sol Ring") == 1
    assert await client.resolve_card_id("t", "sol ring", set_code="CMR") == 2
    assert await client.resolve_card_id("t", "Sol Ring", set_code="xxx", collector_number="490") == 2
    assert seen[0]["exact"] == "true" and seen[0]["name"] == "Sol Ring"
    with pytest.raises(Exception, match="no Archidekt printing"):
        await client.resolve_card_id("t", "Nonexistent Card")
    assert seen[-1].get("exact") is None  # the fallback substring search was tried


async def test_snapshots_list_and_restore_through_a_proposal(stack: Stack) -> None:
    """An applied edit leaves a snapshot; list_snapshots shows it and propose_restore_snapshot makes
    an ordinary proposal that puts every deck row back (printing, finish, categories, quantity),
    applied through the normal path. Audit rows name the OAuth client (id and name) that acted."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    assert structured(await call(h, token, "list_snapshots")) == {"ok": True, "snapshots": []}
    changes = [
        {"action": "add", "card_name": "Arcane Signet", "quantity": 1, "category": "Ramp"},
        {"action": "remove", "card_name": "Acidic Slime"},
        {"action": "set_quantity", "card_name": "Forest", "quantity": 14},
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["state"] == "applied", a
    # Meanwhile the user also changed a finish and the commander's category by hand on Archidekt.
    aesi = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"].startswith("Aesi"))
    assert aesi["modifier"] == "Foil" and aesi["categories"] == ["Commander"]
    aesi["modifier"], aesi["categories"] = "Normal", None
    ark.decks[42]["updatedAt"] = "2026-10-03T01:00:00Z"

    snaps = structured(await call(h, token, "list_snapshots"))["snapshots"]
    assert len(snaps) == 1 and snaps[0]["snapshot_id"] == a["snapshot_id"]
    assert snaps[0]["deck_id"] == "42" and snaps[0]["deck_name"] == "Sample Commander Deck"
    assert snaps[0]["proposal_id"] == p["proposal_id"] and snaps[0]["card_count"] == 100
    assert "deck" not in snaps[0] and "cards" not in snaps[0]  # the listing is not the whole deck

    # Another user sees nothing and cannot restore this snapshot.
    other = await linked_user(
        stack, {"sub": "user-2", "email": "amy@example.com", "name": "Amy"}, "amy", "pw-amy"
    )
    assert structured(await call(h, other, "list_snapshots"))["snapshots"] == []
    r = structured(await call(h, other, "propose_restore_snapshot", {"snapshot_id": a["snapshot_id"]}))
    assert r["ok"] is False and r["error"] == "not_found"

    r = structured(await call(h, token, "propose_restore_snapshot", {"snapshot_id": a["snapshot_id"]}))
    assert r["ok"] and r["state"] == "pending" and r["deck_id"] == "42" and r["kind"] == "restore", r
    lines = r["diff"].splitlines()
    assert lines[0].startswith(f"Restore to snapshot {a['snapshot_id']} taken ")
    assert set(lines[1:]) == {
        "+1 Acidic Slime (cmr 421)",
        "+1 Aesi, Tyrant of Gyre Strait (cmr 365, Foil) [Commander]",
        "-1 Aesi, Tyrant of Gyre Strait (cmr 365)",
        "-1 Arcane Signet (xxx 1) [Ramp]",
        "14 -> 15 Forest (cmr 510)",
    }, lines
    assert len(ark.patches) == 3  # proposing wrote nothing

    a2 = structured(await call(h, token, "apply_proposal", {"proposal_id": r["proposal_id"]}))
    assert a2["ok"] and a2["state"] == "applied" and a2["result"]["verified"] is True, a2
    assert a2["result"]["restored_snapshot_id"] == a["snapshot_id"]
    rows = {c["card"]["oracleCard"]["name"]: c for c in ark.decks[42]["cards"]}
    assert rows["Acidic Slime"]["quantity"] == 1 and rows["Acidic Slime"]["card"]["id"] == 5000
    assert rows["Forest"]["quantity"] == 15 and "Arcane Signet" not in rows
    assert rows["Aesi, Tyrant of Gyre Strait"]["modifier"] == "Foil"
    assert rows["Aesi, Tyrant of Gyre Strait"]["categories"] == ["Commander"]
    assert a2["snapshot_id"] != a["snapshot_id"]  # the restore itself is undoable
    snaps = structured(await call(h, token, "list_snapshots"))["snapshots"]
    assert [s["snapshot_id"] for s in snaps] == [a2["snapshot_id"], a["snapshot_id"]]  # newest first
    again = structured(await call(h, token, "propose_restore_snapshot", {"snapshot_id": a["snapshot_id"]}))
    assert again["ok"] is False and "already matches" in again["message"]

    with h.db.tx() as c:
        rows_ = c.execute(
            "SELECT event, client_id, detail_json FROM audit_log WHERE sub = 'user-1' "
            "AND event IN ('proposal_created', 'proposal_applied') ORDER BY id"
        ).fetchall()
    assert len(rows_) == 4
    for row in rows_:
        detail = json.loads(row["detail_json"])
        assert row["client_id"] and row["client_id"] != "__browser__"
        assert detail["client_name"] == "t"  # the name the client registered with
    assert json.loads(rows_[2]["detail_json"])["restores_snapshot"] == a["snapshot_id"]


async def test_apply_keeps_a_backup_copy_on_archidekt_first(stack: Stack) -> None:
    """Before any write the deck is copied into the user's backup folder on Archidekt (the
    folder is created on first use); the copy is recorded on the snapshot and the result,
    listed by list_snapshots, and kept off list_my_decks."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "remove", "card_name": "Acidic Slime"}]},
        )
    )
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["state"] == "applied", a
    folders = ark.folders["alice"]
    assert [f["name"] for f in folders] == ["MTG Gateway backups"] and folders[0]["private"] is True
    copy_id = int(a["result"]["backup_deck_id"])
    assert a["result"]["backup_url"] == f"https://archidekt.com/decks/{copy_id}"
    copy = ark.decks[copy_id]
    assert ark.deck_folder[copy_id] == folders[0]["id"] and copy_id in ark.private
    assert copy["name"].startswith("Sample Commander Deck (backup 20") and copy["name"].endswith(" UTC)")
    assert p["proposal_id"] in copy["description"] and a["snapshot_id"] in copy["description"]
    # the copy holds the deck as it was before the edit; the original lost Acidic Slime
    before = {c["card"]["oracleCard"]["name"]: c["quantity"] for c in copy["cards"]}
    after = {c["card"]["oracleCard"]["name"]: c["quantity"] for c in ark.decks[42]["cards"]}
    assert before["Acidic Slime"] == 1 and after.get("Acidic Slime", 0) == 0
    snaps = structured(await call(h, token, "list_snapshots"))["snapshots"]
    assert snaps[0]["backup_deck_id"] == str(copy_id)
    assert snaps[0]["backup_url"].endswith(f"/decks/{copy_id}")

    # The backup copy never shows up in the user's deck list; decks in other folders do.
    ark.folders["alice"].append({"id": 999, "name": "Brews", "private": False})
    ark.decks[46] = dict(ark.decks[42], id=46, name="Brew in a folder")
    ark.deck_folder[46] = 999
    listing = structured(await call(h, token, "list_my_decks"))["decks"]
    assert {d["id"] for d in listing} == {"42", "46"}
    assert next(d for d in listing if d["id"] == "46")["folder"] == "Brews"

    # A second edit reuses the folder instead of creating another one.
    p2 = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Arcane Signet"}]},
        )
    )
    assert p2["ok"], p2
    a2 = structured(await call(h, token, "apply_proposal", {"proposal_id": p2["proposal_id"]}))
    assert a2["ok"], a2
    assert [f["name"] for f in ark.folders["alice"]] == ["MTG Gateway backups", "Brews"]
    assert a2["result"]["backup_deck_id"] != a["result"]["backup_deck_id"]


async def test_apply_refuses_to_edit_when_the_backup_fails(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    ark.fail_backup = True
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "remove", "card_name": "Acidic Slime"}]},
        )
    )
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] is False and a["error"] == "backup_failed", a
    assert ark.patches == []  # nothing was sent
    assert (
        structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))["state"]
        == "pending"
    )
    # Once Archidekt answers again the same proposal applies.
    ark.fail_backup = False
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["state"] == "applied" and len(ark.patches) == 1
    with h.db.tx() as c:
        events = [r[0] for r in c.execute("SELECT event FROM audit_log WHERE sub = 'user-1' ORDER BY id")]
    assert "backup_failed" in events and "proposal_failed" not in events


async def test_pinned_printing_is_added_as_its_own_row_with_its_finish(stack: Stack) -> None:
    """An add that names set code and collector number (a scan, or the user's pick) goes to
    Archidekt as exactly that printing, with the finish Archidekt offers for it, as a new row
    next to the deck's other printing of the same card."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    changes = [
        {
            "action": "add",
            "card_name": "Sol Ring",
            "set_code": "SLD",
            "collector_number": "1074",
            "finish": "etched",
        },
        {"action": "add", "card_name": "Swamp", "quantity": 2, "foil": True},
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert p["ok"], p
    assert set(p["diff"].splitlines()) == {"+2 Swamp (Foil)", "1 -> 2 Sol Ring (SLD 1074, Etched)"}
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"], a
    sent = {e["cardid"]: e for e in (pt["cards"][0] for pt in ark.patches)}
    assert sent[124026]["action"] == "add" and sent[124026]["modifications"] == {
        "quantity": 1,
        "companion": False,
        "flippedDefault": False,
        "modifier": "Etched",
    }
    assert sent[9008]["action"] == "add" and sent[9008]["modifications"]["modifier"] == "Foil"
    assert sent[9008]["modifications"]["quantity"] == 2
    rows = [c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring"]
    assert sorted((r["card"]["id"], r["modifier"], r["quantity"]) for r in rows) == [
        (5052, "Normal", 1),
        (124026, "Etched", 1),
    ]


async def test_pinned_printing_already_in_the_deck_grows_that_row(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    row = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    row["card"]["id"] = 91043  # the deck already holds CMR 472
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "add", "card_name": "sol ring", "set_code": "cmr", "collector_number": "472"}
                ],
            },
        )
    )
    assert p["ok"] and p["diff"] == "1 -> 2 Sol Ring (CMR 472)", p
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"], a
    assert len(ark.patches) == 1
    sent = ark.patches[0]["cards"][0]
    assert sent["action"] == "modify" and sent["deckRelationId"] == row["id"]
    assert sent["modifications"]["quantity"] == 2 and sent["modifications"]["modifier"] == "Normal"


async def test_pinned_printing_that_is_missing_or_another_card_refuses_the_edit(stack: Stack) -> None:
    """A hand-picked printing must exist and must be the named card; otherwise apply refuses
    with Archidekt's answer, before any snapshot, backup or write."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    for change, message in [
        (
            {"action": "add", "card_name": "Island", "set_code": "m21", "collector_number": "267"},
            "M21 #267 is Swamp, not 'Island'; nothing was added",
        ),
        (
            {"action": "add", "card_name": "Swamp", "set_code": "m21", "collector_number": "9999"},
            "Archidekt has no printing M21 #9999 of 'Swamp'; nothing was added",
        ),
    ]:
        p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
        assert p["ok"], p  # the proposal records what was asked; Archidekt is consulted at apply time
        a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
        assert a["ok"] is False and a["error"] == "not_found", a
        assert a["message"] == message
        got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
        assert got["state"] == "failed" and got["snapshot_id"] is None
    assert ark.patches == [] and ark.folders == {} and len(ark.decks) == 2
    snaps = structured(await call(h, token, "list_snapshots"))["snapshots"]
    assert snaps == []


async def test_printing_fields_are_validated_on_the_change(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    bad = [
        ([{"action": "add", "card_name": "Swamp", "set_code": "m21"}], "go together"),
        ([{"action": "remove", "card_name": "Swamp", "finish": "foil"}], "apply to add only"),
        ([{"action": "add", "card_name": "Swamp", "finish": "glossy"}], "'nonfoil', 'foil' or 'etched'"),
        ([{"action": "add", "card_name": "Swamp", "set_code": "m21!", "collector_number": "1"}], "set code"),
        (
            [
                {"action": "add", "card_name": "Swamp", "set_code": "m21", "collector_number": "267"},
                {"action": "add", "card_name": "swamp", "set_code": "m21", "collector_number": "268"},
            ],
            "one pinned printing per card name",
        ),
    ]
    for changes, text in bad:
        out = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
        assert out["ok"] is False and out["error"] == "invalid" and text in out["message"], out


async def test_pinned_and_plain_adds_of_one_card_combine(stack: Stack) -> None:
    """A scan can hand back one copy matched by set and number and another by name only: the
    pinned copy becomes its own row, the plain copy grows the deck's existing row."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    changes = [
        {"action": "add", "card_name": "Sol Ring", "set_code": "sld", "collector_number": "1074"},
        {"action": "add", "card_name": "Sol Ring", "quantity": 2},
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert p["ok"] and p["diff"] == "1 -> 4 Sol Ring", p  # no single printing to show
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"], a
    sent = sorted(
        (e["action"], e["cardid"], e["modifications"]["quantity"])
        for e in (pt["cards"][0] for pt in ark.patches)
    )
    assert sent == [("add", 124026, 1), ("modify", 5052, 3)]


async def test_new_deck_says_when_a_listed_printing_was_not_found(stack: Stack) -> None:
    """A pasted list's set and number are honoured when Archidekt has that printing; when it
    does not, the card is added by name and the result says so instead of swapping quietly."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": "Printings", "decklist_text": "1 Sol Ring (cmr) 472\n1 Swamp (xyz) 9\n1 Forest\n"},
        )
    )
    assert p["ok"], p
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"], a
    sent = {e["cardid"] for e in (pt["cards"][0] for pt in ark.patches)}
    assert {91043, 9008, 9004} <= sent  # CMR 472 pinned; Swamp and Forest by name
    assert a["result"]["printing_notes"] == [
        "Swamp: XYZ 9 not found on Archidekt, added its default printing instead"
    ]


async def test_new_deck_verify_fails_when_a_finish_or_printing_is_lost(stack: Stack) -> None:
    """The read-back after a new deck is created compares printing ids and finishes as well as
    names and counts: a Foil row stored as Normal, or a pinned printing stored as another one,
    fails the verify instead of passing because the names and counts agree."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    text = "1 Sol Ring (sld) 1075 *F*\n1 Swamp (m21) 267\n1 Forest\n"

    async def create() -> dict:
        p = structured(await call(h, token, "propose_new_deck", {"name": "Finishes", "decklist_text": text}))
        assert p["ok"], p
        return structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))

    good = await create()
    assert good["ok"] and good["result"]["verified"] is True, good
    ark.add_rows_lose_finish = True
    lost = await create()
    assert lost["ok"] is False and lost["error"] == "verify_mismatch", lost
    assert "Sol Ring (printing or finish)" in lost["message"] and "Swamp" not in lost["message"], lost
    ark.add_rows_lose_finish = False
    ark.add_rows_swap_printing = {87176: 9008}  # Swamp M21 267 stored as the default Swamp
    swapped = await create()
    assert swapped["ok"] is False and swapped["error"] == "verify_mismatch", swapped
    assert "Swamp (printing or finish)" in swapped["message"] and "Sol Ring" not in swapped["message"]
    ark.add_rows_swap_printing = {}


async def test_simulation_calls_take_the_shape_mystic_forge_accepts(stack: Stack) -> None:
    """The fake's goldfish_run, goldfish_ab and validate_decklist take one pydantic ``params``
    model like the real Mystic Forge, so a flat call would fail validation here as it does live.
    Deck 42 has a Commander category, so its report carries a goldfish block with the Metrics."""
    h = stack.h
    token = await linked_user(stack)
    rep = structured(
        await call(h, token, "run_deck_report", {"deck_ref": "42", "games": 20, "options": {"seed": 1}})
    )
    assert rep["ok"], rep
    gf, val = rep["goldfish"], rep["validation"]
    assert gf["ok"] is True and "## Metrics" in gf["text"] and "validation error" not in gf["text"], gf
    assert val["ok"] is True and val["text"].startswith("Validated"), val
    # A/B of two pasted lists: the simulator gets the gateway's rendering (commander first, no
    # headers, sideboard left out), not the raw paste, and the deltas come back.
    text_a = "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n// Lands\n30 Island\nSideboard\n1 Negate\n"
    text_b = text_a.replace("30 Island", "29 Island\n1 Forest")
    out = structured(
        await call(h, token, "compare_decks", {"a": text_a, "b": text_b, "simulate": True, "games": 20})
    )
    assert out["ok"], out
    assert out["goldfish_ab"]["ok"] is True and "## Deltas" in out["goldfish_ab"]["text"], out["goldfish_ab"]
    # A list with no commander is not simulated: Mystic Forge would silently take the first line
    # as the commander and simulate a 59-card deck.
    plain = "24 Forest\n36 Grizzly Bears\n"
    out = structured(
        await call(h, token, "compare_decks", {"a": plain, "b": plain, "simulate": True, "games": 20})
    )
    # the A/B was asked for and refused, so the result fails at the top (the diff is still there)
    assert out["ok"] is False and out["error"] == "simulation_failed" and "Commander" in out["message"], out
    assert out["goldfish_ab"]["ok"] is False and "added" in out, out
    # Mystic Forge answers an unknown commander with a sentence, not a tool error; the report
    # records that as a failed simulation rather than a successful one.
    unknown = "Commander\n1 Unknown Card\n\n99 Island\n"
    out = structured(
        await call(h, token, "compare_decks", {"a": unknown, "b": unknown, "simulate": True, "games": 20})
    )
    assert out["ok"] and out["goldfish_ab"]["ok"] is True  # the A/B fake does not refuse; run does:
    bad = structured(
        await call(h, token, "run_deck_report", {"deck_ref": "42", "games": 20, "options": {"opponents": 9}})
    )
    assert bad["ok"] is False and "opponents" in bad["message"], bad


async def test_another_users_decks_are_the_public_listing_only(stack: Stack) -> None:
    """search_decks by owner is Archidekt's public listing, fetched without any member's token:
    Amy's private deck 43 never appears, whoever asks, and the request itself is anonymous."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)  # alice, linked
    ark.list_auth_schemes.clear()
    out = structured(await call(h, token, "search_decks", {"owner": "amy", "order_by": "-updatedAt"}))
    assert out["ok"], out
    assert [d["id"] for d in out["decks"]] == [] or all(str(d["id"]) != "43" for d in out["decks"]), out
    assert "Amy's deck" not in json.dumps(out)
    assert ark.list_auth_schemes and set(ark.list_auth_schemes) == {""}, ark.list_auth_schemes
    # The same tool on her own name lists only what Archidekt shows anonymously as well.
    mine = structured(await call(h, token, "search_decks", {"owner": "alice", "order_by": "-updatedAt"}))
    assert mine["ok"] and any(str(d["id"]) == "42" for d in mine["decks"]), mine
    assert set(ark.list_auth_schemes) == {""}


async def test_pasted_lists_are_reported_but_not_stored(stack: Stack) -> None:
    """run_deck_report and deck_stats take a pasted list as well as a deck: the list gets the same
    validation and goldfish run and comes back with stored=false, and History stays as it was
    (0.7.1's goldfish_run could simulate a pasted list; its owner keeps that)."""
    h = stack.h
    token = await linked_user(stack)
    before = structured(await call(h, token, "list_deck_reports", {}))["reports"]
    text = "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n// Lands\n99 Island\n"
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": text, "games": 20}))
    assert rep["ok"] and rep["stored"] is False and rep["deck"]["id"] is None, rep
    assert rep["goldfish"]["ok"] is True and "## Metrics" in rep["goldfish"]["text"], rep["goldfish"]
    assert rep["validation"]["ok"] is True, rep["validation"]
    assert rep["stats"]["card_count"] == 100 and rep["stats"]["card_data"] == "unavailable"
    # Double-faced cards reach the simulator by their front face (Archidekt names both faces;
    # Scryfall's collection lookup, which the research service uses, knows only the front).
    faces = (
        "Commander\n1 Enduring Angel // Angelic Enforcer\n\n98 Plains\n"
        "1 Elbrus, the Binding Blade // Withengar Unbound\n"
    )
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": faces, "games": 20}))
    assert rep["ok"] and rep["validation"]["text"].startswith("Validated"), rep["validation"]
    gf = rep["goldfish"]
    assert gf["ok"] and "Commander: 1 Enduring Angel [Commander] |" in gf["text"], gf
    assert "Enduring Angel // Angelic Enforcer" in rep["decklist_text"]  # the member's own list keeps both
    after = structured(await call(h, token, "list_deck_reports", {}))["reports"]
    assert len(after) == len(before)
    # No commander: the structural checks still come back, the simulation is refused as such.
    plain = structured(await call(h, token, "run_deck_report", {"deck_ref": "24 Forest\n36 Grizzly Bears\n"}))
    assert plain["ok"] is False and plain["error"] == "simulation_failed", plain
    assert "Commander" in plain["message"] and plain["stats"]["checks"], plain
    assert plain["goldfish"]["ok"] is False and "Commander" in plain["goldfish"]["text"], plain
    stats = structured(
        await call(
            h,
            token,
            "deck_stats",
            {"deck_ref": "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n// Main\n2 Opt\n97 Island\n"},
        )
    )
    assert stats["ok"] and stats["deck"]["id"] is None, stats
    checks = stats["stats"]["checks"]
    assert checks["singleton_violations"] == [{"name": "Opt", "quantity": 2}] and checks["ok"] is False
    bad = structured(await call(h, token, "deck_stats", {"deck_ref": "not a list at all"}))
    assert bad["ok"] is False, bad
