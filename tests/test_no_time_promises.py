"""Busy, limit and error messages never promise a time ("try again in a minute"): the gateway can't
know when Archidekt, Scryfall, the identity provider or its own queues will have room again. A wait
the gateway does know (a Retry-After in seconds, a fixed cooldown) may still be named."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src" / "mtg_gateway"
PROMISE = re.compile(
    r"try again (?:in a (?:minute|moment|few)|shortly)|try the rest again in a few"
    r"|again in a few minutes|within a minute or two|reload (?:this page |the page )?in a (?:minute|moment)"
    r"|wait a few minutes and try|wait a minute(?:,| and| \()|for a few minutes\.|waits a few minutes"
    r"|refills over a few minutes",
    re.IGNORECASE,
)


def test_no_message_promises_a_time() -> None:
    found = []
    for path in [*SRC.rglob("*.py"), *SRC.rglob("*.js"), *SRC.rglob("*.html")]:
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PROMISE.search(line):
                found.append(f"{path.relative_to(SRC)}:{n}: {line.strip()}")
    assert not found, found


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
        for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if PROMISE.search(line):
                found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()}")
    assert not found, found
