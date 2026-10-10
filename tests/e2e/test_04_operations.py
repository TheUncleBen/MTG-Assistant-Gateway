"""Operational checks on the deployed Swarm stack: isolation, secrets, backup, restart, Mystic Forge."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from .conftest import GEN, Env
from .test_03_tokens import mcp_tools_list

GATEWAY = "mtg_mtg-assistant-gateway"
FORGE = "mtg_mtg-assistant-mysticforge"


def sh(*args: str, check: bool = True) -> str:
    return subprocess.run(args, check=check, capture_output=True, text=True, timeout=300).stdout


def container_of(service: str) -> str:
    for _ in range(60):
        out = sh("docker", "ps", "-q", "--filter", f"name={service}.", "--filter", "status=running").split()
        if out:
            return out[0]
        time.sleep(2)
    raise AssertionError(f"no running container for {service}")


def copy_backup(container: str, backup_output: str, dest: Path) -> Path:
    """Copy the backup named in `mtg-gateway backup` output out of the container.

    Backups are written 0600 for the service user, so the host test user can't read the
    bind-mounted file directly; `docker cp` hands back a copy owned by the caller.
    """
    path = backup_output.split("backup written: ", 1)[1].split()[0]
    sh("docker", "cp", f"{container}:{path}", str(dest))
    return dest


def inspect(service: str) -> dict:
    return json.loads(sh("docker", "service", "inspect", service))[0]


def test_only_the_edge_publishes_a_port():
    assert "Ports" not in inspect(GATEWAY)["Endpoint"], "the gateway must not publish ports"
    assert "Ports" not in inspect(FORGE)["Endpoint"], "Mystic Forge must not publish ports"
    assert inspect(FORGE)["Spec"]["TaskTemplate"]["Networks"]


def test_least_privilege_and_private_mystic_forge_network():
    """Capabilities dropped, no privilege regain after the PUID switch, Mystic Forge off the shared net."""
    for service in (GATEWAY, FORGE):
        spec = inspect(service)["Spec"]["TaskTemplate"]["ContainerSpec"]
        assert spec.get("CapabilityDrop") == ["ALL"], service
        assert sorted(spec.get("CapabilityAdd") or []) == [
            "CAP_CHOWN",
            "CAP_DAC_OVERRIDE",
            "CAP_SETGID",
            "CAP_SETUID",
        ]
        status = sh("docker", "exec", container_of(service), "cat", "/proc/1/status")
        assert "NoNewPrivs:\t1" in status, service
    shared = sh(
        "docker", "network", "inspect", "-f", "{{.Id}}", os.environ.get("MTG_NETWORK_NAME", "mtg-gateway")
    ).strip()
    forge_nets = {n["Target"] for n in inspect(FORGE)["Spec"]["TaskTemplate"]["Networks"]}
    gateway_nets = {n["Target"] for n in inspect(GATEWAY)["Spec"]["TaskTemplate"]["Networks"]}
    assert shared not in forge_nets, "Mystic Forge must not be on the shared proxy network"
    assert shared in gateway_nets and forge_nets <= gateway_nets


def test_secret_values_are_not_in_the_service_environment():
    env = inspect(GATEWAY)["Spec"]["TaskTemplate"]["ContainerSpec"]["Env"]
    fernet = (GEN / "fernet.key").read_text().strip()
    client_secret = json.loads((GEN / "authentik.json").read_text())["client_secret"]
    joined = "\n".join(env)
    assert fernet not in joined and client_secret not in joined
    assert "MTG_FERNET_KEY_FILE=/run/secrets/mtg_fernet_key" in env
    assert "MTG_OIDC_CLIENT_SECRET_FILE=/run/secrets/mtg_oidc_client_secret" in env
    assert "MTG_SESSION_SECRET_FILE=/run/secrets/mtg_session_secret" in env
    # And the files are there, readable by the dropped-privilege process.
    c = container_of(GATEWAY)
    assert sh("docker", "exec", c, "sh", "-c", "wc -c < /run/secrets/mtg_fernet_key").strip() != "0"


def test_gateway_runs_as_puid_not_root():
    c = container_of(GATEWAY)
    uid = sh("docker", "exec", c, "awk", "/^Uid:/{print $2}", "/proc/1/status").strip()
    assert uid == "1000"
    spec = inspect(GATEWAY)["Spec"]["TaskTemplate"]
    assert spec["Resources"]["Limits"]["MemoryBytes"] == 256 * 1024 * 1024
    assert spec["Placement"]["Constraints"][0].startswith("node.hostname ==")


def test_mystic_forge_answers_internally_and_lists_its_tools():
    """Reached from inside the gateway container, the only place that should talk to it."""
    c = container_of(GATEWAY)
    script = """
