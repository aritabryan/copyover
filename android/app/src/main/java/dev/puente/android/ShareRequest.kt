package dev.puente.android

/** A new share intent has its own confirmation, independent of older global transfer results. */
class ShareRequest(var started: Boolean = false) {
    fun canChoose(transferBusy: Boolean): Boolean = !started && !transferBusy
    fun claim(): Boolean {
        if (started) return false
        started = true
        return true
    }
}
