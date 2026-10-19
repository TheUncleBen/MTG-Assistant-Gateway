"""Research tools given a deck id or link read the deck as the member sees it (in the
2026-10-11 live run, goldfish_annotate said "Deck not found" for the member's own private deck,
because Mystic Forge reads Archidekt anonymously). Also: a simulation's text names a stored copy
only when the gateway stored one, and the tool descriptions the gateway adds."""

from __future__ import annotations

import json

import pytest
from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field

from mtg_gateway.mf_proxy import FULL_REPORT, names_deck, rewrite_text
from mtg_gateway.reports import STORED_NOTE

from .conftest import Stack, StackOptions, sse_json
from .fake_mystic_forge import _GoldfishRunInput, _ValidateDecklistInput
from .test_decks_and_proxy import (
    call,
    linked_user,
    mcp_token,
    structured,
)


class _AnnotateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deck: str = Field(min_length=1)


class _TopCardsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    period: str = "week"
    color: str | None = None
    limit: int = 20


class _CommanderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    limit: int = Field(default=10, description="Max cards per category")


def research_service(seen: list[str]) -> MCPServer:
    """Mystic Forge's shape for the tools under test. goldfish_annotate answers a deck id the way
    the real one does for a private deck (it reads Archidekt anonymously) and echoes a list."""
    mf = MCPServer(name="mystic-forge-research")

    @mf.tool(name="goldfish_annotate", description="Pre-flight a deck for goldfish simulation.")
    async def goldfish_annotate(params: _AnnotateInput) -> str:
        seen.append(params.deck)
        if "\n" not in params.deck:
            return "Deck not found on Archidekt. It may be private or the ID is wrong."
        return "# Goldfish annotation pre-flight\nFIRST: " + params.deck.splitlines()[0]

    @mf.tool(name="edhrec_top_cards", description="Get the most popular EDH cards by time period.")
    async def edhrec_top_cards(params: _TopCardsInput) -> str:
        return "# Top Cards"

    @mf.tool(name="edhrec_average_deck", description="Get the average deck for a commander.")
    async def edhrec_average_deck(params: _CommanderInput) -> str:
        return "# Average Deck"

    @mf.tool(name="goldfish_run", description="goldfish simulation")
    async def goldfish_run(params: _GoldfishRunInput) -> str:
        first = params.deck.strip().splitlines()[0]
        return (
            f"# Goldfish run\nCommander: {first} | {params.n} games\n\n## Metrics\n```json\n{{}}\n```\n\n"
            'Full report: goldfish_report("abc123")'
        )

    @mf.tool(name="validate_decklist", description="validate a decklist")
    async def validate_decklist(params: _ValidateDecklistInput) -> str:
        return f"Validated {len(params.decklist.splitlines())} lines"

    return mf


@pytest.fixture
def stack_options() -> StackOptions:
    """Writes on with the shipped approval mode, and a Mystic Forge that records the decks it is
    sent in ``stack.seen``."""
    seen: list[str] = []
    return StackOptions(
        settings={"writes_enabled": True},
        mystic_forge=lambda: research_service(seen),
        started=lambda st: setattr(st, "seen", seen),
    )


def _text(result: dict) -> str:
    return "\n".join(c.get("text", "") for c in result.get("content", []))


async def test_annotate_reads_the_members_private_deck(stack: Stack) -> None:
    stack.ark.private.add(42)  # alice's deck, readable only with her session
    token = await linked_user(stack)
    for ref in ("42", "https://archidekt.com/decks/42/sample-commander-deck"):
        out = await call(stack.h, token, "goldfish_annotate", {"deck": ref})
        assert not out.get("isError"), out
        # the commander leads the list Mystic Forge is given, by its own name and category
        assert "FIRST: 1 Aesi, Tyrant of Gyre Strait" in _text(out) and "[Commander]" in _text(out), out
        assert "Deck not found" not in _text(out)
    assert all("\n" in deck for deck in stack.seen)  # never the bare id: Mystic Forge reads anonymously


