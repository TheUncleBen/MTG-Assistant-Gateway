package local.mtgassistantgateway.app

import java.net.URI
import java.net.URISyntaxException
import java.net.URLEncoder
import java.security.MessageDigest
import java.security.SecureRandom
import java.util.Base64

/**
 * Signing in through the phone's browser, where passkeys and password managers work (a WebView
 * offers neither on the identity provider's page). The gateway's side is in app_signin.py:
 *
 * 1. The gateway's /login page inside the app calls `MtgNative.signInWithBrowser(next, fresh)`.
 * 2. The app keeps a random verifier and opens [startUrl] (with the verifier's SHA-256) in a
 *    Custom Tab; the person signs in at the identity provider there.
 * 3. Where a browser on the phone has an Auth Tab ([pickAuthTabBrowser]: the default browser,
 *    else Chrome, else any other that has one), the app opens the sign-in in one that returns on
 *    the gateway's own https address ([authTabHost], [RETURN_PATH]): the browser checks the
 *    gateway's Digital Asset Links (assetlinks.json names this app), hands the address back and
 *    closes, with no tap ([codeOfReturn]). Only when the browser reports that check failed or
 *    timed out, or the gateway's address can't take an https return, does the app try again in
 *    an Auth Tab on its own scheme ([SCHEME_RETURN], [codeOfSchemeReturn]); the gateway then asks
 *    the identity provider for a new sign-in and gives a code only when the ID token shows one,
 *    else it returns [isSchemeRefusal]'s address and the app finishes in a Custom Tab. In a
 *    Custom Tab the gateway's page opens `mtgassistant-signin://signin?code=...` through an
 *    `intent:` link naming this app's package, once by itself where the browser allows and else
 *    on a tap.
 * 4. The app posts the code with its verifier to [FINISH_PATH] in the WebView, which gets the
 *    session cookie. A code is worthless without the verifier, which never leaves this app.
 *
 * Plain Kotlin, so the unit tests run on the JVM.
 */
object BrowserSignIn {
    const val SCHEME = "mtgassistant-signin"
    const val HOST = "signin"
    const val FINISH_PATH = "/login/app"
    /** Where the gateway hands the code back to a Custom Tab (app_signin.RETURN_PATH). */
    const val RETURN_PATH = "/login/app-return"
    /** The retry: asks the gateway to end the browser's part on [SCHEME] (app_signin.SCHEME_RETURN). */
    const val SCHEME_RETURN = "scheme"
    /** Preferred when the default browser has no Auth Tab. */
    const val CHROME = "com.android.chrome"
    /** How long a started sign-in waits for the browser to come back. */
    const val MAX_AGE_MS = 10 * 60 * 1000L
    private val CODE = Regex("[A-Za-z0-9_-]{43}")
    private val VERIFIER = Regex("[A-Za-z0-9_-]{43,128}")

    private fun b64url(bytes: ByteArray): String = Base64.getUrlEncoder().withoutPadding().encodeToString(bytes)

    /** 32 random bytes as URL-safe base64 (43 characters). */
    fun newVerifier(random: SecureRandom = SecureRandom()): String = b64url(ByteArray(32).also(random::nextBytes))

    fun challengeOf(verifier: String): String =
        b64url(MessageDigest.getInstance("SHA-256").digest(verifier.toByteArray(Charsets.US_ASCII)))

    /** A gateway path to land on after signing in; anything else becomes "/". */
    fun safeNext(next: String?): String =
        if (next != null && next.startsWith("/") && !next.startsWith("//") && '\\' !in next && next.length <= 200) next else "/"

    /** The gateway's /login for the browser leg; [schemeReturn] for an Auth Tab on [SCHEME]. */
    fun startUrl(origin: String, next: String?, challenge: String, fresh: Boolean, schemeReturn: Boolean = false): String =
        origin + "/login?next=" + URLEncoder.encode(safeNext(next), "UTF-8") +
            "&app_challenge=" + challenge + (if (fresh) "&fresh=1" else "") +
            (if (schemeReturn) "&app_return=$SCHEME_RETURN" else "")

