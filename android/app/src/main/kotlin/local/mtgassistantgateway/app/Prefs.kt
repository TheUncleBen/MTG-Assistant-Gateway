package local.mtgassistantgateway.app

import android.content.Context
import android.content.SharedPreferences

/** What the app keeps: which gateway to open, and the sign-in origin that gateway advertised. */
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

    companion object {
        private const val KEY_ORIGIN = "gateway_origin"
        private const val KEY_PROVIDER = "idp_origin"
        private const val KEY_PROVIDER_FOR = "idp_origin_gateway"
    }
}
