package local.mtgassistantgateway.app

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TorchMathTest {
    @Test fun levelCoversWholeRange() {
        assertEquals(1, TorchMath.levelFor(0, 20, 10))
        assertEquals(10, TorchMath.levelFor(19, 20, 10))
        assertEquals(1, TorchMath.levelFor(-5, 20, 10))
        assertEquals(10, TorchMath.levelFor(99, 20, 10))
    }

    @Test fun levelIsMonotonic() {
        var last = 0
        for (p in 0 until 20) {
            val l = TorchMath.levelFor(p, 20, 7)
            assert(l >= last) { "level went down at $p" }
            last = l
        }
    }

    @Test fun levelWithoutStrengthControlIsOne() {
        assertEquals(1, TorchMath.levelFor(15, 20, 1))
        assertEquals(1, TorchMath.levelFor(15, 1, 10))
    }

    @Test fun zoomEndpoints() {
        assertEquals(1f, TorchMath.zoomFor(0, 40, 1f, 8f))
        assertEquals(8f, TorchMath.zoomFor(39, 40, 1f, 8f))
        assertEquals(0.6f, TorchMath.zoomFor(0, 40, 0.6f, 8f))
        assertEquals(1f, TorchMath.zoomFor(10, 40, 1f, 1f))
    }

    @Test fun exposureMapsBothWays() {
        assertEquals(-12, TorchMath.exposureFor(0, -12, 12))
        assertEquals(0, TorchMath.exposureFor(12, -12, 12))
        assertEquals(12, TorchMath.exposureFor(99, -12, 12))
        assertEquals(12, TorchMath.exposurePosition(0, -12, 12))
        assertEquals(0, TorchMath.exposurePosition(-20, -12, 12))
        assertEquals(0, TorchMath.exposurePosition(3, 0, 0))
    }

    @Test fun jpegOrientationBackCamera() {
        // Typical phone: sensor at 90 degrees. Portrait gives 90, landscape (rotation 90) gives 0.
        assertEquals(90, TorchMath.jpegOrientation(90, 0, false))
        assertEquals(0, TorchMath.jpegOrientation(90, 90, false))
        assertEquals(180, TorchMath.jpegOrientation(90, 270, false))
        assertEquals(270, TorchMath.jpegOrientation(90, 180, false))
        assertEquals(0, TorchMath.jpegOrientation(0, 0, false))
    }

    @Test fun jpegOrientationFrontCamera() {
        assertEquals(0, TorchMath.jpegOrientation(270, 90, true))
        assertEquals(270, TorchMath.jpegOrientation(270, 0, true))
    }

    @Test fun guideFractionsMapTheCentreCropBack() {
        // Portrait view 1080x2400 showing a 1080x1920 upright buffer scaled to cover: scale 1.25,
        // so the view shows the middle 864 px of the buffer's width.
        val g = TorchMath.guideFractions(1080, 2400, 1080, 1920, 108f, 360f, 864f, 1680f)!!
        assertEquals(0.18f, g[0], 1e-4f) // (108 - 540) / 1.25 + 540 = 194.4 px of 1080
        assertEquals(0.15f, g[1], 1e-4f) // (360 - 1200) / 1.25 + 960 = 288 px of 1920
        assertEquals(0.64f, g[2], 1e-4f) // 864 / 1.25 = 691.2 px of 1080
        assertEquals(0.7f, g[3], 1e-4f) // 1680 / 1.25 = 1344 px of 1920
        // A box hanging over the edge is clamped; unknown sizes give nothing.
        val c = TorchMath.guideFractions(1000, 1000, 1000, 1000, -100f, -100f, 2000f, 300f)!!
        assertArrayEquals(floatArrayOf(0f, 0f, 1f, 0.2f), c, 1e-5f)
        assertNull(TorchMath.guideFractions(0, 1000, 1000, 1000, 0f, 0f, 10f, 10f))
        assertNull(TorchMath.guideFractions(1000, 1000, 1000, 1000, 2000f, 0f, 10f, 10f))
    }

    /** A card filling the box fills CARD_SHARE of the photo's width, whatever the screen's shape. */
    @Test fun theGuideFillsTheSameShareOfThePhotoOnEveryScreen() {
        assertEquals(0.4f, TorchMath.CARD_SHARE, 0f) // the website's GUIDE_WIDTH too (tests/test_android_shell_rules.py)
        for ((vw, vh) in listOf(1080 to 2190, 1080 to 2400, 1812 to 2176, 2176 to 1812, 720 to 1280)) {
            val share = TorchMath.guideWidthShare(vw, vh, 1440, 1920)!!
            val g = TorchMath.guideFractions(vw, vh, 1440, 1920, (vw - vw * share) / 2f, 0f, vw * share, 10f)!!
            assertEquals("$vw x $vh", TorchMath.CARD_SHARE, g[2], 1e-3f)
        }
        // a tall phone shows about 66 % of the photo's width, so the box is about 60 % of the view
        assertEquals(CardGuideView.DEFAULT_WIDTH, TorchMath.guideWidthShare(1080, 2190, 1440, 1920)!!, 0.01f)
        assertNull(TorchMath.guideWidthShare(0, 2190, 1440, 1920))
        assertEquals(0.9f, TorchMath.guideWidthShare(1000, 1000, 1000, 100)!!, 0f) // never wider than 90 %
    }

    @Test fun uprightSizeSwapsOnQuarterTurns() {
        assertArrayEquals(intArrayOf(4000, 3000), TorchMath.uprightSize(4000, 3000, 0))
        assertArrayEquals(intArrayOf(3000, 4000), TorchMath.uprightSize(4000, 3000, 90))
        assertArrayEquals(intArrayOf(4000, 3000), TorchMath.uprightSize(4000, 3000, 180))
        assertArrayEquals(intArrayOf(3000, 4000), TorchMath.uprightSize(4000, 3000, 270))
        assertArrayEquals(intArrayOf(3000, 4000), TorchMath.uprightSize(4000, 3000, -90))
    }
}
