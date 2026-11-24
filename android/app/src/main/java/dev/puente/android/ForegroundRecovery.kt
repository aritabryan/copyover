package dev.puente.android

internal class ForegroundRecovery {
    private var enabled = true
    private var firstCheck = true
    var requiresReview = false

    fun pause() { enabled = false }
    fun resume() { enabled = true; firstCheck = false; requiresReview = false }
    fun foreground() { firstCheck = true; requiresReview = false }

    fun shouldCheck(paired: Boolean, busy: Boolean, connected: Boolean): Boolean {
        if (!enabled || !paired || busy || requiresReview) return false
        val check = firstCheck || !connected
        firstCheck = false
        return check
    }
}
