"""Every object a member can name (proposals, snapshots, reports, scan sessions, decks) is checked
against the caller on the server, over MCP, the JSON API and the web pages (OWASP API1/API5)."""

from __future__ import annotations

from pathlib import Path

from .conftest import FakeIdP, Harness, gw, idp, make_settings, running  # noqa: F401
from .test_decks_and_proxy import Browser, call, mcp_token, stack, structured  # noqa: F401

AMY = {
    "sub": "user-2",
    "email": "amy@example.test",
    "name": "Amy",
    "preferred_username": "amy",
    "groups": ["mtg-users"],
}
NAV = {"Accept": "text/html"}


async def browser_for(h: Harness, user: dict | None, ark_user: str, pw: str) -> Browser:
    if user:
        h.idp.user = user
    b = Browser(h)
    await b.login()
    r = await b.link(ark_user, pw)
    assert r.status_code == 303, r.text
    return b


async def test_cross_user_objects(stack) -> None:  # noqa: F811
    h = stack.h
    alice_user = dict(h.idp.user)
    # --- Alice makes one of everything --------------------------------------
    ba = await browser_for(h, None, "alice", "pw-alice")
    ta = await mcp_token(h)
    p1 = structured(
        await call(
            h,
            ta,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "card_name": "Sol Ring"}]},
        )
    )
    assert p1["ok"], p1
    applied = structured(await call(h, ta, "apply_proposal", {"proposal_id": p1["proposal_id"]}))
    assert applied["ok"], applied
    snap_id = applied["snapshot_id"]
    p2 = structured(
        await call(
            h,
            ta,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "remove", "card_name": "Sol Ring"}]},
        )
    )
    pid = p2["proposal_id"]
    rep = await h.http.post(
        "/api/v1/reports", json={"deck_id": "42", "games": 50}, headers={"Authorization": f"Bearer {ta}"}
    )
    assert rep.status_code == 201, rep.text
    rid = rep.json()["report_id"]
    csrf_a = await ba.csrf("/account")
    sc = await ba.http.post(
        "/scan/api/sessions", json={"name": "alices box", "items": []}, headers={"X-CSRF-Token": csrf_a}
    )
    assert sc.status_code == 201, sc.text
    scan_id = sc.json()["id"]

    # --- Amy signs in, links her own Archidekt -----------------------------
    bb = await browser_for(h, AMY, "amy", "pw-amy")
    tb = await mcp_token(h)
    auth_b = {"Authorization": f"Bearer {tb}"}
    csrf_b = await bb.csrf("/account")

    # MCP tools
    for name, args in [
        ("get_proposal", {"proposal_id": pid}),
        ("apply_proposal", {"proposal_id": pid}),
        ("reject_proposal", {"proposal_id": pid}),
        ("get_snapshot", {"snapshot_id": snap_id}),
        ("propose_restore_snapshot", {"snapshot_id": snap_id}),
        ("get_deck_report", {"report_id": rid}),
        ("get_scan_session", {"session": scan_id}),
        ("get_scan_session", {"session": "alices box"}),
        ("deck_stats", {"deck_ref": snap_id}),
        ("propose_new_deck", {"name": "x", "scan_session": scan_id}),
        ("propose_deck_changes", {"deck_id": "42", "changes": [{"action": "add", "card_name": "Opt"}]}),
        ("propose_deck_details", {"deck_id": "42", "details": {"name": "pwned"}}),
    ]:
        out = await call(h, tb, name, args)
        sc_ = out.get("structuredContent") or {}
        assert out.get("isError") or sc_.get("ok") is False, (name, out)
    # Alice's deck 42 is public, so Bob may clone it, as on archidekt.com: the proposal is his, the
    # copy would land in his own account, and Alice's deck is only read.
    clone = structured(await call(h, tb, "propose_clone_deck", {"deck_id": "42"}))
    assert clone["ok"] and clone["kind"] == "clone" and clone["deck_id"] == "42", clone
    assert structured(await call(h, tb, "reject_proposal", {"proposal_id": clone["proposal_id"]}))["ok"]
    cmp_ = structured(await call(h, tb, "compare_decks", {"a": snap_id, "b": "42"}))
    assert "a" not in cmp_  # snapshot id is not resolved (ids lack the snap_ prefix): parsed as decklist text
    mine = structured(await call(h, tb, "list_my_proposals"))["proposals"]
    assert [p["kind"] for p in mine] == ["clone"] and mine[0]["state"] == "rejected", mine
    assert structured(await call(h, tb, "list_snapshots"))["snapshots"] == []
    assert structured(await call(h, tb, "list_deck_reports"))["reports"] == []
    assert structured(await call(h, tb, "list_scan_sessions"))["sessions"] == []

    # JSON API (bearer and cookie)
    for method, url, kw in [
        ("GET", f"/api/v1/proposals/{pid}", {}),
        ("POST", f"/api/v1/proposals/{pid}/apply", {}),
        ("POST", f"/api/v1/proposals/{pid}/reject", {}),
        ("GET", f"/api/v1/snapshots/{snap_id}", {}),
        ("GET", f"/api/v1/reports/{rid}", {}),
        ("DELETE", f"/api/v1/reports/{rid}", {}),
        ("GET", f"/api/v1/compare?a={snap_id}&b=42", {}),
        ("POST", "/api/v1/proposals", {"json": {"kind": "restore", "snapshot_id": snap_id}}),
        (
            "POST",
            "/api/v1/proposals",
            {"json": {"kind": "details", "deck_id": "42", "details": {"name": "x"}}},
        ),
    ]:
        r = await h.http.request(method, url, headers=auth_b, **kw)
        assert r.status_code in (403, 404), (method, url, r.status_code, r.text)
        r = await bb.http.request(method, url, headers={"X-CSRF-Token": csrf_b}, **kw)
        assert r.status_code in (403, 404), ("cookie", method, url, r.status_code, r.text)
    hist = (await h.http.get("/api/v1/decks/42/history", headers=auth_b)).json()
    # Bob's history of deck 42 holds only his own rejected clone proposal, nothing of Alice's
    assert [p["kind"] for p in hist["proposals"]] == ["clone"]
    assert hist["snapshots"] == [] and hist["reports"] == []
    acts = (await h.http.get("/api/v1/activity", headers=auth_b)).json()["events"]
    assert all(e.get("sub") in (None, "user-2") for e in acts)

    # Browser pages
    r = await bb.http.get(f"/proposals/{pid}", headers=NAV)
    assert r.status_code == 404
    r = await bb.http.post(f"/proposals/{pid}", data={"csrf": csrf_b, "action": "reject"})
    assert r.status_code in (303, 404), r.text
    r = await bb.http.get(f"/history/reports/{rid}", headers=NAV)
    assert r.status_code == 404
    r = await bb.http.post("/history/restore", data={"csrf": csrf_b, "snapshot_id": snap_id})
    assert r.status_code == 400, r.text
    r = await bb.http.get(f"/decks/42/edit?scan_session={scan_id}", headers=NAV)
    assert "alices box" not in r.text
    # Scan API
    r = await bb.http.get(f"/scan/api/sessions/{scan_id}")
    assert r.status_code == 404
    r = await bb.http.put(
        f"/scan/api/sessions/{scan_id}", json={"name": "amy was here"}, headers={"X-CSRF-Token": csrf_b}
    )
    assert r.status_code == 404
    r = await bb.http.delete(f"/scan/api/sessions/{scan_id}", headers={"X-CSRF-Token": csrf_b})
    assert r.status_code == 404

    # Alice's objects are untouched
    h.idp.user = alice_user
    p = structured(await call(h, ta, "get_proposal", {"proposal_id": pid}))
    assert p["state"] == "pending"
    own = await h.http.get(f"/api/v1/reports/{rid}", headers={"Authorization": f"Bearer {ta}"})
    assert own.status_code == 200
    r = await ba.http.get(f"/scan/api/sessions/{scan_id}")
    assert r.status_code == 200 and r.json()["name"] == "alices box"
    await ba.aclose()
    await bb.aclose()


