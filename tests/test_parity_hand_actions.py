"""Release 0.7.2: the Archidekt parity items the owner approved. Hand actions on a member's own
deck (delete with a typed name, cover image, folders, tags), editing one's own comments, the
collection row details, maybeboard rows in the editor, the precon listing, and one owner per
capability among the assistant's tools."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import json
import re

from mtg_gateway.mf_proxy import ALLOWED_TOOLS, BLOCKED_TOOLS, OWNED_ELSEWHERE

from .test_browse_collection import NAV, Stack, api, linked, stack  # noqa: F401
from .test_decks_and_proxy import Browser, call, mcp_token, structured

HAND_ONLY = (
    "delete_deck",
    "set_cover",
    "move_deck",
    "create_folder",
    "rename_folder",
    "add_tag",
    "remove_tag",
)


async def _form(b: Browser, path: str, data: dict[str, str], *, follow: bool = False):
    csrf = await b.csrf("/decks")
    r = await b.http.post(path, data={"csrf": csrf, **data})
    if follow and r.status_code == 303:
        return await b.http.get(r.headers["location"], headers=NAV)
    return r


async def test_cover_tags_and_folder_from_the_settings_page(stack: Stack) -> None:
    b = await linked(stack)
    db = stack.h.app.state.gateway.db
    try:
        r = await b.http.get("/decks/42/settings", headers=NAV)
        assert r.status_code == 200
        for section in ("id='cover'", "id='tags'", "id='folder'", "id='delete'", "/decks/42/delete"):
            assert section in r.text, section
        # cover: a card of the deck; Archidekt gets its art URL, the gateway remembers the card
        uid = "be68e315-ffef-40a8-8a46-1c042b148c03"
        stack.ark.decks[42]["cards"][0]["card"]["uid"] = uid  # the fixture deck has no Scryfall ids
        before = len(db.list_snapshots("user-1"))
        r = await _form(b, "/decks/42/cover", {"card": uid}, follow=True)
        assert r.status_code == 200 and "Cover image saved" in r.text, r.text[:500]
        sent = stack.ark.updates[-1]
        assert sent["featured"].endswith(f"/{uid}.webp") and sent["customFeatured"] == ""
        assert len(db.list_snapshots("user-1")) == before + 1
        assert db.deck_covers(["42"])["42"]["scryfall_uid"] == uid
        page = await b.http.get("/decks/42", headers=NAV)
        assert f"{uid[0]}/{uid[1]}/{uid}.jpg" in page.text  # the banner shows the chosen art (from Scryfall)
        # a card that is not in the deck is refused; automatic hands the choice back to Archidekt
        r = await _form(b, "/decks/42/cover", {"card": "00000000-0000-4000-8000-000000000001"})
        assert r.status_code == 400 and "Pick a card that is in this deck" in r.text
        r = await _form(b, "/decks/42/cover", {"card": ""}, follow=True)
        assert "picks the cover image again" in r.text
        assert stack.ark.updates[-1] == {"deck_id": 42, "customFeatured": ""}
        # tags: an existing Archidekt tag is reused, a new one created, duplicates refused
        r = await _form(b, "/decks/42/tags", {"action": "add", "name": "Budget"}, follow=True)
        assert "Tag added" in r.text and "Budget" not in r.text.split("Deck tags")[0]
        rels = stack.ark.deck_tags[42]
        assert [(x["tag"], x["name"]) for x in rels] == [(1, "budget")]
        r = await _form(b, "/decks/42/tags", {"action": "add", "name": "sea monsters"}, follow=True)
        assert [x["name"] for x in stack.ark.deck_tags[42]] == ["budget", "sea monsters"]
        assert "sea monsters" in stack.ark.tags.values()
        r = await _form(b, "/decks/42/tags", {"action": "add", "name": "budget"})
        assert r.status_code == 400 and "already has the tag" in r.text
        r = await _form(
            b, "/decks/42/tags", {"action": "remove", "relation_id": str(rels[0]["id"])}, follow=True
        )
        assert "Tag removed" in r.text and [x["name"] for x in stack.ark.deck_tags[42]] == ["sea monsters"]
        # a relation of someone else's deck cannot be removed through this deck
        stack.ark.deck_tags[43] = [{"id": 777, "tag": 2, "deck": 43, "name": "tribal", "position": "M-1"}]
        r = await _form(b, "/decks/42/tags", {"action": "remove", "relation_id": "777"})
        assert r.status_code == 400 and stack.ark.deck_tags[43]
        # folders: create, rename, move the deck, back to the top level
        r = await _form(b, "/folders", {"action": "create", "name": "Cube"}, follow=True)
        assert "Folder created" in r.text and "Cube" in r.text
        folder = stack.ark.folders["alice"][-1]
        r = await _form(b, "/folders", {"action": "create", "name": "cube"})
        assert r.status_code == 400 and "already a folder" in r.text
        r = await _form(
            b,
            "/folders",
            {"action": "rename", "folder_id": str(folder["id"]), "name": "Cubes"},
            follow=True,
        )
        assert "Folder renamed" in r.text and folder["name"] == "Cubes"
        r = await _form(b, "/decks/42/move", {"folder_id": str(folder["id"])}, follow=True)
        assert "Deck moved" in r.text and stack.ark.deck_folder[42] == folder["id"]
        assert stack.ark.mass_updates[-1]["items"][0]["type"] == "deck"
        root = 1000 + 77
        r = await _form(b, "/decks/42/move", {"folder_id": str(root)}, follow=True)
        assert stack.ark.deck_folder[42] == root
        r = await _form(b, "/decks/42/move", {"folder_id": "999999"})
        assert r.status_code == 400 and "not in your Archidekt account" in r.text
    finally:
        await b.aclose()


async def test_delete_needs_the_typed_name_and_keeps_a_snapshot_and_backup(stack: Stack) -> None:
    b = await linked(stack)
    db = stack.h.app.state.gateway.db
    try:
        r = await b.http.get("/decks/42/delete", headers=NAV)
        assert r.status_code == 200 and "Type the deck's name" in r.text
        r = await _form(b, "/decks/42/delete", {"name": "Sample Commander"})
        assert r.status_code == 400 and "does not match" in r.text and 42 in stack.ark.decks
        r = await _form(b, "/decks/42/delete", {"name": " sample commander deck "})
        assert r.status_code == 303 and r.headers["location"] == "/decks?ok=deleted"
        assert stack.ark.deleted == [42] and 42 not in stack.ark.decks
        snaps = [s for s in db.list_snapshots("user-1") if s["deck_id"] == "42"]
        assert snaps and snaps[0]["proposal_id"] is None and snaps[0]["backup_deck_id"]
        copy = stack.ark.decks[int(snaps[0]["backup_deck_id"])]
        assert copy["name"].startswith("Sample Commander Deck (backup") and len(copy["cards"]) > 50
        r = await b.http.get("/decks?ok=deleted", headers=NAV)
        assert "Deck deleted on Archidekt" in r.text
        # someone else's deck cannot be deleted
        stack.ark.private.discard(43)
        r = await _form(b, "/decks/43/delete", {"name": "Amy's deck"})
        assert r.status_code == 403 and 43 in stack.ark.decks
        audit = db._conn.execute(
            "SELECT event, detail_json FROM audit_log WHERE event = 'deck_deleted'"
        ).fetchall()
        assert len(audit) == 1 and json.loads(audit[0]["detail_json"])["origin"] == "browser"
    finally:
        await b.aclose()


async def test_hand_actions_are_not_assistant_tools(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    r = await stack.h.mcp(token, "tools/list", {}, rid=3)
    names = {t["name"] for t in structured_tools(r)}
    assert not names & set(HAND_ONLY), names & set(HAND_ONLY)
    assert not names & set(OWNED_ELSEWHERE), names & set(OWNED_ELSEWHERE)


def structured_tools(r) -> list[dict]:
    from .conftest import sse_json

    return sse_json(r)["result"]["tools"]


def test_one_owner_per_capability() -> None:
    """The member's rule: no two assistant tools offer the same capability. The duplicates
    Mystic Forge has are hidden and point at the gateway tool that owns the job."""
    assert not set(OWNED_ELSEWHERE) & ALLOWED_TOOLS
    assert set(OWNED_ELSEWHERE) <= BLOCKED_TOOLS
    assert set(OWNED_ELSEWHERE) == {
        "goldfish_run",
        "goldfish_ab",
        "archidekt_deck",
        "archidekt_export",
        "archidekt_user_decks",
        "validate_archidekt_deck",
        "precon_diff",
    }
    owners = set(OWNED_ELSEWHERE.values())
    assert owners == {"run_deck_report", "get_deck", "list_my_decks", "deck_stats", "compare_decks"}
    assert not owners & ALLOWED_TOOLS  # every owner is a gateway tool, never another proxied one


async def test_hidden_duplicate_names_its_owner_and_reports_still_simulate(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    res = await call(stack.h, token, "goldfish_run", {"deck": "1 Island", "n": 10})
    assert res.get("isError") and "run_deck_report" in json.dumps(res), res
    res = await call(stack.h, token, "archidekt_deck", {"deck": "42"})
    assert res.get("isError") and "get_deck" in json.dumps(res), res
    # the gateway's own report still runs the goldfish games through the proxy
    r = await call(stack.h, token, "run_deck_report", {"deck_ref": "42", "games": 10})
    out = structured(r)
    assert out["ok"], out
    assert ("POST", "/api/decks/42/") not in stack.ark.calls  # nothing was written


async def test_own_comments_can_be_edited_and_deleted_others_not(stack: Stack) -> None:
    alice = await linked(stack)
    bob = Browser(stack.h)
    try:
        r = await api(
            alice,
            "POST",
            "/social/api/decks/42/comments",
            {"text": "first thoughts"},
            csrf=await alice.csrf("/decks"),
        )
        assert r.status_code == 201, r.text
        cid = r.json()["comment"]["id"]
        thread = (await alice.http.get("/social/api/decks/42/comments")).json()
        assert thread["me"] == 77 and thread["comments"][0]["owner"]["id"] == 77
        r = await api(
            alice,
            "PATCH",
            f"/social/api/decks/42/comments/{cid}",
            {"text": "second thoughts"},
            csrf=await alice.csrf("/decks"),
        )
        assert r.status_code == 200 and r.json()["comment"]["text"] == "second thoughts", r.text
        assert stack.ark.comments[300042][0]["text"] == "second thoughts"
        # another member, linked to another Archidekt account, cannot touch alice's comment
        stack.h.idp.user = {
            **stack.h.idp.user,
            "sub": "bob-sub",
            "preferred_username": "bob",
            "email": "b@x.test",
        }
        await bob.login("/account")
        assert (await bob.link("amy", "pw-amy")).status_code == 303
        r = await api(
            bob,
            "PATCH",
            f"/social/api/decks/42/comments/{cid}",
            {"text": "hijack"},
            csrf=await bob.csrf("/decks"),
        )
        assert r.status_code == 403, r.text
        r = await api(
            bob, "DELETE", f"/social/api/decks/42/comments/{cid}", {}, csrf=await bob.csrf("/decks")
        )
        assert r.status_code == 403 and stack.ark.comments[300042][0]["text"] == "second thoughts"
        r = await api(
            alice, "DELETE", f"/social/api/decks/42/comments/{cid}", {}, csrf=await alice.csrf("/decks")
        )
        assert r.status_code == 200 and r.json()["deleted"] == cid and stack.ark.comments[300042] == []
        # the deck page script has the buttons and the thread load carries "me"
        js = (await alice.http.get("/static/deck.js")).text
        assert '"Edit"' in js and '"Delete"' in js and "d.me" in js
    finally:
        await alice.aclose()
        await bob.aclose()


async def test_collection_row_details_save_condition_language_and_price(stack: Stack) -> None:
    b = await linked(stack)
    try:
        csrf = await b.csrf("/collection")
        r = await b.http.post(
            "/collection", data={"csrf": csrf, "action": "add", "name": "Sol Ring", "quantity": "1"}
        )
        assert r.status_code == 303
        rid = next(iter(stack.ark.collections["alice"]))
        page = await b.http.get("/collection", headers=NAV)
        assert "class='menu detailsform'" in page.text and "name='purchase_price'" in page.text
        r = await b.http.post(
            "/collection",
            data={
                "csrf": csrf,
                "action": "details",
                "id": str(rid),
                "finish": "foil",
                "condition": "LP",
                "language": "de",
                "purchase_price": "1.5",
            },
        )
        assert r.status_code == 303 and r.headers["location"].endswith("ok=updated"), r.headers
        rec = stack.ark.collections["alice"][rid]
        assert (rec["modifier"], rec["condition"], rec["language"], rec["purchasePrice"]) == (
            "Foil",
            "LP",
            "DE",
            1.5,
        )
        page = await b.http.get(r.headers["location"], headers=NAV)
        assert "Card details saved" in page.text and "class='pill cond'>LP<" in page.text
        # the JSON API takes the same fields; an empty price clears it, a bad language is refused
        r = await api(b, "POST", f"/collection/api/rows/{rid}", {"purchase_price": "", "condition": "NM"})
        assert r.status_code == 200, r.text
        assert rec["purchasePrice"] is None and rec["condition"] == "NM"
        r = await api(b, "POST", f"/collection/api/rows/{rid}", {"language": "1"})
        assert r.status_code == 400
        r = await api(b, "POST", f"/collection/api/rows/{rid}", {"purchase_price": "lots"})
        assert r.status_code == 400
    finally:
        await b.aclose()


async def test_editor_edits_maybeboard_rows_and_adds_to_the_maybeboard(stack: Stack) -> None:
    stack.ark.add_side_row(42, "Opt", 2)
    b = await linked(stack)
    try:
        r = await b.http.get("/decks/42/edit", headers=NAV)
        cfg = json.loads(
            re.search(
                r"<script id='editor-config' type='application/json'>(.*?)</script>", r.text, re.S
            ).group(1)
        )
        assert cfg["sideCategory"] == "Maybeboard"
        opt = next(c for c in cfg["cards"] if c["name"] == "Opt")
        assert opt["zone"] == "side" and opt["in_deck"] is False
        assert "id='addzone'" in r.text and "Paste a list" in r.text
        main_before = sum(
            c["quantity"] for c in stack.ark.decks[42]["cards"] if "Maybeboard" not in (c["categories"] or [])
        )
        r = await api(
            b,
            "POST",
            "/api/v1/proposals",
            {
                "kind": "edit",
                "deck_id": "42",
                "changes": [
                    {"action": "set_quantity", "card_name": "Opt", "quantity": 3, "zone": "side"},
                    {"action": "add", "card_name": "Swamp", "quantity": 1, "zone": "side"},
                ],
                "apply": True,
            },
            csrf=await b.csrf("/decks"),
        )
        assert r.status_code == 201 and r.json()["applied"] is True, r.text
        rows = {
            c["card"]["oracleCard"]["name"]: c
            for c in stack.ark.decks[42]["cards"]
            if "Maybeboard" in (c["categories"] or [])
        }
        assert rows["Opt"]["quantity"] == 3 and rows["Swamp"]["quantity"] == 1
        main_after = sum(
            c["quantity"] for c in stack.ark.decks[42]["cards"] if "Maybeboard" not in (c["categories"] or [])
        )
        assert main_after == main_before  # the deck proper is untouched
        rows_out = r.json()["result"]["rows"]
        assert any(x.get("zone") == "side" for x in rows_out), rows_out
        # a commander or a pinned printing cannot go to the maybeboard
        r = await api(
            b,
            "POST",
            "/api/v1/proposals",
            {
                "kind": "edit",
                "deck_id": "42",
                "changes": [{"action": "set_commander", "card_name": "Opt", "zone": "side"}],
            },
            csrf=await b.csrf("/decks"),
        )
        assert r.status_code == 400
    finally:
        await b.aclose()


async def test_precons_page_lists_sets_and_filters(stack: Stack) -> None:
    b = Browser(stack.h)
    try:
        await b.login("/precons")
        r = await b.http.get("/precons", headers=NAV)
        assert r.status_code == 200 and "Sample Set (SMP)" in r.text and "Sample Commander Deck" in r.text
        assert "href='/decks/42'" in r.text
        r = await b.http.get("/precons?q=nothing-here", headers=NAV)
        assert "No preconstructed deck matches" in r.text
        r = await b.http.get("/precons?q=smp", headers=NAV)
        assert "Sample Set (SMP)" in r.text and "Older Set" not in r.text
        home = await b.http.get("/", headers=NAV)
        assert "href='/precons'" in home.text
        search = await b.http.get("/search", headers=NAV)
        assert "href='/precons'" in search.text
        # the listing is cached: one Archidekt call for four page views
        assert stack.ark.calls.count(("GET", "/api/decks/precons/")) == 1
    finally:
        await b.aclose()


# -- what the hidden duplicates had, the owners now carry ------------------------------------------


class _RecordingMF:
    """A research service that records what the gateway sends it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def call(self, name: str, arguments: dict, *, owner=None, internal: bool = False):
        import mcp_types as types

        # Mystic Forge's tools take one pydantic model: {"params": {...}} is the only shape it accepts
        assert set(arguments) == {"params"} and isinstance(arguments["params"], dict), (name, arguments)
        self.calls.append((name, dict(arguments["params"])))
        marker = {"goldfish_run": "## Metrics", "goldfish_ab": "## Deltas (A − B)"}.get(name, "")
        return types.CallToolResult(content=[types.TextContent(type="text", text=f"# {name} ran\n{marker}")])


