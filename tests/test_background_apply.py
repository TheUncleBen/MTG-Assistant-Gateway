"""A large apply answers "applying" with its progress instead of outliving the caller's timeout,
then carries on; get_proposal, the review page and a second apply call all report it."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from mtg_gateway import archidekt as ark_mod
from mtg_gateway import decks as decks_mod
from mtg_gateway.archidekt import ArchidektClient

from .conftest import AUTO, Stack, StackOptions
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, call, linked_user, structured
from .track_a_helpers import press_approve

THREE = [
    {"action": "set_quantity", "card_name": "Forest", "quantity": 3},
    {"action": "remove", "card_name": "Acidic Slime"},
    {"action": "set_quantity", "card_name": "Island", "quantity": 2},
]


@pytest.fixture
def stack_options(monkeypatch) -> StackOptions:
    """The shared AUTO stack, with the assistant's apply answering "applying" after 0.2 s."""
    monkeypatch.setattr(decks_mod, "APPLY_WAIT_SECONDS", 0.2)
    return AUTO


@pytest.fixture
def whole_apply(stack: Stack, monkeypatch) -> None:
    """For a test that checks the apply's own final reply: wait for the whole apply (on the fake it
    takes well under a second) instead of answering "applying" after 0.2 s, which a busy machine
    could reach first (wait on state, not time). It asks for ``stack`` so it is set after
    the stack's own 0.2 s, also when a test names it in ``usefixtures`` (set up before arguments)."""
    monkeypatch.setattr(decks_mod, "APPLY_WAIT_SECONDS", 30)


def _hold(ark: FakeArchidekt, when) -> asyncio.Event:
    """Hold Archidekt's answer to the first request ``when`` matches until the returned event is
    set (a slow Archidekt), so the apply outlasts APPLY_WAIT_SECONDS."""
    orig = ark.transport.handler
    release, held = asyncio.Event(), {"x": False}

    async def slow(req: httpx.Request) -> httpx.Response:
        if not held["x"] and when(req):
            held["x"] = True
            await release.wait()
        return orig(req)

    ark.transport.handler = slow
    return release


async def _until(pred, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not pred():
            await asyncio.sleep(0.01)


async def test_a_slow_apply_answers_applying_with_progress_then_finishes(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    release = _hold(ark, lambda r: r.method == "PATCH" and len(ark.patches) >= 1)
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] and out["state"] == "applying", out
    assert out["progress"]["sent_entries"] == 1 and out["progress"]["of_entries"] == 3, out
    assert "get_proposal" in out["next_step"] and "Do not apply it again" in out["next_step"]
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["state"] == "applying" and got["progress"]["sent_entries"] == 1, got
    # pressing apply again while it runs reports it and sends nothing more
    again = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert again["ok"] and again["state"] == "applying", again
    assert len(ark.patches) == 1
    release.set()
    await _until(lambda: h.db.get_proposal(p["proposal_id"], "user-1")["state"] == "applied")
    done = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert done["state"] == "applied" and "progress" not in done, done
    assert len(ark.patches) == 3
    assert not h.app.state.gateway.decks._running  # nothing left behind


async def test_a_fast_apply_still_answers_applied(stack: Stack) -> None:
    token = await linked_user(stack)
    h = stack.h
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] and out["state"] == "applied" and "progress" not in out, out


