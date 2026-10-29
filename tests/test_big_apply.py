"""A big new deck (Ben, 27 Oct 2026: a 125-card new-deck proposal took many minutes, and his own
edits to the new deck meanwhile did not save): the cards go to Archidekt in a few batched requests
instead of one request per card, each printing is looked up once per apply, and while the deck is
still being made an edit to it is refused with how far the apply got, instead of racing it."""

from __future__ import annotations

import asyncio
import dataclasses
import html
import re
from collections import Counter

import httpx
import pytest

from mtg_gateway import decks as decks_mod

from . import fake_archidekt as FA
from .conftest import Stack
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .track_a_helpers import press_approve

CARDS = 120  # different cards; five more rows repeat some of them in another category


@pytest.fixture
def big_list(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    monkeypatch.setattr(decks_mod, "APPLY_WAIT_SECONDS", 30)
    for n in range(CARDS):
        monkeypatch.setitem(
            FA.CARD_DB, f"test card {n}", {"id": 80000 + n, "oracleCard": {"name": f"Test Card {n}"}}
        )
    rows = [{"name": f"Test Card {n}", "quantity": 1, "categories": ["Main"]} for n in range(CARDS)]
    rows += [{"name": f"Test Card {n}", "quantity": 1, "categories": ["Maybeboard"]} for n in range(5)]
    return rows


def _calls(stack: Stack, since: int) -> Counter:
    out: Counter = Counter()
    for method, path in stack.ark.calls[since:]:
        if path.startswith("/api/cards/v2/"):
            out["card lookups"] += 1
        elif path.endswith("/modifyCards/v2/"):
            out["card writes"] += 1
        else:
            out[f"{method} other"] += 1
    return out


async def test_a_125_card_new_deck_takes_a_few_writes_not_one_per_card(stack: Stack, big_list) -> None:
    h = stack.h
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_new_deck", {"name": "Big brew", "cards": big_list}))
    assert p["ok"], p
    since = len(stack.ark.calls)
    out = await press_approve(h, token, p["proposal_id"])
    assert out["ok"] and out["state"] == "applied", out
    calls = _calls(stack, since)
    print("125-card new deck, Archidekt requests:", dict(calls), "total", sum(calls.values()))
    # one lookup per different card, not one per row; 125 rows in batches of at most 50
    assert calls["card lookups"] == CARDS
    assert calls["card writes"] == 3
    assert all(len(b["cards"]) <= decks_mod.CARD_WRITE_BATCH for b in stack.ark.patches)
    made = stack.ark.decks[int(out["result"]["deck_id"])]
    assert sum(c["quantity"] for c in made["cards"]) == 125
    assert sum(calls.values()) <= CARDS + 10


async def test_a_batch_archidekt_refuses_is_sent_one_card_at_a_time(stack: Stack, big_list) -> None:
    """Archidekt's modifyCards takes a list (archidekt.com's own editor sends several cards in one
    request); should it ever refuse a list (HTTP 400), the cards of that batch go one at a time."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_new_deck", {"name": "Big brew", "cards": big_list[:60]}))
    orig = ark.transport.handler

    def no_lists(req: httpx.Request):  # noqa: ANN202
        if req.url.path.endswith("/modifyCards/v2/") and req.method == "PATCH":
            import json

            if len(json.loads(req.content)["cards"]) > 1:
                ark.calls.append((req.method, req.url.path))
                return httpx.Response(400, json={"cards": ["Expected a single card."]})
        return orig(req)

    ark.transport.handler = no_lists
    out = await press_approve(h, token, p["proposal_id"])
    assert out["ok"] and out["state"] == "applied", out
    made = ark.decks[int(out["result"]["deck_id"])]
    assert sum(c["quantity"] for c in made["cards"]) == 60


async def test_a_batch_saved_with_an_answer_that_is_not_json_is_not_sent_again(
    stack: Stack, big_list
) -> None:
    """Archidekt saved a batch but its answer could not be read (a 200 that is not JSON): the
    cards may be in, so they are not sent again one at a time, which would double them. The
    other batches still go, and the read-back after says whether every card is there."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_new_deck", {"name": "Big brew", "cards": big_list[:60]}))
    ark.garble_card_writes = 1
    out = await press_approve(h, token, p["proposal_id"])
    assert out["ok"] and out["state"] == "applied", out
    made = ark.decks[int(out["result"]["deck_id"])]
    assert sum(c["quantity"] for c in made["cards"]) == 60
    assert [len(b["cards"]) for b in ark.patches] == [
        decks_mod.CARD_WRITE_BATCH,
        60 - decks_mod.CARD_WRITE_BATCH,
    ]


