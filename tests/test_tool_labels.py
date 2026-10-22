"""tool_labels.py: every tool the gateway registers, every Mystic Forge tool it passes through,
every JSON API route, every admin action and every chat card kind has plain words (a readable name
for admin lists and a member's ask), so no raw tool name reaches member text and no Metrics row is
nameless. A new tool without an entry fails here."""

from __future__ import annotations

import re
from pathlib import Path

from starlette.routing import Route

from mtg_gateway import admin, cards, mf_proxy, tool_labels
from mtg_gateway import app as app_mod
from mtg_gateway.tool_labels import TOOLS, metric_label, tool_ask, tool_name

from .conftest import make_settings


def _registered_tools(tmp_path, monkeypatch) -> tuple[list[str], list]:
    servers = []
    build = app_mod.build_mcp_server
    monkeypatch.setattr(
        app_mod, "build_mcp_server", lambda state: servers.append(build(state)) or servers[-1]
    )
    app = app_mod.create_app(make_settings(tmp_path, watchlists_enabled=True))
    return [t.name for t in servers[-1]._tool_manager.list_tools()], app.routes


def test_every_registered_tool_has_a_readable_name_and_a_plain_ask(tmp_path, monkeypatch) -> None:
    tools, _routes = _registered_tools(tmp_path, monkeypatch)
    assert len(tools) > 30 and "watch_card" in tools
    missing = [t for t in tools if t not in TOOLS]
    assert not missing, f"tool_labels.TOOLS lacks {missing}"
    for t in tools:
        words = TOOLS[t]
        assert words.name and words.ask, t
        assert words.name[0].isupper() and words.ask[0].isupper(), t
        # plain words: the raw name is not the label, and no snake_case leaks into member text
        assert t not in (words.name, words.ask) and "_" not in words.name + words.ask, t
        assert "verif" not in (words.name + words.ask).lower(), t
        assert "—" not in words.name + words.ask, t  # no em-dashes in new sentences


def test_every_passed_through_mystic_forge_tool_has_words_too() -> None:
    missing = sorted(mf_proxy.ALLOWED_TOOLS - set(TOOLS))
    assert not missing, missing


def test_every_api_route_admin_action_and_card_kind_is_named(tmp_path, monkeypatch) -> None:
    _tools, routes = _registered_tools(tmp_path, monkeypatch)
    recorded = set()
    for r in routes:
        if isinstance(r, Route) and r.path.startswith("/api/v1/") and r.path != "/api/v1/prefs":
            for m in r.methods or ():
                if m in ("GET", "POST", "DELETE", "PUT", "PATCH"):
                    recorded.add(f"{m} {r.path[len('/api/v1') :]}")
    assert recorded, "no API routes found"
    missing = sorted(recorded - set(tool_labels.API_ROUTES))
    assert not missing, f"tool_labels.API_ROUTES lacks {missing}"
    assert set(admin.ACTIONS) == set(tool_labels.ADMIN_ACTIONS)
    assert set(cards.LINK_KINDS) == set(tool_labels.CARD_KINDS)
    assert set(admin.METRIC_KINDS) <= set(tool_labels.METRIC_KINDS)
    assert tool_labels.METRIC_KINDS["card"] == "Chat card loads"


def test_the_api_route_table_matches_the_source() -> None:
    """The route decorator in api.py is the source of truth; a route added there without words
    here fails (the running app is checked above; this reads the file so the message names the line)."""
    src = (Path(__file__).parents[1] / "src" / "mtg_gateway" / "api.py").read_text(encoding="utf-8")
    found = {f"{m} {p}" for p, m in re.findall(r'@route\("([^"]+)", "([A-Z]+)"', src)}
    assert found and found <= set(tool_labels.API_ROUTES), sorted(found - set(tool_labels.API_ROUTES))


def test_metric_labels_read_as_the_metrics_page_wants() -> None:
    assert tool_name("get_deck") == "Read a deck" and tool_ask("get_deck") == "Read my deck"
    assert (
        tool_ask("compare_decks") == "Compare two decks"
        and tool_ask("run_deck_report") == "Run a test report"
    )
    assert metric_label("tool", "get_deck") == "Read a deck"
    assert metric_label("error", "get_deck") == "Read a deck failed"
    assert metric_label("error", "server_error") == "Server error"
    assert metric_label("error", "made_up") == "Unknown error"
    assert (
        metric_label("tool", "(unknown)") == "Unknown tool"
        and metric_label("tool", "(unnamed)") == "Unknown tool"
    )
    assert metric_label("tool", "<script>") == "Unknown tool"  # a made-up name is never echoed
    assert metric_label("api", "GET /decks") == "List my decks (API)"
    assert metric_label("admin", "disable") == "Account disabled"
    assert metric_label("admin", "enable") == "Account turned back on"
    assert metric_label("admin", "revoke") == "Assistants' access and sign-ins revoked"
    assert metric_label("admin", "delete_data") == "Former member's data deleted"
    assert metric_label("card", "printings") == "Card printings"
    assert tool_labels.recorded_as("get_deck") == "Recorded as get_deck"
    assert tool_ask("nope") == ""
