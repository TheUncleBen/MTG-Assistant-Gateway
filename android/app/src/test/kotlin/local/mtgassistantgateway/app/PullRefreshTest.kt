package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

class PullRefreshTest {
    private val origin = "https://mtg.example.com"

    /** Owner, 2026-11-08: every page, as in any other app (0.7.46 offered it on list pages only). */
    @Test fun everyPageOffersIt() {
        for (path in listOf(
            "/", "/decks", "/decks?folder=Commander", "/collection", "/collection/wants", "/watchlist",
            "/proposals", "/history", "/reports", "/notifications", "/precons", "/cards?q=sol+ring",
            "/search?q=meren", "/forum", "/forum/general/rules", "/forum/threads/all", "/activity", "/decks/",
            "/collection/lists", "/lists/templates", "/lists/defaults?page=2", "/lists/tags", "/lists/btags",
            "/lists/custom?q=elf", "/users/alice", "/users/Some%20One?order=name",
            // pages 0.7.46 left out: the page itself refuses a pull while something typed is unsent
            "/decks/12345", "/decks/12345/", "/decks/12345/edit", "/decks/12345/settings", "/decks/new",
            "/decks/new-package", "/account", "/account/details", "/admin", "/admin/settings", "/install?for=android",
            "/guide", "/lists/templates/new", "/users/alice/decks", "/users", "/forum/thread/123",
            "/proposals/42", "/history/reports/7", "/skill",
        )) {
            assertTrue(path, PullRefresh.offered(origin, "$origin$path"))
        }
    }

    /** The scanner, signing in or out and a goldfish game: a pull would lose what the page holds. */
    @Test fun theScannerSignInAndAGameNeverOfferIt() {
        for (path in listOf(
            "/scan", "/scan/", "/scan/sessions", "/login", "/login?next=%2F", "/login/app-return?code=x",
            "/logout", "/signed-out", "/auth/callback?code=x", "/decks/12345/goldfish", "/data-deleted",
        )) {
            assertFalse(path, PullRefresh.offered(origin, "$origin$path"))
        }
    }

    /** A pull says so in its own header (the gateway's decks.PULL_HEADER) and loads the page itself. */
    @Test fun aPullLoadsThePageWithItsHeader() {
        assertEquals(mapOf("X-MTG-Pull" to "1"), PullRefresh.HEADERS)
        assertEquals("$origin/decks/12345?view=grid", PullRefresh.target("$origin/decks/12345?view=grid#main"))
        assertEquals("$origin/users/alice", PullRefresh.target("$origin/users/alice"))
    }

    @Test fun anotherSiteOrNoPageNeverOffersIt() {
        assertFalse(PullRefresh.offered(origin, "https://archidekt.com/decks"))
        assertFalse(PullRefresh.offered(origin, "https://mtg.example.com:8443/decks"))
        assertFalse(PullRefresh.offered(origin, null))
        assertFalse(PullRefresh.offered(origin, "about:blank"))
        assertFalse(PullRefresh.offered(origin, "https://mtg.example.com/a b"))
    }

    /** The page sits inside the pull layer, so a pull can reach it; the app's own boxes stay above both. */
    @Test fun thePullLayerWrapsThePage() {
        val res = listOf("src/main/res", "app/src/main/res", "android/app/src/main/res")
            .map(::File).first { File(it, "layout/activity_main.xml").isFile }
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(File(res, "layout/activity_main.xml"))
        val layers = doc.getElementsByTagName("androidx.swiperefreshlayout.widget.SwipeRefreshLayout")
        assertEquals(1, layers.length)
        val layer = layers.item(0) as Element
        assertEquals("@+id/refresh", layer.getAttribute("android:id"))
        val webs = layer.getElementsByTagName("WebView")
        assertEquals(1, webs.length)
        assertEquals("@+id/web", (webs.item(0) as Element).getAttribute("android:id"))
        assertEquals("root", (layer.parentNode as Element).getAttribute("android:id").removePrefix("@+id/"))
    }
}
