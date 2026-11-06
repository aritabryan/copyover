package dev.puente.android

import android.app.Application
import android.content.pm.PackageManager
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import rikka.shizuku.Shizuku

object ShizukuAccess {
    fun running(): Boolean = runCatching { Shizuku.pingBinder() && Shizuku.getVersion() >= 13 }.getOrDefault(false)
    fun granted(): Boolean = runCatching { running() && Shizuku.checkSelfPermission() == PackageManager.PERMISSION_GRANTED }.getOrDefault(false)
}

class ShizukuSettingsModel(application: Application) : AndroidViewModel(application) {
    private val store = ConnectionStore(application)
    private fun text(id: Int): String = getApplication<Application>().getString(id)
    var selected by mutableStateOf(store.shizukuSend)
        private set
    var status by mutableStateOf("")
        private set
    private val received = Shizuku.OnBinderReceivedListener { refresh() }
    private val dead = Shizuku.OnBinderDeadListener { refresh() }
    private val permission = Shizuku.OnRequestPermissionResultListener { code, result ->
        if (code == REQUEST) {
            if (result == PackageManager.PERMISSION_GRANTED) select(true)
            else status = text(R.string.shizuku_permission_denied)
        }
    }

    init {
        Shizuku.addBinderReceivedListenerSticky(received)
        Shizuku.addBinderDeadListener(dead)
        Shizuku.addRequestPermissionResultListener(permission)
        refresh()
    }

    fun refresh() {
        selected = store.prepareAutomaticSend(ShizukuAccess.granted())
        status = text(when {
            !ShizukuAccess.running() -> R.string.shizuku_not_running
            !ShizukuAccess.granted() -> R.string.shizuku_authorize_hint
            selected -> R.string.shizuku_auto_on
            else -> R.string.shizuku_manual
        })
    }

    fun enable() {
        if (!ShizukuAccess.running()) { refresh(); return }
        try {
            if (ShizukuAccess.granted()) select(true)
            else if (Shizuku.shouldShowRequestPermissionRationale()) status = text(R.string.shizuku_allow_rationale)
            else Shizuku.requestPermission(REQUEST)
        } catch (_: Exception) { refresh() }
    }

    fun select(enabled: Boolean) {
        store.shizukuSend = enabled
        selected = enabled
        refresh()
    }

    override fun onCleared() {
        Shizuku.removeBinderReceivedListener(received)
        Shizuku.removeBinderDeadListener(dead)
        Shizuku.removeRequestPermissionResultListener(permission)
    }

    companion object { private const val REQUEST = 700 }
}
