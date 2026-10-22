"""The deck's own categories: create, delete (empty only, or its cards moved first), delete unused,
and a record for Archidekt's default Sideboard or Maybeboard (Archidekt parity release 1, approved
by Ben 22 Oct 2026 14:04Z, "All as recommended").

Archidekt's requests come from its public site code (reviews/2026-10-22-archidekt-parity/REPORT.md):
POST /decks/createCategory/ {name, includedInDeck, includedInPrice, isPremier, id: null, deck} and
DELETE /decks/category/{id}/. archidekt.com only ever deletes an EMPTY category; what its server does
with one that still holds cards is unknown, so the gateway never sends that (the fake records any
such request in ``category_deletes_with_cards``, which every test here expects to stay empty)."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import pytest

from mtg_gateway import modes
from mtg_gateway.archidekt import ArchidektClient, ArchidektError, Pacer
from mtg_gateway.decks import (
    OPEN_TABS_NOTE,
    DeckError,
    category_line,
    deck_categories,
)

from .conftest import Stack
from .fake_archidekt import FakeArchidekt
from .test_deck_categories import RAMP, SUB, _cats, _filed, _give_categories, _ready, _svc
from .test_decks_and_proxy import call, linked_user, structured  # noqa: F401 - used below


@pytest.fixture(autouse=True)
def _never_delete_a_full_category(stack: Stack):  # noqa: ANN202
    yield
    assert stack.ark.category_deletes_with_cards == []


# -- the client calls and the fake ----------------------------------------------------------------
async def test_create_category_sends_the_forms_body() -> None:
    ark = FakeArchidekt()
    client = ArchidektClient("https://archidekt.com/api", "test", Pacer(0.0), http=ark.client())
    token = (await client.login("alice", "pw-alice"))["access"]
    body = {"name": "Ramp", "includedInDeck": True, "includedInPrice": False, "isPremier": False}
    await client.create_category(token, "42", {**body, "extra": "never sent", "id": 5})
    assert ("POST", "/api/decks/createCategory/") in ark.calls
    assert ark.category_creates == [{**body, "id": None, "deck": 42}]
    assert _cats(ark)["Ramp"]["includedInPrice"] is False


@pytest.mark.parametrize("bad", [{"name": " "}, {"isPremier": "yes"}, {"includedInDeck": None}])
async def test_create_category_refuses_a_bad_body_before_sending(bad: dict) -> None:
    ark = FakeArchidekt()
    client = ArchidektClient("https://archidekt.com/api", "test", Pacer(0.0), http=ark.client())
    token = (await client.login("alice", "pw-alice"))["access"]
    body = {"name": "Ramp", "includedInDeck": True, "includedInPrice": True, "isPremier": False, **bad}
    with pytest.raises(ArchidektError):
        await client.create_category(token, "42", body)
    with pytest.raises(ArchidektError):
        await client.create_category(token, "4x2", {**body, **{"name": "Ok"}})
    assert ark.category_creates == []


async def test_delete_category_sends_a_delete_by_record_id() -> None:
    ark = FakeArchidekt()
    _give_categories(ark)
    ark.decks[42]["categories"].append({"name": "Spare", "isPremier": False, "includedInDeck": True})
    client = ArchidektClient("https://archidekt.com/api", "test", Pacer(0.0), http=ark.client())
    token = (await client.login("alice", "pw-alice"))["access"]
    deck = await client.get_deck(token, "42")
    spare = next(c for c in deck.categories if c["name"] == "Spare")
    await client.delete_category(token, "42", spare["id"])
    assert ("DELETE", f"/api/decks/category/{spare['id']}/") in ark.calls
    assert "Spare" not in _cats(ark)
    for bad in (True, 0, "5"):
        with pytest.raises(ArchidektError):
            await client.delete_category(token, "42", bad)  # type: ignore[arg-type]


# -- proposing: rows, wording, refusals ------------------------------------------------------------
async def test_a_new_category_reads_as_one_line_and_sends_nothing(stack: Stack) -> None:
    await _ready(stack)
    p = await _svc(stack).propose_categories(
        SUB, "42", [{"action": "create", "category": "Removal", "not_in_price": True}]
    )
    assert p["diff"] == "New category “Removal”: Not in price on. " + OPEN_TABS_NOTE
    assert p["diff"] == category_line(p["rows"][0])
    assert stack.ark.category_creates == [] and stack.ark.category_deletes == []


@pytest.mark.parametrize(
    ("changes", "says"),
    [
        ([{"action": "create", "category": "ramp"}], "already has a category named “ramp”"),
        ([{"action": "create", "category": "A, B"}], "comma"),
        ([{"action": "create", "category": ""}], "1 to 60 characters"),
        ([{"action": "create", "category": "X", "rename_to": "Y"}], "unknown field rename_to"),
        ([{"action": "delete", "category": "Ramp", "premier": True}], "unknown field premier"),
        ([{"action": "drop", "category": "Ramp"}], "change, create or delete"),
        (
            [{"action": "create", "category": "Mana"}, {"category": "Ramp", "rename_to": "mana"}],
            "already has a category named “mana”",
        ),
        ([{"action": "delete", "category": "Nope"}], "The deck has no category “Nope”."),
        (
            [{"action": "delete", "category": "Ramp"}],
            "“Ramp” still has 2 cards. Archidekt only deletes an empty category",
        ),
        ([{"action": "delete", "category": "Ramp", "move_cards_to": "Ramp"}], "to itself"),
        ([{"action": "delete", "category": "Ramp", "move_cards_to": "Gone"}], "no category “Gone”"),
        (
            [
                {"action": "delete", "category": "Ramp", "move_cards_to": "Maybeboard"},
                {"action": "delete", "category": "Maybeboard"},
            ],
            "deleted by this change too",
        ),
        (
            [{"action": "delete", "category": "Ramp", "move_cards_to": "Maybeboard"}, {"category": "Ramp"}],
            "listed twice",
        ),
    ],
)
async def test_refused_before_anything_is_proposed(stack: Stack, changes: list, says: str) -> None:
    await _ready(stack)
    svc = _svc(stack)
    before = svc.db.count_pending_proposals(SUB)
    with pytest.raises(DeckError) as exc:
        await svc.propose_categories(SUB, "42", changes)
    assert exc.value.kind == "invalid" and says in str(exc.value)
    assert svc.db.count_pending_proposals(SUB) == before


async def test_a_delete_with_cards_names_the_count_for_the_page(stack: Stack) -> None:
    await _ready(stack)
    with pytest.raises(DeckError) as exc:
        await _svc(stack).propose_categories(SUB, "42", [{"action": "delete", "category": "Ramp"}])
    assert exc.value.extra == {"category": "Ramp", "cards": 2}


async def test_a_delete_that_moves_cards_says_where(stack: Stack) -> None:
    await _ready(stack)
    stack.ark.decks[42]["categories"].append({"name": "Lands", "isPremier": False, "includedInDeck": True})
    p = await _svc(stack).propose_categories(
        SUB, "42", [{"action": "delete", "category": "Ramp", "move_cards_to": "Lands"}]
    )
    assert p["diff"] == "Delete category “Ramp”: its 2 cards move to “Lands” first. " + OPEN_TABS_NOTE
    assert modes.category_wait(p["rows"]) is None  # Lands counts as Ramp did


@pytest.mark.parametrize(
    ("changes", "why"),
    [
        ([{"action": "create", "category": "Legends", "premier": True}], modes.CATEGORY_COMMANDER_REASON),
        ([{"action": "create", "category": "Maybe", "not_in_deck": True}], modes.CATEGORY_COUNTS_REASON),
        (
            [{"action": "delete", "category": "Commander", "move_cards_to": "Ramp"}],
            modes.CATEGORY_COMMANDER_REASON,
        ),
        (
            [{"action": "delete", "category": "Ramp", "move_cards_to": "Maybeboard"}],
            modes.CATEGORY_COUNTS_REASON,
        ),
        # an empty Maybeboard deleted: Archidekt's default board, high like a rename to or from it
        ([{"action": "delete", "category": "Maybeboard"}], modes.CATEGORY_COUNTS_REASON),
        ([{"action": "create", "category": "Removal"}], None),
        ([{"action": "create", "category": "Spare", "not_in_price": True}], None),
    ],
)
async def test_what_waits_for_the_members_press(stack: Stack, changes: list, why: str | None) -> None:
    await _ready(stack)
    p = await _svc(stack).propose_categories(SUB, "42", changes)
    assert modes.category_wait(p["rows"]) == why
    assert modes.risk_of("categories", p["rows"])[0] == "high"


async def test_the_settings_page_asks_before_any_delete(stack: Stack) -> None:
    await _ready(stack)
    stack.ark.decks[42]["categories"].append({"name": "Spare", "isPremier": False, "includedInDeck": True})
    p = await _svc(stack).propose_categories(SUB, "42", [{"action": "delete", "category": "Spare"}])
    assert modes.hand_edit_confirm("categories", p["rows"]) == modes.CATEGORY_DELETE_CONFIRM
    assert modes.auto_waits("categories", p["rows"]) is None  # an empty one goes in Full auto


# -- applying ---------------------------------------------------------------------------------------
async def test_create_applies_with_backup_snapshot_and_read_back(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    before = set(ark.decks)
    p = await svc.propose_categories(
        SUB, "42", [{"action": "create", "category": "Removal", "not_in_price": True}]
    )
    out = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert out["state"] == "applied" and out["result"]["verified"] is True, out
    assert ark.category_creates == [
        {
            "name": "Removal",
            "includedInDeck": True,
            "includedInPrice": False,
            "isPremier": False,
            "id": None,
            "deck": 42,
        }
    ]
    assert _cats(ark)["Removal"]["includedInPrice"] is False
    assert int(out["result"]["backup_deck_id"]) in set(ark.decks) - before
    assert out["result"]["categories_saved"] == ["Removal"]
    assert ark.patches == [] and ark.category_patches == []


async def test_a_create_the_server_drops_is_reported(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ark.category_create_ignored = True
    p = await svc.propose_categories(SUB, "42", [{"action": "create", "category": "Removal"}])
    with pytest.raises(DeckError) as exc:
        await svc.apply(SUB, p["proposal_id"], via="browser")
    assert exc.value.kind == "verify_mismatch" and "“Removal” (category not found)" in str(exc.value)


async def test_a_name_taken_on_archidekt_since_is_caught_before_writing(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    p = await svc.propose_categories(SUB, "42", [{"action": "create", "category": "Removal"}])
    real = svc._backup_shared

    async def meanwhile(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        made = await real(*args, **kwargs)
        ark.decks[42]["categories"].append({"name": "removal", "isPremier": False, "includedInDeck": True})
        return made

    svc._backup_shared = meanwhile
    with pytest.raises(DeckError) as exc:
        await svc.apply(SUB, p["proposal_id"], via="browser")
    assert exc.value.kind == "stale" and ark.category_creates == []


async def test_delete_of_an_empty_category(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ark.decks[42]["categories"].append({"name": "Spare", "isPremier": False, "includedInDeck": True})
    spare = next(c for c in deck_categories(await svc.get_own_deck(SUB, "42")) if c["name"] == "Spare")
    p = await svc.propose_categories(SUB, "42", [{"action": "delete", "id": spare["id"]}])
    assert p["diff"] == "Delete category “Spare”. " + OPEN_TABS_NOTE
    out = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert out["state"] == "applied" and out["result"]["verified"] is True, out
    assert ark.category_deletes == [spare["id"]] and "Spare" not in _cats(ark)
    assert out["result"]["categories_saved"] == ["Spare (deleted)"]


async def test_delete_moves_the_cards_first_then_deletes(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ark.decks[42]["categories"].append({"name": "Lands", "isPremier": False, "includedInDeck": True})
    for row in ark.decks[42]["cards"]:
        if row["card"]["oracleCard"]["name"] == "Cultivate":
            row["categories"] = ["Ramp", "Lands"]  # already in the target too: not filed twice
    p = await svc.propose_categories(
        SUB, "42", [{"action": "delete", "category": "Ramp", "move_cards_to": "lands"}]
    )
    out = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert out["state"] == "applied" and out["result"]["verified"] is True, out
    assert "Ramp" not in _cats(ark) and _filed(ark, "Lands") == sorted(RAMP)
    cultivate = next(r for r in ark.decks[42]["cards"] if r["card"]["oracleCard"]["name"] == "Cultivate")
    assert cultivate["categories"] == ["Lands"]
    # one card write per row, then the delete
    assert len(ark.patches) == 2 and len(ark.category_deletes) == 1


async def test_delete_and_rename_together_keep_the_new_names(stack: Stack) -> None:
    """A card in a deleted category and a renamed one keeps the new name (the moves are worked out
    with this change's renames, so the card write does not bring the old name back)."""
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ark.decks[42]["categories"].append({"name": "Spare", "isPremier": False, "includedInDeck": True})
    for row in ark.decks[42]["cards"]:
        if row["card"]["oracleCard"]["name"] == "Cultivate":
            row["categories"] = ["Spare", "Ramp"]
    p = await svc.propose_categories(
        SUB,
        "42",
        [
            {"category": "Ramp", "rename_to": "Mana"},
            {"action": "delete", "category": "Spare", "move_cards_to": "Ramp"},
        ],
    )
    out = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert out["state"] == "applied" and out["result"]["verified"] is True, out
    assert "Ramp" not in _cats(ark) and "Spare" not in _cats(ark)
    assert _filed(ark, "Mana") == sorted(RAMP)


async def test_a_card_filed_since_the_moves_stops_the_delete(stack: Stack) -> None:
    """The fresh read just before the deletes: a category that is not empty then is not deleted,
    and the read back says so."""
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ark.decks[42]["categories"].append({"name": "Lands", "isPremier": False, "includedInDeck": True})
    p = await svc.propose_categories(
        SUB, "42", [{"action": "delete", "category": "Ramp", "move_cards_to": "Lands"}]
    )
    real = svc._send

    async def and_one_more(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        await real(*args, **kwargs)
        ark.decks[42]["cards"][0]["categories"] = ["Ramp"]  # someone filed a card there meanwhile

    svc._send = and_one_more
    with pytest.raises(DeckError) as exc:
        await svc.apply(SUB, p["proposal_id"], via="browser")
    assert "“Ramp” (still has cards on Archidekt, so it was not deleted)" in str(exc.value)
    assert ark.category_deletes == []


async def test_delete_unused_deletes_every_empty_category_one_at_a_time(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    for name in ("Spare", "Old"):
        ark.decks[42]["categories"].append({"name": name, "isPremier": False, "includedInDeck": True})
    p = await svc.propose_categories(SUB, "42", [], delete_unused=True)
    assert sorted(r["name"] for r in p["rows"]) == ["Maybeboard", "Old", "Spare"]
    out = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert out["state"] == "applied", out
    assert len(ark.category_deletes) == 3 and {"Spare", "Old", "Maybeboard"}.isdisjoint(_cats(ark))
    assert "Ramp" in _cats(ark)
    with pytest.raises(DeckError, match="no unused categories"):
        await svc.propose_categories(SUB, "42", [], delete_unused=True)


# -- Archidekt's default Sideboard / Maybeboard without a record ------------------------------------
async def test_a_category_without_a_record_gets_one_with_the_switches_asked(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ark.category_no_record.add("Maybeboard")
    cats = {c["name"]: c for c in deck_categories(await svc.get_own_deck(SUB, "42"))}
    assert cats["Maybeboard"]["record"] is False and cats["Maybeboard"]["editable"] is True
    p = await svc.propose_categories(SUB, "42", [{"category": "Maybeboard", "not_in_price": False}])
    assert p["diff"] == "Category “Maybeboard”: Not in price off. " + OPEN_TABS_NOTE
    out = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert out["state"] == "applied" and out["result"]["verified"] is True, out
    assert ark.category_creates[0]["name"] == "Maybeboard" and ark.category_patches == []
    assert (
        isinstance(_cats(ark)["Maybeboard"]["id"], int)
        and _cats(ark)["Maybeboard"]["includedInPrice"] is True
    )


async def test_a_category_without_a_record_has_nothing_to_delete(stack: Stack) -> None:
    await _ready(stack)
    stack.ark.category_no_record.add("Maybeboard")
    with pytest.raises(DeckError, match="no record on Archidekt yet"):
        await _svc(stack).propose_categories(SUB, "42", [{"action": "delete", "category": "Maybeboard"}])


# -- restore -----------------------------------------------------------------------------------------
async def test_restore_makes_a_deleted_category_again_with_its_cards(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ark.decks[42]["categories"].append({"name": "Lands", "isPremier": False, "includedInDeck": True})
    _cats(ark)["Ramp"]["includedInPrice"] = False
    p = await svc.propose_categories(
        SUB, "42", [{"action": "delete", "category": "Ramp", "move_cards_to": "Lands"}]
    )
    done = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert done["state"] == "applied"
    r = await svc.propose_restore(SUB, done["result"]["snapshot_id"])
    assert (
        "New category “Ramp” (deleted since the snapshot): Not in price on. " + OPEN_TABS_NOTE
        in r["diff"].splitlines()
    )
    back = await svc.apply(SUB, r["proposal_id"], via="browser")
    assert back["state"] == "applied" and back["result"]["verified"] is True, back
    assert _cats(ark)["Ramp"]["includedInPrice"] is False and _filed(ark, "Ramp") == sorted(RAMP)


async def test_restore_deletes_a_category_added_since_once_it_is_empty(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    p = await svc.propose_categories(SUB, "42", [{"action": "create", "category": "Removal"}])
    done = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert done["state"] == "applied"
    r = await svc.propose_restore(SUB, done["result"]["snapshot_id"])
    assert "Delete category “Removal” (added since the snapshot). " + OPEN_TABS_NOTE in r["diff"].splitlines()
    back = await svc.apply(SUB, r["proposal_id"], via="browser")
    assert back["state"] == "applied" and back["result"]["verified"] is True, back
    assert "Removal" not in _cats(ark)


# -- the assistant's path: the same tool creates and deletes ----------------------------------------
async def test_the_assistant_creates_deletes_and_deletes_unused_with_the_same_tool(stack: Stack) -> None:
    from .test_approval_modes import _set_mode

    h, ark = stack.h, stack.ark
    token = await _ready(stack, own=False)
    await _set_mode(h, "auto")
    ark.decks[42]["categories"].append({"name": "Spare", "isPremier": False, "includedInDeck": True})
    p = structured(
        await call(
            h,
            token,
            "propose_deck_categories",
            {
                "deck_id": "42",
                "changes": [
                    {"action": "create", "category": "Removal"},
                    {"action": "delete", "category": "Ramp", "move_cards_to": "Removal"},
                ],
            },
        )
    )
    assert p["ok"] and p["kind"] == "categories" and p["assistant_may_apply"] is True, p
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] and out["state"] == "applied", out
    assert [c["name"] for c in ark.category_creates] == ["Removal"]
    assert "Ramp" not in {c["name"] for c in ark.decks[42]["categories"]}
    # delete_unused alone: Spare (empty, plain) goes; Maybeboard is a default board and waits
    p = structured(await call(h, token, "propose_deck_categories", {"deck_id": "42", "delete_unused": True}))
    assert p["ok"] and p["assistant_may_apply"] is False, p
    assert "Delete category “Spare”" in p["diff"] and "Delete category “Maybeboard”" in p["diff"]
    # a delete of a category with cards and nowhere to move them is refused, in the backend's words
    p = structured(
        await call(
            h,
            token,
            "propose_deck_categories",
            {"deck_id": "42", "changes": [{"action": "delete", "category": "Removal"}]},
        )
    )
    assert p["ok"] is False and "only deletes an empty category" in p["message"], p


# -- the security review's findings (release/2026-10-22-0.7.42/security-review) ---------------------
@pytest.mark.parametrize("target", ["Commander", "Legends"])
async def test_cards_moved_into_a_commander_category_wait_for_the_press(stack: Stack, target: str) -> None:
    """S-01: a delete whose cards move into Commander or a Premier category makes them commanders, so
    it is a commander change: it waits in every mode and the page asks with the commander sentence."""
    await _ready(stack)
    stack.ark.decks[42]["categories"].append(
        {"name": "Legends", "isPremier": True, "includedInDeck": True, "includedInPrice": True}
    )
    p = await _svc(stack).propose_categories(
        SUB, "42", [{"action": "delete", "category": "Ramp", "move_cards_to": target}]
    )
    assert modes.category_wait(p["rows"]) == modes.CATEGORY_COMMANDER_REASON
    assert modes.auto_waits("categories", p["rows"]) == modes.CATEGORY_COMMANDER_REASON
    assert modes.hand_edit_confirm("categories", p["rows"]) == modes.CATEGORY_COMMANDER_CONFIRM


async def test_each_delete_reads_the_deck_again_just_before(stack: Stack) -> None:
    """Security review 0.7.42 S-05: the deletes read the deck once and then sent every DELETE in
    turn, so a card filed under a later category in between (on archidekt.com, a collaborator,
    another tab) was not seen and that category was deleted with its cards."""
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    for name in ("Old", "Spare"):
        ark.decks[42]["categories"].append({"name": name, "isPremier": False, "includedInDeck": True})
    p = await svc.propose_categories(SUB, "42", [], delete_unused=True)
    real = svc.client.delete_category
    sent: list[int] = []

    async def delete_then_someone_files(token, deck_id, cid):  # noqa: ANN001, ANN202
        await real(token, deck_id, cid)
        sent.append(cid)
        if len(sent) == 1:  # after the first DELETE, before the next: a card is filed under "Spare"
            ark.decks[42]["cards"][0]["categories"] = ["Spare"]

    svc.client.delete_category = delete_then_someone_files
    try:
        await svc.apply(SUB, p["proposal_id"], via="browser")
    except Exception:  # noqa: BLE001, S110 - the apply may report the category it left; the fake's record is what counts
        pass
    assert ark.category_deletes_with_cards == [], ark.category_deletes_with_cards
    assert "Spare" in _cats(ark) and "Old" not in _cats(ark), list(_cats(ark))
    assert len(sent) == len(p["rows"]) - 1, sent  # every other unused category went


async def test_a_restore_deletes_only_the_categories_its_review_listed(stack: Stack) -> None:
    """Security review 0.7.42 S-06: a restore worked its category plan out again when applied,
    so an empty category made after the review (on archidekt.com) was deleted though no reviewed
    row said so."""
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    done = await svc.apply(SUB, p["proposal_id"], via="browser")
    r = await svc.propose_restore(SUB, done["result"]["snapshot_id"])
    assert "Removal" not in r["diff"]
    ark.decks[42]["categories"].append(
        {"id": 79999, "name": "Removal", "isPremier": False, "includedInDeck": True, "includedInPrice": False}
    )
    await svc.apply(SUB, r["proposal_id"], via="browser")
    assert 79999 not in ark.category_deletes and "Removal" in _cats(ark)
    assert "Ramp" in _cats(ark) and "Mana" not in _cats(ark)
