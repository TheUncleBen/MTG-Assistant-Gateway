"""Phases 1 to 3 against fakes: Mystic Forge proxy, browser login, account
linking, propose and apply, isolation between users."""

from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer

from mtg_gateway.archidekt import ArchidektClient, Pacer
from mtg_gateway.decklist import parse_decklist
from mtg_gateway.mf_proxy import MysticForgeProxy
from mtg_gateway.scan.scryfall import ScryfallError

from .conftest import (
    AUTO_PROXY,
    GATEWAY,
    FakeIdP,
    Harness,
    Stack,
    StackOptions,
    open_stack,
    proposal_stamp,
    sse_json,
    stack_with,
)
from .fake_archidekt import FakeArchidekt
from .fake_mystic_forge import SIM_CALLS, _ValidateDecklistInput, fake_mystic_forge
from .track_a_helpers import apply_any


def _client(settings, ark: FakeArchidekt) -> ArchidektClient:
    return ArchidektClient(settings.archidekt_base, "test-agent", Pacer(0.0), http=ark.client())


stack_options = stack_with(AUTO_PROXY)


@pytest.fixture
async def stack_ro(tmp_path: Path, idp: FakeIdP):
    async with open_stack(tmp_path, idp, StackOptions(settings={"writes_enabled": False})) as st:
        yield st


# -- helpers -----------------------------------------------------------------
async def mcp_token(h: Harness) -> str:
    client = await h.register()
    return (await h.tokens_for(client))["access_token"]


async def call(h: Harness, token: str, name: str, args: dict | None = None) -> dict:
    return await call_as_sent(h, token, name, args)


async def call_as_sent(h: Harness, token: str, name: str, args: dict | None = None) -> dict:
    """A tool call with exactly these arguments, as an assistant would send it."""
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

    async def csrf(self, path: str = "/account/details") -> str:
        r = await self.http.get(path)
        assert r.status_code == 200, r.text
        m = re.search(r"name='csrf' value='([0-9a-f]+)'", r.text)
        assert m, r.text
        return m.group(1)

    async def review_form(self, url: str) -> dict[str, str]:
        """The hidden fields of the review page ``url`` (``/proposals/<pid>``) as a person sends
        them: the CSRF token, a render stamp old enough for the click guard and the version the
        page showed (an Apply without it is refused)."""
        form = {
            "csrf": await self.csrf(url),
            "shown": proposal_stamp(self.h, self.http, url.rsplit("/", 1)[1]),
        }
        text = (await self.http.get(url)).text
        shown = re.search(r"name='version' value='(\d+)'", text)
        if shown:
            form["version"] = shown.group(1)
        # the status the page showed, and when it was set: a change held since is refused
        for key in ("seen_status", "seen_status_at"):
            got = re.search(rf"name='{key}' value='([^']*)'", text)
            if got:
                form[key] = got.group(1)
        return form

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
    page = await b.http.get("/account/details")
    assert "Link your Archidekt account" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]

    bad = await b.link("alice", "wrong")
    assert bad.status_code == 400 and "did not accept" in bad.text
    assert not h.db.get_link("user-1")

    ok = await b.link("alice", "pw-alice")
    assert ok.status_code == 303 and ok.headers["location"] == "/account/details?ok=linked"
    row = h.db.get_link("user-1")
    assert row and row["archidekt_username"] == "alice"
    # encrypted, no password
    assert "pw-alice" not in row["secret_enc"] and "tok-alice" not in row["secret_enc"]
    page = await b.http.get("/account/details?ok=linked")
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
    assert (await b.http.get("/account/details")).status_code == 302  # back to sign-in
    await b.aclose()


