"""Which assistant calls wait for the identity provider's live membership answer (membership.py).

Owner decision 11-01, "Only changes and admin wait": a tool call that only reads or proposes is
answered at once past the cache and the provider asked in the background. Which tools those are
comes from each tool's own readOnlyHint, so a new tool that does not say it is read only waits."""

from __future__ import annotations

import json
from types import SimpleNamespace

from mcp import types
from mcp.server.mcpserver import MCPServer

from mtg_gateway.app import PROPOSAL_TOOLS, WRITE_TOOLS, mcp_only_reads, tool_only_reads

from .conftest import AUTO_PROXY, Stack, stack_with
from .test_decks_and_proxy import mcp_token
from .test_in_chat_approve import _tools

stack_options = stack_with(AUTO_PROXY)  # the proxied research tools are in the catalogue too


async def test_every_tool_in_the_catalogue_is_classified_by_its_own_hint(stack: Stack) -> None:
    tools = await _tools(stack.h, await mcp_token(stack.h))
    reads = stack.gw.mcp_reads
    assert len(tools) > 60
    assert any(n.startswith("scryfall_") for n in tools)  # proxied ones included
    for name, tool in tools.items():
        hint = (tool.get("annotations") or {}).get("readOnlyHint")
        assert reads(name) is (hint is True), name
    # every tool that may change something waits, the proposals apart (owner decision 10-28)
    for name in WRITE_TOOLS - PROPOSAL_TOOLS:
        assert reads(name) is False, name
    assert reads("apply_proposal") is False
    for name in ("propose_deck_changes", "amend_proposal", "withdraw_proposal", "combine_proposals"):
        assert reads(name) is True, name
    assert reads("no_such_tool") is False


def test_a_new_tool_waits_unless_it_says_it_is_read_only() -> None:
    server = MCPServer("t")

    @server.tool(name="push_to_archidekt")
    def push() -> str:
        return "done"

    @server.tool(name="peek", annotations=types.ToolAnnotations(readOnlyHint=True))
    def peek() -> str:
        return "seen"

    tool = server._tool_manager.get_tool
    assert tool_only_reads("push_to_archidekt", tool("push_to_archidekt"), False) is False
    assert tool_only_reads("peek", tool("peek"), False) is True
    # readOnlyHint left out, or false: waits
    unsaid = SimpleNamespace(annotations=types.ToolAnnotations(destructiveHint=False))
    assert tool_only_reads("x", unsaid, False) is False
    # a tool the scope guard counts as a write waits even if marked read only, unless a proposal
    marked = SimpleNamespace(annotations=types.ToolAnnotations(readOnlyHint=True))
    assert tool_only_reads("apply_proposal", marked, False) is False
    assert tool_only_reads("propose_deck_changes", marked, False) is True
    # not a gateway tool: only the research service's own read-only tools, when it is there
    assert tool_only_reads("scryfall_search", None, True) is True
    assert tool_only_reads("scryfall_search", None, False) is False
    assert tool_only_reads("goldfish_run", None, True) is False  # hidden there: answered with a refusal


def test_only_a_tool_call_of_read_tools_counts_as_a_read() -> None:
    def reads(name: str) -> bool:
        return name == "whoami"

    def call(name: object, rid: int = 1) -> dict:
        return {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {"name": name}}

    def body(x: object) -> bytes:
        return json.dumps(x).encode()

    assert mcp_only_reads(body(call("whoami")), reads) is True
    assert mcp_only_reads(body([call("whoami"), call("whoami", 2)]), reads) is True
    assert mcp_only_reads(body([call("whoami"), call("apply_proposal", 2)]), reads) is False
    assert mcp_only_reads(body([]), reads) is False
    assert mcp_only_reads(body(call("apply_proposal")), reads) is False
    assert mcp_only_reads(body(call(None)), reads) is False
    assert mcp_only_reads(body({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}), reads) is False
    assert mcp_only_reads(body({"jsonrpc": "2.0", "id": 1, "method": "tools/call"}), reads) is False
    no_params = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": []}
    assert mcp_only_reads(body(no_params), reads) is False
    assert mcp_only_reads(b"{oops", reads) is False
    assert mcp_only_reads(b"\xff\xfe", reads) is False
    assert mcp_only_reads(b"", reads) is False
    assert mcp_only_reads(b"[" * 60_000, reads) is False  # too deep to read: waits, no error


async def test_the_body_read_ahead_is_handed_on_whole_in_its_pieces() -> None:
    from mtg_gateway.app import _read_ahead

    sent = [
        {"type": "http.request", "body": b'{"a":', "more_body": True},
        {"type": "http.request", "body": b" 1}", "more_body": False},
        {"type": "http.disconnect"},
    ]
    queue = list(sent)

    async def receive() -> dict:
        return queue.pop(0)

    body, replay = await _read_ahead(receive, 1024)
    assert body == b'{"a": 1}'
    assert [await replay(), await replay(), await replay()] == sent

    # past the limit: no body to judge, and every piece still handed on
    queue = [
        {"type": "http.request", "body": b"x" * 10, "more_body": True},
        {"type": "http.request", "body": b"y" * 10, "more_body": True},
        {"type": "http.request", "body": b"z", "more_body": False},
    ]
    copy = list(queue)
    body, replay = await _read_ahead(receive, 15)
    assert body is None
    assert [await replay(), await replay(), await replay()] == copy

    # a client gone before the body ended: nothing to judge, the disconnect handed on
    queue = [{"type": "http.request", "body": b"x", "more_body": True}, {"type": "http.disconnect"}]
    copy = list(queue)
    body, replay = await _read_ahead(receive, 15)
    assert body is None
    assert [await replay(), await replay()] == copy
