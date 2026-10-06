"""Card scanning: resolver, MCP tools, browser API, static assets and isolation."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx
import pytest

from mtg_gateway.scan import service as scan_service
from mtg_gateway.scan.scryfall import ScryfallClient
from mtg_gateway.scan.service import parse_text

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from .fake_scryfall import FakeScryfall
from .test_decks_and_proxy import Browser, call, mcp_token

VENDOR = Path(__file__).resolve().parents[1] / "src" / "mtg_gateway" / "scan" / "static" / "vendor"


class Stack:
    def __init__(self, h: Harness, sf: FakeScryfall):
        self.h = h
        self.sf = sf


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP):
    sf = FakeScryfall()
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        service = h.app.state.gateway.scan
        service.scryfall = ScryfallClient("https://scryfall.test", min_interval=0.0, http=sf.client())
        yield Stack(h, sf)


async def api(b: Browser, method: str, path: str, body: dict | None = None, *, csrf: str | None = None):
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["X-CSRF-Token"] = csrf if csrf is not None else await scan_csrf(b)
    return await b.http.request(
        method, path, content=json.dumps(body) if body is not None else None, headers=headers
    )


async def scan_csrf(b: Browser) -> str:
    r = await b.http.get("/scan")
    assert r.status_code == 200, r.text
    start = r.text.index("<script id='scan-config'")
    blob = r.text[r.text.index(">", start) + 1 : r.text.index("</script>", start)]
    return json.loads(blob)["csrf"]


# -- parsing ------------------------------------------------------------------
def test_parse_text_handles_common_decklist_shapes() -> None:
    cards = parse_text(
        "// Commander\n2 Sol Ring (CMR) 472 *F*\n1x Aesi, Tyrant of Gyre Strait\nFire // Ice [APC]\n\n"
        "Sideboard:\n4 Lightning Bolt\n"
    )
    assert [(c.quantity, c.name, c.set_code, c.collector_number) for c in cards] == [
        (2, "Sol Ring", "cmr", "472"),
        (1, "Aesi, Tyrant of Gyre Strait", "", ""),
        (1, "Fire // Ice", "apc", ""),
        (4, "Lightning Bolt", "", ""),
    ]


# -- resolver through the MCP tool --------------------------------------------
async def test_resolve_cards_statuses(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    out = await call(
        h,
        token,
        "resolve_cards",
        {
            "cards": [
                {"name": "Sol Ring", "quantity": 2},
                {"set": "cmr", "collector_number": "365"},
                {"name": "Aesi, Tyrant of Gyre Stralt"},  # OCR typo, fuzzy fixes it
                {"name": "aesi"},  # Scryfall: ambiguous
                {"name": "Sol Rng"},  # Scryfall's fuzzy says Oathsworn Giant; we refuse it
                {"name": "Nonexistent Card XYZ"},
                "1 Fire Ice",
            ]
        },
    )
    assert out.get("isError") is not True, out
    body = out["structuredContent"]
    assert body["ok"] is True
    statuses = [c["status"] for c in body["cards"]]
    assert statuses == ["exact", "printing", "fuzzy", "ambiguous", "ambiguous", "not_found", "exact"]
    assert body["cards"][0]["card"]["name"] == "Sol Ring" and body["cards"][0]["quantity"] == 2
    assert body["cards"][1]["card"]["set"] == "cmr" and body["cards"][1]["card"]["collector_number"] == "365"
    assert body["cards"][2]["card"]["name"] == "Aesi, Tyrant of Gyre Strait"
    assert "Aesi, Tyrant of Gyre Strait" in body["cards"][3]["suggestions"]
    assert body["cards"][4]["card"] is None and "Oathsworn Giant" in body["cards"][4]["suggestions"]
    assert body["cards"][5]["card"] is None
    assert body["cards"][6]["card"]["name"] == "Fire // Ice"
    assert body["needs_review"] == [2, 3, 4, 5]
    assert body["card_count"] == 2 + 1 + 1 + 1 and body["distinct"] == 4
    assert body["decklist_text"].splitlines()[0].startswith("2 Sol Ring (")
    assert {"action": "add", "card_name": "Sol Ring", "quantity": 2} in body["changes"]
    # One batched collection call for the exact pass, then per-card fallbacks only for the misses.
    posts = [u for m, u in stack.sf.requests if m == "POST"]
    assert len(posts) == 1


async def test_resolve_requires_input_and_handles_scryfall_outage(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    out = (await call(h, token, "resolve_cards", {}))["structuredContent"]
    assert out == {
        "ok": False,
        "error": "invalid",
        "message": "give `cards` or `text` with at least one card",
    }
    stack.sf.fail_with = 503
    out = (await call(h, token, "resolve_cards", {"text": "Sol Ring"}))["structuredContent"]
    assert out["ok"] is False and out["error"] == "unavailable"


async def test_scryfall_client_caches_and_paces() -> None:
    sf = FakeScryfall()
    client = ScryfallClient("https://scryfall.test", min_interval=0.01, http=sf.client())
    a = await client.named("Sol Ring")
    b = await client.named("sol ring")
    assert a is b and len(sf.requests) == 1
    names = await client.autocomplete("aesi")
    assert names[0] == "Aesi, Tyrant of Gyre Strait"
    assert await client.autocomplete("aesi") == names and len(sf.requests) == 2
    await client.aclose()


# -- sessions via MCP and browser ----------------------------------------------
async def test_save_list_get_scan_session_tools(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    saved = (
        await call(
            h,
            token,
            "save_scan_session",
            {"name": "Binder page 1", "text": "2 Sol Ring\n1 Aesi, Tyrant of Gyre Strait"},
        )
    )["structuredContent"]
    assert saved["ok"] and saved["id"].startswith("scan_") and saved["card_count"] == 3
    listed = (await call(h, token, "list_scan_sessions"))["structuredContent"]
    assert [s["name"] for s in listed["sessions"]] == ["Binder page 1"]
    got = (await call(h, token, "get_scan_session", {"session": "binder page 1"}))["structuredContent"]
    assert got["id"] == saved["id"]
    assert got["changes"] == [
        {"action": "add", "card_name": "Sol Ring", "quantity": 2},
        {"action": "add", "card_name": "Aesi, Tyrant of Gyre Strait", "quantity": 1},
    ]
    missing = (await call(h, token, "get_scan_session", {"session": "scan_nope"}))["structuredContent"]
    assert missing["ok"] is False and missing["error"] == "not_found"


async def test_scan_page_requires_login_and_sets_scan_csp(stack: Stack) -> None:
    h = stack.h
    anon = Browser(h)
    r = await anon.http.get("/scan")
    assert r.status_code == 302 and r.headers["location"] == "/login?next=/scan"
    r = await anon.http.get("/scan/api/sessions")
    assert r.status_code == 401
    b = Browser(h)
    await b.login("/scan")
    r = await b.http.get("/scan")
    assert r.status_code == 200
    csp = r.headers["content-security-policy"]
    assert "script-src 'self' 'wasm-unsafe-eval'" in csp and "connect-src 'self'" in csp
    assert "cdn." not in r.text and "https://cards.scryfall.io" in csp
    assert "/scan/static/scan.js?v=" in r.text and "<a href='/scan'>Scan</a>" in r.text
    assert r.headers["permissions-policy"] == "camera=(self)"
    for path, ctype in [
        ("/scan/app.webmanifest", "application/manifest+json"),
        ("/scan/sw.js", "text/javascript"),
        ("/scan/static/scan.js", "text/javascript"),
        ("/scan/static/scan.css", "text/css"),
        ("/scan/static/icon-192.png", "image/png"),
    ]:
        r = await anon.http.get(path)
        assert r.status_code == 200, path
        assert r.headers["content-type"].startswith(ctype), (path, r.headers["content-type"])
    assert (await anon.http.get("/scan/sw.js")).headers["service-worker-allowed"] == "/scan"
    manifest = (await anon.http.get("/scan/app.webmanifest")).json()
    assert manifest["start_url"] == "/scan" and manifest["display"] == "standalone"
    for bad in ("/scan/static/../app.py", "/scan/static/%2e%2e/theme.py", "/scan/static/nope.js"):
        assert (await anon.http.get(bad)).status_code == 404, bad


async def test_browser_api_resolve_and_sessions_with_csrf(stack: Stack) -> None:
    h = stack.h
    b = Browser(h)
    await b.login("/scan")
    r = await api(b, "POST", "/scan/api/resolve", {"cards": [{"name": "Sol Ring"}]}, csrf="wrong")
    assert r.status_code == 403
    r = await b.http.post(
        "/scan/api/resolve", data={"cards": "x"}, headers={"X-CSRF-Token": await scan_csrf(b)}
    )
    assert r.status_code == 415
    r = await api(b, "POST", "/scan/api/resolve", {"cards": [{"name": "Sol Ring"}]})
    assert r.status_code == 200 and r.json()["cards"][0]["card"]["name"] == "Sol Ring"
    r = await b.http.get("/scan/api/search", params={"q": "aesi"})
    assert r.status_code == 200 and r.json()["names"][0].startswith("Aesi")
    items = [
        {"quantity": 2, "name": "Sol Ring", "status": "exact", "card": r2["card"]}
        for r2 in (await api(b, "POST", "/scan/api/resolve", {"cards": [{"name": "Sol Ring"}]})).json()[
            "cards"
        ]
    ]
    items.append({"quantity": 1, "name": "smudged", "status": "not_found", "card": None})
    r = await api(b, "POST", "/scan/api/sessions", {"name": "  Trade  binder ", "items": items})
    assert r.status_code == 201, r.text
    s = r.json()
    assert s["name"] == "Trade binder" and s["card_count"] == 3 and s["unresolved"] == 1
    assert s["items"][0]["card"]["image_small"].startswith("https://cards.scryfall.io/")
    sid = s["id"]
    r = await api(b, "PUT", f"/scan/api/sessions/{sid}", {"items": items[:1], "status": "done"})
    assert r.status_code == 200 and r.json()["status"] == "done" and r.json()["unresolved"] == 0
    r = await b.http.get("/scan/api/sessions")
    assert [x["id"] for x in r.json()["sessions"]] == [sid]
    bad = await api(b, "POST", "/scan/api/sessions", {"items": [{"quantity": 0}]})
    assert bad.status_code == 400
    # Hostile item content is dropped or rejected, never stored.
    r = await api(
        b,
        "POST",
        "/scan/api/sessions",
        {
            "items": [
                {"quantity": 1, "card": {"name": "X", "image_small": "https://evil.test/x.png", "junk": 1}}
            ]
        },
    )
    assert r.status_code == 201
    assert "image_small" not in r.json()["items"][0]["card"] and "junk" not in r.json()["items"][0]["card"]
    r = await api(b, "DELETE", f"/scan/api/sessions/{sid}", {})
    assert r.status_code == 200
    assert (await b.http.get(f"/scan/api/sessions/{sid}")).status_code == 404


async def test_scan_sessions_are_isolated_between_users(stack: Stack) -> None:
    h = stack.h
    alice = Browser(h)
    await alice.login("/scan")
    r = await api(alice, "POST", "/scan/api/sessions", {"name": "Alice's", "items": []})
    sid = r.json()["id"]
    h.idp.user = {**h.idp.user, "sub": "user-2", "preferred_username": "bob", "email": "bob@example.test"}
    bob = Browser(h)
    await bob.login("/scan")
    assert (await bob.http.get("/scan/api/sessions")).json()["sessions"] == []
    assert (await bob.http.get(f"/scan/api/sessions/{sid}")).status_code == 404
    assert (await api(bob, "PUT", f"/scan/api/sessions/{sid}", {"name": "mine now"})).status_code == 404
    assert (await api(bob, "DELETE", f"/scan/api/sessions/{sid}", {})).status_code == 404
    token = await mcp_token(h)  # bob is now the IdP user
    got = (await call(h, token, "get_scan_session", {"session": sid}))["structuredContent"]
    assert got["ok"] is False
    assert (await alice.http.get(f"/scan/api/sessions/{sid}")).status_code == 200


@pytest.mark.skipif(not (VENDOR / "worker.min.js").exists(), reason="run scripts/fetch_ocr_assets.py first")
async def test_vendored_ocr_assets_are_served_with_gzip_and_csp(stack: Stack) -> None:
    anon = Browser(stack.h)
    r = await anon.http.get("/scan/static/vendor/worker.min.js", headers={"Accept-Encoding": "gzip"})
    # Bare vendor paths can't carry ?v=, so they get a day's cache and never "immutable".
    assert r.status_code == 200 and r.headers["cache-control"] == "max-age=86400"
    assert "script-src 'self' 'wasm-unsafe-eval'" in r.headers["content-security-policy"]
    assert r.headers.get("content-encoding") == "gzip"
    assert b"importScripts" in r.content or b"postMessage" in r.content  # httpx decoded it
    r = await anon.http.get(
        "/scan/static/vendor/lang/eng.traineddata", headers={"Accept-Encoding": "identity"}
    )
    assert r.status_code == 200 and "content-encoding" not in r.headers
    assert r.headers["content-type"] == "application/octet-stream"
    assert len(r.content) == 4_113_088


async def test_healthz_unaffected(stack: Stack) -> None:
    r = await httpx.AsyncClient(transport=httpx.ASGITransport(app=stack.h.app), base_url=GATEWAY).get(
        "/healthz"
    )
    assert r.status_code == 200


# -- security review fixes ----------------------------------------------------
def test_parse_text_rejects_long_lines_fast() -> None:
    import time

    from mtg_gateway.scan.service import MAX_LINE, ScanError, parse_cards

    evil = "a" + " " * 32_000 + "b"
    t0 = time.perf_counter()
    assert parse_text(evil + "\n1 Sol Ring\n" + "1 x" + " " * 400 + "y") == parse_text("1 Sol Ring")
    assert time.perf_counter() - t0 < 0.5
    # A line at the limit with inner whitespace still parses quickly.
    t0 = time.perf_counter()
    assert parse_text("1 a" + " " * (MAX_LINE - 10) + "b")[0].name.startswith("a")
    assert time.perf_counter() - t0 < 0.5
    with pytest.raises(ScanError):
        parse_cards([evil])
    with pytest.raises(ScanError):
        parse_text("1 Sol Ring\n" * 2001)


async def test_collection_result_must_match_its_identifier(stack: Stack) -> None:
    """A misaligned batch reply must not label inputs with the wrong card."""
    sf = stack.sf
    real = sf.handle

    def shifted(request: httpx.Request) -> httpx.Response:
        resp = real(request)
        if request.url.path == "/cards/collection":
            data = resp.json()
            data["not_found"] = []  # pretend nothing was missing, so positions shift
            return httpx.Response(200, json=data)
        return resp

    service = stack.h.app.state.gateway.scan
    service.scryfall = ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(shifted), base_url="https://scryfall.test"),
    )
    token = await mcp_token(stack.h)
    out = (
        await call(
            stack.h,
            token,
            "resolve_cards",
            {
                "cards": [
                    {"name": "Nonexistent Card XYZ"},
                    {"name": "Sol Ring"},
                    {"set": "cmr", "collector_number": "365"},
                ]
            },
        )
    )["structuredContent"]
    assert [c["status"] for c in out["cards"]] == ["not_found", "exact", "printing"]
    assert out["cards"][1]["card"]["name"] == "Sol Ring"
    assert out["cards"][2]["card"]["collector_number"] == "365"


async def test_scryfall_client_honours_retry_after() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(429, headers={"Retry-After": "30"}, json={"object": "error", "details": "slow"})

    client = ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://scryfall.test"),
    )
    from mtg_gateway.scan.scryfall import ScryfallError

    with pytest.raises(ScryfallError) as e1:
        await client.named("Sol Ring")
    with pytest.raises(ScryfallError) as e2:
        await client.autocomplete("sol")
    assert e1.value.kind == e2.value.kind == "rate_limited"
    assert calls == ["/cards/named"]  # the second call never went out during the back-off
    await client.aclose()


async def test_scan_config_carries_per_user_draft_key(stack: Stack) -> None:
    h = stack.h
    alice = Browser(h)
    await alice.login("/scan")
    r = await alice.http.get("/scan")
    start = r.text.index("<script id='scan-config'")
    cfg = json.loads(r.text[r.text.index(">", start) + 1 : r.text.index("</script>", start)])
    assert len(cfg["draft"]) == 16 and cfg["draft"] != cfg["csrf"][:16]
    assert "/scan/sw.js?v=" in r.text or "sw.js?v=" in (await alice.http.get("/scan/static/scan.js")).text
    h.idp.user = {**h.idp.user, "sub": "user-2"}
    bob = Browser(h)
    await bob.login("/scan")
    r2 = await bob.http.get("/scan")
    start = r2.text.index("<script id='scan-config'")
    cfg2 = json.loads(r2.text[r2.text.index(">", start) + 1 : r2.text.index("</script>", start)])
    assert cfg2["draft"] != cfg["draft"]


# -- recovery of names Scryfall cannot place ------------------------------------
async def test_ocr_confusions_are_corrected_without_wrong_matches(stack: Stack) -> None:
    """Fuzzy 404 and empty autocomplete used to end in not_found; now common scan errors are retried."""
    token = await mcp_token(stack.h)
    out = (
        await call(
            stack.h,
            token,
            "resolve_cards",
            {
                "cards": [
                    {"name": "S0l Ring"},  # digit zero for o
                    {"name": "Sol Rlng"},  # l for i
                    {"name": "Cultlvate"},  # l for i
                    {"name": "Cultivatte"},  # no single swap helps; prefix "Cultiva" does
                    {"name": "Xqzv Plorth"},  # garbage stays unresolved
                ]
            },
        )
    )["structuredContent"]
    cards = out["cards"]
    assert [c["status"] for c in cards] == ["fuzzy", "fuzzy", "fuzzy", "fuzzy", "not_found"]
    assert [c["card"]["name"] for c in cards[:4]] == ["Sol Ring", "Sol Ring", "Cultivate", "Cultivate"]
    assert "correcting likely scan errors" in cards[0]["note"]
    assert "first letters" in cards[3]["note"]
    assert cards[4]["card"] is None and cards[4]["suggestions"] == []
    assert out["needs_review"] == [0, 1, 2, 3, 4]
    # The recovery budget is bounded: one fuzzy, one autocomplete, then at most 4 + 5 extra lookups.
    garbage = [u for _, u in stack.sf.requests if "Xqzv" in u or "Plorth" in u]
    assert len(garbage) <= 11


async def test_prefix_recovery_reports_ties_as_ambiguous(stack: Stack) -> None:
    # "Cultivatz" has no autocomplete hits and no single-swap fix; the prefix "Cultivat" then
    # offers two equally close names, so nothing is picked.
    stack.sf.autocomplete["cultivat"] = {
        "object": "catalog",
        "total_values": 2,
        "data": ["Cultivate", "Cultivatx"],
    }
    token = await mcp_token(stack.h)
    out = await call(stack.h, token, "resolve_cards", {"cards": [{"name": "Cultivatz"}]})
    card = out["structuredContent"]["cards"][0]
    assert card["status"] == "ambiguous" and card["card"] is None
    assert card["suggestions"] == ["Cultivate", "Cultivatx"]


async def test_recovery_lookups_are_capped_per_call(stack: Stack) -> None:
    """A call full of garbage names must not fan out into hundreds of Scryfall requests."""
    from mtg_gateway.scan.service import MAX_RECOVERY_LOOKUPS_PER_CALL

    names = [{"name": f"Zxqv Blorth {i}"} for i in range(30)]
    token = await mcp_token(stack.h)
    out = (await call(stack.h, token, "resolve_cards", {"cards": names}))["structuredContent"]
    assert all(c["status"] == "not_found" for c in out["cards"])
    gets = [u for m, u in stack.sf.requests if m == "GET"]
    # Per name: one fuzzy lookup and one autocomplete, as before. Recovery adds at most the cap.
    assert len(gets) <= 2 * len(names) + MAX_RECOVERY_LOOKUPS_PER_CALL
    assert len(gets) > 2 * len(names)  # recovery did run for the first names


async def test_resolve_caps_names_per_call(stack: Stack) -> None:
    from mtg_gateway.scan.service import MAX_RESOLVE_CARDS

    token = await mcp_token(stack.h)
    names = [{"name": "Sol Ring"}] * (MAX_RESOLVE_CARDS + 1)
    out = (await call(stack.h, token, "resolve_cards", {"cards": names}))["structuredContent"]
    assert out == {"ok": False, "error": "invalid", "message": f"at most {MAX_RESOLVE_CARDS} cards per call"}
    assert not any(m == "POST" for m, _ in stack.sf.requests)  # rejected before any Scryfall traffic


async def test_one_resolve_in_flight_per_user(stack: Stack) -> None:
    """A second concurrent call from the same account is rejected, not queued; other users are unaffected."""
    from mtg_gateway.scan.service import CardInput, ScanError

    sf = stack.sf
    gate = asyncio.Event()

    async def slow(request: httpx.Request) -> httpx.Response:
        await gate.wait()
        return sf.handle(request)

    service = stack.h.app.state.gateway.scan
    service.scryfall = ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(slow), base_url="https://scryfall.test"),
    )
    first = asyncio.create_task(service.resolve([CardInput("Sol Ring")], owner="alice"))
    await asyncio.sleep(0.05)
    with pytest.raises(ScanError) as exc:
        await service.resolve([CardInput("Sol Ring")], owner="alice")
    assert exc.value.kind == "busy"
    other = asyncio.create_task(service.resolve([CardInput("Sol Ring")], owner="bob"))
    await asyncio.sleep(0.05)
    gate.set()
    assert (await first)[0].status == "exact"
    assert (await other)[0].status == "exact"
    # Once the first call finishes, the account may resolve again.
    assert (await service.resolve([CardInput("Sol Ring")], owner="alice"))[0].status == "exact"


async def test_browser_resolve_reports_busy_as_429(stack: Stack) -> None:
    from mtg_gateway.scan.service import ScanError

    b = Browser(stack.h)
    await b.login()
    service = stack.h.app.state.gateway.scan
    real = service.resolve

    async def busy(*a, **k):
        raise ScanError("busy", "another card lookup for your account is still running; wait for it")

    service.resolve = busy
    try:
        r = await api(b, "POST", "/scan/api/resolve", {"cards": [{"name": "Sol Ring"}]})
    finally:
        service.resolve = real
    assert r.status_code == 429 and r.json()["error"] == "busy"


def test_ocr_variants_are_bounded_and_plausible() -> None:
    from mtg_gateway.scan.service import ocr_variants

    assert ocr_variants("S0l Ring", 4)[0] == "Sol Ring"
    assert "Sol Ring" in ocr_variants("Sol Rlng", 4)
    assert "Cultivate" in ocr_variants("Cultlvate", 4)
    assert ocr_variants("Rarnpant Growth", 4) == ["Rampant Growth"]
    assert ocr_variants("Sol Ring", 4) == ["Soi Ring", "Sol Rlng"]
    assert len(ocr_variants("llllllllll", 4)) == 4


@pytest.mark.skipif(not os.environ.get("SCAN_LIVE"), reason="set SCAN_LIVE=1 to query api.scryfall.com")
async def test_live_scryfall_recovers_known_ocr_misreads() -> None:
    """Inputs the live-checks thread found unresolvable on 2026-10-04. Opt-in: hits Scryfall."""
    from mtg_gateway.db import Database
    from mtg_gateway.scan.service import CardInput, ScanService

    client = ScryfallClient("https://api.scryfall.com", user_agent="MTGAssistantGateway-tests/0.1")
    try:
        service = ScanService(Database(":memory:"), client)
        results = await service.resolve(
            [CardInput("Sol Rlng"), CardInput("S0l Ring"), CardInput("Cultlvate"), CardInput("Xqzv Plorth")]
        )
    finally:
        await client.aclose()
    names = [r.card["name"] if r.card else None for r in results]
    assert names == ["Sol Ring", "Sol Ring", "Cultivate", None]
    assert [r.status for r in results] == ["fuzzy", "fuzzy", "fuzzy", "not_found"]


# -- Scryfall rate limits ------------------------------------------------------
async def test_rate_limit_midway_keeps_resolved_cards_and_defers_the_rest(stack: Stack) -> None:
    """A 429 part-way through a call must not throw away the lookups that already succeeded."""
    sf = stack.sf
    real = sf.handle
    seen = {"n": 0}

    def limited(request: httpx.Request) -> httpx.Response:
        seen["n"] += 1
        if seen["n"] >= 3:  # 1: collection, 2: fuzzy for "Sol Rng", 3: its autocomplete -> 429
            return httpx.Response(
                429, headers={"Retry-After": "30"}, json={"object": "error", "details": "slow down"}
            )
        return real(request)

    service = stack.h.app.state.gateway.scan
    service.scryfall = ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(limited), base_url="https://scryfall.test"),
    )
    token = await mcp_token(stack.h)
    cards = [
        {"name": "Sol Ring", "quantity": 2},
        {"set": "cmr", "collector_number": "365"},
        {"name": "Sol Rng"},
        {"name": "Cultlvate"},
    ]
    out = (await call(stack.h, token, "resolve_cards", {"cards": cards}))["structuredContent"]
    assert out["ok"] is True and out["complete"] is False
    assert [c["status"] for c in out["cards"]] == ["exact", "printing", "deferred", "deferred"]
    assert out["cards"][0]["card"]["name"] == "Sol Ring" and out["card_count"] == 3
    assert out["deferred"] == 2 and 1 <= out["retry_in"] <= 30
    assert "not looked up yet" in out["cards"][2]["note"] and "try again in" in out["cards"][2]["note"]
    assert out["needs_review"] == [2, 3] and "2 of 4 names were not looked up" in out["message"]
    assert seen["n"] == 3  # nothing else was attempted after the 429

    # While the back-off window is open, a new call is answered at once with everything deferred.
    out2 = (await call(stack.h, token, "resolve_cards", {"cards": [{"name": "Cultivate"}]}))[
        "structuredContent"
    ]
    assert out2["ok"] is True and out2["complete"] is False
    assert [c["status"] for c in out2["cards"]] == ["deferred"]
    assert seen["n"] == 3  # no request was sent to Scryfall

    # The saved session keeps the partial state so the user can finish it later.
    saved = (await call(stack.h, token, "save_scan_session", {"name": "Partial", "cards": cards}))[
        "structuredContent"
    ]
    assert saved["ok"] is True and saved["unresolved"] == 4  # all deferred during the back-off
    assert {it["status"] for it in saved["items"]} == {"deferred"}


async def test_call_time_budget_defers_the_rest(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    """A long list of unreadable names is answered within the call budget, the rest deferred.

    The budget clock is faked: every Scryfall request costs 3 s of a 10 s budget and nothing
    else does, so the outcome does not depend on how fast the machine running the test is
    (the real timeouts derived from the budget stay seconds long)."""
    sf = stack.sf
    real = sf.handle
    clock = {"now": 1000.0}

    class FakeTime:
        monotonic = staticmethod(lambda: clock["now"])
        time = staticmethod(time.time)

    monkeypatch.setattr(scan_service, "time", FakeTime)

    async def slow(request: httpx.Request) -> httpx.Response:
        clock["now"] += 3.0
        return real(request)

    service = stack.h.app.state.gateway.scan
    service.scryfall = ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(slow), base_url="https://scryfall.test"),
    )
    service.call_seconds = 10.0
    try:
        token = await mcp_token(stack.h)
        names = [{"name": "Sol Ring"}] + [{"name": f"Zxqv Blorth {i}"} for i in range(12)]
        out = (await call(stack.h, token, "resolve_cards", {"cards": names}))["structuredContent"]
    finally:
        service.call_seconds = 40.0
    statuses = [c["status"] for c in out["cards"]]
    assert statuses[0] == "exact" and "deferred" in statuses and statuses[-1] == "deferred"
    assert out["complete"] is False and out["retry_in"] == 0
    assert "lookup time is used up" in out["cards"][-1]["note"]
    assert "resolve the deferred names again now" in out["message"]
    # Everything attempted was finished or deferred; nothing was dropped.
    assert len(out["cards"]) == len(names) and out["deferred"] == statuses.count("deferred")


async def test_browser_resolve_shows_deferred_state(stack: Stack) -> None:
    sf = stack.sf
    real = sf.handle

    def limited(request: httpx.Request) -> httpx.Response:
        if request.url.path != "/cards/collection":
            return httpx.Response(429, headers={"Retry-After": "7"}, json={"object": "error"})
        return real(request)

    service = stack.h.app.state.gateway.scan
    service.scryfall = ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(limited), base_url="https://scryfall.test"),
    )
    b = Browser(stack.h)
    await b.login()
    r = await api(b, "POST", "/scan/api/resolve", {"cards": [{"name": "Sol Ring"}, {"name": "Sol Rng"}]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["complete"] is False and body["deferred"] == 1 and body["retry_in"] == 7
    assert [c["status"] for c in body["cards"]] == ["exact", "deferred"]
    # While the back-off is open, autocomplete says how long to wait.
    r = await b.http.get("/scan/api/search", params={"q": "Sol"})
    assert r.status_code == 503 and r.headers.get("retry-after") == "7", (r.status_code, r.headers)


async def test_scryfall_client_paces_single_lookups_more_slowly_than_batches() -> None:
    import time

    sf = FakeScryfall()
    client = ScryfallClient("https://scryfall.test", min_interval=0.0, lookup_interval=0.15, http=sf.client())
    t0 = time.monotonic()
    await client.named("Sol Ring")
    await client.named("Cultivate")
    await client.autocomplete("Aesi")
    slow = time.monotonic() - t0
    t0 = time.monotonic()
    await client.collection([{"name": "Sol Ring"}])
    await client.collection([{"name": "Cultivate"}])
    fast = time.monotonic() - t0
    assert slow >= 0.3, slow  # two gaps of at least 0.15 s between three single lookups
    assert fast < 0.1, fast  # the batch endpoint keeps the fast pacer
    assert client.retry_in() == 0
    assert client.lookup_interval == 0.15
    assert ScryfallClient("https://scryfall.test", min_interval=0.2, http=sf.client()).lookup_interval == 0.2


# -- exact printings from the info line ------------------------------------------
async def test_info_line_resolves_the_printing_and_distrusts_a_conflicting_read(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    out = (
        await call(
            stack.h,
            token,
            "resolve_cards",
            {
                "cards": [
                    # Leading zeros as printed on the card; Scryfall keys the number without them.
                    {
                        "name": "Aesi, Tyrant of Gyre Strait",
                        "set": "CMR",
                        "collector_number": "0365",
                        "foil": True,
                    },  # noqa: E501
                    # The info line was misread (points at Aesi) but the title clearly says Sol Ring.
                    {"name": "Sol Ring", "set": "cmr", "collector_number": "365", "lang": "en"},
                    # Foil marker from a pasted list, on a printing that only exists non-foil.
                    "1 Sol Ring (FRC) 21 *F*",
                ]
            },
        )
    )["structuredContent"]
    cards = out["cards"]
    assert cards[0]["status"] == "printing" and cards[0]["card"]["collector_number"] == "365"
    assert cards[0]["foil"] is True and cards[0]["input"]["collector_number"] == "365"
    assert cards[1]["status"] == "exact" and cards[1]["card"]["name"] == "Sol Ring"
    assert "does not match 'Sol Ring'; matched by name instead" in cards[1]["note"]
    assert cards[1]["input"]["lang"] == "en"
    # The single finish wins over the pasted marker, and the note says so.
    assert cards[2]["status"] == "printing" and cards[2]["card"]["set"] == "frc" and cards[2]["foil"] is False
    assert "only exists non-foil; the foil read was ignored" in cards[2]["note"]
    assert out["decklist_text"].splitlines()[0].endswith("*F*")  # CMR 365 Aesi, foil as read and by finish
    assert not out["decklist_text"].splitlines()[-1].endswith("*F*")


async def test_printing_needs_the_title_to_agree(stack: Stack) -> None:
    """A misread number that lands on a similarly named card must not come back as that card."""
    token = await mcp_token(stack.h)
    out = (
        await call(
            stack.h,
            token,
            "resolve_cards",
            {
                "cards": [
                    # Sol Talisman's number under a Sol Ring title (similarity 0.6).
                    {"name": "Sol Ring", "set": "mh2", "collector_number": "236"},
                    # Command Beacon's number under a Command Tower title (similarity 0.67).
                    {"name": "Command Tower", "set": "tdc", "collector_number": "352"},
                    # A long name cut short by the title strip completes to exactly one card: agreed.
                    {"name": "Aesi, Tyrant", "set": "cmr", "collector_number": "365"},
                    # A badly read title that still resolves to the printed card: the printing stands.
                    {"name": "Aesi Tyrant of Gyre Stralt", "set": "dsc", "collector_number": "210"},
                    # Garbage title over a real printing: nobody can tell, so both are offered.
                    {"name": "Xq Zzyx", "set": "cmr", "collector_number": "365"},
                    # Complete names that begin a longer card's name: the title is the card.
                    {"name": "Mountain", "set": "6ed", "collector_number": "195"},  # Mountain Goat
                    {"name": "Apocalypse", "set": "bbd", "collector_number": "217"},  # Apocalypse Hydra
                ]
            },
        )
    )["structuredContent"]
    cards = out["cards"]
    assert cards[0]["status"] == "exact" and cards[0]["card"]["name"] == "Sol Ring"
    assert (
        "MH2 236 is Sol Talisman, which does not match 'Sol Ring'; matched by name instead"
        in (cards[0]["note"])
    )
    assert cards[0]["input"] == {"name": "Sol Ring", "set": "mh2", "collector_number": "236", "quantity": 1}
    assert cards[1]["status"] == "exact" and cards[1]["card"]["name"] == "Command Tower"
    assert "TDC 352 is Command Beacon" in cards[1]["note"]
    assert cards[2]["status"] == "printing" and cards[2]["card"]["collector_number"] == "365"
    assert cards[2]["note"] == "title read as 'Aesi, Tyrant'"
    assert cards[3]["status"] == "printing" and cards[3]["card"]["set"] == "dsc"
    assert cards[3]["note"] == "title read as 'Aesi Tyrant of Gyre Stralt'"
    assert cards[4]["status"] == "ambiguous" and cards[4]["card"] is None
    assert cards[4]["suggestions"][0] == "Aesi, Tyrant of Gyre Strait"
    assert "CMR 365 is Aesi, Tyrant of Gyre Strait but the title read 'Xq Zzyx'" in cards[4]["note"]
    assert cards[5]["status"] == "exact" and cards[5]["card"]["name"] == "Mountain"
    assert "6ED 195 is Mountain Goat, which does not match 'Mountain'" in cards[5]["note"]
    assert cards[6]["status"] == "exact" and cards[6]["card"]["name"] == "Apocalypse"
    assert "BBD 217 is Apocalypse Hydra" in cards[6]["note"]


async def test_resolve_rejects_bad_foil_lang_and_art_hash(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    for bad in (
        {"name": "Sol Ring", "foil": "yes"},
        {"name": "Sol Ring", "lang": "english"},
        {"name": "Sol Ring", "art_hash": "not hex!"},
    ):
        out = (await call(stack.h, token, "resolve_cards", {"cards": [bad]}))["structuredContent"]
        assert out["ok"] is False and out["error"] == "invalid", out


async def test_art_matcher_hook_can_pick_the_printing(stack: Stack) -> None:
    from mtg_gateway.scan.service import CardInput

    service = stack.h.app.state.gateway.scan
    seen: list[tuple[str, str]] = []

    async def matcher(inp: CardInput, card: dict) -> dict | None:
        seen.append((inp.art_hash, card["name"]))
        if card["name"] == "Aesi, Tyrant of Gyre Strait":
            return stack.sf.by_print("dsc", "210")  # the reprint with the same art
        return None

    service.art_matcher = matcher
    try:
        token = await mcp_token(stack.h)
        out = (
            await call(
                stack.h,
                token,
                "resolve_cards",
                {"cards": [{"name": "Aesi, Tyrant of Gyre Strait", "art_hash": "abc123"}, "Sol Ring"]},
            )
        )["structuredContent"]
    finally:
        service.art_matcher = None
    assert out["cards"][0]["card"]["set"] == "dsc" and "printing chosen by artwork" in out["cards"][0]["note"]
    assert out["cards"][1]["card"]["name"] == "Sol Ring"
    assert seen == [("abc123", "Aesi, Tyrant of Gyre Strait")]  # not called without an art hash


async def test_art_matcher_shares_the_call_budget(stack: Stack, monkeypatch: pytest.MonkeyPatch) -> None:
    """The hook is capped by the call's lookup budget and cut off at its deadline."""
    import time

    from mtg_gateway.scan import service as service_module
    from mtg_gateway.scan.service import CardInput

    service = stack.h.app.state.gateway.scan
    calls: list[str] = []

    async def slow_matcher(inp: CardInput, card: dict) -> dict | None:
        calls.append(inp.art_hash)
        await asyncio.sleep(5)
        return stack.sf.by_print("dsc", "210")

    monkeypatch.setattr(service_module, "MAX_RECOVERY_LOOKUPS_PER_CALL", 1)
    service.art_matcher = slow_matcher
    service.call_seconds = 1.0
    try:
        token = await mcp_token(stack.h)
        t0 = time.monotonic()
        out = (
            await call(
                stack.h,
                token,
                "resolve_cards",
                {
                    "cards": [
                        {"name": "Aesi, Tyrant of Gyre Strait", "art_hash": "one"},
                        {"name": "Sol Ring", "art_hash": "two"},
                    ]
                },
            )
        )["structuredContent"]
        took = time.monotonic() - t0
    finally:
        service.art_matcher = None
        service.call_seconds = 40.0
    assert took < 3, took  # the 5 s matcher was cut off at the 1 s deadline
    assert calls == ["one"]  # the second card was never offered: one lookup in the budget
    assert out["cards"][0]["card"]["set"] == "cmr" and "artwork" not in out["cards"][0]["note"]
    assert out["cards"][1]["card"]["name"] == "Sol Ring"


