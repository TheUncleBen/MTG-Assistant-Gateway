"""Release 0.6.4: the home dashboard with the full navigation, public deck search and user pages,
the owned-cards collection (pages, JSON API, tools, isolation), the deck page's toolbar forms,
the guide and the Android app layout."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from mcp import Client

from mtg_gateway.archidekt import ArchidektClient, Pacer
from mtg_gateway.mf_proxy import MysticForgeProxy
from mtg_gateway.scan.scryfall import ScryfallClient

from .conftest import FakeIdP, Harness, make_settings, running, sse_json
from .fake_archidekt import FakeArchidekt
from .fake_scryfall import FakeScryfall
from .test_decks_and_proxy import Browser, call, fake_mystic_forge, mcp_token, structured

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
APP_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 16) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.6.4"}


class Stack:
    def __init__(self, h: Harness, ark: FakeArchidekt, sf: FakeScryfall):
        self.h = h
        self.ark = ark
        self.sf = sf


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP):
    ark = FakeArchidekt()
    sf = FakeScryfall()
    settings = make_settings(tmp_path, writes_enabled=True, archidekt_base="https://ark.test/api")
    client = ArchidektClient(settings.archidekt_base, "test-agent", Pacer(0.0), http=ark.client())
    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(fake_mystic_forge()))
    async with running(Harness(settings, idp, archidekt=client, mf_proxy=proxy)) as h:
        h.app.state.gateway.scan.scryfall = ScryfallClient(
            "https://scryfall.test", min_interval=0.0, http=sf.client()
        )
        yield Stack(h, ark, sf)


async def linked(stack: Stack) -> Browser:
    b = Browser(stack.h)
    await b.login("/account")
    r = await b.link("alice", "pw-alice")
    assert r.status_code == 303, r.text
    return b


async def api(b: Browser, method: str, path: str, body: dict | None = None, *, csrf: str | None = None):
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
        headers["X-CSRF-Token"] = csrf if csrf is not None else await b.csrf("/collection")
    return await b.http.request(
        method, path, content=json.dumps(body) if body is not None else None, headers=headers
    )


# -- home -------------------------------------------------------------------------------------
async def test_home_shows_the_full_navigation_and_every_section(stack: Stack) -> None:
    b = await linked(stack)
    try:
        r = await b.http.get("/", headers=NAV)
        assert r.status_code == 200
        nav = r.text[r.text.index("<nav class='site'") : r.text.index("</nav>")]
        for href in ("/decks", "/search", "/collection", "/scan", "/proposals", "/history"):
            assert f"href='{href}'" in nav, href
        assert "class='tabbar'" in r.text and re.search(r"<body class='[^']*\bhome\b", r.text)
        assert "Sample Commander Deck" in r.text  # the newest decks with their covers
        assert "href='/guide'" in r.text
        assert "Connect an AI assistant" in r.text and "https://mtg.test/mcp" in r.text
        assert "Archidekt account <strong>alice</strong> is linked" in r.text
    finally:
        await b.aclose()


async def test_home_without_a_link_points_at_the_account_page(stack: Stack) -> None:
    b = Browser(stack.h)
    try:
        await b.login("/")
        r = await b.http.get("/", headers=NAV)
        assert r.status_code == 200 and "Link Archidekt" in r.text and "My decks" not in r.text
    finally:
        await b.aclose()


# -- deck page toolbar ------------------------------------------------------------------------
async def test_deck_toolbar_selects_sit_inside_their_own_form(stack: Stack) -> None:
    """Regression: the Quick add form used to be nested inside the view form, so the browser
    closed the outer form early and View as / Group by / Sort by submitted nothing."""
    b = await linked(stack)
    try:
        r = await b.http.get("/decks/42", headers=NAV)
        assert r.status_code == 200, r.text
        start = r.text.index("id='viewform'")
        start = r.text.rindex("<form", 0, start)
        end = r.text.index("</form>", start)
        viewform = r.text[start:end]
        assert "<form" not in viewform[5:]  # no nested form
        assert "name='view'" in viewform and "name='group'" in viewform and "name='sort'" in viewform
        quick = r.text[r.text.index("class='field add quick'") : start]
        assert "</form>" in quick  # the Quick add form closes before the view form opens
        # the banner art is painted by a ::before layer, so the banner itself no longer clips the
        # More menu (the old rule was .banner{overflow:hidden})
        banner_rule = ".banner{position:relative;margin:0 -1rem 1rem;color:#fff;isolation:isolate;z-index:2}"
        assert ".banner::before{" in r.text and banner_rule in r.text
        # stacks view groups the cards by category with drag-and-drop hooks on the owner's deck
        r = await b.http.get("/decks/42?view=stacks", headers=NAV)
        assert "class='deckview stacks'" in r.text and "data-own='1'" in r.text
        assert "data-group='Artifact'" in r.text and "data-card='Sol Ring'" in r.text
        assert "/static/deck.js" in r.text
    finally:
        await b.aclose()


# -- search and user pages --------------------------------------------------------------------
async def test_search_and_user_pages_are_gated_and_list_public_decks(stack: Stack) -> None:
    anon = stack.h.http
    for path in ("/search", "/users/alice", "/collection", "/guide"):
        r = await anon.get(path, headers=NAV)
        assert r.status_code == 302 and r.headers["location"].startswith("/login?next="), path
    b = Browser(stack.h)
    try:
        await b.login("/search")
        r = await b.http.get("/search", headers=NAV)
        assert r.status_code == 200 and "name='name'" in r.text and "name='commander'" in r.text
        r = await b.http.get("/search?q=Sample&order=-viewCount", headers=NAV)
        assert r.status_code == 200, r.text
        assert "Sample Commander Deck" in r.text and "Amy" not in r.text  # Amy's deck is private
        assert "by alice" in r.text  # the owner is shown on every result
        assert stack.ark.search_params[-1]["name"] == "Sample"
        assert stack.ark.search_params[-1]["orderBy"] == "-viewCount"
        r = await b.http.get("/search?commander=Aesi", headers=NAV)
        assert stack.ark.search_params[-1]["commanderName"] == "Aesi"
        assert stack.ark.search_params[-1]["deckFormat"] == "3"
        r = await b.http.get("/users/alice", headers=NAV)
        assert r.status_code == 200 and "Sample Commander Deck" in r.text
        r = await b.http.get("/users/amy", headers=NAV)
        assert r.status_code == 200 and "Amy's deck" not in r.text
        r = await b.http.get("/users/not%20a%20user!", headers=NAV)
        assert r.status_code in (200, 400) and "Amy's deck" not in r.text
    finally:
        await b.aclose()


async def test_search_tool_lists_public_decks_only(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    out = structured(await call(stack.h, token, "search_decks", {"name": "deck"}))
    assert out["ok"] is True, out
    names = [d["name"] for d in out["decks"]]
    assert "Sample Commander Deck" in names and "Amy's deck" not in names
    assert all("private" not in d or not d["private"] for d in out["decks"])


# -- collection ------------------------------------------------------------------------------
async def test_collection_page_add_step_remove_and_export(stack: Stack) -> None:
    """The collection is the linked account's Archidekt Collection: every change lands there (the
    fake's per-user records) and the deck page's green dot is Archidekt's own ``owned`` count."""
    b = await linked(stack)
    try:
        r = await b.http.get("/collection", headers=NAV)
        assert r.status_code == 200 and "No cards yet" in r.text
        csrf = await b.csrf("/collection")
        r = await b.http.post(
            "/collection",
            data={"csrf": csrf, "action": "add", "name": "Sol Ring", "quantity": "2", "finish": "nonfoil"},
        )
        assert r.status_code == 303, r.text
        assert [r["quantity"] for r in stack.ark.collections["alice"].values()] == [2]
        r = await b.http.get("/collection", headers=NAV)
        assert "Sol Ring" in r.text and "<b>1</b> entry in your Archidekt collection" in r.text
        rid = re.search(r"data-id='([0-9]+)'", r.text).group(1)
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "inc", "id": rid})
        assert r.status_code == 303
        assert stack.ark.collections["alice"][int(rid)]["quantity"] == 3
        # adding the same printing again tops up the existing record instead of duplicating it
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "add", "name": "Sol Ring", "quantity": "1"}
        )
        assert r.status_code == 303 and len(stack.ark.collections["alice"]) == 1
        assert stack.ark.collections["alice"][int(rid)]["quantity"] == 4
        # the deck page marks owned cards with Archidekt's count for the signed-in member
        r = await b.http.get("/decks/42", headers=NAV)
        assert "class='owned'" in r.text and "You own 4" in r.text
        csv = await b.http.get("/collection/export.csv")
        assert (
            csv.status_code == 200
            and "Sol Ring" in csv.text
            and csv.headers["content-type"].startswith("text/csv")
        )
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "remove", "id": rid})
        assert r.status_code == 303 and stack.ark.collections["alice"] == {}
        r = await b.http.get("/collection", headers=NAV)
        assert "Sol Ring" not in r.text
        # a stale or missing CSRF token adds nothing and sends the member back with a notice
        r = await b.http.post("/collection", data={"csrf": "nope", "action": "add", "name": "Sol Ring"})
        assert r.status_code == 303 and r.headers["location"] == "/collection?err=expired"
        assert stack.ark.collections["alice"] == {}
        # nothing about the cards is kept on the gateway
        tables = [
            row[0]
            for row in stack.h.app.state.gateway.db._conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        ]
        assert not any("collection" in t for t in tables), tables
    finally:
        await b.aclose()


