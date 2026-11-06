package dev.puente.android

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class ReceiveRetriesTest {
    @Test fun keepsLinkErrorUntilAnotherAttempt() {
        assertFalse(ReceivePolicy.canShowWaiting(false, true))
        assertFalse(ReceivePolicy.canShowWaiting(true, false))
        assertTrue(ReceivePolicy.canShowWaiting(false, false))
    }
    @Test fun keepsRejectionReasonWhenAnotherAddressFails() = runBlocking {
        val saved = Connection("http://pc.local:8765", "key", "a".repeat(32), lastAddress = "http://192.168.1.2:8765")
        val rejection = NativeError("forbidden", "Check the link")
        val error = runCatching {
            ConnectionSelection.choose(saved, { null }) {
                if (it.server == saved.server) throw rejection
                throw NativeError("network", "No network")
            }
        }.exceptionOrNull()
        assertSame(rejection, error)
    }
    @Test fun recoversTemporaryReadWithoutAnotherTap() = runBlocking {
        var attempts = 0
        val waits = mutableListOf<Long>()
        val result = receiveWithRetries(wait = { waits.add(it) }) {
            attempts++
            if (attempts < 3) throw NativeError("network", "No network")
            "copy"
        }
        assertEquals("copy", result)
        assertEquals(3, attempts)
        assertEquals(2, waits.size)
    }

    @Test fun stopsOnLinkRejectionWithoutRetrying() = runBlocking {
        for (code in listOf("unauthorized", "forbidden", "identity", "certificate")) {
            var attempts = 0
            val error = runCatching {
                receiveWithRetries(wait = { fail("Must not wait") }) {
                    attempts++; throw NativeError(code, "Check the link")
                }
            }.exceptionOrNull()
            assertTrue(error is NativeError)
            assertEquals(1, attempts)
            assertTrue(ReceivePolicy.needsReview(error!!))
        }
    }

    @Test fun limitsRetriesAndRespectsCancellation() = runBlocking {
        var attempts = 0
        val error = runCatching {
            receiveWithRetries(wait = {}) { attempts++; throw NativeError("server", "Busy") }
        }.exceptionOrNull()
        assertTrue(error is NativeError)
        assertEquals(3, attempts)
        attempts = 0
        val cancelled = runCatching {
            receiveWithRetries(wait = {}) { attempts++; throw CancellationException() }
        }.exceptionOrNull()
        assertTrue(cancelled is CancellationException)
        assertEquals(1, attempts)
    }
}
