package dev.puente.android

enum class HistoryKind { TEXT, IMAGE }

data class HistoryEntry(
    val id: String,
    val createdAt: Long,
    val kind: HistoryKind,
    val content: String,
    val fingerprint: String,
    val pinned: Boolean = false,
    val imageBytes: Long = 0,
    val mime: String = "text/plain",
)

object HistoryPolicy {
    const val TTL = 7L * 24 * 60 * 60 * 1000
    const val MAX_RECENT = 100
    const val MAX_PINS = 20
    const val IMAGE_BUDGET = 128L * 1024 * 1024
    // Even JSON's worst-case six-byte escaping remains below the bounded index limit.
    const val MAX_TEXT = 128 * 1024

    fun retain(entries: List<HistoryEntry>, now: Long): List<HistoryEntry> {
        val sorted = entries.sortedByDescending { it.createdAt }
        val recent = sorted.filter { !it.pinned && now - it.createdAt < TTL }.take(MAX_RECENT).map { it.id }.toSet()
        return sorted.filter { it.pinned || it.id in recent }
    }

    fun makeImageRoom(entries: List<HistoryEntry>, bytes: Long): List<HistoryEntry>? {
        if (bytes < 0 || bytes > IMAGE_BUDGET) return null
        val retained = entries.toMutableList()
        var used = retained.sumOf { it.imageBytes }
        for (entry in retained.filter { !it.pinned && it.imageBytes > 0 }.sortedBy { it.createdAt }) {
            if (used + bytes <= IMAGE_BUDGET) break
            retained.remove(entry)
            used -= entry.imageBytes
        }
        return retained.takeIf { used + bytes <= IMAGE_BUDGET }
    }
}
