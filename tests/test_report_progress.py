"""A run shows from the press (0.7.46): the report is stored as soon as its deck is read, before the
research service's validation and solo test come back, so Reports, History and the deck list it
while it runs, and the website's Run full test opens the report page at once. The page's solo test
section says it is running and fills in by itself. An assistant's run still waits for its result,
and a refused one keeps nothing; a gateway restart in the middle leaves a report that says so."""

from __future__ import annotations

import asyncio
import json
import time

from mtg_gateway.db import Database
from mtg_gateway.decks import DeckError
from mtg_gateway.report_view import RESEARCH_FAILED
from mtg_gateway.reports import RESEARCH_STOPPED, ReportService

from .conftest import Stack, stack_with
from .test_companion import COMPANION, NAV, linked_browser
from .test_decks_and_proxy import call, linked_user, structured
from .test_forge import _Decks

stack_options = stack_with(COMPANION)


class Gate:
    """Holds the research service's goldfish_run until opened, as a slow service would."""

    def __init__(self, reports: ReportService) -> None:
        self.reports = reports
        self.reached = asyncio.Event()
        self.open = asyncio.Event()
        self.real = reports.mf.call

    async def call(self, name, arguments, **kw):  # noqa: ANN001, ANN003, ANN201
        if name == "goldfish_run":
            self.reached.set()
            await self.open.wait()
        return await self.real(name, arguments, **kw)

    def __enter__(self) -> Gate:
        self.reports.mf.call = self.call
        return self

    def __exit__(self, *exc: object) -> None:
        self.open.set()
        self.reports.mf.call = self.real


def _sub(gw) -> str:  # noqa: ANN001
    return gw.db._one("SELECT sub FROM users", ())["sub"]


async def test_the_website_press_opens_the_report_while_the_solo_test_runs(stack: Stack) -> None:
    gw = stack.h.app.state.gateway
    b = await linked_browser(stack)
    try:
        with Gate(gw.reports) as gate:
            csrf = await b.csrf("/account/details")
            # the press is answered while the research service has not answered yet
            ran = await asyncio.wait_for(b.http.post("/decks/42/report", data={"csrf": csrf}), 3)
            assert ran.status_code == 303, ran.text
            where = ran.headers["location"]
            assert where.startswith("/history/reports/rep_"), where
            rid = where.rsplit("/", 1)[1]
            await asyncio.wait_for(gate.reached.wait(), 3)
            sub = _sub(gw)
            # listed as running everywhere the gateway lists reports
            (row,) = gw.reports.list(sub, "42")
            assert row["report_id"] == rid and row["status"] == "running" and row["research"] == "running", (
                row
            )
            listed = (await b.http.get("/reports", headers=NAV)).text
            assert "Solo test running" in listed and "The solo test is running" in listed
            assert "the solo test is running" in (await b.http.get("/history", headers=NAV)).text
            page = (await b.http.get(where, headers=NAV)).text
            section = page.split("id='rv-solo'", 1)[1].split("</section>", 1)[0]
            assert "data-forge-live data-research" in section and f"data-report-id='{rid}'" in section
            assert "The solo test is running on the research service." in section
            assert "Solo test running" in page.split("id='rep-status'", 1)[1][:400]
            live = (
                await b.http.get(f"/api/v1/reports/{rid}/forge", headers={"Accept": "application/json"})
            ).json()
            assert live["live"] is True and live["research"] == "running", live
            # nothing is recorded in Activity until the run has a result
            assert not [e for e in gw.db.audit_for_user(sub, 50) if e["event"] == "report_created"]
            gate.open.set()
            await asyncio.wait_for(gw.reports.settle(), 5)
        done = gw.reports.get(sub, rid)
        assert done["research"] is None and done["goldfish"]["ok"] and done["status"] == "complete", done
        page = (await b.http.get(where, headers=NAV)).text
        assert "data-research" not in page and "Quick solo test (" in page
        live = (
            await b.http.get(f"/api/v1/reports/{rid}/forge", headers={"Accept": "application/json"})
        ).json()
        assert live["research"] is None and live["live"] is False, live
        (created,) = [e for e in gw.db.audit_for_user(sub, 50) if e["event"] == "report_created"]
        assert created["detail"]["report_id"] == rid and created["client_id"] == "__browser__", created
    finally:
        await b.aclose()


async def test_a_second_press_while_one_runs_opens_the_same_report(stack: Stack) -> None:
    gw = stack.h.app.state.gateway
    b = await linked_browser(stack)
    try:
        with Gate(gw.reports) as gate:
            csrf = await b.csrf("/account/details")
            first = await asyncio.wait_for(b.http.post("/decks/42/report", data={"csrf": csrf}), 3)
            await asyncio.wait_for(gate.reached.wait(), 3)
            again = await asyncio.wait_for(b.http.post("/decks/42/report", data={"csrf": csrf}), 3)
            assert again.headers["location"] == first.headers["location"] + "?reused=1", again.headers
            gate.open.set()
            await asyncio.wait_for(gw.reports.settle(), 5)
        assert len(gw.reports.list(_sub(gw), "42")) == 1
    finally:
        await b.aclose()


