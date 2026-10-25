package local.mtgassistantgateway.app

import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.graphics.drawable.Icon
import android.net.Uri
import android.os.Build
import android.service.chooser.ChooserAction
import android.widget.Toast

/**
 * The deck page's Share: the phone's own share sheet, never one of the app's making. What is shared
 * is the deck's Archidekt link, which anyone can open (the gateway's own link opens only for its
 * members), with the deck's name as the sheet's title. From Android 14 the sheet also offers the
 * deck's other ways out as its own actions: Open on Archidekt, Copy gateway link and Copy deck list
 * (the list as EDHPlay and most sites import it). Older Androids show the plain sheet, the gateway
 * link added to the shared text.
 */
object DeckShare {
    private const val EXTRA_COPY = "local.mtgassistantgateway.app.COPY"
    private const val EXTRA_SAID = "local.mtgassistantgateway.app.SAID"

    fun open(context: Context, title: String?, archidektUrl: String?, gatewayUrl: String?, deckList: String?) {
        val link = archidektUrl?.takeIf { it.startsWith("https://archidekt.com/") } ?: return
        val name = title?.take(200).orEmpty()
        // Before Android 14 the sheet has no actions of its own, so the gateway link (the old Copy
        // link) goes in the text, after the link anyone can open
        val older = Build.VERSION.SDK_INT < Build.VERSION_CODES.UPSIDE_DOWN_CAKE
        val text = shareText(name, link, if (older) gatewayUrl else null)
        val send = Intent(Intent.ACTION_SEND).apply {
            type = "text/plain"
            putExtra(Intent.EXTRA_TEXT, text)
            if (name.isNotEmpty()) putExtra(Intent.EXTRA_TITLE, name)
        }
        val chooser = Intent.createChooser(send, name.ifEmpty { null })
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            val actions = mutableListOf(
                ChooserAction.Builder(
                    Icon.createWithResource(context, R.drawable.ic_share_open),
                    context.getString(R.string.share_open_archidekt),
                    PendingIntent.getActivity(
                        context, 1,
                        Intent(Intent.ACTION_VIEW, Uri.parse(link)).addCategory(Intent.CATEGORY_BROWSABLE),
                        PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
                    ),
                ).build(),
            )
            if (!gatewayUrl.isNullOrEmpty()) {
                actions += copyAction(context, 2, R.drawable.ic_share_copy, R.string.share_copy_gateway,
                    gatewayUrl, R.string.share_copied_gateway)
            }
            if (!deckList.isNullOrEmpty()) {
                actions += copyAction(context, 3, R.drawable.ic_share_list, R.string.share_copy_list,
                    deckList, R.string.share_copied_list)
            }
            chooser.putExtra(Intent.EXTRA_CHOOSER_CUSTOM_ACTIONS, actions.toTypedArray())
        }
        context.startActivity(chooser)
    }

    /** The shared text: the deck's name, its Archidekt link and, when given, its gateway link. */
    fun shareText(name: String, link: String, gatewayUrl: String?): String = buildString {
        if (name.isNotEmpty()) append(name).append('\n')
        append(link)
        if (!gatewayUrl.isNullOrEmpty()) append("\n\nOn the gateway (members): ").append(gatewayUrl)
    }

    private fun copyAction(context: Context, code: Int, iconRes: Int, label: Int, text: String, said: Int): ChooserAction {
        val copy = Intent(context, CopyReceiver::class.java)
            .putExtra(EXTRA_COPY, text)
            .putExtra(EXTRA_SAID, context.getString(said))
        return ChooserAction.Builder(
            Icon.createWithResource(context, iconRes),
            context.getString(label),
            PendingIntent.getBroadcast(
                context, code, copy, PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
            ),
        ).build()
    }

    /** Copies what a sheet action carries. Android 13 and later confirm a copy on their own. */
    class CopyReceiver : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) {
            val text = intent.getStringExtra(EXTRA_COPY) ?: return
            val clip = context.getSystemService(ClipboardManager::class.java) ?: return
            clip.setPrimaryClip(ClipData.newPlainText("MTG Gateway", text))
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) {
                Toast.makeText(context, intent.getStringExtra(EXTRA_SAID).orEmpty(), Toast.LENGTH_SHORT).show()
            }
        }
    }
}
