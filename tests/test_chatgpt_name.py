"""MTG_CHATGPT_NAME: the ChatGPT connector name, when it should differ from MTG_SERVER_NAME. It is
the ChatGPT plugin's displayName and the name the ChatGPT install steps tell people to type; nothing
else changes. ChatGPT refuses a plugin file whose name is over 80 characters (OpenAI's plugin docs).
A longer name never stops the gateway (Q115): it is warned about in the log at start, on the admin
page, and on the install page's plugin-file step."""

from __future__ import annotations

import html
import io
import json
import logging
import zipfile
from pathlib import Path

import pytest

from mtg_gateway import chatgpt_plugin, setup_hub
from mtg_gateway.config import chatgpt_name_warning, load_settings
from mtg_gateway.plugin_page import CHATGPT_NAME_MAX, chatgpt_label, chatgpt_name_too_long, plugin_label

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from .test_admin import NAVIGATE, _with_admin, admin_browser
from .test_backup_and_config import _write_secrets
from .test_decks_and_proxy import Browser

REPO_PLUGIN = Path(__file__).resolve().parent.parent / "plugin"

# 85 characters once filtered, with markup in it.
HOSTILE = 'Ben\'s <b>"Deck"</b> & Helper Gateway of Many Cards for the Thursday Commander Night Pod Crew'
HOSTILE_LABEL = "Ben's b Deck b Helper Gateway of Many Cards for the Thursday Commander Night Pod Crew"


def _install_warning(name: str) -> str:
    return (
        f"This gateway's ChatGPT connector name, “{name}”, is {len(name)} characters, and ChatGPT refuses "
        "a plugin file whose name is over 80 characters. Add the connector by hand with the steps above "
        "instead, or ask the gateway's owner to shorten the name."
    )


def _admin_warning(name: str) -> str:
    return (
        f"The ChatGPT connector name, “{name}”, is {len(name)} characters; ChatGPT refuses a plugin file "
        "whose name is over 80 characters. Set a shorter MTG_CHATGPT_NAME (see docs/DEPLOY.md)."
    )


def _log_warning(n: int) -> str:
    return (
        f"MTG_CHATGPT_NAME is {n} characters; ChatGPT refuses a plugin file whose name is over 80 "
        "characters. The gateway runs, but set a shorter name, or leave it empty to use MTG_SERVER_NAME "
        "if that is 80 characters or fewer."
    )


def _log_warning_server(n: int) -> str:
    return (
        f"MTG_SERVER_NAME is {n} characters and MTG_CHATGPT_NAME is empty, so the ChatGPT connector name "
        "is too long: ChatGPT refuses a plugin file whose name is over 80 characters. The gateway runs, "
        "but set MTG_CHATGPT_NAME to a name of 80 characters or fewer."
    )


# -- config ---------------------------------------------------------------------------------------


def test_the_limit_is_80() -> None:
    assert CHATGPT_NAME_MAX == 80
    assert len(HOSTILE_LABEL) == 85 and plugin_label(HOSTILE) == HOSTILE_LABEL


def test_unset_means_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.delenv("MTG_CHATGPT_NAME", raising=False)
    assert load_settings().chatgpt_name is None
    monkeypatch.setenv("MTG_CHATGPT_NAME", "   ")
    assert load_settings().chatgpt_name is None


def test_set_is_kept_and_server_name_is_untouched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_SERVER_NAME", "The Very Long Name Of Our Deck Gateway")
    monkeypatch.setenv("MTG_CHATGPT_NAME", "  Deck Helper  ")
    s = load_settings()
    assert s.chatgpt_name == "Deck Helper"
    assert s.server_name == "The Very Long Name Of Our Deck Gateway"
    assert chatgpt_name_warning(s) is None
    monkeypatch.setenv("MTG_CHATGPT_NAME", "x" * 80)  # exactly the limit: no warning
    s = load_settings()
    assert s.chatgpt_name == "x" * 80 and chatgpt_name_warning(s) is None


