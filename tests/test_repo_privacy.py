"""The public repository carries none of the owner's own data: not their Archidekt deck ids, not their
name or their decks' names, not their gateway's address. Code comments say "the owner" where a decision
is theirs; tests use neutral ids (12345678) and names.

The private values themselves never live in the repository, not even in pieces, and not in
GitHub either. They are kept in a plain file outside the repository, named by the environment variable
MTG_PRIVATE_VALUES_FILE, with two lines:

  deck_ids = 11111111, 22222222      Archidekt deck ids, digits only
  names = Alex, My Deck Name         names and phrases, matched as whole words in any case, with
                                     spaces, "-" or "_" between the words or nothing

The maintainers run this test with that file before every push. Without it (CI, a contributor's
clone) the generic checks below still run and this one has nothing private to look for.

Card data from Scryfall or Archidekt may name a real artist who shares a first name with someone; a
name followed by one of the artist surnames below is that artist, not private data."""

from __future__ import annotations

import functools
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Surnames of card artists in recorded card data: "<first name> <surname>" is the artist.
ARTIST_SURNAMES = ("Thompson", "Wootten", "Hill", "Maier")

# Hosts the repository may name in a URL: public services it talks to or links to, by registrable
# domain (the last two labels). Anything else must be a reserved name (RFC 2606/6761) such as
# gateway.example.com, so a real gateway's address cannot slip into code, docs or tests.
PUBLIC_DOMAINS = {
    "agent-plugins.org",
    "android.com",
    "anthropic.com",
    "apache.org",
    "archidekt.com",
    "cardhoarder.com",
    "cardmarket.com",
    "chatgpt.com",
    "claude.ai",
    "claude.com",
    "claudemcpcontent.com",
    "contributor-covenant.org",
    "discord.com",
    "discordapp.com",
    "edhplay.com",
    "edhrec.com",
    "github.com",
    "github.io",
    "githubusercontent.com",
    "google.com",
    "googleapis.com",
    "gravatar.com",
    "json-schema.org",
    "keepachangelog.com",
    "microsoftonline.com",
    "modelcontextprotocol.io",
    "moxfield.com",
    "mtgjson.com",
    "npmjs.org",
    "ntfy.sh",
    "openai.com",
    "polyformproject.org",
    "scryfall.com",
    "scryfall.io",
    "semver.org",
    "tcgplayer.com",
    "w3.org",
    "wizards.com",
    "youtu.be",
    "zitadel.cloud",
}
# Look-alike or throwaway hosts that tests feed in on purpose (an open-redirect target, a host that
# only ends like a trusted one): nobody's server.
TEST_INPUT_HOSTS = {
    "a.com",
    "e.co",
    "x.junk",
    "notarchidekt.com",
    "notclaude.ai",
    "notdiscord.com",
    "mtg.example.com.evil.net",
}
RESERVED_TLDS = {"example", "test", "invalid", "localhost", "local", "internal"}
RESERVED_DOMAINS = {"example.com", "example.org", "example.net"}
URL_HOST = re.compile(r"https?://([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
# A self-hosted service's address (three labels or more) written without a scheme
# (MTG_PUBLIC_URL=mtg.<domain>, an Authentik host in a guide).
SERVICE_HOST = re.compile(
    r"(?<![\w.-])(?:mtg|gateway|auth|idp|sso|ntfy|authentik)(?:\.[A-Za-z0-9-]+)+"
    r"\.(?:com|net|org|io|dev|app|co|uk|de|me|xyz|cloud|info|gg|eu|us|ca|nl|fr)(?![\w-])",
    re.IGNORECASE,
)


def _tracked_files() -> list[Path]:
    """Every file git tracks: source, the Android app, tests, docs, deploy files and the plugin."""
    out = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
    return [ROOT / name for name in out.stdout.decode().split("\0") if name]


@functools.cache
def _text_lines() -> tuple[tuple[str, int, str], ...]:
    lines: list[tuple[str, int, str]] = []
    for path in _tracked_files():
        if not path.is_file():  # deleted in the working tree, not yet from the index
            continue
        data = path.read_bytes()
        if b"\0" in data:
            continue  # an image, a font, a jar: not text
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            continue
        rel = path.relative_to(ROOT).as_posix()
        lines.extend((rel, n, line) for n, line in enumerate(text.splitlines(), 1))
    return tuple(lines)


def _private_values() -> tuple[list[str], list[str]]:
    """The owner's deck ids and names from the file MTG_PRIVATE_VALUES_FILE names, if any."""
    path = os.environ.get("MTG_PRIVATE_VALUES_FILE", "")
    if not path:
        return [], []
    found: dict[str, list[str]] = {"deck_ids": [], "names": []}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        key, sep, value = raw.partition("=")
        if sep and key.strip() in found:
            found[key.strip()] = [v.strip() for v in value.split(",") if v.strip()]
    return found["deck_ids"], found["names"]


def private_patterns(deck_ids: list[str], names: list[str]) -> list[re.Pattern[str]]:
    """One pattern per private value. A deck id matches only as a whole number; a name matches as
    whole words in any case, written with spaces, "-", "_" or nothing between its words (prose, a
    URL slug or an identifier), except where it is a card artist's first name."""
    patterns = []
    for deck_id in deck_ids:
        if not deck_id.isdigit():
            raise ValueError("deck_ids holds Archidekt deck ids: digits, separated by commas")
        patterns.append(re.compile(rf"(?<!\d){deck_id}(?!\d)"))
    artists = "|".join(ARTIST_SURNAMES)
    for name in names:
        body = r"[\s_-]*".join(re.escape(w) for w in re.split(r"[\s_-]+", name) if w)
        patterns.append(
            re.compile(rf"(?<![A-Za-z0-9]){body}(?![A-Za-z0-9])(?! (?:{artists})\b)", re.IGNORECASE)
        )
    return patterns


def find_private(lines, patterns: list[re.Pattern[str]]) -> list[str]:
    # Only the place is reported, never the line, so a CI log does not repeat the secret.
    return [f"{rel}:{n}" for rel, n, line in lines if any(p.search(line) for p in patterns)]


def _reserved(host: str) -> bool:
    labels = host.lower().rstrip(".").split(".")
    return labels[-1] in RESERVED_TLDS or ".".join(labels[-2:]) in RESERVED_DOMAINS


def test_the_scan_sees_the_repository() -> None:
    files = _tracked_files()
    assert len(files) > 500, len(files)  # the scan saw the repository, not an empty folder
    seen = {rel for rel, _n, _line in _text_lines()}
    assert {"src/mtg_gateway/app.py", "README.md", ".github/workflows/ci.yml"} <= seen


def test_every_host_in_a_url_is_a_public_service_or_a_reserved_name() -> None:
    found = []
    for rel, n, line in _text_lines():
        for host in URL_HOST.findall(line):
            host = host.lower()
            if host.replace(".", "").isdigit() or _reserved(host) or host in TEST_INPUT_HOSTS:
                continue  # an IP address, an example name or a test's look-alike
            if ".".join(host.split(".")[-2:]) not in PUBLIC_DOMAINS:
                found.append(f"{rel}:{n}: {host}")
    assert not found, (
        "a URL names a host that is neither a known public service nor a reserved example name "
        "(use gateway.example.com):\n" + "\n".join(found)
    )


def test_no_self_hosted_service_address_outside_the_example_domains() -> None:
    found = [
        f"{rel}:{n}: {m.group(0)}"
        for rel, n, line in _text_lines()
        for m in SERVICE_HOST.finditer(line)
        if not _reserved(m.group(0)) and m.group(0).lower() not in TEST_INPUT_HOSTS
    ]
    assert not found, "use gateway.example.com (or auth.example.com):\n" + "\n".join(found)


def test_the_private_scan_finds_what_it_is_given() -> None:
    """The matcher on made-up values, so a run with the secrets is known to look properly."""
    patterns = private_patterns(["11112222"], ["Alex", "Silver Dawn"])
    lines = [
        ("a", 1, "https://archidekt.com/decks/11112222/x"),
        ("b", 1, "deck 111122223 is someone else's"),
        ("c", 1, "ALEX said so"),
        ("d", 1, "alexander and Alexandria are other words"),
        ("e", 1, "Illustrated by Alex Thompson"),
        ("f", 1, "/decks/silver-dawn/"),
        ("g", 1, "SILVER_DAWN"),
        ("h", 1, "silverdawn"),
        ("i", 1, "a silver dawning"),
    ]
    assert find_private(lines, patterns) == ["a:1", "c:1", "f:1", "g:1", "h:1"]
    with pytest.raises(ValueError):
        private_patterns(["12ab"], [])


def test_no_deck_id_or_name_of_the_owner_is_in_the_repository() -> None:
    deck_ids, names = _private_values()
    found = find_private(_text_lines(), private_patterns(deck_ids, names))
    assert not found, "the owner's deck id or name is in these places:\n" + "\n".join(found)
