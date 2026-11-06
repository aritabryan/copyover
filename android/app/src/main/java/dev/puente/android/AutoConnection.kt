package dev.puente.android

import android.content.Context
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class AutoConnection(context: Context, operationId: String? = null) {
    private val store = ConnectionStore(context)
    private val discovery = PcDiscovery(context)
    private val repository = TransferRepository(context, operationId)

    suspend fun current(): Connection {
        val saved = withContext(Dispatchers.IO) { store.load() }
        val resolved = resolve(saved)
        withContext(Dispatchers.IO) { store.rememberAddress(resolved.server, PcCatalog.identity(saved)) }
        return resolved
    }

    suspend fun accept(scanned: Connection): Connection {
        val resolved = resolve(scanned)
        // Keep the stable hostname and the verified current address.
        val connection = scanned.copy(lastAddress = resolved.server)
        withContext(Dispatchers.IO) { store.save(connection) }
        return connection
    }

    suspend fun resolve(connection: Connection): Connection = ConnectionSelection.choose(connection,
        discover = { discovery.find(it) },
        probe = { candidate ->
            withContext(Dispatchers.IO) {
                val verified = if (candidate.pcId.isNotEmpty()) {
                    val address = requireNotNull(repository.verifyIdentity(candidate).address)
                    candidate.copy(server = address, lastAddress = address)
                } else candidate
                repository.ping(verified)
                verified
            }
        })
}
