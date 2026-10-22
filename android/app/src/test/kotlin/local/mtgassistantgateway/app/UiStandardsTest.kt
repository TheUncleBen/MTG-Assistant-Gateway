package local.mtgassistantgateway.app

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.w3c.dom.Element
import java.io.File
import javax.xml.parsers.DocumentBuilderFactory

/**
 * The UI standard, as it applies to the app's resources (ui-standards 2026-10-21, Android rows):
 * buttons at least 44dp tall, the camera sheet's buttons drawn like the pages' (plain outline, one
 * primary), member-facing messages without raw codes, and the pages' wording rules. Reads the XML
 * straight from src/main/res, so it runs on the JVM without an emulator.
 */
class UiStandardsTest {
    private val res: File = listOf("src/main/res", "app/src/main/res", "android/app/src/main/res")
        .map(::File).first { File(it, "values/strings.xml").isFile }

    private fun elements(file: String, tag: String): List<Element> {
        val doc = DocumentBuilderFactory.newInstance().newDocumentBuilder().parse(File(res, file))
        val nodes = doc.getElementsByTagName(tag)
        return (0 until nodes.length).map { nodes.item(it) as Element }
    }

    private fun strings(): Map<String, String> =
        elements("values/strings.xml", "string").associate { it.getAttribute("name") to it.textContent }

    private fun dp(e: Element, attr: String): Int? =
        e.getAttribute(attr).removeSuffix("dp").toIntOrNull()

    private fun tallEnough(e: Element): Boolean {
        val h = dp(e, "android:layout_height") ?: dp(e, "android:minHeight")
        // A styled button takes its height from the style; CameraButton sets minHeight 48dp.
        return (h != null && h >= 44) || e.getAttribute("style").startsWith("@style/CameraButton")
    }

    @Test fun updateBarButtonsMeetTheTouchMinimum() {
        val buttons = elements("layout/activity_main.xml", "Button")
        val bar = buttons.filter { it.getAttribute("android:id") in setOf("@+id/update_later", "@+id/update_now") }
        assertEquals(2, bar.size)
        for (b in bar) assertTrue(b.getAttribute("android:id"), (dp(b, "android:layout_height") ?: 0) >= 44)
        for (b in buttons) assertTrue(b.getAttribute("android:id"), tallEnough(b))
    }

    @Test fun cameraButtonsAreStyledLikeThePages() {
        val plain = listOf("@+id/undo", "@+id/done")
        val toggles = listOf("@+id/torch_toggle", "@+id/auto_toggle")
        val byId = (elements("layout/camera_panel.xml", "Button") + elements("layout/camera_panel.xml", "ToggleButton"))
            .associateBy { it.getAttribute("android:id") }
        for (id in plain) assertEquals(id, "@style/CameraButton.Plain", byId.getValue(id).getAttribute("style"))
        for (id in toggles) assertEquals(id, "@style/CameraButton.Toggle", byId.getValue(id).getAttribute("style"))
        // One primary button in the row: the round orange Shoot keeps the accent fill, the rest are outlines.
        val shoot = elements("layout/camera_panel.xml", "ImageButton").single { it.getAttribute("android:id") == "@+id/shoot" }
        assertEquals("@drawable/shutter_bg", shoot.getAttribute("android:background"))
        val style = File(res, "values/styles.xml").readText()
        assertTrue(style.contains("<style name=\"CameraButton\">"))
        assertTrue(style.contains("<item name=\"android:minHeight\">48dp</item>"))
        assertTrue(style.contains("<item name=\"android:textAllCaps\">false</item>"))
        assertTrue(File(res, "drawable/button_plain_bg.xml").readText().contains("<stroke "))
        assertTrue(File(res, "drawable/button_toggle_bg.xml").readText().contains("android:state_checked=\"true\""))
    }

    @Test fun togglesSayTheirStateInWordsAndNeverTakeTheOrange() {
        // Torch and Auto read "Torch: On" / "Torch: Off" and "Auto: On" / "Auto: Off" (NEW-WORDING 28)
        val s = strings()
        val byId = elements("layout/camera_panel.xml", "ToggleButton").associateBy { it.getAttribute("android:id") }
        val torch = byId.getValue("@+id/torch_toggle")
        val auto = byId.getValue("@+id/auto_toggle")
        assertEquals("@string/torch_state_on", torch.getAttribute("android:textOn"))
        assertEquals("@string/torch_state_off", torch.getAttribute("android:textOff"))
        assertEquals("@string/auto_state_on", auto.getAttribute("android:textOn"))
        assertEquals("@string/auto_state_off", auto.getAttribute("android:textOff"))
        assertEquals("Torch: On", s.getValue("torch_state_on"))
        assertEquals("Torch: Off", s.getValue("torch_state_off"))
        assertEquals("Auto: On", s.getValue("auto_state_on"))
        assertEquals("Auto: Off", s.getValue("auto_state_off"))
        // the checked state is a tint with a bright outline, not the orange fill next to the orange Shoot
        val bg = File(res, "drawable/button_toggle_bg.xml").readText()
        val checked = bg.substringAfter("android:state_checked=\"true\">").substringBefore("</item>")
        assertFalse(checked.contains("@color/accent"))
        assertTrue(checked.contains("<stroke "))
        assertTrue(checked.contains("@color/cam_tint_on"))
        assertFalse(File(res, "color/button_toggle_text.xml").readText().contains("state_checked"))
    }

    @Test fun errorMessagesCarryNoRawCodes() {
        val s = strings()
        // These once showed an HTTP status, a CameraX code or an installer status; the code goes to the log now.
        for (name in listOf("setup_not_gateway", "camera_error", "update_install_failed")) {
            val text = s.getValue(name)
            assertFalse(name, text.contains("%"))
            assertFalse(name, Regex("""\(\s*(HTTP|[-\d]+)""").containsMatchIn(text))
        }
        assertEquals("The address must start with https://.", s.getValue("setup_bad_http"))
        // No member-facing string shows a bare "(HTTP n)" or "(%d)" code.
        for ((name, text) in s) {
            assertFalse(name, text.contains("(HTTP "))
            assertFalse(name, Regex("""\(%\d${'$'}d\)""").containsMatchIn(text))
        }
    }

    @Test fun wordingFollowsThePagesRules() {
        for ((name, text) in strings()) {
            assertFalse(name, text.contains("verif", ignoreCase = true))
            assertFalse(name, text.contains("—")) // no em-dashes
            assertFalse("$name: quoted names take curly quotes", text.contains('"'))
        }
    }

    @Test fun codesAreLoggedWhereTheyLeftTheMessage() {
        val src = File(res.parentFile.parentFile, "kotlin/local/mtgassistantgateway/app")
        val setup = File(src, "SetupActivity.kt").readText()
        val camera = File(src, "CameraPanel.kt").readText()
        val updater = File(src, "Updater.kt").readText()
        assertFalse(setup.contains("R.string.setup_not_gateway, code"))
        assertTrue(setup.contains("Log.w(TAG, \"healthz answered HTTP \$code"))
        assertFalse(camera.contains("R.string.camera_error, "))
        assertTrue(camera.contains("Log.w(TAG, \"camera failed (\$code)"))
        assertFalse(updater.contains("R.string.update_install_failed, "))
        assertTrue(updater.contains("Log.w(TAG, \"install failed: status \$status"))
    }
}
