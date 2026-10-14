"""Whose deck is it: when Archidekt's answer does not say who owns a deck (the owner field missing,
renamed or null), the deck is never treated as someone else's. Editing still needs a readable
owner (nothing is sent to a deck the gateway cannot confirm is the member's), and the refusal
says so instead of naming an owner that is not there."""

from __future__ import annotations

from typing import Any

import pytest

from mtg_gateway.archidekt import parse_deck
from mtg_gateway.decks import DeckError, owns_deck

from .test_browse_collection import Stack, linked, stack  # noqa: F401 - fixture

LINK = {"archidekt_username": "alice", "archidekt_user_id": "7"}


def _deck(owner: Any) -> Any:
    return parse_deck({"id": 42, "name": "Mine", "owner": owner, "cards": [], "categories": []})


def test_a_null_username_is_no_owner_not_the_word_none() -> None:
    """Bug (found in this release's checks): an owner whose username was null was read as the
    owner "None", so the member was told the deck belongs to None."""
    deck = _deck({"id": None, "username": None})
    assert deck.owner == "" and deck.owner_id is None


@pytest.mark.parametrize(
    "owner",
    [None, {}, {"id": None, "username": None}, {"userId": 7, "name": "alice"}, "alice", 7],
)
def test_an_unreadable_owner_is_unknown_never_someone_else(owner: Any) -> None:
    assert owns_deck(_deck(owner), LINK) is None


def test_ownership_by_id_or_username() -> None:
    assert owns_deck(_deck({"id": 7, "username": "alice"}), LINK) is True
    assert owns_deck(_deck({"id": 7, "username": "alice_renamed"}), LINK) is True  # the id cannot change
    assert owns_deck(_deck({"username": "ALICE"}), LINK) is True
    assert owns_deck(_deck({"id": None, "username": "alice"}), LINK) is True
    assert owns_deck(_deck({"id": 8, "username": "amy"}), LINK) is False
    assert owns_deck(_deck({"id": 8}), LINK) is False
    assert owns_deck(_deck({"username": "amy"}), {"archidekt_username": "alice"}) is False


async def test_editing_a_deck_whose_owner_cannot_be_read_is_refused_plainly(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    ark, svc = stack.ark, stack.h.app.state.gateway.decks
    sub = stack.h.app.state.gateway.db._one("SELECT sub FROM users", ())["sub"]
    real = ark._deck_for
    try:
        for owner in (None, {"id": None, "username": None}, {"userId": 7}):
            ark._deck_for = lambda d, who, owner=owner: {**real(d, who), "owner": owner}
            with pytest.raises(DeckError) as err:
                await svc.get_own_deck(sub, "42")
            assert err.value.kind == "contract", owner
            assert "did not say who owns this deck" in str(err.value) and "None" not in str(err.value)
        # someone else's deck is still refused as theirs
        ark._deck_for = lambda d, who: {**real(d, who), "owner": {"id": 78, "username": "amy"}}
        with pytest.raises(DeckError) as err:
            await svc.get_own_deck(sub, "42")
        assert err.value.kind == "forbidden" and "belongs to amy" in str(err.value)
        ark._deck_for = lambda d, who: {**real(d, who), "owner": {"id": 78}}  # an id, no name
        with pytest.raises(DeckError) as err:
            await svc.get_own_deck(sub, "42")
        assert err.value.kind == "forbidden" and "belongs to another Archidekt account" in str(err.value)
    finally:
        ark._deck_for = real
        await b.aclose()
