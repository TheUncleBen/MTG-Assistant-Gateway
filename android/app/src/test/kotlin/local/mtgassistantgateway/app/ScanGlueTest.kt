package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class ScanGlueTest {
    private val nonce = "0123456789abcdef0123456789abcdef"
    private val install = ScanGlue.install(nonce)

    @Test fun installUsesOnlyExportedHooks() {
        // Everything the glue calls on window.__scan must be in scan.js's export list.
        for (hook in ScanGlue.HOOKS) {
            assertTrue("glue should check $hook", install.contains("'$hook'"))
            assertTrue("glue should call $hook", install.contains("s.$hook("))
        }
        assertTrue("the photo comes by URL, never as base64", !install.contains("atob("))
        assertTrue("no fetch(data:) under the page's CSP", !install.contains("fetch("))
        assertTrue(install.contains("PHOTO_MAX = ${ScanGlue.PHOTO_MAX}"))
        assertTrue("no leftover Kotlin templates", !install.contains("$"))
        assertTrue(install.contains("var NONCE = '$nonce';"))
        assertTrue(!install.contains("__NONCE__"))
        for (bad in listOf("", "short", "not-hex-0123456789abcdef", "<script>")) {
            try {
                ScanGlue.install(bad)
                fail("accepted $bad")
            } catch (e: IllegalArgumentException) {
                assertEquals("bad nonce", e.message)
            }
        }
    }

    @Test fun installReportsThePagesEventsAndCompletion() {
        for (event in listOf("auto-added", "unsure", "unidentified", "undo")) {
            assertTrue(event, install.contains("'$event'"))
        }
        for (type in listOf("'no-card'", "'done'")) assertTrue(type, install.contains("type: $type"))
        assertTrue(install.contains("MtgNative.scanEvent(JSON.stringify(ev))"))
        // The camera panel is the only screen while photos are taken: the page must never start its own camera.
        assertTrue(!install.contains("showTab('camera')") || install.indexOf("showTab('camera')") > install.indexOf("end: function"))
    }

    @Test fun onPhotoEmbedsTheUrlAndTheGuide() {
        val url = "https://mtg.example.com" + ScanGlue.PHOTO_PATH + "7"
        val js = ScanGlue.onPhoto(url)
        assertTrue(js.contains("onPhoto('$url', null)"))
        assertTrue(js.endsWith("})();"))
        val guided = ScanGlue.onPhoto(url, floatArrayOf(0.1f, 0.2f, 0.5f, 0.25f))
        assertTrue(guided, guided.contains("onPhoto('$url', {x:0.1,y:0.2,w:0.5,h:0.25})"))
        for (bad in listOf(floatArrayOf(0f, 0f, 1f), floatArrayOf(0f, 0f, 1f, 1.5f), floatArrayOf(Float.NaN, 0f, 1f, 1f))) {
            try {
                ScanGlue.onPhoto(url, bad)
                fail("accepted ${bad.toList()}")
            } catch (e: IllegalArgumentException) {
                assertEquals("bad guide", e.message)
            }
        }
    }

    @Test fun onPhotoRejectsAnythingButAPhotoUrl() {
        for (bad in listOf("", "http://x/__mtgassistant/photo/1", "https://x/a'); alert(1); ('", "javascript:alert(1)", "https://x/a b")) {
            try {
                ScanGlue.onPhoto(bad)
                fail("accepted $bad")
            } catch (e: IllegalArgumentException) {
                assertEquals("bad photo url", e.message)
            }
        }
    }

    @Test fun onPhotoAcceptsAnIpv6Gateway() {
        val url = ScanGlue.photoUrl("https://[::1]:8443", 3)
        assertEquals("https://[::1]:8443/__mtgassistant/photo/3", url)
        assertTrue(ScanGlue.onPhoto(url).contains("onPhoto('$url', null)"))
        assertEquals(3, ScanGlue.photoSeqOf("https://[::1]:8443", url))
    }

    @Test fun photoSeqOfMatchesTheUrlTheAppHandsOver() {
        val origin = "https://mtg.example.com"
        assertEquals(1, ScanGlue.photoSeqOf(origin, ScanGlue.photoUrl(origin, 1)))
        assertEquals(42, ScanGlue.photoSeqOf(origin, "$origin/__mtgassistant/photo/42?x=1"))
        // Anything else under the directory is answered locally (404), never sent to the network.
        for (local in listOf("$origin/__mtgassistant/photo", "$origin/__mtgassistant/photo/", "$origin/__mtgassistant/photo/x", "$origin/__mtgassistant/photo/1/2", "$origin/__mtgassistant/photo/9999999999")) {
            assertEquals(local, -1, ScanGlue.photoSeqOf(origin, local))
        }
        for (other in listOf("https://evil.example/__mtgassistant/photo/1", "$origin/__mtgassistant/photos/1", "$origin/scan", "http://mtg.example.com/__mtgassistant/photo/1")) {
            assertEquals(other, null, ScanGlue.photoSeqOf(origin, other))
        }
    }

    @Test fun acceptedSeqReadsTheEvaluateResult() {
        assertEquals(12, ScanGlue.acceptedSeq("\"ok:12\""))
        for (other in listOf(null, "null", "\"ok\"", "\"no-scan\"", "\"no-glue\"", "\"ok:x\"", "ok:3")) {
            assertEquals(other, null, ScanGlue.acceptedSeq(other))
        }
    }

    @Test fun oneLinersAreExpressions() {
        for (js in listOf(ScanGlue.READY, ScanGlue.BEGIN, ScanGlue.END, ScanGlue.UNDO, ScanGlue.setContinuous(true))) {
            assertTrue(js, js.startsWith("(function(){"))
            assertTrue(js, js.endsWith("})();"))
        }
        assertTrue(ScanGlue.setContinuous(true).contains("setContinuous(true)"))
        assertTrue(ScanGlue.setContinuous(false).contains("setContinuous(false)"))
    }
}
