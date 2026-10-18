"""The self-served assistant plugin, and the one ZIP builder every download uses.

The gateway hosts its own plugin marketplace so nobody needs access to the
source repository to connect (the steps are the setup hub's, setup_hub.py):

- ``/plugin/marketplace.json`` is a Claude Code marketplace with one plugin
  whose source is the ZIP archive below, pinned by SHA-256.
- ``/plugin/<name>.zip`` is the plugin from the repository's ``plugin/``
  folder with this gateway's own address written into its connector files.
  ``<name>`` is ``mtg-gateway`` unless the owner set ``MTG_SERVER_NAME``: then the
  plugin, its connector and skills take a name made from it, and the archive drops
  the project's name and homepage, so the public files don't point at the project.

None of this is secret: the plugin holds the public MCP URL and the end-user
skill text, so these routes need no sign-in. They never read the database and
are built once from files shipped in the image (``/usr/share/mtg-gateway/plugin``,
or ``MTG_PLUGIN_DIR``).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, Response

from . import __version__

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

PLUGIN_NAME = "mtg-gateway"
MARKETPLACE_NAME = "mtg-gateway"
# The setup skill's name, so its command is /<plugin>:setup-mtg-gateway whatever the plugin is called.
SETUP_SKILL = "setup-mtg-gateway"
# The product name the plugin's files carry; an owner's MTG_SERVER_NAME replaces it.
DEFAULT_SITE = "MTG Assistant Gateway"
SLUG_MAX = 40
# ChatGPT refuses a plugin file whose displayName is over 80 characters (OpenAI's plugin docs,
# plugin_display_name_too_long, read 2026-10-15; 30 applies only to its public directory).
CHATGPT_NAME_MAX = 80
# OpenAI's package validation: author.name and interface.developerName at most 120 characters
# (plugin_author_name_too_long, plugin_developer_name_too_long; its plugin docs, read 2026-10-16)
DEVELOPER_NAME_MAX = 120
IMAGE_PLUGIN_DIR = Path("/usr/share/mtg-gateway/plugin")
REPO_PLUGIN_DIR = Path(__file__).resolve().parents[2] / "plugin"
MAX_FILE_BYTES = 512 * 1024
ZIP_DATE = (2026, 1, 1, 0, 0, 0)
# The connector files whose ``url`` the gateway fills in with its own address.
CONNECTOR_FILES = (".mcp.json", "mcp.json")
# What an app shows about the connector: MCP ``serverInfo.description`` (app.py), and the text the
# install page offers to copy into ChatGPT's description box. It names no project, so it suits an
# owner's own MTG_SERVER_NAME.
SERVER_DESCRIPTION = (
    "Magic: The Gathering deck research, goldfish testing and safe Archidekt deck edits, "
    "from a self-hosted gateway."
)
# The icon to upload when adding the connector by hand. OpenAI's docs (read 2026-10-14) say an app
# icon is PNG, JPEG, WebP or SVG, at most 5 MiB, square and at least 48x48 pixels. Where the
# 10 KB limit below comes from is unknown: it is not in those docs (docs/CONNECT.md reports it as
# seen in ChatGPT's form). This icon is kept under it anyway (a test checks it); it is first in
# serverInfo.icons too. /static/ needs no sign-in, so it downloads on a phone as well.
CONNECTOR_ICON_PATH = "/static/gateway-icon-connector.png"
CONNECTOR_ICON_PX = 256
CONNECTOR_ICON_MAX_BYTES = 10 * 1024


def plugin_dir() -> Path | None:
    """The folder holding ``mtg-gateway/.claude-plugin/plugin.json``."""
    override = os.environ.get("MTG_PLUGIN_DIR", "").strip()
    candidates = [Path(override)] if override else [IMAGE_PLUGIN_DIR, REPO_PLUGIN_DIR]
    for d in candidates:
        if (d / PLUGIN_NAME / ".claude-plugin" / "plugin.json").is_file():
            return d
    return None


def plugin_slug(site: str) -> str:
    """The plugin's short name: ``mtg-gateway`` under the default name, otherwise the owner's
    name in lower case with every run of other characters turned into one hyphen."""
    if site == DEFAULT_SITE:
        return PLUGIN_NAME
    slug = re.sub(r"[^a-z0-9]+", "-", site.lower()).strip("-")[:SLUG_MAX].strip("-")
    return slug or PLUGIN_NAME


def chatgpt_zip_name(site: str) -> str:
    """The ChatGPT plugin archive's file name (chatgpt_plugin.py builds it)."""
    return f"{plugin_slug(site)}-chatgpt.zip"


def plugin_label(site: str) -> str:
    """The owner's name as the plugin's text may carry it: letters, digits, spaces and . _ ' -
    only, so it can't break the skills' front matter or the JSON it is written into."""
    label = " ".join(re.sub(r"[^A-Za-z0-9 ._'-]+", " ", site).split())
    return label or plugin_slug(site)


