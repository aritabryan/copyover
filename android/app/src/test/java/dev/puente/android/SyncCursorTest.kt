package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class SyncCursorTest {
    @Test fun automaticTextFitsAndroidClipboardTransaction() {
        assertTrue(AutomaticClipboardPolicy.canCopyText("a".repeat(256 * 1024)))
        assertFalse(AutomaticClipboardPolicy.canCopyText("a".repeat(256 * 1024 + 1)))
        assertFalse(AutomaticClipboardPolicy.canCopyText("👋".repeat(70000)))
    }
    @Test fun remembersOnlyAfterClipboardWasUpdated() {
        val order = mutableListOf<String>()
        val cursor = SyncCursor("old") { order.add("save:$it") }
        cursor.accept("new", true) { order.add("copy") }
        assertEquals(listOf("copy", "save:new"), order)
        assertEquals("new", cursor.token)
        cursor.accept("new", true) { fail("Must not copy the same version twice") }
    }

    @Test fun failedCopyIsRetriedInsteadOfLosingChange() {
        var saved = false
        val cursor = SyncCursor("old") { saved = true }
        assertTrue(runCatching { cursor.accept("new", true) { error("clipboard busy") } }.isFailure)
        assertFalse(saved)
        assertEquals("old", cursor.token)
    }

    @Test fun emptyClipboardDoesNotErasePhoneClipboard() {
        var persisted = ""
        val cursor = SyncCursor("") { persisted = it }
        cursor.accept("empty", false) { fail("Must not clear the Android clipboard") }
        assertEquals("empty", persisted)
    }

    @Test fun pausedOrSwitchedDeliveryReleasesUnusedImage() {
        var removed = false
        ClipboardDelivery { removed = true }.use { /* The PC changed before applying this result. */ }
        assertTrue(removed)
    }

    @Test fun failedClipboardCopyReleasesImageButSuccessfulCopyKeepsUri() {
        var removed = false
        assertTrue(runCatching {
            ClipboardDelivery { removed = true }.use { it.copy { error("clipboard unavailable") } }
        }.isFailure)
        assertTrue(removed)
        removed = false
        ClipboardDelivery { removed = true }.use { it.copy { /* Clipboard accepted URI. */ } }
        assertFalse(removed)
    }

    @Test fun persistenceFailureKeepsImageAlreadyAcceptedByClipboard() {
        var removed = false
        val cursor = SyncCursor("old") { error("disk full") }
        assertTrue(runCatching {
            ClipboardDelivery { removed = true }.use { delivery ->
                cursor.accept("new", true) { delivery.copy {} }
            }
        }.isFailure)
        assertFalse(removed)
        assertEquals("old", cursor.token)
    }
}
