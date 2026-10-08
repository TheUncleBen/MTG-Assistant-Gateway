"""The companion: JSON API (bearer and cookie), the deck, history and activity pages, stored
reports, exports and the app shell. Writes go through proposals exactly as the tools do."""

from __future__ import annotations

import json
import re
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
        # the fake research service answers like Mystic Forge: the simulation and the validation
        # are recorded as they came back
        assert rep["goldfish"]["ok"] is True and "## Metrics" in rep["goldfish"]["text"]
        assert rep["validation"]["ok"] is True
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
        deck = structured(await call(h, token, "get_deck", {"deck_ref": "42"}))
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
        assert "Mana curve" in deck.text and "Run simulation" in deck.text and "Edit deck" in deck.text
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
        assert ran.status_code == 303 and ran.headers["location"].startswith("/history/reports/")
        shown = await b.http.get(ran.headers["location"], headers=NAV)
        assert shown.status_code == 200 and "Goldfish simulation" in shown.text and "## Metrics" in shown.text
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


async def test_playtest_page_frames_archidekts_playtester(stack: Stack) -> None:
    """The playtest page is Archidekt's own playtester in a frame (the only origin the page's CSP
    lets it frame), with the deck's name, a way back and the plain link as a fallback. Missing
    decks and anonymous visitors are handled like the deck page."""
    b = await linked_browser(stack)
    try:
        r = await b.http.get("/decks/42/playtest", headers=NAV)
        assert r.status_code == 200
        assert "<iframe class='playframe' src='https://archidekt.com/playtester-v2/42'" in r.text
        assert "sandbox='allow-scripts allow-same-origin" in r.text
        assert "href='https://archidekt.com/playtester-v2/42' target='_blank'" in r.text  # fallback
        assert "href='/decks/42'" in r.text and "Sample Commander Deck" in r.text
        csp = r.headers["content-security-policy"]
        assert "frame-src https://archidekt.com;" in csp and "frame-ancestors 'none'" in csp
        assert "script-src 'self'" in csp and "img-src 'self'" in csp  # the shell's own script and avatar
        assert "cards.scryfall.io" not in csp
        missing = await b.http.get("/decks/999999/playtest", headers=NAV)
        assert missing.status_code == 404
        anon = await stack.h.http.get("/decks/42/playtest", headers=NAV)
        assert anon.status_code == 302 and anon.headers["location"].startswith("/login?next=")
    finally:
        await b.aclose()


async def test_compare_page_shows_what_a_build_changed(stack: Stack) -> None:
    """The compare view pits this deck against a precon (offered from Archidekt's listing), any
    deck id or link, or a pasted list, with the assistant's compare_decks numbers: taken out,
    put in, changed counts and the statistics' differences."""
    b = await linked_browser(stack)
    try:
        empty = await b.http.get("/decks/42/compare", headers=NAV)
        assert empty.status_code == 200 and "<datalist id='preconlist'>" in empty.text
        assert "<option value='42'>" in empty.text  # the fake's precon listing carries deck 42
        assert "Taken out of" not in empty.text
        # against itself: nothing changes
        same = await b.http.get("/decks/42/compare?with=https://archidekt.com/decks/42/sample", headers=NAV)
        assert same.status_code == 200 and "Taken out of Sample Commander Deck" in same.text
        assert "<li class='muted'>None</li>" in same.text and "No difference" in same.text
        # against a pasted list: the paste is the "before", this deck the "after"
        paste = "1 Sol Ring\n4 Lightning Bolt\n3 Island\n"
        r = await b.http.get("/decks/42/compare", params={"paste": paste}, headers=NAV)
        assert r.status_code == 200 and "Taken out of the pasted list" in r.text
        assert "Lightning Bolt" in r.text and "class='cardlink'" in r.text  # a card the deck holds opens
        assert "compare.js" in r.text and "cardview.js" in r.text
        # the tool's view of the same comparison agrees with the page's tiles
        token = await mcp_token(stack.h)
        tool = structured(await call(stack.h, token, "compare_decks", {"a": paste, "b": "42"}))
        sm = tool["summary"]
        out_tile = f"<b>{sm['cut']}</b><span>cards taken out ({sm['cut_pct']}% of the pasted list)</span>"
        in_tile = f"<b>{sm['added']}</b><span>cards put in ({sm['added_pct']}% of Sample Commander Deck)"
        assert out_tile in r.text and in_tile in r.text
        # the lists under the tiles leave basic lands to their own list, so their counts match the tiles
        added_rows = [a for a in tool["added"] if a["name"] not in ("Island", "Forest")]
        assert f"Put into Sample Commander Deck <span class='count'>{len(added_rows)}</span>" in r.text
        assert len(added_rows) < len(tool["added"])  # the deck's Forests are only under Basic lands
        bad = await b.http.get("/decks/42/compare?with=https://evil.example/decks/1", headers=NAV)
        assert bad.status_code == 400 and "could not be read" in bad.text
        gone = await b.http.get("/decks/42/compare?with=999999", headers=NAV)
        assert gone.status_code == 404
    finally:
        await b.aclose()


