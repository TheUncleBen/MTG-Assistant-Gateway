"""Deck settings' Theorycrafted and Game Type (Archidekt parity release 1, approved by Ben
22 Oct 2026, "All as recommended"): the same PATCH /decks/{id}/update/ as the other settings
(``theorycrafted``: true or false; ``game``: null for Any, 1 Paper, 2 MTGO, 3 Arena, as the
site's deck settings code sends them), proposed, applied, read back and restored like them."""

from __future__ import annotations

import pytest

from mtg_gateway.decks import DeckError, details_payload, parse_details

from .conftest import Stack
from .test_decks_and_proxy import call, linked_user, structured
from .track_a_helpers import apply_any


def test_the_two_settings_parse_and_send_archidekts_fields() -> None:
    got = parse_details({"theorycrafted": True, "game": "Arena"})
    assert got == {"theorycrafted": True, "game": "arena"}
    assert details_payload(got) == {"theorycrafted": True, "game": 3}
    assert details_payload(parse_details({"game": "any"})) == {"game": None}
    assert details_payload(parse_details({"game": "paper"})) == {"game": 1}
    assert details_payload(parse_details({"game": "mtgo"})) == {"game": 2}


@pytest.mark.parametrize(
    "bad", [{"theorycrafted": "yes"}, {"game": "commander"}, {"game": 3}, {"game": True}]
)
def test_a_bad_value_is_refused(bad: dict) -> None:
    with pytest.raises(DeckError):
        parse_details(bad)


async def test_propose_apply_read_back_and_restore(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_deck_details",
            {"deck_id": "42", "details": {"theorycrafted": True, "game": "arena"}},
        )
    )
    assert p["ok"] and p["kind"] == "details", p
    assert set(p["diff"].splitlines()) == {"theorycrafted: no -> yes", "game: Any -> Arena"}, p["diff"]
    a = await apply_any(h, token, p["proposal_id"])
    assert a["ok"] and a["state"] == "applied" and a["result"]["verified"] is True, a
    assert [u for u in ark.updates if u["deck_id"] == 42] == [
        {"deck_id": 42, "theorycrafted": True, "game": 3}
    ]
    assert ark.decks[42]["theorycrafted"] is True and ark.decks[42]["game"] == 3
    # Restore under History puts both back
    r = structured(await call(h, token, "propose_restore_snapshot", {"snapshot_id": a["snapshot_id"]}))
    assert r["ok"], r
    assert {"theorycrafted: yes -> no", "game: Arena -> Any"} <= set(r["diff"].splitlines()), r["diff"]
    a2 = await apply_any(h, token, r["proposal_id"])
    assert a2["ok"] and a2["result"]["verified"] is True, a2
    assert ark.decks[42]["theorycrafted"] is False and ark.decks[42].get("game") is None


async def test_the_settings_page_shows_and_saves_both(stack: Stack) -> None:
    import re

    from .test_companion import NAV, linked_browser

    ark = stack.ark
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/decks/42/settings", headers=NAV)).text
        assert "<label for='theorycrafted'>Theorycrafted</label>" in page
        assert "<input type='hidden' name='theorycrafted_shown' value='1'>" in page
        assert "<select id='game' name='game'><option value='any' selected>Any</option>" in page
        assert "<option value='arena'>Arena</option>" in page
        csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page).group(1)
        base = {"csrf": csrf, "name": ark.decks[42]["name"], "description": ""}
        r = await b.http.post(
            "/decks/42/settings",
            data={**base, "theorycrafted_shown": "1", "theorycrafted": "1", "game": "paper"},
            headers=NAV,
        )
        assert r.status_code == 303, r.text
        assert ark.decks[42]["theorycrafted"] is True and ark.decks[42]["game"] == 1
        # a form from before this release (no switch, no marker) never switches Theorycrafted off
        sent = len(ark.updates)
        r = await b.http.post("/decks/42/settings", data={**base, "private": "1"}, headers=NAV)
        assert all("theorycrafted" not in u and "game" not in u for u in ark.updates[sent:]), ark.updates
        assert ark.decks[42]["theorycrafted"] is True and ark.decks[42]["game"] == 1
    finally:
        await b.aclose()


async def test_a_new_deck_takes_both(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {
                "name": "Arena brew",
                "deck_format": "standard",
                "decklist_text": "4 Opt\n",
                "theorycrafted": True,
                "game": "arena",
            },
        )
    )
    assert p["ok"], p
    assert (
        p["diff"].splitlines()[0] == "New standard deck 'Arena brew' (4 cards, private, theorycrafted, Arena)"
    )
    a = await apply_any(h, token, p["proposal_id"])
    assert a["ok"] and a["result"]["verified"] is True, a
    made = ark.decks[int(a["result"]["deck_id"])]
    assert made["theorycrafted"] is True and made["game"] == 3
    bad = structured(
        await call(h, token, "propose_new_deck", {"name": "x", "decklist_text": "1 Opt", "game": "vintage"})
    )
    assert bad["ok"] is False, bad


