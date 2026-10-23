"""set_companion and set_back_face (Archidekt parity release 1, approved by Ben 22 Oct 2026, "All
as recommended"): Archidekt's card menu switches a row's Companion and "show back face by
default" (its modifyCards ``companion`` and ``flippedDefault``, read from its site code). The
gateway sends them like the other row changes (set_label, set_mana_value): every row of the card
in that zone, the row's other values kept, read back after, put back by a restore. A companion
change waits for the member's press in every mode (it changes what the deck is legal with)."""

from __future__ import annotations

import pytest

from mtg_gateway import modes
from mtg_gateway.decks import DeckError, parse_changes

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, structured
from .track_a_helpers import apply_any

DELVER = "Delver of Secrets // Insectile Aberration"


def _row(ark, name: str) -> dict:  # noqa: ANN001
    return next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == name)


async def _propose(h, token: str, change: dict) -> dict:  # noqa: ANN001
    return structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [change]}))


def test_parse_and_words() -> None:
    (ch,) = parse_changes([{"action": "set_companion", "card_name": "Sol Ring", "companion": True}])
    assert ch.on is True and ch.as_dict() == {
        "action": "set_companion",
        "card_name": "Sol Ring",
        "companion": True,
    }
    (ch,) = parse_changes(
        [{"action": "set_back_face", "card_name": DELVER, "back_face": False, "zone": "side"}]
    )
    assert ch.as_dict() == {
        "action": "set_back_face",
        "card_name": DELVER,
        "back_face": False,
        "zone": "side",
    }
    assert modes.auto_waits("edit", [{"kind": "companion", "name": "Sol Ring"}]) == modes.COMPANION_REASON
    assert modes.auto_waits("edit", [{"kind": "back_face", "name": DELVER}]) is None
    assert modes.risk_of("edit", [{"kind": "back_face", "name": DELVER}])[0] == "high"


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"action": "set_companion", "companion": "yes"}, "needs companion: true or false"),
        ({"action": "set_companion"}, "needs companion"),
        ({"action": "set_back_face", "companion": True}, "takes only card_name, back_face and zone"),
        ({"action": "set_companion", "companion": True, "quantity": 2}, "takes only"),
    ],
)
def test_a_malformed_switch_is_refused(change: dict, says: str) -> None:
    with pytest.raises(DeckError) as err:
        parse_changes([{"card_name": "Sol Ring", **change}])
    assert says in str(err.value)


async def test_companion_waits_for_the_press_then_applies_reads_back_and_restores(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    row = _row(ark, "Sol Ring")
    row["label"] = "Have,#37d67a"
    p = await _propose(h, token, {"action": "set_companion", "name": "Sol Ring", "companion": True})
    assert p["ok"] and "Sol Ring: companion off -> on" in p["diff"], p
    assert p["assistant_may_apply"] is False and p["risk_reason"] == modes.COMPANION_REASON, p
    a = await apply_any(h, token, p["proposal_id"])  # the member's press
    assert a["ok"] and a["result"]["verified"] is True, a
    assert row["companion"] is True and row["label"] == "Have,#37d67a"
    sent = [e for patch in ark.patches for e in patch.get("cards", [patch])]
    assert sent[-1]["modifications"]["companion"] is True
    # the same again changes nothing
    assert not (await _propose(h, token, {"action": "set_companion", "name": "Sol Ring", "companion": True}))[
        "ok"
    ]
    # Restore under History switches it back off
    r = structured(await call(h, token, "propose_restore_snapshot", {"snapshot_id": a["snapshot_id"]}))
    assert r["ok"] and "Sol Ring: companion on -> off" in r["diff"], r
    a2 = await apply_any(h, token, r["proposal_id"])
    assert a2["ok"], a2
    assert row["companion"] is False


async def test_back_face_on_a_two_faced_card_only(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    ark.add_side_row(42, DELVER, category="Sideboard")
    p = await _propose(
        h, token, {"action": "set_back_face", "name": DELVER, "back_face": True, "zone": "side"}
    )
    assert p["ok"] and "show back face off -> on (maybeboard/sideboard)" in p["diff"], p
    a = await apply_any(h, token, p["proposal_id"])
    assert a["ok"] and a["result"]["verified"] is True, a
    assert next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == DELVER)[
        "flippedDefault"
    ]
    one_face = await _propose(h, token, {"action": "set_back_face", "name": "Sol Ring", "back_face": True})
    assert not one_face["ok"] and "has no back face" in one_face["message"], one_face


async def test_a_switch_archidekt_ignores_is_not_reported_verified(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    real = ark.apply_patch

    def drop_companion(deck, cards):  # noqa: ANN001, ANN202
        for e in cards:
            e.get("modifications", {}).pop("companion", None)
        return real(deck, cards)

    ark.apply_patch = drop_companion
    p = await _propose(h, token, {"action": "set_companion", "name": "Sol Ring", "companion": True})
    a = await apply_any(h, token, p["proposal_id"])
    assert not (a.get("ok") and (a.get("result") or {}).get("verified") is True), a


@pytest.mark.parametrize("mode", ["auto", "semi"])
@pytest.mark.parametrize("cut", ["all", "one"])
async def test_removing_or_cutting_the_companion_waits_in_every_mode(
    stack: Stack, mode: str, cut: str
) -> None:
    """Security review 0.7.42 S-03: the companion rule covered set_companion only, so a plain
    remove of the companion card applied in Full auto and the deck lost its companion."""
    from .test_approval_modes import _set_mode

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, mode)
    row = _row(ark, "Sol Ring")
    row["companion"] = True
    row["quantity"] = 2
    ch = {"action": "remove", "card_name": "Sol Ring", "quantity": 2 if cut == "all" else 1}
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [ch]}))
    assert p["ok"], p
    assert p["assistant_may_apply"] is False and p["risk_reason"] == modes.COMPANION_REASON, p
    assert "(the deck's companion)" in p["diff"], p["diff"]
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and row["companion"] is True, out


def test_the_review_page_names_the_companion_on_a_cut_row() -> None:
    from mtg_gateway.pages import _change_rows

    html_rows, _counts, _net = _change_rows(
        [{"kind": "remove", "name": "Sol Ring", "qty": 1, "companion_card": True}], ""
    )
    assert "Sol Ring (the deck&#x27;s companion)" in html_rows, html_rows
