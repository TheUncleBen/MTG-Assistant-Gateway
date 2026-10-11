"""Forge runs from the gateway: decklist conversion, win-rate summaries, the background refresh of a
report's run, and cards Forge lacks named rather than dropped."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from mtg_gateway import reports as reports_module
from mtg_gateway.archidekt import parse_deck
from mtg_gateway.db import Database
from mtg_gateway.decks import DeckError
from mtg_gateway.forge import ForgeClient, forge_deck, refresh, start_run, summarise, wilson
from mtg_gateway.reports import ReportService

from .fake_archidekt import FakeArchidekt


class FakeForge:
    """The Forge service's HTTP API, enough for the gateway: /check, /precons, /jobs."""

    def __init__(self, *, missing: set[str] | None = None) -> None:
        self.missing = missing or set()
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
    assert "Forge games" in page and "Liesa &lt;b&gt;: 2 wins, 50%" in page
    assert "Fake Sticker Card" in page and "stopped at the time limit" in page and "Seed: 7." in page
    running = forge_html({"state": "running", "not_played": []})
    assert "running" in running and "data-forge-live" not in running  # an export: no refresh
    live = forge_html({"state": "running", "not_played": []}, live=True)
    assert "data-forge-live" in live and "updates by itself" in live
    assert "data-forge-live" not in forge_html(done, live=True)  # a finished run is not refreshed
    assert "not run" in forge_html({"state": "failed", "error": "Forge does not have this deck's commander"})
    assert forge_html(None) == ""
    md = report_markdown({"deck_name": "Liesa", "deck_id": "42", "stats": {}, "forge": done})
    assert "## Forge games" in md and "Cards Forge could not play: Fake Sticker Card" in md


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
    assert "Seed: 12 (chosen by the gateway; pass it as options.seed to replay these games)." in seeded


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
