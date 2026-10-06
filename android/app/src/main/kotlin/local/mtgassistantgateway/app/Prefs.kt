package local.mtgassistantgateway.app

import android.content.Context
import android.content.SharedPreferences

/** The one setting the app keeps: which gateway to open. */
class Prefs(context: Context) {
    private val p: SharedPreferences = context.getSharedPreferences("mtgassistant", Context.MODE_PRIVATE)

    /** Gateway origin (`https://host[:port]`), or null until first-launch setup is done. */
    var gatewayOrigin: String?
        get() = p.getString(KEY_ORIGIN, null)
        set(value) {
            p.edit().apply { if (value == null) remove(KEY_ORIGIN) else putString(KEY_ORIGIN, value) }.apply()
        }

    companion object {
        private const val KEY_ORIGIN = "gateway_origin"
    }
}
