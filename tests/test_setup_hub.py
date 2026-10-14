"""The setup hub (D11): one /install page with a section for Claude, Claude Code, ChatGPT, Codex,
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
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(REPO_PLUGIN))
    app_dir = tmp_path / "app-dist"
    app_dir.mkdir()
    (app_dir / "mtg-assistant-gateway.apk").write_bytes(b"PK\x03\x04" + b"\0" * 100)
    meta = '{"version_name": "0.7.24", "cert_sha256": "' + "ab" * 32 + '"}'
    (app_dir / "mtg-assistant-gateway.json").write_text(meta)
    monkeypatch.setenv("MTG_APP_DIR", str(app_dir))
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
    # signed out: the same sign-in as before
    for path, back in (("/skill", "/skill"), ("/app", "/app"), ("/skill/mtg-gateway.zip", "/skill")):
        r = await harness.http.get(path)
        assert r.status_code == 302 and r.headers["location"] == f"/login?next={back}", path
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
