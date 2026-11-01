package local.mtgassistantgateway.app

import android.content.Context
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.net.Uri
import android.provider.OpenableColumns
import android.util.Log
import java.io.File
import java.io.FileOutputStream
import java.io.IOException

/**
 * What the app hands the page after the phone's file picker ([MainActivity]'s onShowFileChooser):
 * every file picked when the page's input takes several (the picker answers with a ClipData), and
 * a HEIC or HEIF photo (Samsung's camera saves them when "High efficiency pictures" is on) as a JPEG,
 * which the page can read. The pure parts are unit tested (FilePickTest).
 */
object FilePick {
    private const val TAG = "MtgFilePick"
    /** A photo's longest side after the change: plenty for the scanner, light enough to upload. */
    const val MAX_SIDE = 4096
    private val HEIF_MIME = setOf("image/heic", "image/heif", "image/heic-sequence", "image/heif-sequence")
    private val HEIF_NAME = Regex("""\.(heic|heif|hif)$""", RegexOption.IGNORE_CASE)

    /**
     * The files picked: every one in [clip] when the input takes several ([multiple]), else the
     * first of them; the single answer [single] when the picker gave no list.
     */
    fun <T> picked(single: T?, clip: List<T>, multiple: Boolean): List<T> = when {
        clip.isNotEmpty() -> if (multiple) clip else clip.take(1)
        single != null -> listOf(single)
        else -> emptyList()
    }

    /** True for a HEIC or HEIF photo, by its type or, when the type is missing or vague, its name. */
    fun isHeif(mime: String?, name: String?): Boolean {
        val m = mime?.lowercase()?.substringBefore(';')?.trim()
        if (m != null && m in HEIF_MIME) return true
        if (m != null && m.isNotEmpty() && m != "application/octet-stream" && m != "image/*") return false
        return name != null && HEIF_NAME.containsMatchIn(name)
    }

    /** The JPEG's name: the photo's own with .jpg in place of .heic (only safe characters kept). */
    fun jpegName(name: String?): String {
        val base = (name ?: "").substringAfterLast('/').replace(HEIF_NAME, "")
            .replace(Regex("[^A-Za-z0-9._ -]"), "_").trim().take(80)
        return (base.ifEmpty { "photo" }) + ".jpg"
    }

    /** How much smaller to decode a [width] x [height] photo so its longest side fits [MAX_SIDE]. */
    fun sampleSize(width: Int, height: Int): Int {
        var n = 1
        while (maxOf(width, height) / n > MAX_SIDE) n *= 2
        return n
    }

    /**
     * [uris] as the page gets them: HEIC and HEIF photos changed to JPEG files in the app's cache,
     * every other file as it is. A photo that can't be changed is passed on as it is (the page then
     * says it can't open it). Slow: never on the main thread.
     */
    fun forPage(context: Context, uris: List<Uri>): Array<Uri> {
        val dir = File(context.cacheDir, "picked")
        dir.deleteRecursively() // the last pick's files: the page has read them
        return uris.mapIndexed { i, uri ->
            val name = displayName(context, uri)
            if (!isHeif(context.contentResolver.getType(uri), name)) return@mapIndexed uri
            try {
                val bitmap = ImageDecoder.decodeBitmap(ImageDecoder.createSource(context.contentResolver, uri)) { decoder, info, _ ->
                    decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                    decoder.setTargetSampleSize(sampleSize(info.size.width, info.size.height))
                }
                // one folder each, so two photos with the same name both reach the page
                val out = File(File(dir, i.toString()).apply { mkdirs() }, jpegName(name))
                try {
                    FileOutputStream(out).use { bitmap.compress(Bitmap.CompressFormat.JPEG, 90, it) }
                } finally {
                    bitmap.recycle()
                }
                Uri.fromFile(out)
            } catch (e: IOException) {
                Log.w(TAG, "HEIC not changed: ${e.javaClass.simpleName}")
                uri
            } catch (e: RuntimeException) { // a decoder that doesn't know the file
                Log.w(TAG, "HEIC not changed: ${e.javaClass.simpleName}")
                uri
            }
        }.toTypedArray()
    }

    private fun displayName(context: Context, uri: Uri): String? = try {
        context.contentResolver.query(uri, arrayOf(OpenableColumns.DISPLAY_NAME), null, null, null)?.use { c ->
            if (c.moveToFirst()) c.getString(0) else null
        } ?: uri.lastPathSegment
    } catch (e: RuntimeException) {
        uri.lastPathSegment
    }
}
