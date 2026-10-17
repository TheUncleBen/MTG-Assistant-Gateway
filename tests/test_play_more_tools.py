"""Play more games for assistants and apps: the play_more_games tool, shared by
Claude and ChatGPT, and POST /api/v1/reports/{rid}/forge/more go through the same rules as the
report page's button (reports.play_more), with the same refusals and a reason code for each."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mtg_gateway import reports as reports_module
from mtg_gateway.app import WRITE_TOOLS

from .conftest import AUTO_PROXY, Stack, sse_json, stack_with
from .test_decks_and_proxy import call, mcp_token, structured
from .test_forge import BRACKETED, FakeForge

stack_options = stack_with(AUTO_PROXY)


async def _until(check: Any) -> None:
    for _ in range(400):
        if check():
            return
        await asyncio.sleep(0.02)
    raise AssertionError("never happened")


def _section(stack: Stack, rid: str) -> dict[str, Any]:
    row = stack.h.db._conn.execute("SELECT forge_json FROM reports WHERE id = ?", (rid,)).fetchone()
    return json.loads(row["forge_json"])


def _forge(stack: Stack, fake: FakeForge) -> Any:
    reports = stack.h.app.state.gateway.reports
    reports.forge, reports.forge_interval = fake.client(), 0.05
    return reports


async def _finished(stack: Stack, fake: FakeForge, token: str, args: dict[str, Any]) -> str:
    """A report run through the tool whose Forge games have all finished."""
    out = structured(
        await call(stack.h, token, "run_deck_report", {"forge": True, "simulate": False, **args})
    )
    assert out["ok"], out
    rid = out["report_id"]
    await _until(lambda: _section(stack, rid).get("job_id"))
    fake.finish(_section(stack, rid)["job_id"], [1, 2, 1, 1])
    await _until(lambda: _section(stack, rid).get("state") == "done")
    return rid


async def test_the_tool_and_the_api_add_games_with_the_buttons_rules(stack: Stack) -> None:
    h = stack.h
    fake = FakeForge(brackets=BRACKETED)
    reports = _forge(stack, fake)
    token = await mcp_token(h)
    auth = {"Authorization": f"Bearer {token}"}
    out = structured(
        await call(h, token, "run_deck_report", {"deck_ref": "42", "forge": True, "simulate": False})
    )
    rid = out["report_id"]
    await _until(lambda: _section(stack, rid).get("job_id"))
    # only once the report's games have finished: the same refusal as the button, with its code
    early = structured(await call(h, token, "play_more_games", {"report_id": rid}))
    assert early == {
        "ok": False,
        "error": "invalid",
        "message": reports_module.MORE_NOT_ENDED,
        "reason": "ended",
        "error_code": early.get("error_code"),
        **{k: v for k, v in early.items() if k not in ("ok", "error", "message", "reason", "error_code")},
    }
    api_early = await h.http.post(f"/api/v1/reports/{rid}/forge/more", headers=auth)
    assert api_early.status_code == 400
    assert (
        api_early.json()["message"] == reports_module.MORE_NOT_ENDED and api_early.json()["reason"] == "ended"
    )
    fake.finish(_section(stack, rid)["job_id"], [1, 2, 1, 1])
    await _until(lambda: _section(stack, rid).get("state") == "done")
    shown = structured(await call(h, token, "get_deck_report", {"report_id": rid}))
    assert shown["forge"]["more_games"] == 4  # the games-per-report number again
    # the tool: the same table again, added to the earlier games
    more = structured(await call(h, token, "play_more_games", {"report_id": rid}))
    assert more["ok"] and more["started"] is True and "message" not in more
    assert more["report_id"] == rid and more["forge_status"]["state"] in ("queued", "running")
    assert len(fake.started) == 2 and fake.started[1]["games"] == 4
    assert fake.started[1]["decks"] == fake.started[0]["decks"]
    audit = h.db._conn.execute(
        "SELECT client_id FROM audit_log WHERE event = 'report_more_games' ORDER BY id DESC LIMIT 1"
    ).fetchone()
    assert audit["client_id"]  # the app that asked is recorded, as for the member's other changes
    fake.finish(_section(stack, rid)["job_id"], [2, 2, 3, 1])
    await _until(lambda: _section(stack, rid).get("state") == "done")
    # the app's API: the same, answered with the report as it now is
    api = await h.http.post(f"/api/v1/reports/{rid}/forge/more", headers=auth)
    assert api.status_code == 202, api.text
    assert api.json()["ok"] and api.json()["forge"]["games_requested"] == 12
    assert len(fake.started) == 3
    fake.finish(_section(stack, rid)["job_id"], [1, 1, 1, 1])
    await _until(lambda: _section(stack, rid).get("state") == "done")
    # at most 50 Forge games a report: 12 played, nine more rounds of 4 and then the last 2
    for games in [4] * 9 + [2]:
        view = structured(await call(h, token, "get_deck_report", {"report_id": rid}))
        assert view["forge"]["more_games"] == games
        step = structured(await call(h, token, "play_more_games", {"report_id": rid}))
        assert step["ok"] and fake.started[-1]["games"] == games
        fake.finish(_section(stack, rid)["job_id"], [1] * games)
        await _until(lambda: _section(stack, rid).get("state") == "done")
    started = len(fake.started)
    full = structured(await call(h, token, "play_more_games", {"report_id": rid}))
    assert full["ok"] is False and full["reason"] == "full" and full["message"] == reports_module.MORE_FULL
    assert len(fake.started) == started
    # a report that isn't the member's, and a gateway without Forge
    gone = structured(await call(h, token, "play_more_games", {"report_id": "rep_nope"}))
    assert (gone["ok"], gone["error"], gone["reason"]) == (False, "not_found", "notfound")
    api_gone = await h.http.post("/api/v1/reports/rep_nope/forge/more", headers=auth)
    assert api_gone.status_code == 404 and api_gone.json()["reason"] == "notfound"
    reports.forge = None
    off = structured(await call(h, token, "play_more_games", {"report_id": rid}))
    assert off["reason"] == "noforge" and off["message"] == reports_module.MORE_NO_FORGE


async def test_a_full_queue_starts_nothing_through_the_tool(stack: Stack) -> None:
    h = stack.h
    fake = FakeForge(brackets=BRACKETED)
    reports = _forge(stack, fake)
    reports.min_interval = 0  # each run below is a fresh report, not the one just made
    token = await mcp_token(h)
    rid = await _finished(stack, fake, token, {"deck_ref": "42"})
    # one run playing and two waiting behind it: the member's queue is full
    for _ in range(3):
        structured(
            await call(
                h, token, "run_deck_report", {"deck_ref": "42", "forge": True, "simulate": False, "games": 20}
            )
        )
    before = _section(stack, rid)
    started = len(fake.started)
    out = structured(await call(h, token, "play_more_games", {"report_id": rid}))
    assert (
        out["ok"] is False and out["reason"] == "queue" and out["message"] == reports_module.MORE_QUEUE_FULL
    )
    assert len(fake.started) == started and _section(stack, rid) == before  # nothing started or changed


async def test_a_comparison_gets_the_same_number_on_both_decks_through_the_tool(stack: Stack) -> None:
    h = stack.h
    fake = FakeForge(brackets=BRACKETED)
    _forge(stack, fake)
    stack.ark.private.discard(43)  # Amy's deck, public for this test
    token = await mcp_token(h)
    first = await _finished(stack, fake, token, {"deck_ref": "42", "compare_with": "43"})
    second = await _finished(stack, fake, token, {"deck_ref": "43", "same_opponents_as": first})
    played = len(fake.started)
    out = structured(await call(h, token, "play_more_games", {"report_id": second}))
    assert out["ok"] and out["started"], out
    # one place in this Forge: the first deck's games start, the second deck's wait behind them
    assert len(fake.started) == played + 1 and _section(stack, second)["state"] == "queued"
    fake.finish(_section(stack, first)["job_id"], [1, 1, 2, 2])
    await _until(lambda: len(fake.started) == played + 2)
    new = fake.started[played:]
    assert new[0]["games"] == new[1]["games"] == 4
    assert new[0]["decks"][1:] == new[1]["decks"][1:] == fake.started[0]["decks"][1:]


async def test_both_assistants_get_the_same_tool_and_it_counts_as_a_change(stack: Stack) -> None:
    """Claude and ChatGPT reach the same MCP server, so they see the same play_more_games; it
    changes something, so it is a write tool, refused to a token connected read-only as the
    API's route (write) is."""
    h = stack.h
    token = await mcp_token(h)
    listed = sse_json(await h.mcp(token, "tools/list", {}, rid=3))["result"]["tools"]
    tool = next(t for t in listed if t["name"] == "play_more_games")
    assert tool["inputSchema"]["required"] == ["report_id"]
    assert tool["annotations"]["readOnlyHint"] is False and tool["annotations"]["openWorldHint"] is False
    assert "button" in tool["description"] and "Never contacts Archidekt" in tool["description"]
    assert "play_more_games" in WRITE_TOOLS


