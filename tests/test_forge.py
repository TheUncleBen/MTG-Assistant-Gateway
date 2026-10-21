"""Forge runs from the gateway: decklist conversion, win-rate summaries, the background refresh of a
report's run, and cards Forge lacks named rather than dropped."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from mtg_gateway import forge as forge_module
from mtg_gateway import reports as reports_module
from mtg_gateway.archidekt import parse_deck
from mtg_gateway.db import Database
from mtg_gateway.decks import DeckError
from mtg_gateway.forge import (
    SOLO_TOO_OLD,
    ForgeClient,
    ForgeError,
    forge_deck,
    pick_opponents,
    refresh,
    start_run,
    start_solo_run,
    summarise,
    summarise_solo,
    wilson,
)
from mtg_gateway.report_view import FORGE_DRIFT_NOTE, forge_lines, forge_solo_lines
from mtg_gateway.reports import (
    SOLO_ONE_AT_A_TIME,
    ReportService,
    solo_game_seconds,
)

from .fake_archidekt import FakeArchidekt


class FakeForge:
    """The Forge service's HTTP API, enough for the gateway: /check, /precons, /jobs."""

    def __init__(self, *, missing: set[str] | None = None, brackets: dict[str, int] | None = None) -> None:
        self.missing = missing or set()
        # precon name -> bracket, as a Forge image built since 0.7.23 lists them (newest first);
        # None: an older image, four precons with no bracket
        self.brackets = brackets
        self.jobs: dict[str, dict[str, Any]] = {}
        self.started: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.up = True
        self.health_probes = 0
        self.max_parallel: int | None = None  # the engine's places, listed in each job when set
        self.parallel_cap: int | None = None  # a Forge image before 2.0.15-mag4 refuses more than 4
        self.solo = False  # a Forge image that plays solo tests (2.0.15-mag6 on) says so in /health

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            self.health_probes += 1
            if not self.up:
                raise httpx.ConnectError("down")
            extra = {"solo": True, "game_seconds": 240, "max_parallel": 1} if self.solo else {}
            return httpx.Response(200, json={"status": "ok", "busy": False, "queued": 0, **extra})
        if path.startswith("/jobs/") and request.method == "DELETE":
            jid = path.rsplit("/", 1)[1]
            if jid not in self.jobs:
                return httpx.Response(404, json={"error": "no such job"})
            self.cancelled.append(jid)
            self.jobs[jid]["state"] = "cancelled"
            return httpx.Response(200, json=self.jobs[jid])
        if path == "/check":
            names = json.loads(request.content)["names"]
            return httpx.Response(
                200, json={"resolved": {n: None if n in self.missing else n for n in names}}
            )
        if path == "/precons":
            if self.brackets is not None:
                listed = [
                    {"name": n, "release_date": "2026", "commander": [], "bracket": b}
                    for n, b in self.brackets.items()
                ]
                return httpx.Response(200, json={"precons": listed})
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
            if self.parallel_cap is not None and body.get("parallel", 1) > self.parallel_cap:
                text = f"parallel must be a whole number from 1 to {self.parallel_cap}"
                return httpx.Response(400, json={"error": text})
            self.started.append(body)
            jid = f"{len(self.jobs):016x}"
            self.jobs[jid] = {"id": jid, "state": "running", "games_requested": body["games"], "results": []}
            if self.max_parallel is not None:
                self.jobs[jid]["max_parallel"] = self.max_parallel
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


def _solo_view(won: int | None, *, cmdr: int | None = 3, kept: int = 7, turns: int = 6) -> dict[str, Any]:
    return {
        "turns": turns,
        "kept": kept,
        "commander_cast_turn": cmdr,
        "first_out_turn": won,
        "won_turn": won,
        "lands_by_turn": [1] * turns,
        "casts_by_turn": [2] * turns,
        "damage_by_turn": [0] * (turns - 1) + [10],
        "commander_damage": 0,
    }


def test_summarise_solo_counts_wins_turns_and_averages_by_turn() -> None:
    results = [
        {"game": 1, "winner": 1, "solo": _solo_view(5, turns=5)},
        {"game": 2, "winner": 1, "solo": _solo_view(7, kept=6, turns=7)},
        {"game": 3, "draw": True, "solo": _solo_view(None, cmdr=None, turns=10)},
        {"game": 4, "unparsed": "garbled"},
    ]
    out = summarise_solo(results, 4, {"opponents": 3, "until_turn": 10})
    assert out["games"] == 3 and out["unread"] == 1
    assert out["won"] == 2 and out["won_rate"] == round(2 / 3, 4)
    assert out["median_won_turn"] == 6 and out["won_turn_range"] == [5, 7]
    assert out["median_commander_turn"] == 3 and out["mulligans"] == 1
    # turn 6 is averaged over the two games still going (7 and 10 turns), not all three
    assert out["damage_by_turn"][4] == round((10 + 0 + 0) / 3, 2)
    assert out["damage_by_turn"][5] == 0 and len(out["damage_by_turn"]) == 10
    assert out["lands_by_turn"] == [1.0] * 10


async def test_start_solo_run_sends_one_deck_and_refresh_summarises_it() -> None:
    fake = FakeForge(missing={"Fake Sticker Card"})
    client = fake.client()
    section = await start_solo_run(client, DECK_TEXT, "Liesa", games=4, seed=7)
    sent = fake.started[0]
    assert sent["solo"] == {"opponents": 3, "until_turn": 10} and sent["format"] == "Commander"
    assert len(sent["decks"]) == 1 and sent["seed"] == 7
    assert section["seats"] == ["Liesa"] and section["not_played"] == ["Fake Sticker Card"]
    fake.jobs[section["job_id"]].update(
        state="done", results=[{"game": 1, "winner": 1, "solo": _solo_view(6)}]
    )
    done = await refresh(client, section)
    assert done["state"] == "done" and done["result"]["median_won_turn"] == 6
    assert "seats" not in done["result"]


async def test_start_solo_run_plays_a_deck_without_commander_against_one() -> None:
    fake = FakeForge()
    await start_solo_run(fake.client(), "4 Lightning Bolt\n20 Mountain\n", "Burn", games=2, until_turn=5)
    sent = fake.started[0]
    assert sent["format"] == "Constructed" and sent["solo"] == {"opponents": 1, "until_turn": 5}
    await start_solo_run(fake.client(), DECK_TEXT, "Liesa", games=1, game_seconds=60)
    assert fake.started[1]["solo"] == {"opponents": 3, "until_turn": 10, "game_seconds": 60}
    with pytest.raises(ForgeError):
        await start_solo_run(fake.client(), DECK_TEXT, "Liesa", games=1, until_turn=31)


