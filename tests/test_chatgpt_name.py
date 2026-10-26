"""The ChatGPT connector name follows the gateway name (the Settings page's "Gateway name"):
filtered as the plugin's files carry it and cut at a word to 80 characters, because ChatGPT
refuses a plugin file whose name is longer (OpenAI's plugin docs). The plugin file and the
install steps use that same name, so a long gateway name never needs a warning."""

from __future__ import annotations

import html
import io
import json
import zipfile
from pathlib import Path

import pytest

from mtg_gateway import chatgpt_plugin
from mtg_gateway.plugin_page import CHATGPT_NAME_MAX, chatgpt_label, plugin_label

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser

REPO_PLUGIN = Path(__file__).resolve().parent.parent / "plugin"

# 85 characters once filtered, with markup in it.
HOSTILE = 'Ben\'s <b>"Deck"</b> & Helper Gateway of Many Cards for the Thursday Commander Night Pod Crew'
HOSTILE_LABEL = "Ben's b Deck b Helper Gateway of Many Cards for the Thursday Commander Night Pod Crew"
HOSTILE_CUT = "Ben's b Deck b Helper Gateway of Many Cards for the Thursday Commander Night Pod"


def test_the_limit_is_80() -> None:
    assert CHATGPT_NAME_MAX == 80


def test_the_name_is_the_gateway_name_filtered_and_cut_at_a_word() -> None:
    assert chatgpt_label("MTG Assistant Gateway") == "MTG Assistant Gateway"
    assert chatgpt_label("Deck Helper™ & Co!") == "Deck Helper Co"
    assert plugin_label(HOSTILE) == HOSTILE_LABEL and len(HOSTILE_LABEL) == 85
    assert chatgpt_label(HOSTILE) == HOSTILE_CUT and len(HOSTILE_CUT) <= 80
    assert chatgpt_label("x" * 81) == "x" * 80  # no space to cut at: cut inside the word
    # a word that ends right at the limit is kept
    assert chatgpt_label("a" * 75 + " word more") == "a" * 75 + " word"


@pytest.mark.parametrize(
    "site,shown",
    [
        ("MTG Assistant Gateway", "MTG Assistant Gateway"),
        ("Deck Helper", "Deck Helper"),
        (HOSTILE, HOSTILE_CUT),
    ],
)
def test_manifest_display_name(site: str, shown: str) -> None:
    doc = chatgpt_plugin.manifest(REPO_PLUGIN, site, GATEWAY)
    assert doc["extensions"]["com.openai"]["interface"]["displayName"] == shown
    data = chatgpt_plugin.build_zip(REPO_PLUGIN, f"{GATEWAY}/mcp", GATEWAY, site)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        written = json.loads(z.read("plugin.json"))
    assert written["extensions"]["com.openai"]["interface"]["displayName"] == shown


def test_a_long_gateway_name_is_cut_where_openai_limits_the_developer_name() -> None:
    """OpenAI's package validation refuses an author.name or developerName over 120 characters, so
    a name that long is cut there at a word and the plugin file still uploads."""
    site = " ".join(["Commander"] * 14)  # 139 characters
    ui = chatgpt_plugin.manifest(REPO_PLUGIN, site, GATEWAY)["extensions"]["com.openai"]["interface"]
    assert ui["developerName"] == " ".join(["Commander"] * 12) and len(ui["developerName"]) <= 120
    assert len(ui["displayName"]) <= 80
    short = chatgpt_plugin.manifest(REPO_PLUGIN, "Deck Helper", GATEWAY)
    assert short["extensions"]["com.openai"]["interface"]["developerName"] == short["author"]["name"]


async def test_route_serves_the_gateway_name(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mtg_gateway.plugin_page.PLUGIN_DIRS", (REPO_PLUGIN,))
    async with running(Harness(make_settings(tmp_path, server_name="Deck Helper"), idp)) as h:
        r = await h.http.get("/plugin/deck-helper-chatgpt.zip")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            doc = json.loads(z.read("plugin.json"))
    assert doc["name"] == "deck-helper"
    assert doc["extensions"]["com.openai"]["interface"]["displayName"] == "Deck Helper"


async def _install(h: Harness) -> tuple[str, str]:
    b = Browser(h)
    try:
        await b.login("/install")
        page = (await b.http.get("/install?for=chatgpt")).text
    finally:
        await b.aclose()
    md = (await h.http.get("/install.md?for=chatgpt")).text
    return page, md


async def test_install_steps_type_the_same_name_as_the_plugin_file(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("mtg_gateway.plugin_page.PLUGIN_DIRS", (REPO_PLUGIN,))
    async with running(Harness(make_settings(tmp_path, server_name=HOSTILE), idp)) as h:
        page, md = await _install(h)
    assert f"readonly value='{html.escape(HOSTILE_CUT)}'>" in page
    assert f"Name: `{HOSTILE_CUT}`" in md
    assert f"Use “{HOSTILE_CUT}” and the description below." in md
    assert f"Chat inside that project with the {HOSTILE_CUT} connector on." in md
    assert "refuses a plugin file" not in page + md
