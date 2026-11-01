"""The member's one-click saves cost at most three Archidekt calls at the shipped settings (0.7.46
audit, row 15). The guards in test_deck_edit_api and test_deck_categories_page run with a 60 s
reuse window, so a busy test machine can't make them flaky; they never showed whether the shipped
2 s window holds when Archidekt is slow. Here the window is the shipped one, the pacer is the
shipped one (a start a second, 30 a minute), and the fake Archidekt takes a second over every call,
as a slow day does."""

from __future__ import annotations

import asyncio
import dataclasses

import httpx

from mtg_gateway import decks as decks_mod
from mtg_gateway.archidekt import Pacer
from mtg_gateway.config import Settings

from .conftest import COLLECTION, Stack, stack_with
from .test_browse_collection import api, linked
from .test_companion import linked_browser
from .test_deck_categories import _give_categories
from .test_deck_categories_page import POST, _csrf

stack_options = stack_with(COLLECTION)

SLOW = 1.0


def _shipped_and_slow(stack: Stack) -> None:
    assert decks_mod.ONE_CLICK_REUSE == 2.0  # the shipped window, not a test's
    shipped = {f.name: f.default for f in dataclasses.fields(Settings)}
    stack.gw.decks.client.pacer = Pacer(
        shipped["archidekt_min_interval"], shipped["archidekt_max_per_minute"]
    )
    orig = stack.ark.transport.handler

    async def slow(req: httpx.Request) -> httpx.Response:
        await asyncio.sleep(SLOW)
        out = orig(req)
        return await out if asyncio.iscoroutine(out) else out

    stack.ark.transport.handler = slow


async def test_a_deck_page_edit_costs_three_calls_at_the_shipped_window(stack: Stack) -> None:
    b = await linked(stack)
    try:
        csrf = await b.csrf("/account/details")
        _shipped_and_slow(stack)
        for qty in (2, 3):  # the first save of a sitting and the one after it
            stack.ark.calls.clear()
            r = await api(
                b,
                "POST",
                "/api/v1/decks/42/edit",
                {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": qty}]},
                csrf=csrf,
            )
            assert r.status_code == 201 and r.json()["applied"] is True, r.text
            assert [m for m, _p in stack.ark.calls] == ["GET", "PATCH", "GET"], stack.ark.calls
            assert r.json()["rows"][0]["qty"] == qty
    finally:
        await b.aclose()


async def test_a_category_save_costs_three_calls_at_the_shipped_window(stack: Stack) -> None:
    _give_categories(stack.ark)
    b = await linked_browser(stack)
    try:
        _page, csrf = await _csrf(b)
        _shipped_and_slow(stack)
        for name in ("Removal", "Draw"):
            stack.ark.calls.clear()
            r = await b.http.post(
                POST,
                data={"csrf": csrf, "action": "create", "category": name, "json": "1"},
                headers={"Accept": "application/json"},
            )
            assert r.status_code == 200 and r.json()["ok"] is True, r.text
            assert [m + " " + p for m, p in stack.ark.calls] == [
                "GET /api/decks/42/",
                "POST /api/decks/createCategory/",
                "GET /api/decks/42/",
            ], stack.ark.calls
    finally:
        await b.aclose()
