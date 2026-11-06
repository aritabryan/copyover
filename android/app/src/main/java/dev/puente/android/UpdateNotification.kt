package dev.puente.android

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.util.Log
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch

object UpdateNotification {
    private const val CHANNEL = "app_updates"
    private const val ID = 4200
    private const val LAST_ANNOUNCED = "last_announced"

    private fun preferences(context: Context) = context.getSharedPreferences("updates", Context.MODE_PRIVATE)

    fun automatic(context: Context): Boolean = preferences(context).getBoolean("automatic", true)

    fun show(context: Context, update: AppUpdate, installedCode: Long) {
        val saved = preferences(context)
        if (!UpdatePolicy.shouldNotify(update.versionCode, installedCode, saved.getLong(LAST_ANNOUNCED, 0))) return
        // Without permission it stays unannounced: the home screen still shows it when the app opens.
        if (!QuickNotification.allowed(context)) return
        val manager = context.getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, context.getString(R.string.channel_updates),
            NotificationManager.IMPORTANCE_DEFAULT))
        val open = PendingIntent.getActivity(context, ID,
            Intent(context, MainActivity::class.java).putExtra(StartTab.EXTRA, "settings")
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val notification = NotificationCompat.Builder(context, CHANNEL)
            .setSmallIcon(R.drawable.ic_bridge)
            .setContentTitle(context.getString(R.string.update_available, update.versionName))
            .setContentText(context.getString(R.string.update_notification_text))
            .setContentIntent(open).setAutoCancel(true).build()
        try {
            manager.notify(ID, notification)
            saved.edit().putLong(LAST_ANNOUNCED, update.versionCode).apply()
        } catch (_: SecurityException) {}
    }

    /** The open app already shows what the notification would say. */
    fun seen(context: Context, update: AppUpdate?) {
        context.getSystemService(NotificationManager::class.java).cancel(ID)
        if (update != null) preferences(context).edit().putLong(LAST_ANNOUNCED, update.versionCode).apply()
    }
}

/** Checks the public release while the app is closed, without needing a linked PC. */
class UpdateCheckJob : JobService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    override fun onStartJob(params: JobParameters): Boolean {
        if (!UpdateNotification.automatic(this)) return false
        scope.launch {
            try {
                val repository = AppUpdateRepository(this@UpdateCheckJob)
                val (update, _) = repository.check()
                if (update != null) UpdateNotification.show(this@UpdateCheckJob, update, repository.installedCode)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Exception) { Log.i("CopyOver", "Update check skipped: ${error.javaClass.simpleName}") }
            catch (error: UnsatisfiedLinkError) { Log.w("CopyOver", "Update check unavailable", error) }
            finally { jobFinished(params, false) }
        }
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean = false

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    companion object {
        private const val JOB_ID = 4201
        private const val EVERY_MILLIS = 6 * 60 * 60 * 1000L

        /** Scheduling again would restart the wait, so a job that already exists is left alone. */
        fun schedule(context: Context) {
            val scheduler = context.getSystemService(JobScheduler::class.java)
            if (scheduler.getPendingJob(JOB_ID) != null) return
            scheduler.schedule(JobInfo.Builder(JOB_ID, ComponentName(context, UpdateCheckJob::class.java))
                .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
                .setPeriodic(EVERY_MILLIS)
                .setPersisted(true)
                .build())
        }
    }
}
