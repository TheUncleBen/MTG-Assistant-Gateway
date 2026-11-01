package local.mtgassistantgateway.app

import java.net.URI
import java.net.URISyntaxException

/**
 * Pull to refresh (0.7.46, screen 9): which gateway pages offer it. Pure, unit tested.
 *
 * A pull from the very top of a page that shows a list reloads it, as the browser's Reload does
 * (the gateway's pages read Archidekt again on a reload instead of their cached copy). Forms, the
 * deck editor, the scanner and the sign-in never offer it: a pull there would throw away what is
 * being typed, edited or scanned. Whether one touch may start a pull is the page's call
 * (static/topbar.js tells the app at each touch through `MtgNative.pullAllowed`): not while a box
 * or menu is open, not from a part of the page that scrolls on its own, not while scrolled down.
 */
object PullRefresh {
    /** The deck page offers it too. Open for Ben: its top is dense with touch targets. */
    const val DECK_PAGE = true

    /** The list pages, by path; a query string or trailing slash makes no difference. */
    private val PAGES = setOf(
        "/", "/decks", "/collection", "/collection/wants", "/watchlist", "/proposals", "/history",
        "/reports", "/notifications", "/precons", "/cards", "/search", "/activity",
    )
    private val DECK = Regex("^/decks/\\d+$")

    /** True when the page at [url] (on the gateway at [origin]) offers pull to refresh. */
    fun offered(origin: String, url: String?): Boolean {
        if (!GatewayUrl.isGateway(origin, url)) return false
        val path = pathOf(url!!) ?: return false
        if (path in PAGES || path == "/forum" || path.startsWith("/forum/")) return true
        return DECK_PAGE && DECK.matches(path)
    }

    private fun pathOf(url: String): String? {
        val p = try {
            URI(url).path ?: return null
        } catch (e: URISyntaxException) {
            return null
        }
        return if (p.length > 1) p.trimEnd('/') else "/"
    }
}
