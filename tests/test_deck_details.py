"""Deck details proposals (name, format, bracket, visibility) and the set_category and
set_commander change actions, through the MCP tools and the browser review page."""

from __future__ import annotations

from pathlib import Path

import pytest

from mtg_gateway.archidekt import DeckCard
from mtg_gateway.decks import Change, DeckError, parse_changes, parse_details

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, Stack, _client, call, linked_user, structured

AMY = {"sub": "user-2", "email": "amy@example.test", "name": "Amy", "preferred_username": "amy", "groups": []}


@pytest.fixture
async def stack(tmp_path: Path, idp: FakeIdP):
    """Writes on, applies allowed over MCP (as test_decks_and_proxy.stack, without the proxy)."""
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, apply_via_mcp=True, archidekt_base="https://ark.test/api"
    )
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h:
        yield Stack(h, ark)


def _rows(ark, deck_id: int) -> dict[str, dict]:
    return {c["card"]["oracleCard"]["name"]: c for c in ark.decks[deck_id]["cards"]}


# -- details proposals ----------------------------------------------------------
async def test_propose_details_then_apply_changes_and_verifies(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    details = {"name": "Aesi Lands", "deck_format": "modern", "edh_bracket": 3, "private": True}
    p = structured(await call(h, token, "propose_deck_details", {"deck_id": "42", "details": details}))
    assert p["ok"] and p["state"] == "pending" and p["kind"] == "details", p
    assert p["deck_id"] == "42" and p["deck_name"] == "Sample Commander Deck"
    assert set(p["diff"].splitlines()) == {
        'name: "Sample Commander Deck" -> "Aesi Lands"',
        "format: unknown -> modern",
        "bracket: none -> 3",
        "private: no -> yes",
    }, p["diff"]
    assert p["changes"] == details
    assert "these details" in p["next_step"]
    assert ark.updates == [] and ark.patches == []  # nothing written yet

    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"], a
    assert a["state"] == "applied" and a["result"]["verified"] is True and a["snapshot_id"]
    assert a["result"]["fields"] == ["deck_format", "edh_bracket", "name", "private"]
    assert a["next_step"] == "Done. The deck's details on Archidekt match this proposal."
    assert ark.patches == []  # no card rows were touched
    # One update for the deck itself; the other PATCH names the backup copy made first.
    assert [u for u in ark.updates if u["deck_id"] == 42] == [
        {"deck_id": 42, "name": "Aesi Lands", "deckFormat": 2, "edhBracket": 3, "private": True}
    ]
    backup_id = int(a["result"]["backup_deck_id"])
    assert ark.decks[backup_id]["name"].startswith("Sample Commander Deck (backup ")
    assert [u["deck_id"] for u in ark.updates] == [backup_id, 42]  # backup before the change
    deck = ark.decks[42]
    assert deck["name"] == "Aesi Lands" and deck["deckFormat"] == 2 and deck["edhBracket"] == 3
    assert deck["private"] is True and 42 in ark.private
    snap = h.db.get_snapshot(a["snapshot_id"], "user-1")
    assert snap and snap["deck"]["name"] == "Sample Commander Deck"  # pre-change state kept

    mine = structured(await call(h, token, "get_my_deck", {"deck_id": "42"}))
    assert mine["ok"] and mine["name"] == "Aesi Lands"

    # Clearing the bracket and making the deck public again is its own proposal.
    p2 = structured(
        await call(
            h,
            token,
            "propose_deck_details",
            {"deck_id": "42", "details": {"edh_bracket": None, "private": False, "description": "x" * 120}},
        )
    )
    assert p2["ok"], p2
    assert set(p2["diff"].splitlines()) == {
        "bracket: 3 -> none",
        "private: yes -> no",
        "description: (changed, 120 chars)",
    }
    assert "xxxx" not in p2["diff"]
    a2 = structured(await call(h, token, "apply_proposal", {"proposal_id": p2["proposal_id"]}))
    assert a2["ok"] and a2["result"]["verified"] is True, a2
    assert ark.decks[42]["edhBracket"] is None and ark.decks[42]["description"] == "x" * 120
    assert 42 not in ark.private


async def test_details_noop_and_bad_values_are_refused(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)

    async def refused(details: object) -> str:
        out = structured(await call(h, token, "propose_deck_details", {"deck_id": "42", "details": details}))
        assert out["ok"] is False and out["error"] == "invalid", out
        return out["message"]

    # The deck already has this name, is public and has no bracket: nothing would change.
    msg = await refused({"name": "Sample Commander Deck", "private": False, "edh_bracket": None})
    assert "already has these details" in msg
    assert "deck_format must be one of" in await refused({"deck_format": "cube"})
    assert "edh_bracket" in await refused({"edh_bracket": 6})
    assert "edh_bracket" in await refused({"edh_bracket": True})
    assert "name must be" in await refused({"name": ""})
    assert "name must be" in await refused({"name": "n" * 201})
    assert "description" in await refused({"description": "d" * 20_001})
    assert "private must be" in await refused({"private": "yes"})
    assert "unknown details: colour" in await refused({"colour": "green", "name": "x"})
    assert "non-empty object" in await refused({})
    assert ark.updates == [] and ark.patches == []
    assert structured(await call(h, token, "list_my_proposals"))["proposals"] == []

    # A no-op that only becomes one at apply time (the user renamed the deck by hand) is caught by
    # the stale check, since Archidekt bumps updatedAt.
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"name": "New"}})
    )
    assert p["ok"]
    ark.decks[42]["name"] = "New"
    ark.decks[42]["updatedAt"] = "2026-10-02T00:00:00Z"
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] is False and a["error"] == "stale" and ark.updates == []


