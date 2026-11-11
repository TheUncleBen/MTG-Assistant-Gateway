"""Notifications to a member's Android app, through the ntfy server bundled in the stack.

The stack runs ntfy next to the gateway on a private network (``MTG_NTFY_URL``, with the access
token in the Docker secret file ``MTG_NTFY_TOKEN_FILE``); nothing else can reach it. Each member
who turns notifications on gets a topic of their own: ``mtg-``, 96 random bits and a short mark
that only this gateway can make (:func:`new_topic`), never shown.
The gateway publishes to it, and the member's Android app reads it through the gateway's own
address (:data:`STREAM_PATH`), holding the topic as its key: the gateway checks the key, then
relays that one topic from ntfy. So phones need no ntfy app, no account on ntfy and no proxy
host of their own, and ntfy's cache gives a phone that was offline what it missed.

What is sent: changes an assistant left waiting for the member's press, grouped per deck (one
message per deck, replaced as the count changes and removed when nothing waits), a deck report
that finished (Forge runs that waited in the queue included), a price alert they set, a new alert
on Archidekt (alerts_push.py) and their scheduled summary. Each message carries the least that
is useful: a short title and line, and a link to the gateway page. Deck contents are never sent.
The token is sent only in the Authorization header and never logged;
a failed publish is logged with the HTTP status alone.

The Account page's Push notifications card is here too (``account_card_html``, ``account_action``):
the opt-in (one switch for the whole account), the events, Send test, the app's connect button,
and the member's scheduled summary (summary.py), which is offered only when a notification
server is set up, since the notifications are how it is sent.
"""

from __future__ import annotations

import asyncio
import base64
import functools
import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import time
import weakref
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import TYPE_CHECKING, Any

import anyio
import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from . import dbwait
from .busy import outage
from .db import Database
from .theme import esc as _esc
from .theme import icon, in_app
from .ui import label

if TYPE_CHECKING:
    from .app import AppState

logger = logging.getLogger(__name__)

EVENTS = ("proposals", "reports", "prices", "alerts")  # the kinds a member can switch off one by one
# "New alerts on Archidekt" (alerts_push.py) is kept on the account (account_prefs), not in
# notify_prefs, so it needed no database upgrade; on unless the member unticked it.
ALERTS_PREF = "notify.alerts"
SUMMARY_CHOICES = ("off", "daily", "weekly")


class Kind:
    """One type of alert (0.7.47): what the Alerts page and the settings call it (``name``), the
    word a push title starts with (``word``: "Price drop · Sol Ring"), its icon, the Android
    channel the app shows it on (``channel``: the app maps the message's tag, the kind's key, to
    its channel), and where it goes by default (``default``: off, list or push)."""

    __slots__ = ("key", "name", "word", "icon", "default", "hint", "admin")

    def __init__(
        self, key: str, name: str, word: str, icon: str, default: str, hint: str, *, admin: bool = False
    ) -> None:
        self.key, self.name, self.word, self.icon = key, name, word, icon
        self.default, self.hint, self.admin = default, hint, admin


# Every type of alert, in the order the settings list them. "waiting" is read live (what waits
# for the member's press now); every other kind is kept (Database.add_gateway_alert).
KINDS: dict[str, Kind] = {
    k.key: k
    for k in (
        Kind(
            "waiting",
            "Waiting for you",
            "Review",
            "proposals",
            "push",
            "An assistant left changes for you to apply or reject (one per deck).",
        ),
        Kind(
            "applied",
            "Applied for you",
            "Applied",
            "check",
            "list",
            "An assistant changed a deck without asking, as your approval mode allows.",
        ),
        Kind("report", "Deck reports", "Report ready", "report", "push", "A test or deck report finished."),
        Kind("price", "Price alerts", "Price alert", "stats", "push", "A watched card crossed your alert."),
        Kind(
            "archidekt",
            "From Archidekt",
            "Archidekt",
            "bell",
            "push",
            "A comment, a reply, a new follower or news in your Archidekt alerts.",
        ),
        Kind(
            "account",
            "New assistant",
            "New assistant",
            "shield",
            "push",
            "An assistant connected to your account: each new connection, never a renewal of its access.",
        ),
        Kind("summary", "Summaries", "Summary", "clock", "push", "Your daily or weekly summary."),
        Kind(
            "gateway",
            "Gateway (admins)",
            "Gateway",
            "settings",
            "push",
            "A newer gateway release, or a nightly backup that failed.",
            admin=True,
        ),
    )
}
DELIVERY = ("off", "list", "push")
DELIVERY_WORDS = {"off": "Off", "list": "Alerts only", "push": "Alerts and push"}
DELIVER_PREF = "notify.deliver"  # account_prefs: {kind: off | list | push}, kinds the member chose
# The old push switches (notify_prefs on_*) and the kind each one was (before 0.7.47).
OLD_SWITCHES = {
    "waiting": "on_proposals",
    "report": "on_reports",
    "price": "on_prices",
    "archidekt": "on_alerts",
}
# What an old switch left unticked meant: no push, and no place in the bell for a price alert or a
# report; the waiting changes and Archidekt's alerts still showed there.
UNTICKED = {"waiting": "list", "report": "off", "price": "off", "archidekt": "list"}
# Old event names callers pass to Notifier.send, and the kind each one is.
EVENT_KINDS = {
    "proposals": "waiting",
    "reports": "report",
    "prices": "price",
    "alerts": "archidekt",
    "summary": "summary",
}


def kind_of(event: str) -> str:
    return EVENT_KINDS.get(event, event)


def push_title(kind: str, subject: str = "", word: str = "") -> str:
    """A push title the member tells apart at a glance: the type first, then what it is about
    ("Price drop · Darksteel Angel", "Report ready · Atraxa"); the type alone with no subject."""
    k = KINDS.get(kind)
    head = word or (k.word if k else "MTG Gateway")
    subject = " ".join(str(subject or "").split())
    return f"{head} · {subject}"[:120] if subject else head


def deliveries(db: Database, sub: str, row: dict[str, Any] | None = None) -> dict[str, str]:
    """Where each kind of alert goes for the member: off, list (the Alerts page only) or push
    (the Alerts page and their phone, when push notifications are on). A kind they never chose
    follows its old switch (off when they had unticked it), else the kind's default."""
    saved = db.get_pref(sub, DELIVER_PREF)
    saved = saved if isinstance(saved, dict) else {}
    out = {}
    for key, k in KINDS.items():
        v = saved.get(key)
        if v in DELIVERY:
            out[key] = v
            continue
        old = OLD_SWITCHES.get(key)
        if old == "on_alerts":
            was = db.get_pref(sub, ALERTS_PREF)
            out[key] = UNTICKED[key] if was is False else k.default
        elif old and row is not None and not bool(row[old]):
            out[key] = UNTICKED[key]
        else:
            out[key] = k.default
    return out


def save_deliveries(db: Database, sub: str, chosen: dict[str, str]) -> None:
    """Store the member's choices (unknown kinds and values are left out), and keep the old
    switches in step: on while the kind is pushed."""
    now = deliveries(db, sub, _row(db, sub))
    for key, v in chosen.items():
        if key in KINDS and v in DELIVERY:
            now[key] = v
    db.set_pref(sub, DELIVER_PREF, now)
    save_prefs(db, sub, **{old: now[k] == "push" for k, old in OLD_SWITCHES.items()})


