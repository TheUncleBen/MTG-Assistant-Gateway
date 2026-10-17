package local.mtgassistantgateway.app

import org.json.JSONException
import org.json.JSONObject

/**
 * The pure parts of the app's notifications ([NotifyService]): the member's key, the gateway's
 * stream lines, what the app does with each one, how long it waits before connecting again, and the
 * messages it already showed. Unit tested (PushTest).
 *
 * The gateway relays the member's own topic from the stack's ntfy server at [PATH], on its own
 * address, as one JSON object per line: `open` when the connection starts, `keepalive` every
 * 25 seconds or so, `message` for a notification and `message_delete` / `message_clear` when the
 * gateway withdraws one (a deck's changes no longer wait). A message's `sequence_id` (a deck's
 * `deck-<id>`) means a later message with the same one replaces it on the phone.
 */
object Push {
    const val PATH = "/app/notifications/stream"

    /** The key is the member's topic as the gateway made it: `mtg-` and 22 random characters. */
    private val KEY = Regex("^mtg-[A-Za-z0-9_-]{22}$")
    /** Message ids from ntfy, and the groups the gateway makes, as the app keeps or shows them. */
    private val ID = Regex("^[A-Za-z0-9]{8,24}$")
    private val GROUP = Regex("^deck-[0-9]{1,12}$")

    fun isKey(key: String?): Boolean = key != null && KEY.matches(key)

    /** What one line of the stream asks of the app. */
    sealed class Action {
        /** The connection works (an `open` or a `keepalive`). */
        object Alive : Action()

        /** Show a notification; [tag] names it on the phone (its group or its own id). */
        data class Show(val id: String, val tag: String, val title: String, val text: String, val click: String?) : Action()

        /** Take the notification [tag] off the phone. */
        data class Remove(val tag: String) : Action()

        /** Nothing to do (an event the app doesn't know, or a line that isn't JSON). */
        object Skip : Action()
    }

    /** The [Action] for one stream [line]. */
    fun parse(line: String): Action {
        val o = try {
            JSONObject(line)
        } catch (e: JSONException) {
            return Action.Skip
        }
        val group = o.optString("sequence_id").takeIf { GROUP.matches(it) }
        return when (o.optString("event")) {
            "open", "keepalive" -> Action.Alive
            "message" -> {
                val id = o.optString("id").takeIf { ID.matches(it) } ?: return Action.Skip
                Action.Show(
                    id = id,
                    tag = group ?: id,
                    title = o.optString("title").take(120).ifBlank { "MTG Gateway" },
                    text = o.optString("message").take(1000),
                    click = o.optString("click").takeIf { it.startsWith("https://") },
                )
            }
            "message_delete", "message_clear" -> if (group != null) Action.Remove(group) else Action.Skip
            else -> Action.Skip
        }
    }

    /** `since` for the next connection: the last message id the app got, so nothing is missed. */
    fun since(lastId: String?): String? = lastId?.takeIf { ID.matches(it) }

    /**
     * Seconds before connecting again after the [failures]th failure in a row (1 is the first):
     * 5, 10, 20, 40, 80, 160 and then 300 at most, so a gateway that is down or restarting is not
     * asked over and over. [jitter] (0 to 1) spreads phones that lost the gateway together.
     */
    fun backoffSeconds(failures: Int, jitter: Double): Long {
        val base = if (failures <= 1) 5L else minOf(MAX_BACKOFF_S, 5L shl minOf(failures - 1, 6))
        return minOf(MAX_BACKOFF_S, base + (base * 0.2 * jitter.coerceIn(0.0, 1.0)).toLong())
    }

    /** What the app does after the gateway answered a connection with [status]. */
    enum class Refusal {
        /** Keep the stream (200). */
        NONE,
        /** The key is nobody's or the member switched notifications off: stop until they press again. */
        STOP,
        /** Try again later (anything else: the gateway restarting, too many connections, no network). */
        RETRY,
    }

    /**
     * What [status] means, with the gateway's error code from the refusal's JSON body ([body]):
     * only the gateway's own refusals stop the app (a proxy or a firewall answering 401 or 403
     * with a page of its own never switches a phone off).
     */
    fun refusal(status: Int, body: String?): Refusal {
        if (status == 200) return Refusal.NONE
        val error = try {
            JSONObject(body ?: "").optString("error")
        } catch (e: JSONException) {
            ""
        }
        val stop = (status == 401 && error == "unknown_key") || (status == 403 && error == "off") ||
            (status == 404 && error == "not_set_up")
        return if (stop) Refusal.STOP else Refusal.RETRY
    }

    /**
     * The last [size] message ids the app showed, oldest first: a message the gateway sends again
     * (after a reconnect from `since`) is not shown twice. Kept in the app's preferences as one
     * comma-separated string.
     */
    class Seen(stored: String?, private val size: Int = 50) {
        private val ids = ArrayDeque((stored ?: "").split(',').filter { ID.matches(it) }.takeLast(size))

        /** True the first time [id] is seen (and it is remembered), false after. */
        fun first(id: String): Boolean {
            if (id in ids) return false
            ids.addLast(id)
            while (ids.size > size) ids.removeFirst()
            return true
        }

        fun stored(): String = ids.joinToString(",")
    }

    const val MAX_BACKOFF_S = 300L
    /** A connection that ends sooner than this counts as a failure (backoff), not as having worked. */
    const val WORKED_MS = 30_000L
    /** No line for this long (the gateway sends one every 25 s) means the connection is dead. */
    const val READ_TIMEOUT_MS = 90_000
}
