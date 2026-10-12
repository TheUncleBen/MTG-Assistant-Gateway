"""Notifications to a member's phone through an ntfy server (https://ntfy.sh/docs/).

Off unless the operator sets ``MTG_NTFY_URL`` (and, for a server with access control, an access
token in the Docker secret file ``MTG_NTFY_TOKEN_FILE``). Each member then opts in on their Account
page and gets a topic of their own: a long random name (or one they choose) that nobody else
can guess. The member subscribes to it in the ntfy app; on a self-hosted server with
``auth-default-access: deny-all`` the operator grants them read access to that topic only.

What is sent: a proposal waiting for the member's approval, a deck report that finished (Forge
runs that waited in the queue included), a price alert they set, and their scheduled summary.
Each message carries the least that is useful: a short title and line, and a link to the
gateway page. Deck contents are never sent. The token is sent only in the Authorization header
and never logged; a failed publish is logged with the HTTP status alone.

The Account page's Notifications card is here too (``account_card_html``, ``account_action``):
the opt-in, the events, the topic, Send test, and the member's scheduled summary (summary.py),
which is offered only when a notification server is set up, since ntfy is how it is sent.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
import secrets
import time
from typing import TYPE_CHECKING, Any

import httpx

from .db import Database

if TYPE_CHECKING:
    from .app import AppState

logger = logging.getLogger(__name__)

EVENTS = ("proposals", "reports", "prices")  # the kinds a member can switch off one by one
SUMMARY_CHOICES = ("off", "daily", "weekly")
# The Account page's notices for the Notifications card and its summary (pages.py shows them).
ACCOUNT_OK = {
    "notify_saved": "Notifications saved.",
    "notify_off": "Notifications are off.",
    "notify_topic": "You have a new topic. Subscribe to it in the ntfy app; the old one gets nothing more.",
    "notify_test_sent": "Test sent. It should reach your phone in a few seconds.",
    "summary_saved": "Summary choice saved.",
}
ACCOUNT_ERR = {
    "notify_test_failed": "The test could not be sent. Check that notifications are on; if they are, the "
    "notification server did not accept it (the operator can see why in the gateway's log).",
    "notify_topic_invalid": "That topic is not allowed or another member has it. Use mtg- and then 12 to "
    "60 letters, digits, - or _, or leave it empty for a random one.",
    "notify_test_wait": "A test was just sent. Wait a minute before sending another.",
    "summary_off": "Summaries are sent through notifications, and this gateway has no notification server.",
}
# Members' topics all start with mtg-, so an operator's ntfy grant can be limited to 'mtg-*' and
# a member can never make the gateway publish to the operator's other topics.
TOPIC_RE = re.compile(r"^mtg-[A-Za-z0-9_-]{12,60}$")
TIMEOUT = 10.0
TEST_COOLDOWN = 60.0  # seconds between one member's Send test presses


def new_topic() -> str:
    """A topic nobody can guess: 'mtg-' and 128 random bits."""
    return "mtg-" + secrets.token_urlsafe(16)


def prefs(db: Database, sub: str) -> dict[str, Any]:
    """The member's notification and summary choices (defaults when they never chose)."""
    with db._lock:
        row = db._conn.execute("SELECT * FROM notify_prefs WHERE sub = ?", (sub,)).fetchone()
    if row is None:
        return {
            "ntfy_on": False,
            "ntfy_topic": None,
            "on_proposals": True,
            "on_reports": True,
            "on_prices": True,
            "summary_every": "off",
            "summary_sent_day": None,
        }
    out = dict(row)
    for k in ("ntfy_on", "on_proposals", "on_reports", "on_prices"):
        out[k] = bool(out[k])
    return out


def save_prefs(db: Database, sub: str, **values: Any) -> None:
    """Store some of the member's choices; the others keep their value. A topic another member
    already has is refused (ValueError)."""
    allowed = {"ntfy_on", "ntfy_topic", "on_proposals", "on_reports", "on_prices", "summary_every"}
    values = {k: v for k, v in values.items() if k in allowed}
    if "summary_every" in values and values["summary_every"] not in SUMMARY_CHOICES:
        raise ValueError("summary_every must be off, daily or weekly")
    topic = values.get("ntfy_topic")
    if topic is not None and not TOPIC_RE.fullmatch(topic):
        raise ValueError("a topic is mtg- and then 12 to 60 letters, digits, - or _")
    with db.tx() as c:
        if topic is not None:
            taken = c.execute(
                "SELECT 1 FROM notify_prefs WHERE ntfy_topic = ? AND sub != ?", (topic, sub)
            ).fetchone()
            if taken:
                raise ValueError("that topic is taken; choose another")
        c.execute(
            "INSERT INTO notify_prefs (sub, updated_at) SELECT ?, ? WHERE EXISTS "
            "(SELECT 1 FROM users WHERE sub = ?) ON CONFLICT(sub) DO NOTHING",
            (sub, int(time.time()), sub),
        )
        for k, v in values.items():
            c.execute(
                f"UPDATE notify_prefs SET {k} = ?, updated_at = ? WHERE sub = ?",
                (int(v) if isinstance(v, bool) else v, int(time.time()), sub),
            )


