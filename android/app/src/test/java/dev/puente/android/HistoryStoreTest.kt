package dev.puente.android

import java.io.File
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull
import org.junit.Assert.*
import org.junit.Test
import org.junit.rules.TemporaryFolder
import org.junit.Rule

class HistoryStoreTest {
    @get:Rule val temp = TemporaryFolder()
    @Test fun deduplicatesAndPersists() = runBlocking {
        val store = HistoryStore(temp.root)
        store.recordText("hello")
        store.recordText("hello")
        assertEquals(1, store.entries.value.size)
        val restored = HistoryStore(temp.root)
        restored.refresh()
        assertEquals("hello", restored.entries.value.single().content)
    }
    @Test fun excludesSensitiveAndDisabledContent() = runBlocking {
        val store = HistoryStore(temp.root)
        store.recordText("secret", sensitive = true)
        assertTrue(store.entries.value.isEmpty())
        store.setEnabled(false)
        store.recordText("hidden")
        assertTrue(store.entries.value.isEmpty())
        val restored = HistoryStore(temp.root)
        restored.refresh()
        assertFalse(restored.enabled.value)
    }
    @Test fun oversizedTextIsSkippedWithVisibleReason() = runBlocking {
        val store = HistoryStore(temp.root)
        assertFalse(store.recordText("a".repeat(HistoryPolicy.MAX_TEXT + 1)))
        assertTrue(store.entries.value.isEmpty())
        assertEquals(HistoryNotice.TEXT_TOO_LONG, store.message.value)
    }
    @Test fun clearPreservesPinsAndDeleteCleansImage() = runBlocking {
        val store = HistoryStore(temp.root)
        store.recordText("keep")
        store.pin(store.entries.value.single().id, true)
        val image = File(temp.root, "source.png").apply { writeBytes(byteArrayOf(1,2,3)) }
        store.recordImage(image, "image/png")
        val saved = File(temp.root, store.entries.value.first { it.kind == HistoryKind.IMAGE }.content)
        assertTrue(saved.exists())
        store.clearRecent()
        assertFalse(saved.exists())
        assertEquals("keep", store.entries.value.single().content)
    }
    @Test fun expiryDeletesImageAndPinLimitRejects() = runBlocking {
        var now = 0L
        val store = HistoryStore(temp.root) { now }
        repeat(20) { store.recordText("pin$it"); store.pin(store.entries.value.first { !it.pinned }.id, true) }
        store.recordText("excess")
        assertFalse(store.pin(store.entries.value.first { !it.pinned }.id, true))
        val image = File(temp.root, "source").apply { writeText("image") }
        store.recordImage(image, "image/png")
        val saved = File(temp.root, store.entries.value.first { it.kind == HistoryKind.IMAGE }.content)
        now = HistoryPolicy.TTL
        store.refresh()
        assertFalse(saved.exists())
        assertEquals(20, store.entries.value.size)
    }
    @Test fun expiredDuplicateCanBeSavedAgain() = runBlocking {
        var now = 0L
        val store = HistoryStore(temp.root) { now }
        store.recordText("same")
        now = HistoryPolicy.TTL
        assertTrue(store.recordText("same"))
        assertEquals(now, store.entries.value.single().createdAt)
    }
    @Test fun corruptedIndexCleansOrphansAndAllowsNewRecords() = runBlocking {
        File(temp.root, "index.json").writeText("broken")
        val orphan = File(temp.root, "orphan.image").apply { writeText("private") }
        val store = HistoryStore(temp.root)
        store.refresh()
        assertFalse(orphan.exists())
        assertTrue(store.recordText("new"))
        assertEquals("new", store.entries.value.single().content)
    }
    @Test fun deleteWaitsForClipboardCopyAndCannotBeFollowedByLateExport() = runBlocking {
        val store = HistoryStore(temp.root)
        store.recordText("copy")
        val id = store.entries.value.single().id
        val entered = CompletableDeferred<Unit>()
        val finishCopy = CompletableDeferred<Unit>()
        val deleted = CompletableDeferred<Unit>()
        val copy = async {
            store.copyEntry(id) {
                entered.complete(Unit)
                finishCopy.await()
                assertFalse(deleted.isCompleted)
                true
            }
        }
        entered.await()
        val delete = launch { store.delete(id); deleted.complete(Unit) }
        assertNull(withTimeoutOrNull(1000) { deleted.await(); true })
        finishCopy.complete(Unit)
        assertTrue(copy.await())
        delete.join()
        assertTrue(deleted.isCompleted)
        assertFalse(store.copyEntry(id) { fail("Deleted entry must not be copied"); true })
    }
}
