"""The Android app's photo hand-off to the /scan page, with the page's service worker in control.

The app hands each phone photo to the page as a gateway-origin URL it answers itself
(``/__mtgassistant/photo/<n>``, ScanGlue.kt and MainActivity.kt). The /scan page is controlled by
its service worker (scope /scan), so that request goes through the worker first. Here the app's
answer is a Playwright route on the browser context, in headless Chromium with the app's user
agent: answered, the photo is read; not answered (the request reaches the gateway, which has no such
page), the glue asks the app for the bytes directly, and when those do not come either the app is
told plainly that the photo never reached the page.
"""

from __future__ import annotations

import base64
from pathlib import Path

import httpx
import pytest

from .test_scan_browser import (  # noqa: F401
    GLUE_NONCE,
    _chromium_path,
    android_glue,
    card_photo,
    server,
    skewed_photo,
)

pytest.importorskip("playwright")

APP_UA = (
    "Mozilla/5.0 (Linux; Android 16; wv) AppleWebKit/537.36 Chrome/140 Mobile Safari/537.36"
    " MTGAssistant/0.7.46"
)
PHOTO_DIR = "/__mtgassistant/photo/"
SW = Path(__file__).resolve().parents[1] / "src" / "mtg_gateway" / "scan" / "static" / "sw.js"


@pytest.fixture
def exe() -> str | None:
    found = _chromium_path()
    if found == "missing":
        pytest.skip("no Chromium available for Playwright")
    return found


@pytest.fixture
def photo(tmp_path: Path) -> bytes:
    card = tmp_path / "sol-ring-card.jpg"
    card_photo(card, "Sol Ring")
    out = tmp_path / "sol-ring.jpg"
    skewed_photo(out, card, [(300, 220), (1130, 300), (1060, 1620), (230, 1520)])
    return out.read_bytes()


def test_the_photo_url_is_never_a_gateway_page(server) -> None:  # noqa: F811
    """A photo request that gets past the app reaches the gateway as a plain 404: no page, no
    sign-in redirect, nothing cached."""
    sid = server.sign_in()
    for cookies in ({}, {"mtg_session": sid}):
        r = httpx.get(f"{server.base}{PHOTO_DIR}1", cookies=cookies, follow_redirects=False)
        assert r.status_code == 404, (r.status_code, r.headers)
        assert "image" not in r.headers.get("content-type", "")


def test_the_scan_worker_leaves_the_app_photo_alone() -> None:
    """The worker never answers an app photo itself, and says so before anything else."""
    js = SW.read_text(encoding="utf-8")
    handler = js[js.index("addEventListener('fetch'") :]
    assert "const APP_PHOTO = '/__mtgassistant/';" in js
    early = "if (url.pathname.startsWith(APP_PHOTO)) return;"
    assert early in handler
    assert handler.index(early) < min(handler.index("if (event.request.method"), handler.index("respondWith"))