async def test_start_solo_run_says_when_forge_is_too_old() -> None:
    fake = FakeForge()
    old = fake.handler

    def refuse(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs" and request.method == "POST":
            return httpx.Response(400, json={"error": "decks must list 2 to 4 decks"})
        return old(request)

    client = ForgeClient("http://forge.test", transport=httpx.MockTransport(refuse))
    section = await start_solo_run(client, DECK_TEXT, "Liesa", games=2)
    assert section == {"state": "failed", "not_played": [], "error": SOLO_TOO_OLD}


class _Decks:
    def __init__(self, **change: Any) -> None:
        self.change = change

    async def get_any_deck(self, sub: str | None, ref: str) -> Any:
        raw = FakeArchidekt().decks[42]
        raw.update(self.change)
        return parse_deck(raw)


BRACKETED = {"New 2": 2, "New 3": 3, "Old 4": 4, "Mid 3": 3, "Older 2": 2, "Oldest 3": 3}


def test_pick_opponents_by_bracket_nearest_then_newest() -> None:
    precons = [{"name": n, "bracket": b} for n, b in BRACKETED.items()]
    names, why = pick_opponents(precons, 3, "archidekt")
    assert names == ["New 3", "Mid 3", "Oldest 3"] and why["rule"] == "bracket"
    assert "bracket 3 precons, matching the bracket set on the deck in Archidekt" in why["note"]
    names, why = pick_opponents(precons, 4, "estimate")
    assert names == ["Old 4", "New 3", "Mid 3"] and why["rule"] == "nearest"
    assert "only 1 bracket 4 precon is here; the rest come from the nearest bracket (3)" in why["note"], why
    assert "estimate" in why["note"]
    names, why = pick_opponents(precons, 1, "archidekt")  # no bracket 1 precons: the nearest, 2
    assert names == ["New 2", "Older 2", "New 3"] and "so the nearest brackets (2, 3) play" in why["note"]
    plain = [{"name": f"Precon {i}"} for i in (1, 2, 3, 4)]
    names, why = pick_opponents(plain, 3, "archidekt")
    assert names == ["Precon 1", "Precon 2", "Precon 3"] and why["rule"] == "newest"
    assert "carry no bracket" in why["note"]
    names, why = pick_opponents(precons, None, None)
    assert names == ["New 2", "New 3", "Old 4"] and "no bracket" in why["note"]


async def test_report_seats_precons_of_the_decks_bracket(tmp_path: Path) -> None:
    """A deck with bracket 3 set on Archidekt plays the bracket 3 precons, a queued run as well;
    without a set bracket the gateway's estimate decides, and the report says which and why."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge(brackets=BRACKETED)
    reports = ReportService(db, _Decks(edhBracket=3), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    first = await reports.run("alice", "42", games=300, options={"seed": 1})
    second = await reports.run("alice", "42", games=300, options={"seed": 2})
    assert [d["precon"] for d in fake.started[0]["decks"][1:]] == ["New 3", "Mid 3", "Oldest 3"]
    choice = first["forge"]["opponent_choice"]
    assert choice["bracket"] == 3 and choice["source"] == "archidekt" and choice["rule"] == "bracket"
    assert second["forge"]["state"] == "queued"
    fake.finish(first["forge"]["job_id"], [1])
    for _ in range(200):
        if len(fake.started) == 2:
            break
        await asyncio.sleep(0.02)
    assert [d["precon"] for d in fake.started[1]["decks"][1:]] == ["New 3", "Mid 3", "Oldest 3"]
    fake.finish(reports.get("alice", second["report_id"])["forge"]["job_id"], [1])
    await asyncio.wait_for(reports._forge_task, timeout=20)
    # the report says why these opponents
    lines = forge_lines(reports.get("alice", first["report_id"])["forge"])
    assert any(
        ln.startswith("Opponents: Opponents are bracket 3 precons") and "New 3 (bracket 3)" in ln
        for ln in lines
    )
    # no bracket set: the estimate (deck 42 holds no game changers: bracket 2)
    fake2 = FakeForge(brackets=BRACKETED)
    estimated = ReportService(
        db, _Decks(updatedAt="2026-10-05T00:00:00Z"), None, forge=fake2.client(), forge_games=4
    )  # type: ignore[arg-type]
    out = await estimated.run("alice", "42", games=300, options={"seed": 5})
    assert out["forge"]["opponent_choice"]["source"] == "estimate"
    assert out["forge"]["opponent_choice"]["bracket"] == out["stats"]["bracket_estimate"]["bracket"] == 2
    assert [d["precon"] for d in fake2.started[0]["decks"][1:]] == ["New 2", "Older 2", "New 3"]
    estimated._forge_task.cancel()


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


async def test_negative_seed_maps_onto_forges_range() -> None:
    fake = FakeForge()
    section = await start_run(fake.client(), DECK_TEXT, "Liesa", games=1, seed=-5)
    assert fake.started[0]["seed"] == 2**63 - 5 and section["seed"] == -5
    assert section["forge_seed"] == 2**63 - 5


async def test_refresh_gives_up_on_an_unexpected_reply() -> None:
    fake = FakeForge()
    client = fake.client()
    section = await start_run(client, DECK_TEXT, "Liesa", games=1)
    fake.jobs[section["job_id"]] = {"id": section["job_id"]}  # no state
    assert await refresh(client, section) == section  # kept, tried again later
    old = {**section, "started_at": section["started_at"] - 5 * 3600}
    assert (await refresh(client, old))["state"] == "lost"


async def test_one_unfinished_forge_run_per_member(tmp_path: Path, monkeypatch: Any) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    first = await reports.run("alice", "42", games=300, options={"seed": 1})
    assert first["forge"]["state"] == "running" and len(fake.started) == 1
    fake.finish(first["forge"]["job_id"], [1])
    assert reports._forge_task is not None
    await asyncio.wait_for(reports._forge_task, timeout=20)
    third = await reports.run("alice", "42", games=300, options={"seed": 3})
    assert third["forge"]["state"] == "running" and len(fake.started) == 2
    fake.finish(third["forge"]["job_id"], [1])
    await asyncio.wait_for(reports._forge_task, timeout=20)
    real_start = reports_module.start_run

    async def slow_start(*args: Any, **kwargs: Any) -> dict[str, Any]:
        await asyncio.sleep(0.05)  # the engine's network round trips, so two reports overlap
        return await real_start(*args, **kwargs)

    monkeypatch.setattr(reports_module, "start_run", slow_start)
    together = await asyncio.gather(
        # two different decks: the same deck's concurrent runs already share one report
        *(reports.run("alice", ref, games=300, options={"seed": 4}) for ref in ("42", "43"))
    )
    # no race past the limit: one plays, the other waits for it
    assert sorted(r["forge"]["state"] for r in together) == ["queued", "running"]
    assert len(fake.started) == 3


async def test_a_second_report_queues_behind_the_running_forge_run(tmp_path: Path) -> None:
    """A report made while the member's Forge run is still playing
    used to get no Forge games ("skipped"). It now waits in the member's queue, says where it is
    and what it waits for, starts by itself when the run ahead ends (also after a restart), and
    only a third waiting report is refused."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    first = await reports.run("alice", "42", games=300, options={"seed": 1})
    second = await reports.run("alice", "42", games=300, options={"seed": 2})
    third = await reports.run("alice", "42", games=300, options={"seed": 3})
    fourth = await reports.run("alice", "42", games=300, options={"seed": 4})
    assert first["forge"]["state"] == "running" and len(fake.started) == 1
    assert second["forge"]["state"] == "queued" and second["forge"]["position"] == 1
    assert second["forge"]["waiting_for"] == {
        "report_id": first["report_id"],
        "deck_name": first["deck_name"],
        "job_id": first["forge"]["job_id"],
    }
    assert "start" not in second["forge"]  # what starting it takes stays inside
    assert (
        third["forge"]["position"] == 2 and third["forge"]["waiting_for"]["report_id"] == second["report_id"]
    )
    assert fourth["forge"]["state"] == "skipped"
    assert fourth["forge"]["error"] == (
        "You already have 2 reports waiting for Forge, and each member can have at most 2 waiting, so "
        "this report has no Forge games. Run the report again once fewer than 2 of your reports are "
        "waiting."
    )
    status = reports.forge_only("alice", second["report_id"])["forge_status"]
    assert status == {
        "state": "queued",
        "games_done": 0,
        "games_requested": 4,
        "position": 1,
        "waiting_for": second["forge"]["waiting_for"],
    }
    # the first run ends: the refresher starts the next one, with its own seed
    fake.finish(first["forge"]["job_id"], [1, 2, 1, 1])
    for _ in range(200):
        if len(fake.started) == 2:
            break
        await asyncio.sleep(0.02)
    assert len(fake.started) == 2 and fake.started[1]["seed"] == 2
    now = reports.get("alice", second["report_id"])
    assert now["forge"]["state"] == "running" and now["forge_status"]["job_id"] == now["forge"]["job_id"]
    # the measured game time (60 s a game in the fake) gives the expected time
    assert now["forge"]["expected_seconds"] == 4 * 60
    assert reports.get("alice", third["report_id"])["forge"]["expected_seconds"] == 8 * 60
    # a gateway restart: a new service on the same database carries on with the queue
    reports._forge_task.cancel()
    again = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    again.forge_interval = 0.05
    again.resume_forge_runs()
    fake.finish(now["forge"]["job_id"], [1])
    for _ in range(200):
        if len(fake.started) == 3:
            break
        await asyncio.sleep(0.02)
    assert len(fake.started) == 3 and fake.started[2]["seed"] == 3
    assert again.get("alice", third["report_id"])["forge"]["state"] == "running"
    fake.finish(again.get("alice", third["report_id"])["forge"]["job_id"], [1])
    await asyncio.wait_for(again._forge_task, timeout=20)


async def test_forge_runs_are_always_seeded() -> None:
    """Without options.seed Forge picked its own seed and the report said null. The gateway
    now picks one, sends it and records it (seed_source says whose it was)."""
    fake = FakeForge()
    section = await start_run(fake.client(), DECK_TEXT, "Liesa", games=1)
    assert isinstance(section["seed"], int) and 0 <= section["seed"] < 2**63
    assert fake.started[0]["seed"] == section["seed"] and section["seed_source"] == "gateway"
    given = await start_run(fake.client(), DECK_TEXT, "Liesa", games=1, seed=9)
    assert given["seed"] == 9 and given["seed_source"] == "request"


def test_report_page_and_markdown_show_forge_results() -> None:
    from mtg_gateway.report_view import forge_html, report_markdown

    done = {
        "state": "done",
        "seed": 7,
        "not_played": ["Fake Sticker Card"],
        "result": {
            "games": 4,
            "games_requested": 4,
            "draws": 1,
            "stopped_slow": 1,
            "average_game_seconds": 41.0,
            "seats": [
                {"deck": "Liesa <b>", "wins": 2, "win_rate": 0.5, "win_rate_95": [0.15, 0.85]},
                {"deck": "Precon 1", "wins": 1, "win_rate": 0.25, "win_rate_95": [0.05, 0.7]},
            ],
        },
    }
    page = forge_html(done)
    # Laid out as a table, a row per deck at the table (a run stored before 0.7.30 as well)
    assert "Forge games" in page and "<table class='qty seats stack'>" in page
    assert "<th scope='row'>Liesa &lt;b&gt; <span class='badge'>your deck</span></th>" in page
    assert (
        "<td class='num' data-label='95% interval'>15% to 85%</td>" in page
        and "41\u00a0s" in page
        and "95% interval 15% to 85%" in page
    )
    assert "<li><span>Won by a player</span><b>3</b></li>" in page
    assert "<li><span>Stopped at the time limit (counted as a draw)</span><b>1</b></li>" in page
    assert "Fake Sticker Card" in page and "Seed: 7 (kept for the record)." in page
    assert "Average game time" in page and "Median game time" not in page  # stored before 0.7.30
    running = forge_html({"state": "running", "not_played": []})
    assert "running" in running and "data-forge-live" not in running  # an export: no refresh
    live = forge_html({"state": "running", "not_played": []}, live=True)
    assert "data-forge-live" in live and "updates by itself" in live
    assert "data-forge-live" not in forge_html(done, live=True)  # a finished run is not refreshed
    assert "not run" in forge_html({"state": "failed", "error": "Forge does not have this deck's commander"})
    # A run that stopped early reports the games it did play, never "not run"
    part = {
        **done,
        "state": "failed",
        "games_requested": 10,
        "result": {**done["result"], "games": 5, "games_requested": 10},
    }
    page = forge_html(part)
    assert "stopped early" in page and "badge danger" not in page and "could not run" not in page
    assert (
        "Forge stopped after 5 of 10 games, because the rest of the games failed. The results below count "
        "only the 5 games played." in page
    )
    assert "<table class='qty seats stack'>" in page
    for state, why in (("timeout", "reached its time limit"), ("cancelled", "was stopped")):
        assert why in forge_html({**part, "state": state})
    # every game played, a lost run, one game, no total recorded
    full = {**part, "games_requested": 5, "result": {**part["result"], "games_requested": 5}}
    page = forge_html(full)
    assert "Forge played all 5 games, but the run did not end cleanly. The results below count all 5." in page
    assert "stopped early" not in page and "not run" not in page
    # one game asked for and played reads as one, not "all 1"
    one = {**part, "games_requested": 1, "result": {**part["result"], "games": 1, "games_requested": 1}}
    page = forge_html(one)
    assert (
        "Forge played the one game asked for, but the run did not end cleanly. The results below count it."
        in page
    )
    assert "all 1" not in page and "stopped early" not in page
    lost = forge_html({**part, "state": "lost", "error": "Forge stopped answering about this run."})
    assert (
        "The gateway lost track of this run after 5 of 10 games. The results below count only those 5 "
        "games. Forge stopped answering about this run." in lost
    )
    one = {"state": "timeout", "result": {"games": 1}}
    assert (
        "Forge stopped after 1 game, because the run reached its time limit. The results below count "
        "only that one game." in forge_html(one)
    )
    assert "Games played: 1." in report_markdown(
        {"deck_name": "L", "deck_id": "4", "stats": {}, "forge": one}
    )
    md = report_markdown({"deck_name": "Liesa", "deck_id": "42", "stats": {}, "forge": part})
    assert "Forge stopped after 5 of 10 games" in md and "could not run" not in md
    from mtg_gateway.reports import report_status

    assert report_status(None, None, part) == "partial"
    # the Reports page, History and the assistant count its games as Forge games
    from mtg_gateway.reports import ReportService, _forge_played, run_settings
    from mtg_gateway.reports_page import in_chip

    row = {"id": "rep_x", "deck_id": "42", "deck_name": "Liesa", "taken_at": 1, "fingerprint": "f"}
    summary = ReportService._summary({**row, "forge_json": json.dumps(part)})
    assert summary["engine"] == "forge" and summary["settings"]["engines"] == ["forge"]
    assert in_chip({"forge_played": _forge_played(part)}, "forge")  # the "With Forge games" chip
    none = ReportService._summary(
        {**row, "forge_json": json.dumps({"state": "failed", "result": {"games": 0}})}
    )
    assert (
        none["engine"] == "preflight_only" and run_settings({}, None, {"state": "timeout"})["engines"] == []
    )
    assert report_status(None, None, {"state": "failed", "result": {"games": 0}}) == "failed"
    # A run still playing shows its count once, and no "won" figure worked out
    # from an older run's totals (the games not yet finished are not wins)
    live = forge_html({**done, "state": "running", "games_requested": 10, "result": {**done["result"]}})
    assert "Games played" in live and "Games finished so far" not in live
    assert "Won by a player" not in live
    assert "Games finished so far: 0 of 10." in forge_html({"state": "running", "games_requested": 10})
    assert forge_html(None) == ""
    md = report_markdown({"deck_name": "Liesa", "deck_id": "42", "stats": {}, "forge": done})
    assert "## Forge games (against precons)" in md and "Cards Forge could not play: Fake Sticker Card" in md
    assert "Liesa <b>: 2 wins, 50% (95% interval 15% to 85%)." in md and "1 of them stopped at the time" in md
    # a newer run: turns, losses and each game
    newer = {
        **done,
        "result": {
            **done["result"],
            "median_turn": 11,
            "turns_range": [8, 30],
            "median_game_seconds": 95.0,
            "ended": {"won": 3, "time_limit": 1},
            "game_list": [{"game": 1, "seconds": 95.0, "turn": 11, "winner": 1, "ended": "won"}],
        },
    }
    newer["result"]["seats"] = [
        {**done["result"]["seats"][0], "losses": {"commander damage": 1, "life": 1}},
        done["result"]["seats"][1],
    ]
    page = forge_html(newer)
    assert "turn 11" in page and "from turn 8 to 30" in page and "1\u00a0min 35\u00a0s" in page
    assert "<td data-label='How it lost'>commander damage 1, life total 1</td>" in page
    assert "<summary>Each game (1)</summary>" in page
    md = report_markdown({"deck_name": "Liesa", "deck_id": "42", "stats": {}, "forge": newer})
    assert (
        "Lost by: commander damage 1, life total 1." in md
        and "Median game length: turn 11 (from turn 8 to 30)." in md
    )


async def test_deleting_a_report_cancels_its_unfinished_forge_run(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    first = await reports.run("alice", "42", games=300, options={"seed": 1})
    reports.delete("alice", first["report_id"])
    await asyncio.gather(*reports._forge_cancels)
    assert fake.cancelled == [first["forge"]["job_id"]]
    # the member's next run starts: the deleted report no longer holds the one-run slot, and its
    # job no longer holds the engine
    second = await reports.run("alice", "42", games=300, options={"seed": 2})
    assert second["forge"]["state"] == "running" and len(fake.started) == 2
    fake.finish(second["forge"]["job_id"], [1])
    await asyncio.wait_for(reports._forge_task, timeout=20)  # type: ignore[arg-type]
    reports.delete("alice", second["report_id"])  # a finished run has nothing to cancel
    assert not reports._forge_cancels and len(fake.cancelled) == 1


async def test_a_run_whose_report_was_deleted_while_starting_is_cancelled(
    tmp_path: Path, monkeypatch: Any
) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    real_start = reports_module.start_run

    async def start_then_delete(*args: Any, **kwargs: Any) -> dict[str, Any]:
        section = await real_start(*args, **kwargs)
        with db.tx() as c:  # the member deletes the report while the engine answers
            c.execute("DELETE FROM reports")
        return section

    monkeypatch.setattr(reports_module, "start_run", start_then_delete)
    with pytest.raises(DeckError):  # the report it would return is gone
        await reports.run("alice", "42", games=300, options={"seed": 1})
    await asyncio.gather(*reports._forge_cancels)
    assert len(fake.cancelled) == 1


async def test_forge_health_is_probed_once_and_reports_an_outage() -> None:
    fake = FakeForge()
    client = fake.client()
    assert all(await asyncio.gather(*[client.healthy() for _ in range(10)]))
    assert fake.health_probes == 1
    fake.up = False
    client._health = (0.0, True)  # the cached answer has expired
    assert await client.healthy() is False and client._health[1] is False


async def test_pruning_old_reports_cancels_their_unfinished_forge_runs(
    tmp_path: Path, monkeypatch: Any
) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    first = await reports.run("alice", "42", games=300, options={"seed": 1})
    with db.tx() as c:  # make it the oldest report
        c.execute("UPDATE reports SET taken_at = 1 WHERE id = ?", (first["report_id"],))
    monkeypatch.setattr(reports_module, "MAX_REPORTS_PER_USER", 1)
    await reports.run("alice", "42", games=300, options={"seed": 2})  # prunes the first report
    await asyncio.gather(*reports._forge_cancels)
    assert fake.cancelled == [first["forge"]["job_id"]]


def test_report_page_shows_a_queued_run_and_its_place() -> None:
    from mtg_gateway.report_view import forge_html, report_markdown

    queued = {
        "state": "queued",
        "waiting": True,
        "games_requested": 10,
        "position": 1,
        "waiting_for": {"report_id": "rep_ahead", "deck_name": "Liesa", "job_id": "00ab"},
        "expected_seconds": 4712,
        "average_game_seconds_measured": 235.6,
    }
    live = forge_html(queued, live=True)
    assert ">Queued</span>" in live and "data-forge-live" in live
    assert "Waiting for your earlier Forge run to finish." in live
    assert "Waiting: number 1 in line, behind your report of Liesa. It starts by itself" in live
    assert "Expected to finish in about 79 minutes (at the measured 235.6 seconds a game)." in live
    assert "href='/history/reports/rep_ahead'" in live
    assert "href=" not in forge_html(queued)  # an export links nowhere
    md = report_markdown({"deck_name": "Aesi", "deck_id": "42", "stats": {}, "forge": queued})
    assert "Waiting: number 1 in line" in md
    seeded = forge_html({"state": "done", "seed": 12, "seed_source": "gateway", "result": {"games": 1}})
    assert "Seed: 12 (chosen by the gateway, kept for the record)." in seeded
    # Every report says the games can differ, the page and the export, whoever chose the seed;
    # none promises a replay
    for source, said in (
        ("gateway", "(chosen by the gateway, kept for the record)"),
        ("request", "(set when this report was asked for, kept for the record)"),
    ):
        forge = {"state": "done", "seed": 12, "seed_source": source, "result": {"games": 1}}
        md = report_markdown({"deck_name": "Aesi", "deck_id": "42", "stats": {}, "forge": forge})
        for text in (forge_html(forge), md):
            assert f"Seed: 12 {said}." in text and FORGE_DRIFT_NOTE in text
            assert "forge_seed" not in text and "repeat" not in text


async def test_report_keeps_a_text_result_once(tmp_path: Path) -> None:
    """A research tool that answers in text also sends it as {"result": text}; the report kept
    both (about 35 KB for one report). Now the text is kept once, and old reports read back without
    the copy."""
    import mcp_types as types

    text = "# Goldfish run\nCommander: Aesi\n\n## Metrics\n" + "x" * 5000

    class _Mf:
        async def call(self, tool: str, arguments: Any, **kwargs: Any) -> Any:
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=text)], structured_content={"result": text}
            )

    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    reports = ReportService(db, _Decks(), _Mf())  # type: ignore[arg-type]
    out = await reports.run("alice", "42", games=300, options={"seed": 1})
    assert out["goldfish"]["ok"] and out["goldfish"]["text"] == text and "data" not in out["goldfish"]
    assert "data" not in out["validation"]
    old = {"tool": "goldfish_run", "ok": True, "text": text, "data": {"result": text}}
    with db.tx() as c:
        c.execute("UPDATE reports SET goldfish_json = ? WHERE id = ?", (json.dumps(old), out["report_id"]))
    assert "data" not in reports.get("alice", out["report_id"])["goldfish"]


