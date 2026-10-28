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
 *    else Chrome, else any other that has one), the app opened the sign-in in one on its own
 *    scheme ([SCHEME_RETURN]): the gateway ends the browser's part with
 *    `mtgassistant-signin://signin?code=...`, which the Auth Tab hands back to this app and
 *    closes, with no tap and no Digital Asset Links check ([codeOfSchemeReturn]). Otherwise, in a
 *    Custom Tab, the gateway sends the browser to [RETURN_PATH]`?code=...` on its own address,
 *    whose page opens `mtgassistant-signin://signin?code=...` through an `intent:` link naming
 *    this app's package, once by itself where the browser allows and else on a tap.
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
    /** Asks the gateway to end the browser's part on [SCHEME] instead (app_signin.SCHEME_RETURN). */
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
