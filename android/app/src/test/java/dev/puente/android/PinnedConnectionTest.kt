package dev.puente.android

import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.security.MessageDigest
import java.util.Base64

class PinnedConnectionTest {
    private val der = "certificate-authority-of-the-pc".toByteArray()
    private val pem = "-----BEGIN CERTIFICATE-----\n" +
        Base64.getMimeEncoder(64, "\n".toByteArray()).encodeToString(der) + "\n-----END CERTIFICATE-----\n"
    private val fingerprint = MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }

    private fun qr() = JSONObject().put("app", "puente-portapapeles").put("version", 1)
        .put("id", "a".repeat(32)).put("name", "mi-pc").put("server", "http://mi-pc.local:8765")
        .put("fallback", "http://192.168.1.2:8765").put("key", "secret-key")

    @Test fun keepsThePcAuthorityWhenItsFingerprintMatches() {
        val connection = PairingCode.parse(qr().put("ca", pem).put("ca_sha256", fingerprint).toString())
        assertEquals(pem, connection.ca)
        assertTrue(connection.encrypted)
    }

    @Test fun olderPcWithoutAuthorityStaysOnHttp() {
        val connection = PairingCode.parse(qr().toString())
        assertEquals("", connection.ca)
        assertFalse(connection.encrypted)
        val request = connection.nativeRequest("ping")
        assertEquals("http://mi-pc.local:8765", request.getString("server"))
        assertFalse(request.has("ca"))
    }

    @Test fun rejectsAnAuthorityThatDoesNotMatchOrIsIncomplete() {
        val other = MessageDigest.getInstance("SHA-256").digest("other".toByteArray()).joinToString("") { "%02x".format(it) }
        val bad = listOf(qr().put("ca", pem).put("ca_sha256", other), qr().put("ca", pem),
            qr().put("ca_sha256", fingerprint), qr().put("ca", "not a certificate").put("ca_sha256", fingerprint),
            qr().put("ca", pem + pem).put("ca_sha256", fingerprint),
            qr().put("ca", pem).put("ca_sha256", fingerprint.uppercase()))
        bad.forEach { assertTrue(runCatching { PairingCode.parse(it.toString()) }.isFailure) }
    }

    @Test fun pinnedPcIsReachedOnlyOverHttpsWithItsAuthority() {
        val connection = PairingCode.parse(qr().put("ca", pem).put("ca_sha256", fingerprint).toString())
        val request = connection.nativeRequest("send_text").put("text", "hola")
        assertEquals("https://mi-pc.local:8765", request.getString("server"))
        assertEquals(pem, request.getString("ca"))
        assertEquals("secret-key", request.getString("key"))
        // Discovered and remembered addresses are http://; the CA decides the scheme.
        assertEquals("https://192.168.2.8:8765",
            connection.copy(server = "http://192.168.2.8:8765").nativeRequest("ping").getString("server"))
        assertEquals("https://[fe80::1%25wlan0]:8765", NativeAddress.of("http://[fe80::1%25wlan0]:8765", true))
        // A link without CA never sends https:// to the core, even if an https address was remembered.
        assertEquals("http://192.168.2.8:8765", NativeAddress.of("https://192.168.2.8:8765", false))
    }

    @Test fun catalogKeepsTheAuthorityAndReadsOlderCatalogs() {
        val pinned = PairingCode.parse(qr().put("ca", pem).put("ca_sha256", fingerprint).toString())
        assertEquals(pem, PcCatalog.decode(PcCatalog().upsert(pinned).encode()).current().ca)
        val older = JSONObject(PcCatalog().upsert(pinned.copy(ca = "")).encode())
        assertFalse(older.getJSONArray("pcs").getJSONObject(0).has("ca"))
        assertEquals("", PcCatalog.decode(older.toString()).current().ca)
    }
}
