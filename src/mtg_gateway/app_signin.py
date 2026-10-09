"""Signing in to the Android app through the phone's browser.

Inside the app, the gateway's pages run in a WebView, and a WebView cannot use passkeys or a
password manager's autofill on the identity provider's sign-in page. So the app signs in the way
native apps do (RFC 8252): the sign-in runs in the phone's browser (a Custom Tab), where passkeys
and autofill work, and the browser hands the result back to the app.

1. ``/login`` asked by the app (its user agent carries ``MTGAssistant/``) shows a small page that
   asks the app to sign in with the browser (``MtgNative.signInWithBrowser``). An app too old to
   know that call is sent on to the in-app sign-in, as before.
2. The app makes a random verifier, keeps it, and opens
   ``/login?next=...&app_challenge=<SHA-256 of the verifier>`` in the browser.
3. After the identity provider, ``/auth/callback`` sets no cookie in the browser. It keeps a
   one-time code (two minutes, in memory) bound to the challenge, and shows a page whose button
   opens the app through an ``intent:`` link pinned to the app's package ID.
4. The app posts the code with its verifier to ``/login/app`` from its WebView; only then is the
   browser session created, with its cookie set in the WebView.

The code alone is useless: without the verifier, which never leaves the app that started the
sign-in, ``/login/app`` refuses it, and ``/login/app`` takes posts only from the app (its user
agent, not from another site's page), so nobody can push their own code into someone else's
browser. An app on the phone that starts a sign-in of its own (and so knows its own verifier)
should not receive the code either: the ``intent:`` link names the gateway's app by package ID,
and Android lets only one installed app hold a package ID. That relies on the browser honouring
``package=`` in ``intent:`` links (Chrome documents it; other browsers are not verified), so the
link also waits for a tap and says which app it opens. A verified https App Link would not rely
on the browser; it needs per-gateway setup (docs/ANDROID.md, section 12).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import html
import re
import secrets
import threading
import time
from dataclasses import dataclass
from urllib.parse import quote

CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")  # base64url SHA-256, no padding
VERIFIER = re.compile(r"[A-Za-z0-9_-]{43,128}")
CODE = re.compile(r"[A-Za-z0-9_-]{43}")
SCHEME = "mtgassistant-signin"
SCRIPT = "/static/app-signin.js"
CODE_TTL = 120
MAX_PENDING = 500


def challenge_of(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass
class _Pending:
    sub: str
    challenge: str
    next_path: str
    expires: float


class AppSignins:
    """One-time codes waiting for the app, keyed by the code's SHA-256. In memory on purpose: a
    code lives two minutes, and a restart only means signing in again."""

    def __init__(self, ttl: int = CODE_TTL, clock=time.monotonic):
        self._ttl = ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._codes: dict[str, _Pending] = {}

    @staticmethod
    def _key(code: str) -> str:
        return hashlib.sha256(code.encode("ascii")).hexdigest()

    def issue(self, sub: str, challenge: str, next_path: str) -> str:
        code = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            for k in [k for k, v in self._codes.items() if v.expires <= now]:
                del self._codes[k]
            while len(self._codes) >= MAX_PENDING:  # oldest first; dicts keep insertion order
                del self._codes[next(iter(self._codes))]
            self._codes[self._key(code)] = _Pending(sub, challenge, next_path, now + self._ttl)
        return code

    def redeem(self, code: str, verifier: str) -> _Pending | None:
        """The pending sign-in for ``code`` if ``verifier`` matches its challenge, else None. A code
        is gone after its first try, right or wrong, so it can't be guessed at."""
        if not CODE.fullmatch(code or "") or not VERIFIER.fullmatch(verifier or ""):
            return None
        with self._lock:
            pending = self._codes.pop(self._key(code), None)
        if pending is None or pending.expires <= self._clock():
            return None
        if not hmac.compare_digest(challenge_of(verifier), pending.challenge):
            return None
        return pending


def intent_url(code: str, package: str) -> str:
    """An ``intent:`` link that opens only the app with ``package``; ``code`` is URL-safe base64."""
    return f"intent://signin?code={code}#Intent;scheme={SCHEME};package={package};end"


def handoff_body(next_path: str, fresh: bool) -> str:
    """The page ``/login`` shows inside the app: app-signin.js asks the app to sign in with the
    browser, or, in an app too old for that, follows the in-app sign-in link."""
    fresh_q = "&fresh=1" if fresh else ""
    inapp = f"/login?next={quote(next_path, safe='/')}&inapp=1{fresh_q}"
    return (
        f"<div class='card' id='app-signin' data-next='{html.escape(next_path)}' "
        f"data-fresh='{'1' if fresh else '0'}' data-inapp='{html.escape(inapp)}'>"
        "<p>Sign-in opens in your phone's browser, where your passkey and password manager work. "
        "When you're done there, tap <b>Open the MTG Assistant Gateway app</b> and you're back "
        "here, signed in.</p>"
        "<div class='actions'><button class='primary' type='button' id='app-signin-go'>"
        "Sign in with the browser</button></div>"
        f"<p class='muted small'><a href='{html.escape(inapp)}'>Sign in inside the app instead</a> "
        "(passkeys and autofill may not work there).</p></div>"
        f"<script src='{SCRIPT}'></script>"
    )


def return_body(code: str, package: str) -> str:
    """The browser's last page: a button that opens the app with the one-time code."""
    url = html.escape(intent_url(code, package))
    return (
        "<div class='card'><p>You're signed in. Go back to the MTG Assistant Gateway app to finish.</p>"
        f"<div class='actions'><a class='btn btn-primary' id='app-return' href='{url}'>"
        "Open the MTG Assistant Gateway app</a></div>"
        "<p class='muted small'>The link works once, for two minutes. If the app doesn't open, "
        "go back to it and sign in again. You stay signed out in this browser. If you didn't "
        "start this sign-in from the MTG Assistant Gateway app, don't tap the button.</p></div>"
    )


def expired_body(next_path: str) -> str:
    return (
        "<div class='card'><p>That sign-in didn't finish: it expired, was already used, or was "
        "started somewhere else. Nothing was signed in.</p>"
        f"<div class='actions'><a class='btn btn-primary' href='/login?next={quote(next_path, safe='/')}'>"
        "Sign in again</a></div></div>"
    )


__all__ = [
    "AppSignins",
    "CHALLENGE",
    "challenge_of",
    "expired_body",
    "handoff_body",
    "intent_url",
    "return_body",
]
