package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class FoldSplitTest {
    @Test fun tabletopSplitsAtTheHinge() {
        // A 2000 px tall panel with a horizontal hinge band from 980 to 1020.
        val s = FoldSplit.compute(1800, 2000, 0, 980, 1800, 1020, horizontal = true)
        assertEquals(FoldSplit.Split(true, 980, 1020), s)
    }

    @Test fun bookSplitsLeftAndRight() {
        val s = FoldSplit.compute(2000, 1800, 1000, 0, 1000, 1800, horizontal = false)
        assertEquals(FoldSplit.Split(false, 1000, 1000), s) // a zero-width hinge is fine
    }

    @Test fun hingeOutsideOrAtTheEdgeIsNoSplit() {
        assertNull(FoldSplit.compute(1800, 2000, 0, -40, 1800, -10, horizontal = true)) // above the panel
        assertNull(FoldSplit.compute(1800, 2000, 0, 2000, 1800, 2040, horizontal = true)) // below it
        assertNull(FoldSplit.compute(1800, 2000, 0, 300, 1800, 320, horizontal = true)) // leaves too little on top
        assertNull(FoldSplit.compute(1800, 2000, 0, 1700, 1800, 1720, horizontal = true)) // too little below
        assertNull(FoldSplit.compute(0, 0, 0, 10, 10, 20, horizontal = true))
        assertNull(FoldSplit.compute(1800, 2000, 0, 1020, 1800, 980, horizontal = true)) // inverted
    }
}