import json, urllib.request
host = 'tasks.mtg-assistant-mysticforge'
h = urllib.request.urlopen(f'http://{host}:8000/health', timeout=20)
print(h.status)
payload = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}).encode()
headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
req = urllib.request.Request(f'http://{host}:8000/mcp', data=payload, headers=headers)
body = urllib.request.urlopen(req, timeout=60).read().decode()
lines = body.splitlines()
line = next((ln for ln in lines if ln.startswith('data:')), body)
tools = json.loads(line[5:] if line.startswith('data:') else line)['result']['tools']
print(len(tools)); print(','.join(sorted(t['name'] for t in tools)))
"""
    out = sh("docker", "exec", c, "python", "-c", script).splitlines()
    assert out[0] == "200"
    assert int(out[1]) >= 40, out
    names = out[2].split(",")
    # Mystic Forge's own list, unfiltered: it still has goldfish_run and archidekt_deck. The
    # gateway hides those from assistants (one tool per job); test_05 checks the gateway's list.
    for expected in (
        "scryfall_search",
        "goldfish_run",
        "rules_search",
        "validate_decklist",
        "archidekt_deck",
    ):
        assert expected in names


def test_mystic_forge_is_not_reachable_through_the_edge(env: Env):
    with httpx.Client(verify=env.ssl_context, trust_env=False) as c:
        for host in ("mtg-assistant-mysticforge", "mysticforge.e2e.test"):
            try:
                r = c.get("https://mtg.e2e.test/health", headers={"Host": host})
                assert r.status_code >= 400
            except httpx.HTTPError:
                pass
        # And no such route on the gateway's hostname either.
        assert c.get("https://mtg.e2e.test/health").status_code == 404


@pytest.fixture
def ben_tokens(env: Env, http: httpx.Client):
    """A real signed-in token pair for alice-test, obtained through the browser flow."""
    import asyncio
    from urllib.parse import urlencode

    from .test_03_tokens import LOOPBACK, MCP_URL, PUBLIC_URL, browser_authorize, exchange, pkce, register

    client = register(http)
    verifier, challenge = pkce()
    q = {
        "client_id": client["client_id"],
        "redirect_uri": LOOPBACK,
        "response_type": "code",
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": "ops",
        "resource": MCP_URL,
    }
    params = asyncio.run(browser_authorize(env, "alice-test", f"{PUBLIC_URL}/authorize?{urlencode(q)}"))
    r = exchange(
        http,
        client,
        grant_type="authorization_code",
        code=params["code"],
        redirect_uri=LOOPBACK,
        code_verifier=verifier,
    )
    assert r.status_code == 200, r.text
    return client, r.json()


def test_backup_export_now_and_restore(http: httpx.Client, ben_tokens, tmp_path: Path):
    _, tokens = ben_tokens
    c = container_of(GATEWAY)
    out = sh("docker", "exec", c, "mtg-gateway", "backup")
    assert "backup written: /backups/mtg-gateway-" in out
    backups = sorted((GEN / "backups").glob("mtg-gateway-*.sqlite"))
    assert backups, "backup file not visible on the host bind mount"
    # Backups hold the encrypted Archidekt credentials and audit log: private to the service user.
    assert backups[-1].stat().st_mode & 0o777 == 0o600
    # Restore check: copy the backup somewhere else (as a restore would) and open the copy on its own.
    restored = copy_backup(c, out, tmp_path / "restored.sqlite")
    db = sqlite3.connect(restored)
    users = {row[0] for row in db.execute("SELECT preferred_username FROM users")}
    assert {"alice-test", "bob-test"} <= users
    # What Authentik asserted reaches the gateway (assistants are told only the name, test_02).
    email, groups = db.execute(
        "SELECT email, groups_json FROM users WHERE preferred_username = 'alice-test'"
    ).fetchone()
    assert email == "alice-test@e2e.test" and "MTG Assistant Gateway Users" in json.loads(groups)
    rows = db.execute("SELECT preferred_username, name, email, groups_json FROM users ORDER BY 1").fetchall()
    (GEN / "identity-evidence.json").write_text(
        json.dumps({u: {"name": n, "email": e, "groups": json.loads(g)} for u, n, e, g in rows}, indent=2)
    )
    assert "guest-test" not in users and "outsider-test" not in users
    assert db.execute("SELECT count(*) FROM oauth_clients").fetchone()[0] >= 3
    assert db.execute("SELECT count(*) FROM tokens WHERE kind='access'").fetchone()[0] >= 1
    events = {row[0] for row in db.execute("SELECT DISTINCT event FROM audit_log")}
    assert {"client_registered", "login_ok", "tokens_issued", "login_rejected_group"} <= events
    db.close()
    # Tokens are stored hashed: the raw bearer token appears nowhere in the file.
    assert tokens["access_token"].encode() not in restored.read_bytes()
    assert tokens["refresh_token"].encode() not in restored.read_bytes()


def test_state_survives_a_service_restart(http: httpx.Client, ben_tokens):
    _, tokens = ben_tokens
    assert mcp_tools_list(http, tokens["access_token"]).status_code == 200
    before = container_of(GATEWAY)
    sh("docker", "service", "update", "--force", "--detach", GATEWAY)
    for _ in range(90):
        time.sleep(2)
        try:
            if container_of(GATEWAY) != before and http.get("/healthz").status_code == 200:
                break
        except (AssertionError, httpx.HTTPError):
            continue
    else:
        pytest.fail("gateway did not come back after a forced restart")
    assert mcp_tools_list(http, tokens["access_token"]).status_code == 200, "tokens lost across restart"


def test_memory_use_is_within_the_stack_limits():
    rows = sh("docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}").splitlines()
    usage = {r.split()[0]: r.split()[1] for r in rows}
    gw = next(v for k, v in usage.items() if k.startswith(GATEWAY))
    mf = next(v for k, v in usage.items() if k.startswith(FORGE))
    print(f"memory: gateway {gw}, mystic forge {mf}")
    (GEN / "memory.txt").write_text(f"gateway {gw}\nmystic-forge {mf}\n")

    def mib(s: str) -> float:
        n, unit = float(s[:-3]), s[-3:]
        return n * (1024 if unit == "GiB" else 1)

    assert mib(gw) < 256 and mib(mf) < 512
