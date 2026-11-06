package dev.puente.android

import android.content.ComponentName
import android.content.Context
import android.content.ServiceConnection
import android.os.Bundle
import android.os.IBinder
import android.os.ParcelFileDescriptor
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import rikka.shizuku.Shizuku
import java.io.File
import java.util.UUID

/** Optional reader: the unprivileged app owns network credentials and all transfers. */
class ShizukuAutoSender(private val context: Context, private val status: (String) -> Unit) : AutoCloseable {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private val store = ConnectionStore(context)
    private val repository = TransferRepository(context)
    private val auto = AutoConnection(context)
    private val boundPc = store.load()
    private val pending = MutableStateFlow<Snapshot?>(null)
    private var remote: IClipboardAccess? = null
    private var binding = false
    private var closed = false
    private var monitor: Job? = null
    private var activeOperation: String? = null
    private var pcVerified = false
    private var linkRejected = false
    private val args = Shizuku.UserServiceArgs(ComponentName(context, ShizukuClipboardService::class.java))
        .daemon(false).processNameSuffix("clipboard").tag("clipboard-reader").version(7)
    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            if (closed) { unbind(); return }
            remote = IClipboardAccess.Stub.asInterface(binder)
            binding = true
        }
        override fun onServiceDisconnected(name: ComponentName?) { remote = null; binding = false; replacePending(null) }
    }

    private data class Snapshot(val token: String, val kind: String, val text: String, val mime: String, val pc: Connection, val sensitive: Boolean)

    private fun sync(phase: SyncPhase, error: String = "") {
        SyncStatusStore.get(context).update(PcCatalog.identity(boundPc), SyncDirection.TO_PC, phase, error)
    }
    private fun replacePending(value: Snapshot?) {
        if (pending.value != value) activeOperation?.let { runCatching { NativeBridge.cancel(it) } }
        pending.value = value
    }

    fun start() {
        if (monitor != null) return
        monitor = scope.launch { monitor() }
        scope.launch { pending.collectLatest { value -> if (value != null) sendLatest(value) } }
    }

    private suspend fun monitor() {
        val gate = OutgoingClipboardGate()
        while (scope.isActive && store.shizukuSend) {
            try {
                if (!ShizukuModePolicy.canRun(store.shizukuSend, ShizukuAccess.running(), ShizukuAccess.granted())) {
                    replacePending(null)
                    unbind()
                    status(context.getString(R.string.shizuku_unavailable_status))
                    sync(SyncPhase.ERROR, context.getString(R.string.shizuku_stopped))
                    delay(3000)
                    continue
                }
                if (!binding) { Shizuku.bindUserService(args, connection); binding = true }
                val service = remote
                if (service != null) {
                    val snapshot = withContext(Dispatchers.IO) { service.snapshot() }
                    accept(snapshot, gate)
                }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: Exception) {
                replacePending(null)
                unbind()
                status(context.getString(R.string.shizuku_read_failed))
                sync(SyncPhase.ERROR, context.getString(R.string.shizuku_check_failed))
                delay(3000)
            }
            delay(1000)
        }
    }

    private suspend fun accept(bundle: Bundle, gate: OutgoingClipboardGate) {
        val kind = bundle.getString("kind").orEmpty()
        if (kind == "error") {
            val reason = context.getString(R.string.shizuku_clipboard_unsupported)
            replacePending(null); status(reason); sync(SyncPhase.ERROR, reason); return
        }
        if (kind == "unavailable") { sync(SyncPhase.ERROR, context.getString(R.string.phone_locked)); return }
        if (ReceivePolicy.canShowWaiting(pending.value != null, linkRejected)) sync(if (pcVerified) SyncPhase.WAITING else SyncPhase.READY)
        val fromPc = bundle.getBoolean("from_pc")
        val changed = gate.observe(bundle.getString("token").orEmpty(), fromPc)
        if (ShizukuModePolicy.dropPending(kind, fromPc)) { replacePending(null); return }
        if (!changed) return
        linkRejected = false
        val text = bundle.getString("text").orEmpty()
        val sensitive = bundle.getBoolean("sensitive")
        if (kind == "text") runCatching { HistoryStore.get(context).recordText(text, sensitive) }
        replacePending(Snapshot(bundle.getString("token").orEmpty(), kind,
            text, bundle.getString("mime").orEmpty(), boundPc, sensitive))
    }

    private fun stillCurrent(snapshot: Snapshot): Boolean {
        val saved = store.load()
        return store.shizukuSend && ShizukuAccess.granted() &&
            saved.pcId == snapshot.pc.pcId && saved.key == snapshot.pc.key && pending.value == snapshot
    }

    private suspend fun sendLatest(snapshot: Snapshot) {
        var resolved: Connection? = null
        while (scope.isActive && stillCurrent(snapshot)) {
            try {
                sync(SyncPhase.SEARCHING)
                val candidate = resolved ?: auto.current()
                val verified = withContext(Dispatchers.IO) {
                    require(candidate.pcId.isNotEmpty()) { context.getString(R.string.relink_pc) }
                    candidate.copy(server = requireNotNull(repository.verifyIdentity(candidate).address))
                }
                resolved = verified
                pcVerified = true
                SyncStatusStore.get(context).verified(PcCatalog.identity(snapshot.pc), SyncDirection.TO_PC)
                if (!stillCurrent(snapshot)) return
                val service = remote ?: return
                sync(SyncPhase.TRANSFERRING)
                val operation = UUID.randomUUID().toString()
                activeOperation = operation
                try {
                    withContext(Dispatchers.IO) {
                        if (!stillCurrent(snapshot)) return@withContext
                        val transfer = TransferRepository(context, operation)
                        if (snapshot.kind == "text") transfer.send(verified, Outgoing(text = snapshot.text, sensitive = snapshot.sensitive))
                        else sendImage(service, verified, snapshot, transfer)
                    }
                } finally { if (activeOperation == operation) activeOperation = null }
                if (stillCurrent(snapshot)) {
                    SyncStatusStore.get(context).delivered(PcCatalog.identity(snapshot.pc), SyncDirection.TO_PC, if (snapshot.kind == "text") "text" else "image")
                    status(context.getString(R.string.sent_to, snapshot.pc.name)); replacePending(null)
                }
                return
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: UnsatisfiedLinkError) { status(context.getString(R.string.reinstall_for_shizuku)); return }
            catch (error: Exception) {
                pcVerified = false
                resolved = null
                if (ReceivePolicy.needsReview(error)) {
                    linkRejected = true
                    val reason = context.describe(error, R.string.send_retry)
                    status(reason); sync(SyncPhase.ERROR, reason); replacePending(null)
                    return
                }
                status(context.getString(R.string.waiting_to_send))
                sync(SyncPhase.ERROR, context.getString(R.string.send_retry))
                delay(10000)
            }
        }
    }

    private suspend fun sendImage(service: IClipboardAccess, pc: Connection, snapshot: Snapshot, transfer: TransferRepository) {
        val extension = requireNotNull(ShizukuModePolicy.imageExtension(snapshot.mime)) { context.getString(R.string.image_format_use_send_file) }
        val directory = File(context.cacheDir, "shizuku-outgoing").apply { mkdirs() }
        val file = File.createTempFile("image-", ".$extension", directory)
        try {
            val descriptor = requireNotNull(service.openImage(snapshot.token))
            ParcelFileDescriptor.AutoCloseInputStream(descriptor).use { input ->
                file.outputStream().use { output ->
                    val buffer = ByteArray(65536)
                    var total = 0L
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        total += count
                        require(total <= 32L * 1024 * 1024) { context.getString(R.string.image_too_large) }
                        output.write(buffer, 0, count)
                    }
                }
            }
            runCatching { HistoryStore.get(context).recordImage(file, snapshot.mime, snapshot.sensitive) }
            if (stillCurrent(snapshot)) transfer.sendPreparedImage(pc, file, snapshot.mime)
        } finally { file.delete() }
    }

    private fun unbind() {
        remote = null
        if (binding) runCatching { Shizuku.unbindUserService(args, connection, true) }
        binding = false
    }

    override fun close() {
        closed = true
        replacePending(null)
        scope.cancel()
        unbind()
    }
}