async def test_collection_json_api_and_cross_member_isolation(stack: Stack) -> None:
    alice = await linked(stack)
    bob = Browser(stack.h)
    try:
        r = await api(
            alice,
            "POST",
            "/collection/api/add",
            {"items": [{"name": "Sol Ring", "quantity": 1, "foil": True}], "source": "scan"},
        )
        assert r.status_code == 200, r.text
        out = r.json()
        # the finish asked for is kept (Archidekt's record "modifier" is Foil); a printing that
        # Archidekt lists in one finish only would be stored in that finish instead
        assert out["ok"] and len(out["added"]) == 1 and out["added"][0]["finish"] == "foil"
        assert stack.ark.collections["alice"][out["added"][0]["id"]]["modifier"] == "Foil"
        rid = out["added"][0]["id"]
        r = await api(alice, "POST", f"/collection/api/rows/{rid}", {"quantity": 4})
        assert r.status_code == 200 and r.json()["row"]["quantity"] == 4
        assert stack.ark.collections["alice"][rid]["quantity"] == 4
        # no CSRF header: refused
        r = await alice.http.post(
            f"/collection/api/rows/{rid}",
            content=json.dumps({"quantity": 9}),
            headers={"Content-Type": "application/json"},
        )
        assert r.status_code == 403
        # another member without a linked account is told to link one, and sees nothing
        stack.h.idp.user = {
            **stack.h.idp.user,
            "sub": "bob-sub",
            "preferred_username": "bob",
            "email": "bob@x.test",
        }
        await bob.login("/collection")
        r = await bob.http.get("/collection", headers=NAV)
        assert r.status_code == 200 and "Sol Ring" not in r.text and "Link Archidekt" in r.text
        r = await api(bob, "POST", f"/collection/api/rows/{rid}", {"quantity": 1})
        assert r.status_code == 409 and r.json()["error"] == "not_linked", r.text
        # linked to a different Archidekt account, bob still cannot reach alice's record
        r = await bob.link("amy", "pw-amy")
        assert r.status_code == 303, r.text
        r = await api(bob, "POST", f"/collection/api/rows/{rid}", {"quantity": 1})
        assert r.status_code == 404, r.text
        r = await api(bob, "DELETE", f"/collection/api/rows/{rid}", {})
        assert r.status_code == 404
        assert stack.ark.collections["alice"][rid]["quantity"] == 4
        r = await bob.http.get("/collection/api/summary")
        assert r.json()["rows"] == 0
        r = await alice.http.get("/collection/api/summary")
        assert r.json()["rows"] == 1 and r.json()["cards"] == 4
        # anonymous: 401 with a login hint, never data
        r = await stack.h.http.get("/collection/api/summary")
        assert r.status_code == 401 and r.json()["error"] == "unauthenticated"
    finally:
        await alice.aclose()
        await bob.aclose()


