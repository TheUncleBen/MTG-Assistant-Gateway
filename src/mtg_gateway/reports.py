"""Stored deck reports: the numbers a "test my deck" run produces, kept per user over time.

A report is one read of a deck plus what the gateway can compute or ask Mystic Forge for:
the deck statistics from ``deck_stats`` (always), a goldfish simulation and a decklist
validation (when the research service is configured and reachable). Reports are owner-scoped,
bounded per user and per deck, and never change anything on Archidekt. Mystic Forge stays
stateless: its results are copied into the gateway's own table, which is what the history
page and the assistant read back.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from typing import Any

from . import card_models, deck_stats
from . import forge as forge_module
from .archidekt import Deck
from .db import Database, _like
from .decklist import DecklistError, front_faces, parse_decklist, to_text
from .decks import DeckError, DeckService, _clean_deck_id, current_client, deck_to_text
from .forge import (
    FINAL_STATES,
    GIVE_UP_SECONDS,
    SOLO_TOO_OLD,
    ForgeClient,
    ForgeError,
    bracket_parts,
    refresh,
    start_run,
    start_solo_run,
    summarise,
)
from .mf_proxy import FULL_REPORT, MysticForgeProxy, busy_retry_after, is_busy
from .report_view import PREFLIGHT_ONLY, goldfish_settings, parse_goldfish, speed_numbers

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS reports (
    id TEXT PRIMARY KEY,
    owner_sub TEXT NOT NULL,
    deck_id TEXT NOT NULL,
    deck_name TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    taken_at INTEGER NOT NULL,
    stats_json TEXT NOT NULL,
    goldfish_json TEXT,
    validation_json TEXT,
    notes TEXT
);
CREATE INDEX IF NOT EXISTS reports_owner ON reports(owner_sub, taken_at);
CREATE INDEX IF NOT EXISTS reports_deck ON reports(owner_sub, deck_id, taken_at);
"""

MAX_REPORTS_PER_USER = 200
MAX_GAMES = 2000
DEFAULT_GAMES = 300
# Numbers the history page trends over time. Each is a path into the stats dict.
TREND_KEYS = (
    "card_count",
    "average_mana_value",
    "land_count",
    "price_total",
    "salt_total",
)
# The numbers a deck's trend (ReportService.trend: the History page and list_deck_reports) follows
# across its stored reports: key, label, unit. The first four come from the stored statistics,
# the last three from the stored goldfish simulation (report_view.speed_numbers).
TREND_METRICS = (
    ("average_mana_value", "Average mana value", ""),
    ("land_count", "Lands", ""),
    ("price_total", "Deck price", "$"),
    ("salt_total", "Salt total", ""),
    ("commander_cast_median_turn", "Commander cast (median turn)", "turn"),
    ("kill_median_turn", "Lethal damage dealt (median turn, when dealt)", "turn"),
    ("kill_pct", "Games dealing lethal damage by the last simulated turn", "%"),
)
TREND_LIMIT = 60
# A member's Forge runs go one at a time, so nobody fills the engine's shared queue. A report made
# while one of theirs is unfinished waits for it: its Forge section is "queued" with no engine job
# yet, and starts by itself when the runs ahead of it end, the queue kept in the reports table so a
# gateway restart carries on with it. At most this many wait. One exception:
# a comparison's second deck starts beside its first when the engine has places for two runs.
MAX_FORGE_WAITING = 2
# Where a report keeps its run's settings: a key of the stored statistics (no column of its own),
# taken out again when the report is read (``settings``).
RUN_KEY = "_run"
# What a member never sees of a Forge section: the start's own data, the copy it plays on (an
# internal address) and how many copies there are, and since when that copy has not answered.
NOT_SHOWN = frozenset(
    {"start", "engine", "engine_copies", "unanswered_since", "deck_text", "played", "earlier", "previous"}
)
# Forge games one report can hold at most, Play more games included (the Forge service's own most
# for one run, FORGE_MAX_GAMES).
MAX_REPORT_FORGE_GAMES = 50
# Why a report has no Forge games although the gateway runs Forge (a "skipped" section's note).
FORGE_NOT_ASKED = "Forge was not asked for this report (solo test only)."
FORGE_CONSTRUCTED = (
    "Forge plays Commander games against Commander precons only, so it does not run a 60-card deck."
)
FORGE_NO_COMMANDER = (
    "Forge plays Commander games against Commander precons only, and this deck has no commander in "
    "its Commander category, so Forge does not run it."
)
# The Forge solo test (forge.start_solo_run): with every report that simulates while Forge is set up,
# the deck also plays alone on Forge, by the full rules, beside the solo test's quick estimate. It
# is kept apart from the games against precons (forge_solo_json), takes no place in the member's
# queue of those, starts after them, and each member has one unfinished at a time (two for
# reports that share opponents). Each round of games gets an equal part of a time budget of play,
# never less than SOLO_MIN_GAME_SECONDS nor more than the Forge service's own limit; Java's start
# and a wait in Forge's queue come on top. A report is finished once both Forge parts are.
SOLO_BUDGET_SECONDS = 270
SOLO_MIN_GAME_SECONDS = 30
# A solo test whose engine stops answering is given up this long after it started (not after
# forge.GIVE_UP_SECONDS), so the member's next one is not kept waiting for hours.
SOLO_GIVE_UP_SECONDS = 3600
# A report's solo test is marked as coming (no engine job yet) when the report is stored; one
# still without a job this long after (a gateway restart while it started) never started.
SOLO_START_SECONDS = 600
SOLO_NOT_STARTED = "The Forge solo test did not start. Run the report again."
SOLO_ONE_AT_A_TIME = (
    "Another report of yours is still playing its Forge solo test, so this report has none. Run the "
    "report again once that one has finished."
)
SOLO_BUSY = "Forge's queue was full, so this report has no Forge solo test. Run the report again later."
# Other members' games against precons that wait in the gateway's queue go first (first come,
# first served): a solo test never takes Forge's place ahead of them.
SOLO_OTHERS_WAITING = (
    "Other members' Forge games are waiting their turn, so this report has no Forge solo test. Run the "
    "report again later."
)
FORGE_BUSY_GAVE_UP = (
    "Forge stayed busy with other runs for four hours, so this run was given up. Run the report again later."
)


class _Flight:
    """The run in progress for one (member, deck) and the requests waiting on it."""

    def __init__(self) -> None:
        self.lock = asyncio.Lock()
        self.users = 0
        self.finished = 0
        self.report_id: str | None = None


class PairHold:
    """Queue room kept for a Forge comparison's two runs from the check (``reserve_pair``) until
    each run is saved, so presses at once can't both pass the member's limit while the decks are
    read. ``left``: runs of the pair not saved yet."""

    def __init__(self) -> None:
        self.left = 2


