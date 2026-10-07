"""Removing a member from the group in Authentik cuts them off on their next request.

Authentik itself keeps honouring a removed member's refresh token and reports their tokens active,
so this proves the gateway's own live check (membership.py) against the real provider."""

from __future__ import annotations

import time
from urllib.parse import urlencode

import httpx

from .conftest import AUTH_URL, GEN, MCP_URL, PUBLIC_URL, Env
from .test_03_tokens import LOOPBACK, browser_authorize, exchange, mcp_tools_list, pkce, register

USER = "bob-test"
CHECK_TTL = 5  # MTG_MEMBERSHIP_CHECK_TTL default


def authentik_api(env: Env) -> httpx.Client:
    token = ""
    for line in (GEN / "ak.env").read_text().splitlines():
        if line.startswith("E2E_AK_TOKEN="):
            token = line.split("=", 1)[1].strip()
    return httpx.Client(
        base_url=f"{AUTH_URL}/api/v3",
        headers={"Authorization": f"Bearer {token}"},
        verify=env.ca_file,
        timeout=30,
    )


def _one(ak: httpx.Client, path: str, **params: str) -> dict:
    rows = [
        r
        for r in ak.get(path, params=params).json()["results"]
        if all(r.get(k) == v for k, v in params.items())
    ]
    assert len(rows) == 1, (path, params, rows)
    return rows[0]


async def test_group_removal_in_authentik_cuts_off_the_member(env: Env, http: httpx.Client):
    client = register(http)
    verifier, challenge = pkce()
    q = {
        "client_id": client["client_id"],
        "redirect_uri": LOOPBACK,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "live-1",
        "resource": MCP_URL,
    }
    params = await browser_authorize(env, USER, f"{PUBLIC_URL}/authorize?{urlencode(q)}")
    r = exchange(
        http,
        client,
        grant_type="authorization_code",
        code=params["code"],
        redirect_uri=LOOPBACK,
        code_verifier=verifier,
    )
    assert r.status_code == 200, r.text
    tokens = r.json()
    assert mcp_tools_list(http, tokens["access_token"]).status_code == 200

    ak = authentik_api(env)
    group = _one(ak, "/core/groups/", name=env.required_group)
    user = _one(ak, "/core/users/", username=USER)
    removed = ak.post(f"/core/groups/{group['pk']}/remove_user/", json={"pk": user["pk"]})
    assert removed.status_code in (200, 204), removed.text
    try:
        time.sleep(CHECK_TTL + 1)  # past the gateway's short membership cache
        assert mcp_tools_list(http, tokens["access_token"]).status_code == 401
        r = exchange(http, client, grant_type="refresh_token", refresh_token=tokens["refresh_token"])
        assert r.status_code == 400 and r.json()["error"] == "invalid_grant", r.text
    finally:
        ak.post(f"/core/groups/{group['pk']}/add_user/", json={"pk": user["pk"]})
