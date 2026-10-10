"""The Android app download page.

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
and the app is only useful to people with an account on it.
"""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import FileResponse, RedirectResponse, Response

from .admin import is_admin
from .pages import _csrf, _safe_next, browser_session
from .theme import render

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


def add_app_routes(server: MCPServer, state: AppState) -> None:
    s = state.settings

    def signed_in(request: Request) -> tuple[str | None, str | None]:
        return browser_session(state, request)

    def to_login(next_path: str) -> Response:
        return RedirectResponse(f"/login?next={_safe_next(next_path)}", status_code=302)

    @server.custom_route("/app", methods=["GET"], include_in_schema=False)
    async def app(request: Request) -> Response:
        sub, sid = signed_in(request)
        if not sub:
            return to_login("/app")
        base = app_dir()
        intro = (
            "<div class='card'><p>The MTG Assistant Gateway app is the Android app for this gateway: "
            "the same pages as here, "
            "full screen, plus a phone-camera scan screen with torch brightness, zoom and exposure. "
            "It is free, has no app store and no account of its own, and on first launch you type this "
            "gateway's address.</p></div>"
        )
        if base is None:
            # No APK is bundled on this gateway: a normal page that says so, not an error page.
            body = (
                intro + "<div class='card'><h2>No app file on this gateway</h2>"
                "<p>Whoever runs this gateway has not added the Android app file yet, so there is nothing "
                "to download here. Everything works in your phone's browser in the meantime: open this "
                "address and add it to your home screen.</p>"
                "<p>If you run the gateway: build the app from the "
                f"<a href='{PROJECT_URL}' rel='noopener'>project repository</a> (docs/ANDROID.md) and put "
                "the file where docs/DEPLOY.md describes; this page then offers it.</p>"
                "<div class='actions'><a class='btn' href='/decks'>Back to decks</a></div></div>"
            )
            return render(
                "Android app",
                body,
                site=s.server_name,
                status=200,
                signed_in=True,
                csrf=_csrf(s, sid),
                admin=is_admin(state, sub),
            )
        meta = app_meta(base)
        version = meta.get("version_name")
        cert = _fingerprint(meta.get("cert_sha256"))
        facts = "".join(
            f"<li>{html.escape(label)}: <code>{html.escape(str(value))}</code></li>"
            for label, value in (
                ("Version", version),
                ("Size", _mb(int(meta["size"]))),
                ("File SHA-256 (download check)", _fingerprint(meta.get("sha256"))),
                ("Signing certificate SHA-256", cert),
            )
            if value
        )
        if cert:
            verify = (
                "<p>Before installing, compare the signing certificate SHA-256 above with the one in the "
                f"<a href='{RELEASES_URL}' rel='noopener'>project's GitHub release</a> for this version "
                "(in the release notes and its <code>mtg-assistant-gateway.json</code>), or with one "
                "the owner of this gateway gave you some other way. If they differ, do not install the "
                "app; tell the owner.</p>"
            )
        else:
            verify = (
                "<p>This build did not record its signing certificate, so the page cannot show one. Ask the "
                "owner of this gateway for the certificate SHA-256 before installing.</p>"
            )
        verify += (
            "<p class='muted small'>Both values on this page come from this gateway, the same place as the "
            "file. The file SHA-256 only shows the download arrived intact; it does not show who made the "
            "app. For a stronger check, on a computer run <code>apksigner verify --print-certs "
            f"{APK_NAME}</code> and compare its certificate SHA-256 digest with the published one.</p>"
        )
        body = (
            intro + "<div class='card'><h2>Install</h2>"
            f"<p><a class='btn btn-primary' href='/app/{APK_NAME}'>Download the app (APK)</a></p>"
            f"<ul>{facts}</ul>"
            f"{verify}"
            "<ol><li>Open the downloaded file. Android asks you to allow installs from your browser "
            "once.</li>"
            "<li>Play Protect may warn that the developer is unknown or unverified: the app is signed by "
            "whoever runs this gateway, not by an app store. Only continue if you downloaded the file from "
            "this page and the signing certificate matches the published one. If Play Protect says the app "
            "is harmful, or you are unsure, do not install it and ask the owner of this gateway.</li>"
            "<li>Open MTG Assistant Gateway and type this gateway's address: "
            f"<code>{html.escape(s.public_url)}</code>.</li>"
            "</ol>"
            "<p class='muted small'>Updates: the app looks for new versions in the project's GitHub "
            "releases and offers them itself; Update installs one over the app you have. Android only "
            "accepts an update signed with the same key as the installed app, so your settings and "
            "sign-in are kept, and a file signed by anyone else is refused. An app from before 0.7.11 "
            "was signed with a test key: uninstall it once and install this file.</p></div>"
        )
        return render(
            "Android app",
            body,
            site=s.server_name,
            signed_in=True,
            csrf=_csrf(s, sid),
            admin=is_admin(state, sub),
        )

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
