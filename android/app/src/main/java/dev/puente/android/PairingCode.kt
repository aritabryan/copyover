package dev.puente.android

import org.json.JSONObject
import java.net.URI
import java.security.MessageDigest
import java.util.Base64

object PairingCode {
    fun parse(contents: String): Connection {
        if (contents.length > 16384) throw LocalizedException(R.string.link_failed_search_again)
        return try {
            val json = JSONObject(contents)
            require(json.getString("app") == "puente-portapapeles" && json.getInt("version") == 1)
            val id = json.getString("id")
            require(id.matches(Regex("[a-f0-9]{32}")))
            val key = json.getString("key")
            require(key.isNotEmpty() && key.all { it.code in 33..126 })
            val server = validAddress(json.getString("server"))
            val fallback = json.optString("fallback").let { if (it.isEmpty()) "" else validAddress(it) }
            val ca = authority(json.optString("ca"), json.optString("ca_sha256"))
            Connection(server, key, id, json.optString("name", "PC").take(80), fallback, ca)
        } catch (_: Exception) {
            throw LocalizedException(R.string.link_failed_search_again)
        }
    }

    /** The PC's CA, checked against its SHA-256 fingerprint. A PC without TLS sends neither. */
    internal fun authority(pem: String, fingerprint: String): String {
        if (pem.isEmpty() && fingerprint.isEmpty()) return ""
        require(pem.length <= 8192 && fingerprint.matches(Regex("[a-f0-9]{64}")))
        val body = Regex("""-----BEGIN CERTIFICATE-----([A-Za-z0-9+/=\s]+)-----END CERTIFICATE-----\s*""")
            .matchEntire(pem)?.groupValues?.get(1) ?: throw IllegalArgumentException()
        val der = Base64.getMimeDecoder().decode(body)
        val actual = MessageDigest.getInstance("SHA-256").digest(der).joinToString("") { "%02x".format(it) }
        require(MessageDigest.isEqual(actual.toByteArray(), fingerprint.toByteArray()))
        return pem
    }

    private fun validAddress(value: String): String {
        val uri = URI(value)
        require(uri.scheme == "http" && !uri.host.isNullOrEmpty())
        require(uri.userInfo == null && uri.query == null && uri.fragment == null)
        require(uri.path.isNullOrEmpty() || uri.path == "/")
        require(uri.port == -1 || uri.port in 1..65535)
        return value.trimEnd('/')
    }
}