def test_the_app_photo_reaches_the_page_through_its_service_worker(server, exe, photo: bytes) -> None:  # noqa: F811
    """Answered by the app, read; not answered, handed over directly; neither, a plain failure."""
    from playwright.sync_api import sync_playwright

    sid = server.sign_in()
    served: dict[int, bytes] = {}  # what the app answers, by photo number
    asked: list[str] = []
    b64 = base64.b64encode(photo).decode()
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=exe, args=["--no-sandbox"])
        ctx = browser.new_context(
            viewport={"width": 412, "height": 915}, is_mobile=True, has_touch=True, user_agent=APP_UA
        )
        ctx.add_cookies([{"name": "mtg_session", "value": sid, "url": server.base}])

        def app(route) -> None:
            url = route.request.url
            asked.append(url)
            n = int(url.rsplit("/", 1)[1])
            if n in served:
                headers = {"Content-Type": "image/jpeg", "Cache-Control": "no-store"}
                route.fulfill(status=200, body=served[n], headers=headers)
            else:
                route.continue_()  # past the app: the gateway answers 404

        ctx.route(f"**{PHOTO_DIR}*", app)
        page = ctx.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.goto(f"{server.base}/scan")
        page.wait_for_function("() => window.__scan && window.__scan.flattenCard")
        # The worker controls the page, as it does on the phone after the first visit.
        page.wait_for_function(
            "() => navigator.serviceWorker.controller"
            " && navigator.serviceWorker.controller.scriptURL.includes('/scan/sw.js')",
            timeout=15_000,
        )
        page.evaluate(
            """() => { window.__events = [];
                 window.MtgNative = { scanEvent: (j) => window.__events.push(JSON.parse(j)),
                                      openCamera: () => {}, version: () => 'test' };
                 window.__scanOcrOverride = async (canvas, kind) => kind === 'info'
                   ? {text: '', confidence: 0, words: []}
                   : {text: 'Sol Ring', confidence: 95, words: []}; }"""
        )
        assert page.evaluate(android_glue()) == "installed"
        assert page.evaluate("() => window.__mtgNative.begin()") == "ok"

        def shoot(n: int) -> str:
            page.evaluate("() => { window.__events = []; window.__scan.state.pending = null; }")
            out = page.evaluate(f"() => window.__mtgNative.onPhoto('{server.base}{PHOTO_DIR}{n}', null)")
            assert out == f"ok:{n}", out
            return out

        def wait_for(kind: str) -> dict:
            page.wait_for_function(f"() => window.__events.some((e) => e.type === '{kind}')", timeout=30_000)
            ev = page.evaluate(f"() => window.__events.find((e) => e.type === '{kind}')")
            assert ev["nonce"] == GLUE_NONCE
            return ev

        # 1. The app answers the URL: the photo is read; nothing else is asked of the app.
        served[1] = photo
        shoot(1)
        done = wait_for("done")
        assert "error" not in done and done["matched"] is True and done["name"] == "Sol Ring", done
        assert not any(e["type"] == "photo-direct" for e in page.evaluate("() => window.__events"))
        assert asked == [f"{server.base}{PHOTO_DIR}1"]

        # 2. The URL reaches the gateway (404): the glue asks the app for the bytes, and reads them.
        shoot(2)
        ask = wait_for("photo-direct")
        assert ask["seq"] == 2
        assert page.evaluate("() => window.__events.some((e) => e.type === 'done')") is False
        assert page.evaluate(f"() => window.__mtgNative.photoData(2, {b64!r})") == "ok"
        done = wait_for("done")
        assert done["seq"] == 2 and "error" not in done and done["matched"] is True, done
        # Single use: a second hand-over of the same photo is refused, and the blob is gone.
        assert page.evaluate(f"() => window.__mtgNative.photoData(2, {b64!r})") == "stale"
        # Only the photo asked for is taken.
        shoot(3)
        assert wait_for("photo-direct")["seq"] == 3
        assert page.evaluate(f"() => window.__mtgNative.photoData(2, {b64!r})") == "stale"

        # 3. The app no longer has it (an empty hand-over): a plain failure the app can say.
        assert page.evaluate("() => window.__mtgNative.photoData(3, '')") == "ok"
        done = wait_for("done")
        assert done["seq"] == 3 and done["error"] == "photo-lost", done

        # 4. No answer at all (an app without the direct hand-over): the same failure, in good time.
        page.evaluate("() => { window.__events = []; }")
        shoot(4)
        wait_for("photo-direct")
        done = wait_for("done")
        assert done["seq"] == 4 and done["error"] == "photo-lost", done
        assert errors == []
        browser.close()


APP = Path(__file__).resolve().parents[1] / "android" / "app" / "src" / "main" / "kotlin"
APP = APP / "local" / "mtgassistantgateway" / "app"


def test_the_app_answers_the_photo_on_either_webview_path() -> None:
    """MainActivity answers the photo URL from its WebViewClient and from a ServiceWorkerClient,
    through one function (gateway origin and photo number checked there), and answers a
    photo-direct ask with that one photo only, once."""
    import re

    src = (APP / "MainActivity.kt").read_text(encoding="utf-8")
    assert "import android.webkit.ServiceWorkerController" in src
    register = (
        "ServiceWorkerController.getInstance()" + ".setServiceWorkerClient(ServiceWorkerClients.photo(this))"
    )
    assert register in src
    calls = re.findall(r"photoResponse\(request\.url\.toString\(\)\)", src)
    assert len(calls) == 2, calls  # Client and the service worker client
    body = src[src.index("internal fun photoResponse") :]
    body = body[: body.index("\n    }\n")]
    assert "ScanGlue.photoSeqOf(origin, url) ?: return null" in body
    assert "wanted != current.first" in body and '"Cache-Control" to "no-store"' in body
    # Held weakly and unhooked when its activity goes.
    assert "WeakReference<MainActivity>" in src and "ServiceWorkerClients.release(this)" in src
    direct = src[src.index("override fun photoDirect") :]
    direct = direct[: direct.index("\n            }\n")]
    assert "if (!onScanPage()) return" in direct
    assert "current.first == seq" in direct and "photo = null" in direct
    assert "ScanGlue.photoData(seq, b64)" in direct
    # minSdk covers ServiceWorkerController (API 24) without a version check.
    gradle = (APP.parents[5] / "build.gradle.kts").read_text(encoding="utf-8")
    assert int(re.search(r"minSdk = (\d+)", gradle).group(1)) >= 24
    panel = (APP / "CameraPanel.kt").read_text(encoding="utf-8")
    assert '"photo-direct" ->' in panel and "host.photoDirect(seq)" in panel
    lost = (
        "ShotLine.Outcome.PhotoLost -> "
        + "pageDone(activity.getString(R.string.scan_photo_lost), stopAuto = true, missed = true)"
    )
    assert lost in panel
    strings = (APP.parents[3] / "res" / "values" / "strings.xml").read_text(encoding="utf-8")
    assert "The app couldn’t hand the photo to the page. Tap Shoot to try again." in strings