class _SixtyCardDecks:
    """Deck 42 as a 60-card Modern deck: no commander, no Commander category."""

    async def get_any_deck(self, sub: str | None, ref: str) -> Any:
        raw = json.loads(json.dumps(FakeArchidekt().decks[42]))
        raw.update(deckFormat=2, categories=[])
        for row in raw["cards"]:
            row["categories"] = None
        return parse_deck(raw)


async def test_compare_with_is_refused_whenever_no_forge_games_run(tmp_path: Path) -> None:
    """A comparison's other deck with no Forge games to
    play is refused, not ignored, and keeps no report: forge=False, no Forge on the gateway, and a
    60-card deck."""
    from mtg_gateway.decks import DeckError
    from mtg_gateway.reports import PAIR_NO_FORGE

    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    sixty = ReportService(db, _SixtyCardDecks(), None, forge=FakeForge().client(), forge_games=4)  # type: ignore[arg-type]
    cases = [
        (sixty, {}),  # a 60-card deck
        (ReportService(db, _Decks(), None, forge=None, forge_games=4), {}),  # type: ignore[arg-type]
        (ReportService(db, _Decks(), None, forge=FakeForge().client(), forge_games=4), {"forge": False}),  # type: ignore[arg-type]
    ]
    for service, kw in cases:
        with pytest.raises(DeckError) as refused:
            await service.run("alice", "42", simulate=False, pair_with="43", **kw)
        assert refused.value.kind == "invalid" and str(refused.value) == PAIR_NO_FORGE
    assert db._conn.execute("SELECT COUNT(*) FROM reports").fetchone()[0] == 0


