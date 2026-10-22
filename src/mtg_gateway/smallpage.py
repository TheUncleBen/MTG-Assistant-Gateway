"""The small pages: sign-in and its errors, consent, "Signed out", "Your data was deleted", the
expired-form and too-large answers, 404, 500 and 503.

One look for all of them (the approved Sign-in and error pages mockup): a centred panel with the
title, one or two short sentences, and the way forward as buttons, one of them orange. The words
are plain: no setting names, no raw error codes and nothing a member cannot act on; what the
person who runs the gateway needs goes to the log.

``panel`` builds the page body (render it with ``heading=False`` and ``HEAD`` in ``head_extra``);
``button`` builds the way-forward buttons. ``static/pages.js`` makes a Back button go back one
page when there is one; without scripts its link goes to the page it names.
"""

from __future__ import annotations

import html

# What a member reads when a sign-in cannot be finished for a reason only the gateway's log can
# explain (a refused client secret, a bad ID token, a missing parameter).
SIGNIN_FAILED = (
    "The sign-in couldn't be finished. Try again; if it keeps failing, tell the person who runs this gateway."
)
CANCELED = "You canceled the sign-in."
# A sign-in link that is no longer good: the website's version, and the one for an assistant
# connecting (Claude, ChatGPT, Codex), which goes back to the app instead of signing in again.
EXPIRED_WEB = (
    "This sign-in link has expired, was already used, or was started in a different browser. "
    "You were not signed in."
)
EXPIRED_APP = (
    "This sign-in link has expired, was already used, or was started in a different browser. "
    "Nothing was connected."
)
EXPIRED_APP_BACK = (
    "Go back to Claude, ChatGPT or the app you were connecting, and start connecting again there."
)
EXPIRED_TITLE = "Sign-in link expired"
UNAVAILABLE = "The identity provider is unavailable. Try again later."
IDP_UNREACHABLE = "The sign-in service can't be reached to confirm your access. Try again later."
REFUSED = "The app's sign-in request was refused. Start again from the app."
FORM_EXPIRED = "This form expired. Reload the page and try again."
TOO_LARGE = "That form was too large."
APP_ONLY = "This sign-in can only be finished by the Android app."
APP_EXPIRED = (
    "That sign-in didn't finish: it expired, was already used, or was started somewhere else. "
    "You were not signed in."
)

SCRIPT = "/static/pages.js"

# The panel: 34rem wide, centred; on phones the buttons share the row.
CSS = (
    ".sio{max-width:34rem;margin:1.5rem auto 0}"
    ".sio-p{padding:1.25rem 1.25rem 1rem}"
    ".sio-p h1{font-size:1.6rem;margin:0 0 .6rem}"
    ".sio-p p{margin:.5rem 0}"
    ".sio-p .ui-actions{margin-top:1rem}"
    ".sio-p .notice{margin:.75rem 0}"
    ".sio-p .choice{margin-top:1rem}"
    ".sio-who{display:flex;flex-direction:column;gap:.1rem;font-size:1rem}"
    ".sio-who .app{font-size:1.25rem;font-weight:900;overflow-wrap:anywhere}"
    ".sio-who .host{color:var(--text-muted);overflow-wrap:anywhere}"
    ".sio .ui-meta{font-size:1rem}.sio .ui-meta code{font-size:1em}"
    "@media (max-width:599.98px){.sio{margin-top:.5rem}.sio .ui-meta{grid-template-columns:1fr;gap:.15rem}"
    ".sio .ui-meta dd{margin-bottom:.4rem}.sio-p .ui-actions .btn,.sio-p .ui-actions button{flex:1 1 auto}}"
)
HEAD = f"<style>{CSS}</style><script src='{SCRIPT}' defer></script>"


def button(text: str, href: str, *, primary: bool = False, back: bool = False) -> str:
    """A way-forward button: a link drawn as a button, the one orange one with ``primary``.
    ``back`` makes it go back one page where the browser has one (static/pages.js); ``href`` is
    where it goes otherwise."""
    cls = "btn btn-primary" if primary else "btn btn-plain"
    extra = " data-history-back" if back else ""
    return f"<a class='{cls}' href='{html.escape(href)}'{extra}>{html.escape(text)}</a>"


