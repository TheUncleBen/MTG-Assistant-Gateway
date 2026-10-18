"""Release 0.7.29, approvals: what the member sees about a change before the press.
An amended change says which app changed it and what on the card's
data, the review page and the "Apply all" list; a held one says so there too; a form that does
not carry the version it showed is refused; the Guide's Full auto line says what still
waits."""

from __future__ import annotations

from mtg_gateway.approve import APPROVAL_SET_META_KEY, APPROVALS_META_KEY

from .conftest import Stack
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import Browser, call, linked_user, mcp_token, structured

SUB = "user-1"


async def _propose(h, token: str, name: str) -> dict:
    res = await call(
        h, token, "propose_deck_changes", {"deck_id": "42", "changes": [{"action": "add", "card_name": name}]}
    )
    return res


def _amend(h, pid: str, row: dict, *, by: str, what: str) -> None:
    assert h.db.amend_proposal(
        pid,
        SUB,
        expect_version=int(row["version"]),
        changes=row["changes"],
        rows=row["rows"],
        diff_text=row["diff"],
        by_client=by,
        what_changed=what,
    )


async def test_an_amended_change_says_who_changed_it_and_what(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    p = structured(await _propose(h, token, "Opt"))
    _amend(h, p["proposal_id"], p, by="another-app", what="Opt instead of Ponder")
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["version"] == 2 and got["what_changed"] == "Opt instead of Ponder"
    assert got["changed_by"].startswith("app: "), got["changed_by"]
    # an unchanged one carries neither
    q = structured(await _propose(h, token, "Swamp"))
    assert "changed_by" not in q and "what_changed" not in q
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
        assert "<dt>Version</dt><dd>2, changed by " in page
        assert ". Last change: Opt instead of Ponder</dd>" in page
        other = (await b.http.get(f"/proposals/{q['proposal_id']}")).text
        assert "<dt>Version</dt>" not in other
    finally:
        await b.aclose()


async def test_apply_all_lists_its_changes_with_held_and_changed_and_names_the_app(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    other = await mcp_token(h)
    first = structured(await _propose(h, token, "Opt"))
    await _propose(h, other, "Swamp")
    res = await _propose(h, token, "Delver of Secrets")
    info = structured(res)["deck_waiting"]
    assert info["this_app"].startswith("app: ")
    ids = res["_meta"][APPROVAL_SET_META_KEY]["ids"]
    lines = {x["proposal_id"]: x for x in info["set_lines"]}
    assert set(lines) == set(ids) and lines[first["proposal_id"]]["summary"]
    # a held one and one its own app changed say so in the list the confirm draws
    maker = h.db.get_proposal(first["proposal_id"], SUB)["created_by_client"]
    assert h.db.set_hold(first["proposal_id"], SUB, "depends:none:rejected")
    _amend(
        h,
        first["proposal_id"],
        structured(await call(h, token, "get_proposal", {"proposal_id": first["proposal_id"]})),
        by=maker,
        what="Opt again",
    )
    res = await _propose(h, token, "Brainstorm")
    info = structured(res)["deck_waiting"]
    lines = {x["proposal_id"]: x for x in info["set_lines"]}
    held = lines.get(first["proposal_id"])
    assert held is not None, lines
    assert held["version"] == 2 and held["what_changed"] == "Opt again"
    assert info["left_out"] == 0
    # One another app rewrote is left out of Apply all, and counted
    _amend(
        h,
        first["proposal_id"],
        structured(await call(h, token, "get_proposal", {"proposal_id": first["proposal_id"]})),
        by="another-app",
        what="Opt once more",
    )
    res = await _propose(h, token, "Ponder")
    info = structured(res)["deck_waiting"]
    assert first["proposal_id"] not in res["_meta"][APPROVAL_SET_META_KEY]["ids"]
    assert first["proposal_id"] not in {x["proposal_id"] for x in info["set_lines"]}
    assert info["left_out"] == 1
    # an amend back by its own app still leaves it out: another app wrote one of its versions
    _amend(
        h,
        first["proposal_id"],
        structured(await call(h, token, "get_proposal", {"proposal_id": first["proposal_id"]})),
        by=maker,
        what="Opt, by its maker",
    )
    board = await call(h, token, "show_waiting_changes", {"deck_id": "42"})
    assert first["proposal_id"] not in board["_meta"][APPROVAL_SET_META_KEY]["ids"]
    assert structured(board)["left_out"] == 1
    # its own Approve still works on the card: it has its own code
    assert first["proposal_id"] in board["_meta"][APPROVALS_META_KEY]
    # the note names it by number, and its row says so
    no = h.db.get_proposal(first["proposal_id"], SUB)["board_no"]
    assert structured(board)["left_out_nos"] == [no]
    rows = {x["proposal_id"]: x for x in structured(board)["waiting"]}
    assert rows[first["proposal_id"]].get("not_in_apply_all") is True
    assert not any(x.get("not_in_apply_all") for k, x in rows.items() if k != first["proposal_id"])
    # its own card says it of itself, and does not count itself among the left out
    own = structured(await call(h, token, "get_proposal", {"proposal_id": first["proposal_id"]}))[
        "deck_waiting"
    ]
    assert own.get("this_left_out") is True and own["left_out"] == 0 and own["left_out_nos"] == [], own


async def test_a_review_page_apply_without_its_version_is_refused(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    p = structured(await _propose(h, token, "Opt"))
    url = f"/proposals/{p['proposal_id']}"
    b = Browser(h)
    try:
        await b.login()
        form = await b.review_form(url)
        assert form["version"] == "1"
        del form["version"]
        r = await b.http.post(url, data={**form, "action": "apply"})
        assert r.status_code == 303 and r.headers["location"] == f"{url}?err=changed"
        assert ark.patches == [] and h.db.get_proposal(p["proposal_id"], SUB)["state"] == "pending"
        page = (await b.http.get(r.headers["location"])).text
        assert "was made since you opened it" in page
        # with the version the page showed, it applies
        r = await b.http.post(url, data={**await b.review_form(url), "action": "apply"})
        assert r.headers["location"].endswith("?ok=applied"), r.headers["location"]
    finally:
        await b.aclose()


async def test_the_guide_says_what_full_auto_still_waits_for(stack: Stack) -> None:
    h = stack.h
    await linked_user(stack)
    h.app.state.gateway.decks.live.set("auto_big_edit_rows", 25, by="admin-test")
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get("/guide")).text
        assert (
            "a new deck, an edit of more than 25 rows, making a deck public or unlisted, and any edit of a "
            "description that has formatting, such as bold text, links or pictures" in page
        )
        assert (
            "deleting a deck always wait for your press, and so does a held change, a change waiting for "
            "another change first, one another app had a hand in, one that replaces another app's change, "
            "or one that another app's waiting change would replace" in page
        )
        assert "applies the rest too" not in page
    finally:
        await b.aclose()


async def test_a_change_another_app_rewrote_waits_even_after_its_own_app_changed_it_again(
    stack: Stack,
) -> None:
    """ChatGPT rewrites Claude's change and Claude changes it back.
    Another app still had a hand in it, so no assistant applies it by itself, in Semi-auto or
    Full auto; its line names both apps, in order, and the review page has no "app:" badge text."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    for mode, card in (("semi", "Opt"), ("auto", "Brainstorm")):
        await _set_mode(h, mode)
        p = structured(await _propose(h, token, card))
        pid = p["proposal_id"]
        maker = h.db.get_proposal(pid, SUB)["created_by_client"]
        assert p["assistant_may_apply"] is True, (mode, p)
        _amend(h, pid, p, by="another-app", what="Ponder instead")
        got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
        _amend(h, pid, got, by=maker, what="Opt again")
        got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
        assert got["assistant_may_apply"] is False, (mode, got)
        assert got["changed_by_apps"][0] == "app: another-app" and len(got["changed_by_apps"]) == 2, got
        if mode == "auto":
            # each app once, at its latest change: another app again puts it last
            _amend(h, pid, got, by="another-app", what="Ponder once more")
            again = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
            assert again["changed_by_apps"][-1] == "app: another-app", again["changed_by_apps"]
            assert len(again["changed_by_apps"]) == 2, again["changed_by_apps"]
        refused = structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
        assert refused["ok"] is False and ark.patches == [], (mode, refused)
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{pid}")).text
        assert "<dt>Version</dt><dd>4, changed by " in page, page[page.find("Version") :][:200]
        assert ", then an assistant. Last change: Ponder once more</dd>" in page
        assert "changed by <span class='badge'" not in page
    finally:
        await b.aclose()
