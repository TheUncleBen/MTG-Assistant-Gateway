package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class FilePickTest {
    @Test fun everyFilePickedReachesAnInputThatTakesSeveral() {
        assertEquals(listOf("a", "b", "c"), FilePick.picked("a", listOf("a", "b", "c"), multiple = true))
        assertEquals(listOf("a"), FilePick.picked(null, listOf("a", "b"), multiple = false))
        assertEquals(listOf("x"), FilePick.picked("x", emptyList(), multiple = true))
        assertEquals(emptyList<String>(), FilePick.picked<String>(null, emptyList(), multiple = true))
    }

    @Test fun heicAndHeifPhotosAreFoundByTypeOrName() {
        assertTrue(FilePick.isHeif("image/heic", null))
        assertTrue(FilePick.isHeif("IMAGE/HEIF", "x.jpg"))
        assertTrue(FilePick.isHeif(null, "20261028_101500.heic"))
        assertTrue(FilePick.isHeif("application/octet-stream", "IMG_1.HEIF"))
        assertFalse(FilePick.isHeif("image/jpeg", "odd.heic")) // the type wins when it is a real one
        assertFalse(FilePick.isHeif(null, "cards.csv"))
        assertFalse(FilePick.isHeif("text/csv", null))
    }

    @Test fun theJpegKeepsThePhotosName() {
        assertEquals("20261028_101500.jpg", FilePick.jpegName("20261028_101500.heic"))
        assertEquals("photo.jpg", FilePick.jpegName(null))
        assertEquals("a_b.jpg", FilePick.jpegName("../a<b.HEIF"))
    }

    @Test fun jpegsAreFoundByTypeOrName() {
        assertTrue(FilePick.isJpeg("image/jpeg", null))
        assertTrue(FilePick.isJpeg("IMAGE/JPEG; q=1", "x"))
        assertTrue(FilePick.isJpeg(null, "20261028_101500.JPG"))
        assertTrue(FilePick.isJpeg("application/octet-stream", "a.jpeg"))
        assertFalse(FilePick.isJpeg("image/png", "odd.jpg")) // the type wins when it is a real one
        assertFalse(FilePick.isJpeg(null, "cards.csv"))
        assertFalse(FilePick.isJpeg("image/heic", null))
    }

    /** A 50 or 200 MP JPEG is changed to a smaller one; a 12 MP one (4000 x 3000) reaches the page as it is. */
    @Test fun onlyAJpegLargerThanTheLimitIsMadeSmaller() {
        assertFalse(FilePick.tooLarge(4000, 3000))
        assertFalse(FilePick.tooLarge(3000, 4096))
        assertTrue(FilePick.tooLarge(4097, 3000))
        assertTrue(FilePick.tooLarge(8160, 6120)) // 50 MP
        assertTrue(FilePick.tooLarge(12240, 16320)) // 200 MP, portrait
        assertEquals(4080, 16320 / FilePick.sampleSize(16320, 12240)) // still far more than the scanner's 2,400 px
        assertTrue(FilePick.QUALITY >= 90)
        assertEquals("IMG_1.jpg", FilePick.jpegName("IMG_1.jpeg"))
        assertEquals("IMG_2.jpg", FilePick.jpegName("IMG_2.JPG"))
    }

    @Test fun aLargePhotoIsDecodedSmallerToFit() {
        assertEquals(1, FilePick.sampleSize(4000, 3000))
        assertEquals(2, FilePick.sampleSize(8160, 6120)) // a 50 MP photo
        assertEquals(4, FilePick.sampleSize(16320, 12240)) // 200 MP
    }
}
