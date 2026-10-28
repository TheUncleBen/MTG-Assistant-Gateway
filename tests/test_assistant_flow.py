"""0.7.46, how an assistant's changes flow (owner decision C3) and what its tool results say
(audit work package WP7): one waiting draft per deck per app, propose and apply in one call where
the member's mode allows it, a failed apply that changed nothing waits again with Try again,
"Propose the rest" after a partly sent apply, one account-action tool, and results the assistant
reads without guessing."""

from __future__ import annotations

from .conftest import Stack
from .test_decks_and_proxy import call, call_as_sent, linked_user, structured
from .test_track_a_board import _mode
from .track_a_helpers import press_approve


def _add(name: str, **more: object) -> dict:
    return {"action": "add", "name": name, **more}


async def _propose(h, token: str, changes: list, **more: object) -> dict:
    return structured(
        await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes, **more})
    )


# -- combine: the board note is worked out after the combined ones are replaced (A12) ----------


async def test_combine_lists_only_what_still_waits_beside_it(stack: Stack) -> None:
    """The combine result listed the proposals it had just replaced under waiting_on_deck, with
    overlaps against them, and told the assistant to tidy them up."""
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    one = await _propose(h, token, [_add("Arcane Signet"), {"action": "remove", "name": "Acidic Slime"}])
    two = await _propose(h, token, [_add("Sol Ring")], separate=True)
    three = await _propose(h, token, [_add("Opt")], separate=True)
    out = structured(
        await call(h, token, "combine_proposals", {"proposal_ids": [one["proposal_id"], two["proposal_id"]]})
    )
    assert out["ok"] and sorted(out["replaced"]) == sorted([one["proposal_id"], two["proposal_id"]]), out
    assert [w["proposal_id"] for w in out["waiting_on_deck"]] == [three["proposal_id"]], out
    assert "overlaps" not in out, out
    assert "overlap" not in out["next_step"].lower(), out["next_step"]


# -- one waiting draft per deck per app (C3, question 1) ---------------------------------------


async def test_a_later_request_from_the_same_app_is_added_to_its_waiting_draft(stack: Stack) -> None:
    """A second propose for the same deck from the same app made a second proposal and a second
    card. It now adds to the app's waiting one: one proposal, a new version, both changes."""
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = await _propose(h, token, [_add("Arcane Signet", category="Ramp")])
    second = await _propose(h, token, [_add("Mind Stone")])
    assert second["ok"] and second["proposal_id"] == first["proposal_id"], second
    assert second["added_to"] == first["proposal_id"] and second["version"] == 2
    assert second["note"].startswith("Added to #1, this app's waiting change for this deck"), second
    assert [r["name"] for r in second["rows"]] == ["Arcane Signet", "Mind Stone"]
    assert "now also adds Mind Stone" in second["what_changed"]
    assert h.db.count_pending_proposals("user-1") == 1
    # its card is the one live card: the result that drew the newer card names the higher number
    assert second["card_seq"] == 2
    # a third request for a card the draft already changes takes that card's change's place
    third = await _propose(h, token, [_add("Arcane Signet", quantity=2, category="Ramp")])
    assert third["version"] == 3 and [r["qty"] for r in third["rows"] if r["name"] == "Arcane Signet"] == [2]
    assert [c["name"] for c in third["changes"]] == ["Arcane Signet", "Mind Stone"], third["changes"]


