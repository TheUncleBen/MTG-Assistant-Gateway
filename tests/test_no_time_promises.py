"""Busy, limit and error messages never promise a time ("try again in a minute"): the gateway can't
know when Archidekt, Scryfall, the identity provider or its own queues will have room again. A wait
the gateway does know (its own pause after a Retry-After, a fixed cooldown) may still be named."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "mtg_gateway"
PROMISE = re.compile(
    r"try again (?:in a (?:minute|moment|few)|shortly|soon)|try the rest again in a few"
    r"|again in a few minutes|within a minute or two|reload (?:this page |the page )?in a (?:minute|moment)"
    r"|wait a few minutes and try|wait a (?:minute|moment)(?:[,.(]| and| \()|for a few minutes\."
    r"|waits a few minutes|refills over a few minutes|try again in [{0-9]|within a few (?:seconds|minutes)"
    r"|in about (?:a |[0-9])",
    re.IGNORECASE,
)
# a message split over several source lines ("...try again in a " "few minutes.") is one string
SPLIT = re.compile(r"""(["'])\s*\n\s*[fbr]*\1""")
# What the pattern also meets that is no busy, limit or error message: code comments, and docs
# describing what a page or the app does (file name, words next to the match)
NOT_A_MESSAGE = {
    ("mf_proxy.py", "answers a tool listing now (within a few seconds)"),  # docstring
    ("forge-live.js", "each finished game shows within about 8 s"),  # comment
    ("IDP-AUTHENTIK.md", "picture appears within a few seconds of the member's next page"),
    ("ANDROID.md", "if that happens again within a few seconds the app unloads the page"),
    ("TROUBLESHOOTING.md", "A change shows up within a few seconds of the member's next page"),
    ("SKILL.md", "All three tasks `Running` within about a minute"),  # operator deploy check
}


def _promises(path: Path, base: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    if path.suffix in {".py", ".js"}:
        text = SPLIT.sub("", text)
    found = []
    for m in PROMISE.finditer(text):
        around = " ".join(text[max(0, m.start() - 80) : m.end() + 80].split())
        if not any(path.name == name and words in around for name, words in NOT_A_MESSAGE):
            found.append(f"{path.relative_to(base)}: ...{around}...")
    return found


def test_no_message_promises_a_time() -> None:
    found = []
    for path in [*SRC.rglob("*.py"), *SRC.rglob("*.js"), *SRC.rglob("*.html")]:
        found += _promises(path, SRC)
    assert not found, found


def test_a_message_split_over_lines_is_still_caught() -> None:
    """The sweep reads a string literal split over source lines as the one message it is."""
    text = 'x = (\n    "Try the rest again in a "\n    "few minutes with a new proposal."\n)\n'
    assert PROMISE.search(SPLIT.sub("", text)) and not PROMISE.search(text.replace("\n", " "))


def test_no_doc_or_app_text_promises_a_time() -> None:
    """The member and admin docs, the plugin's skills and the Android app's strings say the same."""
    paths = [
        *(ROOT / "docs").rglob("*.md"),
        *(ROOT / "plugin").rglob("*.md"),
        ROOT / "README.md",
        ROOT / "android" / "app" / "src" / "main" / "res" / "values" / "strings.xml",
    ]
    found = []
    for path in paths:
        if path.name in {"UPGRADING.md", "CHANGELOG.md"}:  # past releases' notes quote the old texts
            continue
        found += _promises(path, ROOT)
    assert not found, found
