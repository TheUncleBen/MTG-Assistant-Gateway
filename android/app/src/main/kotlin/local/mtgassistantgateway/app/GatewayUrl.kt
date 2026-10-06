package local.mtgassistantgateway.app

import java.net.URI
import java.net.URISyntaxException

/**
 * The gateway address the person typed at first launch, normalised to an origin.
 *
 * Only `https` is accepted: the gateway's sign-in is OAuth over the web and its
 * session cookie is marked Secure, so a plain-http gateway could never sign anyone
 * in anyway. The manifest also turns cleartext traffic off.
 *
 * Pure functions, no Android types, so the unit tests run on a plain JVM.
 */
object GatewayUrl {
    /** Result of [normalize]: an origin like `https://mtg.example.com` or a reason. */
    sealed class Result {
        data class Ok(val origin: String) : Result()
        data class Bad(val reason: String) : Result()
    }

    /**
     * Turns what the person typed into `https://host[:port]`.
     *
     * Accepts a bare host (`mtg.example.com`), a URL with a path (`https://mtg.example.com/scan`)
     * and surrounding whitespace. Rejects `http`, userinfo, non-ASCII hosts and anything
     * that does not parse.
     */
    fun normalize(typed: String): Result {
        var s = typed.trim()
        if (s.isEmpty()) return Result.Bad("Enter the gateway address.")
        if (!s.contains("://")) s = "https://$s"
        val uri = try {
            URI(s)
        } catch (e: URISyntaxException) {
            return Result.Bad("That is not a valid address.")
        }
        val scheme = uri.scheme?.lowercase() ?: return Result.Bad("That is not a valid address.")
        if (scheme != "https") return Result.Bad("The gateway must be reached over https.")
        if (uri.rawUserInfo != null) return Result.Bad("Leave the user name and password out of the address.")
        val host = uri.host ?: return Result.Bad("That is not a valid address.")
        if (host.isEmpty() || !host.all { it.isLetterOrDigit() || it == '.' || it == '-' || it == '[' || it == ']' || it == ':' }) {
            return Result.Bad("That is not a valid address.")
        }
        val port = if (uri.port == -1 || uri.port == 443) "" else ":${uri.port}"
        return Result.Ok("https://${host.lowercase()}$port")
    }

    /** `https://host[:port]` of any absolute URL, or null when it has no host. */
    fun originOf(url: String): String? {
        val uri = try {
            URI(url)
        } catch (e: URISyntaxException) {
            return null
        }
        val scheme = uri.scheme?.lowercase() ?: return null
        val host = uri.host?.lowercase() ?: return null
        val default = when (scheme) {
            "https" -> 443
            "http" -> 80
            else -> -1
        }
        val port = if (uri.port == -1 || uri.port == default) "" else ":${uri.port}"
        return "$scheme://$host$port"
    }

    /** True when [url] is on the gateway's origin (scheme, host and port all equal). */
    fun isGateway(origin: String, url: String?): Boolean =
        url != null && originOf(url) == origin

    /** The path of [url] starts with [path] (`/scan` matches `/scan` and `/scan/x`, not `/scanner`). */
    fun pathStartsWith(url: String?, path: String): Boolean {
        if (url == null) return false
        val p = try {
            URI(url).path ?: return false
        } catch (e: URISyntaxException) {
            return false
        }
        return p == path || p.startsWith("$path/")
    }

    /** Join an origin and an absolute path. */
    fun join(origin: String, path: String): String = origin.trimEnd('/') + (if (path.startsWith("/")) path else "/$path")

    /**
     * The gateway page whose redirect goes straight to the identity provider: /login. Only its
     * redirect may teach the app the provider's origin. /authorize and its consent page are not
     * one: the consent form's Deny redirects to whatever site the connecting application
     * registered, so a redirect after it says nothing about who the provider is.
     */
    fun isSignInStart(origin: String, url: String?): Boolean =
        isGateway(origin, url) && pathStartsWith(url, "/login")

    /** The gateway's consent page for an application (`/authorize/confirm`): Approve goes to the provider, Deny to the application. */
    fun isConsentPage(origin: String, url: String?): Boolean =
        isGateway(origin, url) && pathStartsWith(url, "/authorize/confirm")

    /** A gateway page that is part of a sign-in (its start, /authorize and the consent page, or the provider's callback). */
    fun isSignInPage(origin: String, url: String?): Boolean =
        isGateway(origin, url) &&
            (pathStartsWith(url, "/login") || pathStartsWith(url, "/authorize") || pathStartsWith(url, "/auth"))
}

/**
 * Where a navigation in the app's WebView goes: [STAY] in the app, out to the phone's [BROWSER]
 * (a VIEW intent limited to browsable activities), or [DROP]ped.
 */
enum class Nav { STAY, BROWSER, DROP }