async def test_account_form_requires_csrf_and_session(stack: Stack) -> None:
    h = stack.h
    anon = Browser(h)
    r = await anon.http.get("/account/details")
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
    """0.7.29: a change made elsewhere to a card the proposal touches means it would no longer
    do what was reviewed: nothing is sent and the proposal waits again, marked Needs a look."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    first = ark.decks[42]["cards"][0]["card"]["oracleCard"]["name"]
    p = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "remove", "card_name": first}]},
        )
    )
    ark.bump(42)  # one more copy of that same card, added on Archidekt
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] is False and a["error"] == "stale", a
    assert a["state"] == "pending" and a["status"] == "needs_look", a
    assert ark.patches == []
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["state"] == "pending" and got["status"] == "needs_look", got
    assert got["assistant_may_apply"] is False


async def test_an_unrelated_change_elsewhere_still_applies(stack: Stack) -> None:
    """0.7.29: a change elsewhere that leaves the proposal's rows as reviewed only moves
    its baseline ("Updated"), so it still applies."""
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
    ark.bump(42)  # another card's count changed on Archidekt
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["state"] == "applied", a
    assert len(ark.patches) == 1


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
    assert p["ok"] and "switched off" in p["next_step"]
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
    r = await b.http.post(url, data={**await b.review_form(url), "action": "apply"})
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
    r = await b.http.post(url, data={**await b.review_form(url), "action": "reject"})
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=rejected")
    page = await b.http.get(url + "?ok=rejected")
    # the status label word on the proposal
    assert (
        "Rejected." in page.text
        and "Rejected</span>" in page.text
        and "Reject this proposal" not in page.text
    )
    # Nothing was sent, the assistant sees the state, and a later apply is refused.
    assert ark.patches == []
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["state"] == "rejected" and "Rejected by the user on the review page" in got["next_step"]
    assert got["closed_by"] == "review_page" and "closed_by_app" not in got
    r = await b.http.post(url, data={**await b.review_form(url), "action": "apply"})
    assert r.status_code == 303 and "err=" in r.headers["location"] and ark.patches == []
    r = await b.http.post(url, data={**await b.review_form(url), "action": "reject"})
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
    assert out["unknown_columns"] == []  # Archidekt's own header is read whole


async def test_parse_deck_export_reads_column_aliases_and_names_the_rest(stack: Stack) -> None:
    """Set and Categories (a JSON list or comma-separated) are read like Edition code and Category;
    a column it does not read is named in unknown_columns; decklist_text keeps the categories."""
    h = stack.h
    token = await mcp_token(h)
    csv_text = (
        "Quantity,Name,Set,Categories,Collector Number,Tradelist Count\n"
        '1,"Aesi, Tyrant of Gyre Strait",cmr,Commander,365,0\n'
        '1,Sol Ring,ltc,"[""Ramp"",""Artifact""]",284,0\n'
        "2,Island,m21,Lands,265,0\n"
        "1,Opt,m21,Maybeboard,59,0\n"
    )
    out = structured(await call(h, token, "parse_deck_export", {"csv_text": csv_text}))
    assert out["ok"] and out["unknown_columns"] == ["Tradelist Count"], out
    sol = next(c for c in out["cards"] if c["name"] == "Sol Ring")
    assert sol["set"] == "ltc" and sol["categories"] == ["Ramp", "Artifact"]
    assert out["decklist_text"] == (
        "1 Aesi, Tyrant of Gyre Strait (cmr) 365 [Commander]\n"
        "1 Sol Ring (ltc) 284 [Ramp,Artifact]\n"
        "2 Island (m21) 265 [Lands]\n"
    )
    assert out["card_count"] == 4 and out["sideboard_text"] == "1 Opt (m21) 59 [Maybeboard]\n"
    # Archidekt's own spelling wins over an alias given as well; the alias is named as not read
    both = structured(
        await call(
            h, token, "parse_deck_export", {"csv_text": "Quantity,Name,Edition Code,Set\n1,Opt,m21,xyz\n"}
        )
    )
    assert both["cards"][0]["set"] == "m21" and both["unknown_columns"] == ["Set"], both


async def test_parse_deck_export_keeps_an_etched_finish(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    csv_text = "Quantity,Name,Finish,Edition Code,Collector Number\n1,Sol Ring,Etched,cmm,410\n"
    out = structured(await call(h, token, "parse_deck_export", {"csv_text": csv_text}))
    assert out["ok"] and out["decklist_text"] == "1 Sol Ring (cmm) 410 *E*\n", out


async def test_archidekt_nodeck_rows_stay_out_of_a_new_deck_count(stack: Stack) -> None:
    """Archidekt's import text (format_archidekt's and the gateway's own) marks a maybeboard row
    ``[Maybeboard{noDeck}{noPrice}]``: written, parsed and proposed, the row is kept as a
    maybeboard row and never counted as a deck card."""
    from mtg_gateway.archidekt import FORMAT_NAMES, Deck, DeckCard
    from mtg_gateway.decks import deck_to_archidekt_text

    h = stack.h
    token = await linked_user(stack)

    def card(name: str, cats: list[str]) -> DeckCard:
        return DeckCard(
            relation_id=None, card_id=None, name=name, quantity=1, categories=cats, modifier="Normal",
            set_code="", collector_number="",
        )  # fmt: skip

    deck = Deck(
        id="1", name="t", owner="o", updated_at="", raw={}, format_id=3, format=FORMAT_NAMES[3],
        categories=[{"name": "Maybeboard", "includedInDeck": False}],
        cards=[card("Sol Ring", []), card("Arcane Signet", ["Maybeboard"])],
    )  # fmt: skip
    text = deck_to_archidekt_text(deck)
    assert "1x Arcane Signet [Maybeboard{noDeck}{noPrice}]" in text
    parsed = structured(await call(h, token, "parse_decklist", {"text": text}))
    assert parsed["card_count"] == 1 and parsed["sideboard_count"] == 1, parsed
    assert parsed["decklist_text"] == "1 Sol Ring\n"
    assert parsed["sideboard_text"] == "1 Arcane Signet [Maybeboard]\n"
    p = structured(
        await call(
            h, token, "propose_new_deck", {"name": "No deck", "deck_format": "custom", "decklist_text": text}
        )
    )
    assert p["ok"], p
    assert p["diff"].splitlines()[0] == (
        "New custom deck 'No deck' (1 cards, 1 more in the maybeboard or sideboard, private)"
    )
    assert "+1 Arcane Signet [Maybeboard] (maybeboard/sideboard)" in p["diff"]
    signet = next(c for c in p["changes"]["cards"] if c["name"] == "Arcane Signet")
    assert (
        signet["categories"] == ["Maybeboard"] and signet["in_deck"] is False and signet["no_price"] is True
    )
    sol = next(c for c in p["changes"]["cards"] if c["name"] == "Sol Ring")
    assert "in_deck" not in sol and "no_price" not in sol


async def test_a_new_deck_keeps_a_custom_categorys_switches(stack: Stack) -> None:
    """A custom category flagged {noDeck} or {noPrice} keeps its switches in the new deck (0.7.41
    made it plain, so its cards counted, and warned): the proposal shows the category as a line of
    its own and does not count its cards; applying makes the deck, then the category with its
    switches, and only then adds the cards, and reads the switches back."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    text = "1 Sol Ring\n1 Opt [Considering{noDeck}{noPrice}]\n1 Island [Maybeboard{noDeck}]\n"
    p = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": "Flags", "deck_format": "custom", "decklist_text": text},
        )
    )
    assert p["ok"], p
    assert p["diff"].splitlines()[0] == (
        "New custom deck 'Flags' (1 cards, 2 more in the maybeboard or sideboard, private)"
    )
    assert "New category “Considering”: Not in deck on, Not in price on." in p["diff"]
    assert not p.get("warnings"), p
    opt = next(c for c in p["changes"]["cards"] if c["name"] == "Opt")
    assert opt["in_deck"] is False and opt["categories"] == ["Considering"]
    assert p["changes"]["category_flags"] == [
        {"name": "Considering", "includedInDeck": False, "includedInPrice": False, "isPremier": False}
    ]
    a = await apply_any(h, token, p["proposal_id"])
    assert a["ok"] and a["state"] == "applied" and a["result"]["verified"] is True, a
    new_id = int(a["result"]["deck_id"])
    assert [(c["deck"], c["name"]) for c in ark.category_creates] == [(new_id, "Considering")]
    # the category is made before any card goes in
    calls = [c for c in ark.calls if str(new_id) in c[1] or "createCategory" in c[1]]
    first_card = next(i for i, c in enumerate(calls) if "modifyCards" in c[1])
    assert any("createCategory" in c[1] for c in calls[:first_card]), calls
    cat = next(c for c in ark.decks[new_id]["categories"] if c["name"] == "Considering")
    assert cat["includedInDeck"] is False and cat["includedInPrice"] is False


