"""The end-user skill and guides name only tools the gateway really exposes,
and the skill catalogue covers every exposed tool."""

from __future__ import annotations

import re
from pathlib import Path

from mtg_gateway.mf_proxy import ALLOWED_TOOLS, BLOCKED_TOOLS

from .conftest import FakeIdP, Harness, make_settings, running, sse_json

ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = ROOT / "plugin" / "mtg-gateway" / "skills" / "mtg-gateway"
CATALOGUE = SKILL_DIR / "reference" / "tools.md"
CHATGPT = ROOT / "plugin" / "mtg-gateway" / "chatgpt-instructions.md"
DOCS = [
    SKILL_DIR / "SKILL.md",
    CATALOGUE,
    CHATGPT,
    ROOT / "README.md",
    ROOT / "docs" / "SKILL.md",
    ROOT / "docs" / "ONBOARDING.md",
    ROOT / "docs" / "CONNECT.md",
    ROOT / "docs" / "OPERATIONS.md",
]

# Words shaped like tool names: a known tool family or verb prefix, then snake case.
TOOLISH = re.compile(
    r"\b(?:scryfall|edhrec|archidekt|spellbook|rules|precon|goldfish|validate|format|watchlist|price"
    r"|list|get|apply|propose|parse|account)_[a-z0-9_]+\b"
)
# Field, table and error names that match the pattern but are not tools.
NOT_TOOLS = {
    "archidekt_username",
    "account_page",
    "archidekt_links",
    "archidekt_login",
    "archidekt_password",
    "apply_too_soon",
    "price_total",
    "archidekt_bracket",
    "archidekt_session_refreshed",
    "archidekt_link_expired",
}


async def _local_tools(tmp_path: Path, idp: FakeIdP) -> set[str]:
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        client = await h.register()
        token = (await h.tokens_for(client))["access_token"]
        r = await h.mcp(token, "tools/list")
        return {t["name"] for t in sse_json(r)["result"]["tools"]}


async def test_docs_name_only_real_tools_and_catalogue_covers_all(tmp_path: Path, idp: FakeIdP) -> None:
    local = await _local_tools(tmp_path, idp)
    assert "whoami" in local and "apply_proposal" in local
    exposed = local | ALLOWED_TOOLS
    known = exposed | BLOCKED_TOOLS

    catalogue = CATALOGUE.read_text(encoding="utf-8")
    missing = sorted(t for t in exposed if f"`{t}`" not in catalogue)
    assert not missing, f"tools missing from {CATALOGUE.relative_to(ROOT)}: {missing}"

    unknown: dict[str, list[str]] = {}
    for path in DOCS:
        words = set(TOOLISH.findall(path.read_text(encoding="utf-8"))) - NOT_TOOLS
        bad = sorted(words - known)
        if bad:
            unknown[str(path.relative_to(ROOT))] = bad
    assert not unknown, f"docs mention tools the gateway does not have: {unknown}"


def test_claude_skill_frontmatter_fits_upload_limits() -> None:
    text = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md must start with YAML frontmatter"
    fields = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    name, desc = fields["name"].strip(), fields["description"].strip()
    assert name == SKILL_DIR.name and re.fullmatch(r"[a-z0-9-]{1,64}", name)
    assert 0 < len(desc) <= 200  # Claude's documented limit for uploaded skills


def test_chatgpt_block_fits_custom_instructions() -> None:
    text = CHATGPT.read_text(encoding="utf-8")
    block = text.split("```text\n", 1)[1].split("```", 1)[0]
    assert len(block) <= 4000  # paid-plan custom instructions take 5,000 characters (reported)
