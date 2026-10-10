package local.mtgassistantgateway.app

import android.app.Activity
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageInstaller
import android.content.pm.PackageManager
import android.os.Build
import android.os.Handler
import android.os.Looper
import android.widget.Toast
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * The app updates itself from the project's GitHub releases, so nobody has to uninstall and
 * reinstall to move to a new version ([AppUpdate] holds the rules).
 *
 * 1. Check: on launch, at most every [AppUpdate.INTERVAL_MS], or when asked from the menu, one
 *    anonymous request to GitHub's "latest release" API for [BuildConfig.UPDATE_REPO]. A newer
 *    release is offered only when its published signing certificate is one this app is signed with.
 * 2. Download, when the person taps Update: the release's APK into the app's cache, then checked
 *    before anything is installed: same package, the offered version, signed by this app's own
 *    certificate.
 * 3. Install through Android's PackageInstaller. Android asks once to allow installs from this app
 *    ("Install unknown apps"). From Android 12, an app updating itself may skip the confirmation
 *    (PackageInstaller.SessionParams.setRequireUserAction); Android still decides, and when it asks,
 *    [InstallResultReceiver] shows its confirmation. Android itself refuses an APK signed with another key.
 *
 * Nothing about the person or their gateway is sent: GitHub sees an app version in the User-Agent
 * and, like any website, the phone's IP address.
 */
class Updater(private val activity: Activity, private val ui: Ui) {
    interface Ui {
        fun showOffer(offer: AppUpdate.Offer)
        fun showWorking(text: String)
        fun hideOffer()
    }

    private val main = Handler(Looper.getMainLooper())
    private val prefs = Prefs(activity)
    private val repo = BuildConfig.UPDATE_REPO
    @Volatile private var busy = false

    val enabled: Boolean get() = AppUpdate.latestApi(repo) != null

    /** The automatic check: only when one is due, and silent unless there is something to install. */
    fun checkIfDue() {
        if (enabled && AppUpdate.due(prefs.lastUpdateCheck, System.currentTimeMillis())) check(manual = false)
    }

    /** Looks now; [manual] also says when there is nothing new or the check failed. */
    fun check(manual: Boolean) {
        val api = AppUpdate.latestApi(repo)
        if (api == null) {
            if (manual) toast(activity.getString(R.string.update_off))
            return
        }
        if (busy) return
        busy = true
        Thread {
            // Recorded whatever the outcome, so an unreachable GitHub is not asked again on every return to the app.
            prefs.lastUpdateCheck = System.currentTimeMillis()
            val result: () -> Unit = try {
                val found = latest(api)
                when {
                    found == null -> ({ if (manual) toast(activity.getString(R.string.update_none, BuildConfig.VERSION_NAME)) })
                    !AppUpdate.sameSigner(installedCerts(), AppUpdate.metaCert(fetchText(found.meta.url, AppUpdate.MAX_META_BYTES))) ->
                        ({ if (manual) toast(activity.getString(R.string.update_other_key, found.version)) })
                    else -> ({ ui.showOffer(found) })
                }
            } catch (e: IOException) {
                ({ if (manual) toast(activity.getString(R.string.update_check_failed)) })
            } catch (e: JSONException) {
                ({ if (manual) toast(activity.getString(R.string.update_check_failed)) })
            } catch (e: RuntimeException) { // an odd PackageManager answer must not crash the app
                ({ if (manual) toast(activity.getString(R.string.update_check_failed)) })
            }
            main.post { busy = false; if (!activity.isDestroyed) result() }
        }.start()
    }

    /** Downloads, checks and installs [offer]. */
    fun install(offer: AppUpdate.Offer) {
        if (busy) return
        busy = true
        ui.showWorking(activity.getString(R.string.update_downloading, offer.version))
        val dir = File(activity.cacheDir, "update").apply { mkdirs() }
        val apk = File(dir, "update.apk")
        Thread {
            val problem: String? = try {
                download(offer.apk, apk)
                verify(apk, offer)
            } catch (e: IOException) {
                activity.getString(R.string.update_download_failed)
            } catch (e: RuntimeException) {
                activity.getString(R.string.update_not_an_app)
            }
            if (problem != null) {
                apk.delete()
                main.post { busy = false; if (!activity.isDestroyed) { ui.hideOffer(); toast(problem) } }
                return@Thread
            }
            main.post { ui.showWorking(activity.getString(R.string.update_installing, offer.version)) }
            val installProblem = try {
                commit(apk)
                null
            } catch (e: IOException) {
                activity.getString(R.string.update_install_failed, e.message ?: "")
            } catch (e: RuntimeException) { // SecurityException included
                activity.getString(R.string.update_install_failed, e.message ?: "")
            } finally {
                apk.delete() // the session holds its own copy
            }
            main.post {
                busy = false
                if (!activity.isDestroyed) {
                    ui.hideOffer()
                    toast(installProblem ?: activity.getString(R.string.update_handed_over))
                }
            }
        }.start()
    }

