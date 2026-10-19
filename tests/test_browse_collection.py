"""Release 0.6.4: the home dashboard with the full navigation, public deck search and user pages,
the owned-cards collection (pages, JSON API, tools, isolation), the deck page's toolbar forms,
the guide and the Android app layout."""

from __future__ import annotations

import json
import re

import pytest

from .conftest import COLLECTION, Stack, sse_json, stack_with
from .test_decks_and_proxy import Browser, call, mcp_token, structured

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}
APP_UA = {"User-Agent": "Mozilla/5.0 (Linux; Android 16) Chrome/140 Mobile Safari/537.36 MTGAssistant/0.6.4"}


stack_options = stack_with(COLLECTION)


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
        # Deck covers come from Scryfall, so the home page carries the deck pages' CSP (the e2e
        # suite checks the deployed header against the same string).
        assert r.headers["content-security-policy"] == (
            "default-src 'none'; style-src 'self' 'unsafe-inline'; script-src 'self'; manifest-src 'self'; "
            "connect-src 'self'; img-src 'self' https://cards.scryfall.io; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
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
        css = (await b.http.get(re.search(r"href='(/static/deck\.css\?v=[0-9a-f]{12})'", r.text)[1])).text
        assert ".banner::before{" in css and banner_rule in css
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
        assert stack.ark.search_params[-2]["commanderName"] == "Aesi"
        assert stack.ark.search_params[-2]["deckFormat"] == "3"
        # Archidekt matches a commander only by its full name: the part is looked up and searched again
        assert stack.ark.search_params[-1]["commanderName"] == "Aesi, Tyrant of Gyre Strait"
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


async def test_collection_import_from_csv_and_plain_list(stack: Stack) -> None:
    """Import a list on the Collection page: the page's own Export CSV round-trips (same printings,
    finishes and counts), a CSV with Archidekt's column names and a plain card list work too,
    and an oversized or unreadable paste adds nothing and says why."""
    from mtg_gateway.collection import CollectionError, parse_collection_import

    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        page = await b.http.get("/collection", headers=NAV)
        assert "Import a list" in page.text and "importfile" in page.text and "filepick.js" in page.text
        plain = "2 Sol Ring (CMR) 436\n1 Opt *F*\n"
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": plain})
        assert r.status_code == 303, r.headers
        assert re.fullmatch(r"/collection\?ok=imported&r=[\w-]+", r.headers["location"]), r.headers
        rows = {
            (c["card"]["oracleCard"]["name"], c["modifier"]): c["quantity"]
            for c in stack.ark.collections["alice"].values()
        }
        assert rows == {("Sol Ring", "Normal"): 2, ("Opt", "Foil"): 1}, rows
        exported = (await b.http.get("/collection/export.csv")).text
        assert exported.splitlines()[0].startswith("Quantity,Name,Finish,Condition")
        # the export imports back: the same printings in the same finishes get their copies topped up
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": exported})
        assert r.status_code == 303 and "ok=imported" in r.headers["location"]
        rows = {
            (c["card"]["oracleCard"]["name"], c["modifier"]): c["quantity"]
            for c in stack.ark.collections["alice"].values()
        }
        assert rows == {("Sol Ring", "Normal"): 4, ("Opt", "Foil"): 2}, rows
        # Archidekt's own column names, extra columns ignored, header order free
        arch = "Name,Set Code,Collector Number,Quantity,Foil,Condition,Price\nSol Ring,cmr,436,3,,NM,1.20\n"
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": arch})
        assert "ok=imported" in r.headers["location"]
        assert [
            c["quantity"]
            for c in stack.ark.collections["alice"].values()
            if c["card"]["oracleCard"]["name"] == "Sol Ring"
        ] == [7]
        # refusals: too many rows, nothing readable
        many = "\n".join(f"1 Card {i}" for i in range(101))
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": many})
        assert r.headers["location"].startswith("/collection?err=toomany&r=")
        shown = await b.http.get(r.headers["location"], headers=NAV)
        assert "Nothing was imported: that is 101 rows; an import adds at most 100 at a time" in shown.text
        r = await b.http.post("/collection", data={"csrf": csrf, "action": "import", "text": "   "})
        assert r.headers["location"].startswith("/collection?err=unreadable&r=")
        shown = await b.http.get(r.headers["location"], headers=NAV)
        assert "Nothing was imported: paste or choose a CSV or a card list first." in shown.text
        assert sum(c["quantity"] for c in stack.ark.collections["alice"].values()) == 9
        items = parse_collection_import("Quantity,Name,Finish\n1,Sol Ring,Etched\n")
        assert items[0]["finish"] == "etched" and items[0]["quantity"] == 1
        for bad in (
            "Quantity,Name\n²,Sol Ring\n",
            "Quantity,Name\n٣,Sol Ring\n",
            "Quantity,Name\n1.5,Sol Ring\n",
        ):
            with pytest.raises(CollectionError):  # a count that is not a plain number is refused, not a crash
                parse_collection_import(bad)
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
    """The assistant reads the collection freely; changing it is a proposal (kind ``collection``)
    that waits for the member like a deck edit, and applies through the same gate."""
    b = await linked(stack)
    try:
        token = await mcp_token(stack.h)
        p = structured(
            await call(
                stack.h,
                token,
                "propose_collection_changes",
                {"add": [{"name": "Sol Ring", "quantity": 2}, "Arcane Signet"]},
            )
        )
        assert p["ok"] is True and p["kind"] == "collection" and p["state"] == "pending", p
        assert p["risk"] == "low" and p["assistant_may_apply"] is False  # manual mode: the member applies
        assert "+2 Sol Ring" in p["diff"] and "+1 Arcane Signet" in p["diff"]
        assert stack.ark.collections.get("alice", {}) == {}  # nothing sent yet
        # the review page names the target, and the member's Apply writes to Archidekt
        csrf = await b.csrf("/collection")
        review = await b.http.get(f"/proposals/{p['proposal_id']}", headers=NAV)
        assert review.status_code == 200 and "Your Archidekt collection" in review.text
        applied = await b.http.post(
            f"/api/v1/proposals/{p['proposal_id']}/apply",
            headers={"X-CSRF-Token": csrf},
            json={"version": p["version"]},
        )
        assert applied.status_code == 200, applied.text
        a = applied.json()
        assert a.get("state") == "applied", a
        assert [r["quantity"] for r in a["result"]["added"]] == [2, 1], a
        changed = [(r["previous_quantity"], r["added_quantity"]) for r in a["result"]["added"]]
        assert changed == [(0, 2), (0, 1)], a
        out = structured(await call(stack.h, token, "list_collection", {}))
        assert out["ok"] is True and out["count"] == 2
        # removing is a proposal too, resolved against the collection so the diff names the card
        rid = next(r["id"] for r in out["cards"] if r["name"] == "Sol Ring")
        p2 = structured(
            await call(stack.h, token, "propose_collection_changes", {"remove": [{"id": rid, "quantity": 1}]})
        )
        assert p2["ok"] is True and "-1 Sol Ring" in p2["diff"], p2
        a2 = await b.http.post(
            f"/api/v1/proposals/{p2['proposal_id']}/apply",
            headers={"X-CSRF-Token": csrf},
            json={"version": p2["version"]},
        )
        assert a2.status_code == 200, a2.text
        # the result says what the removal took and what is left, not only the record's old count
        gone = a2.json()["result"]["removed"][0]
        assert (gone["previous_quantity"], gone["removed_quantity"], gone["quantity"]) == (2, 1, 1), gone
        assert sorted(r["quantity"] for r in stack.ark.collections["alice"].values()) == [1, 1]
        # the assistant cannot apply it itself in manual mode
        p3 = structured(await call(stack.h, token, "propose_collection_changes", {"add": ["Sol Ring"]}))
        refused = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p3["proposal_id"]}))
        assert refused["ok"] is False and refused["error"] == "browser_required"
        a3 = await b.http.post(
            f"/api/v1/proposals/{p3['proposal_id']}/apply",
            headers={"X-CSRF-Token": csrf},
            json={"version": p3["version"]},
        )
        grown = a3.json()["result"]["added"][0]  # one more copy on the record that holds one
        assert (grown["previous_quantity"], grown["added_quantity"], grown["quantity"]) == (1, 1, 2), grown
        # social actions only exist as proposals the member approves each time,
        # and no tool writes the collection directly
        listing = sse_json(await stack.h.mcp(token, "tools/list"))
        names = {t["name"] for t in listing["result"]["tools"]}
        assert {"list_collection", "propose_collection_changes"} <= names
        assert not {"add_to_collection", "remove_from_collection"} & names
        social = ("vote", "like", "follow", "comment", "bookmark")
        acting = {n for n in names for w in social if w in n}
        assert all(n.startswith("propose_") or n == "get_deck_comments" for n in acting), acting
    finally:
        await b.aclose()


