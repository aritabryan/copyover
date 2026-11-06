package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class SyncStatusTest {
    @Test fun failureDoesNotEraseLastDeliveryAndWaitingDoesNotClaimSuccess() {
        val delivered = DirectionState().delivered(1000, "texto")
        val failed = delivered.progress(SyncPhase.ERROR, "No connection")
        assertEquals(1000L, failed.deliveredAt)
        assertEquals("texto", failed.kind)
        assertEquals(SyncPhase.ERROR, failed.phase)
        val waiting = failed.progress(SyncPhase.WAITING)
        assertEquals(1000L, waiting.deliveredAt)
        assertEquals("", waiting.error)
    }
    @Test fun receivingAndSendingHaveIndependentState() {
        val states = mapOf(SyncDirection.FROM_PC to DirectionState().delivered(1000, "image"))
        assertNull(states[SyncDirection.TO_PC])
        assertEquals(SyncPhase.WAITING, states.getValue(SyncDirection.FROM_PC).phase)
    }
}