    /**
     * The browser to open an Auth Tab in: the default one when it has Auth Tabs, else Chrome when
     * it has them, else the first of [installed] that has them; null when none has (a Custom Tab
     * then). [installed] lists the browsers that offer Custom Tabs, as the phone lists them.
     */
    fun pickAuthTabBrowser(default: String?, installed: List<String>, hasAuthTab: (String) -> Boolean): String? {
        if (default != null && hasAuthTab(default)) return default
        val others = installed.filter { it != default }.distinct()
        return others.firstOrNull { it == CHROME && hasAuthTab(it) } ?: others.firstOrNull { it != CHROME && hasAuthTab(it) }
    }

    /**
     * The host an Auth Tab returns on: the gateway's own, when [origin] is plain `https://host`.
     * Null (the Auth Tab on [SCHEME] instead) for anything else, a port included: an Auth Tab
     * matches a host and a path only.
     */
    fun authTabHost(origin: String): String? {
        val uri = try { URI(origin) } catch (e: URISyntaxException) { return null }
        val host = uri.host ?: return null
        return if (uri.scheme == "https" && uri.port == -1 && (uri.rawPath ?: "").isEmpty() && uri.rawQuery == null &&
            uri.rawUserInfo == null) host else null
    }

    /** The one-time code from the address an Auth Tab returned, or null when it isn't [RETURN_PATH] on [origin]. */
    fun codeOfReturn(origin: String, returned: String?): String? {
        val host = authTabHost(origin) ?: return null
        val uri = try { URI(returned ?: return null) } catch (e: URISyntaxException) { return null }
        if (uri.scheme != "https" || uri.host != host || uri.port != -1 || uri.rawUserInfo != null || uri.rawPath != RETURN_PATH) {
            return null
        }
        val params = (uri.rawQuery ?: "").split('&').filter { it.isNotEmpty() }
        if (params.size != 1 || !params[0].startsWith("code=")) return null
        val code = params[0].removePrefix("code=")
        return if (CODE.matches(code)) code else null
    }

    /** The address the gateway ends a scheme leg on when it saw no new sign-in (app_signin.SCHEME_REFUSED). */
    const val SCHEME_REFUSED = "$SCHEME://$HOST?error=sign_in_not_fresh"

    /** Whether an Auth Tab on [SCHEME] came back refused: no code, so finish in a Custom Tab. */
    fun isSchemeRefusal(returned: String?): Boolean = returned == SCHEME_REFUSED

    /** The one-time code from the address an Auth Tab on [SCHEME] returned, or null when it isn't ours. */
    fun codeOfSchemeReturn(returned: String?): String? {
        val uri = try { URI(returned ?: return null) } catch (e: URISyntaxException) { return null }
        if (uri.scheme != SCHEME || uri.rawAuthority != HOST || !(uri.rawPath ?: "").isEmpty() || uri.rawFragment != null) {
            return null
        }
        val params = (uri.rawQuery ?: "").split('&').filter { it.isNotEmpty() }
        if (params.size != 1 || !params[0].startsWith("code=")) return null
        val code = params[0].removePrefix("code=")
        return if (CODE.matches(code)) code else null
    }

    /** The one-time code from the link the browser opened, or null when it isn't ours or isn't well formed. */
    fun codeOf(scheme: String?, host: String?, code: String?): String? =
        if (scheme == SCHEME && host == HOST && code != null && CODE.matches(code)) code else null

    /** The form the WebView posts to [FINISH_PATH]; both values are URL-safe base64, so nothing needs escaping. */
    fun finishBody(code: String, verifier: String): ByteArray {
        require(CODE.matches(code) && VERIFIER.matches(verifier))
        return "code=$code&verifier=$verifier".toByteArray(Charsets.US_ASCII)
    }
}
