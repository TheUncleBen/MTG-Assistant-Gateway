"""Deck simulations on Forge, the rules engine in the stack's private Forge service.

The Forge service (docker/forge/) plays real games: opponents, combat, the stack, the graveyard. A run
is a background job there, because a four-player Commander game takes far longer than a goldfish
hand. The gateway starts the job, keeps its state in the report, and refreshes it until it ends.

Every card Forge cannot play is named in the result, never dropped silently; the assistant
explains those from the card's exact text.
"""

from __future__ import annotations

import logging
import math
import time
from typing import Any

import httpx

from .decklist import DecklistError, parse_decklist

logger = logging.getLogger(__name__)

DEFAULT_OPPONENTS = 3
FINAL_STATES = frozenset({"done", "failed", "timeout", "cancelled", "lost"})
# A run still unfinished this long after it started is given up (the engine was unreachable).
GIVE_UP_SECONDS = 4 * 3600


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


def summarise(job: dict[str, Any], seats: list[str]) -> dict[str, Any]:
    """Win rates per seat (seat 1 is the member's deck) from a Forge service job view."""
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
        out["seats"].append(
            {
                "seat": i,
                "deck": name,
                "wins": wins,
                "win_rate": round(wins / games, 4) if games else None,
                "win_rate_95": [low, high],
            }
        )
    times = [r["ms"] for r in results if isinstance(r.get("ms"), int)]
    if times:
        out["average_game_seconds"] = round(sum(times) / len(times) / 1000, 1)
    unparsed = [r["unparsed"] for r in results if r.get("unparsed")]
    if unparsed:
        out["unparsed_results"] = unparsed[:10]
    return out


class ForgeClient:
    def __init__(
        self, base_url: str, *, timeout: float = 15.0, transport: httpx.AsyncBaseTransport | None = None
    ):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout, transport=transport)

    async def _call(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            r = await self._client.request(method, path, json=body)
        except httpx.HTTPError as exc:
            raise ForgeError(
                "unavailable", f"The simulation engine did not answer ({type(exc).__name__})."
            ) from exc
        try:
            data = r.json()
        except ValueError:
            data = {}
        if r.status_code == 404:
            raise ForgeError("not_found", data.get("error") or "not found")
        if r.status_code == 422:
            raise ForgeError("unknown_cards", "Forge does not have some of these cards.", data)
        if r.status_code == 429:
            raise ForgeError("busy", "The simulation engine's queue is full; try again later.")
        if r.status_code >= 400:
            raise ForgeError("invalid", data.get("error") or f"HTTP {r.status_code}")
        return data

    async def health(self) -> dict[str, Any]:
        return await self._call("GET", "/health")

    async def precons(self) -> list[dict[str, Any]]:
        return (await self._call("GET", "/precons")).get("precons") or []

    async def check(self, names: list[str]) -> dict[str, str | None]:
        resolved: dict[str, str | None] = {}
        for i in range(0, len(names), 500):
            resolved.update((await self._call("POST", "/check", {"names": names[i : i + 500]}))["resolved"])
        return resolved

    async def start(self, decks: list[dict[str, Any]], games: int, fmt: str = "Commander") -> dict[str, Any]:
        return await self._call("POST", "/jobs", {"decks": decks, "games": games, "format": fmt})

    async def job(self, job_id: str) -> dict[str, Any]:
        return await self._call("GET", f"/jobs/{job_id}")

    async def cancel(self, job_id: str) -> dict[str, Any]:
        return await self._call("DELETE", f"/jobs/{job_id}")


async def start_run(
    client: ForgeClient, deck_text: str, deck_name: str, *, games: int, opponents: list[str] | None = None
) -> dict[str, Any]:
    """Start a Forge run of the member's deck against bundled precons and return the report's
    ``forge`` section. Cards Forge lacks are removed from the run and named in ``not_played``;
    the run is refused only when the commander itself is missing."""
    deck = forge_deck(deck_text)
    if not deck["commander"]:
        return {
            "state": "failed",
            "error": "The deck has no card in a Commander category, so Forge can't seat it.",
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
    available = [p["name"] for p in await client.precons()]
    chosen = [p for p in (opponents or []) if p in available] or available[:DEFAULT_OPPONENTS]
    if not chosen:
        return {"state": "failed", "error": "The simulation engine has no opponent decks."}
    seats = [deck_name, *chosen]
    job = await client.start([deck, *({"precon": p} for p in chosen)], games)
    return {
        "state": job.get("state", "queued"),
        "job_id": job["id"],
        "seats": seats,
        "not_played": not_played,
        "games_requested": games,
        "forge_version": job.get("forge_version"),
        "started_at": int(time.time()),
    }


async def refresh(client: ForgeClient, section: dict[str, Any]) -> dict[str, Any]:
    """The ``forge`` section updated from its job; unchanged once the run has ended."""
    if section.get("state") in FINAL_STATES or not section.get("job_id"):
        return section
    try:
        job = await client.job(section["job_id"])
    except ForgeError as exc:
        if time.time() - section.get("started_at", time.time()) > GIVE_UP_SECONDS:
            return {
                **section,
                "state": "lost",
                "error": "The simulation engine stopped answering about this run.",
            }
        if exc.kind == "not_found":
            # The Forge service restarted and the job is gone.
            return {
                **section,
                "state": "lost",
                "error": "The simulation engine restarted before this run finished.",
            }
        return section
    out = {**section, "state": job["state"], "result": summarise(job, section.get("seats") or [])}
    if job.get("load_problems"):
        out["load_problems"] = job["load_problems"]
    if job["state"] in ("failed", "timeout"):
        out["log_tail"] = (job.get("log_tail") or [])[-15:]
    return out