async def test_annotate_tells_someone_elses_private_deck_from_a_missing_link(stack: Stack) -> None:
    # alice, linked, asks for amy's private deck 43: neither her session nor the public sees it
    token = await linked_user(stack)
    out = await call(stack.h, token, "goldfish_annotate", {"deck": "43"})
    assert out.get("isError") is True, out
    assert "someone else's private deck" in _text(out) and "alice" in _text(out), out
    assert structured(out)["error_code"] == "NOT_FOUND", out  # the deck read's kind is kept
    # a member with no linked account is told their own private deck needs the link
    stack.h.idp.user = {
        **stack.h.idp.user,
        "sub": "bob-sub",
        "preferred_username": "bob",
        "email": "b@x.test",
    }
    other = await mcp_token(stack.h)
    out = await call(stack.h, other, "goldfish_annotate", {"deck": "43"})
    assert out.get("isError") is True and "no public deck 43" in _text(out), out
    assert "link your Archidekt account" in _text(out), out
    assert stack.seen == []  # nothing was sent to the research service


async def test_annotate_passes_a_pasted_list_through(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    out = await call(
        stack.h, token, "goldfish_annotate", {"deck": "1 Aesi, Tyrant of Gyre Strait\n99 Island"}
    )
    assert "FIRST: 1 Aesi, Tyrant of Gyre Strait" in _text(out), out
    assert stack.seen == ["1 Aesi, Tyrant of Gyre Strait\n99 Island"]


def test_names_deck_is_an_id_or_a_link_never_a_list() -> None:
    assert names_deck("27095317") and names_deck(27095317) and names_deck("archidekt.com/decks/1/x")
    assert not names_deck("1 Sol Ring\n// from https://archidekt.com/decks/1") and not names_deck(True)
    assert not names_deck("1 Sol Ring") and not names_deck(None)


async def test_only_a_stored_report_points_at_its_stored_copy(stack: Stack) -> None:
    h = stack.h
    token = await linked_user(stack)
    stored = structured(await call(h, token, "run_deck_report", {"deck_ref": "42", "games": 20}))
    assert stored["ok"] and stored.get("report_id"), stored
    assert f"{FULL_REPORT} {STORED_NOTE}" in stored["goldfish"]["text"], stored["goldfish"]
    again = structured(await call(h, token, "get_deck_report", {"report_id": stored["report_id"]}))
    assert STORED_NOTE in json.dumps(again)
    pasted = structured(
        await call(
            h,
            token,
            "run_deck_report",
            {"deck_ref": "Commander\n1 Aesi, Tyrant of Gyre Strait\n\n99 Island", "games": 20},
        )
    )
    assert pasted["stored"] is False and FULL_REPORT in pasted["goldfish"]["text"], pasted["goldfish"]
    assert "History" not in json.dumps(pasted["goldfish"]), pasted["goldfish"]
    assert "History" not in rewrite_text('Full report: goldfish_report("x")')


async def test_descriptions_say_what_upstream_leaves_out(stack: Stack) -> None:
    token = await mcp_token(stack.h)
    listing = sse_json(await stack.h.mcp(token, "tools/list"))
    tools = {t["name"]: t for t in listing["result"]["tools"]}
    top = tools["edhrec_top_cards"]["description"]
    assert "limit is per section" in top and "period is ignored" in top, top
    assert "private decks" in tools["goldfish_annotate"]["description"]
    # the average deck shares the commander page's input, but prints the whole deck whatever limit
    limit = tools["edhrec_average_deck"]["inputSchema"]["properties"]["limit"]["description"]
    assert limit.startswith("Not used by this tool") and "per category" not in limit, limit


def test_a_rate_limited_deck_read_keeps_its_kind_and_wait() -> None:
    from mtg_gateway.decks import DeckError
    from mtg_gateway.mf_proxy import _deck_refusal

    out = _deck_refusal(DeckError("rate_limited", "Archidekt budget used up", retry_after=30))
    assert out.is_error and out.content[0].text == "Archidekt budget used up"
    assert out.structured_content == {
        "ok": False,
        "error": "rate_limited",
        "message": "Archidekt budget used up",
        "retry_after": 30,
    }
