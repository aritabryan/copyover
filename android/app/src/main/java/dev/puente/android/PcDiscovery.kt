package dev.puente.android

import android.content.Context
import android.net.nsd.NsdManager
import android.net.nsd.NsdServiceInfo
import android.os.Handler
import android.os.Looper
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlin.coroutines.resume

/** Uses the system mDNS stack. Each search owns and closes its listeners. */
class PcDiscovery(context: Context) {
    private val manager = context.getSystemService(NsdManager::class.java)
    private val handler = Handler(Looper.getMainLooper())

    suspend fun scan(): List<DiscoveredPc> = withContext(Dispatchers.Main.immediate) {
        val found = linkedMapOf<String, DiscoveredPc>()
        withTimeoutOrNull(6000) {
            suspendCancellableCoroutine<Unit> { continuation ->
                val search = Search(null, { if (continuation.isActive) continuation.resume(Unit) },
                    { pc -> found[pc.id] = pc })
                continuation.invokeOnCancellation { handler.post { search.close() } }
                search.start()
            }
        }
        found.values.sortedBy { it.name }
    }

    suspend fun find(linkedId: String): String? = withContext(Dispatchers.Main.immediate) {
        withTimeoutOrNull(6000) {
            suspendCancellableCoroutine { continuation ->
                val search = Search(linkedId, { address ->
                    if (continuation.isActive) continuation.resume(address)
                })
                continuation.invokeOnCancellation { handler.post { search.close() } }
                search.start()
            }
        }
    }

    @Suppress("DEPRECATION") // API 26 compatibility; newer APIs require Android extension checks.
    private inner class Search(private val linkedId: String?, private val result: (String?) -> Unit,
        private val discovered: (DiscoveredPc) -> Unit = {}) {
        private var active = true
        private var started = false
        private var resolving = false
        private val pending = ArrayDeque<NsdServiceInfo>()
        private val seen = mutableSetOf<String>()

        private val listener = object : NsdManager.DiscoveryListener {
            override fun onDiscoveryStarted(type: String) = Unit
            override fun onDiscoveryStopped(type: String) = Unit
            override fun onStopDiscoveryFailed(type: String, code: Int) = Unit
            override fun onStartDiscoveryFailed(type: String, code: Int) {
                handler.post { finish(null) }
            }
            override fun onServiceLost(service: NsdServiceInfo) {
                handler.post { pending.removeAll { it.serviceName == service.serviceName } }
            }
            override fun onServiceFound(service: NsdServiceInfo) {
                handler.post {
                    if (active && DiscoveryPolicy.pcName(service.serviceName) != null && seen.add(service.serviceName)) {
                        pending.addLast(service)
                        resolveNext()
                    }
                }
            }
        }

        fun start() {
            try {
                manager.discoverServices("_http._tcp.", NsdManager.PROTOCOL_DNS_SD, listener)
                started = true
            } catch (_: Exception) { finish(null) }
        }

        fun close() {
            if (!active) return
            active = false
            pending.clear()
            if (started) runCatching { manager.stopServiceDiscovery(listener) }
        }

        private fun finish(address: String?) {
            if (!active) return
            close()
            result(address)
        }

        private fun resolveNext() {
            if (!active || resolving || pending.isEmpty()) return
            resolving = true
            val service = pending.removeFirst()
            val resolver = object : NsdManager.ResolveListener {
                override fun onResolveFailed(info: NsdServiceInfo, code: Int) {
                    handler.post { resolving = false; resolveNext() }
                }
                override fun onServiceResolved(info: NsdServiceInfo) {
                    handler.post { resolved(info) }
                }
            }
            try { manager.resolveService(service, resolver) }
            catch (_: Exception) { resolving = false; resolveNext() }
        }

        private fun resolved(info: NsdServiceInfo) {
            if (!active) return
            resolving = false
            val attributes = info.attributes
            val app = attributes["app"]?.toString(Charsets.UTF_8)
            val id = attributes["id"]?.toString(Charsets.UTF_8)
            val host = info.host?.hostAddress
            if (!DiscoveryPolicy.accepts(app, id) || host == null || info.port !in 1..65535) {
                resolveNext()
                return
            }
            val name = DiscoveryPolicy.pcName(info.serviceName) ?: info.serviceName
            val pc = DiscoveredPc(requireNotNull(id), name, DiscoveryPolicy.address(host, info.port))
            if (linkedId == null) {
                discovered(pc)
                resolveNext()
            } else if (pc.id == linkedId) finish(pc.address)
            else resolveNext()
        }
    }
}