async def test_a_batch_whose_answer_is_unreadable_and_did_not_land_goes_again_once(
    stack: Stack, big_list
) -> None:
    """The same unreadable answer for a batch Archidekt did not save: the deck is read, and the
    cards it does not have go once more, one at a time."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_new_deck", {"name": "Big brew", "cards": big_list[:60]}))
    orig = ark.transport.handler
    seen = {"n": 0}

    def first_lost(req: httpx.Request):  # noqa: ANN202
        if req.url.path.endswith("/modifyCards/v2/") and req.method == "PATCH":
            seen["n"] += 1
            if seen["n"] == 1:
                ark.calls.append((req.method, req.url.path))
                return httpx.Response(200, text="<html>Saved</html>")
        return orig(req)

    ark.transport.handler = first_lost
    out = await press_approve(h, token, p["proposal_id"])
    assert out["ok"] and out["state"] == "applied", out
    made = ark.decks[int(out["result"]["deck_id"])]
    assert sum(c["quantity"] for c in made["cards"]) == 60
    # the lost batch, then the second batch, then the lost batch's cards one at a time
    assert seen["n"] == 2 + decks_mod.CARD_WRITE_BATCH


async def test_an_edit_to_a_deck_still_being_made_is_refused_with_its_progress(
    stack: Stack, big_list
) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    svc = h.app.state.gateway.decks
    p = structured(await call(h, token, "propose_new_deck", {"name": "Big brew", "cards": big_list}))
    orig = ark.transport.handler
    release = asyncio.Event()
    writes = {"n": 0}

    async def slow_second_batch(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/modifyCards/v2/") and req.method == "PATCH":
            writes["n"] += 1
            if writes["n"] == 2:
                await release.wait()
        return orig(req)

    ark.transport.handler = slow_second_batch
    applying = asyncio.ensure_future(press_approve(h, token, p["proposal_id"]))
    async with asyncio.timeout(10):
        while writes["n"] < 2:
            await asyncio.sleep(0.01)
    row = h.db.get_proposal(p["proposal_id"], "user-1")
    deck_id = str(row["deck_id"])
    assert deck_id.isdigit()
    assert svc._deck_locks[f"user-1:{deck_id}"].locked()  # held under the new deck's own id
    b = Browser(h)
    await b.login(f"/decks/{deck_id}")
    try:
        csrf = await b.csrf(f"/decks/{deck_id}")
        page = await b.http.get(f"/decks/{deck_id}")
        text = html.unescape(page.text)
        assert (
            "This deck is still being made: 50 of 125 cards added so far. "
            "Changes wait until it is done." in " ".join(re.sub(r"<[^>]+>", " ", text).split())
        )
        notice = text.split("class='notice making'", 1)[1].split("</p>", 1)[0]
        assert "<span class='ui-spin' aria-hidden='true'></span>" in notice
        assert f"data-made-poll='/decks/{deck_id}/made'" in notice
        # the edit controls are off meanwhile: Edit deck, Quick add, Add a package, the rows' edits
        assert f"href='/decks/{deck_id}/edit'" not in text
        assert re.search(r"<button[^>]*disabled[^>]*>[^<]*<svg[^>]*>.*?</svg> Edit deck</button>", text)
        assert re.search(r"<input id='quick'[^>]* disabled", text)
        assert "class='btn addpkg' aria-disabled='true'" in text
        assert "data-own='1'" not in text.split("class='deckview", 1)[1].split(">", 1)[0]
        made = await b.http.get(f"/decks/{deck_id}/made")
        assert made.status_code == 200 and made.json() == {"made": False}
        # another member learns nothing about it (security review 2, R2-05): no deck of theirs is
        # being made, so the answer is the same as for any deck
        from .test_access_control import AMY

        alice, h.idp.user = h.idp.user, AMY
        amy = Browser(h)
        try:
            await amy.login()
            theirs = await amy.http.get(f"/decks/{deck_id}/made")
        finally:
            await amy.aclose()
            h.idp.user = alice
        r = await b.http.post(
            f"/api/v1/decks/{deck_id}/edit",
            json={"changes": [{"action": "set_quantity", "card_name": "Test Card 50", "quantity": 2}]},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code == 409, r.text
        assert r.json()["message"] == (
            "This deck is still being made: 50 of 125 cards added so far. Try again once it is done."
        )
        # nothing was left waiting in Proposals by the refused edit
        assert [x["id"] for x in h.db.pending_board("user-1", deck_id)] == []
        release.set()
        out = await applying
        assert out["state"] == "applied", out
        assert theirs.status_code == 200 and theirs.json() == {"made": True}, theirs.text
        made = await b.http.get(f"/decks/{deck_id}/made")
        assert made.json() == {"made": True}
        # once it is made, the same edit goes through
        r = await b.http.post(
            f"/api/v1/decks/{deck_id}/edit",
            json={"changes": [{"action": "set_quantity", "card_name": "Test Card 50", "quantity": 2}]},
            headers={"X-CSRF-Token": csrf},
        )
        assert r.status_code in (200, 201) and r.json()["ok"] and r.json()["applied"], r.text
    finally:
        release.set()
        await b.aclose()


async def test_a_new_deck_that_does_not_match_names_its_deck_id(stack: Stack, big_list) -> None:
    """A new deck saved with a card short says which deck was made, as "deck ID N"."""
    h = stack.h
    token = await linked_user(stack)
    svc = h.app.state.gateway.decks
    p = structured(await call(h, token, "propose_new_deck", {"name": "Short", "cards": big_list[:3]}))
    read = svc.get_deck

    async def one_short(*a, **k):  # noqa: ANN002, ANN003, ANN202
        deck = await read(*a, **k)
        return dataclasses.replace(deck, cards=deck.cards[:-1])

    svc.get_deck = one_short
    out = await press_approve(h, token, p["proposal_id"])
    row = h.db.get_proposal(p["proposal_id"], "user-1")
    message = str(out.get("message") or out.get("detail") or row["result"])
    assert row["state"] == "failed", (row["state"], out)
    assert f"The deck was created (deck ID {row['deck_id']}) but these cards" in message, message
