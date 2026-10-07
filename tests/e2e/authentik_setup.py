"""Configure a fresh Authentik the way docs/DEPLOY.md step 3 tells the owner to.

Everything here goes through Authentik's REST API with the bootstrap token, so
the result is exactly what the admin UI click-path produces: one confidential
OAuth2/OpenID provider with a strict redirect URI, one application with slug
``mtg-gateway`` bound to a group, plus test groups and users.

Usage: authentik_setup.py <authentik base url> <token> <gateway public url> <out.json>
Prints nothing secret; the client secret is written only to <out.json>.
"""

from __future__ import annotations

import base64
import json
import re
import secrets
import struct
import sys
import time
import zlib
from pathlib import Path

import httpx

ALLOWED_GROUP = "MTG Assistant Gateway Users"  # bound to the application AND MTG_REQUIRED_GROUP
GUEST_GROUP = "MTG Assistant Gateway Guests"  # bound to the application but NOT MTG_REQUIRED_GROUP
PICTURE_USER = "alice-test"  # has an uploaded profile picture (docs/IDP-AUTHENTIK.md "Profile pictures")
DOCS = Path(__file__).resolve().parents[2] / "docs" / "IDP-AUTHENTIK.md"


def picture_mapping_expression() -> str:
    """The optional scope mapping exactly as the guide prints it, so the tests prove the docs."""
    m = re.search(
        r"<!-- uploaded-picture-mapping:.*?-->\s*```python\n(.*?)```\s*<!-- /uploaded-picture-mapping -->",
        DOCS.read_text(encoding="utf-8"),
        re.S,
    )
    if not m:
        raise SystemExit(f"the uploaded-picture mapping is missing from {DOCS}")
    return m.group(1)


