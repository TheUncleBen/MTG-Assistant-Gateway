"""An apply whose write reached Archidekt but got no answer (a timeout after the write landed).

Every way a change is applied (the assistant's apply_proposal, Apply on the proposal card, Apply
all, an apply that carries on in the background, the review page) reads the deck again and says
what is there. A change the deck shows in full is applied; one it shows only partly stops as
"part of it may have reached Archidekt", with Propose the rest for what is missing. None of them
says "nothing was changed" or invites the same change again, which would make it twice."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from mtg_gateway import decks as decks_mod
from mtg_gateway.decks import BROWSER_CLIENT, current_client

from .conftest import Stack
from .fake_archidekt import FakeArchidekt
from .test_approval_modes import _set_mode
from .test_decks_and_proxy import call, linked_user, structured
from .track_a_helpers import press_approve, review_page_apply

SUB = "user-1"
ONE = [{"action": "set_quantity", "card_name": "Forest", "quantity": 3}]
THREE = [
    {"action": "set_quantity", "card_name": "Forest", "quantity": 3},
    {"action": "remove", "card_name": "Acidic Slime"},
    {"action": "set_quantity", "card_name": "Island", "quantity": 2},
]
# What must never be said of a change that may have reached Archidekt
DUPLICATE_INVITES = ("Nothing further was changed", "Nothing was changed", "Create a new proposal")


def _qty(ark: FakeArchidekt, name: str, deck: int = 42) -> int:
    return sum(
        c["quantity"]
        for c in ark.decks[deck]["cards"]
        if c["card"]["oracleCard"]["name"].split(" // ")[0] == name
    )


def _answer_lost(ark: FakeArchidekt, *, nth: int = 1, path: str = "/decks/42/modifyCards") -> dict[str, int]:
    """Archidekt takes the ``nth`` write whose path holds ``path`` (it lands), but its answer never
    reaches the gateway: the call times out. Every other request is answered as usual."""
    orig = ark.transport.handler
    seen = {"writes": 0}

    async def handler(req: httpx.Request) -> httpx.Response:
        resp = orig(req)
        if req.method in ("PATCH", "POST", "PUT") and path in req.url.path:
            seen["writes"] += 1
            if seen["writes"] == nth:
                raise httpx.ReadTimeout("no answer", request=req)
        return resp

    ark.transport.handler = handler
    return seen


def _no_duplicate_invite(*texts: object) -> None:
    for text in texts:
        for bad in DUPLICATE_INVITES:
            assert bad not in str(text), text


async def _propose(h, token: str, changes: list) -> str:
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": changes}))
    assert p["ok"] and p["state"] == "pending", p
    return p["proposal_id"]


async def test_the_assistants_apply_of_a_write_that_landed_unanswered_is_applied(stack: Stack) -> None:
    """The one write of the change landed, and Archidekt never answered it: the deck read again
    shows the whole change, so it is applied, sent once, and nobody is told to make it again."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    pid = await _propose(h, token, ONE)
    _answer_lost(ark)
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["state"] == "applied", (out, got)
    assert out["ok"] and out["state"] == "applied", out
    assert _qty(ark, "Forest") == 3 and len(ark.patches) == 1
    assert got["next_step"].startswith("Done."), got["next_step"]
    _no_duplicate_invite(out, got["next_step"])


async def test_the_assistants_apply_that_stopped_unanswered_partway_offers_only_the_rest(
    stack: Stack,
) -> None:
    """The first of three writes landed unanswered and the apply stopped there: the deck read again
    shows only that one, so the change failed as partly made. Its next step is Propose the rest,
    which proposes only the two the deck does not show, never the one that landed."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    pid = await _propose(h, token, THREE)
    _answer_lost(ark)
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
    assert out["ok"] is False and out.get("may_have_changed") is True, out
    assert out.get("rest_offered") is True, out
    assert "Archidekt didn't answer in time" in out["message"], out
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["state"] == "failed" and got.get("rest_offered") is True, got
    assert "Propose the rest" in got["next_step"], got["next_step"]
    _no_duplicate_invite(out["message"], got["next_step"])
    assert len(ark.patches) == 1  # the first write went out, and nothing after it
    landed = {
        name
        for name, done in (
            ("Forest", _qty(ark, "Forest") == 3),
            ("Acidic Slime", _qty(ark, "Acidic Slime") == 0),
            ("Island", _qty(ark, "Island") == 2),
        )
        if done
    }
    assert len(landed) == 1, landed
    rest = structured(await call(h, token, "propose_rest", {"proposal_id": pid}))
    assert rest["ok"], rest
    names = {c["name"] for c in rest["changes"]}
    assert names == {"Forest", "Acidic Slime", "Island"} - landed, rest["changes"]


async def test_the_cards_apply_of_a_write_that_landed_unanswered_is_applied(stack: Stack) -> None:
    """The member's Approve on the proposal card: the same outcome as the assistant's apply."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    pid = await _propose(h, token, ONE)
    _answer_lost(ark)
    out = await press_approve(h, token, pid)
    assert out["ok"] and out["state"] == "applied", out
    assert _qty(ark, "Forest") == 3 and len(ark.patches) == 1
    _no_duplicate_invite(out)