async def test_a_new_deck_shows_how_many_cards_were_looked_up(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    cards = [{"name": "Sol Ring"}, {"name": "Island"}, {"name": "Swamp"}]
    p = structured(await call(h, token, "propose_new_deck", {"name": "Slow", "cards": cards}))
    assert p["ok"], p
    seen = {"n": 0}

    def second_card(r: httpx.Request) -> bool:
        if r.url.path.startswith("/api/cards/v2/"):
            seen["n"] += 1
        return seen["n"] == 2

    release = _hold(ark, second_card)
    out = await press_approve(h, token, p["proposal_id"])
    assert out["state"] == "applying", out
    assert out["progress"] == {"resolved_cards": 1, "of_cards": 3}, out
    release.set()
    await _until(lambda: h.db.get_proposal(p["proposal_id"], "user-1")["state"] == "applied")


async def test_the_review_page_shows_progress_and_refreshes_while_applying(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    release = _hold(ark, lambda r: r.method == "PATCH" and len(ark.patches) >= 1)
    b = Browser(h)
    await b.login()
    url = f"/proposals/{p['proposal_id']}"
    r = await b.http.post(url, data={**await b.review_form(url), "action": "apply"})
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=applying"), r.headers
    page = await b.http.get(r.headers["location"])
    assert "Applying. This is a large change" in page.text
    assert "sent 1 of 3 changes" in page.text
    # the refresh reloads the plain page, so the "Applying" notice does not outlive the apply
    assert f"content='5;url=/proposals/{p['proposal_id']}'" in page.text
    assert "check again with get_proposal" not in page.text  # the page speaks to a person
    assert "Apply to Archidekt?" not in page.text  # no second Apply button while it runs
    release.set()
    await _until(lambda: h.db.get_proposal(p["proposal_id"], "user-1")["state"] == "applied")
    page = await b.http.get(url)
    assert "http-equiv='refresh'" not in page.text and "Applying to Archidekt" not in page.text
    await b.aclose()


async def test_shutdown_records_an_apply_that_had_to_be_cut_off(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    _hold(ark, lambda r: r.method == "PATCH" and len(ark.patches) >= 1)  # never released
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["state"] == "applying", out
    await h.app.state.gateway.decks.aclose(grace=0.05)
    row = h.db.get_proposal(p["proposal_id"], "user-1")
    assert row["state"] == "failed" and row["result"]["error"] == "interrupted"
    assert row["result"]["sent_entries"] == 1


def test_shutdown_grace_outlasts_a_paced_apply_and_fits_the_stack() -> None:
    """A redeploy must not cut a running apply off after a few seconds: the gateway waits
    SHUTDOWN_GRACE_SECONDS by default, and the stack and compose files give Docker longer than
    that before it kills the container."""
    import inspect
    import re
    from pathlib import Path

    from mtg_gateway.__main__ import GRACEFUL_SHUTDOWN_SECONDS

    grace = decks_mod.SHUTDOWN_GRACE_SECONDS
    assert grace >= 60
    assert inspect.signature(decks_mod.DeckService.aclose).parameters["grace"].default == grace
    # uvicorn first waits for running requests, then runs the lifespan shutdown where the apply
    # grace is spent: Docker must allow both, one after the other.
    worst = GRACEFUL_SHUTDOWN_SECONDS + grace
    for stack_file in ("deploy/portainer-stack.yml", "deploy/compose/docker-compose.yml"):
        text = (Path(__file__).resolve().parent.parent / stack_file).read_text(encoding="utf-8")
        found = re.findall(r"stop_grace_period:\s*(\d+)s", text)
        assert found, stack_file
        assert all(int(s) > worst for s in found), (stack_file, found, worst)


async def test_shutdown_lets_an_apply_finish_within_the_grace(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    release = _hold(ark, lambda r: r.method == "PATCH" and len(ark.patches) >= 1)
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["state"] == "applying", out
    closing = asyncio.ensure_future(h.app.state.gateway.decks.aclose())  # the default grace
    await asyncio.sleep(0.2)
    assert not closing.done()  # still waiting for the apply, not cutting it off
    release.set()
    await asyncio.wait_for(closing, timeout=10)
    row = h.db.get_proposal(p["proposal_id"], "user-1")
    assert row["state"] == "applied", row


async def test_a_member_s_own_large_new_deck_opens_its_progress_not_applied(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    await linked_user(stack)
    b = Browser(h)
    await b.login()
    release = _hold(ark, lambda r: r.url.path.startswith("/api/cards/v2/"))
    r = await b.http.post(
        "/decks/new",
        data={
            "csrf": await b.csrf("/account"),
            "name": "Slow",
            "format": "commander",
            "source": "1 Sol Ring\n1 Island",
        },
    )
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=applying"), r.headers
    release.set()
    pid = r.headers["location"].split("/proposals/")[1].split("?")[0]
    await _until(lambda: h.db.get_proposal(pid, "user-1")["state"] == "applied")
    await b.aclose()


async def test_a_backup_that_fails_after_the_answer_is_explained(stack: Stack, monkeypatch) -> None:
    h = stack.h
    token = await linked_user(stack)
    svc = h.app.state.gateway.decks
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))

    async def slow_fail(*a, **k):
        await asyncio.sleep(0.5)
        raise decks_mod.DeckError("unavailable", "Archidekt did not answer")

    monkeypatch.setattr(svc, "_backup_copy", slow_fail)
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["state"] == "applying", out
    await _until(lambda: h.db.get_proposal(p["proposal_id"], "user-1")["state"] == "pending")
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["result"]["error"] == "backup_failed", got
    assert got["next_step"].startswith("The last attempt to apply this stopped before anything changed")
    assert stack.ark.patches == []


async def test_an_apply_waits_out_archidekts_429_and_finishes(stack: Stack) -> None:
    """A 429 halfway through an apply pauses it (the review page says so) instead of
    failing it with the deck partly changed; the refused request is sent again after the pause."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    orig = ark.transport.handler
    refused = {"n": 0}

    def once_429(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and len(ark.patches) == 1 and not refused["n"]:
            refused["n"] += 1
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "1"})
        return orig(req)

    ark.transport.handler = once_429
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["state"] == "applying", out
    assert out["progress"].get("waiting_for_archidekt"), out
    await _until(lambda: h.db.get_proposal(p["proposal_id"], "user-1")["state"] == "applied")
    assert refused["n"] == 1 and len(ark.patches) == 3


async def test_a_failed_apply_that_changed_nothing_deletes_its_backup_copy(
    stack: Stack, whole_apply: None
) -> None:
    """Archidekt refuses the first write, so the deck is as it was; the backup copy the apply
    made a moment before is deleted again. The gateway's own snapshot stays."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    orig = ark.transport.handler

    def refuse(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and "modifyCards" in req.url.path:
            return httpx.Response(403, json={"detail": "You do not have permission."})
        return orig(req)

    ark.transport.handler = refuse
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "forbidden", out
    assert "You do not have permission" in out["message"] and "relink" not in out["message"]
    result = h.db.get_proposal(p["proposal_id"], "user-1")["result"]
    assert result.get("backup_deleted") is True and "backup_deck_id" not in result, result
    assert len(ark.deleted) == 1  # the copy, and nothing else
    assert ark.deleted[0] != 42
    snap = h.db.get_snapshot(result["snapshot_id"], "user-1")
    assert snap is not None and snap["backup_deck_id"] is None


async def test_a_failed_apply_that_changed_the_deck_keeps_its_backup_copy(
    stack: Stack, whole_apply: None
) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    orig = ark.transport.handler

    def fail_second(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and "modifyCards" in req.url.path and len(ark.patches) >= 1:
            return httpx.Response(503, json={"detail": "down"})
        return orig(req)

    ark.transport.handler = fail_second
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False, out
    result = h.db.get_proposal(p["proposal_id"], "user-1")["result"]
    assert result.get("backup_deck_id") and not result.get("backup_deleted"), result
    assert ark.deleted == []


def _refuse_once(ark: FakeArchidekt, when) -> dict[str, int]:
    """Answer the first request ``when`` matches with a 429 (Retry-After 1), as Archidekt does when
    it wants the gateway to slow down: the request was not acted on."""
    orig = ark.transport.handler
    refused = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if not refused["n"] and when(req):
            refused["n"] += 1
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "1"})
        return orig(req)

    ark.transport.handler = handler
    return refused


async def test_a_new_deck_whose_create_meets_a_429_is_created_once(stack: Stack) -> None:
    """The deck-creating POST refused with a 429 is waited out and sent again: one deck is made
    (a 429 means Archidekt did not create it), filled and verified."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    cards = [{"name": "Sol Ring"}, {"name": "Island"}]
    p = structured(await call(h, token, "propose_new_deck", {"name": "Paced", "cards": cards}))
    before = set(ark.decks)
    refused = _refuse_once(ark, lambda r: r.method == "POST" and r.url.path == "/api/decks/v2/")
    await press_approve(h, token, p["proposal_id"])
    await _until(lambda: h.db.get_proposal(p["proposal_id"], "user-1")["state"] != "applying")
    row = h.db.get_proposal(p["proposal_id"], "user-1")
    assert row["state"] == "applied", row
    made = set(ark.decks) - before
    assert refused["n"] == 1 and len(made) == 1, made
    assert row["result"]["deck_id"] == str(made.pop()) and row["result"]["verified"] is True


async def test_a_copy_whose_post_meets_a_429_is_made_once(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_clone_deck", {"deck_id": "42", "name": "Paced copy"}))
    assert p["ok"], p
    before = set(ark.decks)
    refused = _refuse_once(ark, lambda r: r.method == "POST" and r.url.path == "/api/decks/copy/")
    structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    await _until(lambda: h.db.get_proposal(p["proposal_id"], "user-1")["state"] != "applying")
    row = h.db.get_proposal(p["proposal_id"], "user-1")
    assert row["state"] == "applied", row
    made = [d for d in set(ark.decks) - before if ark.decks[d]["name"] == "Paced copy"]
    assert refused["n"] == 1 and len(made) == 1, made


# -- Archidekt's 429s during an apply and the backup copy ---------------------
# A stop that changed nothing leaves the proposal pending
NOTHING_CHANGED = (
    "Nothing was changed on Archidekt. The proposal is still pending, so it can be approved again later."
)


@pytest.fixture
def quick_waits(monkeypatch) -> list[float]:
    """The apply's 429 waits, recorded instead of slept (the allowance counts what was asked)."""
    waits: list[float] = []

    async def wait_out(self, seconds: float) -> None:
        waits.append(seconds)
        await asyncio.sleep(0)

    monkeypatch.setattr(ArchidektClient, "_wait_out", wait_out)
    return waits


def _every_patch_429(ark: FakeArchidekt, *, after: int = 0) -> dict[str, int]:
    """Answer every card PATCH after the first ``after`` with a 429 (Retry-After 0)."""
    orig = ark.transport.handler
    tries = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and "modifyCards" in req.url.path and len(ark.patches) >= after:
            tries["n"] += 1
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "0"})
        return orig(req)

    ark.transport.handler = handler
    return tries


async def _finished(h, pid: str) -> dict:
    await _until(lambda: h.db.get_proposal(pid, "user-1")["state"] != "applying", timeout=30)
    return h.db.get_proposal(pid, "user-1")


async def _review_page(h, pid: str) -> str:
    b = Browser(h)
    await b.login()
    page = (await b.http.get(f"/proposals/{pid}")).text
    await b.aclose()
    return page


async def test_an_apply_that_runs_out_of_patience_with_nothing_sent_drops_its_backup(
    stack: Stack, quick_waits: list[float]
) -> None:
    """Every card write meets a 429. The apply waits exactly six times,
    sends the write seven times, never opens the gateway-wide breaker, and stops with one plain
    message; nothing changed, so the backup copy it made is deleted again, and the proposal is
    pending again."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    tries = _every_patch_429(ark)
    structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    row = await _finished(h, p["proposal_id"])
    result = row["result"]
    assert row["state"] == "pending" and result["error"] == "rate_limited", result
    assert result["detail"] == f"{ark_mod.PATIENCE_SPENT} {NOTHING_CHANGED}", result
    assert tries["n"] == 7 and len(quick_waits) == 6 and ark.patches == []
    assert not result.get("deck_written") and result.get("backup_deleted") is True, result
    assert len(ark.deleted) == 1 and ark.deleted[0] != 42 and 42 in ark.decks
    assert not h.app.state.gateway.decks.client.pacer.paused()  # no pause for everyone
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["next_step"] == f"{ark_mod.PATIENCE_SPENT} {NOTHING_CHANGED}", got["next_step"]
    page = await _review_page(h, p["proposal_id"])
    assert (
        f"{ark_mod.PATIENCE_SPENT} Nothing was changed on Archidekt. The proposal is still pending, so "
        "you can apply it again later."
    ) in page
    assert "paused after repeated failures" not in page and "new proposal" not in page


async def test_an_apply_that_runs_out_of_patience_partway_keeps_its_backup(
    stack: Stack, quick_waits: list[float]
) -> None:
    """The first card write landed, then Archidekt kept answering 429: the deck is partly
    changed, so the backup copy is kept and the message says how far it got."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    _every_patch_429(ark, after=1)
    structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    row = await _finished(h, p["proposal_id"])
    result = row["result"]
    assert row["state"] == "failed" and result["error"] == "rate_limited", result
    assert result["detail"].startswith(ark_mod.PATIENCE_SPENT + " 1 of 3 deck row changes had already")
    assert result["detail"].endswith("Try the rest again later with a new proposal.")
    assert result.get("backup_deck_id") and not result.get("backup_deleted") and ark.deleted == []
    page = await _review_page(h, p["proposal_id"])
    assert "1 of 3 changes reached Archidekt first, so the deck is partly changed. Check the deck." in page


async def test_a_breaker_refusal_on_the_first_write_counts_as_nothing_written(
    stack: Stack, whole_apply: None
) -> None:
    """The gateway-wide breaker (opened by other failures) refuses the apply's first
    card write before it is sent. Nothing changed, so the backup copy is deleted again."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    client = h.app.state.gateway.decks.client
    real = client.modify_card

    async def breaker_opens_first(*args, **kwargs):
        client.pacer._open_until = ark_mod.time.monotonic() + 60
        try:
            return await real(*args, **kwargs)
        finally:
            client.pacer._open_until = 0.0  # so the clean-up after the failure may still run

    client.modify_card = breaker_opens_first
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "unavailable", out
    result = h.db.get_proposal(p["proposal_id"], "user-1")["result"]
    assert ark.patches == [] and not result.get("deck_written"), result
    assert result.get("backup_deleted") is True and len(ark.deleted) == 1 and ark.deleted[0] != 42


async def test_a_details_step_whose_re_read_fails_after_the_write_keeps_the_backup(
    stack: Stack, whole_apply: None
) -> None:
    """The tag is added on Archidekt, then the re-read just after is refused (403). The
    deck changed, so the backup copy must be kept, not deleted as if nothing was written."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"add_tags": ["budget"]}})
    )
    assert p["ok"], p
    orig = ark.transport.handler
    tagged = {"x": False}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/api/decks/tagRelations/":
            tagged["x"] = True
        elif tagged["x"] and req.method == "GET" and req.url.path == "/api/decks/42/":
            return httpx.Response(403, json={"detail": "You do not have permission."})
        return orig(req)

    ark.transport.handler = handler
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    if out.get("error") == "browser_required":  # the member's mode asks them: apply as they would
        b = Browser(h)
        await b.login()
        csrf = await b.csrf("/proposals")
        r = await b.http.post(
            f"/api/v1/proposals/{p['proposal_id']}/apply",
            headers={"X-CSRF-Token": csrf},
            json={"version": p["version"]},
        )
        out = r.json()
        await b.aclose()
    assert out.get("ok") is False and out.get("error") == "forbidden", out
    assert [t["name"] for t in ark.deck_tags[42]] == ["budget"]  # the write landed
    result = h.db.get_proposal(p["proposal_id"], "user-1")["result"]
    assert result.get("deck_written") is True, result
    assert result.get("backup_deck_id") and not result.get("backup_deleted") and ark.deleted == []


