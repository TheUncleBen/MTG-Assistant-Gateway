"""0.7.46, one live card per proposal (the owner's ChatGPT session: ten big cards for one new deck,
two of them still showing Approve). A waiting new deck is changed in place (amend_proposal with
new_deck, a second propose_new_deck of the same name, or replaces), each card knows its own
number among its proposal's cards so an older one steps aside, results stay small, the model
reads a proposal with proposal_status (no card) and checks a new deck with check_new_deck, and the
reason typed under the card's Reject is kept."""

from __future__ import annotations

import html
import re

from mtg_gateway import db as db_module
from mtg_gateway.approve import APPROVAL_META_KEY, CARD_ROWS, CARD_URI

from .conftest import Stack
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .test_in_chat_approve import _tools
from .test_round4_data import _app_token
from .test_track_a_board import SUB, _mode


def _cards(*names: str) -> list[dict]:
    return [{"name": n, "quantity": 1} for n in names]


async def _new(h, token: str, name: str = "Atraxa Superfriends", **more) -> dict:
    res = await call(h, token, "propose_new_deck", {"name": name, "cards": _cards("Sol Ring", "Opt"), **more})
    return res


async def _pending(h) -> int:
    return h.db.count_pending_proposals(SUB)


# -- 1. a waiting new deck is amended in place -------------------------------------------------------


async def test_a_waiting_new_deck_is_amended_in_place_and_an_older_cards_press_is_refused(
    stack: Stack,
) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    res = await _new(h, token)
    first = structured(res)
    pid, old_code = first["proposal_id"], res["_meta"][APPROVAL_META_KEY]
    assert first["kind"] == "create_deck" and first["version"] == 1
    amend = {
        "proposal_id": pid,
        "version": 1,
        "new_deck": {"name": "Atraxa Counters", "cards": _cards("Sol Ring", "Island", "Forest")},
    }
    res2 = await call(h, token, "amend_proposal", amend)
    out = structured(res2)
    assert out["ok"] and out["proposal_id"] == pid and out["version"] == 2, out
    assert out["deck_name"] == "Atraxa Counters"
    assert [r["name"] for r in out["rows"] if r["kind"] == "add"] == ["Sol Ring", "Island", "Forest"]
    assert out["rows"][0]["kind"] == "new_deck" and out["rows"][0]["format"] == "commander"
    assert await _pending(h) == 1
    stored = h.db.get_proposal(pid, SUB)
    assert stored["deck_name"] == "Atraxa Counters" and stored["changes"]["name"] == "Atraxa Counters"
    assert [c["name"] for c in stored["changes"]["cards"]] == ["Sol Ring", "Island", "Forest"]
    # the first card's code is for version 1: refused as changed, nothing made on Archidekt
    before = set(ark.decks)
    stale = structured(
        await call(
            h, token, "confirm_proposal", {"proposal_id": pid, "approval": old_code, "decision": "approve"}
        )
    )
    assert stale["ok"] is False and stale["error"] == "changed", stale
    assert set(ark.decks) == before
    # the newest card's code applies the deck as amended
    done = structured(
        await call(
            h,
            token,
            "confirm_proposal",
            {"proposal_id": pid, "approval": res2["_meta"][APPROVAL_META_KEY], "decision": "approve"},
        )
    )
    assert done["ok"] and done["state"] == "applied", done
    (new_id,) = set(ark.decks) - before
    assert ark.decks[new_id]["name"] == "Atraxa Counters"


async def test_an_amend_keeps_every_field_it_does_not_name(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": "Mono U", "deck_format": "modern", "private": False, "decklist_text": "4 Opt\n4 Ponder"},
        )
    )
    out = structured(
        await call(
            h,
            token,
            "amend_proposal",
            {"proposal_id": p["proposal_id"], "version": 1, "new_deck": {"name": "Mono Blue"}},
        )
    )
    assert out["ok"] and out["version"] == 2, out
    head = out["rows"][0]
    assert head["name"] == "Mono Blue" and head["format"] == "modern" and head["private"] is False
    assert head["cards"] == 8
    # a stale version is refused, the proposal unchanged
    stale = structured(
        await call(
            h,
            token,
            "amend_proposal",
            {"proposal_id": p["proposal_id"], "version": 1, "new_deck": {"name": "X"}},
        )
    )
    assert stale["error"] == "changed"
    assert h.db.get_proposal(p["proposal_id"], SUB)["deck_name"] == "Mono Blue"
    # a deck-edit list for a new deck says what to pass instead
    wrong = structured(
        await call(
            h,
            token,
            "amend_proposal",
            {"proposal_id": p["proposal_id"], "version": 2, "changes": [{"action": "add", "name": "Opt"}]},
        )
    )
    assert wrong["ok"] is False and "new_deck" in wrong["message"], wrong


