"""A refresh whose answer never reached the app (a phone network blip), sent again (0.7.46 audit
PA-2): the retry of the just-rotated refresh token signed the member out of that assistant,
because it looked like a stolen token used twice and revoked the whole sign-in.

The gateway now honours that one retry, briefly and narrowly: the same app, the same sign-in,
within RETRY_GRACE_SECONDS of the rotation, and only while the pair the rotation issued has
never been used (the app never got it). The unused pair is retired and a new one issued in the
same sign-in. Anything else is still reuse and revokes the sign-in, as before."""

from __future__ import annotations

import pytest

from mtg_gateway import auth_provider
from tests.conftest import Harness


async def _refresh(gw: Harness, client: dict, refresh_token: str):
    return await gw.token(client, grant_type="refresh_token", refresh_token=refresh_token)


async def test_a_retried_refresh_whose_answer_was_lost_keeps_the_member_signed_in(gw: Harness) -> None:
    client = await gw.register()
    t1 = await gw.tokens_for(client)
    lost = await _refresh(gw, client, t1["refresh_token"])  # its answer never reaches the app
    assert lost.status_code == 200
    t2 = lost.json()
    again = await _refresh(gw, client, t1["refresh_token"])
    assert again.status_code == 200, again.text
    t3 = again.json()
    assert t3["access_token"] not in (t1["access_token"], t2["access_token"])
    assert (await gw.mcp(t3["access_token"], "tools/list")).status_code == 200
    # the pair the app never got is retired; the sign-in carries on with the new one
    assert (await gw.mcp(t2["access_token"], "tools/list")).status_code == 401
    t4 = await _refresh(gw, client, t3["refresh_token"])
    assert t4.status_code == 200, t4.text
    assert (await gw.mcp(t4.json()["access_token"], "tools/list")).status_code == 200


async def test_a_retry_after_the_new_pair_was_used_is_still_reuse(gw: Harness) -> None:
    """The app used the pair the rotation gave it, so it got that answer: the old token presented
    again is someone else's, and the whole sign-in is revoked as before."""
    client = await gw.register()
    t1 = await gw.tokens_for(client)
    t2 = (await _refresh(gw, client, t1["refresh_token"])).json()
    assert (await gw.mcp(t2["access_token"], "tools/list")).status_code == 200
    again = await _refresh(gw, client, t1["refresh_token"])
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert (await gw.mcp(t2["access_token"], "tools/list")).status_code == 401


async def test_a_retry_after_the_window_is_still_reuse(gw: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    client = await gw.register()
    t1 = await gw.tokens_for(client)
    t2 = (await _refresh(gw, client, t1["refresh_token"])).json()
    now = auth_provider.time.time()
    monkeypatch.setattr(auth_provider.time, "time", lambda: now + auth_provider.RETRY_GRACE_SECONDS + 1)
    again = await _refresh(gw, client, t1["refresh_token"])
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    monkeypatch.undo()
    assert (await gw.mcp(t2["access_token"], "tools/list")).status_code == 401  # the sign-in is revoked


async def test_a_retry_after_the_new_refresh_token_was_used_is_still_reuse(gw: Harness) -> None:
    client = await gw.register()
    t1 = await gw.tokens_for(client)
    t2 = (await _refresh(gw, client, t1["refresh_token"])).json()
    t3 = (await _refresh(gw, client, t2["refresh_token"])).json()
    again = await _refresh(gw, client, t1["refresh_token"])
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert (await gw.mcp(t3["access_token"], "tools/list")).status_code == 401


async def test_another_app_cannot_use_the_retry(gw: Harness) -> None:
    client = await gw.register()
    other = await gw.register()
    t1 = await gw.tokens_for(client)
    t2 = (await _refresh(gw, client, t1["refresh_token"])).json()
    stolen = await _refresh(gw, other, t1["refresh_token"])
    assert stolen.status_code == 400 and stolen.json()["error"] == "invalid_grant"
    # the app's own sign-in is untouched by another app's attempt
    assert (await gw.mcp(t2["access_token"], "tools/list")).status_code == 200


async def test_a_signed_out_sign_in_is_not_brought_back_by_a_retry(gw: Harness) -> None:
    client = await gw.register()
    t1 = await gw.tokens_for(client)
    t2 = (await _refresh(gw, client, t1["refresh_token"])).json()
    row = gw.db.get_token(t2["refresh_token"], "refresh")
    gw.db.revoke_family(row["family"])  # signed out (or disconnected) meanwhile
    again = await _refresh(gw, client, t1["refresh_token"])
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"


async def test_the_retry_answers_once_per_answer_and_then_is_reuse(gw: Harness) -> None:
    """Each retry retires the pair before it, so a stolen copy of the old token that comes in
    after the app moved on with the newest pair is reuse."""
    client = await gw.register()
    t1 = await gw.tokens_for(client)
    await _refresh(gw, client, t1["refresh_token"])  # lost
    t3 = (await _refresh(gw, client, t1["refresh_token"])).json()  # the retry
    assert (await gw.mcp(t3["access_token"], "tools/list")).status_code == 200
    again = await _refresh(gw, client, t1["refresh_token"])
    assert again.status_code == 400 and again.json()["error"] == "invalid_grant"
    assert (await gw.mcp(t3["access_token"], "tools/list")).status_code == 401
