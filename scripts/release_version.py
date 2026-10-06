"""The version rules CI enforces (docs/VERSIONS.md).

    python3 scripts/release_version.py check     # pull requests: VERSION is above every released version
    python3 scripts/release_version.py release   # main: VERSION is not released yet (or its tag is this
                                                 # very commit, a re-run); print the release facts

VERSION (one line, MAJOR.MINOR.PATCH) is the single source of the version. Released versions are the
repository's v* tags, so the checkout needs them (actions/checkout with fetch-depth: 0). Both commands
also need a "## [MAJOR.MINOR.PATCH]" section in CHANGELOG.md. ``release`` writes version= and minor=
to $GITHUB_OUTPUT when it is set, and the changelog section to the file given by --notes.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SEMVER = re.compile(r"(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})")


def parse(text: str) -> tuple[int, int, int] | None:
    m = SEMVER.fullmatch(text)
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def read_version(root: Path = ROOT) -> str:
    raw = (root / "VERSION").read_text(encoding="utf-8")
    version = raw.strip()
    if raw not in (version, version + "\n") or parse(version) is None:
        raise SystemExit(f"VERSION must hold one line like 1.2.3, not {raw!r}")
    return version


def released(root: Path = ROOT) -> list[tuple[int, int, int]]:
    out = subprocess.run(
        ["git", "tag", "--list", "v*"], cwd=root, check=True, capture_output=True, text=True
    ).stdout
    versions = [parse(t[1:]) for t in out.split()]
    return sorted(v for v in versions if v is not None)


def tag_commit(version: str, root: Path = ROOT) -> str | None:
    """The commit the tag v<version> points at, or None when there is no such tag."""
    out = subprocess.run(
        ["git", "rev-parse", "-q", "--verify", f"refs/tags/v{version}^{{commit}}"],
        cwd=root,
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def head_commit(root: Path = ROOT) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True
    ).stdout.strip()


def changelog_section(version: str, root: Path = ROOT) -> str:
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    m = re.search(rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |^\[[^\]]+\]: |\Z)", text, re.S | re.M)
    if not m or not m.group(1).strip():
        raise SystemExit(f"CHANGELOG.md has no '## [{version}]' section with notes; add one")
    return m.group(1).strip() + "\n"


def check(version: str, tags: list[tuple[int, int, int]]) -> str | None:
    """None when ``version`` may be merged, else why not."""
    v = parse(version)
    assert v is not None
    if v in tags:
        return f"version {version} is already released; raise VERSION (docs/VERSIONS.md)"
    if tags and v < tags[-1]:
        newest = ".".join(map(str, tags[-1]))
        return f"VERSION {version} is below the newest release {newest}; raise it (docs/VERSIONS.md)"
    return None


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["check", "release"])
    ap.add_argument("--notes", type=Path, help="release: write the changelog section here")
    args = ap.parse_args(argv)
    version = read_version(root)
    tags = released(root)
    if args.command == "release" and tag_commit(version, root) == head_commit(root):
        # This commit already claimed its version (a re-run of a release that stopped partway).
        tags = [t for t in tags if t != parse(version)]
    problem = check(version, tags)
    if problem:
        print(f"::error::{problem}")
        return 1
    notes = changelog_section(version, root)
    print(f"version {version}: not released by another commit, changelog section found")
    if args.command == "release":
        if args.notes:
            args.notes.write_text(notes, encoding="utf-8")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            major, minor, _ = parse(version)  # type: ignore[misc]
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"version={version}\nminor={major}.{minor}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
