"""CI runs what it says it runs: a test that skips for a reason CI doesn't expect fails the test
job, and a pull request that changes the scanner's Python gets the end-to-end suite."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .conftest import EXPECTED_SKIPS, unexpected_skips

ROOT = Path(__file__).resolve().parent.parent
CI = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
E2E = (ROOT / ".github/workflows/e2e.yml").read_text(encoding="utf-8")


def test_the_test_job_lists_skips_and_fails_on_an_unexpected_one() -> None:
    job = CI.split("\n  test:\n")[1].split("\n  py311:\n")[0]
    assert "pytest -q -rs -n auto" in job
    assert 'MTG_ONLY_EXPECTED_SKIPS: "1"' in job
    # what the browser and scan tests need is fetched in the same job, so they never skip there
    assert "python3 scripts/fetch_ocr_assets.py" in job and "playwright install --with-deps chromium" in job


def test_only_the_listed_reasons_pass() -> None:
    assert unexpected_skips([("a", "set SCAN_LIVE=1 to query api.scryfall.com"), ("b", "no change")]) == []
    bad = [("c", "no Chromium available for Playwright"), ("d", "run scripts/fetch_ocr_assets.py first")]
    assert unexpected_skips(bad) == bad
    assert "no Chromium available for Playwright" not in EXPECTED_SKIPS


def _run(tmp_path: Path, body: str, *args: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    (tmp_path / "test_x.py").write_text(body)
    env = {**os.environ, "MTG_ONLY_EXPECTED_SKIPS": "1"}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-q",
            "-p",
            "tests.conftest",
            "-c",
            str(tmp_path / "pytest.ini"),
            "--rootdir",
            str(tmp_path),
            str(tmp_path / "test_x.py"),
            *args,
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
    )


BODY = """
import pytest

def test_runs():
    pass

def test_by_design():
    pytest.skip("no change")

def test_needs_a_browser():
    pytest.skip("no Chromium available for Playwright")
"""


def test_an_unexpected_skip_fails_the_run_with_its_reason(tmp_path: Path) -> None:
    for args in ((), ("-n", "2")):  # alone and through pytest-xdist's workers, as CI runs it
        out = _run(tmp_path, BODY, *args)
        assert out.returncode == 1, (args, out.stdout[-2000:], out.stderr[-2000:])
        assert "skipped for a reason CI does not allow" in out.stdout
        assert "test_needs_a_browser: no Chromium available for Playwright" in out.stdout
        assert "test_by_design" not in out.stdout.split("does not allow")[1]


def test_a_module_skipped_whole_counts_too(tmp_path: Path) -> None:
    body = 'import pytest\npytest.skip("run scripts/fetch_ocr_assets.py first", allow_module_level=True)\n'
    out = _run(tmp_path, body)
    assert out.returncode == 1, out.stdout[-2000:]
    assert "run scripts/fetch_ocr_assets.py first" in out.stdout.split("does not allow")[1]


def test_expected_skips_alone_pass(tmp_path: Path) -> None:
    body = BODY.replace('pytest.skip("no Chromium available for Playwright")', "pass")
    out = _run(tmp_path, body, "-n", "2")
    assert out.returncode == 0, out.stdout[-2000:]


def test_the_end_to_end_suite_runs_for_a_scanner_change() -> None:
    """test_06_scan drives the scan tools, service and routes through the deployed gateway, so a
    pull request that changes them gets the suite; only the scan page's scripts and styles, which
    the suite never loads, are left to the test job's browser tests."""
    assert "grep -vE '^src/mtg_gateway/scan/static/'" in E2E
    assert "grep -vE '^src/mtg_gateway/scan/' " not in E2E
    script = E2E.split("hits=$(")[1].split("\n")[0]

    def relevant(files: list[str]) -> bool:
        out = subprocess.run(
            ["bash", "-c", f'files=$(printf "%s\\n" "$@"); hits=$({script}\n[ -n "$hits" ]', "x", *files],
            capture_output=True,
            text=True,
        )
        return out.returncode == 0

    assert relevant(["src/mtg_gateway/scan/tools.py"])
    assert relevant(["src/mtg_gateway/scan/service.py", "docs/SCANNING.md"])
    assert not relevant(["src/mtg_gateway/scan/static/scan.js"])
    assert not relevant(["docs/SCANNING.md"])
    assert relevant(["src/mtg_gateway/decks.py"])