def test_over_80_characters_only_warns(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _write_secrets(tmp_path, monkeypatch)
    monkeypatch.setenv("MTG_CHATGPT_NAME", "y" * 81)
    s = load_settings()  # never a ConfigError: the gateway starts
    assert s.chatgpt_name == "y" * 81
    assert chatgpt_name_warning(s) == _log_warning(81)


@pytest.mark.parametrize(
    "site,name,logged",
    [
        (None, "y" * 81, _log_warning(81)),
        (None, "y" * 80, None),
        (None, None, None),
        ("s" * 81, None, _log_warning_server(81)),
        ("s" * 81, "Short", None),
        # a setting the filter empties falls back to MTG_SERVER_NAME, and the warning says so
        ("s" * 81, "!!!", _log_warning_server(81)),
        # counted as the plugin file writes it, as the admin page counts
        (None, "y" * 79 + "!!!!", None),
    ],
)
def test_the_warning_is_logged_at_start(
    site: str | None,
    name: str | None,
    logged: str | None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from mtg_gateway.__main__ import main

    _write_secrets(tmp_path, monkeypatch)
    if site is None:
        monkeypatch.delenv("MTG_SERVER_NAME", raising=False)
    else:
        monkeypatch.setenv("MTG_SERVER_NAME", site)
    if name is None:
        monkeypatch.delenv("MTG_CHATGPT_NAME", raising=False)
    else:
        monkeypatch.setenv("MTG_CHATGPT_NAME", name)
    loggers = [logging.getLogger(n) for n in ("httpx", "httpcore")]
    before = [lg.level for lg in loggers]
    try:
        with caplog.at_level(logging.WARNING, logger="mtg_gateway.config"):
            assert main(["check-config"]) == 0
    finally:
        for lg, lvl in zip(loggers, before, strict=True):
            lg.setLevel(lvl)
    warnings = [r for r in caplog.records if r.name == "mtg_gateway.config" and r.levelno == logging.WARNING]
    assert [r.getMessage() for r in warnings] == ([logged] if logged else [])


def test_the_effective_name_and_its_filter() -> None:
    assert chatgpt_label("MTG Assistant Gateway") == "MTG Assistant Gateway"
    assert chatgpt_label("MTG Assistant Gateway", "Deck Helper™ & Co!") == "Deck Helper Co"
    # a name the filter empties falls back to the site's name
    assert chatgpt_label("MTG Assistant Gateway", "!!!") == "MTG Assistant Gateway"
    assert chatgpt_label(HOSTILE) == HOSTILE_LABEL == plugin_label(HOSTILE)
    assert chatgpt_name_too_long("x" * 80) is None
    assert chatgpt_name_too_long("x" * 81) == ("x" * 81, 81)
    assert chatgpt_name_too_long("x" * 81, "Short") is None
    assert chatgpt_name_too_long("Short", "z" * 81) == ("z" * 81, 81)


# -- the plugin file ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "site,gpt,shown",
    [
        ("MTG Assistant Gateway", None, "MTG Assistant Gateway"),
        ("Deck Helper", None, "Deck Helper"),
        (HOSTILE, None, HOSTILE_LABEL),
        ("The Very Long Name Of Our Deck Gateway", "Deck Helper!", "Deck Helper"),
    ],
)
def test_manifest_display_name(site: str, gpt: str | None, shown: str) -> None:
    doc = chatgpt_plugin.manifest(REPO_PLUGIN, site, GATEWAY, gpt)
    assert doc["extensions"]["com.openai"]["interface"]["displayName"] == shown
    # the plugin's name (and so its folder and file names) still follow MTG_SERVER_NAME
    assert doc["name"] == chatgpt_plugin.manifest(REPO_PLUGIN, site, GATEWAY)["name"]
    data = chatgpt_plugin.build_zip(REPO_PLUGIN, f"{GATEWAY}/mcp", GATEWAY, site, gpt)
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        written = json.loads(z.read("plugin.json"))
    assert written["extensions"]["com.openai"]["interface"]["displayName"] == shown


async def test_route_serves_the_chatgpt_name(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(REPO_PLUGIN))
    settings = make_settings(tmp_path, server_name="Deck Helper", chatgpt_name="Decks")
    async with running(Harness(settings, idp)) as h:
        r = await h.http.get("/plugin/deck-helper-chatgpt.zip")
        assert r.status_code == 200
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            doc = json.loads(z.read("plugin.json"))
        assert doc["name"] == "deck-helper"
        assert doc["extensions"]["com.openai"]["interface"]["displayName"] == "Decks"


# -- the install page and /install.md -------------------------------------------------------------


async def _install(h: Harness) -> tuple[str, str]:
    b = Browser(h)
    try:
        await b.login("/install")
        page = (await b.http.get("/install?for=chatgpt")).text
    finally:
        await b.aclose()
    md = (await h.http.get("/install.md?for=chatgpt")).text
    return page, md


async def test_install_uses_the_chatgpt_name_only_where_chatgpt_shows_it(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(REPO_PLUGIN))
    site = "The Very Long Name Of Our Deck Gateway"
    settings = make_settings(tmp_path, server_name=site, chatgpt_name="Deck Helper & Co")
    async with running(Harness(settings, idp)) as h:
        page, md = await _install(h)
    assert "<input id='gpt-name' type='text' readonly value='Deck Helper Co'>" in page
    assert "Use “Deck Helper Co” and the description below." in page
    assert "Name: `Deck Helper Co`" in md
    assert "Use “Deck Helper Co” and the description below." in md
    # the connector the member made in ChatGPT carries the ChatGPT name; everything else keeps
    # MTG_SERVER_NAME
    assert "Chat inside that project with the Deck Helper Co connector on." in md
    assert f"Chat inside that project with the {site} connector on." not in md
    assert f"# Install the {site} assistant plugin" in md
    # no warning: the name ChatGPT shows is short enough
    assert "notice warn" not in page and "refuses a plugin file" not in page + md


async def test_install_without_a_chatgpt_name_is_as_before(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(REPO_PLUGIN))
    async with running(Harness(make_settings(tmp_path, server_name="Deck Helper"), idp)) as h:
        page, md = await _install(h)
    assert "<input id='gpt-name' type='text' readonly value='Deck Helper'>" in page
    assert "Use “Deck Helper” and the description below." in page
    assert "Name: `Deck Helper`" in md
    assert "refuses a plugin file" not in page + md


async def test_install_warns_on_the_plugin_file_step_escaped(
    tmp_path: Path, idp: FakeIdP, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(REPO_PLUGIN))
    async with running(Harness(make_settings(tmp_path, server_name=HOSTILE), idp)) as h:
        page, md = await _install(h)
    warn = _install_warning(HOSTILE_LABEL)
    # on the page: a notice in the "ChatGPT plugin file" card, under its text, before the download
    note = f"<p class='notice warn stepnote'>{html.escape(warn, quote=False)}</p>"
    assert page.count(note) == 1
    card = page.index("ChatGPT plugin file (Business/Enterprise admins only)")
    assert card < page.index(note) < page.index("Download the ChatGPT plugin (ZIP)", card)
    # the hand-add Name box gets no warning
    name_box = page.index("id='gpt-name'")
    assert "notice warn" not in page[name_box : page.index("id='gpt-desc'")]
    # the owner's markup never reaches the page as markup
    assert '<b>"Deck"' not in page and "&lt;b&gt;&quot;Deck&quot;&lt;/b&gt;" in page  # the Name box
    # in the Markdown: in the plugin-file block, before its download link
    block = md.index("**ChatGPT plugin file (Business/Enterprise admins only)**")
    assert block < md.index(warn) < md.index("[Download the ChatGPT plugin (ZIP)]", block)
    assert md.count(warn) == 1
    assert f"Name: `{HOSTILE}`\n" in md


def _plugin_block(ctx: setup_hub.Context) -> setup_hub.Block:
    sec = setup_hub.section("chatgpt", ctx)
    return next(b for b in sec.blocks if b.title and b.title.startswith("ChatGPT plugin file"))


def test_the_plugin_file_step_carries_the_warning_only_when_over_80() -> None:
    assert _plugin_block(setup_hub.Context(GATEWAY, "x" * 80)).warn is None
    assert _plugin_block(setup_hub.Context(GATEWAY, "x" * 81)).warn == _install_warning("x" * 81)
    assert _plugin_block(setup_hub.Context(GATEWAY, "x" * 81, gpt_name="Short")).warn is None
    assert _plugin_block(setup_hub.Context(GATEWAY, "Short", gpt_name="z" * 81)).warn == _install_warning(
        "z" * 81
    )
    # no step of the section carries a warning any more
    sec = setup_hub.section("chatgpt", setup_hub.Context(GATEWAY, "x" * 81))
    assert all(s.warn is None for b in sec.blocks for s in b.steps)


# -- the admin page -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "site,gpt,shown",
    [
        (HOSTILE, None, HOSTILE_LABEL),
        ("x" * 81, None, "x" * 81),
        ("x" * 80, None, None),
        ("Short", "z" * 81, "z" * 81),
        ("MTG Assistant Gateway", None, None),
        (HOSTILE, "Deck Helper", None),
    ],
)
async def test_admin_warns_about_a_long_chatgpt_name(
    tmp_path: Path, idp: FakeIdP, site: str, gpt: str | None, shown: str | None
) -> None:
    settings = make_settings(
        tmp_path, required_group="mtg-users", admin_group="mtg-admins", server_name=site, chatgpt_name=gpt
    )
    async with running(_with_admin(Harness(settings, idp))) as h:
        b = await admin_browser(h)
        try:
            r = await b.http.get("/admin", headers=NAVIGATE)
        finally:
            await b.aclose()
    assert r.status_code == 200
    if shown is None:
        assert "MTG_CHATGPT_NAME" not in r.text
    else:
        assert f"<div class='notice warn'>{html.escape(_admin_warning(shown))}</div>" in r.text
        assert '<b>"Deck"' not in r.text
