package dev.puente.android

import org.junit.Assert.*
import org.junit.Test

class PcCatalogTest {
    private val first = Connection(key = "first-key", pcId = "a".repeat(32), name = "PC")
    private val second = Connection(key = "second-key", pcId = "b".repeat(32), name = "Laptop")
    @Test fun addingPcPreservesExistingLinkAndReplacingOnlyUpdatesSameIdentity() {
        val catalog = PcCatalog(listOf(first), first.pcId).upsert(second)
        assertEquals(2, catalog.connections.size)
        assertEquals(second, catalog.current())
        val changed = catalog.upsert(first.copy(key = "new-key"))
        assertEquals(2, changed.connections.size)
        assertEquals("new-key", changed.current().key)
    }
    @Test fun roundTripRetainsAllConnectionsAndActiveSelection() {
        val catalog = PcCatalog(listOf(first, second), second.pcId)
        assertEquals(catalog, PcCatalog.decode(catalog.encode()))
        assertEquals(first, catalog.select(first.pcId).current())
        assertTrue(runCatching { catalog.select("missing") }.isFailure)
    }
    @Test fun renamePreservesCredentialsAndForgettingActiveSelectsRemainingPc() {
        val catalog = PcCatalog(listOf(first, second), first.pcId)
        val renamed = catalog.rename(first.pcId, " Office ")
        assertEquals("Office", renamed.current().name)
        assertEquals(first.key, renamed.current().key)
        assertEquals(second, renamed.forget(first.pcId).current())
        assertTrue(renamed.forget(first.pcId).forget(second.pcId).current().key.isEmpty())
    }
    @Test fun corruptRecordsFailInsteadOfRestoringForgottenLegacyCredentials() {
        assertTrue(runCatching { PcCatalog.decode("{}") }.isFailure)
        assertTrue(runCatching { PcCatalog.decode("not-json") }.isFailure)
    }
}