class ReportService:
    def __init__(
        self,
        db: Database,
        decks: DeckService,
        mf: MysticForgeProxy | None,
        *,
        min_interval: int = 600,
        forge: ForgeClient | None = None,
        forge_games: int = 4,
    ):
        self.db = db
        self.decks = decks
        self.mf = mf
        # card models assistants wrote, kept for every member's later solo tests
        self.models = card_models.KeptModels(db)
        self.forge = forge
        self._forge_games = forge_games
        # Seconds between refreshes of unfinished Forge runs: each game shows within this of
        # finishing (was 15). A refresh is one small request to the engine per running run.
        self.forge_interval = 3.0
        self._forge_task: asyncio.Task[None] | None = None
        self._forge_cancels: set[asyncio.Task[None]] = set()  # held so a pending cancel is not collected
        # Held from the one-run-per-member check until the new run is saved (one gateway process).
        self._forge_start_lock = asyncio.Lock()
        # Per member, held from the one-solo-test check until its run is saved (one gateway process).
        self._solo_locks: dict[str, asyncio.Lock] = {}
        self._pair_holds: dict[str, list[PairHold]] = {}  # per member, see reserve_pair
        self.min_interval = min_interval
        # (member, report id) once a report is finished, its Forge games included; set by app.py
        # to notify the member (notify.py).
        self.on_finished: Any = None
        # One run at a time per (member, deck): a request that arrives while one runs waits for
        # it and then reuses its report instead of starting another simulation.
        self._runs: dict[tuple[str, str], _Flight] = {}
        with db.tx() as c:
            for stmt in SCHEMA.split(";"):
                if stmt.strip():
                    c.execute(stmt)
            # The app (OAuth client id, or the browser) that ran each report: an app deletes only
            # its own reports through the API. Older reports have none and only the browser may.
            if "created_by_client" not in {r["name"] for r in c.execute("PRAGMA table_info(reports)")}:
                c.execute("ALTER TABLE reports ADD COLUMN created_by_client TEXT")
            # The Forge run of a report (forge.py): its section, and its state for the refresher.
            if "forge_json" not in {r["name"] for r in c.execute("PRAGMA table_info(reports)")}:
                c.execute("ALTER TABLE reports ADD COLUMN forge_json TEXT")
                c.execute("ALTER TABLE reports ADD COLUMN forge_state TEXT")
            # The Forge refresher polls for unfinished runs. forge_state is the last column, behind
            # each report's large JSON, so without this index every poll read every whole report.
            c.execute(
                "CREATE INDEX IF NOT EXISTS reports_forge ON reports(forge_state) "
                "WHERE forge_state IS NOT NULL"
            )
            # The Forge solo test of a report (forge.start_solo_run), apart from its games against
            # precons: its section and state, indexed for the refresher in the same way.
            if "forge_solo_json" not in {r["name"] for r in c.execute("PRAGMA table_info(reports)")}:
                c.execute("ALTER TABLE reports ADD COLUMN forge_solo_json TEXT")
                c.execute("ALTER TABLE reports ADD COLUMN forge_solo_state TEXT")
            c.execute(
                "CREATE INDEX IF NOT EXISTS reports_forge_solo ON reports(forge_solo_state) "
                "WHERE forge_solo_state IS NOT NULL"
            )

    # -- running --------------------------------------------------------------
    async def run(
        self,
        sub: str,
        deck_ref: str,
        *,
        simulate: bool = True,
        games: int = DEFAULT_GAMES,
        options: dict[str, Any] | None = None,
        forge: bool | None = None,
        deck: Deck | None = None,
        snapshot_id: str | None = None,
        same_opponents_as: str | None = None,
        pair_with: str | Deck | None = None,
        pair_hold: PairHold | None = None,
    ) -> dict[str, Any]:
        """Read the deck, compute its statistics, optionally simulate and validate it, store
        the result and return it. Refuses a second report of an unchanged deck within
        ``min_interval`` seconds (returns the existing one instead) unless ``options`` (the
        simulator's knobs, see ``sim_options``) are given, since they change the simulation.
        ``forge`` asks for Forge games apart from the goldfish ones (None: as ``simulate``).
        ``deck`` is a deck already read (one of the member's snapshots, ``snapshot_id``), filed
        under its Archidekt deck like any other report. ``same_opponents_as`` is another of the
        member's reports: this run's Forge games seat that run's opponents, and the run
        is never a reused report. ``pair_with`` is the other deck of a Forge comparison (its id,
        or the deck already read): the opponents are picked for the higher of the two
        decks' brackets, and the run is never a reused report either. The other deck is read
        before anything is stored, so a deck that can't be read leaves no report behind.
        A ``deck`` given without ``snapshot_id`` is the Archidekt deck already read."""
        if not isinstance(games, int) or isinstance(games, bool) or games < 10 or games > MAX_GAMES:
            raise DeckError("invalid", f"games must be an integer from 10 to {MAX_GAMES}")
        options = sim_options(options)
        if same_opponents_as is not None and pair_with is not None:
            raise DeckError("invalid", BOTH_PAIRINGS)
        shared = self._share_from(sub, same_opponents_as) if same_opponents_as is not None else None
        key = (sub, f"snapshot:{snapshot_id}" if snapshot_id else _clean_deck_id(deck_ref))
        flight = self._runs.setdefault(key, _Flight())
        flight.users += 1
        seen = flight.finished
        try:
            async with flight.lock:
                if flight.finished > seen and flight.report_id and shared is None and pair_with is None:
                    # A run for this deck finished while this request waited: reuse its report
                    # instead of starting a second simulation (unless that run's research
                    # calls failed; then this request gets its own try).
                    out = self.get(sub, flight.report_id)
                    if _succeeded(
                        {"goldfish_json": out.get("goldfish"), "validation_json": out.get("validation")}
                    ):
                        out["reused"] = True
                        return out
                out = await self._run(
                    sub,
                    deck_ref,
                    simulate=simulate,
                    games=games,
                    options=options,
                    forge=simulate if forge is None else bool(forge),
                    deck=deck,
                    snapshot_id=snapshot_id,
                    shared=shared,
                    pair_with=pair_with,
                    pair_hold=pair_hold,
                )
                flight.report_id = out["report_id"]
                flight.finished += 1
                return out
        finally:
            flight.users -= 1
            if flight.users <= 0:
                self._runs.pop(key, None)

    async def run_text(
        self,
        sub: str,
        text: str,
        *,
        simulate: bool = True,
        games: int = DEFAULT_GAMES,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """The same validation and simulation for a pasted or hypothetical list. Returned, never
        stored: reports are filed under an Archidekt deck, and a list has none to compare over
        time (clone or create the deck to keep its reports)."""
        if not isinstance(games, int) or isinstance(games, bool) or games < 10 or games > MAX_GAMES:
            raise DeckError("invalid", f"games must be an integer from 10 to {MAX_GAMES}")
        options = sim_options(options)
        try:
            cards = parse_decklist(text)
        except DecklistError as exc:
            raise DeckError("invalid", f"decklist could not be read: {exc}") from exc
        stats = deck_stats.compute_from_text(cards)
        main = to_text(cards)
        commander = (stats.get("commanders") or [None])[0]
        mode = sim_mode(stats)
        goldfish: dict[str, Any] | None = None
        validation: dict[str, Any] | None = None
        if self.mf is not None:
            # The research service reads names through Scryfall's collection lookup, which
            # knows double-faced cards by their front face only.
            sim_text, sim_commander = front_faces(main), _front_face(commander)
            if mode != "constructed":  # a Commander check: see _run
                validation = await self._mf(
                    sub, "validate_decklist", {"decklist": sim_text, "commander": sim_commander}
                )
            if simulate and mode not in SIM_MODES:
                goldfish = {"tool": "goldfish_run", "ok": False, "text": mode}
            elif simulate:
                names = [c.name for c in cards if c.zone == "main"]
                # a pasted list's names are the member's own spelling: its models are never kept
                goldfish = await self._solo_test(sub, sim_text, games, options, mode, names, keep=False)
                _commander_aside(goldfish, sim_commander)
                _keep_quantities(goldfish, sim_text)
        return {
            "stored": False,
            "deck": {"id": None, "name": "Unnamed deck", "card_count": stats["card_count"]},
            "stats": stats,
            "goldfish": goldfish,
            "validation": validation,
            "has_goldfish": bool(goldfish and goldfish.get("ok")),
            "decklist_text": main,
        }

    async def _run(
        self,
        sub: str,
        deck_ref: str,
        *,
        simulate: bool,
        games: int,
        options: dict[str, Any],
        forge: bool = True,
        deck: Deck | None = None,
        snapshot_id: str | None = None,
        shared: dict[str, Any] | None = None,
        pair_with: str | Deck | None = None,
        pair_hold: PairHold | None = None,
    ) -> dict[str, Any]:
        if deck is None:
            deck = await self.decks.get_any_deck(sub, deck_ref)
        latest = self._latest(sub, deck.id)
        now = int(time.time())
        if (
            not options
            and shared is None
            and pair_with is None
            and latest is not None
            and latest["fingerprint"] == deck.fingerprint()
            and now - int(latest["taken_at"]) < self.min_interval
            and _succeeded(latest)
            # a run that asks to simulate never reuses one made without a simulation
            and not (simulate and self.mf is not None and latest.get("goldfish_json") is None)
            # nor does one that asks for Forge reuse one whose Forge games were not asked for
            and not (forge and self.forge is not None and _forge_not_asked(latest.get("forge_json")))
            # nor one whose Forge solo test did not come (skipped, failed): a run again plays it
            and not (
                forge
                and simulate
                and self.forge is not None
                and latest.get("run_mode") in SIM_MODES
                and latest.get("forge_solo_state") not in ("queued", "running", "done")
            )
        ):
            out = self.get(sub, latest["id"])
            out["reused"] = True
            return out
        stats = deck_stats.compute(deck)
        mode = sim_mode(stats)
        plays_forge = self.forge is not None and forge and mode == "commander"
        if pair_with is not None and not plays_forge:
            # Refused, the safe default, not ignored.
            raise DeckError("invalid", PAIR_NO_FORGE)
        # The other deck of a comparison is read now, before anything is stored and outside the
        # Forge start lock: a deck that can't be read stops the run with no report kept.
        other = await self.pair_deck(sub, pair_with) if pair_with is not None else None
        goldfish: dict[str, Any] | None = None
        validation: dict[str, Any] | None = None
        if self.mf is not None:
            # Front faces only: see run_text.
            text = front_faces(deck_to_text(deck))
            commander = _front_face((stats.get("commanders") or [None])[0])
            # validate_decklist checks a Commander deck (100 cards, the commander's colors): a
            # 60-card deck would only be told it is short, so it is not sent there.
            if mode != "constructed":
                validation = await self._mf(
                    sub, "validate_decklist", {"decklist": text, "commander": commander}
                )
            if simulate and mode not in SIM_MODES:
                goldfish = {"tool": "goldfish_run", "ok": False, "text": mode}
            elif simulate:
                names = [c.name for c in deck.cards if deck.in_deck(c)]
                goldfish = await self._solo_test(sub, text, games, options, mode, names, keep=True)
                _commander_aside(goldfish, commander)
                _keep_quantities(goldfish, text)
                _stored_note(goldfish)
        rid = "rep_" + secrets.token_urlsafe(9)
        # a comparison's second report: recorded as the first's partner now (Play more games)
        compare_of = self._compare_claim(sub, shared) if forge and mode == "commander" else None
        # The run's own settings, kept with the statistics (no column of their own) and read back
        # as the report's ``settings``: what the trend compares (run_settings).
        stored_stats = {
            **stats,
            RUN_KEY: {
                "mode": mode if mode in SIM_MODES else None,
                "simulate": bool(simulate),
                "forge": bool(forge),
                "games": games if simulate else None,
                "seed": options.get("seed"),
                **({"forge_seed": options["forge_seed"]} if "forge_seed" in options else {}),
                "until_turn": options.get("until_turn"),
                **({"snapshot_id": snapshot_id} if snapshot_id else {}),
                **({"compare_of": compare_of} if compare_of else {}),
            },
        }
        # the Forge solo test is marked as coming from the start, so the report is not finished
        # (nor its member notified) until it has played too
        plays_solo = self.forge is not None and forge and simulate and mode in SIM_MODES
        solo_mark = {"state": "queued", "started_at": now} if plays_solo else None
        with self.db.tx() as c:
            # Stored only while the member exists: a report still running when they deleted
            # their data is dropped, not written back for the deleted account.
            stored = c.execute(
                "INSERT INTO reports (id, owner_sub, deck_id, deck_name, fingerprint, taken_at, stats_json, "
                "goldfish_json, validation_json, created_by_client, forge_solo_json, forge_solo_state) "
                "SELECT ?,?,?,?,?,?,?,?,?,?,?,? "
                "WHERE EXISTS (SELECT 1 FROM users WHERE sub = ?)",
                (
                    rid,
                    sub,
                    deck.id,
                    deck.name,
                    deck.fingerprint(),
                    now,
                    json.dumps(stored_stats),
                    json.dumps(goldfish) if goldfish is not None else None,
                    json.dumps(validation) if validation is not None else None,
                    current_client.get(),
                    json.dumps(solo_mark) if solo_mark is not None else None,
                    "queued" if solo_mark is not None else None,
                    sub,
                ),
            ).rowcount
            if not stored:
                raise DeckError(
                    "not_found", "Your gateway data was deleted while the report ran; nothing was kept."
                )
            keep = (
                "owner_sub = ? AND id NOT IN ("
                "SELECT id FROM reports WHERE owner_sub = ? ORDER BY taken_at DESC, rowid DESC LIMIT ?)"
            )
            limit = (sub, sub, MAX_REPORTS_PER_USER)
            pruned = c.execute(
                f"SELECT forge_json, forge_solo_json FROM reports WHERE {keep} AND "
                "(forge_json IS NOT NULL OR forge_solo_json IS NOT NULL)",
                limit,
            )
            pruned_forge = [
                json.loads(r[column]) for r in pruned.fetchall() for column in (0, 1) if r[column]
            ]
            c.execute(f"DELETE FROM reports WHERE {keep}", limit)
        for section in pruned_forge:  # an unfinished run goes with its report, as in delete()
            self._cancel_forge(section)
        self.db.audit("report_created", sub=sub, detail={"report_id": rid, "deck_id": deck.id})
        # Shielded: a request cancelled now (its client gone) still saves both Forge parts, so no
        # engine job is left that nobody would cancel. The task is held until it ends.
        task = asyncio.get_running_loop().create_task(
            self._start_forge_parts(
                sub, rid, deck, stats, mode, games, options, forge, plays_solo, shared, other, pair_hold
            )
        )
        self._forge_cancels.add(task)
        task.add_done_callback(self._forge_cancels.discard)
        await asyncio.shield(task)
        return self.get(sub, rid)

    async def _start_forge_parts(
        self,
        sub: str,
        rid: str,
        deck: Deck,
        stats: dict[str, Any],
        mode: str,
        games: int,
        options: dict[str, Any],
        forge: bool,
        plays_solo: bool,
        shared: dict[str, Any] | None,
        other: Deck | None,
        pair_hold: PairHold | None,
    ) -> None:
        """Start report rid's games against precons (or say why there are none), then its Forge
        solo test, sent after them so it never holds them up; the solo test starts even when the
        first part fails, so its report is not left waiting for it."""
        try:
            await self._start_precon_part(
                sub, rid, deck, stats, mode, games, options, forge, shared, other, pair_hold
            )
        finally:
            if plays_solo:
                partner = shared.get("report_id") if shared else None
                await self._start_solo(sub, rid, deck_to_text(deck), deck.name, options.get("seed"), partner)

    async def _start_precon_part(
        self,
        sub: str,
        rid: str,
        deck: Deck,
        stats: dict[str, Any],
        mode: str,
        games: int,
        options: dict[str, Any],
        forge: bool,
        shared: dict[str, Any] | None,
        other: Deck | None,
        pair_hold: PairHold | None,
    ) -> None:
        if self.forge is None:
            self._finished(rid)
        elif not forge:
            # Asked for without Forge (a quick goldfish-only run): no engine job, no queue place.
            self._save_forge(rid, {"state": "skipped", "reason": "not_requested", "note": FORGE_NOT_ASKED})
        elif mode != "commander":
            # Forge plays Commander games against Commander precons only: a 60-card deck (or one
            # with no commander marked) never starts a run that is bound to fail, nor takes a place.
            note = FORGE_CONSTRUCTED if mode == "constructed" else FORGE_NO_COMMANDER
            self._save_forge(rid, {"state": "skipped", "reason": "not_commander", "note": note})
        else:
            # Forge's own seed when given, else the goldfish's, else one the gateway picks
            seed = options.get("forge_seed", options.get("seed"))
            bracket = _pair_bracket(deck, stats, other)
            copies = await self.forge.copies()  # the name's lookup, kept out of the start lock
            async with self._forge_start_lock:
                await self._start_or_queue_forge(
                    sub, rid, deck, games, seed, bracket, shared, copies, hold=pair_hold
                )

    async def pair_deck(self, sub: str, pair_with: str | Deck) -> Deck:
        """The other deck of a Forge comparison, read for its bracket. A deck that can't be read
        is refused with the reason (its kind and wait kept, for the busy page), saying that no
        report was made."""
        if isinstance(pair_with, Deck):
            return pair_with
        try:
            return await self.decks.get_any_deck(sub, pair_with)
        except DeckError as exc:
            raise DeckError(
                exc.kind,
                f"{PAIR_UNREAD} {_sentence(str(exc))}",
                retry_after=exc.retry_after,
                sent=exc.sent,
                **exc.extra,
            ) from exc

    async def _start_or_queue_forge(
        self,
        sub: str,
        rid: str,
        deck: Any,
        games: int,
        seed: Any,
        bracket: tuple[Any, ...],
        shared: dict[str, Any] | None = None,
        copies: list[str] | None = None,
        *,
        hold: PairHold | None = None,
    ) -> None:
        try:
            await self._start_or_queue(
                sub, rid, deck_to_text(deck), deck.name, games, seed, bracket, shared, copies, hold
            )
        finally:
            if hold is not None:  # this run is saved: its place is in the reports table now
                hold.left = max(0, hold.left - 1)

    async def _start_or_queue(
        self,
        sub: str,
        rid: str,
        text: str,
        name: str,
        games: int,
        seed: Any,
        bracket: tuple[Any, ...],
        shared: dict[str, Any] | None,
        copies: list[str] | None,
        hold: PairHold | None,
        more: dict[str, Any] | None = None,
    ) -> None:
        """Start report rid's Forge run, or queue it behind the member's unfinished one. ``more``
        (Play more games): what the extra games keep from the report's earlier ones (more_start)."""
        seed = seed if isinstance(seed, int) else None
        if self._forge_running(sub, rid) and not self._side_by_side(sub, shared):
            # One unfinished Forge run per member, so nobody can fill the shared engine's queue:
            # this one waits for theirs (MAX_FORGE_WAITING at most, counting the places another
            # comparison has reserved).
            full = self._queue_full(sub, rid, hold)
            if full and more is not None:  # extra games: nothing starts, the report stays as it was
                raise DeckError(
                    "invalid", PAIR_SETTING_UP if full == PAIR_SETTING_UP_SKIP else MORE_QUEUE_FULL
                )
            if full:
                self._save_forge(rid, {"state": "skipped", "error": full})
                return
            self._save_forge(
                rid, self._waiting_section(text, name, games, seed, bracket, shared=shared, more=more)
            )
            self._ensure_forge_refresher()
            return
        await self._start_forge(
            rid,
            text,
            name,
            games,
            seed,
            bracket,
            shared=shared,
            copies=copies,
            new_for=(sub, hold),
            more=more,
        )

    def _queue_full(self, sub: str, rid: str, hold: PairHold | None) -> str | None:
        """Why the member's queue can't take one more waiting run (report rid's), or None when it
        can: MAX_FORGE_WAITING at most, counting the places another comparison has reserved."""
        waiting = sum(1 for r, _, s in self._member_runs(sub) if r != rid and _waiting(s))
        reserved = self._reserved(sub, hold)
        if waiting + reserved < MAX_FORGE_WAITING:
            return None
        if reserved:  # places another comparison keeps: its reports are not made yet
            return PAIR_SETTING_UP_SKIP
        return (
            f"You already have {waiting} reports waiting for Forge, and each member can have at most "
            f"{MAX_FORGE_WAITING} waiting, so this report has no Forge games. Run the report again once "
            f"fewer than {MAX_FORGE_WAITING} of your reports are waiting."
        )

    def pair_room(self, sub: str) -> str | None:
        """Why the member's Forge queue can't take both decks of a comparison now, or None when it
        can. Each deck's run is started or waits its turn; with one of theirs unfinished both wait,
        and at most MAX_FORGE_WAITING do. Asked before either report is made, so a comparison
        never makes one deck's report with Forge games and the other's without."""
        runs = self._member_runs(sub)
        playing = sum(1 for _, _, s in runs if not _waiting(s))
        waiting, reserved = len(runs) - playing, self._reserved(sub)
        if (not runs and not reserved) or waiting + reserved + 2 <= MAX_FORGE_WAITING:
            return None
        if reserved:  # places another comparison keeps: its reports are not made yet
            return PAIR_SETTING_UP
        queued = (
            f"{waiting} report waiting for its games"
            if waiting == 1
            else f"{waiting} reports waiting for their games"
        )
        have = f"{playing} Forge run{'' if playing == 1 else 's'} going and {queued}" if playing else queued
        return (
            f"You already have {have}. Comparing two decks needs room for 2 more, and each member can "
            f"have at most {MAX_FORGE_WAITING} reports waiting, so nothing was started. Try again when "
            "none of your reports is waiting for its games."
        )

    async def play_more(self, sub: str, report_id: str) -> dict[str, Any]:
        """Play more games: another round of Forge games for a report whose games have finished,
        against the same opponents, added to its earlier ones (its win rates and 95% intervals then
        count them all). It starts or waits like any run of the member's; with no room in their
        queue nothing is started and the report stays as it was. A report of a Forge comparison
        gets the same number of games as the other deck's report, so the two stay even, and
        nothing starts unless the queue has room for both. The report page's button, the
        assistants' play_more_games tool and the app's API all come here, so the rules are the
        same everywhere; a refusal carries its ``reason`` code (MORE_REFUSALS, or ``notfound`` for a
        report that is not the member's)."""
        try:
            return await self._play_more(sub, report_id)
        except DeckError as exc:
            code = more_reason(str(exc))
            if code and "reason" not in exc.extra:
                exc.extra["reason"] = code
            raise

    async def _play_more(self, sub: str, report_id: str) -> dict[str, Any]:
        if self.forge is None:
            raise DeckError("invalid", MORE_NO_FORGE)
        copies = await self.forge.copies()  # the name's lookup, kept out of the start lock
        async with self._forge_start_lock:
            mine = self._own_forge(sub, str(report_id))
            if mine is None:
                raise DeckError("not_found", "No such report for your account.", reason="notfound")
            rid, section = mine
            games, why, pair = self._more_for(sub, rid, section)
            if why:
                raise DeckError("invalid", why)
            sides = (
                [mine] if pair is None else sorted([mine, pair], key=lambda s: _shared_id(s[1]) is not None)
            )
            hold: PairHold | None = None
            if pair is not None:
                hold, full = self.reserve_pair(sub)
                if full is not None:
                    raise DeckError(
                        "invalid", PAIR_SETTING_UP if full == PAIR_SETTING_UP else MORE_PAIR_QUEUE
                    )
            try:
                first_id, first = sides[0]
                for i, (side_id, side) in enumerate(sides):
                    if i:
                        note = (self._own_forge(sub, first_id) or (first_id, {}))[1].get("more_note")
                        if note:  # the first deck's games did not start: neither do the other's
                            self._save_forge(side_id, {**side, "more_note": str(note)})
                            continue
                    shared = {"report_id": first_id, "deck_name": str(first["seats"][0])} if i else None
                    try:
                        await self._start_or_queue(
                            sub,
                            side_id,
                            side["deck_text"],
                            str(side["seats"][0]),
                            games,
                            None,
                            (None, None),
                            shared,
                            copies,
                            hold,
                            more=self._more_start(side, games, sides[1 - i] if pair is not None else None),
                        )
                    except DeckError as exc:
                        if i:  # the second deck's games can't start: nor do the first's
                            self._undo_more(sub, first_id, first, str(exc))
                        raise
                    if hold is not None:
                        hold.left = max(0, hold.left - 1)
            finally:
                if hold is not None:
                    self.release_pair(sub, hold)
        detail = {"report_id": rid, "games": games, **({"with": pair[0]} if pair else {})}
        self.db.audit("report_more_games", sub=sub, client_id=current_client.get(), detail=detail)
        return self.get(sub, rid)

    def _undo_more(self, sub: str, rid: str, before: dict[str, Any], note: str) -> None:
        """Take back report rid's extra games (stopping their Forge job) and put the report back
        as it was, with why: a comparison's other deck could not get as many."""
        now = self._own_forge(sub, rid)
        if now is None:
            return
        self._cancel_forge(now[1])
        self._save_forge(rid, {**{k: v for k, v in before.items() if k != "more_note"}, "more_note": note})

    def _own_forge(self, sub: str, rid: str) -> tuple[str, dict[str, Any]] | None:
        """Report rid of the member with its Forge section ({} without one), or None."""
        row = self.db._one("SELECT id, forge_json FROM reports WHERE id = ? AND owner_sub = ?", (rid, sub))
        if row is None:
            return None
        section = json.loads(row["forge_json"]) if row["forge_json"] else {}
        return str(row["id"]), section if isinstance(section, dict) else {}

    def _more_pair(self, sub: str, rid: str, section: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
        """The other report of a Forge comparison, as (id, section), when report rid is one of a
        pair: a compare_with report and the report recorded as its partner when it was made (the
        first same_opponents_as report of it, ``compare_of``). A report that only seats another's
        opponents later, with no comparison behind it, has none."""
        partner = self._compare_of(sub, rid)
        if partner is not None and self._compare_partner(sub, partner) == rid:
            return self._own_forge(sub, partner)
        partner = self._compare_partner(sub, rid)
        return self._own_forge(sub, partner) if partner is not None else None

    def _compare_of(self, sub: str, rid: str) -> str | None:
        """The compare_with report that report rid was made as the partner of, or None."""
        row = self.db._one("SELECT stats_json FROM reports WHERE id = ? AND owner_sub = ?", (rid, sub))
        stats = json.loads(row["stats_json"]) if row is not None and row["stats_json"] else {}
        run = stats.get(RUN_KEY) if isinstance(stats, dict) else None
        claim = run.get("compare_of") if isinstance(run, dict) else None
        return claim if isinstance(claim, str) else None

    def _compare_partner(self, sub: str, first: str) -> str | None:
        """The partner recorded for compare_with report ``first``: the oldest report made as its
        partner (two made at once: the older one is)."""
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT id, stats_json FROM reports WHERE owner_sub = ? AND id != ? AND "
                "instr(stats_json, ?) > 0 ORDER BY taken_at, rowid",
                (sub, first, first),
            ).fetchall()
        for row in rows:
            if self._compare_of(sub, str(row["id"])) == first:
                return str(row["id"])
        return None

    def _compare_claim(self, sub: str, shared: dict[str, Any] | None) -> str | None:
        """The compare_with report a new same_opponents_as report becomes the partner of: the
        report it seats, when that one picked its table for a comparison and has no partner yet."""
        target = shared.get("report_id") if isinstance(shared, dict) else None
        if not isinstance(target, str) or self._compare_of(sub, target) is not None:
            return None
        found = self._own_forge(sub, target)
        section = found[1] if found is not None else {}
        start = section.get("start") if isinstance(section.get("start"), dict) else {}
        picked = (section.get("opponent_choice") or {}).get("pair") or bracket_parts(start.get("bracket"))[3]
        if not picked or _shared_id(section) is not None or self._compare_partner(sub, target) is not None:
            return None
        return target

    def _more_for(
        self, sub: str, rid: str, section: dict[str, Any]
    ) -> tuple[int, str | None, tuple[str, dict[str, Any]] | None]:
        """(games, why not, the comparison's other report) for Play more games on report rid:
        a comparison's two reports get the same number, so the fewer either can take."""
        games, why = more_room(section, self.forge_games)
        if why:
            return 0, why, None
        pair = self._more_pair(sub, rid, section)
        if pair is None:
            return games, None, None
        other_games, other_why = more_room(pair[1], self.forge_games)
        if other_why == MORE_NOT_ENDED:
            return 0, MORE_PAIR_WAIT, pair
        if other_why:
            return 0, MORE_PAIR_CANT, pair
        return min(games, other_games), None, pair

    def _more_start(
        self, section: dict[str, Any], games: int, partner: tuple[str, dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """What extra games keep from a report's earlier ones (Play more games): the earlier
        games and their result, the table, and the report as it was if they do not start. On a
        comparison, ``partner`` is the other deck's report as it was, put back the same way if
        these games do not start, so the two stay even."""
        played = list(section["played"])
        total = len(played) + games
        keep = {
            "earlier": played,
            "played": played,
            "games_requested": total,
            "result": summarise({"results": played, "games_requested": total}, section["seats"]),
            "seats": section["seats"],
            "opponent_choice": section.get("opponent_choice") or {},
            "not_played": section.get("not_played") or [],
            "earlier_runs": section.get("earlier_runs") or [{**_run_record(section), "games": len(played)}],
            "deck_text": section["deck_text"],
            **({"shared_with": section["shared_with"]} if section.get("shared_with") else {}),
        }
        return {
            "opponents": section["seats"][1:],
            "choice": section.get("opponent_choice") or {},
            "keep": keep,
            "previous": {k: v for k, v in section.items() if k != "more_note"},
            **({"partner": {"report_id": partner[0], "previous": partner[1]}} if partner else {}),
        }

    def reserve_pair(self, sub: str) -> tuple[PairHold | None, str | None]:
        """Check the member's room for a comparison's two runs (``pair_room``) and reserve it in
        the same step, before either deck is read: (the hold, None), or (None, why not). The
        caller passes the hold to both runs and calls ``release_pair`` when done, failed or not."""
        full = self.pair_room(sub)
        if full is not None:
            return None, full
        hold = PairHold()
        self._pair_holds.setdefault(sub, []).append(hold)
        return hold, None

    def release_pair(self, sub: str, hold: PairHold | None) -> None:
        """Give back what a comparison's hold still reserves (nothing once both runs are saved)."""
        holds = self._pair_holds.get(sub, [])
        if hold in holds:
            holds.remove(hold)
        if not holds:
            self._pair_holds.pop(sub, None)

    def _reserved(self, sub: str, mine: PairHold | None = None) -> int:
        """Places other comparisons of the member hold for runs not saved yet."""
        return sum(h.left for h in self._pair_holds.get(sub, []) if h is not mine)

    def _side_by_side(self, sub: str, shared: dict[str, Any] | None) -> bool:
        """Whether a comparison's second deck may start now, beside its first: the member's
        one unfinished run is the first deck's, started with compare_with, and the engine has
        places for two runs. Then the pair plays in about the time of one run, still inside the
        engine's CPU and memory limit (FORGE_MAX_PARALLEL places, each one Forge process)."""
        if shared is None:
            return False
        runs = self._member_runs(sub)
        if len(runs) != 1 or runs[0][0] != shared.get("report_id"):
            return False
        first = runs[0][2]
        places, copies = first.get("max_parallel"), first.get("engine_copies")
        room = (isinstance(places, int) and places >= 2) or (isinstance(copies, int) and copies >= 2)
        return (
            bool(first.get("job_id"))
            and not _waiting(first)
            and bool((first.get("opponent_choice") or {}).get("pair"))
            and room
        )

    def _waiting_section(
        self,
        text: str,
        name: str,
        games: int,
        seed: int | None,
        bracket: Any,
        shared: dict[str, Any] | None = None,
        more: dict[str, Any] | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        """A Forge section waiting in the gateway's queue, with no engine job yet. Extra games
        (``more``) show the report's earlier games while they wait."""
        start = {"text": text, "name": name, "games": games, "seed": seed, "bracket": bracket}
        if shared is not None:
            start["shared"] = shared
        if more is not None:
            start["more"] = more
            return {
                **more["keep"],
                "state": "queued",
                "waiting": True,
                "queued_at": int(time.time()),
                "start": start,
                **extra,
            }
        return {
            "state": "queued",
            "waiting": True,
            "queued_at": int(time.time()),
            "games_requested": min(games, self.forge_games),
            # What starting it later takes; kept with the report, never shown.
            "start": start,
            **({"shared_with": _shared_link(shared)} if shared is not None else {}),
            **extra,
        }

    def _share_from(self, sub: str, report_id: Any) -> dict[str, Any]:
        """What seating another of the member's reports' opponents takes, checked now:
        that report must be the member's own and have (or be waiting for) Forge games. Its
        opponents themselves are read when this run starts (``_shared_now``), as that run may
        still be waiting for its own."""
        if not isinstance(report_id, str) or not report_id.strip():
            raise DeckError("invalid", "same_opponents_as must be the id of one of your reports.")
        row = self.db._one(
            "SELECT id, deck_name, forge_json FROM reports WHERE id = ? AND owner_sub = ?",
            (report_id.strip(), sub),
        )
        if row is None:
            raise DeckError("not_found", "No such report for your account.")
        section = json.loads(row["forge_json"]) if row["forge_json"] else None
        if not isinstance(section, dict) or not (
            section.get("seats") or (section.get("start") or {}).get("text")
        ):
            raise DeckError("invalid", NO_FORGE_TO_SHARE)
        return {"report_id": str(row["id"]), "deck_name": str(row["deck_name"] or "")}

    def _shared_now(self, sub: str | None, shared: dict[str, Any]) -> dict[str, Any]:
        """The shared run's opponents as they stand now: its seats once it started, else the
        bracket its own run is waiting with (forge.shared_opponents picks for that)."""
        out = dict(shared)
        rid = shared.get("report_id")
        if not sub or not isinstance(rid, str):
            return out  # never read a report without its owner
        row = self.db._one("SELECT forge_json FROM reports WHERE id = ? AND owner_sub = ?", (rid, sub))
        section = json.loads(row["forge_json"]) if row is not None and row["forge_json"] else {}
        seats = section.get("seats") if isinstance(section, dict) else None
        if isinstance(seats, list) and len(seats) > 1:
            out["opponents"] = seats[1:]
        elif isinstance(section, dict) and (section.get("start") or {}).get("bracket"):
            out["bracket"] = section["start"]["bracket"]
        choice = (section.get("opponent_choice") or {}) if isinstance(section, dict) else {}
        if choice.get("pair") and choice.get("rule") != "newest":
            out["pair"] = True  # a comparison's run whose table the higher bracket picked
        return out

    # -- Forge runs (background) ----------------------------------------------
    def _live(self, key: str, fallback: int) -> int:
        """A Forge setting as the admin Settings page or the deployment has it now (decks.live,
        config.MANAGED_SETTINGS), else the value this service was made with."""
        live = getattr(self.decks, "live", None)
        try:
            value = live.get(key) if live is not None else None
        except Exception:  # a settings store that cannot be read never stops a report
            logger.exception("reading the setting %s failed", key)
            value = None
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 1 else fallback

    @property
    def forge_games(self) -> int:
        """Forge games per run (forge_games, 1-50)."""
        return self._live("forge_games", self._forge_games)

    @property
    def forge_parallel(self) -> int:
        """Forge games a run asks to play at once (forge_parallel_games, 1-16); the Forge service
        plays as many as it has places for (FORGE_MAX_PARALLEL, else what its machine fits)."""
        return self._live("forge_parallel_games", 4)

    async def _start_forge(
        self,
        rid: str,
        text: str,
        name: str,
        games: int,
        seed: int | None = None,
        bracket: Any = (None, None),
        busy_since: int | None = None,
        shared: dict[str, Any] | None = None,
        copies: list[str] | None = None,
        new_for: tuple[str, PairHold | None] | None = None,
        more: dict[str, Any] | None = None,
        queued_at: int | None = None,
    ) -> None:
        """Start a Forge run for report rid. new_for (member, their comparison's hold) marks a new
        run: if the engine is busy it would wait, so it must fit the member's queue. ``more``: extra
        games for a report that has some (Play more games), at the same table. ``queued_at``: when
        a waiting run joined the member's queue, kept if it has to wait again."""
        assert self.forge is not None
        level, source, of_deck, pair = bracket_parts(bracket)
        try:
            if shared is not None:
                shared = self._shared_now(self._owner(rid), shared)
            # several copies of the Forge service: the least busy one, kept with the run (engine)
            # so it is always asked about where it plays; one copy: as before, nothing kept
            client, engine, how_many = await self.forge.pick(copies)
            section = await start_run(
                client,
                text,
                name,
                games=min(games, self.forge_games),
                seed=seed,
                bracket=level,
                bracket_source=source,
                bracket_deck=of_deck,
                pair=pair,
                parallel=self.forge_parallel,
                shared=shared,
                more={"opponents": more["opponents"], "choice": more["choice"]} if more else None,
            )
            if more is not None and section.get("job_id"):
                runs = [*more["keep"].get("earlier_runs", []), _run_record(section)]
                section = {**section, **more["keep"], "earlier_runs": runs}
            if engine is not None and section.get("job_id"):
                section.update(engine=engine, engine_copies=how_many)
        except ForgeError as exc:
            if exc.kind == "busy":
                # The engine's own queue (FORGE_MAX_QUEUE, shared by the whole gateway) is full:
                # the run waits in the gateway's queue instead of failing, and the refresher asks
                # again on its next round (forge_interval) until GIVE_UP_SECONDS after the first try.
                since = busy_since if isinstance(busy_since, int) else int(time.time())
                full = self._queue_full(new_for[0], rid, new_for[1]) if new_for else None
                if time.time() - since > GIVE_UP_SECONDS:
                    section = {"state": "failed", "error": FORGE_BUSY_GAVE_UP}
                elif full:  # waiting now would pass the member's limit (a comparison holds places)
                    section = {"state": "skipped", "error": full}
                else:
                    section = self._waiting_section(
                        text,
                        name,
                        games,
                        seed,
                        bracket,
                        shared=shared,
                        more=more,
                        engine_busy=True,
                        busy_since=since,
                        # its place in the member's queue stays where it was
                        **({"queued_at": queued_at} if isinstance(queued_at, int) else {}),
                        retry_after=max(1, round(self.forge_interval)),
                    )
            else:
                section = {
                    "state": "failed",
                    "error": str(exc),
                    **({"detail": exc.detail} if exc.detail else {}),
                }
        except Exception:  # an unexpected reply must not turn a stored report into a tool error
            logger.exception("starting a Forge run failed for %s", rid)
            section = {"state": "failed", "error": "Forge gave an unexpected answer."}
        if more is not None and section.get("state") in ("failed", "skipped"):
            # the extra games did not start: the report keeps its earlier games, with the reason
            why = section.get("error")
            if section.get("state") == "skipped":  # the member's queue (or a comparison's places) full
                why = PAIR_SETTING_UP if why == PAIR_SETTING_UP_SKIP else MORE_QUEUE_FULL
            section = {**more["previous"], "more_note": str(why or "")}
            partner, owner = more.get("partner"), self._owner(rid)
            if isinstance(partner, dict) and isinstance(partner.get("report_id"), str) and owner:
                # a comparison: the other deck's extra games are taken back too
                self._undo_more(owner, partner["report_id"], partner.get("previous") or {}, str(why or ""))
        if not self._save_forge(rid, section):  # the report was deleted while the run started
            self._cancel_forge(section)
            return
        self._ensure_forge_refresher()

    async def _start_solo(
        self, sub: str, rid: str, text: str, name: str, seed: Any, partner: Any = None
    ) -> None:
        """Start report rid's Forge solo test (see SOLO_BUDGET_SECONDS), or say why there is none.
        ``partner``: the report a comparison's second deck seats the opponents of, whose own solo
        test may play beside this one."""
        lock = self._solo_locks.setdefault(sub, asyncio.Lock())
        async with lock:
            await self._start_solo_locked(sub, rid, text, name, seed, partner)

    async def _start_solo_locked(
        self, sub: str, rid: str, text: str, name: str, seed: Any, partner: Any
    ) -> None:
        assert self.forge is not None
        row = self.db._one("SELECT forge_solo_json FROM reports WHERE id = ?", (rid,))
        if row is None:
            return  # the report was deleted meanwhile
        marked = row["forge_solo_json"]
        if marked is not None and json.loads(marked).get("state") != "queued":
            return  # given up (SOLO_START_SECONDS) or started already
        section: dict[str, Any]
        if self._solo_running(sub, rid, partner):
            section = {"state": "skipped", "error": SOLO_ONE_AT_A_TIME}
        elif self._others_waiting(sub):
            section = {"state": "skipped", "error": SOLO_OTHERS_WAITING}
        else:
            try:
                client, engine, how_many = await self.forge.pick()
                health = await client.health()
                if not health.get("solo"):  # a Forge image from before the solo test
                    section = {"state": "skipped", "error": SOLO_TOO_OLD}
                else:
                    places = health.get("max_parallel")
                    places = places if isinstance(places, int) and places >= 1 else 1
                    parallel = max(1, min(self.forge_parallel, places))
                    cap = health.get("game_seconds")
                    cap = cap if isinstance(cap, int) and cap >= 1 else 240
                    games = self.forge_games
                    section = await start_solo_run(
                        client,
                        text,
                        name,
                        games=games,
                        game_seconds=solo_game_seconds(games, parallel, cap),
                        seed=seed if isinstance(seed, int) and not isinstance(seed, bool) else None,
                        parallel=parallel,
                    )
                    if engine is not None and section.get("job_id"):
                        section.update(engine=engine, engine_copies=how_many)
            except ForgeError as exc:
                section = (
                    {"state": "skipped", "error": SOLO_BUSY}
                    if exc.kind == "busy"
                    else {"state": "failed", "error": str(exc)}
                )
            except Exception:  # an unexpected reply must not turn a stored report into a tool error
                logger.exception("starting a Forge solo test failed for %s", rid)
                section = {"state": "failed", "error": "Forge gave an unexpected answer."}
        if section.get("error") == SOLO_TOO_OLD:
            logger.warning(
                "Forge solo test skipped: the Forge image is older than it needs; redeploy "
                "with this version's stack file"
            )
        # only over the mark read above: a report deleted, or a mark given up, while it started
        # keeps that, and the new job is stopped
        if not self._save_solo(rid, section, unless_changed_from=marked):
            self._cancel_forge(section)
            return
        self._ensure_forge_refresher()

    def _others_waiting(self, sub: str) -> bool:
        """Whether another member's run waits in the gateway's queue for its turn on Forge."""
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT forge_json FROM reports WHERE forge_state = 'queued' AND owner_sub != ?", (sub,)
            ).fetchall()
        for r in rows:
            try:
                section = json.loads(r["forge_json"])
            except (TypeError, ValueError):
                continue
            if isinstance(section, dict) and _waiting(section):
                return True
        return False

    def forget_member(self, sub: str) -> None:
        """Before a member's data is deleted: stop the Forge jobs behind their unfinished reports
        (games against precons and solo tests), as deleting one report does."""
        finals = tuple(FINAL_STATES)
        marks = ",".join("?" * len(finals))
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT forge_json, forge_state, forge_solo_json, forge_solo_state FROM reports "
                f"WHERE owner_sub = ? AND ((forge_state IS NOT NULL AND forge_state NOT IN ({marks})) "
                f"OR (forge_solo_state IS NOT NULL AND forge_solo_state NOT IN ({marks})))",
                (sub, *finals, *finals),
            ).fetchall()
        for r in rows:
            for column in ("forge_json", "forge_solo_json"):
                try:
                    section = json.loads(r[column]) if r[column] else None
                except (TypeError, ValueError):
                    continue
                self._cancel_forge(section)

    def _solo_running(self, sub: str, rid: str, partner: Any = None) -> bool:
        """Whether another of the member's Forge solo tests (one with an engine job, not merely
        marked as coming) is unfinished; ``partner``'s does not count."""
        finals = tuple(FINAL_STATES)
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT 1 FROM reports WHERE owner_sub = ? AND id != ? AND id != ? "
                "AND forge_solo_state IS NOT NULL "
                f"AND forge_solo_state NOT IN ({','.join('?' * len(finals))}) "
                "AND json_extract(forge_solo_json, '$.job_id') IS NOT NULL LIMIT 1",
                (sub, rid, str(partner or ""), *finals),
            ).fetchone()
        return row is not None

    def _save_solo(
        self, rid: str, section: dict[str, Any], *, unless_changed_from: str | None = None
    ) -> bool:
        """Store the report's Forge solo test, as _save_forge does its games against precons; one
        that reaches a final state gets the time it did (``finished_at``), and finishes the report
        if its games against precons have too."""
        if section.get("state") in FINAL_STATES and "finished_at" not in section:
            section = {**section, "finished_at": int(time.time())}
        same = "" if unless_changed_from is None else " AND forge_solo_json = ?"
        args = () if unless_changed_from is None else (unless_changed_from,)
        with self.db.tx() as c:
            was = c.execute(
                "SELECT forge_state, forge_solo_state FROM reports WHERE id = ?", (rid,)
            ).fetchone()
            cur = c.execute(
                f"UPDATE reports SET forge_solo_json = ?, forge_solo_state = ? WHERE id = ?{same}",
                (json.dumps(section), section.get("state"), rid, *args),
            )
        if cur.rowcount > 0 and was is not None:
            if not _report_done(was[0], was[1]) and _report_done(was[0], section.get("state")):
                self._finished(rid)
        return cur.rowcount > 0

    def _owner(self, rid: str) -> str | None:
        row = self.db._one("SELECT owner_sub FROM reports WHERE id = ?", (rid,))
        return str(row["owner_sub"]) if row is not None else None

    def _forge_running(self, sub: str, rid: str) -> bool:
        finals = tuple(FINAL_STATES)
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT 1 FROM reports WHERE owner_sub = ? AND id != ? AND forge_state IS NOT NULL AND "
                f"forge_state NOT IN ({','.join('?' * len(finals))}) LIMIT 1",
                (sub, rid, *finals),
            ).fetchone()
        return row is not None

    def _member_runs(self, sub: str) -> list[tuple[str, str, dict[str, Any]]]:
        """The member's unfinished Forge runs, oldest report first: (report id, deck name, section).
        The first that is not waiting is the one playing; the waiting ones follow in turn."""
        finals = tuple(FINAL_STATES)
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT id, deck_name, forge_json FROM reports WHERE owner_sub = ? AND forge_state IS NOT "
                f"NULL AND forge_state NOT IN ({','.join('?' * len(finals))}) ORDER BY taken_at, rowid",
                (sub, *finals),
            ).fetchall()
        out = []
        for r in rows:
            try:
                section = json.loads(r["forge_json"])
            except (TypeError, ValueError):
                continue
            if isinstance(section, dict):
                out.append((str(r["id"]), str(r["deck_name"] or ""), section))
        # the runs going first, then the waiting ones in the order they joined the queue (extra games
        # for an older report wait behind runs queued before them)
        out.sort(key=lambda r: (_waiting(r[2]), _number_or_zero(r[2].get("queued_at")) * _waiting(r[2])))
        return out

    async def _start_waiting_runs(self) -> None:
        """Start each member's next waiting Forge run once nothing of theirs is playing."""
        with self.db._lock:
            subs = [
                r["owner_sub"]
                for r in self.db._conn.execute(
                    "SELECT DISTINCT owner_sub FROM reports WHERE forge_state = 'queued'"
                ).fetchall()
            ]
        copies = await self.forge.copies() if subs and self.forge else None  # outside the lock

        def joined(sub: str) -> int:
            runs = self._member_runs(sub)
            return _number_or_zero(runs[0][2].get("queued_at")) if runs else 0

        # first come, first served across members: the member whose next run joined first asks
        # Forge first, so a full Forge queue never lets a later run overtake an earlier one
        for sub in sorted(subs, key=joined):
            async with self._forge_start_lock:
                runs = self._member_runs(sub)
                if not runs or not _waiting(runs[0][2]):
                    continue  # one of theirs is still playing (or nothing waits)
                rid, _, section = runs[0]
                start = section.get("start") or {}
                if not isinstance(start.get("text"), str):
                    self._save_forge(rid, {"state": "failed", "error": "This waiting run lost its deck."})
                    continue
                await self._start_forge(
                    rid,
                    start["text"],
                    str(start.get("name") or ""),
                    int(start.get("games") or self.forge_games),
                    start.get("seed") if isinstance(start.get("seed"), int) else None,
                    start.get("bracket") or (None, None),
                    busy_since=section.get("busy_since") if section.get("engine_busy") else None,
                    shared=start.get("shared") if isinstance(start.get("shared"), dict) else None,
                    copies=copies,
                    more=start.get("more") if isinstance(start.get("more"), dict) else None,
                    queued_at=section.get("queued_at") if isinstance(section.get("queued_at"), int) else None,
                )

    def measured_game_seconds(self) -> float | None:
        """The average Forge game time of the latest finished run on this gateway, for estimates."""
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT forge_json FROM reports WHERE forge_state = 'done' ORDER BY taken_at DESC LIMIT 10"
            ).fetchall()
        for r in rows:
            try:
                seconds = (json.loads(r["forge_json"]).get("result") or {}).get("average_game_seconds")
            except (TypeError, ValueError, AttributeError):
                continue
            if _number(seconds) and seconds > 0:
                return float(seconds)
        return None

    def _forge_view(self, sub: str, rid: str, section: dict[str, Any]) -> dict[str, Any]:
        """The Forge section as a reader sees it: without what starting it takes, and while it is
        unfinished, with its place in the member's queue (``position``, the report and job it waits
        for) and the expected time left, from the measured average game time when known."""
        out = {k: v for k, v in section.items() if k not in NOT_SHOWN}
        out["games_done"] = _done_games(out)
        if out.get("state") in FINAL_STATES:
            # how many games Play more games would add (0: it can't)
            games, _, pair = self._more_for(sub, rid, section) if self.forge is not None else (0, None, None)
            out["more_games"] = games
            if pair is not None and games:  # a comparison: the other deck gets as many
                out["more_with"] = str((pair[1].get("seats") or [""])[0])
            return out
        runs = self._member_runs(sub)
        ids = [r[0] for r in runs]
        mine = ids.index(rid) if rid in ids else len(ids)
        # this run's own average once it has played a game, else the gateway's latest measure
        per_game = (out.get("result") or {}).get("average_game_seconds") if _done_games(out) else None
        if not (_number(per_game) and per_game > 0):
            per_game = self.measured_game_seconds()
        left = 0
        if out.get("state") == "queued":
            # only a run that has not started waits for the ones ahead (a comparison's second deck
            # may be playing beside its first)
            for _, _, s in runs[:mine]:
                left += _games_left(s)
        if _waiting(out):
            waiting = [r for r in runs if _waiting(r[2])]
            out["position"] = 1 + next((i for i, r in enumerate(waiting) if r[0] == rid), len(waiting))
            if mine > 0:
                ahead_id, ahead_name, ahead = runs[mine - 1]
                out["waiting_for"] = {"report_id": ahead_id, "deck_name": ahead_name}
                if ahead.get("job_id"):
                    out["waiting_for"]["job_id"] = ahead["job_id"]
            if per_game:
                out["expected_start_seconds"] = round(left * per_game)
        if per_game:
            out["expected_seconds"] = round((left + _games_left(out)) * per_game)
            out["average_game_seconds_measured"] = per_game
        return out

    def resume_forge_runs(self) -> None:
        """At startup: carry on refreshing runs a restart left unfinished."""
        self._ensure_forge_refresher()

    def _save_forge(
        self, rid: str, section: dict[str, Any], *, unless_changed_from: str | None = None
    ) -> bool:
        """Store the report's Forge section; False when the report no longer exists, or, with
        ``unless_changed_from`` (the stored text the caller read), when something else saved the
        section since (an undo or more games while the refresher waited on Forge): then the
        newer section stays. A section that reaches a final state (done, failed, skipped...)
        finishes its report, unless its Forge solo test is still to come or playing."""
        same = "" if unless_changed_from is None else " AND forge_json = ?"
        args = () if unless_changed_from is None else (unless_changed_from,)
        with self.db.tx() as c:
            was = c.execute(
                "SELECT forge_state, forge_solo_state FROM reports WHERE id = ?", (rid,)
            ).fetchone()
            cur = c.execute(
                f"UPDATE reports SET forge_json = ?, forge_state = ? WHERE id = ?{same}",
                (json.dumps(section), section.get("state"), rid, *args),
            )
        if cur.rowcount > 0 and was is not None:
            if not _report_done(was[0], was[1]) and _report_done(section.get("state"), was[1]):
                self._finished(rid)
        return cur.rowcount > 0

    def _finished(self, rid: str) -> None:
        if self.on_finished is None:
            return
        with self.db._lock:
            row = self.db._conn.execute("SELECT owner_sub FROM reports WHERE id = ?", (rid,)).fetchone()
        if row is not None:
            try:
                self.on_finished(row[0], rid)
            except Exception:
                logger.exception("report notification failed")

    def _cancel_forge(self, section: Any, *, keep_asking: bool = False) -> None:
        """Best effort: stop the Forge job behind an unfinished section, so a deleted report does
        not keep a game running (or queued) that nobody will read. ``keep_asking``, for a run given
        up because its copy went silent: asked again each round while the copy does not answer, for
        up to SILENT_CANCEL_SECONDS."""
        if (
            self.forge is None
            or not isinstance(section, dict)
            or section.get("state") in FINAL_STATES
            or not section.get("job_id")
        ):
            return
        forge, job_id = self._engine(section), str(section["job_id"])

        async def cancel() -> None:
            until = time.monotonic() + (forge_module.SILENT_CANCEL_SECONDS if keep_asking else 0.0)
            while True:
                try:
                    await forge.cancel(job_id)
                    return
                except ForgeError as exc:
                    if exc.kind != "unavailable" or time.monotonic() >= until:
                        logger.info("cancelling Forge job %s: %s", job_id, exc)
                        return
                except Exception as exc:  # the job may have finished or been forgotten meanwhile
                    logger.info("cancelling Forge job %s: %s", job_id, exc)
                    return
                await asyncio.sleep(self.forge_interval)

        try:
            task = asyncio.get_running_loop().create_task(cancel())
        except RuntimeError:  # no running loop (a synchronous caller); the job ends on its own
            return
        self._forge_cancels.add(task)
        task.add_done_callback(self._forge_cancels.discard)

    def _engine(self, section: dict[str, Any]) -> ForgeClient:
        """The copy of the Forge service a run plays on: the one stored with it, else the one URL."""
        assert self.forge is not None
        pinned = self.forge.at(section.get("engine")) if section.get("engine") else None
        return pinned or self.forge

    def _pinned_gone(self, section: dict[str, Any]) -> Callable[[], Awaitable[bool]] | None:
        """For a run kept on one of several copies: asks whether that copy's address is gone from
        the service's name (False when the name gives no answer now)."""
        assert self.forge is not None
        forge, url = self.forge, section.get("engine")
        if not url or forge.at(url) is None:
            return None

        async def gone() -> bool:
            return await forge.has_copy(url) is False

        return gone

    def _ensure_forge_refresher(self) -> None:
        """Keep one background task refreshing unfinished Forge runs until none is left. Also
        started when a report with an unfinished run is read, so a gateway restart resumes it."""
        if self.forge is None or (self._forge_task and not self._forge_task.done()):
            return
        try:
            self._forge_task = asyncio.get_running_loop().create_task(self._refresh_forge_runs())
        except RuntimeError:  # no running loop (a synchronous caller); the next async one starts it
            pass

    async def _refresh_forge_runs(self) -> None:
        assert self.forge is not None
        finals = tuple(FINAL_STATES)
        while True:
            unfinished = f"NOT IN ({','.join('?' * len(finals))})"
            with self.db._lock:
                rows = [
                    (r["id"], r["forge_json"], self._save_forge)
                    for r in self.db._conn.execute(
                        "SELECT id, forge_json FROM reports WHERE forge_state IS NOT NULL AND "
                        f"forge_state {unfinished}",
                        finals,
                    ).fetchall()
                ] + [
                    (r["id"], r["forge_solo_json"], self._save_solo)
                    for r in self.db._conn.execute(
                        "SELECT id, forge_solo_json FROM reports WHERE forge_solo_state IS NOT NULL AND "
                        f"forge_solo_state {unfinished}",
                        finals,
                    ).fetchall()
                ]
            if not rows:
                return
            for rid, stored, save in rows:
                try:
                    section = json.loads(stored)
                    if _waiting(section):
                        continue  # no engine job yet; started below when its turn comes
                    solo = save == self._save_solo
                    if solo and not section.get("job_id"):
                        # marked as coming when its report was stored; started by _run
                        if time.time() - section.get("started_at", 0) > SOLO_START_SECONDS:
                            failed = {**section, "state": "failed", "error": SOLO_NOT_STARTED}
                            save(rid, failed, unless_changed_from=stored)
                        continue
                    fresh = await refresh(
                        self._engine(section),
                        section,
                        pinned_gone=self._pinned_gone(section),
                        give_up=SOLO_GIVE_UP_SECONDS if solo else forge_module.GIVE_UP_SECONDS,
                    )
                    # written only when a game finished or the state moved, and only over the
                    # section read above: a newer one saved meanwhile is read again next round
                    if fresh == section or not save(rid, fresh, unless_changed_from=stored):
                        continue
                    if fresh.get("state") == "lost" and fresh.get("error") == forge_module.FORGE_SILENT:
                        # given up while the job may still be on the copy: stop it there
                        self._cancel_forge(section, keep_asking=True)
                except Exception:  # one bad row must not stop the others
                    logger.exception("forge refresh failed for %s", rid)
            try:
                await self._start_waiting_runs()
            except Exception:
                logger.exception("starting a waiting Forge run failed")
            await asyncio.sleep(self.forge_interval)

    async def ab(
        self,
        sub: str,
        text_a: str,
        text_b: str,
        *,
        games: int = DEFAULT_GAMES,
        options: dict[str, Any] | None = None,
        modes: tuple[str, str] = ("commander", "commander"),
    ) -> dict[str, Any] | None:
        """A paired goldfish A/B of two decklists (game for game under the same seeds, with the
        deltas' confidence intervals and significance), as the research service reports it.
        Not stored: it is a comparison, not a report of one deck. None without the service.
        ``modes`` is each list's ``sim_mode``: the A/B is refused when one is not simulated, or
        when a Commander deck is compared with a 60-card one."""
        if self.mf is None:
            return None
        if not isinstance(games, int) or isinstance(games, bool) or games < 10 or games > MAX_GAMES:
            raise DeckError("invalid", f"games must be an integer from 10 to {MAX_GAMES}")
        opts = sim_options(options, ab=True)
        refused = next((m for m in modes if m not in SIM_MODES), None)
        if refused is None and modes[0] != modes[1]:
            refused = MIXED_MODES_TEXT
        if refused is not None:
            return {"tool": "goldfish_ab", "ok": False, "text": refused}
        # both decks play the kept models of their cards, as their own solo tests do
        names = [c.name for t in (text_a, text_b) for c in _cards_or_none(t) if c.zone == "main"]
        opts, used = card_models.merge(sub, self.models, names, opts)
        args = {"deck_a": text_a, "deck_b": text_b, "n": games, **opts}
        if modes[0] == "constructed":
            args["constructed"] = True
        out = await self._mf(sub, "goldfish_ab", args)
        if used:
            out["kept_models"] = used
            out["kept_models_note"] = card_models.MEMBER_WRITTEN_NOTE
        return out

    async def _solo_test(
        self,
        sub: str,
        text: str,
        games: int,
        options: dict[str, Any],
        mode: str,
        names: list[str],
        *,
        keep: bool,
    ) -> dict[str, Any]:
        """The solo test (Mystic Forge's goldfish_run) with the kept models of the deck's cards.
        The report records which kept models played (``kept_models``); once the run went
        through, the valid models the assistant passed are kept (``models_kept``), only when
        ``keep`` (a stored deck, whose names are Archidekt's own) and only for cards the simulator
        plays without their effect (goldfish_annotate on the same deck text)."""
        opts, used = card_models.merge(sub, self.models, names, _goldfish(options))
        goldfish = await self._mf(sub, "goldfish_run", _run_args(text, games, opts, mode))
        if used:
            goldfish["kept_models"] = used
            goldfish["kept_models_note"] = card_models.MEMBER_WRITTEN_NOTE
        if self.models.available and keep:
            goldfish["models_saved"] = True  # the report may say descriptions are saved
        own = [a for a in options.get("annotations") or [] if isinstance(a, dict)]
        if own and keep and goldfish.get("ok") and self.models.available:
            goldfish["models_kept"] = self.models.keep(
                sub, own, names, unmodeled=await self._unmodeled(sub, text, mode)
            )
        return goldfish

    async def _unmodeled(self, sub: str, text: str, mode: str) -> list[str] | None:
        """The deck's cards the simulator plays without their effect, from goldfish_annotate on
        the run's own deck text; None when that could not be read (then nothing is kept). The
        run's "unrecognized" list cannot say: an annotated card counts as modelled there."""
        args = {"deck": text, **({"constructed": True} if mode == "constructed" else {})}
        try:
            out = await self._mf(sub, "goldfish_annotate", args, whole=True)
        except DeckError:
            return None  # past the member's research cap: the run itself still stands
        return card_models.needs_annotation(str(out.get("text") or "")) if out.get("ok") else None

    async def _mf(
        self, sub: str, tool: str, arguments: dict[str, Any], *, whole: bool = False
    ) -> dict[str, Any]:
        """One Mystic Forge call, recorded as it came back: structured content when the tool
        gives it, else its text. Failures are recorded, never raised, so a report still holds
        the deck statistics when the research service is down. The call counts against the
        member's own Mystic Forge cap like an interactive one; past it the report is refused
        (rate_limited) and nothing is stored."""
        assert self.mf is not None
        # Mystic Forge's tools take one ``params`` object (pydantic models, published as
        # {"params": {...}} in its tool list); flat arguments are refused before the tool runs.
        params = {k: v for k, v in arguments.items() if v is not None}
        result = await self.mf.call(tool, {"params": params}, owner=sub, internal=True)
        if is_busy(result):
            raise DeckError(
                "rate_limited",
                "Research calls for your account are still running; wait for them to finish and run "
                "the report again.",
                retry_after=busy_retry_after(result),
            )
        out: dict[str, Any] = {"tool": tool, "ok": not result.is_error}
        texts = [c.text for c in result.content if getattr(c, "type", "") == "text"]
        if result.structured_content and result.structured_content != {"result": "\n".join(texts)}:
            # (a tool that returns text sends it again as {"result": text}: kept once, as text)
            out["data"] = result.structured_content
        if texts:
            text = "\n".join(texts)
            out["text"] = text if whole else text[:60_000]  # whole: read here, never stored
            if "data" not in out:
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        out["data"] = parsed
                except ValueError:
                    pass
            # The simulators answer a refusal ("Commander '...' was not recognized", "No cards
            # found") as plain text, not as a tool error; a run without its results block failed.
            marker = SIM_RESULT_MARKERS.get(tool)
            if marker and marker not in text:
                out["ok"] = False
        elif tool in SIM_RESULT_MARKERS:
            out["ok"] = False
        return out

    # -- reading --------------------------------------------------------------
    def _latest(self, sub: str, deck_id: str) -> dict[str, Any] | None:
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT id, fingerprint, taken_at, goldfish_json, validation_json, forge_json, "
                f"forge_solo_state, json_extract(stats_json, '$.{RUN_KEY}.mode') AS run_mode FROM reports "
                "WHERE owner_sub = ? AND deck_id = ? "
                "ORDER BY taken_at DESC, rowid DESC LIMIT 1",
                (sub, deck_id),
            ).fetchone()
        return dict(row) if row else None

    def list(
        self,
        sub: str,
        deck_id: str | None = None,
        limit: int = 20,
        *,
        search: str | None = None,
        since: int | None = None,
        until: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Newest first, with the trend numbers but not the full report. ``search`` matches the
        deck name, case-insensitively; ``since`` (inclusive) and ``until`` (exclusive) are epoch
        seconds."""
        limit = max(1, min(int(limit), 2100))  # the History page reads up to its last page plus one
        sql = "SELECT * FROM reports WHERE owner_sub = ?"
        args: list[Any] = [sub]
        if deck_id:
            sql += " AND deck_id = ?"
            args.append(str(deck_id))
        if search:
            sql += " AND deck_name LIKE ? ESCAPE '\\'"
            args.append(_like(search))
        if since is not None:
            sql += " AND taken_at >= ?"
            args.append(int(since))
        if until is not None:
            sql += " AND taken_at < ?"
            args.append(int(until))
        sql += " ORDER BY taken_at DESC, rowid DESC LIMIT ? OFFSET ?"
        args += [limit, max(0, int(offset))]
        with self.db._lock:
            rows = self.db._conn.execute(sql, args).fetchall()
        return [self._summary(dict(r)) for r in rows]

    def get(self, sub: str, report_id: str) -> dict[str, Any]:
        row = self.db._one("SELECT * FROM reports WHERE id = ? AND owner_sub = ?", (str(report_id), sub))
        if row is None:
            raise DeckError("not_found", "No such report for your account.")
        d = dict(row)
        out = self._summary(d)
        out["stats"] = {k: v for k, v in json.loads(d["stats_json"]).items() if k != RUN_KEY}
        out["goldfish"] = _compact(json.loads(d["goldfish_json"])) if d.get("goldfish_json") else None
        out["validation"] = _compact(json.loads(d["validation_json"])) if d.get("validation_json") else None
        forge = json.loads(d["forge_json"]) if d.get("forge_json") else None
        out["forge"] = self._forge_view(sub, d["id"], forge) if isinstance(forge, dict) else None
        out["forge_status"] = forge_status(out["forge"])
        solo = json.loads(d["forge_solo_json"]) if d.get("forge_solo_json") else None
        out["forge_solo"] = (
            {k: v for k, v in solo.items() if k not in NOT_SHOWN} if isinstance(solo, dict) else None
        )
        if any(x and x.get("state") not in FINAL_STATES for x in (out["forge"], out["forge_solo"])):
            self._ensure_forge_refresher()
        return out

    def newest_forge_solo(self, sub: str, deck_id: str) -> dict[str, Any] | None:
        """The deck's newest finished Forge solo test, for the Goldfish page: its report, when it
        finished, and its headline numbers; None when the deck has none."""
        with self.db._lock:
            row = self.db._conn.execute(
                "SELECT id, forge_solo_json FROM reports WHERE owner_sub = ? AND deck_id = ? AND "
                "forge_solo_state = 'done' ORDER BY taken_at DESC, rowid DESC LIMIT 1",
                (sub, str(deck_id)),
            ).fetchone()
        if row is None:
            return None
        try:
            section = json.loads(row["forge_solo_json"])
        except (TypeError, ValueError):
            return None
        result = section.get("result") if isinstance(section, dict) else None
        if not isinstance(result, dict):
            return None
        return {
            "report_id": str(row["id"]),
            "finished_at": section.get("finished_at"),
            "won_rate": result.get("won_rate"),
            "games": result.get("games"),
            "median_won_turn": result.get("median_won_turn"),
        }

    def forge_queue(self, sub: str) -> list[dict[str, Any]]:
        """The member's unfinished Forge runs for the Reports page: the one playing first, then
        the waiting ones in turn, each as a reader sees it (place, expected times)."""
        return [
            {"report_id": rid, "deck_name": name, "forge": self._forge_view(sub, rid, section)}
            for rid, name, section in self._member_runs(sub)
        ]

    def forge_only(self, sub: str, report_id: str) -> dict[str, Any]:
        """A report's Forge progress alone (get_deck_report view "forge"), for polling."""
        full = self.get(sub, report_id)
        keys = (
            "report_id",
            "deck_id",
            "deck_name",
            "taken_at",
            "status",
            "engine_label",
            "forge_status",
            "forge_solo",
        )
        return {k: full.get(k) for k in keys}

    def decks_seen(self, sub: str) -> dict[str, str]:
        """The decks the member has reports for: id -> last known name."""
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT deck_id, deck_name FROM reports WHERE owner_sub = ? ORDER BY taken_at", (sub,)
            ).fetchall()
        return {str(r["deck_id"]): str(r["deck_name"] or "") for r in rows}

    def index(self, sub: str) -> list[dict[str, Any]]:
        """Every stored report of the member (at most MAX_REPORTS_PER_USER), newest first, with only
        what the Reports page filters and counts by: deck, commanders, who asked, when, the overall
        status and whether Forge games came. One query; the big result texts stay unread."""
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT id, deck_id, deck_name, taken_at, created_by_client, forge_json, forge_solo_state, "
                "json_extract(forge_solo_json, '$.result.games') AS solo_games, "
                "goldfish_json IS NOT NULL AS g_has, json_extract(goldfish_json, '$.ok') AS g_ok, "
                "validation_json IS NOT NULL AS v_has, json_extract(validation_json, '$.ok') AS v_ok, "
                "json_extract(stats_json, '$.commanders') AS commanders FROM reports WHERE owner_sub = ? "
                "ORDER BY taken_at DESC, rowid DESC",
                (sub,),
            ).fetchall()
        out = []
        for r in rows:
            forge = json.loads(r["forge_json"]) if r["forge_json"] else None
            forge = forge if isinstance(forge, dict) else None
            goldfish = {"ok": bool(r["g_ok"])} if r["g_has"] else None
            validation = {"ok": bool(r["v_ok"])} if r["v_has"] else None
            try:
                commanders = [str(c) for c in json.loads(r["commanders"] or "[]") if c]
            except (TypeError, ValueError):
                commanders = []
            out.append(
                {
                    "report_id": r["id"],
                    "deck_id": str(r["deck_id"]),
                    "deck_name": r["deck_name"] or "",
                    "taken_at": r["taken_at"],
                    "created_by_client": r["created_by_client"],
                    "commanders": commanders,
                    "status": report_status(
                        goldfish,
                        validation,
                        forge,
                        {"state": r["forge_solo_state"], "result": {"games": r["solo_games"]}}
                        if r["forge_solo_state"]
                        else None,
                    ),
                    "forge_played": bool(forge and _forge_played(forge)),
                    "forge_state": (forge or {}).get("state"),
                }
            )
        return out

    def headlines(self, sub: str, report_ids: list[str]) -> dict[str, dict[str, Any]]:
        """headline_numbers of some of the member's reports, by report id (the Reports page's
        Decks tested widget)."""
        ids = [str(i) for i in report_ids][:MAX_REPORTS_PER_USER]
        if not ids:
            return {}
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT id, taken_at, stats_json, goldfish_json, forge_json FROM reports "
                f"WHERE owner_sub = ? AND id IN ({','.join('?' * len(ids))})",
                (sub, *ids),
            ).fetchall()
        return {r["id"]: headline_numbers(dict(r)) for r in rows}

    def summaries(self, sub: str, report_ids: list[str]) -> list[dict[str, Any]]:
        """The Reports page's rows, in the order of ``report_ids``: list's summary plus the headline
        numbers a row shows (Forge's win rate for the member's deck with its 95% interval, the solo
        test's median turn for lethal damage) and the deck's commander."""
        ids = [str(i) for i in report_ids][:MAX_REPORTS_PER_USER]
        if not ids:
            return []
        with self.db._lock:
            rows = self.db._conn.execute(
                f"SELECT * FROM reports WHERE owner_sub = ? AND id IN ({','.join('?' * len(ids))})",
                (sub, *ids),
            ).fetchall()
        by_id = {r["id"]: dict(r) for r in rows}
        out = []
        for rid in ids:
            d = by_id.get(rid)
            if d is None:
                continue
            row = self._summary(d)
            row.update(headline_numbers(d))
            out.append(row)
        return out

    def series(self, sub: str, deck_id: str, limit: int = TREND_LIMIT) -> list[dict[str, Any]]:
        """A deck's newest ``limit`` reports oldest first: report_id, taken_at, the trend numbers
        (TREND_KEYS) and the simulation's speed numbers (report_view.speed_numbers), for charts."""
        limit = max(1, min(int(limit), MAX_REPORTS_PER_USER))
        with self.db._lock:
            rows = self.db._conn.execute(
                "SELECT id, taken_at, stats_json, goldfish_json, forge_json FROM reports "
                "WHERE owner_sub = ? AND deck_id = ? ORDER BY taken_at DESC, rowid DESC LIMIT ?",
                (sub, str(deck_id), limit),
            ).fetchall()
        out = []
        for r in reversed(rows):
            stats = json.loads(r["stats_json"]) if r["stats_json"] else {}
            goldfish = json.loads(r["goldfish_json"]) if r["goldfish_json"] else None
            forge = json.loads(r["forge_json"]) if r["forge_json"] else None
            out.append(
                {
                    "report_id": r["id"],
                    "taken_at": r["taken_at"],
                    **{k: stats.get(k) for k in TREND_KEYS},
                    **speed_numbers(goldfish),
                    "settings": run_settings(stats, goldfish, forge),
                }
            )
        return out

    def trend(self, sub: str, deck_id: str, limit: int = TREND_LIMIT) -> dict[str, Any] | None:
        """How a deck's stored reports moved over time: the series (oldest first) and, per
        TREND_METRICS number that at least two reports hold, its first and latest values with
        their dates and the change between them. None with fewer than two reports."""
        series = self.series(sub, deck_id, limit)
        if len(series) < 2:
            return None
        metrics = []
        for key, label, unit in TREND_METRICS:
            held = [p for p in series if _number(p.get(key))]
            left_out = 0
            if key in GOLDFISH_TREND_KEYS and held:
                # A simulation number is only compared with ones run the same way: the series
                # breaks where the game (Commander at 40 life, or 60-card at 20) or the last
                # simulated turn changed, and only the latest stretch is followed.
                same = _comparable(held[-1])
                start = len(held) - 1
                while start > 0 and _comparable(held[start - 1]) == same:
                    start -= 1
                left_out, held = start, held[start:]
            if len(held) < 2:
                continue
            first, latest = held[0], held[-1]
            metric = {
                "key": key,
                "label": label,
                "unit": unit,
                "reports": len(held),
                "first": first[key],
                "first_at": first["taken_at"],
                "latest": latest[key],
                "latest_at": latest["taken_at"],
                "change": round(latest[key] - first[key], 2),
            }
            if left_out:
                metric["left_out"] = left_out
                metric["since_report_id"] = first["report_id"]
            metrics.append(metric)
        return {
            "deck_id": str(deck_id),
            "reports": len(series),
            "first_at": series[0]["taken_at"],
            "latest_at": series[-1]["taken_at"],
            "metrics": metrics,
            "settings_changes": settings_changes(series),
            "series": series,
        }

    def delete(self, sub: str, report_id: str, *, client_id: str | None = None) -> None:
        """Delete one of the member's reports. With ``client_id`` (an app's bearer token), only a
        report that app ran itself; the member's browser passes none and may delete any."""
        sql = "DELETE FROM reports WHERE id = ? AND owner_sub = ?"
        args: tuple[str, ...] = (str(report_id), sub)
        if client_id is not None:
            sql += " AND created_by_client = ?"
            args += (client_id,)
        with self.db.tx() as c:
            row = c.execute(sql.replace("DELETE", "SELECT forge_json, forge_solo_json", 1), args).fetchone()
            cur = c.execute(sql, args)
        if cur.rowcount == 0:
            raise DeckError("not_found", "No such report for your account.")
        # An unfinished Forge run goes with its report: otherwise it keeps the engine busy, and the
        # one-unfinished-run-per-member check (which reads only stored reports) would let a member
        # queue another run by deleting this one.
        for column in ("forge_json", "forge_solo_json"):
            if row is not None and row[column]:
                self._cancel_forge(json.loads(row[column]))

    @staticmethod
    def _summary(d: dict[str, Any]) -> dict[str, Any]:
        stats = json.loads(d["stats_json"]) if d.get("stats_json") else {}
        metrics = {k: stats.get(k) for k in TREND_KEYS}
        goldfish = json.loads(d["goldfish_json"]) if d.get("goldfish_json") else None
        validation = json.loads(d["validation_json"]) if d.get("validation_json") else None
        forge = json.loads(d["forge_json"]) if d.get("forge_json") else None
        forge = forge if isinstance(forge, dict) else None
        solo = json.loads(d["forge_solo_json"]) if d.get("forge_solo_json") else None
        solo = solo if isinstance(solo, dict) else None
        # No rules-engine games came or will come: not run, skipped, or the Forge run ended without games.
        preflight = forge is None or not _forge_played(forge)
        return {
            "report_id": d["id"],
            "deck_id": d["deck_id"],
            "deck_name": d["deck_name"],
            "deck_url": f"https://archidekt.com/decks/{d['deck_id']}",
            "taken_at": d["taken_at"],
            "fingerprint": d["fingerprint"],
            "metrics": metrics,
            "has_goldfish": bool(goldfish and goldfish.get("ok")),
            "has_validation": bool(validation and validation.get("ok")),
            "bracket_estimate": (stats.get("bracket_estimate") or {}).get("bracket"),
            "bracket_set": stats.get("archidekt_bracket"),
            "created_by_client": d.get("created_by_client"),
            "status": report_status(goldfish, validation, forge, solo),
            "engine": "preflight_only" if preflight else "forge",
            "engine_label": PREFLIGHT_ONLY if preflight else "Forge rules engine",
            "settings": run_settings(stats, goldfish, forge),
        }


