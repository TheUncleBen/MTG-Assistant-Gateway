"""The ChatGPT plugin ZIP: Agent Plugins 1.0.0 layout, built for each gateway."""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

import jsonschema
import pytest

from mtg_gateway import chatgpt_plugin, plugin_page

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser

REPO = Path(__file__).resolve().parent.parent
REPO_PLUGIN = REPO / "plugin"
SCHEMAS = Path(__file__).resolve().parent / "fixtures" / "agent-plugins"
# The plugin name rule OpenAI's loader applies (and the spec's schema pattern says the same).
NAME_RULE = re.compile(r"^(?!.*(?:--|\.\.))[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?$")


def _schema(name: str) -> dict:
    return json.loads((SCHEMAS / f"{name}.schema.json").read_text())


def _validate(doc: dict, name: str) -> None:
    jsonschema.Draft202012Validator(_schema(name)).validate(doc)


def _open(data: bytes) -> tuple[zipfile.ZipFile, dict, dict]:
    zf = zipfile.ZipFile(io.BytesIO(data))
    return zf, json.loads(zf.read("plugin.json")), json.loads(zf.read("mcp.json"))


def test_vendored_schemas_are_the_ones_the_loader_checks() -> None:
    assert _schema("plugin")["$id"] == chatgpt_plugin.PLUGIN_SCHEMA
    assert _schema("mcp")["$id"] == chatgpt_plugin.MCP_SCHEMA


def test_archive_layout_and_fields() -> None:
    data = chatgpt_plugin.build_zip(REPO_PLUGIN, f"{GATEWAY}/mcp", GATEWAY)
    zf, manifest, mcp = _open(data)
    names = zf.namelist()
    # The plugin root is the archive root: no wrapping folder.
    assert names[:2] == ["plugin.json", "mcp.json"]
    assert "skills/mtg-gateway/SKILL.md" in names
    assert "skills/mtg-gateway/reference/tools.md" in names
    assert not any("setup-mtg-gateway" in n for n in names)  # Claude Code steps only
    assert not any(n.startswith(".") or "/." in n for n in names)
    assert {"assets/icon.png", "assets/logo.png"} <= set(names)

    _validate(manifest, "plugin")
    _validate(mcp, "mcp")
    assert manifest["$schema"] == "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
    assert manifest["name"] == "mtg-gateway"
    assert manifest["version"] == plugin_page.__version__
    assert mcp == {
        "$schema": "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json",
        "mcpServers": {"mtg-gateway": {"type": "streamable-http", "url": f"{GATEWAY}/mcp"}},
    }

    ui = manifest["extensions"]["com.openai"]["interface"]
    assert ui["displayName"] == "MTG Assistant Gateway"
    assert ui["websiteURL"] == GATEWAY
    assert "privacyPolicyURL" not in ui and "termsOfServiceURL" not in ui  # the gateway has no such pages
    assert 1 <= len(ui["defaultPrompt"]) <= 3 and all(len(p) <= 128 for p in ui["defaultPrompt"])
    for key in ("composerIcon", "logo"):
        path = ui[key]
        assert path.startswith("./") and ".." not in path
        icon = zf.read(path[2:])
        assert icon.startswith(b"\x89PNG\r\n\x1a\n")
    # The gateway's own icon, byte for byte, never another logo.
    static = REPO / "src" / "mtg_gateway" / "static"
    assert zf.read("assets/icon.png") == (static / "gateway-icon-connector.png").read_bytes()
    assert zf.read("assets/logo.png") == (static / "gateway-icon-512.png").read_bytes()

    skill = zf.read("skills/mtg-gateway/SKILL.md").decode()
    head = skill.split("---", 2)[1]
    assert "\nname: mtg-gateway\n" in head and "\ndescription: " in head
    assert skill == (REPO_PLUGIN / "mtg-gateway/skills/mtg-gateway/SKILL.md").read_text()

    # The only hosts the ZIP names are this gateway's own and the public project pages: no other
    # deployment's address can leak into a member's download.
    gateway_host = GATEWAY.split("://", 1)[1].encode()
    for n in names:
        if n.endswith(".png"):
            continue
        hosts = set(re.findall(rb"https?://([A-Za-z0-9.-]+)", zf.read(n)))
        assert hosts <= {gateway_host, b"github.com", b"agent-plugins.org"}, (n, hosts)
    # Same bytes every time.
    assert chatgpt_plugin.build_zip(REPO_PLUGIN, f"{GATEWAY}/mcp", GATEWAY) == data


