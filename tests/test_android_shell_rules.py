"""The Android app's rules that a JVM test can't see whole: what its Kotlin sources, resources and
build file must hold (CI's android-check compiles them and runs their own unit tests). Also the
scanner's one holding distance, which the app and the website must both ask for."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP = ROOT / "android/app/src/main/kotlin/local/mtgassistantgateway/app"
TESTS = ROOT / "android/app/src/test/kotlin/local/mtgassistantgateway/app"
RES = ROOT / "android/app/src/main/res"
SCAN = ROOT / "src/mtg_gateway/scan/static"


def _kt(name: str) -> str:
    return (APP / name).read_text(encoding="utf-8")


def test_a_forum_thread_never_offers_pull_to_refresh() -> None:
    """A thread has a reply box and a contest entry: forms, which the app's own rule keeps a pull
    away from. The forum's front page and its sections still offer it."""
    rule = _kt("PullRefresh.kt")
    assert 'path.startsWith("/forum/")' not in rule
    assert 'Regex("^/forum(/(?!thread(/|$))[^/]+/[^/]+)?$")' in rule
    test = (TESTS / "PullRefreshTest.kt").read_text(encoding="utf-8")
    never = test.split("fun formsTheEditorTheScannerAndAThreadNeverOfferIt")[1].split("@Test")[0]
    assert '"/forum/thread/123"' in never and '"/forum/thread/123/enter"' in never
    offered = test.split("fun listPagesOfferIt")[1].split("@Test")[0]
    assert '"/forum"' in offered and '"/forum/general/rules"' in offered


def test_a_lint_error_fails_the_build_and_ci_runs_lint() -> None:
    gradle = (ROOT / "android/app/build.gradle.kts").read_text(encoding="utf-8")
    assert "abortOnError = false" not in gradle and "abortOnError = true" in gradle
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    check = ci.split("\n  android-check:\n")[1].split("\n  android:\n")[0]
    assert ":app:lintPlainDebug" in check
    assert "android/scripts/test.sh" in check and "build.sh release --throwaway" in check
    # lint runs inside the existing job, not a job of its own
    assert ci.count(":app:lintPlainDebug") == 1
    # the two lint errors: the share sheet's actions exist from Android 14 only
    share = _kt("DeckShare.kt")
    before = share.split("private fun copyAction")[0][-200:]
    assert "@RequiresApi(Build.VERSION_CODES.UPSIDE_DOWN_CAKE)" in before


def test_the_custom_tab_sign_in_always_names_a_browser() -> None:
    """In the applinks build the app owns the gateway's https links, so a Custom Tab with no browser
    named would open /login in the app's own page. It names one, or lets the person pick with this
    app left out."""
    main = _kt("MainActivity.kt")
    start = "val url = Uri.parse(BrowserSignIn.startUrl(origin, next, challenge, fresh, customTab = true))"
    leg = main.split(start)[1]
    leg = leg.split("private fun onAuthTabResult")[0]
    assert "tab.intent.setPackage(pick)" in leg
    assert "Intent.EXTRA_EXCLUDE_COMPONENTS" in leg and "ComponentName(this, MainActivity::class.java)" in leg
    assert "CustomTabsIntent.Builder().setShowTitle(true).build().launchUrl(this, url)" not in main
    assert "BrowserSignIn.customTabBrowser(default, browsers, packageName, ::isSystemApp)" in main
    # the probe is any https address but the gateway's, which the applinks build claims
    assert 'Uri.parse("https://example.com/")' in main
    assert "fun customTabBrowser(" in _kt("BrowserSignIn.kt")
    assert "fun customTabNeverOpensThisApp" in (TESTS / "BrowserSignInTest.kt").read_text(encoding="utf-8")


def test_the_file_picker_makes_a_huge_jpeg_smaller() -> None:
    pick = _kt("FilePick.kt")
    assert "isJpeg(mime, name) && jpegTooLarge(context, uri)" in pick
    assert "inJustDecodeBounds = true" in pick  # the size comes from the header, not a full decode
    assert "bitmap.compress(Bitmap.CompressFormat.JPEG, QUALITY, it)" in pick
    assert "const val QUALITY = 92" in pick
    # ImageDecoder draws the photo upright from its EXIF orientation; nothing turns it off
    assert "ImageDecoder.decodeBitmap" in pick and "setUnpremultipliedRequired" not in pick
    test = (TESTS / "FilePickTest.kt").read_text(encoding="utf-8")
    assert "fun onlyAJpegLargerThanTheLimitIsMadeSmaller" in test


