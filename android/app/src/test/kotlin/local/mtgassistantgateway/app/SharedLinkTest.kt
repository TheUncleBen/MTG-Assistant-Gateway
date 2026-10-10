package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SharedLinkTest {
    private val gw = "https://gateway.example.com"

    private fun open(vararg texts: String?) = (SharedLink.resolve(gw, texts.toList()) as SharedLink.Target.Open).url

    @Test fun bareGatewayLink() = assertEquals("$gw/decks/42", open("https://gateway.example.com/decks/42"))

    @Test fun gatewayLinkInsideMessageText() =
        assertEquals("$gw/decks/42", open("look at this https://gateway.example.com/decks/42 what do you think?"))

    @Test fun discordWrappedAndPunctuated() {
        assertEquals("$gw/decks/42", open("<https://gateway.example.com/decks/42>"))
        assertEquals("$gw/decks/42", open("[my deck](https://gateway.example.com/decks/42)."))
        assertEquals("$gw/decks/42", open("see https://gateway.example.com/decks/42!"))
    }

    @Test fun gatewayPreferredOverEarlierOtherLink() =
        assertEquals("$gw/scan", open("https://discord.com/channels/1/2/3 https://gateway.example.com/scan"))

    @Test fun archidektDeckOpensGatewayDeckPage() {
        assertEquals("$gw/decks/123456", open("https://archidekt.com/decks/123456/my_deck"))
        assertEquals("$gw/decks/7", open("Check out my deck https://www.archidekt.com/decks/7#maybeboard"))
        assertEquals("$gw/decks/99", open("HTTPS://ARCHIDEKT.COM/decks/99?view=table"))
    }

    @Test fun clipDataOrSubjectUsedWhenTextHasNoLink() =
        assertEquals("$gw/decks/5", open("a deck", null, "https://archidekt.com/decks/5"))

    @Test fun otherLinksNotOpened() {
        assertEquals(
            SharedLink.Target.Other("https://discord.com/channels/1/2/3"),
            SharedLink.resolve(gw, listOf("https://discord.com/channels/1/2/3")),
        )
        // Lookalike hosts and non-deck Archidekt pages are not deck links.
        assertEquals(
            SharedLink.Target.Other("https://archidekt.com.evil.example/decks/1"),
            SharedLink.resolve(gw, listOf("https://archidekt.com.evil.example/decks/1")),
        )
        assertEquals(
            SharedLink.Target.Other("https://gateway.example.com.evil.example/decks/1"),
            SharedLink.resolve(gw, listOf("https://gateway.example.com.evil.example/decks/1")),
        )
    }

    @Test fun noLink() {
        assertEquals(SharedLink.Target.None, SharedLink.resolve(gw, listOf("just some text", null, "")))
        assertEquals(SharedLink.Target.None, SharedLink.resolve(gw, listOf("http://gateway.example.com/decks/1")))
        assertEquals(SharedLink.Target.None, SharedLink.resolve(gw, emptyList()))
    }

    @Test fun archidektDeckIdRules() {
        assertEquals("1", SharedLink.archidektDeckId("https://archidekt.com/decks/1"))
        assertNull(SharedLink.archidektDeckId("https://archidekt.com/u/someone"))
        assertNull(SharedLink.archidektDeckId("https://archidekt.com/decks/abc"))
        assertNull(SharedLink.archidektDeckId("https://archidekt.com/decks/1234567890123"))
        assertNull(SharedLink.archidektDeckId("https://archidekt.com:8443/decks/1"))
        assertNull(SharedLink.archidektDeckId("https://me@archidekt.com/decks/1"))
        assertNull(SharedLink.archidektDeckId("http://archidekt.com/decks/1"))
        assertNull(SharedLink.archidektDeckId("https://notarchidekt.com/decks/1"))
    }

    @Test fun urlsInFindsAllInOrder() =
        assertEquals(
            listOf("https://a.example/x", "https://b.example/y"),
            SharedLink.urlsIn("one https://a.example/x, two https://b.example/y."),
        )
}
