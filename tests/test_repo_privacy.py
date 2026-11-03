"""The public repository carries none of the owner's own data: not their Archidekt deck ids, not their
name or their decks' names, not their gateway's address. Code comments say "the owner" where a decision
is theirs; tests use neutral ids (12345678) and names.

The private values themselves never live in the repository, not even in pieces, and not in
GitHub either. They are kept in a plain file outside the repository, named by the environment variable
MTG_PRIVATE_VALUES_FILE, with two lines:

  deck_ids = 11111111, 22222222      Archidekt deck ids, digits only
  names = Alex, My Deck Name         names and phrases, matched as whole words in any case, with
                                     spaces, "-" or "_" between the words or nothing

The owner's own values are checked locally only: a maintainer runs this test with that file before
every push. CI and a contributor's clone don't have it, and must never be given it (no secret, no
variable, no file in the repository). There the owner test runs the generic checks instead, which
need no private value: no Archidekt deck id in a real-looking form (only made-up ids such as 42 or
12345678 appear) and no host in the deployment examples that is not a reserved example name or a
public service. It never skips.

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
# A deck id written where it names a deck: a deck's address (Archidekt's or the gateway's), a
# ?deck= query, or a deck_id / deckId value. Six digits or more is the form real Archidekt ids take.
DECK_REFERENCE = re.compile(
    r"(?:/decks/|[?&]deck=|(?<![A-Za-z])deck_?id[\"']?\s*[:=,(]\s*[\"']?)(\d+)(?!\d)", re.IGNORECASE
)
REAL_LOOKING_DIGITS = 6
# The deployment examples: every host they name is an example name or a public service.
DEPLOY_EXAMPLES = ("deploy/", "docs/DEPLOY")
DEPLOY_HOST = re.compile(
    r"(?:(?<=://)|(?<![\w.@/-]))([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:com|net|org|io|dev|app|co|uk|de|me|xyz|cloud|info|gg"
    r"|eu|us|ca|nl|fr|sh|ai))(?![\w-])",
    re.IGNORECASE,
)
# Where container images come from (an image name, not a gateway's address).
REGISTRY_DOMAINS = {"ghcr.io", "gcr.io", "docker.io", "quay.io"}


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


def made_up_deck_id(digits: str) -> bool:
    """Whether a deck id is plainly made up: short (42, 500), a run of digits (123456, 1234567890123)
    or built from three digits or fewer (11112222, 4242424, 99000000). Real Archidekt ids are six to
    eight digits with no such pattern."""
    if len(digits) < REAL_LOOKING_DIGITS or len(set(digits)) <= 3:
        return True
    runs = "0123456789" * 3
    return digits in runs or digits in runs[::-1]


def real_looking_deck_ids(lines) -> tuple[int, list[str]]:
    """How many deck ids the lines name, and the places of the ones that look real. Only the
    place is reported, never the id, so a CI log does not repeat it."""
    seen = 0
    found = []
    for rel, n, line in lines:
        for m in DECK_REFERENCE.finditer(line):
            seen += 1
            if not made_up_deck_id(m.group(1)):
                found.append(f"{rel}:{n}")
    return seen, found


def deploy_example_hosts(lines, files: set[str]) -> tuple[int, list[str]]:
    """How many hosts the deployment examples name, and the ones that are neither a reserved
    example name, a public service nor an image registry (a file name such as make-secrets.sh is
    not a host)."""
    seen = 0
    found = []
    for rel, n, line in lines:
        if not rel.startswith(DEPLOY_EXAMPLES):
            continue
        for m in DEPLOY_HOST.finditer(line):
            host = m.group(1).lower()
            if host in files:
                continue
            seen += 1
            domain = ".".join(host.split(".")[-2:])
            if not (_reserved(host) or domain in PUBLIC_DOMAINS or domain in REGISTRY_DOMAINS):
                found.append(f"{rel}:{n}: {host}")
    return seen, found


def _tracked_names() -> set[str]:
    return {p.name.lower() for p in _tracked_files()}


def test_the_generic_scans_find_what_they_are_given() -> None:
    """The made-up-id rule and the deploy host scan on made-up lines. A real-looking id is built
    here from a formula, so this file itself names none."""
    real = "".join(str((d * 7 + 3) % 10) for d in range(7))
    lines = [
        ("a", 1, f"https://archidekt.com/decks/{real}/my-deck"),
        ("b", 1, f"see https://mtg.example.com/decks/{real}"),
        ("c", 1, f'{{"deck_id": "{real}"}}'),
        ("d", 1, f"deckId = {real}"),
        ("e", 1, "https://archidekt.com/decks/12345678/x and /decks/42 and /decks/11112222"),
        ("f", 1, 'deck_id="1234567890123", deck_id=99000000'),
        ("g", 1, f"a card seen in {real} decks"),  # a count, not a deck
        ("h", 1, f"/decks?deck={real}"),
    ]
    seen, found = real_looking_deck_ids(lines)
    assert found == ["a:1", "b:1", "c:1", "d:1", "h:1"] and seen == 10, (seen, found)
    home, own = "mtg." + "my-own-home.net", "gateway." + "someones.dev"  # in pieces: not a host here
    deploy = [
        ("deploy/x.env", 1, "MTG_PUBLIC_URL=https://mtg.example.com"),
        ("deploy/x.env", 2, "image: ghcr.io/someone/gateway:1"),
        ("deploy/x.yml", 3, f"rule: Host(`{home}`)"),
        ("docs/DEPLOY.md", 4, f"server_name {own};"),
        ("docs/OTHER.md", 5, f"{home} is not a deploy example"),
        ("deploy/run.sh", 6, "run make-secrets.sh first"),
    ]
    seen, found = deploy_example_hosts(deploy, {"make-secrets.sh"})
    assert seen == 4 and found == [f"deploy/x.yml:3: {home}", f"docs/DEPLOY.md:4: {own}"], (seen, found)


def test_no_deck_id_in_a_real_looking_form_is_in_the_repository() -> None:
    """Tests, docs and fixtures use made-up deck ids only (made_up_deck_id)."""
    seen, found = real_looking_deck_ids(_text_lines())
    assert seen > 50, seen  # the scan saw the deck ids the tests and docs use
    assert not found, "a deck id that looks real (use a made-up one such as 12345678 or 42):\n" + "\n".join(
        found
    )


def test_the_deployment_examples_name_only_example_hosts() -> None:
    seen, found = deploy_example_hosts(_text_lines(), _tracked_names())
    assert seen > 5, seen  # the examples were read
    assert not found, "use mtg.example.com (or auth.example.com):\n" + "\n".join(found)


def test_no_deck_id_or_name_of_the_owner_is_in_the_repository() -> None:
    """The owner's deck ids and names are checked locally only, on a maintainer's machine with
    MTG_PRIVATE_VALUES_FILE set: they are never put in the repository, CI or GitHub secrets.
    Without the file (CI) this test still checks something real and never skips: it runs the
    generic checks, which need no private value (no real-looking deck id anywhere, no host in the
    deployment examples other than an example name or a public service)."""
    deck_ids, names = _private_values()
    lines = _text_lines()
    if deck_ids or names:
        found = find_private(lines, private_patterns(deck_ids, names))
        assert not found, "the owner's deck id or name is in these places:\n" + "\n".join(found)
    seen_ids, real_ids = real_looking_deck_ids(lines)
    seen_hosts, hosts = deploy_example_hosts(lines, _tracked_names())
    assert seen_ids > 50 and seen_hosts > 5, (seen_ids, seen_hosts)
    assert not real_ids, "a deck id that looks real is in these places:\n" + "\n".join(real_ids)
    assert not hosts, "a deployment example names a real host:\n" + "\n".join(hosts)
