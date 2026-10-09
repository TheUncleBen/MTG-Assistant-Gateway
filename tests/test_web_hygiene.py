"""Browser-page hygiene, scan limits, Mystic Forge proxy limits and the /mcp transport."""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path

import mcp_types as types
import pytest
from mcp import Client

from mtg_gateway.mf_proxy import MysticForgeProxy
from mtg_gateway.scan import routes as scan_routes
from mtg_gateway.scan.scryfall import ScryfallClient, summarize
from mtg_gateway.scan.service import MAX_LOOKUPS_PER_USER, MAX_SESSION_BYTES, ScanError, _clean_items

from .conftest import FakeIdP, Harness, make_settings, running
from .fake_scryfall import FakeScryfall
from .test_decks_and_proxy import Browser, call, fake_mystic_forge, mcp_token

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
async def h(tmp_path: Path, idp: FakeIdP):
    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(fake_mystic_forge()))
    async with running(Harness(make_settings(tmp_path), idp, mf_proxy=proxy)) as harness:
        service = harness.app.state.gateway.scan
        service.scryfall = ScryfallClient(
            "https://scryfall.test", min_interval=0.0, http=FakeScryfall().client()
        )
        yield harness


# -- logout (finding 5) --------------------------------------------------------
async def test_logout_without_form_token_clears_nothing(h: Harness) -> None:
    b = Browser(h)
    try:
        await b.login()
        # A cross-site auto-submitted form: no csrf field. Nothing is cleared, the session lives on.
        r = await b.http.post("/logout", data={})
        assert r.status_code == 303 and r.headers["location"] == "/logout"
        assert "set-cookie" not in r.headers and "clear-site-data" not in r.headers
        assert (await b.http.get("/account")).status_code == 200
        # The confirm page offers a real sign-out button.
        page = await b.http.get("/logout")
        assert page.status_code == 200 and "action='/logout'" in page.text
        r = await b.http.post("/logout", data={"csrf": await b.csrf("/logout")})
        assert r.status_code == 303 and r.headers["location"] == "/signed-out"
        # Storage only: pages are no-store, and clearing the HTTP cache made sign-out slow.
        assert r.headers["clear-site-data"] == '"storage"'
        assert (await b.http.get("/account")).status_code == 302
    finally:
        await b.aclose()
    # No session at all: no cookie or storage clearing either.
    r = await h.http.post("/logout", data={})
    assert r.status_code == 303 and r.headers["location"] == "/signed-out"
    assert "clear-site-data" not in r.headers and "set-cookie" not in r.headers
    r = await h.http.get("/logout")
    assert r.status_code == 303 and r.headers["location"] == "/signed-out"


# -- ?err= / ?ok= text (findings 6, 34) ------------------------------------------
async def test_err_query_shows_only_known_codes(h: Harness) -> None:
    b = Browser(h)
    try:
        await b.login()
        phish = "Re-enter your Archidekt password at https://evil.example"
        page = await b.http.get("/account", params={"err": phish})
        assert page.status_code == 200 and "evil.example" not in page.text
        assert "notice error" not in page.text
        page = await b.http.get("/account", params={"ok": phish})
        assert "evil.example" not in page.text and "notice ok" not in page.text
        page = await b.http.get("/account", params={"err": "stale"})
        assert "The deck changed on Archidekt" in page.text
    finally:
        await b.aclose()


# -- headers (finding 7) -----------------------------------------------------------
async def test_authenticated_json_and_pages_are_not_cached_or_sniffed(h: Harness) -> None:
    b = Browser(h)
    try:
        await b.login()
        r = await b.http.get("/scan/api/sessions")
        assert r.status_code == 200
        assert r.headers["cache-control"] == "no-store" and r.headers["x-content-type-options"] == "nosniff"
        page = await b.http.get("/account")
        assert page.headers["cache-control"] == "no-store"
        assert page.headers["x-content-type-options"] == "nosniff"
    finally:
        await b.aclose()
    assert (await h.http.get("/healthz")).headers["x-content-type-options"] == "nosniff"


# -- request bodies (L2) ---------------------------------------------------------
async def test_oversized_bodies_are_refused_without_reading_them_whole(h: Harness) -> None:
    b = Browser(h)
    try:
        await b.login()
        csrf = await b.csrf()
        r = await b.http.post("/account", data={"csrf": csrf, "action": "unlink", "pad": "x" * 20_000})
        assert r.status_code == 413

        sent = 0

        async def endless():
            nonlocal sent
            for _ in range(100):
                sent += 64_000
                yield b"x" * 64_000

        # Chunked, no Content-Length: reading stops once the limit is passed.
        r = await b.http.post(
            "/scan/api/sessions",
            content=endless(),
            headers={"Content-Type": "application/json", "X-CSRF-Token": csrf},
        )
        assert r.status_code == 413 and r.json()["error"] == "too_large"
        assert sent < scan_routes.MAX_JSON + 200_000
    finally:
        await b.aclose()


