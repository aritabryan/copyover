package dev.puente.android

import android.app.Application
import java.io.File

class PuenteApplication : Application() {
    override fun onCreate() {
        super.onCreate()
        if (android.os.Process.myUid() != applicationInfo.uid) return
        TransferRecords.initialize(this)
        val preferences = getSharedPreferences("receive_cursor", MODE_PRIVATE)
        val retained = TransferRecords.received.value.map { it.file }.toMutableSet()
        TransferRecords.state.value.result?.file?.file?.let(retained::add)
        listOf("image", "previous_image").mapNotNull { preferences.getString(it, null) }.mapTo(retained, ::File)
        runCatching { ReceivedCachePolicy.clean(File(cacheDir, "received"), retained) }
    }
}