async def test_collection_tools(stack: Stack) -> None:
    b = await linked(stack)  # the tools work on the member's linked Archidekt account
    try:
        token = await mcp_token(stack.h)
        out = structured(
            await call(stack.h, token, "add_to_collection", {"cards": [{"name": "Sol Ring", "quantity": 2}]})
        )
        assert out["ok"] is True and out["added"][0]["quantity"] == 2, out
        out = structured(await call(stack.h, token, "list_collection", {}))
        assert out["ok"] is True and out["count"] == 1 and out["cards"][0]["name"] == "Sol Ring"
        out = structured(
            await call(stack.h, token, "remove_from_collection", {"name": "Sol Ring", "quantity": 1})
        )
        assert out["ok"] is True and out["removed"][0]["quantity"] == 2
        assert [r["quantity"] for r in stack.ark.collections["alice"].values()] == [1]
        # no tool exists for social actions (likes, follows, comments are a person's own clicks)
        listing = sse_json(await stack.h.mcp(token, "tools/list"))
        names = {t["name"] for t in listing["result"]["tools"]}
        assert {"list_collection", "add_to_collection", "remove_from_collection"} <= names
        social = ("vote", "like", "follow", "comment", "bookmark")
        assert not any(w in n for n in names for w in social), names
    finally:
        await b.aclose()


# -- guide and app layout ---------------------------------------------------------------------
async def test_guide_is_end_user_documentation(stack: Stack) -> None:
    b = Browser(stack.h)
    try:
        await b.login("/guide")
        r = await b.http.get("/guide", headers=NAV)
        assert r.status_code == 200
        for word in (
            "Your decks",
            "Finding decks",
            "Scanning cards",
            "Your collection",
            "Proposals and history",
            "With an AI assistant",
            "The Android app",
        ):
            assert word in r.text, word
        for banned in ("docker", "Docker", "MTG_", "Portainer", "compose"):
            assert banned not in r.text, banned
        menu = r.text[r.text.index("<nav class='user'") : r.text.index("</header>")]
        assert "href='/guide'" in menu  # the Guide is reachable from the account menu
    finally:
        await b.aclose()


async def test_android_app_gets_the_app_layout(stack: Stack) -> None:
    b = Browser(stack.h)
    try:
        await b.login("/")
        web = await b.http.get("/decks", headers=NAV)
        assert "<footer class='site'>" in web.text and "class='app" not in web.text
        assert "data-native" not in web.text
        app = await b.http.get("/decks", headers={**NAV, **APP_UA})
        assert app.status_code == 200
        assert "<footer class='site'>" not in app.text
        assert re.search(r"<body class='[^']*\bapp\b", app.text)
        for action in ("openCamera", "reload", "openInBrowser", "changeGateway"):
            assert f"data-native='{action}'" in app.text, action
        assert (
            "href='/app'" not in app.text[app.text.index("<nav class='user'") : app.text.index("</header>")]
        )
    finally:
        await b.aclose()
