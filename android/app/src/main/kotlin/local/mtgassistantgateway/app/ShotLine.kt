package local.mtgassistantgateway.app

/**
 * What one photo came to, from the page's `done` event (see [ScanGlue]), for the camera panel's
 * status line: the card found (by name, with its set and number), no match for the name as read,
 * or nothing read at all. Only a card the page matched counts as found: a read that matched no card
 * also waits on the page (with its suggestions), and must never read as a match.
 *
 * Pure, so the unit tests can check it without a device.
 */
object ShotLine {
    sealed class Outcome {
        /** The photo never reached the page at all, by its URL or handed over directly. */
        object PhotoLost : Outcome()
        /** The photo never reached the page's reader (it would not load, or the page threw). */
        data class Failed(val error: String) : Outcome()
        /** A card was matched; it waits on the page for Done. [where] is " (FRC 21)" or "". */
        data class Found(val name: String, val where: String, val autoPaused: Boolean) : Outcome()
        /** A name was read but matched no card; it waits on the page with the suggestions. */
        data class NoMatch(val read: String, val blurry: Boolean, val autoPaused: Boolean) : Outcome()
        /** Nothing worth a lookup was read; [pageStatus] is the page's own reason and advice. */
        data class Unreadable(val pageStatus: String) : Outcome()
        /** Auto: the page's own continuous-mode line ("Added …", "Still …", "Could not place …"). */
        data class PageSaid(val status: String) : Outcome()
        object Next : Outcome()
    }

    fun of(
        error: String,
        status: String,
        pending: Boolean,
        matched: Boolean,
        name: String,
        set: String,
        number: String,
        read: String,
        blurry: Boolean,
        auto: Boolean,
    ): Outcome = when {
        error == ScanGlue.PHOTO_LOST -> Outcome.PhotoLost
        error.isNotEmpty() -> Outcome.Failed(error)
        // A read waits on the page for a tap. The page's own continuous mode stops shooting while
        // one does, and shooting on would only replace it, so Auto pauses here too.
        pending && matched && name.isNotEmpty() -> Outcome.Found(name, where(set, number), auto)
        pending -> Outcome.NoMatch(read, blurry, auto)
        auto && status.isNotEmpty() -> Outcome.PageSaid(status)
        status.isNotEmpty() -> Outcome.Unreadable(status)
        else -> Outcome.Next
    }

    /** " (FRC 21)", " (FRC)" or "" for the set and collector number the page matched. */
    fun where(set: String, number: String): String {
        val s = set.trim().uppercase()
        val n = number.trim()
        return when {
            s.isEmpty() -> ""
            n.isEmpty() -> " ($s)"
            else -> " ($s $n)"
        }
    }
}

/**
 * Runs [go] at most once, for the first of several ways a step can end (the camera reporting
 * focus, or the wait for it running out).
 */
class OnceGate(private val go: () -> Unit) {
    var fired = false
        private set

    fun fire() {
        if (fired) return
        fired = true
        go()
    }
}