# The Account page's notices for the Notifications card and its summary (pages.py shows them).
ACCOUNT_OK = {
    "notify_saved": "Push notifications saved.",
    "notify_off": "Push notifications are off.",
    "notify_topic": "Your phones are disconnected. To connect a phone again, open the Account page in the "
    "gateway's app on that phone and press Get push notifications on this phone.",
    "notify_test_sent": "The test message is sent. Each phone you connected should show it.",
    "summary_saved": "Summary choice saved.",
    "summary_sent": "The summary is sent. Each phone you connected should show it.",
}
ACCOUNT_ERR = {
    "notify_test_failed": "The test could not be sent. Check that push notifications are on; if they "
    "are, the notification server did not accept it (the operator can see why in the gateway's log).",
    "notify_test_wait": "A test was just sent. The gateway sends one test a minute, so wait before sending "
    "another.",
    "summary_invalid": "Choose Off, Every day or Every week.",
    "summary_off": "Summaries are sent through push notifications, and this gateway has no notification "
    "server.",
    "summary_send_wait": "A summary was just sent. The gateway sends one a minute, so wait before sending "
    "another.",
    "summary_send_failed": "The summary could not be sent. Check that push notifications are on; if "
    "they are, the notification server did not accept it (the operator can see why in the gateway's log).",
}
# Members' topics all start with mtg-, so an operator's ntfy grant can be limited to 'mtg-*' and
# a member can never make the gateway publish to the operator's other topics.
TOPIC_RE = re.compile(r"^mtg-[A-Za-z0-9_-]{12,60}$")
# A topic the gateway made (new_topic): the only kind an app may use as its key, since a topic is
# all the app shows to read the member's notifications.
STRONG_TOPIC_RE = re.compile(r"^mtg-[A-Za-z0-9_-]{22}$")
# A topic in a request address (one path segment, never the host): what TopicRedactor hides.
_TOPIC_IN_URL_RE = re.compile(r"(?<=[^/]/)mtg-[A-Za-z0-9_-]{12,60}")


class TopicRedactor(logging.Filter):
    """Hides members' topics in httpx's request lines, which are logged at DEBUG: a topic is the
    key to a member's notifications, so it never reaches the log."""

    def filter(self, record: logging.LogRecord) -> bool:
        line = record.getMessage()
        if "mtg-" in line:
            record.msg, record.args = _TOPIC_IN_URL_RE.sub("mtg-…", line), None
        return True


TIMEOUT = 10.0
# A background message (nobody waits for it: a deck's waiting changes, a finished report, a price
# or Archidekt alert) that ntfy could not take for a passing reason (no connection, a time-out, 429
# or 5xx: the server restarting) is sent again after each of these pauses, then given up and logged.
# A refusal (401, 403, 400) is not sent again. The bell keeps its alerts either way.
PUSH_RETRY_DELAYS = (10.0, 60.0, 300.0)
TEST_COOLDOWN = 60.0  # seconds between one member's Send test presses

# The Android app's connection (notify.add_stream_route): the app sends the member's topic as its
# key, and the gateway relays that topic from ntfy as one JSON object per line.
STREAM_PATH = "/app/notifications/stream"
# The app's Mark read on a message: the alert is read for the member everywhere (0.7.47)
READ_PATH = "/app/notifications/read"
# What the app is given of each ntfy event: the kinds it acts on and the fields it shows.
STREAM_EVENTS = frozenset({"open", "keepalive", "message", "message_delete", "message_clear"})
STREAM_FIELDS = ("id", "time", "event", "sequence_id", "title", "message", "click", "tags")
STREAM_KEEPALIVE = 25.0  # seconds: under nginx's (and so NPM's) default 60 s read timeout
STREAM_RECHECK = 60.0  # seconds between checks that the key still belongs to a member who is on
STREAM_STALE = 150.0  # seconds without a line from ntfy (it sends one every 45 s) before giving up
STREAM_MAX_PER_KEY = 4  # connections at once with one key (a phone reconnecting overlaps its old one)
# App connections at once for the whole gateway. Each one is a subscription of the gateway's single
# ntfy visitor, so the stack files set ntfy's NTFY_VISITOR_SUBSCRIPTION_LIMIT above this (250).
STREAM_MAX_TOTAL = 200
# ``since``: an ntfy message id, or a Unix time; anything else starts from now.
SINCE_RE = re.compile(r"^(?:[A-Za-z0-9]{8,24}|\d{9,11})$")
# A deck's grouped message (Notifier.deck_waiting): a later one with the same id replaces it.
# One kept alert's message is "a-<its id>": reading or dismissing the alert anywhere withdraws it.
GROUP_RE = re.compile(r"^(?:deck|a)-\d{1,12}$")
NEW_DECKS = "0"  # the group of new decks an assistant proposed: no deck of their own yet

RECOUNT_DELAY = 1.0  # seconds: changes close together (an apply all, a sweep) are counted once
# The Alerts page keeps the newest alerts per member that they have not dismissed (0.7.47: every
# kind but "waiting", which is read live, and a test, which is for the phone alone).
KEPT_ALERTS = 200
WITHDRAW_MAX = 100  # Mark all read or Clear all withdraws the newest this many messages from the phone
NOT_KEPT = ("waiting", "test")
# the Alerts page's Type filter: value, words
ALERT_TYPES = (("", "Every type"),) + tuple((k.key, k.name) for k in KINDS.values())


def waiting_text(count: int, assistant: str = "") -> str:
    """The line under a deck's waiting title: how many, from which assistant, and what to do."""
    what = "1 change" if count == 1 else f"{count} changes"
    by = f" from {assistant}" if assistant else ""
    return f"{what}{by} to apply or reject."


def waiting_title(count: int, deck_name: str) -> str:
    """The title of a deck's message: "Review · Atraxa" (the count is in its line)."""
    name = " ".join(str(deck_name or "").split())[:80] or "Unnamed deck"
    return push_title("waiting", name)


def new_decks_title(count: int) -> str:
    """The title of the message for new decks an assistant proposed."""
    return push_title("waiting", "1 new deck" if count == 1 else f"{count} new decks")


# The job_runs row holding this database's key for marking the topics the gateway makes (new_topic)
TOPIC_KEY_JOB = "notify_topic_key"
TOPIC_MARK_LEN = 6  # characters of the mark at the end of a topic


_TOPIC_KEYS: weakref.WeakKeyDictionary[Database, bytes] = weakref.WeakKeyDictionary()


def _topic_key(db: Database) -> bytes:
    """This database's key for the topics' marks, made on first use. It lives in the database
    rather than in a Docker secret so that rotating a secret never disconnects the members' phones,
    and a member never sees it, so a topic they typed (before 0.7.30) can't carry a valid mark.
    Kept in memory once read, so a phone connecting takes no write lock."""
    known = _TOPIC_KEYS.get(db)
    if known is not None:
        return known
    with db.tx() as c:
        row = c.execute("SELECT detail_json FROM job_runs WHERE name = ?", (TOPIC_KEY_JOB,)).fetchone()
        if row is None:
            now = int(time.time())
            key = secrets.token_hex(32)
            c.execute(
                "INSERT INTO job_runs (name, day, at, detail_json) VALUES (?, ?, ?, ?)",
                (TOPIC_KEY_JOB, time.strftime("%Y-%m-%d", time.gmtime(now)), now, json.dumps({"key": key})),
            )
        else:
            key = json.loads(row["detail_json"])["key"]
    _TOPIC_KEYS[db] = bytes.fromhex(key)
    return _TOPIC_KEYS[db]


