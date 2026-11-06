package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class HistoryPolicyTest {
    private fun item(id: String, time: Long, pinned: Boolean = false, bytes: Long = 0) =
        HistoryEntry(id, time, HistoryKind.TEXT, "text", "hash$id", pinned, bytes)

    @Test fun expirationKeepsPinsAndRemovesBoundary() {
        val now = HistoryPolicy.TTL + 10
        assertEquals(listOf("new", "pin"), HistoryPolicy.retain(listOf(item("old", 10), item("pin", 0, true), item("new", now)), now).map { it.id })
    }
    @Test fun retainsOnlyNewestHundredRecents() {
        val result = HistoryPolicy.retain((0..120).map { item("$it", it.toLong()) }, 121)
        assertEquals(100, result.size)
        assertEquals("120", result.first().id)
    }
    @Test fun budgetEvictsOldestRecentButNeverPins() {
        assertNull(HistoryPolicy.makeImageRoom(listOf(item("pin", 1, true, HistoryPolicy.IMAGE_BUDGET)), 1))
        assertEquals(emptyList<HistoryEntry>(), HistoryPolicy.makeImageRoom(listOf(item("old", 1, bytes = HistoryPolicy.IMAGE_BUDGET)), 10))
    }
    @Test fun oversizeImageIsRejectedWithoutEvicting() {
        assertNull(HistoryPolicy.makeImageRoom(listOf(item("old", 1)), HistoryPolicy.IMAGE_BUDGET + 1))
    }
}
