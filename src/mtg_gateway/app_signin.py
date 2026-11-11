"""Signing in to the Android app through the phone's browser.

Inside the app, the gateway's pages run in a WebView, and a WebView cannot use passkeys or a
password manager's autofill on the identity provider's sign-in page. So the app signs in the way
native apps do (RFC 8252): the sign-in runs in the phone's browser (a Custom Tab), where passkeys
and autofill work, and the browser hands the result back to the app.

1. ``/login`` asked by the app (its user agent carries ``MTGAssistant/``) shows a small page that
   asks the app to sign in with the browser (``MtgNative.signInWithBrowser``), with a link to
   sign in inside the app instead.
2. The app makes a random verifier, keeps it, and opens
   ``/login?next=...&app_challenge=<SHA-256 of the verifier>`` in the browser. That address shows
   a page first (``confirm_body``): an app asked to sign in to this gateway, Continue only if you
   just pressed Sign in in the gateway's app. Any app on the phone can open that address, and
   without the page the browser went straight to the identity provider's real sign-in page, where
   a person who types their password hands that app the result (the scheme leg below). Only
   Continue goes on to the provider: a post carrying a one-use token that this browser alone
   holds in a cookie (``AppSignins.start_leg``), so neither a link nor another site's page can
   press it for the person.
3. After the identity provider, ``/auth/callback`` sets no cookie in the browser. It keeps a
   one-time code (two minutes, in memory) bound to the challenge and sends the browser to
   ``RETURN_PATH?code=...`` on the gateway's own address. Where a browser on the phone has an
   Auth Tab, the app opened the sign-in in one that returns on that https address: it hands the
   address back to the app and closes, with no tap, but only for an app the gateway vouches for in
   ``/.well-known/assetlinks.json`` (app_page.py). In a Custom Tab the address loads a page with a
   button that opens the app through an ``intent:`` link pinned to the app's package ID. The page
   follows that link once by itself only after a sign-in made during the leg (see below): any app
   can open ``/login?app_challenge=...`` with a plain link, and a browser still signed in at the
   provider passes it straight through, so otherwise the page waits for the member's tap under its
   warning. Chrome opens an ``intent:`` link only from a navigation a person started, which that
   script is not, so when the app says it opened a Custom Tab (``app_return=intent``) the callback
   itself redirects to the same link after such a sign-in, with the page as the link's fallback.
   That gives no app more than the scheme leg below already gives under the same proof.
4. The app posts the code with its verifier to ``/login/app`` from its WebView; only then is the
   browser session created, with its cookie set in the WebView.

Only when the browser reports that it could not confirm the app for that address (Digital Asset
Links failed or timed out), or the gateway's address can't take an https Auth Tab return (a port,
say), does the app try again with an Auth Tab on its own scheme (``app_return=scheme``): the
callback then sends the browser to ``mtgassistant-signin://signin?code=...``. An Auth Tab returns
a scheme to whichever app opened it, and any app on the phone could open one at
``/login?app_challenge=...&app_return=scheme`` with a verifier of its own: with the browser still
signed in at the provider, a silent pass-through would hand that app a session. So that leg asks
the identity provider for a sign-in of its own (``prompt=login`` and ``max_age=0``), and the
callback does not rely on the provider honouring it: the ID token's ``auth_time`` must be no
earlier than the leg's start, less ``FRESH_SKEW`` (a few seconds: the gateway's and the provider's
clocks must agree, docs/IDP-AUTHENTIK.md). Without that proof (no ``auth_time``, or an older one) no
code is made, the browser goes to ``SCHEME_REFUSED``, and the app finishes in a Custom Tab instead.
An https leg asks for the same after a sign-out on the device (``fresh``); with that proof its
return page opens the app by itself.

The code alone is useless: without the verifier, which never leaves the app that started the
sign-in, ``/login/app`` refuses it, and ``/login/app`` takes posts only from the app (its user
agent, not from another site's page), so nobody can push their own code into someone else's
browser. An app on the phone that starts a sign-in of its own (and so knows its own verifier)
should not receive the code either. The https Auth Tab returns the address only to an app whose
package ID and signing certificate the gateway's assetlinks.json names. The ``intent:`` link
names the gateway's app by package ID, and Android lets only one installed app hold a package
ID; that relies on the browser honouring ``package=`` in ``intent:`` links (Chrome documents
it; other browsers are not verified), so the page also says which app it opens. The scheme leg
hands a code only after a person signed in during it, in a browser page that names the gateway.
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
# app_return=intent: the app opened a Custom Tab, not an Auth Tab (BrowserSignIn.INTENT_RETURN)
INTENT_RETURN = "intent"
CODE_TTL = 120
FRESH_SKEW = 5  # seconds of clock difference allowed between the gateway and the identity provider
# Where a scheme leg ends when the ID token can't show a sign-in made during the leg: no code, so
# nothing for any app; the app then finishes in a Custom Tab (BrowserSignIn.isSchemeRefusal).
SCHEME_REFUSED = f"{SCHEME}://signin?error=sign_in_not_fresh"
MAX_PENDING = 500
# The page between the app's address and the identity provider (confirm_body): where its
# Continue and Cancel post, how long it stays good, and the cookie that binds it to this browser.
# /login itself, so the post that leaves for the provider gets this browser's login key as a GET
# of /login does (app.LoginCookieMiddleware): the callback accepts only the browser holding it.
CONTINUE_PATH = "/login"
LEG_TTL = 600
LEG_COOKIE = "mtg_app_leg"
TOKEN = re.compile(r"[A-Za-z0-9_-]{43}")


def challenge_of(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


@dataclass
class _Pending:
    sub: str
    challenge: str
    next_path: str
    expires: float
    fresh: bool = False  # the return page opens the app by itself (after Continue, see auto_return)
    grant: str | None = None  # where the sign-in's provider grant waits for the app's session
    shown: bool = False  # the browser drew the return page (recorded once, in the member's activity)
    new_sign_in: bool = False  # the ID token shows a sign-in made during the leg (activity's reason)


@dataclass
class _Leg:
    """A browser leg the app asked for, waiting for the person's Continue on the page before the
    identity provider (confirm_body)."""

    challenge: str
    next_path: str
    app_return: str | None
    fresh: bool
    expires: float
    source: str = ""  # the network that asked for it (auth_provider.login_source), "" if unknown


class AppSignins:
    """One-time codes waiting for the app, keyed by the code's SHA-256, and the browser legs
    waiting for Continue, keyed by their token's SHA-256. In memory on purpose: a code lives two
    minutes, a leg ten, and a restart only means signing in again."""

    def __init__(self, ttl: int = CODE_TTL, clock=time.monotonic, leg_ttl: int = LEG_TTL):
        self._ttl = ttl
        self._leg_ttl = leg_ttl
        self._clock = clock
        self._lock = threading.Lock()
        self._codes: dict[str, _Pending] = {}
        self._legs: dict[str, _Leg] = {}

    def start_leg(
        self, challenge: str, next_path: str, app_return: str | None, fresh: bool, source: str = ""
    ) -> str:
        """Keep a leg until the person presses Continue (or Cancel) on the page; the token goes in
        the page's form and in this browser's cookie, and ``take_leg`` wants both. Anyone can ask
        for a leg without signing in, so when the table is full the room is made by the network
        that holds the most legs (its oldest goes), as the pending-login table does: one address
        asking for hundreds cannot push out a member's leg waiting for its Continue."""
        token = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            for k in [k for k, v in self._legs.items() if v.expires <= now]:
                del self._legs[k]
            while len(self._legs) >= MAX_PENDING:
                held: dict[str, int] = {}
                for v in self._legs.values():
                    held[v.source] = held.get(v.source, 0) + 1
                most = max(held, key=lambda k: held[k])
                del self._legs[next(k for k, v in self._legs.items() if v.source == most)]
            self._legs[self._key(token)] = _Leg(
                challenge, next_path, app_return, fresh, now + self._leg_ttl, source
            )
        return token

    def take_leg(self, token: str, cookie: str | None) -> _Leg | None:
        """The leg for ``token`` when this browser's cookie holds the same token, else None. A leg
        is gone after its first press, Continue or Cancel, so a page can't be used twice."""
        if not TOKEN.fullmatch(token or "") or not cookie or not hmac.compare_digest(token, cookie):
            return None
        with self._lock:
            leg = self._legs.pop(self._key(token), None)
        if leg is None or leg.expires <= self._clock():
            return None
        return leg

    @staticmethod
    def _key(code: str) -> str:
        return hashlib.sha256(code.encode("ascii")).hexdigest()

    def issue(
        self,
        sub: str,
        challenge: str,
        next_path: str,
        *,
        fresh: bool = False,
        grant: str | None = None,
        new_sign_in: bool | None = None,
    ) -> str:
        code = secrets.token_urlsafe(32)
        now = self._clock()
        with self._lock:
            for k in [k for k, v in self._codes.items() if v.expires <= now]:
                del self._codes[k]
            while len(self._codes) >= MAX_PENDING:  # oldest first; dicts keep insertion order
                del self._codes[next(iter(self._codes))]
            self._codes[self._key(code)] = _Pending(
                sub,
                challenge,
                next_path,
                now + self._ttl,
                fresh,
                grant,
                new_sign_in=fresh if new_sign_in is None else new_sign_in,
            )
        return code

    def fresh(self, code: str) -> bool:
        """Whether ``code`` was made after a sign-in during its leg (the return page then opens the
        app by itself). Looking does not use the code up."""
        if not CODE.fullmatch(code or ""):
            return False
        with self._lock:
            pending = self._codes.get(self._key(code))
        return pending is not None and pending.expires > self._clock() and pending.fresh

    def first_view(self, code: str) -> _Pending | None:
        """The pending sign-in for ``code`` the first time the browser draws its return page, else
        None (a reload, or no such code). Looking does not use the code up."""
        if not CODE.fullmatch(code or ""):
            return None
        with self._lock:
            pending = self._codes.get(self._key(code))
            if pending is None or pending.shown or pending.expires <= self._clock():
                return None
            pending.shown = True
            return pending

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