PAIR_SETTING_UP = (
    "Another comparison of yours is still being set up, so nothing was started. Try again once it "
    "is set up and none of your reports is waiting for its games."
)
MORE_NO_FORGE = "Forge is not set up on this gateway, so no more games can be played."
MORE_NOT_ENDED = "More games can be added once this report's Forge games have finished."
MORE_NOT_THIS_ONE = (
    "More games can't be added to this report: it has no Forge games to add to, or it was made "
    "before this gateway kept what more games need. Run the report again."
)
MORE_FULL = (
    f"This report already has {MAX_REPORT_FORGE_GAMES} Forge games, the most one report can hold. "
    "Run the report again for a fresh set."
)
MORE_PAIR_WAIT = (
    "More games go to both decks of this comparison, so the two stay even. They can be added once "
    "the other deck's Forge games have finished too."
)
MORE_PAIR_CANT = (
    "More games go to both decks of this comparison, so the two stay even, but the other deck's "
    "report can't take more. Run the comparison again."
)
MORE_PAIR_QUEUE = (
    "You already have at least one report waiting for Forge, and each member can have at most "
    f"{MAX_FORGE_WAITING} waiting. More games for both decks of this comparison need room for 2 more, so "
    "nothing was started. Try again once none of your reports is waiting."
)
MORE_QUEUE_FULL = (
    f"You already have {MAX_FORGE_WAITING} reports waiting for Forge, and each member can have at most "
    f"{MAX_FORGE_WAITING} waiting, so nothing was started. Try again once fewer than {MAX_FORGE_WAITING} "
    "of your reports are waiting."
)
# Why Play more games started nothing, by a short code: the report page's notice (?more=) and the
# ``reason`` of a tool's or the API's refusal.
MORE_REFUSALS = {
    "queue": MORE_QUEUE_FULL,
    "full": MORE_FULL,
    "ended": MORE_NOT_ENDED,
    "none": MORE_NOT_THIS_ONE,
    "noforge": MORE_NO_FORGE,
    "pairwait": MORE_PAIR_WAIT,
    "paircant": MORE_PAIR_CANT,
    "pairqueue": MORE_PAIR_QUEUE,
    "setup": PAIR_SETTING_UP,
}


