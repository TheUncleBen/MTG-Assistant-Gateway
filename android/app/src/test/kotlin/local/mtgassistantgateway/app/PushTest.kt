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
            """{"id":"m1AbCdEf9","time":2,"event":"message","sequence_id":"deck-42","title":"2 changes to Atraxa are waiting for you",""" +
                """"message":"Open the review page.","click":"https://mtg.example.com/proposals"}""",
        )
        assertEquals(
            Push.Action.Show("m1AbCdEf9", "deck-42", "2 changes to Atraxa are waiting for you", "Open the review page.", "https://mtg.example.com/proposals"),
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

    @Test fun beforeAnyMessageSinceIsTheLastLinesTime() {
        // a stream that stalled before its first message: the next connection starts a second
        // before the gateway's time on the last line the app got, so nothing sent meanwhile is lost
        assertEquals("1794369039", Push.since(null, 1794369040L))
        assertEquals("m1AbCdEf9", Push.since("m1AbCdEf9", 1794369040L)) // a message id wins
        assertEquals("1794369039", Push.since("1&x=2", 1794369040L))
        assertNull(Push.since(null, 5L)) // not a Unix time the gateway takes
        assertNull(Push.since(null, null))
    }

    @Test fun lineTimeReadsTheGatewaysTime() {
        assertEquals(1794369040L, Push.lineTime("""{"event":"keepalive","time":1794369040}"""))
        assertEquals(1794369039L, Push.lineTime("""{"id":"open0001","time":1794369039,"event":"open"}"""))
        assertNull(Push.lineTime("""{"event":"keepalive","time":5}"""))
        assertNull(Push.lineTime("""{"event":"keepalive"}"""))
        assertNull(Push.lineTime("not json"))
    }

    @Test fun backoffDoublesUpToFiveMinutes() {
        assertEquals(listOf(5L, 10L, 20L, 40L, 80L, 160L, 300L, 300L, 300L), (1..9).map { Push.backoffSeconds(it, 0.0) })
        assertEquals(6L, Push.backoffSeconds(1, 1.0)) // up to a fifth more, spread over phones
        assertEquals(300L, Push.backoffSeconds(30, 1.0))
    }

    @Test fun theNextTryAndTheQuietConnectionCheckAreAlarms() {
        // waiting: the alarm is the next try itself, so deep sleep can't hold it back
        assertEquals(1_000_000L, Push.nextAlarm(connected = false, retryAt = 1_000_000L, lastLineAt = 0L))
        // connected: a little after the moment the connection would count as dead
        val at = Push.nextAlarm(connected = true, retryAt = 0L, lastLineAt = 500_000L)
        assertEquals(500_000L + Push.READ_TIMEOUT_MS + Push.ALARM_SLACK_MS, at)
        assertTrue(Push.stale(500_000L, at))
        assertFalse(Push.stale(500_000L, 500_000L + Push.READ_TIMEOUT_MS))
        assertTrue(Push.TRY_HOLD_MS > 20_000L) // the wake lock outlasts the connect timeout
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

    // 0.7.47: each kind of alert has its own channel, group and words; a kept alert can be marked read
    @Test fun aKeptAlertCarriesItsKindAndItsOwnTag() {
        val a = Push.parse(
            """{"id":"m4AbCdEf9","event":"message","sequence_id":"a-17","tags":["price"],""" +
                """"title":"Price drop · Sol Ring","message":"$1.20 to $0.90: fell below $1.00.",""" +
                """"click":"https://mtg.example.com/notifications/open/17"}""",
        )
        assertEquals(
            Push.Action.Show(
                "m4AbCdEf9", "a-17", "Price drop · Sol Ring", "$1.20 to $0.90: fell below $1.00.",
                "https://mtg.example.com/notifications/open/17", "price",
            ),
            a,
        )
        assertEquals(Push.Action.Remove("a-17"), Push.parse("""{"event":"message_delete","sequence_id":"a-17"}"""))
        // an unknown tag (a newer gateway's kind) is no kind: the general channel
        val b = Push.parse("""{"id":"m5AbCdEf9","event":"message","tags":["sparkles"],"message":"x"}""") as Push.Action.Show
        assertEquals("", b.kind)
    }

    @Test fun eachKindHasItsChannelAndLevel() {
        assertEquals(Push.Channel("waiting", Push.Level.HIGH), Push.channelFor("waiting"))
        assertEquals(Push.Channel("account", Push.Level.HIGH), Push.channelFor("account"))
        assertEquals(Push.Channel("price", Push.Level.DEFAULT), Push.channelFor("price"))
        assertEquals(Push.Channel("report", Push.Level.DEFAULT), Push.channelFor("report"))
        assertEquals(Push.Channel("archidekt", Push.Level.DEFAULT), Push.channelFor("archidekt"))
        assertEquals(Push.Channel("summary", Push.Level.LOW), Push.channelFor("summary"))
        // a test, and a message from a gateway before 0.7.47: the old channel, as before
        assertEquals(Push.Channel("messages", Push.Level.DEFAULT), Push.channelFor(""))
        assertEquals(Push.Channel("messages", Push.Level.DEFAULT), Push.channelFor("test"))
        assertEquals(Push.KINDS.keys, setOf("waiting", "applied", "report", "price", "archidekt", "account", "summary", "gateway"))
    }

    @Test fun aGroupSaysHowManyOfItsKind() {
        assertEquals("3 price alerts", Push.groupText("price", 3))
        assertEquals("2 changes waiting for you", Push.groupText("waiting", 2))
        assertEquals("2 alerts", Push.groupText("", 2))
    }

    @Test fun onlyAKeptAlertCanBeMarkedRead() {
        assertTrue(Push.markable("a-17"))
        assertFalse(Push.markable("deck-42")) // what waits leaves once it is applied or rejected
        assertFalse(Push.markable("m1AbCdEf9"))
        assertEquals("""{"tag":"a-17"}""", Push.readBody("a-17"))
    }
}
