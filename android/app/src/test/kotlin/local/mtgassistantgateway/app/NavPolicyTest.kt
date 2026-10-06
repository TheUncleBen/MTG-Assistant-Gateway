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
        // /authorize starts a sign-in as /login does.
        assertEquals(Nav.STAY, p.main("$gw/authorize?client_id=a", redirect = true))
        assertEquals(Nav.STAY, p.main("$idp/x", redirect = true))
        assertEquals(idp, p.signInOrigin)
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