def signed_in_since(auth_time: object, started: object, skew: int = FRESH_SKEW) -> bool:
    """Whether the ID token's ``auth_time`` shows the person signed in at the identity provider
    after a scheme leg started at ``started`` (Unix seconds, kept in the login session), allowing
    ``skew`` seconds of clock difference. No auth_time, or no start, is not proof: False."""
    if isinstance(auth_time, bool) or not isinstance(auth_time, (int, float)):
        return False
    try:
        start = int(str(started))
    except ValueError:
        return False
    return auth_time >= start - skew


# Set in the login session by the Continue press on confirm_body (pages.app_continue): the callback
# sees it only for a leg a person confirmed in this browser.
CONTINUED = "app_continued"


def auto_return(*, scheme: bool, continued: bool, new_sign_in: bool) -> bool:
    """Whether the browser's part ends by opening the app by itself (the intent: redirect from the
    callback and the return page's own try), instead of waiting for a tap on the return page.

    The scheme leg (an Auth Tab on the app's own scheme, which returns to whichever app opened it)
    still needs a sign-in made during the leg: ``new_sign_in``. An https or Custom Tab leg opens the
    app by itself after the person's Continue (owner, 2026-11-08: "you're still having to click the
    button in order to be redirected back to the app"). Before the Continue page existed (0.7.46,
    security review A-04) a browser still signed in at the identity provider passed any app's leg
    straight through with nobody there, so only a new sign-in (A-02) could show a person was. Now
    the leg reaches the identity provider only through Continue, a post that carries a one-use
    token this browser alone holds in a cookie, pressed on a page that names the gateway and says
    to continue only after pressing Sign in in its app: the same proof a person is there, at the
    step before the identity provider. What the link then reaches is unchanged: an ``intent:`` link
    naming only the gateway app's package, whose sign-in fails without the verifier that the app
    which started the leg alone holds."""
    if scheme:
        return new_sign_in
    return continued or new_sign_in


