package local.mtgassistantgateway.app

/**
 * The rules of the app's own update check ([Updater] does the network and the install), kept free
 * of Android types so the unit tests can drive them.
 *
 * Updates come from the project's GitHub releases: the release job attaches the signed APK
 * ([APK_ASSET]) and the build's metadata ([META_ASSET], with the signing certificate's SHA-256).
 * An update is offered only when the release is newer than this app and is signed with the same
 * certificate as the installed app; Android refuses any other update anyway, so a fork signed with
 * its own key is never nagged about releases it cannot install.
 */
object AppUpdate {
    const val APK_ASSET = "mtg-assistant-gateway-release.apk"
    const val META_ASSET = "mtg-assistant-gateway.json"
    /** How often the app looks on its own: on launch, then at most twice a day. */
    const val INTERVAL_MS = 12L * 3600 * 1000
    /** Upper bounds on what is read: GitHub's release answer, the metadata file, the APK. */
    const val MAX_API_BYTES = 1_000_000
    const val MAX_META_BYTES = 16 * 1024
    const val MAX_APK_BYTES = 100L * 1024 * 1024

    private val SEMVER = Regex("""^v?(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})\.(0|[1-9]\d{0,2})$""")
    private val REPO = Regex("""^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$""")

    /** `0.7.12` or `v0.7.12` to the versionCode the build gives that version (build.gradle.kts). */
    fun versionCode(tag: String?): Int? {
        val m = SEMVER.find(tag?.trim() ?: return null) ?: return null
        val (major, minor, patch) = m.destructured.toList().map(String::toInt)
        return major * 1_000_000 + minor * 1_000 + patch
    }

    /** GitHub's "latest release" address for [repo] (`owner/name`), or null when updates are off. */
    fun latestApi(repo: String): String? =
        if (REPO.matches(repo)) "https://api.github.com/repos/$repo/releases/latest" else null

    /** Assets are fetched only from [repo]'s own release downloads. */
    fun isReleaseDownload(repo: String, url: String?): Boolean =
        url != null && REPO.matches(repo) &&
            url.startsWith("https://github.com/$repo/releases/download/", ignoreCase = true) &&
            !url.contains("..") && !url.contains('?') && !url.contains('#')

    data class Asset(val name: String, val url: String, val size: Long)

    /** A newer release this app can install. */
    data class Offer(val version: String, val versionCode: Int, val apk: Asset, val meta: Asset, val page: String)

    /**
     * The update GitHub's latest release offers over [installedCode], or null: drafts, pre-releases,
     * tags that are not MAJOR.MINOR.PATCH, releases no newer than this app and releases without both
     * files from [repo]'s downloads are skipped.
     */
    fun offer(
        repo: String,
        installedCode: Int,
        tag: String?,
        draft: Boolean,
        prerelease: Boolean,
        page: String?,
        assets: List<Asset>,
    ): Offer? {
        if (draft || prerelease) return null
        val code = versionCode(tag) ?: return null
        if (code <= installedCode) return null
        fun asset(name: String) = assets.firstOrNull { it.name == name && isReleaseDownload(repo, it.url) }
        val apk = asset(APK_ASSET) ?: return null
        val meta = asset(META_ASSET) ?: return null
        if (apk.size <= 0 || apk.size > MAX_APK_BYTES) return null
        val releases = "https://github.com/$repo/releases"
        val link = if (page != null && page.startsWith("$releases/tag/", ignoreCase = true)) page else releases
        return Offer(tag!!.trim().removePrefix("v"), code, apk, meta, link)
    }

    /** A SHA-256 as 64 lower-case hex digits, from either `ab:cd:..` or plain hex; null otherwise. */
    fun fingerprint(value: String?): String? {
        val hex = value?.replace(":", "")?.trim()?.lowercase() ?: return null
        return if (hex.length == 64 && hex.all { it in '0'..'9' || it in 'a'..'f' }) hex else null
    }

    /**
     * Is the published certificate one the installed app is signed with? [installed] are the
     * SHA-256s of the app's current signers (one, unless the key was rotated).
     */
    fun sameSigner(installed: Collection<String>, published: String?): Boolean {
        val p = fingerprint(published) ?: return false
        return installed.mapNotNull(::fingerprint).contains(p)
    }

    /** Whether an automatic check is due: [lastCheck] is when the last one ran (0 = never). */
    fun due(lastCheck: Long, now: Long): Boolean = lastCheck <= 0 || now < lastCheck || now - lastCheck >= INTERVAL_MS

    /** The certificate SHA-256 in the build's metadata file ([META_ASSET]); no JSON library needed. */
    fun metaCert(json: String): String? =
        Regex("\"cert_sha256\"\\s*:\\s*\"([0-9a-fA-F:]{64,95})\"").find(json)?.groupValues?.get(1)?.let(::fingerprint)
}
