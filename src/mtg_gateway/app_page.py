"""The Android app download (its page is the setup hub's Android section, setup_hub.py).

Each gateway can hand out the MTG Assistant Gateway Android app itself, so people get it from the
deployment they use and never need an app store. The image ships the APK that the
release build produced under ``/usr/share/mtg-gateway/app`` (``docker/app-dist`` at
build time); ``MTG_APP_DIR`` overrides the location. Next to the APK the build writes
``mtg-assistant-gateway.json`` with the version, size, the file's SHA-256 and the SHA-256 of the
signing certificate, which the page shows. Both come from the same place as the APK,
so they prove nothing on their own: the file checksum only shows the download is
intact, and the page asks people to compare the certificate fingerprint with the one
published in the project's GitHub release before installing.

Both routes need the browser sign-in, like every other page: the gateway is private,
and the app is only useful to people with an account on it. So does ``/app/latest.json``, the
same facts as JSON: the app's own update check asks its gateway there (with the session the app
already holds) instead of asking GitHub, and downloads the APK from ``/app/<APK_NAME>``.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from .pages import _safe_next, browser_session

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from .app import AppState

APK_NAME = "mtg-assistant-gateway.apk"
META_NAME = "mtg-assistant-gateway.json"
IMAGE_APP_DIR = Path("/usr/share/mtg-gateway/app")
PROJECT_URL = "https://github.com/TheUncleBen/MTG-Assistant-Gateway"
RELEASES_URL = f"{PROJECT_URL}/releases"
MAX_META_BYTES = 16 * 1024
APK_MEDIA_TYPE = "application/vnd.android.package-archive"
LATEST_PATH = "/app/latest.json"


def app_dir() -> Path | None:
    """The folder holding ``mtg-assistant-gateway.apk``, or None when this gateway ships no app."""
    override = os.environ.get("MTG_APP_DIR", "").strip()
    candidates = [Path(override)] if override else [IMAGE_APP_DIR]
    for d in candidates:
        apk = d / APK_NAME
        if apk.is_file() and not apk.is_symlink():
            return d
    return None


def app_meta(base: Path) -> dict[str, Any]:
    """What the build recorded about the APK (``mtg-assistant-gateway.json``), or just its size."""
    meta: dict[str, Any] = {}
    path = base / META_NAME
    if path.is_file() and path.stat().st_size <= MAX_META_BYTES:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = {k: v for k, v in loaded.items() if isinstance(v, (str, int))}
        except (ValueError, OSError):
            meta = {}
    meta.setdefault("size", (base / APK_NAME).stat().st_size)
    return meta


def _mb(size: int) -> str:
    return f"{size / 1_000_000:.1f} MB"


def _fingerprint(value: Any) -> str | None:
    """A SHA-256 as 64 lower-case hex digits (how apksigner and the release notes print it), or None."""
    if not isinstance(value, str):
        return None
    hexdigits = value.replace(":", "").strip().lower()
    if len(hexdigits) != 64 or any(c not in "0123456789abcdef" for c in hexdigits):
        return None
    return hexdigits


def android_facts() -> dict[str, Any] | None:
    """What the setup hub's Android section shows about the app this gateway ships: the build's
    facts as a list (version, size, file and certificate SHA-256), and the certificate alone; None
    when no APK is shipped."""
    base = app_dir()
    if base is None:
        return None
    meta = app_meta(base)
    cert = _fingerprint(meta.get("cert_sha256"))
    facts = "".join(
        f"<li>{html.escape(label)}: <code>{html.escape(str(value))}</code></li>"
        for label, value in (
            ("Version", meta.get("version_name")),
            ("Size", _mb(int(meta["size"]))),
            ("File SHA-256 (download check)", _fingerprint(meta.get("sha256"))),
            ("Signing certificate SHA-256", cert),
        )
        if value
    )
    return {"cert_sha256": cert, "facts_html": f"<ul>{facts}</ul>"}


def add_app_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings

    def signed_in(request: Request) -> tuple[str | None, str | None]:
        return browser_session(state, request)

    def to_login(next_path: str) -> Response:
        return RedirectResponse(f"/login?next={_safe_next(next_path)}", status_code=302)

    @server.custom_route("/app", methods=["GET"], include_in_schema=False)
    async def app(request: Request) -> Response:
        """The Android app's page is the setup hub's Android section (setup_hub.py)."""
        sub, _sid = signed_in(request)
        if not sub:
            return to_login("/app")
        return RedirectResponse("/install?for=android", status_code=302)

    @server.custom_route(LATEST_PATH, methods=["GET"], include_in_schema=False)
    async def latest(request: Request) -> Response:
        """The app this gateway ships, for the Android app's update check (Updater.kt): version,
        size, the file's SHA-256 and the signing certificate's, and where to download it. Signed in
        like the APK itself; a 401 (not a sign-in redirect) tells the app it has no session."""
        headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}
        sub, _sid = signed_in(request)
        if not sub:
            return JSONResponse({"ok": False, "error": "unauthenticated"}, 401, headers=headers)
        base = app_dir()
        if base is None:
            return JSONResponse(
                {"ok": False, "error": "not_found", "message": "This gateway ships no app."},
                404,
                headers=headers,
            )
        meta = app_meta(base)
        version_code = meta.get("version_code")
        out = {
            "ok": True,
            "version_name": meta.get("version_name") if isinstance(meta.get("version_name"), str) else None,
            "version_code": version_code if isinstance(version_code, int) else None,
            "size": (base / APK_NAME).stat().st_size,
            "sha256": _fingerprint(meta.get("sha256")),
            "cert_sha256": _fingerprint(meta.get("cert_sha256")),
            "apk_url": f"{s.public_url}/app/{APK_NAME}",
        }
        return JSONResponse(out, headers=headers)

    @server.custom_route(f"/app/{APK_NAME}", methods=["GET"], include_in_schema=False)
    async def apk(request: Request) -> Response:
        sub, _sid = signed_in(request)
        if not sub:
            return to_login("/app")
        base = app_dir()
        if base is None:
            return Response("app not shipped on this gateway", status_code=404, media_type="text/plain")
        return FileResponse(
            base / APK_NAME,
            media_type=APK_MEDIA_TYPE,
            headers={
                "Content-Disposition": f'attachment; filename="{APK_NAME}"',
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )
