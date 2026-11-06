package dev.puente.android

/** Picks the Dashboard tab: an explicit request wins, then the restored tab, then pairing state. */
object StartTab {
    const val CLIPBOARD = 0
    const val FILES = 1
    const val DEVICES = 2
    const val SETTINGS = 3
    const val EXTRA = "tab"

    fun requested(extra: String?): Int? = when (extra) {
        "files" -> FILES
        "devices" -> DEVICES
        "settings" -> SETTINGS
        else -> null
    }

    fun initial(extra: String?, restored: Int?, paired: Boolean): Int =
        requested(extra) ?: restored ?: if (paired) CLIPBOARD else DEVICES
}
