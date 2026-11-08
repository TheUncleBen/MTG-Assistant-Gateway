package local.mtgassistantgateway.app

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.RectF
import android.util.AttributeSet
import android.view.View

/**
 * A dashed card outline over the camera preview, 63:88 like a Magic card, so people hold the
 * card where the photo will have it large and sharp. The page finds the card's real edges in the
 * photo afterwards, so framing only has to be roughly right.
 *
 * Its width is [widthShare] of the viewfinder's, which the camera panel sets from the preview's
 * crop so a card filling the box fills [TorchMath.CARD_SHARE] of the photo: the phone's main
 * camera about 15 cm from the card, the distance the website's guide asks for too. The main lens
 * of some phones (Samsung's Ultra models among them) cannot focus much closer, and a card held
 * closer comes out blurred. Until the crop is known it is [DEFAULT_WIDTH], the share on a tall
 * phone's screen.
 */
class CardGuideView @JvmOverloads constructor(context: Context, attrs: AttributeSet? = null) : View(context, attrs) {
    private val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
        style = Paint.Style.STROKE
        strokeWidth = 4f
        color = Color.argb(230, 250, 137, 13)
        pathEffect = DashPathEffect(floatArrayOf(28f, 18f), 0f)
    }
    private val rect = RectF()

    /** The box's width as a share of the view's (see [TorchMath.guideWidthShare]). */
    var widthShare: Float = DEFAULT_WIDTH
        set(value) {
            if (value == field || !value.isFinite() || value <= 0f) return
            field = value
            invalidate()
        }

    /** The outline in view pixels; the camera screen uses it to describe the crop to the person. */
    fun guide(): RectF = RectF(rect)

    override fun onDraw(canvas: Canvas) {
        val w = width.toFloat()
        val h = height.toFloat()
        if (w <= 0f || h <= 0f) return
        // Fit a 63:88 card into widthShare of the width, and at most 80% of the height.
        var cw = w * widthShare
        var ch = cw * 88f / 63f
        if (ch > h * 0.8f) {
            ch = h * 0.8f
            cw = ch * 63f / 88f
        }
        rect.set((w - cw) / 2f, (h - ch) / 2f, (w + cw) / 2f, (h + ch) / 2f)
        canvas.drawRoundRect(rect, 24f, 24f, paint)
    }

    companion object {
        /** About the share on a tall phone in portrait (a 3:4 preview cropped to the screen). */
        const val DEFAULT_WIDTH = 0.6f
    }
}
