package dev.puente.android

import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.yield
import org.junit.Assert.*
import org.junit.Test

class SettingsOperationTest {
    @Test fun replacementCancelsOldWorkWithoutClearingNewBusyState() = runBlocking {
        val operations = SettingsOperation()
        val releaseOld = CompletableDeferred<Unit>()
        val releaseNew = CompletableDeferred<Unit>()
        var busy = false
        val old = operations.launch(this, { busy = it }) {
            withContext(NonCancellable) { releaseOld.await() }
        }
        yield()
        operations.cancel()
        val current = operations.launch(this, { busy = it }) { releaseNew.await() }
        yield()
        releaseOld.complete(Unit)
        old.join()
        assertTrue(old.isCancelled)
        assertTrue(busy)
        releaseNew.complete(Unit)
        current.join()
        assertFalse(busy)
    }

    @Test fun cancellingAllowsImmediateReplacementWhileOldWorkUnwinds() = runBlocking {
        val operations = SettingsOperation()
        val release = CompletableDeferred<Unit>()
        var result = ""
        val old = operations.launch(this, {}) {
            withContext(NonCancellable) { release.await() }
            operations.ensureCurrent()
            result = "old PC"
        }
        yield()
        operations.cancel()
        val replacement = operations.launch(this, {}) { result = "new PC" }
        replacement.join()
        release.complete(Unit)
        old.join()
        assertEquals("new PC", result)
    }
}
