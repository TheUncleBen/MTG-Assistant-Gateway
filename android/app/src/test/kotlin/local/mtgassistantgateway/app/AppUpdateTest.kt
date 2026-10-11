package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppUpdateTest {
    private val origin = "https://mtg.example.com"
    private val cert = "ab".repeat(32)
    private val sum = "cd".repeat(32)
    private fun offer(
        name: String? = "0.7.24",
        code: Int? = 7024,
        installed: Int = 7023,
        size: Long = 2_000_000,
        sha256: String? = sum,
        certSum: String? = cert,
    ) = AppUpdate.offer(origin, installed, name, code, size, sha256, certSum)

    @Test fun versionCodeMatchesTheBuild() {
        assertEquals(7012, AppUpdate.versionCode("v0.7.12"))
        assertEquals(1_002_003, AppUpdate.versionCode("1.2.3"))
        for (bad in listOf("v0.7", "0.7.12-rc1", "latest", "v01.2.3", "", null)) assertNull(bad, AppUpdate.versionCode(bad))
    }

    @Test fun theGatewayIsAskedNotGitHub() {
        assertEquals("https://mtg.example.com/app/latest.json", AppUpdate.latestUrl(origin))
        assertEquals("https://mtg.example.com/app/latest.json", AppUpdate.latestUrl("$origin/"))
        assertNull(AppUpdate.latestUrl(null))
        assertNull(AppUpdate.latestUrl("http://mtg.example.com"))
    }

    @Test fun aNewerAppIsOfferedFromTheGatewayItself() {
        val o = offer()!!
        assertEquals("0.7.24", o.version)
        assertEquals(7024, o.versionCode)
        assertEquals("https://mtg.example.com/app/mtg-assistant-gateway.apk", o.apk.url)
        assertEquals(2_000_000L, o.apk.size)
        assertEquals(sum, o.sha256)
        assertEquals(cert, o.cert)
        assertEquals("https://mtg.example.com/app", o.page)
        // the version code may be left out; the name alone gives it
        assertEquals(7024, offer(code = null)!!.versionCode)
        // fingerprints in apksigner's colon form are read too
        assertEquals(cert, offer(certSum = cert.uppercase().chunked(2).joinToString(":"))!!.cert)
    }

    @Test fun sameOrOlderIsNotOffered() {
        assertNull(offer(installed = 7024))
        assertNull(offer(installed = 8000))
    }

    @Test fun oddAnswersAreSkipped() {
        assertNull(offer(name = "nightly"))
        assertNull(offer(name = null))
        assertNull(offer(code = 7025)) // the code does not match the name
        assertNull(offer(sha256 = null))
        assertNull(offer(sha256 = "abc"))
        assertNull(offer(certSum = null))
    }

    @Test fun anOddSizeIsRefused() {
        assertNull(offer(size = 0))
        assertNull(offer(size = AppUpdate.MAX_APK_BYTES + 1))
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

    @Test fun checksAreTwiceADayAtMost() {
        val now = 1_800_000_000_000L
        assertTrue(AppUpdate.due(0, now))
        assertFalse(AppUpdate.due(now - 1000, now))
        assertTrue(AppUpdate.due(now - AppUpdate.INTERVAL_MS, now))
        assertTrue(AppUpdate.due(now + 60_000, now)) // the clock went back
    }

    @Test fun setPointChecksAreHourlyAtMost() {
        // Cold launch and sign-in: due after an hour, not after twelve; a check minutes ago blocks it.
        val now = 1_800_000_000_000L
        assertTrue(AppUpdate.due(0, now, AppUpdate.MIN_GAP_MS))
        assertFalse(AppUpdate.due(now - 10 * 60_000, now, AppUpdate.MIN_GAP_MS))
        assertTrue(AppUpdate.due(now - AppUpdate.MIN_GAP_MS, now, AppUpdate.MIN_GAP_MS))
        assertFalse(AppUpdate.due(now - AppUpdate.MIN_GAP_MS, now))
        assertTrue(AppUpdate.MIN_GAP_MS >= 3600_000L)
    }
}