# -- scan sessions (L1) ----------------------------------------------------------
def test_scan_session_size_is_capped() -> None:
    card = summarize(json.loads((FIXTURES / "live" / "scryfall_named_sol_ring.json").read_text()))
    # A realistic full session (500 cards) still fits.
    full = [{"quantity": 1, "name": card["name"], "status": "exact", "card": card}] * 500
    assert len(_clean_items(full)) == 500
    # Long strings inside list fields are cut; stuffed items push the session over the cap.
    stuffed = {**card, "finishes": ["x" * 5000] * 10, "color_identity": ["y" * 5000] * 10}
    one = _clean_items([{"quantity": 1, "card": stuffed}])[0]["card"]
    assert all(len(x) <= 40 for x in one["finishes"] + one["color_identity"])
    fat = {**card, **{k: "https://scryfall.com/" + "z" * 500 for k in ("scryfall_uri",)}}
    fat.update({k: "w" * 500 for k in ("type_line", "set_name", "mana_cost", "layout", "rarity")})
    items = [{"quantity": 1, "name": "n" * 500, "note": "m" * 500, "card": fat}] * 500
    with pytest.raises(ScanError) as err:
        _clean_items(items)
    assert "too large" in str(err.value) and MAX_SESSION_BYTES < 2_000_000


# -- scan lookups per user (M4) ----------------------------------------------------
async def test_scan_lookups_are_limited_per_user(h: Harness) -> None:
    service = h.app.state.gateway.scan
    gate = asyncio.Event()
    started = 0

    async def slow_autocomplete(q: str) -> list[str]:
        nonlocal started
        started += 1
        await gate.wait()
        return ["Sol Ring"]

    service.scryfall.autocomplete = slow_autocomplete  # type: ignore[method-assign]
    b = Browser(h)
    try:
        await b.login()
        n = MAX_LOOKUPS_PER_USER
        first = [asyncio.create_task(b.http.get("/scan/api/search", params={"q": "sol"})) for _ in range(n)]
        while started < n:
            await asyncio.sleep(0.01)
        busy = await asyncio.wait_for(b.http.get("/scan/api/search", params={"q": "sol"}), 5)
        assert busy.status_code == 429 and busy.json()["error"] == "busy"
        # Another member is not held back by this one's lookups.
        other = asyncio.create_task(service.suggest("sol", owner="someone-else"))
        gate.set()
        assert [(await t).status_code for t in first] == [200] * n
        assert await other == ["Sol Ring"]
        assert (await b.http.get("/scan/api/search", params={"q": "sol"})).status_code == 200
    finally:
        gate.set()
        await b.aclose()


# -- Scryfall cache (finding 50) ---------------------------------------------------
async def test_scryfall_cache_keeps_slim_cards() -> None:
    sf = ScryfallClient("https://scryfall.test", min_interval=0.0, http=FakeScryfall().client())
    raw = json.loads((FIXTURES / "live" / "scryfall_named_sol_ring.json").read_text())
    card = await sf.named("Sol Ring")
    assert summarize(card) == summarize(raw)
    cached = next(iter(sf.cards._items.values()))[1]
    assert "oracle_text" not in cached and "prices" not in cached
    assert len(json.dumps(cached)) < 1500 < len(json.dumps(raw))


# -- asset version (finding 42) ----------------------------------------------------
def test_asset_version_covers_every_static_file(tmp_path: Path) -> None:
    static = tmp_path / "static"
    shutil.copytree(scan_routes.STATIC_DIR, static, ignore=shutil.ignore_patterns("vendor"))
    before = scan_routes._asset_version(static)
    (static / "scan-art.js").write_text((static / "scan-art.js").read_text() + "\n// fix\n")
    after_art = scan_routes._asset_version(static)
    assert after_art != before
    (static / "vendor").mkdir()
    (static / "vendor" / "worker.min.js").write_text("v1")
    v1 = scan_routes._asset_version(static)
    (static / "vendor" / "worker.min.js").write_text("v2")
    assert scan_routes._asset_version(static) not in (v1, after_art)


