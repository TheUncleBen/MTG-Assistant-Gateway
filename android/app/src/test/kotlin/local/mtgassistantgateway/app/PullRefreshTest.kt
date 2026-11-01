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

    @Test fun listPagesOfferIt() {
        for (path in listOf(
            "/", "/decks", "/decks?folder=Commander", "/collection", "/collection/wants", "/watchlist",
            "/proposals", "/history", "/reports", "/notifications", "/precons", "/cards?q=sol+ring",
            "/search?q=meren", "/forum", "/forum/general/rules", "/activity", "/decks/",
            "/collection/lists", "/lists/templates", "/lists/defaults?page=2", "/lists/tags",
            "/lists/custom?q=elf", "/users/alice", "/users/Some%20One?order=name",
        )) {
            assertTrue(path, PullRefresh.offered(origin, "$origin$path"))
        }
    }

    @Test fun theDeckPageFollowsItsSwitch() {
        assertEquals(PullRefresh.DECK_PAGE, PullRefresh.offered(origin, "$origin/decks/12345"))
        assertEquals(PullRefresh.DECK_PAGE, PullRefresh.offered(origin, "$origin/decks/12345/"))
    }

    @Test fun formsTheEditorAndTheScannerNeverOfferIt() {
        for (path in listOf(
            "/decks/12345/edit", "/decks/12345/settings", "/decks/12345/goldfish", "/decks/new",
            "/decks/new-package", "/scan", "/scan/sessions", "/account", "/account/details",
            "/admin/settings", "/login", "/install?for=android", "/guide", "/lists/templates/new",
            "/lists/custom/picture", "/users/alice/decks", "/users",
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
