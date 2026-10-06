package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class NavPolicyTest {
    private val gw = "https://mtg.example.com"
    private val idp = "https://auth.example.com"

    /** A main-frame navigation; [redirect] for a server redirect, [gesture] for a tap. */
    private fun NavPolicy.main(url: String, redirect: Boolean = false, gesture: Boolean = false) =
        decide(url, mainFrame = true, redirect = redirect, gesture = gesture)

    /** The gateway's /login redirecting to the provider, which then shows its login form. */
    private fun signedInto(p: NavPolicy) {
        assertEquals(Nav.STAY, p.main("$gw/login?next=/scan", gesture = true))
        assertEquals(Nav.STAY, p.main("$idp/application/o/authorize/?client_id=x", redirect = true))
        p.pageStarted("$idp/if/flow/login/")
    }

    @Test fun gatewayPagesStay() {
        val p = NavPolicy(gw)
        assertEquals(Nav.STAY, p.main("$gw/scan", gesture = true))
        assertEquals(Nav.STAY, p.main("$gw/decks", redirect = true))
        assertFalse(p.signingIn)
    }

    @Test fun otherSitesGoToTheBrowser() {
        val p = NavPolicy(gw)
        assertEquals(Nav.BROWSER, p.main("https://scryfall.com/card/x", gesture = true))
        assertEquals(Nav.BROWSER, p.main("https://scryfall.com/card/x", redirect = true))
    }

    @Test fun oneProviderIsLearnedFromTheLoginRedirect() {
        val p = NavPolicy(gw)
        signedInto(p)
        assertTrue(p.signingIn)
        assertEquals(idp, p.signInOrigin)
        // The provider's own pages and its redirects stay; the way back to the gateway stays.
        assertEquals(Nav.STAY, p.main("$idp/if/flow/mfa/", gesture = true))
        assertEquals(Nav.STAY, p.main("$idp/application/o/authorize/?x", redirect = true))
        assertEquals(Nav.STAY, p.main("$gw/auth/callback?code=c", redirect = true))
    }

    @Test fun furtherOriginsDuringSignInGoToTheBrowser() {
        val p = NavPolicy(gw)
        signedInto(p)
        // A federated source or an open redirect at the provider: not a second in-app origin.
        assertEquals(Nav.BROWSER, p.main("https://discord.com/oauth2/authorize?x", redirect = true))
        assertEquals(Nav.BROWSER, p.main("https://evil.example/phish", redirect = true))
        assertEquals(Nav.BROWSER, p.main("https://evil.example/phish", gesture = true))
        assertEquals(idp, p.signInOrigin)
    }

    @Test fun onlyARedirectRightAfterLoginTeachesAnOrigin() {
        val p = NavPolicy(gw)
        assertEquals(Nav.STAY, p.main("$gw/login"))
        // A tap on /login's page (not a redirect) does not count.
        assertEquals(Nav.BROWSER, p.main("$idp/x", gesture = true))
        assertNull(p.signInOrigin)
        // Nor does a redirect after some other gateway page, even while signing in.
        assertEquals(Nav.STAY, p.main("$gw/account", redirect = true))
        assertEquals(Nav.BROWSER, p.main("$idp/x", redirect = true))
        assertNull(p.signInOrigin)
        // Nor does a redirect after /authorize: only /login names the provider.
        assertEquals(Nav.STAY, p.main("$gw/authorize?client_id=a", redirect = true))
        assertEquals(Nav.BROWSER, p.main("$idp/x", redirect = true))
        assertNull(p.signInOrigin)
        assertEquals(Nav.STAY, p.main("$gw/login", redirect = true))
        assertEquals(Nav.STAY, p.main("$idp/x", redirect = true))
        assertEquals(idp, p.signInOrigin)
    }

    /** An application's /authorize link opened in the app, up to its consent page. */
    private fun atConsent(p: NavPolicy) {
        val authorize = "$gw/authorize?client_id=c&redirect_uri=https://evil.example/cb"
        assertEquals(Nav.STAY, p.main(authorize, gesture = true))
        assertTrue(p.pageStarted(authorize))
        assertEquals(Nav.STAY, p.main("$gw/authorize/confirm?state=s", redirect = true))
        assertTrue(p.pageStarted("$gw/authorize/confirm?state=s"))
    }

    @Test fun denyOnTheConsentPageGoesToTheBrowser() {
        // Deny answers with a redirect to the application's own site: never the provider.
        val p = NavPolicy(gw)
        atConsent(p)
        assertEquals(Nav.BROWSER, p.main("https://evil.example/cb?error=access_denied", redirect = true))
        assertNull(p.signInOrigin)
        assertEquals(Nav.BROWSER, p.main("https://evil.example/fake-login", gesture = true))
        assertEquals(Nav.BROWSER, p.main("https://evil.example/fake-login", redirect = true))
    }

    @Test fun denyGoesToTheBrowserAfterAnEarlierSignIn() {
        val p = NavPolicy(gw)
        signedInto(p) // still signing in: no gateway page outside the sign-in has loaded
        atConsent(p)
        assertEquals(Nav.BROWSER, p.main("https://evil.example/cb?error=access_denied", redirect = true))
        assertEquals(idp, p.signInOrigin)
        p.pageFinished("$gw/scan")
        atConsent(p)
        assertEquals(Nav.BROWSER, p.main("https://evil.example/cb?error=access_denied", redirect = true))
        assertNull(p.signInOrigin)
    }

    @Test fun denyLoadedWithoutAskingIsStopped() {
        // WebView may not ask shouldOverrideUrlLoading about the redirect after the form post:
        // the page is refused when it starts, so the app sends it to the browser.
        val p = NavPolicy(gw)
        atConsent(p)
        assertFalse(p.pageStarted("https://evil.example/cb?error=access_denied"))
        assertFalse(p.pageStarted("https://evil.example/fake-login"))
        assertNull(p.signInOrigin)
        signedInto(p)
        p.pageFinished("$gw/scan")
        atConsent(p)
        assertFalse(p.pageStarted("https://evil.example/cb?error=access_denied"))
        assertNull(p.signInOrigin)
    }

    @Test fun approveGoesOnToTheProviderNamedByLogin() {
        val p = NavPolicy(gw)
        signedInto(p)
        p.pageFinished("$gw/scan")
        assertEquals(idp, p.knownProvider)
        atConsent(p)
        assertEquals(Nav.STAY, p.main("$idp/application/o/authorize/?x", redirect = true))
        assertTrue(p.signingIn)
        assertEquals(idp, p.signInOrigin)
        assertTrue(p.pageStarted("$idp/if/flow/login/"))
        assertEquals(Nav.STAY, p.main("$gw/auth/callback?code=c", redirect = true))
        // The same, when WebView did not ask about the redirect.
        p.pageFinished("$gw/scan")
        atConsent(p)
        assertTrue(p.pageStarted("$idp/application/o/authorize/?x"))
        assertEquals(idp, p.signInOrigin)
    }

    @Test fun approveWithoutAKnownProviderGoesToTheBrowser() {
        val p = NavPolicy(gw)
        atConsent(p)
        assertEquals(Nav.BROWSER, p.main("$idp/application/o/authorize/?x", redirect = true))
        assertNull(p.signInOrigin)
        atConsent(p)
        assertFalse(p.pageStarted("$idp/application/o/authorize/?x"))
    }

    @Test fun legitimatePagesPassTheStartCheck() {
        val p = NavPolicy(gw)
        assertTrue(p.pageStarted("$gw/login?next=/"))
        assertEquals(Nav.STAY, p.main("$idp/application/o/authorize/?x", redirect = true))
        assertTrue(p.pageStarted("$idp/application/o/authorize/?x"))
        assertTrue(p.pageStarted("$idp/if/flow/login/"))
        assertTrue(p.pageStarted("$gw/auth/callback?code=c"))
        assertTrue(p.pageStarted("about:blank"))
        p.pageFinished("$gw/scan")
        // The provider's page is never handed off by the backstop (Back to it after a sign-in), any
        // other site is.
        assertTrue(p.pageStarted("$idp/if/flow/login/"))
        assertFalse(p.pageStarted("https://scryfall.com/card/x"))
    }

    @Test fun anAdvertisedProviderLetsApproveStayWithoutALogin() {
        // Cold start with a valid session, or an App Link to /authorize: no /login in this run.
        val p = NavPolicy(gw, idp)
        atConsent(p)
        assertEquals(Nav.STAY, p.main("$idp/application/o/authorize/?x", redirect = true))
        assertTrue(p.signingIn)
        assertEquals(idp, p.signInOrigin)
        assertEquals(Nav.STAY, p.main("$idp/if/flow/mfa/", gesture = true))
        assertEquals(Nav.STAY, p.main("$gw/auth/callback?code=c", redirect = true))
        // The same when WebView did not ask about the redirect after the form.
        val q = NavPolicy(gw, idp)
        atConsent(q)
        assertTrue(q.pageStarted("$idp/application/o/authorize/?x"))
        assertEquals(idp, q.signInOrigin)
    }

    @Test fun anAdvertisedProviderStillSendsDenyToTheBrowser() {
        val p = NavPolicy(gw, idp)
        atConsent(p)
        assertEquals(Nav.BROWSER, p.main("https://evil.example/cb?error=access_denied", redirect = true))
        assertNull(p.signInOrigin)
        atConsent(p)
        assertFalse(p.pageStarted("https://evil.example/cb?error=access_denied"))
        assertNull(p.signInOrigin)
        assertEquals(idp, p.knownProvider)
    }

    @Test fun anAdvertisedProviderIsNotReplacedByALoginRedirect() {
        val p = NavPolicy(gw, idp)
        assertEquals(Nav.STAY, p.main("$gw/login", redirect = true))
        assertEquals(Nav.BROWSER, p.main("https://other.example/authorize", redirect = true))
        assertEquals(idp, p.knownProvider)
        assertEquals(Nav.STAY, p.main("$gw/login", redirect = true))
        assertEquals(Nav.STAY, p.main("$idp/application/o/authorize/?x", redirect = true))
        assertEquals(idp, p.signInOrigin)
    }

    @Test fun aProviderPinnedLaterCounts() {
        val p = NavPolicy(gw)
        p.pin(idp)
        atConsent(p)
        assertEquals(Nav.STAY, p.main("$idp/application/o/authorize/?x", redirect = true))
    }

    @Test fun anUnaskedRedirectToTheAdvertisedProviderStays() {
        // A WebView that does not report the /login redirect: the provider's page still stays, and
        // its own steps after it.
        val p = NavPolicy(gw, idp)
        assertTrue(p.pageStarted("$idp/if/flow/login/"))
        assertEquals(Nav.STAY, p.main("$idp/if/flow/mfa/", redirect = true))
    }

    @Test fun historyIsClearedOnceASignInEndsOrAPageWasRefused() {
        val p = NavPolicy(gw, idp)
        signedInto(p)
        assertFalse(p.pageFinished("$idp/if/flow/login/"))
        assertFalse(p.pageFinished("$gw/auth/callback?code=c"))
        assertTrue(p.pageFinished("$gw/")) // Back must not reach the provider's login page
        assertFalse(p.pageFinished("$gw/scan"))
        atConsent(p)
        assertFalse(p.pageStarted("https://evil.example/cb?error=access_denied"))
        assertTrue(p.pageFinished("$gw/")) // nor the refused page
        assertFalse(p.pageFinished("$gw/decks"))
    }

    @Test fun theBackstopHandsOffAtMostOnceInAWhile() {
        val p = NavPolicy(gw)
        assertTrue(p.handOff(1_000))
        assertFalse(p.handOff(1_000 + NavPolicy.LOOP_GUARD_MS - 1))
        assertTrue(p.handOff(1_000 + NavPolicy.LOOP_GUARD_MS))
    }

    @Test fun loginLoadedDirectlyCountsToo() {
        val p = NavPolicy(gw)
        p.pageStarted("$gw/login?next=/") // loadUrl() does not go through shouldOverrideUrlLoading
        assertEquals(Nav.STAY, p.main("$idp/x", redirect = true))
        assertEquals(idp, p.signInOrigin)
    }

    @Test fun providerMustBeHttps() {
        val p = NavPolicy(gw)
        assertEquals(Nav.STAY, p.main("$gw/login"))
        assertEquals(Nav.BROWSER, p.main("http://auth.example.com/x", redirect = true))
        assertNull(p.signInOrigin)
    }

    @Test fun aGatewayPageEndsTheSignIn() {
        val p = NavPolicy(gw)
        signedInto(p)
        p.pageFinished("$gw/auth/callback?code=c") // still part of the sign-in
        assertEquals(idp, p.signInOrigin)
        p.pageFinished("$gw/scan")
        assertFalse(p.signingIn)
        assertNull(p.signInOrigin)
        assertEquals(Nav.BROWSER, p.main("$idp/if/flow/login/", gesture = true))
        // The next sign-in learns afresh.
        signedInto(p)
        assertEquals(idp, p.signInOrigin)
    }

    @Test fun otherSchemesNeedATapInTheMainFrame() {
        val p = NavPolicy(gw)
        assertEquals(Nav.BROWSER, p.main("mailto:someone@example.com", gesture = true))
        assertEquals(Nav.DROP, p.main("market://details?id=x"))
        assertEquals(Nav.DROP, p.main("someapp://do", redirect = true))
        assertEquals(Nav.DROP, p.decide("someapp://do", mainFrame = false, redirect = false, gesture = true))
        assertEquals(Nav.DROP, p.main("intent:#Intent;component=a/b;end", redirect = true))
        assertEquals(Nav.DROP, p.main("not a url", gesture = true))
    }

    @Test fun framesLoadInPlace() {
        val p = NavPolicy(gw)
        assertEquals(Nav.STAY, p.decide("https://captcha.example/frame", mainFrame = false, redirect = false, gesture = false))
        assertNull(p.signInOrigin)
    }
}
