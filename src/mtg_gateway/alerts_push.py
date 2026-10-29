"""Archidekt alerts on the member's phone (0.7.46, decision C10).

Archidekt's bell (Alerts here) has no push of its own and no webhooks, so the gateway asks it. For
each member whose push notifications are on (notify.py), who left "New alerts on Archidekt" ticked
(on by default), whose Archidekt account is linked and whose phone is connected now or was within
PHONE_RECENT, it reads the alert count every POLL_EVERY seconds: one request per member. Only
when the count went up does it read the alert list (one more request), and it pushes each alert
it has not seen before: the alert's own words, and a link to the gateway page that opens it
(a deck, a forum thread, a profile, as the bell does), or to the bell when the gateway has no page
for it (news, contests). More than MAX_SINGLE new alerts at once make one message instead.

Load on Archidekt: for N members with a phone connected and alerts on, at most 60 / 15 * N = 4N
count reads an hour, plus one list read in a round where something new arrived; a count a
member's own page read less than a minute ago (the bell's kept count) is used instead of asking
again, and the count the round reads fills the bell in turn. Ten such members are 40 reads an
hour, about 2 % of the shared 30-a-minute pace.

Rules it keeps, the same as the deck sync (sync.py):
- Read-only. It never marks anything read on Archidekt (the gateway knows no such request).
- Members and assistants go first. Before each request it waits for every member's Archidekt work
  and the shared pacer to be idle, and skips the member until the next round rather than queue
  behind them; its requests are background ones, so a page arriving meanwhile goes first, and
  they never use the share of the per-minute cap kept for pages. Each request spends from the
  member's own budget and stops while less than a quarter of it is left.
- Fails quietly: each request is sent once, never retried, nothing is sent while the pacer's
  breaker or a Retry-After pause is on, and Archidekt being unavailable or asking us to slow down
  ends the round. A session Archidekt rejects never unlinks the member.

No repeats: what it last saw is kept on the account (``account_prefs`` ALERTS_STATE: the count,
and a short fingerprint of each alert in the list, never the words), written before anything is
pushed, so a restart pushes nothing again. A member's first look, a look after more than
STALE_AFTER without one (a phone off for days), a relink to another Archidekt account and
turning alerts or notifications back on all start from what is there now: only what arrives
after is pushed. What the member opens in the gateway's bell counts as seen too.

Unknown (needs a live check): whether Archidekt marks the alerts seen when the list is read. The
site itself sets its badge to 0 after reading it; either way this never pushes an alert twice,
but if it does, a pushed alert no longer shows as new on archidekt.com.
"""

from __future__ import annotations

import asyncio
import contextvars
import hashlib
import logging
import time
from collections import Counter
from typing import TYPE_CHECKING, Any

from . import notify, profile, profile_page
from .decks import DeckError

if TYPE_CHECKING:
    from .db import Database
    from .decks import DeckService
    from .notify import Notifier

logger = logging.getLogger(__name__)

POLL_EVERY = 15 * 60.0  # seconds between rounds
START_DELAY = 120.0  # the first round after a start, so phones have reconnected
# A phone counts as connected for this long after its connection ended: ntfy keeps a message
# 12 hours for a phone that was offline, so one pushed in that time still reaches it.
PHONE_RECENT = 12 * 3600.0
STALE_AFTER = 24 * 3600.0  # a last look older than this starts again from what is there
IDLE_WAIT = 10.0  # how long a request waits for members' Archidekt work to end before skipping
IDLE_POLL = 0.5
MAX_SINGLE = 3  # up to this many new alerts are pushed one by one; more make one message
FINGERPRINT_LEN = 12  # hex characters kept of each alert's fingerprint
ALERTS_STATE = "notify.alerts_state"  # what the last look saw (account_prefs)
TITLE = "New on Archidekt"


def fingerprint(item: dict[str, Any]) -> str:
    """A short mark for one alert (as profile.notification_rows gives it): its kind, words and
    link. The words themselves are never stored."""
    raw = "\n".join((str(item.get("kind") or ""), str(item.get("text") or ""), str(item.get("url") or "")))
    return hashlib.sha256(raw.encode()).hexdigest()[:FINGERPRINT_LEN]


def path_for(item: dict[str, Any]) -> str:
    """Where a pushed alert opens: the gateway's own page, as the bell links it, else the bell."""
    href = profile_page._gateway_url(item.get("url"))
    return href if href and href.startswith("/") and not href.startswith("//") else "/notifications"


def _state(db: Database, sub: str, uid: int) -> dict[str, Any] | None:
    """What the last look for this Archidekt account saw, or None to start from now."""
    got = db.get_pref(sub, ALERTS_STATE)
    if not isinstance(got, dict) or got.get("uid") != uid or not isinstance(got.get("count"), int):
        return None
    if time.time() - float(got.get("at") or 0) > STALE_AFTER:
        return None
    return got


def _save(db: Database, sub: str, uid: int, count: int, known: list[str] | None) -> None:
    db.set_pref(sub, ALERTS_STATE, {"uid": uid, "count": count, "known": known, "at": int(time.time())})


def forget(db: Database, sub: str) -> None:
    """Start again from what is there (alerts or notifications turned back on)."""
    db.delete_pref(sub, ALERTS_STATE)


def seen_in_bell(db: Database, sub: str, uid: int, items: list[dict[str, Any]]) -> None:
    """The member opened the gateway's bell and saw ``items``: none of them is pushed later. The
    count goes to 0, as the site's own bell sets it, so any alert after this one is looked for."""
    _save(db, sub, uid, 0, [fingerprint(i) for i in items])


