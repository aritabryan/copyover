package dev.puente.android

import android.content.Context
import android.content.ContextWrapper
import android.content.SharedPreferences
import android.util.Base64
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import java.security.KeyStore
import java.util.UUID
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import javax.crypto.Cipher
import javax.crypto.SecretKey
import org.junit.Assert.*
import org.junit.Test
import org.junit.runner.RunWith

/** Uses the real Android Keystore with isolated preferences; never replaces app credentials. */
@RunWith(AndroidJUnit4::class)
class ConnectionMigrationTest {
    @Test fun automaticOnAuthorizationAndPreferencesKept() = isolated { context, _ ->
        val store = ConnectionStore(context)
        assertTrue(store.automaticReceive)
        assertFalse(store.prepareAutomaticSend(false))
        assertTrue(store.prepareAutomaticSend(true))
        assertTrue(ConnectionStore(context).shizukuSend)
        assertTrue(store.prepareAutomaticSend(false))
        store.shizukuSend = false
        store.automaticReceive = false
        assertFalse(store.prepareAutomaticSend(true))
        assertFalse(ConnectionStore(context).automaticReceive)
    }
    private fun isolated(block: (Context, SharedPreferences) -> Unit) {
        val base = InstrumentationRegistry.getInstrumentation().targetContext
        val name = "connection-migration-test-${UUID.randomUUID()}"
        val preferences = base.getSharedPreferences(name, Context.MODE_PRIVATE)
        val context = object : ContextWrapper(base) {
            override fun getSharedPreferences(name: String?, mode: Int): SharedPreferences = preferences
        }
        try { block(context, preferences) } finally {
            preferences.edit().clear().commit()
            base.deleteSharedPreferences(name)
        }
    }

    private fun legacy(context: Context, preferences: SharedPreferences): Connection {
        // Initialize the existing key alias through the production store without deleting it.
        ConnectionStore(context).list()
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        val key = keyStore.getKey("puente.connection", null) as SecretKey
        val pc = Connection("http://old-pc.local:8765", "a".repeat(64), "a".repeat(32), "Mi PC", "http://192.168.1.2:8765")
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key) }
        val ciphertext = cipher.doFinal(pc.key.toByteArray(Charsets.UTF_8))
        check(preferences.edit().clear().putString("server", pc.server).putString("pc_id", pc.pcId)
            .putString("pc_name", pc.name).putString("last_address", pc.lastAddress)
            .putString("key", Base64.encodeToString(ciphertext, Base64.NO_WRAP))
            .putString("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP)).commit())
        return pc
    }

    @Test fun realKeystoreMigrationPreservesExistingPairingAndRemovesLegacyFields() = isolated { context, preferences ->
        val previous = legacy(context, preferences)
        val store = ConnectionStore(context)
        assertEquals(previous, store.load())
        assertEquals(previous, ConnectionStore(context).load())
        assertEquals(listOf(previous), store.list())
        assertFalse(preferences.contains("key"))
        assertFalse(preferences.contains("iv"))
        assertFalse(preferences.getString("catalog", "")!!.contains(previous.key))
        val other = previous.copy(pcId = "b".repeat(32), key = "b".repeat(64), name = "Otra PC")
        store.save(other)
        store.select(previous.pcId)
        store.rename(previous.pcId, "Renombrada")
        assertEquals(previous.key, ConnectionStore(context).load().key)
        store.forget(previous.pcId)
        assertEquals(listOf(other), ConnectionStore(context).list())
        store.forget(other.pcId)
        assertTrue(ConnectionStore(context).load().key.isEmpty())
    }

    @Test fun invalidCatalogNeverFallsBackToOldCredentials() = isolated { context, preferences ->
        legacy(context, preferences)
        preferences.edit().putString("catalog", "invalid").putString("catalog_iv", "invalid").commit()
        assertTrue(runCatching { ConnectionStore(context).load() }.isFailure)
        assertTrue(preferences.contains("key"))
        assertEquals("invalid", preferences.getString("catalog", ""))
    }

    @Test fun independentStoreInstancesDoNotLoseConcurrentSavedPcs() = isolated { context, _ ->
        val executor = Executors.newFixedThreadPool(3)
        try {
            val tasks = (0 until 6).map { index -> executor.submit {
                ConnectionStore(context).save(Connection(key = "key-$index", pcId = index.toString().padStart(32, '0')))
            } }
            tasks.forEach { it.get(20, TimeUnit.SECONDS) }
            assertEquals(6, ConnectionStore(context).list().size)
        } finally { executor.shutdownNow() }
    }

    @Test fun staleAddressDiscoveryDoesNotSwitchActivePc() = isolated { context, _ ->
        val store = ConnectionStore(context)
        val first = Connection(key = "a", pcId = "a".repeat(32))
        val second = Connection(key = "b", pcId = "b".repeat(32))
        store.save(first)
        store.save(second)
        store.rememberAddress("http://stale:8765", first.pcId)
        assertEquals(second, ConnectionStore(context).load())
        assertEquals("", store.list().first { it.pcId == first.pcId }.lastAddress)
    }
}
