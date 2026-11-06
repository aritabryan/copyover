package dev.puente.android

import android.content.Context
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONArray

data class AndroidFileOffer(
    val id: String,
    val name: String,
    val mime: String,
    val size: Long,
    val pc: String,
    val pcId: String,
)

/** Poll on IO. Offers announce files only; accepting and downloading require a user action. */
class AndroidOfferMonitor(context: Context) {
    private val appContext = context.applicationContext
    private val preferences = context.getSharedPreferences("android_offers", Context.MODE_PRIVATE)
    private val mutablePending = MutableStateFlow<AndroidFileOffer?>(null)
    val pending: StateFlow<AndroidFileOffer?> = mutablePending.asStateFlow()
    private val mutableError = MutableStateFlow<String?>(null)
    val error: StateFlow<String?> = mutableError.asStateFlow()
    private val generation = OfferGeneration()

    fun reportError(error: Throwable) {
        mutableError.value = appContext.describe(error, R.string.offers_check_failed)
    }

    fun poll(connection: Connection, repository: TransferRepository) {
        val epoch = generation.capture()
        if (connection.pcId.isBlank() || connection.key.isBlank()) {
            clearPending()
            return
        }
        val json = repository.checkOffer(connection)
        if (json == null) {
            generation.publish(epoch) { clearPendingInternal(); mutableError.value = null }
            return
        }
        val id = json.getString("id")
        val name = json.getString("name")
        val size = json.getLong("size")
        require(id.matches(Regex("[a-f0-9]{32}"))) { appContext.getString(R.string.offer_invalid) }
        require(name.isNotBlank() && name.length <= 255 && name.none { it == '/' || it == '\\' || it.code < 32 }) {
            appContext.getString(R.string.offer_invalid)
        }
        require(size >= 0) { appContext.getString(R.string.offer_invalid) }
        generation.publish(epoch) {
            val active = ConnectionStore(appContext).load()
            if (active.pcId == connection.pcId && active.key == connection.key) {
                mutableError.value = null
                val suppressed = preferences.getStringSet("dismissed:${connection.pcId}", emptySet()).orEmpty()
                mutablePending.value = if (delivered(connection.pcId).contains(id) || id in suppressed) null else AndroidFileOffer(
                    id, name, json.optString("mime", "application/octet-stream"), size,
                    json.optString("pc", connection.name), connection.pcId,
                )
                mutablePending.value?.let { offer ->
                    if (preferences.getBoolean("notifications", true)) {
                        // Older receipts included notifications blocked by Android. Start a fresh
                        // receipt namespace; delivered and dismissed offers remain excluded above.
                        OfferNotificationReceipt.post(id, preferences.getString("notified-v2:${connection.pcId}", ""),
                            notify = { notifyOffer(offer) },
                            remember = { preferences.edit().putString("notified-v2:${connection.pcId}", it).apply() })
                    }
                }
            }
        }
    }

    /** Call after a completed, confirmed download; cancellation must never call this. */
    @Synchronized
    fun markDelivered(pcId: String, offerId: String) {
        val ids = (delivered(pcId) + offerId).distinct().takeLast(200)
        check(preferences.edit().putString(pcId, JSONArray(ids).toString()).commit()) {
            appContext.getString(R.string.offer_save_failed)
        }
        if (mutablePending.value?.let { it.pcId == pcId && it.id == offerId } == true) clearPending()
    }

    fun clearPending() {
        generation.invalidate { clearPendingInternal(); mutableError.value = null }
    }

    private fun clearPendingInternal() {
        mutablePending.value = null
        appContext.getSystemService(NotificationManager::class.java).cancel(NOTIFICATION_ID)
    }

    @Synchronized
    fun forget(pcId: String) {
        generation.invalidate {
            preferences.edit().remove(pcId).remove("announced:$pcId").remove("notified-v2:$pcId").remove("dismissed:$pcId").apply()
            if (mutablePending.value?.pcId == pcId) { clearPendingInternal(); mutableError.value = null }
        }
    }

    @Synchronized
    fun dismiss(offer: AndroidFileOffer) {
        val key = "dismissed:${offer.pcId}"
        val ids = preferences.getStringSet(key, emptySet()).orEmpty().toList()
        check(preferences.edit().putStringSet(key, (ids + offer.id).distinct().takeLast(200).toSet()).commit())
        if (mutablePending.value?.id == offer.id && mutablePending.value?.pcId == offer.pcId) clearPending()
    }

    private fun delivered(pcId: String): List<String> {
        val array = JSONArray(preferences.getString(pcId, "[]"))
        return (0 until array.length()).map { array.getString(it) }
    }

    private fun notifyOffer(offer: AndroidFileOffer): Boolean {
        if (!notificationsAllowed(appContext)) return false
        val manager = appContext.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, appContext.getString(R.string.channel_file_offers), NotificationManager.IMPORTANCE_DEFAULT))
        val intent = Intent(appContext, MainActivity::class.java).putExtra("tab", "files")
            .addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP)
        val pendingIntent = PendingIntent.getActivity(appContext, 42, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(appContext, CHANNEL)
            .setSmallIcon(android.R.drawable.stat_sys_download)
            .setContentTitle(appContext.getString(R.string.offer_title, offer.pc))
            .setContentText(appContext.getString(R.string.offer_text, offer.name, appContext.fileSize(offer.size)))
            .setContentIntent(pendingIntent).setAutoCancel(true).build()
        return runCatching { manager.notify(NOTIFICATION_ID, notification); true }.getOrDefault(false)
    }

    companion object {
        private const val CHANNEL = "android_file_offers"
        private const val NOTIFICATION_ID = 2403
        fun notificationsAllowed(context: Context): Boolean {
            if (!QuickNotification.allowed(context)) return false
            val channel = context.getSystemService(NotificationManager::class.java).getNotificationChannel(CHANNEL)
            return channel == null || channel.importance != NotificationManager.IMPORTANCE_NONE
        }
        @Volatile private var instance: AndroidOfferMonitor? = null
        fun get(context: Context): AndroidOfferMonitor = instance ?: synchronized(this) {
            instance ?: AndroidOfferMonitor(context.applicationContext).also { instance = it }
        }
    }
}
