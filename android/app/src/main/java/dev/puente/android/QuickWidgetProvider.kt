package dev.puente.android

import android.appwidget.AppWidgetManager
import android.appwidget.AppWidgetProvider
import android.content.Context
import android.widget.RemoteViews

class QuickWidgetProvider : AppWidgetProvider() {
    override fun onUpdate(context: Context, manager: AppWidgetManager, ids: IntArray) {
        val views = RemoteViews(context.packageName, R.layout.widget_actions).apply {
            setOnClickPendingIntent(R.id.send, QuickNotification.pendingAction(context, QuickNotification.SEND, 1))
            setOnClickPendingIntent(R.id.receive, QuickNotification.pendingAction(context, QuickNotification.RECEIVE, 2))
            setOnClickPendingIntent(R.id.attach, QuickNotification.pendingAction(context, QuickNotification.ATTACH, 3))
        }
        manager.updateAppWidget(ids, views)
    }
}
