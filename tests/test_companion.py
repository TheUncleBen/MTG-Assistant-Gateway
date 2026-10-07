"""The companion: JSON API (bearer and cookie), the deck, history and activity pages, stored
reports, exports and the app shell. Writes go through proposals exactly as the tools do."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import Client

from mtg_gateway.archidekt import ArchidektClient, Pacer
from mtg_gateway.mf_proxy import MysticForgeProxy

from .conftest import FakeIdP, Harness, make_settings, running, sse_json
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, call, fake_mystic_forge, mcp_token, structured

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}


class Stack:
    def __init__(self, h: Harness, ark: FakeArchidekt):
        self.h = h
        self.ark = ark


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP):
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path,
        writes_enabled=True,
        approval_mode_default="auto",
        archidekt_base="https://ark.test/api",
        android_assetlinks='[{"relation": ["delegate_permission/common.handle_all_urls"]}]',
    )
    client = ArchidektClient(settings.archidekt_base, "test-agent", Pacer(0.0), http=ark.client())
    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(fake_mystic_forge()))
    async with running(Harness(settings, idp, archidekt=client, mf_proxy=proxy)) as h:
        yield Stack(h, ark)


async def linked_browser(stack: Stack) -> Browser:
    b = Browser(stack.h)
    await b.login("/account")
    r = await b.link("alice", "pw-alice")
    assert r.status_code == 303, r.text
    return b


# -- API authentication ----------------------------------------------------------
async def test_api_needs_a_token_or_a_session(stack: Stack) -> None:
    h = stack.h
    r = await h.http.get("/api/v1/me")
    assert r.status_code == 401 and r.json()["error"] == "unauthenticated"
    assert "Bearer" in r.headers["www-authenticate"]
    r = await h.http.get("/api/v1/me", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401
    token = await mcp_token(h)
    r = await h.http.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert r.json()["via"] == "api" and r.json()["account"]["linked"] is False
    assert r.headers["cache-control"] == "no-store"


async def test_cookie_writes_need_the_csrf_header(stack: Stack) -> None:
    b = await linked_browser(stack)
    try:
        me = await b.http.get("/api/v1/me")
        assert me.status_code == 200 and me.json()["via"] == "browser"
        r = await b.http.post("/api/v1/proposals", json={"deck_id": "42", "changes": []})
        assert r.status_code == 403 and r.json()["error"] == "csrf"
        csrf = await b.csrf("/account")
        r = await b.http.post(
            "/api/v1/proposals",
            json={"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 201, r.text
        assert r.json()["state"] == "pending"
    finally:
        await b.aclose()


# -- decks, proposals and snapshots over the API --------------------------------
async def test_api_deck_flow(stack: Stack) -> None:
    h = stack.h
    b = await linked_browser(stack)
    try:
        token = await mcp_token(h)
        auth = {"Authorization": f"Bearer {token}"}
        decks = await h.http.get("/api/v1/decks", headers=auth)
        assert decks.status_code == 200
        ids = [d["id"] for d in decks.json()["decks"]]
        assert "42" in ids and decks.json()["decks"][0]["url"].startswith("https://archidekt.com/decks/")
        deck = await h.http.get("/api/v1/decks/42", headers=auth)
        assert deck.status_code == 200
        body = deck.json()
        assert body["cards"] and body["stats"]["card_count"] == body["card_count"]
        assert "mana_curve" in body["stats"]
        stats = await h.http.get("/api/v1/decks/42/stats", headers=auth)
        assert stats.status_code == 200 and stats.json()["deck"]["id"] == "42"
        # propose, apply (writes are on and apply over MCP is allowed), snapshot, compare
        p = await h.http.post(
            "/api/v1/proposals",
            json={"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring", "quantity": 1}]},
            headers=auth,
        )
        assert p.status_code == 201, p.text
        pid = p.json()["proposal_id"]
        applied = await h.http.post(f"/api/v1/proposals/{pid}/apply", headers=auth)
        assert applied.status_code == 200, applied.text
        assert applied.json()["state"] == "applied"
        snaps = await h.http.get("/api/v1/snapshots?deck_id=42", headers=auth)
        assert snaps.status_code == 200 and len(snaps.json()["snapshots"]) == 1
        sid = snaps.json()["snapshots"][0]["snapshot_id"]
        snap = await h.http.get(f"/api/v1/snapshots/{sid}", headers=auth)
        assert snap.status_code == 200 and snap.json()["deck"]["id"] == "42"
        cmp_ = await h.http.get(f"/api/v1/compare?a={sid}&b=42", headers=auth)
        assert cmp_.status_code == 200, cmp_.text
        assert any(c["name"] == "Sol Ring" for c in cmp_.json()["added"] + cmp_.json()["changed"])
        hist = await h.http.get("/api/v1/decks/42/history", headers=auth)
        assert hist.status_code == 200
        assert len(hist.json()["proposals"]) == 1 and len(hist.json()["snapshots"]) == 1
        # reject a second proposal
        p2 = await h.http.post(
            "/api/v1/proposals",
            json={"deck_id": "42", "changes": [{"action": "remove", "card_name": "Sol Ring"}]},
            headers=auth,
        )
        assert p2.status_code == 201
        rej = await h.http.post(f"/api/v1/proposals/{p2.json()['proposal_id']}/reject", headers=auth)
        assert rej.status_code == 200 and rej.json()["state"] == "rejected"
        again = await h.http.post(f"/api/v1/proposals/{p2.json()['proposal_id']}/reject", headers=auth)
        assert again.status_code == 409
        missing = await h.http.get("/api/v1/proposals/nope", headers=auth)
        assert missing.status_code == 404 and missing.json()["error"] == "not_found"
    finally:
        await b.aclose()


async def test_api_other_user_sees_nothing(stack: Stack, idp: FakeIdP) -> None:
    h = stack.h
    b = await linked_browser(stack)
    try:
        token = await mcp_token(h)
        auth = {"Authorization": f"Bearer {token}"}
        p = await h.http.post(
            "/api/v1/proposals",
            json={"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]},
            headers=auth,
        )
        pid = p.json()["proposal_id"]
        idp.user = {**idp.user, "sub": "user-2", "preferred_username": "bob"}
        other = {"Authorization": f"Bearer {await mcp_token(h)}"}
        assert (await h.http.get(f"/api/v1/proposals/{pid}", headers=other)).status_code == 404
        assert (await h.http.post(f"/api/v1/proposals/{pid}/apply", headers=other)).status_code == 404
        assert (await h.http.get("/api/v1/decks", headers=other)).status_code == 409  # not linked
    finally:
        await b.aclose()


# -- reports --------------------------------------------------------------------
async def test_reports_are_stored_and_listed(stack: Stack) -> None:
    h = stack.h
    b = await linked_browser(stack)
    try:
        token = await mcp_token(h)
        auth = {"Authorization": f"Bearer {token}"}
        r = await h.http.post("/api/v1/reports", json={"deck_id": "42", "games": 50}, headers=auth)
        assert r.status_code == 201, r.text
        rep = r.json()
        assert rep["stats"]["card_count"] > 0 and rep["metrics"]["card_count"] == rep["stats"]["card_count"]
        # the fake research service has no goldfish tool: recorded as a failed call, not an error
        assert rep["goldfish"]["ok"] is False and rep["validation"]["ok"] is False
        again = await h.http.post("/api/v1/reports", json={"deck_id": "42"}, headers=auth)
        assert again.status_code == 201 and again.json()["report_id"] == rep["report_id"]
        assert again.json()["reused"] is True
        listed = await h.http.get("/api/v1/reports?deck_id=42", headers=auth)
        assert listed.status_code == 200 and len(listed.json()["reports"]) == 1
        one = await h.http.get(f"/api/v1/reports/{rep['report_id']}", headers=auth)
        assert one.status_code == 200 and one.json()["deck_id"] == "42"
        bad = await h.http.post("/api/v1/reports", json={"deck_id": "42", "games": 5}, headers=auth)
        assert bad.status_code == 400
        # the same via MCP tools
        out = structured(await call(h, token, "list_deck_reports", {"deck_id": "42"}))
        assert out["ok"] and out["reports"][0]["report_id"] == rep["report_id"]
        got = structured(await call(h, token, "get_deck_report", {"report_id": rep["report_id"]}))
        assert got["ok"] and got["stats"]["card_count"] == rep["stats"]["card_count"]
        # pages
        page = await b.http.get("/history?deck_id=42", headers=NAV)
        assert page.status_code == 200 and "Report:" in page.text
        detail = await b.http.get(f"/history/reports/{rep['report_id']}", headers=NAV)
        assert detail.status_code == 200 and "Goldfish simulation" in detail.text
        gone = await h.http.delete(f"/api/v1/reports/{rep['report_id']}", headers=auth)
        assert gone.status_code == 200
        assert (await h.http.get(f"/api/v1/reports/{rep['report_id']}", headers=auth)).status_code == 404
    finally:
        await b.aclose()


# -- new MCP tools ----------------------------------------------------------------
async def test_new_tools_are_listed_and_work(stack: Stack) -> None:
    h = stack.h
    b = await linked_browser(stack)
    try:
        token = await mcp_token(h)
        r = await h.mcp(token, "tools/list")
        names = {t["name"] for t in sse_json(r)["result"]["tools"]}
        assert {
            "deck_stats",
            "compare_decks",
            "get_snapshot",
            "reject_proposal",
            "run_deck_report",
            "list_deck_reports",
            "get_deck_report",
        } <= names
        stats = structured(await call(h, token, "deck_stats", {"deck_ref": "42"}))
        assert stats["ok"] and stats["deck"]["id"] == "42" and "mana_curve" in stats["stats"]
        deck = structured(await call(h, token, "get_my_deck", {"deck_id": "42"}))
        assert deck["ok"] and "stats" in deck and "commanders" in deck
        listed = structured(await call(h, token, "list_my_decks", {"name_contains": "zzz-no-such"}))
        assert listed["ok"] and listed["decks"] == []
        p = structured(
            await call(
                h,
                token,
                "propose_deck_changes",
                {"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]},
            )
        )
        assert p["ok"]
        rej = structured(await call(h, token, "reject_proposal", {"proposal_id": p["proposal_id"]}))
        assert rej["ok"] and rej["state"] == "rejected"
        p2 = structured(
            await call(
                h,
                token,
                "propose_deck_changes",
                {"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]},
            )
        )
        applied = structured(await call(h, token, "apply_proposal", {"proposal_id": p2["proposal_id"]}))
        assert applied["ok"] and applied["state"] == "applied", applied
        sid = applied["result"]["snapshot_id"]
        snap = structured(await call(h, token, "get_snapshot", {"snapshot_id": sid}))
        assert snap["ok"] and snap["id"] == "42" and snap["snapshot_id"] == sid
        cmp_ = structured(await call(h, token, "compare_decks", {"a": snap["snapshot_id"], "b": "42"}))
        assert cmp_["ok"] and (cmp_["added"] or cmp_["changed"])
        text_cmp = structured(
            await call(h, token, "compare_decks", {"a": "1 Sol Ring\n1 Arcane Signet", "b": "1 Sol Ring"})
        )
        assert text_cmp["ok"] and [c["name"] for c in text_cmp["removed"]] == ["Arcane Signet"]
    finally:
        await b.aclose()


# -- pages ------------------------------------------------------------------------
async def test_deck_pages(stack: Stack) -> None:
    b = Browser(stack.h)
    try:
        await b.login("/decks")
        unlinked = await b.http.get("/decks", headers=NAV)
        assert unlinked.status_code == 200 and "Link Archidekt" in unlinked.text
        r = await b.link("alice", "pw-alice")
        assert r.status_code == 303
        lst = await b.http.get("/decks", headers=NAV)
        assert lst.status_code == 200
        assert "class='wide" in lst.text and "/decks/42" in lst.text and "tabbar" in lst.text
        assert "target='_blank'" not in lst.text.split("<main")[1].split("Open on Archidekt")[0]
        deck = await b.http.get("/decks/42", headers=NAV)
        assert deck.status_code == 200
        assert "Mana curve" in deck.text and "Run deck report" in deck.text and "Edit deck" in deck.text
        assert "Open on Archidekt" in deck.text  # the one external link may open a new tab
        missing = await b.http.get("/decks/999999", headers=NAV)
        assert missing.status_code == 404
        opened = await b.http.get("/decks/open?ref=https://archidekt.com/decks/42/x", headers=NAV)
        assert opened.status_code == 303 and opened.headers["location"] == "/decks/42"
        export = await b.http.get("/decks/42/export", headers=NAV)
        assert export.status_code == 200 and "<textarea" in export.text
        txt = await b.http.get("/decks/42/export.txt")
        assert txt.status_code == 200 and txt.headers["content-disposition"].startswith("attachment")
        js = await b.http.get("/decks/42/export.json")
        assert js.status_code == 200 and js.json()["id"] == "42"
        csrf = await b.csrf("/account")
        ran = await b.http.post("/decks/42/report", data={"csrf": csrf})
        assert ran.status_code == 303 and ran.headers["location"] == "/history?deck_id=42"
        hist = await b.http.get("/history", headers=NAV)
        assert hist.status_code == 200 and "Report:" in hist.text
        act = await b.http.get("/activity", headers=NAV)
        assert act.status_code == 200 and "archidekt linked" in act.text
        for path in ("/decks", "/decks/42", "/history", "/activity"):
            anon = await stack.h.http.get(path, headers=NAV)
            assert anon.status_code == 302 and anon.headers["location"].startswith("/login?next="), path
    finally:
        await b.aclose()


async def test_app_shell_routes(stack: Stack) -> None:
    h = stack.h
    m = await h.http.get("/app.webmanifest")
    assert m.status_code == 200 and m.json()["start_url"] == "/decks"
    assert m.headers["content-type"].startswith("application/manifest+json")
    sw = await h.http.get("/sw.js")
    assert sw.status_code == 200 and "fetch" in sw.text and sw.headers["cache-control"] == "no-store"
    links = await h.http.get("/.well-known/assetlinks.json")
    assert links.status_code == 200
    assert links.json()[0]["relation"] == ["delegate_permission/common.handle_all_urls"]
    assert (await h.http.get("/static/../app.py")).status_code == 404
    assert (await h.http.get("/static/nope.js")).status_code == 404


def editor_config(page: str) -> dict:
    return json.loads(page.split("id='editor-config' type='application/json'>")[1].split("</script>")[0])


async def test_deck_editor_page(stack: Stack) -> None:
    from mtg_gateway.scan.scryfall import ScryfallClient

    from .fake_scryfall import FakeScryfall

    h = stack.h
    h.app.state.gateway.scan.scryfall = ScryfallClient(
        "https://scryfall.test", min_interval=0.0, http=FakeScryfall().client()
    )
    b = Browser(h)
    try:
        await b.login("/decks")
        r = await b.link("alice", "pw-alice")
        assert r.status_code == 303
        edit = await b.http.get("/decks/42/edit", headers=NAV)
        assert edit.status_code == 200, edit.text
        assert "id='editor-config'" in edit.text and "/static/companion.js" in edit.text
        assert "script-src 'self'" in edit.headers["content-security-policy"]
        assert "connect-src 'self'" in edit.headers["content-security-policy"]
        cfg = json.loads(
            edit.text.split("id='editor-config' type='application/json'>")[1].split("</script>")[0]
        )
        assert cfg["deckId"] == "42" and cfg["cards"] and cfg["groups"] and cfg["csrf"]
        assert cfg["prefill"] == []
        # someone else's deck cannot be edited, a missing one is not found
        other = await b.http.get("/decks/43/edit", headers=NAV)
        assert other.status_code in (403, 404), other.text
        assert (await b.http.get("/decks/999999/edit", headers=NAV)).status_code == 404
        # a scan session prefills additions
        token = await mcp_token(h)
        saved = (await call(h, token, "save_scan_session", {"name": "Binder", "text": "2 Sol Ring"}))[
            "structuredContent"
        ]
        sid = saved["id"]
        pre = await b.http.get(f"/decks/42/edit?scan_session={sid}", headers=NAV)
        assert pre.status_code == 200
        cfg = json.loads(
            pre.text.split("id='editor-config' type='application/json'>")[1].split("</script>")[0]
        )
        assert cfg["prefill"] and cfg["prefill"][0]["action"] == "add"
        assert cfg["prefill"][0]["card_name"] == "Sol Ring" and cfg["prefill"][0]["quantity"] == 2
        assert "<option value='" + sid + "' selected>" in pre.text
        js = await b.http.get("/static/companion.js")
        assert js.status_code == 200 and "editor-config" in js.text
        anon = await h.http.get("/decks/42/edit", headers=NAV)
        assert anon.status_code == 302
    finally:
        await b.aclose()