async def test_the_new_deck_box_has_both(stack: Stack) -> None:
    import re

    from .test_companion import NAV, linked_browser

    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/decks", headers=NAV)).text
        assert "<input type='checkbox' name='theorycrafted' value='1'>" in page
        assert "<select id='game' name='game'><option value='any' selected>Any</option>" in page
        csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page).group(1)
        r = await b.http.post(
            "/decks/new",
            data={
                "csrf": csrf,
                "name": "Paper",
                "format": "commander",
                "game": "paper",
                "theorycrafted": "1",
            },
            headers=NAV,
        )
        assert r.status_code == 303, r.text
        made = max(stack.ark.decks)
        assert stack.ark.decks[made]["game"] == 1 and stack.ark.decks[made]["theorycrafted"] is True
    finally:
        await b.aclose()


async def test_the_settings_page_has_one_element_per_id(stack: Stack) -> None:
    """The Main settings form was id='main' like the page's own <main> (the Skip to content
    target), so the skip link and the section menu could land on either."""
    import re
    from collections import Counter

    from .test_companion import NAV, linked_browser

    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/decks/42/settings", headers=NAV)).text
        twice = [i for i, n in Counter(re.findall(r"\bid='([^']+)'", page)).items() if n > 1]
        assert twice == [], twice
        assert "<a href='#ds-main'>Main settings</a>" in page and "id='ds-main'" in page
    finally:
        await b.aclose()


async def test_a_stale_settings_page_sends_only_what_the_member_changed(stack: Stack) -> None:
    """Security review 0.7.42 S-04: the Main settings form had no baseline, so a page opened
    before a change made elsewhere (an assistant's, or on archidekt.com) put the old Theorycrafted,
    Game type, Private and Unlisted back when the member saved anything else from it."""
    import re

    from .test_companion import NAV, linked_browser

    ark = stack.ark
    b = await linked_browser(stack)
    try:
        page = (await b.http.get("/decks/42/settings", headers=NAV)).text
        csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page).group(1)
        was = dict(re.findall(r"<input type='hidden' name='(was_[a-z_]+)' value='([^']*)'>", page))
        assert {"was_private", "was_unlisted", "was_theorycrafted", "was_game", "was_name"} <= set(was), was
        # meanwhile, elsewhere: Theorycrafted on, Arena, unlisted, and a new description
        ark.decks[42].update(theorycrafted=True, game=3, unlisted=True, description="Changed elsewhere")
        name = ark.decks[42]["name"]
        form = {
            **was,
            "csrf": csrf,
            "name": name + " v2",  # the one thing the member changed
            "description": "",
            "theorycrafted_shown": "1",
            "game": "any",
            "deck_format": was["was_deck_format"],  # as the page drew it
        }
        r = await b.http.post("/decks/42/settings", data=form, headers=NAV)
        assert r.status_code == 303, r.text
        assert ark.updates[-1] == {"deck_id": 42, "name": name + " v2"}, ark.updates[-1]
        d = ark.decks[42]
        assert d["theorycrafted"] is True and d["game"] == 3 and d["unlisted"] is True
        assert d["description"] == "Changed elsewhere"
        # a field the member did change from the stale page is still sent
        r = await b.http.post(
            "/decks/42/settings", data={**form, "name": name + " v2", "game": "paper"}, headers=NAV
        )
        assert r.status_code == 303, r.text
        assert d["game"] == 1 and d["theorycrafted"] is True
        # nothing changed at all: nothing is sent, and the page says so
        sent = len(ark.updates)
        r = await b.http.post("/decks/42/settings", data={**form, "name": name}, headers=NAV)
        assert len(ark.updates) == sent and "nothing to save" in r.text.lower(), r.text[-600:]
    finally:
        await b.aclose()


@pytest.mark.parametrize(
    ("text", "says"),
    [
        ("".join(f"1 Card {i} [C{i}{{noPrice}}]\n" for i in range(31)), "at most 30"),
        ("1 Opt [" + "L" * 61 + "{noPrice}]\n", "1 to 60 characters"),
        ("1 Island [Ramp{noPrice}]\n1 Forest [ramp{noPrice}]\n", "differ only in capitals"),
    ],
)
async def test_a_new_decks_flagged_categories_are_held_to_the_category_limits(
    stack: Stack, text: str, says: str
) -> None:
    """Security review 0.7.42 S-07: each flagged category is a Create Category call, so a pasted
    list is held to the same limits as propose_deck_categories (30, 60 characters, case-blind)."""
    h = stack.h
    token = await linked_user(stack)
    p = structured(
        await call(
            h, token, "propose_new_deck", {"name": "Big", "deck_format": "commander", "decklist_text": text}
        )
    )
    assert p["ok"] is False and says in p.get("error", "") + p.get("message", ""), p