def test_a_shared_comparison_run_names_the_higher_bracket_only_when_one_picked(tmp_path: Path) -> None:
    """The second report says its table was picked for the higher of the two
    brackets only when a bracket picked it; a comparison whose first run seated the newest precons
    (no bracket on either deck, or unrated precons) gets the plain share note."""
    import json

    from .test_history_page import store_report

    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    reports = ReportService(db, _Decks(), None, forge=FakeForge().client(), forge_games=4)  # type: ignore[arg-type]
    seats = ["Liesa", "New 3", "Mid 3", "Old 4"]
    for rid, choice, paired in (
        ("rep_b", {"rule": "bracket", "pair": True, "note": "x"}, True),
        ("rep_n", {"rule": "newest", "pair": True, "note": "x"}, False),
        ("rep_o", {"rule": "bracket", "note": "x"}, False),
    ):
        store_report(db, "alice", rid)
        section = {"state": "done", "seats": seats, "opponent_choice": choice}
        db._conn.execute("UPDATE reports SET forge_json = ? WHERE id = ?", (json.dumps(section), rid))
        now = reports._shared_now("alice", {"report_id": rid, "deck_name": "Deck A"})
        assert now["opponents"] == seats[1:] and bool(now.get("pair")) is paired, (rid, now)


async def test_a_sixty_card_report_never_starts_or_queues_forge(tmp_path: Path) -> None:
    """Consolidation bug 2: every 60-card report started a Forge run bound to fail, which could take
    a waiting place. Now it never reaches the engine, takes no place, and says why."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _SixtyCardDecks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    finished: list[str] = []
    reports.on_finished = lambda sub, rid: finished.append(rid)
    out = await reports.run("alice", "42", games=300, options={"seed": 1})
    assert fake.started == [] and out["forge"]["state"] == "skipped"
    assert out["forge"]["reason"] == "not_commander" and "60-card deck" in out["forge"]["note"]
    assert out["engine"] == "preflight_only" and out["engine_label"] == "Preflight only (no rules engine)"
    assert out["status"] == "complete" and out["settings"]["mode"] == "constructed"
    assert finished == [out["report_id"]]  # the report is finished at once
    # no place taken: a Commander report of the same member starts at once
    commander = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    ran = await commander.run("alice", "42", games=300, options={"seed": 2})
    assert ran["forge"]["state"] == "running" and len(fake.started) == 1
    assert ran["engine"] == "forge" and ran["status"] == "running"
    commander._forge_task.cancel()


async def test_a_report_can_leave_forge_out(tmp_path: Path) -> None:
    """Forge=False runs a quick report without Forge games (simulate is no longer the one switch
    for both); unset, Forge follows simulate as before. A later report that asks for Forge does
    not reuse one that left it out."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    quick = await reports.run("alice", "42", games=300, forge=False)
    assert fake.started == [] and quick["forge"]["reason"] == "not_requested"
    assert quick["engine_label"] == "Preflight only (no rules engine)" and quick["status"] == "complete"
    assert "forge" not in quick["settings"]["engines"]
    # the same unchanged deck with Forge asked for: a new report, not the quick one again
    full = await reports.run("alice", "42", games=300)
    assert full["report_id"] != quick["report_id"] and not full.get("reused")
    assert full["forge"]["state"] == "running" and len(fake.started) == 1
    # and a second quick one reuses that full report (it holds more, nothing less)
    again = await reports.run("alice", "42", games=300, forge=False)
    assert again.get("reused") and again["report_id"] == full["report_id"]
    # simulate=False without forge: Forge off as before
    none = await reports.run("alice", "42", games=300, simulate=False, options={"seed": 3})
    assert none["forge"]["reason"] == "not_requested" and len(fake.started) == 1
    reports._forge_task.cancel()


class _BusyForge(FakeForge):
    """The Forge service with its own queue full (FORGE_MAX_QUEUE): 429 until ``busy`` is cleared."""

    def __init__(self) -> None:
        super().__init__()
        self.busy = True
        self.refused = 0

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs" and request.method == "POST" and self.busy:
            self.refused += 1
            return httpx.Response(429, json={"error": "simulation queue is full; try again later"})
        return super().handler(request)


