package dev.puente.android

import kotlinx.coroutines.CancellationException

data class DiscoveredPc(val id: String, val name: String, val address: String)

object DiscoveryPolicy {
    /** The PC announces itself as «Puente <name>»; «CopyOver <name>» is accepted too so the PC can switch later
     *  without phones on this version losing it. */
    private val prefixes = listOf("CopyOver ", "Puente ")

    fun pcName(serviceName: String): String? =
        prefixes.firstOrNull { serviceName.startsWith(it) }?.let { serviceName.removePrefix(it) }?.takeIf { it.isNotBlank() }

    fun accepts(app: String?, id: String?): Boolean =
        app == "puente-portapapeles" && id?.matches(Regex("[a-f0-9]{32}")) == true

    fun matches(app: String?, id: String?, linkedId: String): Boolean =
        linkedId.isNotEmpty() && app == "puente-portapapeles" && id == linkedId

    fun address(host: String, port: Int): String {
        val escaped = host.replace("%", "%25")
        return if (host.contains(':')) "http://[$escaped]:$port" else "http://$escaped:$port"
    }
}

/** Probe addresses first; never retry a send that may already have reached Windows. */
object ConnectionSelection {
    suspend fun choose(saved: Connection, discover: suspend (String) -> String?,
                       probe: suspend (Connection) -> Connection): Connection {
        if (saved.key.isEmpty()) throw LocalizedException(R.string.link_pc_to_start)
        val discovered = if (saved.pcId.isNotEmpty()) discover(saved.pcId) else null
        val addresses = listOfNotNull(discovered, saved.server, saved.lastAddress)
            .filter { it.isNotBlank() }.distinct()
        var failure: Exception? = null
        for (address in addresses) {
            val candidate = saved.copy(server = address, lastAddress = address)
            try {
                return probe(candidate)
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (error: Exception) {
                if (failure == null || !ReceivePolicy.needsReview(failure)) failure = error
            }
        }
        throw failure ?: LocalizedException(R.string.pc_not_found)
    }
}
