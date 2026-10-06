"""VERSION is the one source of the version; everything that repeats it must agree (docs/VERSIONS.md)."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import mtg_gateway

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import release_version as rv  # noqa: E402


def test_everything_carries_the_version_file() -> None:
    version = rv.read_version(ROOT)
    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"] == version
    assert mtg_gateway.__version__ == version
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text())
    assert market["metadata"]["version"] == version
    assert {p["version"] for p in market["plugins"]} == {version}
    for manifest in ROOT.glob("plugin/*/plugin.json"):
        assert json.loads(manifest.read_text())["version"] == version, manifest
    for manifest in ROOT.glob("plugin/*/.claude-plugin/plugin.json"):
        assert json.loads(manifest.read_text())["version"] == version, manifest
    # The Android app reads VERSION at build time; there is no second copy to drift.
    gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text()
    assert re.search(r'rootProject\.file\("\.\./VERSION"\)', gradle)
    assert not list(ROOT.glob("android/**/BuildInfo.kt"))


def test_the_version_has_a_changelog_section() -> None:
    assert rv.changelog_section(rv.read_version(ROOT), ROOT).strip()


@pytest.mark.parametrize(
    ("version", "tags", "ok"),
    [
        ("1.0.0", [], True),
        ("1.0.1", [(1, 0, 0)], True),
        ("1.1.0", [(1, 0, 0), (1, 0, 5)], True),
        ("2.0.0", [(1, 9, 9)], True),
        ("1.0.0", [(1, 0, 0)], False),  # already released
        ("1.0.4", [(1, 0, 0), (1, 0, 5)], False),  # below the newest
    ],
)
def test_check(version: str, tags: list[tuple[int, int, int]], ok: bool) -> None:
    assert (rv.check(version, tags) is None) is ok


@pytest.mark.parametrize(
    "text", ["1.0", "v1.0.0", "01.0.0", "1.0.0-rc1", "1.0.0 ", "1.0.0\n2.0.0", "1.2.3; rm"]
)
def test_parse_rejects(text: str) -> None:
    assert rv.parse(text) is None


def test_read_version_rejects_extra_lines(tmp_path: Path) -> None:
    (tmp_path / "VERSION").write_text("1.0.0\n\n")
    with pytest.raises(SystemExit):
        rv.read_version(tmp_path)


def _repo(tmp_path: Path, version: str) -> Path:
    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)

    (tmp_path / "VERSION").write_text(version + "\n")
    (tmp_path / "CHANGELOG.md").write_text(f"# Changelog\n\n## [{version}] - 2026-01-01\n\n- Notes.\n")
    git("init", "-q")
    git("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "c", "--allow-empty")
    git("add", ".")
    git("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "v")
    return tmp_path


def test_release_accepts_a_rerun_of_its_own_tag(tmp_path: Path) -> None:
    root = _repo(tmp_path, "1.2.0")
    assert rv.main(["release"], root) == 0
    subprocess.run(["git", "tag", "v1.2.0"], cwd=root, check=True)
    # The tag is on this very commit: a re-run of a release that stopped partway may finish it.
    assert rv.main(["release"], root) == 0
    # A pull request still may not reuse it.
    assert rv.main(["check"], root) == 1


def test_release_refuses_a_version_tagged_on_another_commit(tmp_path: Path) -> None:
    root = _repo(tmp_path, "1.2.0")
    subprocess.run(["git", "tag", "v1.2.0", "HEAD~1"], cwd=root, check=True)
    assert rv.main(["release"], root) == 1