async def test_the_cards_apply_that_stopped_unanswered_partway_says_it_may_have_changed(stack: Stack) -> None:
    """On the card, a change that stopped after an unanswered write carries may_have_changed and
    Propose the rest, so the card never adds "ask the assistant for a new proposal"."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    pid = await _propose(h, token, THREE)
    _answer_lost(ark)
    out = await press_approve(h, token, pid)
    assert out["ok"] is False and out["state"] == "failed", out
    assert out.get("may_have_changed") is True and out.get("rest_offered") is True, out
    _no_duplicate_invite(out["message"])


async def test_apply_all_counts_a_write_that_landed_unanswered_as_applied(stack: Stack) -> None:
    """Apply all on the Proposals page: the change whose write landed unanswered is applied, and
    the next one in the selection goes on."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    first = await _propose(h, token, ONE)
    second = structured(
        await call(
            h,
            token,
            "propose_deck_changes",
            {
                "deck_id": "42",
                "changes": [{"action": "set_quantity", "card_name": "Island", "quantity": 2}],
                "separate": True,
            },
        )
    )["proposal_id"]
    _answer_lost(ark)
    decks = h.app.state.gateway.decks
    reset = current_client.set(BROWSER_CLIENT)
    try:
        run = decks.apply_selection(SUB, [first, second], via="browser")
    finally:
        current_client.reset(reset)
    await decks.batches.wait(run["batch_id"], 60)
    assert [a["proposal_id"] for a in run["applied"]] == [first, second], run
    assert run["failed"] == [] and run["not_sent"] == [], run
    assert _qty(ark, "Forest") == 3 and _qty(ark, "Island") == 2


