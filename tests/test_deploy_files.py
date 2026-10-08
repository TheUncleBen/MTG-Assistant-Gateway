"""The shipped deployment files keep the safer settings: the example env files leave in-chat applying
off, the Compose services are capped,
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
def test_example_env_keeps_the_default_approval_mode_manual(name: str):
    # Out of the box every change waits for the member's own press; members loosen their own
    # mode on the Account page, and the examples do not cap it.
    env = _env(ROOT / name)
    assert env["MTG_APPROVAL_MODE_DEFAULT"] == "manual" and env["MTG_APPROVAL_MODE_MAX"] == "auto"
    assert "MTG_APPLY_VIA_MCP" not in env and "MTG_APPLY_MIN_AGE_SECONDS" not in env


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


def test_every_static_file_is_shipped_in_the_package() -> None:
    """The image installs the wheel, so a static file the package-data globs miss (a card page, a
    script) is absent at runtime and the gateway fails to start; CI's smoke job caught exactly that
    for `static/cards/` once."""
    import tomllib

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text())["tool"]["setuptools"]["package-data"]
    src = root / "src"
    missing = []
    for pkg, patterns in data.items():
        pkg_dir = src / pkg.replace(".", "/")
        for f in pkg_dir.rglob("*"):
            if not f.is_file() or "__pycache__" in f.parts or f.suffix == ".py":
                continue
            rel = f.relative_to(pkg_dir).as_posix()
            if not rel.startswith("static/"):
                continue
            if rel.startswith("static/vendor/"):
                continue  # covered by the recursive vendor glob of the scan package
            sub = [p for p in data if p != pkg and p.startswith(pkg + ".")]
            if any(rel.startswith(s2.split(".", 1)[1].replace(".", "/") + "/") for s2 in sub):
                continue
            if not any(_glob_match(rel, pat) for pat in patterns):
                missing.append(f"{pkg}: {rel}")
    assert not missing, missing


def _glob_match(rel: str, pattern: str) -> bool:
    import fnmatch

    if "**" in pattern:
        return fnmatch.fnmatch(rel, pattern.replace("**/", "*"))
    # a single * never crosses a folder boundary, as in setuptools
    return rel.count("/") == pattern.count("/") and fnmatch.fnmatch(rel, pattern)
