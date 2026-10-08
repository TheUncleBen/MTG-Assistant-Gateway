"""Deck tools end to end: ingest, proposals, the write switches, the browser review page, isolation.

Archidekt is the local mock from stacks/edge-stack.yml (tests/fake_archidekt.py over HTTP), so
nothing here touches archidekt.com. The gateway starts with the stack file defaults, writes off;
the write tests switch MTG_WRITES_ENABLED and MTG_APPROVAL_MODE_DEFAULT with ``docker service update``
and switch them back at the end of the module.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from .conftest import GEN, PUBLIC_URL, Env, McpClient, browser_page, click_guarded, gateway_browser_sign_in
from .test_04_operations import GATEWAY, container_of, copy_backup, sh

CSV = (Path(__file__).resolve().parents[1] / "fixtures" / "sample_deck.csv").read_text(encoding="utf-8")
MOCK = "http://archidektmock:8000"
STATE: dict[str, str] = {}  # proposal ids shared between the ordered tests below


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def clients(env: Env) -> dict[str, McpClient]:
    """One MCP client per test user, signed in once through the browser, tokens kept in memory."""
    return {u: McpClient(env, u, client_name=f"deck e2e {u}") for u in ("alice-test", "bob-test")}


@pytest.fixture(scope="module", autouse=True)
def fresh_mock_and_default_switches():
    mock("/__e2e/reset", method="POST")
    yield
    set_switches(writes="false", mode="manual")


def mock(path: str, method: str = "GET") -> str:
    """Reach the mock's /__e2e endpoints from inside the overlay network (it publishes no port)."""
    code = (
        "import sys,urllib.request;"
        f"r=urllib.request.Request('{MOCK}{path}', method='{method}', "
        f"data=b'' if '{method}'=='POST' else None);"
        "sys.stdout.write(urllib.request.urlopen(r, timeout=10).read().decode())"
    )
    return sh("docker", "exec", container_of(GATEWAY), "python", "-c", code)


def set_switches(*, writes: str, mode: str) -> None:
    """Flip the write switch and the default approval mode the way the owner would in Portainer,
    then wait for the restart."""
    sh(
        "docker", "service", "update", "--quiet",
        "--env-add", f"MTG_WRITES_ENABLED={writes}",
        "--env-add", f"MTG_APPROVAL_MODE_DEFAULT={mode}",
        GATEWAY,
    )  # fmt: skip
    ctx = httpx.Client(verify=str(GEN / "ca.crt"), trust_env=False, timeout=5)
    deadline = time.time() + 240
    while time.time() < deadline:
        try:
            if ctx.get(f"{PUBLIC_URL}/healthz").status_code == 200:
                env_now = sh(
                    "docker",
                    "service",
                    "inspect",
                    GATEWAY,
                    "--format",
                    "{{json .Spec.TaskTemplate.ContainerSpec.Env}}",
                )
                if f'"MTG_WRITES_ENABLED={writes}"' in env_now and container_of(GATEWAY):
                    return
        except (httpx.HTTPError, subprocess.CalledProcessError):
            pass
        time.sleep(3)
    raise AssertionError("gateway did not come back after the service update")


async def ensure_unlinked(env: Env, user: str) -> None:
    """Start from 'no Archidekt account linked' even when the suite ran before on this stack."""
    async with browser_page() as page:
        await gateway_browser_sign_in(page, env, user, "/account")
        if "Unlink and delete stored session" in await page.inner_text("body"):
            await page.click("text=Unlink and delete stored session")
            await page.wait_for_url(f"{PUBLIC_URL}/account?ok=unlinked", timeout=30_000)
        assert "Link your Archidekt account" in await page.inner_text("body")


def test_tool_surface_includes_deck_and_proxied_research_tools(clients):
    async def go():
        async with clients["alice-test"].session() as s:
            names = {t.name for t in (await s.list_tools()).tools}
        assert {
            "whoami", "account_status", "list_my_decks", "get_my_deck", "get_deck", "parse_decklist",
            "parse_deck_export", "propose_new_deck", "propose_deck_changes", "list_my_proposals",
            "get_proposal", "apply_proposal",
        } <= names, names  # fmt: skip
        # research tools proxied from Mystic Forge (allow-list), nothing from its block-list, and none
        # of the duplicates a gateway tool owns (one tool per job)
        assert {
            "scryfall_named",
            "goldfish_annotate",
            "validate_decklist",
            "precon_search",
            "rules_search",
        } <= names
        assert not names & {"goldfish_start", "goldfish_state", "watchlist_list", "price_history"}, names
        assert not names & {"goldfish_run", "goldfish_ab", "archidekt_deck", "precon_diff"}, names

    run(go())


