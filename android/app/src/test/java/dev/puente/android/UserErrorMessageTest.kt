package dev.puente.android

import org.junit.Assert.*
import org.junit.Test
import java.net.ConnectException

class UserErrorMessageTest {
    @Test fun unknownExceptionsUseTheScreenMessageRatherThanConsoleText() {
        assertEquals(R.string.receive_failed, UserErrorMessage.resource(
            IllegalStateException("java.lang.IllegalStateException: internal path /data/private"), R.string.receive_failed))
    }

    @Test fun networkErrorsExplainHowToFindThePc() {
        assertEquals(R.string.pc_not_found, UserErrorMessage.resource(ConnectException("ECONNREFUSED"), R.string.receive_failed))
        assertEquals(R.string.pc_not_found, UserErrorMessage.resource(NativeError("network", "tcp error"), R.string.receive_failed))
    }

    @Test fun linkAndCertificateProblemsRemainDistinguishable() {
        assertEquals(R.string.receive_link_rejected, UserErrorMessage.resource(NativeError("unauthorized", "HTTP 401"), R.string.receive_failed))
        assertEquals(R.string.native_certificate_mismatch, UserErrorMessage.resource(NativeError("certificate", "TLS error"), R.string.receive_failed))
    }
}
