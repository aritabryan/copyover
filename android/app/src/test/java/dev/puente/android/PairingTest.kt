package dev.puente.android

import kotlinx.coroutines.runBlocking
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test

class PairingTest {
    private fun qr() = JSONObject().put("app", "puente-portapapeles").put("version", 1)
        .put("id", "a".repeat(32)).put("name", "mi-pc").put("server", "http://mi-pc.local:8765")
        .put("fallback", "http://192.168.1.2:8765").put("key", "secret-key")

    @Test fun readsOurQrWithoutManualConfiguration() {
        val connection = PairingCode.parse(qr().toString())
        assertEquals("a".repeat(32), connection.pcId)
        assertEquals("secret-key", connection.key)
        assertEquals("mi-pc", connection.name)
        assertEquals("http://192.168.1.2:8765", connection.lastAddress)
    }

    @Test fun rejectsUnrelatedQrInvalidUrlsAndEmptyCredentials() {
        val bad = listOf(qr().put("app", "other"), qr().put("version", 2),
            qr().put("id", ""), qr().put("key", ""), qr().put("server", "http://user:pass@pc"),
            qr().put("server", "https://pc"), qr().put("server", "http://pc/receive"),
            qr().put("fallback", "http://pc?key=secret"))
        bad.forEach { assertTrue(runCatching { PairingCode.parse(it.toString()) }.isFailure) }
        assertTrue(runCatching { PairingCode.parse("not a QR") }.isFailure)
    }

    @Test fun changedIpIsUsedWithoutChangingIdentityOrKey() = runBlocking {
        val saved = PairingCode.parse(qr().toString())
        val calls = mutableListOf<String>()
        val resolved = ConnectionSelection.choose(saved, { id ->
            assertEquals(saved.pcId, id)
            "http://192.168.2.8:8765"
        }, { calls.add(it.server); it })
        assertEquals(listOf("http://192.168.2.8:8765"), calls)
        assertEquals(saved.key, resolved.key)
        assertEquals(saved.pcId, resolved.pcId)
    }

    @Test fun usesStableNameOrLastAddressWhenDiscoveryIsBlocked() = runBlocking {
        val saved = PairingCode.parse(qr().toString())
        val resolved = ConnectionSelection.choose(saved, { null }, {
            if (it.server.contains(".local")) error("No DNS")
            it
        })
        assertEquals(saved.lastAddress, resolved.server)
    }

    @Test fun neverProbesOrSendsWithoutPairing() = runBlocking {
        var called = false
        assertTrue(runCatching {
            ConnectionSelection.choose(Connection(), { called = true; null }, { called = true; it })
        }.isFailure)
        assertFalse(called)
    }

    @Test fun discoveryMatchesOnlyLinkedPcAndFormatsIpv6() {
        assertFalse(DiscoveryPolicy.matches("other", "a", "a"))
        assertFalse(DiscoveryPolicy.matches("puente-portapapeles", "b", "a"))
        assertTrue(DiscoveryPolicy.matches("puente-portapapeles", "a", "a"))
        assertEquals("http://[fe80::1%25wlan0]:8765", DiscoveryPolicy.address("fe80::1%wlan0", 8765))
    }

    @Test fun listsOnlyPuentePcsWithAValidIdentityBeforeLinking() {
        assertTrue(DiscoveryPolicy.accepts("puente-portapapeles", "a".repeat(32)))
        assertFalse(DiscoveryPolicy.accepts("other", "a".repeat(32)))
        assertFalse(DiscoveryPolicy.accepts("puente-portapapeles", ""))
        assertFalse(DiscoveryPolicy.accepts("puente-portapapeles", "../fake"))
    }

    @Test fun recognizesThePcByEitherServicePrefix() {
        assertEquals("copyover-escritorio", DiscoveryPolicy.pcName("Puente copyover-escritorio"))
        assertEquals("copyover-escritorio", DiscoveryPolicy.pcName("CopyOver copyover-escritorio"))
        assertNull(DiscoveryPolicy.pcName("Impresora HP"))
        assertNull(DiscoveryPolicy.pcName("Puente "))
    }
}