def test_parse_decklist_and_the_owners_csv_export(clients):
    async def go():
        c = clients["alice-test"]
        async with c.session() as s:
            parsed = await c.call(
                s, "parse_decklist", {"text": "1 Sol Ring\n1x Arcane Signet (cmr) 436 [Ramp]\nSB: 1 Forest\n"}
            )
            assert parsed["ok"] and parsed["card_count"] == 2 and parsed["sideboard_count"] == 1, parsed
            assert "Arcane Signet" in parsed["decklist_text"] and "Forest" in parsed["sideboard_text"]
            export = await c.call(s, "parse_deck_export", {"csv_text": CSV})
        assert export["ok"], export
        # An Archidekt CSV export with every column selected: 72 rows, 100 cards.
        assert export["distinct"] == 72 and export["card_count"] + export["side_count"] == 100, export
        assert "Commander" in export["categories"]
        assert "Aesi, Tyrant of Gyre Strait" in export["decklist_text"]

    run(go())


def test_public_deck_reads_need_no_link_and_proposals_do(clients, env: Env):
    async def go():
        await ensure_unlinked(env, "alice-test")
        c = clients["alice-test"]
        async with c.session() as s:
            status = await c.call(s, "account_status")
            assert status["linked"] is False and status["writes_enabled"] is False, status
            assert status["account_page"] == f"{PUBLIC_URL}/account"
            deck = await c.call(s, "get_deck", {"deck_ref": "42"})
            assert deck["ok"] and deck["name"] == "Sample Commander Deck", deck
            assert sum(card["quantity"] for card in deck["cards"]) == 100
            by_url = await c.call(
                s, "get_deck", {"deck_ref": "https://archidekt.com/decks/42/sample_commander_deck"}
            )
            assert by_url["ok"] and by_url["id"] == "42", by_url
            # The report's simulation reaches the real Mystic Forge in the stack with the argument
            # shape its tools take; a shape error would come back as a pydantic "validation error".
            rep = await c.call(s, "run_deck_report", {"deck_ref": "42", "games": 20})
            assert rep["ok"], rep
            for block in (rep["goldfish"], rep["validation"]):
                assert block is not None and "validation error" not in block.get("text", ""), block
                assert "Field required" not in block.get("text", ""), block
            # Deck 42 has a commander and real card names, so the real engine must simulate it:
            # a failed or refused run is a failure of this test, not a skipped assertion.
            assert rep["goldfish"]["ok"] is True, rep["goldfish"]
            assert "## Metrics" in rep["goldfish"]["text"], rep["goldfish"]
            assert rep["validation"]["ok"] is True, rep["validation"]
            assert rep["has_goldfish"] is True and rep["has_validation"] is True, rep
            private = await c.call(s, "get_deck", {"deck_ref": "43"})
            assert private["ok"] is False, private
            unlinked = await c.call(
                s,
                "propose_deck_changes",
                {
                    "deck_id": "42",
                    "changes": [{"action": "add", "card_name": "Arcane Signet", "quantity": 1}],
                },
            )
            assert unlinked["ok"] is False and unlinked["error"] == "not_linked", unlinked
            assert "/account" in unlinked["message"]
            assert (await c.call(s, "list_my_decks"))["error"] == "not_linked"

    run(go())


@pytest.mark.parametrize("user,archidekt_user", [("alice-test", "alice"), ("bob-test", "amy")])
def test_link_archidekt_account_in_the_browser(clients, env: Env, user: str, archidekt_user: str):
    async def go():
        await ensure_unlinked(env, user)
        async with browser_page() as page:
            await gateway_browser_sign_in(page, env, user, "/account")
            # a wrong password is refused by Archidekt (the mock) and shown on the page
            await page.fill("input[name=archidekt_login]", archidekt_user)
            await page.fill("input[name=archidekt_password]", "wrong")
            await page.click("text=Link account")
            await page.wait_for_load_state("domcontentloaded")
            assert "did not accept" in await page.inner_text("body")
            await page.fill("input[name=archidekt_login]", archidekt_user)
            await page.fill("input[name=archidekt_password]", f"pw-{archidekt_user}")
            await page.click("text=Link account")
            await page.wait_for_url(f"{PUBLIC_URL}/account?ok=linked", timeout=30_000)
            text = await page.inner_text("body")
            assert f"Linked to {archidekt_user}" in text, text
        c = clients[user]
        async with c.session() as s:
            status = await c.call(s, "account_status")
            assert status["linked"] is True and status["archidekt_username"] == archidekt_user, status
            decks = await c.call(s, "list_my_decks")
        assert decks["ok"], decks
        assert [d["id"] for d in decks["decks"]] == (["42"] if archidekt_user == "alice" else ["43"]), decks

    run(go())


