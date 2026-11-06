package dev.puente.android

/** When the quick panel may close by itself: anything that still needs the user keeps it open. */
object QuickPanelPolicy {
    const val CLOSE_DELAY_MS = 1200L

    fun canAutoClose(started: Boolean, state: TransferState): Boolean {
        // Before its own action starts the panel only sees the result of the previous transfer.
        if (!started || !state.finished || state.busy || state.failed) return false
        val result = state.result ?: return true
        // A received file waits for Save or Share; received text must reach the clipboard first.
        return result.file == null && (result.text == null || state.textCopied)
    }
}