async def test_details_apply_reports_a_mismatch_when_archidekt_keeps_the_old_values(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"name": "Renamed"}})
    )
    assert p["ok"], p
    # Archidekt answers 200 but does not store the change.
    original = ark.handle

    def ignoring(request):
        resp = original(request)
        if request.url.path.endswith("/update/"):
            ark.decks[42]["name"] = "Sample Commander Deck"
        return resp

    ark.transport.handler = ignoring
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] is False and a["error"] == "verify_mismatch" and "name" in a["message"], a
    row = h.db.get_proposal(p["proposal_id"], "user-1")
    assert row["state"] == "failed" and row["snapshot_id"] and row["result"]["error"] == "verify_mismatch"


async def test_details_proposal_is_refused_for_decks_the_account_does_not_own(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.private.discard(43)  # Amy's deck, now public: readable, still not Alice's to edit
    out = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "43", "details": {"name": "Mine now"}})
    )
    assert out["ok"] is False and out["error"] == "forbidden"
    out = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "https://evil.test/decks/42", "details": {}})
    )
    assert out["ok"] is False and out["error"] == "invalid"


async def test_details_proposal_shows_on_the_review_page_and_only_to_its_owner(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_details",
            {"deck_id": "42", "details": {"name": "Aesi <Lands>", "unlisted": True, "description": "hi"}},
        )
    )
    assert p["ok"], p
    assert p["review_url"] == f"{GATEWAY}/proposals/{p['proposal_id']}"
    b = Browser(h)
    await b.login()
    page = await b.http.get(f"/proposals/{p['proposal_id']}")
    assert page.status_code == 200, page.text
    assert "Deck details that will change" in page.text
    assert "3 deck details changed; no cards change" in page.text
    assert "&quot;Aesi &lt;Lands&gt;&quot;" in page.text and "<Lands>" not in page.text
    assert "<span class='act'>unlisted</span>" in page.text
    assert "(changed, 2 chars)" in page.text
    assert "Net " not in page.text and "Card changes" not in page.text
    assert "Apply these changes to Archidekt" in page.text

    # Applying from the review page works as for a card edit.
    r = await b.http.post(f"/proposals/{p['proposal_id']}", data={"csrf": await b.csrf(), "action": "apply"})
    assert r.status_code == 303 and "ok=" in r.headers["location"], r.headers
    assert ark.decks[42]["name"] == "Aesi <Lands>" and ark.decks[42]["unlisted"] is True
    await b.aclose()

    # Another user sees nothing of it.
    other = await linked_user(stack, AMY, "amy", "pw-amy")
    assert structured(await call(h, other, "list_my_proposals"))["proposals"] == []
    out = structured(await call(h, other, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "not_found"
    b2 = Browser(h)
    await b2.login()
    assert (await b2.http.get(f"/proposals/{p['proposal_id']}")).status_code == 404
    await b2.aclose()


def test_parse_details_normalises_the_format() -> None:
    assert parse_details({"deck_format": " EDH "}) == {"deck_format": "edh"}
    with pytest.raises(DeckError):
        parse_details(["name"])


# -- set_category and set_commander ----------------------------------------------
async def test_set_category_and_set_commander_proposed_applied_and_verified(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    before = _rows(ark, 42)
    assert before["Sol Ring"]["categories"] is None  # the fixture keeps only Commander
    assert before["Aesi, Tyrant of Gyre Strait"]["categories"] == ["Commander"]
    changes = [
        {"action": "set_category", "card_name": "sol ring", "category": "Artifacts"},
        {"action": "set_commander", "card_name": "Rampant Growth"},
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert p["ok"] and p["kind"] == "edit", p
    assert set(p["diff"].splitlines()) == {
        "Sol Ring: category (none) -> Artifacts",
        "Commander: Aesi, Tyrant of Gyre Strait -> Rampant Growth",
    }, p["diff"]
    assert p["changes"] == [
        {"action": "set_category", "card_name": "sol ring", "category": "Artifacts"},
        {"action": "set_commander", "card_name": "Rampant Growth", "category": "Commander"},
    ]
    assert ark.patches == []

    b = Browser(h)
    await b.login()
    page = await b.http.get(f"/proposals/{p['proposal_id']}")
    assert "2 recategorised" in page.text and "Net 0 cards" in page.text
    assert "<span class='act'>Category</span><span class='name'>Sol Ring</span>" in page.text
    assert "<span class='act'>Commander</span><span class='name'>Rampant Growth</span>" in page.text
    await b.aclose()

    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["state"] == "applied" and a["result"]["verified"] is True, a
    assert a["result"]["sent_entries"] == 3
    sent = [patch["cards"][0] for patch in ark.patches]
    assert all(e["action"] == "modify" and e["modifications"]["quantity"] == 1 for e in sent)
    by_rel = {e["deckRelationId"]: e for e in sent}
    assert by_rel[before["Sol Ring"]["id"]]["categories"] == ["Artifacts"]
    assert by_rel[before["Rampant Growth"]["id"]]["categories"] == ["Commander"]
    assert by_rel[before["Aesi, Tyrant of Gyre Strait"]["id"]]["categories"] == []
    assert set(sent[0]) == {"action", "cardid", "deckRelationId", "patchId", "categories", "modifications"}
    after = _rows(ark, 42)
    assert after["Sol Ring"]["categories"] == ["Artifacts"]
    assert after["Rampant Growth"]["categories"] == ["Commander"]
    assert after["Aesi, Tyrant of Gyre Strait"]["categories"] is None
    assert sum(c["quantity"] for c in ark.decks[42]["cards"]) == 100
    snap = h.db.get_snapshot(a["snapshot_id"], "user-1")
    assert snap and snap["deck"]["cards"][1]["categories"] == ["Commander"]

    # Partners: two set_commander changes in one proposal; the old commander is demoted.
    p2 = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "set_commander", "card_name": "Sol Ring"},
                    {"action": "set_commander", "card_name": "Acidic Slime"},
                ],
            },
        )
    )
    assert p2["ok"], p2
    assert p2["diff"] == "Commander: Rampant Growth -> Acidic Slime, Sol Ring"
    a2 = structured(await call(h, token, "apply_proposal", {"proposal_id": p2["proposal_id"]}))
    assert a2["ok"] and a2["result"]["verified"] is True, a2
    after = _rows(ark, 42)
    commanders = sorted(n for n, c in after.items() if "Commander" in (c["categories"] or []))
    assert commanders == ["Acidic Slime", "Sol Ring"]
    assert after["Rampant Growth"]["categories"] is None

    # Setting the commander to what it already is changes nothing and is refused.
    out = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "set_commander", "card_name": "Sol Ring"},
                    {"action": "set_commander", "card_name": "Acidic Slime"},
                ],
            },
        )
    )
    assert out["ok"] is False and out["error"] == "invalid" and "exactly as it is" in out["message"]


