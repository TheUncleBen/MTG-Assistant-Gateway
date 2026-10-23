"""Card packages (parity release 2, REQUESTS.md section 6): making one with the New package form's
request, and changing its keywords, budget and formats with the deck settings' second request (by
the package block's own id). Nothing here was recorded from archidekt.com."""

# ruff: noqa: F811 - the stack fixture is imported and then named as a parameter
from __future__ import annotations

import pytest

from mtg_gateway import packages

from .conftest import Stack
from .test_account_actions import _browser_apply
from .test_decks_and_proxy import call, linked_user, structured


def test_settings_are_checked_and_sent_in_archidekts_names() -> None:
    got = packages.parse(
        {"keywords": "  shocklands,  fetchlands ", "budget": 2, "intended_formats": ["commander", "Modern"]}
    )
    assert got == {"keywords": "shocklands, fetchlands", "budget": 2, "intendedFormats": [3, 2]}
    assert packages.parse({"keywords": "x"}) == {"keywords": "x", "budget": None, "intendedFormats": []}
    assert packages.parse({"budget": None}, partial=True) == {"budget": None}
    assert packages.text(got) == "keywords “shocklands, fetchlands”, budget $$, for Commander, Modern"


@pytest.mark.parametrize(
    ("raw", "says"),
    [
        ({}, "needs keywords"),
        ({"keywords": "x" * 201}, "1 to 200"),
        ({"keywords": "x", "budget": 5}, "1 to 4"),
        ({"keywords": "x", "budget": True}, "1 to 4"),
        ({"keywords": "x", "intended_formats": ["chess"]}, "format names"),
        ({"keywords": "x", "price": 1}, "unknown package fields"),
    ],
)
def test_bad_settings_are_refused(raw: dict, says: str) -> None:
    with pytest.raises(Exception, match=says):
        packages.parse(raw)


async def _new_package(stack: Stack, token: str, **extra: object) -> dict:
    args = {
        "name": "Fetches",
        "package": {"keywords": "fetchlands", "budget": 3, "intended_formats": ["commander"]},
    }
    p = structured(await call(stack.h, token, "propose_new_deck", {**args, **extra}))
    assert p["ok"] is True, p
    return p


async def test_a_new_package_is_made_with_the_new_package_request(stack: Stack) -> None:
    token = await linked_user(stack)
    p = await _new_package(stack, token)
    assert p["diff"].startswith(
        "New card package 'Fetches' (0 cards, private; keywords “fetchlands”, budget $$$"
    )
    assert not [d for d in stack.ark.decks.values() if d.get("cardPackage")]  # nothing made yet
    out = await _browser_apply(stack.h, p["proposal_id"])
    assert out["state"] == "applied", out
    made = stack.ark.decks[int(out["result"]["deck_id"])]
    assert made["cardPackage"]["keywords"] == "fetchlands" and made["cardPackage"]["intendedFormats"] == [3]
    assert made["deckFormat"] is None and made["private"] is True
    assert out["result"]["verified"] is True


async def test_a_package_with_cards_gets_them_after_it_is_made(stack: Stack) -> None:
    token = await linked_user(stack)
    p = await _new_package(stack, token, decklist_text="1 Sol Ring\n1 Arcane Signet")
    out = await _browser_apply(stack.h, p["proposal_id"])
    made = stack.ark.decks[int(out["result"]["deck_id"])]
    assert sorted(c["card"]["oracleCard"]["name"] for c in made["cards"]) == ["Arcane Signet", "Sol Ring"]


async def test_package_settings_change_by_the_blocks_own_id(stack: Stack) -> None:
    token = await linked_user(stack)
    out = await _browser_apply(stack.h, (await _new_package(stack, token))["proposal_id"])
    deck_id = str(out["result"]["deck_id"])
    block = stack.ark.decks[int(deck_id)]["cardPackage"]
    p = structured(
        await call(
            stack.h,
            token,
            "propose_deck_details",
            {"deck_id": deck_id, "details": {"package": {"keywords": "fetches", "budget": None}}},
        )
    )
    assert p["ok"] is True, p
    assert (
        "package: keywords “fetchlands”, budget $$$, for Commander -> keywords “fetches”, budget any"
        in p["diff"]
    )
    done = await _browser_apply(stack.h, p["proposal_id"])
    assert done["state"] == "applied", done
    assert (block["keywords"], block["budget"], block["intendedFormats"]) == ("fetches", None, [3])
    same = structured(
        await call(
            stack.h,
            token,
            "propose_deck_details",
            {"deck_id": deck_id, "details": {"package": {"keywords": "fetches"}}},
        )
    )
    assert same["ok"] is False and "nothing would change" in same["message"]
    not_one = structured(
        await call(
            stack.h, token, "propose_deck_details", {"deck_id": "42", "details": {"package": {"budget": 1}}}
        )
    )
    assert not_one["ok"] is False and "not a card package" in not_one["message"]


