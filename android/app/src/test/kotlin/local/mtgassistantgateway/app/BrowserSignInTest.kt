package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
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

    @Test(expected = IllegalArgumentException::class) fun finishBodyRefusesOddValues() {
        BrowserSignIn.finishBody("a".repeat(43), "x&y=1")
    }
}
