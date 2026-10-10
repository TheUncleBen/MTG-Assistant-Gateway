package local.mtgassistantgateway.app

import android.content.Context
import android.content.SharedPreferences

/**
 * What the app keeps: which gateway to open, the sign-in origin that gateway advertised, the
 * verifier of a browser sign-in in progress ([BrowserSignIn]), and when it last looked for an app
 * update ([Updater]).
 */
class Prefs(context: Context) {
    private val p: SharedPreferences = context.getSharedPreferences("mtgassistant", Context.MODE_PRIVATE)

    /** Gateway origin (`https://host[:port]`), or null until first-launch setup is done. */
    var gatewayOrigin: String?
        get() = p.getString(KEY_ORIGIN, null)
        set(value) {
            p.edit().apply { if (value == null) remove(KEY_ORIGIN) else putString(KEY_ORIGIN, value) }.apply()
        }

    /** The sign-in origin [gateway] advertised (`/.well-known/mtg-gateway`), or null; never another gateway's. */
    fun providerFor(gateway: String): String? =
        if (p.getString(KEY_PROVIDER_FOR, null) == gateway) p.getString(KEY_PROVIDER, null) else null

    fun setProvider(gateway: String, provider: String) {
        p.edit().putString(KEY_PROVIDER_FOR, gateway).putString(KEY_PROVIDER, provider).apply()
    }

    /** Remembers the verifier of the browser sign-in just started for [gateway] (one at a time). */
    fun setPendingSignIn(gateway: String, verifier: String, now: Long) {
        p.edit().putString(KEY_SIGNIN_FOR, gateway).putString(KEY_SIGNIN_VERIFIER, verifier).putLong(KEY_SIGNIN_AT, now).apply()
    }

    /**
     * The verifier of the browser sign-in started for [gateway] within [BrowserSignIn.MAX_AGE_MS],
     * or null. Read once: it is removed either way, so a code can be finished only once.
     */
    fun takePendingSignIn(gateway: String, now: Long): String? {
        val forGateway = p.getString(KEY_SIGNIN_FOR, null)
        val verifier = p.getString(KEY_SIGNIN_VERIFIER, null)
        val at = p.getLong(KEY_SIGNIN_AT, 0L)
        p.edit().remove(KEY_SIGNIN_FOR).remove(KEY_SIGNIN_VERIFIER).remove(KEY_SIGNIN_AT).apply()
        return if (forGateway == gateway && verifier != null && now >= at && now - at <= BrowserSignIn.MAX_AGE_MS) verifier else null
    }

    /** When the last update check finished (epoch ms), 0 for never. */
    var lastUpdateCheck: Long
        get() = p.getLong(KEY_UPDATE_CHECK, 0L)
        set(value) {
            p.edit().putLong(KEY_UPDATE_CHECK, value).apply()
        }

    companion object {
        private const val KEY_ORIGIN = "gateway_origin"
        private const val KEY_PROVIDER = "idp_origin"
        private const val KEY_PROVIDER_FOR = "idp_origin_gateway"
        private const val KEY_SIGNIN_FOR = "signin_gateway"
        private const val KEY_SIGNIN_VERIFIER = "signin_verifier"
        private const val KEY_SIGNIN_AT = "signin_started_at"
        private const val KEY_UPDATE_CHECK = "update_checked_at"
    }
}