async def test_category_changes_mix_with_count_changes_and_are_verified(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    changes = [
        {"action": "add", "card_name": "Arcane Signet", "category": "Ramp"},
        {"action": "set_category", "card_name": "Sol Ring", "category": "Ramp"},
    ]
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert p["ok"] and set(p["diff"].splitlines()) == {
        "+1 Arcane Signet [Ramp]",  # the category an add sends is shown in the diff
        "Sol Ring: category (none) -> Ramp",
    }
    ark.fail_patch_silently = True
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] is False and a["error"] == "verify_mismatch", a
    assert "Arcane Signet" in a["message"] and "Sol Ring" in a["message"]
    row = h.db.get_proposal(p["proposal_id"], "user-1")
    assert row["state"] == "failed" and row["result"]["error"] == "verify_mismatch"

    ark.fail_patch_silently = False
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    a = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert a["ok"] and a["result"]["verified"] is True, a
    after = _rows(ark, 42)
    assert after["Sol Ring"]["categories"] == ["Ramp"] and after["Arcane Signet"]["categories"] == ["Ramp"]


async def test_category_changes_are_validated(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)

    async def refused(changes: list[dict]) -> str:
        out = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
        assert out["ok"] is False and out["error"] == "invalid", out
        return out["message"]

    assert "not in the deck" in await refused(
        [{"action": "set_category", "card_name": "Arcane Signet", "category": "Ramp"}]
    )
    assert "not in the deck" in await refused([{"action": "set_commander", "card_name": "Opt"}])
    assert "needs a category" in await refused([{"action": "set_category", "card_name": "Sol Ring"}])
    assert "needs a category" in await refused(
        [{"action": "set_category", "card_name": "Sol Ring", "category": "c" * 61}]
    )
    assert "takes only card_name" in await refused(
        [{"action": "set_category", "card_name": "Sol Ring", "category": "Ramp", "quantity": 2}]
    )
    assert "takes only card_name" in await refused(
        [{"action": "set_commander", "card_name": "Sol Ring", "set_code": "cmr", "collector_number": "1"}]
    )
    assert "always files" in await refused(
        [{"action": "set_commander", "card_name": "Sol Ring", "category": "Ramp"}]
    )
    assert "one set_category or set_commander per card" in await refused(
        [
            {"action": "set_category", "card_name": "Sol Ring", "category": "Ramp"},
            {"action": "set_commander", "card_name": "sol ring"},
        ]
    )
    assert "same proposal" in await refused(
        [
            {"action": "remove", "card_name": "Sol Ring"},
            {"action": "set_category", "card_name": "Sol Ring", "category": "Ramp"},
        ]
    )
    assert ark.patches == [] and ark.updates == []
    assert structured(await call(h, token, "list_my_proposals"))["proposals"] == []