async def test_a_card_package_is_amended_and_superseded_the_same_way(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    pkg = {"keywords": "fetchlands"}
    p = structured(await call(h, token, "propose_new_deck", {"name": "Fetches", "cards": [], "package": pkg}))
    assert p["changes"]["package"]["keywords"] == "fetchlands"
    again = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": "fetches ", "cards": _cards("Polluted Delta"), "package": {"keywords": "fetch lands"}},
        )
    )
    assert again["proposal_id"] == p["proposal_id"] and again["updated_in_place"] is True, again
    assert again["changes"]["package"]["keywords"] == "fetch lands"
    amended = structured(
        await call(
            h,
            token,
            "amend_proposal",
            {
                "proposal_id": p["proposal_id"],
                "version": 2,
                "new_deck": {"cards": _cards("Polluted Delta", "Scalding Tarn")},
            },
        )
    )
    assert amended["version"] == 3 and amended["changes"]["package"]["keywords"] == "fetch lands", amended
    # a deck of the same name is not the package
    deck = structured(await _new(h, token, name="Fetches"))
    assert deck["proposal_id"] != p["proposal_id"] and "updated_in_place" not in deck
    assert await _pending(h) == 2


# -- 2. a second propose of the same name updates the waiting one ---------------------------------------


async def test_proposing_the_same_new_deck_again_updates_it_in_place(stack: Stack) -> None:
    h = stack.h
    await linked_user(stack)
    await _mode(h, "manual")
    a, _ = await _app_token(h, "ChatGPT")
    b, _ = await _app_token(h, "Claude")
    first = structured(await _new(h, a))
    again = structured(
        await call(
            h,
            a,
            "propose_new_deck",
            {"name": "  atraxa SUPERFRIENDS ", "cards": _cards("Sol Ring", "Ponder")},
        )
    )
    assert again["proposal_id"] == first["proposal_id"] and again["version"] == 2, again
    assert again["updated_in_place"] is True and again["replaced_version"] == 1
    assert "instead of making a second proposal" in again["note"]
    assert await _pending(h) == 1
    # an exact repeat changes nothing and says so
    same = structured(
        await call(
            h, a, "propose_new_deck", {"name": "atraxa SUPERFRIENDS", "cards": _cards("Sol Ring", "Ponder")}
        )
    )
    assert same["proposal_id"] == first["proposal_id"] and same["version"] == 2
    assert same["duplicate_of"] == first["proposal_id"] and "updated_in_place" not in same
    # another app's new deck of the same name is its own proposal (that app's own)
    other = structured(await _new(h, b))
    assert other["proposal_id"] != first["proposal_id"] and "updated_in_place" not in other
    # a decided one is not reused
    h.db.reject_proposal(first["proposal_id"], SUB)
    fresh = structured(await _new(h, a))
    assert fresh["proposal_id"] != first["proposal_id"] and fresh["version"] == 1