def test_new_deck_inputs_mark_rows_outside_the_deck() -> None:
    """Every input of propose_new_deck says which rows sit outside the deck the same way."""
    from mtg_gateway.decks import normalise_cards

    csv_rows = normalise_cards(csv_text="Quantity,Name,Category\n1,Sol Ring,Ramp\n1,Opt,Maybeboard\n")
    assert [c.get("in_deck", True) for c in csv_rows] == [True, False]
    deck_json = json.dumps(
        {
            "cards": [
                {"name": "Sol Ring", "quantity": 1},
                {"name": "Opt", "categories": ["Maybe"], "in_deck": False},
            ]
        }
    )
    assert [c.get("in_deck", True) for c in normalise_cards(json_text=deck_json)] == [True, False]
    listed = normalise_cards(decklist_text="1 Sol Ring\nSB: 1 Negate\n1 Opt [Later{noDeck}]\n")
    assert [c.get("in_deck", True) for c in listed] == [True, False, False]
    assert listed[1]["categories"] == ["Sideboard"] and listed[2]["categories"] == ["Later"]


async def test_cmdr_marker_reads_as_the_commander_everywhere(stack: Stack) -> None:
    """A trailing *CMDR* (which goldfish_annotate accepts) marks the commander for every tool that
    reads a pasted list, so a list checked there is not refused by the report tools."""
    h = stack.h
    token = await linked_user(stack)
    text = "1 Aesi, Tyrant of Gyre Strait *CMDR*\n1 Sol Ring\n1 Arcane Signet\n97 Island"
    parsed = structured(await call(h, token, "parse_decklist", {"text": text}))
    assert parsed["ok"] and parsed["decklist_text"].startswith("1 Aesi, Tyrant of Gyre Strait [Commander]\n")
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": text, "games": 20}))
    assert rep["ok"] and rep["stats_brief"]["commanders"] == ["Aesi, Tyrant of Gyre Strait"], rep
    cmp = structured(await call(h, token, "compare_decks", {"a_list": text, "b_list": text}))
    assert cmp["ok"], cmp
    bad = structured(await call(h, token, "parse_decklist", {"text": "1 Sol Ring\n-1 Island"}))
    assert bad["ok"] is False and "-1 Island" in bad["message"], bad


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
    # the SB: row is created too, but is not a deck card
    assert p["diff"].splitlines()[0] == (
        "New commander deck 'Test import' (4 cards, 1 more in the maybeboard or sideboard, private)"
    )
    assert "+1 Sol Ring [Ramp]" in p["diff"] and "+1 Island [Sideboard]" in p["diff"]
    assert len(ark.decks) == 2  # nothing created yet
    a = await apply_any(h, token, p["proposal_id"])
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
    a = await apply_any(h, token, p["proposal_id"])
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
    a = await apply_any(h, token, p["proposal_id"])
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
    async with open_stack(tmp_path, idp, StackOptions(settings={"writes_enabled": True})) as st:
        yield st


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
    r = await b.http.post(url, data={**await b.review_form(url), "action": "apply"})
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
    a = await apply_any(h, token, p["proposal_id"])
    assert a["ok"] and a["state"] == "applied", a
    p2 = structured(
        await call(h, token, "propose_new_deck", {"name": "lower", "decklist_text": "1 sol ring\n2 forest\n"})
    )
    a2 = await apply_any(h, token, p2["proposal_id"])
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
    assert (await b.http.get("/account/details")).status_code == 200
    r = await h.http.post(
        "/revoke",
        data={
            "token": tokens["refresh_token"],
            "client_id": client["client_id"],
            "client_secret": client["client_secret"],
        },
    )
    assert r.status_code == 200, r.text
    r = await b.http.get("/account/details")
    assert r.status_code == 200
    await b.aclose()


@pytest.fixture
async def stack_manual(tmp_path: Path, idp: FakeIdP):
    manual = AUTO_PROXY.but(settings={"writes_enabled": True, "approval_mode_default": "manual"})
    async with open_stack(tmp_path, idp, manual) as st:
        yield st


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
    r = await b.http.post(url, data={**await b.review_form(url), "action": "apply"})
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


async def test_resolve_card_prefers_named_printing() -> None:
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
    assert (await client.resolve_card("t", "Sol Ring"))["id"] == 1
    assert (await client.resolve_card("t", "sol ring", set_code="CMR"))["id"] == 2
    assert (await client.resolve_card("t", "Sol Ring", set_code="xxx", collector_number="490"))["id"] == 2
    assert seen[0]["exact"] == "true" and seen[0]["name"] == "Sol Ring"
    with pytest.raises(Exception, match="Archidekt doesn't have"):
        (await client.resolve_card("t", "Nonexistent Card"))["id"]
    assert seen[-1].get("exact") is None  # the fallback substring search was tried


