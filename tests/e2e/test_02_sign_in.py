"""The whole client flow against real Authentik, for four kinds of user."""

from __future__ import annotations

import pytest

from .conftest import PUBLIC_URL, LoginNotCompleted


async def test_two_users_get_their_own_identities(mcp_client):
    alice = mcp_client("alice-test", client_name="Claude-like client")
    bob = mcp_client("bob-test", client_name="ChatGPT-like client")
    me_ben = await alice.whoami()
    me_friend = await bob.whoami()

    # Each person gets their own identity; the assistant is told only the display name (minimum
    # disclosure: no email, groups, subject, client or scopes).
    assert me_ben == {
        "signed_in": True,
        "name": "Alice Test",
        "gateway_version": me_ben["gateway_version"],
        "account_page": f"{PUBLIC_URL}/account",
    }
    assert me_friend["name"] == "Bob Test"
    # Each client registered itself (dynamic registration) and got its own client_id.
    assert alice.storage.client_info and bob.storage.client_info
    assert alice.storage.client_info.client_id != bob.storage.client_info.client_id
    assert alice.storage.client_info.client_secret
    assert alice.storage.tokens and alice.storage.tokens.refresh_token
    assert (alice.storage.tokens.expires_in or 0) <= 3600
    # What Authentik asserted (email, groups) is recorded from the gateway's own records by
    # test_04's backup check, in .generated/identity-evidence.json.


async def test_same_user_from_two_clients_is_the_same_person(mcp_client):
    ca = mcp_client("alice-test", client_name="client A")
    cb = mcp_client("alice-test", client_name="client B")
    a, b = await ca.whoami(), await cb.whoami()
    assert a["name"] == b["name"] == "Alice Test"
    assert ca.storage.client_info.client_id != cb.storage.client_info.client_id


async def test_user_outside_required_group_is_refused_by_the_gateway(mcp_client):
    """guest-test passes Authentik's application policy but is not in MTG_REQUIRED_GROUP."""
    c = mcp_client("guest-test")
    with pytest.raises(LoginNotCompleted) as exc:
        await c.whoami()
    login = exc.value.login
    assert login.callback is None
    assert login.final_url.startswith("https://mtg.e2e.test/auth/callback")
    assert "not in the group that may use this service" in login.page_text
    assert c.storage.tokens is None


async def test_user_not_bound_to_the_application_is_refused_by_authentik(mcp_client):
    """outsider-test has an Authentik account but no binding to the application."""
    c = mcp_client("outsider-test")
    with pytest.raises(LoginNotCompleted) as exc:
        await c.whoami()
    login = exc.value.login
    assert login.callback is None
    assert login.final_url.startswith("https://auth.e2e.test/"), login.final_url
    assert c.storage.tokens is None
