"""The setup hub: one /install page with a section for Claude, Claude Code, ChatGPT, Codex,
the assistant skill and the Android app, and /install.md, both from one step list. The old
addresses keep working: /skill and /app lead to their section, and the downloads stay where they
were."""

from __future__ import annotations

import html
import re
from pathlib import Path

import pytest

from mtg_gateway import setup_hub

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser

REPO = Path(__file__).resolve().parent.parent
REPO_PLUGIN = REPO / "plugin"


@pytest.fixture
async def harness(tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("mtg_gateway.plugin_page.PLUGIN_DIRS", (REPO_PLUGIN,))
    app_dir = tmp_path / "app-dist"
    app_dir.mkdir()
    (app_dir / "mtg-assistant-gateway.apk").write_bytes(b"PK\x03\x04" + b"\0" * 100)
    meta = '{"version_name": "0.7.24", "cert_sha256": "' + "ab" * 32 + '"}'
    (app_dir / "mtg-assistant-gateway.json").write_text(meta)
    monkeypatch.setattr("mtg_gateway.app_page.IMAGE_APP_DIR", app_dir)
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        yield h


async def test_page_and_markdown_come_from_one_step_list(harness: Harness) -> None:
    """Every step of every section, as the step list writes it, is on the page and in the
    Markdown, numbered the same way: there is no second copy of the steps to drift."""
    b = Browser(harness)
    try:
        await b.login("/install")
        for key in setup_hub.SECTIONS:
            page = (await b.http.get(f"/install?for={key}")).text
            md = (await harness.http.get(f"/install.md?for={key}")).text
            ctx = setup_hub.Context(GATEWAY, "MTG Assistant Gateway", app={"cert_sha256": "ab" * 32})
            sec = setup_hub.section(key, ctx)
            assert f"## {sec.heading}" in md, key
            for block in sec.blocks:
                if block.steps:
                    # the same number of numbered steps in each
                    ol = page.split(f"<h2>{html.escape(block.title or sec.heading)}</h2>", 1)[1]
                    ol = ol[ol.index("<ol class='setup'>") : ol.index("</ol>")]
                    assert ol.count("<li>") == len(block.steps), (key, block.title)
                for n, step in enumerate(block.steps, 1):
                    assert setup_hub.inline_html(step.text, GATEWAY) in page, (key, step.text)
                    assert f"{n}. {step.text}" in md, (key, step.text)
                for para in block.paras:
                    assert setup_hub.inline_html(para, GATEWAY) in page, (key, para)
                    assert para in md, (key, para)
    finally:
        await b.aclose()


async def test_the_hub_has_every_section_with_steps_copy_buttons_and_icon(harness: Harness) -> None:
    b = Browser(harness)
    try:
        await b.login("/install")
        hub = (await b.http.get("/install")).text
        for key in ("claude", "claude-code", "chatgpt", "codex", "skill", "android"):
            assert f"href='/install?for={key}'" in hub, key
        gpt = (await b.http.get("/install?for=chatgpt")).text
        assert "<ol class='setup'>" in gpt and "data-copy='gpt-url'" in gpt
        assert re.search(r"href='/static/gateway-icon-connector.png\?v=[0-9a-f]{12}' download=", gpt)
        skill = (await b.http.get("/install?for=skill")).text
        assert "href='/skill/mtg-gateway.zip'" in skill and "data-copy='gpt-text'" in skill
        android = (await b.http.get("/install?for=android")).text
        assert "href='/app/mtg-assistant-gateway.apk'" in android and "0.7.24" in android
        assert "ab" * 32 in android
    finally:
        await b.aclose()


# Research notes, not instructions: where a fact came from, when it was tested, how sure we are.
RESEARCH_WORDING = re.compile(
    r"\(reported|reportedly|\breported by\b|not documented|documented by|undocumented|"
    r"OpenAI's (pages|help|documentation)|Anthropic's|according to (OpenAI|Anthropic)|"
    r"checked (with this gateway|on a|on the)|tested with this gateway|user report|\binferred\b|"
    r"unknown to us|\bverified\b|\b20\d\d-\d\d-\d\d\b",
    re.IGNORECASE,
)


async def test_no_section_reads_like_research_notes(harness: Harness) -> None:
    """The setup steps are plain instructions: no sources cited, no test dates, no hedges such
    as "(reported)" or "not documented", on the page or in the Markdown, for every section."""
    b = Browser(harness)
    try:
        await b.login("/install")
        for key in ("", *setup_hub.SECTIONS):
            query = f"?for={key}" if key else ""
            page = (await b.http.get(f"/install{query}")).text
            # the ChatGPT instructions box is text for the assistant (it names a `verified` field)
            page = re.sub(r"<textarea[^>]*>.*?</textarea>", "", page, flags=re.DOTALL)
            md = (await harness.http.get(f"/install.md{query}")).text
            for where, text in (("page", page), ("md", md)):
                found = [m.group(0) for m in RESEARCH_WORDING.finditer(text)]
                assert not found, (key or "hub", where, found)
    finally:
        await b.aclose()


async def test_old_addresses_keep_working(harness: Harness) -> None:
    # signed out: the downloads ask for the same sign-in as before; the pages read signed out (C2)
    for path, back in (("/skill/mtg-gateway.zip", "/skill"), ("/app/mtg-assistant-gateway.apk", "/app")):
        r = await harness.http.get(path)
        assert r.status_code == 302 and r.headers["location"] == f"/login?next={back}", path
    for path, section in (("/skill", "skill"), ("/app", "android")):
        r = await harness.http.get(path)
        assert r.status_code == 302 and r.headers["location"] == f"/install?for={section}", path
    assert (await harness.http.get("/install.md")).status_code == 200
    assert (await harness.http.get("/plugin/mtg-gateway.zip")).status_code == 200
    b = Browser(harness)
    try:
        await b.login("/install")
        for path, section in (("/skill", "skill"), ("/app", "android")):
            r = await b.http.get(path)
            assert r.status_code == 302 and r.headers["location"] == f"/install?for={section}", path
        assert (await b.http.get("/skill/mtg-gateway.zip")).status_code == 200
        assert (await b.http.get("/app/mtg-assistant-gateway.apk")).status_code == 200
        assert (await b.http.get("/app/latest.json")).json()["version_name"] == "0.7.24"
    finally:
        await b.aclose()


async def test_the_public_markdown_keeps_the_signed_in_parts_on_the_page(harness: Harness) -> None:
    md = (await harness.http.get("/install.md")).text
    assert "## Assistant skill" not in md and "## Android app" not in md  # the assistants' steps only
    assert "`?for=skill` and `?for=android`" in md
    skill = (await harness.http.get("/install.md?for=skill")).text
    assert f"They are on {GATEWAY}/install?for=skill after you sign in." in skill
    assert "<textarea" not in skill
    android = (await harness.http.get("/install.md?for=android")).text
    assert f"[Download the app (APK)]({GATEWAY}/app/mtg-assistant-gateway.apk)" in android
    assert "ab" * 32 not in android  # the facts are the signed-in page's


def test_connect_doc_points_at_the_hub() -> None:
    doc = (REPO / "docs" / "CONNECT.md").read_text(encoding="utf-8")
    assert "/install?for=skill" in doc and "/install?for=android" in doc


# -- the approved layout (F11, F12): the app list beside the steps, one orange button a section --


async def test_the_app_list_sits_beside_the_steps_and_marks_the_picked_app(harness: Harness) -> None:
    b = Browser(harness)
    try:
        await b.login("/install")
        hub = (await b.http.get("/install")).text
        # the purpose line under the title; the page is a wide one
        assert "<div class='ui-head'><h1>Guided setup</h1></div><p class='ui-sub'>" in hub
        assert setup_hub.PURPOSE in hub and "class='wrap ui-wide'" in hub
        # the list (a computer) and the drop-down (narrow screens) are both there; CSS shows one
        assert "<nav class='pick' aria-label='Which app'><h2>Which app will you use?</h2>" in hub
        assert "<details class='drop'><summary><span class='v'>Which app will you use?</span>" in hub
        assert hub.count("Which app will you use?") == 2  # the list's heading and the drop-down's label
        assert "Pick one to see only the steps that work there." not in hub
        assert "Other sections:" not in hub and "btn-primary' href='/install?for=" not in hub
        assert ".inst .cols{grid-template-columns:17rem minmax(0,1fr)}" in hub
        # the first screen: the intro, the phone note and the Connector URL; its address is a link
        assert (
            f"<a class='mdaddr' href='{GATEWAY}/install.md'><code>{GATEWAY}/<wbr>install.md</code></a>" in hub
        )
        assert f"<p>{setup_hub.INTRO}</p>" in hub.replace("&#x27;", "'")
        assert "data-copy='mcp-url'" in hub and "On a phone?" in hub
        # a picked app: tinted in the list (aria-current), named in the drop-down, no orange button
        claude = (await b.http.get("/install?for=claude")).text
        assert (
            "<a href='/install?for=claude' aria-current='true'>Claude: web, desktop, iPhone and Android</a>"
            in claude
        )
        assert claude.count("aria-current='true'") == 2  # the list and the drop-down
        assert (
            "<span class='sfk'>Steps for:</span><span class='v'>Claude: web, desktop, iPhone and Android"
            "</span>" in claude
        )
        assert f"<a class='mdaddr' href='{GATEWAY}/install.md?for=claude'>" in claude
    finally:
        await b.aclose()


async def test_each_section_has_one_orange_button(harness: Harness) -> None:
    b = Browser(harness)
    try:
        await b.login("/install")
        expected = {
            "claude": ["Connect to Claude"],
            "claude-code": [],
            "chatgpt": ["Download the icon (PNG)"],
            "codex": [],
            "skill": ["Download the skill (ZIP)"],
            "android": ["Download the app (APK)"],
        }
        for key, orange in expected.items():
            page = (await b.http.get(f"/install?for={key}")).text
            found = re.findall(
                r"<a class='btn btn-primary' href='[^']*'[^>]*>(?:<svg.*?</svg> )?([^<]*)</a>", page
            )
            assert found == orange, (key, found)
        claude = (await b.http.get("/install?for=claude")).text
        assert re.search(
            r"<a class='btn' href='/plugin/mtg-gateway.zip'><svg.*?</svg> Download the plugin \(ZIP\)</a>",
            claude,
        )
    finally:
        await b.aclose()


async def test_the_approved_wording_changes(harness: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    b = Browser(harness)
    try:
        await b.login("/install")
        claude = (await b.http.get("/install?for=claude")).text
        # F12: the test step is a plain ask, not a tool name
        assert "Ask your assistant “Who am I on the MTG gateway?” It should answer with your name." in claude
        assert "whoami" not in claude.split("<h2>After connecting</h2>", 1)[1]
        android = (await b.http.get("/install?for=android")).text
        assert "Play Protect may warn that the developer is unknown. That is expected" in android
        # Ben (25 Oct): a Samsung phone installs nothing while Auto Blocker is on, updates included,
        # and Play Protect's offer to scan the app may be taken or not
        blocker = (
            "turn <strong>Auto Blocker</strong> off (Settings &gt; Security and privacy &gt; Auto Blocker)"
        )
        assert blocker in android
        assert "neither this app nor its updates" in android
        assert "It may also offer to scan the app first: scanning it or not are both fine." in android
        assert "with Auto Blocker still off on a Samsung phone" in android
        assert "unverified" not in android
        # the Markdown keeps its lead and the same intro
        md = (await harness.http.get("/install.md")).text
        assert md.splitlines()[2] == f"{setup_hub.MD_LEAD} {setup_hub.INTRO}"
        assert "Ask your assistant “Who am I on the MTG gateway?” It should answer with your name." in md
    finally:
        await b.aclose()


async def test_without_an_app_file_the_page_speaks_to_members(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mtg_gateway.plugin_page.PLUGIN_DIRS", (REPO_PLUGIN,))
    monkeypatch.setattr("mtg_gateway.app_page.IMAGE_APP_DIR", tmp_path / "no-such-dir")
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        b = Browser(h)
        try:
            await b.login("/install")
            page = (await b.http.get("/install?for=android")).text
            assert "<h2>No app file on this gateway</h2>" in page
            assert (
                "<p>The person who runs this gateway hasn't added the app file yet. In the meantime, "
                f"open <strong>{GATEWAY}</strong> in your phone's browser and add it to your home "
                "screen.</p>" in page
            )
            assert "docs/ANDROID.md" not in page and "If you run the gateway" not in page
            assert "<a class='btn' href='/decks'>Back to decks</a>" in page and "btn-primary" not in page
        finally:
            await b.aclose()