    // -- GitHub -------------------------------------------------------------

    private fun latest(api: String): AppUpdate.Offer? {
        val o = JSONObject(fetchText(api, AppUpdate.MAX_API_BYTES))
        val list = o.optJSONArray("assets")
        val assets = (0 until (list?.length() ?: 0)).mapNotNull { i ->
            val a = list!!.optJSONObject(i) ?: return@mapNotNull null
            AppUpdate.Asset(a.optString("name"), a.optString("browser_download_url"), a.optLong("size"))
        }
        return AppUpdate.offer(
            repo,
            BuildConfig.VERSION_CODE,
            o.optString("tag_name").ifEmpty { null },
            o.optBoolean("draft"),
            o.optBoolean("prerelease"),
            o.optString("html_url").ifEmpty { null },
            assets,
        )
    }

    private fun open(url: String): HttpURLConnection {
        val conn = URL(url).openConnection() as HttpURLConnection
        conn.instanceFollowRedirects = true // release files redirect to GitHub's download host (https only)
        conn.useCaches = false
        conn.connectTimeout = 15_000
        conn.readTimeout = 30_000
        conn.setRequestProperty("Accept", if (url.startsWith("https://api.github.com/")) "application/vnd.github+json" else "application/octet-stream")
        conn.setRequestProperty("User-Agent", "MTGAssistant/" + BuildConfig.VERSION_NAME + " update-check")
        if (conn.responseCode != 200) {
            conn.disconnect()
            throw IOException("HTTP ${conn.responseCode}")
        }
        if (conn.url.protocol != "https") {
            conn.disconnect()
            throw IOException("not https")
        }
        return conn
    }

    private fun fetchText(url: String, max: Int): String {
        val conn = open(url)
        try {
            val out = java.io.ByteArrayOutputStream()
            conn.inputStream.use { input ->
                val buf = ByteArray(8192)
                while (true) {
                    val n = input.read(buf)
                    if (n < 0) break
                    out.write(buf, 0, n)
                    if (out.size() > max) throw IOException("answer too large")
                }
            }
            return String(out.toByteArray(), Charsets.UTF_8)
        } finally {
            conn.disconnect()
        }
    }

