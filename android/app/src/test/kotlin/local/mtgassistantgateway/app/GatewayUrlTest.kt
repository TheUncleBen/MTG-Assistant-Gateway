package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class GatewayUrlTest {
    private fun ok(s: String) = (GatewayUrl.normalize(s) as GatewayUrl.Result.Ok).origin
    private fun bad(s: String) = GatewayUrl.normalize(s) as GatewayUrl.Result.Bad

    @Test fun bareHostGetsHttps() = assertEquals("https://mtg.example.com", ok("mtg.example.com"))
    @Test fun pathAndWhitespaceDropped() = assertEquals("https://mtg.example.com", ok("  https://MTG.example.com/scan?x=1 "))
    @Test fun defaultPortDropped() = assertEquals("https://mtg.example.com", ok("https://mtg.example.com:443/"))
    @Test fun customPortKept() = assertEquals("https://mtg.example.com:8443", ok("mtg.example.com:8443"))
    @Test fun httpRejected() = assertTrue(bad("http://mtg.example.com").reason.contains("https"))
    @Test fun userInfoRejected() = assertTrue(bad("https://me:secret@mtg.example.com").reason.contains("password"))
    @Test fun emptyRejected() = assertTrue(bad("   ").reason.isNotEmpty())
    @Test fun garbageRejected() = assertTrue(bad("https://exa mple").reason.isNotEmpty())

    @Test fun originOfHandlesPorts() {
        assertEquals("https://a.example", GatewayUrl.originOf("https://a.example:443/x"))
        assertEquals("https://a.example:8443", GatewayUrl.originOf("https://a.example:8443/x"))
        assertEquals("http://a.example", GatewayUrl.originOf("http://A.EXAMPLE/"))
        assertNull(GatewayUrl.originOf("mailto:someone@example.com"))
        assertNull(GatewayUrl.originOf("not a url"))
    }

    @Test fun isGatewayComparesWholeOrigin() {
        val origin = "https://mtg.example.com"
        assertTrue(GatewayUrl.isGateway(origin, "https://mtg.example.com/scan?x"))
        assertFalse(GatewayUrl.isGateway(origin, "https://mtg.example.com.evil.net/"))
        assertFalse(GatewayUrl.isGateway(origin, "http://mtg.example.com/"))
        assertFalse(GatewayUrl.isGateway(origin, "https://mtg.example.com:8443/"))
        assertFalse(GatewayUrl.isGateway(origin, null))
    }

    @Test fun pathPrefix() {
        assertTrue(GatewayUrl.pathStartsWith("https://x.example/scan", "/scan"))
        assertTrue(GatewayUrl.pathStartsWith("https://x.example/scan/sessions?q=1", "/scan"))
        assertFalse(GatewayUrl.pathStartsWith("https://x.example/scanner", "/scan"))
        assertFalse(GatewayUrl.pathStartsWith("https://x.example/", "/scan"))
        assertFalse(GatewayUrl.pathStartsWith(null, "/scan"))
    }

    @Test fun photoPathPrefix() {
        // The app's photo hand-off URL must match the prefix shouldInterceptRequest tests.
        val url = "https://x.example" + ScanGlue.PHOTO_PATH + "1"
        assertTrue(GatewayUrl.pathStartsWith(url, ScanGlue.PHOTO_DIR))
        assertFalse(GatewayUrl.pathStartsWith("https://x.example/__mtgassistant/photos/1", ScanGlue.PHOTO_DIR))
    }

    @Test fun ipv6Gateway() {
        val origin = ok("https://[::1]:8443/scan")
        assertEquals("https://[::1]:8443", origin)
        assertTrue(GatewayUrl.isGateway(origin, "https://[::1]:8443/x"))
        assertFalse(GatewayUrl.isGateway(origin, "https://[::2]:8443/x"))
    }

    @Test fun signInPages() {
        val o = "https://x.example"
        assertTrue(GatewayUrl.isSignInStart(o, "$o/login?next=/scan"))
        // /authorize and its consent page are part of a sign-in but never teach the provider:
        // Deny on the consent page redirects to the connecting application's own site.
        assertFalse(GatewayUrl.isSignInStart(o, "$o/authorize?client_id=a"))
        assertFalse(GatewayUrl.isSignInStart(o, "$o/authorize/confirm?state=s"))
        assertTrue(GatewayUrl.isSignInPage(o, "$o/authorize?client_id=a"))
        assertTrue(GatewayUrl.isSignInPage(o, "$o/login"))
        assertTrue(GatewayUrl.isConsentPage(o, "$o/authorize/confirm?state=s"))
        assertFalse(GatewayUrl.isConsentPage(o, "$o/authorize?client_id=a"))
        assertFalse(GatewayUrl.isConsentPage(o, "https://idp.example/authorize/confirm"))
        assertTrue(GatewayUrl.isSignInPage(o, "$o/auth/callback?code=x"))
        assertTrue(GatewayUrl.isSignInPage(o, "$o/authorize/confirm"))
        assertFalse(GatewayUrl.isSignInStart(o, "$o/auth/callback"))
        assertFalse(GatewayUrl.isSignInStart(o, "$o/loginx"))
        assertFalse(GatewayUrl.isSignInPage(o, "$o/account"))
        assertFalse(GatewayUrl.isSignInStart(o, "https://idp.example/login"))
    }

    @Test fun advertisedProvider() {
        assertEquals("https://auth.example.com", GatewayUrl.parseProvider("""{"idp_origin": "https://auth.example.com"}"""))
        assertEquals("https://auth.example.com:8443", GatewayUrl.parseProvider("""{"idp_origin":"https://auth.example.com:8443"}"""))
        assertNull(GatewayUrl.parseProvider("""{"idp_origin": "http://auth.example.com"}"""))
        assertNull(GatewayUrl.parseProvider("""{"idp_origin": "https://auth.example.com/path"}"""))
        assertNull(GatewayUrl.parseProvider("""{"idp_origin": "https://user@auth.example.com"}"""))
        assertNull(GatewayUrl.parseProvider("""{"idp_origin": "javascript:alert(1)"}"""))
        assertNull(GatewayUrl.parseProvider("""{"status": "ok"}"""))
        assertNull(GatewayUrl.parseProvider("not json"))
    }

    @Test fun join() {
        assertEquals("https://x.example/scan", GatewayUrl.join("https://x.example", "/scan"))
        assertEquals("https://x.example/scan", GatewayUrl.join("https://x.example/", "scan"))
    }
}