async def test_unversioned_static_files_are_not_immutable(
    h: Harness, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    static = tmp_path / "static"
    shutil.copytree(scan_routes.STATIC_DIR, static, ignore=shutil.ignore_patterns("vendor"))
    (static / "vendor").mkdir()
    (static / "vendor" / "worker.min.js").write_text("// engine")
    monkeypatch.setattr(scan_routes, "STATIC_DIR", static)
    # vendor/ files are fetched by bare path, so they must not be pinned for a year.
    r = await h.http.get("/scan/static/vendor/worker.min.js")
    assert r.status_code == 200 and "immutable" not in r.headers["cache-control"]
    r = await h.http.get("/scan/static/scan-art.js")
    assert r.status_code == 200 and "immutable" not in r.headers["cache-control"]
    r = await h.http.get("/scan/static/scan-art.js", params={"v": "abc"})
    assert "immutable" in r.headers["cache-control"]


# -- Mystic Forge proxy (finding 39) ----------------------------------------------
class _SlowClient:
    def __init__(self, gate: asyncio.Event | None = None):
        self.gate = gate

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def call_tool(self, name, args):
        if self.gate is None:
            await asyncio.sleep(3600)
        else:
            await self.gate.wait()
        return types.CallToolResult(content=[types.TextContent(type="text", text="ok")])


async def test_proxy_limits_calls_per_user_and_has_a_deadline() -> None:
    gate = asyncio.Event()
    proxy = MysticForgeProxy("http://x/mcp", client_factory=lambda: _SlowClient(gate))
    running_calls = [asyncio.create_task(proxy.call("scryfall_named", {}, owner="a")) for _ in range(2)]
    await asyncio.sleep(0.01)
    busy = await proxy.call("scryfall_named", {}, owner="a")
    assert busy.is_error and "busy" in busy.content[0].text  # type: ignore[union-attr]
    other = asyncio.create_task(proxy.call("scryfall_named", {}, owner="b"))  # another member is fine
    gate.set()
    outs = await asyncio.gather(*running_calls, other)
    assert all(not o.is_error for o in outs)
    assert proxy._in_flight == {}

    stuck = MysticForgeProxy("http://x/mcp", client_factory=lambda: _SlowClient(), deadline=0.05)
    out = await asyncio.wait_for(stuck.call("goldfish_odds", {"params": {"deck_size": 99}}, owner="a"), 5)
    assert out.is_error and "longer than" in out.content[0].text  # type: ignore[union-attr]
    assert stuck._in_flight == {}


async def test_proxy_middleware_counts_calls_by_token_subject(h: Harness) -> None:
    token = await mcp_token(h)
    proxy = h.app.state.gateway.mf_proxy
    assert not (await call(h, token, "scryfall_named", {"name": "Sol Ring"})).get("isError")
    proxy.max_calls_per_user = 0  # every call by a signed-in subject is now over the limit
    out = await call(h, token, "scryfall_named", {"name": "Sol Ring"})
    assert out.get("isError") is True and "busy" in str(out)


# -- GET /mcp (finding 51) ---------------------------------------------------------
async def test_get_mcp_is_refused_after_auth(h: Harness) -> None:
    assert (await h.http.get("/mcp")).status_code == 401
    token = await mcp_token(h)
    r = await asyncio.wait_for(
        h.http.get(
            "/mcp",
            headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream", "Host": "mtg.test"},
        ),
        5,
    )
    assert r.status_code == 405
    # POST is unaffected.
    assert (await h.mcp(token, "tools/list")).status_code == 200


# -- Connected apps (finding 37) ---------------------------------------------------
async def test_member_can_see_and_disconnect_connected_apps(h: Harness) -> None:
    first = await h.register(client_name="Claude")
    second = await h.register(client_name="ChatGPT")
    t1 = (await h.tokens_for(first))["access_token"]
    t2 = (await h.tokens_for(second))["access_token"]
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get("/account")).text
        assert "Connected apps" in page and "Claude" in page and "ChatGPT" in page
        csrf = await b.csrf()
        r = await b.http.post(
            "/account", data={"csrf": csrf, "action": "disconnect", "client_id": first["client_id"]}
        )
        assert r.status_code == 303 and r.headers["location"] == "/account?ok=disconnected"
        assert (await h.mcp(t1, "tools/list")).status_code == 401
        assert (await h.mcp(t2, "tools/list")).status_code == 200
        # Without the form token nothing happens.
        r = await b.http.post("/account", data={"action": "disconnect_all"})
        assert r.status_code == 403 and (await h.mcp(t2, "tools/list")).status_code == 200
        r = await b.http.post("/account", data={"csrf": csrf, "action": "disconnect_all"})
        assert r.status_code == 303 and (await h.mcp(t2, "tools/list")).status_code == 401
        assert (await b.http.get("/account")).status_code == 200  # the browser session stays
    finally:
        await b.aclose()