async def test_a_restore_puts_back_colour_tags_mana_values_and_category_order(stack: Stack) -> None:
    """A restore puts back what Semi-auto and Full auto can change by themselves, a row's colour tag
    and custom mana value, and the order of its categories (the first is the primary one, which
    decides whether the card is in the deck); the verification checks all three."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    deck = ark.decks[42]
    ring = next(c for c in deck["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    ring["categories"] = ["Ramp", "Artifact"]
    changes = [{"action": "set_quantity", "card_name": "Forest", "quantity": 14}]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    a = await apply_any(h, token, p["proposal_id"])
    assert a["ok"] and a["state"] == "applied", a
    # then the tag, the mana value and the primary category change (an assistant, or by hand)
    ring = next(c for c in deck["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    ring["label"], ring["customCmc"], ring["categories"] = "Have,#37d67a", 5, ["Artifact", "Ramp"]
    deck["updatedAt"] = "2026-10-03T01:00:00Z"
    r = structured(await call(h, token, "propose_restore_snapshot", {"snapshot_id": a["snapshot_id"]}))
    assert r["ok"], r
    lines = r["diff"].splitlines()
    assert "Sol Ring: color tag Have (#37d67a) -> no tag" not in lines  # the reordered row is re-made
    assert any(line.startswith("+1 Sol Ring") and line.endswith("[Ramp, Artifact]") for line in lines), lines
    assert any(line.startswith("-1 Sol Ring") and line.endswith("[Artifact, Ramp]") for line in lines), lines
    a2 = await apply_any(h, token, r["proposal_id"])
    assert a2["ok"] and a2["state"] == "applied" and a2["result"]["verified"] is True, a2
    rings = [c for c in deck["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring"]
    assert len(rings) == 1 and rings[0]["categories"] == ["Ramp", "Artifact"], rings
    assert not (rings[0].get("label") or "").split(",")[0] and rings[0].get("customCmc") is None
    # a tag and a mana value alone are put back on the row itself
    rings[0]["label"], rings[0]["customCmc"] = "Want,#ff0000", 3
    deck["updatedAt"] = "2026-10-03T02:00:00Z"
    r = structured(await call(h, token, "propose_restore_snapshot", {"snapshot_id": a["snapshot_id"]}))
    assert r["ok"], r
    lines = r["diff"].splitlines()
    assert "Sol Ring: color tag Want (#ff0000) -> no tag" in lines, lines
    assert "Sol Ring: custom mana value 3 -> none" in lines, lines
    # one Archidekt entry, two review lines: the head counts what the member reads
    assert lines[0].endswith(" (2 changes)"), lines[0]
    a3 = await apply_any(h, token, r["proposal_id"])
    assert a3["ok"] and a3["state"] == "applied" and a3["result"]["verified"] is True, a3
    ring = next(c for c in deck["cards"] if c["card"]["oracleCard"]["name"] == "Sol Ring")
    assert not (ring.get("label") or "").split(",")[0] and ring.get("customCmc") is None


async def test_snapshots_list_and_restore_through_a_proposal(stack: Stack) -> None:
    """An applied edit leaves a snapshot; list_snapshots shows it and propose_restore_snapshot makes
    an ordinary proposal that puts every deck row back (printing, finish, categories, quantity),
    applied through the normal path. Audit rows name the OAuth client (id and name) that acted."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    assert structured(await call(h, token, "list_snapshots")) == {
        "ok": True,
        "snapshots": [],
        "has_more": False,
    }
    changes = [
        {"action": "add", "card_name": "Arcane Signet", "quantity": 1, "category": "Ramp"},
        {"action": "remove", "card_name": "Acidic Slime"},
        {"action": "set_quantity", "card_name": "Forest", "quantity": 14},
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    a = await apply_any(h, token, p["proposal_id"])
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
        # Ramp came with the Arcane Signet the edit added: the deck had no such category then
        # (0.7.42: a restore deletes a category added since, once its cards are gone)
        "Delete category “Ramp” (added since the snapshot). Archidekt tabs already open on this deck "
        "show the old categories until they are reloaded.",
    }, lines
    assert len(ark.patches) == 3  # proposing wrote nothing

    a2 = await apply_any(h, token, r["proposal_id"])
    assert a2["ok"] and a2["state"] == "applied" and a2["result"]["verified"] is True, a2
    assert a2["result"]["restored_snapshot_id"] == a["snapshot_id"]
    rows = {c["card"]["oracleCard"]["name"]: c for c in ark.decks[42]["cards"]}
    assert rows["Acidic Slime"]["quantity"] == 1 and rows["Acidic Slime"]["card"]["id"] == 5000
    assert rows["Forest"]["quantity"] == 15 and "Arcane Signet" not in rows
    assert "Ramp" not in {c["name"] for c in ark.decks[42]["categories"]}
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
    assert [f["name"] for f in folders] == ["MTG Gateway Backups"] and folders[0]["private"] is True
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
    assert [f["name"] for f in ark.folders["alice"]] == ["MTG Gateway Backups", "Brews"]
    # within 15 minutes of the first copy, the second edit shares it (its own snapshot
    # still holds the exact deck before it); tests/test_backup_limits.py covers the rest.
    assert a2["result"]["backup_deck_id"] == a["result"]["backup_deck_id"]
    assert a2["result"]["backup_reused"] is True and a2["snapshot_id"] != a["snapshot_id"]


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
            "Archidekt doesn't have “Swamp” in that printing (M21 #9999), so nothing was added.",
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


