package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class OfferGenerationTest {
    @Test fun clearedOrForgottenLinkCannotPublishAnInFlightOffer() {
        val gate = OfferGeneration()
        val beforeSwitch = gate.capture()
        var visible = "old"
        gate.invalidate { visible = "" }
        assertFalse(gate.publish(beforeSwitch) { visible = "stale" })
        assertEquals("", visible)
        assertTrue(gate.publish(gate.capture()) { visible = "new" })
        assertEquals("new", visible)
    }
}