async def test_dropping_an_unused_backup_never_deletes_the_deck_itself(
    stack: Stack, monkeypatch, whole_apply: None
) -> None:
    """If the copy's id were ever the source deck's own, the clean-up after a failed
    apply must not delete the real deck."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    svc = h.app.state.gateway.decks
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))

    async def copy_answers_the_source_id(sub, deck, snapshot_id, *, reason):
        h.db.set_snapshot_backup(snapshot_id, backup_deck_id=deck.id, backup_url="")
        return {"backup_deck_id": deck.id, "backup_url": ""}

    monkeypatch.setattr(svc, "_backup_copy", copy_answers_the_source_id)
    orig = ark.transport.handler

    def refuse(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and "modifyCards" in req.url.path:
            return httpx.Response(403, json={"detail": "You do not have permission."})
        return orig(req)

    ark.transport.handler = refuse
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert out["ok"] is False and out["error"] == "forbidden", out
    assert ark.deleted == [] and 42 in ark.decks
    result = h.db.get_proposal(p["proposal_id"], "user-1")["result"]
    assert not result.get("backup_deleted"), result


def _tag_post_then_deck_read_429(ark: FakeArchidekt) -> None:
    """The tag is added on Archidekt, then every read of deck 42 after it meets a 429."""
    orig = ark.transport.handler
    tagged = {"x": False}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/api/decks/tagRelations/":
            tagged["x"] = True
        elif tagged["x"] and req.method == "GET" and req.url.path == "/api/decks/42/":
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "0"})
        return orig(req)

    ark.transport.handler = handler


def _every_tag_post_429(ark: FakeArchidekt) -> None:
    orig = ark.transport.handler

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/api/decks/tagRelations/":
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "0"})
        return orig(req)

    ark.transport.handler = handler


PARTLY = (
    "Part of this change had already reached Archidekt, so check the deck. Try the rest again "
    "later with a new proposal."
)


async def test_a_stop_after_a_tag_was_added_never_tells_the_assistant_nothing_changed(
    stack: Stack, quick_waits: list[float]
) -> None:
    """The tag reached Archidekt, then the re-read kept meeting 429s. The stored
    message, get_proposal's next_step and the review page all say part of it reached Archidekt;
    none says "Nothing was changed", and a tags-only proposal never claims other details."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"add_tags": ["budget"]}})
    )
    _tag_post_then_deck_read_429(ark)
    await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    row = await _finished(h, p["proposal_id"])
    result = row["result"]
    assert row["state"] == "failed" and result["error"] == "rate_limited", result
    assert [t["name"] for t in ark.deck_tags[42]] == ["budget"] and result.get("deck_written") is True
    assert result["detail"] == (
        f"{ark_mod.PATIENCE_SPENT} A snapshot ({result['snapshot_id']}) of the deck before the change "
        f"was kept. {PARTLY}"
    ), result["detail"]
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["next_step"] == f"{ark_mod.PATIENCE_SPENT} {PARTLY}", got["next_step"]
    page = await _review_page(h, p["proposal_id"])
    assert f"{ark_mod.PATIENCE_SPENT} Part of this change reached Archidekt first. Check the deck." in page