async def test_runs_waiting_on_a_busy_forge_ask_again_first_come_first_served(tmp_path: Path) -> None:
    """With Forge's queue full, each round asked again for each member's
    waiting run in no particular order, so a later run could take the room an earlier one waited
    for. The member whose run joined the queue first now asks first."""
    db = Database(tmp_path / "t.sqlite")
    for sub in ("zed", "amy"):
        db.upsert_user(sub, email=None, name=None, preferred_username=None, groups=[])
    fake = _BusyForge()
    asked: list[int] = []
    handler = fake.handler

    def recording(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/jobs" and request.method == "POST":
            asked.append(json.loads(request.content)["seed"])
        return handler(request)

    fake.handler = recording  # type: ignore[method-assign]
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 3600  # this test asks again itself
    zed = await reports.run("zed", "42", games=300, options={"seed": 1})
    amy = await reports.run("amy", "42", games=300, options={"seed": 2})
    reports._forge_task.cancel()
    for rid, joined in ((zed["report_id"], 2000), (amy["report_id"], 1000)):  # Amy's run joined first
        section = reports.get("zed" if rid == zed["report_id"] else "amy", rid)["forge"]
        assert section["state"] == "queued" and section["engine_busy"] is True
        db._conn.execute(
            "UPDATE reports SET forge_json = json_set(forge_json, '$.queued_at', ?) WHERE id = ?",
            (joined, rid),
        )
    asked.clear()
    await reports._start_waiting_runs()
    assert asked == [2, 1]


async def test_a_report_made_while_forge_is_busy_never_splits_a_comparison(tmp_path: Path) -> None:
    """A report made while the member had nothing unfinished went straight to Forge; with
    Forge's own queue full it became a waiting run without counting a Compare press's held places,
    so the press's second deck lost its Forge games. A run that would wait on a busy Forge now
    counts them first, and is skipped instead when the comparison needs the room."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = _BusyForge()
    fake.brackets = dict(BRACKETED)
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 3600  # no retry while the press runs

    def states() -> list[str]:
        rows = db._conn.execute("SELECT forge_json FROM reports ORDER BY rowid").fetchall()
        return [json.loads(r["forge_json"])["state"] for r in rows]

    hold, full = reports.reserve_pair("alice")  # the press holds its 2 places, then reads the decks
    assert hold is not None and full is None
    other = await reports.pair_deck("alice", "43")
    solo = await reports.run("alice", "44", simulate=False, forge=True, options={"seed": 7})
    assert solo["forge"]["state"] == "skipped", solo["forge"]
    assert solo["forge"]["error"] == reports_module.PAIR_SETTING_UP_SKIP
    first = await reports.run("alice", "42", simulate=False, forge=True, pair_with=other, pair_hold=hold)
    second = await reports.run(
        "alice",
        "43",
        simulate=False,
        forge=True,
        deck=other,
        same_opponents_as=first["report_id"],
        pair_hold=hold,
    )
    reports.release_pair("alice", hold)
    assert first["forge"]["state"] == "queued" and first["forge"]["engine_busy"] is True
    assert second["forge"]["state"] == "queued", second["forge"]  # both decks wait: no split
    assert states() == ["skipped", "queued", "queued"]
    # with no comparison holding places, a busy Forge still lets the report wait its turn
    db._conn.execute("DELETE FROM reports")
    alone = await reports.run("alice", "44", simulate=False, forge=True, options={"seed": 8})
    assert alone["forge"]["state"] == "queued" and alone["forge"]["engine_busy"] is True
    if reports._forge_task:
        reports._forge_task.cancel()


async def test_an_engine_busy_run_waits_and_is_retried(tmp_path: Path) -> None:
    """QA-002/QA-008: when the Forge service said its queue was full, the run ended failed. It now
    waits in the gateway's queue (engine_busy, retry_after: the refresher's interval) and the
    refresher starts it once the engine has room; after GIVE_UP_SECONDS it is given up."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = _BusyForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    out = await reports.run("alice", "42", games=300, options={"seed": 9})
    forge = out["forge"]
    assert forge["state"] == "queued" and forge["engine_busy"] is True and forge["retry_after"] == 1
    assert out["status"] == "queued" and out["forge_status"]["engine_busy"] is True
    assert "start" not in forge
    assert any("asks Forge again" in ln for ln in forge_lines(forge)), forge_lines(forge)
    # a second report of the member waits behind it, not beside it
    second = await reports.run("alice", "42", games=300, options={"seed": 10})
    assert second["forge"]["state"] == "queued" and second["forge"]["position"] == 2
    for _ in range(100):  # tried again on each round while the engine stays busy
        if fake.refused >= 3:
            break
        await asyncio.sleep(0.02)
    assert fake.refused >= 3 and fake.started == []
    fake.busy = False
    for _ in range(200):
        if fake.started:
            break
        await asyncio.sleep(0.02)
    assert len(fake.started) == 1 and fake.started[0]["seed"] == 9
    now = reports.get("alice", out["report_id"])
    assert now["forge"]["state"] == "running" and "engine_busy" not in now["forge"]
    reports._forge_task.cancel()
    # four hours of busy answers: given up, with the reason
    fake.busy = True
    since = int(time.time()) - reports_module.GIVE_UP_SECONDS - 10
    await reports._start_forge(
        second["report_id"], DECK_TEXT, "Liesa", 4, None, (None, None), busy_since=since
    )
    gave_up = reports.get("alice", second["report_id"])["forge"]
    assert gave_up["state"] == "failed" and "four hours" in gave_up["error"]


def test_forge_games_default_and_win_rate_range() -> None:
    """Four Forge games a report by default (more on request), each win rate shown with its 95% range."""
    from mtg_gateway.config import Settings

    assert Settings.__dataclass_fields__["forge_games"].default == 4
    lines = forge_lines(
        {
            "state": "done",
            "result": {
                "games": 10,
                "games_requested": 10,
                "seats": [{"deck": "Liesa", "wins": 4, "win_rate": 0.4, "win_rate_95": [0.17, 0.69]}],
            },
        }
    )
    assert "Liesa: 4 wins, 40% (95% interval 17% to 69%)." in lines


def test_start_run_has_no_opponents_parameter() -> None:
    import inspect

    assert "opponents" not in inspect.signature(start_run).parameters


def test_report_status_and_run_settings() -> None:
    """QA-010: one overall status per report; QA-009: each report's run settings."""
    from mtg_gateway.reports import report_status, run_settings

    ok = {"ok": True, "text": "x"}
    bad = {"ok": False, "text": "x"}
    assert report_status(ok, ok, None) == "complete"
    assert report_status(ok, ok, {"state": "running"}) == "running"
    assert report_status(ok, ok, {"state": "queued", "waiting": True}) == "queued"
    assert report_status(ok, ok, {"state": "done"}) == "complete"
    assert report_status(ok, ok, {"state": "failed"}) == "partial"
    assert report_status(ok, ok, {"state": "skipped", "error": "queue full"}) == "partial"
    assert report_status(ok, ok, {"state": "skipped", "reason": "not_commander"}) == "complete"
    assert report_status(bad, ok, None) == "partial"
    assert report_status(bad, bad, {"state": "lost"}) == "failed"
    assert report_status(None, None, None) == "complete"  # statistics only, nothing else asked
    text = (
        "# Goldfish run\nCommander: Aesi | 300 games, seed 42, through turn 10\n\n"
        "## Metrics\n```json\n{}\n```"
    )
    settings = run_settings(
        {"commanders": ["Aesi"], "_run": {"mode": "commander", "games": 300}},
        {"ok": True, "text": text},
        {"state": "done", "games_requested": 10},
    )
    assert settings == {
        "mode": "commander",
        "games": 300,
        "seed": 42,
        "until_turn": 10,
        "engines": ["goldfish", "forge"],
        "forge_games": 10,
    }
    # an old report kept no settings: they come from its result and statistics
    old = run_settings({"commanders": ["Aesi"], "card_count": 100}, {"ok": True, "text": text}, None)
    assert old["mode"] == "commander" and old["games"] == 300 and old["engines"] == ["goldfish"]


def test_a_forge_run_that_ended_without_games_is_labelled_preflight_only() -> None:
    """0.7.24 review: a failed, timed-out, cancelled or lost Forge run brought no rules-engine games."""
    import json

    from mtg_gateway.reports import PREFLIGHT_ONLY, ReportService

    row = {"id": "rep_x", "deck_id": "1", "deck_name": "D", "taken_at": "t", "fingerprint": "f"}
    for state, engine in (("failed", "preflight_only"), ("lost", "preflight_only"), ("done", "forge")):
        s = ReportService._summary({**row, "forge_json": json.dumps({"state": state})})
        assert s["engine"] == engine, state
        assert (s["engine_label"] == PREFLIGHT_ONLY) is (engine == "preflight_only")


def test_summary_has_game_lengths_endings_and_losses() -> None:
    """Per-seat losses by kind, the median game time and turn, how the games ended, and
    each game listed; an older Forge image without turns or reasons leaves those out."""
    out = summarise(
        {
            "results": [
                {"game": 1, "ms": 10000, "winner": 1, "turn": 9, "lost": {"2": "life total reached 0"}},
                {
                    "game": 2,
                    "ms": 30000,
                    "winner": 2,
                    "turn": 14,
                    "lost": {"1": "accumulation of 21 damage from generals"},
                },
                {"game": 3, "ms": 600000, "winner": None, "draw": True, "stopped_slow": True},
                {"game": 4, "ms": 20000, "winner": None, "unparsed": "something"},
                {
                    "game": 5,
                    "ms": 5000,
                    "winner": None,
                    "draw": True,
                    "turn": 30,
                    "lost": {"2": "milled 3 cards"},
                },
            ]
        },
        ["Mine", "Opp"],
    )
    assert out["median_game_seconds"] == 20.0 and out["average_game_seconds"] == 133.0
    assert out["median_turn"] == 14 and out["turns_range"] == [9, 30]
    assert out["ended"] == {"won": 2, "draw": 1, "time_limit": 1, "unread": 1}
    assert out["seats"][0]["losses"] == {"commander damage": 1}
    assert out["seats"][1]["losses"] == {"life": 1, "other": 1}
    assert [g["ended"] for g in out["game_list"]] == ["won", "won", "time_limit", "unread", "draw"]
    assert out["game_list"][0] == {"game": 1, "seconds": 10.0, "turn": 9, "winner": 1, "ended": "won"}
    old = summarise({"results": [{"game": 1, "ms": 1000, "winner": 1}]}, ["Mine", "Opp"])
    assert "median_turn" not in old and "losses" not in old["seats"][1]


class _Live:
    def __init__(self, **values: Any) -> None:
        self.values = values

    def get(self, key: str) -> Any:
        return self.values[key]


async def test_more_than_four_at_once_falls_back_to_four_on_an_older_forge_image() -> None:
    """Up to 16 games at once since 0.7.33; a Forge image before 2.0.15-mag4 refuses more than 4
    (and never had more than 4 places), so the run is asked again with 4 instead of failing."""
    fake = FakeForge()
    fake.parallel_cap = 4
    section = await start_run(fake.client(), DECK_TEXT, "Liesa", games=12, parallel=12)
    assert section["state"] == "running" and [s["parallel"] for s in fake.started] == [4]
    fake.parallel_cap = 16  # a 2.0.15-mag4 image takes all 12
    await start_run(fake.client(), DECK_TEXT, "Liesa", games=12, parallel=12)
    assert fake.started[-1]["parallel"] == 12
    # a refusal of 4 or fewer is not retried
    fake.parallel_cap = 2
    with pytest.raises(ForgeError, match="parallel"):
        await fake.client().start([{"precon": "Precon 1"}], 3, parallel=3)
    assert len(fake.started) == 2


