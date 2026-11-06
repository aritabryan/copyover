package dev.puente.android

import android.annotation.SuppressLint
import android.os.Build
import android.service.quicksettings.Tile
import android.service.quicksettings.TileService

/** Quick Settings tiles: the panel opens on its own, so they work with the app closed. */
abstract class QuickTileService(private val action: String, private val request: Int) : TileService() {
    override fun onStartListening() {
        super.onStartListening()
        qsTile?.apply {
            state = Tile.STATE_INACTIVE
            // "Send" alone could belong to any app in the shade.
            if (Build.VERSION.SDK_INT >= 29) subtitle = getString(R.string.app_name)
            updateTile()
        }
    }

    override fun onClick() {
        super.onClick()
        if (isLocked) unlockAndRun(::openPanel) else openPanel()
    }

    @SuppressLint("StartActivityAndCollapseDeprecated")
    private fun openPanel() {
        if (Build.VERSION.SDK_INT >= 34) startActivityAndCollapse(QuickNotification.pendingAction(this, action, request))
        else @Suppress("DEPRECATION") startActivityAndCollapse(QuickNotification.actionIntent(this, action))
    }
}

class SendTileService : QuickTileService(QuickNotification.SEND, 1)
class ReceiveTileService : QuickTileService(QuickNotification.RECEIVE, 2)
