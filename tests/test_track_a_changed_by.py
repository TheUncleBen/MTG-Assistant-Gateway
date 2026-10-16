"""Release 0.7.29, approvals: what the member sees about a change before the press (the Q141
check). An amended change says which app changed it and what (G16, Q134 item 5) on the card's
data, the review page and the "Apply all" list; a held one says so there too; a form that does
not carry the version it showed is refused (G16); the Guide's Full auto line says what still
waits."""

from __future__ import annotations

from mtg_gateway.approve import APPROVAL_SET_META_KEY

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
        assert "<dt>Version</dt><dd>2, changed by " in page and ": Opt instead of Ponder</dd>" in page
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
    # a held one and a changed one say so in the list the confirm draws
    assert h.db.set_hold(first["proposal_id"], SUB, "depends:none:rejected")
    _amend(
        h,
        first["proposal_id"],
        structured(await call(h, token, "get_proposal", {"proposal_id": first["proposal_id"]})),
        by="another-app",
        what="Opt again",
    )
    res = await _propose(h, token, "Brainstorm")
    lines = {x["proposal_id"]: x for x in structured(res)["deck_waiting"]["set_lines"]}
    held = lines.get(first["proposal_id"])
    assert held is not None, lines
    assert (
        held["version"] == 2
        and held["what_changed"] == "Opt again"
        and held["changed_by"].startswith("app: ")
    )


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
        assert "changed since you opened it" in page
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
        assert "a new deck and an edit of more than 25 rows" in page
        assert (
            "deleting a deck always wait for your press, and so does a held change or one another app changed"
            in page
        )
        assert "applies the rest too" not in page
    finally:
        await b.aclose()