def test_a_missed_scan_photo_can_be_saved_without_a_permission() -> None:
    panel = _kt("CameraPanel.kt")
    photo = _kt("ScanPhoto.kt")
    assert "savePhoto.setOnClickListener { saveLastPhoto() }" in panel
    for miss in (
        "R.string.no_card_outline), missed = true",
        "R.string.scan_failed, o.error), stopAuto = true, missed = true",
        "R.string.shot_unreadable, o.pageStatus), missed = true",
        "R.string.scan_page_slow), stopAuto = true, missed = true",
    ):
        assert miss in panel, miss
    assert "ScanPhoto.save(app, ScanPhoto.withNote(jpeg, note), name)" in panel
    assert "MediaStore.Images.Media.RELATIVE_PATH" in photo and "IS_PENDING" in photo
    manifest = (ROOT / "android/app/src/main/AndroidManifest.xml").read_text(encoding="utf-8")
    assert "WRITE_EXTERNAL_STORAGE" not in manifest and "READ_MEDIA_IMAGES" not in manifest
    layout = (RES / "layout/camera_panel.xml").read_text(encoding="utf-8")
    assert "@+id/save_photo" in layout and "@string/save_photo" in layout
    strings = (RES / "values/strings.xml").read_text(encoding="utf-8")
    assert '<string name="save_photo">Save this photo</string>' in strings
    assert (TESTS / "ScanPhotoTest.kt").is_file()


def test_the_app_and_the_website_ask_for_one_distance() -> None:
    """Both guides are sized so a card filling them fills the same share of the photo, and every
    line on either surface names the one distance that share gives: about 15 cm (6 in)."""
    scan_js = (SCAN / "scan.js").read_text(encoding="utf-8")
    scan_css = (SCAN / "scan.css").read_text(encoding="utf-8")
    torch = _kt("TorchMath.kt")
    web = float(re.search(r"const GUIDE_WIDTH = ([0-9.]+);", scan_js).group(1))
    app = float(re.search(r"const val CARD_SHARE = ([0-9.]+)f", torch).group(1))
    assert web == app == 0.4
    assert re.search(r"\.scan \.camera \.guide\{[^}]*width:40%", scan_css)
    # the app's box follows the preview's crop, so it is CARD_SHARE of the photo on every screen
    assert "guideView.widthShare = it" in _kt("CameraPanel.kt")
    assert "var cw = w * widthShare" in _kt("CardGuideView.kt")
    strings = (RES / "values/strings.xml").read_text(encoding="utf-8")
    said = re.findall(r"\d+ cm \(\d+ in\)", strings + scan_js)
    assert said and set(said) == {"15 cm (6 in)"}, said
    docs = "".join(p.read_text(encoding="utf-8") for p in (ROOT / "docs").glob("*.md"))
    assert set(re.findall(r"\d+ cm \(\d+ in\)", docs)) == {"15 cm (6 in)"}


def test_the_gateways_busy_page_shows_in_the_app_not_the_apps_own_error_box() -> None:
    """A request that waited the limit for the database is answered 503 with the gateway's own
    page (dbwait.py), whose Try again posts the member's form again. The app's error box for a
    reverse proxy's 502/503/504 would cover it, and its Retry reloads (a posted form would be
    lost), so the X-MTG-Busy header keeps the box away, as X-MTG-Offline does for the offline page."""
    main = _kt("MainActivity.kt")
    assert 'private const val BUSY_HEADER = "X-MTG-Busy"' in main
    handler = main.split("override fun onReceivedHttpError")[1].split("override fun")[0]
    assert "BUSY_HEADER" in handler
    assert handler.index("BUSY_HEADER") < handler.index("errorBox.visibility = View.VISIBLE")


def test_the_back_key_asks_only_a_gateway_page_and_a_new_page_starts_with_no_layer() -> None:
    """The Back key asks the page to close its top layer (static/layers.js) only on a gateway page:
    a sign-in provider's page could otherwise answer the hook and keep Back for itself. A new page
    forgets an open layer as it starts, not as it finishes, so a layer opened before the page has
    finished loading keeps its Back."""
    main = _kt("MainActivity.kt")
    back = main.split("override fun handleOnBackPressed")[1].split("private val windowInfo")[0]
    asks = back.index("evaluateJavascript(BACK_KEY)")
    assert re.search(r"if \(gatewayPage\)[^\n]*\{\s*\n?\s*web\.evaluateJavascript\(BACK_KEY\)", back[: asks + 40])
    started = main.split("override fun onPageStarted")[1].split("override fun")[0]
    finished = main.split("override fun onPageFinished")[1].split("override fun")[0]
    assert "layerOpen = false" in started
    assert "layerOpen = false" not in finished