async def test_a_card_no_magic_card_is_called_is_refused_when_proposed(stack: Stack) -> None:
    """With the card-name catalog loaded, an add of a name that is no card is refused when the
    proposal is made (and when it is amended), instead of waiting and failing at apply. A card
    already in the deck, a double-faced card's front face and any name while the catalog is not
    loaded still pass."""
    h = stack.h
    token = await linked_user(stack)
    names = stack.h.app.state.gateway.scan.names
    assert names.known("Sol Ring") is None  # not loaded: nothing is refused
    names.set_names(["Island", "Swamp", "Delver of Secrets // Insectile Aberration"])
    bad = [{"action": "add", "card_name": "Islend"}, {"action": "add", "card_name": "Swamp"}]
    out = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": bad}))
    assert out["ok"] is False and out["error"] == "unknown_cards", out
    assert out["unknown_cards"] == ["Islend"] and "“Islend”" in out["message"], out
    good = [
        {"action": "add", "card_name": "Delver of Secrets"},
        {"action": "add", "card_name": "Sol Ring"},  # in deck 42, though not in this catalog
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": good}))
    assert p["ok"], p
    amended = structured(
        await call(
            h,
            token,
            "amend_proposal",
            {"proposal_id": p["proposal_id"], "version": p["version"], "changes": bad},
        )
    )
    assert amended["ok"] is False and amended["error"] == "unknown_cards", amended
    # a name the catalog lacks (a token it does not list, a card from a set newer than the
    # catalog) is asked of Scryfall before it is refused; an answer other than "not found" refuses
    # nothing. The tokens made most often are in the catalog (names.TOKEN_NAMES): never asked
    scan = stack.h.app.state.gateway.scan
    asked: list[str] = []

    async def named(name: str, **_kw: object) -> dict:
        asked.append(name)
        if name == "Goat":
            return {"name": "Goat"}
        if name == "Offline Card":
            raise ScryfallError("unavailable", "down")
        raise ScryfallError("not_found", "no card")

    scan.scryfall.named = named
    for name, ok in (("Treasure", True), ("Goat", True), ("Offline Card", True), ("Islend", False)):
        r = structured(
            await call(
                h,
                token,
                "propose_deck_changes",
                {"deck_id": "42", "changes": [{"action": "add", "card_name": name}], "separate": True},
            )
        )
        assert r["ok"] is ok, (name, r)
    assert asked == ["Goat", "Offline Card", "Islend"]
    assert names.known("Insectile Aberration") is True  # a back face is a card too


async def test_the_unknown_card_check_asks_scryfall_a_few_names_and_remembers_the_answers(
    stack: Stack,
) -> None:
    """Names the catalog lacks are asked of Scryfall at most five per proposal (the rest pass),
    and each answer, found or not, is kept a while, so a retried proposal asks nothing again."""
    import httpx

    from mtg_gateway.scan.scryfall import ScryfallClient

    h = stack.h
    token = await linked_user(stack)
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.params.get("exact", "")
        asked.append(name)
        if name == "Brand New Card" or name.startswith("New Set Card"):
            return httpx.Response(200, json={"object": "card", "name": name})
        return httpx.Response(404, json={"object": "error", "status": 404, "details": "no card"})

    scan = h.app.state.gateway.scan
    scan.scryfall = ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    scan.names.set_names(["Island", "Swamp"])
    typos = [f"Islend {i}" for i in range(7)]
    adds = [{"action": "add", "card_name": n} for n in ["Brand New Card", *typos]]
    for _ in range(2):
        out = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": adds}))
        assert out["ok"] is False and out["unknown_cards"] == typos[:4], out
    assert asked == ["Brand New Card", *typos[:4]]  # five asked once; the second try asks nothing
    # a sixth name the catalog lacks is never asked and never refused, even a misspelt one
    asked.clear()
    news = [f"New Set Card {i}" for i in range(5)]
    adds = [{"action": "add", "card_name": n} for n in [*news, "Sixth Typo"]]
    out = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": adds}))
    assert out["ok"], out
    assert asked == news


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
    a = await apply_any(h, token, p["proposal_id"])
    assert a["ok"] and a["result"]["verified"], a
    sent = {e["cardid"] for pt in ark.patches for e in pt["cards"]}
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
        return await apply_any(h, token, p["proposal_id"])

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


async def test_new_deck_keeps_colour_labels_and_verify_checks_them(stack: Stack) -> None:
    """A pasted list's ^Name,#rrggbb^ tags, and a CSV export's Label column, go to the new deck's
    rows; a tag Archidekt does not keep fails the read-back instead of passing on the cards."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    text = "1 Sol Ring (sld) 1075 ^Test,#008000^\n1 Swamp ^Proxy^\n1 Forest ^,#656565^\n"
    csv_text = 'Quantity,Name,Label\n1,Sol Ring,"Test,#008000"\n1,Forest,\n'

    async def create(**src: str) -> dict:
        p = structured(await call(h, token, "propose_new_deck", {"name": "Labels", **src}))
        assert p["ok"], p
        return await apply_any(h, token, p["proposal_id"])

    good = await create(decklist_text=text)
    assert good["ok"] and good["result"]["verified"] is True, good
    rows = {r["card"]["oracleCard"]["name"]: r for r in ark.decks[int(good["result"]["deck_id"])]["cards"]}
    assert rows["Sol Ring"]["label"] == "Test,#008000"
    assert rows["Swamp"]["label"] == "Proxy,#656565"  # a bare name gets the default grey
    assert "label" not in rows["Forest"]  # Archidekt's empty tag is no tag
    from_csv = await create(csv_text=csv_text)
    assert from_csv["ok"] and from_csv["result"]["verified"] is True, from_csv
    made = ark.decks[int(from_csv["result"]["deck_id"])]["cards"]
    rows = {r["card"]["oracleCard"]["name"]: r for r in made}
    assert rows["Sol Ring"]["label"] == "Test,#008000"
    ark.add_rows_lose_label = True
    lost = await create(decklist_text=text)
    assert lost["ok"] is False and lost["error"] == "verify_mismatch", lost
    assert "Sol Ring (label)" in lost["message"] and "Forest" not in lost["message"], lost
    ark.add_rows_lose_label = False


async def test_simulation_calls_take_the_shape_mystic_forge_accepts(stack: Stack) -> None:
    """The fake's goldfish_run, goldfish_ab and validate_decklist take one pydantic ``params``
    model like the real Mystic Forge, so a flat call would fail validation here as it does live.
    Deck 42 has a Commander category, so its report carries a goldfish block with the Metrics."""
    h = stack.h
    token = await linked_user(stack)
    rep = structured(
        await call(
            h,
            token,
            "run_deck_report",
            {"deck_ref": "42", "games": 20, "options": {"seed": 1, "forge_seed": 2}},
        )
    )
    assert rep["ok"], rep
    # forge_seed is the gateway's own: the simulator gets seed only
    ((_, sent),) = [c for c in SIM_CALLS if c[0] == "goldfish_run"][-1:]
    assert sent["seed"] == 1 and "forge_seed" not in sent, sent
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
    # A short list with no commander is not simulated: Mystic Forge would silently take the first
    # line as the commander (a list of 60 or more is a 60-card game, see test_sixty_card_decks_...).
    plain = "20 Forest\n20 Grizzly Bears\n"
    out = structured(
        await call(h, token, "compare_decks", {"a": plain, "b": plain, "simulate": True, "games": 20})
    )
    # the A/B was asked for and refused, so the result fails at the top (the diff is still there)
    assert out["ok"] is False and out["error"] == "simulation_failed" and "60 cards" in out["message"], out
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


async def test_sixty_card_decks_are_simulated_as_sixty_card_games(
    stack: Stack, caplog: pytest.LogCaptureFixture
) -> None:
    """A deck without a commander and with 60 or more cards (Modern here) is simulated as a 60-card
    game: Mystic Forge gets constructed=true and the main deck only, never the sideboard. A short
    list, and a Commander-format deck missing its commander, are refused with a reason; Commander
    decks are unchanged."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    modern = json.loads(json.dumps(ark.decks[42]))
    modern.update(
        id=44,
        name="Modern deck",
        deckFormat=2,
        categories=[{"name": "Sideboard", "isPremier": False, "includedInDeck": False}],
    )
    for row in modern["cards"]:
        row["categories"] = None
    modern["cards"][0]["categories"] = ["Sideboard"]  # one sideboard card, never simulated
    sideboard_name = modern["cards"][0]["card"]["oracleCard"]["name"]
    ark.decks[44] = modern
    ark.users["alice"]["decks"].append(44)
    SIM_CALLS.clear()
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": "44", "games": 20}))
    assert rep["ok"], rep
    assert rep["goldfish"]["ok"] is True and "Format: 60-card" in rep["goldfish"]["text"], rep["goldfish"]
    ((tool, sent),) = SIM_CALLS
    assert tool == "goldfish_run" and sent["constructed"] is True, sent
    # the research service's validation is a Commander check (100 cards): a 60-card deck skips it
    assert rep["validation"] is None, rep["validation"]
    assert sideboard_name not in str(sent["deck"]) and "Commander" not in str(sent["deck"]), sent
    # the stored report reads back as a 60-card game
    got = structured(await call(h, token, "get_deck_report", {"report_id": rep["report_id"]}))
    assert "Format: 60-card" in got["goldfish"]["text"], got
    # the copies of each main-deck card are kept with the run; the sideboard card is not
    kept = got["goldfish"]["quantities"]
    sent_total = sum(c.quantity for c in parse_decklist(sent["deck"]) if c.zone == "main")
    assert sum(kept.values()) == sent_total and sideboard_name.split(" // ")[0] not in kept, kept
    # The same deck under the Commander format, without a commander: refused, not guessed.
    ark.decks[44]["deckFormat"] = 3
    ark.decks[44]["updatedAt"] = "2026-10-03T00:00:00Z"
    SIM_CALLS.clear()
    with caplog.at_level(logging.WARNING, logger="mtg_gateway.tools_reports"):
        rep = structured(await call(h, token, "run_deck_report", {"deck_ref": "44", "games": 20}))
    assert rep["ok"] is False and rep["error"] == "simulation_failed" and "Commander" in rep["message"], rep
    assert SIM_CALLS == []
    # the reason is in the gateway's log too, where an operator (or a CI log) can read it
    assert any("simulation did not run: No solo test" in r.getMessage() for r in caplog.records), (
        caplog.records
    )
    # Pasted lists: 60 cards plays, 40 is refused, a Commander list keeps its commander game.
    sixty = "4 Lightning Bolt\n20 Mountain\n36 Goblin Guide\nSideboard\n2 Smash to Smithereens\n"
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": sixty, "games": 20}))
    assert rep["ok"] and rep["stored"] is True and rep["source"]["label"] == "Pasted list", rep
    assert SIM_CALLS[-1][1]["constructed"] is True and "Smash" not in str(SIM_CALLS[-1][1]["deck"])
    assert rep["validation"] is None, rep["validation"]
    short = "4 Lightning Bolt\n20 Mountain\n16 Goblin Guide\n"
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": short, "games": 20}))
    assert rep["ok"] is False and "at least 60 cards" in rep["message"] and "has 40" in rep["message"], rep
    commander = "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n99 Island\n"
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": commander, "games": 20}))
    assert rep["ok"] and SIM_CALLS[-1][1]["constructed"] is False, rep
    assert rep["validation"]["ok"] is True, rep  # a Commander list is still validated
    # A 100-card paste with no commander marked is a Commander deck, not a 60-card game.
    calls = len(SIM_CALLS)
    unmarked = "1 Aesi, Tyrant of Gyre Strait\n99 Island\n"
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": unmarked, "games": 20}))
    assert rep["ok"] is False and "commander unmarked" in rep["message"], rep
    assert len(SIM_CALLS) == calls
    # A/B: two 60-card lists compare as 60-card games; a Commander list against a 60-card one
    # is refused.
    other = sixty.replace("36 Goblin Guide", "35 Goblin Guide\n1 Monastery Swiftspear")
    out = structured(
        await call(
            h, token, "compare_decks", {"a_list": sixty, "b_list": other, "simulate": True, "games": 20}
        )
    )
    assert out["ok"] and out["goldfish_ab"]["ok"], out
    assert SIM_CALLS[-1][0] == "goldfish_ab" and SIM_CALLS[-1][1]["constructed"] is True
    out = structured(
        await call(
            h, token, "compare_decks", {"a_list": sixty, "b_list": commander, "simulate": True, "games": 20}
        )
    )
    assert out["ok"] is False and "one deck has a commander" in out["message"], out


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


async def test_pasted_lists_are_reported_and_kept_as_pasted_lists(stack: Stack) -> None:
    """run_deck_report and deck_stats take a pasted list as well as a deck: the list gets the same
    validation and goldfish run (0.7.1's goldfish_run could simulate a pasted list; its owner keeps
    that), and since 0.7.46 the run is kept as one of the member's reports, labelled "Pasted
    list" and filed under no deck (owner feedback F1: every simulation is a report)."""
    h = stack.h
    token = await linked_user(stack)
    before = structured(await call(h, token, "list_deck_reports", {}))["reports"]
    text = "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n// Lands\n99 Island\n"
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": text, "games": 20}))
    assert rep["ok"] and rep["stored"] is True and rep["report_id"], rep
    assert rep["deck_id"] == "" and rep["deck_url"] is None, rep
    assert rep["source"] == {"kind": "list", "label": "Pasted list"}, rep["source"]
    # A pasted list has no name: it reads as the pasted list of its commander.
    assert rep["deck_name"] == "Pasted list (Aesi, Tyrant of Gyre Strait)", rep["deck_name"]
    kept = structured(await call(h, token, "get_deck_report", {"report_id": rep["report_id"]}))
    assert kept["ok"] and kept["goldfish"]["ok"] and kept["source"]["label"] == "Pasted list", kept
    assert rep["goldfish"]["ok"] is True and "## Metrics" in rep["goldfish"]["text"], rep["goldfish"]
    assert rep["validation"]["ok"] is True, rep["validation"]
    assert rep["stats_brief"]["card_count"] == 100 and rep["stats_brief"]["card_data"] == "unavailable"
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
    assert len(after) == len(before) + 2 and {r["source"]["kind"] for r in after[:2]} == {"list"}
    # No commander and under 60 cards: the structural checks still come back, the simulation is
    # refused as such (60 or more is a 60-card game, see test_sixty_card_decks_...).
    plain = structured(await call(h, token, "run_deck_report", {"deck_ref": "24 Forest\n26 Grizzly Bears\n"}))
    assert plain["ok"] is False and plain["error"] == "simulation_failed", plain
    assert "Commander" in plain["message"] and "legal" in plain["stats_brief"], plain
    kept = structured(await call(h, token, "get_deck_report", {"report_id": plain["report_id"]}))
    assert kept["stats"]["checks"], kept
    assert plain["goldfish"]["ok"] is False and "at least 60 cards" in plain["goldfish"]["text"], plain
    stats = structured(
        await call(
            h,
            token,
            "deck_stats",
            {"deck_ref": "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n// Main\n2 Opt\n97 Island\n"},
        )
    )
    assert stats["ok"] and stats["deck"]["id"] is None and stats["deck"]["name"] == "Unnamed deck", stats
    checks = stats["stats"]["checks"]
    assert checks["singleton_violations"] == [{"name": "Opt", "quantity": 2}] and checks["ok"] is False
    bad = structured(await call(h, token, "deck_stats", {"deck_ref": "not a list at all"}))
    assert bad["ok"] is False, bad


async def test_validate_decklist_gets_the_gateways_singleton_check() -> None:
    """validate_decklist (Mystic Forge) checks names, size and color identity only, so a 100-card
    list with two Sol Rings came back PASSED while deck_stats flagged it. The gateway adds a
    Gateway checks section and turns the headline to ISSUES FOUND."""
    passed = "# Validation: PASSED\n\nAll 3 unique cards verified on Scryfall.\nDeck size: 100 cards."
    mf = MCPServer(name="mystic-forge-validate")

    @mf.tool(name="validate_decklist", description="validate a decklist")
    async def validate_decklist(params: _ValidateDecklistInput) -> str:
        return passed

    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(mf))
    two = "1 Aesi, Tyrant of Gyre Strait\n2 Sol Ring\n97 Island"
    bad = await proxy.call("validate_decklist", {"decklist": two, "commander": "Aesi"})
    text = bad.content[0].text
    assert text.startswith("# Validation: ISSUES FOUND") and "## Gateway checks" in text, text
    assert "**more than one copy (singleton):** Sol Ring ×2" in text
    assert bad.structured_content is None or "ISSUES FOUND" in json.dumps(bad.structured_content)
    one = "1 Aesi, Tyrant of Gyre Strait\n1 Sol Ring\n98 Island\n"
    text = (await proxy.call("validate_decklist", {"decklist": one})).content[0].text
    assert text.startswith("# Validation: PASSED") and "Singleton: no card has more than one copy." in text
    # basic lands and cards allowed in any number are not duplicates
    rats = await proxy.call("validate_decklist", {"decklist": "1 Aesi\n30 Relentless Rats\n69 Island"})
    assert rats.content[0].text.startswith("# Validation: PASSED")


