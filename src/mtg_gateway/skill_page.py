"""The assistant skill download page.

Signed-in people can download the Claude skill as a ZIP and copy the ChatGPT
instructions. Both live in the assistant plugin, ``plugin/mtg-gateway`` in the
repository (``skills/mtg-gateway/SKILL.md`` and ``chatgpt-instructions.md``),
which the image copies under ``/usr/share/mtg-gateway/plugin``; ``MTG_PLUGIN_DIR``
overrides the location, as it does for the plugin routes. The ZIP is built in
memory on first request and cached.
"""

from __future__ import annotations

import html
import io
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .pages import _csrf, _safe_next, browser_session
from .plugin_page import PLUGIN_NAME, plugin_dir
from .theme import render

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

SKILL_NAME = "mtg-gateway"
# Inside the plugin root: the skill folder and the ChatGPT text.
SKILLS_SUBDIR = "skills"
MAX_FILE_BYTES = 512 * 1024
# Fixed timestamp so the same files always give the same ZIP bytes.
ZIP_DATE = (2026, 1, 1, 0, 0, 0)


def skill_dir() -> Path | None:
    """The plugin root holding ``skills/mtg-gateway/SKILL.md`` and ``chatgpt-instructions.md``."""
    base = plugin_dir()
    if base is None:
        return None
    root = base / PLUGIN_NAME
    return root if (root / SKILLS_SUBDIR / SKILL_NAME / "SKILL.md").is_file() else None


def build_zip(base: Path) -> bytes:
    """ZIP of ``base/skills/mtg-gateway`` with the folder at the root, as Claude's upload expects.

    Only regular files are included; symlinks and oversized files are skipped.
    """
    root = base / SKILLS_SUBDIR / SKILL_NAME
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


def chatgpt_text(base: Path) -> str | None:
    """The fenced ```text block from ``chatgpt-instructions.md``."""
    path = base / "chatgpt-instructions.md"
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        return None
    text = path.read_text(encoding="utf-8")
    if "```text\n" not in text:
        return None
    return text.split("```text\n", 1)[1].split("```", 1)[0].strip()


def add_skill_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings
    cache: dict[str, bytes] = {}

    def signed_in(request: Request) -> tuple[str | None, str | None]:
        return browser_session(state, request)

    def to_login(next_path: str) -> Response:
        return RedirectResponse(f"/login?next={_safe_next(next_path)}", status_code=302)

    @server.custom_route("/skill", methods=["GET"], include_in_schema=False)
    async def skill(request: Request) -> Response:
        sub, sid = signed_in(request)
        if not sub:
            return to_login("/skill")
        base = skill_dir()
        if base is None:
            body = "<div class='card'><p>The assistant skill is not installed on this gateway.</p></div>"
            return render(
                "Assistant skill", body, site=s.server_name, status=503, signed_in=True, csrf=_csrf(s, sid)
            )
        gpt = chatgpt_text(base)
        gpt_block = (
            "<p>Select all the text in the box and copy it.</p>"
            "<textarea readonly rows='14' class='mono' aria-label='ChatGPT project instructions'>"
            f"{html.escape(gpt)}</textarea>"
            if gpt
            else "<p>The ChatGPT instructions are not available on this gateway.</p>"
        )
        body = (
            "<div class='card'><p>The MTG skill teaches your assistant to use this gateway well: which tool "
            "to use, how to report simulations honestly, and to show you every deck change before making "
            "it. It is optional but recommended.</p></div>"
            "<div class='card'><h2>Claude</h2>"
            "<p><a class='btn btn-primary' href='/skill/mtg-gateway.zip'>Download the skill (ZIP)</a></p>"
            "<ol><li>Turn on Code execution and file creation in Claude's settings "
            "(Settings, Capabilities).</li>"
            "<li>On claude.ai or in the desktop app, open Customize, then Skills.</li>"
            "<li>Click +, then Create skill, then Upload a skill, and choose the ZIP. Do not unzip it.</li>"
            "</ol></div>"
            "<div class='card'><h2>ChatGPT</h2>"
            "<p>Create a project, open its Project settings, and paste this into the project's "
            "instructions. Then chat inside that project with the MTG Assistant Gateway connector on.</p>"
            "<p class='muted small'>ChatGPT supports custom connectors on the web only, not in its phone "
            "apps.</p>"
            f"{gpt_block}</div>"
        )
        return render("Assistant skill", body, site=s.server_name, signed_in=True, csrf=_csrf(s, sid))

    @server.custom_route("/skill/mtg-gateway.zip", methods=["GET"], include_in_schema=False)
    async def skill_zip(request: Request) -> Response:
        sub, _sid = signed_in(request)
        if not sub:
            return to_login("/skill")
        base = skill_dir()
        if base is None:
            return Response("skill not installed", status_code=503, media_type="text/plain")
        data = cache.get(str(base))
        if data is None:
            data = cache[str(base)] = build_zip(base)
        return Response(
            data,
            media_type="application/zip",
            headers={
                "Content-Disposition": f'attachment; filename="{SKILL_NAME}.zip"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )


__all__ = ["add_skill_routes", "build_zip", "chatgpt_text", "skill_dir"]