async def test_forge_reads_its_settings_live_and_its_own_seed(tmp_path: Path) -> None:
    """Games per run and games at once come from the Settings page as they are now
    (decks.live); options.forge_seed seeds Forge alone and never reaches the goldfish."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    decks = _Decks()
    decks.live = _Live(forge_games=6, forge_parallel_games=2)  # type: ignore[attr-defined]
    reports = ReportService(db, decks, None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    out = await reports.run("alice", "42", games=300, options={"seed": 7, "forge_seed": 11})
    assert fake.started[0]["games"] == 6 and fake.started[0]["parallel"] == 2
    assert fake.started[0]["seed"] == 11 and out["forge"]["seed"] == 11
    assert out["forge"]["seed_source"] == "request"
    decks.live.values.update(forge_parallel_games=1)  # type: ignore[attr-defined]
    assert reports.forge_parallel == 1
    reports._forge_task.cancel()  # type: ignore[union-attr]
    # one game at a time sends no parallel at all (a Forge image before 0.7.30 never sees it)
    section = await start_run(fake.client(), DECK_TEXT, "Liesa", games=1, parallel=1)
    assert "parallel" not in fake.started[-1] and "parallel" not in section
    assert reports_module._goldfish({"seed": 7, "forge_seed": 11, "until_turn": 8}) == {
        "seed": 7,
        "until_turn": 8,
    }
    with pytest.raises(DeckError, match="forge_seed must be an integer"):
        reports_module.sim_options({"forge_seed": "x"})


class _PairDecks:
    """Deck 42 with bracket 3 set on Archidekt and deck 43 with bracket 2 (its own table would be
    the bracket 2 precons)."""

    async def get_any_deck(self, sub: str | None, ref: str) -> Any:
        raw = FakeArchidekt().decks[42]
        raw.update(id=int(ref), name=f"Deck {ref}", edhBracket=3 if ref == "42" else 2)
        return parse_deck(raw)


async def test_two_decks_meet_the_same_opponents(tmp_path: Path) -> None:
    """The second deck's run seats the first run's precons (picked for the first deck's
    bracket), waits behind it as any second run does, says whose opponents they are and links that
    report; a report of someone else, or one without Forge games, is refused."""
    from mtg_gateway.report_view import report_body_html

    db = Database(tmp_path / "t.sqlite")
    for sub in ("alice", "bob"):
        db.upsert_user(sub, email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge(brackets=BRACKETED)
    reports = ReportService(db, _PairDecks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    first = await reports.run("alice", "42", simulate=False, forge=True)
    assert [d["precon"] for d in fake.started[0]["decks"][1:]] == ["New 3", "Mid 3", "Oldest 3"]
    second = await reports.run(
        "alice", "43", simulate=False, forge=True, same_opponents_as=first["report_id"]
    )
    assert second["forge"]["state"] == "queued"
    assert second["forge"]["shared_with"] == {"report_id": first["report_id"], "deck_name": "Deck 42"}
    page = report_body_html(second)
    assert f"href='/history/reports/{first['report_id']}'>Open Deck 42's report" in page
    fake.finish(first["forge"]["job_id"], [1])
    for _ in range(200):
        if len(fake.started) == 2:
            break
        await asyncio.sleep(0.02)
    # deck 43's own bracket (2) would seat New 2, Older 2, New 3: it meets deck 42's table instead
    assert [d["precon"] for d in fake.started[1]["decks"][1:]] == ["New 3", "Mid 3", "Oldest 3"]
    now = reports.get("alice", second["report_id"])["forge"]
    choice = now["opponent_choice"]
    assert choice["rule"] == "shared" and choice["shared_with"]["report_id"] == first["report_id"]
    assert choice["opponent_brackets"] == {"New 3": 3, "Mid 3": 3, "Oldest 3": 3}
    assert any("The same opponents as Deck 42's Forge games" in ln for ln in forge_lines(now))
    fake.finish(now["job_id"], [2])
    await asyncio.wait_for(reports._forge_task, timeout=20)
    # a share is never answered with a reused report
    again = await reports.run("alice", "43", simulate=False, forge=True, same_opponents_as=first["report_id"])
    assert not again.get("reused") and again["report_id"] != second["report_id"]
    reports._forge_task.cancel()
    with pytest.raises(DeckError) as other:
        await reports.run("bob", "43", forge=True, same_opponents_as=first["report_id"])
    assert other.value.kind == "not_found"
    quick = await reports.run("alice", "44", simulate=False, forge=False)
    with pytest.raises(DeckError, match="no Forge games"):
        await reports.run("alice", "43", forge=True, same_opponents_as=quick["report_id"])
    with pytest.raises(DeckError, match="same_opponents_as"):
        await reports.run("alice", "43", forge=True, same_opponents_as=" ")


async def test_shared_opponents_that_left_the_engine_or_never_sat() -> None:
    """A share whose precons this Forge image no longer has fails and says so; one whose run never
    started picks for that run's bracket, as its own run would."""
    from mtg_gateway.forge import shared_opponents

    fake = FakeForge(brackets=BRACKETED)
    gone = {"report_id": "rep_a", "deck_name": "Deck A", "opponents": ["New 3", "Retired 9"]}
    section = await start_run(fake.client(), DECK_TEXT, "Liesa", games=1, shared=gone)
    assert section["state"] == "failed" and "Retired 9" in section["error"] and not fake.started
    precons = [{"name": n, "bracket": b} for n, b in BRACKETED.items()]
    names, why = shared_opponents(precons, {"deck_name": "Deck A", "bracket": [2, "archidekt"]})
    assert names == ["New 2", "Older 2", "New 3"]
    assert why["rule"] == "shared" and why["bracket"] == 2
    assert why["note"] == (
        "The same opponents Deck A's Forge games get, so both decks meet the same table. Bracket 2 (the "
        "bracket set on Deck A in Archidekt): only 2 bracket 2 precons are here; the rest come from the "
        "nearest bracket (3)."
    )
    # An estimate is never called the deck's bracket
    _, est = shared_opponents(precons, {"deck_name": "Deck A", "bracket": [2, "estimate"]})
    assert "Archidekt's estimate from cards for Deck A is bracket 2: only 2" in est["note"]
    assert "Deck A's bracket" not in est["note"]
    # With no bracket, or precons without brackets, the note says the newest precons play
    _, none = shared_opponents(precons, {"deck_name": "Deck A"})
    assert none["note"].endswith(
        "There is no bracket for Deck A (none set in Archidekt and no estimate), so the newest precons play."
    )
    unrated = [{"name": n} for n in BRACKETED]
    _, old = shared_opponents(unrated, {"deck_name": "Deck A", "bracket": [3, "estimate"]})
    assert (
        "Archidekt's estimate from cards for Deck A is bracket 3, but the precons in this gateway's version "
        "of Forge carry no bracket, so the newest precons play." in old["note"]
    )
    assert "picked" not in none["note"] + old["note"]
    # In the second deck's report, every note names the first deck, never "the deck"
    for note in (why["note"], est["note"], none["note"], old["note"]):
        assert "the deck" not in note.lower().replace("the deck's", ""), note
    # Every sentence starts with a capital, and a deck's name is never re-cased
    _, unnamed = shared_opponents(precons, {"deck_name": ""})
    assert "table. There is no bracket for the other deck (none set in Archidekt" in unnamed["note"]
    note = pick_opponents(precons, None, None, deck="eldrazi tron")[1]["note"]
    assert note.startswith("There is no bracket for eldrazi tron (")
    assert pick_opponents(precons, None, None)[1]["note"].startswith("The deck has no bracket")
    # a deck's own report still reads "the deck"
    assert pick_opponents(precons, 4, "estimate")[1]["note"].startswith(
        "Archidekt's estimate from cards is bracket 4"
    )
    # a comparison's notes, in the first report and the second
    from mtg_gateway.forge import PAIR_LEAD, PAIR_NO_BRACKET, pair_choice

    first = pair_choice(pick_opponents(precons, 3, "archidekt")[1], 3)
    assert first["pair"] and first["note"].startswith(f"{PAIR_LEAD} Opponents are bracket 3 precons")
    assert PAIR_LEAD == "Opponents for this comparison are picked for the higher of the two decks' brackets."
    assert pair_choice(pick_opponents(precons, None, None)[1], None)["note"] == PAIR_NO_BRACKET
    _, waiting = shared_opponents(precons, {"deck_name": "Deck A", "bracket": [None, None, None, True]})
    assert waiting["note"] == (
        "The same opponents Deck A's Forge games get, so both decks meet the same table. Neither deck has "
        "a bracket (none set in Archidekt and no estimate), so the newest precons play."
    )
    sat = {"deck_name": "Deck A", "opponents": ["New 3", "Mid 3", "Old 4"]}
    assert shared_opponents(precons, {**sat, "pair": True})[1]["note"] == (
        "The same opponents as Deck A's Forge games, picked for the higher of the two decks' brackets, so "
        "both decks met the same table."
    )
    assert shared_opponents(precons, sat)[1]["note"] == (
        "The same opponents as Deck A's Forge games, so both decks met the same table."
    )


