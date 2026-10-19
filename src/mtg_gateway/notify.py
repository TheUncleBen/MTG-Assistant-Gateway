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
that finished (Forge runs that waited in the queue included), a price alert they set, and their
scheduled summary. Each message carries the least that is useful: a short title and line, and a
link to the gateway page. Deck contents are never sent. The token is sent only in the
Authorization header and never logged; a failed publish is logged with the HTTP status alone.

The Account page's Notifications card is here too (``account_card_html``, ``account_action``):
the opt-in (one switch for the whole account), the events, Send test, the app's connect button,
and the member's scheduled summary (summary.py), which is offered only when a notification
server is set up, since the notifications are how it is sent.
"""

from __future__ import annotations

import asyncio
import base64
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

from .db import Database
from .theme import esc as _esc
from .theme import in_app

if TYPE_CHECKING:
    from .app import AppState

logger = logging.getLogger(__name__)

EVENTS = ("proposals", "reports", "prices")  # the kinds a member can switch off one by one
SUMMARY_CHOICES = ("off", "daily", "weekly")
# The Account page's notices for the Notifications card and its summary (pages.py shows them).
ACCOUNT_OK = {
    "notify_saved": "Notifications saved.",
    "notify_off": "Notifications are off.",
    "notify_topic": "Your phones are disconnected. To connect a phone again, open the Account page in the "
    "gateway's app on that phone and press Get notifications on this phone.",
    "notify_test_sent": "The test message is sent. Each phone you connected should show it within a few "
    "seconds.",
    "summary_saved": "Summary choice saved.",
}
ACCOUNT_ERR = {
    "notify_test_failed": "The test could not be sent. Check that notifications are on; if they are, the "
    "notification server did not accept it (the operator can see why in the gateway's log).",
    "notify_test_wait": "A test was just sent. The gateway sends one test a minute, so wait before sending "
    "another.",
    "summary_invalid": "Choose Off, Every day or Every week.",
    "summary_off": "Summaries are sent through notifications, and this gateway has no notification server.",
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
TEST_COOLDOWN = 60.0  # seconds between one member's Send test presses

# The Android app's connection (notify.add_stream_route): the app sends the member's topic as its
# key, and the gateway relays that topic from ntfy as one JSON object per line.
STREAM_PATH = "/app/notifications/stream"
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
GROUP_RE = re.compile(r"^deck-\d{1,12}$")
NEW_DECKS = "0"  # the group of new decks an assistant proposed: no deck of their own yet

RECOUNT_DELAY = 1.0  # seconds: changes close together (an apply all, a sweep) are counted once
WAITING_TEXT = "Open it to look at the changes, then apply or reject them."


def waiting_title(count: int, deck_name: str) -> str:
    """The title of a deck's message: "1 change to Atraxa is waiting for you", "2 changes to ... are"."""
    name = " ".join(str(deck_name or "").split())[:80] or "your deck"
    if count == 1:
        return f"1 change to {name} is waiting for you"
    return f"{count} changes to {name} are waiting for you"


def new_decks_title(count: int) -> str:
    """The title of the message for new decks an assistant proposed."""
    if count == 1:
        return "1 new deck is waiting for you"
    return f"{count} new decks are waiting for you"


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