async def test_scan_config_carries_thresholds(stack: Stack) -> None:
    b = Browser(stack.h)
    await b.login()
    r = await b.http.get("/scan")
    start = r.text.index("<script id='scan-config'")
    blob = json.loads(r.text[r.text.index(">", start) + 1 : r.text.index("</script>", start)])
    assert blob["thresholds"]["fuzzy_min_similarity"] == 0.65
    assert blob["thresholds"]["auto_add_confidence"] == 80.0
    assert blob["thresholds"]["foil_star_ink_ratio"] == 0.09
    assert blob["thresholds"]["glare_ratio"] == 0.08
    assert blob["thresholds"]["min_ocr_confidence"] == 50.0


async def test_truncated_title_check_sees_all_completions(stack: Stack) -> None:
    """Scryfall sends up to 20 completions; the printed card may sit past the 8 shown as suggestions."""
    aesi = "Aesi, Tyrant of Gyre Strait"
    filler = [f"Aesi, Tyrant of Gyre Strait{i}" for i in range(9)]
    stack.sf.autocomplete["aesi, tyrant"] = {
        "object": "catalog",
        "total_values": 11,
        "data": filler + [aesi, f"{aesi} // {aesi}"],
    }
    token = await mcp_token(stack.h)
    out = (
        await call(
            stack.h,
            token,
            "resolve_cards",
            {"cards": [{"name": "Aesi, Tyrant", "set": "cmr", "collector_number": "365"}]},
        )
    )["structuredContent"]
    # Nine other completions start with the fragment too, so it is not unique: ambiguous, not printing,
    # and the suggestions shown are capped while the check saw them all.
    assert out["cards"][0]["status"] == "ambiguous"
    assert out["cards"][0]["suggestions"][0] == aesi and len(out["cards"][0]["suggestions"]) <= 8
    # (A different fragment: the client caches completions per query.)
    stack.sf.autocomplete["aesi, tyran"] = {
        "object": "catalog",
        "total_values": 11,
        "data": [f"Sol Ring {i}" for i in range(9)] + [aesi, f"{aesi} // {aesi}"],
    }
    out = (
        await call(
            stack.h,
            token,
            "resolve_cards",
            {"cards": [{"name": "Aesi, Tyran", "set": "cmr", "collector_number": "365", "foil": True}]},
        )
    )["structuredContent"]
    # Past the first eight entries, and listed twice with its back face: still the one completion.
    assert out["cards"][0]["status"] == "printing" and out["cards"][0]["foil"] is True


