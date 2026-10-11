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
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
from typing import Any

import httpx

from .db import Database

logger = logging.getLogger(__name__)

EVENTS = ("proposals", "reports", "prices")  # the kinds a member can switch off one by one
SUMMARY_CHOICES = ("off", "daily", "weekly")
# The Account page's notices for the Notifications and Summary cards (pages.py shows them).
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

    def subscribe_url(self, topic: str) -> str:
        return f"{self.url}/{topic}"

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