@pytest.mark.parametrize(
    ("site", "expected"),
    [
        ("Deck Helper", "deck-helper"),
        ("  Kim's   Cards!! ", "kim-s-cards"),
        ('Deck: "Helper" <b>', "deck-helper-b"),
        ("--Ünïcode..Deck--", "n-code-deck"),
        ("!!!", "mtg-gateway"),
        ("x" * 100, "x" * plugin_page.SLUG_MAX),
        ("A--B..C", "a-b-c"),
    ],
)
def test_owner_name_becomes_a_valid_plugin_name(site: str, expected: str) -> None:
    data = chatgpt_plugin.build_zip(REPO_PLUGIN, f"{GATEWAY}/mcp", GATEWAY, site)
    zf, manifest, mcp = _open(data)
    _validate(manifest, "plugin")
    _validate(mcp, "mcp")
    assert manifest["name"] == expected and NAME_RULE.match(expected) and len(expected) <= 64
    assert list(mcp["mcpServers"]) == [expected]
    assert f"skills/{expected}/SKILL.md" in zf.namelist()
    skill = zf.read(f"skills/{expected}/SKILL.md").decode()
    assert skill.startswith(f"---\nname: {expected}\n")
    ui = manifest["extensions"]["com.openai"]["interface"]
    assert ui["displayName"] == plugin_page.plugin_label(site)
    # The project's own name, homepage and author are gone under an owner's name.
    assert "homepage" not in manifest and manifest["author"] == {"name": plugin_page.plugin_label(site)}
    for n in zf.namelist():
        if not n.endswith(".png"):
            text = zf.read(n).decode()
            for needle in ("MTG Assistant Gateway", "mtg-gateway", "TheUncleBen", "MTG-Assistant-Gateway"):
                assert needle not in text or expected == "mtg-gateway", (n, needle)


async def test_route_serves_this_gateways_plugin_without_sign_in(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(REPO_PLUGIN))
    async with running(Harness(make_settings(tmp_path, server_name="Deck Helper"), idp)) as h:
        r = await h.http.get("/plugin/deck-helper-chatgpt.zip")  # no session cookie, like the Claude ZIP
        assert r.status_code == 200
        assert r.headers["content-type"] == "application/zip"
        assert 'filename="deck-helper-chatgpt.zip"' in r.headers["content-disposition"]
        assert r.headers["etag"] == f'"{hashlib.sha256(r.content).hexdigest()}"'
        _zf, manifest, mcp = _open(r.content)
        assert manifest["name"] == "deck-helper"
        assert mcp["mcpServers"]["deck-helper"]["url"] == f"{GATEWAY}/mcp"
        assert (await h.http.get("/plugin/mtg-gateway-chatgpt.zip")).status_code == 404

        # The setup steps offer it to workspace admins, apart from the connector steps.
        md = (await h.http.get("/install.md?for=chatgpt")).text
        link = f"[Download the ChatGPT plugin (ZIP)]({GATEWAY}/plugin/deck-helper-chatgpt.zip)"
        assert link in md
        # D8: labelled for Business/Enterprise admins, with Plus and Pro sent to the connector
        assert "**ChatGPT plugin file (Business/Enterprise admins only)**" in md
        assert "Plus and Pro accounts have no plugin upload" in md and "they use the connector" in md
        assert md.index("12. Sign in") < md.index(link)  # not one of the numbered connector steps
        b = Browser(h)
        try:
            await b.login("/install")
            page = (await b.http.get("/install?for=chatgpt")).text
        finally:
            await b.aclose()
        assert "href='/plugin/deck-helper-chatgpt.zip'" in page
        assert "Download the ChatGPT plugin (ZIP)" in page
        assert "<h2>ChatGPT plugin file (Business/Enterprise admins only)</h2>" in page


async def test_route_gives_503_without_the_plugin_folder(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(tmp_path / "nowhere"))
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        assert (await h.http.get("/plugin/mtg-gateway-chatgpt.zip")).status_code == 503
