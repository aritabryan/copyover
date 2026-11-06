package dev.puente.android

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class SettingsOperationsTest {
    @Test fun successfulProbeProducesVisibleResultWithoutCredentials() = runBlocking {
        val check = ConnectionCheckController()
        val pc = Connection(server = "http://pc.local:8765", key = "private-key", name = "Laptop")
        val result = check.check({
            assertEquals(ConnectionCheckPhase.CHECKING, check.feedback.value.phase)
            pc
        }, { "Could not connect" })
        assertEquals(pc, result)
        assertEquals(ConnectionCheckPhase.SUCCESS, check.feedback.value.phase)
        assertEquals("Laptop", check.feedback.value.pcName)
        assertFalse(check.feedback.value.toString().contains("private-key"))
        check.dismiss()
        assertEquals(ConnectionCheckPhase.IDLE, check.feedback.value.phase)
    }

    @Test fun failedProbeCanBeRetriedAndShowsTheFailure() = runBlocking {
        val check = ConnectionCheckController()
        assertNull(check.check({ throw IllegalStateException("offline") }, { "PC unavailable" }))
        assertEquals(ConnectionCheckPhase.ERROR, check.feedback.value.phase)
        assertEquals("PC unavailable", check.feedback.value.message)
        check.check({ Connection(server = "http://pc.local:8765", key = "key", name = "PC") }, { "failed" })
        assertEquals(ConnectionCheckPhase.SUCCESS, check.feedback.value.phase)
    }

    @Test fun leavingTheProbeDoesNotDisplayAFakeFailure() = runBlocking {
        val check = ConnectionCheckController()
        try {
            check.check({ throw CancellationException("leaving") }, { "failed" })
            fail("Cancellation must propagate")
        } catch (_: CancellationException) { }
        assertEquals(ConnectionCheckPhase.IDLE, check.feedback.value.phase)
    }

    @Test fun blockedNotificationIsRetriedForTheSameFileAfterPermissionIsGranted() {
        var remembered: String? = null
        var permitted = false
        var posted = 0
        val notify = { if (permitted) { posted++; true } else false }
        fun attempt() = OfferNotificationReceipt.post("offer-1", remembered, notify) { remembered = it }
        assertFalse(attempt())
        assertNull(remembered)
        permitted = true
        assertTrue(attempt())
        assertEquals("offer-1", remembered)
        assertFalse(attempt())
        assertEquals(1, posted)
    }
}
