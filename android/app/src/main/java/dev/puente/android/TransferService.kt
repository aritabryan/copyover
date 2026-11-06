package dev.puente.android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import androidx.core.content.ContextCompat
import kotlinx.coroutines.*
import org.json.JSONObject
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap

class TransferService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var operation: String? = null
    override fun onBind(intent: Intent?): IBinder? = null
    override fun onCreate() { super.onCreate(); TransferRecords.initialize(this) }
    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == CANCEL) { cancel(); if (operation == null) stopSelf(); return START_NOT_STICKY }
        val id = intent?.getStringExtra("id") ?: return START_NOT_STICKY
        val task = tasks.remove(id) ?: run { stopSelf(); return START_NOT_STICKY }
        operation = id
        getSystemService(NotificationManager::class.java).createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.channel_transfers), NotificationManager.IMPORTANCE_LOW))
        startForeground(410, notification(getString(R.string.preparing_transfer)))
        TransferRecords.update(this, TransferState(busy = true, operationId = id, message = getString(R.string.preparing)))
        scope.launch { runTransfer(id, task) }
        return START_NOT_STICKY
    }
    private suspend fun runTransfer(id: String, task: Task) = coroutineScope {
        var unconfirmedFile: ReceivedFile? = null
        val direction = if (task.outgoing != null) SyncDirection.TO_PC else SyncDirection.FROM_PC
        val status = SyncStatusStore.get(this@TransferService)
        status.update(task.connection.pcId, direction, SyncPhase.SEARCHING)
        val poll = launch { pollProgress(id) }
        try {
            val connection = AutoConnection(this@TransferService, id).resolve(task.connection)
            status.verified(task.connection.pcId, direction)
            check(!isCancelled(id)) { getString(R.string.transfer_cancelled) }
            status.update(task.connection.pcId, direction, SyncPhase.TRANSFERRING)
            val repository = TransferRepository(this@TransferService, id)
            val offerId = task.offerId ?: if (task.outgoing == null) repository.checkOffer(connection)?.getString("id") else null
            val result = when {
                offerId != null -> repository.downloadOffer(connection, offerId).also {
                    unconfirmedFile = it.file
                    check(!isCancelled(id)) { getString(R.string.transfer_cancelled) }
                    TransferRepository(this@TransferService).confirmOffer(connection, offerId)
                    AndroidOfferMonitor.get(this@TransferService).markDelivered(connection.pcId, offerId)
                    unconfirmedFile = null
                }
                task.outgoing != null -> repository.send(connection, task.outgoing)
                else -> receiveWithRetries { repository.receive(connection) }
            }
            recordDelivery(task, result, direction)
            runCatching { recordHistory(task, result) }
            withContext(NonCancellable) { poll.cancelAndJoin() }
            TransferRecords.update(this@TransferService, TransferState(finished = true, message = result.message, result = result))
        } catch (error: Exception) {
            withContext(NonCancellable) { poll.cancelAndJoin() }
            unconfirmedFile?.file?.let { it.delete(); it.parentFile?.delete() }
            val reason = describe(error, R.string.transfer_interrupted)
            status.update(task.connection.pcId, direction, SyncPhase.ERROR, reason)
            TransferRecords.update(this@TransferService, TransferState(finished = true, failed = true, message = reason))
        } catch (_: UnsatisfiedLinkError) {
            withContext(NonCancellable) { poll.cancelAndJoin() }
            TransferRecords.update(this@TransferService, TransferState(finished = true, failed = true, message = getString(R.string.reinstall_to_complete)))
        } finally {
            poll.cancel(); cancelled.remove(id); releaseStageStream(id); activePcId = null; operation = null
            stopForeground(STOP_FOREGROUND_REMOVE)
            val latest = TransferRecords.state.value
            val open = PendingIntent.getActivity(this@TransferService, 412,
                Intent(this@TransferService, MainActivity::class.java).putExtra("tab", "files"), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
            val notification = Notification.Builder(this@TransferService, CHANNEL).setSmallIcon(R.drawable.ic_bridge)
                .setContentTitle(getString(R.string.notif_title_transfer)).setContentText(latest.message)
                .setContentIntent(open).setAutoCancel(true).build()
            runCatching { getSystemService(NotificationManager::class.java).notify(411, notification) }
            stopSelf()
        }
    }

    private fun recordDelivery(task: Task, result: TransferResult, direction: SyncDirection) {
        val status = SyncStatusStore.get(this)
        val pc = PcCatalog.identity(task.connection)
        if (result.text != null || result.file != null || task.outgoing != null) {
            val kind = if (result.file != null || task.outgoing?.uris?.isNotEmpty() == true) "file" else "text"
            status.delivered(pc, direction, kind)
        } else status.update(pc, direction, SyncPhase.WAITING)
    }

    private suspend fun recordHistory(task: Task, result: TransferResult) {
        val history = HistoryStore.get(this)
        if (task.outgoing?.sensitive != true) {
            task.outgoing?.text?.takeIf { task.outgoing.uris.isEmpty() }?.let { history.recordText(it) }
        }
        result.text?.let { history.recordText(it) }
        result.file?.takeIf { it.mime.startsWith("image/") }?.let { history.recordImage(it.file, it.mime) }
    }

    private suspend fun pollProgress(id: String) = coroutineScope {
        var last = 0L
        var speed = 0L
        while (isActive) {
            delay(300)
            runCatching { JSONObject(NativeBridge.progress(id)) }.getOrNull()?.let { progress ->
                val bytes = progress.optLong("bytes")
                speed = (speed * 3 + ((bytes - last).coerceAtLeast(0) * 1000 / 300)) / 4
                last = bytes
                TransferRecords.progress(this@TransferService, id, bytes, progress.optLong("total"), speed, getString(R.string.transferring))
                runCatching { getSystemService(NotificationManager::class.java).notify(410, notification(getString(R.string.transferred_bytes, fileSize(bytes)))) }
            }
        }
    }

    private fun cancel() { cancel(this) }
    override fun onDestroy() { cancel(); scope.cancel(); super.onDestroy() }
    private fun notification(message: String): Notification {
        val cancel = PendingIntent.getService(this, 410, Intent(this, TransferService::class.java).setAction(CANCEL), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val open = PendingIntent.getActivity(this, 411, Intent(this, MainActivity::class.java), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return Notification.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_bridge).setContentTitle(getString(R.string.notif_title_files)).setContentText(message).setContentIntent(open).setOngoing(true).addAction(Notification.Action.Builder(null, getString(R.string.cancel), cancel).build()).build()
    }
    companion object {
        private const val CHANNEL = "puente_transfers"
        private const val CANCEL = "dev.puente.android.CANCEL_TRANSFER"
        private data class Task(val connection: Connection, val outgoing: Outgoing?, val offerId: String?)
        private val tasks = ConcurrentHashMap<String, Task>()
        private val cancelled = ConcurrentHashMap.newKeySet<String>()
        private val stagingStreams = ConcurrentHashMap<String, java.io.Closeable>()
        fun stageStream(id: String, stream: java.io.Closeable) {
            stagingStreams[id] = stream
            if (isCancelled(id)) { runCatching { stream.close() }; throw LocalizedException(R.string.transfer_cancelled) }
        }
        fun releaseStageStream(id: String) { stagingStreams.remove(id) }
        @Volatile var activePcId: String? = null
            private set
        fun isCancelled(id: String): Boolean = cancelled.contains(id)
        fun cancelForPc(context: Context, pcId: String) { if (activePcId == pcId) cancel(context) }
        @Synchronized fun start(context: Context, connection: Connection, outgoing: Outgoing? = null, offerId: String? = null) {
            TransferRecords.initialize(context)
            if (TransferRecords.state.value.busy) return
            val id = UUID.randomUUID().toString()
            TransferRecords.update(context, TransferState(busy = true, operationId = id, message = context.getString(R.string.preparing)))
            tasks[id] = Task(connection, outgoing, offerId)
            activePcId = connection.pcId
            try {
                ContextCompat.startForegroundService(context, Intent(context, TransferService::class.java).putExtra("id", id))
            } catch (_: Exception) {
                tasks.remove(id); activePcId = null
                TransferRecords.update(context, TransferState(finished = true, failed = true, message = context.getString(R.string.transfer_start_failed)))
            }
        }
        fun cancel(context: Context) {
            val id = TransferRecords.state.value.operationId ?: return
            if (!TransferRecords.state.value.busy) return
            cancelled.add(id)
            stagingStreams.remove(id)?.let { runCatching { it.close() } }
            runCatching { NativeBridge.cancel(id) }
        }
    }
}