TAGS = {
    "proposals": "memo",
    "reports": "bar_chart",
    "prices": "chart_with_upwards_trend",
    "summary": "calendar",
}


class Notifier:
    """Publishes to the operator's ntfy server for members who opted in."""

    def __init__(self, ntfy_url: str | None, token: str | None, db: Database, public_url: str, *, http=None):
        self.url = (ntfy_url or "").rstrip("/")
        self._token = token
        self.db = db
        self.public_url = public_url.rstrip("/")
        self._http: httpx.AsyncClient | None = http
        self._tasks: set[asyncio.Task[Any]] = set()  # held so a pending send is not collected
        self.sent = 0
        self._last_test: dict[str, float] = {}

    def test_allowed(self, sub: str) -> bool:
        """True (and the member's next test held back for TEST_COOLDOWN) unless they pressed
        Send test less than TEST_COOLDOWN seconds ago."""
        now = time.monotonic()
        if now - self._last_test.get(sub, -TEST_COOLDOWN) < TEST_COOLDOWN:
            return False
        if len(self._last_test) > 1000:
            self._last_test = {k: t for k, t in self._last_test.items() if now - t < TEST_COOLDOWN}
        self._last_test[sub] = now
        return True

    def __repr__(self) -> str:  # the token stays out of any printed or logged object
        return f"Notifier(url={self.url!r})"

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    async def publish(self, topic: str, title: str, message: str, path: str = "/", tags: str = "") -> bool:
        """One message to ``topic``; True when the server took it. Never raises."""
        if not self.enabled or not topic:
            return False
        body: dict[str, Any] = {
            "topic": topic,
            "title": title[:120],
            "message": message[:1000],
            "click": f"{self.public_url}{path}",
        }
        if tags:
            body["tags"] = [tags]
        headers = {"Content-Type": "application/json"}
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        http = self._http or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
        try:
            r = await http.post(self.url + "/", json=body, headers=headers)
        except httpx.HTTPError as exc:
            logger.warning("ntfy publish failed: %s", exc.__class__.__name__)
            return False
        finally:
            if self._http is None:
                await http.aclose()
        if r.status_code >= 300:
            logger.warning("ntfy publish refused: HTTP %d", r.status_code)
            return False
        self.sent += 1
        return True

    async def send(self, sub: str, event: str, title: str, message: str, path: str = "/") -> bool:
        """Publish to the member's topic when they opted in and want this kind of event
        (``event``: proposals, reports, prices, summary or test)."""
        if not self.enabled:
            return False
        p = prefs(self.db, sub)
        if not p["ntfy_on"] or not p["ntfy_topic"]:
            return False
        if event in EVENTS and not p[f"on_{event}"]:
            return False
        return await self.publish(p["ntfy_topic"], title, message, path, tags=TAGS.get(event, ""))

    def soon(self, sub: str, event: str, title: str, message: str, path: str = "/") -> None:
        """Send in the background: the caller (a proposal, a report) never waits for ntfy."""
        if not self.enabled:
            return
        try:
            task = asyncio.get_running_loop().create_task(self.send(sub, event, title, message, path))
        except RuntimeError:  # no running loop (a synchronous caller): nothing is sent
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def drain(self) -> None:
        """Wait for background sends (tests, shutdown)."""
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)


# -- Account page: the Notifications card, with the summary ----------------------------------------

SUMMARY_CSS = (
    "<style>#summary pre{white-space:pre-wrap;font-family:inherit;margin:.5rem 0 0;padding:.6rem .8rem;"
    "background:var(--surface-2);border-radius:3px;overflow-wrap:anywhere}</style>"
)


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""), quote=True)


def _summary_html(state: AppState, sub: str, p: dict[str, Any], csrf_in: str) -> str:
    """The summary part of the card (only with a notification server: ntfy is how it is sent).
    The latest summary is built only for a member who chose one, not on every page view."""
    every = p["summary_every"]
    period = "day" if every == "daily" else "week"
    opts = "".join(
        f"<option value='{v}'{' selected' if v == every else ''}>{label}</option>"
        for v, label in (("off", "Off"), ("daily", "Every day"), ("weekly", "Every week (Mondays)"))
    )
    what = (
        "your watched prices, deck values, finished reports and proposals"
        if state.prices is not None
        else "your finished reports and proposals"
    )
    latest = ""
    if every != "off" and state.summaries is not None:
        built = state.summaries.build(sub, period)
        latest = (
            f"<h4>{'Today' if period == 'day' else 'The last 7 days'}</h4><pre>{_esc(built['text'])}</pre>"
        )
    return (
        f"<div class='summary' id='summary'>{SUMMARY_CSS}<h3>Summary</h3>"
        f"<p>A plain summary of {what}, written by the gateway from its own data (no AI) and sent "
        "through your notifications when they are on. Your assistant can read it with "
        "<code>get_summary</code> and write you its own version on a schedule.</p>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='summary_save'>"
        "<label for='s-every'>Send my summary</label>"
        f"<select id='s-every' name='summary_every'>{opts}</select>"
        f"<button class='primary'>Save summary</button></form>{latest}</div>"
    )


