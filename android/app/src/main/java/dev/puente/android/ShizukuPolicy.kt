package dev.puente.android

object ShizukuModePolicy {
    fun initialSelection(saved: Boolean?, authorized: Boolean): Boolean = saved ?: authorized
    fun canRun(selected: Boolean, running: Boolean, granted: Boolean): Boolean = selected && running && granted
    fun dropPending(kind: String, fromPc: Boolean): Boolean = fromPc || kind in setOf("empty", "unsupported", "error")
    fun imageExtension(mime: String): String? = mapOf(
        "image/png" to "png", "image/jpeg" to "jpg", "image/webp" to "webp", "image/gif" to "gif",
        "image/bmp" to "bmp", "image/heic" to "heic", "image/heif" to "heic")[mime]
}

class OutgoingClipboardGate {
    private var previous: String? = null
    fun observe(token: String, fromPc: Boolean): Boolean {
        val baseline = previous == null
        val changed = previous != token
        previous = token
        return !baseline && changed && !fromPc
    }
}

object ClipboardApiArguments {
    fun build(types: List<String>, user: Int): List<Any?> = when (types) {
        listOf("java.lang.String") -> {
            require(user == 0) { "This Android version only allows the primary user with Shizuku." }
            listOf("com.android.shell")
        }
        listOf("java.lang.String", "int") -> listOf("com.android.shell", user)
        listOf("java.lang.String", "java.lang.String", "int") -> listOf("com.android.shell", null, user)
        listOf("java.lang.String", "java.lang.String", "int", "int") -> listOf("com.android.shell", null, user, 0)
        else -> error("This Android version uses an unsupported clipboard API.")
    }
}