# -- printings -------------------------------------------------------------------
async def test_printings_by_oracle_id_and_by_name(stack: Stack) -> None:
    """The picker's list: every printing of a card, newest first, with finishes and art images."""
    aesi_oracle = "6511f317-bd38-46d0-b800-7125a3f420da"
    token = await mcp_token(stack.h)
    out = (await call(stack.h, token, "card_printings", {"oracle_id": aesi_oracle}))["structuredContent"]
    assert out["ok"] is True and [c["set"] for c in out["cards"]] == ["sld", "dsc", "plst", "cmr"]
    assert out["has_more"] is False and out["total_cards"] == 4
    assert out["cards"][-1]["finishes"] == ["foil"] and out["cards"][-1]["image_art"].startswith("https://")
    # Printings are cached as summaries in a cache of their own, not as raw Scryfall records.
    cached = stack.h.app.state.gateway.scan.scryfall.prints_cache.get(aesi_oracle)
    assert cached is not None and set(cached["cards"][0]) == set(out["cards"][0])
    assert "prints" not in str(stack.h.app.state.gateway.scan.scryfall.names._items.keys())
    by_name = (await call(stack.h, token, "card_printings", {"name": "Aesi, Tyrant of Gyre Strait"}))[
        "structuredContent"
    ]
    assert by_name["oracle_id"] == aesi_oracle and len(by_name["cards"]) == 4
    bad = (await call(stack.h, token, "card_printings", {"oracle_id": "not-an-id"}))["structuredContent"]
    assert bad["ok"] is False and bad["error"] == "invalid"
    none = (await call(stack.h, token, "card_printings", {}))["structuredContent"]
    assert none["ok"] is False and none["error"] == "invalid"
    # The page's endpoint, behind the browser session, cached per user.
    b = Browser(stack.h)
    await b.login()
    r = await b.http.get(f"/scan/api/prints?oracle_id={aesi_oracle}")
    assert r.status_code == 200 and len(r.json()["cards"]) == 4
    assert "private" in r.headers["cache-control"]
    r = await b.http.get("/scan/api/prints?oracle_id=00000000-0000-0000-0000-000000000000")
    assert r.status_code == 404
    # The later lookups for the same card are served from the client's cache.
    searches = [u for m, u in stack.sf.requests if "/cards/search" in u and aesi_oracle in u]
    assert len(searches) == 1, searches


