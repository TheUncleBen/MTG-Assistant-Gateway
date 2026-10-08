"""A large apply answers "applying" with its progress instead of outliving the caller's timeout,
then carries on; get_proposal, the review page and a second apply call all report it."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from mtg_gateway import decks as decks_mod

from .conftest import FakeIdP, Harness, make_settings, running
from .fake_archidekt import FakeArchidekt
from .test_decks_and_proxy import Browser, Stack, _client, call, linked_user, structured

THREE = [
    {"action": "set_quantity", "card_name": "Forest", "quantity": 3},
    {"action": "remove", "card_name": "Acidic Slime"},
    {"action": "set_quantity", "card_name": "Island", "quantity": 2},
]


@pytest.fixture
async def stack(tmp_path, idp: FakeIdP, monkeypatch):
    monkeypatch.setattr(decks_mod, "APPLY_WAIT_SECONDS", 0.2)
    ark = FakeArchidekt()
    settings = make_settings(
        tmp_path, writes_enabled=True, approval_mode_default="auto", archidekt_base="https://ark.test/api"
    )
    async with running(Harness(settings, idp, archidekt=_client(settings, ark))) as h:
        yield Stack(h, ark)


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
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
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
    r = await b.http.post(url, data={"csrf": await b.csrf(url), "action": "apply"})
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
