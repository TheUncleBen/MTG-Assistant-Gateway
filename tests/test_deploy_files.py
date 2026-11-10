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
    """The owner, 2026-10-25: every feature is on by default; an env variable only turns one off. The owner,
    2026-10-28: MTG_SERVER_NAME is back too, for the Gateway name, and the three sign-in times
    that had a stack variable keep it (their Settings page rows, 0.7.46)."""
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
        "MTG_ACCESS_TOKEN_TTL",
        "MTG_REFRESH_TOKEN_TTL",
        "MTG_BROWSER_SESSION_TTL",
    }
    times = {
        "MTG_DECK_SYNC_MINUTES",
        "MTG_ACCESS_TOKEN_TTL",
        "MTG_REFRESH_TOKEN_TTL",
        "MTG_BROWSER_SESSION_TTL",
    }
    assert all(v is True for k, v in on.items() if k not in times)
    assert all(on[k] > 0 for k in times)
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
def test_example_env_files_have_the_gateway_name_empty_with_its_reason(name: str):
    """The owner, 2026-10-28: MTG_SERVER_NAME is in each example under a one-line note, and the Settings
    page keeps the Gateway name until someone sets it in the stack. Empty like every other optional
    line (0.7.46 review): a name typed after a commented-out ``#MTG_SERVER_NAME=`` in Portainer's
    Advanced mode was silently ignored."""
    lines = (ROOT / name).read_text().splitlines()
    at = lines.index(f"{NAME_VAR}=")
    assert "Gateway name" in lines[at - 1] and lines[at - 1].startswith("# ")
    assert f"#{NAME_VAR}=" not in lines
    assert _env(ROOT / name)[NAME_VAR] == ""


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
        "MTG_PLUGIN_DIR", "MTG_APP_DIR", "MTG_SCAN_GLARE", "MTG_REAUTH_INTERVAL",
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


# Values the 0.7.45 example files set for the operator that are empty from 0.7.46 (a value there
# locks a Settings row, or means something else now). Copying them over from an old file is the
# trap the update steps warn about (0.7.46 review).
EMPTIED_IN_0746 = {
    "MTG_ACCESS_TOKEN_TTL": "3600",
    "MTG_REFRESH_TOKEN_TTL": "2592000",
    "MTG_BROWSER_SESSION_TTL": "7200",
}


@pytest.mark.parametrize("name", ENV_FILES)
def test_every_value_the_older_examples_set_and_these_leave_empty_is_named_in_the_update_steps(name: str):
    env = _env(ROOT / name)
    upgrading = (ROOT / "docs" / "UPGRADING.md").read_text()
    step_one = upgrading.split("1. **Your values.**", 1)[1].split("2. **Secrets.**", 1)[0]
    bullet = upgrading.split("- **0.7.45 or older:** the older example files", 1)[1].split("\n- **", 1)[0]
    for var, old in EMPTIED_IN_0746.items():
        assert env[var] == "", var
        assert f"`{var}={old}`" in step_one, var
        assert f"`{var}={old}`" in bullet, var


def test_the_stacks_pass_no_variable_that_nothing_reads() -> None:
    """DISABLE_IPV6 came from another project's image convention: the gateway, its Mystic Forge
    image and Forge never read it, so the Swarm stack set it for nothing and Compose didn't at all."""
    for name in ("deploy/portainer-stack.yml", "deploy/compose/docker-compose.yml"):
        assert "DISABLE_IPV6" not in (ROOT / name).read_text(), name
    code = list((ROOT / "src").rglob("*.py")) + [p for p in (ROOT / "docker").rglob("*") if p.is_file()]
    assert not any("DISABLE_IPV6" in p.read_text(errors="ignore") for p in code)


def test_the_push_test_gives_up_on_an_emulator_that_never_comes_back(tmp_path: Path):
    """push-test.sh's boot() waits for the emulator a bounded time and then fails, so a reboot the
    emulator never returns from is one FAIL line instead of the CI step hanging until its 30 minutes
    run out (CI on a0bc0e23 hung after "message after an update", at the reboot)."""
    import re
    import subprocess
    import time

    script = (ROOT / "android/scripts/push-test.sh").read_text(encoding="utf-8")
    boot = re.search(r"(?ms)^boot\(\) \{\n.*?^\}\n", script)
    helpers = re.search(r"(?m)^until_ok\(\) .*$", script)
    assert boot is not None and helpers is not None
    fake = tmp_path / "bin"
    fake.mkdir()
    (fake / "adb").write_text('#!/usr/bin/env bash\n[ "$1" = wait-for-device ] && exec sleep 600\nexit 0\n')
    (fake / "adb").chmod(0o755)
    body = f'{helpers.group(0)}\n{boot.group(0)}\nPORT=1\nboot; echo "boot=$?"\n'
    started = time.monotonic()
    r = subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        timeout=60,
        env={"PATH": f"{fake}:/usr/bin:/bin", "PUSH_TEST_BOOT_WAIT": "2"},
    )
    assert "boot=1" in r.stdout, (r.stdout, r.stderr)
    assert time.monotonic() - started < 30


