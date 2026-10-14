package local.mtgassistantgateway.app

/**
 * When the app reloads its page by itself because the phone's network came back (Q63). The app
 * calls [onNetwork] each time the default network's state is reported (ConnectivityManager's
 * default-network callback, registered while the activity is started) with whether its error box
 * is showing over a page that failed to load (the gateway's offline page, a connection error, the
 * gateway not answering). A reload is due only when:
 * - the error box is showing (a page that is working is never reloaded);
 * - the network has just become usable (connected and validated by Android): reports while it
 *   stays usable (signal or bandwidth changes) do nothing. The first report after the callback is
 *   registered counts as the network coming back, since the app did not watch while stopped;
 * - and the last automatic reload was at least [minGapMs] ago, so a network that flaps can never
 *   make the app reload over and over.
 * Pure, unit tested (ReconnectTest).
 */
class Reconnect(private val minGapMs: Long = MIN_GAP_MS) {
    private var usable: Boolean? = null // null: nothing reported since the callback was registered
    private var lastReload: Long? = null

    /** The default network is [usable] (or not) now, at [nowMs] (elapsedRealtime). True: reload. */
    fun onNetwork(usable: Boolean, errorShowing: Boolean, nowMs: Long): Boolean {
        val was = this.usable
        this.usable = usable
        if (!usable || was == true || !errorShowing) return false
        val last = lastReload
        if (last != null && nowMs - last < minGapMs) return false
        lastReload = nowMs
        return true
    }

    /** The callback was unregistered (the activity stopped): the next report starts afresh. */
    fun reset() {
        usable = null
    }

    companion object {
        const val MIN_GAP_MS = 10_000L
    }
}
