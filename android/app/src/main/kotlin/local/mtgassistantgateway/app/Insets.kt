package local.mtgassistantgateway.app

import android.os.Build
import android.view.View
import android.view.WindowInsets

/**
 * Apps targeting Android 15+ draw under the status and navigation bars, and with Android 16 as the
 * target the window no longer resizes for the keyboard either. Each screen's root view therefore
 * takes the system bars, the display cutout and the keyboard as padding so nothing is covered.
 *
 * Android 10 (API 29) has none of the typed-insets API; it is also never edge-to-edge here, so the
 * padding is simply not applied there.
 */
object Insets {
    /** [top] false for a panel pinned to the bottom of the screen, which must not grow at the top. */
    fun padSystemBars(root: View, top: Boolean = true) {
        if (Build.VERSION.SDK_INT < 30) return
        val originalTop = root.paddingTop
        root.setOnApplyWindowInsetsListener { v, insets ->
            val bars = insets.getInsets(WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            val ime = insets.getInsets(WindowInsets.Type.ime())
            v.setPadding(bars.left, if (top) bars.top else originalTop, bars.right, maxOf(bars.bottom, ime.bottom))
            WindowInsets.CONSUMED
        }
        root.requestApplyInsets()
    }
}
