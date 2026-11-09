package local.mtgassistantgateway.app

import android.app.Activity
import android.app.DownloadManager
import android.content.ActivityNotFoundException
import android.content.ContentValues
import android.content.Intent
import android.content.pm.PackageInfo
import android.content.pm.PackageManager
import android.os.Environment
import android.os.Handler
import android.os.Looper
import android.provider.MediaStore
import android.util.Log
import android.webkit.CookieManager
import android.widget.Toast
import org.json.JSONException
import org.json.JSONObject
import java.io.File
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest

/**
 * The app updates itself from the gateway it is set up for, so nobody has to uninstall and
 * reinstall to move to a new version ([AppUpdate] holds the rules).
 *
 * 1. Check: at each cold launch and just after a sign-in (at most every [AppUpdate.MIN_GAP_MS]),
 *    on returning to the app (at most every [AppUpdate.INTERVAL_MS]), or when asked from the menu
 *    or the Account page: one request to the gateway's [AppUpdate.LATEST_PATH], with the sign-in
 *    the app's pages already hold (the gateway serves its APK only to its members). A newer app is
 *    offered only when its signing certificate is one this app is signed with.
 * 2. Download, when the person taps Update: the gateway's APK into the app's cache, then checked
 *    before anything is installed: the size and SHA-256 the gateway gave, same package, the offered
 *    version, signed by this app's own certificate.
 * 3. Save the checked APK to the phone's Downloads folder and open Downloads, where the person
 *    taps it and Android's own installer updates the app. The app does not install anything
 *    itself (0.7.47, owner 2026-11-09): it asks for no install permission
 *    (REQUEST_INSTALL_PACKAGES, UPDATE_PACKAGES_WITHOUT_USER_ACTION), the pattern Google Play
 *    Protect flagged on a sideloaded app that downloads and installs code. Android itself refuses
 *    an APK signed with another key.
 *
 * Nothing goes anywhere but the member's own gateway. A build with an empty UPDATE_REPO
 * (build.gradle.kts) never looks for updates.
 */
class Updater(private val activity: Activity, private val ui: Ui) {
    interface Ui {
        fun showOffer(offer: AppUpdate.Offer)
        fun showWorking(text: String)
        fun hideOffer()
    }

    private val main = Handler(Looper.getMainLooper())
    private val prefs = Prefs(activity)
    @Volatile private var busy = false

    /** The gateway's update answer, or null when this build looks for no updates or no gateway is set. */
    private fun latestUrl(): String? =
        if (BuildConfig.UPDATE_REPO.isEmpty()) null else AppUpdate.latestUrl(prefs.gatewayOrigin)

    val enabled: Boolean get() = latestUrl() != null

    /** The automatic check: only when one is due, and silent unless there is something to install. */
    fun checkIfDue() {
        if (enabled && AppUpdate.due(prefs.lastUpdateCheck, System.currentTimeMillis())) check(manual = false)
    }

    /**
     * The check at a set point (cold launch, just after a sign-in): like [checkIfDue], but due after
     * [AppUpdate.MIN_GAP_MS] instead of the twice-a-day interval.
     */
    fun checkAtSetPoint() {
        if (enabled && AppUpdate.due(prefs.lastUpdateCheck, System.currentTimeMillis(), AppUpdate.MIN_GAP_MS)) check(manual = false)
    }

