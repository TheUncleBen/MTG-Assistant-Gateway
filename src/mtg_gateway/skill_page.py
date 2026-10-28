"""The assistant skill download.

Signed-in people download the Claude skill as a ZIP here; its steps and the ChatGPT instructions
to copy are the setup hub's skill section (``/install?for=skill``, setup_hub.py), where ``/skill``
leads. Both live in the assistant plugin, ``plugin/mtg-gateway`` in the repository
(``skills/mtg-gateway/SKILL.md`` and ``chatgpt-instructions.md``), which the image copies under
``/usr/share/mtg-gateway/plugin``, as for the plugin routes. The ZIP is built in memory on first
request (plugin_page's ZIP builder) and cached.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from .pages import _safe_next, browser_session
from .plugin_page import MAX_FILE_BYTES, PLUGIN_NAME, plugin_dir, shipped_files, zip_bytes

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

SKILL_NAME = "mtg-gateway"
# Inside the plugin root: the skill folder and the ChatGPT text.
SKILLS_SUBDIR = "skills"


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
    return zip_bytes((p.relative_to(root.parent).as_posix(), p.read_bytes()) for p in shipped_files(root))


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
    cache: dict[str, bytes] = {}

    def signed_in(request: Request) -> tuple[str | None, str | None]:
        return browser_session(state, request)

    def to_login(next_path: str) -> Response:
        return RedirectResponse(f"/login?next={_safe_next(next_path)}", status_code=302)

    @server.custom_route("/skill", methods=["GET"], include_in_schema=False)
    async def skill(request: Request) -> Response:
        """The skill's steps are the setup hub's skill section (setup_hub.py), which reads signed
        out too; the download itself needs a sign-in."""
        return RedirectResponse("/install?for=skill", status_code=302)

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