async def test_export_import_round_trip_keeps_every_card_finish_and_commander(stack: Stack) -> None:
    """Export → import → compare, for each export the pages offer: the plain .txt, the Archidekt
    import text and the .csv each re-create the same deck (names, counts, finishes including
    etched, commander category, sideboard rows) through propose_new_deck and apply."""
    b = await linked_browser(stack)
    try:
        h, ark = stack.h, stack.ark
        token = await mcp_token(h)
        # a deck with an etched printing, a foil basic, a commander and a side row
        p = structured(
            await call(
                h,
                token,
                "propose_deck_changes",
                {
                    "deck_id": "42",
                    "changes": [
                        {
                            "action": "add",
                            "card_name": "Sol Ring",
                            "set_code": "SLD",
                            "collector_number": "1074",
                            "finish": "etched",
                        },
                        {"action": "add", "card_name": "Swamp", "quantity": 2, "foil": True},
                    ],
                },
            )
        )
        assert p["ok"], p
        assert structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))["ok"]
        ark.add_side_row(42, "Opt", 2)

        def shape(deck: dict) -> set[tuple]:
            return {
                (c["name"], c["quantity"], c["finish"], "Commander" in c["categories"], c["in_deck"])
                for c in deck["cards"]
            }

        source = structured(await call(h, token, "get_deck", {"deck_ref": "42", "view": "cards"}))
        want = shape(source)
        assert ("Sol Ring", 1, "Etched", False, True) in want and ("Swamp", 2, "Foil", False, True) in want
        assert any(cmd for (_, _, _, cmd, _) in want) and ("Opt", 2, "Normal", False, False) in want
        exports = {
            "plain .txt": ("decklist_text", (await b.http.get("/decks/42/export.txt")).text),
            "archidekt .txt": ("decklist_text", (await b.http.get("/decks/42/export.archidekt.txt")).text),
            ".csv": ("csv_text", (await b.http.get("/decks/42/export.csv")).text),
            ".json": ("json_text", (await b.http.get("/decks/42/export.json")).text),
        }
        assert "[Commander]" in exports["plain .txt"][1] and "*E*" in exports["plain .txt"][1]
        assert json.loads(exports[".json"][1])["id"] == "42"  # the gateway's own deck JSON, as is
        for label, (field, text) in exports.items():
            made = structured(
                await call(h, token, "propose_new_deck", {"name": f"Round trip {label}", field: text})
            )
            assert made["ok"], (label, made)
            applied = structured(await call(h, token, "apply_proposal", {"proposal_id": made["proposal_id"]}))
            assert applied["ok"] and applied["result"]["verified"], (label, applied)
            copy = structured(
                await call(h, token, "get_deck", {"deck_ref": applied["result"]["deck_id"], "view": "cards"})
            )
            got = shape(copy)
            assert got == want, (label, sorted(want - got), sorted(got - want))
        # The New deck page takes the same JSON (pasted, or read from a file by the page's script).
        csrf = await b.csrf("/account")
        r = await b.http.post(
            "/decks/new",
            data={
                "csrf": csrf,
                "name": "From the page",
                "format": "commander",
                "kind": "json",
                "source": exports[".json"][1],
            },
        )
        assert r.status_code == 303, r.text
        page = await b.http.get("/decks/new", headers=NAV)
        assert (
            "type='file'" in page.text and "filepick.js" in page.text and "gateway .json export" in page.text
        )
        made_ids = [d for d in ark.decks if ark.decks[d]["name"] == "From the page"]
        assert (
            len(made_ids) == 1
            and shape(
                structured(await call(h, token, "get_deck", {"deck_ref": str(made_ids[0]), "view": "cards"}))
            )
            == want
        )
        bad = structured(await call(h, token, "propose_new_deck", {"name": "x", "json_text": '{"cards": 3}'}))
        assert bad["ok"] is False and "cards list" in bad["message"], bad
    finally:
        await b.aclose()