def new_alerts(items: list[dict[str, Any]], known: list[str] | None, rise: int) -> list[dict[str, Any]]:
    """The alerts in ``items`` (newest first) that are new: unseen, and not among ``known`` (the
    list the last look saw, each the times it was there, so the same words arriving twice are
    two alerts), at most ``rise`` (how far the count went up), newest first. With nothing known
    (only a count), the newest ``rise`` unseen ones."""
    left = Counter(known or ())
    out = []
    # oldest first: a known alert matches the oldest of the same words, so a new one with the same
    # words is the one left over
    for item in reversed(items):
        mark = fingerprint(item)
        if left[mark] > 0:
            left[mark] -= 1
        elif not item.get("seen"):
            out.append(item)
    out.reverse()
    return out[: max(0, rise)]


class AlertWatch:
    def __init__(self, decks: DeckService, notifier: Notifier, interval: float = POLL_EVERY):
        self.decks = decks
        self.db = decks.db
        self.notifier = notifier
        self.interval = interval
        self._lock = asyncio.Lock()  # one round at a time

    @property
    def enabled(self) -> bool:
        return self.notifier.enabled

    async def loop(self) -> None:
        await asyncio.sleep(START_DELAY)
        while True:
            try:
                await self.run_round()
            except Exception:  # never let one bad round end the loop
                logger.exception("Archidekt alerts round failed")
            await asyncio.sleep(self.interval)

    def members(self) -> list[str]:
        """Members to look for: push on with a phone connected lately, alerts on, linked."""
        out = []
        for sub, topic in notify.push_members(self.db):
            if not self.notifier.phone_recent(topic, PHONE_RECENT):
                continue
            if not notify.prefs(self.db, sub)["on_alerts"] or self.db.get_link(sub) is None:
                continue
            out.append(sub)
        return out

    async def run_round(self) -> int:
        """One round; how many members were looked at. Runs in a context of its own, so no
        request's client or page turn carries over."""
        if not self.enabled:
            return 0
        async with self._lock:
            task = asyncio.get_running_loop().create_task(self._round(), context=contextvars.Context())
            return await task

    async def _round(self) -> int:
        n = 0
        for sub in self.members():
            result = await self.check(sub)
            n += 1
            if result.get("error") in ("unavailable", "rate_limited"):
                break  # Archidekt is struggling: the rest wait for the next round
        return n

    async def _idle(self) -> bool:
        """Wait (up to IDLE_WAIT) until no member's Archidekt work is running or waiting. False
        when that takes longer, or while the pacer refuses calls."""
        if self.decks.client.pacer.paused():
            return False
        deadline = time.monotonic() + IDLE_WAIT
        while self.decks.archidekt_busy():
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(IDLE_POLL)
        return True

    async def _read(self, sub: str, fn: Any) -> Any:
        """One background request for the member, or None when it must wait for the next round
        (members are busy, the member's budget is low)."""
        if not await self._idle():
            return None
        try:
            self.decks._sync_spend(sub)
        except DeckError:
            return None
        return await self.decks._call_unslotted(sub, fn, background=True)

    async def check(self, sub: str) -> dict[str, Any]:
        """Look at one member's alerts and push the new ones. Never raises."""
        try:
            return await self._check(sub)
        except DeckError as exc:
            logger.info("Archidekt alerts look stopped for a member: %s", exc.kind)
            return {"error": exc.kind}
        except Exception:
            logger.exception("Archidekt alerts look failed")
            return {"error": "internal"}

    async def _check(self, sub: str) -> dict[str, Any]:
        link = self.db.get_link(sub)
        uid_text = str((link or {}).get("archidekt_user_id") or "")
        if not uid_text.isdigit():
            return {"skipped": "not_linked"}
        uid = int(uid_text)
        client = self.decks.client
        kept = profile_page._COUNT.get(sub)
        if kept and kept[0] > time.monotonic():
            count = kept[1]  # a page asked a moment ago
        else:
            got = await self._read(sub, lambda token, *_: client.notification_count(token, uid))
            if got is None:
                return {"skipped": "busy"}
            count = int(got)
            profile_page._COUNT[sub] = (time.monotonic() + profile_page.COUNT_TTL, count)
        state = _state(self.db, sub, uid)
        if state is None:
            _save(self.db, sub, uid, count, None)
            return {"count": count, "start": True}
        if count <= state["count"]:
            _save(self.db, sub, uid, count, state.get("known"))
            return {"count": count}
        rows = await self._read(sub, lambda token, *_: client.notifications(token, uid))
        if rows is None:
            return {"skipped": "busy"}
        items = profile.notification_rows(rows)
        # what the bell showed while the list was being read counts as seen too
        state = _state(self.db, sub, uid) or state
        fresh = new_alerts(items, state.get("known"), count - int(state["count"]))
        _save(self.db, sub, uid, count, [fingerprint(i) for i in items])  # before any push
        await self._push(sub, fresh)
        return {"count": count, "pushed": len(fresh)}

    async def _push(self, sub: str, fresh: list[dict[str, Any]]) -> None:
        if len(fresh) > MAX_SINGLE:
            await self.notifier.send(
                sub,
                "alerts",
                f"{len(fresh)} new on Archidekt",
                "Comments, replies or follows are waiting in your Archidekt alerts.",
                "/notifications",
            )
            return
        for item in reversed(fresh):  # oldest first, so the newest is on top on the phone
            text = str(item.get("text") or "").strip() or "Something new is in your Archidekt alerts."
            await self.notifier.send(sub, "alerts", TITLE, text, path_for(item))
