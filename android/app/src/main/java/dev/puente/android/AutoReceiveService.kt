package dev.puente.android

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.os.Build
import android.os.IBinder
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.security.MessageDigest
import java.io.File

/** Runs PC reception and, when explicitly selected, the optional Shizuku sender. */
class AutoReceiveService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Main.immediate)
    private var worker: Job? = null
    private var sender: ShizukuAutoSender? = null
    private var offers: Job? = null
    private var boundIdentity = ""
    private val store by lazy { ConnectionStore(this) }
    private val repository by lazy { TransferRepository(this) }
    private val preferences by lazy { getSharedPreferences("receive_cursor", MODE_PRIVATE) }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, getString(R.string.channel_auto_receive), NotificationManager.IMPORTANCE_LOW))
        ServiceCompat.startForeground(this, NOTIFICATION, notification(getString(R.string.status_searching_pc)),
            if (Build.VERSION.SDK_INT >= 29) ServiceInfo.FOREGROUND_SERVICE_TYPE_CONNECTED_DEVICE else 0)
        scope.launch {
            while (isActive) {
                runCatching { HistoryStore.get(this@AutoReceiveService).refresh() }
                delay(60 * 60 * 1000L)
            }
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == TURN_OFF) { store.automaticReceive = false; store.shizukuSend = false; store.fileOffers = false }
        applyDisabledFeatures(this, store)
        val saved = store.load()
        resetForConnection(fingerprint(saved))
        if (!shouldRun(saved)) {
            stopSelf()
            return START_NOT_STICKY
        }
        configureReceiver(saved)
        configureSender(saved)
        configureOffers()
        return START_STICKY
    }

    private fun shouldRun(saved: Connection): Boolean =
        saved.key.isNotEmpty() && (store.automaticReceive || store.shizukuSend || store.fileOffers)

    private fun resetForConnection(identity: String) {
        if (identity != boundIdentity) {
            worker?.cancel(); worker = null
            sender?.close(); sender = null
            offers?.cancel(); offers = null
            AndroidOfferMonitor.get(this).clearPending()
            boundIdentity = identity
        }
    }

    private fun configureReceiver(saved: Connection) {
        if (store.automaticReceive) {
            if (worker?.isActive != true) worker = scope.launch { receiveLoop() }
        } else { worker?.cancel(); worker = null }
        if (!store.automaticReceive) SyncStatusStore.get(this@AutoReceiveService).update(PcCatalog.identity(saved), SyncDirection.FROM_PC, SyncPhase.DISABLED)
    }

    private fun configureSender(saved: Connection) {
        if (store.shizukuSend) {
            if (sender == null) sender = ShizukuAutoSender(this, ::updateNotification).also { it.start() }
        } else { sender?.close(); sender = null }
        if (!store.shizukuSend) SyncStatusStore.get(this@AutoReceiveService).update(PcCatalog.identity(saved), SyncDirection.TO_PC, SyncPhase.DISABLED)
    }

    private fun configureOffers() {
        if (store.fileOffers) {
            if (offers?.isActive != true) offers = scope.launch { offerLoop() }
        } else { offers?.cancel(); offers = null; AndroidOfferMonitor.get(this).clearPending() }
    }

    private suspend fun offerLoop() {
        val auto = AutoConnection(this)
        val monitor = AndroidOfferMonitor.get(this)
        while (scope.isActive && store.fileOffers) {
            try {
                val saved = store.load()
                val connection = auto.resolve(saved)
                withContext(Dispatchers.IO) { monitor.poll(connection, repository) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) {
                monitor.reportError(error)
                if (ReceivePolicy.needsReview(error)) return
            }
            catch (_: UnsatisfiedLinkError) { monitor.reportError(IllegalStateException(getString(R.string.reinstall_for_files))); return }
            delay(10000)
        }
    }

    private suspend fun receiveLoop() {
        val auto = AutoConnection(this)
        var connection: Connection? = null
        var failures = 0
        while (scope.isActive && store.automaticReceive) {
            try {
                if (connection == null) SyncStatusStore.get(this@AutoReceiveService).update(PcCatalog.identity(store.load()), SyncDirection.FROM_PC, SyncPhase.SEARCHING)
                connection = verifiedConnection(auto, connection) ?: return
                receiveChange(connection)
                failures = 0
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (_: UnsatisfiedLinkError) {
                updateNotification(getString(R.string.reinstall_for_auto_receive))
                stopSelf()
                return
            } catch (error: Exception) {
                SyncStatusStore.get(this@AutoReceiveService).update(PcCatalog.identity(store.load()), SyncDirection.FROM_PC, SyncPhase.ERROR, describe(error, R.string.receive_failed))
                connection = null
                updateNotification(describe(error, R.string.status_searching_pc))
                if (ReceivePolicy.needsReview(error)) return
                delay(ReceivePolicy.backoff(++failures))
            }
        }
    }

    private suspend fun verifiedConnection(auto: AutoConnection, cached: Connection?): Connection? {
        val saved = withContext(Dispatchers.IO) { store.load() }
        if (saved.key.isEmpty()) { stopSelf(); return null }
        val resolved = if (cached?.pcId == saved.pcId && cached.key == saved.key) cached else auto.current()
        // Recheck identity before sending the key, even after a previous long poll succeeded.
        return withContext(Dispatchers.IO) {
            require(resolved.pcId.isNotEmpty()) { getString(R.string.relink_for_auto_receive) }
            val address = requireNotNull(repository.verifyIdentity(resolved).address)
            SyncStatusStore.get(this@AutoReceiveService).verified(PcCatalog.identity(saved), SyncDirection.FROM_PC)
            resolved.copy(server = address)
        }
    }

    private suspend fun receiveChange(connection: Connection) {
        val linkIdentity = fingerprint(connection)
        val cursor = cursor(linkIdentity)
        updateNotification(getString(R.string.receiving_from, connection.name))
        SyncStatusStore.get(this@AutoReceiveService).update(PcCatalog.identity(connection), SyncDirection.FROM_PC, SyncPhase.WAITING)
        var downloaded: File? = null
        ClipboardDelivery { downloaded?.let(::discardImage) }.use { delivery ->
            val result = withContext(Dispatchers.IO) {
                repository.receiveAutomatic(connection, cursor.token).also { downloaded = it.content.file?.file }
            }
            val content = result.content
            val current = withContext(Dispatchers.IO) { store.load() }
            // A PC switch or turning it off while waiting must not apply the previous PC's clipboard.
            if (!store.automaticReceive || fingerprint(current) != linkIdentity) return
            val supported = content.text?.let(AutomaticClipboardPolicy::canCopyText)
                ?: (content.file?.mime?.startsWith("image/") == true)
            val isNew = result.cursor != cursor.token && supported
            cursor.accept(result.cursor, supported) {
                delivery.copy { copy(content) }
                content.file?.let { rememberImage(it.file) }
            }
            if (isNew) {
                SyncStatusStore.get(this@AutoReceiveService).delivered(PcCatalog.identity(connection), SyncDirection.FROM_PC, if (content.text != null) "text" else "image")
                runCatching {
                    content.text?.let { HistoryStore.get(this).recordText(it) }
                    content.file?.let { HistoryStore.get(this).recordImage(it.file, it.mime) }
                }
            }
        }
    }

    private fun discardImage(file: File) {
        val root = File(cacheDir, "received").canonicalPath + File.separator
        if (!file.canonicalPath.startsWith(root)) return
        file.delete()
        file.parentFile?.delete()
    }

    private fun rememberImage(file: File) {
        val current = preferences.getString("image", null)
        val previous = preferences.getString("previous_image", null)
        check(preferences.edit().putString("image", file.absolutePath).putString("previous_image", current).commit()) {
            getString(R.string.remember_image_failed)
        }
        // Retain current and previous URIs so an app already reading the last image can finish.
        if (previous != null && previous != current && previous != file.absolutePath) discardImage(File(previous))
    }

    private fun cursor(fingerprint: String): SyncCursor {
        val initial = preferences.getString("cursor:$fingerprint", null)
            ?: if (preferences.getString("pc", "") == fingerprint) preferences.getString("cursor", "").orEmpty() else ""
        return SyncCursor(initial) { token ->
            check(preferences.edit().putString("cursor:$fingerprint", token).commit()) {
                getString(R.string.remember_copy_failed)
            }
        }
    }

    private fun fingerprint(connection: Connection): String = MessageDigest.getInstance("SHA-256")
        .digest("${connection.pcId}:${connection.key}".toByteArray(Charsets.UTF_8))
        .joinToString("") { "%02x".format(it) }

    private fun copy(content: TransferResult) {
        val clipboard = getSystemService(ClipboardManager::class.java)
        if (content.text != null) {
            clipboard.setPrimaryClip(PcClipboard.mark(ClipData.newPlainText(getString(R.string.clip_label_from_pc), content.text)))
            return
        }
        val image = content.file ?: return
        require(image.mime.startsWith("image/")) { getString(R.string.auto_content_unsupported) }
        val uri = FileProvider.getUriForFile(this, "$packageName.files", image.file)
        clipboard.setPrimaryClip(PcClipboard.mark(ClipData.newUri(contentResolver, getString(R.string.clip_label_from_pc), uri)))
    }

    private fun notification(text: String): Notification {
        val open = PendingIntent.getActivity(this, 20, Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val turnOff = PendingIntent.getService(this, 21, Intent(this, AutoReceiveService::class.java).setAction(TURN_OFF),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        return NotificationCompat.Builder(this, CHANNEL).setSmallIcon(R.drawable.ic_bridge)
            .setContentTitle(getString(if (store.shizukuSend) R.string.notif_title_auto else R.string.notif_title_pc_to_phone)).setContentText(text)
            .setContentIntent(open).setOngoing(true).setSilent(true).setOnlyAlertOnce(true)
            .addAction(0, getString(R.string.turn_off), turnOff).build()
    }

    private var shownStatus = ""
    private fun updateNotification(text: String) {
        if (text == shownStatus) return
        shownStatus = text
        try { getSystemService(NotificationManager::class.java).notify(NOTIFICATION, notification(text)) }
        catch (_: SecurityException) { /* The foreground service remains visible in Android's task manager. */ }
    }

    override fun onDestroy() {
        sender?.close()
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        private const val CHANNEL = "automatic_receive"
        private const val NOTIFICATION = 2
        // Turns off every automatic feature this service runs; the user re-enables them in Ajustes.
        private const val TURN_OFF = "dev.puente.android.PAUSE_RECEIVE"

        fun refresh(context: Context) {
            val store = ConnectionStore(context)
            applyDisabledFeatures(context, store)
            val intent = Intent(context, AutoReceiveService::class.java)
            if ((store.automaticReceive || store.shizukuSend || store.fileOffers) && store.load().key.isNotEmpty()) ContextCompat.startForegroundService(context, intent)
            else context.stopService(intent)
        }

        private fun applyDisabledFeatures(context: Context, store: ConnectionStore) {
            if (!store.fileOffers) AndroidOfferMonitor.get(context).clearPending()
            val pcId = PcCatalog.identity(store.load())
            if (!store.automaticReceive) SyncStatusStore.get(context).update(pcId, SyncDirection.FROM_PC, SyncPhase.DISABLED)
            if (!store.shizukuSend) SyncStatusStore.get(context).update(pcId, SyncDirection.TO_PC, SyncPhase.DISABLED)
        }
    }
}
