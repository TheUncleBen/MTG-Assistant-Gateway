"""The app shell: web manifest, robots.txt, service worker, Android asset links, icons and /static."""

from __future__ import annotations

import json
import re
from types import SimpleNamespace
from typing import TYPE_CHECKING

from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response

from .companion import ICON_HEADERS, OFFLINE_PAGE, SHORTCUT_ICON, STATIC_DIR, _static_payload, short_name
from .theme import (
    STYLESHEETS,
    static_version,
)

if TYPE_CHECKING:
    from mcp.server.mcpserver import MCPServer


def add_shell_routes(server: MCPServer, h: SimpleNamespace) -> None:
    """Manifest, robots, service worker, asset links, icons and static files."""
    s = h.s

    # -- app shell: manifest, service worker, Android asset links ----------------
    @server.custom_route("/app.webmanifest", methods=["GET"], include_in_schema=False)
    async def manifest(_request: Request) -> Response:
        data = {
            "name": s.server_name,
            "short_name": short_name(s.server_name),
            "description": "Your Magic: The Gathering decks on Archidekt, with an assistant that proposes "
            "changes you approve.",
            "id": "/",
            "start_url": "/decks",
            "scope": "/",
            "display": "standalone",
            # A manifest has one colour set: the dark one, which matches the top bar in both themes
            # (the pages' own theme-color meta follows the chosen theme).
            "background_color": "#181818",
            "theme_color": "#111111",
            "icons": [
                {
                    "src": "/static/gateway-icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": "/static/gateway-icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": "/static/gateway-icon-maskable-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "maskable",
                },
                {
                    "src": "/static/gateway-icon-maskable-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
            "shortcuts": [
                {"name": "My decks", "url": "/decks", "icons": [SHORTCUT_ICON]},
                {"name": "Scan cards", "url": "/scan", "icons": [SHORTCUT_ICON]},
                {"name": "Proposals", "url": "/proposals", "icons": [SHORTCUT_ICON]},
            ],
        }
        return JSONResponse(
            data, media_type="application/manifest+json", headers={"Cache-Control": "public, max-age=3600"}
        )

    @server.custom_route("/robots.txt", methods=["GET"], include_in_schema=False)
    async def robots(_request: Request) -> Response:
        # Nothing on the gateway is for search engines (every page says noindex too).
        return Response(
            "User-agent: *\nDisallow: /\n",
            media_type="text/plain",
            headers={"Cache-Control": "public, max-age=86400"},
        )

    @server.custom_route("/sw.js", methods=["GET"], include_in_schema=False)
    async def service_worker(_request: Request) -> Response:
        # Network only: the pages are private and must never be served from a cache. A page load
        # that fails at the network level is tried twice more (after 0.3 s and 1 s) before the
        # plain offline page built here answers instead of the browser's own error screen, so one
        # dropped connection on the way in doesn't show "offline". GET navigations only. The page
        # follows the member's chosen theme (the mtg_theme cookie, read through the Cookie Store
        # where the browser has it). The X-MTG-Offline header tells the Android app this is the
        # gateway's own offline answer. Navigation preload: the browser asks for the page while the
        # worker is still starting (a browser stops an idle worker after a short while, and starting
        # it again on a phone takes a noticeable moment), and the worker answers with that same
        # response instead of making the request only once it runs.
        js = (
            "self.addEventListener('install',()=>self.skipWaiting());"
            "self.addEventListener('activate',e=>e.waitUntil(Promise.all([self.clients.claim(),"
            "self.registration.navigationPreload?self.registration.navigationPreload.enable():null])));"
            "const RETRY=[300,1000];"
            "const wait=ms=>new Promise(r=>setTimeout(r,ms));"
            "const tryFetch=(req,i)=>fetch(req).catch(err=>"
            "i<RETRY.length?wait(RETRY[i]).then(()=>tryFetch(req,i+1)):Promise.reject(err));"
            "const PAGE=" + json.dumps(OFFLINE_PAGE) + ";"
            "const theme=()=>(self.cookieStore?self.cookieStore.get('mtg_theme'):Promise.resolve(null))"
            ".then(c=>c&&(c.value==='light'||c.value==='dark')?c.value:'').catch(()=>'');"
            "const offline=t=>new Response(PAGE.replace(\"<html lang='en'>\","
            "t?`<html lang='en' data-theme='${t}'>`:\"<html lang='en'>\"),"
            "{status:503,headers:{'Content-Type':'text/html; charset=utf-8',"
            "'Cache-Control':'no-store','X-MTG-Offline':'1',"
            "'Content-Security-Policy':\"default-src 'none'; style-src 'unsafe-inline'; img-src data:; "
            "base-uri 'none'; frame-ancestors 'none'\"}});"
            "self.addEventListener('fetch',e=>{"
            "if(e.request.mode!=='navigate'||e.request.method!=='GET')return;"
            "e.respondWith(Promise.resolve(e.preloadResponse).catch(()=>null)"
            ".then(r=>r||tryFetch(e.request,0)).catch(()=>theme().then(offline)));"
            "});"
        )
        return Response(js, media_type="application/javascript", headers={"Cache-Control": "no-store"})

    @server.custom_route("/.well-known/assetlinks.json", methods=["GET"], include_in_schema=False)
    async def assetlinks(_request: Request) -> Response:
        raw = getattr(s, "android_assetlinks", None)
        if not raw:
            return JSONResponse([], headers={"Cache-Control": "public, max-age=3600"})
        try:
            data = json.loads(raw)
        except ValueError:
            return JSONResponse([], headers={"Cache-Control": "no-store"})
        return JSONResponse(data, headers={"Cache-Control": "public, max-age=3600"})

    # Browsers, and apps that show a connector's icon from its site, ask for these at the root.
    @server.custom_route("/favicon.ico", methods=["GET"], include_in_schema=False)
    async def favicon(_request: Request) -> Response:
        return FileResponse(STATIC_DIR / "favicon.ico", media_type="image/x-icon", headers=ICON_HEADERS)

    @server.custom_route("/apple-touch-icon.png", methods=["GET"], include_in_schema=False)
    async def touch_icon(_request: Request) -> Response:
        return FileResponse(STATIC_DIR / "gateway-icon-180.png", media_type="image/png", headers=ICON_HEADERS)

    @server.custom_route("/static/{path:path}", methods=["GET"], include_in_schema=False)
    async def static(request: Request) -> Response:
        rel = request.path_params["path"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", rel):
            return Response("not found", 404)
        if rel not in STYLESHEETS:
            target = (STATIC_DIR / rel).resolve()
            if not str(target).startswith(str(STATIC_DIR.resolve()) + "/") or not target.is_file():
                return Response("not found", 404)
        version = static_version(rel)
        if version is None:
            return Response("not found", 404)
        # Pages ask for each file by its digest (theme.versioned_static): that address never
        # changes content, so the browser keeps it for a year and doesn't ask again. Asked for
        # without it (or by an older digest), five minutes, then a check that costs no body.
        headers = {
            "Cache-Control": "public, max-age=31536000, immutable"
            if request.query_params.get("v") == version
            else "public, max-age=300",
            "ETag": f'W/"{version}"',
            "X-Content-Type-Options": "nosniff",
        }
        body, gz, media_type = _static_payload(rel)
        if gz is not None:
            headers["Vary"] = "Accept-Encoding"
        if headers["ETag"] in request.headers.get("if-none-match", ""):
            return Response(status_code=304, headers=headers)
        if gz is not None and "gzip" in request.headers.get("accept-encoding", ""):
            headers["Content-Encoding"] = "gzip"
            return Response(gz, media_type=media_type, headers=headers)
        return Response(body, media_type=media_type, headers=headers)
