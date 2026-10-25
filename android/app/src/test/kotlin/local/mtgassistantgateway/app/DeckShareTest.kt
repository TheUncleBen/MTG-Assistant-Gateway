package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Test

class DeckShareTest {
    private val ar = "https://archidekt.com/decks/42"
    private val gw = "https://gateway.example.com/decks/42"

    @Test fun nameThenArchidektLink() = assertEquals("Meren\n$ar", DeckShare.shareText("Meren", ar, null))

    @Test fun noNameIsTheLinkAlone() = assertEquals(ar, DeckShare.shareText("", ar, null))

    // Before Android 14 the sheet has no Copy gateway link action: the link goes in the text
    @Test fun gatewayLinkAfterTheOneAnyoneCanOpen() =
        assertEquals("Meren\n$ar\n\nOn the gateway (members): $gw", DeckShare.shareText("Meren", ar, gw))

    @Test fun emptyGatewayLinkLeftOut() = assertEquals("Meren\n$ar", DeckShare.shareText("Meren", ar, ""))
}