def test_parse_deck_carries_rules_text_and_the_archidekt_import_syntax() -> None:
    from pathlib import Path

    from mtg_gateway.archidekt import FORMAT_NAMES, Deck, DeckCard, parse_deck
    from mtg_gateway.decks import deck_to_archidekt_text

    sample = Path(__file__).parent / "fixtures" / "live" / "archidekt_deck_sample.json"
    deck = parse_deck(json.loads(sample.read_text(encoding="utf-8")))
    assert all(c.oracle_text for c in deck.cards)  # the live payload has text for every card
    text = deck_to_archidekt_text(deck)
    names = [line.split(" ", 1)[1].split(" (")[0].casefold() for line in text.splitlines()]
    assert "[Commander{top}]" in text and names == sorted(names)  # by card name, like Archidekt's export

    def card(name: str, qty: int, **over) -> DeckCard:
        base = dict(
            relation_id=None,
            card_id=None,
            name=name,
            quantity=qty,
            categories=[],
            modifier="Normal",
            set_code="",
            collector_number="",
        )
        return DeckCard(**{**base, **over})  # fmt: skip

    deck = Deck(
        id="1", name="t", owner="o", updated_at="", raw={}, format_id=3, format=FORMAT_NAMES[3],
        categories=[
            {"name": "Commander", "isPremier": True, "includedInDeck": True},
            {"name": "Maybeboard", "includedInDeck": False},
        ],
        cards=[
            card("Sol Ring", 1, set_code="cmr", collector_number="1", modifier="Foil", categories=["Ramp"],
                 label="Upgrade,#ff0000"),
            card("Aesi, Tyrant of Gyre Strait", 1, categories=["Commander"], modifier="Etched"),
            card("Opt", 2, categories=["Maybeboard"], label=",#656565"),  # Archidekt's colour-only label
            card("Weird ^ Name [x]", 1, categories=["A,b"]),
        ],
    )  # fmt: skip
    assert deck_to_archidekt_text(deck).splitlines() == [
        "1x Aesi, Tyrant of Gyre Strait *E* [Commander{top}]",
        "2x Opt [Maybeboard{noDeck}{noPrice}]",
        "1x Sol Ring (cmr) 1 *F* [Ramp] ^Upgrade,#ff0000^",
        "1x Weird Name [x] [A b]",
    ]  # sorted by card name, as Archidekt's own export is


