package local.mtgassistantgateway.app

import java.net.URI
import java.net.URISyntaxException

/**
 * Pull to refresh: which gateway pages offer it, and how a pull asks for the page. Pure, unit tested.
 *
 * Every gateway page offers it (owner, 2026-11-08: "every page should have the functionality of
 * being able to drag from the top of the screen down to refresh the page like you would intuitively
 * be able to do in any other application"), except the few where a pull would break something
 * the page itself can't protect: the scanner (its camera and the cards scanned so far), the
 * sign-in and sign-out pages (a pull would start or end a sign-in again), and a goldfish game
 * (its game lives in the page). 0.7.46 offered it on list pages only.
 *
 * A pull loads the page again with [HEADERS] (WebView.loadUrl(String, Map), MainActivity.pull):
 * the gateway's decks.is_pull reads it as the member asking for what is on Archidekt now. The pages
 * read Archidekt again, through its pacer, instead of the copies it keeps for a while
 * (app.ReloadMiddleware); the deck page always reads the deck again, unless its last read is under
 * three seconds old (decks.PAGE_DECK_PULL_FLOOR). The app says so itself because Android does not
 * document which headers its WebView.reload sends; a dedicated header, not Cache-Control, because
 * loadUrl's documentation says WebView may replace its caching headers with its own. A GET of the
 * page's own address: a page drawn from a form's POST is never sent again.
 *
 * Whether one touch may start a pull is the page's call (static/topbar.js tells the app at each
 * touch through `MtgNative.pullAllowed`): not while a box or menu is open, not while something
 * typed in a form is unsent (a settings page, a forum reply), not from a part of the page that
 * scrolls on its own or can be dragged, not while scrolled down, and not on a page that says
 * `data-no-pull`.
 */
object PullRefresh {
    /** The header a pull sends (the gateway's decks.PULL_HEADER), and its value. */
    const val HEADER = "X-MTG-Pull"
    val HEADERS: Map<String, String> = mapOf(HEADER to "1")

    /** The pages that never offer it, by path; a query string or trailing slash makes no difference. */
    private val NEVER = setOf("/login", "/logout", "/signed-out", "/data-deleted")
    private val NEVER_UNDER = listOf("/scan/", "/login/", "/auth/")
    private val GOLDFISH = Regex("^/decks/\\d+/goldfish$")

    /** True when the page at [url] (on the gateway at [origin]) offers pull to refresh. */
    fun offered(origin: String, url: String?): Boolean {
        if (!GatewayUrl.isGateway(origin, url)) return false
        val path = pathOf(url!!) ?: return false
        if (path == "/scan" || path in NEVER || GOLDFISH.matches(path)) return false
        return NEVER_UNDER.none { path.startsWith(it) }
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