async def test_foil_follows_a_single_finish_and_changes_carry_the_printing(stack: Stack) -> None:
    """CMR 365 Aesi exists in foil only, so a scan of it is foil without a star being read."""
    token = await mcp_token(stack.h)
    out = (
        await call(
            stack.h,
            token,
            "resolve_cards",
            {
                "cards": [
                    {"name": "Aesi, Tyrant of Gyre Strait", "set": "cmr", "collector_number": "365"},
                    {"name": "Aesi, Tyrant of Gyre Strait", "set": "cmr", "collector_number": "365"},
                    {"name": "Sol Ring", "set": "frc", "collector_number": "21", "quantity": 2},
                    {"name": "Sol Ring", "set": "cmr", "collector_number": "472"},
                    {"name": "Cultivate", "set": "msc", "collector_number": "172"},
                    {"name": "Command Tower", "set": "frc", "collector_number": "22", "foil": True},
                ]
            },
        )
    )["structuredContent"]
    cards = out["cards"]
    assert cards[0]["foil"] is True and cards[0]["card"]["finishes"] == ["foil"]
    assert cards[2]["foil"] is False  # FRC 21 exists in nonfoil only
    assert cards[4]["foil"] is None  # MSC 172 comes both ways and nothing was read
    # A star read on a nonfoil-only printing names a finish that does not exist: the printing wins.
    assert cards[5]["foil"] is False and "only exists non-foil; the foil read was ignored" in cards[5]["note"]
    assert out["decklist_text"].splitlines()[0].endswith("*F*")
    changes = {c["card_name"]: c for c in out["changes"]}
    # One agreed printing in one finish: the addition names it. Sol Ring, with one copy matched by
    # name only (the fixtures have no CMR 472): by name only.
    assert changes["Aesi, Tyrant of Gyre Strait"] == {
        "action": "add",
        "card_name": "Aesi, Tyrant of Gyre Strait",
        "quantity": 2,
        "set_code": "cmr",
        "collector_number": "365",
        "foil": True,
    }
    assert changes["Sol Ring"] == {"action": "add", "card_name": "Sol Ring", "quantity": 3}
    # An unknown finish is not a claim of "not foil": the printing goes along, foil stays out.
    assert changes["Cultivate"] == {
        "action": "add",
        "card_name": "Cultivate",
        "quantity": 1,
        "set_code": "msc",
        "collector_number": "172",
    }


