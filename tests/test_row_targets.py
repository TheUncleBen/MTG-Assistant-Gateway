"""A change on one row of a card that has several (Ben, 2026-10-28): Sol Ring in the deck, in a
category left out of the deck other than Maybeboard and Sideboard ("Optional Artwork") and in
the Sideboard. A remove, a quantity change, a move or a printing change that names the row's
relation id changes that row only, whatever zone it is sent with, and the review names the row's
own category. A row that is gone since is refused with nothing sent."""

from __future__ import annotations

import pytest

from mtg_gateway.decks import DeckError, parse_changes

from .conftest import Stack
from .test_account_actions import _browser_apply
from .test_approval_modes import AESI, _commanders, _second_commander_row_first, _set_mode
from .test_decks_and_proxy import call, linked_user, structured


def _rows(stack: Stack) -> list[tuple[str, int, str, str]]:
    """Deck 42's Sol Ring rows as (primary category, quantity, set, number), sorted."""
    return sorted(
        (
            (c["categories"] or [""])[0],
            c["quantity"],
            c["card"]["edition"]["editioncode"],
            c["card"]["collectorNumber"],
        )
        for c in stack.ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == "Sol Ring"
    )


def _rel(stack: Stack, category: str) -> int:
    return next(
        c["id"]
        for c in stack.ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == "Sol Ring" and (c["categories"] or [""])[0] == category
    )


async def _setup(stack: Stack) -> str:
    stack.ark.add_side_row(42, "Sol Ring", 1, category="Optional Artwork")
    stack.ark.add_side_row(42, "Sol Ring", 2, category="Sideboard")
    await linked_user(stack)
    return stack.gw.db._one("SELECT sub FROM users", ())["sub"]


async def _apply(stack: Stack, sub: str, changes: list[dict]) -> dict:
    p = await stack.gw.decks.propose(sub, "42", changes, row_flags=True)
    out = await _browser_apply(stack.h, p["proposal_id"])
    assert out.get("state") == "applied", out
    return p


def test_a_relation_id_goes_with_a_row_change_only() -> None:
    got = parse_changes([{"action": "remove", "card_name": "Sol Ring", "relation_id": 7}])
    assert got[0].relation_id == 7 and got[0].as_dict()["relation_id"] == 7
    for bad in (
        {"action": "add", "card_name": "Sol Ring", "relation_id": 7},
        {"action": "remove", "card_name": "Sol Ring", "relation_id": "x"},
        {"action": "remove", "card_name": "Sol Ring", "relation_id": 0},
    ):
        with pytest.raises(DeckError, match="relation_id"):
            parse_changes([bad])


async def test_a_remove_takes_out_that_row_only_and_names_its_category(stack: Stack) -> None:
    sub = await _setup(stack)
    before = _rows(stack)
    # sent without a zone, as a page that does not know it would: the row says where it is
    p = await _apply(
        stack,
        sub,
        [{"action": "remove", "card_name": "Sol Ring", "relation_id": _rel(stack, "Optional Artwork")}],
    )
    assert "-1 Sol Ring [Optional Artwork]" in p["diff"], p["diff"]
    assert _rows(stack) == [r for r in before if r[0] != "Optional Artwork"]


async def test_a_quantity_change_counts_that_row_only(stack: Stack) -> None:
    sub = await _setup(stack)
    before = _rows(stack)
    p = await _apply(
        stack,
        sub,
        [
            {
                "action": "set_quantity",
                "card_name": "Sol Ring",
                "quantity": 1,
                "zone": "side",
                "relation_id": _rel(stack, "Sideboard"),
            }
        ],
    )
    assert "2 -> 1 Sol Ring [Sideboard]" in p["diff"], p["diff"]
    assert _rows(stack) == [(c, 1 if c == "Sideboard" else q, s, n) for c, q, s, n in before]


async def test_a_move_and_a_printing_change_take_that_row_only(stack: Stack) -> None:
    sub = await _setup(stack)
    art = _rel(stack, "Optional Artwork")
    await _apply(
        stack,
        sub,
        [{"action": "set_category", "card_name": "Sol Ring", "category": "Maybeboard", "relation_id": art}],
    )
    cats = sorted(r[0] for r in _rows(stack))
    assert "Optional Artwork" not in cats and "Maybeboard" in cats and "Sideboard" in cats
    side = _rel(stack, "Sideboard")
    p = await _apply(
        stack,
        sub,
        [
            {
                "action": "set_printing",
                "card_name": "Sol Ring",
                "set_code": "sld",
                "collector_number": "1075",
                "relation_id": side,
            }
        ],
    )
    assert "SLD 1075" in p["diff"], p["diff"]
    printed = {r[0]: (r[2], r[3]) for r in _rows(stack)}
    assert printed["Sideboard"] == ("sld", "1075")
    assert printed["Maybeboard"] != ("sld", "1075")
    assert [r for r in _rows(stack) if r[0] not in ("Sideboard", "Maybeboard")][0][2:] != ("sld", "1075")


async def test_a_row_gone_since_is_refused(stack: Stack) -> None:
    sub = await _setup(stack)
    with pytest.raises(DeckError, match="no longer in the deck"):
        await stack.gw.decks.propose(
            sub, "42", [{"action": "remove", "card_name": "Sol Ring", "relation_id": 999999}]
        )


async def test_a_side_printing_change_without_a_row_needs_the_card_to_have_one_side_row(stack: Stack) -> None:
    """The deck page's Undo of a side row's printing names the card and zone only (the change made
    a new row): it is taken when the card has one maybeboard or sideboard row, and refused, with
    nothing sent, when it has more."""
    stack.ark.add_side_row(42, "Sol Ring", 1, category="Optional Artwork")
    await linked_user(stack)
    sub = stack.gw.db._one("SELECT sub FROM users", ())["sub"]
    one = {"action": "set_printing", "card_name": "Sol Ring", "set_code": "sld", "collector_number": "1075"}
    await _apply(stack, sub, [{**one, "zone": "side"}])
    printed = {r[0]: (r[2], r[3]) for r in _rows(stack)}
    assert printed["Optional Artwork"] == ("sld", "1075") and printed[""] != ("sld", "1075")
    stack.ark.add_side_row(42, "Sol Ring", 2, category="Sideboard")
    with pytest.raises(DeckError, match="more than one maybeboard or sideboard row"):
        await stack.gw.decks.propose(sub, "42", [{**one, "collector_number": "1074", "zone": "side"}])


@pytest.mark.parametrize("second_row", [False, True], ids=["one row", "two rows"])
@pytest.mark.parametrize("mode", ["auto", "semi"])
async def test_a_remove_of_the_commander_row_by_its_relation_id_waits(
    stack: Stack, mode: str, second_row: bool
) -> None:
    """A row change by relation id goes through the same commander check as any other: removing
    the commander's own row waits for the member's press in Full auto and Semi-auto, whether the
    commander has that row only or another row in the deck as well."""
    h, ark = stack.h, stack.ark
    if second_row:
        _second_commander_row_first(ark)
    token = await linked_user(stack)
    await _set_mode(h, mode)
    rel = next(
        c["id"]
        for c in ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"] == AESI and "Commander" in (c["categories"] or [])
    )
    change = {"action": "remove", "card_name": AESI, "relation_id": rel}
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))
    assert p["ok"] and p["state"] == "pending", p
    assert "commander" in [r.get("kind") for r in p["rows"]], p["rows"]
    assert p["risk"] == "high" and p["assistant_may_apply"] is False
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "browser_required"
    assert ark.patches == [] and _commanders(ark) == [AESI]
