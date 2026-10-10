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
 * opens the gateway's own page for that deck. Nothing else ever loads inside the app.
 *
 * Pure functions, no Android types, so the unit tests run on a plain JVM.
 */
object SharedLink {
    sealed class Target {
        /** A gateway page to load in the app ([url] is on the gateway's origin). */
        data class Open(val url: String) : Target()
        /** Only links the app does not open ([first] is the first of them). */
        data class Other(val first: String) : Target()
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

    /**
     * Where the shared [texts] (EXTRA_TEXT, then each ClipData item, ...) lead for a gateway at
     * [origin]: the first gateway link, else the first archidekt.com deck link as the gateway's deck
     * page, else [Target.Other] or [Target.None].
     */
    fun resolve(origin: String, texts: List<String?>): Target {
        val urls = texts.flatMap { urlsIn(it) }.distinct()
        if (urls.isEmpty()) return Target.None
        urls.firstOrNull { GatewayUrl.isGateway(origin, it) }?.let { return Target.Open(it) }
        for (u in urls) {
            val id = archidektDeckId(u) ?: continue
            return Target.Open(GatewayUrl.join(origin, "/decks/$id"))
        }
        return Target.Other(urls.first())
    }
}
