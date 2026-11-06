package dev.puente.android

import org.junit.Assert.*
import org.junit.Test
import java.io.File

class QuickPanelPolicyTest {
    private val sent = TransferState(finished = true, message = "ok", result = TransferResult("ok"))

    @Test fun closesAfterItsOwnSuccessfulAction() = assertTrue(QuickPanelPolicy.canAutoClose(true, sent))
    @Test fun previousResultNeverClosesANewPanel() = assertFalse(QuickPanelPolicy.canAutoClose(false, sent))
    @Test fun staysOpenWhileBusy() = assertFalse(QuickPanelPolicy.canAutoClose(true, TransferState(busy = true)))
    @Test fun aFailureStaysVisible() = assertFalse(QuickPanelPolicy.canAutoClose(true, TransferState(finished = true, failed = true)))
    @Test fun receivedFileWaitsForTheUser() {
        val file = ReceivedFile(File("foto.jpg"), "foto.jpg", "image/jpeg")
        assertFalse(QuickPanelPolicy.canAutoClose(true, sent.copy(result = TransferResult("ok", file = file))))
    }
    @Test fun receivedTextClosesOnlyOnceCopied() {
        val received = sent.copy(result = TransferResult("ok", text = "hola"))
        assertFalse(QuickPanelPolicy.canAutoClose(true, received))
        assertTrue(QuickPanelPolicy.canAutoClose(true, received.copy(textCopied = true)))
    }
}