async def test_replaces_names_the_waiting_new_deck_this_one_takes_the_place_of(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = structured(await _new(h, token, name="Draft one"))
    out = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": "Final list", "cards": _cards("Opt"), "replaces": first["proposal_id"]},
        )
    )
    assert out["ok"] and out["proposal_id"] == first["proposal_id"] and out["version"] == 2, out
    assert out["deck_name"] == "Final list" and out["updated_in_place"] is True
    assert "because replaces names it" in out["note"]
    assert await _pending(h) == 1
    # replaces is for a waiting new deck only: a deck edit, or one no longer waiting, is refused
    edit = structured(
        await call(
            h, token, "propose_deck_changes", {"deck_id": "42", "changes": [{"action": "add", "name": "Opt"}]}
        )
    )
    bad = structured(
        await call(
            h,
            token,
            "propose_new_deck",
            {"name": "X", "cards": _cards("Opt"), "replaces": edit["proposal_id"]},
        )
    )
    assert bad["ok"] is False and "amend_proposal" in bad["message"], bad
    gone = structured(
        await call(h, token, "propose_new_deck", {"name": "Y", "cards": _cards("Opt"), "replaces": "nope"})
    )
    assert gone["ok"] is False and "not a waiting proposal" in gone["message"]
    assert await _pending(h) == 2  # the new deck and the edit, nothing more


async def test_at_the_version_limit_a_new_proposal_replaces_the_waiting_one(
    stack: Stack, monkeypatch
) -> None:
    monkeypatch.setattr(db_module, "MAX_PROPOSAL_VERSIONS", 1)
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = structured(await _new(h, token))
    two = structured(
        await call(h, token, "propose_new_deck", {"name": "Atraxa Superfriends", "cards": _cards("Opt")})
    )
    assert two["proposal_id"] == first["proposal_id"] and two["version"] == 2
    three = structured(
        await call(h, token, "propose_new_deck", {"name": "Atraxa Superfriends", "cards": _cards("Ponder")})
    )
    assert three["proposal_id"] != first["proposal_id"] and three["replaced"] == [first["proposal_id"]], three
    assert h.db.get_proposal(first["proposal_id"], SUB)["state"] == "replaced"
    assert await _pending(h) == 1
    # amend at the limit says which tool and parameter to use, both of which exist
    full = structured(
        await call(
            h,
            token,
            "amend_proposal",
            {"proposal_id": three["proposal_id"], "version": 1, "new_deck": {"name": "Z"}},
        )
    )
    full = (
        full
        if full.get("error") == "versions_full"
        else structured(
            await call(
                h,
                token,
                "amend_proposal",
                {"proposal_id": three["proposal_id"], "version": 2, "new_deck": {"name": "Z2"}},
            )
        )
    )
    assert (
        full["error"] == "versions_full"
        and "propose_new_deck" in full["message"]
        and "replaces" in full["message"]
    )


async def test_no_refusal_points_to_a_parameter_that_does_not_exist(stack: Stack) -> None:
    """amend's refusals name only tools and parameters the assistant has: replaces only where the
    tool takes it (propose_deck_changes, propose_new_deck)."""
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    tools = await _tools(h, token)
    takes_replaces = {n for n, t in tools.items() if "replaces" in t["inputSchema"].get("properties", {})}
    assert takes_replaces == {"propose_deck_changes", "propose_new_deck"}
    cat = structured(
        await call(
            h,
            token,
            "propose_deck_categories",
            {"deck_id": "42", "changes": [{"action": "create", "category": "Removal"}]},
        )
    )
    assert cat["ok"], cat
    out = structured(
        await call(
            h, token, "amend_proposal", {"proposal_id": cat["proposal_id"], "version": 1, "changes": []}
        )
    )
    assert out["ok"] is False and "withdraw_proposal" in out["message"], out
    assert "replaces" not in out["message"]


# -- 3. each card knows its number ---------------------------------------------------------------------


