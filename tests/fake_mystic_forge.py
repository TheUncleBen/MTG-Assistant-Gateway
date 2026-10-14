"""A fake Mystic Forge MCP server: the tools the gateway proxies, with the input shapes the real
service validates, for tests that run the gateway's Mystic Forge proxy in memory."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, ConfigDict, Field


class _GoldfishRunInput(BaseModel):
    """The shape of Mystic Forge's goldfish_run input (server.py GoldfishRunInput, extra forbidden):
    one ``params`` object. A flat call fails validation exactly as it does against the real service."""

    model_config = ConfigDict(extra="forbid")
    deck: str = Field(min_length=1)
    n: int = Field(default=300, ge=1, le=5000)
    seed: int | None = None
    until_turn: int = Field(default=10, ge=1, le=30)
    opponents: int = Field(default=1, ge=1, le=5)
    mulligan: dict[str, object] | None = None
    annotations: list[dict[str, object]] | None = None
    combos: list[object] | None = None
    constructed: bool = False


class _GoldfishAbInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    deck_a: str = Field(min_length=1)
    deck_b: str = Field(min_length=1)
    n: int = Field(default=300, ge=1, le=5000)
    seed: int | None = None
    until_turn: int = Field(default=10, ge=1, le=30)
    annotations: list[dict[str, object]] | None = None
    annotations_a: list[dict[str, object]] | None = None
    annotations_b: list[dict[str, object]] | None = None
    combos: list[object] | None = None
    allow_different_commanders: bool = False
    constructed: bool = False


class _ValidateDecklistInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decklist: str = Field(min_length=1)
    commander: str | None = None


# The simulator calls the fake received, (tool, params), for tests that check what was sent.
SIM_CALLS: list[tuple[str, dict[str, object]]] = []


def fake_mystic_forge() -> MCPServer:
    mf = MCPServer(name="mystic-forge-fake")

    @mf.tool(name="scryfall_named", description="Look up a card by name")
    async def scryfall_named(name: str) -> dict[str, object]:
        return {"name": name, "oracle_text": "fake oracle text"}

    # The simulators, with Mystic Forge's one-model signature and its renderers' headings. A
    # commander the fake does not know is answered the way Mystic Forge does: a sentence, not an
    # error.
    @mf.tool(name="goldfish_run", description="goldfish simulation")
    async def goldfish_run(params: _GoldfishRunInput) -> str:
        SIM_CALLS.append(("goldfish_run", params.model_dump()))
        first = params.deck.strip().splitlines()[0]
        if params.constructed:
            # Mystic Forge's header for a 60-card game (patch 0007)
            size = sum(int(line.split()[0]) for line in params.deck.strip().splitlines())
            return (
                f"# Goldfish run\nDeck: {size} cards | Format: 60-card (no commander, 20 life, on the "
                f"play) | {params.n} games\n\n## Metrics\n```json\n{{}}\n```"
            )
        if "Unknown" in first:
            return f"Commander '{first}' was not recognized by Scryfall; fix the name and rerun."
        return f"# Goldfish run\nCommander: {first} | {params.n} games\n\n## Metrics\n```json\n{{}}\n```"

    @mf.tool(name="goldfish_ab", description="paired goldfish A/B")
    async def goldfish_ab(params: _GoldfishAbInput) -> str:
        SIM_CALLS.append(("goldfish_ab", params.model_dump()))
        return f"# Goldfish A/B\n{params.n} paired games\n\n## Deltas (A − B)\n(none)"

    @mf.tool(name="validate_decklist", description="validate a decklist")
    async def validate_decklist(params: _ValidateDecklistInput) -> str:
        # Like Mystic Forge, which reads names through Scryfall's collection lookup: a
        # double-faced card named with both faces is "not found".
        both = [line for line in params.decklist.splitlines() if " // " in line]
        if both:
            return "# Validation: ISSUES FOUND\n\n**not found on Scryfall:**\n" + "\n".join(both)
        return f"Validated {len(params.decklist.splitlines())} lines"

    @mf.tool(name="watchlist_list", description="stateful, must stay hidden")
    async def watchlist_list() -> list[str]:
        return ["secret"]

    @mf.tool(name="unknown_future_tool", description="not on either list")
    async def unknown_future_tool() -> str:
        return "hidden"

    return mf