def test_propose_changes_is_review_only_while_writes_are_off(clients):
    async def go():
        c = clients["alice-test"]
        async with c.session() as s:
            p = await c.call(s, "propose_deck_changes", {
                "deck_id": "42",
                "changes": [
                    {"action": "add", "card_name": "Arcane Signet", "quantity": 1, "category": "Ramp"},
                    {"action": "remove", "card_name": "Rampant Growth"},
                ],
            })  # fmt: skip
            assert p["ok"] and p["state"] == "pending" and p["kind"] == "edit", p
            assert p["review_url"] == f"{PUBLIC_URL}/proposals/{p['proposal_id']}"
            assert "Arcane Signet" in p["diff"] and "Rampant Growth" in p["diff"], p["diff"]
            assert p["writes_enabled"] is False and "disabled" in p["next_step"]
            STATE["edit"] = p["proposal_id"]
            refused = await c.call(s, "apply_proposal", {"proposal_id": p["proposal_id"]})
            assert refused["ok"] is False and refused["error"] == "writes_disabled", refused
            again = await c.call(s, "get_proposal", {"proposal_id": p["proposal_id"]})
            assert again["state"] == "pending", again
            mine = await c.call(s, "list_my_proposals")
            assert p["proposal_id"] in {x["id"] for x in mine["proposals"]}, mine
        assert mock("/__e2e/patches") == "[]", "nothing may reach Archidekt while writes are off"

    run(go())


def test_propose_new_deck_from_the_csv_export_is_review_only(clients):
    async def go():
        c = clients["alice-test"]
        async with c.session() as s:
            p = await c.call(
                s,
                "propose_new_deck",
                {"name": "E2E copy of Sample Commander Deck", "deck_format": "commander", "csv_text": CSV},
            )
            assert p["ok"] and p["kind"] == "create_deck" and p["state"] == "pending", p
            assert (
                p["diff"].splitlines()[0]
                == "New commander deck 'E2E copy of Sample Commander Deck' (100 cards, private)"
            ), p["diff"]
            refused = await c.call(s, "apply_proposal", {"proposal_id": p["proposal_id"]})
            assert refused["ok"] is False and refused["error"] == "writes_disabled", refused
            STATE["create"] = p["proposal_id"]
            bad = await c.call(
                s,
                "propose_new_deck",
                {"name": "x", "deck_format": "commander", "decklist_text": "1 Sol Ring", "csv_text": CSV},
            )
            assert bad["ok"] is False and bad["error"] == "invalid", bad
        assert json.loads(mock("/__e2e/health"))["decks"] == [42, 43], (
            "no deck may be created while writes are off"
        )

    run(go())


def test_users_cannot_see_or_touch_each_others_decks_and_proposals(clients, env: Env):
    async def go():
        f = clients["bob-test"]
        async with f.session() as s:
            assert (await f.call(s, "whoami"))["preferred_username"] == "bob-test"
            other = await f.call(s, "get_proposal", {"proposal_id": STATE["edit"]})
            assert other["ok"] is False and other["error"] == "not_found", other
            assert (await f.call(s, "apply_proposal", {"proposal_id": STATE["edit"]}))["error"] in (
                "writes_disabled",
                "not_found",
            )
            assert (await f.call(s, "list_my_proposals"))["proposals"] == []
            own = await f.call(s, "get_my_deck", {"deck_id": "42"})
            assert own["ok"] is False and own["error"] == "forbidden", own  # public, but not Amy's
            change = await f.call(
                s,
                "propose_deck_changes",
                {"deck_id": "42", "changes": [{"action": "add", "card_name": "Forest", "quantity": 1}]},
            )
            assert change["ok"] is False and change["error"] == "forbidden", change
            assert (await f.call(s, "get_deck", {"deck_ref": "42"}))["ok"]  # readable, as anyone can
            amy = await f.call(s, "get_my_deck", {"deck_id": "43"})
            assert amy["ok"] and amy["name"] == "Amy's deck", amy
        async with browser_page() as page:
            await gateway_browser_sign_in(page, env, "bob-test", f"/proposals/{STATE['edit']}")
            assert "No such proposal for your account" in await page.inner_text("body")

    run(go())