def account_card_html(state: AppState, sub: str, csrf_in: str) -> str:
    """The Account page's Notifications card: a note when the operator set up no ntfy server,
    otherwise the member's choices, their topic, Send test and the summary schedule."""
    n = state.notifier
    if n is None or not n.enabled:
        watched = "a watched card crosses your alert, " if state.prices is not None else ""
        return (
            "<div class='card' id='notifications'><h2>Notifications</h2><p class='muted'>This gateway has no "
            "notification server set up. Its operator can add one (an ntfy server); then you can get a "
            f"message on your phone when a proposal waits for you, a deck report finishes, {watched}"
            "or your summary is ready.</p></div>"
        )
    p = prefs(state.db, sub)

    def box(name: str, label: str) -> str:
        return (
            f"<label class='check'><input type='checkbox' name='{name}' value='1'"
            f"{' checked' if p[name] else ''}> <span>{_esc(label)}</span></label>"
        )

    topic = p["ntfy_topic"] or ""
    where = (
        f"<dl class='meta'><dt>Server</dt><dd><code>{_esc(n.url)}</code></dd>"
        f"<dt>Your topic</dt><dd><code>{_esc(topic)}</code></dd></dl>"
        "<p class='muted small'>In the ntfy app, add a subscription to this topic on this server (use "
        "another server, not ntfy.sh, if the server above is not ntfy.sh). If the operator protects the "
        "server, sign in in the app with the user they gave you. Keep the topic to yourself: anyone who "
        "knows it and may read the server can read your notifications.</p>"
        if topic
        else ""
    )
    test = (
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='notify_test'>"
        "<button>Send test</button></form>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='notify_new_topic'>"
        "<button class='inline'>New random topic</button></form>"
        if p["ntfy_on"] and topic
        else ""
    )
    # With watchlists off there is no price alert to choose; a choice made earlier is kept as it was.
    prices_box = (
        box("on_prices", "A watched card crossed my price alert")
        if state.prices is not None
        else ("<input type='hidden' name='on_prices' value='1'>" if p["on_prices"] else "")
    )
    return (
        "<div class='card' id='notifications'><h2>Notifications</h2>"
        "<p>Get a short message on your phone through ntfy. Each one says only what happened and links "
        "to the page here: never your deck's cards.</p>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='notify_save'>"
        f"{box('ntfy_on', 'Send me notifications')}"
        "<fieldset><legend>When</legend>"
        f"{box('on_proposals', 'A proposal waits for my approval')}"
        f"{box('on_reports', 'A deck report finished (Forge runs that waited included)')}"
        f"{prices_box}</fieldset>"
        "<label for='n-topic'>Topic (empty: a random one nobody can guess)</label>"
        f"<input id='n-topic' name='ntfy_topic' maxlength='64' autocomplete='off' value='{_esc(topic)}' "
        "pattern='mtg-[A-Za-z0-9_\\-]{12,60}' title='mtg- and then 12 to 60 letters, digits, - or _'>"
        "<button class='primary'>Save notifications</button></form>"
        f"{where}<div class='form-actions'>{test}</div>"
        f"{_summary_html(state, sub, p, csrf_in)}</div>"
    )


async def account_action(state: AppState, sub: str, action: str, data: dict[str, str]) -> str | None:
    """Handle the Notifications card's forms on the Account page. Returns the redirect's query
    (``ok=...`` or ``err=...``), or None for an action that is not one of these."""
    if action == "notify_save":
        on = data.get("ntfy_on") == "1"
        topic = data.get("ntfy_topic", "").strip()
        current = prefs(state.db, sub)["ntfy_topic"]
        if on and not topic:
            topic = current or new_topic()
        try:
            save_prefs(
                state.db,
                sub,
                ntfy_on=on,
                on_proposals=data.get("on_proposals") == "1",
                on_reports=data.get("on_reports") == "1",
                on_prices=data.get("on_prices") == "1",
                **({"ntfy_topic": topic} if topic else {}),
            )
        except ValueError:
            return "err=notify_topic_invalid"
        return "ok=notify_saved" if on else "ok=notify_off"
    if action == "notify_new_topic":
        save_prefs(state.db, sub, ntfy_topic=new_topic())
        return "ok=notify_topic"
    if action == "notify_test":
        n = state.notifier
        p = prefs(state.db, sub)
        if n is None or not n.enabled or not p["ntfy_on"] or not p["ntfy_topic"]:
            return "err=notify_test_failed"
        if not n.test_allowed(sub):
            return "err=notify_test_wait"
        ok = await n.send(
            sub,
            "test",
            "Test from your MTG gateway",
            "Notifications work. This is the only test message.",
            "/account",
        )
        return "ok=notify_test_sent" if ok else "err=notify_test_failed"
    if action == "summary_save":
        # Summaries go out only through ntfy: without a server there is no schedule to choose.
        if state.notifier is None or not state.notifier.enabled:
            return "err=summary_off"
        try:
            save_prefs(state.db, sub, summary_every=data.get("summary_every", "off"))
        except ValueError:
            return "err=notify_topic_invalid"
        return "ok=summary_saved"
    return None