def home() -> str:
    return button("Home", "/")


def panel(title: str, body_html: str, actions_html: str = "", extra_html: str = "") -> str:
    """The small page's body: the panel with its title, the text (trusted markup), the buttons
    and anything after them (small print). Render with ``heading=False``."""
    acts = f"<div class='ui-actions'>{actions_html}</div>" if actions_html else ""
    return (
        f"<div class='sio'><section class='card sio-p'><h1>{html.escape(title)}</h1>"
        f"{body_html}{acts}{extra_html}</section></div>"
    )


def p(text: str) -> str:
    return f"<p>{html.escape(text)}</p>"


def small(text: str) -> str:
    return f"<p class='muted small'>{html.escape(text)}</p>"


def error_strip(text: str) -> str:
    return f"<div class='notice error' role='alert'>{html.escape(text)}</div>"


def signin_failed(login_href: str = "/login") -> str:
    return panel("Sign-in failed", p(SIGNIN_FAILED), button("Try again", login_href, primary=True) + home())


def signin_canceled(login_href: str = "/login") -> str:
    return panel("Sign-in canceled", p(CANCELED), button("Sign in again", login_href, primary=True) + home())


def signin_unavailable(login_href: str = "/login") -> str:
    return panel(
        "Sign-in unavailable", p(UNAVAILABLE), button("Try again", login_href, primary=True) + home()
    )


def link_expired_web(login_href: str = "/login") -> str:
    return panel(EXPIRED_TITLE, p(EXPIRED_WEB), button("Sign in again", login_href, primary=True))


def link_expired_app() -> str:
    return panel(EXPIRED_TITLE, p(EXPIRED_APP) + p(EXPIRED_APP_BACK), home())


def refused(message: str, status: int = 403) -> str:
    """A sign-in the gateway itself refused for a reason the member can act on (not in the group,
    account disabled, another sign-in provider): the message as it is, with Home."""
    return panel("Sign-in failed", p(message), home())


def login_error(kind: str, message: str, *, assistant: bool, login_href: str = "/login") -> str:
    """The page for an auth_provider.LoginError: by its ``kind``, the expired-link page (the
    assistant's version when an app was connecting, else the website's), the unavailable page, the
    gateway's own refusal with its message, or the plain "couldn't be finished" page."""
    if kind == "expired":
        return link_expired_app() if assistant else link_expired_web(login_href)
    if kind == "unavailable":
        return signin_unavailable(login_href)
    if kind == "refused":
        return refused(message)
    return signin_failed(login_href)


def login_error_title(kind: str) -> str:
    return {"expired": EXPIRED_TITLE, "unavailable": "Sign-in unavailable"}.get(kind, "Sign-in failed")


def form_expired(reload_text: str, reload_href: str, back_href: str, *, nothing: str) -> str:
    """An expired form: the sentence, what was not done ("Nothing was applied."), Reload and
    Back."""
    return panel(
        "Form expired",
        p(FORM_EXPIRED) + f"<p class='muted small' style='margin:0'>{html.escape(nothing)}</p>",
        button(reload_text, reload_href, primary=True) + button("Back", back_href, back=True),
    )


def too_large(back_href: str) -> str:
    return panel("Too large", p(TOO_LARGE), button("Back", back_href, back=True))


__all__ = [
    "APP_EXPIRED",
    "APP_ONLY",
    "CANCELED",
    "CSS",
    "EXPIRED_APP",
    "EXPIRED_APP_BACK",
    "EXPIRED_TITLE",
    "EXPIRED_WEB",
    "FORM_EXPIRED",
    "HEAD",
    "IDP_UNREACHABLE",
    "REFUSED",
    "SIGNIN_FAILED",
    "TOO_LARGE",
    "UNAVAILABLE",
    "button",
    "error_strip",
    "form_expired",
    "home",
    "link_expired_app",
    "link_expired_web",
    "login_error",
    "login_error_title",
    "p",
    "panel",
    "refused",
    "signin_canceled",
    "signin_failed",
    "signin_unavailable",
    "small",
    "too_large",
]