def test_writes_on_apply_is_browser_only_and_the_review_page_applies(clients, env: Env):
    set_switches(writes="true", mode="manual")
    pid = STATE["edit"]

    async def go():
        c = clients["alice-test"]
        async with c.session() as s:
            assert (await c.call(s, "account_status"))["writes_enabled"] is True
            over_mcp = await c.call(s, "apply_proposal", {"proposal_id": pid})
            assert over_mcp["ok"] is False and over_mcp["error"] == "browser_required", over_mcp
            assert (
                over_mcp["review_url"] == f"{PUBLIC_URL}/proposals/{pid}" and over_mcp["state"] == "pending"
            )
        assert mock("/__e2e/patches") == "[]"
        async with browser_page() as page:
            await gateway_browser_sign_in(page, env, "alice-test", f"/proposals/{pid}")
            text = await page.inner_text("body")
            assert "Arcane Signet" in text and "Rampant Growth" in text and "pending" in text.lower(), text
            # the form carries a CSRF token; a POST without one is refused (also covered by unit tests)
            await click_guarded(page, "form.guarded button[value=apply]")
            await page.wait_for_url(f"{PUBLIC_URL}/proposals/{pid}?ok=applied", timeout=60_000)
            text = await page.inner_text("body")
            assert (
                "applied" in text.lower() and "Done. The deck on Archidekt matches this proposal." in text
            ), text
        async with c.session() as s:
            p = await c.call(s, "get_proposal", {"proposal_id": pid})
            assert p["state"] == "applied" and p["result"]["verified"] is True and p["snapshot_id"], p
            deck = await c.call(s, "get_my_deck", {"deck_id": "42"})
            names = {card["name"] for card in deck["cards"]}
            assert "Arcane Signet" in names and "Rampant Growth" not in names, sorted(names)
            twice = await c.call(s, "apply_proposal", {"proposal_id": pid})
            assert twice["error"] in ("browser_required", "already_applied"), twice
        patches = mock("/__e2e/patches")
        assert patches.count('"action"') == 2 and '"add"' in patches and '"remove"' in patches, patches

    run(go())


def test_stale_deck_is_refused_on_the_review_page(clients, env: Env):
    async def go():
        c = clients["alice-test"]
        async with c.session() as s:
            p = await c.call(
                s,
                "propose_deck_changes",
                {
                    "deck_id": "42",
                    "changes": [{"action": "add", "card_name": "Rampant Growth", "quantity": 1}],
                },
            )
            assert p["ok"] and p["state"] == "pending", p
        mock("/__e2e/bump/42", method="POST")  # someone edits the deck on Archidekt meanwhile
        before = mock("/__e2e/patches")
        async with browser_page() as page:
            await gateway_browser_sign_in(page, env, "alice-test", f"/proposals/{p['proposal_id']}")
            await click_guarded(page, "form.guarded button[value=apply]")
            await page.wait_for_url(lambda u: "err=" in u, timeout=60_000)
            text = await page.inner_text("body")
            assert "changed on Archidekt since this proposal was made" in text, text
        async with c.session() as s:
            after = await c.call(s, "get_proposal", {"proposal_id": p["proposal_id"]})
            assert after["state"] == "failed" and after["result"]["error"] == "stale", after
        assert mock("/__e2e/patches") == before, "a stale proposal must send nothing"

    run(go())


def test_apply_over_mcp_only_in_auto_mode(clients):
    set_switches(writes="true", mode="auto")

    async def go():
        c = clients["alice-test"]
        async with c.session() as s:
            p = await c.call(
                s,
                "propose_deck_changes",
                {
                    "deck_id": "42",
                    "changes": [{"action": "add", "card_name": "Rampant Growth", "quantity": 1}],
                },
            )
            assert p["ok"], p
            # In auto mode the proposal says the assistant may apply it, and it may.
            assert p["approval_mode"] == "auto" and p["assistant_may_apply"] is True, p
            applied = await c.call(s, "apply_proposal", {"proposal_id": p["proposal_id"]})
            assert applied["ok"] and applied["state"] == "applied" and applied["result"]["verified"], applied
            create = await c.call(s, "apply_proposal", {"proposal_id": STATE["create"]})
            # the CSV deck has cards the mock's card database does not know: created, then reported
            assert create["ok"] is False and create["error"] in (
                "not_found",
                "verify_mismatch",
                "invalid",
                "contract",
            ), create

    run(go())
    set_switches(writes="false", mode="manual")

    # The audit log records both routes, and no Archidekt password or session token is in the database.
    c = container_of(GATEWAY)
    out = sh("docker", "exec", c, "mtg-gateway", "backup")
    assert "backup written" in out
    copy = GEN / "restored-decks.sqlite"
    copy.unlink(missing_ok=True)
    copy_backup(c, out, copy)
    db = sqlite3.connect(copy)
    applied = db.execute("SELECT detail_json FROM audit_log WHERE event='proposal_applied'").fetchall()
    assert any('"via": "browser"' in d for (d,) in applied) and any(
        '"via": "mcp"' in d for (d,) in applied
    ), applied
    events = {row[0] for row in db.execute("SELECT DISTINCT event FROM audit_log")}
    assert {"archidekt_linked", "proposal_created", "proposal_failed"} <= events, events
    db.close()
    raw = copy.read_bytes()
    assert b"pw-alice" not in raw and b"tok-alice" not in raw, (
        "Archidekt credentials must be stored encrypted only"
    )
    copy.unlink()
