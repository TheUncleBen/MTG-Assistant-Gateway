package local.mtgassistantgateway.app

import android.content.Intent
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.view.View
import android.view.inputmethod.EditorInfo
import android.widget.Button
import android.widget.EditText
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import org.json.JSONObject
import java.io.IOException
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.Executors

/**
 * First launch, and "Change gateway" later: ask for the gateway address and check it answers
 * on /healthz before saving. Anyone running their own gateway types their own address here,
 * so the app has no server baked in.
 */
class SetupActivity : ComponentActivity() {
    private val io = Executors.newSingleThreadExecutor()
    private val main = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(R.layout.activity_setup)
        Insets.padSystemBars(findViewById(R.id.root))
        val input = findViewById<EditText>(R.id.gateway_url)
        val status = findViewById<TextView>(R.id.setup_status)
        val button = findViewById<Button>(R.id.setup_continue)
        Prefs(this).gatewayOrigin?.let {
            input.setText(it)
            // "Change gateway" from a working app: Back returns to the page instead of leaving the
            // app (MainActivity finished itself to get here). A callback, because with
            // enableOnBackInvokedCallback the activity's onBackPressed() is never called.
            onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
                override fun handleOnBackPressed() {
                    startActivity(Intent(this@SetupActivity, MainActivity::class.java))
                    finish()
                }
            })
        }

        button.setOnClickListener {
            when (val r = GatewayUrl.normalize(input.text.toString())) {
                is GatewayUrl.Result.Bad -> showStatus(status, getString(reasonText(r.reason)), error = true)
                is GatewayUrl.Result.Ok -> {
                    button.isEnabled = false
                    showStatus(status, getString(R.string.setup_checking, r.origin), error = false)
                    io.execute {
                        val problem = checkHealth(r.origin)
                        main.post {
                            if (isDestroyed || isFinishing) return@post
                            button.isEnabled = true
                            if (problem == null) {
                                Prefs(this).gatewayOrigin = r.origin
                                startActivity(Intent(this, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP))
                                finish()
                            } else {
                                showStatus(status, problem, error = true)
                            }
                        }
                    }
                }
            }
        }
        wireGoKey(input, button)
    }

    /** The keyboard's Go key does what Continue does. */
    private fun wireGoKey(input: EditText, button: Button) {
        input.setOnEditorActionListener { _, actionId, _ ->
            if (actionId == EditorInfo.IME_ACTION_GO) {
                button.performClick()
                true
            } else false
        }
    }

    private fun reasonText(p: GatewayUrl.Problem): Int = when (p) {
        GatewayUrl.Problem.EMPTY -> R.string.setup_bad_empty
        GatewayUrl.Problem.INVALID -> R.string.setup_bad_invalid
        GatewayUrl.Problem.NOT_HTTPS -> R.string.setup_bad_http
        GatewayUrl.Problem.USER_INFO -> R.string.setup_bad_userinfo
    }

    override fun onDestroy() {
        io.shutdownNow()
        super.onDestroy()
    }

    private fun showStatus(view: TextView, text: String, error: Boolean) {
        view.text = text
        view.visibility = View.VISIBLE
        view.setTextColor(getColor(if (error) R.color.danger else R.color.text_muted))
    }

    /** Null when `GET origin/healthz` returns JSON with `"status"` "ok" or "degraded", else a message for the person. */
    private fun checkHealth(origin: String): String? {
        val conn = try {
            URL(GatewayUrl.join(origin, "/healthz")).openConnection() as HttpURLConnection
        } catch (e: IOException) {
            return getString(R.string.setup_unreachable)
        }
        return try {
            conn.connectTimeout = 8000
            conn.readTimeout = 8000
            conn.instanceFollowRedirects = false
            conn.setRequestProperty("Accept", "application/json")
            val code = conn.responseCode
            if (code != 200) return getString(R.string.setup_not_gateway, code)
            val body = conn.inputStream.bufferedReader().use { it.readText() }
            val ok = try {
                // "degraded" is still a gateway: only its research service is down
                JSONObject(body).optString("status") in setOf("ok", "degraded")
            } catch (e: Exception) {
                false
            }
            if (ok) null else getString(R.string.setup_not_gateway, code)
        } catch (e: IOException) {
            getString(R.string.setup_unreachable)
        } finally {
            conn.disconnect()
        }
    }
}
