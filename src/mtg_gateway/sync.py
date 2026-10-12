"""Automatic Archidekt -> gateway deck sync (0.7.25, R-153).

Archidekt is the master copy of every deck. It has no webhooks, so the gateway asks it: when a
member opens the app (any signed-in page, at most once per OPEN_GAP) and every
``MTG_DECK_SYNC_MINUTES`` (30 by default) for members whose proposals are waiting. A check is one
read of the member's deck list (Archidekt's ``/decks/v3/?ownerId=...``, the list every deck page
already reads, with each deck's ``updatedAt``; one request per 50 decks, at most MAX_LIST_PAGES)
plus a full read of a deck only when it has a waiting proposal and its ``updatedAt`` moved since
the last check (at most MAX_DECK_READS per check). Only the member's own decks are in that list,
so proposals on someone else's deck (a clone of a public deck, a collaborator's deck) are not
checked; Apply still compares those as before. The fresh list lands in the member's deck-list
cache, so the app shows the current decks.

What it does with a changed deck: a waiting proposal whose deck no longer matches the deck the
proposal was made from (the same fingerprint Apply compares, decks.py ``_current_deck_for``) is
flagged out of date at once, so the review page, the proposal list and the assistant say so before
anyone presses Apply.

Rules it keeps (COMPARISON.md section 7, R1-R5):
- Read-only. It never writes to Archidekt and never changes, applies, rejects or deletes a
  proposal, snapshot or undo entry. The flag lives in memory next to the proposal; Apply still
  re-reads the live deck and decides for itself, so a missed or wrong flag cannot lose data.
- Members and assistants go first. Before the list read and before each deck read it waits for
  every member's Archidekt work, every apply and the shared pacer to be idle, and skips the rest
  of the check (until the next one) rather than queue behind them; a member arriving while one
  of its requests is in the pacer waits for that one request. All checks share one lock, so at
  most one background request is ever in the pacer. It uses no member's Archidekt slot or budget.
- A deck with an apply or restore running is left alone until that ends.
- Fails quietly: every request is sent once, never retried, and nothing is sent while the
  pacer's breaker or a Retry-After pause is on. Any error ends that member's check, and Archidekt
  being unavailable or asking us to slow down ends the whole round, so a round adds at most one
  failure to the breaker. The last good list stays and the next check tries again.
- Never unlinks: a session Archidekt rejects ends the check; the link is kept and the member's
  own next request decides, as before. A token refresh it needs is stored like any other and
  audited as the deck sync's.

After a restart the flags are rebuilt by the first checks (no database change, schema stays 12).
Unknown (needs a live check by Ben): whether Archidekt moves ``updatedAt`` on every card edit. If
it does not, a change can go unflagged until Apply refuses it, exactly as before 0.7.25.
"""

from __future__ import annotations

import asyncio
import contextvars
import logging
import time
from typing import Any

from .decks import DeckError, DeckService

logger = logging.getLogger(__name__)

OPEN_GAP = 5 * 60.0  # an app open checks again once the member's last check is this old
IDLE_WAIT = 10.0  # how long a step waits for members' Archidekt work to end before skipping
IDLE_POLL = 0.5
MAX_DECK_READS = 5  # full deck reads one member's check may make; the rest wait for the next
START_DELAY = 120.0  # the first timed round after a start (flags are rebuilt then)
MAX_FLAGS = 20_000


