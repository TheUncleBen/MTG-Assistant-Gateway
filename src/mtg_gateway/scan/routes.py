"""Browser side of scanning: the /scan page, its JSON API and self-hosted assets.

Security model, same as the other pages (pages.py): the gateway's browser
session cookie identifies the user, and every state-changing request carries
the session-bound CSRF token, here as the ``X-CSRF-Token`` header. GET never
changes state.

The scan page is the only page that runs script, so it gets its own Content
Security Policy instead of the site-wide ``default-src 'none'``:

* ``script-src 'self' 'wasm-unsafe-eval'`` — the page script, the Tesseract.js
  worker and its WebAssembly core are served from this origin under
  /scan/static; ``wasm-unsafe-eval`` is what lets a same-origin script
  instantiate WebAssembly. No CDN is ever referenced.
* ``worker-src 'self'`` — the OCR runs in a dedicated Worker loaded by URL
  (``workerBlobURL: false``), so no blob: URLs are needed.
* ``connect-src 'self'`` — the page talks only to this gateway; Scryfall is
  reached server-side (scryfall.py), never from the phone.
* ``img-src 'self' blob: data: https://cards.scryfall.io`` — card images are
  shown straight from Scryfall's image host; blob:/data: are the captured frames.
* ``media-src 'self' blob:`` — the live camera preview is a MediaStream.

Dedicated workers take their CSP from their own script's response, so the same
policy is sent with every /scan/static file.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import mimetypes
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, RedirectResponse, Response

from ..pages import _csrf, _safe_next, browser_session, read_limited
from ..theme import render
from .service import ScanError, ScanService, parse_cards

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer

    from ..app import AppState

STATIC_DIR = Path(__file__).parent / "static"
SCAN_CSP = (
    "default-src 'none'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; "
    "style-src 'self' 'unsafe-inline'; img-src 'self' blob: data: https://cards.scryfall.io; "
    "connect-src 'self'; media-src 'self' blob:; manifest-src 'self'; form-action 'self'; "
    "base-uri 'none'; frame-ancestors 'none'"
)
MAX_JSON = 2_000_000
mimetypes.add_type("application/wasm", ".wasm")
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("application/octet-stream", ".traineddata")


# Per-user JSON is never stored by a browser or proxy; nosniff on every API reply.
NO_STORE = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"}


def _asset_version(static_dir: Path = STATIC_DIR) -> str:
    """Short digest of every file under static/ (the vendored OCR engine included), used to
    bust the browser and service-worker caches after a deploy changes any of them."""
    h = hashlib.sha256()
    for p in sorted(static_dir.rglob("*")):
        if p.is_file():
            h.update(p.relative_to(static_dir).as_posix().encode() + bytes(1))
            h.update(p.read_bytes())
    return h.hexdigest()[:12]


def ocr_assets_present() -> bool:
    vendor = STATIC_DIR / "vendor"
    return (vendor / "worker.min.js").exists() and (vendor / "lang" / "eng.traineddata").exists()


def add_scan_routes(server: MCPServer, state: AppState, service: ScanService) -> None:
    s = state.settings
    version = _asset_version()

    def current(request: Request) -> tuple[str | None, str | None]:
        return browser_session(state, request)

    def api_user(request: Request, *, write: bool) -> tuple[str, str] | Response:
        sub, sid = current(request)
        if not sub or not sid:
            return JSONResponse(
                {"ok": False, "error": "unauthenticated", "login": "/login?next=/scan"}, 401, headers=NO_STORE
            )
        if write:
            expected = _csrf(s, sid) or ""
            given = request.headers.get("x-csrf-token", "")
            if not expected or not _eq(given, expected):
                return JSONResponse(
                    {"ok": False, "error": "csrf", "message": "Reload the page and retry."},
                    403,
                    headers=NO_STORE,
                )
        return sub, sid

    async def body(request: Request) -> dict[str, Any] | Response:
        if not request.headers.get("content-type", "").startswith("application/json"):
            return JSONResponse(
                {"ok": False, "error": "invalid", "message": "send JSON"}, 415, headers=NO_STORE
            )
        raw = await read_limited(request, MAX_JSON)
        if raw is None:
            return JSONResponse({"ok": False, "error": "too_large"}, 413, headers=NO_STORE)
        try:
            data = json.loads(raw)
        except (ValueError, RecursionError):  # RecursionError: deeply nested arrays or objects
            return JSONResponse(
                {"ok": False, "error": "invalid", "message": "bad JSON"}, 400, headers=NO_STORE
            )
        if not isinstance(data, dict):
            return JSONResponse(
                {"ok": False, "error": "invalid", "message": "expected an object"}, 400, headers=NO_STORE
            )
        return data

    def fail(exc: ScanError) -> Response:
        codes = {"invalid": 400, "not_found": 404, "unavailable": 503, "rate_limited": 503, "busy": 429}
        code = codes.get(exc.kind, 400)
        headers = {}
        if exc.kind == "rate_limited":
            headers["Retry-After"] = str(max(1, service.scryfall.retry_in()))
        return JSONResponse(
            {"ok": False, "error": exc.kind, "message": str(exc)}, code, headers={**NO_STORE, **headers}
        )

    # -- page ---------------------------------------------------------------
    @server.custom_route("/scan", methods=["GET"], include_in_schema=False)
    async def scan_page(request: Request) -> Response:
        sub, sid = current(request)
        if not sub:
            return RedirectResponse(f"/login?next={_safe_next('/scan')}", status_code=302)
        user = state.db.get_user(sub) or {}
        who = user.get("preferred_username") or user.get("email") or sub
        config = {
            "csrf": _csrf(s, sid),
            "user": who,
            # Opaque per-user value: the page's local draft is keyed by it, so a draft
            # cannot show up for another account on a shared device.
            "draft": _draft_key(s, sub),
            "ocr": ocr_assets_present(),
            "version": version,
            "static": "/scan/static",
            "thresholds": service.t.as_dict(),
        }
        body_html = (
            "<link rel='stylesheet' href='/scan/static/scan.css?v=" + version + "'>"
            "<link rel='manifest' href='/scan/app.webmanifest'>"
            f"<script id='scan-config' type='application/json'>{_json_for_html(config)}</script>"
            "<noscript><div class='notice error'>Scanning needs JavaScript.</div></noscript>"
            "<div id='scan-app' class='scan'>"
            "<div class='notice' id='scan-loading'>Loading the scanner…</div></div>"
            f"<script src='/scan/static/geometry.js?v={version}' defer></script>"
            f"<script src='/scan/static/scan-art.js?v={version}' defer></script>"
            f"<script src='/scan/static/scan.js?v={version}' defer></script>"
        )
        user_admin = bool(s.admin_group and s.admin_group in (user.get("groups") or []))
        resp = render(
            "Scan cards",
            body_html,
            site=s.server_name,
            signed_in=True,
            csrf=_csrf(s, sid),
            current="/scan",
            admin=user_admin,
        )
        resp.headers["Content-Security-Policy"] = SCAN_CSP
        resp.headers["Permissions-Policy"] = "camera=(self)"
        return resp

    @server.custom_route("/scan/app.webmanifest", methods=["GET"], include_in_schema=False)
    async def manifest(_request: Request) -> Response:
        data = {
            "name": f"{s.server_name} Scanner",
            "short_name": "Card Scan",
            "description": "Scan physical Magic cards into your deck gateway.",
            "id": "/scan",
            "start_url": "/scan",
            "scope": "/scan",
            "display": "standalone",
            "orientation": "portrait",
            "background_color": "#1c1f26",
            "theme_color": "#1c1f26",
            "icons": [
                {"src": "/scan/static/icon-192.png", "sizes": "192x192", "type": "image/png"},
                {"src": "/scan/static/icon-512.png", "sizes": "512x512", "type": "image/png"},
                {
                    "src": "/scan/static/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        }
        return JSONResponse(
            data, media_type="application/manifest+json", headers={"Cache-Control": "no-cache"}
        )

    @server.custom_route("/scan/sw.js", methods=["GET"], include_in_schema=False)
    async def service_worker(request: Request) -> Response:
        # Served from /scan/ so its scope can cover the page; the script itself lives in static/.
        return _static_file(
            STATIC_DIR / "sw.js", request, max_age=0, extra={"Service-Worker-Allowed": "/scan"}
        )

    @server.custom_route("/scan/static/{path:path}", methods=["GET"], include_in_schema=False)
    async def static(request: Request) -> Response:
        rel = request.path_params["path"]
        if not re.fullmatch(r"[A-Za-z0-9_./-]{1,200}", rel) or ".." in rel.split("/"):
            return Response("not found", 404)
        target = (STATIC_DIR / rel).resolve()
        if not str(target).startswith(str(STATIC_DIR.resolve()) + "/") or not target.is_file():
            return Response("not found", 404)
        if "v" in request.query_params:
            return _static_file(target, request, max_age=31536000)
        # The OCR engine fetches vendor/ files by bare path (Tesseract appends the names), so they
        # cannot carry ?v=. A day, not "immutable": a replaced file reaches browsers without a
        # service worker within a day, and the worker's own cache is keyed by the asset version.
        max_age = 86400 if rel.startswith("vendor/") else 300
        return _static_file(target, request, max_age=max_age, immutable=False)

    # -- JSON API -----------------------------------------------------------
    @server.custom_route("/scan/api/search", methods=["GET"], include_in_schema=False)
    async def search(request: Request) -> Response:
        who = api_user(request, write=False)
        if isinstance(who, Response):
            return who
        try:
            names = await service.suggest(request.query_params.get("q", ""), owner=who[0])
        except ScanError as exc:
            return fail(exc)
        return JSONResponse(
            {"ok": True, "names": names},
            headers={"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"},
        )

    @server.custom_route("/scan/api/peek", methods=["GET"], include_in_schema=False)
    async def peek(request: Request) -> Response:
        """Summaries for the names a suggestion list shows (``names=a|b|c``, up to twenty)."""
        who = api_user(request, write=False)
        if isinstance(who, Response):
            return who
        names = [n for n in (request.query_params.get("names") or "").split("|") if n.strip()]
        try:
            cards = await service.peek(names, owner=who[0])
        except ScanError as exc:
            return fail(exc)
        slim = [
            {
                k: c.get(k)
                for k in (
                    "name",
                    "mana_cost",
                    "type_line",
                    "image_small",
                    "set",
                    "collector_number",
                    "oracle_id",
                )
            }
            for c in cards
        ]
        return JSONResponse(
            {"ok": True, "cards": slim},
            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )

    @server.custom_route("/scan/api/prints", methods=["GET"], include_in_schema=False)
    async def prints(request: Request) -> Response:
        who = api_user(request, write=False)
        if isinstance(who, Response):
            return who
        try:
            out = await service.printings(request.query_params.get("oracle_id", ""), owner=who[0])
        except ScanError as exc:
            return fail(exc)
        return JSONResponse(
            {"ok": True, **out},
            headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"},
        )

    @server.custom_route("/scan/api/resolve", methods=["POST"], include_in_schema=False)
    async def resolve(request: Request) -> Response:
        who = api_user(request, write=True)
        if isinstance(who, Response):
            return who
        data = await body(request)
        if isinstance(data, Response):
            return data
        try:
            results = await service.resolve(parse_cards(data.get("cards")), owner=who[0])
        except ScanError as exc:
            return fail(exc)
        return JSONResponse({"ok": True, **service.describe(results)}, headers=NO_STORE)

    @server.custom_route("/scan/api/sessions", methods=["GET"], include_in_schema=False)
    async def sessions(request: Request) -> Response:
        who = api_user(request, write=False)
        if isinstance(who, Response):
            return who
        return JSONResponse({"ok": True, "sessions": service.list_sessions(who[0])}, headers=NO_STORE)

    @server.custom_route("/scan/api/sessions", methods=["POST"], include_in_schema=False)
    async def create_session(request: Request) -> Response:
        who = api_user(request, write=True)
        if isinstance(who, Response):
            return who
        data = await body(request)
        if isinstance(data, Response):
            return data
        try:
            out = service.new_session(
                who[0], str(data.get("name") or ""), data.get("items") or [], source="browser"
            )
        except ScanError as exc:
            return fail(exc)
        return JSONResponse({"ok": True, **out}, 201, headers=NO_STORE)

    @server.custom_route("/scan/api/sessions/{sid}", methods=["GET"], include_in_schema=False)
    async def get_session(request: Request) -> Response:
        who = api_user(request, write=False)
        if isinstance(who, Response):
            return who
        try:
            return JSONResponse(
                {"ok": True, **service.get_session(who[0], request.path_params["sid"])}, headers=NO_STORE
            )
        except ScanError as exc:
            return fail(exc)

    @server.custom_route("/scan/api/sessions/{sid}", methods=["PUT"], include_in_schema=False)
    async def update_session(request: Request) -> Response:
        who = api_user(request, write=True)
        if isinstance(who, Response):
            return who
        data = await body(request)
        if isinstance(data, Response):
            return data
        try:
            out = service.update_session(
                who[0],
                request.path_params["sid"],
                name=data.get("name"),
                items=data.get("items"),
                status=data.get("status"),
            )
        except ScanError as exc:
            return fail(exc)
        return JSONResponse({"ok": True, **out}, headers=NO_STORE)

    @server.custom_route("/scan/api/sessions/{sid}", methods=["DELETE"], include_in_schema=False)
    async def delete_session(request: Request) -> Response:
        who = api_user(request, write=True)
        if isinstance(who, Response):
            return who
        try:
            service.delete_session(who[0], request.path_params["sid"])
        except ScanError as exc:
            return fail(exc)
        return JSONResponse({"ok": True}, headers=NO_STORE)


def _draft_key(s: Any, sub: str) -> str:
    return hmac.new(s.session_secret.encode(), f"draft:{sub}".encode(), hashlib.sha256).hexdigest()[:16]


def _eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())


def _json_for_html(data: dict[str, Any]) -> str:
    # Inside <script type=application/json>: only "</" could break out, so escape it.
    return json.dumps(data).replace("</", "<\\/").replace("<!--", "<\\!--")


def _static_file(
    path: Path,
    request: Request,
    *,
    max_age: int,
    immutable: bool = True,
    extra: dict[str, str] | None = None,
) -> Response:
    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    if path.suffix == ".js":
        media_type = "text/javascript"
    headers = {
        "Content-Security-Policy": SCAN_CSP,
        "Cache-Control": f"public, max-age={max_age}, immutable"
        if immutable and max_age >= 86400
        else f"max-age={max_age}",
        "X-Content-Type-Options": "nosniff",
        "Vary": "Accept-Encoding",
        **(extra or {}),
    }
    gz = path.with_name(path.name + ".gz")
    if gz.is_file() and "gzip" in request.headers.get("accept-encoding", ""):
        headers["Content-Encoding"] = "gzip"
        return FileResponse(gz, media_type=media_type, headers=headers)
    return FileResponse(path, media_type=media_type, headers=headers)
