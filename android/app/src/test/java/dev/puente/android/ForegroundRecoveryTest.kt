package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class ForegroundRecoveryTest {
    @Test fun reopensTheSavedConnectionEvenIfItsOldStatusSaidConnected() {
        val policy = ForegroundRecovery()
        assertTrue(policy.shouldCheck(paired = true, busy = false, connected = true))
        assertFalse(policy.shouldCheck(paired = true, busy = false, connected = true))
    }

    @Test fun repeatsTemporaryFailuresWithoutInterruptingActiveWork() {
        val policy = ForegroundRecovery()
        assertFalse(policy.shouldCheck(paired = true, busy = true, connected = false))
        assertTrue(policy.shouldCheck(paired = true, busy = false, connected = false))
        assertTrue(policy.shouldCheck(paired = true, busy = false, connected = false))
        assertFalse(policy.shouldCheck(paired = true, busy = false, connected = true))
    }

    @Test fun respectsNewPcAndNeverCreatesALinkForAnUnpairedPhone() {
        val policy = ForegroundRecovery()
        assertFalse(policy.shouldCheck(paired = false, busy = false, connected = false))
        policy.pause()
        assertFalse(policy.shouldCheck(paired = true, busy = false, connected = false))
        policy.foreground()
        assertFalse(policy.shouldCheck(paired = true, busy = false, connected = false))
        policy.resume()
        assertTrue(policy.shouldCheck(paired = true, busy = false, connected = false))
    }

    @Test fun stopsForAnInvalidLinkUntilTheUserReopensOrReconnects() {
        val policy = ForegroundRecovery()
        policy.requiresReview = true
        assertFalse(policy.shouldCheck(paired = true, busy = false, connected = false))
        policy.foreground()
        assertTrue(policy.shouldCheck(paired = true, busy = false, connected = false))
    }
}