async def test_private_deck_of_another_member_is_unreachable(stack) -> None:  # noqa: F811
    """Amy's deck 43 is private on Archidekt. Alice (linked as alice) must not read or edit it."""
    h = stack.h
    bb = await browser_for(h, AMY, "amy", "pw-amy")
    await bb.aclose()
    h.idp.user = {
        "sub": "user-1",
        "email": "alice@example.test",
        "name": "Alice",
        "preferred_username": "alice",
        "groups": ["mtg-users"],
    }
    ba = await browser_for(h, None, "alice", "pw-alice")
    ta = await mcp_token(h)
    for name, args in [
        ("get_deck", {"deck_ref": "43"}),
        ("deck_stats", {"deck_ref": "43"}),
        ("run_deck_report", {"deck_ref": "43", "games": 50}),
        ("propose_deck_changes", {"deck_id": "43", "changes": [{"action": "add", "card_name": "Opt"}]}),
        ("propose_deck_details", {"deck_id": "43", "details": {"private": False}}),
        ("propose_clone_deck", {"deck_id": "43"}),
    ]:
        out = await call(h, ta, name, args)
        sc_ = out.get("structuredContent") or {}
        assert out.get("isError") or sc_.get("ok") is False, (name, out)
    auth = {"Authorization": f"Bearer {ta}"}
    for url in ["/api/v1/decks/43", "/api/v1/decks/43/stats"]:
        assert (await h.http.get(url, headers=auth)).status_code in (403, 404)
    for url in [
        "/decks/43",
        "/decks/43/export.json",
        "/decks/43/export.txt",
        "/decks/43/export.csv",
        "/decks/43/settings",
        "/decks/43/edit",
    ]:
        r = await ba.http.get(url, headers=NAV)
        assert r.status_code in (400, 403, 404), (url, r.status_code)
        assert "Amy's deck" not in r.text
    await ba.aclose()