TAGS = {
    "proposals": "memo",
    "reports": "bar_chart",
    "prices": "chart_with_upwards_trend",
    "summary": "calendar",
}


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
        self._waiting: dict[tuple[str, str], int] = {}  # the count each deck's message last said
        self._cleared: set[tuple[str, str]] = set()  # decks whose message this process withdrew
        self._streams: dict[str, int] = {}  # open app connections per key
        self._kicks: dict[str, asyncio.Event] = {}  # set to end a key's connections now
        self._closing: asyncio.Event | None = None  # set when the gateway stops
        self._loop: asyncio.AbstractEventLoop | None = None
        # what waits for each member's press, per deck: {deck_id: (deck name, count, a waiting
        # proposal's id)}; set by watch_waiting
        self._count: Callable[[str], dict[str, tuple[str, int, str]]] | None = None
        self._dirty: dict[str, set[str]] = {}  # members to count again, with the decks touched
        self._dirty_lock = threading.Lock()
        self.recount_delay = RECOUNT_DELAY

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

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    async def _request(self, method: str, url: str, **kw: Any) -> bool:
        """One request to ntfy; True when it took it. Never raises; logs the status alone."""
        http = self._http or httpx.AsyncClient(timeout=TIMEOUT, follow_redirects=False)
        try:
            r = await http.request(method, url, **kw)
        except httpx.HTTPError as exc:
            logger.warning("ntfy publish failed: %s", exc.__class__.__name__)
            return False
        finally:
            if self._http is None:
                await http.aclose()
        if r.status_code >= 300:
            logger.warning("ntfy publish refused: HTTP %d", r.status_code)
            return False
        return True

    async def publish(
        self, topic: str, title: str, message: str, path: str = "/", tags: str = "", group: str | None = None
    ) -> bool:
        """One message to ``topic``; True when the server took it. Never raises. A ``group``
        (:data:`GROUP_RE`) replaces the topic's earlier message of that group on the phone."""
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
        ok = await self._request(
            "POST", self.url + "/", json=body, headers={"Content-Type": "application/json", **self._headers()}
        )
        if ok:
            self.sent += 1
        return ok

    async def send(
        self, sub: str, event: str, title: str, message: str, path: str = "/", group: str | None = None
    ) -> bool:
        """Publish to the member's topic when they opted in and want this kind of event
        (``event``: proposals, reports, prices, summary or test)."""
        if not self.enabled:
            return False
        p = prefs(self.db, sub)
        if not p["ntfy_on"] or not p["ntfy_topic"]:
            return False
        if event in EVENTS and not p[f"on_{event}"]:
            return False
        return await self.publish(
            p["ntfy_topic"], title, message, path, tags=TAGS.get(event, ""), group=group
        )

    async def withdraw(self, sub: str, group: str) -> bool:
        """Remove the member's message of ``group`` from their phone (nothing waits there any
        more). Sent whatever the member's event choices, so a message never outlives its cause."""
        if not self.enabled or not GROUP_RE.fullmatch(group):
            return False
        p = prefs(self.db, sub)
        if not p["ntfy_on"] or not p["ntfy_topic"]:
            return False
        return await self._request("DELETE", f"{self.url}/{p['ntfy_topic']}/{group}", headers=self._headers())

    def _background(self, work: Any) -> None:
        try:
            task = asyncio.get_running_loop().create_task(work)
        except RuntimeError:  # no running loop (a synchronous caller): nothing is sent
            work.close()
            return
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def soon(
        self, sub: str, event: str, title: str, message: str, path: str = "/", group: str | None = None
    ) -> None:
        """Send in the background: the caller (a proposal, a report) never waits for ntfy."""
        if self.enabled:
            self._background(self.send(sub, event, title, message, path, group))

    def withdraw_soon(self, sub: str, group: str) -> None:
        """:meth:`withdraw` in the background."""
        if self.enabled:
            self._background(self.withdraw(sub, group))

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
            self.soon(sub, "proposals", title, WAITING_TEXT, path, group)
        else:
            self._waiting.pop(key, None)
            self._cleared.add(key)
            self.withdraw_soon(sub, group)
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
        """Wait for background sends, and for counts still to run (tests, shutdown)."""
        deadline = time.monotonic() + self.recount_delay + 5
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
        return True

    def full(self) -> bool:
        """Whether the gateway already has :data:`STREAM_MAX_TOTAL` app connections open."""
        return sum(self._streams.values()) >= STREAM_MAX_TOTAL

    def release(self, topic: str) -> None:
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
            return _refuse(403, "off", "Notifications are off for this account.")
        if state.membership is not None:
            outcome = await state.membership.check(sub)
            if outcome is Membership.REVOKED:
                return _refuse(401, "unknown_key", "This phone's notification key is no longer valid.")
            if outcome is Membership.UNAVAILABLE:
                return _refuse(503, "unavailable", "The sign-in service isn't answering. Try again later.")
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
            return _refuse(503, "unavailable", "The notification server isn't answering. Try again later.")
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
                return await state.membership.check(sub) is not Membership.REVOKED
            return True

        return _Stream(
            n.relay(key, upstream, still_valid, finish),
            finish,
            media_type="application/x-ndjson",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )


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

SUMMARY_CSS = (
    "<style>#summary pre{white-space:pre-wrap;font-family:inherit;margin:.5rem 0 0;padding:.6rem .8rem;"
    "background:var(--surface-2);border-radius:3px;overflow-wrap:anywhere}</style>"
)


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
        "through your notifications when they are on. Your assistant can read it too and write you "
        "its own version on a schedule.</p>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='summary_save'>"
        "<label for='s-every'>How often</label>"
        f"<select id='s-every' name='summary_every'>{opts}</select>"
        f"<button class='primary'>Save summary</button></form>{latest}</div>"
    )


def strong_topic(db: Database, sub: str, topic: str | None) -> bool:
    """A topic the gateway made for this member (:func:`new_topic`): only those work as an app's key."""
    return _marked(_topic_key(db), sub, topic)


