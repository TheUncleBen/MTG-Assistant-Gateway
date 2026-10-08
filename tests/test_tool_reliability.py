"""The assistant's tools as an assistant meets them: self-contained input schemas, one card-field
spelling, compact deck reads, refusals instead of guesses, errors flagged as errors, and the
collection and commander-search paths that used to fail quietly."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from mcp import Client
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from mtg_gateway.archidekt import ArchidektClient, Pacer
from mtg_gateway.decklist import parse_decklist
from mtg_gateway.mf_proxy import MysticForgeProxy
from mtg_gateway.scan.scryfall import ScryfallClient
from mtg_gateway.schemas import (
    DeckChange,
    DeckDetails,
    card_aliases,
    compact_schema,
    deck_change_for_service,
    flatten_params,
    inline_refs,
)

from .conftest import FakeIdP, Harness, make_settings, running
from .fake_archidekt import FakeArchidekt
from .fake_scryfall import FakeScryfall
from .test_decks_and_proxy import Browser, call, linked_user, mcp_token, sse_json, structured

NAV = {"Sec-Fetch-Mode": "navigate", "Accept": "text/html"}


# -- a Mystic Forge fake in the real one's shape: every tool takes one `params` model ---------
class NamedInput(BaseModel):
    name: str = Field(description="Card name to look up.", min_length=1)
    set_code: str | None = Field(default=None, description="Optional set code.")


class PreconInput(BaseModel):
    query: str = Field(description="Words in the precon's name.")


class FormatInput(BaseModel):
    cards: list[NamedInput] = Field(description="The cards.")


def real_shaped_mystic_forge() -> MCPServer:
    mf = MCPServer(name="mystic-forge-shaped")

    @mf.tool(name="scryfall_named", description="Look up a card by name")
    async def scryfall_named(params: NamedInput) -> str:
        return f"{params.name} [{params.set_code or 'any'}]: fake oracle text"

    @mf.tool(name="precon_search", description="Find precons")
    async def precon_search(params: PreconInput) -> str:
        # what the real Mystic Forge answers when its upstream request fails (seen live 2026-10-08)
        return "Unexpected error: ProxyError: 403 Forbidden"

    @mf.tool(
        name="format_archidekt",
        description="REQUIRED when outputting any decklist for Archidekt import. Do NOT manually format "
        "decklists — always call this tool instead. Annotations: pass them to goldfish_run as `annotations`.",
    )
    async def format_archidekt(params: FormatInput) -> str:
        return f"{len(params.cards)} cards; next call goldfish_run"

    return mf


class Stack:
    def __init__(self, h: Harness, ark: FakeArchidekt, sf: FakeScryfall):
        self.h, self.ark, self.sf = h, ark, sf


async def _stack(tmp_path: Path, idp: FakeIdP, mode: str):
    ark = FakeArchidekt()
    sf = FakeScryfall()
    settings = make_settings(
        tmp_path, writes_enabled=True, approval_mode_default=mode, archidekt_base="https://ark.test/api"
    )
    client = ArchidektClient(settings.archidekt_base, "test-agent", Pacer(0.0), http=ark.client())
    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=lambda: Client(real_shaped_mystic_forge()))
    async with running(Harness(settings, idp, archidekt=client, mf_proxy=proxy)) as h:
        h.app.state.gateway.scan.scryfall = ScryfallClient(
            "https://scryfall.test", min_interval=0.0, http=sf.client()
        )
        yield Stack(h, ark, sf)


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP):
    async for s in _stack(tmp_path, idp, "auto"):
        yield s


@pytest.fixture
async def manual(tmp_path: Path, idp: FakeIdP):
    async for s in _stack(tmp_path, idp, "manual"):
        yield s


async def tools(h: Harness, token: str) -> dict[str, dict]:
    listing = sse_json(await h.mcp(token, "tools/list"))
    return {t["name"]: t for t in listing["result"]["tools"]}


# -- schemas ----------------------------------------------------------------------------------
def test_inline_refs_writes_definitions_out_and_stops_at_cycles() -> None:
    schema = {
        "$defs": {"Card": {"type": "object", "properties": {"name": {"type": "string"}}}},
        "properties": {"cards": {"type": "array", "items": {"$ref": "#/$defs/Card"}}},
    }
    out = inline_refs(schema)
    assert "$defs" not in out and out["properties"]["cards"]["items"]["properties"]["name"]
    loop = {
        "$defs": {"N": {"type": "object", "properties": {"next": {"$ref": "#/$defs/N"}}}},
        "$ref": "#/$defs/N",
    }
    out = inline_refs(loop)
    assert out["properties"]["next"] == {"$ref": "#/$defs/N"} and "N" in out["$defs"]  # finite


def test_compact_schema_drops_titles_and_null_branches_but_keeps_meaningful_null() -> None:
    schema = compact_schema(DeckDetails.model_json_schema())
    text = json.dumps(schema)
    assert '"title"' not in text
    assert schema["properties"]["name"]["type"] == "string"  # "string or null" became "string"
    assert {"type": "null"} in schema["properties"]["edh_bracket"]["anyOf"]  # "null clears it"
    # a property that is itself called "title" survives: only the keyword goes
    kept = compact_schema({"type": "object", "properties": {"title": {"type": "string", "title": "Title"}}})
    assert kept == {"type": "object", "properties": {"title": {"type": "string"}}}


def test_flatten_params_only_for_a_single_params_object() -> None:
    wrapped = {
        "$defs": {"In": {"type": "object", "properties": {"q": {"type": "string"}}, "required": ["q"]}},
        "properties": {"params": {"$ref": "#/$defs/In"}},
        "required": ["params"],
    }
    assert flatten_params(wrapped) == {
        "type": "object",
        "properties": {"q": {"type": "string"}},
        "required": ["q"],
    }
    assert flatten_params({"properties": {"q": {"type": "string"}}}) is None


def test_card_aliases_map_old_spellings_onto_the_documented_ones() -> None:
    assert card_aliases({"card_name": "Sol Ring", "set": "cmr", "foil": True}) == {
        "name": "Sol Ring",
        "set_code": "cmr",
        "finish": "foil",
    }
    assert card_aliases({"name": "A", "card_name": "B", "finish": "Normal"}) == {
        "name": "A",
        "finish": "nonfoil",
    }
    change = DeckChange.model_validate({"action": "Add", "card_name": "Sol Ring", "finish": "normal"})
    assert deck_change_for_service(change) == {"action": "add", "card_name": "Sol Ring", "finish": "normal"}
    for bad in (
        {"action": "add", "name": "X", "quantity": True},
        {"action": "add", "name": "X", "quantity": "2"},
    ):
        with pytest.raises(ValueError):
            DeckChange.model_validate(bad)  # a boolean or a string is not a count
    assert DeckChange.model_validate({"action": "set_quantity", "name": "X", "quantity": 0}).quantity == 0
    assert DeckDetails.model_validate({"deck_format": "EDH"}).deck_format == "edh"
    with pytest.raises(ValueError):
        DeckDetails.model_validate({"private": "yes"})


def test_parse_decklist_skips_and_reports_lines_that_are_not_cards() -> None:
    missed: list[str] = []
    cards = parse_decklist("1 Sol Ring\nTotal: 100 cards\nhttps://archidekt.com/decks/1\n2 Island\n", missed)
    assert [c.name for c in cards] == ["Sol Ring", "Island"]
    assert missed == ["Total: 100 cards", "https://archidekt.com/decks/1"]


# -- the tool list ------------------------------------------------------------------------------
async def test_every_listed_schema_is_self_contained_and_flat(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    listed = await tools(stack.h, token)
    assert "get_my_deck" not in listed and "archidekt_user" not in listed  # merged into their owners
    for name, tool in listed.items():
        text = json.dumps(tool["inputSchema"])
        assert "$ref" not in text and '"title"' not in text, name
        assert set(tool["inputSchema"].get("properties", {})) != {"params"}, name
    change = listed["propose_deck_changes"]["inputSchema"]["properties"]["changes"]["items"]
    assert {"action", "name", "quantity", "finish", "zone"} <= set(change["properties"])
    assert change["properties"]["finish"]["enum"] == ["nonfoil", "foil", "etched"]
    # Mystic Forge's params-wrapped tools are listed flat, and their text names the gateway owners
    named = listed["scryfall_named"]["inputSchema"]
    assert named["required"] == ["name"] and "set_code" in named["properties"]
    desc = listed["format_archidekt"]["description"]
    assert "goldfish_run" not in desc and "REQUIRED" not in desc


async def test_mystic_forge_calls_take_flat_or_wrapped_arguments(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    flat = await call(stack.h, token, "scryfall_named", {"name": "Sol Ring", "set_code": "cmr"})
    assert not flat.get("isError") and "Sol Ring [cmr]" in json.dumps(flat), flat
    wrapped = await call(stack.h, token, "scryfall_named", {"params": {"name": "Sol Ring"}})
    assert not wrapped.get("isError") and "Sol Ring [any]" in json.dumps(wrapped), wrapped
    out = await call(stack.h, token, "format_archidekt", {"cards": [{"name": "Sol Ring"}]})
    assert "goldfish_run" not in json.dumps(out) and "run_deck_report" in json.dumps(out)


async def test_mystic_forge_upstream_failure_is_flagged_as_an_error(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    out = await call(stack.h, token, "precon_search", {"query": "Aesi"})
    assert out.get("isError") is True and "ProxyError" in json.dumps(out), out


async def test_arguments_that_do_not_fit_get_a_flagged_structured_refusal(stack: Stack) -> None:
    token = await linked_user(stack)
    res = await call(
        stack.h,
        token,
        "propose_deck_changes",
        {"deck_id": "42", "changes": [{"action": "explode", "name": "X"}]},
    )
    out = structured(res)
    assert res.get("isError") is True and out["ok"] is False and out["error"] == "invalid"
    assert "changes.0.action" in out["message"] and "'set_quantity'" in out["message"]
    # a refusal from the tool itself carries the error flag too
    res = await call(stack.h, token, "get_deck", {"deck_ref": "not a deck"})
    assert res.get("isError") is True and structured(res)["ok"] is False


# -- deck reads ---------------------------------------------------------------------------------
async def test_get_deck_views_trade_detail_for_size(stack: Stack) -> None:
    token = await linked_user(stack)
    sizes = {}
    for view in ("summary", "text", "cards", "export", "full"):
        out = structured(await call(stack.h, token, "get_deck", {"deck_ref": "42", "view": view}))
        assert out["ok"] and out["owner"] == "alice", (view, out)
        sizes[view] = len(json.dumps(out))
        assert ("cards" in out) == (view in ("cards", "full")), view
        assert ("decklist_text" in out) == (view in ("text", "full")), view
        assert ("archidekt_text" in out) == (view in ("export", "full")), view
    assert sizes["summary"] < sizes["text"] < sizes["full"] and sizes["cards"] < sizes["full"]
    default = structured(await call(stack.h, token, "get_deck", {"deck_ref": "42"}))
    assert default["view"] == "text" and "decklist_text" in default
    # rules text needs card rows: asked with a view that has none, it comes with the cards view
    rules = structured(await call(stack.h, token, "get_deck", {"deck_ref": "42", "include_text": True}))
    assert rules["view"] == "cards" and all("oracle_text" in c for c in rules["cards"])
    # an own private deck still reads through the one tool, and the owner field says whose it is
    stack.ark.private.add(42)
    mine = structured(await call(stack.h, token, "get_deck", {"deck_ref": "42", "view": "summary"}))
    assert mine["ok"] and mine["owner"] == "alice"


async def test_compare_decks_refuses_a_reference_it_cannot_read(stack: Stack) -> None:
    token = await linked_user(stack)
    out = structured(await call(stack.h, token, "compare_decks", {"a": "42", "b": "my atraxa deck"}))
    assert out["ok"] is False and out["error"] == "invalid" and "b_list" in out["message"], out
    out = structured(
        await call(
            stack.h,
            token,
            "compare_decks",
            {"a_list": "1 Sol Ring\n1 Sol Ring\nTotal: 2", "b_list": "3 Sol Ring\n1 Island"},
        )
    )
    assert out["ok"] is True, out
    assert out["unread_lines"] == {"a": ["Total: 2"]}
    changed = json.dumps(out)
    assert "Island" in changed and "Sol Ring" in changed
    # a card listed twice counts both lines: 2 -> 3 is one more copy, not 1 -> 3
    assert {"name": "Sol Ring", "before": 2, "after": 3} in out["changed"], out
    both = structured(
        await call(stack.h, token, "compare_decks", {"a": "42", "a_list": "1 Sol Ring", "b": "42"})
    )
    assert both["ok"] is False and "not both" in both["message"]


# -- proposals ----------------------------------------------------------------------------------
async def test_proposal_list_carries_a_summary_and_rejections_a_closed_at(manual: Stack) -> None:
    token = await linked_user(manual)
    p = structured(
        await call(
            manual.h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "name": "Island", "quantity": 2}]},
        )
    )
    assert p["ok"], p
    listed = structured(await call(manual.h, token, "list_my_proposals"))["proposals"]
    assert "Island" in listed[0]["summary"] and len(listed[0]["summary"]) <= 400
    structured(await call(manual.h, token, "reject_proposal", {"proposal_id": p["proposal_id"]}))
    row = structured(await call(manual.h, token, "list_my_proposals"))["proposals"][0]
    assert row["state"] == "rejected" and row.get("closed_at") and not row.get("applied_at"), row


async def test_new_deck_size_and_fuzzy_scan_warnings(manual: Stack) -> None:
    token = await linked_user(manual)
    p = structured(
        await call(
            manual.h,
            token,
            "propose_new_deck",
            {"name": "Short", "deck_format": "commander", "cards": [{"name": "Sol Ring"}]},
        )
    )
    assert p["ok"] and any("100" in w for w in p.get("warnings", [])), p


# -- collection ---------------------------------------------------------------------------------
async def test_the_assistant_applies_its_own_collection_proposal_in_auto_mode(stack: Stack) -> None:
    token = await linked_user(stack)
    p = structured(
        await call(
            stack.h, token, "propose_collection_changes", {"add": [{"name": "Sol Ring", "quantity": 2}]}
        )
    )
    assert p["ok"] and p["assistant_may_apply"] is True, p
    a = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] is True and a["state"] == "applied", a
    assert a["result"]["verified"] is True and a["result"]["mismatches"] == []
    assert [r["quantity"] for r in manual_rows(stack)] == [2]


def manual_rows(stack: Stack) -> list[dict]:
    return list(stack.ark.collections.get("alice", {}).values())


async def test_a_collection_write_archidekt_did_not_keep_is_not_verified(stack: Stack) -> None:
    class Forgetful(dict):
        def __setitem__(self, key, value) -> None:  # answers the write, keeps nothing
            pass

    token = await linked_user(stack)
    stack.ark.collections["alice"] = Forgetful()
    p = structured(await call(stack.h, token, "propose_collection_changes", {"add": ["Sol Ring"]}))
    a = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["result"]["verified"] is False, a
    assert any("Sol Ring" in m or "record" in m for m in a["result"]["mismatches"]), a


async def test_a_manual_collection_proposal_carries_the_in_chat_card(manual: Stack) -> None:
    token = await linked_user(manual)
    listed = await tools(manual.h, token)
    meta = listed["propose_collection_changes"].get("_meta") or {}
    assert any("ui" in str(k) for k in meta), meta
    res = await call(manual.h, token, "propose_collection_changes", {"add": ["Sol Ring"]})
    assert (res.get("_meta") or {}).get("mtg/approval"), res
    assert "mtg/approval" not in json.dumps(res["structuredContent"])  # never in the model's context


# -- search -------------------------------------------------------------------------------------
async def test_search_decks_finds_a_commander_by_part_of_its_name(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    exact = structured(
        await call(stack.h, token, "search_decks", {"commander": "Aesi, Tyrant of Gyre Strait"})
    )
    assert [str(d["id"]) for d in exact["decks"]] == ["42"] and "commander_matched" not in exact
    part = structured(await call(stack.h, token, "search_decks", {"commander": "aesi"}))
    assert part["commander_matched"] == "Aesi, Tyrant of Gyre Strait"
    assert [str(d["id"]) for d in part["decks"]] == ["42"]
    stack.sf.commanders["krenko"] = ["Krenko, Mob Boss", "Krenko, Tin Street Kingpin"]
    several = structured(await call(stack.h, token, "search_decks", {"commander": "Krenko"}))
    assert several["ok"] and several["decks"] == []
    assert several["commander_suggestions"] == ["Krenko, Mob Boss", "Krenko, Tin Street Kingpin"]
    nothing = structured(await call(stack.h, token, "search_decks", {"commander": "Nobody At All"}))
    assert nothing["ok"] and nothing["decks"] == [] and "commander_suggestions" not in nothing


async def test_search_decks_limit_and_owner(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    one = structured(await call(stack.h, token, "search_decks", {"limit": 1}))
    assert len(one["decks"]) == 1, one and one["more_on_page"] is True
    # one user's public decks (what archidekt_user listed): their private deck 43 never shows
    alice = structured(
        await call(stack.h, token, "search_decks", {"owner": "alice", "order_by": "-updatedAt"})
    )
    assert [(str(d["id"]), d["owner"]) for d in alice["decks"]] == [("42", "alice")]
    amy = structured(await call(stack.h, token, "search_decks", {"owner": "amy"}))
    assert amy["ok"] and amy["decks"] == []


async def test_search_page_finds_a_commander_by_part_of_its_name(stack: Stack) -> None:
    b = Browser(stack.h)
    await b.login()
    try:
        r = await b.http.get("/search?commander=aesi", headers=NAV)
        assert r.status_code == 200 and "Showing decks led by Aesi, Tyrant of Gyre Strait" in r.text
        assert "Sample Commander Deck" in r.text
        stack.sf.commanders["krenko"] = ["Krenko, Mob Boss", "Krenko, Tin Street Kingpin"]
        r = await b.http.get("/search?commander=Krenko", headers=NAV)
        assert "Did you mean" in r.text and "Krenko, Mob Boss" in r.text
    finally:
        await b.aclose()


# -- scan tools ---------------------------------------------------------------------------------
async def test_card_printings_filters_and_limits(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    name = "Aesi, Tyrant of Gyre Strait"
    every = structured(await call(stack.h, token, "card_printings", {"name": name}))
    assert every["total_cards"] == 4 and every["matching"] == 4
    cmr = structured(await call(stack.h, token, "card_printings", {"name": name, "set_code": "CMR"}))
    assert [c["set"] for c in cmr["cards"]] == ["cmr"] and cmr["matching"] == 1
    one = structured(await call(stack.h, token, "card_printings", {"name": name, "limit": 1}))
    assert len(one["cards"]) == 1 and one["truncated"] is True


# -- health check -------------------------------------------------------------------------------
async def test_health_check_reports_the_research_service(stack: Stack) -> None:
    r = await stack.h.http.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok" and r.json()["mystic_forge"] == "ok"


async def test_health_check_notices_the_research_service_is_down(tmp_path: Path, idp: FakeIdP) -> None:
    class Down:
        async def __aenter__(self):
            raise ConnectionError("refused")

        async def __aexit__(self, *exc):
            return False

    settings = make_settings(tmp_path)
    proxy = MysticForgeProxy("http://mf.test/mcp", client_factory=Down)
    async with running(Harness(settings, idp, mf_proxy=proxy)) as h:
        r = await h.http.get("/healthz")
        # still 200: the gateway's own pages work, so the container must not be restarted for it
        assert r.status_code == 200, r.text
        assert r.json()["status"] == "degraded" and r.json()["mystic_forge"] == "down"


# -- linking Archidekt --------------------------------------------------------------------------
async def test_linking_archidekt_needs_the_risk_note_acknowledged(stack: Stack) -> None:
    b = Browser(stack.h)
    await b.login("/account")
    try:
        page = await b.http.get("/account", headers=NAV)
        assert "terms of service restrict automated access" in page.text
        assert "name='accept_risk'" in page.text and "trusted with this link" in page.text
        csrf = await b.csrf("/account")
        r = await b.http.post(
            "/account",
            data={
                "csrf": csrf,
                "action": "link",
                "archidekt_login": "alice",
                "archidekt_password": "pw-alice",
            },
        )
        assert r.status_code == 400 and "Tick the box" in r.text
        assert stack.h.app.state.gateway.db.get_link("user-1") is None  # nothing linked, nothing sent
        assert (await b.link("alice", "pw-alice")).status_code == 303  # with the tick it links
    finally:
        await b.aclose()


# -- Archidekt sign-ins stay out of reach -------------------------------------------------------
def _linked_db(tmp_path: Path, marker: str):
    from mtg_gateway.db import Database

    db = Database(tmp_path / "d" / "g.sqlite")
    db.upsert_user("u1", email=None, name="x", preferred_username=None, groups=[])
    db.save_link("u1", username="alice", user_id="1", secret_enc=marker)
    return db


def test_backups_carry_no_archidekt_session(tmp_path: Path) -> None:
    from mtg_gateway.backup import export_now

    marker = "gAAAAA-SECRET-MARKER-" + "x" * 40
    db = _linked_db(tmp_path, marker)
    out = export_now(db, tmp_path / "b", keep_days=14)
    assert marker.encode() not in out.read_bytes()
    assert db.get_link("u1") is not None  # the live link is untouched
    db.close()


def test_unlinking_leaves_no_copy_of_the_session_on_disk(tmp_path: Path) -> None:
    marker = "gAAAAA-SECRET-MARKER-" + "y" * 40
    db = _linked_db(tmp_path, marker)
    assert db.revoke_link("u1")
    for f in (tmp_path / "d").iterdir():  # the database and its WAL alike
        assert marker.encode() not in f.read_bytes(), f.name
    db.close()


def test_settings_never_print_their_secrets(tmp_path: Path) -> None:
    settings = make_settings(tmp_path)
    shown = repr(settings)
    for secret in (settings.fernet_key, settings.session_secret, settings.oidc_client_secret):
        assert secret and secret not in shown


# -- deck organisation through a details proposal -----------------------------------------------
async def test_folder_tags_and_cover_go_through_a_details_proposal(stack: Stack) -> None:
    token = await linked_user(stack)
    ark = stack.ark
    uid = "be68e315-ffef-40a8-8a46-1c042b148c03"
    ark.decks[42]["cards"][0]["card"]["uid"] = uid  # the fixture deck has no Scryfall ids
    cover_name = ark.decks[42]["cards"][0]["card"]["oracleCard"]["name"]
    ark.folders["alice"] = [{"id": 501, "name": "Cube", "private": False, "parentFolder": 1077}]
    details = {"folder": "cube", "add_tags": ["budget", "Spicy"], "cover": cover_name}
    p = structured(await call(stack.h, token, "propose_deck_details", {"deck_id": "42", "details": details}))
    assert p["ok"] and p["kind"] == "details" and p["risk"] == "high", p
    diff = p["diff"]
    assert "folder: Top level (no folder) -> Cube" in diff and "tags: none -> budget, Spicy" in diff, diff
    assert f"cover: current -> {cover_name}" in diff
    assert ark.deck_tags.get(42, []) == [] and 42 not in ark.deck_folder  # nothing sent yet
    a = structured(await call(stack.h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    if a.get("error") == "browser_required":  # the member's mode asks them: apply as they would
        b = Browser(stack.h)
        await b.login()
        csrf = await b.csrf("/proposals")
        r = await b.http.post(f"/api/v1/proposals/{p['proposal_id']}/apply", headers={"X-CSRF-Token": csrf})
        a = r.json()
        await b.aclose()
    assert a.get("state") == "applied", a
    assert ark.deck_folder[42] == 501
    assert [x["name"] for x in ark.deck_tags[42]] == ["budget", "Spicy"]
    assert ark.updates[-1]["featured"].endswith(f"/{uid}.webp")
    # taking a tag off names a tag the deck has; anything else is refused before any proposal
    off = structured(
        await call(
            stack.h, token, "propose_deck_details", {"deck_id": "42", "details": {"remove_tags": ["nope"]}}
        )
    )
    assert off["ok"] is False and "no tag 'nope'" in off["message"]
    bad = structured(
        await call(
            stack.h, token, "propose_deck_details", {"deck_id": "42", "details": {"folder": "Nowhere"}}
        )
    )
    assert bad["ok"] is False and "Cube" in bad["message"]  # the error lists the folders there are


async def test_new_deck_cards_keep_the_etched_and_foil_finish(manual: Stack) -> None:
    token = await linked_user(manual)
    cards = [
        {"name": "Sol Ring", "set_code": "sld", "collector_number": "1074", "finish": "etched"},
        {"name": "Swamp", "quantity": 2, "finish": "foil"},
        {"name": "Island", "finish": "nonfoil"},
    ]
    p = structured(await call(manual.h, token, "propose_new_deck", {"name": "Finishes", "cards": cards}))
    assert p["ok"], p
    row = manual.h.db._one("SELECT * FROM proposals WHERE id = ?", (p["proposal_id"],))
    finishes = {c["name"]: c["finish"] for c in json.loads(row["changes_json"])["cards"]}
    assert finishes == {"Sol Ring": "Etched", "Swamp": "Foil", "Island": ""}, finishes


# -- G17: a name a scan guessed is flagged on the proposal ----------------------------------------
def _fuzzy_scan(stack: Stack, sub: str = "user-1") -> None:
    now = 1_700_000_000
    stack.h.app.state.gateway.scan.store.save(
        {
            "id": "scan_g17",
            "owner_sub": sub,
            "name": "binder",
            "status": "done",
            "source": "photo",
            "items": [
                {
                    "input": {"name": "Sol Rimg"},
                    "status": "fuzzy",
                    "card": {"name": "Sol Ring", "set": "cmr", "collector_number": "472"},
                    "note": "matched 'Sol Ring' from 'Sol Rimg'",
                    "quantity": 1,
                },
                {"input": {"name": "Island"}, "status": "exact", "card": {"name": "Island"}, "quantity": 1},
            ],
            "created_at": now,
            "updated_at": now,
        }
    )


async def test_a_guessed_name_is_flagged_on_the_proposal_the_review_and_the_card(manual: Stack) -> None:
    token = await linked_user(manual)
    _fuzzy_scan(manual)
    p = structured(
        await call(
            manual.h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "add", "name": "Sol Ring", "quantity": 1},
                    {"action": "add", "name": "Island", "quantity": 1},
                ],
            },
        )
    )
    assert p["ok"], p
    assert p["guessed_names"] == [{"name": "Sol Ring", "read_as": "Sol Rimg"}], p
    assert "name guessed from 'Sol Rimg'" in p["diff"]
    island = next(r for r in p["rows"] if r.get("name") == "Island")
    assert "guessed_from" not in island  # an exact scan match is not flagged
    b = Browser(manual.h)
    await b.login(f"/proposals/{p['proposal_id']}")
    page = await b.http.get(f"/proposals/{p['proposal_id']}")
    assert (
        "name guessed from &#x27;Sol Rimg&#x27;" in page.text or "name guessed from 'Sol Rimg'" in page.text
    )


async def test_a_guessed_name_is_flagged_on_a_new_deck_and_a_collection_proposal(manual: Stack) -> None:
    token = await linked_user(manual)
    _fuzzy_scan(manual)
    nd = structured(
        await call(manual.h, token, "propose_new_deck", {"name": "G17", "cards": [{"name": "Sol Ring"}]})
    )
    assert nd["guessed_names"] == [{"name": "Sol Ring", "read_as": "Sol Rimg"}], nd
    col = structured(
        await call(manual.h, token, "propose_collection_changes", {"add": [{"name": "sol ring"}]})
    )
    assert col.get("guessed_names"), col


async def test_no_scan_means_no_flag(manual: Stack) -> None:
    token = await linked_user(manual)
    p = structured(
        await call(
            manual.h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [{"action": "add", "name": "Sol Ring", "quantity": 1}]},
        )
    )
    assert p["ok"] and "guessed_names" not in p and "guessed" not in p["diff"]


# -- a simulation that was asked for and failed fails the result ------------------------------------
async def test_with_mystic_forge_down_a_requested_simulation_fails_at_the_top(stack: Stack) -> None:
    class Down:
        async def __aenter__(self):
            raise ConnectionError("refused")

        async def __aexit__(self, *exc):
            return False

    token = await linked_user(stack)
    gw = stack.h.app.state.gateway
    real = gw.reports.mf
    gw.reports.mf = MysticForgeProxy("http://mf.test/mcp", client_factory=Down)
    try:
        lst = "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n// Lands\n99 Island\n"
        for name, args in (
            ("run_deck_report", {"deck_ref": "42", "games": 20}),
            ("run_deck_report", {"deck_ref": lst, "games": 20}),
            (
                "compare_decks",
                {"a": lst, "b": lst.replace("99 Island", "98 Island\n1 Forest"), "simulate": True},
            ),
        ):
            res = await call(stack.h, token, name, args)
            out = structured(res)
            assert out["ok"] is False and out["error"] == "simulation_failed", (name, out)
            assert res["isError"] is True, (name, res)
            assert "did not run" in out["message"] and "still valid" in out["message"], out
        # asked for nothing to simulate: the report is a success
        plain = structured(
            await call(stack.h, token, "run_deck_report", {"deck_ref": "42", "simulate": False})
        )
        assert plain["ok"] is True, plain
        cmp = structured(await call(stack.h, token, "compare_decks", {"a": "42", "b": "42"}))
        assert cmp["ok"] is True, cmp
    finally:
        gw.reports.mf = real


async def test_a_report_without_a_simulation_is_not_reused_for_one_that_asks(stack: Stack) -> None:
    token = await linked_user(stack)
    gw = stack.h.app.state.gateway
    ran: list[str] = []

    async def fake_mf(sub, tool, args):
        ran.append(tool)
        return {"tool": tool, "ok": True, "text": "## Metrics\nok"}

    gw.reports._mf = fake_mf
    first = structured(await call(stack.h, token, "run_deck_report", {"deck_ref": "42", "simulate": False}))
    assert first["ok"] is True and "goldfish_run" not in ran, first
    second = structured(await call(stack.h, token, "run_deck_report", {"deck_ref": "42", "simulate": True}))
    # the earlier report had no simulation, so this one runs it instead of reporting a failure
    assert second.get("reused") is not True and "goldfish_run" in ran, second
    assert second.get("error") != "simulation_failed", second
