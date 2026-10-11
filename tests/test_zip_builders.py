"""The one ZIP builder (plugin_page.shipped_files and zip_bytes) behind the three downloads: the
Claude plugin, the ChatGPT plugin and the skill. Each archive must be the same, byte for byte, as
the separate builders of 0.7.23 made it (kept below as the reference), so a pinned marketplace
digest and every client that already has the files see no change. The download rules stay too:
the plugins are public, the skill needs a sign-in."""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from mtg_gateway import chatgpt_plugin, plugin_page, skill_page
from mtg_gateway.plugin_page import (
    CONNECTOR_FILES,
    DEFAULT_SITE,
    MAX_FILE_BYTES,
    PLUGIN_NAME,
    ZIP_DATE,
    _renamed,
    _with_url,
    plugin_slug,
)

from .conftest import GATEWAY, FakeIdP, Harness, make_settings, running
from .test_decks_and_proxy import Browser

REPO_PLUGIN = Path(__file__).resolve().parent.parent / "plugin"


# -- the 0.7.23 builders, unchanged, as the reference -------------------------------------------


def _old_claude_zip(base: Path, mcp_url: str, site: str = DEFAULT_SITE) -> bytes:
    root = base / PLUGIN_NAME
    name = plugin_slug(site)
    custom = site != DEFAULT_SITE
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            rel_parts = path.relative_to(root).parts
            if any(p.startswith(".") and p != ".claude-plugin" for p in rel_parts[:-1]):
                continue
            data = path.read_bytes()
            rel = path.relative_to(base).as_posix()
            if custom:
                data = _renamed(data, rel, site, name)
                rel = "/".join(name if part == PLUGIN_NAME else part for part in rel.split("/"))
            if len(rel_parts) == 1 and rel_parts[0] in CONNECTOR_FILES:
                data = _with_url(data, mcp_url)
            info = zipfile.ZipInfo(rel, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


def _old_chatgpt_zip(base: Path, mcp_url: str, public_url: str, site: str = DEFAULT_SITE) -> bytes:
    name = plugin_slug(site)
    files: dict[str, bytes] = {
        "plugin.json": (
            json.dumps(chatgpt_plugin.manifest(base, site, public_url), indent=2) + "\n"
        ).encode(),
        "mcp.json": (json.dumps(chatgpt_plugin.mcp_config(site, mcp_url), indent=2) + "\n").encode(),
    }
    skill = base / PLUGIN_NAME / "skills" / PLUGIN_NAME
    for path in sorted(skill.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        rel = path.relative_to(skill).as_posix()
        if any(part.startswith(".") for part in rel.split("/")):
            continue
        data = path.read_bytes()
        if site != DEFAULT_SITE:
            data = _renamed(data, rel, site, name)
        files[f"skills/{name}/{rel}"] = data
    for rel, src in chatgpt_plugin.ASSETS.items():
        files[rel] = src.read_bytes()
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, data in files.items():
            info = zipfile.ZipInfo(rel, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


def _old_skill_zip(base: Path) -> bytes:
    root = base / "skills" / "mtg-gateway"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(root.rglob("*")):
            if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
                continue
            rel = path.relative_to(root.parent).as_posix()
            if any(part.startswith(".") for part in path.relative_to(root).parts):
                continue
            info = zipfile.ZipInfo(rel, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, path.read_bytes())
    return buf.getvalue()


# -- a plugin folder with every awkward case ----------------------------------------------------


@pytest.fixture
def awkward(tmp_path: Path) -> Path:
    """The repository's plugin plus a hidden folder, a hidden file in the skill, a symlink and an
    oversized file: each builder must skip exactly what it skipped before."""
    import shutil

    base = tmp_path / "plugin"
    shutil.copytree(REPO_PLUGIN, base, symlinks=True)
    root = base / PLUGIN_NAME
    (root / ".git").mkdir()
    (root / ".git" / "config").write_text("[core]\n")
    skill = root / "skills" / PLUGIN_NAME
    (skill / ".DS_Store").write_text("junk")
    (skill / "big.md").write_bytes(b"x" * (MAX_FILE_BYTES + 1))
    secret = tmp_path / "secret.txt"
    secret.write_text("do not ship")
    (skill / "link.md").symlink_to(secret)
    return base


def _names(data: bytes) -> list[str]:
    return zipfile.ZipFile(io.BytesIO(data)).namelist()


@pytest.mark.parametrize("site", [DEFAULT_SITE, "Deck Helper"])
def test_claude_plugin_zip_is_unchanged(awkward: Path, site: str) -> None:
    for base in (REPO_PLUGIN, awkward):
        new = plugin_page.build_zip(base, f"{GATEWAY}/mcp", site)
        assert new == _old_claude_zip(base, f"{GATEWAY}/mcp", site)
    names = _names(plugin_page.build_zip(awkward, f"{GATEWAY}/mcp", site))
    slug = plugin_slug(site)
    assert f"{slug}/.mcp.json" in names and f"{slug}/.claude-plugin/plugin.json" in names
    assert not any("/.git/" in n or n.endswith(("big.md", "link.md")) for n in names)


@pytest.mark.parametrize("site", [DEFAULT_SITE, "Deck Helper"])
def test_chatgpt_plugin_zip_is_unchanged(awkward: Path, site: str) -> None:
    for base in (REPO_PLUGIN, awkward):
        new = chatgpt_plugin.build_zip(base, f"{GATEWAY}/mcp", GATEWAY, site)
        assert new == _old_chatgpt_zip(base, f"{GATEWAY}/mcp", GATEWAY, site)
    names = _names(chatgpt_plugin.build_zip(awkward, f"{GATEWAY}/mcp", GATEWAY, site))
    assert names[:2] == ["plugin.json", "mcp.json"]
    assert not any(n.endswith((".DS_Store", "big.md", "link.md")) for n in names)


def test_skill_zip_is_unchanged(awkward: Path) -> None:
    for base in (REPO_PLUGIN / PLUGIN_NAME, awkward / PLUGIN_NAME):
        assert skill_page.build_zip(base) == _old_skill_zip(base)
    names = _names(skill_page.build_zip(awkward / PLUGIN_NAME))
    assert "mtg-gateway/SKILL.md" in names
    assert not any(n.endswith((".DS_Store", "big.md", "link.md")) for n in names)


async def test_download_rules_are_unchanged(tmp_path: Path, idp: FakeIdP, monkeypatch) -> None:
    """The two plugins download without a sign-in; the skill asks for one first."""
    monkeypatch.setenv("MTG_PLUGIN_DIR", str(REPO_PLUGIN))
    async with running(Harness(make_settings(tmp_path), idp)) as h:
        for path in ("/plugin/mtg-gateway.zip", "/plugin/mtg-gateway-chatgpt.zip"):
            r = await h.http.get(path)
            assert r.status_code == 200 and r.headers["content-type"] == "application/zip", path
        r = await h.http.get("/skill/mtg-gateway.zip")
        assert r.status_code == 302 and r.headers["location"] == "/login?next=/skill"
        b = Browser(h)
        try:
            await b.login("/")
            r = await b.http.get("/skill/mtg-gateway.zip")
            assert r.status_code == 200 and r.content == skill_page.build_zip(REPO_PLUGIN / PLUGIN_NAME)
        finally:
            await b.aclose()
