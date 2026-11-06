package dev.puente.android

import java.io.IOException
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay

class NativeError(val code: String, message: String) : IOException(message)

object ReceivePolicy {
    fun canShowWaiting(hasPending: Boolean, linkRejected: Boolean): Boolean = !hasPending && !linkRejected
    fun needsReview(error: Throwable): Boolean = error is NativeError &&
        error.code in setOf("unauthorized", "forbidden", "identity", "certificate")

    fun isTemporary(error: Throwable): Boolean = error is NativeError && error.code in setOf("network", "server")

    fun backoff(failures: Int): Long = listOf(1000L, 3000L, 10000L, 30000L)[(failures - 1).coerceIn(0, 3)]
}

suspend fun <T> receiveWithRetries(wait: suspend (Long) -> Unit = { delay(it) }, operation: suspend () -> T): T {
    var failures = 0
    while (true) {
        try { return operation() }
        catch (cancelled: CancellationException) { throw cancelled }
        catch (error: Exception) {
            failures++
            if (!ReceivePolicy.isTemporary(error) || failures >= 3) throw error
            wait(ReceivePolicy.backoff(failures))
        }
    }
}
