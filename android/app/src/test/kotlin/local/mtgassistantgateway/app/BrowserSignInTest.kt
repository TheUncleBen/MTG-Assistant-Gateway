package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class BrowserSignInTest {
    @Test fun verifierAndChallengeMatchTheGateway() {
        val v = BrowserSignIn.newVerifier()
        assertTrue(Regex("[A-Za-z0-9_-]{43}").matches(v))
        assertNotEquals(v, BrowserSignIn.newVerifier())
        // RFC 7636 appendix B: the same S256 the gateway's app_signin.challenge_of computes.
        assertEquals(
            "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
            BrowserSignIn.challengeOf("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"),
        )
    }

    @Test fun startUrlKeepsTheNextPathOnTheGateway() {
        val c = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        assertEquals(
            "https://mtg.example.com/login?next=%2Fdecks%3Fq%3D1&app_challenge=$c",
            BrowserSignIn.startUrl("https://mtg.example.com", "/decks?q=1", c, fresh = false),
        )
        assertTrue(BrowserSignIn.startUrl("https://g", "/", c, fresh = true).endsWith("&fresh=1"))
        for (bad in listOf(null, "", "https://evil.test/", "//evil.test", "/\\evil", "decks", "/" + "a".repeat(250))) {
            assertEquals("/", BrowserSignIn.safeNext(bad))
        }
    }

    @Test fun onlyOurLinkWithAWellFormedCodeCounts() {
        val code = "a".repeat(43)
        assertEquals(code, BrowserSignIn.codeOf("mtgassistant-signin", "signin", code))
        assertNull(BrowserSignIn.codeOf("https", "signin", code))
        assertNull(BrowserSignIn.codeOf("mtgassistant-signin", "other", code))
        assertNull(BrowserSignIn.codeOf("mtgassistant-signin", "signin", null))
        assertNull(BrowserSignIn.codeOf("mtgassistant-signin", "signin", "short"))
        assertNull(BrowserSignIn.codeOf("mtgassistant-signin", "signin", code.dropLast(1) + "&"))
        assertEquals("code=$code&verifier=$code", String(BrowserSignIn.finishBody(code, code)))
    }

    @Test fun anAuthTabOnTheAppsSchemeIsAskedForInTheStartUrl() {
        val c = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        assertEquals(
            "https://g/login?next=%2F&app_challenge=$c&fresh=1&app_return=scheme",
            BrowserSignIn.startUrl("https://g", "/", c, fresh = true, schemeReturn = true),
        )
        assertEquals("https://g/login?next=%2F&app_challenge=$c", BrowserSignIn.startUrl("https://g", "/", c, fresh = false))
    }

    @Test fun aCustomTabSaysSoInTheStartUrl() {
        val c = "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"
        assertEquals(
            "https://g/login?next=%2F&app_challenge=$c&fresh=1&app_return=intent",
            BrowserSignIn.startUrl("https://g", "/", c, fresh = true, customTab = true),
        )
        assertEquals(
            "https://g/login?next=%2F&app_challenge=$c&app_return=intent",
            BrowserSignIn.startUrl("https://g", "/", c, fresh = false, customTab = true),
        )
    }

    @Test fun theThisAppLineSaysWhetherSignInComesBackByItself() {
        val has = setOf("com.android.chrome")
        val status = { default: String?, installed: List<String>, system: Boolean ->
            BrowserSignIn.returnStatus(BrowserSignIn.pickAuthTabBrowser(default, installed, { system }) { it in has })
        }
        // Samsung Internet as the default, with the phone's own Chrome: the Auth Tab in Chrome
        assertEquals("auto", status("com.sec.android.app.sbrowser", listOf("com.android.chrome"), true))
        // Chrome missing, or not the phone's own: a Custom Tab, which may wait for a tap
        assertEquals("tap", status("com.sec.android.app.sbrowser", listOf("com.sec.android.app.sbrowser"), true))
        assertEquals("tap", status("org.firefox", listOf("com.android.chrome"), false))
        assertEquals("tap", status(null, emptyList(), true))
    }

    @Test fun theDefaultBrowserFirstThenAChromeThatCameWithThePhoneAndNothingElse() {
        val has = setOf("com.android.chrome", "org.other.browser", "evil")
        val system = setOf("com.android.chrome", "com.sec.android.app.sbrowser")
        val pick = { default: String?, installed: List<String> ->
            BrowserSignIn.pickAuthTabBrowser(default, installed, { it in system }) { it in has }
        }
        assertEquals("org.other.browser", pick("org.other.browser", listOf("com.android.chrome", "org.other.browser")))
        // Samsung Internet as the default, without Auth Tabs: Chrome, though listed after another
        assertEquals("com.android.chrome", pick("com.sec.android.app.sbrowser", listOf("org.other.browser", "com.android.chrome")))
        assertEquals("com.android.chrome", pick(null, listOf("com.android.chrome")))
        // never another app that says it has Auth Tabs (security review A-01)
        assertNull(pick("x", listOf("evil")))
        assertNull(pick("com.sec.android.app.sbrowser", listOf("com.sec.android.app.sbrowser", "org.other.browser")))
        assertNull(pick("com.sec.android.app.sbrowser", listOf("com.sec.android.app.sbrowser", "org.firefox")))
        assertNull(pick(null, emptyList()))
        // an app that took Chrome's package name without coming with the phone
        val sideloaded = BrowserSignIn.pickAuthTabBrowser("org.firefox", listOf("com.android.chrome"), { false }) { it == "com.android.chrome" }
        assertNull(sideloaded)
    }

    @Test fun onlyTheAppsSchemeWithAWellFormedCodeCountsFromAnAuthTab() {
        val code = "a".repeat(43)
        assertEquals(code, BrowserSignIn.codeOfSchemeReturn("mtgassistant-signin://signin?code=$code"))
        for (bad in listOf(
            null,
            "",
            "https://mtg.example.com/login/app-return?code=$code",
            "mtgassistant-signin://other?code=$code",
            "mtgassistant-signin://signin/x?code=$code",
            "mtgassistant-signin://u@signin?code=$code",
            "mtgassistant-signin://signin:1?code=$code",
            "mtgassistant-signin://signin?code=short",
            "mtgassistant-signin://signin?code=$code&code=$code",
            "mtgassistant-signin://signin?x=1&code=$code",
            "mtgassistant-signin://signin?code=$code#f",
            "evil://signin?code=$code",
        )) {
            assertNull(bad, BrowserSignIn.codeOfSchemeReturn(bad))
        }
    }

    @Test fun anAuthTabReturnsOnlyOnThePlainHttpsGateway() {
        assertEquals("mtg.example.com", BrowserSignIn.authTabHost("https://mtg.example.com"))
        for (bad in listOf("http://mtg.example.com", "https://mtg.example.com:8443", "https://u@mtg.example.com", "https://mtg.example.com/x", "not a url", "")) {
            assertNull(bad, BrowserSignIn.authTabHost(bad))
        }
    }

    @Test fun onlyTheGatewaysReturnAddressWithAWellFormedCodeCounts() {
        val g = "https://mtg.example.com"
        val code = "a".repeat(43)
        assertEquals(code, BrowserSignIn.codeOfReturn(g, "$g/login/app-return?code=$code"))
        for (bad in listOf(
            null,
            "https://evil.test/login/app-return?code=$code",
            "http://mtg.example.com/login/app-return?code=$code",
            "https://mtg.example.com:444/login/app-return?code=$code",
            "https://u@mtg.example.com/login/app-return?code=$code",
            "$g/login/app-returns?code=$code",
            "$g/login/app-return?code=short",
            "$g/login/app-return?code=$code&code=$code",
            "$g/login/app-return?x=1&code=$code",
            "$g/login/app-return",
            "mtgassistant-signin://signin?code=$code",
        )) {
            assertNull(bad, BrowserSignIn.codeOfReturn(g, bad))
        }
        assertNull(BrowserSignIn.codeOfReturn("https://mtg.example.com:8443", "https://mtg.example.com:8443/login/app-return?code=$code"))
    }

    @Test fun aRefusedSchemeLegCarriesNoCodeAndIsKnownAsRefused() {
        assertEquals("mtgassistant-signin://signin?error=sign_in_not_fresh", BrowserSignIn.SCHEME_REFUSED)
        assertTrue(BrowserSignIn.isSchemeRefusal(BrowserSignIn.SCHEME_REFUSED))
        assertNull(BrowserSignIn.codeOfSchemeReturn(BrowserSignIn.SCHEME_REFUSED))
        for (other in listOf(null, "mtgassistant-signin://signin?code=${"a".repeat(43)}", "evil://signin?error=sign_in_not_fresh")) {
            assertFalse(other.toString(), BrowserSignIn.isSchemeRefusal(other))
        }
    }

    @Test(expected = IllegalArgumentException::class) fun finishBodyRefusesOddValues() {
        BrowserSignIn.finishBody("a".repeat(43), "x&y=1")
    }

    /**
     * The Custom Tab names its browser: in the applinks build an unnamed one goes to this app (it
     * owns the gateway's https links). Never this app, whatever the phone lists.
     */
    @Test fun customTabNeverOpensThisApp() {
        val self = "local.mtgassistantgateway.app"
        val system = { p: String -> p in setOf("com.android.chrome", "com.sec.android.app.sbrowser") }
        // the default browser, when there is one
        assertEquals("org.mozilla.firefox",
            BrowserSignIn.customTabBrowser("org.mozilla.firefox", listOf("org.mozilla.firefox", "com.android.chrome", self), self, system))
        // never this app, even listed as the default
        assertEquals("com.android.chrome", BrowserSignIn.customTabBrowser(self, listOf(self, "com.android.chrome"), self, system))
        // no default (Android's own chooser answered): the one browser that came with the phone
        assertEquals("com.android.chrome",
            BrowserSignIn.customTabBrowser("android", listOf("com.android.chrome", "org.mozilla.firefox", self), self, system))
        // two built-in browsers and no default, or none at all: the person picks (null)
        assertNull(BrowserSignIn.customTabBrowser(null, listOf("com.android.chrome", "com.sec.android.app.sbrowser"), self, system))
        assertNull(BrowserSignIn.customTabBrowser(null, listOf(self, "org.example.sideloaded"), self, system))
        assertNull(BrowserSignIn.customTabBrowser(null, emptyList(), self, system))
    }
}
