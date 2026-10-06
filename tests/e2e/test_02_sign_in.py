"""The whole client flow against real Authentik, for four kinds of user."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from .conftest import LoginNotCompleted

EVIDENCE = Path(__file__).with_name(".generated") / "identity-evidence.json"


async def test_two_users_get_their_own_identities(mcp_client):
    alice = mcp_client("alice-test", client_name="Claude-like client")
    bob = mcp_client("bob-test", client_name="ChatGPT-like client")
    me_ben = await alice.whoami()
    me_friend = await bob.whoami()

    assert me_ben["preferred_username"] == "alice-test"
    assert me_friend["preferred_username"] == "bob-test"
    assert me_ben["sub"] != me_friend["sub"]
    assert me_ben["email"] == "alice-test@e2e.test"
    assert "MTG Assistant Gateway Users" in me_ben["groups"]
    # Each client registered itself (dynamic registration) and got its own client_id.
    assert me_ben["client_id"] != me_friend["client_id"]
    assert alice.storage.client_info and alice.storage.client_info.client_secret
    assert alice.storage.tokens and alice.storage.tokens.refresh_token
    assert me_ben["token_expires_in_seconds"] <= 3600
    # Nothing in the identity came from the client: it is what Authentik asserted.
    assert me_ben["name"] == "Alice Test"
    # Keep what this Authentik version asserted, for comparing versions run by run (no tokens).
    EVIDENCE.parent.mkdir(exist_ok=True)
    EVIDENCE.write_text(json.dumps({"alice-test": me_ben, "bob-test": me_friend}, indent=2, sort_keys=True))


async def test_same_user_from_two_clients_is_the_same_person(mcp_client):
    a = await mcp_client("alice-test", client_name="client A").whoami()
    b = await mcp_client("alice-test", client_name="client B").whoami()
    assert a["sub"] == b["sub"]
    assert a["client_id"] != b["client_id"]


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
