package dev.puente.android

import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Job
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch

internal class SettingsOperation {
    private var current: Job? = null

    fun cancel() {
        val previous = current
        current = null
        previous?.cancel()
    }

    suspend fun ensureCurrent() { currentCoroutineContext().ensureActive() }

    fun launch(scope: CoroutineScope, busy: (Boolean) -> Unit, block: suspend CoroutineScope.() -> Unit): Job {
        val job = scope.launch(start = CoroutineStart.LAZY) {
            try { block() }
            finally {
                if (current === currentCoroutineContext()[Job]) {
                    current = null
                    busy(false)
                }
            }
        }
        current = job
        busy(true)
        job.start()
        return job
    }
}