def chatgpt_name_from_setting(chatgpt_name: str | None) -> str:
    """The owner's MTG_CHATGPT_NAME through plugin_label's filter; empty when unset or when the
    filter leaves nothing (MTG_SERVER_NAME is used then)."""
    return " ".join(re.sub(r"[^A-Za-z0-9 ._'-]+", " ", chatgpt_name or "").split())


def chatgpt_label(site: str, chatgpt_name: str | None = None) -> str:
    """The name ChatGPT shows for this gateway: the owner's MTG_CHATGPT_NAME when set, else their
    MTG_SERVER_NAME, through plugin_label's filter either way."""
    return chatgpt_name_from_setting(chatgpt_name) or plugin_label(site)


def chatgpt_name_too_long(site: str, chatgpt_name: str | None = None) -> tuple[str, int] | None:
    """The name ChatGPT shows and its length when that is over CHATGPT_NAME_MAX, else None."""
    label = chatgpt_label(site, chatgpt_name)
    return (label, len(label)) if len(label) > CHATGPT_NAME_MAX else None


def cut_at_word(text: str, limit: int) -> str:
    """``text`` cut to ``limit`` characters at the last space before it; when the first ``limit``
    characters hold no space, at ``limit`` itself, inside the word. Trailing spaces and . - _ '
    go too."""
    if len(text) <= limit:
        return text
    head = text[:limit]
    return (head.rsplit(" ", 1)[0] if " " in head else head).rstrip(" .-_'") or head


def developer_name_cut(site: str) -> tuple[int, str] | None:
    """When the owner's MTG_SERVER_NAME, filtered as the plugin file writes it (plugin_label), is
    over DEVELOPER_NAME_MAX: its length and the shortened name the ChatGPT plugin file uses as
    its developer and author name; else None."""
    label = plugin_label(site)
    return (len(label), cut_at_word(label, DEVELOPER_NAME_MAX)) if len(label) > DEVELOPER_NAME_MAX else None


def developer_name_warning(site: str) -> str | None:
    """The owner's notice (admin page, log at start) when the plugin file shortens their name."""
    cut = developer_name_cut(site)
    if not cut:
        return None
    return (
        f"MTG_SERVER_NAME is {cut[0]} characters. OpenAI's plugin check refuses a plugin file whose "
        f"developer name is over {DEVELOPER_NAME_MAX} characters, so the ChatGPT plugin file uses a "
        f"shortened name, “{cut[1]}”, as its developer and author name. To choose the name yourself, "
        f"set MTG_SERVER_NAME to {DEVELOPER_NAME_MAX} characters or fewer."
    )


def _renamed(data: bytes, rel: str, site: str, name: str) -> bytes:
    """A text file of the plugin with the product's name and short name replaced by the
    owner's, and no link to the project's homepage or its author."""
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data
    label = plugin_label(site)
    if rel.endswith(".json"):
        doc = json.loads(text)
        if isinstance(doc, dict):
            doc.pop("homepage", None)
            doc.pop("repository", None)
            if "author" in doc:
                doc["author"] = {"name": DEFAULT_SITE}  # becomes the owner's name below
        text = json.dumps(doc, indent=2) + "\n"
    # The setup skill keeps its name under every owner's name: people type it.
    text = text.replace(SETUP_SKILL, "\0setup\0").replace(DEFAULT_SITE, label).replace(PLUGIN_NAME, name)
    return text.replace("\0setup\0", SETUP_SKILL).encode("utf-8")


def _with_url(raw: bytes, mcp_url: str) -> bytes:
    """Return the connector file with every server's ``url`` set to this gateway."""
    doc = json.loads(raw.decode("utf-8"))
    for server in doc.get("mcpServers", {}).values():
        server["url"] = mcp_url
    return (json.dumps(doc, indent=2) + "\n").encode("utf-8")


# -- the one ZIP builder (the Claude plugin here, the ChatGPT plugin in chatgpt_plugin.py and the
# skill in skill_page.py all go through these two) ------------------------------------------------


def shipped_files(root: Path, *, hidden_files: bool = False, hidden_dirs: tuple[str, ...] = ()) -> list[Path]:
    """The files under ``root`` an archive may carry, sorted: regular files only (symlinks and
    files over ``MAX_FILE_BYTES`` are skipped), and nothing in a hidden folder other than
    ``hidden_dirs`` (a stray ``.git`` must never ship). Hidden files themselves (``.mcp.json``)
    only with ``hidden_files``."""
    out = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            continue
        *dirs, last = path.relative_to(root).parts
        if any(p.startswith(".") and p not in hidden_dirs for p in dirs):
            continue
        if last.startswith(".") and not hidden_files:
            continue
        out.append(path)
    return out


