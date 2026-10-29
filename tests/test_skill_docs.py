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
    "apply_needs_user",
    "apply_if_allowed",
    "price_total",
    "price_change",
    "price_note",
    "price_source_1",
    "price_source_2",
    "watchlist_url",
    "archidekt_bracket",
    "archidekt_text",
    "archidekt_session_refreshed",
    "archidekt_link_expired",
    "archidekt_link_swept",
    "archidekt_link_unsealed",
    "archidekt_sessions_sealed",
}


async def _local_tools(tmp_path: Path, idp: FakeIdP) -> set[str]:
    # watchlists on: the catalogue documents the optional watch tools too
    async with running(Harness(make_settings(tmp_path, watchlists_enabled=True), idp)) as h:
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
    # plain words: what to tell the member about the check after an apply
    assert "Report whether the check matched" in block and "Report verified" not in block


def test_both_guides_say_never_to_ask_for_a_sign_in() -> None:
    """Ben, 0.7.31 test round: a ChatGPT test asked him to sign in to archidekt.com so it could
    check its own changes. Both guides say it never needs that: the tools read changes back."""
    skill = (SKILL_DIR / "SKILL.md").read_text(encoding="utf-8")
    assert "Apart from linking Archidekt on `/account` (and the first" in skill
    assert "you never need the user to sign in\n   to archidekt.com or the gateway for you" in skill
    assert "read it back yourself (`get_deck`, `list_my_decks`)" in skill
    block = CHATGPT.read_text(encoding="utf-8").split("```text\n", 1)[1]
    assert "codes or a sign-in for you, except linking Archidekt at account_page" in block


def test_every_guide_says_to_finish_the_review_before_proposing() -> None:
    """Ben, 0.7.31 test round: a card came up after every Approve, one after another. Claude's skill,
    the ChatGPT block and the server instructions every app gets all say to propose once, after the
    review, and to amend a waiting proposal instead of proposing again."""
    from mtg_gateway.app import server_instructions

    skill = " ".join((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split())
    assert "**Finish your review before you propose:** put every change into one proposal" in skill
    assert "call `amend_proposal` instead of proposing again" in skill
    block = CHATGPT.read_text(encoding="utf-8").split("```text\n", 1)[1]
    assert (
        "Finish your review, then propose once with every change; to change a waiting one, amend_proposal."
        in block
    )
    told = server_instructions("https://gateway.example.com")
    assert "Finish your review before proposing: one proposal with every change, and amend_proposal" in told


def test_no_guide_mentions_a_message_posted_as_the_user() -> None:
    """Owner decision 0.7.46: the cards no longer post "Approved, go on" as the user's message. The
    guides say the outcome comes with the user's next message, to carry on from it and to read the
    proposal only when that is unclear; none still mentions the posted message."""
    from mtg_gateway.app import server_instructions
    from mtg_gateway.guide import add_guide_routes

    skill = " ".join((SKILL_DIR / "SKILL.md").read_text(encoding="utf-8").split())
    block = CHATGPT.read_text(encoding="utf-8").split("```text\n", 1)[1]
    told = server_instructions("https://gateway.example.com")
    guide = Path(add_guide_routes.__code__.co_filename).read_text(encoding="utf-8")
    for text in (skill, block, told, guide):
        assert "go on" not in text.replace("goes on", "")
    assert "Carry on from the outcome you were told" in skill
    assert "After a card press, use the outcome given; proposal_status only if unclear." in block
    assert "Carry on from that outcome, and call proposal_status only when it is unclear" in told


def test_no_doc_still_names_the_cards_0_7_33_took_away() -> None:
    """Since 0.7.33 a deck, the account, statistics, comparisons, deck lists and scans come as text;
    the docs and the assistants' guides once listed their cards in a dozen places."""
    stale = re.compile(
        r"a deck, the account|the same account card|deck card for the snapshot|same deck list card"
        r"|a deck by category|\(`deck`, `snapshot`, `printings`\)",
        re.IGNORECASE,
    )
    files = [*(ROOT / "docs").glob("*.md"), *(ROOT / "plugin").rglob("*.md"), ROOT / "README.md"]
    found = [f"{p.relative_to(ROOT)}: {m.group(0)}" for p in files for m in stale.finditer(p.read_text())]
    assert not found, found


def test_the_android_guide_says_confirm_for_the_browsers_app_check() -> None:
    text = " ".join((ROOT / "docs" / "ANDROID.md").read_text(encoding="utf-8").split())
    assert "could not confirm the app" in text and "could not verify the app" not in text


def test_removal_from_the_group_is_told_without_a_time_promise() -> None:
    for name in ("CHANGELOG.md", "docs/IDP-AUTHENTIK.md"):
        text = " ".join((ROOT / name).read_text(encoding="utf-8").split())
        assert "loses access within a minute" not in text and "within a minute it revokes" not in text, name
        assert "asked again in the background" in text, name
    # a page being read never waits for the provider; writes, admin pages and assistant requests do,
    # so a removed member is refused on the next one (tests/e2e/test_08_live_membership.py)
    text = " ".join((ROOT / "docs/IDP-AUTHENTIK.md").read_text(encoding="utf-8").split())
    assert "(writes, admin pages and assistant requests wait for the answer)" in text
    assert "60 seconds" not in text


def test_chatgpt_block_uses_the_glossary_and_names_the_newer_answers() -> None:
    """Independent review of 0.7.46: the ChatGPT block still said "SIMULATION", "colour" and "user
    approves", and never named deck_after, plan_first or propose_account_action, which Claude's skill
    explains. The block uses the approved words and gives each a short line."""
    block = CHATGPT.read_text(encoding="utf-8").split("```text\n", 1)[1].split("```", 1)[0]
    assert "SIMULATION" not in block and "\nTEST\n" in block
    assert "colour" not in block and "color identity" in block
    assert "approves" not in block
    assert "deck_after" in block and "unpriced_added" in block
    assert "plan_first" in block
    assert "propose_account_action" in block and "propose_delete_deck" in block
    assert len(block) <= 4000