def intent_url(code: str, package: str, fallback: str | None = None) -> str:
    """An ``intent:`` link that opens only the app with ``package``; ``code`` is URL-safe base64.
    ``fallback``: the https address a browser opens instead when it can't open the app."""
    extra = f"S.browser_fallback_url={quote(fallback, safe='')};" if fallback else ""
    return f"intent://signin?code={code}#Intent;scheme={SCHEME};package={package};{extra}end"


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


def confirm_body(site: str, token: str) -> str:
    """The page between the app's address and the identity provider: an app asked to sign in, and
    only the person who just pressed Sign in in the gateway's app should go on. Continue and
    Cancel post the leg's token to ``CONTINUE_PATH``. Render it with ``heading=False`` and
    ``smallpage.HEAD``, with the identity provider's origin in the page's form-action."""
    form = (
        f"<form method='post' action='{CONTINUE_PATH}'>"
        f"<input type='hidden' name='leg' value='{html.escape(token)}'>"
        "<button type='submit' name='action' value='continue' class='primary'>Continue</button>"
        "<button type='submit' name='action' value='cancel' class='btn-plain'>Cancel</button></form>"
    )
    return smallpage.panel(
        "Sign in to the app?",
        f"<p>An app asked to sign in to “{html.escape(site)}”.</p>"
        "<p>Continue only if you just pressed <b>Sign in</b> in the MTG Assistant Gateway app. If you "
        "didn't, press Cancel: an app that signs in this way gets access to your account.</p>",
        form,
        "<p class='muted small' style='margin-bottom:0'>Continue opens your identity provider's "
        "sign-in page.</p>",
    )


