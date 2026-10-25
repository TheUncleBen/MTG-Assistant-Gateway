"""Form tokens masked with fresh randomness on every page load (BREACH, compress.py): the page
carries the mask, then the token's key XOR the mask, so no two loads show the same characters,
while every one of them checks against the same key."""

from __future__ import annotations

import hmac
import secrets


def mask(key: bytes) -> str:
    pad = secrets.token_bytes(len(key))
    return pad.hex() + bytes(a ^ b for a, b in zip(pad, key, strict=True)).hex()


def matches(key: bytes, given: str) -> bool:
    """Whether ``given`` is ``key`` masked (or ``key`` itself, unmasked, from a page drawn before
    masking: it is the same secret). Lower-case hex digits only."""
    if not given or len(given) not in (2 * len(key), 4 * len(key)) or given.strip("0123456789abcdef"):
        return False
    raw = bytes.fromhex(given)
    if len(raw) == 2 * len(key):
        pad, masked = raw[: len(key)], raw[len(key) :]
        raw = bytes(a ^ b for a, b in zip(pad, masked, strict=True))
    return hmac.compare_digest(raw, key)
