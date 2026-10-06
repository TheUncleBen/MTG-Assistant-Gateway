"""Per-day counters and the MCP middleware that feeds them."""

from __future__ import annotations

import datetime as _dt
from pathlib import Path
from types import SimpleNamespace

import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from mtg_gateway.admin import overview_data
from mtg_gateway.db import Database
from mtg_gateway.metrics import Metrics


@pytest.fixture
def db(tmp_path: Path):
    d = Database(tmp_path / "m.sqlite")
    yield d
    d.close()


def test_record_upserts_per_day_user_kind_name(db: Database) -> None:
    m = Metrics(db)
    today = _dt.datetime.now(_dt.UTC).date().isoformat()
    assert m.today() == today
    m.record("tool", "whoami", "u1")
    m.record("tool", "whoami", "u1")
    m.record("tool", "whoami", None)  # not tied to a user: its own row
    m.record("error", "whoami", "u1")
    m.record("api", "GET /decks", "u2", n=5)
    db.metrics_increment("2000-01-01", "u1", "tool", "ancient", 99)  # outside every window
    assert db.metrics_totals(30) == [
        {"kind": "api", "name": "GET /decks", "n": 5},
        {"kind": "tool", "name": "whoami", "n": 3},
        {"kind": "error", "name": "whoami", "n": 1},
    ]
    assert db.metrics_series(30, kind="tool") == [{"day": today, "kind": "tool", "n": 3}]
    assert {r["kind"] for r in db.metrics_series(7)} == {"api", "tool", "error"}
    assert db.metrics_series(1) == db.metrics_series(30)  # today is day one of the window
    rows = db._conn.execute("SELECT sub, n FROM metrics WHERE name = 'whoami' AND kind = 'tool'").fetchall()
    assert sorted((r["sub"], r["n"]) for r in rows) == [("", 1), ("u1", 2)]


def test_overview_series_is_zero_filled_for_every_day(db: Database) -> None:
    Metrics(db).record("tool", "whoami", "u1")
    state = SimpleNamespace(db=db)
    data = overview_data(state, days=30)
    assert data["tool_calls"] == 1 and data["errors"] == 0 and data["applies"] == 0
    series = data["series"]
    assert set(series) == {"tool", "error"}
    assert len(series["tool"]) == 30 and sum(series["tool"].values()) == 1
    assert list(series["tool"])[-1] == Metrics.today()
    assert sum(series["error"].values()) == 0


async def test_mcp_middleware_counts_tool_calls_and_errors(db: Database) -> None:
    mw = Metrics(db).mcp_middleware()
    token = AccessToken(token="t", client_id="c", scopes=["mtg"], subject="u1")
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:

        async def ok(_ctx):
            return {"content": [], "isError": False}

        async def failed(_ctx):
            return {"content": [{"type": "text", "text": "boom"}], "isError": True}

        async def raises(_ctx):
            raise RuntimeError("tool blew up")

        call = SimpleNamespace(method="tools/call", params={"name": "whoami", "arguments": {}})
        assert (await mw(call, ok))["isError"] is False
        assert (await mw(call, failed))["isError"] is True
        with pytest.raises(RuntimeError):
            await mw(SimpleNamespace(method="tools/call", params={"name": "deck_get"}), raises)
        # Not a tool call: passed through, not counted.
        assert await mw(SimpleNamespace(method="tools/list", params=None), ok) == {
            "content": [],
            "isError": False,
        }
        unnamed = SimpleNamespace(method="tools/call", params={})
        await mw(unnamed, ok)
    finally:
        auth_context_var.reset(reset)
    # Without an authenticated user the count is still taken, under no subject.
    await mw(SimpleNamespace(method="tools/call", params={"name": "whoami"}), ok)

    totals = {(t["kind"], t["name"]): t["n"] for t in db.metrics_totals(30)}
    assert totals == {
        ("tool", "whoami"): 3,
        ("error", "whoami"): 1,
        ("tool", "deck_get"): 1,
        ("error", "deck_get"): 1,
        ("tool", "(unnamed)"): 1,
    }
    today = Metrics.today()
    by_sub = db._conn.execute(
        "SELECT sub, SUM(n) AS n FROM metrics WHERE day = ? AND kind = 'tool' GROUP BY sub", (today,)
    ).fetchall()
    assert {r["sub"]: r["n"] for r in by_sub} == {"u1": 4, "": 1}


def test_record_never_raises(db: Database) -> None:
    m = Metrics(db)
    db.close()  # the connection is gone: the UPSERT fails, the caller does not
    m.record("tool", "whoami", "u1")