def canceled_body() -> str:
    """After Cancel, or a page that is no longer good: nothing was signed in, and the way to sign in
    is in the app (Sign in here would start the website's own sign-in)."""
    return smallpage.panel(
        "Sign-in canceled",
        "<p>Nothing was signed in. You can close this tab.</p>",
        extra_html="<p class='muted small' style='margin-bottom:0'>To sign in to the MTG Assistant "
        "Gateway app, go back to it and press <b>Sign in</b> again.</p>",
    )


def return_body(code: str, package: str, *, auto: bool = False) -> str:
    """The browser's last page: a button that opens the app with the one-time code. With ``auto``
    (``auto_return``: after the person's Continue, ``AppSignins.fresh``) app-signin.js follows it
    once by itself where the browser allows that without a tap. The script runs once the page has
    loaded, so the small print is drawn before it leaves."""
    url = html.escape(intent_url(code, package))
    # data-once: what app-signin.js remembers, so it tries once per code without keeping the code
    # itself in the browser's storage (a hash of it, under one fixed key)
    once = hashlib.sha256(code.encode("ascii")).hexdigest()[:32]
    flag = f" data-auto='1' data-once='{once}'" if auto else ""
    button = (
        f"<a class='btn btn-primary' id='app-return' href='{url}'{flag}>"
        "Open the MTG Assistant Gateway app</a>"
    )
    small_print = (
        "<p class='muted small' style='margin-bottom:0'>The link works once, for two minutes. If the app "
        "doesn't open, go back to it and sign in again. You stay signed out in this browser. If you "
        "didn't start this sign-in from the MTG Assistant Gateway app, don't tap the button.</p>"
    )
    return (
        smallpage.panel(
            "Signed in",
            "<p>You're signed in. Go back to the MTG Assistant Gateway app to finish.</p>",
            button,
            small_print,
        )
        + f"<script src='{SCRIPT}' defer></script>"
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
