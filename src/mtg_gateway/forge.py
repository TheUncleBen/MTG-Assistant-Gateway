"""Deck simulations on Forge, the rules engine in the stack's private Forge service.

The Forge service (docker/forge/) plays real games: opponents, combat, the stack, the graveyard. A run
is a background job there, because a four-player Commander game takes far longer than a goldfish
hand. The gateway starts the job, keeps its state in the report, and refreshes it until it ends.

Every card Forge cannot play is named in the result, never dropped silently; the assistant
explains those from the card's exact text.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import math
import secrets
import socket
import statistics
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from .decklist import DecklistError, parse_decklist

logger = logging.getLogger(__name__)

DEFAULT_OPPONENTS = 3
FINAL_STATES = frozenset({"done", "failed", "timeout", "cancelled", "lost", "skipped"})
# A run still unfinished this long after it started is given up (the engine was unreachable).
GIVE_UP_SECONDS = 4 * 3600
# The health check's probe of the service (as mf_proxy.py does for Mystic Forge): short, and cached
# so however often /healthz is asked, the service sees one probe per half minute at most.
HEALTH_DEADLINE_SECONDS = 3.0
HEALTH_CACHE_SECONDS = 30.0
HEALTH_DOWN_CACHE_SECONDS = 5.0
# Looking up the copies of the service (several addresses behind one name) gets this long; no
# answer in time counts as one copy, so a slow resolver never holds up a run's start.
LOOKUP_DEADLINE_SECONDS = 3.0
# A run kept on one of several copies is given up once that copy has not answered this long, or at
# once when its address is no longer the service's: a restarted or moved copy has a new address,
# and its old one never answers about the run again (one copy answers "not found" instead).
PINNED_SILENCE_SECONDS = 120.0
# A run given up because its copy went silent is cancelled there, asked again while the copy stays
# silent for up to this long: a copy that answers again would otherwise play the abandoned run to
# its end and keep its places busy.
SILENT_CANCEL_SECONDS = 1800.0


class ForgeError(Exception):
    def __init__(self, kind: str, message: str, detail: dict[str, Any] | None = None):
        super().__init__(message)
        self.kind = kind
        self.detail = detail or {}


def forge_deck(text: str) -> dict[str, Any]:
    """A decklist (the gateway's decklist_text, Commander category marking the commander) in the
    Forge service's shape: {"commander": [names], "main": [[count, name], ...]}. Main zone only."""
    try:
        cards = parse_decklist(text)
    except DecklistError as exc:
        raise ForgeError("invalid", f"decklist could not be read: {exc}") from exc
    commander: list[str] = []
    main: dict[str, int] = {}
    for c in cards:
        if c.zone != "main":
            continue
        if "Commander" in c.categories:
            commander.append(c.name)
        else:
            main[c.name] = main.get(c.name, 0) + c.quantity
    return {"commander": commander, "main": [[n, name] for name, n in main.items()]}


def wilson(wins: int, games: int) -> tuple[float, float]:
    """95% Wilson interval of a win rate, as fractions."""
    if games <= 0:
        return (0.0, 0.0)
    z = 1.96
    p = wins / games
    centre = (p + z * z / (2 * games)) / (1 + z * z / games)
    half = z * math.sqrt(p * (1 - p) / games + z * z / (4 * games * games)) / (1 + z * z / games)
    return (round(max(0.0, centre - half), 4), round(min(1.0, centre + half), 4))


# How Forge says a player lost (its "Game Outcome: ... has lost because/due to ..." entries), in a
# few plain kinds; anything else is "other" and its words are kept with the game.
LOSS_KINDS = (
    ("damage from generals", "commander damage"),
    ("life total", "life"),
    ("poison", "poison"),
    ("library", "empty library"),
    ("conced", "conceded"),
)
# Games listed one by one in a summary (a run plays at most 50).
MAX_GAMES_LISTED = 50


def loss_kind(why: Any) -> str:
    text = str(why or "").casefold()
    return next((kind for key, kind in LOSS_KINDS if key in text), "other")


def summarise(job: dict[str, Any], seats: list[str]) -> dict[str, Any]:
    """Win rates per seat (seat 1 is the member's deck) from a Forge service job view, with how
    long the games took (seconds and, from Forge images that print it, turns), how each ended, and
    how each seat lost."""
    results = job.get("results") or []
    games = len(results)
    out: dict[str, Any] = {
        "games": games,
        "games_requested": job.get("games_requested"),
        "draws": sum(1 for r in results if r.get("draw")),
        # Games Forge stopped at its time limit (counted as draws): many of them mean the Pi is too slow
        # for these decks, and the win rates say little.
        "stopped_slow": sum(1 for r in results if r.get("stopped_slow")),
        "seats": [],
    }
    for i, name in enumerate(seats, start=1):
        wins = sum(1 for r in results if r.get("winner") == i)
        low, high = wilson(wins, games)
        losses: dict[str, int] = {}
        for r in results:
            lost = r.get("lost") if isinstance(r.get("lost"), dict) else {}
            if str(i) in lost:
                kind = loss_kind(lost[str(i)])
                losses[kind] = losses.get(kind, 0) + 1
        out["seats"].append(
            {
                "seat": i,
                "deck": name,
                "wins": wins,
                "win_rate": round(wins / games, 4) if games else None,
                "win_rate_95": [low, high],
                **({"losses": losses} if losses else {}),
            }
        )
    times = [r["ms"] for r in results if isinstance(r.get("ms"), int)]
    if times:
        out["average_game_seconds"] = round(sum(times) / len(times) / 1000, 1)
        out["median_game_seconds"] = round(statistics.median(times) / 1000, 1)
    turns = [r["turn"] for r in results if isinstance(r.get("turn"), int) and not isinstance(r["turn"], bool)]
    if turns:
        out["median_turn"] = statistics.median(turns)
        out["turns_range"] = [min(turns), max(turns)]
    out["ended"] = {
        "won": sum(1 for r in results if isinstance(r.get("winner"), int)),
        "draw": sum(1 for r in results if r.get("draw") and not r.get("stopped_slow")),
        "time_limit": out["stopped_slow"],
        "unread": sum(1 for r in results if r.get("unparsed")),
    }
    out["game_list"] = [
        {
            "game": r.get("game"),
            "seconds": round(r["ms"] / 1000, 1) if isinstance(r.get("ms"), int) else None,
            "turn": r.get("turn") if isinstance(r.get("turn"), int) else None,
            "winner": r.get("winner") if isinstance(r.get("winner"), int) else None,
            "ended": "time_limit"
            if r.get("stopped_slow")
            else "draw"
            if r.get("draw")
            else "won"
            if isinstance(r.get("winner"), int)
            else "unread",
        }
        for r in results[:MAX_GAMES_LISTED]
        if isinstance(r, dict)
    ]
    unparsed = [r["unparsed"] for r in results if r.get("unparsed")]
    if unparsed:
        out["unparsed_results"] = unparsed[:10]
    return out


FORGE_RESTARTED = "Forge restarted before this run finished."
MORE_GONE = (
    "Some of this report's opponents are no longer in this gateway's Forge ({gone}), so more games "
    "can't be played at the same table. Run the report (or its comparison) again."
)
FORGE_SILENT = "Forge stopped answering about this run."


async def _addresses(host: str, port: int) -> list[str]:
    """Every address ``host`` resolves to (one per copy of a service in Swarm's dnsrr endpoint mode
    or Docker Compose, one in Swarm's default virtual-IP mode)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


class ForgeClient:
    """The Forge service at ``base_url``. When that name resolves to several addresses (several
    copies of the service, each with its own jobs), ``pick`` chooses the least busy copy for a new
    run and ``at`` reaches that same copy later, so a run is always asked about where it plays.
    With one address nothing changes: every call goes to ``base_url``."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[[str, int], Awaitable[list[str]]] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)
        self._health: tuple[float, bool] = (0.0, False)  # (checked at, answering), for the admin page
        self._health_lock = asyncio.Lock()
        self._timeout, self._transport = timeout, transport
        # A client given its own transport (the tests) has no network to look names up on, unless
        # it is given a resolver too.
        self._resolver = resolver or (_addresses if transport is None else None)
        url = httpx.URL(self.base_url)
        self._scheme, self._host = url.scheme, url.host
        self._port = url.port or (443 if url.scheme == "https" else 80)
        self._copies: dict[str, ForgeClient] = {}

    def _copy_url(self, address: str) -> str:
        host = f"[{address}]" if ":" in address else address
        return f"{self._scheme}://{host}:{self._port}"

    async def _lookup(self) -> list[str] | None:
        """Every address the service's name has now, or None when that can't be told: no resolver,
        an address given directly, or no answer within LOOKUP_DEADLINE_SECONDS."""
        if self._resolver is None:
            return None
        try:
            ipaddress.ip_address(self._host)
            return None  # an address, not a name: one copy
        except ValueError:
            pass
        try:
            found = await asyncio.wait_for(self._resolver(self._host, self._port), LOOKUP_DEADLINE_SECONDS)
        except Exception as exc:  # no answer now (a timeout included): the one service, as before
            logger.info("looking up Forge's copies failed: %s", type(exc).__name__)
            return None
        return sorted(set(found))

    async def copies(self) -> list[str]:
        """The base URL of each copy behind ``base_url``, or [] when there is only one (one address,
        an address given directly, or a name that can't be looked up now)."""
        found = await self._lookup()
        if not found:
            return []
        if all(ipaddress.ip_address(a.split("%")[0]).is_loopback for a in found):
            return []  # "localhost" as 127.0.0.1 and ::1 is one service, not two copies
        return [self._copy_url(a) for a in found] if len(found) > 1 else []

    async def has_copy(self, url: Any) -> bool | None:
        """Whether a run's stored copy (``engine``) is still one of the service's addresses, or None
        when that can't be told now (the name gave no answer)."""
        found = await self._lookup()
        if found is None:
            return None
        pinned = self.at(url)
        return pinned is not None and pinned.base_url in {self._copy_url(a) for a in found}

    def at(self, url: Any) -> ForgeClient | None:
        """The client for one copy, from a run's stored ``engine``: only an address URL on this
        service's scheme and port (what ``copies`` gives), never any other host."""
        if not isinstance(url, str):
            return None
        try:
            parsed = httpx.URL(url)
            ipaddress.ip_address(parsed.host)
        except (ValueError, TypeError, httpx.InvalidURL):
            return None
        if (
            parsed.scheme != self._scheme
            or (parsed.port or self._port) != self._port
            or parsed.path not in ("", "/")
        ):
            return None
        clean = self._copy_url(parsed.host)
        if clean not in self._copies:
            self._copies[clean] = ForgeClient(clean, timeout=self._timeout, transport=self._transport)
        return self._copies[clean]

    async def pick(self, urls: list[str] | None = None) -> tuple[ForgeClient, str | None, int]:
        """Where a new run starts: (client, that copy's URL to store with the run, number of copies).
        With one copy: (this client, None, 1). Otherwise the copy with the most free places, then
        the shortest queue; a copy that does not answer is passed over. ``urls``: the copies
        already looked up (``copies``), so a caller can do the lookup outside a lock."""
        urls = await self.copies() if urls is None else urls
        if not urls:
            return self, None, 1

        async def load(url: str) -> tuple[Any, ...] | None:
            client = self.at(url)
            try:
                h = await asyncio.wait_for(client.health(), HEALTH_DEADLINE_SECONDS) if client else None
            except Exception:
                return None
            if not isinstance(h, dict) or h.get("status") != "ok":
                return None
            free = h.get("free_places")
            if not isinstance(free, int):  # an image before 0.7.30: idle or not
                free = 0 if h.get("busy") else 1
            queued = h.get("queued") if isinstance(h.get("queued"), int) else 0
            return (-free, queued)

        scores = await asyncio.gather(*(load(u) for u in urls))
        ranked = sorted((s, i) for i, s in enumerate(scores) if s is not None)
        best = urls[ranked[0][1]] if ranked else urls[0]  # none answered: the start says so
        client = self.at(best)
        assert client is not None
        return client, best, len(urls)

    @property
    def last_health(self) -> tuple[float, bool]:
        """(when last checked, whether it answered), for the admin page."""
        return self._health

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            r = await self._client.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise ForgeError("unavailable", f"Forge did not answer ({type(exc).__name__}).") from exc
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 404:
            raise ForgeError("not_found", data.get("error") or "not found")
        if r.status_code == 422:
            raise ForgeError("unknown_cards", "Forge does not have some of these cards.", data)
        if r.status_code == 429:
            raise ForgeError("busy", "Forge's queue is full. Try again later.")
        if r.status_code >= 400:
            raise ForgeError("invalid", data.get("error") or f"HTTP {r.status_code}")
        return data

    async def health(self) -> dict[str, Any]:
        return await self._call("GET", "/health")

    async def healthy(self) -> bool:
        """Whether the service answers /health now, for the health check; cached like Mystic
        Forge's probe. A failure is logged once per probe."""
        at, ok = self._health
        if time.time() - at < (HEALTH_CACHE_SECONDS if ok else HEALTH_DOWN_CACHE_SECONDS):
            return ok
        async with self._health_lock:
            at, ok = self._health  # another caller may have probed while this one waited
            if time.time() - at < (HEALTH_CACHE_SECONDS if ok else HEALTH_DOWN_CACHE_SECONDS):
                return ok
            try:
                reply = await asyncio.wait_for(self.health(), HEALTH_DEADLINE_SECONDS)
                ok = isinstance(reply, dict) and reply.get("status") == "ok"
            except Exception as exc:
                logger.warning("health check: Forge is not answering: %s", type(exc).__name__)
                ok = False
            self._health = (time.time(), ok)
            return ok

    async def precons(self) -> list[dict[str, Any]]:
        return (await self._call("GET", "/precons")).get("precons") or []

    async def check(self, names: list[str]) -> dict[str, str | None]:
        resolved: dict[str, str | None] = {}
        for i in range(0, len(names), 500):
            resolved.update((await self._call("POST", "/check", {"names": names[i : i + 500]}))["resolved"])
        return resolved

    async def start(
        self,
        decks: list[dict[str, Any]],
        games: int,
        fmt: str = "Commander",
        seed: int | None = None,
        parallel: int = 1,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"decks": decks, "games": games, "format": fmt}
        if seed is not None:
            body["seed"] = seed
        if parallel > 1:  # a Forge image before 0.7.30 ignores it and plays one game at a time
            body["parallel"] = parallel
        return await self._call("POST", "/jobs", body)

    async def job(self, job_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/jobs/{job_id}")

    async def cancel(self, job_id: str) -> dict[str, Any]:
        return await self._call("DELETE", f"/jobs/{job_id}")


BRACKET_SOURCES = {
    "archidekt": "the bracket set on the deck in Archidekt",
    "estimate": "Archidekt's estimate from cards",
}


def _source_text(source: str | None, deck: str | None) -> str:
    """Where the bracket came from, naming ``deck`` when the notes are about another deck (a
    shared run, read in the second deck's report)."""
    if deck and source == "archidekt":
        return f"the bracket set on {deck} in Archidekt"
    if deck and source == "estimate":
        return f"Archidekt's estimate from cards for {deck}"
    return BRACKET_SOURCES.get(source or "", f"{deck}'s bracket" if deck else "the deck's bracket")


def pick_opponents(
    precons: list[dict[str, Any]], bracket: int | None, source: str | None, *, deck: str | None = None
) -> tuple[list[str], dict[str, Any]]:
    """The precons to seat against a deck, and why: those of the deck's bracket first, then the
    nearest brackets, newest first within a bracket (the Forge service lists its precons newest
    first). A precon's bracket comes from the Forge image (docker/forge/precons.py: the game
    changers it holds). Without a bracket for the deck, or precons that carry none (an older
    Forge image), the newest precons play and ``opponent_choice`` says so. ``deck`` names the deck
    the bracket belongs to when the notes appear in another deck's report (shared_opponents)."""
    newest = [p["name"] for p in precons][:DEFAULT_OPPONENTS]
    rated = [p for p in precons if isinstance(p.get("bracket"), int)]
    if bracket is None:
        # a deck's name is never re-cased ("eldrazi tron" stays as its owner wrote it)
        lead = f"There is no bracket for {deck}" if deck else "The deck has no bracket"
        return newest, {
            "rule": "newest",
            "note": f"{lead} (none set in Archidekt and no estimate), so the newest precons play.",
        }
    why = _source_text(source, deck)
    # an estimate is never called the deck's bracket
    lead = (
        f"{why[0].upper()}{why[1:]} is bracket {bracket}"
        if source == "estimate"
        else f"Bracket {bracket} ({why})"
    )
    if not rated:
        return newest, {
            "bracket": bracket,
            "source": source,
            "rule": "newest",
            "note": f"{lead}, but the precons in this gateway's version of Forge carry no bracket, so "
            "the newest precons play. A newer version of Forge matches opponents by bracket.",
        }
    order = sorted(range(len(rated)), key=lambda i: (abs(rated[i]["bracket"] - bracket), i))
    chosen = [rated[i] for i in order[:DEFAULT_OPPONENTS]]
    exact = sum(1 for p in chosen if p["bracket"] == bracket)
    if exact == len(chosen):
        note = f"Opponents are bracket {bracket} precons, matching {why}."
    else:
        others = sorted({p["bracket"] for p in chosen if p["bracket"] != bracket})
        nearest = f"the nearest bracket{'s' if len(others) != 1 else ''} ({', '.join(map(str, others))})"
        if exact:
            have = f"{exact} bracket {bracket} precon{'s are' if exact != 1 else ' is'} here"
            note = f"{lead}: only {have}; the rest come from {nearest}."
        else:
            note = f"{lead}: no bracket {bracket} precon is here, so {nearest} play."
    return [p["name"] for p in chosen], {
        "bracket": bracket,
        "source": source,
        "rule": "bracket" if exact == len(chosen) else "nearest",
        "opponent_brackets": {p["name"]: p["bracket"] for p in chosen},
        "note": note,
    }


# A Forge comparison (the Compare page's "Test both in Forge", or an assistant's
# compare_with) seats both decks against precons picked for the higher of their brackets, so the
# order of the two decks doesn't matter.
PAIR_LEAD = "Opponents for this comparison are picked for the higher of the two decks' brackets."
PAIR_NO_BRACKET = (
    "Neither deck has a bracket (none set in Archidekt and no estimate), so the newest precons play."
)


def pair_choice(choice: dict[str, Any], bracket: int | None) -> dict[str, Any]:
    """A comparison's opponent choice: marked as one, its note led by PAIR_LEAD, or saying that
    neither deck has a bracket."""
    note = PAIR_NO_BRACKET if bracket is None else f"{PAIR_LEAD} {choice['note']}"
    return {**choice, "pair": True, "note": note}


def bracket_parts(value: Any) -> tuple[int | None, str | None, str | None, bool]:
    """A run's stored bracket: [level, source] for the deck's own, or [level, source, deck, True]
    for a comparison's run (``deck`` names the other deck when its bracket was the higher, else
    is None)."""
    parts = list(value) if isinstance(value, (list, tuple)) else []
    parts += [None] * (4 - len(parts))
    level, source, of_deck, pair = parts[:4]
    return (
        level if isinstance(level, int) and not isinstance(level, bool) else None,
        source if isinstance(source, str) else None,
        of_deck if isinstance(of_deck, str) and of_deck else None,
        pair is True,
    )


def shared_opponents(
    precons: list[dict[str, Any]], shared: dict[str, Any]
) -> tuple[list[str] | None, dict[str, Any]]:
    """The precons another report's run sat at the table, so both decks meet the same
    table; (None, choice) when one of them is no longer in this Forge image. Without that run's
    precons (it never started), they are picked for its bracket, as its own run would."""
    deck = str(shared.get("deck_name") or "the other deck")
    link = {k: shared[k] for k in ("report_id", "deck_name") if isinstance(shared.get(k), str)}
    opponents = shared.get("opponents")
    if isinstance(opponents, list) and opponents and all(isinstance(o, str) for o in opponents):
        have = {p["name"] for p in precons}
        gone = [o for o in opponents if o not in have]
        if gone:
            return None, {
                "rule": "shared",
                "shared_with": link,
                "note": f"Some of {deck}'s opponents are no longer in this Forge ({', '.join(gone)}), "
                "so this run cannot seat the same table. Run both reports again to compare them.",
            }
        by_name = {p["name"]: p for p in precons}
        brackets = {o: by_name[o]["bracket"] for o in opponents if isinstance(by_name[o].get("bracket"), int)}
        how = ", picked for the higher of the two decks' brackets," if shared.get("pair") else ","
        return list(opponents), {
            "rule": "shared",
            "shared_with": link,
            **({"opponent_brackets": brackets} if brackets else {}),
            **({"pair": True} if shared.get("pair") else {}),
            "note": f"The same opponents as {deck}'s Forge games{how} so both decks met the same table.",
        }
    # its run has not started yet: picked as that run will pick them, for the bracket it waits
    # with (the higher of the two decks' in a comparison: then [n, source, deck])
    level, source, of_deck, pair = bracket_parts(shared.get("bracket"))
    chosen, picked = pick_opponents(precons, level, source, deck=of_deck or deck)
    if pair:
        picked = pair_choice(picked, level)
    # how they were picked is pick_opponents' own note, true also with no bracket or unrated precons
    return chosen, {
        **picked,
        "rule": "shared",
        "shared_with": link,
        "note": f"The same opponents {deck}'s Forge games get, so both decks meet the same table. "
        f"{picked['note']}",
    }


async def start_run(
    client: ForgeClient,
    deck_text: str,
    deck_name: str,
    *,
    games: int,
    seed: int | None = None,
    bracket: int | None = None,
    bracket_source: str | None = None,
    bracket_deck: str | None = None,
    pair: bool = False,
    parallel: int = 1,
    shared: dict[str, Any] | None = None,
    more: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Start a Forge run of the member's deck against bundled precons and return the report's
    ``forge`` section. ``shared`` seats the opponents of another report's run, so two
    decks meet the same table: its ``opponents`` (that run's precons) when it has them, else
    they are picked for its ``bracket``; ``report_id`` and ``deck_name`` say whose they were.
    ``pair``: a comparison's first run, whose ``bracket`` is the higher of the two
    decks' (``bracket_deck`` names the other deck when it is that one's).
    ``more`` (Play more games): the ``opponents`` and ``choice`` of the report's earlier games, so
    the extra games seat the same table.
    Cards Forge lacks are removed from the run and named in ``not_played``; the run is refused
    only when the commander itself is missing. Every run is seeded: without a seed from the
    request the gateway picks one, so ``seed`` is always the one Forge used (``seed_source`` says
    whose it was), kept for the record: Forge does not always replay a game exactly from its seed."""
    deck = forge_deck(deck_text)
    if not deck["commander"]:
        return {
            "state": "failed",
            # Forge's opponents are the bundled Commander precons; there are no 60-card ones.
            "error": "Forge plays Commander games against Commander precons, and this deck has no "
            "card in a Commander category, so Forge doesn't run it.",
        }
    names = list(dict.fromkeys(deck["commander"] + [name for _, name in deck["main"]]))
    resolved = await client.check(names)
    not_played = sorted(n for n in names if not resolved.get(n))
    if any(not resolved.get(c) for c in deck["commander"]):
        return {
            "state": "failed",
            "not_played": not_played,
            "error": "Forge does not have this deck's commander, so it can't run the game.",
        }
    deck["main"] = [[n, name] for n, name in deck["main"] if resolved.get(name)]
    precons = [p for p in await client.precons() if isinstance(p, dict) and isinstance(p.get("name"), str)]
    if more is not None:
        chosen, choice = list(more.get("opponents") or []), dict(more.get("choice") or {})
        gone = [o for o in chosen if o not in {p["name"] for p in precons}]
        if gone:
            return {"state": "failed", "error": MORE_GONE.format(gone=", ".join(gone))}
    elif shared is not None:
        chosen, choice = shared_opponents(precons, shared)
        if chosen is None:
            return {"state": "failed", "error": choice["note"], "opponent_choice": choice}
    else:
        chosen, choice = pick_opponents(precons, bracket, bracket_source, deck=bracket_deck)
        if pair:
            choice = pair_choice(choice, bracket)
    if not chosen:
        return {"state": "failed", "error": "Forge has no opponent decks."}
    seats = [deck_name, *chosen]
    # Forge takes seeds from 0 to 2**63 - 1 (it reads "-5" as a flag); the gateway's seeds may be
    # negative, so they are mapped onto that range, the same seed always to the same Forge seed.
    # Without one, Forge would pick its own and never say which (its seed option is optional).
    source = "request" if seed is not None else "gateway"
    if seed is None:
        seed = secrets.randbelow(2**63)
    forge_seed = seed % 2**63
    job = await client.start(
        [deck, *({"precon": p} for p in chosen)], games, seed=forge_seed, parallel=max(1, parallel)
    )
    return {
        "state": job.get("state", "queued"),
        "seed": seed,
        "seed_source": source,
        **({"forge_seed": forge_seed} if forge_seed != seed else {}),
        "job_id": job["id"],
        "seats": seats,
        "opponent_choice": choice,
        "not_played": not_played,
        "games_requested": games,
        "forge_version": job.get("forge_version"),
        # the deck as Forge played it, so Play more games can seat it again (never shown)
        "deck_text": deck_text,
        # how many games the engine plays at once (its own ceiling may lower what was asked)
        **({"parallel": job["parallel"]} if isinstance(job.get("parallel"), int) else {}),
        # its places for all runs together: two or more let a comparison's decks play side by side
        **({"max_parallel": job["max_parallel"]} if isinstance(job.get("max_parallel"), int) else {}),
        "started_at": int(time.time()),
    }


async def refresh(
    client: ForgeClient,
    section: dict[str, Any],
    *,
    pinned_gone: Callable[[], Awaitable[bool]] | None = None,
) -> dict[str, Any]:
    """The ``forge`` section updated from its job; unchanged once the run has ended.
    ``pinned_gone``, for a run kept on one of several copies: whether that copy's address is no
    longer the service's (asked only when the copy does not answer)."""
    if section.get("state") in FINAL_STATES or not section.get("job_id"):
        return section
    try:
        job = await client.job(section["job_id"])
        if not isinstance(job, dict) or job.get("state") not in {"queued", "running", *FINAL_STATES}:
            raise ForgeError("invalid", "Forge gave an unexpected answer.")
    except ForgeError as exc:
        now = time.time()
        if now - section.get("started_at", now) > GIVE_UP_SECONDS:
            return {**section, "state": "lost", "error": FORGE_SILENT}
        if exc.kind == "not_found":
            # The Forge service restarted and the job is gone.
            return {**section, "state": "lost", "error": FORGE_RESTARTED}
        if pinned_gone is not None and exc.kind == "unavailable":
            # A copy that restarted or moved has a new address: as lost as a single copy's restart.
            if await pinned_gone():
                return {**section, "state": "lost", "error": FORGE_RESTARTED}
            since = section.get("unanswered_since")
            since = since if isinstance(since, (int, float)) else int(now)
            if now - since >= PINNED_SILENCE_SECONDS:
                return {**section, "state": "lost", "error": FORGE_SILENT}
            return {**section, "unanswered_since": since}
        return section
    # Play more games: the report's earlier games come first, and these are numbered after them
    earlier = [r for r in section.get("earlier") or [] if isinstance(r, dict)]
    offset = max([0, *(r["game"] for r in earlier if isinstance(r.get("game"), int))])
    played = earlier + [
        {**r, "game": offset + r["game"]} if isinstance(r.get("game"), int) else r
        for r in job.get("results") or []
        if isinstance(r, dict)
    ]
    asked = section.get("games_requested") if earlier else job.get("games_requested")
    out = {
        **section,
        "state": job["state"],
        "result": summarise({**job, "results": played, "games_requested": asked}, section.get("seats") or []),
        "played": played,
    }
    out.pop("unanswered_since", None)
    if job.get("load_problems"):
        out["load_problems"] = job["load_problems"]
    if job["state"] in ("failed", "timeout"):
        out["log_tail"] = (job.get("log_tail") or [])[-15:]
    return out