async def test_apply_all_says_a_change_that_stopped_unanswered_may_have_changed(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    pid = await _propose(h, token, THREE)
    _answer_lost(ark)
    decks = h.app.state.gateway.decks
    reset = current_client.set(BROWSER_CLIENT)
    try:
        run = decks.apply_selection(SUB, [pid], via="browser")
    finally:
        current_client.reset(reset)
    await decks.batches.wait(run["batch_id"], 60)
    assert [f["proposal_id"] for f in run["failed"]] == [pid], run
    why = run["failed"][0]["why"]
    assert "may have reached Archidekt" in why, why
    _no_duplicate_invite(why)


async def test_a_background_apply_that_stopped_unanswered_partway_says_so_later(
    stack: Stack, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An apply that outlived the caller's wait (it answered "applying") and then stopped on an
    unanswered write: get_proposal afterwards gives Propose the rest, never "make a new one"."""
    monkeypatch.setattr(decks_mod, "APPLY_WAIT_SECONDS", 0.05)
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    pid = await _propose(h, token, THREE)
    orig = ark.transport.handler
    release = asyncio.Event()

    async def slow(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and "/decks/42/modifyCards" in req.url.path:
            await release.wait()
            orig(req)  # it lands
            raise httpx.ReadTimeout("no answer", request=req)
        return orig(req)

    ark.transport.handler = slow
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
    assert out["state"] == "applying", out
    release.set()
    await h.app.state.gateway.decks.wait_applied(pid)
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["state"] == "failed" and got.get("rest_offered") is True, got
    assert "Propose the rest" in got["next_step"], got["next_step"]
    _no_duplicate_invite(got["next_step"])


async def test_the_review_page_says_a_change_that_stopped_unanswered_may_have_changed(stack: Stack) -> None:
    """The review page's own Apply: the page says the deck may have changed and offers Propose the
    rest, not "Nothing was changed on Archidekt"."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    pid = await _propose(h, token, THREE)
    _answer_lost(ark)
    got = await review_page_apply(h, token, pid)
    assert got["state"] == "failed" and got.get("rest_offered") is True, got
    from .test_decks_and_proxy import Browser

    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{pid}")).text
    finally:
        await b.aclose()
    assert "Propose the rest" in page
    _no_duplicate_invite(page.split("<main", 1)[-1])


async def test_a_details_change_whose_write_landed_unanswered_never_says_nothing_changed(
    stack: Stack,
) -> None:
    """A change of another kind (the deck's name): the deck may have changed, so its failure never
    says nothing was changed or to make a new proposal."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "manual")
    p = structured(
        await call(
            h,
            token,
            "propose_deck_details",
            {"deck_id": "42", "details": {"name": "Renamed by the assistant"}},
        )
    )
    assert p["ok"], p
    _answer_lost(ark, path="/decks/42/update")
    out = await press_approve(h, token, p["proposal_id"])
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert ark.decks[42]["name"] == "Renamed by the assistant"
    if got["state"] == "failed":
        assert out.get("may_have_changed") is True, out
        _no_duplicate_invite(out.get("message"), got["next_step"])
    else:
        assert got["state"] == "applied", got


async def test_a_category_change_whose_write_went_unanswered_says_the_deck_may_have_changed(
    stack: Stack,
) -> None:
    """A kind of change the apply does not read through (a category rename): it fails, and every
    place that says so (the card's answer, get_proposal, the review page) says the deck may have
    changed and to check it, never that nothing was changed or to make the change again."""
    from .test_deck_categories import _give_categories
    from .test_decks_and_proxy import Browser

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    _give_categories(ark)
    await _set_mode(h, "manual")
    p = structured(
        await call(
            h,
            token,
            "propose_deck_categories",
            {"deck_id": "42", "changes": [{"category": "Ramp", "rename_to": "Mana"}]},
        )
    )
    assert p["ok"], p
    _answer_lost(ark, path="/decks/category/")
    out = await press_approve(h, token, p["proposal_id"])
    assert out["ok"] is False and out["state"] == "failed", out
    assert out.get("may_have_changed") is True and out.get("retryable") is False, out
    assert "may have" in out["message"], out["message"]
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got.get("may_have_changed") is True, got
    assert "may have reached Archidekt" in got["next_step"], got["next_step"]
    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{p['proposal_id']}")).text
    finally:
        await b.aclose()
    assert "may have" in page
    _no_duplicate_invite(out["message"], got["next_step"], page.split("<main", 1)[-1])


# -- the stored session refused at the backup (0.7.46 audit PA-4) ----------------------------------


def _refused_at_backup(ark: FakeArchidekt) -> None:
    orig = ark.transport.handler

    def revoked(req: httpx.Request) -> httpx.Response:
        # from the backup copy on, Archidekt refuses the stored session and its refresh
        if req.method == "POST" and ("/decks/copy/" in req.url.path or "/decks/folders/" in req.url.path):
            ark.tokens.clear()
            ark.inject = [(401, None)] * 2
        return orig(req)

    ark.transport.handler = revoked


async def test_an_apply_stopped_by_a_refused_session_says_to_relink(stack: Stack) -> None:
    """Archidekt refused the stored session when the backup copy was made: nothing changed and the
    proposal waits, but "apply it again once Archidekt answers" was wrong, as only relinking fixes
    it. The answer, the proposal's next step and the review page say to relink, then apply again."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    await _set_mode(h, "auto")
    pid = await _propose(h, token, ONE)
    assert stack.gw.decks.live.get("archidekt_backups")  # backups on, as shipped
    _refused_at_backup(ark)
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": pid}))
    assert out["ok"] is False and out["error"] == "backup_failed", out
    assert "Relink" in out["message"] and "then apply it again" in out["message"], out
    assert "once Archidekt answers" not in out["message"], out
    got = structured(await call(h, token, "get_proposal", {"proposal_id": pid}))
    assert got["state"] == "pending" and "Relink" in got["next_step"], got["next_step"]
    assert _qty(ark, "Forest") != 3 and not ark.patches
    from .test_decks_and_proxy import Browser

    b = Browser(h)
    try:
        await b.login()
        page = (await b.http.get(f"/proposals/{pid}")).text
    finally:
        await b.aclose()
    assert "Relink under Account details, then apply it again" in page, page
    assert "once Archidekt answers" not in page


async def test_the_review_pages_apply_stopped_by_a_refused_session_says_to_relink(stack: Stack) -> None:
    from .test_decks_and_proxy import Browser

    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    pid = await _propose(h, token, ONE)
    _refused_at_backup(ark)
    b = Browser(h)
    try:
        await b.login()
        url = f"/proposals/{pid}"
        r = await b.http.post(url, data={**await b.review_form(url), "action": "apply"})
        assert r.status_code == 303 and "err=backup_relink" in r.headers["location"], r.headers
        page = (await b.http.get(r.headers["location"])).text
    finally:
        await b.aclose()
    assert "relink under Account details, then apply it again" in page, page
    assert not ark.patches


# -- a deck delete whose read back failed (0.7.46 audit PA-11) --------------------------------------


async def test_a_delete_that_could_not_be_read_back_says_so(stack: Stack) -> None:
    """The read back after the delete was answered "slow down": that shows nothing about the deck,
    so it is not said to be still there; the answer says it couldn't be checked."""
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    orig = ark.transport.handler
    deleted = {"yes": False}

    def busy_after_delete(req: httpx.Request) -> httpx.Response:
        if req.method == "DELETE":
            deleted["yes"] = True
        elif deleted["yes"] and req.method == "GET" and req.url.path == "/api/decks/42/":
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "1"})
        return orig(req)

    ark.transport.handler = busy_after_delete
    with pytest.raises(decks_mod.DeckError) as err:
        await h.app.state.gateway.decks.delete_deck(SUB, "42", "Sample Commander Deck")
    text = str(err.value)
    assert deleted["yes"] and "couldn't be read back to check it is gone" in text, text
    assert "can still be read" not in text, text
