"""No real credential is committed: no private key, no token in a well-known provider format, no
keystore and no environment file with values. Test fixtures use obviously fake values; a line that
must hold a fake credential in a real-looking shape says so with the marker FAKE_SECRET_MARKER on the
same line. Runs in CI on every push, with no setup."""

from __future__ import annotations

import re

from tests.test_repo_privacy import ROOT, _text_lines, _tracked_files

FAKE_SECRET_MARKER = "not-a-real-secret"

# Shapes that are a credential wherever they appear (provider documentation formats).
SECRET_SHAPES: dict[str, re.Pattern[str]] = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY"),
    "GitHub token": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{50,})"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
    "Google API key": re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"),
    "Slack token": re.compile(r"\bxox[abprs]-[0-9A-Za-z-]{10,}"),
    "Anthropic or OpenAI key": re.compile(r"\bsk-(?:ant-)?[A-Za-z0-9_-]{32,}"),
    "Stripe key": re.compile(r"\b[rs]k_live_[0-9A-Za-z]{20,}"),
    "Docker Hub token": re.compile(r"\bdckr_pat_[A-Za-z0-9_-]{20,}"),
    "JSON web token": re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
}

# Files that must never be committed, whatever they hold.
FORBIDDEN_FILES = re.compile(
    r"(?:^|/)(?:\.env(?:\.[\w-]+)?|[\w.-]+\.(?:jks|keystore|p12|pfx|pem|key)|id_(?:rsa|ed25519|ecdsa))$"
)
# Example environment files are documentation: their names end in .example.
ALLOWED_FILES = re.compile(r"\.example$")


def find_secrets(lines) -> list[str]:
    found = []
    for rel, n, line in lines:
        if FAKE_SECRET_MARKER in line:
            continue
        for kind, shape in SECRET_SHAPES.items():
            if shape.search(line):
                found.append(f"{rel}:{n} ({kind})")
    return found


def test_the_shapes_catch_what_they_are_for() -> None:
    fake = "".join(chr(ord("a") + i % 26) for i in range(40))
    lines = [
        ("a", 1, "-----BEGIN OPENSSH " + "PRIVATE KEY-----"),
        ("b", 1, "token = 'ghp_" + fake + "'"),
        ("c", 1, "AKIA" + "ABCDEFGHIJKLMNOP"),
        ("d", 1, "key: sk-ant-" + fake),
        ("e", 1, "ghp_" + fake + "  # " + FAKE_SECRET_MARKER),
        ("f", 1, "an ordinary line about a token"),
    ]
    assert find_secrets(lines) == [
        "a:1 (private key)",
        "b:1 (GitHub token)",
        "c:1 (AWS access key)",
        "d:1 (Anthropic or OpenAI key)",
    ]


def test_no_credential_is_in_any_tracked_file() -> None:
    found = find_secrets(_text_lines())
    assert not found, "these lines hold what looks like a real credential:\n" + "\n".join(found)


def test_no_keystore_key_or_environment_file_is_tracked() -> None:
    names = [p.relative_to(ROOT).as_posix() for p in _tracked_files()]
    bad = [n for n in names if FORBIDDEN_FILES.search(n) and not ALLOWED_FILES.search(n)]
    assert not bad, "these files must not be in the repository:\n" + "\n".join(bad)
