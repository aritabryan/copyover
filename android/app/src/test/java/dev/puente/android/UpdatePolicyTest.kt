package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class UpdatePolicyTest {
    @Test fun announcesEachNewVersionOnce() {
        assertTrue(UpdatePolicy.shouldNotify(21, 20, 0))
        assertFalse(UpdatePolicy.shouldNotify(21, 20, 21))
        assertFalse(UpdatePolicy.shouldNotify(20, 20, 0))
        assertTrue(UpdatePolicy.shouldNotify(22, 20, 21))
    }

    @Test fun downloadedApkMustMatchManifest() {
        assertTrue(UpdatePolicy.matches(12, "a".repeat(64), 12, "a".repeat(64)))
        assertFalse(UpdatePolicy.matches(12, "a".repeat(64), 11, "a".repeat(64)))
        assertFalse(UpdatePolicy.matches(12, "a".repeat(64), 12, "b".repeat(64)))
        assertFalse(UpdatePolicy.matches(0, "a".repeat(64), 0, "a".repeat(64)))
    }

    @Test fun packageVersionAndCurrentSignersMustMatch() {
        val valid = UpdatePolicy.accepts("dev.puente.android", "dev.puente.android", 4, 3, 4, setOf("cert"), setOf("cert"))
        assertTrue(valid)
        assertFalse(UpdatePolicy.accepts("other", "dev.puente.android", 4, 3, 4, setOf("cert"), setOf("cert")))
        assertFalse(UpdatePolicy.accepts("dev.puente.android", "dev.puente.android", 4, 3, 4, setOf("evil"), setOf("cert")))
        assertFalse(UpdatePolicy.accepts("dev.puente.android", "dev.puente.android", 3, 3, 4, setOf("cert"), setOf("cert")))
    }
}