def _push_test_boot(tmp_path: Path, adb: str) -> tuple[str, float, str]:
    """Run push-test.sh's boot_after_reboot against a stand-in adb; its stdout, seconds, calls."""
    import re
    import subprocess
    import time

    script = (ROOT / "android/scripts/push-test.sh").read_text(encoding="utf-8")
    parts = [
        re.search(r"(?m)^until_ok\(\) .*$", script),
        re.search(r"(?ms)^boot\(\) \{\n.*?^\}\n", script),
        re.search(r"(?ms)^boot_after_reboot\(\) \{\n.*?^\}\n", script),
    ]
    assert all(parts)
    fake = tmp_path / "bin"
    fake.mkdir(exist_ok=True)
    (fake / "adb").write_text(adb)
    (fake / "adb").chmod(0o755)
    run = f'PORT=1\nOUT={tmp_path}\nboot_after_reboot; echo "boot=$?"\n'
    body = "\n".join(p.group(0) for p in parts) + "\n" + run
    started = time.monotonic()
    r = subprocess.run(
        ["bash", "-c", body],
        capture_output=True,
        text=True,
        timeout=90,
        env={"PATH": f"{fake}:/usr/bin:/bin", "PUSH_TEST_BOOT_WAIT": "2", "T": str(tmp_path)},
    )
    calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
    return r.stdout + r.stderr, time.monotonic() - started, calls


def test_after_a_reboot_the_push_test_restarts_adb_once_and_waits_again(tmp_path: Path):
    """An emulator that boots again but that adb no longer sees (CI run 42712220091: "did not come
    back from the reboot") is found again by restarting adb's server, once; the run goes on."""
    adb = (
        "#!/usr/bin/env bash\n"
        'echo "$1" >> "$T/calls"\n'
        '[ "$1" = start-server ] && touch "$T/seen"\n'
        '[ "$1" = wait-for-device ] && [ ! -f "$T/seen" ] && exec sleep 600\n'
        '[ "$1" = shell ] && echo 1\n'
        "exit 0\n"
    )
    out, took, calls = _push_test_boot(tmp_path, adb)
    assert "boot=0" in out, out
    assert calls.split().count("kill-server") == 1 and calls.split().count("start-server") == 1, calls
    assert "first wait failed" in (tmp_path / "reboot.txt").read_text()
    assert took < 30


def test_after_a_reboot_the_push_test_gives_up_after_one_retry(tmp_path: Path):
    """One retry only, each wait bounded: an emulator that is really gone is one FAIL line, and the
    exit trap's logcat (which waits for a device for ever without one) is bounded too."""
    adb = (
        '#!/usr/bin/env bash\necho "$1" >> "$T/calls"\n[ "$1" = wait-for-device ] && exec sleep 600\nexit 0\n'
    )
    out, took, calls = _push_test_boot(tmp_path, adb)
    assert "boot=1" in out, out
    assert calls.split().count("wait-for-device") == 2, calls
    assert "second wait failed" in (tmp_path / "reboot.txt").read_text()
    assert took < 30
    script = (ROOT / "android/scripts/push-test.sh").read_text(encoding="utf-8")
    assert "timeout 30 adb logcat -d" in script and "if ! boot_after_reboot; then" in script


def test_every_variable_the_gateway_reads_is_documented() -> None:
    """0.7.46 sweep B-10: each variable config.py reads is either passed by both stack files (and
    so listed in their examples, above) or, when only a test stack sets it, named in both example
    files and in DEPLOY.md, which say what it is for."""
    src = (ROOT / "src" / "mtg_gateway" / "config.py").read_text(encoding="utf-8")
    read = set(re.findall(r'_env\w*\(\s*"([A-Z0-9_]+)"', src))
    assert "MTG_PUBLIC_URL" in read and len(read) > 20  # the pattern still finds them
    stacks = [(ROOT / f).read_text(encoding="utf-8") for f in STACK_FILES]
    docs = [(ROOT / f).read_text(encoding="utf-8") for f in (*ENV_FILES, "docs/DEPLOY.md")]

    def named(name: str, text: str) -> bool:
        return bool(re.search(rf"\b{name}\b", text))

    missing = [
        name
        for name in sorted(read)
        if not all(named(name, t) for t in stacks) and not all(named(name, t) for t in docs)
    ]
    assert missing == []
