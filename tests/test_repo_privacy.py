"""The public repository carries none of the owner's own data (whole-application audit, row 3): not
their Archidekt deck ids, not their name. Code comments say "the owner" where a decision is theirs;
tests use neutral ids (12345678) and names. Card data from Scryfall or Archidekt may name a real
artist who shares the first name, which is not the owner's data."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# Every text file of the repository, wherever it is: source, the Android app, tests, docs, deploy
# files and the plugin. Build output and downloaded assets are not the repository's own text.
SKIP_DIRS = {".git", "__pycache__", "node_modules", "vendor", "build", ".gradle", ".venv", ".idea"}
TEXT = {
    ".py",
    ".js",
    ".css",
    ".html",
    ".md",
    ".txt",
    ".json",
    ".yml",
    ".yaml",
    ".toml",
    ".kt",
    ".kts",
    ".xml",
    ".sh",
    ".csv",
    ".dek",
    ".cfg",
    ".ini",
    ".env",
    ".example",
    ".properties",
    ".svg",
}
# Built from pieces so this file does not carry them itself.
DECK_IDS = re.compile(r"(?<!\d)(?:" + "|".join(["2709" + "3869", "2709" + "5317"]) + r")(?!\d)")
NAME = re.compile(r"\b" + "B" + "en" + r"\b")
# Card artists in recorded card data: real people, not the owner.
ARTISTS = re.compile(r"\b" + "B" + r"en (?:Thompson|Wootten|Hill|Maier)\b")


def _files() -> list[Path]:
    out = []
    for path in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in path.relative_to(ROOT).parts):
            continue
        if path.is_file() and (path.suffix in TEXT or path.name in {"Dockerfile", "VERSION"}):
            out.append(path)
    return out


def test_no_deck_id_or_name_of_the_owner_is_in_the_repository() -> None:
    found: list[str] = []
    files = _files()
    assert len(files) > 500, len(files)  # the walk saw the repository, not an empty folder
    for path in files:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for n, line in enumerate(text.splitlines(), 1):
            if DECK_IDS.search(line) or NAME.search(ARTISTS.sub("", line)):
                found.append(f"{path.relative_to(ROOT)}:{n}: {line.strip()[:120]}")
    assert not found, "\n".join(found)
