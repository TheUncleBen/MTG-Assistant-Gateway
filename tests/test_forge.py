"""Forge runs from the gateway: decklist conversion, win-rate summaries, the background refresh of a
report's run, and cards Forge lacks named rather than dropped."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from mtg_gateway.archidekt import parse_deck
from mtg_gateway.db import Database
from mtg_gateway.forge import ForgeClient, forge_deck, refresh, start_run, summarise, wilson
from mtg_gateway.reports import ReportService

from .fake_archidekt import FakeArchidekt


class FakeForge:
    """The Forge service's HTTP API, enough for the gateway: /check, /precons, /jobs."""

    def __init__(self, *, missing: set[str] | None = None) -> None:
        self.missing = missing or set()
        self.jobs: dict[str, dict[str, Any]] = {}
        self.started: list[dict[str, Any]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/check":
            names = json.loads(request.content)["names"]
            return httpx.Response(
                200, json={"resolved": {n: None if n in self.missing else n for n in names}}
            )
        if path == "/precons":
            return httpx.Response(
                200,
                json={
                    "precons": [
                        {"name": f"Precon {i}", "release_date": "2026", "commander": []} for i in (1, 2, 3, 4)
                    ]
                },
            )
        if path == "/jobs" and request.method == "POST":
            body = json.loads(request.content)
            self.started.append(body)
            jid = f"{len(self.jobs):016x}"
            self.jobs[jid] = {"id": jid, "state": "running", "games_requested": body["games"], "results": []}
            return httpx.Response(202, json=self.jobs[jid])
        if path.startswith("/jobs/"):
            job = self.jobs.get(path.rsplit("/", 1)[1])
            return (
                httpx.Response(200, json=job) if job else httpx.Response(404, json={"error": "no such job"})
            )
        return httpx.Response(404, json={"error": "not found"})

    def finish(self, jid: str, winners: list[int]) -> None:
        self.jobs[jid].update(
            state="done",
            results=[{"game": i + 1, "ms": 60000, "winner": w, "draw": False} for i, w in enumerate(winners)],
        )

    def client(self) -> ForgeClient:
        return ForgeClient("http://forge.test", transport=httpx.MockTransport(self.handler))


DECK_TEXT = "1 Liesa, Forgotten Archangel [Commander]\n1 Sol Ring\n1 Fake Sticker Card\n10 Plains\n1 Plains\n"


def test_forge_deck_splits_commander_and_merges_rows() -> None:
    deck = forge_deck(DECK_TEXT + "Sideboard\n1 Swords to Plowshares\n")
    assert deck["commander"] == ["Liesa, Forgotten Archangel"]
    assert deck["main"] == [[1, "Sol Ring"], [1, "Fake Sticker Card"], [11, "Plains"]]


def test_wilson_and_summary() -> None:
    assert wilson(0, 0) == (0.0, 0.0)
    low, high = wilson(5, 10)
    assert low < 0.5 < high
    out = summarise(
        {
            "games_requested": 4,
            "results": [
                {"winner": 1, "ms": 1000},
                {"winner": 2, "ms": 3000},
                {"draw": True, "winner": None},
                {"winner": 1},
            ],
        },
        ["Mine", "Opp"],
    )
    assert out["games"] == 4 and out["draws"] == 1
    assert out["seats"][0]["wins"] == 2 and out["seats"][0]["win_rate"] == 0.5
    assert out["average_game_seconds"] == 2.0


async def test_start_run_names_cards_forge_lacks_and_plays_the_rest() -> None:
    fake = FakeForge(missing={"Fake Sticker Card"})
    section = await start_run(fake.client(), DECK_TEXT, "Liesa", games=5)
    assert section["state"] == "running"
    assert section["not_played"] == ["Fake Sticker Card"]
    assert section["seats"] == ["Liesa", "Precon 1", "Precon 2", "Precon 3"]
    sent = fake.started[0]
    assert sent["games"] == 5 and sent["decks"][1:] == [{"precon": f"Precon {i}"} for i in (1, 2, 3)]
    assert [1, "Fake Sticker Card"] not in sent["decks"][0]["main"]


async def test_start_run_refuses_a_missing_commander() -> None:
    fake = FakeForge(missing={"Liesa, Forgotten Archangel"})
    section = await start_run(fake.client(), DECK_TEXT, "Liesa", games=5)
    assert section["state"] == "failed" and "commander" in section["error"]
    assert fake.started == []


async def test_refresh_finishes_and_reports_a_lost_job() -> None:
    fake = FakeForge()
    client = fake.client()
    section = await start_run(client, DECK_TEXT, "Liesa", games=3)
    fake.finish(section["job_id"], [1, 1, 3])
    done = await refresh(client, section)
    assert done["state"] == "done" and done["result"]["seats"][0]["wins"] == 2
    assert await refresh(client, done) is done  # final: never asked again
    gone = await refresh(client, {**section, "job_id": "ffffffffffffffff"})
    assert gone["state"] == "lost"


class _Decks:
    async def get_any_deck(self, sub: str | None, ref: str) -> Any:
        return parse_deck(FakeArchidekt().decks[42])


async def test_report_runs_forge_in_the_background(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    out = await reports.run("alice", "42", games=300, options={"seed": 7})
    assert out["forge"]["state"] == "running"
    assert fake.started[0]["games"] == 4  # capped for the Pi
    assert fake.started[0]["seed"] == 7 and out["forge"]["seed"] == 7
    fake.finish(out["forge"]["job_id"], [1, 2, 1, 1])
    task = reports._forge_task
    assert task is not None
    await asyncio.wait_for(task, timeout=20)
    final = reports.get("alice", out["report_id"])["forge"]
    assert final["state"] == "done" and final["result"]["seats"][0]["wins"] == 3
