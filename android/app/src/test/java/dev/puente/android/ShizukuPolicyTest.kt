package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class ShizukuPolicyTest {
    @Test fun enablesOnAuthorizationWithoutChangingSavedChoice() {
        assertFalse(ShizukuModePolicy.initialSelection(null, false))
        assertTrue(ShizukuModePolicy.initialSelection(null, true))
        assertFalse(ShizukuModePolicy.initialSelection(false, true))
        assertTrue(ShizukuModePolicy.initialSelection(true, false))
    }
    @Test fun activatingDoesNotSendExistingClipboard() {
        val gate = OutgoingClipboardGate()
        assertFalse(gate.observe("old", false))
        assertTrue(gate.observe("new", false))
        assertFalse(gate.observe("new", false))
    }

    @Test fun firstCopyAfterActivatingOnEmptyClipboardIsSent() {
        val gate = OutgoingClipboardGate()
        assertFalse(gate.observe("empty", false))
        assertTrue(gate.observe("first-copy", false))
        assertFalse(gate.observe("empty", true))
        assertTrue(gate.observe("second-copy", false))
    }

    @Test fun clearingClipboardDropsQueuedDataWhileLockDoesNotPretendToClearIt() {
        assertTrue(ShizukuModePolicy.dropPending("empty", false))
        assertTrue(ShizukuModePolicy.dropPending("text", true))
        assertTrue(ShizukuModePolicy.dropPending("error", false))
        assertFalse(ShizukuModePolicy.dropPending("unavailable", false))
        assertFalse(ShizukuModePolicy.dropPending("text", false))
        assertEquals("png", ShizukuModePolicy.imageExtension("image/png"))
        assertNull(ShizukuModePolicy.imageExtension("application/pdf"))
    }

    @Test fun pcCopiesAreIgnoredAndManualModeNeedsNoPrivileges() {
        val gate = OutgoingClipboardGate()
        gate.observe("initial", false)
        assertFalse(gate.observe("pc-copy", true))
        assertTrue(gate.observe("phone-copy", false))
        assertFalse(ShizukuModePolicy.canRun(false, true, true))
        assertFalse(ShizukuModePolicy.canRun(true, false, true))
        assertFalse(ShizukuModePolicy.canRun(true, true, false))
        assertTrue(ShizukuModePolicy.canRun(true, true, true))
    }

    @Test fun clipboardApiArgumentsAreExplicitForSupportedAndroidSignatures() {
        assertEquals(listOf("com.android.shell"), ClipboardApiArguments.build(listOf("java.lang.String"), 0))
        assertTrue(runCatching { ClipboardApiArguments.build(listOf("java.lang.String"), 10) }.isFailure)
        assertEquals(listOf("com.android.shell", 10), ClipboardApiArguments.build(listOf("java.lang.String", "int"), 10))
        assertEquals(listOf("com.android.shell", null, 10), ClipboardApiArguments.build(listOf("java.lang.String", "java.lang.String", "int"), 10))
        assertEquals(listOf("com.android.shell", null, 10, 0), ClipboardApiArguments.build(listOf("java.lang.String", "java.lang.String", "int", "int"), 10))
        assertTrue(runCatching { ClipboardApiArguments.build(listOf("java.lang.String", "long"), 0) }.isFailure)
    }
}
