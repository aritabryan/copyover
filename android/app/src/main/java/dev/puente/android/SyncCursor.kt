package dev.puente.android

object AutomaticClipboardPolicy {
    fun canCopyText(text: String): Boolean = text.toByteArray(Charsets.UTF_8).size <= 256 * 1024
}

/** Release an unused download, including when pause, PC switching or clipboard writes interrupt delivery. */
class ClipboardDelivery(private val discard: () -> Unit) : AutoCloseable {
    private var applied = false
    fun copy(operation: () -> Unit) {
        operation()
        applied = true
    }
    override fun close() { if (!applied) discard() }
}

/** A failed clipboard write must never acknowledge the server's change. */
class SyncCursor(initial: String, private val persist: (String) -> Unit) {
    var token = initial
        private set

    fun accept(next: String, hasContent: Boolean, copy: () -> Unit) {
        if (next == token) return
        if (hasContent) copy()
        persist(next)
        token = next
    }
}
