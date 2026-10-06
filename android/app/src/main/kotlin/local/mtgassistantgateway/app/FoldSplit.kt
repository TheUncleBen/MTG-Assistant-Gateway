package local.mtgassistantgateway.app

/**
 * Where to split the scan panel on a half-folded phone. Pure, unit tested.
 *
 * A foldable held half open has a hinge across the window: horizontal in tabletop posture (the
 * phone sits on the table like a laptop), vertical in book posture. The viewfinder then goes on
 * one side of the hinge and the controls on the other, so neither is bent in the middle.
 * Everything is in the panel's own pixels; the hinge rectangle comes from WindowManager in window
 * pixels, so the caller subtracts the panel's position in the window first.
 */
object FoldSplit {
    /**
     * [horizontal]: the hinge runs left to right, so the viewfinder is the top [first] pixels and
     * the controls start [second] pixels down; otherwise the viewfinder is the left [first] pixels
     * and the controls start [second] pixels in.
     */
    data class Split(val horizontal: Boolean, val first: Int, val second: Int)

    /**
     * The split for a hinge at ([hingeLeft], [hingeTop])-([hingeRight], [hingeBottom]) in panel
     * pixels, or null when the hinge does not cross the panel or would leave either side under a
     * quarter of the panel (a hinge at the very edge is not worth splitting for).
     */
    fun compute(
        panelWidth: Int,
        panelHeight: Int,
        hingeLeft: Int,
        hingeTop: Int,
        hingeRight: Int,
        hingeBottom: Int,
        horizontal: Boolean,
    ): Split? {
        if (panelWidth <= 0 || panelHeight <= 0) return null
        val size = if (horizontal) panelHeight else panelWidth
        val start = if (horizontal) hingeTop else hingeLeft
        val end = if (horizontal) hingeBottom else hingeRight
        if (end < start) return null
        if (start <= 0 || end >= size) return null
        val quarter = size / 4
        if (start < quarter || size - end < quarter) return null
        return Split(horizontal, start, end)
    }
}
