package local.mtgassistantgateway.app

import android.Manifest
import android.content.ActivityNotFoundException
import android.content.ContentValues
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Message
import android.os.SystemClock
import android.provider.MediaStore
import android.view.View
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.PermissionRequest
import android.webkit.RenderProcessGoneDetail
import android.webkit.URLUtil
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
import android.webkit.WebSettings
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.Button
import android.widget.FrameLayout
import android.widget.ImageButton
import android.widget.PopupMenu
import android.widget.ProgressBar
import android.widget.TextView
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.core.util.Consumer
import androidx.window.java.layout.WindowInfoTrackerCallbackAdapter
import androidx.window.layout.FoldingFeature
import androidx.window.layout.WindowInfoTracker
import androidx.window.layout.WindowLayoutInfo
import java.io.ByteArrayInputStream

/**
 * The app's main screen: the gateway's own web pages, full screen, with the few things a
 * browser tab cannot give them bolted on natively:
 *
 * - the camera permission, so the /scan page's live camera works inside the app;
 * - a phone-camera scan panel with torch brightness, zoom and exposure ([CameraPanel]), laid over
 *   the /scan page so the page keeps running and reads each photo through [ScanGlue];
 * - file pickers (photo and CSV import) and downloads (CSV export, skill zip);
 * - a floating menu: scan, reload, open in browser, change gateway.
 *
 * Navigation policy ([NavPolicy]): pages on the gateway's origin, and on the one identity
 * provider the gateway advertises (`/.well-known/mtg-gateway`; for an older gateway, the one its
 * /login redirected to) during a sign-in, stay in the app. Links anywhere else open in the phone's browser, as do `target=_blank` links. Other schemes
 * open another app only when tapped in the main frame, and only an activity that accepts links
 * from a browser.
 *
 * Links into the app: an https link to the configured gateway (an App Link in the applinks flavor,
 * or a link shared to the app) opens that page. Before setup the address screen is shown without
 * any prefill, so a link can never pick the gateway. Links to any other site are ignored.
 *
 * Fold posture (Jetpack WindowManager) is tracked here for the whole window and handed to the
 * scan panel, which lays itself out around the hinge.
 */
class MainActivity : ComponentActivity() {
    private lateinit var prefs: Prefs
    private lateinit var origin: String
    private lateinit var web: WebView
    private lateinit var progress: ProgressBar
    private lateinit var errorBox: View
    private lateinit var errorText: TextView