def test_parse_changes_keeps_category_actions_simple() -> None:
    out = parse_changes(
        [
            {"action": "Set_Category", "card_name": " Sol Ring ", "category": " Ramp "},
            {"action": "set_commander", "card_name": "Aesi, Tyrant of Gyre Strait", "quantity": 0},
        ]
    )
    assert out == [
        Change("set_category", "Sol Ring", category="Ramp"),
        Change("set_commander", "Aesi, Tyrant of Gyre Strait", category="Commander"),
    ]
    assert out[0].as_dict() == {"action": "set_category", "card_name": "Sol Ring", "category": "Ramp"}


def test_category_plan_uses_relation_ids() -> None:
    from mtg_gateway.archidekt import Deck
    from mtg_gateway.decks import build_payload, category_plan

    def card(rel: int, name: str, cats: list[str]) -> DeckCard:
        return DeckCard(rel, 100 + rel, name, 1, cats, "Normal", "cmr", str(rel))

    deck = Deck(
        id="1",
        name="d",
        owner="o",
        updated_at="",
        cards=[
            card(1, "Aesi, Tyrant of Gyre Strait", ["Commander", "Creature"]),
            card(2, "Sol Ring", ["Ramp"]),
            card(3, "Sol Ring", ["Maybeboard"]),
            card(4, "Forest", []),
        ],
        categories=[{"name": "Maybeboard", "includedInDeck": False}],
        raw={},
    )
    want, lines = category_plan(
        deck,
        parse_changes(
            [
                {"action": "set_category", "card_name": "Sol Ring", "category": "Artifacts"},
                {"action": "set_commander", "card_name": "Forest"},
            ]
        ),
    )
    # Only the deck-proper row of Sol Ring moves; the maybeboard copy stays out of the deck.
    # Aesi keeps Creature and loses Commander.
    assert want == {2: ["Artifacts"], 4: ["Commander"], 1: ["Creature"]}
    assert lines == [
        "Sol Ring: category Ramp -> Artifacts",
        "Commander: Aesi, Tyrant of Gyre Strait -> Forest",
    ]
    payload = build_payload(deck, deck.counts_by_name(), {}, recategorise=want)
    assert [(e["deckRelationId"], e["categories"], e["modifications"]["quantity"]) for e in payload] == [
        (1, ["Creature"], 1),
        (2, ["Artifacts"], 1),
        (4, ["Commander"], 1),
    ]
    # A card whose only rows are on the maybeboard is not "in the deck" for category changes.
    maybe_only = Deck(
        id="1",
        name="d",
        owner="o",
        updated_at="",
        cards=[card(3, "Sol Ring", ["Maybeboard"])],
        categories=[{"name": "Maybeboard", "includedInDeck": False}],
        raw={},
    )
    with pytest.raises(DeckError, match="not in the deck"):
        change = {"action": "set_category", "card_name": "Sol Ring", "category": "Ramp"}
        category_plan(maybe_only, parse_changes([change]))