async def test_a_read_only_app_is_refused_on_both_paths_and_starts_nothing(stack: Stack) -> None:
    """An app connected read-only (scope mtg.read) gets insufficient_scope from the tool and the
    API, and no Forge games start; the same app with the mtg scope starts them, and a repeat is
    refused with its reason on both paths."""
    from .test_round4_data import _app_token

    h = stack.h
    fake = FakeForge(brackets=BRACKETED)
    _forge(stack, fake)
    rid = await _finished(stack, fake, await mcp_token(h), {"deck_ref": "42"})
    started, before = len(fake.started), _section(stack, rid)
    reader, _ = await _app_token(h, "reader", scope="mtg.read")
    tool = await call(h, reader, "play_more_games", {"report_id": rid})
    assert tool.get("isError") or (tool.get("structuredContent") or {}).get("ok") is False
    assert "insufficient_scope" in json.dumps(tool)
    api = await h.http.post(
        f"/api/v1/reports/{rid}/forge/more", headers={"Authorization": f"Bearer {reader}"}
    )
    assert api.status_code == 403 and api.json()["error"] == "insufficient_scope"
    assert len(fake.started) == started and _section(stack, rid) == before  # nothing started
    writer, _ = await _app_token(h, "writer", scope="mtg")
    first = structured(await call(h, writer, "play_more_games", {"report_id": rid}))
    assert first["ok"] and first["started"] and len(fake.started) == started + 1
    again = structured(await call(h, writer, "play_more_games", {"report_id": rid}))
    assert (again["ok"], again["error"], again["reason"]) == (False, "invalid", "ended")
    api_again = await h.http.post(
        f"/api/v1/reports/{rid}/forge/more", headers={"Authorization": f"Bearer {writer}"}
    )
    assert api_again.status_code == 400 and api_again.json()["reason"] == "ended"
    assert len(fake.started) == started + 1