async def test_direct_validate_decklist_reads_front_faces(stack: Stack) -> None:
    """QA-004: reports sent double-faced cards by their front face, but a direct validate_decklist
    passed "A // B" through, and the research service (Scryfall's collection lookup) did not find
    them. The proxy now sends front faces too, with the same helper."""
    h = stack.h
    token = await mcp_token(h)
    faces = (
        "1 Enduring Angel // Angelic Enforcer\n1 Elbrus, the Binding Blade // Withengar Unbound\n98 Plains"
    )
    out = await call(
        h, token, "validate_decklist", {"decklist": faces, "commander": "Enduring Angel // Angelic Enforcer"}
    )
    text = out["content"][0]["text"]
    assert text.startswith("Validated 3 lines") and "not found" not in text, text


async def test_a_busy_research_refusal_says_when_to_retry() -> None:
    """QA-008: the busy refusal is structured (error busy) and now carries retry_after: the seconds
    until the member's oldest running call is stopped at the latest (the call deadline)."""
    from mtg_gateway.mf_proxy import busy_retry_after, is_busy

    proxy = MysticForgeProxy(
        "http://mf.test/mcp", client_factory=lambda: Client(fake_mystic_forge()), queue_wait=0.2
    )
    proxy._listed = True
    proxy._in_flight["u1"] = 2
    proxy._started["u1"] = [time.monotonic() - 20, time.monotonic() - 5]
    out = await proxy.call("scryfall_named", {"name": "Sol Ring"}, owner="u1")
    assert is_busy(out) and out.structured_content["error"] == "busy"
    assert 99 <= busy_retry_after(out) <= 100  # 120 s deadline, 20 s gone
    assert out.structured_content["message"].startswith("2 research calls")
    assert "still running after a short wait" in out.structured_content["message"]
    # a call that ran and ended frees its slot and its start time
    proxy._in_flight.clear()
    proxy._started.clear()
    ok = await proxy.call("scryfall_named", {"name": "Sol Ring"}, owner="u1")
    assert not ok.is_error and proxy._started == {} and proxy._in_flight == {}


