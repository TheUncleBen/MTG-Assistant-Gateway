package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppUpdateTest {
    private val repo = "TheUncleBen/MTG-Assistant-Gateway"
    private val dl = "https://github.com/$repo/releases/download/v0.7.12"
    private val cert = "ab".repeat(32)
    private fun assets(apkUrl: String = "$dl/${AppUpdate.APK_ASSET}", size: Long = 2_000_000) = listOf(
        AppUpdate.Asset(AppUpdate.APK_ASSET, apkUrl, size),
        AppUpdate.Asset(AppUpdate.META_ASSET, "$dl/${AppUpdate.META_ASSET}", 300),
        AppUpdate.Asset("mtg-assistant-gateway-release.aab", "$dl/mtg-assistant-gateway-release.aab", 2_100_000),
    )
    private fun offer(
        tag: String? = "v0.7.12",
        installed: Int = 7011,
        draft: Boolean = false,
        prerelease: Boolean = false,
        page: String? = "https://github.com/$repo/releases/tag/v0.7.12",
        list: List<AppUpdate.Asset> = assets(),
    ) = AppUpdate.offer(repo, installed, tag, draft, prerelease, page, list)

    @Test fun versionCodeMatchesTheBuild() {
        assertEquals(7012, AppUpdate.versionCode("v0.7.12"))
        assertEquals(1_002_003, AppUpdate.versionCode("1.2.3"))
        for (bad in listOf("v0.7", "0.7.12-rc1", "latest", "v01.2.3", "", null)) assertNull(bad, AppUpdate.versionCode(bad))
    }

    @Test fun aNewerReleaseIsOffered() {
        val o = offer()!!
        assertEquals("0.7.12", o.version)
        assertEquals(7012, o.versionCode)
        assertEquals("$dl/${AppUpdate.APK_ASSET}", o.apk.url)
        assertEquals("$dl/${AppUpdate.META_ASSET}", o.meta.url)
        assertEquals("https://github.com/$repo/releases/tag/v0.7.12", o.page)
    }

    @Test fun sameOrOlderIsNotOffered() {
        assertNull(offer(installed = 7012))
        assertNull(offer(installed = 8000))
    }

    @Test fun draftsPreReleasesAndOddTagsAreSkipped() {
        assertNull(offer(draft = true))
        assertNull(offer(prerelease = true))
        assertNull(offer(tag = "nightly"))
        assertNull(offer(tag = null))
    }

    @Test fun bothFilesMustComeFromThisRepositorysDownloads() {
        assertNull(offer(list = assets(apkUrl = "https://evil.example/${AppUpdate.APK_ASSET}")))
        assertNull(offer(list = assets(apkUrl = "https://github.com/someone/else/releases/download/v0.7.12/${AppUpdate.APK_ASSET}")))
        assertNull(offer(list = assets(apkUrl = "http://github.com/$repo/releases/download/v0.7.12/${AppUpdate.APK_ASSET}")))
        assertNull(offer(list = assets(apkUrl = "$dl/../../x/${AppUpdate.APK_ASSET}")))
        assertNull(offer(list = assets().filter { it.name != AppUpdate.META_ASSET }))
        assertNull(offer(list = assets().filter { it.name != AppUpdate.APK_ASSET }))
        // GitHub may spell the owner differently from the build setting.
        assertNotNull(offer(list = assets(apkUrl = "https://github.com/theuncleben/mtg-assistant-gateway/releases/download/v0.7.12/${AppUpdate.APK_ASSET}")))
    }

    @Test fun anOddSizeIsRefused() {
        assertNull(offer(list = assets(size = 0)))
        assertNull(offer(list = assets(size = AppUpdate.MAX_APK_BYTES + 1)))
    }

    @Test fun aReleasePageElsewhereFallsBackToTheReleasesList() =
        assertEquals("https://github.com/$repo/releases", offer(page = "https://evil.example/")!!.page)

    @Test fun updatesCanBeTurnedOffAtBuildTime() {
        assertNull(AppUpdate.latestApi(""))
        assertNull(AppUpdate.latestApi("not a repo"))
        assertEquals("https://api.github.com/repos/$repo/releases/latest", AppUpdate.latestApi(repo))
    }

    @Test fun onlyTheSameSignerCounts() {
        assertTrue(AppUpdate.sameSigner(listOf(cert), cert))
        assertTrue(AppUpdate.sameSigner(listOf(cert), cert.uppercase().chunked(2).joinToString(":")))
        assertTrue(AppUpdate.sameSigner(listOf("cd".repeat(32), cert), cert)) // a rotated key's history
        assertFalse(AppUpdate.sameSigner(listOf("cd".repeat(32)), cert))
        assertFalse(AppUpdate.sameSigner(listOf(cert), null))
        assertFalse(AppUpdate.sameSigner(listOf(cert), "abc"))
        assertFalse(AppUpdate.sameSigner(emptyList(), cert))
    }

    @Test fun metaCertIsRead() {
        val json = """{"version_name": "0.7.12", "version_code": 7012, "sha256": "${"0".repeat(64)}", "cert_sha256": "$cert", "size": 1}"""
        assertEquals(cert, AppUpdate.metaCert(json))
        assertNull(AppUpdate.metaCert("""{"cert_sha256": "nothex"}"""))
        assertNull(AppUpdate.metaCert("{}"))
    }

    @Test fun checksAreTwiceADayAtMost() {
        val now = 1_800_000_000_000L
        assertTrue(AppUpdate.due(0, now))
        assertFalse(AppUpdate.due(now - 1000, now))
        assertTrue(AppUpdate.due(now - AppUpdate.INTERVAL_MS, now))
        assertTrue(AppUpdate.due(now + 60_000, now)) // the clock went back
    }
}
