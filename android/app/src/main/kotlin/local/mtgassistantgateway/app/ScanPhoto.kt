package local.mtgassistantgateway.app

import android.content.ContentValues
import android.content.Context
import android.os.Environment
import android.provider.MediaStore
import java.io.IOException
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone

/**
 * "Save this photo" on the camera panel after a scan that found no card: the photo exactly as the
 * page got it, kept in the phone's Pictures › MTG Gateway, so the member can send it to whoever runs
 * the gateway and the miss can be looked into. The JPEG carries a comment with what the page needs
 * to read it the same way again (where the dashed box lay, whether the camera reported focus) and
 * what it came to. Saved through MediaStore, which needs no permission from Android 10 (the app's
 * minSdk) for the app's own pictures. The pure parts are unit tested (ScanPhotoTest).
 */
object ScanPhoto {
    /** The folder under the phone's Pictures. */
    const val FOLDER = "MTG Gateway"
    private const val COM = 0xFE
    private const val MAX_NOTE = 4000

    /** The file's name: the time it was taken, in UTC, so two saves never share one. */
    fun fileName(millis: Long): String {
        val f = SimpleDateFormat("yyyyMMdd-HHmmss-SSS", Locale.US)
        f.timeZone = TimeZone.getTimeZone("UTC")
        return "mtg-scan-" + f.format(Date(millis)) + ".jpg"
    }

    /** The comment the photo carries: one line per fact, printable ASCII only, at most [MAX_NOTE] bytes. */
    fun note(facts: List<Pair<String, String>>): String =
        facts.joinToString("\n") { (k, v) -> "$k: ${v.replace(Regex("[^\\x20-\\x7E]"), "?")}" }.take(MAX_NOTE)

    /**
     * [jpeg] with [note] as a JPEG comment (a COM segment), placed after the leading APPn segments
     * so the EXIF block, and with it the photo's orientation, stays where readers look for it. Not
     * a JPEG (no start marker), or a malformed header: [jpeg] as it is.
     */
    fun withNote(jpeg: ByteArray, note: String): ByteArray {
        if (jpeg.size < 4 || u(jpeg, 0) != 0xFF || u(jpeg, 1) != 0xD8) return jpeg
        var at = 2
        while (at + 4 <= jpeg.size && u(jpeg, at) == 0xFF && u(jpeg, at + 1) in 0xE0..0xEF) {
            val len = (u(jpeg, at + 2) shl 8) or u(jpeg, at + 3)
            if (len < 2 || at + 2 + len > jpeg.size) return jpeg
            at += 2 + len
        }
        val text = note.toByteArray(Charsets.US_ASCII)
        val len = text.size + 2
        val seg = byteArrayOf(0xFF.toByte(), COM.toByte(), (len shr 8).toByte(), (len and 0xFF).toByte()) + text
        return jpeg.copyOfRange(0, at) + seg + jpeg.copyOfRange(at, jpeg.size)
    }

    /** Saves [jpeg] as [name] in Pictures › [FOLDER]. Slow: never on the main thread. */
    @Throws(IOException::class)
    fun save(context: Context, jpeg: ByteArray, name: String) {
        val resolver = context.contentResolver
        val values = ContentValues().apply {
            put(MediaStore.Images.Media.DISPLAY_NAME, name)
            put(MediaStore.Images.Media.MIME_TYPE, "image/jpeg")
            put(MediaStore.Images.Media.RELATIVE_PATH, Environment.DIRECTORY_PICTURES + "/" + FOLDER)
            put(MediaStore.Images.Media.IS_PENDING, 1)
        }
        val item = resolver.insert(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, values)
            ?: throw IOException("no MediaStore entry")
        try {
            val out = resolver.openOutputStream(item) ?: throw IOException("no output stream")
            out.use { it.write(jpeg) }
            values.clear()
            values.put(MediaStore.Images.Media.IS_PENDING, 0)
            resolver.update(item, values, null, null)
        } catch (e: IOException) {
            resolver.delete(item, null, null)
            throw e
        } catch (e: RuntimeException) { // a MediaStore that refuses the write
            resolver.delete(item, null, null)
            throw IOException(e)
        }
    }

    private fun u(b: ByteArray, i: Int): Int = b[i].toInt() and 0xFF
}