def _topic_mark(key: bytes, sub: str, body: str) -> str:
    digest = hmac.new(key, f"{sub}\n{body}".encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode()[:TOPIC_MARK_LEN]


def _new_topic(key: bytes, sub: str) -> str:
    body = secrets.token_urlsafe(12)
    return f"mtg-{body}{_topic_mark(key, sub, body)}"


def _marked(key: bytes, sub: str, topic: str | None) -> bool:
    if not topic or STRONG_TOPIC_RE.fullmatch(topic) is None:
        return False
    body, mark = topic[4:-TOPIC_MARK_LEN], topic[-TOPIC_MARK_LEN:]
    return hmac.compare_digest(mark, _topic_mark(key, sub, body))


def new_topic(db: Database, sub: str) -> str:
    """A topic nobody can guess for this member: 'mtg-', 96 random bits and the mark that says
    the gateway made it (:func:`strong_topic`)."""
    return _new_topic(_topic_key(db), sub)


def _row(db: Database, sub: str) -> dict[str, Any] | None:
    with db._lock:
        row = db._conn.execute("SELECT * FROM notify_prefs WHERE sub = ?", (sub,)).fetchone()
    return None if row is None else dict(row)


def prefs(db: Database, sub: str) -> dict[str, Any]:
    """The member's notification and summary choices (defaults when they never chose).
    ``deliver``: where each kind of alert goes (:func:`deliveries`); each old switch ``on_*`` is
    True while its kind is pushed."""
    row = _row(db, sub)
    if row is None:
        out: dict[str, Any] = {
            "ntfy_on": False,
            "ntfy_topic": None,
            "summary_every": "off",
            "summary_sent_day": None,
        }
    else:
        out = dict(row)
        out["ntfy_on"] = bool(out["ntfy_on"])
    out["deliver"] = deliveries(db, sub, row)
    for kind, old in OLD_SWITCHES.items():
        out[old] = out["deliver"][kind] == "push"
    return out


def push_members(db: Database) -> list[tuple[str, str]]:
    """(member, topic) of every member whose notifications are on."""
    with db._lock:
        rows = db._conn.execute(
            "SELECT sub, ntfy_topic FROM notify_prefs WHERE ntfy_on = 1 AND ntfy_topic IS NOT NULL "
            "ORDER BY sub"
        ).fetchall()
    return [(r["sub"], r["ntfy_topic"]) for r in rows]


def save_prefs(db: Database, sub: str, **values: Any) -> None:
    """Store some of the member's choices; the others keep their value. A topic another member
    already has is refused (ValueError)."""
    allowed = {"ntfy_on", "ntfy_topic", "on_proposals", "on_reports", "on_prices", "summary_every"}
    if "on_alerts" in values:
        db.set_pref(sub, ALERTS_PREF, bool(values["on_alerts"]))
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


def replace_old_topics(db: Database) -> int:
    """Give every member whose topic the gateway didn't make (:func:`strong_topic`) a new one from
    :func:`new_topic`, and return how many were replaced. Runs at every start: the first start
    after the update replaces every topic from before 0.7.30, and a later one replaces a topic a
    member typed while an older version ran on this database."""
    now = int(time.time())
    key = _topic_key(db)
    with db.tx() as c:
        rows = c.execute("SELECT sub, ntfy_topic FROM notify_prefs WHERE ntfy_topic IS NOT NULL").fetchall()
        subs = [r["sub"] for r in rows if not _marked(key, r["sub"], r["ntfy_topic"])]
        for sub in subs:
            c.execute(
                "UPDATE notify_prefs SET ntfy_topic = ?, updated_at = ? WHERE sub = ?",
                (_new_topic(key, sub), now, sub),
            )
    if subs:
        logger.info("notifications: replaced %d topic(s) the gateway didn't make", len(subs))
    return len(subs)


def topic_owner(db: Database, topic: str) -> tuple[str, bool] | None:
    """Whose topic this is and whether their notifications are on, or None for nobody's."""
    with db._lock:
        row = db._conn.execute(
            "SELECT sub, ntfy_on FROM notify_prefs WHERE ntfy_topic = ?", (topic,)
        ).fetchone()
    return None if row is None else (row["sub"], bool(row["ntfy_on"]))


# A message's tag is its kind (KINDS): the app shows each kind on its own channel, in its own
# group. A test has its own.
TAGS = {**{k: k for k in KINDS}, "test": "test"}


def _for_app(line: str) -> bytes | None:
    """One line of ntfy's JSON stream as the app gets it: known events only, known fields only."""
    try:
        ev = json.loads(line)
    except ValueError:
        return None
    if not isinstance(ev, dict) or ev.get("event") not in STREAM_EVENTS:
        return None
    out = {k: ev[k] for k in STREAM_FIELDS if k in ev}
    return (json.dumps(out, separators=(",", ":")) + "\n").encode()


def _message_id(out: bytes | None) -> str | None:
    """The id of a message line :func:`_for_app` made (other events have none that matters)."""
    if out is None or b'"event":"message"' not in out:
        return None
    mid = json.loads(out).get("id")
    return mid if isinstance(mid, str) else None


def _press_allowed(last: dict[str, float], sub: str) -> bool:
    """True (and the member's next press held back for TEST_COOLDOWN) unless they pressed less
    than TEST_COOLDOWN seconds ago; ``last`` keeps each member's last press."""
    now = time.monotonic()
    if now - last.get(sub, -TEST_COOLDOWN) < TEST_COOLDOWN:
        return False
    if len(last) > 1000:
        for k in [k for k, t in last.items() if now - t >= TEST_COOLDOWN]:
            del last[k]
    last[sub] = now
    return True


class Notifier:
    """Publishes to the stack's ntfy server for members who opted in, and relays each member's
    topic to their Android app (:meth:`relay`)."""

    def __init__(self, ntfy_url: str | None, token: str | None, db: Database, public_url: str, *, http=None):
        self.url = (ntfy_url or "").rstrip("/")
        self._token = token
        self.db = db
        self.public_url = public_url.rstrip("/")
        self._http: httpx.AsyncClient | None = http
        self._tasks: set[asyncio.Task[Any]] = set()  # held so a pending send is not collected
        self.sent = 0
        self._last_test: dict[str, float] = {}
        self._last_summary: dict[str, float] = {}
        self._waiting: dict[tuple[str, str], int] = {}  # the count each deck's message last said
        self._cleared: set[tuple[str, str]] = set()  # decks whose message this process withdrew
        self._streams: dict[str, int] = {}  # open app connections per key
        self._phone_seen: dict[str, float] = {}  # when each key's last connection opened or ended
        self._kicks: dict[str, asyncio.Event] = {}  # set to end a key's connections now
        self._closing: asyncio.Event | None = None  # set when the gateway stops
        self._loop: asyncio.AbstractEventLoop | None = None
        # what waits for each member's press, per deck: {deck_id: (deck name, count, a waiting
        # proposal's id)}; set by watch_waiting
        self._count: Callable[[str], dict[str, tuple[str, int, str]]] | None = None
        self._dirty: dict[str, set[str]] = {}  # members to count again, with the decks touched
        self._dirty_lock = threading.Lock()
        self.recount_delay = RECOUNT_DELAY
        self._no_retry = asyncio.Event()  # set by drain: messages waiting to be sent again are given up
        self.admin_check: Callable[[str], bool] | None = None

    def test_allowed(self, sub: str) -> bool:
        """True (and the member's next test held back for TEST_COOLDOWN) unless they pressed
        Send test less than TEST_COOLDOWN seconds ago."""
        return _press_allowed(self._last_test, sub)

    def summary_allowed(self, sub: str) -> bool:
        """The same for the Account page's Send it now, with its own wait: a test does not hold
        back the summary, nor the summary a test."""
        return _press_allowed(self._last_summary, sub)

    def __repr__(self) -> str:  # the token stays out of any printed or logged object
        return f"Notifier(url={self.url!r})"

    @property
    def enabled(self) -> bool:
        return bool(self.url)

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _request(self, method: str, url: str, **kw: Any) -> bool:
        """One request to ntfy; True when it took it. Never raises; logs the status alone."""
        return await self._attempt(method, url, **kw) == "ok"

    async def _attempt(self, method: str, url: str, **kw: Any) -> str:
        """One request to ntfy: "ok" when it took it, "again" when it could not for a passing
        reason (no connection, a time-out, 429 or 5xx), "refused" otherwise. Never raises; logs
        the status alone."""
        http = self._http or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
        try:
            r = await http.request(method, url, **kw)
        except httpx.HTTPError as exc:
            logger.warning("ntfy publish failed: %s", exc.__class__.__name__)
            return "again"
        finally:
            if self._http is None:
                await http.aclose()
            await dbwait.settle()  # a background job's database work waits off the loop
        if r.status_code >= 300:
            logger.warning("ntfy publish refused: HTTP %d", r.status_code)
            return "again" if r.status_code == 429 or r.status_code >= 500 else "refused"
        return "ok"

    async def _sent(
        self, method: str, url: str, *, retry: bool, still: Callable[[], bool] | None, **kw: Any
    ) -> bool:
        """``_attempt``; with ``retry``, one that failed for a passing reason is sent again in the
        background after each of PUSH_RETRY_DELAYS while ``still()`` (the message is still the one
        to show) holds. Returns whether the first attempt was taken."""
        outcome = await self._attempt(method, url, **kw)
        if outcome == "again" and retry:
            self._background(self._again(method, url, still, kw))
        return outcome == "ok"

    async def _again(
        self, method: str, url: str, still: Callable[[], bool] | None, kw: dict[str, Any]
    ) -> None:
        for delay in PUSH_RETRY_DELAYS:
            try:  # the gateway stopping (drain) ends the wait: the message is given up
                await asyncio.wait_for(self._no_retry.wait(), delay)
                return
            except TimeoutError:
                pass
            if still is not None and not still():
                return  # replaced or withdrawn meanwhile: the old message is not sent
            outcome = await self._attempt(method, url, **kw)
            if outcome == "ok":
                if method == "POST":
                    self.sent += 1
                return
            if outcome == "refused":
                return
        logger.warning("ntfy publish given up after %d tries", len(PUSH_RETRY_DELAYS) + 1)

    async def publish(
        self,
        topic: str,
        title: str,
        message: str,
        path: str = "/",
        tags: str = "",
        group: str | None = None,
        *,
        retry: bool = False,
        still: Callable[[], bool] | None = None,
    ) -> bool:
        """One message to ``topic``; True when the server took it. Never raises. A ``group``
        (:data:`GROUP_RE`) replaces the topic's earlier message of that group on the phone.
        ``retry``: nobody waits for this message, so one ntfy could not take for a passing reason
        is sent again later (PUSH_RETRY_DELAYS) while ``still()`` holds."""
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
        if group is not None and GROUP_RE.fullmatch(group):
            body["sequence_id"] = group
        ok = await self._sent(
            "POST",
            self.url + "/",
            retry=retry,
            still=still,
            json=body,
            headers={"Content-Type": "application/json", **self._headers()},
        )
        if ok:
            self.sent += 1
        return ok

    async def send(
        self,
        sub: str,
        event: str,
        title: str,
        message: str,
        path: str = "/",
        group: str | None = None,
        *,
        retry: bool = False,
        still: Callable[[], bool] | None = None,
        subject: str = "",
        ukey: str | None = None,
    ) -> bool:
        """One alert for the member: kept for their Alerts page and pushed to their phone, as
        their choice for its kind says (``event``: a kind of :data:`KINDS`, an old event name
        (:data:`EVENT_KINDS`), or test). ``subject``: the deck or card it is about. ``ukey``: a
        key naming this alert, so the same one is kept and pushed once. A kept alert's push
        opens it through /notifications/open/<id> (marked read there) and is withdrawn from the
        phone once it is read or dismissed anywhere. True when the notification server took the
        push. ``retry``, ``still``: as for :meth:`publish` (a background message; never a press
        that is answered with the outcome, such as Send test)."""
        await dbwait.before_side_effect()  # a notification sent can't be taken back
        kind = kind_of(event)
        k = KINDS.get(kind)
        if k is not None and k.admin and not self.is_admin(sub):
            return False
        p = prefs(self.db, sub)
        where = p["deliver"].get(kind, "push")
        if where == "off":
            return False
        if kind not in NOT_KEPT:
            # a write: off the loop, so another process holding the database's lock never stalls
            # the gateway's pages while it waits (test_db_lock_jobs)
            alert_id = await asyncio.to_thread(
                functools.partial(
                    self.db.add_gateway_alert,
                    sub,
                    event,
                    title,
                    message,
                    path,
                    keep=KEPT_ALERTS,
                    kind=kind,
                    subject=subject,
                    ukey=ukey,
                )
            )
            if alert_id is None:  # no such member, or this alert was kept (and pushed) before
                return False
            path = f"/notifications/open/{alert_id}"
            group = group or f"a-{alert_id}"
        if where != "push" or not self.enabled:
            return False
        if not p["ntfy_on"] or not p["ntfy_topic"]:
            return False
        return await self.publish(
            p["ntfy_topic"],
            title,
            message,
            path,
            tags=TAGS.get(kind, ""),
            group=group,
            retry=retry,
            still=still,
        )

    def is_admin(self, sub: str) -> bool:
        """Whether the member is one of the gateway's admins (set by the app: ``admin_check``)."""
        return bool(self.admin_check and self.admin_check(sub))

    def withdraw_alerts(self, sub: str, ids: list[int]) -> None:
        """Take the messages of these kept alerts off the member's phones (read or dismissed
        elsewhere), the newest WITHDRAW_MAX of them."""
        for i in sorted(ids, reverse=True)[:WITHDRAW_MAX]:
            self.withdraw_soon(sub, f"a-{int(i)}")

    async def withdraw(self, sub: str, group: str, *, still: Callable[[], bool] | None = None) -> bool:
        """Remove the member's message of ``group`` from their phone (nothing waits there any
        more). Sent whatever the member's event choices, so a message never outlives its cause."""
        if not self.enabled or not GROUP_RE.fullmatch(group):
            return False
        p = prefs(self.db, sub)
        if not p["ntfy_on"] or not p["ntfy_topic"]:
            return False
        return await self._sent(
            "DELETE",
            f"{self.url}/{p['ntfy_topic']}/{group}",
            retry=True,
            still=still,
            headers=self._headers(),
        )

    def _background(self, work: Any) -> None:
        """Run ``work`` on the gateway's loop: from a worker thread (an alert read on the Alerts
        page, a sign-in) it is handed to the loop :meth:`start` remembered."""
        try:
            task = asyncio.get_running_loop().create_task(work)
        except RuntimeError:
            loop = self._loop
            if loop is not None and not loop.is_closed():
                try:
                    loop.call_soon_threadsafe(self._background, work)
                    return
                except RuntimeError:  # the loop closed in between
                    pass
            work.close()  # no loop to run it on (a script): nothing is sent
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def soon(
        self,
        sub: str,
        event: str,
        title: str,
        message: str,
        path: str = "/",
        group: str | None = None,
        *,
        still: Callable[[], bool] | None = None,
        subject: str = "",
    ) -> None:
        """Send in the background: the caller (a proposal, a report) never waits for ntfy, and a
        message ntfy could not take for a passing reason is sent again later. An alert the bell
        keeps is recorded even with no push server."""
        if self.enabled or kind_of(event) not in NOT_KEPT:
            self._background(
                self.send(sub, event, title, message, path, group, retry=True, still=still, subject=subject)
            )

    def withdraw_soon(self, sub: str, group: str, *, still: Callable[[], bool] | None = None) -> None:
        """:meth:`withdraw` in the background."""
        if self.enabled:
            self._background(self.withdraw(sub, group, still=still))

    def deck_waiting(
        self, sub: str, deck_id: str, deck_name: str, count: int, path: str, *, touched: bool = False
    ) -> None:
        """The member's one message for a deck: how many assistant changes wait for their press
        there (``count``), replaced on the phone each time the count changes and removed when it
        reaches 0. A count the phone already shows sends nothing. A deck whose proposals just
        changed (``touched``) is withdrawn at 0 even when this process never sent its message (one
        sent before a restart), once until it sends one again."""
        if not self.enabled or not str(deck_id).isdigit():
            return
        group = f"deck-{int(deck_id)}"
        key = (sub, group)
        if count == 0 and key not in self._waiting and not (touched and key not in self._cleared):
            return
        if count > 0 and self._waiting.get(key) == count:
            return
        if count > 0:
            self._waiting[key] = count
            self._cleared.discard(key)
            title = new_decks_title(count) if deck_id == NEW_DECKS else waiting_title(count, deck_name)
            # sent again later only while the phone should still show this count
            self.soon(
                sub,
                "proposals",
                title,
                waiting_text(count),
                path,
                group,
                still=lambda: self._waiting.get(key) == count,
            )
        else:
            self._waiting.pop(key, None)
            self._cleared.add(key)
            self.withdraw_soon(sub, group, still=lambda: key not in self._waiting)
        if len(self._waiting) > 5000:  # a long-running gateway: forget the oldest decks
            for k in list(self._waiting)[:1000]:
                self._waiting.pop(k, None)
        if len(self._cleared) > 5000:
            self._cleared.clear()

    def watch_waiting(self, count: Callable[[str], dict[str, tuple[str, int, str]]]) -> None:
        """Keep each deck's message in step with ``count(sub)`` (DeckService.waiting_by_deck),
        counted again shortly after :meth:`proposals_changed`."""
        self._count = count

    def start(self) -> None:
        """Remember the gateway's event loop: a change made on a worker thread is counted on it,
        and the gateway's stop ends the app connections from it."""
        self._loop = asyncio.get_running_loop()
        if self._closing is None:
            self._closing = asyncio.Event()

    def proposals_changed(self, sub: str, deck_id: str | None) -> None:
        """A member's proposals changed (Database.watch_proposals; any thread, inside the write):
        count them again in the background, once for changes close together."""
        if not self.enabled or self._count is None:
            return
        with self._dirty_lock:
            first = sub not in self._dirty
            decks = self._dirty.setdefault(sub, set())
            if deck_id and len(decks) < 200:
                decks.add(deck_id)
        if not first:
            return
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.call_later(self.recount_delay, self._recount_soon, sub)
        elif self._loop is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(
                self._loop.call_later, self.recount_delay, self._recount_soon, sub
            )
        else:  # no loop to count on (a script, a test without one): nothing is sent
            with self._dirty_lock:
                self._dirty.pop(sub, None)

    def _recount_soon(self, sub: str) -> None:
        self._background(self.recount(sub))

    async def recount(self, sub: str) -> None:
        """Count what waits for the member's press on each deck and bring each deck's message
        in step: sent or replaced when its count changed, withdrawn when nothing waits there."""
        with self._dirty_lock:
            touched = self._dirty.pop(sub, set())
        if self._count is None:
            return
        try:
            counted = await asyncio.to_thread(self._count, sub)
        except Exception:
            logger.exception("counting waiting proposals failed")
            return

        def key(deck_id: str) -> str:  # new decks share one message, deck-0
            return NEW_DECKS if deck_id == "new" else deck_id

        now = {key(k): v for k, v in counted.items()}
        touched = {key(d) for d in touched}
        shown = {g[len("deck-") :] for (s, g) in self._waiting if s == sub}
        for deck_id in sorted(shown | set(now) | touched):
            name, count, proposal_id = now.get(deck_id, ("", 0, ""))
            if count == 1:
                path = f"/proposals/{proposal_id}"
            else:
                path = "/proposals" if deck_id == NEW_DECKS else f"/decks/{deck_id}"
            self.deck_waiting(sub, deck_id, name, count, path, touched=deck_id in touched)

    async def drain(self) -> None:
        """Wait for background sends, and for counts still to run (tests, shutdown). A message
        waiting to be sent again (PUSH_RETRY_DELAYS) is given up rather than waited for."""
        deadline = time.monotonic() + self.recount_delay + 5
        self._no_retry.set()
        try:
            await self._drain(deadline)
        finally:
            self._no_retry.clear()

    async def _drain(self, deadline: float) -> None:
        while True:
            # A finished task leaves the set only in its done callback, which runs on a later turn
            # of the loop: drop finished ones here, or gathering them again would never yield.
            self._tasks.difference_update([t for t in self._tasks if t.done()])
            if not (self._tasks or (self._dirty and time.monotonic() < deadline)):
                return
            if self._tasks:
                await asyncio.gather(*list(self._tasks), return_exceptions=True)
            await asyncio.sleep(0.02 if not self._tasks else 0)

    # -- the Android app's connection ------------------------------------------------------

    def kick(self, topic: str | None) -> None:
        """End the app connections that use ``topic`` now (it was replaced or switched off); the
        app reconnects, is refused and stops."""
        if topic and (ev := self._kicks.get(topic)) is not None:
            ev.set()

    def close_streams(self) -> None:
        """End every app connection: the gateway is stopping, and a connection that never ends
        would hold the stop for its whole grace period. Safe to call from a signal handler."""
        loop, closing = self._loop, self._closing
        if loop is not None and closing is not None:
            loop.call_soon_threadsafe(closing.set)

    async def open_upstream(self, topic: str, since: str) -> httpx.Response | None:
        """ntfy's JSON stream of ``topic`` (from ``since``, else from now), or None when ntfy
        doesn't answer it. The caller closes the response."""
        params = {"since": since} if since else {}
        http = self._http or httpx.AsyncClient(
            timeout=httpx.Timeout(TIMEOUT, read=None), follow_redirects=False
        )
        try:
            req = http.build_request(
                "GET", f"{self.url}/{topic}/json", params=params, headers=self._headers()
            )
            r = await http.send(req, stream=True)
        except httpx.HTTPError as exc:
            logger.warning("ntfy stream failed: %s", exc.__class__.__name__)
            if self._http is None:
                await http.aclose()
            return None
        if r.status_code != 200:
            logger.warning("ntfy stream refused: HTTP %d", r.status_code)
            await r.aclose()
            if self._http is None:
                await http.aclose()
            return None
        if self._http is None:
            r.extensions["mtg_client"] = http  # closed with the response (relay)
        return r

    async def relay(
        self,
        topic: str,
        upstream: httpx.Response,
        still_valid: Callable[[], Awaitable[bool]],
        finish: Callable[[], Awaitable[None]],
    ) -> AsyncIterator[bytes]:
        """The app's side of one connection: ntfy's lines for ``topic``, filtered (:func:`_for_app`),
        with a keepalive whenever ntfy is quiet for :data:`STREAM_KEEPALIVE` seconds. Ends when
        ntfy ends or goes quiet for :data:`STREAM_STALE` seconds, when :func:`still_valid` turns
        false (checked every :data:`STREAM_RECHECK` seconds), on :meth:`kick` and on
        :meth:`close_streams`. The connection was counted by :meth:`claim`; ``finish`` closes
        ntfy's side and releases it, once, whether or not this ever started."""
        if self._closing is None:
            self.start()
        assert self._closing is not None
        closing = self._closing
        kick = self._kicks.setdefault(topic, asyncio.Event())
        lines = upstream.aiter_lines().__aiter__()
        pending: asyncio.Future[str] | None = None
        stops = [asyncio.ensure_future(closing.wait()), asyncio.ensure_future(kick.wait())]
        last_line = last_check = time.monotonic()
        seen: set[str] = set()
        try:
            while not (closing.is_set() or kick.is_set()):
                if pending is None:
                    pending = asyncio.ensure_future(lines.__anext__())
                done, _ = await asyncio.wait(
                    {pending, *stops}, timeout=STREAM_KEEPALIVE, return_when=asyncio.FIRST_COMPLETED
                )
                now = time.monotonic()
                if pending in done:
                    try:
                        line = pending.result()
                    except (StopAsyncIteration, httpx.HTTPError):
                        break
                    pending = None
                    last_line = now
                    out = _for_app(line)
                    # ntfy can send a message twice when a connection starts with since= (seen
                    # with 2.31.1): pass each message id on once per connection.
                    mid = _message_id(out)
                    if out is not None and (mid is None or mid not in seen):
                        if mid is not None:
                            seen.add(mid)
                            if len(seen) > 500:
                                seen.clear()
                        yield out
                elif not done:
                    if now - last_line >= STREAM_STALE:
                        break
                    yield (json.dumps({"event": "keepalive", "time": int(time.time())}) + "\n").encode()
                if now - last_check >= STREAM_RECHECK:
                    last_check = now
                    if not await still_valid():
                        break
        finally:
            for f in (pending, *stops):
                if f is not None:
                    f.cancel()
            await finish()

    def claim(self, topic: str) -> bool:
        """Count one more app connection with ``topic``, or False past
        :data:`STREAM_MAX_PER_KEY`. Counted before anything is awaited, so connections made at the
        same moment can't all pass the check; :meth:`release` gives it back."""
        if self._streams.get(topic, 0) >= STREAM_MAX_PER_KEY:
            return False
        self._streams[topic] = self._streams.get(topic, 0) + 1
        self._phone_seen[topic] = time.monotonic()
        return True

    def full(self) -> bool:
        """Whether the gateway already has :data:`STREAM_MAX_TOTAL` app connections open."""
        return sum(self._streams.values()) >= STREAM_MAX_TOTAL

    def release(self, topic: str) -> None:
        self._phone_seen[topic] = time.monotonic()
        if len(self._phone_seen) > 5000:  # keys gone for good: forget the oldest
            for k in sorted(self._phone_seen, key=self._phone_seen.__getitem__)[:1000]:
                self._phone_seen.pop(k, None)
        left = self._streams.get(topic, 1) - 1
        if left > 0:
            self._streams[topic] = left
        else:
            self._streams.pop(topic, None)
            self._kicks.pop(topic, None)

    @staticmethod
    async def close_upstream(upstream: httpx.Response) -> None:
        await upstream.aclose()
        client = upstream.extensions.get("mtg_client")
        if client is not None:
            await client.aclose()

    def streams(self, topic: str) -> int:
        """App connections open with ``topic`` now."""
        return self._streams.get(topic, 0)

    def phone_recent(self, topic: str, within: float) -> bool:
        """Whether a phone is connected with ``topic`` now, or was in the last ``within`` seconds
        (since this process started: phones reconnect at once after a restart)."""
        if self._streams.get(topic, 0) > 0:
            return True
        seen = self._phone_seen.get(topic)
        return seen is not None and time.monotonic() - seen <= within


def _bearer(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    return auth[7:].strip() if auth[:7].lower() == "bearer " else ""


def _refuse(status: int, error: str, message: str) -> Response:
    return JSONResponse(
        {"ok": False, "error": error, "message": message}, status, headers={"Cache-Control": "no-store"}
    )


def add_stream_route(state: AppState, server: Any) -> None:
    """``GET`` :data:`STREAM_PATH`: the member's notifications for their Android app, as one JSON
    object per line, for as long as the app keeps the connection open.

    The app sends its key (the member's topic) as ``Authorization: Bearer``, and ``since`` (the
    id of the last message it got) to get what it missed. Answers 401 for a key that is nobody's
    (replaced, or the member is gone), 403 while the member's notifications are off, 404 when the
    gateway has no notification server, 429 past :data:`STREAM_MAX_PER_KEY` connections, 503 past
    :data:`STREAM_MAX_TOTAL` for the whole gateway and 503
    when ntfy or the identity provider can't be reached (the app tries again later). The reply
    carries ``X-Accel-Buffering: no`` so nginx (and so NPM) passes each line on at once."""
    from .membership import Membership

    @server.custom_route(STREAM_PATH, methods=["GET"], include_in_schema=False)
    async def notifications_stream(request: Request) -> Response:
        n = state.notifier
        if n is None or not n.enabled:
            return _refuse(404, "not_set_up", "This gateway has no notification server.")
        key = _bearer(request)
        owner = topic_owner(state.db, key) if STRONG_TOPIC_RE.fullmatch(key) else None
        if owner is None or not strong_topic(state.db, owner[0], key):
            return _refuse(401, "unknown_key", "This phone's notification key is no longer valid.")
        sub, on = owner
        if not on:
            return _refuse(403, "off", "Push notifications are off for this account.")
        if state.membership is not None:
            outcome = await state.membership.check(sub)
            if outcome is Membership.REVOKED:
                return _refuse(401, "unknown_key", "This phone's notification key is no longer valid.")
            if outcome is Membership.UNAVAILABLE:
                return _refuse(503, "unavailable", outage("unavailable", "The sign-in service", again=True))
            if outcome is Membership.SIGN_IN:
                # none of the member's sign-ins is honoured: the key is kept, and the phone tries
                # again later (any refusal but its three stop codes) until they sign in again
                return _refuse(
                    403, "sign_in_again", "Sign in to the gateway again to keep getting push notifications."
                )
        if n.full():  # the app tries again later, like any refusal but these three
            return _refuse(503, "busy", "The gateway has too many phones connected. Try again later.")
        if not n.claim(key):
            return _refuse(429, "too_many", "This key already has too many connections open.")
        since = request.query_params.get("since", "")
        try:
            upstream = await n.open_upstream(key, since if SINCE_RE.fullmatch(since) else "")
        except BaseException:
            n.release(key)
            raise
        if upstream is None:
            n.release(key)
            return _refuse(503, "unavailable", outage("unavailable", "The notification server", again=True))
        finished = False

        async def finish() -> None:
            nonlocal finished
            if finished:
                return
            finished = True
            n.release(key)
            await n.close_upstream(upstream)

        async def still_valid() -> bool:
            """The key is still the member's and on, and the member is still allowed in (a member
            removed at the identity provider is cut off at the next re-check)."""
            if topic_owner(state.db, key) != (sub, True):
                return False
            if state.membership is not None:
                outcome = await state.membership.check(sub)
                return outcome is Membership.ALLOWED or outcome is Membership.UNAVAILABLE
            return True

        return _Stream(
            n.relay(key, upstream, still_valid, finish),
            finish,
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )


def add_read_route(state: AppState, server: Any) -> None:
    """``POST`` :data:`READ_PATH`: the app's Mark read on one message. The app sends its key as
    for the stream and ``{"tag": "a-<alert id>"}`` (the message's group); the member's alert is
    marked read, so the Alerts page and the bell show it read and the member's other phones take
    the message off. 401 for a key that is nobody's, 404 for an alert that is not theirs."""
    from .membership import Membership

    @server.custom_route(READ_PATH, methods=["POST"], include_in_schema=False)
    async def notifications_read(request: Request) -> Response:
        key = _bearer(request)
        owner = topic_owner(state.db, key) if STRONG_TOPIC_RE.fullmatch(key) else None
        if owner is None or not strong_topic(state.db, owner[0], key):
            return _refuse(401, "unknown_key", "This phone's notification key is no longer valid.")
        sub = owner[0]
        if state.membership is not None and await state.membership.check(sub) is Membership.REVOKED:
            return _refuse(401, "unknown_key", "This phone's notification key is no longer valid.")
        try:
            body = json.loads((await request.body())[:1024] or b"{}")
        except ValueError:
            body = None
        tag = str(body.get("tag") or "") if isinstance(body, dict) else ""
        m = re.fullmatch(r"a-(\d{1,12})", tag)
        if m is None or state.db.alert_owner(int(m.group(1))) != sub:
            return _refuse(404, "not_found", "That alert is not on this account.")
        changed = state.db.set_gateway_alerts_read(sub, [int(m.group(1))])
        if changed and state.notifier is not None:
            state.notifier.withdraw_alerts(sub, changed)
        return JSONResponse({"ok": True}, headers={"Cache-Control": "no-store"})


class _Stream(StreamingResponse):
    """A StreamingResponse that also runs ``finish`` when the client left before the body started
    (the body's own ``finally`` never runs then), shielded from the request's cancellation."""

    def __init__(self, content: AsyncIterator[bytes], finish: Callable[[], Awaitable[None]], **kw: Any):
        super().__init__(content, **kw)
        self._finish = finish

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        try:
            await super().__call__(scope, receive, send)
        finally:
            with anyio.CancelScope(shield=True):
                await self._finish()


# -- Account page: the Notifications card, with the summary ----------------------------------------

ACCOUNT_CSS = """<style>
/* the Account page's Push notifications and Summary cards */
.nf-card .gh{display:flex;flex-wrap:wrap;align-items:center;gap:.4rem .75rem;margin:0 0 .35rem}
.nf-card .gh h2{margin:0}
.nf-card .intro{margin:0 0 .75rem;line-height:1.45}
.nf-card h3{margin:0 0 .35rem;font-size:1.05rem}
.nf-card form{margin:0}
/* every tick box on its own 44 px row, with room between the rows */
.nf-card .ui-checks label{line-height:1.35;padding:.2rem 0}
.nf-card .master label{font-weight:700}
/* the When group: a heading and an indent, not a panel inside the card */
.nf-card .nf-when{margin:.35rem 0 0;padding:0 0 0 1.85rem;border:0;min-width:0}
.nf-card .nf-when legend{padding:0;margin:0 0 .15rem;font-weight:700;color:var(--text-muted);font-size:.86rem;
  text-transform:uppercase;letter-spacing:.03em}
/* a choice's hint, under its words (past the 20 px box and the label's gap) */
.nf-card .nf-sub{margin:-.2rem 0 .25rem;padding-left:calc(20px + .6rem);font-size:.86rem;line-height:1.45}
.nf-card .part{margin:1rem 0 0;padding:1rem 0 0;border-top:1px solid var(--border-soft)}
.nf-card .part .ui-hint{font-size:.86rem;line-height:1.45;margin:.25rem 0 .5rem}
.nf-card .part p{line-height:1.45}
.nf-card .ui-actions{margin:.6rem 0 .25rem}
.nf-card .notice{margin:0 0 .75rem}
/* each type of alert: its name and what it is on the left, where it goes on the right; on a
   phone the drop-down goes under the words, the row's full width */
.nf-card .nf-kinds{margin:0;padding:0;border:0;min-width:0}
.nf-card .nf-kinds legend{padding:0;margin:0 0 .25rem;font-weight:700;color:var(--text-muted);
  font-size:.86rem;text-transform:uppercase;letter-spacing:.03em}
.nf-card .nf-kind{display:flex;flex-wrap:wrap;align-items:center;gap:.35rem 1rem;padding:.6rem 0;
  border-top:1px solid var(--border-soft)}
.nf-card .nf-kind:first-of-type{border-top:0}
.nf-card .nf-kind .w{flex:1 1 16rem;min-width:0}
.nf-card .nf-kind .w label{display:block;font-weight:700;overflow-wrap:anywhere}
.nf-card .nf-kind .w small{display:block;color:var(--text-muted);font-size:.86rem;line-height:1.4;
  overflow-wrap:anywhere}
.nf-card .nf-kind select:not(.msel-native),.nf-card .nf-kind .msel-btn{flex:0 1 13rem;min-width:0;
  min-height:44px}
/* Summary: How often and Save summary on one line, at the end of what they act on */
.nf-card .sum-row{display:flex;flex-wrap:wrap;align-items:flex-end;gap:.5rem}
.nf-card .sum-row .fld{flex:0 1 16rem;min-width:0}
.nf-card .sum-row label{display:block;margin:0 0 .3rem;font-weight:700}
.nf-card .sum-row select:not(.msel-native),.nf-card .sum-row .msel-btn{width:100%}
.nf-card .sum-text{margin:.25rem 0;padding:.6rem .8rem;background:var(--surface-2);
  border:1px solid var(--border-soft);border-radius:var(--radius);font-family:inherit;font-size:.92rem;
  white-space:pre-wrap;overflow-wrap:anywhere}
@media (max-width:599.98px){
  .nf-card .ui-actions > *{flex:1 1 100%}
  .nf-card .ui-actions form{display:flex}
  .nf-card .ui-actions form button{flex:1 1 100%}
  .nf-card .sum-row .fld,.nf-card .sum-row button{flex:1 1 100%}
  .nf-card .nf-when{padding-left:1rem}
  .nf-card .nf-kind select:not(.msel-native),.nf-card .nf-kind .msel-btn{flex:1 1 100%}
}
</style>"""

# Which notices belong to which card: each shows inside its own card, where the redirect lands.
NOTIFY_NOTICES = frozenset(
    {
        "notify_saved",
        "notify_off",
        "notify_topic",
        "notify_test_sent",
        "notify_test_failed",
        "notify_test_wait",
    }
)
SUMMARY_NOTICES = frozenset(
    {
        "summary_saved",
        "summary_sent",
        "summary_invalid",
        "summary_off",
        "summary_send_wait",
        "summary_send_failed",
    }
)
CARD_NOTICES = NOTIFY_NOTICES | SUMMARY_NOTICES


def _card_notice(ok: str | None, err: str | None, codes: frozenset[str]) -> str:
    if ok in codes and ok in ACCOUNT_OK:
        return f"<div class='notice ok' role='status'>{_esc(ACCOUNT_OK[ok])}</div>"
    if err in codes and err in ACCOUNT_ERR:
        return f"<div class='notice error' role='alert'>{_esc(ACCOUNT_ERR[err])}</div>"
    return ""


def _summary_html(
    state: AppState, sub: str, p: dict[str, Any], csrf_in: str, *, on: bool, notice: str = ""
) -> str:
    """The Summary card (only with a notification server: ntfy is how it is sent). The latest
    summary is built only for a member who chose one, not on every page view; Send it now sends
    that same summary at once, so it shows only then."""
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
        kind = "daily" if period == "day" else "weekly"
        hint = (
            f"Sends this summary to your phones now, as your {kind} summary would. "
            "Like Send test, once a minute."
        )
        if not on:
            hint = (
                "Push notifications are off, so there is nowhere to send it. Tick Send me push "
                "notifications and press Save push notifications first."
            )
        latest = (
            f"<div class='part'><h3>{'Today' if period == 'day' else 'The last 7 days'}</h3>"
            f"<pre class='sum-text'>{_esc(built['text'])}</pre>"
            f"<form method='post' class='ui-actions'>{csrf_in}"
            "<input type='hidden' name='action' value='summary_send'>"
            f"<button class='btn-plain' aria-describedby='s-now'{'' if on else ' disabled'}>"
            f"{icon('send')}Send it now</button></form>"
            f"<p class='ui-hint' id='s-now'>{hint}</p></div>"
        )
    return (
        "<section class='card nf-card' id='summary' aria-labelledby='s-h'>"
        "<div class='gh'><h2 id='s-h'>Summary</h2></div>"
        f"<p class='intro'>A plain summary of {what}, written by the gateway from its own data (no AI) and "
        "sent through your push notifications when they are on. Your assistant can read it too and write you "
        "its own version on a schedule.</p>"
        f"{notice}"
        f"<form method='post' class='sum-row'>{csrf_in}"
        "<input type='hidden' name='action' value='summary_save'>"
        "<span class='fld'><label for='s-every'>How often</label>"
        f"<select id='s-every' name='summary_every'>{opts}</select></span>"
        f"<button class='primary'>Save summary</button></form>{latest}</section>"
    )


def strong_topic(db: Database, sub: str, topic: str | None) -> bool:
    """A topic the gateway made for this member (:func:`new_topic`): only those work as an app's key."""
    return _marked(_topic_key(db), sub, topic)


# Samsung's own battery saving can stop the app's connection whatever Android allows (0.7.46 review).
SAMSUNG_STEP = (
    "On a Samsung phone, also add the app to Never sleeping apps (Settings, Battery, Background usage "
    "limits), or the phone can put it to sleep and stop your push notifications."
)


def _phone_html(state: AppState, sub: str, p: dict[str, Any]) -> str:
    """The card's part about this phone, inside the Android app: whether it is connected, and the
        button that connects it (which asks Android for the notification permission and for the
        app to keep running in the background). The app says which part applies (feedback.js).
    The button is plain: Save push notifications stays the card's one orange button."""
    topic = p["ntfy_topic"]
    if not strong_topic(state.db, sub, topic):  # none yet, or not one new_topic made: give one now
        topic = new_topic(state.db, sub)
        save_prefs(state.db, sub, ntfy_topic=topic)
    key = _esc(topic)
    return (
        f"<div class='phone' data-native-state='notificationsState' data-native-arg='{key}'>"
        "<h3>This phone</h3>"
        "<p data-native-show='connected' hidden><span class='badge ok'>Connected</span> This phone gets your "
        "push notifications, also while the app is closed. While it is connected, Android shows a quiet "
        f"notification called Always connected. {SAMSUNG_STEP}</p>"
        "<p data-native-show='blocked' hidden>Android has push notifications turned off for this app. "
        "Turn them on in Android's settings for the app, then press Get push notifications on this "
        "phone.</p>"
        "<p data-native-show='off' hidden>This phone doesn't get your push notifications yet. Press Get "
        "push notifications on this phone. Android may ask you to allow push notifications and to let "
        "the app run in the background. Allow both so push notifications arrive while the app is "
        f"closed. {SAMSUNG_STEP}</p>"
        "<div class='form-actions' data-native-show='off blocked' hidden>"
        f"<button type='button' class='btn-plain' data-native='startNotifications' data-native-arg='{key}' "
        f"data-native-auto>{icon('bell')}Get push notifications on this phone</button></div>"
        "<p class='ui-hint' data-native-show='' hidden>Update the app to get push notifications on "
        "this phone.</p>"
        "</div>"
    )


def kinds_html(state: AppState, sub: str, p: dict[str, Any], *, push: bool) -> str:
    """One row a type of alert, with where it goes: Off, Alerts only, or Alerts and push (only
    while this gateway can push). Admins see the gateway's own alerts too."""
    admin = state.notifier is not None and state.notifier.is_admin(sub)
    choices = DELIVERY if push else ("off", "list")
    rows = []
    for k in KINDS.values():
        if k.admin and not admin:
            continue
        if k.key == "price" and state.prices is None:
            continue
        now = p["deliver"].get(k.key, k.default)
        if not push and now == "push":
            now = "list"
        opts = "".join(
            f"<option value='{v}'{' selected' if v == now else ''}>{_esc(DELIVERY_WORDS[v])}</option>"
            for v in choices
        )
        fid = f"nf-{k.key}"
        rows.append(
            f"<div class='nf-kind'><span class='w'><label for='{fid}'>{_esc(k.name)}</label>"
            f"<small id='{fid}-h'>{_esc(k.hint)}</small></span>"
            f"<select id='{fid}' name='deliver_{k.key}' aria-describedby='{fid}-h'>{opts}</select></div>"
        )
    return "<fieldset class='nf-kinds'><legend>Each type of alert</legend>" + "".join(rows) + "</fieldset>"


def account_card_html(
    state: AppState,
    sub: str,
    csrf_in: str,
    *,
    ok: str | None = None,
    err: str | None = None,
    heading: bool = True,
) -> str:
    """The Notifications page (Account › Account details › Notifications, and the Alerts page's
    Settings): where each type of alert goes (:func:`kinds_html`), the push switch for the whole
    account, "Your phones" (this phone's part inside the app), Send test, Disconnect my phones,
    and the summary's own card. A notice from one of their forms (``ok`` / ``err``) shows inside
    the card it came from. ``heading`` False leaves out the card's own heading (its page names it)."""
    n = state.notifier
    h2 = "<h2 id='n-h'>Push notifications</h2>" if heading else ""
    p = prefs(state.db, sub)
    push = n is not None and n.enabled
    named = "aria-labelledby='n-h'" if heading else "aria-label='Push notifications'"
    save = (
        "<div class='ui-actions'><button class='primary'>Save push notifications</button></div>"
        "<p class='ui-hint'><a href='/notifications'>Open Alerts</a> to read, mark read or clear them.</p>"
    )
    if not push:
        return (
            f"{ACCOUNT_CSS}<section class='card nf-card' id='notifications' {named}>"
            f"<div class='gh'>{h2}</div>"
            "<p class='intro'>Choose which alerts show under Alerts (the bell). This gateway has no "
            "notification server set up, so the Android app can't get push notifications; the person who "
            "runs this gateway can turn them on.</p>"
            f"{_card_notice(ok, err, NOTIFY_NOTICES)}"
            f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='notify_save'>"
            f"{kinds_html(state, sub, p, push=False)}{save}</form></section>"
        )

    on = p["ntfy_on"] and bool(p["ntfy_topic"])
    if in_app():
        phone = _phone_html(state, sub, p) if on else ""
    else:
        phone = (
            "<p class='ui-hint'>Push notifications arrive in the gateway's Android app, also while it is "
            "closed. To get them, install the app from the <a href='/install?for=android'>Android app</a> "
            "page and sign in. Then open this page in the app, tick Send me push notifications and press "
            "Save push notifications.</p>"
        )
    test = (
        "<div class='ui-actions'>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='notify_test'>"
        "<button class='btn-plain'>Send test</button></form>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='notify_new_topic'>"
        "<button class='btn-plain' aria-describedby='n-disc'>Disconnect my phones</button></form></div>"
        "<p class='ui-hint' id='n-disc'>Disconnect my phones: your phones stop getting push notifications "
        "until each one is connected again.</p>"
        if on
        else ""
    )
    # inside the app the part is about this phone and has its own heading (_phone_html)
    head = "" if in_app() else "<h3>Your phones</h3>"
    phones = f"<div class='part'>{head}{phone}{test}</div>" if phone or test else ""
    return (
        f"{ACCOUNT_CSS}<section class='card nf-card' id='notifications' {named}>"
        f"<div class='gh'>{h2}{label('On', 'green', 'check') if on else ''}</div>"
        "<p class='intro'>Every alert shows under Alerts (the bell), where you can read, mark read and clear "
        "it. Choose which types also come to your phone as a push notification. A push names its type and "
        "the deck or card first, and never lists your deck's cards. Reading an alert anywhere takes it off "
        "your phones.</p>"
        f"{_card_notice(ok, err, NOTIFY_NOTICES)}"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='notify_save'>"
        f"<div class='ui-checks master'>{_master_box(p)}</div>"
        f"{kinds_html(state, sub, p, push=True)}{save}</form>"
        f"{phones}</section>"
        f"{_summary_html(state, sub, p, csrf_in, on=on, notice=_card_notice(ok, err, SUMMARY_NOTICES))}"
    )


def _master_box(p: dict[str, Any]) -> str:
    return (
        "<label><input type='checkbox' name='ntfy_on' value='1' aria-describedby='n-master'"
        f"{' checked' if p['ntfy_on'] else ''}><span>Send me push notifications</span></label>"
        "<p class='ui-hint nf-sub' id='n-master'>Off: nothing comes to your phones; every alert still shows "
        "under Alerts.</p>"
    )


def chosen_deliveries(data: dict[str, str]) -> dict[str, str]:
    """Where each kind goes, from the form: its drop-down (deliver_<kind>), or, from a form
    before 0.7.47, its tick box (ticked: pushed, else off)."""
    if any(k.startswith("deliver_") for k in data):
        return {k: data[f"deliver_{k}"] for k in KINDS if data.get(f"deliver_{k}") in DELIVERY}
    return {kind: "push" if data.get(old) == "1" else UNTICKED[kind] for kind, old in OLD_SWITCHES.items()}


async def account_action(state: AppState, sub: str, action: str, data: dict[str, str]) -> str | None:
    """Handle the Notifications card's forms on the Account page. Returns the redirect's query
    (``ok=...`` or ``err=...``), or None for an action that is not one of these. Saved with
    notifications on inside the app, the query also carries ``connect=1``: the page then presses
    Get notifications on this phone for the member (feedback.js)."""
    if action == "notify_save":
        chosen = chosen_deliveries(data)
        n = state.notifier
        if n is None or not n.enabled:  # no push server: where alerts go, nothing else
            save_deliveries(state.db, sub, {k: ("list" if v == "push" else v) for k, v in chosen.items()})
            return "ok=notify_saved"
        on = data.get("ntfy_on") == "1"
        alerts = chosen.get("archidekt") == "push"
        before = prefs(state.db, sub)
        current = before["ntfy_topic"]
        if on and alerts and not (before["ntfy_on"] and before["on_alerts"]):
            # turned (back) on: Archidekt alerts are pushed from now, not what came meanwhile
            from .alerts_push import forget

            forget(state.db, sub)
        topic = (
            current if strong_topic(state.db, sub, current) else (new_topic(state.db, sub) if on else None)
        )
        save_prefs(state.db, sub, ntfy_on=on, **({"ntfy_topic": topic} if topic else {}))
        save_deliveries(state.db, sub, chosen)
        if not on:
            if state.notifier is not None:
                state.notifier.kick(current)
            return "ok=notify_off"
        return "ok=notify_saved&connect=1" if in_app() else "ok=notify_saved"
    if action == "notify_new_topic":
        old = prefs(state.db, sub)["ntfy_topic"]
        save_prefs(state.db, sub, ntfy_topic=new_topic(state.db, sub))
        # nor may the old key come back from a copy saved before a database upgrade
        state.db.scrub_upgrade_copies(sub, "phone")
        if state.notifier is not None:
            state.notifier.kick(old)
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
            "Push notifications work. This is the only test message.",
            "/account/details/notifications",
        )
        return "ok=notify_test_sent" if ok else "err=notify_test_failed"
    if action == "summary_send":
        # Send it now: the summary the card shows, sent at once as the schedule would send it.
        n = state.notifier
        p = prefs(state.db, sub)
        if n is None or not n.enabled or state.summaries is None:
            return "err=summary_off"
        if not p["ntfy_on"] or not p["ntfy_topic"] or p["summary_every"] == "off":
            return "err=summary_send_failed"
        if not n.summary_allowed(sub):
            return "err=summary_send_wait"
        ok = await state.summaries.send(sub, "day" if p["summary_every"] == "daily" else "week")
        return "ok=summary_sent" if ok else "err=summary_send_failed"
    if action == "summary_save":
        # Summaries go out only as notifications: without a server there is no schedule to choose.
        if state.notifier is None or not state.notifier.enabled:
            return "err=summary_off"
        try:
            save_prefs(state.db, sub, summary_every=data.get("summary_every", "off"))
        except ValueError:
            return "err=summary_invalid"
        return "ok=summary_saved"
    return None