def more_reason(text: str) -> str:
    """The MORE_REFUSALS code of a Play more games refusal ("" for any other message)."""
    return next((code for code, t in MORE_REFUSALS.items() if t == text), "")


PAIR_SETTING_UP_SKIP = (
    "Another comparison of yours is still being set up and needs the rest of your Forge queue, so "
    "this report has no Forge games. Run the report again once that comparison is set up and fewer "
    f"than {MAX_FORGE_WAITING} of your reports are waiting."
)
NO_FORGE_TO_SHARE = (
    "That report has no Forge games, so there are no opponents to share. Run it again with Forge, then "
    "compare."
)
PAIR_UNREAD = "The gateway could not read the other deck, so no report was made."
PAIR_NO_FORGE = (
    "compare_with needs Forge games, and this report has none (forge is false, Forge isn't set up on "
    "this gateway, or the deck has no commander)."
)
BOTH_PAIRINGS = (
    "compare_with and same_opponents_as each pick the opponents: use compare_with on the first deck's "
    "report and same_opponents_as on the second's, not both on one."
)


def _number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# The trend numbers that come from the goldfish simulation, so depend on how it was run.
GOLDFISH_TREND_KEYS = ("commander_cast_median_turn", "kill_median_turn", "kill_pct")
# The settings settings_changes compares between consecutive reports.
SETTING_KEYS = ("mode", "games", "seed", "until_turn", "engines")
# Forge states that can end without rules-engine games.
_FORGE_NONE = ("skipped", "failed", "timeout", "cancelled", "lost")


