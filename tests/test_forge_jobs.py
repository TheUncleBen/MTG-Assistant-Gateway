"""Forge runs are background jobs (owner decision 11-01): on a small machine such as a Raspberry Pi 4,
a test against precons can take ten minutes, so no page, app call or assistant tool waits on Forge.
Every run is settled from the gateway's own tables and answered at once with its report id and the
page that shows its progress; Forge is asked in the background. The progress (the step, the games
played of those asked for, the time so far) shows on the report page, the deck page, Home and in
get_deck_report; a gateway restart before a run reached Forge starts it again; the member can stop a
test; and a Forge that can't be reached, a timeout or a failure are said in plain words."""

from __future__ import annotations

import asyncio
import gc
import html
import json
import warnings
from pathlib import Path
from typing import Any

import httpx
import pytest

from mtg_gateway import forge as forge_module
from mtg_gateway import report_page
from mtg_gateway import reports as reports_module
from mtg_gateway.db import Database
from mtg_gateway.decks import DeckError
from mtg_gateway.forge import ForgeClient
from mtg_gateway.reports import ReportService

from .conftest import AUTO_PROXY, Stack, stack_with
from .test_companion import NAV, linked_browser
from .test_decks_and_proxy import call, mcp_token, structured
from .test_forge import BRACKETED, FakeForge, _Decks

stack_options = stack_with(AUTO_PROXY)


