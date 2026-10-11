package local.mtgassistantgateway.app

import java.net.URI
import java.net.URISyntaxException

/**
 * What a share into the app (ACTION_SEND) points at.
 *
 * Apps rarely share a bare URL: Discord shares the message text with the link inside it (or a
 * discord.com link to the message), a browser may add the page title, and some apps put the text
 * in ClipData rather than EXTRA_TEXT. So every https URL in every shared text is looked at, and the
 * first one the app can open wins: a page on the gateway, or an archidekt.com deck link, which
 * opens the gateway's own page for that deck. Nothing else ever loads inside the app. The same rules
 * apply to a link copied to the clipboard ("Open a copied link").
 *
 * Pure functions, no Android types, so the unit tests run on a plain JVM.
 */
object SharedLink {
    sealed class Target {
        /** A gateway page to load in the app ([url] is on the gateway's origin). */
        data class Open(val url: String) : Target()
        /** Only links the app does not open ([first] is the first of them). */
        data class Other(val first: String) : Target()
        /** No https link, but a file (EXTRA_STREAM) was shared. */
        object File : Target()
        /** No https link in what was shared. */
        object None : Target()
    }

    // Stops at whitespace and at the characters chat apps wrap links in: <...>, (...), [...], quotes.
    private val URL = Regex("https://[^\\s<>\"'()\\[\\]{}`|\\\\^]+", RegexOption.IGNORE_CASE)
    private const val TRAILING = ".,;:!?*_~"

    /** Every https URL in [text], in order, with trailing sentence punctuation and markdown removed. */
    fun urlsIn(text: String?): List<String> {
        if (text.isNullOrEmpty() || text.length > 100_000) return emptyList()
        return URL.findAll(text).mapNotNull { m ->
            val url = m.value.trimEnd { it in TRAILING }
            val origin = GatewayUrl.originOf(url)
            if (origin != null && origin.startsWith("https://")) url else null
        }.toList()
    }

    /**
     * The Archidekt deck id in an archidekt.com deck link (`https://archidekt.com/decks/123/name`,
     * `www.` or not), or null. Same rule as the gateway's own deck-link reader: digits, at most 12.
     */
    fun archidektDeckId(url: String): String? {
        val uri = try {
            URI(url)
        } catch (e: URISyntaxException) {
            return null
        }
        if (uri.scheme?.lowercase() != "https" || uri.rawUserInfo != null) return null
        if (uri.port != -1 && uri.port != 443) return null
        val host = uri.host?.lowercase() ?: return null
        if (host != "archidekt.com" && host != "www.archidekt.com") return null
        val parts = (uri.path ?: return null).split('/').filter { it.isNotEmpty() }
        if (parts.size < 2 || parts[0] != "decks") return null
        val id = parts[1]
        return if (id.length in 1..12 && id.all { it in '0'..'9' }) id else null
    }

    /** The host of [url] in lower case (`discord.com`), for telling the person what arrived; null if none. */
    fun hostOf(url: String): String? = try {
        URI(url).host?.lowercase()?.takeIf { it.isNotEmpty() }
    } catch (e: URISyntaxException) {
        null
    }

    /**
     * A link to Discord itself (a message link from Discord's own Share), not the link in a message.
     * Discord's Share on a message hands over such a link; the deck link has to be shared or copied
     * on its own.
     */
    fun isDiscord(url: String): Boolean {
        val host = hostOf(url) ?: return false
        return listOf("discord.com", "discordapp.com", "discord.gg").any { host == it || host.endsWith(".$it") }
    }

    /**
     * Where the shared [texts] (EXTRA_TEXT, then each ClipData item, ...) lead for a gateway at
     * [origin]: the first gateway link, else the first archidekt.com deck link as the gateway's deck
     * page, else [Target.Other], or [Target.File] when there was no link but [file] was shared, or
     * [Target.None].
     */
    fun resolve(origin: String, texts: List<String?>, file: Boolean = false): Target {
        val urls = texts.flatMap { urlsIn(it) }.distinct()
        if (urls.isEmpty()) return if (file) Target.File else Target.None
        urls.firstOrNull { GatewayUrl.isGateway(origin, it) }?.let { return Target.Open(it) }
        for (u in urls) {
            val id = archidektDeckId(u) ?: continue
            return Target.Open(GatewayUrl.join(origin, "/decks/$id"))
        }
        return Target.Other(urls.first())
    }
}
