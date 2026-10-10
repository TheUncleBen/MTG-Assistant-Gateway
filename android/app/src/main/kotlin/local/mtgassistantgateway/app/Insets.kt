package local.mtgassistantgateway.app

import android.graphics.Canvas
import android.graphics.ColorFilter
import android.graphics.Paint
import android.graphics.PixelFormat
import android.graphics.drawable.ColorDrawable
import android.graphics.drawable.Drawable
import android.os.Build
import android.view.View
import android.view.WindowInsets

/**
 * Apps targeting Android 15+ draw under the status and navigation bars, and with Android 16 as the
 * target the window no longer resizes for the keyboard either. Each screen's root view therefore
 * takes the system bars, the display cutout and the keyboard as padding so nothing is covered.
 *
 * Android 15+ also ignores the theme's `statusBarColor` and `navigationBarColor`: the bars are
 * transparent and whatever the root view draws shows through. With the root's plain page
 * background that left white status-bar icons on the light theme's near-white page. So on those
 * versions the root's background paints the pages' top-bar colour under the status bar and the
 * bottom tab bar's colour under the navigation bar ([BarStrips]), the way Android 10-14 show them
 * from the theme, and the theme's light/dark icon flags stay right.
 *
 * Android 10 (API 29) has none of the typed-insets API; it is also never edge-to-edge here, so the
 * padding is simply not applied there.
 */
object Insets {
    /** [top] false for a panel pinned to the bottom of the screen, which must not grow at the top. */
    fun padSystemBars(root: View, top: Boolean = true) {
        if (Build.VERSION.SDK_INT < 30) return
        val originalTop = root.paddingTop
        val strips = if (Build.VERSION.SDK_INT >= 35 && top) {
            val c = root.context
            BarStrips(root.background ?: ColorDrawable(c.getColor(R.color.bg)), c.getColor(R.color.navbar), c.getColor(R.color.toolbar))
                .also { root.background = it }
        } else null
        root.setOnApplyWindowInsetsListener { v, insets ->
            val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            val ime = insets.getInsets(WindowInsets.Type.ime())
            strips?.set(bars.top, bars.bottom)
            v.setPadding(bars.left, if (top) bars.top else originalTop, bars.right, maxOf(bars.bottom, ime.bottom))
            WindowInsets.CONSUMED
        }
        root.requestApplyInsets()
    }

    /** [base] over the whole view, with a [topColor] strip [top] px tall and a [bottomColor] strip [bottom] px tall. */
    private class BarStrips(private val base: Drawable, private val topColor: Int, private val bottomColor: Int) : Drawable() {
        private var top = 0
        private var bottom = 0
        private val paint = Paint()

        fun set(top: Int, bottom: Int) {
            if (this.top == top && this.bottom == bottom) return
            this.top = top
            this.bottom = bottom
            invalidateSelf()
        }

        override fun draw(canvas: Canvas) {
            val b = bounds
            base.bounds = b
            base.draw(canvas)
            if (top > 0) {
                paint.color = topColor
                canvas.drawRect(b.left.toFloat(), b.top.toFloat(), b.right.toFloat(), (b.top + top).toFloat(), paint)
            }
            if (bottom > 0) {
                paint.color = bottomColor
                canvas.drawRect(b.left.toFloat(), (b.bottom - bottom).toFloat(), b.right.toFloat(), b.bottom.toFloat(), paint)
            }
        }

        override fun setAlpha(alpha: Int) {
            base.alpha = alpha
            paint.alpha = alpha
        }

        override fun setColorFilter(colorFilter: ColorFilter?) {
            base.colorFilter = colorFilter
        }

        @Deprecated("Deprecated in Java")
        override fun getOpacity(): Int = PixelFormat.TRANSLUCENT
    }
}