async def test_each_card_drawn_gets_the_next_number_and_the_cards_own_read_does_not(stack: Stack) -> None:
    h = stack.h
    await linked_user(stack)
    await _mode(h, "manual")
    a, _ = await _app_token(h, "ChatGPT")
    b, _ = await _app_token(h, "Claude")
    first = structured(await _new(h, a))
    pid = first["proposal_id"]
    assert first["card_seq"] == 1
    amended = structured(
        await call(
            h, a, "amend_proposal", {"proposal_id": pid, "version": 1, "new_deck": {"cards": _cards("Opt")}}
        )
    )
    assert amended["card_seq"] == 2
    # the card's own read says the newest number and counts nothing
    for _ in range(2):
        assert structured(await call(h, a, "get_proposal", {"proposal_id": pid}))["card_seq"] == 2
    # the model's status check draws no card and has no number
    status = structured(await call(h, a, "proposal_status", {"proposal_id": pid}))
    assert status["ok"] and status["version"] == 2 and "card_seq" not in status
    # another app's chat counts its own cards: drawing one there leaves this app's newest as it is
    theirs = structured(
        await call(h, b, "propose_new_deck", {"name": "Taken over", "cards": _cards("Opt"), "replaces": pid})
    )
    assert theirs["proposal_id"] == pid and theirs["version"] == 3 and theirs["card_seq"] == 1, theirs
    assert structured(await call(h, a, "get_proposal", {"proposal_id": pid}))["card_seq"] == 2
    # a refusal draws no live card and takes no number
    stale = structured(
        await call(h, a, "amend_proposal", {"proposal_id": pid, "version": 1, "new_deck": {"name": "Q"}})
    )
    assert stale["ok"] is False and "card_seq" not in stale
    assert structured(await call(h, a, "get_proposal", {"proposal_id": pid}))["card_seq"] == 2


