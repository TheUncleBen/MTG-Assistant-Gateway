"""The shipped deployment files keep the safer settings: the example env files leave in-chat applying
off, base images are pinned by digest, Dependabot moves the pins, the Compose services are capped,
and CI checks the Gradle wrapper before any Android build. Plain text checks, no YAML parser."""

from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _env(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in path.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip()
    return out


@pytest.mark.parametrize("name", ["deploy/stack.env.example", "deploy/compose/.env.example"])
def test_example_env_keeps_apply_via_mcp_off(name: str):
    # With true, a prompt-injected assistant could apply its own proposal after the minimum wait;
    # the examples keep the member's own click on the review page in the loop.
    assert _env(ROOT / name)["MTG_APPLY_VIA_MCP"] == "false"


@pytest.mark.parametrize("name", ["Dockerfile", "docker/mystic-forge/Dockerfile"])
def test_base_images_pinned_by_digest(name: str):
    froms = [line for line in (ROOT / name).read_text().splitlines() if line.startswith("FROM ")]
    assert froms
    for line in froms:
        assert re.match(r"FROM [\w./-]+:[\w.-]+@sha256:[0-9a-f]{64}( AS \w+)?$", line), line


def test_dependabot_covers_every_ecosystem():
    text = (ROOT / ".github/dependabot.yml").read_text()
    for eco in ("github-actions", "pip", "docker", "gradle"):
        assert f"package-ecosystem: {eco}\n" in text
    assert "/docker/mystic-forge" in text


def _service(compose: str, name: str) -> str:
    m = re.search(rf"^  {name}:\n(.*?)(?=^  \S|^\S)", compose, re.S | re.M)
    assert m, name
    return m.group(1)


@pytest.mark.parametrize("name", ["gateway", "mysticforge"])
def test_compose_services_are_capped(name: str):
    svc = _service((ROOT / "deploy/compose/docker-compose.yml").read_text(), name)
    assert "cap_drop:\n      - ALL\n" in svc
    assert "- no-new-privileges:true" in svc
    assert re.search(r"^          pids: \d+", svc, re.M)


def test_compose_mystic_forge_publishes_no_port():
    svc = _service((ROOT / "deploy/compose/docker-compose.yml").read_text(), "mysticforge")
    assert "ports:" not in svc and "expose:" not in svc


def test_ci_validates_gradle_wrapper_before_android_builds():
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    for job in ("android-check", "android"):
        body = _service(ci, job)
        check = body.find("gradle/actions/wrapper-validation@")
        build = body.find("run: |")  # the first script step, the build
        assert 0 <= check < build, job
        assert re.search(r"wrapper-validation@[0-9a-f]{40} ", body), job
