"""Release 0.7.29, approvals: a second step never lands without its first, and a
combined change keeps what its parts needed. An assistant does not apply a change while one it
depends on still waits; a held change, or another app's, is never combined; a change that
depends on one closed outside the apply paths (a restart mid-apply, expiry) is held."""

from __future__ import annotations

from mtg_gateway.decks import BROWSER_CLIENT, current_client

from .conftest import Stack
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import call, linked_user, structured
from .test_round4_data import _app_token

SUB = "user-1"


def _add(name: str) -> list[dict]:
    return [{"action": "add", "card_name": name}]


async def _propose(h, token: str, changes: list, **more: object) -> dict:
    p = structured(
        await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes, **more})
    )
    assert p["ok"], p
    return p


async def _get(h, token: str, pid: str) -> dict:
    return structured(await call(h, token, "get_proposal", {"proposal_id": pid}))


async def test_full_auto_waits_while_the_first_step_still_waits(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    first = await _propose(h, token, _add("Opt"))
    second = await _propose(h, token, _add("Arcane Signet"), depends_on=[first["proposal_id"]])
    await _set_mode(h, "auto")
    got = await _get(h, token, second["proposal_id"])
    assert got["assistant_may_apply"] is False
    assert got["press_reason"] == f"waits for #{first['board_no']}, which is not applied"
    assert (
        got["risk"] == "low" and "waits for the user's own press in every approval mode" in got["next_step"]
    )
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": second["proposal_id"]}))
    assert out.get("state") != "applied", out
    assert ark.patches == [] and h.db.get_proposal(second["proposal_id"], SUB)["state"] == "pending"
    # once the first step is applied, the second may go
    assert structured(await call(h, token, "apply_proposal", {"proposal_id": first["proposal_id"]}))["ok"]
    await h.app.state.gateway.decks.wait_applied(first["proposal_id"])
    assert (await _get(h, token, second["proposal_id"]))["assistant_may_apply"] is True


async def test_a_held_change_is_not_combined(stack: Stack) -> None:
    """A held change is never combined; nothing changes."""
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    base = await _propose(h, token, _add("Opt"))
    top = await _propose(h, token, _add("Arcane Signet"), depends_on=[base["proposal_id"]])
    other = await _propose(h, token, _add("Swamp"))
    assert structured(await call(h, token, "reject_proposal", {"proposal_id": base["proposal_id"]}))["ok"]
    comb = structured(
        await call(
            h, token, "combine_proposals", {"proposal_ids": [top["proposal_id"], other["proposal_id"]]}
        )
    )
    assert comb["ok"] is False and f"#{top['board_no']} is held, so it can't be combined" in comb["message"]
    assert [h.db.get_proposal(x["proposal_id"], SUB)["state"] for x in (top, other)] == ["pending"] * 2


async def test_another_apps_change_is_not_combined(stack: Stack) -> None:
    """An assistant combines only its own app's waiting changes that no other app
    changed; another app's, or one another app changed, is refused and nothing changes."""
    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    theirs = await _propose(h, b, _add("Opt"))
    mine = await _propose(h, a, _add("Arcane Signet"))
    comb = structured(
        await call(h, a, "combine_proposals", {"proposal_ids": [theirs["proposal_id"], mine["proposal_id"]]})
    )
    assert comb["ok"] is False, comb
    assert (
        f"#{theirs['board_no']} was made or changed by another app, so it can't be combined"
        in comb["message"]
    )
    assert [h.db.get_proposal(x["proposal_id"], SUB)["state"] for x in (theirs, mine)] == ["pending"] * 2
    # one of Claude's own that ChatGPT changed is refused too
    row = h.db.get_proposal(mine["proposal_id"], SUB)
    assert h.db.amend_proposal(
        mine["proposal_id"],
        SUB,
        expect_version=int(row["version"]),
        changes=row["changes"],
        rows=row["rows"],
        diff_text=row["diff_text"],
        by_client=row["created_by_client"] + "-other",
        what_changed="by ChatGPT",
    )
    other = await _propose(h, a, _add("Swamp"))
    comb = structured(
        await call(h, a, "combine_proposals", {"proposal_ids": [mine["proposal_id"], other["proposal_id"]]})
    )
    assert comb["ok"] is False and "changed by another app" in comb["message"], comb
    # the same app combining only its own changes works as before
    one = await _propose(h, a, _add("Island"))
    own = structured(
        await call(h, a, "combine_proposals", {"proposal_ids": [one["proposal_id"], other["proposal_id"]]})
    )
    assert own["ok"], own
    assert "press_reason" not in (await _get(h, a, own["proposal_id"]))


async def test_a_restart_mid_apply_holds_the_dependents(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    first = await _propose(h, token, _add("Opt"))
    second = await _propose(h, token, _add("Arcane Signet"), depends_on=[first["proposal_id"]])
    assert h.db.claim_proposal(first["proposal_id"], SUB)  # an apply under way...
    assert h.db.fail_interrupted_applies() == 1  # ...and the gateway restarts
    got = await _get(h, token, second["proposal_id"])
    assert got["status"] == "held", got
    assert got["status_reason"] == f"It depends on #{first['board_no']}, which failed.", got
    board = h.db.pending_board(SUB, "42")
    assert [r["status"] for r in board if r["id"] == second["proposal_id"]] == ["held"]


async def test_an_expired_first_step_holds_its_dependents_and_does_not_block_the_member(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    first = await _propose(h, token, _add("Opt"))
    second = await _propose(h, token, _add("Arcane Signet"), depends_on=[first["proposal_id"]])
    with h.db.tx() as c:
        c.execute("UPDATE proposals SET expires_at = 1 WHERE id = ?", (first["proposal_id"],))
    # before the prune marks it, and after
    board = h.db.pending_board(SUB, "42")
    assert [r["status_reason"] for r in board] == [f"depends:{first['proposal_id']}:expired"]
    got = await _get(h, token, second["proposal_id"])
    assert got["status_reason"] == f"It depends on #{first['board_no']}, which expired.", got
    await _set_mode(h, "auto")
    assert (await _get(h, token, second["proposal_id"]))["assistant_may_apply"] is False
    # the member may still apply a held change themselves
    decks = h.app.state.gateway.decks
    reset = current_client.set(BROWSER_CLIENT)
    try:
        run = decks.apply_selection(SUB, [second["proposal_id"]], via="review_page")
    finally:
        current_client.reset(reset)
    await decks.batches.wait(run["batch_id"], 60)
    assert [x["proposal_id"] for x in run["applied"]] == [second["proposal_id"]], run["not_sent"]
    assert len(ark.patches) == 1


async def test_a_change_that_replaces_another_apps_change_waits_for_the_member(stack: Stack) -> None:
    """A new change that replaces another app's waiting change is
    not applied by an assistant in Full auto; one that replaces only its own app's change is."""
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    await _set_mode(h, "auto")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    await _set_mode(h, "manual")
    theirs = await _propose(h, b, _add("Opt"))
    await _set_mode(h, "auto")
    new = await _propose(h, a, _add("Delver of Secrets"), replaces=[theirs["proposal_id"]])
    # ChatGPT's change is not closed by Claude: it waits until the member's press
    assert "replaced" not in new and new.get("replaces_when_applied") == [theirs["proposal_id"]], new
    assert h.db.get_proposal(theirs["proposal_id"], SUB)["state"] == "pending"
    assert new["assistant_may_apply"] is False
    assert new["press_reason"] == "includes or replaces a change another app made or changed"
    assert new["replace_note"] == f"This replaces #{theirs['board_no']}, which still waits."
    old = await _get(h, b, theirs["proposal_id"])
    assert old["replace_note"] == f"#{new['board_no']} would replace this. Apply one of them, not both."
    # ChatGPT can't apply its own change by itself while a replacement waits
    assert old["assistant_may_apply"] is False
    assert old["press_reason"] == f"would be replaced by #{new['board_no']}, which also waits"
    out = structured(await call(h, a, "apply_proposal", {"proposal_id": new["proposal_id"]}))
    assert out.get("state") != "applied" and ark.patches == [], out
    assert h.db.get_proposal(theirs["proposal_id"], SUB)["state"] == "pending"
    # the member's press applies it, and only then is ChatGPT's change closed as replaced
    decks = h.app.state.gateway.decks
    reset = current_client.set(BROWSER_CLIENT)
    try:
        run = decks.apply_selection(SUB, [new["proposal_id"]], via="review_page")
    finally:
        current_client.reset(reset)
    await decks.batches.wait(run["batch_id"], 60)
    assert [x["proposal_id"] for x in run["applied"]] == [new["proposal_id"]], [
        f.get("why") for f in run["failed"]
    ]
    gone = h.db.get_proposal(theirs["proposal_id"], SUB)
    assert gone["state"] == "replaced" and gone["status_reason"] == f"replaced by #{new['board_no']}", gone
    shown = await _get(h, b, theirs["proposal_id"])
    assert shown["replace_note"] == f"Replaced by #{new['board_no']}."
    # replacing its own app's change is not stopped by this
    await _set_mode(h, "manual")
    own = await _propose(h, a, _add("Island"))
    await _set_mode(h, "auto")
    again = await _propose(h, a, _add("Swamp"), replaces=[own["proposal_id"]])
    assert "press_reason" not in again, again
    assert again["replaced"] == [own["proposal_id"]] and "replaces_when_applied" not in again


async def test_rejecting_a_replacement_of_another_apps_change_leaves_that_change_waiting(
    stack: Stack,
) -> None:
    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    theirs = await _propose(h, b, _add("Opt"))
    new = await _propose(h, a, _add("Ponder"), replaces=[theirs["proposal_id"]])
    assert structured(await call(h, a, "reject_proposal", {"proposal_id": new["proposal_id"]}))["ok"]
    row = h.db.get_proposal(theirs["proposal_id"], SUB)
    assert row["state"] == "pending" and row["status"] is None, row


async def test_a_change_and_its_replacement_ticked_together_are_refused_by_name(stack: Stack) -> None:
    """Another app's change waits until its replacement is applied, so both can
    be ticked; Apply all never applies both, and never picks one for the member."""
    from mtg_gateway.decks import DeckError

    from .test_decks_and_proxy import Browser

    h, ark = stack.h, stack.ark
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    theirs = await _propose(h, b, _add("Opt"))
    new = await _propose(h, a, _add("Delver of Secrets"), replaces=[theirs["proposal_id"]])
    pair = [theirs["proposal_id"], new["proposal_id"]]
    decks = h.app.state.gateway.decks
    want = (
        f"#{new['board_no']} replaces #{theirs['board_no']} on {theirs['deck_name']}, so they can't "
        "be applied together. Nothing was sent. Uncheck one of them, then apply again."
    )
    reset = current_client.set(BROWSER_CLIENT)
    try:
        try:
            decks.apply_selection(SUB, pair, via="review_page")
            raise AssertionError("both were started")
        except DeckError as exc:
            assert exc.kind == "pick_one" and str(exc) == want, str(exc)
    finally:
        current_client.reset(reset)
    # the Proposals page names the pair and sends nothing
    br = Browser(h)
    try:
        await br.login()
        csrf = await br.csrf("/proposals")
        form = {"csrf": csrf, "action": "apply", "back": "/proposals"}
        versions = {f"v_{p}": "1" for p in pair}
        r = await br.http.post("/proposals/select", data={**form, **versions, "pick": pair})
        assert r.status_code == 409 and "Uncheck one of them" in r.text, r.text
        r = await br.http.post(
            "/proposals/apply", data={**form, **versions, "picks": ",".join(pair), "shown": "1.x"}
        )
        assert r.status_code == 409 and "Uncheck one of them" in r.text
    finally:
        await br.aclose()
    assert ark.patches == []
    assert all(h.db.get_proposal(p, SUB)["state"] == "pending" for p in pair)
    # either one alone goes to the confirm
    plan = decks.plan_selection(SUB, [theirs["proposal_id"]])
    assert plan["count"] == 1


async def test_the_pages_show_why_a_change_waits_and_what_replaces_it(stack: Stack) -> None:
    """The member sees the replace pair and the second-step wait on
    the Proposals list and the review page, and the assistant's next step in Full auto says why."""
    import html

    from .test_decks_and_proxy import Browser

    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    theirs = await _propose(h, b, _add("Opt"))
    new = await _propose(h, a, _add("Delver of Secrets"), replaces=[theirs["proposal_id"]])
    first = await _propose(h, a, _add("Island"))
    second = await _propose(h, a, _add("Swamp"), depends_on=[first["proposal_id"]])
    await _set_mode(h, "auto")
    step = (await _get(h, a, second["proposal_id"]))["next_step"]
    assert f"it waits for #{first['board_no']}, which is not applied, so it waits for the user's own" in step
    assert "Do not call apply_proposal." in step and "asks them every time" not in step
    br = Browser(h)
    try:
        await br.login()
        listing = html.unescape((await br.http.get("/proposals")).text)
        assert f"#{new['board_no']} would replace this. Apply one of them, not both." in listing
        assert f"This replaces #{theirs['board_no']}, which still waits." in listing
        assert f"Waits for #{first['board_no']}, which is not applied." in listing
        review = html.unescape((await br.http.get(f"/proposals/{new['proposal_id']}")).text)
        assert "<dt>Waits for you</dt>" in review
        assert (
            f"This replaces #{theirs['board_no']}, which still waits. This one includes or replaces a "
            "change another app made or changed, so it needs your press." in review
        )
        review = html.unescape((await br.http.get(f"/proposals/{second['proposal_id']}")).text)
        assert (
            f"This one waits for #{first['board_no']}, which is not applied, so it needs your press."
            in review
        )
        plain = html.unescape((await br.http.get(f"/proposals/{first['proposal_id']}")).text)
        assert "<dt>Waits for you</dt>" not in plain
    finally:
        await br.aclose()


async def test_applying_a_replaced_change_on_its_own_holds_its_replacement(stack: Stack) -> None:
    """A safe default: ChatGPT's +Arcane Signet is #1, Claude's
    +Delver replacing it is #2. The member applies #1 on its own; #2 is then held, says why, and
    no assistant applies it in any mode. The member may still apply it."""
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    theirs = await _propose(h, b, _add("Arcane Signet"))
    new = await _propose(h, a, _add("Delver of Secrets"), replaces=[theirs["proposal_id"]])
    decks = h.app.state.gateway.decks
    reset = current_client.set(BROWSER_CLIENT)
    try:
        run = decks.apply_selection(SUB, [theirs["proposal_id"]], via="review_page")
        await decks.batches.wait(run["batch_id"], 60)
    finally:
        current_client.reset(reset)
    assert h.db.get_proposal(theirs["proposal_id"], SUB)["state"] == "applied"
    got = await _get(h, a, new["proposal_id"])
    assert got["status"] == "held", got
    assert got["status_reason"] == f"It would replace #{theirs['board_no']}, which was applied.", got
    assert got["press_reason"] == f"would replace #{theirs['board_no']}, which was applied"
    assert got["next_step"].startswith("Held: the proposal this one would replace was applied on its own")
    assert "still does exactly what was reviewed" not in str(got)
    for mode in ("semi", "auto"):
        await _set_mode(h, mode)
        assert (await _get(h, a, new["proposal_id"]))["assistant_may_apply"] is False
        out = structured(await call(h, a, "apply_proposal", {"proposal_id": new["proposal_id"]}))
        assert out.get("state") != "applied", out
    assert h.db.get_proposal(new["proposal_id"], SUB)["state"] == "pending"
    sent = len(ark.patches)
    # the member may still apply it
    reset = current_client.set(BROWSER_CLIENT)
    try:
        run = decks.apply_selection(SUB, [new["proposal_id"]], via="review_page")
        await decks.batches.wait(run["batch_id"], 60)
    finally:
        current_client.reset(reset)
    assert [x["proposal_id"] for x in run["applied"]] == [new["proposal_id"]], run
    assert len(ark.patches) > sent


async def test_a_replaced_change_applied_outside_the_apply_paths_is_held_when_read(stack: Stack) -> None:
    """The same hold, set lazily when the board is read (a row closed as applied without settle)."""
    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    b, _ = await _app_token(h, "ChatGPT")
    theirs = await _propose(h, b, _add("Arcane Signet"))
    new = await _propose(h, a, _add("Delver of Secrets"), replaces=[theirs["proposal_id"]])
    with h.db.tx() as c:
        c.execute("UPDATE proposals SET state = 'applied' WHERE id = ?", (theirs["proposal_id"],))
    assert h.db.get_proposal(new["proposal_id"], SUB)["status"] is None
    got = await _get(h, a, new["proposal_id"])
    assert got["status"] == "held" and "which was applied" in got["status_reason"], got


async def test_applying_a_change_its_own_app_replaced_names_the_replacement(stack: Stack) -> None:
    """Review round 3, Low: the refusal says the change was replaced, and by which."""
    from mtg_gateway.decks import DeckError

    h = stack.h
    await linked_user(stack)
    await _set_mode(h, "manual")
    a, _ = await _app_token(h, "Claude")
    old = await _propose(h, a, _add("Opt"))
    new = await _propose(h, a, _add("Delver of Secrets"), replaces=[old["proposal_id"]])
    decks = h.app.state.gateway.decks
    reset = current_client.set(BROWSER_CLIENT)
    try:
        try:
            decks.apply_selection(SUB, [old["proposal_id"]], via="review_page")
            raise AssertionError("a replaced change was started")
        except DeckError as exc:
            assert str(exc) == (
                f"#{old['board_no']} was replaced by #{new['board_no']}, so nothing was sent. "
                f"Apply #{new['board_no']} instead."
            ), str(exc)
    finally:
        current_client.reset(reset)
    line = decks.plan_selection(SUB, [old["proposal_id"]])["skipped"][0]
    assert (
        line["why"] == f"Not sent: it was replaced by #{new['board_no']}. Apply #{new['board_no']} instead."
    )