def _forge_played(forge: dict[str, Any]) -> bool:
    """Forge games came or will come: a run that stopped early counts by the games it played."""
    return forge.get("state") not in _FORGE_NONE or _done_games(forge) > 0


def run_settings(stats: dict[str, Any], goldfish: Any, forge: Any) -> dict[str, Any]:
    """How a report was run, for its trend point and its reader: the game (``mode``: commander or
    constructed, None when not simulated), the goldfish games, seed and last turn (read from the
    stored result, else from what was asked), the engines that played (goldfish, forge) and
    Forge's games. Reports stored before 0.7.24 kept no settings: theirs come from the result."""
    asked = stats.get(RUN_KEY) if isinstance(stats.get(RUN_KEY), dict) else {}
    ran = goldfish_settings(goldfish)
    mode = asked.get("mode") if RUN_KEY in stats else sim_mode(stats)
    if ran.get("constructed"):
        mode = "constructed"
    engines = []
    if isinstance(goldfish, dict) and goldfish.get("ok"):
        engines.append("goldfish")
    if isinstance(forge, dict) and _forge_played(forge):
        engines.append("forge")
    out = {
        "mode": mode if mode in SIM_MODES else None,
        "games": ran.get("games") if ran.get("games") is not None else asked.get("games"),
        "seed": ran.get("seed") if ran.get("seed") is not None else asked.get("seed"),
        "until_turn": ran.get("until_turn") if ran.get("until_turn") is not None else asked.get("until_turn"),
        "engines": engines,
        "forge_games": forge.get("games_requested") if "forge" in engines else None,
    }
    if "goldfish" not in engines:
        out.update(games=None, seed=None, until_turn=None)
    if asked.get("snapshot_id"):
        out["snapshot_id"] = asked["snapshot_id"]
    return out


