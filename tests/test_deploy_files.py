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
    """0.7.29: a setting the Settings page manages is passed empty unless the
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
            # every one, the gateway name too ("# MTG_SERVER_NAME=" passed before)
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
    # 0.7.32: every one of them, so an env value always reaches the gateway and locks the setting
    # (until 0.7.31 the stack files left out 21 of them, and setting those did nothing)
    assert passed == {s.env for s in MANAGED_SETTINGS.values()}


# The stack files and the Compose proxy overrides each example file covers, and what an example
# may list that no file interpolates (COMPOSE_PROFILES is read by Docker Compose itself).
COVERED = [
    ("deploy/stack.env.example", ["deploy/portainer-stack.yml"], set()),
    (
        "deploy/compose/.env.example",
        [
            "deploy/compose/docker-compose.yml",
            "deploy/proxy/caddy/compose.caddy.yml",
            "deploy/proxy/traefik/compose.traefik.yml",
        ],
        {"COMPOSE_PROFILES"},
    ),
]
EXAMPLE_LINE = re.compile(r"^(# ?)?([A-Z][A-Z0-9_]*)=(.*)$")
REQUIRED = re.compile(r"^# REQUIRED\b")
ON_PAGE = "# Optional. On the Settings page if not set here."
OFF_PAGE = "# Optional. Not on the Settings page."


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


def _example(name: str) -> dict[str, tuple[str, bool, str]]:
    """{variable: (label, set, value)}. The label is the last REQUIRED or Optional line of the
    comment just above the variable (other variable lines in between belong to the same note)."""
    out: dict[str, tuple[str, bool, str]] = {}
    label = ""
    for line in (ROOT / name).read_text().splitlines():
        m = EXAMPLE_LINE.match(line)
        if m:
            out[m.group(2)] = (label, m.group(1) is None, m.group(3))
        elif not line.strip() or line.startswith("# ---"):
            label = ""
        elif REQUIRED.match(line) or line.startswith("# Optional."):
            label = line
    return out


@pytest.mark.parametrize(("example", "files", "extra"), COVERED)
def test_every_variable_the_stack_reads_is_in_its_example(example: str, files: list[str], extra: set):
    """R-220: the example lists every variable its stack file reads, and nothing it doesn't."""
    read, listed = set(_interpolated(files)), set(_example(example))
    assert read - listed == set()
    assert listed - read - extra == set()


@pytest.mark.parametrize(("example", "files", "extra"), COVERED)
def test_every_example_variable_says_required_or_optional_and_where_it_is_changed(
    example: str, files: list[str], extra: set
):
    """R-220: each variable's note says REQUIRED or Optional, and an optional one says whether it
    is on the Settings page when it isn't set in the file (R-208: setting it there locks it)."""
    from mtg_gateway.config import MANAGED_SETTINGS

    managed = {s.env for s in MANAGED_SETTINGS.values()}
    read = _interpolated(files)
    for var, (label, _set, _value) in _example(example).items():
        if REQUIRED.match(label):
            assert var not in managed, var
            continue
        assert label.startswith(ON_PAGE if var in managed else OFF_PAGE), (var, label)
        # what the stack file insists on is never labelled optional
        assert read.get(var, ("", ""))[0] != ":?", var


# Optional variables whose example value isn't the value used when unset, each with the reason
# its note gives: writes on (Ben's call, B5, 0.7.31), a placeholder for a value needed with Forge
# on, and the second backup folder, whose fallback (/dev/null, mounted in its place) means none.
NOT_THE_DEFAULT = {"MTG_WRITES_ENABLED", "FORGE_NODE", "MTG_BACKUP_COPY_DIR"}


@pytest.mark.parametrize(("example", "files", "extra"), COVERED)
def test_every_optional_example_value_is_its_default(example: str, files: list[str], extra: set):
    """R-220: an optional line shows the value used when it isn't set: the stack file's fallback,
    or for a setting the stack passes empty, the gateway's own default."""
    from mtg_gateway.config import MANAGED_SETTINGS

    defaults = {s.env: s.default for s in MANAGED_SETTINGS.values()}
    read = _interpolated(files)
    for var, (label, _set, value) in _example(example).items():
        if REQUIRED.match(label) or var in NOT_THE_DEFAULT or var in extra:
            continue
        if var in defaults:
            d = defaults[var]
            assert value == (str(d).lower() if isinstance(d, bool) else str(d)), var
        else:
            assert value == read[var][1], (var, value, read[var])


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


def test_upgrading_lists_the_range_of_every_setting_the_0732_stack_newly_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 0.7.32 upgrade note lists each of the 21 settings the stack file now passes with the
    values the gateway accepts at start, and those are the values load_settings accepts."""
    from mtg_gateway.config import ConfigError, load_settings

    from .test_admin import _env

    text = (ROOT / "docs" / "UPGRADING.md").read_text(encoding="utf-8")
    section = text.split("### 0.7.32", 1)[1].split("\n### ", 1)[0]
    rows = dict(re.findall(r"^\s*\| `(MTG_[A-Z_]+)` \| (.+?) \|$", section, re.M))
    newly = {
        "MTG_AUTO_BIG_EDIT_ROWS",
        "MTG_PROPOSAL_EXPIRY_DAYS",
        "MTG_ARCHIDEKT_BACKUP_COPIES",
        "MTG_ARCHIDEKT_BACKUP_BOX_DEFAULT",
        "MTG_SNAPSHOTS_PER_DECK",
        "MTG_FORGE_PARALLEL_GAMES",
    } | {n for n in rows if n.startswith("MTG_SCAN_")}
    assert set(rows) == newly and len(rows) == 21, sorted(rows)
    _env(tmp_path, monkeypatch)
    for name, allowed in rows.items():
        if allowed == "`true` or `false`":
            continue
        lo, hi = allowed.split(" to ")
        step = "1" if "." not in hi else "0.001"
        above = str(float(hi) + float(step)) if "." in hi else str(int(hi) + 1)
        below = str(float(lo) - float(step)) if "." in lo else str(int(lo) - 1)
        for ok in (lo, hi):
            monkeypatch.setenv(name, ok)
            load_settings()
        for bad in (above, below):
            monkeypatch.setenv(name, bad)
            with pytest.raises(ConfigError, match=name):
                load_settings()
        monkeypatch.delenv(name)


def test_a_setting_that_is_not_a_number_names_seconds_only_for_a_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A non-number in a decimal setting stops the gateway with a message that says what is
    wanted: a number of seconds for the pacing gaps, a plain number for a ratio or score."""
    from mtg_gateway.config import ConfigError, load_settings

    from .test_admin import _env

    _env(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_SCAN_GLARE_RATIO", "lots")
    with pytest.raises(ConfigError) as err:
        load_settings()
    assert str(err.value) == "MTG_SCAN_GLARE_RATIO must be a number"
    monkeypatch.delenv("MTG_SCAN_GLARE_RATIO")
    monkeypatch.setenv("MTG_SCRYFALL_LOOKUP_INTERVAL", "soon")
    with pytest.raises(ConfigError) as err:
        load_settings()
    assert str(err.value) == "MTG_SCRYFALL_LOOKUP_INTERVAL must be a number of seconds"