def zip_bytes(entries: Iterable[tuple[str, bytes]]) -> bytes:
    """A ZIP of ``(archive path, content)`` in the order given, with a fixed date and mode, so
    the same files always give the same bytes (a marketplace pins the archive's SHA-256)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel, data in entries:
            info = zipfile.ZipInfo(rel, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, data)
    return buf.getvalue()


def build_zip(base: Path, mcp_url: str, site: str = DEFAULT_SITE) -> bytes:
    """ZIP of ``base/mtg-gateway`` with the folder at the root and the MCP URL filled in.
    Under an owner's own ``site`` name, folders and text are renamed (``_renamed``).

    Only regular files are included; symlinks and oversized files are skipped. Hidden
    folders other than ``.claude-plugin`` are left out (there are none in the repository,
    but a stray ``.git`` must never ship).
    """
    root = base / PLUGIN_NAME
    name = plugin_slug(site)
    custom = site != DEFAULT_SITE

    def entries() -> Iterator[tuple[str, bytes]]:
        for path in shipped_files(root, hidden_files=True, hidden_dirs=(".claude-plugin",)):
            rel_parts = path.relative_to(root).parts
            data = path.read_bytes()
            rel = path.relative_to(base).as_posix()
            if custom:
                data = _renamed(data, rel, site, name)
                rel = "/".join(name if part == PLUGIN_NAME else part for part in rel.split("/"))
            if len(rel_parts) == 1 and rel_parts[0] in CONNECTOR_FILES:
                data = _with_url(data, mcp_url)
            yield rel, data

    return zip_bytes(entries())


def marketplace(public_url: str, owner: str, zip_sha256: str) -> dict:
    """The ``marketplace.json`` document pointing at the served archive."""
    name = plugin_slug(owner)
    return {
        "name": MARKETPLACE_NAME if name == PLUGIN_NAME else name,
        "owner": {"name": owner, "url": public_url},
        "metadata": {
            "description": f"The assistant plugin for {owner}: the gateway connector plus the MTG skill.",
            "version": __version__,
        },
        "plugins": [
            {
                "name": name,
                "description": "Magic: The Gathering research, goldfish simulation and safe Archidekt "
                f"deck edits through this gateway. Run /{name}:{SETUP_SKILL} after installing.",
                "version": __version__,
                "category": "productivity",
                "keywords": ["mtg", "magic-the-gathering", "archidekt", "mcp"],
                "source": {
                    "source": "archive",
                    # Content-addressed so a cached marketplace never pairs with a newer archive.
                    "url": f"{public_url}/plugin/{name}.zip?v={zip_sha256}",
                    "sha256": zip_sha256,
                },
            }
        ],
    }


def claude_connect_link(public_url: str, name: str) -> str:
    """claude.ai's documented install link that pre-fills the Add custom connector dialog."""
    return (
        "https://claude.ai/customize/connectors?modal=add-custom-connector"
        f"&connectorName={quote(name, safe='')}&connectorUrl={quote(public_url + '/mcp', safe='')}"
    )


def add_plugin_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings
    cache: dict[str, tuple[bytes, str]] = {}

    def archive() -> tuple[bytes, str] | None:
        base = plugin_dir()
        if base is None:
            return None
        key = f"{base}|{s.mcp_url}|{s.server_name}"
        hit = cache.get(key)
        if hit is None:
            data = build_zip(base, s.mcp_url, s.server_name)
            hit = cache[key] = (data, hashlib.sha256(data).hexdigest())
        return hit

    static = {"Cache-Control": "public, max-age=300", "X-Content-Type-Options": "nosniff"}

    @server.custom_route("/plugin/marketplace.json", methods=["GET"], include_in_schema=False)
    async def marketplace_json(_request: Request) -> Response:
        hit = archive()
        if hit is None:
            return JSONResponse({"error": "plugin not installed on this gateway"}, status_code=503)
        # Revalidate every time: the digest inside must match the archive served now.
        return JSONResponse(
            marketplace(s.public_url, s.server_name, hit[1]),
            headers={**static, "Cache-Control": "no-cache"},
        )

    zip_name = f"{plugin_slug(s.server_name)}.zip"

    @server.custom_route(f"/plugin/{zip_name}", methods=["GET"], include_in_schema=False)
    async def plugin_zip(_request: Request) -> Response:
        hit = archive()
        if hit is None:
            return PlainTextResponse("plugin not installed on this gateway", status_code=503)
        return Response(
            hit[0],
            media_type="application/zip",
            headers={
                **static,
                "Content-Disposition": f'attachment; filename="{zip_name}"',
                "ETag": f'"{hit[1]}"',
            },
        )


__all__ = [
    "add_plugin_routes",
    "build_zip",
    "chatgpt_zip_name",
    "claude_connect_link",
    "marketplace",
    "plugin_dir",
    "plugin_label",
    "plugin_slug",
    "shipped_files",
    "zip_bytes",
]