async def test_read_only_token_cannot_propose_collection_changes(stack: Stack) -> None:
    from .test_round4_data import _app_token

    b = await linked(stack)
    try:
        token, _cid = await _app_token(stack.h, "reader", scope="mtg.read")
        out = structured(await call(stack.h, token, "propose_collection_changes", {"add": ["Sol Ring"]}))
        assert out["ok"] is False and out["error"] == "insufficient_scope", out
        assert stack.ark.collections.get("alice", {}) == {}
        listed = structured(await call(stack.h, token, "list_collection", {}))
        assert listed["ok"] is True  # reading stays open to a read-only app
    finally:
        await b.aclose()


# -- guide and app layout ---------------------------------------------------------------------
async def test_guide_is_end_user_documentation(stack: Stack) -> None:
    b = Browser(stack.h)
    try:
        await b.login("/guide")
        r = await b.http.get("/guide", headers=NAV)
        assert r.status_code == 200
        # the four Diátaxis parts, in order, each with its own sections nested under it
        parts = [r.text.index(f"id='{pid}'") for pid in ("tutorials", "howto", "reference", "explanation")]
        assert parts == sorted(parts)
        for word in (
            "Tutorials",
            "How-to guides",
            "Reference",
            "Explanation",
            "Link Archidekt",
            "Your first deck edit",
            "Add or change cards",
            "Use your collection",
            "Scan a photo of your cards",
            "What the assistant can do, by tool",
            "Approval modes and risk levels",
            "Keyboard shortcuts",
            "Approvals, snapshots and backups",
            "Privacy and what is stored",
            "Use the Android app",
        ):
            assert word in r.text, word
        assert "<nav aria-label='Guide contents'>" in r.text and "src='/static/guide.js?v=" in r.text
        js = await b.http.get("/static/guide.js")
        assert js.status_code == 200 and "guide-q" in js.text
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
        # These two stay in the app's own menu and the Account page's App card only
        for action in ("openCopiedLink", "checkForUpdates"):
            assert f"data-native='{action}'" not in app.text, action
        assert (
            "href='/install?for=android'"
            not in app.text[app.text.index("<nav class='user'") : app.text.index("</header>")]
        )
    finally:
        await b.aclose()