    private fun download(asset: AppUpdate.Asset, to: File) {
        val conn = open(asset.url)
        try {
            var total = 0L
            conn.inputStream.use { input ->
                to.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        total += n
                        if (total > asset.size || total > AppUpdate.MAX_APK_BYTES) throw IOException("larger than published")
                        out.write(buf, 0, n)
                    }
                }
            }
            if (total != asset.size) throw IOException("incomplete download")
        } finally {
            conn.disconnect()
        }
    }

    // -- checks before installing -------------------------------------------

    /** Null when [apk] is the offered version of this app, signed with this app's certificate. */
    private fun verify(apk: File, offer: AppUpdate.Offer): String? {
        val info = archiveInfo(apk) ?: return activity.getString(R.string.update_not_an_app)
        if (info.packageName != activity.packageName) return activity.getString(R.string.update_not_an_app)
        if (info.longVersionCode != offer.versionCode.toLong()) return activity.getString(R.string.update_not_an_app)
        val signers = certsOf(info)
        val mine = installedCerts()
        if (signers.isEmpty() || mine.isEmpty() || !signers.all { s -> AppUpdate.sameSigner(mine, s) }) {
            return activity.getString(R.string.update_other_key, offer.version)
        }
        return null
    }

    @Suppress("DEPRECATION")
    private fun archiveInfo(apk: File): PackageInfo? {
        val pm = activity.packageManager
        val withSigners = pm.getPackageArchiveInfo(apk.path, PackageManager.GET_SIGNING_CERTIFICATES)
        if (withSigners?.signingInfo != null) return withSigners
        // Some Android versions leave signingInfo empty for a file that is not installed; the older
        // flag still reads the file's certificates there.
        return pm.getPackageArchiveInfo(apk.path, PackageManager.GET_SIGNATURES)
    }

    @Suppress("DEPRECATION")
    private fun certsOf(info: PackageInfo): List<String> {
        val si = info.signingInfo
        val sigs = when {
            si == null -> info.signatures?.toList().orEmpty()
            si.hasMultipleSigners() -> si.apkContentsSigners.toList()
            else -> si.signingCertificateHistory.toList()
        }
        return sigs.map { sig -> MessageDigest.getInstance("SHA-256").digest(sig.toByteArray()).joinToString("") { "%02x".format(it) } }
    }

    private fun installedCerts(): List<String> =
        certsOf(activity.packageManager.getPackageInfo(activity.packageName, PackageManager.GET_SIGNING_CERTIFICATES))

    // -- install ------------------------------------------------------------

    private fun commit(apk: File) {
        val installer = activity.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(activity.packageName)
            setSize(apk.length())
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
        }
        val id = installer.createSession(params)
        try {
            installer.openSession(id).use { session ->
                session.openWrite("base.apk", 0, apk.length()).use { out ->
                    apk.inputStream().use { it.copyTo(out) }
                    session.fsync(out)
                }
                // Mutable: PackageInstaller adds the result to it. The receiver is not exported, so
                // only this session's result reaches it.
                val result = PendingIntent.getBroadcast(
                    activity,
                    id,
                    Intent(activity, InstallResultReceiver::class.java).setPackage(activity.packageName),
                    PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE,
                )
                session.commit(result.intentSender)
            }
        } catch (e: Exception) {
            runCatching { installer.abandonSession(id) }
            throw if (e is IOException || e is SecurityException) e else IOException(e.message, e)
        }
    }

    private fun toast(text: String) = Toast.makeText(activity, text, Toast.LENGTH_LONG).show()

    /**
     * PackageInstaller's answer. When Android wants the person to confirm (or to allow installs
     * from this app first), it hands over its own confirmation screen. That is shown at once while
     * the app is in front; Android does not let an app in the background open a screen, so then it
     * waits for the app to come back ([showPendingConfirmation]). After a successful update Android
     * restarts nothing: the update closed the app and it opens again from its icon.
     */
    class InstallResultReceiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            when (val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)) {
                PackageInstaller.STATUS_PENDING_USER_ACTION -> {
                    val confirm = confirmation(context, intent) ?: return
                    if (inFront) {
                        launch(context, confirm)
                    } else {
                        pendingConfirmation = confirm
                    }
                }
                PackageInstaller.STATUS_SUCCESS -> Unit
                PackageInstaller.STATUS_FAILURE_ABORTED -> Unit // the person said no
                else -> {
                    val why = intent.getStringExtra(PackageInstaller.EXTRA_STATUS_MESSAGE).orEmpty()
                    Toast.makeText(context, context.getString(R.string.update_install_failed, "$status $why".trim()), Toast.LENGTH_LONG).show()
                }
            }
        }
    }

    companion object {
        /** Whether MainActivity is resumed (set from its onResume/onPause). */
        @Volatile var inFront = false
        @Volatile private var pendingConfirmation: Intent? = null

        /** Shows a confirmation Android asked for while the app was in the background. */
        fun showPendingConfirmation(context: Context) {
            val confirm = pendingConfirmation ?: return
            pendingConfirmation = null
            launch(context, confirm)
        }

        /**
         * The confirmation screen PackageInstaller handed over, only if it belongs to a system app
         * (the platform's installer), with any URI grants removed: this receiver only ever starts
         * Android's own install confirmation.
         */
        private fun confirmation(context: Context, result: Intent): Intent? {
            val confirm = if (Build.VERSION.SDK_INT >= 34) {
                result.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
            } else {
                @Suppress("DEPRECATION") result.getParcelableExtra(Intent.EXTRA_INTENT)
            } ?: return null
            confirm.removeFlags(
                Intent.FLAG_GRANT_READ_URI_PERMISSION or Intent.FLAG_GRANT_WRITE_URI_PERMISSION or
                    Intent.FLAG_GRANT_PERSISTABLE_URI_PERMISSION or Intent.FLAG_GRANT_PREFIX_URI_PERMISSION,
            )
            val target = context.packageManager.resolveActivity(confirm, 0)?.activityInfo?.applicationInfo ?: return null
            return if (target.flags and android.content.pm.ApplicationInfo.FLAG_SYSTEM != 0) confirm else null
        }

        private fun launch(context: Context, confirm: Intent) {
            try {
                context.startActivity(confirm.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            } catch (e: RuntimeException) {
                Toast.makeText(context, R.string.update_confirm_failed, Toast.LENGTH_LONG).show()
            }
        }
    }
}
