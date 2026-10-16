"""Release 0.7.29, approvals and proposals: the Proposals page with checkboxes, the confirm, the
result panel and Undo, the deck page's strip and the Home tile (FLEX §3-§4, Q134 A2)."""

from __future__ import annotations

import re

from .conftest import Stack, aged_stamp
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import Browser, call, linked_user, structured

SUB = "user-1"


async def _propose(h, token: str, changes: list, deck: str = "42", **more: object) -> str:
    p = structured(
        await call(h, token, "propose_deck_changes", {"deck_id": deck, "changes": changes, **more})
    )
    assert p["ok"], p
    return p["proposal_id"]


def _add(name: str) -> list[dict]:
    return [{"action": "add", "card_name": name}]


def _qty(ark, name: str) -> int:
    return sum(
        c["quantity"]
        for c in ark.decks[42]["cards"]
        if c["card"]["oracleCard"]["name"].split(" // ")[0] == name
    )


async def _signed_in(stack: Stack) -> tuple[str, Browser]:
    token = await linked_user(stack)
    await _set_mode(stack.h, "manual")
    b = Browser(stack.h)
    await b.login()
    return token, b


async def test_the_list_groups_waiting_changes_by_deck_with_checkboxes(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token, b = await _signed_in(stack)
    try:
        one = await _propose(h, token, _add("Opt"))
        await _propose(h, token, [{"action": "set_quantity", "card_name": "Forest", "quantity": 14}])
        new = structured(
            await call(h, token, "propose_new_deck", {"name": "Brew", "decklist_text": "1 Sol Ring"})
        )["proposal_id"]
        forest = next(c for c in ark.decks[42]["cards"] if c["card"]["oracleCard"]["name"] == "Forest")
        forest["quantity"] = 13
        h.app.state.gateway.decks.recheck_waiting(SUB, await h.app.state.gateway.decks.get_deck(SUB, 42))
        page = (await b.http.get("/proposals")).text
        assert "id='deck-42'" in page and "data-all='42'" in page
        assert f"name='pick' value='{one}' data-group='42'" in page
        assert f"name='v_{one}' value='1'" in page
        assert "<span class='st wait'>Waiting</span>" in page
        assert "<span class='st look'" in page and "Needs a look" in page  # #2 would now be 13 -> 14
        assert "Not tied to one of your decks" in page and f"value='{new}' data-group='untied'" in page
        assert "Apply checked" in page and "Reject checked" in page
        assert "/static/proposals.js" in page
    finally:
        await b.aclose()


async def test_reject_from_the_list_rejects_only_the_checked_rows(stack: Stack) -> None:
    h = stack.h
    token, b = await _signed_in(stack)
    try:
        a = await _propose(h, token, _add("Opt"))
        c = await _propose(h, token, _add("Delver of Secrets"))
        csrf = await b.csrf("/proposals")
        r = await b.http.post(
            "/proposals/select", data={"csrf": csrf, "action": "reject", "pick": a, "back": "/proposals"}
        )
        assert r.status_code == 303 and r.headers["location"] == "/proposals?ok=rejected_some"
        assert h.db.get_proposal(a, SUB)["state"] == "rejected"
        assert h.db.get_proposal(c, SUB)["state"] == "pending"
        # nothing checked: nothing is done
        r = await b.http.post("/proposals/select", data={"csrf": csrf, "action": "reject"})
        assert r.headers["location"] == "/proposals?err=none_selected"
        # a forged form without the session's token does nothing
        r = await b.http.post("/proposals/select", data={"csrf": "x", "action": "reject", "pick": c})
        assert r.status_code == 403 and h.db.get_proposal(c, SUB)["state"] == "pending"
    finally:
        await b.aclose()


async def test_apply_confirms_the_order_then_applies_and_offers_undo(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token, b = await _signed_in(stack)
    try:
        old = await _propose(h, token, _add("Swamp"))
        a = await _propose(h, token, _add("Opt"))
        c = await _propose(h, token, _add("Delver of Secrets"))
        csrf = await b.csrf("/proposals")
        r = await b.http.post(
            "/proposals/select",
            content=f"csrf={csrf}&action=apply&pick={c}&pick={a}&v_{a}=1&v_{c}=1&back=/proposals",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        assert r.status_code == 200, r.text
        confirm = r.text
        assert "2 changes, 2 rows in all." in confirm
        assert confirm.index("+1 Opt") < confirm.index("+1 Delver of Secrets")  # oldest first
        assert "1 older change to this deck stays waiting." in confirm
        assert "Apply 2 changes" in confirm and "action='/proposals/apply'" in confirm
        assert _qty(ark, "Opt") == 0  # the confirm sends nothing
        picks = re.search(r"name='picks' value='([^']+)'", confirm).group(1)
        assert picks == f"{a},{c}"
        # a press faster than a person reads is refused (click guard)
        r = await b.http.post(
            "/proposals/apply",
            data={"csrf": csrf, "action": "apply", "picks": picks, "shown": "1.x", "back": "/proposals"},
        )
        assert r.headers["location"] == "/proposals?err=none_selected" and _qty(ark, "Opt") == 0
        shown = aged_stamp(h.settings, f"select:{SUB}")
        r = await b.http.post(
            "/proposals/apply",
            data={"csrf": csrf, "action": "apply", "picks": picks, "shown": shown, "back": "/proposals"},
        )
        assert r.status_code == 303 and r.headers["location"].startswith("/proposals/applied/"), r.text
        result = (await b.http.get(r.headers["location"])).text
        assert "Applied 2." in result and "Undo on" in result
        assert _qty(ark, "Opt") == 1 and _qty(ark, "Delver of Secrets") == 1
        assert h.db.get_proposal(old, SUB)["state"] == "pending"
        form = dict(re.findall(r"name='(snapshot|applied|after)' value='([^']*)'", result))
        r = await b.http.post("/proposals/undo", data={"csrf": csrf, "action": "preview", **form})
        assert r.status_code == 200 and "Undo puts the deck back" in r.text, r.text
        assert "This also reverts" not in r.text
        shown = aged_stamp(h.settings, f"undo:{SUB}:{form['snapshot']}")
        r = await b.http.post(
            "/proposals/undo", data={"csrf": csrf, "action": "undo", "shown": shown, **form}
        )
        assert r.status_code == 303 and r.headers["location"].endswith("?ok=undone"), r.text
        assert _qty(ark, "Opt") == 0 and _qty(ark, "Delver of Secrets") == 0
    finally:
        await b.aclose()


async def test_the_review_page_sends_the_version_it_showed(stack: Stack) -> None:
    """G16: the review page's Apply carries the version; one amended since is refused."""
    h, ark = stack.h, stack.ark
    token, b = await _signed_in(stack)
    try:
        pid = await _propose(h, token, _add("Opt"))
        url = f"/proposals/{pid}"
        assert "name='version' value='1'" in (await b.http.get(url)).text
        form = await b.review_form(url)
        out = structured(
            await call(
                h,
                token,
                "amend_proposal",
                {"proposal_id": pid, "version": 1, "changes": _add("Delver of Secrets")},
            )
        )
        assert out["ok"], out
        r = await b.http.post(url, data={**form, "action": "apply", "version": "1"})
        assert r.headers["location"] == f"{url}?err=changed", r.headers
        assert _qty(ark, "Delver of Secrets") == 0 and _qty(ark, "Opt") == 0
        r = await b.http.post(url, data={**form, "action": "apply", "version": "2"})
        assert r.headers["location"] == f"{url}?ok=applied"
        page = (await b.http.get(url)).text
        assert "Undo this change" in page
    finally:
        await b.aclose()


async def test_the_deck_page_shows_the_strip_and_home_counts_the_decks(stack: Stack) -> None:
    h = stack.h
    token, b = await _signed_in(stack)
    try:
        page = (await b.http.get("/decks/42")).text
        assert "class='wstrip'" not in page  # nothing waiting: no strip
        await _propose(h, token, _add("Opt"))
        await _propose(h, token, _add("Delver of Secrets"))
        page = (await b.http.get("/decks/42")).text
        assert "<details class='wstrip' id='waiting' data-open-wide>" in page
        assert "<b>2 waiting changes</b>" in page and "oldest 1 min" in page
        # at the top, under the banner (Q134 A2)
        assert page.index("class='banner") < page.index("class='wstrip'") < page.index("id='cards'")
        assert "name='back' value='/decks/42'" in page
        home = (await b.http.get("/")).text
        assert "2 waiting on 1 deck." in home
    finally:
        await b.aclose()