def small_png() -> bytes:
    """An 8x8 orange PNG, built here so no image file is needed."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    rows = b"".join(b"\x00" + b"\xd9\x77\x06" * 8 for _ in range(8))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 8, 8, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


USERS = {
    # username: (display name, groups)
    "alice-test": ("Alice Test", [ALLOWED_GROUP]),
    "bob-test": ("Bob Test", [ALLOWED_GROUP]),
    "guest-test": ("Guest Test", [GUEST_GROUP]),
    "outsider-test": ("Outsider Test", []),
}


READY_TIMEOUT = 300  # seconds to wait for Authentik's worker to apply the default blueprints

# The default blueprints this setup and the suite depend on: sign-in, OAuth authorization,
# provider sign-out and session invalidation. Paths are stable across 2025.6 and 2026.2.
REQUIRED_BLUEPRINTS = (
    "default/flow-default-authentication-flow.yaml",
    "default/flow-default-provider-authorization-implicit-consent.yaml",
    "default/flow-default-provider-invalidation.yaml",
    "default/flow-default-invalidation-flow.yaml",
)


def wait_for_blueprints(ak: AK) -> None:
    """Block until the worker reports the blueprints the suite needs as applied.

    The API answers long before the worker has applied the default blueprints, and
    setup that runs in that window fails in version-specific ways (a 400 from a filter
    on 2026.2.2, flows that are not there yet). A status of error or warning is treated
    as not applied yet, because the worker retries; only the timeout ends the run, naming
    the last status of each missing blueprint.
    """
    started = time.monotonic()
    while True:
        try:
            instances = ak.get("/managed/blueprints/")["results"]
        except httpx.HTTPError as exc:
            instances, note = [], f"blueprint list unavailable: {exc}"
        else:
            note = f"{len(instances)} blueprint instances known"
        by_path = {b.get("path"): b.get("status") for b in instances}
        pending = {path: by_path.get(path, "not listed") for path in REQUIRED_BLUEPRINTS}
        pending = {path: status for path, status in pending.items() if status != "successful"}
        if not pending:
            print(f"authentik default blueprints applied ({time.monotonic() - started:.0f}s; {note})")
            return
        # "error" and "warning" are not final: the worker re-applies the default blueprints
        # after migrations and a first attempt can fail while that is still under way (seen on
        # 2026.2.2 for the authentication flow). Keep waiting; the timeout reports the last status.
        if time.monotonic() - started > READY_TIMEOUT:
            raise SystemExit(
                f"Authentik did not apply its default blueprints within {READY_TIMEOUT}s: {pending} "
                f"({note}); is the authentik-worker service running?"
            )
        time.sleep(3)


class AK:
    def __init__(self, base: str, token: str, verify: bool | str = True):
        self.http = httpx.Client(
            base_url=f"{base.rstrip('/')}/api/v3",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=60,
            verify=verify,
        )

    def get(self, path: str, **params: str) -> dict:
        r = self.http.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def one(self, path: str, **params: str) -> dict:
        res = self.get(path, **params)["results"]
        if len(res) != 1:
            raise SystemExit(f"expected one result for {path} {params}, got {len(res)}")
        return res[0]

    def find_or_create(self, path: str, lookup: dict[str, str], body: dict) -> dict:
        # List filters are substring searches on some endpoints, so require an exact match.
        res = [
            r for r in self.get(path, **lookup)["results"] if all(r.get(k) == v for k, v in lookup.items())
        ]
        if res:
            return res[0]
        r = self.http.post(path, json=body)
        if r.status_code >= 400:
            raise SystemExit(f"POST {path} failed {r.status_code}: {r.text}")
        return r.json()


def wait_ready(base: str, verify: bool | str, timeout: int = 600) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = httpx.get(f"{base.rstrip('/')}/-/health/ready/", verify=verify, timeout=10)
            if r.status_code < 400:
                return
        except httpx.HTTPError:
            pass
        time.sleep(3)
    raise SystemExit("Authentik did not become ready")


def main() -> None:
    base, token, gateway_url, out = sys.argv[1:5]
    verify: bool | str = sys.argv[5] if len(sys.argv) > 5 else True
    wait_ready(base, verify)
    ak = AK(base, token, verify)

    # The bootstrap token exists only after the worker applied the blueprints.
    for _ in range(100):
        try:
            ak.get("/core/users/me/")
            break
        except httpx.HTTPStatusError:
            time.sleep(3)
    else:
        raise SystemExit("bootstrap API token never became valid")

    wait_for_blueprints(ak)

    # Flows and keys referenced by the DEPLOY.md provider form. The worker creates
    # them from blueprints shortly after the API comes up, so wait for them.
    def wait_one(path: str, **params: str) -> dict:
        deadline = time.monotonic() + READY_TIMEOUT
        last = "no result"
        while time.monotonic() < deadline:
            try:
                res = ak.get(path, **params)["results"]
            except httpx.HTTPStatusError as exc:
                # 2026.x answers 400 to a filter whose value no object carries yet (seen for the
                # scope mappings right after boot): the same "not there yet" as an empty list.
                if exc.response.status_code != 400:
                    raise
                res, last = [], f"HTTP 400: {exc.response.text[:200]}"
            if len(res) == 1:
                return res[0]
            if res:
                last = f"{len(res)} results"
            time.sleep(3)
        raise SystemExit(
            f"{path} {params} did not appear within {READY_TIMEOUT}s ({last}); "
            "Authentik's default blueprints are not applied, is the worker running?"
        )

    auth_flow = wait_one("/flows/instances/", slug="default-provider-authorization-implicit-consent")
    inval_flow = wait_one("/flows/instances/", slug="default-provider-invalidation-flow")
    signing_key = wait_one("/crypto/certificatekeypairs/", name="authentik Self-signed Certificate")
    scope_pks = []
    # offline_access: the gateway keeps Authentik's refresh token to re-check membership live.
    for scope in ("openid", "email", "profile", "offline_access"):
        managed = f"goauthentik.io/providers/oauth2/scope-{scope}"
        scope_pks.append(wait_one("/propertymappings/provider/scope/", managed=managed)["pk"])
    # The optional mapping for uploaded pictures, and Avatars set to use the uploaded one first.
    name = "MTG Assistant Gateway: uploaded picture"
    picture_mapping = ak.find_or_create(
        "/propertymappings/provider/scope/",
        {"name": name},
        {"name": name, "scope_name": "profile", "expression": picture_mapping_expression()},
    )
    scope_pks.append(picture_mapping["pk"])
    r = ak.http.patch("/admin/settings/", json={"avatars": "attributes.avatar,initials"})
    if r.status_code >= 400:
        raise SystemExit(f"avatar setting failed {r.status_code}: {r.text}")
    png = small_png()
    Path(out).with_name("avatar.png").write_bytes(png)

    client_id = secrets.token_hex(20)
    client_secret = secrets.token_urlsafe(64)
    provider = ak.find_or_create(
        "/providers/oauth2/",
        {"name": "MTG Assistant Gateway"},
        {
            "name": "MTG Assistant Gateway",
            "authorization_flow": auth_flow["pk"],
            "invalidation_flow": inval_flow["pk"],
            "client_type": "confidential",
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uris": [{"matching_mode": "strict", "url": f"{gateway_url.rstrip('/')}/auth/callback"}],
            "signing_key": signing_key["pk"],
            "property_mappings": scope_pks,
            "sub_mode": "hashed_user_id",
            "include_claims_in_id_token": True,
            # Short-lived Authentik access tokens, so the suite also exercises the gateway renewing
            # them with the refresh token for its live membership checks (membership.py).
            "access_token_validity": "minutes=1",
        },
    )
    # Applications are addressed by slug (the list endpoint has no slug filter).
    r = ak.http.get("/core/applications/mtg-gateway/")
    if r.status_code == 404:
        r = ak.http.post(
            "/core/applications/",
            json={
                "name": "MTG Assistant Gateway",
                "slug": "mtg-gateway",
                "provider": provider["pk"],
                "meta_launch_url": gateway_url,
            },
        )
    if r.status_code >= 400:
        raise SystemExit(f"application setup failed {r.status_code}: {r.text}")
    app = r.json()

    groups = {
        name: ak.find_or_create("/core/groups/", {"name": name}, {"name": name})
        for name in (ALLOWED_GROUP, GUEST_GROUP)
    }
    # DEPLOY.md step 3.2: bind the groups that may use the application. (The bindings
    # list only filters by target, so match the group here.)
    bound = {b.get("group") for b in ak.get("/policies/bindings/", target=app["pk"])["results"]}
    for name, group in groups.items():
        if group["pk"] not in bound:
            r = ak.http.post(
                "/policies/bindings/",
                json={"target": app["pk"], "group": group["pk"], "order": 0, "enabled": True},
            )
            if r.status_code >= 400:
                raise SystemExit(f"binding {name} failed {r.status_code}: {r.text}")

    passwords: dict[str, str] = {}
    login_checks: dict[str, int] = {}
    for username, (name, user_groups) in USERS.items():
        user = ak.find_or_create(
            "/core/users/",
            {"username": username},
            {
                "username": username,
                "name": name,
                "email": f"{username}@e2e.test",
                "is_active": True,
                "groups": [groups[g]["pk"] for g in user_groups],
                "path": "users",
                "attributes": (
                    {"avatar": "data:image/png;base64," + base64.b64encode(png).decode()}
                    if username == PICTURE_USER
                    else {}
                ),
            },
        )
        pw = secrets.token_urlsafe(18)
        login_checks[username] = set_password_and_verify(ak, base, verify, user["pk"], username, pw)
        passwords[username] = pw

    issuer = f"{base.rstrip('/')}/application/o/mtg-gateway/"
    evidence = collect_evidence(ak, auth_flow, inval_flow, provider, app, groups)
    evidence["login_checks"] = login_checks  # set_password attempts each test user needed (1 = first worked)
    with open(out.replace(".json", "-evidence.json"), "w", encoding="utf-8") as f:
        json.dump(evidence, f, indent=2, sort_keys=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(
            {
                "issuer": issuer,
                "client_id": provider["client_id"],
                "client_secret": provider.get("client_secret") or client_secret,
                "required_group": ALLOWED_GROUP,
                "users": {u: {"password": passwords[u], "groups": USERS[u][1]} for u in USERS},
            },
            f,
            indent=2,
        )
    print(
        f"authentik {evidence['version']} configured: issuer {issuer}, client_id {provider['client_id']}, "
        f"{len(USERS)} test users, {len(evidence['blueprints'])} blueprints applied"
    )


def password_is_accepted(base: str, verify: bool | str, username: str, password: str) -> bool:
    """Sign in through Authentik's flow executor API, as the browser does, and report the verdict.

    The authentication flow is loaded first so the session carries a plan, then the identification
    and password stages are answered; success is the executor's redirect challenge.
    """
    flow = "default-authentication-flow"
    with httpx.Client(base_url=base.rstrip("/"), verify=verify, timeout=30, follow_redirects=True) as c:
        c.get(f"/if/flow/{flow}/")
        url = f"/api/v3/flows/executor/{flow}/?query="
        json_only = {"Accept": "application/json"}
        c.get(url, headers=json_only)
        r = c.post(
            url, json={"component": "ak-stage-identification", "uid_field": username}, headers=json_only
        )
        if r.status_code != 200 or r.json().get("component") != "ak-stage-password":
            return False
        r = c.post(url, json={"component": "ak-stage-password", "password": password}, headers=json_only)
        return r.status_code == 200 and r.json().get("component") == "xak-flow-redirect"


def set_password_and_verify(ak: AK, base: str, verify: bool | str, pk: int, username: str, pw: str) -> int:
    """Set a test user's password and prove Authentik accepts it before the suite relies on it.

    One CI run saw Authentik refuse a freshly set password at the login page (PR #27's first e2e
    run); the next run passed. Until the cause is known, setup retries the set_password call when
    a login check fails and records how many attempts each user needed, so a recurrence is
    visible in the evidence instead of failing a test minutes later.
    """
    for attempt in range(1, 6):
        r = ak.http.post(f"/core/users/{pk}/set_password/", json={"password": pw})
        if r.status_code >= 400:
            raise SystemExit(f"set_password failed for {username}: {r.status_code} {r.text}")
        if password_is_accepted(base, verify, username, pw):
            if attempt > 1:
                print(f"warning: {username}: Authentik accepted the password only on attempt {attempt}")
            return attempt
        print(f"warning: {username}: Authentik refused the password just set (attempt {attempt}); retrying")
        time.sleep(3)
    raise SystemExit(f"{username}: Authentik kept refusing the password set through its API")


def collect_evidence(
    ak: AK, auth_flow: dict, inval_flow: dict, provider: dict, app: dict, groups: dict
) -> dict:
    """What this Authentik version actually produced, for comparing versions run by run.

    Nothing secret: no client secret, no passwords, no tokens. Written next to the setup
    output as ``*-evidence.json`` and printed by the CI workflow.
    """

    def tolerant(path: str, **params: str) -> dict | list | str:
        try:
            return ak.get(path, **params)
        except (httpx.HTTPError, ValueError) as exc:  # a version without the endpoint is still evidence
            return f"unavailable: {exc}"

    version = tolerant("/admin/version/")
    blueprints = tolerant("/managed/blueprints/")
    flows = tolerant("/flows/instances/")
    bindings = tolerant("/policies/bindings/", target=app["pk"])
    return {
        "version": version.get("version_current") if isinstance(version, dict) else version,
        "version_raw": version,
        "blueprints": [
            {"name": b.get("name"), "path": b.get("path"), "status": b.get("status")}
            for b in (blueprints.get("results", []) if isinstance(blueprints, dict) else [])
        ],
        "flows": [
            {k: f.get(k) for k in ("slug", "designation", "authentication")}
            for f in (flows.get("results", []) if isinstance(flows, dict) else [])
        ],
        "provider": {
            k: provider.get(k)
            for k in (
                "pk",
                "client_type",
                "sub_mode",
                "issuer_mode",
                "include_claims_in_id_token",
                "access_code_validity",
                "access_token_validity",
                "refresh_token_validity",
                "redirect_uris",
                "property_mappings",
                "signing_key",
                "encryption_key",
            )
        },
        "provider_flows": {"authorization": auth_flow.get("slug"), "invalidation": inval_flow.get("slug")},
        "application": {k: app.get(k) for k in ("pk", "slug", "provider", "meta_launch_url")},
        "group_bindings": [
            {"group": b.get("group"), "order": b.get("order"), "enabled": b.get("enabled")}
            for b in (bindings.get("results", []) if isinstance(bindings, dict) else [])
        ],
        "groups": {name: g.get("pk") for name, g in groups.items()},
    }


if __name__ == "__main__":
    main()