class SlowForge(FakeForge):
    """Forge on a slow machine: every call waits until ``gate`` opens, so anything that waited on
    Forge would never answer while it is shut."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.gate = asyncio.Event()
        self.asked = 0

    async def slow(self, request: httpx.Request) -> httpx.Response:
        self.asked += 1
        await self.gate.wait()
        return self.handler(request)

    def client(self) -> ForgeClient:
        return ForgeClient("http://forge.test", transport=httpx.MockTransport(self.slow))


class DownForge(FakeForge):
    """Forge that can't be reached at all (stopped, restarting, or still loading its cards)."""

    def handler(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")


def _service(
    tmp_path: Path, fake: FakeForge, *, db: Database | None = None
) -> tuple[Database, ReportService]:
    if db is None:
        db = Database(tmp_path / "t.sqlite")
        for sub in ("alice", "bob"):
            db.upsert_user(sub, email=None, name=None, preferred_username=None, groups=[])
    reports = ReportService(db, _Decks(), None, forge=fake.client(), forge_games=4)  # type: ignore[arg-type]
    reports.forge_interval = 0.05
    return db, reports


async def _until(check: Any) -> None:
    for _ in range(400):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("never happened")


async def _answered(fake: SlowForge, work: Any) -> Any:
    """``work``'s answer while Forge is shut; one that waits on Forge fails the test (and Forge is
    opened, so nothing is left waiting behind it)."""
    try:
        return await asyncio.wait_for(work, 2)
    except TimeoutError:
        fake.gate.set()
        raise AssertionError("the answer waited on Forge") from None


async def _handed(reports: ReportService, sub: str, rid: str) -> dict[str, Any]:
    """Report rid once its games against precons reached Forge (an engine job, or why not)."""
    await _until(
        lambda: (
            reports.get(sub, rid)["forge"].get("job_id")
            or reports.get(sub, rid)["forge"]["state"] in ("failed", "skipped")
        )
    )
    return reports.get(sub, rid)


def _stop_service(reports: ReportService) -> None:
    if reports._forge_task is not None:
        reports._forge_task.cancel()


# -- nothing waits on Forge ------------------------------------------------------------------
async def test_a_report_answers_at_once_while_forge_is_slow(tmp_path: Path) -> None:
    fake = SlowForge()
    fake.solo = True
    _, reports = _service(tmp_path, fake)
    # answered with Forge still shut: the report id, both Forge parts on their way, and the step
    out = await _answered(fake, reports.run("alice", "42", games=300, options={"seed": 1}))
    assert out["report_id"].startswith("rep_") and fake.started == []
    assert out["forge"]["state"] == "queued" and out["forge"]["starting"] is True
    assert out["forge_solo"]["state"] == "queued" and out["status"] == "queued"
    assert out["progress"]["phase"] == "starting"
    assert out["progress"]["text"] == "Handing the games to Forge."
    assert out["progress"]["elapsed_text"] == "under a minute so far"
    fake.gate.set()
    await asyncio.wait_for(reports.starts_settled(), 5)
    now = reports.get("alice", out["report_id"])
    assert now["forge"]["state"] == "running" and now["forge"]["job_id"] and len(fake.started) == 2
    assert now["forge_solo"]["state"] == "running"
    assert now["progress"] == {
        "phase": "playing",
        "text": "Forge has played 0 of 4 games.",
        "games_done": 0,
        "games_total": 4,
        "elapsed_seconds": now["progress"]["elapsed_seconds"],
        "elapsed_text": "under a minute so far",
    }
    _stop_service(reports)


async def test_play_more_games_answers_at_once_while_forge_is_slow(tmp_path: Path) -> None:
    fake = SlowForge()
    fake.gate.set()
    _, reports = _service(tmp_path, fake)
    out = await reports.run("alice", "42", games=300, options={"seed": 1})
    rid = out["report_id"]
    await _handed(reports, "alice", rid)
    fake.finish(reports.get("alice", rid)["forge"]["job_id"], [1, 2, 1, 1])
    await _until(lambda: reports.get("alice", rid)["forge"]["state"] == "done")
    fake.gate.clear()  # Forge is slow again
    more = await _answered(fake, reports.play_more("alice", rid))
    # the earlier games stay on show while the extra ones are handed over
    assert more["forge"]["state"] == "queued" and more["forge"]["result"]["games"] == 4
    assert more["progress"]["phase"] == "starting" and len(fake.started) == 1
    fake.gate.set()
    await asyncio.wait_for(reports.starts_settled(), 5)
    playing = reports.get("alice", rid)
    assert playing["forge"]["state"] == "running" and len(fake.started) == 2
    assert playing["progress"]["text"] == "Forge has played 4 of 8 games."
    _stop_service(reports)


async def test_the_tools_and_the_api_answer_at_once_while_forge_is_slow(stack: Stack) -> None:
    h = stack.h
    gw = h.app.state.gateway
    fake = SlowForge(brackets=BRACKETED)
    gw.reports.forge, gw.reports.forge_interval = fake.client(), 0.05
    gw.reports.min_interval = 0  # every press a new test, never the one made a moment ago
    token = await mcp_token(h)
    try:
        ask = {"deck_ref": "42", "forge": True, "simulate": False}
        out = structured(await _answered(fake, call(h, token, "run_deck_report", ask)))
        assert out["ok"] and fake.started == [], out
        rid = out["report_id"]
        assert out["report_url"].endswith(f"/history/reports/{rid}")
        assert out["forge_status"]["state"] == "queued" and out["progress"]["phase"] == "starting"
        # the follow-up call: where the test stands
        view = structured(
            await _answered(fake, call(h, token, "get_deck_report", {"report_id": rid, "view": "forge"}))
        )
        assert (
            view["progress"]["text"] == "Handing the games to Forge."
            and view["report_url"] == out["report_url"]
        )
        # the app's API: the same, at once
        auth = {"Authorization": f"Bearer {token}"}
        api = await _answered(
            fake,
            h.http.post(
                "/api/v1/reports", json={"deck_id": "42", "simulate": False, "forge": True}, headers=auth
            ),
        )
        assert api.status_code == 201, api.text
        assert api.json()["forge"]["state"] == "queued"  # waits behind the first test
        running = await h.http.get("/api/v1/reports/running", headers=auth)
        steps = [r["step"]["phase"] for r in running.json()["running"]]
        assert sorted(steps) == ["starting", "waiting"], running.json()
        fake.gate.set()
        await asyncio.wait_for(gw.reports.starts_settled(), 5)
        view = structured(await call(h, token, "get_deck_report", {"report_id": rid, "view": "forge"}))
        assert view["progress"]["phase"] == "playing" and view["progress"]["games_total"] == 5
        assert view["forge_status"]["job_id"]
    finally:
        fake.gate.set()


# -- a gateway restart -----------------------------------------------------------------------
async def test_a_restart_while_a_run_is_handed_over_starts_it_again(tmp_path: Path) -> None:
    fake = SlowForge()
    fake.solo = True
    db, reports = _service(tmp_path, fake)
    out = await _answered(fake, reports.run("alice", "42", games=300, options={"seed": 1}))
    rid = out["report_id"]
    # the gateway stops while Forge is still being asked: its background work goes with it
    tasks = list(reports._launching.values())
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    _stop_service(reports)
    row = db._one("SELECT forge_json, forge_solo_json FROM reports WHERE id = ?", (rid,))
    assert json.loads(row["forge_json"])["starting"] is True and not json.loads(row["forge_json"]).get(
        "job_id"
    )
    assert fake.started == []
    # the next start: both parts wait their turn again and start by themselves
    fake.gate.set()
    _, again = _service(tmp_path, fake, db=db)
    again.resume_forge_runs()
    await _until(
        lambda: (
            again.get("alice", rid)["forge"]["state"] == "running"
            and again.get("alice", rid)["forge_solo"]["state"] == "running"
        )
    )
    assert len([j for j in fake.started if "solo" not in j]) == 1 and len(fake.started) == 2
    _stop_service(again)


async def test_a_start_stopped_before_it_began_leaves_no_unfinished_work_behind(tmp_path: Path) -> None:
    """The background start of a report's Forge parts stopped in the same moment it was made (the
    gateway shutting down as a report was made): nothing is left half-made. The start's own work
    is made when the start runs, so a stop before then has nothing to drop (CI saw "coroutine
    '_start_forge_parts' was never awaited" from such a stop), and the report is no longer marked
    as being handed over."""
    fake = SlowForge()
    _, reports = _service(tmp_path, fake)
    with warnings.catch_warnings(record=True) as seen:
        warnings.simplefilter("always")
        out = await reports.run("alice", "42", games=300, options={"seed": 1})
        rid = out["report_id"]
        tasks = list(reports._launching.values())
        assert tasks, "the Forge start is handed to the background"
        for t in tasks:
            t.cancel()  # before the start ran once: the gateway stopped in the same moment
        await asyncio.gather(*tasks, return_exceptions=True)
        assert rid not in reports._launching
        del out, tasks
        gc.collect()
    dropped = [w for w in seen if issubclass(w.category, RuntimeWarning)]
    assert dropped == [], [str(w.message) for w in dropped]
    assert fake.started == []
    _stop_service(reports)


# -- the member's Stop -----------------------------------------------------------------------
async def test_stop_ends_a_playing_run_and_keeps_its_games(tmp_path: Path) -> None:
    fake = FakeForge()
    db, reports = _service(tmp_path, fake)
    told: list[str] = []
    reports.on_finished = lambda sub, rid: told.append(rid)
    out = await reports.run("alice", "42", games=300, options={"seed": 1})
    rid = out["report_id"]
    await _handed(reports, "alice", rid)
    jid = reports.get("alice", rid)["forge"]["job_id"]
    fake.jobs[jid]["results"] = [{"game": 1, "ms": 60000, "winner": 1, "draw": False}]
    await _until(lambda: reports.get("alice", rid)["forge"]["games_done"] == 1)
    with pytest.raises(DeckError):
        reports.stop("bob", rid)  # not his
    stopped = reports.stop("alice", rid)
    await asyncio.gather(*reports._forge_cancels)
    assert fake.cancelled == [jid]
    f = stopped["forge"]
    assert (
        f["state"] == "cancelled"
        and f["stopped_by"] == "member"
        and f["error"] == reports_module.STOPPED_BY_MEMBER
    )
    assert f["result"]["games"] == 1 and stopped["progress"] is None and stopped["status"] == "partial"
    assert told == []  # the member stopped it: no "report finished" alert
    audit = db._one("SELECT detail_json FROM audit_log WHERE event = 'report_stopped'", ())
    assert json.loads(audit["detail_json"]) == {"report_id": rid}
    # the page says it in their words, with the games kept
    page = report_page.report_page_html(stopped, csrf="t")
    assert "Forge stopped after 1 of 4 games, because you stopped the test." in page
    assert "id='rv-progress'" in page and "rv-progress' data-report-id" in page and "hidden>" in page
    # stopping again changes nothing
    assert reports.stop("alice", rid)["forge"] == f
    _stop_service(reports)


async def test_stop_ends_a_waiting_run_and_takes_back_waiting_extra_games(tmp_path: Path) -> None:
    fake = FakeForge()
    _, reports = _service(tmp_path, fake)
    first = await reports.run("alice", "42", games=300, options={"seed": 1})
    await _handed(reports, "alice", first["report_id"])
    jid = reports.get("alice", first["report_id"])["forge"]["job_id"]
    fake.finish(jid, [1, 2, 1, 1])
    await _until(lambda: reports.get("alice", first["report_id"])["forge"]["state"] == "done")
    playing = await reports.run("alice", "42", games=300, options={"seed": 2})
    await _handed(reports, "alice", playing["report_id"])
    waiting = await reports.run("alice", "42", games=300, options={"seed": 3})
    assert waiting["forge"]["waiting"] is True and waiting["progress"]["phase"] == "waiting"
    stopped = reports.stop("alice", waiting["report_id"])
    assert stopped["forge"]["state"] == "cancelled" and "start" not in stopped["forge"]
    # extra games waiting behind the playing run: stopped, the report is as it was, saying so
    more = await reports.play_more("alice", first["report_id"])
    assert more["forge"]["state"] == "queued" and more["forge"]["waiting"] is True
    back = reports.stop("alice", first["report_id"])["forge"]
    assert (
        back["state"] == "done"
        and back["result"]["games"] == 4
        and back["more_note"] == reports_module.MORE_STOPPED
    )
    # the playing run ends; nothing stopped starts after it
    fake.finish(reports.get("alice", playing["report_id"])["forge"]["job_id"], [1, 1, 1, 1])
    await _until(lambda: reports.get("alice", playing["report_id"])["forge"]["state"] == "done")
    await asyncio.sleep(0.2)
    assert len(fake.started) == 2
    _stop_service(reports)


async def test_the_report_page_stops_a_test(stack: Stack) -> None:
    gw = stack.h.app.state.gateway
    fake = FakeForge(brackets=BRACKETED)
    gw.reports.forge, gw.reports.forge_interval = fake.client(), 0.05
    b = await linked_browser(stack)
    sub = gw.db._one("SELECT sub FROM users", ())["sub"]
    out = await gw.reports.run(sub, "42", simulate=False, forge=True)
    rid = out["report_id"]
    await _handed(gw.reports, sub, rid)
    page = (await b.http.get(f"/history/reports/{rid}", headers=NAV)).text
    panel = page.split("id='rv-progress'", 1)[1].split("</section>", 1)[0]
    # the gateway's games per report (the Settings page's number, 5 by default)
    assert "data-forge-live" in panel and "Forge has played 0 of 5 games." in panel
    assert "<progress class='rv-bar' max='5' value='0'" in panel and "Under a minute so far." in panel
    assert f"href='/history/reports/{rid}?box=stop'" in panel and "Games already played are kept." in panel
    assert "id='rv-stop'" in page and "Stop this test?" in page
    live = (await b.http.get(f"/api/v1/reports/{rid}/forge", headers={"Accept": "application/json"})).json()
    assert (
        "Forge has played 0 of 5 games." in live["progress_html"] and live["progress"]["phase"] == "playing"
    )
    csrf = await b.csrf("/account/details")
    refused = await b.http.post(f"/history/reports/{rid}/forge/stop", data={"csrf": "nope"})
    assert refused.status_code == 403
    r = await b.http.post(f"/history/reports/{rid}/forge/stop", data={"csrf": csrf})
    assert r.status_code == 303 and r.headers["location"] == f"/history/reports/{rid}?stopped=1"
    page = (await b.http.get(r.headers["location"], headers=NAV)).text
    assert "You stopped this test." in page and "id='rv-stop'" not in page
    assert gw.reports.get(sub, rid)["forge"]["state"] == "cancelled"
    # the app's API stops a test the same way
    gw.reports.min_interval = 0
    second = await gw.reports.run(sub, "42", simulate=False, forge=True)
    await _handed(gw.reports, sub, second["report_id"])
    token = await mcp_token(stack.h)
    api = await stack.h.http.post(
        f"/api/v1/reports/{second['report_id']}/forge/stop", headers={"Authorization": f"Bearer {token}"}
    )
    assert api.status_code == 200 and api.json()["forge"]["state"] == "cancelled", api.text


# -- plain words -----------------------------------------------------------------------------
async def test_a_forge_that_cant_be_reached_is_said_in_plain_words(tmp_path: Path) -> None:
    fake = DownForge()
    _, reports = _service(tmp_path, fake)
    out = await reports.run("alice", "42", games=300, options={"seed": 1})
    r = await _handed(reports, "alice", out["report_id"])
    assert r["forge"]["state"] == "failed" and r["forge"]["error"] == forge_module.FORGE_UNREACHABLE
    assert "ConnectError" not in json.dumps(r)
    assert (
        r["forge_status"]["error"]
        == "Forge couldn't be reached, so no Forge games were played. Run the test again later."
    )
    # Play more games names that the games never played, instead of "made before this gateway..."
    with pytest.raises(DeckError) as refused:
        await reports.play_more("alice", out["report_id"])
    assert str(refused.value) == reports_module.MORE_NOT_RUN and refused.value.extra["reason"] == "notrun"
    # the page offers Run again in the Forge section
    page = report_page.report_page_html(r, csrf="t")
    forge = page.split("id='rv-forge'", 1)[1].split("</section>", 1)[0]
    assert html.escape(forge_module.FORGE_UNREACHABLE) in forge and "action='/decks/42/report'" in forge
    assert "Run again" in forge
    _stop_service(reports)


# -- the progress, the same everywhere -------------------------------------------------------
def test_progress_names_the_step_the_games_and_the_time_so_far() -> None:
    now = 10_000.0
    assert (
        reports_module.run_progress(research=False, forge={"state": "done"}, solo=None, taken_at=0, now=now)
        is None
    )
    research = reports_module.run_progress(research=True, forge=None, solo=None, taken_at=now - 30, now=now)
    assert (
        research and research["phase"] == "solo_test" and research["elapsed_text"] == "under a minute so far"
    )
    waiting = {
        "state": "queued",
        "waiting": True,
        "queued_at": now - 600,
        "position": 2,
        "games_requested": 5,
    }
    p = reports_module.run_progress(research=False, forge=waiting, solo=None, taken_at=0, now=now)
    assert p and p["text"] == "Waiting for your earlier Forge test to finish (number 2 in your line)."
    assert p["elapsed_text"] == "10 min so far" and p["games_total"] == 5
    busy = {**waiting, "engine_busy": True}
    p = reports_module.run_progress(research=False, forge=busy, solo=None, taken_at=0, now=now)
    assert (
        p and p["phase"] == "forge_busy" and p["text"] == "Waiting for Forge, which is busy with other tests."
    )
    playing = {
        "state": "running",
        "job_id": "j",
        "queued_at": now - 3900,
        "games_requested": 5,
        "result": {"games": 2},
    }
    p = reports_module.run_progress(research=False, forge=playing, solo=None, taken_at=0, now=now)
    assert p and p["text"] == "Forge has played 2 of 5 games." and p["elapsed_text"] == "1 h 5 min so far"
    assert (p["games_done"], p["games_total"]) == (2, 5)
    solo = {
        "state": "running",
        "job_id": "s",
        "started_at": now - 120,
        "result": {"games": 1, "games_requested": 4},
    }
    p = reports_module.run_progress(research=False, forge={"state": "done"}, solo=solo, taken_at=0, now=now)
    assert (
        p and p["text"] == "Forge has played 1 of 4 solo test games." and p["elapsed_text"] == "2 min so far"
    )
    # the one-line mark of Home and the deck page
    line = reports_module._progress_line({"taken_at": now - 60}, playing)
    assert line.startswith("Forge has played 2 of 5 games · ") and line.endswith(" so far")
    assert reports_module._progress_line({"research_state": "running", "taken_at": now}, {}).startswith(
        "the solo test is running on the research service · "
    )
    # the panel: the bar only while games are counted, never a time left
    html = report_page.progress_html(
        {"report_id": "rep_x", "progress": {**p, "elapsed_text": "2 min so far"}, "forge_solo": solo}
    )
    assert "<progress class='rv-bar' max='4' value='1'" in html and "2 min so far." in html
    assert "left" not in html.split("rv-time", 1)[1].split("</p>", 1)[0]
