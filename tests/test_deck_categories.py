"""The deck's own categories: rename and the flags Premier, Not in deck and Not in price (kind
"categories", UI overhaul package C, approved by Ben 21 Oct 2026 23:49Z).

Archidekt's request is taken from its deck editor's public code (CATEGORIES-RESEARCH.md): PATCH
/decks/category/{id}/ with the whole category. The fake behaves as that research expects (a rename
in place, cards follow, flags kept); the assumptions only a live test can confirm are pinned here
by the gateway's own checks either way (a rename onto a taken name is refused before sending, and
the deck is read back after every write)."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import pytest

from mtg_gateway import modes, recheck
from mtg_gateway.archidekt import ArchidektClient, ArchidektError, Pacer
from mtg_gateway.decks import (
    BROWSER_CLIENT,
    OPEN_TABS_NOTE,
    DeckError,
    category_line,
    current_client,
    deck_categories,
)

from .conftest import Stack
from .fake_archidekt import FakeArchidekt
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import call, linked_user, structured

SUB = "user-1"  # the signed-in member of linked_user
RAMP = ("Coiling Oracle", "Cultivate")


def _cats(ark: FakeArchidekt, deck_id: int = 42) -> dict[str, dict]:
    return {c["name"]: c for c in ark.decks[deck_id]["categories"]}


def _filed(ark: FakeArchidekt, name: str, deck_id: int = 42) -> list[str]:
    return sorted(
        c["card"]["oracleCard"]["name"]
        for c in ark.decks[deck_id]["cards"]
        if name in (c.get("categories") or [])
    )


def _give_categories(ark: FakeArchidekt) -> None:
    """Deck 42 with a Ramp category holding two cards and a Maybeboard (not in deck or price)."""
    deck = ark.decks[42]
    deck["categories"] += [
        {"name": "Ramp", "isPremier": False, "includedInDeck": True, "includedInPrice": True},
        {"name": "Maybeboard", "isPremier": False, "includedInDeck": False, "includedInPrice": False},
    ]
    for row in deck["cards"]:
        if row["card"]["oracleCard"]["name"] in RAMP:
            row["categories"] = ["Ramp"]


async def _ready(stack: Stack, *, own: bool = True) -> str:
    token = await linked_user(stack)
    _give_categories(stack.ark)
    if own:
        current_client.set(BROWSER_CLIENT)  # the member's own deck settings page
    return token


def _svc(stack: Stack):  # noqa: ANN202
    return stack.h.app.state.gateway.decks


# -- the client call and the fake ---------------------------------------------------------------
async def test_update_category_sends_the_editors_whole_body_by_record_id() -> None:
    ark = FakeArchidekt()
    _give_categories(ark)
    client = ArchidektClient("https://archidekt.com/api", "test", Pacer(0.0), http=ark.client())
    token = (await client.login("alice", "pw-alice"))["access"]
    deck = await client.get_deck(token, "42")
    ramp = next(c for c in deck.categories if c["name"] == "Ramp")
    body = {
        "name": "Mana",
        "includedInDeck": True,
        "includedInPrice": False,
        "isPremier": False,
        "id": ramp["id"],
    }
    await client.update_category(token, "42", {**body, "extra": "never sent"})
    assert ("PATCH", f"/api/decks/category/{ramp['id']}/") in ark.calls
    assert ark.category_patches == [{"deck_id": 42, **body}]
    assert _filed(ark, "Mana") == sorted(RAMP) and _filed(ark, "Ramp") == []
    assert _cats(ark)["Mana"]["includedInPrice"] is False and _cats(ark)["Mana"]["id"] == ramp["id"]


@pytest.mark.parametrize(
    "bad",
    [
        {"id": None},
        {"id": True},
        {"name": " "},
        {"isPremier": "yes"},
        {"includedInPrice": None},
    ],
)
async def test_update_category_refuses_a_bad_body_before_sending(bad: dict) -> None:
    ark = FakeArchidekt()
    client = ArchidektClient("https://archidekt.com/api", "test", Pacer(0.0), http=ark.client())
    token = (await client.login("alice", "pw-alice"))["access"]
    body = {
        "name": "Mana",
        "includedInDeck": True,
        "includedInPrice": True,
        "isPremier": False,
        "id": 5,
        **bad,
    }
    with pytest.raises(ArchidektError):
        await client.update_category(token, "42", body)
    assert [c for c in ark.calls if "category" in c[1]] == []


# -- proposing: rows, wording, refusals ------------------------------------------------------------
async def test_the_review_line_names_the_change_in_the_settings_pages_words(stack: Stack) -> None:
    await _ready(stack)
    p = await _svc(stack).propose_categories(
        SUB, "42", [{"category": "Ramp", "rename_to": "Mana", "not_in_price": True}]
    )
    assert p["kind"] == "categories" and len(p["rows"]) == 1
    line = "Category “Ramp”: rename to “Mana” (its 2 cards go with it), Not in price on. " + OPEN_TABS_NOTE
    assert p["diff"] == line == category_line(p["rows"][0])
    flags = _svc(stack).describe(SUB, p["proposal_id"])
    assert flags["diff"] == line  # the same line on the review page, the card, Changes and History
    assert stack.ark.category_patches == []  # proposing sends nothing


async def test_flag_only_lines_carry_the_open_tabs_note_too(stack: Stack) -> None:
    # any category change, not only a rename, is unseen by an Archidekt tab already open on the deck
    await _ready(stack)
    p = await _svc(stack).propose_categories(SUB, "42", [{"category": "Maybeboard", "not_in_price": False}])
    assert p["diff"] == "Category “Maybeboard”: Not in price off. " + OPEN_TABS_NOTE
    assert "old name" not in OPEN_TABS_NOTE and "old categories" in OPEN_TABS_NOTE


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"category": "Ramp", "rename_to": "maybeboard"}, "already has a category named “maybeboard”"),
        ({"category": "Ramp", "rename_to": "Commander"}, "already has a category named “Commander”"),
        ({"category": "Nope", "rename_to": "X"}, "The deck has no category “Nope”."),
        ({"category": "Ramp", "not_in_price": False}, "nothing would change"),
        ({"category": "Ramp", "rename_to": "a" * 61}, "1 to 60 characters"),
        ({"category": "Ramp", "rename_to": "Ramp, Rocks"}, "comma"),
        ({"category": "Ramp", "premier": "yes"}, "true or false"),
    ],
)
async def test_refused_before_anything_is_proposed_or_sent(stack: Stack, change: dict, says: str) -> None:
    await _ready(stack)
    svc = _svc(stack)
    before = svc.db.count_pending_proposals(SUB)
    with pytest.raises(DeckError) as exc:
        await svc.propose_categories(SUB, "42", [change])
    assert exc.value.kind == "invalid" and says in str(exc.value)
    assert svc.db.count_pending_proposals(SUB) == before and stack.ark.category_patches == []


async def test_two_renames_onto_one_name_are_refused(stack: Stack) -> None:
    await _ready(stack)
    with pytest.raises(DeckError, match="already has a category named"):
        await _svc(stack).propose_categories(
            SUB,
            "42",
            [{"category": "Ramp", "rename_to": "Rocks"}, {"category": "Maybeboard", "rename_to": "rocks"}],
        )


async def test_another_members_deck_is_refused(stack: Stack) -> None:
    await _ready(stack)
    with pytest.raises(DeckError) as exc:
        await _svc(stack).propose_categories(SUB, "43", [{"category": "Commander", "rename_to": "Boss"}])
    assert exc.value.kind in ("forbidden", "not_found")


async def test_a_category_without_a_record_is_given_one_and_renamed(stack: Stack) -> None:
    """Archidekt's default Sideboard and Maybeboard may have no record until someone creates one
    (0.7.41 refused them). Since 0.7.42 the change creates the record under the name its cards
    are filed under, then renames it by the id Archidekt gave it."""
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ark.category_no_record.add("Ramp")
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    out = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert out["state"] == "applied" and out["result"]["verified"] is True, out
    assert [c["name"] for c in ark.category_creates] == ["Ramp"]
    assert [b["name"] for b in ark.category_patches] == ["Mana"]
    assert _filed(ark, "Mana") == sorted(RAMP) and "Ramp" not in _cats(ark)


# -- applying: backup, snapshot, one call at a time, read back ---------------------------------------
async def test_the_members_save_applies_with_backup_snapshot_and_read_back(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ramp_id = (
        svc.db
        and next(c for c in deck_categories(await svc.get_own_deck(SUB, "42")) if c["name"] == "Ramp")["id"]
    )
    before = set(ark.decks)
    p = await svc.propose_categories(
        SUB,
        "42",
        [{"category": "Ramp", "rename_to": "Mana"}, {"category": "Maybeboard", "not_in_price": False}],
    )
    out = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert out["state"] == "applied", out
    result = out["result"]
    assert result["verified"] is True and result["categories_saved"] == ["Mana", "Maybeboard"]
    # renamed in place: the same record, its cards with it, flags kept
    assert _cats(ark)["Mana"]["id"] == ramp_id and "Ramp" not in _cats(ark)
    assert _filed(ark, "Mana") == sorted(RAMP)
    assert _cats(ark)["Maybeboard"]["includedInPrice"] is True
    # one write per category, each the whole body, and no card write
    assert [b["name"] for b in ark.category_patches] == ["Mana", "Maybeboard"]
    assert ark.patches == []
    # a backup copy first (backups are on), and a snapshot with every category's record
    assert result.get("backup_deck_id") and int(result["backup_deck_id"]) in set(ark.decks) - before
    snap = svc.db.get_snapshot(result["snapshot_id"], SUB)
    kept = {c["name"]: c for c in snap["deck"]["categories"]}
    assert kept["Ramp"]["id"] == ramp_id and kept["Maybeboard"]["includedInPrice"] is False
    assert sorted(
        c["card"]["oracleCard"]["name"]
        for c in snap["deck"]["cards"]
        if "Ramp" in (c.get("categories") or [])
    ) == sorted(RAMP)


async def test_a_deck_changed_since_is_refused_with_nothing_sent(stack: Stack) -> None:
    """The stale-deck guard: a card changed on Archidekt since (the fingerprint) stops it."""
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    ark.decks[42]["cards"][0]["quantity"] += 1
    ark.decks[42]["updatedAt"] = "2026-10-09T00:00:00Z"
    with pytest.raises(DeckError) as exc:
        await svc.apply(SUB, p["proposal_id"], via="browser")
    assert exc.value.kind == "stale" and ark.category_patches == []
    assert svc.db.get_proposal(p["proposal_id"], SUB)["status"] == "needs_look"


async def test_a_flag_changed_on_archidekt_since_is_refused_too(stack: Stack) -> None:
    """Flags are not in the card fingerprint, so the categories recorded with the change are
    compared just before the apply writes anything."""
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    _cats(ark)["Maybeboard"]["includedInPrice"] = True  # changed on Archidekt, updatedAt kept
    with pytest.raises(DeckError) as exc:
        await svc.apply(SUB, p["proposal_id"], via="browser")
    assert exc.value.kind == "stale" and ark.category_patches == []


async def test_a_change_during_the_backup_is_caught_before_writing(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    real = svc._backup_shared

    async def meanwhile(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        made = await real(*args, **kwargs)
        _cats(ark)["Ramp"]["name"] = "Rocks"  # renamed on Archidekt while the copy was made
        for row in ark.decks[42]["cards"]:
            if row.get("categories") == ["Ramp"]:
                row["categories"] = ["Rocks"]
        return made

    svc._backup_shared = meanwhile
    with pytest.raises(DeckError) as exc:
        await svc.apply(SUB, p["proposal_id"], via="browser")
    assert exc.value.kind == "stale" and ark.category_patches == []


async def test_cards_that_do_not_follow_a_rename_are_reported(stack: Stack) -> None:
    """Only a live test can show that the cards follow the new name. If they do not, the read back
    says so and the snapshot is kept; nothing claims it worked."""
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ark.category_rename_leaves_cards = True
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    with pytest.raises(DeckError) as exc:
        await svc.apply(SUB, p["proposal_id"], via="browser")
    assert exc.value.kind == "verify_mismatch"
    assert "cards that did not follow: Coiling Oracle, Cultivate" in str(exc.value)
    row = svc.db.get_proposal(p["proposal_id"], SUB)
    assert row["state"] == "failed" and row["result"]["snapshot_id"]


async def test_flags_the_server_did_not_keep_are_reported(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ark.category_flags_ignored = True
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "not_in_price": True}])
    with pytest.raises(DeckError) as exc:
        await svc.apply(SUB, p["proposal_id"], via="browser")
    assert exc.value.kind == "verify_mismatch" and "“Ramp” (Not in price)" in str(exc.value)


async def test_it_stops_at_the_first_failure_and_says_what_was_saved(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    p = await svc.propose_categories(
        SUB,
        "42",
        [{"category": "Ramp", "rename_to": "Mana"}, {"category": "Maybeboard", "not_in_price": False}],
    )
    real = ark.handle
    seen: list[str] = []

    def second_fails(request):  # noqa: ANN001, ANN202
        if "/decks/category/" in request.url.path:
            seen.append(request.url.path)
            if len(seen) == 2:
                import httpx

                return httpx.Response(400, json={"detail": "nope"})
        return real(request)

    ark.transport.handler = second_fails
    with pytest.raises(DeckError) as exc:
        await svc.apply(SUB, p["proposal_id"], via="browser")
    assert "Saved before it stopped: “Mana”; the other categories were not changed." in str(exc.value)
    assert len(seen) == 2  # never a third
    row = svc.db.get_proposal(p["proposal_id"], SUB)
    assert row["state"] == "failed" and row["result"]["categories_saved"] == ["Mana"]
    assert _cats(ark)["Maybeboard"]["includedInPrice"] is False


async def test_writes_wait_their_turn_one_at_a_time(stack: Stack) -> None:
    """Each category write goes through the client's pacer and the next starts only once the last
    has answered."""
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    p = await svc.propose_categories(
        SUB,
        "42",
        [{"category": "Ramp", "rename_to": "Mana"}, {"category": "Maybeboard", "not_in_price": False}],
    )
    client = svc.client
    real = client._paced_send
    order: list[str] = []
    inflight = {"n": 0, "max": 0}

    async def counted(method, path, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN202
        if "/decks/category/" in path:
            order.append("pace")  # the request is handed to the pacer
        return await real(method, path, *args, **kwargs)

    real_handle = ark.handle

    def handle(request):  # noqa: ANN001, ANN202
        if "/decks/category/" in request.url.path:
            inflight["n"] += 1
            inflight["max"] = max(inflight["max"], inflight["n"])
            order.append("write")
            try:
                return real_handle(request)
            finally:
                inflight["n"] -= 1
        return real_handle(request)

    client._paced_send = counted
    ark.transport.handler = handle
    out = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert out["state"] == "applied" and inflight["max"] == 1
    writes = [i for i, x in enumerate(order) if x == "write"]
    assert len(writes) == 2 and all(order[i - 1] == "pace" for i in writes)


# -- History's Restore puts names and flags back -------------------------------------------------------
async def test_restore_puts_category_names_and_flags_back_by_id(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    ids = {c["name"]: c["id"] for c in deck_categories(await svc.get_own_deck(SUB, "42"))}
    p = await svc.propose_categories(
        SUB,
        "42",
        [
            {"category": "Ramp", "rename_to": "Mana", "not_in_price": True},
            {"category": "Maybeboard", "not_in_deck": False},
        ],
    )
    done = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert done["state"] == "applied"
    ark.category_patches.clear()
    r = await svc.propose_restore(SUB, done["result"]["snapshot_id"])
    lines = r["diff"].splitlines()
    assert (
        "Category “Mana”: rename to “Ramp” (its 2 cards go with it), Not in price off. " + OPEN_TABS_NOTE
        in lines
    )
    assert "Category “Maybeboard”: Not in deck on. " + OPEN_TABS_NOTE in lines
    assert not [ln for ln in lines if ln.startswith(("+", "-"))]  # no card is removed and added again
    back = await svc.apply(SUB, r["proposal_id"], via="browser")
    assert back["state"] == "applied" and back["result"]["verified"] is True, back
    assert _cats(ark)["Ramp"]["id"] == ids["Ramp"] and "Mana" not in _cats(ark)
    assert (
        _cats(ark)["Ramp"]["includedInPrice"] is True and _cats(ark)["Maybeboard"]["includedInDeck"] is False
    )
    assert _filed(ark, "Ramp") == sorted(RAMP) and ark.patches == []  # by the category write only
    assert [b["id"] for b in ark.category_patches] == sorted(b["id"] for b in ark.category_patches)


async def test_restore_moves_cards_back_if_a_rename_left_them_behind(stack: Stack) -> None:
    """Had a live rename not taken its cards along, the restore works out the cards again on the
    deck read back after its category writes, so they are moved back by card writes."""
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    done = await svc.apply(SUB, p["proposal_id"], via="browser")
    r = await svc.propose_restore(SUB, done["result"]["snapshot_id"])
    ark.category_rename_leaves_cards = True
    back = await svc.apply(SUB, r["proposal_id"], via="browser")
    assert back["state"] == "applied" and back["result"]["verified"] is True, back
    assert _filed(ark, "Ramp") == sorted(RAMP) and ark.patches  # the cards went back by card writes


async def test_restore_refuses_a_name_another_category_has_now(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    done = await svc.apply(SUB, p["proposal_id"], via="browser")
    _cats(ark)["Maybeboard"]["name"] = "Ramp"  # renamed on Archidekt since
    with pytest.raises(DeckError, match="can't get its name “Ramp” back"):
        await svc.propose_restore(SUB, done["result"]["snapshot_id"])


async def test_a_waiting_card_edit_naming_a_renamed_category_needs_a_look(stack: Stack) -> None:
    """N-1 (review 2026-10-22): a waiting card edit that files a card under a category renamed
    since would, applied as it stands, make the old name again with default switches. After the
    categories apply it waits as Needs a look, never "updated", so it is reviewed again first."""
    await _ready(stack)
    svc = _svc(stack)
    edit = await svc.propose(
        SUB, "42", [{"action": "set_category", "card_name": "Sol Ring", "category": "Ramp"}]
    )
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    done = await svc.apply(SUB, p["proposal_id"], via="browser")
    assert done["state"] == "applied", done
    row = svc.db.get_proposal(edit["proposal_id"], SUB)
    assert row["state"] == "pending" and row["status"] == "needs_look", row
    assert recheck.reason_text(row["status_reason"]) == (
        "The category “Ramp” was renamed to “Mana” since this was made. Applied as it stands, "
        "it would make a new category “Ramp”."
    )
    # a later check (a deck page view) keeps the mark, and applying it is refused, nothing sent
    deck = await svc.get_own_deck(SUB, "42")
    svc.recheck_waiting(SUB, deck)
    assert svc.db.get_proposal(edit["proposal_id"], SUB)["status"] == "needs_look"
    with pytest.raises(DeckError) as exc:
        await svc.apply(SUB, edit["proposal_id"], via="browser")
    assert exc.value.kind == "stale" and "renamed to “Mana”" in str(exc.value)
    assert stack.ark.patches == []


async def test_a_restore_that_cannot_be_checked_after_writing_reports_a_mismatch(stack: Stack) -> None:
    """N-2 (review 2026-10-22): once the restore's category writes are sent, a deck that can no
    longer be worked out against the snapshot (another category took the name meanwhile) is a
    mismatch with the snapshot kept, never the before-anything-is-sent refusal."""
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    done = await svc.apply(SUB, p["proposal_id"], via="browser")
    r = await svc.propose_restore(SUB, done["result"]["snapshot_id"])
    send = svc._send_categories

    async def then_renamed_elsewhere(*a, **k):  # noqa: ANN002, ANN003, ANN202
        await send(*a, **k)
        ramp = _cats(ark)["Ramp"]
        _cats(ark)["Maybeboard"]["name"] = "Taken"
        ramp["name"] = "Mana"  # undone on Archidekt at once, and its old name taken
        _cats(ark)["Taken"]["name"] = "Ramp"
        for row in ark.decks[42]["cards"]:
            if "Ramp" in (row.get("categories") or []):
                row["categories"] = ["Mana" if x == "Ramp" else x for x in row["categories"]]

    svc._send_categories = then_renamed_elsewhere
    with pytest.raises(DeckError) as exc:
        await svc.apply(SUB, r["proposal_id"], via="browser")
    assert exc.value.kind == "verify_mismatch", exc.value
    assert "can't get its name “Ramp” back" in str(exc.value) and "snapshot" in str(exc.value)


async def test_undo_preview_lists_the_category_lines(stack: Stack) -> None:
    await _ready(stack)
    svc = _svc(stack)
    p = await svc.propose_categories(SUB, "42", [{"category": "Maybeboard", "not_in_price": False}])
    done = await svc.apply(SUB, p["proposal_id"], via="browser")
    prev = await svc.undo_preview(
        SUB, done["result"]["snapshot_id"], applied=[p["proposal_id"]], after_at=int(done["applied_at"])
    )
    assert prev["nothing"] is False
    assert [r["kind"] for r in prev["rows"]] == ["deck_category"]


# -- risk: what waits for the member's press -----------------------------------------------------------
@pytest.mark.parametrize(
    ("rows", "wait"),
    [
        (
            [{"kind": "deck_category", "name": "Commander", "premier_before": True, "premier_after": False}],
            "changes the commander",
        ),
        (
            [{"kind": "deck_category", "name": "Legends", "premier_before": False, "premier_after": True}],
            "changes the commander",
        ),
        (
            [{"kind": "deck_category", "name": "Commander", "rename_to": "Boss", "commander": True}],
            "changes the commander",
        ),
        (
            [{"kind": "deck_category", "name": "Legends", "rename_to": "Boss", "commander": True}],
            "changes the commander",
        ),
        (
            [
                {
                    "kind": "deck_category",
                    "name": "Maybe",
                    "not_in_deck_before": True,
                    "not_in_deck_after": False,
                }
            ],
            "changes which cards count in the deck",
        ),
        (
            [{"kind": "deck_category", "name": "Side", "rename_to": "Sideboard", "counts": True}],
            "changes which cards count in the deck",
        ),
        (
            [
                {
                    "kind": "deck_category",
                    "name": "Ramp",
                    "not_in_price_before": False,
                    "not_in_price_after": True,
                }
            ],
            None,
        ),
        ([{"kind": "deck_category", "name": "Ramp", "rename_to": "Mana"}], None),
        (None, "has no list of rows to check"),
    ],
)
def test_categories_risk_and_auto_waits(rows, wait) -> None:  # noqa: ANN001
    risk, why = modes.risk_of("categories", rows)
    assert risk == "high"
    assert why == (wait or "changes the deck's categories")
    assert modes.auto_waits("categories", rows) == wait
    # Full auto applies it only when nothing waits; semi never (high), manual never
    assert modes.assistant_may_apply("auto", risk, auto_wait=wait) is (wait is None)
    assert modes.assistant_may_apply("semi", risk, auto_wait=wait) is False
    assert modes.assistant_may_apply("manual", risk, auto_wait=wait) is False


def test_the_settings_page_asks_first_for_a_commander_category() -> None:
    premier = [{"kind": "deck_category", "name": "Commander", "premier_before": True, "premier_after": False}]
    assert modes.hand_edit_confirm("categories", premier) == modes.CATEGORY_COMMANDER_CONFIRM
    plain = [{"kind": "deck_category", "name": "Ramp", "rename_to": "Mana"}]
    assert modes.hand_edit_confirm("categories", plain) is None


@pytest.mark.parametrize(
    "rows",
    [
        [{"kind": "deck_category", "name": "Maybe", "not_in_deck_before": True, "not_in_deck_after": False}],
        [{"kind": "deck_category", "name": "Side", "rename_to": "Sideboard", "counts": True}],
        [{"kind": "deck_category", "name": "Sideboard", "rename_to": "Extras", "counts": True}],
    ],
)
def test_the_settings_page_asks_first_for_whatever_an_assistant_would_wait_on(rows) -> None:  # noqa: ANN001
    # the same rule by hand and from an assistant: every reason a category change waits asks first
    assert modes.category_wait(rows) == modes.CATEGORY_COUNTS_REASON
    why = modes.hand_edit_confirm("categories", rows)
    assert why == modes.CATEGORY_COUNTS_CONFIRM
    assert modes.CATEGORY_CONFIRM_TITLES[why] == "Change which cards count in the deck?"
    assert modes.CATEGORY_CONFIRM_TITLES[modes.CATEGORY_COMMANDER_CONFIRM] == "Change the commander category?"


async def test_rows_mark_the_category_holding_the_commander(stack: Stack) -> None:
    await _ready(stack)
    ark, svc = stack.ark, _svc(stack)
    # the commander row is filed under Commander and Legends too
    for row in ark.decks[42]["cards"]:
        if row.get("categories") == ["Commander"]:
            row["categories"] = ["Commander", "Legends"]
    ark.decks[42]["categories"].append(
        {"name": "Legends", "isPremier": False, "includedInDeck": True, "includedInPrice": True}
    )
    p = await svc.propose_categories(SUB, "42", [{"category": "Legends", "rename_to": "Bosses"}])
    assert p["rows"][0]["commander"] is True and p["risk"] == "high"
    assert modes.auto_waits("categories", p["rows"]) == "changes the commander"
    cats = {c["name"]: c for c in deck_categories(await svc.get_own_deck(SUB, "42"))}
    assert cats["Legends"]["commander"] and cats["Commander"]["commander"] and not cats["Ramp"]["commander"]


# -- the assistant's path: propose, review, apply ----------------------------------------------------------
async def test_the_assistant_proposes_and_full_auto_applies_a_plain_rename(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await _ready(stack, own=False)
    await _set_mode(h, "auto")
    p = structured(
        await call(
            h,
            token,
            "propose_deck_categories",
            {"deck_id": "42", "changes": [{"category": "Ramp", "rename_to": "Mana"}]},
        )
    )
    assert (
        p["ok"] and p["kind"] == "categories" and p["risk"] == "high" and p["assistant_may_apply"] is True
    ), p
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] and out["state"] == "applied", out
    assert _filed(ark, "Mana") == sorted(RAMP)


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"category": "Commander", "premier": False}, "changes the commander"),
        ({"category": "Commander", "rename_to": "Boss"}, "changes the commander"),
        ({"category": "Maybeboard", "not_in_deck": False}, "changes which cards count in the deck"),
    ],
)
async def test_full_auto_never_applies_a_commander_or_deck_count_change(
    stack: Stack, change: dict, reason: str
) -> None:
    h, ark = stack.h, stack.ark
    token = await _ready(stack, own=False)
    await _set_mode(h, "auto")
    p = structured(await call(h, token, "propose_deck_categories", {"deck_id": "42", "changes": [change]}))
    assert p["ok"] and p["assistant_may_apply"] is False and p["risk_reason"] == reason, p
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required", out
    assert ark.category_patches == []


async def test_semi_mode_waits_for_any_category_change(stack: Stack) -> None:
    h = stack.h
    token = await _ready(stack, own=False)
    await _set_mode(h, "semi")
    p = structured(
        await call(
            h,
            token,
            "propose_deck_categories",
            {"deck_id": "42", "changes": [{"category": "Ramp", "not_in_price": True}]},
        )
    )
    assert p["ok"] and p["risk"] == "high" and p["assistant_may_apply"] is False, p


async def test_a_held_category_change_waits_in_full_auto(stack: Stack) -> None:
    h = stack.h
    token = await _ready(stack, own=False)
    await _set_mode(h, "auto")
    p = structured(
        await call(
            h,
            token,
            "propose_deck_categories",
            {"deck_id": "42", "changes": [{"category": "Ramp", "rename_to": "Mana"}]},
        )
    )
    assert h.db.set_hold(p["proposal_id"], SUB, "depends:other:rejected")
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required", out
    assert stack.ark.category_patches == []


async def test_another_apps_change_is_not_applied_by_this_app(stack: Stack) -> None:
    """A category change the member's own page made is never applied over MCP by an assistant."""
    h = stack.h
    token = await _ready(stack)
    await _set_mode(h, "auto")
    p = await _svc(stack).propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "other_client", out
    assert stack.ark.category_patches == []


async def test_a_read_only_token_may_not_propose_category_changes() -> None:
    from mtg_gateway.app import WRITE_TOOLS

    assert "propose_deck_categories" in WRITE_TOOLS


def test_the_tool_has_plain_words() -> None:
    from mtg_gateway.tool_labels import TOOLS

    assert TOOLS["propose_deck_categories"].ask == "Add, delete or rename a deck category"


async def test_category_templates_on_the_account_are_never_touched(stack: Stack) -> None:
    await _ready(stack)
    svc = _svc(stack)
    p = await svc.propose_categories(SUB, "42", [{"category": "Ramp", "rename_to": "Mana"}])
    await svc.apply(SUB, p["proposal_id"], via="browser")
    assert not [c for c in stack.ark.calls if "/users/category" in c[1]]