def test_unique_completion_needs_one_matching_suggestion() -> None:
    from mtg_gateway.scan.service import _unique_completion

    aesi = "Aesi, Tyrant of Gyre Strait"
    assert _unique_completion("Aesi, Tyrant", [aesi], aesi)
    assert _unique_completion("Aesi, Tyrant", ["Sol Ring", aesi], aesi)  # unrelated suggestions do not count
    assert _unique_completion("Aesi, Tyrant", [aesi, f"{aesi} // {aesi}"], aesi)  # Scryfall's // duplicate
    assert not _unique_completion("Aesi, Tyrant", [aesi, "Aesi, Tyrant of Nowhere"], aesi)
    assert not _unique_completion("Mountain", ["Mountain", "Mountain Goat"], "Mountain Goat")
    assert not _unique_completion("Aesi, Tyrant", [], aesi)  # nothing to complete to
    assert not _unique_completion("Sol Ring", ["Sol Talisman"], "Sol Talisman")  # not a prefix
    assert not _unique_completion("", [aesi], aesi)


def test_clean_number_strips_leading_zeros_only() -> None:
    from mtg_gateway.scan.service import _clean_number

    assert _clean_number("0472") == "472"
    assert _clean_number("0") == "0"
    assert _clean_number("000") == "0"
    assert _clean_number("123a") == "123a"
    assert _clean_number("A05") == "A05"
    assert _clean_number(" 21 ") == "21"


