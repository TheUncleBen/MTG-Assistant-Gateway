"""No fixed sleeps in the tests, unless a comment says why a fixed time is the right tool.

A fixed wait (Playwright's ``wait_for_timeout`` or ``time.sleep``) makes a test slow on a fast
machine and flaky on a loaded one: it guesses how long the page needs instead of waiting for what
the test needs. Tests wait on the real condition instead (``expect(...)``, ``wait_for_function``,
``wait_for_url``, a polling loop with a deadline).

A fixed wait stays only where the time itself is the point: a check that something does NOT
happen within a window, a debounce or timer the code under test uses, or the pause between two
tries of a polling loop that has a deadline. Such a wait carries the marker comment
``# fixed wait: <reason>`` on the same line or in the comment lines just above it.
"""

from __future__ import annotations

import re
from pathlib import Path

TESTS = Path(__file__).resolve().parent
MARKER = re.compile(r"#\s*fixed wait:\s*\S")
WAIT = re.compile(r"\b(?:wait_for_timeout|time\.sleep)\(")


def _comment_above(lines: list[str], i: int) -> str:
    """The block of comment lines just above line ``i`` (a reason may take more than one line)."""
    block = []
    j = i - 1
    while j >= 0 and lines[j].lstrip().startswith("#"):
        block.append(lines[j])
        j -= 1
    return "\n".join(reversed(block))


def unexplained_waits(root: Path = TESTS) -> list[str]:
    found = []
    for path in sorted(root.rglob("*.py")):
        if path.resolve() == Path(__file__).resolve():
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for i, line in enumerate(lines):
            if line.lstrip().startswith("#") or not WAIT.search(line):
                continue
            if MARKER.search(line) or MARKER.search(_comment_above(lines, i)):
                continue
            found.append(f"{path.relative_to(root.parent)}:{i + 1}: {line.strip()}")
    return found


def test_no_fixed_wait_without_a_reason() -> None:
    found = unexplained_waits()
    assert not found, (
        "fixed waits without a '# fixed wait: <reason>' comment; wait on the real condition "
        "instead:\n" + "\n".join(found)
    )


def test_the_scan_sees_a_wait_and_its_marker(tmp_path: Path) -> None:
    (tmp_path / "test_x.py").write_text(
        "page.wait_for_timeout(300)\n"
        "# fixed wait: proves no request is sent within the debounce window,\n"
        "# a reason on two lines\n"
        "page.wait_for_timeout(300)\n"
        "time.sleep(0.1)  # fixed wait: poll interval of a loop with a deadline\n"
        "time.sleep(1)  # not a marker\n"
        "# a comment naming wait_for_timeout( is not a wait\n",
        encoding="utf-8",
    )
    found = unexplained_waits(tmp_path)
    assert [f.split(": ", 1)[1] for f in found] == [
        "page.wait_for_timeout(300)",
        "time.sleep(1)  # not a marker",
    ]


# A page's sideways scroll measured straight after a click or a zoom can catch a transition
# halfway (goldfish's card size at 200% text: 2 px in a full parallel run, 0 alone). A test
# measures it through waits.sideways, which waits for the page to settle first. A composite
# measure (several numbers from one script) is left alone: it is one line of a larger script.
BARE_SIDEWAYS = re.compile(
    r'^\s*(?:\w+ = |.*page\.evaluate\(\s*)"(?:\(\) => )?document\.documentElement\.scrollWidth - '
    r'document\.documentElement\.clientWidth"'
)


def test_sideways_scroll_is_measured_once_the_page_has_settled() -> None:
    found = []
    for path in sorted(TESTS.glob("test_*.py")):
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if BARE_SIDEWAYS.search(line):
                found.append(f"{path.name}:{i + 1}: {line.strip()}")
    assert not found, "measure sideways scrolling with waits.sideways(page):\n" + "\n".join(found)