async def test_a_research_call_past_the_members_share_waits_its_turn_and_says_so() -> None:
    """AT-38: Claude sends tool calls in parallel, and a third research call was refused at once
    (2 per member). It now waits, briefly and bounded (queue_wait), for one of the member's calls
    to finish, runs, and its answer says it waited. The share itself is unchanged."""
    import anyio

    proxy = MysticForgeProxy(
        "http://mf.test/mcp", client_factory=lambda: Client(fake_mystic_forge()), queue_wait=5.0
    )
    proxy._listed = True
    proxy._in_flight["u1"] = 2
    proxy._started["u1"] = [time.monotonic(), time.monotonic()]
    seen: list[int] = []

    async def finish_one() -> None:
        await anyio.sleep(1.2)
        seen.append(proxy._in_flight["u1"])
        proxy._in_flight["u1"] = 1
        proxy._started["u1"].pop()

    async with anyio.create_task_group() as tg:
        tg.start_soon(finish_one)
        out = await proxy.call("scryfall_named", {"name": "Sol Ring"}, owner="u1")
    assert seen == [2] and not out.is_error, out  # it ran only once a slot was free
    text = out.content[0].text
    assert text.startswith("(This call waited 1 s for your other research calls to finish.)"), text
    assert proxy._in_flight == {"u1": 1}  # its own slot is given back
    assert proxy.max_calls_per_user == 2  # never raised
    # no wait, no note
    plain = await proxy.call("scryfall_named", {"name": "Sol Ring"}, owner="u2")
    assert "waited" not in json.dumps(plain.model_dump(mode="json"))