async def test_new_deck_page_says_which_card_was_not_found(stack: Stack) -> None:
    """A New deck list with a card Archidekt does not know fails at apply time. The review page
    then says something the proposal needs was not found and shows the card's name in the result,
    instead of "No such proposal for your account" (the proposal does exist)."""
    b = await linked_browser(stack)
    try:
        csrf = await b.csrf("/account")
        r = await b.http.post(
            "/decks/new",
            data={
                "csrf": csrf,
                "name": "Typo deck",
                "format": "commander",
                "kind": "list",
                "source": "1 Sol Ring\n1 Definitely Not A Card Zzz\n",
            },
        )
        assert r.status_code == 303 and "?err=missing" in r.headers["location"], (r.status_code, r.headers)
        page = await b.http.get(r.headers["location"], headers=NAV)
        assert page.status_code == 200
        assert "was not found on Archidekt" in page.text and "No such proposal" not in page.text
        assert "Definitely Not A Card Zzz" in page.text  # the result names the card
        # the code for a proposal that really does not exist is unchanged
        gone = await b.http.get("/proposals/nope?err=not_found", headers=NAV)
        assert "No such proposal" in gone.text
    finally:
        await b.aclose()


async def test_export_only_formats_arena_mtgo_and_pdf(stack: Stack) -> None:
    """Arena text, an MTGO .dek and a PDF download for any deck the member can read. They are
    one-way (nothing imports them back); each names every card with its count and keeps the
    commander and sideboard apart. The PDF is read back with its own stream decoding."""
    import zlib

    b = await linked_browser(stack)
    try:
        stack.ark.add_side_row(42, "Opt", 2, category="Sideboard")
        stack.ark.add_side_row(42, "Delver of Secrets // Insectile Aberration", 1)  # Maybeboard: left out
        arena = await b.http.get("/decks/42/export.arena.txt")
        assert arena.status_code == 200 and arena.headers["content-disposition"].endswith('.arena.txt"')
        blocks = arena.text.strip().split("\n\n")
        assert [blk.splitlines()[0] for blk in blocks] == ["Commander", "Deck", "Sideboard"]
        assert blocks[0].splitlines()[1] == "1 Aesi, Tyrant of Gyre Strait (CMR) 365"
        assert blocks[2].splitlines()[1].startswith("2 Opt") and "1 Sol Ring (CMR) 472" in blocks[1]
        assert "Delver of Secrets" not in arena.text  # Arena has no maybeboard
        dek = await b.http.get("/decks/42/export.dek")
        assert dek.status_code == 200 and dek.headers["content-type"].startswith("application/xml")
        import xml.etree.ElementTree as ET

        root = ET.fromstring(dek.text)
        rows = {(c.get("Name"), c.get("Quantity"), c.get("Sideboard")) for c in root.iter("Cards")}
        assert ("Opt", "2", "true") in rows and ("Sol Ring", "1", "false") in rows and len(rows) >= 50
        pdf = await b.http.get("/decks/42/export.pdf")
        assert pdf.status_code == 200 and pdf.headers["content-type"] == "application/pdf"
        assert pdf.content.startswith(b"%PDF-1.4") and pdf.content.rstrip().endswith(b"%%EOF")
        streams = re.findall(rb"stream\n(.*?)\nendstream", pdf.content, re.S)
        text = b"".join(zlib.decompress(x) for x in streams).decode("cp1252")
        assert "Sample Commander Deck" in text and "Aesi, Tyrant of Gyre Strait" in text and "Opt" in text
        assert b"/Type /Catalog" in pdf.content and b"/Count 2" in pdf.content  # 100 rows need two pages
        for path in ("/decks/43/export.arena.txt", "/decks/43/export.dek", "/decks/43/export.pdf"):
            r = await b.http.get(path)  # Amy's private deck: not readable, nothing leaks
            assert r.status_code == 404 and "Amy" not in r.text, path
    finally:
        await b.aclose()


async def test_another_persons_public_deck_can_be_cloned_but_not_edited(stack: Stack) -> None:
    """Clone deck is on every deck the member can read, as on archidekt.com (public decks and
    precons included); Edit deck stays the owner's. The copy lands in the member's own account."""
    b = await linked_browser(stack)
    try:
        stack.ark.private.discard(43)  # Amy's deck, public for this test
        page = await b.http.get("/decks/43", headers=NAV)
        assert page.status_code == 200 and "Clone deck" in page.text and "Edit deck" not in page.text
        csrf = await b.csrf("/account")
        before = set(stack.ark.decks)
        r = await b.http.post("/decks/43/clone", data={"csrf": csrf})
        assert r.status_code == 303 and "ok=created" in r.headers["location"], r.headers
        (new_id,) = set(stack.ark.decks) - before
        copy = stack.ark.decks[new_id]
        assert copy["owner"]["username"] == "alice" and copy["name"] == "Copy of - Amy's deck"
        assert copy["private"] is True and len(copy["cards"]) == len(stack.ark.decks[43]["cards"])
    finally:
        stack.ark.private.add(43)
        await b.aclose()
