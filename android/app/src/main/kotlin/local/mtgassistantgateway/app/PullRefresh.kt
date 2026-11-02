package local.mtgassistantgateway.app

import java.net.URI
import java.net.URISyntaxException

/**
 * Pull to refresh (0.7.46, screen 9): which gateway pages offer it, and how a pull asks for the
 * page. Pure, unit tested.
 *
 * A pull from the very top of a page that shows a list loads that page again with [HEADERS]
 * (WebView.loadUrl(String, Map), MainActivity.pull): the gateway's decks.is_pull reads it as the
 * member asking for what is on Archidekt now. The pages read Archidekt again, through its pacer,
 * instead of the copies it keeps for a while (app.ReloadMiddleware: My decks, Binder, Binder ›
 * Lists and each list, Pinned cards, Alerts, the forum and profiles); the deck page always reads
 * the deck again, unless its last read is under three seconds old (decks.PAGE_DECK_PULL_FLOOR).
 * The app says so itself because Android does not document which headers its WebView.reload sends;
 * a dedicated header, not Cache-Control, because loadUrl's documentation says WebView may replace
 * its caching headers with its own. A GET of the page's own address: a page drawn from a form's
 * POST is never sent again. Forms, the deck editor, the scanner and the sign-in never offer it: a
 * pull there would throw away what is being typed, edited or scanned. A forum thread is such a page
 * (its reply box and contest entry are forms), so the forum's own lists offer it and a thread does
 * not. Whether one touch may start a
 * pull is the page's call (static/topbar.js tells the app at each touch through
 * `MtgNative.pullAllowed`): not while a box or menu is open, not from a part of the page that
 * scrolls on its own, not while scrolled down.
 */
object PullRefresh {
    /** The deck page offers it too. Open for the owner: its top is dense with touch targets. */
    const val DECK_PAGE = true

    /** The header a pull sends (the gateway's decks.PULL_HEADER), and its value. */
    const val HEADER = "X-MTG-Pull"
    val HEADERS: Map<String, String> = mapOf(HEADER to "1")

    /** The list pages, by path; a query string or trailing slash makes no difference. */
    private val PAGES = setOf(
        "/", "/decks", "/collection", "/collection/wants", "/collection/lists", "/watchlist",
        "/proposals", "/history", "/reports", "/notifications", "/precons", "/cards", "/search",
        "/activity",
    )
    private val DECK = Regex("^/decks/\\d+$")

    /** The forum's lists: the front page and a section (/forum/<group>/<section>), never a thread. */
    private val FORUM = Regex("^/forum(/(?!thread(/|$))[^/]+/[^/]+)?$")

    /** Binder › Lists' own pages (lists_page.TABS), never the box addresses under them. */
    private val LIST = Regex("^/lists/(templates|defaults|tags|btags|custom)$")

    /** A profile: someone's public decks (browse.user_page). */
    private val PROFILE = Regex("^/users/[^/]+$")

    /** True when the page at [url] (on the gateway at [origin]) offers pull to refresh. */
    fun offered(origin: String, url: String?): Boolean {
        if (!GatewayUrl.isGateway(origin, url)) return false
        val path = pathOf(url!!) ?: return false
        if (path in PAGES || FORUM.matches(path)) return true
        if (LIST.matches(path) || PROFILE.matches(path)) return true
        return DECK_PAGE && DECK.matches(path)
    }

    /**
     * The address a pull loads: the page's own without its #fragment, since loading an address
     * that differs from the page's only in its fragment scrolls instead of loading the page.
     */
    fun target(url: String): String = url.substringBefore('#')

    private fun pathOf(url: String): String? {
        val p = try {
            URI(url).path ?: return null
        } catch (e: URISyntaxException) {
            return null
        }
        return if (p.length > 1) p.trimEnd('/') else "/"
    }
}
