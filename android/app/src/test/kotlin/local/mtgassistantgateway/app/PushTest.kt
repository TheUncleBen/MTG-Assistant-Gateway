package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PushTest {
    private val key = "mtg-AbCdEfGhIjKlMnOpQrSt_-"

    @Test fun onlyGatewayMadeTopicsAreKeys() {
        assertTrue(Push.isKey(key))
        assertFalse(Push.isKey(null))
        assertFalse(Push.isKey("mtg-my-own-topic-1234")) // one a member typed before 0.7.30
        assertFalse(Push.isKey(key + "x"))
        assertFalse(Push.isKey("mtg-AbCdEfGhIjKlMnOpQr/t_-"))
    }

    @Test fun aMessageIsShownUnderItsDecksGroup() {
        val a = Push.parse(
            """{"id":"m1AbCdEf9","time":2,"event":"message","sequence_id":"deck-42","title":"2 changes waiting for Atraxa",""" +
                """"message":"Open the review page.","click":"https://mtg.example.com/proposals"}""",
        )
        assertEquals(
            Push.Action.Show("m1AbCdEf9", "deck-42", "2 changes waiting for Atraxa", "Open the review page.", "https://mtg.example.com/proposals"),
            a,
        )
    }

    @Test fun aMessageWithoutAGroupUsesItsIdAndOnlyHttpsLinks() {
        val a = Push.parse("""{"id":"m2AbCdEf9","event":"message","sequence_id":"../x","message":"Done.","click":"intent://x"}""")
        assertEquals(Push.Action.Show("m2AbCdEf9", "m2AbCdEf9", "MTG Gateway", "Done.", null), a)
    }

    @Test fun withdrawnMessagesAreRemovedAndOtherLinesSkipped() {
        assertEquals(Push.Action.Remove("deck-42"), Push.parse("""{"id":"d1AbCdEf9","event":"message_delete","sequence_id":"deck-42"}"""))
        assertEquals(Push.Action.Remove("deck-7"), Push.parse("""{"event":"message_clear","sequence_id":"deck-7"}"""))
        assertEquals(Push.Action.Skip, Push.parse("""{"event":"message_delete"}"""))
        assertEquals(Push.Action.Alive, Push.parse("""{"event":"keepalive","time":5}"""))
        assertEquals(Push.Action.Alive, Push.parse("""{"id":"o1","event":"open"}"""))
        assertEquals(Push.Action.Skip, Push.parse("""{"event":"poll_request"}"""))
        assertEquals(Push.Action.Skip, Push.parse("not json"))
        assertEquals(Push.Action.Skip, Push.parse("""{"event":"message","id":"x"}""")) // no usable id
    }

    @Test fun longTextsAreCut() {
        val a = Push.parse("""{"id":"m3AbCdEf9","event":"message","title":"${"t".repeat(300)}","message":"${"m".repeat(3000)}"}""")
        a as Push.Action.Show
        assertEquals(120, a.title.length)
        assertEquals(1000, a.text.length)
    }

    @Test fun sinceIsTheLastIdOnly() {
        assertEquals("m1AbCdEf9", Push.since("m1AbCdEf9"))
        assertNull(Push.since(null))
        assertNull(Push.since("1&x=2"))
    }

    @Test fun backoffDoublesUpToFiveMinutes() {
        assertEquals(listOf(5L, 10L, 20L, 40L, 80L, 160L, 300L, 300L, 300L), (1..9).map { Push.backoffSeconds(it, 0.0) })
        assertEquals(6L, Push.backoffSeconds(1, 1.0)) // up to a fifth more, spread over phones
        assertEquals(300L, Push.backoffSeconds(30, 1.0))
    }

    @Test fun onlyTheGatewaysOwnRefusalsStopThePhone() {
        assertEquals(Push.Refusal.NONE, Push.refusal(200, null))
        assertEquals(Push.Refusal.STOP, Push.refusal(401, """{"ok":false,"error":"unknown_key"}"""))
        assertEquals(Push.Refusal.STOP, Push.refusal(403, """{"ok":false,"error":"off"}"""))
        assertEquals(Push.Refusal.STOP, Push.refusal(404, """{"ok":false,"error":"not_set_up"}"""))
        assertEquals(Push.Refusal.RETRY, Push.refusal(403, "<html>a proxy's page</html>"))
        assertEquals(Push.Refusal.RETRY, Push.refusal(401, null))
        assertEquals(Push.Refusal.RETRY, Push.refusal(404, "Not Found")) // an older gateway, or a proxy
        assertEquals(Push.Refusal.RETRY, Push.refusal(429, """{"ok":false,"error":"too_many"}"""))
        assertEquals(Push.Refusal.RETRY, Push.refusal(503, """{"ok":false,"error":"unavailable"}"""))
        assertEquals(Push.Refusal.RETRY, Push.refusal(502, ""))
    }

    @Test fun aMessageSentAgainIsShownOnce() {
        val seen = Push.Seen(null, size = 3)
        assertTrue(seen.first("aaaaaaaa1"))
        assertFalse(seen.first("aaaaaaaa1"))
        assertTrue(seen.first("aaaaaaaa2"))
        assertTrue(seen.first("aaaaaaaa3"))
        assertTrue(seen.first("aaaaaaaa4")) // the oldest goes
        assertEquals("aaaaaaaa2,aaaaaaaa3,aaaaaaaa4", seen.stored())
        val again = Push.Seen(seen.stored() + ",bad id,", size = 3)
        assertFalse(again.first("aaaaaaaa4"))
        assertTrue(again.first("aaaaaaaa1"))
    }
}