async def test_a_comparisons_second_deck_plays_beside_the_first_when_forge_has_room(tmp_path: Path) -> None:
    """With places for two runs on the engine, a comparison's second deck
    starts at once beside its first instead of waiting for it; otherwise, and for any other report,
    one run at a time per member stays."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    deck = await _Decks().get_any_deck("alice", "42")

    async def pair(fake: FakeForge) -> tuple[ReportService, dict[str, Any], dict[str, Any]]:
        reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
        reports.forge_interval = 0.05
        first = await reports.run("alice", "42", games=300, options={"seed": 1}, pair_with=deck)
        second = await reports.run(
            "alice", "42", games=300, options={"seed": 2}, deck=deck, same_opponents_as=first["report_id"]
        )
        return reports, first, second

    async def settle(reports: ReportService, fake: FakeForge) -> None:
        for jid in list(fake.jobs):
            fake.finish(jid, [1])
        for _ in range(400):
            if reports._forge_task is None or reports._forge_task.done():
                break
            for jid in list(fake.jobs):
                if fake.jobs[jid]["state"] != "done":
                    fake.finish(jid, [1])
            await asyncio.sleep(0.02)
        assert reports._forge_task is None or reports._forge_task.done()

    roomy = FakeForge()
    roomy.max_parallel = 2
    reports, first, second = await pair(roomy)
    assert first["forge"]["max_parallel"] == 2
    assert second["forge"]["state"] == "running" and len(roomy.started) == 2
    assert roomy.started[1]["decks"][1:] == roomy.started[0]["decks"][1:]  # the same table
    # a third report waits until both have ended
    third = await reports.run("alice", "42", games=300, options={"seed": 3})
    assert third["forge"]["state"] == "queued" and len(roomy.started) == 2
    roomy.finish(first["forge"]["job_id"], [1])
    for _ in range(20):
        await asyncio.sleep(0.05)
    assert len(roomy.started) == 2  # the second deck still plays
    await settle(reports, roomy)
    assert len(roomy.started) == 3

    # one place on the engine: the second deck waits for the first, as before
    tight = FakeForge()
    tight.max_parallel = 1
    reports, first, second = await pair(tight)
    assert second["forge"]["state"] == "queued" and len(tight.started) == 1
    await settle(reports, tight)
    assert len(tight.started) == 2

    # an engine that does not say (an older image): as before
    old = FakeForge()
    reports, first, second = await pair(old)
    assert second["forge"]["state"] == "queued" and len(old.started) == 1
    await settle(reports, old)

    # same_opponents_as on a report made without compare_with is not a pair: it waits
    plain = FakeForge()
    plain.max_parallel = 2
    reports = ReportService(db, _Decks(), None, forge=plain.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    one = await reports.run("alice", "42", games=300, options={"seed": 4})
    two = await reports.run(
        "alice", "42", games=300, options={"seed": 5}, deck=deck, same_opponents_as=one["report_id"]
    )
    assert two["forge"]["state"] == "queued" and len(plain.started) == 1
    await settle(reports, plain)


async def test_each_forge_game_shows_as_it_finishes(tmp_path: Path, monkeypatch: Any) -> None:
    """Speed fixes 3 and 4: while the run plays, each finished game reaches the report
    within one refresh (every 3 s by default, was 15), on the page and in the poller's numbers,
    and a refresh that finds nothing new writes nothing."""
    from mtg_gateway.report_view import forge_html
    from mtg_gateway.reports import forge_status

    assert ReportService(Database(tmp_path / "d.sqlite"), _Decks(), None).forge_interval == 3.0  # type: ignore[arg-type]
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    saves: list[str] = []
    real_save = reports._save_forge
    monkeypatch.setattr(
        reports, "_save_forge", lambda rid, s, **kw: saves.append(rid) or real_save(rid, s, **kw)
    )
    out = await reports.run("alice", "42", games=300, options={"seed": 7})
    jid, rid = out["forge"]["job_id"], out["report_id"]
    await asyncio.sleep(0.3)
    quiet = len(saves)
    await asyncio.sleep(0.3)
    assert len(saves) == quiet  # nothing new: nothing written
    fake.jobs[jid]["results"] = [
        {"game": i + 1, "ms": 40000, "winner": w, "draw": False, "turn": 9} for i, w in enumerate([1, 3])
    ]
    for _ in range(100):
        if (reports.get("alice", rid)["forge"].get("result") or {}).get("games") == 2:
            break
        await asyncio.sleep(0.02)
    section = reports.get("alice", rid)["forge"]
    assert section["state"] == "running" and forge_status(section)["games_done"] == 2
    page = forge_html(section, live=True, report_id=rid)
    assert "<summary>Each game (2)</summary>" in page and "data-forge-live" in page
    fake.finish(jid, [1, 3, 1, 1])
    await asyncio.wait_for(reports._forge_task, timeout=20)
    assert reports.get("alice", rid)["forge"]["state"] == "done"


class _TwoCopies:
    """Two copies of the Forge service, each with its own jobs (forge_service.py keeps them in
    memory). The service name ("forge.test") is spread over both in turn, as Swarm's load balancing
    does; each copy's own address reaches only that copy."""

    def __init__(self, free: tuple[int, int] = (1, 1)) -> None:
        self.copies = {"10.0.9.1": FakeForge(), "10.0.9.2": FakeForge()}
        self.free = dict(zip(self.copies, free, strict=True))
        self.turn = 0
        self.silent: set[str] = set()  # copies that are listed but do not answer

    def handler(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host in self.silent or (host != "forge.test" and host not in self.copies):
            raise httpx.ConnectError("no answer", request=request)  # a copy gone, or hung
        if host not in self.copies:
            self.turn += 1
            host = list(self.copies)[self.turn % len(self.copies)]
        if request.url.path == "/health":
            return httpx.Response(
                200, json={"status": "ok", "busy": False, "queued": 0, "free_places": self.free[host]}
            )
        return self.copies[host].handler(request)

    def client(self, *, resolve: bool) -> ForgeClient:
        async def addresses(host: str, port: int) -> list[str]:
            assert (host, port) == ("forge.test", 8000)
            return list(self.copies)

        return ForgeClient(
            "http://forge.test:8000",
            transport=httpx.MockTransport(self.handler),
            resolver=addresses if resolve else None,
        )


def _section(db: Database, rid: str) -> dict[str, Any]:
    """A report's Forge section as stored (members see it without the copy it plays on)."""
    with db._lock:
        row = db._conn.execute("SELECT forge_json FROM reports WHERE id = ?", (rid,)).fetchone()
    return json.loads(row["forge_json"])


async def test_runs_stay_with_the_forge_copy_that_started_them(tmp_path: Path) -> None:
    """With two copies of the Forge service behind one name, a run
    started on one copy and asked about on the other was marked lost. Each run now keeps the copy
    it started on and is always asked there; with one address nothing changes."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    db.upsert_user("bob", email=None, name=None, preferred_username=None, groups=[])

    # before: one name, two copies, nothing pinned: the refresh lands on the other copy
    two = _TwoCopies()
    reports = ReportService(db, _Decks(), None, forge=two.client(resolve=False), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    out = await reports.run("alice", "42", games=300, options={"seed": 1})
    assert "engine" not in out["forge"]
    await asyncio.wait_for(reports._forge_task, timeout=20)
    lost = reports.get("alice", out["report_id"])["forge"]
    assert lost["state"] == "lost" and lost["error"] == "Forge restarted before this run finished."

    # after: each copy's address is known, the run is pinned and finishes
    two = _TwoCopies(free=(0, 1))
    reports = ReportService(db, _Decks(), None, forge=two.client(resolve=True), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    a = await reports.run("alice", "42", games=300, options={"seed": 2})
    stored = _section(db, a["report_id"])
    assert stored["engine"] == "http://10.0.9.2:8000" and stored["engine_copies"] == 2
    # the copy's internal address is not shown to the member
    assert not {"engine", "engine_copies"} & set(a["forge"])
    assert not {"engine", "engine_copies"} & set(reports.get("alice", a["report_id"])["forge_status"])
    two.free = {"10.0.9.1": 1, "10.0.9.2": 0}  # the second copy is now playing alice's run
    b = await reports.run("bob", "42", games=300, options={"seed": 3})
    assert _section(db, b["report_id"])["engine"] == "http://10.0.9.1:8000"  # bob's: the other machine
    assert [len(c.started) for c in two.copies.values()] == [1, 1]
    for copy in two.copies.values():
        for jid in copy.jobs:
            copy.finish(jid, [1, 2])
    await asyncio.wait_for(reports._forge_task, timeout=20)
    for sub, rep in (("alice", a), ("bob", b)):
        assert reports.get(sub, rep["report_id"])["forge"]["state"] == "done"
    # a stored engine is used only as an address on the service's own scheme and port
    assert reports.forge.at("http://evil.example:8000") is None
    assert reports.forge.at("http://10.0.9.1:9999") is None
    assert reports.forge.at("http://10.0.9.1:8000/x") is None
    assert reports.forge.at("http://10.0.9.1:8000") is not None

    # a compare across two copies of one place each: the second deck plays at once on the other
    two = _TwoCopies(free=(1, 1))
    reports = ReportService(db, _Decks(), None, forge=two.client(resolve=True), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    deck = await _Decks().get_any_deck("alice", "42")
    first = await reports.run("alice", "42", games=300, options={"seed": 4}, pair_with=deck)
    first_engine = _section(db, first["report_id"])["engine"]
    two.free[first_engine.split("//")[1].split(":")[0]] = 0
    second = await reports.run(
        "alice", "42", games=300, options={"seed": 5}, deck=deck, same_opponents_as=first["report_id"]
    )
    assert second["forge"]["state"] == "running"
    assert _section(db, second["report_id"])["engine"] != first_engine
    for copy in two.copies.values():
        for jid in copy.jobs:
            copy.finish(jid, [1])
    await asyncio.wait_for(reports._forge_task, timeout=20)


async def test_a_run_whose_forge_copy_restarted_elsewhere_is_marked_lost(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """A copy that restarts comes back on a new address, and its old one never
    answers. The run kept on it is marked lost at once, as a single copy's restart is, and the
    member's next run starts; a copy still listed but silent is given up after
    PINNED_SILENCE_SECONDS. Neither stays "running" for GIVE_UP_SECONDS."""
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    two = _TwoCopies(free=(0, 1))
    reports = ReportService(db, _Decks(), None, forge=two.client(resolve=True), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    first = await reports.run("alice", "42", games=300, options={"seed": 1})
    assert _section(db, first["report_id"])["engine"] == "http://10.0.9.2:8000"
    second = await reports.run("alice", "42", games=300, options={"seed": 2})
    assert second["forge"]["state"] == "queued"  # waits for alice's first run

    # the copy on 10.0.9.2 restarts as 10.0.9.3 (a new container, an empty job list)
    del two.copies["10.0.9.2"]
    two.copies["10.0.9.3"] = FakeForge()
    two.free = {"10.0.9.1": 1, "10.0.9.3": 1}
    for _ in range(200):
        if reports.get("alice", first["report_id"])["forge"]["state"] == "lost":
            break
        await asyncio.sleep(0.05)
    lost = reports.get("alice", first["report_id"])["forge"]
    assert lost["state"] == "lost" and lost["error"] == "Forge restarted before this run finished."
    for _ in range(200):  # the member's queue is free again: the waiting run starts
        if reports.get("alice", second["report_id"])["forge"]["state"] == "running":
            break
        await asyncio.sleep(0.05)
    assert reports.get("alice", second["report_id"])["forge"]["state"] == "running"

    # a copy still listed but not answering: given up after the silence limit, not four hours
    playing = _section(db, second["report_id"])["engine"].split("//")[1].split(":")[0]
    two.silent.add(playing)
    for _ in range(10):
        await asyncio.sleep(0.05)
    assert reports.get("alice", second["report_id"])["forge"]["state"] == "running"  # within the limit
    assert "unanswered_since" in _section(db, second["report_id"])
    assert "unanswered_since" not in reports.get("alice", second["report_id"])["forge"]
    monkeypatch.setattr(forge_module, "PINNED_SILENCE_SECONDS", 0.0)
    await asyncio.wait_for(reports._forge_task, timeout=20)
    silent = reports.get("alice", second["report_id"])["forge"]
    assert silent["state"] == "lost" and silent["error"] == "Forge stopped answering about this run."

    # the given-up job is cancelled on its copy once that copy answers again,
    # so a copy that comes back does not play the abandoned run to its end
    job_id = _section(db, second["report_id"])["job_id"]
    assert two.copies[playing].cancelled == [] and reports._forge_cancels  # still asking
    await asyncio.sleep(0.2)
    two.silent.discard(playing)
    await asyncio.wait_for(asyncio.gather(*reports._forge_cancels), timeout=20)
    assert two.copies[playing].cancelled == [job_id]
    assert reports.get("alice", second["report_id"])["forge"]["state"] == "lost"

    # one copy (nothing pinned): a refused connection stays a wait, as before
    one = await refresh(
        ForgeClient("http://10.0.9.2:8000", transport=httpx.MockTransport(two.handler)),
        {"state": "running", "job_id": "j", "started_at": int(time.time())},
    )
    assert one["state"] == "running" and "unanswered_since" not in one


async def test_cancelling_on_a_silent_copy_stops_asking_after_a_while(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """The cancel of a given-up run is asked again only while its copy stays
    silent, up to SILENT_CANCEL_SECONDS; a job the copy no longer knows ends it at once."""
    db = Database(tmp_path / "t.sqlite")
    two = _TwoCopies()
    reports = ReportService(db, _Decks(), None, forge=two.client(resolve=True), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.02
    monkeypatch.setattr(forge_module, "SILENT_CANCEL_SECONDS", 0.2)
    two.silent.add("10.0.9.1")
    section = {"state": "running", "job_id": "j1", "engine": "http://10.0.9.1:8000"}
    reports._cancel_forge(section, keep_asking=True)
    await asyncio.wait_for(asyncio.gather(*reports._forge_cancels), timeout=5)  # gave up
    two.silent.clear()
    assert two.copies["10.0.9.1"].cancelled == []
    reports._cancel_forge({**section, "job_id": "unknown"}, keep_asking=True)
    await asyncio.wait_for(asyncio.gather(*reports._forge_cancels), timeout=0.15)  # not found: done


async def test_one_forge_address_changes_nothing(tmp_path: Path, monkeypatch: Any) -> None:
    """A single copy (one address, an address given directly, "localhost", or a name that can't be
    looked up in time): no copy is picked or stored, every call goes to the one URL."""
    monkeypatch.setattr(forge_module, "LOOKUP_DEADLINE_SECONDS", 0.2)

    async def one(host: str, port: int) -> list[str]:
        return ["10.0.9.7"]

    async def loopback(host: str, port: int) -> list[str]:
        return ["127.0.0.1", "::1"]

    async def broken(host: str, port: int) -> list[str]:
        raise OSError("no such host")

    async def slow(host: str, port: int) -> list[str]:
        await asyncio.sleep(30)
        return ["10.0.9.7", "10.0.9.8"]

    fake = FakeForge()
    for url, resolver in (
        ("http://forge.test:8000", one),
        ("http://localhost:8000", loopback),
        ("http://forge.test:8000", broken),
        ("http://forge.test:8000", slow),  # no answer within the lookup's time limit
        ("http://10.0.9.7:8000", one),
    ):
        client = ForgeClient(url, transport=httpx.MockTransport(fake.handler), resolver=resolver)
        assert await client.copies() == []
        picked, engine, copies = await client.pick()
        assert picked is client and engine is None and copies == 1


def test_solo_game_seconds_share_five_minutes() -> None:
    assert solo_game_seconds(5, 1, 240) == 54  # 270 s over 5 rounds
    assert solo_game_seconds(4, 4, 240) == 240  # one round: the service's own limit
    assert solo_game_seconds(50, 1, 240) == 30  # never less than 30 s
    assert solo_game_seconds(50, 1, 20) == 20  # nor more than the service allows


async def test_a_report_plays_the_forge_solo_test_first(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    fake.solo = True
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=5)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    out = await reports.run("alice", "42", games=300, options={"seed": 7})
    solo_job, precon_job = fake.started
    assert solo_job["solo"] == {"opponents": 3, "until_turn": 10, "game_seconds": 54}
    assert len(solo_job["decks"]) == 1 and solo_job["games"] == 5 and solo_job["seed"] == 7
    assert "solo" not in precon_job and len(precon_job["decks"]) == 4
    assert out["forge_solo"]["state"] == "running" and "deck_text" not in out["forge_solo"]
    assert reports.newest_forge_solo("alice", "42") is None
    # a second report while this one's solo test plays gets none
    again = await reports.run("alice", "42", games=300, options={"seed": 8})
    assert again["forge_solo"] == {"state": "skipped", "error": SOLO_ONE_AT_A_TIME, "finished_at": ANY_TIME}
    fake.jobs[out["forge_solo"]["job_id"]].update(
        state="done", results=[{"game": 1, "winner": 1, "solo": _solo_view(6)}]
    )
    for _ in range(200):
        if reports.get("alice", out["report_id"])["forge_solo"]["state"] == "done":
            break
        await asyncio.sleep(0.02)
    solo = reports.get("alice", out["report_id"])["forge_solo"]
    assert solo["state"] == "done" and solo["result"]["median_won_turn"] == 6
    newest = reports.newest_forge_solo("alice", "42")
    assert newest == {
        "report_id": out["report_id"],
        "finished_at": solo["finished_at"],
        "won_rate": 1.0,
        "games": 1,
        "median_won_turn": 6,
    }
    # deleting a report stops its unfinished runs, the solo test's included
    third = await reports.run("alice", "42", games=300, options={"seed": 9})
    reports.delete("alice", third["report_id"])
    await asyncio.sleep(0.05)
    assert third["forge_solo"]["job_id"] in fake.cancelled
    reports._forge_task.cancel()


async def test_an_older_forge_gives_no_solo_test(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.sqlite")
    db.upsert_user("alice", email=None, name=None, preferred_username=None, groups=[])
    fake = FakeForge()
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    out = await reports.run("alice", "42", games=300, options={"seed": 7})
    assert out["forge_solo"]["state"] == "skipped" and out["forge_solo"]["error"] == SOLO_TOO_OLD
    assert all("solo" not in job for job in fake.started)
    # a run without Forge, or without a simulation, has no solo test at all
    quick = await reports.run("alice", "42", games=300, options={"seed": 8}, forge=False)
    assert quick["forge_solo"] is None
    reports._forge_task.cancel()


class _AnyTime:
    def __eq__(self, other: object) -> bool:
        return isinstance(other, int) and other > 0


ANY_TIME = _AnyTime()


def test_forge_solo_lines_read_plainly() -> None:
    result = summarise_solo(
        [
            {"game": 1, "winner": 1, "solo": _solo_view(5, turns=5)},
            {"game": 2, "draw": True, "stopped_slow": True, "solo": _solo_view(None, turns=4)},
        ],
        2,
        {"opponents": 3, "until_turn": 10},
    )
    lines = forge_solo_lines({"state": "done", "result": result, "not_played": ["Sticker Card"]})
    assert lines[0] == "Games: 2, each counted up to your turn 10"
    assert lines[1].startswith("Won by turn 10: 1 of 2 (50%, 95% interval ")
    assert "Median winning turn: 5 (turns 5 to 5)" in lines
    assert "Mulligans: 0 of 2 games" in lines
    assert "1 of 2 games reached Forge's time limit first, so they count as not won." in lines
    assert lines[-1] == "Cards Forge could not play: Sticker Card"
    assert forge_solo_lines({"state": "running"}) == ["Games are going now."]
