package dev.puente.android

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.widget.RemoteViews
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

object QuickNotification {
    private const val CHANNEL = "clipboard_actions"
    private const val ID = 1
    const val SEND = "dev.puente.android.SEND_CLIPBOARD"
    const val RECEIVE = "dev.puente.android.RECEIVE_CLIPBOARD"
    const val ATTACH = "dev.puente.android.ATTACH_FILES"

    fun allowed(context: Context): Boolean {
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context,
                Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return false
        return NotificationManagerCompat.from(context).areNotificationsEnabled()
    }

    /** Without a paired PC the buttons cannot work, so the notification stays hidden until one is paired. */
    fun show(context: Context): Boolean {
        if (!allowed(context)) return false
        if (!runCatching { ConnectionStore(context).load().key.isNotEmpty() }.getOrDefault(false)) {
            hide(context)
            return true
        }
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, context.getString(R.string.channel_quick_actions), NotificationManager.IMPORTANCE_LOW))
        if (manager.getNotificationChannel(CHANNEL).importance == NotificationManager.IMPORTANCE_NONE) return false
        val open = PendingIntent.getActivity(context, 0, Intent(context, MainActivity::class.java),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val controls = RemoteViews(context.packageName, R.layout.notification_actions).apply {
            setOnClickPendingIntent(R.id.send, pendingAction(context, SEND, 1))
            setOnClickPendingIntent(R.id.receive, pendingAction(context, RECEIVE, 2))
            setOnClickPendingIntent(R.id.attach, pendingAction(context, ATTACH, 3))
        }
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_bridge)
            .setContentTitle(context.getString(R.string.app_name))
            .setContentText(context.getString(R.string.quick_notification_text))
            .setContentIntent(open).setOngoing(true).setSilent(true)
            .setStyle(NotificationCompat.DecoratedCustomViewStyle())
            .setCustomContentView(controls)
            .setCustomBigContentView(controls).build()
        return try { manager.notify(ID, notification); true } catch (_: SecurityException) { false }
    }

    fun hide(context: Context) = context.getSystemService(NotificationManager::class.java).cancel(ID)

    /** CLEAR_TASK: a panel left open behind other apps must not come back instead of running the new action. */
    fun actionIntent(context: Context, action: String): Intent =
        Intent(context, QuickActionActivity::class.java).setAction(action)
            .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TASK)

    fun pendingAction(context: Context, action: String, request: Int): PendingIntent =
        PendingIntent.getActivity(context, request, actionIntent(context, action),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
}

class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action !in listOf(Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED)) return
        if (ConnectionStore(context).notificationEnabled) QuickNotification.show(context)
        try { AutoReceiveService.refresh(context) }
        catch (_: Exception) { /* Some manufacturers require opening CopyOver after reboot. */ }
    }
}
