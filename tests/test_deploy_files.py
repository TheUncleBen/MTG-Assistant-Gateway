"""The shipped deployment files keep the safer settings: the example env files leave in-chat applying
off, the Compose services are capped, the settings the admin Settings page manages are left to it,
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
    text = (ROOT / name).read_text()
    assert "\n# MTG_APPROVAL_MODE_DEFAULT=manual\n" in text and "\n# MTG_APPROVAL_MODE_MAX=auto\n" in text
    assert "MTG_APPROVAL_MODE_DEFAULT" not in env and "MTG_APPROVAL_MODE_MAX" not in env
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


@pytest.mark.parametrize("name", ["deploy/stack.env.example", "deploy/compose/.env.example"])
def test_example_env_keeps_watchlists_off(name: str):
    assert "MTG_WATCHLISTS_ENABLED" not in _env(ROOT / name)
    assert "\n# MTG_WATCHLISTS_ENABLED=false\n" in (ROOT / name).read_text()
    from mtg_gateway.config import MANAGED_SETTINGS

    assert MANAGED_SETTINGS["watchlists_enabled"].default is False


@pytest.mark.parametrize("name", ["deploy/portainer-stack.yml", "deploy/compose/docker-compose.yml"])
def test_stack_files_leave_the_settings_page_its_settings(name: str):
    """0.7.29 (Q17): a setting the Settings page manages is passed empty unless the
    env sets it. A default written here would count as "set in your stack" and lock it there."""
    from mtg_gateway.config import MANAGED_SETTINGS

    text = (ROOT / name).read_text()
    for spec in MANAGED_SETTINGS.values():
        lines = re.findall(rf"^\s*{spec.env}:.*$", text, re.M)
        assert lines in ([], [f"      {spec.env}: ${{{spec.env}:-}}"]), lines
    assert "MTG_WATCHLISTS_ENABLED: ${MTG_WATCHLISTS_ENABLED:-}\n" in text


@pytest.mark.parametrize("name", ["deploy/stack.env.example", "deploy/compose/.env.example"])
def test_example_env_files_leave_the_settings_page_its_settings(name: str):
    """Each managed setting is shown commented out, with the gateway's own default."""
    from mtg_gateway.config import MANAGED_SETTINGS

    env, text = _env(ROOT / name), (ROOT / name).read_text()
    for spec in MANAGED_SETTINGS.values():
        assert spec.env not in env, spec.env
        for value in re.findall(rf"^# {spec.env}=(.*)$", text, re.M):
            default = spec.default
            shown = str(default).lower() if isinstance(default, bool) else str(default)
            # every one, the gateway name too (review R12-3: "# MTG_SERVER_NAME=" passed before)
            assert value == shown, (spec.env, value)


@pytest.mark.parametrize(
    ("stack_file", "example"),
    [
        ("deploy/portainer-stack.yml", "deploy/stack.env.example"),
        ("deploy/compose/docker-compose.yml", "deploy/compose/.env.example"),
    ],
)
def test_every_managed_setting_the_stack_passes_is_in_its_example(stack_file: str, example: str):
    """A setting the stack file passes is listed (commented out) in the example file next to it, so
    the example's note ("Lines starting # MTG_ are settings…") covers every one of them."""
    from mtg_gateway.config import MANAGED_SETTINGS

    passed = {
        s.env
        for s in MANAGED_SETTINGS.values()
        if f"      {s.env}: ${{{s.env}:-}}" in (ROOT / stack_file).read_text()
    }
    text = (ROOT / example).read_text()
    listed = {s.env for s in MANAGED_SETTINGS.values() if re.search(rf"^# {s.env}=", text, re.M)}
    assert passed and passed - listed == set()


@pytest.mark.parametrize(
    ("name", "service"),
    [("deploy/portainer-stack.yml", "mtg-assistant-ntfy"), ("deploy/compose/docker-compose.yml", "ntfy")],
)
def test_ntfy_has_room_for_every_phone_the_gateway_relays(name: str, service: str):
    """ntfy sees every phone's connection coming from the gateway, one visitor, so its per-visitor
    limits are raised above the gateway's own cap on connections (option names and defaults
    from ntfy 2.31.1's cmd/serve.go and server/config.go)."""
    from mtg_gateway.notify import STREAM_MAX_TOTAL

    svc = _service((ROOT / name).read_text(), service)
    limits = dict(re.findall(r'^      (NTFY_VISITOR_[A-Z_]+): "([^"]+)"$', svc, re.M))
    assert int(limits["NTFY_VISITOR_SUBSCRIPTION_LIMIT"]) > STREAM_MAX_TOTAL
    assert int(limits["NTFY_VISITOR_REQUEST_LIMIT_BURST"]) >= 2 * STREAM_MAX_TOTAL
    assert limits["NTFY_VISITOR_REQUEST_LIMIT_REPLENISH"] == "1s"