    /** What stays in the WebView: the gateway, and its identity provider during a sign-in. */
    private lateinit var nav: NavPolicy
    /**
     * The main-frame document the bridge answers: bumped on the UI thread each time a new one
     * commits, with whether it is a gateway page. [NativeBridge] reads both on the calling thread at
     * call time, so a call made by a page that is being replaced is never credited to the next one.
     */
    @Volatile private var pageGen = 0
    @Volatile private var gatewayPage = false
    private var fileCallback: ValueCallback<Array<Uri>>? = null
    private var pendingCameraPermission: PermissionRequest? = null
    private var pendingCamera = false // open the camera as soon as the /scan page is ready
    private var scanReloaded = false // the /scan page was reloaded once to get its hooks; never loop
    private val backCallback = object : OnBackPressedCallback(false) {
        override fun handleOnBackPressed() {
            if (camera != null) closeCamera() else if (web.canGoBack()) web.goBack()
        }
    }
    private val windowInfo = WindowInfoTrackerCallbackAdapter(WindowInfoTracker.getOrCreate(this))
    private val foldListener = Consumer<WindowLayoutInfo> { info ->
        fold = info.displayFeatures.filterIsInstance<FoldingFeature>().firstOrNull()
        camera?.onFold(fold)
    }
    private var fold: FoldingFeature? = null
    private var camera: CameraPanel? = null // the scan panel while it is open
    /** Ties the page's scan events to the glue this activity installed (see [ScanGlue.install]). */
    private val glueNonce = java.security.SecureRandom().let { r -> ByteArray(16).also(r::nextBytes) }.joinToString("") { "%02x".format(it) }
    /** The photo the page is about to fetch from [ScanGlue.PHOTO_PATH]; read on Chromium's IO thread. */
    @Volatile private var photo: Pair<Int, ByteArray>? = null
    private var photoSeq = 0
    private lateinit var root: FrameLayout
    private lateinit var fab: ImageButton
    /** The error box's Retry goes to the gateway's home page instead of reloading a refused page. */
    private var retryHome = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        prefs = Prefs(this)
        val saved = prefs.gatewayOrigin
        if (saved == null) {
            // Not set up yet: the address screen, with no prefill from a link (a link must not pick the gateway).
            startActivity(Intent(this, SetupActivity::class.java))
            finish()
            return
        }
        origin = saved
        nav = NavPolicy(origin, prefs.providerFor(origin))
        fetchProvider()
        onBackPressedDispatcher.addCallback(this, backCallback)
        setContentView(R.layout.activity_main)
        root = findViewById(R.id.root)
        Insets.padSystemBars(root)
        web = findViewById(R.id.web)
        progress = findViewById(R.id.progress)
        errorBox = findViewById(R.id.error_box)
        errorText = findViewById(R.id.error_text)
        findViewById<Button>(R.id.error_retry).setOnClickListener {
            errorBox.visibility = View.GONE
            if (retryHome) web.loadUrl(origin + "/") else web.reload()
            retryHome = false
        }
        fab = findViewById(R.id.fab)
        fab.setOnClickListener { showMenu(it) }
        configureWebView()
        // A link is consumed once: after a restore the saved page wins, not the old intent.
        val linked = takeLinkedUrl(intent)?.takeIf { GatewayUrl.isGateway(origin, it) }
        when {
            savedInstanceState != null -> {
                // The blank page over a refused one has no Retry after a restore: start at home.
                val restored = web.restoreState(savedInstanceState)
                if (restored == null || restored.currentItem?.url in listOf(null, BLANK)) web.loadUrl(origin + "/")
            }
            linked != null -> web.loadUrl(linked)
            else -> web.loadUrl(origin + "/")
        }
    }

    /** The app is already open (singleTask) and a link to the gateway arrives: show that page. */
    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        val url = takeLinkedUrl(intent) ?: return
        if (!GatewayUrl.isGateway(origin, url)) {
            Toast.makeText(this, getString(R.string.link_other_gateway), Toast.LENGTH_LONG).show()
            return
        }
        if (camera != null) closeCamera()
        web.loadUrl(url)
    }

    override fun onStart() {
        super.onStart()
        windowInfo.addWindowLayoutInfoListener(this, mainExecutor, foldListener)
    }

    override fun onStop() {
        windowInfo.removeWindowLayoutInfoListener(foldListener)
        super.onStop()
    }

    override fun onSaveInstanceState(outState: Bundle) {
        super.onSaveInstanceState(outState)
        web.saveState(outState)
    }

    private fun configureWebView() {
        val s: WebSettings = web.settings
        s.javaScriptEnabled = true
        s.domStorageEnabled = true // the /scan page keeps its draft list in localStorage
        s.mediaPlaybackRequiresUserGesture = false // the live camera preview is a <video>
        s.allowFileAccess = false
        s.allowContentAccess = false
        s.setSupportMultipleWindows(true) // so target=_blank reaches onCreateWindow
        s.javaScriptCanOpenWindowsAutomatically = false
        s.mixedContentMode = WebSettings.MIXED_CONTENT_NEVER_ALLOW
        s.safeBrowsingEnabled = true
        s.setSupportZoom(false)
        s.userAgentString = s.userAgentString + " MTGAssistant/" + BuildConfig.VERSION_NAME
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(web, false)
        web.addJavascriptInterface(NativeBridge(), "MtgNative")
        web.webViewClient = Client()
        web.webChromeClient = Chrome()
        web.setDownloadListener { url, userAgent, contentDisposition, mimeType, _ ->
            download(url, userAgent, contentDisposition, mimeType)
        }
    }

    // -- menu ---------------------------------------------------------------

    private fun showMenu(anchor: View) {
        val menu = PopupMenu(this, anchor)
        menu.menuInflater.inflate(R.menu.main, menu.menu)
        menu.setOnMenuItemClickListener { item ->
            when (item.itemId) {
                R.id.menu_scan -> startScan()
                R.id.menu_reload -> web.reload()
                R.id.menu_browser -> openExternal(Uri.parse(web.url ?: origin))
                R.id.menu_gateway -> {
                    startActivity(Intent(this, SetupActivity::class.java))
                    finish()
                }
            }
            true
        }
        menu.show()
    }

    /** Opens the phone-camera scan panel, going to the /scan page first if needed. */
    private fun startScan() {
        if (camera != null) return
        if (checkSelfPermission(Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            pendingCamera = true
            requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_PERM_NATIVE_CAMERA)
            return
        }
        if (onScanPage()) {
            web.evaluateJavascript(ScanGlue.READY) { ready ->
                if (ready == "true") {
                    scanReloaded = false
                    // Park the page on its list tab (stops its own camera) while the panel has the lens.
                    web.evaluateJavascript(ScanGlue.BEGIN) { if (camera == null && !isDestroyed) openCamera() }
                } else if (!scanReloaded) {
                    scanReloaded = true
                    pendingCamera = true
                    web.loadUrl(GatewayUrl.join(origin, "/scan"))
                } else {
                    scanReloaded = false
                    Toast.makeText(this, getString(R.string.scan_page_too_old), Toast.LENGTH_LONG).show()
                }
            }
        } else {
            pendingCamera = true
            web.loadUrl(GatewayUrl.join(origin, "/scan"))
        }
    }

    private fun onScanPage(): Boolean =
        GatewayUrl.isGateway(origin, web.url) && GatewayUrl.pathStartsWith(web.url, "/scan")

    /** Called when a gateway /scan page finished loading: install the glue, then open the camera if that was waiting. */
    private fun scanPageReady() {
        web.evaluateJavascript(ScanGlue.install(glueNonce)) {
            if (pendingCamera) {
                pendingCamera = false
                startScan()
            }
        }
    }

    // -- the scan panel ----------------------------------------------------------

    private fun openCamera() {
        lateinit var panel: CameraPanel
        panel = CameraPanel(this, root, object : CameraPanel.Host {
            override fun photo(jpeg: ByteArray, guide: FloatArray?) {
                if (!onScanPage()) {
                    panel.handoffFailed()
                    return
                }
                val n = ++photoSeq
                val js = try {
                    ScanGlue.onPhoto(ScanGlue.photoUrl(origin, n), guide)
                } catch (e: IllegalArgumentException) {
                    panel.handoffFailed() // an origin the glue cannot quote; never crash the UI thread
                    return
                }
                photo = n to jpeg // served to the page by Client.shouldInterceptRequest, never sent anywhere
                web.evaluateJavascript(js) { result ->
                    val seq = ScanGlue.acceptedSeq(result)
                    if (seq != null) panel.photoAccepted(seq) else panel.handoffFailed()
                }
            }

            override fun continuous(on: Boolean) {
                web.evaluateJavascript(ScanGlue.setContinuous(on), null)
            }

            override fun undo() {
                web.evaluateJavascript(ScanGlue.UNDO, null)
            }

            override fun close() = closeCamera()
        }, glueNonce)
        camera = panel
        root.addView(panel.view, root.indexOfChild(fab)) // under the FAB, over the page
        fab.visibility = View.GONE
        panel.onFold(fold)
        panel.resume()
        syncBackCallback()
    }

    private fun closeCamera() {
        val panel = camera ?: return
        camera = null
        root.removeView(panel.view)
        photo = null
        fab.visibility = View.VISIBLE
        syncBackCallback()
        // Closes the camera and waits for it, so the page's own camera can have the lens; only then
        // back to the page's camera tab, when a shaky read waits there for a tap.
        panel.destroy {
            if (!isDestroyed && onScanPage()) web.evaluateJavascript(ScanGlue.END, null)
        }
    }

    // -- results: file picker, permissions ------------------------------------------

    override fun onActivityResult(requestCode: Int, resultCode: Int, data: Intent?) {
        when (requestCode) {
            REQ_FILE -> {
                val cb = fileCallback ?: return
                fileCallback = null
                cb.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(resultCode, data))
            }
            else -> super.onActivityResult(requestCode, resultCode, data)
        }
    }

    override fun onRequestPermissionsResult(requestCode: Int, permissions: Array<String>, grantResults: IntArray) {
        val granted = grantResults.isNotEmpty() && grantResults[0] == PackageManager.PERMISSION_GRANTED
        when (requestCode) {
            REQ_PERM_WEB_CAMERA -> {
                val req = pendingCameraPermission
                pendingCameraPermission = null
                if (req == null) return
                if (granted) req.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE)) else req.deny()
            }
            REQ_PERM_NATIVE_CAMERA -> {
                if (granted && pendingCamera) {
                    pendingCamera = false
                    startScan()
                } else {
                    pendingCamera = false
                    if (!shouldShowRequestPermissionRationale(Manifest.permission.CAMERA)) {
                        // Denied for good ("don't ask again"): Android shows no prompt any more, so
                        // the only way back is the app's settings page. Say so and open it.
                        Toast.makeText(this, getString(R.string.camera_permission_settings), Toast.LENGTH_LONG).show()
                        try {
                            startActivity(
                                Intent(android.provider.Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.fromParts("package", packageName, null))
                            )
                        } catch (e: ActivityNotFoundException) {
                            // no settings screen on this device; the message above still explains
                        }
                    } else {
                        Toast.makeText(this, getString(R.string.camera_permission_needed), Toast.LENGTH_LONG).show()
                    }
                }
            }
            else -> super.onRequestPermissionsResult(requestCode, permissions, grantResults)
        }
    }

    // -- helpers -----------------------------------------------------------

    /**
     * Reads the sign-in origin the gateway advertises ([GatewayUrl.APP_CONFIG_PATH]) and pins it, so
     * the consent page's Approve stays in the app even before this run has seen a /login. One
     * request to the gateway over https: no cookies, redirects not followed, a small body. A gateway
     * too old to answer keeps the earlier behaviour (the provider is learned from /login).
     */
    private fun fetchProvider() {
        val gateway = origin
        val app = applicationContext
        Thread {
            val provider = try {
                val conn = java.net.URL(gateway + GatewayUrl.APP_CONFIG_PATH).openConnection() as java.net.HttpURLConnection
                try {
                    conn.instanceFollowRedirects = false
                    conn.useCaches = false
                    conn.connectTimeout = 10_000
                    conn.readTimeout = 10_000
                    if (conn.responseCode != 200) null
                    else GatewayUrl.parseProvider(String(conn.inputStream.use { it.readNBytesCompat(4096) }, Charsets.UTF_8))
                } finally {
                    conn.disconnect()
                }
            } catch (e: java.io.IOException) {
                null
            } ?: return@Thread
            Handler(app.mainLooper).post {
                Prefs(app).setProvider(gateway, provider)
                if (!isDestroyed && origin == gateway) nav.pin(provider)
            }
        }.start()
    }

    /** Up to [max] bytes (InputStream.readNBytes needs API 33). */
    private fun java.io.InputStream.readNBytesCompat(max: Int): ByteArray {
        val out = java.io.ByteArrayOutputStream()
        val buf = ByteArray(1024)
        while (out.size() < max) {
            val n = read(buf, 0, minOf(buf.size, max - out.size()))
            if (n < 0) break
            out.write(buf, 0, n)
        }
        return out.toByteArray()
    }

    /**
     * Opens [uri] in another app the way a browser would: only activities that accept links from
     * a browser (CATEGORY_BROWSABLE), never a named component. Callers decide whether it may leave
     * the app at all ([NavPolicy]).
     */
    private fun openExternal(uri: Uri) {
        val intent = Intent(Intent.ACTION_VIEW, uri).addCategory(Intent.CATEGORY_BROWSABLE)
        intent.component = null
        intent.selector = null
        try {
            startActivity(intent)
        } catch (e: ActivityNotFoundException) {
            Toast.makeText(this, getString(R.string.no_app_for_link), Toast.LENGTH_SHORT).show()
        }
    }

    /**
     * Saves a gateway download (CSV export, the skill zip) to Downloads. The app fetches it itself
     * rather than through DownloadManager, which would re-send the gateway's cookies to wherever a
     * redirect pointed: one request to the gateway, redirects not followed (a redirect means the
     * session ended, so the person is asked to sign in again).
     */
    private fun download(url: String, userAgent: String, contentDisposition: String, mimeType: String) {
        if (!GatewayUrl.isGateway(origin, url)) {
            val scheme = Uri.parse(url).scheme?.lowercase()
            if (scheme == "https" || scheme == "http") openExternal(Uri.parse(url))
            return
        }
        val name = URLUtil.guessFileName(url, contentDisposition, mimeType)
        val type = mimeType.ifBlank { "application/octet-stream" }
        val cookie = CookieManager.getInstance().getCookie(url)
        val app = applicationContext
        Toast.makeText(this, getString(R.string.downloading, name), Toast.LENGTH_SHORT).show()
        Thread {
            val message = try {
                fetchToDownloads(app, url, userAgent, cookie, name, type)
            } catch (e: java.io.IOException) {
                app.getString(R.string.download_failed, name)
            }
            Handler(app.mainLooper).post { Toast.makeText(app, message, Toast.LENGTH_LONG).show() }
        }.start()
    }

    /** Runs off the UI thread, with the application context so a closed screen doesn't matter. Returns the message to show. */
    private fun fetchToDownloads(app: Context, url: String, userAgent: String, cookie: String?, name: String, type: String): String {
        val resolver = app.contentResolver
        val conn = java.net.URL(url).openConnection() as java.net.HttpURLConnection
        try {
            conn.instanceFollowRedirects = false
            conn.connectTimeout = 15_000
            conn.readTimeout = 30_000
            conn.setRequestProperty("User-Agent", userAgent)
            if (cookie != null) conn.setRequestProperty("Cookie", cookie)
            val code = conn.responseCode
            if (code in 300..399 || code == 401) return app.getString(R.string.download_signed_out)
            if (code != 200) return app.getString(R.string.download_failed, name)
            val values = ContentValues().apply {
                put(MediaStore.Downloads.DISPLAY_NAME, name)
                put(MediaStore.Downloads.MIME_TYPE, type)
                put(MediaStore.Downloads.IS_PENDING, 1)
            }
            val item = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
                ?: return app.getString(R.string.download_failed, name)
            try {
                val out = resolver.openOutputStream(item) ?: throw java.io.IOException("no output stream")
                out.use { o -> conn.inputStream.use { it.copyTo(o) } }
                values.clear()
                values.put(MediaStore.Downloads.IS_PENDING, 0)
                resolver.update(item, values, null, null)
            } catch (e: java.io.IOException) {
                resolver.delete(item, null, null)
                throw e
            }
            return app.getString(R.string.download_saved, name)
        } finally {
            conn.disconnect()
        }
    }

    /**
     * Back closes the panel or goes back in the page history. The callback is enabled only while
     * there is somewhere to go, so on the first page the system's own back-to-home (predictive
     * back on Android 13+) animation runs.
     */
    private fun syncBackCallback() {
        backCallback.isEnabled = camera != null || web.canGoBack()
    }

    /**
     * The https URL an ACTION_VIEW (App Link) or ACTION_SEND intent carries, or null. The intent's
     * action is cleared so the same link is not followed again on a later recreate.
     */
    private fun takeLinkedUrl(intent: Intent?): String? {
        if (intent == null) return null
        val raw = when (intent.action) {
            Intent.ACTION_VIEW -> intent.dataString
            Intent.ACTION_SEND -> intent.getStringExtra(Intent.EXTRA_TEXT)?.trim()
            else -> null
        } ?: return null
        intent.action = null
        val uri = Uri.parse(raw)
        if (uri.scheme?.lowercase() != "https" || uri.host.isNullOrEmpty()) return null
        return raw
    }

    override fun onPause() {
        camera?.pause()
        web.onPause()
        CookieManager.getInstance().flush()
        super.onPause()
    }

    override fun onResume() {
        super.onResume()
        web.onResume()
        camera?.resume()
    }

    override fun onDestroy() {
        camera?.destroy {}
        camera = null
        if (this::web.isInitialized) web.destroy()
        super.onDestroy()
    }

    // -- WebView clients -----------------------------------------------------

    private inner class Client : WebViewClient() {
        /** The current phone photo, for the one URL the glue loads it from. Runs off the UI thread. */
        override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? {
            val wanted = ScanGlue.photoSeqOf(origin, request.url.toString()) ?: return null
            val current = photo
            if (current == null || wanted != current.first) {
                return WebResourceResponse("text/plain", "utf-8", 404, "Gone", emptyMap(), ByteArrayInputStream(ByteArray(0)))
            }
            val headers = mapOf("Cache-Control" to "no-store", "Content-Length" to current.second.size.toString())
            return WebResourceResponse("image/jpeg", null, 200, "OK", headers, ByteArrayInputStream(current.second))
        }

        override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
            return when (nav.decide(request.url.toString(), request.isForMainFrame, request.isRedirect, request.hasGesture())) {
                Nav.STAY -> false
                Nav.BROWSER -> {
                    // A tap always opens; a navigation nobody tapped opens at most once in a while.
                    if (request.hasGesture() || nav.openUnasked(SystemClock.elapsedRealtime())) {
                        openExternal(request.url)
                    }
                    true
                }
                Nav.DROP -> true
            }
        }

        /** Called (posted to the UI thread) once a new main-frame document has committed. */
        override fun onPageStarted(view: WebView, url: String, favicon: android.graphics.Bitmap?) {
            if (url == BLANK) return // the app's own blank page over a refused one (below)
            progress.visibility = View.VISIBLE
            errorBox.visibility = View.GONE // a new page is loading: an older error no longer applies
            val allowed = nav.pageStarted(url)
            gatewayPage = GatewayUrl.isGateway(origin, url)
            pageGen++
            if (!allowed) {
                // A page from elsewhere that no navigation check saw (WebView does not ask about the
                // redirect after a form post): it never stays behind the app's chrome. The browser
                // gets it and the app goes back to the gateway, at most once in a few seconds; a page
                // that keeps coming back is only stopped, with Retry taking the person home.
                view.stopLoading()
                if (nav.handOff(SystemClock.elapsedRealtime())) {
                    openExternal(Uri.parse(url))
                    view.loadUrl(origin + "/")
                } else {
                    // Unload it: a stopped page that has already committed would keep running its
                    // scripts (and dialogs) behind the message.
                    view.loadUrl(BLANK)
                    progress.visibility = View.GONE
                    retryHome = true
                    errorText.text = getString(R.string.page_elsewhere)
                    errorBox.visibility = View.VISIBLE
                }
            }
        }

        override fun onPageFinished(view: WebView, url: String) {
            progress.visibility = View.GONE
            if (url == BLANK) {
                // Back must not reload the refused page under it; Retry takes the person home.
                view.clearHistory()
                syncBackCallback()
                return
            }
            val gateway = GatewayUrl.isGateway(origin, url)
            // Once a sign-in ends (or a page was refused), its pages leave the history: Back would
            // otherwise reopen the provider's old login page, or the refused page, every time.
            if (nav.pageFinished(url)) view.clearHistory()
            syncBackCallback()
            // A new document under an open panel (reload, sign-in bounce) would start the page's own
            // camera and take the lens from the panel, so the panel closes first; the glue is installed
            // fresh and the person taps Scan again.
            if (camera != null) closeCamera()
            if (gateway && GatewayUrl.pathStartsWith(url, "/scan")) {
                scanPageReady()
            } else {
                pendingCamera = false // the person went elsewhere before /scan loaded
            }
        }

        /** A gateway without the scan page (an older release) answers 404: say so instead of waiting. */
        override fun onReceivedHttpError(view: WebView, request: WebResourceRequest, errorResponse: WebResourceResponse) {
            if (!request.isForMainFrame) return
            val url = request.url.toString()
            // The reverse proxy answers 502/503/504 with its own bare page while the gateway is down or
            // restarting: show the app's own message with Retry instead.
            if (GatewayUrl.isGateway(origin, url) && errorResponse.statusCode in 502..504) {
                progress.visibility = View.GONE
                errorText.text = getString(R.string.gateway_down)
                errorBox.visibility = View.VISIBLE
                return
            }
            if (!pendingCamera) return
            if (GatewayUrl.isGateway(origin, url) && GatewayUrl.pathStartsWith(url, "/scan") && errorResponse.statusCode == 404) {
                pendingCamera = false
                scanReloaded = false
                Toast.makeText(this@MainActivity, getString(R.string.scan_page_missing), Toast.LENGTH_LONG).show()
            }
        }

        override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
            syncBackCallback()
        }

        override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
            if (!request.isForMainFrame) return
            progress.visibility = View.GONE
            errorText.text = when (error.errorCode) {
                WebViewClient.ERROR_HOST_LOOKUP, WebViewClient.ERROR_CONNECT, WebViewClient.ERROR_TIMEOUT -> getString(R.string.page_offline)
                else -> getString(R.string.page_error, error.description)
            }
            errorBox.visibility = View.VISIBLE
        }

        /**
         * The page's renderer crashed or was killed to free memory (it can happen during a camera
         * scan on a busy phone). A WebView whose renderer is gone can't be used again: replace it
         * with a fresh one on the same page instead of letting the whole app crash.
         */
        override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
            if (view !== web) return true // a pop-up's renderer: nothing of ours to rebuild
            val url = view.url?.takeIf { GatewayUrl.isGateway(origin, it) } ?: (origin + "/")
            if (camera != null) closeCamera()
            val params = view.layoutParams
            val index = root.indexOfChild(view)
            root.removeView(view)
            view.destroy()
            web = WebView(this@MainActivity).also { root.addView(it, index, params) }
            configureWebView()
            web.loadUrl(url)
            Toast.makeText(this@MainActivity, getString(R.string.page_reloaded), Toast.LENGTH_LONG).show()
            return true
        }
    }

    private inner class Chrome : WebChromeClient() {
        override fun onProgressChanged(view: WebView, newProgress: Int) {
            progress.progress = newProgress
        }

        /** The /scan page's live camera. Granted for the gateway's own pages only. */
        override fun onPermissionRequest(request: PermissionRequest) {
            val wantsOnlyCamera = request.resources.toList() == listOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE)
            if (!wantsOnlyCamera || !GatewayUrl.isGateway(origin, request.origin.toString())) {
                request.deny()
                return
            }
            if (checkSelfPermission(Manifest.permission.CAMERA) == PackageManager.PERMISSION_GRANTED) {
                request.grant(arrayOf(PermissionRequest.RESOURCE_VIDEO_CAPTURE))
            } else {
                pendingCameraPermission = request
                requestPermissions(arrayOf(Manifest.permission.CAMERA), REQ_PERM_WEB_CAMERA)
            }
        }

        override fun onShowFileChooser(
            webView: WebView,
            filePathCallback: ValueCallback<Array<Uri>>,
            fileChooserParams: FileChooserParams,
        ): Boolean {
            // Photo and CSV import are gateway features; a sign-in page has no business with files.
            if (!GatewayUrl.isGateway(origin, web.url)) return false
            fileCallback?.onReceiveValue(null)
            fileCallback = filePathCallback
            val intent = fileChooserParams.createIntent()
            try {
                startActivityForResult(intent, REQ_FILE)
            } catch (e: ActivityNotFoundException) {
                fileCallback = null
                return false
            }
            return true
        }

        /** target=_blank: catch the URL the popup would load and open it in the browser instead. */
        override fun onCreateWindow(view: WebView, isDialog: Boolean, isUserGesture: Boolean, resultMsg: Message): Boolean {
            if (!isUserGesture) return false
            val popup = WebView(this@MainActivity)
            popup.webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(v: WebView, request: WebResourceRequest): Boolean {
                    val url = request.url.toString()
                    if (GatewayUrl.isGateway(origin, url)) web.loadUrl(url) else openExternal(request.url)
                    v.post { v.destroy() }
                    return true
                }
            }
            popup.postDelayed({ popup.destroy() }, 10_000) // window.open('') and the like never navigate
            (resultMsg.obj as WebView.WebViewTransport).webView = popup
            resultMsg.sendToTarget()
            return true
        }
    }

    /**
     * `window.MtgNative`. The framework puts it on every page and frame the WebView loads (the
     * origin-scoped alternative is AndroidX-only), so each method answers only the gateway's main
     * frame: which document is showing is read when the call arrives ([pageGen], [gatewayPage]) and
     * checked again on the UI thread before acting, so a page calling on its way out can't act on
     * the gateway page that replaces it. The gateway's pages allow no frames (CSP), so a gateway
     * document is its main frame. Calls run on a binder thread.
     */
    private inner class NativeBridge {
        /** Runs [action] on the UI thread if a gateway page was showing at call time and still is. */
        private fun onGatewayPage(action: () -> Unit) {
            val gen = pageGen
            if (!gatewayPage) return
            web.post { if (gen == pageGen && gatewayPage && GatewayUrl.isGateway(origin, web.url)) action() }
        }

        @JavascriptInterface
        fun openCamera() = onGatewayPage { startScan() }

        @JavascriptInterface
        fun version(): String = if (gatewayPage) BuildConfig.VERSION_NAME else ""

        /** The /scan page reporting what it did with a photo (see [ScanGlue]). */
        @JavascriptInterface
        fun scanEvent(json: String) = onGatewayPage { camera?.onScanEvent(json) }
    }

    companion object {
        private const val BLANK = "about:blank"
        private const val REQ_FILE = 2
        private const val REQ_PERM_WEB_CAMERA = 3
        private const val REQ_PERM_NATIVE_CAMERA = 4
    }
}