    /** Looks now; [manual] also says when there is nothing new or the check failed. */
    fun check(manual: Boolean) {
        val api = latestUrl()
        if (api == null) {
            if (manual) toast(activity.getString(R.string.update_off))
            return
        }
        if (busy) return
        busy = true
        Thread {
            // Recorded whatever the outcome, so an unreachable gateway is not asked again on every return to the app.
            prefs.lastUpdateCheck = System.currentTimeMillis()
            val result: () -> Unit = try {
                val found = latest(api)
                when {
                    found == null -> ({ if (manual) toast(activity.getString(R.string.update_none, BuildConfig.VERSION_NAME)) })
                    !AppUpdate.sameSigner(installedCerts(), found.cert) ->
                        ({ if (manual) toast(activity.getString(R.string.update_other_key, found.version)) })
                    // "Later" keeps that version quiet for a while; a check asked for offers it anyway
                    !manual && AppUpdate.snoozed(
                        found.versionCode, prefs.snoozedUpdateCode, prefs.snoozedUpdateAt, System.currentTimeMillis(),
                    ) -> ({ })
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

    /** "Later" on the update bar: the automatic check keeps quiet about [offer]'s version for [AppUpdate.SNOOZE_MS]. */
    fun later(offer: AppUpdate.Offer) {
        prefs.snoozedUpdateCode = offer.versionCode
        prefs.snoozedUpdateAt = System.currentTimeMillis()
    }

    /** Downloads and checks [offer], then saves it to Downloads for the person to install. */
    fun install(offer: AppUpdate.Offer) {
        if (busy) return
        busy = true
        ui.showWorking(activity.getString(R.string.update_downloading, offer.version))
        val dir = File(activity.cacheDir, "update").apply { mkdirs() }
        val apk = File(dir, "update.apk")
        Thread {
            val problem: String? = try {
                download(offer, apk)
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
            val name = AppUpdate.downloadName(offer.version)
            val saveProblem = try {
                saveToDownloads(apk, name)
                null
            } catch (e: IOException) {
                Log.w(TAG, "update not saved: ${e.message}") // the reason stays out of the toast
                activity.getString(R.string.update_save_failed)
            } catch (e: RuntimeException) {
                Log.w(TAG, "update not saved: ${e.javaClass.simpleName} ${e.message}")
                activity.getString(R.string.update_save_failed)
            } finally {
                apk.delete()
            }
            main.post {
                busy = false
                if (!activity.isDestroyed) {
                    ui.hideOffer()
                    if (saveProblem != null) {
                        toast(saveProblem)
                    } else {
                        toast(activity.getString(R.string.update_saved, name))
                        openDownloads()
                    }
                }
            }
        }.start()
    }

    // -- the gateway --------------------------------------------------------

    /** The gateway's answer at [api] as an offer; null when it ships nothing newer (404 included). */
    private fun latest(api: String): AppUpdate.Offer? {
        val origin = api.removeSuffix(AppUpdate.LATEST_PATH)
        val text = try {
            fetchText(api, AppUpdate.MAX_API_BYTES)
        } catch (e: NotShipped) {
            return null
        }
        val o = JSONObject(text)
        return AppUpdate.offer(
            origin,
            BuildConfig.VERSION_CODE,
            o.optString("version_name").ifEmpty { null },
            if (o.has("version_code") && !o.isNull("version_code")) o.optInt("version_code") else null,
            o.optLong("size"),
            o.optString("sha256").ifEmpty { null },
            o.optString("cert_sha256").ifEmpty { null },
        )
    }

    /** The gateway ships no app (404): nothing to offer, not a failed check. */
    private class NotShipped : IOException("no app on this gateway")

    /**
     * A GET on the gateway with the session cookie the app's pages hold. Redirects are not followed:
     * the gateway answers a request without a session with a sign-in redirect (or a 401), and the
     * check then fails quietly until the person signs in again.
     */
    private fun open(url: String, accept: String): HttpURLConnection {
        val conn = URL(url).openConnection() as HttpURLConnection
        if (conn.url.protocol != "https") {
            conn.disconnect()
            throw IOException("not https")
        }
        conn.instanceFollowRedirects = false
        conn.useCaches = false
        conn.connectTimeout = 15_000
        conn.readTimeout = 30_000
        conn.setRequestProperty("Accept", accept)
        conn.setRequestProperty("User-Agent", "MTGAssistant/" + BuildConfig.VERSION_NAME + " update-check")
        CookieManager.getInstance().getCookie(url)?.let { conn.setRequestProperty("Cookie", it) }
        val code = conn.responseCode
        if (code != 200) {
            conn.disconnect()
            throw if (code == 404) NotShipped() else IOException("HTTP $code")
        }
        return conn
    }

    private fun fetchText(url: String, max: Int): String {
        val conn = open(url, "application/json")
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

    private fun download(offer: AppUpdate.Offer, to: File) {
        val asset = offer.apk
        val conn = open(asset.url, "application/octet-stream")
        try {
            var total = 0L
            val digest = MessageDigest.getInstance("SHA-256")
            conn.inputStream.use { input ->
                to.outputStream().use { out ->
                    val buf = ByteArray(64 * 1024)
                    while (true) {
                        val n = input.read(buf)
                        if (n < 0) break
                        total += n
                        if (total > asset.size || total > AppUpdate.MAX_APK_BYTES) throw IOException("larger than published")
                        digest.update(buf, 0, n)
                        out.write(buf, 0, n)
                    }
                }
            }
            if (total != asset.size) throw IOException("incomplete download")
            val sum = digest.digest().joinToString("") { "%02x".format(it) }
            if (sum != offer.sha256) throw IOException("not the file the gateway described")
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

    // -- saving to Downloads ---------------------------------------------------

    /** The checked APK into the phone's Downloads folder (MediaStore, no storage permission from Android 10). */
    private fun saveToDownloads(apk: File, name: String) {
        val resolver = activity.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Downloads.DISPLAY_NAME, name)
            put(MediaStore.Downloads.MIME_TYPE, AppUpdate.APK_TYPE)
            put(MediaStore.Downloads.RELATIVE_PATH, Environment.DIRECTORY_DOWNLOADS)
            put(MediaStore.Downloads.IS_PENDING, 1)
        }
        val uri = resolver.insert(MediaStore.Downloads.EXTERNAL_CONTENT_URI, values)
            ?: throw IOException("Downloads refused the file")
        try {
            (resolver.openOutputStream(uri) ?: throw IOException("Downloads gave no stream")).use { out ->
                apk.inputStream().use { it.copyTo(out) }
            }
            resolver.update(uri, ContentValues().apply { put(MediaStore.Downloads.IS_PENDING, 0) }, null, null)
        } catch (e: Exception) {
            runCatching { resolver.delete(uri, null, null) }
            throw if (e is IOException) e else IOException(e.message, e)
        }
    }

    /** Android's own Downloads list, where the saved update is tapped to install it. */
    private fun openDownloads() {
        try {
            activity.startActivity(Intent(DownloadManager.ACTION_VIEW_DOWNLOADS).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
        } catch (e: ActivityNotFoundException) {
            toast(activity.getString(R.string.update_open_downloads))
        }
    }

    private fun toast(text: String) = Toast.makeText(activity, text, Toast.LENGTH_LONG).show()

    companion object {
        private const val TAG = "MtgAssistant"
        /** Whether MainActivity is resumed (set from its onResume/onPause). */
        @Volatile var inFront = false
    }
}