async def test_the_gateway_refuses_an_approve_from_an_older_card_of_the_same_version(stack: Stack) -> None:
    """The same version drawn twice (the assistant's own apply answers with the card again): the
    first card's code is refused with "Use the newest card", whatever that card shows, and nothing
    is sent; the newest card's code applies. A read from an older card hands it no code."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    res = await call(
        h, token, "propose_deck_changes", {"deck_id": "42", "changes": [{"action": "add", "name": "Opt"}]}
    )
    p, old_code = structured(res), res["_meta"][APPROVAL_META_KEY]
    pid = p["proposal_id"]
    assert p["card_seq"] == 1
    again = await call(h, token, "apply_proposal", {"proposal_id": pid})
    redrawn = structured(again)
    assert redrawn["error"] == "browser_required" and redrawn["card_seq"] == 2 and redrawn["version"] == 1
    new_code = again["_meta"][APPROVAL_META_KEY]
    assert new_code != old_code
    # the older card's read: no code; the newest card's read: its code; a read naming no card
    # (a card from before card numbers) is an older card too once this app has drawn a numbered
    # one: no code, and the newest card's number, so it steps aside
    older = await call(h, token, "get_proposal", {"proposal_id": pid, "card_seq": 1})
    assert structured(older)["card_seq"] == 2 and APPROVAL_META_KEY not in (older.get("_meta") or {})
    newest = await call(h, token, "get_proposal", {"proposal_id": pid, "card_seq": 2})
    assert newest["_meta"][APPROVAL_META_KEY] == new_code
    for unnumbered in ({"proposal_id": pid}, {"proposal_id": pid, "card_seq": 0}):
        plain = await call(h, token, "get_proposal", unnumbered)
        assert structured(plain)["card_seq"] == 2 and APPROVAL_META_KEY not in (plain.get("_meta") or {})
    before = len(ark.patches)
    stale = structured(
        await call(
            h, token, "confirm_proposal", {"proposal_id": pid, "approval": old_code, "decision": "approve"}
        )
    )
    assert stale["ok"] is False and stale["error"] == "not_newest_card", stale
    assert "Use the newest card" in stale["message"] and stale["state"] == "pending"
    assert len(ark.patches) == before
    done = structured(
        await call(
            h, token, "confirm_proposal", {"proposal_id": pid, "approval": new_code, "decision": "approve"}
        )
    )
    assert done["ok"] and done["state"] == "applied", done


async def test_a_card_from_before_card_numbers_still_gets_its_code_when_no_card_has_one(
    stack: Stack,
) -> None:
    """A proposal made before 0.7.46 has no card numbers: its card (which names none) is the only
    card, so its read still carries the code."""
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    res = await call(
        h, token, "propose_deck_changes", {"deck_id": "42", "changes": [{"action": "add", "name": "Opt"}]}
    )
    pid = structured(res)["proposal_id"]
    with h.db.tx() as c:
        c.execute("UPDATE proposals SET card_seqs = NULL WHERE id = ?", (pid,))
    plain = await call(h, token, "get_proposal", {"proposal_id": pid})
    assert "card_seq" not in structured(plain)
    code = plain["_meta"][APPROVAL_META_KEY]
    done = structured(
        await call(
            h, token, "confirm_proposal", {"proposal_id": pid, "approval": code, "decision": "approve"}
        )
    )
    assert done["ok"] and done["state"] == "applied", done


async def test_the_card_numbers_survive_in_the_database(stack: Stack) -> None:
    h = stack.h
    assert db_module.SCHEMA_VERSION == 18
    cols = {r[1] for r in h.db._conn.execute("PRAGMA table_info(proposals)")}
    assert "card_seqs" in cols
    token = await linked_user(stack)
    await _mode(h, "manual")
    pid = structured(await _new(h, token))["proposal_id"]
    assert h.db.get_proposal(pid, SUB)["card_seqs"]


def test_an_older_database_gets_the_card_numbers_column(tmp_path) -> None:
    import sqlite3

    path = tmp_path / "old.db"
    db = db_module.Database(str(path))
    db.close()
    raw = sqlite3.connect(path)
    raw.execute("ALTER TABLE proposals DROP COLUMN card_seqs")
    raw.execute("PRAGMA user_version = 15")
    raw.commit()
    raw.close()
    again = db_module.Database(str(path))
    try:
        assert again.schema_version == 18
        assert "card_seqs" in {r[1] for r in again._conn.execute("PRAGMA table_info(proposals)")}
    finally:
        again.close()


# -- 4. results stay small -------------------------------------------------------------------------------


async def test_a_big_proposals_result_carries_a_few_rows_and_says_how_to_get_the_rest(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    names = [f"Card Number {i}" for i in range(60)]
    res = await call(h, token, "propose_new_deck", {"name": "Big one", "cards": _cards(*names)})
    p = structured(res)
    assert len(p["rows"]) == CARD_ROWS and p["rows_total"] == 61, p.get("rows_total")
    assert p["rows_summary"] == {
        "adds": 60,
        "removes": 0,
        "others": 1,
        "rows": 61,
        "groups": {"new_deck": 1, "add": 60},
    }
    assert len(p["changes"]["cards"]) == CARD_ROWS and p["changes"]["cards_total"] == 60
    assert len(p["diff"].splitlines()) == 40 and p["diff_lines_total"] == 61
    assert "proposal_status with full true" in p["more"]
    text = res["content"][0]["text"]
    assert "… and 21 more (proposal_status with full true lists them all)" in text
    assert "Card Number 59" not in text and len(text) < 8000
    pid = p["proposal_id"]
    # the card's own read: everything (old cards rely on it), or as many rows as it asks for
    whole = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert len(whole["rows"]) == 61 and "rows_total" not in whole
    few = structured(await call(h, token, "get_proposal", {"proposal_id": pid, "rows_limit": 8}))
    assert len(few["rows"]) == 8 and few["rows_total"] == 61
    # the model's read: bounded unless it asks for everything
    status = structured(await call(h, token, "proposal_status", {"proposal_id": pid}))
    assert len(status["rows"]) == CARD_ROWS and status["rows_total"] == 61
    full = structured(await call(h, token, "proposal_status", {"proposal_id": pid, "full": True}))
    assert len(full["rows"]) == 61 and len(full["changes"]["cards"]) == 60


# -- 5 and 6. a status read and a dry run draw no card ----------------------------------------------------


async def test_the_status_and_check_tools_draw_no_card_and_the_cards_read_is_the_cards_own(
    stack: Stack,
) -> None:
    tools = await _tools(stack.h, await linked_user(stack))
    for name in ("proposal_status", "check_new_deck"):
        assert "ui" not in (tools[name].get("_meta") or {}), name
        assert tools[name]["annotations"]["readOnlyHint"] is True
    read = tools["get_proposal"]["_meta"]
    assert read["ui"] == {"visibility": ["app"]} and read["openai/visibility"] == "private"
    assert read["openai/widgetAccessible"] is True
    assert "resourceUri" not in read["ui"] and "ui/resourceUri" not in read
    assert tools["propose_new_deck"]["_meta"]["ui"]["resourceUri"] == CARD_URI
    assert "replaces" in tools["propose_new_deck"]["inputSchema"]["properties"]
    assert "new_deck" in tools["amend_proposal"]["inputSchema"]["properties"]


async def test_check_new_deck_finds_problems_and_the_waiting_one_without_saving(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")

    async def known(names: list[str]) -> dict[str, bool | None]:
        return {n: n != "Sol Rnig" for n in names}

    h.app.state.gateway.decks.known_cards = known
    ok = structured(
        await call(h, token, "check_new_deck", {"name": "Brew", "cards": _cards("Sol Ring", "Opt")})
    )
    assert ok["ok"] and ok["suggest"] == "propose" and ok["problems"] == [] and ok["card_count"] == 2, ok
    assert any("deck" in w.lower() for w in ok["warnings"])  # 2 cards is no Commander deck
    assert await _pending(h) == 0
    typo = structured(await call(h, token, "check_new_deck", {"name": "Brew", "cards": _cards("Sol Rnig")}))
    assert typo["suggest"] == "fix" and typo["problems"][0]["unknown_cards"] == ["Sol Rnig"]
    bad = structured(await call(h, token, "check_new_deck", {"name": "Brew", "decklist_text": ""}))
    assert bad["suggest"] == "fix" and bad["problems"][0]["error"] == "invalid"
    p = structured(await _new(h, token, name="Brew"))
    again = structured(await call(h, token, "check_new_deck", {"name": "brew", "cards": _cards("Opt")}))
    assert again["suggest"] == "amend" and again["same_name"]["proposal_id"] == p["proposal_id"]
    assert again["same_name"]["version"] == 1 and "amend_proposal" in again["next_step"]
    assert await _pending(h) == 1


async def test_the_guides_say_to_check_first_propose_once_and_read_with_proposal_status() -> None:
    from mtg_gateway.app import server_instructions

    told = server_instructions("https://gateway.example.com")
    assert "a new deck with check_new_deck; then propose once" in told
    assert "Never propose again to change a waiting proposal" in told
    assert "proposal_status, which draws no card" in told
    assert "get_proposal" not in told  # the card's own read


# -- 7. the reason typed under Reject ------------------------------------------------------------------


async def test_the_reason_typed_on_the_cards_reject_is_kept_and_shown_escaped(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    res = await _new(h, token, name="Tribal")
    pid = structured(res)["proposal_id"]
    why = "Keep <b>Islands</b> & cut a creature " + "x" * 600
    out = structured(
        await call(
            h,
            token,
            "confirm_proposal",
            {
                "proposal_id": pid,
                "approval": res["_meta"][APPROVAL_META_KEY],
                "decision": "reject",
                "reason": why,
            },
        )
    )
    assert out["ok"] and out["state"] == "rejected", out
    kept = structured(await call(h, token, "proposal_status", {"proposal_id": pid}))["reject_reason"]
    assert kept.startswith("Keep <b>Islands</b> & cut a creature") and len(kept) == 500
    b = Browser(h)
    await b.login()
    try:
        page = (await b.http.get(f"/proposals/{pid}")).text
        assert "Your reason: “Keep &lt;b&gt;Islands&lt;/b&gt; &amp; cut a creature" in page
        assert "<b>Islands</b>" not in page
        listing = (await b.http.get("/proposals?show=all")).text
        line = re.search(
            r"Rejected by you: “Keep &lt;b&gt;Islands&lt;/b&gt; &amp; cut a creature x+…”", listing
        )
        assert line, listing[:2000]
        assert len(html.unescape(line.group(0))) < 120
    finally:
        await b.aclose()


async def test_a_reject_without_a_reason_reads_as_before(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    res = await _new(h, token, name="Plain")
    pid = structured(res)["proposal_id"]
    await call(
        h,
        token,
        "confirm_proposal",
        {"proposal_id": pid, "approval": res["_meta"][APPROVAL_META_KEY], "decision": "reject"},
    )
    status = structured(await call(h, token, "proposal_status", {"proposal_id": pid}))
    assert status["state"] == "rejected" and "reject_reason" not in status


# -- 8. ChatGPT's apps work once the connector is added on the web -------------------------------


async def test_the_chatgpt_steps_say_its_apps_work_once_the_connector_is_added_on_the_web(
    stack: Stack,
) -> None:
    """The owner used the gateway in the ChatGPT Android app (2026-10-24 and 2026-10-27) after adding
    the connector on chatgpt.com; the steps no longer say the phone apps can't use it."""
    md = (await stack.h.http.get("/install.md?for=chatgpt")).text
    flat = " ".join(md.split())
    assert "Add it on chatgpt.com in the computer's web browser" in flat
    assert "it was tested in the ChatGPT Android app" in flat and "The iPhone app has not been tried" in flat
    assert "Does not work on:** the Free plan" in flat
    for gone in ("iPhone and Android apps, or the Free plan", "on the web only, not in its phone apps"):
        assert gone not in flat
    b = Browser(stack.h)
    try:
        await b.login("/install")
        picker = " ".join((await b.http.get("/install")).text.split())
    finally:
        await b.aclose()
    assert "ChatGPT's phone apps do not support custom connectors" not in picker
    assert "ChatGPT's apps work once the connector is added on chatgpt.com" in picker


