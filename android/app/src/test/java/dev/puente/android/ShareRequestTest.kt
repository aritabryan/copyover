package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class ShareRequestTest {
    @Test fun newShareIsIndependentOfAnEarlierCompletedTransfer() {
        val oldTransfer = TransferState(finished = true)
        val request = ShareRequest()
        assertTrue(request.canChoose(oldTransfer.busy))
        assertTrue(request.claim())
        assertFalse(request.canChoose(false))
        assertFalse(request.claim())
    }
    @Test fun rotationDoesNotOfferToResendTheSameShare() {
        assertFalse(ShareRequest(started = true).canChoose(false))
        assertFalse(ShareRequest().canChoose(true))
    }
}