async def test_an_assistants_run_is_listed_while_it_runs_and_waits_for_its_result(stack: Stack) -> None:
    gw = stack.h.app.state.gateway
    token = await linked_user(stack)
    with Gate(gw.reports) as gate:
        task = asyncio.create_task(call(stack.h, token, "run_deck_report", {"deck_ref": "42", "games": 20}))
        await asyncio.wait_for(gate.reached.wait(), 3)
        (row,) = gw.reports.list(_sub(gw), "42")
        assert row["status"] == "running" and row["research"] == "running", row
        assert not task.done()
        gate.open.set()
        out = structured(await asyncio.wait_for(task, 5))
    assert out["ok"] and out["report_id"] == row["report_id"] and out["status"] == "complete", out
    assert out["goldfish"]["ok"], out


async def test_a_refused_research_call_keeps_nothing_for_an_assistant_and_says_why_on_the_website(
    stack: Stack,
) -> None:
    gw = stack.h.app.state.gateway
    await linked_user(stack)
    sub = _sub(gw)
    real = gw.reports._mf

    async def busy(sub_, tool, arguments, **kw):  # noqa: ANN001, ANN003, ANN202
        if tool == "goldfish_run":
            raise DeckError("rate_limited", "Research calls for your account are still running.")
        return await real(sub_, tool, arguments, **kw)

    gw.reports._mf = busy
    try:
        try:
            await gw.reports.run(sub, "42", forge=False)
        except DeckError as exc:
            assert exc.kind == "rate_limited"
        else:
            raise AssertionError("an assistant's refused run answers with the refusal")
        assert gw.reports.list(sub, "42") == []  # nothing kept, as before
        out = await gw.reports.start(sub, "42", forge=False)
        await gw.reports.settle()
        r = gw.reports.get(sub, out["report_id"])
        assert r["research"] == "failed" and r["status"] == "failed", r
        assert r["goldfish"]["note"] == "Research calls for your account are still running."
    finally:
        gw.reports._mf = real


def test_a_report_cut_off_by_a_restart_says_so(tmp_path) -> None:  # noqa: ANN001
    db = Database(tmp_path / "g.db")
    db.upsert_user("u1", email=None, name=None, preferred_username=None, groups=[])
    ReportService(db, _Decks(), None)
    stats = json.dumps({"card_count": 100, "_run": {"mode": "commander", "simulate": True}})
    with db.tx() as c:
        c.execute(
            "INSERT INTO reports (id, owner_sub, deck_id, deck_name, fingerprint, taken_at, stats_json, "
            "research_state) VALUES ('rep_cut', 'u1', '42', 'Deck', 'f', ?, ?, 'running')",
            (int(time.time()), stats),
        )
    again = ReportService(db, _Decks(), None)  # the gateway starts again
    r = again.get("u1", "rep_cut")
    assert r["research"] == "failed" and r["status"] == "failed", r
    assert r["goldfish"]["note"] == RESEARCH_STOPPED


async def test_a_research_failure_reads_in_words_with_run_again(stack: Stack) -> None:
    gw = stack.h.app.state.gateway
    b = await linked_browser(stack)
    try:
        sub = _sub(gw)
        real = gw.reports.mf.call

        async def broken(name, arguments, **kw):  # noqa: ANN001, ANN003, ANN202
            if name == "goldfish_run":
                from mcp import types

                return types.CallToolResult(
                    content=[
                        types.TextContent(type="text", text="Error executing tool run_deck_report: boom")
                    ],
                    isError=True,
                )
            return await real(name, arguments, **kw)

        gw.reports.mf.call = broken
        try:
            out = await gw.reports.run(sub, "42", forge=False)
        finally:
            gw.reports.mf.call = real
        # the assistant still reads the service's own words
        assert "Error executing tool" in out["goldfish"]["text"], out["goldfish"]
        page = (await b.http.get(f"/history/reports/{out['report_id']}", headers=NAV)).text
        main = page.split("<main", 1)[1]
        assert "Error executing tool" not in main and "run_deck_report" not in main
        solo = main.split("id='rv-solo'", 1)[1].split("</section>", 1)[0]
        assert RESEARCH_FAILED in solo, solo
        assert "action='/decks/42/report'" in solo and "Run again" in solo, solo
        md = (await b.http.get(f"/history/reports/{out['report_id']}/export.md")).text
        assert "Error executing tool" not in md and RESEARCH_FAILED in md
    finally:
        await b.aclose()
