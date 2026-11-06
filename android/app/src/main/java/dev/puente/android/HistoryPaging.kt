package dev.puente.android

data class HistoryPage(val index: Int, val total: Int, val entries: List<HistoryEntry>)

object HistoryPaging {
    const val PER_PAGE = 20
    private const val SUMMARY_LENGTH = 400

    fun create(entries: List<HistoryEntry>, requested: Int): HistoryPage {
        val total = ((entries.size + PER_PAGE - 1) / PER_PAGE).coerceAtLeast(1)
        val index = requested.coerceIn(0, total - 1)
        return HistoryPage(index, total, entries.drop(index * PER_PAGE).take(PER_PAGE))
    }

    fun summary(text: String): String {
        if (text.length <= SUMMARY_LENGTH) return text
        val cut = text.take(SUMMARY_LENGTH - 1).let { if (it.last().isHighSurrogate()) it.dropLast(1) else it }
        return "$cut…"
    }
}
