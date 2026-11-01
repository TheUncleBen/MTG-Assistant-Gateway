"""compare_decks with simulate for two decks of different sizes (assistant tool review AT-20,
2026-10-28): the paired A/B needs the same size, and the real use, a work-in-progress deck
against the finished one, rarely has it. Each deck now plays its own games under one seed, and
the result says plainly that the numbers are not game for game."""

from __future__ import annotations

from .conftest import COLLECTION, Stack, stack_with
from .fake_mystic_forge import SIM_CALLS
from .test_decks_and_proxy import Browser, call, linked_user, structured

stack_options = stack_with(COLLECTION)

A = "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n// Lands\n99 Island\n"
B = A.replace("99 Island", "102 Island")


async def _compare(stack: Stack, token: str, **extra: object) -> dict:
    args = {"a_list": A, "b_list": B, "simulate": True, "games": 20, **extra}
    return structured(await call(stack.h, token, "compare_decks", args))


async def test_decks_of_different_sizes_play_unpaired_under_one_seed(stack: Stack) -> None:
    token = await linked_user(stack)
    SIM_CALLS.clear()
    out = await _compare(stack, token)
    ab = out["goldfish_ab"]
    assert out["ok"] is True and ab["ok"] is True and ab["unpaired"] is True, out
    runs = [params for tool, params in SIM_CALLS if tool == "goldfish_run"]
    assert len(runs) == 2 and runs[0]["seed"] == runs[1]["seed"] == ab["seed"], SIM_CALLS
    assert runs[0]["n"] == runs[1]["n"] == 20
    assert "not game for game" in ab["note"] and "100" in ab["note"] and "103" in ab["note"], ab
    assert ab["a"]["text"].startswith("# Goldfish run") and ab["b"]["text"].startswith("# Goldfish run"), ab
    # every run is a report (owner, 10-27; whole-application audit row 10): not one paired A/B
    # report (that page reads a paired A/B's deltas) but each deck's own solo test, naming the other
    assert out["stored"] is True and out["report_id"] == out["report_ids"]["a"], out
    assert "kept as a report of its own" in ab["note"], ab
    for side in ("a", "b"):
        got = structured(
            await call(stack.h, token, "get_deck_report", {"report_id": out["report_ids"][side]})
        )
        assert got["ok"] and got["source"]["kind"] == "compare_unpaired", got
        assert got["source"]["label"] == "Comparison, not game for game" and got["source"]["side"] == side
        assert got["goldfish"]["text"] == ab[side]["text"], got  # the solo test as the deck played it
        assert "against" in got["deck_name"], got["deck_name"]
    listed = structured(await call(stack.h, token, "list_deck_reports", {}))["reports"]
    assert {r["report_id"] for r in listed} >= set(out["report_ids"].values()), listed
    b = Browser(stack.h)
    await b.login()
    page = await b.http.get(f"/history/reports/{out['report_ids']['b']}")
    await b.aclose()
    assert page.status_code == 200 and "Comparison, not game for game" in page.text, page.text[:500]


async def test_an_unpaired_comparison_keeps_the_seed_it_was_given(stack: Stack) -> None:
    token = await linked_user(stack)
    SIM_CALLS.clear()
    out = await _compare(stack, token, options={"seed": 7})
    assert out["goldfish_ab"]["seed"] == 7, out
    assert [p["seed"] for t, p in SIM_CALLS if t == "goldfish_run"] == [7, 7]


async def test_decks_of_the_same_size_still_play_the_paired_ab(stack: Stack) -> None:
    token = await linked_user(stack)
    SIM_CALLS.clear()
    out = structured(
        await call(stack.h, token, "compare_decks", {"a_list": A, "b_list": A, "simulate": True, "games": 20})
    )
    assert out["ok"] and "unpaired" not in out["goldfish_ab"], out
    assert [t for t, _p in SIM_CALLS] == ["goldfish_ab"]
