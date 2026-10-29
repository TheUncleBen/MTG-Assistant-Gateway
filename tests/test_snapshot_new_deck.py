"""A deleted deck's snapshot gives the website a way back (independent review of 0.7.46: the delete
box promises a snapshot under History, but there a deleted deck's snapshot only said it can't be
restored, and only an assistant could rebuild the deck). History offers "Make a new deck from
this": a new-deck proposal with the snapshot's name, format, cards and categories, which the member
reviews and applies like any new deck. The same in the Android app; the assistant's way
(get_snapshot, then propose_new_deck) keeps working."""

from __future__ import annotations

import re

from .conftest import COLLECTION, Stack, stack_with
from .test_browse_collection import api, linked
from .test_decks_and_proxy import call, structured
from .test_notify_app import APP_UA
from .test_round4_data import _app_token

stack_options = stack_with(COLLECTION)


def _sub(st: Stack) -> str:
    return st.gw.db._one("SELECT sub FROM users", ())["sub"]


async def _snapshot_of_42(st: Stack, b) -> tuple[str, dict]:
    """A snapshot of deck 42 as a Modern deck (a page edit takes it), then the deck deleted on
    Archidekt."""
    st.ark.decks[42]["deckFormat"] = 2
    r = await api(
        b,
        "POST",
        "/api/v1/decks/42/edit",
        {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}]},
    )
    assert r.status_code == 201 and r.json()["applied"], r.text
    snap_id = r.json()["snapshot_id"]
    deck = st.gw.db.get_snapshot(snap_id, _sub(st))["deck"]
    st.ark.decks.pop(42)
    return snap_id, deck


def _counts(cards: list[dict]) -> dict[tuple[str, tuple[str, ...]], int]:
    out: dict[tuple[str, tuple[str, ...]], int] = {}
    for c in cards:
        key = (c["card"]["oracleCard"]["name"], tuple(c.get("categories") or []))
        out[key] = out.get(key, 0) + int(c["quantity"])
    return out


async def test_a_live_deck_s_snapshot_offers_restore_and_no_new_deck(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        await api(
            b,
            "POST",
            "/api/v1/decks/42/edit",
            {"changes": [{"action": "set_quantity", "card_name": "Cultivate", "quantity": 2}]},
        )
        page = (await b.http.get("/history?type=snapshots")).text
        assert "Restore (review first)" in page and "Make a new deck from this" not in page
    finally:
        await b.aclose()


async def test_a_deleted_deck_s_snapshot_makes_a_new_deck_proposal_to_review(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        snap_id, deck = await _snapshot_of_42(stack, b)
        for ua in (None, APP_UA):  # the website, and the Android app's own browser
            headers = {"user-agent": ua} if ua else {}
            page = (await b.http.get(f"/history?open=s-{snap_id}", headers=headers)).text
            assert f"action='/history/snapshots/{snap_id}/new-deck'" in page, page
            assert "Make a new deck from this" in page and "Restore (review first)" not in page
        csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page).group(1)
        r = await b.http.post(f"/history/snapshots/{snap_id}/new-deck", data={"csrf": csrf})
        assert r.status_code == 303, r.text
        url = r.headers["location"]
        pid = url.rsplit("/", 1)[1]
        p = stack.gw.db.get_proposal(pid, _sub(stack))
        assert p["kind"] == "create_deck" and p["state"] == "pending"
        assert p["deck_name"] == deck["name"]
        before = len(stack.ark.decks)
        # nothing is made until the member applies it on its review page
        r = await b.http.post(url, data={**await b.review_form(url), "action": "apply"})
        assert r.status_code in (200, 303), r.text
        made = stack.gw.db.get_proposal(pid, _sub(stack))
        assert made["state"] == "applied", made.get("result")
        assert len(stack.ark.decks) == before + 1
        new = stack.ark.decks[int(made["deck_id"])]
        assert new["name"] == deck["name"] and new["deckFormat"] == deck["deckFormat"] == 2
        assert _counts(new["cards"]) == _counts(deck["cards"])  # cards and their categories
    finally:
        await b.aclose()


async def test_the_assistant_can_still_rebuild_a_deleted_deck(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        snap_id, deck = await _snapshot_of_42(stack, b)
        token, _cid = await _app_token(stack.h, "assistant-test")
        snap = structured(await call(stack.h, token, "get_snapshot", {"snapshot_id": snap_id}))
        assert snap["snapshot_id"] == snap_id and snap.get("decklist_text")
        out = structured(
            await call(
                stack.h,
                token,
                "propose_new_deck",
                {"name": deck["name"], "deck_format": "commander", "decklist_text": snap["decklist_text"]},
            )
        )
        assert out.get("proposal_id"), out
    finally:
        await b.aclose()


async def test_a_snapshot_that_is_gone_says_so_and_proposes_nothing(stack: Stack) -> None:  # noqa: F811
    b = await linked(stack)
    try:
        csrf = await b.csrf("/history")
        r = await b.http.post("/history/snapshots/nope/new-deck", data={"csrf": csrf})
        assert r.status_code == 400 and "Nothing was changed on Archidekt" in r.text
        assert "Back to the snapshot" in r.text
        assert not stack.gw.db._one("SELECT 1 FROM proposals WHERE kind = 'create_deck'", ())
    finally:
        await b.aclose()
