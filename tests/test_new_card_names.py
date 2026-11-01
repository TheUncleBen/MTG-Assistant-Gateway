"""Names checked the way deck edits check them, for a new deck and for the binder, and the proposals
that can only be applied with a linked Archidekt account refused at once without one (0.7.46
audit): a misspelt card in a new deck or a binder change waited as a proposal (and a binder
change, being low risk, could be applied by Semi or Full auto), and a copy of a deck or a binder
change was made for an account with no Archidekt link, though it could never be applied."""

from __future__ import annotations

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, mcp_token, structured
from .test_track_a_board import _mode


def _cards(*names: str) -> list[dict]:
    return [{"name": n, "quantity": 1} for n in names]


async def _pending(h) -> int:
    return len([p for p in h.db.list_proposals("user-1", 100) if p["state"] == "pending"])


def _catalog(h, unknown: set[str]) -> None:
    """The card-name catalog, as the scan service answers it: every name is a card except these."""

    async def known(names: list[str]) -> dict[str, bool | None]:
        return {n: n not in unknown for n in names}

    h.app.state.gateway.decks.known_cards = known


# -- a new deck ---------------------------------------------------------------------------------


async def test_check_new_deck_asks_archidekt_about_a_name_the_catalog_cannot_judge(stack: Stack) -> None:
    """No card-name catalog: "Notacard Xyzzyplex" passed check_new_deck with only the size warning.
    It is asked of Archidekt now, as check_deck_changes does, and one Archidekt lacks is a problem."""
    h = stack.h
    token = await linked_user(stack)
    out = structured(
        await call(h, token, "check_new_deck", {"name": "Brew", "cards": _cards("Notacard Xyzzyplex", "Opt")})
    )
    assert out["suggest"] == "fix", out
    missing = next(p for p in out["problems"] if p["error"] == "missing_on_archidekt")
    assert missing["missing_on_archidekt"] == ["Notacard Xyzzyplex"], missing
    assert "Archidekt doesn't have “Notacard Xyzzyplex”" in missing["message"], missing


async def test_propose_new_deck_names_a_card_archidekt_does_not_have(stack: Stack) -> None:
    """The apply looks every card up before it makes the deck, so a card Archidekt lacks fails it
    after the member approved it. It was proposed with no word about it; now, as in a deck edit,
    the row is marked and the result warns, naming the card, so the assistant can fix it first."""
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    out = structured(
        await call(
            h, token, "propose_new_deck", {"name": "Brew", "cards": _cards("Notacard Xyzzyplex", "Opt")}
        )
    )
    assert out["ok"] and out["state"] == "pending", out
    assert out["missing_on_archidekt"] == ["Notacard Xyzzyplex"], out
    assert any("Archidekt doesn't have “Notacard Xyzzyplex”" in w for w in out["warnings"]), out
    row = next(r for r in out["rows"] if "Notacard Xyzzyplex" in str(r))
    assert row.get("missing") is True, row


async def test_propose_new_deck_refuses_a_name_that_is_no_card(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    _catalog(h, {"Sol Rnig"})
    out = structured(
        await call(h, token, "propose_new_deck", {"name": "Brew", "cards": _cards("Sol Rnig", "Opt")})
    )
    assert out["ok"] is False and out["error"] == "unknown_cards", out
    assert out["unknown_cards"] == ["Sol Rnig"] and "No card is called “Sol Rnig”" in out["message"], out
    assert await _pending(h) == 0


async def test_an_amend_of_a_new_deck_checks_the_new_list_too(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    _catalog(h, {"Sol Rnig"})
    p = structured(await call(h, token, "propose_new_deck", {"name": "Brew", "cards": _cards("Opt")}))
    assert p["ok"] and p["state"] == "pending", p
    out = structured(
        await call(
            h,
            token,
            "amend_proposal",
            {
                "proposal_id": p["proposal_id"],
                "version": p["version"],
                "new_deck": {"cards": _cards("Opt", "Sol Rnig")},
            },
        )
    )
    assert out["ok"] is False and out["error"] == "unknown_cards", out
    assert structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))["version"] == 1


async def test_a_new_deck_of_real_cards_is_proposed_as_before(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = structured(
        await call(h, token, "propose_new_deck", {"name": "Brew", "cards": _cards("Opt", "Swamp")})
    )
    assert p["ok"] and p["state"] == "pending", p
    assert "(2 cards, private)" in p["diff"], p["diff"]
    one = structured(await call(h, token, "propose_new_deck", {"name": "Solo", "cards": _cards("Opt")}))
    assert "(1 card, private)" in one["diff"], one["diff"]


# -- the binder ----------------------------------------------------------------------------------


async def test_a_binder_add_that_is_no_card_is_refused(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    _catalog(h, {"Sol Rnig"})
    out = structured(
        await call(h, token, "propose_collection_changes", {"add": [{"name": "Sol Rnig", "quantity": 1}]})
    )
    assert out["ok"] is False and out["error"] == "unknown_cards", out
    assert out["unknown_cards"] == ["Sol Rnig"], out
    assert await _pending(h) == 0


async def test_a_binder_add_archidekt_does_not_have_is_high_risk(stack: Stack) -> None:
    """A binder change is low risk, so Semi auto let the assistant apply it. One whose card
    Archidekt doesn't have is high risk now, as in a deck edit: Semi auto waits for the member
    (Full auto treats it as it treats such a deck edit)."""
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "semi")
    out = structured(
        await call(
            h,
            token,
            "propose_collection_changes",
            {"add": [{"name": "Notacard Xyzzyplex", "quantity": 1}, {"name": "Opt", "quantity": 1}]},
        )
    )
    assert out["ok"] and out["state"] == "pending", out
    assert out["risk"] == "high" and out["assistant_may_apply"] is False, out
    assert out["missing_on_archidekt"] == ["Notacard Xyzzyplex"], out
    ok = structured(
        await call(h, token, "propose_collection_changes", {"add": [{"name": "Opt", "quantity": 1}]})
    )
    assert ok["risk"] == "low" and ok["assistant_may_apply"] is True, ok


# -- no Archidekt link -----------------------------------------------------------------------------


async def test_a_binder_change_without_a_link_is_refused(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    out = structured(
        await call(h, token, "propose_collection_changes", {"add": [{"name": "Opt", "quantity": 1}]})
    )
    assert out["ok"] is False and out["error"] == "not_linked", out
    assert "/account" in out["message"], out
    assert await _pending(h) == 0


async def test_a_copy_of_a_deck_without_a_link_is_refused(stack: Stack) -> None:
    h = stack.h
    token = await mcp_token(h)
    out = structured(await call(h, token, "propose_clone_deck", {"deck_id": "42"}))
    assert out["ok"] is False and out["error"] == "not_linked", out
    assert "/account" in out["message"], out
    assert await _pending(h) == 0