async def test_a_stop_after_the_new_deck_was_made_says_so_to_the_assistant(
    stack: Stack, quick_waits: list[float]
) -> None:
    """The new deck was created, then every card write met a 429. get_proposal's
    next_step says the deck was made, as the stored message does."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    cards = [{"name": "Forest", "quantity": 3}, {"name": "Island", "quantity": 2}]
    p = structured(await call(h, token, "propose_new_deck", {"name": "New", "cards": cards}))
    assert p["ok"], p
    _every_patch_429(ark)
    await press_approve(h, token, p["proposal_id"])
    row = await _finished(h, p["proposal_id"])
    result = row["result"]
    assert row["state"] == "failed" and result["error"] == "rate_limited" and result["deck_id"], result
    made = (
        f"The new deck {result['deck_id']} was created on Archidekt, but no cards were added. Try the "
        "rest again later with a new proposal."
    )
    assert result["detail"] == f"{ark_mod.PATIENCE_SPENT} {made}", result["detail"]
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["next_step"] == f"{ark_mod.PATIENCE_SPENT} {made}", got["next_step"]


async def test_a_tags_only_stop_before_anything_was_written_claims_no_other_details(
    stack: Stack, quick_waits: list[float]
) -> None:
    """A tags-only proposal whose tag write keeps meeting 429s wrote nothing. The
    message says nothing changed and never that "the deck's other details were applied"."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(
        await call(h, token, "propose_deck_details", {"deck_id": "42", "details": {"add_tags": ["budget"]}})
    )
    _every_tag_post_429(ark)
    await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    row = await _finished(h, p["proposal_id"])
    result = row["result"]
    assert row["state"] == "pending" and result["error"] == "rate_limited", result
    assert not ark.deck_tags.get(42) and not result.get("deck_written"), result
    assert result["detail"] == (
        f"{ark_mod.PATIENCE_SPENT} A snapshot ({result['snapshot_id']}) of the deck before the change "
        f"was kept. {NOTHING_CHANGED}"
    ), result["detail"]
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["next_step"] == f"{ark_mod.PATIENCE_SPENT} {NOTHING_CHANGED}", got["next_step"]


