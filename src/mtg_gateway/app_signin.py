"""Signing in to the Android app through the phone's browser.

Inside the app, the gateway's pages run in a WebView, and a WebView cannot use passkeys or a
password manager's autofill on the identity provider's sign-in page. So the app signs in the way
native apps do (RFC 8252): the sign-in runs in the phone's browser (a Custom Tab), where passkeys
and autofill work, and the browser hands the result back to the app.

1. ``/login`` asked by the app (its user agent carries ``MTGAssistant/``) shows a small page that
   asks the app to sign in with the browser (``MtgNative.signInWithBrowser``), with a link to
   sign in inside the app instead.
2. The app makes a random verifier, keeps it, and opens
   ``/login?next=...&app_challenge=<SHA-256 of the verifier>`` in the browser.
3. After the identity provider, ``/auth/callback`` sets no cookie in the browser. It keeps a
   one-time code (two minutes, in memory) bound to the challenge. Where a browser on the phone
   has an Auth Tab, the app (0.7.46) opened the sign-in in one with ``app_return=scheme``: the
   browser is sent to ``mtgassistant-signin://signin?code=...``, which the Auth Tab hands back to
   the app that opened it, and closes, with no tap and no Digital Asset Links check. Otherwise
   (an older app, or a browser without Auth Tabs) the browser is sent to ``RETURN_PATH?code=...``
   on the gateway's own address: an older app's https Auth Tab takes it there, but only for an app
   the gateway vouches for in ``/.well-known/assetlinks.json`` (app_page.py); any other browser
   loads a page that opens the app once by itself where the browser allows, and keeps a button
   that opens it through an ``intent:`` link pinned to the app's package ID.
4. The app posts the code with its verifier to ``/login/app`` from its WebView; only then is the
   browser session created, with its cookie set in the WebView.

A leg that returns on the app's scheme always asks the identity provider for a sign-in of its
own (``prompt=login``). An Auth Tab returns the scheme to whichever app opened it, and any app on
the phone could open one at ``/login?app_challenge=...&app_return=scheme`` with a verifier of its
own: with the browser still signed in at the provider, a silent pass-through would hand that app
a session. Asking for the sign-in means a person has to sign in, in a browser page that names
the gateway, for any app to get a code.

The code alone is useless: without the verifier, which never leaves the app that started the
sign-in, ``/login/app`` refuses it, and ``/login/app`` takes posts only from the app (its user
agent, not from another site's page), so nobody can push their own code into someone else's
browser. An app on the phone that starts a sign-in of its own (and so knows its own verifier)
should not receive the code either. The Auth Tab returns the address only to an app whose
package ID and signing certificate the gateway's assetlinks.json names. The ``intent:`` link
names the gateway's app by package ID, and Android lets only one installed app hold a package
ID; that relies on the browser honouring ``package=`` in ``intent:`` links (Chrome documents
it; other browsers are not verified), so the link also waits for a tap and says which app it opens.
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

from . import smallpage

CHALLENGE = re.compile(r"[A-Za-z0-9_-]{43}")  # base64url SHA-256, no padding
VERIFIER = re.compile(r"[A-Za-z0-9_-]{43,128}")
CODE = re.compile(r"[A-Za-z0-9_-]{43}")
SCHEME = "mtgassistant-signin"
SCRIPT = "/static/app-signin.js"
RETURN_PATH = "/login/app-return"  # where the Auth Tab hands back; BrowserSignIn.RETURN_PATH in the app
SCHEME_RETURN = "scheme"  # app_return=scheme: back on SCHEME (BrowserSignIn.SCHEME_RETURN in the app)
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


def scheme_url(code: str) -> str:
    """Where a leg started with ``app_return=scheme`` ends; ``code`` is URL-safe base64."""
    return f"{SCHEME}://signin?code={code}"


def intent_url(code: str, package: str) -> str:
    """An ``intent:`` link that opens only the app with ``package``; ``code`` is URL-safe base64."""
    return f"intent://signin?code={code}#Intent;scheme={SCHEME};package={package};end"


def handoff_body(next_path: str, fresh: bool) -> str:
    """The page ``/login`` shows inside the app (one of the small pages, smallpage.py):
    app-signin.js asks the app to sign in with the browser; the link signs in inside the app
    instead. Render it with ``heading=False`` and ``smallpage.HEAD``."""
    fresh_q = "&fresh=1" if fresh else ""
    inapp = f"/login?next={quote(next_path, safe='/')}&inapp=1{fresh_q}"
    return (
        f"<div id='app-signin' data-next='{html.escape(next_path)}' data-fresh='{'1' if fresh else '0'}'>"
        + smallpage.panel(
            "Sign in",
            "<p>Sign-in opens in your phone's browser, where your passkey and password manager work. "
            "When you're done there, tap <b>Open the MTG Assistant Gateway app</b> and you're back "
            "here, signed in.</p>",
            "<button class='primary' type='button' id='app-signin-go'>Sign in with the browser</button>",
            f"<p class='muted small' style='margin-bottom:0'><a href='{html.escape(inapp)}'>Sign in inside "
            "the app instead</a> (passkeys and autofill may not work there).</p>",
        )
        + f"</div><script src='{SCRIPT}'></script>"
    )


def return_body(code: str, package: str) -> str:
    """The browser's last page: a button that opens the app with the one-time code. app-signin.js
    follows it once by itself where the browser allows that without a tap."""
    url = html.escape(intent_url(code, package))
    return smallpage.panel(
        "Signed in",
        "<p>You're signed in. Go back to the MTG Assistant Gateway app to finish.</p>",
        f"<a class='btn btn-primary' id='app-return' href='{url}'>Open the MTG Assistant Gateway app</a>"
        f"<script src='{SCRIPT}'></script>",
        "<p class='muted small' style='margin-bottom:0'>The link works once, for two minutes. If the app "
        "doesn't open, go back to it and sign in again. You stay signed out in this browser. If you didn't "
        "start this sign-in from the MTG Assistant Gateway app, don't tap the button.</p>",
    )


def expired_body(next_path: str) -> str:
    return smallpage.panel(
        "Sign-in failed",
        smallpage.p(smallpage.APP_EXPIRED),
        smallpage.button("Sign in again", f"/login?next={quote(next_path, safe='/')}", primary=True),
    )


__all__ = [
    "AppSignins",
    "CHALLENGE",
    "CODE",
    "RETURN_PATH",
    "challenge_of",
    "expired_body",
    "handoff_body",
    "intent_url",
    "return_body",
    "scheme_url",
]
