"""Defence against double-clickjacking on the pages whose one button grants something.

The consent page (Approve hands an MCP client a code for the member's account) and the
proposal review page (Apply writes to Archidekt) cannot be framed (``frame-ancestors 'none'``
and ``X-Frame-Options: DENY``), but framing is not needed: a hostile page asks for a
double-click and, between the two clicks, swaps a top-level window (a popup's ``opener``, or
the popup itself) to the gateway page, so the second click lands on the button.

Two layers, both small:

1. The buttons are rendered ``disabled`` and ``static/clickguard.js`` (loaded with
   ``script-src 'self'``; no inline script, so the strict CSP stays) enables them only once
   the page has been visible and focused for ``GUARD_DWELL_MS`` and the person has moved the
   pointer or pressed a key on it, at least ``GUARD_SETTLE_MS`` before. Leaving the page
   (blur, hidden, pagehide) disables them again and restarts both clocks. A click that lands
   right after a window swap therefore hits a disabled button and does nothing.

2. Without JavaScript the disabled form is hidden and a ``<noscript>`` copy with working
   buttons is shown instead (a page with scripts on never renders it, so an attacker cannot
   fall back to it). Every form carries a signed render time (``shown``); the server refuses
   a submit that arrives less than ``MIN_FORM_AGE`` seconds after the page was rendered and
   shows the page again. With JavaScript on that never triggers, because the script waits
   longer than that before enabling anything.

A POST without a ``shown`` field is accepted: it cannot come from the gateway's own page (which
always carries one), and a forged request would already need the page's CSRF token, which only
the page itself holds. Scripts and tests that post the form directly keep working that way.
"""

from __future__ import annotations

import hashlib
import hmac
import html
import time

GUARD_SCRIPT = "/static/clickguard.js"
# The script's timings (kept in step with static/clickguard.js, which reads them from data-*).
GUARD_DWELL_MS = 800
GUARD_SETTLE_MS = 300
# Server-side minimum between rendering a guarded form and accepting its submit.
MIN_FORM_AGE = 0.75
# A render stamp older than this is still accepted (the form's own expiry is checked elsewhere).
_MAX_STAMP_AGE = 7 * 86400


def form_stamp(secret: str, scope: str, now: float | None = None) -> str:
    """Signed render time for a guarded form, bound to ``scope`` (the login or proposal)."""
    ms = int((time.time() if now is None else now) * 1000)
    sig = hmac.new(secret.encode(), f"shown:{scope}:{ms}".encode(), hashlib.sha256).hexdigest()[:32]
    return f"{ms}.{sig}"


def submitted_too_soon(secret: str, scope: str, value: str | None, now: float | None = None) -> bool:
    """True when a guarded form's submit must be refused and the page shown again: its
    ``shown`` stamp is malformed, not ours, or younger than MIN_FORM_AGE. No stamp at all is
    accepted (see the module docstring)."""
    if value is None:
        return False
    ms_text, _, sig = value.partition(".")
    if not ms_text.isdigit() or len(ms_text) > 15:
        return True
    expected = hmac.new(secret.encode(), f"shown:{scope}:{ms_text}".encode(), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig.encode(), expected.encode()):
        return True
    age = (time.time() if now is None else now) - int(ms_text) / 1000
    return age < MIN_FORM_AGE or age > _MAX_STAMP_AGE


def guarded_form(
    *,
    hidden: dict[str, str],
    buttons: list[tuple[str, str, str]],
    action: str | None = None,
    extra: str = "",
) -> str:
    """HTML for a form whose submit buttons start disabled until ``clickguard.js`` enables them,
    plus a ``<noscript>`` copy with working buttons for browsers without JavaScript.

    ``buttons`` are (value, label, class) of ``<button name='action'>``; ``extra`` is markup
    appended inside the button row (a plain link, for instance). Values must already be safe;
    ``hidden`` values and labels are escaped here.
    """
    act = f" action='{html.escape(action)}'" if action else ""
    fields = "".join(
        f"<input type='hidden' name='{html.escape(k)}' value='{html.escape(v)}'>" for k, v in hidden.items()
    )

    def row(disabled: bool) -> str:
        attrs = " disabled data-guard" if disabled else ""
        return "".join(
            f"<button type='submit' name='action' value='{html.escape(value)}' "
            f"class='{html.escape(cls)}'{attrs}>{html.escape(label)}</button>"
            for value, label, cls in buttons
        )

    return (
        f"<form method='post'{act} class='guarded' data-dwell='{GUARD_DWELL_MS}' "
        f"data-settle='{GUARD_SETTLE_MS}'>{fields}"
        f"<div class='choice'>{row(True)}{extra}</div>"
        "<p class='muted small guard-hint'>The buttons unlock a moment after this page opens, once "
        "you move the pointer, touch the screen or press a key here. On a phone, tap once to "
        "unlock, then tap your choice.</p></form>"
        "<noscript><style>form.guarded{display:none}</style>"
        f"<form method='post'{act}>{fields}<div class='choice'>{row(False)}{extra}</div></form></noscript>"
        f"<script src='{GUARD_SCRIPT}' defer></script>"
    )
