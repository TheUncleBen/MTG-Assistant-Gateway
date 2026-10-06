package local.mtgassistantgateway.app

/** Slider-to-camera value mapping for the native scan screen. Pure, unit tested. */
object TorchMath {
    /**
     * Torch strength for a slider position. Levels run 1..[maxLevel] (Camera2's
     * FLASH_TORCH_STRENGTH_MAX_LEVEL); a slider of [steps] positions starting at 0 maps
     * linearly, never below 1 and never above the maximum.
     */
    fun levelFor(position: Int, steps: Int, maxLevel: Int): Int {
        if (maxLevel <= 1 || steps <= 1) return 1
        val p = position.coerceIn(0, steps - 1)
        val level = 1 + Math.round(p.toDouble() * (maxLevel - 1) / (steps - 1)).toInt()
        return level.coerceIn(1, maxLevel)
    }

    /** Zoom ratio for a slider position between [min] and [max], linear. */
    fun zoomFor(position: Int, steps: Int, min: Float, max: Float): Float {
        if (steps <= 1 || max <= min) return min
        val p = position.coerceIn(0, steps - 1)
        return (min + (max - min) * p / (steps - 1)).coerceIn(min, max)
    }

    /** Exposure compensation index for a slider position over a [low]..[high] range. */
    fun exposureFor(position: Int, low: Int, high: Int): Int {
        if (high <= low) return low
        return (low + position).coerceIn(low, high)
    }

    /** Slider position that shows a given exposure index. */
    fun exposurePosition(index: Int, low: Int, high: Int): Int = (index - low).coerceIn(0, maxOf(0, high - low))

    /** A camera buffer's size as it shows upright: the sides swap when the buffer is rotated a quarter turn. */
    fun uprightSize(width: Int, height: Int, rotationDegrees: Int): IntArray {
        val quarter = (((rotationDegrees % 360) + 360) % 360) % 180 != 0
        return if (quarter) intArrayOf(height, width) else intArrayOf(width, height)
    }

    /** Degrees a JPEG must be rotated so it is upright, from sensor orientation and display rotation. */
    fun jpegOrientation(sensorOrientation: Int, displayRotationDegrees: Int, frontFacing: Boolean): Int {
        val rotation = ((displayRotationDegrees % 360) + 360) % 360
        return if (frontFacing) (sensorOrientation + rotation) % 360
        else (sensorOrientation - rotation + 360) % 360
    }

    /**
     * The dashed guide box as fractions `[x, y, w, h]` of the photo. The preview covers the view
     * with the camera buffer scaled uniformly and centred (centre crop), so a view pixel maps back
     * to the buffer by the inverse of that scale; the photo has the buffer's aspect, so fractions
     * of the buffer are fractions of the photo. [bufW] and [bufH] are the buffer's upright size.
     * Clamped to the photo; null when the sizes are not known yet.
     */
    fun guideFractions(viewW: Int, viewH: Int, bufW: Int, bufH: Int, gx: Float, gy: Float, gw: Float, gh: Float): FloatArray? {
        if (viewW <= 0 || viewH <= 0 || bufW <= 0 || bufH <= 0 || gw <= 0f || gh <= 0f) return null
        val scale = maxOf(viewW.toFloat() / bufW, viewH.toFloat() / bufH)
        val x0 = ((gx - viewW / 2f) / scale + bufW / 2f) / bufW
        val y0 = ((gy - viewH / 2f) / scale + bufH / 2f) / bufH
        val x1 = x0 + gw / scale / bufW
        val y1 = y0 + gh / scale / bufH
        val cx0 = x0.coerceIn(0f, 1f)
        val cy0 = y0.coerceIn(0f, 1f)
        val cx1 = x1.coerceIn(0f, 1f)
        val cy1 = y1.coerceIn(0f, 1f)
        if (cx1 <= cx0 || cy1 <= cy0) return null
        return floatArrayOf(cx0, cy0, cx1 - cx0, cy1 - cy0)
    }
}