def test_the_docs_no_longer_say_chatgpts_phone_apps_cant_use_the_gateway() -> None:
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    connect = " ".join((root / "docs" / "CONNECT.md").read_text(encoding="utf-8").split())
    assert "the ChatGPT phone apps can't use this gateway" not in connect
    assert "iOS and Android apps: OpenAI doesn't support them" not in connect
    assert "ChatGPT Android app | Not tried; add it on chatgpt.com" in connect
    assert "iPhone app: not tested" in connect
    arch = (root / "docs" / "ARCHITECTURE.md").read_text(encoding="utf-8")
    assert "Web only (OpenAI says its phone apps" not in arch
    for doc in ("CONNECT.md", "ARCHITECTURE.md", "ONBOARDING.md", "PLUGIN.md"):
        text = " ".join((root / "docs" / doc).read_text(encoding="utf-8").split())
        assert "iPhone app" not in text or "tested in the ChatGPT iPhone" not in text, doc


def test_the_card_sent_to_the_chat_leaves_out_the_sources_comments_and_indentation() -> None:
    """Every card is a whole document a chat loads once per card drawn; the source keeps its
    comments, the copy sent leaves out whole comment lines, indentation and blank lines."""
    from mtg_gateway.approve import CARD_PATH, card_html
    from mtg_gateway.cards import card_html as any_card

    html = card_html()
    assert len(html.encode()) < 68_000, len(html.encode())  # 85,318 bytes before
    lines = html.split("\n")
    assert not [x for x in lines if x.strip().startswith(("//", "/*"))]
    assert not [x for x in lines if x != x.strip() or not x.strip()][:-1]
    # code and markup are untouched, and the source still explains itself
    assert "var RETRY_FIRST_MS = 2000, RETRY_LAST_MS = 60000, RETRY_MAX = 6;" in html
    assert '<textarea id="reason" maxlength="500"' in html and "https://" in html
    assert "// " in CARD_PATH.read_text(encoding="utf-8")
    for name in ("waiting-card", "deck-card", "stats-card"):
        assert not [x for x in any_card(name).split("\n") if x.strip().startswith("//")], name


async def test_a_change_the_assistant_may_apply_still_has_apply_now_on_its_card(stack: Stack) -> None:
    """Semi and Full auto: a change the assistant may apply itself had no code, so its card had
    nothing to press, and a change the assistant never applied waited with no way to apply it
    from the chat. The card now has the code (Apply now, Reject); the assistant can still apply."""
    from .test_approval_modes import _adds

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    for mode in ("semi", "auto"):
        await _mode(h, mode)
        res = await _adds(h, token, 1)
        p = structured(res)
        assert p["assistant_may_apply"] is True and p["approval_mode"] == mode, p
        code = res["_meta"][APPROVAL_META_KEY]
        before = len(ark.patches)
        done = structured(
            await call(h, token, "confirm_proposal", {"proposal_id": p["proposal_id"], "approval": code})
        )
        assert done["ok"] and done["state"] == "applied" and len(ark.patches) > before, done
        # the assistant's own apply is unchanged
        own = structured(await _adds(h, token, 1))
        out = structured(await call(h, token, "apply_proposal", {"proposal_id": own["proposal_id"]}))
        assert out["ok"] and out["state"] == "applied", out