def _phone_html(state: AppState, sub: str, p: dict[str, Any]) -> str:
    """The card's part about this phone, inside the Android app: whether it is connected, and the
    button that connects it (which asks Android for the notification permission and for the
    app to keep running in the background). The app says which part applies (feedback.js)."""
    topic = p["ntfy_topic"]
    if not strong_topic(state.db, sub, topic):  # none yet, or not one new_topic made: give one now
        topic = new_topic(state.db, sub)
        save_prefs(state.db, sub, ntfy_topic=topic)
    key = _esc(topic)
    return (
        f"<div class='phone' data-native-state='notificationsState' data-native-arg='{key}'>"
        "<h3>This phone</h3>"
        "<p data-native-show='connected' hidden><span class='badge ok'>Connected</span> This phone gets your "
        "notifications, also while the app is closed. While it is connected, Android shows a quiet "
        "notification called Always connected.</p>"
        "<p data-native-show='blocked' hidden>Android has notifications turned off for this app. Turn "
        "them on in Android's settings for the app, then press Get notifications on this phone.</p>"
        "<p data-native-show='off' hidden>This phone doesn't get your notifications yet. Press Get "
        "notifications on this phone. Android may ask you to allow notifications and to let the app run "
        "in the background. Allow both so notifications arrive while the app is closed.</p>"
        "<div class='form-actions' data-native-show='off blocked' hidden>"
        f"<button type='button' class='primary' data-native='startNotifications' data-native-arg='{key}' "
        "data-native-auto>Get notifications on this phone</button></div>"
        "<p class='muted small' data-native-show='' hidden>Update the app to get notifications on this "
        "phone.</p>"
        "</div>"
    )


def account_card_html(state: AppState, sub: str, csrf_in: str) -> str:
    """The Account page's Notifications card: a note when the gateway has no notification server,
    otherwise the member's choices (one switch for the whole account), this phone's part inside
    the app, Send test, Disconnect my phones and the summary schedule."""
    n = state.notifier
    if n is None or not n.enabled:
        watched = ", a watched card crosses your alert" if state.prices is not None else ""
        return (
            "<div class='card' id='notifications'><h2>Notifications</h2><p class='muted'>This gateway has no "
            "notification server set up, so the Android app can't get notifications. The person who runs "
            "this gateway can turn them on. The app then tells you when an assistant leaves changes waiting "
            f"for your press, a deck report finishes{watched} or your summary is ready.</p></div>"
        )
    p = prefs(state.db, sub)

    def box(name: str, label: str) -> str:
        return (
            f"<label class='check'><input type='checkbox' name='{name}' value='1'"
            f"{' checked' if p[name] else ''}> <span>{_esc(label)}</span></label>"
        )

    on = p["ntfy_on"] and bool(p["ntfy_topic"])
    if in_app():
        phone = _phone_html(state, sub, p) if on else ""
    else:
        phone = (
            "<p class='muted small'>Notifications arrive in the gateway's Android app, also while it is "
            "closed. To get them, install the app from the <a href='/install?for=android'>Android app</a> "
            "page and sign in. Then open this page in the app, tick Send me notifications and press Save "
            "notifications.</p>"
        )
    test = (
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='notify_test'>"
        "<button>Send test</button></form>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='notify_new_topic'>"
        "<button class='inline' title='Your phones stop getting notifications until each one is connected "
        "again.'>Disconnect my phones</button></form>"
        if on
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
        "<p>The gateway's Android app can show you a short message when something happens. Each message "
        "says only what happened and links to the right page on this gateway. It never lists your deck's "
        "cards. The choices below apply to your whole account and to every phone you connect.</p>"
        f"<form method='post'>{csrf_in}<input type='hidden' name='action' value='notify_save'>"
        f"{box('ntfy_on', 'Send me notifications')}"
        "<fieldset><legend>When</legend>"
        f"{box('on_proposals', 'An assistant left changes waiting for my press (one message per deck)')}"
        f"{box('on_reports', 'A deck report finished (Forge runs that waited included)')}"
        f"{prices_box}</fieldset>"
        "<button class='primary'>Save notifications</button></form>"
        f"{phone}<div class='form-actions'>{test}</div>"
        f"{_summary_html(state, sub, p, csrf_in)}</div>"
    )


async def account_action(state: AppState, sub: str, action: str, data: dict[str, str]) -> str | None:
    """Handle the Notifications card's forms on the Account page. Returns the redirect's query
    (``ok=...`` or ``err=...``), or None for an action that is not one of these. Saved with
    notifications on inside the app, the query also carries ``connect=1``: the page then presses
    Get notifications on this phone for the member (feedback.js)."""
    if action == "notify_save":
        on = data.get("ntfy_on") == "1"
        before = prefs(state.db, sub)
        current = before["ntfy_topic"]
        topic = (
            current if strong_topic(state.db, sub, current) else (new_topic(state.db, sub) if on else None)
        )
        save_prefs(
            state.db,
            sub,
            ntfy_on=on,
            on_proposals=data.get("on_proposals") == "1",
            on_reports=data.get("on_reports") == "1",
            on_prices=data.get("on_prices") == "1",
            **({"ntfy_topic": topic} if topic else {}),
        )
        if not on:
            if state.notifier is not None:
                state.notifier.kick(current)
            return "ok=notify_off"
        return "ok=notify_saved&connect=1" if in_app() else "ok=notify_saved"
    if action == "notify_new_topic":
        old = prefs(state.db, sub)["ntfy_topic"]
        save_prefs(state.db, sub, ntfy_topic=new_topic(state.db, sub))
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
            "Notifications work. This is the only test message.",
            "/account",
        )
        return "ok=notify_test_sent" if ok else "err=notify_test_failed"
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
