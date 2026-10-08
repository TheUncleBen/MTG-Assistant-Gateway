"""Card scanning end to end: resolve_cards, scan sessions, two-user isolation, the /scan page's CSP.

Scryfall is the local mock from stacks/edge-stack.yml, reached by the gateway at the real
hostname https://api.scryfall.com (network alias plus a test-CA certificate), so no request
leaves the sandbox. The mock answers the collection endpoint in reverse order on purpose.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from .conftest import MCP_URL, PUBLIC_URL, Env, McpClient, browser_page, gateway_browser_sign_in
from .test_04_operations import GATEWAY, container_of, sh

# Every other page may load only the gateway's own scripts (feedback.js, the offline service worker)
# and its own images (the account picture, /account/avatar).
STRICT_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; worker-src 'self'; img-src 'self'; "
    "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
)
HOME_CSP = (
    "default-src 'none'; style-src 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
    "img-src 'self' https://cards.scryfall.io; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
)
STATE: dict[str, str] = {}


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(scope="module")
def clients(env: Env) -> dict[str, McpClient]:
    return {u: McpClient(env, u, client_name=f"scan e2e {u}") for u in ("alice-test", "bob-test")}


def scryfall_requests() -> list[list[str]]:
    """What the mock saw, read from inside the overlay network (it publishes no port)."""
    code = (
        "import sys,urllib.request;"
        "sys.stdout.write(urllib.request.urlopen('https://api.scryfall.com/__e2e/requests', timeout=10)"
        ".read().decode())"
    )
    return json.loads(sh("docker", "exec", container_of(GATEWAY), "python", "-c", code))


def test_resolve_cards_matches_by_identifier_not_position(clients):
    before = len(scryfall_requests())

    async def go():
        c = clients["alice-test"]
        async with c.session() as s:
            names = {t.name for t in (await s.list_tools()).tools}
            assert {"resolve_cards", "save_scan_session", "list_scan_sessions", "get_scan_session"} <= names
            text = "2 Sol Ring\n1 Aesi, Tyrant of Gyre Strait (CMR) 365\n1 Nonexistent Card XYZ\n1 Sol Rng\n"
            out = await c.call(s, "resolve_cards", {"text": text})
        assert out["ok"], out
        cards = out["cards"]
        # The mock returned the batch in reverse order; each result must still carry its own card.
        assert cards[0]["status"] == "exact" and cards[0]["card"]["name"] == "Sol Ring", cards[0]
        assert cards[0]["quantity"] == 2
        assert cards[1]["status"] == "printing", cards[1]
        assert (cards[1]["card"]["set"], cards[1]["card"]["collector_number"]) == ("cmr", "365"), cards[1]
        assert cards[1]["card"]["name"] == "Aesi, Tyrant of Gyre Strait"
        assert cards[2]["status"] == "not_found", cards[2]
        assert cards[3]["status"] != "exact", cards[3]  # OCR noise is never silently accepted
        assert {2, 3} <= set(out["needs_review"]), out["needs_review"]
        assert out["status_counts"]["exact"] == 1 and out["status_counts"]["printing"] == 1
        assert {"action": "add", "name": "Sol Ring", "quantity": 2} in [
            {k: ch[k] for k in ("action", "name", "quantity")} for ch in out["changes"]
        ], out["changes"]
        assert "2 Sol Ring" in out["decklist_text"]

    run(go())
    seen = scryfall_requests()[before:]
    collection = [r for r in seen if r[0] == "POST" and "/cards/collection" in r[1]]
    assert len(collection) == 1, seen  # one batched lookup for the whole list, as the live check saw
    assert all(r[1].startswith("https://api.scryfall.com/") for r in seen), seen


def test_scan_sessions_are_saved_listed_and_fetched_by_id_or_name(clients):
    async def go():
        c = clients["alice-test"]
        async with c.session() as s:
            saved = await c.call(
                s,
                "save_scan_session",
                {"name": "E2E scan", "text": "2 Sol Ring\n1 Aesi, Tyrant of Gyre Strait (CMR) 365"},
            )
            assert saved["ok"] and saved["id"].startswith("scan_") and saved["name"] == "E2E scan", saved
            STATE["session"] = saved["id"]
            listed = await c.call(s, "list_scan_sessions")
            assert saved["id"] in {x["id"] for x in listed["sessions"]}, listed
            by_id = await c.call(s, "get_scan_session", {"session": saved["id"]})
            by_name = await c.call(s, "get_scan_session", {"session": "e2e scan"})
        for got in (by_id, by_name):
            assert got["ok"] and got["id"] == saved["id"], got
            assert (
                "2 Sol Ring" in got["decklist_text"] and "Aesi, Tyrant of Gyre Strait" in got["decklist_text"]
            )
            assert {ch["name"] for ch in got["changes"]} == {"Sol Ring", "Aesi, Tyrant of Gyre Strait"}

    run(go())


def test_another_user_cannot_read_the_sessions(clients, env: Env):
    async def go():
        f = clients["bob-test"]
        async with f.session() as s:
            assert (await f.call(s, "whoami"))["preferred_username"] == "bob-test"
            assert (await f.call(s, "list_scan_sessions"))["sessions"] == []
            by_id = await f.call(s, "get_scan_session", {"session": STATE["session"]})
            by_name = await f.call(s, "get_scan_session", {"session": "E2E scan"})
        assert by_id["ok"] is False and by_id["error"] == "not_found", by_id
        assert by_name["ok"] is False and by_name["error"] == "not_found", by_name
        # the /scan page's own API, with bob's browser session
        async with browser_page() as page:
            await gateway_browser_sign_in(page, env, "bob-test", "/scan")
            listing = await page.request.get(f"{PUBLIC_URL}/scan/api/sessions")
            assert listing.status == 200
            assert STATE["session"] not in await listing.text()
            one = await page.request.get(f"{PUBLIC_URL}/scan/api/sessions/{STATE['session']}")
            assert one.status == 404, await one.text()

    run(go())


def test_scan_page_has_its_own_csp_and_the_rest_keep_the_strict_one(clients, env: Env, http: httpx.Client):
    anonymous = http.get("/scan")
    assert anonymous.status_code == 302 and anonymous.headers["location"].startswith("/login?next="), (
        anonymous.headers
    )
    assert http.get("/scan/api/sessions").status_code in (401, 403)

    async def go():
        async with browser_page() as page:
            await gateway_browser_sign_in(page, env, "alice-test", "/scan")
            scan = await page.goto(f"{PUBLIC_URL}/scan")
            csp = await scan.header_value("content-security-policy")
            assert scan.status == 200 and csp, csp
            assert "'wasm-unsafe-eval'" in csp and "worker-src 'self'" in csp, csp
            assert "img-src 'self' blob: data: https://cards.scryfall.io" in csp, csp
            assert "frame-ancestors 'none'" in csp and "base-uri 'none'" in csp, csp
            assert await scan.header_value("permissions-policy") == "camera=(self)"
            assert "Scan cards" in await page.inner_text("body")
            account = await page.goto(f"{PUBLIC_URL}/account")
            assert await account.header_value("content-security-policy") == STRICT_CSP
            proposals = await page.goto(f"{PUBLIC_URL}/proposals")
            assert await proposals.header_value("content-security-policy") == STRICT_CSP
            landing = await page.goto(f"{PUBLIC_URL}/")
            assert landing.status == 200 and MCP_URL in await page.inner_text("body")
            # The home page shows deck covers from Scryfall, so it carries the deck pages' CSP: the
            # strict one plus that single image host and connect-src 'self'.
            assert await landing.header_value("content-security-policy") == HOME_CSP
            # the scanner's own search goes through the gateway (and so to the mock), never to Scryfall
            search = await page.request.get(f"{PUBLIC_URL}/scan/api/search?q=sol%20r")
            assert search.status == 200 and "Sol Ring" in await search.text(), await search.text()

    run(go())