async def test_the_same_request_again_adds_nothing(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = await _propose(h, token, [_add("Opt"), _add("Mind Stone")])
    again = await _propose(h, token, [_add("Mind Stone")])
    assert again["duplicate_of"] == first["proposal_id"] and again["version"] == 1, again
    assert again["note"] == "Already in #1: it holds these changes, so nothing was added."


async def test_a_draft_is_per_app_and_separate_opts_out(stack: Stack) -> None:
    from .test_round4_data import _app_token

    h = stack.h
    await linked_user(stack)
    await _mode(h, "manual")
    claude, _ = await _app_token(h, "Claude")
    chatgpt, _ = await _app_token(h, "ChatGPT")
    a = await _propose(h, claude, [_add("Opt")])
    b = await _propose(h, chatgpt, [_add("Mind Stone")])
    assert b["proposal_id"] != a["proposal_id"] and "added_to" not in b
    c = await _propose(h, claude, [_add("Island")], separate=True)
    assert c["proposal_id"] != a["proposal_id"] and c["version"] == 1 and "added_to" not in c
    # with two drafts of its own, a later request goes to the newest
    d = await _propose(h, claude, [_add("Swamp")])
    assert d["added_to"] == c["proposal_id"], d


async def test_another_kind_of_change_to_the_same_card_is_proposed_on_its_own(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = await _propose(h, token, [{"action": "set_quantity", "name": "Forest", "quantity": 14}])
    moved = await _propose(h, token, [{"action": "set_category", "name": "Forest", "category": "Lands"}])
    assert moved["proposal_id"] != first["proposal_id"] and "added_to" not in moved, moved
    assert "#1 already makes another kind of change to “Forest”" in moved["note"], moved["note"]


async def test_a_draft_the_deck_changed_under_is_left_alone(stack: Stack) -> None:
    """A draft that would now do something else on the deck (changed on Archidekt) is Needs a
    look; a later request is not mixed into it."""
    from .test_track_a_recheck import _edit_elsewhere

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = await _propose(h, token, [{"action": "set_quantity", "name": "Forest", "quantity": 14}])
    _edit_elsewhere(ark, "Forest", 2)
    later = await _propose(h, token, [_add("Opt")])
    assert later["proposal_id"] != first["proposal_id"] and "added_to" not in later
    old = structured(await call(h, token, "proposal_status", {"proposal_id": first["proposal_id"]}))
    assert old["status"] == "needs_look" and old["version"] == 1


async def test_a_full_draft_takes_no_more(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    first = await _propose(h, token, [_add(f"Card {i}") for i in range(39)])
    more = await _propose(h, token, [_add("Opt"), _add("Island")])
    assert more["proposal_id"] != first["proposal_id"], more
    assert "would pass 40 changes" in more["note"]
    # one that fits still goes in
    fits = await _propose(h, token, [_add("Swamp")])
    assert fits["added_to"] in (first["proposal_id"], more["proposal_id"])


# -- result keys and short answers (A23) -------------------------------------------------------


async def test_results_name_cards_as_the_inputs_do_and_reject_and_withdraw_answer_short(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = await _propose(h, token, [_add("Opt", finish="foil"), {"action": "remove", "name": "Acidic Slime"}])
    full = structured(
        await call(h, token, "proposal_status", {"proposal_id": p["proposal_id"], "full": True})
    )
    assert full["changes"] == [
        {"action": "add", "name": "Opt", "quantity": 1, "finish": "foil"},
        {"action": "remove", "name": "Acidic Slime"},
    ], full["changes"]
    assert all("card_name" not in c for c in p["changes"])
    out = structured(await call(h, token, "reject_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] and out["state"] == "rejected" and out["proposal_id"] == p["proposal_id"], out
    assert not {"card_seq", "rows", "changes", "diff", "result", "risk_reason"} & set(out), sorted(out)
    assert out["closed_by"] == "assistant" and out["closed_at"]
    q = await _propose(h, token, [_add("Island")])
    out = structured(
        await call(h, token, "withdraw_proposal", {"proposal_id": q["proposal_id"], "reason": "not needed"})
    )
    assert out["ok"] and out["state"] == "withdrawn", out
    assert not {"card_seq", "rows", "changes", "diff", "result"} & set(out), sorted(out)


# -- the 40-change limit is in the schema (A24) -------------------------------------------------


async def test_the_change_limit_is_in_every_change_list_schema(stack: Stack) -> None:
    from mtg_gateway.decks import MAX_CHANGES

    from .test_in_chat_approve import _tools

    tools = await _tools(stack.h, await linked_user(stack))
    for name in ("propose_deck_changes", "check_deck_changes", "amend_proposal"):
        changes = tools[name]["inputSchema"]["properties"]["changes"]
        lists = (
            [changes]
            if changes.get("type") == "array"
            else [b for b in changes.get("anyOf", []) if b.get("type") == "array"]
        )
        assert lists and all(b.get("maxItems") == MAX_CHANGES for b in lists), (name, changes)
        assert f"at most {MAX_CHANGES}" in changes.get("description", ""), (name, changes)


# -- apply_proposal draws no card; the expected Manual refusal is no error (C3 q2, A22) ----------


async def test_apply_proposal_draws_no_card_and_its_manual_refusal_is_a_plain_answer(stack: Stack) -> None:
    from .test_in_chat_approve import _tools

    h = stack.h
    token = await linked_user(stack)
    tool = (await _tools(h, token))["apply_proposal"]
    meta = tool.get("_meta") or {}
    assert "resourceUri" not in (meta.get("ui") or {}) and "ui/resourceUri" not in meta, meta
    assert meta.get("anthropic/requiresUserInteraction") is True  # the host's own prompt stays
    await _mode(h, "manual")
    p = await _propose(h, token, [_add("Opt")])
    res = await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    assert not res.get("isError"), res
    out = structured(res)
    assert out["ok"] is False and out["error"] == "browser_required" and out["review_url"], out
    assert not {"rows", "diff", "changes", "card_seq", "deck_waiting"} & set(out), sorted(out)
    assert "card" in out["message"] and "review page" in out["message"], out["message"]
    assert h.db.count_pending_proposals("user-1") == 1


# -- a propose never applies; apply_proposal is the one write (owner decision 0.7.46) ----------


async def test_semi_auto_leaves_a_low_risk_change_waiting_and_says_to_call_apply(stack: Stack) -> None:
    """Proposals stay read only (owner decision, 0.7.46): in Semi auto a low-risk change waits,
    nothing reaches Archidekt, and next_step tells the assistant to call apply_proposal, which
    applies it."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "semi")
    res = await call_as_sent(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [_add("Opt")]})
    out = structured(res)
    assert out["ok"] and out["state"] == "pending" and out["assistant_may_apply"] is True, out
    assert ark.patches == [] and "applied_now" not in out
    assert "call apply_proposal now" in out["next_step"], out["next_step"]
    # an apply_if_allowed from an app that learnt the old schema changes nothing either
    old = structured(
        await call_as_sent(
            h,
            token,
            "propose_deck_changes",
            {"deck_id": "42", "changes": [_add("Island")], "separate": True, "apply_if_allowed": True},
        )
    )
    assert old["state"] == "pending" and ark.patches == [], old
    done = structured(await call(h, token, "apply_proposal", {"proposal_id": out["proposal_id"]}))
    assert done["state"] == "applied" and len(ark.patches) == 1, done
    assert (
        structured(await call(h, token, "proposal_status", {"proposal_id": out["proposal_id"]}))[
            "applied_via"
        ]
        == "assistant"
    )


async def test_manual_never_applies_in_the_propose_call(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    out = structured(
        await call_as_sent(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [_add("Opt")]})
    )
    assert out["state"] == "pending" and "applied_now" not in out and ark.patches == []


async def test_full_auto_still_waits_for_a_new_deck(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "auto")
    made = len(ark.decks)
    out = structured(
        await call_as_sent(h, token, "propose_new_deck", {"name": "Fresh", "cards": [{"name": "Sol Ring"}]})
    )
    assert out["state"] == "pending" and out["assistant_may_apply"] is False and len(ark.decks) == made


# -- names nothing could check, and cards Archidekt doesn't have (A5, A6) ------------------------


def _card_search_fails(ark) -> None:
    """Archidekt's card search answers 503 (a slow or failing Archidekt); deck reads still work."""
    import httpx

    orig = ark.transport.handler

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/api/cards/v2/":
            return httpx.Response(503, text="unavailable")
        return orig(req)

    ark.transport.handler = handler


async def test_a_name_nothing_could_check_is_never_low_risk(stack: Stack) -> None:
    """No card-name catalog yet and Archidekt's card search down: "+1 Islend" was proposed as Low
    risk and, in Semi auto, applied. It now waits, says the name wasn't checked, and is High."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "semi")
    _card_search_fails(ark)
    out = structured(
        await call_as_sent(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [_add("Islend")]})
    )
    assert out["ok"] and out["state"] == "pending" and ark.patches == [], out
    assert out["risk"] == "high" and out["assistant_may_apply"] is False, out
    assert out["unchecked_names"] == ["Islend"]
    assert any("Couldn't check that “Islend” is a card" in w for w in out["warnings"]), out
    assert "+1 Islend (not checked)" in out["diff"]


async def test_a_card_archidekt_doesnt_have_is_marked_when_proposed(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "semi")
    out = structured(
        await call_as_sent(
            h, token, "propose_deck_changes", {"deck_id": "42", "changes": [_add("Islend"), _add("Opt")]}
        )
    )
    assert out["state"] == "pending" and out["risk"] == "high" and out["assistant_may_apply"] is False, out
    assert out["missing_on_archidekt"] == ["Islend"] and "unchecked_names" not in out
    assert "+1 Islend (Archidekt doesn't have this card)" in out["diff"] and "+1 Opt\n" in out["diff"] + "\n"
    # a card Archidekt finds counts as checked: Semi auto lets the assistant apply it
    ok = structured(
        await call_as_sent(
            h, token, "propose_deck_changes", {"deck_id": "42", "changes": [_add("Swamp")], "separate": True}
        )
    )
    assert ok["state"] == "pending" and ok["risk"] == "low" and ok["assistant_may_apply"] is True, ok


async def test_an_apply_leaves_out_only_the_card_archidekt_doesnt_have(stack: Stack) -> None:
    """One card Archidekt lacked failed the whole change after the member approved it; now the
    rest is applied and the result names the card left out."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = await _propose(
        h, token, [_add("Opt"), _add("Mind Stone"), {"action": "remove", "name": "Acidic Slime"}]
    )
    assert p["missing_on_archidekt"] == ["Mind Stone"], p
    looked = len([c for c in ark.calls if "/cards/" in c[1]])
    done = await press_approve(h, token, p["proposal_id"])
    assert done["state"] == "applied", done
    assert done["left_out"] == [{"name": "Mind Stone", "why": "Archidekt doesn't have this card"}]
    assert "“Mind Stone” was left out: Archidekt doesn't have it." in done["applied_text"]
    names = {c["card"]["oracleCard"]["name"] for c in ark.decks[42]["cards"]}
    assert "Opt" in names and "Acidic Slime" not in names and "Mind Stone" not in names
    # the answers looked up when it was proposed are used again
    assert len([c for c in ark.calls if "/cards/" in c[1]]) == looked


async def test_an_apply_with_only_cards_archidekt_doesnt_have_sends_nothing(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = await _propose(h, token, [_add("Mind Stone")])
    done = await press_approve(h, token, p["proposal_id"])
    assert done["ok"] is False and "Archidekt doesn't have “Mind Stone”" in done["message"], done
    assert ark.patches == []


async def test_a_misspelt_name_added_to_a_draft_is_refused_once_and_the_draft_stays(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    h.app.state.gateway.scan.names.set_names(["Opt", "Island"])
    draft = await _propose(h, token, [_add("Opt")])
    asked: list[str] = []

    async def known(names: list[str]) -> dict:
        asked.extend(names)
        return {n: n != "Islend" for n in names}

    h.app.state.gateway.decks.known_cards = known
    out = await _propose(h, token, [_add("Islend")])
    assert out["ok"] is False and out["error"] == "unknown_cards", out
    assert asked.count("Islend") == 1, asked
    assert h.db.get_proposal(draft["proposal_id"], "user-1")["version"] == 1


# -- a failed apply: Try again when nothing changed, Propose the rest after part of it (C3.3) ----


def _second_card_write_fails(ark) -> None:
    """Archidekt takes the first card write of an apply and answers 503 to the rest."""
    import httpx

    orig = ark.transport.handler
    seen = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and req.url.path.endswith("/modifyCards/v2/"):
            seen["n"] += 1
            if seen["n"] > 1:
                return httpx.Response(503, json={"detail": "Service unavailable."})
        return orig(req)

    ark.transport.handler = handler


async def test_a_change_that_stopped_partway_offers_propose_the_rest(stack: Stack) -> None:
    from .test_decks_and_proxy import Browser

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = await _propose(h, token, [_add("Opt"), {"action": "remove", "name": "Acidic Slime"}])
    _second_card_write_fails(ark)
    got = await press_approve(h, token, p["proposal_id"])
    assert got["ok"] is False and got["state"] == "failed", got
    now = structured(await call(h, token, "proposal_status", {"proposal_id": p["proposal_id"]}))
    assert now["rest_offered"] is True and "Propose the rest" in now["next_step"], now
    names = {c["card"]["oracleCard"]["name"] for c in ark.decks[42]["cards"]}
    landed_opt = "Opt" in names
    # the review page offers it too
    b = Browser(h)
    await b.login()
    page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
    assert ">Propose the rest</button>" in page and "Part of this change reached Archidekt" in page
    await b.aclose()
    # the card's button: the gateway proposes only what is not on the deck
    rest = structured(await call(h, token, "propose_rest", {"proposal_id": p["proposal_id"]}))
    assert rest["ok"] and rest["state"] == "pending" and rest["proposal_id"] != p["proposal_id"], rest
    assert [c["name"] for c in rest["changes"]] == (["Acidic Slime"] if landed_opt else ["Opt"]), rest
    again = structured(await call(h, token, "propose_rest", {"proposal_id": p["proposal_id"]}))
    assert again["proposal_id"] == rest["proposal_id"]  # asked twice, proposed once
    from .test_in_chat_approve import _tools

    tools = await _tools(h, token)
    assert tools["propose_rest"]["_meta"]["ui"]["visibility"] == ["app"]  # the card's, not the model's


async def test_the_proposals_list_offers_try_again_after_archidekt_didnt_answer(stack: Stack) -> None:
    from .test_decks_and_proxy import Browser

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = await _propose(h, token, [_add("Opt")])
    ark.fail_card_writes = True
    got = await press_approve(h, token, p["proposal_id"])
    assert got["state"] == "pending" and got["try_again"], got
    b = Browser(h)
    await b.login()
    page = (await b.http.get("/proposals")).text
    assert "Archidekt didn&#x27;t answer, so nothing was changed." in page
    assert ">Try\u00a0again<" in page
    await b.aclose()
    assert ark.patches == []


async def test_a_write_that_failed_but_never_landed_waits_again(stack: Stack) -> None:
    """A card write answered 503 may have landed; the deck read again shows it did not, so the
    change waits again instead of failing."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = await _propose(h, token, [_add("Opt")])
    ark.fail_card_writes = True
    got = await press_approve(h, token, p["proposal_id"])
    assert got["state"] == "pending" and got["try_again"] is True, got
    row = h.db.get_proposal(p["proposal_id"], "user-1")
    assert row["result"]["read_back_unchanged"] is True and row["result"]["try_again"] is True


async def test_every_proposal_tool_carries_the_same_read_only_hint(stack: Stack) -> None:
    """propose_* said read only and amend, withdraw and combine did not, so ChatGPT asked the user
    to confirm the amend the instructions want, but never a fresh propose (A9). Owner decision
    0.7.46: no propose applies anything, so all of them are read only (they change only the
    gateway's own proposal records); apply_proposal is the write."""
    from .test_in_chat_approve import _tools

    tools = await _tools(stack.h, await linked_user(stack))
    names = [
        n
        for n in tools
        if n.startswith("propose_") or n in ("amend_proposal", "withdraw_proposal", "combine_proposals")
    ]
    assert len(names) > 15
    assert {n: tools[n]["annotations"]["readOnlyHint"] for n in names} == dict.fromkeys(names, True)
    assert tools["apply_proposal"]["annotations"]["readOnlyHint"] is False


# -- results the assistant reads: compact, and no longer than it needs (A7) ----------------------


async def test_account_status_is_compact_and_under_six_tenths_of_its_old_size(stack: Stack) -> None:
    """account_status in Semi-auto was 1,688 characters of indented JSON, most of it the full
    list of what the mode applies. The assistant learns that from each proposal's
    assistant_may_apply; the full list stays on the account page."""
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "semi")
    out = await call(h, token, "account_status")
    text = out["content"][0]["text"]
    assert "\n" not in text, text
    assert len(text) < 1688 * 0.6, (len(text), text)
    sc = structured(out)
    assert sc["approval_mode"] == "semi" and "assistant_may_apply" in sc["approval_mode_note"], sc


async def test_a_plain_result_is_sent_as_compact_json(stack: Stack) -> None:
    """Every JSON text result was indented, which only adds spaces the model pays for."""
    h = stack.h
    token = await linked_user(stack)
    for name, args in (("list_my_decks", {}), ("whoami", {}), ("get_deck", {"deck_id": "9999"})):
        out = await call(h, token, name, args)
        text = out["content"][0]["text"]
        assert text.startswith("{") and "\n" not in text, (name, text[:200])


# -- next_step says what to do in this app, without making the assistant guess (A11) -----------

CLAUDE_CALLBACK = "https://claude.ai/api/mcp/auth_callback"


async def _token_for_app(h, redirect_uri: str) -> str:
    client = await h.register(redirect_uri)
    code, verifier = await h.full_login(client, redirect_uri=redirect_uri)
    r = await h.token(
        client, grant_type="authorization_code", code=code, code_verifier=verifier, redirect_uri=redirect_uri
    )
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


async def test_next_step_is_one_plain_sentence_for_an_app_known_to_show_cards(stack: Stack) -> None:
    """next_step said "If your app shows this proposal as a card … Otherwise, show …", so the
    assistant had to guess which app it is. The gateway knows: Claude's and ChatGPT's apps show the
    card, so it says so once; an app it can't place gets the review link."""
    h = stack.h
    await linked_user(stack)
    await _mode(h, "manual")
    claude = await _token_for_app(h, CLAUDE_CALLBACK)
    p = await _propose(h, claude, [_add("Arcane Signet")])
    assert p["ok"] and p["state"] == "pending", p
    step = p["next_step"]
    assert step.startswith("The user decides on the card shown with this result"), step
    assert "If your app" not in step and "Otherwise" not in step, step
    assert step.count(". ") == 0 and step.endswith("."), step  # one sentence
    other = await _token_for_app(h, "https://client.test/cb")
    q = await _propose(h, other, [_add("Sol Ring")])
    assert "If your app" not in q["next_step"] and "review link" in q["next_step"], q["next_step"]


# -- failure and status text in plain words (A18) ------------------------------------------------


async def test_a_printing_archidekt_lacks_is_named_in_plain_words(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "manual")
    p = await _propose(h, token, [_add("Sol Ring", set_code="zzz", collector_number="999")])
    assert p["ok"], p
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    if out.get("error") == "browser_required":  # manual: the member's own press applies it
        await press_approve(h, token, p["proposal_id"])
        out = structured(await call(h, token, "proposal_status", {"proposal_id": p["proposal_id"]}))
    text = str(out)
    assert "no Archidekt printing found" not in text, text
    assert "Archidekt doesn't have “Sol Ring” in that printing (ZZZ #999)" in text, text


async def test_the_review_page_shows_no_reason_line_for_a_closed_changes_risk(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _mode(h, "auto")
    p = structured(
        await call_as_sent(h, token, "propose_deck_changes", {"deck_id": "42", "changes": [_add("Sol Ring")]})
    )
    assert structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))["state"] == (
        "applied"
    )
    from .test_decks_and_proxy import Browser

    b = Browser(h)
    await b.login()
    page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
    await b.aclose()
    assert "judged when it closed" not in page


# -- the review panel says Needs a look once (A21) -----------------------------------------------


async def test_the_review_panel_says_needs_a_look_once(stack: Stack) -> None:
    """The side panel showed the Needs-a-look message in a grey box and again in an orange one."""
    from .test_decks_and_proxy import Browser
    from .test_track_a_recheck import _edit_elsewhere
    from .test_track_a_recheck import _propose as _forest_propose

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = await _forest_propose(h, token)
    _edit_elsewhere(ark, "Forest", 2)
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["error"] == "stale", out
    b = Browser(h)
    await b.login()
    page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
    await b.aclose()
    assert page.count("the deck changed after this change was made") == 1, page.count(
        "the deck changed after this change was made"
    )
    assert "Reviewed: 15 -&gt; 14 Forest" in page or "Reviewed: 15 -> 14 Forest" in page


async def test_no_propose_tool_applies_and_each_says_it_changes_nothing(stack: Stack) -> None:
    """Owner decision 0.7.46: no proposal tool takes apply_if_allowed any more or says a call may
    apply it, and propose_deck_changes, the one an assistant uses most, says it changes nothing on
    Archidekt (apply_proposal is the one write)."""
    import json

    from mtg_gateway.app import PROPOSAL_TOOLS

    from .conftest import sse_json

    h = stack.h
    token = await linked_user(stack)
    r = await h.mcp(token, "tools/list", {}, rid=3)
    tools = {t["name"]: t for t in sse_json(r)["result"]["tools"]}
    assert not [n for n, t in tools.items() if "apply_if_allowed" in json.dumps(t)]
    for name in sorted(PROPOSAL_TOOLS & set(tools)):
        assert "this call applies" not in tools[name]["description"], name
    assert "Changes nothing on Archidekt" in tools["propose_deck_changes"]["description"]
