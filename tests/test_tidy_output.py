"""The assistant tool review's naming and size batch (2026-10-28, AT-30 to AT-36): one spelling
and one value set in every tool, numbers at three significant digits, and get_deck without the
fields an assistant does not use."""

from __future__ import annotations

from typing import Any

from mtg_gateway import app as app_module
from mtg_gateway.tidy import tidy_float, tidy_text, tidy_value

from .conftest import COLLECTION, Stack, stack_with
from .test_decks_and_proxy import call, linked_user, structured

stack_options = stack_with(COLLECTION)


def _rows(deck: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {r["name"]: r for r in deck["cards"]}


async def test_get_deck_rows_use_the_write_tools_finishes_and_color_letters(stack: Stack) -> None:
    """get_deck said "Normal", "Foil", "Etched" and ["White", "Blue"]; every write tool takes
    nonfoil, foil, etched and the letters, so an assistant echoed "Normal" back (AT-30)."""
    deck = stack.ark.decks[42]
    deck["cards"][0]["modifier"] = "Foil"
    deck["cards"][0]["card"]["oracleCard"]["colorIdentity"] = ["Green", "Blue"]
    token = await linked_user(stack)
    out = structured(await call(stack.h, token, "get_deck", {"deck_ref": "42", "view": "cards"}))
    finishes = {r["finish"] for r in out["cards"]}
    assert finishes <= {"nonfoil", "foil", "etched"} and "foil" in finishes, finishes
    first = _rows(out)[deck["cards"][0]["card"]["oracleCard"]["name"]]
    assert first["color_identity"] == ["G", "U"], first


async def test_get_deck_cards_leave_out_flavor_artist_and_split_types(stack: Stack) -> None:
    """get_deck cards with include_text was 58 KB for 100 cards: flavor text and artist are of no
    use to an assistant, types and subtypes repeat type_line (AT-32). The full view keeps them."""
    deck = stack.ark.decks[42]
    oracle = deck["cards"][0]["card"]["oracleCard"]
    deck["cards"][0]["card"]["flavor"] = "A long flavor text."
    deck["cards"][0]["card"]["artist"] = "Some Artist"
    token = await linked_user(stack)
    args = {"deck_ref": "42", "view": "cards", "include_text": True}
    cards = structured(await call(stack.h, token, "get_deck", args))
    row = _rows(cards)[oracle["name"]]
    assert "oracle_text" in row and "type_line" in row, row
    assert not {"flavor_text", "artist", "types", "subtypes"} & set(row), row
    full = structured(await call(stack.h, token, "get_deck", {**args, "view": "full"}))
    assert _rows(full)[oracle["name"]]["flavor_text"] == "A long flavor text.", _rows(full)


async def test_get_deck_text_carries_the_headline_numbers_not_the_whole_stats(stack: Stack) -> None:
    """get_deck sent the same ~5 KB statistics as deck_stats on every read (AT-31): its text and
    cards views carry stats_brief, the summary view (and deck_stats) the whole statistics."""
    token = await linked_user(stack)
    text = structured(await call(stack.h, token, "get_deck", {"deck_ref": "42"}))
    assert "stats" not in text and text["stats_brief"]["card_count"] == text["card_count"], text
    assert {"legal", "problems", "color_identity", "price_total", "bracket_estimate"} <= set(
        text["stats_brief"]
    )
    summary = structured(await call(stack.h, token, "get_deck", {"deck_ref": "42", "view": "summary"}))
    stats = structured(await call(stack.h, token, "deck_stats", {"deck_ref": "42"}))
    assert "mana_curve" in summary["stats"] and "mana_curve" in str(stats), summary


async def test_stats_use_one_spelling_and_one_legality_list(stack: Stack) -> None:
    """deck_stats said colour_pips and colour_identity where every other tool says color, and had
    legality_problems beside checks.legal_problems (AT-30)."""
    token = await linked_user(stack)
    out = await call(stack.h, token, "get_deck", {"deck_ref": "42", "view": "summary"})
    stats = structured(out)["stats"]
    assert {"color_pips", "color_identity", "cards_color_identity"} <= set(stats), sorted(stats)
    assert "colour" not in out["content"][0]["text"], out["content"][0]["text"][:300]
    assert "legality_problems" not in stats and "color_identity_violations" in stats["checks"], stats


def test_long_floats_are_sent_at_three_significant_digits() -> None:
    """0.32691781564606526 in a result only costs tokens (AT-34). A price keeps its cents."""
    assert tidy_float(0.32691781564606526) == 0.327
    assert tidy_float(12.349999999) == 12.35 and tidy_float(1234.5678901) == 1234.57
    assert tidy_float(0.000123456789) == 0.000123
    assert tidy_value({"a": [0.32691781564606526, 3, 1.5, True], "b": "x"}) == {
        "a": [0.327, 3, 1.5, True],
        "b": "x",
    }
    text = '## Metrics\n```json\n{"ci": [0.31234567891, 0.4], "version": "0.7.46"}\n```'
    assert tidy_text(text) == '## Metrics\n```json\n{"ci": [0.312, 0.4], "version": "0.7.46"}\n```'


async def test_the_middleware_rounds_the_text_and_the_structured_content_alike() -> None:
    mw = app_module.schema_error_middleware()

    class Ctx:
        method = "tools/call"

    data = {"win_rate": 0.32691781564606526, "price": 412.3}
    result = {
        "content": [{"type": "text", "text": '{\n  "win_rate": 0.32691781564606526,\n  "price": 412.3\n}'}],
        "structuredContent": data,
    }

    async def call_next(_ctx: Any) -> Any:
        return result

    out = await mw(Ctx(), call_next)
    assert out["structuredContent"] == {"win_rate": 0.327, "price": 412.3}, out
    assert out["content"][0]["text"] == '{"win_rate":0.327,"price":412.3}', out


async def test_run_deck_report_answers_with_the_headline_and_get_deck_report_has_the_rest(
    stack: Stack,
) -> None:
    """run_deck_report's answer was ~30 KB: the statistics a third time, the Metrics JSON inside
    the simulation's text, a copies map and "forge skipped" twice (AT-33). Its answer now has the
    headline numbers; get_deck_report has the whole report."""
    token = await linked_user(stack)
    rep = structured(await call(stack.h, token, "run_deck_report", {"deck_ref": "42", "games": 20}))
    assert rep["ok"] and "stats" not in rep and rep["stats_brief"]["card_count"] == 100, rep
    assert "```json" not in rep["goldfish"]["text"] and "get_deck_report" in rep["goldfish"]["text"]
    assert "quantities" not in rep["goldfish"] and "coverage" in rep, rep
    assert rep["forge"] is None or rep["forge"].get("state") != "skipped", rep
    assert "get_deck_report" in rep["full_report"], rep
    full = structured(await call(stack.h, token, "get_deck_report", {"report_id": rep["report_id"]}))
    assert "mana_curve" in full["stats"] and "```json" in full["goldfish"]["text"], full
