"""A member's Apply on the review page answers within a short wait (BROWSER_APPLY_WAIT_SECONDS)
and shows the progress, instead of holding the press for the assistant's 20 s window; the
assistant's apply_proposal keeps the long wait. A second press while it runs sends nothing more,
and the page then reaches the outcome."""

from __future__ import annotations

import asyncio
import time

import pytest

from mtg_gateway import decks as decks_mod

from .conftest import AUTO, Stack, StackOptions
from .test_background_apply import THREE, _hold, _until
from .test_decks_and_proxy import Browser, call, linked_user, structured


@pytest.fixture
def stack_options() -> StackOptions:
    """The AUTO stack (the assistant may apply), with the real waits."""
    return AUTO


def test_the_review_page_waits_less_than_the_assistant() -> None:
    assert decks_mod.BROWSER_APPLY_WAIT_SECONDS == 3.0
    assert decks_mod.APPLY_WAIT_SECONDS == 20.0


async def test_a_held_apply_on_the_review_page_answers_at_once_with_progress(stack: Stack) -> None:
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    pid = p["proposal_id"]
    url = f"/proposals/{pid}"
    release = _hold(ark, lambda r: r.method == "PATCH" and len(ark.patches) >= 1)  # Archidekt is slow
    b = Browser(h)
    try:
        await b.login()
        form = {**await b.review_form(url), "action": "apply"}
        t0 = time.monotonic()
        r = await b.http.post(url, data=form)
        took = time.monotonic() - t0
        assert r.status_code == 303 and r.headers["location"] == f"{url}?ok=applying", r.headers
        assert took < 5, f"the press waited {took:.1f} s"
        page = (await b.http.get(r.headers["location"])).text
        assert "sent 1 of 3 changes" in page and "data-applied-live" in page
        # Back to the form, or a refresh that sends it again, while it runs: nothing more is sent
        again = await b.http.post(url, data=form)
        assert again.status_code == 303 and len(ark.patches) == 1, again.headers
        assert h.db.get_proposal(pid, "user-1")["state"] == "applying"
        release.set()
        await _until(lambda: h.db.get_proposal(pid, "user-1")["state"] == "applied")
        # the same address (?ok=applying), asked again by the page's script, now says it is done
        done = (await b.http.get(f"{url}?ok=applying")).text
        assert "Done. Archidekt matches this proposal." in done
        assert "Applying." not in done and "data-applied-live" not in done
        assert "Applied. Archidekt now matches this proposal." in done
    finally:
        release.set()
        await b.aclose()
    assert len(ark.patches) == 3  # each change once


async def test_the_assistant_still_waits_the_long_window(stack: Stack, monkeypatch) -> None:
    """apply_proposal over MCP waits APPLY_WAIT_SECONDS, not the review page's short wait: an
    apply that ends after the short wait answers "applied" to the assistant."""
    monkeypatch.setattr(decks_mod, "BROWSER_APPLY_WAIT_SECONDS", 0.05)
    h, ark = stack.h, stack.ark
    token = await linked_user(stack)
    p = structured(await call(h, token, "propose_deck_changes", {"deck_id": "42", "changes": THREE}))
    release = _hold(ark, lambda r: r.method == "PATCH" and len(ark.patches) >= 1)

    async def let_go() -> None:
        await asyncio.sleep(0.3)
        release.set()

    go = asyncio.create_task(let_go())
    out = structured(await call(h, token, "apply_proposal", {"proposal_id": p["proposal_id"]}))
    await go
    assert out["ok"] and out["state"] == "applied", out
