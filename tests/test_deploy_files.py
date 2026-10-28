"""The shipped deployment files: the example env files list exactly what the stack files read, every
feature is on, the settings the admin Settings page manages are left to it, the Compose services are
capped, and CI checks the Gradle wrapper before any Android build. Plain text checks, no YAML
parser."""

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


def test_out_of_the_box_every_change_waits_for_the_members_press():
    # Members loosen their own mode on the Account page; the Settings page sets the default and cap.
    from mtg_gateway.config import MANAGED_SETTINGS

    assert MANAGED_SETTINGS["approval_mode_default"].default == "manual"
    assert MANAGED_SETTINGS["approval_mode_max"].default == "auto"
    assert not MANAGED_SETTINGS["approval_mode_default"].env


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


ENV_FILES = ["deploy/stack.env.example", "deploy/compose/.env.example"]
STACK_FILES = ["deploy/portainer-stack.yml", "deploy/compose/docker-compose.yml"]


# The one text setting with a stack variable: commented out in the example files.
NAME_VAR = "MTG_SERVER_NAME"


def _switches() -> list[str]:
    from mtg_gateway.config import MANAGED_SETTINGS

    return [s.env for s in MANAGED_SETTINGS.values() if s.env and s.env != NAME_VAR]


def test_only_the_feature_switches_have_an_env_variable_and_each_is_on():
    """Ben, 2026-10-25: every feature is on by default; an env variable only turns one off. Ben,
    2026-10-28: MTG_SERVER_NAME is back too, for the Gateway name."""
    from mtg_gateway.config import MANAGED_SETTINGS, Settings

    assert MANAGED_SETTINGS["server_name"].env == NAME_VAR
    on = {s.env: s.default for s in MANAGED_SETTINGS.values() if s.env and s.env != NAME_VAR}
    assert set(on) == {
        "MTG_WATCHLISTS_ENABLED",
        "MTG_ARCHIDEKT_BACKUPS",
        "MTG_APPLY_IN_CHAT",
        "MTG_SCAN_ART_ENABLED",
        "MTG_UPDATE_CHECK",
        "MTG_DECK_SYNC_MINUTES",
    }
    assert all(v is True for k, v in on.items() if k != "MTG_DECK_SYNC_MINUTES")
    assert on["MTG_DECK_SYNC_MINUTES"] > 0
    assert Settings.__dataclass_fields__["writes_enabled"].default is True


@pytest.mark.parametrize("name", ENV_FILES)
def test_example_env_files_leave_each_switch_empty_so_the_page_can_change_it(name: str):
    """R-208: a value written here would lock the Settings page row; empty means on."""
    env = _env(ROOT / name)
    for var in _switches():
        assert env.get(var) == "", var
    assert env["MTG_WRITES_ENABLED"] == "true"


@pytest.mark.parametrize("name", ENV_FILES)
def test_example_env_files_have_the_gateway_name_commented_out_with_its_reason(name: str):
    """Ben, 2026-10-28: MTG_SERVER_NAME is in each example, commented out under a one-line note, so
    the Settings page keeps the Gateway name until someone sets it in the stack."""
    lines = (ROOT / name).read_text().splitlines()
    at = lines.index(f"#{NAME_VAR}=")
    assert "Gateway name" in lines[at - 1] and lines[at - 1].startswith("# ")
    assert NAME_VAR not in _env(ROOT / name)


@pytest.mark.parametrize("name", STACK_FILES)
def test_stack_files_pass_the_gateway_name_empty(name: str):
    text = (ROOT / name).read_text()
    assert re.findall(rf"^\s*{NAME_VAR}:.*$", text, re.M) == [f"      {NAME_VAR}: ${{{NAME_VAR}:-}}"]


@pytest.mark.parametrize("name", STACK_FILES)
def test_stack_files_pass_each_switch_empty_and_writes_on(name: str):
    text = (ROOT / name).read_text()
    for var in _switches():
        assert re.findall(rf"^\s*{var}:.*$", text, re.M) == [f"      {var}: ${{{var}:-}}"], var
    assert "      MTG_WRITES_ENABLED: ${MTG_WRITES_ENABLED:-true}\n" in text


# The stack files and the Compose proxy overrides each example file covers.
COVERED = [
    ("deploy/stack.env.example", ["deploy/portainer-stack.yml"]),
    (
        "deploy/compose/.env.example",
        [
            "deploy/compose/docker-compose.yml",
            "deploy/proxy/caddy/compose.caddy.yml",
            "deploy/proxy/traefik/compose.traefik.yml",
        ],
    ),
]
EXAMPLE_LINE = re.compile(r"^(# ?)?([A-Z][A-Z0-9_]*)=(.*)$")