/**
 * The app's navigation policy, kept free of Android types so the unit tests can drive it.
 *
 * Gateway pages stay in the app. So does one identity-provider origin per sign-in: it is learned
 * only from an https server redirect that directly follows a gateway /login load (the one gateway
 * page that redirects nowhere but to the provider), and it is forgotten once a gateway page
 * outside the sign-in has loaded. The consent page for a connecting application redirects either
 * to the provider (Approve) or to the application's own site (Deny), and the app cannot tell the
 * two apart, so a redirect after it stays in the app only when it goes to the provider an earlier
 * /login in this app session already named ([knownProvider]); anything else, the Deny redirect
 * included, opens in the browser. Every other page opens in the browser too, including any further
 * origin the provider redirects to, so a page from elsewhere never fills the app's frame without
 * an address bar. Other schemes (deep links into other apps) leave the app only on a tap in the
 * main frame; anything else is dropped.
 */
class NavPolicy(private val origin: String) {
    /** The identity provider allowed in the app during the current sign-in, if one was learned. */
    var signInOrigin: String? = null
        private set
    /** The provider a gateway /login redirected to earlier in this app session; kept after the sign-in ends. */
    var knownProvider: String? = null
        private set
    /** A sign-in is in progress: a gateway /login page was seen and none other since. */
    var signingIn = false
        private set
    /** The last main-frame load was a gateway sign-in start, so the next redirect may name the provider. */
    private var afterSignInStart = false
    /** The last main-frame load was the consent page, so the next redirect may go to [knownProvider]. */
    private var afterConsent = false

    /** Decides a navigation the WebView asks about (`shouldOverrideUrlLoading`). */
    fun decide(url: String, mainFrame: Boolean, redirect: Boolean, gesture: Boolean): Nav {
        val scheme = try {
            URI(url).scheme?.lowercase()
        } catch (e: URISyntaxException) {
            null
        } ?: return Nav.DROP
        if (scheme != "https" && scheme != "http") return if (mainFrame && gesture) Nav.BROWSER else Nav.DROP
        // A frame inside a page loads in place: it can never take over the screen, and the
        // gateway's own pages allow no frames at all (CSP default-src 'none').
        if (!mainFrame) return Nav.STAY
        val target = GatewayUrl.originOf(url) ?: return Nav.DROP
        if (target == origin) {
            noteGatewayPage(url)
            return Nav.STAY
        }
        return if (admit(target, scheme, redirect)) Nav.STAY else Nav.BROWSER
    }

    /** Whether a main-frame page on another origin may load in the app; learns the provider as it goes. */
    private fun admit(target: String, scheme: String, redirect: Boolean): Boolean {
        val follows = afterSignInStart
        val consent = afterConsent
        afterSignInStart = false
        afterConsent = false
        if (redirect && follows && signInOrigin == null && scheme == "https") {
            // The gateway's /login sending the browser to its identity provider.
            signInOrigin = target
            knownProvider = target
            return true
        }
        if (redirect && consent && scheme == "https" && target == knownProvider) {
            // Approve on the consent page, going on to the provider /login named earlier. A Deny
            // goes to the application's own site, never this origin, so it opens in the browser.
            signInOrigin = target
            signingIn = true
            return true
        }
        return signingIn && target == signInOrigin // the provider's own pages and redirects
    }

    /**
     * A main-frame page started loading (`onPageStarted`). Returns false when the page must not
     * stay in the app: the caller stops it and opens it in the browser instead. This is the
     * backstop for loads WebView never asked [decide] about: it does not ask about POST requests,
     * so possibly not about the redirect after the consent form either. Such a page arrives
     * straight from a gateway page, so it is judged as a redirect would be.
     */
    fun pageStarted(url: String): Boolean {
        val scheme = try {
            URI(url).scheme?.lowercase()
        } catch (e: URISyntaxException) {
            null
        }
        if (scheme == "https" || scheme == "http") {
            val target = GatewayUrl.originOf(url)
            if (target != origin) {
                if (target != null && admit(target, scheme, redirect = true)) return true
                afterSignInStart = false
                afterConsent = false
                return false
            }
        }
        noteGatewayPage(url)
        return true
    }

    private fun noteGatewayPage(url: String) {
        afterSignInStart = GatewayUrl.isSignInStart(origin, url)
        afterConsent = GatewayUrl.isConsentPage(origin, url)
        if (afterSignInStart) signingIn = true
    }

    /** A main-frame page finished loading (`onPageFinished`): a gateway page outside the sign-in ends it. */
    fun pageFinished(url: String) {
        if (GatewayUrl.isGateway(origin, url) && !GatewayUrl.isSignInPage(origin, url)) {
            signingIn = false
            signInOrigin = null
            afterSignInStart = false
            afterConsent = false
        }
    }
}
