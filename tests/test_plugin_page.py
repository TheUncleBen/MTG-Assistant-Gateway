"""The public install page and the self-served plugin marketplace and archive."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from mtg_gateway import plugin_page

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser

REPO = Path(__file__).resolve().parent.parent
REPO_PLUGIN = REPO / "plugin"


@pytest.fixture
async def harness(tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(REPO_PLUGIN))
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        yield h


def test_repository_plugin_files_are_well_formed() -> None:
    for name in ("mtg-gateway", "mtg-gateway-operator"):
        root = REPO_PLUGIN / name
        manifest = json.loads((root / ".claude-plugin" / "plugin.json").read_text())
        assert manifest["name"] == name
        portable = json.loads((root / "plugin.json").read_text())
        assert portable["name"] == name
        skills = sorted(p.parent.name for p in root.glob("skills/*/SKILL.md"))
        assert skills, name
        for skill in root.glob("skills/*/SKILL.md"):
            head = skill.read_text().split("---", 2)[1]
            assert f"name: {skill.parent.name}" in head
            assert "description:" in head
    # The repository copy has no address: it is filled in from MTG_GATEWAY_URL or by the gateway.
    repo_mcp = json.loads((REPO_PLUGIN / "mtg-gateway" / ".mcp.json").read_text())
    assert repo_mcp["mcpServers"]["mtg-gateway"] == {"type": "http", "url": "${MTG_GATEWAY_URL:-}"}
    marketplace = json.loads((REPO / ".claude-plugin" / "marketplace.json").read_text())
    assert {p["name"] for p in marketplace["plugins"]} == {"mtg-gateway", "mtg-gateway-operator"}
    assert all(p["source"].startswith("./plugin/") for p in marketplace["plugins"])


async def test_marketplace_and_archive_are_public_and_consistent(harness: Harness) -> None:
    mp = await harness.http.get("/plugin/marketplace.json")
    assert mp.status_code == 200
    doc = mp.json()
    assert doc["name"] == "mtg-gateway"
    (entry,) = doc["plugins"]
    assert entry["name"] == "mtg-gateway"
    assert entry["source"]["source"] == "archive"
    sha = entry["source"]["sha256"]
    assert entry["source"]["url"] == f"{GATEWAY}/plugin/mtg-gateway.zip?v={sha}"
    assert mp.headers["cache-control"] == "no-cache"

    z = await harness.http.get(f"/plugin/mtg-gateway.zip?v={sha}")
    assert z.status_code == 200
    assert z.headers["content-type"] == "application/zip"
    assert hashlib.sha256(z.content).hexdigest() == entry["source"]["sha256"]
    assert z.headers["etag"] == f'"{entry["source"]["sha256"]}"'

    zf = zipfile.ZipFile(io.BytesIO(z.content))
    names = zf.namelist()
    assert all(n.startswith("mtg-gateway/") for n in names)
    for required in (
        "mtg-gateway/.claude-plugin/plugin.json",
        "mtg-gateway/.mcp.json",
        "mtg-gateway/plugin.json",
        "mtg-gateway/mcp.json",
        "mtg-gateway/skills/mtg-gateway/SKILL.md",
        "mtg-gateway/skills/mtg-gateway/reference/tools.md",
        "mtg-gateway/skills/setup/SKILL.md",
    ):
        assert required in names, required
    assert not any("/.git/" in n for n in names)
    # The served copy carries this gateway's own address, for both plugin formats.
    for connector in ("mtg-gateway/.mcp.json", "mtg-gateway/mcp.json"):
        servers = json.loads(zf.read(connector))["mcpServers"]
        assert servers["mtg-gateway"]["url"] == f"{GATEWAY}/mcp", connector
    portable = json.loads(zf.read("mtg-gateway/mcp.json"))["mcpServers"]["mtg-gateway"]
    assert portable["type"] == "streamable-http"

    # Same bytes every time, so the pinned digest stays valid.
    again = await harness.http.get("/plugin/mtg-gateway.zip")
    assert again.content == z.content


async def test_install_pages(harness: Harness) -> None:
    b = Browser(harness)
    try:
        await b.login("/install")
        await _check_install_pages(harness, b)
    finally:
        await b.aclose()


async def _check_install_pages(harness: Harness, b: Browser) -> None:
    picker = await b.http.get("/install")
    assert picker.status_code == 200
    for key in plugin_page.CLIENTS:
        assert f"href='/install?for={key}'" in picker.text
    assert "claude plugin install" not in picker.text  # steps only after a client is picked
    assert "Sign out" not in picker.text

    page = await b.http.get("/install?for=claude-code")
    assert page.status_code == 200
    assert f"claude plugin marketplace add {GATEWAY}/plugin/marketplace.json" in page.text
    assert "claude plugin install mtg-gateway@mtg-gateway" in page.text
    assert "codex mcp add" not in page.text
    page = await b.http.get("/install?for=claude")
    assert "/plugin/mtg-gateway.zip" in page.text
    assert "modal=add-custom-connector" in page.text
    assert "connectorUrl=https%3A%2F%2Fmtg.test%2Fmcp" in page.text
    assert "default-src 'none'" in page.headers["content-security-policy"]
    assert "Sign out" not in page.text

    md = await harness.http.get("/install.md")
    assert md.status_code == 200
    assert md.headers["content-type"].startswith("text/markdown")
    assert "claude plugin install mtg-gateway@mtg-gateway" in md.text
    assert f"{GATEWAY}/mcp" in md.text
    assert "never paste a password" in md.text
    assert "install.md?for=chatgpt" in md.text  # no ?for=: every client, plus how to narrow it
    assert "codex mcp add" in md.text and "Developer mode" in md.text

    landing = await b.http.get("/")
    assert "href='/install'" in landing.text


async def test_install_steps_narrow_to_one_client(harness: Harness) -> None:
    md = (await harness.http.get("/install.md?for=ChatGPT")).text
    assert "## ChatGPT" in md and "Developer mode" in md
    assert "claude plugin install" not in md and "codex mcp add" not in md
    # The agent rules and the blocked-tool fallback are always included.
    assert "Do not retry it" in md
    assert f"{GATEWAY}/proposals" in md and f"{GATEWAY}/scan" in md
    assert "install.md?for=" not in md

    b = Browser(harness)
    try:
        await b.login("/install")
        page = (await b.http.get("/install?for=chatgpt")).text
        assert "Does not work on" in page and "href='/install?for=claude'" in page
        assert "modal=add-custom-connector" not in page

        # An unknown value falls back to the picker / the full Markdown, never an error.
        assert (await b.http.get("/install?for=<script>")).status_code == 200
    finally:
        await b.aclose()
    assert "<script>" not in (await harness.http.get("/install?for=<script>")).text
    assert "codex mcp add" in (await harness.http.get("/install.md?for=nope")).text


async def test_missing_plugin_folder_gives_503(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(tmp_path / "nowhere"))
    assert plugin_page.plugin_dir() is None
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        assert (await h.http.get("/plugin/marketplace.json")).status_code == 503
        assert (await h.http.get("/plugin/mtg-gateway.zip")).status_code == 503
        assert (await h.http.get("/install")).status_code == 200