def _interpolated(files: list[str]) -> dict[str, tuple[str, str]]:
    """{name: (operator, default)} of every ${NAME...} outside a comment."""
    out: dict[str, tuple[str, str]] = {}
    for f in files:
        for line in (ROOT / f).read_text().splitlines():
            if line.lstrip().startswith("#"):
                continue
            for m in re.finditer(r"\$\{([A-Z][A-Z0-9_]*)(:?[-?])?([^}]*)\}", line):
                out.setdefault(m.group(1), (m.group(2) or "", m.group(3)))
    return out


def _example(name: str, *, optional_only: bool = False) -> dict[str, str]:
    """{variable: value}, commented-out lines included; with ``optional_only``, none from a
    "(REQUIRED)" section, whose values are placeholders to replace."""
    out: dict[str, str] = {}
    required = False
    for line in (ROOT / name).read_text().splitlines():
        if line.startswith("# ---"):
            required = "(REQUIRED)" in line
        m = EXAMPLE_LINE.match(line)
        if m and not (optional_only and required):
            out[m.group(2)] = m.group(3)
    return out


@pytest.mark.parametrize(("example", "files"), COVERED)
def test_every_variable_the_stack_reads_is_in_its_example(example: str, files: list[str]):
    """R-220: the example lists every variable its stack file reads, and nothing it doesn't."""
    read, listed = set(_interpolated(files)), set(_example(example))
    assert read - listed == set()
    assert listed - read == set()


@pytest.mark.parametrize(("example", "files"), COVERED)
def test_every_optional_example_value_is_its_default(example: str, files: list[str]):
    """R-220: an optional line shows the value used when it isn't set (the stack file's fallback),
    except the second backup folder, whose fallback (/dev/null, mounted in its place) means none."""
    read = _interpolated(files)
    for var, value in _example(example, optional_only=True).items():
        op, default = read[var]
        if op == ":?" or var == "MTG_BACKUP_COPY_DIR":
            continue
        assert value == default, (var, value, default)


@pytest.mark.parametrize("name", ENV_FILES + STACK_FILES)
def test_no_removed_variable_is_left_in_the_deploy_files(name: str):
    """Settings that moved to the Settings page, and values the stack fixes, are gone (2026-10-25)."""
    text = (ROOT / name).read_text()
    for gone in (
        "MTG_CHATGPT_NAME", "MTG_LOG_LEVEL", "MTG_APPROVAL_MODE", "MTG_FORGE_GAMES",
        "MTG_ARCHIDEKT_BACKUP_FOLDER", "MTG_AUTHENTIK_API_URL", "MTG_ALLOWED_HOSTS", "MTG_IMAGE",
        "MF_NODE", "MF_TAG", "FORGE_TAG", "FORGE_XMX", "FORGE_MAX_PARALLEL", "COMPOSE_PROFILES",
        "MTG_PLUGIN_DIR", "MTG_APP_DIR", "MTG_SCAN_GLARE",
    ):  # fmt: skip
        assert gone not in text, gone


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


def test_a_release_build_refuses_a_tree_with_the_push_test_hook(tmp_path: Path):
    """push-test.sh --prepare writes an exported debug-only receiver into android/app/src/debug: a
    release build stops at once while that folder exists, before any tool or download."""
    import shutil
    import subprocess

    scripts = tmp_path / "android" / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(ROOT / "android/scripts/build.sh", scripts / "build.sh")
    (tmp_path / "android/app/src/debug").mkdir(parents=True)
    r = subprocess.run(
        ["bash", str(scripts / "build.sh"), "release", "--throwaway"], capture_output=True, text=True
    )
    assert r.returncode == 1 and "android/app/src/debug exists" in r.stderr, r.stderr


def test_the_release_manifest_check_finds_the_hook_and_never_passes_on_nothing(tmp_path: Path):
    import subprocess

    check = ROOT / "android/scripts/check-release-manifest.sh"
    build = tmp_path / "build"

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(["bash", str(check), str(build), "plain"], capture_output=True, text=True)

    assert run().returncode == 1  # no manifest found is a failure, not a pass
    merged = build / "intermediates/merged_manifest/plainRelease/processPlainReleaseMainManifest"
    merged.mkdir(parents=True)
    (merged / "AndroidManifest.xml").write_text("<manifest><application/></manifest>")
    assert run().returncode == 0
    packaged = build / "intermediates/packaged_manifests/plainRelease/processPlainReleaseManifestForPackage"
    packaged.mkdir(parents=True)
    (packaged / "AndroidManifest.xml").write_text(
        '<manifest><application><receiver android:name="local.mtgassistantgateway.app.PushTestHooks" />'
        "</application></manifest>"
    )
    r = run()
    assert r.returncode == 1 and "PushTestHooks" in r.stderr
    # another flavor's manifests are not this build's
    assert subprocess.run(["bash", str(check), str(build), "applinks"], capture_output=True).returncode == 1