async def test_account_page_in_the_app_has_an_app_card(stack: Stack) -> None:
    """Inside the app the Account page shows the installed version and the update
    check, not "Get the Android app"; in a browser it stays as it was."""
    b = Browser(stack.h)
    try:
        await b.login("/account")
        web = await b.http.get("/account", headers=NAV)
        assert web.status_code == 200
        assert "Get the Android app" in web.text and "href='/install?for=android'" in web.text
        assert "<h2>App</h2>" not in web.text and "data-native" not in web.text

        app = await b.http.get("/account", headers={**NAV, **APP_UA})
        assert app.status_code == 200
        main = app.text[app.text.index("</header>") :]
        assert "Get the Android app" not in main and "href='/install?for=android'" not in main
        card = main[main.index("<div class='card' id='app'>") :]
        card = card[: card.index("</div></div>") + 12]
        assert "<h2>App</h2>" in card
        assert "<dt>Installed version</dt><dd>0.6.4</dd>" in card
        assert "data-native='checkForUpdates'" in card and "data-native='openCopiedLink'" in card

        # A user agent with the mark but no readable version still gets the card, marked unknown,
        # and nothing from the user agent reaches the page.
        odd = {"User-Agent": "Mozilla/5.0 MTGAssistant/<b>x</b>"}
        r = await b.http.get("/account", headers={**NAV, **odd})
        assert "<dt>Installed version</dt><dd>unknown</dd>" in r.text and "<b>x</b>" not in r.text
        r = await b.http.get("/account", headers={**NAV, "User-Agent": "MTGAssistant/0.7.22.9"})
        assert "<dd>unknown</dd>" in r.text
    finally:
        await b.aclose()
