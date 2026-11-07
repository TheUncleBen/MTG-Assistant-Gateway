package local.mtgassistantgateway.app

/**
 * The rules of the app's own update check ([Updater] does the network and the install), kept free
 * of Android types so the unit tests can drive them.
 *
 * Updates come from the gateway the app is set up for: it serves the APK it ships at [APK_PATH]
 * and the build's facts about it at [LATEST_PATH] (version, size, the file's SHA-256 and the
 * signing certificate's), both behind the same sign-in as its pages. An update is offered only
 * when that APK is newer than this app and is signed with the same certificate as the installed
 * app; Android refuses any other update anyway, so an app signed with another key is never nagged
 * about a file it cannot install. A self-hoster who builds their own APK is offered their own.
 */
object AppUpdate {
    /** The gateway's JSON about the app it ships (app_page.py), and the APK itself. */
    const val LATEST_PATH = "/app/latest.json"
    const val APK_PATH = "/app/mtg-assistant-gateway.apk"
    /** How often the app looks on its own while it stays open: at most twice a day. */
    const val INTERVAL_MS = 12L * 3600 * 1000
    /**
     * The floor for the checks at set points (each cold launch, just after a sign-in): at most once
     * an hour, so the gateway is not asked on every launch. A manual check always runs.
     */
    const val MIN_GAP_MS = 3600L * 1000
    /** Upper bounds on what is read: the gateway's answer, the APK. */
    const val MAX_API_BYTES = 16 * 1024
    const val MAX_APK_BYTES = 100L * 1024 * 1024

    private val SEMVER = Regex("""^v?(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})$""")

    /** `0.7.12` or `v0.7.12` to the versionCode the build gives that version (build.gradle.kts). */
    fun versionCode(tag: String?): Int? {
        val m = SEMVER.find(tag?.trim() ?: return null) ?: return null
        val (major, minor, patch) = m.destructured.toList().map(String::toInt)
        return major * 1_000_000 + minor * 1_000 + patch
    }

    /** Where the gateway at [origin] (`https://host[:port]`) answers the check, or null for no gateway. */
    fun latestUrl(origin: String?): String? =
        if (origin != null && origin.startsWith("https://")) origin.trimEnd('/') + LATEST_PATH else null

    data class Asset(val name: String, val url: String, val size: Long)

    /** A newer app the gateway offers: the APK at [apk], with its file and certificate SHA-256s. */
    data class Offer(val version: String, val versionCode: Int, val apk: Asset, val sha256: String, val cert: String, val page: String)

    /**
     * The update the gateway at [origin] offers over [installedCode], or null: a version that is not
     * MAJOR.MINOR.PATCH, a version code that does not match it, an app no newer than this one, an odd
     * size, or a missing file or certificate SHA-256. The APK is always fetched from [origin] itself.
     */
    fun offer(
        origin: String,
        installedCode: Int,
        versionName: String?,
        versionCodeGiven: Int?,
        size: Long,
        sha256: String?,
        cert: String?,
    ): Offer? {
        val code = versionCode(versionName) ?: return null
        if (versionCodeGiven != null && versionCodeGiven != code) return null
        if (code <= installedCode) return null
        if (size <= 0 || size > MAX_APK_BYTES) return null
        val fileSum = fingerprint(sha256) ?: return null
        val certSum = fingerprint(cert) ?: return null
        val base = origin.trimEnd('/')
        val apk = Asset(APK_PATH.substringAfterLast('/'), base + APK_PATH, size)
        return Offer(versionName!!.trim().removePrefix("v"), code, apk, fileSum, certSum, "$base/app")
    }

    /** A SHA-256 as 64 lower-case hex digits, from either `ab:cd:..` or plain hex; null otherwise. */
    fun fingerprint(value: String?): String? {
        val hex = value?.replace(":", "")?.trim()?.lowercase() ?: return null
        return if (hex.length == 64 && hex.all { it in '0'..'9' || it in 'a'..'f' }) hex else null
    }

    /**
     * Is the published certificate one the installed app is signed with? [installed] are the
     * SHA-256s of the app's signers. A future key rotation (APK signature scheme v3) is not handled:
     * the rotated app would not be offered, and people would install it from the gateway's setup page once.
     */
    fun sameSigner(installed: Collection<String>, published: String?): Boolean {
        val p = fingerprint(published) ?: return false
        return installed.mapNotNull(::fingerprint).contains(p)
    }

    /** Whether an automatic check is due: [lastCheck] is when the last one ran (0 = never), [every] the gap. */
    fun due(lastCheck: Long, now: Long, every: Long = INTERVAL_MS): Boolean =
        lastCheck <= 0 || now < lastCheck || now - lastCheck >= every

    /** How long "Later" on the update bar keeps that version quiet: three days, not until the next check. */
    const val SNOOZE_MS = 3L * 24 * 3600 * 1000

    /**
     * Whether the automatic check keeps quiet about the version [offerCode]: "Later" put off
     * [snoozedCode] at [snoozedAt] (0 = never), less than [SNOOZE_MS] ago. A newer version is
     * offered at once; "Check for app updates" always offers it.
     */
    fun snoozed(offerCode: Int, snoozedCode: Int, snoozedAt: Long, now: Long): Boolean =
        snoozedAt > 0 && snoozedCode == offerCode && now >= snoozedAt && now - snoozedAt < SNOOZE_MS
}
