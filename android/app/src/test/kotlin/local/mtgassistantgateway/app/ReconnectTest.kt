package local.mtgassistantgateway.app

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ReconnectTest {
    @Test fun reloadsWhenTheNetworkComesBackOverAnError() {
        val r = Reconnect()
        assertFalse(r.onNetwork(usable = true, errorShowing = false, nowMs = 0))
        assertFalse(r.onNetwork(usable = false, errorShowing = true, nowMs = 1_000))
        assertTrue(r.onNetwork(usable = true, errorShowing = true, nowMs = 20_000))
    }

    @Test fun neverReloadsAWorkingPage() {
        val r = Reconnect()
        assertFalse(r.onNetwork(usable = true, errorShowing = false, nowMs = 0))
        assertFalse(r.onNetwork(usable = false, errorShowing = false, nowMs = 1_000))
        assertFalse(r.onNetwork(usable = true, errorShowing = false, nowMs = 20_000))
    }

    @Test fun noReloadWhileTheNetworkIsStillDown() {
        val r = Reconnect()
        assertFalse(r.onNetwork(usable = false, errorShowing = true, nowMs = 0))
        assertFalse(r.onNetwork(usable = false, errorShowing = true, nowMs = 60_000))
    }

    @Test fun reportsWhileTheNetworkStaysUsableDoNothing() {
        // Signal or bandwidth changes repeat the callback with the same usable network.
        val r = Reconnect()
        assertFalse(r.onNetwork(usable = false, errorShowing = true, nowMs = 0))
        assertTrue(r.onNetwork(usable = true, errorShowing = true, nowMs = 20_000))
        for (t in 1..50) assertFalse(r.onNetwork(usable = true, errorShowing = true, nowMs = 20_000L + t * 60_000L))
    }

    @Test fun aFlappingNetworkCannotReloadInALoop() {
        val r = Reconnect()
        assertFalse(r.onNetwork(usable = false, errorShowing = true, nowMs = 0))
        assertTrue(r.onNetwork(usable = true, errorShowing = true, nowMs = 1_000))
        var reloads = 0
        var t = 1_000L
        repeat(100) { // down and up every half second, the error box showing throughout
            t += 250
            r.onNetwork(usable = false, errorShowing = true, nowMs = t)
            t += 250
            if (r.onNetwork(usable = true, errorShowing = true, nowMs = t)) reloads++
        }
        // 50 s of flapping: at most one more reload per MIN_GAP_MS
        assertTrue("reloaded $reloads times", reloads <= 5)
        // and the gap is honoured right after a reload
        val q = Reconnect()
        assertFalse(q.onNetwork(usable = false, errorShowing = true, nowMs = 0))
        assertTrue(q.onNetwork(usable = true, errorShowing = true, nowMs = 100))
        assertFalse(q.onNetwork(usable = false, errorShowing = true, nowMs = 200))
        assertFalse(q.onNetwork(usable = true, errorShowing = true, nowMs = 100 + Reconnect.MIN_GAP_MS - 1))
        assertFalse(q.onNetwork(usable = false, errorShowing = true, nowMs = 100 + Reconnect.MIN_GAP_MS))
        assertTrue(q.onNetwork(usable = true, errorShowing = true, nowMs = 100 + Reconnect.MIN_GAP_MS))
    }

    @Test fun theFirstReportAfterRegisteringCountsAsComingBack() {
        // The callback is off while the activity is stopped: returning to the app with the error
        // box up and the network usable reloads once.
        val r = Reconnect()
        assertTrue(r.onNetwork(usable = true, errorShowing = true, nowMs = 0))
        assertFalse(r.onNetwork(usable = true, errorShowing = true, nowMs = 30_000))
        r.reset()
        assertTrue(r.onNetwork(usable = true, errorShowing = true, nowMs = 60_000))
        r.reset()
        assertFalse(r.onNetwork(usable = true, errorShowing = true, nowMs = 61_000)) // within the gap
    }
}
