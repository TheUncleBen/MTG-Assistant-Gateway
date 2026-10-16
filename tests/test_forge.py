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

from mtg_gateway import reports as reports_module
from mtg_gateway.archidekt import parse_deck
from mtg_gateway.db import Database
from mtg_gateway.decks import DeckError
from mtg_gateway.forge import ForgeClient, forge_deck, pick_opponents, refresh, start_run, summarise, wilson
from mtg_gateway.report_view import forge_lines
from mtg_gateway.reports import ReportService

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

    def handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/health":
            self.health_probes += 1
            if not self.up:
                raise httpx.ConnectError("down")
            return httpx.Response(200, json={"status": "ok", "busy": False, "queued": 0})
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
    """Owner's checklist T-152 (F39): a report made while the member's Forge run is still playing
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
    assert fourth["forge"]["state"] == "skipped" and "2 reports waiting" in fourth["forge"]["error"]
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
    """F40: without options.seed Forge picked its own seed and the report said null. The gateway
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
    # G9: laid out as a table, a row per deck at the table (a run stored before 0.7.30 as well)
    assert "Forge games" in page and "<table class='qty seats stack'>" in page
    assert "<th scope='row'>Liesa &lt;b&gt; <span class='badge'>your deck</span></th>" in page
    assert "<td class='num' data-label='95% interval'>15% to 85%</td>" in page and "41 s" in page
    assert "<li><span>Won by a player</span><b>3</b></li>" in page
    assert "<li><span>Stopped at the time limit (counted as a draw)</span><b>1</b></li>" in page
    assert "Fake Sticker Card" in page and "Seed: 7." in page
    assert "Average game time" in page and "Median game time" not in page  # stored before 0.7.30
    running = forge_html({"state": "running", "not_played": []})
    assert "running" in running and "data-forge-live" not in running  # an export: no refresh
    live = forge_html({"state": "running", "not_played": []}, live=True)
    assert "data-forge-live" in live and "updates by itself" in live
    assert "data-forge-live" not in forge_html(done, live=True)  # a finished run is not refreshed
    assert "not run" in forge_html({"state": "failed", "error": "Forge does not have this deck's commander"})
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
    assert "turn 11" in page and "from turn 8 to 30" in page and "1 min 35 s" in page
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
    assert "queued" in live and "data-forge-live" in live
    assert "Waiting for your earlier Forge run to finish." in live
    assert "Number 1 in your queue, behind your report of Liesa (rep_ahead)" in live
    assert "Expected to finish in about 79 minutes (at the measured 235.6 seconds a game)." in live
    assert "href='/history/reports/rep_ahead'" in live
    assert "href=" not in forge_html(queued)  # an export links nowhere
    md = report_markdown({"deck_name": "Aesi", "deck_id": "42", "stats": {}, "forge": queued})
    assert "Number 1 in your queue" in md
    seeded = forge_html({"state": "done", "seed": 12, "seed_source": "gateway", "result": {"games": 1}})
    assert "Seed: 12 (chosen by the gateway and kept with the report)." in seeded
    assert "forge_seed" not in seeded  # the page promises no replay (C-1)
    seeded_md = report_markdown(
        {
            "deck_name": "Aesi",
            "deck_id": "42",
            "stats": {},
            "forge": {"state": "done", "seed": 12, "seed_source": "gateway"},
        }
    )
    assert "later games can differ, so compare runs by their win rates and intervals" in seeded_md


async def test_report_keeps_a_text_result_once(tmp_path: Path) -> None:
    """B7: a research tool that answers in text also sends it as {"result": text}; the report kept
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
    """D17: forge=False runs a quick report without Forge games (simulate is no longer the one switch
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
    assert any("asks the engine again" in ln for ln in forge_lines(forge)), forge_lines(forge)
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
    """D15: ten Forge games a report by default, each win rate shown with its 95% range."""
    from mtg_gateway.config import Settings

    assert Settings.__dataclass_fields__["forge_games"].default == 10
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
    """G9/A33: per-seat losses by kind, the median game time and turn, how the games ended, and
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


async def test_forge_reads_its_settings_live_and_its_own_seed(tmp_path: Path) -> None:
    """O11: games per run and games at once come from the Settings page as they are now
    (decks.live); A34: options.forge_seed seeds Forge alone and never reaches the goldfish."""
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
    """K6 (Q136): the second deck's run seats the first run's precons (picked for the first deck's
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
    assert why["rule"] == "shared" and why["bracket"] == 2 and "picked for Deck A's bracket" in why["note"]
