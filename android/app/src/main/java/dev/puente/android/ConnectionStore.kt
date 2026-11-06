package dev.puente.android

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import org.json.JSONObject
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

/** [ca] is the PC's certificate authority (PEM) received when it was linked; empty for older links. */
data class Connection(val server: String = "http://puente-pc.local:8765", val key: String = "",
    val pcId: String = "", val name: String = "PC", val lastAddress: String = "", val ca: String = "") {
    /** With the PC's CA the connection goes over HTTPS and only that PC's certificate is accepted. */
    val encrypted: Boolean get() = ca.isNotEmpty()

    /** Request for the native core: HTTPS pinned to the CA when there is one, plain HTTP otherwise. */
    fun nativeRequest(op: String): JSONObject = JSONObject().put("op", op)
        .put("server", NativeAddress.of(server, encrypted)).put("key", key)
        .apply { if (encrypted) put("ca", ca) }
}

/** Saved and discovered addresses stay http://; the scheme follows whether the PC's CA is known. */
object NativeAddress {
    fun of(address: String, encrypted: Boolean): String {
        val plain = address.removePrefix("https://").removePrefix("http://")
        return if (encrypted) "https://$plain" else "http://$plain"
    }
}

/** Private settings; the access key is encrypted with a non-exportable Android Keystore key. */
class ConnectionStore(private val context: Context) {
    private val preferences = context.getSharedPreferences("connection", Context.MODE_PRIVATE)
    var notificationEnabled: Boolean
        get() = preferences.getBoolean("notification", false)
        set(value) { preferences.edit().putBoolean("notification", value).apply() }
    var automaticReceive: Boolean
        get() = preferences.getBoolean("automatic_receive", true)
        set(value) { preferences.edit().putBoolean("automatic_receive", value).apply() }
    var shizukuSend: Boolean
        get() = preferences.getBoolean("shizuku_send", false)
        set(value) { preferences.edit().putBoolean("shizuku_send", value).apply() }
    var fileOffers: Boolean
        get() = preferences.getBoolean("file_offers", true)
        set(value) { preferences.edit().putBoolean("file_offers", value).apply() }

    fun prepareAutomaticSend(authorized: Boolean): Boolean {
        val saved = if (preferences.contains("shizuku_send")) shizukuSend else null
        val enabled = ShizukuModePolicy.initialSelection(saved, authorized)
        if (saved == null && enabled) shizukuSend = true
        return enabled
    }

    private fun legacy(): Connection = Connection(
        preferences.getString("server", null) ?: Connection().server,
        decryptKey(),
        preferences.getString("pc_id", "").orEmpty(),
        preferences.getString("pc_name", "PC").orEmpty(),
        preferences.getString("last_address", "").orEmpty(),
    )

    fun load(): Connection = synchronized(lock) { catalog().current() }
    fun list(): List<Connection> = synchronized(lock) { catalog().connections }
    fun save(connection: Connection) = synchronized(lock) {
        persist(catalog().upsert(connection.copy(server = connection.server.trim().trimEnd('/'))))
    }
    fun select(id: String) = synchronized(lock) { persist(catalog().select(id)) }
    fun rename(id: String, name: String) = synchronized(lock) { persist(catalog().rename(id, name)) }
    fun forget(id: String) = synchronized(lock) { persist(catalog().forget(id)) }

    private fun catalog(): PcCatalog {
        val encrypted = preferences.getString("catalog", null)
        if (encrypted != null) {
            val iv = requireNotNull(preferences.getString("catalog_iv", null))
            return PcCatalog.decode(decrypt(encrypted, iv))
        }
        val previous = legacy()
        val migrated = if (previous.key.isEmpty()) PcCatalog() else PcCatalog().upsert(previous)
        persist(migrated)
        return migrated
    }

    private fun persist(catalog: PcCatalog) {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, secretKey())
        val ciphertext = cipher.doFinal(catalog.encode().toByteArray(Charsets.UTF_8))
        check(preferences.edit()
            .putString("catalog", Base64.encodeToString(ciphertext, Base64.NO_WRAP))
            .putString("catalog_iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .remove("server").remove("pc_id").remove("pc_name").remove("last_address").remove("key").remove("iv").commit()) {
            context.getString(R.string.settings_save_failed)
        }
    }

    fun rememberAddress(address: String, expectedId: String = PcCatalog.identity(load())) = synchronized(lock) {
        val saved = catalog()
        val pc = saved.current()
        if (PcCatalog.identity(pc) == expectedId && pc.key.isNotEmpty()) persist(saved.upsert(pc.copy(lastAddress = address)))
    }

    private fun decryptKey(): String {
        val encrypted = preferences.getString("key", null) ?: return ""
        val iv = requireNotNull(preferences.getString("iv", null)) { context.getString(R.string.old_link_unreadable) }
        return decrypt(encrypted, iv)
    }

    private fun decrypt(encrypted: String, iv: String): String {
        val cipher = Cipher.getInstance("AES/GCM/NoPadding")
        cipher.init(Cipher.DECRYPT_MODE, secretKey(), GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)))
        return String(cipher.doFinal(Base64.decode(encrypted, Base64.NO_WRAP)), Charsets.UTF_8)
    }

    private fun secretKey(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey("puente.connection", null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder("puente.connection", KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }

    companion object { private val lock = Any() }
}
