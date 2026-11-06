package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class HistoryPagingTest {
    private fun entries(count: Int) = (1..count).map {
        HistoryEntry("$it", it.toLong(), HistoryKind.TEXT, "Copy $it", "hash-$it")
    }

    @Test fun allCopiesReachableWithoutDuplicatesAcrossPages() {
        val originals = entries(57)
        val pages = (0..2).map { HistoryPaging.create(originals, it) }
        assertEquals(listOf(20, 20, 17), pages.map { it.entries.size })
        assertEquals(originals, pages.flatMap { it.entries })
        assertTrue(pages.all { it.total == 3 })
    }

    @Test fun correctsPageWhenLastCopiesAreDeleted() {
        assertEquals(1, HistoryPaging.create(entries(40), 2).index)
        assertEquals(0, HistoryPaging.create(entries(40), -1).index)
        val empty = HistoryPaging.create(emptyList(), 9)
        assertEquals(0, empty.index)
        assertEquals(1, empty.total)
        assertTrue(empty.entries.isEmpty())
    }

    @Test fun limitsPreviewWithoutChangingFullCopy() {
        val original = "a".repeat(HistoryPolicy.MAX_TEXT)
        assertTrue(HistoryPaging.summary(original).length <= 400)
        assertTrue(HistoryPaging.summary(original).endsWith("…"))
        assertEquals(HistoryPolicy.MAX_TEXT, original.length)
        assertEquals("short", HistoryPaging.summary("short"))
        val emoji = HistoryPaging.summary("a".repeat(398) + "😀" + "b".repeat(100))
        assertFalse(emoji.dropLast(1).last().isHighSurrogate())
    }
}