def _comparable(point: dict[str, Any]) -> tuple[Any, Any]:
    settings = point.get("settings") or {}
    return settings.get("mode"), settings.get("until_turn")


def settings_changes(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Where a deck's run settings changed from one report to the next (each setting compared
    only when both reports hold it): the report, and per changed setting [before, after]."""
    out = []
    last: dict[str, Any] = {}
    for p in series:
        settings = p.get("settings") or {}
        changed = {}
        for key in SETTING_KEYS:
            value = settings.get(key)
            if value is None or value == []:
                continue
            if key in last and last[key] != value:
                changed[key] = [last[key], value]
            last[key] = value
        if changed:
            out.append({"report_id": p["report_id"], "taken_at": p["taken_at"], "changed": changed})
    return out


def report_status(goldfish: Any, validation: Any, forge: Any, solo: Any = None) -> str:
    """One word for where a report stands: "queued" or "running" while its Forge games or its
    Forge solo test wait or play, else "complete" when every part asked for came back, "partial"
    when some did not (a failed simulation or validation, Forge games that failed or found no
    place), "failed" when none did. Forge not run on purpose (not asked for, or a 60-card deck)
    is not a failure, nor is a Forge solo test skipped (Forge busy or too old)."""
    going = [
        x.get("state") for x in (forge, solo) if isinstance(x, dict) and x.get("state") not in FINAL_STATES
    ]
    if going:
        return "running" if "running" in going else "queued"
    parts = [bool(b.get("ok")) for b in (goldfish, validation) if isinstance(b, dict)]
    some = list(parts)
    if isinstance(forge, dict) and forge.get("reason") not in ("not_requested", "not_commander"):
        parts.append(forge.get("state") == "done")
        # a run that stopped early still brought back the games it played
        some.append(forge.get("state") == "done" or _done_games(forge) > 0)
    if isinstance(solo, dict) and solo.get("state") != "skipped":
        parts.append(solo.get("state") == "done")
        some.append(solo.get("state") == "done" or _done_games(solo) > 0)
    if parts and not any(some):
        return "failed"
    return "complete" if all(parts) else "partial"


# The headline numbers of a stored report (headline_numbers), by report id, its time and the size
# of its stored results: reading them parses the solo test's text, and a stored report's results
# change only when its Forge games do (then the forge part's size changes too).
_HEADLINES: dict[tuple[Any, ...], dict[str, Any]] = {}
_HEADLINES_MAX = 1024


def headline_numbers(d: dict[str, Any]) -> dict[str, Any]:
    """A stored report's headline numbers (``d`` is its database row) for a list row or a widget:
    ``win`` (the member's deck's Forge win rate, its 95% interval, the games and the opponents,
    once a game has finished), the Forge run's ``forge_state`` and games played and asked for,
    the solo test's ``kill_median_turn`` and the damage that counts as lethal (``kill_damage``:
    40, or 20 in a 60-card game), whether the deck is ``constructed``, and its ``commander``
    (front face). A missing number is None, never guessed."""
    key = (d.get("id"), d.get("taken_at"), len(d.get("goldfish_json") or ""), len(d.get("forge_json") or ""))
    hit = _HEADLINES.get(key)
    if hit is not None:
        return dict(hit)
    stats = json.loads(d["stats_json"]) if d.get("stats_json") else {}
    goldfish = json.loads(d["goldfish_json"]) if d.get("goldfish_json") else None
    forge = json.loads(d["forge_json"]) if d.get("forge_json") else None
    forge = forge if isinstance(forge, dict) else {}
    res = forge.get("result") if isinstance(forge.get("result"), dict) else {}
    seats = [s for s in res.get("seats") or [] if isinstance(s, dict)]
    win = None
    if seats and isinstance(seats[0].get("win_rate"), (int, float)) and res.get("games"):
        low, high = (seats[0].get("win_rate_95") or [None, None])[:2]
        win = {
            "rate": float(seats[0]["win_rate"]),
            "lo": low if isinstance(low, (int, float)) else None,
            "hi": high if isinstance(high, (int, float)) else None,
            "games": res.get("games"),
            "opponents": len(seats) - 1,
        }
    g = parse_goldfish(goldfish) if isinstance(goldfish, dict) else None
    kill = None
    if g and g["ok"]:
        m = g.get("metrics") or {}
        k = m.get("kill") if isinstance(m.get("kill"), dict) else {}
        kill = k.get("median_among_reached")
        kill = float(kill) if isinstance(kill, (int, float)) and not isinstance(kill, bool) else None
    constructed = bool(g and g.get("format") == "constructed") or (
        RUN_KEY in stats and isinstance(stats[RUN_KEY], dict) and stats[RUN_KEY].get("mode") == "constructed"
    )
    commanders = stats.get("commanders") if isinstance(stats.get("commanders"), list) else []
    out = {
        "win": win,
        "forge_state": forge.get("state"),
        "forge_done": res.get("games") or 0,
        "forge_asked": forge.get("games_requested") or res.get("games_requested"),
        "kill_median_turn": kill,
        "kill_damage": 20 if constructed else 40,
        "constructed": constructed,
        "commander": _front_face(str(commanders[0])) if commanders and commanders[0] else None,
    }
    if forge.get("state") in FINAL_STATES or not forge:  # a run still playing changes its numbers
        if len(_HEADLINES) >= _HEADLINES_MAX:
            _HEADLINES.clear()
        _HEADLINES[key] = out
    return dict(out)


def _forge_not_asked(forge_json: Any) -> bool:
    """Whether a stored report has no Forge games because none were asked for (or none could be
    the gateway ran no Forge then): a later report that asks for them does not reuse it."""
    if not forge_json:
        return True
    section = json.loads(forge_json) if isinstance(forge_json, str) else forge_json
    return isinstance(section, dict) and section.get("reason") == "not_requested"


def _cards_or_none(text: str) -> list[Any]:
    try:
        return list(parse_decklist(text))
    except DecklistError:
        return []


def _shared_link(shared: dict[str, Any]) -> dict[str, str]:
    """Whose opponents a run seats, as the report shows it."""
    return {k: shared[k] for k in ("report_id", "deck_name") if isinstance(shared.get(k), str)}


def _shared_id(section: dict[str, Any]) -> str | None:
    """The report whose opponents this Forge section seats (same_opponents_as), or None: kept on
    the section while it waits, and in its opponent choice once it started."""
    for link in (section.get("shared_with"), (section.get("opponent_choice") or {}).get("shared_with")):
        if isinstance(link, dict) and isinstance(link.get("report_id"), str):
            return link["report_id"]
    return None


def _number_or_zero(value: Any) -> float:
    return float(value) if _number(value) else 0.0


def _run_record(section: dict[str, Any]) -> dict[str, Any]:
    """One Forge run of a report, for the record: its seed, whose seed it was, its games and the
    Forge version that played them."""
    return {
        "seed": section.get("seed"),
        "seed_source": section.get("seed_source"),
        "games": section.get("games_requested"),
        "forge_version": section.get("forge_version"),
    }


def more_room(section: Any, most: int) -> tuple[int, str | None]:
    """(games, None) when Play more games can add ``games`` Forge games to a report with this
    section (``most`` a press, the Forge games per report setting), else (0, why not)."""
    if not isinstance(section, dict) or not section.get("state"):
        return 0, MORE_NOT_THIS_ONE  # no Forge games at all
    if section.get("state") not in FINAL_STATES:
        return 0, MORE_NOT_ENDED
    played = section.get("played") if isinstance(section.get("played"), list) else []
    if not played or not section.get("deck_text") or len(section.get("seats") or []) < 2:
        return 0, MORE_NOT_THIS_ONE
    room = min(most, MAX_REPORT_FORGE_GAMES - len(played))
    if room <= 0:
        return 0, MORE_FULL
    return room, None


def solo_game_seconds(games: int, places: int, cap: int) -> int:
    """Each Forge solo-test game's time limit: an equal part of SOLO_BUDGET_SECONDS for each round
    of games (``places`` play at once), between SOLO_MIN_GAME_SECONDS and the service's ``cap``."""
    rounds = max(1, -(-max(1, games) // max(1, places)))
    return max(min(SOLO_MIN_GAME_SECONDS, cap), min(cap, SOLO_BUDGET_SECONDS // rounds))


def _report_done(forge_state: Any, solo_state: Any) -> bool:
    """Whether a report with these Forge states is finished: its games against precons ended,
    and its Forge solo test, if it has one, too."""
    return forge_state in FINAL_STATES and (solo_state is None or solo_state in FINAL_STATES)


def _waiting(section: dict[str, Any]) -> bool:
    """A Forge run waiting in the gateway's queue for the member's earlier one (no engine job)."""
    return section.get("state") == "queued" and bool(section.get("waiting")) and not section.get("job_id")


def _done_games(section: dict[str, Any]) -> int:
    games = (section.get("result") or {}).get("games")
    return games if isinstance(games, int) else 0


def _games_left(section: dict[str, Any]) -> int:
    requested = section.get("games_requested")
    return max(0, requested - _done_games(section)) if isinstance(requested, int) else 0


def _compact(block: Any) -> Any:
    """A stored research block without ``data`` when it only repeats the text ({"result": text}):
    reports stored before 0.7.22 kept both, about twice the size."""
    data = block.get("data") if isinstance(block, dict) else None
    if isinstance(data, dict) and set(data) == {"result"} and isinstance(data["result"], str):
        if data["result"][:60_000] == block.get("text"):
            return {k: v for k, v in block.items() if k != "data"}
    return block


def forge_status(section: dict[str, Any] | None) -> dict[str, Any] | None:
    """The few numbers a poller needs about a report's Forge run: state, games done of those
    asked for, and while it waits its place in the queue and the report it waits for, with the
    expected time left when the gateway has measured a game."""
    if not isinstance(section, dict):
        return None
    out: dict[str, Any] = {
        "state": section.get("state"),
        "games_done": _done_games(section),
        "games_requested": section.get("games_requested"),
    }
    for key in (
        "job_id",
        "position",
        "waiting_for",
        "engine_busy",
        "retry_after",
        "expected_seconds",
        "expected_start_seconds",
        "reason",
        "note",
        "error",
    ):
        if section.get(key) is not None:
            out[key] = section[key]
    return out


def _succeeded(row: dict[str, Any]) -> bool:
    """Whether a stored report's research blocks all came back ok. A report whose simulation or
    validation failed (the service down, a refusal) is kept as the record of that failure but
    never reused in place of a fresh run."""
    for key in ("goldfish_json", "validation_json"):
        block = row.get(key)
        if isinstance(block, str):
            block = json.loads(block)
        if block is not None and not block.get("ok"):
            return False
    return True


# The simulator's knobs a report or an A/B passes through to the research service, which
# validates their values (the proxy already bounds n and until_turn). Unknown keys are refused
# here so a typo never silently runs the default simulation.
RUN_OPTIONS = ("annotations", "combos", "seed", "forge_seed", "until_turn", "opponents", "mulligan")
# Options only the gateway reads, never sent to the goldfish engine.
GATEWAY_OPTIONS = ("forge_seed",)
AB_OPTIONS = ("annotations", "annotations_a", "annotations_b", "combos", "seed", "until_turn")
AB_FLAGS = ("allow_different_commanders",)


STORED_NOTE = "The stored copy is under History (get_deck_report)."


def _stored_note(goldfish: dict[str, Any]) -> None:
    """Point the simulation text of a report the gateway stores at its stored copy. Only a stored
    report says so: a pasted list's run (run_text) is returned and kept nowhere."""
    for holder, key in ((goldfish, "text"), (goldfish.get("data") or {}, "result")):
        value = holder.get(key)
        if isinstance(value, str) and FULL_REPORT in value and STORED_NOTE not in value:
            holder[key] = value.replace(FULL_REPORT, f"{FULL_REPORT} {STORED_NOTE}", 1)


def _keep_quantities(goldfish: dict[str, Any], text: str) -> None:
    """Keep how many copies of each card the simulated deck held (front faces, main zone) with the
    simulation's result: its honesty report names each card once, so the report page shows the
    copies beside each card and counts a 60-card deck's coverage in copies."""
    if not goldfish.get("ok"):
        return
    try:
        cards = parse_decklist(text)
    except DecklistError:
        return
    copies: dict[str, int] = {}
    for c in cards:
        if c.zone == "main":
            name = _front_face(c.name) or c.name
            copies[name] = copies.get(name, 0) + c.quantity
    if copies:
        goldfish["quantities"] = copies


def _front_face(name: str | None) -> str | None:
    return name.split(" // ", 1)[0] if isinstance(name, str) else name


# One entry of the honesty report's card lines: "Name (drawn 11%)", comma-separated. Names hold
# commas themselves ("Liesa, Forgotten Archangel"), so entries split on the "(drawn …)" tail.
_ENTRY = re.compile(r"(.+?)( \(drawn \d+%\))(?:, |$)")


def _commander_aside(goldfish: dict[str, Any], commander: str | None) -> None:
    """Take the commander out of the simulation's "unrecognized" list, in the metrics and in the
    text. The simulator casts it from the command zone (so it is never drawn) and plays it as a
    creature; its honesty report still files it as an unrecognized card "drawn 0%", which reads
    as a lookup failure. The report says instead that the commander's abilities beyond combat
    are not modelled. Nothing else in the result changes; a result without the list is left
    as it is."""
    if not commander or not goldfish.get("ok"):
        return
    data = goldfish.get("data")
    metrics = data.get("metrics") if isinstance(data, dict) else None
    honesty = metrics.get("honesty") if isinstance(metrics, dict) else None
    if not isinstance(honesty, dict):
        return
    unrecognized = honesty.get("unrecognized")
    if not isinstance(unrecognized, list):
        return
    kept = [c for c in unrecognized if not (isinstance(c, dict) and c.get("name") == commander)]
    if len(kept) == len(unrecognized):
        return
    honesty["unrecognized"] = kept
    honesty["commander_note"] = (
        f"{commander} is cast from the command zone (never drawn); its abilities beyond combat are "
        "not modeled"
    )
    text = goldfish.get("text")
    if not isinstance(text, str):
        return
    lines = text.split("\n")
    for i, line in enumerate(lines):
        if not line.startswith("Unrecognized"):
            continue
        # "Unrecognized — 32 cards (candidates for annotation; see goldfish_annotate):" then one
        # line "- Name (drawn 11%), Name (drawn 0%), ..."
        if i + 1 < len(lines) and lines[i + 1].startswith("- "):
            entries = _ENTRY.findall(lines[i + 1][2:])
            rest = [name + drawn for name, drawn in entries if name != commander]
            # Only rewrite a line the parse reproduces in full: an entry without a "(drawn N%)"
            # tail would otherwise be dropped silently.
            if ", ".join(n + d for n, d in entries) != lines[i + 1][2:] or len(rest) == len(entries):
                break
            lines[i + 1] = "- " + ", ".join(rest) if rest else "- (none)"
            lines[i] = re.sub(
                r"\b(\d+) cards?\b",
                lambda m: f"{int(m.group(1)) - 1} card{'' if int(m.group(1)) - 1 == 1 else 's'}",
                line,
                count=1,
            )
            lines.insert(i + 2, f"Commander — {honesty['commander_note']}")
        break
    goldfish["text"] = "\n".join(lines)


# What a successful simulation's text always contains (Mystic Forge's renderers).
SIM_RESULT_MARKERS = {"goldfish_run": "## Metrics", "goldfish_ab": "## Deltas"}
NO_COMMANDER_TEXT = (
    "No solo test: a Commander deck needs its commander in the deck's Commander (premier) category. "
    "Put it there and run again."
)
# A deck without a commander is played as a 60-card game: Mystic Forge's ``constructed`` games
# (docker/mystic-forge/patches/0007): no command zone, 20 life, a 7-card London mulligan with no
# free one, and on the play (no draw on turn 1). The sideboard is never in the simulated deck.
SIM_MODES = ("commander", "constructed")
MIN_CONSTRUCTED = 60
SMALL_DECK_TEXT = (
    "No solo test: a deck without a commander is played as a 60-card game and needs at least "
    "{min} cards in the main deck (this one has {count}). For a Commander deck, put the "
    "commander in the deck's Commander (premier) category."
)
# A list with no set format (or Archidekt's Custom) and about 100 cards is almost always a
# Commander deck whose commander is not marked; playing it as a 60-card game would report the
# wrong game.
COMMANDER_SIZED = range(99, 102)
UNMARKED_COMMANDER_TEXT = (
    "No solo test: this {count}-card list has no set format and no commander, so it looks like a "
    "Commander deck with its commander unmarked. Put the commander in the deck's Commander "
    '(premier) category, or under a "Commander" heading in a pasted list, and run again.'
)
MIXED_MODES_TEXT = (
    "No comparison games: one deck has a commander and the other does not. The comparison plays "
    "two Commander decks or two 60-card decks."
)


def sim_mode(stats: dict[str, Any]) -> str:
    """How the goldfish simulator plays a deck, from its statistics (``deck_stats.compute`` or
    ``compute_from_text``): "commander", "constructed" (no commander, at least 60 cards, not a
    Commander-style format and not an unformatted 99-101 card list), or else the sentence saying
    why it is not simulated."""
    if stats.get("commanders"):
        return "commander"
    if stats.get("format") in deck_stats._COMMANDER_FORMATS:
        # Mystic Forge would take the first line as the commander and simulate a 99-card deck.
        return NO_COMMANDER_TEXT
    count = stats.get("card_count") or 0
    if stats.get("format") in (None, "", "custom") and count in COMMANDER_SIZED:
        return UNMARKED_COMMANDER_TEXT.format(count=count)
    if count < MIN_CONSTRUCTED:
        return SMALL_DECK_TEXT.format(min=MIN_CONSTRUCTED, count=count)
    return "constructed"


def _sentence(text: str) -> str:
    """A reason as its own sentence: first letter upper-cased, ending with a full stop."""
    text = text.strip()
    if not text:
        return ""
    text = text[0].upper() + text[1:]
    return text if text[-1] in ".!?" else text + "."


def _pair_bracket(deck: Any, stats: dict[str, Any], other: Deck | None) -> tuple[Any, ...]:
    """The bracket this run's opponents are picked for: the deck's own (forge_bracket), or in a
    Forge comparison the higher of the two decks', each deck's set bracket where it
    has one, else Archidekt's estimate from cards; a tie keeps this deck's."""
    own = forge_bracket(deck, stats)
    if other is None:
        return own
    theirs = forge_bracket(other, deck_stats.compute(other))
    if theirs[0] is not None and (own[0] is None or theirs[0] > own[0]):
        return (*theirs, other.name or f"Deck {other.id}", True)
    return (*own, None, True)


def forge_bracket(deck: Any, stats: dict[str, Any]) -> tuple[int | None, str | None]:
    """The bracket Forge matches opponents to, and where it came from: the bracket set on the
    deck in Archidekt, else the gateway's estimate (deck_stats.bracket_estimate)."""
    own = getattr(deck, "edh_bracket", None)
    if isinstance(own, int) and not isinstance(own, bool) and 1 <= own <= 5:
        return own, "archidekt"
    est = (stats.get("bracket_estimate") or {}).get("bracket")
    if isinstance(est, int) and not isinstance(est, bool):
        return est, "estimate"
    return None, None


def _goldfish(options: dict[str, Any]) -> dict[str, Any]:
    """The options the goldfish engine takes: those for the gateway (forge_seed) left out."""
    return {k: v for k, v in options.items() if k not in GATEWAY_OPTIONS}


def _run_args(text: str, games: int, options: dict[str, Any], mode: str) -> dict[str, Any]:
    args = {"deck": text, "n": games, **options}
    if mode == "constructed":
        args["constructed"] = True
    return args


def sim_options(options: dict[str, Any] | None, *, ab: bool = False) -> dict[str, Any]:
    """The given simulator options with unset ones dropped; refuses keys the simulation does
    not take (``invalid``)."""
    if not options:
        return {}
    if not isinstance(options, dict):
        raise DeckError("invalid", "simulation options must be an object")
    allowed = (*AB_OPTIONS, *AB_FLAGS) if ab else RUN_OPTIONS
    out = {k: v for k, v in options.items() if v is not None and v != [] and v != {}}
    unknown = sorted(k for k in out if k not in allowed)
    if unknown:
        raise DeckError("invalid", f"unknown simulation option(s): {', '.join(unknown)}")
    if len(json.dumps(out)) > 60_000:
        raise DeckError("too_large", "simulation options larger than 60 kB")
    for key, low, high in (
        ("opponents", 1, 5),
        ("until_turn", 1, 30),
        ("seed", -(2**63), 2**63 - 1),
        ("forge_seed", -(2**63), 2**63 - 1),
    ):
        if key in out and (not isinstance(out[key], int) or isinstance(out[key], bool)):
            raise DeckError("invalid", f"simulation option {key} must be an integer")
        if key in out and not low <= out[key] <= high:
            raise DeckError("invalid", f"simulation option {key} must be from {low} to {high}")
    for key in ("annotations", "annotations_a", "annotations_b", "combos"):
        if key in out and not isinstance(out[key], list):
            raise DeckError("invalid", f"simulation option {key} must be a list")
    if "mulligan" in out and not isinstance(out["mulligan"], dict):
        raise DeckError("invalid", "simulation option mulligan must be an object")
    return out


def deck_summary_for(deck: Deck) -> dict[str, Any]:
    """The short deck block shared by tools, pages and the API."""
    return {
        "id": deck.id,
        "name": deck.name,
        "owner": deck.owner,
        "updated_at": deck.updated_at,
        "url": f"https://archidekt.com/decks/{deck.id}",
    }


__all__ = ["ReportService", "TREND_KEYS", "TREND_METRICS", "deck_summary_for", "sim_mode", "sim_options"]