async def test_mcp_rechecks_required_group_every_request(tmp_path: Path, idp: FakeIdP) -> None:  # noqa: F811
    """Once the member's recorded groups lose MTG_REQUIRED_GROUP, /mcp refuses the access token at
    once, like the JSON API and the web pages, instead of serving it until it expires."""
    settings = make_settings(tmp_path, required_group="mtg-users")
    async with running(Harness(settings, idp)) as h:
        token = await mcp_token(h)
        with h.db.tx() as c:
            c.execute("UPDATE users SET groups_json = '[\"other\"]' WHERE sub = 'user-1'")
        r = await h.mcp(token, "tools/call", {"name": "whoami", "arguments": {}})
        assert r.status_code == 401, r.text
        api = await h.http.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
        assert api.status_code == 401


async def test_public_surface(gw: Harness) -> None:  # noqa: F811
    for path in ("/metrics", "/admin/metrics", "/api/v1/admin/metrics", "/api/v1/admin/users"):
        r = await gw.http.get(path)
        assert r.status_code in (302, 401, 404), (path, r.status_code)
        assert "user-1" not in r.text
    h = await gw.http.get("/healthz")
    assert set(h.json()) == {"status", "version", "mystic_forge"}  # states only, nothing about members
    # No CORS on the cookie API: a cross-site page can't read it.
    r = await gw.http.options(
        "/api/v1/me", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "GET"}
    )
    assert "access-control-allow-origin" not in r.headers
    r = await gw.http.get("/api/v1/me", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in r.headers


async def test_refresh_token_is_bound_to_its_client(gw: Harness) -> None:  # noqa: F811
    c1 = await gw.register()
    c2 = await gw.register()
    t1 = await gw.tokens_for(c1)
    r = await gw.token(c2, grant_type="refresh_token", refresh_token=t1["refresh_token"])
    assert r.status_code == 400
    # and the original still works (not burned by the foreign attempt)
    r = await gw.token(c1, grant_type="refresh_token", refresh_token=t1["refresh_token"])
    assert r.status_code == 200


async def test_non_admin_cannot_post_admin_actions(tmp_path: Path, idp: FakeIdP) -> None:  # noqa: F811
    settings = make_settings(tmp_path, admin_group="mtg-admins")
    async with running(Harness(settings, idp)) as h:
        b = Browser(h)
        await b.login()
        csrf = await b.csrf("/account")
        r = await b.http.post("/admin/users/user-1", data={"csrf": csrf, "action": "disable"})
        assert r.status_code == 404
        r = await b.http.post(
            "/api/v1/admin/users/user-1", json={"action": "disable"}, headers={"X-CSRF-Token": csrf}
        )
        assert r.status_code == 404
        assert h.db.get_user("user-1")["disabled_at"] is None
        # a bearer token gets no admin API either
        token = await mcp_token(h)
        r = await h.http.get("/api/v1/admin/users", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code in (401, 404)
        await b.aclose()