class _ListingDown:
    """A Mystic Forge client whose tool listing fails while ``down`` is set (as a fresh gateway's
    first listing can, while the network attaches); calls still reach the fake Mystic Forge."""

    down = True
    forwarded: list[str] = []

    def __init__(self) -> None:
        self.inner = Client(fake_mystic_forge())

    async def __aenter__(self) -> _ListingDown:
        await self.inner.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.inner.__aexit__(*exc)

    async def list_tools(self):
        if _ListingDown.down:
            raise TimeoutError
        return await self.inner.list_tools()

    async def call_tool(self, name: str, arguments: dict):
        _ListingDown.forwarded.append(name)
        return await self.inner.call_tool(name, arguments)


def _listing_down_proxy() -> MysticForgeProxy:
    _ListingDown.down, _ListingDown.forwarded = True, []
    return MysticForgeProxy("http://mf.test/mcp", client_factory=_ListingDown)  # type: ignore[arg-type]


async def test_a_wrapped_call_keeps_its_shape_when_the_tool_listing_failed() -> None:
    """When Mystic Forge's first tool listing failed, a call that arrived inside params was sent
    flat (the proxy knew no tool took params), Mystic Forge refused it, and the report's simulation
    failed. The caller knew the shape, so it is now kept."""
    proxy = _listing_down_proxy()
    out = await proxy.call(
        "goldfish_run", {"params": {"deck": "1 Sol Ring\n59 Island", "n": 20}}, internal=True
    )
    text = out.content[0].text
    assert not out.is_error and "## Metrics" in text, text
    assert proxy._listed is False and _ListingDown.forwarded == ["goldfish_run"]


async def test_a_flat_call_is_refused_when_the_tool_listing_failed() -> None:
    """A flat call has no shape to go by while the listing is down: the proxy tries one listing
    now, even within the back-off, and if that fails too it refuses, worth retrying, without
    sending anything."""
    proxy = _listing_down_proxy()
    await proxy.tools()  # failed once: the back-off is running
    out = await proxy.call("goldfish_run", {"deck": "1 Sol Ring\n59 Island", "n": 20}, internal=True)
    assert out.is_error and out.structured_content["error"] == "unavailable", out
    assert out.structured_content["retry_after"] == 60
    assert out.content[0].text == (
        "the research service is not ready yet and nothing was sent; try again later"
    )
    assert _ListingDown.forwarded == []
    # once Mystic Forge lists its tools, the same flat call is wrapped and answered
    _ListingDown.down = False
    ok = await proxy.call("goldfish_run", {"deck": "1 Sol Ring\n59 Island", "n": 20}, internal=True)
    assert not ok.is_error and "## Metrics" in ok.content[0].text, ok
    assert proxy._listed and _ListingDown.forwarded == ["goldfish_run"]


async def test_a_report_still_simulates_when_the_first_tool_listing_failed(stack: Stack) -> None:
    """The e2e failure: a report made while Mystic Forge's first tool listing had failed came back
    simulation_failed. It now runs its goldfish games."""
    h = stack.h
    token = await linked_user(stack)
    proxy = stack.gw.mf_proxy  # as a freshly started gateway has it: nothing listed yet
    assert proxy is stack.gw.reports.mf
    _ListingDown.down, _ListingDown.forwarded = True, []
    proxy._client_factory, proxy._listed, proxy._wrapped, proxy._tools = _ListingDown, False, set(), []
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": "42", "games": 20}))
    assert rep["ok"] and rep["goldfish"]["ok"] is True, rep
    assert "goldfish_run" in _ListingDown.forwarded
    rows = await call(h, token, "list_deck_reports", {})
    assert structured(rows)["reports"][0]["has_goldfish"] is True, rows


async def test_run_deck_report_takes_the_members_own_snapshot(stack: Stack) -> None:
    """Consolidation bug 4: run_deck_report said it took a snapshot id and refused one. It now
    reports the member's own snapshot (as compare_decks reads it), filed under its deck with the
    snapshot id in its settings; another member's snapshot is refused."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    sub = h.db._one("SELECT sub FROM users", ())["sub"]
    old = json.loads(json.dumps(ark.decks[42]))
    old["name"] = "Aesi as it was"
    h.db.save_snapshot("snaptest1", owner_sub=sub, deck_id="42", proposal_id=None, fingerprint="f", deck=old)
    h.db.save_snapshot(
        "snaptest2", owner_sub="someone-else", deck_id="42", proposal_id=None, fingerprint="f", deck=old
    )
    rep = structured(await call(h, token, "run_deck_report", {"deck_ref": "snap_snaptest1", "games": 20}))
    assert rep["ok"] and rep["deck_id"] == "42" and rep["deck_name"] == "Aesi as it was", rep
    assert rep["settings"]["snapshot_id"] == "snap_snaptest1" and rep["status"] == "complete"
    refused = structured(await call(h, token, "run_deck_report", {"deck_ref": "snaptest2", "games": 20}))
    assert refused["ok"] is False and refused["error"] == "invalid", refused


async def test_compare_advice_is_true_to_bracket_matched_forge(stack: Stack) -> None:
    """Consolidation bug 3: since 0.7.23 each deck meets Forge opponents of its own bracket, so
    "the same options.seed in Forge" is no like-for-like comparison. The advice now says so and
    names the goldfish A/B as the like-for-like one; run_deck_report takes forge apart from simulate."""
    h = stack.h
    token = await mcp_token(h)
    r = await h.mcp(token, "tools/list", {}, rid=3)
    tools = {t["name"]: t for t in sse_json(r)["result"]["tools"]}
    compare = tools["compare_decks"]["description"]
    assert "run run_deck_report on each with the same options.seed" not in compare
    assert "like-for-like" in compare and "own bracket" in compare
    report = tools["run_deck_report"]
    assert "forge" in report["inputSchema"]["properties"] and "Preflight only" in report["description"]
    assert "60-card deck's report, or one with no commander marked, never starts it" in report["description"]