async def test_owner_tools_carry_what_the_hidden_duplicates_had(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    gw = stack.h.app.state.gateway
    # get_deck: rules text on request, Archidekt's import syntax always (archidekt_deck, archidekt_export)
    plain = structured(await call(stack.h, token, "get_deck", {"deck_ref": "42"}))
    assert plain["ok"] and all("oracle_text" not in c for c in plain["cards"])
    assert "\n1x " in plain["archidekt_text"] and "[Commander{top}]" in plain["archidekt_text"]
    full = structured(await call(stack.h, token, "get_deck", {"deck_ref": "42", "include_text": True}))
    assert all("oracle_text" in c for c in full["cards"])
    # deck_stats: the structural checks validate_archidekt_deck made
    stats = structured(await call(stack.h, token, "deck_stats", {"deck_ref": "42"}))["stats"]
    checks = stats["checks"]
    assert set(checks) == {
            "deck_size", "commander_zone", "colour_identity_violations", "singleton_violations",
            "copy_limit_violations", "uncategorised", "problems", "ok",
        }  # fmt: skip
    assert checks["deck_size"]["actual"] == plain["card_count"] and checks["commander_zone"]["count"] == 1
    # compare_decks: a precon-style summary with the basics apart (precon_diff)
    cmp_ = structured(await call(stack.h, token, "compare_decks", {"a": "42", "b": "1 Sol Ring\n1 Opt"}))
    assert cmp_["ok"] and set(cmp_["summary"]) == {
        "before_size", "after_size", "cut", "added", "kept", "cut_pct", "added_pct", "basic_land_changes",
    }  # fmt: skip
    assert cmp_["summary"]["before_size"] == plain["card_count"]
    # run_deck_report passes the simulator's knobs through (goldfish_run); unknown ones are refused
    real = gw.reports.mf
    rec = _RecordingMF()
    gw.reports.mf = rec
    try:
        options = {
            "seed": 7,
            "until_turn": 8,
            "opponents": 2,
            "mulligan": {"min_sources": 2},
            "annotations": [{"card": "Opt", "role": "cantrip"}],
            "combos": [["Opt", "Sol Ring"]],
        }
        out = structured(
            await call(stack.h, token, "run_deck_report", {"deck_ref": "42", "games": 10, "options": options})
        )
        assert out["ok"] and not out.get("reused"), out
        name, args = next(c for c in rec.calls if c[0] == "goldfish_run")
        assert args == {"deck": args["deck"], "n": 10, **options}
        again = structured(
            await call(stack.h, token, "run_deck_report", {"deck_ref": "42", "games": 10, "options": options})
        )
        assert not again.get("reused")  # options change the simulation: no ten-minute reuse
        bad = structured(
            await call(stack.h, token, "run_deck_report", {"deck_ref": "42", "options": {"nn": 5}})
        )
        assert bad == {"ok": False, "error": "invalid", "message": "unknown simulation option(s): nn"}
        # compare_decks simulate=true is the paired A/B (goldfish_ab), with its own knobs, not stored
        before = len(gw.reports.list("user-1"))
        ab = structured(
            await call(
                stack.h,
                token,
                "compare_decks",
                {"a": "42", "b": "Commander\n1 Opt\n\n1 Sol Ring", "simulate": True, "games": 20,
                 "options": {"allow_different_commanders": True, "annotations_b": [{"card": "Opt"}]}},
            )
        )  # fmt: skip
        assert ab["ok"] and ab["goldfish_ab"]["ok"] and "goldfish_ab ran" in ab["goldfish_ab"]["text"]
        name, args = rec.calls[-1]
        assert name == "goldfish_ab"
        assert (
            args["deck_b"] == "1 Opt [Commander]\n1 Sol Ring [Commander]\n" and args["n"] == 20
        )  # gateway rendering, commander first
        assert args["allow_different_commanders"] is True and args["annotations_b"] == [{"card": "Opt"}]
        assert "opponents" not in args
        assert len(gw.reports.list("user-1")) == before  # an A/B is not a stored report
        bad = structured(
            await call(stack.h, token, "compare_decks", {"a": "42", "b": "1 Opt", "simulate": True,
                                                       "options": {"opponents": 2}})
        )  # fmt: skip
        assert bad["error"] == "invalid" and "opponents" in bad["message"]
    finally:
        gw.reports.mf = real
    # without the research service the A/B says so instead of failing the comparison
    gw.reports.mf = None
    try:
        out = structured(
            await call(stack.h, token, "compare_decks", {"a": "42", "b": "1 Opt", "simulate": True})
        )
        assert out["ok"] and out["goldfish_ab"]["error"] == "unavailable"
    finally:
        gw.reports.mf = real