async def test_a_details_stop_after_other_details_were_written_still_says_they_were(
    stack: Stack, quick_waits: list[float]
) -> None:
    """The other side: the new name was written, then the tag kept meeting 429s.
    "The deck's other details were applied" is true here and stays, with what reached Archidekt."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    details = {"name": "Renamed", "add_tags": ["budget"]}
    p = structured(await call(h, token, "propose_deck_details", {"deck_id": "42", "details": details}))
    assert p["ok"], p
    _every_tag_post_429(ark)
    await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]})
    row = await _finished(h, p["proposal_id"])
    result = row["result"]
    assert ark.decks[42]["name"] == "Renamed" and result.get("deck_written") is True, result
    assert result["detail"] == (
        f"{ark_mod.PATIENCE_SPENT} The deck's other details were applied. A snapshot "
        f"({result['snapshot_id']}) of the deck before the change was kept. {PARTLY}"
    ), result["detail"]
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["next_step"] == f"{ark_mod.PATIENCE_SPENT} {PARTLY}", got["next_step"]


LATER = (
    "Nothing was changed on Archidekt. The unused backup copy will be deleted from the backup folder "
    "by itself once Archidekt allows it. The proposal is still pending, so it can be approved again "
    "later."
)


async def test_a_stop_whose_backup_copy_could_not_be_deleted_queues_its_delete(
    stack: Stack, quick_waits: list[float]
) -> None:
    """Archidekt asks for 30 s, so the clean-up's delete of the
    unused backup would be refused too. It is not sent but queued for later, and the assistant's
    message, next_step and the review page say the copy will be deleted by itself. Nothing
    changed, so the proposal is pending again."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    orig = ark.transport.handler
    deletes = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and "modifyCards" in req.url.path:
            return httpx.Response(429, json={"detail": "slow down"}, headers={"Retry-After": "30"})
        if req.method == "DELETE":
            deletes["n"] += 1
        return orig(req)

    ark.transport.handler = handler
    try:
        out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
        row = await _finished(h, p["proposal_id"])
    finally:
        h.app.state.gateway.decks.client.pacer._retry_until = 0
    result = row["result"]
    assert row["state"] == "pending" and result["error"] == "rate_limited", result
    assert result.get("backup_deck_id") and ark.deleted == [] and ark.patches == [], result
    assert deletes["n"] == 0  # the pause was running: the doomed delete was never sent
    assert result["detail"] == f"{ark_mod.PATIENCE_SPENT} {LATER}", result["detail"]
    if out.get("ok") is False:  # answered before the apply moved to the background
        assert out["message"] == result["detail"] and out["state"] == "pending", out
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["next_step"] == f"{ark_mod.PATIENCE_SPENT} {LATER}", got["next_step"]
    queued = h.db.queued_backup_deletes()
    assert [(q["snapshot_id"], q["backup_deck_id"], q["attempts"]) for q in queued] == [
        (result["snapshot_id"], str(result["backup_deck_id"]), 0)
    ], queued
    page = await _review_page(h, p["proposal_id"])
    assert (
        f"{ark_mod.PATIENCE_SPENT} Nothing was changed on Archidekt. The unused backup copy will be "
        "deleted from the backup folder by itself once Archidekt allows it. The proposal is still "
        "pending, so you can apply it again later."
    ) in page


