package local.mtgassistantgateway.app

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ScanPhotoTest {
    private fun b(vararg v: Int) = ByteArray(v.size) { v[it].toByte() }

    /** SOI, an APP1 (EXIF) of 4 data bytes, an APP0 of 2, then a DQT and the rest. */
    private val jpeg = b(0xFF, 0xD8, 0xFF, 0xE1, 0x00, 0x06, 1, 2, 3, 4, 0xFF, 0xE0, 0x00, 0x04, 5, 6, 0xFF, 0xDB, 0x00, 0x03, 7, 0xFF, 0xD9)

    @Test fun theNoteGoesAfterTheExifSoTheOrientationStaysWhereReadersLook() {
        val out = ScanPhoto.withNote(jpeg, "hi")
        // everything up to the end of the APPn segments is unchanged
        assertArrayEquals(jpeg.copyOfRange(0, 16), out.copyOfRange(0, 16))
        // then COM, length 4 (2 + "hi"), the text
        assertArrayEquals(b(0xFF, 0xFE, 0x00, 0x04, 'h'.code, 'i'.code), out.copyOfRange(16, 22))
        // then the rest as it was
        assertArrayEquals(jpeg.copyOfRange(16, jpeg.size), out.copyOfRange(22, out.size))
    }

    @Test fun anythingThatIsNotAWellFormedJpegIsLeftAsItIs() {
        val notJpeg = b(0x89, 0x50, 0x4E, 0x47, 0, 0)
        assertArrayEquals(notJpeg, ScanPhoto.withNote(notJpeg, "x"))
        val cut = b(0xFF, 0xD8, 0xFF, 0xE1, 0x10, 0x00, 1, 2) // APP1 claims more than there is
        assertArrayEquals(cut, ScanPhoto.withNote(cut, "x"))
    }

    @Test fun theNoteIsPlainAsciiOneFactALine() {
        val n = ScanPhoto.note(listOf("focus" to "reported sharp", "outcome" to "No match for “Sol Rmg”."))
        assertEquals("focus: reported sharp\noutcome: No match for ?Sol Rmg?.", n)
        assertTrue(ScanPhoto.note(listOf("k" to "x".repeat(10_000))).length <= 4000)
    }

    @Test fun eachSaveHasItsOwnName() {
        assertEquals("mtg-scan-20261101-120000-000.jpg", ScanPhoto.fileName(1_793_534_400_000L))
        assertTrue(ScanPhoto.fileName(1L) != ScanPhoto.fileName(2L))
    }
}
