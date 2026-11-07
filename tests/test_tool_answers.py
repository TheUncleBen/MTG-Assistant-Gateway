"""Tool answers the whole-application audit (0.7.46, BACKEND-AND-TOOLS section 2) found wrong or
unclear: each answer says what happened in words an assistant can pass on, never an empty list or
a cut result that reads as something else."""

from __future__ import annotations

from typing import Any

from .conftest import COLLECTION, Stack, stack_with
from .test_decks_and_proxy import call, linked_user, structured

# the research proxy (validate_decklist) and Scryfall behind the gateway
stack_options = stack_with(COLLECTION)


async def test_my_decks_by_an_unknown_format_is_refused_and_edh_finds_commander_decks(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    bad = structured(await call(h, token, "list_my_decks", {"deck_format": "comander"}))
    assert bad["ok"] is False and bad["error"] == "invalid", bad
    assert "deck_format must be one of" in bad["message"] and "commander" in bad["message"]
    every = structured(await call(h, token, "list_my_decks", {}))
    commander = [d["id"] for d in every["decks"] if d.get("format_name") == "commander"]
    assert commander, every
    for name in ("commander", "edh", " EDH "):
        got = structured(await call(h, token, "list_my_decks", {"deck_format": name}))
        assert got["ok"] and [d["id"] for d in got["decks"]] == commander, (name, got)


async def test_a_deck_too_large_for_one_answer_is_refused_with_the_view_that_answers(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    deck = ark.decks[42]
    rows = list(deck["cards"])
    deck["cards"] = [{**r, "id": 500_000 + i * 1000 + n} for i in range(25) for n, r in enumerate(rows)]
    whole = structured(
        await call(h, token, "get_deck", {"deck_ref": "42", "view": "full", "include_text": True})
    )
    assert whole["ok"] is False and whole["error"] == "too_large", whole
    assert (
        "“Sample Commander Deck” is too large" in whole["message"]
        and "without include_text" in whole["message"]
    ), whole
    full = structured(await call(h, token, "get_deck", {"deck_ref": "42", "view": "full"}))
    assert full["ok"] is False and "with view 'text' or 'summary'" in full["message"], full
    text = structured(await call(h, token, "get_deck", {"deck_ref": "42"}))
    assert text["ok"] is True and text["card_count"] >= 2000, {k: text[k] for k in ("ok", "card_count")}


async def test_deck_stats_names_each_commander_once(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    deck = ark.decks[42]
    leader = next(r for r in deck["cards"] if "Commander" in (r.get("categories") or []))
    deck["cards"] += [{**leader, "id": 700_000 + n} for n in range(3)]
    out = structured(await call(h, token, "deck_stats", {"deck_ref": "42"}))
    names: list[Any] = out["stats"]["commanders"]
    assert names == [leader["card"]["oracleCard"]["name"]], names


async def test_price_history_with_nothing_named_asks_for_a_card_or_deck(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    out = structured(await call(h, token, "card_price_history", {}))
    assert out["ok"] is False and out["error"] == "invalid", out
    assert out["message"] == "Give the card (its watch_id or name) or a deck_id.", out


async def test_a_new_deck_of_one_card_says_one_card(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": "Lone Ring", "deck_format": "commander", "cards": [{"name": "Sol Ring"}]},
        )
    )
    assert p["ok"], p
    assert "(1 card, private" in p["diff"], p["diff"]


async def test_a_research_tool_missing_an_argument_names_the_field_as_listed(stack: Stack) -> None:
    """Research tools take their arguments wrapped in one "params" object upstream; the refusal
    names the field the assistant sees in the listing, without that wrapping."""
    h = stack.h
    token = await linked_user(stack)
    out = structured(await call(h, token, "validate_decklist", {}))
    assert out["ok"] is False and out["error"] == "invalid", out
    assert "decklist is required" in out["message"] and "params." not in out["message"], out
