package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class SyncPresentationTest {
    @Test fun readerReadyDoesNotPretendPcIsVerified() {
        assertEquals(SyncIndicator.ATTENTION, syncIndicator(DirectionState(phase = SyncPhase.READY)))
        assertEquals(SyncIndicator.ACTIVE, syncIndicator(DirectionState(phase = SyncPhase.WAITING)))
    }

    @Test fun lastSuccessfulDeliveryDoesNotHideCurrentError() {
        val failed = DirectionState().delivered(1234, "text").progress(SyncPhase.ERROR, "No connection")
        assertEquals(SyncIndicator.ATTENTION, syncIndicator(failed))
        assertEquals(1234L, latestDelivery(listOf(DirectionState(), failed))?.deliveredAt)
    }

    @Test fun latestDeliveryIgnoresEmptyStates() {
        assertNull(latestDelivery(listOf(DirectionState(), DirectionState())))
        val older = DirectionState(deliveredAt = 1000)
        val latest = DirectionState(deliveredAt = 2000)
        assertEquals(latest, latestDelivery(listOf(latest, older)))
    }

    @Test fun timeLabelsAreCompactAndClampFutureClockChanges() {
        assertEquals(RelativeTime(RelativeUnit.NOW), relativeDeliveryTime(1000, 1000))
        assertEquals(RelativeTime(RelativeUnit.NOW), relativeDeliveryTime(2000, 1000))
        assertEquals(RelativeTime(RelativeUnit.MINUTES, 2), relativeDeliveryTime(1000, 121000))
        assertEquals(RelativeTime(RelativeUnit.HOURS, 3), relativeDeliveryTime(1000, 10801000))
        assertEquals(RelativeTime(RelativeUnit.DAYS, 2), relativeDeliveryTime(1000, 172801000))
    }

    @Test fun disabledAndWorkingHaveDistinctSignals() {
        assertEquals(SyncIndicator.DISABLED, syncIndicator(DirectionState()))
        listOf(SyncPhase.SEARCHING, SyncPhase.SYNCING, SyncPhase.TRANSFERRING).forEach {
            assertEquals(SyncIndicator.BUSY, syncIndicator(DirectionState(phase = it)))
        }
    }
}
