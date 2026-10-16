"""The ChatGPT plugin: an Agent Plugins ZIP built for this gateway.

OpenAI's plugins use the Agent Plugins format (agent-plugins.org, version 1.0.0), the
same one its Codex loader reads. The archive holds, at its root:

- ``plugin.json``: the manifest. ``$schema`` must be exactly ``PLUGIN_SCHEMA`` and
  ``name`` lowercase letters, digits, dots and hyphens. What ChatGPT shows (name,
  descriptions, icon) sits under ``extensions["com.openai"].interface``.
- ``mcp.json``: the connector, one ``streamable-http`` server with this gateway's MCP
  URL and nothing else. Sign-in needs no fields here: the client finds the gateway's
  OAuth through standard MCP discovery, as the connector does when added by hand.
- ``skills/<name>/``: the MTG skill from the repository's plugin (the Claude setup skill
  is left out: its steps are Claude Code commands).
- ``assets/``: the gateway's own icon.

``/plugin/<name>-chatgpt.zip`` serves it. Like the Claude plugin it is public: it holds
only the public MCP URL and the skill text. Under an owner's own ``MTG_SERVER_NAME`` the
name, text and skill folder follow it, and the project's homepage and author are dropped.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from . import __version__
from .plugin_page import (
    DEFAULT_SITE,
    PLUGIN_NAME,
    SERVER_DESCRIPTION,
    _renamed,
    chatgpt_label,
    chatgpt_zip_name,
    plugin_dir,
    plugin_label,
    plugin_slug,
    shipped_files,
    zip_bytes,
)

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

PLUGIN_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"
STATIC = Path(__file__).parent / "static"
# Archive path -> the gateway's own icon it is copied from.
ASSETS = {
    "assets/icon.png": STATIC / "gateway-icon-connector.png",
    "assets/logo.png": STATIC / "gateway-icon-512.png",
}
# Suggested first messages: at most 3, each at most 128 characters (OpenAI's loader drops the rest).
DEFAULT_PROMPTS = [
    "List my Archidekt decks.",
    "Check my Commander deck's curve, colors and legality.",
    "Goldfish my deck and tell me how fast it is.",
]


DEVELOPER_NAME_MAX = 120


def _cut(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters at the last space before it (mid-word only when one
    word is longer than that)."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    return (head.rsplit(" ", 1)[0] if " " in head else head).rstrip(" .-_'") or head


def manifest(base: Path, site: str, public_url: str, chatgpt_name: str | None = None) -> dict:
    """``plugin.json``: the repository's portable manifest, renamed under an owner's name, with
    the schema, this version and ChatGPT's display fields added. ``displayName`` is the owner's
    MTG_CHATGPT_NAME when set, else their site name (ChatGPT refuses one over 80 characters)."""
    name = plugin_slug(site)
    raw = (base / PLUGIN_NAME / "plugin.json").read_bytes()
    if site != DEFAULT_SITE:
        raw = _renamed(raw, "plugin.json", site, name)
    doc = json.loads(raw)
    # OpenAI's package validation refuses an author.name or interface.developerName over 120
    # characters (plugin_author_name_too_long, plugin_developer_name_too_long; its plugin docs,
    # read 2026-10-16): a long MTG_SERVER_NAME is cut there, at a word, so the file still uploads
    if isinstance(doc.get("author"), dict) and isinstance(doc["author"].get("name"), str):
        doc["author"]["name"] = _cut(doc["author"]["name"], DEVELOPER_NAME_MAX)
    developer = _cut((doc.get("author") or {}).get("name") or plugin_label(site), DEVELOPER_NAME_MAX)
    return {
        "$schema": PLUGIN_SCHEMA,
        "name": name,
        "version": __version__,
        "description": SERVER_DESCRIPTION,
        **{k: doc[k] for k in ("author", "homepage", "keywords") if k in doc},
        "extensions": {
            "com.openai": {
                "interface": {
                    "displayName": chatgpt_label(site, chatgpt_name),
                    "shortDescription": "MTG decks and research",  # OpenAI documents at most 30 characters
                    "longDescription": SERVER_DESCRIPTION
                    + " Every deck change is shown to you first and applied only after you approve.",
                    "developerName": developer,
                    "category": "Productivity",
                    "capabilities": ["Read", "Write"],
                    "websiteURL": public_url,
                    "defaultPrompt": DEFAULT_PROMPTS,
                    "composerIcon": "./assets/icon.png",
                    "logo": "./assets/logo.png",
                }
            }
        },
    }


def mcp_config(site: str, mcp_url: str) -> dict:
    return {
        "$schema": MCP_SCHEMA,
        "mcpServers": {plugin_slug(site): {"type": "streamable-http", "url": mcp_url}},
    }


def build_zip(
    base: Path, mcp_url: str, public_url: str, site: str = DEFAULT_SITE, chatgpt_name: str | None = None
) -> bytes:
    """The ChatGPT plugin archive, with ``plugin.json`` at its root (not inside a folder)."""
    name = plugin_slug(site)
    files: dict[str, bytes] = {
        "plugin.json": (json.dumps(manifest(base, site, public_url, chatgpt_name), indent=2) + "\n").encode(),
        "mcp.json": (json.dumps(mcp_config(site, mcp_url), indent=2) + "\n").encode(),
    }
    skill = base / PLUGIN_NAME / "skills" / PLUGIN_NAME
    for path in shipped_files(skill):
        rel = path.relative_to(skill).as_posix()
        data = path.read_bytes()
        if site != DEFAULT_SITE:
            data = _renamed(data, rel, site, name)
        files[f"skills/{name}/{rel}"] = data
    for rel, src in ASSETS.items():
        files[rel] = src.read_bytes()
    return zip_bytes(files.items())


def add_chatgpt_plugin_route(server: MCPServer, state: AppState) -> None:
    s = state.settings
    cache: dict[str, tuple[bytes, str]] = {}
    filename = chatgpt_zip_name(s.server_name)

    @server.custom_route(f"/plugin/{filename}", methods=["GET"], include_in_schema=False)
    async def chatgpt_zip(_request: Request) -> Response:
        base = plugin_dir()
        if base is None:
            return PlainTextResponse("plugin not installed on this gateway", status_code=503)
        key = f"{base}|{s.mcp_url}|{s.server_name}|{s.chatgpt_name}"
        hit = cache.get(key)
        if hit is None:
            data = build_zip(base, s.mcp_url, s.public_url, s.server_name, s.chatgpt_name)
            hit = cache[key] = (data, hashlib.sha256(data).hexdigest())
        return Response(
            hit[0],
            media_type="application/zip",
            headers={
                "Cache-Control": "public, max-age=300",
                "X-Content-Type-Options": "nosniff",
                "Content-Disposition": f'attachment; filename="{filename}"',
                "ETag": f'"{hit[1]}"',
            },
        )


__all__ = ["add_chatgpt_plugin_route", "build_zip", "manifest", "mcp_config"]