def _deck_with(cards: list, fmt: str = "commander", deck_id: str = "1", block: dict | None = None):
    from mtg_gateway.archidekt import Deck

    cats = [{"name": "Commander", "isPremier": True, "includedInDeck": True}]
    raw = {"cardPackage": block} if block else {}
    return Deck(deck_id, "D" + deck_id, "alice", "", cards, cats, raw, format=fmt)


def _c(name: str, qty: int = 1, cats: list | None = None, ci: list | None = None):
    from mtg_gateway.archidekt import DeckCard

    return DeckCard(None, None, name, qty, cats or [], "Normal", "", "", color_identity=ci or [])


def test_adds_follow_the_sites_add_package_options() -> None:
    deck = _deck_with([_c("Aesi", 1, ["Commander"], ["G", "U"]), _c("Sol Ring")])
    pkg = _deck_with(
        [
            _c("Sol Ring"),
            _c("Lightning Bolt", 4, ["Burn"], ["R"]),
            _c("Opt", 2, ["Draw"], ["U"]),
            _c("Opt", 1, ["Draw"], ["U"]),
        ],
        deck_id="2",
        block={"id": 9, "keywords": "x"},
    )
    got_pkg, changes, skipped = packages.adds(deck, pkg)
    assert got_pkg is pkg
    assert changes == [
        {"action": "add", "card_name": "Opt", "quantity": 1, "categories": ["Draw"]}
    ]  # singleton
    assert skipped == {
        "in the deck already": ["Sol Ring"],
        "outside the commander's colors": ["Lightning Bolt"],
    }
    _, every, none = packages.adds(
        deck, pkg, categories="maybeboard", skip_existing=False, skip_outside_identity=False, singleton=False
    )
    assert none == {}
    assert {c["card_name"]: (c["quantity"], c.get("zone")) for c in every} == {
        "Sol Ring": (1, "side"),
        "Lightning Bolt": (4, "side"),
        "Opt": (3, "side"),
    }
    assert "4 cards" not in packages.source_text("P", every, none) and "8 cards" in packages.source_text(
        "P", every, none
    )
    with pytest.raises(Exception, match="not a card package"):
        packages.adds(deck, _deck_with([_c("Opt")], deck_id="3"))


async def test_a_package_is_added_as_reviewed_adds_and_counts_one_import_when_applied(stack: Stack) -> None:
    token = await linked_user(stack)
    made = await _browser_apply(
        stack.h,
        (await _new_package(stack, token, decklist_text="1 Sol Ring\n1 Arcane Signet"))["proposal_id"],
    )
    pkg_id = int(made["result"]["deck_id"])
    listed = structured(await call(stack.h, token, "list_my_packages", {}))
    assert [p["id"] for p in listed["packages"]] == [str(pkg_id)] and listed["packages"][0]["imports"] == 0
    p = structured(
        await call(stack.h, token, "propose_add_package", {"deck_id": "42", "package_id": str(pkg_id)})
    )
    assert p["ok"] is True, p
    lines = p["diff"].splitlines()
    assert lines[0] == "From card package “Fetches”: 1 card (left out, in the deck already: Sol Ring)", lines
    assert lines[1].startswith("+1 Arcane Signet"), lines
    assert stack.ark.account.package_imports == {}  # nothing counted for a proposal
    done = await _browser_apply(stack.h, p["proposal_id"])
    assert done["state"] == "applied", done
    assert stack.ark.account.package_imports == {pkg_id: 1}
    assert "Arcane Signet" in {c["card"]["oracleCard"]["name"] for c in stack.ark.decks[42]["cards"]}
    again = structured(
        await call(stack.h, token, "propose_add_package", {"deck_id": "42", "package_id": str(pkg_id)})
    )
    assert again["ok"] is False and "Nothing to add" in again["message"]
