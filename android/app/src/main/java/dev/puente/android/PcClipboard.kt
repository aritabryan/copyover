package dev.puente.android

import android.content.ClipData
import android.os.PersistableBundle

object PcClipboard {
    const val ORIGIN = "dev.puente.android.from_pc"
    fun mark(clip: ClipData): ClipData = clip.apply {
        description.extras = PersistableBundle().apply { putBoolean(ORIGIN, true) }
    }
}
