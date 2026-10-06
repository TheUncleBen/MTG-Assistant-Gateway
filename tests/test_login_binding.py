"""A login can only be approved and finished by the browser that started it.

Each test plays an attacker who starts a login in their own browser (approving their own consent
page) and then tries to have a victim's browser finish it, which would hand the attacker a code
for the victim's account.
"""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

import httpx

from mtg_gateway.oidc import pkce_pair

from .conftest import GATEWAY, Harness

EVIL = "https://evil.test/cb"


def _browser(gw: Harness) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.app), base_url=GATEWAY)


def _authorize(client_id: str, state: str = "x", scope: str = "mtg") -> dict[str, str]:
    _verifier, challenge = pkce_pair()
    return {
        "client_id": client_id,
        "redirect_uri": EVIL,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
        "scope": scope,
    }


async def _attacker_login(gw: Harness, atk: httpx.AsyncClient) -> tuple[str, str, str, str]:
    """(client_id, login id, consent csrf, IdP URL) after the attacker approved their own login."""
    reg = await atk.post(
        "/register",
        json={"redirect_uris": [EVIL], "client_name": "Claude", "token_endpoint_auth_method": "none"},
    )
    client_id = reg.json()["client_id"]
    r = await atk.get("/authorize", params=_authorize(client_id))
    login_id = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    page = await atk.get("/authorize/confirm", params={"state": login_id})
    csrf = re.search(r"name='csrf' value='([0-9a-f]+)'", page.text).group(1)  # type: ignore[union-attr]
    go = await atk.post("/authorize/confirm", data={"state": login_id, "csrf": csrf, "action": "approve"})
    assert go.status_code == 303
    return client_id, login_id, csrf, go.headers["location"]


async def _victim_idp_callback(gw: Harness, idp_url: str) -> str:
    """The victim, signed in at the IdP, is bounced through the attacker's IdP URL silently."""
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=gw.idp.app)) as c:
        cb = (await c.get(idp_url)).headers["location"]
    p = urlparse(cb)
    return f"{p.path}?{p.query}"


def _set_cookies(r: httpx.Response) -> str:
    return ";".join(r.headers.get_list("set-cookie"))


async def test_error_redirect_does_not_plant_the_login_cookie(gw: Harness):
    atk, victim = _browser(gw), _browser(gw)
    client_id, login_id, _csrf, idp_url = await _attacker_login(gw, atk)
    # An /authorize that fails (bad scope) echoes the attacker's state in its redirect.
    r = await victim.get("/authorize", params=_authorize(client_id, state=login_id, scope="bogus"))
    assert r.status_code == 400 and "location" not in r.headers  # shown, never redirected
    assert "mtg_login" not in _set_cookies(r)
    done = await victim.get(await _victim_idp_callback(gw, idp_url))
    assert done.status_code == 403
    assert "code=" not in done.headers.get("location", "")


async def test_login_next_does_not_plant_the_login_cookie(gw: Harness):
    atk, victim = _browser(gw), _browser(gw)
    # The victim is signed in to the gateway's pages, so /login redirects straight to `next`.
    start = await victim.get("/login", params={"next": "/account"})
    assert (await victim.get(await _victim_idp_callback(gw, start.headers["location"]))).status_code == 302
    victim.cookies.delete("__Host-mtg_login")  # a key from an earlier sign-in would not help anyway
    _client_id, login_id, _csrf, idp_url = await _attacker_login(gw, atk)
    r = await victim.get("/login", params={"next": f"/account?state={login_id}"})
    assert r.status_code == 302 and "mtg_login" not in _set_cookies(r)
    done = await victim.get(await _victim_idp_callback(gw, idp_url))
    assert done.status_code == 403


async def test_victim_browser_cannot_approve_the_attackers_login(gw: Harness):
    atk, victim = _browser(gw), _browser(gw)
    await victim.get("/login", params={"next": "/account"})  # the victim has a login key of their own
    _client_id, login_id, csrf, _idp = await _attacker_login(gw, atk)
    # A cross-site form post with the token the attacker read from their own consent page.
    form = {"state": login_id, "csrf": csrf, "action": "approve"}
    post = await victim.post("/authorize/confirm", data=form)
    assert post.status_code == 403 and "location" not in post.headers
    assert (await victim.get("/authorize/confirm", params={"state": login_id})).status_code == 400


async def test_a_refused_callback_does_not_burn_the_login(gw: Harness):
    atk, victim = _browser(gw), _browser(gw)
    client_id, _login_id, _csrf, idp_url = await _attacker_login(gw, atk)
    cb = await _victim_idp_callback(gw, idp_url)
    assert (await victim.get(cb)).status_code == 403
    assert (await atk.get(cb)).status_code == 302  # the browser that started it still can
    assert client_id


async def test_login_cookie_is_host_only_and_two_logins_share_it(gw: Harness):
    client = await gw.register()
    a, _ = await gw.start_login(client)
    assert "__Host-mtg_login=" in _set_cookies(a) and "Path=/;" in _set_cookies(a)
    key = gw.http.cookies.get("__Host-mtg_login")
    b, _ = await gw.start_login(client)
    assert gw.http.cookies.get("__Host-mtg_login") == key  # reused, so the first login still works
    for redirect in (a, b):
        r = await gw.callback(await gw.idp_leg(redirect))
        assert r.status_code == 302 and "code=" in r.headers["location"]


async def test_non_ascii_form_tokens_are_refused_not_crashing(gw: Harness):
    client = await gw.register()
    r, _ = await gw.start_login(client)
    login_id = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    form = {"state": login_id, "csrf": "é", "action": "approve"}
    post = await gw.http.post("/authorize/confirm", data=form)
    assert post.status_code == 403
