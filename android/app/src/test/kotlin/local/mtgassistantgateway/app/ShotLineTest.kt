package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

class ShotLineTest {
    private fun of(
        pending: Boolean,
        matched: Boolean = false,
        name: String = "",
        status: String = "",
        error: String = "",
        read: String = "",
        blurry: Boolean = false,
        auto: Boolean = false,
    ) = ShotLine.of(error, status, pending, matched, name, "frc", "21", read, blurry, auto)

    @Test fun aMatchNamesTheCardAndItsPrinting() {
        assertEquals(ShotLine.Outcome.Found("Sol Ring", " (FRC 21)", false), of(true, matched = true, name = "Sol Ring"))
        assertEquals(ShotLine.Outcome.Found("Sol Ring", " (FRC 21)", true), of(true, matched = true, name = "Sol Ring", auto = true))
        assertEquals(" (FRC)", ShotLine.where("frc", ""))
        assertEquals("", ShotLine.where("", "21"))
    }

    @Test fun aReadThatMatchedNothingIsNeverAMatch() {
        // The review's repro: a read waiting on the page used to show "Found a match" whatever it was.
        for (auto in listOf(false, true)) {
            val o = of(true, matched = false, read = "Xyzzy Qwerty", status = "No match. Pick from the suggestions or type the name.", auto = auto)
            assertEquals(ShotLine.Outcome.NoMatch("Xyzzy Qwerty", false, auto), o)
            assertFalse(o is ShotLine.Outcome.Found)
        }
        // A matched flag without a name (a page that sent none) is no match either.
        assertTrue(of(true, matched = true, name = "") is ShotLine.Outcome.NoMatch)
        assertEquals(ShotLine.Outcome.NoMatch("i i", true, false), of(true, read = "i i", blurry = true))
    }

    @Test fun nothingReadSaysSoWithThePagesReason() {
        val why = "Found the card but could not read its name. Hold it steady in good light."
        assertEquals(ShotLine.Outcome.Unreadable(why), of(false, status = why))
        assertEquals(ShotLine.Outcome.Next, of(false))
        // In Auto the page's own line ("Added …", "Still …") is shown as it is.
        assertEquals(ShotLine.Outcome.PageSaid("Added Sol Ring (FRC 21). Next card, or undo."),
            of(false, status = "Added Sol Ring (FRC 21). Next card, or undo.", auto = true))
        assertEquals(ShotLine.Outcome.Failed("photo not loaded"), of(true, matched = true, name = "Sol Ring", error = "photo not loaded"))
        // The photo never reached the page at all: its own outcome, for a plain line.
        assertEquals(ShotLine.Outcome.PhotoLost, of(false, error = ScanGlue.PHOTO_LOST))
    }

    @Test fun onceGateRunsOnce() {
        var n = 0
        val gate = OnceGate { n++ }
        assertFalse(gate.fired)
        gate.fire()
        gate.fire()
        assertEquals(1, n)
        assertTrue(gate.fired)
    }

    @Test fun theCameraFocusesOnTheBoxBeforeEachShot() {
        // CameraX cannot run on the JVM, so this reads CameraPanel.kt: the shutter goes through a
        // focus and metering action on the dashed box, and the picture is taken only from the gate
        // that the focus result or its timeout opens.
        val src = listOf("src/main/kotlin", "app/src/main/kotlin", "android/app/src/main/kotlin")
            .map { File(it, "local/mtgassistantgateway/app/CameraPanel.kt") }.first { it.isFile }.readText()
        assertTrue(src.contains("startFocusAndMetering("))
        assertTrue(src.contains("FocusMeteringAction.FLAG_AF"))
        assertTrue(src.contains("OnceGate {"))
        // the focus result is noted for "Save this photo" (ScanPhoto), then the gate opens
        val listener = src.substringAfter("focus.addListener({").substringBefore("}, activity.mainExecutor)")
        assertTrue(listener.contains("isFocusSuccessful") && listener.trimEnd().endsWith("gate.fire()"))
        val timeout = src.substringAfter("ui.postDelayed({").substringBefore("}, FOCUS_TIMEOUT_MS)")
        assertTrue(timeout.trimEnd().endsWith("gate.fire()"))
        assertEquals("one place takes the picture", 1, Regex("""\.takePicture\(""").findAll(src).count())
        assertTrue(src.contains("CAPTURE_MODE_MAXIMIZE_QUALITY"))
        assertFalse(src.contains("R.string.pending_on_page"))
    }
}
