"""A report's three engines side by side (assistant tool review C-7, 2026-10-28): the solo test
said 53% kill by turn 10, Forge solo won 5 of 5, Forge against precons won 1 of 5, and a member
comparing them was left to guess why. side_by_side puts each engine's headline number next to
the others with what it plays (the solo test's coverage, Forge's full rules) and says how to read
them together."""

from __future__ import annotations

import json
from pathlib import Path

from mtg_gateway.reports import side_by_side

from .conftest import COLLECTION, Stack, stack_with
from .test_decks_and_proxy import call, linked_user, structured

stack_options = stack_with(COLLECTION)

GOLDFISH = {
    "tool": "goldfish_run",
    "ok": True,
    "text": (Path(__file__).parent / "fixtures" / "reports" / "goldfish_run.md").read_text(),
}
SOLO = {
    "state": "done",
    "result": {
        "games": 5,
        "until_turn": 10,
        "won": 5,
        "won_rate": 1.0,
        "won_rate_95": [0.566, 1.0],
        "median_won_turn": 9,
    },
}
FORGE = {
    "state": "done",
    "games_requested": 5,
    "result": {
        "games": 5,
        "seats": [
            {"deck": "Mine", "wins": 1, "win_rate": 0.2, "win_rate_95": [0.036, 0.624]},
            {"deck": "Precon A", "wins": 2, "win_rate": 0.4, "win_rate_95": [0.1, 0.8]},
        ],
    },
}


def test_each_engine_says_its_number_and_what_it_plays() -> None:
    out = side_by_side(GOLDFISH, FORGE, SOLO, deck_size=98)
    solo_test, forge_solo, precons = out["solo_test"], out["forge_solo"], out["forge_vs_precons"]
    assert solo_test["kill_pct"] == 57.0 and solo_test["until_turn"] == 10, solo_test
    assert solo_test["played_with_effect"] == f"{solo_test['modeled']} of 98 cards", solo_test
    assert forge_solo == {
        "plays": "Forge, every card by the full rules, against opponents who do nothing",
        "games": 5,
        "won_rate": 1.0,
        "won_rate_95": [0.566, 1.0],
        "median_won_turn": 9,
        "until_turn": 10,
    }, forge_solo
    assert precons["win_rate"] == 0.2 and precons["games"] == 5 and precons["opponents"] == 1, precons
    assert precons["enough"] is False, precons  # 5 games: a 95% interval far too wide to lead with
    note = out["note"]
    assert "different questions" in note and "coverage" in note and "too few games" in note, note


def test_nothing_side_by_side_with_one_engine_only() -> None:
    assert side_by_side(GOLDFISH, None, None, deck_size=98) is None
    assert side_by_side(None, FORGE, None, deck_size=98) is None
    assert side_by_side(GOLDFISH, {"state": "queued"}, {"state": "queued"}, deck_size=98) is None


async def test_the_report_and_its_forge_view_carry_it(stack: Stack) -> None:
    token = await linked_user(stack)
    rep = structured(await call(stack.h, token, "run_deck_report", {"deck_ref": "42", "games": 20}))
    rid = rep["report_id"]
    with stack.gw.db.tx() as c:
        c.execute(
            "UPDATE reports SET goldfish_json = ?, forge_json = ?, forge_state = 'done', "
            "forge_solo_json = ?, forge_solo_state = 'done' WHERE id = ?",
            (json.dumps(GOLDFISH), json.dumps(FORGE), json.dumps(SOLO), rid),
        )
    full = structured(await call(stack.h, token, "get_deck_report", {"report_id": rid}))
    assert full["side_by_side"]["forge_vs_precons"]["win_rate"] == 0.2, full.get("side_by_side")
    forge = structured(await call(stack.h, token, "get_deck_report", {"report_id": rid, "view": "forge"}))
    assert forge["side_by_side"] == full["side_by_side"], forge