class DeckSync:
    def __init__(self, decks: DeckService, minutes: int):
        self.decks = decks
        self.db = decks.db
        self.interval = max(0, int(minutes)) * 60.0
        self._lock = asyncio.Lock()  # one check at a time, for every member
        self._checked: dict[str, float] = {}  # sub -> when its last check started (monotonic)
        self._tasks: dict[str, asyncio.Task[Any]] = {}  # sub -> the app-open check under way
        # (sub, deck id) -> the deck list's updatedAt when its waiting proposals were last compared
        self._seen: dict[tuple[str, str], str] = {}
        self._flags: dict[str, int] = {}  # proposal id -> when it was seen out of date (epoch)

    @property
    def enabled(self) -> bool:
        return self.interval > 0

    def out_of_date(self, proposal_id: str) -> int | None:
        """When the sync saw this proposal's deck stop matching it; None when it has not."""
        return self._flags.get(proposal_id)

    # -- triggers ---------------------------------------------------------------------------------
    def opened(self, sub: str) -> None:
        """A signed-in page was opened: check the member's decks in the background unless they
        were checked in the last OPEN_GAP seconds or a check is under way. Never waits."""
        if not self.enabled or sub in self._tasks:
            return
        last = self._checked.get(sub)
        if last is not None and time.monotonic() - last < OPEN_GAP:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # no running loop (a synchronous test client): nothing to schedule
            return
        # a burst of page loads starts one check; a member with no link is looked up once a gap
        self._checked[sub] = time.monotonic()
        if self.db.get_link(sub) is None:
            return
        # nobody's request: a fresh context, so no request's client id or timing carries over
        task = loop.create_task(self.check(sub), context=contextvars.Context())
        self._tasks[sub] = task

        def done(t: asyncio.Task[Any]) -> None:
            if self._tasks.get(sub) is t:
                self._tasks.pop(sub, None)
            if not t.cancelled() and t.exception() is not None:
                logger.info("deck sync check failed: %r", t.exception())

        task.add_done_callback(done)

    async def loop(self) -> None:
        """Every ``interval`` seconds, check each linked member with waiting proposals."""
        await asyncio.sleep(START_DELAY)
        while True:
            try:
                await self.run_round()
            except Exception:  # never let one bad round end the loop
                logger.exception("deck sync round failed")
            await asyncio.sleep(self.interval)

    async def run_round(self) -> int:
        """One timed round; how many members were checked."""
        subs = list(dict.fromkeys(r["owner_sub"] for r in self.db.pending_baselines()))
        n = 0
        for sub in subs:
            if self.db.get_link(sub) is None:
                continue
            self._checked[sub] = time.monotonic()
            result = await self.check(sub)
            n += 1
            if result.get("error") in ("unavailable", "rate_limited"):
                break  # Archidekt is struggling: the rest wait for the next round
        return n

    # -- one member's check -----------------------------------------------------------------------
    async def _idle(self) -> bool:
        """Wait (up to IDLE_WAIT) until no member's Archidekt work is running or waiting. False
        when that takes longer, or while the pacer refuses calls (breaker or Retry-After)."""
        if self.decks.client.pacer.paused():
            return False
        deadline = time.monotonic() + IDLE_WAIT
        while self.decks.archidekt_busy():
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(IDLE_POLL)
        return True

    async def check(self, sub: str) -> dict[str, Any]:
        """Compare the member's waiting proposals with their decks on Archidekt (see the module
        text). Returns what it did, for the log and the tests; never raises for Archidekt errors."""
        async with self._lock:
            try:
                return await self._check(sub)
            except DeckError as exc:
                logger.info("deck sync stopped for a member: %s", exc.kind)
                return {"error": exc.kind}
            except Exception:  # never let a surprise stop the round or reach a member
                logger.exception("deck sync check failed")
                return {"error": "internal"}

    async def _check(self, sub: str) -> dict[str, Any]:
        self._forget_closed()
        if not await self._idle():
            return {"skipped": "busy"}
        rows = await self.decks.sync_deck_list(sub)
        if rows is None:
            return {"skipped": "slow"}
        listed = {str(d["id"]): str(d.get("updated_at") or "") for d in rows}
        waiting: dict[str, list[dict[str, Any]]] = {}
        for p in self.db.pending_baselines(sub):
            if p["deck_id"] in listed and p["id"] not in self._flags:
                waiting.setdefault(p["deck_id"], []).append(p)
        reads, flagged, skipped = 0, [], ""
        for deck_id, props in waiting.items():
            key = (sub, deck_id)
            if listed[deck_id] and self._seen.get(key) == listed[deck_id]:
                continue  # unchanged on Archidekt since its proposals were last compared
            if reads >= MAX_DECK_READS:
                skipped = "read_cap"
                break
            if self.decks.deck_busy(sub, deck_id):
                continue  # an apply or restore is writing it; compared on a later check
            if not await self._idle():
                skipped = "busy"
                break
            deck = await self.decks.sync_read_deck(sub, deck_id)
            reads += 1
            if self.decks.deck_busy(sub, deck_id):
                continue  # an apply started meanwhile: what was read may be halfway through it
            fingerprint = deck.fingerprint()
            still = {p["id"] for p in self.db.pending_baselines(sub)}
            for p in props:
                if p["id"] in still and p["baseline_fingerprint"] != fingerprint:
                    self._flags[p["id"]] = int(time.time())
                    flagged.append(p["id"])
            self._seen[key] = listed[deck_id]
        if flagged:
            logger.info("deck sync: %d waiting proposal(s) flagged out of date", len(flagged))
        out: dict[str, Any] = {"decks": len(listed), "reads": reads, "flagged": flagged}
        return {**out, "skipped": skipped} if skipped else out

    def _forget_closed(self) -> None:
        """Drop flags of proposals no longer waiting, and remembered decks past the cap."""
        if not self._flags and len(self._seen) < MAX_FLAGS:
            return
        live = {p["id"] for p in self.db.pending_baselines()}
        self._flags = {pid: at for pid, at in self._flags.items() if pid in live}
        if len(self._seen) >= MAX_FLAGS:
            self._seen.clear()