# -- a clone stopped after its copy was made ---------------------------------------------------------
def _clone_copied_then(ark: FakeArchidekt, answer: httpx.Response) -> dict[str, bool]:
    """The clone's POST /decks/copy/ goes through; every deck read after it gets ``answer``."""
    orig = ark.transport.handler
    state = {"copied": False}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "POST" and req.url.path == "/api/decks/copy/":
            state["copied"] = True
            return orig(req)
        if state["copied"] and req.method == "GET" and "/decks/" in req.url.path:
            return answer
        return orig(req)

    ark.transport.handler = handler
    state["orig"] = orig  # type: ignore[assignment]
    return state


def _copy_made(copy_id: str) -> str:
    return (
        f"The copy {copy_id} was made on Archidekt, but the gateway could not check it. Look at it on "
        "Archidekt before asking for another copy."
    )


@pytest.mark.usefixtures("quick_waits", "whole_apply")
async def test_a_clone_stopped_after_its_copy_was_made_stays_failed_and_is_never_copied_twice(
    stack: Stack,
) -> None:
    """The copy was made, then the read-back met a 429 longer
    than the apply may wait. Something changed on Archidekt, so the proposal stays failed (not
    pending), records the new deck, says the copy was made (never "no cards were added": a copy
    holds the cards), and a second approval makes no second copy."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_clone_deck", {"deck_id": "42", "name": "Paced copy"}))
    assert p["ok"], p
    before = set(ark.decks)
    state = _clone_copied_then(
        ark, httpx.Response(429, json={"detail": "slow"}, headers={"Retry-After": "3600"})
    )
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    row = await _finished(h, p["proposal_id"])
    made = [d for d in set(ark.decks) - before if ark.decks[d]["name"] == "Paced copy"]
    assert len(made) == 1, made
    assert row["state"] == "failed", ("copy made but proposal back to pending", row["result"])
    result = row["result"]
    assert result["error"] == "rate_limited" and result["deck_id"] == str(made[0]), result
    assert result["detail"] == f"{ark_mod.PATIENCE_SPENT} {_copy_made(str(made[0]))}", result["detail"]
    assert out["ok"] is False and out.get("state") != "pending", out
    got = structured(await call(h, token, "get_proposal", {"proposal_id": p["proposal_id"]}))
    assert got["next_step"] == f"{ark_mod.PATIENCE_SPENT} {_copy_made(str(made[0]))}", got["next_step"]
    ark.transport.handler = state["orig"]
    h.app.state.gateway.decks.client.pacer._retry_until = 0
    again = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    assert again["ok"] is False, again
    assert [d for d in set(ark.decks) - before if ark.decks[d]["name"] == "Paced copy"] == made


@pytest.mark.usefixtures("whole_apply")
async def test_a_clone_whose_read_back_fails_says_the_copy_was_made(stack: Stack) -> None:
    """A clone whose read-back meets a 5xx after the copy was made fails with the new
    deck recorded, and its message says the copy was made."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_clone_deck", {"deck_id": "42", "name": "Down copy"}))
    before = set(ark.decks)
    _clone_copied_then(ark, httpx.Response(503, json={"detail": "down"}))
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    row = await _finished(h, p["proposal_id"])
    [made] = [d for d in set(ark.decks) - before if ark.decks[d]["name"] == "Down copy"]
    assert row["state"] == "failed" and row["result"]["deck_id"] == str(made), row["result"]
    assert row["result"]["detail"] == out["message"], row["result"]
    assert out["message"].startswith("Archidekt returned HTTP 503"), out
    assert out["message"].endswith(f" {_copy_made(str(made))}"), out
    assert "no cards were added" not in out["message"]