async def test_confident_fuzzy_match_skips_the_second_lookup(stack: Stack) -> None:
    """Finding E: a clear misread ("Aesi, Tyrant of Gyre Stralt") used to cost fuzzy + autocomplete."""
    token = await mcp_token(stack.h)
    out = (await call(stack.h, token, "resolve_cards", {"cards": [{"name": "Aesi, Tyrant of Gyre Stralt"}]}))[
        "structuredContent"
    ]
    card = out["cards"][0]
    assert card["status"] == "fuzzy" and card["card"]["name"] == "Aesi, Tyrant of Gyre Strait"
    gets = [u for m, u in stack.sf.requests if m == "GET"]
    assert len(gets) == 1 and "fuzzy=" in gets[0], gets


async def test_call_budget_counts_the_batched_pass(stack: Stack) -> None:
    """Finding F: the clock starts when the call starts, and the batched pass runs under it too."""
    sf = stack.sf
    real = sf.handle

    async def slow(request: httpx.Request) -> httpx.Response:
        await asyncio.sleep(0.12 if request.url.path == "/cards/collection" else 0.01)
        return real(request)

    service = stack.h.app.state.gateway.scan
    service.scryfall = ScryfallClient(
        "https://scryfall.test",
        min_interval=0.0,
        http=httpx.AsyncClient(transport=httpx.MockTransport(slow), base_url="https://scryfall.test"),
    )
    service.call_seconds = 0.1
    try:
        token = await mcp_token(stack.h)
        names = [{"name": "Sol Ring"}, {"name": "Zxqv"}]
        out = (await call(stack.h, token, "resolve_cards", {"cards": names}))["structuredContent"]
    finally:
        service.call_seconds = 40.0
    # The batched pass outlived the budget: everything is deferred, and no single lookup follows.
    assert [c["status"] for c in out["cards"]] == ["deferred", "deferred"]
    assert out["retry_in"] == 0 and "lookup time is used up" in out["cards"][0]["note"]
    assert all(m == "POST" for m, _ in sf.requests)