def _second_patch(ark: FakeArchidekt, answer) -> None:
    """The first card write goes through; the second gets ``answer`` (a response, or an exception
    class raised inside the gateway)."""
    orig = ark.transport.handler

    def handler(req: httpx.Request) -> httpx.Response:
        if req.method == "PATCH" and "modifyCards" in req.url.path and len(ark.patches) >= 1:
            if isinstance(answer, httpx.Response):
                return answer
            raise answer("unexpected")
        return orig(req)

    ark.transport.handler = handler


@pytest.mark.usefixtures("whole_apply")
async def test_a_partly_landed_failure_stores_the_message_its_caller_got(stack: Stack) -> None:
    """The stored detail (read by the chat card after a slow Approve and by the review page) is the
    same message the apply's caller got, with how much had been sent."""
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    _second_patch(ark, httpx.Response(503, json={"detail": "down"}))
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    row = await _finished(h, p["proposal_id"])
    assert row["state"] == "failed" and row["result"]["sent_entries"] == 1, row["result"]
    assert "1 of 3 deck row changes had already been sent" in out["message"], out
    # the reason ends its own sentence before the note ("... HTTP 503. 1 of 3 ...")
    assert out["message"].startswith("Archidekt returned HTTP 503. 1 of 3 "), out["message"]
    assert row["result"]["detail"] == out["message"], (row["result"], out)


@pytest.mark.usefixtures("whole_apply")
async def test_an_unexpected_failure_stores_the_message_its_caller_got(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    _second_patch(ark, RuntimeError)
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    row = await _finished(h, p["proposal_id"])
    assert row["state"] == "failed" and row["result"]["error"] == "internal", row["result"]
    assert out["message"].startswith("Applying failed unexpectedly. 1 of 3 deck row changes"), out
    assert row["result"]["detail"] == out["message"], (row["result"], out)
